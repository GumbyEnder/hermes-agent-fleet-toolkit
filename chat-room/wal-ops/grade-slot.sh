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
# VERDICT AGE IS SECONDS, NOT ~37 MIN (corrected 2026-11-14; the earlier note
# here described the pre-GRACE design and is false against the journal). The :05
# tick derives the NEXT slot of its OWN hour and sleeps to it: 09:05 ->
# slot_ep 1794666478 = 09:27:58, graded 09:28:06; 12:05 -> 12:27:58, graded
# 12:28:41. No run derives a past slot unless SLOT_EP is passed, so the old
# "too-early tick hits the guard path and exits 0" line described a path that
# never executed: the top guard tests the DERIVED second, which no run ever
# writes (see the authoritative re-test after slot_tag=$row_tag).
#
# NEVER REWRITE THIS FILE IN PLACE WHILE A TICK IS RUNNING (cp, or `install`:
# measured 2026-11-14, install left the inode unchanged, 15479751, across a 6 B
# -> 11,643 B write). A running bash resumes from its fd offset, not from a line
# boundary. Measured: the 11:05 tick started on the 8,585 B build, slept 1421 s
# with fd 255 parked at byte 5186 (the blank line after the wait block), the file
# was rewritten in place to a longer build, and at 11:28:41 it resumed at 5186 --
# which in the longer build is the middle of the guard line -- and died with
# `line 116: syntax error near unexpected token ';'` / `'; then'`, status
# 2/INVALIDARGUMENT. All eight committed builds pass `bash -n`, so no committed
# build can produce that error: the bytes read were a mixture of two builds, and
# the slot went ungraded by the timer. Write to a temp name and `mv` it into
# place; an open fd then keeps the OLD inode and finishes on coherent old bytes
# (that is the only reason the "(deleted)" inode shape is safe).
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
# The unit that WRITES the capture. Its own exit status is the second instrument for this
# slot: the trailer (below) is written by the capture path, the journal is written by
# systemd, and they disagree only when the trailer lost its race to tee.
WITNESS_SVC=${AGENT_CHAT_WITNESS_SVC:-hermes-pairsplit-witness.service}
VERIFY=${AGENT_CHAT_VERIFIER:-/mnt/nas/host-ops/agent-chat/verify-slot-manifest.sh}
# The verifier that grades is the CIFS copy, so a NAS-side edit changes every
# verdict with no gate in the way (frodo, 2026-11-14). The rehearsal harness was
# moved out of the share for exactly this reason; this leg pins the executed gate
# to the committed blob and says both hashes out loud.
REPO=${AGENT_CHAT_REPO:-/home/gumbyender/Agents/Projects/hermes-agent-fleet-toolkit}
VERIFY_REL=chat-room/wal-ops/verify-slot-manifest.sh
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

# The one-grade-per-slot guard is AUTHORITATIVE only here. The cheap check at the
# top tests the DERIVED second (YYYYMMDDTHHMMSS, no fraction); this line renames
# slot_tag to the manifest ROW tag, which carries microseconds and can sit 1-2 s
# later (derived 20261114T092758 -> row 20261114T092759.702524-0500). Two
# different names, so the file a run WRITES was never the file the top check
# LOOKED FOR and the guard had never fired for any slot: measured 2026-11-14
# 09:28:25, a run without --force re-derived and re-graded 092759 eighteen
# seconds after the timer had graded it (and only a bad PARENT stopped the
# duplicate post). Re-test on the name that actually gets written.
if [ "$FORCE" = 0 ] && [ "$DRY" = 0 ] && [ -e "$GUARD_DIR/$slot_tag" ]; then
  echo "slot $slot_tag already graded ($(cat "$GUARD_DIR/$slot_tag" 2>/dev/null)) -- nothing to do"
  exit 0
fi

# The witness runs every 5 min and captures a WINDOW, so the log covering this
# slot is written up to ~5 min AFTER the slot second. Pick the OLDEST log whose
# "# captured" is >= the slot, and wait for it.
# Why oldest and not newest: the test is one-sided (captured >= slot_ep), so in a
# backfill EVERY later capture satisfies it and `ls -1t | head -6` handed back the
# newest one. Measured 2026-11-15 11:49: SLOT_EP=1794752913 (slot
# 20261115T092844.082424-0500) graded against pairsplit2-20261115T112835.log and
# printed THAT capture's trailer -- rc=0 GRADED late=11.874 -- as the 09:28 slot's
# verdict, while the slot's own capture (092835) says rc=0 VACUOUS_STRUCTURAL
# late=11.082. A verdict about an hour, taken from a window two hours later, with
# nothing on the line saying so. Oldest-covering is identical to newest-covering on
# the live path (only one capture is >= the slot second yet) and correct for every
# backfill inside the usable-read window. The capture's own `slot=` token is also
# read: if it names a different slot, the trailer is refused, never relabelled.
# Scan from the filename, not from the head of the directory: names are
# timestamped, so a capture made before slot-300s cannot cover the slot.
log=""
cap_slot=""
cutoff_tag=$(date -d "@$((slot_ep - 300))" +%Y%m%dT%H%M%S)
for i in $(seq 1 90); do
  for cand in $(ls -1 "$WITNESS_DIR"/pairsplit2-*.log 2>/dev/null | sort); do
    cname=${cand##*/}; cname=${cname#pairsplit2-}; cname=${cname%.log}
    case "$cname" in
      [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]) ;;
      *) continue ;;
    esac
    [ "$cname" \< "$cutoff_tag" ] && continue
    cap=$(sed -n 's/^# captured \([^;]*\);.*/\1/p' "$cand" | head -1)
    cap_ep=$(date -d "$cap" +%s 2>/dev/null || echo 0)
    if [ "$cap_ep" -ge "$slot_ep" ]; then log="$cand"; break; fi
  done
  [ -n "$log" ] && break
  sleep 4
done
[ -n "${log:-}" ] && cap_slot=$(grep -oE 'slot=[0-9]{8}T[0-9]{6}\.[0-9]+' "$log" 2>/dev/null | tail -1 | cut -d= -f2)
echo "slot_ep=$slot_ep slot_tag=$slot_tag log=${log:-NONE-covering-slot} capture_slot=${cap_slot:-ABSENT}"

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
  # Verifier identity: the gate that grades must be the committed blob.
  verify_head=$(git -C "$REPO" rev-parse "HEAD:$VERIFY_REL" 2>/dev/null || echo UNAVAILABLE)
  verify_disk=$(git -C "$REPO" hash-object "$VERIFY" 2>/dev/null || echo UNAVAILABLE)
  # HEAD resolvable but the executed file unhashable is a MISMATCH, not an
  # excuse: a missing or unreadable verifier must not read as "unasserted".
  if [ "$verify_head" = UNAVAILABLE ]; then
    echo "  INFO verifier identity UNASSERTED disk=$verify_disk HEAD=$verify_head (no git at $REPO)"
  elif [ "$verify_disk" = "$verify_head" ]; then
    echo "  PASS verifier identity disk=$verify_disk HEAD=$verify_head"
  else
    echo "  FAIL verifier identity disk=$verify_disk HEAD=$verify_head -- the verifier that produced the verdict above is NOT the committed one; reinstall it from $REPO"
    identity_fail=1
  fi
  echo
  echo "  verifier rc=$rc -- posted by $(readlink -f "$0") (verifier $VERIFY)"
  # --- completeness: $VERIFY grades MANIFEST LEGS, not the slot. Measured 2026-11-15
  # 08:28:46 (room 5443): a capture holding NO checkpoint line at all graded
  # "RESULT PASS (0 failed legs)" / "verifier rc=0" into the room while the witness unit
  # exited 4/NOPERMISSION in the journal. So a RESULT line is a statement about the copy
  # and never about the slot: name the artifact's own trailer and the writer unit's own
  # exit, and let the reader see the pair. The trailer is written ONLY on the witness
  # capture path (>= 4cdfa20), so a pre-patch capture has none and its verdict lives in
  # the journal alone -- that absence is a finding, not a pass.
  vline=$(grep -E '^# VERDICT: rc=' "$log" 2>/dev/null | tail -1)
  jwin=$(journalctl --user -u "$WITNESS_SVC" --since "@$slot_ep" --until "@$((slot_ep + 150))" --no-pager 2>/dev/null)
  wstat=$(printf '%s\n' "$jwin" | grep -oE 'code=(exited|killed), status=[^ ]+' | tail -1)
  # systemd writes `code=exited, status=N/...` into the journal ONLY when the process
  # exits non-zero. A green oneshot leaves "Starting"/"Finished" and no status token,
  # so a grep alone reports UNAVAILABLE for exactly the runs that succeeded. Measured
  # 2026-11-15 09:28:46 (room 5473): the witness finished status=0 at 09:28:44.458449,
  # the unit journal holds Starting 09:25:00 and Finished 09:28:44, and this field printed
  # "UNAVAILABLE". Absence of a failure line is not absence of the run.
  wstat_note=
  if [ -z "$wstat" ] && printf '%s\n' "$jwin" | grep -q "Finished $WITNESS_SVC"; then
    wstat="code=exited, status=0/SUCCESS"
    wstat_note=" (synthesized from the journal's Finished line -- systemd logs code=/status= only on a non-zero exit)"
  fi
  refused=0
  case "${cap_slot:-}" in
    "") verdict_slot="${slot_tag}" ;;   # capture carries no token -- nothing to refuse on
    *) case "$slot_tag" in
         "$cap_slot"*) verdict_slot="${slot_tag}" ;;
         *) verdict_slot="REFUSE" ;;
       esac ;;
  esac
  if [ -n "$vline" ] && [ "$verdict_slot" = REFUSE ]; then
    echo "  slot verdict: REFUSED -- this slot has no capture; ${log##*/} carries slot=$cap_slot's trailer (${vline#\# VERDICT: }), which is that slot's verdict and not this one's"
    vline=""
    refused=1
  fi
  if [ -n "$vline" ]; then
    echo "  slot verdict: ${vline#\# VERDICT: }"
  elif [ "$refused" = 0 ]; then
    echo "  slot verdict: ABSENT -- this capture carries no '# VERDICT: rc=' trailer (witness build predates 4cdfa20), so the RESULT above grades manifest legs only and is NOT this slot's verdict"
  fi
  if [ -n "$wstat" ]; then
    echo "  witness exit: $wstat$wstat_note (journal, $WITNESS_SVC, slot..slot+150s)"
    # The trailer rc and systemd's ExecMainStatus are the same fact written by two
    # different processes. A disagreement means the trailer lost its race to tee (see the
    # witness's own note) and the journal is the honest witness -- say so, do not pick one.
    if [ -n "$vline" ]; then
      trc=$(printf '%s' "$vline" | sed -nE 's/^# VERDICT: rc=([0-9]+).*/\1/p')
      # `code=killed, status=15/TERM` is signal 15, and the witness writes rc=128+sig for
      # exactly that case -- compare like with like, or every trapped signal reads as a
      # MISMATCH (143 vs 15) and the pair stops being worth reading.
      jrc=$(printf '%s' "$wstat" | sed -nE 's/^code=exited, status=([0-9]+).*/\1/p')
      if [ -z "$jrc" ]; then
        jrc=$(printf '%s' "$wstat" | sed -nE 's/^code=killed, status=([0-9]+).*/1\1/p' | sed -E 's/^1([0-9]+)$/\1/')
        jrc=$(( 128 + ${jrc:-0} ))
      fi
      [ -n "$trc" ] && [ "$trc" != "$jrc" ] && echo "  MISMATCH trailer rc=$trc journal=$wstat (as rc $jrc) -- the trailer lost the race to tee; read ExecMainStatus, not the trailer"
    fi
  elif printf '%s\n' "$jwin" | grep -q "Starting $WITNESS_SVC"; then
    echo "  witness exit: INCOMPLETE -- journal shows Starting and no Finished for $WITNESS_SVC in slot..slot+150s; the run did not end"
  else
    echo "  witness exit: UNAVAILABLE (no journal line for $WITNESS_SVC in slot..slot+150s)"
  fi
} > "/tmp/grade-slot-body.$$" 2>&1
body=$(cat "/tmp/grade-slot-body.$$")
rm -f "/tmp/grade-slot-body.$$"
# The body block runs in a subshell, so the identity verdict is read back out of
# the text it produced rather than out of a variable set inside it.
case "$body" in
  *"FAIL verifier identity"*) identity_fail=1 ;;
  *) identity_fail=0 ;;
esac

if [ "$DRY" = 1 ]; then
  printf '%s\n' "$body"
  # --dry sends nothing, but it still reports the identity verdict so the gate
  # can be exercised both ways without posting a fake grade into the room.
  [ "$identity_fail" = 1 ] && exit 6
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
# Post first, then fail: a mismatched verifier is a finding the room must see,
# and the guard must record that the slot was graded. Exit 6 makes the unit's
# own status carry the failure instead of hiding it behind a successful POST.
if [ "$identity_fail" = 1 ]; then
  echo "grade-slot.sh: verifier identity MISMATCH -- slot $slot_tag was graded by an uncommitted verifier (see the posted body)" >&2
  exit 6
fi
