#!/usr/bin/env bash
# check-grade-timer.sh -- detect and repair a grade-slot.timer that armed nothing.
#
# WHY: measured 2026-11-14 06:29 -- after the 06:05 fire ran 23 min into the
# window, grade-slot.timer reported `NextElapseUSecRealtime=` (EMPTY) and
# `TimersCalendar={ OnCalendar=*-*-* *:05:00 ; next_elapse=Sat 2026-11-14
# 06:05:00 EST }` (next elapse stuck in the past), while `list-timers` showed
# NEXT `-`. A calendar timer in that state grades nothing ever again and the
# only symptom is a room that goes quiet. The cause is NOT established, so this
# checks the condition and repairs it; it never explains it.
#
# Health = ActiveState=active AND NextElapseUSecRealtime parses AND it is in the
# future. Anything else -> restart the timer (a restart re-arms from OnCalendar)
# and re-check.
#
# rc: 0 healthy, 1 repaired, 2 repair FAILED.
set -u
TIMER=${TIMER:-grade-slot.timer}
LOG=${LOG:-/mnt/nas/host-ops/agent-chat/grade-timer-guard.log}

now=$(date +%s)
state=$(systemctl --user show "$TIMER" -p ActiveState --value 2>/dev/null || true)
next=$(systemctl --user show "$TIMER" -p NextElapseUSecRealtime --value 2>/dev/null || true)
next_s=$(date -d "$next" +%s 2>/dev/null || echo 0)

say() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%:z')" "$*" | tee -a "$LOG" >&2; }

if [ "$state" = active ] && [ "$next_s" -gt "$now" ]; then
    exit 0
fi

say "UNARMED state='${state}' next='${next}' -> restarting $TIMER"
systemctl --user reset-failed "$TIMER" >/dev/null 2>&1 || true
systemctl --user restart "$TIMER" >/dev/null 2>&1 || { say "REPAIR FAILED: restart rc=$?"; exit 2; }

for _ in 1 2 3 4 5; do
    sleep 1
    state=$(systemctl --user show "$TIMER" -p ActiveState --value 2>/dev/null || true)
    next=$(systemctl --user show "$TIMER" -p NextElapseUSecRealtime --value 2>/dev/null || true)
    next_s=$(date -d "$next" +%s 2>/dev/null || echo 0)
    if [ "$state" = active ] && [ "$next_s" -gt "$(date +%s)" ]; then
        say "REPAIRED state=active next='${next}'"
        exit 1
    fi
done

say "REPAIR FAILED after restart: state='${state}' next='${next}'"
exit 2
