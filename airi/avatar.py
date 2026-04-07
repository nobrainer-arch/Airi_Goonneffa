# airi/avatar.py
import discord
from discord.ext import commands
from utils import _err


class AvatarView(discord.ui.View):
    def __init__(self, member: discord.Member):
        super().__init__(timeout=120)
        self.member = member
        # Add download buttons
        av = member.display_avatar
        self.add_item(discord.ui.Button(
            label="PNG", style=discord.ButtonStyle.link,
            url=av.replace(format="png", size=1024).url, emoji="🖼️"
        ))
        self.add_item(discord.ui.Button(
            label="JPG", style=discord.ButtonStyle.link,
            url=av.replace(format="jpg", size=1024).url, emoji="📷"
        ))
        self.add_item(discord.ui.Button(
            label="WEBP", style=discord.ButtonStyle.link,
            url=av.replace(format="webp", size=1024).url, emoji="💾"
        ))
        # Global avatar button if they have a server-specific one
        if member.guild_avatar and member.avatar:
            self.add_item(discord.ui.Button(
                label="View global avatar", style=discord.ButtonStyle.link,
                url=member.avatar.replace(format="png", size=1024).url, emoji="🌐"
            ))


class AvatarCog(commands.Cog, name="Avatar"):
    def __init__(self, bot): self.bot = bot

    @commands.command(aliases=["av", "pfp"])
    async def avatar(self, ctx, member: discord.Member = None):
        """View someone's avatar with download links."""
        target = member or ctx.author
        av = target.display_avatar

        e = discord.Embed(
            title=f"Avatar of {target.display_name}",
            color=target.color if target.color.value else 0x7289da,
        )

        # Show decoration ID if they have one
        if hasattr(target, "avatar_decoration") and target.avatar_decoration:
            e.add_field(
                name="Avatar decoration",
                value=str(target.avatar_decoration.asset),
                inline=False,
            )

        # Server-specific vs global avatar note
        if target.guild_avatar:
            e.set_footer(text=f"Showing server avatar for {target.display_name}")
        else:
            e.set_footer(text=f"Behind every avatar, there's a world to discover.")

        e.set_image(url=av.replace(size=1024).url)
        await ctx.send(embed=e, view=AvatarView(target))
