#!/usr/bin/env bash
# wake-quiet-gate.sh -- the dispatcher restart predicate, as code instead of a paragraph.
#
# WHY THIS FILE EXISTS
# A dispatcher restart is the only thing that puts a committed wake_dispatcher.py into
# force (a running python process never reloads). The gate on that restart used to be a
# count: "restart when live turns == 0". Measured 2026-11-16 over 152 wake rows since
# 2026-11-15 18:00: min 38.9 s, p50 310.6 s, p90 947.4 s, max 2,323.6 s; 28 over 600 s,
# 21 over the 900 s WAKE_TIMEOUT budget. So, measured consequences:
#
#   1. A count threshold ABOVE zero is never safe: MAX_CONCURRENCY is 3 and three live
#      turns (game/buzzy/frodo at 03:02-03:08) is a legitimate state, not drift.
#   2. A TIME threshold set at the 900 s budget is wrong 21 times in 152 turns, because
#      p90 (947 s) already exceeds it.
#
# The honest predicate is two clauses, and this file is both of them:
#
#   clause 1  live_turns == 0        -- the cgroup counter, delegated (never retyped).
#   clause 2  no wake activity for N  -- N above the LONGEST MEASURED turn, not the budget.
#
# N defaults to 2400 s: it covers the observed max of 2,323.6 s with margin, and it costs
# nothing, because the only restart it delays is a restart with no turn to protect.
#
# CLAUSE 1 IS DELEGATED, DELIBERATELY
# `restart-dispatcher-when-no-turns.sh --count` owns the live-turn predicate: children of
# the live MainPID's cgroup, argv shape-matched in both launch shapes, -1 = cannot tell.
# Retyping that regex in this file is the defect that script's header documents (a
# relaunch-wrapper turn carries its argv as python source text, so the space-separated
# shape reads 0 forever). One predicate, one owner.
#
# THE GATE CANNOT BE CLOSED FROM INSIDE A TURN
# A room turn is itself a process in the unit's cgroup, so `--count` and
# /health active_turns both include the observer. Measured 2026-11-16 03:45:36 from a
# frodo turn: --count 2 == health 2, and one of the two pids was the turn asking
# (1191982, argv '-p','frodo','-z'). A turn that evaluates its own gate reads >= 1
# forever, indistinguishable from a busy fleet. So this script is meant to be armed as a
# waiter (systemd-run --user --unit=...), never run by the turn that wants the restart.
# Grading it by hand from a turn is still useful -- it will just say busy, honestly.
#
# WHY QUIET IS MEASURED FROM THE JOURNAL AND NOT FROM /health
# /health reports what the running process knows: `active` reaches 0 while a drained
# turn's child is still working, and it is empty at startup, before the spill is loaded.
# The journal is the record of what the dispatcher did -- every turn's completion carries
# dur_s/rc/posted_id, and every arrival carries a POST /wake line. So clause 2 reads the
# last activity event, not a counter.
#
# The dispatcher logs no explicit "turn start" event (wake_dispatcher.py:843 spawns with
# Popen and logs only at :895 on completion). The arrival leg is therefore the http
# POST /wake line, which is the event that causes the start; park/retry/requeue events
# bridge the gap while a turn runs. A GET /health line is our own probe and never counts.
#
# Usage:
#   wake-quiet-gate.sh                  # grade once, change nothing
#   wake-quiet-gate.sh --wait [secs]    # poll until safe or deadline (default 14400)
#   wake-quiet-gate.sh --restart [secs] # --wait, then restart and print the new MainPID
#   wake-quiet-gate.sh --json           # one JSON object on stdout, no log line
#
# Env: WAKE_QUIET_S (default 2400), WAKE_QUIET_LOG (default below)
#
# Exit: 0 safe (or restarted) | 1 not safe | 2 cannot tell | 3 gave up waiting
#
# Committed 100644 like its neighbours: invoke as `bash <path>`, never `./<path>` -- the
# NAS CIFS mount is nounix and the repo copy is a reader, not the exec leg.
set -uo pipefail

UNIT=hermes-wake-dispatcher.service
WALOPS=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
COUNT_SCRIPT=${WAKE_COUNT_SCRIPT:-$WALOPS/restart-dispatcher-when-no-turns.sh}
LOG=${WAKE_QUIET_LOG:-/mnt/nas/host-ops/agent-chat/wake-quiet-gate.log}
QUIET_S=${WAKE_QUIET_S:-2400}

# Every event that means a wake is in flight, or just was. `wake` is the completion
# (dur_s/rc/posted_id); the rest are arrivals, parks, retries and skips. Adding an event
# here can only make the gate more conservative; removing one is how the gate gets wrong.
ACTIVITY_EVENTS='wake wake_over_budget wake_parked park_requeue park_still_busy gate_held gate_held_busy wake_retry_spilled wake_abandoned spill_repump spill_requeue_held spill_resume spill_load wake_answered_in_room wake_skip_answered reply_skipped_already_posted reply_post_check_failed reply_failed'

json=0
mode=grade
deadline_s=14400
while [ $# -gt 0 ]; do
  case "$1" in
    --json) json=1 ;;
    --wait) mode=wait; [ $# -gt 1 ] && [[ ${2:-} =~ ^[0-9]+$ ]] && { deadline_s=$2; shift; } ;;
    --restart) mode=restart; [ $# -gt 1 ] && [[ ${2:-} =~ ^[0-9]+$ ]] && { deadline_s=$2; shift; } ;;
    -h|--help) sed -n '2,80p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "wake-quiet-gate: unknown arg $1" >&2; exit 2 ;;
  esac
  shift
done

live_turns() {
  if [ ! -r "$COUNT_SCRIPT" ]; then printf '%s\n' -1; return; fi
  local n
  n=$(bash "$COUNT_SCRIPT" --count 2>/dev/null)
  [[ $n =~ ^-?[0-9]+$ ]] || n=-1
  printf '%s\n' "$n"
}

# Prints: "<last_activity_epoch|-> <quiet_seconds|->" or "ERR" when the journal cannot
# be read. A zero-length journal window is a legitimate quiet window; a FAILED read is
# not, and must never be written as quiet (a parse error wearing evidence's clothes).
activity_since() {
  local now=$1 window=$2
  local out rc
  out=$(journalctl --user -u "$UNIT" --since "@$((now - window))" -o cat 2>/dev/null); rc=$?
  if [ "$rc" -ne 0 ]; then printf 'ERR\n'; return; fi
  printf '%s\n' "$out" | ACTIVITY_EVENTS="$ACTIVITY_EVENTS" NOW="$now" python3 -c '
import json, os, sys
events = set(os.environ["ACTIVITY_EVENTS"].split())
now = float(os.environ["NOW"])
last = None
for line in sys.stdin:
    line = line.strip()
    if not line.startswith("{"):
        continue
    try:
        d = json.loads(line)
    except ValueError:
        continue
    evt = d.get("evt")
    counts = evt in events
    # An arrival is the event that causes a spawn; the completion has its own evt.
    if evt == "http" and "POST /wake" in str(d.get("line", "")):
        counts = True
    if not counts:
        continue
    ts = d.get("ts")
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        continue
    if last is None or ts > last:
        last = ts
print("%s %s" % ("-" if last is None else repr(last),
                 "-" if last is None else "%.0f" % max(0.0, now - last)))
'
}

grade() {
  # echoes: "verdict live_turns quiet_s need_s last_ts"
  local now lt act lts qs verdict
  now=$(date +%s)
  lt=$(live_turns)
  if [ "$lt" = "-1" ]; then
    printf 'cannot-tell -1 - %s -\n' "$QUIET_S"
    return 2
  fi
  if [ "$lt" -gt 0 ]; then
    printf 'busy %s - %s -\n' "$lt" "$QUIET_S"
    return 1
  fi
  act=$(activity_since "$now" $((QUIET_S + 120)))
  if [ "$act" = "ERR" ]; then
    printf 'cannot-tell 0 - %s -\n' "$QUIET_S"
    return 2
  fi
  lts=${act%% *}; qs=${act##* }
  if [ "$qs" = "-" ]; then
    # No activity line inside (N + 120 s): the quiet clause is satisfied by absence,
    # and the window is what makes the absence a measurement rather than a guess.
    printf 'safe 0 %s %s -\n' "$((QUIET_S + 120))" "$QUIET_S"
    return 0
  fi
  if [ "$qs" -ge "$QUIET_S" ]; then
    printf 'safe 0 %s %s %s\n' "$qs" "$QUIET_S" "$lts"
    return 0
  fi
  printf 'quiet-not-reached 0 %s %s %s\n' "$qs" "$QUIET_S" "$lts"
  return 1
}

emit() {
  local verdict=$1 lt=$2 qs=$3 need=$4 lts=$5 rc=$6
  if [ "$json" -eq 1 ]; then
    printf '{"verdict": "%s", "live_turns": %s, "quiet_s": "%s", "need_s": %s, "last_activity_ts": "%s", "unit": "%s", "rc": %s}\n' \
      "$verdict" "$lt" "$qs" "$need" "$lts" "$UNIT" "$rc"
  else
    printf '%s gate verdict=%s live_turns=%s quiet_s=%s need_s=%s last_activity_ts=%s unit=%s rc=%s\n' \
      "$(date -Is)" "$verdict" "$lt" "$qs" "$need" "$lts" "$UNIT" "$rc" | tee -a "$LOG"
  fi
}

one_grade() {
  local line rc
  line=$(grade); rc=$?
  # shellcheck disable=SC2086
  emit $line "$rc"
  return $rc
}

case "$mode" in
  grade)
    one_grade
    exit $?
    ;;
esac

# Wait mode. Every terminal branch writes a line: a start line with no terminal line
# means "still inside the loop", and that is the only honest reading of silence.
DEADLINE=$(( $(date +%s) + deadline_s ))
printf '%s armed mode=%s need_s=%s deadline=%s live_turns=%s\n' \
  "$(date -Is)" "$mode" "$QUIET_S" "$(date -d "@$DEADLINE" -Is)" "$(live_turns)" >>"$LOG"
while :; do
  line=$(grade); rc=$?
  if [ "$rc" -eq 0 ]; then
    emit $line 0
    break
  fi
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    emit $line 3
    printf '%s GAVE UP after %ss (%s)\n' "$(date -Is)" "$deadline_s" "$line" >>"$LOG"
    exit 3
  fi
  sleep 5
done

if [ "$mode" = "restart" ]; then
  old=$(systemctl --user show -p MainPID --value "$UNIT")
  systemctl --user restart "$UNIT" || { echo "restart failed" >>"$LOG"; exit 1; }
  sleep 3
  printf '%s restarted old_MainPID=%s new_MainPID=%s health=%s\n' "$(date -Is)" "$old" \
    "$(systemctl --user show -p MainPID --value "$UNIT")" \
    "$(curl -s --max-time 5 http://127.0.0.1:8095/health)" >>"$LOG"
fi
exit 0
