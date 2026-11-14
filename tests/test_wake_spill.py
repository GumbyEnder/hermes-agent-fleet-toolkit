"""Dispatcher spill: the queue survives SIGTERM, and a restart does not double-post.

The queue lives in the process, so before the spill every accepted-but-unfinished
wake died with a restart — and the bus had already been told 200, so nothing
retried it. These cases pin the three behaviours that make a restart safe:

  A. an accepted wake is on disk until its turn finishes, then it is gone;
  B. a SIGTERM leaves the pending records on disk and the next boot re-runs them;
  C. a record whose mention already has a reply from that profile is dropped at
     boot instead of posting a second answer under the same mention.

Never call Service.output() while the service is running: it reads the live
stdout pipe and blocks until exit, so a diagnostic built eagerly into an assert
message hangs the test instead of reporting it.
"""
import importlib.util
import json
import os
import stat
import tempfile
import time
import unittest

from support import ROOT, Service, bus, free_port, get_json, post

_spec = importlib.util.spec_from_file_location(
    "derive_wake_spill", ROOT / "chat-room/derive_wake_spill.py")
assert _spec is not None and _spec.loader is not None, "cannot load derive_wake_spill"
derive_wake_spill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(derive_wake_spill)

SLOW_STUB = """#!/bin/sh
# args: -p <profile> -z <prompt>
echo "start $2" >> "$STUB_COUNT"
sleep "${STUB_SLEEP:-0}"
echo "stub answer for $2"
"""


def make_stub(tmp, sleep_s):
    p = os.path.join(tmp, "spill-stub.sh")
    with open(p, "w") as fh:
        fh.write(SLOW_STUB)
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
    return p


def wait_for(fn, timeout=15.0, step=0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return None


def spill_lines(path):
    try:
        with open(path) as fh:
            return [ln for ln in fh.read().splitlines() if ln.strip()]
    except FileNotFoundError:
        return []


class SpillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fleet-spilltest-")
        self.spill = os.path.join(self.tmp, "wake-spill.jsonl")
        self.count = os.path.join(self.tmp, "count.txt")
        self.stub = make_stub(self.tmp, 30)

    def _env(self, bus_url, port, **over):
        env = dict(os.environ)
        env.pop("WAKE_ALLOW", None)
        env.update({"AGENT_CHAT_SECRET": "test-secret-not-real",
                    "PYTHONUNBUFFERED": "1",
                    "AGENT_CHAT_URL": bus_url, "WAKE_PORT": str(port),
                    "WAKE_ALLOW": "alpha", "WAKE_BIN": self.stub,
                    "WAKE_MAX_CONCURRENCY": "1", "WAKE_MAX_QUEUE": "10",
                    "WAKE_DEDUPE_TTL": "60",
                    "WAKE_SPILL": self.spill, "STUB_COUNT": self.count,
                    "STUB_SLEEP": "30"})
        env.update(over)
        return env

    def _launch(self, bus_url, port, **over):
        return Service(str(ROOT / "chat-room/wake_dispatcher.py"),
                       self._env(bus_url, port, **over), f"http://127.0.0.1:{port}/health")

    def test_spill_record_lives_until_the_turn_finishes(self):
        """A: accepted -> on disk; finished -> dropped."""
        with bus(AGENT_CHAT_RATE_PER_MIN="600", AGENT_CHAT_RATE_BURST="1000") as (b, _t, _s):
            port = free_port()
            with self._launch(b, port, STUB_SLEEP="3") as svc:
                url = f"http://127.0.0.1:{port}"
                st, body = post(url + "/wake/alpha",
                                {"from": "t", "topic": "wake", "text": "@alpha job",
                                 "message_id": 5001})
                self.assertEqual(st, 200, body)
                got = wait_for(lambda: len(spill_lines(self.spill)) == 1)
                self.assertTrue(got, f"no spill record 15s after accept: "
                                     f"{spill_lines(self.spill)!r}")
                rec = json.loads(spill_lines(self.spill)[0])
                self.assertEqual(rec["profile"], "alpha")
                self.assertEqual(rec["payload"]["message_id"], 5001)
                # the turn is still running (stub sleeps 3 s): record must remain
                time.sleep(1.0)
                self.assertEqual(len(spill_lines(self.spill)), 1)
                got = wait_for(lambda: spill_lines(self.spill) == [], timeout=20)
                self.assertTrue(got, f"record never dropped: {spill_lines(self.spill)!r}")

    def test_sigterm_persists_queue_and_next_boot_resumes_it(self):
        """B: SIGTERM while busy -> records on disk -> new process re-runs them."""
        with bus(AGENT_CHAT_RATE_PER_MIN="600", AGENT_CHAT_RATE_BURST="1000") as (b, _t, _s):
            port = free_port()
            first = self._launch(b, port)
            first.__enter__()
            try:
                url = f"http://127.0.0.1:{port}"
                for mid in (6001, 6002):
                    st, body = post(url + "/wake/alpha",
                                    {"from": "t", "topic": "wake",
                                     "text": f"@alpha job {mid}", "message_id": mid})
                    self.assertEqual(st, 200, body)
                self.assertTrue(wait_for(lambda: len(spill_lines(self.spill)) == 2),
                                f"spill: {spill_lines(self.spill)!r}")
                first.proc.terminate()
                first.proc.wait(timeout=23)
                self.assertIn(first.proc.returncode, (0, -15, 143))
            finally:
                first.__exit__(None, None, None)
            left = spill_lines(self.spill)
            self.assertEqual(len(left), 2,
                             f"SIGTERM lost queued wakes: {left!r}")
            ids = sorted(json.loads(x)["payload"]["message_id"] for x in left)
            self.assertEqual(ids, [6001, 6002])
            # boot the same spill file on the same bus; short turns so the
            # resumed queue drains inside the test's patience
            second = self._launch(b, port, STUB_SLEEP="1")
            with second as svc:
                url = f"http://127.0.0.1:{port}"
                h = get_json(url + "/health")
                self.assertEqual(h["spilled"], 2, h)
                # one record is immediately claimed by the single worker
                self.assertEqual(h["queued"] + h["active"], 2, h)
                got = wait_for(lambda: get_json(url + "/health")["done"] >= 2,
                               timeout=40)
                self.assertTrue(got, f"resumed turns never finished: "
                                     f"{get_json(url + '/health')}")
                got = wait_for(lambda: spill_lines(self.spill) == [], timeout=10)
                self.assertTrue(got, f"spill not drained: {spill_lines(self.spill)!r}")
            ran = 0
            if os.path.exists(self.count):
                with open(self.count) as fh:
                    ran = fh.read().count("start")
            self.assertGreaterEqual(ran, 2, f"resumed records never spawned a turn ({ran})")

    def test_record_with_existing_reply_is_not_re_run(self):
        """C: a turn killed after posting must not double-post on resume."""
        with bus(AGENT_CHAT_RATE_PER_MIN="600", AGENT_CHAT_RATE_BURST="1000") as (b, _t, _s):
            mid = json.loads(post(b + "/send", {"from": "t", "topic": "wake",
                                                "text": "@alpha answered already"})[1])["id"]
            st, body = post(b + "/send", {"from": "alpha", "topic": "wake",
                                          "text": "(re: @mention) already answered",
                                          "reply_to": mid})
            self.assertEqual(st, 200, body)
            with open(self.spill, "w") as fh:
                fh.write(json.dumps({"seq": 1, "profile": "alpha",
                                     "payload": {"from": "t", "topic": "wake",
                                                 "text": "@alpha answered already",
                                                 "message_id": mid}}) + "\n")
            port = free_port()
            with self._launch(b, port) as svc:
                url = f"http://127.0.0.1:{port}"
                self.assertTrue(wait_for(lambda: spill_lines(self.spill) == [],
                                         timeout=20),
                                f"answered record not dropped: {spill_lines(self.spill)!r}")
                time.sleep(0.5)
                h = get_json(url + "/health")
                self.assertEqual(h["done"], 0, h)
            self.assertFalse(os.path.exists(self.count),
                             "a turn was spawned for an already-answered mention")

    def test_fresh_wake_for_an_answered_mention_runs_no_turn(self):
        """D: the answered-check is not startup-only; a re-delivered mention is a no-op.

        Live case: 4148 woke twice (4173, 4188), 4073 twice (4136, 4204). Both
        were re-deliveries inside one process, which the boot-time check never saw.
        """
        with bus(AGENT_CHAT_RATE_PER_MIN="600", AGENT_CHAT_RATE_BURST="1000") as (b, _t, _s):
            mid = json.loads(post(b + "/send", {"from": "t", "topic": "wake",
                                                "text": "@alpha second delivery"})[1])["id"]
            st, body = post(b + "/send", {"from": "alpha", "topic": "wake",
                                          "text": "(re: @mention) first delivery",
                                          "reply_to": mid})
            self.assertEqual(st, 200, body)
            port = free_port()
            with self._launch(b, port) as svc:
                url = f"http://127.0.0.1:{port}"
                st, body = post(url + "/wake/alpha",
                                {"from": "t", "topic": "wake", "text": "@alpha second delivery",
                                 "message_id": mid})
                self.assertEqual(st, 200, body)
                got = wait_for(lambda: get_json(url + "/health")["skipped_answered"] == 1)
                self.assertTrue(got, f"no skip: {get_json(url + '/health')}")
                self.assertTrue(wait_for(lambda: spill_lines(self.spill) == [], timeout=10),
                                f"spill not drained: {spill_lines(self.spill)!r}")
                h = get_json(url + "/health")
                self.assertEqual(h["done"], 0, h)
                self.assertNotIn("overflow", h, "health still reports the ambiguous name")
                self.assertIn("held_for_repump", h, h)
            self.assertFalse(os.path.exists(self.count),
                             "a turn was spawned for an already-answered mention")

    def test_a_killed_turn_is_re_spilled_and_abandoned_at_the_cap(self):
        """E: a turn killed at the HARD cap is re-spilled, then abandoned at the cap.

        Live case: 4164 ran 900.1 s against a 900 s budget, rc null, posted 0, and
        was dropped from the spill even though the orphan child posted #4200
        eleven seconds later. A killed turn is not a finished turn.

        The kill point is now the hard cap, not the budget: the budget only
        reports (see test_wake_budget.py — killing the client at the budget
        released the gate while the gateway turn ran on). The stub sleeps 120 s
        behind a 3 s hard cap, so this also pins that the kill reaches the whole
        process group: otherwise communicate() waits on the grandchild's pipe.
        """
        with bus(AGENT_CHAT_RATE_PER_MIN="600", AGENT_CHAT_RATE_BURST="1000") as (b, _t, _s):
            port = free_port()
            started = time.time()
            with self._launch(b, port, STUB_SLEEP="120", WAKE_TIMEOUT="1",
                              WAKE_HARD_CAP="3", WAKE_RETRY_MAX="2") as svc:
                url = f"http://127.0.0.1:{port}"
                st, body = post(url + "/wake/alpha",
                                {"from": "t", "topic": "wake", "text": "@alpha dies",
                                 "message_id": 7001})
                self.assertEqual(st, 200, body)
                got = wait_for(lambda: get_json(url + "/health")["retried"] >= 1, timeout=25)
                self.assertTrue(got, f"no retry after a dead turn: {get_json(url + '/health')}")
                got = wait_for(lambda: get_json(url + "/health")["abandoned"] == 1, timeout=40)
                self.assertTrue(got, f"never abandoned at the cap: {get_json(url + '/health')}")
                h = get_json(url + "/health")
                self.assertEqual(h["retried"], 2, h)      # attempt 1 and attempt 2
                self.assertTrue(wait_for(lambda: spill_lines(self.spill) == [], timeout=10),
                                f"abandoned record still on disk: {spill_lines(self.spill)!r}")
            starts = 0
            if os.path.exists(self.count):
                with open(self.count) as fh:
                    starts = fh.read().count("start")
            self.assertEqual(starts, 3, f"expected 3 spawns (1 + 2 retries), got {starts}")
            self.assertLess(time.time() - started, 60,
                            "the wrapper blocked on the grandchild's pipe: the hard-cap "
                            "kill did not reach the process group")

    def test_a_turn_killed_by_a_stop_is_re_spilled(self):
        """G: rc=-15 with no reply is not a finished turn either.

        Live: the 00:16:37 restart killed the two active wrappers — buzzy/4205 and
        game/4203 both logged rc=-15, posted 0 — and the old code dropped both
        records. A stop is the one kill the dispatcher causes itself.
        """
        with bus(AGENT_CHAT_RATE_PER_MIN="600", AGENT_CHAT_RATE_BURST="1000") as (b, _t, _s):
            port = free_port()
            stub = os.path.join(self.tmp, "suicide-stub.sh")
            with open(stub, "w") as fh:
                fh.write("#!/bin/sh\necho start >> \"$STUB_COUNT\"\nkill -TERM $$\n")
            os.chmod(stub, os.stat(stub).st_mode | stat.S_IEXEC)
            with self._launch(b, port, WAKE_BIN=stub, WAKE_RETRY_MAX="1") as svc:
                url = f"http://127.0.0.1:{port}"
                st, body = post(url + "/wake/alpha",
                                {"from": "t", "topic": "wake", "text": "@alpha dies",
                                 "message_id": 7200})
                self.assertEqual(st, 200, body)
                got = wait_for(lambda: get_json(url + "/health")["retried"] >= 1, timeout=20)
                self.assertTrue(got, f"rc=-15 was treated as finished: {get_json(url + '/health')}")
                got = wait_for(lambda: get_json(url + "/health")["abandoned"] == 1, timeout=20)
                self.assertTrue(got, f"never abandoned at the cap: {get_json(url + '/health')}")

    def test_duplicate_records_on_disk_collapse_to_one_turn(self):
        """F: the spill file is the durable duplicate key, not the 600 s TTL.

        The live spill carried buzzy/4205, buzzy/4212 and buzzy/4220 twice each.
        """
        with bus(AGENT_CHAT_RATE_PER_MIN="600", AGENT_CHAT_RATE_BURST="1000") as (b, _t, _s):
            with open(self.spill, "w") as fh:
                for seq in (1, 2):
                    fh.write(json.dumps({"seq": seq, "profile": "alpha",
                                         "payload": {"from": "t", "topic": "wake",
                                                     "text": "@alpha twice",
                                                     "message_id": 7100}}) + "\n")
            port = free_port()
            with self._launch(b, port, STUB_SLEEP="1") as svc:
                url = f"http://127.0.0.1:{port}"
                got = wait_for(lambda: get_json(url + "/health")["done"] >= 1, timeout=25)
                self.assertTrue(got, f"the surviving record never ran: {get_json(url + '/health')}")
                self.assertTrue(wait_for(lambda: spill_lines(self.spill) == [], timeout=10),
                                f"spill not drained: {spill_lines(self.spill)!r}")
                time.sleep(1.0)
            starts = 0
            if os.path.exists(self.count):
                with open(self.count) as fh:
                    starts = fh.read().count("start")
            self.assertEqual(starts, 1, f"duplicate key ran {starts} turns")


class DeriveTests(unittest.TestCase):
    """The cutover tool's one rule: unanswered mention == accepted-but-unfinished wake."""

    def _msg(self, mid, sender, text, ts, parent=None):
        return {"id": mid, "ts": ts, "sender": sender, "topic": "work",
                "text": text, "kind": "message", "parent_id": parent}

    def test_drops_answered_old_and_self_mentions(self):
        now = 10_000.0
        msgs = [
            self._msg(1, "alice", "@beta please", now - 60),          # unanswered -> keep
            self._msg(2, "beta", "(re: @mention) done", now - 50, 1),  # answers 1
            self._msg(3, "alice", "@beta again two", now - 40),       # unanswered, parent below
            self._msg(4, "beta", "(re: @mention) done", now - 30, 3),
            self._msg(5, "beta", "@beta self mention", now - 20),     # sender == profile
            self._msg(6, "alice", "@beta stale", now - 5000),         # outside the window
            self._msg(7, "alice", "@beta live one", now - 10),        # unanswered -> keep
        ]
        got = derive_wake_spill.derive(msgs, ["beta"], 60)
        self.assertEqual([r["payload"]["message_id"] for r in got], [7])

    def test_a_child_from_another_agent_does_not_count_as_answered(self):
        now = 10_000.0
        msgs = [
            self._msg(1, "alice", "@beta and @gamma please", now - 60),
            self._msg(2, "gamma", "(re: @mention) mine", now - 50, 1),
        ]
        got = derive_wake_spill.derive(msgs, ["beta", "gamma"], 60)
        self.assertEqual([(r["profile"], r["payload"]["message_id"]) for r in got],
                         [("beta", 1)])

    def test_exclude_keeps_a_mention_the_caller_is_already_answering(self):
        now = 10_000.0
        msgs = [self._msg(9, "alice", "@beta one", now - 5)]
        self.assertEqual(derive_wake_spill.derive(msgs, ["beta"], 60, {9}), [])


if __name__ == "__main__":
    unittest.main()
