# airi/audit_log.py
# Lightweight audit trail for economy actions and moderation.
# Kept minimal to avoid RAM/disk bloat — rows auto-pruned after 30 days.
import discord
from discord.ext import commands
import db
from utils import _err, C_INFO, is_mod
from airi.guild_config import check_channel

MAX_LOG_ROWS = 500  # per guild — older rows pruned on write
LOG_KEEP_DAYS = 30

# ── Write helpers ────────────────────────────────────────────────

async def log(guild_id: int, user_id: int, action: str, detail: str = None,
              amount: int = None, ):
    """Insert an audit row. Fire-and-forget — caller does not await errors."""
    try:
        await db.pool.execute("""
            INSERT INTO audit_log (guild_id, user_id, action, detail, amount)
            VALUES ($1,$2,$3,$4,$5)
        """, guild_id, user_id, action, detail, amount)
    except Exception as e:
        print(f"Audit log write failed: {e}")

async def prune_old(guild_id: int):
    """Remove rows older than LOG_KEEP_DAYS. Call once per day from background task."""
    try:
        await db.pool.execute("""
            DELETE FROM audit_log
            WHERE guild_id=$1 AND created_at < NOW() - INTERVAL '30 days'
        """, guild_id)
    except Exception as e:
        print(f"Audit prune failed: {e}")


# ── Cog ──────────────────────────────────────────────────────────

class AuditLogCog(commands.Cog, name="AuditLog"):
    def __init__(self, bot): self.bot = bot

    @commands.group(name="auditlog", aliases=["audit", "logs"], invoke_without_command=True)
    async def auditlog(self, ctx):
        if not is_mod(ctx.author):
            return await _err(ctx, "You are not a mod.")
        await ctx.send(
            "**Audit log commands:**\n"
            "`!audit user @user [count]` — last N actions by a user\n"
            "`!audit recent [count]` — last N actions in this server\n"
            "`!audit types` — list of logged action types"
        )

    @auditlog.command(name="user")
    async def audit_user(self, ctx, member: discord.Member, count: int = 15):
        if not is_mod(ctx.author): return await _err(ctx, "You are not a mod.")
        count = min(count, 50)
        gid = ctx.guild.id
        rows = await db.pool.fetch("""
            SELECT action, detail, amount, balance_before, balance_after, created_at
            FROM audit_log
            WHERE guild_id=$1 AND user_id=$2
            ORDER BY created_at DESC LIMIT $3
        """, gid, member.id, count)

        if not rows:
            return await ctx.send(embed=discord.Embed(
                description=f"No audit entries for {member.mention}.", color=C_INFO
            ))

        e = discord.Embed(title=f"📋 Audit Log — {member.display_name}", color=C_INFO)
        lines = []
        for r in rows:
            ts = r["created_at"].strftime("%m/%d %H:%M")
            amt = f" ({'+' if r['amount'] and r['amount']>0 else ''}{r['amount']:,})" if r["amount"] else ""
            lines.append(f"`{ts}` **{r['action']}**{amt}" + (f" — {r['detail']}" if r["detail"] else ""))
        e.description = "\n".join(lines[:20])
        e.set_footer(text=f"Showing last {min(len(rows),20)} of {len(rows)} entries")
        await ctx.send(embed=e)

    @auditlog.command(name="recent")
    async def audit_recent(self, ctx, count: int = 20):
        if not is_mod(ctx.author): return await _err(ctx, "You are not a mod.")
        count = min(count, 50)
        gid   = ctx.guild.id
        rows  = await db.pool.fetch("""
            SELECT user_id, action, detail, amount, created_at
            FROM audit_log WHERE guild_id=$1
            ORDER BY created_at DESC LIMIT $2
        """, gid, count)

        if not rows:
            return await ctx.send(embed=discord.Embed(description="No audit entries yet.", color=C_INFO))

        e = discord.Embed(title="📋 Recent Audit Log", color=C_INFO)
        lines = []
        for r in rows:
            ts   = r["created_at"].strftime("%m/%d %H:%M")
            m    = ctx.guild.get_member(r["user_id"])
            name = m.display_name if m else f"<@{r['user_id']}>"
            amt  = f" ({'+' if r['amount'] and r['amount']>0 else ''}{r['amount']:,})" if r["amount"] else ""
            lines.append(f"`{ts}` **{name}** — {r['action']}{amt}")
        e.description = "\n".join(lines)
        await ctx.send(embed=e)

    @auditlog.command(name="types")
    async def audit_types(self, ctx):
        if not is_mod(ctx.author): return await _err(ctx, "You are not a mod.")
        e = discord.Embed(title="📋 Logged Action Types", color=C_INFO)
        e.description = (
            "`daily_claim` `work` `crime` `pay` `give`\n"
            "`shop_buy` `gacha_roll` `ah_sell` `ah_buy` `ah_cancel`\n"
            "`claim_waifu` `waifu_sold` `relationship_start` `relationship_end`\n"
            "`biz_collect` `biz_open` `biz_sell`\n"
            "`ban` `kick` `timeout` `shutdown`"
        )
        await ctx.send(embed=e)
