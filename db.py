# db.py — database pool + schema init
import asyncpg
import config

pool: asyncpg.Pool = None  # type: ignore

async def init():
    global pool
    pool = await asyncpg.create_pool(config.DATABASE_URL, min_size=2, max_size=10)
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS guild_config (
                guild_id  BIGINT NOT NULL,
                key       TEXT   NOT NULL,
                value     TEXT   NOT NULL DEFAULT '',
                PRIMARY KEY (guild_id, key)
            );

            CREATE TABLE IF NOT EXISTS economy (
                guild_id     BIGINT NOT NULL,
                user_id      BIGINT NOT NULL,
                balance      BIGINT NOT NULL DEFAULT 0,
                kakera       INTEGER NOT NULL DEFAULT 0,
                last_daily   TIMESTAMP,
                streak       INTEGER NOT NULL DEFAULT 0,
                daily_boost  BOOLEAN NOT NULL DEFAULT FALSE,
                active_title TEXT,
                titles       TEXT[] DEFAULT '{}',
                xp_boost_until TIMESTAMP,
                proposals_made INTEGER NOT NULL DEFAULT 0,
                last_daily_reminder DATE,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS xp (
                guild_id     BIGINT NOT NULL,
                user_id      BIGINT NOT NULL,
                xp           INTEGER NOT NULL DEFAULT 0,
                level        INTEGER NOT NULL DEFAULT 0,
                last_msg     TIMESTAMP,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS social (
                guild_id         BIGINT NOT NULL,
                user_id          BIGINT NOT NULL,
                rep              INTEGER NOT NULL DEFAULT 0,
                last_rep_given   TIMESTAMP,
                hugs_received    INTEGER NOT NULL DEFAULT 0,
                kisses_received  INTEGER NOT NULL DEFAULT 0,
                pats_received    INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS claims (
                guild_id    BIGINT NOT NULL,
                claimer_id  BIGINT NOT NULL,
                claimed_id  BIGINT NOT NULL,
                claimed_at  TIMESTAMP NOT NULL DEFAULT NOW(),
                UNIQUE (guild_id, claimer_id, claimed_id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS claims_exclusive
                ON claims (guild_id, claimed_id);

            CREATE TABLE IF NOT EXISTS nsfw_optout (
                guild_id BIGINT NOT NULL,
                user_id  BIGINT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS rpblock (
                guild_id   BIGINT NOT NULL,
                user_id    BIGINT NOT NULL,
                blocked_id BIGINT NOT NULL,
                PRIMARY KEY (guild_id, user_id, blocked_id)
            );

            CREATE TABLE IF NOT EXISTS relationships (
                id          SERIAL PRIMARY KEY,
                guild_id    BIGINT NOT NULL,
                user1_id    BIGINT NOT NULL,
                user2_id    BIGINT NOT NULL,
                type        TEXT   NOT NULL,
                status      TEXT   NOT NULL DEFAULT 'active',
                started_at  TIMESTAMP NOT NULL DEFAULT NOW(),
                dowry       INTEGER NOT NULL DEFAULT 0,
                prenup      BOOLEAN NOT NULL DEFAULT FALSE,
                cheating_strikes INTEGER NOT NULL DEFAULT 0,
                infamy_points    INTEGER NOT NULL DEFAULT 0
            );
            CREATE UNIQUE INDEX IF NOT EXISTS rel_active_pair
                ON relationships (guild_id, LEAST(user1_id,user2_id), GREATEST(user1_id,user2_id))
                WHERE status = 'active';

            CREATE TABLE IF NOT EXISTS shared_accounts (
                relationship_id INTEGER PRIMARY KEY REFERENCES relationships(id) ON DELETE CASCADE,
                balance         BIGINT NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS proposals (
                id           SERIAL PRIMARY KEY,
                guild_id     BIGINT NOT NULL,
                proposer_id  BIGINT NOT NULL,
                target_id    BIGINT NOT NULL,
                prop_type    TEXT   NOT NULL,
                dowry        INTEGER NOT NULL DEFAULT 0,
                prenup       BOOLEAN NOT NULL DEFAULT FALSE,
                status       TEXT   NOT NULL DEFAULT 'pending',
                created_at   TIMESTAMP NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS court_cases (
                id              SERIAL PRIMARY KEY,
                guild_id        BIGINT NOT NULL,
                relationship_id INTEGER REFERENCES relationships(id),
                filer_id        BIGINT NOT NULL,
                defendant_id    BIGINT NOT NULL,
                reason          TEXT   NOT NULL DEFAULT '',
                status          TEXT   NOT NULL DEFAULT 'open',
                verdict         TEXT,
                filed_at        TIMESTAMP NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS rel_optout (
                guild_id BIGINT NOT NULL,
                user_id  BIGINT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS mutual_affection (
                guild_id  BIGINT NOT NULL,
                user1_id  BIGINT NOT NULL,
                user2_id  BIGINT NOT NULL,
                kiss_count INTEGER NOT NULL DEFAULT 0,
                hug_count  INTEGER NOT NULL DEFAULT 0,
                prompt_sent BOOLEAN NOT NULL DEFAULT FALSE,
                PRIMARY KEY (guild_id, user1_id, user2_id)
            );

            CREATE TABLE IF NOT EXISTS inventory (
                guild_id  BIGINT NOT NULL,
                user_id   BIGINT NOT NULL,
                item_key  TEXT   NOT NULL,
                quantity  INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id, item_key)
            );

            CREATE TABLE IF NOT EXISTS auction_house (
                id          SERIAL PRIMARY KEY,
                guild_id    BIGINT NOT NULL,
                seller_id   BIGINT NOT NULL,
                item_key    TEXT   NOT NULL,
                item_name   TEXT   NOT NULL,
                quantity    INTEGER NOT NULL DEFAULT 1,
                min_bid     INTEGER NOT NULL DEFAULT 0,
                buyout      INTEGER NOT NULL DEFAULT 0,
                current_bid INTEGER NOT NULL DEFAULT 0,
                bidder_id   BIGINT,
                status      TEXT   NOT NULL DEFAULT 'active',
                expires_at  TIMESTAMP NOT NULL,
                channel_id  BIGINT,
                message_id  BIGINT
            );

            CREATE TABLE IF NOT EXISTS orders (
                id          SERIAL PRIMARY KEY,
                guild_id    BIGINT NOT NULL,
                buyer_id    BIGINT NOT NULL,
                item_key    TEXT   NOT NULL,
                item_name   TEXT   NOT NULL,
                max_price   INTEGER NOT NULL,
                quantity    INTEGER NOT NULL DEFAULT 1,
                filled      INTEGER NOT NULL DEFAULT 0,
                status      TEXT   NOT NULL DEFAULT 'open'
            );

            CREATE TABLE IF NOT EXISTS work_log (
                guild_id    BIGINT NOT NULL,
                user_id     BIGINT NOT NULL,
                last_work   TIMESTAMP,
                last_crime  TIMESTAMP,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS businesses (
                id          SERIAL PRIMARY KEY,
                guild_id    BIGINT NOT NULL,
                owner_id    BIGINT NOT NULL,
                manager_id  BIGINT,
                name        TEXT   NOT NULL,
                biz_type    TEXT   NOT NULL,
                level       INTEGER NOT NULL DEFAULT 1,
                last_collect TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS gacha_pity (
                guild_id BIGINT NOT NULL,
                user_id  BIGINT NOT NULL,
                pulls    INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS gacha_inventory (
                guild_id  BIGINT NOT NULL,
                user_id   BIGINT NOT NULL,
                item_key  TEXT   NOT NULL,
                item_name TEXT   NOT NULL,
                rarity    TEXT   NOT NULL,
                obtained_at TIMESTAMP NOT NULL DEFAULT NOW(),
                PRIMARY KEY (guild_id, user_id, item_key)
            );

            CREATE TABLE IF NOT EXISTS anime_waifus (
                id           SERIAL PRIMARY KEY,
                guild_id     BIGINT NOT NULL,
                owner_id     BIGINT NOT NULL,
                char_name    TEXT   NOT NULL,
                char_image   TEXT   NOT NULL DEFAULT '',
                rarity       TEXT   NOT NULL DEFAULT 'common',
                source_id    INTEGER,
                series       TEXT   DEFAULT 'Unknown',
                gender       TEXT   DEFAULT 'female',
                favourites   INTEGER DEFAULT 0,
                personality_tag TEXT,
                card_wrap    TEXT   DEFAULT 'default',
                affection    INTEGER DEFAULT 0,
                obtained_at  TIMESTAMP NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS banners (
                id         SERIAL PRIMARY KEY,
                guild_id   BIGINT NOT NULL,
                char_name  TEXT   NOT NULL,
                char_image TEXT   NOT NULL,
                char_gender TEXT  NOT NULL DEFAULT 'female',
                rarity     TEXT   NOT NULL,
                series     TEXT   DEFAULT 'Unknown',
                source_id  INTEGER,
                boost_mult FLOAT  DEFAULT 2.0,
                starts_at  TIMESTAMP NOT NULL DEFAULT NOW(),
                ends_at    TIMESTAMP NOT NULL,
                is_active  BOOLEAN   DEFAULT TRUE
            );

            CREATE TABLE IF NOT EXISTS milestones_claimed (
                guild_id   BIGINT NOT NULL,
                user_id    BIGINT NOT NULL,
                milestone  TEXT   NOT NULL,
                claimed_at TIMESTAMP NOT NULL DEFAULT NOW(),
                PRIMARY KEY (guild_id, user_id, milestone)
            );

            CREATE TABLE IF NOT EXISTS achievements (
                guild_id    BIGINT NOT NULL,
                user_id     BIGINT NOT NULL,
                achievement TEXT   NOT NULL,
                progress    INTEGER NOT NULL DEFAULT 0,
                completed   BOOLEAN NOT NULL DEFAULT FALSE,
                completed_at TIMESTAMP,
                PRIMARY KEY (guild_id, user_id, achievement)
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id         SERIAL PRIMARY KEY,
                guild_id   BIGINT NOT NULL,
                user_id    BIGINT NOT NULL,
                action     TEXT   NOT NULL,
                detail     TEXT   NOT NULL DEFAULT '',
                amount     INTEGER NOT NULL DEFAULT 0,
                logged_at  TIMESTAMP NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS afk (
                guild_id  BIGINT NOT NULL,
                user_id   BIGINT NOT NULL,
                reason    TEXT   NOT NULL DEFAULT 'AFK',
                set_at    TIMESTAMP NOT NULL DEFAULT NOW(),
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS gender_prefs (
                user_id BIGINT PRIMARY KEY,
                gender  TEXT   NOT NULL DEFAULT 'u'
            );

            CREATE TABLE IF NOT EXISTS online_streaks (
                guild_id             BIGINT NOT NULL,
                user_id              BIGINT NOT NULL,
                current_streak_hours INTEGER NOT NULL DEFAULT 0,
                best_streak_hours    INTEGER NOT NULL DEFAULT 0,
                last_active          TIMESTAMP,
                PRIMARY KEY (guild_id, user_id)
            );
        """)

    # Safe migrations for fresh or existing DBs
    migrations = [
        "ALTER TABLE economy ADD COLUMN IF NOT EXISTS kakera INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE economy ADD COLUMN IF NOT EXISTS proposals_made INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE economy ADD COLUMN IF NOT EXISTS last_daily_reminder DATE",
        "ALTER TABLE economy ADD COLUMN IF NOT EXISTS xp_boost_until TIMESTAMP",
        "ALTER TABLE relationships ADD COLUMN IF NOT EXISTS cheating_strikes INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE relationships ADD COLUMN IF NOT EXISTS infamy_points INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE social ADD COLUMN IF NOT EXISTS hugs_received INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE social ADD COLUMN IF NOT EXISTS kisses_received INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE social ADD COLUMN IF NOT EXISTS pats_received INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE anime_waifus ADD COLUMN IF NOT EXISTS source_id INTEGER",
        "ALTER TABLE anime_waifus ADD COLUMN IF NOT EXISTS series TEXT DEFAULT 'Unknown'",
        "ALTER TABLE anime_waifus ADD COLUMN IF NOT EXISTS gender TEXT DEFAULT 'female'",
        "ALTER TABLE anime_waifus ADD COLUMN IF NOT EXISTS favourites INTEGER DEFAULT 0",
        "ALTER TABLE anime_waifus ADD COLUMN IF NOT EXISTS personality_tag TEXT",
        "ALTER TABLE anime_waifus ADD COLUMN IF NOT EXISTS card_wrap TEXT DEFAULT 'default'",
        "ALTER TABLE anime_waifus ADD COLUMN IF NOT EXISTS affection INTEGER DEFAULT 0",
        "ALTER TABLE claims ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMP NOT NULL DEFAULT NOW()",
        "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS last_collected TIMESTAMP",
    ]
    async with pool.acquire() as conn:
        for sql in migrations:
            try:
                await conn.execute(sql)
            except Exception:
                pass

    print("✅ Database ready")
