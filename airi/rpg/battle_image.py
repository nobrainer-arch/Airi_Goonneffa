# airi/rpg/battle_image.py — PIL Battle Card Generator
# Layout exactly from user wireframe:
#  LEFT  = player (avatar top-left, 2 bars, 3 skill boxes, equipment row)
#  CENTER = VS divider
#  RIGHT = monster (avatar top-right mirrored, 2 bars, 3 skill boxes, equip row)

from __future__ import annotations
import asyncio
import io
import math
import textwrap
from typing import Optional

import aiohttp
from PIL import Image, ImageDraw, ImageFilter, ImageFont


# ── Canvas ────────────────────────────────────────────────────────
W, H    = 1100, 620
HALF    = W // 2          # 550
VS_W    = 80              # width of VS divider strip
L_START = 0
L_END   = HALF - VS_W // 2   # 510
R_START = HALF + VS_W // 2   # 590
R_END   = W                  # 1100

# ── Palette ────────────────────────────────────────────────────────
BG          = (18, 18, 28)
PANEL_BG    = (28, 30, 48)
CARD_BG     = (38, 40, 62)
CARD_BORDER = (70, 75, 110)
HP_FULL     = (220, 55, 55)
HP_EMPTY    = (55, 20, 20)
MP_FULL     = (55, 120, 220)
MP_EMPTY    = (20, 40, 80)
WHITE       = (255, 255, 255)
GREY        = (160, 165, 190)
GOLD        = (255, 200, 60)
GREEN       = (60, 200, 100)
VS_COLOR    = (255, 220, 60)
SHADOW_CLR  = (0, 0, 0, 160)


def _font(size: int) -> ImageFont.FreeTypeFont:
    """Load a font — falls back to default if TTF not found."""
    for path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ]:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


def _round_rect(draw: ImageDraw.Draw, xy, radius: int, fill=None, outline=None, width=2):
    """Draw a rounded rectangle."""
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=fill, outline=outline, width=width)


def _bar(draw, x, y, w, h, pct, full_color, empty_color, radius=4):
    """Draw a rounded progress bar."""
    _round_rect(draw, (x, y, x+w, y+h), radius, fill=empty_color)
    filled_w = max(radius*2, int(w * pct))
    _round_rect(draw, (x, y, x+filled_w, y+h), radius, fill=full_color)


def _shadow_text(draw, pos, text, font, color=WHITE, shadow_offset=2):
    dx, dy = pos
    draw.text((dx+shadow_offset, dy+shadow_offset), text, font=font, fill=(0,0,0,180))
    draw.text(pos, text, font=font, fill=color)


def _centered_text(draw, cx, y, text, font, color=WHITE):
    bbox = font.getbbox(text)
    tw = bbox[2] - bbox[0]
    draw.text((cx - tw//2, y), text, font=font, fill=color)


async def _download_image(url: str, size: tuple[int,int]) -> Optional[Image.Image]:
    """Download and resize an image from URL, return RGBA Image or None."""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status == 200:
                    data = await r.read()
                    img = Image.open(io.BytesIO(data)).convert("RGBA")
                    img = img.resize(size, Image.LANCZOS)
                    return img
    except Exception:
        pass
    return None


def _circle_crop(img: Image.Image) -> Image.Image:
    """Crop an image into a circle with transparent background."""
    size = img.size
    mask = Image.new("L", size, 0)
    d    = ImageDraw.Draw(mask)
    d.ellipse((0, 0, size[0]-1, size[1]-1), fill=255)
    result = Image.new("RGBA", size, (0,0,0,0))
    result.paste(img, mask=mask)
    return result


def _placeholder_avatar(size: int, color: tuple, letter: str = "?") -> Image.Image:
    """Create a simple colored circle avatar when image unavailable."""
    img  = Image.new("RGBA", (size, size), (0,0,0,0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((0, 0, size-1, size-1), fill=color)
    font = _font(size // 2)
    bbox = font.getbbox(letter)
    tx = (size - (bbox[2]-bbox[0])) // 2
    ty = (size - (bbox[3]-bbox[1])) // 2
    draw.text((tx, ty), letter, font=font, fill=WHITE)
    return img


def _draw_skill_box(draw: ImageDraw.Draw, x: int, y: int, w: int, h: int,
                    skill_name: str, rank: str, on_cooldown: bool = False):
    """Draw a skill card box."""
    rank_colors = {"F":(100,100,100),"E":(70,160,70),"D":(70,100,210),
                   "C":(70,70,210),"B":(130,70,210),"A":(210,140,30),
                   "S":(210,50,50),"SS":(255,110,40),"SSS":(255,210,30)}
    rk_color = rank_colors.get(rank, (90,90,90))
    border   = (80,80,80) if on_cooldown else rk_color
    fill     = (30,30,42) if on_cooldown else (42,42,62)
    _round_rect(draw, (x, y, x+w, y+h), 8, fill=fill, outline=border, width=2)
    # rank badge top-right
    draw.ellipse((x+w-22, y+4, x+w-4, y+22), fill=rk_color)
    rf = _font(9)
    draw.text((x+w-19, y+6), rank, font=rf, fill=WHITE)
    # skill name (truncated, wrapped)
    nf   = _font(11)
    name = skill_name[:14] + "…" if len(skill_name) > 14 else skill_name
    bbox = nf.getbbox(name)
    tw   = bbox[2]-bbox[0]
    draw.text((x + (w-tw)//2, y+h-22), name, font=nf, fill=GREY if on_cooldown else WHITE)
    if on_cooldown:
        _centered_text(draw, x+w//2, y+h//2-10, "⏱", _font(22), (150,100,100))


def _draw_equipment_row(draw: ImageDraw.Draw, x: int, y: int, w: int, h: int,
                        weapon: str, armor: str, on_left: bool = True):
    """Bottom equipment strip with icon boxes and stat bars."""
    _round_rect(draw, (x, y, x+w, y+h), 8, fill=CARD_BG, outline=CARD_BORDER, width=1)
    icon_sz = 36
    bar_y1  = y + 8
    bar_y2  = y + h // 2 + 4
    bar_h   = 14
    font_sm = _font(11)

    if on_left:
        # weapon icon left, bars right
        ix, iy = x+8, y+(h-icon_sz)//2
        draw.rounded_rectangle([ix, iy, ix+icon_sz, iy+icon_sz], radius=6, fill=CARD_BG, outline=GOLD, width=1)
        _centered_text(draw, ix+icon_sz//2, iy+8, "⚔", _font(18), GOLD)
        bx, bw = ix+icon_sz+10, w - icon_sz - 40
        # weapon bar
        pct_w = 0.7  # placeholder
        _bar(draw, bx, bar_y1, bw, bar_h, pct_w, GOLD, (50,40,10))
        draw.text((bx, bar_y1-13), weapon[:18], font=font_sm, fill=GOLD)
        # armor icon
        ix2, iy2 = x+8, iy  # same x stack
        pct_a = 0.5
        _bar(draw, bx, bar_y2, bw, bar_h, pct_a, (150,200,255), (20,40,60))
        draw.text((bx, bar_y2-13), armor[:18], font=font_sm, fill=(150,200,255))
    else:
        # mirrored: bars left, icon right
        ix, iy = x+w-icon_sz-8, y+(h-icon_sz)//2
        draw.rounded_rectangle([ix, iy, ix+icon_sz, iy+icon_sz], radius=6, fill=CARD_BG, outline=GOLD, width=1)
        _centered_text(draw, ix+icon_sz//2, iy+8, "⚔", _font(18), GOLD)
        bx, bw = x+8, w - icon_sz - 40
        _bar(draw, bx, bar_y1, bw, bar_h, 0.8, GOLD, (50,40,10))
        draw.text((bx, bar_y1-13), weapon[:18], font=font_sm, fill=GOLD)
        _bar(draw, bx, bar_y2, bw, bar_h, 0.6, (150,200,255), (20,40,60))
        draw.text((bx, bar_y2-13), armor[:18], font=font_sm, fill=(150,200,255))


# ── Main generator ────────────────────────────────────────────────
async def generate_battle_card(
    # Player data
    player_name:    str,
    player_class:   str,
    player_hp:      int, player_hp_max:   int,
    player_mp:      int, player_mp_max:   int,
    player_str:     int, player_def:      int, player_agi:    int,
    player_skills:  list[dict],          # [{"name":..,"rank":..,"on_cd":bool}]
    player_weapon:  str = "Unarmed",
    player_armor:   str = "None",
    player_avatar_url: str | None = None,
    player_class_color: tuple = (80,120,220),
    # Monster data
    monster_name:   str  = "Unknown",
    monster_type:   str  = "Normal",
    monster_hp:     int  = 100, monster_hp_max:  int = 100,
    monster_mp:     int  = 0,   monster_mp_max:  int = 0,
    monster_str:    int  = 10,  monster_def:     int = 5,  monster_agi: int = 5,
    monster_skills: list[dict] = None,
    monster_weapon: str  = "Claws",
    monster_armor:  str  = "Hide",
    monster_image_url: str | None = None,
    monster_color:  tuple = (180, 60, 60),
    # Battle state
    effects_player: list[str] = None,
    effects_monster: list[str] = None,
    combat_log:     list[str] = None,
    turn_owner:     str  = "player",
    sleeping:       bool = False,
) -> io.BytesIO:

    monster_skills  = monster_skills  or []
    effects_player  = effects_player  or []
    effects_monster = effects_monster or []
    combat_log      = combat_log      or []

    # ── Download images concurrently ──────────────────────────────
    player_img_raw, monster_img_raw = await asyncio.gather(
        _download_image(player_avatar_url, (120, 120)) if player_avatar_url else asyncio.sleep(0, result=None),
        _download_image(monster_image_url, (120, 120)) if monster_image_url else asyncio.sleep(0, result=None),
    )

    # ── Build canvas ──────────────────────────────────────────────
    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # Subtle gradient background
    for y in range(H):
        shade = int(18 + (y / H) * 12)
        draw.line([(0, y), (W, y)], fill=(shade, shade, shade+12))

    # ── VS divider ────────────────────────────────────────────────
    vs_x = HALF - VS_W // 2
    for i in range(VS_W):
        alpha = 1.0 - abs(i - VS_W//2) / (VS_W//2)
        c = int(30 + alpha * 25)
        draw.line([(vs_x+i, 0), (vs_x+i, H)], fill=(c, c, c+10))
    vf = _font(38)
    _centered_text(draw, HALF, H//2 - 28, "VS", vf, VS_COLOR)
    # turn indicator below VS
    tf = _font(13)
    turn_txt = "YOUR TURN" if turn_owner == "player" else "ENEMY TURN"
    turn_col = (60,200,100) if turn_owner == "player" else (220,80,80)
    _centered_text(draw, HALF, H//2 + 20, turn_txt, tf, turn_col)

    pad = 14
    PW  = vs_x - pad*2    # panel width

    # ══════════════════════════════════════════════════
    # LEFT PANEL — Player
    # ══════════════════════════════════════════════════
    lx = pad                # left panel x start

    # ── Top card (identity + bars) ────────────────────
    top_h = 160
    _round_rect(draw, (lx, pad, lx+PW, pad+top_h), 12, fill=PANEL_BG, outline=CARD_BORDER, width=2)

    # Avatar circle (left side of top card)
    ava_sz = 90
    ava_x, ava_y = lx+12, pad+10
    if player_img_raw:
        av = _circle_crop(player_img_raw.resize((ava_sz, ava_sz), Image.LANCZOS))
        img.paste(av, (ava_x, ava_y), av)
    else:
        ph = _placeholder_avatar(ava_sz, player_class_color, player_name[0].upper())
        img.paste(ph, (ava_x, ava_y), ph)

    # Border ring around avatar
    draw.ellipse((ava_x-3, ava_y-3, ava_x+ava_sz+3, ava_y+ava_sz+3),
                 outline=tuple(min(255, c+60) for c in player_class_color), width=3)

    # Name + class
    nf = _font(17)
    cf = _font(12)
    tx = ava_x + ava_sz + 12
    draw.text((tx, pad+10), player_name[:18], font=nf, fill=WHITE)
    draw.text((tx, pad+32), player_class, font=cf, fill=tuple(min(255, c+80) for c in player_class_color))

    # HP bar
    bar_x = tx; bar_w = lx+PW - tx - 14
    hp_pct  = player_hp  / max(player_hp_max, 1)
    mp_pct  = player_mp  / max(player_mp_max, 1)
    draw.text((bar_x, pad+52), f"❤ {player_hp}/{player_hp_max}", font=cf, fill=(255,100,100))
    _bar(draw, bar_x, pad+68, bar_w, 14, hp_pct, HP_FULL, HP_EMPTY)
    draw.text((bar_x, pad+90), f"💙 {player_mp}/{player_mp_max}", font=cf, fill=(100,160,255))
    _bar(draw, bar_x, pad+106, bar_w, 14, mp_pct, MP_FULL, MP_EMPTY)

    # Stats row inside top card (bottom of it)
    sf = _font(11)
    stats_y = pad+130
    stat_entries = [(f"⚔ {player_str}", (255,160,60)),
                    (f"🛡 {player_def}", (100,200,255)),
                    (f"⚡ {player_agi}", (200,255,100))]
    stx = lx+12
    for label, col in stat_entries:
        draw.text((stx, stats_y), label, font=sf, fill=col)
        bbox = sf.getbbox(label)
        stx += bbox[2]-bbox[0] + 20

    # Effects on player
    if effects_player:
        ef = _font(10)
        ex = lx + PW - 12
        for eff in effects_player[:3]:
            bbox = ef.getbbox(eff)
            tw = bbox[2]-bbox[0]
            draw.text((ex-tw, stats_y), eff, font=ef, fill=(255,200,100))
            ex -= tw + 8

    # ── Skill boxes ──────────────────────────────────────────────
    skill_y  = pad + top_h + 12
    skill_h  = 130
    n_skills = 3
    skill_w  = (PW - (n_skills-1)*8) // n_skills
    for i in range(n_skills):
        sx = lx + i*(skill_w+8)
        if i < len(player_skills):
            sk = player_skills[i]
            _draw_skill_box(draw, sx, skill_y, skill_w, skill_h,
                            sk.get("name","?"), sk.get("rank","F"), sk.get("on_cd", False))
        else:
            _round_rect(draw, (sx, skill_y, sx+skill_w, skill_y+skill_h), 8,
                        fill=(24,24,36), outline=(45,45,65), width=1)
            _centered_text(draw, sx+skill_w//2, skill_y+50, "—", _font(20), (60,60,80))

    # ── Equipment row ─────────────────────────────────────────────
    eq_y = skill_y + skill_h + 12
    eq_h = H - eq_y - pad
    _draw_equipment_row(draw, lx, eq_y, PW, eq_h, player_weapon, player_armor, on_left=True)

    # ══════════════════════════════════════════════════
    # RIGHT PANEL — Monster
    # ══════════════════════════════════════════════════
    rx = R_START + pad

    # ── Top card ─────────────────────────────────────────────────
    _round_rect(draw, (rx, pad, rx+PW, pad+top_h), 12, fill=PANEL_BG, outline=CARD_BORDER, width=2)

    # Avatar circle (right side)
    ava_rx = rx + PW - ava_sz - 12
    if monster_img_raw:
        av = _circle_crop(monster_img_raw.resize((ava_sz, ava_sz), Image.LANCZOS))
        img.paste(av, (ava_rx, ava_y), av)
    else:
        ph = _placeholder_avatar(ava_sz, monster_color, monster_name[0].upper())
        img.paste(ph, (ava_rx, ava_y), ph)

    draw.ellipse((ava_rx-3, ava_y-3, ava_rx+ava_sz+3, ava_y+ava_sz+3),
                 outline=tuple(min(255, c+60) for c in monster_color), width=3)

    if sleeping:
        draw.text((ava_rx+ava_sz-20, ava_y), "💤", font=_font(22), fill=WHITE)

    # Name + type (right-aligned)
    rtx = rx + PW - 14
    mn_bbox = nf.getbbox(monster_name[:18])
    draw.text((rtx - (mn_bbox[2]-mn_bbox[0]), pad+10), monster_name[:18], font=nf, fill=WHITE)
    mt_bbox = cf.getbbox(monster_type[:18])
    draw.text((rtx - (mt_bbox[2]-mt_bbox[0]), pad+32), monster_type[:18], font=cf,
              fill=tuple(min(255, c+80) for c in monster_color))

    # HP/MP bars (right panel — bars from right side)
    mbar_x = rx+14; mbar_w = ava_rx - rx - 28
    m_hp_pct = monster_hp / max(monster_hp_max, 1)
    m_mp_pct = monster_mp / max(monster_mp_max, 1)
    draw.text((mbar_x, pad+52), f"❤ {monster_hp}/{monster_hp_max}", font=cf, fill=(255,100,100))
    _bar(draw, mbar_x, pad+68, mbar_w, 14, m_hp_pct, HP_FULL, HP_EMPTY)
    if monster_mp_max > 0:
        draw.text((mbar_x, pad+90), f"💙 {monster_mp}/{monster_mp_max}", font=cf, fill=(100,160,255))
        _bar(draw, mbar_x, pad+106, mbar_w, 14, m_mp_pct, MP_FULL, MP_EMPTY)

    # Stats + effects
    ms_entries = [(f"⚔ {monster_str}", (255,160,60)),
                  (f"🛡 {monster_def}", (100,200,255)),
                  (f"⚡ {monster_agi}", (200,255,100))]
    mstx = rx+14
    for label, col in ms_entries:
        draw.text((mstx, stats_y), label, font=sf, fill=col)
        bbox = sf.getbbox(label)
        mstx += bbox[2]-bbox[0] + 20

    if effects_monster:
        ef = _font(10)
        ex = rx + PW - 12
        for eff in effects_monster[:3]:
            bbox = ef.getbbox(eff)
            tw = bbox[2]-bbox[0]
            draw.text((ex-tw, stats_y), eff, font=ef, fill=(255,130,130))
            ex -= tw + 8

    # Skill boxes (right panel)
    for i in range(n_skills):
        sx = rx + i*(skill_w+8)
        if i < len(monster_skills):
            sk = monster_skills[i]
            _draw_skill_box(draw, sx, skill_y, skill_w, skill_h,
                            sk.get("name","?"), sk.get("rank","F"), False)
        else:
            _round_rect(draw, (sx, skill_y, sx+skill_w, skill_y+skill_h), 8,
                        fill=(24,24,36), outline=(45,45,65), width=1)
            _centered_text(draw, sx+skill_w//2, skill_y+50, "—", _font(20), (60,60,80))

    # Equipment row (right panel, mirrored)
    _draw_equipment_row(draw, rx, eq_y, PW, eq_h, monster_weapon, monster_armor, on_left=False)

    # ── Combat log strip (bottom center) ─────────────────────────
    # Overlay a semi-transparent strip at the very bottom center
    if combat_log:
        log_y  = H - 44
        log_x0 = vs_x - 180
        log_x1 = vs_x + VS_W + 180
        _round_rect(draw, (log_x0, log_y-4, log_x1, H-6), 8, fill=(10,10,20), outline=(50,50,80), width=1)
        lf = _font(11)
        for j, line in enumerate(combat_log[-2:]):
            _centered_text(draw, HALF, log_y + j*16, line[:50], lf, (220,220,220))

    # ── Nightmare gauge (if applicable) ──────────────────────────
    # subtle indicator under monster HP bar when boss gauge > 0

    # ── Vignette ─────────────────────────────────────────────────
    vignette = Image.new("RGBA", (W, H), (0,0,0,0))
    vd = ImageDraw.Draw(vignette)
    for i in range(40):
        alpha = int(i * 3)
        vd.rectangle([i, i, W-i, H-i], outline=(0,0,0,alpha))
    img = img.convert("RGBA")
    img = Image.alpha_composite(img, vignette)
    img = img.convert("RGB")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf
