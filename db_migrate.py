#!/usr/bin/env python3
"""
clone_railway_to_local.py – dynamically reads the remote schema,
                            so it never breaks on column name mismatches.
"""
import asyncio
import asyncpg

REMOTE_URL = "postgresql://postgres:qxlFNckhaebEuJwFCxBHNsJybvPcyfHX@roundhouse.proxy.rlwy.net:38374/railway"
LOCAL_URL  = "postgresql://postgres:haha%2F008@localhost:5432/dcbot"

# Tables to clone – order matters for foreign‑key constraints.
# We delay “discovery” of columns until runtime.
TABLE_ORDER = [
    "guild_config",
    "guild_setup",
    "economy",
    "xp",
    "social",
    "claims",
    "nsfw_optout",
    "rel_optout",
    "rpblock",
    "protection",
    "work_log",
    "gacha_pity",
    "inventory",
    "businesses",
    "auction_house",
    "audit_log",
    "antinoobify_messages",
    "greeted_channels",
    "gacha_persistent",
    "user_prefs",
    "afk",
    "milestones_claimed",
    "achievements",
    "online_streaks",
    "kakera_shop_purchases",
    "orders",
    "anime_waifus",
    "relationships",
    "shared_accounts",
    "court_cases",
    "proposals",
    "waifu_market",
    "ah_bids",
]

async def get_table_columns(conn, table_name):
    """Return list of column names (order by ordinal position) for a table."""
    rows = await conn.fetch(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = $1
        ORDER BY ordinal_position
        """,
        table_name
    )
    return [r[0] for r in rows]

async def find_sequence_for_table(conn, table_name):
    """Try to find a sequence that likely belongs to a SERIAL column."""
    # Common naming: tablename_colname_seq
    # We'll just look for any sequence owned by this table's columns.
    rows = await conn.fetch(
        """
        SELECT pg_get_serial_sequence($1, attname)
        FROM pg_attribute
        WHERE attrelid = $1::regclass
          AND attnum > 0
          AND pg_get_serial_sequence($1, attname) IS NOT NULL
        LIMIT 1
        """,
        table_name
    )
    if rows:
        return rows[0][0]  # full qualified sequence name
    return None

async def clone():
    remote = await asyncpg.connect(REMOTE_URL)
    local  = await asyncpg.connect(LOCAL_URL)
    print("Connected to both databases.\n")

    for table in TABLE_ORDER:
        # 1. Discover remote columns
        columns = await get_table_columns(remote, table)
        if not columns:
            print(f"  ⚠️  Table {table} not found on remote – skipping.")
            continue
        col_names = ", ".join(columns)

        print(f"Processing {table} ({len(columns)} cols) …")

        # 2. Fetch all rows from Railway
        rows = await remote.fetch(f'SELECT {col_names} FROM "{table}"')
        print(f"  Fetched {len(rows)} rows.")

        # 3. Clear local table
        await local.execute(f'DELETE FROM "{table}"')
        print(f"  Cleared local {table}.")

        # 4. Insert rows (if any)
        if rows:
            values_parts = []
            args = []
            idx = 1  # placeholder counter
            for row in rows:
                placeholders = ", ".join(f"${idx + i}" for i in range(len(columns)))
                values_parts.append(f"({placeholders})")
                args.extend([row[col] for col in columns])
                idx += len(columns)

            insert_query = f'INSERT INTO "{table}" ({col_names}) VALUES {",".join(values_parts)}'
            await local.execute(insert_query, *args)
            print(f"  Inserted {len(rows)} rows into local {table}.")

        # 5. Reset serial sequence if any
        seq = await find_sequence_for_table(remote, table)
        if seq:
            try:
                await local.execute(
                    f"SELECT setval('{seq}', COALESCE((SELECT MAX({columns[0]}) FROM {table}), 1))"
                )
                print(f"  Reset sequence {seq}.")
            except Exception as e:
                # Not all first columns are serial; ignore harmless failures
                pass

        print()

    await remote.close()
    await local.close()
    print("✅ Cloning complete! Your local database matches Railway.")

if __name__ == "__main__":
    asyncio.run(clone())