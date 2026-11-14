#!/usr/bin/env bash
# Mutation test for the (token, db) half of grade-slot-token.py's C1.
#
# WHY: bus-checkpoint.log is SHARED across DBs. A rehearsal or scratch fixture that
# writes the same token to a different db path produces three legs, one token --
# which satisfies "C1 is a key" unless the grader also demands one db. This test
# proves the grader refuses that shape, and that it refuses a manifest row whose db
# disagrees with the legs.
#
# The fixtures are copies; the production log and manifest are never written.
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
GRADER="$HERE/grade-slot-token.py"
LOG=${BZ_SLOT_LOG:-/home/gumbyender/agents/agent-chat/bus-checkpoint.log}
MAN=${BZ_SLOT_MAN:-/mnt/nas/agent-chat/room.db.bak.manifest.jsonl}
TOKEN=${1:-20261114T011402.927213-0500}
W=$(mktemp -d "${TMPDIR:-/tmp}/gradermix.XXXXXX")
trap 'rm -rf "$W"' EXIT

fail=0
ok()   { echo "PASS  $1"; }
bad()  { echo "FAIL  $1"; fail=1; }

# --- baseline: the real slot must still pass after the change ------------------
if python3 "$GRADER" "$TOKEN" >"$W/base.out" 2>&1; then
  ok "baseline $TOKEN still PASSes"
else
  bad "baseline $TOKEN no longer PASSes"; sed 's/^/      /' "$W/base.out"
fi

# --- case A: one leg names a different db --------------------------------------
grep -F "slot=$TOKEN" "$LOG" >"$W/legs.log"
[ "$(wc -l <"$W/legs.log")" -eq 3 ] || { bad "fixture needs 3 legs under $TOKEN"; exit 1; }
awk 'NR==3 && !done { sub(/db=[^ ]+/, "db=/tmp/scratch/other-room.db"); done=1 } { print }' \
  "$W/legs.log" >"$W/mixed.log"
BZ_SLOT_LOG="$W/mixed.log" BZ_SLOT_MAN="$MAN" python3 "$GRADER" "$TOKEN" >"$W/a.out" 2>&1
rc=$?
if [ $rc -ne 0 ] && grep -q "span 2 dbs" "$W/a.out"; then
  ok "mixed-db legs rejected (rc=$rc)"
else
  bad "mixed-db legs NOT rejected (rc=$rc)"; sed 's/^/      /' "$W/a.out"
fi

# --- case B: manifest row names a different db ---------------------------------
if grep -qF "\"slot\": \"$TOKEN\"" "$MAN"; then
  python3 - "$TOKEN" "$MAN" "$W/man.jsonl" <<'PY'
import json, sys
token, src, dst = sys.argv[1:4]
with open(src) as f, open(dst, "w") as g:
    for line in f:
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("slot") == token:
            r["db"] = "/tmp/scratch/other-room.db"
        g.write(json.dumps(r) + "\n")
PY
  BZ_SLOT_LOG="$W/legs.log" BZ_SLOT_MAN="$W/man.jsonl" python3 "$GRADER" "$TOKEN" >"$W/b.out" 2>&1
  rc=$?
  if [ $rc -ne 0 ] && grep -q "manifest row db" "$W/b.out"; then
    ok "manifest-db mismatch rejected (rc=$rc)"
  else
    bad "manifest-db mismatch NOT rejected (rc=$rc)"; sed 's/^/      /' "$W/b.out"
  fi
else
  echo "SKIP  no manifest row for $TOKEN (rotated out of the journal)"
fi

[ $fail -eq 0 ] && echo "RESULT PASS  grader keys on (token, db)" && exit 0
echo "RESULT FAIL"; exit 1
