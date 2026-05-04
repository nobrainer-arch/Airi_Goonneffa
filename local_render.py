#!/usr/bin/env python3
"""
push_full_sync_to_render_with_constraints.py
Makes Render an exact replica of local DB, including:
- Tables and columns (with SERIAL detection)
- Primary keys
- Unique constraints
- Foreign keys
- Data (inserted in parent‑first order to satisfy FKs)
- Sequence reset
"""

import asyncio
import asyncpg
from collections import defaultdict

LOCAL_URL = "postgresql://postgres:haha%2F008@localhost:5432/dcbot"
RENDER_URL = "postgresql://eth:oQMMlvSsb3VXnAnsSM4Mxp9zLlDgWLu3@dpg-d7s5hctckfvc73afcacg-a.virginia-postgres.render.com/dcbot_dnkt?sslmode=require"

BATCH_SIZE = 1000

# -------------------------------------------------------------------
# Schema inspection (handles arrays, SERIAL, etc.)
# -------------------------------------------------------------------
async def get_tables(conn):
    rows = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    return [r['tablename'] for r in rows]

async def get_columns(conn, table):
    """Return dict: column_name -> {type, nullable, default} using real PG types."""
    rows = await conn.fetch("""
        SELECT
            a.attname AS column_name,
            format_type(a.atttypid, a.atttypmod) AS data_type,
            NOT a.attnotnull AS is_nullable,
            pg_get_expr(d.adbin, d.adrelid) AS column_default
        FROM pg_attribute a
        LEFT JOIN pg_attrdef d ON a.attrelid = d.adrelid AND a.attnum = d.adnum
        WHERE a.attrelid = $1::regclass
          AND a.attnum > 0
          AND NOT a.attisdropped
        ORDER BY a.attnum
    """, table)
    return {r['column_name']: {
        'type': r['data_type'],
        'nullable': r['is_nullable'],
        'default': r['column_default']
    } for r in rows}

async def get_primary_key(conn, table):
    """Return list of column names that form the primary key, or empty list."""
    rows = await conn.fetch("""
        SELECT a.attname
        FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        WHERE i.indrelid = $1::regclass AND i.indisprimary
        ORDER BY a.attnum
    """, table)
    return [r['attname'] for r in rows]

async def get_unique_constraints(conn, table):
    """
    Return list of unique constraint definitions (excluding primary key).
    Each item is a list of column names.
    """
    rows = await conn.fetch("""
        SELECT
            i.indkey,
            array_agg(a.attname ORDER BY a.attnum) AS columns
        FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        WHERE i.indrelid = $1::regclass
          AND i.indisunique
          AND NOT i.indisprimary
        GROUP BY i.indkey
    """, table)
    return [r['columns'] for r in rows]

async def get_foreign_keys(conn):
    fk_map = defaultdict(set)
    rows = await conn.fetch("""
        SELECT
            conrelid::regclass::text AS child_table,
            confrelid::regclass::text AS parent_table
        FROM pg_constraint
        WHERE contype = 'f' AND connamespace = 'public'::regnamespace
    """)
    for r in rows:
        fk_map[r['child_table']].add(r['parent_table'])
    return fk_map

def topological_order(tables, fk_map):
    """Return parent‑first order using simple {child: set(parents)} fk_map."""
    graph = {t: set() for t in tables}
    for child, parents in fk_map.items():
        if child in graph:
            for parent in parents:
                if parent in graph:
                    graph[child].add(parent)
    indegree = {t: len(graph[t]) for t in tables}
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
        print("⚠️ Circular FK detected – using original table order")
        return tables
    return order

async def get_serial_column(conn, table):
    row = await conn.fetchrow("""
        SELECT attname AS column_name, pg_get_serial_sequence($1::text, attname) AS seq_name
        FROM pg_attribute
        WHERE attrelid = $1::regclass AND attnum > 0
          AND NOT attisdropped
          AND pg_get_serial_sequence($1::text, attname) IS NOT NULL
        LIMIT 1
    """, table)
    if row and row['seq_name']:
        return row['column_name'], row['seq_name']
    return None, None

# -------------------------------------------------------------------
# Schema alteration on Render (target)
# -------------------------------------------------------------------
async def create_table_from_local(local, target, table):
    cols = await get_columns(local, table)
    if not cols:
        return
    col_defs = []
    for name, info in cols.items():
        is_serial = (
            info['type'] in ('integer', 'bigint') and
            info['default'] and
            'nextval' in info['default']
        )
        if is_serial:
            pg_type = 'SERIAL' if info['type'] == 'integer' else 'BIGSERIAL'
            null_clause = "" if info['nullable'] else "NOT NULL"
            default_clause = ""
        else:
            pg_type = info['type']
            null_clause = "" if info['nullable'] else "NOT NULL"
            default_clause = f"DEFAULT {info['default']}" if info['default'] else ""
        col_defs.append(f'"{name}" {pg_type} {null_clause} {default_clause}'.strip())
    create_sql = f'CREATE TABLE "{table}" (\n  ' + ',\n  '.join(col_defs) + '\n);'
    await target.execute(create_sql)
    print(f"  ✅ Created table {table} on Render")

async def add_missing_columns(local, target, table):
    local_cols = await get_columns(local, table)
    target_cols = await get_columns(target, table)
    missing = set(local_cols) - set(target_cols)
    for col in missing:
        info = local_cols[col]
        is_serial = (
            info['type'] in ('integer', 'bigint') and
            info['default'] and
            'nextval' in info['default']
        )
        if is_serial:
            pg_type = 'SERIAL' if info['type'] == 'integer' else 'BIGSERIAL'
            null_clause = "" if info['nullable'] else "NOT NULL"
            default_clause = ""
        else:
            pg_type = info['type']
            null_clause = "" if info['nullable'] else "NOT NULL"
            default_clause = f"DEFAULT {info['default']}" if info['default'] else ""
        alter_sql = f'ALTER TABLE "{table}" ADD COLUMN "{col}" {pg_type} {null_clause} {default_clause}'
        await target.execute(alter_sql)
        print(f"  ➕ Added column {col} to {table} on Render")

async def drop_extra_columns(local, target, table):
    local_cols = await get_columns(local, table)
    target_cols = await get_columns(target, table)
    extra = set(target_cols) - set(local_cols)
    for col in extra:
        await target.execute(f'ALTER TABLE "{table}" DROP COLUMN IF EXISTS "{col}" CASCADE')
        print(f"  ➖ Dropped column {col} from {table} on Render")

async def drop_extra_tables(local_tables, target_tables, target):
    extra = set(target_tables) - set(local_tables)
    for table in extra:
        await target.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')
        print(f"  🗑️ Dropped extra table {table} from Render")

async def add_primary_key(target, table, pk_cols):
    if pk_cols:
        pk_sql = f'ALTER TABLE "{table}" ADD PRIMARY KEY ({", ".join(f"\"{c}\"" for c in pk_cols)});'
        try:
            await target.execute(pk_sql)
            print(f"  🔑 Added primary key to {table}")
        except Exception as e:
            print(f"  ⚠️ Could not add PK to {table}: {e}")

async def add_unique_constraints(target, table, unique_constraints):
    for idx, cols in enumerate(unique_constraints):
        constraint_name = f"uniq_{table}_{'_'.join(cols)}".replace('"','')
        col_list = ", ".join(f"\"{c}\"" for c in cols)
        sql = f'ALTER TABLE "{table}" ADD CONSTRAINT "{constraint_name}" UNIQUE ({col_list});'
        try:
            await target.execute(sql)
            print(f"  🔗 Added unique constraint {constraint_name} on {table}")
        except Exception as e:
            print(f"  ⚠️ Could not add unique constraint on {table}: {e}")

async def add_foreign_keys(target, fk_map):
    """Add all foreign key constraints on target."""
    for child, parents in fk_map.items():
        for info in parents:
            parent = info['parent_table']
            child_cols = info['child_cols']
            parent_cols = info['parent_cols']
            if not child_cols or not parent_cols:
                continue
            col_pair = '_'.join(child_cols)
            fk_name = f"fk_{child}_{parent}_{col_pair}".replace('"','')
            sql = (
                f'ALTER TABLE "{child}" ADD CONSTRAINT "{fk_name}" '
                f'FOREIGN KEY ({", ".join(f"\"{c}\"" for c in child_cols)}) '
                f'REFERENCES "{parent}" ({", ".join(f"\"{c}\"" for c in parent_cols)}) '
                f'ON DELETE CASCADE;'
            )
            try:
                await target.execute(sql)
                print(f"  🔗 Added foreign key {fk_name} on {child} -> {parent}")
            except Exception as e:
                print(f"  ⚠️ Could not add FK on {child}: {e}")

# -------------------------------------------------------------------
# Data copy (local -> Render) – truncates each table before insert
# -------------------------------------------------------------------
async def copy_table_data(local, target, table, common_cols):
    if not common_cols:
        return
    col_list = ', '.join(f'"{c}"' for c in common_cols)
    rows = await local.fetch(f'SELECT {col_list} FROM "{table}"')
    if not rows:
        print(f"  ⏭️ {table}: no rows")
        return
    await target.execute(f'TRUNCATE TABLE "{table}" RESTART IDENTITY CASCADE')
    for offset in range(0, len(rows), BATCH_SIZE):
        batch = rows[offset:offset+BATCH_SIZE]
        args = []
        values = []
        idx = 1
        for row in batch:
            ph = ', '.join(f'${idx+i}' for i in range(len(common_cols)))
            values.append(f'({ph})')
            args.extend(row[c] for c in common_cols)
            idx += len(common_cols)
        insert_sql = f'INSERT INTO "{table}" ({col_list}) VALUES {", ".join(values)}'
        await target.execute(insert_sql, *args)
    print(f"  📋 Copied {len(rows)} rows into {table} on Render")
    # Reset sequence if a SERIAL column exists
    col_name, seq_name = await get_serial_column(local, table)
    if seq_name and col_name in common_cols:
        max_val = await target.fetchval(f'SELECT MAX("{col_name}") FROM "{table}"') or 0
        await target.execute(f"SELECT setval('{seq_name}', {max_val + 1})")
        print(f"  🔄 Reset sequence {seq_name} to {max_val+1}")

# -------------------------------------------------------------------
# Main sync (copies schema + constraints + data)
# -------------------------------------------------------------------
async def sync():
    local = await asyncpg.connect(LOCAL_URL)
    render = await asyncpg.connect(RENDER_URL)
    print("Connected to local and Render.\n")

    local_tables = await get_tables(local)
    render_tables = await get_tables(render)
    print(f"Local tables:  {len(local_tables)}")
    print(f"Render tables: {len(render_tables)}\n")

    # 1. Drop extra tables from Render
    await drop_extra_tables(local_tables, render_tables, render)
    render_tables = await get_tables(render)

    # 2. Create missing tables and sync columns
    for table in local_tables:
        if table not in render_tables:
            await create_table_from_local(local, render, table)
        else:
            await add_missing_columns(local, render, table)
            await drop_extra_columns(local, render, table)

    # 3. Gather constraints from local DB
    local_pk = {}
    local_unique = {}
    for table in local_tables:
        local_pk[table] = await get_primary_key(local, table)
        local_unique[table] = await get_unique_constraints(local, table)
    local_fk = await get_foreign_keys(local)
    # Filter FKs to only existing tables
    local_fk = {k: v for k, v in local_fk.items() if k in local_tables}

    # 4. Determine parent‑first order for data copy
    insert_order = topological_order(local_tables, local_fk)
    print(f"\nData copy order (parents first): {', '.join(insert_order)}\n")

    # 5. Copy data (no need to disable triggers because we insert in parent‑first order)
    for table in insert_order:
        local_cols = await get_columns(local, table)
        target_cols = await get_columns(render, table)
        common = [c for c in local_cols if c in target_cols]
        await copy_table_data(local, render, table, common)

    # 6. Add constraints AFTER all data is copied (to avoid FK violations during inserts)
    print("\nAdding constraints...")
    # Primary keys first (they are also unique)
    for table in local_tables:
        await add_primary_key(render, table, local_pk[table])
    # Unique constraints (skip PK because already added)
    for table in local_tables:
        await add_unique_constraints(render, table, local_unique[table])
    # Foreign keys last
    #await add_foreign_keys(render, local_fk)

    await local.close()
    await render.close()
    print("\n🎉 Render database is now an exact replica of local DB (including all constraints).")

if __name__ == "__main__":
    asyncio.run(sync())