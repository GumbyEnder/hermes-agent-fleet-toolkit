#!/usr/bin/env bash
# check-exec-copies.sh -- witness the PUBLISHED COPIES that no roster hashed.
#
# WHY (measured 2026-11-15, cc21043 at 090470e): enumerate-exec-set.sh walked the
# executors and returned five UNWITNESSED paths. Four of them are NAS copies under
# $NAS (check-grade-timer.sh a15db06f, wal-keeper.py 5271966c, wal-unlink-watch.py
# 2bbd0c25, wal-unlink-watch-verdict.py 3a57232c) and the fifth is the LIVE leg a unit
# executes (~/agents/agent-chat/wal-checkpoint.sh ino 15344036 against repo ino
# 17726110). MANIFEST.md5 pins the REPO file of each; the mirror legs pin four other
# paths (LEG_TABLE in install-mirror-legs.sh). These five were byte-identical and in no
# table at all -- so the day one of them is edited in place, or swapped by a writer
# that renames a temp file over the name, every gate in the tree stays green and the
# path a unit execs carries bytes nobody recorded.
#
# TWO CLAIMS, TWO ARMS, and the difference is the point:
#   bytes    -- the copy's md5 must equal the repo file's md5 (drift arm). A published
#               copy that drifts from its repo file is a different build, and cc21043's
#               snapshot measured the whole class byte-identical, so any difference is
#               news. FAIL names drift=<n>.
#   identity -- the copy's (btime, md5) must equal the recorded pair (swap arm). Bytes
#               cannot see a rewrite that reproduced identical content: an atomic write
#               (temp + rename) preserves content and md5, so a md5-only witness grades
#               the swap green. Measured 2026-11-15 on the doc pair: the swap of
#               provenance-quoting.md kept md5 035c5016 and the 16/16 doc gate stayed
#               green until DOC-IDENTS.tsv existed.
#
# AN INODE NUMBER IS A COORDINATE ON CIFS, AND THAT IS MEASURED, NOT ASSUMED.
# Measured on the first production run of this gate (2026-11-15 ~21:5x): the four NAS
# copies' inode numbers all moved together 841874229/841874009/841873966/841874026 ->
# 844381460..844381463 while md5 AND btime were unchanged on every one of them, and no
# write happened (the qtree is not touched by any unit at that hour). The kernel
# synthesises ino per mount session, so grading ino as identity would FAIL clean files
# and, worse, teach the reader to re-record -- which is the one act that destroys the
# witness. So the roster's identity pair for a copy is (md5, btime): btime is carried by
# the server (it survived the renumber), and a NEW btime with old bytes is the
# temp-file-and-rename signature no md5 can see. ino is recorded LAST and graded as a
# NOTE (ino_renumbered=), never as moved=.
#
# Re-record with: bash check-exec-copies.sh --record
#
# Usage: bash check-exec-copies.sh [--record]
#   NAS=<dir>   root of the NAS copies (default /mnt/nas/host-ops/agent-chat)
#   LIVE=<dir>  root of the live legs    (default ~/agents/agent-chat)
#   EXEC_IDENTS_FILE=<path>  roster to read/write (default ./EXEC-IDENTS.tsv); a drill
#                            MUST set it, or --record writes scratch inodes into the repo
# Exit: 0 green, 1 FAIL, 2 cannot grade.

set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")" || exit 2
WALOPS=$PWD

NAS=${NAS:-/mnt/nas/host-ops/agent-chat}
LIVE=${LIVE:-$HOME/agents/agent-chat}
# The roster path is overridable ONLY so a drill can re-record without overwriting the
# production table. Measured 2026-11-15 the hard way: a scratch-root rehearsal ran
# --record against the fixed path and wrote five scratch inodes into the repo roster --
# i.e. the drill destroyed the very witness it was rehearsing. A --record is the one
# destructive act in this gate, so it must be directed at a file the caller names.
IDENTS_FILE=${EXEC_IDENTS_FILE:-$WALOPS/EXEC-IDENTS.tsv}

# The roster of paths: name:root. Adding a copy here is the deliberate act; a file
# present under either root but absent from this list is invisible to the gate, so
# enumerate-exec-set.sh stays the discoverer and this list is the committed answer.
COPIES="
check-grade-timer.sh:nas
wal-keeper.py:nas
wal-unlink-watch.py:nas
wal-unlink-watch-verdict.py:nas
wal-checkpoint.sh:live
"
root_of() { case "$1" in nas) printf '%s' "$NAS" ;; live) printf '%s' "$LIVE" ;; *) printf '%s' "" ;; esac; }

ident_line() { # name root md5 size btime ino   (ino last: on CIFS it is a coordinate)
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$5" "$6"
}
live_line() { # name root -> ident_line of the live file, or "missing" fields
  local n=$1 r d f i b m s
  r=$(root_of "$2"); f="$r/$n"
  if [ ! -f "$f" ]; then ident_line "$n" "$2" missing missing missing missing; return; fi
  m=$(md5sum "$f" 2>/dev/null | awk '{print $1}'); m=${m:-'?'}
  s=$(stat -c %s "$f" 2>/dev/null || echo '?')
  b=$(stat -c %w "$f" 2>/dev/null | tr ' ' 'T'); b=${b:--}
  i=$(stat -c %i "$f" 2>/dev/null || echo '?')
  ident_line "$n" "$2" "$m" "$s" "$b" "$i"
}

if [ "${1:-}" = "--record" ]; then
  : > "$IDENTS_FILE.new" || exit 2
  for c in $COPIES; do live_line "${c%%:*}" "${c##*:}" >> "$IDENTS_FILE.new"; done
  mv -f "$IDENTS_FILE.new" "$IDENTS_FILE" || { echo "FAIL: cannot write $IDENTS_FILE"; exit 2; }
  echo "recorded: $(wc -l < "$IDENTS_FILE") copy rows -> $IDENTS_FILE"
  exit 0
fi

rc=0
TMP=$(mktemp) || exit 2

# --- load the recorded rows ---------------------------------------------------
declare -A R_ROOT=() R_MD5=() R_INO=() R_BT=()
if [ -f "$IDENTS_FILE" ]; then
  while IFS=$'\t' read -r n r m s b i; do
    [ -n "${n:-}" ] || continue
    R_ROOT[$n]=$r; R_MD5[$n]=$m; R_BT[$n]=${b:-}; R_INO[$n]=${i:-}
  done < "$IDENTS_FILE"
fi

drift=0; moved=0; renum=0; copies=0
for c in $COPIES; do
  name=${c%%:*}; root=${c##*:}; copies=$((copies+1))
  path="$(root_of "$root")/$name"
  # The repo file is the reference build. check-manifest.sh pins it; this gate only
  # reads it, so a drifted repo file is that gate's FAIL to make, not a silent pass here.
  repo="$WALOPS/$name"
  if [ ! -f "$repo" ]; then
    echo "FAIL $name: no repo file $repo -- cannot grade a copy against nothing" >> "$TMP"; rc=2; continue
  fi
  want=$(md5sum "$repo" | awk '{print $1}')
  if [ ! -f "$path" ]; then
    echo "FAIL copy/$name: MISSING $path -- a roster row for a published path that is gone; repair: cp -p $repo $path (run as the owner of the live leg, never through a unit)" >> "$TMP"
    rc=1; continue
  fi
  got=$(md5sum "$path" | awk '{print $1}')
  li=$(stat -c %i "$path"); lb=$(stat -c %w "$path" 2>/dev/null | tr ' ' 'T'); lb=${lb:--}
  if [ "$got" != "$want" ]; then
    drift=$((drift+1))
    echo "FAIL bytes/$name: DRIFT copy $got != repo $want at $path -- the published bytes are not this tree's bytes; diff before copying anything" >> "$TMP"
    rc=1
  elif [ -z "${R_INO[$name]:-}" ]; then
    echo "FAIL copy/$name: no row in EXEC-IDENTS.tsv -- an unrecorded copy is a claim nothing can refute; record it: bash check-exec-copies.sh --record" >> "$TMP"
    rc=1
  elif [ "${R_ROOT[$name]}" != "$root" ] || [ "${R_MD5[$name]}" != "$got" ] \
       || [ "${R_BT[$name]}" != "$lb" ]; then
    moved=$((moved+1))
    echo "FAIL identity/$name: RECORDED STATE REPLACED at $path -- root ${R_ROOT[$name]}->$root md5 ${R_MD5[$name]}->$got btime ${R_BT[$name]}->$lb (ino ${R_INO[$name]}->$li). A new birth time with the same bytes is the atomic-write signature (temp + rename), which no md5 can see. If the write was intended, re-record and name the writer in the commit: bash check-exec-copies.sh --record" >> "$TMP"
    rc=1
  elif [ "${R_INO[$name]}" != "$li" ]; then
    renum=$((renum+1))
    echo "NOTE coordinate/$name: ino ${R_INO[$name]} -> $li with btime and md5 unchanged (nlink=$(stat -c %h "$path")) -- on CIFS an inode NUMBER is a mount-session coordinate the kernel synthesises, so this is not evidence of a write. Counted as ino_renumbered=, never as moved=." >> "$TMP"
  else
    echo "OK   $name: $root $path  md5=$got ino=$li nlink=$(stat -c %h "$path")" >> "$TMP"
  fi
done

# A row for a copy no longer in COPIES grades nothing; drop it deliberately.
for n in "${!R_INO[@]}"; do
  found=0
  for c in $COPIES; do [ "${c%%:*}" = "$n" ] && found=1; done
  if [ "$found" -eq 0 ]; then
    echo "FAIL stale row/$n: recorded but no longer in COPIES -- a deleted copy keeps a row that grades nothing: bash check-exec-copies.sh --record" >> "$TMP"
    rc=1
  fi
done

if [ "$renum" -gt 0 ] && [ "$renum" -eq "$copies" ]; then
  echo "NOTE: all $copies inode numbers moved together with btime fixed -- the measured CIFS renumber signature (2026-11-15 21:5x: inos 841874xxx -> 8443814xx on the four NAS copies, md5 and btime untouched). Nothing was written; do not re-record for this, and do not read a group-wide ino move as $copies writes." >> "$TMP"
fi

ok_n=$(grep -c '^OK ' "$TMP")
fail_n=$(grep -c '^FAIL' "$TMP")
cat "$TMP"

# copies_idents is one digest over the (name, ino, btime) triplets the gate just
# graded, so a room post can carry the identity claim in one field the way docs_md5
# carries the content claim. It moves on a re-record, not on a byte edit.
copies_idents=none
if [ -f "$IDENTS_FILE" ]; then
  copies_idents=$(grep -v '^\s*$' "$IDENTS_FILE" 2>/dev/null \
    | awk -F'\t' '{print $1"\t"$5}' | sha256sum | cut -c1-8)
fi
sha=$(git rev-parse --short HEAD 2>/dev/null || echo no-git)
if ! DIRTY_RAW=$(git status --porcelain --untracked-files=no 2>/dev/null); then
  dirty=unknown
elif [ -n "$DIRTY_RAW" ]; then dirty=1
else dirty=0
fi
unset DIRTY_RAW
echo "exec-copies gate: head=$sha dirty=$dirty copies=$copies ok=$ok_n fail=$fail_n drift=$drift moved=$moved ino_renumbered=$renum copies_idents=${copies_idents:-none} nas=$NAS live=$LIVE rc=$rc"
rm -f "$TMP"
exit $rc
