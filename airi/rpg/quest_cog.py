# airi/rpg/quest_cog.py — Daily Quest Cog
# Provides /rpg quests command + daily quest panel
# References: MapleStory daily task structure, Galaxy manhwa mission board

import json
import discord
from discord.ext import commands
from datetime import date, timezone, datetime

import db
from .quests import (
    get_daily_quests, format_quests_embed, HIDDEN_QUESTS,
    RATING_EMOJI, RATING_COLOR
)
from .char import add_pending_xp


async def _load_quest_state(gid: int, uid: int) -> dict:
    """Load today's quest state from DB. Returns {quests, last_date, hidden_done}."""
    row = await db.pool.fetchrow(
        "SELECT quest_data FROM rpg_daily_quests WHERE guild_id=$1 AND user_id=$2",
        gid, uid
    )
    today = date.today().isoformat()
    if not row:
        return {"quests": [], "last_date": "", "hidden_done": []}
    try:
        data = json.loads(row["quest_data"])
    except Exception:
        data = {}
    if data.get("last_date") != today:
        # New day — reset quests, keep hidden_done history
        data["quests"]    = []
        data["last_date"] = today
    return data


async def _save_quest_state(gid: int, uid: int, state: dict):
    await db.pool.execute(
        """INSERT INTO rpg_daily_quests (guild_id, user_id, quest_data)
           VALUES ($1, $2, $3)
           ON CONFLICT (guild_id, user_id)
           DO UPDATE SET quest_data = $3""",
        gid, uid, json.dumps(state)
    )


async def _get_char_level(gid: int, uid: int) -> int:
    lvl = await db.pool.fetchval(
        "SELECT char_level FROM rpg_characters WHERE guild_id=$1 AND user_id=$2", gid, uid
    )
    return int(lvl or 1)


class QuestCog(commands.Cog, name="Quests"):
    def __init__(self, bot):
        self.bot = bot

    async def _ensure_quests(self, gid: int, uid: int) -> dict:
        """Ensure today's quests are initialised for this player."""
        state = await _load_quest_state(gid, uid)
        if not state.get("quests"):
            lvl = await _get_char_level(gid, uid)
            state["quests"]    = get_daily_quests(lvl)
            state["last_date"] = date.today().isoformat()
            await _save_quest_state(gid, uid, state)
        return state

    @commands.hybrid_command(name="quests", description="View your daily quests and claim rewards.")
    async def quests_cmd(self, ctx):
        gid = ctx.guild.id; uid = ctx.author.id
        char = await db.pool.fetchrow(
            "SELECT char_level FROM rpg_characters WHERE guild_id=$1 AND user_id=$2", gid, uid
        )
        if not char:
            return await ctx.send("❌ No character found. Use `/rpg start` to create one.", ephemeral=True)

        state = await self._ensure_quests(gid, uid)
        quests = state["quests"]

        # Build embed
        e = discord.Embed(
            title="📋 Daily Quests",
            description=(
                "Complete quests for bonus XP, coins, and kakera.\n"
                "**Quests reset midnight UTC.** Hidden quests unlock on special clears.\n"
            ),
            color=0x9b59b6
        )
        for f in format_quests_embed(quests):
            e.add_field(name=f["name"], value=f["value"], inline=False)

        # Count completions
        done_count = sum(1 for q in quests if q.get("completed") and not q.get("claimed"))
        all_done   = all(q.get("completed") for q in quests)
        e.set_footer(text=f"{done_count}/3 completed today")

        view = QuestClaimView(ctx, gid, uid, state)
        await ctx.send(embed=e, view=view, ephemeral=True)

    async def award_quest_progress(self, gid: int, uid: int, event: str, value: int = 1):
        """
        Called by dungeon/combat systems when game events happen.
        Events: kill_mob, kill_boss, clear_dungeon, use_skill, survive_turn,
                s_rank_clear, nightmare_clear, eye_break, boss_low_hp, no_potion_clear
        """
        from .quests import check_quest_progress
        state = await _load_quest_state(gid, uid)
        if not state.get("quests"):
            return  # No quests loaded yet

        newly_done = check_quest_progress(state["quests"], event, value)
        if newly_done:
            await _save_quest_state(gid, uid, state)
            # Check all-daily-done hidden quest
            if all(q.get("completed") for q in state["quests"]):
                if "all_daily_done" not in state.get("hidden_done", []):
                    state.setdefault("hidden_done", []).append("all_daily_done")
                    # Award all-done bonus
                    hq = next((h for h in HIDDEN_QUESTS if h["trigger"] == "all_daily_done"), None)
                    if hq:
                        rwd = hq["reward"]
                        await add_pending_xp(gid, uid, rwd.get("pending_xp", 0))
                        from airi.economy import add_coins
                        await add_coins(gid, uid, rwd.get("coins", 0))
                        await _save_quest_state(gid, uid, state)
        return newly_done


class QuestClaimView(discord.ui.View):
    def __init__(self, ctx, gid, uid, state):
        super().__init__(timeout=60)
        self._ctx   = ctx
        self._gid   = gid
        self._uid   = uid
        self._state = state

    @discord.ui.button(label="🎁 Claim Completed", style=discord.ButtonStyle.success, row=0)
    async def claim_btn(self, inter, btn):
        if inter.user.id != self._uid:
            return await inter.response.send_message("Not yours.", ephemeral=True)
        await inter.response.defer(ephemeral=True)

        quests = self._state.get("quests", [])
        claimable = [q for q in quests if q.get("completed") and not q.get("claimed")]
        if not claimable:
            return await inter.followup.send("Nothing to claim yet.", ephemeral=True)

        total_xp    = 0
        total_coins = 0
        total_kak   = 0
        total_gems  = 0
        names       = []

        for q in claimable:
            q["claimed"] = True
            rwd = q.get("reward", {})
            total_xp    += rwd.get("pending_xp", 0)
            total_coins += rwd.get("coins", 0)
            total_kak   += rwd.get("kakera", 0)
            total_gems  += rwd.get("gems", 0)
            names.append(q["name"])

        await _save_quest_state(self._gid, self._uid, self._state)

        if total_xp:    await add_pending_xp(self._gid, self._uid, total_xp)
        if total_coins:
            from airi.economy import add_coins
            await add_coins(self._gid, self._uid, total_coins)
        if total_kak:
            from airi.kakera import add_kakera
            await add_kakera(self._gid, self._uid, total_kak)
        if total_gems:
            await db.pool.execute(
                "UPDATE economy SET gems=gems+$1 WHERE guild_id=$2 AND user_id=$3",
                total_gems, self._gid, self._uid
            )

        e = discord.Embed(
            title="🎉 Quests Claimed!",
            description=(
                "\n".join(f"✅ {n}" for n in names) + "\n\n"
                f"**Rewards:** +{total_xp} XP  ·  +{total_coins} 🪙  ·  +{total_kak} 💎"
                + (f"  ·  +{total_gems} ✨" if total_gems else "")
            ),
            color=0xf1c40f
        )
        await inter.followup.send(embed=e, ephemeral=True)
        self.stop()

    @discord.ui.button(label="🔄 Refresh", style=discord.ButtonStyle.secondary, row=0)
    async def refresh_btn(self, inter, btn):
        if inter.user.id != self._uid:
            return await inter.response.send_message("Not yours.", ephemeral=True)
        await inter.response.defer(ephemeral=True)

        state = await _load_quest_state(self._gid, self._uid)
        lvl   = await _get_char_level(self._gid, self._uid)
        if not state.get("quests"):
            state["quests"]    = get_daily_quests(lvl)
            state["last_date"] = date.today().isoformat()
            await _save_quest_state(self._gid, self._uid, state)
        self._state = state

        e = discord.Embed(title="📋 Daily Quests (Refreshed)", color=0x9b59b6)
        for f in format_quests_embed(state["quests"]):
            e.add_field(name=f["name"], value=f["value"], inline=False)
        await inter.followup.send(embed=e, ephemeral=True)


async def setup(bot):
    await bot.add_cog(QuestCog(bot))
