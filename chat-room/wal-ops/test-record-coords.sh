#!/usr/bin/env bash
# test-record-coords.sh -- the arms of --record-coords, on scratch rosters.
#
# The point of --record-coords is that it can only ever move a COORDINATE. So every arm
# here is a refusal or a proof of non-damage, run against a copy of the production
# roster -- never the roster itself.
#
# The arms SYNTHESISE their own staleness instead of inheriting it from the source
# roster. Measured the hard way: the first version assumed the live roster was stale, ran
# green, was refreshed by the very feature under test, and then failed three of four arms
# on the next run because there was nothing left to re-coordinate. A test whose fixture
# is the production file's accident is a test that expires.
#
#   A  drift-only renumber (NAS inos forced stale) -> rewrites ino, columns 1-5 identical
#   B  gate against the refreshed roster    -> ok=5 ino_renumbered=0, no NOTE lines
#   C  identity moved (one md5 forged)      -> REFUSED, roster byte-identical after
#   D  subset renumber inside one root      -> REFUSED, roster byte-identical after
#   E  copies_idents unchanged by A         -> the digest is over (name, btime), not ino
#
# Usage: bash test-record-coords.sh [roster]   (default ./EXEC-IDENTS.tsv)
# Exit 0 all arms held, 1 an arm failed.

set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")" || exit 2
SRC=${1:-$PWD/EXEC-IDENTS.tsv}
NAS=${NAS:-/mnt/nas/host-ops/agent-chat}
S=$(mktemp -d) || exit 2
trap 'rm -rf "$S"' EXIT
bad=0
say() { printf '%s\n' "$*"; }
fail() { printf 'FAIL arm/%s: %s\n' "$1" "$2"; bad=$((bad+1)); }
ident_cols() { awk -F'\t' -v OFS='\t' '{print $1,$2,$3,$4,$5}' "$1"; }

[ -f "$SRC" ] || { say "no roster: $SRC"; exit 2; }
cp "$SRC" "$S/base.tsv"

# --- fixture: force every NAS row to a stale coordinate -----------------------
# (a real dead-session value, not the live one, so there is always work to do)
awk -F'\t' -v OFS='\t' '$2=="nas"{$6=900000000+i} {i++}1' "$S/base.tsv" > "$S/stale.tsv"

cp "$S/stale.tsv" "$S/r.tsv"

# --- A: coordinate refresh on a stale roster ---------------------------------
out=$(EXEC_IDENTS_FILE=$S/r.tsv bash check-exec-copies.sh --record-coords); rc=$?
say "A rc=$rc $(grep '^record-coords:' <<<"$out")"
[ "$rc" -eq 0 ] || fail A "rc=$rc, expected 0"
if ! diff <(ident_cols "$S/stale.tsv") <(ident_cols "$S/r.tsv") >"$S/dA" 2>&1; then
  fail A "identity columns moved: $(cat "$S/dA")"
fi
n_stale=$(paste <(awk -F'\t' '{print $6}' "$S/stale.tsv") <(awk -F'\t' '{print $6}' "$S/r.tsv") | awk '$1!=$2' | wc -l)
say "A ino_rows_changed=$n_stale"
[ "$n_stale" -gt 0 ] || fail A "no ino row changed -- the arm graded nothing"

# --- E: the identity digest must not move when only coordinates do -----------
ids_before=$(awk -F'\t' '{print $1"\t"$5}' "$S/stale.tsv" | sha256sum | cut -c1-8)
ids_after=$(awk -F'\t' '{print $1"\t"$5}' "$S/r.tsv" | sha256sum | cut -c1-8)
say "E copies_idents $ids_before -> $ids_after"
[ "$ids_before" = "$ids_after" ] || fail E "the (name, btime) digest moved on a coordinate-only rewrite"

# --- B: the refreshed roster grades clean with no NOTE noise -----------------
outb=$(EXEC_IDENTS_FILE=$S/r.tsv bash check-exec-copies.sh); rcb=$?
say "B rc=$rcb $(tail -1 <<<"$outb")"
[ "$rcb" -eq 0 ] || fail B "rc=$rcb, expected 0"
grep -q 'ino_renumbered=0' <<<"$outb" || fail B "ino_renumbered != 0"
grep -q 'ok=5' <<<"$outb" || fail B "ok != 5"
if grep -q '^NOTE' <<<"$outb"; then fail B "clean roster still prints NOTE lines"; fi

# --- C: a moved identity must be refused, and the roster left alone ----------
cp "$S/r.tsv" "$S/c.tsv"
first=$(awk -F'\t' 'NR==1{print $1}' "$S/c.tsv")
awk -F'\t' -v OFS='\t' -v n="$first" '$1==n{$3="deadbeefdeadbeefdeadbeefdeadbeef"}1' "$S/c.tsv" > "$S/c.new" && mv "$S/c.new" "$S/c.tsv"
before=$(md5sum < "$S/c.tsv")
outc=$(EXEC_IDENTS_FILE=$S/c.tsv bash check-exec-copies.sh --record-coords 2>&1); rcc=$?
say "C rc=$rcc $(tail -1 <<<"$outc")"
[ "$rcc" -eq 2 ] || fail C "rc=$rcc, expected 2 (refusal)"
grep -q 'IDENTITY MOVED' <<<"$outc" || fail C "refusal did not name IDENTITY MOVED"
[ "$before" = "$(md5sum < "$S/c.tsv")" ] || fail C "REFUSED but the roster changed"

# --- D: a subset inside one root must be refused -----------------------------
cp "$S/r.tsv" "$S/d.tsv"
awk -F'\t' -v OFS='\t' -v n='check-grade-timer.sh' '$1==n{$6="900000999"}1' "$S/d.tsv" > "$S/d.new" && mv "$S/d.new" "$S/d.tsv"
before=$(md5sum < "$S/d.tsv")
outd=$(EXEC_IDENTS_FILE=$S/d.tsv bash check-exec-copies.sh --record-coords 2>&1); rcd=$?
say "D rc=$rcd $(grep -E 'SUBSET|REFUSED' <<<"$outd" | head -1)"
[ "$rcd" -eq 2 ] || fail D "rc=$rcd, expected 2 (refusal)"
grep -q 'SUBSET' <<<"$outd" || fail D "refusal did not name SUBSET"
[ "$before" = "$(md5sum < "$S/d.tsv")" ] || fail D "REFUSED but the roster changed"

if [ "$bad" -eq 0 ]; then say "record-coords arms: A=ok B=ok C=refused D=refused E=ok fail=0 rc=0"; exit 0; fi
say "record-coords arms: fail=$bad rc=1"; exit 1
