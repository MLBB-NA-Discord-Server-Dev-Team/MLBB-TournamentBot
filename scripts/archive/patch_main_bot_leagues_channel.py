"""Insert #bot-leagues channel bootstrap into bot/main.py"""
path = "/root/MLBB-TournamentBot/bot/main.py"
with open(path, "r") as f:
    content = f.read()

# 1. Add bootstrap call in on_ready
old_calls = """        await self._bootstrap_notifications_channel()
        await self._bootstrap_admin_log_channel()
        await self._bootstrap_bot_commands_channel()"""
new_calls = """        await self._bootstrap_notifications_channel()
        await self._bootstrap_admin_log_channel()
        await self._bootstrap_bot_commands_channel()
        await self._bootstrap_bot_leagues_channel()"""
content = content.replace(old_calls, new_calls)

# 2. Add the bootstrap method right before `async def close`
old_close = """    async def close(self):
        await db.close()
        await super().close()"""
new_method = '''    async def _bootstrap_bot_leagues_channel(self):
        """
        Ensure #bot-leagues exists in MATCH_VOICE_CATEGORY_ID.
        Public read, staff write. Used for persistent bot-league and autonomous sim notifications.
        """
        if not config.MATCH_VOICE_CATEGORY_ID:
            return

        for guild in self.guilds:
            category = guild.get_channel(config.MATCH_VOICE_CATEGORY_ID)
            if not isinstance(category, discord.CategoryChannel):
                continue

            existing = discord.utils.get(category.text_channels, name="bot-leagues")
            if existing:
                config.BOT_LEAGUE_CHANNEL_ID = existing.id
                logger.info("Bot leagues channel: #%s (%s)", existing.name, existing.id)
                return

            # Public read, only bot + staff can send
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(
                    view_channel=True, send_messages=False, add_reactions=True
                ),
                guild.me: discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, embed_links=True
                ),
            }
            for role in guild.roles:
                if role.name in config.STAFF_ROLES:
                    overwrites[role] = discord.PermissionOverwrite(
                        view_channel=True, send_messages=True
                    )

            channel = await guild.create_text_channel(
                "bot-leagues",
                category=category,
                overwrites=overwrites,
                topic="Autonomous bot-league activity: persistent weekly leagues + daily simulation health checks.",
                reason="TournamentBot bootstrap",
            )
            config.BOT_LEAGUE_CHANNEL_ID = channel.id
            logger.info("Created bot leagues channel: #%s (%s)", channel.name, channel.id)
            _write_env_key("BOT_LEAGUE_CHANNEL_ID", str(channel.id))
            return

    async def close(self):
        await db.close()
        await super().close()'''

content = content.replace(old_close, new_method)

with open(path, "w") as f:
    f.write(content)
print("OK")
