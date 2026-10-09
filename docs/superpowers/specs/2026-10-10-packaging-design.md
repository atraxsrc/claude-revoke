# Packaging design: .deb, .rpm and Arch packages on GitHub Releases

Date: 2026-10-10. Status: approved in chat, implementation pending.

## Goal

Users on Debian/Ubuntu/Pop!_OS, Fedora and Arch install claude-revoke with
their package manager from one downloaded file, and get the launcher entry
and icon without running `install.sh`. Every GitHub release carries the
three packages and a checksum file, built and smoke-tested by CI. The
maintainer still bumps the version in exactly one place.

Out of scope: distro repositories (PPA, COPR, AUR), package signing,
architectures other than `all`/`noarch`/`any` (the tool is pure Python),
support for other agents.

## Package contents

Identical for all three formats.

| Path in package | Source | Mode |
| --- | --- | --- |
| `/usr/bin/claude-revoke` | `claude_revoke.py` | 0755 |
| `/usr/share/applications/claude-revoke.desktop` | generated from `packaging/claude-revoke.desktop.in` | 0644 |
| `/usr/share/icons/hicolor/scalable/apps/claude-revoke.svg` | `assets/logo.svg` | 0644 |
| `/usr/share/doc/claude-revoke/README.md` | `README.md` | 0644 |
| `/usr/share/doc/claude-revoke/CHANGELOG.md` | `CHANGELOG.md` | 0644 |
| `/usr/share/doc/claude-revoke/LICENSE` | `LICENSE` | 0644 |

Metadata:

- name `claude-revoke`, version = `__version__` from `claude_revoke.py`,
  release `1`, architecture `all` (deb) / `noarch` (rpm) / `any` (arch).
- description: "Audit and revoke Claude Code access on Linux" plus the
  README's one-paragraph summary.
- license `MIT`, homepage `https://github.com/atraxsrc/claude-revoke`.
- maintainer `atraxsrc <92285717+atraxsrc@users.noreply.github.com>`
  (the noreply address already in the public git history).
- dependencies: `python3 (>= 3.8)` on deb, `python3 >= 3.8` on rpm,
  `python` on arch. No others; the tool uses only the standard library.
- deb section `utils`, priority `optional`.

The generated desktop entry differs from the `install.sh` one in two
lines: `Exec=claude-revoke --pause` and `Terminal=true`. A package cannot
pick a terminal emulator at install time, so the desktop environment's
default terminal is used. `install.sh` and `uninstall.sh` are unchanged and
remain the from-source path.

## Build: `packaging/nfpm.yaml` and `packaging/build.sh`

`packaging/nfpm.yaml` is the single nfpm configuration. It takes the
version from the `VERSION` environment variable and lists the staged files
above.

`packaging/build.sh` (bash, `set -euo pipefail`) has two entry points:

- `build.sh stage`: reads the version from `claude_revoke.py`, creates
  `dist/stage/` with the exact tree from the table, generates the desktop
  entry with `sed` from the `.in` template, and writes nothing else. Needs
  no network and no nfpm. This is the part the unit test covers.
- `build.sh` (default): runs `stage`, then finds nfpm. If `nfpm` is on
  `PATH` it is used; otherwise the pinned release (`v2.47.0`,
  `nfpm_2.47.0_Linux_x86_64.tar.gz`, sha256
  `0660ca602b2d2d2ae4781a06c692b3eeb9d437ffea05b831d76e41f4a3188783`) is
  downloaded into `dist/tools/`, the checksum is verified before the
  archive is extracted, and a mismatch aborts the build. On a CPU
  architecture other than x86_64 the script stops with a message telling
  the user to install nfpm themselves. It then runs
  `nfpm package --packager deb|rpm|archlinux --target dist/` and writes
  `dist/SHA256SUMS` over the three packages.

Expected output for version 0.2.0 (file names are nfpm defaults and may differ slightly, e.g. the deb may carry the release number):
`dist/claude-revoke_0.2.0_all.deb`, `dist/claude-revoke-0.2.0-1.noarch.rpm`,
`dist/claude-revoke-0.2.0-1-any.pkg.tar.zst`, `dist/SHA256SUMS`.

`dist/` is added to `.gitignore`.

## CI: `.github/workflows/ci.yml`

Triggers: push to `main`, pull requests, `workflow_dispatch`, and
`release` with type `published`. Jobs:

1. `test` (ubuntu-latest): `python3 -m unittest discover -s tests`.
2. `build` (ubuntu-latest, needs `test`): `packaging/build.sh`, uploads
   `dist/` as an artifact named `packages`.
3. `smoke` (needs `build`), matrix over containers `debian:stable`,
   `fedora:latest`, `archlinux:latest`: downloads the artifact, installs
   the matching package (`apt-get install ./*.deb`, `dnf install ./*.rpm`,
   `pacman -U --noconfirm ./*.pkg.tar.zst`), then runs
   `claude-revoke --version` (must print the expected version),
   `claude-revoke --report --no-secret-scan` against an empty `HOME`,
   and `desktop-file-validate` on the installed entry.
4. `publish` (release event only, needs `smoke`, `permissions:
   contents: write`): asserts the release tag equals `v<__version__>`
   and fails otherwise, then `gh release upload "$TAG" dist/* --clobber`.

Actions are pinned by major version (`actions/checkout@v4`,
`actions/upload-artifact@v4`, `actions/download-artifact@v4`). The nfpm
version is pinned only in `build.sh`; CI uses the same script.

## README and roadmap

Install section, in this order:

1. "Packages": download from the Releases page, then
   `sudo apt install ./claude-revoke_*.deb`,
   `sudo dnf install ./claude-revoke-*.rpm`,
   `sudo pacman -U ./claude-revoke-*.pkg.tar.zst`; remove with
   `sudo apt remove claude-revoke`, `sudo dnf remove claude-revoke`,
   `sudo pacman -R claude-revoke`. Note that the launcher entry opens
   the desktop's default terminal.
2. "From source": the existing `git clone` + `./install.sh` text.

Roadmap: remove the `.deb`/PPA line (done) and the other-agents line
(dropped for now). Keep scheduled audits. Add "AUR and COPR packages if
people ask for them".

CHANGELOG: new "Unreleased" entry "Packages: .deb, .rpm and Arch packages
on every release, built and smoke-tested by CI. `--version` and the
changelog heading remain the only version bump."

## Testing

- `tests/test_packaging.py` (unittest, no network): runs
  `packaging/build.sh stage` in a temp copy and asserts the six staged
  files exist with the right modes, the desktop entry has
  `Exec=claude-revoke --pause` and `Terminal=true` and nothing else
  changed from the template, and the version the script reports equals
  `claude_revoke.__version__`.
- Local, once per change: full `packaging/build.sh`, then
  `dpkg-deb --info` and `--contents`, `lintian` on the `.deb`, and
  running `--version` and `--report` from the extracted package tree.
- CI smoke matrix covers Fedora and Arch, which cannot be tested on the
  development machine.

## Error handling

- Missing source file during staging: the script stops with the file name.
- Checksum mismatch on the nfpm download: the archive is deleted and the
  build stops.
- Tag/version mismatch at publish time: CI fails before uploading anything.
- Any smoke job failing blocks publish; the release stays without assets
  and can be re-run with `workflow_dispatch` after a fix.

## Release flow after this lands

1. Bump `__version__` and the CHANGELOG heading, merge to `main`.
2. CI on `main` must be green (packages were built and installed on all
   three distros).
3. Tag `vX.Y.Z`, push, publish the GitHub release. CI attaches the four
   files.
