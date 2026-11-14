#!/usr/bin/env python3
"""Rehearse per-slot backup artifacts (#4268 point 2) on a scratch dir.

Two phases, because retention changes what is attestable:
  A. AGENT_CHAT_BACKUP_KEEP=0 (keep everything): every row's `path` exists and
     hashes to its recorded sha256/bytes -- including the OLDEST row, which is
     the whole point (the old rolling name could not do that).
  B. AGENT_CHAT_BACKUP_KEEP=2: prune keeps 2 per base and deletes the oldest,
     so a row outside retention must be read as PRUNED, never as a FAIL.

usage: rehearse-per-slot-backup.py [--dir DIR]
"""
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile

BUS = "/home/gumbyender/Agents/Projects/hermes-agent-fleet-toolkit/chat-room/bus.py"
SLOTS = ["20261114T041500.111111-0500", "20261114T051500.222222-0500",
         "20261114T061500.333333-0500"]

arg_dir = None
if len(sys.argv) > 1 and sys.argv[1] == "--dir":
    arg_dir = sys.argv[2]

# The live DB is local (home); only the backup destination is on CIFS. Keep that
# split here too: a WAL-mode sqlite on /mnt/nas is not what the bus does.
root = tempfile.mkdtemp(prefix="rehearse-slot-", dir=os.path.expanduser(
    "~/.hermes/profiles/frodo/cache/scratch"))
bak_root = arg_dir or root
if arg_dir:
    os.makedirs(bak_root, exist_ok=True)
db = os.path.join(root, "room.db")

c = sqlite3.connect(db)
c.execute("PRAGMA journal_mode=WAL")
c.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, body TEXT)")
c.executemany("INSERT INTO messages (body) VALUES (?)",
              [("row %d " % i + "x" * 200,) for i in range(500)])
c.commit()

fails = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (" " + detail if detail else ""))
    if not ok:
        fails.append(name)


def run(phase, keep):
    bak = os.path.join(bak_root, "backup-" + phase)
    os.makedirs(bak, exist_ok=True)
    os.environ["AGENT_CHAT_BACKUP"] = bak
    os.environ["AGENT_CHAT_BACKUP_KEEP"] = str(keep)
    os.environ["AGENT_CHAT_DB"] = db
    os.environ["AGENT_CHAT_SECRET"] = "rehearse"
    spec = importlib.util.spec_from_file_location("bus_" + phase, BUS)
    bus = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bus)
    bus.DB_PATH = db
    bus.BUS_CHECKPOINT_LOG = os.path.join(root, "bus-checkpoint-%s.log" % phase)
    for s in SLOTS:
        assert bus.backup("rehearse", slot=s), "backup returned None for %s" % s
    rows = [json.loads(l) for l in open(os.path.join(bak, "room.db.bak.manifest.jsonl"))]
    print("\n--- phase %s (KEEP=%d) ---" % (phase, keep))
    check("[%s] three rows written" % phase, len(rows) == 3, "got %d" % len(rows))
    for idx, r in enumerate(rows):
        # A row is inside retention while fewer than `keep` newer rows exist.
        retained = keep <= 0 or (len(rows) - 1 - idx) < keep
        for base in ("room.db.bak", "room.db-wal.bak"):
            meta = r["files"][base]
            want = base + "." + r["slot"]
            check("[%s] %s names its own artifact" % (phase, r["slot"]),
                  meta.get("path") == want, "path=%s" % meta.get("path"))
            p = os.path.join(bak, meta.get("path") or "")
            if not retained:
                check("[%s] %s artifact PRUNED by retention" % (phase, r["slot"]),
                      not os.path.exists(p), p)
                continue
            check("[%s] %s artifact exists" % (phase, r["slot"]), os.path.exists(p), p)
            if os.path.exists(p):
                h = hashlib.sha256(open(p, "rb").read()).hexdigest()
                check("[%s] %s artifact hash == row sha256" % (phase, r["slot"]),
                      h == meta["sha256"], "disk=%s row=%s" % (h[:12], meta["sha256"][:12]))
                check("[%s] %s artifact bytes == row bytes" % (phase, r["slot"]),
                      os.path.getsize(p) == meta["bytes"])
    check("[%s] rolling alias still present" % phase,
          os.path.exists(os.path.join(bak, "room.db.bak")))
    left = sorted(n for n in os.listdir(bak)
                  if n.startswith("room.db.bak.2") and n != "room.db.bak.manifest.jsonl")
    return rows, left


rows_a, left_a = run("keep0", 0)
check("[keep0] no pruning: all three slots present", len(left_a) == 3, str(left_a))

rows_b, left_b = run("keep2", 2)
check("[keep2] prune kept 2 main artifacts", len(left_b) == 2, str(left_b))
check("[keep2] prune deleted the OLDEST slot",
      not any(SLOTS[0] in n for n in left_b), str(left_b))

print("\nRESULT %s (%d failed)" % ("PASS" if not fails else "FAIL", len(fails)))
print("db scratch kept at %s" % root)
print("backup destination %s" % bak_root)
sys.exit(0 if not fails else 1)
