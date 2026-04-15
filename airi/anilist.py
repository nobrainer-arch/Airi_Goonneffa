# airi/anilist.py — AniList character fetching
# Rarity is PERCENTILE-BASED within the fetched pool, not absolute.
# Top 2 chars by favs = mythic, next 5 = legendary, etc.
# This guarantees every board always has mythic chars regardless of abs favs.
import aiohttp
import asyncio
import random

ANILIST_URL = "https://graphql.anilist.co"
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

QUERY = """
query ($page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    characters(sort: FAVOURITES_DESC) {
      id
      name { full }
      gender
      favourites
      image { large }
      description(asHtml: false)
      media(perPage: 1) {
        nodes {
          title { romaji english native }
          type
        }
      }
    }
  }
}
"""

# Pool size targets per rarity bucket
POOL_TARGETS = {
    "mythic":    2,
    "legendary": 5,
    "epic":      15,
    "rare":      30,
    "common":    38,
}
TOTAL_TARGET = sum(POOL_TARGETS.values())  # 90


def _assign_rarity_by_rank(chars_sorted: list[dict]) -> list[dict]:
    """
    Assign rarity based on rank within the fetched pool (percentile-based).
    chars_sorted must already be sorted by favourites DESC.

    Distribution (out of 90 total):
      Rank 1-2   → mythic
      Rank 3-7   → legendary
      Rank 8-22  → epic
      Rank 23-52 → rare
      Rank 53+   → common
    """
    boundaries = [
        ("mythic",    2),
        ("legendary", 7),
        ("epic",      22),
        ("rare",      52),
    ]
    result = []
    for i, c in enumerate(chars_sorted):
        rank = i + 1
        rarity = "common"
        for r, cutoff in boundaries:
            if rank <= cutoff:
                rarity = r
                break
        c = dict(c)
        c["rarity"] = rarity
        result.append(c)
    return result


def _parse(c: dict, gender_filter: str) -> dict | None:
    gender = (c.get("gender") or "").lower()
    if gender_filter == "female" and "female" not in gender:
        return None
    if gender_filter == "male" and "male" not in gender:
        return None

    fav   = c.get("favourites", 0)
    media = c.get("media", {}).get("nodes", [])
    series = ""
    if media:
        t = media[0].get("title", {})
        series = t.get("english") or t.get("romaji") or t.get("native") or "Unknown"

    bio = (c.get("description") or "").replace("\n", " ").strip()
    if len(bio) > 200:
        bio = bio[:197] + "…"

    return {
        "id":         c.get("id"),
        "name":       c.get("name", {}).get("full", "Unknown"),
        "image":      c.get("image", {}).get("large", ""),
        "favourites": fav,
        "gender":     gender_filter,
        "rarity":     "common",   # overwritten by _assign_rarity_by_rank
        "series":     series or "Unknown",
        "bio":        bio,
    }


async def _fetch_page(session: aiohttp.ClientSession, page: int) -> list | None:
    try:
        async with session.post(
            ANILIST_URL,
            json={"query": QUERY, "variables": {"page": page, "perPage": 50}},
            timeout=aiohttp.ClientTimeout(total=15),
            headers=HEADERS,
        ) as r:
            if r.status == 200:
                data = await r.json()
                return data.get("data", {}).get("Page", {}).get("characters", [])
            if r.status == 429:
                return None  # rate-limited
    except Exception as e:
        print(f"AniList p{page}: {e}")
    return []


async def fetch_characters_for_board(gender: str = "female") -> dict:
    """
    Fetch up to TOTAL_TARGET characters, sort by favourites DESC,
    assign rarity by rank (percentile), return bucketed dict.

    Returns:
        mythic/legendary/epic/rare/common → lists
        all         → full sorted list (up to 90)
        rate_limited → bool
    """
    collected: list[dict] = []
    page = 1
    requests_used = 0
    rate_limited = False

    async with aiohttp.ClientSession() as session:
        while len(collected) < TOTAL_TARGET and requests_used < 20:
            result = await _fetch_page(session, page)
            requests_used += 1
            if result is None:
                rate_limited = True
                break
            if not result:
                break   # no more pages
            for c in result:
                p = _parse(c, gender)
                if p:
                    collected.append(p)
                if len(collected) >= TOTAL_TARGET:
                    break
            page += 1
            await asyncio.sleep(0.6)

    # Sort by favourites desc, take top TOTAL_TARGET
    collected.sort(key=lambda x: x["favourites"], reverse=True)
    collected = collected[:TOTAL_TARGET]

    # Assign rarity by percentile rank
    collected = _assign_rarity_by_rank(collected)

    # Build buckets
    buckets: dict = {k: [] for k in POOL_TARGETS}
    for c in collected:
        r = c["rarity"]
        buckets.setdefault(r, []).append(c)

    buckets["all"] = collected
    buckets["rate_limited"] = rate_limited
    buckets["requests_used"] = requests_used
    return buckets
