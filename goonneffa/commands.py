# goonneffa/commands.py — Goonneffa moderation + fun commands
import discord
from discord.ext import commands
import random, asyncio
import config, db
from utils import _err, C_SOCIAL, C_ERROR

WATCH_QUERIES = ["anime watching", "anime look", "anime observe", "anime stare"]
SPY_QUERIES   = ["anime spy", "anime sneak peek", "anime hiding watching", "anime peek"]

async def _get_gif(query: str) -> str:
    """Get GIF via gif_provider's klipy_search."""
    try:
        from airi.gif_provider import klipy_search
        urls = await klipy_search(query, 5)
        return random.choice(urls) if urls else ""
    except Exception:
        return ""


class GoonneffaCog(commands.Cog, name="Goonneffa"):
    def __init__(self, bot): self.bot = bot

    @commands.hybrid_command(name="watch", description="Watch someone — anime style")
    async def watch(self, ctx, target: discord.Member = None):
        if target is None:
            gif = await _get_gif(random.choice(WATCH_QUERIES))
            e = discord.Embed(description=f"👀 **{ctx.author.display_name}** is watching...", color=C_SOCIAL)
            if gif: e.set_image(url=gif)
            return await ctx.send(embed=e)
        gif = await _get_gif(random.choice(WATCH_QUERIES))
        e = discord.Embed(
            description=f"👀 **{ctx.author.display_name}** is watching **{target.display_name}**",
            color=C_SOCIAL
        )
        if gif: e.set_image(url=gif)
        await ctx.send(embed=e)

    @commands.hybrid_command(name="spy", description="Spy on someone — anime style")
    async def spy(self, ctx, target: discord.Member = None):
        if target is None:
            gif = await _get_gif(random.choice(SPY_QUERIES))
            e = discord.Embed(description=f"🕵️ **{ctx.author.display_name}** is spying...", color=C_SOCIAL)
            if gif: e.set_image(url=gif)
            return await ctx.send(embed=e)
        gif = await _get_gif(random.choice(SPY_QUERIES))
        e = discord.Embed(
            description=f"🕵️ **{ctx.author.display_name}** is spying on **{target.display_name}**...",
            color=C_SOCIAL
        )
        if gif: e.set_image(url=gif)
        await ctx.send(embed=e)

    @commands.hybrid_command(name="warn", description="Warn a member")
    @commands.has_permissions(moderate_members=True)
    async def warn(self, ctx, member: discord.Member, *, reason: str = "No reason given"):
        if member.top_role >= ctx.author.top_role and not ctx.author.guild_permissions.administrator:
            return await _err(ctx, "You can't warn someone with equal or higher role.")
        e = discord.Embed(
            title="⚠️ Warning Issued",
            description=f"**{member.display_name}** has been warned.\n**Reason:** {reason}",
            color=0xf39c12
        )
        await ctx.send(embed=e)
        try:
            await member.send(embed=discord.Embed(
                title=f"⚠️ Warning in {ctx.guild.name}",
                description=f"**Reason:** {reason}",
                color=0xf39c12
            ))
        except Exception: pass

    @commands.hybrid_command(name="timeout", description="Timeout a member")
    @commands.has_permissions(moderate_members=True)
    async def timeout_cmd(self, ctx, member: discord.Member, minutes: int = 10, *, reason: str = "No reason"):
        if minutes < 1 or minutes > 40320:
            return await _err(ctx, "Timeout must be 1–40320 minutes.")
        from datetime import timedelta
        try:
            await member.timeout(timedelta(minutes=minutes), reason=reason)
            e = discord.Embed(
                description=f"🔇 **{member.display_name}** timed out for **{minutes}m**.\n**Reason:** {reason}",
                color=0xe74c3c
            )
            await ctx.send(embed=e)
        except discord.Forbidden:
            await _err(ctx, "I don't have permission to timeout that member.")

    @commands.hybrid_command(name="kick", description="Kick a member")
    @commands.has_permissions(kick_members=True)
    async def kick(self, ctx, member: discord.Member, *, reason: str = "No reason"):
        try:
            await member.kick(reason=reason)
            await ctx.send(embed=discord.Embed(
                description=f"👢 **{member.display_name}** was kicked.\n**Reason:** {reason}",
                color=0xe74c3c
            ))
        except discord.Forbidden:
            await _err(ctx, "I can't kick that member.")

    @commands.hybrid_command(name="ban", description="Ban a member")
    @commands.has_permissions(ban_members=True)
    async def ban(self, ctx, member: discord.Member, *, reason: str = "No reason"):
        try:
            await member.ban(reason=reason)
            await ctx.send(embed=discord.Embed(
                description=f"🔨 **{member.display_name}** was banned.\n**Reason:** {reason}",
                color=0xe74c3c
            ))
        except discord.Forbidden:
            await _err(ctx, "I can't ban that member.")

    @commands.hybrid_command(name="rpblock", description="Block someone from RP actions")
    async def rpblock(self, ctx, member: discord.Member = None):
        if member is None:
            # Show block list management UI
            gid = ctx.guild.id
            uid = ctx.author.id
            rows = await db.pool.fetch(
                "SELECT blocked_id FROM rpblock WHERE guild_id=$1 AND user_id=$2", gid, uid
            )
            blocked = [ctx.guild.get_member(r["blocked_id"]) for r in rows]
            blocked = [m for m in blocked if m]
            desc = "**Blocked from RP:** " + ", ".join(m.display_name for m in blocked) if blocked else "*No one blocked.*"

            class RPBlockView(discord.ui.View):
                def __init__(self_): super().__init__(timeout=120)
                @discord.ui.button(label="Add Block", style=discord.ButtonStyle.danger)
                async def add(self_, inter, btn):
                    if inter.user.id != uid: return await inter.response.send_message("Not for you.", ephemeral=True)
                    sel = discord.ui.UserSelect(placeholder="Select to block…")
                    async def cb(i2):
                        target = sel.values[0]
                        await db.pool.execute(
                            "INSERT INTO rpblock (guild_id,user_id,blocked_id) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
                            gid, uid, target.id
                        )
                        for i in v.children: i.disabled = True
                        await i2.response.edit_message(content=f"✅ Blocked **{target.display_name}** from RP.", view=v)
                    sel.callback = cb
                    class v(discord.ui.View):
                        def __init__(self__): super().__init__(timeout=60); self__.add_item(sel)
                    await inter.response.send_message("Block from RP:", view=v(), ephemeral=True)
                @discord.ui.button(label="Remove Block", style=discord.ButtonStyle.secondary)
                async def rem(self_, inter, btn):
                    if inter.user.id != uid: return await inter.response.send_message("Not for you.", ephemeral=True)
                    if not blocked: return await inter.response.send_message("No one blocked.", ephemeral=True)
                    opts = [discord.SelectOption(label=m.display_name, value=str(m.id)) for m in blocked]
                    sel2 = discord.ui.Select(placeholder="Select to unblock…", options=opts)
                    async def cb2(i2):
                        mid = int(sel2.values[0])
                        await db.pool.execute("DELETE FROM rpblock WHERE guild_id=$1 AND user_id=$2 AND blocked_id=$3", gid, uid, mid)
                        for i in v2.children: i.disabled = True
                        await i2.response.edit_message(content="✅ Unblocked.", view=v2)
                    sel2.callback = cb2
                    class v2(discord.ui.View):
                        def __init__(self__): super().__init__(timeout=60); self__.add_item(sel2)
                    await inter.response.send_message("Unblock from RP:", view=v2(), ephemeral=True)

            await ctx.send(embed=discord.Embed(description=desc, color=C_SOCIAL), view=RPBlockView())
            return
        # Direct block
        gid, uid = ctx.guild.id, ctx.author.id
        await db.pool.execute(
            "INSERT INTO rpblock (guild_id,user_id,blocked_id) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
            gid, uid, member.id
        )
        await ctx.send(embed=discord.Embed(
            description=f"✅ **{member.display_name}** blocked from RP actions.",
            color=0x2ecc71
        ))
