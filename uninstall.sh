#!/usr/bin/env bash
# Remove claude-revoke. Your quarantine folder (~/.claude-revoke-quarantine) is kept.
set -euo pipefail
rm -f "$HOME/.local/bin/claude-revoke" \
      "$HOME/.local/share/applications/claude-revoke.desktop" \
      "$HOME/.local/share/icons/hicolor/scalable/apps/claude-revoke.svg"
echo "claude-revoke removed."
if [ -d "$HOME/.claude-revoke-quarantine" ]; then
  echo "Quarantine kept at ~/.claude-revoke-quarantine - delete it yourself once you're sure."
fi
