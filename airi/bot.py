# airi/bot.py — Airi bot
import discord
from discord.ext import commands
import json
import asyncio
import re
import db
import config
import actio

intents = discord.Intents.default()
intents.message_content = True
intents.members         = True
intents.presences       = True

# ── Dynamic prefix — handles "!", "airi ", and "airi" (no space) ──
async def get_prefix(bot, message: discord.Message):
    # "!" prefix
    prefixes = ["!", "! "]
    # "airi " with a space — e.g. "airi balance"
    prefixes.append("airi ")
    # "airi" without space — e.g. "airibalance"
    # We'll handle this via a special on_message listener instead
    # so we don't break words like "airing" etc.
    return prefixes

bot = commands.Bot(
    command_prefix=get_prefix,
    intents=intents,
    help_command=None,
)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ Airi online as {bot.user} ({bot.user.id})")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    stripped = message.content.strip()
    lower    = stripped.lower()

    # "airi" alone → help
    if lower == "airi":
        ctx = await bot.get_context(message)
        cmd = bot.get_command("help")
        if cmd:
            await ctx.invoke(cmd)
        return

    # "airicmd" — no space prefix (e.g. "airibalance" → "balance")
    # Only match if it starts with "airi" followed by a known command name
    if lower.startswith("airi") and not lower.startswith("airi "):
        remainder = stripped[4:]  # strip "airi"
        if remainder and not remainder[0].isspace():
            # Rebuild message as "!<remainder>" and re-process
            fake_content = "!" + remainder
            original = message.content
            message.content = fake_content
            ctx = await bot.get_context(message)
            message.content = original  # restore
            if ctx.command:
                await bot.invoke(ctx)
                return

    # "airi cmd" — space prefix, e.g. "airi balance"
    if lower.startswith("airi "):
        remainder = stripped[5:]
        if remainder:
            fake_content = "!" + remainder
            original = message.content
            message.content = fake_content
            ctx = await bot.get_context(message)
            message.content = original  # restore
            if ctx.command:
                await bot.invoke(ctx)
                return

    # Track online streak
    try:
        await db.pool.execute("""
            INSERT INTO online_streaks (guild_id,user_id,last_active)
            VALUES ($1,$2,NOW())
            ON CONFLICT (guild_id,user_id) DO UPDATE SET last_active=NOW()
        """, message.guild.id, message.author.id)
    except Exception:
        pass

    await bot.process_commands(message)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send(
            embed=discord.Embed(description=f"❌ {error}", color=0xe74c3c),
            delete_after=8
        )
    elif isinstance(error, commands.CommandNotFound):
        pass
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send(
            embed=discord.Embed(description="❌ You don't have permission for that.", color=0xe74c3c),
            delete_after=8
        )
    elif isinstance(error, commands.BotMissingPermissions):
        await ctx.send(
            embed=discord.Embed(description="❌ I'm missing permissions to do that.", color=0xe74c3c),
            delete_after=8
        )
    else:
        print(f"Command error in {ctx.command}: {error}")

async def setup_hook():
    await db.init()

    # Load gifs.json
    try:
        with open("gifs.json") as f:
            gifs_data = json.load(f)
        from airi.gif_provider import load_gifs_pool
        load_gifs_pool(gifs_data)
        print(f"✅ Loaded {len(gifs_data)} GIF categories")
    except Exception as e:
        print(f"⚠️  gifs.json: {e}")

    # Build command metadata from actio.py
    from airi.commands import NSFW_COMMANDS, setup_commands
    cmd_meta = {}
    for cmd, data in actio.ACTIONS.items():
        cmd_meta[cmd] = {
            "is_nsfw":  cmd in NSFW_COMMANDS,
            "has_solo": "solo" in data,
            "desc":     f"{cmd.replace('_',' ').title()} action",
        }
    for cmd in actio.ACTIONS_SOLO:
        cmd_meta[cmd] = {
            "is_nsfw": cmd in NSFW_COMMANDS,
            "has_solo": True,
            "desc": f"{cmd} solo action",
        }

    # Load all cogs
    from airi.economy       import EconomyCog
    from airi.xp            import XPCog
    from airi.social        import SocialCog
    from airi.marketplace   import MarketplaceCog
    from airi.guild_config  import GuildConfigCog
    from airi.setup         import SetupCog
    from airi.help_ui       import HelpCog
    from airi.relationships import RelationshipCog
    from airi.jobs          import JobsCog
    from airi.gacha         import GachaCog
    from airi.business      import BusinessCog
    from airi.inventory     import InventoryCog
    from airi.auction_house import AuctionHouseCog
    from airi.audit_log     import AuditLogCog
    from airi.avatar        import AvatarCog
    from airi.orders        import OrdersCog
    from airi.afk           import AFKCog
    from airi.gender        import GenderCog
    from airi.kakera        import KakeraCog
    from airi.milestones    import MilestonesCog
    from airi.anime_chars   import AnimeCharsCog
    from airi.events        import EventsCog
    from airi.leaderboard   import LeaderboardCog
    from airi.ignore        import IgnoreCog
    from airi.banners       import BannersCog
    from airi.rpg           import RPGStatsCog, DungeonCog

    for cog_cls in [
        EconomyCog, XPCog, SocialCog, MarketplaceCog, GuildConfigCog,
        SetupCog, HelpCog, RelationshipCog, JobsCog, GachaCog,
        BusinessCog, InventoryCog, AuctionHouseCog, AuditLogCog,
        AvatarCog, OrdersCog, AFKCog, GenderCog, KakeraCog,
        MilestonesCog, AnimeCharsCog, EventsCog, LeaderboardCog,
        IgnoreCog, BannersCog, RPGStatsCog, DungeonCog,
    ]:
        await bot.add_cog(cog_cls(bot))

    setup_commands(bot, cmd_meta)
    print("✅ All cogs loaded")

bot.setup_hook = setup_hook
