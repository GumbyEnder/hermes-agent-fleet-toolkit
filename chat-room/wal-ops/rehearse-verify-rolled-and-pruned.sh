#!/usr/bin/env bash
# rehearse-verify-rolled-and-pruned.sh -- independent mutation test of
# verify-slot-manifest.sh's per-slot-path branches, by buzzy 2026-11-14.
#
# WHY: frodo's claim is that a row naming its OWN artifact (files.<base>.path)
# is re-assertable for every slot inside retention, that a missing artifact
# inside retention is a FAIL (a deletion), and that only outside retention is
# it INFO PRUNED. A run against the live manifest can only ever show the happy
# branch, because there is (today) one per-slot row. So mutate a COPY:
#
#   case1 rolled   : real 05:27 row + one synthetic newer row -> real files,
#                    row is NOT newest, still asserts bytes/sha against ITS path
#   case2 deleted  : same row, path points at a name that does not exist,
#                    inside retention -> FAIL pruned? NO: FAIL missing
#   case3 pruned   : same row + KEEP synthetic newer rows -> INFO PRUNED, PASS
#
# Never touches /mnt/nas/agent-chat: builds a scratch tree and points the
# verifier at it with AGENT_CHAT_BACKUP.
#
# usage: bash rehearse-verify-rolled-and-pruned.sh   (rc != 0 if any expectation breaks)
set -u
VERIFY=${VERIFY:-/mnt/nas/host-ops/agent-chat/verify-slot-manifest.sh}
SRC=${SRC:-/mnt/nas/agent-chat}
KEEP=${AGENT_CHAT_BACKUP_KEEP:-24}
W=$(mktemp -d "${TMPDIR:-/tmp}/vsm-rehearse.XXXXXX")
trap 'rm -rf "$W"' EXIT
rc_total=0

# real row + real per-slot artifacts
grep '"path"' "$SRC/room.db.bak.manifest.jsonl" | tail -1 > "$W/row.json"
row_slot=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["slot"])' "$W/row.json")
row_path=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["files"]["room.db.bak"]["path"])' "$W/row.json")
row_wpath=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["files"]["room.db-wal.bak"]["path"])' "$W/row.json")
mkdir -p "$W/bdir"
cp "$SRC/$row_path" "$SRC/$row_wpath" "$W/bdir/"
echo "rehearsing row $row_slot path=$row_path keep=$KEEP"

mkfuture () { # $1 = n synthetic newer rows appended after the real row
  local n=$1 f="$W/bdir/room.db.bak.manifest.jsonl"
  cp "$W/row.json" "$f"
  python3 - "$f" "$row_slot" "$n" "$row_path" "$row_wpath" <<'PY'
import json, sys
f, slot, n, p, wp = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5]
row = json.loads(open(f).read())
base = row['slot'].split('.')[0]
for i in range(1, n + 1):
    r = json.loads(json.dumps(row))
    r['slot'] = base + '.%06d-0000' % i          # later than the real row
    r['files']['room.db.bak']['path'] = p + '.newer%d' % i
    r['files']['room.db-wal.bak']['path'] = wp + '.newer%d' % i
    open(f, 'a').write(json.dumps(r) + '\n')
PY
}

run () { AGENT_CHAT_BACKUP="$W/bdir" AGENT_CHAT_BACKUP_KEEP="$KEEP" \
         BUS_CHECKPOINT_LOG=/dev/null bash "$VERIFY" "$@"; }
# NOTE: filter on the FULL slot, not a prefix -- synthetic rows share the prefix
# and rows[-1] would select the synthetic one (measured: case1 graded .000001).

expect () { # $1 label  $2 expected regex  $3 expected RESULT  $4 = output
  local out=$4
  local ok=1
  grep -qE "$2" <<<"$out" || ok=0
  grep -qE "RESULT $3" <<<"$out" || ok=0
  printf '%-8s %s\n' "$( [ $ok = 1 ] && echo PASS || echo FAIL )" "$1"
  [ $ok = 1 ] || { sed 's/^/         | /' <<<"$out"; rc_total=1; }
}

# case1: one newer row -> the row keeps asserting its own path
mkfuture 1
expect "rolled-but-in-retention still asserts its own path" \
       "PASS room.db.bak sha256 disk=[0-9a-f]{16} row=[0-9a-f]{16} path=$row_path" PASS \
       "$(run "$row_slot")"

# case2: same row, artifact deleted, 1 newer -> FAIL (a deletion, not shelf life)
rm "$W/bdir/$row_path"
expect "missing INSIDE retention is FAIL" \
       "FAIL room.db.bak present .*missing INSIDE retention" FAIL \
       "$(run "$row_slot")"

# case3: KEEP newer ARTIFACTS on disk, artifact absent -> INFO PRUNED, PASS
# NOTE (frodo 2026-11-14): retention is counted in artifacts ON DISK, not rows.
# The original case3 wrote KEEP newer ROWS and no files, which models the very
# divergence the fix exists to catch -- it read FAIL against 9492dfc1 and the
# "3/3 PASS" claim in #4445 does not reproduce. Create the files too.
mkfuture "$KEEP"
# artifacts must carry a VALID slot token (SLOT_RE) that sorts after the row,
# or newer_artifacts() cannot see them and the case tests nothing.
python3 - "$W/bdir" "$row_slot" "$KEEP" <<'PY'
import os, sys
from datetime import datetime, timedelta
d, slot, keep = sys.argv[1], sys.argv[2], int(sys.argv[3])
# A synthetic artifact is "newer" only if its SLOT TOKEN sorts after the row's
# token -- newer_artifacts() compares the token strings lexicographically. Build
# real timestamps, never a hand-rolled HHMMSS+1: 08:27:59 + 1 read as a minute
# carry and produced ...T082700.000001, which sorts BEFORE ...T082759.421389, so
# the case counted 0 newer artifacts and tested nothing (measured 2026-11-14,
# keep=3 and keep=24 both read `0 newer artifacts on disk`).
t = datetime.strptime(slot, '%Y%m%dT%H%M%S.%f%z')
for i in range(1, keep + 1):
    n = 'room.db.bak.%s' % (t + timedelta(seconds=i)).strftime('%Y%m%dT%H%M%S.%f%z')
    open(os.path.join(d, n), 'wb').write(b'x')
PY
expect "outside retention (KEEP newer ARTIFACTS) is INFO PRUNED, not FAIL" \
       "INFO room.db.bak PRUNED by retention" PASS \
       "$(run "$row_slot")"

# case3b: KEEP newer ROWS but no newer artifacts -> still FAIL (a real deletion)
# drop EVERY room.db.bak artifact (the row's own included): rows alone must not
# read as pruned, which is the divergence the fix exists to catch.
rm -f "$W"/bdir/room.db.bak.2026*
expect "KEEP newer ROWS with no newer artifacts is still FAIL" \
       "FAIL room.db.bak present .*missing INSIDE retention" FAIL \
       "$(run "$row_slot")"

exit $rc_total
