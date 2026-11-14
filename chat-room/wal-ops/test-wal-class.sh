#!/usr/bin/env bash
# Harness for the `wal_class` field wal-checkpoint.sh appends to checkpoint.log.
# Four arms, each on a throwaway db with its own log/streak file, so production
# history is untouched. Pins the contract:
#
#   wal=absent            -> closed       (last close completed, sqlite unlinked)
#   wal=0                 -> stranded     (pair present, zero frames, HOLDER UNKNOWN --
#                                          an unqualified close leaves this with nobody
#                                          holding the file, so it must not read as
#                                          "a connection is open now")
#   wal>0, folded==log    -> resident     (bytes at high-water, all frames in the db)
#   wal>0, folded<log     -> unfolded     (frames live only in the -wal; pin=1)
#
# Holders are real background sqlite connections that sleep, because the whole
# point of these arms is the LAST-CLOSE rule: a holder that dies before the tick
# turns every arm into `closed` and the harness would grade its own accident.
# Run it as: bash test-wal-class.sh [path-to-wal-checkpoint.sh]
# Rehearse wal-checkpoint.sh's four wal_class arms against a throwaway db.
# Holders are plain background processes that sleep, so no coproc/stdio games.
set -u
W=$(mktemp -d /tmp/walclass-XXXXXX)
SCRIPT="${1:-$HOME/Agents/Projects/hermes-agent-fleet-toolkit/chat-room/wal-ops/wal-checkpoint.sh}"
DB="$W/r.db"
HOLD_SECS="${HOLD_SECS:-6}"

mk() {
  rm -f "$DB" "$DB-wal" "$DB-shm"
  python3 - "$DB" <<'PY'
import sqlite3,sys
c=sqlite3.connect(sys.argv[1]); c.execute("PRAGMA journal_mode=WAL")
c.execute("PRAGMA wal_autocheckpoint=0"); c.execute("CREATE TABLE t(x)"); c.commit(); c.close()
PY
}

ins() {
  python3 - "$DB" "$1" <<'PY'
import sqlite3,sys
c=sqlite3.connect(sys.argv[1]); c.execute("PRAGMA wal_autocheckpoint=0")
c.execute("BEGIN")
for i in range(int(sys.argv[2])): c.execute("INSERT INTO t VALUES (?)",(i,))
c.commit(); c.close()
PY
}

holder() {  # holder <idle|mark>  -> background, holds for HOLD_SECS
  python3 - "$DB" "$1" "$HOLD_SECS" <<'PY' &
import sqlite3,sys,time
db,mode,secs=sys.argv[1],sys.argv[2],float(sys.argv[3])
c=sqlite3.connect(db,timeout=10); c.execute("PRAGMA journal_mode=WAL")
if mode=="mark":
    c.execute("BEGIN"); c.execute("SELECT x FROM t").fetchone()
else:
    c.execute("SELECT COUNT(*) FROM t").fetchone()
time.sleep(secs); c.close()
PY
  echo $!
}

run_arm() {  # run_arm <label> <expected>
  local label="$1" want="$2"
  local log="$W/$label.log" st="$W/$label.streak"
  AGENT_CHAT_DB="$DB" AGENT_CHAT_CHECKPOINT_LOG="$log" AGENT_CHAT_PIN_STREAK="$st" \
    bash "$SCRIPT" >/dev/null 2>&1; local rc=$?
  local line got
  line=$(tail -1 "$log")
  got=$(printf '%s' "$line" | sed -n 's/.*wal_class=\([a-z_]*\).*/\1/p')
  printf '%-11s expected=%-11s got=%-11s rc=%s | %s\n' "$label" "$want" "$got" "$rc" \
    "$(printf '%s' "$line" | sed 's/^[^ ]* //')"
  [ "$got" = "$want" ] || echo "  MISMATCH"
}

echo "scratch=$W  script=$SCRIPT  hold=${HOLD_SECS}s"

mk;                              run_arm closed closed

# The arm the old wording could not express: 0 B pair, NO HOLDER AT ALL.
# A read-only open/close strands this pair and exits; nothing holds the file.
# Graded `closed`, NOT `stranded`, because this probe's own rw open + PASSIVE
# checkpoint + close is a qualifying close and unlinks it. That asymmetry is the
# whole reading: `wal=0` in this log means the tick's close was not the last
# qualifying one -- never "a holderless pair, again".
mk
python3 - "$DB" <<'PY'
import sqlite3,sys
c=sqlite3.connect("file:"+sys.argv[1]+"?mode=ro",uri=True)
c.execute("SELECT COUNT(*) FROM t").fetchone(); c.close()
PY
stat -c '  before arm: wal=%s' "$DB-wal" 2>/dev/null || echo "  before arm: wal=absent"
run_arm holderless_tidied closed

mk; holder idle >/dev/null; sleep 0.6
stat -c '  before arm: wal=%s' "$DB-wal" 2>/dev/null || echo "  before arm: wal=absent"
run_arm stranded stranded;        wait

mk; holder idle >/dev/null; sleep 0.6; ins 40
stat -c '  before arm: wal=%s' "$DB-wal" 2>/dev/null || echo "  before arm: wal=absent"
run_arm resident resident;       wait

mk; ins 10; holder mark >/dev/null; sleep 0.6; ins 200
stat -c '  before arm: wal=%s' "$DB-wal" 2>/dev/null || echo "  before arm: wal=absent"
run_arm unfolded unfolded;       wait

echo "--- full lines ---"
for f in "$W"/*.log; do echo "[$(basename "$f")]"; sed 's/^/  /' "$f"; done
rm -rf "$W"
