"""Bus behaviour: auth, threading, telemetry, rate limits, search, cursors, viewer."""
import json
import os
import sqlite3
import unittest
import urllib.parse

from support import bus, get, get_json, post, sign, SECRET


class BusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._ctx = bus(AGENT_CHAT_RATE_PER_MIN="600", AGENT_CHAT_RATE_BURST="1000",
                       AGENT_CHAT_MAX_BYTES="4000")
        cls.base, cls.tmp, cls.svc = cls._ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)

    # -- basics -----------------------------------------------------------
    def test_health_and_tip(self):
        h = get_json(self.base + "/health")
        self.assertEqual(h["status"], "ok")
        self.assertIn("messages", h)
        tip = get_json(self.base + "/api/tip")
        self.assertIsInstance(tip["max_id"], int)

    def test_signed_send_and_read_back(self):
        st, body = post(self.base + "/send",
                        {"from": "tester", "topic": "unit", "text": "hello room"})
        self.assertEqual(st, 200, body)
        mid = json.loads(body)["id"]
        msgs = get_json(self.base + f"/api/messages?since={mid - 1}&limit=5")
        self.assertTrue(any(m["id"] == mid and m["text"] == "hello room" for m in msgs))

    def test_bad_signature_rejected(self):
        st, body = post(self.base + "/send", {"from": "x", "text": "forged"}, sig="deadbeef")
        self.assertEqual(st, 401)

    def test_oversize_body_rejected(self):
        st, body = post(self.base + "/send",
                        {"from": "big", "topic": "unit", "text": "x" * 8000})
        self.assertEqual(st, 413, body)

    def test_rate_limit(self):
        with bus(AGENT_CHAT_RATE_PER_MIN="1", AGENT_CHAT_RATE_BURST="3") as (base, _tmp, _s):
            codes = [post(base + "/send", {"from": "flood", "topic": "t",
                                           "text": f"m{i}"})[0] for i in range(6)]
        self.assertIn(429, codes)
        self.assertEqual(codes.count(200), 3)

    # -- threading --------------------------------------------------------
    def test_reply_to_stored_and_thread_query(self):
        st, body = post(self.base + "/send", {"from": "a", "topic": "thread", "text": "root"})
        root = json.loads(body)["id"]
        st, body = post(self.base + "/send", {"from": "b", "topic": "thread",
                                              "text": "answer", "reply_to": root})
        self.assertEqual(st, 200, body)
        reply = json.loads(body)["id"]
        msgs = get_json(self.base + f"/api/messages?thread={root}")
        ids = [m["id"] for m in msgs]
        self.assertIn(root, ids)
        self.assertIn(reply, ids)
        self.assertTrue(all(m["parent_id"] in (None, root) for m in msgs))

    def test_reply_to_unknown_parent_rejected(self):
        st, body = post(self.base + "/send", {"from": "a", "topic": "thread",
                                              "text": "orphan", "reply_to": 999999})
        self.assertEqual(st, 400, body)

    # -- telemetry retention ---------------------------------------------
    def test_telemetry_hidden_by_default(self):
        post(self.base + "/send", {"from": "pad", "topic": "attention", "text": "telemetry-row"})
        default = get_json(self.base + "/api/messages?since=0&limit=2000")
        self.assertFalse(any(m["topic"] == "attention" for m in default))
        all_msgs = get_json(self.base + "/api/messages?since=0&limit=2000&telemetry=1")
        self.assertTrue(any(m["topic"] == "attention" for m in all_msgs))

    # -- search / mentions ------------------------------------------------
    def test_search_finds_text(self):
        post(self.base + "/send", {"from": "s", "topic": "unit",
                                   "text": "unique-searchable-phrase-xyz"})
        hits = get_json(self.base + "/api/search?q="
                        + urllib.parse.quote("unique-searchable-phrase-xyz"))
        self.assertTrue(any("unique-searchable" in m["text"] for m in hits))

    def test_mentions_endpoint(self):
        post(self.base + "/send", {"from": "s", "topic": "work", "text": "ping @zed please"})
        hits = get_json(self.base + "/api/mentions?name=zed")
        self.assertTrue(any("@zed" in m["text"] for m in hits))
        self.assertEqual(get_json(self.base + "/api/mentions?name=nobody"), [])

    # -- read cursors -----------------------------------------------------
    def test_read_cursor_roundtrip(self):
        st, body = post(self.base + "/api/reads", {"name": "cursor-agent", "last_id": 42})
        self.assertEqual(st, 200, body)
        rows = get_json(self.base + "/api/reads")
        self.assertTrue(any(r["name"] == "cursor-agent" and r["last_id"] == 42 for r in rows))
        st, _ = post(self.base + "/api/reads", {"name": "x", "last_id": "nope"})
        self.assertEqual(st, 400)

    # -- agents -----------------------------------------------------------
    def test_agents_hides_webhook_urls(self):
        st, body = post(self.base + "/agents/register",
                        {"name": "hooky", "webhook_url": "http://127.0.0.1:1/wake/hooky"})
        self.assertEqual(st, 200, body)
        agents = get_json(self.base + "/agents")
        self.assertTrue(any(a["name"] == "hooky" for a in agents))
        self.assertFalse(any("webhook_url" in a for a in agents))

    # -- viewer + page token ---------------------------------------------
    def test_viewer_page_has_compose_and_token(self):
        st, html = get(self.base + "/")
        self.assertEqual(st, 200)
        self.assertIn("Agent Chat Room", html)
        self.assertIn("ui/send", html)
        self.assertIn("compose", html)

    def test_ui_send_with_page_token(self):
        st, html = get(self.base + "/")
        token = html.split('const TOKEN="', 1)[1].split('"', 1)[0]
        st, body = post(self.base + "/ui/send",
                        {"token": token, "text": "from the browser", "from": "human",
                         "topic": "unit"}, signed=False)
        self.assertEqual(st, 200, body)
        self.assertTrue(json.loads(body)["ok"])

    def test_ui_send_rejects_bad_token(self):
        st, body = post(self.base + "/ui/send",
                        {"token": "1.bad", "text": "nope"}, signed=False)
        self.assertEqual(st, 401, body)

    # -- metrics ----------------------------------------------------------
    def test_metrics_exposes_counters(self):
        st, body = get(self.base + "/metrics")
        self.assertEqual(st, 200)
        self.assertIn("agent_chat_messages_total", body)
        self.assertIn("agent_chat_sent_total", body)


class MigrationTests(unittest.TestCase):
    def test_legacy_schema_is_upgraded(self):
        """A pre-v0.3 room.db (no parent_id, no reads) must open and keep its rows."""
        import tempfile
        tmp = tempfile.mkdtemp(prefix="fleet-legacy-")
        db = os.path.join(tmp, "room.db")
        c = sqlite3.connect(db)
        c.execute("""CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     ts REAL NOT NULL, sender TEXT NOT NULL,
                     topic TEXT NOT NULL DEFAULT 'general', text TEXT NOT NULL,
                     kind TEXT NOT NULL DEFAULT 'message')""")
        c.execute("CREATE TABLE agents (name TEXT PRIMARY KEY, webhook_url TEXT NOT NULL,"
                  " registered_at REAL NOT NULL)")
        c.execute("INSERT INTO messages (ts, sender, topic, text) "
                  "VALUES (1.0, 'old', 'general', 'legacy message')")
        c.commit()
        c.close()
        with bus(AGENT_CHAT_DB=db) as (base, _tmp, _svc):
            msgs = get_json(base + "/api/messages?since=0&limit=100")
            self.assertTrue(any(m["text"] == "legacy message" for m in msgs))
            self.assertTrue(all("parent_id" in m for m in msgs))
            st, _ = post(base + "/api/reads", {"name": "m", "last_id": 1})
            self.assertEqual(st, 200)
        c = sqlite3.connect(db)
        cols = {r[1] for r in c.execute("PRAGMA table_info(messages)")}
        self.assertIn("parent_id", cols)
        self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 2)
        c.close()


if __name__ == "__main__":
    unittest.main()
