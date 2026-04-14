# goonneffa/commands.py — Uses live Klipy search, no watch conflict, guided no-arg UI
import discord
from discord.ext import commands
import random
import db
from utils import _err, C_SOCIAL, C_ERROR, C_INFO

WATCH_QUERY = "anime watching stare"
SPY_QUERY   = "anime spy sneaking peek"

async def _search_gif(query: str) -> str:
    try:
        from airi.gif_provider import klipy_search
        urls = await klipy_search(query, 8)
        return random.choice(urls) if urls else ""
    except Exception:
        return ""

class GoonneffaCog(commands.Cog, name="Goonneffa"):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="gwatch", aliases=["glare"], description="Watch someone — anime style")
    async def gwatch(self, ctx, target: discord.Member = None):
        gif = await _search_gif(WATCH_QUERY)
        desc = (f"👀 **{ctx.author.display_name}** is watching **{target.display_name}**"
                if target else f"👀 **{ctx.author.display_name}** is watching...")
        e = discord.Embed(description=desc, color=C_SOCIAL)
        if gif: e.set_image(url=gif)
        await ctx.send(embed=e)

    @commands.hybrid_command(name="gspy", description="Spy on someone — anime style")
    async def gspy(self, ctx, target: discord.Member = None):
        gif = await _search_gif(SPY_QUERY)
        desc = (f"🕵️ **{ctx.author.display_name}** is spying on **{target.display_name}**..."
                if target else f"🕵️ **{ctx.author.display_name}** is spying...")
        e = discord.Embed(description=desc, color=C_SOCIAL)
        if gif: e.set_image(url=gif)
        await ctx.send(embed=e)

    @commands.hybrid_command(name="gifsearch", aliases=["gsearch"], description="Search for a GIF")
    async def gifsearch(self, ctx, *, query: str = None):
        if query is None:
            class QModal(discord.ui.Modal, title="Search GIFs"):
                query_in = discord.ui.TextInput(label="Search query", placeholder="anime dance…", required=True)
                async def on_submit(m, inter):
                    await inter.response.defer()
                    await _do_gifsearch(inter, m.query_in.value)
            if hasattr(ctx, "send_modal"):
                return await ctx.send_modal(QModal())
            return await ctx.send("Usage: `g!gifsearch <query>`", delete_after=8)
        await _do_gifsearch(ctx, query)

    @commands.hybrid_command(name="rpblock", description="Block/unblock someone from RP actions")
    async def rpblock(self, ctx, member: discord.Member = None):
        gid, uid = ctx.guild.id, ctx.author.id
        if member is None:
            rows = await db.pool.fetch("SELECT blocked_id FROM rpblock WHERE guild_id=$1 AND user_id=$2", gid, uid)
            blocked = [ctx.guild.get_member(r["blocked_id"]) for r in rows]
            blocked = [m for m in blocked if m]
            desc = "**Blocked from RP:** " + ", ".join(m.display_name for m in blocked) if blocked else "*No one blocked.*"
            class RPView(discord.ui.View):
                def __init__(self_): super().__init__(timeout=120)
                @discord.ui.button(label="➕ Add Block", style=discord.ButtonStyle.danger)
                async def add(self_, inter, btn):
                    if inter.user.id != uid: return await inter.response.send_message("Not for you.", ephemeral=True)
                    sel = discord.ui.UserSelect(placeholder="Block from RP…")
                    async def cb(i2):
                        t = sel.values[0]
                        if t.bot: return await i2.response.send_message("Can't block bots.", ephemeral=True)
                        await db.pool.execute("INSERT INTO rpblock VALUES ($1,$2,$3) ON CONFLICT DO NOTHING", gid, uid, t.id)
                        for i in v.children: i.disabled = True
                        await i2.response.edit_message(content=f"✅ Blocked **{t.display_name}** from RP.", view=v)
                    sel.callback = cb
                    class v(discord.ui.View):
                        def __init__(vs): super().__init__(timeout=60); vs.add_item(sel)
                    await inter.response.send_message("Block from RP:", view=v(), ephemeral=True)
                @discord.ui.button(label="➖ Remove", style=discord.ButtonStyle.secondary)
                async def rem(self_, inter, btn):
                    if inter.user.id != uid: return await inter.response.send_message("Not for you.", ephemeral=True)
                    if not blocked: return await inter.response.send_message("No one blocked.", ephemeral=True)
                    opts = [discord.SelectOption(label=m.display_name, value=str(m.id)) for m in blocked]
                    sel2 = discord.ui.Select(placeholder="Unblock…", options=opts)
                    async def cb2(i2):
                        await db.pool.execute("DELETE FROM rpblock WHERE guild_id=$1 AND user_id=$2 AND blocked_id=$3", gid, uid, int(sel2.values[0]))
                        for i in v2.children: i.disabled = True
                        await i2.response.edit_message(content="✅ Unblocked.", view=v2)
                    sel2.callback = cb2
                    class v2(discord.ui.View):
                        def __init__(vs): super().__init__(timeout=60); vs.add_item(sel2)
                    await inter.response.send_message("Unblock:", view=v2(), ephemeral=True)
            return await ctx.send(embed=discord.Embed(description=desc, color=C_SOCIAL), view=RPView())
        exist = await db.pool.fetchrow("SELECT 1 FROM rpblock WHERE guild_id=$1 AND user_id=$2 AND blocked_id=$3", gid, uid, member.id)
        if exist:
            await db.pool.execute("DELETE FROM rpblock WHERE guild_id=$1 AND user_id=$2 AND blocked_id=$3", gid, uid, member.id)
            await ctx.send(embed=discord.Embed(description=f"✅ **{member.display_name}** unblocked from RP.", color=0x2ecc71))
        else:
            await db.pool.execute("INSERT INTO rpblock VALUES ($1,$2,$3) ON CONFLICT DO NOTHING", gid, uid, member.id)
            await ctx.send(embed=discord.Embed(description=f"✅ **{member.display_name}** blocked from RP.", color=0x2ecc71))

async def _do_gifsearch(ctx_or_inter, query: str):
    from airi.gif_provider import klipy_search
    urls = await klipy_search(query, 10)
    if not urls:
        msg = f"❌ No GIFs found for **{query}**."
        return await (ctx_or_inter.followup.send(msg) if hasattr(ctx_or_inter, "followup") else ctx_or_inter.send(msg))
    current = [0]
    author_id = getattr(getattr(ctx_or_inter, "author", None), "id", None) or getattr(getattr(ctx_or_inter, "user", None), "id", None)
    def _emb():
        e = discord.Embed(title=f"🔍 {query}", color=C_SOCIAL)
        e.set_image(url=urls[current[0]])
        e.set_footer(text=f"Result {current[0]+1}/{len(urls)} · Lock to keep")
        return e
    class GSView(discord.ui.View):
        def __init__(self_):
            super().__init__(timeout=300); self_._upd()
        def _upd(self_):
            self_.prev.disabled = current[0] == 0
            self_.nxt.disabled  = current[0] == len(urls) - 1
        @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
        async def prev(self_, inter, btn):
            if inter.user.id != author_id: return await inter.response.send_message("Not for you.", ephemeral=True)
            current[0] -= 1; self_._upd(); await inter.response.edit_message(embed=_emb(), view=self_)
        @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
        async def nxt(self_, inter, btn):
            if inter.user.id != author_id: return await inter.response.send_message("Not for you.", ephemeral=True)
            current[0] += 1; self_._upd(); await inter.response.edit_message(embed=_emb(), view=self_)
        @discord.ui.button(label="🔒 Lock", style=discord.ButtonStyle.success)
        async def lock(self_, inter, btn):
            if inter.user.id != author_id: return await inter.response.send_message("Not for you.", ephemeral=True)
            e = _emb(); e.set_footer(text=f"🔒 Locked · {inter.user.display_name}")
            await inter.response.edit_message(embed=e, view=None); self_.stop()
    v = GSView()
    if hasattr(ctx_or_inter, "followup"):
        await ctx_or_inter.followup.send(embed=_emb(), view=v)
    else:
        await ctx_or_inter.send(embed=_emb(), view=v)
