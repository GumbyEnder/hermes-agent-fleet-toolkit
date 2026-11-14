#!/usr/bin/env bash
# Grade the NEXT scheduled backup slot, then post the witness header + verifier
# output into the chat room as a child of a named message.
#
# Why the slot is derived, not passed: the witness slot is
# ExecMainStartTimestamp + n*BACKUP_INTERVAL (1 h), so a restart MOVES it. A
# grader armed for a fixed hour grades the wrong second or nothing at all --
# measured 2026-11-14: the bus restarted 02:18:00.814751 and the one-shot unit
# armed for 03:18 had already passed its derived slot by the time it fired.
#
# So: derive start + n*3600 for the smallest n that is still ahead of now, wait
# for that second, resolve the ARM's own tag from the manifest (its tag carries
# microseconds and can sit 1-2 s after the derived second), then run the
# read-only verifier on the resolved tag.
#
# If no row appears near the derived second, the script posts the MISS and exits
# non-zero WITHOUT running the verifier. It never grades a slot it cannot see:
# the old fall-through graded the derived tag and printed "FAIL no manifest rows
# matched" about a slot that existed (room msg 4335), i.e. the grader invented
# the failure.
#
# One grade per slot: a guard file under ~/agents/agent-chat/graded/<tag> stops
# a second post for the same slot (the timer may fire again while a long run is
# still sleeping). --force overrides.
#
# VERDICT AGE IS ~37 MIN BY CONSTRUCTION, not a defect. The timer fires at :05
# and slots land at :27:58 of the hour, so the :05 tick derives a slot an hour
# older (06:05 grades 05:27) while the newest slot waits for the next tick. No
# slot is skipped -- the too-early tick hits the guard path and exits 0 -- every
# verdict is simply ~37 min stale. Move the timer to :30 if that ever matters;
# until then this note is the record so nobody re-derives it as a bug.
#
# usage: grade-slot.sh [--dry] [--force] [PARENT_ID]
#   --dry      print the post body, do not send, do not write the guard
#   --force    ignore an existing guard for this slot
#   PARENT_ID  room message to thread under (default 4303)
set -uo pipefail

DRY=0
FORCE=0
PARENT=4303
for a in "$@"; do
  case "$a" in
    --dry) DRY=1 ;;
    --force) FORCE=1 ;;
    # PARENT is a room message id, threaded under by /send. A slot tag here is a
    # plausible-looking argument that only fails at the bus, AFTER the derived slot
    # has been graded -- and line ~181 then writes the usage error into the guard
    # file, destroying the record that the slot was graded. Measured 2026-11-14:
    # `--force <slot_tag>` left graded/20261114T092759.702524-0500 holding
    # "agentbus send: error: argument --reply-to: invalid int value: '<slot>'" while
    # the real post (#4560) had landed. Reject the shape here instead.
    *) case "$a" in
         ''|*[!0-9]*) echo "grade-slot.sh: PARENT must be a room message id (integer), got '$a'" >&2
                      echo "usage: grade-slot.sh [--dry] [--force] [PARENT_ID]   (default 4303)" >&2
                      exit 2 ;;
         *) PARENT="$a" ;;
       esac ;;
  esac
done

SVC=hermes-agent-chat-bus.service
VERIFY=/mnt/nas/host-ops/agent-chat/verify-slot-manifest.sh
MANIFEST=/mnt/nas/agent-chat/room.db.bak.manifest.jsonl
WITNESS_DIR=/home/gumbyender/agents/agent-chat
GUARD_DIR="$WITNESS_DIR/graded"
mkdir -p "$GUARD_DIR"
export PATH=/home/gumbyender/.local/bin:$PATH

interval=${AGENT_CHAT_BACKUP_INTERVAL:-3600}
FROM=${GRADE_FROM:-buzzy}          # who the witness post is sent as

start_txt=$(systemctl --user show "$SVC" -p ExecMainStartTimestamp --value)
start_ep=$(date -d "$start_txt" +%s) || { echo "cannot parse start '$start_txt'" >&2; exit 2; }
now=$(date +%s)

# A timer can fire a few seconds AFTER the slot second it meant to grade, and
# the row lands ~1 s after the slot -- so "next slot strictly after now" skips a
# whole hour when the trigger is 7 s late (measured 2026-11-14 04:18:07: derived
# 05:18 and slept 3601 s past a slot that had just been written). Grace keeps a
# just-passed slot eligible.
GRACE=${GRADE_GRACE_S:-45}
if [ -n "${SLOT_EP:-}" ]; then
  slot_ep=$SLOT_EP
else
  n=$(( (now - GRACE - start_ep) / interval + 1 ))
  [ "$n" -lt 1 ] && n=1
  slot_ep=$(( start_ep + n * interval ))
fi

slot_tag=$(date -d "@$slot_ep" +%Y%m%dT%H%M%S)
offs=$(( slot_ep - start_ep ))
# SLOT_EP can precede this ExecMainStart (a restart moved the slot under a
# caller-supplied second), which used to print the nonsense "+ -6478s". Keep the
# number, name the condition: a negative offset is not a small offset.
if [ "$offs" -lt 0 ]; then
  offs_txt="${offs}s BEFORE start (SLOT_EP predates this ExecMainStart; a restart moved the slot)"
else
  offs_txt="+${offs}s"
fi

if [ "$FORCE" = 0 ] && [ "$DRY" = 0 ] && [ -e "$GUARD_DIR/$slot_tag" ]; then
  echo "slot $slot_tag already graded ($(cat "$GUARD_DIR/$slot_tag" 2>/dev/null)) -- nothing to do"
  exit 0
fi

wait_s=$(( slot_ep + 8 - now ))
if [ "$wait_s" -gt 0 ]; then
  [ "$wait_s" -gt 3700 ] && { echo "slot $slot_ep is ${wait_s}s out (> 1 interval) -- refusing" >&2; exit 5; }
  if [ "$DRY" = 1 ]; then
    echo "would wait ${wait_s}s for slot $(date -d "@$slot_ep" +%Y%m%dT%H%M%S) (--dry: not waiting)" >&2
  else
    sleep "$wait_s"
  fi
fi

# Resolve the tag from the manifest: the row whose copy_time_ns is nearest the
# derived second within 180 s. A prefix match on the derived second matches
# nothing, because the arm's token carries microseconds.
row_tag=""
ROW_TRIES=${GRADE_ROW_TRIES:-45}
for i in $(seq 1 "$ROW_TRIES"); do
  row_tag=$(python3 - "$MANIFEST" "$slot_ep" <<'PY'
import json, sys
man, slot_ep = sys.argv[1], float(sys.argv[2])
best = None
for line in open(man):
    line = line.strip()
    if not line:
        continue
    r = json.loads(line)
    ct = r.get("files", {}).get("room.db.bak", {}).get("copy_time_ns")
    if ct is None:
        continue
    d = abs(ct / 1e9 - slot_ep)
    if d <= 180 and (best is None or d < best[0]):
        best = (d, r["slot"])
print(best[1] if best else "")
PY
)
  [ -n "$row_tag" ] && break
  sleep 4
done

if [ -z "$row_tag" ]; then
  miss="slot $slot_tag (derived $(date -d "@$slot_ep" +%Y%m%dT%H%M%S) = start '$start_txt' $offs_txt) -- NO manifest row within the row-wait window. Graded NOTHING: no row, no verdict. posted by grade-slot.sh"
  # --dry prints once on stdout; a real run prints once on stderr and posts once.
  # (Printing in both places duplicated the MISS line in every --dry transcript.
  #  This ordering lived only in ~/.local/bin for a day, unrecorded here.)
  if [ "$DRY" = 1 ]; then printf '%s\n' "$miss"; exit 4; fi
  echo "$miss" >&2
  agentbus send "$miss" --topic work --from "$FROM" --reply-to "$PARENT" >/dev/null
  exit 4
fi
slot_tag=$row_tag

# The witness runs every 5 min and captures a WINDOW, so the log covering this
# slot is written up to ~5 min AFTER the slot second. Pick the newest log whose
# "# captured" is >= the slot, and wait for it.
log=""
for i in $(seq 1 90); do
  for cand in $(ls -1t "$WITNESS_DIR"/pairsplit2-*.log 2>/dev/null | head -6); do
    cap=$(sed -n 's/^# captured \([^;]*\);.*/\1/p' "$cand" | head -1)
    cap_ep=$(date -d "$cap" +%s 2>/dev/null || echo 0)
    if [ "$cap_ep" -ge "$slot_ep" ]; then log="$cand"; break; fi
  done
  [ -n "$log" ] && break
  sleep 4
done
echo "slot_ep=$slot_ep slot_tag=$slot_tag log=${log:-NONE-covering-slot}"

{
  echo "slot $slot_tag (derived $(date -d "@$slot_ep" +%Y%m%dT%H%M%S) = start $start_txt $offs_txt), graded automatically"
  echo
  if [ -n "${log:-}" ]; then
    grep -E '^# (captured|MainPID|HEAD|RUNNING|wal )|^# holder-class|checkpoint-pre|checkpoint:' "$log" | tail -12
    echo
  fi
  out=$(bash "$VERIFY" "$slot_tag" 2>&1); rc=$?
  printf '%s\n' "$out" | sed 's/^/  /'
  echo
  echo "  verifier rc=$rc -- posted by /mnt/nas/host-ops/agent-chat/grade-slot.sh"
} > "/tmp/grade-slot-body.$$" 2>&1
body=$(cat "/tmp/grade-slot-body.$$")
rm -f "/tmp/grade-slot-body.$$"

if [ "$DRY" = 1 ]; then
  printf '%s\n' "$body"
  exit 0
fi
resp=$(agentbus send "$body" --topic work --from "$FROM" --reply-to "$PARENT" 2>&1)
echo "$resp"
# The guard file is what the NEXT run reads to decide "already graded", so it must
# only ever hold a successful post. Writing $resp unconditionally made a failed
# send (bad PARENT, bus down) replace the record with an error string.
if printf '%s' "$resp" | python3 -c 'import json,sys; sys.exit(0 if isinstance(json.load(sys.stdin).get("id"), int) else 1)' 2>/dev/null; then
  printf '%s\n' "$resp" > "$GUARD_DIR/$slot_tag"
else
  echo "grade-slot.sh: POST did not return an id -- guard file for $slot_tag left untouched" >&2
  exit 5
fi
