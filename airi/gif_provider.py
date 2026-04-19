# airi/gif_provider.py
# GIF provider — Live Klipy search as primary source.
# gifs.json kept as optional NSFW fallback only.
# SFW chain: nekos.best → nekos.life → waifu.pics → Klipy
# NSFW chain: Klipy search → gifs.json pool → waifu.pics nsfw
import aiohttp
import random
import os
from datetime import datetime, timedelta, timezone

KLIPY_KEY = os.getenv("KLIPY_API_KEY", "1bhaiaxUnVFAr4JpBsWgAMOv3Z12Noyx0R2DVuqrKRJeDmalZuKiaLJC6AOkRFJ8")
REDDIT_UA  = "AiriBot/2.0 (Discord bot)"

# ── gifs.json pools (NSFW fallback, loaded at startup) ─────────────
_nsfw_pool: dict[str, list[str]] = {}

# ── Anti-repeat GIF tracking ─────────────────────────────────────
# Maps (user_id, command) -> last gif URL to prevent consecutive repeats
_last_gif_cache: dict[tuple, str] = {}

def _pick_no_repeat(urls: list[str], user_id: int | None, cmd: str) -> str | None:
    """Pick a random URL that wasn't the last one used by this user for this command."""
    if not urls:
        return None
    if len(urls) == 1:
        return urls[0]
    key     = (user_id, cmd)
    last    = _last_gif_cache.get(key)
    choices = [u for u in urls if u != last] or urls
    picked  = random.choice(choices)
    _last_gif_cache[key] = picked
    # Trim cache to 200 entries to avoid unbounded memory growth
    if len(_last_gif_cache) > 200:
        oldest = list(_last_gif_cache.keys())[:100]
        for k in oldest:
            _last_gif_cache.pop(k, None)
    return picked

def load_gifs_pool(gifs_data: dict):
    """
    Load gifs.json into _nsfw_pool only (the json is now NSFW-only).
    SFW commands use live APIs instead.
    """
    global _nsfw_pool
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
        good_urls = [u for u in urls if "discord" not in u or "ex=" not in u]
        if not good_urls:
            good_urls = urls
        _nsfw_pool.setdefault(base, []).extend(good_urls)

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
    "spank": "slap", "bang": "shoot",
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
    "hi": "hug", "bye": "wave", "rage": "slap", "happy": "happy",
}

# Klipy query map — used for BOTH SFW and NSFW via live search
KLIPY_QUERIES: dict[str, str] = {
    # SFW actions
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
    "cheer": "anime cheer", "nod": "anime nod", "stare": "anime stare",
    "spank": "anime spank", "shoot": "anime gun", "bang": "anime shoot",
    "crym": "anime cry male", "fapm": "anime alone embarrassed",
    # NSFW actions — Klipy handles these as keyword searches
    "fuck": "anime sex", "bfuck": "anime doggy style",
    "dickride": "anime riding", "anal": "anime anal",
    "bathroomfuck": "anime quickie", "bondage": "anime bondage",
    "blowjob": "anime blowjob", "kuni": "anime oral",
    "pussyeat": "anime oral", "lickdick": "anime oral",
    "titjob": "anime paizuri", "threesome": "anime threesome",
    "gangbang": "anime gangbang", "fap": "anime solo",
    "grabbutts": "anime grope", "grabboobs": "anime grope",
    "grind": "anime grind", "feet": "anime feet",
    "finger": "anime fingering", "69": "anime 69",
    "cum": "anime cum", "cum_male": "anime cum",
    "fuck_lesbian": "anime lesbian", "grope": "anime grope",
}

OTAKU_MAP: dict[str, str] = {
    "hug": "hug", "kiss": "kiss", "pat": "pat", "poke": "poke",
    "bite": "bite", "lick": "lick", "slap": "slap", "wave": "wave",
    "cuddle": "cuddle", "cry": "cry", "sad": "sad", "shrug": "shrug",
    "lol": "laugh", "bored": "bored", "rage": "triggered",
    "peek": "stare", "watch": "stare", "shock": "surprised",
    "wink": "wink", "dance": "dance", "smile": "smile", "blush": "blush",
    "nom": "nom",
}

# ── Klipy pool cache (per query, ~25 varied URLs) ──────────────────
_klipy_pools: dict[str, list[str]] = {}
_klipy_used:  dict[str, set[str]]  = {}
KLIPY_POOL_SIZE = 25

# ── Short-lived cache ──────────────────────────────────────────────
_cache: dict[str, tuple[str, datetime]] = {}
_TTL   = timedelta(seconds=30)

def _get_cache(key: str) -> str | None:
    e = _cache.get(key)
    if e and datetime.now(timezone.utc) - e[1] < _TTL:
        return e[0]
    return None

def _set_cache(key: str, url: str):
    _cache[key] = (url, datetime.now(timezone.utc))
    if len(_cache) > 600:
        old = sorted(_cache.items(), key=lambda x: x[1][1])[:100]
        for k, _ in old: _cache.pop(k, None)

# ── HTTP helper ────────────────────────────────────────────────────
async def _get(url: str, timeout: int = 8) -> dict | None:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                url,
                timeout=aiohttp.ClientTimeout(total=timeout),
                headers={"User-Agent": REDDIT_UA},
            ) as r:
                if r.status == 200:
                    return await r.json()
    except Exception:
        pass
    return None

# ── SFW API fetchers ───────────────────────────────────────────────
async def _nekosbest(category: str) -> str | None:
    ck = f"nb:{category}"
    if c := _get_cache(ck): return c
    data = await _get(f"https://nekos.best/api/v2/{category}")
    if data:
        results = data.get("results", [])
        if results:
            url = random.choice(results)["url"]
            _set_cache(ck, url)
            return url
    return None

async def _nekoslife(category: str) -> str | None:
    ck = f"nl:{category}"
    if c := _get_cache(ck): return c
    data = await _get(f"https://nekos.life/api/v2/img/{category}")
    if data:
        url = data.get("url")
        if url:
            _set_cache(ck, url)
            return url
    return None

async def _waifupics(category: str, nsfw: bool = False) -> str | None:
    ck = f"wp:{'nsfw' if nsfw else 'sfw'}:{category}"
    if c := _get_cache(ck): return c
    data = await _get(f"https://api.waifu.pics/{'nsfw' if nsfw else 'sfw'}/{category}")
    if data:
        url = data.get("url")
        if url:
            _set_cache(ck, url)
            return url
    return None

async def _klipy_pooled(query: str) -> str | None:
    """Pick a varied URL from pool, refill when exhausted."""
    pool = _klipy_pools.get(query, [])
    used = _klipy_used.setdefault(query, set())

    if not pool or len(used) >= len(pool):
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
async def get_sfw_gif(command: str, user_id: int | None = None) -> tuple[str | None, str]:
    """SFW priority: nekos.best → nekos.life → waifu.pics → Klipy → OtakuGIFs. gifs.json is NOT used for SFW."""
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
async def get_nsfw_gif(command: str, user_id: int | None = None) -> tuple[str | None, str]:
    """
    NSFW priority: gifs.json pool (local) → Klipy live search → waifu.pics nsfw.
    gifs.json is the primary source because it contains curated NSFW content.
    Klipy acts as a fallback for commands not in the json or when the pool is empty.
    """
    # 1. gifs.json pool — primary NSFW source
    urls = _nsfw_pool.get(command, [])
    if urls:
        return random.choice(urls), "local"

    # 2. Klipy live search — fallback for commands not in gifs.json
    query = KLIPY_QUERIES.get(command, f"anime {command}")
    url = await _klipy_pooled(query)
    if url: return url, "Klipy"

    # 3. waifu.pics nsfw — last resort
    nsfw_wp_map = {
        "blowjob": "blowjob", "fuck": "fuck", "anal": "anal",
        "kuni": "neko", "cum": "cum", "bondage": "bondage",
    }
    cat = nsfw_wp_map.get(command)
    if cat:
        url = await _waifupics(cat, True)
        if url: return url, "waifu.pics"

    return None, ""

# ── Public: unified entry point ─────────────────────────────────────
async def get_gif(command: str, is_nsfw: bool, user_id: int | None = None) -> tuple[str | None, str]:
    """Get a GIF, ensuring no consecutive repeat for the same user+command pair."""
    if is_nsfw:
        url, src = await get_nsfw_gif(command, user_id=user_id)
        if url: return url, src
        url, src = await get_sfw_gif(command, user_id=user_id)
        if url: return url, src
    else:
        url, src = await get_sfw_gif(command, user_id=user_id)
        if url: return url, src
    return None, ""

# ── Public: Klipy free-text search (for !gifsearch and Goonneffa) ──
async def klipy_search(query: str, count: int = 8) -> list[str]:
    """
    Return up to `count` varied GIF URLs for a search query.
    Used by !gifsearch and Goonneffa's watch/spy commands.
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
