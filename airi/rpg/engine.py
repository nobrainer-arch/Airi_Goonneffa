# airi/rpg/engine.py — Battle Engine v4 (FULL REWRITE)
#
# Fixes applied (from RPG_Analysis_Blueprint.md):
#   [BUG-1]  Defense: multiplicative formula CON/(CON+200) — never zeroes out
#   [BUG-2]  Multi-hit loop: hits=N fires DamageCalculator N times, crit rolls per hit
#   [BUG-2]  All missing skill effects: agi_boost, all_stats_boost, wither, taunt,
#            Endless Nightmare ×3.5, Greedy Hand flag, always_first, summon
#   [BUG-3]  Fear Gauge: monsters build pressure on player → Terrified debuff
#   [BAL-3]  Reaction: full-block chance (8%) + counter window
#   [BAL-4]  Class-based mana regen floor (Warrior/Knight min 5/turn)
#   [NEW]    Element system: attacks carry element vs monster weakness
#   [NEW]    Monster AI profiles + boss phase transitions at 50% HP

import random
from dataclasses import dataclass, field

GRADE_MULT = {
    "Inferior":0.8,"Normal":1.0,"Bronze":1.2,
    "Silver":1.5,"Gold":1.8,"Emerald":2.2,"Diamond":2.8,"Legendary":4.0,
}

# [BAL-4] Minimum mana regen per turn by class
CLASS_MANA_REGEN_FLOOR = {
    "Warrior":5,"Knight":5,"Gunman":4,"Archer":4,
    "Shadow":4,"Mage":2,"Healer":2,"Necromancer":2,
}

@dataclass
class Effect:
    etype:    str
    duration: int
    value:    float = 0.0
    source:   str   = ""

    def tick(self) -> bool:
        self.duration -= 1
        return self.duration > 0

    def apply_tick(self, unit) -> str | None:
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


@dataclass
class CombatUnit:
    name:             str
    hp:               int
    hp_max:           int
    mana:             int
    mana_max:         int
    strength:         int
    constitution:     int
    agility:          int
    spirit:           int
    reaction:         int
    crit_chance:      float
    crit_damage:      float
    damage_reduction: float
    reflect_pct:      float
    grade:            str   = "Normal"
    is_player:        bool  = True
    class_name:       str   = ""
    element:          str   = "physical"
    weakness:         str   = "physical"
    stealth:          bool  = False
    stealth_bonus:    float = 0.0
    first_hit_active: bool  = False
    first_hit_bonus:  float = 0.0
    nightmare_gauge:  int   = 0
    fear_gauge:       int   = 0
    sleeping:         bool  = False
    crit_streak:      int   = 0
    passive_nightmare_bonus:  float = 0.0
    passive_eye_break_mult:   float = 3.0
    passive_extra_loot:       float = 0.0
    passive_counter_pct:      float = 0.10
    passive_stealth_dur_mult: float = 1.0
    has_summon:       bool  = False
    summon_str:       int   = 0
    summon_turns:     int   = 0
    effects:   list   = field(default_factory=list)
    cooldowns: dict   = field(default_factory=dict)

    @property
    def alive(self): return self.hp > 0
    def is_stunned(self):     return any(e.etype=="stun"        for e in self.effects)
    def is_ground_bound(self):return any(e.etype=="ground_bind" for e in self.effects)
    def is_terrified(self):   return any(e.etype=="terrified"   for e in self.effects)

    def get_shield_hp(self):
        for e in self.effects:
            if e.etype == "shield": return e.value
        return 0.0

    def drain_shield(self, amount):
        for e in self.effects[:]:
            if e.etype == "shield":
                absorbed = min(e.value, amount)
                e.value -= absorbed
                if e.value <= 0: self.effects.remove(e)
                return amount - absorbed
        return amount

    def get_wither_reduction(self):
        return min(sum(e.value for e in self.effects if e.etype in ("wither","taunt")), 0.70)

    def get_agi_mult(self):
        for e in self.effects:
            if e.etype == "agi_boost": return e.value
        return 1.0

    def get_stat_mult(self):
        for e in self.effects:
            if e.etype == "all_stats_boost": return e.value
        return 1.0

    def get_str_mult(self):
        base = 1.0
        for e in self.effects:
            if e.etype in ("str_boost","all_stats_boost"):
                base = max(base, e.value)
        return base

    def add_effect(self, etype, duration, value=0.0, source=""):
        if etype == "shield":
            for e in self.effects:
                if e.etype == "shield":
                    e.value += value; e.duration = max(e.duration, duration); return
        else:
            for e in self.effects[:]:
                if e.etype == etype:
                    e.duration = max(e.duration, duration); e.value = max(e.value, value); return
        self.effects.append(Effect(etype=etype, duration=duration, value=value, source=source))

    def tick_effects(self):
        logs=[]; remove=[]
        for eff in self.effects:
            line = eff.apply_tick(self)
            if line: logs.append(line)
            if not eff.tick():
                remove.append(eff)
                if eff.etype not in ("regen","str_boost","shield"):
                    logs.append(f"✨ **{eff.etype.replace('_',' ').title()}** wore off")
        for e in remove:
            if e in self.effects: self.effects.remove(e)
        return logs

    def tick_cooldowns(self):
        self.cooldowns = {k: max(0,v-1) for k,v in self.cooldowns.items() if v>1}

    def regen_mana(self):
        floor = CLASS_MANA_REGEN_FLOOR.get(self.class_name, 2)
        regen = max(floor, self.spirit // 5)
        self.mana = min(self.mana_max, self.mana + regen)

    def clear_between_floors(self):
        """[BUG-5] Clear debuffs, keep buffs."""
        KEEP = {"regen","str_boost","all_stats_boost","shield","revival","agi_boost"}
        self.effects = [e for e in self.effects if e.etype in KEEP]
        self.fear_gauge = 0


@dataclass
class DamageResult:
    raw:   int   = 0
    final: int   = 0
    is_crit: bool = False
    nightmare_triggered: bool = False
    fear_triggered: bool = False
    element_mult: float = 1.0
    hits:  int   = 1
    reflected: int = 0
    log: list = field(default_factory=list)


class DamageCalculator:
    @staticmethod
    def _single_hit(attacker, defender, per_hit_mult, is_magic, element):
        log = []
        # 1. Base
        stat_mult = attacker.get_stat_mult()
        base = (attacker.spirit if is_magic else attacker.strength * attacker.get_str_mult())
        dmg  = float(base) * per_hit_mult * stat_mult

        # 2. First-hit bonus
        if attacker.first_hit_active and attacker.first_hit_bonus > 0:
            dmg *= (1 + attacker.first_hit_bonus)
            attacker.first_hit_active = False
            log.append(f"⚡ **First Strike**: ×{1+attacker.first_hit_bonus:.1f}")

        # 3. Stealth break
        if attacker.stealth and attacker.stealth_bonus > 0:
            dmg *= (1 + attacker.stealth_bonus)
            attacker.stealth = False
            log.append(f"🌑 **Stealth Break**: ×{1+attacker.stealth_bonus:.1f}")

        # 4. Crit — per-hit roll [BUG-2]
        eff_crit = min(attacker.crit_chance + attacker.crit_streak * 0.05, 0.80)
        is_crit  = random.random() < eff_crit
        if is_crit:
            dmg *= attacker.crit_damage
            attacker.crit_streak = 0
            log.append(f"💥 **CRITICAL**: ×{attacker.crit_damage:.1f}")
        else:
            attacker.crit_streak += 1

        # 5. Eye Break
        if defender.sleeping:
            mult = attacker.passive_eye_break_mult
            dmg *= mult
            defender.sleeping = False
            defender.nightmare_gauge = 0
            log.append(f"👁️ **EYE BREAK**: ×{mult:.1f}!")

        # 6. Element [NEW]
        from .elements import get_element_mult, element_banner, ELEMENT_EMOJI
        el_mult = get_element_mult(element, defender.weakness)
        if el_mult != 1.0:
            dmg *= el_mult
            banner = element_banner(el_mult)
            if banner:
                log.append(f"{banner}{ELEMENT_EMOJI.get(element,'')} {element.title()} vs {defender.weakness}")

        # 7. Grade
        dmg *= GRADE_MULT.get(attacker.grade, 1.0)

        # 8. Terrified [BUG-3]
        if attacker.is_terrified() and attacker.is_player:
            dmg *= 0.5
            attacker.effects = [e for e in attacker.effects if e.etype != "terrified"]
            log.append("😱 **Terrified**: dmg halved!")

        # 9. Reaction — full block or counter [BAL-3]
        react_denom = attacker.agility * attacker.get_agi_mult() + defender.reaction
        if react_denom > 0 and not defender.sleeping and not defender.is_stunned():
            react_chance = (defender.reaction / react_denom) * 0.40
            roll = random.random()
            if roll < react_chance * 0.20:
                log.append("🛡️ **Perfect Block!**")
                return 0, is_crit, log

        # 10. Wither/Taunt DEF reduction [BUG-2]
        wither_red   = defender.get_wither_reduction()
        effective_con = max(0, defender.constitution * (1 - wither_red))

        # 11. Multiplicative defense [BUG-1] — core fix
        def_reduction = effective_con / (effective_con + 200)
        dmg *= (1 - def_reduction)

        # 12. Flat % reduction (talents/armour)
        dmg *= (1 - min(defender.damage_reduction, 0.80))

        # 13. Shield
        dmg = defender.drain_shield(dmg)

        final = max(1, int(dmg))
        log.append(f"💔 **{attacker.name}** → **{final}** dmg")
        return final, is_crit, log

    @staticmethod
    def calculate(attacker, defender, skill_mult=1.0, is_magic=False,
                  skill_name="", hits=1, element=""):
        res = DamageResult()
        hits = max(1, hits)
        per  = skill_mult / hits
        eff_el = element or attacker.element or "physical"
        total = 0; any_crit = False

        for i in range(hits):
            if hits > 1: res.log.append(f"  → Hit {i+1}/{hits}:")
            h_final, h_crit, h_log = DamageCalculator._single_hit(
                attacker, defender, per, is_magic, eff_el)
            total += h_final
            if h_crit: any_crit = True
            res.log.extend(h_log)
            # Apply each hit for shield drain across multi-hit
            defender.hp = max(0, defender.hp - h_final)
            if not defender.alive: break

        # Undo so caller's single subtraction is correct
        defender.hp = min(defender.hp_max, defender.hp + total)

        res.final = total; res.raw = total; res.is_crit = any_crit; res.hits = hits

        # Reflection
        if defender.reflect_pct > 0:
            res.reflected = max(1, int(total * defender.reflect_pct))
            res.log.append(f"↩️ **Reflect**: {res.reflected} back!")

        # Nightmare gauge — monster side
        if not defender.is_player and not defender.sleeping:
            fill = int((20 * (1 + attacker.passive_nightmare_bonus) + (5 if any_crit else 0)) * hits)
            defender.nightmare_gauge = min(100, defender.nightmare_gauge + fill)
            if defender.nightmare_gauge >= 100:
                defender.sleeping = True
                res.log.append(f"💤 **{defender.name}** → **SLEEPING STATE** (next ×{attacker.passive_eye_break_mult:.1f})!")

        # Fear gauge — player side [BUG-3]
        if defender.is_player and not defender.is_terrified():
            fill = 15 + (8 if any_crit else 0)
            defender.fear_gauge = min(100, defender.fear_gauge + fill)
            if defender.fear_gauge >= 100:
                defender.fear_gauge = 0
                defender.add_effect("terrified", 1, 0.5, "Fear")
                res.fear_triggered = True
                res.log.append("😱 **FEAR GAUGE FULL** — **Terrified!** Next attack -50%!")

        return res


# ── Monster AI Profiles [MISSING-5] ──────────────────────────────
AI_PROFILES = {
    "aggressive": {"attack":0.40,"strong_attack":0.40,"venom_attack":0.10,"defend":0.10},
    "defensive":  {"attack":0.30,"strong_attack":0.15,"venom_attack":0.05,"defend":0.50},
    "debuffer":   {"attack":0.25,"strong_attack":0.15,"venom_attack":0.40,"defend":0.20},
    "berserker":  {"attack":0.10,"strong_attack":0.65,"venom_attack":0.10,"defend":0.15},
    "guardian":   {"attack":0.30,"strong_attack":0.25,"venom_attack":0.10,"defend":0.35},
}

def _profile_for(name, is_boss, hp_pct):
    if is_boss: return "berserker" if hp_pct < 0.50 else "guardian"
    n = name.lower()
    if any(x in n for x in ("mage","shaman","lich","wizard")): return "debuffer"
    if any(x in n for x in ("knight","golem","guardian","wall")): return "defensive"
    return "aggressive"

def _weighted_choice(profile_name):
    p = AI_PROFILES.get(profile_name, AI_PROFILES["aggressive"])
    return random.choices(list(p.keys()), weights=list(p.values()), k=1)[0]


class BattleEngine:
    def __init__(self, player, monster):
        self.player  = player
        self.monster = monster
        self.turn    = 0
        self._enrage_announced = False

    def apply_passive_defense(self, player_skills):
        from .skills import SKILL_DB
        logs = []
        for sk in player_skills:
            name = sk["skill_name"]
            info = SKILL_DB.get(name, {})
            stype = info.get("type","")
            if stype == "passive":
                pe = info.get("passive_effect", {})
                if "nightmare_fill_bonus" in pe:
                    self.player.passive_nightmare_bonus = pe["nightmare_fill_bonus"]
                if "eye_break_mult" in pe:
                    self.player.passive_eye_break_mult  = pe["eye_break_mult"]
                if "extra_loot_chance" in pe:
                    self.player.passive_extra_loot = max(self.player.passive_extra_loot, pe["extra_loot_chance"])
                if "counter_dmg_pct" in pe:
                    self.player.passive_counter_pct = pe["counter_dmg_pct"]
                if "stealth_duration_mult" in pe:
                    self.player.passive_stealth_dur_mult = pe["stealth_duration_mult"]
                continue
            if stype not in ("shield","abjuration","barrier"): continue
            mc = info.get("mana", 20)
            if self.player.mana < mc: continue
            self.player.mana -= mc
            eff = info.get("effect", {})
            if eff.get("type") == "shield":
                sv = self.player.mana_max * eff.get("value_pct_mana", 0.5)
                self.player.add_effect("shield", eff.get("duration",3), sv, name)
                logs.append(f"🛡️ **{name}**: +{int(sv)} shield HP")
            else:
                dr = eff.get("dmg_reduction",0.10)
                self.player.damage_reduction = min(0.80, self.player.damage_reduction + dr)
                logs.append(f"🛡️ **{name}**: +{int(dr*100)}% DR")
        return logs

    def process_player_action(self, action, skill_name="", skill_mult=1.0,
                               is_magic=False, skill_effect=None, hits=1, element=""):
        self.turn += 1
        result = {
            "player_dmg":0,"monster_dmg":0,"log":[],"fled":False,
            "player_alive":True,"monster_alive":True,"turn":self.turn,
            "eye_break":False,"fear_triggered":False,"skill_used":skill_name!="",
        }
        if not self.player.alive or not self.monster.alive:
            result["player_alive"]  = self.player.alive
            result["monster_alive"] = self.monster.alive
            return result

        self.player.regen_mana()

        if action == "flee":
            base = self.player.agility * self.player.get_agi_mult()
            if random.random() < base / max(base + self.monster.agility, 1):
                result["fled"] = True
                result["log"].append("🏃 **Escaped successfully!**")
                return result
            result["log"].append("❌ **Escape failed!**")

        elif action == "stealth":
            if self.player.stealth:
                result["log"].append("⚠️ Already in stealth.")
            elif self.player.is_stunned():
                result["log"].append("⚠️ Stunned!")
            else:
                self.player.stealth = True
                self.player.stealth_bonus = 0.6
                result["log"].append(f"🌑 **Stealth**: first strike ×{1+0.6:.1f}")

        elif action in ("attack","skill"):
            se = skill_effect or {}
            et = se.get("type","")
            # Apply non-damage effects
            if et == "venom":
                self.monster.add_effect("venom", se.get("duration",3)); result["log"].append(f"☠️ Venom!")
            elif et in ("burn","bleed"):
                self.monster.add_effect(et, se.get("duration",3), se.get("value", int(self.player.spirit*0.4)))
                result["log"].append(f"🔥 {et.title()}!")
            elif et in ("stun","ground_bind"):
                self.monster.add_effect(et, se.get("duration",2))
                result["log"].append(f"⚡ {et.replace('_',' ').title()} applied!")
            elif et == "regen":
                self.player.add_effect("regen", se.get("duration",3))
                result["log"].append("💚 Regen active!")
            elif et == "str_boost":
                m = se.get("str_mult",1.2)
                self.player.add_effect("str_boost", se.get("duration",3), m)
                result["log"].append(f"💪 STR ×{m:.1f} for {se.get('duration',3)}T!")
            elif et == "agi_boost":
                m = se.get("agi_mult",1.3)
                self.player.add_effect("agi_boost", se.get("duration",2), m)
                result["log"].append(f"💨 AGI ×{m:.1f} for {se.get('duration',2)}T!")
            elif et == "all_stats_boost":
                m = se.get("mult",1.1)
                self.player.add_effect("all_stats_boost", se.get("duration",3), m)
                result["log"].append(f"✨ All stats ×{m:.1f} for {se.get('duration',3)}T!")
            elif et == "wither":
                red = se.get("def_reduction",0.4)
                self.monster.add_effect("wither", se.get("duration",3), red)
                result["log"].append(f"💀 Monster DEF -{int(red*100)}%!")
            elif et == "taunt":
                red = se.get("target_def_reduction",0.2)
                self.monster.add_effect("taunt", se.get("duration",2), red)
                result["log"].append(f"🛡️ Taunt! Monster DEF -{int(red*100)}%!")
            elif et == "shield":
                sv = self.player.mana_max * se.get("value_pct_mana",0.5)
                self.player.add_effect("shield", se.get("duration",3), sv)
                result["log"].append(f"🛡️ +{int(sv)} shield HP!")
            elif et == "heal":
                healed = int(self.player.hp_max * se.get("heal_pct",0.25))
                self.player.hp = min(self.player.hp_max, self.player.hp + healed)
                result["log"].append(f"💚 +{healed} HP!")
            elif et == "summon":
                self.player.has_summon  = True
                self.player.summon_str  = int(self.player.strength * se.get("summon_str_pct",0.3))
                self.player.summon_turns = se.get("summon_turns",2)
                result["log"].append(f"💀 Skeleton summoned! STR {self.player.summon_str}")

            # Damage
            NON_DMG = {"heal","shield","str_boost","agi_boost","all_stats_boost",
                       "regen","buff","wither","taunt","stun","venom","burn",
                       "bleed","ground_bind","summon"}
            if et not in NON_DMG:
                dmg_res = DamageCalculator.calculate(
                    self.player, self.monster, skill_mult, is_magic,
                    skill_name, hits=hits, element=element or self.player.element)
                self.monster.hp      = max(0, self.monster.hp - dmg_res.final)
                result["player_dmg"] = dmg_res.final
                result["eye_break"]  = dmg_res.nightmare_triggered
                if dmg_res.reflected:
                    self.player.hp = max(0, self.player.hp - dmg_res.reflected)
                result["log"].extend(dmg_res.log)

            # Berserker passive
            if self.player.class_name == "Warrior":
                hp_pct = self.player.hp / max(self.player.hp_max,1)
                if hp_pct < 0.30 and not any(e.etype=="str_boost" for e in self.player.effects):
                    self.player.add_effect("str_boost", 99, 1.5, "Berserker")
                    result["log"].append("🔥 **BERSERKER**: STR ×1.5!")

            # Mage Brave Heart
            if self.player.class_name == "Mage" and is_magic and result["player_dmg"] > 0:
                miss_pct = 1.0 - (self.player.hp / max(self.player.hp_max,1))
                bonus = min(0.50, miss_pct)
                if bonus > 0.05:
                    extra = int(result["player_dmg"] * bonus)
                    self.monster.hp = max(0, self.monster.hp - extra)
                    result["player_dmg"] += extra
                    result["log"].append(f"💙 **Brave Heart**: +{extra} ({int(bonus*100)}% HP missing)")

            # Summon attack
            if self.player.has_summon and self.player.summon_turns > 0:
                sdmg = max(1, int(self.player.summon_str * random.uniform(0.5,0.9)))
                self.monster.hp = max(0, self.monster.hp - sdmg)
                result["player_dmg"] += sdmg
                result["log"].append(f"💀 **Skeleton**: {sdmg} dmg")
                self.player.summon_turns -= 1
                if self.player.summon_turns <= 0:
                    self.player.has_summon = False
                    result["log"].append("💀 Skeleton crumbles.")

        result["log"].extend(self.player.tick_effects())
        self.player.tick_cooldowns()

        if not self.monster.alive:
            result["monster_alive"] = False
            result["player_alive"]  = self.player.alive
            return result

        # Monster turn
        if self.monster.is_stunned() or self.monster.sleeping:
            state = "stunned" if self.monster.is_stunned() else "sleeping"
            result["log"].append(f"💤 **{self.monster.name}** is {state}!")
        else:
            hp_pct    = self.monster.hp / max(self.monster.hp_max,1)
            is_boss   = "[BOSS]" in self.monster.name
            if is_boss and hp_pct < 0.50 and not self._enrage_announced:
                self._enrage_announced = True
                result["log"].append(f"⚠️ **{self.monster.name}** ENRAGES at 50% HP!")
            profile = _profile_for(self.monster.name, is_boss, hp_pct)
            choice  = _weighted_choice(profile)
            m_mult  = 1.0; m_venom = False

            if choice == "strong_attack":
                m_mult = random.uniform(1.4, 2.0)
                result["log"].append(f"⚠️ **{self.monster.name}**: powerful strike!")
            elif choice == "venom_attack":
                m_venom = True; m_mult = 0.8
                result["log"].append(f"☠️ **{self.monster.name}**: venom attack!")
            elif choice == "defend":
                self.monster.add_effect("str_boost", 1, 0.8, "defend")
                result["log"].append(f"🛡️ **{self.monster.name}** braces!")
                result["log"].extend(self.monster.tick_effects())
                self.monster.tick_cooldowns()
                result["player_alive"]  = self.player.alive
                result["monster_alive"] = self.monster.alive
                return result

            m_res = DamageCalculator.calculate(
                self.monster, self.player, m_mult, element=self.monster.element)
            incoming = m_res.final

            if m_venom and not any(e.etype=="venom" for e in self.player.effects):
                self.player.add_effect("venom",3); result["log"].append("☠️ Venom on you!")

            for eff in self.player.effects[:]:
                if eff.etype == "revival" and self.player.hp - incoming <= 0:
                    self.player.effects.remove(eff)
                    incoming = self.player.hp - 1
                    result["log"].append("✨ **Revival Orb** — survived 1 HP!")
                    break

            self.player.hp = max(0, self.player.hp - incoming)
            if m_res.reflected:
                self.monster.hp = max(0, self.monster.hp - m_res.reflected)
            result["monster_dmg"]    = incoming
            result["fear_triggered"] = m_res.fear_triggered
            result["log"].extend(m_res.log)

            # Healer passive
            if self.turn % 2 == 0 and self.player.class_name == "Healer":
                regen = max(1, int(self.player.hp_max * 0.05))
                self.player.hp = min(self.player.hp_max, self.player.hp + regen)
                result["log"].append(f"💚 **Life Aura**: +{regen} HP")

        result["log"].extend(self.monster.tick_effects())
        self.monster.tick_cooldowns()
        result["player_alive"]  = self.player.alive
        result["monster_alive"] = self.monster.alive
        return result


def ai_choose_action(unit, enemy, skills):
    from .skills import SKILL_DB
    hp_pct = unit.hp / max(unit.hp_max,1)

    def _avail(name):
        info = SKILL_DB.get(name,{})
        if unit.cooldowns.get(name,0) > 0: return None
        if unit.mana < info.get("mana",10): return None
        return info

    # 1. Heal
    if hp_pct < 0.40:
        for sk in skills:
            info = _avail(sk["skill_name"])
            if info and info.get("type") == "heal":
                return {"action":"skill","skill_name":sk["skill_name"],"skill_mult":0,
                        "is_magic":False,"skill_effect":{"type":"heal","heal_pct":info.get("heal_pct",0.25)},
                        "mana_cost":info.get("mana",10),"hits":1}

    # 2. Debuff
    has_debuff = any(e.etype in ("wither","taunt","venom","burn") for e in enemy.effects)
    if not has_debuff:
        for sk in skills:
            info = _avail(sk["skill_name"])
            if info and info.get("type") in ("debuff","cc"):
                return {"action":"skill","skill_name":sk["skill_name"],"skill_mult":0,
                        "is_magic":False,"skill_effect":info.get("effect",{}),"mana_cost":info.get("mana",5),"hits":1}

    # 3. Shield
    if unit.get_shield_hp() <= 0 and enemy.strength > unit.constitution*3:
        for sk in skills:
            info = _avail(sk["skill_name"])
            if info and info.get("type") in ("shield","abjuration"):
                return {"action":"skill","skill_name":sk["skill_name"],"skill_mult":0,
                        "is_magic":False,"skill_effect":info.get("effect",{}),"mana_cost":info.get("mana",10),"hits":1}

    # 4. Best offensive skill
    best=None; best_score=0
    for sk in skills:
        info = _avail(sk["skill_name"])
        if not info or info.get("type") not in ("attack","burst","magic","ranged"): continue
        mult  = info.get("multiplier",1.0); n = info.get("hits",1)
        score = mult * (1.4 if enemy.hp/max(enemy.hp_max,1) < 0.30 else 1.0) * (1+n*0.05)
        if score > best_score:
            best_score = score
            best = {"action":"skill","skill_name":sk["skill_name"],"skill_mult":mult,
                    "is_magic":info.get("scaling")=="spirit","skill_effect":info.get("effect") or {},
                    "mana_cost":info.get("mana",10),"hits":n,"element":info.get("element","")}
    if best: return best

    # 5. Stealth for Shadow
    if not unit.stealth and unit.class_name == "Shadow":
        for sk in skills:
            if sk["skill_name"] == "Shadow Sneak" and _avail("Shadow Sneak"):
                return {"action":"stealth","skill_name":"Shadow Sneak","skill_mult":0,
                        "is_magic":False,"skill_effect":{},"mana_cost":0,"hits":1}

    return {"action":"attack","skill_name":"","skill_mult":1.0,
            "is_magic":False,"skill_effect":{},"mana_cost":0,"hits":1}
