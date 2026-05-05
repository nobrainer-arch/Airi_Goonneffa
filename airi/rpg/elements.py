# airi/rpg/elements.py — Element system
# References: Pokémon type chart, Final Fantasy elemental design,
#             Galaxy manhwa energy-type attacks
# 6 elements: physical / fire / ice / lightning / dark / holy
# Chart: attacker_element → defender_element → multiplier
# 1.5 = super effective, 0.5 = resisted, 0.0 = immune, 1.0 = neutral

ELEMENTS = ["physical", "fire", "ice", "lightning", "dark", "holy"]

ELEMENT_EMOJI = {
    "physical":  "⚔️",
    "fire":      "🔥",
    "ice":       "❄️",
    "lightning": "⚡",
    "dark":      "🌑",
    "holy":      "✨",
}

# ELEMENT_CHART[attacker][defender] = multiplier
ELEMENT_CHART: dict[str, dict[str, float]] = {
    "physical": {
        "physical": 1.0, "fire": 1.0, "ice": 1.0,
        "lightning": 1.0, "dark": 1.0, "holy": 1.0,
    },
    "fire": {
        "physical": 1.0, "fire": 0.5, "ice": 1.5,
        "lightning": 0.8, "dark": 1.2, "holy": 0.8,
    },
    "ice": {
        "physical": 1.0, "fire": 1.0, "ice": 0.5,
        "lightning": 1.5, "dark": 1.0, "holy": 1.0,
    },
    "lightning": {
        "physical": 1.0, "fire": 1.5, "ice": 0.8,
        "lightning": 0.5, "dark": 1.2, "holy": 0.8,
    },
    "dark": {
        "physical": 1.0, "fire": 0.8, "ice": 1.0,
        "lightning": 0.8, "dark": 0.5, "holy": 1.8,
    },
    "holy": {
        "physical": 1.0, "fire": 1.0, "ice": 1.0,
        "lightning": 1.0, "dark": 1.8, "holy": 0.5,
    },
}

# Monster type → natural element weakness/resistance
MONSTER_ELEMENT: dict[str, str] = {
    # fire weaknesses
    "Frost Golem": "ice", "Ice Dragon": "ice", "Ice Wraith": "ice",
    # lightning weaknesses
    "Orc Warrior": "physical", "Troll Shaman": "fire", "Blood Wolf": "physical",
    # dark weaknesses (undead)
    "Skeleton": "dark", "Elder Lich": "dark", "Plague Zombie": "dark",
    "Bone Dragon": "dark", "Death Knight": "dark",
    # holy weaknesses
    "Shadow Behemoth": "dark", "Void God": "dark", "Fallen God": "dark",
    # physical
    "Goblin": "physical", "Bandit": "physical", "Goblin King": "physical",
    "Orc Scout": "physical", "Kobold Grunt": "physical",
}

def get_element_mult(attack_element: str, defender_element: str) -> float:
    """Return damage multiplier for attacker element vs defender element."""
    chart = ELEMENT_CHART.get(attack_element or "physical", {})
    return chart.get(defender_element or "physical", 1.0)

def element_banner(mult: float) -> str:
    """Return a log banner for the element interaction."""
    if mult >= 1.8:  return "🌟 **SUPER EFFECTIVE!** "
    if mult >= 1.5:  return "✅ **Effective!** "
    if mult <= 0.0:  return "🚫 **IMMUNE!** "
    if mult <= 0.5:  return "🛡️ **Resisted** "
    return ""
