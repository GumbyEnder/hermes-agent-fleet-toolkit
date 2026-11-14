#!/usr/bin/env python3
"""Gate test: a wake must not spawn a rival turn while the gateway says one is running.

Two cases, both against a stub — nothing here touches the real room:

  A. gateway reports an active turn for the profile for ~4 s: the dispatcher must
     defer (gate_held_busy) and spawn NOTHING; once the gateway answers idle the
     same record must run exactly once.
  B. no gateway socket at all: the gate is the flock alone (gate_unverified) and
     the record still runs — fail open, as before.

Run: /usr/bin/python3 test-gate-busy.py
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


def start_dispatcher(root, bus_url, port, gateway_home):
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
               WAKE_TIMEOUT="60", WAKE_SPILL=os.path.join(root, "spill.jsonl"),
               WAKE_LOCK_DIR=os.path.join(root, "locks"),
               WAKE_GATEWAY_HOME=gateway_home)
    log = open(os.path.join(root, "dispatcher.log"), "w")
    proc = subprocess.Popen([sys.executable, DISPATCHER], env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    return proc, calls, log


def wake(port, mid):
    body = json.dumps({"from": "buzzy", "topic": "work",
                       "text": "gate test", "message_id": mid}).encode()
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


def case_a():
    root = tempfile.mkdtemp(prefix="gate-a-")
    home = os.path.join(root, "home")
    os.makedirs(home)
    gw = StubGateway(os.path.join(home, "gateway.sock"), busy_for=4.0)
    bus_port, port = free_port(), free_port()
    bus = ThreadingHTTPServer(("127.0.0.1", bus_port), StubBus)
    threading.Thread(target=bus.serve_forever, daemon=True).start()
    proc, calls, log = start_dispatcher(root, f"http://127.0.0.1:{bus_port}", port, home)
    try:
        for _ in range(50):
            try:
                health(port)
                break
            except Exception:
                time.sleep(0.2)
        wake(port, 4205)
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if spawn_count(calls):
                raise AssertionError("spawned while the gateway reported a live turn")
            time.sleep(0.2)
        h = health(port)
        if h["gate_busy"] < 1:
            raise AssertionError(f"no gate_held_busy in health: {h}")
        # busy window over: the same record must now run, exactly once
        deadline = time.time() + 20.0
        while time.time() < deadline and spawn_count(calls) < 1:
            time.sleep(0.3)
        if spawn_count(calls) != 1:
            raise AssertionError(f"expected exactly 1 spawn after idle, got {spawn_count(calls)}")
        if not StubBus.sent:
            raise AssertionError("the deferred record never posted its reply")
        print(f"A ok: deferred while busy ({h['gate_busy']} checks), then 1 spawn "
              f"({len(gw.calls)} control calls), 1 reply posted")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        bus.shutdown()
        gw.srv.close()
        log.close()
        with open(os.path.join(root, "dispatcher.log")) as fh:
            body = fh.read()
        for evt in ("gate_held_busy", "wake"):
            if evt not in body:
                print(f"A: missing event {evt} in dispatcher log\n{body[-2000:]}")
        shutil.rmtree(root, ignore_errors=True)


def case_b():
    root = tempfile.mkdtemp(prefix="gate-b-")
    home = os.path.join(root, "home")
    os.makedirs(home)   # no gateway.sock anywhere
    bus_port, port = free_port(), free_port()
    bus = ThreadingHTTPServer(("127.0.0.1", bus_port), StubBus)
    threading.Thread(target=bus.serve_forever, daemon=True).start()
    proc, calls, log = start_dispatcher(root, f"http://127.0.0.1:{bus_port}", port, home)
    try:
        for _ in range(50):
            try:
                health(port)
                break
            except Exception:
                time.sleep(0.2)
        wake(port, 4212)
        deadline = time.time() + 20.0
        while time.time() < deadline and spawn_count(calls) < 1:
            time.sleep(0.3)
        if spawn_count(calls) != 1:
            raise AssertionError(f"fail-open spawn missing: {spawn_count(calls)}")
        with open(os.path.join(root, "dispatcher.log")) as fh:
            body = fh.read()
        if "gate_unverified" not in body:
            raise AssertionError("no gate_unverified event for an absent gateway")
        print("B ok: no gateway -> gate_unverified, still ran once")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        bus.shutdown()
        log.close()
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    case_a()
    case_b()
    print("PASS")
