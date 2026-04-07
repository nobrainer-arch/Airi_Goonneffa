# airi/commands.py
# GIF commands: UserSelect pickers, back buttons that disable after use,
# NSFW consent, RPBlock, gifsearch command
import discord
from discord.ext import commands
import random
import asyncio
from datetime import datetime
import config
import actio
import db
from utils import _err, C_INFO
from airi.guild_config import check_channel
from airi.gif_provider import get_gif, klipy_search

# ── NSFW command set ──────────────────────────────────────────────
NSFW_COMMANDS = {
    "blowjob","suck","pussyeat","titjob","fuck","dickride","bfuck",
    "anal","bathroomfuck","cum","69","threesome","gangbang","fap",
    "lick","grind","spank","bondage","grabbutts","grabboobs","kuni",
    "crym","cumm","fapm","finger","feet","bang",
}

# ── Back-action buttons — imported from config ────────────────────
BACK_ACTIONS = config.BACK_ACTIONS

# ── Empty-use rate limiter ────────────────────────────────────────
_EMPTY_WINDOW = 600
_empty: dict[str, tuple[int, float]] = {}

def _check_empty(uid: str) -> bool:
    now = datetime.utcnow().timestamp()
    c, ws = _empty.get(uid, (0, now))
    if now - ws > _EMPTY_WINDOW: c, ws = 0, now
    c += 1; _empty[uid] = (c, ws)
    return c > config.EMPTY_LIMIT

def _reset_empty(uid: str): _empty.pop(uid, None)

# ── Antinoobify DB helpers ────────────────────────────────────────
async def _load_msgs():
    rows = await db.pool.fetch("SELECT guild_id, channel_id, message_id FROM antinoobify_messages")
    return [dict(r) for r in rows]
async def _add_msg(gid, cid, mid):
    await db.pool.execute("INSERT INTO antinoobify_messages (message_id,guild_id,channel_id) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING", mid, gid, cid)
async def _del_msg(mid):
    await db.pool.execute("DELETE FROM antinoobify_messages WHERE message_id=$1", mid)

# ── Consent checks ────────────────────────────────────────────────
async def _nsfw_consent_check(ctx, target: discord.Member) -> bool:
    gid = ctx.guild.id
    if target == ctx.guild.me:
        await ctx.send("you can't do that to me uwu 🥺", delete_after=8); return True
    if await db.pool.fetchval("SELECT 1 FROM nsfw_optout WHERE guild_id=$1 AND user_id=$2", gid, target.id):
        await _err(ctx, f"{target.display_name} has opted out of NSFW commands 🔒"); return True
    if await db.pool.fetchval("SELECT 1 FROM rpblock WHERE guild_id=$1 AND user_id=$2 AND blocked_id=$3", gid, target.id, ctx.author.id):
        await _err(ctx, f"{target.display_name} has blocked you from RP commands."); return True
    rel = await db.pool.fetchrow("""
        SELECT user1_id, user2_id FROM relationships
        WHERE guild_id=$1 AND (user1_id=$2 OR user2_id=$2) AND status='active'
    """, gid, target.id)
    if rel:
        partner_id = rel["user2_id"] if rel["user1_id"] == target.id else rel["user1_id"]
        if await db.pool.fetchval("SELECT 1 FROM nsfw_optout WHERE guild_id=$1 AND user_id=$2", gid, partner_id):
            partner = ctx.guild.get_member(partner_id)
            pname = partner.display_name if partner else f"<@{partner_id}>"
            await _err(ctx, f"{target.display_name}'s partner **{pname}** hasn't consented."); return True
    return False

async def _sfw_rp_check(ctx, target: discord.Member) -> bool:
    gid = ctx.guild.id
    if target == ctx.guild.me:
        await ctx.send("you can't do that to me uwu 🥺", delete_after=8); return True
    if await db.pool.fetchval("SELECT 1 FROM rpblock WHERE guild_id=$1 AND user_id=$2 AND blocked_id=$3", gid, target.id, ctx.author.id):
        await _err(ctx, f"{target.display_name} has blocked you from RP commands."); return True
    return False

# ── Action text helpers ────────────────────────────────────────────
def _get_action_text(cmd: str, ag: str, tg: str | None = None) -> str:
    if tg is None:
        solo = actio.ACTIONS_SOLO.get(cmd)
        if solo: return random.choice(solo)
        actions = actio.ACTIONS.get(cmd)
        if not actions: return f"used {cmd}"
        for k in ("solo", "default"):
            if k in actions: return random.choice(actions[k])
        first = next(iter(actions.values()), None)
        return random.choice(first) if first else f"used {cmd}"
    actions = actio.ACTIONS.get(cmd)
    if not actions: return f"used {cmd} on {{target}}"
    for k in (ag+tg, "default"):
        if k in actions: return random.choice(actions[k])
    first = next(iter(actions.values()), None)
    return random.choice(first) if first else f"used {cmd} on {{target}}"

def _get_action(cmd, ag, tg=None): return _get_action_text(cmd, ag, tg)

# ── Embed builder ──────────────────────────────────────────────────
async def _make_embed(bot, ctx, description, gif_url, source, cmd) -> discord.Embed:
    title = await db.pool.fetchval(
        "SELECT active_title FROM economy WHERE guild_id=$1 AND user_id=$2",
        ctx.guild.id, ctx.author.id
    )
    e = discord.Embed(description=description, color=0x7289da, timestamp=datetime.now())
    e.set_author(name=bot.user.display_name, icon_url=bot.user.display_avatar.url)
    footer = f"{'✨ '+title+'  ·  ' if title else ''}{ctx.author.display_name}"
    tip = "  |  !rpblock @user to block" if cmd in NSFW_COMMANDS else ""
    e.set_footer(text=footer + tip)
    e.set_image(url=gif_url)
    return e

# ── Back button view (disables itself after use) ───────────────────
class BackView(discord.ui.View):
    def __init__(self, cmd: str, target_id: int, author_id: int, bot):
        super().__init__(timeout=600)
        self._cmd    = cmd
        self._target = target_id
        self._author = author_id
        self._bot    = bot
        self._used   = False
        back_label = BACK_ACTIONS[cmd][1]
        btn = discord.ui.Button(
            label=back_label,
            style=discord.ButtonStyle.secondary,
            custom_id=f"back:{cmd}:{target_id}:{author_id}"
        )
        btn.callback = self._on_back
        self.add_item(btn)

    async def _on_back(self, interaction: discord.Interaction):
        if interaction.user.id != self._target:
            await interaction.response.send_message("This button isn't for you.", ephemeral=True); return
        if self._used:
            await interaction.response.send_message("Already used.", ephemeral=True); return
        self._used = True
        # Disable the button atomically via interaction response (no separate message.edit needed)
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(view=self)
        from airi.gender import get_gender
        ag = await get_gender(str(interaction.user.id)) or "u"
        tg = await get_gender(str(self._author)) or "u"
        author_m = interaction.guild.get_member(self._author)
        is_nsfw = self._cmd in NSFW_COMMANDS
        gif_url, source = await get_gif(self._cmd, is_nsfw)
        if not gif_url:
            await interaction.followup.send("Couldn't fetch a GIF right now.", ephemeral=True); return
        raw = _get_action_text(self._cmd, ag, tg)
        action = raw.format(
            author=interaction.user.display_name,
            target=author_m.mention if author_m else f"<@{self._author}>"
        )
        title = await db.pool.fetchval(
            "SELECT active_title FROM economy WHERE guild_id=$1 AND user_id=$2",
            interaction.guild.id, interaction.user.id
        )
        e = discord.Embed(description=action, color=0x7289da, timestamp=datetime.now())
        e.set_author(name=self._bot.user.display_name, icon_url=self._bot.user.display_avatar.url)
        e.set_footer(text=f"{'✨ '+title+'  ·  ' if title else ''}{interaction.user.display_name}")
        e.set_image(url=gif_url)
        await interaction.followup.send(embed=e)
        self.stop()

# ── Recipient UserSelect picker ────────────────────────────────────
class RecipientSelect(discord.ui.UserSelect):
    def __init__(self, cmd: str, author_id: int, bot, is_nsfw: bool, max_v: int = 5):
        super().__init__(placeholder="Select one or more recipients", min_values=1, max_values=max_v)
        self._cmd     = cmd
        self._author  = author_id
        self._bot     = bot
        self._is_nsfw = is_nsfw

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self._author:
            await interaction.response.send_message("This selector isn't for you.", ephemeral=True); return
        from airi.gender import get_gender
        ag = await get_gender(str(self._author)) or "u"

        # Disable the select immediately so user sees it was received
        for item in self.view.children: item.disabled = True
        await interaction.response.edit_message(view=self.view)

        for member in self.values:
            if self._is_nsfw:
                if await db.pool.fetchval("SELECT 1 FROM nsfw_optout WHERE guild_id=$1 AND user_id=$2", interaction.guild.id, member.id):
                    await interaction.followup.send(f"{member.display_name} has opted out.", ephemeral=True); continue
            elif await db.pool.fetchval("SELECT 1 FROM rpblock WHERE guild_id=$1 AND user_id=$2 AND blocked_id=$3", interaction.guild.id, member.id, self._author):
                await interaction.followup.send(f"{member.display_name} has blocked you from RP.", ephemeral=True); continue

            tg      = await get_gender(str(member.id)) or "u"
            raw     = _get_action_text(self._cmd, ag, tg)
            action  = raw.format(author=interaction.user.display_name, target=member.mention)
            gif_url, source = await get_gif(self._cmd, self._is_nsfw)
            if not gif_url:
                await interaction.followup.send(f"Couldn't fetch a GIF for {self._cmd}.", ephemeral=True); return
            title = await db.pool.fetchval(
                "SELECT active_title FROM economy WHERE guild_id=$1 AND user_id=$2",
                interaction.guild.id, self._author
            )
            e = discord.Embed(description=action, color=0x7289da, timestamp=datetime.now())
            e.set_author(name=self._bot.user.display_name, icon_url=self._bot.user.display_avatar.url)
            tip = "  |  !rpblock @user to block" if self._is_nsfw else ""
            e.set_footer(text=f"{'✨ '+title+'  ·  ' if title else ''}{interaction.user.display_name}{tip}")
            e.set_image(url=gif_url)
            view = BackView(self._cmd, member.id, self._author, self._bot) if self._cmd in BACK_ACTIONS else None
            await interaction.followup.send(embed=e, view=view)

        self.view.stop()

class RecipientView(discord.ui.View):
    def __init__(self, cmd: str, author_id: int, bot, is_nsfw: bool):
        super().__init__(timeout=120)
        max_v = config.MULTI_TARGET_COMMANDS.get(cmd, 5)
        self.add_item(RecipientSelect(cmd, author_id, bot, is_nsfw, max_v))

# ── Pagination view ────────────────────────────────────────────────
class PageView(discord.ui.View):
    def __init__(self, pages: list[discord.Embed], bot):
        super().__init__(timeout=300)
        self._pages   = pages
        self._current = 0
        self._update_buttons()

    def _update_buttons(self):
        self.prev_btn.disabled = self._current == 0
        self.next_btn.disabled = self._current == len(self._pages) - 1

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary, custom_id="page_prev")
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._current -= 1; self._update_buttons()
        await interaction.response.edit_message(embed=self._pages[self._current], view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary, custom_id="page_next")
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._current += 1; self._update_buttons()
        await interaction.response.edit_message(embed=self._pages[self._current], view=self)

# ── Build pages for !antinoobify ─────────────────────────────────
def _build_pages(commands_data):
    sc = sorted(commands_data.keys()); ps = 15; pages = []
    total = max(1, (len(sc)-1)//ps+1)
    for i in range(0, len(sc), ps):
        chunk = sc[i:i+ps]
        e = discord.Embed(
            title="🎭 Action Commands",
            description=f"Page {len(pages)+1}/{total} · `!antinoobify <cmd>` for details",
            color=C_INFO
        )
        e.add_field(name="Commands", value="\n".join(f"`!{c}`" for c in chunk), inline=False)
        pages.append(e)
    return pages

# ── Main setup function ────────────────────────────────────────────
def setup_commands(bot, commands_data):

    @bot.command()
    async def ping(ctx):
        await ctx.send("🏓 pong")

    @bot.command(aliases=["rpblock_user"])
    async def rpblock(ctx, member: discord.Member = None):
        if not member: return await _err(ctx, "Usage: `!rpblock @user`")
        gid, uid = ctx.guild.id, ctx.author.id
        exists = await db.pool.fetchval("SELECT 1 FROM rpblock WHERE guild_id=$1 AND user_id=$2 AND blocked_id=$3", gid, uid, member.id)
        if exists:
            await db.pool.execute("DELETE FROM rpblock WHERE guild_id=$1 AND user_id=$2 AND blocked_id=$3", gid, uid, member.id)
            await ctx.send(f"✅ Unblocked **{member.display_name}** from RP commands.", delete_after=8)
        else:
            await db.pool.execute("INSERT INTO rpblock (guild_id,user_id,blocked_id) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING", gid, uid, member.id)
            await ctx.send(f"✅ Blocked **{member.display_name}** from using RP/GIF commands on you.", delete_after=8)

    @bot.command(aliases=["setgender", "rpgender"])
    async def gender(ctx):
        class GenderView(discord.ui.View):
            def __init__(self_): super().__init__(timeout=60)

            @discord.ui.button(label="Male", style=discord.ButtonStyle.primary, emoji="♂️")
            async def male(self_, interaction, button):
                if interaction.user.id != ctx.author.id:
                    return await interaction.response.send_message("Not for you.", ephemeral=True)
                from airi.gender import set_gender
                await set_gender(str(ctx.author.id), "b")
                await interaction.response.edit_message(
                    embed=discord.Embed(title="♂️ Gender set to Male", description=ctx.author.display_name, color=0x3498db),
                    view=None
                )

            @discord.ui.button(label="Female", style=discord.ButtonStyle.danger, emoji="♀️")
            async def female(self_, interaction, button):
                if interaction.user.id != ctx.author.id:
                    return await interaction.response.send_message("Not for you.", ephemeral=True)
                from airi.gender import set_gender
                await set_gender(str(ctx.author.id), "g")
                await interaction.response.edit_message(
                    embed=discord.Embed(title="♀️ Gender set to Female", description=ctx.author.display_name, color=0xe91e63),
                    view=None
                )

            @discord.ui.button(label="Reset", style=discord.ButtonStyle.secondary, emoji="🔄")
            async def reset(self_, interaction, button):
                if interaction.user.id != ctx.author.id:
                    return await interaction.response.send_message("Not for you.", ephemeral=True)
                from airi.gender import reset_gender
                await reset_gender(str(ctx.author.id))
                await interaction.response.edit_message(
                    embed=discord.Embed(description="Gender preference cleared.", color=0x95a5a6),
                    view=None
                )

        from airi.gender import get_gender
        current = await get_gender(str(ctx.author.id))
        label = {"b": "Male", "g": "Female"}.get(current, "Not set")
        e = discord.Embed(
            title="♟️ Role Play Gender",
            description=(
                "Select your preferred gender for action text.\n\n"
                f"**Current:** {label}"
            ),
            color=0x7289da,
        )
        e.set_footer(text=ctx.author.display_name)
        await ctx.send(embed=e, view=GenderView())

    # ── !gifsearch ────────────────────────────────────────────────
    @bot.command(aliases=["gifs", "searchgif"])
    async def gifsearch(ctx, *, query: str = None):
        """Search Klipy for GIFs. Browse results and Accept to lock one in."""
        if not query: return await _err(ctx, "Usage: `!gifsearch <query>`")
        async with ctx.typing():
            urls = await klipy_search(query, 8)
        if not urls:
            return await _err(ctx, f"No GIFs found for **{query}**.")

        class GifSearchView(discord.ui.View):
            def __init__(self_, urls_, author_id_):
                super().__init__(timeout=180)
                self_._urls    = urls_
                self_._author  = author_id_
                self_._current = 0
                self_._accepted = False
                self_._upd()

            def _upd(self_):
                for item in self_.children:
                    if item.label == "◀ Prev":
                        item.disabled = self_._current == 0
                    elif item.label == "Next ▶":
                        item.disabled = self_._current == len(self_._urls) - 1
                    elif item.label == "✅ Lock this GIF":
                        item.disabled = self_._accepted

            def _embed(self_) -> discord.Embed:
                e = discord.Embed(title=f"🔍 {query}", color=C_INFO)
                e.set_image(url=self_._urls[self_._current])
                e.set_footer(text=f"Result {self_._current+1}/{len(self_._urls)} · Powered by Klipy")
                return e

            @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
            async def prev(self_, inter, btn):
                if inter.user.id != self_._author:
                    return await inter.response.send_message("Not for you.", ephemeral=True)
                self_._current -= 1; self_._upd()
                await inter.response.edit_message(embed=self_._embed(), view=self_)

            @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
            async def nxt(self_, inter, btn):
                if inter.user.id != self_._author:
                    return await inter.response.send_message("Not for you.", ephemeral=True)
                self_._current += 1; self_._upd()
                await inter.response.edit_message(embed=self_._embed(), view=self_)

            @discord.ui.button(label="✅ Lock this GIF", style=discord.ButtonStyle.success)
            async def accept(self_, inter, btn):
                if inter.user.id != self_._author:
                    return await inter.response.send_message("Not for you.", ephemeral=True)
                self_._accepted = True
                # Remove all buttons — final locked embed
                e = self_._embed()
                e.title = f"🔒 {query}"
                e.set_footer(text=f"Locked by {inter.user.display_name} · Result {self_._current+1}/{len(self_._urls)}")
                await inter.response.edit_message(embed=e, view=None)
                self_.stop()

        v = GifSearchView(urls, ctx.author.id)
        await ctx.send(embed=v._embed(), view=v)

    # ── GIF command factory ───────────────────────────────────────
    def make_gif_command(cmd_name, aliases=None):
        aliases = aliases or []
        is_nsfw = cmd_name in NSFW_COMMANDS
        cat     = "nsfw_gif" if is_nsfw else "sfw_gif"

        @bot.command(name=cmd_name, aliases=aliases)
        async def gif_cmd(ctx, member: discord.Member = None):
            if not await check_channel(ctx, cat): return
            from airi.gender import get_gender
            ag = await get_gender(str(ctx.author.id)) or "u"

            # Resolve target from reply
            target = member
            if target is None and ctx.message.reference:
                try:
                    ref    = await ctx.channel.fetch_message(ctx.message.reference.message_id)
                    target = ref.author
                except Exception:
                    pass

            if target is None:
                # Solo or show picker
                actions = actio.ACTIONS.get(cmd_name, {})
                is_solo = isinstance(actions, dict) and "solo" in actions
                if is_solo or cmd_name in config.SOLO_COMMANDS:
                    raw    = _get_action(cmd_name, ag, None)
                    action = raw.format(author=ctx.author.display_name)
                    gif_url, source = await get_gif(cmd_name, is_nsfw)
                    if not gif_url: return await _err(ctx, "Couldn't fetch a GIF right now.")
                    e = await _make_embed(bot, ctx, action, gif_url, source, cmd_name)
                    await ctx.send(embed=e)
                    return
                else:
                    if _check_empty(str(ctx.author.id)): return
                    e = discord.Embed(
                        description=f"**{ctx.author.display_name}** wants to `!{cmd_name}` someone...",
                        color=0x7289da
                    )
                    await ctx.send(embed=e, view=RecipientView(cmd_name, ctx.author.id, bot, is_nsfw))
                    return

            if is_nsfw:
                if await _nsfw_consent_check(ctx, target): return
            else:
                if await _sfw_rp_check(ctx, target): return

            _reset_empty(str(ctx.author.id))
            tg     = await get_gender(str(target.id)) or "u"
            raw    = _get_action(cmd_name, ag, tg)
            action = raw.format(author=ctx.author.display_name, target=target.mention)
            gif_url, source = await get_gif(cmd_name, is_nsfw)
            if not gif_url: return await _err(ctx, "Couldn't fetch a GIF right now.")
            e = await _make_embed(bot, ctx, action, gif_url, source, cmd_name)
            view = BackView(cmd_name, target.id, ctx.author.id, bot) if cmd_name in BACK_ACTIONS else None
            await ctx.send(embed=e, view=view)
            if ag == "u":
                await ctx.send(f"💡 {ctx.author.mention} set your gender with `!gender`", delete_after=10)

        return gif_cmd

    # Register all commands from actio + aliases from config
    all_cmds = set(actio.ACTIONS.keys()) | set(actio.ACTIONS_SOLO.keys())
    for cmd_name in all_cmds:
        make_gif_command(cmd_name, config.ALIASES.get(cmd_name, []))

    # ── !antinoobify / !cmds ──────────────────────────────────────
    @bot.command(name="antinoobify", aliases=["cmds", "giflist"])
    async def antinoobify(ctx, *, command_name: str = None):
        commands_data_local = {k: {} for k in all_cmds}
        if command_name is None:
            pages = _build_pages(commands_data_local)
            msg   = await ctx.send(embed=pages[0], view=PageView(pages, bot) if len(pages) > 1 else None)
            if len(pages) > 1:
                await _add_msg(ctx.guild.id, ctx.channel.id, msg.id)
        else:
            cmd = bot.get_command(command_name)
            if not cmd: return await _err(ctx, f"Command `{command_name}` not found.")
            base    = cmd.name
            actions = actio.ACTIONS.get(base, {})
            aliases = config.ALIASES.get(base, [])
            is_nsfw = base in NSFW_COMMANDS
            e = discord.Embed(
                title=f"{'🔞 ' if is_nsfw else ''}Command: !{base}",
                description=f"**Aliases:** {', '.join(aliases) or 'None'}",
                color=C_INFO,
            )
            e.add_field(name="Usage", value=f"`!{base} @user` or `!{base}` (shows picker)", inline=False)
            if isinstance(actions, dict) and actions.get("solo"):
                ex = random.choice(actions["solo"]).replace("{author}", ctx.author.display_name)
                e.add_field(name="Example (solo)", value=f"*{ex}*", inline=False)
            if isinstance(actions, dict) and "default" in actions:
                ex = random.choice(actions["default"]).replace("{author}", ctx.author.display_name).replace("{target}", "@user")
                e.add_field(name="Example (targeted)", value=f"*{ex}*", inline=False)
            await ctx.send(embed=e)


async def restore_antinoobify_listeners(bot, commands_data):
    for msg_data in await _load_msgs():
        guild   = bot.get_guild(msg_data["guild_id"])
        channel = guild.get_channel(msg_data["channel_id"]) if guild else None
        if not channel: continue
        try: message = await channel.fetch_message(msg_data["message_id"])
        except discord.NotFound:
            await _del_msg(msg_data["message_id"]); continue
        except Exception: continue
        pages = _build_pages({})
        if len(pages) > 1:
            try: await message.edit(view=PageView(pages, bot))
            except Exception: pass
