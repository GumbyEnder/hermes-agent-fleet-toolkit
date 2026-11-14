#!/usr/bin/env bash
# Grade the scheduled backup slot that the witness will capture, then post the
# result into the chat room as a child of a named message.
#
# Why this exists: the witness slot is ExecMainStartTimestamp + BACKUP_INTERVAL
# (1 h), not a wall-clock time, so a restart moves it. A grader that sleeps to a
# fixed hour grades the wrong second or nothing. This derives the next slot from
# the live unit, waits for the manifest row to appear, then runs the read-only
# verifier.
#
# usage: grade-slot.sh [--dry] [PARENT_ID]
#   --dry      print the post body, do not send
#   PARENT_ID  room message to thread under (default 4303)
set -uo pipefail

DRY=0
PARENT=4303
for a in "$@"; do
  case "$a" in
    --dry) DRY=1 ;;
    *) PARENT="$a" ;;
  esac
done

SVC=hermes-agent-chat-bus.service
VERIFY=/mnt/nas/host-ops/agent-chat/verify-slot-manifest.sh
MANIFEST=/mnt/nas/agent-chat/room.db.bak.manifest.jsonl
WITNESS_DIR=/home/gumbyender/agents/agent-chat
export PATH=/home/gumbyender/.local/bin:$PATH

start_txt=$(systemctl --user show "$SVC" -p ExecMainStartTimestamp --value)
start_ep=$(date -d "$start_txt" +%s) || { echo "cannot parse start '$start_txt'" >&2; exit 2; }
interval=3600                       # bus prints the real value at startup; see journal
slot_ep=${SLOT_EP:-$((start_ep + interval))}
now=$(date +%s)
if [ -z "${SLOT_EP:-}" ] && [ "$now" -gt $((slot_ep + 120)) ]; then
  echo "slot $slot_ep already passed by $((now - slot_ep))s -- nothing to wait for" >&2
  exit 3
fi
if [ -z "${SLOT_EP:-}" ]; then
  wait_s=$((slot_ep + 8 - now))
  [ "$wait_s" -gt 0 ] && sleep "$wait_s"
fi

slot_tag=$(date -d "@$slot_ep" +%Y%m%dT%H%M%S)

# The ARM's own slot tag carries microseconds and can sit 1-2 s AFTER the
# derived second (03:18:01.047425 for the 03:18:00 clock), so a prefix match on
# the derived second matches NOTHING and the verifier answers "FAIL no manifest
# rows matched" about a slot that exists -- that is the grader lying, not a
# failed slot. Resolve the tag from the manifest instead: the row whose
# copy_time_ns is nearest the derived second within 180 s.
row_tag=""
for i in $(seq 1 45); do
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
if [ -n "$row_tag" ]; then slot_tag="$row_tag"; else
  echo "no manifest row within 180s of $slot_ep -- grading the derived tag and saying so" >&2
fi

# the row is the thing being graded; wait for it rather than for a clock
for i in $(seq 1 30); do
  grep -q "\"slot\": \"$slot_tag" "$MANIFEST" 2>/dev/null && break
  sleep 2
done
# The witness runs every 5 min and captures a WINDOW, so the log that covers
# this slot is written up to ~5 min AFTER the slot second -- never take the
# newest log at slot+8s, that is the previous window and its header would be
# posted as if it described this slot. Pick the newest log whose "# captured"
# is >= the slot, and wait for it.
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
  echo "slot $slot_tag (derived $(date -d "@$slot_ep" +%Y%m%dT%H%M%S) = start $start_txt + ${interval}s), graded automatically"
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
agentbus send "$body" --topic work --from buzzy --reply-to "$PARENT"
