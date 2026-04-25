# airi/gif_provider.py
# Auto-detects ALL commands from actio.ACTIONS — no manual registration needed.
# To add a new command: add it to actio.py ACTIONS dict, done.
# NSFW classification is still handled by NSFW_COMMANDS in commands.py.
#
# SFW priority:  nekos.best → nekos.life → waifu.pics → Klipy ("anime {cmd}") → OtakuGIFs
# NSFW priority: gifs.json pool → Klipy live → waifu.pics nsfw
#
import random
import aiohttp
import json
import os

# ── Anti-repeat GIF tracking ──────────────────────────────────────
_last_gif_cache: dict[tuple, str] = {}
_nsfw_pool:      dict[str, list[str]] = {}

def _pick_no_repeat(urls: list[str], user_id: int | None, cmd: str) -> str | None:
    if not urls: return None
    if len(urls) == 1: return urls[0]
    key    = (user_id, cmd)
    last   = _last_gif_cache.get(key)
    pool   = [u for u in urls if u != last] or urls
    picked = random.choice(pool)
    _last_gif_cache[key] = picked
    if len(_last_gif_cache) > 200:
        for k in list(_last_gif_cache.keys())[:100]: _last_gif_cache.pop(k, None)
    return picked


# ── gifs.json NSFW pool loader ────────────────────────────────────
def load_gifs_pool(gifs_data: dict):
    """Call once at startup with the parsed gifs.json dict."""
    global _nsfw_pool
    for raw_key, items in gifs_data.items():
        base = raw_key.split("/")[-1].split(".")[0].lower()
        try:
            from airi.commands import NSFW_COMMANDS as _nsfw_set
            if base not in _nsfw_set: continue
        except ImportError:
            pass
        good_urls = []
        for item in (items if isinstance(items, list) else [items]):
            url_val = item.get("url") if isinstance(item, dict) else str(item)
            if url_val: good_urls.append(url_val)
        _nsfw_pool.setdefault(base, []).extend(good_urls)


# ── API-specific GIF fetchers ─────────────────────────────────────
async def _get(url, params=None, headers=None, timeout=8) -> dict | None:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params=params, headers=headers,
                             timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                if r.status == 200: return await r.json(content_type=None)
    except Exception: pass
    return None

async def _nekosbest(category: str) -> str | None:
    data = await _get(f"https://nekos.best/api/v2/{category}")
    try: return data["results"][0]["url"]
    except: return None

async def _nekoslife(category: str) -> str | None:
    data = await _get(f"https://nekos.life/api/v2/img/{category}")
    try: return data["url"]
    except: return None

async def _waifupics(category: str, nsfw: bool) -> str | None:
    kind = "nsfw" if nsfw else "sfw"
    data = await _get(f"https://api.waifu.pics/{kind}/{category}")
    try: return data["url"]
    except: return None

_klipy_pool: dict[str, list[str]] = {}

async def _klipy_pooled(query: str) -> str | None:
    from config import KLIPY_API_KEY
    if query not in _klipy_pool:
        data = await _get(
            "https://api.klipy.com/v1/reactions/search",
            params={"q": query, "per_page": 15},
            headers={"Authorization": f"Bearer {KLIPY_API_KEY}"},
        )
        results = []
        try:
            for item in data.get("data",{}).get("clips",[]):
                url = item.get("clip",{}).get("url") or item.get("url")
                if url: results.append(url)
        except: pass
        _klipy_pool[query] = results
    urls = _klipy_pool.get(query,[])
    return random.choice(urls) if urls else None

async def _otaku(reaction: str) -> str | None:
    data = await _get(f"https://api.otakugifs.xyz/gif?reaction={reaction}")
    try: return data["url"]
    except: return None


# ── Service maps (SFW only — extend freely, or let fallback handle it) ───
NEKOSBEST_SFW: dict[str, str] = {
    "hug":"hug","pat":"pat","kiss":"kiss","cuddle":"cuddle","wave":"wave",
    "poke":"poke","slap":"nod","laugh":"laugh","cry":"cry","blush":"blush",
    "bite":"bite","dance":"dance","smile":"smile","wink":"wink","nod":"nod",
    "thumbsup":"thumbsup","shoot":"shoot","sleep":"sleep",
}
NEKOSLIFE_SFW: dict[str, str] = {
    "tickle":"tickle","smug":"smug","baka":"baka","think":"think",
    "hug":"hug","kiss":"kiss","pat":"pat","poke":"poke","cuddle":"cuddle",
}
WAIFUPICS_SFW: dict[str, str] = {
    "hug":"hug","kiss":"kiss","pat":"pat","cuddle":"cuddle","slap":"slap",
    "kick":"kick","poke":"poke","cry":"cry","blush":"blush","dance":"dance",
    "smile":"smile","wave":"wave","wink":"wink","nod":"nod","nom":"nom",
    "bite":"bite","happy":"happy","laugh":"laugh","lick":"lick",
}
OTAKU_MAP: dict[str, str] = {
    "cry":"cry","laugh":"laugh","blush":"blush","wave":"wave","smile":"smile",
    "happy":"happy","wink":"wink","pat":"pat","hug":"hug","kiss":"kiss",
}


# ── Auto-fallback query builder ───────────────────────────────────
def _klipy_query(command: str) -> str:
    """Build a sensible Klipy query for any command automatically.
    No manual registration needed — this covers any cmd added to actio.py."""
    overrides: dict[str, str] = {
        "fuck":        "anime sex hentai",
        "bfuck":       "anime doggy hentai",
        "blowjob":     "anime blowjob hentai",
        "dickride":    "anime riding hentai",
        "anal":        "anime anal hentai",
        "kuni":        "anime cunnilingus hentai",
        "pussyeat":    "anime pussy eating hentai",
        "titjob":      "anime titjob paizuri hentai",
        "threesome":   "anime threesome hentai",
        "gangbang":    "anime gangbang hentai",
        "fap":         "anime masturbate hentai",
        "cum":         "anime cum hentai",
        "finger":      "anime fingering hentai",
        "grind":       "anime grinding hentai",
        "bathroomfuck":"anime sex quickie hentai",
        "bondage":     "anime bondage hentai",
        "spank":       "anime spanking hentai",
        "grabbutts":   "anime ass groping",
        "grabboobs":   "anime boob groping",
        "69":          "anime 69 hentai",
        "footjob":     "anime footjob",
        "squirt":      "anime squirt hentai",
        "footlick":    "anime foot lick",
        "cum_male":    "anime cum facial",
        "fuck_lesbian":"anime lesbian sex hentai",
    }
    return overrides.get(command, f"anime {command}")


# ── Public SFW entry point ────────────────────────────────────────
async def get_sfw_gif(command: str, user_id: int | None = None) -> tuple[str | None, str]:
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

    # ── Auto-fallback: any command in actio.py gets a Klipy search ──
    query = _klipy_query(command)
    url = await _klipy_pooled(query)
    if url: return url, "Klipy"

    reaction = OTAKU_MAP.get(command)
    if reaction:
        url = await _otaku(reaction)
        if url: return url, "OtakuGIFs"

    return None, ""


# ── Public NSFW entry point ───────────────────────────────────────
async def get_nsfw_gif(command: str, user_id: int | None = None) -> tuple[str | None, str]:
    # 1. gifs.json curated pool
    urls = _nsfw_pool.get(command, [])
    if urls:
        url = _pick_no_repeat(urls, user_id, command)
        if url: return url, "local"

    # 2. Klipy — auto-query for any command
    query = _klipy_query(command)
    url = await _klipy_pooled(query)
    if url: return url, "Klipy"

    # 3. waifu.pics nsfw fallback
    nsfw_wp = {
        "blowjob":"blowjob","fuck":"fuck","anal":"anal","kuni":"neko",
        "cum":"cum","bondage":"bondage","fap":"masturbation",
    }
    cat = nsfw_wp.get(command)
    if cat:
        url = await _waifupics(cat, True)
        if url: return url, "waifu.pics"

    return None, ""


# ── Unified entry ─────────────────────────────────────────────────
async def get_gif(
    command: str,
    is_nsfw: bool,
    user_id: int | None = None,
) -> tuple[str | None, str]:
    if is_nsfw:
        url, src = await get_nsfw_gif(command, user_id=user_id)
        if url: return url, src
        url, src = await get_sfw_gif(command, user_id=user_id)
        if url: return url, src
    else:
        url, src = await get_sfw_gif(command, user_id=user_id)
        if url: return url, src
    return None, ""
