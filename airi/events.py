# airi/events.py
import discord
from discord.ext import commands
import asyncio
import config
import db
from .gender import set_gender, reset_gender

# Command intent hints — word → canonical command
INTENT_MAP: dict[str, str] = {
    "profile":    "!profile", "prof":       "!profile",
    "balance":    "!balance", "bal":        "!balance", "coins":  "!balance",
    "daily":      "!daily",
    "work":       "!work",
    "crime":      "!crime",
    "shop":       "!shop",
    "inventory":  "!inventory", "inv":       "!inventory",
    "gacha":      "!gacha",  "roll":       "!gacha",
    "claim":      "!claim",
    "waifu":      "!waifu",  "harem":      "!mywaifu",  "mywaifu": "!mywaifu",
    "rep":        "!rep",
    "leaderboard":"!leaderboard", "lb":     "!leaderboard",
    "propose":    "!propose dating @user",
    "marry":      "!propose marriage @user",
    "divorce":    "!endrel court",
    "breakup":    "!endrel",
    "auction":    "!ah list", "ah":         "!ah list",
    "order":      "!orderbook", "orders":   "!orderbook",
    "help":       "!help",
    "setup":      "!setup",
    "config":     "!config show",
    "rank":       "!rank",
    "xp":         "!rank",
    "level":      "!rank",
    "pay":        "!pay @user <amount>",
}


def _detect_intent(text: str) -> str | None:
    """Return a suggested command if text mentions one of our features."""
    lower = text.lower()
    for word, cmd in INTENT_MAP.items():
        if word in lower.split() or lower.startswith(word):
            return cmd
    return None


class EventsCog(commands.Cog, name="Events"):
    def __init__(self, bot, commands_data):
        self.bot           = bot
        self.commands_data = commands_data
        self.spam_tracker: dict[str, dict] = {}

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"Airi online as {self.bot.user}")
        from .commands import restore_antinoobify_listeners
        asyncio.create_task(restore_antinoobify_listeners(self.bot, self.commands_data))

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Remind users to claim their daily when they join/rejoin."""
        if member.bot: return
        gid, uid = member.guild.id, member.id
        from datetime import datetime, timedelta
        import db
        row = await db.pool.fetchrow(
            "SELECT last_daily, streak FROM economy WHERE guild_id=$1 AND user_id=$2", gid, uid
        )
        if not row: return  # brand new user, no data yet

        # Check if they haven't claimed today
        if row["last_daily"]:
            elapsed = datetime.utcnow() - row["last_daily"]
            if elapsed < timedelta(hours=22):
                return  # already claimed recently
        
        streak = row["streak"] or 0
        desc = "Daily reminder! Use **!daily** to collect coins."
        if streak > 1:
            desc += f"\n🔥 {streak}-day streak! Keep it going."
        e = discord.Embed(description=desc, color=0xf1c40f)
        e.set_thumbnail(url=member.display_avatar.url)

        class DailyButton(discord.ui.View):
            def __init__(self_): super().__init__(timeout=300)
            @discord.ui.button(label="💰 Claim Daily", style=discord.ButtonStyle.success)
            async def claim(self_, inter, btn):
                if inter.user.id != member.id:
                    return await inter.response.send_message("Not for you.", ephemeral=True)
                for i in self_.children: i.disabled = True
                await inter.response.edit_message(view=self_)
                # Invoke daily command
                ctx = await self.bot.get_context(inter.message) if hasattr(inter, "message") else None
                if ctx:
                    await ctx.invoke(self.bot.get_command("daily"))
                else:
                    await inter.followup.send("Use `!daily` in a bot channel!", ephemeral=True)

        try:
            await member.send(embed=e, view=DailyButton())
        except discord.Forbidden:
            pass  # DMs disabled — silently skip

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        user_id       = str(message.author.id)
        content_lower = message.content.lower().strip()
        content       = message.content.strip()

        # ── Spam block ──────────────────────────────────────────
        last = self.spam_tracker.get(user_id)
        if last and last["msg"] == content_lower:
            last["count"] += 1
        else:
            self.spam_tracker[user_id] = {"msg": content_lower, "count": 1}
        if self.spam_tracker[user_id]["count"] >= config.SPAM_LIMIT:
            try: await message.delete()
            except discord.HTTPException: pass
            return

        # ── @me gender shortcut (legacy) ────────────────────────
        if content_lower.startswith("@me"):
            parts = content_lower.split()
            if len(parts) == 2 and parts[1] in ("g", "b"):
                await set_gender(user_id, parts[1])
                label = "👩 Girl" if parts[1] == "g" else "👦 Boy"
                await message.channel.send(f"{message.author.mention} set as {label}", delete_after=8)
                return
            if len(parts) == 2 and parts[1] == "reset":
                await reset_gender(user_id)
                await message.channel.send(f"{message.author.mention} reset", delete_after=8)
                return

        # ── "airi ..." prefix ───────────────────────────────────
        if content_lower.startswith("airi "):
            rest = content[5:].strip()
            if rest.startswith("!"): rest = rest[1:]
            message.content = "!" + rest
            await self.bot.process_commands(message)
            return

        # ── Intent detector — "!/ airi" typo or casual mention ──
        # Triggers when message has "!/" or mentions airi but isn't a real command
        is_slash_typo = content.startswith("!/")
        mentions_airi = "airi" in content_lower and not content_lower.startswith("airi ")
        is_real_cmd   = content.startswith("!")

        if (is_slash_typo or mentions_airi) and not is_real_cmd:
            # Extract the rest of the message to detect intent
            probe = content[2:] if is_slash_typo else content
            suggestion = _detect_intent(probe)
            if suggestion:
                try:
                    await message.reply(
                        f"💡 Did you mean **`{suggestion}`**?",
                        delete_after=15,
                        mention_author=False,
                    )
                except discord.HTTPException:
                    pass
            # Don't process as a command — it wasn't one
            return

        await self.bot.process_commands(message)
