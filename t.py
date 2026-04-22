
import asyncio
import config
from airi.bot import bot

async def main():
    try:
        await bot.start(config.AIRI_TOKEN)
    except Exception as e:
        print(f"CRASH: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())