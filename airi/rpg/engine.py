# airi/rpg/engine.py — Stable Battle Engine v3
# Turn-based with D&D-inspired mechanics:
#  - Speed determines who goes first (higher AGI = faster)
#  - Damage pipeline: base → crit → skill mult → reduction → flat def → shields → min 1
#  - Passive defense: shield/barrier skills auto-activate before first attack (cost mana)
#  - Effects: venom, burn, bleed, stun, ground_bind, regen, str_boost, shield
#  - Nightmare gauge → sleep → eye break x3
#  - Reaction counter (high reaction = chance to reduce incoming damage)

import random
from dataclasses import dataclass, field
from typing import Optional

GRADE_MULT = {
    "Inferior":0.8,"Normal":1.0,"Bronze":1.2,
    "Silver":1.5,"Gold":1.8,"Emerald":2.2,"Diamond":2.8,"Legendary":4.0,
}


# ── Effects ───────────────────────────────────────────────────────
@dataclass
class Effect:
    etype:    str           # venom|burn|bleed|stun|ground_bind|regen|str_boost|shield|slow
    duration: int           # turns remaining
    value:    float = 0.0   # damage value / heal / shield HP / mult
    source:   str  = ""

    def tick(self) -> bool:
        """Decrement duration. Returns True if still active."""
        self.duration -= 1
        return self.duration > 0

    def apply_tick(self, unit: "CombatUnit") -> str | None:
        """Apply per-turn effect. Returns log line or None."""
        if self.etype == "venom":
            dmg = max(1, int(unit.hp_max * 0.05))
            unit.hp = max(0, unit.hp - dmg)
            return f"☠️ **Venom**: -{dmg} HP ({self.duration}T left)"
        if self.etype == "burn":
            dmg = max(1, int(self.value))
            unit.hp = max(0, unit.hp - dmg)
            return f"🔥 **Burn**: -{dmg} HP ({self.duration}T left)"
        if self.etype == "bleed":
            dmg = max(1, int(unit.hp_max * 0.03))
            unit.hp = max(0, unit.hp - dmg)
            return f"🩸 **Bleed**: -{dmg} HP ({self.duration}T left)"
        if self.etype == "regen":
            heal = max(1, int(unit.hp_max * 0.05))
            unit.hp = min(unit.hp_max, unit.hp + heal)
            return f"💚 **Regen**: +{heal} HP ({self.duration}T left)"
        return None


# ── Combat Unit ───────────────────────────────────────────────────
@dataclass
class CombatUnit:
    name:             str
    hp:               int
    hp_max:           int
    mana:             int
    mana_max:         int
    strength:         int
    constitution:     int    # flat defense = constitution × 0.5
    agility:          int    # speed, initiative, dodge
    spirit:           int    # magic scaling, mana regen
    reaction:         int    # reduces incoming damage (0–100 range)
    crit_chance:      float  # 0.0–1.0
    crit_damage:      float  # multiplier (e.g. 1.5)
    damage_reduction: float  # % reduction, capped at 0.80
    reflect_pct:      float  # % of received damage reflected (Knight)
    grade:            str    = "Normal"
    is_player:        bool   = True
    # State
    stealth:          bool  = False
    stealth_bonus:    float = 0.0
    first_hit_active: bool  = False
    first_hit_bonus:  float = 0.0
    nightmare_gauge:  int   = 0
    sleeping:         bool  = False
    effects:   list   = field(default_factory=list)
    cooldowns: dict   = field(default_factory=dict)  # name → turns

    @property
    def flat_def(self) -> float:
        return self.constitution * 0.5

    @property
    def alive(self) -> bool:
        return self.hp > 0

    def is_stunned(self) -> bool:
        return any(e.etype == "stun" for e in self.effects)

    def is_ground_bound(self) -> bool:
        return any(e.etype == "ground_bind" for e in self.effects)

    def get_shield_hp(self) -> float:
        for e in self.effects:
            if e.etype == "shield":
                return e.value
        return 0.0

    def drain_shield(self, amount: float) -> float:
        """Returns remaining damage after shield absorbs. Removes shield if depleted."""
        for e in self.effects[:]:
            if e.etype == "shield":
                absorbed = min(e.value, amount)
                e.value -= absorbed
                if e.value <= 0:
                    self.effects.remove(e)
                return amount - absorbed
        return amount

    def add_effect(self, etype: str, duration: int, value: float = 0.0, source: str = ""):
        """Add effect. Refresh if same type (except shield which stacks)."""
        if etype == "shield":
            # Stack shield HP
            for e in self.effects:
                if e.etype == "shield":
                    e.value += value
                    e.duration = max(e.duration, duration)
                    return
        else:
            for e in self.effects[:]:
                if e.etype == etype:
                    e.duration = max(e.duration, duration)
                    e.value = max(e.value, value)
                    return
        self.effects.append(Effect(etype=etype, duration=duration, value=value, source=source))

    def tick_effects(self) -> list[str]:
        logs = []
        remove = []
        for eff in self.effects:
            line = eff.apply_tick(self)
            if line:
                logs.append(line)
            if not eff.tick():
                remove.append(eff)
                if eff.etype not in ("regen","str_boost"):
                    logs.append(f"✨ **{eff.etype.replace('_',' ').title()}** wore off")
        for e in remove:
            if e in self.effects:
                self.effects.remove(e)
        return logs

    def tick_cooldowns(self):
        self.cooldowns = {k: max(0, v-1) for k, v in self.cooldowns.items() if v > 1}

    def regen_mana(self):
        """Restore spirit/5 mana per turn (min 2)."""
        regen = max(2, self.spirit // 5)
        self.mana = min(self.mana_max, self.mana + regen)

    def get_str_mult(self) -> float:
        """Check for str_boost effects."""
        for e in self.effects:
            if e.etype == "str_boost":
                return e.value
        return 1.0


# ── Damage Result ────────────────────────────────────────────────
@dataclass
class DamageResult:
    raw:          int  = 0
    final:        int  = 0
    is_crit:      bool = False
    is_stealth:   bool = False
    is_backstab:  bool = False
    nightmare_triggered: bool = False
    reflected:    int  = 0
    log: list[str] = field(default_factory=list)


# ── Damage Calculator ─────────────────────────────────────────────
class DamageCalculator:
    @staticmethod
    def calculate(
        attacker: CombatUnit,
        defender: CombatUnit,
        skill_mult: float = 1.0,
        is_magic:   bool  = False,
        skill_name: str   = "",
    ) -> DamageResult:
        res = DamageResult()
        log = res.log

        # 1. Base damage (STR × str_boost × skill_mult)
        str_mult = attacker.get_str_mult()
        if is_magic:
            base = attacker.spirit * skill_mult
        else:
            base = attacker.strength * str_mult * skill_mult
        res.raw = int(base)

        # Berserker: low HP bonus
        if attacker.is_player and (attacker.hp / max(attacker.hp_max, 1)) < 0.30:
            if attacker.name != "": # check class name is Warrior handled outside
                pass

        dmg = float(base)

        # 2. First-hit bonus (Gunman passive / Ring of Skeleton King)
        if attacker.first_hit_active and attacker.first_hit_bonus > 0:
            dmg *= (1 + attacker.first_hit_bonus)
            attacker.first_hit_active = False
            log.append(f"⚡ **First Strike**: ×{1+attacker.first_hit_bonus:.1f}")

        # 3. Stealth break bonus
        if attacker.stealth and attacker.stealth_bonus > 0:
            res.is_stealth = True
            dmg *= (1 + attacker.stealth_bonus)
            attacker.stealth = False
            log.append(f"🌑 **Stealth Break**: ×{1+attacker.stealth_bonus:.1f}")

        # 4. Critical hit
        crit_roll = random.random()
        if crit_roll < attacker.crit_chance:
            res.is_crit = True
            dmg *= attacker.crit_damage
            log.append(f"💥 **CRITICAL HIT**: ×{attacker.crit_damage:.1f}")

        # 5. Nightmare eye-break (×3 on sleeping monster)
        if defender.sleeping and not attacker.is_player is False:
            # player attacks sleeping monster
            pass
        if defender.sleeping:
            dmg *= 3.0
            res.nightmare_triggered = True
            defender.sleeping = False
            defender.nightmare_gauge = 0
            log.append(f"👁️ **EYE BREAK**: ×3.0 on sleeping target!")

        # 6. Grade multiplier
        grade_m = GRADE_MULT.get(attacker.grade, 1.0)
        if grade_m != 1.0:
            dmg *= grade_m

        # 7. Reaction dodge / partial block
        # reaction_chance = defender.reaction / (attacker.agility + defender.reaction)
        react_denom = attacker.agility + defender.reaction
        if react_denom > 0 and not defender.sleeping and not defender.is_stunned():
            react_chance = defender.reaction / react_denom
            if random.random() < react_chance * 0.3:   # up to 30% partial block from reaction
                block = dmg * 0.25
                dmg -= block
                log.append(f"⚡ **Reaction**: blocked {int(block)} dmg")

        # 8. Damage reduction %
        red = min(defender.damage_reduction, 0.80)
        dmg *= (1 - red)

        # 9. Flat defense from constitution
        flat = defender.flat_def
        dmg -= flat

        # 10. Shield absorption
        dmg = defender.drain_shield(dmg)

        # 11. Minimum 1
        res.final = max(1, int(dmg))
        log.append(f"💔 **{attacker.name}** → **{res.final}** dmg to {defender.name}")

        # 12. Reflection (Knight)
        if defender.reflect_pct > 0:
            res.reflected = max(1, int(res.final * defender.reflect_pct))
            log.append(f"↩️ **Reflect**: {res.reflected} back!")

        # 13. Nightmare gauge fill (for monsters)
        if not defender.is_player and not defender.sleeping:
            fill = 20 + (5 if res.is_crit else 0)
            defender.nightmare_gauge = min(100, defender.nightmare_gauge + fill)
            if defender.nightmare_gauge >= 100:
                defender.sleeping = True
                log.append(f"💤 **{defender.name}** enters **SLEEPING STATE** — next hit ×3.0!")

        return res


# ── Battle Engine ────────────────────────────────────────────────
class BattleEngine:
    def __init__(self, player: CombatUnit, monster: CombatUnit):
        self.player  = player
        self.monster = monster
        self.turn    = 0
        self._last_player_action = ""

    def apply_passive_defense(self, player_skills: list[dict]) -> list[str]:
        """
        Auto-activate shield/barrier/abjuration skills at battle start.
        Costs mana but does NOT use a turn. Called once before first turn.
        """
        from .skills import SKILL_DB
        logs = []
        for sk in player_skills:
            name = sk["skill_name"]
            info = SKILL_DB.get(name, {})
            stype = info.get("type", "")
            if stype not in ("shield", "abjuration", "barrier"):
                continue
            mana_cost = info.get("mana", 20)
            if self.player.mana < mana_cost:
                continue
            # Activate it
            self.player.mana -= mana_cost
            eff = info.get("effect", {})
            if eff.get("type") == "shield":
                shield_val = self.player.mana_max * eff.get("value_pct_mana", 0.5)
                self.player.add_effect("shield", eff.get("duration", 3), shield_val, name)
                logs.append(f"🛡️ **{name}** auto-activated: +{int(shield_val)} shield HP (costs {mana_cost} mana)")
            else:
                dmg_red = eff.get("dmg_reduction", 0.10)
                self.player.damage_reduction = min(0.80, self.player.damage_reduction + dmg_red)
                logs.append(f"🛡️ **{name}** auto-activated: +{int(dmg_red*100)}% damage reduction (costs {mana_cost} mana)")
        return logs

    def process_player_action(
        self,
        action:       str,       # "attack" | "skill" | "stealth" | "flee"
        skill_name:   str  = "",
        skill_mult:   float= 1.0,
        is_magic:     bool = False,
        skill_effect: dict = None,
    ) -> dict:
        """Process one full exchange: player acts → monster responds. Returns result dict."""
        self.turn += 1
        result = {
            "player_dmg": 0, "monster_dmg": 0,
            "log": [], "fled": False,
            "player_alive": True, "monster_alive": True,
            "turn": self.turn,
        }

        if not self.player.alive or not self.monster.alive:
            result["player_alive"] = self.player.alive
            result["monster_alive"] = self.monster.alive
            return result

        # ── Mana regen per turn ─────────────────────────────
        self.player.regen_mana()

        # ── Player action ───────────────────────────────────
        if action == "flee":
            flee_chance = self.player.agility / max(self.player.agility + self.monster.agility, 1)
            if random.random() < flee_chance:
                result["fled"] = True
                result["log"].append("🏃 **You fled successfully!**")
                return result
            else:
                result["log"].append("❌ **Escape failed!** Monster counters!")

        elif action == "stealth":
            if self.player.stealth:
                result["log"].append("⚠️ Already in stealth.")
            elif self.player.is_stunned():
                result["log"].append("⚠️ Stunned — can't stealth.")
            else:
                self.player.stealth = True
                self.player.stealth_bonus = 0.6
                result["log"].append("🌑 **Shadow Sneak**: entered stealth — first hit ×1.6!")

        elif action in ("attack", "skill"):
            # Apply skill effects before damage
            if skill_effect:
                etype = skill_effect.get("type","")
                if etype in ("venom","burn","bleed","stun","ground_bind"):
                    self.monster.add_effect(
                        etype,
                        skill_effect.get("duration", 2),
                        skill_effect.get("value", 5.0),
                        skill_name,
                    )
                    result["log"].append(f"💢 **{skill_name}** applied **{etype}** to {self.monster.name}!")
                elif etype == "regen":
                    self.player.add_effect("regen", skill_effect.get("duration",3), 0.0, skill_name)
                    result["log"].append(f"💚 **{skill_name}**: regeneration active!")
                elif etype == "str_boost":
                    self.player.add_effect("str_boost", skill_effect.get("duration",3), skill_effect.get("str_mult",1.2), skill_name)
                    result["log"].append(f"⚡ **{skill_name}**: STR ×{skill_effect.get('str_mult',1.2):.1f} for {skill_effect.get('duration',3)} turns!")
                elif etype == "shield":
                    sv = self.player.mana_max * skill_effect.get("value_pct_mana", 0.5)
                    self.player.add_effect("shield", skill_effect.get("duration",3), sv, skill_name)
                    result["log"].append(f"🛡️ **{skill_name}**: +{int(sv)} shield HP!")

            # Check heal type
            if skill_effect and skill_effect.get("type") == "heal":
                heal_pct = skill_effect.get("heal_pct", 0.25)
                healed   = int(self.player.hp_max * heal_pct)
                self.player.hp = min(self.player.hp_max, self.player.hp + healed)
                result["log"].append(f"💚 **{skill_name}**: +{healed} HP restored!")
            elif skill_effect and skill_effect.get("type") in ("shield","str_boost","regen","buff"):
                pass   # effect-only skill, no damage
            else:
                dmg_res = DamageCalculator.calculate(
                    self.player, self.monster, skill_mult, is_magic, skill_name
                )
                self.monster.hp = max(0, self.monster.hp - dmg_res.final)
                if dmg_res.reflected:
                    self.player.hp = max(0, self.player.hp - dmg_res.reflected)
                result["player_dmg"] = dmg_res.final
                result["log"].extend(dmg_res.log)

                # Berserker passive
                if self.player.is_player and self.player.hp > 0:
                    hp_pct = self.player.hp / self.player.hp_max
                    if hp_pct < 0.30 and not any(e.etype=="str_boost" for e in self.player.effects):
                        self.player.add_effect("str_boost", 99, 1.5, "Berserker")
                        result["log"].append("🔥 **BERSERKER**: STR ×1.5 (HP < 30%)!")

        # ── Tick player effects ─────────────────────────────
        result["log"].extend(self.player.tick_effects())
        self.player.tick_cooldowns()

        if not self.monster.alive:
            result["monster_alive"] = False
            result["player_alive"]  = self.player.alive
            return result

        # ── Monster action ──────────────────────────────────
        if self.monster.is_stunned() or self.monster.sleeping:
            state = "stunned" if self.monster.is_stunned() else "sleeping"
            result["log"].append(f"💤 **{self.monster.name}** is {state} — can't act!")
        elif self.player.stealth and self.monster.reaction < self.player.agility:
            result["log"].append(f"👁️ {self.monster.name} can't find you in the shadows…")
        else:
            # Monster attacks
            m_mult = 1.0
            # Occasionally monsters use a stronger attack
            if random.random() < 0.25:
                m_mult = random.uniform(1.3, 1.8)
                result["log"].append(f"⚠️ **{self.monster.name}** uses a special attack!")
            m_res = DamageCalculator.calculate(self.monster, self.player, m_mult)
            self.player.hp = max(0, self.player.hp - m_res.final)
            if m_res.reflected:
                self.monster.hp = max(0, self.monster.hp - m_res.reflected)
            result["monster_dmg"] = m_res.final
            result["log"].extend(m_res.log)

            # Healer regen every 2 turns
            if self.turn % 2 == 0:
                regen = max(1, int(self.player.hp_max * 0.05))
                self.player.hp = min(self.player.hp_max, self.player.hp + regen)
                result["log"].append(f"💚 **Healer Regen**: +{regen} HP")

        # ── Tick monster effects ────────────────────────────
        result["log"].extend(self.monster.tick_effects())
        self.monster.tick_cooldowns()

        result["player_alive"]  = self.player.alive
        result["monster_alive"] = self.monster.alive
        return result


# ── Smart Auto-Battle AI ─────────────────────────────────────────
def ai_choose_action(unit: CombatUnit, enemy: CombatUnit, skills: list[dict]) -> dict:
    """
    Choose best action for auto-battle.
    Returns dict: {action, skill_name, skill_mult, is_magic, skill_effect, mana_cost}
    Priority:
      1. Heal if HP < 40%
      2. Shield/buff if no active shield and has one
      3. Best offensive skill (highest mult, enough mana, off cooldown)
      4. Basic attack
    """
    from .skills import SKILL_DB
    hp_pct = unit.hp / max(unit.hp_max, 1)

    # 1. Emergency heal
    if hp_pct < 0.40:
        for sk in skills:
            info = SKILL_DB.get(sk["skill_name"], {})
            if (info.get("type") == "heal"
                    and unit.cooldowns.get(sk["skill_name"], 0) == 0
                    and unit.mana >= info.get("mana", 10)):
                return {
                    "action": "skill", "skill_name": sk["skill_name"],
                    "skill_mult": 1.0, "is_magic": False,
                    "skill_effect": {"type":"heal","heal_pct": info.get("heal_pct",0.25)},
                    "mana_cost": info.get("mana", 10),
                }

    # 2. Apply shield if available and not active
    if unit.get_shield_hp() <= 0:
        for sk in skills:
            info = SKILL_DB.get(sk["skill_name"], {})
            if (info.get("type") in ("shield","abjuration")
                    and unit.cooldowns.get(sk["skill_name"], 0) == 0
                    and unit.mana >= info.get("mana", 10)):
                eff = info.get("effect", {})
                return {
                    "action": "skill", "skill_name": sk["skill_name"],
                    "skill_mult": 0.0, "is_magic": False,
                    "skill_effect": eff,
                    "mana_cost": info.get("mana", 10),
                }

    # 3. Best offensive/debuff skill
    best = None; best_score = 0
    for sk in skills:
        info = SKILL_DB.get(sk["skill_name"], {})
        stype = info.get("type","")
        if stype not in ("attack","burst","magic","ranged","debuff"):
            continue
        if unit.cooldowns.get(sk["skill_name"], 0) > 0:
            continue
        mana = info.get("mana", 10)
        if unit.mana < mana:
            continue
        mult  = info.get("multiplier", 1.0)
        # Score: multiplier + enemy HP consideration
        score = mult * (1.5 if enemy.hp / max(enemy.hp_max,1) < 0.30 else 1.0)
        if score > best_score:
            best_score = score
            eff = info.get("effect") or {}
            best = {
                "action": "skill", "skill_name": sk["skill_name"],
                "skill_mult": mult,
                "is_magic": info.get("scaling") == "spirit",
                "skill_effect": eff,
                "mana_cost": mana,
            }

    if best:
        return best

    # 4. Stealth if Shadow class and not in stealth
    if not unit.stealth and any(s["skill_name"] == "Shadow Sneak" for s in skills):
        info = SKILL_DB.get("Shadow Sneak", {})
        if unit.mana >= info.get("mana", 0) and unit.cooldowns.get("Shadow Sneak", 0) == 0:
            return {"action":"stealth","skill_name":"Shadow Sneak","skill_mult":0,"is_magic":False,"skill_effect":{},"mana_cost":0}

    # 5. Basic attack
    return {"action":"attack","skill_name":"","skill_mult":1.0,"is_magic":False,"skill_effect":{},"mana_cost":0}
