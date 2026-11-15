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
# usage: install-mirror-legs.sh [--dry] [--allow-absent]
#   --dry           print the plan and the md5 each leg would get; write nothing
#   --allow-absent  tolerate a leg DIRECTORY that is absent, and report it as a skip
#                   (also `REQUIRE_LEGS=0` in the environment). Without it an absent
#                   directory is a FAIL.
# rc: 0 every published leg already matches the repo, or was written;
#     1 a write failed / no repo source / a leg directory is absent (default);
#     2 usage;
#     3 --dry found at least one leg it WOULD write (i.e. drift), nothing written;
#     4 --allow-absent tolerated an absent leg directory, so at least one leg was
#       never measured (nothing about that leg is known: not ok, not drift).
# rc=3 exists because a --dry run that prints drift and exits 0 makes the caller's
# verdict a string match on this script's phrasing: reword "would write" and a gate
# that greps for it reads green over a stale leg (measured 2026-11-15 11:5x on a
# shadow tree: tampered NAS leg -> `dry  would write ...` and rc=0). The exit status
# is the fact; the text is only its label. Callers must OR rc with the text.
#
# WHY AN ABSENT LEG DIRECTORY IS A FAIL, NOT A SKIP (measured 2026-11-15 ~10:41):
# `NAS=<absent dir> ./install-mirror-legs.sh` printed `ok .../.local/bin/grade-slot.sh`,
# `skip ... (dir absent)` x2 and exited 0 -- "nothing published", successful exit. The
# absent-directory case is not exotic: the NAS leg is a mount, so on a box where it is
# not mounted the mount point is simply an empty directory (or absent) and the writer
# reports success having written nothing, leaving the exec'd witness stale. That is
# 6158258 re-armed at the write step, with a green return code on top of it. The skip
# line said so; rc -- the token a caller quotes -- did not.
#
# So the absent branch fails by default and the skip path has to be ASKED for
# (--allow-absent / REQUIRE_LEGS=0), which keeps the rehearsal arms able to arm it.
# A tolerated skip is still not green: it exits 4, because "I did not measure this leg"
# is a third state, not a pass and not drift. Two states, two rc, neither of them 0.
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")" || exit 2

DRY=0
ALLOW_ABSENT=0
case "${REQUIRE_LEGS:-1}" in 0|no|false|off) ALLOW_ABSENT=1 ;; esac
for a in "$@"; do
  case "$a" in
    --dry) DRY=1 ;;
    --allow-absent) ALLOW_ABSENT=1 ;;
    *) echo "usage: install-mirror-legs.sh [--dry] [--allow-absent]" >&2 ; exit 2 ;;
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
would=0
skipped=0
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
    if [ ! -d "$d" ]; then
      # Absent leg directory = FAIL by default (see the header): the NAS leg is a
      # mount, so "absent" is what a failed mount looks like, and reporting success
      # after writing nothing is how a stale exec'd witness survives a green gate.
      if [ "$ALLOW_ABSENT" = 1 ]; then
        echo "skip $m (dir absent, tolerated by --allow-absent/REQUIRE_LEGS=0)"
        skipped=$((skipped+1))
      else
        echo "FAIL absent $m (dir absent: $d -- a leg that was not measured is not an ok)" >&2
        rc=1
      fi
      continue
    fi
    if [ "$DRY" = 1 ]; then
      echo "dry  would write $m  ${have:-absent} -> $want"
      would=$((would+1))
      continue
    fi
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
# A --dry run that would change a published leg exits 3. rc stays the writer's verdict
# whether or not the caller can parse the line above it.
# Precedence when several states hold at once: 1 (a leg is broken/unreadable) beats
# 3 (a leg would be written) beats 4 (a leg was tolerated absent). All three are
# non-zero; 0 is reserved for "every leg was measured and matches, or was just written".
if [ "$rc" = 0 ]; then
  if [ "$DRY" = 1 ] && [ "$would" -gt 0 ]; then
    rc=3
  elif [ "$skipped" -gt 0 ]; then
    rc=4
  fi
fi
exit $rc
