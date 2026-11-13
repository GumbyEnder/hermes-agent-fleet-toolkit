#!/usr/bin/env bash
# Fold the chat-room WAL into room.db from OUTSIDE the bus process.
# WHY (corrected 2026-11-13): CAUSE UNKNOWN. This timer is a MITIGATION, not a fix.
# It was installed on the theory that the bus's PrivateTmp=yes namespace broke
# sqlite's WAL work; that option is gone from the unit since 2026-11-12 21:35:31 and
# the error class continued (410 `disk I/O error` journal lines on 11-12, 76 on 11-13).
# Reproduced mechanism: with a connection open, if room.db-wal is unlinked while it
# still holds frames, every later connect IN THAT PROCESS fails at db()'s
# `PRAGMA journal_mode=WAL` (bus.py:82) with `disk I/O error`; a 0 B sidecar unlinked
# the same way is harmless. Who unlinks the live sidecar is still UNIDENTIFIED —
# watch /mnt/nas/host-ops/agent-chat/wal-unlink-watch.log for the event.
# The loss it mitigates is real: committed rows can live only in room.db-wal, and a
# restart can lose them (measured 2026-11-11: ids 3057-3088).
set -uo pipefail
DB="${AGENT_CHAT_DB:-$HOME/agents/agent-chat/room.db}"
# LOG and the pin-streak state file are overridable ONLY so the script can be
# rehearsed against a throwaway DB without writing into production history.
LOG="${AGENT_CHAT_CHECKPOINT_LOG:-$HOME/agents/agent-chat/checkpoint.log}"
STREAK_FILE="${AGENT_CHAT_PIN_STREAK:-$HOME/agents/agent-chat/walcheck.pin_streak}"

[ -f "$DB" ] || { echo "$(date -Is) no db at $DB" >>"$LOG"; exit 1; }
# Measure the sidecar BEFORE the checkpoint. When no other process holds the DB this sqlite3 CLI
# is the LAST connection, so its close checkpoints and UNLINKS the -wal: a stat taken afterwards
# reports the probe's own teardown, not the state that was there (measured 2026-11-13: absent,
# 0 B and 12,392 B all read `absent` after the call). And `stat -c%s ... || echo 0` collapses
# "absent" and "0 B" into one token — the distinction that decides whether the EIO wedge fires.
# Three states, three tokens, on both sides.
wal_before=$(stat -c%s "$DB-wal" 2>/dev/null || echo absent)
# fd census of the bus at fold time (appended last; existing tokens unaffected).
# History, because this comment used to assert the opposite: until 59bef9f
# (deployed 02:31:47) the bus opened one sqlite3 connection per request and never
# closed it, pinning the -wal (85 handles on the live inode measured 02:25:29) so a
# "last connection close" was unreachable by construction. The context manager now
# closes, so this census is a LEAK DETECTOR, not a precondition: healthy is fds in
# single digits with wal=absent (measured 9/9 ticks 02:39:35-02:55:36), and a
# climbing count means the leak is back and the sidecar will pin again. Read the
# wal pair only when wal_before is numeric — absent/absent is a tautology, and since
# the fix it is also the steady state, so it cannot discriminate anything.
buspid=$(systemctl --user show -p MainPID --value hermes-agent-chat-bus.service 2>/dev/null)
fds=$(ls "/proc/${buspid:-0}/fd" 2>/dev/null | wc -l)
out=$(sqlite3 "$DB" ".timeout 5000" "PRAGMA wal_checkpoint(PASSIVE);" 2>&1)
rc=$?
wal_after=$(stat -c%s "$DB-wal" 2>/dev/null || echo absent)
# PIN DETECTION — from the tuple this probe already prints. PASSIVE reports
# busy|log|folded, and busy=0 does NOT mean nothing is stuck: a reader holding a read
# mark returns 0|2|1 (the checkpoint ran, folded 1 of 2 frames, the rest stay
# un-foldable until that reader leaves — measured 2026-11-13, sqlite 3.53.1, arms D/E/F
# in walpin/probe2.py). `folded<log` is the discriminator; the old `0|* -> exit 0`
# made that shape as silent as bus.py's discarded tuple. This is the FOLD alarm and
# it is orthogonal to WAL-PINNED in wal-unlink-watch-verdict.py, which only sees the
# unlink: a 10/10-folded WAL can still be pinned (that was 03:42-04:03 on 11-13), and
# an un-foldable frame means rows are living only in the sidecar.
pin=0
# ABSENT ARM (rehearsed): a tick with no sidecar, or a 0 B sidecar, carries NO
# information about pinning -- out=0|0|0 has log=0, i.e. nothing to fold. Emit
# pin=absent and leave the streak file untouched; otherwise one idle tick resets
# a live pin run to 0 and the 5-tick floor can never be reached.
# This is NOT an override, and must not be described as one: the tuple arm below
# still runs and can set pin=1 after this line. Two separate things keep it safe,
# and only the first is a guard:
#   (1) the `if [ "$pin" != absent ]` wrapper around the parse, so no future reorder
#       can re-arm absent;
#   (2) the numeric fact that an absent / 0 B sidecar normally yields out=0|0|0,
#       nlog=0, which makes the `nlog -gt 0` test unreachable.
# (2) alone is a coincidence, not a guard: a sidecar created between the stat and the
# checkpoint can return 0|N|M with frames, and the unwrapped form then fires pin=1 on
# a WAL this probe measured as absent. Measured with a stubbed sqlite3, 2026-11-13.
case "$wal_before" in absent|0) pin=absent ;; esac
if [ "$pin" != absent ]; then
case "$out" in
  [0-9]*\|[0-9]*\|[0-9]*)
    nlog=${out#*|}; nlog=${nlog%%|*}; nfold=${out##*|}
    if [ "$nlog" -gt 0 ] 2>/dev/null && [ "$nfold" -lt "$nlog" ] 2>/dev/null; then pin=1; fi ;;
esac
fi
# Consecutive-run counter: one 0|N|M tick is an ordinary reader (an execute_code
# kernel mid-SELECT, ~seconds). The alarm is persistence, not a single frame: exit
# nonzero after PIN_ALERT_STREAK ticks, 5 x 2 min = a 10-minute floor, which is past
# the 600 s bound §2d specified so a transient cursor can never page anyone.
streak=0
if [ -f "$STREAK_FILE" ]; then
  streak=$(cat "$STREAK_FILE" 2>/dev/null)
  case "$streak" in ''|*[!0-9]*) streak=0 ;; esac
fi
# ERROR STREAK — a SECOND counter, because `pin` and `err` are different facts and
# collapsing them would silently redefine `streak` for every reader of this log.
# Until now the non-tuple arm (`*) exit 1`) fired on a single malformed output: one
# `database is locked`, one rc!=0, and the unit was failed with no persistence
# requirement, while a genuinely wedged probe that printed a clean tuple each tick
# would never fail it. Symmetric to the pin fix: the alarm is repetition.
ERR_FILE="${AGENT_CHAT_ERR_STREAK:-$HOME/agents/agent-chat/walcheck.err_streak}"
err=0
if [ -f "$ERR_FILE" ]; then
  err=$(cat "$ERR_FILE" 2>/dev/null)
  case "$err" in ''|*[!0-9]*) err=0 ;; esac
fi
bad=1
case "$out" in [0-9]*\|[0-9]*\|[0-9]*) bad=0 ;; esac
if [ "$bad" = 1 ]; then err=$((err + 1)); else err=0; fi
printf '%s\n' "$err" >"$ERR_FILE" 2>/dev/null
if [ "$pin" = absent ]; then :
elif [ "$pin" = 1 ]; then streak=$((streak + 1)); else streak=0; fi
if [ "$pin" != absent ]; then printf '%s\n' "$streak" >"$STREAK_FILE" 2>/dev/null; fi
echo "$(date -Is) rc=$rc out=$out wal=$wal_after wal_before=$wal_before fds=$fds pin=$pin streak=$streak err=$err" >>"$LOG"

# Keep the log from growing forever. One line per run — except the first record (2026-11-11T03:56:08),
# whose out= held a newline and so spans two — and the real cadence is the timer's
# OnUnitActiveSec=2min (hermes-agent-chat-walcheck.timer), i.e. ~262,800 lines/year, so this
# trim is load-bearing, not cosmetic. (This line claimed "10-minute cadence = ~52k"; that held for
# 249 intervals of 600-630 s, ending 2026-11-12T21:34:47 — 86 exactly 600.0, 125 at 601.0, 38 wider —
# then the timer was rewritten at 21:37:13. Stale-by-change, not wrong-by-5x: 52k is history,
# 262,800 is now.)
if [ "$(wc -l <"$LOG")" -gt 5000 ]; then tail -n 2000 "$LOG" >"$LOG.tmp" && mv "$LOG.tmp" "$LOG"; fi

# Fail when the checkpoint reports busy/I-O trouble, or when frames have stayed
# un-foldable across PIN_ALERT_STREAK ticks. `0|*` is NOT sufficient: PASSIVE returns
# busy=0 with folded<log whenever a reader holds a read mark.
# BOTH arms now require persistence, each on its own counter. The old `*) exit 1`
# meant a single `database is locked` failed the unit on a busy second while a probe
# wedged for an hour failed it never — the alarm is repetition, not one sample.
PIN_ALERT_STREAK="${PIN_ALERT_STREAK:-5}"
if { [ "$pin" != absent ] && [ "$streak" -ge "$PIN_ALERT_STREAK" ]; } || [ "$err" -ge "$PIN_ALERT_STREAK" ]; then
  exit 1
fi

# No consumer exists for that status today: no user unit carries OnFailure=, nothing
# reads `systemctl --user --failed`, so a nonzero exit lands in the unit's record and
# stops there. The load-bearing surfaces are this line (pin=/streak=/err=) and the
# journal record of the oneshot run; wire OnFailure= or a --failed reader before
# treating the exit code as a pager.
exit 0
