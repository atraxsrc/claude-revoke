# Changelog

## Unreleased

- New: scheduled audits. `claude-revoke --schedule weekly` (or `daily`, `off`) enables a systemd user timer that runs `claude-revoke --notify`: the usual scan, a one-line summary in the journal, and a desktop notification when anything is stale or risky. Off by default, never changes anything.
- New: `--notify` flag (what the timer runs). Needs `notify-send` for the notification; the packages recommend `libnotify-bin`/`libnotify`.
- Packages and `install.sh` ship the timer and service units; `uninstall.sh` stops and removes them.

## 0.3.0 - 2026-10-10

- Packages: `.deb`, `.rpm` and Arch packages are built from one nfpm config, installed and smoke-tested on Debian, Fedora and Arch by CI, and attached to every GitHub release together with `SHA256SUMS`. The launcher entry from a package opens your desktop's default terminal.
- CI runs the test suite on every push and pull request.

## 0.2.0 - 2026-10-10

- Fix: rewriting `~/.claude.json` or `~/.claude/settings.json` no longer loosens their permissions (a `0600` file stayed `0600`, new files are created `0600`).
- Fix: the quarantine folder and the folders inside it are now owner-only (`0700`), since they hold transcripts and config backups.
- Fix: projects inside folders named `dev`, `run`, `sys` or `proc` are found again. Those names are now only skipped directly under `/`.
- Fix: the scan no longer walks `~/.cache`, `~/.local`, `~/snap` and other skipped folders at the top of your home, so it is faster.
- Fix: `--restore` now puts back only the entries that run removed, into the file as it is now, so anything Claude Code wrote after the run is kept. The full backup is still used when the file is missing or unreadable, and for 0.1.0 quarantine folders.
- Fix: the secret scan no longer reports identifiers and placeholders (`generateTokenForUser`, `your_password_here`, `AKIA...EXAMPLE`). Each distinct secret is counted once and shown with its file, line and a redacted preview.
- Fix: unexpected shapes in `~/.claude.json` or settings files (a list where an object is expected, non-string rules, `"hooks": "x"`) no longer crash the scan or the apply step.
- New: `--pause` waits for Enter before exiting, so a terminal opened from the launcher stays open long enough to read the undo command. The launcher entry now uses it: re-run `./install.sh` to update it.
- Tests: `python3 -m unittest discover -s tests` (standard library only).

## 0.1.0 - 2026-10-09

First release.

- Installer-style TUI with four categories: trusted projects, session transcripts, project settings files, global settings.
- Grouped checkboxes (STALE / RISKY / OK) with group-level ticking, search and view filters.
- Secret scan of transcripts.
- One-tick hardening with deny rules for keys and `.env` files.
- Quarantine instead of delete, JSON backups, `--restore`.
- `--report`, `--only`, `--dry-run`, `--roots`, `--accent`, `NO_COLOR`.
- Colors come only from the terminal theme palette.
- `install.sh` with a COSMIC launcher entry.
