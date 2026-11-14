#!/usr/bin/env bash
# rehearse-busy-bound.sh -- prove check-grade-timer.sh's busy() bound.
#
# The guard treats "the unit this timer activates is still running" as HEALTHY
# (rc 0) because restarting a timer under a live run wedges it. That branch used
# to be UNBOUNDED: a grade run that hangs and never goes inactive keeps
# list-timers at next:null forever, and the guard reads healthy while the room
# goes quiet -- the failure it exists to catch, wearing the mask of the condition
# it must not act on. This rehearses the bound against scratch units, never the
# live grade-slot pair.
#
# Arms:
#   A  run active, elapsed < STUCK_AFTER        -> rc 0, log empty, no flag
#   B  run active, elapsed >= STUCK_AFTER       -> rc 0, ONE STUCK? line, flag
#   C  same stuck run again (< 1 h later)       -> rc 0, NO new line (cadence)
#   D  timer never restarted under the run      -> ArmedMono unchanged B -> D
#   E  run released, timer re-armed             -> rc 0, RUN ENDED line, flag gone
#   F  unknown timer (no activates unit)        -> rc 2 REPAIR FAILED, no hang,
#                                                  no STUCK/ENDED noise
#   G  activated unit stopped mid-run, flag left -> rc 0, RUN ENDED via healthy path
#
# rc: 0 all arms passed, 1 otherwise.
set -u
GUARD=${GUARD:-$HOME/Agents/Projects/hermes-agent-fleet-toolkit/chat-room/wal-ops/check-grade-timer.sh}
TIMER=guardtest.timer
SVC=guardtest.service
UNITDIR=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
TMP=$(mktemp -d)
LOG=$TMP/guard.log
FLAG=$TMP/guard.log.stuck
fails=0

cleanup() {
    systemctl --user stop "$SVC" "$TIMER" >/dev/null 2>&1 || true
    rm -f "$UNITDIR/$TIMER" "$UNITDIR/$SVC"
    systemctl --user daemon-reload >/dev/null 2>&1 || true
    rm -rf "$TMP"
}
trap cleanup EXIT

ok()   { printf 'ok   %s\n' "$*"; }
bad()  { printf 'FAIL %s\n' "$*"; fails=$((fails+1)); }
# expect_contains <label> <needle> [file]
expect_contains() { grep -qF -- "$2" "${3:-$LOG}" && ok "$1" || bad "$1 (missing: $2)"; }
expect_count()    { local n f=${3:-$LOG}; n=$(grep -cF -- "$2" "$f" 2>/dev/null || true); [ "$n" = "$1" ] && ok "$f count=$1" || bad "$f count=$n, wanted $1"; }
[ -f "$GUARD" ] || { echo "FAIL guard not found: $GUARD"; exit 1; }
bash -n "$GUARD" || { echo "FAIL syntax"; exit 1; }

# --- scratch units -----------------------------------------------------------
cat > "$UNITDIR/$SVC" <<'EOF'
[Unit]
Description=rehearsal: long-running stand-in for grade-slot.service
[Service]
Type=oneshot
ExecStart=/bin/sleep 600
RemainAfterExit=no
EOF
cat > "$UNITDIR/$TIMER" <<'EOF'
[Unit]
Description=rehearsal: stand-in for grade-slot.timer
[Timer]
OnCalendar=*-*-* *:*:00/15
Unit=guardtest.service
[Install]
WantedBy=timers.target
EOF
systemctl --user daemon-reload || { echo "FAIL daemon-reload"; exit 1; }
# ORDER MATTERS: `next` goes null only when the elapse ARRIVES while the unit is
# already active (measured: a timer started with its elapse in the future keeps
# next non-null for the whole run). So start the run first, then the timer.
systemctl --user start --no-block "$SVC" || { echo "FAIL service start"; exit 1; }
systemctl --user start "$TIMER" || { echo "FAIL timer start"; exit 1; }

next_of() {
    systemctl --user list-timers --output=json --all "$TIMER" \
      | python3 -c 'import json,sys;print([r.get("next") for r in json.load(sys.stdin) if r.get("unit")=="guardtest.timer"][0])'
}

# precondition: a live run must really make the timer read next:null
next=""; for _ in $(seq 1 40); do next=$(next_of); [ "$next" = None ] && break; sleep 1; done
[ "$next" = None ] && ok "precondition: next:null once the elapse passed under a live run" \
                   || bad "precondition: next=$next after 40 s, expected None"
mono_before=$(systemctl --user show "$SVC" -p ExecMainStartTimestampMonotonic --value)
timer_mono_before=$(systemctl --user show "$TIMER" -p ActiveEnterTimestampMonotonic --value)

# --- A: under the threshold, silent -----------------------------------------
TIMER=$TIMER LOG=$LOG STUCK_AFTER=30 STUCK_FLAG=$FLAG bash "$GUARD" 2>/dev/null; rc=$?
[ "$rc" = 0 ] && ok "A rc=0 under threshold" || bad "A rc=$rc"
[ ! -s "$LOG" ] && ok "A log empty (silence keeps meaning nothing happened)" || bad "A log not empty"
[ ! -f "$FLAG" ] && ok "A no flag" || bad "A flag written under threshold"

# --- B: past the threshold, one line, still rc 0 ----------------------------
sleep 3
TIMER=$TIMER LOG=$LOG STUCK_AFTER=2 STUCK_FLAG=$FLAG bash "$GUARD" 2>/dev/null; rc=$?
[ "$rc" = 0 ] && ok "B rc=0 while a run is stuck (never restart under a run)" || bad "B rc=$rc"
expect_count 1 "STUCK? guardtest.service still active after"
expect_contains "B names the threshold" "threshold 2s"
expect_contains "B says it did not act" "not restarting under a run"
[ -f "$FLAG" ] && ok "B flag written" || bad "B flag missing"

# --- C: second tick, cadence suppresses a repeat -----------------------------
TIMER=$TIMER LOG=$LOG STUCK_AFTER=2 STUCK_FLAG=$FLAG bash "$GUARD" 2>/dev/null; rc=$?
[ "$rc" = 0 ] && ok "C rc=0" || bad "C rc=$rc"
expect_count 1 "STUCK?"   # unchanged: the hourly re-log has not come due

# --- D: the guard did not restart the timer under the live run --------------
timer_mono_after=$(systemctl --user show "$TIMER" -p ActiveEnterTimestampMonotonic --value)
[ "$timer_mono_after" = "$timer_mono_before" ] && ok "D timer never restarted under the run" \
  || bad "D timer ActiveEnterTimestampMonotonic moved: $timer_mono_before -> $timer_mono_after"
mono_after=$(systemctl --user show "$SVC" -p ExecMainStartTimestampMonotonic --value)
[ -n "$mono_before" ] && [ "$mono_after" = "$mono_before" ] && ok "D service never restarted (ExecMainStart $mono_after)" \
  || bad "D service start moved: $mono_before -> $mono_after"

# --- E: release, then the healthy path reaps the flag -----------------------
systemctl --user stop "$SVC" >/dev/null 2>&1
n=""; for _ in $(seq 1 40); do n=$(next_of); [ "$n" != None ] && break; sleep 1; done
[ "$n" != None ] && ok "E timer re-armed after the release (next != null)" || bad "E timer still unarmed"
TIMER=$TIMER LOG=$LOG STUCK_AFTER=2 STUCK_FLAG=$FLAG bash "$GUARD" 2>/dev/null; rc=$?
[ "$rc" = 0 ] && ok "E rc=0 on the re-armed tick" || bad "E rc=$rc"
expect_count 1 "RUN ENDED: guardtest.service released, timer re-armed"
[ ! -f "$FLAG" ] && ok "E flag cleared" || bad "E flag survived the reap"

# --- G: flag survives a stop the guard never sees as a new run --------------
printf '999\n' > "$FLAG"                     # stale flag from an earlier run
TIMER=$TIMER LOG=$LOG STUCK_AFTER=2 STUCK_FLAG=$FLAG bash "$GUARD" 2>/dev/null; rc=$?
[ "$rc" = 0 ] && ok "G rc=0 with only a stale flag" || bad "G rc=$rc"
expect_count 2 "RUN ENDED:"
[ ! -f "$FLAG" ] && ok "G stale flag reaped" || bad "G stale flag survived"

# --- F: an unknown timer still repairs (the old path is intact) ------------
TIMER=no-such-rehearsal.timer LOG=$LOG STUCK_AFTER=2 STUCK_FLAG=$FLAG timeout 30 bash "$GUARD" 2>/dev/null; rc=$?
[ "$rc" = 2 ] && ok "F rc=2 REPAIR FAILED for an unknown timer (no hang)" || bad "F rc=$rc"
expect_contains "F reports the failed repair" "REPAIR FAILED"

echo
if [ "$fails" = 0 ]; then echo "RESULT PASS (0 failed arms)"; exit 0; fi
echo "RESULT FAIL ($fails failed arms)"; exit 1
