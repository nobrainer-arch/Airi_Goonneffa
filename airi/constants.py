# airi/constants.py — Centralised constants and emoji definitions
# Edit KAKERA_EMOJI once you've uploaded the gem emoji to your server.
# Format: "<:name:id>" — get it by typing \:gemname: in Discord.

KAKERA_EMOJI = "💎"          # Replace with <:gem:YOUR_ID> once uploaded
KAKERA_NAME  = "Gems"        # Display name (shown in help text)

# Rarity display system (hue/stars/aura for card visuals)
RARITY_STYLE: dict[str, dict] = {
    "common":    {"stars": "★☆☆☆☆", "hue": "⚪", "glow": "",      "aura": "Faint Glow",      "color": 0x808080},
    "rare":      {"stars": "★★☆☆☆", "hue": "🔵", "glow": "✧",     "aura": "Ocean Pulse",     "color": 0x3498db},
    "epic":      {"stars": "★★★☆☆", "hue": "🟣", "glow": "✨",     "aura": "Mystic Aura",     "color": 0x9b59b6},
    "legendary": {"stars": "★★★★☆", "hue": "🟡", "glow": "🌟",     "aura": "Golden Radiance", "color": 0xf1c40f},
    "mythic":    {"stars": "★★★★★", "hue": "🌈", "glow": "💫",     "aura": "Cosmic Energy",   "color": 0xff66ff},
}

# Personality trait tags (used in card display)
PERSONALITY_TAGS = [
    "Tsundere 😤", "Yandere 🔪", "Kuudere ❄️", "Deredere 💕",
    "Dandere 🌸", "Onee-san 👑", "Genki ⚡", "Shy 🙈",
    "Mysterious 🌙", "Fierce 🔥", "Gentle 🌿", "Playful 🎮",
]

# Card wrap types (cosmetic skins for cards)
CARD_WRAPS: dict[str, dict] = {
    "default":   {"emoji": "🃏", "bonus": "",               "color": None},
    "valentine": {"emoji": "💝", "bonus": "+5% affection",  "color": 0xff69b4},
    "gothic":    {"emoji": "🖤", "bonus": "+5% kakera",     "color": 0x1a1a1a},
    "galaxy":    {"emoji": "🌌", "bonus": "+5% lucky pull", "color": 0x0d1b2a},
    "champion":  {"emoji": "🏆", "bonus": "Gold aura",      "color": 0xffd700},
}

# Flavour text pool for cards
CARD_FLAVOUR: list[str] = [
    "I won't lose to you.",      "Stay by my side forever.",
    "This feeling… is it love?", "Don't look at me like that!",
    "I'll protect you, always.", "You're mine now. Got it?",
    "Hmph. You're not so bad.",  "Don't make me blush, idiot.",
    "I need you more than air.", "One day I'll tell you the truth.",
    "Your smile is my weakness.", "Promise me you won't leave.",
    "You called? I was busy…",   "I refuse to admit I missed you.",
    "Standing by your side is enough.", "Let's stay like this a little longer.",
]
