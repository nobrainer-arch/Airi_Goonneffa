# airi/rpg/shop.py — DnD 5e API RPG Shop (class-divided, filtered, paginated)
# Spells and Equipment fetched live from dnd5eapi.co
# Divided by class, filterable by cost/restriction/type
import aiohttp
import asyncio
import discord
from discord.ext import commands
import db
from utils import C_INFO, C_SUCCESS, C_WARN, C_ERROR, _err
from airi.economy import get_balance, add_coins

DND_API = "https://www.dnd5eapi.co/api"
PAGE_SZ = 8   # items per page

# ── Caches ─────────────────────────────────────────────────────────
_spell_list:  list[dict] = []
_equip_list:  list[dict] = []
_spell_detail: dict[str, dict] = {}
_equip_detail: dict[str, dict] = {}
_race_list:   list[dict] = []
_class_list:  list[dict] = []
_class_detail: dict[str, dict] = {}

# ── Class → Spell school mapping (D&D flavor for filtering) ────────
CLASS_SPELL_SCHOOLS = {
    "Mage":       {"Evocation","Abjuration","Conjuration","Divination","Enchantment","Illusion","Necromancy","Transmutation"},
    "Necromancer":{"Necromancy","Conjuration","Divination"},
    "Healer":     {"Evocation","Abjuration","Divination","Necromancy"},
    "Knight":     {"Abjuration","Evocation","Divination"},
    "Warrior":    {"Evocation","Transmutation"},
    "Archer":     {"Divination","Transmutation","Evocation"},
    "Gunman":     {"Evocation","Transmutation"},
    "Shadow":     {"Illusion","Enchantment","Divination","Necromancy"},
}

# Weapon type → class restriction
CLASS_WEAPON_TYPES = {
    "Shadow":     {"simple","weapon"},
    "Warrior":    {"martial","weapon","heavy"},
    "Knight":     {"martial","weapon","shield","armor"},
    "Archer":     {"ranged","simple","weapon"},
    "Gunman":     {"ranged","martial","weapon"},
    "Mage":       {"simple","weapon","staff","wand","rod"},
    "Necromancer":{"simple","weapon","staff","wand"},
    "Healer":     {"simple","weapon","club","mace","staff"},
}

def rank_from_level(level: int) -> str:
    return {0:"F",1:"E",2:"D",3:"C",4:"B",5:"A",6:"S",7:"SS",8:"SS",9:"SSS"}.get(min(level,9),"C")

def spell_price(level: int) -> int:
    return 300 + level * 700  # 300 for cantrips, up to 6,600 for level 9

def equip_price(category: str, weight: str = "") -> int:
    base = {"Weapon":1200,"Armor":1500,"Shield":800,"Adventuring Gear":300,
            "Tools":400,"Wondrous Items":2000,"Ring":1800,"Wand":900,"Staff":1100,
            "Rod":900,"Potion":250}
    return base.get(category, 500)


# ── DnD API fetchers ───────────────────────────────────────────────
async def _get(url: str) -> dict | None:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    return await r.json()
    except Exception as e:
        print(f"DnD API error {url}: {e}")
    return None

async def load_spell_list() -> list[dict]:
    global _spell_list
    if _spell_list: return _spell_list
    data = await _get(f"{DND_API}/spells?limit=500")
    _spell_list = data.get("results",[]) if data else []
    return _spell_list

async def load_equip_list() -> list[dict]:
    global _equip_list
    if _equip_list: return _equip_list
    data = await _get(f"{DND_API}/equipment?limit=500")
    _equip_list = data.get("results",[]) if data else []
    return _equip_list

async def get_spell_detail(index: str) -> dict | None:
    if index in _spell_detail: return _spell_detail[index]
    data = await _get(f"{DND_API}/spells/{index}")
    if data: _spell_detail[index] = data
    return data

async def get_equip_detail(index: str) -> dict | None:
    if index in _equip_detail: return _equip_detail[index]
    data = await _get(f"{DND_API}/equipment/{index}")
    if data: _equip_detail[index] = data
    return data

async def load_dnd_classes() -> list[dict]:
    global _class_list
    if _class_list: return _class_list
    data = await _get(f"{DND_API}/classes")
    _class_list = data.get("results",[]) if data else []
    return _class_list

async def get_dnd_class(index: str) -> dict | None:
    if index in _class_detail: return _class_detail[index]
    data = await _get(f"{DND_API}/classes/{index}")
    if data: _class_detail[index] = data
    return data

async def load_dnd_races() -> list[dict]:
    global _race_list
    if _race_list: return _race_list
    data = await _get(f"{DND_API}/races")
    _race_list = data.get("results",[]) if data else []
    return _race_list


# ── Spell effect mapping ── what each spell does in battle ─────────
def _spell_battle_effect(spell: dict) -> str:
    """Summarize what the spell does in RPG battle from D&D description."""
    school = spell.get("school", {}).get("name", "")
    level  = spell.get("level", 0)
    dmg    = spell.get("damage", {})
    heal   = spell.get("heal_at_slot_level", {})
    dc     = spell.get("dc", {})
    conc   = spell.get("concentration", False)
    ritual = spell.get("ritual", False)

    parts = []
    if dmg.get("damage_type", {}).get("name"):
        dtype = dmg["damage_type"]["name"]
        mult  = 1.2 + level * 0.15
        parts.append(f"⚔️ Deals ×{mult:.1f} {dtype} damage (SPI-scaled)")
    if heal:
        pct = 0.10 + level * 0.05
        parts.append(f"💚 Heals {int(pct*100)}% of max HP")
    if dc:
        dtype2 = dc.get("dc_type",{}).get("name","")
        parts.append(f"🎲 Target makes {dtype2} save or is affected")
    if conc:
        parts.append("⚡ Concentration: lasts up to 3 turns")
    if school in ("Abjuration","Shield"):
        shield_val = 20 + level * 15
        parts.append(f"🛡️ Creates a {shield_val}-HP shield")
    if school == "Necromancy" and level > 0:
        parts.append("☠️ Applies Venom or Wither effect")
    if school == "Illusion":
        parts.append("👁️ Enemy loses 1 turn (stunned)")
    if school == "Conjuration":
        parts.append("👾 Summons a minion for 2 turns")
    if school == "Enchantment":
        parts.append("🧠 Reduces enemy STR by 20% for 2 turns")
    if not parts:
        mult = 1.0 + level * 0.1
        parts.append(f"✨ Deals ×{mult:.1f} magic damage")
    return "  ·  ".join(parts[:2])


# ── Spell filter logic ─────────────────────────────────────────────
def _filter_spells(spells_with_detail: list[dict], player_class: str | None,
                   filter_mode: str) -> list[dict]:
    """filter_mode: 'all', 'class', 'cheap', 'expensive', 'no_restrict'"""
    out = []
    for sp in spells_with_detail:
        level     = sp.get("level", 0)
        school    = sp.get("school",{}).get("name","")
        classes   = [c.get("name","") for c in sp.get("classes",[])]
        price     = spell_price(level)

        if filter_mode == "cheap"     and price > 2000: continue
        if filter_mode == "expensive" and price < 3000: continue
        if filter_mode == "no_restrict":
            # Spells available to 3+ classes or no class restriction listed
            if classes and len(classes) < 3: continue
        if filter_mode == "class" and player_class:
            allowed_schools = CLASS_SPELL_SCHOOLS.get(player_class, set())
            if school and school not in allowed_schools:
                if not any(c.lower() == player_class.lower() for c in classes):
                    continue
        out.append(sp)
    return out


# ── Shop Embeds ────────────────────────────────────────────────────
def _spell_embed(spells: list[dict], page: int, total_pages: int,
                 filter_label: str, player_class: str | None) -> discord.Embed:
    e = discord.Embed(
        title=f"📚 Spell Shop — {filter_label}",
        description=(
            f"**Costs 🪙 coins · Page {page+1}/{total_pages}**\n"
            f"Spells power up in battle using **SPI** stat. Mana cost shown.\n"
            + (f"Showing spells for **{player_class}** class\n" if player_class else "")
            + "\u200b"
        ),
        color=0x3498db,
    )
    start = page * PAGE_SZ
    for sp in spells[start:start+PAGE_SZ]:
        level   = sp.get("level", 0)
        school  = sp.get("school",{}).get("name","?")
        classes = ", ".join(c.get("name","") for c in sp.get("classes",[]))[:40]
        price   = spell_price(level)
        rank    = rank_from_level(level)
        mana    = 10 + level * 5
        desc    = sp.get("desc",[""])[0][:80] if sp.get("desc") else ""
        effect  = _spell_battle_effect(sp)

        e.add_field(
            name=f"[{rank}] {sp.get('name','?')} — {price:,} 🪙",
            value=(
                f"*{school} · Lv.{level} · {mana} mana*\n"
                f"{effect}\n"
                + (f"*For: {classes}*" if classes else "")
            ),
            inline=False,
        )
    e.set_footer(text="Use the dropdown to buy a spell · ◀▶ to browse · Filter to narrow results")
    return e

def _equip_embed(items: list[dict], page: int, total_pages: int, filter_label: str) -> discord.Embed:
    e = discord.Embed(
        title=f"⚔️ Equipment Shop — {filter_label}",
        description=f"**Costs 🪙 coins · Page {page+1}/{total_pages}**\nEquipment boosts stats permanently when equipped.\n\u200b",
        color=0xe74c3c,
    )
    start = page * PAGE_SZ
    for it in items[start:start+PAGE_SZ]:
        cat   = it.get("equipment_category",{}).get("name","?")
        price = it.get("_price", equip_price(cat))
        cost  = it.get("cost",{})
        wt    = it.get("weight",0)
        props = [p.get("name","") for p in it.get("properties",[])]
        prop_txt = ", ".join(props[:3]) if props else ""

        # Stat effects
        ac  = it.get("armor_class",{})
        dmg = it.get("damage",{})
        effects = []
        if ac: effects.append(f"+{ac.get('base',0)} DEF")
        if dmg: effects.append(f"+DMG ({dmg.get('damage_dice','?')})")
        if wt: effects.append(f"{wt} lbs")
        eff_txt = "  ·  ".join(effects[:2]) or "General use"

        rank = "F" if price < 500 else ("E" if price < 1000 else ("D" if price < 2000 else "C"))

        e.add_field(
            name=f"[{rank}] {it.get('name','?')} — {price:,} 🪙",
            value=(
                f"*{cat}*  ·  {eff_txt}\n"
                + (f"*{prop_txt}*" if prop_txt else "")
            ),
            inline=True,
        )
    e.set_footer(text="Use the dropdown to buy · ◀▶ to browse")
    return e


# ── Main Shop View ─────────────────────────────────────────────────
class RPGShopView(discord.ui.View):
    """Tabbed shop: Spells | Weapons | Armor | Accessories.
       Filter bar: All | Class-Only | Cheap | Expensive | No Restrict"""

    def __init__(self, ctx, player_class: str | None):
        super().__init__(timeout=300)
        self._ctx         = ctx
        self._class       = player_class
        self._tab         = "spells"    # spells | weapons | armor | accessories
        self._filter      = "all"       # all | class | cheap | expensive | no_restrict
        self._page        = 0
        self._spell_cache : list[dict] = []
        self._equip_cache : list[dict] = []
        self._loaded      = False

    # ── Data loading ───────────────────────────────────────────────
    async def _load(self):
        if self._loaded: return
        spell_list = await load_spell_list()
        equip_list = await load_equip_list()

        # Load detail for first 80 spells (parallel)
        tasks = [get_spell_detail(sp["index"]) for sp in spell_list[:80]]
        details = await asyncio.gather(*tasks, return_exceptions=True)
        self._spell_cache = [d for d in details if isinstance(d, dict)]

        # Assign prices to equipment
        for it in equip_list[:120]:
            d = await get_equip_detail(it["index"])
            if d:
                cat   = d.get("equipment_category",{}).get("name","Gear")
                d["_price"] = equip_price(cat)
                self._equip_cache.append(d)

        self._loaded = True

    def _filtered_spells(self) -> list[dict]:
        return _filter_spells(self._spell_cache, self._class, self._filter)

    def _filtered_equip(self, category: str) -> list[dict]:
        cats = {"weapons":["Weapon"],"armor":["Armor","Shield"],"accessories":["Wondrous Items","Ring","Wand","Staff","Rod","Potion"]}
        target = cats.get(category, [])
        return [it for it in self._equip_cache
                if any(t.lower() in it.get("equipment_category",{}).get("name","").lower() for t in target)]

    # ── Embed ──────────────────────────────────────────────────────
    def _build_embed(self) -> discord.Embed:
        filter_labels = {"all":"All","class":f"{self._class} Only","cheap":"Cheap (<2k)","expensive":"Premium (3k+)","no_restrict":"No Class Restrict"}
        fl = filter_labels.get(self._filter,"All")

        if self._tab == "spells":
            items = self._filtered_spells()
            total_pages = max(1,(len(items)+PAGE_SZ-1)//PAGE_SZ)
            self._page = min(self._page, total_pages-1)
            return _spell_embed(items, self._page, total_pages, fl, self._class)
        else:
            items = self._filtered_equip(self._tab)
            total_pages = max(1,(len(items)+PAGE_SZ-1)//PAGE_SZ)
            self._page = min(self._page, total_pages-1)
            return _equip_embed(items, self._page, total_pages, fl)

    # ── Build controls ─────────────────────────────────────────────
    def _rebuild_controls(self):
        self.clear_items()

        # ── Row 0: Tab buttons ─────────────────────────────────────
        for label, value, emoji in [
            ("Spells","spells","📚"),
            ("Weapons","weapons","⚔️"),
            ("Armor","armor","🛡️"),
            ("Accessories","accessories","💍"),
        ]:
            btn = discord.ui.Button(
                label=label, emoji=emoji,
                style=discord.ButtonStyle.primary if self._tab==value else discord.ButtonStyle.secondary,
                row=0,
            )
            tab_val = value
            async def tab_cb(inter, tv=tab_val):
                if inter.user.id != self._ctx.author.id: return await inter.response.send_message("Not for you.",ephemeral=True)
                self._tab = tv; self._page = 0; self._rebuild_controls()
                await inter.response.edit_message(embed=self._build_embed(), view=self)
            btn.callback = tab_cb
            self.add_item(btn)

        # ── Row 1: Filter select ───────────────────────────────────
        filter_opts = [
            discord.SelectOption(label="All Items",           value="all",         emoji="🌐"),
            discord.SelectOption(label="Class-Specific",      value="class",       emoji="🎭"),
            discord.SelectOption(label="Cheap (< 2,000 🪙)", value="cheap",       emoji="💸"),
            discord.SelectOption(label="Premium (3,000+ 🪙)", value="expensive",  emoji="💎"),
            discord.SelectOption(label="No Class Restriction",value="no_restrict", emoji="🔓"),
        ]
        # Set default
        for o in filter_opts:
            o.default = (o.value == self._filter)
        filter_sel = discord.ui.Select(
            placeholder="🔍 Filter items…",
            options=filter_opts,
            row=1,
        )
        async def filter_cb(inter):
            if inter.user.id != self._ctx.author.id: return await inter.response.send_message("Not for you.",ephemeral=True)
            self._filter = filter_sel.values[0]; self._page = 0; self._rebuild_controls()
            await inter.response.edit_message(embed=self._build_embed(), view=self)
        filter_sel.callback = filter_cb
        self.add_item(filter_sel)

        # ── Row 2: Page navigation ─────────────────────────────────
        if self._tab == "spells":
            items     = self._filtered_spells()
            page_data = items
        else:
            page_data = self._filtered_equip(self._tab)

        total_pages = max(1,(len(page_data)+PAGE_SZ-1)//PAGE_SZ)

        prev_btn = discord.ui.Button(label="◀ Prev", style=discord.ButtonStyle.secondary, row=2,
                                     disabled=self._page==0)
        async def prev_cb(inter):
            if inter.user.id != self._ctx.author.id: return await inter.response.send_message("Not for you.",ephemeral=True)
            self._page -= 1; self._rebuild_controls()
            await inter.response.edit_message(embed=self._build_embed(), view=self)
        prev_btn.callback = prev_cb
        self.add_item(prev_btn)

        page_lbl = discord.ui.Button(label=f"Page {self._page+1}/{total_pages}",
                                     style=discord.ButtonStyle.secondary, disabled=True, row=2)
        self.add_item(page_lbl)

        next_btn = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary, row=2,
                                     disabled=self._page>=total_pages-1)
        async def next_cb(inter):
            if inter.user.id != self._ctx.author.id: return await inter.response.send_message("Not for you.",ephemeral=True)
            self._page += 1; self._rebuild_controls()
            await inter.response.edit_message(embed=self._build_embed(), view=self)
        next_btn.callback = next_cb
        self.add_item(next_btn)

        # ── Row 3: Buy dropdown ────────────────────────────────────
        start = self._page * PAGE_SZ
        if self._tab == "spells":
            visible = self._filtered_spells()[start:start+PAGE_SZ]
            buy_opts = [
                discord.SelectOption(
                    label=f"{sp.get('name','?')[:50]} — {spell_price(sp.get('level',0)):,} 🪙",
                    value=sp.get("index",""),
                    description=f"[{rank_from_level(sp.get('level',0))}] {sp.get('school',{}).get('name','?')} spell",
                )
                for sp in visible[:25] if sp.get("index")
            ]
        else:
            visible = self._filtered_equip(self._tab)[start:start+PAGE_SZ]
            buy_opts = [
                discord.SelectOption(
                    label=f"{it.get('name','?')[:50]} — {it.get('_price',500):,} 🪙",
                    value=it.get("index",""),
                    description=it.get("equipment_category",{}).get("name","?")[:50],
                )
                for it in visible[:25] if it.get("index")
            ]

        if buy_opts:
            buy_sel = discord.ui.Select(
                placeholder="🛒 Buy an item…",
                options=buy_opts,
                row=3,
            )
            tab_snap = self._tab
            async def buy_cb(inter):
                if inter.user.id != self._ctx.author.id: return await inter.response.send_message("Not for you.",ephemeral=True)
                await inter.response.defer(ephemeral=True)
                idx = buy_sel.values[0]
                if tab_snap == "spells":
                    await _buy_spell(inter, idx, self._ctx.guild.id, inter.user.id)
                else:
                    await _buy_equipment(inter, idx, self._ctx.guild.id, inter.user.id)
            buy_sel.callback = buy_cb
            self.add_item(buy_sel)


# ── Buy logic ──────────────────────────────────────────────────────
async def _buy_spell(inter: discord.Interaction, spell_index: str, gid: int, uid: int):
    detail = await get_spell_detail(spell_index)
    if not detail:
        return await inter.followup.send("❌ Couldn't fetch spell details.", ephemeral=True)

    name  = detail.get("name","?")
    level = detail.get("level", 0)
    price = spell_price(level)
    rank  = rank_from_level(level)
    mana  = 10 + level * 5
    school= detail.get("school",{}).get("name","?")

    # Check already learned
    exists = await db.pool.fetchval(
        "SELECT 1 FROM rpg_skills WHERE guild_id=$1 AND user_id=$2 AND skill_name=$3",
        gid, uid, name
    )
    if exists:
        return await inter.followup.send(f"📚 You already know **{name}**!", ephemeral=True)

    bal = await get_balance(gid, uid)
    if bal < price:
        return await inter.followup.send(f"❌ Need **{price:,}** 🪙 but have **{bal:,}**.", ephemeral=True)

    await add_coins(gid, uid, -price)
    await db.pool.execute("""
        INSERT INTO rpg_skills (guild_id, user_id, skill_name, skill_rank, mana_cost)
        VALUES ($1,$2,$3,$4,$5)
        ON CONFLICT (guild_id, user_id, skill_name) DO NOTHING
    """, gid, uid, name, rank, mana)

    effect = _spell_battle_effect(detail)
    e = discord.Embed(
        title=f"📚 Learned: {name}!",
        description=(
            f"**School:** {school}  ·  **Level:** {level}  ·  **Rank:** [{rank}]\n"
            f"**Mana cost:** {mana}\n"
            f"**Battle effect:** {effect}\n\n"
            f"*-{price:,} 🪙 deducted from your wallet.*"
        ),
        color=C_SUCCESS,
    )
    await inter.followup.send(embed=e, ephemeral=True)


async def _buy_equipment(inter: discord.Interaction, equip_index: str, gid: int, uid: int):
    detail = await get_equip_detail(equip_index)
    if not detail:
        return await inter.followup.send("❌ Couldn't fetch item details.", ephemeral=True)

    name  = detail.get("name","?")
    cat   = detail.get("equipment_category",{}).get("name","Gear")
    price = equip_price(cat)
    rank  = "F" if price < 500 else ("E" if price < 1000 else ("D" if price < 2000 else "C"))

    # Determine slot
    slot_map = {"Weapon":"weapon","Shield":"armor","Armor":"armor",
                "Ring":"accessory","Wand":"accessory","Staff":"accessory",
                "Rod":"accessory","Potion":"accessory"}
    slot = slot_map.get(cat, "accessory")

    # Stat effects
    ac  = detail.get("armor_class",{})
    dmg = detail.get("damage",{})
    effect_parts = []
    if ac.get("base"):   effect_parts.append(f"+{ac['base']} CON/DEF")
    if dmg.get("damage_dice"): effect_parts.append(f"+STR ({dmg['damage_dice']})")
    eff_desc = "  ·  ".join(effect_parts) or "General equipment"

    bal = await get_balance(gid, uid)
    if bal < price:
        return await inter.followup.send(f"❌ Need **{price:,}** 🪙 but have **{bal:,}**.", ephemeral=True)

    await add_coins(gid, uid, -price)
    await db.pool.execute("""
        INSERT INTO rpg_equipment (guild_id, user_id, slot, item_name, item_rank, effect_desc)
        VALUES ($1,$2,$3,$4,$5,$6)
        ON CONFLICT (guild_id, user_id, slot)
        DO UPDATE SET item_name=$4, item_rank=$5, effect_desc=$6
    """, gid, uid, slot, name, rank, eff_desc)

    # Apply stat boosts from AC/damage
    if ac.get("base"):
        await db.pool.execute("""
            UPDATE rpg_characters SET constitution=constitution+$1
            WHERE guild_id=$2 AND user_id=$3
        """, max(1, ac["base"]//2), gid, uid)

    e = discord.Embed(
        title=f"⚔️ Equipped: {name}!",
        description=(
            f"**Category:** {cat}  ·  **Slot:** {slot}  ·  **Rank:** [{rank}]\n"
            f"**Effect:** {eff_desc}\n\n"
            f"*-{price:,} 🪙 deducted. Item equipped immediately.*"
        ),
        color=C_SUCCESS,
    )
    await inter.followup.send(embed=e, ephemeral=True)


# ── Cog ────────────────────────────────────────────────────────────
class RPGShopCog(commands.Cog, name="RPGShop"):
    def __init__(self, bot): self.bot = bot

    @commands.hybrid_command(name="rpgshop", aliases=["spellshop","equipshop"],
                             description="Browse the RPG shop — spells, weapons, armor, accessories")
    async def rpgshop(self, ctx):
        """Open the DnD-powered RPG shop with class filtering."""
        from airi.rpg.stats import get_char
        char = await get_char(ctx.guild.id, ctx.author.id)
        player_class = char["class"] if char else None

        await ctx.defer()
        view = RPGShopView(ctx, player_class)

        # Loading message
        e = discord.Embed(
            title="🔄 Loading Shop…",
            description="Fetching items from D&D 5e API…",
            color=C_INFO,
        )
        msg = await ctx.send(embed=e, view=view)

        # Load data async, then update
        await view._load()
        view._rebuild_controls()
        await msg.edit(embed=view._build_embed(), view=view)

    @commands.hybrid_command(name="myspells", description="View your learned spells")
    async def myspells(self, ctx, member: discord.Member = None):
        target = member or ctx.author
        skills = await db.pool.fetch(
            "SELECT skill_name, skill_rank, mana_cost FROM rpg_skills WHERE guild_id=$1 AND user_id=$2",
            ctx.guild.id, target.id
        )
        if not skills:
            return await ctx.send(embed=discord.Embed(
                description="No spells learned yet. Visit `/rpgshop`!", color=C_WARN))
        e = discord.Embed(
            title=f"📚 {target.display_name}'s Spellbook",
            color=0x3498db,
        )
        for sk in skills:
            e.add_field(
                name=f"[{sk['skill_rank']}] {sk['skill_name']}",
                value=f"Mana: {sk['mana_cost']}",
                inline=True,
            )
        await ctx.send(embed=e)
