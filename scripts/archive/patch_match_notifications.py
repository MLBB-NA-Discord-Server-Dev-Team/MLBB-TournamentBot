"""
Convert match.py's single-channel notification helper to a multi-guild broadcast.

Replaces `_notifications_channel(bot)` with `_send_notification(bot, **kwargs)`
which loops over all channels in data/guilds.json. Updates all call sites.
"""
import re

path = "/root/MLBB-TournamentBot/bot/cogs/match.py"
with open(path, "r") as f:
    content = f.read()

# 1. Replace the helper function
old_helper = '''def _notifications_channel(bot: commands.Bot) -> discord.TextChannel | None:
    if not config.MATCH_NOTIFICATIONS_CHANNEL_ID:
        return None
    return bot.get_channel(config.MATCH_NOTIFICATIONS_CHANNEL_ID)'''

new_helper = '''# Multi-guild broadcast: read data/guilds.json and post to every guild's
# #match-notifications channel. Falls back to config.MATCH_NOTIFICATIONS_CHANNEL_ID
# if guilds.json is missing.
import json
from pathlib import Path

_GUILDS_FILE = Path(__file__).parent.parent.parent / "data" / "guilds.json"


def _get_notification_channel_ids() -> list[int]:
    if _GUILDS_FILE.exists():
        try:
            with open(_GUILDS_FILE) as f:
                data = json.load(f)
            ids = [int(g["match_notifications"]) for g in data.get("guilds", []) if g.get("match_notifications")]
            if ids:
                return ids
        except Exception:
            pass
    if config.MATCH_NOTIFICATIONS_CHANNEL_ID:
        return [config.MATCH_NOTIFICATIONS_CHANNEL_ID]
    return []


async def _send_notification(bot: commands.Bot, **send_kwargs) -> bool:
    """Broadcast a message to all configured match-notifications channels."""
    sent = False
    for ch_id in _get_notification_channel_ids():
        channel = bot.get_channel(ch_id)
        if not channel:
            continue
        try:
            await channel.send(**send_kwargs)
            sent = True
        except Exception as e:
            logger.warning("Failed to post match notification to %s: %s", ch_id, e)
    return sent'''

if old_helper not in content:
    raise SystemExit("old helper not found in match.py")
content = content.replace(old_helper, new_helper)

# 2. Replace simple `notif_channel.send(embed=embed)` pattern
# Pattern:
#     notif_channel = _notifications_channel(interaction.client)
#     if notif_channel:
#         await notif_channel.send(embed=embed)
simple_pat = re.compile(
    r"^(\s+)notif_channel = _notifications_channel\((.*?)\)\n"
    r"\1if notif_channel:\n"
    r"\1    await notif_channel\.send\(embed=embed\)",
    re.MULTILINE,
)
def simple_sub(m):
    indent = m.group(1)
    bot_expr = m.group(2)
    return f"{indent}await _send_notification({bot_expr}, embed=embed)"
count_simple = 0
def _count(m):
    global count_simple
    count_simple += 1
    return simple_sub(m)
content = simple_pat.sub(_count, content)
print(f"Replaced {count_simple} simple notification calls")

# 3. Handle the more complex ones: `if needs_admin and notif_channel:` etc.
# These need to keep notif_channel as a variable. Easiest: replace the
# `notif_channel = _notifications_channel(...)` line with a list-based variant
# and use `_send_notification` broadcast.

# Pattern with needs_admin:
#     notif_channel = _notifications_channel(interaction.client)
#     if notif_channel:
#         await notif_channel.send(embed=embed)
#
#     if needs_admin and notif_channel:
#         ...
#         await notif_channel.send(...)
#
# The first pair was already replaced, leaving `if needs_admin and notif_channel:`
# dangling. Let me check what's left.

# Check for any remaining `notif_channel` references
remaining = re.findall(r"notif_channel", content)
if remaining:
    print(f"WARNING: {len(remaining)} remaining notif_channel references — manual review needed")
    # Print the lines for inspection
    for i, line in enumerate(content.split("\n"), 1):
        if "notif_channel" in line:
            print(f"  line {i}: {line.strip()}")

# 4. The dispute case at line 360:
#     notif_channel = _notifications_channel(interaction.client)
#     if notif_channel:
#         ...
#         await notif_channel.send(
#             content=mention, embed=embed,
#         )
# This has different kwargs (content + embed). Handle with a specific pattern.

dispute_pat = re.compile(
    r"^(\s+)notif_channel = _notifications_channel\((.*?)\)\n"
    r"\1if notif_channel:\n"
    r"\1    ([^\n]*?)\n"
    r"\1    await notif_channel\.send\(\n"
    r"(.*?)\1    \)",
    re.MULTILINE | re.DOTALL,
)

def dispute_sub(m):
    indent = m.group(1)
    bot_expr = m.group(2)
    middle = m.group(3)
    kwargs = m.group(4)
    return (
        f"{indent}{middle}\n"
        f"{indent}await _send_notification({bot_expr},\n"
        f"{kwargs}{indent})"
    )

count_dispute = 0
def _count2(m):
    global count_dispute
    count_dispute += 1
    return dispute_sub(m)
content = dispute_pat.sub(_count2, content)
print(f"Replaced {count_dispute} multi-arg notification calls")

# Final check
if "notif_channel" in content:
    print("\nRemaining notif_channel references (manual review needed):")
    for i, line in enumerate(content.split("\n"), 1):
        if "notif_channel" in line:
            print(f"  line {i}: {line.strip()}")
else:
    print("\nAll notif_channel references converted")

with open(path, "w") as f:
    f.write(content)
