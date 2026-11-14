#!/usr/bin/env python3
"""Queue test: one seq must never leave two copies on the work queue.

Both cases widen the window on purpose so the test does something on the first
run instead of depending on a lucky interleaving:

  A. two threads _enqueue the same seq at once, with put_nowait slowed to 50 ms
     -> exactly one copy on the queue and one marker in _inqueue. (Pre-fix this
     reproduced copies_in_queue=2 on every round.)
  B. the pump sees a seq that is already queued (a marker left over from a
     deferral) -> it drops the held duplicate instead of putting a second copy.

No service, no room: it imports the dispatcher as a module. Set
WAKE_DISPATCHER_PATH to run the same test against another build (that is how the
stamp is proven: the pre-fix blob must FAIL case A).

Run: /usr/bin/python3 test-enqueue-race.py
"""
import importlib.util
import os
import queue
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.environ.get("WAKE_DISPATCHER_PATH", os.path.join(HERE, "wake_dispatcher.py"))
ROUNDS = 20


def load(path):
    spec = importlib.util.spec_from_file_location("wake_dispatcher_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SlowQueue(queue.Queue):
    """put_nowait with a wide window, so a test-and-put race has time to happen."""

    def put_nowait(self, item):
        time.sleep(0.05)
        super().put_nowait(item)


def reset(mod):
    mod._work = SlowQueue(maxsize=mod.MAX_QUEUE)
    mod._inqueue = set()
    mod._overflow = []
    mod._pending = {}


def case_a(mod):
    bad = 0
    for rnd in range(ROUNDS):
        reset(mod)
        seq = ("stubprofile", 9000 + rnd)
        barrier = threading.Barrier(2)

        def go():
            barrier.wait()
            mod._enqueue("stubprofile", {"message_id": seq[1]}, seq)

        ts = [threading.Thread(target=go) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=10)
        copies = mod._work.qsize()
        markers = len(mod._inqueue)
        if copies != 1 or markers != 1:
            bad += 1
            print(f"  round {rnd}: copies_in_queue={copies} markers={markers}")
    if bad:
        raise AssertionError(f"case A: {bad}/{ROUNDS} rounds left duplicate copies")
    print(f"A ok: {ROUNDS} rounds x 2 concurrent _enqueue of one seq -> 1 copy each")


def case_b(mod):
    reset(mod)
    mod._work = queue.Queue(maxsize=mod.MAX_QUEUE)
    seq = ("stubprofile", 9101)
    mod._pending[seq] = {"profile": "stubprofile", "payload": {"message_id": 9101}}
    mod._overflow = [seq]
    mod._inqueue = {seq}          # already queued elsewhere: this marker is a dup
    threading.Thread(target=mod._pump_loop, daemon=True).start()
    deadline = time.time() + 5.0
    while time.time() < deadline and seq in mod._overflow:
        time.sleep(0.1)
    copies, markers, held = mod._work.qsize(), len(mod._inqueue), list(mod._overflow)
    # stop feeding the pump before asserting on a quiet global
    mod._overflow = []
    mod._pending = {}
    time.sleep(1.2)
    if copies != 0:
        raise AssertionError(f"case B: pump put a second copy (qsize={copies})")
    if seq in held:
        raise AssertionError("case B: pump never dropped the duplicate marker")
    if markers != 1:
        raise AssertionError(f"case B: marker count changed ({markers})")
    print("B ok: pump dropped the held duplicate, 0 copies queued, 1 marker")


if __name__ == "__main__":
    print(f"module under test: {PATH}")
    m = load(PATH)
    case_a(m)
    case_b(m)
    print("PASS")
