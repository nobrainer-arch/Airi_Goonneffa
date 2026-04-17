import asyncio
import db
import config

async def main():
    await db.init()
    print("Database initialized!")

if __name__ == "__main__":
    asyncio.run(main())