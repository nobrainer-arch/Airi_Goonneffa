# airi/bot.py
import discord
from discord.ext import commands
import asyncio
import config
import db
from utils import load_json, data_path
from .commands import setup_commands
from .gender import load_prefs
from .gif_provider import load_gifs_pool

TOKEN     = config.AIRI_TOKEN
GIFS_FILE = data_path("gifs.json")

# Load gifs.json — NSFW pool + any SFW entries
gifs = load_json(GIFS_FILE, {})
# commands_data kept for actio-based fallback lookups
commands_data: dict = {}
for key, urls in gifs.items():
    if key.endswith("_male"):
        base = key[:-5]; commands_data.setdefault(base, {}).setdefault("male", []).extend(urls)
    elif key.endswith("_female"):
        base = key[:-7]; commands_data.setdefault(base, {}).setdefault("female", []).extend(urls)
    else:
        commands_data.setdefault(key, {}).setdefault("neutral", []).extend(urls)
for base, data in commands_data.items():
    if "neutral" not in data:
        data["neutral"] = data.get("male", []) + data.get("female", [])

intents = discord.Intents.default()
intents.message_content = True
intents.members = True


class AiriBot(commands.Bot):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.remove_command("help")  # Remove built-in help to use our custom one

    async def on_message(self, message):
        # Suppress default — EventsCog.on_message is the dispatcher
        pass

    async def setup_hook(self):
        await db.init()

        # Populate gif pools from gifs.json (must happen AFTER db init)
        load_gifs_pool(gifs)

        from airi.events        import EventsCog
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
        from airi.afk           import AFKCog
        from airi.avatar        import AvatarCog
        from airi.orders        import OrdersCog
        from airi.anime_chars   import AnimeCharsCog
        from airi.kakera        import KakeraCog
        from airi.milestones    import MilestonesCog
        from airi.banners       import BannersCog

        await self.add_cog(EventsCog(self, commands_data))
        await self.add_cog(AFKCog(self))
        await self.add_cog(EconomyCog(self))
        await self.add_cog(XPCog(self))
        await self.add_cog(SocialCog(self))
        await self.add_cog(MarketplaceCog(self))
        await self.add_cog(GuildConfigCog(self))
        await self.add_cog(SetupCog(self))
        await self.add_cog(HelpCog(self))
        await self.add_cog(RelationshipCog(self))
        await self.add_cog(JobsCog(self))
        await self.add_cog(GachaCog(self))
        await self.add_cog(BusinessCog(self))
        await self.add_cog(InventoryCog(self))
        await self.add_cog(AuctionHouseCog(self))
        await self.add_cog(AuditLogCog(self))
        await self.add_cog(AvatarCog(self))
        await self.add_cog(OrdersCog(self))
        await self.add_cog(AnimeCharsCog(self))
        await self.add_cog(KakeraCog(self))
        await self.add_cog(MilestonesCog(self))
        await self.add_cog(BannersCog(self))

        await load_prefs()
        setup_commands(self, commands_data)

        # Restore persistent AH listing views on restart
        ah_cog = self.cogs.get("AuctionHouse")
        if ah_cog:
            asyncio.create_task(ah_cog.restore_views())

        self.loop.create_task(self._cleanup_task())
        print("✅ Airi fully loaded.")

    async def _cleanup_task(self):
        await self.wait_until_ready()
        tick = 0
        while not self.is_closed():
            try:
                await db.pool.execute("UPDATE proposals SET status='expired' WHERE status='pending' AND expires_at < NOW()")
                await db.pool.execute("DELETE FROM protection WHERE expires_at < NOW()")
                ah_cog = self.cogs.get("AuctionHouse")
                if ah_cog: await ah_cog.expire_listings()
                tick += 1
                if tick >= 144:  # ~24h
                    tick = 0
                    guilds = await db.pool.fetch("SELECT DISTINCT guild_id FROM audit_log")
                    from airi.audit_log import prune_old
                    for g in guilds:
                        await prune_old(g["guild_id"])
            except Exception as e:
                print(f"Cleanup error: {e}")
            await asyncio.sleep(600)


bot = AiriBot(command_prefix="!", intents=intents)

if __name__ == "__main__":
    bot.run(TOKEN)
