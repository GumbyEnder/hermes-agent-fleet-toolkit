#!/usr/bin/env bash
# test-mtime-record-arm.sh -- arms for the RECORD (mtime) arm in check-wal-doc-links.sh.
#
# THE DEFECT THIS GRADES. check-wal-doc-links.sh records four cells per doc in
# DOC-IDENTS.tsv: name, ino, btime, mtime. Through 9698889 it graded only the first
# three; the mtime cell was emitted, written and published but took part in nothing.
# Measured (frodo, 2026-11-16): one inode, btime identical to the nanosecond, bytes
# identical, mtime moved -- an in-place rewrite of identical bytes (r+b/write,
# dd conv=notrunc, a restore that keeps mtime out of it). md5 green, -ef green, ino
# green, btime green: the write left no trace anywhere while the column read as
# coverage. Same defect class as the unrecorded inode: a witness you can choose not
# to record is not a witness.
#
# SIX ARMS, and N1-N3 must be RED on the pre-fix gate or they certify nothing:
#   N1  in-place same-byte rewrite         -> green before, WRITTEN SINCE after.
#       The arm also asserts docs_md5, bytes, ino and btime are UNCHANGED, or it
#       proves nothing about what the mtime cell is for.
#   N2  backdated mtime inside the window  -> green before, MTIME MOVED BACKWARDS
#       after, and NOT the restored-time message. That non-overlap is the assertion:
#       two records, two questions. Run only on a row whose recorded mtime is well
#       after its btime (wal-forensics.md is the real shape: btime 06:47:59 vs
#       recorded 22:27:27). A fresh copy has a half-millisecond window and the arm
#       would be false, so arm N2 manufactures the wide window the honest way -- a
#       forward write then a re-record -- exactly as the canonical row got it.
#   N3  re-record                          -> green, and the DOC-IDENTS.tsv diff is
#       exactly one changed line with idents= moved.
#   N4/N5  two cells, two UTC OFFSETS      -> the record arm must follow the INSTANT.
#       N4 (record earlier in time, wall clock later) must read WRITTEN SINCE where a
#       string compare reads BACKWARDS; N5 is frodo's measured fall-back pair
#       (live 2026-11-01T01:30-0400 vs record 2026-11-01T01:15-0500) where a string
#       compare reads FORWARD over a 2700 s backdate and the arm printed NOTHING. Each
#       offset arm asserts the string compare disagrees on its own fixture, so neither
#       can pass against a gate that still compares strings.
#   N6  record cell that will not parse    -> DIRECTION UNMEASURED and rc=1, never a skip.
#
# THE DRILL NEVER TOUCHES THE CANONICAL FARM. Each arm builds its own tree under
# $TMPDIR, copies the docs with plain `cp` (so btime==mtime on the copy, not a
# preserved-time copy), points a FAKE $HOME at it through hardlinks so the -ef arm is
# genuinely satisfied, and makes it a git repo so head=/dirty= and the N3 diff are
# real. The gate self-roots at `readlink -f $0`, so the copy records its own
# DOC-IDENTS.tsv -- measured 2026-11-15: the copy recorded c10a83a3 while the repo's
# roster stayed 6f4791ae.
#
#   bash test-mtime-record-arm.sh [path/to/check-wal-doc-links.sh]
# Default is this script's sibling. Pass a `git show <sha>:...` extract to run the
# arms against a pre-fix gate (they must be RED there).
set -u

HERE=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
GATE=${1:-$HERE/check-wal-doc-links.sh}
[ -f "$GATE" ] || { echo "FAIL: no gate at $GATE"; exit 2; }
GATE=$(readlink -f "$GATE")
DOCS=$(sed -n 's/^DOCS="\(.*\)"$/\1/p' "$GATE" | head -1)
PROFILES=$(sed -n 's/^PROFILES="\(.*\)"$/\1/p' "$GATE" | head -1)
[ -n "$DOCS" ] || { echo "FAIL: cannot read DOCS from $GATE"; exit 2; }
[ -n "$PROFILES" ] || { echo "FAIL: cannot read PROFILES from $GATE"; exit 2; }

WORK=$(mktemp -d "${TMPDIR:-/tmp}/mtime-record-arm.XXXXXX") || exit 2
trap 'rm -rf "$WORK"' EXIT

pass=0
failed=0
P() { echo "PASS  $*"; pass=$((pass+1)); }
F() { echo "FAIL  $*"; failed=$((failed+1)); }

# fresh drill tree for one arm: $1 = arm dir
build() {
  local h=$1
  mkdir -p "$h/wal-ops" "$h/home"
  cp "$GATE" "$h/wal-ops/check-wal-doc-links.sh"
  local d p
  for d in $DOCS; do
    [ -f "$HERE/$d" ] || { echo "FAIL: source doc missing: $HERE/$d"; return 2; }
    cp "$HERE/$d" "$h/wal-ops/$d"
  done
  cp "$HERE/DOC-IDENTS.tsv" "$h/wal-ops/DOC-IDENTS.tsv"
  for p in $PROFILES; do
    mkdir -p "$h/home/.hermes/profiles/$p/skills/agent-chat-bus/references"
    for d in $DOCS; do
      ln -f "$h/wal-ops/$d" "$h/home/.hermes/profiles/$p/skills/agent-chat-bus/references/$d" || return 2
    done
  done
  git -C "$h/wal-ops" init -q || return 2
  git -C "$h/wal-ops" add -A || return 2
  git -C "$h/wal-ops" -c user.email=drill@local -c user.name=drill commit -qm drill-baseline || return 2
  return 0
}

# record the drill's own identities and commit, so a green run is a CLEAN tree
record() {
  local h=$1
  HOME="$h/home" bash "$h/wal-ops/check-wal-doc-links.sh" --record-idents >/dev/null 2>&1 || return 2
  git -C "$h/wal-ops" -c user.email=drill@local -c user.name=drill commit -qam record || return 2
  return 0
}

# run the drill gate; sets G_RC, G_OUT, G_FAILS, G_VERDICT
rgate() {
  local h=$1
  G_OUT=$(HOME="$h/home" bash "$h/wal-ops/check-wal-doc-links.sh" 2>&1)
  G_RC=$?
  G_VERDICT=$(printf '%s\n' "$G_OUT" | grep '^doc-links gate:' | tail -1)
  G_FAILS=$(printf '%s\n' "$G_OUT" | grep -c '^FAIL')
  return 0
}

field() { printf '%s\n' "$G_VERDICT" | sed -n "s/.*[[:space:]]$1=\([^[:space:]]*\).*/\1/p"; }

# stat prints "YYYY-MM-DD HH:MM:SS.fffffffff +HHMM" -- three fields. Render through the
# same shape the gate's stamp() uses, or the string compare here disagrees with it.
fmt() { awk '{ if (NF == 3) print $1 "T" $2 $3; else print "-" }'; }
stamps() {
  local i b m
  i=$(stat -c %i "$1")
  b=$(stat -c %w "$1" 2>/dev/null | fmt)
  m=$(stat -c %y "$1" 2>/dev/null | fmt)
  printf '%s|%s|%s\n' "$i" "$b" "$m"
}

rewrite_same_bytes() {
  python3 - "$1" <<'PY'
import os, sys
p = sys.argv[1]
fd = os.open(p, os.O_RDWR)
n = os.fstat(fd).st_size
data = os.read(fd, n)
assert len(data) == n
os.lseek(fd, 0, 0)
written = 0
while written < n:
    written += os.write(fd, data[written:])
os.fsync(fd)
os.close(fd)
PY
}

# ---- N1: in-place same-byte rewrite ------------------------------------------------
H=$WORK/n1
build "$H" && record "$H" || { F "N1 drill setup"; }
rgate "$H"
if [ "$G_RC" -ne 0 ] || [ "$G_FAILS" -ne 0 ]; then
  F "N1 baseline not green (rc=$G_RC fails=$G_FAILS)"
else
  P "N1 baseline green on a clean drill tree"
fi
md5_before=$(field docs_md5)
bytes_before=$(cd "$H/wal-ops" && sha256sum $DOCS | sha256sum | awk '{print $1}')
st_before=$(stamps "$H/wal-ops/wal-census.md")
ids_before=$(field idents)
rewrite_same_bytes "$H/wal-ops/wal-census.md" || F "N1 rewrite failed"
st_after=$(stamps "$H/wal-ops/wal-census.md")
bytes_after=$(cd "$H/wal-ops" && sha256sum $DOCS | sha256sum | awk '{print $1}')
rgate "$H"
md5_after=$(field docs_md5)
ino_b=$(printf '%s' "$st_before" | cut -d'|' -f1);  ino_a=$(printf '%s' "$st_after" | cut -d'|' -f1)
bt_b=$(printf '%s' "$st_before" | cut -d'|' -f2);   bt_a=$(printf '%s' "$st_after" | cut -d'|' -f2)
mt_b=$(printf '%s' "$st_before" | cut -d'|' -f3);   mt_a=$(printf '%s' "$st_after" | cut -d'|' -f3)
[ "$bytes_before" = "$bytes_after" ] && P "N1 bytes unchanged (sha256 over all docs)" \
  || F "N1 bytes moved -- the arm is not testing a same-byte write"
[ "$md5_before" = "$md5_after" ] && P "N1 docs_md5 unchanged ($md5_before)" \
  || F "N1 docs_md5 moved: $md5_before -> $md5_after"
[ "$ino_b" = "$ino_a" ] && P "N1 ino unchanged ($ino_a)" || F "N1 ino moved: $ino_b -> $ino_a"
[ "$bt_b" = "$bt_a" ] && P "N1 btime unchanged ($bt_a)" || F "N1 btime moved: $bt_b -> $bt_a"
[ "$mt_b" != "$mt_a" ] && P "N1 mtime moved ($mt_b -> $mt_a) -- the only field that does" \
  || F "N1 mtime did not move; nothing to detect"
case "$G_OUT" in
  *wal-census.md*"WRITTEN SINCE THE RECORD"*) P "N1 gate FAILs WRITTEN SINCE THE RECORD" ;;
  *) F "N1 gate did not report WRITTEN SINCE THE RECORD (rc=$G_RC fails=$G_FAILS) -- arm is RED"; printf '%s\n' "$G_OUT" | grep '^FAIL' | sed 's/^/      /' ;;
esac
case "$G_OUT" in
  *"MTIME MOVED BACKWARDS"*) F "N1 also reported MTIME MOVED BACKWARDS -- messages overlap" ;;
  *) P "N1 no backwards message (forward write only)" ;;
esac
[ "$G_RC" -ne 0 ] && P "N1 gate rc=$G_RC" || F "N1 gate rc=0 with a same-byte rewrite on a recorded row"

# ---- N2: backdated mtime with ino/btime unchanged ----------------------------------
H=$WORK/n2
build "$H" && record "$H" || { F "N2 drill setup"; }
# manufacture the wide window the honest way: forward write, then re-record.
# A fresh copy has btime==mtime to the microsecond, so a fixed backdate would land
# below btime and fire the restored-time arm instead -- that arm's question, not ours.
D2="$H/wal-ops/wal-forensics.md"
b_epoch=$(stat -c %W "$D2")
[ "$b_epoch" -gt 0 ] || F "N2 no btime on this filesystem -- the arm cannot run here"
touch -m -d "@$((b_epoch + 3600))" "$D2"
HOME="$H/home" bash "$H/wal-ops/check-wal-doc-links.sh" --record-idents >/dev/null 2>&1
git -C "$H/wal-ops" -c user.email=drill@local -c user.name=drill commit -qam "record wide window"
rgate "$H"
if [ "$G_RC" -ne 0 ]; then
  F "N2 wide-window baseline not green (rc=$G_RC fails=$G_FAILS)"
  printf '%s\n' "$G_OUT" | grep '^FAIL' | sed 's/^/      /'
else
  P "N2 wide-window baseline green (recorded mtime 1h after btime)"
fi
rec=$(stat -c %y "$D2" | fmt)
bt=$(stat -c %w "$D2" | fmt)
ino2_b=$(stat -c %i "$D2")
touch -m -d "@$((b_epoch + 1800))" "$D2"
rgate "$H"
ino2_a=$(stat -c %i "$D2")
bt2_a=$(stat -c %w "$D2" | fmt)
[ "$ino2_b" = "$ino2_a" ] && P "N2 ino unchanged" || F "N2 ino moved"
[ "$bt" = "$bt2_a" ] && P "N2 btime unchanged" || F "N2 btime moved"
case "$G_OUT" in
  *wal-forensics.md*"MTIME MOVED BACKWARDS"*) P "N2 gate FAILs MTIME MOVED BACKWARDS" ;;
  *) F "N2 gate did not report MTIME MOVED BACKWARDS (rc=$G_RC fails=$G_FAILS) -- arm is RED"; printf '%s\n' "$G_OUT" | grep '^FAIL' | sed 's/^/      /' ;;
esac
case "$G_OUT" in
  *"RESTORED-TIME COPY"*) F "N2 also reported RESTORED-TIME COPY -- the arms overlap; the backdate was not inside the window" ;;
  *) P "N2 no restored-time message (live mtime stayed after btime: $bt -> $(stat -c %y "$D2" | fmt))" ;;
esac
case "$G_OUT" in
  *"WRITTEN SINCE THE RECORD"*) F "N2 also reported WRITTEN SINCE -- direction collapsed to inequality" ;;
  *) P "N2 no written-since message (direction is graded, not difference)" ;;
esac

# ---- N3: re-record ----------------------------------------------------------------
H=$WORK/n3
build "$H" && record "$H" || { F "N3 drill setup"; }
rgate "$H"
ids_a=$(field idents)
rewrite_same_bytes "$H/wal-ops/wal-census.md" || F "N3 rewrite failed"
rgate "$H"
ids_b=$(field idents)
[ "$ids_a" != "$ids_b" ] && P "N3 idents= moved ($ids_a -> $ids_b) with nothing rewritten" \
  || F "N3 idents= did not move; the drill recorded nothing"
cp "$H/wal-ops/DOC-IDENTS.tsv" "$H/tsv.before"
HOME="$H/home" bash "$H/wal-ops/check-wal-doc-links.sh" --record-idents >/dev/null 2>&1
rgate "$H"
[ "$G_RC" -eq 0 ] && [ "$G_FAILS" -eq 0 ] && P "N3 gate green after re-record (rc=0 fails=0)" \
  || { F "N3 gate still red after re-record (rc=$G_RC fails=$G_FAILS)"; printf '%s\n' "$G_OUT" | grep '^FAIL' | sed 's/^/      /'; }
if diff -q "$H/tsv.before" "$H/wal-ops/DOC-IDENTS.tsv" >/dev/null; then
  F "N3 re-record wrote nothing"
else
  changed=$(diff -U0 "$H/tsv.before" "$H/wal-ops/DOC-IDENTS.tsv" | grep -c '^[-+][^-+]')
  [ "$changed" -eq 2 ] && P "N3 DOC-IDENTS.tsv diff is exactly one changed line" \
    || { F "N3 DOC-IDENTS.tsv diff is $changed changed lines, not one"; diff -U0 "$H/tsv.before" "$H/wal-ops/DOC-IDENTS.tsv" | sed 's/^/      /'; }
  diff "$H/tsv.before" "$H/wal-ops/DOC-IDENTS.tsv" | grep -q '^> wal-census.md' \
    && P "N3 the re-recorded row is wal-census.md" || F "N3 the re-recorded row is not the written doc"
fi
ids_c=$(field idents)
[ "$ids_b" = "$ids_c" ] && P "N3 idents= holds across the re-record ($ids_c) -- a record, not a rewrite" \
  || F "N3 idents= moved across the re-record: $ids_b -> $ids_c"

# ---- the OFFSET arms: two cells, two UTC offsets ------------------------------------
# A stamp is local time plus an offset, so a lexicographic compare orders WALL CLOCK.
# When the two cells are spoken in different offsets -- which is what a DST change
# produces, and what any roster written on the other side of the change carries -- the
# string compare and the truth disagree, in BOTH directions. N4 and N5 manufacture that
# disagreement in the roster cell, which is the cell the arm grades, and each asserts
# (a) the direction the truth has, (b) that the string compare disagrees, so the arm
# cannot pass against a gate that still compares strings.
set_row() {
  local h=$1 d=$2 i=$3 b=$4 m=$5
  awk -F'\t' -v OFS='\t' -v d="$d" -v i="$i" -v b="$b" -v m="$m" \
    '$1==d { print d, i, b, m; next } { print }' "$h/wal-ops/DOC-IDENTS.tsv" \
    > "$h/wal-ops/DOC-IDENTS.tsv.new" && mv "$h/wal-ops/DOC-IDENTS.tsv.new" "$h/wal-ops/DOC-IDENTS.tsv"
}
eps() { date -d "$1" +%s 2>/dev/null; }

# ---- N4: recorded 15 min EARLIER in time, spoken in +1100 (wall clock 16h later) ---
# A string compare reads this as BACKWARDS; the truth is WRITTEN SINCE. False FAIL --
# noisy and safe -- but wrong, and it is the same defect whose false-PASS half is N5.
# The offset here is just a legitimate zone name on either side of UTC; no clock is
# faked and the instant is real.
H=$WORK/n4
build "$H" && record "$H" || { F "N4 drill setup"; }
D4="$H/wal-ops/wal-census.md"
e4=$(date +%s)
touch -m -d "@$e4" "$D4"
ino4=$(stat -c %i "$D4"); bt4=$(stat -c %w "$D4" | fmt); lm4=$(stat -c %y "$D4" | fmt)
rmt4=$(TZ=Etc/GMT-11 date -d "@$((e4 - 900))" '+%FT%T.%N%z')
set_row "$H" wal-census.md "$ino4" "$bt4" "$rmt4"
rr4=$(eps "$rmt4"); rl4=$(eps "$lm4")
[ "$rl4" -gt "$rr4" ] && P "N4 truth: live is 15 min LATER than the record (epoch $rl4 > $rr4)" \
  || F "N4 the fixture is not the shape claimed: live $rl4 vs record $rr4"
if [[ "$lm4" > "$rmt4" ]]; then F "N4 the string compare does NOT disagree here -- the arm proves nothing"; else P "N4 the string compare inverts it (live '$lm4' sorts BELOW record '$rmt4') -- teeth"; fi
rgate "$H"
case "$G_OUT" in
  *wal-census.md*"WRITTEN SINCE THE RECORD"*) P "N4 gate FAILs WRITTEN SINCE THE RECORD (truth, not wall clock)" ;;
  *wal-census.md*"MTIME MOVED BACKWARDS"*) F "N4 gate said BACKWARDS -- the compare followed the offset, not the instant" ;;
  *) F "N4 gate printed neither direction (rc=$G_RC fails=$G_FAILS) -- the arm did not run"; printf '%s\n' "$G_OUT" | grep '^FAIL' | sed 's/^/      /' ;;
esac
case "$G_OUT" in
  *"RESTORED-TIME COPY"*) F "N4 also reported RESTORED-TIME COPY -- live mtime is now, after btime; arms overlap" ;;
  *) P "N4 no restored-time message (live mtime is after btime)" ;;
esac
[ "$G_RC" -ne 0 ] && P "N4 gate rc=$G_RC" || F "N4 gate rc=0 with a record cell that is 15 min behind the file"

# ---- N5: frodo's measured DST pair, both cells REAL ---------------------------------
# live 2026-11-01T01:30:00-0400 (epoch 1793511000) against a record cell
# 2026-11-01T01:15:00-0500 (epoch 1793513700): the record is 45 min LATER and the wall
# clock is EARLIER. `[[ $live > $record ]]` reads TRUE while the file was backdated 45
# minutes. That is the false PASS: the arm designed to see a clock set on a live inode
# printed nothing across fall-back. The restored-time message is EXPECTED here too --
# the drill's mtime is 15 days older than its btime -- so it is asserted, not excused.
H=$WORK/n5
build "$H" && record "$H" || { F "N5 drill setup"; }
D5="$H/wal-ops/wal-census.md"
e5=1793511000
touch -m -d "@$e5" "$D5"
ino5=$(stat -c %i "$D5"); bt5=$(stat -c %w "$D5" | fmt); lm5=$(stat -c %y "$D5" | fmt)
rmt5="2026-11-01T01:15:00.000000000-0500"
set_row "$H" wal-census.md "$ino5" "$bt5" "$rmt5"
rr5=$(eps "$rmt5"); rl5=$(eps "$lm5")
case "$lm5" in
  *-0400) P "N5 live cell rendered in the pre-transition offset ($lm5)" ;;
  *) F "N5 live cell did not render -0400 ($lm5) -- this host is not mid-fall-back and the fixture is not the measured pair" ;;
esac
[ $((rr5 - rl5)) -eq 2700 ] && P "N5 truth: the record is 2700 s LATER than live (epoch $rr5 vs $rl5)" \
  || F "N5 epoch gap is $((rr5 - rl5)) s, not 2700 -- not the measured pair"
if [[ "$lm5" > "$rmt5" ]]; then P "N5 the string compare reads FORWARD on a 45-minute backdate -- the false PASS"; else F "N5 the string compare does not invert this pair; check the fixture"; fi
rgate "$H"
case "$G_OUT" in
  *wal-census.md*"MTIME MOVED BACKWARDS"*) P "N5 gate FAILs MTIME MOVED BACKWARDS across the offset change" ;;
  *wal-census.md*"WRITTEN SINCE THE RECORD"*) F "N5 gate said WRITTEN SINCE -- direction followed wall clock" ;;
  *) F "N5 gate printed no direction (rc=$G_RC fails=$G_FAILS) -- FALSE PASS: the backdate is invisible"; printf '%s\n' "$G_OUT" | grep '^FAIL' | sed 's/^/      /' ;;
esac
case "$G_OUT" in
  *"RESTORED-TIME COPY"*) P "N5 restored-time also fires (mtime 15 days before btime) -- expected, different question" ;;
  *) P "N5 no restored-time message (btime survived the drill's backdate on this fs)" ;;
esac

# ---- N6: a record cell that will not parse -------------------------------------------
# `date -d` returning nothing must be UNMEASURED and RED, never a silent skip: a branch
# the tree does not enter is not an arm.
H=$WORK/n6
build "$H" && record "$H" || { F "N6 drill setup"; }
D6="$H/wal-ops/wal-census.md"
ino6=$(stat -c %i "$D6"); bt6=$(stat -c %w "$D6" | fmt)
set_row "$H" wal-census.md "$ino6" "$bt6" "not-a-time"
rgate "$H"
case "$G_OUT" in
  *wal-census.md*"DIRECTION UNMEASURED"*) P "N6 gate FAILs DIRECTION UNMEASURED on an unparseable record cell" ;;
  *) F "N6 gate did not report DIRECTION UNMEASURED (rc=$G_RC fails=$G_FAILS) -- an unreadable cell passed"; printf '%s\n' "$G_OUT" | grep '^FAIL' | sed 's/^/      /' ;;
esac
[ "$G_RC" -ne 0 ] && P "N6 gate rc=$G_RC" || F "N6 gate rc=0 with an unreadable record cell"

echo
echo "test-mtime-record-arm: PASS=$pass FAIL=$failed gate=$GATE"
if [ "$failed" -eq 0 ]; then echo "RESULT PASS"; exit 0; fi
echo "RESULT FAIL"
exit 1
