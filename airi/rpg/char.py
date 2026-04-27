# airi/rpg/char.py — Unified character module (replaces stats.py + character.py)
# Single source of truth for: get_char, create_char, get_skills, get_equipment,
# XP system, VIT/HP/Mana formulas, race bonuses, class growth, character sheet embed
import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone
import db
from utils import C_INFO, C_SUCCESS, C_WARN, _err
from .classes import CLASSES, RANK_EMOJI, RANK_COLORS, get_realm, str_label

# ── XP Table ───────────────────────────────────────────────────────
XP_TABLE = [
    0,300,900,2700,6500,14000,23000,34000,48000,64000,    # 1–10
    85000,100000,120000,140000,165000,195000,225000,265000,305000,355000, # 11–20
    425000,495000,570000,650000,735000,820000,915000,1015000,1120000,1230000, # 21–30
]
MAX_CHAR_LEVEL = 100

def _xp_for_level_100(lvl: int) -> int:
    """Total XP to reach level `lvl`. Extends the 30-entry table up to 100."""
    if lvl <= 1: return 0
    idx = lvl - 1
    if idx < len(XP_TABLE): return XP_TABLE[idx]
    return int(XP_TABLE[-1] * (1.18 ** (lvl - len(XP_TABLE))))

def xp_for_level(lvl: int) -> int:
    if lvl <= 1: return 0
    return _xp_for_level_100(lvl)

def level_from_xp(xp: int) -> int:
    # Binary search for levels beyond table
    if xp <= 0: return 1
    # Check table first
    lvl = 1
    for i, t in enumerate(XP_TABLE):
        if xp >= t: lvl = i+1
    # If at max table level, check extended
    if lvl >= len(XP_TABLE):
        for test_lvl in range(len(XP_TABLE)+1, 101):
            if xp >= _xp_for_level_100(test_lvl):
                lvl = test_lvl
            else:
                break
    return min(lvl, MAX_CHAR_LEVEL)

def xp_to_next(xp: int) -> tuple[int,int]:
    lvl    = level_from_xp(xp)
    nxt    = XP_TABLE[min(lvl, MAX_CHAR_LEVEL-1)]
    needed = max(0, nxt - xp)
    return lvl, needed

# ── HP / Mana formulas ─────────────────────────────────────────────
def calc_hp(con: int, vit: int, lvl: int) -> int:
    """HP = (10 + CON×2 + VIT×10) × (1 + lvl×0.05)"""
    return max(10, int((10 + con*2 + vit*10) * (1 + lvl*0.05)))

def calc_mana(spi: int, lvl: int) -> int:
    """Mana = SPI × 3 + lvl × 5"""
    return max(10, spi*3 + lvl*5)

# ── Dungeon tiers / difficulties ───────────────────────────────────
# Manhwa calibrated:
# Lv1-10 dungeon (Tier I): newbie mobs have 15 stats (class-transfer skeleton warrior)
#   → cap at 150 for normal Lv10 monsters (goblin guard "compared to lv7 skeleton")
# Lv11-25 dungeon (Tier II): lv14 goblin=800 STR, blood wolf=600 STR
#   → cap at 1200 so boss gets 3000 (1200×2.5 boss mult matches Goblin King)
# Lv26-50 dungeon (Tier III): elite territory, several-times stronger
#   → cap at 5000
# Lv51-75 (Tier IV): Late Stage, dangerous territory
#   → cap at 15000
# Lv76-100 (Tier V): Peak/Transcendent, divine-level territory
#   → cap at 50000
DUNGEON_TIERS = {
    1:{"name":"Tier I",  "min_level":1,  "stat_cap":150},
    2:{"name":"Tier II", "min_level":10, "stat_cap":1200},
    3:{"name":"Tier III","min_level":20, "stat_cap":5000},
    4:{"name":"Tier IV", "min_level":30, "stat_cap":15000},
    5:{"name":"Tier V",  "min_level":40, "stat_cap":50000},
}
DIFFICULTIES = {
    "normal":    {"stat":1.0,"xp":1.0,"loot":1.0,"label":"⚔️ Normal",   "color":0x27ae60,"min_level":1},
    "nightmare": {"stat":3.0,"xp":3.0,"loot":3.0,"label":"💀 Nightmare","color":0xe74c3c,"min_level":10},
    "hell":      {"stat":5.0,"xp":5.0,"loot":5.0,"label":"🔥 Hell",     "color":0xff0000,"min_level":20},
}

def get_dungeon_tier(char_level: int) -> int:
    t = 1
    for tier, info in DUNGEON_TIERS.items():
        if char_level >= info["min_level"]: t = tier
    return t

# ── Race bonuses ───────────────────────────────────────────────────
RACE_BONUSES = {
    "Human":    {"str":1,"agi":1,"spi":1,"con":1,"vit":1},
    "Elf":      {"agi":2,"spi":1},
    "Dwarf":    {"con":2,"vit":2},
    "Halfling": {"agi":2},
    "Dragonborn":{"str":2,"con":1},
    "Gnome":    {"spi":2},
    "Half-Elf": {"agi":1,"spi":1,"con":1},
    "Half-Orc": {"str":2,"vit":1},
    "Tiefling": {"spi":2,"agi":1},
}
DND_RACES = list(RACE_BONUSES.keys())

# ── Class stat growth per level ────────────────────────────────────
# Manhwa: each level grants 10 stat points (20 after lv11)
# Distributed by class specialty
# Total per level: ~10 early (splits across stats), ~20 later
CLASS_GROWTH = {
    "Shadow":     {"str":2,"agi":5,"spi":1,"con":2,"vit":1},   # 11/level, AGI primary
    "Warrior":    {"str":5,"agi":1,"spi":0,"con":4,"vit":4},   # 14/level, STR+CON
    "Mage":       {"str":0,"agi":1,"spi":7,"con":1,"vit":2},   # 11/level, SPI primary
    "Necromancer":{"str":1,"agi":1,"spi":6,"con":1,"vit":2},   # 11/level, SPI focused
    "Archer":     {"str":3,"agi":5,"spi":1,"con":1,"vit":2},   # 12/level, AGI+STR
    "Gunman":     {"str":3,"agi":4,"spi":1,"con":2,"vit":2},   # 12/level, AGI+STR
    "Knight":     {"str":2,"agi":0,"spi":1,"con":5,"vit":5},   # 13/level, CON+VIT
    "Healer":     {"str":0,"agi":1,"spi":5,"con":2,"vit":3},   # 11/level, SPI+VIT
}

def _get_level_growth_mult(char_level: int) -> float:
    """Manhwa: stat gain doubles after level 11."""
    return 2.0 if char_level > 10 else 1.0

# ── DB helpers ─────────────────────────────────────────────────────
async def get_char(gid, uid) -> dict | None:
    r = await db.pool.fetchrow("SELECT * FROM rpg_characters WHERE guild_id=$1 AND user_id=$2", gid, uid)
    if not r: return None
    d = dict(r)
    # Back-compat: map old 'defence' → 'constitution'
    if "constitution" not in d:
        d["constitution"] = d.get("defence", 10)
    # Fill new columns with defaults if missing
    d.setdefault("vitality",    10)
    d.setdefault("char_level",  d.get("realm_level",1))
    d.setdefault("char_xp",     0)
    d.setdefault("race",        "Human")
    d.setdefault("cd_remover_charges", 0)
    return d

async def create_char(gid, uid, class_name: str, race: str = "Human") -> dict:
    cls  = CLASSES[class_name]
    base = cls["base"]
    bon  = RACE_BONUSES.get(race, {})
    vit  = 10
    con  = base["con"] + bon.get("con",0)
    spi  = base["spi"] + bon.get("spi",0)
    hp   = calc_hp(con, vit, 1)
    mn   = calc_mana(spi, 1)

    row  = await db.pool.fetchrow("""
        INSERT INTO rpg_characters
            (guild_id,user_id,class,realm_level,char_level,char_xp,race,
             strength,constitution,agility,spirit,vitality,
             hp_max,hp_current,mana_max,mana_current,stat_points,talent,cd_remover_charges)
        VALUES ($1,$2,$3,1,1,0,$4,$5,$6,$7,$8,10,$9,$9,$10,$10,5,$11,0)
        ON CONFLICT (guild_id,user_id) DO NOTHING RETURNING *
    """, gid, uid, class_name, race,
        base["str"]+bon.get("str",0),
        con,
        base["agi"]+bon.get("agi",0),
        spi,
        hp, mn, cls["talent_name"])
    for s, r in cls["starting_skills"]:
        await db.pool.execute("""
            INSERT INTO rpg_skills (guild_id,user_id,skill_name,skill_rank)
            VALUES ($1,$2,$3,$4) ON CONFLICT DO NOTHING
        """, gid, uid, s, r)
    return await get_char(gid, uid)

async def get_skills(gid, uid) -> list[dict]:
    rows = await db.pool.fetch("SELECT * FROM rpg_skills WHERE guild_id=$1 AND user_id=$2", gid, uid)
    return [dict(r) for r in rows]

async def get_equipment(gid, uid) -> list[dict]:
    rows = await db.pool.fetch("SELECT * FROM rpg_equipment WHERE guild_id=$1 AND user_id=$2", gid, uid)
    return [dict(r) for r in rows]

async def add_char_xp(gid, uid, xp_gain: int) -> dict:
    """Add XP, apply level-up if threshold reached. Returns result dict."""
    row = await db.pool.fetchrow(
        "SELECT char_level,char_xp,class,spirit,constitution,vitality FROM rpg_characters WHERE guild_id=$1 AND user_id=$2",
        gid, uid
    )
    if not row: return {"leveled_up":False,"new_level":1}

    old_lvl = row.get("char_level") or row.get("realm_level",1)
    old_xp  = int(row.get("char_xp") or 0)
    new_xp  = old_xp + xp_gain
    new_lvl = level_from_xp(new_xp)
    leveled = new_lvl > old_lvl

    sql    = "UPDATE rpg_characters SET char_xp=$1,char_level=$2"
    params = [new_xp, new_lvl]
    i      = 3

    if leveled:
        grw = CLASS_GROWTH.get(row.get("class","Warrior"), CLASS_GROWTH["Warrior"])
        levels_gained = new_lvl - old_lvl
        sa = grw["str"]*levels_gained; ca = grw["con"]*levels_gained
        aa = grw["agi"]*levels_gained; spa = grw["spi"]*levels_gained
        va = grw["vit"]*levels_gained
        sql += f",strength=strength+${i},constitution=constitution+${i+1},agility=agility+${i+2},spirit=spirit+${i+3},vitality=vitality+${i+4}"
        params += [sa,ca,aa,spa,va]; i += 5
        # Recalc HP/Mana
        new_con = int(row["constitution"]+ca); new_vit = int(row["vitality"]+va)
        new_spi = int(row["spirit"]+spa)
        new_hp  = calc_hp(new_con, new_vit, new_lvl)
        new_mn  = calc_mana(new_spi, new_lvl)
        sql += f",hp_max=${i},hp_current=${i},mana_max=${i+1},mana_current=${i+1},stat_points=stat_points+2"
        params += [new_hp,new_mn]; i += 2

    sql += f" WHERE guild_id=${i} AND user_id=${i+1}"
    params += [gid, uid]
    await db.pool.execute(sql, *params)
    return {"leveled_up":leveled,"new_level":new_lvl,"old_level":old_lvl,"total_xp":new_xp}

# ── Visual helpers ─────────────────────────────────────────────────
def _bar(cur, mx, n=12):
    f = max(0, int((cur/max(mx,1))*n))
    return "█"*f+"░"*(n-f)

def char_embed(char: dict, member: discord.Member) -> discord.Embed:
    """Manhwa-style character sheet."""
    cls     = CLASSES.get(char["class"],{})
    race    = char.get("race","Human")
    clvl    = char.get("char_level", char.get("realm_level",1))
    cxp     = char.get("char_xp",0)
    realm_n, realm_e = get_realm(clvl)
    _, xp_need = xp_to_next(cxp)
    xp_this  = cxp - xp_for_level(clvl)
    xp_pct   = int(100*xp_this/(xp_need+xp_this+1)) if xp_need+xp_this > 0 else 100

    str_ = char.get("strength",10); agi = char.get("agility",10)
    spi  = char.get("spirit",10);   con = char.get("constitution",10)
    vit  = char.get("vitality",10)
    hp_c = char.get("hp_current",100); hp_m = char.get("hp_max",100)
    mn_c = char.get("mana_current",50); mn_m = char.get("mana_max",50)

    e = discord.Embed(color=cls.get("color",0x5d6bb5))
    e.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    e.add_field(name="\u200b", value=(
        f"```\n"
        f"[NAME: {member.display_name.upper()[:18]}]\n"
        f"[OCCUPATION: {char['class'].upper()} [{cls.get('talent_rank','?')}]]\n"
        f"[RACE: {race.upper()}]\n"
        f"[REALM: {realm_e} {realm_n.upper()}]\n"
        f"[LEVEL: {clvl} ({xp_pct:.1f}%)]\n"
        f"```"
    ), inline=False)
    e.add_field(name="⚔️ Stats", value=(
        f"```\n"
        f"[STR: {str_:>4}  [{str_label(str_)}]]\n"
        f"[AGI: {agi:>4}]\n"
        f"[SPI: {spi:>4}]\n"
        f"[CON: {con:>4}]\n"
        f"[VIT: {vit:>4}]\n"
        f"```"
    ), inline=True)
    e.add_field(name="💫 Vitals", value=(
        f"```\n"
        f"[HP:   {hp_c}/{hp_m}]\n"
        f"{_bar(hp_c,hp_m)} ❤️\n"
        f"[MANA: {mn_c}/{mn_m}]\n"
        f"{_bar(mn_c,mn_m)} 💙\n"
        f"[EXP:  {xp_this}/{xp_this+xp_need}]\n"
        f"```"
    ), inline=True)
    sp = char.get("stat_points",0)
    if sp > 0:
        e.add_field(name="✨ Free Points", value=f"**{sp}** pts — `/rpg allocate`!", inline=False)
    e.set_footer(text=f"Talent: {char.get('talent','?')} · /rpg skills · /rpg equip")
    return e

# ── Equipment embed ─────────────────────────────────────────────────
def equip_embed(char: dict, equipment: list[dict], member: discord.Member) -> discord.Embed:
    cls   = CLASSES.get(char["class"],{})
    eq_map= {e["slot"]:e for e in equipment}
    SLOTS = [("weapon","⚔️ Weapon"),("armor","🛡️ Armor"),("ring","💍 Ring"),("accessory","🔮 Accessory")]
    e = discord.Embed(title=f"🎒 Equipment — {member.display_name}", color=cls.get("color",0x5d6bb5))
    e.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    for sk, sl in SLOTS:
        it = eq_map.get(sk)
        if it:
            r = it.get("item_rank","F")
            e.add_field(name=sl, value=f"{RANK_EMOJI.get(r,'⬜')} **{it['item_name']}** [{r}]\n_{it.get('effect_desc','')}_", inline=True)
        else:
            e.add_field(name=sl, value="_Empty_", inline=True)
    return e

def skills_embed(char: dict, skills: list[dict], member: discord.Member) -> discord.Embed:
    cls = CLASSES.get(char["class"],{})
    e   = discord.Embed(title=f"📚 Skill Book — {member.display_name}", color=cls.get("color",0x5d6bb5))
    e.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    if not skills:
        e.description = "No skills yet. Buy from `/rpgshop` or earn from dungeons!"
    else:
        for s in skills[:15]:
            r = s.get("skill_rank","F")
            e.add_field(name=f"{RANK_EMOJI.get(r,'⬜')} {s['skill_name']} [{r}]",
                        value=f"Mana: {s.get('mana_cost',10)}", inline=True)
    return e

# ── Views ─────────────────────────────────────────────────────────
class RPGPanel(discord.ui.View):
    def __init__(self, char, member, skills, equipment, viewer_id):
        super().__init__(timeout=300)
        self._c=char; self._m=member; self._sk=skills; self._eq=equipment; self._v=viewer_id

    @discord.ui.button(label="📋 Stats",   style=discord.ButtonStyle.primary,   row=0)
    async def s1(self,i,b): await i.response.edit_message(embed=char_embed(self._c,self._m),view=self)

    @discord.ui.button(label="📚 Skills",  style=discord.ButtonStyle.secondary, row=0)
    async def s2(self,i,b):
        e=skills_embed(self._c,self._sk,self._m)
        await i.response.edit_message(embed=e,view=_Back(self,char_embed(self._c,self._m)))

    @discord.ui.button(label="🎒 Equip",   style=discord.ButtonStyle.secondary, row=0)
    async def s3(self,i,b):
        e=equip_embed(self._c,self._eq,self._m)
        await i.response.edit_message(embed=e,view=_Back(self,char_embed(self._c,self._m)))

    @discord.ui.button(label="📊 Allocate",style=discord.ButtonStyle.success,   row=1)
    async def s4(self,i,b):
        if i.user.id != self._m.id: return await i.response.send_message("Only your character.",ephemeral=True)
        if not self._c.get("stat_points"): return await i.response.send_message("No free points.",ephemeral=True)
        v = AllocView(self._c, self._m, i.guild_id, parent=self)
        await i.response.edit_message(embed=v._embed(), view=v)

    @discord.ui.button(label="⚔️ Dungeon",  style=discord.ButtonStyle.danger,    row=1)
    async def dungeon_btn(self,i,b):
        # Trigger the dungeon command in the same channel
        await i.response.defer()
        cog = i.client.cogs.get("Dungeon")
        if not cog:
            return await i.edit_original_response(
                embed=discord.Embed(description="Dungeon module unavailable.", color=0xe74c3c), view=self)
        class FC:
            guild=i.guild; author=i.user; channel=i.channel; bot=i.client
            async def send(self_,*a,**kw):
                kw.pop("delete_after",None)
                return await i.channel.send(*a,**kw)
        await cog.dungeon(FC())

    @discord.ui.button(label="🛒 Shop",     style=discord.ButtonStyle.secondary, row=1)
    async def shop_btn(self,i,b):
        await i.response.defer()
        from airi.rpg.shop import ShopView
        player_class = self._c.get("class")
        shop_view = ShopView(
            type("FCtx",(),{"author":i.user,"guild":i.guild,"channel":i.channel,"bot":i.client})(),
            player_class
        )
        load_e = discord.Embed(title="🔄 Loading Shop...", description="Fetching from D&D 5e API...", color=0x3498db)
        # Send as new message so dungeon panel stays intact
        msg = await i.channel.send(embed=load_e)
        await shop_view._load()
        shop_view._rebuild()
        await msg.edit(embed=shop_view._embed(), view=shop_view)

    @discord.ui.button(label="⚔️ Guild",    style=discord.ButtonStyle.secondary, row=2)
    async def guild_btn(self,i,b):
        await i.response.defer()
        cog = i.client.cogs.get("GuildSystem")
        if not cog:
            return await i.edit_original_response(
                embed=discord.Embed(description="Guild module unavailable.", color=0xe74c3c), view=self)
        class FC:
            guild=i.guild; author=i.user; channel=i.channel; bot=i.client
            async def send(self_,*a,**kw):
                kw.pop("delete_after",None)
                return await i.channel.send(*a,**kw)
        await cog.guild_cmd(FC())

class _Back(discord.ui.View):
    def __init__(self,parent,home): super().__init__(timeout=300); self._p=parent; self._h=home
    @discord.ui.button(label="◀ Back",style=discord.ButtonStyle.secondary)
    async def back(self,i,b): await i.response.edit_message(embed=self._h,view=self._p)

class AllocView(discord.ui.View):
    def __init__(self,char,member,gid,parent):
        super().__init__(timeout=120)
        self._c=dict(char); self._m=member; self._gid=gid; self._uid=member.id
        self._p=parent; self._pend={"strength":0,"constitution":0,"agility":0,"spirit":0,"vitality":0}
        self._pts=char.get("stat_points",0); self._upd()
    def _left(self): return self._pts-sum(self._pend.values())
    def _upd(self): self.confirm_btn.disabled=sum(self._pend.values())==0
    def _embed(self):
        c=self._c
        e=discord.Embed(title="📊 Allocate Stat Points",
                        description=f"**Remaining:** {self._left()} / {self._pts}\nVIT increases max HP. SPI increases Mana.",
                        color=0x5d6bb5)
        for k,l in [("strength","STR"),("constitution","CON"),("agility","AGI"),("spirit","SPI"),("vitality","VIT")]:
            cur=c.get(k,0); add=self._pend[k]
            e.add_field(name=l,value=f"{cur}"+(f" → **{cur+add}** (+{add})" if add else ""),inline=True)
        return e
    async def _add(self,i,key):
        if i.user.id!=self._uid: return await i.response.send_message("Not for you.",ephemeral=True)
        if self._left()<=0: return await i.response.send_message("No points left!",ephemeral=True)
        self._pend[key]+=1; self._upd()
        await i.response.edit_message(embed=self._embed(),view=self)
    @discord.ui.button(label="+STR",style=discord.ButtonStyle.primary,row=0)
    async def b1(self,i,b): await self._add(i,"strength")
    @discord.ui.button(label="+CON",style=discord.ButtonStyle.primary,row=0)
    async def b2(self,i,b): await self._add(i,"constitution")
    @discord.ui.button(label="+AGI",style=discord.ButtonStyle.primary,row=0)
    async def b3(self,i,b): await self._add(i,"agility")
    @discord.ui.button(label="+SPI",style=discord.ButtonStyle.primary,row=0)
    async def b4(self,i,b): await self._add(i,"spirit")
    @discord.ui.button(label="+VIT",style=discord.ButtonStyle.primary,row=0)
    async def b5(self,i,b): await self._add(i,"vitality")
    @discord.ui.button(label="↺ Reset",style=discord.ButtonStyle.secondary,row=1)
    async def reset_btn(self,i,b):
        if i.user.id!=self._uid: return await i.response.send_message("Not for you.",ephemeral=True)
        self._pend={k:0 for k in self._pend}; self._upd()
        await i.response.edit_message(embed=self._embed(),view=self)
    @discord.ui.button(label="◀ Back",style=discord.ButtonStyle.secondary,row=1)
    async def back_btn(self,i,b):
        if i.user.id!=self._uid: return await i.response.send_message("Not for you.",ephemeral=True)
        await i.response.edit_message(embed=char_embed(self._c,self._m),view=self._p)
    @discord.ui.button(label="✅ Confirm",style=discord.ButtonStyle.success,disabled=True,row=1)
    async def confirm_btn(self,i,b):
        if i.user.id!=self._uid: return await i.response.send_message("Not for you.",ephemeral=True)
        used=sum(self._pend.values())
        if not used: return
        for c in self.children: c.disabled=True
        await i.response.defer()
        await db.pool.execute("""
            UPDATE rpg_characters
            SET strength=strength+$1,constitution=constitution+$2,agility=agility+$3,
                spirit=spirit+$4,vitality=vitality+$5,stat_points=stat_points-$6
            WHERE guild_id=$7 AND user_id=$8
        """,self._pend["strength"],self._pend["constitution"],self._pend["agility"],
            self._pend["spirit"],self._pend["vitality"],used,self._gid,self._uid)
        # Reload fresh char after update
        char=await get_char(self._gid,self._uid)
        sk=await get_skills(self._gid,self._uid); eq=await get_equipment(self._gid,self._uid)
        nv=RPGPanel(char,self._m,sk,eq,self._uid)
        # Show updated embed — HP/Mana recalculated in char_embed from live DB
        e=char_embed(char,self._m)
        e.title="✅ Stats Updated!"
        e.color=0x2ecc71
        await i.edit_original_response(embed=e,view=nv)
        self.stop()

class ClassSelectView(discord.ui.View):
    def __init__(self,uid,gid):
        super().__init__(timeout=300)
        self._uid=uid; self._gid=gid
        self._classes=list(CLASSES.items()); self._page=0
        self._upd_lbl()
    def _upd_lbl(self): self.confirm_btn.label=f"✅ Play as {self._classes[self._page][0]}"
    def _embed(self):
        name,cls=self._classes[self._page]; base=cls["base"]
        e=discord.Embed(title=f"{cls['emoji']} Class: {name}",description=cls["desc"],color=cls["color"])
        e.add_field(name="📊 Base Stats",value=(
            f"STR:**{base['str']}** CON:**{base['con']}**\n"
            f"AGI:**{base['agi']}** SPI:**{base['spi']}**\n"
            f"HP:**{base['hp']}** Mana:**{base['mana']}**"),inline=True)
        e.add_field(name=f"✨ {cls['talent_name']} [{cls['talent_rank']}]",value=cls["passive"][:120],inline=False)
        e.add_field(name="📚 Starting Skills",value="\n".join(f"{RANK_EMOJI.get(r,'⬜')} {s} [{r}]" for s,r in cls["starting_skills"]),inline=False)
        e.set_footer(text=f"Class {self._page+1}/{len(self._classes)} · ◀▶ browse · ✅ confirm")
        return e
    @discord.ui.button(label="◀",style=discord.ButtonStyle.secondary,row=0)
    async def prev(self,i,b):
        if i.user.id!=self._uid: return await i.response.send_message("Not for you.",ephemeral=True)
        self._page=(self._page-1)%len(self._classes); self._upd_lbl()
        await i.response.edit_message(embed=self._embed(),view=self)
    @discord.ui.button(label="✅ Play as ...",style=discord.ButtonStyle.success,row=0)
    async def confirm_btn(self,i,b):
        if i.user.id!=self._uid: return await i.response.send_message("Not for you.",ephemeral=True)
        cls_name=self._classes[self._page][0]
        race_view=RaceSelectView(self._uid, self._gid, cls_name)
        for c in self.children: c.disabled=True
        await i.response.edit_message(embed=race_view._embed(),view=race_view)
        self.stop()
    @discord.ui.button(label="▶",style=discord.ButtonStyle.secondary,row=0)
    async def next_btn(self,i,b):
        if i.user.id!=self._uid: return await i.response.send_message("Not for you.",ephemeral=True)
        self._page=(self._page+1)%len(self._classes); self._upd_lbl()
        await i.response.edit_message(embed=self._embed(),view=self)

class RaceSelectView(discord.ui.View):
    def __init__(self,uid,gid,cls_name):
        super().__init__(timeout=180)
        self._uid=uid; self._gid=gid; self._cls=cls_name
        opts=[discord.SelectOption(label=race,value=race,description=self._desc(race)) for race in DND_RACES]
        sel=discord.ui.Select(placeholder="Choose your race…",options=opts); sel.callback=self._pick
        self.add_item(sel)
    def _desc(self,race):
        b=RACE_BONUSES.get(race,{})
        return ", ".join(f"+{v} {k.upper()}" for k,v in b.items()) or "Balanced"
    def _embed(self):
        e=discord.Embed(title="🧬 Choose Your Race",
                        description="Your race gives permanent stat bonuses.\nPick one to create your character!",
                        color=0x5d6bb5)
        for race in DND_RACES:
            e.add_field(name=race,value=self._desc(race),inline=True)
        return e
    async def _pick(self,i:discord.Interaction):
        if i.user.id!=self._uid: return await i.response.send_message("Not for you.",ephemeral=True)
        race=i.data["values"][0]
        for c in self.children: c.disabled=True
        await i.response.defer()
        char=await create_char(self._gid,self._uid,self._cls,race)
        sk=await get_skills(self._gid,self._uid); eq=await get_equipment(self._gid,self._uid)
        cls=CLASSES[self._cls]
        e=char_embed(char,i.user); e.title=f"✨ Character Created! {cls['emoji']} {self._cls}"
        e.description=(f"Welcome, **{i.user.display_name}**!\n"
                       f"You are now a **{cls['emoji']} {self._cls}** of the **{race}** race.\n"
                       f"Talent: **{cls['talent_name']}**\nYou have **5 free stat points** — use `/rpg allocate`!")
        await i.edit_original_response(embed=e,view=RPGPanel(char,i.user,sk,eq,self._uid))
        self.stop()

# ── Cog ────────────────────────────────────────────────────────────
class RPGStatsCog(commands.Cog, name="RPG"):
    def __init__(self,bot):
        self.bot=bot
        self.hp_regen_task.start()

    def cog_unload(self):
        self.hp_regen_task.cancel()

    @tasks.loop(minutes=2)
    async def hp_regen_task(self):
        """Regen HP and mana out of dungeon every 2 minutes."""
        await self.bot.wait_until_ready()
        try:
            # Regen 5% HP and 10% Mana for all characters not currently in a dungeon
            # (those in dungeon have last_explore within the last 5 min)
            await db.pool.execute("""
                UPDATE rpg_characters SET
                    hp_current   = LEAST(hp_max,   hp_current   + GREATEST(1, hp_max   / 20)),
                    mana_current = LEAST(mana_max, mana_current + GREATEST(2, mana_max / 10))
                WHERE (guild_id, user_id) NOT IN (
                    SELECT guild_id, user_id FROM work_log
                    WHERE last_explore > NOW() - INTERVAL '5 minutes'
                )
            """)
        except Exception as e:
            print(f"HP regen task error: {e}")

    @commands.hybrid_group(name="rpg",description="RPG character system",invoke_without_command=True)
    async def rpg(self,ctx):
        char=await get_char(ctx.guild.id,ctx.author.id)
        if not char:
            v=ClassSelectView(ctx.author.id,ctx.guild.id)
            return await ctx.send(embed=discord.Embed(title="⚔️ Create Your Character",
                description="Browse classes with ◀▶ then choose your race!",color=0x5d6bb5),view=v)
        sk=await get_skills(ctx.guild.id,ctx.author.id)
        eq=await get_equipment(ctx.guild.id,ctx.author.id)
        await ctx.send(embed=char_embed(char,ctx.author),
                       view=RPGPanel(char,ctx.author,sk,eq,ctx.author.id))

    @rpg.command(name="stats")
    async def rpg_stats(self,ctx,member:discord.Member=None):
        t=member or ctx.author
        char=await get_char(ctx.guild.id,t.id)
        if not char: return await ctx.send(embed=discord.Embed(description="No character yet.",color=0xf39c12))
        sk=await get_skills(ctx.guild.id,t.id); eq=await get_equipment(ctx.guild.id,t.id)
        await ctx.send(embed=char_embed(char,t),view=RPGPanel(char,t,sk,eq,ctx.author.id))

    @rpg.command(name="allocate")
    async def rpg_allocate(self,ctx):
        char=await get_char(ctx.guild.id,ctx.author.id)
        if not char: return await ctx.send(embed=discord.Embed(description="No character. Use `/rpg`.",color=0xf39c12))
        if not char.get("stat_points"): return await ctx.send(embed=discord.Embed(description="No free points. Level up to earn more!",color=0xf39c12))
        panel=RPGPanel(char,ctx.author,await get_skills(ctx.guild.id,ctx.author.id),await get_equipment(ctx.guild.id,ctx.author.id),ctx.author.id)
        v=AllocView(char,ctx.author,ctx.guild.id,parent=panel)
        await ctx.send(embed=v._embed(),view=v)

    @rpg.command(name="leaderboard")
    async def rpg_lb(self,ctx):
        rows=await db.pool.fetch("""
            SELECT user_id,class,realm_level,(strength+constitution+agility+spirit+vitality) AS power
            FROM rpg_characters WHERE guild_id=$1 ORDER BY power DESC LIMIT 10
        """,ctx.guild.id)
        if not rows: return await ctx.send(embed=discord.Embed(description="No characters yet!",color=0x5d6bb5))
        medals=["🥇","🥈","🥉"]
        lines=[]
        for i,r in enumerate(rows):
            m=ctx.guild.get_member(r["user_id"])
            if not m: continue
            realm,rem=get_realm(r["realm_level"])
            lines.append(f"{medals[i] if i<3 else f'`{i+1}`'} **{m.display_name}** — {r['class']} · {rem} {realm} · Power **{r['power']}**")
        e=discord.Embed(title="⚔️ RPG Power Leaderboard",description="\n".join(lines) or "No data.",color=0x5d6bb5)
        await ctx.send(embed=e)
