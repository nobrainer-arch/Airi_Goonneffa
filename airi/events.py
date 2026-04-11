# airi/events.py — Event listeners
import discord
from discord.ext import commands
from datetime import datetime, timedelta
import db
import config
from utils import C_ECONOMY

class EventsCog(commands.Cog, name="Events"):
    def __init__(self, bot): self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"Events cog ready")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot: return
        gid, uid = member.guild.id, member.id
        row = await db.pool.fetchrow(
            "SELECT last_daily, streak FROM economy WHERE guild_id=$1 AND user_id=$2", gid, uid
        )
        if not row: return
        if row["last_daily"]:
            elapsed = datetime.utcnow() - row["last_daily"]
            if elapsed < timedelta(hours=22): return
        streak = row["streak"] or 0
        desc = "Don't forget your **!daily** reward!"
        if streak > 1: desc += f"\n🔥 You're on a **{streak}-day streak!** Keep it going."
        e = discord.Embed(title="👋 Welcome back!", description=desc, color=C_ECONOMY)
        e.set_thumbnail(url=member.display_avatar.url)

        class DailyBtn(discord.ui.View):
            def __init__(self_): super().__init__(timeout=300)
            @discord.ui.button(label="💰 Claim Daily", style=discord.ButtonStyle.success)
            async def claim(self_, inter, btn):
                if inter.user.id != member.id:
                    return await inter.response.send_message("Not for you.", ephemeral=True)
                for i in self_.children: i.disabled = True
                await inter.response.edit_message(view=self_)
                await inter.followup.send("Use `!daily` in a bot channel to claim!", ephemeral=True)
        try:
            await member.send(embed=e, view=DailyBtn())
        except discord.Forbidden: pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild: return
        # AFK check
        gid, uid = message.guild.id, message.author.id
        afk_row = await db.pool.fetchrow(
            "SELECT reason FROM afk WHERE guild_id=$1 AND user_id=$2", gid, uid
        )
        if afk_row:
            await db.pool.execute("DELETE FROM afk WHERE guild_id=$1 AND user_id=$2", gid, uid)
            try:
                await message.channel.send(
                    f"👋 Welcome back {message.author.mention}! Your AFK has been removed.",
                    delete_after=8
                )
            except Exception: pass
        # Mention AFK users
        for m in message.mentions:
            row = await db.pool.fetchrow(
                "SELECT reason, set_at FROM afk WHERE guild_id=$1 AND user_id=$2", gid, m.id
            )
            if row:
                ago = datetime.utcnow() - row["set_at"]
                mins = int(ago.total_seconds() // 60)
                try:
                    await message.channel.send(
                        f"💤 **{m.display_name}** is AFK: {row['reason']} ({mins}m ago)",
                        delete_after=10
                    )
                except Exception: pass
