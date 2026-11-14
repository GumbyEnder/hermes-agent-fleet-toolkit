#!/usr/bin/env bash
# check-grade-timer.sh -- detect and repair a timer that armed nothing.
#
# WHY: measured 2026-11-14 06:29 -- after the 06:05 fire ran 23 min into the
# window, grade-slot.timer reported `NextElapseUSecRealtime=` (EMPTY) and
# `TimersCalendar={ OnCalendar=*-*-* *:05:00 ; next_elapse=Sat 2026-11-14
# 06:05:00 EST }` (next elapse stuck in the past), while `list-timers` showed
# NEXT `-`. A calendar timer in that state grades nothing ever again and the
# only symptom is a room that goes quiet. The cause is NOT established, so this
# checks the condition and repairs it; it never explains it.
#
# rc: 0 healthy, 1 repaired (was unarmed), 2 repair FAILED. A run that is still
# active is HEALTHY (rc 0) by design -- restarting the timer under a live run
# wedges it -- but it is bounded: past STUCK_AFTER the guard writes a STUCK? line
# and keeps watching, and writes RUN ENDED when the run releases.
#
# THE AUTHORITY IS `list-timers`, NOT THE PROPERTIES. Measured here (systemd
# 255): a MONOTONIC timer (OnUnitActiveSec) leaves BOTH NextElapseUSecRealtime
# AND TimersCalendar EMPTY while firing perfectly -- so a guard keyed on those
# properties "repairs" a healthy timer on every tick (reproduced on
# check-grade-timer.timer itself, rc 2). `systemctl list-timers --output=json`
# carries `next` (usec since epoch, null when unarmed) for every timer kind, so
# that is the source; the properties are only a fallback for older systemd.
#
# rc: 0 healthy, 1 repaired (was unarmed), 2 repair FAILED.
set -u
TIMER=${TIMER:-grade-slot.timer}
LOG=${LOG:-/mnt/nas/host-ops/agent-chat/grade-timer-guard.log}
STUCK_AFTER=${STUCK_AFTER:-2400}          # s; a run longer than this is logged, never killed
STUCK_FLAG=${STUCK_FLAG:-$LOG.stuck}      # last elapsed value already reported

next_epoch() {   # prints "epoch:<s>" | "usec:<n>" | nothing
    systemctl --user list-timers --output=json --all "$TIMER" 2>/dev/null \
      | python3 -c '
import json, sys
u = sys.argv[1]
try:
    rows = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for r in rows:
    if r.get("unit") == u and r.get("next") is not None:
        print("usec:%d" % r["next"]); break
' "$TIMER" 2>/dev/null
}

busy() {   # 0 when the unit this timer activates is still running
    local act
    act=$(activates_unit)
    [ -n "$act" ] || return 1
    case "$(systemctl --user is-active "$act" 2>/dev/null)" in
        active|activating|reloading) return 0 ;;
    esac
    return 1
}

activates_unit() {   # prints the unit this timer activates, empty when unknown
    systemctl --user list-timers --output=json --all "$TIMER" 2>/dev/null \
      | python3 -c '
import json, sys
u = sys.argv[1]
try:
    rows = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for r in rows:
    if r.get("unit") == u:
        print(r.get("activates") or ""); break
' "$TIMER" 2>/dev/null
}

run_elapsed() {   # prints how long the activated unit has been running, in s
    local act mono now
    act=$(activates_unit)
    [ -n "$act" ] || return 1
    # ExecMainStart, NOT ActiveEnter: a Type=oneshot unit that is still running
    # sits in `activating`, so ActiveEnterTimestampMonotonic is still 0 for the
    # entire run -- exactly the window this measures. Measured here: the
    # rehearsal's oneshot read ActiveEnter 0 / ExecMainStart set. ActiveEnter is
    # only the fallback for a unit type that never records ExecMainStart.
    mono=$(systemctl --user show "$act" -p ExecMainStartTimestampMonotonic --value 2>/dev/null || true)
    case "$mono" in ''|*[!0-9]*) mono=0 ;; esac
    if [ "$mono" -le 0 ]; then
        mono=$(systemctl --user show "$act" -p ActiveEnterTimestampMonotonic --value 2>/dev/null || true)
        case "$mono" in ''|*[!0-9]*) return 1 ;; esac
    fi
    [ "$mono" -gt 0 ] || return 1
    now=$(awk '{printf "%d", $1 * 1000000}' /proc/uptime 2>/dev/null) || return 1
    [ "$now" -gt "$mono" ] || return 1
    echo $(( (now - mono) / 1000000 ))
}

# THE BUSY BRANCH NEEDS A BOUND. A run that hangs and never goes inactive keeps
# list-timers at `next: null`, so `busy` reads healthy forever and the room goes
# quiet exactly as it would from an unarmed timer -- the one failure this guard
# exists for, wearing the mask of the condition it must not act on. So: measure
# the run, never restart under it (rc stays 0), and say so in the log once it
# crosses STUCK_AFTER, then once an hour while it persists, plus one line when it
# ends. Under the threshold the log stays silent, so "zero lines" keeps meaning
# "nothing happened" (grade-slot.service runs ~23 min; 40 min is headroom, not a
# verdict on the run -- the guard still does not restart the timer).
note_busy() {
    local el last
    el=$(run_elapsed) || return 0
    [ -n "$el" ] || return 0
    if [ "$el" -lt "$STUCK_AFTER" ]; then
        # a flag here belongs to a PREVIOUS run (a fresh one is under the
        # threshold), so report it as ended; this is the only in-busy exit that
        # sees a release, because a normal release re-arms the timer and the
        # next tick takes the healthy path (reap_flag) instead.
        reap_flag "released, new run at ${el}s"
        return 0
    fi
    last=$(cat "$STUCK_FLAG" 2>/dev/null || true)
    case "$last" in ''|*[!0-9]*) last=0 ;; esac
    if [ "$last" -eq 0 ] || [ $(( el - last )) -ge 3600 ]; then
        say "STUCK? $(activates_unit) still active after ${el}s (threshold ${STUCK_AFTER}s) -- not restarting under a run"
        printf '%s\n' "$el" > "$STUCK_FLAG" 2>/dev/null || true
    fi
    return 0
}

reap_flag() {   # announce and clear a flag left by a run that is gone
    [ -f "$STUCK_FLAG" ] || return 0
    # A LIVE RUN OWNS THE FLAG. healthy() returns 0 from the busy branch too, so
    # reaping on "the guard found it healthy" would announce a release one line
    # after note_busy announced the stall -- measured: both lines in one tick,
    # and the flag gone while the run was still active. Only the absence of a
    # run may reap.
    busy && return 0
    local act last
    act=$(activates_unit)
    last=$(cat "$STUCK_FLAG" 2>/dev/null || true)
    say "RUN ENDED: ${act:-?} ${1:-no longer running} (was flagged at ${last:-?}s)"
    rm -f "$STUCK_FLAG"
}

healthy() {
    local st raw cand
    st=$(systemctl --user show "$TIMER" -p ActiveState --value 2>/dev/null || true)
    [ "$st" = active ] || return 1
    raw=$(next_epoch)
    if [ -n "$raw" ]; then
        [ "${raw%%:*}" = usec ] || return 1
        cand=$(( ${raw#usec:} / 1000000 ))
        [ "$cand" -gt "$(date +%s)" ]
        return
    fi
    # A RUNNING TRIGGER IS NOT AN UNARMED TIMER. Measured here (systemd 255,
    # 2026-11-14 07:37, scratch oneshot sleeping 30 s under a 10 s calendar
    # timer): while the activated unit is active, list-timers reports
    # `next: null` and NextElapseUSecRealtime is empty -- systemd computes the
    # next elapse when the triggered unit goes inactive. Flagging that as
    # unarmed restarts the timer under a live run and then reports REPAIR
    # FAILED (rc 2, failed unit) because the state repeats until the run ends.
    # Seen on the real guard at 07:07:07, two minutes into the 07:05 grade.
    # grade-slot.service runs ~23 min, so this window is most of every hour.
    # Bounded as of this version: note_busy() logs a run that outlives
    # STUCK_AFTER without ever restarting under it (see its comment).
    if busy; then note_busy; return 0; fi
    # fallback: the two properties (calendar timers on older systemd)
    for cand in \
        "$(systemctl --user show "$TIMER" -p NextElapseUSecRealtime --value 2>/dev/null || true)" \
        "$(systemctl --user show "$TIMER" -p TimersCalendar --value 2>/dev/null || true)"; do
        cand=${cand##*next_elapse=}; cand=${cand%%\}*}; cand=${cand%"${cand##*[! ]}"}
        [ -n "$cand" ] || continue
        local e
        e=$(date -d "$cand" +%s 2>/dev/null) || continue
        [ "$e" -gt "$(date +%s)" ] && return 0
    done
    return 1
}

say() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%:z')" "$*" | tee -a "$LOG" >&2; }

if healthy; then
    # A flag left by a run that released normally is reaped here: the release
    # re-arms the timer, so a normal end is seen on THIS path, not in note_busy.
    reap_flag "released, timer re-armed"
    exit 0
fi

say "UNARMED timer=$TIMER -> restarting"
systemctl --user reset-failed "$TIMER" >/dev/null 2>&1 || true
systemctl --user restart "$TIMER" >/dev/null 2>&1 || { say "REPAIR FAILED: restart rc=$?"; exit 2; }

for _ in 1 2 3 4 5; do
    sleep 1
    if healthy; then say "REPAIRED timer=$TIMER next=$(next_epoch)"; exit 1; fi
done

say "REPAIR FAILED after restart: timer=$TIMER"
exit 2
