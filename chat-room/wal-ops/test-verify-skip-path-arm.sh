#!/usr/bin/env bash
# test-verify-skip-path-arm.sh -- regression arms for the skip:absent arm of
# verify-slot-manifest.sh.
#
# THE HOLE (buzzy 2026-11-14, independently reproduced): check_artifact() returns
# at the `state.startswith('skip')` branch WITHOUT reading meta['path']. A row
# that says `wal=skip:absent` while files["room.db-wal.bak"] also names a path
# therefore asserts nothing about that path -- the leg "row says absent" passes
# and the named artifact is never looked for. That is a row shape a bus never
# writes (bus.py writes {"bytes": None, "state": "absent"} with no path key),
# but the manifest is plain JSONL on the backup destination: a torn append, a
# hand repair, or a restore of a foreign tree reaches it without bus.py running,
# and those are exactly the inputs this verifier exists for.
#
# ARMS (asserted, proposed semantics):
#   A bypass   -- absent row that names a path that is not on disk  -> FAIL
#   B control  -- absent row in the exact bus shape (no path key)    -> PASS
#   C live     -- the live manifest rows                             -> PASS
# B is the control that matters: a fix that also fails the bus's own absent row
# would be worse than the bypass.
#
# Usage: test-verify-skip-path-arm.sh [verify-slot-manifest.sh]
#   default verifier: ./verify-slot-manifest.sh next to this script.
# Exit 0 only if A fails-as-expected, B passes, C passes.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
VERIFY=${1:-$HERE/verify-slot-manifest.sh}
LIVE=/mnt/nas/agent-chat
MAN=$LIVE/room.db.bak.manifest.jsonl
W=$(mktemp -d "${TMPDIR:-/tmp}/skiparm.XXXXXX") || exit 1
trap 'rm -rf "$W"' EXIT
bad=0
note() { printf '%s\n' "$*"; }

[ -x "$VERIFY" ] || { note "FAIL verifier not executable: $VERIFY"; exit 2; }
[ -r "$MAN" ]    || { note "FAIL live manifest unreadable: $MAN"; exit 2; }

# --- shadow backup dir: symlinks to the live artifacts + a crafted manifest -----
# The verifier derives MAN from AGENT_CHAT_BACKUP, so the crafted manifest and
# the artifacts it is audited against have to live in one directory. Symlinking
# the real artifacts keeps retention counts and sizes honest.
# NEVER symlink the live manifest in here: `cp` onto a symlink follows it and
# writes THROUGH to the live file (this harness truncated the live manifest to
# one row on 2026-11-14 doing exactly that). Artifacts only; manifest is written
# fresh below.
for f in "$LIVE"/room.db.bak.* "$LIVE"/room.db-wal.bak*; do
    [ -e "$f" ] || continue
    case $(basename "$f") in *.manifest.jsonl) continue ;; esac
    ln -s "$f" "$W/$(basename "$f")" 2>/dev/null
done
# Belt and braces: the manifest path must not exist as a symlink before we write.
rm -f "$W/room.db.bak.manifest.jsonl"

python3 - "$MAN" "$W" <<'PY'
import json, sys
man, w = sys.argv[1], sys.argv[2]
rows = [json.loads(l) for l in open(man) if l.strip()]
row = rows[-1]
base = 'room.db-wal.bak'
# A bypass row: skip:absent, but names an artifact that is NOT on disk.
a = json.loads(json.dumps(row))
a['wal'] = 'skip:absent'
a['files'][base] = {'bytes': None, 'state': 'absent',
                    'path': '%s.20261114T235959.999999-0500' % base}
# B control: the exact shape bus.py writes for a skipped copy (no path key).
b = json.loads(json.dumps(row))
b['wal'] = 'skip:absent'
b['files'][base] = {'bytes': None, 'state': 'absent'}
for name, r in (('A.jsonl', a), ('B.jsonl', b)):
    with open('%s/%s' % (w, name), 'w') as f:
        f.write(json.dumps(r, sort_keys=True) + '\n')
print('BYPASS_PATH=%s' % a['files'][base]['path'])
PY
BYPASS_PATH=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["files"]["room.db-wal.bak"]["path"])' "$W/A.jsonl")
[ -e "$LIVE/$BYPASS_PATH" ] && { note "FATAL fixture path exists on disk: $BYPASS_PATH"; exit 2; }

run_manifest() {  # $1=crafted rows, $2=label ; rc on stdout
    cp "$1" "$W/room.db.bak.manifest.jsonl"
    # Arms A/B are rows no bus ever wrote, so their slot has no checkpoint-pre
    # history: point the log at an empty file rather than at the live log, where
    # an unrelated slot's pre-phase would add legs these arms are not about.
    AGENT_CHAT_BACKUP=$W AGENT_CHAT_BACKUP_KEEP=24 \
        BUS_CHECKPOINT_LOG="$W/buslog.empty" "$VERIFY" >"$W/out.$2" 2>&1
    echo $?
}
: > "$W/buslog.empty"

note "verifier: $VERIFY"
note "fixture bypass path: $BYPASS_PATH (does not exist on disk)"

rcA=$(run_manifest "$W/A.jsonl" A)
if grep -q '^FAIL .*skip row names no path' "$W/out.A"; then
    if [ "$rcA" -eq 1 ]; then note "PASS arm A: bypass row FAILs the skip-path leg (rc=1)"
    else note "FAIL arm A: leg FAILed but rc=$rcA"; bad=1; fi
else
    note "FAIL arm A: bypass row PASSed -- the skip arm does not read meta['path'] (rc=$rcA)"; bad=1
fi
sed 's/^/    A| /' "$W/out.A"

rcB=$(run_manifest "$W/B.jsonl" B)
if [ "$rcB" -eq 0 ] && grep -q '^RESULT PASS' "$W/out.B"; then
    note "PASS arm B: bus-shape absent row still PASSes (rc=0)"
else
    note "FAIL arm B: the fix rejects the bus's own absent row (rc=$rcB)"; bad=1
fi

# --- C: live manifest, and byte-identity against a reference build if given -----
AGENT_CHAT_BACKUP_KEEP=24 "$VERIFY" >"$W/out.C" 2>&1; rcC=$?
if [ "$rcC" -eq 0 ] && grep -q '^RESULT PASS' "$W/out.C"; then
    note "PASS arm C: live manifest PASSes (rc=0)"
else
    note "FAIL arm C: live manifest rc=$rcC"; sed 's/^/    C| /' "$W/out.C"; bad=1
fi
if [ -n "${VERIFY_REF:-}" ]; then
    AGENT_CHAT_BACKUP_KEEP=24 "$VERIFY_REF" >"$W/out.Cref" 2>&1
    if diff -q "$W/out.Cref" "$W/out.C" >/dev/null; then
        note "PASS arm C-ref: live output byte-identical to $VERIFY_REF"
    else
        note "FAIL arm C-ref: live output differs from reference:"
        diff "$W/out.Cref" "$W/out.C" | sed 's/^/    | /'
        bad=1
    fi
fi
[ "$bad" -eq 0 ] && note "RESULT PASS (all arms)" || note "RESULT FAIL"
exit $bad
