# airi/rpg/skills.py — v2 (FULL REWRITE)
# Every skill now has: element field, correct effect types, hits field wired,
# and all passive_effect keys that engine.py reads.
# References: Galaxy manhwa skill types, Pokémon move categories,
#             Solo Leveling ability naming, D&D 5e spell school divisions

SKILL_DB: dict[str, dict] = {

    # ── Shadow / Assassin ────────────────────────────────────────
    "Shadow Sneak": {
        "rank":"B","type":"stealth","mana":0,
        "desc":"Enter stealth. First hit ×1.6 from shadow. Stealth Mastery doubles duration.",
        "effect":{"stealth_duration":12,"break_bonus":0.6},
        "upgrades":{2:{"break_bonus":0.75},3:{"break_bonus":0.9}},
        "combat_action":"stealth","classes":["Shadow"],
    },
    "Stealth Mastery": {
        "rank":"B","type":"passive","mana":0,
        "desc":"Passive: stealth duration ×2. Stealth undetectable by monsters below Tier III.",
        "passive_effect":{"stealth_duration_mult":2.0,"stealth_undetectable":True},
        "classes":["Shadow"],
    },
    "Backstab": {
        "rank":"C","type":"burst","mana":10,
        "desc":"Attack from shadow. ×2.0 damage. Requires stealth.",
        "multiplier":2.0,"requires_stealth":True,"element":"dark",
        "upgrades":{2:{"multiplier":2.1},3:{"multiplier":2.2}},
        "classes":["Shadow"],
    },
    "Greedy Hand": {
        "rank":"D","type":"passive","mana":0,
        "desc":"Passive: 20% extra loot chance. Max rank: 70%.",
        "passive_effect":{"extra_loot_chance":0.2},
        "upgrades":{2:{"extra_loot_chance":0.35},3:{"extra_loot_chance":0.5},4:{"extra_loot_chance":0.7}},
        "classes":["Shadow"],
    },
    "Venom Dart": {
        "rank":"C","type":"ranged","mana":25,
        "desc":"Poisoned dart. Applies Venom (5% HP/turn for 5 turns).",
        "multiplier":0.8,"hits":1,"element":"dark",
        "effect":{"type":"venom","duration":5},
        "classes":["Shadow"],
    },
    "Shadow Slash": {
        "rank":"B","type":"attack","mana":20,
        "desc":"High-speed slash from shadow. ×1.8 damage.",
        "multiplier":1.8,"element":"dark","classes":["Shadow"],
    },
    "Instant Shadow Slash": {
        "rank":"A","type":"attack","mana":35,
        "desc":"Teleport-slash. ×2.5 damage. Ignores ground-bind.",
        "multiplier":2.5,"element":"dark","ignores_effects":["ground_bind","stun"],
        "classes":["Shadow"],
    },
    "Endless Nightmare": {
        "rank":"A","type":"passive","mana":0,
        "desc":"Passive: Nightmare fills 20% faster. Eye Break deals ×3.5 instead of ×3.0.",
        "passive_effect":{"nightmare_fill_bonus":0.2,"eye_break_mult":3.5},
        "classes":["Shadow"],
    },

    # ── Necromancer ──────────────────────────────────────────────
    "Skeleton Summoning Magic": {
        "rank":"F","type":"summon","mana":40,
        "desc":"Summon a skeletal minion for 2 turns. Minion STR = 30% of yours.",
        "effect":{"type":"summon","summon_turns":2,"summon_str_pct":0.3},
        "classes":["Necromancer"],
    },
    "Withering Magic": {
        "rank":"B","type":"debuff","mana":30,
        "desc":"Reduce target DEF by 40% for 3 turns.",
        "multiplier":0.0,
        "effect":{"type":"wither","duration":3,"def_reduction":0.4},
        "classes":["Necromancer"],
    },
    "Soul Drain": {
        "rank":"C","type":"magic","mana":25,
        "desc":"Drain enemy life force. ×1.4 dark magic damage. Restores 10% of damage as HP.",
        "multiplier":1.4,"scaling":"spirit","element":"dark",
        "effect":{"type":"lifesteal","pct":0.10},
        "classes":["Necromancer"],
    },

    # ── Warrior ──────────────────────────────────────────────────
    "Heavy Strike": {
        "rank":"D","type":"attack","mana":10,
        "desc":"Powerful melee blow. ×1.6 damage.",
        "multiplier":1.6,"element":"physical","classes":["Warrior"],
    },
    "War Cry": {
        "rank":"C","type":"buff","mana":15,
        "desc":"Boost STR ×1.2 for 3 turns.",
        "effect":{"type":"str_boost","duration":3,"str_mult":1.2},
        "classes":["Warrior"],
    },
    "Reckless Charge": {
        "rank":"B","type":"attack","mana":20,
        "desc":"Charge with full force. ×2.0 damage. Applies Bleed on crit.",
        "multiplier":2.0,"element":"physical",
        "effect":{"type":"bleed","duration":3,"value":0},
        "classes":["Warrior"],
    },

    # ── Mage ─────────────────────────────────────────────────────
    "Fireball": {
        "rank":"C","type":"magic","mana":30,
        "desc":"Ball of flame. ×1.5 fire damage (SPI-scaled).",
        "multiplier":1.5,"scaling":"spirit","element":"fire",
        "classes":["Mage"],
    },
    "Mana Shield": {
        "rank":"D","type":"shield","mana":20,
        "desc":"Create a shield absorbing 50% of max mana as HP.",
        "effect":{"type":"shield","duration":3,"value_pct_mana":0.5},
        "classes":["Mage"],
    },
    "Blizzard": {
        "rank":"B","type":"magic","mana":40,
        "desc":"Ice storm hits 3 times. ×0.7 each (total ×2.1). Ice element.",
        "multiplier":2.1,"hits":3,"scaling":"spirit","element":"ice",
        "classes":["Mage"],
    },
    "Thunder Strike": {
        "rank":"B","type":"magic","mana":35,
        "desc":"Lightning bolt. ×1.8 lightning damage. 30% chance to Stun 1 turn.",
        "multiplier":1.8,"scaling":"spirit","element":"lightning",
        "effect":{"type":"stun","duration":1},
        "classes":["Mage"],
    },

    # ── Archer ───────────────────────────────────────────────────
    "Multi-Arrow": {
        "rank":"D","type":"attack","mana":15,
        "desc":"Fire 2 arrows. Each ×0.8 damage (total ×1.6). Crits roll per arrow.",
        "multiplier":1.6,"hits":2,"element":"physical","classes":["Archer"],
    },
    "Wind Step": {
        "rank":"C","type":"evasion","mana":10,
        "desc":"AGI ×1.3 for 2 turns. Reaction increased by 30%.",
        "effect":{"type":"agi_boost","duration":2,"agi_mult":1.3},
        "classes":["Archer"],
    },
    "Rain of Arrows": {
        "rank":"B","type":"attack","mana":25,
        "desc":"3 arrows from above. Each ×0.8 (total ×2.4). Ice tips — ice element.",
        "multiplier":2.4,"hits":3,"element":"ice","classes":["Archer"],
    },

    # ── Gunman ────────────────────────────────────────────────────
    "Bullet Rain": {
        "rank":"C","type":"attack","mana":20,
        "desc":"4 rapid shots. Each ×0.5 (total ×2.0). Crits per bullet.",
        "multiplier":2.0,"hits":4,"element":"physical","classes":["Gunman"],
    },
    "Quick Draw": {
        "rank":"B","type":"attack","mana":15,
        "desc":"Instant draw. ×1.8 damage. Always fires before monster this turn.",
        "multiplier":1.8,"always_first":True,"element":"physical","classes":["Gunman"],
    },
    "Incendiary Round": {
        "rank":"C","type":"attack","mana":25,
        "desc":"Fire round. ×1.4 fire damage. Applies Burn (2 turns, 5% HP/turn).",
        "multiplier":1.4,"element":"fire",
        "effect":{"type":"burn","duration":2,"value":0},
        "classes":["Gunman"],
    },

    # ── Knight ────────────────────────────────────────────────────
    "Taunt": {
        "rank":"C","type":"debuff","mana":5,
        "desc":"Monster DEF -20% for 2 turns. Forces it to target you.",
        "effect":{"type":"taunt","duration":2,"target_def_reduction":0.2},
        "classes":["Knight"],
    },
    "Counter Strike": {
        "rank":"B","type":"passive","mana":0,
        "desc":"Passive: counter-attack damage increased to 80% (default 10%).",
        "passive_effect":{"counter_dmg_pct":0.8},
        "classes":["Knight"],
    },
    "Guardian's Aura": {
        "rank":"B","type":"shield","mana":25,
        "desc":"Massive shield. Absorbs 80% of max mana as HP. Lasts 4 turns.",
        "effect":{"type":"shield","duration":4,"value_pct_mana":0.8},
        "classes":["Knight"],
    },
    "Holy Smite": {
        "rank":"C","type":"attack","mana":20,
        "desc":"Divine strike. ×1.5 holy damage. Extra effective vs undead/dark.",
        "multiplier":1.5,"element":"holy","classes":["Knight"],
    },

    # ── Healer ────────────────────────────────────────────────────
    "Heal": {
        "rank":"C","type":"heal","mana":30,
        "desc":"Restore 25% of max HP.",
        "heal_pct":0.25,"classes":["Healer"],
    },
    "Blessing": {
        "rank":"B","type":"buff","mana":20,
        "desc":"All stats ×1.1 for 3 turns.",
        "effect":{"type":"all_stats_boost","duration":3,"mult":1.1},
        "classes":["Healer"],
    },
    "Mass Heal": {
        "rank":"A","type":"heal","mana":40,
        "desc":"Restore 50% of max HP.",
        "heal_pct":0.50,"classes":["Healer"],
    },
    "Sacred Light": {
        "rank":"B","type":"magic","mana":30,
        "desc":"Holy beam. ×1.6 holy damage (SPI-scaled).",
        "multiplier":1.6,"scaling":"spirit","element":"holy","classes":["Healer"],
    },

    # ── Universal (from skill books) ─────────────────────────────
    "Ground Bind": {
        "rank":"C","type":"cc","mana":25,
        "desc":"Bind enemy 2 turns. They cannot dodge or use movement skills.",
        "effect":{"type":"ground_bind","duration":2},
        "classes":None,
    },
    "Battle Meditation": {
        "rank":"D","type":"buff","mana":15,
        "desc":"Regen active: restore 5% HP per turn for 4 turns.",
        "effect":{"type":"regen","duration":4},
        "classes":None,
    },
}


def get_skill(name: str) -> dict | None:
    return SKILL_DB.get(name)

def skills_for_class(class_name: str) -> list[dict]:
    return [{"name":k,**v} for k,v in SKILL_DB.items()
            if v.get("classes") is None or class_name in (v.get("classes") or [])]

def apply_upgrade(skill_name: str, level: int) -> dict:
    base = dict(SKILL_DB.get(skill_name, {}))
    for lvl in sorted((base.get("upgrades") or {}).keys()):
        if level >= lvl:
            base.update(base["upgrades"][lvl])
    return base
