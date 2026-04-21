# airi/rpg/dungeon_final.py — Clean dungeon system (replaces dungeon.py + dungeon_v2.py)
# Turn-based combat with PIL battle image updated each turn
# Passive defense auto-activates, smart auto-battle, difficulty selector

import discord
from discord.ext import commands
import random, asyncio, aiohttp
from datetime import datetime, timezone, timedelta
import db
from utils import _err, C_INFO, C_WARN, C_SUCCESS, C_ERROR
from .engine import CombatUnit, BattleEngine, ai_choose_action
from .char   import (get_char, get_skills, get_equipment, add_char_xp,
                      calc_hp, calc_mana, get_dungeon_tier, DIFFICULTIES, DUNGEON_TIERS)
from .classes import CLASSES, get_realm
from .skills  import SKILL_DB
from .battle_image import generate_battle_card

DND_API  = "https://www.dnd5eapi.co/api"
BASE_CD  = 300    # 5 min cooldown
MAX_CD_USES = 10

GRADES       = ["F","E","D","C","B","A","S","SS","SSS"]
LOOT_WEIGHTS = {
    "normal":    [350,230,150,100,70,50,30,10,3],
    "nightmare": [150,180,160,130,110,100,70,50,25],
    "hell":      [40,80,130,160,160,160,130,90,60],
}
GRADE_REWARDS = {
    "F":{"coins":(5,25),    "kak":0,  "gems":0},
    "E":{"coins":(20,70),   "kak":1,  "gems":0},
    "D":{"coins":(70,180),  "kak":2,  "gems":0},
    "C":{"coins":(180,400), "kak":5,  "gems":0},
    "B":{"coins":(400,800), "kak":10, "gems":1},
    "A":{"coins":(800,1800),"kak":20, "gems":1},
    "S":{"coins":(1800,3500),"kak":50,"gems":2},
    "SS":{"coins":(3500,7000),"kak":100,"gems":3},
    "SSS":{"coins":(7000,18000),"kak":250,"gems":6},
}

TRAVEL_LINES = [
    "🚶 You venture into **{d}**…","⚔️ You step through the gate of **{d}**…",
    "🌑 Shadows swallow you as you enter **{d}**…","🔥 Heat radiates from **{d}**…",
    "💀 The air turns cold at the entrance of **{d}**…",
]
ENCOUNTER_LINES = [
    "👁️ Something moves in the darkness…","⚠️ You hear growling ahead…",
    "💥 A monster leaps from the shadows!","🐾 Footsteps — you're not alone.",
    "🌀 An enemy materialises before you!",
]
DUNGEON_NAMES = {
    1:["Goblin Cave","Ruined Temple","Bandit Hideout"],
    2:["Orc Stronghold","Dark Forest","Cursed Mines"],
    3:["Dragon's Lair","Undead Keep","Shadow Realm"],
    4:["Abyss Gate","Titan's Tomb","Void Sanctum"],
    5:["God's Abandoned Realm","Nightmare Domain","Hell's Gateway"],
}

# ── Monster caches ─────────────────────────────────────────────────
_monster_list: list[dict] = []
_monster_cache: dict[str,dict] = {}

async def _get_json(url):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url,timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status==200: return await r.json()
    except: pass
    return None

async def _load_monster_list():
    global _monster_list
    if _monster_list: return _monster_list
    d = await _get_json(f"{DND_API}/monsters")
    _monster_list = d.get("results",[]) if d else []
    return _monster_list

async def _load_monster(slug):
    if slug in _monster_cache: return _monster_cache[slug]
    d = await _get_json(f"{DND_API}/monsters/{slug}")
    if d: _monster_cache[slug] = d
    return d

def _parse_monster(data, tier, difficulty):
    cap  = DUNGEON_TIERS[tier]["stat_cap"]
    mult = DIFFICULTIES.get(difficulty,{}).get("stat",1.0)
    cr   = float(data.get("challenge_rating",1) or 1)
    ac   = (data.get("armor_class") or [{"value":12}])[0].get("value",12)
    hp   = data.get("hit_points",30)
    actions = [a.get("name","Attack") for a in data.get("actions",[])[:3]]

    raw = {
        "str": max(5,(data.get("strength",10)-10)*2+8),
        "def": max(2,ac//2),
        "agi": max(3,(data.get("dexterity",10)-10)+8),
        "spi": max(3,(data.get("wisdom",10)-10)+5),
        "hp":  hp,
    }
    s = {k: min(int(v*mult), cap) for k,v in raw.items() if k!="hp"}
    s["hp"] = min(int(hp*mult), cap*50)
    is_boss = cr >= 5
    xp      = int(cr*100*mult)
    char_xp = int(xp*0.5)
    kak     = max(1, xp//25) * (3 if is_boss else 1)

    return {
        "name":data.get("name","Unknown"),
        "type":f"{data.get('size','Medium')} {data.get('type','Monster')}",
        "hp":s["hp"],"hp_max":s["hp"],"mp":0,"mp_max":0,
        "str":s["str"],"def":s["def"],"agi":s["agi"],"spi":s.get("spi",5),
        "reaction":s["agi"],
        "dmg_reduction":min(0.4,ac/50),
        "xp":xp,"char_xp":char_xp,"kakera":kak,
        "gem_drop":is_boss or random.random()<0.10,
        "is_boss":is_boss,
        "image_url":data.get("image"),
        "skills":[{"name":a,"rank":"C","on_cd":False,"mana":0} for a in actions],
        "weapon":actions[0] if actions else "Claws",
        "armor":f"AC {ac}",
        "color":(200,50,50) if is_boss else (100,120,160),
        "coins":(s["str"]*3, s["str"]*8),
    }

def _fallback_monster(tier, difficulty):
    POOL = {
        1:[("Crawler",12,4,14,3,40),("Goblin",8,3,16,4,25)],
        2:[("Corruptor",18,12,8,8,70),("Sprint Predator",16,5,24,3,55)],
        3:[("Nurturer",28,15,12,10,200),("Dark Knight",32,22,10,5,180)],
        4:[("Curse Master",45,25,18,22,400),("Ferocious Ape",70,30,25,10,600)],
        5:[("Nightmare King",100,40,60,30,1000),("Fallen God",120,50,80,40,1500)],
    }
    base_name,str_,def_,agi,spi,hp = random.choice(POOL.get(tier,POOL[1]))
    mult = DIFFICULTIES.get(difficulty,{}).get("stat",1.0)
    cap  = DUNGEON_TIERS[tier]["stat_cap"]
    def sc(v): return min(int(v*mult),cap)
    xp = int(str_*10*mult); char_xp = int(xp*0.5); kak = max(1,xp//25)
    is_boss = tier >= 3
    return {
        "name":base_name,"type":"Monster" if not is_boss else "Boss",
        "hp":min(int(hp*mult),cap*50),"hp_max":min(int(hp*mult),cap*50),"mp":0,"mp_max":0,
        "str":sc(str_),"def":sc(def_),"agi":sc(agi),"spi":sc(spi),"reaction":sc(agi),
        "dmg_reduction":0.05+tier*0.02,
        "xp":xp,"char_xp":char_xp,"kakera":kak*(3 if is_boss else 1),
        "gem_drop":is_boss,"is_boss":is_boss,"image_url":None,
        "skills":[{"name":"Bite","rank":"F","on_cd":False,"mana":0}],
        "weapon":"Claws","armor":"Natural Armor","color":(200,50,50) if is_boss else (100,120,160),
        "coins":(sc(str_)*3,sc(str_)*8),
    }

async def _get_monster(tier,difficulty):
    ml = await _load_monster_list()
    if ml:
        slug = random.choice(ml)["index"]
        data = await _load_monster(slug)
        if data: return _parse_monster(data,tier,difficulty)
    return _fallback_monster(tier,difficulty)

def _roll_loot(difficulty, luck=0.0):
    w = list(LOOT_WEIGHTS.get(difficulty, LOOT_WEIGHTS["normal"]))
    shift = int(luck*40)
    for i in range(3): w[i]=max(1,w[i]-shift); w[-1-i]+=shift
    return random.choices(GRADES,weights=w,k=1)[0]

async def _luck_bonus(gid,uid):
    rows = await db.pool.fetch("SELECT effect_key,effect_value FROM rpg_equipment WHERE guild_id=$1 AND user_id=$2",gid,uid)
    return min(1.0, sum(float(r.get("effect_value",0) or 0) for r in rows if "luck" in str(r.get("effect_key","")).lower()))


# ── Build CombatUnit from char dict ────────────────────────────────
def _build_player_unit(char, difficulty="normal") -> CombatUnit:
    cls   = CLASSES.get(char["class"],{})
    clvl  = char.get("char_level",char.get("realm_level",1))
    con   = char.get("constitution",10); vit=char.get("vitality",10); spi=char.get("spirit",10)
    hp    = calc_hp(con,vit,clvl); mn = calc_mana(spi,clvl)
    return CombatUnit(
        name=f"{char.get('char_class',char['class'])} Player",
        hp=min(char.get("hp_current",hp),hp), hp_max=hp,
        mana=min(char.get("mana_current",mn),mn), mana_max=mn,
        strength=char.get("strength",10),
        constitution=con,
        agility=char.get("agility",10),
        spirit=spi,
        reaction=spi,
        crit_chance=cls.get("base",{}).get("crit_chance",0.08),
        crit_damage=1.5,
        damage_reduction=cls.get("base",{}).get("damage_reduction",0.05),
        reflect_pct=0.10 if char["class"]=="Knight" else 0.0,
        grade="Normal", is_player=True,
        first_hit_active=char["class"]=="Gunman",
        first_hit_bonus=0.5 if char["class"]=="Gunman" else 0.0,
    )

def _build_monster_unit(monster) -> CombatUnit:
    return CombatUnit(
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

# ── Skill list for image ────────────────────────────────────────────
def _skill_list_for_image(skills_db, engine: BattleEngine):
    from .skills import SKILL_DB
    out = []
    for s in skills_db[:3]:
        name = s["skill_name"]
        info = SKILL_DB.get(name,{})
        out.append({
            "name":name,"rank":s.get("skill_rank","F"),
            "on_cd":engine.player.cooldowns.get(name,0)>0,
            "mana":info.get("mana",s.get("mana_cost",10)),
        })
    return out

def _effects_list(unit: CombatUnit):
    tags={"venom":"☠️Venom","burn":"🔥Burn","bleed":"🩸Bleed","stun":"⚡Stun",
          "ground_bind":"🌿Bind","regen":"💚Regen","str_boost":"💪STR+","shield":"🛡️Shield"}
    return [f"{tags.get(e.etype,e.etype)}({e.duration}T)" for e in unit.effects[:3]]


# ── Battle View ─────────────────────────────────────────────────────
class BattleView(discord.ui.View):
    def __init__(self, ctx, char, skills_db, monster, equipment, difficulty, tier):
        super().__init__(timeout=300)
        self._ctx=ctx; self._char=char; self._skills_db=skills_db
        self._monster=monster; self._eq=equipment
        self._difficulty=difficulty; self._tier=tier
        self._gid=ctx.guild.id; self._uid=ctx.author.id
        self._running=True; self._log=[]; self._rewards={}
        self._turn=0
        # Build engine
        pc = _build_player_unit(char, difficulty)
        mc = _build_monster_unit(monster)
        self._engine = BattleEngine(pc, mc)
        # Passive defense auto-activate
        passive_logs = self._engine.apply_passive_defense(skills_db)
        self._log.extend(passive_logs)
        # Who goes first (higher AGI)
        self._player_first = pc.agility >= mc.agility
        self._cls_rgb = self._get_rgb(char)
        self._mon_col = monster.get("color",(200,50,50))

    def _get_rgb(self, char):
        c = CLASSES.get(char["class"],{}).get("color",0x4444ff)
        return ((c>>16)&0xff,(c>>8)&0xff,c&0xff)

    def _embed(self) -> discord.Embed:
        p=self._engine.player; m=self._engine.monster
        diff=DIFFICULTIES.get(self._difficulty,{}); label=diff.get("label","⚔️")
        turn_who = "player" if self._running else "none"
        e=discord.Embed(
            title=f"{label}  ·  {p.name} vs {m.name}",
            color=diff.get("color",0xe67e22),
        )
        e.set_image(url="attachment://battle.png")
        if self._log:
            e.description = f"`{self._log[-1]}`" if self._log else ""
        if not self._running:
            rw=self._rewards
            if not m.alive:
                g=rw.get("grade","F")
                e.description=(f"✅ **VICTORY!**  [{g} Grade Loot]\n"
                                f"+{rw.get('char_xp',0)} XP  ·  +{rw.get('coins',0):,} 🪙  ·  +{rw.get('kakera',0)} 💎"
                                +(f"  ·  +{rw.get('gems',0)} gems" if rw.get('gems') else "")
                                +(f"\n✨ **Level Up!** → Lv.{rw.get('new_level','?')}" if rw.get("leveled_up") else "")
                                +(f"\n🎁 Found **Cooldown Remover** charge!" if rw.get("cd_item") else ""))
            else:
                e.description="💀 **DEFEATED.** Respawned at 1 HP."
        return e

    async def _render(self):
        p=self._engine.player; m=self._engine.monster
        eq_map={e["slot"]:e for e in self._eq}
        sl=_skill_list_for_image(self._skills_db,self._engine)
        turn_who = "player" if self._running and self._engine.player.alive else "none"
        buf=await generate_battle_card(
            player_name=self._ctx.author.display_name,
            player_class=self._char["class"],
            player_hp=max(0,p.hp), player_hp_max=p.hp_max,
            player_mp=max(0,p.mana), player_mp_max=p.mana_max,
            player_str=p.strength, player_def=p.constitution, player_agi=p.agility,
            player_skills=sl,
            player_weapon=eq_map.get("weapon",{}).get("item_name","Unarmed"),
            player_armor=eq_map.get("armor",{}).get("item_name","None"),
            player_avatar_url=str(self._ctx.author.display_avatar.url),
            player_class_color=self._cls_rgb,
            monster_name=m.name,
            monster_type=self._monster.get("type","Monster"),
            monster_hp=max(0,m.hp), monster_hp_max=m.hp_max,
            monster_str=m.strength, monster_def=m.constitution, monster_agi=m.agility,
            monster_skills=self._monster.get("skills",[])[:3],
            monster_weapon=self._monster.get("weapon","Claws"),
            monster_armor=self._monster.get("armor","Hide"),
            monster_image_url=self._monster.get("image_url"),
            monster_color=self._mon_col,
            effects_player=_effects_list(p),
            effects_monster=_effects_list(m),
            combat_log=self._log[-2:],
            turn_owner=turn_who,
            sleeping=m.sleeping,
            turn_number=self._turn,
        )
        return discord.File(buf, filename="battle.png")

    def _upd_btns(self):
        ok = self._running and self._engine.player.alive
        for c in self.children: c.disabled = not ok

    async def _process(self, inter, action, skill_name="", skill_mult=1.0, is_magic=False, skill_effect=None):
        self._turn += 1
        result = self._engine.process_player_action(action, skill_name, skill_mult, is_magic, skill_effect or {})
        for ln in result.get("log",[]): self._log.append(ln)

        if result.get("fled"):
            self._running=False
            for c in self.children: c.disabled=True
            f=await self._render()
            e=self._embed(); e.description="🏃 You fled the dungeon!"
            await inter.edit_original_response(embed=e,attachments=[f],view=self)
            return

        if not result.get("monster_alive",True):
            self._running=False
            await self._end(inter, victory=True)
            return
        if not result.get("player_alive",True):
            self._running=False
            await self._end(inter, victory=False)
            return

        self._upd_btns()
        f=await self._render()
        await inter.edit_original_response(embed=self._embed(),attachments=[f],view=self)

    async def _end(self, inter, victory):
        for c in self.children: c.disabled=True
        gid,uid=self._gid,self._uid
        if victory:
            luck  = await _luck_bonus(gid,uid)
            grade = _roll_loot(self._difficulty,luck)
            rwd   = GRADE_REWARDS[grade]
            coins = random.randint(*rwd["coins"])
            kak   = int(self._monster.get("kakera",1)*DIFFICULTIES.get(self._difficulty,{}).get("loot",1))
            gems  = rwd["gems"] + (1 if self._monster.get("gem_drop") else 0)
            char_xp=int(self._monster.get("char_xp",50)*DIFFICULTIES.get(self._difficulty,{}).get("xp",1))
            # CD remover rare drop
            luck_cd = 0.02 + luck*0.03 + (0.06 if self._monster.get("is_boss") else 0)
            cd_item = random.random() < luck_cd
            # Grants
            from airi.economy import add_coins as _ac
            await _ac(gid,uid,coins)
            from airi.kakera import add_kakera; await add_kakera(gid,uid,kak)
            if gems: await db.pool.execute("UPDATE economy SET gems=gems+$1 WHERE guild_id=$2 AND user_id=$3",gems,gid,uid)
            await db.pool.execute("""INSERT INTO xp (guild_id,user_id,xp) VALUES ($1,$2,$3)
                ON CONFLICT (guild_id,user_id) DO UPDATE SET xp=xp.xp+$3""",gid,uid,int(self._monster.get("xp",50)))
            char_result=await add_char_xp(gid,uid,char_xp)
            if cd_item:
                await db.pool.execute("""UPDATE rpg_characters SET cd_remover_charges=LEAST(cd_remover_charges+1,$1)
                    WHERE guild_id=$2 AND user_id=$3""",MAX_CD_USES,gid,uid)
            await db.pool.execute("""UPDATE rpg_characters SET hp_current=hp_max,mana_current=mana_max
                WHERE guild_id=$1 AND user_id=$2""",gid,uid)
            self._rewards={"grade":grade,"coins":coins,"kakera":kak,"gems":gems,
                           "char_xp":char_xp,"cd_item":cd_item,**char_result}
        else:
            from airi.economy import add_coins as _ac,get_balance
            bal=await get_balance(gid,uid); loss=min(500,bal//10)
            if loss: await _ac(gid,uid,-loss)
            await db.pool.execute("UPDATE rpg_characters SET hp_current=1 WHERE guild_id=$1 AND user_id=$2",gid,uid)
            self._rewards={}

        await db.pool.execute("""INSERT INTO work_log (guild_id,user_id,last_explore) VALUES ($1,$2,NOW())
            ON CONFLICT (guild_id,user_id) DO UPDATE SET last_explore=NOW()""",gid,uid)
        f=await self._render()
        await inter.edit_original_response(embed=self._embed(),attachments=[f],view=self)
        self.stop()

    @discord.ui.button(label="⚔️ Attack",  style=discord.ButtonStyle.danger,    row=0)
    async def atk(self,inter,btn):
        if inter.user.id!=self._uid: return await inter.response.send_message("Not your battle.",ephemeral=True)
        await inter.response.defer()
        await self._process(inter,"attack")

    @discord.ui.button(label="✨ Skill",   style=discord.ButtonStyle.primary,   row=0)
    async def skl(self,inter,btn):
        if inter.user.id!=self._uid: return await inter.response.send_message("Not your battle.",ephemeral=True)
        avail=[]
        for s in self._skills_db:
            info=SKILL_DB.get(s["skill_name"],{})
            if (self._engine.player.cooldowns.get(s["skill_name"],0)<=0
                    and self._engine.player.mana>=info.get("mana",10)
                    and info.get("type") not in ("passive",)):
                avail.append(s)
        if not avail:
            return await inter.response.send_message("No skills available (check mana/cooldowns).",ephemeral=True)
        opts=[discord.SelectOption(
            label=f"{s['skill_name']} [{s.get('skill_rank','F')}]",
            value=s["skill_name"],
            description=f"Mana:{SKILL_DB.get(s['skill_name'],{}).get('mana',10)} · {SKILL_DB.get(s['skill_name'],{}).get('type','?')}",
        ) for s in avail[:25]]
        sel=discord.ui.Select(placeholder="Choose a skill…",options=opts)
        async def sel_cb(i2):
            if i2.user.id!=self._uid: return await i2.response.send_message("Not for you.",ephemeral=True)
            await i2.response.defer()
            sname=sel.values[0]; info=SKILL_DB.get(sname,{})
            mana=info.get("mana",10); mult=info.get("multiplier",1.0)
            is_magic=info.get("scaling")=="spirit"
            eff=info.get("effect") or {}
            stype=info.get("type","attack")
            self._engine.player.mana-=mana
            self._engine.player.cooldowns[sname]=3
            if stype in ("heal","shield","abjuration","buff","str_boost","regen"):
                await self._process(i2,"skill",sname,0,False,{**eff,"type":stype})
            else:
                await self._process(i2,"skill",sname,mult,is_magic,eff if eff else None)
        sel.callback=sel_cb
        sv=discord.ui.View(timeout=30); sv.add_item(sel)
        await inter.response.send_message("Choose a skill:",view=sv,ephemeral=True)

    @discord.ui.button(label="🏃 Flee",    style=discord.ButtonStyle.secondary, row=0)
    async def flee(self,inter,btn):
        if inter.user.id!=self._uid: return await inter.response.send_message("Not your battle.",ephemeral=True)
        await inter.response.defer()
        await self._process(inter,"flee")

    @discord.ui.button(label="⚡ Auto",    style=discord.ButtonStyle.secondary, row=1)
    async def auto_btn(self,inter,btn):
        if inter.user.id!=self._uid: return await inter.response.send_message("Not your battle.",ephemeral=True)
        await inter.response.defer()
        # Run 1 auto turn using smart AI
        chosen=ai_choose_action(self._engine.player,self._engine.monster,self._skills_db)
        action=chosen["action"]; sname=chosen.get("skill_name","")
        mana_cost=chosen.get("mana_cost",0)
        if mana_cost>0:
            self._engine.player.mana-=mana_cost
            if sname: self._engine.player.cooldowns[sname]=3
        await self._process(inter,action,sname,chosen.get("skill_mult",1.0),
                            chosen.get("is_magic",False),chosen.get("skill_effect",{}))


# ── Difficulty Picker ───────────────────────────────────────────────
class DifficultyView(discord.ui.View):
    def __init__(self,ctx,char_level,on_pick,tier):
        super().__init__(timeout=60)
        self._ctx=ctx; self._cb=on_pick; self._clvl=char_level; self._tier=tier
    def _embed(self):
        dname=random.choice(DUNGEON_NAMES.get(self._tier,DUNGEON_NAMES[1]))
        e=discord.Embed(title=f"⚔️ Enter: {dname}",
                        description=f"Choose difficulty. Higher = tougher + better rewards.\n**Your level:** {self._clvl}",color=C_INFO)
        for d,info in DIFFICULTIES.items():
            locked=self._clvl<info["min_level"]
            e.add_field(name=f"{info['label']}"+(  " 🔒" if locked else ""),
                        value=(f"×{info['stat']:.0f} stats · ×{info['xp']:.0f} XP · ×{info['loot']:.0f} loot\n"
                               +("*Unlocks at lvl "+str(info['min_level'])+"*" if locked else "**Available**")),
                        inline=False)
        e.add_field(name="⚡ Auto-Battle",value="Skip manual — AI fights for you (uses your skills smartly)",inline=False)
        e.set_footer(text="60 seconds to choose")
        return e
    @discord.ui.button(label="⚔️ Normal",    style=discord.ButtonStyle.success,   row=0)
    async def n(self,i,b):
        if i.user.id!=self._ctx.author.id: return await i.response.send_message("Not for you.",ephemeral=True)
        await self._cb("normal",i); self.stop()
    @discord.ui.button(label="💀 Nightmare", style=discord.ButtonStyle.danger,    row=0)
    async def nm(self,i,b):
        if i.user.id!=self._ctx.author.id: return await i.response.send_message("Not for you.",ephemeral=True)
        if self._clvl<10: return await i.response.send_message("❌ Nightmare needs level 10.",ephemeral=True)
        await self._cb("nightmare",i); self.stop()
    @discord.ui.button(label="🔥 Hell",      style=discord.ButtonStyle.danger,    row=0)
    async def he(self,i,b):
        if i.user.id!=self._ctx.author.id: return await i.response.send_message("Not for you.",ephemeral=True)
        if self._clvl<20: return await i.response.send_message("❌ Hell needs level 20.",ephemeral=True)
        await self._cb("hell",i); self.stop()
    @discord.ui.button(label="⚡ Auto-Battle",style=discord.ButtonStyle.secondary, row=1)
    async def ab(self,i,b):
        if i.user.id!=self._ctx.author.id: return await i.response.send_message("Not for you.",ephemeral=True)
        await self._cb("auto",i); self.stop()


# ── Auto-battle (full simulation) ──────────────────────────────────
async def _run_auto_battle(ctx, char, skills_db, monster, difficulty, tier) -> dict:
    pc=_build_player_unit(char,difficulty)
    mc=_build_monster_unit(monster)
    engine=BattleEngine(pc,mc)
    engine.apply_passive_defense(skills_db)
    turns=0
    while pc.alive and mc.alive and turns<40:
        chosen=ai_choose_action(pc,mc,skills_db)
        action=chosen["action"]; sname=chosen.get("skill_name","")
        mana_cost=chosen.get("mana_cost",0)
        if mana_cost>0:
            pc.mana-=mana_cost
            if sname: pc.cooldowns[sname]=3
        engine.process_player_action(action,sname,chosen.get("skill_mult",1.0),
                                     chosen.get("is_magic",False),chosen.get("skill_effect",{}))
        turns+=1
    return {"victory":not mc.alive,"turns":turns,"hp_left":pc.hp,"hp_max":pc.hp_max}


# ── Cog ─────────────────────────────────────────────────────────────
class DungeonCog(commands.Cog, name="Dungeon"):
    def __init__(self,bot): self.bot=bot

    @commands.hybrid_command(name="dungeon",aliases=["explore","hunt","d"],
                             description="Enter a dungeon to fight monsters")
    async def dungeon(self,ctx):
        gid,uid=ctx.guild.id,ctx.author.id
        char=await get_char(gid,uid)
        if not char:
            return await ctx.send(embed=discord.Embed(description="No character! Use `/rpg` first.",color=C_WARN))
        char_level=char.get("char_level",char.get("realm_level",1))
        tier=get_dungeon_tier(char_level)
        # Cooldown check
        cd_row=await db.pool.fetchrow("SELECT last_explore FROM work_log WHERE guild_id=$1 AND user_id=$2",gid,uid)
        if cd_row and cd_row.get("last_explore"):
            last=cd_row["last_explore"]
            if not hasattr(last,"tzinfo") or last.tzinfo is None:
                from datetime import timezone as tz; last=last.replace(tzinfo=tz.utc)
            from datetime import timezone as tz
            elapsed=(datetime.now(tz.utc)-last).total_seconds()
            if elapsed<BASE_CD:
                rem=int(BASE_CD-elapsed)
                charges=await db.pool.fetchval("SELECT cd_remover_charges FROM rpg_characters WHERE guild_id=$1 AND user_id=$2",gid,uid) or 0
                e=discord.Embed(description=(f"⏱️ Recovering. Ready in **{rem//60}m {rem%60}s**.\n"
                    +(f"⏩ You have **{charges}/{MAX_CD_USES}** CD remover charges → `/usecd` to skip!" if charges else "")),color=C_WARN)
                return await ctx.send(embed=e,delete_after=15)
        dv=DifficultyView(ctx,char_level,self._on_pick,tier)
        dv._char=char; dv._tier=tier
        msg=await ctx.send(embed=dv._embed(),view=dv)

    async def _on_pick(self,difficulty,interaction):
        await interaction.response.defer()
        gid,uid=interaction.guild_id,interaction.user.id
        char=await get_char(gid,uid)
        tier=get_dungeon_tier(char.get("char_level",char.get("realm_level",1)))

        if difficulty=="auto":
            monster=await _get_monster(tier,"normal")
            skills_db=await get_skills(gid,uid)
            eq=await get_equipment(gid,uid)
            # Travel message
            dname=random.choice(DUNGEON_NAMES.get(tier,DUNGEON_NAMES[1]))
            e=discord.Embed(title="⚡ Auto-Battle",
                description=f"Simulating fight against **{monster['name']}** in **{dname}**…",color=C_INFO)
            await interaction.edit_original_response(embed=e,view=None)
            await asyncio.sleep(2)
            result=await _run_auto_battle(interaction,char,skills_db,monster,"normal",tier)
            if result["victory"]:
                luck=await _luck_bonus(gid,uid)
                grade=_roll_loot("normal",luck); rwd=GRADE_REWARDS[grade]
                coins=random.randint(*rwd["coins"]); kak=monster.get("kakera",1)
                char_xp=int(monster.get("char_xp",50))
                from airi.economy import add_coins as _ac; await _ac(gid,uid,coins)
                from airi.kakera import add_kakera; await add_kakera(gid,uid,kak)
                char_result=await add_char_xp(gid,uid,char_xp)
                await db.pool.execute("""INSERT INTO xp (guild_id,user_id,xp) VALUES ($1,$2,$3)
                    ON CONFLICT (guild_id,user_id) DO UPDATE SET xp=xp.xp+$3""",gid,uid,monster.get("xp",50))
                await db.pool.execute("""INSERT INTO work_log (guild_id,user_id,last_explore) VALUES ($1,$2,NOW())
                    ON CONFLICT (guild_id,user_id) DO UPDATE SET last_explore=NOW()""",gid,uid)
                e=discord.Embed(title=f"⚡ Auto-Battle: Victory!",
                    description=(f"Defeated **{monster['name']}** in {result['turns']} turns!\n\n"
                        f"**[{grade} Grade]**  ·  +{coins:,} 🪙  ·  +{kak} 💎  ·  +{char_xp} XP"
                        +(f"\n✨ **Level Up!** → Lv.{char_result.get('new_level')}" if char_result.get("leveled_up") else "")),
                    color=C_SUCCESS)
            else:
                e=discord.Embed(title="⚡ Auto-Battle: Defeated",
                    description=f"**{monster['name']}** was too strong. Train more!",color=C_ERROR)
            await interaction.edit_original_response(embed=e)
            return

        # Manual battle with travel delay
        diff_info=DIFFICULTIES.get(difficulty,{})
        dname=random.choice(DUNGEON_NAMES.get(tier,DUNGEON_NAMES[1]))
        travel_secs=random.randint(8,20)
        e=discord.Embed(title=f"{diff_info.get('label','⚔️')} — {dname}",
            description=f"{random.choice(TRAVEL_LINES).format(d=dname)}\n\n⏳ **{travel_secs}s** travel time…",
            color=diff_info.get("color",C_INFO))
        await interaction.edit_original_response(embed=e,view=None)
        await asyncio.sleep(travel_secs)

        e2=discord.Embed(title=f"{diff_info.get('label','⚔️')} — {dname}",
            description=random.choice(ENCOUNTER_LINES)+"\n🎲 Spawning monster…",color=diff_info.get("color",C_INFO))
        await interaction.edit_original_response(embed=e2)
        await asyncio.sleep(random.randint(2,4))

        monster=await _get_monster(tier,difficulty)
        skills_db=await get_skills(gid,uid); eq=await get_equipment(gid,uid)

        class FC:
            author=interaction.user; guild=interaction.guild; channel=interaction.channel; bot=interaction.client
        bv=BattleView(FC,char,skills_db,monster,eq,difficulty,tier)
        f=await bv._render()
        e3=bv._embed()
        e3.description=f"⚔️ **{monster['name']}** appears! — {diff_info.get('label','Normal')}"
        await interaction.edit_original_response(embed=e3,attachments=[f],view=bv)

    @commands.hybrid_command(name="usecd",description="Use a Dungeon Cooldown Remover charge")
    async def usecd(self,ctx):
        gid,uid=ctx.guild.id,ctx.author.id
        charges=await db.pool.fetchval("SELECT cd_remover_charges FROM rpg_characters WHERE guild_id=$1 AND user_id=$2",gid,uid) or 0
        if charges<=0:
            return await ctx.send(embed=discord.Embed(description="❌ No CD Remover charges. Earn them as rare drops from dungeons!",color=C_WARN),delete_after=10)
        await db.pool.execute("UPDATE rpg_characters SET cd_remover_charges=cd_remover_charges-1 WHERE guild_id=$1 AND user_id=$2",gid,uid)
        await db.pool.execute("UPDATE work_log SET last_explore=NULL WHERE guild_id=$1 AND user_id=$2",gid,uid)
        new=charges-1
        await ctx.send(embed=discord.Embed(description=f"⏩ Cooldown cleared! You can explore again.\n**Charges: {new}/{MAX_CD_USES}**",color=C_SUCCESS),delete_after=10)
