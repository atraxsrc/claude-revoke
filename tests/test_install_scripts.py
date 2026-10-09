"""Tests for install.sh and uninstall.sh, run against a throwaway HOME."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

UNITS = ".config/systemd/user"


class TestInstallScripts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        stubs = self.home / "stubs"
        stubs.mkdir()
        self.log = self.home / "systemctl.log"
        stub = stubs / "systemctl"
        stub.write_text('#!/bin/sh\necho "$*" >> "$SYSTEMCTL_LOG"\n')
        stub.chmod(0o755)
        # stubs come first so systemctl is ours; the rest of PATH supplies python3, install, sed
        self.env = dict(os.environ, HOME=str(self.home), SYSTEMCTL_LOG=str(self.log),
                        PATH=f"{stubs}:{os.environ.get('PATH', '/usr/bin:/bin')}",
                        XDG_CONFIG_HOME=str(self.home / ".config"))

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, name, **env):
        return subprocess.run(["bash", str(ROOT / name)], env=dict(self.env, **env),
                              capture_output=True, text=True, timeout=60)

    def test_units_follow_xdg_config_home(self):
        cfg = self.home / "cfg"
        r = self.run_script("install.sh", XDG_CONFIG_HOME=str(cfg))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((cfg / "systemd/user/claude-revoke-audit.timer").is_file())
        self.assertFalse((self.home / UNITS).exists())
        r = self.run_script("uninstall.sh", XDG_CONFIG_HOME=str(cfg))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((cfg / "systemd/user/claude-revoke-audit.timer").exists())

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_install_puts_everything_under_home(self):
        r = self.run_script("install.sh")
        self.assertEqual(r.returncode, 0, r.stderr)
        binary = self.home / ".local/bin/claude-revoke"
        self.assertTrue(os.access(binary, os.X_OK))
        desktop = (self.home / ".local/share/applications/claude-revoke.desktop").read_text()
        self.assertIn(f"{binary} --pause", desktop)
        self.assertTrue((self.home / ".local/share/icons/hicolor/scalable/apps/claude-revoke.svg").is_file())
        service = (self.home / UNITS / "claude-revoke-audit.service").read_text()
        self.assertIn(f"ExecStart={binary} --notify\n", service)
        self.assertNotIn("@BIN@", service)
        self.assertEqual((self.home / UNITS / "claude-revoke-audit.timer").read_bytes(),
                         (ROOT / "packaging/claude-revoke-audit.timer").read_bytes())
        self.assertIn("--user daemon-reload", self.calls())
        self.assertIn("--schedule weekly", r.stdout)

    def test_uninstall_removes_everything_and_stops_the_timer(self):
        r = self.run_script("install.sh")
        self.assertEqual(r.returncode, 0, r.stderr)
        units = self.home / UNITS
        dropin = units / "claude-revoke-audit.timer.d"
        dropin.mkdir(parents=True)
        (dropin / "schedule.conf").write_text("[Timer]\nOnCalendar=\nOnCalendar=daily\n")
        # what `systemctl --user enable` leaves behind
        wants = units / "timers.target.wants"
        wants.mkdir()
        (wants / "claude-revoke-audit.timer").symlink_to(units / "claude-revoke-audit.timer")
        r = self.run_script("uninstall.sh")
        self.assertEqual(r.returncode, 0, r.stderr)
        for rel in (".local/bin/claude-revoke",
                    ".local/share/applications/claude-revoke.desktop",
                    ".local/share/icons/hicolor/scalable/apps/claude-revoke.svg",
                    f"{UNITS}/claude-revoke-audit.service",
                    f"{UNITS}/claude-revoke-audit.timer",
                    f"{UNITS}/claude-revoke-audit.timer.d",
                    f"{UNITS}/timers.target.wants/claude-revoke-audit.timer"):
            self.assertFalse((self.home / rel).is_symlink() or (self.home / rel).exists(), rel)
        self.assertIn("--user disable --now claude-revoke-audit.timer", self.calls())


if __name__ == "__main__":
    unittest.main()
