#!/usr/bin/env python3
"""grade-slot-token.py -- grade ONE bus backup slot on the token contract.

The contract the lock exists to buy, and nothing more:

  C1  the slot token is a key: one token on checkpoint-pre, checkpoint and
      backup, a token that parses as a datetime, and exactly one manifest row
      carrying that same token.
  C2  zero interleaving: the three legs are CONSECUTIVE lines of
      bus-checkpoint.log (nothing from another folder between them),
      timestamps non-decreasing, and no manifest row from a different pid was
      written inside the slot's copy window.
  C3  src=shutdown under the lock: all three legs and the manifest row carry
      the same src, and the shared token proves the three legs came from ONE
      _slot() call -- which holds _slot_lock across checkpoint+backup (bus.py
      `_slot`).  A leg outside that call cannot carry the token.

Read-only.  Exit 0 = PASS, 1 = FAIL (legs printed), 2 = usage.

usage: grade-slot-token.py <slot-token-or-prefix>
       grade-slot-token.py --list
"""
import datetime as dt
import json
import os
import re
import sys

LOG = os.environ.get("BZ_SLOT_LOG", "/home/gumbyender/agents/agent-chat/bus-checkpoint.log")
MAN = os.environ.get("BZ_SLOT_MAN", "/mnt/nas/agent-chat/room.db.bak.manifest.jsonl")

LEG = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4}) "
    r"db=(?P<db>\S+) (?P<kind>checkpoint-pre|checkpoint|backup): (?P<body>.*)$"
)
TOKEN_FMT = "%Y%m%dT%H%M%S.%f%z"


def legs(path):
    out = []
    for i, line in enumerate(open(path), 1):
        m = LEG.match(line.rstrip("\n"))
        if not m:
            continue
        body = m.group("body")
        tok = re.search(r"slot=(\S+)", body)
        src = re.search(r"src=(\S+)", body)
        out.append(
            {
                "line": i,
                "ts": dt.datetime.strptime(m.group("ts"), "%Y-%m-%dT%H:%M:%S%z"),
                "kind": m.group("kind"),
                "body": body,
                "slot": tok.group(1) if tok else None,
                "src": src.group(1) if src else None,
            }
        )
    return out


def manifest(path):
    rows = []
    for i, line in enumerate(open(path), 1):
        line = line.strip()
        if line:
            rows.append((i, json.loads(line)))
    return rows


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    want = argv[1]
    these = legs(LOG)
    if want == "--list":
        seen = []
        for l in these:
            if l["slot"] and l["slot"] not in seen:
                seen.append(l["slot"])
        for s in seen:
            print(s)
        return 0

    fails = []
    grp = [l for l in these if l["slot"] and l["slot"].startswith(want)]
    if not grp:
        # token-less legs can only be addressed by prefixing on purpose; no.
        print("FAIL no token-bearing legs match %r" % want)
        return 1
    tokens = sorted({l["slot"] for l in grp})
    if len(tokens) != 1:
        fails.append("C1 %d distinct tokens matched %r" % (len(tokens), tokens))
    token = tokens[0]
    grp = [l for l in these if l["slot"] == token]

    # ---- C1: token is a key ------------------------------------------------
    kinds = [l["kind"] for l in grp]
    for need in ("checkpoint-pre", "checkpoint", "backup"):
        if kinds.count(need) != 1:
            fails.append("C1 %s appears %d times (want 1)" % (need, kinds.count(need)))
    if len(grp) != 3:
        fails.append("C1 %d legs under token %s (want 3)" % (len(grp), token))
    try:
        inst = dt.datetime.strptime(token, TOKEN_FMT)
    except ValueError as e:
        fails.append("C1 token %r does not parse as %s (%s)" % (token, TOKEN_FMT, e))
        inst = None

    rows = [(i, r) for i, r in manifest(MAN) if r.get("slot") == token]
    if len(rows) != 1:
        fails.append("C1 %d manifest rows carry token %s (want 1)" % (len(rows), token))
    row = rows[0][1] if rows else None

    # ---- C2: zero interleaving --------------------------------------------
    idx = sorted(l["line"] for l in grp)
    if idx and idx != list(range(idx[0], idx[0] + len(idx))):
        fails.append("C2 legs are not consecutive log lines: %s" % idx)
    span = [l for l in these if idx and idx[0] < l["line"] < idx[-1]]
    other = [l for l in span if l["slot"] != token]
    if other:
        fails.append(
            "C2 %d foreign leg(s) inside the slot: %s"
            % (len(other), [(l["line"], l["kind"], l["slot"]) for l in other])
        )
    seq = sorted(grp, key=lambda l: (l["ts"], l["line"]))
    if [l["ts"] for l in seq] != [l["ts"] for l in sorted(grp, key=lambda l: l["line"])]:
        fails.append("C2 leg timestamps go backwards: %s" % [l["ts"].isoformat() for l in seq])
    if inst is not None and grp:
        lo = min(l["ts"] for l in grp) - dt.timedelta(seconds=3)
        hi = max(l["ts"] for l in grp) + dt.timedelta(seconds=3)
        if not (lo <= inst <= hi):
            fails.append("C2 token instant %s outside leg window %s..%s" % (inst.isoformat(), lo.isoformat(), hi.isoformat()))
    if row is not None and grp:
        copies = [f.get("copy_time_ns") for f in (row.get("files") or {}).values() if f.get("copy_time_ns")]
        if copies:
            cw = (min(copies) / 1e9, max(copies) / 1e9)
            lo = min(l["ts"] for l in grp).timestamp() - 3
            hi = max(l["ts"] for l in grp).timestamp() + 3
            if not (lo <= cw[0] and cw[1] <= hi):
                fails.append("C2 manifest copy window %s outside leg window" % (cw,))
        conflicts = [
            (i, r.get("pid"), r.get("slot"))
            for i, r in manifest(MAN)
            if r is not row
            and r.get("pid") != row.get("pid")
            and min(copies or [0]) / 1e9 - 2
            <= max((f.get("copy_time_ns") or 0) for f in (r.get("files") or {}).values()) / 1e9
            <= max(copies or [0]) / 1e9 + 2
        ]
        if conflicts:
            fails.append("C2 foreign manifest row(s) in the copy window: %s" % conflicts)

    # ---- C3: src=shutdown, one call, under the lock ------------------------
    srcs = {l["src"] for l in grp}
    if len(srcs) != 1:
        fails.append("C3 legs disagree on src: %s" % sorted(srcs))
    if "shutdown" not in srcs:
        fails.append("C3 src is %s, not shutdown" % sorted(srcs))
    if row is not None:
        if row.get("src") not in srcs:
            fails.append("C3 manifest src=%r not in leg srcs %s" % (row.get("src"), sorted(srcs)))
        if row.get("pid") is None:
            fails.append("C3 manifest row has no pid")
        main = row.get("main")
        if not (isinstance(main, str) and main.startswith("copy:")):
            fails.append("C3 manifest main is %r, no copy arm ran" % (main,))
    else:
        fails.append("C3 no manifest row to attest the copy")

    print("slot  %s" % token)
    for l in sorted(grp, key=lambda l: l["line"]):
        print("  L%-4d %s %-14s %s" % (l["line"], l["ts"].isoformat(), l["kind"], l["body"]))
    if row is not None:
        print(
            "  MAN   pid=%s src=%s main=%s wal=%s"
            % (row.get("pid"), row.get("src"), row.get("main"), row.get("wal"))
        )
    if fails:
        for f in fails:
            print("FAIL " + f)
        print("RESULT FAIL (%d)" % len(fails))
        return 1
    print("RESULT PASS  C1 token-is-key  C2 zero-interleaving  C3 src=shutdown-in-one-call")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
