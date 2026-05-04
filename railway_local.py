#!/usr/bin/env python3
"""
sync_local_to_railway.py – makes local DB an exact replica of Railway:
- drops extra local tables/columns
- adds missing tables/columns
- copies all data (respecting FKs)
- resets sequences
"""
import asyncio
import asyncpg
from collections import defaultdict

REMOTE_URL = "postgresql://postgres:qxlFNckhaebEuJwFCxBHNsJybvPcyfHX@roundhouse.proxy.rlwy.net:38374/railway"
LOCAL_URL  = "postgresql://postgres:haha%2F008@localhost:5432/dcbot"

BATCH_SIZE = 1000

# -------------------------------------------------------------------
# Schema inspection
# -------------------------------------------------------------------
async def get_tables(conn):
    rows = await conn.fetch("""
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public'
        ORDER BY tablename
    """)
    return [r['tablename'] for r in rows]

async def get_columns(conn, table):
    """Return dict: column_name -> {type, nullable, default}"""
    rows = await conn.fetch("""
        SELECT
            column_name,
            data_type,
            is_nullable,
            column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = $1
        ORDER BY ordinal_position
    """, table)
    return {r['column_name']: {
        'type': r['data_type'],
        'nullable': r['is_nullable'] == 'YES',
        'default': r['column_default']
    } for r in rows}

async def get_foreign_keys(conn):
    fk_map = defaultdict(set)
    rows = await conn.fetch("""
        SELECT
            conrelid::regclass::text AS child_table,
            confrelid::regclass::text AS parent_table
        FROM pg_constraint
        WHERE contype = 'f'
          AND connamespace = 'public'::regnamespace
    """)
    for r in rows:
        fk_map[r['child_table']].add(r['parent_table'])
    return fk_map

def topological_order(tables, fk_map):
    graph = {t: set() for t in tables}
    for child, parents in fk_map.items():
        if child in graph:
            for p in parents:
                if p in graph:
                    graph[child].add(p)
    indegree = {t: len(parents) for t, parents in graph.items()}
    queue = [t for t in tables if indegree[t] == 0]
    order = []
    while queue:
        node = queue.pop(0)
        order.append(node)
        for other, parents in graph.items():
            if node in parents:
                indegree[other] -= 1
                if indegree[other] == 0:
                    queue.append(other)
    if len(order) != len(tables):
        raise Exception("Circular FK dependency")
    return order

async def get_serial_column(conn, table):
    row = await conn.fetchrow("""
        SELECT
            a.attname AS column_name,
            pg_get_serial_sequence(a.attrelid::regclass::text, a.attname) AS seq_name
        FROM pg_attribute a
        WHERE a.attrelid = $1::regclass
          AND a.attnum > 0
          AND NOT a.attisdropped
          AND pg_get_serial_sequence(a.attrelid::regclass::text, a.attname) IS NOT NULL
        LIMIT 1
    """, table)
    if row and row['seq_name']:
        return row['column_name'], row['seq_name']
    return None, None

# -------------------------------------------------------------------
# Schema ALTER commands
# -------------------------------------------------------------------
async def create_table_from_remote(remote, local, table):
    """Create table on local with same columns as remote."""
    cols = await get_columns(remote, table)
    if not cols:
        return
    col_defs = []
    for name, info in cols.items():
        null_clause = "" if info['nullable'] else "NOT NULL"
        default_clause = f"DEFAULT {info['default']}" if info['default'] else ""
        col_defs.append(f'"{name}" {info["type"]} {null_clause} {default_clause}'.strip())
    create_sql = f'CREATE TABLE "{table}" (\n  ' + ',\n  '.join(col_defs) + '\n);'
    await local.execute(create_sql)
    print(f"  ✅ Created table {table}")

async def add_missing_columns(remote, local, table):
    """Add to local any columns that exist in remote but not in local."""
    remote_cols = await get_columns(remote, table)
    local_cols = await get_columns(local, table)
    missing = set(remote_cols) - set(local_cols)
    for col in missing:
        info = remote_cols[col]
        null_clause = "" if info['nullable'] else "NOT NULL"
        default_clause = f"DEFAULT {info['default']}" if info['default'] else ""
        alter_sql = f'ALTER TABLE "{table}" ADD COLUMN "{col}" {info["type"]} {null_clause} {default_clause}'
        await local.execute(alter_sql)
        print(f"  ➕ Added column {col} to {table}")

async def drop_extra_columns(remote, local, table):
    """Drop from local any columns that don't exist in remote."""
    remote_cols = await get_columns(remote, table)
    local_cols = await get_columns(local, table)
    extra = set(local_cols) - set(remote_cols)
    for col in extra:
        await local.execute(f'ALTER TABLE "{table}" DROP COLUMN IF EXISTS "{col}" CASCADE')
        print(f"  ➖ Dropped column {col} from {table}")

async def drop_extra_tables(remote, local, remote_tables, local_tables):
    extra = set(local_tables) - set(remote_tables)
    # Drop in reverse topological order? Safer: just drop CASCADE
    for table in extra:
        await local.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')
        print(f"  🗑️ Dropped extra table {table}")

# -------------------------------------------------------------------
# Data copy
# -------------------------------------------------------------------
async def copy_table_data(remote, local, table, insert_order):
    """Copy all rows from remote to local for a given table."""
    # Get common columns (both DBs now have same schema after sync)
    remote_cols = await get_columns(remote, table)
    local_cols = await get_columns(local, table)
    common_cols = [c for c in remote_cols if c in local_cols]
    if not common_cols:
        return
    col_list = ', '.join(f'"{c}"' for c in common_cols)
    # Fetch from remote
    rows = await remote.fetch(f'SELECT {col_list} FROM "{table}"')
    if not rows:
        return
    # Clear local table (data will be replaced)
    await local.execute(f'TRUNCATE TABLE "{table}" RESTART IDENTITY CASCADE')
    # Batch insert
    for offset in range(0, len(rows), BATCH_SIZE):
        batch = rows[offset:offset + BATCH_SIZE]
        args = []
        values_parts = []
        idx = 1
        for row in batch:
            ph = ', '.join(f'${idx + i}' for i in range(len(common_cols)))
            values_parts.append(f'({ph})')
            args.extend([row[c] for c in common_cols])
            idx += len(common_cols)
        insert_sql = f'INSERT INTO "{table}" ({col_list}) VALUES {", ".join(values_parts)}'
        await local.execute(insert_sql, *args)
    print(f"  📋 Copied {len(rows)} rows into {table}")
    # Reset sequence if any serial column exists in common columns
    col_name, seq_name = await get_serial_column(remote, table)
    if seq_name and col_name in common_cols:
        max_val = await local.fetchval(f'SELECT MAX("{col_name}") FROM "{table}"') or 0
        await local.execute(f"SELECT setval('{seq_name}', {max_val+1})")

# -------------------------------------------------------------------
# Main sync
# -------------------------------------------------------------------
async def sync():
    remote = await asyncpg.connect(REMOTE_URL)
    local  = await asyncpg.connect(LOCAL_URL)
    print("Connected to both DBs.\n")

    # 1. Get table lists
    remote_tables = await get_tables(remote)
    local_tables  = await get_tables(local)
    print(f"Remote tables ({len(remote_tables)}): {', '.join(remote_tables)}")
    print(f"Local tables  ({len(local_tables)}): {', '.join(local_tables)}\n")

    # 2. Drop extra tables from local
    await drop_extra_tables(remote, local, remote_tables, local_tables)

    # 3. Create missing tables (and add columns to existing ones)
    for table in remote_tables:
        if table not in local_tables:
            await create_table_from_remote(remote, local, table)
        else:
            await add_missing_columns(remote, local, table)
            await drop_extra_columns(remote, local, table)

    # 4. Re-fetch local tables (schema now matches)
    local_tables = await get_tables(local)

    # 5. Get FK order for copying data (parent first)
    fk_map = await get_foreign_keys(remote)
    fk_map = {k: {p for p in v if p in remote_tables} for k, v in fk_map.items() if k in remote_tables}
    insert_order = topological_order(remote_tables, fk_map)
    print(f"\nData copy order: {', '.join(insert_order)}\n")

    # 6. Disable triggers on local for clean copy
    await local.execute("SET session_replication_role = replica;")
    try:
        for table in insert_order:
            await copy_table_data(remote, local, table, insert_order)
    finally:
        await local.execute("SET session_replication_role = DEFAULT;")
        print("\n✅ Triggers re-enabled.")

    await remote.close()
    await local.close()
    print("\n🎉 Local database is now an exact replica of Railway (schema + data).")

if __name__ == "__main__":
    asyncio.run(sync())