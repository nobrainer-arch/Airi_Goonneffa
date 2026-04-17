# airi/xp.py — updated for multi-guild economy calls
import discord
from discord.ext import commands
from datetime import datetime, timezone
import random
import config
import db
from airi.economy import is_xp_boosted, add_coins
from airi.guild_config import get_channel, get_channels, K_XP, K_LEVELUP

XP_MIN      = 15
XP_MAX      = 25
XP_COOLDOWN = 60

def xp_for_level(level: int) -> int:
    return int(100 * (level ** 1.5) + 50 * level)

def level_from_xp(total_xp: int) -> int:
    level = 0
    while xp_for_level(level + 1) <= total_xp:
        level += 1
    return level

def coin_reward_for_level(level: int) -> int:
    return level * 50

async def get_rank(guild_id: int, user_id: int) -> int:
    rows = await db.pool.fetch("SELECT user_id FROM xp WHERE guild_id=$1 ORDER BY xp DESC", guild_id)
    ids = [r["user_id"] for r in rows]
    try:    return ids.index(user_id) + 1
    except: return len(ids) + 1


class XPCog(commands.Cog, name="XP"):
    def __init__(self, bot):
        self.bot = bot
        self._cooldowns: dict[str, float] = {}

    def _on_cooldown(self, guild_id, user_id):
        key = f"{guild_id}_{user_id}"
        import time; now = time.time()
        if now - self._cooldowns.get(key, 0) < XP_COOLDOWN:
            return True
        self._cooldowns[key] = now
        return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        # XP channel restriction is handled by check_channel; here we respect K_XP
        xp_chs = await get_channels(message.guild.id, K_XP)
        if xp_chs and message.channel.id not in xp_chs:
            return
        if self._on_cooldown(message.guild.id, message.author.id):
            return

        gid, uid = message.guild.id, message.author.id
        gain = random.randint(XP_MIN, XP_MAX)
        if await is_xp_boosted(gid, uid):
            gain *= 2

        row = await db.pool.fetchrow("""
            INSERT INTO xp (guild_id, user_id, xp, level) VALUES ($1, $2, $3, 0)
            ON CONFLICT (guild_id, user_id) DO UPDATE SET xp = xp.xp + $3
            RETURNING xp, level
        """, gid, uid, gain)

        new_xp    = row["xp"]
        old_level = row["level"]
        new_level = level_from_xp(new_xp)

        if new_level > old_level:
            await db.pool.execute("UPDATE xp SET level=$1 WHERE guild_id=$2 AND user_id=$3", new_level, gid, uid)
            reward = coin_reward_for_level(new_level)
            await add_coins(gid, uid, reward)
            # Grant 2 RPG stat points per level-up
            try:
                await db.pool.execute(
                    "UPDATE rpg_characters SET stat_points=stat_points+2 WHERE guild_id=$1 AND user_id=$2",
                    gid, uid
                )
            except Exception:
                pass
            # Level milestones
            from airi.milestones import check_milestone, update_achievement
            await check_milestone(self.bot, gid, uid, 'level', new_level, message.channel)
            await update_achievement(self.bot, gid, uid, 'level_up_10', new_level, message.channel)

            title = await db.pool.fetchval("SELECT active_title FROM economy WHERE guild_id=$1 AND user_id=$2", gid, uid)

            embed = discord.Embed(
                title="⬆️ Level Up!",
                description=(
                    f"{message.author.mention} reached **Level {new_level}**!\n"
                    f"💰 +{reward:,} coins"
                ),
                color=discord.Color.green(),
            )
            embed.set_thumbnail(url=message.author.display_avatar.url)
            if title:
                embed.set_footer(text=f"✨ {title}")

            levelup_ch_id = await get_channel(gid, K_LEVELUP)
            target_ch = self.bot.get_channel(levelup_ch_id) if levelup_ch_id else message.channel
            await target_ch.send(embed=embed)

    @commands.hybrid_command(aliases=["rank", "xp", "level"])
    async def rankcard(self, ctx, member: discord.Member = None):
        target = member or ctx.author
        gid, uid = ctx.guild.id, target.id
        row = await db.pool.fetchrow("SELECT xp, level FROM xp WHERE guild_id=$1 AND user_id=$2", gid, uid)
        total_xp = row["xp"]    if row else 0
        level    = row["level"] if row else 0
        current_floor = xp_for_level(level)
        next_ceiling  = xp_for_level(level + 1)
        needed_xp     = next_ceiling - current_floor
        progress_xp   = total_xp - current_floor
        pct           = min(100, int((progress_xp / needed_xp) * 100)) if needed_xp else 100
        filled        = int(pct / 100 * 12)
        bar           = "█" * filled + "░" * (12 - filled)
        rank          = await get_rank(gid, uid)
        title         = await db.pool.fetchval("SELECT active_title FROM economy WHERE guild_id=$1 AND user_id=$2", gid, uid)

        embed = discord.Embed(color=discord.Color.blurple())
        embed.set_author(name=target.display_name, icon_url=target.display_avatar.url)
        embed.add_field(name="Level",    value=str(level),      inline=True)
        embed.add_field(name="Rank",     value=f"#{rank}",      inline=True)
        embed.add_field(name="Total XP", value=f"{total_xp:,}", inline=True)
        embed.add_field(name=f"Progress → Level {level+1}", value=f"`{bar}` {pct}%\n{progress_xp:,}/{needed_xp:,} XP", inline=False)
        footer_parts = []
        if title: footer_parts.append(f"✨ {title}")
        if await is_xp_boosted(gid, uid): footer_parts.append("⚡ XP Boost active!")
        if footer_parts: embed.set_footer(text=" · ".join(footer_parts))
        await ctx.send(embed=embed)
