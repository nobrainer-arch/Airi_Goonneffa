import discord
from discord.ext import commands
import db, config

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix=["g!", "goonneffa "], intents=intents, help_command=None)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ Goonneffa online as {bot.user}")

async def setup_hook():
    from goonneffa.commands import GoonneffaCog
    from goonneffa.moderation import ModerationCog
    await bot.add_cog(GoonneffaCog(bot))
    await bot.add_cog(ModerationCog(bot))

bot.setup_hook = setup_hook
