#!/usr/bin/env bash
# run-gate-logged.sh -- run a wal-ops gate and give its output a durable home.
#
# WHY (2026-11-15, from a retraction): a gate result quoted in the room had no log behind it.
# The run happened in a session scratch dir, and scratch is a 24h-pruned TMPDIR, so by the
# next morning the numbers were unbacked -- neither party could table the multi-path claim
# it rested on. A gate reading is only a fact if a third party can open the same file later.
#
# So: every gate run writes
#   $LOG_DIR/<gate>-<UTCts>-<head12>.log   full stdout+stderr, plus a HEADER/FOOTER
#   $LOG_DIR/INDEX.tsv                     one row per run: ts, gate, head, origin, dirty, rc,
#                                          signature, log, args (args is field NINE, last)
# and the log is written on FAIL as well as PASS (a failure that was not captured is the
# expensive case). Logs live on NAS, never in a session scratch dir.
#
# Usage:  bash run-gate-logged.sh <gate-script> [gate args...]
#         GATE_LOG_DIR=/some/dir bash run-gate-logged.sh ...    (default /mnt/nas/host-ops/gate-logs)
# Run it from the repo root: HEAD/dirty are taken from the current work tree.
# Exit code is the gate's own rc, so this wraps transparently in && chains.

set -uo pipefail

if [ $# -lt 1 ]; then
  sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
fi

GATE="$1"; shift
LOG_DIR="${GATE_LOG_DIR:-/mnt/nas/host-ops/gate-logs}"

if [ ! -f "$GATE" ]; then
  echo "run-gate-logged: gate not found: $GATE" >&2
  exit 2
fi
if ! mkdir -p "$LOG_DIR" 2>/dev/null || [ ! -w "$LOG_DIR" ]; then
  echo "run-gate-logged: log dir not writable: $LOG_DIR -- refusing to run an unlogged gate" >&2
  exit 2
fi

GATE_NAME=$(basename "$GATE" .sh)
TS=$(date -u +%Y%m%dT%H%M%SZ)
TS_LOCAL=$(date +%Y-%m-%dT%H:%M:%S%z)
REPO=$(git rev-parse --show-toplevel 2>/dev/null || echo "not-a-repo")
HEAD_SHA=$(git rev-parse HEAD 2>/dev/null || echo none)
ORIGIN_SHA=$(git rev-parse origin/master 2>/dev/null || echo none)
DIRTY=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
HEAD12=$(printf '%s' "$HEAD_SHA" | cut -c1-12)

LOG="$LOG_DIR/${GATE_NAME}-${TS}-${HEAD12}.log"

{
  echo "# gate           : $GATE $*"
  echo "# started_utc    : $TS"
  echo "# started_local  : $TS_LOCAL"
  echo "# cwd            : $(pwd)"
  echo "# repo           : $REPO"
  echo "# head           : $HEAD_SHA"
  echo "# origin/master  : $ORIGIN_SHA"
  echo "# dirty_paths    : $DIRTY"
  echo "# host           : $(hostname)"
  echo "#--- gate output ---"
} > "$LOG"

bash "$GATE" "$@" 2>&1 | tee -a "$LOG"
RC=${PIPESTATUS[0]}

# The gate's own closing line is its signature: "doc-links gate: head=... ok=.. fail=.. rc=..".
SIG=$(grep -E 'gate:.*ok=|^RESULT ' "$LOG" | tail -1)

# ARGS is field NINE, last, so nothing that parses the first eight columns breaks, and an
# args field can never sit where a head is read. Why %q then tr: the SLOT_EP incident is
# the argument -- an arg carrying whitespace or a tab either shifts every column to its
# right or silently truncates the row. %q keeps the value re-enterable; the tr keeps the
# row one line of tab-separated fields. (2026-11-15, frodo's counter-proposal, landed.)
ARGS=$(printf '%q ' "$@"); ARGS=${ARGS% }
ARGS=$(printf '%s' "$ARGS" | tr '\t\n' '  ')
# printf '%q ' with zero operands still runs the format once and prints '', which would
# claim an empty argument was passed. A gate invoked with no args says so explicitly.
[ $# -gt 0 ] || ARGS='<none>'

{
  echo "#--- footer ---"
  echo "# finished_local : $(date +%Y-%m-%dT%H:%M:%S%z)"
  echo "# rc             : $RC"
  echo "# signature      : ${SIG:-<none>}"
} >> "$LOG"

printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "$TS_LOCAL" "$GATE_NAME" "$HEAD12" "${ORIGIN_SHA:0:12}" "$DIRTY" "$RC" \
  "${SIG:-<none>}" "$LOG" "$ARGS" >> "$LOG_DIR/INDEX.tsv"

echo "run-gate-logged: rc=$RC log=$LOG"

exit "$RC"
