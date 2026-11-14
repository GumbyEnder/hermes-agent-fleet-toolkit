#!/usr/bin/env python3
"""Live probe: the gate must defer to the REAL gateway's answer, not to a stub.

Runs a throwaway dispatcher (own port, own spill/lock dirs, fake WAKE_BIN) against
the live gateway socket, and pushes a signed wake for a profile that is genuinely
mid-turn. Expectation: gate_held_busy with the gateway's own pid and session, and
zero spawns. Nothing is posted to the room: the stub bus discards, and the record
never reaches a turn.
"""
import importlib.util
import json
import os
import tempfile
import threading
import time
import shutil
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("tgb", os.path.join(HERE, "test-gate-busy.py"))
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)

PROFILE = os.environ.get("PROBE_PROFILE", "game")
SECRET = ""
for line in open(os.path.expanduser("~/.config/hermes-fleet/env")):
    if line.startswith("AGENT_CHAT_SECRET="):
        SECRET = line.split("=", 1)[1].strip().strip('"')
t.SECRET = SECRET

root = tempfile.mkdtemp(prefix="gate-live-")
bp, port = t.free_port(), t.free_port()
bus = ThreadingHTTPServer(("127.0.0.1", bp), t.StubBus)
threading.Thread(target=bus.serve_forever, daemon=True).start()
import subprocess, sys
fake_bin = os.path.join(root, "fake-hermes")
calls = os.path.join(root, "spawns.log")
with open(fake_bin, "w") as fh:
    fh.write(f"#!/bin/sh\nprintf '%s\\n' invoked >> {calls}\necho reply\n")
os.chmod(fake_bin, 0o755)
env = dict(os.environ, AGENT_CHAT_SECRET=SECRET, AGENT_CHAT_URL=f"http://127.0.0.1:{bp}",
           WAKE_BIN=fake_bin, WAKE_BIND="127.0.0.1", WAKE_PORT=str(port),
           WAKE_ALLOW=PROFILE, WAKE_TIMEOUT="60",
           WAKE_SPILL=os.path.join(root, "spill.jsonl"),
           WAKE_LOCK_DIR=os.path.join(root, "locks"),
           WAKE_GATEWAY_HOME=os.path.expanduser("~/.hermes"))
log = open(os.path.join(root, "dispatcher.log"), "w")
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "wake_dispatcher.py")],
                        env=env, stdout=log, stderr=subprocess.STDOUT)
try:
    for _ in range(50):
        try:
            t.health(port); break
        except Exception:
            time.sleep(0.2)
    # the guard in wake() checks the allow-list path; it posts to /wake/<profile>
    import urllib.request, hashlib, hmac as hm
    body = json.dumps({"from": "buzzy", "topic": "work", "text": "live gate probe",
                       "message_id": 999999}).encode()
    sig = hm.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/wake/{PROFILE}", data=body,
                                 headers={"X-Signature": sig, "Content-Type": "application/json"})
    print("push:", json.loads(urllib.request.urlopen(req, timeout=10).read()))
    time.sleep(5)
    print("spawns:", t.spawn_count(calls))
    print("health:", {k: t.health(port)[k] for k in ("gate_busy", "queued", "active", "spilled")})
    proc.terminate(); proc.wait(timeout=10); log.close()
    for line in open(os.path.join(root, "dispatcher.log")):
        rec = json.loads(line)
        if rec.get("evt") in {"gate_held_busy", "gate_unverified", "gate_held", "wake"}:
            print(json.dumps(rec, sort_keys=True))
finally:
    with __import__("contextlib").suppress(Exception):
        proc.terminate(); proc.wait(timeout=10)
    bus.shutdown(); log.close()
    shutil.rmtree(root, ignore_errors=True)
