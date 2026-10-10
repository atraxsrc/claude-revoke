<p align="center">
  <img src="assets/banner.svg" alt="claude-revoke banner" width="100%">
</p>

<p align="center">
  <img alt="Python 3.8+" src="https://img.shields.io/badge/python-3.8%2B-61afef?style=flat-square">
  <img alt="No dependencies" src="https://img.shields.io/badge/dependencies-none-98c379?style=flat-square">
  <img alt="Linux" src="https://img.shields.io/badge/platform-linux-e5c07b?style=flat-square">
  <img alt="Pop!_OS COSMIC" src="https://img.shields.io/badge/tested%20on-Pop!__OS%20COSMIC-48b9c7?style=flat-square">
  <img alt="MIT license" src="https://img.shields.io/badge/license-MIT-c678dd?style=flat-square">
</p>

---

Every time you run Claude Code in a folder and click **"Always allow"**, it remembers. After a year you can have hundreds of trusted folders, auto-approve rules, extra directories and saved transcripts spread across your disk, and no easy way to see them all.

**claude-revoke** is an installer-style terminal UI in reverse. Instead of choosing what to install, you see everything Claude Code has been given, grouped into **STALE**, **RISKY** and **OK**, tick what you want gone, and apply. Nothing is deleted: removed items go to a quarantine folder, and one command undoes a run.

<p align="center">
  <img src="assets/screenshot-checklist.svg" alt="Checklist screen" width="92%">
</p>

## Features

- **Full inventory**: trusted folders, "always allow" rules, extra directories, session transcripts, per-project settings files, hooks and MCP servers.
- **Grouped checkboxes**: STALE (folder deleted), RISKY (broad rules such as `Bash(*)`, bypass mode, hooks, leaked secrets) and OK. Tick a whole group or one folder at a time.
- **Secret scan**: finds AWS keys, private keys, GitHub/Slack/API tokens and `KEY=value` lines inside saved transcripts, and shows the file, line and a redacted preview of each, so you know what to rotate. Identifiers and placeholders like `your_password_here` are filtered out.
- **Hardening**: one tick adds deny rules for `~/.ssh`, `~/.aws`, `.env`, `*.pem` and similar.
- **Safe by design**: dry-run mode, a review screen, typed `YES` confirmation, JSON backups, quarantine instead of delete, and `--restore`.
- **Theme-aware**: draws only with your terminal's own palette, so it matches your rice.
- **Zero dependencies**: a single Python file using the standard library.

<p align="center">
  <img src="assets/screenshot-transcripts.svg" alt="Session transcripts with secret scan results" width="92%">
</p>

## Install

### Packages (Debian, Ubuntu, Pop!_OS, Fedora, Arch)

Download the file for your distro from the [latest release](https://github.com/atraxsrc/claude-revoke/releases/latest), then:

```bash
sudo apt install ./claude-revoke_*.deb          # Debian, Ubuntu, Pop!_OS
sudo dnf install ./claude-revoke-*.rpm          # Fedora
sudo pacman -U ./claude-revoke-*.pkg.tar.zst    # Arch
```

This installs the `claude-revoke` command and a **Claude Revoke** launcher entry that opens in your desktop's default terminal. `SHA256SUMS` on the release page lets you check the download. Remove with `sudo apt remove claude-revoke`, `sudo dnf remove claude-revoke` or `sudo pacman -R claude-revoke`; if you turned on the scheduled audit, run `claude-revoke --schedule off` first.

If you installed from source before, run `./uninstall.sh` first. Otherwise the old copy in `~/.local/bin` keeps shadowing the package, and upgrades seem to do nothing.

### From source (any distro)

```bash
git clone https://github.com/atraxsrc/claude-revoke
cd claude-revoke
./install.sh
```

This installs the `claude-revoke` command into `~/.local/bin` and adds a **Claude Revoke** entry to the COSMIC launcher (Super, then type "revoke"), opening in COSMIC Terminal when it is installed. Remove both with `./uninstall.sh`.

To run it without installing:

```bash
python3 claude_revoke.py --dry-run
```

## Usage

```bash
claude-revoke --report                 # read-only audit, plain text
claude-revoke --report --only stale    # just the leftovers
claude-revoke --dry-run                # full UI, applying changes nothing
claude-revoke                          # the real thing (close Claude Code first)
claude-revoke --restore ~/.claude-revoke-quarantine/<timestamp>
claude-revoke --roots ~/code ~/work    # limit where it searches for project settings
claude-revoke --pause                  # wait for Enter before closing (the launcher entry uses this)
claude-revoke --version                # print the version
claude-revoke --schedule weekly        # weekly check with a desktop notification (daily | off)
claude-revoke --notify                 # what the scheduled check runs: scan, summary, notification
```

### Keys

| Key | Action |
| --- | --- |
| `↑` `↓` / `j` `k` | Move |
| `Enter` | Open a category / go back |
| `Space` | Tick a folder, or a whole group on its header |
| `a` / `n` | Tick / untick everything visible |
| `s` / `r` | Tick all stale / all risky |
| `v` | View: all → stale → risky → selected |
| `/` | Search by folder name |
| `Esc` / `q` | Back / quit |

<p align="center">
  <img src="assets/screenshot-menu.svg" alt="Main menu" width="92%">
</p>

## Scheduled audits

Let it check for you and only speak up when something needs a look:

```bash
claude-revoke --schedule weekly        # or daily; "off" turns it back off
```

Once a week a systemd user timer runs `claude-revoke --notify`: the same scan as `--report`, then a desktop notification such as "Trusted projects: 2 stale, 1 risky; Session transcripts: 1 with secrets. Run claude-revoke to review." You get one notification per change; while the situation stays the same it is only logged. Nothing found, nothing shown. It never changes anything; revoking stays a decision you make in the UI.

Under the hood: `claude-revoke-audit.timer` and `.service` (shipped by the packages in `/usr/lib/systemd/user/`, by `install.sh` in `~/.config/systemd/user/`), a drop-in with your chosen interval, and `notify-send` for the notification (`libnotify-bin` on Debian and Ubuntu, `libnotify` elsewhere). The scheduled run uses the default options (whole home, secret scan on); `systemctl --user edit claude-revoke-audit.service` changes that. Useful commands:

```bash
systemctl --user status claude-revoke-audit.timer     # when it runs next
journalctl --user -u claude-revoke-audit              # what past runs found
systemctl --user start claude-revoke-audit.service    # run one now
```

## Matching your terminal theme

claude-revoke never hard-codes colors. Everything comes from your terminal theme's 16-color palette, and the bars use one palette slot as the accent. Choose which one:

```bash
claude-revoke --accent magenta
claude-revoke --accent bright-cyan
claude-revoke --accent 12               # palette index 0-15
export CLAUDE_REVOKE_ACCENT=yellow      # make it permanent (~/.bashrc)
```

`NO_COLOR=1` gives a monochrome UI.

## What it reads and changes

| Location | What it is | Action |
| --- | --- | --- |
| `~/.claude.json` → `projects` | Trusted folders and their allow rules | Entry removed (file backed up) |
| `~/.claude.json` → `mcpServers` | MCP servers for every project | Entry removed (file backed up) |
| `~/.claude/projects/*` | Session transcripts | Moved to quarantine |
| `<project>/.claude/settings*.json` | Per-project rules and hooks | Moved to quarantine |
| `~/.claude/settings.json` | Global rules, extra dirs, hooks, bypass mode | Entry removed (file backed up) |

Everything removed lands in `~/.claude-revoke-quarantine/<timestamp>/` together with a `manifest.json`. Check that nothing broke, then delete that folder for good. `--restore` puts back only what that run removed, so anything Claude Code has written to its files since is kept.

> [!IMPORTANT]
> Close all Claude Code sessions before applying. A running session can write `~/.claude.json` again and undo your changes. claude-revoke warns you if it detects one.

> [!NOTE]
> Revoking only stops future auto-approval. If the scan finds secrets in transcripts, assume they were sent to the model during that session and **rotate them**.

## Limitations

This cleans up configuration and stored data. It does not sandbox Claude Code, which still runs with your user's permissions. For OS-enforced limits, use Claude Code's sandbox mode, [landrun](https://github.com/Zouuup/landrun), bubblewrap or a container.

Claude Code's file layout can change between versions. Run `--report` first and check it against your machine.

## Development

```bash
python3 -m unittest discover -s tests   # runs against a temporary fake home folder
packaging/build.sh                      # builds the .deb, .rpm and Arch packages into dist/
```

## Roadmap

- [ ] AUR and COPR packages if people ask for them

## See also

[git-safety-net](https://github.com/atraxsrc/git-safety-net): a global gitleaks
hook that blocks commits containing secrets in every repo on your machine, plus
a scanner for repos you already have. claude-revoke finds secrets in Claude's
session transcripts, git-safety-net keeps them out of git.

## License

MIT. See [LICENSE](LICENSE).

claude-revoke is an independent project. It is not affiliated with or endorsed by Anthropic. "Claude" is a trademark of Anthropic.
