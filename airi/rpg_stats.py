# airi/rpg_stats.py — System RPG: Character Stats, Classes, Realms, Talents, Skills, Equipment
# Inspired by manhwa-style RPG stat panels (Name / Class / Realm / STR / DEF / AGI / SPI / HP / Mana / EXP)
import discord
from discord.ext import commands
from datetime import datetime, timezone
import db
from utils import C_INFO, C_SUCCESS, C_ERROR, C_WARN, _err

# ── Rank system ───────────────────────────────────────────────────
RANK_ORDER  = ["F", "E", "D", "C", "B", "A", "S", "SS", "SSS"]
RANK_COLORS = {
    "F": 0x808080, "E": 0x95a5a6, "D": 0x27ae60,
    "C": 0x2980b9, "B": 0x8e44ad, "A": 0xf39c12,
    "S": 0xe74c3c, "SS": 0xff6b35, "SSS": 0xffd700,
    "Unknown": 0x5d5d8a,
}
RANK_EMOJI = {
    "F": "⬜", "E": "🟩", "D": "🟦", "C": "🔵",
    "B": "🟣", "A": "🟠", "S": "🔴", "SS": "🌟", "SSS": "💫",
    "Unknown": "❓",
}

# ── Realm progression ────────────────────────────────────────────
REALMS = [
    ("Apprentice",    1,  10, "🌱"),
    ("Disciple",     11,  25, "⚔️"),
    ("Middle Stage", 26,  50, "🔥"),
    ("Late Stage",   51,  75, "⚡"),
    ("Peak",         76,  99, "🌙"),
    ("Transcendent",100, 999, "✨"),
]

def get_realm(level: int) -> tuple[str, str]:
    for name, lo, hi, emoji in REALMS:
        if lo <= level <= hi:
            return name, emoji
    return "Transcendent", "✨"

# ── Strength qualifier labels ─────────────────────────────────────
STR_TIERS = [
    (1000, "Transcendent"),
    (500,  "Formidable"),
    (200,  "Mighty"),
    (100,  "Powerful"),
    (50,   "Overwhelming Vigour"),
    (25,   "Average"),
    (10,   "Below Average"),
    (0,    "Weak"),
]
def str_label(value: int) -> str:
    for threshold, label in STR_TIERS:
        if value >= threshold: return label
    return "Weak"

# ── Classes ───────────────────────────────────────────────────────
CLASSES: dict[str, dict] = {
    "Necromancer": {
        "emoji": "⚰️", "color": 0x6a0dad,
        "desc": "Master of death and dark arts. High Spirit and Mana, low physical stats.",
        "base": {"str": 8, "def": 5, "agi": 7, "spi": 18, "hp": 70, "mana": 120},
        "talent_name": "Death's Embrace",
        "talent_rank": "Unknown",
        "passive": "First skill used each battle deals **+15% damage**.",
        "active": "**Corpse Summon** — Raise a skeletal ally that fights for 2 turns. Cost: 40 mana. (Cooldown: 4h)",
        "starting_skills": [("Skeleton Summoning Magic", "F"), ("Withering Magic", "B")],
    },
    "Warrior": {
        "emoji": "⚔️", "color": 0xe74c3c,
        "desc": "Frontline powerhouse. Exceptional Strength and Defence.",
        "base": {"str": 18, "def": 15, "agi": 8, "spi": 3, "hp": 120, "mana": 30},
        "talent_name": "Berserker's Soul",
        "talent_rank": "A",
        "passive": "When HP falls below 30%, Strength increases by **+50%**.",
        "active": "**Berserk** — Triple STR for 1 turn, take 2× damage. Cost: 20 mana. (Cooldown: 6h)",
        "starting_skills": [("Heavy Strike", "D"), ("War Cry", "C")],
    },
    "Mage": {
        "emoji": "🔮", "color": 0x3498db,
        "desc": "Arcane power incarnate. The Brave Heart talent is one-of-a-kind.",
        "base": {"str": 6, "def": 5, "agi": 8, "spi": 20, "hp": 60, "mana": 150},
        "talent_name": "Brave Heart of a Mage",
        "talent_rank": "Unknown",
        "passive": "Gain **free stat points** after each enemy killed with a physical attack.",
        "active": "**Attribute Equalize** — Set all stats equal to your highest stat value. (Cooldown: 24h · Unique)",
        "restriction": "Before using the active effect, only one stat can be selected as the reference value. This ability is truly one-of-a-kind.",
        "starting_skills": [("Fireball", "C"), ("Mana Shield", "D")],
    },
    "Archer": {
        "emoji": "🏹", "color": 0x27ae60,
        "desc": "Swift and lethal. High Agility with natural critical hit talent.",
        "base": {"str": 12, "def": 7, "agi": 18, "spi": 5, "hp": 80, "mana": 50},
        "talent_name": "Eagle Eye",
        "talent_rank": "B",
        "passive": "**+20% critical hit** chance on all attacks.",
        "active": "**Aimed Shot** — Guaranteed critical strike. Cannot miss. Cost: 30 mana. (Cooldown: 2h)",
        "starting_skills": [("Multi-Arrow", "D"), ("Wind Step", "C")],
    },
    "Gunman": {
        "emoji": "🔫", "color": 0xf39c12,
        "desc": "Lightning-fast gunslinger. First attack is always devastating.",
        "base": {"str": 14, "def": 7, "agi": 16, "spi": 5, "hp": 85, "mana": 50},
        "talent_name": "Brave Heart of a Gunman",
        "talent_rank": "A",
        "passive": "First attack each battle deals **+50% damage** (like Ring of the Skeleton King, built-in).",
        "active": "**Rapid Fire** — Attack 3 times in one turn. Cost: 35 mana. (Cooldown: 4h)",
        "starting_skills": [("Bullet Rain", "C"), ("Quick Draw", "B")],
    },
    "Knight": {
        "emoji": "🛡️", "color": 0x95a5a6,
        "desc": "Impenetrable defender. Highest Defence, powerful counter-attack talent.",
        "base": {"str": 12, "def": 20, "agi": 5, "spi": 5, "hp": 130, "mana": 20},
        "talent_name": "Iron Wall",
        "talent_rank": "S",
        "passive": "Reflect **10% of all damage received** back to the attacker.",
        "active": "**Shield Bash** — Stun enemy for 1 turn. Unblockable. Cost: 15 mana. (Cooldown: 3h)",
        "starting_skills": [("Taunt", "C"), ("Counter Strike", "B")],
    },
    "Healer": {
        "emoji": "💚", "color": 0x1abc9c,
        "desc": "Life-sustaining support. High Spirit and constant regeneration.",
        "base": {"str": 5, "def": 8, "agi": 10, "spi": 22, "hp": 70, "mana": 130},
        "talent_name": "Light's Touch",
        "talent_rank": "A",
        "passive": "Recover **5% of max HP** every 2 turns in battle.",
        "active": "**Holy Heal** — Restore 40% of max HP. Can target an ally. Cost: 50 mana. (Cooldown: 2h)",
        "starting_skills": [("Heal", "C"), ("Blessing", "B")],
    },
}

# ── DB helpers ─────────────────────────────────────────────────────
async def get_char(gid: int, uid: int) -> dict | None:
    row = await db.pool.fetchrow(
        "SELECT * FROM rpg_characters WHERE guild_id=$1 AND user_id=$2", gid, uid
    )
    return dict(row) if row else None

async def create_char(gid: int, uid: int, class_name: str) -> dict:
    cls  = CLASSES[class_name]
    base = cls["base"]
    row  = await db.pool.fetchrow("""
        INSERT INTO rpg_characters
            (guild_id, user_id, class, realm_level,
             strength, defence, agility, spirit,
             hp_max, hp_current, mana_max, mana_current,
             stat_points, talent)
        VALUES ($1,$2,$3,1,$4,$5,$6,$7,$8,$8,$9,$9,5,$10)
        ON CONFLICT (guild_id, user_id) DO NOTHING
        RETURNING *
    """, gid, uid, class_name,
        base["str"], base["def"], base["agi"], base["spi"],
        base["hp"], base["mana"], cls["talent_name"]
    )
    for skill_name, skill_rank in cls["starting_skills"]:
        await db.pool.execute("""
            INSERT INTO rpg_skills (guild_id, user_id, skill_name, skill_rank)
            VALUES ($1,$2,$3,$4) ON CONFLICT (guild_id, user_id, skill_name) DO NOTHING
        """, gid, uid, skill_name, skill_rank)
    return dict(row) if row else await get_char(gid, uid)

async def get_skills(gid: int, uid: int) -> list[dict]:
    rows = await db.pool.fetch(
        "SELECT * FROM rpg_skills WHERE guild_id=$1 AND user_id=$2 ORDER BY skill_name",
        gid, uid
    )
    return [dict(r) for r in rows]

async def get_equipment(gid: int, uid: int) -> list[dict]:
    rows = await db.pool.fetch(
        "SELECT * FROM rpg_equipment WHERE guild_id=$1 AND user_id=$2",
        gid, uid
    )
    return [dict(r) for r in rows]

# ── UI helpers ────────────────────────────────────────────────────
def _hp_bar(cur: int, mx: int, length: int = 12) -> str:
    filled = max(0, int((cur / max(mx, 1)) * length))
    return "█" * filled + "░" * (length - filled)

# ── Embed builders ─────────────────────────────────────────────────
def build_stats_embed(char: dict, member: discord.Member) -> discord.Embed:
    cls_info   = CLASSES.get(char["class"], {})
    realm, rem = get_realm(char["realm_level"])
    str_tier   = str_label(char["strength"])
    color      = cls_info.get("color", C_INFO)

    e = discord.Embed(
        title=f"{cls_info.get('emoji','⚔️')} Character Sheet",
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    e.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    e.set_thumbnail(url=member.display_avatar.url)

    e.add_field(
        name="📋 Identity",
        value=(
            f"**Name:** {member.display_name}\n"
            f"**Class:** {char['class']}\n"
            f"**Realm:** {rem} {realm} [Lv.{char['realm_level']}]\n"
            f"**Talent:** {char.get('talent','???')} [Rank Unknown]"
        ),
        inline=False,
    )
    e.add_field(
        name="⚔️ Combat Stats",
        value=(
            f"**STR:** {char['strength']}  *[{str_tier}]*\n"
            f"**DEF:** {char['defence']}\n"
            f"**AGI:** {char['agility']}\n"
            f"**SPI:** {char['spirit']}"
        ),
        inline=True,
    )
    e.add_field(
        name="💫 Vitals",
        value=(
            f"**HP:** {char['hp_current']}/{char['hp_max']}\n"
            f"`{_hp_bar(char['hp_current'], char['hp_max'])}` ❤️\n"
            f"**Mana:** {char['mana_current']}/{char['mana_max']}\n"
            f"`{_hp_bar(char['mana_current'], char['mana_max'])}` 💙"
        ),
        inline=True,
    )
    if char.get("stat_points", 0) > 0:
        e.add_field(
            name="✨ Free Stat Points",
            value=f"**{char['stat_points']}** point(s) — use `/rpg allocate`!",
            inline=False,
        )
    e.set_footer(text="📚 /rpg talent  ·  /rpg skills  ·  /rpg equip  ·  /rpg allocate")
    return e


def build_talent_embed(char: dict, member: discord.Member) -> discord.Embed:
    cls_info = CLASSES.get(char["class"], {})
    trank    = cls_info.get("talent_rank", "Unknown")
    color    = RANK_COLORS.get(trank, cls_info.get("color", C_INFO))

    e = discord.Embed(
        title=f"✨ Talent: {char.get('talent','???')}",
        description=f"**Rank:** [{trank}]",
        color=color,
    )
    e.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    e.add_field(name="🔵 Passive Effect", value=cls_info.get("passive", "Unknown"), inline=False)
    e.add_field(name="🔴 Active Effect (Unique)", value=cls_info.get("active", "Unknown"), inline=False)
    if cls_info.get("restriction"):
        e.add_field(name="⚠️ Restriction", value=cls_info["restriction"], inline=False)
    e.set_footer(text=f"{char['class']} · Talent bound to this character")
    return e


def build_skills_embed(char: dict, skills: list[dict], member: discord.Member) -> discord.Embed:
    cls_info = CLASSES.get(char["class"], {})
    e = discord.Embed(
        title=f"📚 Skill Book — {member.display_name}",
        color=cls_info.get("color", C_INFO),
    )
    e.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    if not skills:
        e.description = "No skills learned yet.\nDefeat monsters and find skill books to learn new abilities!"
    else:
        for s in skills[:15]:
            rank = s.get("skill_rank", "F")
            e.add_field(
                name=f"{RANK_EMOJI.get(rank,'⬜')} {s['skill_name']}",
                value=f"**[{rank}-Rank]**",
                inline=True,
            )
    e.set_footer(text=f"Total skills: {len(skills)}")
    return e


def build_equipment_embed(char: dict, equipment: list[dict], member: discord.Member) -> discord.Embed:
    cls_info  = CLASSES.get(char["class"], {})
    equipped  = {eq["slot"]: eq for eq in equipment}
    SLOTS     = [
        ("weapon",    "⚔️ Weapon"),
        ("armor",     "🛡️ Armor"),
        ("ring",      "💍 Ring"),
        ("accessory", "🔮 Accessory"),
    ]
    e = discord.Embed(
        title=f"🎒 Equipment — {member.display_name}",
        color=cls_info.get("color", C_INFO),
    )
    e.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    for slot_key, slot_label in SLOTS:
        item = equipped.get(slot_key)
        if item:
            rank = item.get("item_rank", "F")
            effect = item.get("effect_desc", "")
            e.add_field(
                name=slot_label,
                value=(
                    f"{RANK_EMOJI.get(rank,'⬜')} **{item['item_name']}** [{rank}-Rank]\n"
                    + (f"_{effect}_" if effect else "_No special effect_")
                ),
                inline=True,
            )
        else:
            e.add_field(name=slot_label, value="_Empty_", inline=True)
    e.set_footer(text="Equipment from dungeon drops · /rpg equip <slot> <item_name>")
    return e


# ── RPG Main Panel View ───────────────────────────────────────────
class RPGPanelView(discord.ui.View):
    def __init__(self, char: dict, member: discord.Member,
                 skills: list, equipment: list, viewer_id: int):
        super().__init__(timeout=300)
        self._char      = char
        self._member    = member
        self._skills    = skills
        self._equipment = equipment
        self._viewer    = viewer_id

    @discord.ui.button(label="📋 Stats", style=discord.ButtonStyle.primary, row=0)
    async def stats_btn(self, interaction: discord.Interaction, btn):
        e = build_stats_embed(self._char, self._member)
        await interaction.response.edit_message(embed=e, view=self)

    @discord.ui.button(label="✨ Talent", style=discord.ButtonStyle.secondary, row=0)
    async def talent_btn(self, interaction: discord.Interaction, btn):
        e    = build_talent_embed(self._char, self._member)
        back = _BackView(self, build_stats_embed(self._char, self._member))
        await interaction.response.edit_message(embed=e, view=back)

    @discord.ui.button(label="📚 Skills", style=discord.ButtonStyle.secondary, row=0)
    async def skills_btn(self, interaction: discord.Interaction, btn):
        e    = build_skills_embed(self._char, self._skills, self._member)
        back = _BackView(self, build_stats_embed(self._char, self._member))
        await interaction.response.edit_message(embed=e, view=back)

    @discord.ui.button(label="🎒 Equipment", style=discord.ButtonStyle.secondary, row=0)
    async def equip_btn(self, interaction: discord.Interaction, btn):
        e    = build_equipment_embed(self._char, self._equipment, self._member)
        back = _BackView(self, build_stats_embed(self._char, self._member))
        await interaction.response.edit_message(embed=e, view=back)

    @discord.ui.button(label="📊 Allocate Points", style=discord.ButtonStyle.success, row=1)
    async def alloc_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._member.id:
            return await interaction.response.send_message("Only the owner can allocate stats.", ephemeral=True)
        if not self._char.get("stat_points"):
            return await interaction.response.send_message("No stat points available!", ephemeral=True)
        view = StatAllocView(self._char, self._member, interaction.guild_id, parent_view=self)
        await interaction.response.edit_message(embed=view._embed(), view=view)


class _BackView(discord.ui.View):
    """Generic back button that returns to parent view."""
    def __init__(self, parent: RPGPanelView, home_embed: discord.Embed):
        super().__init__(timeout=300)
        self._parent = parent
        self._home   = home_embed

    @discord.ui.button(label="◀ Back", style=discord.ButtonStyle.secondary)
    async def back_btn(self, interaction: discord.Interaction, btn):
        await interaction.response.edit_message(embed=self._home, view=self._parent)


# ── Stat Allocation View ──────────────────────────────────────────
class StatAllocView(discord.ui.View):
    def __init__(self, char: dict, member: discord.Member,
                 gid: int, parent_view: RPGPanelView):
        super().__init__(timeout=120)
        self._char    = dict(char)
        self._member  = member
        self._gid     = gid
        self._uid     = member.id
        self._parent  = parent_view
        self._pending = {"strength": 0, "defence": 0, "agility": 0, "spirit": 0}
        self._points  = char.get("stat_points", 0)
        self._update_confirm()

    def _pts_left(self) -> int:
        return self._points - sum(self._pending.values())

    def _update_confirm(self):
        self.confirm_btn.disabled = sum(self._pending.values()) == 0

    def _embed(self) -> discord.Embed:
        c    = self._char
        left = self._pts_left()
        e    = discord.Embed(
            title="📊 Allocate Stat Points",
            description=f"**Points remaining:** {left} / {self._points}\nClick +STR / +DEF / +AGI / +SPI to add points.",
            color=C_INFO,
        )
        for col_key, label in [("strength","STR"),("defence","DEF"),("agility","AGI"),("spirit","SPI")]:
            cur = c.get(col_key, 0)
            add = self._pending[col_key]
            e.add_field(
                name=label,
                value=f"{cur}" + (f" → **{cur+add}** (+{add})" if add else ""),
                inline=True,
            )
        return e

    async def _add(self, interaction: discord.Interaction, stat_key: str):
        if interaction.user.id != self._uid:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        if self._pts_left() <= 0:
            return await interaction.response.send_message("No points left!", ephemeral=True)
        self._pending[stat_key] += 1
        self._update_confirm()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="+STR", style=discord.ButtonStyle.primary, row=0)
    async def btn_str(self, i, b): await self._add(i, "strength")
    @discord.ui.button(label="+DEF", style=discord.ButtonStyle.primary, row=0)
    async def btn_def(self, i, b): await self._add(i, "defence")
    @discord.ui.button(label="+AGI", style=discord.ButtonStyle.primary, row=0)
    async def btn_agi(self, i, b): await self._add(i, "agility")
    @discord.ui.button(label="+SPI", style=discord.ButtonStyle.primary, row=0)
    async def btn_spi(self, i, b): await self._add(i, "spirit")

    @discord.ui.button(label="↺ Reset", style=discord.ButtonStyle.secondary, row=1)
    async def reset_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._uid:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        self._pending = {"strength": 0, "defence": 0, "agility": 0, "spirit": 0}
        self._update_confirm()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="◀ Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._uid:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        e = build_stats_embed(self._char, self._member)
        await interaction.response.edit_message(embed=e, view=self._parent)

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.success, disabled=True, row=1)
    async def confirm_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._uid:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        pts_used = sum(self._pending.values())
        if pts_used == 0: return
        for item in self.children: item.disabled = True
        await interaction.response.defer()
        await db.pool.execute("""
            UPDATE rpg_characters
            SET strength    = strength + $1,
                defence     = defence  + $2,
                agility     = agility  + $3,
                spirit      = spirit   + $4,
                stat_points = stat_points - $5
            WHERE guild_id=$6 AND user_id=$7
        """, self._pending["strength"], self._pending["defence"],
            self._pending["agility"], self._pending["spirit"],
            pts_used, self._gid, self._uid)
        char     = await get_char(self._gid, self._uid)
        skills   = await get_skills(self._gid, self._uid)
        equipment= await get_equipment(self._gid, self._uid)
        new_view = RPGPanelView(char, self._member, skills, equipment, self._uid)
        e = build_stats_embed(char, self._member)
        e.title = f"✅ Stats Updated! {e.title}"
        await interaction.edit_original_response(embed=e, view=new_view)
        self.stop()


# ── Class Selection Flow ──────────────────────────────────────────
class ClassSelectView(discord.ui.View):
    def __init__(self, ctx_or_inter, uid: int, gid: int):
        super().__init__(timeout=180)
        self._uid     = uid
        self._gid     = gid
        self._ctx     = ctx_or_inter
        self._page    = 0
        self._classes = list(CLASSES.items())
        self._update_label()

    def _update_label(self):
        name = self._classes[self._page][0]
        self.confirm_btn.label = f"✅ Play as {name}"

    def _embed(self) -> discord.Embed:
        name, cls = self._classes[self._page]
        base = cls["base"]
        e = discord.Embed(
            title=f"{cls['emoji']} Class: {name}",
            description=cls["desc"],
            color=cls["color"],
        )
        e.add_field(
            name="📊 Base Stats",
            value=(
                f"STR: **{base['str']}**  DEF: **{base['def']}**\n"
                f"AGI: **{base['agi']}**  SPI: **{base['spi']}**\n"
                f"HP: **{base['hp']}**  Mana: **{base['mana']}**"
            ),
            inline=True,
        )
        trank = cls["talent_rank"]
        e.add_field(
            name=f"✨ Talent: {cls['talent_name']} [{trank}]",
            value=f"🔵 {cls['passive'][:120]}",
            inline=False,
        )
        e.add_field(
            name="📚 Starting Skills",
            value="\n".join(f"{RANK_EMOJI.get(r,'⬜')} {s} [{r}-Rank]" for s, r in cls["starting_skills"]),
            inline=False,
        )
        e.set_footer(text=f"Class {self._page+1}/{len(self._classes)} · ◀ ▶ to browse · ✅ to confirm")
        return e

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, row=0)
    async def prev_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._uid:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        self._page = (self._page - 1) % len(self._classes)
        self._update_label()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="✅ Play as ...", style=discord.ButtonStyle.success, row=0)
    async def confirm_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._uid:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        cls_name = self._classes[self._page][0]
        for item in self.children: item.disabled = True
        await interaction.response.defer()
        char      = await create_char(self._gid, self._uid, cls_name)
        skills    = await get_skills(self._gid, self._uid)
        equipment = await get_equipment(self._gid, self._uid)
        cls_info  = CLASSES[cls_name]
        e = build_stats_embed(char, interaction.user)
        e.title = f"✨ Character Created — {e.title}"
        e.description = (
            f"Welcome, **{interaction.user.display_name}**!\n"
            f"You are now a **{cls_info['emoji']} {cls_name}** in the realm of **Apprentice [Lv.1]**.\n\n"
            f"You have **5 free stat points** — use `/rpg allocate` to spend them.\n"
            f"Your starting talent: **{cls_info['talent_name']}**"
        )
        view = RPGPanelView(char, interaction.user, skills, equipment, self._uid)
        await interaction.edit_original_response(embed=e, view=view)
        self.stop()

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, row=0)
    async def next_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._uid:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        self._page = (self._page + 1) % len(self._classes)
        self._update_label()
        await interaction.response.edit_message(embed=self._embed(), view=self)


# ── Cog ───────────────────────────────────────────────────────────
class RPGStatsCog(commands.Cog, name="RPG"):
    def __init__(self, bot): self.bot = bot

    @commands.hybrid_group(name="rpg", description="RPG character system", invoke_without_command=True)
    async def rpg(self, ctx):
        """Open your character sheet, or create a character if you don't have one."""
        char = await get_char(ctx.guild.id, ctx.author.id)
        if not char:
            view = ClassSelectView(ctx, ctx.author.id, ctx.guild.id)
            e = discord.Embed(
                title="⚔️ Create Your Character",
                description=(
                    "You don't have a character yet!\n\n"
                    "Browse the classes below and choose one to begin your journey.\n"
                    "Each class has unique base stats, a talent, and starting skills."
                ),
                color=C_INFO,
            )
            return await ctx.send(embed=e, view=view)
        skills    = await get_skills(ctx.guild.id, ctx.author.id)
        equipment = await get_equipment(ctx.guild.id, ctx.author.id)
        view = RPGPanelView(char, ctx.author, skills, equipment, ctx.author.id)
        await ctx.send(embed=build_stats_embed(char, ctx.author), view=view)

    @rpg.command(name="stats", description="View a character sheet")
    async def rpg_stats(self, ctx, member: discord.Member = None):
        target = member or ctx.author
        char   = await get_char(ctx.guild.id, target.id)
        if not char:
            whose = "You don't" if target == ctx.author else f"{target.display_name} doesn't"
            return await ctx.send(
                embed=discord.Embed(
                    description=f"{whose} have a character yet. Use `/rpg` to create one!",
                    color=C_WARN,
                )
            )
        skills    = await get_skills(ctx.guild.id, target.id)
        equipment = await get_equipment(ctx.guild.id, target.id)
        view = RPGPanelView(char, target, skills, equipment, ctx.author.id)
        await ctx.send(embed=build_stats_embed(char, target), view=view)

    @rpg.command(name="create", description="Create a new character (overwrites existing)")
    async def rpg_create(self, ctx):
        existing = await get_char(ctx.guild.id, ctx.author.id)
        if existing:
            class ConfirmOverwrite(discord.ui.View):
                def __init__(cv_self): super().__init__(timeout=30)
                @discord.ui.button(label="⚠️ Yes, recreate", style=discord.ButtonStyle.danger)
                async def yes(cv_self, inter, btn):
                    if inter.user.id != ctx.author.id:
                        return await inter.response.send_message("Not for you.", ephemeral=True)
                    await db.pool.execute(
                        "DELETE FROM rpg_characters WHERE guild_id=$1 AND user_id=$2",
                        ctx.guild.id, ctx.author.id
                    )
                    await db.pool.execute("DELETE FROM rpg_skills WHERE guild_id=$1 AND user_id=$2", ctx.guild.id, ctx.author.id)
                    await db.pool.execute("DELETE FROM rpg_equipment WHERE guild_id=$1 AND user_id=$2", ctx.guild.id, ctx.author.id)
                    for item in cv_self.children: item.disabled = True
                    await inter.response.edit_message(view=cv_self)
                    sel_view = ClassSelectView(ctx, ctx.author.id, ctx.guild.id)
                    await inter.followup.send(embed=sel_view._embed(), view=sel_view)
                @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
                async def no(cv_self, inter, btn):
                    for item in cv_self.children: item.disabled = True
                    await inter.response.edit_message(content="Cancelled.", view=cv_self)
            return await ctx.send(
                embed=discord.Embed(
                    title="⚠️ Recreate Character?",
                    description="This will **delete your current character** and all skills/equipment.\nAre you sure?",
                    color=C_ERROR,
                ),
                view=ConfirmOverwrite(),
            )
        view = ClassSelectView(ctx, ctx.author.id, ctx.guild.id)
        await ctx.send(embed=view._embed(), view=view)

    @rpg.command(name="talent", description="View your talent in detail")
    async def rpg_talent(self, ctx):
        char = await get_char(ctx.guild.id, ctx.author.id)
        if not char:
            return await ctx.send(embed=discord.Embed(description="No character yet — use `/rpg`.", color=C_WARN))
        e    = build_talent_embed(char, ctx.author)
        back = _BackView(
            RPGPanelView(char, ctx.author,
                         await get_skills(ctx.guild.id, ctx.author.id),
                         await get_equipment(ctx.guild.id, ctx.author.id),
                         ctx.author.id),
            build_stats_embed(char, ctx.author),
        )
        await ctx.send(embed=e, view=back)

    @rpg.command(name="skills", description="View your skill book")
    async def rpg_skills(self, ctx, member: discord.Member = None):
        target = member or ctx.author
        char   = await get_char(ctx.guild.id, target.id)
        if not char:
            return await ctx.send(embed=discord.Embed(description="No character yet.", color=C_WARN))
        skills = await get_skills(ctx.guild.id, target.id)
        e      = build_skills_embed(char, skills, target)
        await ctx.send(embed=e)

    @rpg.command(name="equip", description="View or manage equipment")
    async def rpg_equip(self, ctx, member: discord.Member = None):
        target = member or ctx.author
        char   = await get_char(ctx.guild.id, target.id)
        if not char:
            return await ctx.send(embed=discord.Embed(description="No character yet.", color=C_WARN))
        equipment = await get_equipment(ctx.guild.id, target.id)
        await ctx.send(embed=build_equipment_embed(char, equipment, target))

    @rpg.command(name="allocate", description="Spend your free stat points")
    async def rpg_allocate(self, ctx):
        char = await get_char(ctx.guild.id, ctx.author.id)
        if not char:
            return await ctx.send(embed=discord.Embed(description="No character yet — use `/rpg`.", color=C_WARN))
        if not char.get("stat_points"):
            return await ctx.send(
                embed=discord.Embed(
                    description="You have no free stat points right now.\nLevel up or complete milestones to earn more!",
                    color=C_WARN,
                )
            )
        panel = RPGPanelView(char, ctx.author,
                             await get_skills(ctx.guild.id, ctx.author.id),
                             await get_equipment(ctx.guild.id, ctx.author.id),
                             ctx.author.id)
        view = StatAllocView(char, ctx.author, ctx.guild.id, parent_view=panel)
        await ctx.send(embed=view._embed(), view=view)

    @rpg.command(name="leaderboard", description="RPG power leaderboard")
    async def rpg_lb(self, ctx):
        rows = await db.pool.fetch("""
            SELECT user_id, class, realm_level,
                   (strength + defence + agility + spirit) AS power
            FROM rpg_characters WHERE guild_id=$1
            ORDER BY power DESC LIMIT 10
        """, ctx.guild.id)
        if not rows:
            return await ctx.send(embed=discord.Embed(description="No characters yet!", color=C_INFO))
        medals = ["🥇","🥈","🥉"]
        lines  = []
        for i, r in enumerate(rows):
            m = ctx.guild.get_member(r["user_id"])
            if not m: continue
            realm, rem = get_realm(r["realm_level"])
            lines.append(
                f"{medals[i] if i<3 else f'`{i+1}`'} **{m.display_name}** — "
                f"{r['class']} · {rem} {realm} · Power: **{r['power']}**"
            )
        e = discord.Embed(
            title="⚔️ RPG Power Leaderboard",
            description="\n".join(lines) or "No data.",
            color=C_INFO,
        )
        e.set_footer(text="Power = STR + DEF + AGI + SPI")
        await ctx.send(embed=e)
