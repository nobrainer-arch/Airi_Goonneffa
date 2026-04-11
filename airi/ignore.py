# airi/ignore.py
import discord
from discord.ext import commands
import db
from utils import _err, C_INFO

async def _get_ignored(guild_id: int) -> set[str]:
    """Return set of command names that are ignored in this guild."""
    row = await db.pool.fetchrow(
        "SELECT value FROM guild_config WHERE guild_id=$1 AND key='ignored_commands'",
        guild_id
    )
    if not row or not row["value"]:
        return set()
    return set(row["value"].split(","))

async def _set_ignored(guild_id: int, commands_set: set[str]):
    """Store ignored commands as comma-separated string."""
    value = ",".join(sorted(commands_set)) if commands_set else ""
    await db.pool.execute("""
        INSERT INTO guild_config (guild_id, key, value)
        VALUES ($1, 'ignored_commands', $2)
        ON CONFLICT (guild_id, key) DO UPDATE SET value = $2
    """, guild_id, value)


class IgnoreCog(commands.Cog, name="Ignore"):
    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        """Global check: block ignored commands for non-admins."""
        if not ctx.guild:
            return True
        # Admins bypass ignore
        if ctx.author.guild_permissions.administrator:
            return True
        ignored = await _get_ignored(ctx.guild.id)
        cmd_name = ctx.command.qualified_name if ctx.command else ""
        if cmd_name in ignored:
            await _err(ctx, f"Command `{cmd_name}` is disabled in this server.")
            return False
        return True

    @commands.hybrid_group(name="ignore", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def ignore(self, ctx):
        """Manage ignored commands. Use `!ignore add <cmd>` or `!ignore remove <cmd>`."""
        await ctx.send_help(ctx.command)

    @ignore.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def ignore_add(self, ctx, command_name: str):
        """Ignore a command in this server (admin only)."""
        cmd = self.bot.get_command(command_name)
        if not cmd:
            return await _err(ctx, f"Command `{command_name}` not found.")
        ignored = await _get_ignored(ctx.guild.id)
        if command_name in ignored:
            return await _err(ctx, f"`{command_name}` is already ignored.")
        ignored.add(command_name)
        await _set_ignored(ctx.guild.id, ignored)
        await ctx.send(embed=discord.Embed(
            description=f"✅ Ignored command `{command_name}` in this server.",
            color=C_INFO
        ))

    @ignore.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def ignore_remove(self, ctx, command_name: str):
        """Stop ignoring a command."""
        ignored = await _get_ignored(ctx.guild.id)
        if command_name not in ignored:
            return await _err(ctx, f"`{command_name}` is not ignored.")
        ignored.remove(command_name)
        await _set_ignored(ctx.guild.id, ignored)
        await ctx.send(embed=discord.Embed(
            description=f"✅ Command `{command_name}` is no longer ignored.",
            color=C_INFO
        ))

    @ignore.command(name="list", aliases=["show"])
    @commands.has_permissions(manage_guild=True)
    async def ignore_list(self, ctx):
        """List all ignored commands in this server."""
        ignored = await _get_ignored(ctx.guild.id)
        if not ignored:
            desc = "No commands are ignored in this server."
        else:
            desc = "\n".join(f"`{cmd}`" for cmd in sorted(ignored))
        embed = discord.Embed(
            title="🚫 Ignored Commands",
            description=desc,
            color=C_INFO
        )
        await ctx.send(embed=embed)

    # Alias for users to see ignored commands without admin (read-only)
    @commands.hybrid_command(name="ignored")
    async def ignored_list(self, ctx):
        """Show which commands are ignored in this server (read-only)."""
        ignored = await _get_ignored(ctx.guild.id)
        if not ignored:
            desc = "No commands are ignored."
        else:
            desc = "\n".join(f"`{cmd}`" for cmd in sorted(ignored))
        embed = discord.Embed(
            title="🚫 Server Ignored Commands",
            description=desc,
            color=C_INFO
        )
        await ctx.send(embed=embed)