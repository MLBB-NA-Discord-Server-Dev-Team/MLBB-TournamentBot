"""Shrink README.md's Deployment section to a summary + link to DEPLOYMENT.md."""
path = "/root/MLBB-TournamentBot/README.md"
with open(path, "r") as f:
    content = f.read()

# Find the Deployment section (from "## Deployment" through the next "##")
deploy_start = content.find("## Deployment\n")
player_start = content.find("## Player Journey")
if deploy_start == -1 or player_start == -1:
    raise SystemExit("Could not find section markers")

old_section = content[deploy_start:player_start]

new_section = """## Deployment

See [DEPLOYMENT.md](./DEPLOYMENT.md) for the full deployment guide.

### Quick start

```bash
# 1. Clone the repo
git clone <repo-url> /root/MLBB-TournamentBot
cd /root/MLBB-TournamentBot

# 2. Copy the env template and fill in your values
cp .env.sample .env
nano .env    # required: DISCORD_TOKEN, WP_PLAY_MLBB, DB_PASSWORD, MATCH_VOICE_CATEGORY_ID

# 3. Run the bootstrap script
sudo bash scripts/deploy.sh
```

The `scripts/deploy.py` script runs 7 idempotent phases: pre-flight checks, database migration, WordPress infrastructure, systemd service install, cron jobs, log rotation, and post-install verification. Safe to re-run for upgrades:

```bash
cd /root/MLBB-TournamentBot && git pull && sudo bash scripts/deploy.sh --force
```

### Adding the bot to a new Discord server

The bot supports multi-guild operation with shared backend (same WordPress/SportsPress) and broadcast notifications (all guilds see all events).

```bash
# 1. Invite bot to the new server
# 2. Add guild ID to GUILD_IDS in .env
# 3. Provision category + 4 channels
venv/bin/python scripts/provision_guild.py <guild_id> --name PROD

# 4. Restart for slash command sync
systemctl restart mlbb-tournament-bot
```

See [DEPLOYMENT.md § Multi-Guild Support](./DEPLOYMENT.md#multi-guild-support) for details.

### Invite URL

```
https://discord.com/api/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=369468123907121&scope=bot+applications.commands
```

"""

content = content.replace(old_section, new_section)

with open(path, "w") as f:
    f.write(content)
print("README Deployment section shrunk")
print(f"New length: {len(content)} chars")
