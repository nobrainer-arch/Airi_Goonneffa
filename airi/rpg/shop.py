# airi/rpg/shop.py — Unified /shop command
# Tabs: Spells | Weapons | Armor | Accessories | Potions | Travel (Market)
# Spells & Equipment from DnD 5e API + Market consumables all in one place.
# Travel tab shows the distance-gated items (with travel cost in coins, no real delay).
import aiohttp, asyncio, discord
from discord.ext import commands
import db
from utils import C_INFO, C_SUCCESS, C_WARN, _err
from airi.economy import get_balance, add_coins

DND_API = "https://www.dnd5eapi.co/api"
PAGE_SZ = 7

# ── Caches ──────────────────────────────────────────────────────────
_spell_list:   list[dict] = []
_equip_list:   list[dict] = []
_spell_detail: dict[str,dict] = {}
_equip_detail: dict[str,dict] = {}

# ── Class → spell school mapping ────────────────────────────────────
CLASS_SCHOOLS = {
    "Shadow":     {"Illusion","Enchantment","Necromancy","Divination"},
    "Necromancer":{"Necromancy","Conjuration","Divination"},
    "Healer":     {"Evocation","Abjuration","Divination","Necromancy"},
    "Knight":     {"Abjuration","Evocation","Divination"},
    "Warrior":    {"Evocation","Transmutation"},
    "Archer":     {"Divination","Transmutation","Evocation"},
    "Gunman":     {"Evocation","Transmutation"},
    "Mage":       {"Evocation","Abjuration","Conjuration","Divination","Enchantment","Illusion","Necromancy","Transmutation"},
}

RANK_FROM_LEVEL = {0:"F",1:"E",2:"D",3:"C",4:"B",5:"A",6:"S",7:"SS",8:"SS",9:"SSS"}
RANK_EMOJI = {"F":"⬜","E":"🟩","D":"🟦","C":"🔵","B":"🟣","A":"🟠","S":"🔴","SS":"🌟","SSS":"💫"}

def spell_price(lvl):  return 300 + lvl * 700
def equip_price(cat):
    return {"Weapon":1200,"Armor":1500,"Shield":800,"Adventuring Gear":300,
            "Tools":400,"Wondrous Items":2000,"Ring":1800,"Wand":900,"Staff":1100,
            "Rod":900,"Potion":250}.get(cat, 500)

# ── Market consumables (buyable in shop + travel tab) ───────────────
MARKET_ITEMS = {
    "hp_potion_s": {"name":"Small HP Potion",   "price":200,  "rank":"F","type":"consumable","effect":"Restore 20% HP in combat"},
    "hp_potion_m": {"name":"Medium HP Potion",  "price":500,  "rank":"E","type":"consumable","effect":"Restore 40% HP in combat"},
    "hp_potion_l": {"name":"Large HP Potion",   "price":1200, "rank":"D","type":"consumable","effect":"Restore 70% HP in combat"},
    "mana_potion": {"name":"Mana Potion",        "price":400,  "rank":"E","type":"consumable","effect":"Restore 30% Mana in combat"},
    "antidote":    {"name":"Antidote",           "price":300,  "rank":"F","type":"consumable","effect":"Cure Venom/Burn in combat"},
    "elixir":      {"name":"Elixir of Strength", "price":2000, "rank":"C","type":"consumable","effect":"+20 STR for 1 dungeon run"},
    "revival_orb": {"name":"Revival Orb",        "price":5000, "rank":"B","type":"consumable","effect":"Survive 1 lethal hit automatically"},
    "luck_charm":  {"name":"Lucky Charm",        "price":1500, "rank":"C","type":"accessory", "effect":"+5% loot luck (equipped)","travel_only":True},
    "speed_boots": {"name":"Boots of Swiftness", "price":3000, "rank":"B","type":"equipment", "effect":"+15 AGI permanently","travel_only":True},
    "mage_robe":   {"name":"Arcane Robe",        "price":2500, "rank":"C","type":"equipment", "effect":"+20 SPI permanently","travel_only":True},
    "shadow_cloak":{"name":"Shadow Cloak",       "price":3500, "rank":"B","type":"equipment", "effect":"+15 AGI +5% crit permanently","travel_only":True},
    "iron_shield": {"name":"Iron Shield",        "price":1800, "rank":"D","type":"equipment", "effect":"+12 CON permanently","travel_only":True},
}

# ── DnD API helpers ─────────────────────────────────────────────────
async def _get(url):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200: return await r.json()
    except Exception as e: print(f"DnD API: {e}")
    return None

async def load_spells() -> list[dict]:
    global _spell_list
    if _spell_list: return _spell_list
    d = await _get(f"{DND_API}/spells?limit=500")
    _spell_list = d.get("results",[]) if d else []
    return _spell_list

async def load_equip() -> list[dict]:
    global _equip_list
    if _equip_list: return _equip_list
    d = await _get(f"{DND_API}/equipment?limit=500")
    _equip_list = d.get("results",[]) if d else []
    return _equip_list

async def get_spell(idx) -> dict|None:
    if idx in _spell_detail: return _spell_detail[idx]
    d = await _get(f"{DND_API}/spells/{idx}")
    if d: _spell_detail[idx] = d
    return d

async def get_equip(idx) -> dict|None:
    if idx in _equip_detail: return _equip_detail[idx]
    d = await _get(f"{DND_API}/equipment/{idx}")
    if d: _equip_detail[idx] = d
    return d

# ── Spell battle effect summary ─────────────────────────────────────
def spell_effect(sp: dict) -> str:
    school = sp.get("school",{}).get("name","")
    lvl    = sp.get("level",0)
    dmg    = sp.get("damage",{})
    heal   = sp.get("heal_at_slot_level",{})
    conc   = sp.get("concentration",False)

    parts = []
    if dmg.get("damage_type",{}).get("name"):
        parts.append(f"⚔️ ×{1.2+lvl*0.15:.1f} {dmg['damage_type']['name']} dmg (SPI-scaled)")
    if heal:
        parts.append(f"💚 Heals {10+lvl*5}% max HP")
    if school == "Abjuration":
        parts.append(f"🛡️ +{20+lvl*15} shield HP")
    if school == "Illusion":
        parts.append("👁️ Enemy loses 1 turn (stun)")
    if school == "Necromancy" and lvl > 0:
        parts.append("☠️ Applies Venom effect")
    if school == "Enchantment":
        parts.append("🧠 Enemy STR -20% for 2T")
    if conc:
        parts.append("⚡ Concentration (lasts 3T)")
    if not parts:
        parts.append(f"✨ ×{1.0+lvl*0.1:.1f} magic damage")
    return "  ·  ".join(parts[:2])

# ── Filtering ───────────────────────────────────────────────────────
def filter_spells(spells, player_class, fmode):
    out = []
    for sp in spells:
        lvl    = sp.get("level",0)
        school = sp.get("school",{}).get("name","")
        classes= [c.get("name","") for c in sp.get("classes",[])]
        price  = spell_price(lvl)
        if fmode == "cheap"      and price > 2000: continue
        if fmode == "expensive"  and price < 3000: continue
        if fmode == "no_restrict" and classes and len(classes) < 3: continue
        if fmode == "class" and player_class:
            allowed = CLASS_SCHOOLS.get(player_class, set())
            if school not in allowed and not any(c.lower()==player_class.lower() for c in classes): continue
        out.append(sp)
    return out

# ── Embeds ──────────────────────────────────────────────────────────
def _spell_embed(items, page, total_pages, flabel, player_class):
    e = discord.Embed(
        title=f"📚 Spell Shop — {flabel}",
        description=(f"Spells power up in battle via **SPI** stat.\n"
                     +(f"Showing: **{player_class}** affinity\n" if player_class else "")
                     +f"Page **{page+1}/{total_pages}**\n\u200b"),
        color=0x3498db,
    )
    start = page * PAGE_SZ
    for sp in items[start:start+PAGE_SZ]:
        lvl    = sp.get("level",0)
        school = sp.get("school",{}).get("name","?")
        price  = spell_price(lvl)
        rank   = RANK_FROM_LEVEL.get(min(lvl,9),"F")
        mana   = 10 + lvl*5
        eff    = spell_effect(sp)
        cls    = ", ".join(c.get("name","") for c in sp.get("classes",[]))[:40]
        e.add_field(
            name=f"{RANK_EMOJI.get(rank,'⬜')} [{rank}] {sp.get('name','?')} — {price:,} 🪙",
            value=f"*{school} · Lv{lvl} · {mana} mana*\n{eff}"+(f"\n*For: {cls}*" if cls else ""),
            inline=False,
        )
    e.set_footer(text="Buy via dropdown · ◀▶ browse · filter to narrow")
    return e

def _equip_embed(items, page, total_pages, tab_name):
    e = discord.Embed(title=f"⚔️ {tab_name} Shop", color=0xe74c3c,
                      description=f"Equipment boosts stats permanently.\nPage **{page+1}/{total_pages}**\n\u200b")
    start = page * PAGE_SZ
    for it in items[start:start+PAGE_SZ]:
        cat   = it.get("equipment_category",{}).get("name","?")
        price = it.get("_price", equip_price(cat))
        ac    = it.get("armor_class",{})
        dmg   = it.get("damage",{})
        eff   = []
        if ac.get("base"): eff.append(f"+{ac['base']} DEF")
        if dmg.get("damage_dice"): eff.append(f"+DMG ({dmg['damage_dice']})")
        rank  = "F" if price<500 else ("E" if price<1000 else ("D" if price<2000 else "C"))
        e.add_field(
            name=f"{RANK_EMOJI.get(rank,'⬜')} [{rank}] {it.get('name','?')} — {price:,} 🪙",
            value=f"*{cat}* · {'  '.join(eff) or 'General use'}",
            inline=True,
        )
    e.set_footer(text="Buy via dropdown · ◀▶ browse")
    return e

def _potions_embed(page, total_pages):
    items = [(k,v) for k,v in MARKET_ITEMS.items() if v["type"]=="consumable"]
    e = discord.Embed(title="🧪 Potions & Consumables", color=0x2ecc71,
                      description=f"Usable in dungeon combat from the Skill menu.\nPage **{page+1}/{total_pages}**\n\u200b")
    start = page * PAGE_SZ
    for key, it in items[start:start+PAGE_SZ]:
        e.add_field(
            name=f"{RANK_EMOJI.get(it['rank'],'⬜')} [{it['rank']}] {it['name']} — {it['price']:,} 🪙",
            value=it["effect"], inline=False,
        )
    e.set_footer(text="Buy via dropdown")
    return e

def _travel_embed(page, total_pages):
    items = [(k,v) for k,v in MARKET_ITEMS.items() if v.get("travel_only")]
    e = discord.Embed(title="🏪 Market District — Rare Equipment", color=0xf39c12,
                      description=(
                          "These items are only available from the **Market District**.\n"
                          "A small **travel fee** (50 🪙) applies per purchase.\n"
                          f"Page **{page+1}/{total_pages}**\n\u200b"
                      ))
    start = page * PAGE_SZ
    for key, it in items[start:start+PAGE_SZ]:
        e.add_field(
            name=f"{RANK_EMOJI.get(it['rank'],'⬜')} [{it['rank']}] {it['name']} — {it['price']:,} 🪙 (+50 travel)",
            value=it["effect"], inline=False,
        )
    e.set_footer(text="Buy via dropdown · Travel fee deducted automatically")
    return e


# ── Main Shop View ───────────────────────────────────────────────────
class ShopView(discord.ui.View):
    def __init__(self, ctx, player_class):
        super().__init__(timeout=300)
        self._ctx         = ctx
        self._class       = player_class
        self._tab         = "spells"
        self._filter      = "all"
        self._page        = 0
        self._spell_cache : list[dict] = []
        self._equip_cache : list[dict] = []
        self._loaded      = False

    async def _load(self):
        if self._loaded: return
        spell_list = await load_spells()
        equip_list = await load_equip()
        tasks = [get_spell(sp["index"]) for sp in spell_list[:80]]
        details = await asyncio.gather(*tasks, return_exceptions=True)
        self._spell_cache = [d for d in details if isinstance(d, dict)]
        for it in equip_list[:120]:
            d = await get_equip(it["index"])
            if d:
                cat = d.get("equipment_category",{}).get("name","Gear")
                d["_price"] = equip_price(cat)
                self._equip_cache.append(d)
        self._loaded = True

    def _filtered_spells(self):
        return filter_spells(self._spell_cache, self._class, self._filter)

    def _filtered_equip(self, tab):
        cats = {
            "weapons":     ["Weapon"],
            "armor":       ["Armor","Shield"],
            "accessories": ["Wondrous Items","Ring","Wand","Staff","Rod"],
        }
        target = cats.get(tab, [])
        return [it for it in self._equip_cache
                if any(t.lower() in it.get("equipment_category",{}).get("name","").lower() for t in target)]

    def _current_items(self):
        if self._tab == "spells":    return self._filtered_spells()
        if self._tab == "potions":   return [(k,v) for k,v in MARKET_ITEMS.items() if v["type"]=="consumable"]
        if self._tab == "travel":    return [(k,v) for k,v in MARKET_ITEMS.items() if v.get("travel_only")]
        return self._filtered_equip(self._tab)

    def _total_pages(self):
        return max(1, (len(self._current_items()) + PAGE_SZ - 1) // PAGE_SZ)

    def _embed(self):
        fl_labels = {"all":"All","class":f"{self._class or 'Class'} Only","cheap":"Cheap (<2k)",
                     "expensive":"Premium (3k+)","no_restrict":"No Restriction"}
        fl = fl_labels.get(self._filter, "All")
        tp = self._total_pages()
        pg = min(self._page, tp-1)
        if self._tab == "spells":    return _spell_embed(self._filtered_spells(), pg, tp, fl, self._class)
        if self._tab == "potions":   return _potions_embed(pg, tp)
        if self._tab == "travel":    return _travel_embed(pg, tp)
        tab_labels = {"weapons":"⚔️ Weapons","armor":"🛡️ Armor","accessories":"💍 Accessories"}
        return _equip_embed(self._filtered_equip(self._tab), pg, tp, tab_labels.get(self._tab,"Equipment"))

    def _rebuild(self):
        self.clear_items()
        tabs = [("📚 Spells","spells"),("⚔️ Weapons","weapons"),("🛡️ Armor","armor"),
                ("💍 Accessories","accessories"),("🧪 Potions","potions"),("🏪 Market","travel")]

        # Row 0: tab buttons (first 4)
        for label, val in tabs[:4]:
            btn = discord.ui.Button(
                label=label, row=0,
                style=discord.ButtonStyle.primary if self._tab==val else discord.ButtonStyle.secondary,
            )
            v2 = val
            async def tab_cb(inter, tv=v2):
                if inter.user.id != self._ctx.author.id:
                    return await inter.response.send_message("Not for you.", ephemeral=True)
                self._tab=tv; self._page=0; self._rebuild()
                await inter.response.edit_message(embed=self._embed(), view=self)
            btn.callback = tab_cb
            self.add_item(btn)

        # Row 1: last 2 tabs + filter select
        for label, val in tabs[4:]:
            btn = discord.ui.Button(
                label=label, row=1,
                style=discord.ButtonStyle.primary if self._tab==val else discord.ButtonStyle.secondary,
            )
            v2 = val
            async def tab_cb2(inter, tv=v2):
                if inter.user.id != self._ctx.author.id:
                    return await inter.response.send_message("Not for you.", ephemeral=True)
                self._tab=tv; self._page=0; self._rebuild()
                await inter.response.edit_message(embed=self._embed(), view=self)
            btn.callback = tab_cb2
            self.add_item(btn)

        # Filter select (only for spells/equip, hidden for potions/travel)
        if self._tab not in ("potions","travel"):
            fopts = [
                discord.SelectOption(label="All Items",            value="all",         default=self._filter=="all"),
                discord.SelectOption(label="Class-Specific",       value="class",       default=self._filter=="class"),
                discord.SelectOption(label="Cheap (< 2,000 🪙)",  value="cheap",       default=self._filter=="cheap"),
                discord.SelectOption(label="Premium (3,000+ 🪙)", value="expensive",   default=self._filter=="expensive"),
                discord.SelectOption(label="No Class Restriction", value="no_restrict", default=self._filter=="no_restrict"),
            ]
            fsel = discord.ui.Select(placeholder="🔍 Filter…", options=fopts, row=1)
            async def filter_cb(inter):
                if inter.user.id != self._ctx.author.id:
                    return await inter.response.send_message("Not for you.", ephemeral=True)
                self._filter=fsel.values[0]; self._page=0; self._rebuild()
                await inter.response.edit_message(embed=self._embed(), view=self)
            fsel.callback = filter_cb
            # Only add if we have room (row 1 has 2 tab buttons already — 3 items max per row)
            # Move to row 2
            fsel.row = 2
            self.add_item(fsel)

        # Row 3: page navigation
        tp = self._total_pages()
        prev = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, row=3, disabled=self._page==0)
        page_lbl = discord.ui.Button(label=f"{min(self._page,tp-1)+1}/{tp}", style=discord.ButtonStyle.secondary, disabled=True, row=3)
        nxt  = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary, row=3, disabled=self._page>=tp-1)
        async def prev_cb(inter):
            if inter.user.id != self._ctx.author.id: return await inter.response.send_message("Not for you.", ephemeral=True)
            self._page-=1; self._rebuild()
            await inter.response.edit_message(embed=self._embed(), view=self)
        async def next_cb(inter):
            if inter.user.id != self._ctx.author.id: return await inter.response.send_message("Not for you.", ephemeral=True)
            self._page+=1; self._rebuild()
            await inter.response.edit_message(embed=self._embed(), view=self)
        prev.callback = prev_cb; nxt.callback = next_cb
        self.add_item(prev); self.add_item(page_lbl); self.add_item(nxt)

        # Row 4: buy dropdown
        start = min(self._page, tp-1) * PAGE_SZ
        buy_opts = []
        if self._tab == "spells":
            for sp in self._filtered_spells()[start:start+PAGE_SZ][:25]:
                if not sp.get("index"): continue
                lvl = sp.get("level",0)
                rank = RANK_FROM_LEVEL.get(min(lvl,9),"F")
                buy_opts.append(discord.SelectOption(
                    label=f"{sp['name'][:50]} — {spell_price(lvl):,} 🪙",
                    value=f"spell:{sp['index']}",
                    description=f"[{rank}] {sp.get('school',{}).get('name','?')} spell",
                    emoji=RANK_EMOJI.get(rank,"⬜"),
                ))
        elif self._tab in ("potions","travel"):
            src = [(k,v) for k,v in MARKET_ITEMS.items()
                   if (v["type"]=="consumable" if self._tab=="potions" else v.get("travel_only"))]
            for key, it in src[start:start+PAGE_SZ][:25]:
                buy_opts.append(discord.SelectOption(
                    label=f"{it['name'][:50]} — {it['price']:,} 🪙",
                    value=f"market:{key}",
                    description=it["effect"][:80],
                    emoji=RANK_EMOJI.get(it["rank"],"⬜"),
                ))
        else:
            for it in self._filtered_equip(self._tab)[start:start+PAGE_SZ][:25]:
                if not it.get("index"): continue
                cat   = it.get("equipment_category",{}).get("name","?")
                price = it.get("_price", equip_price(cat))
                rank  = "F" if price<500 else ("E" if price<1000 else ("D" if price<2000 else "C"))
                buy_opts.append(discord.SelectOption(
                    label=f"{it['name'][:50]} — {price:,} 🪙",
                    value=f"equip:{it['index']}",
                    description=cat[:50],
                    emoji=RANK_EMOJI.get(rank,"⬜"),
                ))
        if buy_opts:
            bsel = discord.ui.Select(placeholder="🛒 Buy an item…", options=buy_opts, row=4)
            tab_snap = self._tab
            async def buy_cb(inter):
                if inter.user.id != self._ctx.author.id:
                    return await inter.response.send_message("Not for you.", ephemeral=True)
                await inter.response.defer(ephemeral=True)
                val = bsel.values[0]
                if val.startswith("spell:"):
                    await _buy_spell(inter, val[6:], inter.guild_id, inter.user.id)
                elif val.startswith("market:"):
                    await _buy_market_item(inter, val[7:], inter.guild_id, inter.user.id,
                                           travel_fee=tab_snap=="travel")
                else:
                    await _buy_equip(inter, val[6:], inter.guild_id, inter.user.id)
            bsel.callback = buy_cb
            self.add_item(bsel)


# ── Buy logic ───────────────────────────────────────────────────────
async def _buy_spell(inter, spell_idx, gid, uid):
    detail = await get_spell(spell_idx)
    if not detail: return await inter.followup.send("❌ Couldn't fetch spell.", ephemeral=True)
    name  = detail.get("name","?"); lvl = detail.get("level",0)
    price = spell_price(lvl); rank = RANK_FROM_LEVEL.get(min(lvl,9),"F")
    mana  = 10 + lvl*5; school = detail.get("school",{}).get("name","?")
    if await db.pool.fetchval("SELECT 1 FROM rpg_skills WHERE guild_id=$1 AND user_id=$2 AND skill_name=$3",gid,uid,name):
        return await inter.followup.send(f"📚 Already know **{name}**!", ephemeral=True)
    bal = await get_balance(gid,uid)
    if bal < price: return await inter.followup.send(f"❌ Need {price:,} 🪙, have {bal:,}.", ephemeral=True)
    await add_coins(gid,uid,-price)
    await db.pool.execute("INSERT INTO rpg_skills (guild_id,user_id,skill_name,skill_rank,mana_cost) VALUES ($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING",
                          gid,uid,name,rank,mana)
    e = discord.Embed(title=f"📚 Learned: {name}!",
        description=(f"**School:** {school} · **Lv{lvl}** · **[{rank}]**\n"
                     f"**Mana cost:** {mana}\n**Effect:** {spell_effect(detail)}\n\n"
                     f"*-{price:,} 🪙 deducted.*"), color=C_SUCCESS)
    await inter.followup.send(embed=e, ephemeral=True)

async def _buy_equip(inter, equip_idx, gid, uid):
    detail = await get_equip(equip_idx)
    if not detail: return await inter.followup.send("❌ Couldn't fetch item.", ephemeral=True)
    name  = detail.get("name","?")
    cat   = detail.get("equipment_category",{}).get("name","Gear")
    price = equip_price(cat)
    rank  = "F" if price<500 else ("E" if price<1000 else ("D" if price<2000 else "C"))
    slot_map = {"Weapon":"weapon","Shield":"armor","Armor":"armor","Ring":"accessory",
                "Wand":"accessory","Staff":"accessory","Rod":"accessory"}
    slot  = slot_map.get(cat,"accessory")
    ac    = detail.get("armor_class",{}); dmg = detail.get("damage",{})
    eff   = []
    if ac.get("base"):       eff.append(f"+{ac['base']} DEF")
    if dmg.get("damage_dice"): eff.append(f"+DMG ({dmg['damage_dice']})")
    eff_txt = "  ·  ".join(eff) or "General"
    bal = await get_balance(gid,uid)
    if bal < price: return await inter.followup.send(f"❌ Need {price:,} 🪙, have {bal:,}.", ephemeral=True)
    await add_coins(gid,uid,-price)
    await db.pool.execute("""INSERT INTO rpg_equipment (guild_id,user_id,slot,item_name,item_rank,effect_desc)
        VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (guild_id,user_id,slot) DO UPDATE SET item_name=$4,item_rank=$5,effect_desc=$6""",
        gid,uid,slot,name,rank,eff_txt)
    if ac.get("base"): await db.pool.execute("UPDATE rpg_characters SET constitution=constitution+$1 WHERE guild_id=$2 AND user_id=$3",max(1,ac["base"]//2),gid,uid)
    e = discord.Embed(title=f"⚔️ Equipped: {name}!",
        description=(f"**Slot:** {slot} · **[{rank}]**\n**Effect:** {eff_txt}\n\n*-{price:,} 🪙 deducted.*"),color=C_SUCCESS)
    await inter.followup.send(embed=e, ephemeral=True)

async def _buy_market_item(inter, item_key, gid, uid, travel_fee=False):
    it = MARKET_ITEMS.get(item_key)
    if not it: return await inter.followup.send("❌ Item not found.", ephemeral=True)
    total = it["price"] + (50 if travel_fee else 0)
    bal   = await get_balance(gid,uid)
    if bal < total: return await inter.followup.send(f"❌ Need {total:,} 🪙 (incl. travel fee), have {bal:,}.", ephemeral=True)
    await add_coins(gid,uid,-total)
    # Apply effects
    msg = await _apply_market_effect(gid, uid, item_key, it)
    note = " *(+50 🪙 travel fee)*" if travel_fee else ""
    e = discord.Embed(title=f"✅ Bought: {it['name']}",
        description=f"**Effect:** {it['effect']}\n{msg}\n\n*-{total:,} 🪙 deducted{note}.*", color=C_SUCCESS)
    await inter.followup.send(embed=e, ephemeral=True)

async def _apply_market_effect(gid, uid, key, item):
    eff = item.get("effect","")
    if key == "speed_boots":
        await db.pool.execute("UPDATE rpg_characters SET agility=agility+15 WHERE guild_id=$1 AND user_id=$2",gid,uid)
        return "✅ +15 AGI applied permanently."
    elif key == "mage_robe":
        await db.pool.execute("UPDATE rpg_characters SET spirit=spirit+20,mana_max=mana_max+60,mana_current=mana_current+60 WHERE guild_id=$1 AND user_id=$2",gid,uid)
        return "✅ +20 SPI and +60 Mana applied."
    elif key == "shadow_cloak":
        await db.pool.execute("UPDATE rpg_characters SET agility=agility+15 WHERE guild_id=$1 AND user_id=$2",gid,uid)
        await db.pool.execute("""INSERT INTO rpg_equipment (guild_id,user_id,slot,item_name,item_rank,effect_desc,effect_key,effect_value)
            VALUES ($1,$2,'accessory','Shadow Cloak','B','+15 AGI +5% crit','crit_add',0.05)
            ON CONFLICT (guild_id,user_id,slot) DO UPDATE SET item_name='Shadow Cloak',effect_key='crit_add',effect_value=0.05""",gid,uid)
        return "✅ Shadow Cloak equipped. +15 AGI +5% crit."
    elif key == "iron_shield":
        await db.pool.execute("UPDATE rpg_characters SET constitution=constitution+12 WHERE guild_id=$1 AND user_id=$2",gid,uid)
        return "✅ +12 CON applied permanently."
    elif key == "luck_charm":
        await db.pool.execute("""INSERT INTO rpg_equipment (guild_id,user_id,slot,item_name,item_rank,effect_desc,effect_key,effect_value)
            VALUES ($1,$2,'ring','Lucky Charm','C','+5% loot luck','luck',0.05)
            ON CONFLICT (guild_id,user_id,slot) DO UPDATE SET item_name='Lucky Charm',effect_key='luck',effect_value=0.05""",gid,uid)
        return "✅ Lucky Charm equipped. +5% loot drop chance."
    else:  # consumables → inventory
        await db.pool.execute("""INSERT INTO inventory (guild_id,user_id,item_key,quantity) VALUES ($1,$2,$3,1)
            ON CONFLICT (guild_id,user_id,item_key) DO UPDATE SET quantity=inventory.quantity+1""",gid,uid,key)
        return "✅ Added to inventory. Use during dungeon battle."


# ── Cog ─────────────────────────────────────────────────────────────
class RPGShopCog(commands.Cog, name="RPGShop"):
    def __init__(self, bot): self.bot = bot

    @commands.hybrid_command(
        name="shop",
        aliases=["rpgshop","spellshop","equipshop"],
        description="Browse spells, weapons, armor, potions, and market equipment",
    )
    async def shop(self, ctx):
        """Unified RPG shop — spells, equipment, potions, and market items all in one."""
        from .char import get_char
        char = await get_char(ctx.guild.id, ctx.author.id)
        player_class = char.get("class") if char else None

        await ctx.defer()
        e = discord.Embed(title="🔄 Loading Shop…", description="Fetching from D&D 5e API…", color=C_INFO)
        msg = await ctx.send(embed=e)
        view = ShopView(ctx, player_class)
        await view._load()
        view._rebuild()
        await msg.edit(embed=view._embed(), view=view)

    @commands.hybrid_command(name="myspells", description="View your learned spells and skills")
    async def myspells(self, ctx, member: discord.Member = None):
        target = member or ctx.author
        rows = await db.pool.fetch("SELECT skill_name,skill_rank,mana_cost FROM rpg_skills WHERE guild_id=$1 AND user_id=$2", ctx.guild.id, target.id)
        if not rows:
            return await ctx.send(embed=discord.Embed(description="No spells learned yet. Visit `/shop`!", color=C_WARN))
        e = discord.Embed(title=f"📚 {target.display_name}'s Spellbook", color=0x3498db)
        for s in rows:
            e.add_field(name=f"{RANK_EMOJI.get(s['skill_rank'],'⬜')} [{s['skill_rank']}] {s['skill_name']}",
                        value=f"Mana: {s['mana_cost']}", inline=True)
        await ctx.send(embed=e)
