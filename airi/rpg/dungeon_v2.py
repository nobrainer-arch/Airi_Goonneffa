# airi/rpg/dungeon_v2.py — Dungeon with difficulties, travel delay, loot grades,
# auto-battle, cooldown-remover item, dungeon events
import discord
from discord.ext import commands
from discord.ext import tasks
import random, asyncio, aiohttp
from datetime import datetime, timezone, timedelta
import db
from utils import _err, C_INFO, C_WARN, C_SUCCESS, C_ERROR
from .engine import CombatUnit, BattleEngine
from .stats  import get_char, get_skills, get_equipment
from .classes import CLASSES, get_realm
from .skills  import SKILL_DB
from .battle_image import generate_battle_card
from .character import (
    add_char_xp, get_char_full, scale_monster, get_dungeon_tier,
    DIFFICULTY_MULT, DUNGEON_TIERS, calc_hp_max, calc_mana_max
)

DND_API       = "https://www.dnd5eapi.co/api"
BASE_CD_SECS  = 300    # 5 min base cooldown
MAX_CD_USES   = 10     # max cooldown-remover charges per user

# ── Loot grade system ──────────────────────────────────────────────
LOOT_GRADES = ["F","E","D","C","B","A","S","SS","SSS"]
GRADE_WEIGHTS_NORMAL    = [400,250,150,80,50,30,15,5,2]
GRADE_WEIGHTS_NIGHTMARE = [200,200,150,100,100,80,50,30,15]
GRADE_WEIGHTS_HELL      = [50,100,150,150,150,150,100,80,50]

GRADE_REWARDS = {
    "F":  {"coins":(5,20),    "kakera":0,  "gems":0},
    "E":  {"coins":(20,60),   "kakera":1,  "gems":0},
    "D":  {"coins":(60,150),  "kakera":2,  "gems":0},
    "C":  {"coins":(150,350), "kakera":5,  "gems":0},
    "B":  {"coins":(350,700), "kakera":10, "gems":1},
    "A":  {"coins":(700,1500),"kakera":20, "gems":1},
    "S":  {"coins":(1500,3000),"kakera":40,"gems":2},
    "SS": {"coins":(3000,6000),"kakera":80,"gems":3},
    "SSS":{"coins":(6000,15000),"kakera":200,"gems":5},
}

# ── Travel flavor (like Dank Memer) ────────────────────────────────
TRAVEL_MSGS = [
    "🚶 You venture into the **{dungeon}**…",
    "⚔️ You steel yourself and enter the **{dungeon}**…",
    "🌑 The shadows swallow you as you step into **{dungeon}**…",
    "🔥 Heat radiates from the entrance of **{dungeon}**…",
    "💀 The air grows cold as you descend into **{dungeon}**…",
    "🗡️ Your hand tightens on your weapon as you enter **{dungeon}**…",
]
ENCOUNTER_MSGS = [
    "👁️ Something moves in the darkness…",
    "⚠️ You hear growling ahead…",
    "💥 An enemy leaps from the shadows!",
    "🐾 Footsteps — you're not alone.",
    "🌀 A monster materializes before you!",
]
DUNGEON_NAMES = {
    1: ["Goblin Cave","Bandit Hideout","Ruined Temple"],
    2: ["Orc Stronghold","Dark Forest","Cursed Mines"],
    3: ["Dragon's Lair","Undead Keep","Shadow Realm"],
    4: ["Abyss Gate","Titan's Tomb","Void Dungeon"],
    5: ["God's Abandoned Realm","Nightmare Domain","Hell's Gateway"],
}

# ── Caches ──────────────────────────────────────────────────────────
_monster_list: list[dict] = []
_monster_cache: dict[str,dict] = {}

async def _get_json(url):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url,timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200: return await r.json()
    except: pass
    return None

async def _load_monster_list():
    global _monster_list
    if _monster_list: return _monster_list
    d = await _get_json(f"{DND_API}/monsters")
    _monster_list = d.get("results",[]) if d else []
    return _monster_list

async def _load_monster(slug: str) -> dict | None:
    if slug in _monster_cache: return _monster_cache[slug]
    d = await _get_json(f"{DND_API}/monsters/{slug}")
    if d: _monster_cache[slug] = d
    return d

# ── Monster builder ────────────────────────────────────────────────
def _parse_monster(data: dict, tier: int, difficulty: str) -> dict:
    name    = data.get("name","Unknown")
    cr      = float(data.get("challenge_rating",1) or 1)
    hp_base = data.get("hit_points",30)
    ac_list = data.get("armor_class",[{"value":12}])
    ac      = ac_list[0].get("value",12) if ac_list else 12
    str_    = data.get("strength",10)
    dex     = data.get("dexterity",10)
    con     = data.get("constitution",10)
    wis     = data.get("wisdom",10)

    cap  = DUNGEON_TIERS[tier]["monster_stat_cap"]
    mult = DIFFICULTY_MULT.get(difficulty,{}).get("stat",1.0)

    base = {
        "str": max(5, (str_-10)*2+8),
        "def": max(2, ac//2 + (con-10)//2),
        "agi": max(3, (dex-10)+8),
        "spi": max(3, (wis-10)+5),
        "hp":  hp_base,
        "con": max(5, (con-10)*2+8),
    }

    scaled = {k: min(int(v*mult), cap) for k,v in base.items() if k != "hp"}
    scaled["hp"] = min(int(hp_base * mult), cap * 50)

    xp_base  = int(cr * 100)
    xp_gain  = int(xp_base * mult)
    kakera   = max(1, xp_gain//25)
    is_boss  = cr >= 5
    if is_boss: kakera *= 3; xp_gain = int(xp_gain * 1.5)
    gem_drop = (random.random() < (0.30 if is_boss else 0.10))

    actions = [a.get("name","Attack") for a in data.get("actions",[])[:3]]
    img_url = data.get("image")

    return {
        "name":      name,
        "type":      f"{data.get('size','Medium')} {data.get('type','Monster')}",
        "hp":        scaled["hp"], "hp_max": scaled["hp"],
        "mp":0,"mp_max":0,
        "str":       scaled["str"], "def": scaled["def"],
        "agi":       scaled["agi"], "spi": scaled["spi"],
        "reaction":  scaled["agi"],
        "dmg_reduction": min(0.4, ac/50),
        "xp":        xp_gain,
        "char_xp":   int(xp_gain * 0.5),  # character XP (separate from server XP)
        "kakera":    kakera,
        "gem_drop":  gem_drop,
        "is_boss":   is_boss,
        "image_url": img_url,
        "skills":    [{"name":a,"rank":"C","on_cd":False} for a in actions],
        "weapon":    actions[0] if actions else "Claws",
        "armor":     f"AC {ac}",
        "color":     (180,55,55) if is_boss else (100,120,160),
        "coins":     (scaled["str"]*3, scaled["str"]*8),
    }

def _fallback_monster(tier: int, difficulty: str) -> dict:
    POOL = {
        1:[{"name":"Crawler","str":12,"def":4,"agi":14,"spi":3,"hp":40,"is_boss":False}],
        2:[{"name":"Corruptor","str":18,"def":12,"agi":8,"spi":8,"hp":70,"is_boss":False}],
        3:[{"name":"Nurturer","str":28,"def":15,"agi":12,"spi":10,"hp":200,"is_boss":True}],
        4:[{"name":"Curse Master","str":45,"def":25,"agi":18,"spi":22,"hp":400,"is_boss":True}],
        5:[{"name":"Nightmare King","str":100,"def":40,"agi":60,"spi":30,"hp":1000,"is_boss":True}],
    }
    base = dict(random.choice(POOL.get(tier, POOL[1])))
    mult = DIFFICULTY_MULT.get(difficulty,{}).get("stat",1.0)
    cap  = DUNGEON_TIERS[tier]["monster_stat_cap"]
    for k in ("str","def","agi","spi","hp"):
        base[k] = min(int(base[k]*mult), cap*(50 if k=="hp" else 1))
    base.update({
        "hp_max":base["hp"],"mp":0,"mp_max":0,
        "type":"Monster","reaction":base["agi"],
        "dmg_reduction":0.05,"image_url":None,
        "skills":[{"name":"Bite","rank":"F","on_cd":False}],
        "weapon":"Claws","armor":"Scales","color":(180,55,55),
        "coins":(base["str"]*3,base["str"]*8),
        "xp":int(base["str"]*10*mult),
        "char_xp":int(base["str"]*5*mult),
        "kakera":max(1,int(base["str"]*10*mult//25)),
        "gem_drop":base.get("is_boss",False),
    })
    return base

async def _get_monster(tier: int, difficulty: str) -> dict:
    ml = await _load_monster_list()
    if ml:
        slug = random.choice(ml)["index"]
        data = await _load_monster(slug)
        if data: return _parse_monster(data, tier, difficulty)
    return _fallback_monster(tier, difficulty)

# ── Loot roll ──────────────────────────────────────────────────────
def _roll_loot(difficulty: str, luck_bonus: float = 0.0) -> str:
    """Roll a loot grade. luck_bonus shifts weights toward rare."""
    weights = {
        "normal":    list(GRADE_WEIGHTS_NORMAL),
        "nightmare": list(GRADE_WEIGHTS_NIGHTMARE),
        "hell":      list(GRADE_WEIGHTS_HELL),
    }.get(difficulty, list(GRADE_WEIGHTS_NORMAL))

    # Apply luck: shift weight from bottom 3 to top 3
    luck_shift = int(luck_bonus * 50)
    for i in range(3):
        weights[i]   = max(1, weights[i] - luck_shift)
        weights[-1-i]+= luck_shift
    return random.choices(LOOT_GRADES, weights=weights, k=1)[0]

async def _get_luck_bonus(gid: int, uid: int) -> float:
    """Check luck accessories. Returns 0.0–1.0 bonus."""
    rows = await db.pool.fetch(
        "SELECT effect_key, effect_value FROM rpg_equipment WHERE guild_id=$1 AND user_id=$2",
        gid, uid
    )
    luck = 0.0
    for r in rows:
        if "luck" in str(r.get("effect_key","")).lower():
            luck += float(r.get("effect_value",0) or 0)
    return min(1.0, luck)

# ── Cooldown remover ───────────────────────────────────────────────
async def get_cd_remover_charges(gid: int, uid: int) -> int:
    v = await db.pool.fetchval(
        "SELECT cd_remover_charges FROM rpg_characters WHERE guild_id=$1 AND user_id=$2",
        gid, uid
    )
    return int(v or 0)

async def use_cd_remover(gid: int, uid: int) -> bool:
    charges = await get_cd_remover_charges(gid, uid)
    if charges <= 0: return False
    await db.pool.execute(
        "UPDATE rpg_characters SET cd_remover_charges=cd_remover_charges-1 WHERE guild_id=$1 AND user_id=$2",
        gid, uid
    )
    # Clear the explore cooldown
    await db.pool.execute(
        "UPDATE work_log SET last_explore=NULL WHERE guild_id=$1 AND user_id=$2",
        gid, uid
    )
    return True

# ── Battle View ────────────────────────────────────────────────────
class BattleView(discord.ui.View):
    def __init__(self, ctx, char: dict, skills: list, monster: dict, eq: list,
                 difficulty: str, tier: int):
        super().__init__(timeout=300)
        self._ctx        = ctx
        self._char       = char
        self._monster    = monster
        self._skills_db  = skills
        self._eq         = eq
        self._difficulty = difficulty
        self._tier       = tier
        self._running    = True
        self._log: list[str] = []
        self._last_rewards: dict = {}

        cls      = CLASSES.get(char["class"], {})
        cls_col  = cls.get("color", 0x4444ff)
        cls_rgb  = ((cls_col>>16)&0xff,(cls_col>>8)&0xff,cls_col&0xff)

        vit = char.get("vitality",10)
        con = char.get("constitution",10)
        spi = char.get("spirit",10)
        clvl= char.get("char_level", char.get("realm_level",1))
        hp_max = calc_hp_max(con, vit, clvl)
        mn_max = calc_mana_max(spi, clvl)

        pc = CombatUnit(
            name=ctx.author.display_name,
            hp=char["hp_current"], hp_max=hp_max,
            mana=char["mana_current"], mana_max=mn_max,
            strength=char["strength"], constitution=con,
            agility=char["agility"], spirit=spi,
            reaction=spi,
            crit_chance=cls.get("base",{}).get("crit_chance",0.08),
            crit_damage=1.5,
            damage_reduction=cls.get("base",{}).get("damage_reduction",0.05),
            reflect_pct=0.10 if char["class"]=="Knight" else 0.0,
            grade="Normal", is_player=True,
            first_hit_active=char["class"]=="Gunman",
            first_hit_bonus=0.5 if char["class"]=="Gunman" else 0.0,
        )
        mc = CombatUnit(
            name=monster["name"],
            hp=monster["hp"], hp_max=monster["hp_max"],
            mana=0, mana_max=0,
            strength=monster["str"], constitution=monster["def"],
            agility=monster["agi"], spirit=monster.get("spi",5),
            reaction=monster["reaction"],
            crit_chance=0.05, crit_damage=1.5,
            damage_reduction=monster.get("dmg_reduction",0.05),
            reflect_pct=0.0, grade="Normal", is_player=False,
        )
        self._engine    = BattleEngine(pc, mc)
        self._cls_rgb   = cls_rgb
        self._mon_col   = monster.get("color",(180,55,55))
        self._gid       = ctx.guild.id
        self._uid       = ctx.author.id

    def _build_diff_banner(self) -> str:
        d = DIFFICULTY_MULT.get(self._difficulty,{})
        return d.get("label","⚔️ Normal")

    def _skill_list(self) -> list[dict]:
        return [{"name":s["skill_name"],"rank":s.get("skill_rank","F"),
                 "on_cd":self._engine.player.cooldowns.get(s["skill_name"],0)>0}
                for s in self._skills_db[:3]]

    async def _render(self) -> discord.File:
        p = self._engine.player; m = self._engine.monster
        eq_map = {e["slot"]:e for e in self._eq}
        buf = await generate_battle_card(
            player_name=self._ctx.author.display_name,
            player_class=self._char["class"],
            player_hp=max(0,p.hp), player_hp_max=p.hp_max,
            player_mp=max(0,p.mana), player_mp_max=p.mana_max,
            player_str=p.strength, player_def=p.constitution, player_agi=p.agility,
            player_skills=self._skill_list(),
            player_weapon=eq_map.get("weapon",{}).get("item_name","Unarmed"),
            player_armor=eq_map.get("armor",{}).get("item_name","None"),
            player_avatar_url=str(self._ctx.author.display_avatar.url),
            player_class_color=self._cls_rgb,
            monster_name=m.name,
            monster_type=self._monster.get("type","Monster"),
            monster_hp=max(0,m.hp), monster_hp_max=m.hp_max,
            monster_mp=0, monster_mp_max=0,
            monster_str=m.strength, monster_def=m.constitution, monster_agi=m.agility,
            monster_skills=self._monster.get("skills",[])[:3],
            monster_weapon=self._monster.get("weapon","Claws"),
            monster_armor=self._monster.get("armor","Hide"),
            monster_image_url=self._monster.get("image_url"),
            monster_color=self._mon_col,
            effects_player=[f"{e.type}({e.duration}T)" for e in self._engine.player.effects[:3]],
            effects_monster=[f"{e.type}({e.duration}T)" for e in self._engine.monster.effects[:3]],
            combat_log=self._log[-2:],
            turn_owner="player" if self._running else "none",
            sleeping=self._engine.monster.sleeping,
        )
        return discord.File(buf, filename="battle.png")

    def _embed(self) -> discord.Embed:
        p = self._engine.player; m = self._engine.monster
        diff_lbl = self._build_diff_banner()
        e = discord.Embed(
            title=f"{diff_lbl}  ·  {p.name} vs {m.name}",
            color=DIFFICULTY_MULT.get(self._difficulty,{}).get("color",0xe67e22),
        )
        e.set_image(url="attachment://battle.png")
        if self._log:
            e.description = f"`{self._log[-1]}`"
        if not self._running:
            rw = self._last_rewards
            if not m.alive:
                loot_grade = rw.get("grade","F")
                e.description = (
                    f"✅ **Victory!**  [{loot_grade} loot]\n"
                    f"+{rw.get('char_xp',0)} char XP  ·  +{rw.get('coins',0):,} 🪙  ·  "
                    f"+{rw.get('kakera',0)} 💎" +
                    (f"  ·  +{rw.get('gems',0)} gems" if rw.get('gems') else "")
                )
            else:
                e.description = f"💀 **Defeated.** Respawned with 1 HP."
        return e

    async def _update(self, interaction: discord.Interaction, result: dict):
        for ln in result.get("log",[]): self._log.append(ln)
        if not result.get("player_alive",True) or not self._running and result.get("fled"):
            self._running = False
            await self._end(interaction, result.get("player_alive",True) and not result.get("fled"))
            return
        if not result.get("monster_alive",True):
            self._running = False
            await self._end(interaction, True)
            return
        if result.get("fled"):
            self._running = False
            for c in self.children: c.disabled = True
            f = await self._render()
            e = self._embed(); e.description = "🏃 You fled!"
            await interaction.edit_original_response(embed=e, attachments=[f], view=self)
            return
        self._upd_btns()
        f = await self._render()
        await interaction.edit_original_response(embed=self._embed(), attachments=[f], view=self)

    def _upd_btns(self):
        ok = self._running and self._engine.player.alive
        for c in self.children: c.disabled = not ok

    async def _end(self, interaction: discord.Interaction, victory: bool):
        for c in self.children: c.disabled = True
        gid, uid = self._gid, self._uid

        if victory:
            luck = await _get_luck_bonus(gid, uid)
            grade = _roll_loot(self._difficulty, luck)
            rwd   = GRADE_REWARDS[grade]
            coins = random.randint(*rwd["coins"])
            kak   = int(self._monster.get("kakera",1) * DIFFICULTY_MULT.get(self._difficulty,{}).get("loot",1))
            gems  = rwd["gems"] + (1 if self._monster.get("gem_drop") else 0)
            char_xp = int(self._monster.get("char_xp",50) * DIFFICULTY_MULT.get(self._difficulty,{}).get("xp",1))

            # CD remover rare drop (affected by luck)
            cd_drop_chance = 0.02 + luck * 0.03   # 2-5% base
            if self._monster.get("is_boss"): cd_drop_chance *= 3
            got_cd_item = random.random() < cd_drop_chance

            # XP grants
            await db.pool.execute("""
                INSERT INTO xp (guild_id,user_id,xp) VALUES ($1,$2,$3)
                ON CONFLICT (guild_id,user_id) DO UPDATE SET xp=xp.xp+$3
            """, gid, uid, int(self._monster.get("xp",50) * DIFFICULTY_MULT.get(self._difficulty,{}).get("xp",1)))

            char_result = await add_char_xp(gid, uid, char_xp)

            from airi.economy import add_coins as _ac
            await _ac(gid, uid, coins)
            from airi.kakera import add_kakera
            await add_kakera(gid, uid, kak)
            if gems:
                await db.pool.execute("UPDATE economy SET gems=gems+$1 WHERE guild_id=$2 AND user_id=$3", gems, gid, uid)
            if got_cd_item:
                charges = await get_cd_remover_charges(gid, uid)
                if charges < MAX_CD_USES:
                    await db.pool.execute(
                        "UPDATE rpg_characters SET cd_remover_charges=LEAST(cd_remover_charges+1,$1) WHERE guild_id=$2 AND user_id=$3",
                        MAX_CD_USES, gid, uid
                    )

            await db.pool.execute("""
                UPDATE rpg_characters SET hp_current=hp_max, mana_current=mana_max
                WHERE guild_id=$1 AND user_id=$2
            """, gid, uid)

            self._last_rewards = {"grade":grade,"coins":coins,"kakera":kak,"gems":gems,
                                  "char_xp":char_xp,"cd_item":got_cd_item,
                                  "leveled_up":char_result.get("leveled_up")}
        else:
            from airi.economy import get_balance, add_coins as _ac
            bal  = await get_balance(gid, uid)
            loss = min(500, bal//10)
            if loss: await _ac(gid, uid, -loss)
            await db.pool.execute(
                "UPDATE rpg_characters SET hp_current=1, mana_current=mana_max//2 WHERE guild_id=$1 AND user_id=$2",
                gid, uid
            )
            self._last_rewards = {}

        await db.pool.execute("""
            INSERT INTO work_log (guild_id,user_id,last_explore) VALUES ($1,$2,NOW())
            ON CONFLICT (guild_id,user_id) DO UPDATE SET last_explore=NOW()
        """, gid, uid)

        f = await self._render()
        await interaction.edit_original_response(embed=self._embed(), attachments=[f], view=self)

        # Level-up announcement
        if self._last_rewards.get("leveled_up"):
            await interaction.followup.send(
                embed=discord.Embed(
                    title="⬆️ LEVEL UP!",
                    description=f"**{interaction.user.display_name}** reached character **Level {char_result.get('new_level')}**!",
                    color=0xf1c40f,
                ),
                ephemeral=False,
            )
        if self._last_rewards.get("cd_item"):
            await interaction.followup.send(
                f"🎁 **{interaction.user.display_name}** found a **⏱️ Dungeon Cooldown Remover**!",
                ephemeral=False,
            )
        self.stop()

    @discord.ui.button(label="⚔️ Attack", style=discord.ButtonStyle.danger, row=0)
    async def atk(self, inter, btn):
        if inter.user.id != self._uid: return await inter.response.send_message("Not your battle.",ephemeral=True)
        if not self._running: return
        await inter.response.defer()
        result = self._engine.process_player_action("attack")
        await self._update(inter, result)

    @discord.ui.button(label="✨ Skill", style=discord.ButtonStyle.primary, row=0)
    async def skl(self, inter, btn):
        if inter.user.id != self._uid: return await inter.response.send_message("Not your battle.",ephemeral=True)
        if not self._running: return
        avail = [s for s in self._skills_db
                 if self._engine.player.cooldowns.get(s["skill_name"],0)<=0
                 and self._engine.player.mana >= SKILL_DB.get(s["skill_name"],{}).get("mana",10)]
        if not avail:
            return await inter.response.send_message("No skills available.",ephemeral=True)
        opts = [discord.SelectOption(label=f"{s['skill_name']} [{s.get('skill_rank','F')}]",
                                     value=s["skill_name"],
                                     description=f"Mana: {SKILL_DB.get(s['skill_name'],{}).get('mana',10)}")
                for s in avail[:25]]
        sel = discord.ui.Select(placeholder="Choose a skill…", options=opts)
        async def sel_cb(i2):
            if i2.user.id != self._uid: return await i2.response.send_message("Not for you.",ephemeral=True)
            await i2.response.defer()
            sname = sel.values[0]
            info  = SKILL_DB.get(sname,{})
            self._engine.player.mana -= info.get("mana",10)
            self._engine.player.cooldowns[sname] = 3
            mult = info.get("multiplier",1.0)
            if info.get("type") == "heal":
                hp_pct = info.get("heal_pct",0.25)
                heal   = int(self._engine.player.hp_max * hp_pct)
                self._engine.player.hp = min(self._engine.player.hp_max, self._engine.player.hp+heal)
                result = {"log":[f"💚 {sname}: +{heal} HP"],"player_alive":True,"monster_alive":True}
            elif info.get("type") == "stealth":
                result = self._engine.process_player_action("stealth")
            else:
                result = self._engine.process_player_action("skill", sname, skill_multiplier=mult)
            await self._update(i2, result)
        sel.callback = sel_cb
        sv = discord.ui.View(timeout=30); sv.add_item(sel)
        await inter.response.send_message("Choose:", view=sv, ephemeral=True)

    @discord.ui.button(label="🏃 Flee", style=discord.ButtonStyle.secondary, row=0)
    async def flee(self, inter, btn):
        if inter.user.id != self._uid: return await inter.response.send_message("Not your battle.",ephemeral=True)
        if not self._running: return
        await inter.response.defer()
        result = self._engine.process_player_action("flee")
        await self._update(inter, result)


# ── Auto-battle ────────────────────────────────────────────────────
class AutoBattleView(discord.ui.View):
    """Runs combat automatically, shows summary. Good for farming."""
    def __init__(self, ctx, char: dict, skills: list, monster: dict, eq: list,
                 difficulty: str, tier: int):
        super().__init__(timeout=120)
        self._ctx   = ctx
        self._char  = char
        self._skills= skills
        self._mon   = monster
        self._eq    = eq
        self._diff  = difficulty
        self._tier  = tier
        self._gid   = ctx.guild.id
        self._uid   = ctx.author.id

    async def run(self) -> dict:
        """Simulate the whole battle. Returns result dict."""
        cls   = CLASSES.get(self._char["class"],{})
        vit   = self._char.get("vitality",10)
        con   = self._char.get("constitution",10)
        spi   = self._char.get("spirit",10)
        clvl  = self._char.get("char_level",self._char.get("realm_level",1))

        from .engine import CombatUnit, BattleEngine
        pc = CombatUnit(
            name=self._ctx.author.display_name,
            hp=self._char["hp_current"], hp_max=calc_hp_max(con,vit,clvl),
            mana=self._char["mana_current"], mana_max=calc_mana_max(spi,clvl),
            strength=self._char["strength"], constitution=con,
            agility=self._char["agility"], spirit=spi, reaction=spi,
            crit_chance=cls.get("base",{}).get("crit_chance",0.08),
            crit_damage=1.5,
            damage_reduction=cls.get("base",{}).get("damage_reduction",0.05),
            reflect_pct=0.10 if self._char["class"]=="Knight" else 0.0,
            grade="Normal", is_player=True,
        )
        mc = CombatUnit(
            name=self._mon["name"],
            hp=self._mon["hp"], hp_max=self._mon["hp_max"],
            mana=0, mana_max=0,
            strength=self._mon["str"], constitution=self._mon["def"],
            agility=self._mon["agi"], spirit=5, reaction=self._mon["agi"],
            crit_chance=0.05, crit_damage=1.5,
            damage_reduction=self._mon.get("dmg_reduction",0.05),
            reflect_pct=0.0, grade="Normal", is_player=False,
        )
        engine  = BattleEngine(pc, mc)
        turns   = 0
        max_t   = 30  # prevent infinite loops

        while pc.alive and mc.alive and turns < max_t:
            # Use best available skill if possible
            best_skill = ""
            best_mult  = 1.0
            for s in self._skills:
                nm   = s["skill_name"]
                info = SKILL_DB.get(nm,{})
                if engine.player.cooldowns.get(nm,0) <= 0 and engine.player.mana >= info.get("mana",10):
                    m2 = info.get("multiplier",1.0)
                    if m2 > best_mult:
                        best_mult  = m2
                        best_skill = nm
            if best_skill:
                engine.player.mana -= SKILL_DB.get(best_skill,{}).get("mana",10)
                engine.player.cooldowns[best_skill] = 3
                engine.process_player_action("skill", best_skill, skill_multiplier=best_mult)
            else:
                engine.process_player_action("attack")
            turns += 1

        victory = not mc.alive
        return {"victory":victory, "turns":turns, "hp_left":pc.hp}

    @discord.ui.button(label="⚡ Run Auto-Battle", style=discord.ButtonStyle.danger)
    async def run_btn(self, inter: discord.Interaction, btn):
        if inter.user.id != self._uid:
            return await inter.response.send_message("Not for you.",ephemeral=True)
        await inter.response.defer()
        for c in self.children: c.disabled = True
        result = await self.run()
        gid, uid = self._gid, self._uid

        if result["victory"]:
            luck = await _get_luck_bonus(gid, uid)
            grade = _roll_loot(self._diff, luck)
            rwd   = GRADE_REWARDS[grade]
            coins = random.randint(*rwd["coins"])
            kak   = int(self._mon.get("kakera",1) * DIFFICULTY_MULT.get(self._diff,{}).get("loot",1))
            char_xp = int(self._mon.get("char_xp",50) * DIFFICULTY_MULT.get(self._diff,{}).get("xp",1))

            await db.pool.execute("""
                INSERT INTO xp (guild_id,user_id,xp) VALUES ($1,$2,$3)
                ON CONFLICT (guild_id,user_id) DO UPDATE SET xp=xp.xp+$3
            """, gid, uid, int(self._mon.get("xp",50)))
            await add_char_xp(gid, uid, char_xp)
            from airi.economy import add_coins as _ac; await _ac(gid, uid, coins)
            from airi.kakera import add_kakera; await add_kakera(gid, uid, kak)

            diff_lbl = DIFFICULTY_MULT.get(self._diff,{}).get("label","⚔️ Normal")
            e = discord.Embed(
                title=f"⚡ Auto-Battle: {diff_lbl}",
                description=(
                    f"**{inter.user.display_name}** defeated **{self._mon['name']}** "
                    f"in {result['turns']} turns!\n\n"
                    f"**[{grade} loot]**  ·  +{coins:,} 🪙  ·  +{kak} 💎  ·  +{char_xp} char XP"
                ),
                color=C_SUCCESS,
            )
        else:
            e = discord.Embed(
                title="⚡ Auto-Battle: Defeated",
                description=f"**{inter.user.display_name}** was defeated by **{self._mon['name']}**.",
                color=C_ERROR,
            )
        await inter.edit_original_response(embed=e, view=self)

        await db.pool.execute("""
            INSERT INTO work_log (guild_id,user_id,last_explore) VALUES ($1,$2,NOW())
            ON CONFLICT (guild_id,user_id) DO UPDATE SET last_explore=NOW()
        """, gid, uid)
        self.stop()

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, inter: discord.Interaction, btn):
        if inter.user.id != self._uid:
            return await inter.response.send_message("Not for you.",ephemeral=True)
        for c in self.children: c.disabled = True
        await inter.response.edit_message(
            content="Auto-battle cancelled.", view=self)
        self.stop()


# ── Difficulty Picker ──────────────────────────────────────────────
class DifficultyView(discord.ui.View):
    def __init__(self, ctx, char_level: int, on_pick):
        super().__init__(timeout=60)
        self._ctx  = ctx
        self._cb   = on_pick
        self._clvl = char_level

    def _embed(self) -> discord.Embed:
        tier  = get_dungeon_tier(self._clvl)
        dname = random.choice(DUNGEON_NAMES.get(tier, DUNGEON_NAMES[1]))
        e = discord.Embed(
            title=f"⚔️ Enter: {dname}",
            description=f"Choose your difficulty. Higher = more rewards, tougher monsters.\n**Your level:** {self._clvl}",
            color=C_INFO,
        )
        for diff, info in DIFFICULTY_MULT.items():
            locked = self._clvl < info["min_level"]
            e.add_field(
                name=f"{info['label']}" + (" 🔒" if locked else ""),
                value=(
                    f"×{info['stat']:.0f} monster stats  ·  ×{info['xp']:.0f} XP  ·  ×{info['loot']:.0f} loot\n"
                    + (f"*Unlocks at level {info['min_level']}*" if locked else "*Available*")
                ),
                inline=False,
            )
        e.set_footer(text="You have 60 seconds to choose")
        return e

    @discord.ui.button(label="⚔️ Normal",    style=discord.ButtonStyle.success, row=0)
    async def normal(self, inter, btn):
        if inter.user.id != self._ctx.author.id: return await inter.response.send_message("Not for you.",ephemeral=True)
        await self._cb("normal", inter); self.stop()

    @discord.ui.button(label="💀 Nightmare", style=discord.ButtonStyle.danger, row=0)
    async def nightmare(self, inter, btn):
        if inter.user.id != self._ctx.author.id: return await inter.response.send_message("Not for you.",ephemeral=True)
        if self._clvl < DIFFICULTY_MULT["nightmare"]["min_level"]:
            return await inter.response.send_message(f"❌ Nightmare unlocks at character level 10.",ephemeral=True)
        await self._cb("nightmare", inter); self.stop()

    @discord.ui.button(label="🔥 Hell",      style=discord.ButtonStyle.danger, row=0)
    async def hell(self, inter, btn):
        if inter.user.id != self._ctx.author.id: return await inter.response.send_message("Not for you.",ephemeral=True)
        if self._clvl < DIFFICULTY_MULT["hell"]["min_level"]:
            return await inter.response.send_message(f"❌ Hell unlocks at character level 20.",ephemeral=True)
        await self._cb("hell", inter); self.stop()

    @discord.ui.button(label="⚡ Auto (Normal)", style=discord.ButtonStyle.secondary, row=1)
    async def auto(self, inter, btn):
        if inter.user.id != self._ctx.author.id: return await inter.response.send_message("Not for you.",ephemeral=True)
        await self._cb("auto", inter); self.stop()


# ── Cog ────────────────────────────────────────────────────────────
class DungeonCog(commands.Cog, name="Dungeon"):
    def __init__(self, bot): self.bot = bot

    @commands.hybrid_command(name="dungeon", aliases=["explore","hunt","d"],
                             description="Enter a dungeon to fight monsters")
    async def dungeon(self, ctx):
        gid, uid = ctx.guild.id, ctx.author.id
        char = await get_char(gid, uid)
        if not char:
            return await ctx.send(embed=discord.Embed(
                description="No character yet! Use `/rpg` to create one.", color=C_WARN))

        char_level = char.get("char_level") or char.get("realm_level",1)
        tier = get_dungeon_tier(char_level)

        # Check cooldown
        cd_row = await db.pool.fetchrow(
            "SELECT last_explore FROM work_log WHERE guild_id=$1 AND user_id=$2", gid, uid
        )
        if cd_row and cd_row.get("last_explore"):
            last = cd_row["last_explore"]
            if not hasattr(last,"tzinfo") or last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc)-last).total_seconds()
            if elapsed < BASE_CD_SECS:
                rem   = int(BASE_CD_SECS - elapsed)
                charges = await get_cd_remover_charges(gid, uid)
                e = discord.Embed(
                    description=(
                        f"⏱️ Recovering. Ready in **{rem//60}m {rem%60}s**.\n"
                        + (f"You have **{charges}/{MAX_CD_USES}** cooldown charges — use `/usecd` to skip!" if charges else "")
                    ),
                    color=C_WARN,
                )
                return await ctx.send(embed=e, delete_after=15)

        # Show difficulty picker
        diff_view = DifficultyView(ctx, char_level, self._on_diff_pick)
        diff_view._tier = tier
        diff_view._char = char
        msg = await ctx.send(embed=diff_view._embed(), view=diff_view)
        diff_view._msg = msg

    async def _on_diff_pick(self, difficulty: str, interaction: discord.Interaction):
        await interaction.response.defer()
        gid, uid = interaction.guild_id, interaction.user.id
        char = await get_char(gid, uid)
        tier = get_dungeon_tier(char.get("char_level") or char.get("realm_level",1))

        if difficulty == "auto":
            # Skip straight to auto-battle setup
            monster = await _get_monster(tier, "normal")
            skills  = await get_skills(gid, uid)
            eq      = await get_equipment(gid, uid)
            ctx_fake = type("FC",(),{
                "author":interaction.user,"guild":interaction.guild,
                "channel":interaction.channel,"bot":interaction.client
            })()
            auto_view = AutoBattleView(ctx_fake, char, skills, monster, eq, "normal", tier)
            e = discord.Embed(
                title=f"⚡ Auto-Battle Setup",
                description=(
                    f"**Opponent:** {monster['name']} (Tier {tier})\n"
                    f"HP: {monster['hp']}  STR: {monster['str']}  AGI: {monster['agi']}\n\n"
                    "Click **Run Auto-Battle** to simulate the fight automatically."
                ),
                color=C_INFO,
            )
            await interaction.edit_original_response(embed=e, view=auto_view)
            return

        # Travel delay — feeling of distance (Dank Memer style)
        travel_secs = random.randint(5, 30)   # 5–30 second travel
        tier_names  = DUNGEON_NAMES.get(tier, DUNGEON_NAMES[1])
        dname       = random.choice(tier_names)
        travel_msg  = random.choice(TRAVEL_MSGS).format(dungeon=dname)
        diff_info   = DIFFICULTY_MULT.get(difficulty, {})

        e = discord.Embed(
            title=f"{diff_info.get('label','⚔️')} — {dname}",
            description=f"{travel_msg}\n\n⏳ Travelling… **{travel_secs}s**",
            color=diff_info.get("color",C_INFO),
        )
        await interaction.edit_original_response(embed=e, view=None)
        await asyncio.sleep(travel_secs)

        # Encounter delay
        enc_msg = random.choice(ENCOUNTER_MSGS)
        e2 = discord.Embed(
            title=f"{diff_info.get('label','⚔️')} — {dname}",
            description=f"{enc_msg}\n\n🎲 Encountering monster…",
            color=diff_info.get("color",C_INFO),
        )
        await interaction.edit_original_response(embed=e2)
        await asyncio.sleep(random.randint(2,5))

        # Fetch monster + build battle
        monster = await _get_monster(tier, difficulty)
        skills  = await get_skills(gid, uid)
        eq      = await get_equipment(gid, uid)

        # Build fake ctx
        ctx_fake = type("FC",(),{
            "author":interaction.user,"guild":interaction.guild,
            "channel":interaction.channel,"bot":interaction.client
        })()
        battle_view = BattleView(ctx_fake, char, skills, monster, eq, difficulty, tier)
        f  = await battle_view._render()
        e3 = battle_view._embed()
        e3.description = f"⚔️ **{monster['name']}** appears! — {diff_info.get('label','Normal')}"
        await interaction.edit_original_response(embed=e3, attachments=[f], view=battle_view)

    @commands.hybrid_command(name="usecd", description="Use a Dungeon Cooldown Remover charge")
    async def usecd(self, ctx):
        charges = await get_cd_remover_charges(ctx.guild.id, ctx.author.id)
        if charges <= 0:
            return await ctx.send(embed=discord.Embed(
                description="❌ No Cooldown Remover charges. They drop rarely from dungeons!",
                color=C_WARN,
            ), delete_after=10)
        ok = await use_cd_remover(ctx.guild.id, ctx.author.id)
        if ok:
            new_charges = await get_cd_remover_charges(ctx.guild.id, ctx.author.id)
            await ctx.send(embed=discord.Embed(
                description=f"⏱️ Cooldown removed! You can explore again now.\n**Charges remaining: {new_charges}/{MAX_CD_USES}**",
                color=C_SUCCESS,
            ), delete_after=10)
        else:
            await ctx.send("❌ Failed.", delete_after=5)

    @commands.hybrid_command(name="cdcharges", description="Check your Dungeon Cooldown Remover charges")
    async def cdcharges(self, ctx):
        charges = await get_cd_remover_charges(ctx.guild.id, ctx.author.id)
        await ctx.send(embed=discord.Embed(
            description=(
                f"⏱️ **{ctx.author.display_name}** has **{charges}/{MAX_CD_USES}** Cooldown Remover charge(s).\n"
                "Earn them as rare drops from dungeons (luck accessories improve drop rate).\n"
                "Use `/usecd` to skip your dungeon cooldown."
            ),
            color=C_INFO,
        ), delete_after=20)
