"""Tests for claude_revoke, run against a throwaway home folder.

    python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import claude_revoke as cr  # noqa: E402


def mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


class FakeHome(unittest.TestCase):
    """Points every path constant in claude_revoke at a temp home folder."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._saved = {}
        paths = {
            "HOME": self.home,
            "CLAUDE_JSON": self.home / ".claude.json",
            "CLAUDE_DIR": self.home / ".claude",
            "SESSIONS_DIR": self.home / ".claude" / "projects",
            "GLOBAL_SETTINGS": self.home / ".claude" / "settings.json",
            "QUARANTINE_ROOT": self.home / ".claude-revoke-quarantine",
        }
        for name, value in paths.items():
            self._saved[name] = getattr(cr, name)
            setattr(cr, name, value)
        (self.home / ".claude" / "projects").mkdir(parents=True)

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(cr, name, value)
        self._tmp.cleanup()

    def write(self, rel, data, perms=0o644):
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data))
        os.chmod(p, perms)
        return p

    def settings_keys(self):
        return [Path(i.key).relative_to(self.home).as_posix()
                for i in cr.scan_settings([self.home])]


class TestFilePermissions(FakeHome):
    def test_rewrite_keeps_private_file_private(self):
        p = self.write(".claude.json", {"projects": {}}, perms=0o600)
        cr.write_json_atomic(p, {"projects": {}})
        self.assertEqual(mode(p), 0o600)

    def test_new_file_is_private(self):
        p = self.home / "new.json"
        cr.write_json_atomic(p, {})
        self.assertEqual(mode(p), 0o600)

    def test_quarantine_folders_are_private(self):
        self.write(".claude/settings.json", {"permissions": {"allow": ["Bash(*)"]}}, perms=0o600)
        plan = [i for i in cr.scan_global({}) if i.kind == "gallow"]
        cr.apply_plan(plan)
        self.assertEqual(mode(cr.QUARANTINE_ROOT), 0o700)
        for qdir in cr.QUARANTINE_ROOT.iterdir():
            self.assertEqual(mode(qdir), 0o700)

    def test_existing_loose_quarantine_root_is_tightened(self):
        cr.QUARANTINE_ROOT.mkdir(mode=0o775)
        os.chmod(cr.QUARANTINE_ROOT, 0o775)
        self.write(".claude/settings.json", {"permissions": {"allow": ["Bash(*)"]}}, perms=0o600)
        cr.apply_plan([i for i in cr.scan_global({}) if i.kind == "gallow"])
        self.assertEqual(mode(cr.QUARANTINE_ROOT), 0o700)


class TestSettingsWalk(FakeHome):
    def test_finds_project_inside_folder_named_dev(self):
        self.write("projects/dev/app/.claude/settings.local.json", {})
        self.assertIn("projects/dev/app/.claude/settings.local.json", self.settings_keys())

    def test_finds_project_inside_folders_named_run_sys_proc(self):
        for name in ("run", "sys", "proc"):
            self.write(f"work/{name}/app/.claude/settings.json", {})
        found = self.settings_keys()
        for name in ("run", "sys", "proc"):
            self.assertIn(f"work/{name}/app/.claude/settings.json", found)

    def test_skips_cache_folders_at_top_of_home(self):
        # ~/.claude exists in setUp, which used to switch the skip list off for ~
        self.write(".cache/pkg/.claude/settings.json", {})
        self.write("node_modules/pkg/.claude/settings.json", {})
        self.assertEqual(self.settings_keys(), [])

    def test_skips_node_modules_deeper_down(self):
        self.write("code/app/node_modules/pkg/.claude/settings.json", {})
        self.write("code/app/.claude/settings.json", {})
        self.assertEqual(self.settings_keys(), ["code/app/.claude/settings.json"])

    def test_ignores_global_claude_dir(self):
        self.write(".claude/settings.json", {})
        self.assertEqual(self.settings_keys(), [])


class TestApplyAndRestore(FakeHome):
    def test_round_trip_restores_everything(self):
        cj = {"projects": {"/gone/app": {"allowedTools": ["Bash(*)"]}},
              "mcpServers": {"srv": {"command": "x"}}}
        gs = {"permissions": {"allow": ["Bash(*)"], "additionalDirectories": ["/gone"]}}
        self.write(".claude.json", cj, perms=0o600)
        self.write(".claude/settings.json", gs, perms=0o600)
        proj = self.write("code/app/.claude/settings.local.json", {"permissions": {"allow": ["Read"]}})
        sess = self.home / ".claude/projects/-gone-app"
        sess.mkdir()
        (sess / "a.jsonl").write_text("{}\n")

        cats = cr.scan_all([self.home], secret_scan=False)
        plan = [i for c in cats for i in c.items if i.kind != "harden"]
        self.assertEqual(sorted({i.kind for i in plan}),
                         ["gallow", "gdir", "gmcp", "session", "settings", "trust"])
        cr.apply_plan(plan)

        self.assertEqual(cr.load_json(cr.CLAUDE_JSON), {"projects": {}, "mcpServers": {}})
        self.assertFalse(proj.exists())
        self.assertFalse(sess.exists())

        qdir = next(cr.QUARANTINE_ROOT.iterdir())
        with contextlib.redirect_stdout(io.StringIO()):
            cr.restore(qdir)

        self.assertEqual(cr.load_json(cr.CLAUDE_JSON), cj)
        self.assertEqual(cr.load_json(cr.GLOBAL_SETTINGS), gs)
        self.assertEqual(mode(cr.CLAUDE_JSON), 0o600)
        self.assertTrue(proj.is_file())
        self.assertTrue((sess / "a.jsonl").is_file())

    def test_harden_adds_deny_rules_and_keeps_existing(self):
        self.write(".claude/settings.json", {"permissions": {"deny": ["Read(x)"]}}, perms=0o600)
        cr.apply_plan([i for i in cr.scan_global({}) if i.kind == "harden"])
        deny = cr.load_json(cr.GLOBAL_SETTINGS)["permissions"]["deny"]
        self.assertEqual(deny, ["Read(x)"] + cr.DENY_RULES)
        self.assertEqual(mode(cr.GLOBAL_SETTINGS), 0o600)


if __name__ == "__main__":
    unittest.main()
