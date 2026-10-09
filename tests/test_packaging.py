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


def run_build(script, *args, env=None, cwd=None, timeout=60):
    return subprocess.run(["bash", str(script), *args], env=dict(os.environ, **(env or {})),
                          cwd=cwd, capture_output=True, text=True, timeout=timeout)


# A stand-in for nfpm: writes an empty package with the real naming scheme into
# --target, so the whole build step can be exercised without network or nfpm.
STUB_NFPM = r"""#!/usr/bin/env bash
while [ $# -gt 0 ]; do
    case "$1" in --packager) p="$2"; shift ;; --target) t="$2"; shift ;; esac
    shift
done
case "$p" in
    deb) f="claude-revoke_${VERSION}-1_all.deb" ;;
    rpm) f="claude-revoke-${VERSION}-1.noarch.rpm" ;;
    archlinux) f="claude-revoke-${VERSION}-1-any.pkg.tar.zst" ;;
esac
: > "$t/$f"
"""


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


class TestBuildOutputFolder(unittest.TestCase):
    """The documented DIST override must not touch files that are not ours."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tree = Path(self.tmp.name)
        self.script = copy_sources(self.tree)
        stub = self.tree / "stub-nfpm"
        stub.write_text(STUB_NFPM)
        stub.chmod(0o755)
        self.env = {"NFPM": str(stub)}

    def tearDown(self):
        self.tmp.cleanup()

    def test_keeps_unrelated_packages_in_dist(self):
        out = self.tree / "out"
        out.mkdir()
        (out / "other-app_1.0_amd64.deb").write_text("keep me")
        (out / "other.rpm").write_text("keep me")
        r = run_build(self.script, env={**self.env, "DIST": str(out)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((out / "other-app_1.0_amd64.deb").read_text(), "keep me")
        self.assertEqual((out / "other.rpm").read_text(), "keep me")
        sums = (out / "SHA256SUMS").read_text()
        self.assertNotIn("other", sums)
        self.assertEqual(len(sums.splitlines()), 3)

    def test_replaces_our_older_packages_in_dist(self):
        out = self.tree / "out"
        out.mkdir()
        (out / "claude-revoke_0.0.1-1_all.deb").write_text("stale")
        r = run_build(self.script, env={**self.env, "DIST": str(out)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((out / "claude-revoke_0.0.1-1_all.deb").exists())
        self.assertEqual(len(list(out.glob("claude-revoke*"))), 3)

    def test_relative_dist_is_resolved_from_the_current_directory(self):
        r = run_build(self.script, env={**self.env, "DIST": "out"}, cwd=self.tree)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(list((self.tree / "out").glob("claude-revoke*"))), 3)
        self.assertTrue((self.tree / "out" / "SHA256SUMS").is_file())


if __name__ == "__main__":
    unittest.main()
