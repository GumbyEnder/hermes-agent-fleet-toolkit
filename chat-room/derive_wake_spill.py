#!/usr/bin/env python3
"""Rebuild a wake-spill file from the room, for ONE kind of cutover.

A dispatcher build that predates the spill (<= 400e658) keeps its queue only in
memory, so it cannot hand it over: `systemctl restart` drops every accepted wake
whose turn has not finished. Reconstructing the queue from the room is the only
lossless path for that first restart, and it needs one thing to be true — an
accepted-but-unfinished wake is, by definition, a mention with no reply from that
profile under it.

That identity is what this tool uses, bounded by wall-clock age because the
backlog is shallow: on 2026-11-13 a queue of 18 queued + 2 active matched 18
unanswered mentions in the previous 60 minutes.

Do NOT use it as a queue of record afterwards. Once a spill-aware dispatcher is
running, its own file is the truth: a re-derivation would re-wake mentions whose
replies were posted unparented.

  python3 chat-room/derive_wake_spill.py --minutes 60 \
      --out ~/agents/agent-chat/wake-spill.jsonl [--exclude 4043] [--print]
"""
import argparse
import json
import os
import sys
import time
import urllib.request

PROFILES = "aragorn,bns,buzzy,coder-b70,frodo,game,gollum,pippin,skyx"


def rows(bus, limit=200000):
    url = f"{bus.rstrip('/')}/api/messages?since=0&limit={limit}"
    with urllib.request.urlopen(url, timeout=30) as r:
        data = json.loads(r.read() or b"[]")
    if not isinstance(data, list):
        raise SystemExit(f"unexpected /api/messages shape: {type(data).__name__}")
    return data


def derive(messages, profiles, minutes, exclude=()):
    now = max((m["ts"] for m in messages), default=time.time())
    answered = {}
    for m in messages:
        parent = m.get("parent_id")
        if parent:
            answered.setdefault(parent, set()).add(m.get("sender"))
    out = []
    for m in messages:
        text = m.get("text") or ""
        if (now - m["ts"]) / 60.0 > minutes:
            continue
        if m["id"] in exclude:
            continue
        for p in profiles:
            if p == m.get("sender"):
                continue
            if f"@{p}" in text and p not in answered.get(m["id"], set()):
                out.append({"profile": p,
                            "payload": {"from": m.get("sender"), "topic": m.get("topic"),
                                        "text": text, "message_id": m["id"]}})
    out.sort(key=lambda r: r["payload"]["message_id"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bus", default=os.environ.get("AGENT_CHAT_URL",
                                                    "http://100.122.198.32:8090"))
    ap.add_argument("--minutes", type=float, default=60.0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--allow", default=os.environ.get("WAKE_ALLOW", PROFILES))
    ap.add_argument("--exclude", default="", help="comma list of message ids")
    ap.add_argument("--print", action="store_true")
    a = ap.parse_args()
    profiles = [p.strip() for p in a.allow.split(",") if p.strip()]
    exclude = {int(x) for x in a.exclude.split(",") if x.strip()}
    recs = derive(rows(a.bus), profiles, a.minutes, exclude)
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for i, rec in enumerate(recs, 1):
            fh.write(json.dumps(dict(rec, seq=i), sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, a.out)
    per = {}
    for rec in recs:
        per[rec["profile"]] = per.get(rec["profile"], 0) + 1
    print(json.dumps({"records": len(recs), "minutes": a.minutes,
                      "per_profile": per, "out": os.path.abspath(a.out)},
                     sort_keys=True))
    if a.print:
        for rec in recs:
            p = rec["payload"]
            print(p["message_id"], rec["profile"], len(p["text"]),
                  (p["text"][:70].replace("\n", " ")))


if __name__ == "__main__":
    sys.exit(main())
