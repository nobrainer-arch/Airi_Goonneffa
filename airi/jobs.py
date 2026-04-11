# airi/jobs.py
import discord
from discord.ext import commands
from datetime import datetime, timedelta
import random
import db
from utils import _err, C_ECONOMY, C_ERROR
from airi.guild_config import check_channel
from airi.economy import add_coins
from airi.audit_log import log as _audit
from utils import log_txn

WORK_COOLDOWN   = 3600
CRIME_COOLDOWN  = 7200
PAY_DAILY_LIMIT = 10000
PAY_TAX         = 0.05

JOBS = [
    ("💼 Salesperson",     (200, 400), 0,  None),
    ("👨‍🍳 Line Cook",        (250, 450), 5,  None),
    ("🎨 Graphic Designer", (300, 500), 10, None),
    ("💻 Freelance Dev",    (400, 700), 20, None),
    ("🎤 Streamer",         (500, 900), 30, None),
]

CRIMES = [
    ("🎰 Card Counting",   (400,  900), (100, 300), 0.60),
    ("🏃 Shoplifting",     (200,  500), (150, 250), 0.65),
    ("💻 Wire Fraud",      (600, 1500), (300, 500), 0.55),
    ("🎭 Scam Call",       (300,  700), (200, 350), 0.60),
]


async def _get_level(guild_id, user_id):
    row = await db.pool.fetchrow("SELECT level FROM xp WHERE guild_id=$1 AND user_id=$2", guild_id, user_id)
    return row["level"] if row else 0

async def _ensure_work(conn, guild_id, user_id):
    await conn.execute(
        "INSERT INTO work_log (guild_id, user_id) VALUES ($1,$2) ON CONFLICT DO NOTHING",
        guild_id, user_id
    )


class JobsCog(commands.Cog, name="Jobs"):
    def __init__(self, bot): self.bot = bot

    @commands.hybrid_command(name="work", description="Work for coins")
    async def work(self, ctx): await self._do_work(ctx)
    async def _do_work(self, ctx):
        """Work a job for coins. 1-hour cooldown."""
        if not await check_channel(ctx, "economy"): return
        gid, uid, now = ctx.guild.id, ctx.author.id, datetime.utcnow()

        async with db.pool.acquire() as conn:
            await _ensure_work(conn, gid, uid)
            row = await conn.fetchrow("SELECT last_work FROM work_log WHERE guild_id=$1 AND user_id=$2", gid, uid)
            if row and row["last_work"]:
                elapsed = now - row["last_work"]
                if elapsed < timedelta(seconds=WORK_COOLDOWN):
                    rem = timedelta(seconds=WORK_COOLDOWN) - elapsed
                    h, s = divmod(int(rem.total_seconds()), 3600)
                    return await _err(ctx, f"Come back in **{h}h {s//60}m**.")

        level   = await _get_level(gid, uid)
        job     = [j for j in JOBS if level >= j[2]][-1]
        earned  = random.randint(*job[1])
        await add_coins(gid, uid, earned)
        await log_txn(ctx.bot, gid, "Work", "System", ctx.author, earned, job[0])
        from airi.milestones import update_achievement
        await update_achievement(ctx.bot, gid, uid, 'work_50', 1, ctx.channel)
        await db.pool.execute("UPDATE work_log SET last_work=$1 WHERE guild_id=$2 AND user_id=$3", now, gid, uid)
        await _audit(gid, uid, "work", job[0], earned)

        e = discord.Embed(color=C_ECONOMY)
        e.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        e.description = f"{job[0]} **{ctx.author.display_name}** earned **{earned:,} coins**!"
        e.set_footer(text=f"Level {level} · 1h cooldown")
        await ctx.send(embed=e)

    @commands.hybrid_command(name="crime", description="Commit a crime for coins")
    async def crime(self, ctx): await self._do_crime(ctx)
    async def _do_crime(self, ctx):
        """Risky: ~60% win, ~40% fine. 2-hour cooldown."""
        if not await check_channel(ctx, "economy"): return
        gid, uid, now = ctx.guild.id, ctx.author.id, datetime.utcnow()

        async with db.pool.acquire() as conn:
            await _ensure_work(conn, gid, uid)
            row = await conn.fetchrow("SELECT last_crime FROM work_log WHERE guild_id=$1 AND user_id=$2", gid, uid)
            if row and row.get("last_crime"):
                elapsed = now - row["last_crime"]
                if elapsed < timedelta(seconds=CRIME_COOLDOWN):
                    rem = timedelta(seconds=CRIME_COOLDOWN) - elapsed
                    h, s = divmod(int(rem.total_seconds()), 3600)
                    return await _err(ctx, f"Lay low for **{h}h {s//60}m** more.")

        crime_entry = random.choice(CRIMES)
        name, win_range, loss_range, rate = crime_entry
        success = random.random() < rate
        await db.pool.execute("UPDATE work_log SET last_crime=$1 WHERE guild_id=$2 AND user_id=$3", now, gid, uid)

        if success:
            earned = random.randint(*win_range)
            await add_coins(gid, uid, earned)
            await _audit(gid, uid, "crime_win", name, earned)
            await log_txn(ctx.bot, gid, "Crime Win", "System", ctx.author, earned, name)
            from airi.milestones import update_achievement
            await update_achievement(ctx.bot, gid, uid, 'crime_win_10', 1, ctx.channel)
            e = discord.Embed(
                description=f"{name} {ctx.author.mention} pulled it off — **+{earned:,} coins** 😈",
                color=C_ECONOMY
            )
        else:
            fine = random.randint(*loss_range)
            await add_coins(gid, uid, -fine)
            await _audit(gid, uid, "crime_fail", name, -fine)
            await log_txn(ctx.bot, gid, "Crime Fine", ctx.author, "System", fine, name)
            e = discord.Embed(
                description=f"{name} {ctx.author.mention} got caught — **−{fine:,} coins** 🚓",
                color=C_ERROR
            )
        e.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=e)

    @commands.hybrid_command(name="jobs", description="See available jobs")
    async def jobs(self, ctx):
        """See all available jobs and their level requirements."""
        level = await _get_level(ctx.guild.id, ctx.author.id)
        e = discord.Embed(title="💼 Available Jobs", color=C_ECONOMY)
        for name, earn_range, min_level, _ in JOBS:
            status = "✅" if level >= min_level else f"🔒 Lv.{min_level}"
            e.add_field(
                name=f"{status} {name}",
                value=f"**{earn_range[0]}–{earn_range[1]}** coins / work",
                inline=True,
            )
        e.set_footer(text=f"Your level: {level} · Work cooldown: 1h")
        await ctx.send(embed=e)
