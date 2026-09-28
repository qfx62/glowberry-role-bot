# GlowBerry SMP Role Bot

A separate Python Discord bot for customizable role panels and staff announcements. Its attached logo is used in the role panel embed. All commands require **Manage Server** permission, and the bot never parses chat messages or needs Message Content Intent.

## Start

1. Install Python 3.11 or newer, then run `python -m pip install -r requirements.txt`.
2. Copy `.env.example` to `.env` and replace the token. Set `GUILD_ID` to your server ID for instant slash command updates. **Do not upload `.env` or your bot token to GitHub.**
3. In Discord Developer Portal, invite the bot with the `bot` and `applications.commands` scopes. Give it **Manage Roles, View Channels, Send Messages, Embed Links, Attach Files, Add Reactions, Read Message History, and Manage Messages**. `Manage Messages` allows reaction panels to resync after edits. Move its highest role above every role it should assign. No privileged intents are needed.
4. Run `python bot.py` from this directory on a Python host or VPS. GitHub stores code but does not keep the bot online. The SQLite database `role_panels.sqlite3` holds all settings and must be backed up on the bot host.

## Build your first panel

1. `/roles create` → choose **Buttons**, **Dropdown menu**, or **Emoji reactions**; enter a title, description, and optional six-digit color.
2. Take the panel ID from the response. `/roles add` → ID, Discord role, emoji, optional button label and menu description. Repeat for each role.
3. `/roles publish` → ID and channel. The panel gets the GlowBerry SMP logo. Buttons and menus work across bot restarts. Emoji reactions add the role and removing a reaction removes it.
4. `/roles edit` changes the title, description or color. `/roles add` updates an existing role option. `/roles remove` removes a choice. `/roles list` shows IDs and links. `/roles delete` deletes a panel and its posted message.

Maximum: 20 roles per panel. Make more panels if you need more. Emoji reactions require each emoji to be unique on a panel. Members cannot self-assign Discord managed roles or roles above the bot's highest role.

## Post as the bot

- `/message send` takes a channel and text. Add a title for a colored embed, and optionally attach an image. Mentions do not ping users or `@everyone`.
- `/message edit` changes a regular message sent by this bot using the channel and numeric message ID. Use `/roles edit` for role panels.

The bot runs independently of the BlitzFFA website, the QCS ticket bot, and any other project.
