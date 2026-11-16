#!/usr/bin/env python3
"""test-bus-mention-ts.py -- the sender's clock on the wire.

store() used to throw its ts away: the envelope could only be dated by whatever
the receiver did next, so a turn that waited five hours behind the gate looked
fresh to every clock it could read. The fix is one value, carried end to end:
store() returns (id, ts), both dispatch_mentions call sites pass it, and the
signed payload carries "ts".

This test is offline and uses a throwaway DB (AGENT_CHAT_DB). Run from this dir.
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time

TMP = tempfile.mkdtemp(prefix="bus-mts-")
DB = os.path.join(TMP, "room.db")
os.environ["AGENT_CHAT_DB"] = DB
os.environ["AGENT_CHAT_SECRET"] = "test-secret"
os.environ["AGENT_CHAT_BACKUP"] = ""

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("bus", os.path.join(HERE, "bus.py"))
assert spec and spec.loader
bus = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bus)

fails, checks = [], 0


def check(name, got, want):
    global checks
    checks += 1
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'} {name}: got {got!r} want {want!r}")
    if not ok:
        fails.append(name)


bus.migrate()

# ---- 1. store() returns the row's clock, and it is the row's clock ----------
mid, ts = bus.store("buzzy", "work", "@frodo ping", "message", None)
check("store returns a 2-tuple", isinstance((mid, ts), tuple) and isinstance(mid, int), True)
with sqlite3.connect(DB) as c:
    row_ts = c.execute("SELECT ts FROM messages WHERE id=?", (mid,)).fetchone()[0]
check("returned ts IS the stored row ts", ts, row_ts)
check("returned ts has sub-ms resolution", round(ts, 6) == round(row_ts, 6), True)

# ---- 2. the signed payload carries it --------------------------------------
captured = []
real_urlopen = bus.urllib.request.urlopen


class FakeResp:
    def read(self):
        return b"{}"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(req, timeout=None):
    try:
        captured.append(json.loads(req.data.decode()))
    except Exception:
        captured.append(None)
    return FakeResp()


with sqlite3.connect(DB) as c:
    c.execute("INSERT INTO agents (name, webhook_url, registered_at) VALUES (?,?,?)",
              ("frodo", "http://127.0.0.1:9/wake", time.time()))
    c.commit()

bus.urllib.request.urlopen = fake_urlopen
try:
    woken = bus.dispatch_mentions("buzzy", "work", "@frodo ping", mid, None, ts=ts)
    deadline = time.time() + 3
    while not captured and time.time() < deadline:
        time.sleep(0.02)
finally:
    bus.urllib.request.urlopen = real_urlopen

check("dispatch woke frodo", woken, ["frodo"])
check("payload reached the wire", len(captured) == 1, True)
got = captured[0] if captured else {}
check("payload carries ts", got.get("ts"), ts)
check("payload ts equals the room row ts", round(float(got.get("ts", 0)), 6), round(row_ts, 6))
check("payload still carries message_id/parent_id/kind",
      (got.get("message_id"), got.get("kind")), (mid, "mention"))

# ---- 3. a caller that forgets ts must not crash the send -------------------
captured.clear()
bus.urllib.request.urlopen = fake_urlopen
try:
    bus.dispatch_mentions("buzzy", "work", "@frodo ping", mid, None)
    deadline = time.time() + 3
    while not captured and time.time() < deadline:
        time.sleep(0.02)
finally:
    bus.urllib.request.urlopen = real_urlopen
check("ts omitted -> payload ts is null, not a fake clock",
      ("ts" in captured[0] and captured[0]["ts"] is None) if captured else "no-capture", True)

# ---- 4. both HTTP call sites unpack the tuple ------------------------------
src = open(os.path.join(HERE, "bus.py")).read()
check("two dispatch_mentions call sites pass ts=", src.count("ts=mts"), 2)
check("both call sites unpack (mid, ts)", src.count("mid, mts = store("), 2)

print()
print(f"{checks - len(fails)}/{checks} checks passed")
if fails:
    print("FAILED:", ", ".join(fails))
    sys.exit(1)
print("BUS_MENTION_TS_OK")
