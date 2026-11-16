#!/usr/bin/env bash
# test-record-coords.sh -- the arms of --record-coords, on scratch rosters.
#
# The point of --record-coords is that it can only ever move a COORDINATE. So every arm
# here is a refusal or a proof of non-damage, run against a copy of the production
# roster -- never the roster itself. Four arms:
#   A  clean drift-only renumber (fresh session) -> rewrites ino, columns 1-5 identical
#   B  gate against the refreshed roster    -> ok=5 ino_renumbered=0, no NOTE lines
#   C  identity moved (one md5 forged)      -> REFUSED, roster byte-identical after
#   D  subset renumber inside one root      -> REFUSED, roster byte-identical after
#
# Usage: bash test-record-coords.sh [roster]   (default ./EXEC-IDENTS.tsv)
# Exit 0 all arms held, 1 an arm failed.

set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")" || exit 2
SRC=${1:-$PWD/EXEC-IDENTS.tsv}
S=$(mktemp -d) || exit 2
trap 'rm -rf "$S"' EXIT
bad=0
say() { printf '%s\n' "$*"; }
fail() { printf 'FAIL arm/%s: %s\n' "$1" "$2"; bad=$((bad+1)); }

[ -f "$SRC" ] || { say "no roster: $SRC"; exit 2; }
cp "$SRC" "$S/r.tsv"

# --- A: coordinate refresh on a stale roster ---------------------------------
out=$(EXEC_IDENTS_FILE=$S/r.tsv bash check-exec-copies.sh --record-coords); rc=$?
say "A rc=$rc $out"
[ "$rc" -eq 0 ] || fail A "rc=$rc, expected 0"
grep -q 'ident_unchanged=5' <<<"$out" || fail A "no ident_unchanged=5 in $out"
# columns 1-5 must be byte-identical to the source roster
if ! diff <(awk -F'\t' -v OFS='\t' '{print $1,$2,$3,$4,$5}' "$SRC") \
          <(awk -F'\t' -v OFS='\t' '{print $1,$2,$3,$4,$5}' "$S/r.tsv") >"$S/dA" 2>&1; then
  fail A "identity columns moved: $(cat "$S/dA")"
fi
# and the ino column must now equal the live coordinates
n_stale=$(paste <(awk -F'\t' '{print $6}' "$SRC") <(awk -F'\t' '{print $6}' "$S/r.tsv") | awk '$1!=$2' | wc -l)
say "A ino_rows_changed=$n_stale"
[ "$n_stale" -gt 0 ] || fail A "no ino row changed -- the arm graded nothing"

# --- B: the refreshed roster grades clean with no NOTE noise -----------------
outb=$(EXEC_IDENTS_FILE=$S/r.tsv bash check-exec-copies.sh); rcb=$?
say "B rc=$rcb $(tail -1 <<<"$outb")"
[ "$rcb" -eq 0 ] || fail B "rc=$rcb, expected 0"
grep -q 'ino_renumbered=0' <<<"$outb" || fail B "ino_renumbered != 0: $(tail -1 <<<"$outb")"
grep -q 'ok=5' <<<"$outb" || fail B "ok != 5"
if grep -q '^NOTE' <<<"$outb"; then fail B "clean roster still prints NOTE lines: $(grep '^NOTE' <<<"$outb")"; fi

# --- C: a moved identity must be refused, and the roster left alone ----------
cp "$SRC" "$S/c.tsv"
first=$(awk -F'\t' 'NR==1{print $1}' "$S/c.tsv")
awk -F'\t' -v OFS='\t' -v n="$first" '$1==n{$3="deadbeefdeadbeefdeadbeefdeadbeef"}1' "$S/c.tsv" > "$S/c.new" && mv "$S/c.new" "$S/c.tsv"
before=$(md5sum < "$S/c.tsv")
outc=$(EXEC_IDENTS_FILE=$S/c.tsv bash check-exec-copies.sh --record-coords 2>&1); rcc=$?
say "C rc=$rcc $(tail -1 <<<"$outc")"
[ "$rcc" -eq 2 ] || fail C "rc=$rcc, expected 2 (refusal)"
grep -q 'IDENTITY MOVED' <<<"$outc" || fail C "refusal did not name IDENTITY MOVED"
[ "$before" = "$(md5sum < "$S/c.tsv")" ] || fail C "REFUSED but the roster changed"

# --- D: a subset inside one root must be refused -----------------------------
cp "$SRC" "$S/d.tsv"
live_ino=$(stat -c %i "${NAS:-/mnt/nas/host-ops/agent-chat}/check-grade-timer.sh" 2>/dev/null || echo 0)
awk -F'\t' -v OFS='\t' -v n='check-grade-timer.sh' -v i="$live_ino" '$1==n{$6=i}1' "$S/d.tsv" > "$S/d.new" && mv "$S/d.new" "$S/d.tsv"
before=$(md5sum < "$S/d.tsv")
outd=$(EXEC_IDENTS_FILE=$S/d.tsv bash check-exec-copies.sh --record-coords 2>&1); rcd=$?
say "D rc=$rcd $(grep -E 'SUBSET|REFUSED' <<<"$outd" | head -1)"
[ "$rcd" -eq 2 ] || fail D "rc=$rcd, expected 2 (refusal)"
grep -q 'SUBSET' <<<"$outd" || fail D "refusal did not name SUBSET"
[ "$before" = "$(md5sum < "$S/d.tsv")" ] || fail D "REFUSED but the roster changed"

if [ "$bad" -eq 0 ]; then say "record-coords arms: A=ok B=ok C=refused D=refused fail=0 rc=0"; exit 0; fi
say "record-coords arms: fail=$bad rc=1"; exit 1
