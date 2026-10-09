# Changelog

## Unreleased

- Fix: rewriting `~/.claude.json` or `~/.claude/settings.json` no longer loosens their permissions (a `0600` file stayed `0600`, new files are created `0600`).
- Fix: the quarantine folder is now owner-only (`0700`), since it holds transcripts and config backups.
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
