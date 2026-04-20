# airi/rpg/market.py — RPG Market Location + Travel system (like Dank Memer)
# Players must "travel" to the market to buy RPG items — creates distance feel
# Travel takes 30s–2min. While travelling, buttons are disabled.

import discord
from discord.ext import commands
import asyncio, random
from datetime import datetime, timezone, timedelta
import aiohttp
import db
from utils import _err, C_INFO, C_SUCCESS, C_WARN
from airi.economy import get_balance, add_coins

TRAVEL_DELAY_MIN = 30    # seconds
TRAVEL_DELAY_MAX = 120   # max 2 minutes
MARKET_CD_MINS   = 10    # cooldown between market visits

TRAVEL_MESSAGES = [
    "🚶 You set off toward the **Market District**...",
    "🗺️ You follow the trade road to the **Market**...",
    "🏪 The smell of commerce draws you toward the **Market District**...",
    "⚔️ You sheathe your blade and head to the **Market**...",
    "🌆 You make your way through the city toward the **Market District**...",
    "🛤️ The cobblestones lead you to the **Merchant's Quarter**...",
    "💼 Coin in hand, you head to the **Market**...",
]

ARRIVAL_MESSAGES = [
    "🏪 You arrive at the **Market District**! Browse with the buttons below.",
    "🎪 The **Market** is busy today. What are you looking for?",
    "✨ The **Merchant's Quarter** gleams with rare items.",
    "💰 The **Market District** — where fortunes are made and spent!",
]

DND_API = "https://www.dnd5eapi.co/api"

# ── Market inventory (items available, rotates) ────────────────────
# These pull from DnD API + custom RPG consumables
MARKET_CONSUMABLES = {
    "hp_potion_s": {
        "name":"Small HP Potion",     "price":200,  "effect":"Restore 20% HP in combat",   "type":"consumable","rank":"F"},
    "hp_potion_m": {
        "name":"Medium HP Potion",    "price":500,  "effect":"Restore 40% HP in combat",   "type":"consumable","rank":"E"},
    "hp_potion_l": {
        "name":"Large HP Potion",     "price":1200, "effect":"Restore 70% HP in combat",   "type":"consumable","rank":"D"},
    "mana_potion":  {
        "name":"Mana Potion",         "price":400,  "effect":"Restore 30% Mana in combat", "type":"consumable","rank":"E"},
    "antidote":     {
        "name":"Antidote",            "price":300,  "effect":"Cure Venom status",           "type":"consumable","rank":"F"},
    "elixir":       {
        "name":"Elixir of Strength",  "price":2000, "effect":"+20 STR for 1 dungeon",       "type":"consumable","rank":"C"},
    "revival_orb":  {
        "name":"Revival Orb",         "price":5000, "effect":"Survive 1 lethal hit (auto)", "type":"consumable","rank":"B"},
    "luck_charm":   {
        "name":"Lucky Charm",         "price":1500, "effect":"+5% loot luck for 3 dungeons","type":"accessory","rank":"C"},
    "speed_boots":  {
        "name":"Boots of Swiftness",  "price":3000, "effect":"+15 AGI permanently",          "type":"equipment","rank":"B"},
    "mage_robe":    {
        "name":"Arcane Robe",         "price":2500, "effect":"+20 SPI permanently",          "type":"equipment","rank":"C"},
    "shadow_cloak": {
        "name":"Shadow Cloak",        "price":3500, "effect":"+15 AGI +5% crit permanently", "type":"equipment","rank":"B"},
    "iron_shield":  {
        "name":"Iron Shield",         "price":1800, "effect":"+12 CON permanently",          "type":"equipment","rank":"D"},
}

RANK_EMOJI = {"F":"⬜","E":"🟩","D":"🟦","C":"🔵","B":"🟣","A":"🟠","S":"🔴","SS":"🌟","SSS":"💫"}

# ── Market View ────────────────────────────────────────────────────
class MarketView(discord.ui.View):
    """The market shop UI shown after travel arrival."""
    def __init__(self, ctx):
        super().__init__(timeout=300)
        self._ctx = ctx
        self._tab = "consumables"  # consumables | equipment | accessories
        self._build_controls()

    def _build_controls(self):
        self.clear_items()

        # Tab buttons
        for label, value, emoji in [
            ("🧪 Potions",     "consumables","🧪"),
            ("⚔️ Equipment",   "equipment",  "⚔️"),
            ("💍 Accessories", "accessories","💍"),
        ]:
            btn = discord.ui.Button(
                label=label, emoji=emoji, row=0,
                style=discord.ButtonStyle.primary if self._tab==value else discord.ButtonStyle.secondary,
            )
            tab_snap = value
            async def tab_cb(inter, tv=tab_snap):
                if inter.user.id != self._ctx.author.id:
                    return await inter.response.send_message("Not for you.", ephemeral=True)
                self._tab = tv; self._build_controls()
                await inter.response.edit_message(embed=self._embed(), view=self)
            btn.callback = tab_cb
            self.add_item(btn)

        # Buy dropdown
        items = self._get_items()
        opts  = [
            discord.SelectOption(
                label=f"{it['name'][:50]} — {it['price']:,} 🪙",
                value=key,
                description=it["effect"][:80],
                emoji=RANK_EMOJI.get(it["rank"],"⬜"),
            ) for key, it in items[:25]
        ]
        if opts:
            buy_sel = discord.ui.Select(placeholder="🛒 Buy an item…", options=opts, row=1)
            async def buy_cb(inter):
                if inter.user.id != self._ctx.author.id:
                    return await inter.response.send_message("Not for you.", ephemeral=True)
                await inter.response.defer(ephemeral=True)
                await self._buy(inter, buy_sel.values[0])
            buy_sel.callback = buy_cb
            self.add_item(buy_sel)

        # Leave button
        leave = discord.ui.Button(label="🚪 Leave Market", style=discord.ButtonStyle.secondary, row=2)
        async def leave_cb(inter):
            if inter.user.id != self._ctx.author.id:
                return await inter.response.send_message("Not for you.", ephemeral=True)
            for c in self.children: c.disabled = True
            await inter.response.edit_message(
                embed=discord.Embed(description="🚶 You leave the **Market District**.",color=C_WARN),
                view=self,
            )
        leave.callback = leave_cb
        self.add_item(leave)

    def _get_items(self) -> list[tuple[str,dict]]:
        tab_types = {"consumables":"consumable","equipment":"equipment","accessories":"accessory"}
        t = tab_types.get(self._tab,"consumable")
        return [(k,v) for k,v in MARKET_CONSUMABLES.items() if v["type"]==t]

    def _embed(self) -> discord.Embed:
        e = discord.Embed(
            title="🏪 Market District",
            description=random.choice(ARRIVAL_MESSAGES) + "\n\u200b",
            color=0xf39c12,
        )
        for key, it in self._get_items():
            e.add_field(
                name=f"{RANK_EMOJI.get(it['rank'],'⬜')} [{it['rank']}] {it['name']} — {it['price']:,} 🪙",
                value=it["effect"],
                inline=True,
            )
        e.set_footer(text="Select from the dropdown to buy · 🚪 Leave when done")
        return e

    async def _buy(self, inter: discord.Interaction, item_key: str):
        it   = MARKET_CONSUMABLES.get(item_key)
        if not it:
            return await inter.followup.send("Item not found.", ephemeral=True)
        gid, uid = inter.guild_id, inter.user.id
        bal  = await get_balance(gid, uid)
        if bal < it["price"]:
            return await inter.followup.send(
                f"❌ Need **{it['price']:,}** 🪙 but have **{bal:,}**.", ephemeral=True
            )
        await add_coins(gid, uid, -it["price"])

        # Apply effect
        effect_msg = await _apply_item_effect(gid, uid, item_key, it)

        e = discord.Embed(
            title=f"✅ Bought: {it['name']}",
            description=(
                f"**Effect:** {it['effect']}\n"
                f"{effect_msg}\n\n"
                f"*-{it['price']:,} 🪙 deducted.*"
            ),
            color=C_SUCCESS,
        )
        await inter.followup.send(embed=e, ephemeral=True)


async def _apply_item_effect(gid: int, uid: int, key: str, item: dict):
    """Apply item effects to the character immediately."""
    eff = item.get("effect","")
    msg = ""

    if key == "speed_boots":
        await db.pool.execute("UPDATE rpg_characters SET agility=agility+15 WHERE guild_id=$1 AND user_id=$2", gid, uid)
        msg = "✅ +15 AGI applied permanently."
    elif key == "mage_robe":
        await db.pool.execute(
            "UPDATE rpg_characters SET spirit=spirit+20, mana_max=mana_max+60, mana_current=mana_current+60 WHERE guild_id=$1 AND user_id=$2",
            gid, uid
        )
        msg = "✅ +20 SPI and +60 Mana applied."
    elif key == "shadow_cloak":
        await db.pool.execute("UPDATE rpg_characters SET agility=agility+15 WHERE guild_id=$1 AND user_id=$2", gid, uid)
        # Store in equipment slot
        await db.pool.execute("""
            INSERT INTO rpg_equipment (guild_id,user_id,slot,item_name,item_rank,effect_desc,effect_key,effect_value)
            VALUES ($1,$2,'accessory','Shadow Cloak','B','+15 AGI +5% crit','crit_add',0.05)
            ON CONFLICT (guild_id,user_id,slot) DO UPDATE SET item_name='Shadow Cloak',effect_key='crit_add',effect_value=0.05
        """, gid, uid)
        msg = "✅ Shadow Cloak equipped. +15 AGI +5% crit."
    elif key == "iron_shield":
        await db.pool.execute("UPDATE rpg_characters SET constitution=constitution+12 WHERE guild_id=$1 AND user_id=$2", gid, uid)
        msg = "✅ +12 CON applied permanently."
    elif key == "luck_charm":
        # Store in equipment
        await db.pool.execute("""
            INSERT INTO rpg_equipment (guild_id,user_id,slot,item_name,item_rank,effect_desc,effect_key,effect_value)
            VALUES ($1,$2,'accessory','Lucky Charm','C','+5% loot luck','luck',0.05)
            ON CONFLICT (guild_id,user_id,slot) DO UPDATE SET item_name='Lucky Charm',effect_key='luck',effect_value=0.05
        """, gid, uid)
        msg = "✅ Lucky Charm equipped. +5% loot drop chance."
    elif "potion" in key or "antidote" in key or "elixir" in key or "orb" in key:
        # Store as consumable inventory item
        await db.pool.execute("""
            INSERT INTO inventory (guild_id,user_id,item_key,quantity)
            VALUES ($1,$2,$3,1)
            ON CONFLICT (guild_id,user_id,item_key) DO UPDATE SET quantity=inventory.quantity+1
        """, gid, uid, key)
        msg = f"✅ Added to inventory. Use in dungeon from Skill menu."
    else:
        msg = "✅ Item acquired."
    return msg


# ── Travel sequence ───────────────────────────────────────────────
async def _do_travel(ctx) -> bool:
    """Show travel animation, wait, return True when arrived."""
    delay = random.randint(TRAVEL_DELAY_MIN, TRAVEL_DELAY_MAX)
    travel_msg = random.choice(TRAVEL_MESSAGES)

    for i in range(5):
        dots = "." * (i%3+1)
        e = discord.Embed(
            title="🚶 Travelling to Market",
            description=f"{travel_msg}\n\n⏳ **{delay}s** remaining{dots}",
            color=0x3498db,
        )
        e.set_footer(text="Market sells potions, equipment, accessories. Cannot be bought without travelling!")
        if i == 0:
            msg = await ctx.send(embed=e)
        else:
            await msg.edit(embed=e)
        await asyncio.sleep(delay / 5)

    e = discord.Embed(
        title="✅ Arrived at Market District!",
        description=random.choice(ARRIVAL_MESSAGES),
        color=C_SUCCESS,
    )
    await msg.edit(embed=e)
    await asyncio.sleep(1)
    return msg


# ── Cog ──────────────────────────────────────────────────────────
class MarketCog(commands.Cog, name="Market"):
    def __init__(self, bot):
        self.bot = bot
        self._travelling: set[int] = set()  # user_ids currently travelling

    @commands.hybrid_command(name="market", aliases=["travelmarket","shop_rpg"],
                             description="Travel to the RPG market to buy items")
    async def market(self, ctx):
        """Travel to the market district to buy potions, equipment, and accessories."""
        gid, uid = ctx.guild.id, ctx.author.id

        if uid in self._travelling:
            return await ctx.send("⏳ You're already travelling!", delete_after=5)

        # Check market cooldown
        cd_row = await db.pool.fetchrow(
            "SELECT last_market FROM work_log WHERE guild_id=$1 AND user_id=$2", gid, uid
        )
        if cd_row and cd_row.get("last_market"):
            last = cd_row["last_market"]
            if not hasattr(last,"tzinfo") or last.tzinfo is None:
                from datetime import timezone as _tz; last=last.replace(tzinfo=_tz.utc)
            from datetime import timezone as _tz, timedelta
            elapsed = (datetime.now(_tz.utc)-last).total_seconds()
            if elapsed < MARKET_CD_MINS*60:
                rem = int(MARKET_CD_MINS*60 - elapsed)
                return await ctx.send(
                    embed=discord.Embed(
                        description=f"⏳ Market visit on cooldown. Return in **{rem//60}m {rem%60}s**.",
                        color=C_WARN,
                    ), delete_after=10
                )

        # Check they have a character
        char = await db.pool.fetchrow(
            "SELECT 1 FROM rpg_characters WHERE guild_id=$1 AND user_id=$2", gid, uid
        )
        if not char:
            return await ctx.send(embed=discord.Embed(
                description="You need an RPG character to visit the market! Use `/rpg` first.",
                color=C_WARN,
            ))

        # Travel!
        self._travelling.add(uid)
        try:
            msg = await _do_travel(ctx)
        finally:
            self._travelling.discard(uid)

        # Arrived — set cooldown and show market
        await db.pool.execute("""
            INSERT INTO work_log (guild_id,user_id,last_market) VALUES ($1,$2,NOW())
            ON CONFLICT (guild_id,user_id) DO UPDATE SET last_market=NOW()
        """, gid, uid)

        view = MarketView(ctx)
        await msg.edit(embed=view._embed(), view=view)
