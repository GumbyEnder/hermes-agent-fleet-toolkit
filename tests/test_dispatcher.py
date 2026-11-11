"""Dispatcher: auth, allow-list, dedupe, bounded queue, threaded reply."""
import json
import os
import stat
import tempfile
import time
import unittest

from support import bus, dispatcher, get_json, post, sign

STUB = """#!/bin/sh
# args: -p <profile> -z <prompt>
echo "$2" >> "$STUB_COUNT"
sleep "${STUB_SLEEP:-0}"
echo "stub answer for $2"
"""


def make_stub(tmp):
    p = os.path.join(tmp, "stub-agent.sh")
    with open(p, "w") as fh:
        fh.write(STUB)
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
    return p


def wait_for(fn, timeout=8.0, step=0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return None


class DispatcherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="fleet-disp-")
        cls.count = os.path.join(cls.tmp, "count.txt")
        cls.stub = make_stub(cls.tmp)
        cls._bus_ctx = bus(AGENT_CHAT_RATE_PER_MIN="600", AGENT_CHAT_RATE_BURST="1000")
        cls.bus_url, _tmp, _svc = cls._bus_ctx.__enter__()
        cls._d_ctx = dispatcher(cls.bus_url, WAKE_ALLOW="alpha,beta", WAKE_BIN=cls.stub,
                                WAKE_MAX_CONCURRENCY="1", WAKE_MAX_QUEUE="1",
                                WAKE_DEDUPE_TTL="60", STUB_COUNT=cls.count,
                                STUB_SLEEP="0")
        cls.disp, cls.svc = cls._d_ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._d_ctx.__exit__(None, None, None)
        cls._bus_ctx.__exit__(None, None, None)

    def _post_msg(self, text):
        st, body = post(self.bus_url + "/send",
                        {"from": "tester", "topic": "wake", "text": text})
        return json.loads(body)["id"]

    def test_health_shape(self):
        h = get_json(self.disp + "/health")
        self.assertEqual(h["status"], "ok")
        self.assertEqual(h["allowed"], ["alpha", "beta"])
        for k in ("active", "queued", "max_concurrency", "max_queue", "done", "deduped"):
            self.assertIn(k, h)

    def test_unknown_profile_forbidden(self):
        st, _ = post(self.disp + "/wake/gamma", {"from": "x", "text": "hi"})
        self.assertEqual(st, 403)

    def test_bad_signature_rejected(self):
        st, _ = post(self.disp + "/wake/alpha", {"from": "x", "text": "hi"}, sig="deadbeef")
        self.assertEqual(st, 401)

    def test_duplicate_mention_runs_once(self):
        mid = self._post_msg("@alpha one job")
        payload = {"from": "tester", "topic": "wake", "text": "@alpha one job",
                   "message_id": mid}
        first = post(self.disp + "/wake/alpha", payload)
        second = post(self.disp + "/wake/alpha", payload)
        self.assertEqual(first[0], 200, first[1])
        self.assertEqual(second[0], 200, second[1])
        self.assertTrue(json.loads(second[1]).get("duplicate"))
        got = wait_for(lambda: os.path.exists(self.count) and
                       open(self.count).read().count("alpha") >= 1)
        self.assertTrue(got, "stub agent never ran")
        time.sleep(1.5)      # give a duplicate the chance to overshoot
        self.assertEqual(open(self.count).read().count("alpha"), 1)

    def test_reply_is_posted_threaded(self):
        mid = self._post_msg("@beta please answer")
        st, body = post(self.disp + "/wake/beta",
                        {"from": "tester", "topic": "wake", "text": "@beta please answer",
                         "message_id": mid})
        self.assertEqual(st, 200, body)

        def reply():
            msgs = get_json(self.bus_url + f"/api/messages?thread={mid}&since=0&limit=50")
            return [m for m in msgs if m["sender"] == "beta" and m["parent_id"] == mid]
        got = wait_for(reply, timeout=10)
        self.assertTrue(got, "no threaded reply from the woken agent")
        self.assertIn("stub answer", got[0]["text"])

    def test_one_turn_per_profile_at_a_time(self):
        """Two mentions of the same profile never run concurrently.

        Separate mention ids are separate dedupe keys, so dedupe does not cover
        them; two concurrent turns read the room before either answers and both
        post under the same mention. The per-profile gate must serialize them.
        """
        log = os.path.join(self.tmp, "gate.log")
        stub = os.path.join(self.tmp, "gate-stub.sh")
        with open(stub, "w") as fh:
            fh.write("#!/bin/sh\n"
                     'echo start >> "$STUB_LOG"\n'
                     'sleep "${STUB_SLEEP:-0}"\n'
                     'echo end >> "$STUB_LOG"\n'
                     'echo "stub answer for $2"\n')
        os.chmod(stub, os.stat(stub).st_mode | stat.S_IEXEC)
        with bus(AGENT_CHAT_RATE_PER_MIN="600", AGENT_CHAT_RATE_BURST="1000") as (b2, _t, _s):
            with dispatcher(b2, WAKE_ALLOW="gamma", WAKE_BIN=stub,
                            WAKE_MAX_CONCURRENCY="2", WAKE_MAX_QUEUE="10",
                            WAKE_DEDUPE_TTL="60", STUB_LOG=log,
                            STUB_SLEEP="1.2") as (d2, _svc2):
                for i in (2001, 2002):
                    st, body = post(d2 + "/wake/gamma",
                                    {"from": "t", "topic": "wake",
                                     "text": f"@gamma job {i}", "message_id": i})
                    self.assertEqual(st, 200, body)
                got = wait_for(lambda: os.path.exists(log) and
                               open(log).read().count("end") >= 2, timeout=15)
                self.assertTrue(got, "the two turns never finished")
                depth, peak = 0, 0
                for line in open(log).read().split():
                    depth += 1 if line == "start" else -1
                    peak = max(peak, depth)
                self.assertEqual(peak, 1, f"two turns for gamma overlapped: {open(log).read()!r}")

    def test_queue_full_returns_429(self):
        """One worker, one queue slot, slow agent: the third mention is refused."""
        with bus(AGENT_CHAT_RATE_PER_MIN="600", AGENT_CHAT_RATE_BURST="1000") as (b2, _t, _s):
            count = os.path.join(self.tmp, "slow.txt")
            with dispatcher(b2, WAKE_ALLOW="slow", WAKE_BIN=self.stub,
                            WAKE_MAX_CONCURRENCY="1", WAKE_MAX_QUEUE="1",
                            STUB_COUNT=count, STUB_SLEEP="2") as (d2, _svc2):
                codes = []
                for i in range(4):
                    st, _ = post(d2 + "/wake/slow",
                                 {"from": "t", "topic": "wake",
                                  "text": f"@slow job {i}", "message_id": 1000 + i})
                    codes.append(st)
                    time.sleep(0.25)
                self.assertIn(429, codes, f"expected backpressure, got {codes}")
                self.assertEqual(codes[0], 200)


if __name__ == "__main__":
    unittest.main()
