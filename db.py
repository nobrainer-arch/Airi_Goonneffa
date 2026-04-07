# db.py
import asyncpg
import os
import asyncio

pool: asyncpg.Pool | None = None
_initialized = False  # singleton guard — prevents double init when both bots share the process

async def init():
    global pool, _initialized
    if _initialized:
        return
    _initialized = True

    for attempt in range(5):
        try:
            pool = await asyncpg.create_pool(
                dsn=os.getenv("DATABASE_URL"),
                min_size=1,   # reduced from 2 — saves RAM on Railway
                max_size=8,   # reduced from 10 — stays under Railway's 20-conn limit
                statement_cache_size=0,  # required for PgBouncer on Railway
                command_timeout=30,
            )
            break
        except Exception as e:
            if attempt == 4:
                raise
            print(f"DB connect attempt {attempt+1} failed: {e}. Retrying in 3s...")
            await asyncio.sleep(3)

    await _create_tables()
    await _create_indexes()   # MUST be outside transaction block
    print("✅ Database connected and tables ready.")


async def _create_tables():
    # All CREATE TABLE runs inside a single implicit transaction — safe
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS economy (
                guild_id        BIGINT    NOT NULL,
                user_id         BIGINT    NOT NULL,
                balance         INTEGER   NOT NULL DEFAULT 0,
                last_daily      TIMESTAMP,
                streak          INTEGER   NOT NULL DEFAULT 0,
                titles          TEXT[]    NOT NULL DEFAULT '{}',
                active_title    TEXT,
                xp_boost_until  TIMESTAMP,
                daily_boost     BOOLEAN   NOT NULL DEFAULT FALSE,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS xp (
                guild_id BIGINT  NOT NULL,
                user_id  BIGINT  NOT NULL,
                xp       INTEGER NOT NULL DEFAULT 0,
                level    INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS social (
                guild_id       BIGINT NOT NULL,
                user_id        BIGINT NOT NULL,
                rep            INTEGER   NOT NULL DEFAULT 0,
                last_rep_given TIMESTAMP,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS claims (
                guild_id   BIGINT    NOT NULL,
                claimer_id BIGINT    NOT NULL,
                claimed_id BIGINT    NOT NULL,
                claimed_at TIMESTAMP NOT NULL DEFAULT NOW(),
                PRIMARY KEY (guild_id, claimer_id, claimed_id)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS claims_exclusive
                ON claims (guild_id, claimed_id);

            CREATE TABLE IF NOT EXISTS user_prefs (
                user_id BIGINT PRIMARY KEY,
                gender  CHAR(1)
            );

            CREATE TABLE IF NOT EXISTS nsfw_optout (
                guild_id BIGINT NOT NULL,
                user_id  BIGINT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS rel_optout (
                guild_id BIGINT NOT NULL,
                user_id  BIGINT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS protection (
                guild_id   BIGINT    NOT NULL,
                user_id    BIGINT    NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS relationships (
                id          SERIAL    PRIMARY KEY,
                guild_id    BIGINT    NOT NULL,
                user1_id    BIGINT    NOT NULL,
                user2_id    BIGINT    NOT NULL,
                type        TEXT      NOT NULL,
                started_at  TIMESTAMP NOT NULL DEFAULT NOW(),
                proposer_id BIGINT,
                dowry_paid  INTEGER   NOT NULL DEFAULT 0,
                prenup      BOOLEAN   NOT NULL DEFAULT FALSE,
                status      TEXT      NOT NULL DEFAULT 'active'
            );

            CREATE UNIQUE INDEX IF NOT EXISTS rel_active_pair
                ON relationships (guild_id, LEAST(user1_id,user2_id), GREATEST(user1_id,user2_id))
                WHERE status = 'active';

            CREATE TABLE IF NOT EXISTS proposals (
                id          SERIAL    PRIMARY KEY,
                guild_id    BIGINT    NOT NULL,
                proposer_id BIGINT    NOT NULL,
                target_id   BIGINT    NOT NULL,
                type        TEXT      NOT NULL,
                dowry       INTEGER   NOT NULL DEFAULT 0,
                message_id  BIGINT,
                channel_id  BIGINT,
                status      TEXT      NOT NULL DEFAULT 'pending',
                created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
                expires_at  TIMESTAMP,
                min_bid     INTEGER,
                current_bid INTEGER,
                current_bidder_id BIGINT,
                listing_message_id BIGINT,
                listing_channel_id BIGINT
            );

            CREATE TABLE IF NOT EXISTS shared_accounts (
                relationship_id INTEGER PRIMARY KEY REFERENCES relationships(id) ON DELETE CASCADE,
                guild_id        BIGINT  NOT NULL,
                balance         INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS court_cases (
                id              SERIAL    PRIMARY KEY,
                guild_id        BIGINT    NOT NULL,
                relationship_id INTEGER   NOT NULL,
                filer_id        BIGINT    NOT NULL,
                defendant_id    BIGINT    NOT NULL,
                judge_id        BIGINT,
                reason          TEXT,
                status          TEXT      NOT NULL DEFAULT 'open',
                created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
                message_id      BIGINT
            );

            CREATE TABLE IF NOT EXISTS waifu_market (
                id              SERIAL    PRIMARY KEY,
                guild_id        BIGINT    NOT NULL,
                seller_id       BIGINT    NOT NULL,
                waifu_id        BIGINT    NOT NULL,
                min_bid         INTEGER   NOT NULL DEFAULT 0,
                buyout_price    INTEGER,
                current_bid     INTEGER   NOT NULL DEFAULT 0,
                current_bidder  BIGINT,
                status          TEXT      NOT NULL DEFAULT 'active',
                listed_at       TIMESTAMP NOT NULL DEFAULT NOW(),
                channel_id      BIGINT,
                message_id      BIGINT
            );

            CREATE TABLE IF NOT EXISTS guild_config (
                guild_id BIGINT NOT NULL,
                key      TEXT   NOT NULL,
                value    TEXT   NOT NULL,
                PRIMARY KEY (guild_id, key)
            );

            CREATE TABLE IF NOT EXISTS guild_setup (
                guild_id   BIGINT    PRIMARY KEY,
                setup_done BOOLEAN   NOT NULL DEFAULT FALSE,
                setup_by   BIGINT,
                setup_at   TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS antinoobify_messages (
                message_id BIGINT PRIMARY KEY,
                guild_id   BIGINT NOT NULL,
                channel_id BIGINT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS greeted_channels (
                channel_id BIGINT PRIMARY KEY
            );

            CREATE TABLE IF NOT EXISTS work_log (
                guild_id   BIGINT    NOT NULL,
                user_id    BIGINT    NOT NULL,
                last_work  TIMESTAMP,
                last_crime TIMESTAMP,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS gacha_pity (
                guild_id BIGINT  NOT NULL,
                user_id  BIGINT  NOT NULL,
                pulls    INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS businesses (
                id           SERIAL    PRIMARY KEY,
                guild_id     BIGINT    NOT NULL,
                owner_id     BIGINT    NOT NULL,
                manager_id   BIGINT,
                type         TEXT      NOT NULL,
                name         TEXT      NOT NULL,
                level        INTEGER   NOT NULL DEFAULT 1,
                balance      INTEGER   NOT NULL DEFAULT 0,
                last_collect TIMESTAMP,
                status       TEXT      NOT NULL DEFAULT 'running',
                created_at   TIMESTAMP NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS inventory (
                guild_id  BIGINT NOT NULL,
                user_id   BIGINT NOT NULL,
                item_key  TEXT   NOT NULL,
                quantity  INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id, item_key)
            );

            CREATE TABLE IF NOT EXISTS auction_house (
                id          SERIAL    PRIMARY KEY,
                guild_id    BIGINT    NOT NULL,
                seller_id   BIGINT    NOT NULL,
                item_key    TEXT      NOT NULL,
                item_name   TEXT      NOT NULL,
                rarity      TEXT      NOT NULL DEFAULT 'common',
                quantity    INTEGER   NOT NULL DEFAULT 1,
                price       INTEGER   NOT NULL,
                status      TEXT      NOT NULL DEFAULT 'active',
                listed_at   TIMESTAMP NOT NULL DEFAULT NOW(),
                expires_at  TIMESTAMP,
                min_bid     INTEGER,
                current_bid INTEGER,
                current_bidder_id BIGINT,
                listing_message_id BIGINT,
                listing_channel_id BIGINT
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id         SERIAL    PRIMARY KEY,
                guild_id   BIGINT    NOT NULL,
                user_id    BIGINT    NOT NULL,
                action     TEXT      NOT NULL,
                detail     TEXT,
                amount     INTEGER,
                balance_before INTEGER,
                balance_after  INTEGER,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS afk (
                guild_id BIGINT    NOT NULL,
                user_id  BIGINT    NOT NULL,
                reason   TEXT      NOT NULL DEFAULT 'AFK',
                set_at   TIMESTAMP NOT NULL DEFAULT NOW(),
                PRIMARY KEY (guild_id, user_id)
            );


            CREATE TABLE IF NOT EXISTS gacha_persistent (
                guild_id    BIGINT PRIMARY KEY,
                channel_id  BIGINT NOT NULL,
                message_id  BIGINT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ah_bids (
                id          SERIAL    PRIMARY KEY,
                listing_id  INTEGER   NOT NULL,
                guild_id    BIGINT    NOT NULL,
                bidder_id   BIGINT    NOT NULL,
                amount      INTEGER   NOT NULL,
                placed_at   TIMESTAMP NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS anime_waifus (
                id          SERIAL    PRIMARY KEY,
                guild_id    BIGINT    NOT NULL,
                owner_id    BIGINT    NOT NULL,
                char_name   TEXT      NOT NULL,
                char_image  TEXT      NOT NULL,
                rarity      TEXT      NOT NULL DEFAULT 'common',
                obtained_at TIMESTAMP NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS orders (
                id          SERIAL    PRIMARY KEY,
                guild_id    BIGINT    NOT NULL,
                buyer_id    BIGINT    NOT NULL,
                item_key    TEXT      NOT NULL,
                item_name   TEXT      NOT NULL,
                max_price   INTEGER   NOT NULL,
                quantity    INTEGER   NOT NULL DEFAULT 1,
                status      TEXT      NOT NULL DEFAULT 'open',
                created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
                filled_by   BIGINT
            );

            -- Add cheating_strikes column if not present (safe to run multiple times)
            -- Note: ALTER TABLE in asyncpg init must be outside CREATE IF NOT EXISTS blocks
            -- We handle this via the migration below after tables are created.


            -- Kakera currency (second currency, earned from milestones/dupes)
            ALTER TABLE economy ADD COLUMN IF NOT EXISTS kakera INTEGER NOT NULL DEFAULT 0;

            -- Action counters on social table  
            ALTER TABLE social ADD COLUMN IF NOT EXISTS hugs_received INTEGER NOT NULL DEFAULT 0;
            ALTER TABLE social ADD COLUMN IF NOT EXISTS kisses_received INTEGER NOT NULL DEFAULT 0;
            ALTER TABLE social ADD COLUMN IF NOT EXISTS pats_received INTEGER NOT NULL DEFAULT 0;

            -- Milestones claimed (prevent double rewards)
            CREATE TABLE IF NOT EXISTS milestones_claimed (
                guild_id   BIGINT NOT NULL,
                user_id    BIGINT NOT NULL,
                milestone  TEXT NOT NULL,
                claimed_at TIMESTAMP NOT NULL DEFAULT NOW(),
                PRIMARY KEY (guild_id, user_id, milestone)
            );

            -- Achievements
            CREATE TABLE IF NOT EXISTS achievements (
                guild_id    BIGINT NOT NULL,
                user_id     BIGINT NOT NULL,
                achievement TEXT NOT NULL,
                progress    INTEGER NOT NULL DEFAULT 0,
                completed   BOOLEAN NOT NULL DEFAULT FALSE,
                completed_at TIMESTAMP,
                PRIMARY KEY (guild_id, user_id, achievement)
            );

            -- Online streaks
            CREATE TABLE IF NOT EXISTS online_streaks (
                guild_id             BIGINT NOT NULL,
                user_id              BIGINT NOT NULL,
                current_streak_hours INTEGER NOT NULL DEFAULT 0,
                best_streak_hours    INTEGER NOT NULL DEFAULT 0,
                last_active          TIMESTAMP,
                PRIMARY KEY (guild_id, user_id)
            );

            -- Kakera shop items
            CREATE TABLE IF NOT EXISTS kakera_shop_purchases (
                guild_id   BIGINT NOT NULL,
                user_id    BIGINT NOT NULL,
                item       TEXT NOT NULL,
                purchased_at TIMESTAMP NOT NULL DEFAULT NOW(),
                PRIMARY KEY (guild_id, user_id, item)
            );

            -- Daily reminder tracking (separate from last_daily - tracks if reminder was sent today)
            ALTER TABLE economy ADD COLUMN IF NOT EXISTS last_daily_reminder DATE;

            CREATE TABLE IF NOT EXISTS rpblock (
                guild_id   BIGINT NOT NULL,
                user_id    BIGINT NOT NULL,
                blocked_id BIGINT NOT NULL,
                PRIMARY KEY (guild_id, user_id, blocked_id)
            );
        """)


async def _create_indexes():
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction block.
    # Each statement must be its own top-level query with autocommit.
    indexes = [
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_xp_guild_xp      ON xp        (guild_id, xp DESC)",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_economy_balance   ON economy   (guild_id, balance DESC)",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_social_rep        ON social    (guild_id, rep DESC)",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_claims_claimer    ON claims    (guild_id, claimer_id)",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_wm_active         ON waifu_market (guild_id, status) WHERE status = 'active'",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rel_active        ON relationships (guild_id, user1_id, user2_id) WHERE status = 'active'",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_proposals_pending ON proposals (guild_id, target_id) WHERE status = 'pending'",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_biz_guild         ON businesses (guild_id, owner_id)",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ah_active         ON auction_house (guild_id, status) WHERE status = 'active'",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_audit_guild       ON audit_log  (guild_id, created_at DESC)",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_inventory_user    ON inventory  (guild_id, user_id)",
    ]
    # Migrate: add cheating_strikes to relationships if not present
    try:
        await pool.execute("""
            ALTER TABLE relationships ADD COLUMN IF NOT EXISTS cheating_strikes INTEGER NOT NULL DEFAULT 0
        """)
    except Exception:
        pass

    # Use a raw connection outside pool — CONCURRENTLY requires non-transactional context
    conn = await pool.acquire()
    try:
        for sql in indexes:
            try:
                await conn.execute(sql)
            except Exception as e:
                # Index already exists or minor issue — not fatal
                if "already exists" not in str(e).lower():
                    print(f"Index warning: {e}")
    finally:
        await pool.release(conn)
