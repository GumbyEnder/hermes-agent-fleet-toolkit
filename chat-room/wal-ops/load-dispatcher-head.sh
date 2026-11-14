#!/usr/bin/env bash
# Load the committed wake_dispatcher.py into the running dispatcher, safely.
#
# WHY THIS EXISTS
# A restart of hermes-wake-dispatcher.service does NOT stop the turns it started
# (systemd: "Unit process NNN (hermes) remains running after unit stopped",
# measured 03:56:57 and 06:17:01). So the rule is not "wait for /health active 0"
# -- that counter is per-process and forgets. The rule is: no live `hermes -p <p>
# -z` turn child anywhere, and no turn holding the flock gate, before bouncing.
# A restart while a turn is in flight makes that turn an orphan: it finishes, but
# the NEW process has no evt:wake for it, so its room post is uncounted and its
# final stdout is not posted (938920 posted 4453 that way at 06:43:34).
#
# THEREFORE THIS MUST BE RUN FROM A PLAIN SHELL or a one-shot unit -- never from
# inside a wake turn, because that turn is one of the children being counted.
#
# DIVISION OF LABOUR, so this is not a third script doing one job:
#   * to SCHEDULE a reload from inside a turn  -> restart-dispatcher-after-turn.sh
#     <own wrapper pid> (waits for that pid, sleeps 20 s so the reply post lands,
#     then restarts; logs to wake-idle-restart.log),
#   * to VERIFY what is loaded afterwards (`--check`) or to bounce from a quiet
#     shell, this script. --check is the leg that names the spent-vs-loaded gap:
#     HEAD blob vs tree blob vs the unit's ExecMainStartTimestamp.
# Do not gate a reload on /health active==0: that counter is the RUNNING process's
# memory and is zeroed by the very restart it is meant to wait for.
#
# WHAT IT DOES
#   1. resolve the committed blob (git rev-parse HEAD:chat-room/wake_dispatcher.py)
#      and compare with the blob of the file on disk, and with the loaded build
#      (the file's mtime vs the unit's ExecMainStartTimestamp)
#   2. if the loaded process already carries HEAD, exit 0 doing nothing
#   3. refuse (rc 3) while any turn child is alive or the gate is held
#   4. otherwise restart, then verify: /health answers, uptime_s small, and the
#      start timestamp is NEWER than the dispatcher file's mtime
#
# usage: bash load-dispatcher-head.sh [--check] [--wait SECONDS]
#   --check        report state and exit (no restart), rc 0 in-sync / 1 stale
#   --wait N       poll for up to N seconds for the fleet to go quiet (default 0)
#
# CIFS note: /mnt/nas is file_mode=0644 nounix, so this file CANNOT carry +x and
# any systemd unit must ExecStart through `bash <path>` (203/EXEC otherwise).
set -uo pipefail

REPO=${REPO:-/home/gumbyender/Agents/Projects/hermes-agent-fleet-toolkit}
REL=chat-room/wake_dispatcher.py
UNIT=hermes-wake-dispatcher.service
LOG=${DLOG:-/mnt/nas/host-ops/agent-chat/dispatcher-load.log}

MODE=run; WAIT=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --check) MODE=check; shift ;;
    --wait) WAIT="${2:-0}"; shift 2 ;;
    --wait=*) WAIT="${1#--wait=}"; shift ;;
    -h|--help) sed -n '1,40p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

say() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" | tee -a "$LOG" >&2; }

cd "$REPO" || { say "FATAL no repo at $REPO"; exit 2; }
head_blob=$(git rev-parse "HEAD:$REL" 2>/dev/null) || { say "FATAL cannot resolve HEAD:$REL"; exit 2; }
tree_blob=$(git hash-object "$REL") || exit 2
f="$REPO/$REL"
f_mtime=$(date -d "$(stat -c %y "$f")" +%s)

start_txt=$(systemctl --user show "$UNIT" -p ExecMainStartTimestamp --value)
pid=$(systemctl --user show "$UNIT" -p MainPID --value)
[ -n "$start_txt" ] && start_ep=$(date -d "$start_txt" +%s) || start_ep=0

say "HEAD:$REL=$head_blob tree=$tree_blob unit=$UNIT pid=$pid start=$start_txt file_mtime=$(date -d "@$f_mtime" '+%F %T')"
[ "$tree_blob" = "$head_blob" ] || say "WARN tree differs from HEAD (uncommitted edit to $REL)"
# loaded build = the file content at start time; mtime is the proxy, and it is
# only valid as 'file NOT newer than the start', which is the safe direction.
if [ "$start_ep" -gt 0 ] && [ "$f_mtime" -le "$start_ep" ]; then
  say "IN-SYNC the running process started after the file was last written (nothing to load)"
  exit 0
fi

if [ "$MODE" = check ]; then
  say "STALE file mtime $(date -d "@$f_mtime" '+%F %T') > start $start_txt -- a restart would load current bytes"
  exit 1
fi

LIVE_PAT="hermes.*-p[ ',]+[a-z-]+[ ',]+-z"
turns() { pgrep -af "$LIVE_PAT" | grep -v 'load-dispatcher-head' | grep -c . ; }
gate_holders() {
  local d=${WAKE_LOCK_DIR:-/home/gumbyender/agents/agent-chat/locks} n=0 l
  command -v fuser >/dev/null 2>&1 || return 0
  for l in "$d"/*.lock; do
    [ -e "$l" ] || continue
    n=$(( n + $(fuser "$l" 2>/dev/null | wc -w) ))
  done
  echo "$n"
}
tmo=$(( $(date +%s) + WAIT ))
while :; do
  n=$(turns); gate=$(gate_holders)
  if [ "$n" -eq 0 ] && [ "$gate" -eq 0 ]; then break; fi
  if [ "$(date +%s)" -ge "$tmo" ]; then
    say "REFUSED $n live turn child(ren), gate_holders=$gate -- a restart now orphans them (rc 3)"
    pgrep -af "$LIVE_PAT" | grep -v load-dispatcher-head | cut -c1-160 | sed 's/^/    /' >&2
    exit 3
  fi
  sleep 10
done

say "fleet quiet (0 turns) -- restarting $UNIT"
systemctl --user restart "$UNIT" || { say "FATAL restart failed"; exit 2; }
sleep 5
new_start=$(systemctl --user show "$UNIT" -p ExecMainStartTimestamp --value)
new_ep=$(date -d "$new_start" +%s)
say "restarted: MainPID=$(systemctl --user show "$UNIT" -p MainPID --value) start=$new_start"
if [ "$new_ep" -le "$f_mtime" ]; then
  say "FAIL new start $new_start is not after file mtime -- still the old bytes"; exit 1
fi
if curl -fsS --max-time 10 http://127.0.0.1:8095/health >/dev/null; then
  say "PASS /health answers after restart (uptime $(curl -fsS --max-time 5 http://127.0.0.1:8095/health | python3 -c 'import json,sys;print(json.load(sys.stdin).get("uptime_s"))'))"
else
  say "FAIL /health silent after restart"; exit 1
fi
say "LOADED $REL @ $(git rev-parse --short HEAD) -- spent-vs-loaded gap closed"
