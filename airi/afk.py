# airi/afk.py
import discord
from discord.ext import commands
from datetime import datetime, timezone
import db
from utils import C_WARN, C_INFO, _err

async def _ensure_table():
    """afk table created in db._create_tables — this is just a helper import guard."""
    pass

def _make_tz_aware(ts):
    if ts is None: return None
    from datetime import timezone as _tz
    if hasattr(ts, "tzinfo") and ts.tzinfo is not None: return ts
    return ts.replace(tzinfo=_tz.utc)

async def get_afk(guild_id: int, user_id: int) -> dict | None:
    row = await db.pool.fetchrow(
        "SELECT reason, set_at FROM afk WHERE guild_id=$1 AND user_id=$2",
        guild_id, user_id
    )
    return dict(row) if row else None

async def set_afk(guild_id: int, user_id: int, reason: str):
    await db.pool.execute("""
        INSERT INTO afk (guild_id, user_id, reason, set_at)
        VALUES ($1,$2,$3,NOW())
        ON CONFLICT (guild_id, user_id) DO UPDATE SET reason=$3, set_at=NOW()
    """, guild_id, user_id, reason)

async def clear_afk(guild_id: int, user_id: int):
    await db.pool.execute("DELETE FROM afk WHERE guild_id=$1 AND user_id=$2", guild_id, user_id)


class AFKCog(commands.Cog, name="AFK"):
    def __init__(self, bot): self.bot = bot

    @commands.hybrid_command(aliases=["away"])
    async def afk(self, ctx, *, reason: str = "AFK"):
        """Set yourself as AFK. Bot will notify people who mention you."""
        reason = reason[:100]  # cap length
        await set_afk(ctx.guild.id, ctx.author.id, reason)
        e = discord.Embed(
            title="Set to AFK.",
            description=f"**Reason:** {reason}\n\nI'll let them know if they mention you. >w<",
            color=C_WARN,
        )
        e.set_thumbnail(url=ctx.author.display_avatar.url)
        e.set_footer(text=ctx.author.display_name)
        await ctx.send(embed=e)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        gid = message.guild.id
        uid = message.author.id

        # Return-from-AFK detection
        afk_data = await get_afk(gid, uid)
        if afk_data:
            await clear_afk(gid, uid)
            e = discord.Embed(
                description=f"Welcome back {message.author.mention}! I've removed your AFK.",
                color=C_INFO,
            )
            try:
                await message.channel.send(embed=e, delete_after=8)
            except Exception:
                pass

        # Notify AFK users when mentioned
        if not message.mentions:
            return
        for mentioned in message.mentions:
            if mentioned.bot or mentioned.id == uid:
                continue
            afk_info = await get_afk(gid, mentioned.id)
            if afk_info:
                since = afk_info["set_at"]
                if since and (not hasattr(since,'tzinfo') or since.tzinfo is None): since = since.replace(tzinfo=__import__('datetime').timezone.utc)
                delta = (datetime.now(__import__('datetime').timezone.utc) - since) if since else None
                time_str = ""
                if delta:
                    h, rem = divmod(int(delta.total_seconds()), 3600)
                    m = rem // 60
                    if h: time_str = f" ({h}h {m}m ago)"
                    elif m: time_str = f" ({m}m ago)"
                e = discord.Embed(
                    description=(
                        f"**{mentioned.display_name}** is currently AFK{time_str}.\n"
                        f"**Reason:** {afk_info['reason']}"
                    ),
                    color=C_WARN,
                )
                e.set_thumbnail(url=mentioned.display_avatar.url)
                try:
                    await message.channel.send(embed=e, delete_after=10)
                except Exception:
                    pass
