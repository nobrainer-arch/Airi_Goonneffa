# airi/rpg/character.py — Character system: DnD races/classes, leveling, VIT/HP scaling
# Stats shown like manhwa: Name / Class [Race] / Level / STR / AGI / SPI / CON / VIT
# Equipment: [] | Skills: [] | Accessories: []
# Mana = SPI stat | HP = base + VIT * 10

import aiohttp
import discord
from discord.ext import commands
import db
from utils import C_INFO, C_SUCCESS, C_WARN

DND_API = "https://www.dnd5eapi.co/api"

# ── XP curve: roughly doubles each tier ────────────────────────────
# Level N needs XP_TABLE[N-1] total XP to reach level N
XP_TABLE = [
    0,      # Lv 1  (starting)
    300,    # Lv 2
    900,    # Lv 3
    2700,   # Lv 4
    6500,   # Lv 5
    14000,  # Lv 6
    23000,  # Lv 7
    34000,  # Lv 8
    48000,  # Lv 9
    64000,  # Lv 10  — Nightmare unlocks
    85000,  # Lv 11
    100000, # Lv 12
    120000, # Lv 13
    140000, # Lv 14
    165000, # Lv 15
    195000, # Lv 16
    225000, # Lv 17
    265000, # Lv 18
    305000, # Lv 19
    355000, # Lv 20  — Hell unlocks
    425000, # Lv 21
    495000, # Lv 22
    570000, # Lv 23
    650000, # Lv 24
    735000, # Lv 25
    820000, # Lv 26
    915000, # Lv 27
    1015000,# Lv 28
    1120000,# Lv 29
    1230000,# Lv 30
]
MAX_CHAR_LEVEL = len(XP_TABLE)

def xp_for_level(level: int) -> int:
    """Total XP needed to reach `level` (1-based)."""
    if level <= 1: return 0
    return XP_TABLE[min(level-1, len(XP_TABLE)-1)]

def xp_to_next(current_xp: int) -> tuple[int, int]:
    """Returns (current_level, xp_needed_for_next_level)."""
    level = 1
    for i, threshold in enumerate(XP_TABLE):
        if current_xp >= threshold:
            level = i + 1
        else:
            break
    needed = XP_TABLE[min(level, len(XP_TABLE)-1)] - current_xp
    return level, max(0, needed)

# ── Dungeon level caps per tier ────────────────────────────────────
# Every 10 character levels unlocks the next dungeon tier
# Monster base stats are capped so Normal/Nightmare/Hell can multiply safely
DUNGEON_TIERS = {
    1:  {"name":"Tier I",   "min_level":1,  "monster_stat_cap":30,  "max_monster_level":5},
    2:  {"name":"Tier II",  "min_level":10, "monster_stat_cap":60,  "max_monster_level":15},
    3:  {"name":"Tier III", "min_level":20, "monster_stat_cap":100, "max_monster_level":30},
    4:  {"name":"Tier IV",  "min_level":30, "monster_stat_cap":180, "max_monster_level":50},
    5:  {"name":"Tier V",   "min_level":40, "monster_stat_cap":300, "max_monster_level":80},
}

DIFFICULTY_MULT = {
    "normal":    {"stat":1.0, "xp":1.0, "loot":1.0, "label":"⚔️ Normal",   "color":0x27ae60, "min_level":1},
    "nightmare": {"stat":3.0, "xp":3.0, "loot":3.0, "label":"💀 Nightmare","color":0xe74c3c, "min_level":10},
    "hell":      {"stat":5.0, "xp":5.0, "loot":5.0, "label":"🔥 Hell",     "color":0xff0000, "min_level":20},
}

def get_dungeon_tier(char_level: int) -> int:
    tier = 1
    for t, info in DUNGEON_TIERS.items():
        if char_level >= info["min_level"]:
            tier = t
    return tier

def scale_monster(base_stats: dict, difficulty: str, tier: int) -> dict:
    """Scale monster stats by difficulty, capped so code doesn't break."""
    mult  = DIFFICULTY_MULT.get(difficulty, DIFFICULTY_MULT["normal"])["stat"]
    cap   = DUNGEON_TIERS[tier]["monster_stat_cap"]
    s     = dict(base_stats)
    for key in ("str","def","agi","spi","con","vit"):
        if key in s:
            s[key] = min(int(s[key] * mult), cap)
    if "hp" in s:
        s["hp"] = min(int(s["hp"] * mult), cap * 50)
    return s

# ── HP / Mana formulas ─────────────────────────────────────────────
def calc_hp_max(con: int, vit: int, level: int) -> int:
    """HP = (10 + CON×2 + VIT×10) × (1 + level×0.05)"""
    base = 10 + con * 2 + vit * 10
    return max(10, int(base * (1 + level * 0.05)))

def calc_mana_max(spi: int, level: int) -> int:
    """Mana = SPI × 3 + level×5  (Mana capacity = Spirit)"""
    return max(10, spi * 3 + level * 5)

# ── Stat growth per level per class ────────────────────────────────
CLASS_GROWTH = {
    "Shadow":     {"str":2,"agi":4,"spi":1,"con":2,"vit":1},
    "Warrior":    {"str":4,"agi":1,"spi":0,"con":3,"vit":3},
    "Mage":       {"str":0,"agi":1,"spi":5,"con":1,"vit":1},
    "Necromancer":{"str":1,"agi":1,"spi":4,"con":1,"vit":1},
    "Archer":     {"str":2,"agi":4,"spi":1,"con":1,"vit":2},
    "Gunman":     {"str":2,"agi":3,"spi":1,"con":2,"vit":2},
    "Knight":     {"str":2,"agi":0,"spi":1,"con":4,"vit":4},
    "Healer":     {"str":0,"agi":1,"spi":4,"con":2,"vit":2},
}

# ── DnD Race → stat bonuses (D&D 5e flavor, adapted to our system) ──
RACE_BONUSES = {
    "Human":      {"str":1,"agi":1,"spi":1,"con":1,"vit":1},
    "Elf":        {"agi":2,"spi":1},
    "Dwarf":      {"con":2,"vit":2},
    "Halfling":   {"agi":2},
    "Dragonborn": {"str":2,"con":1},
    "Gnome":      {"spi":2},
    "Half-Elf":   {"agi":1,"spi":1,"con":1},
    "Half-Orc":   {"str":2,"vit":1},
    "Tiefling":   {"spi":2,"agi":1},
}

# DnD 5e SRD races available for selection
DND_RACES = list(RACE_BONUSES.keys())

# ── Character stat sheet display (manhwa style) ────────────────────
def _bar(cur, mx, n=12):
    f = max(0, int((cur/max(mx,1))*n))
    return "█"*f + "░"*(n-f)

def char_sheet_embed(char: dict, member: discord.Member) -> discord.Embed:
    """Manhwa-style character sheet matching the screenshots."""
    from airi.rpg.classes import CLASSES, get_realm, str_label

    cls   = char.get("class","Warrior")
    race  = char.get("race","Human")
    clvl  = char.get("char_level", char.get("realm_level",1))
    cxp   = char.get("char_xp", 0)
    _, xp_needed = xp_to_next(cxp)

    realm_name, realm_emoji = get_realm(clvl)
    cls_info = CLASSES.get(cls, {})
    color    = cls_info.get("color", 0x5d6bb5)

    str_  = char.get("strength",10)
    agi   = char.get("agility",10)
    spi   = char.get("spirit",10)
    con   = char.get("constitution",10)
    vit   = char.get("vitality",10)
    hp_c  = char.get("hp_current",100)
    hp_m  = char.get("hp_max",100)
    mn_c  = char.get("mana_current",50)
    mn_m  = char.get("mana_max",50)

    e = discord.Embed(color=color)
    e.set_author(name=member.display_name, icon_url=member.display_avatar.url)

    # Header block — manhwa style
    e.add_field(name="\u200b", value=(
        f"```\n"
        f"[NAME: {member.display_name.upper()}]\n"
        f"[OCCUPATION: {cls.upper()} [{cls_info.get('talent_rank','?')}]]\n"
        f"[RACE: {race.upper()}]\n"
        f"[LEVEL: {clvl} [{int(100*(cxp-xp_for_level(clvl))/(xp_needed or 1)):.2f}%]]\n"
        f"```"
    ), inline=False)

    # Stats block
    e.add_field(name="\u200b", value=(
        f"```\n"
        f"[STRENGTH: {str_} ({str_label(str_)})]  ×2\n"
        f"[AGILITY:  {agi}]\n"
        f"[SPIRIT:   {spi}]\n"
        f"[PHYSIQUE: {con}]\n"
        f"[VITALITY: {vit}]\n"
        f"```"
    ), inline=True)

    # Vitals block
    e.add_field(name="\u200b", value=(
        f"```\n"
        f"[HP:   {hp_c}/{hp_m}]\n"
        f"[MANA: {mn_c}/{mn_m}]\n"
        f"[EXP:  {cxp-xp_for_level(clvl)}/{xp_needed}]\n"
        f"```"
    ), inline=True)

    # Equipment, Skills, Accessories
    equip_names = char.get("_equipment_names", ["NONE"])
    skill_names = char.get("_skill_names", [])
    acc_names   = char.get("_accessory_names", [])

    e.add_field(name="\u200b", value=(
        f"```\n"
        f"[EQUIPMENT: {', '.join(equip_names[:3]) or 'NONE'}]\n"
        f"[SKILLS:    {', '.join(skill_names[:4]) or 'NONE'}]\n"
        f"[ACCESSORIES: {', '.join(acc_names[:3]) or 'NONE'}]\n"
        f"```"
    ), inline=False)

    # HP / Mana bars
    e.add_field(
        name=f"❤️ HP  {hp_c}/{hp_m}",
        value=f"`{_bar(hp_c, hp_m)}`",
        inline=True,
    )
    e.add_field(
        name=f"💙 Mana  {mn_c}/{mn_m}",
        value=f"`{_bar(mn_c, mn_m)}`",
        inline=True,
    )

    e.set_footer(text=f"Realm: {realm_emoji} {realm_name}  ·  /rpg allocate to spend stat points")
    return e


# ── DB helpers ─────────────────────────────────────────────────────
async def get_char_full(gid: int, uid: int) -> dict | None:
    """Load character with equipment/skill names for display."""
    row = await db.pool.fetchrow("SELECT * FROM rpg_characters WHERE guild_id=$1 AND user_id=$2", gid, uid)
    if not row: return None
    char = dict(row)
    # Fill missing new cols
    char.setdefault("vitality",    10)
    char.setdefault("char_level",  char.get("realm_level",1))
    char.setdefault("char_xp",     0)
    char.setdefault("race",        "Human")

    # Load equipment names
    eq = await db.pool.fetch("SELECT slot, item_name FROM rpg_equipment WHERE guild_id=$1 AND user_id=$2", gid, uid)
    eq_map = {r["slot"]: r["item_name"] for r in eq}
    char["_equipment_names"] = [v for k,v in eq_map.items() if k in ("weapon","armor")]
    char["_accessory_names"] = [v for k,v in eq_map.items() if k not in ("weapon","armor")]

    # Load skill names
    sk = await db.pool.fetch("SELECT skill_name FROM rpg_skills WHERE guild_id=$1 AND user_id=$2 LIMIT 8", gid, uid)
    char["_skill_names"] = [r["skill_name"] for r in sk]

    return char

async def add_char_xp(gid: int, uid: int, xp_gain: int) -> dict:
    """Add XP to character. Returns dict with leveled_up, new_level."""
    row = await db.pool.fetchrow(
        "SELECT char_level, char_xp, spirit, constitution, vitality FROM rpg_characters WHERE guild_id=$1 AND user_id=$2",
        gid, uid
    )
    if not row: return {"leveled_up": False, "new_level": 1}

    old_level = row.get("char_level") or row.get("realm_level", 1)
    old_xp    = row.get("char_xp", 0) or 0
    new_xp    = old_xp + xp_gain
    new_level, _ = xp_to_next(new_xp)
    new_level = max(new_level, 1)  # xp_to_next returns current level

    # Actually recalculate level from XP table
    calc_level = 1
    for i, threshold in enumerate(XP_TABLE):
        if new_xp >= threshold:
            calc_level = i + 1
    new_level = min(calc_level, MAX_CHAR_LEVEL)

    leveled_up = new_level > old_level

    # Update XP + level
    update_sql = "UPDATE rpg_characters SET char_xp=$1, char_level=$2"
    params     = [new_xp, new_level]

    if leveled_up:
        # Apply class growth stats
        cls_row = await db.pool.fetchval("SELECT class FROM rpg_characters WHERE guild_id=$1 AND user_id=$2", gid, uid)
        growth  = CLASS_GROWTH.get(cls_row or "Warrior", CLASS_GROWTH["Warrior"])
        levels_gained = new_level - old_level

        str_add = growth["str"] * levels_gained
        agi_add = growth["agi"] * levels_gained
        spi_add = growth["spi"] * levels_gained
        con_add = growth["con"] * levels_gained
        vit_add = growth["vit"] * levels_gained

        update_sql += (", strength=strength+$3, agility=agility+$4"
                       ", spirit=spirit+$5, constitution=constitution+$6"
                       ", vitality=vitality+$7")
        params += [str_add, agi_add, spi_add, con_add, vit_add]

        # Recalculate HP/Mana after stat gain
        # Need current stats
        new_con = (row.get("constitution",10) or 10) + con_add
        new_vit = (row.get("vitality",10) or 10) + vit_add
        new_spi = (row.get("spirit",10) or 10) + spi_add
        new_hp  = calc_hp_max(new_con, new_vit, new_level)
        new_mn  = calc_mana_max(new_spi, new_level)
        update_sql += ", hp_max=$8, hp_current=$8, mana_max=$9, mana_current=$9, stat_points=stat_points+2"
        params += [new_hp, new_mn]

    idx = len(params)+1
    update_sql += f" WHERE guild_id=${idx} AND user_id=${idx+1}"
    params += [gid, uid]
    await db.pool.execute(update_sql, *params)

    return {"leveled_up": leveled_up, "new_level": new_level, "old_level": old_level,
            "xp_gained": xp_gain, "total_xp": new_xp}


# ── Race selection UI ──────────────────────────────────────────────
class RaceSelectView(discord.ui.View):
    def __init__(self, ctx, on_select_cb):
        super().__init__(timeout=180)
        self._ctx = ctx
        self._cb  = on_select_cb

        opts = [
            discord.SelectOption(
                label=race,
                value=race,
                description=self._bonus_desc(race),
            ) for race in DND_RACES
        ]
        sel = discord.ui.Select(placeholder="Choose your race…", options=opts)
        sel.callback = self._on_pick
        self.add_item(sel)

    def _bonus_desc(self, race: str) -> str:
        b = RACE_BONUSES.get(race, {})
        parts = []
        labels = {"str":"STR","agi":"AGI","spi":"SPI","con":"CON","vit":"VIT"}
        for k,v in b.items():
            parts.append(f"+{v} {labels.get(k,k.upper())}")
        return ", ".join(parts) or "Balanced"

    async def _on_pick(self, interaction: discord.Interaction):
        if interaction.user.id != self._ctx.author.id:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        race = interaction.data["values"][0]
        for c in self.children: c.disabled = True
        await interaction.response.edit_message(view=self)
        await self._cb(race, interaction)
        self.stop()

    def _embed(self) -> discord.Embed:
        e = discord.Embed(
            title="🧬 Choose Your Race",
            description="Your race gives permanent stat bonuses to your character.",
            color=C_INFO,
        )
        for race in DND_RACES:
            bonus = self._bonus_desc(race)
            e.add_field(name=race, value=bonus, inline=True)
        return e
