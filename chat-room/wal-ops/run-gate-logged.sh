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
#   $LOG_DIR/INDEX.tsv                     one row per run: ts, gate, head, origin,
#                                          dirty_count, rc, signature, log, args
#                                          (field five is a COUNT; a gate's own closing
#                                          `dirty=` is the 0|1|unknown boolean of the same
#                                          tree. Two keys, one tree, different meanings --
#                                          so they carry different NAMES. Measured 2026-11-15
#                                          by frodo: the wrapper journalled dirty=4 while
#                                          check-exec-copies printed dirty=1 on the same
#                                          tree, and a reader comparing the two reads a
#                                          contradiction that is not there. No reader moved:
#                                          check-index-rows.sh reads field NINE and NF only.)
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
# --verify -q, because `git rev-parse <unknown-rev>` ECHOES ITS OWN OPERAND on stdout and
# exits non-zero: a repo with no `origin` remote journalled ORIGIN_SHA="origin/master",
# truncated to `origin/maste` -- a sha field carrying a branch NAME, next to head=none that
# said the lookup failed. Measured 2026-11-15 on a scratch repo (arm B of the dirty test).
#
# head= carries THREE states, not two. `git rev-parse HEAD 2>/dev/null || echo none`
# collapsed "there is no repo here" and "git ran and could not resolve HEAD" into one
# token -- the same two-contradictions-on-one-line shape as the old dirty=0, one field
# away from the number a reader trusts. `none` means no repo; `unknown` means git ran
# and failed (no commits yet, corrupt index, git missing). Measured 2026-11-15.
if ! git --version >/dev/null 2>&1; then
  HEAD_SHA=unknown            # git itself is missing or failing, so nothing about this tree is known
elif ! git rev-parse --git-dir >/dev/null 2>&1; then
  HEAD_SHA=none               # git works; this directory is not inside a repo
elif ! HEAD_SHA=$(git rev-parse --verify -q HEAD 2>/dev/null); then
  HEAD_SHA=unknown            # git works, it is a repo, and HEAD does not resolve (no commits, bad index)
fi
# `none` for origin is a STATE, not a failure: a repo may simply have no such ref, so
# origin has two endings (sha | none) while head has three.
ORIGIN_SHA=$(git rev-parse --verify -q origin/master 2>/dev/null || echo none)
# dirty= is 0|1|unknown, and the row carries the PATH SET beside the count.
# Two defects, one line, both measured 2026-11-15 in a shared worktree:
#   (a) the count came from the EMPTINESS of `git status --porcelain`, so a failing git
#       journalled dirty=0 -- the `1ff9cc8` class, left here with head=none as the tell
#       until now;
#   (b) `# dirty_paths` printed that count, so a field named for a set carried a
#       cardinality. A count names the TREE and never the author: frodo read dirty=1 and
#       nine paths that were mine, in flight, four minutes before 1ff9cc8 committed them.
#       The SET is the part a second writer can recognise as theirs, and it is the only
#       part that can be intersected with a commit's scope. Attribution still does not
#       exist here -- no git command names who wrote an uncommitted path -- so this field
#       is evidence for a human/peer check, never a verdict.
# SCOPE: this count is `git status --porcelain` (untracked INCLUDED), while a gate's own
# `dirty=` token is usually `git status --porcelain --untracked-files=no` (tracked-only).
# One untracked path moves this number and cannot move that boolean. Measured 2026-11-15T22:47:54
# -0500 in a single journalled row: dirty_count=4 beside the gate's dirty=1, the difference being
# chat-room/test-bus-mention-ts.py -- scope, not drift. Quote either number with its scope.
#
# THE DISCRIMINATOR IS NOT `git ls-files`; IT IS `git ls-tree -r HEAD`. Three objects live under
# one tree and each is read by a different command: the WORKTREE (porcelain), the INDEX
# (`ls-files`, and `--untracked-files=no`), and HEAD (what head=/origin=/a commit's scope
# describe). `git ls-files` grades the INDEX, so a staged-but-uncommitted path IS in it and is
# NOT in HEAD: `git add p`, no commit, then `ls-files p` = 1 hit, `ls-tree -r HEAD --name-only
# -- p` = 0 hits, `porcelain --untracked-files=no` = 1 line `A  p`, `diff --name-status HEAD` =
# `A	p`. A rule keyed on ls-files calls that path tracked, so a reader intersecting the two
# counts concludes the scopes agree -- the exact failure a scope rule exists to prevent.
# Measured 2026-11-16 in a scratch repo, untracked arm and staged arm (frodo's counter-example).
#
# So the row CLASSIFIES instead of asking a reader to intersect two booleans:
#   dirty_untracked    -- porcelain `??`: in the WORKTREE, absent from the INDEX
#   dirty_head_absent  -- dirty paths with no `git ls-tree -r HEAD` entry: INDEX != HEAD
# A path can appear in both; only head_absent can move a commit's scope. A rename prints in
# porcelain's `old -> new` form and both endpoints are graded here; a path porcelain has to
# QUOTE (spaces, tabs, non-ASCII) is graded as the quoted token and will not match ls-tree --
# read that as "cannot place", never as "absent".
if ! DIRTY_RAW=$(git status --porcelain 2>/dev/null); then
  DIRTY=unknown
  DIRTY_SET='<unknown>'
  DIRTY_UNTRACKED='<unknown>'
  DIRTY_HEAD_ABSENT='<unknown>'
elif [ -n "$DIRTY_RAW" ]; then
  DIRTY=$(printf '%s\n' "$DIRTY_RAW" | wc -l | tr -d ' ')
  DIRTY_SET=$(printf '%s\n' "$DIRTY_RAW" | cut -c4- | tr '\n' ' ' | sed 's/ $//')
  DIRTY_UNTRACKED=$(printf '%s\n' "$DIRTY_RAW" | awk 'substr($0,1,2)=="??"{print substr($0,4)}' \
    | tr '\n' ' ' | sed 's/ $//')
  if [ "$HEAD_SHA" = unknown ] || [ "$HEAD_SHA" = none ]; then
    DIRTY_HEAD_ABSENT="<no HEAD to compare: $HEAD_SHA>"
  else
    HEAD_NAMES=$(git ls-tree -r "$HEAD_SHA" --name-only 2>/dev/null) || HEAD_NAMES=''
    ABSENT=''
    while IFS= read -r _p; do
      [ -n "$_p" ] || continue
      case "$_p" in
        *' -> '*) _cands=("${_p%% -> *}" "${_p##* -> }") ;;
        *)        _cands=("$_p") ;;
      esac
      for _c in "${_cands[@]}"; do
        printf '%s\n' "$HEAD_NAMES" | grep -Fxq -- "$_c" || ABSENT="$ABSENT $_c"
      done
    done < <(printf '%s\n' "$DIRTY_RAW" | cut -c4-)
    DIRTY_HEAD_ABSENT=$(printf '%s' "${ABSENT# }")
    unset ABSENT _p _c _cands
  fi
  # An empty class is a MEASURED zero, not an unset variable: `${x:-<unset>}` at print time
  # would render both the same, so say <none> here where the measurement happened.
  [ -n "$DIRTY_UNTRACKED" ] || DIRTY_UNTRACKED='<none>'
  [ -n "$DIRTY_HEAD_ABSENT" ] || DIRTY_HEAD_ABSENT='<none>'
else
  DIRTY=0
  DIRTY_SET='<none>'
  DIRTY_UNTRACKED='<none>'
  DIRTY_HEAD_ABSENT='<none>'
fi
unset DIRTY_RAW
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
  echo "# dirty_count    : $DIRTY"
  echo "# dirty_paths    : $DIRTY_SET"
  echo "# dirty_untracked : ${DIRTY_UNTRACKED:-<unset>}"
  echo "# dirty_head_absent : ${DIRTY_HEAD_ABSENT:-<unset>}"
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
