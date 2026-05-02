# airi/rpg/dungeon_final.py — Progressive dungeon with floor scaling, boss, loot drops
# Floor 1 = weakest mob → floor N = boss at tier cap
# Live timer on travel/encounter delays
# Auto-battle = real-time per-hit updates

import discord
from discord.ext import commands
import random, asyncio, aiohttp
from datetime import datetime, timezone, timedelta
import db
from utils import _err, C_INFO, C_WARN, C_SUCCESS, C_ERROR
from .engine import CombatUnit, BattleEngine, ai_choose_action
from .char   import (get_char, get_skills, get_equipment, add_char_xp, add_pending_xp,
                     calc_hp, calc_mana, get_dungeon_tier, DIFFICULTIES, DUNGEON_TIERS)
from .classes import CLASSES
from .skills  import SKILL_DB
from .battle_image import generate_battle_card

DND_API     = "https://www.dnd5eapi.co/api"
BASE_CD     = 60      # 1 minute cooldown
MAX_CD_USES = 10

GRADES = ["F","E","D","C","B","A","S","SS","SSS"]
LOOT_W = {
    "normal":    [350,230,150,100,70,50,30,10,3],
    "nightmare": [150,180,160,130,110,100,70,50,25],
    "hell":      [40,80,130,160,160,160,130,90,60],
}
GRADE_REWARDS = {
    "F":  {"coins":(5,25),      "kak":0,   "gems":0},
    "E":  {"coins":(20,70),     "kak":1,   "gems":0},
    "D":  {"coins":(70,180),    "kak":2,   "gems":0},
    "C":  {"coins":(180,400),   "kak":5,   "gems":0},
    "B":  {"coins":(400,800),   "kak":10,  "gems":1},
    "A":  {"coins":(800,1800),  "kak":20,  "gems":1},
    "S":  {"coins":(1800,3500), "kak":50,  "gems":2},
    "SS": {"coins":(3500,7000), "kak":100, "gems":3},
    "SSS":{"coins":(7000,18000),"kak":250, "gems":6},
}

# Minimum HP floors so DnD API can't give us a 36hp goblin as boss
# Manhwa calibrated HP floors
# Lv16 Goblin King = 3000 HP → Tier II boss min ~3000
# Lv14 goblin = 1000 GP → Tier II mob min ~800
TIER_MIN_BOSS_HP = {1:150, 2:3000,  3:15000, 4:50000, 5:200000}
TIER_MIN_MOB_HP  = {1:15,  2:400,   3:2000,  4:8000,  5:30000}

# Monster item drops by tier
MONSTER_DROPS = {
    1:[("hp_potion_s","Small HP Potion"),("antidote","Antidote")],
    2:[("hp_potion_m","Medium HP Potion"),("mana_potion","Mana Potion")],
    3:[("hp_potion_l","Large HP Potion"),("elixir","Elixir of Strength")],
    4:[("revival_orb","Revival Orb"),("luck_charm","Lucky Charm")],
    5:[("revival_orb","Revival Orb"),("shadow_cloak","Shadow Cloak")],
}
BOSS_CHEST = {
    1:[("iron_shield","Iron Shield"),("speed_boots","Boots of Swiftness"),("luck_charm","Lucky Charm")],
    2:[("speed_boots","Boots of Swiftness"),("mage_robe","Arcane Robe"),("shadow_cloak","Shadow Cloak")],
    3:[("shadow_cloak","Shadow Cloak"),("revival_orb","Revival Orb"),("mage_robe","Arcane Robe")],
    4:[("shadow_cloak","Shadow Cloak"),("luck_charm","Lucky Charm"),("speed_boots","Boots of Swiftness")],
    5:[("shadow_cloak","Shadow Cloak"),("mage_robe","Arcane Robe"),("revival_orb","Revival Orb")],
}

# Dungeon layout: how many floors, boss title
TIER_LAYOUT = {
    1:{"floors":4, "boss_title":"Dungeon Guardian"},
    2:{"floors":5, "boss_title":"Warlord"},
    3:{"floors":6, "boss_title":"Ancient Dragon"},
    4:{"floors":7, "boss_title":"Void Titan"},
    5:{"floors":8, "boss_title":"Fallen God"},
}

DUNGEON_NAMES = {
    1:["Goblin Cave","Ruined Temple","Bandit Hideout"],
    2:["Orc Stronghold","Dark Forest","Cursed Mines"],
    3:["Dragon's Lair","Undead Keep","Shadow Realm"],
    4:["Abyss Gate","Titan's Tomb","Void Sanctum"],
    5:["God's Realm","Nightmare Domain","Hell's Gateway"],
}

TRAVEL_LINES = [
    "You venture into **{d}**…",
    "You step through the gate of **{d}**…",
    "Shadows swallow you as you enter **{d}**…",
    "Heat radiates from the entrance of **{d}**…",
    "The air turns cold at the entrance of **{d}**…",
]
ENCOUNTER_LINES = [
    "Something moves in the darkness…",
    "You hear growling ahead…",
    "A monster leaps from the shadows!",
    "Footsteps echo — you're not alone.",
    "An enemy materialises before you!",
]
BOSS_INTROS = [
    "💀 **THE BOSS AWAITS.** The ground shakes as it approaches!",
    "💀 **FLOOR GUARDIAN AWAKENED.** A massive creature blocks your path!",
    "🔥 **BOSS ENCOUNTER!** The dungeon's master finally reveals itself!",
    "⚡ **FINAL FLOOR!** The most powerful creature in this dungeon appears!",
]

# ── Monster caches ────────────────────────────────────────────────
_mlist:  list[dict] = []
_mcache: dict[str,dict] = {}

async def _json(url):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200: return await r.json()
    except: pass
    return None

async def _ml():
    global _mlist
    if _mlist: return _mlist
    d = await _json(f"{DND_API}/monsters")
    _mlist = d.get("results",[]) if d else []
    return _mlist

async def _md(slug):
    if slug in _mcache: return _mcache[slug]
    d = await _json(f"{DND_API}/monsters/{slug}")
    if d: _mcache[slug] = d
    return d

def _scale(base, floor, total, cap, dmult, is_boss):
    if is_boss:
        return min(int(cap * dmult * 2.0), cap)
    frac = 0.20 + 0.55 * (floor-1) / max(total-2, 1)
    frac = min(frac, 0.80)
    return min(int(base * frac * dmult), cap)

def _parse_mon(data, tier, diff, floor, total, is_boss):
    cap  = DUNGEON_TIERS[tier]["stat_cap"]
    mult = DIFFICULTIES.get(diff,{}).get("stat",1.0)
    cr   = float(data.get("challenge_rating",1) or 1)
    ac   = (data.get("armor_class") or [{"value":12}])[0].get("value",12)
    hp_r = data.get("hit_points",30)
    acts = [a.get("name","Attack") for a in data.get("actions",[])[:3]]
    raw  = {
        "str":max(5,(data.get("strength",10)-10)*2+8),
        "def":max(2,ac//2),
        "agi":max(3,(data.get("dexterity",10)-10)+8),
        "spi":max(3,(data.get("wisdom",10)-10)+5),
    }
    sc   = {k:_scale(v,floor,total,cap,mult,is_boss) for k,v in raw.items()}
    frac = 0.20 + 0.55*(floor-1)/max(total-2,1)
    hp   = int(hp_r*(mult*2.5 if is_boss else mult*frac))
    mhp  = (TIER_MIN_BOSS_HP if is_boss else TIER_MIN_MOB_HP).get(tier,20)
    hp   = max(mhp, min(hp, cap*50))
    xp   = int(cr*100*mult*(2.5 if is_boss else 0.5+0.4*(floor/total)))
    kak  = max(1,xp//25)*(5 if is_boss else 1)
    name = data.get("name","Unknown")
    if is_boss:
        name = "[BOSS] " + name + " — " + TIER_LAYOUT[tier]["boss_title"]
    return {
        "name":name,
        "type":"💀 BOSS" if is_boss else data.get("type","Monster"),
        "hp":hp,"hp_max":hp,"mp":0,"mp_max":0,
        "str":sc["str"],"def":sc["def"],"agi":sc["agi"],"spi":sc["spi"],
        "reaction":sc["agi"],
        "dmg_reduction":min(0.55 if is_boss else 0.30, ac/50),
        "xp":xp,"char_xp":int(xp*0.5),"kakera":kak,
        "gem_drop":is_boss or random.random()<0.12,
        "is_boss":is_boss,"image_url":data.get("image"),
        "skills":[{"name":a,"rank":"S" if is_boss else "C","on_cd":False,"mana":0} for a in acts],
        "weapon":acts[0] if acts else ("Dragon Breath" if is_boss else "Claws"),
        "armor":f"AC {ac}"+(" (Boss)" if is_boss else ""),
        "color":(220,20,20) if is_boss else (80+floor*20,70,80+floor*12),
        "floor":floor,"total_floors":total,"is_boss":is_boss,
    }

def _fallback_mon(tier, diff, floor, total, is_boss):
    # Manhwa-calibrated: Tier I floor 1 starts weak, scales to lv10 goblin-level
    # Transcript: lv14 goblin=800 STR, blood wolf=600 STR, 1000 HP
    # Tier I mobs: 15-150 STR (lv1-10 range)
    # Tier II mobs: 100-800 STR (lv11-25 range, matching transcript)
    POOLS = {
        1:[("Goblin",8,4,12,3,20),("Forest Slime",5,8,3,2,30),
           ("Kobold Grunt",10,5,14,3,18),("Bandit",12,6,10,3,35),
           ("Orc Scout",14,8,8,4,40),("Cave Spider",6,3,16,5,22),
           ("Skeleton",10,10,8,2,25),("Rabid Wolf",12,4,16,3,30)],
        2:[("Orc Warrior",80,40,50,20,400),("Dark Goblin",100,30,80,25,350),
           ("Blood Wolf",90,15,90,20,400),("Cursed Knight",120,80,40,15,600),
           ("Shadow Spawn",70,25,110,40,300),("Bone Dragon",110,60,50,10,700),
           ("Plague Zombie",60,50,30,15,800),("Troll Shaman",80,30,40,60,450)],
        3:[("Death Knight",600,400,200,100,3000),("Elder Lich",400,200,300,500,2500),
           ("Blood Dragon",800,300,500,200,4000),("Void Golem",1000,800,100,50,6000),
           ("Shadow Behemoth",700,350,400,150,3500)],
        4:[("Abyss Lord",2500,1500,800,600,15000),("Chaos Titan",4000,2000,600,400,20000),
           ("Void Ancient",3000,1200,1500,1000,18000),("Fallen Paladin",3500,2500,700,300,22000)],
        5:[("Divine Beast",10000,5000,8000,3000,60000),("Chaos Seraph",15000,8000,12000,6000,80000),
           ("World Eater",20000,10000,15000,5000,100000),("Void God",18000,9000,14000,8000,90000)],
    }
    # Manhwa-calibrated bosses
    # Tier I Goblin King (lv16): 3000 STR/HP → boss at top of tier
    # Tier II = lv20 dungeon boss, Tier III = lv30+
    BOSSES = {
        1:("Goblin King",     300,150, 80, 30, 3000),
        2:("Dungeon Warlord", 1200,600,300,120,12000),
        3:("Ancient Dragon",  5000,2500,1200,500,50000),
        4:("Void Titan",      15000,7500,3500,1500,150000),
        5:("Fallen God",      50000,25000,12000,5000,500000),
    }
    if is_boss:
        name,s,d,a,sp,hp = BOSSES.get(tier,BOSSES[1])
        name = "[BOSS] " + name + " — " + TIER_LAYOUT[tier]["boss_title"]
    else:
        name,s,d,a,sp,hp = random.choice(POOLS.get(tier,POOLS[1]))
    cap  = DUNGEON_TIERS[tier]["stat_cap"]
    mult = DIFFICULTIES.get(diff,{}).get("stat",1.0)
    frac = 0.20 + 0.55*(floor-1)/max(total-2,1)
    def sc(v): return min(int(v*(mult*2.0 if is_boss else mult*frac)), cap)
    mhp  = (TIER_MIN_BOSS_HP if is_boss else TIER_MIN_MOB_HP).get(tier,20)
    s_hp = max(mhp, min(int(hp*(mult*2.5 if is_boss else mult*frac)), cap*50))
    xp   = int(s*(20 if is_boss else 10)*mult); char_xp=int(xp*0.5)
    kak  = max(1,xp//25)*(5 if is_boss else 1)
    return {
        "name":name,"type":"💀 BOSS" if is_boss else "Monster",
        "hp":s_hp,"hp_max":s_hp,"mp":0,"mp_max":0,
        "str":sc(s),"def":sc(d),"agi":sc(a),"spi":sc(sp),"reaction":sc(a),
        "dmg_reduction":0.15 if is_boss else 0.05+tier*0.02,
        "xp":xp,"char_xp":char_xp,"kakera":kak,
        "gem_drop":is_boss,"is_boss":is_boss,"image_url":None,
        "skills":[{"name":"Devastate" if is_boss else "Bite","rank":"S" if is_boss else "F","on_cd":False,"mana":0}],
        "weapon":"Boss Slam" if is_boss else "Claws",
        "armor":"Boss Plate" if is_boss else "Natural Armor",
        "color":(220,20,20) if is_boss else (80+floor*20,70,80),
        "floor":floor,"total_floors":total,"is_boss":is_boss,
    }

async def _get_mon(tier, diff, floor, total, is_boss=False):
    ml = await _ml()
    if ml:
        pool = ml[len(ml)//2:] if is_boss and len(ml)>20 else ml
        slug = random.choice(pool)["index"]
        data = await _md(slug)
        if data: return _parse_mon(data,tier,diff,floor,total,is_boss)
    return _fallback_mon(tier,diff,floor,total,is_boss)

def _roll_loot(diff, luck=0.0, is_boss=False):
    w = list(LOOT_W.get(diff,LOOT_W["normal"]))
    if is_boss:
        for i in range(3): w[i]=max(1,w[i]-80); w[-1-i]+=80
    s = int(luck*40)
    for i in range(3): w[i]=max(1,w[i]-s); w[-1-i]+=s
    return random.choices(GRADES,weights=w,k=1)[0]

async def _luck(gid,uid):
    rows = await db.pool.fetch("SELECT effect_key,effect_value FROM rpg_equipment WHERE guild_id=$1 AND user_id=$2",gid,uid)
    return min(1.0, sum(float(r.get("effect_value",0) or 0) for r in rows if "luck" in str(r.get("effect_key","")).lower()))

async def _drop_item(gid, uid, tier, is_boss, luck):
    drops = MONSTER_DROPS.get(tier,MONSTER_DROPS[1])
    chance = (0.40+luck*0.3) if is_boss else (0.12+luck*0.12)
    if random.random() > chance: return None
    key,name = random.choice(drops)
    await db.pool.execute(
        "INSERT INTO inventory (guild_id,user_id,item_key,quantity) VALUES ($1,$2,$3,1) "
        "ON CONFLICT (guild_id,user_id,item_key) DO UPDATE SET quantity=inventory.quantity+1",
        gid,uid,key)
    return name

async def _drop_chest(gid, uid, tier):
    key,name = random.choice(BOSS_CHEST.get(tier,BOSS_CHEST[1]))
    await db.pool.execute(
        "INSERT INTO inventory (guild_id,user_id,item_key,quantity) VALUES ($1,$2,$3,1) "
        "ON CONFLICT (guild_id,user_id,item_key) DO UPDATE SET quantity=inventory.quantity+1",
        gid,uid,key)
    return name

def _build_pc(char) -> CombatUnit:
    cls  = CLASSES.get(char["class"],{})
    clvl = char.get("char_level",char.get("realm_level",1))
    con  = char.get("constitution",10); vit=char.get("vitality",10); spi=char.get("spirit",10)
    hp   = calc_hp(con,vit,clvl); mn=calc_mana(spi,clvl)
    return CombatUnit(
        name=char["class"],
        hp=min(char.get("hp_current",hp),hp), hp_max=hp,
        mana=min(char.get("mana_current",mn),mn), mana_max=mn,
        strength=char.get("strength",10), constitution=con,
        agility=char.get("agility",10), spirit=spi, reaction=spi,
        crit_chance=cls.get("base",{}).get("crit_chance",0.08),
        crit_damage=1.5,
        damage_reduction=cls.get("base",{}).get("damage_reduction",0.05),
        reflect_pct=0.10 if char["class"]=="Knight" else 0.0,
        grade="Normal", is_player=True,
        class_name=char["class"],
        first_hit_active=char["class"]=="Gunman",
        first_hit_bonus=0.5 if char["class"]=="Gunman" else 0.0,
    )

def _build_mc(m) -> CombatUnit:
    return CombatUnit(
        name=m["name"][:40], hp=m["hp"],hp_max=m["hp_max"],mana=0,mana_max=0,
        strength=m["str"],constitution=m["def"],agility=m["agi"],
        spirit=m.get("spi",5),reaction=m["reaction"],
        crit_chance=0.08 if m.get("is_boss") else 0.05,crit_damage=1.5,
        damage_reduction=m.get("dmg_reduction",0.05),reflect_pct=0.0,
        grade="Normal",is_player=False,
    )

def _eff(unit:CombatUnit):
    t={"venom":"☠️","burn":"🔥","bleed":"🩸","stun":"⚡","ground_bind":"🌿","regen":"💚","str_boost":"💪","shield":"🛡️"}
    return [t.get(e.etype,e.etype[:2])+"("+str(e.duration)+"T)" for e in unit.effects[:3]]

def _sk_img(sdb, engine:BattleEngine):
    return [{"name":s["skill_name"],"rank":s.get("skill_rank","F"),
             "on_cd":engine.player.cooldowns.get(s["skill_name"],0)>0,
             "mana":SKILL_DB.get(s["skill_name"],{}).get("mana",s.get("mana_cost",10))}
            for s in sdb[:3]]


# ─────────────────────────────────────────────────────────────────
# DungeonRun state
# ─────────────────────────────────────────────────────────────────
class DungeonRun:
    def __init__(self,char,skills_db,eq,diff,tier,dname):
        self.char=char; self.skills_db=skills_db; self.eq=eq
        self.diff=diff; self.tier=tier; self.dname=dname
        layout=TIER_LAYOUT[tier]
        self.total_floors=layout["floors"]
        self.current_floor=0
        self.xp=0; self.cxp=0; self.coins=0; self.kak=0; self.gems=0
        self.grades=[]; self.drops=[]; self.cd_item=False; self.fled=False
        self.pc=_build_pc(char)

    @property
    def is_boss(self): return self.current_floor==self.total_floors
    def pbar(self):
        done=self.current_floor-1
        cells=[]
        for i in range(1,self.total_floors+1):
            if i<self.current_floor:   cells.append("🟥")
            elif i==self.current_floor:cells.append("💀" if self.is_boss else "⚔️")
            else:                       cells.append("⬜")
        return "".join(cells)
    def floor_label(self):
        if self.is_boss: return f"💀 BOSS FLOOR {self.current_floor}/{self.total_floors}"
        return f"🗺️ Floor {self.current_floor}/{self.total_floors}"


# ─────────────────────────────────────────────────────────────────
# FloorBattleView
# ─────────────────────────────────────────────────────────────────
class FloorBattleView(discord.ui.View):
    def __init__(self,ctx,run,monster,gid,uid):
        super().__init__(timeout=300)
        self._ctx=ctx; self._run=run; self._m=monster
        self._gid=gid; self._uid=uid
        self._log=[]; self._running=True; self._turn=0
        mc=_build_mc(monster)
        self._eng=BattleEngine(run.pc,mc)
        if run.current_floor==1:
            for ln in self._eng.apply_passive_defense(run.skills_db):
                self._log.append(ln)
        cls=CLASSES.get(run.char["class"],{}); c=cls.get("color",0x4444ff)
        self._cr=((c>>16)&0xff,(c>>8)&0xff,c&0xff)
        self._mc=monster.get("color",(200,50,50))

    def _embed(self,extra=""):
        run=self._run; p=self._eng.player; m=self._eng.monster
        dc=DIFFICULTIES.get(run.diff,{}).get("color",0xe67e22)
        dl=DIFFICULTIES.get(run.diff,{}).get("label","⚔️")
        e=discord.Embed(title=dl+"  ·  "+run.dname+"  ·  "+run.floor_label(),
                        color=0xff0000 if run.is_boss else dc)
        e.set_image(url="attachment://battle.png")
        pct=p.hp/max(p.hp_max,1); bar="█"*int(pct*12)+"░"*(12-int(pct*12))
        desc=run.pbar()+"\n"
        if self._log: desc+= "`"+self._log[-1][:80]+"`\n"
        if extra: desc+="\n"+extra
        e.description=desc
        e.add_field(name="Your HP",value=f"`{bar}` {p.hp}/{p.hp_max}",inline=True)
        e.add_field(name="Mana",   value=f"{p.mana}/{p.mana_max}",inline=True)
        effs=", ".join(_eff(p)) or "—"
        e.add_field(name="Effects",value=effs,inline=True)
        return e

    async def _render(self):
        p=self._eng.player; m=self._eng.monster
        eq_map={e["slot"]:e for e in self._run.eq}
        sl=_sk_img(self._run.skills_db,self._eng)
        turn_who="player" if self._running else "none"
        n=self._ctx.author if hasattr(self._ctx,"author") else self._ctx.user
        buf=await generate_battle_card(
            player_name=n.display_name,player_class=self._run.char["class"],
            player_hp=max(0,p.hp),player_hp_max=p.hp_max,
            player_mp=max(0,p.mana),player_mp_max=p.mana_max,
            player_str=p.strength,player_def=p.constitution,player_agi=p.agility,
            player_skills=sl,
            player_weapon=eq_map.get("weapon",{}).get("item_name","Unarmed"),
            player_armor=eq_map.get("armor",{}).get("item_name","None"),
            player_avatar_url=str(n.display_avatar.url),
            player_class_color=self._cr,
            monster_name=m.name[:40],monster_type=self._m.get("type","Monster")[:40],
            monster_hp=max(0,m.hp),monster_hp_max=m.hp_max,
            monster_str=m.strength,monster_def=m.constitution,monster_agi=m.agility,
            monster_skills=self._m.get("skills",[])[:3],
            monster_weapon=self._m.get("weapon","Claws"),
            monster_armor=self._m.get("armor","Hide"),
            monster_image_url=self._m.get("image_url"),
            monster_color=self._mc,
            effects_player=_eff(p),effects_monster=_eff(m),
            combat_log=self._log[-2:],turn_owner=turn_who,sleeping=m.sleeping,
            turn_number=self._turn,
        )
        return discord.File(buf,filename="battle.png")

    def _upd(self):
        ok=self._running and self._eng.player.alive
        for c in self.children: c.disabled=not ok

    async def _proc(self,inter,action,sname="",mult=1.0,magic=False,eff=None):
        self._turn+=1
        res=self._eng.process_player_action(action,sname,mult,magic,eff or {})
        for ln in res.get("log",[]): self._log.append(ln)
        if res.get("fled"):
            self._running=False; self._run.fled=True
            for c in self.children: c.disabled=True
            f=await self._render()
            e=self._embed("🏃 You **fled** the dungeon!")
            await inter.edit_original_response(embed=e,attachments=[f],view=self)
            return
        if not res.get("monster_alive",True):
            self._running=False
            await self._win(inter); return
        if not res.get("player_alive",True):
            self._running=False
            await self._lose(inter); return
        self._upd()
        f=await self._render()
        await inter.edit_original_response(embed=self._embed(),attachments=[f],view=self)

    async def _win(self,inter):
        run=self._run; gid=self._gid; uid=self._uid
        luck=await _luck(gid,uid)
        grade=_roll_loot(run.diff,luck,is_boss=run.is_boss)
        rwd=GRADE_REWARDS[grade]
        coins=random.randint(*rwd["coins"])
        kak=int(self._m.get("kakera",1)*DIFFICULTIES.get(run.diff,{}).get("loot",1))
        gems=rwd["gems"]+(1 if self._m.get("gem_drop") else 0)
        cxp=int(self._m.get("char_xp",50)*DIFFICULTIES.get(run.diff,{}).get("xp",1))
        # Item drops
        drop=await _drop_item(gid,uid,run.tier,run.is_boss,luck)
        chest=None
        if run.is_boss:
            chest=await _drop_chest(gid,uid,run.tier)
            cc=0.05+luck*0.05
            if random.random()<cc:
                run.cd_item=True
                ch=await db.pool.fetchval("SELECT cd_remover_charges FROM rpg_characters WHERE guild_id=$1 AND user_id=$2",gid,uid) or 0
                if ch<MAX_CD_USES:
                    await db.pool.execute("UPDATE rpg_characters SET cd_remover_charges=cd_remover_charges+1 WHERE guild_id=$1 AND user_id=$2",gid,uid)
        # Accum
        run.coins+=coins; run.kak+=kak; run.gems+=gems; run.cxp+=cxp
        run.xp+=int(self._m.get("xp",50)); run.grades.append(grade)
        if drop:   run.drops.append("📦 "+drop)
        if chest:  run.drops.append("🎁 [Chest] "+chest)
        for c in self.children: c.disabled=True
        f=await self._render()
        drop_txt=""
        if drop:  drop_txt+="\n📦 **"+drop+"** dropped!"
        if chest: drop_txt+="\n🎁 **[Chest]** "+chest+"!"
        if run.is_boss:
            e=self._embed(
                "✅ **BOSS DEFEATED!** ["+grade+" Grade]\n"
                "+"+str(coins)+" 🪙  ·  +"+str(kak)+" 💎  ·  +"+str(cxp)+" XP"
                +(("  ·  +"+str(gems)+" gems") if gems else "")
                +drop_txt
                +("\n⏱️ Found **Cooldown Remover!**" if run.cd_item else "")
            )
            e.title="🏆 DUNGEON COMPLETE — "+run.dname+"!"
            e.color=0xffd700
            await inter.edit_original_response(embed=e,attachments=[f],view=self)
            await self._finalize(inter)
        else:
            nf=run.current_floor+1
            boss_next=(nf==run.total_floors)
            intro=random.choice(BOSS_INTROS) if boss_next else "✅ Floor "+str(run.current_floor)+" cleared!"
            e=self._embed(
                intro+"\n+"+str(coins)+" 🪙  ·  +"+str(kak)+" 💎  ·  +"+str(cxp)+" XP  ·  ["+grade+"]"
                +drop_txt
            )
            e.color=0xffd700 if boss_next else C_SUCCESS
            cv=ContinueView(inter,run,nf,gid,uid)
            await inter.edit_original_response(embed=e,attachments=[f],view=cv)
        self.stop()

    async def _lose(self,inter):
        for c in self.children: c.disabled=True
        from airi.economy import get_balance,add_coins as _ac
        bal=await get_balance(self._gid,self._uid)
        loss=min(500,bal//10)
        if loss: await _ac(self._gid,self._uid,-loss)
        await db.pool.execute("UPDATE rpg_characters SET hp_current=1 WHERE guild_id=$1 AND user_id=$2",self._gid,self._uid)
        await db.pool.execute("INSERT INTO work_log (guild_id,user_id,last_explore) VALUES ($1,$2,NOW()) ON CONFLICT (guild_id,user_id) DO UPDATE SET last_explore=NOW()",self._gid,self._uid)
        f=await self._render()
        e=self._embed("💀 **DEFEATED on Floor "+str(self._run.current_floor)+"!**\n-"+str(loss)+" 🪙 penalty. Respawned at 1 HP.")
        e.color=C_ERROR
        await inter.edit_original_response(embed=e,attachments=[f],view=self)
        self.stop()

    async def _finalize(self,inter):
        run=self._run; gid=self._gid; uid=self._uid
        from airi.economy import add_coins as _ac; await _ac(gid,uid,run.coins)
        from airi.kakera import add_kakera; await add_kakera(gid,uid,run.kak)
        if run.gems: await db.pool.execute("UPDATE economy SET gems=gems+$1 WHERE guild_id=$2 AND user_id=$3",run.gems,gid,uid)
        await db.pool.execute("INSERT INTO xp (guild_id,user_id,xp) VALUES ($1,$2,$3) ON CONFLICT (guild_id,user_id) DO UPDATE SET xp=xp.xp+$3",gid,uid,run.xp)
        cr=await add_char_xp(gid,uid,run.cxp)
        await db.pool.execute("UPDATE rpg_characters SET hp_current=hp_max,mana_current=mana_max WHERE guild_id=$1 AND user_id=$2",gid,uid)
        await db.pool.execute("INSERT INTO work_log (guild_id,user_id,last_explore) VALUES ($1,$2,NOW()) ON CONFLICT (guild_id,user_id) DO UPDATE SET last_explore=NOW()",gid,uid)
        gs=" ".join("["+g+"]" for g in run.grades)
        ds=("  ·  ".join(run.drops[-4:]) if run.drops else "")
        lvl_txt=("\n✨ **LEVEL UP!** → Lv."+str(cr.get("new_level"))) if cr.get("leveled_up") else ""
        summ=discord.Embed(
            title="📊 Run Summary — "+run.dname,
            description=(
                "**"+str(run.total_floors)+" floors** cleared · "+DIFFICULTIES.get(run.diff,{}).get("label","⚔️")+"\n\n"
                "💰 **+"+str(run.coins)+"** 🪙  ·  💎 **+"+str(run.kak)+"** kakera"
                +(("  ·  **+"+str(run.gems)+"** gems") if run.gems else "")
                +"\n⚔️ **+"+str(run.cxp)+"** char XP"
                +"\nLoot: "+gs
                +("\nDrops: "+ds if ds else "")
                +lvl_txt
            ),
            color=0xffd700,
        )
        try: await inter.followup.send(embed=summ)
        except: pass
        if cr.get("leveled_up"):
            try:
                u=inter.user if hasattr(inter,"user") else inter.author
                await inter.followup.send(embed=discord.Embed(
                    title="⬆️ LEVEL UP!",
                    description="**"+u.display_name+"** reached character **Level "+str(cr.get("new_level"))+"**!",
                    color=0xf1c40f))
            except: pass

    @discord.ui.button(label="⚔️ Attack", style=discord.ButtonStyle.danger,    row=0)
    async def atk(self,inter,btn):
        if inter.user.id!=self._uid: return await inter.response.send_message("Not yours.",ephemeral=True)
        await inter.response.defer()
        await self._proc(inter,"attack")

    @discord.ui.button(label="✨ Skill",  style=discord.ButtonStyle.primary,   row=0)
    async def skl(self,inter,btn):
        if inter.user.id!=self._uid: return await inter.response.send_message("Not yours.",ephemeral=True)
        avail=[s for s in self._run.skills_db
               if self._eng.player.cooldowns.get(s["skill_name"],0)<=0
               and self._eng.player.mana>=SKILL_DB.get(s["skill_name"],{}).get("mana",10)
               and SKILL_DB.get(s["skill_name"],{}).get("type") not in ("passive",)]
        if not avail:
            return await inter.response.send_message("No skills available.",ephemeral=True)
        opts=[discord.SelectOption(
            label=s["skill_name"]+" ["+s.get("skill_rank","F")+"]",
            value=s["skill_name"],
            description="Mana:"+str(SKILL_DB.get(s["skill_name"],{}).get("mana",10))+" · "+SKILL_DB.get(s["skill_name"],{}).get("type","?"),
        ) for s in avail[:25]]
        sel=discord.ui.Select(placeholder="Choose a skill…",options=opts)
        async def sel_cb(i2):
            if i2.user.id!=self._uid: return await i2.response.send_message("Not for you.",ephemeral=True)
            await i2.response.defer()
            sn=sel.values[0]; info=SKILL_DB.get(sn,{})
            mc_=info.get("mana",10); mult=info.get("multiplier",1.0)
            is_m=info.get("scaling")=="spirit"; eff=info.get("effect") or {}
            self._eng.player.mana-=mc_; self._eng.player.cooldowns[sn]=3
            st=info.get("type","attack")
            if st in ("heal","shield","abjuration","buff","str_boost","regen"):
                await self._proc(i2,"skill",sn,0,False,dict(eff,type=st))
            else:
                await self._proc(i2,"skill",sn,mult,is_m,eff if eff else None)
        sel.callback=sel_cb
        sv=discord.ui.View(timeout=30); sv.add_item(sel)
        # Edit main message to show skill selector
        for c in self.children: c.disabled=True
        e=self._embed("✨ Choose a skill:")
        await inter.response.edit_message(embed=e,view=sv)

    @discord.ui.button(label="🏃 Flee",   style=discord.ButtonStyle.secondary, row=0)
    async def flee(self,inter,btn):
        if inter.user.id!=self._uid: return await inter.response.send_message("Not yours.",ephemeral=True)
        await inter.response.defer()
        await self._proc(inter,"flee")

    @discord.ui.button(label="⚡ Auto",   style=discord.ButtonStyle.secondary, row=1)
    async def auto_btn(self,inter,btn):
        if inter.user.id!=self._uid: return await inter.response.send_message("Not yours.",ephemeral=True)
        await inter.response.defer()
        chosen=ai_choose_action(self._eng.player,self._eng.monster,self._run.skills_db)
        action=chosen["action"]; sn=chosen.get("skill_name","")
        mc_=chosen.get("mana_cost",0)
        if mc_>0: self._eng.player.mana-=mc_
        if sn and mc_>0: self._eng.player.cooldowns[sn]=3
        await self._proc(inter,action,sn,chosen.get("skill_mult",1.0),
                         chosen.get("is_magic",False),chosen.get("skill_effect",{}))

    @discord.ui.button(label="🧪 Items",  style=discord.ButtonStyle.secondary, row=1)
    async def items_btn(self,inter,btn):
        if inter.user.id!=self._uid: return await inter.response.send_message("Not yours.",ephemeral=True)
        from airi.rpg.shop import MARKET_ITEMS
        POTION_KEYS=["hp_potion_s","hp_potion_m","hp_potion_l","mana_potion","antidote","revival_orb"]
        owned=[]
        for key in POTION_KEYS:
            qty=await db.pool.fetchval("SELECT quantity FROM inventory WHERE guild_id=$1 AND user_id=$2 AND item_key=$3",self._gid,self._uid,key) or 0
            if qty>0:
                it=MARKET_ITEMS.get(key,{})
                owned.append((key,it.get("name",key),qty))
        if not owned:
            return await inter.response.send_message("🧪 No usable items in inventory. Buy potions from .",ephemeral=True)
        opts=[discord.SelectOption(label=f"{name} (x{qty})",value=key,description=MARKET_ITEMS.get(key,{}).get("effect","")[:80]) for key,name,qty in owned[:25]]
        sel=discord.ui.Select(placeholder="Use a potion…",options=opts)
        async def item_cb(i2):
            if i2.user.id!=self._uid: return await i2.response.send_message("Not for you.",ephemeral=True)
            await i2.response.defer()
            key=sel.values[0]
            ok=await db.pool.fetchval("SELECT quantity FROM inventory WHERE guild_id=$1 AND user_id=$2 AND item_key=$3",self._gid,self._uid,key) or 0
            if ok<=0:
                self._upd(); f=await self._render()
                return await i2.edit_original_response(embed=self._embed("❌ No "+key+" left!"),attachments=[f],view=self)
            await db.pool.execute("UPDATE inventory SET quantity=quantity-1 WHERE guild_id=$1 AND user_id=$2 AND item_key=$3",self._gid,self._uid,key)
            p=self._eng.player; note=""
            if key=="hp_potion_s":
                gain=max(1,int(p.hp_max*0.20)); p.hp=min(p.hp_max,p.hp+gain); note=f"🧪 Small HP Potion: +{gain} HP"
            elif key=="hp_potion_m":
                gain=max(1,int(p.hp_max*0.40)); p.hp=min(p.hp_max,p.hp+gain); note=f"🧪 Medium HP Potion: +{gain} HP"
            elif key=="hp_potion_l":
                gain=max(1,int(p.hp_max*0.70)); p.hp=min(p.hp_max,p.hp+gain); note=f"🧪 Large HP Potion: +{gain} HP"
            elif key=="mana_potion":
                gain=max(1,int(p.mana_max*0.30)); p.mana=min(p.mana_max,p.mana+gain); note=f"💙 Mana Potion: +{gain} Mana"
            elif key=="antidote":
                p.effects=[e for e in p.effects if e.etype not in ("venom","burn")]; note="🌿 Antidote: cured Venom/Burn"
            elif key=="revival_orb":
                if not any(e.etype=="revival" for e in p.effects):
                    from .engine import Effect
                    p.effects.append(Effect(etype="revival",duration=999,value=1.0,source="Revival Orb"))
                note="✨ Revival Orb: survives next lethal hit"
            self._log.append(note)
            self._upd(); f=await self._render()
            await i2.edit_original_response(embed=self._embed(note),attachments=[f],view=self)
        sel.callback=item_cb
        sv=discord.ui.View(timeout=30); sv.add_item(sel)
        for c in self.children: c.disabled=True
        await inter.response.edit_message(embed=self._embed("🧪 Choose an item:"),view=sv)


# ─────────────────────────────────────────────────────────────────
# Between-floor continue/leave view
# ─────────────────────────────────────────────────────────────────
class ContinueView(discord.ui.View):
    def __init__(self,inter,run,nf,gid,uid):
        super().__init__(timeout=120)
        self._inter=inter; self._run=run; self._nf=nf; self._gid=gid; self._uid=uid
        p=run.pc; pct=int(p.hp/max(p.hp_max,1)*100)
        boss=(nf==run.total_floors)
        self.cont_btn.label=("💀 FIGHT BOSS" if boss else "➡️ Floor "+str(nf))+" ("+str(pct)+"% HP)"
        self.cont_btn.style=discord.ButtonStyle.danger if boss else discord.ButtonStyle.success

    @discord.ui.button(label="➡️ Next Floor",style=discord.ButtonStyle.success,row=0)
    async def cont_btn(self,inter,btn):
        if inter.user.id!=self._uid: return await inter.response.send_message("Not yours.",ephemeral=True)
        await inter.response.defer()
        for c in self.children: c.disabled=True
        run=self._run; run.current_floor=self._nf
        boss=run.is_boss
        e=discord.Embed(
            title=run.dname+" · "+run.floor_label(),
            description=(random.choice(BOSS_INTROS) if boss else random.choice(ENCOUNTER_LINES))+"\n🎲 Spawning…",
            color=0xff0000 if boss else C_INFO,
        )
        await inter.edit_original_response(embed=e,view=None)
        await asyncio.sleep(1)
        m=await _get_mon(run.tier,run.diff,run.current_floor,run.total_floors,boss)
        class FC:
            author=inter.user; guild=inter.guild; channel=inter.channel; bot=inter.client; user=inter.user
        bv=FloorBattleView(FC,run,m,self._gid,self._uid)
        f=await bv._render()
        e2=bv._embed()
        e2.description=(run.pbar()+"\n"
            +("💀 **BOSS: "+m["name"][:40]+"** appears!" if boss else "⚔️ **"+m["name"][:40]+"** appears!"))
        e2.color=0xff0000 if boss else DIFFICULTIES.get(run.diff,{}).get("color",C_INFO)
        await inter.edit_original_response(embed=e2,attachments=[f],view=bv)
        self.stop()

    @discord.ui.button(label="🚪 Leave",style=discord.ButtonStyle.secondary,row=0)
    async def leave_btn(self,inter,btn):
        if inter.user.id!=self._uid: return await inter.response.send_message("Not yours.",ephemeral=True)
        await inter.response.defer()
        for c in self.children: c.disabled=True
        run=self._run; gid=self._gid; uid=self._uid
        from airi.economy import add_coins as _ac; await _ac(gid,uid,run.coins)
        from airi.kakera import add_kakera; await add_kakera(gid,uid,run.kak)
        if run.cxp: await add_char_xp(gid,uid,run.cxp)
        await db.pool.execute("INSERT INTO work_log (guild_id,user_id,last_explore) VALUES ($1,$2,NOW()) ON CONFLICT (guild_id,user_id) DO UPDATE SET last_explore=NOW()",gid,uid)
        gs=" ".join("["+g+"]" for g in run.grades) or "—"
        e=discord.Embed(
            title="🚪 Left — "+run.dname,
            description=(
                "Cleared **"+str(run.current_floor-1)+"/"+str(run.total_floors)+"** floors.\n\n"
                "💰 +"+str(run.coins)+" 🪙  ·  💎 +"+str(run.kak)+"  ·  +"+str(run.cxp)+" XP\n"
                "Loot: "+gs
            ),
            color=C_WARN,
        )
        await inter.edit_original_response(embed=e,view=self)
        self.stop()


# ─────────────────────────────────────────────────────────────────
# Live countdown helper
# ─────────────────────────────────────────────────────────────────
async def _countdown(interaction, title, desc_prefix, secs, color=C_INFO):
    """Edit the message every second with a live countdown."""
    msg = None
    for remaining in range(secs, 0, -1):
        bar = "█"*int((1-remaining/secs)*20)+"░"*int(remaining/secs*20)
        e = discord.Embed(
            title=title,
            description=desc_prefix+"\n\n`"+bar+"`  **"+str(remaining)+"s**",
            color=color,
        )
        try:
            if msg is None:
                # First call — edit the interaction response
                await interaction.edit_original_response(embed=e, view=None)
            else:
                await msg.edit(embed=e)
        except: pass
        await asyncio.sleep(1)


# ─────────────────────────────────────────────────────────────────
# Difficulty picker
# ─────────────────────────────────────────────────────────────────
class DiffView(discord.ui.View):
    def __init__(self,ctx,clvl,tier,cb):
        super().__init__(timeout=60)
        self._ctx=ctx; self._clvl=clvl; self._tier=tier; self._cb=cb
    def _embed(self,dname):
        layout=TIER_LAYOUT.get(self._tier,TIER_LAYOUT[1])
        e=discord.Embed(
            title="⚔️ Enter: "+dname,
            description=(
                "**Tier "+str(self._tier)+" Dungeon** · "+str(layout["floors"])+" floors\n"
                "Floors 1–"+str(layout["floors"]-1)+": progressively stronger monsters\n"
                "Floor "+str(layout["floors"])+": 💀 "+layout["boss_title"]+" (BOSS)\n\n"
                "Your character level: **"+str(self._clvl)+"**"
            ),
            color=C_INFO,
        )
        for d,info in DIFFICULTIES.items():
            locked=self._clvl<info["min_level"]
            e.add_field(
                name=info["label"]+(" 🔒" if locked else ""),
                value=("×"+str(info["stat"])+" stats · ×"+str(info["xp"])+" XP · ×"+str(info["loot"])+" loot\n"
                       +("Unlocks at lvl "+str(info["min_level"]) if locked else "**Available**")),
                inline=False,
            )
        e.add_field(name="⚡ Auto-Battle",value="AI fights all floors with live per-hit updates",inline=False)
        e.set_footer(text="60s to choose")
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
    @discord.ui.button(label="⚡ Auto",       style=discord.ButtonStyle.secondary, row=1)
    async def ab(self,i,b):
        if i.user.id!=self._ctx.author.id: return await i.response.send_message("Not for you.",ephemeral=True)
        await self._cb("auto",i); self.stop()


# ─────────────────────────────────────────────────────────────────
# Cog
# ─────────────────────────────────────────────────────────────────
class DungeonCog(commands.Cog, name="Dungeon"):
    def __init__(self, bot): self.bot = bot

    @commands.hybrid_command(name="dungeon", aliases=["explore","hunt","d"],
                             description="Enter a dungeon — fight floors to the boss!")
    async def dungeon(self, ctx):
        gid,uid=ctx.guild.id,ctx.author.id
        char=await get_char(gid,uid)
        if not char:
            return await ctx.send(embed=discord.Embed(description="No character! Use `/rpg` first.",color=C_WARN))
        clvl=char.get("char_level",char.get("realm_level",1))
        tier=get_dungeon_tier(clvl)
        # Cooldown check
        cd=await db.pool.fetchrow("SELECT last_explore FROM work_log WHERE guild_id=$1 AND user_id=$2",gid,uid)
        if cd and cd.get("last_explore"):
            last=cd["last_explore"]
            if not hasattr(last,"tzinfo") or last.tzinfo is None:
                from datetime import timezone as tz; last=last.replace(tzinfo=tz.utc)
            from datetime import timezone as tz
            el=(datetime.now(tz.utc)-last).total_seconds()
            if el<BASE_CD:
                rem=int(BASE_CD-el)
                charges=await db.pool.fetchval("SELECT cd_remover_charges FROM rpg_characters WHERE guild_id=$1 AND user_id=$2",gid,uid) or 0
                return await ctx.send(embed=discord.Embed(
                    description="⏱️ Recovering. Ready in **"+str(rem)+"s**.\n"
                    +(str(charges)+"/"+str(MAX_CD_USES)+" CD charges → `/usecd` to skip!" if charges else ""),
                    color=C_WARN),delete_after=15)
        dname=random.choice(DUNGEON_NAMES.get(tier,DUNGEON_NAMES[1]))
        dv=DiffView(ctx,clvl,tier,self._pick)
        dv._char=char; dv._tier=tier; dv._dname=dname
        await ctx.send(embed=dv._embed(dname),view=dv)

    async def _pick(self,diff,interaction):
        await interaction.response.defer()
        gid,uid=interaction.guild_id,interaction.user.id
        char=await get_char(gid,uid)
        tier=get_dungeon_tier(char.get("char_level",char.get("realm_level",1)))
        dname=random.choice(DUNGEON_NAMES.get(tier,DUNGEON_NAMES[1]))

        if diff=="auto":
            skills_db=await get_skills(gid,uid); eq=await get_equipment(gid,uid)
            layout=TIER_LAYOUT[tier]; total=layout["floors"]
            run=DungeonRun(char,skills_db,eq,"normal",tier,dname)
            pc=run.pc
            e=discord.Embed(title="⚡ Auto-Battle — "+dname,
                description="Starting "+str(total)+"-floor crawl… simulating in real time.",color=C_INFO)
            await interaction.edit_original_response(embed=e,view=None)
            diff_info=DIFFICULTIES["normal"]

            for floor in range(1,total+1):
                run.current_floor=floor
                boss=(floor==total)
                m=await _get_mon(tier,"normal",floor,total,boss)
                mc=_build_mc(m)
                eng=BattleEngine(pc,mc)
                if floor==1: eng.apply_passive_defense(skills_db)
                if boss:
                    e=discord.Embed(title="⚡ Auto-Battle — "+dname,
                        description=run.pbar()+"\n💀 **BOSS: "+m["name"][:40]+"** appears!",color=0xff0000)
                    await interaction.edit_original_response(embed=e)
                    await asyncio.sleep(1)
                turns=0; clog=[]
                while pc.alive and mc.alive and turns<40:
                    chosen=ai_choose_action(pc,mc,skills_db)
                    action=chosen["action"]; sn=chosen.get("skill_name","")
                    mc_=chosen.get("mana_cost",0)
                    if mc_>0: pc.mana-=mc_
                    if sn and mc_>0: pc.cooldowns[sn]=3
                    res=eng.process_player_action(action,sn,chosen.get("skill_mult",1.0),
                                                  chosen.get("is_magic",False),chosen.get("skill_effect",{}))
                    turns+=1
                    for ln in res.get("log",[]): clog.append(ln)
                    clog=clog[-4:]
                    if turns%2==0 or not mc.alive or not pc.alive:
                        hb="█"*int((pc.hp/max(pc.hp_max,1))*12)+"░"*(12-int((pc.hp/max(pc.hp_max,1))*12))
                        mb="█"*int((mc.hp/max(mc.hp_max,1))*12)+"░"*(12-int((mc.hp/max(mc.hp_max,1))*12))
                        boss_tag="💀 **BOSS** · " if boss else ""
                        lines=[
                            run.pbar(),
                            boss_tag+"Floor **"+str(floor)+"/"+str(total)+"** · Turn **"+str(turns)+"**",
                            "",
                            "**You** `"+hb+"` "+str(pc.hp)+"/"+str(pc.hp_max)+" HP",
                            "**"+mc.name[:30]+"** `"+mb+"` "+str(mc.hp)+"/"+str(mc.hp_max)+" HP",
                            "",
                        ]
                        lines+=["`"+ln[:60]+"`" for ln in clog[-3:]]
                        e=discord.Embed(
                            title="⚡ Auto-Battle — "+dname,
                            description="\n".join(lines),
                            color=0xff0000 if boss else diff_info.get("color",C_INFO),
                        )
                        try: await interaction.edit_original_response(embed=e)
                        except: pass
                        await asyncio.sleep(0.7)
                if not pc.alive:
                    from airi.economy import add_coins as _ac; await _ac(gid,uid,run.coins)
                    from airi.kakera import add_kakera; await add_kakera(gid,uid,run.kak)
                    if run.cxp: await add_char_xp(gid,uid,run.cxp)
                    await db.pool.execute("UPDATE rpg_characters SET hp_current=1 WHERE guild_id=$1 AND user_id=$2",gid,uid)
                    await db.pool.execute("INSERT INTO work_log (guild_id,user_id,last_explore) VALUES ($1,$2,NOW()) ON CONFLICT (guild_id,user_id) DO UPDATE SET last_explore=NOW()",gid,uid)
                    e=discord.Embed(title="⚡ Auto-Battle: Defeated on Floor "+str(floor),
                        description="Fell on **Floor "+str(floor)+"/"+str(total)+"**.\nPartial: +"+str(run.coins)+" 🪙  ·  +"+str(run.kak)+" 💎",
                        color=C_ERROR)
                    await interaction.edit_original_response(embed=e); return
                # Floor cleared
                luck=await _luck(gid,uid)
                grade=_roll_loot("normal",luck,boss)
                rwd=GRADE_REWARDS[grade]
                coins=random.randint(*rwd["coins"])
                kak=m.get("kakera",1); cxp=int(m.get("char_xp",50))
                run.coins+=coins; run.kak+=kak; run.cxp+=cxp
                run.xp+=int(m.get("xp",50)); run.grades.append(grade)
                drop=await _drop_item(gid,uid,tier,boss,luck)
                if boss: chest=await _drop_chest(gid,uid,tier); run.drops.append("🎁 [Chest] "+chest)
                if drop: run.drops.append("📦 "+drop)
                await asyncio.sleep(0.5)
            # All done
            from airi.economy import add_coins as _ac; await _ac(gid,uid,run.coins)
            from airi.kakera import add_kakera; await add_kakera(gid,uid,run.kak)
            if run.gems: await db.pool.execute("UPDATE economy SET gems=gems+$1 WHERE guild_id=$2 AND user_id=$3",run.gems,gid,uid)
            await db.pool.execute("INSERT INTO xp (guild_id,user_id,xp) VALUES ($1,$2,$3) ON CONFLICT (guild_id,user_id) DO UPDATE SET xp=xp.xp+$3",gid,uid,run.xp)
            cr=await add_char_xp(gid,uid,run.cxp)
            await db.pool.execute("UPDATE rpg_characters SET hp_current=hp_max,mana_current=mana_max WHERE guild_id=$1 AND user_id=$2",gid,uid)
            await db.pool.execute("INSERT INTO work_log (guild_id,user_id,last_explore) VALUES ($1,$2,NOW()) ON CONFLICT (guild_id,user_id) DO UPDATE SET last_explore=NOW()",gid,uid)
            gs=" ".join("["+g+"]" for g in run.grades)
            ds="  ·  ".join(run.drops[-4:]) if run.drops else ""
            lv=("\n✨ **LEVEL UP!** → Lv."+str(cr.get("new_level"))) if cr.get("leveled_up") else ""
            e=discord.Embed(title="🏆 Auto-Battle COMPLETE — "+dname+"!",
                description=(
                    "Cleared all **"+str(total)+" floors**!\n\n"
                    "💰 +"+str(run.coins)+" 🪙  ·  💎 +"+str(run.kak)+"  ·  +"+str(run.cxp)+" XP\n"
                    "Grades: "+gs+("\nDrops: "+ds if ds else "")+lv
                ),color=0xffd700)
            await interaction.edit_original_response(embed=e); return

        # Manual dungeon
        diff_info=DIFFICULTIES.get(diff,{})
        travel=random.randint(5,12)
        travel_desc="🚶 "+random.choice(TRAVEL_LINES).format(d=dname)
        await _countdown(interaction, diff_info.get("label","⚔️")+" — "+dname, travel_desc, travel, diff_info.get("color",C_INFO))
        enc=random.randint(2,4)
        enc_desc="⚠️ "+random.choice(ENCOUNTER_LINES)+"\n🎲 Spawning Floor 1 creature…"
        await _countdown(interaction, diff_info.get("label","⚔️")+" — "+dname, enc_desc, enc, diff_info.get("color",C_INFO))
        skills_db=await get_skills(gid,uid); eq=await get_equipment(gid,uid)
        run=DungeonRun(char,skills_db,eq,diff,tier,dname); run.current_floor=1
        m=await _get_mon(tier,diff,1,run.total_floors,False)
        class FC:
            author=interaction.user; guild=interaction.guild; channel=interaction.channel
            bot=interaction.client; user=interaction.user
        bv=FloorBattleView(FC,run,m,gid,uid)
        f=await bv._render()
        e=bv._embed()
        e.description=run.pbar()+"\n⚔️ **"+m["name"][:40]+"** appears!"
        e.color=diff_info.get("color",C_INFO)
        await interaction.edit_original_response(embed=e,attachments=[f],view=bv)

    @commands.hybrid_command(name="usecd",description="Use a Dungeon Cooldown Remover charge")
    async def usecd(self,ctx):
        gid,uid=ctx.guild.id,ctx.author.id
        ch=await db.pool.fetchval("SELECT cd_remover_charges FROM rpg_characters WHERE guild_id=$1 AND user_id=$2",gid,uid) or 0
        if ch<=0:
            return await ctx.send(embed=discord.Embed(description="❌ No CD Remover charges! Earn them as rare boss drops.",color=C_WARN),delete_after=10)
        await db.pool.execute("UPDATE rpg_characters SET cd_remover_charges=cd_remover_charges-1 WHERE guild_id=$1 AND user_id=$2",gid,uid)
        await db.pool.execute("UPDATE work_log SET last_explore=NULL WHERE guild_id=$1 AND user_id=$2",gid,uid)
        await ctx.send(embed=discord.Embed(description="⏩ Cooldown cleared! "+str(ch-1)+"/"+str(MAX_CD_USES)+" charges remaining.",color=C_SUCCESS),delete_after=10)
