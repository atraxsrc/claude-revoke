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
                        PATH=f"{stubs}:{os.environ.get('PATH', '/usr/bin:/bin')}")

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, name):
        return subprocess.run(["bash", str(ROOT / name)], env=self.env,
                              capture_output=True, text=True, timeout=60)

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
        self.run_script("install.sh")
        dropin = self.home / UNITS / "claude-revoke-audit.timer.d"
        dropin.mkdir(parents=True)
        (dropin / "schedule.conf").write_text("[Timer]\nOnCalendar=\nOnCalendar=daily\n")
        r = self.run_script("uninstall.sh")
        self.assertEqual(r.returncode, 0, r.stderr)
        for rel in (".local/bin/claude-revoke",
                    ".local/share/applications/claude-revoke.desktop",
                    ".local/share/icons/hicolor/scalable/apps/claude-revoke.svg",
                    f"{UNITS}/claude-revoke-audit.service",
                    f"{UNITS}/claude-revoke-audit.timer",
                    f"{UNITS}/claude-revoke-audit.timer.d"):
            self.assertFalse((self.home / rel).exists(), rel)
        self.assertIn("--user disable --now claude-revoke-audit.timer", self.calls())


if __name__ == "__main__":
    unittest.main()
