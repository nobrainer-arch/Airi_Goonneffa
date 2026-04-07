# airi/anilist.py — AniList + Jikan API for character fetching
# Fetches real anime characters with popularity data for rarity assignment.
# Popular chars (high favourites) = rarer. One-user-only for Legendary/Mythic.
import aiohttp
import random
import asyncio
import db
from datetime import datetime

ANILIST_URL = "https://graphql.anilist.co"
JIKAN_URL   = "https://api.jikan.moe/v4"

# Rarity thresholds based on AniList favourites count
def favourites_to_rarity(fav: int) -> str:
    if fav >= 15000: return "mythic"
    if fav >= 7000:  return "legendary"
    if fav >= 3000:  return "epic"
    if fav >= 1000:  return "rare"
    return "common"


# ── AniList queries ────────────────────────────────────────────────
CHAR_QUERY = """
query ($page: Int, $perPage: Int, $isFemale: Boolean) {
  Page(page: $page, perPage: $perPage) {
    characters(sort: FAVOURITES_DESC) {
      id
      name { full }
      image { large }
      favourites
      gender
      media(sort: POPULARITY_DESC, perPage: 1) {
        nodes { title { english romaji } }
      }
    }
  }
}
"""

async def _anilist_fetch(page: int = 1, per_page: int = 50,
                         session: aiohttp.ClientSession | None = None) -> list[dict]:
    """Fetch characters from AniList, sorted by favourites."""
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()
    try:
        async with session.post(
            ANILIST_URL,
            json={"query": CHAR_QUERY, "variables": {"page": page, "perPage": per_page}},
            timeout=aiohttp.ClientTimeout(total=10),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        ) as r:
            if r.status == 200:
                data = await r.json()
                return data.get("data", {}).get("Page", {}).get("characters", [])
    except Exception as e:
        print(f"AniList fetch error: {e}")
    finally:
        if own_session:
            await session.close()
    return []


async def _jikan_fetch(page: int = 1) -> list[dict]:
    """Fallback: fetch from Jikan (MAL) if AniList fails."""
    session = aiohttp.ClientSession()
    try:
        async with session.get(
            f"{JIKAN_URL}/characters",
            params={"order_by": "favorites", "sort": "desc", "page": page},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            if r.status == 200:
                data = await r.json()
                raw = data.get("data", [])
                chars = []
                for c in raw:
                    chars.append({
                        "id":         c.get("mal_id"),
                        "name":       c.get("name", "Unknown"),
                        "image":      c.get("images", {}).get("jpg", {}).get("image_url", ""),
                        "favourites": c.get("favorites", 0),
                        "gender":     None,
                        "series":     c.get("anime", [{}])[0].get("title") if c.get("anime") else "Unknown",
                    })
                return chars
    except Exception as e:
        print(f"Jikan fetch error: {e}")
    finally:
        await session.close()
    return []


def _parse_anilist(raw: list[dict]) -> list[dict]:
    chars = []
    for c in raw:
        name  = c.get("name", {}).get("full", "Unknown")
        image = c.get("image", {}).get("large", "")
        fav   = c.get("favourites", 0)
        gender = c.get("gender") or "female"
        media = c.get("media", {}).get("nodes", [{}])
        series = ""
        if media:
            t = media[0].get("title", {})
            series = t.get("english") or t.get("romaji") or "Unknown"
        chars.append({
            "id":         c.get("id"),
            "name":       name,
            "image":      image,
            "favourites": fav,
            "gender":     gender.lower() if gender else "female",
            "series":     series,
            "rarity":     favourites_to_rarity(fav),
            "source":     "anilist",
        })
    return chars


async def fetch_characters(count: int = 50, gender: str = "female") -> list[dict]:
    """Fetch `count` characters of given gender. Falls back to Jikan if AniList fails."""
    chars = []
    page  = 1
    while len(chars) < count and page <= 5:
        raw = await _anilist_fetch(page=page, per_page=50)
        if not raw:
            raw_j = await _jikan_fetch(page=page)
            if raw_j:
                # Jikan doesn't have gender — assign female for waifu, male for husbando
                for c in raw_j:
                    c["gender"] = gender
                    c["rarity"] = favourites_to_rarity(c.get("favourites", 0))
                    c["source"] = "jikan"
                chars.extend(raw_j)
            break
        parsed = _parse_anilist(raw)
        # Filter by gender (AniList often has null gender for non-binary/unknown)
        filtered = [c for c in parsed if (c["gender"] or "female") == gender or c["gender"] in (None, "")]
        chars.extend(filtered)
        page += 1
        await asyncio.sleep(0.5)  # rate limit

    random.shuffle(chars)
    return chars[:count]


# ── Image ownership: Legendary/Mythic = one user only ─────────────
async def is_char_taken(guild_id: int, char_source_id: int, rarity: str) -> int | None:
    """For legendary/mythic chars, returns the owner_id if already claimed. None otherwise."""
    if rarity not in ("legendary", "mythic"):
        return None
    row = await db.pool.fetchrow("""
        SELECT owner_id FROM anime_waifus
        WHERE guild_id=$1 AND source_id=$2 AND rarity=$3
        LIMIT 1
    """, guild_id, char_source_id, rarity)
    return row["owner_id"] if row else None


async def ensure_char_columns():
    """Add source_id column to anime_waifus if not present (run once on startup)."""
    try:
        await db.pool.execute(
            "ALTER TABLE anime_waifus ADD COLUMN IF NOT EXISTS source_id INTEGER"
        )
        await db.pool.execute(
            "ALTER TABLE anime_waifus ADD COLUMN IF NOT EXISTS series TEXT DEFAULT 'Unknown'"
        )
        await db.pool.execute(
            "ALTER TABLE anime_waifus ADD COLUMN IF NOT EXISTS gender TEXT DEFAULT 'female'"
        )
        await db.pool.execute(
            "ALTER TABLE anime_waifus ADD COLUMN IF NOT EXISTS favourites INTEGER DEFAULT 0"
        )
        await db.pool.execute(
            "ALTER TABLE anime_waifus ADD COLUMN IF NOT EXISTS personality_tag TEXT"
        )
        await db.pool.execute(
            "ALTER TABLE anime_waifus ADD COLUMN IF NOT EXISTS card_wrap TEXT DEFAULT 'default'"
        )
        await db.pool.execute(
            "ALTER TABLE anime_waifus ADD COLUMN IF NOT EXISTS affection INTEGER DEFAULT 0"
        )
    except Exception as e:
        print(f"anilist ensure_columns: {e}")
