"""Fix the two remaining notif_channel call sites in match.py."""
path = "/root/MLBB-TournamentBot/bot/cogs/match.py"
with open(path, "r") as f:
    content = f.read()

# Site 1: admin ping after low-confidence submit
old1 = """        # Ping admins if low confidence
        if needs_admin and notif_channel:
            staff_ping = " ".join(
                r.mention for r in interaction.guild.roles if r.name in config.ADMIN_ROLES
            )
            if staff_ping:
                await notif_channel.send(
                    f"⚠️ Admin review needed for submission `#{submission_id}` — low confidence parse. {staff_ping}"
                )"""
new1 = """        # Ping admins if low confidence
        if needs_admin:
            staff_ping = " ".join(
                r.mention for r in interaction.guild.roles if r.name in config.ADMIN_ROLES
            )
            if staff_ping:
                await _send_notification(
                    interaction.client,
                    content=f"⚠️ Admin review needed for submission `#{submission_id}` — low confidence parse. {staff_ping}",
                )"""
assert old1 in content, "Site 1 pattern not found"
content = content.replace(old1, new1)

# Site 2: dispute embed with admin ping
old2 = """        notif_channel = _notifications_channel(interaction.client)
        if notif_channel:
            # Ping admins
            staff_ping = " ".join(
                r.mention for r in interaction.guild.roles if r.name in config.ADMIN_ROLES
            )
            await notif_channel.send(
                f"{staff_ping}\\n" if staff_ping else "", embed=embed
            )"""
new2 = """        # Ping admins in #match-notifications (broadcast)
        staff_ping = " ".join(
            r.mention for r in interaction.guild.roles if r.name in config.ADMIN_ROLES
        )
        await _send_notification(
            interaction.client,
            content=f"{staff_ping}\\n" if staff_ping else "",
            embed=embed,
        )"""
assert old2 in content, "Site 2 pattern not found"
content = content.replace(old2, new2)

# Final sanity check
if "notif_channel" in content:
    print("ERROR: notif_channel still present")
    for i, line in enumerate(content.split("\n"), 1):
        if "notif_channel" in line:
            print(f"  line {i}: {line.strip()}")
    raise SystemExit(1)

with open(path, "w") as f:
    f.write(content)
print("OK: all notif_channel references removed")
