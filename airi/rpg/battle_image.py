# airi/rpg/battle_image.py — Battle Card Generator v3
# Matches the Image 1 reference: dark panels, character art fills, skill boxes with icons,
# HP/mana bars, stat row, VS divider, combat log strip.
from __future__ import annotations
import asyncio, io, math, random
from typing import Optional
import aiohttp
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ── Canvas ─────────────────────────────────────────────────────────
W, H   = 1200, 660
HALF   = W // 2
VS_W   = 80
L_END  = HALF - VS_W // 2
R_START= HALF + VS_W // 2
PAD    = 14

# ── Palette ────────────────────────────────────────────────────────
BG_DARK   = (8, 8, 14)
PANEL_BG  = (16, 18, 28)
CARD_BG   = (24, 26, 40)
BORDER    = (55, 60, 100)
GOLD_COL  = (200, 160, 40)
WHITE     = (240, 242, 255)
GREY      = (130, 135, 160)
HP_FULL   = (180, 40, 40)
HP_EMPTY  = (50, 10, 10)
MP_FULL   = (40, 100, 200)
MP_EMPTY  = (12, 28, 65)
VS_COL    = (220, 185, 50)
GREEN     = (50, 190, 80)
RED_LT    = (240, 70, 60)

RANK_COL = {
    "F":(80,80,80),"E":(50,140,50),"D":(50,90,200),"C":(50,50,200),
    "B":(130,50,200),"A":(200,140,20),"S":(210,40,40),
    "SS":(255,100,30),"SSS":(255,200,20),"Unknown":(70,70,110),
}

def _font(sz):
    for p in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ]:
        try: return ImageFont.truetype(p, sz)
        except: pass
    return ImageFont.load_default()

def _rrect(draw, xy, r=8, fill=None, outline=None, width=1):
    draw.rounded_rectangle(list(xy), radius=r, fill=fill, outline=outline, width=width)

def _txt(draw, pos, text, font, fill=WHITE, shadow=True):
    if shadow:
        draw.text((pos[0]+1, pos[1]+1), text, font=font, fill=(0,0,0,200))
    draw.text(pos, text, font=font, fill=fill)

def _ctxt(draw, cx, y, text, font, fill=WHITE):
    bb = font.getbbox(text); tw = bb[2]-bb[0]
    _txt(draw, (cx-tw//2, y), text, font, fill)

def _rtxt(draw, rx, y, text, font, fill=WHITE):
    bb = font.getbbox(text); tw = bb[2]-bb[0]
    _txt(draw, (rx-tw, y), text, font, fill)

def _bar(draw, x, y, w, h, pct, full, empty, r=5):
    _rrect(draw, (x,y,x+w,y+h), r, fill=empty)
    fw = max(r*2, int(w*max(0,min(1,pct))))
    _rrect(draw, (x,y,x+fw,y+h), r, fill=full)

async def _dl(url, sz):
    if not url: return None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status == 200:
                    return Image.open(io.BytesIO(await r.read())).convert("RGBA").resize(sz, Image.LANCZOS)
    except: pass
    return None

def _circle_crop(img, border_col=None, bw=3):
    sz = img.size
    mask = Image.new("L", sz, 0)
    ImageDraw.Draw(mask).ellipse((0,0,sz[0]-1,sz[1]-1), fill=255)
    out = Image.new("RGBA", sz, (0,0,0,0))
    out.paste(img.convert("RGBA"), mask=mask)
    if border_col:
        bd = Image.new("RGBA", sz, (0,0,0,0))
        ImageDraw.Draw(bd).ellipse((0,0,sz[0]-1,sz[1]-1), outline=border_col, width=bw)
        out = Image.alpha_composite(out, bd)
    return out

def _placeholder(sz, col, letter):
    img = Image.new("RGBA", (sz,sz), (0,0,0,0))
    d   = ImageDraw.Draw(img)
    # Gradient circle
    for r in range(sz//2, 0, -3):
        t = r/(sz//2)
        c = tuple(int(v*t) for v in col) + (int(255*t**0.5),)
        d.ellipse((sz//2-r,sz//2-r,sz//2+r,sz//2+r), fill=c)
    f  = _font(sz//2)
    bb = f.getbbox(letter)
    tx = (sz-(bb[2]-bb[0]))//2; ty = (sz-(bb[3]-bb[1]))//2
    d.text((tx+1,ty+1), letter, font=f, fill=(0,0,0,180))
    d.text((tx,ty), letter, font=f, fill=WHITE)
    return img

def _draw_panel_bg(canvas: Image.Image, x, y, w, h, base_color: tuple, is_left: bool):
    """Draw large panel with gradient tinting from base_color."""
    d = ImageDraw.Draw(canvas)
    # Dark gradient fill matching team color
    for i in range(h):
        t   = i / h
        r   = int(PANEL_BG[0] + base_color[0]*0.08 * (1-t))
        g_  = int(PANEL_BG[1] + base_color[1]*0.08 * (1-t))
        b   = int(PANEL_BG[2] + base_color[2]*0.15 * (1-t))
        r = min(255, r); g_ = min(255, g_); b = min(255, b)
        d.line([(x,y+i),(x+w,y+i)], fill=(r,g_,b))
    _rrect(d, (x,y,x+w,y+h), 12, fill=None, outline=BORDER, width=2)

def _skill_box(draw, x, y, w, h, name, rank, on_cd, mana_cost=0):
    rc   = RANK_COL.get(rank, (90,90,90))
    fill = (14,14,22) if on_cd else (28,30,48)
    bord = (50,50,70) if on_cd else rc
    _rrect(draw, (x,y,x+w,y+h), 10, fill=fill, outline=bord, width=2)
    # Rank pill top-right
    pw,ph = 26,15
    _rrect(draw, (x+w-pw-3,y+3,x+w-3,y+ph+3), 7, fill=rc)
    _ctxt(draw, x+w-pw//2-3, y+4, rank, _font(9), WHITE)
    # Cooldown icon or skill art placeholder
    mid_y = y + h//2 - 18
    if on_cd:
        _ctxt(draw, x+w//2, mid_y, "⏱", _font(22), (130,80,80))
    else:
        # Colored diamond icon based on rank
        size = 18
        ix, iy = x+w//2-size, mid_y
        rc_a = rc + (220,)
        pts  = [(x+w//2,mid_y),(x+w//2+size,mid_y+size),(x+w//2,mid_y+size*2),(x+w//2-size,mid_y+size)]
        draw.polygon(pts, fill=rc_a)
    # Skill name bottom
    short = (name[:11]+"…") if len(name)>11 else name
    _ctxt(draw, x+w//2, y+h-19, short, _font(10), GREY if on_cd else WHITE)
    # Mana cost badge bottom-right
    if mana_cost > 0:
        ms = str(mana_cost)
        _rrect(draw,(x+w-24,y+h-16,x+w-2,y+h-2),4,fill=(20,40,80))
        _ctxt(draw,x+w-13,y+h-15,ms,_font(8),(100,160,255))


async def generate_battle_card(
    # Player
    player_name:    str,
    player_class:   str,
    player_hp: int, player_hp_max: int,
    player_mp: int, player_mp_max: int,
    player_str: int, player_def: int, player_agi: int,
    player_skills:  list[dict],
    player_weapon:  str = "Unarmed",
    player_armor:   str = "None",
    player_avatar_url: str|None = None,
    player_class_color: tuple = (60, 80, 220),
    # Monster
    monster_name:   str = "Unknown",
    monster_type:   str = "Monster",
    monster_hp: int = 100, monster_hp_max: int = 100,
    monster_mp: int = 0,   monster_mp_max: int = 0,
    monster_str: int = 10, monster_def: int = 5, monster_agi: int = 5,
    monster_skills: list[dict] = None,
    monster_weapon: str = "Claws",
    monster_armor:  str = "Hide",
    monster_image_url: str|None = None,
    monster_color:  tuple = (200, 50, 50),
    # Battle state
    effects_player:  list[str] = None,
    effects_monster: list[str] = None,
    combat_log:      list[str] = None,
    turn_owner:      str = "player",    # "player" | "monster" | "none"
    sleeping:        bool = False,
    turn_number:     int  = 1,
) -> io.BytesIO:

    monster_skills  = monster_skills  or []
    effects_player  = effects_player  or []
    effects_monster = effects_monster or []
    combat_log      = combat_log      or []

    # Download avatars
    p_img, m_img = await asyncio.gather(
        _dl(player_avatar_url, (140,140)) if player_avatar_url else asyncio.sleep(0, result=None),
        _dl(monster_image_url, (140,140)) if monster_image_url else asyncio.sleep(0, result=None),
    )

    # ── Canvas ──────────────────────────────────────────────────────
    canvas = Image.new("RGB", (W, H), BG_DARK)
    draw   = ImageDraw.Draw(canvas)

    # Grid texture
    for x in range(0, W, 36):
        draw.line([(x,0),(x,H)], fill=(13,14,24), width=1)
    for y in range(0, H, 36):
        draw.line([(0,y),(W,y)], fill=(13,14,24), width=1)

    PW = L_END - PAD*2   # panel width

    # ── Left panel background ──────────────────────────────────────
    _draw_panel_bg(canvas, PAD, PAD, PW, H-PAD*2-60, player_class_color, True)

    # ── Right panel background ─────────────────────────────────────
    _draw_panel_bg(canvas, R_START+PAD, PAD, PW, H-PAD*2-60, monster_color, False)

    draw = ImageDraw.Draw(canvas)

    # ── VS divider ──────────────────────────────────────────────────
    vcx = HALF
    for i in range(VS_W):
        alpha = 1.0 - abs(i-VS_W//2)/(VS_W//2+1)
        c = int(12 + alpha*10)
        draw.line([(vcx-VS_W//2+i,0),(vcx-VS_W//2+i,H)], fill=(c,c,c+6))
    # VS text with glow effect
    vf = _font(46)
    for dx in range(-2,3):
        for dy in range(-2,3):
            if dx or dy:
                draw.text((vcx-28+dx, H//2-60+dy), "VS", font=vf, fill=(*VS_COL,40))
    _txt(draw, (vcx-28, H//2-60), "VS", vf, VS_COL)

    # Turn indicator
    turn_col = GREEN if turn_owner=="player" else (RED_LT if turn_owner=="monster" else GREY)
    turn_txt = (f"⚔️ YOUR TURN (T{turn_number})" if turn_owner=="player"
                else (f"👾 ENEMY TURN (T{turn_number})" if turn_owner=="monster"
                else "— BATTLE OVER —"))
    _ctxt(draw, vcx, H//2-10, turn_txt, _font(12), turn_col)

    # ── PLAYER PANEL (Left) ────────────────────────────────────────
    lx = PAD

    # Header card: avatar + name + HP/MP
    hdr_h = 165
    _rrect(draw, (lx,PAD,lx+PW,PAD+hdr_h), 12, fill=(20,22,36), outline=tuple(min(255,c+40) for c in player_class_color), width=2)

    # Avatar circle
    ava_sz = 100
    ax, ay = lx+10, PAD+12
    av = _circle_crop(
        (p_img.resize((ava_sz,ava_sz),Image.LANCZOS) if p_img else _placeholder(ava_sz,player_class_color,player_name[0].upper())),
        border_col=tuple(min(255,c+80) for c in player_class_color), bw=3
    )
    canvas_rgba = canvas.convert("RGBA")
    canvas_rgba.paste(av, (ax,ay), av)
    canvas = canvas_rgba.convert("RGB"); draw = ImageDraw.Draw(canvas)

    # Name & class label
    tx = ax+ava_sz+12
    _txt(draw, (tx,PAD+10), player_name[:18], _font(20), WHITE)
    _txt(draw, (tx,PAD+36), player_class, _font(13), tuple(min(255,c+100) for c in player_class_color))

    # HP bar
    bw = lx+PW-tx-10
    hp_pct = player_hp/max(player_hp_max,1); mp_pct = player_mp/max(player_mp_max,1)
    _txt(draw,(tx,PAD+58),f"HP: {player_hp}/{player_hp_max}",_font(12),(255,110,110))
    _bar(draw,tx,PAD+76,bw,14,hp_pct,HP_FULL,HP_EMPTY)
    _txt(draw,(tx,PAD+98),f"MP: {player_mp}/{player_mp_max}",_font(12),(100,160,255))
    _bar(draw,tx,PAD+116,bw,12,mp_pct,MP_FULL,MP_EMPTY)

    # Stats row (STR, DEF, AGI) with icons inside header
    st_y = PAD+136
    stx = lx+8
    for lbl, col in [(f"⚔  {player_str}",(255,165,60)),(f"🛡  {player_def}",(90,190,255)),(f"⚡  {player_agi}",(190,255,90))]:
        _txt(draw,(stx,st_y),lbl,_font(12),col)
        stx += _font(12).getbbox(lbl)[2]+18

    # Effects (right-align in header)
    if effects_player:
        ex = lx+PW-8
        for eff in effects_player[:3]:
            bb  = _font(10).getbbox(eff)
            tw  = bb[2]-bb[0]
            _txt(draw,(ex-tw,st_y),eff,_font(10),(255,220,80))
            ex -= tw+6

    # ── Skill boxes (3) ───────────────────────────────────────────
    sk_y = PAD+hdr_h+10; sk_h = 130; n_sk = 3
    sk_w = (PW-(n_sk-1)*8)//n_sk
    for i in range(n_sk):
        sx = lx + i*(sk_w+8)
        if i < len(player_skills):
            sk = player_skills[i]
            _skill_box(draw, sx, sk_y, sk_w, sk_h, sk.get("name","?"), sk.get("rank","F"),
                       sk.get("on_cd",False), sk.get("mana",0))
        else:
            _rrect(draw,(sx,sk_y,sx+sk_w,sk_y+sk_h),10,fill=(14,15,24),outline=(40,42,65),width=1)
            _ctxt(draw,sx+sk_w//2,sk_y+sk_h//2-8,"—",_font(18),BORDER)

    # ── Weapon/armor strip ─────────────────────────────────────────
    wp_y = sk_y+sk_h+10; wp_h = H-wp_y-PAD-56
    _rrect(draw,(lx,wp_y,lx+PW,wp_y+wp_h),8,fill=CARD_BG,outline=BORDER,width=1)
    # Weapon icon
    ico = 32
    _rrect(draw,(lx+6,wp_y+6,lx+6+ico,wp_y+6+ico),6,fill=(30,30,50),outline=GOLD_COL,width=1)
    _ctxt(draw,lx+6+ico//2,wp_y+13,"⚔",_font(16),GOLD_COL)
    bx = lx+ico+14; bw2 = PW-ico-20
    _txt(draw,(bx,wp_y+5),player_weapon[:22],_font(11),GOLD_COL)
    _bar(draw,bx,wp_y+20,bw2,10,0.75,GOLD_COL,(45,35,8))
    # Armor
    _txt(draw,(bx,wp_y+34),player_armor[:22],_font(11),(120,170,255))
    _bar(draw,bx,wp_y+49,bw2,10,0.60,(120,170,255),(15,30,60))

    # ── MONSTER PANEL (Right) ──────────────────────────────────────
    rx = R_START+PAD

    _rrect(draw,(rx,PAD,rx+PW,PAD+hdr_h),12,fill=(20,22,36),outline=tuple(min(255,c+40) for c in monster_color),width=2)

    # Monster avatar (right side)
    mav_x = rx+PW-ava_sz-10
    mav   = _circle_crop(
        (m_img.resize((ava_sz,ava_sz),Image.LANCZOS) if m_img else _placeholder(ava_sz,monster_color,monster_name[0].upper())),
        border_col=tuple(min(255,c+80) for c in monster_color), bw=3
    )
    canvas_rgba = canvas.convert("RGBA")
    canvas_rgba.paste(mav,(mav_x,ay),mav)
    canvas = canvas_rgba.convert("RGB"); draw = ImageDraw.Draw(canvas)

    if sleeping:
        _txt(draw,(mav_x+ava_sz-24,ay),"💤",_font(20),WHITE)

    # Monster name (right-aligned)
    _rtxt(draw,rx+PW-8,PAD+10,monster_name[:18],_font(20),WHITE)
    _rtxt(draw,rx+PW-8,PAD+36,monster_type[:18],_font(13),tuple(min(255,c+100) for c in monster_color))

    # HP/MP bars (left of avatar)
    mbw = mav_x-rx-20; mbx = rx+10
    m_hp_pct = monster_hp/max(monster_hp_max,1)
    _txt(draw,(mbx,PAD+58),f"HP: {monster_hp}/{monster_hp_max}",_font(12),(255,110,110))
    _bar(draw,mbx,PAD+76,mbw,14,m_hp_pct,HP_FULL,HP_EMPTY)
    if monster_mp_max > 0:
        m_mp_pct = monster_mp/max(monster_mp_max,1)
        _txt(draw,(mbx,PAD+98),f"MP: {monster_mp}/{monster_mp_max}",_font(12),(100,160,255))
        _bar(draw,mbx,PAD+116,mbw,12,m_mp_pct,MP_FULL,MP_EMPTY)

    # Monster stats
    mstx = rx+10
    for lbl, col in [(f"⚔  {monster_str}",(255,165,60)),(f"🛡  {monster_def}",(90,190,255)),(f"⚡  {monster_agi}",(190,255,90))]:
        _txt(draw,(mstx,st_y),lbl,_font(12),col)
        mstx += _font(12).getbbox(lbl)[2]+18

    # Monster effects
    if effects_monster:
        ex = rx+PW-8
        for eff in effects_monster[:3]:
            bb  = _font(10).getbbox(eff)
            tw  = bb[2]-bb[0]
            _txt(draw,(ex-tw,st_y),eff,_font(10),(255,130,130))
            ex -= tw+6

    # Monster skill boxes
    for i in range(n_sk):
        sx = rx + i*(sk_w+8)
        if i < len(monster_skills):
            sk = monster_skills[i]
            _skill_box(draw,sx,sk_y,sk_w,sk_h,sk.get("name","?"),sk.get("rank","F"),False,0)
        else:
            _rrect(draw,(sx,sk_y,sx+sk_w,sk_y+sk_h),10,fill=(14,15,24),outline=(40,42,65),width=1)
            _ctxt(draw,sx+sk_w//2,sk_y+sk_h//2-8,"—",_font(18),BORDER)

    # Monster equipment strip
    _rrect(draw,(rx,wp_y,rx+PW,wp_y+wp_h),8,fill=CARD_BG,outline=BORDER,width=1)
    # Mirrored: icon right side
    _rrect(draw,(rx+PW-ico-6,wp_y+6,rx+PW-6,wp_y+6+ico),6,fill=(30,30,50),outline=GOLD_COL,width=1)
    _ctxt(draw,rx+PW-6-ico//2,wp_y+13,"⚔",_font(16),GOLD_COL)
    mbx2 = rx+8; mbw3 = PW-ico-20
    _txt(draw,(mbx2,wp_y+5),monster_weapon[:22],_font(11),GOLD_COL)
    _bar(draw,mbx2,wp_y+20,mbw3,10,0.80,GOLD_COL,(45,35,8))
    _txt(draw,(mbx2,wp_y+34),monster_armor[:22],_font(11),(120,170,255))
    _bar(draw,mbx2,wp_y+49,mbw3,10,0.65,(120,170,255),(15,30,60))

    # ── Nightmare gauge bar (under monster panel) ──────────────────
    # (placeholder — shown only when gauge > 0)

    # ── Combat log strip (bottom center) ──────────────────────────
    log_y = H-54
    log_w = 580
    _rrect(draw,(HALF-log_w//2,log_y-4,HALF+log_w//2,H-10),10,fill=(8,8,16),outline=(50,55,90),width=2)
    if combat_log:
        for j,ln in enumerate(combat_log[-2:]):
            col = WHITE if j == len(combat_log[-2:])-1 else GREY
            _ctxt(draw,HALF,log_y+j*17,ln[:65],_font(11),col)

    # ── Vignette ─────────────────────────────────────────────────
    vig = Image.new("RGBA",(W,H),(0,0,0,0))
    vd  = ImageDraw.Draw(vig)
    for i in range(50):
        a = int(i*3.5)
        vd.rectangle([i,i,W-i,H-i],outline=(0,0,0,a))
    canvas = Image.alpha_composite(canvas.convert("RGBA"),vig).convert("RGB")

    buf = io.BytesIO()
    canvas.save(buf,format="PNG",optimize=True)
    buf.seek(0)
    return buf
