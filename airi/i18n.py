# airi/i18n.py — Internationalisation middleware
#
# Problem: auto_tr() exists in translator.py but is called nowhere.
#          Every cog uses ctx.send(embed=e) directly, bypassing the lang pref.
#
# Solution: a thin wrapper layer.
#   - tr_embed(embed, lang)  → returns a NEW embed with all text fields translated
#   - tr_send(ctx, embed)    → fetches user lang, translates embed, sends it
#   - tr_str(text, gid, uid) → translate a raw string for a user
#
# Cogs that need localisation import tr_send and call it instead of ctx.send.
# Cogs that don't care are untouched — no big bang rewrite required.
#
# Button labels are NOT translated here — Discord renders buttons on the client
# and there's no per-user label API. Only embed text is translated.
#
# Translation is fire-and-forget: if it fails or times out the original English
# is returned so the bot never goes silent.

import asyncio
import discord
from typing import Optional

# Lazy import to avoid circular — translator imports db which imports nothing circular
async def _get_lang(gid: int, uid: int) -> Optional[str]:
    from .translator import get_lang
    try:
        return await get_lang(gid, uid)
    except Exception:
        return None


async def _translate(text: str, lang: str) -> str:
    from .translator import translate
    try:
        result, _ = await asyncio.wait_for(translate(text, lang), timeout=5.0)
        return result
    except Exception:
        return text


async def tr_str(text: str, gid: int, uid: int) -> str:
    """Translate a raw string for a specific user. Returns original on failure."""
    lang = await _get_lang(gid, uid)
    if not lang or lang == "en":
        return text
    return await _translate(text, lang)


async def tr_embed(embed: discord.Embed, lang: str) -> discord.Embed:
    """
    Return a new Embed with all text fields translated to `lang`.
    Uses asyncio.gather so all translations run in PARALLEL — ~5s max total
    instead of 5s × N fields sequentially (which would timeout slash commands).
    """
    if not lang or lang == "en":
        return embed

    async def _t(text: str | None) -> str | None:
        if not text:
            return text
        stripped = text.strip()
        if not stripped or len(stripped) <= 3:
            return text
        return await _translate(text, lang)

    # Collect all strings to translate
    to_translate = [
        embed.title or "",
        embed.description or "",
        (embed.footer.text if embed.footer and embed.footer.text else ""),
    ]
    field_names  = [f.name  for f in embed.fields]
    field_values = [f.value for f in embed.fields]
    all_texts = to_translate + field_names + field_values

    # Run all translations in parallel
    results = await asyncio.gather(*[_t(t) for t in all_texts])

    t_title, t_desc, t_footer = results[0], results[1], results[2]
    split    = 3 + len(field_names)
    t_names  = results[3:3 + len(field_names)]
    t_values = results[3 + len(field_names):]

    new = discord.Embed(color=embed.color)
    if t_title:       new.title       = t_title
    if t_desc:        new.description = t_desc
    for i, f in enumerate(embed.fields):
        new.add_field(name=t_names[i] or f.name, value=t_values[i] or f.value, inline=f.inline)
    if t_footer and embed.footer:
        new.set_footer(text=t_footer, icon_url=embed.footer.icon_url)
    if embed.author:
        new.set_author(name=embed.author.name or "", icon_url=embed.author.icon_url, url=embed.author.url)
    if embed.thumbnail:
        new.set_thumbnail(url=embed.thumbnail.url)
    if embed.image:
        new.set_image(url=embed.image.url)
    if embed.timestamp:
        new.timestamp = embed.timestamp
    return new


async def tr_send(
    ctx,
    embed: discord.Embed,
    *,
    view:        Optional[discord.ui.View] = None,
    ephemeral:   bool = False,
    attachments: list = None,
    followup:    bool = False,
) -> None:
    """
    Drop-in replacement for ctx.send(embed=...).
    For slash commands: auto-defers the interaction before translating
    so we don't hit Discord's 3-second response window during translation.
    """
    gid = ctx.guild.id if ctx.guild else 0
    uid = ctx.author.id if hasattr(ctx, "author") else (ctx.user.id if hasattr(ctx, "user") else 0)

    lang = await _get_lang(gid, uid)

    # Detect slash command context — defer BEFORE translating to avoid timeout
    is_slash = hasattr(ctx, "interaction") and ctx.interaction is not None
    if is_slash and lang and lang != "en":
        try:
            if not ctx.interaction.response.is_done():
                await ctx.interaction.response.defer(ephemeral=ephemeral)
            embed = await tr_embed(embed, lang)
            kwargs: dict = {"embed": embed}
            if view:        kwargs["view"]        = view
            if attachments: kwargs["attachments"] = attachments
            await ctx.interaction.followup.send(**kwargs, ephemeral=ephemeral)
            return
        except Exception:
            pass  # Fall through to normal send on any error

    if lang and lang != "en":
        embed = await tr_embed(embed, lang)

    kwargs: dict = {"embed": embed}
    if view:        kwargs["view"]      = view
    if ephemeral:   kwargs["ephemeral"] = True
    if attachments: kwargs["attachments"] = attachments

    if followup and hasattr(ctx, "followup"):
        await ctx.followup.send(**kwargs)
    elif hasattr(ctx, "send"):
        await ctx.send(**kwargs)
    elif hasattr(ctx, "response"):
        await ctx.response.send_message(**kwargs)


async def tr_inter_send(
    inter: discord.Interaction,
    embed: discord.Embed,
    *,
    view:      Optional[discord.ui.View] = None,
    ephemeral: bool = False,
) -> None:
    """
    Same as tr_send but for raw discord.Interaction objects (button callbacks, etc).
    Uses inter.user instead of ctx.author.
    """
    gid = inter.guild_id or 0
    uid = inter.user.id

    lang = await _get_lang(gid, uid)
    if lang and lang != "en":
        embed = await tr_embed(embed, lang)

    kwargs: dict = {"embed": embed}
    if view:      kwargs["view"]      = view
    if ephemeral: kwargs["ephemeral"] = True

    if inter.response.is_done():
        await inter.followup.send(**kwargs)
    else:
        await inter.response.send_message(**kwargs)


async def tr_edit(
    inter: discord.Interaction,
    embed: discord.Embed,
    *,
    view:        Optional[discord.ui.View] = None,
    attachments: list = None,
) -> None:
    """
    Translate and then edit the original response of an interaction.
    Used in button callbacks that call inter.edit_original_response().
    """
    gid = inter.guild_id or 0
    uid = inter.user.id

    lang = await _get_lang(gid, uid)
    if lang and lang != "en":
        embed = await tr_embed(embed, lang)

    kwargs: dict = {"embed": embed}
    if view:        kwargs["view"]        = view
    if attachments: kwargs["attachments"] = attachments

    await inter.edit_original_response(**kwargs)
