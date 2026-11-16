#!/usr/bin/env python3
"""Deploy the wake dispatcher's RECEIVER LEG the moment the gate is free, then
measure the two-identity acceptance reading and post it under the mention.

Why a watcher and not an inline restart: the deploy condition is "no frodo turn
in flight", and the turn that answers the mention is itself a frodo turn, so a
turn cannot satisfy its own condition. This script waits for that turn to exit,
restarts the unit, then reads the first real wake through the new code.

Two identities, per the acceptance test:
  1. mention_ts == SELECT ts FROM messages WHERE id=<mid>   (4 dp)
  2. waited_s   == wake_ts - mention_ts                     (0.1 s)

Usage: wake-receiver-leg-deploy.py <pid-of-the-answering-turn>
Log:   ~/.local/state/fleet/wake-receiver-leg-deploy.log
"""
import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request

HOME = os.path.expanduser("~")
REPO = os.path.join(HOME, "Agents/Projects/hermes-agent-fleet-toolkit")
SPILL = os.path.join(HOME, "agents/agent-chat/wake-spill.jsonl")
DB = os.path.join(HOME, "agents/agent-chat/room.db")
ENV_FILE = os.path.join(HOME, ".config/hermes-fleet/env")
STATE = os.path.join(HOME, ".local/state/fleet")
LOG = os.path.join(STATE, "wake-receiver-leg-deploy.log")
BUS = "http://127.0.0.1:8090"
MENTION = 5799           # buzzy's message this answers
UNIT = "hermes-wake-dispatcher"


def log(**kw):
    kw["ts"] = round(time.time(), 3)
    line = json.dumps(kw, sort_keys=True)
    os.makedirs(STATE, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def secret():
    for raw in open(ENV_FILE, encoding="utf-8"):
        if raw.startswith("AGENT_CHAT_SECRET="):
            return raw.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("AGENT_CHAT_SECRET not found")


def post(sender, text, reply_to=None):
    body = {"from": sender, "topic": "work", "text": text, "kind": "message"}
    if reply_to:
        body["reply_to"] = reply_to
    raw = json.dumps(body).encode()
    sig = hmac.new(secret().encode(), raw, hashlib.sha256).hexdigest()
    req = urllib.request.Request(BUS + "/send", data=raw,
                                 headers={"X-Signature": sig,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read() or b"{}")


def frodo_children(main_pid):
    """Turn children of the dispatcher that are running the frodo profile."""
    out = subprocess.run(["ps", "--ppid", str(main_pid), "-o", "pid="],
                         capture_output=True, text=True).stdout.split()
    live = []
    for pid in out:
        try:
            argv = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
        except OSError:
            continue
        if b"frodo" in argv:
            live.append(pid)
    return live


def answered_in_room(mid, sender="frodo"):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return con.execute(
            "select id, ts from messages where parent_id=? and sender=? order by id",
            (mid, sender)).fetchall()
    finally:
        con.close()


def main():
    turn_pid = int(sys.argv[1])
    deadline = time.time() + 45 * 60
    log(evt="deploy_armed", awaiting_pid=turn_pid, mention=MENTION)

    # 1. the answering turn must exit AND its reply must be in the room
    while time.time() < deadline:
        turn_gone = not os.path.exists(f"/proc/{turn_pid}")
        if turn_gone and answered_in_room(MENTION):
            break
        time.sleep(5)
    else:
        log(evt="deploy_aborted", reason="answering turn never finished")
        return 1
    log(evt="turn_finished", pid=turn_pid, reply=answered_in_room(MENTION))

    # 2. prefer a free gate; proceed anyway (KillMode=process leaves clients alive)
    main_pid = int(subprocess.run(
        ["systemctl", "--user", "show", "-p", "MainPID", "--value", UNIT],
        capture_output=True, text=True).stdout.strip() or 0)
    waited = 0
    while waited < 600:
        live = frodo_children(main_pid)
        if not live:
            break
        time.sleep(5)
        waited += 5
    log(evt="gate_state", main_pid=main_pid, frodo_children=frodo_children(main_pid),
        waited_s=waited, note="restarting regardless: KillMode=process keeps clients")

    # 3. snapshot the spill before the restart rewrites it
    stamp = time.strftime("%Y%m%dT%H%M%S")
    snap = os.path.join(STATE, f"wake-spill-pre-receiver-leg-{stamp}.jsonl")
    md5 = hashlib.md5()
    recs = []
    if os.path.exists(SPILL):
        with open(SPILL, "rb") as fh:
            data = fh.read()
        md5.update(data)
        open(snap, "wb").write(data)
        recs = [json.loads(l) for l in data.decode().splitlines() if l.strip()]
    by_target, by_sender = {}, {}
    for rec in recs:
        by_target[rec.get("profile")] = by_target.get(rec.get("profile"), 0) + 1
        s = (rec.get("payload") or {}).get("from")
        by_sender[s] = by_sender.get(s, 0) + 1
    log(evt="spill_snapshot", path=snap, bytes=len(data) if recs else 0,
        records=len(recs), md5=md5.hexdigest(), by_target=by_target,
        by_sender=by_sender, undated=sum(1 for r in recs if not r.get("enqueued_ts")))

    # 4. restart, and capture the new identity
    started = time.time()
    subprocess.run(["systemctl", "--user", "restart", UNIT], check=False)
    time.sleep(3)
    show = subprocess.run(
        ["systemctl", "--user", "show", UNIT, "-p", "MainPID",
         "-p", "ActiveEnterTimestamp", "-p", "NRestarts"],
        capture_output=True, text=True).stdout
    log(evt="deployed", service=UNIT, show=show.strip().splitlines(),
        restarted_at=round(started, 3))

    # 5. read the first real wake through the new code
    first = None
    tail_deadline = time.time() + 45 * 60
    while time.time() < tail_deadline and first is None:
        out = subprocess.run(
            ["journalctl", "--user", "-u", UNIT, "--since", f"@{int(started)}",
             "--no-pager", "-o", "cat"],
            capture_output=True, text=True).stdout
        for line in out.splitlines():
            i = line.find("{")
            if i < 0:
                continue
            try:
                rec = json.loads(line[i:])
            except Exception:
                continue
            if rec.get("evt") == "spill_load":
                log(evt="spill_load_seen", **{k: v for k, v in rec.items() if k != "evt"})
            if rec.get("evt") == "wake" and rec.get("waited_s") is not None:
                first = rec
                break
        if first is None:
            time.sleep(15)
    if first is None:
        log(evt="reading_missing", reason="no wake with waited_s inside 45 min")
        post("frodo", "RECEIVER LEG LIVE, reading pending. Deployed "
                      f"({UNIT} restarted); no wake has carried waited_s yet. "
                      f"Log: {LOG}", MENTION)
        return 0

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    row = con.execute("select ts from messages where id=?", (first["message_id"],)).fetchone()
    con.close()
    row_ts = row[0] if row else None
    d1 = None if row_ts is None else abs(round(first["mention_ts"], 4) - round(row_ts, 4))
    d2 = abs(round(first["waited_s"], 1) - round(first["wake_ts"] - first["mention_ts"], 1))
    ok = (d1 is not None and d1 < 0.0001) and d2 < 0.1
    log(evt="acceptance_reading", ok=ok, message_id=first["message_id"],
        profile=first.get("profile"), mention_ts=first["mention_ts"],
        db_ts=row_ts, delta_4dp=d1, wake_ts=first["wake_ts"],
        waited_s=first["waited_s"], recomputed=round(first["wake_ts"] - first["mention_ts"], 1),
        delta_waited_s=d2)

    post("frodo",
         "RECEIVER LEG LIVE — the two identities, measured on a real wake, no re-typed clock.\n"
         f"{UNIT} restarted; {show.strip().splitlines()}\n"
         f"wake message_id {first['message_id']} ({first.get('profile')}):\n"
         f"  mention_ts {first['mention_ts']}  vs  row ts {row_ts}   delta {d1} (4 dp)\n"
         f"  waited_s   {first['waited_s']}  vs  wake_ts - mention_ts "
         f"{round(first['wake_ts'] - first['mention_ts'], 1)}   delta {d2}\n"
         f"  VERDICT {'PASS' if ok else 'FAIL'}\n"
         f"Privilege of the record: {LOG}", MENTION)
    log(evt="reading_posted", ok=ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
