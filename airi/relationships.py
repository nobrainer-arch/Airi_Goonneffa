# airi/relationships.py
import discord
from discord.ext import commands
from datetime import datetime, timedelta
import asyncio
import db
from utils import _err, C_REL, C_ERROR, C_WARN, C_SUCCESS, is_mod
from airi.guild_config import check_channel, get_court_channel, is_judge
from airi.economy import add_coins, get_balance

HOOKUP_MINIMUM  = 200
PROPOSAL_TIMEOUT = 3600
CHEATING_WARN_AT = 3
RELTYPES = {"hookup", "dating", "married"}


async def _get_rel(guild_id, user_id):
    """Return the most significant active relationship for this user.
    Prioritises married > dating > hookup so court/shared commands always
    find the marriage even when a hookup with a different user is also active.
    """
    return await db.pool.fetchrow("""
        SELECT * FROM relationships
        WHERE guild_id=$1 AND (user1_id=$2 OR user2_id=$2) AND status='active'
        ORDER BY CASE type
            WHEN 'married' THEN 0
            WHEN 'dating'  THEN 1
            ELSE                2
        END
        LIMIT 1
    """, guild_id, user_id)


async def _get_married_rel(guild_id, user_id):
    """Return active marriage only — used by endrel court, shared account, verdict."""
    return await db.pool.fetchrow("""
        SELECT * FROM relationships
        WHERE guild_id=$1 AND (user1_id=$2 OR user2_id=$2)
          AND status='active' AND type='married'
        LIMIT 1
    """, guild_id, user_id)

async def _rel_opted_out(guild_id, user_id):
    return await db.pool.fetchval(
        "SELECT 1 FROM rel_optout WHERE guild_id=$1 AND user_id=$2", guild_id, user_id
    )


# ── Proposal accept/decline view ──────────────────────────────────
class ProposalView(discord.ui.View):
    def __init__(self, proposer_id: int, target_id: int, proposal_row_id: int,
                 ptype: str, dowry: int, prenup: bool):
        super().__init__(timeout=PROPOSAL_TIMEOUT)
        self._proposer  = proposer_id
        self._target    = target_id
        self._row_id    = proposal_row_id
        self._ptype     = ptype
        self._dowry     = dowry
        self._prenup    = prenup
        self._done      = False

    async def _finish(self, interaction: discord.Interaction, accepted: bool):
        if self._done:
            await interaction.response.send_message("Already responded.", ephemeral=True)
            return
        if interaction.user.id != self._target:
            await interaction.response.send_message("Not for you.", ephemeral=True)
            return
        self._done = True
        self.stop()

        gid = interaction.guild_id
        uid = self._proposer
        tid = self._target

        await db.pool.execute(
            "UPDATE proposals SET status=$1 WHERE id=$2",
            "accepted" if accepted else "rejected", self._row_id
        )

        if accepted:
            rel = await db.pool.fetchrow("""
                INSERT INTO relationships (guild_id, user1_id, user2_id, type, proposer_id, dowry_paid, prenup)
                VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id
            """, gid, uid, tid, self._ptype, uid, self._dowry, self._prenup)
            if self._ptype == "married":
                await db.pool.execute("""
                    INSERT INTO shared_accounts (relationship_id, guild_id, balance) VALUES ($1,$2,0)
                """, rel["id"], gid)
            label = "💍 **You are now married!**" if self._ptype == "married" else "💘 **You are now dating!**"
            shared_note = "\n🏦 A shared account was created — use `!shared balance`." if self._ptype == "married" else ""
            e = discord.Embed(
                title="💕 Proposal Accepted!",
                description=f"{interaction.user.mention} said **YES**!\n{label}{shared_note}",
                color=C_SUCCESS,
            )
        else:
            if self._dowry > 0:
                await add_coins(gid, uid, self._dowry)
            e = discord.Embed(
                title="💔 Proposal Declined",
                description=f"{interaction.user.mention} said **no**.",
                color=C_ERROR,
            )

        # Disable buttons, edit original message
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(embed=e, view=self)

    @discord.ui.button(label="✅ Accept", style=discord.ButtonStyle.success, custom_id="proposal_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._finish(interaction, True)

    @discord.ui.button(label="❌ Decline", style=discord.ButtonStyle.danger, custom_id="proposal_decline")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._finish(interaction, False)

    async def on_timeout(self):
        self._done = True


class _CheatingResolutionView(discord.ui.View):
    """Sent to both partners after CHEATING_WARN_AT strikes in a dating relationship.
    If both click Continue → reset strikes. If either clicks Break up → end relationship.
    24h timeout → auto break up.
    """
    def __init__(self, rel_id: int, guild_id: int, cheater_id: int, partner_id: int, bot):
        super().__init__(timeout=86400)   # 24 hours
        self._rel_id    = rel_id
        self._guild_id  = guild_id
        self._cheater   = cheater_id
        self._partner   = partner_id
        self._bot       = bot
        self._votes: dict[int, str] = {}  # uid → "continue" | "breakup"

    async def _resolve(self, interaction: discord.Interaction, choice: str):
        uid = interaction.user.id
        if uid not in (self._cheater, self._partner):
            return await interaction.response.send_message("This isn't for you.", ephemeral=True)
        self._votes[uid] = choice
        await interaction.response.send_message(
            f"✅ Your choice (**{choice}**) has been recorded.", ephemeral=True
        )
        # Both voted?
        if len(self._votes) == 2:
            await self._finalise()
        elif choice == "breakup":
            # One partner chose breakup → immediate
            await self._finalise()

    async def _finalise(self):
        self.stop()
        if "breakup" in self._votes.values():
            await db.pool.execute(
                "UPDATE relationships SET status='ended' WHERE id=$1", self._rel_id
            )
            msg = "💔 The relationship has ended due to repeated cheating."
        else:
            msg = "💚 Both partners chose to continue. Strikes have been reset."
        e = discord.Embed(description=msg, color=C_WARN)
        guild = self._bot.get_guild(self._guild_id)
        if guild:
            for uid in (self._cheater, self._partner):
                m = guild.get_member(uid)
                if m:
                    try: await m.send(embed=e)
                    except Exception: pass

    async def on_timeout(self):
        await self._finalise_timeout()

    async def _finalise_timeout(self):
        await db.pool.execute(
            "UPDATE relationships SET status='ended' WHERE id=$1", self._rel_id
        )
        e = discord.Embed(
            description="💔 No response received in 24 hours — the relationship has ended.",
            color=C_ERROR,
        )
        guild = self._bot.get_guild(self._guild_id)
        if guild:
            for uid in (self._cheater, self._partner):
                m = guild.get_member(uid)
                if m:
                    try: await m.send(embed=e)
                    except Exception: pass

    @discord.ui.button(label="💚 Continue", style=discord.ButtonStyle.success)
    async def continue_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._resolve(interaction, "continue")

    @discord.ui.button(label="💔 Break up", style=discord.ButtonStyle.danger)
    async def breakup_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._resolve(interaction, "breakup")



class RelationshipCog(commands.Cog, name="Relationships"):
    def __init__(self, bot):
        self.bot = bot
        self._cheating_counts: dict[tuple, int] = {}


    # ── Propose dating / marriage ────────────────────────────────
    @commands.group(name="propose", invoke_without_command=True)
    async def propose(self, ctx):
        await _err(ctx, "Usage: `!propose dating @user` or `!propose marriage @user <dowry>`")

    @propose.command(name="dating")
    async def propose_dating(self, ctx, member: discord.Member):
        if not await check_channel(ctx, "relationship"): return
        await self._send_proposal(ctx, member, "dating", 0)

    @propose.command(name="marriage")
    async def propose_marriage(self, ctx, member: discord.Member, dowry: int = 0):
        if not await check_channel(ctx, "relationship"): return
        if dowry < 0: return await _err(ctx, "Dowry can't be negative.")
        if dowry > 0:
            bal = await get_balance(ctx.guild.id, ctx.author.id)
            if bal < dowry: return await _err(ctx, f"You don't have **{dowry:,} coins** for the dowry.")
        await self._send_proposal(ctx, member, "marriage", dowry)

    async def _send_proposal(self, ctx, member, ptype, dowry):
        gid, uid, tid = ctx.guild.id, ctx.author.id, member.id
        if member.bot or member == ctx.author:
            return await _err(ctx, "Invalid target.")
        if await _rel_opted_out(gid, tid):
            return await _err(ctx, f"{member.display_name} has opted out of relationship commands.")
        if await _get_rel(gid, uid):
            return await _err(ctx, "You're already in a relationship. End it first.")
        if await _get_rel(gid, tid):
            return await _err(ctx, f"{member.display_name} is already in a relationship.")

        existing = await db.pool.fetchrow(
            "SELECT id FROM proposals WHERE guild_id=$1 AND proposer_id=$2 AND status='pending'",
            gid, uid
        )
        if existing: return await _err(ctx, "You already have a pending proposal.")

        expires = datetime.utcnow() + timedelta(seconds=PROPOSAL_TIMEOUT)
        if dowry > 0: await add_coins(gid, uid, -dowry)

        prenup_row = await db.pool.fetchrow("SELECT titles FROM economy WHERE guild_id=$1 AND user_id=$2", gid, uid)
        prenup = "prenup" in list(prenup_row["titles"] or []) if prenup_row else False

        label    = "💍 Marriage Proposal" if ptype == "marriage" else "💌 Dating Proposal"
        dowry_tx = f"\n\n💰 **Dowry offered:** {dowry:,} coins" if dowry > 0 else ""
        prenup_tx = "\n📜 **Prenup attached** — assets protected on divorce." if prenup and ptype == "marriage" else ""

        row = await db.pool.fetchrow("""
            INSERT INTO proposals (guild_id, proposer_id, target_id, type, dowry, expires_at)
            VALUES ($1,$2,$3,$4,$5,$6) RETURNING id
        """, gid, uid, tid, ptype, dowry, expires)

        view = ProposalView(uid, tid, row["id"], ptype, dowry, prenup)
        e = discord.Embed(
            title=label,
            description=(
                f"{ctx.author.mention} is asking {member.mention} to "
                f"{'get married' if ptype == 'marriage' else 'start dating'}!"
                f"{dowry_tx}{prenup_tx}\n\n*Expires in 1 hour.*"
            ),
            color=C_REL,
        )
        e.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=e, view=view)

    # ── View relationship ────────────────────────────────────────
    @commands.command(aliases=["relationship", "partner"])
    async def myrel(self, ctx):
        if not await check_channel(ctx, "relationship"): return
        gid, uid = ctx.guild.id, ctx.author.id
        rel = await _get_rel(gid, uid)
        if not rel:
            e = discord.Embed(title="💔 Single",
                description="You're not in a relationship.\n`!propose dating @user` to change that.",
                color=C_WARN)
            return await ctx.send(embed=e)

        partner_id = rel["user2_id"] if rel["user1_id"] == uid else rel["user1_id"]
        partner = ctx.guild.get_member(partner_id)
        pname = partner.mention if partner else f"<@{partner_id}>"
        since = rel["started_at"].strftime("%B %d, %Y") if rel["started_at"] else "Unknown"
        type_label = {"hookup": "💋 Hookup", "dating": "💘 Dating", "married": "💍 Married"}.get(rel["type"], "Unknown")

        e = discord.Embed(title=type_label, color=C_REL)
        e.add_field(name="Partner", value=pname,  inline=True)
        e.add_field(name="Since",   value=since,   inline=True)
        e.add_field(name="Prenup",  value="✅ Yes" if rel["prenup"] else "❌ No", inline=True)
        if rel["type"] == "married":
            shared = await db.pool.fetchval("SELECT balance FROM shared_accounts WHERE relationship_id=$1", rel["id"])
            e.add_field(name="🏦 Shared Account", value=f"**{shared or 0:,} coins**", inline=False)
        await ctx.send(embed=e)

    # ── Shared account ───────────────────────────────────────────
    @commands.group(name="shared", invoke_without_command=True)
    async def shared(self, ctx):
        if ctx.invoked_subcommand is not None: return
        await _err(ctx, "Use `!shared balance`, `!shared deposit <amt>`, or `!shared withdraw <amt>`")

    @shared.command(name="balance", aliases=["bal"])
    async def shared_balance(self, ctx):
        if not await check_channel(ctx, "relationship"): return
        rel = await _get_married_rel(ctx.guild.id, ctx.author.id)
        if not rel:
            return await _err(ctx, "You need to be married to use a shared account.")
        bal = await db.pool.fetchval("SELECT balance FROM shared_accounts WHERE relationship_id=$1", rel["id"]) or 0
        await ctx.send(embed=discord.Embed(title="🏦 Shared Account", description=f"**{bal:,} coins**", color=C_REL))

    @shared.command(name="deposit")
    async def shared_deposit(self, ctx, amount: int):
        if not await check_channel(ctx, "relationship"): return
        if amount <= 0: return await _err(ctx, "Amount must be positive.")
        gid, uid = ctx.guild.id, ctx.author.id
        rel = await _get_married_rel(gid, uid)
        if not rel:
            return await _err(ctx, "You need to be married to use a shared account.")
        bal = await get_balance(gid, uid)
        if bal < amount:
            return await _err(ctx, f"You only have **{bal:,} coins**.")
        await add_coins(gid, uid, -amount)
        await db.pool.execute("UPDATE shared_accounts SET balance=balance+$1 WHERE relationship_id=$2", amount, rel["id"])
        await ctx.send(embed=discord.Embed(description=f"💰 Deposited **{amount:,} coins** into shared account.", color=C_REL))

    @shared.command(name="withdraw")
    async def shared_withdraw(self, ctx, amount: int):
        if not await check_channel(ctx, "relationship"): return
        if amount <= 0: return await _err(ctx, "Amount must be positive.")
        gid, uid = ctx.guild.id, ctx.author.id
        rel = await _get_married_rel(gid, uid)
        if not rel:
            return await _err(ctx, "You need to be married to use a shared account.")
        shared_bal = await db.pool.fetchval("SELECT balance FROM shared_accounts WHERE relationship_id=$1", rel["id"]) or 0
        if shared_bal < amount: return await _err(ctx, f"Shared account only has **{shared_bal:,} coins**.")
        await add_coins(gid, uid, amount)
        await db.pool.execute("UPDATE shared_accounts SET balance=balance-$1 WHERE relationship_id=$2", amount, rel["id"])
        await ctx.send(embed=discord.Embed(description=f"💸 Withdrew **{amount:,} coins** from shared account.", color=C_REL))

    # ── End relationship / Divorce ───────────────────────────────
    @commands.group(name="endrel", invoke_without_command=True, aliases=["breakup"])
    async def endrel(self, ctx):
        """End a hookup or dating relationship instantly."""
        if ctx.invoked_subcommand is not None:
            return  # ← CRITICAL FIX: don't run base when subcommand matches
        if not await check_channel(ctx, "relationship"): return
        gid, uid = ctx.guild.id, ctx.author.id
        rel = await _get_rel(gid, uid)
        if not rel: return await _err(ctx, "You're not in a relationship.")
        if rel["type"] == "married":
            return await _err(ctx, "You're married. Use `!endrel court` to file for divorce.")
        await db.pool.execute("UPDATE relationships SET status='ended' WHERE id=$1", rel["id"])
        partner_id = rel["user2_id"] if rel["user1_id"] == uid else rel["user1_id"]
        partner = ctx.guild.get_member(partner_id)
        e = discord.Embed(
            title="💔 Relationship Ended",
            description=f"**{ctx.author.display_name}** ended the relationship with {partner.mention if partner else f'<@{partner_id}>'}.",
            color=C_WARN
        )
        await ctx.send(embed=e)

    @endrel.command(name="court")
    async def endrel_court(self, ctx, *, reason: str = "No reason given"):
        """File for divorce — sends case to the court channel."""
        if not await check_channel(ctx, "relationship"): return
        gid, uid = ctx.guild.id, ctx.author.id
        rel = await _get_married_rel(gid, uid)
        if not rel:
            return await _err(ctx, "You need to be married to file for divorce.")

        existing_case = await db.pool.fetchrow(
            "SELECT id FROM court_cases WHERE relationship_id=$1 AND status='open'", rel["id"]
        )
        if existing_case:
            return await _err(ctx, f"A court case is already open (Case `#{existing_case['id']}`).")

        partner_id = rel["user2_id"] if rel["user1_id"] == uid else rel["user1_id"]
        partner = ctx.guild.get_member(partner_id)
        pname = partner.mention if partner else f"<@{partner_id}>"

        case = await db.pool.fetchrow("""
            INSERT INTO court_cases (guild_id, relationship_id, filer_id, defendant_id, reason)
            VALUES ($1,$2,$3,$4,$5) RETURNING id
        """, gid, rel["id"], uid, partner_id, reason)

        court_ch_id = await get_court_channel(gid)
        court_ch = self.bot.get_channel(court_ch_id) if court_ch_id else ctx.channel

        e = discord.Embed(
            title=f"⚖️ Divorce Case #{case['id']} Filed",
            description=(
                f"**Filer:** {ctx.author.mention}\n"
                f"**Defendant:** {pname}\n"
                f"**Reason:** {reason}\n\n"
                f"A judge must use:\n"
                f"`!verdict {case['id']} divorce` — grant the divorce\n"
                f"`!verdict {case['id']} dismiss` — dismiss the case"
            ),
            color=C_ERROR, timestamp=datetime.utcnow(),
        )
        e.set_footer(text="Prenup applies if one was active at time of marriage.")
        msg = await court_ch.send(embed=e)
        await db.pool.execute("UPDATE court_cases SET message_id=$1 WHERE id=$2", msg.id, case["id"])
        if court_ch != ctx.channel:
            await ctx.send(f"📋 Divorce case filed in {court_ch.mention} as Case `#{case['id']}`.")

    # ── Verdict (judge only) ─────────────────────────────────────
    @commands.command()
    async def verdict(self, ctx, case_id: int, decision: str):
        """Judge only — !verdict <id> divorce|dismiss"""
        if not await is_judge(ctx.author):
            return await _err(ctx, "You are not a mod.")
        decision = decision.lower()
        if decision not in ("divorce", "dismiss"):
            return await _err(ctx, "Decision must be `divorce` or `dismiss`.")

        case = await db.pool.fetchrow("SELECT * FROM court_cases WHERE id=$1 AND status='open'", case_id)
        if not case: return await _err(ctx, f"No open case `#{case_id}`.")

        gid = case["guild_id"]
        rel = await db.pool.fetchrow("SELECT * FROM relationships WHERE id=$1", case["relationship_id"])

        if decision == "divorce":
            shared = await db.pool.fetchrow("SELECT balance FROM shared_accounts WHERE relationship_id=$1", rel["id"])
            if shared and shared["balance"] > 0:
                half = shared["balance"] // 2
                await add_coins(gid, rel["user1_id"], half)
                await add_coins(gid, rel["user2_id"], shared["balance"] - half)
                await db.pool.execute("UPDATE shared_accounts SET balance=0 WHERE relationship_id=$1", rel["id"])

            await db.pool.execute("UPDATE relationships SET status='divorced' WHERE id=$1", rel["id"])
            await db.pool.execute("UPDATE court_cases SET status='closed', judge_id=$1 WHERE id=$2", ctx.author.id, case_id)

            u1 = ctx.guild.get_member(rel["user1_id"])
            u2 = ctx.guild.get_member(rel["user2_id"])
            u1s = u1.mention if u1 else "<@" + str(rel["user1_id"]) + ">"
            u2s = u2.mention if u2 else "<@" + str(rel["user2_id"]) + ">"
            asset_line = "📜 *Prenup active — shared account split 50/50.*" if rel["prenup"] else "💸 *Shared account split 50/50.*"
            e = discord.Embed(
                title=f"⚖️ Case #{case_id} — Divorce Granted",
                description=(
                    f"Judge {ctx.author.mention} granted the divorce.\n"
                    f"{u1s} and {u2s} are now divorced.\n{asset_line}"
                ),
                color=C_WARN,
            )
        else:
            await db.pool.execute("UPDATE court_cases SET status='dismissed', judge_id=$1 WHERE id=$2", ctx.author.id, case_id)
            e = discord.Embed(
                title=f"⚖️ Case #{case_id} — Dismissed",
                description=f"Judge {ctx.author.mention} dismissed the case. The marriage continues.",
                color=C_SUCCESS,
            )
        await ctx.send(embed=e)

    # ── Cheating detection ───────────────────────────────────────
    async def log_cheating(self, guild_id, cheater_id, partner_id, target_id, guild):
        cheater = guild.get_member(cheater_id)
        partner = guild.get_member(partner_id)
        target  = guild.get_member(target_id)

        # Increment strikes in DB (persistent across restarts)
        new_count = await db.pool.fetchval("""
            UPDATE relationships
            SET cheating_strikes = cheating_strikes + 1
            WHERE guild_id=$1 AND (user1_id=$2 OR user2_id=$2) AND status='active'
            RETURNING cheating_strikes
        """, guild_id, cheater_id) or 1

        # DM the partner
        if partner:
            try:
                e = discord.Embed(
                    title="⚠️ Cheating Alert",
                    description=(
                        f"**{cheater.display_name if cheater else '???'}** used an NSFW command on "
                        f"**{target.display_name if target else '???'}** — not you.\n\n"
                        f"Strike **{new_count}/{CHEATING_WARN_AT}**."
                        + ("" if new_count < CHEATING_WARN_AT
                           else "\n⚖️ **Final strike — consequences incoming…**")
                    ),
                    color=C_ERROR,
                )
                await partner.send(embed=e)
            except Exception:
                pass

        if new_count < CHEATING_WARN_AT:
            return

        # Reset strikes
        await db.pool.execute("""
            UPDATE relationships SET cheating_strikes=0
            WHERE guild_id=$1 AND (user1_id=$2 OR user2_id=$2) AND status='active'
        """, guild_id, cheater_id)

        rel = await _get_rel(guild_id, cheater_id)
        if not rel: return

        if rel["type"] == "married":
            # Auto-file divorce in court channel
            existing = await db.pool.fetchrow(
                "SELECT id FROM court_cases WHERE relationship_id=$1 AND status='open'", rel["id"]
            )
            if not existing:
                case = await db.pool.fetchrow("""
                    INSERT INTO court_cases (guild_id, relationship_id, filer_id, defendant_id, reason)
                    VALUES ($1,$2,$3,$4,'Repeated cheating — auto-filed') RETURNING id
                """, guild_id, rel["id"], partner_id, cheater_id)
                court_ch_id = await get_court_channel(guild_id)
                court_ch = self.bot.get_channel(court_ch_id)
                if court_ch:
                    await court_ch.send(embed=discord.Embed(
                        title="⚖️ Auto-Filed Divorce",
                        description=(
                            f"<@{cheater_id}> has been auto-reported for repeated cheating.\n"
                            f"Case `#{case['id']}` — a judge must rule."
                        ),
                        color=C_ERROR,
                    ))

        elif rel["type"] == "dating":
            # Send both partners a Continue/Break up choice
            view = _CheatingResolutionView(rel["id"], guild_id, cheater_id, partner_id, self.bot)
            msg = (
                f"⚠️ **Relationship Check**\n\n"
                f"<@{cheater_id}> has been caught cheating **{CHEATING_WARN_AT}** times.\n"
                f"Both of you need to decide: **continue** the relationship or **break up**?\n\n"
                f"*No response in 24 hours = automatic break up.*"
            )
            e = discord.Embed(description=msg, color=C_WARN)
            for uid in (cheater_id, partner_id):
                m = guild.get_member(uid)
                if m:
                    try: await m.send(embed=e, view=view)
                    except Exception: pass

    # ── Opt-out ──────────────────────────────────────────────────
    @commands.command(aliases=["relopt"])
    async def reloptout(self, ctx, action: str = "out"):
        action = action.lower()
        gid, uid = ctx.guild.id, ctx.author.id
        if action == "out":
            await db.pool.execute("INSERT INTO rel_optout (guild_id,user_id) VALUES ($1,$2) ON CONFLICT DO NOTHING", gid, uid)
            await ctx.send("✅ Opted out of relationship commands.", delete_after=10)
        elif action == "in":
            await db.pool.execute("DELETE FROM rel_optout WHERE guild_id=$1 AND user_id=$2", gid, uid)
            await ctx.send("✅ Opted back into relationship commands.", delete_after=10)
        else:
            await _err(ctx, "Use `!reloptout out` or `!reloptout in`")
