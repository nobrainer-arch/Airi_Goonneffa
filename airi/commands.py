# airi/commands.py — All GIF/action commands
# Hybrid: /cmd, !cmd, and "airi cmd" all work
# All commands show a recipient picker if no target is provided
import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone
import random
import db
import config
import actio
from utils import _err, C_SOCIAL, C_ERROR
from airi.gif_provider import get_gif
from airi.guild_config import check_channel

# ── Which commands are NSFW ────────────────────────────────────────
NSFW_COMMANDS: set[str] = {
    "fuck","bfuck","dickride","anal","bathroomfuck","bondage","blowjob",
    "kuni","pussyeat","lickdick","titjob","threesome","gangbang","fap",
    "grabbutts","grabboobs","grind","feet","finger","69","cum","cum_male",
    "fuck_lesbian",
}


# ── Helper: get action text (never returns same text twice in a row) ──
_last_text: dict[str, str] = {}  # cmd → last used text

def _get_action_text(cmd: str, ag: str, tg: str, back: bool = False) -> str:
    key_back = "back" if back else "default"
    entry = actio.ACTIONS.get(cmd, {})
    pool  = entry.get(key_back) or entry.get("default") or [f"{{author}} uses {cmd} on {{target}}"]
    last  = _last_text.get(f"{cmd}_{key_back}")
    choices = [t for t in pool if t != last] or pool
    picked = random.choice(choices)
    _last_text[f"{cmd}_{key_back}"] = picked
    return picked


def _get_solo_text(cmd: str) -> str:
    entry = actio.ACTIONS.get(cmd, {})
    pool  = entry.get("solo") or actio.ACTIONS_SOLO.get(cmd) or [f"{{author}} does {cmd}"]
    last  = _last_text.get(f"{cmd}_solo")
    choices = [t for t in pool if t != last] or pool
    picked = random.choice(choices)
    _last_text[f"{cmd}_solo"] = picked
    return picked


async def _build_embed(bot, ctx_or_inter, action_text: str, gif_url: str,
                        cmd_name: str, author: discord.Member,
                        target_member: discord.Member | None = None) -> discord.Embed:
    if hasattr(ctx_or_inter, "guild"):
        guild = ctx_or_inter.guild
        gid   = guild.id
        uid   = author.id
    else:
        return discord.Embed(description=action_text, color=C_SOCIAL)

    target_member = target_member or author
    stats = await db.pool.fetchrow(
        "SELECT hugs_received, kisses_received, pats_received FROM social WHERE guild_id=$1 AND user_id=$2",
        gid, target_member.id
    )
    pats = (stats or {}).get("pats_received", 0) or 0
    tracked = sum(( (stats or {}).get("hugs_received", 0) or 0,
                    (stats or {}).get("kisses_received", 0) or 0,
                    pats ))
    extra = f"\n\n*Pats received: {pats:,} · Tracked actions: {tracked:,}*" if stats else ""

    title = await db.pool.fetchval(
        "SELECT active_title FROM economy WHERE guild_id=$1 AND user_id=$2", gid, uid
    )
    e = discord.Embed(description=action_text + extra, color=C_SOCIAL, timestamp=datetime.now(timezone.utc))
    e.set_author(name=bot.user.display_name, icon_url=bot.user.display_avatar.url)
    tip = "  |  !rpblock @user to block" if cmd_name in NSFW_COMMANDS else ""
    e.set_footer(text=f"{'✨ '+title+'  ·  ' if title else ''}{author.display_name}{tip}")
    if gif_url: e.set_image(url=gif_url)
    return e


# ── Back button ───────────────────────────────────────────────────
class BackView(discord.ui.View):
    def __init__(self, cmd: str, target_id: int, author_id: int, bot):
        super().__init__(timeout=300)  # 5 min timeout
        self._cmd      = cmd
        self._target   = target_id
        self._author   = author_id
        self._bot      = bot
        self._used     = False
        _, label = config.BACK_ACTIONS.get(cmd, (cmd, f"↩ {cmd.title()} back"))
        btn = discord.ui.Button(
            label=label,
            style=discord.ButtonStyle.primary,
            custom_id=f"back:{cmd}:{target_id}:{author_id}:{random.randint(0,99999)}"
        )
        btn.callback = self._on_back
        self.add_item(btn)

    async def _on_back(self, interaction: discord.Interaction):
        if interaction.user.id != self._target:
            return await interaction.response.send_message("This button isn't for you.", ephemeral=True)
        if self._used:
            return await interaction.response.send_message("Already used.", ephemeral=True)
        self._used = True
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(view=self)

        from airi.gender import get_gender
        ag  = await get_gender(str(interaction.user.id)) or "u"
        tg  = await get_gender(str(self._author)) or "u"
        author_m = interaction.guild.get_member(self._author)
        is_nsfw  = self._cmd in NSFW_COMMANDS

        # Get a DIFFERENT gif than might have been shown (new random pull)
        gif_url, _ = await get_gif(self._cmd, is_nsfw, user_id=getattr(interaction,"user",None) and interaction.user.id)
        if not gif_url:
            await interaction.followup.send("Couldn't fetch a GIF right now.", ephemeral=True)
            return

        raw    = _get_action_text(self._cmd, ag, tg, back=True)
        action = raw.format(author=interaction.user.display_name,
                            target=author_m.mention if author_m else f"<@{self._author}>")

        if author_m:
            e = await _build_embed(self._bot, interaction, action, gif_url, self._cmd, interaction.user, target_member=author_m)
        else:
            e = await _build_embed(self._bot, interaction, action, gif_url, self._cmd, interaction.user)
        await interaction.followup.send(embed=e)


# ── Consent check ─────────────────────────────────────────────────
async def _nsfw_consent_check(ctx_or_inter, target: discord.Member) -> bool:
    """Returns True if the action should be BLOCKED."""
    if hasattr(ctx_or_inter, "guild"):
        guild = ctx_or_inter.guild
        author = ctx_or_inter.author if hasattr(ctx_or_inter, "author") else ctx_or_inter.user
    else:
        return False

    gid = guild.id
    # Check nsfw optout
    if await db.pool.fetchval("SELECT 1 FROM nsfw_optout WHERE guild_id=$1 AND user_id=$2", gid, target.id):
        await _err(ctx_or_inter, f"**{target.display_name}** has NSFW opt-out enabled.")
        return True
    # Check rpblock
    if await db.pool.fetchval(
        "SELECT 1 FROM rpblock WHERE guild_id=$1 AND user_id=$2 AND blocked_id=$3",
        gid, target.id, author.id
    ):
        await _err(ctx_or_inter, f"**{target.display_name}** has blocked you from RP.")
        return True
    return False


# ── Recipient picker ───────────────────────────────────────────────
class RecipientSelect(discord.ui.UserSelect):
    def __init__(self, cmd: str, author_id: int, bot, is_nsfw: bool, max_v: int = 1):
        super().__init__(
            placeholder="Select who to target…",
            min_values=1, max_values=max_v
        )
        self._cmd     = cmd
        self._author  = author_id
        self._bot     = bot
        self._is_nsfw = is_nsfw

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self._author:
            return await interaction.response.send_message("This isn't for you.", ephemeral=True)

        try:
            from airi.gender import get_gender
            ag = await get_gender(str(self._author)) or "u"

            # Acknowledge the interaction early so followups work consistently.
            await interaction.response.defer(ephemeral=True)

            sent_any = False
            for member in self.values:
                if member.bot:
                    await interaction.followup.send("❌ Bots can't be targeted.", ephemeral=True)
                    continue
                if self._is_nsfw:
                    if await db.pool.fetchval("SELECT 1 FROM nsfw_optout WHERE guild_id=$1 AND user_id=$2",
                                              interaction.guild.id, member.id):
                        await interaction.followup.send(f"**{member.display_name}** has NSFW opt-out.", ephemeral=True)
                        continue
                elif await db.pool.fetchval(
                    "SELECT 1 FROM rpblock WHERE guild_id=$1 AND user_id=$2 AND blocked_id=$3",
                    interaction.guild.id, member.id, self._author
                ):
                    await interaction.followup.send(f"**{member.display_name}** blocked you from RP.", ephemeral=True)
                    continue

                tg      = await get_gender(str(member.id)) or "u"
                raw     = _get_action_text(self._cmd, ag, tg)
                action  = raw.format(author=interaction.user.display_name, target=member.mention)
                gif_url, _ = await get_gif(self._cmd, self._is_nsfw, user_id=getattr(interaction,"user",None) and interaction.user.id)
                if not gif_url:
                    await interaction.followup.send(f"Couldn't fetch a GIF for `{self._cmd}`.", ephemeral=True)
                    continue

                e = await _build_embed(self._bot, interaction, action, gif_url, self._cmd, interaction.user, target_member=member)
                view = BackView(self._cmd, member.id, self._author, self._bot) if self._cmd in config.BACK_ACTIONS else None
                if view:
                    await interaction.followup.send(embed=e, view=view)
                else:
                    await interaction.followup.send(embed=e)
                sent_any = True

                await _increment_action_counter(interaction, self._cmd, member)

            if not sent_any:
                await interaction.followup.send("No valid targets could be processed.", ephemeral=True)
        except Exception as err:
            print(f"RecipientSelect callback error for {self._cmd}: {err}")
            try:
                await interaction.followup.send("❌ Something went wrong while selecting a target.", ephemeral=True)
            except Exception:
                pass
        finally:
            if self.view:
                self.view.stop()


class RecipientView(discord.ui.View):
    def __init__(self, cmd: str, author_id: int, bot, is_nsfw: bool):
        super().__init__(timeout=300)
        max_v = config.MULTI_TARGET_COMMANDS.get(cmd, 1)
        self.add_item(RecipientSelect(cmd, author_id, bot, is_nsfw, max_v))


# ── Action counter + milestone trigger ────────────────────────────
async def _increment_action_counter(ctx_or_inter, cmd: str, target: discord.Member):
    counter_map = {"hug": "hugs_received", "kiss": "kisses_received", "pat": "pats_received"}
    col = counter_map.get(cmd)
    if not col: return
    if hasattr(ctx_or_inter, "guild"):
        guild = ctx_or_inter.guild
        author = ctx_or_inter.author if hasattr(ctx_or_inter, "author") else ctx_or_inter.user
    else:
        return
    gid = guild.id
    tid = target.id
    try:
        new_val = await db.pool.fetchval(f"""
            INSERT INTO social (guild_id, user_id, {col}) VALUES ($1,$2,1)
            ON CONFLICT (guild_id,user_id) DO UPDATE SET {col}=social.{col}+1 RETURNING {col}
        """, gid, tid)
        # Also track mutual affection for relationship progression prompts
        uid = author.id
        if cmd == "kiss":
            u1, u2 = min(uid,tid), max(uid,tid)
            await db.pool.execute("""
                INSERT INTO mutual_affection (guild_id,user1_id,user2_id,kiss_count)
                VALUES ($1,$2,$3,1) ON CONFLICT (guild_id,user1_id,user2_id)
                DO UPDATE SET kiss_count=mutual_affection.kiss_count+1
            """, gid, u1, u2)
        # Milestone check
        if new_val:
            from airi.milestones import check_milestone
            channel = ctx_or_inter.channel if hasattr(ctx_or_inter, "channel") else None
            bot = ctx_or_inter.client if hasattr(ctx_or_inter, "client") else None
            await check_milestone(bot, gid, tid, cmd, new_val, channel)
    except Exception as err:
        print(f"Counter error: {err}")


# ── GifSearch command ──────────────────────────────────────────────
async def _gifsearch(ctx, *, query: str):
    from airi.gif_provider import klipy_search
    urls = await klipy_search(query, 8)
    if not urls:
        return await _err(ctx, f"No GIFs found for **{query}**.")

    current = [0]

    class GifSearchView(discord.ui.View):
        def __init__(self_):
            super().__init__(timeout=300)
            self_._upd()
        def _upd(self_):
            self_.prev.disabled = current[0] == 0
            self_.nxt.disabled  = current[0] == len(urls) - 1
        def _embed(self_):
            e = discord.Embed(title=f"🔍 {query}", color=C_SOCIAL)
            e.set_image(url=urls[current[0]])
            e.set_footer(text=f"Result {current[0]+1}/{len(urls)} · Navigate then Lock to keep")
            return e
        @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
        async def prev(self_, inter, btn):
            if inter.user.id != ctx.author.id: return await inter.response.send_message("Not for you.", ephemeral=True)
            current[0] -= 1; self_._upd()
            await inter.response.edit_message(embed=self_._embed(), view=self_)
        @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
        async def nxt(self_, inter, btn):
            if inter.user.id != ctx.author.id: return await inter.response.send_message("Not for you.", ephemeral=True)
            current[0] += 1; self_._upd()
            await inter.response.edit_message(embed=self_._embed(), view=self_)
        @discord.ui.button(label="✅ Lock this GIF", style=discord.ButtonStyle.success)
        async def lock(self_, inter, btn):
            if inter.user.id != ctx.author.id: return await inter.response.send_message("Not for you.", ephemeral=True)
            e = self_._embed()
            e.set_footer(text=f"🔒 Locked by {inter.user.display_name} · Result {current[0]+1}/{len(urls)}")
            await inter.response.edit_message(embed=e, view=None)
            self_.stop()

    v = GifSearchView()
    await ctx.send(embed=v._embed(), view=v)


# ── Setup all commands on the bot ─────────────────────────────────
def setup_commands(bot, commands_data: dict):
    """
    Dynamically register all action commands as hybrid commands.
    commands_data: {cmd_name: {is_nsfw, has_solo, description}}
    """
    @bot.listen("on_message")
    async def _airi_prefix_listener(message: discord.Message):
        """Handles 'airi' alone to show help (no double processing)."""
        if message.author.bot:
            return
        content = message.content.strip()
        # Only respond to exact "airi" (case-insensitive) with no extra arguments
        if content.lower() == "airi":
            ctx = await bot.get_context(message)
            help_cmd = bot.get_command("help")
            if help_cmd:
                await ctx.invoke(help_cmd)

    for cmd_name, meta in commands_data.items():
        is_nsfw  = meta.get("is_nsfw", False)
        has_solo = meta.get("has_solo", False)
        desc     = meta.get("desc", f"Perform the {cmd_name} action")
        aliases  = config.ALIASES.get(cmd_name, [])

        def make_command(name=cmd_name, nsfw=is_nsfw, solo=has_solo):
            @commands.hybrid_command(name=name, aliases=aliases[:10], description=desc, with_app_command=False)
            async def _cmd(ctx, target: discord.Member = None):
                # If no target and not a solo command, show picker
                if target is None:
                    # Commands in SOLO_COMMANDS always do solo action
                    # Other commands: always show picker (even if they have solo text)
                    if name in config.SOLO_COMMANDS:
                        raw    = _get_solo_text(name)
                        action = raw.format(author=ctx.author.display_name, target="")
                        gif_url, _ = await get_gif(name, nsfw, user_id=getattr(ctx,"author",None) and ctx.author.id)
                        if not gif_url: return await _err(ctx, "Couldn't fetch a GIF right now.")
                        e = await _build_embed(ctx.bot, ctx, action, gif_url, name, ctx.author, target_member=ctx.author)
                        return await ctx.send(embed=e)
                    else:
                        # Show recipient picker for ALL commands with targets
                        view = RecipientView(name, ctx.author.id, ctx.bot, nsfw)
                        e = discord.Embed(
                            description=f"Who do you want to `{name}`?",
                            color=C_SOCIAL
                        )
                        return await ctx.send(embed=e, view=view)

                # Has target
                if target == ctx.author: return await _err(ctx, "You can't target yourself.")
                if target.bot: return await _err(ctx, "Bots can't be targeted.")

                # NSFW checks
                if nsfw:
                    if await db.pool.fetchval("SELECT 1 FROM nsfw_optout WHERE guild_id=$1 AND user_id=$2",
                                              ctx.guild.id, target.id):
                        return await _err(ctx, f"**{target.display_name}** has NSFW opt-out.")

                    # Check if target is partner/claimed; if not, send consent request
                    gid, uid, tid = ctx.guild.id, ctx.author.id, target.id
                    is_partner = await db.pool.fetchval("""
                        SELECT 1 FROM relationships
                        WHERE guild_id=$1 AND (user1_id=$2 OR user2_id=$2)
                          AND (user1_id=$3 OR user2_id=$3) AND status='active'
                    """, gid, uid, tid)
                    is_claimed = await db.pool.fetchval(
                        "SELECT 1 FROM claims WHERE guild_id=$1 AND claimer_id=$2 AND claimed_id=$3",
                        gid, uid, tid
                    )
                    if not is_partner and not is_claimed:
                        from airi.commands import HookupRequestView
                        from airi.gender import get_gender as _gg
                        ag2 = await _gg(str(uid)) or "u"
                        tg2 = await _gg(str(tid)) or "u"
                        req = HookupRequestView(ctx.author, target, name, ag2, tg2, ctx.bot)
                        try:
                            await target.send(
                                embed=discord.Embed(
                                    description=f"**{ctx.author.display_name}** wants to `{name}` with you!\nAccept or decline in 60s.",
                                    color=C_SOCIAL
                                ), view=req
                            )
                            return await ctx.send(f"📨 Sent consent request to {target.mention}.", delete_after=15)
                        except discord.Forbidden:
                            return await _err(ctx, f"Couldn't DM {target.mention}. They may have DMs disabled.")
                else:
                    # SFW: check rpblock
                    if await db.pool.fetchval(
                        "SELECT 1 FROM rpblock WHERE guild_id=$1 AND user_id=$2 AND blocked_id=$3",
                        ctx.guild.id, target.id, ctx.author.id
                    ):
                        return await _err(ctx, f"**{target.display_name}** blocked you from RP.")

                from airi.gender import get_gender
                ag = await get_gender(str(ctx.author.id)) or "u"
                tg = await get_gender(str(target.id))     or "u"
                raw     = _get_action_text(name, ag, tg)
                action  = raw.format(author=ctx.author.display_name, target=target.mention)
                gif_url, _ = await get_gif(name, nsfw, user_id=getattr(ctx,"author",None) and ctx.author.id)
                if not gif_url: return await _err(ctx, "Couldn't fetch a GIF right now.")
                e = await _build_embed(ctx.bot, ctx, action, gif_url, name, ctx.author, target_member=target)
                view = BackView(name, target.id, ctx.author.id, ctx.bot) if name in config.BACK_ACTIONS else None
                await ctx.send(embed=e, view=view)
                await _increment_action_counter(ctx, name, target)
                if ag == "u":
                    await ctx.send(f"💡 {ctx.author.mention} set your gender with `!gender`", delete_after=10)

            return _cmd

        cmd_obj = make_command()
        bot.add_command(cmd_obj)

    # Add gifsearch
    @bot.hybrid_command(name="gifsearch", aliases=["gifs","searchgif"], description="Search for GIFs via Klipy")
    async def gifsearch_cmd(ctx, *, query: str):
        await _gifsearch(ctx, query=query)


# ── Hookup consent view ────────────────────────────────────────────
class HookupRequestView(discord.ui.View):
    def __init__(self, author: discord.Member, target: discord.Member,
                 cmd_name: str, ag: str, tg: str, bot):
        super().__init__(timeout=60)
        self._author  = author
        self._target  = target
        self._cmd     = cmd_name
        self._ag      = ag
        self._tg      = tg
        self._bot     = bot
        self._done    = False

    @discord.ui.button(label="✅ Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._target.id:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        if self._done: return
        self._done = True
        for i in self.children: i.disabled = True
        await interaction.response.edit_message(content="✅ Accepted!", view=self)
        is_nsfw = self._cmd in NSFW_COMMANDS
        raw     = _get_action_text(self._cmd, self._ag, self._tg)
        action  = raw.format(author=self._author.display_name, target=self._target.mention)
        gif_url, _ = await get_gif(self._cmd, is_nsfw, user_id=getattr(interaction,"user",None) and interaction.user.id)
        if not gif_url: return
        e = await _build_embed(self._bot, interaction, action, gif_url, self._cmd, self._author, target_member=self._target)
        guild = self._author.guild
        for ch in guild.text_channels:
            p = ch.permissions_for(guild.me)
            if p.send_messages and p.embed_links:
                view = BackView(self._cmd, self._target.id, self._author.id, self._bot) if self._cmd in config.BACK_ACTIONS else None
                await ch.send(embed=e, view=view)
                break
        self.stop()

    @discord.ui.button(label="❌ Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self._target.id:
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        if self._done: return
        self._done = True
        for i in self.children: i.disabled = True
        await interaction.response.edit_message(content="❌ Declined.", view=self)
        try:
            await self._author.send(embed=discord.Embed(
                description=f"**{self._target.display_name}** declined your `{self._cmd}` request.",
                color=C_ERROR
            ))
        except Exception: pass
        self.stop()

    async def on_timeout(self):
        self._done = True
        try:
            await self._author.send(embed=discord.Embed(
                description=f"Your `{self._cmd}` request to **{self._target.display_name}** timed out.",
                color=0xf39c12
            ))
        except Exception: pass
