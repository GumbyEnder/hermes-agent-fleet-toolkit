#!/usr/bin/env bash
# Restart hermes-wake-dispatcher after one named turn exits AND no other turn is live.
#
# restart-dispatcher-after-turn.sh waits for a single pid only. With MAX_CONCURRENCY
# going 2 -> 3 the odds that a second profile's turn is alive when that pid exits go up
# (measured: 3 profiles had turns inside the 07:25-08:05 window), and a restart inside
# that turn orphans it -- the rc=-15 / re-run defect this file's sibling exists to avoid.
#
# So: wait for the named wrapper pid, let its post land, then keep waiting until the
# process table shows no live `hermes -p <p> -z` turn, then restart.
#
# Queue depth is deliberately NOT a condition: on this host the queue sits at 20/20 for
# hours while turns complete, and the spill carries queued records across a restart
# (spill_load / spill_resume), so a non-empty queue is not an orphan risk.
#
# REVISED 2026-11-14 (buzzy), two defects proven on this host:
#
# 1. live_turns() used to be `pgrep -af hermes | grep -Ec "<turn shape>"`. That counts
#    ANY process whose argv carries both "hermes" and the literal turn shape -- proven
#    with a decoy `bash -c 'exec -a "hermes -p zzzdecoy -z hello" sleep 40'`, which
#    moved the count 2 -> 3 with zero real turns added. `$$` is NOT the fix: the
#    script's own argv (".../host-ops/...") matches neither token, and a plain
#    `bash -c` that merely quotes the regex does not match either (the literal
#    "[a-z-]+" is not in its own character class). The honest predicate is the one
#    the dispatcher itself guarantees: a turn is a process in the dispatcher unit's
#    cgroup. Measured: shipped counter and cgroup counter agreed 4/4 while no decoy
#    existed, and diverged only when the decoy was present.
#
# 2. A missing terminal line is not a death. The 09:11:28 waiter on 2026-11-14 was
#    read as "killed" three hours into its 14400 s idle wait; it was still running,
#    exited status=1 at 13:11:29 on its own deadline (journal: transient unit
#    "Main process exited, code=exited, status=1/FAILURE", Consumed 2min27.404s), and
#    wrote `GAVE UP waiting for live turns (live_turns=3)` at 13:11:29. Silence in the
#    log means "still inside the loop" iff the unit is alive -- read the unit, not the
#    log, before calling a waiter dead. Still, every exit now gets a line (trap below).
#
# 3. INSTALLED 2026-11-14 17:01 (buzzy, frodo's candidate + two edits): the body of
#    live_turns() was still `pgrep -P <MainPID>` -- a ppid census, which is the weaker
#    predicate this header names and does not implement. :747 spawns every turn with
#    start_new_session=True, so a turn outlives its spawner by construction; a restart
#    by any other hand leaves orphans reparented to systemd --user (ppid 1616) that
#    `pgrep -P` cannot see. Repro on this box 16:58: a turn-shaped orphan placed in the
#    unit's cgroup read 3 on the ppid leg and 4 on the cgroup leg, 3 again once killed
#    (/mnt/nas/host-ops/agent-chat/test-live-turns-cgroup.py re-runs it).
#
# Usage: restart-dispatcher-when-no-turns.sh <pid> [pid_deadline_s] [idle_deadline_s]
#        restart-dispatcher-when-no-turns.sh --count     # print live_turns, change nothing
set -u
UNIT=hermes-wake-dispatcher.service
LOG=/mnt/nas/host-ops/agent-chat/wake-idle-restart.log

# A live turn is a process in the dispatcher UNIT'S CGROUP whose argv carries a turn
# shape. Cgroup membership, not ppid: membership survives the spawner's exit, and it
# is also what makes the count decoy-proof (a decoy outside the unit cannot appear).
# Both launch shapes: the relaunch wrapper carries "'-p', 'frodo', '-z'" (quoted source
# text) and a direct spawn carries "-p frodo -z".
# MainPID is resolved HERE, not at arm time: a relaunch does not invalidate a waiter.
live_turns() {
  local main cg pid n=0 procs re="-p [a-z-]+ -z|'-p', '[a-z-]+', '-z'"
  main=$(systemctl --user show -p MainPID --value "$UNIT" 2>/dev/null)
  if ! [[ $main =~ ^[0-9]+$ ]] || [ "$main" -le 0 ]; then printf '%s\n' -1; return; fi
  # The unit's cgroup, not the ppid chain: membership survives the spawner's exit.
  # Measured 2026-11-14 16:58: a turn-shaped process whose parent exits is reparented
  # to systemd --user (ppid 1616, not MainPID) and is invisible to `pgrep -P`, while
  # /proc/<pid>/cgroup still names this unit. That is the orphan case the dispatcher
  # itself documents at _worker's "orphan from a restarted dispatcher".
  cg=$(sed -n 's/^0:://p' "/proc/$main/cgroup" 2>/dev/null)
  if [ -n "$cg" ] && [ -r "/sys/fs/cgroup$cg/cgroup.procs" ]; then
    procs=$(cat "/sys/fs/cgroup$cg/cgroup.procs" 2>/dev/null)
  else
    # Never substitute the weaker ppid predicate for the stronger one -- a blind 0 is
    # the failure this function exists to prevent. "cannot tell" (-1) makes the caller
    # wait out its deadline and GAVE UP, instead of restarting over a turn it cannot see.
    printf '%s\n' -1; return
  fi
  for pid in $procs; do
    [ "$pid" = "$main" ] && continue
    cmd=$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null) || continue
    [[ $cmd =~ $re ]] && n=$((n + 1))
  done
  printf '%s\n' "$n"
}

if [ "${1:-}" = "--count" ]; then
  live_turns
  exit 0
fi

PID=${1:?turn wrapper pid}
DEADLINE=$(( $(date +%s) + ${2:-1800} ))
IDLE_DEADLINE=$(( $(date +%s) + ${3:-1800} ))

# Every exit gets a line, including a SIGTERM from whoever armed us. A bare EXIT trap
# reported rc=0 for a killed waiter (measured: SIGTERM -> process rc -15, logged rc=0),
# so the signals that mean "someone killed me" are trapped and recorded by name.
SIG=none
trap 'SIG=TERM; exit 143' TERM
trap 'SIG=INT; exit 130' INT
trap 'SIG=HUP; exit 129' HUP
trap 'rc=$?; printf "%s exiting rc=%s sig=%s phase=%s live_turns=%s\n" "$(date -Is)" "$rc" "$SIG" "${PHASE:-arm}" "$(live_turns)" >>"$LOG"' EXIT
PHASE=pid
ARM_TURNS=$(live_turns)
printf '%s armed pid=%s pid_deadline=%s idle_deadline=%s live_turns=%s\n' \
  "$(date -Is)" "$PID" "$(date -d "@$DEADLINE" -Is)" "$(date -d "@$IDLE_DEADLINE" -Is)" "$ARM_TURNS" >>"$LOG"

while kill -0 "$PID" 2>/dev/null; do
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    printf '%s GAVE UP waiting for pid %s\n' "$(date -Is)" "$PID" >>"$LOG"
    exit 1
  fi
  sleep 2
done
printf '%s pid %s gone -> waiting for the fleet to quiesce\n' "$(date -Is)" "$PID" >>"$LOG"
# The wrapper exits before the dispatcher posts its stdout as the reply.
sleep 20
PHASE=quiesce

while [ "$(live_turns)" -ne 0 ]; do
  if [ "$(date +%s)" -ge "$IDLE_DEADLINE" ]; then
    printf '%s GAVE UP waiting for live turns (live_turns=%s)\n' "$(date -Is)" "$(live_turns)" >>"$LOG"
    exit 1
  fi
  sleep 5
done
printf '%s live_turns=0 -> restart %s\n' "$(date -Is)" "$UNIT" >>"$LOG"
PHASE=restart
systemctl --user restart "$UNIT"
sleep 3
printf '%s restarted MainPID=%s health=%s\n' "$(date -Is)" \
  "$(systemctl --user show -p MainPID --value "$UNIT")" \
  "$(curl -s --max-time 5 http://127.0.0.1:8095/health)" >>"$LOG"
