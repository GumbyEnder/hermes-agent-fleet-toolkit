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

    def test_speaker_split_hours_and_reply_graph(self):
        """The analytics page is built on these fields; they must be measured, not inferred."""
        base = self.start()
        st, body = post(base + "/send", {"from": "alpha", "topic": "unit", "text": "root"})
        root = json.loads(body)["id"]
        post(base + "/send", {"from": "beta", "topic": "unit", "text": "answering",
                              "reply_to": root})
        post(base + "/send", {"from": "beta", "topic": "unit", "text": "again"})
        room = get_json(base + "/api/analytics?days=3")["room"]

        self.assertEqual(len(room["dates"]), 3, "window is zero-filled, one row per day")
        self.assertEqual([r["date"] for r in room["per_day"]], room["dates"])
        self.assertEqual(sum(r["messages"] for r in room["per_day"]),
                         room["window_messages"])

        per_day = {r["date"]: r["messages"] for r in room["per_day"]}
        series = {s["sender"]: s["counts"] for s in room["per_day_senders"]}
        self.assertEqual(series["alpha"][-1], 1)
        self.assertEqual(series["beta"][-1], 2)
        for counts in series.values():
            self.assertEqual(len(counts), len(room["dates"]))
        # stack total per day equals that day's real volume
        self.assertEqual(sum(c[-1] for c in series.values()), per_day[room["dates"][-1]])

        self.assertEqual(len(room["per_hour"]), 24)
        self.assertEqual(sum(room["per_hour"]), room["window_messages"])
        this_hour = int(time.strftime("%H"))
        self.assertGreaterEqual(room["per_hour"][this_hour], 3)

        edges = {(e["from"], e["to"]): e["messages"] for e in room["reply_graph"]}
        self.assertEqual(edges.get(("alpha", "beta")), 1)
        senders = {s["sender"]: s for s in room["by_sender"]}
        self.assertEqual(senders["beta"]["replies"], 1)
        self.assertEqual(senders["alpha"]["threads_started"], 1)
        self.assertEqual(senders["beta"]["threads_started"], 1)
        self.assertGreaterEqual(room["stats"]["active_senders"], 2)
        self.assertIn("alpha", {s["sender"] for s in room["by_sender"]})
        self.assertEqual(room["stats"]["replies"], 1)

    def test_analytics_page_renders(self):
        base = self.start()
        st, body = get(base + "/analytics")
        self.assertEqual(st, 200)
        self.assertIn("Agent Room", body)
        self.assertIn("/api/analytics?days=", body)

    def test_thread_health_and_backlog_are_measured(self):
        """Tier C gauges: unanswered rate, median reply lag, per-agent backlog."""
        base = self.start()
        root = json.loads(post(base + "/send", {"from": "alpha", "topic": "unit",
                                                "text": "question"})[1])["id"]
        time.sleep(0.05)
        post(base + "/send", {"from": "beta", "topic": "unit", "text": "answer",
                              "reply_to": root})
        post(base + "/send", {"from": "gamma", "topic": "unit", "text": "orphan"})
        post(base + "/api/reads", {"name": "alpha", "last_id": root})
        room = get_json(base + "/api/analytics?days=1")["room"]
        st = room["stats"]
        self.assertGreaterEqual(st["thread_roots"], 2)
        self.assertGreaterEqual(st["open_threads"], 1)
        self.assertGreater(st["unanswered_rate"], 0)
        self.assertIsNotNone(st["median_reply_lag_s"])
        backlog = {b["name"]: b["backlog"] for b in room["backlog"]}
        self.assertIn("alpha", backlog)
        self.assertGreater(backlog["alpha"], 0, "a cursor behind the tip has a backlog")

    def test_snapshot_file_is_read_only_and_cached(self):
        write_usage(self.usage)
        base = self.start(self.usage)
        before = os.stat(self.usage).st_mtime
        get_json(base + "/api/analytics?days=7")
        get_json(base + "/api/analytics?days=7")
        self.assertEqual(os.stat(self.usage).st_mtime, before)


if __name__ == "__main__":
    unittest.main()
