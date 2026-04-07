# airi/gif_provider.py
# GIF provider:
#   SFW:  nekos.best → nekos.life → waifu.pics → Klipy (pool/variety) → otakugifs
#   NSFW: gifs.json pool (no male/female distinction, merged by command name)
#         Klipy as secondary fallback for commands with no gifs.json entry
import aiohttp
import random
import os
from datetime import datetime, timedelta

KLIPY_KEY = os.getenv("KLIPY_API_KEY", "1bhaiaxUnVFAr4JpBsWgAMOv3Z12Noyx0R2DVuqrKRJeDmalZuKiaLJC6AOkRFJ8")
REDDIT_UA = "AiriBot/2.0 (Discord bot)"

# ── gifs.json pools (populated by load_gifs_pool() called from bot.py) ────────
_nsfw_pool: dict[str, list[str]] = {}
_sfw_pool:  dict[str, list[str]] = {}

def load_gifs_pool(gifs_data: dict):
    """
    Merge gifs.json keys (blowjob_male, blowjob_female → blowjob) into flat pools.
    Called once from bot.py after the json is loaded.  No gender distinction needed
    because actio.py handles the text; we just need a pool of images per action.
    """
    global _nsfw_pool, _sfw_pool
    # Import lazily to avoid circular dependency
    try:
        from airi.commands import NSFW_COMMANDS as _nsfw_set
    except Exception:
        _nsfw_set = set()

    for raw_key, urls in gifs_data.items():
        base = raw_key
        for suffix in ("_male", "_female", "_lesbian", "_neutral", "_nb"):
            if raw_key.endswith(suffix):
                base = raw_key[: -len(suffix)]
                break
        # Filter obviously broken Discord CDN expiry links
        good_urls = [u for u in urls if "discord" not in u or "ex=" not in u]
        if not good_urls:
            good_urls = urls  # keep all if nothing survived the filter
        if base in _nsfw_set:
            _nsfw_pool.setdefault(base, []).extend(good_urls)
        else:
            _sfw_pool.setdefault(base, []).extend(good_urls)

# ── SFW API mappings ────────────────────────────────────────────────
NEKOSBEST_SFW: dict[str, str] = {
    "hug": "hug", "kiss": "kiss", "pat": "pat", "wave": "wave",
    "cuddle": "cuddle", "cry": "cry", "laugh": "laugh", "bored": "bored",
    "rage": "angry", "blush": "blush", "smile": "smile", "poke": "poke",
    "slap": "slap", "bite": "bite", "hi": "wave", "bye": "wave",
    "lol": "laugh", "sad": "cry", "shrug": "shrug", "yeet": "yeet",
    "punch": "punch", "kick": "kick", "stare": "stare", "nod": "nod",
    "wink": "wink", "happy": "happy", "sleep": "sleep", "think": "think",
    "run": "run", "shoot": "shoot", "handhold": "handhold", "tickle": "tickle",
    "nom": "nom", "peek": "stare", "shock": "blush", "tease": "tease",
    "facepalm": "facepalm", "pout": "pout", "cheer": "cheer", "dance": "dance",
}

NEKOSLIFE_SFW: dict[str, str] = {
    "hug": "hug", "kiss": "kiss", "pat": "pat", "poke": "poke",
    "slap": "slap", "tickle": "tickle", "cuddle": "cuddle", "cry": "cry",
    "blush": "blush", "wave": "wave", "smile": "smug", "lol": "happy",
    "nom": "nom", "sad": "cry", "handhold": "handhold", "nod": "nod",
    "wink": "wink", "dance": "dance", "hi": "wave", "bye": "wave",
    "rage": "slap", "shock": "blush", "bored": "bored", "peek": "stare",
    "yeet": "yeet", "shoot": "shoot",
}

WAIFUPICS_SFW: dict[str, str] = {
    "hug": "hug", "kiss": "kiss", "pat": "pat", "cuddle": "cuddle",
    "cry": "cry", "lol": "happy", "wave": "wave", "poke": "poke",
    "slap": "slap", "bite": "bite", "nom": "nom", "blush": "blush",
    "smile": "smile", "wink": "wink", "handhold": "handhold", "yeet": "yeet",
    "bonk": "bonk", "kick": "kick", "glomp": "glomp", "dance": "dance",
    "hi": "wave", "bye": "wave", "rage": "slap", "happy": "happy",
}

KLIPY_QUERIES: dict[str, str] = {
    "hug": "anime hug", "kiss": "anime kiss", "pat": "anime head pat",
    "poke": "anime poke", "bite": "anime bite", "lick": "anime lick",
    "slap": "anime slap", "punch": "anime punch", "wave": "anime wave",
    "cuddle": "anime cuddle", "hi": "anime hello", "bye": "anime goodbye",
    "lol": "anime laugh", "bored": "anime bored", "rage": "anime angry",
    "cry": "anime cry", "sad": "anime sad", "shrug": "anime shrug",
    "sip": "anime drink tea", "shock": "anime shocked", "tickle": "anime tickle",
    "nom": "anime nom", "peek": "anime peek", "watch": "anime stare",
    "sleep": "anime sleep", "wink": "anime wink", "dance": "anime dance",
    "smile": "anime smile", "blush": "anime blush", "laugh": "anime laugh",
    "tease": "anime tease", "facepalm": "anime facepalm", "pout": "anime pout",
    "cheer": "anime cheer", "nod": "anime nod", "stfu": "anime stare",
    "idiot": "anime annoyed", "fah": "anime shocked",
}

OTAKU_MAP: dict[str, str] = {
    "hug": "hug", "kiss": "kiss", "pat": "pat", "poke": "poke",
    "bite": "bite", "lick": "lick", "slap": "slap", "wave": "wave",
    "cuddle": "cuddle", "cry": "cry", "sad": "sad", "shrug": "shrug",
    "lol": "laugh", "bored": "bored", "rage": "triggered", "hi": "wave",
    "peek": "stare", "watch": "stare", "shock": "surprised",
    "wink": "wink", "dance": "dance", "smile": "smile", "blush": "blush",
    "nom": "nom",
}

# ── Klipy variety pool ─────────────────────────────────────────────
# Pre-fetch KLIPY_POOL_SIZE URLs per query, rotate through them so
# consecutive !hug calls all show different GIFs (>20 unique before repeating)
_klipy_pools: dict[str, list[str]] = {}
_klipy_used:  dict[str, set[str]]  = {}
KLIPY_POOL_SIZE = 25

# ── Cache ──────────────────────────────────────────────────────────
_cache: dict[str, tuple[str, datetime]] = {}
_TTL   = timedelta(seconds=30)

def _get_cache(key: str) -> str | None:
    e = _cache.get(key)
    if e and datetime.utcnow() - e[1] < _TTL:
        return e[0]
    return None

def _set_cache(key: str, url: str):
    _cache[key] = (url, datetime.utcnow())
    if len(_cache) > 600:
        old = sorted(_cache.items(), key=lambda x: x[1][1])[:100]
        for k, _ in old: _cache.pop(k, None)

# ── HTTP helper ────────────────────────────────────────────────────
async def _get(url: str, timeout: int = 6) -> dict | None:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=timeout),
                             headers={"User-Agent": REDDIT_UA}) as r:
                if r.status == 200:
                    return await r.json()
    except Exception:
        pass
    return None

# ── SFW fetchers ───────────────────────────────────────────────────
async def _nekosbest(category: str) -> str | None:
    ck = f"nb:{category}"
    if c := _get_cache(ck): return c
    data = await _get(f"https://nekos.best/api/v2/{category}")
    if data:
        results = data.get("results", [])
        if results:
            url = random.choice(results)["url"]
            _set_cache(ck, url); return url
    return None

async def _nekoslife(category: str) -> str | None:
    ck = f"nl:{category}"
    if c := _get_cache(ck): return c
    data = await _get(f"https://nekos.life/api/v2/img/{category}")
    if data:
        url = data.get("url")
        if url: _set_cache(ck, url); return url
    return None

async def _waifupics(category: str, nsfw: bool = False) -> str | None:
    ck = f"wp:{'nsfw' if nsfw else 'sfw'}:{category}"
    if c := _get_cache(ck): return c
    data = await _get(f"https://api.waifu.pics/{'nsfw' if nsfw else 'sfw'}/{category}")
    if data:
        url = data.get("url")
        if url: _set_cache(ck, url); return url
    return None

async def _klipy_pooled(query: str) -> str | None:
    """Pick a URL from a pre-fetched pool, ensuring >= KLIPY_POOL_SIZE variety."""
    pool = _klipy_pools.get(query, [])
    used = _klipy_used.setdefault(query, set())

    if not pool or (len(used) >= len(pool)):
        # Refill
        data = await _get(
            f"https://api.klipy.com/api/v1/{KLIPY_KEY}/gifs/search"
            f"?q={query}&limit={KLIPY_POOL_SIZE}"
        )
        if data:
            items = data.get("data", {}).get("data", [])
            urls = []
            for item in items:
                try:
                    u = item["file"]["md"]["gif"]["url"]
                    if u: urls.append(u)
                except (KeyError, TypeError):
                    pass
            if urls:
                _klipy_pools[query] = urls
                pool = urls
                used.clear()

    if not pool:
        return None

    fresh = [u for u in pool if u not in used]
    if not fresh:
        used.clear()
        fresh = pool

    url = random.choice(fresh)
    used.add(url)
    return url

async def _otaku(reaction: str) -> str | None:
    data = await _get(f"https://api.otakugifs.xyz/gif?reaction={reaction}")
    return data.get("url") if data else None

# ── Public: SFW ────────────────────────────────────────────────────
async def get_sfw_gif(command: str) -> tuple[str | None, str]:
    """Priority: gifs.json SFW pool → nekos.best → nekos.life → waifu.pics → Klipy → otakugifs"""
    urls = _sfw_pool.get(command, [])
    if urls:
        return random.choice(urls), "local"

    cat = NEKOSBEST_SFW.get(command)
    if cat:
        url = await _nekosbest(cat)
        if url: return url, "nekos.best"

    cat = NEKOSLIFE_SFW.get(command)
    if cat:
        url = await _nekoslife(cat)
        if url: return url, "nekos.life"

    cat = WAIFUPICS_SFW.get(command)
    if cat:
        url = await _waifupics(cat, False)
        if url: return url, "waifu.pics"

    query = KLIPY_QUERIES.get(command, f"anime {command}")
    url = await _klipy_pooled(query)
    if url: return url, "Klipy"

    reaction = OTAKU_MAP.get(command)
    if reaction:
        url = await _otaku(reaction)
        if url: return url, "OtakuGIFs"

    return None, ""

# ── Public: NSFW ───────────────────────────────────────────────────
async def get_nsfw_gif(command: str) -> tuple[str | None, str]:
    """Primary: gifs.json pool. Fallback: Klipy."""
    urls = _nsfw_pool.get(command, [])
    if urls:
        return random.choice(urls), "local"

    # Klipy fallback — generic query
    url = await _klipy_pooled(f"anime {command}")
    if url: return url, "Klipy"

    return None, ""

# ── Public: unified entry point ─────────────────────────────────────
async def get_gif(command: str, is_nsfw: bool) -> tuple[str | None, str]:
    if is_nsfw:
        url, src = await get_nsfw_gif(command)
        if url: return url, src
        url, src = await get_sfw_gif(command)
        if url: return url, src
    else:
        url, src = await get_sfw_gif(command)
        if url: return url, src
    return None, ""

# ── Public: Klipy free-text search (for !gifsearch) ────────────────
async def klipy_search(query: str, count: int = 8) -> list[str]:
    """
    Return up to `count` varied GIF URLs for a search query.
    Fetches KLIPY_POOL_SIZE, shuffles, returns first `count`.
    Subsequent calls with the same query will return different GIFs.
    """
    data = await _get(
        f"https://api.klipy.com/api/v1/{KLIPY_KEY}/gifs/search"
        f"?q={query}&limit={max(count, KLIPY_POOL_SIZE)}"
    )
    if not data:
        return []
    items = data.get("data", {}).get("data", [])
    urls = []
    for item in items:
        try:
            u = item["file"]["md"]["gif"]["url"]
            if u: urls.append(u)
        except (KeyError, TypeError):
            pass
    random.shuffle(urls)
    return urls[:count]
