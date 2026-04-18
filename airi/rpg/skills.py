# airi/rpg/skills.py — Skill definitions from Kinfang transcript
# Every skill here is directly from the transcript, not invented.

SKILL_DB: dict[str, dict] = {
    # ── Shadow/Assassin skills ──────────────────────────────────
    "Shadow Sneak": {
        "rank": "B", "type": "stealth", "mana": 0,
        "desc": "Enter stealth for 12 turns. First hit from stealth deals ×1.6 bonus damage. Attacking reveals you.",
        "effect": {"stealth_duration": 12, "break_bonus": 0.6},
        "upgrades": {
            2: {"stealth_duration": 14, "break_bonus": 0.75},
            3: {"stealth_duration": 16, "break_bonus": 0.9},  # transcript max
        },
        "combat_action": "stealth",
        "classes": ["Shadow"],
    },
    "Stealth Mastery": {
        "rank": "B", "type": "passive", "mana": 0,
        "desc": "Passive: stealth duration ×2. Stealth level +1 (undetectable by most monsters).",
        "passive_effect": {"stealth_duration_mult": 2.0, "stealth_undetectable": True},
        "classes": ["Shadow"],
    },
    "Backstab": {
        "rank": "C", "type": "burst", "mana": 10,
        "desc": "Attack from outside enemy vision. Deals ×2.0 damage. Must be used from stealth.",
        "multiplier": 2.0, "requires_stealth": True, "requires_outside_vision": True,
        "upgrades": {2: {"multiplier": 2.1}, 3: {"multiplier": 2.2}},
        "classes": ["Shadow"],
    },
    "Greedy Hand": {
        "rank": "D", "type": "passive", "mana": 0,
        "desc": "Passive: 20% chance to get extra loot from monsters. Increases to 70% at max rank.",
        "passive_effect": {"extra_loot_chance": 0.2},
        "upgrades": {
            2: {"extra_loot_chance": 0.35},
            3: {"extra_loot_chance": 0.5},
            4: {"extra_loot_chance": 0.7},
        },
        "classes": ["Shadow"],
    },
    "Venom Dart": {
        "rank": "C", "type": "ranged", "mana": 25,
        "desc": "Throw a poisoned dart. 120 base damage + applies Venom (50% HP/turn for 5 turns). Range: 14m.",
        "base_damage": 120, "multiplier": 1.0,
        "effect": {"type": "venom", "duration": 5, "dmg_pct": 0.05},  # 5% max HP/turn
        "classes": ["Shadow"],
    },
    "Shadow Slash": {
        "rank": "B", "type": "attack", "mana": 20,
        "desc": "High-speed slash from shadow. ×1.8 damage. Can be chained after Shadow Sneak.",
        "multiplier": 1.8, "combo_after": "Shadow Sneak",
        "classes": ["Shadow"],
    },
    "Instant Shadow Slash": {
        "rank": "A", "type": "attack", "mana": 35,
        "desc": "Instant teleport-slash. ×2.5 damage. Ignores ground-bind and stun.",
        "multiplier": 2.5, "ignores_effects": ["ground_bind", "stun"],
        "classes": ["Shadow"],
    },
    "Endless Nightmare": {
        "rank": "A", "type": "passive", "mana": 0,
        "desc": "Passive: All attacks fill target's Nightmare Gauge faster (+20% fill rate). When enemy sleeps, your next attack deals ×3.5 instead of ×3.0.",
        "passive_effect": {"nightmare_fill_bonus": 0.2, "eye_break_mult": 3.5},
        "classes": ["Shadow"],
    },
    # ── Necromancer skills ───────────────────────────────────────
    "Skeleton Summoning Magic": {
        "rank": "F", "type": "summon", "mana": 40,
        "desc": "Summon a weak skeletal minion to fight for 2 turns. Minion STR = 30% of yours.",
        "effect": {"summon_turns": 2, "summon_str_pct": 0.3},
        "classes": ["Necromancer"],
    },
    "Withering Magic": {
        "rank": "B", "type": "debuff", "mana": 30,
        "desc": "Reduce target's DEF by 40% for 3 turns.",
        "multiplier": 0.0, "effect": {"type": "wither", "duration": 3, "def_reduction": 0.4},
        "classes": ["Necromancer"],
    },
    # ── Warrior skills ──────────────────────────────────────────
    "Heavy Strike": {
        "rank": "D", "type": "attack", "mana": 10,
        "desc": "Powerful melee strike. ×1.6 damage.",
        "multiplier": 1.6, "classes": ["Warrior"],
    },
    "War Cry": {
        "rank": "C", "type": "buff", "mana": 15,
        "desc": "Boost STR by 20% for 3 turns.",
        "effect": {"type": "str_boost", "duration": 3, "str_mult": 1.2},
        "classes": ["Warrior"],
    },
    # ── Mage skills ─────────────────────────────────────────────
    "Fireball": {
        "rank": "C", "type": "magic", "mana": 30,
        "desc": "Fire a ball of flame. ×1.5 damage (SPI-scaling).",
        "multiplier": 1.5, "scaling": "spirit", "classes": ["Mage"],
    },
    "Mana Shield": {
        "rank": "D", "type": "shield", "mana": 20,
        "desc": "Create a shield absorbing 50% of your mana value in damage.",
        "effect": {"type": "shield", "duration": 3, "value_pct_mana": 0.5},
        "classes": ["Mage"],
    },
    # ── Archer skills ────────────────────────────────────────────
    "Multi-Arrow": {
        "rank": "D", "type": "attack", "mana": 15,
        "desc": "Fire 2 arrows simultaneously. Each deals ×0.8 damage (total ×1.6).",
        "multiplier": 1.6, "hits": 2, "classes": ["Archer"],
    },
    "Wind Step": {
        "rank": "C", "type": "evasion", "mana": 10,
        "desc": "Increase AGI by 30% for 2 turns. Dodge chance +15%.",
        "effect": {"type": "agi_boost", "duration": 2, "agi_mult": 1.3},
        "classes": ["Archer"],
    },
    # ── Gunman skills ────────────────────────────────────────────
    "Bullet Rain": {
        "rank": "C", "type": "attack", "mana": 20,
        "desc": "Fire 4 rapid shots. Each deals ×0.5 damage (total ×2.0).",
        "multiplier": 2.0, "hits": 4, "classes": ["Gunman"],
    },
    "Quick Draw": {
        "rank": "B", "type": "attack", "mana": 15,
        "desc": "Instant draw and fire. ×1.8 damage, always goes first in turn.",
        "multiplier": 1.8, "always_first": True, "classes": ["Gunman"],
    },
    # ── Knight skills ─────────────────────────────────────────────
    "Taunt": {
        "rank": "C", "type": "debuff", "mana": 5,
        "desc": "Force monster to target you. Monster DEF -20% for 2 turns.",
        "effect": {"type": "taunt", "duration": 2, "target_def_reduction": 0.2},
        "classes": ["Knight"],
    },
    "Counter Strike": {
        "rank": "B", "type": "passive", "mana": 0,
        "desc": "Passive: counter-attack damage increased to 80% (from 50%).",
        "passive_effect": {"counter_dmg_pct": 0.8},
        "classes": ["Knight"],
    },
    # ── Healer skills ────────────────────────────────────────────
    "Heal": {
        "rank": "C", "type": "heal", "mana": 30,
        "desc": "Restore 25% of max HP.",
        "heal_pct": 0.25, "classes": ["Healer"],
    },
    "Blessing": {
        "rank": "B", "type": "buff", "mana": 20,
        "desc": "All stats +10% for 3 turns.",
        "effect": {"type": "all_stats_boost", "duration": 3, "mult": 1.1},
        "classes": ["Healer"],
    },
    # ── Learnable from books (any class) ─────────────────────────
    "Ground Bind": {
        "rank": "C", "type": "cc", "mana": 25,
        "desc": "Bind enemy to the ground for 2 turns. They cannot dodge or use movement skills.",
        "effect": {"type": "ground_bind", "duration": 2},
        "classes": None,  # learnable by anyone from skill book
    },
}


def get_skill(name: str) -> dict | None:
    return SKILL_DB.get(name)

def skills_for_class(class_name: str) -> list[dict]:
    return [{"name": k, **v} for k, v in SKILL_DB.items()
            if v.get("classes") and class_name in v["classes"]]

def apply_upgrade(skill_name: str, level: int) -> dict:
    """Return skill dict with upgrades applied for given level."""
    base = dict(SKILL_DB.get(skill_name, {}))
    upgrades = base.get("upgrades", {})
    for lvl in sorted(upgrades.keys()):
        if level >= lvl:
            base.update(upgrades[lvl])
    return base
