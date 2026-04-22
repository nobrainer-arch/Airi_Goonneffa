# airi/rpg/classes.py — Class + Realm system
# Scaled from Disastrous Necromancer manhwa mechanics
# Level 1 chars start with ~100 in primary stat
# Level 10 mobs: 800 STR (goblin guard in transcript)
# Level 16 boss: 3000 STR/HP (Goblin King)
# Each level grants 10 base stat points (20 after level 11 in the manhwa)

RANK_ORDER  = ["F","E","D","C","B","A","S","SS","SSS","Unknown"]
RANK_COLORS = {
    "F":0x808080,"E":0x95a5a6,"D":0x27ae60,"C":0x2980b9,
    "B":0x8e44ad,"A":0xf39c12,"S":0xe74c3c,"SS":0xff6b35,
    "SSS":0xffd700,"Unknown":0x5d5d8a,
}
RANK_EMOJI = {
    "F":"⬜","E":"🟩","D":"🟦","C":"🔵","B":"🟣",
    "A":"🟠","S":"🔴","SS":"🌟","SSS":"💫","Unknown":"❓",
}

# ── Realm progression (from manhwa) ───────────────────────────────
# Levels 1-10: Apprentice (class-transfer range, newbie dungeons)
# Levels 11-25: Disciple (level 12 = Xiyang Academy entry)  
# Levels 26-50: Middle Stage (level 20 dungeons, guild territory)
# Levels 51-75: Late Stage (major arenas, dangerous territory)
# Levels 76-99: Peak (elite adventurers, level 80 divine mage level)
# Level 100+: Transcendent (divine-level, like the Dragon God)
REALMS = [
    ("Apprentice",     1,  10, "🌱"),
    ("Disciple",      11,  25, "⚔️"),
    ("Middle Stage",  26,  50, "🔥"),
    ("Late Stage",    51,  75, "⚡"),
    ("Peak",          76,  99, "🌙"),
    ("Transcendent", 100, 999, "✨"),
]

# ── Strength tiers (from manhwa) ───────────────────────────────────
# Goblin at lv14 = 800 STR; Blood wolf lv14 = 600 STR
# Goblin King lv16 = 3000 STR; Lynn at lv10 = ~100 STR (base)
# With 10 pts/level scaling: lv50 char ≈ 500+ STR
STR_TIERS = [
    (5000, "Divine"),
    (2000, "Transcendent"),
    (1000, "Formidable"),
    (500,  "Mighty"),
    (200,  "Powerful"),
    (100,  "Overwhelming Vigour"),
    (50,   "Average"),
    (0,    "Weak"),
]

def get_realm(level: int) -> tuple[str, str]:
    for name, lo, hi, emoji in REALMS:
        if lo <= level <= hi:
            return name, emoji
    return "Transcendent", "✨"

def str_label(value: int) -> str:
    for threshold, label in STR_TIERS:
        if value >= threshold:
            return label
    return "Weak"

# ── Class definitions ────────────────────────────────────────────────
# Base stats set so starting player is competitive vs Lv1-3 dungeon mobs
# Manhwa reference: Lynn starts Lv1 Necromancer with ~20 SPI,
# skeleton warrior at class-transfer has 15 across all 4 stats
CLASSES: dict[str, dict] = {
    "Shadow": {
        "emoji": "🌑", "color": 0x2c3e50,
        "desc": "Unique hidden class. Undetectable stealth, assassination specialist. Highest AGI.",
        "base": {"str": 12, "con": 10, "agi": 22, "spi": 8, "hp": 90, "mana": 70,
                 "crit_chance": 0.15, "damage_reduction": 0.05},
        "talent_name": "Authority of Judgment", "talent_rank": "Unknown",
        "passive": "Stealth undetectable by monsters. First stealth hit ×1.9 damage.",
        "active": "Shadow Form: invisible 16s, all stealth hits ×2.5. (CD: 30min)",
        "hidden_class": True,
        "starting_skills": [("Shadow Sneak","B"),("Backstab","C"),("Stealth Mastery","B")],
    },
    "Necromancer": {
        "emoji": "⚰️", "color": 0x6a0dad,
        "desc": "Master of death. SPI-scaled summons and soul magic.",
        "base": {"str": 8, "con": 8, "agi": 7, "spi": 20, "hp": 75, "mana": 130,
                 "crit_chance": 0.05, "damage_reduction": 0.03},
        "talent_name": "Death's Embrace", "talent_rank": "Unknown",
        "passive": "First skill each battle +15% bonus damage. Summons inherit 30% of SPI.",
        "active": "Corpse Summon: raise skeletal ally 2 turns. Cost: 40 mana.",
        "starting_skills": [("Skeleton Summoning Magic","F"),("Withering Magic","B")],
    },
    "Warrior": {
        "emoji": "⚔️", "color": 0xe74c3c,
        "desc": "Frontline powerhouse. Highest STR and CON.",
        "base": {"str": 20, "con": 16, "agi": 8, "spi": 3, "hp": 140, "mana": 30,
                 "crit_chance": 0.08, "damage_reduction": 0.12},
        "talent_name": "Berserker's Soul", "talent_rank": "A",
        "passive": "HP < 30%: STR ×1.5. Immune to stun while berserking.",
        "active": "Berserk: STR ×3 for 1 turn, take 2× damage. Cost: 20 mana.",
        "starting_skills": [("Heavy Strike","D"),("War Cry","C")],
    },
    "Mage": {
        "emoji": "🔮", "color": 0x3498db,
        "desc": "Arcane mastery. Brave Heart talent — damage scales with missing HP.",
        "base": {"str": 6, "con": 8, "agi": 9, "spi": 22, "hp": 80, "mana": 150,
                 "crit_chance": 0.10, "damage_reduction": 0.04},
        "talent_name": "Brave Heart", "talent_rank": "S",
        "passive": "Magic damage +1% for each 1% HP missing (up to +50%).",
        "active": "Mana Overload: next spell costs 0 mana and deals ×2.0. (CD: 5min)",
        "starting_skills": [("Fireball","C"),("Mana Shield","D")],
    },
    "Archer": {
        "emoji": "🏹", "color": 0x27ae60,
        "desc": "Precision striker. Multi-hit skills and high crit rate.",
        "base": {"str": 14, "con": 9, "agi": 20, "spi": 8, "hp": 95, "mana": 60,
                 "crit_chance": 0.18, "damage_reduction": 0.05},
        "talent_name": "Eagle Eye", "talent_rank": "A",
        "passive": "Critical hit chance +5% for every consecutive non-crit attack (stacks 3×).",
        "active": "Rain of Arrows: 3 hits at ×0.8 each. Cost: 25 mana.",
        "starting_skills": [("Multi-Arrow","D"),("Wind Step","C")],
    },
    "Gunman": {
        "emoji": "🔫", "color": 0xf39c12,
        "desc": "First strike specialist. First attack always devastating.",
        "base": {"str": 15, "con": 10, "agi": 18, "spi": 7, "hp": 100, "mana": 55,
                 "crit_chance": 0.12, "damage_reduction": 0.06},
        "talent_name": "First Draw", "talent_rank": "B",
        "passive": "First attack each battle deals ×1.5 damage automatically.",
        "active": "Bullet Rain: 5 fast hits at ×0.6 each. Cost: 30 mana.",
        "starting_skills": [("Bullet Rain","C"),("Quick Draw","D")],
    },
    "Knight": {
        "emoji": "🛡️", "color": 0x95a5a6,
        "desc": "Impenetrable defender. Highest CON, powerful counter-attack.",
        "base": {"str": 12, "con": 22, "agi": 6, "spi": 5, "hp": 160, "mana": 40,
                 "crit_chance": 0.05, "damage_reduction": 0.20},
        "talent_name": "Iron Wall", "talent_rank": "A",
        "passive": "10% of damage received is reflected back. Shield skills last +2 turns.",
        "active": "Guardian's Aura: +30% damage reduction for 3 turns. Cost: 25 mana.",
        "starting_skills": [("Taunt","C"),("Counter Strike","C")],
    },
    "Healer": {
        "emoji": "💚", "color": 0x1abc9c,
        "desc": "Life-sustaining support. Constant regen, powerful heals.",
        "base": {"str": 6, "con": 12, "agi": 8, "spi": 20, "hp": 110, "mana": 120,
                 "crit_chance": 0.04, "damage_reduction": 0.08},
        "talent_name": "Life Aura", "talent_rank": "B",
        "passive": "Restore 5% HP every 2 turns automatically.",
        "active": "Mass Heal: restore 30% of max HP. Cost: 40 mana.",
        "starting_skills": [("Heal","D"),("Blessing","C")],
    },
}
