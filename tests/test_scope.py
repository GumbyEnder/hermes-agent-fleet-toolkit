"""v5 scope: project/ticket columns, the registry, filters, and the rollout flag.

Scope is a structured column, not a topic string: `topic` stays the delivery lane
(work/attention/telemetry) and never carries project identity. Rows that predate
v5 stay NULL, which reads as "unscoped", not "general".
"""
import json
import unittest

from support import bus, get_json, post


def send(base, text, **extra):
    payload = {"from": "tester", "topic": "work", "text": text}
    payload.update(extra)
    st, body = post(base + "/send", payload)
    return st, json.loads(body)


class ScopeTests(unittest.TestCase):
    def test_project_and_ticket_round_trip(self):
        with bus() as (base, _tmp, _svc):
            st, res = send(base, "scoped message", project="dev-boards",
                           ticket="T-042")
            self.assertEqual(st, 200, res)
            self.assertEqual(res["project"], "dev-boards")
            # a bare id gets the default system prefixed
            self.assertEqual(res["ticket"], "devboards:T-042")

            one = [m for m in get_json(base + "/api/messages?since=0&limit=50")
                   if m["id"] == res["id"]]
            self.assertEqual(len(one), 1, one)
            self.assertEqual(one[0]["project"], "dev-boards")
            self.assertEqual(one[0]["ticket"], "devboards:T-042")

    def test_ticket_id_keeps_its_case(self):
        with bus() as (base, _tmp, _svc):
            _st, res = send(base, "jira style", project="skyx", ticket="jira:SKYX-3")
            self.assertEqual(res["ticket"], "jira:SKYX-3", res)
            _st, res = send(base, "system lowercased", project="skyx",
                            ticket="JIRA:SKYX-4")
            self.assertEqual(res["ticket"], "jira:SKYX-4", res)

    def test_bad_project_is_refused_not_mangled(self):
        with bus() as (base, _tmp, _svc):
            for bad in ("Dev Boards", "../etc", "x" * 80, "devboards!"):
                st, res = send(base, "nope", project=bad)
                self.assertEqual(st, 400, (bad, res))

    def test_filters_and_registry(self):
        with bus() as (base, _tmp, _svc):
            send(base, "a1", project="alpha")
            send(base, "a2", project="alpha", ticket="T-1")
            send(base, "b1", project="beta")
            send(base, "loose")                       # unscoped, still accepted

            proj = {m["text"] for m in
                    get_json(base + "/api/messages?project=alpha&limit=50")}
            self.assertEqual(proj, {"a1", "a2"})
            tick = [m["text"] for m in
                    get_json(base + "/api/messages?ticket=devboards:T-1&limit=50")]
            self.assertEqual(tick, ["a2"])
            loose = [m["text"] for m in
                     get_json(base + "/api/messages?unscoped=1&limit=50")]
            # the room's own "created" row is unscoped too, so compare as a set
            self.assertEqual(set(loose), {"loose", "Agent Chat Room created."})

            reg = get_json(base + "/api/projects")
            self.assertEqual(reg["count"], 2)
            names = {p["project"]: p for p in reg["projects"]}
            self.assertEqual(names["alpha"]["messages"], 2)
            self.assertEqual(names["alpha"]["tickets"], 1)
            self.assertGreaterEqual(reg["unscoped"], 1)
            self.assertIn("devboards:T-1",
                          {t["ticket"] for t in reg["tickets"]})

    def test_reply_inherits_parent_scope(self):
        with bus() as (base, _tmp, _svc):
            _st, root = send(base, "root", project="gamma")
            _st, rep = send(base, "reply", reply_to=root["id"])
            self.assertEqual(rep["project"], "gamma", rep)

    def test_require_project_flag(self):
        with bus(AGENT_CHAT_REQUIRE_PROJECT="1") as (base, _tmp, _svc):
            st, res = send(base, "no scope")
            self.assertEqual(st, 400, res)
            st, res = send(base, "scoped", project="alpha")
            self.assertEqual(st, 200, res)

    def test_allowlist(self):
        with bus(AGENT_CHAT_PROJECTS="alpha,beta") as (base, _tmp, _svc):
            st, res = send(base, "in list", project="alpha")
            self.assertEqual(st, 200, res)
            st, res = send(base, "off list", project="gamma")
            self.assertEqual(st, 400, res)


if __name__ == "__main__":
    unittest.main()
