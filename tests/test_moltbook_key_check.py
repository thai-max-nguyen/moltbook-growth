#!/usr/bin/env python3
"""Tests for moltbook_key_check.py — probe + rotation logic.

Run: python3 ~/.config/mundo-bot/tests/test_moltbook_key_check.py

No pytest dependency — uses raw unittest so it runs in cron with stock /usr/bin/python3.
"""
import json
import os
import sys
import tempfile
import unittest
import unittest.mock as mock
import urllib.error
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/.config/mundo-bot"))
import moltbook_key_check as mkc


class ProbeReturnsThreeStates(unittest.TestCase):
    """The probe must distinguish True / False / None — confused False with
    network errors caused the 2026-05-29 false-CRIT cascade."""

    def test_200_with_success_returns_true(self):
        body = json.dumps({"success": True, "agent": {"name": "mundo"}}).encode()
        fake = mock.MagicMock(); fake.status = 200; fake.read.return_value = body
        fake.__enter__ = mock.Mock(return_value=fake); fake.__exit__ = mock.Mock(return_value=False)
        with mock.patch("urllib.request.urlopen", return_value=fake):
            self.assertIs(mkc._probe("any"), True)

    def test_200_without_success_field_returns_false(self):
        body = json.dumps({"agent": {"name": "mundo"}}).encode()
        fake = mock.MagicMock(); fake.status = 200; fake.read.return_value = body
        fake.__enter__ = mock.Mock(return_value=fake); fake.__exit__ = mock.Mock(return_value=False)
        with mock.patch("urllib.request.urlopen", return_value=fake):
            self.assertIs(mkc._probe("any"), False)

    def test_explicit_401_returns_false(self):
        err = urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            self.assertIs(mkc._probe("any"), False)

    def test_500_returns_none_not_false(self):
        """5xx is server-side — we don't know about the key. Don't rotate."""
        err = urllib.error.HTTPError("url", 500, "Server Error", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            self.assertIsNone(mkc._probe("any"))

    def test_dns_failure_returns_none_not_false(self):
        """The bug we fixed: gaierror was being treated as 401."""
        import socket
        err = urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided"))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            self.assertIsNone(mkc._probe("any"))

    def test_timeout_returns_none(self):
        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError("slow")):
            self.assertIsNone(mkc._probe("any"))


class MainBehavior(unittest.TestCase):
    """End-to-end logic in main()."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        self.creds = self.tmpdir / "credentials.json"
        self.env = self.tmpdir / ".env"
        self.alert = self.tmpdir / "key_alert.CRIT"
        self.log = self.tmpdir / "log"
        self._patches = [
            mock.patch.object(mkc, "CREDS_JSON", self.creds),
            mock.patch.object(mkc, "ENV_FILE", self.env),
            mock.patch.object(mkc, "ALERT", self.alert),
            mock.patch.object(mkc, "LOG", self.log),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()

    def _write_creds(self, key):
        self.creds.write_text(json.dumps({"api_key": key, "agent_name": "mundo"}))

    def _write_env(self, key):
        self.env.write_text(f"MOLTBOOK_API_KEY={key}\n")

    def test_valid_creds_exits_0_silently(self):
        self._write_creds("good_key")
        with mock.patch.object(mkc, "_probe", side_effect=lambda k: True if k == "good_key" else False):
            self.assertEqual(mkc.main(), 0)
        self.assertFalse(self.alert.exists())

    def test_network_blip_does_not_raise_crit(self):
        """The regression test for 2026-05-29 false-CRIT cascade."""
        self._write_creds("any_key")
        with mock.patch.object(mkc, "_probe", return_value=None):
            self.assertEqual(mkc.main(), 0)
        self.assertFalse(self.alert.exists())

    def test_stale_creds_with_working_env_rotates(self):
        self._write_creds("stale")
        self._write_env("fresh")
        probe_results = {"stale": False, "fresh": True}
        with mock.patch.object(mkc, "_probe", side_effect=lambda k: probe_results.get(k, False)):
            self.assertEqual(mkc.main(), 0)
        synced = json.loads(self.creds.read_text())
        self.assertEqual(synced["api_key"], "fresh")
        self.assertFalse(self.alert.exists())

    def test_both_keys_401_raises_crit(self):
        self._write_creds("dead1")
        self._write_env("dead2")
        with mock.patch.object(mkc, "_probe", return_value=False):
            self.assertEqual(mkc.main(), 1)
        self.assertTrue(self.alert.exists())
        self.assertIn("401", self.alert.read_text())

    def test_creds_401_but_env_network_blip_no_crit(self):
        """Mixed scenario: real failure on creds, unknown on env. Don't CRIT prematurely."""
        self._write_creds("dead")
        self._write_env("unknown")
        probe_results = {"dead": False, "unknown": None}
        with mock.patch.object(mkc, "_probe", side_effect=lambda k: probe_results.get(k, False)):
            self.assertEqual(mkc.main(), 0)
        self.assertFalse(self.alert.exists())

    def test_rotation_clears_existing_alert(self):
        self._write_creds("stale")
        self._write_env("fresh")
        self.alert.write_text("old alert")
        probe_results = {"stale": False, "fresh": True}
        with mock.patch.object(mkc, "_probe", side_effect=lambda k: probe_results.get(k, False)):
            mkc.main()
        self.assertFalse(self.alert.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
