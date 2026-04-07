# goonneffa/commands.py
# Moderation commands with button / modal UI so mods type less.
import discord
from discord.ext import commands
from datetime import timedelta
from utils import is_mod, _err
import db


# ── Log helper (per-guild) ─────────────────────────────────────────
async def _log(bot, guild_id: int, action: str, target: discord.Member,
               mod: discord.Member, reason: str):
    try:
        from airi.guild_config import get_log_channel
        ch_id = await get_log_channel(guild_id)
        ch    = bot.get_channel(ch_id) if ch_id else bot.get_channel(__import__("config").LOG_CHANNEL_ID)
        if not ch: return
        is_severe = action.lower() in ("ban", "kick")
        e = discord.Embed(
            title=f"🛡️ {action.title()}",
            color=0xe74c3c if is_severe else 0xf39c12,
            timestamp=discord.utils.utcnow(),
        )
        e.add_field(name="Target", value=f"{target.mention} (`{target.id}`)", inline=True)
        e.add_field(name="Mod",    value=mod.mention,                          inline=True)
        e.add_field(name="Reason", value=reason,                               inline=False)
        await ch.send(embed=e)
    except Exception as err:
        print(f"Goon log failed: {err}")


# ── Reason modal ──────────────────────────────────────────────────
class ReasonModal(discord.ui.Modal):
    reason_in = discord.ui.TextInput(
        label="Reason",
        placeholder="Describe the reason...",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=512,
    )

    def __init__(self, action: str, target: discord.Member, duration: int = 0):
        super().__init__(title=f"{action.title()} — {target.display_name}")
        self._action   = action
        self._target   = target
        self._duration = duration  # seconds, only for timeout

    async def on_submit(self, interaction: discord.Interaction):
        reason = self.reason_in.value.strip()
        mod    = interaction.user
        guild  = interaction.guild

        try:
            if self._action == "ban":
                await self._target.ban(reason=reason)
                desc = f"🔨 **{self._target}** was banned."
            elif self._action == "kick":
                await self._target.kick(reason=reason)
                desc = f"👢 **{self._target}** was kicked."
            elif self._action == "timeout":
                end = discord.utils.utcnow() + timedelta(seconds=self._duration)
                await self._target.timeout(end, reason=reason)
                mins = self._duration // 60
                desc = f"⏱️ **{self._target}** timed out for **{mins}m**."
            elif self._action == "warn":
                gid = guild.id
                uid = self._target.id
                count = await db.pool.fetchval(
                    "SELECT COUNT(*) FROM audit_log WHERE guild_id=$1 AND user_id=$2 AND action='warn'",
                    gid, uid
                ) or 0
                await db.pool.execute("""
                    INSERT INTO audit_log (guild_id, user_id, action, detail)
                    VALUES ($1,$2,'warn',$3)
                """, gid, uid, reason)
                desc = f"⚠️ **{self._target}** warned (**{count+1}** total warnings)."
                try:
                    await self._target.send(embed=discord.Embed(
                        title=f"⚠️ Warning from {guild.name}",
                        description=f"**Reason:** {reason}\n\nThis is warning #{count+1}.",
                        color=0xf39c12,
                    ))
                except Exception:
                    pass
            else:
                desc = f"✅ Action `{self._action}` applied to **{self._target}**."

            await _log(interaction.client, guild.id, self._action, self._target, mod, reason)
            await interaction.response.send_message(
                embed=discord.Embed(description=desc, color=0x2ecc71),
                ephemeral=True
            )

        except discord.Forbidden:
            await interaction.response.send_message(
                f"❌ Missing permissions to {self._action} {self._target.mention}.", ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)


# ── Timeout duration picker ───────────────────────────────────────
DURATION_OPTIONS = [
    discord.SelectOption(label="5 minutes",   value="300"),
    discord.SelectOption(label="10 minutes",  value="600"),
    discord.SelectOption(label="30 minutes",  value="1800"),
    discord.SelectOption(label="1 hour",      value="3600"),
    discord.SelectOption(label="6 hours",     value="21600"),
    discord.SelectOption(label="12 hours",    value="43200"),
    discord.SelectOption(label="24 hours",    value="86400"),
    discord.SelectOption(label="1 week",      value="604800"),
]


class TimeoutDurationView(discord.ui.View):
    def __init__(self, target: discord.Member):
        super().__init__(timeout=60)
        self._target = target

    @discord.ui.select(placeholder="Select timeout duration...", options=DURATION_OPTIONS)
    async def duration_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        duration = int(select.values[0])
        await interaction.response.send_modal(ReasonModal("timeout", self._target, duration))
        self.stop()


# ── Action picker view ─────────────────────────────────────────────
class ModActionView(discord.ui.View):
    def __init__(self, target: discord.Member, mod_id: int):
        super().__init__(timeout=120)
        self._target = target
        self._mod    = mod_id

    def _check_mod(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._mod and is_mod(interaction.user)

    @discord.ui.button(label="⏱️ Timeout", style=discord.ButtonStyle.secondary)
    async def timeout_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_mod(interaction):
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        view = TimeoutDurationView(self._target)
        await interaction.response.send_message("How long?", view=view, ephemeral=True)

    @discord.ui.button(label="⚠️ Warn", style=discord.ButtonStyle.primary)
    async def warn_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_mod(interaction):
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        await interaction.response.send_modal(ReasonModal("warn", self._target))

    @discord.ui.button(label="👢 Kick", style=discord.ButtonStyle.danger)
    async def kick_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_mod(interaction):
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        await interaction.response.send_modal(ReasonModal("kick", self._target))

    @discord.ui.button(label="🔨 Ban", style=discord.ButtonStyle.danger)
    async def ban_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_mod(interaction):
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        # Confirm first
        class BanConfirmView(discord.ui.View):
            def __init__(self_, t): super().__init__(timeout=30); self_._t = t
            @discord.ui.button(label="Yes, ban them", style=discord.ButtonStyle.danger)
            async def yes(self_, inter, btn):
                for i in self_.children: i.disabled = True
                await inter.response.edit_message(view=self_)
                await inter.response.send_modal(ReasonModal("ban", self_._t))
                self_.stop()
            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def no(self_, inter, btn):
                for i in self_.children: i.disabled = True
                await inter.response.edit_message(content="Cancelled.", view=self_)
                self_.stop()

        await interaction.response.send_message(
            f"⚠️ Really ban **{self._target}**?",
            view=BanConfirmView(self._target),
            ephemeral=True
        )

    @discord.ui.button(label="📋 View Warnings", style=discord.ButtonStyle.secondary)
    async def warns_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_mod(interaction):
            return await interaction.response.send_message("Not for you.", ephemeral=True)
        rows = await db.pool.fetch("""
            SELECT detail, created_at FROM audit_log
            WHERE guild_id=$1 AND user_id=$2 AND action='warn'
            ORDER BY created_at DESC LIMIT 10
        """, interaction.guild_id, self._target.id)
        if not rows:
            await interaction.response.send_message(
                f"**{self._target}** has no warnings.", ephemeral=True
            )
            return
        lines = [f"`{r['created_at'].strftime('%m/%d %H:%M')}` {r['detail']}" for r in rows]
        e = discord.Embed(
            title=f"⚠️ Warnings — {self._target.display_name}",
            description="\n".join(lines),
            color=0xf39c12,
        )
        await interaction.response.send_message(embed=e, ephemeral=True)


class CommandsCog(commands.Cog, name="CommandsCog"):
    def __init__(self, bot): self.bot = bot

    @commands.command()
    async def ping(self, ctx):
        await ctx.send("🏓 pong")

    # ── !mod @user — main button panel ───────────────────────────
    @commands.command(aliases=["moderate", "modpanel"])
    async def mod(self, ctx, member: discord.Member = None):
        """Open moderation panel for a user."""
        if not is_mod(ctx.author):
            return await _err(ctx, "You are not a mod.")
        if member is None:
            # UserSelect picker
            class PickView(discord.ui.View):
                def __init__(self_): super().__init__(timeout=60)
                @discord.ui.user_select(placeholder="Select a member to moderate...")
                async def pick(self_, inter, sel):
                    if inter.user.id != ctx.author.id:
                        return await inter.response.send_message("Not for you.", ephemeral=True)
                    target = sel.values[0]
                    for i in self_.children: i.disabled = True
                    await inter.response.edit_message(view=self_)
                    await _send_mod_panel(inter, target, ctx.author)
                    self_.stop()
            await ctx.send("Who do you want to moderate?", view=PickView())
            return
        await _send_mod_panel(ctx, member, ctx.author)

    # ── Legacy text commands (kept for muscle memory) ─────────────
    @commands.group(name="goonneffa", invoke_without_command=True)
    async def gn(self, ctx, member: discord.Member = None):
        if ctx.invoked_subcommand is not None: return
        if not is_mod(ctx.author): return await _err(ctx, "You are not a mod.")
        if member:
            await _send_mod_panel(ctx, member, ctx.author)
        else:
            await ctx.send("Usage: `!mod @user` — opens the moderation panel.")

    @gn.command(name="ban")
    async def gn_ban(self, ctx, member: discord.Member, *, reason: str):
        if not is_mod(ctx.author): return await _err(ctx, "You are not a mod.")
        try:
            await member.ban(reason=reason)
            await ctx.send(embed=discord.Embed(description=f"🔨 Banned {member.mention} — {reason}", color=0xe74c3c))
            await _log(self.bot, ctx.guild.id, "ban", member, ctx.author, reason)
        except Exception as e:
            await _err(ctx, f"Failed: {e}", delete_cmd=False)
        finally:
            try: await ctx.message.delete()
            except: pass

    @gn.command(name="kick")
    async def gn_kick(self, ctx, member: discord.Member, *, reason: str):
        if not is_mod(ctx.author): return await _err(ctx, "You are not a mod.")
        try:
            await member.kick(reason=reason)
            await ctx.send(embed=discord.Embed(description=f"👢 Kicked {member.mention} — {reason}", color=0xe74c3c))
            await _log(self.bot, ctx.guild.id, "kick", member, ctx.author, reason)
        except Exception as e:
            await _err(ctx, f"Failed: {e}", delete_cmd=False)
        finally:
            try: await ctx.message.delete()
            except: pass

    @gn.command(name="timeout")
    async def gn_timeout(self, ctx, member: discord.Member, duration: int, *, reason: str):
        if not is_mod(ctx.author): return await _err(ctx, "You are not a mod.")
        try:
            end = discord.utils.utcnow() + timedelta(seconds=duration)
            await member.timeout(end, reason=reason)
            await ctx.send(embed=discord.Embed(description=f"⏱️ Timed out {member.mention} for {duration}s — {reason}", color=0xf39c12))
            await _log(self.bot, ctx.guild.id, "timeout", member, ctx.author, reason)
        except Exception as e:
            await _err(ctx, f"Failed: {e}", delete_cmd=False)
        finally:
            try: await ctx.message.delete()
            except: pass

    # ── Warn history ──────────────────────────────────────────────
    @commands.command()
    async def warns(self, ctx, member: discord.Member):
        if not is_mod(ctx.author): return await _err(ctx, "You are not a mod.")
        rows = await db.pool.fetch("""
            SELECT detail, created_at FROM audit_log
            WHERE guild_id=$1 AND user_id=$2 AND action='warn'
            ORDER BY created_at DESC LIMIT 20
        """, ctx.guild.id, member.id)
        if not rows:
            return await ctx.send(f"**{member.display_name}** has no warnings.")
        lines = [f"`{r['created_at'].strftime('%m/%d %H:%M')}` {r['detail']}" for r in rows]
        e = discord.Embed(
            title=f"⚠️ Warnings — {member.display_name} ({len(rows)})",
            description="\n".join(lines), color=0xf39c12,
        )
        await ctx.send(embed=e)

    @commands.command()
    async def clearwarns(self, ctx, member: discord.Member):
        if not is_mod(ctx.author): return await _err(ctx, "You are not a mod.")
        deleted = await db.pool.execute(
            "DELETE FROM audit_log WHERE guild_id=$1 AND user_id=$2 AND action='warn'",
            ctx.guild.id, member.id
        )
        await ctx.send(f"✅ Cleared warnings for **{member.display_name}**.", delete_after=8)

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def shutdown(self, ctx):
        await ctx.send("🔴 Goonneffa shutting down...")
        await self.bot.close()

    @commands.command(aliases=["cdh", "cleanbotmsgs"])
    @commands.has_permissions(manage_messages=True)
    async def chatdelhist(self, ctx, count: int = 10):
        count = min(count, 100)
        msgs  = []
        async for msg in ctx.channel.history(limit=500):
            if msg.author == ctx.guild.me: msgs.append(msg)
            if len(msgs) >= count: break
        if not msgs: return await ctx.send("No bot messages found.", delete_after=4)
        try: await ctx.channel.delete_messages(msgs)
        except Exception:
            for m in msgs:
                try: await m.delete()
                except Exception: pass
        await ctx.send(f"🗑️ Deleted **{len(msgs)}** messages.", delete_after=4)


async def _send_mod_panel(ctx_or_inter, target: discord.Member, mod: discord.Member):
    """Send the mod panel embed + action buttons."""
    gid = target.guild.id
    warn_count = await db.pool.fetchval(
        "SELECT COUNT(*) FROM audit_log WHERE guild_id=$1 AND user_id=$2 AND action='warn'",
        gid, target.id
    ) or 0

    e = discord.Embed(
        title=f"🛡️ Mod Panel — {target.display_name}",
        color=0xe74c3c,
        timestamp=discord.utils.utcnow(),
    )
    e.set_thumbnail(url=target.display_avatar.url)
    e.add_field(name="User",     value=f"{target.mention} (`{target.id}`)", inline=True)
    e.add_field(name="Joined",   value=discord.utils.format_dt(target.joined_at, "R") if target.joined_at else "?", inline=True)
    e.add_field(name="⚠️ Warns", value=str(warn_count), inline=True)
    e.add_field(name="Roles",    value=" ".join(r.mention for r in target.roles[1:6]) or "None", inline=False)
    e.set_footer(text=f"Moderated by {mod.display_name}")

    view = ModActionView(target, mod.id)
    if hasattr(ctx_or_inter, "send"):
        await ctx_or_inter.send(embed=e, view=view)
    else:
        await ctx_or_inter.followup.send(embed=e, view=view, ephemeral=True)
