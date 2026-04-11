# config.py
import os
from dotenv import load_dotenv

load_dotenv()

# ── Tokens ────────────────────────────────────────────────────────
AIRI_TOKEN = os.getenv("AIRI_TOKEN")
GOON_TOKEN = os.getenv("GOON_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# ── IDs ───────────────────────────────────────────────────────────
AIRI_ID       = int(os.getenv("AIRI_ID", 0))
GOON_ID       = int(os.getenv("GOON_ID", 0))
IGNORED_USERS = {GOON_ID, AIRI_ID}
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", 0))

# ── API Keys ──────────────────────────────────────────────────────
KLIPY_API_KEY = os.getenv("KLIPY_API_KEY", "1bhaiaxUnVFAr4JpBsWgAMOv3Z12Noyx0R2DVuqrKRJeDmalZuKiaLJC6AOkRFJ8")

# ── Limits ────────────────────────────────────────────────────────
EMPTY_LIMIT = 2
SPAM_LIMIT  = 5
TIMEOUT_DURATION          = 3600
BADWORD_TIMEOUT_THRESHOLD = 5
GOONNEFFA_COOLDOWN        = 300

# ── Commands that work without a target (solo mode) ───────────────
SOLO_COMMANDS = {
    "cry", "sip", "sad", "shrug", "crym", "cumm", "fapm",
    "peek", "watch", "lol", "bored", "rage", "fap", "shock",
}

MULTI_TARGET_COMMANDS = {
    "gangbang":  3,
    "threesome": 2,
}

# ── Commands that support "X back" button ─────────────────────────
BACK_ACTIONS: dict[str, tuple[str, str]] = {
    "hug":    ("hug",    "🤗 Hug back"),
    "kiss":   ("kiss",   "💋 Kiss back"),
    "pat":    ("pat",    "🤚 Pat back"),
    "poke":   ("poke",   "👉 Poke back"),
    "bite":   ("bite",   "😈 Bite back"),
    "wave":   ("wave",   "👋 Wave back"),
    "lick":   ("lick",   "👅 Lick back"),
    "slap":   ("slap",   "👋 Slap back"),
    "spank":  ("spank",  "🔥 Spank back"),
    "cuddle": ("cuddle", "🤗 Cuddle back"),
}

# ── Aliases  ─  edit freely, no need to touch command files ───────
ALIASES: dict[str, list[str]] = {
    "hug":          ["snuggle", "embrace", "bearhug"],
    "peek":         ["spy", "stalk", "glance", "peep"],
    "hi":           ["hello", "hey", "sup", "yo"],
    "bye":          ["goodbye", "farewell", "bai", "cya", "byebye"],
    "watch":        ["stare", "observe", "gaze"],
    "lol":          ["lmao", "rofl", "haha", "kek"],
    "bored":        ["bore", "meh", "yawn"],
    "rage":         ["mad", "angry", "furious"],
    "sip":          ["tea", "drink", "siptea"],
    "kuni":         ["nuzzle", "kissies"],
    "pat":          ["headpat", "patpat", "pats"],
    "cry":          ["sob", "tear", "crying"],
    "sad":          ["depressed", "gloomy"],
    "shrug":        ["idk", "whatever", "dunno"],
    "poke":         ["boop", "prod"],
    "lick":         ["tongue", "licky"],
    "grabbutts":    ["grabass", "assgrab", "gropass", "bootygrab"],
    "grabboobs":    ["boobgrab", "titgrab", "gropetits", "feelboobs"],
    "grind":        ["dryhump", "hump", "grinding"],
    "cuddle":       ["spoon"],
    "bondage":      ["tie", "bind", "restrain", "rope", "bdsm"],
    "spank":        ["slapass", "smack", "spanking", "spanks"],
    "blowjob":      ["bj", "head", "fellatio", "blow", "oral","sucking"],
    "pussyeat":     ["eat", "eatout", "munch", "cunnilingus", "lickpussy"],
    "titjob":       ["titfuck", "boobjob", "paizuri", "titf"],
    "fuck":         ["sex", "screw", "smash", "rail", "pound", "plow", "dick"],
    "dickride":     ["ride", "cowgirl", "reversecowgirl", "riding"],
    "bfuck":        ["doggy", "backshots", "frombehind", "dogstyle"],
    "anal":         ["buttsex", "analsex", "backdoor", "assfuck", "buttfuck"],
    "bathroomfuck": ["quickie", "bathroom", "stallfuck", "restroom"],
    "bang":         ["shoot", "banging"],
    "cum":          ["finish", "nut", "creampie", "cumshot"],
    "69":           ["sixtynine", "69ing", "mutualoral"],
    "threesome":    ["3some", "threesum", "3sum"],
    "gangbang":     ["gb", "gang", "train", "runatrain"],
    "fap":          ["masturbate", "jerkoff", "jackoff", "wank", "fapfap", "touchself"],
    "kiss":         ["smooch", "peck", "makeout"],
    "footjob":      ["footfuck"]
}

# ── Flat alias lookup: alias → canonical name ─────────────────────
ALIASES_FLAT: dict[str, str] = {
    alias: cmd for cmd, aliases in ALIASES.items() for alias in aliases
}
