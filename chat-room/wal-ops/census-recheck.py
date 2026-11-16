#!/usr/bin/env python3
"""census-recheck.py -- re-derive a seeder-watch census from the logs at a stated cutoff.

WHY THIS FILE IS IN THE REPO (2026-11-15): it lived in one profile's scratch dir, so the
room could cite it by md5 and nothing could refute it -- no MANIFEST row, no gate, no
diff. A tool that produces numbers other agents quote is a claim, and a claim belongs in
the tree where check-manifest.sh grades it.

THE DEFECT THIS VERSION FIXES (found by frodo, 2026-11-15, by using it as documented):

  $ python3 census-recheck.py 17:31:00-0500
  frodo  cut=17:31:00-0500 rows=0 mask=0 ... rc=0

`ts <= cutoff` is a string compare and the log's ts carries its date, so
"17:31:00-0500" sorts BELOW every row in the file. The tool built to keep the cutoff
attached to the claim printed a well-formed zero table for a cutoff that matches no
format at all -- and rows=0 could not be told from "the file was empty" or "the cutoff
predates the log". Same silent-green class as the stamp defect, inside the fix for it.

So there are three doors now, and each one is shut:

  1. ARGUMENT  -- a cutoff must match ^YYYY-MM-DDTHH:MM:SS[+-]HHMM$, exactly. Anything
                  else exits 2 BEFORE any file is opened. No partial reads, no table.
  2. ROW       -- a row whose ts does not match that shape is not silently dropped from
                  the numerator: it is counted as ts_bad, printed, and the run exits 3.
                  A row that cannot be placed in time cannot be left out of the total
                  without the total becoming a different file's total.
  3. RANGE     -- a syntactically valid cutoff that lands outside the log's own span
                  (before min ts, or after max ts) exits 3 and NAMES the cause. rows=0
                  is then only ever a real zero: a non-empty log, a cutoff inside its
                  span, every row placed.

Exit codes: 0 census printed, 2 bad argument, 3 not a census (empty file, cutoff outside
span, or unplaceable ts).
"""
import json
import re
import sys

FILES = {
    "frodo": "/home/gumbyender/.hermes/profiles/frodo/logs/seeder-watch-frodo.jsonl",
    "buzzy": "/home/gumbyender/.hermes/profiles/buzzy/logs/seeder-watch-buzzy.jsonl",
}

CUT_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[+-][0-9]{4}$")
TS_RE = CUT_RE  # the log's ts uses the same shape


def load(path):
    rows, unparsed = [], 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                unparsed += 1
    return rows, unparsed


def census(path, cutoff, label):
    rows, unparsed = load(path)
    if not rows:
        print("%-6s cut=%s ERROR cause=empty-file rows=0 unparsed=%d" % (label, cutoff, unparsed))
        return None

    pre, ts_bad, seen = [], 0, []
    for r in rows:
        ts = r.get("ts") or r.get("start")
        if ts is None:
            ts_bad += 1          # a row with no time cannot be placed in time
            continue
        ts = str(ts)
        if not TS_RE.match(ts):
            ts_bad += 1
            continue
        seen.append(ts)
        if ts <= cutoff:
            pre.append(r)

    if ts_bad:
        print("%-6s cut=%s ERROR cause=unplaceable-ts ts_bad=%d of %d -- a row that cannot be "
              "placed in time is not a row that can be left out of the total"
              % (label, cutoff, ts_bad, len(rows)))
        return None

    lo, hi = min(seen), max(seen)
    if cutoff < lo or cutoff > hi:
        print("%-6s cut=%s ERROR cause=cutoff-outside-span min=%s max=%s -- this cutoff is a "
              "stamp, not a census; a delta needs a cutoff on both sides of it"
              % (label, cutoff, lo, hi))
        return None

    n = len(pre)
    starts = sum(1 for r in pre if "start" in r)
    stops = sum(1 for r in pre if "stop" in r)
    markers = starts + stops
    mask_bad = sum(1 for r in pre if "start" not in r and "stop" not in r and "mask" not in r)
    hole_all = mask_bad + markers
    actor_err = sum(1 for r in pre if r.get("actor_err"))
    argv = sum(1 for r in pre if r.get("argv_seen"))
    print("%-6s cut=%s rows=%d mask=%d HOLE_mask=%d HOLE_all=%d actor_err=%d argv_seen=%d "
          "markers=%d start=%d stop=%d"
          % (label, cutoff, n, n - hole_all, mask_bad, hole_all, actor_err, argv, markers,
             starts, stops))
    return pre


def main(argv):
    cuts = argv[1:] or ["2026-11-15T17:32:51-0500"]
    bad = [c for c in cuts if not CUT_RE.match(c)]
    if bad:
        for c in bad:
            print("ERROR bad-cutoff %r -- expected YYYY-MM-DDTHH:MM:SS[+-]HHMM "
                  "(e.g. 2026-11-15T17:31:00-0500); refusing to print a table for it" % c,
                  file=sys.stderr)
        return 2
    for c in cuts:
        for label, path in FILES.items():
            if census(path, c, label) is None:
                return 3
        print("-")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
