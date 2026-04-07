# goonneffa/bot.py
import discord
from discord.ext import commands
import config
import db

TOKEN = config.GOON_TOKEN

intents = discord.Intents.default()
intents.message_content = True
intents.members         = True


class GoonBot(commands.Bot):
    async def setup_hook(self):
        # DB pool is already initialised by AiriBot.setup_hook since both bots
        # share the same process. If goonneffa ever runs standalone, init here.
        if db.pool is None:
            await db.init()

        from goonneffa.moderation import ModerationCog
        from goonneffa.commands   import CommandsCog

        await self.add_cog(ModerationCog(self))
        await self.add_cog(CommandsCog(self))
        print("✅ Goonneffa cogs loaded.")

    async def on_ready(self):
        print(f"Goonneffa online as {self.user}")


bot = GoonBot(command_prefix="!", intents=intents)
