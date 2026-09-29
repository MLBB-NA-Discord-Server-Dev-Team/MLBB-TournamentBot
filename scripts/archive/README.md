# Archived one-off scripts

Historical scripts that were run once on the production server (`/root/MLBB-TournamentBot`)
and are kept for reference only. **Do not re-run them** — their changes are already in the codebase.

| File | Purpose |
|---|---|
| `patch_main_bot_leagues_channel.py` | Added `#bot-leagues` channel bootstrap to `bot/main.py` |
| `patch_match_notifications.py` | Converted match notifications to multi-guild broadcast (`data/guilds.json`) |
| `patch_match_remaining.py` | Fixed the remaining notification call sites in `bot/cogs/match.py` |
| `patch_readme*.py` | README restructuring (deployment section, multi-guild docs, link to DEPLOYMENT.md) |
| `verify_stagger.py` | Smoke test for the per-league month-slot stagger design (superseded by the format-group stagger in `season_init.py`; will not run against current code) |
| `mlbb-signup-commands-append.php` | Original `[mlbb_signup_commands]` shortcode snippet; now lives in `wordpress/mu-plugins/mlbb-league-list.php` |
