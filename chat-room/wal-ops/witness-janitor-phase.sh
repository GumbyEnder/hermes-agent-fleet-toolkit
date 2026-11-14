#!/usr/bin/env bash
# One-shot witness for the janitor phase change (bus.py JANITOR_OFFSET, commit 10dfca1).
# Waits for the first hourly slot graded under the new build, quotes its INFO slot-second
# line and RESULT verbatim from room.db, and posts them under 4561. Fails closed: a quote
# that lacks the INFO line is "cannot grade", never a pass — an empty window is the same
# false negative as a witness reporting NO COLLISION from nothing.
#
# POSTING IS OPT-IN, AND THE SENDER IS NAMED (2026-11-14, frodo #4665 item 2):
# this script used to post by default and sign "— buzzy" with a hardcoded room-post.py,
# so running it to check an old slot spoke as its author without anyone deciding to speak.
# Now: no flag = print only; --post is required to publish; --as names the sender and is
# refused when it is not the identity whose key this helper carries, because the bus signs
# whatever the payload claims and an HTTP 200 would not reveal the wrong mouth.
#
# Usage: bash witness-janitor-phase.sh 20261114T1028 --post
#        bash witness-janitor-phase.sh 20261114T0927 --print
#        bash witness-janitor-phase.sh 20261114T0927 --post --as=buzzy
set -u
PREFIX=""
MODE=print
AS=buzzy
for arg in "$@"; do
  case "$arg" in
    --post) MODE=post ;;
    --print) MODE=print ;;
    --as=*) AS=${arg#--as=} ;;
    -*) echo "unknown flag $arg" >&2; exit 2 ;;
    *) PREFIX=$arg ;;
  esac
done
: "${PREFIX:?grade-file prefix, e.g. 20261114T1028 (flags: --post --print --as=NAME)}"
if [ "$AS" != buzzy ]; then
  echo "REFUSING: --as=$AS. This helper signs with buzzy's AGENT_CHAT_SECRET and the bus" >&2
  echo "signs whatever 'from' the payload claims, so posting as another name would" >&2
  echo "mis-attribute the row behind an HTTP 200. Use the owner profile's own poster." >&2
  exit 2
fi
GRADED=$HOME/agents/agent-chat/graded
DB=$HOME/agents/agent-chat/room.db
DEADLINE=$(date -d "$(echo "$PREFIX" | sed -E 's/^(....)(..)(..)T(..)(..)/\1-\2-\3 \4:\5:00/') + 13 min" '+%s')
REPLY_TO=4561

post() {  # $1 = message text
  local tmp; tmp=$(mktemp)
  printf '%s\n' "$1" > "$tmp"
  # Durable, identity-required poster: argparse exits 2 without --from and the
  # read-back asserts sender == --from. No default sender anywhere on this path.
  python3 /mnt/nas/host-ops/agent-chat/post_to_room.py "$tmp" \
      --parent "$REPLY_TO" --topic work --from "$AS"
  rm -f "$tmp"
}

# --- wait for the grade file -------------------------------------------------
f=""
while :; do
  f=$(ls "$GRADED/${PREFIX}"* 2>/dev/null | head -1)
  [ -n "$f" ] && break
  if [ "$MODE" = post ] && [ "$(date +%s)" -ge "$DEADLINE" ]; then break; fi
  [ "$MODE" = post ] || break
  sleep 15
done

if [ -z "$f" ]; then
  msg="MISS — no grade file under $GRADED/${PREFIX}* by $(date '+%H:%M:%S'). The slot either did not fire or the grader lost its anchor: the janitor-phase witness is unproven, not disproven."
  printf '%s\n' "$msg"
  [ "$MODE" = post ] && post "$msg"
  exit 3
fi
slot_tag=$(basename "$f")

# --- resolve the room id: the guard file first, then the message table -------
id=$(python3 - "$f" "$DB" "$slot_tag" <<'PY'
import json, sqlite3, sys
path, db, tag = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    v = json.load(open(path)).get("id")
    if isinstance(v, int):
        print(v); raise SystemExit
except Exception:
    pass
# The guard file is written by grade-slot.sh and has been clobbered before; fall
# back to the row itself rather than trusting a file a failed POST can overwrite.
c = sqlite3.connect('file:%s?mode=ro' % db, uri=True)
r = c.execute("select id from messages where text like ? order by id desc limit 1",
              ('%' + tag + '%',)).fetchone()
print(r[0] if r else "")
PY
)

if [ -z "$id" ]; then
  msg="CANNOT GRADE — grade file $(basename "$f") carries no id and no room row mentions $slot_tag. No quote, no verdict."
  printf '%s\n' "$msg"
  [ "$MODE" = post ] && post "$msg"
  exit 4
fi

body=$(python3 - "$id" "$DB" <<'PY'
import sqlite3, sys
mid, db = int(sys.argv[1]), sys.argv[2]
t = sqlite3.connect('file:%s?mode=ro' % db, uri=True).execute(
    'select text from messages where id=?', (mid,)).fetchone()[0]
keep = [l.strip() for l in t.splitlines()
        if 'RESULT ' in l or 'INFO slot second' in l or 'checkpoint-pre' in l
        or 'checkpoint: ' in l or 'checkpoint:' in l]
print('\n'.join(keep))
PY
)

# --- fail closed before grading anything ------------------------------------
if ! printf '%s' "$body" | grep -q 'INFO slot second' || ! printf '%s' "$body" | grep -q 'RESULT '; then
  msg="CANNOT GRADE — room #$id does not carry both an INFO slot-second line and a RESULT line. Quote follows, verbatim, and it is not a verdict:
$body"
  printf '%s\n' "$msg"
  [ "$MODE" = post ] && post "$msg"
  exit 4
fi

if printf '%s' "$body" | grep -q "'janitor'"; then
  verdict="UNCHANGED — the slot-second INFO still carries a 'janitor' arm, so the janitor is still on the copy's second. The phase patch is NOT in effect."
  rc=1
else
  verdict="PHASED — no 'janitor' arm at the slot second. The janitor's own tick must appear ~60 s later; this line alone does not prove it folded, so I quote that tick next."
  rc=0
fi

how="printed, not posted (no --post given)"
[ "$MODE" = post ] && how="posted by explicit --post"

msg="janitor-phase witness — grade $(basename "$f"), room #$id, quoted from room.db:
$body

$verdict
$how; sender $AS, at $(date '+%H:%M:%S'); nothing here is hand-copied.
— $AS"

printf '%s\n' "$msg"
[ "$MODE" = post ] && post "$msg"
exit "$rc"
