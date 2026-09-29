"""Add 'Adding a new Discord server' subsection to the Deployment section."""
path = "/root/MLBB-TournamentBot/README.md"
with open(path, "r") as f:
    content = f.read()

# Insert after "### Upgrading an existing deployment" subsection,
# before "### Troubleshooting"
old = """### Upgrading an existing deployment

```bash
cd /root/MLBB-TournamentBot
git pull
sudo bash scripts/deploy.sh --force    # re-runs all phases; idempotent
```

### Troubleshooting"""

new = """### Upgrading an existing deployment

```bash
cd /root/MLBB-TournamentBot
git pull
sudo bash scripts/deploy.sh --force    # re-runs all phases; idempotent
```

### Adding the bot to a new Discord server

The bot supports running in multiple Discord guilds simultaneously (e.g., DEV + PROD) while sharing a single WordPress/SportsPress backend. Each guild gets its own category and 4 channels; notifications are broadcast to **all** configured guilds.

To add the bot to a new server:

1. **Invite the bot** to the new guild using the standard invite URL (needs `bot` + `applications.commands` scopes and the **Manage Channels** permission).
2. **Add the new guild ID** to `GUILD_IDS` in `.env` (comma-separated). This enables slash command sync to the new guild.
3. **Run the provisioning script**:
   ```bash
   cd /root/MLBB-TournamentBot
   venv/bin/python scripts/provision_guild.py <new_guild_id> --name PROD
   ```
   The script:
   - Finds or creates a category named **"MLBB Tournaments"** (or uses `MATCH_VOICE_CATEGORY_ID` from `.env` if that category exists in this guild)
   - Finds or creates the 4 text channels (`#match-notifications`, `#tournament-admin`, `#bot-commands`, `#bot-leagues`) with correct role permissions
   - Appends the guild + channel IDs to `data/guilds.json`
4. **Restart the bot**:
   ```bash
   systemctl restart mlbb-tournament-bot
   ```

From then on, all admin-log events, match notifications, persistent-league state transitions, and autonomous-sim health reports will broadcast to **every** guild in `data/guilds.json`.

**Other `provision_guild.py` commands:**
```bash
python scripts/provision_guild.py --list                    # show configured guilds
python scripts/provision_guild.py <guild_id> --remove       # drop from guilds.json (channels kept)
python scripts/provision_guild.py <guild_id> --name NAME    # re-provision / update existing
```

**How broadcast routing works:**
- `services/admin_log.py` reads `data/guilds.json` on each call and posts to every `admin_log` channel
- `bot/cogs/match.py` has a `_send_notification()` helper that broadcasts match notifications
- `scripts/persistent_league.py` and `scripts/autonomous_sim.py` load `data/guilds.json` at startup and post to every `bot_leagues` channel
- If `data/guilds.json` is missing (fresh install), all code falls back to the legacy single-channel env vars (`ADMIN_LOG_CHANNEL_ID`, `MATCH_NOTIFICATIONS_CHANNEL_ID`, `BOT_LEAGUE_CHANNEL_ID`)

### Troubleshooting"""

if old not in content:
    raise SystemExit("Marker section not found in README")

content = content.replace(old, new)

with open(path, "w") as f:
    f.write(content)
print("Multi-guild section added")
