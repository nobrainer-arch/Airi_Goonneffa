# airi/gender.py
# Gender preferences backed by PostgreSQL.
# In-memory cache still used so we don't hit the DB on every GIF command.

import db

_cache: dict[str, str | None] = {}


async def get_gender(user_id: str) -> str | None:
    if user_id in _cache:
        return _cache[user_id]
    row = await db.pool.fetchrow(
        "SELECT gender FROM user_prefs WHERE user_id = $1",
        int(user_id),
    )
    value = row["gender"].strip() if row else None
    _cache[user_id] = value
    return value


async def set_gender(user_id: str, gender: str) -> None:
    _cache[user_id] = gender
    await db.pool.execute("""
        INSERT INTO user_prefs (user_id, gender)
        VALUES ($1, $2)
        ON CONFLICT (user_id) DO UPDATE SET gender = EXCLUDED.gender
    """, int(user_id), gender)


async def reset_gender(user_id: str) -> None:
    _cache.pop(user_id, None)
    await db.pool.execute(
        "DELETE FROM user_prefs WHERE user_id = $1", int(user_id)
    )


async def load_prefs() -> None:
    """Warm the in-memory cache from the DB at startup."""
    rows = await db.pool.fetch("SELECT user_id, gender FROM user_prefs")
    for row in rows:
        _cache[str(row["user_id"])] = row["gender"].strip()
    print(f"✅ Loaded {len(rows)} gender prefs from DB.")
