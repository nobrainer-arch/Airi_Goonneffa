# airi/rpg/guild_system.py — RPG Guild System
# Guilds: create (level req), GvG combat, honor points, dungeon control
# Inspired by manhwa guild panels (Level / EXP Pool / Honor Points)
import discord
from discord.ext import commands
from discord.ext import tasks
from datetime import datetime, timezone, timedelta
import random
import db
from utils import _err, C_INFO, C_SUCCESS, C_WARN, C_ERROR

GUILD_CREATE_LEVEL  = 10   # character level needed to found a guild
GUILD_MAX_MEMBERS   = 50
GVG_COOLDOWN_HOURS  = 24
HONOR_PER_GVG_WIN   = 500
HONOR_PER_GVG_LOSS  = 50
DUNGEON_CONTROL_COST= 2000  # honor points to control a dungeon tier

GUILD_TIER_NAMES = ["Beginner","Bronze","Silver","Gold","Platinum","Diamond","Legendary"]

def _guild_tier(honor: int) -> str:
    if honor < 1000:   return "Beginner"
    if honor < 5000:   return "Bronze"
    if honor < 15000:  return "Silver"
    if honor < 40000:  return "Gold"
    if honor < 100000: return "Platinum"
    if honor < 300000: return "Diamond"
    return "Legendary"

def _guild_embed(guild_row: dict, guild: discord.Guild) -> discord.Embed:
    """Manhwa-style guild info panel."""
    leader = guild.get_member(guild_row["leader_id"])
    tier   = _guild_tier(guild_row.get("honor_points",0))
    color  = {
        "Beginner":0x808080,"Bronze":0xcd7f32,"Silver":0xc0c0c0,
        "Gold":0xffd700,"Platinum":0x00ced1,"Diamond":0x00bfff,"Legendary":0xff4500,
    }.get(tier,0x5d6bb5)

    e = discord.Embed(
        title=f"⚔️ Guild: {guild_row['name']}",
        description=guild_row.get("description","No description set."),
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    e.add_field(name="\u200b", value=(
        f"```\n"
        f"[GUILD: {guild_row['name'].upper()}]\n"
        f"LEVEL: {tier.upper()}\n"
        f"EXP POOL: {guild_row.get('exp_pool',0):,} POINTS\n"
        f"HONOR POINTS: {guild_row.get('honor_points',0):,}\n"
        f"MEMBERS: {guild_row.get('member_count',0)}/{GUILD_MAX_MEMBERS}\n"
        f"LEADER: {leader.display_name if leader else 'Unknown'}\n"
        f"```"
    ), inline=False)

    controlled = guild_row.get("controlled_dungeons","")
    if controlled:
        e.add_field(
            name="🏰 Controlled Dungeons",
            value=controlled.replace(",",", ") or "None",
            inline=True,
        )
    wins  = guild_row.get("gvg_wins",0)
    losses= guild_row.get("gvg_losses",0)
    e.add_field(name="⚔️ GvG Record", value=f"{wins}W / {losses}L", inline=True)
    e.set_footer(text=f"Guild ID: {guild_row['id']}")
    return e

# ── DB helpers ──────────────────────────────────────────────────────
async def get_guild_by_user(gid: int, uid: int) -> dict | None:
    r = await db.pool.fetchrow("""
        SELECT rg.* FROM rpg_guilds rg
        JOIN rpg_guild_members rgm ON rgm.guild_id=rg.id
        WHERE rg.server_id=$1 AND rgm.user_id=$2
    """, gid, uid)
    return dict(r) if r else None

async def get_guild_by_name(gid: int, name: str) -> dict | None:
    r = await db.pool.fetchrow(
        "SELECT * FROM rpg_guilds WHERE server_id=$1 AND LOWER(name)=LOWER($2)", gid, name
    )
    return dict(r) if r else None

async def get_guild_by_id(guild_id: int) -> dict | None:
    r = await db.pool.fetchrow("SELECT * FROM rpg_guilds WHERE id=$1", guild_id)
    return dict(r) if r else None

async def get_guild_members(guild_id: int) -> list[dict]:
    rows = await db.pool.fetch("""
        SELECT rgm.user_id, rgm.role, rgm.joined_at,
               rc.char_level, rc.strength, rc.constitution, rc.agility
        FROM rpg_guild_members rgm
        LEFT JOIN rpg_characters rc ON rc.user_id=rgm.user_id
        WHERE rgm.guild_id=$1 ORDER BY rc.char_level DESC NULLS LAST
    """, guild_id)
    return [dict(r) for r in rows]

async def get_char_level(gid: int, uid: int) -> int:
    v = await db.pool.fetchval(
        "SELECT COALESCE(char_level, realm_level, 1) FROM rpg_characters WHERE guild_id=$1 AND user_id=$2",
        gid, uid
    )
    return int(v or 1)

# ── Guild power calculation for GvG ────────────────────────────────
async def _guild_power(guild_id: int) -> int:
    rows = await db.pool.fetch("""
        SELECT rc.strength, rc.constitution, rc.agility, rc.spirit,
               COALESCE(rc.char_level,1) AS char_level
        FROM rpg_guild_members rgm
        JOIN rpg_characters rc ON rc.user_id=rgm.user_id
        WHERE rgm.guild_id=$1
    """, guild_id)
    if not rows: return 0
    total = sum((r["strength"]+r["constitution"]+r["agility"]+r["spirit"]) * r["char_level"]
                for r in rows)
    return total


# ── Views ───────────────────────────────────────────────────────────
class GuildCreateModal(discord.ui.Modal, title="Create a Guild"):
    name_in = discord.ui.TextInput(label="Guild Name",       required=True,  max_length=32)
    desc_in = discord.ui.TextInput(label="Guild Description",required=False, max_length=200,
                                    style=discord.TextStyle.paragraph)

    def __init__(self, ctx):
        super().__init__()
        self._ctx = ctx

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        gid, uid = self._ctx.guild.id, interaction.user.id
        name = self.name_in.value.strip()
        desc = self.desc_in.value.strip() or "A brave new guild."

        # Validate level
        char_level = await get_char_level(gid, uid)
        if char_level < GUILD_CREATE_LEVEL:
            return await interaction.followup.send(
                f"❌ Need character level **{GUILD_CREATE_LEVEL}** to create a guild (you are level {char_level}).",
                ephemeral=True,
            )
        # Check not already in a guild
        existing = await get_guild_by_user(gid, uid)
        if existing:
            return await interaction.followup.send(
                f"❌ You're already in **{existing['name']}**. Leave first.", ephemeral=True
            )
        # Check name taken
        taken = await get_guild_by_name(gid, name)
        if taken:
            return await interaction.followup.send(f"❌ Guild name **{name}** already taken.", ephemeral=True)

        # Create guild
        row = await db.pool.fetchrow("""
            INSERT INTO rpg_guilds
                (server_id, name, description, leader_id, exp_pool, honor_points,
                 gvg_wins, gvg_losses, member_count, created_at)
            VALUES ($1,$2,$3,$4,0,0,0,0,1,NOW())
            RETURNING id
        """, gid, name, desc, uid)
        guild_id = row["id"]

        await db.pool.execute("""
            INSERT INTO rpg_guild_members (guild_id, user_id, role, joined_at)
            VALUES ($1,$2,'leader',NOW())
        """, guild_id, uid)

        e = discord.Embed(
            title=f"🏛️ Guild Created: {name}",
            description=(
                f"**{interaction.user.display_name}** founded **{name}**!\n\n"
                f"*{desc}*\n\n"
                f"Use `/guild invite @member` to recruit.\n"
                f"Use `/guild war @guild` to challenge others.\n"
                f"Earn **honor points** through GvG to control dungeons!"
            ),
            color=C_SUCCESS,
        )
        await interaction.followup.send(embed=e, ephemeral=False)


class GvGView(discord.ui.View):
    """Guild vs Guild challenge — both leaders must confirm, then auto-combat runs."""
    def __init__(self, challenger_guild: dict, target_guild: dict, target_leader: discord.Member):
        super().__init__(timeout=120)
        self._chal  = challenger_guild
        self._tgt   = target_guild
        self._tgt_leader = target_leader
        self._accepted   = False

    @discord.ui.button(label="⚔️ Accept Challenge!", style=discord.ButtonStyle.danger)
    async def accept(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._tgt_leader.id:
            return await interaction.response.send_message(
                "Only the challenged guild's leader can accept.", ephemeral=True
            )
        self._accepted = True
        for c in self.children: c.disabled = True
        await interaction.response.edit_message(view=self)
        await self._run_gvg(interaction)
        self.stop()

    @discord.ui.button(label="❌ Decline", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._tgt_leader.id:
            return await interaction.response.send_message(
                "Only the challenged guild's leader can decline.", ephemeral=True
            )
        for c in self.children: c.disabled = True
        await interaction.response.edit_message(
            embed=discord.Embed(description=f"❌ {self._tgt['name']} declined the challenge.", color=C_WARN),
            view=self,
        )
        self.stop()

    async def _run_gvg(self, interaction: discord.Interaction):
        """Auto-calculate GvG winner based on guild power."""
        p1 = await _guild_power(self._chal["id"])
        p2 = await _guild_power(self._tgt["id"])
        # Add some randomness (20% variance)
        p1 = int(p1 * random.uniform(0.85, 1.15))
        p2 = int(p2 * random.uniform(0.85, 1.15))

        winner = self._chal if p1 >= p2 else self._tgt
        loser  = self._tgt  if p1 >= p2 else self._chal

        # Award honor points
        honor_gain = HONOR_PER_GVG_WIN
        honor_loss = HONOR_PER_GVG_LOSS
        await db.pool.execute("""
            UPDATE rpg_guilds
            SET honor_points=honor_points+$1, gvg_wins=gvg_wins+1,
                last_gvg=NOW()
            WHERE id=$2
        """, honor_gain, winner["id"])
        await db.pool.execute("""
            UPDATE rpg_guilds
            SET honor_points=GREATEST(0, honor_points-$1), gvg_losses=gvg_losses+1,
                last_gvg=NOW()
            WHERE id=$2
        """, honor_loss, loser["id"])

        # Transfer honor banner
        e = discord.Embed(
            title="⚔️ GvG Battle Result!",
            color=C_SUCCESS,
        )
        e.add_field(
            name=f"🏆 {winner['name']} WINS!",
            value=(
                f"Power: **{p1 if winner==self._chal else p2:,}** vs **{p2 if winner==self._chal else p1:,}**\n"
                f"[{winner['name']}'s HONOR POINTS TRANSFERRED TO — {winner['name']}]\n"
                f"+{honor_gain} honor  ·  -{honor_loss} honor from {loser['name']}"
            ),
            inline=False,
        )
        e.add_field(
            name=f"💔 {loser['name']} defeated",
            value=f"Lost **{honor_loss}** honor points.",
            inline=False,
        )
        e.set_footer(text="Next GvG available in 24 hours")
        await interaction.followup.send(embed=e)


# ── Guild Command Panel View ────────────────────────────────────────
class GuildPanelView(discord.ui.View):
    """Main guild panel — shows guild info with action buttons."""
    def __init__(self, ctx, my_guild: dict):
        super().__init__(timeout=300)
        self._ctx   = ctx
        self._guild = my_guild
        is_leader = (my_guild["leader_id"] == ctx.author.id)
        if not is_leader:
            self.remove_item(self.manage_btn)
            self.remove_item(self.war_btn)

    @discord.ui.button(label="👥 Members", style=discord.ButtonStyle.secondary, row=0)
    async def members_btn(self, interaction: discord.Interaction, btn):
        members = await get_guild_members(self._guild["id"])
        guild   = interaction.guild
        e = discord.Embed(title=f"👥 {self._guild['name']} — Members", color=C_INFO)
        for m in members[:20]:
            user = guild.get_member(m["user_id"])
            uname = user.display_name if user else f"<@{m['user_id']}>"
            role  = "👑 Leader" if m["role"]=="leader" else ("⚔️ Officer" if m["role"]=="officer" else "🪖 Member")
            lvl   = m.get("char_level",1) or 1
            e.add_field(name=f"{role} {uname}", value=f"Level {lvl}", inline=True)
        e.set_footer(text=f"{len(members)}/{GUILD_MAX_MEMBERS} members")
        await interaction.response.send_message(embed=e, ephemeral=True)

    @discord.ui.button(label="🏰 Dungeons", style=discord.ButtonStyle.secondary, row=0)
    async def dungeons_btn(self, interaction: discord.Interaction, btn):
        controlled = self._guild.get("controlled_dungeons","") or ""
        honor      = self._guild.get("honor_points",0)
        e = discord.Embed(title=f"🏰 Dungeon Control — {self._guild['name']}", color=C_INFO)
        e.add_field(
            name="Currently Controlling",
            value=controlled.replace(",","\n") or "None",
            inline=False,
        )
        e.add_field(
            name="💡 How it works",
            value=(
                f"Spend **{DUNGEON_CONTROL_COST:,}** honor to claim a dungeon tier.\n"
                "Members get **+10% loot bonus** in controlled dungeons.\n"
                f"Your guild has **{honor:,}** honor points.\n\n"
                "Use `/guild control <tier>` to claim."
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=e, ephemeral=True)

    @discord.ui.button(label="⚔️ Declare War", style=discord.ButtonStyle.danger, row=1)
    async def war_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._guild["leader_id"]:
            return await interaction.response.send_message("Only the guild leader can declare war.", ephemeral=True)
        await interaction.response.send_message(
            "Use `/guild war <guild_name>` to challenge another guild!", ephemeral=True
        )

    @discord.ui.button(label="⚙️ Manage", style=discord.ButtonStyle.secondary, row=1)
    async def manage_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._guild["leader_id"]:
            return await interaction.response.send_message("Only the guild leader can manage.", ephemeral=True)
        e = discord.Embed(
            title="⚙️ Guild Management",
            description=(
                "`/guild invite @member` — Invite someone to the guild\n"
                "`/guild kick @member` — Remove a member\n"
                "`/guild promote @member` — Promote to officer\n"
                "`/guild control <tier>` — Spend honor to control a dungeon\n"
                "`/guild disband` — Dissolve the guild (irreversible)\n"
                "`/guild setdesc <text>` — Update the guild description"
            ),
            color=C_INFO,
        )
        await interaction.response.send_message(embed=e, ephemeral=True)

    @discord.ui.button(label="🚪 Leave", style=discord.ButtonStyle.secondary, row=1)
    async def leave_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id == self._guild["leader_id"]:
            return await interaction.response.send_message(
                "You're the leader — disband the guild or transfer leadership first.", ephemeral=True
            )
        class ConfirmLeave(discord.ui.View):
            def __init__(cv): super().__init__(timeout=30)
            @discord.ui.button(label="Yes, leave",style=discord.ButtonStyle.danger)
            async def yes(cv,i2,b):
                await i2.response.defer(ephemeral=True)
                await db.pool.execute("DELETE FROM rpg_guild_members WHERE guild_id=$1 AND user_id=$2",
                                      self._guild["id"], i2.user.id)
                await db.pool.execute("UPDATE rpg_guilds SET member_count=GREATEST(0,member_count-1) WHERE id=$1",
                                      self._guild["id"])
                await i2.followup.send(f"✅ You left **{self._guild['name']}**.", ephemeral=True)
            @discord.ui.button(label="Cancel",style=discord.ButtonStyle.secondary)
            async def no(cv,i2,b): await i2.response.send_message("Cancelled.",ephemeral=True)
        await interaction.response.send_message("Leave guild?", view=ConfirmLeave(), ephemeral=True)


# ── Cog ─────────────────────────────────────────────────────────────
class GuildSystemCog(commands.Cog, name="GuildSystem"):
    def __init__(self, bot): self.bot = bot

    @commands.hybrid_group(name="guild", invoke_without_command=True,
                           description="RPG Guild system")
    async def guild_cmd(self, ctx):
        """Show your guild panel or prompt to create/join one."""
        gid, uid = ctx.guild.id, ctx.author.id
        my_guild = await get_guild_by_user(gid, uid)
        if not my_guild:
            return await ctx.send(embed=discord.Embed(
                description=(
                    "You're not in a guild!\n\n"
                    f"• `/guild create` — Found your own (requires level {GUILD_CREATE_LEVEL})\n"
                    "• `/guild join <name>` — Request to join (leader must `/guild invite` you)\n"
                    "• `/guild list` — Browse all guilds on this server"
                ),
                color=C_INFO,
            ))
        # Fetch member count
        mc = await db.pool.fetchval("SELECT COUNT(*) FROM rpg_guild_members WHERE guild_id=$1", my_guild["id"])
        my_guild["member_count"] = mc or 0
        view = GuildPanelView(ctx, my_guild)
        await ctx.send(embed=_guild_embed(my_guild, ctx.guild), view=view)

    @guild_cmd.command(name="create", description="Create a new guild")
    async def guild_create(self, ctx):
        modal = GuildCreateModal(ctx)
        if hasattr(ctx, "interaction") and ctx.interaction:
            await ctx.interaction.response.send_modal(modal)
        else:
            await ctx.author.send("Use `/guild create` (slash command) to open the creation form.")

    @guild_cmd.command(name="info", description="View info about a guild")
    async def guild_info(self, ctx, name: str):
        g = await get_guild_by_name(ctx.guild.id, name)
        if not g:
            return await _err(ctx, f"No guild named **{name}** found.")
        mc = await db.pool.fetchval("SELECT COUNT(*) FROM rpg_guild_members WHERE guild_id=$1", g["id"])
        g["member_count"] = mc or 0
        await ctx.send(embed=_guild_embed(g, ctx.guild))

    @guild_cmd.command(name="list", description="List all guilds on this server")
    async def guild_list(self, ctx):
        rows = await db.pool.fetch(
            "SELECT * FROM rpg_guilds WHERE server_id=$1 ORDER BY honor_points DESC LIMIT 15",
            ctx.guild.id
        )
        if not rows:
            return await ctx.send(embed=discord.Embed(description="No guilds yet. `/guild create` to be first!", color=C_INFO))
        medals = ["🥇","🥈","🥉"]
        lines  = []
        for i,r in enumerate(rows):
            tier = _guild_tier(r["honor_points"])
            lines.append(
                f"{medals[i] if i<3 else f'`{i+1}`'} **{r['name']}** — "
                f"{tier} · {r['honor_points']:,} HP · {r['gvg_wins']}W/{r['gvg_losses']}L"
            )
        e = discord.Embed(title="⚔️ Guild Rankings", description="\n".join(lines), color=C_INFO)
        e.set_footer(text="HP = Honor Points")
        await ctx.send(embed=e)

    @guild_cmd.command(name="invite", description="Invite a member to your guild")
    async def guild_invite(self, ctx, member: discord.Member):
        gid, uid = ctx.guild.id, ctx.author.id
        my_guild = await get_guild_by_user(gid, uid)
        if not my_guild or my_guild["leader_id"] != uid:
            return await _err(ctx, "You must be a guild leader to invite members.")
        target_guild = await get_guild_by_user(gid, member.id)
        if target_guild:
            return await _err(ctx, f"{member.display_name} is already in **{target_guild['name']}**.")
        mc = await db.pool.fetchval("SELECT COUNT(*) FROM rpg_guild_members WHERE guild_id=$1", my_guild["id"])
        if (mc or 0) >= GUILD_MAX_MEMBERS:
            return await _err(ctx, f"Guild is full ({GUILD_MAX_MEMBERS} members max).")

        # Send invite to target
        class InviteView(discord.ui.View):
            def __init__(iv): super().__init__(timeout=120)
            @discord.ui.button(label="✅ Accept Invite",style=discord.ButtonStyle.success)
            async def accept(iv,inter,btn):
                if inter.user.id != member.id:
                    return await inter.response.send_message("Not for you.",ephemeral=True)
                await db.pool.execute("""
                    INSERT INTO rpg_guild_members (guild_id,user_id,role,joined_at)
                    VALUES ($1,$2,'member',NOW())
                    ON CONFLICT DO NOTHING
                """, my_guild["id"], member.id)
                await db.pool.execute("UPDATE rpg_guilds SET member_count=member_count+1 WHERE id=$1", my_guild["id"])
                for c in iv.children: c.disabled=True
                await inter.response.edit_message(
                    embed=discord.Embed(description=f"✅ {member.mention} joined **{my_guild['name']}**!",color=C_SUCCESS),
                    view=iv,
                )
            @discord.ui.button(label="❌ Decline",style=discord.ButtonStyle.secondary)
            async def decline(iv,inter,btn):
                if inter.user.id != member.id:
                    return await inter.response.send_message("Not for you.",ephemeral=True)
                for c in iv.children: c.disabled=True
                await inter.response.edit_message(
                    embed=discord.Embed(description=f"❌ {member.display_name} declined.",color=C_WARN),
                    view=iv,
                )

        e = discord.Embed(
            title=f"🏛️ Guild Invite: {my_guild['name']}",
            description=(
                f"{ctx.author.mention} is inviting {member.mention} to join **{my_guild['name']}**!\n"
                f"Honor: {my_guild['honor_points']:,} · Tier: {_guild_tier(my_guild['honor_points'])}"
            ),
            color=C_INFO,
        )
        await ctx.send(embed=e, view=InviteView())

    @guild_cmd.command(name="kick", description="Kick a member from your guild")
    async def guild_kick(self, ctx, member: discord.Member):
        gid, uid = ctx.guild.id, ctx.author.id
        my_guild = await get_guild_by_user(gid, uid)
        if not my_guild or my_guild["leader_id"] != uid:
            return await _err(ctx, "Only guild leaders can kick members.")
        if member.id == uid:
            return await _err(ctx, "You can't kick yourself.")
        await db.pool.execute(
            "DELETE FROM rpg_guild_members WHERE guild_id=$1 AND user_id=$2",
            my_guild["id"], member.id
        )
        await db.pool.execute("UPDATE rpg_guilds SET member_count=GREATEST(0,member_count-1) WHERE id=$1", my_guild["id"])
        await ctx.send(embed=discord.Embed(description=f"⚔️ {member.mention} was removed from **{my_guild['name']}**.", color=C_WARN))

    @guild_cmd.command(name="war", description="Challenge another guild to GvG combat")
    async def guild_war(self, ctx, guild_name: str):
        gid, uid = ctx.guild.id, ctx.author.id
        my_guild = await get_guild_by_user(gid, uid)
        if not my_guild or my_guild["leader_id"] != uid:
            return await _err(ctx, "Only guild leaders can declare war.")
        target = await get_guild_by_name(gid, guild_name)
        if not target:
            return await _err(ctx, f"No guild named **{guild_name}**.")
        if target["id"] == my_guild["id"]:
            return await _err(ctx, "You can't war yourself.")

        # Check GvG cooldown
        last_gvg = my_guild.get("last_gvg")
        if last_gvg:
            if not hasattr(last_gvg,"tzinfo") or last_gvg.tzinfo is None:
                last_gvg = last_gvg.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc)-last_gvg).total_seconds()
            if elapsed < GVG_COOLDOWN_HOURS*3600:
                rem = int(GVG_COOLDOWN_HOURS*3600 - elapsed)
                return await _err(ctx, f"GvG on cooldown. Available in **{rem//3600}h {(rem%3600)//60}m**.")

        target_leader = ctx.guild.get_member(target["leader_id"])
        if not target_leader:
            return await _err(ctx, f"Can't find the leader of **{guild_name}**.")

        e = discord.Embed(
            title=f"⚔️ Guild War Challenge!",
            description=(
                f"**{my_guild['name']}** challenges **{target['name']}** to battle!\n\n"
                f"Power: **{await _guild_power(my_guild['id']):,}** vs ???\n"
                f"Winner gets **+{HONOR_PER_GVG_WIN}** honor points!\n\n"
                f"{target_leader.mention} — do you accept?"
            ),
            color=0xe74c3c,
        )
        view = GvGView(my_guild, target, target_leader)
        await ctx.send(embed=e, view=view)

    @guild_cmd.command(name="control", description="Spend honor to control a dungeon tier")
    async def guild_control(self, ctx, tier: int):
        gid, uid = ctx.guild.id, ctx.author.id
        my_guild = await get_guild_by_user(gid, uid)
        if not my_guild or my_guild["leader_id"] != uid:
            return await _err(ctx, "Only guild leaders can control dungeons.")
        if not (1 <= tier <= 5):
            return await _err(ctx, "Dungeon tier must be 1–5.")
        honor = my_guild.get("honor_points",0)
        if honor < DUNGEON_CONTROL_COST:
            return await _err(ctx, f"Need **{DUNGEON_CONTROL_COST:,}** honor but have **{honor:,}**.")
        tier_name = f"Tier {tier}"
        controlled = set((my_guild.get("controlled_dungeons","") or "").split(","))
        controlled.discard("")
        controlled.add(tier_name)
        await db.pool.execute("""
            UPDATE rpg_guilds
            SET honor_points=honor_points-$1, controlled_dungeons=$2
            WHERE id=$3
        """, DUNGEON_CONTROL_COST, ",".join(controlled), my_guild["id"])
        await ctx.send(embed=discord.Embed(
            description=(
                f"🏰 **{my_guild['name']}** now controls **{tier_name}** dungeons!\n"
                f"Members get +10% loot bonus in Tier {tier} dungeons.\n"
                f"Spent {DUNGEON_CONTROL_COST:,} honor · {honor-DUNGEON_CONTROL_COST:,} remaining."
            ),
            color=C_SUCCESS,
        ))

    @guild_cmd.command(name="setdesc", description="Update your guild description")
    async def guild_setdesc(self, ctx, *, description: str):
        gid, uid = ctx.guild.id, ctx.author.id
        my_guild = await get_guild_by_user(gid, uid)
        if not my_guild or my_guild["leader_id"] != uid:
            return await _err(ctx, "Only guild leaders can change the description.")
        await db.pool.execute("UPDATE rpg_guilds SET description=$1 WHERE id=$2", description[:200], my_guild["id"])
        await ctx.send(embed=discord.Embed(description="✅ Guild description updated!", color=C_SUCCESS), delete_after=5)

    @guild_cmd.command(name="disband", description="Dissolve your guild (irreversible)")
    async def guild_disband(self, ctx):
        gid, uid = ctx.guild.id, ctx.author.id
        my_guild = await get_guild_by_user(gid, uid)
        if not my_guild or my_guild["leader_id"] != uid:
            return await _err(ctx, "Only the guild leader can disband.")
        class ConfirmDisband(discord.ui.View):
            def __init__(cv): super().__init__(timeout=30)
            @discord.ui.button(label="⚠️ Yes, disband",style=discord.ButtonStyle.danger)
            async def yes(cv,i2,b):
                await db.pool.execute("DELETE FROM rpg_guild_members WHERE guild_id=$1", my_guild["id"])
                await db.pool.execute("DELETE FROM rpg_guilds WHERE id=$1", my_guild["id"])
                for c in cv.children: c.disabled=True
                await i2.response.edit_message(
                    embed=discord.Embed(description=f"💔 **{my_guild['name']}** has been disbanded.",color=C_ERROR),
                    view=cv,
                )
            @discord.ui.button(label="Cancel",style=discord.ButtonStyle.secondary)
            async def no(cv,i2,b): await i2.response.send_message("Cancelled.",ephemeral=True)
        await ctx.send(
            embed=discord.Embed(title="⚠️ Disband Guild?",
                                 description=f"This will permanently delete **{my_guild['name']}** and remove all members.",
                                 color=C_ERROR),
            view=ConfirmDisband(),
        )
