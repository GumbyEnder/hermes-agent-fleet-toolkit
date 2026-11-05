"""agentbus CLI: health, send, read --last, --mark cursors, mentions, reads."""
import json
import os
import subprocess
import sys
import unittest

from support import ROOT, bus, base_env, get_json, post

AB = str(ROOT / "chat-room/agentbus.py")


class AgentbusCli(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._ctx = bus(AGENT_CHAT_RATE_PER_MIN="600", AGENT_CHAT_RATE_BURST="1000")
        cls.base, _tmp, _svc = cls._ctx.__enter__()
        cls.env = base_env(AGENT_CHAT_URL=cls.base, AGENT_NAME="cli-tester")

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, AB, *args], env=self.env,
                              capture_output=True, text=True, timeout=30)

    def test_health(self):
        r = self.run_cli("health")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('"status": "ok"', r.stdout)

    def test_send_then_read_last(self):
        r = self.run_cli("send", "cli message", "--topic", "unit", "--from", "cli-tester")
        self.assertEqual(r.returncode, 0, r.stderr)
        mid = json.loads(r.stdout)["id"]
        r = self.run_cli("read", "--last", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"[{mid}]", r.stdout)
        self.assertIn("cli message", r.stdout)

    def test_read_mark_sets_server_cursor(self):
        self.run_cli("send", "cursor me", "--topic", "unit", "--from", "marker")
        r = self.run_cli("read", "--last", "1", "--mark", "--from", "marker")
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = get_json(self.base + "/api/reads")
        self.assertTrue(any(row["name"] == "marker" and row["last_id"] > 0 for row in rows))

    def test_mentions_and_reads(self):
        post(self.base + "/send", {"from": "x", "topic": "work", "text": "hey @cli-tester"})
        r = self.run_cli("mentions", "--name", "cli-tester")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("@cli-tester", r.stdout)
        self.run_cli("read", "--last", "1", "--mark", "--from", "cli-tester")
        r = self.run_cli("reads")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("cli-tester", r.stdout)
        self.assertIn("last_id=", r.stdout)


if __name__ == "__main__":
    unittest.main()
