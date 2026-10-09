"""Tests for claude_revoke, run against a throwaway home folder.

    python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "claude_revoke.py"
DESKTOP = ROOT / "packaging" / "claude-revoke.desktop.in"

sys.path.insert(0, str(ROOT))
import claude_revoke as cr  # noqa: E402

# Fake credentials for the secret scan tests, built from two halves so that
# secret scanners (including this repo's own gitleaks pre-commit hook) do not
# flag the test file itself.
GH = "ghp_" + "Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4zAb7d"     # GitHub-token-shaped, 36 random-looking chars
PW = "p4ssW0rd" + "-9Xk2qLm7Zt"                        # a real-looking password


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

    def latest_quarantine(self):
        return next(cr.QUARANTINE_ROOT.iterdir())

    def restore_latest(self):
        with contextlib.redirect_stdout(io.StringIO()):
            cr.restore(self.latest_quarantine())

    def transcripts(self, *lines, project="-x-app"):
        d = self.home / ".claude" / "projects" / project
        d.mkdir(parents=True, exist_ok=True)
        (d / "s.jsonl").write_text("\n".join(lines) + "\n")
        return d


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
        # every folder the tool creates: root, the run folder and backup/sessions/settings inside it
        self.write(".claude.json", {"projects": {"/gone/app": {}}}, perms=0o600)
        self.write("code/app/.claude/settings.local.json", {})
        sess = self.home / ".claude/projects/-gone-app"
        sess.mkdir()
        (sess / "a.jsonl").write_text("{}\n")
        cats = cr.scan_all([self.home], secret_scan=False)
        cr.apply_plan([i for c in cats for i in c.items if i.kind in ("trust", "session", "settings")])
        qdir = self.latest_quarantine()
        for d in (cr.QUARANTINE_ROOT, qdir, qdir / "backup", qdir / "sessions", qdir / "settings"):
            self.assertEqual(mode(d), 0o700, d)

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

        self.restore_latest()

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


class TestRestoreKeepsNewerChanges(FakeHome):
    """--restore puts back only what the run removed, so anything Claude Code
    wrote to the file after the run survives."""

    def test_restore_keeps_projects_trusted_after_the_run(self):
        cj = {"projects": {"/gone/a": {"allowedTools": ["Bash(*)"]}, "/kept/b": {}}}
        self.write(".claude.json", cj, perms=0o600)
        plan = [i for i in cr.scan_trust(cj["projects"], set()) if i.key == "/gone/a"]
        cr.apply_plan(plan)
        # Claude Code runs again: trusts a new folder and bumps a counter
        data = cr.load_json(cr.CLAUDE_JSON)
        data["projects"]["/new/c"] = {"hasTrustDialogAccepted": True}
        data["numStartups"] = 42
        cr.write_json_atomic(cr.CLAUDE_JSON, data)

        self.restore_latest()
        data = cr.load_json(cr.CLAUDE_JSON)
        self.assertEqual(set(data["projects"]), {"/gone/a", "/kept/b", "/new/c"})
        self.assertEqual(data["projects"]["/gone/a"], {"allowedTools": ["Bash(*)"]})
        self.assertEqual(data["numStartups"], 42)

    def test_restore_undoes_global_edits_but_keeps_newer_rules(self):
        gs = {"permissions": {"allow": ["Bash(*)", "Read"], "additionalDirectories": ["/gone"],
                              "defaultMode": "bypassPermissions"},
              "hooks": {"Stop": []}}
        self.write(".claude/settings.json", gs, perms=0o600)
        plan = [i for i in cr.scan_global({}) if i.key != "Read"]
        self.assertEqual(sorted(i.kind for i in plan), ["gallow", "gdir", "ghooks", "gmode", "harden"])
        cr.apply_plan(plan)
        data = cr.load_json(cr.GLOBAL_SETTINGS)
        self.assertEqual(data["permissions"]["allow"], ["Read"])
        self.assertNotIn("hooks", data)
        # the user adds an allow rule and a deny rule of their own after the run
        data["permissions"]["allow"].append("Edit")
        data["permissions"]["deny"].append("Read(/secret)")
        cr.write_json_atomic(cr.GLOBAL_SETTINGS, data)

        self.restore_latest()
        data = cr.load_json(cr.GLOBAL_SETTINGS)
        p = data["permissions"]
        self.assertEqual(sorted(p["allow"]), ["Bash(*)", "Edit", "Read"])
        self.assertEqual(p["additionalDirectories"], ["/gone"])
        self.assertEqual(p["defaultMode"], "bypassPermissions")
        self.assertEqual(p["deny"], ["Read(/secret)"])
        self.assertEqual(data["hooks"], {"Stop": []})

    def test_restore_falls_back_to_backup_when_file_is_gone(self):
        cj = {"projects": {"/gone/a": {}}}
        self.write(".claude.json", cj, perms=0o600)
        cr.apply_plan(cr.scan_trust(cj["projects"], set()))
        cr.CLAUDE_JSON.unlink()
        self.restore_latest()
        self.assertEqual(cr.load_json(cr.CLAUDE_JSON), cj)

    def test_restore_handles_manifest_from_0_1_0(self):
        # 0.1.0 manifests only have "moves" and "backups": the backup is copied back whole
        cj = {"projects": {"/gone/a": {}}}
        self.write(".claude.json", cj, perms=0o600)
        cr.apply_plan(cr.scan_trust(cj["projects"], set()))
        qdir = self.latest_quarantine()
        m = cr.load_json(qdir / "manifest.json")
        m.pop("edits", None)
        cr.write_json_atomic(qdir / "manifest.json", m)
        self.restore_latest()
        self.assertEqual(cr.load_json(cr.CLAUDE_JSON), cj)


class TestSecretScan(FakeHome):
    def test_ignores_identifiers_and_placeholders(self):
        d = self.transcripts(
            '{"t":"token = generateTokenForUser(user)"}',
            '{"t":"\\"password\\": \\"your_password_here\\""}',
            '{"t":"api_key=process_env_API_KEY_VALUE"}',
            '{"t":"secret: xxxxxxxxxxxxxxxxxxxx"}',
            '{"t":"AKIAIOSFODNN7EXAMPLE"}',
        )
        self.assertEqual(cr.scan_secrets(d), [])

    def test_reports_kind_file_line_and_redacted_value(self):
        d = self.transcripts('{"t":"hello"}', '{"t":"nothing"}', '{"t":"export GH=' + GH + '"}')
        hits = cr.scan_secrets(d)
        self.assertEqual(len(hits), 1)
        h = hits[0]
        self.assertEqual((h.kind, h.file, h.line), ("GitHub token", "s.jsonl", 3))
        self.assertNotIn(GH, h.redacted)
        self.assertTrue(h.redacted.startswith("ghp_"), h.redacted)

    def test_counts_each_distinct_secret_once(self):
        d = self.transcripts('{"t":"' + GH + '"}', '{"t":"again ' + GH + '"}')
        self.assertEqual(len(cr.scan_secrets(d)), 1)

    def test_still_finds_real_looking_assignment(self):
        d = self.transcripts('{"t":"DB_PASSWORD=' + PW + '"}')
        hits = cr.scan_secrets(d)
        self.assertEqual([h.kind for h in hits], ["secret assignment"])
        self.assertNotIn(PW, hits[0].redacted)

    def test_session_item_shows_where_the_secret_is(self):
        self.transcripts('{"t":"hello"}', '{"t":"' + GH + '"}')
        items = cr.scan_sessions({}, secret_scan=True)
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertTrue(it.risky)
        self.assertIn("SECRETS:1", it.badge)
        self.assertTrue(any("s.jsonl:2" in line for line in it.detail), it.detail)
        self.assertFalse(any(GH in line for line in it.detail))


class TestOddJson(FakeHome):
    """Unexpected shapes in the JSON files must never crash the scan or apply."""

    def test_claude_json_that_is_a_list(self):
        self.write(".claude.json", [1, 2])
        self.assertEqual(len(cr.scan_all([self.home], secret_scan=False)), 4)

    def test_projects_that_is_a_list(self):
        self.write(".claude.json", {"projects": [1]})
        cats = cr.scan_all([self.home], secret_scan=False)
        self.assertEqual(cats[0].items, [])

    def test_odd_project_entries(self):
        self.write(".claude.json", {
            "projects": {"/a": {"allowedTools": "Bash", "mcpServers": [1]}, "/b": None, "/c": 7},
            "mcpServers": [1]})
        cats = cr.scan_all([self.home], secret_scan=False)
        self.assertEqual(sorted(i.key for i in cats[0].items), ["/a", "/b", "/c"])

    def test_odd_global_settings(self):
        self.write(".claude/settings.json", {
            "permissions": {"allow": ["Bash(*)", 5, None], "additionalDirectories": "x",
                            "deny": None, "defaultMode": 3},
            "hooks": "nope"})
        items = cr.scan_global({"mcpServers": {"ok": {"args": "x"}, "bad": [1]}})
        self.assertEqual([i.key for i in items if i.kind == "gallow"], ["Bash(*)"])
        self.assertIn("ghooks", [i.kind for i in items])
        self.assertEqual(sorted(i.key for i in items if i.kind == "gmcp"), ["bad", "ok"])

    def test_global_settings_that_is_a_list(self):
        self.write(".claude/settings.json", [1])
        self.assertEqual([i.kind for i in cr.scan_global({})], ["harden"])

    def test_analyze_settings_odd_shapes(self):
        risks, n = cr.analyze_settings({"permissions": [1], "mcpServers": [1], "hooks": [1]})
        self.assertEqual(n, 0)
        self.assertTrue(risks)

    def test_apply_survives_odd_permissions(self):
        self.write(".claude/settings.json", {"permissions": [1]}, perms=0o600)
        cr.apply_plan([i for i in cr.scan_global({}) if i.kind == "harden"])
        self.assertEqual(cr.load_json(cr.GLOBAL_SETTINGS)["permissions"]["deny"], cr.DENY_RULES)

    def test_apply_survives_odd_claude_json(self):
        self.write(".claude.json", {"projects": [1], "mcpServers": "x"}, perms=0o600)
        log = cr.apply_plan([cr.Item("trust", "/a", "/a")])
        self.assertTrue(any(line.startswith("SKIP") for line in log), log)


class TestPauseFlag(FakeHome):
    """--pause keeps a launcher-opened terminal window open until Enter is pressed."""

    def run_cli(self, *args):
        env = dict(os.environ, CLAUDE_REVOKE_HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run([sys.executable, str(SCRIPT), *args], input="\n", env=env,
                              capture_output=True, text=True, timeout=60)

    def test_pause_waits_for_enter_after_report(self):
        r = self.run_cli("--report", "--no-secret-scan", "--pause")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Press Enter", r.stdout)

    def test_no_pause_by_default(self):
        r = self.run_cli("--report", "--no-secret-scan")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Press Enter", r.stdout + r.stderr)

    def test_pause_also_waits_after_an_error(self):
        r = self.run_cli("--restore", str(self.home / "nope"), "--pause")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("No manifest.json", r.stderr)
        self.assertIn("Press Enter", r.stdout)

    def test_launcher_entry_uses_pause(self):
        exec_line = [l for l in DESKTOP.read_text().splitlines() if l.startswith("Exec=")][0]
        self.assertIn("--pause", exec_line)


class TestVersion(unittest.TestCase):
    def test_version_flag(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "--version"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), f"claude-revoke {cr.__version__}")

    def test_changelog_top_entry_matches_version(self):
        heads = [l for l in (ROOT / "CHANGELOG.md").read_text().splitlines() if l.startswith("## ")]
        released = f"## {cr.__version__} - "
        self.assertTrue(heads[0] == "## Unreleased" or heads[0].startswith(released), heads[0])
        self.assertTrue(any(h.startswith(released) for h in heads), heads)
        self.assertEqual(heads.count("## Unreleased"), 1 if heads[0] == "## Unreleased" else 0)


class TestSummarize(unittest.TestCase):
    def item(self, kind="trust", **kw):
        return cr.Item(kind, "/x", "/x", **kw)

    def test_nothing_to_report(self):
        cats = [cr.Category("Trusted projects", "", [self.item()]),
                cr.Category("Global settings", "", [])]
        self.assertIsNone(cr.summarize(cats))

    def test_counts_stale_and_risky_per_category(self):
        cats = [cr.Category("Trusted projects", "",
                            [self.item(stale=True), self.item(stale=True), self.item(risky=True)]),
                cr.Category("Session transcripts", "",
                            [self.item("session", risky=True, badge="1.2K  2026-10-10  SECRETS:3")]),
                cr.Category("Project settings files", "", [self.item("settings")])]
        self.assertEqual(cr.summarize(cats),
                         "Trusted projects: 2 stale, 1 risky; Session transcripts: 1 risky (1 with secrets)")


class TestNotifyFlag(FakeHome):
    """--notify is what the systemd timer runs: scan, one summary line, desktop notification."""

    def run_notify(self, with_notify_send=True):
        bindir = self.home / "bin"
        bindir.mkdir(exist_ok=True)
        log = self.home / "notify.log"
        if with_notify_send:
            stub = bindir / "notify-send"
            stub.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" >> "$NOTIFY_LOG"\n')
            stub.chmod(0o755)
        env = dict(os.environ, CLAUDE_REVOKE_HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1",
                   PATH=str(bindir), NOTIFY_LOG=str(log))
        r = subprocess.run([sys.executable, str(SCRIPT), "--notify", "--no-secret-scan"], env=env,
                           capture_output=True, text=True, timeout=60)
        return r, (log.read_text() if log.exists() else "")

    def test_notifies_when_something_is_stale(self):
        self.write(".claude.json", {"projects": {"/gone/app": {}}})
        r, sent = self.run_notify()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Trusted projects: 1 stale", r.stdout)
        self.assertIn("--app-name=claude-revoke", sent)
        self.assertIn("Trusted projects: 1 stale", sent)
        self.assertIn("Run claude-revoke to review", sent)

    def test_quiet_when_nothing_found(self):
        r, sent = self.run_notify()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("nothing stale or risky", r.stdout)
        self.assertEqual(sent, "")

    def test_still_exits_zero_without_notify_send(self):
        self.write(".claude.json", {"projects": {"/gone/app": {}}})
        r, sent = self.run_notify(with_notify_send=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("libnotify", r.stderr)
        self.assertEqual(sent, "")


class TestScheduleFlag(FakeHome):
    """--schedule weekly|daily|off drives the systemd user timer through systemctl."""

    def run_schedule(self, choice, with_systemctl=True):
        bindir = self.home / "bin"
        bindir.mkdir(exist_ok=True)
        log = self.home / "systemctl.log"
        if with_systemctl:
            stub = bindir / "systemctl"
            stub.write_text('#!/bin/sh\necho "$*" >> "$SYSTEMCTL_LOG"\n')
            stub.chmod(0o755)
        env = dict(os.environ, CLAUDE_REVOKE_HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1",
                   PATH=str(bindir), SYSTEMCTL_LOG=str(log))
        r = subprocess.run([sys.executable, str(SCRIPT), "--schedule", choice], env=env,
                           capture_output=True, text=True, timeout=60)
        calls = log.read_text().splitlines() if log.exists() else []
        return r, calls

    def dropin(self):
        return self.home / ".config/systemd/user/claude-revoke-audit.timer.d/schedule.conf"

    def test_daily_writes_dropin_and_enables_timer(self):
        r, calls = self.run_schedule("daily")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.dropin().read_text(), "[Timer]\nOnCalendar=\nOnCalendar=daily\n")
        self.assertEqual(calls, ["--user daemon-reload", "--user enable --now claude-revoke-audit.timer"])
        self.assertIn("daily", r.stdout)

    def test_weekly_overrides_an_earlier_choice(self):
        self.run_schedule("daily")
        r, _ = self.run_schedule("weekly")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OnCalendar=weekly", self.dropin().read_text())

    def test_off_disables_timer(self):
        r, calls = self.run_schedule("off")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(calls, ["--user disable --now claude-revoke-audit.timer"])
        self.assertIn("off", r.stdout)

    def test_rejects_other_values(self):
        r, calls = self.run_schedule("hourly")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(calls, [])

    def test_fails_clearly_without_systemctl(self):
        r, calls = self.run_schedule("weekly", with_systemctl=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("systemctl", r.stderr)
        self.assertFalse(self.dropin().exists())


if __name__ == "__main__":
    unittest.main()
