"""bus_watch: mention/directive selection, cursor semantics, repo baseline survival."""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

from support import ROOT, bus, base_env, get_json, post


def load_module(env):
    saved = dict(os.environ)
    os.environ.update(env)
    try:
        spec = importlib.util.spec_from_file_location(
            "bus_watch_under_test", str(ROOT / "chat-room/bus_watch.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        os.environ.clear()
        os.environ.update(saved)


class WatchSelection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fleet-watch-")
        self.mod = load_module({"WATCH_PROFILE": "watcher", "WATCH_DIRECTIVES": "human,boss",
                                "WATCH_IGNORE_TOPICS": "attention",
                                "WATCH_STATE_DIR": self.tmp, "WATCH_REPOS": ""})

    def test_direct_mention(self):
        self.assertTrue(self.mod.wants_me({"sender": "a", "topic": "work",
                                           "text": "hey @watcher look"}))
        self.assertFalse(self.mod.wants_me({"sender": "a", "topic": "work", "text": "no ping"}))

    def test_own_posts_never_wake(self):
        self.assertFalse(self.mod.wants_me({"sender": "watcher", "topic": "work",
                                            "text": "@watcher note to self"}))

    def test_mention_is_whole_token(self):
        # "@watcher" must not fire inside "@watcher2" / "@watcher_bot" (@buzzy vs @buzzygrok).
        self.assertFalse(self.mod.wants_me({"sender": "a", "topic": "work",
                                            "text": "@watcher2 take this one"}))
        self.assertFalse(self.mod.wants_me({"sender": "a", "topic": "work",
                                            "text": "@watcher_bot take this one"}))
        self.assertTrue(self.mod.wants_me({"sender": "a", "topic": "work",
                                           "text": "@watcher2 and @watcher: this one is yours"}))
        self.assertTrue(self.mod.wants_me({"sender": "a", "topic": "work",
                                           "text": "ping @watcher."}))

    def test_ignored_topic(self):
        self.assertFalse(self.mod.wants_me({"sender": "a", "topic": "attention",
                                            "text": "@watcher telemetry mention"}))

    def test_directive_sender_in_work_lane(self):
        self.assertTrue(self.mod.wants_me({"sender": "human", "topic": "infra",
                                           "text": "please check the deploy"}))
        self.assertFalse(self.mod.wants_me({"sender": "human", "topic": "random",
                                            "text": "unrelated chatter"}))


class WatchCursors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._ctx = bus(AGENT_CHAT_RATE_PER_MIN="600", AGENT_CHAT_RATE_BURST="1000")
        cls.base, _tmp, _svc = cls._ctx.__enter__()
        cls.tmp = tempfile.mkdtemp(prefix="fleet-watch2-")
        cls.env = base_env(AGENT_CHAT_URL=cls.base, WATCH_PROFILE="watcher",
                           WATCH_STATE_DIR=cls.tmp, WATCH_IGNORE_TOPICS="attention",
                           WATCH_PUBLISH_CURSOR="1", AGENT_CHAT_RATE_PER_MIN="600")
        cls.state_path = pathlib.Path(cls.tmp) / "watcher_watch_state.json"

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)

    def run_watch(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "chat-room/bus_watch.py"), *args],
                              env=self.env, capture_output=True, text=True, timeout=60)

    def test_prime_then_dry_run_does_not_move_cursor(self):
        r = self.run_watch("--prime")
        self.assertEqual(r.returncode, 0, r.stderr)
        primed = json.loads(self.state_path.read_text())["last_id"]
        post(self.base + "/send", {"from": "x", "topic": "work", "text": "@watcher ping"})
        r = self.run_watch("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("DRY-RUN", r.stdout)
        self.assertEqual(json.loads(self.state_path.read_text())["last_id"], primed)

    def test_tick_advances_and_publishes_cursor(self):
        self.run_watch("--prime")
        post(self.base + "/send", {"from": "x", "topic": "work", "text": "@watcher hello"})
        r = self.run_watch("--dry-run")
        self.assertIn("addressed_to_me=1", r.stdout)
        # a real tick would wake an agent; publish happens through save_state on any tick
        r = self.run_watch()
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = get_json(self.base + "/api/reads")
        self.assertTrue(any(row["name"] == "watcher" for row in rows),
                        "cursor was not published to the bus")


if __name__ == "__main__":
    unittest.main()
