# airi/mod_panel.py — stub that re-exports ModPanelCog from goonneffa
# Airi's bot.py imports this; the actual implementation lives in goonneffa/mod_panel.py
# If you run only Airi standalone, remove this import from bot.py and skip this file.
try:
    from goonneffa.mod_panel import ModPanelCog
except ImportError:
    from discord.ext import commands
    class ModPanelCog(commands.Cog):
        """Placeholder — mod panel runs on Goonneffa bot only."""
        pass
