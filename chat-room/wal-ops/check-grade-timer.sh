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

if healthy; then exit 0; fi

say "UNARMED timer=$TIMER -> restarting"
systemctl --user reset-failed "$TIMER" >/dev/null 2>&1 || true
systemctl --user restart "$TIMER" >/dev/null 2>&1 || { say "REPAIR FAILED: restart rc=$?"; exit 2; }

for _ in 1 2 3 4 5; do
    sleep 1
    if healthy; then say "REPAIRED timer=$TIMER next=$(next_epoch)"; exit 1; fi
done

say "REPAIR FAILED after restart: timer=$TIMER"
exit 2
