# airi/rpg/events.py — Dungeon Events System
# Random spawn events in bot channel with high rare loot and guaranteed drops
# Static dungeon loot: crappy guaranteed + chance at rare (F→SSS)

import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta
import random
import asyncio
import db
from utils import C_INFO, C_SUCCESS, C_WARN, C_ERROR
from airi.guild_config import get_channel, K_BOT

# ── Loot tables ─────────────────────────────────────────────────────
GRADES = ["F","E","D","C","B","A","S","SS","SSS"]

# Static dungeon chest loot (guaranteed crappy + chance rare)
CHEST_GUARANTEED = {
    1: {"coins":(10,40),   "kakera":0, "item":"Rusty Dagger"},
    2: {"coins":(40,120),  "kakera":1, "item":"Iron Sword"},
    3: {"coins":(120,350), "kakera":2, "item":"Steel Armor"},
    4: {"coins":(350,900), "kakera":5, "item":"Enchanted Cloak"},
    5: {"coins":(900,2500),"kakera":15,"item":"Mythril Blade"},
}
CHEST_RARE_WEIGHTS  = [300,200,150,100,80,60,40,15,5]   # F→SSS normal chest
EVENT_RARE_WEIGHTS  = [50, 80, 120,150,150,150,130,80,50]  # events skewed rare

EVENT_GUARANTEED_LOOT = {
    "Dragon Raid":          {"coins":(2000,5000),"kakera":50,"gems":3,"item":"Dragon Scale"},
    "Boss Invasion":        {"coins":(1500,3500),"kakera":30,"gems":2,"item":"Invader Sword"},
    "Ancient Ruins":        {"coins":(800,2000), "kakera":20,"gems":1,"item":"Ruin Key"},
    "Merchant Caravan":     {"coins":(500,1500), "kakera":10,"gems":0,"item":"Gold Pouch"},
    "Shadow Incursion":     {"coins":(1000,2500),"kakera":25,"gems":1,"item":"Shadow Orb"},
    "Golem Awakening":      {"coins":(800,2000), "kakera":15,"gems":1,"item":"Core Fragment"},
    "Eclipse Gate":         {"coins":(2500,6000),"kakera":60,"gems":4,"item":"Eclipse Shard"},
    "Undead Army":          {"coins":(600,1800), "kakera":20,"gems":1,"item":"Soul Gem"},
}

EVENT_POOL = list(EVENT_GUARANTEED_LOOT.keys())

EVENT_DESCRIPTIONS = {
    "Dragon Raid":     "A **Dragon** descends upon the realm! Adventurers must work together to slay it.",
    "Boss Invasion":   "A powerful **Invader Boss** has appeared at the dungeon gates!",
    "Ancient Ruins":   "An **Ancient Ruin** has surfaced — first to claim it wins rare artifacts.",
    "Merchant Caravan":"A **Lost Merchant** appears, carrying rare wares from distant lands.",
    "Shadow Incursion":"The **Shadow Realm** bleeds into reality. Hunters of the dark are rewarded.",
    "Golem Awakening": "A **Rune Golem** awakens, guarding a treasure hoard from ages past.",
    "Eclipse Gate":    "The **Eclipse Gate** opens once a moon — those who step through gain great power.",
    "Undead Army":     "A **Undead Horde** marches on the town — protect the realm for rewards!",
}

# ── Active event tracker ──────────────────────────────────────────
_active_events: dict[int, dict] = {}  # guild_id → active event data


class EventView(discord.ui.View):
    """Player participation button for an active event."""
    def __init__(self, guild_id: int, event_name: str, loot: dict,
                 ends_at: datetime, server_id: int):
        super().__init__(timeout=None)   # persistent
        self._gid       = guild_id
        self._event     = event_name
        self._loot      = loot
        self._ends_at   = ends_at
        self._server_id = server_id
        self._custom_id = f"event_{guild_id}_{event_name[:10].replace(' ','_')}"
        self.claim_btn.custom_id = self._custom_id

    @discord.ui.button(label="⚔️ Participate!", style=discord.ButtonStyle.danger,
                       custom_id="event_participate")
    async def claim_btn(self, interaction: discord.Interaction, btn):
        now = datetime.now(timezone.utc)
        if now > self._ends_at:
            return await interaction.response.send_message("❌ This event has ended!", ephemeral=True)

        uid = interaction.user.id
        gid = self._gid   # server (Discord guild) ID

        # Check they have a character
        char = await db.pool.fetchrow(
            "SELECT char_level, realm_level FROM rpg_characters WHERE guild_id=$1 AND user_id=$2",
            gid, uid
        )
        if not char:
            return await interaction.response.send_message(
                "❌ You need an RPG character! Use `/rpg` to create one.", ephemeral=True
            )

        # Check if already claimed this event instance
        already = await db.pool.fetchval("""
            SELECT 1 FROM event_claims WHERE guild_id=$1 AND user_id=$2 AND event_id=$3
        """, gid, uid, self._custom_id)
        if already:
            return await interaction.response.send_message(
                "✅ You already participated in this event!", ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        # Record claim
        await db.pool.execute("""
            INSERT INTO event_claims (guild_id, user_id, event_id, claimed_at)
            VALUES ($1,$2,$3,NOW()) ON CONFLICT DO NOTHING
        """, gid, uid, self._custom_id)

        # Guaranteed loot
        coins_range = self._loot.get("coins",(200,500))
        coins   = random.randint(*coins_range)
        kakera  = self._loot.get("kakera",10)
        gems    = self._loot.get("gems",0)
        g_item  = self._loot.get("item","Mystery Box")

        # Rare loot roll
        char_level = int(char.get("char_level") or char.get("realm_level",1))
        luck_bonus = 0.0  # could check accessories
        grade  = random.choices(GRADES, weights=EVENT_RARE_WEIGHTS, k=1)[0]
        grade_coins_mult = {"F":1,"E":1.5,"D":2,"C":3,"B":5,"A":8,"S":15,"SS":25,"SSS":50}
        bonus_coins = int(random.randint(*coins_range) * grade_coins_mult.get(grade,1))
        total_coins = coins + bonus_coins

        from airi.economy import add_coins
        await add_coins(gid, uid, total_coins)
        from airi.kakera import add_kakera
        await add_kakera(gid, uid, kakera)
        if gems:
            await db.pool.execute(
                "UPDATE economy SET gems=gems+$1 WHERE guild_id=$2 AND user_id=$3", gems, gid, uid
            )

        e = discord.Embed(
            title=f"🎁 Event Reward: {self._event}",
            description=(
                f"**Guaranteed loot:**\n"
                f"• {g_item}\n"
                f"• **{coins:,}** 🪙 coins\n"
                f"• **{kakera}** 💎 kakera"
                + (f"\n• **{gems}** 💎 gems" if gems else "")
                + f"\n\n**Rare roll: [{grade}]** → +{bonus_coins:,} bonus coins!\n"
                f"**Total: {total_coins:,} 🪙**"
            ),
            color=C_SUCCESS,
        )
        await interaction.followup.send(embed=e, ephemeral=True)


# ── Event spawn helpers ────────────────────────────────────────────
def _event_embed(event_name: str, loot: dict, ends_at: datetime) -> discord.Embed:
    desc = EVENT_DESCRIPTIONS.get(event_name,"A special event has appeared!")
    guaranteed = loot.get("item","Mystery Box")
    coins_rng  = loot.get("coins",(200,500))
    kakera     = loot.get("kakera",10)
    gems       = loot.get("gems",0)

    e = discord.Embed(
        title=f"🌟 EVENT: {event_name.upper()}",
        description=(
            f"{desc}\n\n"
            f"**Guaranteed Loot:**\n"
            f"• {guaranteed}\n"
            f"• {coins_rng[0]:,}–{coins_rng[1]:,} 🪙\n"
            f"• {kakera} 💎 kakera"
            + (f"\n• {gems} 💎 gems" if gems else "")
            + f"\n\n**+ Rare Roll chance (F → SSS)**\n"
            f"⏰ Ends: {discord.utils.format_dt(ends_at,'R')}\n\n"
            "*Click ⚔️ Participate to claim your rewards!*"
        ),
        color=0xf39c12,
        timestamp=datetime.now(timezone.utc),
    )
    e.set_footer(text="Events spawn randomly · Higher tier = better guaranteed loot")
    return e


async def spawn_event(bot, server_id: int):
    """Spawn a random event in the bot channel."""
    # Get bot channel
    ch_id = await get_channel(server_id, K_BOT)
    if not ch_id:
        # Try to find any text channel
        guild = bot.get_guild(server_id)
        if not guild: return
        ch = guild.system_channel or next((c for c in guild.text_channels if c.permissions_for(guild.me).send_messages), None)
        if not ch: return
    else:
        ch = bot.get_channel(int(ch_id))
        if not ch: return

    event_name = random.choice(EVENT_POOL)
    loot       = EVENT_GUARANTEED_LOOT[event_name]
    duration   = timedelta(minutes=random.randint(30,90))
    ends_at    = datetime.now(timezone.utc) + duration

    view = EventView(server_id, event_name, loot, ends_at, server_id)
    msg  = await ch.send(
        "@here 🌟 **A SPECIAL EVENT HAS APPEARED!**",
        embed=_event_embed(event_name, loot, ends_at),
        view=view,
    )
    _active_events[server_id] = {
        "name": event_name, "msg_id": msg.id,
        "channel_id": ch.id, "ends_at": ends_at,
    }
    # Schedule cleanup
    async def cleanup():
        await asyncio.sleep(duration.total_seconds())
        _active_events.pop(server_id, None)
        try:
            msg2 = await ch.fetch_message(msg.id)
            e = msg2.embeds[0] if msg2.embeds else discord.Embed()
            e.title = f"⏰ EVENT ENDED: {event_name}"
            e.color = 0x808080
            await msg2.edit(embed=e, view=None)
        except Exception: pass
    asyncio.create_task(cleanup())


# ── Cog ──────────────────────────────────────────────────────────────
class EventsCog(commands.Cog, name="Events"):
    def __init__(self, bot):
        self.bot = bot
        self.auto_spawn.start()

    def cog_unload(self):
        self.auto_spawn.cancel()

    @tasks.loop(minutes=45)
    async def auto_spawn(self):
        """Auto-spawn events every ~45 min in each active server."""
        await self.bot.wait_until_ready()
        # Only spawn 30% of the time per cycle to keep it unpredictable
        if random.random() > 0.3:
            return
        for guild in self.bot.guilds:
            # Don't spawn if one is already active
            if guild.id in _active_events:
                continue
            try:
                await spawn_event(self.bot, guild.id)
            except Exception as e:
                print(f"Event spawn error for {guild.id}: {e}")

    @commands.hybrid_command(name="spawnevent", description="[Admin] Manually spawn an event")
    @commands.has_permissions(manage_guild=True)
    async def spawn_event_cmd(self, ctx, event_name: str = None):
        if event_name and event_name not in EVENT_GUARANTEED_LOOT:
            return await ctx.send(f"Unknown event. Options: {', '.join(EVENT_POOL)}")
        if event_name is None:
            event_name = random.choice(EVENT_POOL)
        await spawn_event(self.bot, ctx.guild.id)
        await ctx.send(f"✅ Event **{event_name}** spawned!", delete_after=5)

    @commands.hybrid_command(name="currentevent", description="Check if there's an active event")
    async def current_event(self, ctx):
        ev = _active_events.get(ctx.guild.id)
        if not ev:
            return await ctx.send(embed=discord.Embed(
                description="No active event right now. Events spawn randomly every 45–90 minutes!",
                color=C_WARN,
            ), delete_after=10)
        ends  = ev["ends_at"]
        ch    = ctx.guild.get_channel(ev["channel_id"])
        await ctx.send(embed=discord.Embed(
            description=(
                f"🌟 **{ev['name']}** is active!\n"
                f"Ends: {discord.utils.format_dt(ends,'R')}\n"
                + (f"Channel: {ch.mention}" if ch else "")
            ),
            color=C_INFO,
        ), delete_after=15)


# ── Static Dungeon Chest roller (imported by dungeon_v2) ──────────
def roll_chest_loot(tier: int, luck_bonus: float = 0.0) -> dict:
    """Roll static dungeon chest: crappy guaranteed + chance rare."""
    base = CHEST_GUARANTEED.get(tier, CHEST_GUARANTEED[1])
    coins = random.randint(*base["coins"])
    kakera = base["kakera"]
    item   = base["item"]

    # Rare roll
    weights = list(CHEST_RARE_WEIGHTS)
    if luck_bonus > 0:
        shift = int(luck_bonus * 30)
        for i in range(3): weights[i] = max(1, weights[i]-shift)
        for i in range(3): weights[-1-i] += shift
    grade = random.choices(GRADES, weights=weights, k=1)[0]

    # Rare grade bonus coins
    mult_map = {"F":1,"E":1.5,"D":2,"C":3,"B":5,"A":8,"S":15,"SS":25,"SSS":50}
    bonus = int(coins * mult_map.get(grade,1) * 0.5)

    return {
        "item":   item,
        "coins":  coins + bonus,
        "kakera": kakera,
        "grade":  grade,
        "grade_bonus": bonus,
    }
