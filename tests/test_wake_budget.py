"""The budget reports a slow turn; it does not kill the gate that covers it.

`hermes -p P -z` is a *client* of hermes-gateway.service: it hands the prompt
over and waits for the answer. The per-profile flock reaches that client through
`pass_fds`, so the client's lifetime IS the gate's lifetime. Killing it at the
budget therefore released the gate while the gateway turn ran on, and the next
resumed record spawned a rival turn under the same mention — buzzy/4205 posted
4254 (00:23:38) and 4257 (00:31) from one mention.

These cases pin the new contract:

  A. a turn that runs past its budget is allowed to finish and post, once, with
     no retry — and its gate stays shut for the whole overshoot;
  B. the lock file names the *waiter*, not the dispatcher, so it can be checked;
The hard cap itself (a wedged client killed with its process group, then
re-spilled) is pinned by test_wake_spill.py case E.
"""
import fcntl
import json
import os
import stat
import tempfile
import time
import unittest

from support import ROOT, Service, bus, free_port, get_json, get, post

LATE_STUB = """#!/bin/sh
# args: -p <profile> -z <prompt>
echo "start $2" >> "$STUB_COUNT"
sleep "${STUB_SLEEP:-0}"
echo "late answer for $2"
"""


def wait_for(fn, timeout=15.0, step=0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return None


def lock_is_held(path):
    """True when some other process holds the flock on `path`."""
    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    finally:
        os.close(fd)
    return False


def spill_lines(path):
    try:
        with open(path) as fh:
            return [ln for ln in fh.read().splitlines() if ln.strip()]
    except FileNotFoundError:
        return []


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fleet-budgettest-")
        self.spill = os.path.join(self.tmp, "wake-spill.jsonl")
        self.count = os.path.join(self.tmp, "count.txt")
        self.locks = os.path.join(self.tmp, "locks")
        self.stub = os.path.join(self.tmp, "late-stub.sh")
        with open(self.stub, "w") as fh:
            fh.write(LATE_STUB)
        os.chmod(self.stub, os.stat(self.stub).st_mode | stat.S_IEXEC)

    def _launch(self, bus_url, port, **over):
        env = dict(os.environ)
        env.pop("WAKE_ALLOW", None)
        env.update({"AGENT_CHAT_SECRET": "test-secret-not-real",
                    "PYTHONUNBUFFERED": "1",
                    "AGENT_CHAT_URL": bus_url, "WAKE_PORT": str(port),
                    "WAKE_ALLOW": "alpha", "WAKE_BIN": self.stub,
                    "WAKE_MAX_CONCURRENCY": "1", "WAKE_MAX_QUEUE": "10",
                    "WAKE_DEDUPE_TTL": "60",
                    "WAKE_SPILL": self.spill, "WAKE_LOCK_DIR": self.locks,
                    "STUB_COUNT": self.count})
        env.update(over)
        return Service(str(ROOT / "chat-room/wake_dispatcher.py"), env,
                       f"http://127.0.0.1:{port}/health")

    def test_an_over_budget_turn_finishes_once_under_a_held_gate(self):
        """A+B: no kill at the budget, and the gate stays shut while it overshoots."""
        with bus(AGENT_CHAT_RATE_PER_MIN="600", AGENT_CHAT_RATE_BURST="1000") as (b, _t, _s):
            port = free_port()
            with self._launch(b, port, STUB_SLEEP="4", WAKE_TIMEOUT="1",
                              WAKE_HARD_CAP="60") as svc:
                url = f"http://127.0.0.1:{port}"
                # the mention must exist in the room: the reply is threaded under
                # it, and the bus rejects a reply_to that names no row
                mid = json.loads(post(b + "/send", {"from": "t", "topic": "wake",
                                                    "text": "@alpha slow"})[1])["id"]
                st, body = post(url + "/wake/alpha",
                                {"from": "t", "topic": "wake", "text": "@alpha slow",
                                 "message_id": mid})
                self.assertEqual(st, 200, body)
                self.assertTrue(wait_for(lambda: len(spill_lines(self.spill)) == 1),
                                "the wake was not spilled before its turn ran")
                lock = os.path.join(self.locks, "alpha.lock")
                # t = 2 s: the budget (1 s) is already spent, the stub runs on
                time.sleep(2.0)
                h = get_json(url + "/health")
                self.assertEqual(h["done"], 0, f"the turn was ended at its budget: {h}")
                self.assertEqual(h["retried"], 0, f"a slow turn was re-spilled: {h}")
                self.assertTrue(os.path.exists(lock), f"no lock file at {lock}")
                self.assertTrue(lock_is_held(lock),
                                "the gate opened while the turn was still working — "
                                "this is the 4205 duplicate window")
                with open(lock) as fh:
                    pid, profile, _ts = fh.read().split()
                self.assertEqual(profile, "alpha")
                with open(f"/proc/{pid}/cmdline", "rb") as fh:
                    cmdline = fh.read().decode("utf-8", "replace")
                self.assertIn("late-stub.sh", cmdline,
                              f"lock file names pid {pid}, which is not the waiter: "
                              f"{cmdline!r}")
                got = wait_for(lambda: get_json(url + "/health")["done"] == 1, timeout=25)
                self.assertTrue(got, f"the over-budget turn never finished: "
                                     f"{get_json(url + '/health')}")
                h = get_json(url + "/health")
                self.assertEqual(h["retried"], 0, h)
                self.assertEqual(h["failed"], 0, h)
                self.assertTrue(wait_for(lambda: spill_lines(self.spill) == [], timeout=10),
                                f"record not cleared: {spill_lines(self.spill)!r}")
            # the wake itself arrives out-of-band (POST /wake), so read the room
            # rather than a thread rooted at 8100
            bodies = [m["text"] for m in get_json(b + "/api/messages?limit=200")]
            self.assertEqual(sum("late answer for alpha" in t for t in bodies), 1,
                             f"expected exactly one reply under the mention, got {bodies!r}")
            with open(self.count) as fh:
                self.assertEqual(fh.read().count("start"), 1,
                                 "the mention was turned twice")

if __name__ == "__main__":
    unittest.main()
