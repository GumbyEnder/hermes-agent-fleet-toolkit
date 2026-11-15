#!/usr/bin/env bash
# install-mirror-legs.sh -- write the published mirror legs from the repo, in one step.
#
# WHY THIS EXISTS (measured 2026-11-15, 09:38): commit 6158258 moved
# chat-room/wal-ops/grade-slot.sh (blob 9bbc8f3, md5 2513aee7), regenerated MANIFEST
# row 8 and installed ~/.local/bin/grade-slot.sh by cp+mv. The third leg,
# /mnt/nas/host-ops/agent-chat/grade-slot.sh, kept a1b524e9 (15,606 B) for 25 minutes
# while every gate read green: check-manifest hashes the repo worktree,
# check-grade-timer asks whether the timer ARMED, and nothing hashed either extra leg.
# The room had already cited the stale path as "installed at ..." while MANIFEST row 8
# had moved under it. A leg no gate reads has to be written by the same step that writes
# the roster, or it drifts on the first hurried install.
#
# WHY A PER-FILE TABLE, NOT ONE GLOBAL LEG LIST: the legs are not the same set.
# grade-slot.sh is exec'd from ~/.local/bin and CITED at the NAS path; the witness is
# exec'd from the NAS path only. A global list would invent a ~/.local/bin copy of the
# witness that nothing runs -- one more leg to drift.
#
# usage: install-mirror-legs.sh [--dry]
#   --dry   print the plan and the md5 each leg would get; write nothing
# rc: 0 every published leg already matches the repo, or was written; 1 a write failed.
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")" || exit 2

DRY=0
for a in "$@"; do
  case "$a" in
    --dry) DRY=1 ;;
    *) echo "usage: install-mirror-legs.sh [--dry]" >&2 ; exit 2 ;;
  esac
done
NAS=${NAS:-/mnt/nas/host-ops/agent-chat}
LEG_TABLE="
grade-slot.sh|$HOME/.local/bin:$NAS
witness-split-pair-v2.sh|$NAS
verify-slot-manifest.sh|$NAS
"
# verify-slot-manifest.sh is in this table because it IS the gate grade-slot
# executes (AGENT_CHAT_VERIFIER defaults to the NAS path) and it had no writer:
# until 2026-11-15 the NAS copy was maintained by hand, so an edit to the repo
# verifier changed no verdict until somebody remembered to copy it, and no gate
# hashed it. One leg, the NAS one -- ~/.local/bin carries no verifier, and
# inventing one would be a leg nothing runs.

rc=0
while IFS='|' read -r f legs; do
  [ -n "${f:-}" ] || continue
  src="./$f"
  if [ ! -f "$src" ]; then echo "FAIL no repo source: $src" >&2 ; rc=1 ; continue ; fi
  want=$(md5sum "$src" | awk '{print $1}')
  oldifs=$IFS; IFS=:
  for d in $legs; do
    IFS=$oldifs
    m="$d/$f"
    have=$(md5sum "$m" 2>/dev/null | awk '{print $1}')
    if [ "$have" = "$want" ]; then echo "ok   $m ($want)" ; continue ; fi
    if [ ! -d "$d" ]; then echo "skip $m (dir absent)" ; continue ; fi
    if [ "$DRY" = 1 ]; then echo "dry  would write $m  ${have:-absent} -> $want" ; continue ; fi
    # cp to a temp name IN THE SAME DIRECTORY then mv: an open fd keeps the old inode,
    # so a unit already running this script finishes on coherent old bytes. Never rewrite
    # the destination in place (the in-place hazard is documented at the top of grade-slot.sh).
    tmp="$d/.$f.tmp-$$"
    if cp "$src" "$tmp" && mv -f "$tmp" "$m"; then
      echo "wrote $m  ${have:-absent} -> $(md5sum "$m" | awk '{print $1}')"
    else
      echo "FAIL write $m" >&2 ; rm -f "$tmp" ; rc=1
    fi
    IFS=:
  done
  IFS=$oldifs
done < <(printf '%s\n' "$LEG_TABLE" | sed '/^[[:space:]]*$/d')
exit $rc
