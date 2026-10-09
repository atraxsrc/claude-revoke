#!/usr/bin/env bash
# Remove claude-revoke. Your quarantine folder (~/.claude-revoke-quarantine) is kept.
set -euo pipefail
# scheduled audit: stop the timer and remove its units and schedule override
units="$HOME/.config/systemd/user"
command -v systemctl >/dev/null && systemctl --user disable --now claude-revoke-audit.timer 2>/dev/null || true
rm -rf "$units/claude-revoke-audit.timer.d"
rm -f "$units/claude-revoke-audit.service" "$units/claude-revoke-audit.timer"
command -v systemctl >/dev/null && systemctl --user daemon-reload 2>/dev/null || true
rm -f "$HOME/.local/bin/claude-revoke" \
      "$HOME/.local/share/applications/claude-revoke.desktop" \
      "$HOME/.local/share/icons/hicolor/scalable/apps/claude-revoke.svg"
echo "claude-revoke removed."
if [ -d "$HOME/.claude-revoke-quarantine" ]; then
  echo "Quarantine kept at ~/.claude-revoke-quarantine - delete it yourself once you're sure."
fi
