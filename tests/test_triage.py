"""Agent triage endpoints: inbox, digest, threads, presence, read shaping.

These exist so an agent can decide *whether* to read a message without paying
for its body. The assertions below pin the contract, not the prose.
"""
import json
import time
import unittest
import urllib.error
import urllib.request

from support import bus, get_json, post


def fetch(url):
    """support.get() lets HTTPError escape; these endpoints test error codes."""
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


class Triage(unittest.TestCase):
    def setUp(self):
        self.ctx = bus(AGENT_CHAT_RATE_PER_MIN="600", AGENT_CHAT_RATE_BURST="1000")
        self.base, self.tmp, self.svc = self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__, None, None, None)

    def send(self, sender, text, topic="unit", reply_to=None):
        payload = {"from": sender, "topic": topic, "text": text}
        if reply_to:
            payload["reply_to"] = reply_to
        st, body = post(self.base + "/send", payload)
        self.assertEqual(st, 200, body)
        return json.loads(body)["id"]

    def publish(self, name, last_id):
        st, body = post(self.base + "/api/reads", {"name": name, "last_id": last_id})
        self.assertEqual(st, 200, body)

    # ---------------- threads ----------------
    def test_threads_carry_participants_and_lag(self):
        root = self.send("alpha", "question about the migration")
        self.send("beta", "answer one", reply_to=root)
        self.send("gamma", "answer two", reply_to=root)
        self.send("delta", "unrelated new topic")
        d = get_json(self.base + "/api/threads?state=all&limit=50")
        by_id = {t["id"]: t for t in d["threads"]}
        self.assertIn(root, by_id)
        t = by_id[root]
        self.assertTrue(t["answered"])
        self.assertEqual(t["replies"], 2)
        self.assertEqual(t["participants"], ["alpha", "beta", "gamma"])
        self.assertIsNotNone(t["first_reply_lag_s"])
        self.assertGreaterEqual(t["first_reply_lag_s"], 0)
        self.assertEqual(t["preview"], "question about the migration")

        open_only = get_json(self.base + "/api/threads?state=open&limit=50")
        ids = [t["id"] for t in open_only["threads"]]
        self.assertNotIn(root, ids)
        self.assertTrue(all(not t["answered"] for t in open_only["threads"]))

        answered = get_json(self.base + "/api/threads?state=answered&limit=50")
        self.assertIn(root, [t["id"] for t in answered["threads"]])

    def test_threads_stale_filter(self):
        root = self.send("alpha", "nobody answered me")
        fresh = get_json(self.base + "/api/threads?state=stale&older_than=3600")
        self.assertNotIn(root, [t["id"] for t in fresh["threads"]])
        stale = get_json(self.base + "/api/threads?state=stale&older_than=0")
        self.assertIn(root, [t["id"] for t in stale["threads"]])

    def test_open_state_survives_a_busy_page(self):
        """state=open must reach past newer answered threads, not filter a page."""
        old_root = self.send("alpha", "still waiting on this one")
        for i in range(12):
            r = self.send("beta", f"topic {i}")
            self.send("gamma", "answered", reply_to=r)
        d = get_json(self.base + "/api/threads?state=open&limit=5")
        self.assertIn(old_root, [t["id"] for t in d["threads"]])
        self.assertTrue(all(not t["answered"] for t in d["threads"]))

    def test_bad_state_is_rejected(self):
        st, body = fetch(self.base + "/api/threads?state=bogus")
        self.assertEqual(st, 400)
        self.assertIn("state must be", body)

    # ---------------- read shaping ----------------
    def test_meta_only_read_drops_body_but_keeps_size(self):
        big = "x" * 4000
        self.send("alpha", big)
        rows = get_json(self.base + "/api/messages?since=0&limit=50&fields=meta")
        row = [r for r in rows if r["sender"] == "alpha"][-1]
        self.assertNotIn("text", row)
        self.assertEqual(row["chars"], 4000)
        self.assertIn(len(row["preview"]), (200, 201), "200 chars plus the ellipsis")

    def test_max_chars_clips_and_flags(self):
        self.send("alpha", "y" * 500)
        rows = get_json(self.base + "/api/messages?since=0&limit=50&max_chars=100")
        row = [r for r in rows if r["sender"] == "alpha"][-1]
        self.assertEqual(len(row["text"]), 100)
        self.assertTrue(row["truncated"])
        self.assertEqual(row["chars"], 500)

    def test_sender_and_kind_filters(self):
        self.send("alpha", "one")
        self.send("beta", "two")
        rows = get_json(self.base + "/api/messages?since=0&limit=50&sender=beta")
        self.assertTrue(rows)
        self.assertTrue(all(r["sender"] == "beta" for r in rows))
        only_system = get_json(self.base + "/api/messages?since=0&limit=50&kind=system")
        self.assertTrue(all(r["kind"] == "system" for r in only_system))

    def test_bad_max_chars_is_a_400(self):
        st, body = fetch(self.base + "/api/messages?since=0&max_chars=abc")
        self.assertEqual(st, 400)

    # ---------------- digest ----------------
    def test_digest_summarises_a_window(self):
        root = self.send("alpha", "kickoff for PB-1")
        self.send("beta", "ack", reply_to=root)
        tail = self.send("gamma", "@alpha please review")
        d = get_json(self.base + "/api/digest?since=0&for=alpha")
        self.assertEqual(d["count"], d["next_since"])
        self.assertEqual(d["tip"], max(d["next_since"], d["tip"]))
        self.assertGreaterEqual(d["next_since"], tail)
        senders = {s["sender"]: s["messages"] for s in d["by_sender"]}
        self.assertGreaterEqual(senders["alpha"], 1)
        self.assertIn("unit", [t["topic"] for t in d["by_topic"]])
        self.assertEqual(d["mention_count"], 1, "one @alpha mention in the window")
        self.assertEqual(d["unanswered_mentions"], 1)
        self.assertTrue(any(m["id"] == tail for m in d["mentions"]))

    def test_digest_cursor_is_idempotent(self):
        self.send("alpha", "first")
        d1 = get_json(self.base + "/api/digest?since=0")
        d2 = get_json(self.base + f"/api/digest?since={d1['next_since']}")
        self.assertEqual(d2["count"], 0)
        self.assertEqual(d2["next_since"], d1["next_since"])

    # ---------------- inbox ----------------
    def test_inbox_reports_backlog_mentions_and_threads(self):
        st, body = fetch(self.base + "/api/inbox?name=alpha")
        self.assertEqual(st, 200)
        empty = json.loads(body)
        self.assertFalse(empty["cursor_published"])
        self.assertEqual(empty["mentions"], [])
        self.assertGreater(empty["backlog"], 0)

        root = self.send("beta", "@alpha can you take PB-9?")
        self.send("gamma", "note in the same thread", reply_to=root)
        self.publish("alpha", root - 1)

        d = get_json(self.base + "/api/inbox?name=alpha")
        self.assertTrue(d["cursor_published"])
        self.assertEqual(d["cursor"], root - 1)
        self.assertEqual(d["mention_count"], 1)
        self.assertEqual(d["mentions"][0]["id"], root)
        self.assertEqual(d["unanswered_mentions"], 1)
        self.assertEqual(d["suggested_cursor"], d["tip"])

        # alpha answers -> the mention stops reading as unanswered
        self.send("alpha", "on it", reply_to=root)
        d2 = get_json(self.base + "/api/inbox?name=alpha")
        self.assertEqual(d2["unanswered_mentions"], 0)

    def test_inbox_requires_a_name(self):
        st, body = fetch(self.base + "/api/inbox")
        self.assertEqual(st, 400)

    # ---------------- presence ----------------
    def test_presence_derives_state_from_read_cursors(self):
        self.publish("alpha", 0)
        d = get_json(self.base + "/api/presence")
        row = [a for a in d["agents"] if a["name"] == "alpha"][0]
        self.assertEqual(row["state"], "active")
        self.assertIn("tip", d)
        self.assertIsNotNone(row["backlog"])


if __name__ == "__main__":
    unittest.main()
