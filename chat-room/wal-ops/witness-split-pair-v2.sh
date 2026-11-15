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
#        --print        : compute slot/window and exit without sleeping or capturing
#        --grade <file> : run THIS gate against a STORED capture, no sleep, no capture.
#                         Added 2026-11-14 so a branch can be proven on a real artifact
#                         instead of on an hour's wait; a rehearsal against a copy of the
#                         gate proves the copy, which is the trap this mode removes.
# CIFS note: systemd cannot exec this in place (EACCES on a nounix mount) -- schedule
# `bash /mnt/nas/host-ops/agent-chat/witness-split-pair-v2.sh`.
# Writes ~/agents/agent-chat/pairsplit2-<stamp>.log and prints one verdict line.

set -u
PRINT_ONLY=0
GRADE_FILE=""
case "${1:-}" in
  --print) PRINT_ONLY=1 ;;
  --grade) GRADE_FILE="${2:-}"; [ -s "$GRADE_FILE" ] || { echo "VERDICT: CANNOT GRADE -- --grade needs an existing, non-empty capture file (got '${2:-}')"; exit 2; } ;;
esac

# --- deadline, trailer and signals (added 2026-11-15 08:30, patch deadline-30).
# WHY A DEADLINE AND NOT A TRY COUNT: the flush-retry loop was 5 x (sleep 1 + journalctl),
# i.e. a DURATION that nobody printed, and its ceiling moved with the sleep's overshoot:
# slot+7.27..7.52. Measured 02:28: the last read ended 02:28:40.519272 and the backup's own
# line landed 02:28:40.533775 -- the loop lost the arm by 14.503 ms and printed "NO
# CHECKPOINT LINE AT ALL", a wrong cause with a wrong next action. The wait is now bounded
# by an absolute deadline and the lateness is a property of the ARM (its own slot token
# minus this script's slot second), so a missed slot names the number.
# DEADLINE < AGENT_CHAT_JANITOR_OFFSET IS STRUCTURAL, NOT MARGIN: the deadline is
# grid+DEADLINE; the janitor tick is grid+60.006 and MOVING AWAY at 0.005562 s/h (its own
# work; measured 10:29:33.764448 -> 07:29:33.881251). There is no override in
# ~/.config/hermes-fleet/env and the code default is 60. If that offset ever drops to
# <= DEADLINE the chase loop finds the JANITOR's line first, grades the janitor arm as the
# hour and never sees backup. That is the break -- not "a late janitor".
# WHY 30: backup lateness was 10.010883 at 07:28:43, drift 0.440486 s/h over 21 intervals.
# The 0.760681 at tick 1 is 0.644335 s of ExecMainStartTimestamp truncation (from
# ExecMainStartTimestampMonotonic) + 0.116346 s of thread startup: a START CONSTANT that
# redraws on restart, NOT a rate -- do not divide the first tick by the hour count.
# Headroom 19.99 s puts the last good slot between 11-17 00:28 and 08:28 depending on
# whether you take the last six ticks, the mean, or the load hour excluded; every rate in
# that band fails by the 09:28 slot on the 17th. Quote the band, never one instant.
DEADLINE="${WDEADLINE:-30}"

TOKEN_RE='src=backup slot=[0-9T.-]+'
arm_ep() { # slot token 20261114T152835.534123-0500 -> epoch seconds, 0 if unparsable
  date -d "$(printf '%s' "$1" | sed -E 's/^([0-9]{4})([0-9]{2})([0-9]{2})T([0-9]{2})([0-9]{2})([0-9]{2})\..*([+-][0-9]{4})$/\1-\2-\3 \4:\5:\6 \7/')" +%s 2>/dev/null || echo 0
}

_finished=0
LATE=""
# finish() writes EXACTLY ONE trailer, and ONLY on the capture path: in --grade mode OUT is
# the stored capture, so an unconditional trailer is the mutation this instrument forbids.
# It also no-ops on exits that happen before this run created its own file (exit 2 has no
# OUT; exit 3 runs before `: > "$OUT"`). Append with >> AFTER the tee pipeline closed and
# AFTER the flush-retry append: tee opens without O_APPEND, so a trailer written while the
# group still holds the fd is overwritten by tee's next write at its own offset. A signal
# that lands INSIDE the group can still lose that race; the journal's ExecMainStatus is
# then the honest witness and the trailer/status pair below is what catches it.
finish() {
  local rc="${1:-0}" why="${2:-OK}"
  if [ -n "${GRADE_FILE:-}" ] || [ -z "${OUT:-}" ] || [ ! -f "${OUT:-/nonexistent}" ] || [ "$_finished" = 1 ]; then
    return 0
  fi
  _finished=1
  printf '# VERDICT: rc=%s %s late=%s deadline=%ss\n' "$rc" "$why" "${LATE:-absent}" "$DEADLINE" >> "$OUT"
}
on_sig() {
  # MEASURED on bash 5.2.21: an UNTRAPPED TERM/INT/HUP still runs an EXIT trap, and inside
  # it $? is 0 (wait_rc 143/0/129, trap printing rc=0 every time); only KILL leaves no
  # trap. So "missing trailer means killed" is false in both directions, and an EXIT-only
  # trap would write rc=0 on a killed run. Trap the three, name the signal, and compare the
  # trailer's rc with systemd's ExecMainStatus (143/129/130 vs 0).
  finish "$((128 + $2))" "KILLED_$1"
  exit "$((128 + $2))"
}
trap 'on_sig TERM 15' TERM
trap 'on_sig INT 2' INT
trap 'on_sig HUP 1' HUP

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
SINCE="@$FROM"
OUT=""
if [ -n "$GRADE_FILE" ]; then
  # stored capture: no sleep, no clock arithmetic, no re-read. The window and the
  # instant are whatever the artifact's own header says; this mode grades only.
  OUT="$GRADE_FILE"
  echo "# grade-only: $OUT ($(wc -c <"$OUT") B, header: $(head -1 "$OUT"))"
else
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
# --- identity BEFORE the pipeline: the `# log` line stats $OUT from inside the group that
# is piped to `tee "$OUT"`, so the file is tee's creation and the stat races it. Measured
# 2026-11-14: 21:28:35 wrote ino 15344355, 22:28:35 wrote ino n/a with the same blob, while
# 1,000+ rehearsals (incl. 60 under the unit's own Nice=10/IO-idle scope) never reproduced
# it. Create the inode here and stat it once; tee then truncates that same inode (ino stable
# across truncate, verified), so the field measures instead of depending on who wins.
: > "$OUT"
_WLOG_INO="$(stat -c %i "$OUT" 2>/dev/null || echo n/a)"
SINCE="@$FROM"
REPO="$HOME/Agents/Projects/hermes-agent-fleet-toolkit"
{
  echo "# captured $(date -Is); slot $(date -d @$SLOT -Is); journal since $SINCE"
  # log identity: resolve the label a reader will stat, and print the inode -- the one
  # field that proves a copy is not the original (two inodes, one md5, 2026-11-14).
  echo "# log $(readlink -f "$OUT") ino $_WLOG_INO cwd $PWD"
  echo "# MainPID $(show MainPID) start $START_TXT NRestarts $(show NRestarts)"
  echo "# HEAD $(git -C "$REPO" rev-parse --short HEAD) bus.py $(md5sum "$REPO/chat-room/bus.py" | cut -d' ' -f1) tree: $(git -C "$REPO" status --porcelain | wc -l) modified"
  # --- witness identity: the instrument's OWN bytes, never the repo tip. `# HEAD` is a
  # name that expires: measured 2026-11-14, the 16:28:35 capture wrote `# HEAD 10a4811`
  # and master moved to 8761e94 eight minutes later, so that line invites a citation of a
  # commit that no longer resolves without the reflog. `git hash-object` is
  # content-addressed -- it needs no repo, no index and no object, so the same bytes hash
  # the same after the commit is gone, after GC, and on a copy that was never committed.
  # Measured on this deploy: exec copy and repo copy both -> c1474b47, md5 e461e2ba.
  # Cite THIS line and the md5 beside it; read `# HEAD` as lineage, not as identity.
  _self="$(readlink -f "${BASH_SOURCE[0]}")"
  echo "# WITNESS blob $(git hash-object -- "$_self" 2>/dev/null || echo n/a) size $(stat -c %s "$_self") md5 $(md5sum "$_self" | cut -d' ' -f1) path $_self"
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
  # Unit = DEEPEST .service OR .scope component of /proc/<pid>/cgroup; every user unit
  # sits under user@1000.service, so a first-component walk grades them all the same and
  # excludes nothing. .scope counts: measured 2026-11-13 19:36, a `systemd-run --user
  # --scope --unit=fooprobe` sits at .../user@1000.service/app.slice/fooprobe.scope, so a
  # service-only match printed `sleep@user@1000.service` -- the exact failure this walk is
  # supposed to fix. The unit file prescribes systemd-run for a measurement window, and a
  # --scope keeper is that shape; match both suffixes, deepest first. A process with no
  # unit of its own lives in the session slice and resolves to user@1000.service: read that
  # as "no unit of its own", not as a unit -- the /proc cgroup cannot say more.
  if [ -n "$_pids" ]; then
    _cls=""
    for _p in $_pids; do
      _comm="$(cat "/proc/$_p/comm" 2>/dev/null || echo '?')"
      _unit="$(awk -F/ '{for(i=NF;i>0;i--) if ($i ~ /[.]service$/ || $i ~ /[.]scope$/) {print $i; exit}}' "/proc/$_p/cgroup" 2>/dev/null)"
      _cls="$_cls ${_p}:${_comm}@${_unit:-gone}"
    done
    echo "# holder-class$_cls"
  fi
  echo "# keeper $(KEEP=$(systemctl --user show -p MainPID --value hermes-agent-chat-wal-keeper.service 2>/dev/null || echo 0); echo "pid=${KEEP:-0}") -- keeper MainPID only (0 = retired); frame supply is a property of ANY live holder at the slot second, named on the # holders line above, not of the keeper"
} | tee "$OUT"
fi

# --- chase to the DEADLINE, then say so (2026-11-15; replaces 5 x (sleep 1 + journalctl)).
# The capture instant is slot+2.16..2.41 (the sleep to slot+2 overshoots); the old loop was
# 5 x (1 s + journalctl 0.01 s) = 5.11 s, so its ceiling was slot+7.27..7.52 and the 02:28
# arm at slot+7.5338 lost by 14.5 ms. This loop re-reads the same window until the absolute
# deadline, so a line at slot+12 is caught and a line that never comes is NAMED (exit 6)
# instead of being reported as a restart. It changes no verdict on a window that has a line.
if [ -z "$GRADE_FILE" ] && ! grep -Eq 'checkpoint-pre:|checkpoint:' "$OUT"; then
  _tries=0
  _dl_ns=$(( (SLOT + DEADLINE) * 1000000000 ))
  while [ "$(date +%s%N)" -lt "$_dl_ns" ] && ! grep -Eq 'checkpoint-pre:|checkpoint:' "$OUT"; do
    sleep 1; _tries=$(( _tries + 1 ))
    journalctl --user -u "$SVC" --since "$SINCE" --no-pager \
      | grep -E 'checkpoint-pre:|checkpoint:|checkpoint failed|janitor failed|backup failed' >> "$OUT"
  done
  [ "$_tries" -gt 0 ] && echo "# flush-retry: ${_tries} read(s) of the same window; deadline slot+${DEADLINE}s" >> "$OUT"
fi

# --- the arm's lateness, read from THIS artifact's own token (never from BT at the grading
# block, which is `tail -1` of the tick log: on expiry the arm never ticked, so BT is the
# PREVIOUS hour and the field would print ~3600 s; in --grade it is whatever the log says at
# grading time). Every checkpoint-pre: line carries its own slot= token, so subtract that.
_BTOK="$(grep -oE "$TOKEN_RE" "$OUT" 2>/dev/null | tail -1 | sed 's/.*slot=//')"
# Reference second: the live grid for a capture, but for --grade the ARTIFACT's own slot
# from its header -- SLOT in grade mode is the NEXT slot from the clock, so subtracting it
# from a stored token would print a negative number of hours.
_ref="$SLOT"
if [ -n "$GRADE_FILE" ]; then
  _ref="$(date -d "$(sed -nE 's/^# captured .*; slot ([^;]+);.*$/\1/p' "$OUT" | head -1)" +%s 2>/dev/null || echo "$SLOT")"
fi
if [ -n "$_BTOK" ]; then
  _frac="$(printf '%s' "$_BTOK" | sed -nE 's/^[0-9]{8}T[0-9]{6}\.([0-9]+)[+-][0-9]{4}$/\1/p')"
  [ -z "$_frac" ] && _frac=0
  LATE="$(awk -v a="$(arm_ep "$_BTOK")" -v f="$_frac" -v b="$_ref" 'BEGIN{printf "%.3f", a + f / (10 ^ length(f)) - b}')"
  echo "# arm backup slot=${_BTOK} late=${LATE}s (arm token - slot second, deadline ${DEADLINE}s)"
else
  LATE="absent"
  echo "# arm backup slot=absent late=absent (no checkpoint line in this artifact)"
fi

# --- presence first: an empty window is a witness failure, not a healthy database.
# (the vacuous gate below must not swallow it -- measured 11:22, empty window printed
#  VACUOUS because LOGMAX defaulted to 0)
if ! grep -Eq 'checkpoint-pre:|checkpoint:' "$OUT"; then
  # --- expiry is its own cause, never exit 4. The old exit 4 text ("restart or stopped
  # unit") was wrong for every miss after the lateness outgrew the 5-try ceiling: measured
  # 02:28 (line at slot+7.5338) and every slot after it. rc 6 carries the number, so the
  # journal and the trailer agree that the arm is late rather than absent.
  _dl_ns=${_dl_ns:-0}
  if [ -z "$GRADE_FILE" ] && [ "$_dl_ns" -gt 0 ] && [ "$(date +%s%N)" -ge "$_dl_ns" ]; then
    echo "VERDICT: DEADLINE EXPIRED -- no checkpoint-pre:/checkpoint: line by slot+${DEADLINE}s. This is NOT a restart and NOT an empty phase: the arm is past the deadline. Re-derive the lateness series from bus-checkpoint.log before grading this hour, and do not read the slot as evidence either way."
    finish 6 DEADLINE_EXPIRED
    exit 6
  fi
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
    finish 5 PAIR_FAILED
    exit 5
  fi
  echo "VERDICT: NO CHECKPOINT LINE AT ALL in window and no failure line -- restart or stopped unit; recompute the phase from ExecMainStartTimestamp."
  finish 4 NO_LINE
  exit 4
fi

# --- the two arms, first-pre vs second-post, read once for the ordering arm below.
JAN="$(grep -E 'checkpoint-pre: .*src=janitor' "$OUT" | tail -1)"
BAK="$(grep -E '^[^#]*checkpoint: .*src=backup' "$OUT" | tail -1)"
JLOG="$(printf '%s' "$JAN" | grep -oE 'log=[0-9]+' | head -1 | cut -d= -f2)"
JFOLD="$(printf '%s' "$JAN" | grep -oE 'folded=[0-9]+' | head -1 | cut -d= -f2)"
BLOG="$(printf '%s' "$BAK" | grep -oE 'log=[0-9]+' | head -1 | cut -d= -f2)"
BFOLD="$(printf '%s' "$BAK" | grep -oE 'folded=[0-9]+' | head -1 | cut -d= -f2)"
# which src arms are actually IN this capture's window (order-independent, so a reversed
# window cannot be read as the old janitor-then-backup serialization)
SRCS="$(grep -oE 'src=[a-z]+' "$OUT" | cut -d= -f2 | sort -u | tr '\n' ' ' | sed 's/ $//')"
NARMS=$(printf '%s' "$SRCS" | wc -w)
# measured arm offset, read from the tick log rather than assumed: the janitor offset
# change (census, wal-census.md) moved the second arm ~60 s past the first, which is why
# both arms can no longer appear in one 7 s window.
# arm_ep() is defined with the deadline/trailer helpers at the top of this script; the
# lateness field needs it before this block runs.
BT="$(grep -oE 'src=backup slot=[0-9T.-]+' "$CPLOG" 2>/dev/null | tail -1 | sed 's/.*slot=//')"
JT="$(grep -oE 'src=janitor slot=[0-9T.-]+' "$CPLOG" 2>/dev/null | tail -1 | sed 's/.*slot=//')"
if [ -n "$BT" ] && [ -n "$JT" ]; then
  OFF=$(( $(arm_ep "$JT") - $(arm_ep "$BT") ))
  OFFSET_TXT="latest slot pair in the tick log, backup $BT -> janitor $JT = ${OFF}s apart (not this capture's hour unless the tokens say so)"
else
  OFFSET_TXT="unmeasurable (tick log has no src=backup and src=janitor token pair; CPLOG=$CPLOG)"
fi

# --- frames gate: a collision on a zero-frame WAL cannot contend and grades nothing.
LOGMAX=$(grep -oE 'log=[0-9]+' "$OUT" | cut -d= -f2 | sort -n | tail -1)
BUSY_ANY=$(grep -cE 'busy=1' "$OUT" || true)
if [ "${LOGMAX:-0}" -le 0 ] && [ "${BUSY_ANY:-0}" -eq 0 ]; then
  # Name the cause when it is structural, not an idle hour: no -wal at capture and no
  # keeper means the bus's per-request last close deleted the sidecar, so log=0 is
  # forced by the connection model rather than by the traffic in this hour.
  if ! grep -Eq '^# wal [0-9]+' "$OUT" && grep -Eq '^# keeper pid=0' "$OUT"; then
    echo "VERDICT: VACUOUS (STRUCTURAL) -- no -wal sidecar at capture and no persistent holder: the bus opens/closes a connection per request and SQLite deletes the -wal on the last close, so backup/janitor TRUNCATEd a zero-frame database. busy=0 is forced on ANY build and this slot grades nothing. No persistent holder and no live kernel at the capture second: the keeper is retired (unit stopped, disabled, Wants= removed), so frame supply is opportunistic and this hourly pair grades frames only in the slots where they happen to be there."
    finish 0 VACUOUS_STRUCTURAL
    exit 0
  fi
  echo "VERDICT: VACUOUS -- slot ran with log=0 on every line (no frames to contend); busy=0 here is a property of an idle WAL, not of the build. Not evidence either way."
  finish 0 VACUOUS
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
elif [ "$NARMS" = "1" ]; then
  # ADDED 2026-11-14. The ORDERED, NOT CONTENTED branch below required BOTH arms in one
  # 7 s window. Since the janitor offset change the arms are ~60 s apart (measured, #4813
  # 15:29:33 vs 15:28:35), so that branch is unreachable from a live capture -- every slot
  # fell through to "NO COLLISION ... weaker than the branch it can no longer reach", which
  # is honest but never says WHY. This branch says why, and grades the one arm that is in
  # the window instead of pretending two arms were compared.
  echo "VERDICT: SINGLE-ARM at the offset -- window holds one src arm only (src=${SRCS:-?}); read that arm's own pre/post pair above. Measured arm offset from the tick log: ${OFFSET_TXT}. Two arms that far apart cannot overlap on the hourly grid, so this slot grades frame supply and this arm's fold, NOT contention between the arms -- the serialization is the janitor offset, not the bus. Do not cite this slot as evidence the split is fixed or that it is broken."
elif [ "$NARMS" -ge 2 ]; then
  # The retired branch's shape, now treated as NEWS: both arms in one 7 s window means the
  # offset is gone (or the capture is stale/duplicated). It is not a health grade, and the
  # old wording would have named the wrong first arm on a reversed window (#4813 item 5b).
  echo "VERDICT: SPLIT-WINDOW -- both arms (src=${SRCS}) appear inside one 7 s window, so the janitor offset is absent or this capture is stale/duplicated. This is not the hourly serialization the ledger has been grading: re-derive the grid from bus-checkpoint.log (slot tokens) before grading, and do not read the first line as 'the first arm' -- name the arm, not the position."
elif [ "${POSTBUSY:-0}" -gt 0 ]; then
  echo "VERDICT: TRUNCATE BUSY with no pin -- busy=1 on the checkpoint line while neither line reports folded<log; sqlite could not run the checkpoint (a concurrent writer, not a read mark) and it cost no frames."
elif grep -Eq 'checkpoint-pre:|checkpoint:' "$OUT"; then
  echo "VERDICT: NO COLLISION in window WITH FRAMES (log>0 seen) -- no arm reported busy=1 or a pin, but this shape is not the ordered serialization and does not establish that contention was exercised; read the pre/post pairs before quoting health."
else
  echo "VERDICT: UNGRADED -- checkpoint lines present but no arm matched (new line shape; fix the greps before trusting this file)."
fi

# --- the trailer, on every normal path that produced an artifact. This is the line the
# census and the per-slot check read: `grep -c '^# VERDICT: rc='` must be 1 and it must be
# the last line. Before this patch NO verdict reached the file at all (every one was echoed
# outside the tee group), so 42 of 61 existing captures end on `# keeper ` and 17 on
# `# flush-retry:` -- the rule needs a cutover head, not a single regex.
finish 0 GRADED
