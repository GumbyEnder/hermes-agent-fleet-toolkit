"""Fleet usage: collector shape, budgets, retention, viewer endpoints, weekly guard."""
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

from support import ROOT, Service, base_env, free_port, get, get_json

COLLECTOR = str(ROOT / "fleet-usage/token_collector.py")
VIEWER = str(ROOT / "fleet-usage/usage_viewer.py")
WEEKLY = str(ROOT / "fleet-usage/weekly_burn_post.py")


def make_home(tmp, rows=()):
    """Fake HERMES home with one profile DB containing session_model_usage rows."""
    home = pathlib.Path(tmp) / "home"
    prof = home / ".hermes/profiles/demo"
    prof.mkdir(parents=True)
    db = sqlite3.connect(prof / "state.db")
    db.execute("""CREATE TABLE session_model_usage (
        session_id TEXT, model TEXT, billing_mode TEXT, api_call_count INTEGER,
        input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
        cache_write_tokens INTEGER, reasoning_tokens INTEGER,
        estimated_cost_usd REAL, actual_cost_usd REAL, first_seen REAL, last_seen REAL)""")
    now = time.time()
    for i, row in enumerate(rows):
        db.execute("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (f"s{i}", "test-model", "api", 3, row.get("input", 1000),
                    row.get("output", 100), 0, 0, 0, row.get("cost", 1.0), 0.0,
                    now - 3600, now - 60))
    db.commit()
    db.close()
    return home


class CollectorTests(unittest.TestCase):
    def run_collector(self, env_extra, timeout=120):
        return subprocess.run([sys.executable, COLLECTOR], env=env_extra,
                              capture_output=True, text=True, timeout=timeout)

    def test_empty_home_is_safe(self):
        """No ~/.hermes at all must not crash (CI runs this way)."""
        tmp = tempfile.mkdtemp(prefix="fleet-usage-")
        out = os.path.join(tmp, "usage.json")
        env = base_env(HOME=os.path.join(tmp, "empty"), USAGE_JSON=out,
                       AGENT_USAGE_REMOTES="")
        r = self.run_collector(env)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.load(open(out))
        for win in ("7d", "30d", "all"):
            self.assertIn(win, data["windows"])
            self.assertEqual(data["windows"][win]["total"]["sessions"], 0)
        self.assertEqual(data["profiles"], [])

    def test_collects_rows_and_reports_shape(self):
        tmp = tempfile.mkdtemp(prefix="fleet-usage2-")
        home = make_home(tmp, [{"input": 5000, "output": 500, "cost": 1.5}])
        out = os.path.join(tmp, "usage.json")
        env = base_env(HOME=str(home), USAGE_JSON=out, AGENT_USAGE_REMOTES="")
        r = self.run_collector(env)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.load(open(out))
        self.assertEqual(data["windows"]["7d"]["total"]["sessions"], 1)
        self.assertEqual(data["windows"]["7d"]["total"]["input"], 5000)
        self.assertAlmostEqual(data["windows"]["7d"]["total"]["est_cost"], 1.5, places=3)
        self.assertIn("demo", data["windows"]["7d"]["by_profile"])
        self.assertTrue(data["top_sessions"])
        self.assertIn("budgets", data)

    def test_budget_alert_fires_over_threshold(self):
        tmp = tempfile.mkdtemp(prefix="fleet-usage3-")
        home = make_home(tmp, [{"input": 100, "output": 10, "cost": 4.0}])
        out = os.path.join(tmp, "usage.json")
        env = base_env(HOME=str(home), USAGE_JSON=out, AGENT_USAGE_REMOTES="",
                       USAGE_BUDGET_PROFILE_USD="1.0", AGENT_CHAT_SECRET="")
        r = self.run_collector(env)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.load(open(out))
        self.assertEqual(len(data["budgets"]), 1)
        self.assertEqual(data["budgets"][0]["profile"], "demo")
        self.assertGreater(data["budgets"][0]["cost_24h"], 1.0)

    def test_history_retention_prunes_old_snapshots(self):
        tmp = tempfile.mkdtemp(prefix="fleet-usage4-")
        home = make_home(tmp)
        out = os.path.join(tmp, "usage.json")
        hist = os.path.join(tmp, "history.db")
        c = sqlite3.connect(hist)
        c.execute("""CREATE TABLE snapshots (ts REAL PRIMARY KEY, sessions INTEGER,
                     input INTEGER, output INTEGER, cache_read INTEGER, reasoning INTEGER,
                     billable_cost REAL, included_cost REAL)""")
        old = time.time() - 500 * 86400
        c.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?)", (old, 1, 1, 1, 0, 0, 0, 0))
        c.commit()
        c.close()
        env = base_env(HOME=str(home), USAGE_JSON=out, AGENT_USAGE_REMOTES="",
                       USAGE_HISTORY_DAYS="90")
        r = self.run_collector(env)
        self.assertEqual(r.returncode, 0, r.stderr)
        c = sqlite3.connect(hist)
        remaining = c.execute("SELECT COUNT(*) FROM snapshots WHERE ts = ?", (old,)).fetchone()[0]
        c.close()
        self.assertEqual(remaining, 0, "old snapshot should have been pruned")


class ViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="fleet-viewer-")
        cls.data = os.path.join(cls.tmp, "usage.json")
        now = time.time()
        json.dump({"generated": now, "profiles": ["demo"], "history": [], "top_sessions": [],
                   "anomaly": {"active": False}, "budgets": [],
                   "windows": {"7d": {"cutoff": 0,
                                      "total": {"sessions": 2, "input": 100, "output": 10,
                                                "cache_read": 0, "reasoning": 0,
                                                "est_cost": 1.25, "actual_cost": 0.0,
                                                "billable_cost": 1.25, "included_cost": 0.0},
                                      "by_profile": {"demo": {"sessions": 2, "input": 100,
                                                              "output": 10, "cache_read": 0,
                                                              "est_cost": 1.25,
                                                              "last_active": now}},
                                      "by_model": {"m1": {"sessions": 2, "input": 100,
                                                          "output": 10, "cache_read": 0,
                                                          "est_cost": 1.25}},
                                      "last_active": {"demo": now}}}}, open(cls.data, "w"))
        port = free_port()
        env = base_env(USAGE_JSON=cls.data, USAGE_PORT=str(port), USAGE_BIND="",
                       NOTIFY_SOCKET="")
        cls.base = f"http://127.0.0.1:{port}"
        cls._svc = Service(VIEWER, env, cls.base + "/health")
        cls._svc.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._svc.__exit__(None, None, None)

    def test_health_reports_generated(self):
        h = get_json(self.base + "/health")
        self.assertEqual(h["status"], "ok")
        self.assertGreater(h["generated"], 0)

    def test_csv_export(self):
        st, body = get(self.base + "/api/export.csv")
        self.assertEqual(st, 200)
        self.assertTrue(body.startswith("window,profile,model"))
        self.assertIn("demo", body)

    def test_metrics(self):
        st, body = get(self.base + "/metrics")
        self.assertEqual(st, 200)
        self.assertIn('fleet_usage_est_cost{window="7d"} 1.25', body)
        self.assertIn("fleet_usage_anomaly_active 0", body)


class WeeklyGuard(unittest.TestCase):
    def test_recent_state_short_circuits(self):
        tmp = tempfile.mkdtemp(prefix="fleet-weekly-")
        data = os.path.join(tmp, "usage.json")
        with open(data, "w") as fh:
            json.dump({"windows": {"7d": {"total": {"sessions": 1, "input": 1, "output": 1,
                                                    "cache_read": 0, "est_cost": 0.0},
                                          "by_profile": {}}}}, fh)
        with open(os.path.join(tmp, ".weekly_state.json"), "w") as fh:
            json.dump({"ts": time.time()}, fh)
        env = base_env(USAGE_JSON=data, AGENT_CHAT_SECRET="")
        r = subprocess.run([sys.executable, WEEKLY], env=env, capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
