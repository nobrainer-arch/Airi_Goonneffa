# airi/rpg/dungeon.py — DungeonCog with !dungeon command
# Uses D&D 5e API for monsters + PIL battle card image
import discord
from discord.ext import commands
import random, asyncio, aiohttp
from datetime import datetime, timezone
import io
import db
from utils import _err, C_INFO, C_WARN, C_SUCCESS, C_ERROR
from .engine import CombatUnit, DamageCalculator, ReactionSystem, BattleEngine, Effect
from .stats  import get_char, get_skills, get_equipment
from .classes import CLASSES, get_realm
from .skills  import SKILL_DB
from .battle_image import generate_battle_card


DND_API = "https://www.dnd5eapi.co/api"
EXPLORE_CD = 300   # 5 min cooldown in seconds

# CR ranges per realm
REALM_CR = {
    "Apprentice":  (0, 0.5),
    "Disciple":    (0.5, 1),
    "Middle Stage":(1, 3),
    "Late Stage":  (3, 5),
    "Peak":        (5, 8),
    "Transcendent":(8, 15),
}

# ── D&D API monster fetcher ───────────────────────────────────────
_monster_cache: dict[str, dict] = {}
_monster_list_cache: list[dict] = []

async def _fetch_dnd_monster_list() -> list[dict]:
    global _monster_list_cache
    if _monster_list_cache:
        return _monster_list_cache
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{DND_API}/monsters", timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    data = await r.json()
                    _monster_list_cache = data.get("results", [])
                    return _monster_list_cache
    except Exception as e:
        print(f"DnD API list error: {e}")
    return []

async def _fetch_dnd_monster(slug: str) -> dict | None:
    if slug in _monster_cache:
        return _monster_cache[slug]
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{DND_API}/monsters/{slug}", timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    data = await r.json()
                    _monster_cache[slug] = data
                    return data
    except Exception as e:
        print(f"DnD API monster error: {e}")
    return None

async def _get_random_monster(realm_level: int) -> dict:
    """Fetch a random D&D monster appropriate for the realm, or use fallback."""
    realm, _ = get_realm(realm_level)
    cr_min, cr_max = REALM_CR.get(realm, (0, 1))

    monster_list = await _fetch_dnd_monster_list()
    if monster_list:
        # Filter by name heuristic (can't filter by CR from list endpoint)
        # Just pick random and scale
        slug = random.choice(monster_list)["index"]
        data = await _fetch_dnd_monster(slug)
        if data:
            return _parse_dnd_monster(data, realm_level)

    # Fallback pool
    return _fallback_monster(realm_level)

def _parse_dnd_monster(data: dict, realm_level: int) -> dict:
    """Convert D&D API monster to our stat system."""
    name = data.get("name", "Unknown Monster")
    hp   = data.get("hit_points", 30) + realm_level * 2
    ac   = data.get("armor_class", [{}])
    if isinstance(ac, list):
        ac_val = ac[0].get("value", 12) if ac else 12
    else:
        ac_val = 12

    abl = data.get("special_abilities", [])
    actions = data.get("actions", [])
    action_names = [a.get("name","Attack") for a in actions[:3]]

    # Map D&D ability scores → our system
    str_score = data.get("strength", 10)
    dex_score = data.get("dexterity", 10)
    con_score = data.get("constitution", 10)
    wis_score = data.get("wisdom", 10)

    our_str = max(5, (str_score - 10) * 2 + 10 + realm_level)
    our_def = max(2, (con_score - 10) + ac_val // 2)
    our_agi = max(3, (dex_score - 10) + 8)
    our_spi = max(3, (wis_score - 10) + 5)

    cr = data.get("challenge_rating", 1)
    xp = data.get("xp", int(cr * 100))

    # Monster image from Open5e (fallback)
    image_url = data.get("image") or None

    # Synthetic skills from actions
    skills = []
    for a in actions[:3]:
        skills.append({
            "name": a.get("name","Attack")[:16],
            "rank": "C",
            "on_cd": False,
        })

    size = data.get("size","Medium")
    type_str = f"{size} {data.get('type','Monster')}"

    return {
        "name":       name,
        "type":       type_str,
        "hp":         hp,
        "hp_max":     hp,
        "mp":         0,
        "mp_max":     0,
        "str":        our_str,
        "def":        our_def,
        "agi":        our_agi,
        "spi":        our_spi,
        "reaction":   our_agi,
        "xp":         xp,
        "coins":      (our_str * 3, our_str * 8),
        "image_url":  image_url,
        "skills":     skills,
        "weapon":     action_names[0] if action_names else "Claws",
        "armor":      f"AC {ac_val}",
        "color":      (180, 60, 60),
        "dmg_reduction": min(0.4, ac_val / 50),
    }

def _fallback_monster(realm_level: int) -> dict:
    realm, _ = get_realm(realm_level)
    POOL = {
        "Apprentice": [
            {"name":"Crawler",     "type":"Normal Monster","hp":40, "str":12,"def":4, "agi":14,"spi":3,"xp":60, "coins":(30,80),"color":(80,140,80)},
            {"name":"Skeleton",    "type":"Undead",        "hp":30, "str":10,"def":6, "agi":10,"spi":2,"xp":45, "coins":(20,60),"color":(200,200,180)},
            {"name":"Goblin",      "type":"Humanoid",      "hp":25, "str":8, "def":3, "agi":16,"spi":4,"xp":40, "coins":(15,50),"color":(80,160,40)},
        ],
        "Disciple": [
            {"name":"Corruptor",   "type":"Normal Monster","hp":70, "str":18,"def":12,"agi":8, "spi":8,"xp":120,"coins":(80,200),"color":(130,50,200)},
            {"name":"Sprint Predator","type":"Beast",      "hp":55, "str":16,"def":5, "agi":24,"spi":3,"xp":100,"coins":(60,160),"color":(200,130,30)},
        ],
        "Middle Stage": [
            {"name":"Nurturer",    "type":"Boss",          "hp":200,"str":28,"def":15,"agi":12,"spi":10,"xp":300,"coins":(250,600),"color":(180,60,60)},
            {"name":"Dark Knight", "type":"Humanoid Elite","hp":180,"str":32,"def":22,"agi":10,"spi":5,"xp":280,"coins":(200,500),"color":(50,50,150)},
        ],
        "Late Stage": [
            {"name":"Curse Master","type":"Rare Boss",     "hp":400,"str":45,"def":25,"agi":18,"spi":22,"xp":600,"coins":(500,1200),"color":(160,40,200)},
        ],
        "Peak": [
            {"name":"Ferocious Ape","type":"Elite Boss",   "hp":600,"str":70,"def":30,"agi":25,"spi":10,"xp":1000,"coins":(800,2000),"color":(120,80,40)},
        ],
        "Transcendent": [
            {"name":"Nightmare King","type":"Legendary Boss","hp":1000,"str":100,"def":40,"agi":60,"spi":30,"xp":2000,"coins":(1500,4000),"color":(60,0,100)},
        ],
    }
    pool = POOL.get(realm, POOL["Apprentice"])
    m = dict(random.choice(pool))
    m["hp"]      += realm_level * 3
    m["hp_max"]  = m["hp"]
    m["mp"]      = 0
    m["mp_max"]  = 0
    m["reaction"]= m["agi"]
    m["image_url"]= None
    m["skills"]  = [{"name":"Bite","rank":"F","on_cd":False}]
    m["weapon"]  = "Claws"
    m["armor"]   = "Scales"
    m["dmg_reduction"] = 0.05
    m["spi"]     = m.get("spi",5)
    return m


# ── Battle View ───────────────────────────────────────────────────
class BattleView(discord.ui.View):
    def __init__(self, ctx, player_char: dict, player_skills_db: list,
                 monster: dict, equipment: list):
        super().__init__(timeout=300)
        self._ctx = ctx
        self._eq  = equipment
        gid, uid  = ctx.guild.id, ctx.author.id

        # Class color
        cls      = CLASSES.get(player_char["class"], {})
        cls_col  = cls.get("color", 0x4444ff)
        cls_rgb  = ((cls_col>>16)&0xff, (cls_col>>8)&0xff, cls_col&0xff)
        mon_col  = monster.get("color", (180,60,60))

        # Build player CombatUnit
        pc = CombatUnit(
            name         = ctx.author.display_name,
            hp           = player_char["hp_current"],
            hp_max       = player_char["hp_max"],
            mana         = player_char["mana_current"],
            mana_max     = player_char["mana_max"],
            strength     = player_char["strength"],
            constitution = player_char["constitution"],
            agility      = player_char["agility"],
            spirit       = player_char["spirit"],
            reaction     = player_char["spirit"],
            crit_chance  = cls.get("base",{}).get("crit_chance",0.08),
            crit_damage  = 1.5,
            damage_reduction = cls.get("base",{}).get("damage_reduction",0.05),
            reflect_pct  = 0.10 if player_char["class"] == "Knight" else 0.0,
            grade        = "Normal",
            is_player    = True,
            first_hit_active = player_char["class"] == "Gunman",
            first_hit_bonus  = 0.5 if player_char["class"] == "Gunman" else 0.0,
        )
        # Apply class bonuses
        bonus = cls.get("bonus", {})
        pc.strength      += bonus.get("str",0)
        pc.constitution  += bonus.get("con",0)
        pc.agility       += bonus.get("agi",0)
        pc.spirit        += bonus.get("spi",0)

        # Build monster CombatUnit
        mc = CombatUnit(
            name         = monster["name"],
            hp           = monster["hp"],
            hp_max       = monster["hp_max"],
            mana         = monster["mp"],
            mana_max     = monster["mp_max"],
            strength     = monster["str"],
            constitution = monster["def"],
            agility      = monster["agi"],
            spirit       = monster.get("spi", 5),
            reaction     = monster["reaction"],
            crit_chance  = 0.05,
            crit_damage  = 1.5,
            damage_reduction = monster.get("dmg_reduction", 0.05),
            reflect_pct  = 0.0,
            grade        = "Normal",
            is_player    = False,
        )

        self._engine     = BattleEngine(pc, mc)
        self._monster    = monster
        self._char       = player_char
        self._skills_db  = player_skills_db
        self._cls_rgb    = cls_rgb
        self._mon_col    = mon_col
        self._gid        = gid
        self._uid        = uid
        self._log: list[str] = []
        self._running    = True

    def _player_skill_list(self) -> list[dict]:
        """Build skill list for image from DB skills."""
        out = []
        for s in self._skills_db[:3]:
            name = s["skill_name"]
            cd   = self._engine.player.cooldowns.get(name, 0)
            out.append({"name": name, "rank": s.get("skill_rank","F"), "on_cd": cd > 0})
        return out

    def _monster_skill_list(self) -> list[dict]:
        return self._monster.get("skills", [])[:3]

    def _effects_list(self, unit: CombatUnit) -> list[str]:
        tags = {"venom":"☠️Venom","stun":"⚡Stun","ground_bind":"🌿Bind",
                "nightmare":"💤Sleep","burn":"🔥Burn","bleed":"🩸Bleed"}
        return [f"{tags.get(e.type,e.type)}({e.duration}T)" for e in unit.effects[:3]]

    async def _render(self) -> discord.File:
        p = self._engine.player
        m = self._engine.monster
        eq_map = {e["slot"]: e for e in self._eq}
        weapon_name = eq_map.get("weapon", {}).get("item_name", "Unarmed")
        armor_name  = eq_map.get("armor",  {}).get("item_name", "None")

        buf = await generate_battle_card(
            player_name     = self._ctx.author.display_name,
            player_class    = self._char["class"],
            player_hp       = max(0, p.hp), player_hp_max = p.hp_max,
            player_mp       = max(0, p.mana), player_mp_max = p.mana_max,
            player_str      = p.strength, player_def = p.constitution, player_agi = p.agility,
            player_skills   = self._player_skill_list(),
            player_weapon   = weapon_name, player_armor = armor_name,
            player_avatar_url = str(self._ctx.author.display_avatar.url),
            player_class_color = self._cls_rgb,
            monster_name    = m.name,
            monster_type    = self._monster.get("type","Monster"),
            monster_hp      = max(0, m.hp),  monster_hp_max = m.hp_max,
            monster_mp      = max(0, m.mana), monster_mp_max = m.mana_max,
            monster_str     = m.strength, monster_def = m.constitution, monster_agi = m.agility,
            monster_skills  = self._monster_skill_list(),
            monster_weapon  = self._monster.get("weapon","Claws"),
            monster_armor   = self._monster.get("armor","Hide"),
            monster_image_url = self._monster.get("image_url"),
            monster_color   = self._mon_col,
            effects_player  = self._effects_list(self._engine.player),
            effects_monster = self._effects_list(self._engine.monster),
            combat_log      = self._log[-2:],
            turn_owner      = "player" if self._running else "none",
            sleeping        = self._engine.monster.sleeping,
        )
        return discord.File(buf, filename="battle.png")

    def _embed(self, result: dict | None = None) -> discord.Embed:
        p = self._engine.player
        m = self._engine.monster
        e = discord.Embed(
            title=f"⚔️ {p.name} VS {m.name}",
            color=0xe67e22,
            timestamp=datetime.now(timezone.utc),
        )
        e.set_image(url="attachment://battle.png")
        if result and result.get("log"):
            last = result["log"][-1] if result["log"] else ""
            e.description = f"`{last}`"
        if not self._running:
            if m.alive:
                e.description = "💀 You were defeated!"
            else:
                xp, coins = self._monster.get("xp",50), random.randint(*self._monster.get("coins",(30,100)))
                e.description = f"✅ **Victory!**\n+{xp} XP  ·  +{coins:,} coins"
        return e

    async def _update(self, interaction: discord.Interaction, result: dict):
        for line in result.get("log", []):
            self._log.append(line)
        if not result.get("player_alive", True):
            self._running = False
            await self._end(interaction, victory=False)
            return
        if not result.get("monster_alive", True):
            self._running = False
            await self._end(interaction, victory=True)
            return
        if result.get("fled"):
            self._running = False
            await db.pool.execute("""
                INSERT INTO work_log (guild_id,user_id,last_explore) VALUES ($1,$2,NOW())
                ON CONFLICT (guild_id,user_id) DO UPDATE SET last_explore=NOW()
            """, self._gid, self._uid)
            f = await self._render()
            e = self._embed(result)
            e.description = "🏃 You fled successfully and recovered fully!"
            for c in self.children: c.disabled = True
            await interaction.edit_original_response(embed=e, attachments=[f], view=self)
            return

        self._update_buttons()
        f = await self._render()
        await interaction.edit_original_response(embed=self._embed(result), attachments=[f], view=self)

    async def _end(self, interaction: discord.Interaction, victory: bool):
        for c in self.children: c.disabled = True
        if victory:
            xp    = self._monster.get("xp", 50)
            coins = random.randint(*self._monster.get("coins", (30,100)))
            await db.pool.execute("""
                INSERT INTO xp (guild_id,user_id,xp) VALUES ($1,$2,$3)
                ON CONFLICT (guild_id,user_id) DO UPDATE SET xp=xp.xp+$3
            """, self._gid, self._uid, xp)
            from airi.economy import add_coins
            await add_coins(self._gid, self._uid, coins)
            # Heal to full after win
            await db.pool.execute("""
                UPDATE rpg_characters SET hp_current=hp_max, mana_current=mana_max
                WHERE guild_id=$1 AND user_id=$2
            """, self._gid, self._uid)
            # Mage passive: stat point on kill
            if self._char["class"] == "Mage":
                await db.pool.execute("""
                    UPDATE rpg_characters SET stat_points=stat_points+1
                    WHERE guild_id=$1 AND user_id=$2
                """, self._gid, self._uid)
        else:
            # Respawn at 1 HP, lose 10% coins
            from airi.economy import get_balance, add_coins
            bal  = await get_balance(self._gid, self._uid)
            loss = min(500, bal // 10)
            if loss > 0: await add_coins(self._gid, self._uid, -loss)
            await db.pool.execute("""
                UPDATE rpg_characters SET hp_current=1, mana_current=mana_max//2
                WHERE guild_id=$1 AND user_id=$2
            """, self._gid, self._uid)

        # Set explore cooldown
        await db.pool.execute("""
            INSERT INTO work_log (guild_id,user_id,last_explore) VALUES ($1,$2,NOW())
            ON CONFLICT (guild_id,user_id) DO UPDATE SET last_explore=NOW()
        """, self._gid, self._uid)

        f = await self._render()
        await interaction.edit_original_response(embed=self._embed(), attachments=[f], view=self)
        self.stop()

    def _update_buttons(self):
        enabled = self._running and self._engine.player.alive
        for c in self.children:
            if hasattr(c, "label"):
                c.disabled = not enabled

    @discord.ui.button(label="⚔️ Attack", style=discord.ButtonStyle.danger, row=0)
    async def attack_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._uid:
            return await interaction.response.send_message("Not your battle.", ephemeral=True)
        if not self._running:
            return await interaction.response.send_message("Battle is over.", ephemeral=True)
        await interaction.response.defer()
        result = self._engine.process_player_action("attack")
        await self._update(interaction, result)

    @discord.ui.button(label="✨ Skill", style=discord.ButtonStyle.primary, row=0)
    async def skill_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._uid:
            return await interaction.response.send_message("Not your battle.", ephemeral=True)
        if not self._running:
            return await interaction.response.send_message("Battle is over.", ephemeral=True)

        avail = []
        for s in self._skills_db:
            name = s["skill_name"]
            info = SKILL_DB.get(name, {})
            cd   = self._engine.player.cooldowns.get(name, 0)
            mana = info.get("mana", s.get("mana_cost", 10))
            if cd <= 0 and self._engine.player.mana >= mana:
                avail.append(s)

        if not avail:
            return await interaction.response.send_message("No skills available (check mana/cooldowns).", ephemeral=True)

        opts = [
            discord.SelectOption(
                label=f"{s['skill_name']} [{s.get('skill_rank','F')}]",
                value=s["skill_name"],
                description=f"Mana: {SKILL_DB.get(s['skill_name'],{}).get('mana',10)}",
            ) for s in avail[:25]
        ]
        sel = discord.ui.Select(placeholder="Choose a skill…", options=opts)
        async def sel_cb(i2: discord.Interaction):
            if i2.user.id != self._uid:
                return await i2.response.send_message("Not for you.", ephemeral=True)
            await i2.response.defer()
            sname = sel.values[0]
            info = SKILL_DB.get(sname, {})
            mana = info.get("mana", 10)
            if self._engine.player.mana < mana:
                return await i2.followup.send(f"❌ Not enough mana! Need {mana}.", ephemeral=True)
            
            self._engine.player.mana -= mana
            skill_type = info.get("type", "attack")
            
            if skill_type == "heal":
                heal_pct = info.get("heal_pct", 0.25)
                heal = int(self._engine.player.hp_max * heal_pct)
                self._engine.player.hp = min(self._engine.player.hp_max, self._engine.player.hp + heal)
                result = {"log": [f"💚 Used {sname}: +{heal} HP!"], "player_alive": True, "monster_alive": True}
                await self._update(i2, result)
                
            elif skill_type == "shield":
                shield_pct = info.get("effect", {}).get("value_pct_mana", 0.5)
                shield_value = int(self._engine.player.mana_max * shield_pct)
                self._engine.player.effects.append(Effect(type="shield", duration=3, value=shield_value, source=sname))
                result = {"log": [f"🔵 Used {sname}: Shield absorbing {shield_value} damage created!"], "player_alive": True, "monster_alive": True}
                await self._update(i2, result)
                
            elif skill_type == "stealth":
                result = self._engine.process_player_action("stealth")
                await self._update(i2, result)
                
            elif skill_type in ("debuff", "cc"):
                effect_info = info.get("effect", {})
                eff_type = effect_info.get("type", "ground_bind")
                duration = effect_info.get("duration", 2)
                self._engine.monster.add_effect(eff_type, duration, source=sname)
                result = {"log": [f"🌀 Used {sname}: {eff_type} applied for {duration} turns!"], "player_alive": True, "monster_alive": True}
                await self._update(i2, result)
                
            else:  # attack type
                mult = info.get("multiplier", 1.0)
                result = self._engine.process_player_action("skill", sname, skill_multiplier=mult)
                await self._update(i2, result)
                
        sel.callback = sel_cb
        sv = discord.ui.View(timeout=30)
        sv.add_item(sel)
        await interaction.response.send_message("Choose a skill:", view=sv, ephemeral=True)

    @discord.ui.button(label="🌑 Stealth", style=discord.ButtonStyle.secondary, row=0)
    async def stealth_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._uid:
            return await interaction.response.send_message("Not your battle.", ephemeral=True)
        if self._char["class"] not in ("Shadow",):
            return await interaction.response.send_message("Your class can't use stealth.", ephemeral=True)
        if not self._running: return
        await interaction.response.defer()
        result = self._engine.process_player_action("stealth")
        await self._update(interaction, result)

    @discord.ui.button(label="🏃 Flee", style=discord.ButtonStyle.secondary, row=1)
    async def flee_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._uid:
            return await interaction.response.send_message("Not your battle.", ephemeral=True)
        if not self._running: return
        await interaction.response.defer()
        result = self._engine.process_player_action("flee")
        await self._update(interaction, result)


# ── Cog ────────────────────────────────────────────────────────────
class DungeonCog(commands.Cog, name="Dungeon"):
    def __init__(self, bot): self.bot = bot

    @commands.hybrid_command(name="dungeon", aliases=["explore","hunt"],
                             description="Enter a dungeon and fight monsters")
    async def dungeon(self, ctx):
        gid, uid = ctx.guild.id, ctx.author.id
        char = await get_char(gid, uid)
        if not char:
            return await ctx.send(embed=discord.Embed(
                description="You need a character first! Use `/rpg` to create one.", color=C_WARN))

        # Cooldown check
        cd_row = await db.pool.fetchrow(
            "SELECT last_explore FROM work_log WHERE guild_id=$1 AND user_id=$2", gid, uid
        )
        if cd_row and cd_row.get("last_explore"):
            from datetime import timezone as tz
            last = cd_row["last_explore"]
            if last and (not hasattr(last,"tzinfo") or last.tzinfo is None):
                last = last.replace(tzinfo=tz.utc)
            from datetime import timedelta
            elapsed = (datetime.now(tz.utc) - last).total_seconds()
            if elapsed < EXPLORE_CD:
                rem = int(EXPLORE_CD - elapsed)
                return await ctx.send(
                    embed=discord.Embed(
                        description=f"⏱️ You're recovering. Explore again in **{rem//60}m {rem%60}s**.",
                        color=C_WARN), delete_after=10)

        await ctx.defer()

        # Fetch monster
        monster = await _get_random_monster(char["realm_level"])

        # Load player skills and equipment
        skills    = await get_skills(gid, uid)
        equipment = await get_equipment(gid, uid)

        view = BattleView(ctx, char, skills, monster, equipment)

        # Initial render
        f = await view._render()
        e = view._embed()
        e.description = f"⚔️ A **{monster['name']}** appears!\n*Use the buttons to fight.*"
        await ctx.send(embed=e, file=f, view=view)
