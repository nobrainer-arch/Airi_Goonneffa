# airi/hub.py — Consolidated command hub
# Merges: balance/pay/give/shop/daily/work/crime → /economy
# Merges: profile/rep/claim/waifu/mywaifu/rel → /social  
# Merges: leaderboard (all categories) → /lb  
# Merges: inventory/shop → /shop (all types)
# /rpg stays as-is (already grouped)
# /guild, /dungeon, /market stay as-is
# Actio commands stay individual (user request)
import discord
from discord.ext import commands
from datetime import datetime, timezone
import db
from utils import _err, C_INFO, C_SUCCESS, C_WARN, C_ECONOMY, C_SOCIAL, C_GACHA


# ════════════════════════════════════════════════════════════════════
#  /economy — balance, pay, give, daily, work, crime all in one
# ════════════════════════════════════════════════════════════════════
class EconomyHub(commands.Cog, name="EconomyHub"):
    def __init__(self, bot): self.bot = bot

    @commands.hybrid_command(
        name="economy", aliases=["eco","wallet","bal","balance","daily","work","crime","pay","give"],
        description="Economy hub — balance, daily, work, pay, give"
    )
    async def economy(self, ctx, member: discord.Member = None, amount: int = None):
        """Open the economy hub. All economy actions in one message."""
        from airi.daily_panel import open_daily_panel

        if member and amount:
            # Quick pay: !economy @user 500
            from airi.economy import EconomyCog
            cog = ctx.bot.cogs.get("Economy")
            if cog: return await cog.pay(ctx, member=member, amount=amount)

        # Open the full economy panel
        await open_daily_panel(ctx)


# ════════════════════════════════════════════════════════════════════
#  /social — profile, rep, waifu, claim, release, relationships
# ════════════════════════════════════════════════════════════════════
class SocialHub(commands.Cog, name="SocialHub"):
    def __init__(self, bot): self.bot = bot

    @commands.hybrid_command(
        name="social", aliases=["profile","pf","rep","waifu","mywaifu","claim","myrel"],
        description="Social hub — profile, rep, waifu, relationships"
    )
    async def social(self, ctx, member: discord.Member = None):
        """Open the social hub for yourself or view another member."""
        from airi.social import SocialCog
        cog = ctx.bot.cogs.get("Social")
        if cog:
            target = member or ctx.author
            return await cog.profile(ctx, member=target)


# ════════════════════════════════════════════════════════════════════
#  /lb — unified leaderboard, all categories via dropdown
# ════════════════════════════════════════════════════════════════════
class LeaderboardHub(commands.Cog, name="LeaderboardHub"):
    def __init__(self, bot): self.bot = bot

    @commands.hybrid_command(
        name="lb", aliases=["leaderboard","top","rank"],
        description="Server leaderboards — XP, coins, hugs, RPG power, and more"
    )
    async def lb(self, ctx, category: str = "xp"):
        from airi.leaderboard import LB_CATEGORIES, _build_lb
        cat = category.lower()
        if cat not in LB_CATEGORIES: cat = "xp"
        e = await _build_lb(ctx.guild, cat)

        # Build dropdown with all categories
        opts = [
            discord.SelectOption(
                label=v[0][:50], value=k, default=(k == cat),
                emoji={"xp":"📈","coins":"💰","rep":"⭐","hugs":"🤗","kisses":"💋",
                       "pats":"🤚","rpg":"⚔️","marriage":"💍","waifuscore":"👑",
                       "proposals":"💌"}.get(k,"📊")
            )
            for k, v in LB_CATEGORIES.items()
        ]
        sel = discord.ui.Select(placeholder="📊 Change leaderboard category…", options=opts)
        async def sel_cb(inter):
            new_e = await _build_lb(inter.guild, sel.values[0])
            for o in sel.options: o.default = (o.value == sel.values[0])
            await inter.response.edit_message(embed=new_e, view=view)
        sel.callback = sel_cb

        class LBView(discord.ui.View):
            def __init__(lv): super().__init__(timeout=300); lv.add_item(sel)
        view = LBView()
        await ctx.send(embed=e, view=view)


# ════════════════════════════════════════════════════════════════════
#  /server — server stats, roles, config, setup
# ════════════════════════════════════════════════════════════════════
class ServerHub(commands.Cog, name="ServerHub"):
    def __init__(self, bot): self.bot = bot

    @commands.hybrid_command(name="server", aliases=["serverinfo","si"],
                             description="Server info and configuration hub")
    async def server(self, ctx):
        g = ctx.guild
        e = discord.Embed(title=f"🌐 {g.name}", color=C_INFO, timestamp=datetime.now(timezone.utc))
        e.set_thumbnail(url=g.icon.url if g.icon else None)
        e.add_field(name="👥 Members", value=f"{g.member_count:,}", inline=True)
        e.add_field(name="📅 Created",  value=discord.utils.format_dt(g.created_at,"R"), inline=True)
        e.add_field(name="👑 Owner",    value=g.owner.mention if g.owner else "?", inline=True)
        e.add_field(name="📝 Channels", value=f"{len(g.text_channels)} text · {len(g.voice_channels)} voice", inline=True)
        e.add_field(name="🎭 Roles",    value=str(len(g.roles)), inline=True)

        # Setup button
        setup_btn = discord.ui.Button(label="⚙️ Run Setup", style=discord.ButtonStyle.primary)
        config_btn = discord.ui.Button(label="📋 View Config", style=discord.ButtonStyle.secondary)

        async def setup_cb(inter):
            from airi.setup import SetupCog
            cog = inter.client.cogs.get("Setup")
            if cog and inter.user.guild_permissions.manage_guild:
                class FC:
                    guild=inter.guild; author=inter.user; bot=inter.client
                    async def send(s,*a,**kw): return await inter.followup.send(*a,**kw)
                await inter.response.defer()
                await cog.setup(FC())
            else:
                await inter.response.send_message("Need **Manage Server** permission.", ephemeral=True)

        async def config_cb(inter):
            from airi.guild_config import GuildConfigCog
            cog = inter.client.cogs.get("Config")
            if cog:
                await inter.response.defer(ephemeral=True)
                class FC:
                    guild=inter.guild; author=inter.user; bot=inter.client
                    async def send(s,*a,**kw): return await inter.followup.send(*a,ephemeral=True,**kw)
                await cog.cfg_show(FC())
            else:
                await inter.response.send_message("Config unavailable.", ephemeral=True)

        setup_btn.callback = setup_cb
        config_btn.callback = config_cb
        v = discord.ui.View(timeout=120)
        v.add_item(setup_btn); v.add_item(config_btn)
        await ctx.send(embed=e, view=v)
