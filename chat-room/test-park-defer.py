#!/usr/bin/env python3
"""Park test: a deferred record must not re-enter the work queue while it is busy.

The defect this pins: `_enqueue` succeeds whenever `qsize < MAX_QUEUE`, so the
three deferral paths (local gate held, flock held by an orphan turn, gateway
reports a live turn) put the record straight back where another worker took it
within milliseconds and deferred it again. Two workers then spent their whole
window on one already-busy profile, and at 20/20 the pump re-added a held record
on a 1 s tick. A deferred record must be PARKED: off the queue, with a
not-before deadline, re-injected only after the gateway answers idle.

Two cases, both against stubs — nothing here touches the real room:

  A. one wake for a profile whose gateway is busy for ~10 s. Assert: nothing
     spawns, the record is not on the queue (queued==0, parked>=1) for the whole
     busy window, the gateway is probed at a backoff rate not a spin rate, and
     the record runs EXACTLY ONCE with one reply when the gateway goes idle.
  B. 24 wakes at WAKE_MAX_QUEUE=2 with the same busy gateway: every record is
     accounted for (queued + held + parked + in flight == 24) while it is busy,
     and after idle all 24 run once each, spilled drains to 0 and the partition
     stays `ok`.

Run: /usr/bin/python3 test-park-defer.py
"""
import hashlib
import hmac as hm
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DISPATCHER = os.path.join(HERE, "wake_dispatcher.py")
SECRET = "test-secret"


class StubGateway:
    """Answers the `active-work` control verb; busy until `until`."""

    def __init__(self, path, busy_for):
        self.path = path
        self.busy_until = time.time() + busy_for
        self.calls = []
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(path)
        self.srv.listen(8)
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            with conn:
                line = b""
                while b"\n" not in line:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    line += chunk
                try:
                    req = json.loads(line.decode() or "{}")
                except ValueError:
                    req = {}
                profile = (req.get("params") or {}).get("profile")
                busy = time.time() < self.busy_until
                self.calls.append((time.time(), profile, busy))
                units = ([{"kind": "chat", "session": f"agent:{profile}:local:dm:stub",
                           "pid": os.getpid(), "elapsed_s": 12.0}] if busy else [])
                conn.sendall(json.dumps({"ok": True, "id": req.get("id"),
                                         "result": {"active_agents": len(units),
                                                    "active_work": units,
                                                    "profile": profile,
                                                    "pid": os.getpid()}}).encode() + b"\n")


class StubBus(BaseHTTPRequestHandler):
    sent = []

    def do_GET(self):
        self._json([])

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n)
        want = hm.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
        if self.headers.get("X-Signature") != want:
            self._json({"error": "bad signature"}, 401)
            return
        body = json.loads(raw or b"{}")
        StubBus.sent.append(body)
        self._json({"id": 900 + len(StubBus.sent)})

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_dispatcher(root, bus_url, port, gateway_home, max_queue=20):
    fake_bin = os.path.join(root, "fake-hermes")
    calls = os.path.join(root, "spawns.log")
    with open(fake_bin, "w") as fh:
        fh.write("#!/bin/sh\n"
                 f"printf '%s\\n' invoked >> {calls}\n"
                 "echo 'stub reply for the wake'\n")
    os.chmod(fake_bin, 0o755)
    env = dict(os.environ,
               AGENT_CHAT_SECRET=SECRET, AGENT_CHAT_URL=bus_url,
               WAKE_BIN=fake_bin, WAKE_BIND="127.0.0.1", WAKE_PORT=str(port),
               WAKE_ALLOW="stubprofile", WAKE_MAX_CONCURRENCY="2",
               WAKE_MAX_QUEUE=str(max_queue),
               WAKE_TIMEOUT="60", WAKE_SPILL=os.path.join(root, "spill.jsonl"),
               WAKE_LOCK_DIR=os.path.join(root, "locks"),
               WAKE_GATEWAY_HOME=gateway_home,
               WAKE_PARK_DELAY="3", WAKE_PARK_DELAY_MAX="12")
    log = open(os.path.join(root, "dispatcher.log"), "w")
    proc = subprocess.Popen([sys.executable, DISPATCHER], env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    return proc, calls, log


def wake(port, mid):
    body = json.dumps({"from": "buzzy", "topic": "work",
                       "text": "park test", "message_id": mid}).encode()
    sig = hm.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/wake/stubprofile", data=body,
                                 headers={"X-Signature": sig,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def health(port):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
        return json.loads(r.read())


def spawn_count(path):
    try:
        with open(path) as fh:
            return len([ln for ln in fh.read().splitlines() if ln.strip()])
    except FileNotFoundError:
        return 0


def wait_health(port):
    for _ in range(60):
        try:
            return health(port)
        except Exception:
            time.sleep(0.2)
    raise AssertionError("dispatcher never answered /health")


def boot(root, busy_for, max_queue=20):
    home = os.path.join(root, "home")
    os.makedirs(home)
    gw = StubGateway(os.path.join(home, "gateway.sock"), busy_for=busy_for)
    bus_port, port = free_port(), free_port()
    bus = ThreadingHTTPServer(("127.0.0.1", bus_port), StubBus)
    threading.Thread(target=bus.serve_forever, daemon=True).start()
    proc, calls, log = start_dispatcher(root, f"http://127.0.0.1:{bus_port}", port,
                                        home, max_queue=max_queue)
    wait_health(port)
    return gw, bus, proc, calls, log, port


def case_a():
    StubBus.sent = []
    root = tempfile.mkdtemp(prefix="park-a-")
    gw, bus, proc, calls, log, port = boot(root, busy_for=10.0)
    try:
        wake(port, 5201)
        off_queue = parked_seen = samples = 0
        deadline = time.time() + 9.0
        while time.time() < deadline:
            if spawn_count(calls):
                raise AssertionError("spawned while the gateway reported a live turn")
            h = health(port)
            if h["partition"]["ok"] is False:
                raise AssertionError(f"partition broke while parked: {h['partition']}")
            samples += 1
            # the record must be OFF the queue: no worker may pick it up again
            if h["queued"] == 0:
                off_queue += 1
            if h["parked"] >= 1:
                parked_seen += 1
            time.sleep(0.25)
        if off_queue < int(samples * 0.9):
            raise AssertionError(
                f"record re-entered the queue while its profile was busy: "
                f"queued==0 in only {off_queue}/{samples} samples")
        if parked_seen < int(samples * 0.9):
            raise AssertionError(
                f"record was not parked: parked>=1 in only {parked_seen}/{samples} samples")
        probes = len(gw.calls)
        # Measured with WAKE_PARK_DELAY=3 on the same stub: parked code probes at
        # 3 s and 9 s (2 in 8 s); the pre-park build spent 6 in 8 s because both
        # workers cycled the same record through the queue. The bound is the spin
        # detector, not a tuning target.
        if probes > 5:
            raise AssertionError(f"gateway probed {probes} times in 9 s: that is a spin")
        deadline = time.time() + 40.0
        while time.time() < deadline and spawn_count(calls) < 1:
            time.sleep(0.3)
        time.sleep(1.5)     # give a duplicate a chance to show up
        if spawn_count(calls) != 1:
            raise AssertionError(f"expected exactly 1 spawn after idle, got {spawn_count(calls)}")
        if len(StubBus.sent) != 1:
            raise AssertionError(f"expected exactly 1 reply, got {len(StubBus.sent)}")
        h = health(port)
        if h["parked"] != 0 or h["spilled"] != 0:
            raise AssertionError(f"park/spill did not drain: parked={h['parked']} "
                                 f"spilled={h['spilled']}")
        if not h["partition"]["ok"]:
            raise AssertionError(f"partition not ok at rest: {h['partition']}")
        print(f"A ok: parked off-queue ({off_queue}/{samples} samples queued==0, "
              f"{parked_seen}/{samples} parked>=1), {probes} gateway probes in 9 s, "
              f"then exactly 1 spawn and 1 reply")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        bus.shutdown()
        gw.srv.close()
        log.close()
        with open(os.path.join(root, "dispatcher.log")) as fh:
            body = fh.read()
        for evt in ("wake_parked", "park_requeue", "wake"):
            if evt not in body:
                print(f"A: missing event {evt} in dispatcher log\n{body[-2000:]}")
        shutil.rmtree(root, ignore_errors=True)


def case_b():
    StubBus.sent = []
    root = tempfile.mkdtemp(prefix="park-b-")
    gw, bus, proc, calls, log, port = boot(root, busy_for=12.0, max_queue=2)
    mids = list(range(5301, 5325))
    try:
        for mid in mids:
            wake(port, mid)
        deadline = time.time() + 10.0
        accounted_bad = None
        while time.time() < deadline:
            if spawn_count(calls):
                raise AssertionError("spawned while the gateway reported a live turn")
            h = health(port)
            p = h["partition"]
            accounted = p["on_queue_set"] + p["held"] + p["parked"] + p["in_workers"]
            if accounted != len(mids):
                accounted_bad = (accounted, p)
                break
            if not p["ok"]:
                raise AssertionError(f"partition not ok under pressure: {p}")
            time.sleep(0.3)
        if accounted_bad:
            raise AssertionError(
                f"records lost or double-counted while parked: {accounted_bad[0]} "
                f"of {len(mids)} accounted, partition {accounted_bad[1]}")
        deadline = time.time() + 90.0
        while time.time() < deadline and spawn_count(calls) < len(mids):
            time.sleep(0.3)
        time.sleep(2.0)     # a duplicate would land here
        if spawn_count(calls) != len(mids):
            raise AssertionError(f"expected {len(mids)} spawns, got {spawn_count(calls)}")
        answered = sorted(b.get("reply_to") for b in StubBus.sent)
        if answered != mids:
            raise AssertionError(f"replies do not match the mentions: "
                                 f"{len(answered)} for {len(mids)} ids, dupes="
                                 f"{len(answered) - len(set(answered))}")
        h = health(port)
        if h["spilled"] != 0 or h["parked"] != 0 or h["held_for_repump"] != 0:
            raise AssertionError(f"did not drain: spilled={h['spilled']} "
                                 f"parked={h['parked']} held={h['held_for_repump']}")
        if not h["partition"]["ok"]:
            raise AssertionError(f"partition not ok at rest: {h['partition']}")
        print(f"B ok: 24 records accounted for throughout the busy window "
              f"({len(gw.calls)} gateway probes), then {len(StubBus.sent)} replies, "
              f"one per mention, spill drained")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        bus.shutdown()
        gw.srv.close()
        log.close()
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    case_a()
    case_b()
    print("PASS")
