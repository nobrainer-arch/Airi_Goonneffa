# airi/rpg/classes.py — Class definitions, talents, base stats
# Source: Kinfang manhwa mechanics

RANK_ORDER = ["F","E","D","C","B","A","S","SS","SSS","Unknown"]
RANK_COLORS = {
    "F":0x808080,"E":0x95a5a6,"D":0x27ae60,"C":0x2980b9,
    "B":0x8e44ad,"A":0xf39c12,"S":0xe74c3c,"SS":0xff6b35,
    "SSS":0xffd700,"Unknown":0x5d5d8a,
}
RANK_EMOJI = {
    "F":"⬜","E":"🟩","D":"🟦","C":"🔵","B":"🟣",
    "A":"🟠","S":"🔴","SS":"🌟","SSS":"💫","Unknown":"❓",
}

REALMS = [
    ("Apprentice",    1,  10, "🌱"),
    ("Disciple",     11,  25, "⚔️"),
    ("Middle Stage", 26,  50, "🔥"),
    ("Late Stage",   51,  75, "⚡"),
    ("Peak",         76,  99, "🌙"),
    ("Transcendent",100, 999, "✨"),
]

STR_TIERS = [
    (1000,"Transcendent"),(500,"Formidable"),(200,"Mighty"),
    (100,"Powerful"),(50,"Overwhelming Vigour"),(25,"Average"),(0,"Weak"),
]

def get_realm(level: int) -> tuple[str, str]:
    for name, lo, hi, emoji in REALMS:
        if lo <= level <= hi: return name, emoji
    return "Transcendent", "✨"

def str_label(value: int) -> str:
    for threshold, label in STR_TIERS:
        if value >= threshold: return label
    return "Weak"

# ── Class definitions ─────────────────────────────────────────
CLASSES: dict[str, dict] = {
    "Shadow": {
        "emoji": "🌑", "color": 0x2c3e50,
        "desc": "Highest hidden assassin class. Exceptional agility and stealth. Undetectable by monsters.",
        "base": {"str": 12, "con": 10, "agi": 20, "spi": 8, "hp": 80, "mana": 60,
                 "reaction": 15, "crit_chance": 0.15, "damage_reduction": 0.05},
        "bonus": {"str": 3, "con": 3, "agi": 8, "spi": 5},
        "talent_name": "Authority of Judgment",
        "talent_rank": "Unknown",
        "passive": "Stealth is undetectable by monsters. Stealth duration ×2. First attack from stealth ×1.9 damage.",
        "active": "**Shadow Form** — become completely invisible for 16s. All attacks from stealth ×2.5. (Cooldown: 30min)",
        "hidden_class": True,
        "starting_skills": [("Shadow Sneak","B"),("Backstab","C"),("Stealth Mastery","B")],
    },
    "Necromancer": {
        "emoji": "⚰️", "color": 0x6a0dad,
        "desc": "Master of death and dark arts. High Spirit and Mana.",
        "base": {"str": 8, "con": 8, "agi": 7, "spi": 18, "hp": 70, "mana": 120,
                 "reaction": 8, "crit_chance": 0.05, "damage_reduction": 0.03},
        "bonus": {},
        "talent_name": "Death's Embrace",
        "talent_rank": "Unknown",
        "passive": "First skill used each battle deals **+15% bonus damage**.",
        "active": "**Corpse Summon** — raise a skeletal ally for 2 turns. Cost: 40 mana.",
        "starting_skills": [("Skeleton Summoning Magic","F"),("Withering Magic","B")],
    },
    "Warrior": {
        "emoji": "⚔️", "color": 0xe74c3c,
        "desc": "Frontline powerhouse. Exceptional Strength and Constitution.",
        "base": {"str": 18, "con": 15, "agi": 8, "spi": 3, "hp": 130, "mana": 30,
                 "reaction": 10, "crit_chance": 0.08, "damage_reduction": 0.10},
        "bonus": {},
        "talent_name": "Berserker's Soul",
        "talent_rank": "A",
        "passive": "When HP < 30%, Strength ×1.5.",
        "active": "**Berserk** — STR ×3 for 1 turn, take 2× damage. Cost: 20 mana.",
        "starting_skills": [("Heavy Strike","D"),("War Cry","C")],
    },
    "Mage": {
        "emoji": "🔮", "color": 0x3498db,
        "desc": "Arcane power. The Brave Heart talent is truly one-of-a-kind.",
        "base": {"str": 6, "con": 5, "agi": 8, "spi": 20, "hp": 60, "mana": 150,
                 "reaction": 9, "crit_chance": 0.08, "damage_reduction": 0.02},
        "bonus": {},
        "talent_name": "Brave Heart of a Mage",
        "talent_rank": "Unknown",
        "passive": "Gain free stat points after killing an enemy with a physical attack.",
        "active": "**Attribute Equalize** — set all stats equal to highest stat. (Cooldown: 24h · Unique)",
        "restriction": "Before using: only one stat can be selected as the reference value.",
        "starting_skills": [("Fireball","C"),("Mana Shield","D")],
    },
    "Archer": {
        "emoji": "🏹", "color": 0x27ae60,
        "desc": "Swift and lethal. High Agility with natural critical hit talent.",
        "base": {"str": 12, "con": 7, "agi": 18, "spi": 5, "hp": 80, "mana": 50,
                 "reaction": 14, "crit_chance": 0.20, "damage_reduction": 0.04},
        "bonus": {},
        "talent_name": "Eagle Eye",
        "talent_rank": "B",
        "passive": "+20% critical hit chance.",
        "active": "**Aimed Shot** — guaranteed critical, cannot miss. Cost: 30 mana.",
        "starting_skills": [("Multi-Arrow","D"),("Wind Step","C")],
    },
    "Gunman": {
        "emoji": "🔫", "color": 0xf39c12,
        "desc": "Lightning-fast gunslinger. First attack always devastating.",
        "base": {"str": 14, "con": 7, "agi": 16, "spi": 5, "hp": 85, "mana": 50,
                 "reaction": 13, "crit_chance": 0.12, "damage_reduction": 0.05},
        "bonus": {},
        "talent_name": "Brave Heart of a Gunman",
        "talent_rank": "A",
        "passive": "First attack each battle deals ×1.5 damage (like Ring of Skeleton King, built-in).",
        "active": "**Rapid Fire** — attack 3 times in one turn. Cost: 35 mana.",
        "starting_skills": [("Bullet Rain","C"),("Quick Draw","B")],
    },
    "Knight": {
        "emoji": "🛡️", "color": 0x95a5a6,
        "desc": "Impenetrable defender. Highest Constitution, powerful counter-attack.",
        "base": {"str": 12, "con": 20, "agi": 5, "spi": 5, "hp": 150, "mana": 20,
                 "reaction": 12, "crit_chance": 0.05, "damage_reduction": 0.20},
        "bonus": {},
        "talent_name": "Iron Wall",
        "talent_rank": "S",
        "passive": "Reflect 10% of damage received back to attacker.",
        "active": "**Shield Bash** — stun enemy 1 turn, unblockable. Cost: 15 mana.",
        "starting_skills": [("Taunt","C"),("Counter Strike","B")],
    },
    "Healer": {
        "emoji": "💚", "color": 0x1abc9c,
        "desc": "Life-sustaining support. High Spirit and constant regeneration.",
        "base": {"str": 5, "con": 8, "agi": 10, "spi": 22, "hp": 70, "mana": 130,
                 "reaction": 11, "crit_chance": 0.05, "damage_reduction": 0.06},
        "bonus": {},
        "talent_name": "Light's Touch",
        "talent_rank": "A",
        "passive": "Recover 5% max HP every 2 turns.",
        "active": "**Holy Heal** — restore 40% max HP. Cost: 50 mana.",
        "starting_skills": [("Heal","C"),("Blessing","B")],
    },
}
