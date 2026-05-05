# airi/rpg/quests.py — Daily Quest System
# References:
#   - Galaxy manhwa: Rank E/B quest system, hidden objectives, mission ratings
#   - Torn Tales Discord RPG: daily engagement hooks, narrative ties
#   - MapleStory: rotating daily tasks with tiered rewards
#
# 3 daily quests per player, rotate at midnight UTC
# Quest types: kill_mobs, kill_boss, clear_floors, use_skills, survive_turns
# Reward: pending_xp multiplier (1.5×) for 24h + kakera + bonus coins
# Hidden quest: unlocks if main quest completed with S/A rating

import random
from datetime import datetime, timezone, date

# ── Quest Pool ──────────────────────────────────────────────────
QUEST_POOL = [
    # Tier I quests (always available)
    {
        "id": "kill5_mobs",
        "name": "🗡️ First Blood",
        "desc": "Defeat 5 monsters in any dungeon.",
        "type": "kill_mobs", "target": 5,
        "reward": {"pending_xp": 300, "coins": 200, "kakera": 5},
        "tier": 1,
    },
    {
        "id": "clear1_dungeon",
        "name": "🗺️ Explorer",
        "desc": "Clear any dungeon from floor 1 to the boss.",
        "type": "clear_dungeon", "target": 1,
        "reward": {"pending_xp": 500, "coins": 400, "kakera": 10},
        "tier": 1,
    },
    {
        "id": "kill1_boss",
        "name": "☠️ Boss Slayer",
        "desc": "Defeat a dungeon boss.",
        "type": "kill_boss", "target": 1,
        "reward": {"pending_xp": 600, "coins": 500, "kakera": 15},
        "tier": 1,
    },
    {
        "id": "use_skills_5",
        "name": "✨ Skill Practitioner",
        "desc": "Use skills 5 times in combat.",
        "type": "use_skills", "target": 5,
        "reward": {"pending_xp": 250, "coins": 150, "kakera": 5},
        "tier": 1,
    },
    {
        "id": "survive_10turns",
        "name": "🛡️ Endurance Test",
        "desc": "Survive 10 turns in a single combat.",
        "type": "survive_turns", "target": 10,
        "reward": {"pending_xp": 350, "coins": 250, "kakera": 8},
        "tier": 1,
    },
    # Tier II quests (level 10+)
    {
        "id": "kill10_mobs",
        "name": "🔥 Massacre",
        "desc": "Defeat 10 monsters in dungeons.",
        "type": "kill_mobs", "target": 10,
        "reward": {"pending_xp": 800, "coins": 600, "kakera": 20},
        "tier": 2,
    },
    {
        "id": "clear2_dungeons",
        "name": "⚔️ Veteran Adventurer",
        "desc": "Complete 2 full dungeon runs.",
        "type": "clear_dungeon", "target": 2,
        "reward": {"pending_xp": 1200, "coins": 900, "kakera": 30},
        "tier": 2,
    },
    {
        "id": "kill3_bosses",
        "name": "👑 Boss Hunter",
        "desc": "Defeat 3 dungeon bosses.",
        "type": "kill_boss", "target": 3,
        "reward": {"pending_xp": 1500, "coins": 1200, "kakera": 40},
        "tier": 2,
    },
    {
        "id": "no_potion_run",
        "name": "💪 Iron Will",
        "desc": "Complete a dungeon without using any potions.",
        "type": "no_potion_clear", "target": 1,
        "reward": {"pending_xp": 1000, "coins": 800, "kakera": 25, "bonus": "xp_mult"},
        "tier": 2,
    },
    {
        "id": "s_rank_clear",
        "name": "🌟 Perfection",
        "desc": "Clear a dungeon with an S-rank run rating.",
        "type": "s_rank_clear", "target": 1,
        "reward": {"pending_xp": 2000, "coins": 1500, "kakera": 50, "gems": 1},
        "tier": 2,
    },
    # Tier III quests (level 25+)
    {
        "id": "nightmare_clear",
        "name": "💀 Nightmare Walker",
        "desc": "Complete a full dungeon on Nightmare difficulty.",
        "type": "nightmare_clear", "target": 1,
        "reward": {"pending_xp": 3000, "coins": 2500, "kakera": 80, "gems": 2},
        "tier": 3,
    },
    {
        "id": "kill_boss_low_hp",
        "name": "🩸 On the Brink",
        "desc": "Defeat a boss while at 20% HP or less.",
        "type": "boss_low_hp", "target": 1,
        "reward": {"pending_xp": 2500, "coins": 2000, "kakera": 70, "gems": 1},
        "tier": 3,
    },
    {
        "id": "use_nightmare",
        "name": "😴 Sandman",
        "desc": "Trigger Eye Break (Nightmare Gauge) 5 times.",
        "type": "eye_break", "target": 5,
        "reward": {"pending_xp": 2000, "coins": 1800, "kakera": 60},
        "tier": 3,
    },
]

# Hidden quests — unlock on special conditions
HIDDEN_QUESTS = [
    {
        "id": "first_s_rank",
        "name": "⭐ Legend Rising",
        "desc": "HIDDEN: Achieved your first S-rank dungeon clear!",
        "reward": {"pending_xp": 5000, "coins": 3000, "kakera": 100, "gems": 3},
        "trigger": "s_rank_clear",
    },
    {
        "id": "fear_resist",
        "name": "🧠 Unbreakable",
        "desc": "HIDDEN: Resisted the Fear Gauge 10 times.",
        "reward": {"pending_xp": 3000, "coins": 2000, "kakera": 80},
        "trigger": "fear_resist_10",
    },
    {
        "id": "all_daily_complete",
        "name": "🏆 Daily Champion",
        "desc": "HIDDEN: Completed all 3 daily quests in one day!",
        "reward": {"pending_xp": 4000, "coins": 2500, "kakera": 120, "xp_mult_24h": 1.5},
        "trigger": "all_daily_done",
    },
]

def get_daily_quests(char_level: int, seed_date: date | None = None) -> list[dict]:
    """
    Return 3 daily quests appropriate for the player's level.
    Uses date as seed so the same player gets the same quests all day.
    """
    today = seed_date or date.today()
    seed = int(today.strftime("%Y%m%d"))
    rng  = random.Random(seed)

    # Determine available tiers
    tiers = [1]
    if char_level >= 10: tiers.append(2)
    if char_level >= 25: tiers.append(3)

    pool = [q for q in QUEST_POOL if q["tier"] in tiers]
    rng.shuffle(pool)

    # Pick 3 varied quests (different types)
    chosen = []
    used_types = set()
    for q in pool:
        if q["type"] not in used_types:
            chosen.append(dict(q, progress=0, completed=False))
            used_types.add(q["type"])
        if len(chosen) == 3:
            break

    # Fill remaining if not enough variety
    for q in pool:
        if len(chosen) >= 3: break
        if q not in chosen:
            chosen.append(dict(q, progress=0, completed=False))

    return chosen[:3]

def check_quest_progress(quests: list[dict], event: str, value: int = 1) -> list[dict]:
    """
    Update quest progress based on a game event.
    Events: kill_mob, kill_boss, clear_dungeon, use_skill, survive_turns,
            s_rank_clear, nightmare_clear, eye_break, boss_low_hp, no_potion_clear
    Returns list of newly completed quests.
    """
    newly_done = []
    event_map = {
        "kill_mob": "kill_mobs",
        "kill_boss": "kill_boss",
        "clear_dungeon": "clear_dungeon",
        "use_skill": "use_skills",
        "survive_turn": "survive_turns",
        "s_rank_clear": "s_rank_clear",
        "nightmare_clear": "nightmare_clear",
        "eye_break": "eye_break",
        "boss_low_hp": "boss_low_hp",
        "no_potion_clear": "no_potion_clear",
    }
    qtype = event_map.get(event)
    if not qtype: return newly_done

    for q in quests:
        if q.get("completed"): continue
        if q["type"] == qtype:
            q["progress"] = min(q["target"], q.get("progress", 0) + value)
            if q["progress"] >= q["target"]:
                q["completed"] = True
                newly_done.append(q)

    return newly_done

def format_quests_embed(quests: list[dict]) -> list[dict]:
    """Return formatted quest fields for embed."""
    fields = []
    for q in quests:
        prog  = q.get("progress", 0)
        tgt   = q["target"]
        done  = q.get("completed", False)
        bar   = ("█" * int(prog / tgt * 10)).ljust(10, "░")
        rwd   = q["reward"]
        rwd_s = f"+{rwd.get('pending_xp',0)} XP  ·  +{rwd.get('coins',0)} 🪙  ·  +{rwd.get('kakera',0)} 💎"
        if rwd.get("gems"): rwd_s += f"  ·  +{rwd['gems']} 💎✨"
        fields.append({
            "name": ("✅ " if done else "🔲 ") + q["name"],
            "value": (
                q["desc"] + "\n"
                + f"`{bar}` {prog}/{tgt}"
                + ("\n**COMPLETE!**" if done else "")
                + f"\n*Reward: {rwd_s}*"
            ),
            "inline": False,
        })
    return fields

def calc_run_rating(
    floors_cleared: int,
    total_floors: int,
    hp_pct: float,
    total_turns: int,
    used_potions: int = 0,
    eye_breaks: int = 0,
) -> str:
    """
    Calculate run performance rating.
    Referenced from Galaxy manhwa mission rating system (base + excellence bonus).
    S = near perfect, A = strong, B = average, C = survived, D = rough
    """
    score = (floors_cleared / max(total_floors, 1)) * 100
    score += hp_pct * 25         # up to +25 for high HP
    score -= total_turns * 0.3   # penalize slow runs
    score -= used_potions * 5    # penalize potion reliance
    score += eye_breaks * 3      # bonus for using mechanics

    if score >= 100: return "S"
    if score >= 80:  return "A"
    if score >= 60:  return "B"
    if score >= 40:  return "C"
    return "D"

RATING_EMOJI = {"S": "🌟", "A": "🔶", "B": "🔷", "C": "⬜", "D": "🔴"}
RATING_COLOR = {"S": 0xffd700, "A": 0xf39c12, "B": 0x3498db, "C": 0x95a5a6, "D": 0xe74c3c}
