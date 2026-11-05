"""Hermes plugin: payload shapes, cursor handling, threading, wait_for_reply."""
import importlib.util
import json
import os
import pathlib
import unittest
import urllib.error

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_plugin():
    os.environ.update({"HERMES_PROFILE": "plug", "AGENT_CHAT_SECRET": "s",
                       "AGENT_CHAT_URL": "http://bus.invalid"})
    spec = importlib.util.spec_from_file_location(
        "plug", str(ROOT / "hermes-plugin-agent-chat/__init__.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.p = load_plugin()
        self.sent = []
        self.cursors = {}
        self.gets = []

        def fake_signed(path, payload):
            self.sent.append((path, payload))
            return b'{"ok": true, "id": 77}'

        def fake_get(path, timeout=20):
            self.gets.append(path)
            if path == "/api/reads":
                return [{"name": "plug", "last_id": self.cursors.get("plug", 0), "updated": 1}]
            if "/api/mentions" in path:
                return [{"id": 9, "ts": 1, "sender": "a", "topic": "work",
                         "text": "hey @plug", "kind": "message", "parent_id": None}]
            return [{"id": 5, "ts": 1, "sender": "a", "topic": "work",
                     "text": "hello", "kind": "message", "parent_id": 4}]

        self.p._signed = fake_signed
        self.p._get_json = fake_get

    def test_send_posts_with_reply_to(self):
        out = self.p.send_to_room({"text": "answer", "topic": "work", "reply_to": 4})
        self.assertIn("id 77", out)
        path, payload = self.sent[-1]
        self.assertEqual(path, "/send")
        self.assertEqual(payload["reply_to"], 4)
        self.assertEqual(payload["from"], "plug")

    def test_send_rejects_bad_reply_to(self):
        out = self.p.send_to_room({"text": "x", "reply_to": "not-an-id"})
        self.assertIn("error", out)
        self.assertFalse(self.sent)

    def test_send_reports_rate_limit(self):
        def boom(path, payload):
            raise urllib.error.HTTPError("u", 429, "too many", None, None)
        self.p._signed = boom
        self.assertIn("rate limit", self.p.send_to_room({"text": "x"}))

    def test_read_mentions_only_hits_mentions_endpoint(self):
        out = self.p.read_room({"mentions_only": True})
        self.assertIn("/api/mentions", self.gets[-1])
        self.assertIn("@plug", out)

    def test_read_since_cursor_reads_and_advances(self):
        self.cursors["plug"] = 4
        out = self.p.read_room({"since_cursor": True})
        self.assertIn("since=4", self.gets[-1])
        self.assertIn("hello", out)
        self.assertEqual(self.sent[-1][0], "/api/reads")
        self.assertEqual(self.sent[-1][1]["last_id"], 5)

    def test_wait_for_reply_times_out_cleanly(self):
        self.p._get_json = lambda path, timeout=20: []
        out = self.p.wait_for_reply({"parent_id": 4, "timeout_s": 5})
        self.assertIn("no reply", out)


if __name__ == "__main__":
    unittest.main()
