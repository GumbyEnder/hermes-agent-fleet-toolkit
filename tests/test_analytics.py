"""Analytics endpoint: room buckets, usage merge, missing-file safety, day bounds."""
import json
import os
import tempfile
import time
import unittest

from support import bus, base_env, get, get_json, post


def write_usage(path):
    now = time.time()
    hist = [
        {"ts": now - 3 * 86400, "sessions": 10, "input": 1000, "output": 100,
         "cache_read": 0, "reasoning": 0, "billable_cost": 1.0, "included_cost": 0.0},
        {"ts": now - 3 * 86400 + 3600, "sessions": 12, "input": 5000, "output": 400,
         "cache_read": 0, "reasoning": 0, "billable_cost": 3.5, "included_cost": 0.0},
        {"ts": now - 60, "sessions": 20, "input": 9000, "output": 700,
         "cache_read": 0, "reasoning": 0, "billable_cost": 7.0, "included_cost": 0.0},
    ]
    win = {"total": {"sessions": 3, "input": 9000, "output": 700, "cache_read": 0,
                     "reasoning": 0, "est_cost": 7.0, "billable_cost": 7.0,
                     "included_cost": 0.0},
           "by_profile": {"agent-a": {"sessions": 2, "input": 5000, "output": 400,
                                      "cache_read": 0, "est_cost": 3.5,
                                      "last_active": now - 120},
                          "agent-b": {"sessions": 1, "input": 10, "output": 5,
                                      "cache_read": 0, "est_cost": 0.25,
                                      "last_active": now - 4000}}}
    data = {"generated": now, "profiles": ["agent-a", "agent-b"],
            "windows": {"7d": win, "30d": win, "all": win},
            "history": hist,
            "top_sessions": [{"session_id": "s1", "profile": "agent-a", "model": "m",
                              "input": 1, "output": 2, "est_cost": 4.2, "last_seen": now}],
            "anomaly": {"active": False}, "budgets": [], "budget_limit_usd": 0}
    with open(path, "w") as fh:
        json.dump(data, fh)
    return data


class Analytics(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fleet-analytics-")
        self.usage = os.path.join(self.tmp, "usage.json")

    def start(self, usage_path=None):
        over = {"AGENT_CHAT_RATE_PER_MIN": "600", "AGENT_CHAT_RATE_BURST": "1000"}
        if usage_path:
            over["USAGE_JSON"] = usage_path
        else:
            over["USAGE_JSON"] = os.path.join(self.tmp, "absent.json")
        ctx = bus(**over)
        base, _tmp, _svc = ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        return base

    def test_room_buckets_and_threads(self):
        base = self.start()
        st, body = post(base + "/send", {"from": "alpha", "topic": "unit", "text": "one"})
        root = json.loads(body)["id"]
        post(base + "/send", {"from": "alpha", "topic": "unit", "text": "two"})
        post(base + "/send", {"from": "beta", "topic": "other", "text": "reply",
                              "reply_to": root})
        d = get_json(base + "/api/analytics?days=7")
        room = d["room"]
        self.assertEqual(room["days"], 7)
        today = time.strftime("%Y-%m-%d")
        bucket = [r for r in room["per_day"] if r["date"] == today][0]
        self.assertGreaterEqual(bucket["messages"], 3)
        senders = {r["sender"]: r["messages"] for r in room["by_sender"]}
        self.assertEqual(senders["alpha"], 2)
        self.assertEqual(senders["beta"], 1)
        self.assertGreaterEqual(room["threads"], 1)
        topics = {r["topic"] for r in room["by_topic"]}
        self.assertIn("unit", topics)
        self.assertFalse(d["usage"]["available"], "absent usage.json must not 500")
        self.assertTrue(d["agents"], "room senders still appear as agents")

    def test_usage_merge_and_daily_deltas(self):
        write_usage(self.usage)
        base = self.start(self.usage)
        post(base + "/send", {"from": "agent-a", "topic": "unit", "text": "hello"})
        d = get_json(base + "/api/analytics?days=30")
        u = d["usage"]
        self.assertTrue(u["available"])
        self.assertIn("7d", u["windows"])
        self.assertAlmostEqual(sum(x["cost"] for x in u["daily"]), 6.0, places=2)
        self.assertTrue(all(x["date"] for x in u["daily"]))
        row = [a for a in d["agents"] if a["sender"] == "agent-a"][0]
        self.assertEqual(row["messages"], 1)
        self.assertEqual(row["tokens_7d"], 5400)
        self.assertAlmostEqual(row["cost_7d"], 3.5, places=2)
        # a profile with usage but no room messages still shows up
        self.assertIn("agent-b", [a["sender"] for a in d["agents"]])
        self.assertEqual([a for a in d["agents"] if a["sender"] == "agent-b"][0]["messages"], 0)

    def test_days_are_bounded(self):
        base = self.start()
        self.assertEqual(get_json(base + "/api/analytics?days=5000")["room"]["days"], 365)
        self.assertEqual(get_json(base + "/api/analytics?days=0")["room"]["days"], 1)
        self.assertEqual(get_json(base + "/api/analytics")["room"]["days"], 30)

    def test_snapshot_file_is_read_only_and_cached(self):
        write_usage(self.usage)
        base = self.start(self.usage)
        before = os.stat(self.usage).st_mtime
        get_json(base + "/api/analytics?days=7")
        get_json(base + "/api/analytics?days=7")
        self.assertEqual(os.stat(self.usage).st_mtime, before)


if __name__ == "__main__":
    unittest.main()
