#!/usr/bin/env python3
"""Count WAL-IDLE rows, distinct holder pids, and three different "episode" quantities.

Why this exists: a count is only a fact as a (parser, window) pair. Three parsers over
the same rows give three different numbers, and none of them is "the" episode count:

  episodes (A)  consecutive rows chain when 540 <= dt <= 660 s AND no_delete_for rises by
                600 +/- 2 s AND the holder pid sets intersect non-emptily. Handover and
                extra connections are allowed.
  strict (B)    consecutive rows chain only when the holder PID sets are exactly equal.
  runs   (C)    consecutive rows chain only when the verbatim `holders=` STRING is equal
                (no dt gate, no no_delete_for gate). This is a run count, not an episode
                count -- it is the column that must never be labelled "holder-set equality".

Window: every row whose stamp is <= the cut, over the artifact files named on the command
line, deduped on (stamp, no_delete_for, verbatim holders=), sorted by stamp. The default
window is the frozen 2026-11-14 census pair (sha256 pinned in its SHA256SUMS), which is why
its numbers are quotable: nothing in that directory can move.

  ./episodes-cut.py                          # frozen pair, no cut (whole window)
  ./episodes-cut.py 2026-11-14T01:00        # frozen pair, cut at an ISO stamp
  ./episodes-cut.py --first 20 --first 23    # first N rows (row-index cuts)
  ./episodes-cut.py --live /path/wal-unlink-verdict.log 2026-11-15T04:00

A row-index cut over a live file is NOT quotable: the file only grows and a late row can
sort into the middle, so the same N can mean different rows tomorrow. Quote row-index cuts
only against a frozen artifact, and always with the upper bound (N or the stamp).
"""
import argparse
import re
import sys
from datetime import datetime

DEFAULT_DIR = "/mnt/nas/host-ops/agent-chat/census/20261114T082421Z"
DEFAULT_FILES = ["wal-unlink-verdict.log.1", "wal-unlink-verdict.log"]

ROW = re.compile(r'^(\S+) WAL-IDLE\s+\S+\s+(.*)$')
NDF = re.compile(r'no_delete_for=(\d+)s')
HOLD = re.compile(r'holders=([^ ]+)')


def load(paths):
    rows = []
    for path in paths:
        try:
            with open(path, 'rb') as fh:
                data = fh.read()
        except FileNotFoundError:
            print(f"missing: {path}", file=sys.stderr)
            continue
        for line in data.split(b'\n'):
            s = line.decode('utf-8', 'replace')
            m = ROW.match(s)
            if not m:
                continue
            stamp, rest = m.group(1), m.group(2)
            n = NDF.search(rest)
            h = HOLD.search(rest)
            if not n or not h:
                continue
            holders = h.group(1)
            rows.append((stamp, int(n.group(1)), holders,
                         tuple(sorted(t.split(':')[0] for t in holders.split(',')))))
    seen, out = set(), []
    for r in rows:
        k = (r[0], r[1], r[2])
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    out.sort(key=lambda r: r[0])
    return out


def _dt(a, b):
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()


def episodes_a(rows):
    e = 1
    for i in range(1, len(rows)):
        p, r = rows[i - 1], rows[i]
        chained = (540 <= _dt(p[0], r[0]) <= 660
                   and 598 <= r[1] - p[1] <= 602
                   and bool(set(p[3]) & set(r[3])))
        if not chained:
            e += 1
    return e


def strict_b(rows):
    return 1 + sum(1 for i in range(1, len(rows)) if rows[i - 1][3] != rows[i][3])


def runs_c(rows):
    return 1 + sum(1 for i in range(1, len(rows)) if rows[i - 1][2] != rows[i][2])


def report(label, rows):
    pids = {p for r in rows for p in r[3]}
    print(f"window {label}: rows={len(rows)} pids={len(pids)} "
          f"episodes={episodes_a(rows)} strict_set={strict_b(rows)} runs={runs_c(rows)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cuts", nargs="*", help="ISO stamps; window = rows with stamp <= cut")
    ap.add_argument("--first", type=int, action="append", default=[],
                    help="row-index window over the sorted rows (repeatable)")
    ap.add_argument("--dir", default=DEFAULT_DIR, help="artifact directory")
    ap.add_argument("--file", action="append", default=[], help="artifact basename (repeatable)")
    ap.add_argument("--live", action="append", default=[], help="extra live file (repeatable)")
    args = ap.parse_args()

    names = args.file or DEFAULT_FILES
    paths = [f"{args.dir}/{n}" for n in names] + list(args.live)
    rows = load(paths)
    if not rows:
        print("no WAL-IDLE rows parsed", file=sys.stderr)
        return 2
    print(f"files: {', '.join(paths)}")
    print(f"span:  {rows[0][0]} -> {rows[-1][0]}")

    if not args.cuts and not args.first:
        report("all", rows)
        return 0
    for cut in args.cuts:
        report(f"<= {cut}", [r for r in rows if r[0] <= cut])
    for n in sorted(args.first):
        report(f"first {n}", rows[:n])
    return 0


if __name__ == "__main__":
    sys.exit(main())
