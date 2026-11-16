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
# A RENUMBER IS A MOUNT SESSION, SO IT IS GROUPED BY ROOT, NOT BY COPY SET.
# Second event, measured by buzzy 2026-11-15 ~22:5x and reproduced here: the same four
# NAS copies went 844381460..844381463 -> 845153283..845153286 -- ONE group, ONE
# direction, md5 and btime unchanged on all four, copies_idents=bef9ef35 both before and
# after -- while the live ext4 leg stayed at ino 15344036. Two facts fall out:
#   (a) the synthesis is per MOUNT SESSION, not per file. Four-of-four moving together
#       under one root is a remount; the opt-in COPIES set spans two filesystems, so
#       "renum == copies" was the wrong denominator -- this gate now groups by root and
#       prints ONE `NOTE ino_group: root=nas n=4 <first>..<last>` line for a whole-root
#       move instead of one NOTE per file.
#   (b) a SUBSET within one root is the shape that carries information (a relink, a
#       partially restored slot, a copy landing on a different mount), so per-row NOTEs
#       are kept for exactly that case and are never collapsed.
#
# RE-COORDINATING A DEAD SESSION WITHOUT DESTROYING THE WITNESS: --record-coords.
# A roster left holding a dead mount session's ino numbers prints NOTEs forever on clean
# files, and the only repair used to be --record -- the act that overwrites the very
# (md5, btime) pair the gate exists to compare against. --record-coords rewrites the INO
# COLUMN ONLY and refuses if anything else moved:
#   * refuses if the roster is missing, a rostered copy is gone, or any recorded
#     (root, md5, size, btime) differs from disk -- that is a real identity event and
#     belongs to --record, with the writer named in the commit;
#   * refuses if the renumber is a SUBSET inside one root -- the interesting shape is not
#     a coordinate refresh, and a blind refresh would erase the signal;
#   * asserts, before and after the write, that columns 1-5 are byte-identical, and
#     prints rows=/ino_changed=/ident_unchanged= plus every old->new pair.
# It is non-destructive in the only sense that matters: md5 and btime -- the identity
# pair -- are never rewritten, only verified.
#
# Re-record everything with: bash check-exec-copies.sh --record
# Refresh dead coordinates with: bash check-exec-copies.sh --record-coords
#
# Usage: bash check-exec-copies.sh [--record|--record-coords]
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

# --- load the recorded rows (both subcommands need them) ----------------------
declare -A R_ROOT=() R_MD5=() R_BSIZE=() R_BT=() R_INO=()
ROSTER_LINES=()   # roster file lines, in file order, so a rewrite preserves layout
if [ -f "$IDENTS_FILE" ]; then
  while IFS=$'\t' read -r n r m s b i; do
    [ -n "${n:-}" ] || continue
    R_ROOT[$n]=$r; R_MD5[$n]=$m; R_BSIZE[$n]=${s:-}; R_BT[$n]=${b:-}; R_INO[$n]=${i:-}
    ROSTER_LINES+=("$n")
  done < "$IDENTS_FILE"
fi

if [ "${1:-}" = "--record" ]; then
  : > "$IDENTS_FILE.new" || exit 2
  for c in $COPIES; do live_line "${c%%:*}" "${c##*:}" >> "$IDENTS_FILE.new"; done
  mv -f "$IDENTS_FILE.new" "$IDENTS_FILE" || { echo "FAIL: cannot write $IDENTS_FILE"; exit 2; }
  echo "recorded: $(wc -l < "$IDENTS_FILE") copy rows -> $IDENTS_FILE"
  exit 0
fi

if [ "${1:-}" = "--record-coords" ]; then
  if [ ! -f "$IDENTS_FILE" ] || [ "${#R_INO[@]}" -eq 0 ]; then
    echo "record-coords: no roster at $IDENTS_FILE -- nothing to re-coordinate; use --record on a first pass" >&2
    exit 2
  fi
  rc=0
  # measure disk once, and refuse on anything but an ino move
  declare -A N_ROOT=() N_MD5=() N_BSIZE=() N_BT=() N_INO=()
  for c in $COPIES; do
    name=${c%%:*}; root=${c##*:}; path="$(root_of "$root")/$name"
    if [ -z "${R_INO[$name]:-}" ]; then
      echo "FAIL record-coords/$name: no roster row -- an unrecorded copy needs --record, not a coordinate refresh" >&2; rc=2; continue
    fi
    if [ ! -f "$path" ]; then
      echo "FAIL record-coords/$name: MISSING $path -- a coordinate refresh cannot cover a copy that is gone" >&2; rc=2; continue
    fi
    IFS=$'\t' read -r _ _ m s b i < <(live_line "$name" "$root")
    N_ROOT[$name]=$root; N_MD5[$name]=$m; N_BSIZE[$name]=$s; N_BT[$name]=$b; N_INO[$name]=$i
    if [ "${R_ROOT[$name]}" != "$root" ] || [ "${R_MD5[$name]}" != "$m" ] \
       || [ "${R_BSIZE[$name]}" != "$s" ] || [ "${R_BT[$name]}" != "$b" ]; then
      printf 'FAIL record-coords/%s: IDENTITY MOVED -- root %s->%s md5 %s->%s size %s->%s btime %s->%s. A coordinate refresh must not touch the identity pair; this is a --record event with the writer named in the commit.\n' \
        "$name" "${R_ROOT[$name]}" "$root" "${R_MD5[$name]}" "$m" "${R_BSIZE[$name]}" "$s" "${R_BT[$name]}" "$b" >&2
      rc=2
    fi
  done
  # a whole-root move is a mount session; a subset inside a root is the signal, not noise
  declare -A ROOT_TOTAL=() ROOT_MOVED=()
  for c in $COPIES; do
    n=${c%%:*}; r=${c##*:}
    ROOT_TOTAL[$r]=$(( ${ROOT_TOTAL[$r]:-0} + 1 ))
    [ -n "${N_INO[$n]:-}" ] && [ "${R_INO[$n]}" != "${N_INO[$n]}" ] && ROOT_MOVED[$r]=$(( ${ROOT_MOVED[$r]:-0} + 1 ))
  done
  for r in "${!ROOT_MOVED[@]}"; do
    if [ "${ROOT_MOVED[$r]}" -ne "${ROOT_TOTAL[$r]}" ]; then
      echo "FAIL record-coords: root=$r moved ${ROOT_MOVED[$r]}/${ROOT_TOTAL[$r]} -- a SUBSET renumber is the shape that carries information (relink, partial restore, one path on another mount), so a blind refresh would erase it. Grade it; if it was intended, --record and name the writer." >&2
      rc=2
    fi
  done
  if [ "$rc" -ne 0 ]; then
    echo "record-coords: REFUSED, roster untouched: $IDENTS_FILE" >&2
    exit 2
  fi
  # rewrite ino column only, preserving every other byte and the file's row order
  : > "$IDENTS_FILE.new" || exit 2
  changed=0
  for n in "${ROSTER_LINES[@]}"; do
    old_ino=${R_INO[$n]}
    # rows not in COPIES keep their line verbatim; stale-row grading is the gate's job
    if [ -n "${N_INO[$n]:-}" ]; then
      new_ino=${N_INO[$n]}
      printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$n" "${R_ROOT[$n]}" "${R_MD5[$n]}" "${R_BSIZE[$n]}" "${R_BT[$n]}" "$new_ino" >> "$IDENTS_FILE.new"
      if [ "$old_ino" != "$new_ino" ]; then
        changed=$((changed+1)); printf '  ino %s: %s -> %s\n' "$n" "$old_ino" "$new_ino"
      fi
    else
      grep -P "^$(printf '%s' "$n" | sed 's/[.[\*^$]/\\&/g')\t" "$IDENTS_FILE" >> "$IDENTS_FILE.new"
    fi
  done
  # pre/post assertion: columns 1-5 byte-identical, row count identical
  before=$(awk -F'\t' '{print $1"\t"$2"\t"$3"\t"$4"\t"$5}' "$IDENTS_FILE")
  after=$(awk -F'\t' '{print $1"\t"$2"\t"$3"\t"$4"\t"$5}' "$IDENTS_FILE.new")
  rows_in=$(grep -c . "$IDENTS_FILE"); rows_out=$(grep -c . "$IDENTS_FILE.new")
  if [ "$before" != "$after" ] || [ "$rows_in" != "$rows_out" ]; then
    rm -f "$IDENTS_FILE.new"
    echo "FAIL record-coords: the rewrite changed more than the ino column (rows $rows_in->$rows_out) -- roster untouched" >&2
    exit 2
  fi
  mv -f "$IDENTS_FILE.new" "$IDENTS_FILE" || { echo "FAIL: cannot write $IDENTS_FILE" >&2; exit 2; }
  echo "record-coords: rows=$rows_out ino_changed=$changed ident_unchanged=$rows_out -> $IDENTS_FILE"
  exit 0
fi

rc=0
TMP=$(mktemp) || exit 2

drift=0; moved=0; renum=0; copies=0
declare -A NOTE_LINE=() RENUM_INOS=() ROOT_COPIES=() ROOT_RENUM=() GROUP_FIELD=()
for c in $COPIES; do
  name=${c%%:*}; root=${c##*:}; copies=$((copies+1))
  ROOT_COPIES[$root]=$(( ${ROOT_COPIES[$root]:-0} + 1 ))
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
    ROOT_RENUM[$root]=$(( ${ROOT_RENUM[$root]:-0} + 1 ))
    RENUM_INOS[$root]="${RENUM_INOS[$root]:-} $li"
    NOTE_LINE[$name]="NOTE coordinate/$name: ino ${R_INO[$name]} -> $li with btime and md5 unchanged (nlink=$(stat -c %h "$path")) -- on CIFS an inode NUMBER is a mount-session coordinate the kernel synthesises, so this is not evidence of a write. Counted as ino_renumbered=, never as moved=."
  else
    echo "OK   $name: $root $path  md5=$got ino=$li nlink=$(stat -c %h "$path")" >> "$TMP"
  fi
done

# Emit the renumber evidence GROUPED BY ROOT. A whole root moving together is one mount
# session and gets one line; a subset inside a root is the informative shape and keeps
# its per-row evidence verbatim.
groups=()
for r in "${!ROOT_RENUM[@]}"; do
  if [ "${ROOT_RENUM[$r]}" -eq "${ROOT_COPIES[$r]}" ]; then
    set -- ${RENUM_INOS[$r]}
    first=$1; last=$1
    for v in "$@"; do [ "$v" -lt "$first" ] && first=$v; [ "$v" -gt "$last" ] && last=$v; done
    echo "NOTE ino_group: root=$r n=${ROOT_RENUM[$r]}/${ROOT_COPIES[$r]} $first..$last -- every copy under this root renumbered together with md5 and btime fixed on each: ONE mount session, $first..$last, not ${ROOT_RENUM[$r]} writes. Do not re-record for this; refresh the stale coordinates with: bash check-exec-copies.sh --record-coords" >> "$TMP"
    # the per-row NOTEs for this root are collapsed into the group line above
    for c in $COPIES; do n=${c%%:*}; [ "${c##*:}" = "$r" ] && unset 'NOTE_LINE[$n]'; done
    groups+=("$r:$first..$last")
  else
    echo "NOTE ino_partial: root=$r n=${ROOT_RENUM[$r]}/${ROOT_COPIES[$r]} -- a SUBSET of one root moved; per-row evidence follows, and this is the shape that carries information (relink, partial restore, a single path landing on another mount), not a mount session." >> "$TMP"
    groups+=("$r:partial")
  fi
done
for n in "${!NOTE_LINE[@]}"; do echo "${NOTE_LINE[$n]}" >> "$TMP"; done

# A row for a copy no longer in COPIES grades nothing; drop it deliberately.
for n in "${!R_INO[@]}"; do
  found=0
  for c in $COPIES; do [ "${c%%:*}" = "$n" ] && found=1; done
  if [ "$found" -eq 0 ]; then
    echo "FAIL stale row/$n: recorded but no longer in COPIES -- a deleted copy keeps a row that grades nothing: bash check-exec-copies.sh --record" >> "$TMP"
    rc=1
  fi
done

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
ino_group=none
[ "${#groups[@]}" -gt 0 ] && ino_group=$(IFS=,; echo "${groups[*]}")
# head= carries THREE states, not two -- the same branch as run-gate-logged.sh:63-68,
# check-manifest.sh and check-wal-doc-links.sh. This gate carried a third copy of the old
# two-ending one-liner (found by grep on 2026-11-16 while fixing the two frodo named: the
# vocabulary rule is one field, one vocabulary, so a third instance is not out of scope --
# it is the counter-example that would have kept `no-git` alive in gate logs).
if ! git --version >/dev/null 2>&1; then
  sha=unknown
elif ! git rev-parse --git-dir >/dev/null 2>&1; then
  sha=none
elif ! sha=$(git rev-parse --short HEAD 2>/dev/null); then
  sha=unknown
fi
if ! DIRTY_RAW=$(git status --porcelain --untracked-files=no 2>/dev/null); then
  dirty=unknown
elif [ -n "$DIRTY_RAW" ]; then dirty=1
else dirty=0
fi
unset DIRTY_RAW
echo "exec-copies gate: head=$sha dirty=$dirty copies=$copies ok=$ok_n fail=$fail_n drift=$drift moved=$moved ino_renumbered=$renum ino_group=$ino_group copies_idents=${copies_idents:-none} nas=$NAS live=$LIVE rc=$rc"
rm -f "$TMP"
exit $rc
