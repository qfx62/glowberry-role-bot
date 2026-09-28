"""GlowBerry SMP role panels and announcement bot. Python 3.11+, discord.py 2.6+."""
from __future__ import annotations

# ---------------------------- Configuration ----------------------------
import asyncio
import os
import sqlite3
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = int(os.getenv("GUILD_ID", "0"))  # Optional; use your server ID for fast command sync.
DATABASE = Path(__file__).with_name("role_panels.sqlite3")
LOGO = Path(__file__).parent / "assets" / "glowberry-logo.png"
DEFAULT_COLOR = 0xF5B42D
NO_MENTIONS = discord.AllowedMentions.none()


# ----------------------------- Database -------------------------------
class Store:
    def __init__(self):
        self.db = sqlite3.connect(DATABASE)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS panels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                style TEXT NOT NULL CHECK(style IN ('buttons','menu','reactions')),
                color INTEGER NOT NULL,
                channel_id INTEGER,
                message_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS choices (
                panel_id INTEGER NOT NULL REFERENCES panels(id) ON DELETE CASCADE,
                role_id INTEGER NOT NULL,
                emoji TEXT NOT NULL,
                label TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(panel_id, role_id)
            );
        """)

    def one(self, query, params=()):
        result = self.db.execute(query, params).fetchone()
        return dict(result) if result else None

    def all(self, query, params=()):
        return [dict(row) for row in self.db.execute(query, params)]

    def run(self, query, params=()):
        with self.db:
            return self.db.execute(query, params).lastrowid

    def panel(self, guild_id, panel_id):
        return self.one("SELECT * FROM panels WHERE id=? AND guild_id=?", (panel_id, guild_id))

    def choices(self, panel_id):
        return self.all("SELECT * FROM choices WHERE panel_id=? ORDER BY rowid", (panel_id,))


# -------------------------- Helper functions ---------------------------
def admin(interaction: discord.Interaction):
    return (interaction.guild is not None and isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.manage_guild)


async def require_admin(interaction: discord.Interaction):
    if admin(interaction):
        return True
    await interaction.response.send_message("Manage Server permission is required.", ephemeral=True)
    return False


def parse_color(value: str | None):
    if not value:
        return DEFAULT_COLOR
    value = value.strip().lstrip("#")
    if len(value) != 6 or any(c not in "0123456789abcdefABCDEF" for c in value):
        raise ValueError("Use a six-digit hex color, such as `#F5B42D`.")
    return int(value, 16)


def parse_emoji(value: str):
    value = value.strip()
    if not value or len(value) > 100:
        raise ValueError("Supply one emoji, such as `🌟` or `<:icon:123456789012345678>`.")
    return discord.PartialEmoji.from_str(value)


def safe(text: str):
    return discord.utils.escape_mentions(text)


def make_embed(panel: dict, entries: list[dict], *, logo: bool):
    embed = discord.Embed(title=safe(panel["title"]), description=safe(panel["description"]),
                          color=panel["color"])
    if logo:
        embed.set_thumbnail(url="attachment://glowberry-logo.png")
    if entries:
        lines = [f"{item['emoji']}  **{safe(item['label'])}**" for item in entries]
        embed.add_field(name="Available roles", value="\n".join(lines)[:1024], inline=False)
    prompt = "React below to add a role; remove your reaction to remove it." if panel["style"] == "reactions" else "Select a role below to add or remove it."
    embed.set_footer(text=f"GlowBerry SMP • {prompt}")
    return embed


def manageable(guild: discord.Guild, role: discord.Role):
    me = guild.me
    return (role.guild == guild and not role.is_default() and not role.managed
            and me is not None and me.guild_permissions.manage_roles and role < me.top_role)


async def toggle_role(interaction: discord.Interaction, role_id: int, panel_id: int):
    bot: RoleBot = interaction.client
    panel = bot.store.panel(interaction.guild_id, panel_id)
    entry = bot.store.one("SELECT role_id FROM choices WHERE panel_id=? AND role_id=?", (panel_id, role_id))
    if not panel or not entry or panel["message_id"] != interaction.message.id:
        await interaction.response.send_message("This role option is no longer available.", ephemeral=True)
        return
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Use this in the server.", ephemeral=True)
        return
    role = interaction.guild.get_role(role_id)
    if not role or not manageable(interaction.guild, role):
        await interaction.response.send_message("I cannot manage that role. Check my Manage Roles permission and role position.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        if role in interaction.user.roles:
            await interaction.user.remove_roles(role, reason=f"Self-serve role panel {panel_id}")
            result = "Removed"
        else:
            await interaction.user.add_roles(role, reason=f"Self-serve role panel {panel_id}")
            result = "Added"
        await interaction.followup.send(f"{result} **{role.name}**.", ephemeral=True)
    except discord.HTTPException:
        await interaction.followup.send("Discord could not update your role. Please try again.", ephemeral=True)


# ----------------------- Persistent role controls ----------------------
class RoleButton(discord.ui.Button):
    def __init__(self, panel_id: int, item: dict):
        self.panel_id = panel_id
        self.role_id = item["role_id"]
        super().__init__(label=item["label"][:80], emoji=parse_emoji(item["emoji"]),
                         style=discord.ButtonStyle.secondary,
                         custom_id=f"gb:role:{panel_id}:{self.role_id}")

    async def callback(self, interaction: discord.Interaction):
        await toggle_role(interaction, self.role_id, self.panel_id)


class RoleMenu(discord.ui.Select):
    def __init__(self, panel_id: int, entries: list[dict]):
        self.panel_id = panel_id
        options = [discord.SelectOption(label=item["label"][:100], value=str(item["role_id"]),
                                        emoji=parse_emoji(item["emoji"]),
                                        description=item["description"][:100] or None)
                   for item in entries]
        super().__init__(placeholder="Choose a role to toggle", min_values=1, max_values=1,
                         options=options, custom_id=f"gb:menu:{panel_id}")

    async def callback(self, interaction: discord.Interaction):
        await toggle_role(interaction, int(self.values[0]), self.panel_id)


class RoleView(discord.ui.View):
    def __init__(self, panel_id: int, style: str, entries: list[dict]):
        super().__init__(timeout=None)
        if style == "menu":
            self.add_item(RoleMenu(panel_id, entries))
        elif style == "buttons":
            for item in entries:
                self.add_item(RoleButton(panel_id, item))


# ---------------------------- Bot lifecycle ----------------------------
class RoleBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.reactions = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents,
                         allowed_mentions=NO_MENTIONS, help_command=None)
        self.store = Store()
        self.panel_lock = asyncio.Lock()

    async def setup_hook(self):
        for panel in self.store.all("SELECT * FROM panels WHERE message_id IS NOT NULL AND style != 'reactions'"):
            entries = self.store.choices(panel["id"])
            if entries:
                self.add_view(RoleView(panel["id"], panel["style"], entries), message_id=panel["message_id"])
        await self.add_cog(Panels(self))
        await self.add_cog(Messages(self))
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def refresh(self, panel: dict):
        """Update a published panel after editing its settings or choices."""
        if not panel["message_id"]:
            return
        entries = self.store.choices(panel["id"])
        channel = self.get_channel(panel["channel_id"]) or await self.fetch_channel(panel["channel_id"])
        message = await channel.fetch_message(panel["message_id"])
        view = RoleView(panel["id"], panel["style"], entries) if entries and panel["style"] != "reactions" else None
        await message.edit(embed=make_embed(panel, entries, logo=LOGO.exists()), view=view,
                           allowed_mentions=NO_MENTIONS)
        if view:
            self.add_view(view, message_id=message.id)
        if panel["style"] == "reactions":
            # Synchronize reactions only when staff change the panel, not on every click.
            await message.clear_reactions()
            for item in entries:
                await message.add_reaction(parse_emoji(item["emoji"]))

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self.reaction_role(payload, added=True)

    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self.reaction_role(payload, added=False)

    async def reaction_role(self, payload: discord.RawReactionActionEvent, *, added: bool):
        if not payload.guild_id or payload.user_id == (self.user.id if self.user else None):
            return
        panel = self.store.one("SELECT * FROM panels WHERE message_id=? AND guild_id=? AND style='reactions'",
                               (payload.message_id, payload.guild_id))
        if not panel:
            return
        option = self.store.one("SELECT * FROM choices WHERE panel_id=? AND emoji=?",
                                (panel["id"], str(payload.emoji)))
        if not option:
            return
        guild = self.get_guild(payload.guild_id)
        if not guild:
            return
        role = guild.get_role(option["role_id"])
        if not role or not manageable(guild, role):
            return
        try:
            member = await guild.fetch_member(payload.user_id)
            if added:
                await member.add_roles(role, reason=f"Reaction role panel {panel['id']}")
            else:
                await member.remove_roles(role, reason=f"Reaction role panel {panel['id']}")
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass


# ----------------------- Slash command: role panels ---------------------
class Panels(commands.Cog):
    roles = app_commands.Group(name="roles", description="Create and manage self-serve role panels")

    def __init__(self, bot: RoleBot):
        self.bot = bot

    @roles.command(name="create", description="Create a role panel draft")
    @app_commands.choices(style=[app_commands.Choice(name="Buttons", value="buttons"),
                                 app_commands.Choice(name="Dropdown menu", value="menu"),
                                 app_commands.Choice(name="Emoji reactions", value="reactions")])
    async def create(self, interaction: discord.Interaction, title: str, description: str,
                     style: app_commands.Choice[str], color: str = "#F5B42D"):
        if not await require_admin(interaction):
            return
        try:
            hex_color = parse_color(color)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        if len(title) > 256 or len(description) > 1500:
            await interaction.response.send_message("Keep the title under 256 and the description under 1,500 characters.", ephemeral=True)
            return
        panel_id = self.bot.store.run(
            "INSERT INTO panels(guild_id,title,description,style,color) VALUES(?,?,?,?,?)",
            (interaction.guild_id, title, description, style.value, hex_color))
        await interaction.response.send_message(
            f"Created panel **#{panel_id}**. Add choices with `/roles add`, then post it with `/roles publish`.",
            ephemeral=True)

    @roles.command(name="add", description="Add a role option to a panel")
    async def add(self, interaction: discord.Interaction, panel_id: int, role: discord.Role,
                  emoji: str, label: str = "", description: str = ""):
        if not await require_admin(interaction):
            return
        panel = self.bot.store.panel(interaction.guild_id, panel_id)
        if not panel:
            await interaction.response.send_message("Panel ID not found. Use `/roles list`.", ephemeral=True)
            return
        if not manageable(interaction.guild, role):
            await interaction.response.send_message("I need Manage Roles and a position above that role; managed roles cannot be assigned.", ephemeral=True)
            return
        try:
            parsed = parse_emoji(emoji)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        if parsed.id and interaction.guild.get_emoji(parsed.id) is None:
            await interaction.response.send_message("Use a Unicode emoji or a custom emoji from this server.", ephemeral=True)
            return
        entries = self.bot.store.choices(panel_id)
        if len(entries) >= 20 and role.id not in {item["role_id"] for item in entries}:
            await interaction.response.send_message("A panel can contain up to 20 roles.", ephemeral=True)
            return
        if panel["style"] == "reactions" and any(item["emoji"] == str(parsed) and item["role_id"] != role.id for item in entries):
            await interaction.response.send_message("Each reaction emoji must be unique on this panel.", ephemeral=True)
            return
        if len(label or role.name) > 80 or len(description) > 100:
            await interaction.response.send_message("Labels must be under 80 characters and descriptions under 100.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.bot.panel_lock:
            self.bot.store.run("INSERT INTO choices(panel_id,role_id,emoji,label,description) VALUES(?,?,?,?,?) "
                               "ON CONFLICT(panel_id,role_id) DO UPDATE SET emoji=excluded.emoji,label=excluded.label,description=excluded.description",
                               (panel_id, role.id, str(parsed), label or role.name, description))
            try:
                await self.bot.refresh(panel)
            except discord.HTTPException as error:
                await interaction.followup.send(f"Saved the role, but could not update the posted panel: {error}", ephemeral=True)
                return
        await interaction.followup.send(f"Saved **{role.name}** in panel #{panel_id}.", ephemeral=True)

    @roles.command(name="remove", description="Remove a role option from a panel")
    async def remove(self, interaction: discord.Interaction, panel_id: int, role: discord.Role):
        if not await require_admin(interaction):
            return
        panel = self.bot.store.panel(interaction.guild_id, panel_id)
        if not panel:
            await interaction.response.send_message("Panel ID not found.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.bot.panel_lock:
            self.bot.store.run("DELETE FROM choices WHERE panel_id=? AND role_id=?", (panel_id, role.id))
            try:
                await self.bot.refresh(panel)
            except discord.HTTPException as error:
                await interaction.followup.send(f"Removed the option, but could not update the message: {error}", ephemeral=True)
                return
        await interaction.followup.send("Role option removed.", ephemeral=True)

    @roles.command(name="publish", description="Post a configured role panel in a channel")
    async def publish(self, interaction: discord.Interaction, panel_id: int, channel: discord.TextChannel):
        if not await require_admin(interaction):
            return
        panel = self.bot.store.panel(interaction.guild_id, panel_id)
        if not panel:
            await interaction.response.send_message("Panel ID not found.", ephemeral=True)
            return
        if panel["message_id"]:
            await interaction.response.send_message("Already posted. Edit its choices or create another panel.", ephemeral=True)
            return
        entries = self.bot.store.choices(panel_id)
        if not entries:
            await interaction.response.send_message("Add at least one role with `/roles add` first.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.bot.panel_lock:
            view = RoleView(panel_id, panel["style"], entries) if panel["style"] != "reactions" else None
            try:
                file = discord.File(LOGO, filename="glowberry-logo.png") if LOGO.exists() else None
                message = await channel.send(embed=make_embed(panel, entries, logo=file is not None),
                                             view=view, file=file, allowed_mentions=NO_MENTIONS)
                self.bot.store.run("UPDATE panels SET channel_id=?,message_id=? WHERE id=?",
                                   (channel.id, message.id, panel_id))
                if view:
                    self.bot.add_view(view, message_id=message.id)
                else:
                    for item in entries:
                        await message.add_reaction(parse_emoji(item["emoji"]))
            except discord.HTTPException as error:
                await interaction.followup.send(f"Could not finish posting the panel: {error}. Check permissions in that channel.", ephemeral=True)
                return
        await interaction.followup.send(f"Panel #{panel_id} is live: {message.jump_url}", ephemeral=True)

    @roles.command(name="edit", description="Change a panel's title, description or color")
    async def edit(self, interaction: discord.Interaction, panel_id: int,
                   title: str | None = None, description: str | None = None, color: str | None = None):
        if not await require_admin(interaction):
            return
        panel = self.bot.store.panel(interaction.guild_id, panel_id)
        if not panel:
            await interaction.response.send_message("Panel ID not found.", ephemeral=True)
            return
        try:
            hex_color = parse_color(color) if color else panel["color"]
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        if len(title or panel["title"]) > 256 or len(description or panel["description"]) > 1500:
            await interaction.response.send_message("Title or description is too long.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.bot.panel_lock:
            self.bot.store.run("UPDATE panels SET title=?,description=?,color=? WHERE id=?",
                               (title or panel["title"], description or panel["description"], hex_color, panel_id))
            try:
                await self.bot.refresh(self.bot.store.panel(interaction.guild_id, panel_id))
            except discord.HTTPException as error:
                await interaction.followup.send(f"Settings saved, but the posted message could not update: {error}", ephemeral=True)
                return
        await interaction.followup.send("Panel updated.", ephemeral=True)

    @roles.command(name="list", description="View configured role panels and their IDs")
    async def list_panels(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        panels = self.bot.store.all("SELECT * FROM panels WHERE guild_id=? ORDER BY id DESC LIMIT 30", (interaction.guild_id,))
        lines = [f"**#{p['id']}** {safe(p['title'])} · {p['style']} · "
                 + (f"[posted](https://discord.com/channels/{p['guild_id']}/{p['channel_id']}/{p['message_id']})" if p["message_id"] else "draft")
                 for p in panels]
        await interaction.response.send_message("\n".join(lines)[:1900] or "No panels yet. Use `/roles create`.", ephemeral=True,
                                                allowed_mentions=NO_MENTIONS)

    @roles.command(name="delete", description="Remove a panel and its posted message")
    async def delete(self, interaction: discord.Interaction, panel_id: int):
        if not await require_admin(interaction):
            return
        panel = self.bot.store.panel(interaction.guild_id, panel_id)
        if not panel:
            await interaction.response.send_message("Panel ID not found.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.bot.panel_lock:
            if panel["message_id"]:
                try:
                    channel = self.bot.get_channel(panel["channel_id"]) or await self.bot.fetch_channel(panel["channel_id"])
                    message = await channel.fetch_message(panel["message_id"])
                    await message.delete()
                except discord.NotFound:
                    pass
                except discord.HTTPException as error:
                    await interaction.followup.send(f"Could not delete the posted message: {error}", ephemeral=True)
                    return
            self.bot.store.run("DELETE FROM panels WHERE id=? AND guild_id=?", (panel_id, interaction.guild_id))
        await interaction.followup.send(f"Deleted panel #{panel_id}.", ephemeral=True)


# ----------------------- Slash command: bot messages --------------------
class Messages(commands.Cog):
    message = app_commands.Group(name="message", description="Send messages as the bot")

    def __init__(self, bot: RoleBot):
        self.bot = bot

    @message.command(name="send", description="Send plain text or a styled announcement as the bot")
    async def send(self, interaction: discord.Interaction, channel: discord.TextChannel,
                   text: str, title: str | None = None, color: str = "#F5B42D",
                   image: discord.Attachment | None = None):
        if not await require_admin(interaction):
            return
        if len(text) > (4000 if title else 2000) or (title and len(title) > 256):
            await interaction.response.send_message("Text or title is too long.", ephemeral=True)
            return
        try:
            hex_color = parse_color(color)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        if image and (not image.content_type or not image.content_type.startswith("image/")):
            await interaction.response.send_message("Upload an image file for the image option.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            file = await image.to_file() if image else None
            embed = None
            if title:
                embed = discord.Embed(title=safe(title), description=safe(text), color=hex_color)
                if file:
                    embed.set_image(url=f"attachment://{file.filename}")
            result = await channel.send(content=safe(text) if not title else None, embed=embed,
                                        file=file, allowed_mentions=NO_MENTIONS)
        except discord.HTTPException as error:
            await interaction.followup.send(f"Could not send: {error}", ephemeral=True)
            return
        await interaction.followup.send(f"Sent: {result.jump_url}", ephemeral=True)

    @message.command(name="edit", description="Edit a text or embed message previously sent by this bot")
    async def edit(self, interaction: discord.Interaction, channel: discord.TextChannel,
                   message_id: str, text: str, title: str | None = None, color: str = "#F5B42D"):
        if not await require_admin(interaction):
            return
        if not message_id.isdigit() or len(text) > (4000 if title else 2000):
            await interaction.response.send_message("Enter a numeric message ID and valid text.", ephemeral=True)
            return
        try:
            hex_color = parse_color(color)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            message = await channel.fetch_message(int(message_id))
            if message.author.id != self.bot.user.id or self.bot.store.one("SELECT 1 FROM panels WHERE message_id=?", (message.id,)):
                await interaction.followup.send("I can only edit my own regular messages. Use `/roles edit` for panels.", ephemeral=True)
                return
            embed = discord.Embed(title=safe(title), description=safe(text), color=hex_color) if title else None
            await message.edit(content=safe(text) if not title else None, embed=embed,
                               allowed_mentions=NO_MENTIONS)
        except discord.HTTPException as error:
            await interaction.followup.send(f"Could not edit that message: {error}", ephemeral=True)
            return
        await interaction.followup.send("Message edited.", ephemeral=True)


# ------------------------------- Start --------------------------------
if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("Set DISCORD_TOKEN in a private .env file.")
    RoleBot().run(TOKEN)
