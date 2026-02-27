"""Discord bot: slash commands for managing RSS/Atom feeds."""

from __future__ import annotations

import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from cordfeeder.config import Config
from cordfeeder.database import Database
from cordfeeder.formatter import format_item_embed
from cordfeeder.parser import extract_feed_metadata, parse_feed
from cordfeeder.poller import Poller

logger = logging.getLogger(__name__)

_CMD_TIMEOUT = aiohttp.ClientTimeout(total=15)


def has_feed_manager_role(interaction: discord.Interaction, role_name: str) -> bool:
    """Check whether the interacting user has the required role (case-sensitive)."""
    return any(role.name == role_name for role in interaction.user.roles)


class FeedCog(commands.Cog):
    """Slash command group for managing RSS/Atom feeds."""

    feed_group = app_commands.Group(name="feed", description="Manage RSS feeds")

    def __init__(self, bot: CordFeederBot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # /feed add <url> [channel]
    # ------------------------------------------------------------------

    @feed_group.command(name="add", description="Subscribe to an RSS/Atom feed")
    @app_commands.describe(
        url="Feed URL to subscribe to",
        channel="Channel to post items in (defaults to current)",
    )
    async def feed_add(
        self,
        interaction: discord.Interaction,
        url: str,
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not has_feed_manager_role(interaction, self.bot.config.feed_manager_role):
            await interaction.response.send_message(
                "You need the **{}** role to use this command.".format(
                    self.bot.config.feed_manager_role
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        target_channel = channel or interaction.channel

        try:
            async with self.bot.poller._session.get(
                url, timeout=_CMD_TIMEOUT
            ) as resp:
                body = await resp.text()

            items = parse_feed(body)
            metadata = extract_feed_metadata(body)
        except Exception as exc:
            await interaction.followup.send(
                f"Failed to fetch or parse feed: {exc}", ephemeral=True
            )
            return

        feed_name = metadata.title or url
        feed_id = await self.bot.db.add_feed(
            url=url,
            name=feed_name,
            channel_id=target_channel.id,
            guild_id=interaction.guild_id,
            added_by=interaction.user.id,
        )

        # Post initial items (most recent N, oldest-first)
        count = self.bot.config.initial_items_count
        initial = items[:count] if items else []
        for item in reversed(initial):
            embed = format_item_embed(
                item=item,
                feed_name=feed_name,
                feed_url=url,
                feed_id=feed_id,
                feed_icon_url=metadata.image_url,
            )
            msg = await target_channel.send(embed=embed)
            await self.bot.db.record_posted_item(feed_id, item.guid, message_id=msg.id)

        # Mark all parsed items as posted so the poller only picks up
        # truly new items going forward.
        for item in items:
            await self.bot.db.record_posted_item(feed_id, item.guid)

        await interaction.followup.send(
            f"Subscribed to **{feed_name}** (ID `{feed_id}`) in {target_channel.mention}.",
            ephemeral=True,
        )
        logger.info(
            "feed added via command",
            extra={"feed_id": feed_id, "url": url, "guild_id": interaction.guild_id},
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
        if not has_feed_manager_role(interaction, self.bot.config.feed_manager_role):
            await interaction.response.send_message(
                "You need the **{}** role to use this command.".format(
                    self.bot.config.feed_manager_role
                ),
                ephemeral=True,
            )
            return

        feed = await self.bot.db.get_feed(id)
        if not feed or feed["guild_id"] != interaction.guild_id:
            await interaction.response.send_message(
                f"Feed `{id}` not found in this server.", ephemeral=True
            )
            return

        await self.bot.db.remove_feed(id)
        await interaction.response.send_message(
            f"Removed feed **{feed['name']}** (ID `{id}`).", ephemeral=True
        )
        logger.info(
            "feed removed via command",
            extra={"feed_id": id, "guild_id": interaction.guild_id},
        )

    # ------------------------------------------------------------------
    # /feed list
    # ------------------------------------------------------------------

    @feed_group.command(name="list", description="List all feeds for this server")
    async def feed_list(self, interaction: discord.Interaction) -> None:
        feeds = await self.bot.db.list_feeds(interaction.guild_id)

        if not feeds:
            await interaction.response.send_message(
                "No feeds configured.", ephemeral=True
            )
            return

        lines: list[str] = []
        for f in feeds:
            interval_min = (f.get("poll_interval") or 900) // 60
            line = (
                f"**{f['name']}** (ID `{f['id']}`)\n"
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

    @feed_group.command(name="preview", description="Preview the latest item from a feed")
    @app_commands.describe(url_or_id="Feed URL or feed ID to preview")
    async def feed_preview(
        self,
        interaction: discord.Interaction,
        url_or_id: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        # Resolve feed ID to URL if numeric
        feed_url = url_or_id
        feed_name_override = None
        if url_or_id.isdigit():
            feed = await self.bot.db.get_feed(int(url_or_id))
            if not feed or feed["guild_id"] != interaction.guild_id:
                await interaction.followup.send(
                    f"Feed `{url_or_id}` not found in this server.", ephemeral=True
                )
                return
            feed_url = feed["url"]
            feed_name_override = feed["name"]

        try:
            async with self.bot.poller._session.get(
                feed_url, timeout=_CMD_TIMEOUT
            ) as resp:
                body = await resp.text()

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

        footer = "Preview · not subscribed" if url_or_id == feed_url else "Preview"
        embed.set_footer(text=footer)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /feed config
    # ------------------------------------------------------------------

    @feed_group.command(name="config", description="Show bot status and configuration")
    async def feed_config(self, interaction: discord.Interaction) -> None:
        if not has_feed_manager_role(interaction, self.bot.config.feed_manager_role):
            await interaction.response.send_message(
                "You need the **{}** role to use this command.".format(
                    self.bot.config.feed_manager_role
                ),
                ephemeral=True,
            )
            return

        feeds = await self.bot.db.list_feeds(interaction.guild_id)
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

        super().__init__(command_prefix="!", intents=intents)

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
