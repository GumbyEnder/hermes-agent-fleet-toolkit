#!/usr/bin/env bash
# E2E arms for restart-dispatcher-when-no-turns.sh -- the arm the 17:01 install
# never got: does the INSTALLED waiter, when it actually fires, do the right thing?
#
# The real dispatcher unit is never restarted here. The waiter is exercised against
# throwaway transient user units ("sinks") whose cgroups we control, and every
# restart the waiter attempts is intercepted by a stub systemctl that forwards
# `show` to the real binary and only LOGS `restart`.
#
# The subject is a byte-for-byte copy of the installed file with exactly two
# constants substituted (UNIT, LOG); the diff is printed as arm 0 so the run is
# self-evidencing -- if the copy ever drifts from the installed file, arm 0 fails.
#
# Arms:
#   0 subject identity      diff vs installed == the two substituted constants
#   A healthy              3 turn-shaped procs in the unit cgroup -> live_turns 3
#   B orphan in cgroup     +1 double-forked (ppid != MainPID) -> 4, ppid census 3
#   B' orphan killed       -> 3 vs 3
#   C dead MainPID         stub systemctl -> /proc/<dead>/cgroup unreadable -> -1
#   D waiter on -1         fails CLOSED: GAVE UP, rc=1, ZERO restart attempts
#   E waiter on 0 turns    restarts ONCE, and the unit it names is the unit asked for
#
# usage: bash test-waiter-e2e.sh
set -u
SUBJECT=/mnt/nas/host-ops/agent-chat/restart-dispatcher-when-no-turns.sh
SPAWNER=/mnt/nas/host-ops/agent-chat/e2e-sink-spawner.py
WORK=$(mktemp -d /tmp/waiter-e2e.XXXXXX)
FAILED=0
pass() { printf 'PASS  %s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*"; FAILED=$((FAILED+1)); }

SINK_FULL=waiter-e2e-full.service
SINK_EMPTY=waiter-e2e-empty.service
cleanup() {
  systemctl --user stop "$SINK_FULL" >/dev/null 2>&1
  systemctl --user stop "$SINK_EMPTY" >/dev/null 2>&1
  systemctl --user reset-failed "$SINK_FULL" "$SINK_EMPTY" >/dev/null 2>&1
}
trap cleanup EXIT

# ---- stubs ---------------------------------------------------------------
mkdir -p "$WORK/bin-dead" "$WORK/bin-fwd"
cat >"$WORK/bin-dead/systemctl" <<'EOF'
#!/bin/sh
case "$*" in *show*) echo 999999; exit 0 ;; esac
echo "$(date -Is) STUB-RESTART-ATTEMPT $*" >>"$STUB_LOG"
exit 0
EOF
cat >"$WORK/bin-fwd/systemctl" <<'EOF'
#!/bin/sh
case "$*" in *restart*) echo "$(date -Is) STUB-RESTART-ATTEMPT $*" >>"$STUB_LOG"; exit 0 ;; esac
exec /usr/bin/systemctl "$@"
EOF
chmod +x "$WORK/bin-dead/systemctl" "$WORK/bin-fwd/systemctl"

# ---- arm 0: the subject is the installed file + two constants --------------
build_subject() { # $1=unit $2=logfile -> path
  local out="$WORK/subject-$1.sh"
  sed -e "s|^UNIT=.*|UNIT=$1|" -e "s|^LOG=.*|LOG=$2|" "$SUBJECT" >"$out"
  chmod +x "$out"
  printf '%s\n' "$out"
}
S=$(build_subject "$SINK_FULL" "$WORK/waiter.log")
if [ "$(diff "$SUBJECT" "$S" | grep -c '^[<>]')" = "4" ] &&
   [ -z "$(diff "$SUBJECT" "$S" | grep '^[<>]' | grep -v '^[<>] \(UNIT\|LOG\)=')" ]; then
  pass "0 subject = installed file, only UNIT and LOG substituted"
  diff "$SUBJECT" "$S" | sed 's/^/      /'
else
  fail "0 subject differs from the installed file beyond UNIT/LOG"
  diff "$SUBJECT" "$S" | sed 's/^/      /'
fi

# ---- sinks ---------------------------------------------------------------
systemd-run --user --unit="${SINK_FULL%.service}" --collect \
  python3 "$SPAWNER" 3 0 300 >"$WORK/full.txt" 2>&1
for _ in $(seq 1 20); do grep -q MAINPID= "$WORK/full.txt" && break; sleep 0.5; done
MAIN=$(systemctl --user show -p MainPID --value "$SINK_FULL" 2>/dev/null)
printf 'sink %s MainPID=%s\n' "$SINK_FULL" "${MAIN:-<none>}"

# ---- A: healthy ----------------------------------------------------------
A=$("$S" --count)
[ "$A" = "3" ] && pass "A healthy: installed live_turns = 3" || fail "A healthy: live_turns=$A (want 3)"

# ---- B: orphan sits in the cgroup, invisible to pgrep -P -----------------
# Migrate the spawner into the unit's cgroup first (a child born in the test
# shell's cgroup would never be counted here -- measured), then let it double-fork.
CG_DIR="/sys/fs/cgroup$(sed -n 's/^0:://p' /proc/$MAIN/cgroup)"
E2E_CGROUP="$CG_DIR/cgroup.procs" python3 "$SPAWNER" 0 1 300 >/dev/null 2>&1 &
sleep 2
PP=$(pgrep -P "$MAIN" -f -- '-p [a-z-]+ -z' | wc -l)
CG=$("$S" --count)
[ "$CG" = "4" ] && [ "$PP" = "3" ] \
  && pass "B orphan in cgroup: cgroup=$CG ppid=$PP (the divergence)" \
  || fail "B orphan in cgroup: cgroup=$CG ppid=$PP (want 4 vs 3)"
ORPHAN=$(for p in $(cat "$CG_DIR/cgroup.procs"); do
  [ "$p" = "$MAIN" ] && continue
  # -a is load-bearing: /proc/<pid>/cmdline is NUL-separated, and plain grep -q on a
  # NUL-bearing file returns 1 in this build even when the text is there (measured).
  grep -aq -- '-p.*frodo.*-z' "/proc/$p/cmdline" 2>/dev/null || continue
  ppid=$(awk '{print $4}' "/proc/$p/stat" 2>/dev/null)
  [ "$ppid" != "$MAIN" ] && echo "$p"; done | head -1)
[ -n "${ORPHAN:-}" ] && pass "B' located the orphan (pid $ORPHAN, ppid != MainPID)" \
  || fail "B' could not locate the orphan in the cgroup"
[ -n "${ORPHAN:-}" ] && kill "$ORPHAN" 2>/dev/null
sleep 1
CG2=$("$S" --count); PP2=$(pgrep -P "$MAIN" -f -- '-p [a-z-]+ -z' | wc -l)
[ "$CG2" = "3" ] && [ "$PP2" = "3" ] \
  && pass "B' orphan killed: cgroup=$CG2 ppid=$PP2" \
  || fail "B' orphan killed: cgroup=$CG2 ppid=$PP2 (want 3 vs 3)"

# ---- C: dead MainPID -> cannot tell -> -1 --------------------------------
C=$(STUB_LOG=$WORK/dead.log PATH="$WORK/bin-dead:$PATH" "$S" --count)
[ "$C" = "-1" ] && pass "C dead MainPID: live_turns = -1 (fails closed, not blind 0)" \
  || fail "C dead MainPID: live_turns=$C (want -1)"

# ---- D and E: run the waiter itself, in parallel ------------------------
# A pid that is definitely gone: spawn, reap, then hand its number to the waiter.
sleep 1 & GONE=$!
wait "$GONE" 2>/dev/null
: >"$WORK/dead.log"; : >"$WORK/fwd.log"

systemd-run --user --unit="${SINK_EMPTY%.service}" --collect sleep 300 >/dev/null 2>&1
sleep 1
E_MAIN=$(systemctl --user show -p MainPID --value "$SINK_EMPTY" 2>/dev/null)
ES=$(build_subject "$SINK_EMPTY" "$WORK/e.log")
E0=$(PATH="$WORK/bin-fwd:$PATH" STUB_LOG="$WORK/fwd.log" "$ES" --count)

# D: dead MainPID -> live_turns -1 -> must fail closed, never restart.
PATH="$WORK/bin-dead:$PATH" STUB_LOG="$WORK/dead.log" \
  "$S" "$GONE" 60 1 >"$WORK/D.out" 2>&1 &
D_W=$!
# E: empty cgroup, healthy unit -> live_turns 0 -> must restart exactly once.
PATH="$WORK/bin-fwd:$PATH" STUB_LOG="$WORK/fwd.log" \
  "$ES" "$GONE" 60 30 >"$WORK/E.out" 2>&1 &
E_W=$!

for _ in $(seq 1 120); do kill -0 "$D_W" 2>/dev/null || break; sleep 1; done
for _ in $(seq 1 120); do kill -0 "$E_W" 2>/dev/null || break; sleep 1; done
wait "$D_W"; D_RC=$?
wait "$E_W"; E_RC=$?
sleep 1

DLOG=$(cat "$WORK/dead.log" 2>/dev/null)
if grep -q "GAVE UP waiting for live turns (live_turns=-1)" "$WORK/waiter.log" 2>/dev/null; then
  pass "D waiter on -1: GAVE UP on live_turns=-1 (burned its deadline, did not restart)"
else
  fail "D waiter on -1: no 'GAVE UP ... live_turns=-1' line in the waiter log"
  sed 's/^/      subject-log: /' "$WORK/waiter.log" 2>/dev/null
fi
[ "$D_RC" = "1" ] && pass "D waiter on -1: exited rc=1" || fail "D waiter on -1: rc=$D_RC (want 1)"
[ -n "$DLOG" ] && fail "D waiter on -1: attempted a restart anyway: $DLOG" \
  || pass "D waiter on -1: zero restart attempts"
E_ATT=$(grep -c STUB-RESTART-ATTEMPT "$WORK/fwd.log" 2>/dev/null || true)
E_LINE=$(grep STUB-RESTART-ATTEMPT "$WORK/fwd.log" 2>/dev/null)
if [ "$E_ATT" = "1" ] && printf '%s' "$E_LINE" | grep -q "restart $SINK_EMPTY"; then
  pass "E waiter on 0 turns: restarted once, named $SINK_EMPTY"
else
  fail "E waiter on 0 turns: attempts=$(E_ATT) line=[$E_LINE] (want 1 for $SINK_EMPTY)"
fi
if [ "$E0" = "0" ]; then pass "E precondition: empty sink reads live_turns = 0"
else fail "E precondition: empty sink reads live_turns=$E0 (want 0)"; fi
grep -h "restart $SINK_EMPTY\|live_turns=0" "$WORK/e.log" 2>/dev/null | sed 's/^/      /'

printf '\n%s arms failed\nwork=%s\n' "$FAILED" "$WORK"
[ "$FAILED" -eq 0 ] && { rm -rf "$WORK"; exit 0; } || exit 1
