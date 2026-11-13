#!/bin/bash
# Witness the hourly janitor/backup collision for the BUSY/PINNED split -- v2.
#
# WHY v1 WAS WRONG (measured 2026-11-13 11:15 slot, frodo's witness3):
#  1. The slot was armed as OnCalendar=<hour>:00, so it fired 16 s BEFORE the pair.
#     v1 caught the pair only because of a fixed `sleep 30` and `SINCE=now-90`.
#     Salvage condition was phase_seconds <= 30; a phase at :31-:59 would have
#     printed "NO COLLISION" from a window that ended before the pair -- the
#     09:48:09 false negative. v2 computes the slot from ExecMainStartTimestamp.
#  2. The 11:15 capture was VACUOUS: both arms busy=0 log=0 folded=0 and the WAL
#     was ABSENT. Two wal_checkpoint(TRUNCATE) on a zero-frame database cannot
#     contend, so busy=0 is guaranteed on ANY build. v2 refuses to grade health
#     when no line in the window carries log>0 -- it prints VACUOUS instead.
#  3. WHY log=0 IS THE STEADY STATE, not an occasional idle hour (measured
#     2026-11-13 11:57): room.db header bytes 18/19 are 02 02 -- the file IS in
#     WAL mode -- and the 2-min probe shows wal=197792 out=0|48|48 through
#     traffic (11:27-11:41) then wal=absent from 11:43. The bus opens and closes
#     a connection per request, and SQLite deletes the -wal on the LAST close.
#     So the sidecar exists only while a request is in flight, and a slot landing
#     in an idle gap folds air no matter which build is loaded. Gating on log>0
#     alone would therefore print VACUOUS at slot after slot, forever -- honest
#     but still proving nothing. hermes-agent-chat-wal-keeper.service holds one
#     idle read connection so the last close is unreachable and frames from the
#     hour's traffic are still in the WAL at the pair. That is one way to get a
#     holder, not the only one: a live session kernel attached at the slot second
#     holds the sidecar just as well (measured 2026-11-13 15:15:19, keeper off:
#     log=30 folded=30, holder 1092165 = a hermes kernel). So frame supply is
#     opportunistic -- keeper, bus, or a kernel turn, whichever is live at :19 --
#     and a slot with none of them grades nothing. The keeper is retired
#     (stopped, disabled, Wants= removed) and must not be re-armed to "fix" this.
#
# WHEN: BACKUP_INTERVAL=JANITOR_INTERVAL=3600, both sleep-first, so the pair fires
# at ExecMainStartTimestamp + 1 h, NOT on the clock. Recompute every run; a restart
# inside the window destroys it and moves the phase.
#
# USAGE: witness-split-pair-v2.sh [--print]   (no arg = arm: sleep to slot, capture)
#        --print : compute slot/window and exit without sleeping or capturing
# CIFS note: systemd cannot exec this in place (EACCES on a nounix mount) -- schedule
# `bash /mnt/nas/host-ops/agent-chat/witness-split-pair-v2.sh`.
# Writes ~/agents/agent-chat/pairsplit2-<stamp>.log and prints one verdict line.

set -u
PRINT_ONLY=0
[ "${1:-}" = "--print" ] && PRINT_ONLY=1

# Rehearsal overrides. Defaults ARE production; nothing changes unless the caller
# sets them. A rehearsal must run THIS script's gate, not a copy of it, or the
# proof is about the copy: measured 2026-11-13, the rehearsal bus is a transient
# unit with a short BACKUP_INTERVAL, so the service name, the pair interval and
# the WAL path all differ from production while the grading block does not.
SVC="${WSVC:-hermes-agent-chat-bus.service}"
INTERVAL="${WINTERVAL:-3600}"
DBP="${WDB:-$HOME/agents/agent-chat/room.db}"
CPLOG="${WCPLOG:-$HOME/agents/agent-chat/bus-checkpoint.log}"
show() { systemctl --user show -p "$1" --value "$SVC"; }

START_TXT="$(show ExecMainStartTimestamp)"
# An EMPTY timestamp is not a missing slot, it is a wrong one: `date -d ''` prints
# today 00:00:00, so the -le 0 guard below passes and the script invents a slot
# from midnight (measured 2026-11-13: a failed rehearsal unit printed "slot
# 11:58:12" off a 00:00 epoch and graded an empty window). Fail on empty first.
if [ -z "$START_TXT" ] || [ "$START_TXT" = "n/a" ]; then
  echo "VERDICT: CANNOT GRADE -- ExecMainStartTimestamp empty for $SVC (unit never started or already collected); no slot exists."
  exit 2
fi
START_EP="$(date -d "$START_TXT" +%s 2>/dev/null || echo 0)"
if [ "$START_EP" -le 0 ]; then
  echo "VERDICT: CANNOT GRADE -- ExecMainStartTimestamp unreadable ('$START_TXT'); no slot exists."
  exit 2
fi
NOW=$(date +%s)
# the pair repeats every INTERVAL s, so the slot we watch is the NEXT multiple of
# INTERVAL after the process start -- not start+INTERVAL, which is already past when
# a run is re-fired after the first witness (measured: 10:15:19 start, 11:21 re-run).
K=$(( (NOW - START_EP) / INTERVAL + 1 ))
SLOT=$(( START_EP + INTERVAL * K ))
FROM=$(( SLOT - 5 ))

if [ "$PRINT_ONLY" = 1 ]; then
  echo "# MainPID $(show MainPID) start $START_TXT"
  echo "# slot #$K $(date -d @$SLOT -Is)  window_from $(date -d @$FROM -Is)"
  echo "# now  $(date -d @$NOW -Is)  sleep_to_slot_plus_2=$(( SLOT + 2 - NOW ))s"
  exit 0
fi

# If we fired early (the usual case: OnCalendar truncates to the minute) wait for the
# slot; if we fired late but still inside a sane window, capture immediately.
# MARGIN is how long past the slot we read: 2 s is right for the production bus (its
# backup thread ticks within a second of start+3600), but a rehearsal bus pays DB
# init before the thread starts and was measured ticking at start+33 with a 30 s
# interval -- capture at +2 read the window one second before the tick.
MARGIN="${WMARGIN:-2}"
# A recurring timer re-arms this (hermes-pairsplit-witness.timer, every 5 min). Only a
# tick whose wait fits inside WMAXWAIT sleeps, so a stale phase after a bus restart costs
# one period instead of an hour, and the slot+MARGIN moment falls inside exactly one
# period. A one-shot transient timer was what evaporated after 12:15 (RemainAfterElapse).
NEED=$(( SLOT + MARGIN - NOW ))
if [ -n "${WMAXWAIT:-}" ] && [ "$NEED" -gt "$WMAXWAIT" ]; then
  echo "# deferred: next slot $(date -d @$SLOT -Is) is ${NEED}s away (WMAXWAIT=${WMAXWAIT}); this tick captured nothing."
  exit 0
fi
if [ "$NOW" -lt "$(( SLOT + MARGIN ))" ]; then
  sleep "$NEED"
elif [ "$NOW" -gt "$(( SLOT + 120 ))" ]; then
  echo "VERDICT: TOO LATE -- slot $(date -d @$SLOT -Is) is $(( NOW - SLOT ))s past; this run watched nothing."
  exit 3
fi

OUT="$HOME/agents/agent-chat/pairsplit2-$(date +%Y%m%dT%H%M%S).log"
SINCE="@$FROM"
REPO="$HOME/Agents/Projects/hermes-agent-fleet-toolkit"
{
  echo "# captured $(date -Is); slot $(date -d @$SLOT -Is); journal since $SINCE"
  echo "# MainPID $(show MainPID) start $START_TXT NRestarts $(show NRestarts)"
  echo "# HEAD $(git -C "$REPO" rev-parse --short HEAD) bus.py $(md5sum "$REPO/chat-room/bus.py" | cut -d' ' -f1) tree: $(git -C "$REPO" status --porcelain | wc -l) modified"
  _bus="$REPO/chat-room/bus.py"
  _me="$(stat -c %Y "$_bus" 2>/dev/null || echo 0)"
  if [ "$_me" -gt "$START_EP" ] 2>/dev/null; then
    echo "# RUNNING build: DRIFT -- bus.py mtime $(date -d @$_me -Is) is AFTER start $(date -d @$START_EP -Is); loaded build predates HEAD"
  else
    echo "# RUNNING build: matches disk -- bus.py mtime $(date -d @$_me -Is) <= start $(date -d @$START_EP -Is); sha256 $(sha256sum "$_bus" | cut -d' ' -f1)"
  fi
  journalctl --user -u "$SVC" --since "$SINCE" --no-pager \
    | grep -E 'checkpoint-pre:|checkpoint:|checkpoint failed|janitor failed|backup failed'
  echo "# tee $(stat -c '%s %y' "$CPLOG" 2>/dev/null || echo absent)"
  # --- wal line: size + holders. Never let fuser's own error text land in the holders
  # field. Measured 2026-11-13T16:15:21: with the sidecar absent, `fuser "$DBP-wal" 2>&1`
  # put "Specified filename ... does not exist." where the holder list belongs, so the
  # field was empty-by-ERROR, not empty-by-measurement, and a reader grepping holders for
  # ": <pid>" tokens would parse a sentence as a holder. The shipped gate only survives
  # this because it keys on `^# wal [0-9]+`; do not leave the trap for the next reader.
  # ROOT CAUSE (psmisc 23.7, measured 16:57): fuser writes the FILE NAME to stderr and the
  # padded PIDs to stdout (' 1598328'), so the old 2>&1 manufactured both the `path: pid`
  # look in the 15:15 capture and the sentence in the 16:15 one. Stderr is dropped and the
  # path is re-prefixed explicitly, so holders is deterministically
  # `<db>-wal: <pid>[ <pid>]` | `none` (measured, no holder) | `n/a` (fuser not run).
  _pids=""
  if [ -e "$DBP-wal" ]; then
    _pids="$(fuser "$DBP-wal" 2>/dev/null | tr -s '[:space:]' ' ' | sed -e 's/^ //' -e 's/ $//')"
    _hold="$_pids"
    if [ -n "$_hold" ]; then _hold="$DBP-wal: $_hold"; else _hold="none"; fi
    echo "# wal $(stat -c '%s %y' "$DBP-wal" 2>/dev/null || echo absent) holders: $_hold"
  else
    echo "# wal absent holders: n/a (sidecar absent at capture; fuser not run)"
  fi
  # --- holder CLASSES, additive on their own line. The `# holders` field carries bare
  # PIDs and a PID dies with its interpreter: measured 2026-11-13, the 17:15 slot named
  # 1629524 and 1661351, both GONE (/proc absent) by 18:02 -- so an hour later the
  # artifact cannot say keeper vs session-kernel vs bus without a live box. Annotate at
  # capture time, and keep the `# wal ... holders:` shape (already quoted in the ledger)
  # byte-identical: this is a SECOND line, never an extension of that field.
  # Unit = DEEPEST .service component of /proc/<pid>/cgroup; every user unit sits under
  # user@1000.service, so a first-component walk grades them all the same and excludes
  # nothing.
  if [ -n "$_pids" ]; then
    _cls=""
    for _p in $_pids; do
      _comm="$(cat "/proc/$_p/comm" 2>/dev/null || echo '?')"
      _unit="$(awk -F/ '{for(i=NF;i>0;i--) if ($i ~ /\.service$/) {print $i; exit}}' "/proc/$_p/cgroup" 2>/dev/null)"
      _cls="$_cls ${_p}:${_comm}@${_unit:-gone}"
    done
    echo "# holder-class$_cls"
  fi
  echo "# keeper $(KEEP=$(systemctl --user show -p MainPID --value hermes-agent-chat-wal-keeper.service 2>/dev/null || echo 0); echo "pid=${KEEP:-0}") -- keeper MainPID only (0 = retired); frame supply is a property of ANY live holder at the slot second, named on the # holders line above, not of the keeper"
} | tee "$OUT"

# --- presence first: an empty window is a witness failure, not a healthy database.
# (the vacuous gate below must not swallow it -- measured 11:22, empty window printed
#  VACUOUS because LOGMAX defaulted to 0)
if ! grep -Eq 'checkpoint-pre:|checkpoint:' "$OUT"; then
  # A failed fold is not a restart. Two real failures measured 2026-11-13 on a rehearsal bus driven
  # into the failure (frames held in the WAL, DB frozen with chattr +i; shipped bus.py at aef2baf):
  #   15:47:01  janitor failed: attempt to write a readonly database
  #             checkpoint failed: src=backup err=disk I/O error          -> no checkpoint line
  #   15:47:29  checkpoint failed: src=backup err=disk I/O error only     -> no checkpoint line
  # The second one is why `checkpoint failed` must be in this alternation: it is the line CHECKPOINT()
  # ITSELF emits when the arms cannot fold, and without it a live unit whose fold failed graded
  # "NO CHECKPOINT LINE AT ALL -- restart or stopped unit", i.e. exit 4, a wrong cause with a wrong
  # next action. The cause is read out of the err/failure text and printed; it is NOT assumed to be a
  # lock (a lock was only the first measured cause; readonly and disk I/O both reached this arm).
  if grep -Eq 'janitor failed|backup failed|checkpoint failed' "$OUT"; then
    CAUSE="$(grep -Eo '(janitor failed|backup failed|checkpoint failed).*' "$OUT" \
      | sed -E 's/^[a-z ]+failed: *//; s/^src=[^ ]+ //; s/^err=//' | sort -u | paste -sd'@' - | sed 's/@/; /g')"
    echo "VERDICT: PAIR FAILED BEFORE ANY CHECKPOINT -- cause: ${CAUSE:-unnamed failure line}; the arms could not fold, so this window grades no arm and must not be read as an empty phase. A failed fold is not a restart, and this branch fires whatever the cause was: read the err text above, do not assume a lock."
    exit 5
  fi
  echo "VERDICT: NO CHECKPOINT LINE AT ALL in window and no failure line -- restart or stopped unit; recompute the phase from ExecMainStartTimestamp."
  exit 4
fi

# --- the two arms, first-pre vs second-post, read once for the ordering arm below.
JAN="$(grep -E 'checkpoint-pre: .*src=janitor' "$OUT" | tail -1)"
BAK="$(grep -E '^[^#]*checkpoint: .*src=backup' "$OUT" | tail -1)"
JLOG="$(printf '%s' "$JAN" | grep -oE 'log=[0-9]+' | head -1 | cut -d= -f2)"
JFOLD="$(printf '%s' "$JAN" | grep -oE 'folded=[0-9]+' | head -1 | cut -d= -f2)"
BLOG="$(printf '%s' "$BAK" | grep -oE 'log=[0-9]+' | head -1 | cut -d= -f2)"
BFOLD="$(printf '%s' "$BAK" | grep -oE 'folded=[0-9]+' | head -1 | cut -d= -f2)"

# --- frames gate: a collision on a zero-frame WAL cannot contend and grades nothing.
LOGMAX=$(grep -oE 'log=[0-9]+' "$OUT" | cut -d= -f2 | sort -n | tail -1)
BUSY_ANY=$(grep -cE 'busy=1' "$OUT" || true)
if [ "${LOGMAX:-0}" -le 0 ] && [ "${BUSY_ANY:-0}" -eq 0 ]; then
  # Name the cause when it is structural, not an idle hour: no -wal at capture and no
  # keeper means the bus's per-request last close deleted the sidecar, so log=0 is
  # forced by the connection model rather than by the traffic in this hour.
  if ! grep -Eq '^# wal [0-9]+' "$OUT" && grep -Eq '^# keeper pid=0' "$OUT"; then
    echo "VERDICT: VACUOUS (STRUCTURAL) -- no -wal sidecar at capture and no persistent holder: the bus opens/closes a connection per request and SQLite deletes the -wal on the last close, so backup/janitor TRUNCATEd a zero-frame database. busy=0 is forced on ANY build and this slot grades nothing. No persistent holder and no live kernel at the capture second: the keeper is retired (unit stopped, disabled, Wants= removed), so frame supply is opportunistic and this hourly pair grades frames only in the slots where they happen to be there."
    exit 0
  fi
  echo "VERDICT: VACUOUS -- slot ran with log=0 on every line (no frames to contend); busy=0 here is a property of an idle WAL, not of the build. Not evidence either way."
  exit 0
fi

# --- the TRUNCATE's own return, not only the PASSIVE's. Measured against the
# 2026-11-13T12:02:18 rehearsal capture (reh-1215): pre 0|1057|0 PINNED,
# post 1|1057|0 moved=0 -- the collision this instrument exists for -- and the
# older ladder graded it "real pin" with the word busy appearing nowhere, because
# the pre-PINNED arm was tested before any arm that reads the post line. A pin
# the TRUNCATE then folded (busy=0) and a pin the TRUNCATE could not fold
# (busy=1) are different facts and must not share a verdict.
POSTBUSY=$(grep -cE '^[^#]*checkpoint: busy=1' "$OUT" || true)
if grep -Eq 'checkpoint-pre: busy=1 log=-1.* BUSY' "$OUT"; then
  echo "VERDICT: split LIVE on the PRE line (busy=1 log=-1 tagged BUSY; <= 84092dd printed nothing there)"
elif grep -Eq '(checkpoint|checkpoint-pre): busy=1 log=-1.* PINNED' "$OUT"; then
  echo "VERDICT: split NOT LIVE -- busy=1 log=-1 still tagged PINNED"
elif grep -Eq 'checkpoint-pre: .* PINNED' "$OUT" && [ "${POSTBUSY:-0}" -gt 0 ]; then
  echo "VERDICT: split CONFIRMED at the TRUNCATE -- PRE PASSIVE reports frames un-folded (folded<log, PINNED) AND the TRUNCATE itself returned busy=1 with them still un-folded; that is the collision, not a healthy pin. Row-risk: frames sat across the fold. Reachable only with a holder inside a read transaction (rehearsal, never the live zero-frame slot)."
elif grep -Eq 'checkpoint-pre: .* PINNED' "$OUT"; then
  echo "VERDICT: real pin -- pre line reports folded<log; read folded vs log (this is a row-risk shape), but the TRUNCATE returned busy=0, so the fold completed on this pass."
elif grep -Eq '^[^#]*checkpoint: .* PINNED' "$OUT" && ! grep -Eq '^[^#]*checkpoint-pre: .* PINNED' "$OUT"; then
  echo "VERDICT: post-only pin (>= aef2baf shape: pre clean, post fails the TRUNCATE) -- row-risk, the PRE line proves the PASSIVE was healthy and the TRUNCATE is what failed"
elif [ -n "$JAN" ] && [ -n "$BAK" ] && [ "${JLOG:-0}" -gt 0 ] && [ "${JLOG:-0}" = "${JFOLD:--1}" ] && [ "${BLOG:-1}" = "0" ] && [ "${BFOLD:-1}" = "0" ]; then
  echo "VERDICT: ORDERED, NOT CONTENTED -- grades ordering only. First arm's PASSIVE saw frames and folded all of them (janitor pre folded==log=$JLOG>0), and the second arm's TRUNCATE found an empty WAL (backup post log=0 folded=0). Two arms that cannot overlap cannot contend by construction, so this window proves the fold sequence ran in order and proves NOTHING about contention; busy=0 here is a property of the serialization, not of the build. Measured 2/2 live slots with frames (12:15:19, 13:15:19)."
elif [ "${POSTBUSY:-0}" -gt 0 ]; then
  echo "VERDICT: TRUNCATE BUSY with no pin -- busy=1 on the checkpoint line while neither line reports folded<log; sqlite could not run the checkpoint (a concurrent writer, not a read mark) and it cost no frames."
elif grep -Eq 'checkpoint-pre:|checkpoint:' "$OUT"; then
  echo "VERDICT: NO COLLISION in window WITH FRAMES (log>0 seen) -- no arm reported busy=1 or a pin, but this shape is not the ordered serialization and does not establish that contention was exercised; read the pre/post pairs before quoting health."
else
  echo "VERDICT: UNGRADED -- checkpoint lines present but no arm matched (new line shape; fix the greps before trusting this file)."
fi
