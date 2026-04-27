# airi/games.py — Mini-games: Rock Paper Scissors + more
import discord
from discord.ext import commands
import random, asyncio
from utils import C_INFO, C_SUCCESS, C_WARN
import db

CHOICES = {"🪨": "rock", "📄": "paper", "✂️": "scissors"}
BEATS   = {"rock": "scissors", "scissors": "paper", "paper": "rock"}
EMOJI   = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}

RPS_OUTCOMES = {
    "win":  ["🏆 You win! {bot} can't beat that!",
             "✨ Victory! {you} vs {bot} — you take it!",
             "🎉 You beat Airi! Nice one~"],
    "lose": ["😤 Airi wins! {bot} beats {you}!",
             "🤖 Airi is unbeatable! {bot} crushes {you}!",
             "💀 Airi takes the W! {bot} vs {you}"],
    "tie":  ["🤝 It's a tie! {you} vs {bot} — no winner!",
             "😮 We matched! {you} and {bot} — draw!",
             "⚖️ Equal footing! Both chose {you}~"],
}


class RPSView(discord.ui.View):
    def __init__(self, ctx, bet: int = 0):
        super().__init__(timeout=60)
        self._ctx  = ctx
        self._bet  = bet
        self._done = False

    def _embed(self) -> discord.Embed:
        e = discord.Embed(
            title="🪨 Rock  📄 Paper  ✂️ Scissors",
            description=(
                f"**{self._ctx.author.display_name}** challenges Airi!\n\n"
                + (f"Bet: **{self._bet:,} 🪙**\n\n" if self._bet else "")
                + "Choose your move:"
            ),
            color=0x3498db,
        )
        e.set_footer(text="60 seconds to pick")
        return e

    async def _play(self, inter: discord.Interaction, player_choice: str):
        if inter.user.id != self._ctx.author.id:
            return await inter.response.send_message("Not your game!", ephemeral=True)
        if self._done:
            return await inter.response.send_message("Game already over.", ephemeral=True)
        self._done = True
        for c in self.children: c.disabled = True

        bot_choice = random.choice(list(CHOICES.values()))
        p = player_choice; b = bot_choice

        if p == b:
            outcome = "tie"
            coin_msg = ""
        elif BEATS[p] == b:
            outcome = "win"
            if self._bet:
                await db.pool.execute("""
                    UPDATE economy SET balance=balance+$1
                    WHERE guild_id=$2 AND user_id=$3
                """, self._bet, inter.guild_id, inter.user.id)
                coin_msg = f"\n+**{self._bet:,}** 🪙 won!"
            else:
                coin_msg = ""
        else:
            outcome = "lose"
            if self._bet:
                bal = await db.pool.fetchval(
                    "SELECT balance FROM economy WHERE guild_id=$1 AND user_id=$2",
                    inter.guild_id, inter.user.id) or 0
                loss = min(self._bet, bal)
                await db.pool.execute("""
                    UPDATE economy SET balance=GREATEST(0,balance-$1)
                    WHERE guild_id=$2 AND user_id=$3
                """, loss, inter.guild_id, inter.user.id)
                coin_msg = f"\n-**{loss:,}** 🪙 lost."
            else:
                coin_msg = ""

        msg = random.choice(RPS_OUTCOMES[outcome]).format(
            you=EMOJI[p], bot=EMOJI[b]
        )
        colors = {"win": 0x2ecc71, "lose": 0xe74c3c, "tie": 0xf39c12}
        e = discord.Embed(
            title="🎮 Rock Paper Scissors — Result",
            description=(
                f"**{inter.user.display_name}:** {EMOJI[p]}\n"
                f"**Airi:** {EMOJI[b]}\n\n"
                f"{msg}{coin_msg}"
            ),
            color=colors[outcome],
        )
        e.set_footer(text="Play again? /rps")
        await inter.response.edit_message(embed=e, view=self)

    @discord.ui.button(emoji="🪨", label="Rock",     style=discord.ButtonStyle.secondary, row=0)
    async def rock(self, inter, btn):     await self._play(inter, "rock")
    @discord.ui.button(emoji="📄", label="Paper",    style=discord.ButtonStyle.secondary, row=0)
    async def paper(self, inter, btn):    await self._play(inter, "paper")
    @discord.ui.button(emoji="✂️", label="Scissors", style=discord.ButtonStyle.secondary, row=0)
    async def scissors(self, inter, btn): await self._play(inter, "scissors")


class GamesCog(commands.Cog, name="Games"):
    def __init__(self, bot): self.bot = bot

    @commands.hybrid_command(
        name="rps",
        aliases=["rockpaperscissors","rock","roshambo"],
        description="Play Rock Paper Scissors against Airi (optionally bet coins)",
    )
    async def rps(self, ctx, bet: int = 0):
        """
        Play Rock Paper Scissors.
        Optionally bet coins — win to double, lose to lose.
        Examples: /rps  /rps 500
        """
        if bet < 0:
            return await ctx.send(embed=discord.Embed(
                description="❌ Bet cannot be negative.", color=0xe74c3c))
        if bet > 0:
            bal = await db.pool.fetchval(
                "SELECT balance FROM economy WHERE guild_id=$1 AND user_id=$2",
                ctx.guild.id, ctx.author.id) or 0
            if bal < bet:
                return await ctx.send(embed=discord.Embed(
                    description=f"❌ Need **{bet:,}** 🪙 to bet, you have **{bal:,}**.",
                    color=0xe74c3c))
        view = RPSView(ctx, bet)
        await ctx.send(embed=view._embed(), view=view)
