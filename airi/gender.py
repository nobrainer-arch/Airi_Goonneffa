# airi/gender.py
# Gender preferences backed by PostgreSQL.
# In-memory cache still used so we don't hit the DB on every GIF command.

import discord
from discord.ext import commands
import db
from utils import _err, C_INFO

_cache: dict[str, str | None] = {}


async def get_gender(user_id: str) -> str | None:
    if user_id in _cache:
        return _cache[user_id]
    row = await db.pool.fetchrow(
        "SELECT gender FROM user_prefs WHERE user_id = $1",
        int(user_id),
    )
    value = row["gender"].strip() if row else None
    _cache[user_id] = value
    return value


async def set_gender(user_id: str, gender: str) -> None:
    _cache[user_id] = gender
    await db.pool.execute("""
        INSERT INTO user_prefs (user_id, gender)
        VALUES ($1, $2)
        ON CONFLICT (user_id) DO UPDATE SET gender = EXCLUDED.gender
    """, int(user_id), gender)


async def reset_gender(user_id: str) -> None:
    _cache.pop(user_id, None)
    await db.pool.execute(
        "DELETE FROM user_prefs WHERE user_id = $1", int(user_id)
    )


async def load_prefs() -> None:
    """Warm the in-memory cache from the DB at startup."""
    rows = await db.pool.fetch("SELECT user_id, gender FROM user_prefs")
    for row in rows:
        _cache[str(row["user_id"])] = row["gender"].strip()
    print(f"✅ Loaded {len(rows)} gender prefs from DB.")

class GenderCog(commands.Cog, name="Gender"):
    def __init__(self, bot): self.bot = bot

    @commands.hybrid_command(name="gender", description="Set your gender for GIF text targeting")
    async def gender(self, ctx, gender: str = None):
        """Set your gender: m (male), f (female), nb (non-binary), or u (unspecified/neutral)."""
        if gender is None:
            current = await get_gender(str(ctx.author.id)) or "not set"
            await ctx.send(embed=discord.Embed(
                description=f"Your current gender: **{current}**\nUse `!gender m/f/nb/u` to set.",
                color=C_INFO
            ))
            return
        gender = gender.lower()
        if gender not in ("m", "f", "nb", "u"):
            return await _err(ctx, "Gender must be `m`, `f`, `nb`, or `u`.")
        await set_gender(str(ctx.author.id), gender)
        await ctx.send(embed=discord.Embed(
            description=f"✅ Gender set to **{gender}** (m=male, f=female, nb=non-binary, u=unspecified/neutral).",
            color=C_INFO
        ))
