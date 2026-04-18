# airi/rpg/engine.py — Combat Engine
# Based EXACTLY on Kinfang transcript mechanics:
# - Speed/reaction system (action_time = 100/agi)
# - Stealth with break-strike bonus
# - Backstab from outside vision (×2.0)
# - Damage stacks: stealth_bonus × backstab × crit × grade_mult
# - Multi-layer defense pipeline
# - Effect system: poison, stun, ground-bind, nightmare
# - Cooldowns tick per-action

import random
from dataclasses import dataclass, field
from typing import Optional

# ── Grade multipliers (item/skill grade from transcript) ──────────
GRADE_MULT = {
    "Inferior": 0.8, "Normal": 1.0, "Bronze": 1.2,
    "Silver": 1.5,  "Gold": 1.8,  "Emerald": 2.2,
    "Diamond": 2.8, "Legendary": 4.0,
}

# ── Effect types ──────────────────────────────────────────────────
EFFECT_TYPES = {"venom", "stun", "ground_bind", "nightmare", "burn", "bleed", "shield"}


@dataclass
class Effect:
    type: str
    duration: int          # turns remaining
    value: float = 0.0     # damage per turn (venom), or gauge fill (nightmare)
    source: str = ""

    def tick(self) -> bool:
        """Tick one turn. Returns True if still active."""
        self.duration -= 1
        return self.duration > 0


@dataclass
class DamageResult:
    raw: int = 0
    after_stealth: int = 0
    after_backstab: int = 0
    after_crit: int = 0
    after_grade: int = 0
    after_reduction: int = 0
    after_defence: int = 0
    after_shield: int = 0
    final: int = 0
    is_crit: bool = False
    is_backstab: bool = False
    is_stealth_break: bool = False
    nightmare_triggered: bool = False
    reflected: int = 0
    log: list[str] = field(default_factory=list)


@dataclass
class CombatUnit:
    name: str
    hp: int
    hp_max: int
    mana: int
    mana_max: int
    strength: int
    constitution: int    # defence scaling: flat_def = constitution × 0.5
    agility: int         # action speed: action_time = 100 / agility
    spirit: int          # magic scaling
    reaction: int        # chance to counter-attack
    crit_chance: float   # 0.0–1.0
    crit_damage: float   # multiplier (default 1.5)
    damage_reduction: float  # 0.0–1.0, capped at 0.80
    reflect_pct: float   # % of received damage reflected (Knight passive)
    grade: str           # Normal, Bronze, Silver etc.
    is_player: bool      # True = player, False = monster
    # Combat state
    stealth: bool = False
    stealth_turns: int = 0
    stealth_break_bonus: float = 0.0   # stacks from skills
    first_hit_active: bool = False      # Ring of Skeleton King / Gunman passive
    first_hit_bonus: float = 0.0
    nightmare_gauge: int = 0            # 0→100, then monster sleeps
    sleeping: bool = False
    effects: list[Effect] = field(default_factory=list)
    cooldowns: dict[str, int] = field(default_factory=dict)  # skill_name → turns remaining
    action_time: float = 0.0            # ticks until next action

    def __post_init__(self):
        self.action_time = 100.0 / max(self.agility, 1)
        self.damage_reduction = min(self.damage_reduction, 0.80)  # hard cap

    @property
    def flat_defence(self) -> float:
        return self.constitution * 0.5

    @property
    def alive(self) -> bool:
        return self.hp > 0

    def has_effect(self, etype: str) -> bool:
        return any(e.type == etype for e in self.effects)

    def get_effect(self, etype: str) -> Optional[Effect]:
        return next((e for e in self.effects if e.type == etype), None)

    def add_effect(self, etype: str, duration: int, value: float = 0.0, source: str = ""):
        """Add effect. Venom does NOT stack — refresh instead."""
        existing = self.get_effect(etype)
        if existing and etype == "venom":
            existing.duration = max(existing.duration, duration)
            return
        self.effects.append(Effect(type=etype, duration=duration, value=value, source=source))

    def tick_effects(self) -> list[str]:
        """Process all effects. Returns log lines."""
        logs = []
        remove = []
        for eff in self.effects:
            if eff.type == "venom":
                dmg = max(1, int(self.hp_max * 0.05))  # 5% max HP per turn (50%/turn as described)
                self.hp = max(0, self.hp - dmg)
                logs.append(f"☠️ {self.name} takes **{dmg}** venom damage ({eff.duration-1} turns left)")
            if not eff.tick():
                remove.append(eff)
                logs.append(f"✨ {self.name}: [{eff.type}] wore off")
        for e in remove:
            self.effects.remove(e)
        return logs

    def tick_cooldowns(self):
        for k in list(self.cooldowns):
            if self.cooldowns[k] > 0:
                self.cooldowns[k] -= 1
            if self.cooldowns[k] <= 0:
                del self.cooldowns[k]

    def is_stunned(self) -> bool:
        return self.has_effect("stun")

    def is_ground_bound(self) -> bool:
        return self.has_effect("ground_bind")


# ── Damage Calculator ─────────────────────────────────────────────
class DamageCalculator:
    """
    Full damage pipeline from transcript:
    base(STR) → ×stealth_bonus(1.6→1.9) → ×backstab(2.0 if outside vision)
    → ×crit(1.5) → ×eye_break(3.0 nightmare wake) → ×grade_mult
    → ×(1 - dmg_reduction) → -flat_defence(constitution×0.5)
    → -shield → final (min 1)
    """

    @staticmethod
    def calculate(
        attacker: CombatUnit,
        defender: CombatUnit,
        skill_multiplier: float = 1.0,
        is_outside_vision: bool = False,
        skill_name: str = "",
    ) -> DamageResult:
        res = DamageResult()
        log = res.log

        # ── Step 1: Base damage ───────────────────────────────────
        base = attacker.strength * skill_multiplier
        res.raw = int(base)
        log.append(f"Raw: **{res.raw}** (STR {attacker.strength} × {skill_multiplier:.2f})")

        damage = float(base)

        # ── Step 2: First-hit bonus (Gunman / Ring of Skeleton King) ─
        if attacker.first_hit_active and attacker.first_hit_bonus > 0:
            damage *= (1 + attacker.first_hit_bonus)
            log.append(f"⚡ First Hit: ×{1+attacker.first_hit_bonus:.1f}")
            attacker.first_hit_active = False  # consumed

        # ── Step 3: Stealth break bonus ───────────────────────────
        if attacker.stealth and attacker.stealth_break_bonus > 0:
            res.is_stealth_break = True
            mult = 1.0 + attacker.stealth_break_bonus
            damage *= mult
            log.append(f"🌑 Stealth Break: ×{mult:.1f} (+{attacker.stealth_break_bonus*100:.0f}%)")
            attacker.stealth = False
            attacker.stealth_turns = 0

        res.after_stealth = int(damage)

        # ── Step 4: Backstab (outside vision) ────────────────────
        if is_outside_vision and (attacker.stealth or res.is_stealth_break):
            res.is_backstab = True
            damage *= 2.0
            log.append("🗡️ Backstab: ×2.0 (outside vision)")
        res.after_backstab = int(damage)

        # ── Step 5: Critical hit ──────────────────────────────────
        if random.random() < attacker.crit_chance:
            res.is_crit = True
            damage *= attacker.crit_damage
            log.append(f"💥 Critical Hit: ×{attacker.crit_damage:.1f}")
        res.after_crit = int(damage)

        # ── Step 6: Nightmare eye-break (×3.0 on sleeping monster) ─
        if defender.sleeping:
            damage *= 3.0
            res.nightmare_triggered = True
            defender.sleeping = False
            defender.nightmare_gauge = 0
            log.append("👁️ EYE BREAK on sleeping enemy: ×3.0!")
        res.after_grade = int(damage)

        # ── Step 7: Grade multiplier ──────────────────────────────
        grade_m = GRADE_MULT.get(attacker.grade, 1.0)
        damage *= grade_m
        if grade_m != 1.0:
            log.append(f"📊 Grade [{attacker.grade}]: ×{grade_m}")

        # ── Step 8: Damage reduction % ───────────────────────────
        red = min(defender.damage_reduction, 0.80)
        damage *= (1.0 - red)
        if red > 0:
            log.append(f"🛡️ Reduction {red*100:.0f}%: -{int(damage * red / (1-red+1e-9))}")
        res.after_reduction = int(damage)

        # ── Step 9: Flat defence ──────────────────────────────────
        flat_def = defender.flat_defence
        damage -= flat_def
        if flat_def > 0:
            log.append(f"🛡️ Flat DEF (CON×0.5): -{int(flat_def)}")
        res.after_defence = int(damage)

        # ── Step 10: Shield absorption ────────────────────────────
        shield_eff = defender.get_effect("shield")
        if shield_eff and shield_eff.value > 0:
            absorbed = min(shield_eff.value, damage)
            shield_eff.value -= absorbed
            damage -= absorbed
            log.append(f"🔵 Shield absorbed: {int(absorbed)}")
            if shield_eff.value <= 0:
                defender.effects.remove(shield_eff)
        res.after_shield = int(damage)

        # ── Step 11: Final (minimum 1) ────────────────────────────
        res.final = max(1, int(damage))
        log.append(f"💔 Final damage: **{res.final}**")

        # ── Step 12: Reflect (Knight passive) ─────────────────────
        if defender.reflect_pct > 0:
            res.reflected = max(1, int(res.final * defender.reflect_pct))
            log.append(f"↩️ Reflected: {res.reflected} back to {attacker.name}")

        # ── Step 13: Nightmare gauge (for sleeping bosses) ────────
        if not defender.is_player and not defender.sleeping:
            gauge_fill = 15  # per hit
            defender.nightmare_gauge = min(100, defender.nightmare_gauge + gauge_fill)
            if defender.nightmare_gauge >= 100:
                defender.sleeping = True
                log.append(f"💤 {defender.name} enters **SLEEPING STATE**! Next hit deals ×3.0!")

        return res


# ── Reaction System ───────────────────────────────────────────────
class ReactionSystem:
    """
    reaction_chance = defender.reaction / (attacker.agility + defender.reaction)
    If reaction triggered: defender gets a counter-attack at 50% strength.
    """
    @staticmethod
    def check_reaction(attacker: CombatUnit, defender: CombatUnit) -> bool:
        if defender.is_stunned() or defender.sleeping or defender.stealth:
            return False
        total = attacker.agility + defender.reaction
        if total <= 0: return False
        chance = defender.reaction / total
        return random.random() < chance

    @staticmethod
    def counter_attack(attacker: CombatUnit, defender: CombatUnit) -> DamageResult:
        """Defender counter-attacks at 50% strength."""
        old_str = defender.strength
        defender.strength = max(1, int(defender.strength * 0.5))
        result = DamageCalculator.calculate(defender, attacker)
        defender.strength = old_str
        result.log.insert(0, f"⚡ **COUNTER** by {defender.name}!")
        return result


# ── Battle Engine ─────────────────────────────────────────────────
class BattleEngine:
    """
    Turn-based wrapper for Discord bot use.
    Each call to process_turn() resolves ONE player action
    and ONE monster action (if monster hasn't been killed).
    """

    def __init__(self, player: CombatUnit, monster: CombatUnit):
        self.player  = player
        self.monster = monster
        self.turn    = 0
        self.log: list[str] = []

    def _apply_damage(self, target: CombatUnit, result: DamageResult):
        target.hp = max(0, target.hp - result.final)
        if result.reflected > 0:
            # Apply reflection to the attacker
            attacker = self.monster if target == self.player else self.player
            attacker.hp = max(0, attacker.hp - result.reflected)

    def process_player_action(
        self,
        action: str,           # "attack", "skill", "stealth", "flee"
        skill_name: str = "",
        is_outside_vision: bool = False,
        skill_multiplier: float = 1.0,
    ) -> dict:
        """Process one player action. Returns summary dict."""
        self.turn += 1
        self.log.clear()
        result_data = {"player_dmg": 0, "monster_dmg": 0, "log": [], "fled": False,
                       "player_alive": True, "monster_alive": True}

        if not self.player.alive or not self.monster.alive:
            return result_data

        # ── Player action ──────────────────────────────────────
        if action == "flee":
            flee_chance = self.player.agility / (self.player.agility + self.monster.agility)
            if random.random() < flee_chance:
                # Heal to full on successful flee
                self.player.hp = self.player.hp_max
                self.player.mana = self.player.mana_max
                result_data["fled"] = True
                result_data["log"] = ["🏃 You fled successfully and recovered fully!"]
                return result_data
            else:
                self.log.append("❌ Escape failed!")

        elif action == "stealth":
            # Can't stealth while already in stealth or stunned
            if self.player.stealth:
                self.log.append("⚠️ Already in stealth!")
            elif self.player.is_stunned():
                self.log.append("⚠️ Can't stealth while stunned!")
            else:
                self.player.stealth = True
                self.player.stealth_turns = 12  # base duration from transcript
                self.log.append("🌑 You entered **Shadow Sneak** (stealth for 12 turns). First hit ×1.6!")

        elif action in ("attack", "skill"):
            mult = skill_multiplier if action == "skill" else 1.0
            res  = DamageCalculator.calculate(self.player, self.monster, mult, is_outside_vision)
            self._apply_damage(self.monster, res)
            self.log.extend(res.log)
            result_data["player_dmg"] = res.final

            # Berserker passive: low HP
            if (self.player.hp / self.player.hp_max) < 0.30:
                self.player.strength = int(self.player.strength * 1.5)
                self.log.append("🔥 BERSERK: STR ×1.5 (HP below 30%)")

            # Mage passive: kill grants stat point
            if not self.monster.alive and self.player.is_player:
                self.log.append("✨ Mage Brave Heart: +1 stat point from kill!")

            # Reaction check
            if self.monster.alive and ReactionSystem.check_reaction(self.player, self.monster):
                counter = ReactionSystem.counter_attack(self.player, self.monster)
                self._apply_damage(self.player, counter)
                self.log.extend(counter.log)
                result_data["monster_dmg"] += counter.final

        # ── Tick effects on player ──────────────────────────────
        self.log.extend(self.player.tick_effects())
        self.player.tick_cooldowns()

        # ── Monster action (if alive and player not fled) ──────
        if self.monster.alive and not result_data["fled"]:
            if self.monster.is_stunned() or self.monster.sleeping:
                self.log.append(f"💤 {self.monster.name} is {'stunned' if self.monster.is_stunned() else 'sleeping'} — cannot act!")
            elif self.player.stealth and self.monster.reaction < self.player.agility:
                # Monster can't see stealthed player
                self.log.append(f"👁️ {self.monster.name} can't detect you (stealth).")
            else:
                m_res = DamageCalculator.calculate(self.monster, self.player, 1.0)
                self._apply_damage(self.player, m_res)
                self.log.extend(m_res.log)
                result_data["monster_dmg"] += m_res.final

                # Healer passive: regen every 2 turns
                if self.turn % 2 == 0:
                    regen = max(1, int(self.player.hp_max * 0.05))
                    self.player.hp = min(self.player.hp_max, self.player.hp + regen)
                    self.log.append(f"💚 Light's Touch: +{regen} HP regen")

        # ── Tick effects on monster ─────────────────────────────
        self.log.extend(self.monster.tick_effects())
        self.monster.tick_cooldowns()

        result_data["log"] = list(self.log)
        result_data["player_alive"] = self.player.alive
        result_data["monster_alive"] = self.monster.alive
        return result_data
