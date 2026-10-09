# Packaging (.deb, .rpm, Arch) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every GitHub release carries a `.deb`, an `.rpm`, an Arch package and a `SHA256SUMS`, built from one nfpm config and smoke-tested on Debian, Fedora and Arch by CI.

**Architecture:** `packaging/build.sh` stages the six package files plus a deb `copyright` copy into `dist/stage/`, then runs a pinned, checksum-verified nfpm over `packaging/nfpm.yaml` to produce the three packages. `.github/workflows/ci.yml` runs the unit tests, builds, installs each package in a distro container, and on a published release attaches the files. Version comes only from `__version__` in `claude_revoke.py`.

**Tech Stack:** bash, nfpm 2.47.0, GitHub Actions, Python `unittest` (standard library only, as the rest of the project).

**Spec:** `docs/superpowers/specs/2026-10-10-packaging-design.md`

## Global Constraints

- Python 3.8+ and standard library only in `claude_revoke.py` and `tests/`; no new runtime dependencies.
- nfpm pinned to `2.47.0`; tarball `nfpm_2.47.0_Linux_x86_64.tar.gz`, sha256 `0660ca602b2d2d2ae4781a06c692b3eeb9d437ffea05b831d76e41f4a3188783`; a mismatch aborts the build and deletes the download.
- Package name `claude-revoke`, release `1`, architecture `all`/`noarch`/`any`, license `MIT`, homepage `https://github.com/atraxsrc/claude-revoke`, maintainer `atraxsrc <92285717+atraxsrc@users.noreply.github.com>`.
- Dependencies: deb `python3 (>= 3.8)`, rpm `python3 >= 3.8`, arch `python`.
- Generated desktop entry: `Exec=claude-revoke --pause`, `Terminal=true`, every other line identical to `packaging/claude-revoke.desktop.in`.
- `install.sh`, `uninstall.sh` and `packaging/claude-revoke.desktop.in` are not changed.
- No em-dashes anywhere. Commits authored as the repo's existing `atraxsrc` noreply identity, never pushed from this session.
- `dist/` is gitignored; nothing under it is ever committed.

## Review Focus

1. A source file listed for staging is missing (e.g. `LICENSE` deleted): `build.sh stage` must stop with a non-zero status naming the file, not produce a package with a hole in it. Pinned by `test_stage_fails_when_a_source_file_is_missing` in Task 1.
2. `__version__` cannot be read from `claude_revoke.py` (line renamed or quoted differently): the build must stop, not produce `claude-revoke__all.deb`. Pinned by `test_stage_fails_without_a_readable_version` in Task 1.
3. The nfpm download does not match the pinned checksum (network tampering, wrong pin): the build must stop before extracting anything and must not leave the archive behind. Pinned by `test_build_aborts_on_nfpm_checksum_mismatch` in Task 2 using a `file://` URL.
4. `nfpm.yaml` and the staged tree drift apart (a file added to one but not the other): nfpm would fail late or ship an incomplete package. Pinned by `test_every_src_in_nfpm_yaml_is_staged` in Task 2.
5. The release tag does not match `__version__` (someone tags `v0.3.0` while the script says `0.2.0`): the publish job must fail before uploading. This runs only in CI; the check is a plain string comparison in Task 3, verified by reading, and exercised the first time a release is cut.

---

### Task 1: Staging step (`build.sh stage`)

**Files:**
- Create: `packaging/build.sh` (executable)
- Create: `tests/test_packaging.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `__version__ = "X.Y.Z"` line in `claude_revoke.py`; `packaging/claude-revoke.desktop.in`.
- Produces: `packaging/build.sh stage` creating `$DIST/stage/` (default `DIST=<repo>/dist`) with exactly these files, and printing `staged <version> in <stage dir>` on stdout:

| staged path | mode |
| --- | --- |
| `usr/bin/claude-revoke` | 0755 |
| `usr/share/applications/claude-revoke.desktop` | 0644 |
| `usr/share/icons/hicolor/scalable/apps/claude-revoke.svg` | 0644 |
| `usr/share/doc/claude-revoke/README.md` | 0644 |
| `usr/share/doc/claude-revoke/CHANGELOG.md` | 0644 |
| `usr/share/doc/claude-revoke/LICENSE` | 0644 |
| `usr/share/doc/claude-revoke/copyright` | 0644 |

  (`copyright` is a second copy of `LICENSE`; Debian policy expects that name.) Task 2 relies on these exact relative paths and on the `DIST` and `NFPM_URL`/`NFPM_SHA256` environment overrides.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_packaging.py`:

```python
"""Tests for packaging/build.sh. The staging step needs no network and no nfpm."""
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "packaging" / "build.sh"

sys.path.insert(0, str(ROOT))
import claude_revoke as cr  # noqa: E402

STAGED = {
    "usr/bin/claude-revoke": 0o755,
    "usr/share/applications/claude-revoke.desktop": 0o644,
    "usr/share/icons/hicolor/scalable/apps/claude-revoke.svg": 0o644,
    "usr/share/doc/claude-revoke/README.md": 0o644,
    "usr/share/doc/claude-revoke/CHANGELOG.md": 0o644,
    "usr/share/doc/claude-revoke/LICENSE": 0o644,
    "usr/share/doc/claude-revoke/copyright": 0o644,
}

# Everything build.sh reads from the repo; copied into a temp tree by the failure tests.
SOURCES = ["claude_revoke.py", "assets/logo.svg", "README.md", "CHANGELOG.md", "LICENSE",
           "packaging/build.sh", "packaging/claude-revoke.desktop.in", "packaging/nfpm.yaml"]


def run_build(script, *args, env=None, timeout=60):
    return subprocess.run(["bash", str(script), *args], env=dict(os.environ, **(env or {})),
                          capture_output=True, text=True, timeout=timeout)


def copy_sources(dest, skip=()):
    """A minimal repo copy so build.sh can be run against a broken tree."""
    for rel in SOURCES:
        if rel in skip or not (ROOT / rel).exists():
            continue
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dest / rel)
    return dest / "packaging" / "build.sh"


class TestStage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dist = Path(cls.tmp.name) / "dist"
        r = run_build(BUILD, "stage", env={"DIST": str(cls.dist)})
        assert r.returncode == 0, r.stderr
        cls.out = r.stdout
        cls.stage = cls.dist / "stage"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_stages_exactly_the_packaged_files_with_modes(self):
        found = {p.relative_to(self.stage).as_posix(): stat.S_IMODE(p.stat().st_mode)
                 for p in self.stage.rglob("*") if p.is_file()}
        self.assertEqual(found, STAGED)

    def test_staged_script_is_the_real_one(self):
        self.assertEqual((self.stage / "usr/bin/claude-revoke").read_bytes(),
                         (ROOT / "claude_revoke.py").read_bytes())

    def test_desktop_entry_uses_the_default_terminal(self):
        lines = (self.stage / "usr/share/applications/claude-revoke.desktop").read_text().splitlines()
        self.assertIn("Exec=claude-revoke --pause", lines)
        self.assertIn("Terminal=true", lines)
        template = (ROOT / "packaging/claude-revoke.desktop.in").read_text().splitlines()
        self.assertEqual([l for l in template if l not in lines],
                         ["Exec=@TERM@ @BIN@ --pause", "Terminal=false"])
        self.assertEqual(len(lines), len(template))

    def test_reports_the_script_version(self):
        self.assertIn(f"staged {cr.__version__} in ", self.out)


class TestStageFailures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tree = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_stage_fails_when_a_source_file_is_missing(self):
        script = copy_sources(self.tree, skip=("LICENSE",))
        r = run_build(script, "stage")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("LICENSE", r.stderr)
        self.assertFalse((self.tree / "dist" / "stage" / "usr/bin/claude-revoke").exists())

    def test_stage_fails_without_a_readable_version(self):
        script = copy_sources(self.tree)
        src = self.tree / "claude_revoke.py"
        src.write_text(src.read_text().replace('__version__ = "', '__version__ = \'', 1))
        r = run_build(script, "stage")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("__version__", r.stderr)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/0xdev1/Documents/projects/repos/github/claude-revoke && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_packaging -v 2>&1 | tail -20`

Expected: `TestStage` errors in `setUpClass` because `packaging/build.sh` does not exist (`bash: .../build.sh: No such file or directory`), and both `TestStageFailures` tests fail on the `assertIn` (bash exits 127 with the same message, so `returncode != 0` passes but "LICENSE"/"__version__" is not in stderr).

- [ ] **Step 3: Write `packaging/build.sh` with the staging step only**

```bash
#!/usr/bin/env bash
# Build claude-revoke packages (.deb, .rpm and Arch) with nfpm.
#
#   packaging/build.sh          stage the files, fetch nfpm if needed, build into dist/
#   packaging/build.sh stage    only create dist/stage/ (no network, no nfpm)
#
# The version is read from __version__ in claude_revoke.py. Override the output
# folder with DIST=<dir>.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dist="${DIST:-$root/dist}"
stage="$dist/stage"

fail() { echo "build.sh: $*" >&2; exit 1; }

version() {
    local v
    v="$(sed -n 's/^__version__ = "\([^"]*\)"$/\1/p' "$root/claude_revoke.py")"
    [ -n "$v" ] || fail "could not read __version__ from claude_revoke.py"
    echo "$v"
}

put() {   # put <repo file> <path inside the package> <mode>
    [ -f "$root/$1" ] || fail "missing source file $1"
    install -D -m "$3" "$root/$1" "$stage$2"
}

do_stage() {
    local v f
    v="$(version)"
    # Check every input before touching dist/, so a failure leaves no half-staged tree.
    for f in claude_revoke.py assets/logo.svg README.md CHANGELOG.md LICENSE packaging/claude-revoke.desktop.in; do
        [ -f "$root/$f" ] || fail "missing source file $f"
    done
    rm -rf "$stage"
    put claude_revoke.py /usr/bin/claude-revoke 755
    put assets/logo.svg /usr/share/icons/hicolor/scalable/apps/claude-revoke.svg 644
    put README.md /usr/share/doc/claude-revoke/README.md 644
    put CHANGELOG.md /usr/share/doc/claude-revoke/CHANGELOG.md 644
    put LICENSE /usr/share/doc/claude-revoke/LICENSE 644
    put LICENSE /usr/share/doc/claude-revoke/copyright 644     # the name Debian policy expects
    # A package cannot pick a terminal emulator at install time the way
    # install.sh does, so the desktop's default terminal is used.
    install -d -m 755 "$stage/usr/share/applications"
    sed -e 's|^Exec=.*|Exec=claude-revoke --pause|' -e 's|^Terminal=false$|Terminal=true|' \
        "$root/packaging/claude-revoke.desktop.in" > "$stage/usr/share/applications/claude-revoke.desktop"
    chmod 644 "$stage/usr/share/applications/claude-revoke.desktop"
    echo "staged $v in $stage"
}

case "${1:-build}" in
    stage) do_stage ;;
    build) fail "the build step is not implemented yet" ;;
    *) echo "usage: $0 [stage|build]" >&2; exit 2 ;;
esac
```

Then: `chmod 755 packaging/build.sh`

- [ ] **Step 4: Add `dist/` to `.gitignore`**

Append to `.gitignore`:

```
dist/
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd /home/0xdev1/Documents/projects/repos/github/claude-revoke && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_packaging -v 2>&1 | tail -12`

Expected: 6 tests, `OK`. Then the whole suite: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests 2>&1 | tail -3` must show `OK` with 40 tests.

- [ ] **Step 6: Commit**

```bash
cd /home/0xdev1/Documents/projects/repos/github/claude-revoke
GIT_CONFIG_GLOBAL=/dev/null git add packaging/build.sh tests/test_packaging.py .gitignore
GIT_CONFIG_GLOBAL=/dev/null gitleaks git --pre-commit --staged --redact --no-banner --log-level warn .
GIT_CONFIG_GLOBAL=/dev/null GIT_AUTHOR_NAME=atraxsrc GIT_AUTHOR_EMAIL=92285717+atraxsrc@users.noreply.github.com GIT_COMMITTER_NAME=atraxsrc GIT_COMMITTER_EMAIL=92285717+atraxsrc@users.noreply.github.com git commit -q -m "packaging: staging step of build.sh with tests"
```

---

### Task 2: nfpm config and the full build

**Files:**
- Create: `packaging/nfpm.yaml`
- Modify: `packaging/build.sh` (replace the `build)` case and add `find_nfpm`/`do_build`)
- Modify: `tests/test_packaging.py` (add `TestNfpmConfig`, `TestBuildFailures`)

**Interfaces:**
- Consumes: the staged tree and `DIST` override from Task 1.
- Produces: `packaging/build.sh` (no argument) writing into `$DIST`: one `*.deb`, one `*.rpm`, one `*.pkg.tar.zst` and `SHA256SUMS`; exit non-zero on any failure. Environment overrides `NFPM_URL` (download URL, default GitHub release) and `NFPM_SHA256` (expected checksum). Task 3's CI calls `packaging/build.sh` with no arguments and uploads `dist/*.deb dist/*.rpm dist/*.pkg.tar.zst dist/SHA256SUMS`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_packaging.py` before `if __name__ == "__main__":`:

```python
class TestNfpmConfig(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "packaging" / "nfpm.yaml").read_text()

    def test_every_src_in_nfpm_yaml_is_staged(self):
        srcs = re.findall(r"^\s*-\s*src:\s*(\S+)\s*$", self.text, re.M)
        self.assertEqual(sorted(srcs), sorted(STAGED))

    def test_dst_matches_src_under_root(self):
        pairs = re.findall(r"src:\s*(\S+)\s*\n\s*dst:\s*(\S+)", self.text)
        self.assertEqual(len(pairs), len(STAGED))
        for src, dst in pairs:
            self.assertEqual(dst, "/" + src)

    def test_version_comes_from_the_environment(self):
        self.assertIn("version: ${VERSION}", self.text)


class TestBuildFailures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tree = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_aborts_on_nfpm_checksum_mismatch(self):
        script = copy_sources(self.tree)
        fake = self.tree / "fake-nfpm.tar.gz"
        fake.write_bytes(b"not a real archive")
        env = {"PATH": "/usr/bin:/bin",            # no nfpm on PATH, so the download path is taken
               "NFPM_URL": fake.as_uri(),
               "NFPM_SHA256": "0" * 64}
        r = run_build(script, env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("checksum", r.stderr.lower())
        tools = self.tree / "dist" / "tools"
        self.assertEqual(list(tools.glob("*.tar.gz")) if tools.exists() else [], [])
        self.assertFalse((tools / "nfpm").exists())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/0xdev1/Documents/projects/repos/github/claude-revoke && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_packaging -v 2>&1 | tail -20`

Expected: the three `TestNfpmConfig` tests error with `FileNotFoundError` for `packaging/nfpm.yaml`; `test_build_aborts_on_nfpm_checksum_mismatch` fails because stderr says `the build step is not implemented yet` and does not contain "checksum".

- [ ] **Step 3: Create `packaging/nfpm.yaml`**

```yaml
# One description of the package; packaging/build.sh turns it into .deb, .rpm
# and Arch packages. build.sh sets VERSION and runs nfpm from dist/stage/, so
# every src below is relative to that staged tree.
name: claude-revoke
arch: all
platform: linux
version: ${VERSION}
release: "1"
section: utils
priority: optional
maintainer: atraxsrc <92285717+atraxsrc@users.noreply.github.com>
vendor: atraxsrc
homepage: https://github.com/atraxsrc/claude-revoke
license: MIT
description: |
  Audit and revoke Claude Code access on Linux.
  An installer-style terminal UI in reverse: it shows every folder Claude
  Code was trusted with, every "always allow" rule, extra directory, saved
  transcript, hook and MCP server, lets you tick what to remove, and
  quarantines it instead of deleting so a run can be undone.
depends:
  - python3 (>= 3.8)
contents:
  - src: usr/bin/claude-revoke
    dst: /usr/bin/claude-revoke
    file_info:
      mode: 0755
  - src: usr/share/applications/claude-revoke.desktop
    dst: /usr/share/applications/claude-revoke.desktop
    file_info:
      mode: 0644
  - src: usr/share/icons/hicolor/scalable/apps/claude-revoke.svg
    dst: /usr/share/icons/hicolor/scalable/apps/claude-revoke.svg
    file_info:
      mode: 0644
  - src: usr/share/doc/claude-revoke/README.md
    dst: /usr/share/doc/claude-revoke/README.md
    file_info:
      mode: 0644
  - src: usr/share/doc/claude-revoke/CHANGELOG.md
    dst: /usr/share/doc/claude-revoke/CHANGELOG.md
    file_info:
      mode: 0644
  - src: usr/share/doc/claude-revoke/LICENSE
    dst: /usr/share/doc/claude-revoke/LICENSE
    file_info:
      mode: 0644
  - src: usr/share/doc/claude-revoke/copyright
    dst: /usr/share/doc/claude-revoke/copyright
    file_info:
      mode: 0644
overrides:
  rpm:
    depends:
      - python3 >= 3.8
  archlinux:
    depends:
      - python
```

- [ ] **Step 4: Add the build step to `packaging/build.sh`**

Insert after the `set -euo pipefail` line:

```bash
# Pinned nfpm release used when nfpm is not already on PATH.
NFPM_VERSION="2.47.0"
NFPM_TGZ="nfpm_${NFPM_VERSION}_Linux_x86_64.tar.gz"
NFPM_URL="${NFPM_URL:-https://github.com/goreleaser/nfpm/releases/download/v${NFPM_VERSION}/${NFPM_TGZ}}"
NFPM_SHA256="${NFPM_SHA256:-0660ca602b2d2d2ae4781a06c692b3eeb9d437ffea05b831d76e41f4a3188783}"
```

Insert after the `do_stage()` function:

```bash
find_nfpm() {
    if command -v nfpm >/dev/null 2>&1; then
        command -v nfpm
        return
    fi
    local bin="$dist/tools/nfpm" tgz="$dist/tools/$NFPM_TGZ"
    if [ ! -x "$bin" ]; then
        [ "$(uname -m)" = "x86_64" ] || fail "no pinned nfpm for $(uname -m): install nfpm (https://nfpm.goreleaser.com) and re-run"
        mkdir -p "$dist/tools"
        echo "downloading nfpm $NFPM_VERSION" >&2
        curl -sSfL -o "$tgz" "$NFPM_URL"
        if ! echo "$NFPM_SHA256  $tgz" | sha256sum -c --status -; then
            rm -f "$tgz"
            fail "nfpm download does not match the pinned checksum, aborting"
        fi
        tar -xzf "$tgz" -C "$dist/tools" nfpm
        rm -f "$tgz"
    fi
    echo "$bin"
}

do_build() {
    do_stage
    local nfpm v
    nfpm="$(find_nfpm)"
    v="$(version)"
    rm -f "$dist"/*.deb "$dist"/*.rpm "$dist"/*.pkg.tar.zst "$dist/SHA256SUMS"
    for packager in deb rpm archlinux; do
        (cd "$stage" && VERSION="$v" "$nfpm" package --config "$root/packaging/nfpm.yaml" \
            --packager "$packager" --target "$dist/")
    done
    (cd "$dist" && sha256sum ./*.deb ./*.rpm ./*.pkg.tar.zst | sed 's|^\([^ ]*\)  \./|\1  |' > SHA256SUMS)
    echo
    echo "Packages in $dist:"
    (cd "$dist" && ls -1 ./*.deb ./*.rpm ./*.pkg.tar.zst SHA256SUMS | sed 's|^\./|  |;s|^SHA|  SHA|')
}
```

Replace the `case` block at the end with:

```bash
case "${1:-build}" in
    stage) do_stage ;;
    build) do_build ;;
    *) echo "usage: $0 [stage|build]" >&2; exit 2 ;;
esac
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd /home/0xdev1/Documents/projects/repos/github/claude-revoke && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_packaging -v 2>&1 | tail -14`

Expected: 10 tests, `OK`.

- [ ] **Step 6: Run the real build and inspect the `.deb`**

Run (needs network to github.com for the nfpm download):

```bash
cd /home/0xdev1/Documents/projects/repos/github/claude-revoke
packaging/build.sh
dpkg-deb --info dist/*.deb
dpkg-deb --contents dist/*.deb
lintian dist/*.deb
rm -rf "$TMPDIR/x" && dpkg-deb -x dist/*.deb "$TMPDIR/x"
"$TMPDIR/x/usr/bin/claude-revoke" --version
mkdir -p "$TMPDIR/empty-home" && HOME="$TMPDIR/empty-home" "$TMPDIR/x/usr/bin/claude-revoke" --report --no-secret-scan
desktop-file-validate "$TMPDIR/x/usr/share/applications/claude-revoke.desktop"
(cd dist && sha256sum -c SHA256SUMS)
```

Expected: `--info` shows `Package: claude-revoke`, `Version: 0.2.0-1`, `Architecture: all`, `Depends: python3 (>= 3.8)`, `Maintainer: atraxsrc <...>`; `--contents` lists the seven files with `-rwxr-xr-x` on `usr/bin/claude-revoke` and `-rw-r--r--` elsewhere; `--version` prints `claude-revoke 0.2.0`; the report prints four `== ... ==` headings; `desktop-file-validate` prints nothing; `sha256sum -c` prints three `OK` lines. lintian may print warnings (e.g. `no-changelog`, `extended-description` style tags); errors about missing files or wrong modes are not acceptable and must be fixed before moving on. Record what lintian printed in the task notes.

- [ ] **Step 7: Confirm `dist/` is ignored and commit**

Run: `GIT_CONFIG_GLOBAL=/dev/null git status --short | grep -c '^?? dist' ` must print `0`.

```bash
cd /home/0xdev1/Documents/projects/repos/github/claude-revoke
GIT_CONFIG_GLOBAL=/dev/null git add packaging/nfpm.yaml packaging/build.sh tests/test_packaging.py
GIT_CONFIG_GLOBAL=/dev/null gitleaks git --pre-commit --staged --redact --no-banner --log-level warn .
GIT_CONFIG_GLOBAL=/dev/null GIT_AUTHOR_NAME=atraxsrc GIT_AUTHOR_EMAIL=92285717+atraxsrc@users.noreply.github.com GIT_COMMITTER_NAME=atraxsrc GIT_COMMITTER_EMAIL=92285717+atraxsrc@users.noreply.github.com git commit -q -m "packaging: nfpm config and build.sh producing deb, rpm and Arch packages"
```

---

### Task 3: CI workflow

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `packaging/build.sh` (Task 2) and its outputs in `dist/`; `__version__` in `claude_revoke.py`.
- Produces: a workflow named `CI` with jobs `test`, `build` (output `version`), `smoke` (matrix), `publish`. Task 4's README refers to release assets produced by `publish`.

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:
  release:
    types: [published]

permissions:
  contents: read

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: python3 -m unittest discover -s tests -v

  build:
    needs: test
    runs-on: ubuntu-latest
    outputs:
      version: ${{ steps.version.outputs.version }}
    steps:
      - uses: actions/checkout@v4
      - id: version
        run: echo "version=$(sed -n 's/^__version__ = "\([^"]*\)"$/\1/p' claude_revoke.py)" >> "$GITHUB_OUTPUT"
      - run: packaging/build.sh
      - uses: actions/upload-artifact@v4
        with:
          name: packages
          path: |
            dist/*.deb
            dist/*.rpm
            dist/*.pkg.tar.zst
            dist/SHA256SUMS
          if-no-files-found: error

  smoke:
    needs: build
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        include:
          - image: debian:stable
            install: apt-get update -qq && apt-get install -y -qq --no-install-recommends ./dist/*.deb desktop-file-utils
          - image: fedora:latest
            install: dnf install -y ./dist/*.rpm desktop-file-utils
          - image: archlinux:latest
            install: pacman -Sy --noconfirm && pacman -U --noconfirm ./dist/*.pkg.tar.zst && pacman -S --noconfirm desktop-file-utils
    container: ${{ matrix.image }}
    env:
      EXPECTED: claude-revoke ${{ needs.build.outputs.version }}
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: packages
          path: dist
      - name: Install the package
        run: ${{ matrix.install }}
      - name: Run the installed tool
        run: |
          actual="$(claude-revoke --version)"
          [ "$actual" = "$EXPECTED" ] || { echo "got '$actual', want '$EXPECTED'"; exit 1; }
          mkdir -p /tmp/empty-home
          HOME=/tmp/empty-home claude-revoke --report --no-secret-scan
          desktop-file-validate /usr/share/applications/claude-revoke.desktop
          cd dist && sha256sum -c SHA256SUMS

  publish:
    if: github.event_name == 'release'
    needs: smoke
    runs-on: ubuntu-latest
    permissions:
      contents: write
    env:
      TAG: ${{ github.event.release.tag_name }}
      GH_TOKEN: ${{ github.token }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/download-artifact@v4
        with:
          name: packages
          path: dist
      - name: Check the release tag matches __version__
        run: |
          want="v$(sed -n 's/^__version__ = "\([^"]*\)"$/\1/p' claude_revoke.py)"
          [ "$TAG" = "$want" ] || { echo "release tag $TAG does not match $want"; exit 1; }
      - name: Attach the packages to the release
        run: gh release upload "$TAG" dist/* --clobber
```

- [ ] **Step 2: Validate the YAML and the job wiring**

Run:

```bash
cd /home/0xdev1/Documents/projects/repos/github/claude-revoke
python3 - <<'EOF'
import yaml
w = yaml.safe_load(open(".github/workflows/ci.yml"))
jobs = w["jobs"]
assert list(jobs) == ["test", "build", "smoke", "publish"], list(jobs)
assert jobs["build"]["needs"] == "test" and jobs["smoke"]["needs"] == "build" and jobs["publish"]["needs"] == "smoke"
assert jobs["publish"]["if"] == "github.event_name == 'release'"
assert jobs["publish"]["permissions"] == {"contents": "write"}
assert w["permissions"] == {"contents": "read"}
assert [m["image"] for m in jobs["smoke"]["strategy"]["matrix"]["include"]] == ["debian:stable", "fedora:latest", "archlinux:latest"]
assert set(w[True]) == {"push", "pull_request", "workflow_dispatch", "release"}   # PyYAML parses the key `on` as True
print("workflow ok")
EOF
```

Expected: `workflow ok`. (PyYAML 6.0.1 is installed on this machine; it is only used for this check, not by the project.)

- [ ] **Step 3: Commit**

```bash
cd /home/0xdev1/Documents/projects/repos/github/claude-revoke
GIT_CONFIG_GLOBAL=/dev/null git add .github/workflows/ci.yml
GIT_CONFIG_GLOBAL=/dev/null gitleaks git --pre-commit --staged --redact --no-banner --log-level warn .
GIT_CONFIG_GLOBAL=/dev/null GIT_AUTHOR_NAME=atraxsrc GIT_AUTHOR_EMAIL=92285717+atraxsrc@users.noreply.github.com GIT_COMMITTER_NAME=atraxsrc GIT_COMMITTER_EMAIL=92285717+atraxsrc@users.noreply.github.com git commit -q -m "ci: test, build packages, smoke-test on Debian/Fedora/Arch, attach to releases"
```

---

### Task 4: README, CHANGELOG and the version test

**Files:**
- Modify: `README.md` (Install section lines 37-45, Development section line 118-120, Roadmap section)
- Modify: `CHANGELOG.md` (new `## Unreleased` at the top)
- Modify: `tests/test_claude_revoke.py` (`TestVersion.test_changelog_top_entry_matches_version`)

**Interfaces:**
- Consumes: nothing from code; documents the Task 2 build and Task 3 release assets.
- Produces: the changelog rule "top heading is `## Unreleased` or `## <__version__> - <date>`, and a `## <__version__> - ` heading exists" that later releases must follow.

- [ ] **Step 1: Write the failing test**

In `tests/test_claude_revoke.py`, replace the body of `test_changelog_top_entry_matches_version` with:

```python
    def test_changelog_top_entry_matches_version(self):
        heads = [l for l in (ROOT / "CHANGELOG.md").read_text().splitlines() if l.startswith("## ")]
        released = f"## {cr.__version__} - "
        self.assertTrue(heads[0] == "## Unreleased" or heads[0].startswith(released), heads[0])
        self.assertTrue(any(h.startswith(released) for h in heads), heads)
        self.assertEqual(heads.count("## Unreleased"), 1 if heads[0] == "## Unreleased" else 0)
```

- [ ] **Step 2: Run the test**

This change relaxes the rule (an `## Unreleased` section is now allowed on top), so it passes on the current CHANGELOG too. The point of changing it first is that Step 3 would fail the old assertion `heads[0].startswith("## 0.2.0 - ")`. Run:

`cd /home/0xdev1/Documents/projects/repos/github/claude-revoke && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_claude_revoke.TestVersion -v 2>&1 | tail -5`

Expected: 2 tests, `OK`.

- [ ] **Step 3: Add the CHANGELOG entry**

At the top of `CHANGELOG.md`, replace:

```markdown
# Changelog

## 0.2.0 - 2026-10-10
```

with:

```markdown
# Changelog

## Unreleased

- Packages: `.deb`, `.rpm` and Arch packages are built from one nfpm config, installed and smoke-tested on Debian, Fedora and Arch by CI, and attached to every GitHub release together with `SHA256SUMS`. The launcher entry from a package opens your desktop's default terminal.
- CI runs the test suite on every push and pull request.

## 0.2.0 - 2026-10-10
```

- [ ] **Step 4: Update the README Install section**

Replace:

```markdown
## Install (Pop!_OS / Ubuntu / Debian)

```bash
git clone https://github.com/atraxsrc/claude-revoke
cd claude-revoke
./install.sh
```

This installs the `claude-revoke` command into `~/.local/bin` and adds a **Claude Revoke** entry to the COSMIC launcher (Super, then type "revoke"). Remove both with `./uninstall.sh`.
```

with:

```markdown
## Install

### Packages (Debian, Ubuntu, Pop!_OS, Fedora, Arch)

Download the file for your distro from the [latest release](https://github.com/atraxsrc/claude-revoke/releases/latest), then:

```bash
sudo apt install ./claude-revoke_*.deb          # Debian, Ubuntu, Pop!_OS
sudo dnf install ./claude-revoke-*.rpm          # Fedora
sudo pacman -U ./claude-revoke-*.pkg.tar.zst    # Arch
```

This installs the `claude-revoke` command and a **Claude Revoke** launcher entry that opens in your desktop's default terminal. `SHA256SUMS` on the release page lets you check the download. Remove with `sudo apt remove claude-revoke`, `sudo dnf remove claude-revoke` or `sudo pacman -R claude-revoke`.

### From source (any distro)

```bash
git clone https://github.com/atraxsrc/claude-revoke
cd claude-revoke
./install.sh
```

This installs the `claude-revoke` command into `~/.local/bin` and adds a **Claude Revoke** entry to the COSMIC launcher (Super, then type "revoke"), opening in COSMIC Terminal when it is installed. Remove both with `./uninstall.sh`.
```

- [ ] **Step 5: Update the README Development and Roadmap sections**

Replace:

```markdown
```bash
python3 -m unittest discover -s tests   # runs against a temporary fake home folder
```
```

with:

```markdown
```bash
python3 -m unittest discover -s tests   # runs against a temporary fake home folder
packaging/build.sh                      # builds the .deb, .rpm and Arch packages into dist/
```
```

Replace:

```markdown
- [ ] `.deb` package and Pop!_OS install via a PPA
- [ ] Support for other agents (Codex CLI, Gemini CLI, Cursor)
- [ ] Scheduled audits with a desktop notification
```

with:

```markdown
- [ ] Scheduled audits with a desktop notification
- [ ] AUR and COPR packages if people ask for them
```

- [ ] **Step 6: Run the whole suite and re-stage**

Run: `cd /home/0xdev1/Documents/projects/repos/github/claude-revoke && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests 2>&1 | tail -3`

Expected: 44 tests, `OK`. (The README and CHANGELOG are packaged, so `TestStage` re-runs the staging step over the new text.)

- [ ] **Step 7: Commit**

```bash
cd /home/0xdev1/Documents/projects/repos/github/claude-revoke
GIT_CONFIG_GLOBAL=/dev/null git add README.md CHANGELOG.md tests/test_claude_revoke.py
GIT_CONFIG_GLOBAL=/dev/null gitleaks git --pre-commit --staged --redact --no-banner --log-level warn .
GIT_CONFIG_GLOBAL=/dev/null GIT_AUTHOR_NAME=atraxsrc GIT_AUTHOR_EMAIL=92285717+atraxsrc@users.noreply.github.com GIT_COMMITTER_NAME=atraxsrc GIT_COMMITTER_EMAIL=92285717+atraxsrc@users.noreply.github.com git commit -q -m "docs: package install instructions, changelog, roadmap"
```

---

### Task 5: Final verification and handoff

**Files:** none new.

- [ ] **Step 1: Full rebuild from a clean `dist/`**

```bash
cd /home/0xdev1/Documents/projects/repos/github/claude-revoke
rm -rf dist
packaging/build.sh
dpkg-deb --contents dist/*.deb | awk '{print $1, $NF}'
lintian dist/*.deb
rm -rf "$TMPDIR/x" && dpkg-deb -x dist/*.deb "$TMPDIR/x" && "$TMPDIR/x/usr/bin/claude-revoke" --version
desktop-file-validate "$TMPDIR/x/usr/share/applications/claude-revoke.desktop"
(cd dist && sha256sum -c SHA256SUMS)
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests 2>&1 | tail -3
GIT_CONFIG_GLOBAL=/dev/null git status --short | grep -v '^??' ; echo "tree clean when nothing printed above"
GIT_CONFIG_GLOBAL=/dev/null git log --format='%h %s' main..HEAD
```

Expected: seven packaged files with the right modes, `claude-revoke 0.2.0`, no desktop-file-validate output, three `OK` checksum lines, 44 tests `OK`, clean tree, and six commits on `feat/packaging` ahead of `main`: spec, plan, Task 1, Task 2, Task 3, Task 4.

- [ ] **Step 2: Hand off**

Report to the user: the branch name, what each commit does, the lintian output verbatim, and the merge command. The user merges and pushes (`git switch main && git merge feat/packaging && git push origin main`); CI then runs on `main`. Watch the run with `gh run watch` or on the Actions tab; the `smoke` matrix is the first time the `.rpm` and Arch package are installed anywhere. Only after that run is green should the next release be cut (bump `__version__`, rename `## Unreleased` to `## 0.3.0 - <date>`, tag `v0.3.0`, publish the release; `publish` attaches the packages).
