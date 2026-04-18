# airi/rpg/stats.py — RPGStatsCog: /rpg command panel
# Moved from rpg_stats.py into rpg/ subfolder
import discord
from discord.ext import commands
from datetime import datetime, timezone
from airi import rpg
import db
from utils import C_INFO, C_SUCCESS, C_ERROR, C_WARN, _err
from .classes import CLASSES, RANK_EMOJI, RANK_COLORS, get_realm, str_label

# ── DB helpers ─────────────────────────────────────────────────────
async def get_char(gid, uid):
    r = await db.pool.fetchrow("SELECT * FROM rpg_characters WHERE guild_id=$1 AND user_id=$2", gid, uid)
    if r and "constitution" not in r:
        # Old schema, map defence to constitution
        r = dict(r)
        r["constitution"] = r.get("defence", 10)
    return dict(r) if r else None

async def create_char(gid, uid, class_name):
    cls  = CLASSES[class_name]
    base = cls["base"]
    row  = await db.pool.fetchrow("""
        INSERT INTO rpg_characters
            (guild_id,user_id,class,realm_level,strength,constitution,agility,spirit,
             hp_max,hp_current,mana_max,mana_current,stat_points,talent)
        VALUES ($1,$2,$3,1,$4,$5,$6,$7,$8,$8,$9,$9,5,$10)
        ON CONFLICT (guild_id,user_id) DO NOTHING RETURNING *
    """, gid, uid, class_name,
        base["str"], base["con"], base["agi"], base["spi"],
        base["hp"], base["mana"], cls["talent_name"])
    for s, r in cls["starting_skills"]:
        await db.pool.execute("""
            INSERT INTO rpg_skills (guild_id,user_id,skill_name,skill_rank)
            VALUES ($1,$2,$3,$4) ON CONFLICT DO NOTHING
        """, gid, uid, s, r)
    return await get_char(gid, uid)

async def get_skills(gid, uid):
    rows = await db.pool.fetch("SELECT * FROM rpg_skills WHERE guild_id=$1 AND user_id=$2", gid, uid)
    return [dict(r) for r in rows]

async def get_equipment(gid, uid):
    rows = await db.pool.fetch("SELECT * FROM rpg_equipment WHERE guild_id=$1 AND user_id=$2", gid, uid)
    return [dict(r) for r in rows]

# ── Embed helpers ──────────────────────────────────────────────────
def _bar(cur, mx, n=12):
    f = max(0, int((cur/max(mx,1))*n))
    return "█"*f + "░"*(n-f)

def stats_embed(char, member):
    cls = CLASSES.get(char["class"], {})
    realm, rem = get_realm(char["realm_level"])
    e = discord.Embed(title=f"{cls.get('emoji','⚔️')} Character Sheet", color=cls.get("color", C_INFO),
                      timestamp=datetime.now(timezone.utc))
    e.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    e.set_thumbnail(url=member.display_avatar.url)
    e.add_field(name="📋 Identity", value=(
        f"**Name:** {member.display_name}\n**Class:** {char['class']}\n"
        f"**Realm:** {rem} {realm} [Lv.{char['realm_level']}]\n"
        f"**Talent:** {char.get('talent','???')} [Rank Unknown]"), inline=False)
    e.add_field(name="⚔️ Combat Stats", value=(
        f"**STR:** {char['strength']}  *[{str_label(char['strength'])}]*\n"
        f"**CON:** {char['constitution']}\n**AGI:** {char['agility']}\n**SPI:** {char['spirit']}"), inline=True)
    e.add_field(name="💫 Vitals", value=(
        f"**HP:** {char['hp_current']}/{char['hp_max']}\n`{_bar(char['hp_current'],char['hp_max'])}` ❤️\n"
        f"**Mana:** {char['mana_current']}/{char['mana_max']}\n`{_bar(char['mana_current'],char['mana_max'])}` 💙"), inline=True)
    if char.get("stat_points", 0) > 0:
        e.add_field(name="✨ Free Points", value=f"**{char['stat_points']}** pts — use `/rpg allocate`!", inline=False)
    e.set_footer(text="📚 /rpg talent · /rpg skills · /rpg equip · /rpg allocate")
    return e

def talent_embed(char, member):
    cls = CLASSES.get(char["class"], {})
    trank = cls.get("talent_rank","Unknown")
    e = discord.Embed(title=f"✨ Talent: {char.get('talent','???')}",
                      description=f"**Rank:** [{trank}]",
                      color=RANK_COLORS.get(trank, cls.get("color", C_INFO)))
    e.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    e.add_field(name="🔵 Passive", value=cls.get("passive","Unknown"), inline=False)
    e.add_field(name="🔴 Active (Unique)", value=cls.get("active","Unknown"), inline=False)
    if cls.get("restriction"):
        e.add_field(name="⚠️ Restriction", value=cls["restriction"], inline=False)
    return e

def skills_embed(char, skills, member):
    cls = CLASSES.get(char["class"], {})
    e = discord.Embed(title=f"📚 Skill Book — {member.display_name}", color=cls.get("color", C_INFO))
    e.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    if not skills:
        e.description = "No skills yet. Defeat monsters and find skill books!"
    else:
        for s in skills[:15]:
            r = s.get("skill_rank","F")
            e.add_field(name=f"{RANK_EMOJI.get(r,'⬜')} {s['skill_name']}",
                        value=f"**[{r}-Rank]**", inline=True)
    e.set_footer(text=f"Total: {len(skills)} skills")
    return e

def equip_embed(char, equipment, member):
    cls   = CLASSES.get(char["class"], {})
    eqmap = {eq["slot"]: eq for eq in equipment}
    SLOTS = [("weapon","⚔️ Weapon"),("armor","🛡️ Armor"),("ring","💍 Ring"),("accessory","🔮 Accessory")]
    e = discord.Embed(title=f"🎒 Equipment — {member.display_name}", color=cls.get("color", C_INFO))
    e.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    for slot_key, slot_label in SLOTS:
        item = eqmap.get(slot_key)
        if item:
            r = item.get("item_rank","F")
            e.add_field(name=slot_label, value=f"{RANK_EMOJI.get(r,'⬜')} **{item['item_name']}** [{r}-Rank]\n_{item.get('effect_desc','')}_", inline=True)
        else:
            e.add_field(name=slot_label, value="_Empty_", inline=True)
    return e

# ── Views ──────────────────────────────────────────────────────────
class RPGPanel(discord.ui.View):
    def __init__(self, char, member, skills, equipment, viewer_id):
        super().__init__(timeout=300)
        self._char = char; self._member = member
        self._skills = skills; self._equipment = equipment
        self._vid = viewer_id

    @discord.ui.button(label="📋 Stats", style=discord.ButtonStyle.primary, row=0)
    async def stats_btn(self, i, b):
        await i.response.edit_message(embed=stats_embed(self._char, self._member), view=self)

    @discord.ui.button(label="✨ Talent", style=discord.ButtonStyle.secondary, row=0)
    async def talent_btn(self, i, b):
        e    = talent_embed(self._char, self._member)
        back = _Back(self, stats_embed(self._char, self._member))
        await i.response.edit_message(embed=e, view=back)

    @discord.ui.button(label="📚 Skills", style=discord.ButtonStyle.secondary, row=0)
    async def skills_btn(self, i, b):
        e    = skills_embed(self._char, self._skills, self._member)
        back = _Back(self, stats_embed(self._char, self._member))
        await i.response.edit_message(embed=e, view=back)

    @discord.ui.button(label="🎒 Equip", style=discord.ButtonStyle.secondary, row=0)
    async def equip_btn(self, i, b):
        e    = equip_embed(self._char, self._equipment, self._member)
        back = _Back(self, stats_embed(self._char, self._member))
        await i.response.edit_message(embed=e, view=back)

    @discord.ui.button(label="📊 Allocate", style=discord.ButtonStyle.success, row=1)
    async def alloc_btn(self, i, b):
        if i.user.id != self._member.id:
            return await i.response.send_message("Only the owner can allocate stats.", ephemeral=True)
        if not self._char.get("stat_points"):
            return await i.response.send_message("No free stat points!", ephemeral=True)
        v = AllocView(self._char, self._member, i.guild_id, parent=self)
        await i.response.edit_message(embed=v._embed(), view=v)

class _Back(discord.ui.View):
    def __init__(self, parent, home_embed):
        super().__init__(timeout=300)
        self._p = parent; self._h = home_embed
    @discord.ui.button(label="◀ Back", style=discord.ButtonStyle.secondary)
    async def back(self, i, b): await i.response.edit_message(embed=self._h, view=self._p)

class AllocView(discord.ui.View):
    def __init__(self, char, member, gid, parent):
        super().__init__(timeout=120)
        self._char = dict(char); self._member = member
        self._gid = gid; self._uid = member.id
        self._parent = parent
        self._pend = {"strength":0,"constitution":0,"agility":0,"spirit":0}
        self._pts = char.get("stat_points",0)
        self._upd()

    def _left(self): return self._pts - sum(self._pend.values())
    def _upd(self): self.confirm_btn.disabled = sum(self._pend.values()) == 0

    def _embed(self):
        c = self._char
        e = discord.Embed(title="📊 Allocate Stat Points",
                          description=f"**Remaining:** {self._left()} / {self._pts}",
                          color=C_INFO)
        for k, lbl in [("strength","STR"),("constitution","CON"),("agility","AGI"),("spirit","SPI")]:
            cur = c.get(k,0); add = self._pend[k]
            e.add_field(name=lbl, value=f"{cur}" + (f" → **{cur+add}** (+{add})" if add else ""), inline=True)
        return e

    async def _add(self, i, key):
        if i.user.id != self._uid: return await i.response.send_message("Not for you.", ephemeral=True)
        if self._left() <= 0: return await i.response.send_message("No points left!", ephemeral=True)
        self._pend[key] += 1; self._upd()
        await i.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="+STR", style=discord.ButtonStyle.primary, row=0)
    async def b_str(self,i,b): await self._add(i,"strength")
    @discord.ui.button(label="+CON", style=discord.ButtonStyle.primary, row=0)
    async def b_con(self,i,b): await self._add(i,"constitution")
    @discord.ui.button(label="+AGI", style=discord.ButtonStyle.primary, row=0)
    async def b_agi(self,i,b): await self._add(i,"agility")
    @discord.ui.button(label="+SPI", style=discord.ButtonStyle.primary, row=0)
    async def b_spi(self,i,b): await self._add(i,"spirit")

    @discord.ui.button(label="↺ Reset", style=discord.ButtonStyle.secondary, row=1)
    async def reset_btn(self, i, b):
        if i.user.id != self._uid: return await i.response.send_message("Not for you.", ephemeral=True)
        self._pend = {k:0 for k in self._pend}; self._upd()
        await i.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="◀ Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_btn(self, i, b):
        if i.user.id != self._uid: return await i.response.send_message("Not for you.", ephemeral=True)
        await i.response.edit_message(embed=stats_embed(self._char, self._member), view=self._parent)

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.success, disabled=True, row=1)
    async def confirm_btn(self, i, b):
        if i.user.id != self._uid: return await i.response.send_message("Not for you.", ephemeral=True)
        used = sum(self._pend.values())
        if not used: return
        for item in self.children: item.disabled = True
        await i.response.defer()
        await db.pool.execute("""
            UPDATE rpg_characters SET
                strength=strength+$1, constitution=constitution+$2,
                agility=agility+$3,   spirit=spirit+$4,
                stat_points=stat_points-$5
            WHERE guild_id=$6 AND user_id=$7
        """, self._pend["strength"], self._pend["constitution"],
            self._pend["agility"], self._pend["spirit"], used, self._gid, self._uid)
        char = await get_char(self._gid, self._uid)
        sk   = await get_skills(self._gid, self._uid)
        eq   = await get_equipment(self._gid, self._uid)
        nv   = RPGPanel(char, self._member, sk, eq, self._uid)
        e    = stats_embed(char, self._member)
        e.title = "✅ Stats Updated! " + e.title
        await i.edit_original_response(embed=e, view=nv)
        self.stop()

class ClassSelect(discord.ui.View):
    def __init__(self, uid, gid):
        super().__init__(timeout=180)
        self._uid = uid; self._gid = gid
        self._classes = list(CLASSES.items()); self._page = 0
        self._upd_label()

    def _upd_label(self): self.confirm_btn.label = f"✅ Play as {self._classes[self._page][0]}"

    def _embed(self):
        name, cls = self._classes[self._page]
        base = cls["base"]
        e = discord.Embed(title=f"{cls['emoji']} Class: {name}", description=cls["desc"], color=cls["color"])
        e.add_field(name="📊 Base Stats", value=(
            f"STR:**{base['str']}** CON:**{base['con']}**\n"
            f"AGI:**{base['agi']}** SPI:**{base['spi']}**\n"
            f"HP:**{base['hp']}** Mana:**{base['mana']}**"), inline=True)
        e.add_field(name=f"✨ {cls['talent_name']} [{cls['talent_rank']}]", value=cls["passive"][:120], inline=False)
        e.add_field(name="📚 Starting Skills",
                    value="\n".join(f"{RANK_EMOJI.get(r,'⬜')} {s} [{r}]" for s,r in cls["starting_skills"]),
                    inline=False)
        e.set_footer(text=f"Class {self._page+1}/{len(self._classes)} · ◀▶ browse · ✅ confirm")
        return e

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, row=0)
    async def prev_btn(self, i, b):
        if i.user.id != self._uid: return await i.response.send_message("Not for you.", ephemeral=True)
        self._page = (self._page - 1) % len(self._classes); self._upd_label()
        await i.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="✅ Play as ...", style=discord.ButtonStyle.success, row=0)
    async def confirm_btn(self, i, b):
        if i.user.id != self._uid: return await i.response.send_message("Not for you.", ephemeral=True)
        cls_name = self._classes[self._page][0]
        for item in self.children: item.disabled = True
        await i.response.defer()
        char = await create_char(self._gid, self._uid, cls_name)
        sk   = await get_skills(self._gid, self._uid)
        eq   = await get_equipment(self._gid, self._uid)
        cls  = CLASSES[cls_name]
        e    = stats_embed(char, i.user)
        e.title = "✨ Character Created! " + e.title
        e.description = (f"Welcome, **{i.user.display_name}**! You are now a **{cls['emoji']} {cls_name}**.\n"
                         f"You have **5 free stat points** — use `/rpg allocate`!")
        await i.edit_original_response(embed=e, view=RPGPanel(char, i.user, sk, eq, self._uid))
        self.stop()

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, row=0)
    async def next_btn(self, i, b):
        if i.user.id != self._uid: return await i.response.send_message("Not for you.", ephemeral=True)
        self._page = (self._page + 1) % len(self._classes); self._upd_label()
        await i.response.edit_message(embed=self._embed(), view=self)

# ── Cog ────────────────────────────────────────────────────────────
class RPGStatsCog(commands.Cog, name="RPG"):
    def __init__(self, bot): self.bot = bot

    @commands.hybrid_group(name="rpg", description="RPG character system", invoke_without_command=True)
    async def rpg(self, ctx):
        char = await get_char(ctx.guild.id, ctx.author.id)
        if not char:
            v = ClassSelect(ctx.author.id, ctx.guild.id)
            return await ctx.send(embed=v._embed(), view=v)
        sk = await get_skills(ctx.guild.id, ctx.author.id)
        eq = await get_equipment(ctx.guild.id, ctx.author.id)
        await ctx.send(embed=stats_embed(char, ctx.author),
                       view=RPGPanel(char, ctx.author, sk, eq, ctx.author.id))

    @rpg.command(name="stats")
    async def rpg_stats(self, ctx, member: discord.Member = None):
        target = member or ctx.author
        char = await get_char(ctx.guild.id, target.id)
        if not char:
            return await ctx.send(embed=discord.Embed(description="No character yet.", color=C_WARN))
        sk = await get_skills(ctx.guild.id, target.id)
        eq = await get_equipment(ctx.guild.id, target.id)
        await ctx.send(embed=stats_embed(char, target),
                       view=RPGPanel(char, target, sk, eq, ctx.author.id))

    @rpg.command(name="create", description="Create a new character (overwrites existing)")
    async def rpg_create(self, ctx):
        existing = await get_char(ctx.guild.id, ctx.author.id)
        if existing:
            class ConfirmOverwrite(discord.ui.View):
                def __init__(self_):
                    super().__init__(timeout=30)
                @discord.ui.button(label="⚠️ Yes, recreate", style=discord.ButtonStyle.danger)
                async def yes(self_, inter, btn):
                    if inter.user.id != ctx.author.id:
                        return await inter.response.send_message("Not for you.", ephemeral=True)
                    await db.pool.execute("DELETE FROM rpg_characters WHERE guild_id=$1 AND user_id=$2", ctx.guild.id, ctx.author.id)
                    await db.pool.execute("DELETE FROM rpg_skills WHERE guild_id=$1 AND user_id=$2", ctx.guild.id, ctx.author.id)
                    await db.pool.execute("DELETE FROM rpg_equipment WHERE guild_id=$1 AND user_id=$2", ctx.guild.id, ctx.author.id)
                    for item in self_.children: item.disabled = True
                    await inter.response.edit_message(view=self_)
                    view = ClassSelect(ctx.author.id, ctx.guild.id)
                    await inter.followup.send(embed=view._embed(), view=view)
                @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
                async def no(self_, inter, btn):
                    if inter.user.id != ctx.author.id:
                        return await inter.response.send_message("Not for you.", ephemeral=True)
                    for item in self_.children: item.disabled = True
                    await inter.response.edit_message(content="Cancelled.", view=self_)
            return await ctx.send(
                embed=discord.Embed(
                    title="⚠️ Recreate Character?",
                    description="This will delete your current character and all skills/equipment. Continue?",
                    color=C_ERROR,
                ),
                view=ConfirmOverwrite(),
            )
        view = ClassSelect(ctx.author.id, ctx.guild.id)
        await ctx.send(embed=view._embed(), view=view)

    @rpg.command(name="talent", description="View your talent in detail")
    async def rpg_talent(self, ctx):
        char = await get_char(ctx.guild.id, ctx.author.id)
        if not char:
            return await ctx.send(embed=discord.Embed(description="No character yet.", color=C_WARN))
        e = talent_embed(char, ctx.author)
        back = _Back(
            RPGPanel(char, ctx.author,
                     await get_skills(ctx.guild.id, ctx.author.id),
                     await get_equipment(ctx.guild.id, ctx.author.id),
                     ctx.author.id),
            stats_embed(char, ctx.author),
        )
        await ctx.send(embed=e, view=back)

    @rpg.command(name="skills", description="View your skill book")
    async def rpg_skills(self, ctx, member: discord.Member = None):
        target = member or ctx.author
        char = await get_char(ctx.guild.id, target.id)
        if not char:
            return await ctx.send(embed=discord.Embed(description="No character yet.", color=C_WARN))
        skills = await get_skills(ctx.guild.id, target.id)
        await ctx.send(embed=skills_embed(char, skills, target))

    @rpg.command(name="equip", description="View your equipment")
    async def rpg_equip(self, ctx, member: discord.Member = None):
        target = member or ctx.author
        char = await get_char(ctx.guild.id, target.id)
        if not char:
            return await ctx.send(embed=discord.Embed(description="No character yet.", color=C_WARN))
        equipment = await get_equipment(ctx.guild.id, target.id)
        await ctx.send(embed=equip_embed(char, equipment, target))

    @rpg.command(name="allocate")
    async def rpg_allocate(self, ctx):
        char = await get_char(ctx.guild.id, ctx.author.id)
        if not char: return await ctx.send(embed=discord.Embed(description="No character yet.", color=C_WARN))
        if not char.get("stat_points"):
            return await ctx.send(embed=discord.Embed(description="No free stat points.", color=C_WARN))
        panel = RPGPanel(char, ctx.author,
                         await get_skills(ctx.guild.id, ctx.author.id),
                         await get_equipment(ctx.guild.id, ctx.author.id),
                         ctx.author.id)
        v = AllocView(char, ctx.author, ctx.guild.id, parent=panel)
        await ctx.send(embed=v._embed(), view=v)

    @rpg.command(name="leaderboard")
    async def rpg_lb(self, ctx):
        rows = await db.pool.fetch("""
            SELECT user_id,class,realm_level,(strength+constitution+agility+spirit) AS power
            FROM rpg_characters WHERE guild_id=$1 ORDER BY power DESC LIMIT 10
        """, ctx.guild.id)
        if not rows: return await ctx.send(embed=discord.Embed(description="No characters yet!", color=C_INFO))
        medals = ["🥇","🥈","🥉"]
        lines = []
        for i, r in enumerate(rows):
            m = ctx.guild.get_member(r["user_id"])
            if not m: continue
            realm, rem = get_realm(r["realm_level"])
            lines.append(f"{medals[i] if i<3 else f'`{i+1}`'} **{m.display_name}** — {r['class']} · {rem} {realm} · Power **{r['power']}**")
        e = discord.Embed(title="⚔️ RPG Power Leaderboard",
                          description="\n".join(lines) or "No data.", color=C_INFO)
        await ctx.send(embed=e)
        
    @rpg.group(name="shop", description="RPG Shop", invoke_without_command=True)
    async def rpg_shop(self, ctx):
        e = discord.Embed(title="🏪 RPG Shop", color=C_INFO)
        e.add_field(name="`!rpg shop skills`", value="Buy spells from D&D", inline=False)
        e.add_field(name="`!rpg shop equipment`", value="Buy weapons, armor, accessories", inline=False)
        await ctx.send(embed=e)

    @rpg_shop.command(name="skills")
    async def rpg_shop_skills(self, ctx):
        from .shop import shop_skills
        await shop_skills(ctx)

    @rpg_shop.command(name="equipment")
    async def rpg_shop_equipment(self, ctx):
        from .shop import shop_equipment
        await shop_equipment(ctx)
