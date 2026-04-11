# airi/leaderboard.py
import discord
from datetime import datetime
import db

# Category definitions: (display name, query builder function)
# Each function returns (rows, value_key) for that guild


async def _lb_xp(guild_id: int):
    rows = await db.pool.fetch(
        "SELECT user_id, xp FROM xp WHERE guild_id=$1 ORDER BY xp DESC LIMIT 10",
        guild_id
    )
    return rows, "xp"

async def _lb_coins(guild_id: int):
    rows = await db.pool.fetch(
        "SELECT user_id, balance FROM economy WHERE guild_id=$1 ORDER BY balance DESC LIMIT 10",
        guild_id
    )
    return rows, "balance"

async def _lb_rep(guild_id: int):
    rows = await db.pool.fetch(
        "SELECT user_id, rep FROM social WHERE guild_id=$1 ORDER BY rep DESC LIMIT 10",
        guild_id
    )
    return rows, "rep"

async def _lb_hugs(guild_id: int):
    rows = await db.pool.fetch(
        "SELECT user_id, hugs_received FROM social WHERE guild_id=$1 ORDER BY hugs_received DESC LIMIT 10",
        guild_id
    )
    return rows, "hugs_received"

async def _lb_kisses(guild_id: int):
    rows = await db.pool.fetch(
        "SELECT user_id, kisses_received FROM social WHERE guild_id=$1 ORDER BY kisses_received DESC LIMIT 10",
        guild_id
    )
    return rows, "kisses_received"

async def _lb_pats(guild_id: int):
    rows = await db.pool.fetch(
        "SELECT user_id, pats_received FROM social WHERE guild_id=$1 ORDER BY pats_received DESC LIMIT 10",
        guild_id
    )
    return rows, "pats_received"

async def _lb_marriage_duration(guild_id: int):
    # Get active marriages, compute days since started_at, order by longest
    rows = await db.pool.fetch("""
        SELECT user1_id, user2_id, started_at,
               EXTRACT(EPOCH FROM (NOW() - started_at)) / 86400 AS days
        FROM relationships
        WHERE guild_id=$1 AND type='married' AND status='active'
        ORDER BY days DESC LIMIT 10
    """, guild_id)
    # Transform into list of dicts with user_id and days (we'll show both partners)
    # But leaderboard expects user_id per row. We'll create two entries per marriage,
    # but that would double count. Better: show top 10 individuals by marriage duration
    # So we'll list each user with their own marriage duration.
    individual = []
    for row in rows:
        days = int(row["days"])
        individual.append({"user_id": row["user1_id"], "days": days})
        individual.append({"user_id": row["user2_id"], "days": days})
    # Sort by days descending, take top 10 unique users (if same user appears twice, keep highest)
    unique = {}
    for item in individual:
        uid = item["user_id"]
        if uid not in unique or item["days"] > unique[uid]:
            unique[uid] = item["days"]
    sorted_users = sorted(unique.items(), key=lambda x: x[1], reverse=True)[:10]
    # Convert to list of dicts with 'user_id' and 'days'
    result = [{"user_id": uid, "days": days} for uid, days in sorted_users]
    return result, "days"

async def _lb_waifu_score(guild_id: int):
    # Score: mythic=100, legendary=20, epic=5, rare=2, common=1
    rows = await db.pool.fetch("""
        SELECT owner_id AS user_id,
               SUM(CASE rarity
                   WHEN 'mythic' THEN 100
                   WHEN 'legendary' THEN 20
                   WHEN 'epic' THEN 5
                   WHEN 'rare' THEN 2
                   ELSE 1 END) AS score
        FROM anime_waifus
        WHERE guild_id=$1
        GROUP BY owner_id
        ORDER BY score DESC LIMIT 10
    """, guild_id)
    return rows, "score"

async def _lb_proposals(guild_id: int):
    # Use proposals_made column from economy (incremented on successful proposals)
    rows = await db.pool.fetch("""
        SELECT user_id, proposals_made
        FROM economy
        WHERE guild_id=$1 AND proposals_made > 0
        ORDER BY proposals_made DESC LIMIT 10
    """, guild_id)
    return rows, "proposals_made"

LB_CATEGORIES = {
    "xp":        ("📈 XP", _lb_xp),
    "coins":     ("💰 Coins", _lb_coins),
    "rep":       ("⭐ Reputation", _lb_rep),
    "hugs":      ("🤗 Hugs Received", _lb_hugs),
    "kisses":    ("💋 Kisses Received", _lb_kisses),
    "pats":      ("🤚 Pats Received", _lb_pats),
    "marriage":  ("💍 Marriage Duration", _lb_marriage_duration),
    "waifuscore":("👑 Waifu Score", _lb_waifu_score),
    "proposals": ("💌 Proposals Made", _lb_proposals),
}

async def _build_lb(guild: discord.Guild, category: str) -> discord.Embed:
    """Build a leaderboard embed for the given category."""
    gid = guild.id
    if category not in LB_CATEGORIES:
        category = "xp"
    title, query_func = LB_CATEGORIES[category]

    rows, value_key = await query_func(gid)

    embed = discord.Embed(
        title=f"🏆 {title} Leaderboard — {guild.name}",
        color=0xf1c40f,
        timestamp=datetime.utcnow()
    )
    if not rows:
        embed.description = "No data available for this category."
        return embed

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, row in enumerate(rows):
        user_id = row["user_id"]
        member = guild.get_member(user_id)
        name = member.display_name if member else f"<@{user_id}>"
        value = row[value_key]
        # Format value nicely
        if category == "marriage":
            val_str = f"{int(value)} days"
        elif category == "waifuscore":
            val_str = f"{int(value):,} pts"
        else:
            val_str = f"{int(value):,}"
        medal = medals[i] if i < 3 else f"`{i+1}`"
        lines.append(f"{medal} **{name}** — {val_str}")
    embed.description = "\n".join(lines)
    embed.set_footer(text="Use dropdown to change category")
    return embed


from discord.ext import commands

class LeaderboardCog(commands.Cog, name="Leaderboard"):
    """Placeholder cog for leaderboard functionality.
    The actual leaderboard command is provided by SocialCog to avoid duplication.
    This cog exists only for modular loading in bot.py.
    """
    def __init__(self, bot):
        self.bot = bot