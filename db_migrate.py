#!/usr/bin/env python3
"""
clone_railway_to_local.py — corrected placeholder numbering
"""
import asyncio
import asyncpg

REMOTE_URL = "postgresql://postgres:qxlFNckhaebEuJwFCxBHNsJybvPcyfHX@roundhouse.proxy.rlwy.net:38374/railway"
LOCAL_URL  = "postgresql://postgres:haha%2F008@localhost:5432/dcbot"

TABLES = [
    ("guild_config",           ["guild_id", "key", "value"], None),
    ("guild_setup",            ["guild_id", "setup_done", "setup_by", "setup_at"], None),
    ("economy",                ["guild_id", "user_id", "balance", "last_daily", "streak",
                                "titles", "active_title", "xp_boost_until", "daily_boost",
                                "kakera", "last_daily_reminder"], None),
    ("xp",                     ["guild_id", "user_id", "xp", "level"], None),
    ("social",                 ["guild_id", "user_id", "rep", "last_rep_given",
                                "hugs_received", "kisses_received", "pats_received"], None),
    ("claims",                 ["guild_id", "claimer_id", "claimed_id", "claimed_at"], None),
    ("nsfw_optout",            ["guild_id", "user_id"], None),
    ("rel_optout",             ["guild_id", "user_id"], None),
    ("rpblock",                ["guild_id", "user_id", "blocked_id"], None),
    ("protection",             ["guild_id", "user_id", "expires_at"], None),
    ("work_log",               ["guild_id", "user_id", "last_work", "last_crime"], None),
    ("gacha_pity",             ["guild_id", "user_id", "pulls"], None),
    ("inventory",              ["guild_id", "user_id", "item_key", "quantity"], None),
    ("businesses",             ["id", "guild_id", "owner_id", "manager_id", "type", "name",
                                "level", "balance", "last_collect", "status", "created_at"], "id"),
    ("auction_house",          ["id", "guild_id", "seller_id", "item_key", "item_name",
                                "rarity", "quantity", "price", "status", "listed_at",
                                "expires_at", "min_bid", "current_bid", "current_bidder_id",
                                "listing_message_id", "listing_channel_id"], "id"),
    ("audit_log",              ["id", "guild_id", "user_id", "action", "detail", "amount",
                                "balance_before", "balance_after", "created_at"], "id"),
    ("antinoobify_messages",   ["message_id", "guild_id", "channel_id"], None),
    ("greeted_channels",       ["channel_id"], None),
    ("gacha_persistent",       ["guild_id", "channel_id", "message_id"], None),
    ("user_prefs",             ["user_id", "gender"], None),
    ("afk",                    ["guild_id", "user_id", "reason", "set_at"], None),
    ("milestones_claimed",     ["guild_id", "user_id", "milestone", "claimed_at"], None),
    ("achievements",           ["guild_id", "user_id", "achievement", "progress",
                                "completed", "completed_at"], None),
    ("online_streaks",         ["guild_id", "user_id", "current_streak_hours",
                                "best_streak_hours", "last_active"], None),
    ("kakera_shop_purchases",  ["guild_id", "user_id", "item", "purchased_at"], None),
    ("orders",                 ["id", "guild_id", "buyer_id", "item_key", "item_name",
                                "max_price", "quantity", "status", "created_at",
                                "filled_by"], "id"),
    ("anime_waifus",           ["id", "guild_id", "owner_id", "char_name", "char_image",
                                "rarity", "obtained_at"], "id"),
    ("relationships",          ["id", "guild_id", "user1_id", "user2_id", "type",
                                "started_at", "proposer_id", "dowry_paid", "prenup",
                                "status", "cheating_strikes"], "id"),
    ("shared_accounts",        ["relationship_id", "guild_id", "balance"], None),
    ("court_cases",            ["id", "guild_id", "relationship_id", "filer_id",
                                "defendant_id", "judge_id", "reason", "status",
                                "created_at", "message_id"], "id"),
    ("proposals",              ["id", "guild_id", "proposer_id", "target_id", "type",
                                "dowry", "message_id", "channel_id", "status",
                                "created_at", "expires_at", "min_bid", "current_bid",
                                "current_bidder_id", "listing_message_id", "listing_channel_id"], "id"),
    ("waifu_market",           ["id", "guild_id", "seller_id", "waifu_id", "min_bid",
                                "buyout_price", "current_bid", "current_bidder",
                                "status", "listed_at", "channel_id", "message_id"], "id"),
    ("ah_bids",                ["id", "listing_id", "guild_id", "bidder_id", "amount",
                                "placed_at"], "id"),
]

async def clone():
    remote = await asyncpg.connect(REMOTE_URL)
    local  = await asyncpg.connect(LOCAL_URL)
    print("Connected to both databases.\n")

    for table, columns, seq_col in TABLES:
        col_names = ", ".join(columns)
        print(f"Processing {table} ...")

        # 1. Fetch all rows from Railway
        rows = await remote.fetch(f"SELECT {col_names} FROM {table}")
        print(f"  Fetched {len(rows)} rows from Railway.")

        # 2. Wipe local table
        await local.execute(f"DELETE FROM {table}")
        print(f"  Cleared local {table}.")

        # 3. Insert rows if any
        if rows:
            values_parts = []
            args = []
            idx = 1
            for row in rows:
                # Build correctly numbered placeholders for this row
                placeholders = ", ".join(f"${idx + i}" for i in range(len(columns)))
                values_parts.append(f"({placeholders})")
                args.extend([row[col] for col in columns])
                idx += len(columns)

            insert_query = f"INSERT INTO {table} ({col_names}) VALUES {','.join(values_parts)}"
            await local.execute(insert_query, *args)
            print(f"  Inserted {len(rows)} rows into local {table}.")

        # 4. Reset the sequence if table has a SERIAL column
        if seq_col:
            seq_name = f"{table}_{seq_col}_seq"
            try:
                await local.execute(
                    f"SELECT setval('{seq_name}', COALESCE((SELECT MAX({seq_col}) FROM {table}), 1))"
                )
                print(f"  Reset sequence {seq_name}.")
            except Exception as e:
                print(f"  Could not reset sequence {seq_name}: {e}")

        print()

    await remote.close()
    await local.close()
    print("✅ Migration complete! Local DB now mirrors Railway.")

if __name__ == "__main__":
    asyncio.run(clone())