# airi/milestones.py — Milestone & Achievement system
# Milestones: one-time rewards for reaching thresholds (coins + kakera)
# Achievements: tracked progress toward goals
import discord
from datetime import datetime, timezone
import db
from utils import C_GACHA, C_SUCCESS

# ── Milestone definitions ─────────────────────────────────────────
# key format: "{category}_{threshold}"
MILESTONES: dict[str, dict] = {
    # Hug milestones
    "hug_10":    {"label": "🤗 10 Hugs Received",    "coins": 100,    "kakera": 1},
    "hug_50":    {"label": "🤗 50 Hugs Received",    "coins": 500,    "kakera": 5},
    "hug_100":   {"label": "🤗 100 Hugs Received",   "coins": 1000,   "kakera": 10},
    "hug_500":   {"label": "🤗 500 Hugs Received",   "coins": 5000,   "kakera": 50},
    "hug_1000":  {"label": "🤗 1,000 Hugs Received", "coins": 10000,  "kakera": 100},
    # Kiss milestones
    "kiss_10":   {"label": "💋 10 Kisses Received",   "coins": 200,   "kakera": 2},
    "kiss_50":   {"label": "💋 50 Kisses Received",   "coins": 1000,  "kakera": 10},
    "kiss_100":  {"label": "💋 100 Kisses Received",  "coins": 2500,  "kakera": 25},
    "kiss_500":  {"label": "💋 500 Kisses Received",  "coins": 10000, "kakera": 75},
    # Pat milestones
    "pat_10":    {"label": "🤚 10 Pats Received",     "coins": 50,    "kakera": 1},
    "pat_100":   {"label": "🤚 100 Pats Received",    "coins": 500,   "kakera": 5},
    "pat_500":   {"label": "🤚 500 Pats Received",    "coins": 2000,  "kakera": 20},
    # Level milestones
    "level_5":   {"label": "⬆️ Level 5",              "coins": 200,   "kakera": 2},
    "level_10":  {"label": "⬆️ Level 10",             "coins": 500,   "kakera": 5},
    "level_25":  {"label": "⬆️ Level 25",             "coins": 1500,  "kakera": 15},
    "level_50":  {"label": "⬆️ Level 50",             "coins": 5000,  "kakera": 50},
    "level_75":  {"label": "⬆️ Level 75",             "coins": 10000, "kakera": 75},
    "level_100": {"label": "⬆️ Level 100",            "coins": 20000, "kakera": 150},
    # Gacha milestones
    "gacha_10":   {"label": "🎰 10 Gacha Rolls",      "coins": 200,   "kakera": 3},
    "gacha_50":   {"label": "🎰 50 Gacha Rolls",      "coins": 1000,  "kakera": 10},
    "gacha_100":  {"label": "🎰 100 Gacha Rolls",     "coins": 3000,  "kakera": 25},
    "gacha_500":  {"label": "🎰 500 Gacha Rolls",     "coins": 10000, "kakera": 75},
    "gacha_1000": {"label": "🎰 1,000 Gacha Rolls",   "coins": 25000, "kakera": 200},
    # Proposal milestones
    "proposals_1":  {"label": "💍 First Marriage",    "coins": 500,   "kakera": 5},
    "proposals_3":  {"label": "💍 3 Marriages",       "coins": 1500,  "kakera": 15},
    "proposals_5":  {"label": "💍 5 Marriages",       "coins": 3000,  "kakera": 30},
    # Marriage duration milestones (days)
    "married_1":   {"label": "💑 Married 1 Day",      "coins": 100,   "kakera": 1},
    "married_7":   {"label": "💑 Married 7 Days",     "coins": 500,   "kakera": 5},
    "married_30":  {"label": "💑 Married 30 Days",    "coins": 2000,  "kakera": 20},
    "married_100": {"label": "💑 Married 100 Days",   "coins": 10000, "kakera": 100},
    "married_365": {"label": "💑 1 Year Anniversary", "coins": 50000, "kakera": 500},
}

# ── Achievement definitions ───────────────────────────────────────
# type: "counter" (tracks a count), "boolean" (done once)
ACHIEVEMENTS: dict[str, dict] = {
    "hugger":        {"name": "🤗 Serial Hugger",      "desc": "Give 1,000 hugs",       "goal": 1000,  "coins": 5000,  "kakera": 50,  "type": "counter"},
    "kisser":        {"name": "💋 Kiss Machine",        "desc": "Give 500 kisses",       "goal": 500,   "coins": 3000,  "kakera": 30,  "type": "counter"},
    "roller":        {"name": "🎰 Gacha Addict",       "desc": "Roll gacha 500 times",  "goal": 500,   "coins": 5000,  "kakera": 50,  "type": "counter"},
    "wealthy":       {"name": "💰 Wealthy",             "desc": "Earn 100,000 total coins", "goal": 100000, "coins": 0, "kakera": 100, "type": "counter"},
    "social_star":   {"name": "⭐ Social Star",         "desc": "Receive 100 rep",       "goal": 100,   "coins": 2000,  "kakera": 20,  "type": "counter"},
    "claim_master":  {"name": "👑 Claim Master",        "desc": "Claim 10 waifus",       "goal": 10,    "coins": 3000,  "kakera": 25,  "type": "counter"},
    "first_claim":   {"name": "💘 First Claim",         "desc": "Claim your first waifu","goal": 1,     "coins": 200,   "kakera": 2,   "type": "counter"},
    "first_marriage":{"name": "💍 Newlywed",            "desc": "Get married",           "goal": 1,     "coins": 1000,  "kakera": 10,  "type": "counter"},
    "level_up_10":   {"name": "📈 Rising Star",         "desc": "Reach level 10",        "goal": 10,    "coins": 500,   "kakera": 5,   "type": "level"},
    "work_50":       {"name": "💼 Dedicated Worker",    "desc": "Work 50 times",         "goal": 50,    "coins": 2000,  "kakera": 15,  "type": "counter"},
    "daily_7":       {"name": "📅 Weekly Devotion",     "desc": "7-day daily streak",    "goal": 7,     "coins": 1000,  "kakera": 10,  "type": "streak"},
    "daily_30":      {"name": "📅 Monthly Devotion",    "desc": "30-day daily streak",   "goal": 30,    "coins": 5000,  "kakera": 50,  "type": "streak"},
    "crime_win_10":  {"name": "🦹 Career Criminal",     "desc": "Win 10 crimes",         "goal": 10,    "coins": 1000,  "kakera": 10,  "type": "counter"},
    "first_ah":      {"name": "🏪 Market Debut",        "desc": "Buy from Auction House","goal": 1,     "coins": 300,   "kakera": 3,   "type": "counter"},
}


async def _already_claimed(guild_id: int, user_id: int, key: str) -> bool:
    return bool(await db.pool.fetchval(
        "SELECT 1 FROM milestones_claimed WHERE guild_id=$1 AND user_id=$2 AND milestone=$3",
        guild_id, user_id, key
    ))


async def _mark_claimed(guild_id: int, user_id: int, key: str):
    await db.pool.execute("""
        INSERT INTO milestones_claimed (guild_id, user_id, milestone)
        VALUES ($1, $2, $3) ON CONFLICT DO NOTHING
    """, guild_id, user_id, key)


async def check_milestone(bot, guild_id: int, user_id: int, category: str, current_value: int,
                           announce_channel=None):
    """Check and award any newly reached milestones for a given category + counter value."""
    from airi.economy import add_coins
    from airi.kakera import add_kakera

    awarded = []
    for key, m in MILESTONES.items():
        if not key.startswith(category + "_"):
            continue
        threshold = int(key.split("_")[-1])
        if current_value < threshold:
            continue
        if await _already_claimed(guild_id, user_id, key):
            continue

        # Award!
        await _mark_claimed(guild_id, user_id, key)
        if m["coins"] > 0:
            await add_coins(guild_id, user_id, m["coins"])
        if m["kakera"] > 0:
            await add_kakera(guild_id, user_id, m["kakera"])
        awarded.append(m)

    if awarded and announce_channel:
        member = announce_channel.guild.get_member(user_id)
        if member:
            for m in awarded:
                e = discord.Embed(
                    title="🏆 Milestone Reached!",
                    description=(
                        f"**{member.display_name}** reached **{m['label']}**!\n\n"
                        + (f"💰 +**{m['coins']:,}** coins" if m["coins"] else "")
                        + ("  " if m["coins"] and m["kakera"] else "")
                        + (f"💎 +**{m['kakera']:,}** kakera" if m["kakera"] else "")
                    ),
                    color=C_SUCCESS,
                )
                e.set_thumbnail(url=member.display_avatar.url)
                try:
                    await announce_channel.send(embed=e, delete_after=30)
                except Exception:
                    pass
    return awarded


async def update_achievement(bot, guild_id: int, user_id: int, achievement_key: str,
                              increment: int = 1, announce_channel=None):
    """Increment an achievement counter and award if newly completed."""
    if achievement_key not in ACHIEVEMENTS:
        return
    a = ACHIEVEMENTS[achievement_key]
    row = await db.pool.fetchrow("""
        INSERT INTO achievements (guild_id, user_id, achievement, progress)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (guild_id, user_id, achievement)
        DO UPDATE SET progress = LEAST(achievements.progress + $4, $5)
        RETURNING progress, completed
    """, guild_id, user_id, achievement_key, increment, a["goal"])

    if not row or row["completed"]:
        return
    if row["progress"] >= a["goal"]:
        await db.pool.execute("""
            UPDATE achievements SET completed=TRUE, completed_at=NOW()
            WHERE guild_id=$1 AND user_id=$2 AND achievement=$3
        """, guild_id, user_id, achievement_key)

        from airi.economy import add_coins
        from airi.kakera import add_kakera
        if a["coins"] > 0: await add_coins(guild_id, user_id, a["coins"])
        if a["kakera"] > 0: await add_kakera(guild_id, user_id, a["kakera"])

        if announce_channel:
            member = announce_channel.guild.get_member(user_id)
            if member:
                e = discord.Embed(
                    title="🏅 Achievement Unlocked!",
                    description=(
                        f"{member.mention} completed **{a['name']}**!\n"
                        f"*{a['desc']}*\n\n"
                        + (f"💰 +**{a['coins']:,}** coins" if a["coins"] else "")
                        + ("  " if a["coins"] and a["kakera"] else "")
                        + (f"💎 +**{a['kakera']:,}** kakera" if a["kakera"] else "")
                    ),
                    color=C_SUCCESS,
                )
                e.set_thumbnail(url=member.display_avatar.url)
                try:
                    await announce_channel.send(embed=e, delete_after=30)
                except Exception:
                    pass


class MilestonesCog(discord.ext.commands.Cog, name="Milestones"):
    def __init__(self, bot): self.bot = bot

    @discord.ext.commands.command(aliases=["achievements", "ach"])
    async def achieve(self, ctx, member: discord.Member = None):
        """View your achievements progress."""
        target = member or ctx.author
        gid, uid = ctx.guild.id, target.id

        rows = await db.pool.fetch("""
            SELECT achievement, progress, completed, completed_at
            FROM achievements WHERE guild_id=$1 AND user_id=$2
            ORDER BY completed DESC, progress DESC
        """, gid, uid)

        ach_data = {r["achievement"]: r for r in rows}
        PAGE = 5
        all_keys = list(ACHIEVEMENTS.keys())
        pages = [all_keys[i:i+PAGE] for i in range(0, len(all_keys), PAGE)]
        current = [0]

        def build_page(idx):
            chunk = pages[idx]
            e = discord.Embed(
                title=f"🏅 {target.display_name}'s Achievements",
                color=C_GACHA,
            )
            e.set_thumbnail(url=target.display_avatar.url)
            for key in chunk:
                a = ACHIEVEMENTS[key]
                data = ach_data.get(key)
                progress = data["progress"] if data else 0
                done     = data["completed"] if data else False
                bar_len  = 10
                filled   = int((progress / a["goal"]) * bar_len) if a["goal"] else bar_len
                bar      = "█" * filled + "░" * (bar_len - filled)
                status   = "✅" if done else "⬜"
                reward_txt = ""
                if a["coins"]: reward_txt += f"💰 {a['coins']:,}"
                if a["kakera"]: reward_txt += f"  💎 {a['kakera']}"
                e.add_field(
                    name=f"{status} {a['name']}",
                    value=f"*{a['desc']}*\n`{bar}` {progress}/{a['goal']}\n{reward_txt}",
                    inline=False,
                )
            e.set_footer(text=f"Page {idx+1}/{len(pages)} · Completed: {sum(1 for r in rows if r['completed'])}/{len(ACHIEVEMENTS)}")
            return e

        class AchView(discord.ui.View):
            def __init__(self_):
                super().__init__(timeout=180)
                self_._upd()
            def _upd(self_):
                self_.prev.disabled = current[0] == 0
                self_.nxt.disabled  = current[0] == len(pages) - 1
            @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
            async def prev(self_, inter, btn):
                if inter.user.id != ctx.author.id:
                    return await inter.response.send_message("Not for you.", ephemeral=True)
                current[0] -= 1; self_._upd()
                await inter.response.edit_message(embed=build_page(current[0]), view=self_)
            @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
            async def nxt(self_, inter, btn):
                if inter.user.id != ctx.author.id:
                    return await inter.response.send_message("Not for you.", ephemeral=True)
                current[0] += 1; self_._upd()
                await inter.response.edit_message(embed=build_page(current[0]), view=self_)

        v = AchView() if len(pages) > 1 else None
        await ctx.send(embed=build_page(0), view=v)

    @discord.ext.commands.command(aliases=["ms"])
    async def milestones(self, ctx, member: discord.Member = None):
        """View milestone progress for hug/kiss/pat/level/gacha."""
        target = member or ctx.author
        gid, uid = ctx.guild.id, target.id

        # Pull stats
        soc = await db.pool.fetchrow(
            "SELECT hugs_received, kisses_received, pats_received, rep FROM social WHERE guild_id=$1 AND user_id=$2",
            gid, uid
        )
        xpr = await db.pool.fetchrow("SELECT level FROM xp WHERE guild_id=$1 AND user_id=$2", gid, uid)

        hugs  = soc["hugs_received"]  if soc else 0
        kiss  = soc["kisses_received"] if soc else 0
        pats  = soc["pats_received"]  if soc else 0
        level = xpr["level"]          if xpr else 0

        claimed = {
            r["milestone"] async for r in
            await db.pool.fetch("SELECT milestone FROM milestones_claimed WHERE guild_id=$1 AND user_id=$2", gid, uid)
        } if False else set()
        claimed_rows = await db.pool.fetch(
            "SELECT milestone FROM milestones_claimed WHERE guild_id=$1 AND user_id=$2", gid, uid
        )
        claimed = {r["milestone"] for r in claimed_rows}

        def milestone_line(key, current_val):
            threshold = int(key.split("_")[-1])
            m = MILESTONES[key]
            done = key in claimed
            pct  = min(100, int(current_val / threshold * 100))
            bar  = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
            status = "✅" if done else f"`{bar}` {current_val}/{threshold}"
            return f"{'✅' if done else '⬜'} **{m['label']}** — {status}  💰{m['coins']:,} 💎{m['kakera']}"

        e = discord.Embed(title=f"🎯 {target.display_name}'s Milestones", color=C_GACHA)
        e.set_thumbnail(url=target.display_avatar.url)

        hug_lines   = [milestone_line(k, hugs)  for k in MILESTONES if k.startswith("hug_")]
        kiss_lines  = [milestone_line(k, kiss)  for k in MILESTONES if k.startswith("kiss_")]
        pat_lines   = [milestone_line(k, pats)  for k in MILESTONES if k.startswith("pat_")]
        level_lines = [milestone_line(k, level) for k in MILESTONES if k.startswith("level_")]

        if hug_lines:  e.add_field(name=f"🤗 Hugs ({hugs})",   value="\n".join(hug_lines[:3]),   inline=False)
        if kiss_lines: e.add_field(name=f"💋 Kisses ({kiss})", value="\n".join(kiss_lines[:3]),  inline=False)
        if pat_lines:  e.add_field(name=f"🤚 Pats ({pats})",   value="\n".join(pat_lines[:3]),   inline=False)
        if level_lines:e.add_field(name=f"⬆️ Level ({level})", value="\n".join(level_lines[:3]), inline=False)

        await ctx.send(embed=e)

import discord.ext.commands
