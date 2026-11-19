"""Schema v4: message envelopes, tasks with leases, artifacts, amendments.

The board is a projection of `tasks`; `task_events` is the record. These tests
pin both, plus the identity rule: the sender the signature proves is the actor,
and a claim cannot be stolen while its lease is live.
"""
import json
import os
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from support import bus, get_json, post


def fetch(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


class Tasks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fleet-tasks-")
        self.ctx = bus(AGENT_CHAT_RATE_PER_MIN="600", AGENT_CHAT_RATE_BURST="1000",
                       AGENT_CHAT_ARTIFACTS=os.path.join(self.tmp, "artifacts"),
                       AGENT_CHAT_ARTIFACT_OVER="0")
        self.base, self.tmpdir, _svc = self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__, None, None, None)

    def call(self, path, payload) -> tuple[int, dict]:
        st, body = post(self.base + path, payload)
        return st, (json.loads(body) if body.strip().startswith("{") else body)

    def make_task(self, **kw):
        payload = {"from": "alice", "title": "Wire the widget", "topic": "unit"}
        payload.update(kw)
        st, d = self.call("/api/tasks", payload)
        self.assertEqual(st, 200, d)
        return d["task"]

    # ---------------- create / board ----------------
    def test_create_announces_and_lands_on_the_board(self):
        t = self.make_task(ref="PB-114", detail="make it real")
        self.assertEqual(t["state"], "open")
        self.assertEqual(t["effective_state"], "open")
        self.assertEqual(t["ref"], "PB-114")
        board = get_json(self.base + "/api/tasks?state=all")
        self.assertEqual(board["count"], 1)
        self.assertEqual(board["counts"].get("open"), 1)
        # the room got a task message carrying the envelope
        msgs = get_json(self.base + "/api/messages?since=0&limit=50&kind=task")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["kind"], "task")
        self.assertEqual(msgs[0]["data"]["task"], t["id"])
        self.assertEqual(msgs[0]["text"].splitlines()[0], "Wire the widget")

    def test_missing_title_is_400(self):
        st, d = self.call("/api/tasks", {"from": "alice", "title": "  "})
        self.assertEqual(st, 400)
        self.assertIn("title", d["error"])

    def test_board_filters_by_state_owner_and_ref(self):
        t1 = self.make_task(title="one", ref="PB-1", owner="bob")
        self.make_task(title="two", ref="PB-2")
        self.call("/api/tasks/claim", {"from": "bob", "id": t1["id"]})
        open_only = get_json(self.base + "/api/tasks?state=open")
        self.assertEqual([t["title"] for t in open_only["tasks"]], ["two"])
        claimed = get_json(self.base + "/api/tasks?state=claimed")
        self.assertEqual([t["title"] for t in claimed["tasks"]], ["one"])
        mine = get_json(self.base + "/api/tasks?state=all&owner=bob")
        self.assertEqual([t["title"] for t in mine["tasks"]], ["one"])
        by_ref = get_json(self.base + "/api/tasks?state=all&ref=PB-2")
        self.assertEqual(by_ref["count"], 1)

    # ---------------- leases ----------------
    def test_claim_is_exclusive_while_the_lease_is_live(self):
        t = self.make_task()
        st, d = self.call("/api/tasks/claim", {"from": "bob", "id": t["id"], "lease_s": 600})
        self.assertEqual(st, 200, d)
        self.assertEqual(d["task"]["state"], "claimed")
        self.assertEqual(d["task"]["claimed_by"], "bob")
        self.assertGreater(d["task"]["lease_s_left"], 500)

        st, d = self.call("/api/tasks/claim", {"from": "carol", "id": t["id"]})
        self.assertEqual(st, 409, "a live lease must not be stolen")
        self.assertIn("bob", d["error"])

        # the holder can re-claim (idempotent) and renew
        st, d = self.call("/api/tasks/renew", {"from": "bob", "id": t["id"], "lease_s": 900})
        self.assertEqual(st, 200, d)
        self.assertGreater(d["task"]["lease_s_left"], 800)
        st, d = self.call("/api/tasks/renew", {"from": "carol", "id": t["id"]})
        self.assertEqual(st, 409)

    def test_expired_lease_reads_as_open_and_is_claimable(self):
        t = self.make_task()
        st, d = self.call("/api/tasks/claim", {"from": "bob", "id": t["id"], "lease_s": 60})
        self.assertEqual(st, 200, d)
        # force expiry: a 60s lease is the floor, so age it directly in the DB
        import sqlite3
        con = sqlite3.connect(os.path.join(self.tmpdir, "room.db"), timeout=10)
        con.execute("UPDATE tasks SET claim_expires = ? WHERE id = ?",
                    (time.time() - 5, t["id"]))
        con.commit()
        con.close()
        board = get_json(self.base + "/api/tasks?state=open")
        self.assertIn(t["id"], [x["id"] for x in board["tasks"]],
                      "an expired claim is open work, not a held task")
        st, d = self.call("/api/tasks/claim", {"from": "carol", "id": t["id"]})
        self.assertEqual(st, 200, d)
        self.assertEqual(d["task"]["claimed_by"], "carol")

    # ---------------- completion ----------------
    def test_complete_records_evidence_and_announces(self):
        t = self.make_task()
        self.call("/api/tasks/claim", {"from": "bob", "id": t["id"]})
        st, d = self.call("/api/tasks/complete",
                          {"from": "bob", "id": t["id"], "outcome": "shipped",
                           "evidence": "abc1234"})
        self.assertEqual(st, 200, d)
        self.assertEqual(d["task"]["state"], "done")
        self.assertEqual(d["task"]["outcome"], "shipped")
        self.assertEqual(d["task"]["evidence"], "abc1234")
        self.assertTrue(d["posted"], "completion is room news")
        outcomes = get_json(self.base + "/api/messages?since=0&limit=50&kind=outcome")
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]["data"]["task"], t["id"])
        self.assertIn("abc1234", outcomes[0]["text"])

        st, d = self.call("/api/tasks/complete", {"from": "bob", "id": t["id"]})
        self.assertEqual(st, 409)
        st, d = self.call("/api/tasks/claim", {"from": "carol", "id": t["id"]})
        self.assertEqual(st, 409, "a done task is not claimable")

    def test_a_foreign_claim_cannot_be_completed_by_someone_else(self):
        t = self.make_task()
        self.call("/api/tasks/claim", {"from": "bob", "id": t["id"]})
        st, d = self.call("/api/tasks/complete", {"from": "carol", "id": t["id"]})
        self.assertEqual(st, 409)
        self.assertIn("bob", d["error"])

    def test_bounce_requires_a_reason_and_releases_the_task(self):
        t = self.make_task()
        self.call("/api/tasks/claim", {"from": "bob", "id": t["id"]})
        st, d = self.call("/api/tasks/bounce", {"from": "bob", "id": t["id"]})
        self.assertEqual(st, 400)
        self.assertIn("reason", d["error"])
        st, d = self.call("/api/tasks/bounce",
                          {"from": "bob", "id": t["id"], "reason": "wrong repo"})
        self.assertEqual(st, 200, d)
        self.assertEqual(d["task"]["state"], "bounced")
        self.assertIsNone(d["task"]["claimed_by"])

    def test_release_returns_work_to_the_pool_without_calling_it_rejected(self):
        t = self.make_task()
        self.call("/api/tasks/claim", {"from": "bob", "id": t["id"]})
        st, d = self.call("/api/tasks/release", {"from": "carol", "id": t["id"]})
        self.assertEqual(st, 409, "only the holder may release")
        st, d = self.call("/api/tasks/release",
                          {"from": "bob", "id": t["id"], "reason": "out of turn budget"})
        self.assertEqual(st, 200, d)
        self.assertEqual(d["task"]["state"], "open", "released is open, not bounced")
        self.assertIsNone(d["task"]["claimed_by"])
        detail = get_json(self.base + f"/api/tasks?id={t['id']}")
        self.assertEqual([e["action"] for e in detail["events"]], ["create", "claim", "release"])
        st, d = self.call("/api/tasks/claim", {"from": "carol", "id": t["id"]})
        self.assertEqual(st, 200, d)

    def test_task_detail_has_events_and_thread(self):
        t = self.make_task()
        self.call("/api/tasks/claim", {"from": "bob", "id": t["id"]})
        d = get_json(self.base + f"/api/tasks?id={t['id']}")
        self.assertEqual(d["id"], t["id"])
        actions = [e["action"] for e in d["events"]]
        self.assertEqual(actions, ["create", "claim"])
        self.assertTrue(d["thread"])
        self.assertEqual(d["thread"][0]["kind"], "task")
        st, body = fetch(self.base + "/api/tasks?id=99999")
        self.assertEqual(st, 404)

    # ---------------- envelopes ----------------
    def test_send_carries_a_data_envelope(self):
        st, body = post(self.base + "/send", {
            "from": "alice", "topic": "unit", "kind": "verdict",
            "text": "accept", "data": {"task": 7, "verdict": "ACCEPT", "commit": "abc"}})
        self.assertEqual(st, 200, body)
        rows = get_json(self.base + "/api/messages?since=0&limit=50&kind=verdict")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["data"]["verdict"], "ACCEPT")
        self.assertEqual(rows[0]["data"]["commit"], "abc")

    def test_unknown_kind_is_rejected_with_the_allowed_set(self):
        st, body = post(self.base + "/send", {"from": "alice", "text": "x",
                                              "kind": "gossip"})
        self.assertEqual(st, 400)
        d = json.loads(body)
        self.assertEqual(d["error"], "unknown kind 'gossip'")
        self.assertIn("verdict", d["allowed"])

    def test_data_must_be_an_object_and_bounded(self):
        st, body = post(self.base + "/send", {"from": "alice", "text": "x",
                                              "data": ["not", "a", "dict"]})
        self.assertEqual(st, 400)
        self.assertIn("JSON object", json.loads(body)["error"])
        st, body = post(self.base + "/send", {"from": "alice", "text": "x",
                                              "data": {"blob": "z" * 5000}})
        self.assertEqual(st, 400)
        self.assertIn("larger than", json.loads(body)["error"])

    # ---------------- artifacts + amendments ----------------
    def test_artifact_roundtrip(self):
        payload = "report\n" + ("line\n" * 500)
        st, d = self.call("/api/artifacts", {"from": "alice", "text": payload})
        self.assertEqual(st, 200, d)
        sha = d["sha256"]
        self.assertEqual(len(sha), 64)
        self.assertGreater(d["bytes"], 2000)
        st, body = fetch(self.base + d["url"])
        self.assertEqual(st, 200)
        self.assertEqual(body, payload)
        st, _ = fetch(self.base + "/artifact/" + "0" * 64)
        self.assertEqual(st, 404, "unknown or malformed sha is a 404, never a traversal")

    def test_route_large_folds_a_long_post_into_an_artifact(self):
        big = "x" * 9000
        st, body = post(self.base + "/send", {"from": "alice", "topic": "unit",
                                              "text": big, "route_large": True})
        self.assertEqual(st, 200, body)
        rows = get_json(self.base + "/api/messages?since=0&limit=50&sender=alice")
        row = rows[-1]
        self.assertLess(len(row["text"]), 700, "the room keeps a head, not the essay")
        art = row["data"]["artifact"]
        self.assertEqual(art["chars"], 9000)
        st, served = fetch(self.base + art["url"])
        self.assertEqual(st, 200)
        self.assertEqual(served, big)

    def test_only_the_sender_can_amend(self):
        st, body = post(self.base + "/send", {"from": "alice", "topic": "unit",
                                              "text": "typo here"})
        target = json.loads(body)["id"]
        st, body = post(self.base + "/api/amend", {"from": "bob", "id": target,
                                                   "text": "not mine"})
        self.assertEqual(st, 403)
        st, body = post(self.base + "/api/amend", {"from": "alice", "id": target,
                                                   "text": "fixed"})
        self.assertEqual(st, 200, body)
        d = json.loads(body)
        self.assertEqual(d["supersedes"], target)
        rows = get_json(self.base + "/api/messages?since=0&limit=50&kind=amendment")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["data"]["supersedes"], target)
        self.assertTrue(rows[0]["text"].startswith(f"[amendment to #{target}]"))
        # the original is untouched: no delete exists in this bus
        orig = [m for m in get_json(self.base + "/api/messages?since=0&limit=50")
                if m["id"] == target][0]
        self.assertEqual(orig["text"], "typo here")


class UiTaskEndpoint(unittest.TestCase):
    """The board is clickable: /ui/task is the page-token path, same actions."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fleet-uitask-")
        self.ctx = bus(AGENT_CHAT_RATE_PER_MIN="600", AGENT_CHAT_RATE_BURST="1000",
                       AGENT_CHAT_ARTIFACTS=os.path.join(self.tmp, "artifacts"))
        self.base, self.tmpdir, _svc = self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__, None, None, None)

    def make_task(self):
        st, body = post(self.base + "/api/tasks",
                        {"from": "alice", "title": "board click", "topic": "unit"})
        self.assertEqual(st, 200, body)
        return json.loads(body)["task"]

    def token(self):
        import re
        _st, body = fetch(self.base + "/")
        m = re.search(r'const TOKEN="([^"]+)"', body)
        self.assertIsNotNone(m, "viewer page must mint a token")
        return m.group(1)

    def ui(self, payload):
        st, body = post(self.base + "/ui/task", payload, signed=False)
        return st, (json.loads(body) if body.strip().startswith("{") else body)

    def test_ui_task_requires_the_page_token(self):
        t = self.make_task()
        st, _d = self.ui({"action": "claim", "id": t["id"], "from": "shane"})
        self.assertEqual(st, 401)
        st, _d = self.ui({"token": "bogus.0", "action": "claim", "id": t["id"],
                          "from": "shane"})
        self.assertEqual(st, 401)

    def test_ui_task_claims_and_completes(self):
        t = self.make_task()
        tok = self.token()
        st, d = self.ui({"token": tok, "action": "claim", "id": t["id"], "from": "shane"})
        self.assertEqual(st, 200, d)
        self.assertEqual(d["task"]["claimed_by"], "shane")
        st, d = self.ui({"token": tok, "action": "complete", "id": t["id"],
                         "from": "shane", "outcome": "clicked through", "announce": True})
        self.assertEqual(st, 200, d)
        self.assertEqual(d["task"]["state"], "done")
        self.assertTrue(d["posted"])
        st, _d = self.ui({"token": tok, "action": "release", "id": t["id"], "from": "shane"})
        self.assertEqual(st, 409, "a done task cannot be released")

    def test_unknown_ui_action_is_400(self):
        tok = self.token()
        st, d = self.ui({"token": tok, "action": "teleport", "id": 1, "from": "shane"})
        self.assertEqual(st, 400)
        self.assertIn("action must be", d["error"])


if __name__ == "__main__":
    unittest.main()
