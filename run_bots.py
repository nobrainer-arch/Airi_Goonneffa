from airi.bot import bot as airi_bot
from goonneffa.bot import bot as goon_bot
import config
import asyncio

async def main():
    await asyncio.gather(
        airi_bot.start(config.AIRI_TOKEN),
        goon_bot.start(config.GOON_TOKEN)
    )

if __name__ == "__main__":
    asyncio.run(main())
