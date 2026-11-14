#!/usr/bin/env bash
# rehearse-run-failure.sh -- prove check-grade-timer.sh's RUN FAILED leg.
#
# WHY: an armed timer says the NEXT run will start; it says nothing about the
# last one. Measured 2026-11-14 11:28:41: the 11:05 grade run died with a bash
# syntax error (grade-slot.sh rewritten in place while the run had it open), the
# timer re-armed for 12:05 -- and every earlier guard leg read healthy, so the
# 11:28 slot went ungraded with no line in the guard log. note_failed() closes
# that by checking the activated unit's failure state, observe-only.
#
# Arms:
#   A  last run exited 0, timer armed         -> rc 0, log EMPTY, no flag
#   B  last run exited 7, timer armed         -> rc 0 (not a timer fault), ONE
#                                                RUN FAILED line naming rc=7
#   C  same failed run, next tick             -> rc 0, NO new line (dedupe)
#   D  a later run exits 0                    -> rc 0, dedupe cleared
#   E  then a new run exits 7                 -> rc 0, ONE new line (count 2)
#   F  the failing unit is still running      -> no RUN FAILED line (a live run
#                                                is never blamed for a stale one)
#   G  guard never restarts the timer         -> ArmedMono unchanged throughout
#
# rc: 0 all arms passed, 1 otherwise.
set -u
GUARD=${GUARD:-$HOME/Agents/Projects/hermes-agent-fleet-toolkit/chat-room/wal-ops/check-grade-timer.sh}
TIMER=guardfail.timer
SVC=guardfail.service
UNITDIR=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
TMP=$(mktemp -d)
LOG=$TMP/guard.log
FLAG=$TMP/guard.log.failed
RUNNER=$TMP/runner.sh
fails=0

cleanup() {
    systemctl --user stop "$SVC" "$TIMER" >/dev/null 2>&1 || true
    rm -f "$UNITDIR/$TIMER" "$UNITDIR/$SVC"
    systemctl --user daemon-reload >/dev/null 2>&1 || true
    rm -rf "$TMP"
}
trap cleanup EXIT

ok()  { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails+1)); }
count() { grep -cF -- 'RUN FAILED' "${1:-$LOG}" 2>/dev/null || true; }
expect_count() { local n; n=$(count); [ "$n" = "$1" ] && ok "RUN FAILED count=$1" || bad "RUN FAILED count=$n, wanted $1"; }
[ -f "$GUARD" ] || { echo "FAIL guard not found: $GUARD"; exit 1; }
bash -n "$GUARD" || { echo "FAIL syntax"; exit 1; }

# --- scratch units: the service's exit code is baked into $RUNNER -------------
# (systemctl --user start does NOT pass the caller's environment, so RC=7 in this
# shell never reaches the unit -- the code has to live in the script it runs.)
set_rc() { printf '#!/bin/sh\nexit %s\n' "$1" > "$RUNNER"; chmod +x "$RUNNER"; }
set_rc 0
cat > "$UNITDIR/$SVC" <<EOF
[Unit]
Description=rehearsal: stand-in whose exit code is controlled by the driver
[Service]
Type=oneshot
ExecStart=$RUNNER
RemainAfterExit=no
EOF
cat > "$UNITDIR/$TIMER" <<EOF
[Unit]
Description=rehearsal: stand-in for grade-slot.timer
[Timer]
OnCalendar=*:*:00/15
Unit=$SVC
[Install]
WantedBy=timers.target
EOF
systemctl --user daemon-reload >/dev/null 2>&1
systemctl --user start "$TIMER" >/dev/null 2>&1

run_guard() { TIMER=$TIMER LOG=$LOG FAILED_FLAG=$FLAG STUCK_FLAG=$TMP/guard.log.stuck bash "$GUARD" 2>/dev/null; echo $?; }
mono() { systemctl --user show "$TIMER" -p ActiveEnterTimestampMonotonic --value 2>/dev/null; }
# fire <rc>: run the unit once with that exit code
fire() { set_rc "$1"; systemctl --user start "$SVC" >/dev/null 2>&1 || true; }

# A -- a run that exits 0 is silent
fire 0
sleep 1
rc=$(run_guard); [ "$rc" = 0 ] && ok "A rc=0 after a clean run" || bad "A rc=$rc"
[ ! -s "$LOG" ] && ok "A log empty (silence keeps meaning nothing happened)" || bad "A log not empty: $(wc -l < "$LOG") lines"

# B -- a run that exits 7 is reported once
fire 7
sleep 1
m0=$(mono)
rc=$(run_guard); [ "$rc" = 0 ] && ok "B rc=0 (a failed run is not a timer fault)" || bad "B rc=$rc"
expect_count 1
grep -qF 'exited rc=7' "$LOG" && ok "B names the exit status" || bad "B does not name rc=7"
m1=$(mono); [ "$m0" = "$m1" ] && ok "B guard did not touch the timer" || bad "B timer mono moved $m0 -> $m1"

# C -- same run, next tick: deduped
rc=$(run_guard); [ "$rc" = 0 ] && ok "C rc=0" || bad "C rc=$rc"
expect_count 1

# D -- a later clean run clears the dedupe
fire 0
sleep 1
rc=$(run_guard); [ "$rc" = 0 ] && ok "D rc=0 after a clean run" || bad "D rc=$rc"
expect_count 1
[ ! -s "$FLAG" ] && ok "D dedupe cleared" || bad "D dedupe still set: $(cat "$FLAG")"

# E -- a new failure after a clean run is a NEW line
fire 7
sleep 1
rc=$(run_guard); [ "$rc" = 0 ] && ok "E rc=0" || bad "E rc=$rc"
expect_count 2

# F -- a live run owns the present; the stale failure is not re-announced
cat > "$UNITDIR/$SVC" <<EOF
[Unit]
Description=rehearsal: long run, still active
[Service]
Type=oneshot
ExecStart=/bin/sleep 600
RemainAfterExit=no
EOF
systemctl --user daemon-reload >/dev/null 2>&1
rm -f "$FLAG"
systemctl --user start "$SVC" >/dev/null 2>&1 &
sleep 2
before=$(count)
rc=$(run_guard); [ "$rc" = 0 ] && ok "F rc=0 while a run is live" || bad "F rc=$rc"
[ "$(count)" = "$before" ] && ok "F no RUN FAILED line for a live run" || bad "F emitted a line while busy"

printf '\n%d line(s) in the guard log\n' "$(count)"
if [ "$fails" -eq 0 ]; then echo 'RESULT PASS (0 failed arms)'; exit 0; fi
echo "RESULT FAIL ($fails failed arms)"; exit 1
