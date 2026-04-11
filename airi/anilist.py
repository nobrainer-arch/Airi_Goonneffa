# airi/anilist.py — AniList character fetching with media + description
import aiohttp
import asyncio
import random

ANILIST_URL = "https://graphql.anilist.co"
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
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

def _favourites_to_rarity(fav: int) -> str:
    if fav >= 20000: return "mythic"
    if fav >= 8000:  return "legendary"
    if fav >= 3000:  return "epic"
    if fav >= 1000:  return "rare"
    return "common"

def _parse(c: dict, gender_filter: str) -> dict | None:
    gender = (c.get("gender") or "").lower()
    if gender_filter == "female" and "female" not in gender: return None
    if gender_filter == "male"   and "male"   not in gender: return None
    fav    = c.get("favourites", 0)
    media  = c.get("media", {}).get("nodes", [])
    series = ""
    if media:
        t = media[0].get("title", {})
        series = t.get("english") or t.get("romaji") or t.get("native") or "Unknown"
    bio = (c.get("description") or "").replace("\n", " ").strip()
    if len(bio) > 200: bio = bio[:197] + "…"
    return {
        "id":         c.get("id"),
        "name":       c.get("name", {}).get("full", "Unknown"),
        "image":      c.get("image", {}).get("large", ""),
        "favourites": fav,
        "gender":     gender_filter,
        "rarity":     _favourites_to_rarity(fav),
        "series":     series or "Unknown",
        "bio":        bio,
    }

async def _fetch_page(session: aiohttp.ClientSession, page: int) -> list | None:
    try:
        async with session.post(
            ANILIST_URL,
            json={"query": QUERY, "variables": {"page": page, "perPage": 50}},
            timeout=aiohttp.ClientTimeout(total=12),
            headers=HEADERS,
        ) as r:
            if r.status == 200:
                data = await r.json()
                return data.get("data", {}).get("Page", {}).get("characters", [])
            if r.status == 429:
                return None   # rate-limited
    except Exception as e:
        print(f"AniList p{page}: {e}")
    return []

async def fetch_characters_for_board(gender: str = "female") -> dict:
    """
    Returns buckets:
      mythic/legendary/epic/rare/common  → lists
      all       → up to 90 total
      rate_limited → bool
    Targets: 2 mythic, 5 legendary, 15 epic, 30 rare, 38 common  (= 90)
    """
    targets = {"mythic":2,"legendary":5,"epic":15,"rare":30,"common":38}
    buckets: dict[str,list] = {k:[] for k in targets}
    page = 1; requests_used = 0; rate_limited = False

    session = aiohttp.ClientSession()
    try:
        while requests_used < 90:
            result = await _fetch_page(session, page)
            requests_used += 1
            if result is None:            # rate-limited
                rate_limited = True; break
            for c in result:
                p = _parse(c, gender)
                if not p: continue
                r = p["rarity"]
                if len(buckets[r]) < targets.get(r, 0):
                    buckets[r].append(p)
            needed = sum(max(0, targets[r] - len(buckets[r])) for r in targets)
            if needed == 0: break
            page += 1
            await asyncio.sleep(0.7)
    finally:
        await session.close()

    all_chars = []
    for r in ("mythic","legendary","epic","rare","common"):
        all_chars.extend(buckets[r])
    buckets["all"] = all_chars[:90]
    buckets["rate_limited"] = rate_limited
    buckets["requests_used"] = requests_used
    return buckets
