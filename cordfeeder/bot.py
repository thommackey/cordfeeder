"""Discord bot: slash commands for managing RSS/Atom feeds."""

from __future__ import annotations

import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from cordfeeder.config import Config
from cordfeeder.database import Database
from cordfeeder.discovery import FeedNotFoundError, discover_feed_url
from cordfeeder.formatter import format_item_message, sanitise_mentions
from cordfeeder.parser import extract_feed_metadata, parse_feed
from cordfeeder.poller import MAX_FEED_BYTES, Poller

logger = logging.getLogger(__name__)

_CMD_TIMEOUT = aiohttp.ClientTimeout(total=15)


def _safe_name(name: str) -> str:
    """Sanitise a feed name for use in command responses."""
    return sanitise_mentions(name).replace("\n", " ").replace("\r", "")


def _guild_id(interaction: discord.Interaction) -> int:
    """Extract guild_id from a guild-only interaction."""
    assert interaction.guild_id is not None
    return interaction.guild_id


class FeedCog(commands.Cog):
    """Slash command group for managing RSS/Atom feeds."""

    feed_group = app_commands.Group(
        name="feed",
        description="Manage RSS feeds",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: CordFeederBot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # /feed add <url> [channel]
    # ------------------------------------------------------------------

    @feed_group.command(
        name="add",
        description="Subscribe to a feed (URL) or move an existing feed (ID)",
    )
    @app_commands.describe(
        url_or_id="Feed URL to subscribe to, or feed ID to move",
        channel="Channel to post items in (defaults to current)",
    )
    async def feed_add(
        self,
        interaction: discord.Interaction,
        url_or_id: str,
        channel: discord.TextChannel | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = _guild_id(interaction)
        target_channel: discord.TextChannel = channel or interaction.channel  # type: ignore[assignment]

        # If it's a feed ID, just move the existing feed
        if url_or_id.isdigit():
            feed = await self.bot.db.get_feed(int(url_or_id))
            if not feed or feed["guild_id"] != guild:
                await interaction.followup.send(
                    f"Feed `{url_or_id}` not found in this server.", ephemeral=True
                )
                return
            old_channel_id = feed["channel_id"]
            await self.bot.db.update_feed_channel(feed["id"], target_channel.id)
            safe = _safe_name(feed["name"])
            ch = target_channel.mention
            fid = feed["id"]
            if old_channel_id == target_channel.id:
                msg = f"**{safe}** (ID `{fid}`) is already in {ch}."
            else:
                msg = f"Moved **{safe}** (ID `{fid}`) to {ch}."
            await interaction.followup.send(msg, ephemeral=True)
            return

        # It's a URL — discover the actual feed URL, then fetch and validate
        http = self.bot.poller._http
        try:
            feed_url = await discover_feed_url(url_or_id, http, _CMD_TIMEOUT)
        except FeedNotFoundError:
            await interaction.followup.send(
                "No RSS/Atom feed found at that URL.", ephemeral=True
            )
            return

        try:
            async with http.get(feed_url, timeout=_CMD_TIMEOUT) as resp:
                raw = await resp.content.read(MAX_FEED_BYTES + 1)
                if len(raw) > MAX_FEED_BYTES:
                    raise ValueError("Feed response too large")
                encoding = resp.get_encoding() or "utf-8"
                body = raw.decode(encoding, errors="replace")

            items = parse_feed(body)
            metadata = extract_feed_metadata(body)
        except Exception as exc:
            await interaction.followup.send(
                f"Failed to fetch or parse feed: {exc}", ephemeral=True
            )
            return

        feed_name = metadata.title or feed_url
        safe = _safe_name(feed_name)

        # Check if this feed already exists on this server
        existing = await self.bot.db.get_feed_by_url(feed_url, guild)
        if existing:
            feed_id = existing["id"]
            old_channel_id = existing["channel_id"]
            await self.bot.db.update_feed_channel(feed_id, target_channel.id)
            ch = target_channel.mention
            if old_channel_id == target_channel.id:
                msg = f"**{safe}** (ID `{feed_id}`) is already in {ch}."
            else:
                msg = f"Moved **{safe}** (ID `{feed_id}`) to {ch}."
            await interaction.followup.send(msg, ephemeral=True)
            return

        feed_id = await self.bot.db.add_feed(
            url=feed_url,
            name=feed_name,
            channel_id=target_channel.id,
            guild_id=guild,
            added_by=interaction.user.id,
        )

        # Mark all parsed items as posted FIRST so the poller only picks up
        # truly new items going forward — even if initial posting below fails.
        for item in items:
            await self.bot.db.record_posted_item(feed_id, item.guid)

        # Post initial items (most recent N, oldest-first)
        count = self.bot.config.initial_items_count
        initial = items[:count] if items else []
        for item in reversed(initial):
            content = format_item_message(
                item=item,
                feed_name=feed_name,
                feed_id=feed_id,
            )
            try:
                await target_channel.send(content)
            except Exception:
                logger.warning(
                    "failed to post initial item",
                    extra={"feed_id": feed_id, "guid": item.guid},
                )
                break

        await interaction.followup.send(
            f"Subscribed to **{safe}** (ID `{feed_id}`) in {target_channel.mention}.",
            ephemeral=True,
        )
        logger.info(
            "feed added via command",
            extra={"feed_id": feed_id, "url": feed_url, "guild_id": guild},
        )

    # ------------------------------------------------------------------
    # /feed remove <id>
    # ------------------------------------------------------------------

    @feed_group.command(name="remove", description="Unsubscribe from a feed")
    @app_commands.describe(id="Feed ID to remove")
    async def feed_remove(
        self,
        interaction: discord.Interaction,
        id: int,
    ) -> None:
        guild = _guild_id(interaction)
        feed = await self.bot.db.get_feed(id)
        if not feed or feed["guild_id"] != guild:
            await interaction.response.send_message(
                f"Feed `{id}` not found in this server.", ephemeral=True
            )
            return

        await self.bot.db.remove_feed(id)
        await interaction.response.send_message(
            f"Removed feed **{_safe_name(feed['name'])}** (ID `{id}`).",
            ephemeral=True,
        )
        logger.info(
            "feed removed via command",
            extra={"feed_id": id, "guild_id": guild},
        )

    # ------------------------------------------------------------------
    # /feed list
    # ------------------------------------------------------------------

    @feed_group.command(name="list", description="List all feeds for this server")
    async def feed_list(self, interaction: discord.Interaction) -> None:
        feeds = await self.bot.db.list_feeds(_guild_id(interaction))

        if not feeds:
            await interaction.response.send_message(
                "No feeds configured.", ephemeral=True
            )
            return

        lines: list[str] = []
        for f in feeds:
            interval_min = (f.get("poll_interval") or 900) // 60
            line = (
                f"**{_safe_name(f['name'])}** (ID `{f['id']}`)\n"
                f"  <#{f['channel_id']}> · every {interval_min}m"
            )
            errors = f.get("consecutive_errors", 0)
            if errors:
                line += f" · {errors} error(s)"
            lines.append(line)

        embed = discord.Embed(
            title="Configured feeds",
            description="\n\n".join(lines),
            colour=discord.Colour.blurple(),
        )
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------
    # /feed preview <url>
    # ------------------------------------------------------------------

    @feed_group.command(
        name="preview", description="Preview the latest item from a feed"
    )
    @app_commands.describe(url_or_id="Feed URL or feed ID to preview")
    async def feed_preview(
        self,
        interaction: discord.Interaction,
        url_or_id: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = _guild_id(interaction)

        # Resolve feed ID to URL if numeric
        feed_url = url_or_id
        feed_name_override = None
        if url_or_id.isdigit():
            feed = await self.bot.db.get_feed(int(url_or_id))
            if not feed or feed["guild_id"] != guild:
                await interaction.followup.send(
                    f"Feed `{url_or_id}` not found in this server.", ephemeral=True
                )
                return
            feed_url = feed["url"]
            feed_name_override = feed["name"]

        # Discover actual feed URL if not already resolved from DB
        http = self.bot.poller._http
        if not feed_name_override:
            try:
                feed_url = await discover_feed_url(feed_url, http, _CMD_TIMEOUT)
            except FeedNotFoundError:
                await interaction.followup.send(
                    "No RSS/Atom feed found at that URL.", ephemeral=True
                )
                return

        try:
            async with http.get(feed_url, timeout=_CMD_TIMEOUT) as resp:
                raw = await resp.content.read(MAX_FEED_BYTES + 1)
                if len(raw) > MAX_FEED_BYTES:
                    raise ValueError("Feed response too large")
                encoding = resp.get_encoding() or "utf-8"
                body = raw.decode(encoding, errors="replace")

            items = parse_feed(body)
            metadata = extract_feed_metadata(body)
        except Exception as exc:
            await interaction.followup.send(
                f"Failed to fetch or parse feed: {exc}", ephemeral=True
            )
            return

        if not items:
            await interaction.followup.send(
                "Feed parsed but contains no items.", ephemeral=True
            )
            return

        display_name = feed_name_override or metadata.title
        item = items[0]
        embed = discord.Embed(
            title=item.title,
            url=item.link,
            description=item.summary or None,
            colour=discord.Colour.light_grey(),
        )
        if display_name:
            embed.set_author(name=display_name)
        if item.image_url:
            embed.set_thumbnail(url=item.image_url)

        footer = "Preview" if feed_name_override else "Preview · not subscribed"
        embed.set_footer(text=footer)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /feed config
    # ------------------------------------------------------------------

    @feed_group.command(name="config", description="Show bot status and configuration")
    async def feed_config(self, interaction: discord.Interaction) -> None:
        feeds = await self.bot.db.list_feeds(_guild_id(interaction))
        total = len(feeds)
        errored = sum(1 for f in feeds if f.get("consecutive_errors", 0) > 0)
        interval_min = self.bot.config.default_poll_interval // 60

        await interaction.response.send_message(
            f"**Bot status**\n"
            f"Total feeds: {total}\n"
            f"Errored feeds: {errored}\n"
            f"Default interval: {interval_min}m",
            ephemeral=True,
        )


class CordFeederBot(commands.Bot):
    """Main bot class for CordFeeder."""

    def __init__(self, config: Config, db: Database) -> None:
        intents = discord.Intents.default()
        # message_content not needed — we only use slash commands
        intents.message_content = False

        super().__init__(
            command_prefix="!",
            intents=intents,
            allowed_mentions=discord.AllowedMentions.none(),
        )

        self.config = config
        self.db = db
        self.poller = Poller(config=config, db=db, bot=self)

    async def setup_hook(self) -> None:
        cog = FeedCog(self)
        await self.add_cog(cog)
        await self.tree.sync()
        await self.poller.start()
        logger.info("bot setup complete")

    async def close(self) -> None:
        await self.poller.stop()
        await self.db.close()
        await super().close()
        logger.info("bot shut down")
