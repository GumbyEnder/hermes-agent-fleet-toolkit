#!/usr/bin/env bash
# check-index-rows.sh -- the READER for INDEX.tsv field NINE.
#
# WHY (measured 2026-11-15, frodo's audit): `run-gate-logged.sh` writes nine fields, but until this
# script there was no reader at all -- `grep -rn INDEX.tsv` over /mnt/nas/host-ops and the repo
# returned the writer and one doc line. A field with no consumer cannot regress: it can only rot
# silently. Field nine had never once run on a non-empty argument list in the live table either
# (all 8 nine-field rows were `<none>`), so the quoting path was untested where it mattered.
#
# What this gate asserts, per row (INDEX.tsv is tab-separated, append-only):
#   1. NF is 8 or 9, nothing else. NF>9 means an argument carried a raw tab into the row and
#      shifted the columns right; NF<8 means a writer lost a column. Both are FAIL.
#   2. A 9-field row may never be followed by an 8-field row. Field nine was introduced at
#      68f5065; the file is append-only, so a later 8-field row is a writer rollback. A strict
#      reader would then skip the row that records the regression -- silently.
#   3. Field nine on a 9-field row is non-empty. An empty ninth column is a row that ends in a
#      tab: the writer knew to append a field and did not know what to put in it.
#   4. Field nine is never `''`. This is the trap, not a style check: `printf '%q ' "$@"` with
#      zero operands prints `''`, so the pre-guard writer recorded "no arguments" as a quoted
#      empty argument. `''` is also what a caller who genuinely passed ONE empty argument gets,
#      and the row cannot tell the two apart -- so the value is ambiguous, not merely wrong, and
#      ambiguity is a refusal here. The writer now says `<none>`; `''` means the guard is gone.
#   5. At least one 9-field row exists. A file with none is a file that predates or lost the
#      column, and every check above would pass vacuously over it.
#
# rows= IS A READ-TIME SNAPSHOT, AND THE ROW THAT REPORTS IT CANNOT BE IN IT.
# Measured three times (2026-11-15, 21:47 and later, twice more): the gate printed rows=N
# against a file of N lines, and the very next wrapper run appended N+1. Not a race, not a
# counter that "cannot keep up": run-gate-logged.sh appends this run's INDEX row AFTER the
# gate returns, so the gate can never count its own witness. The closing line therefore
# carries the decomposition instead of a bare total -- rows=<non-blank rows read>,
# lines=<NR>, blank=<lines-rows>, self_row=excluded -- and the testable invariant for a
# wrapped run is rows + 1 == the line count a later reader sees. Two earlier readings of
# this were wrong: "the file grew" (nothing grew between the read and the print) and
# "the counter is one behind" (it counts exactly what exists; the missing row does not
# exist yet). Decomposing the field is the fix; adding +1 would be a prediction, not a count.
#
# Usage: bash check-index-rows.sh [index-path]
#        default: ${GATE_LOG_DIR:-/mnt/nas/host-ops/gate-logs}/INDEX.tsv
# Exit: 0 all rows held, 1 any refusal, 2 index unreadable.
# Run it through run-gate-logged.sh from the repo root so the verdict has a log path.

set -u

IDX="${1:-${GATE_LOG_DIR:-/mnt/nas/host-ops/gate-logs}/INDEX.tsv}"

if [ ! -f "$IDX" ]; then
  printf 'index-rows gate: index=%s rows=0 lines=0 blank=0 self_row=excluded eight=0 nine=0 ok=0 fail=1 rc=2 -- index not readable\n' "$IDX"
  exit 2
fi

# FAIL lines go to stderr (they are the evidence); the single stdout line is the counts, so the
# caller reads exactly one number tuple and never a complaint line as if it were a verdict.
VERDICT=$(awk -F'\t' '
  function fail(s) { print s > "/dev/stderr"; bad++ }
  /^[[:space:]]*$/ { next }
  {
    rows++
    if (NF == 9) { nine++; seen_nine = 1 }
    else if (NF == 8) {
      eight++
      if (seen_nine)
        fail(sprintf("FAIL line %d: 8-field row AFTER the first 9-field row (ts=%s gate=%s head=%s)", NR, $1, $2, $3))
    }
    else
      fail(sprintf("FAIL line %d: NF=%d, expected 8 or 9 (ts=%s gate=%s) -- a tab inside a field shifts every column to its right", NR, NF, $1, $2))
    if (NF == 9) {
      if ($9 == "")
        fail(sprintf("FAIL line %d: field 9 is empty (ts=%s gate=%s)", NR, $1, $2))
      else if ($9 == "\047\047")
        fail(sprintf("FAIL line %d: field 9 is %s -- ambiguous: the unguarded no-arg path writes this identical token, so the row cannot say whether zero or one empty argument was passed (ts=%s gate=%s)", NR, $9, $1, $2))
    }
  }
  END {
    if (rows > 0 && nine == 0)
      fail(sprintf("FAIL: no 9-field rows in %d rows -- this file predates or lost field nine, so every other check is vacuous", rows))
    printf "%d %d %d %d %d %d\n", rows, NR, NR - rows, eight, nine, bad
  }
' "$IDX")

read -r rows lines blank eight nine bad <<< "$VERDICT"
rows=${rows:-0}; lines=${lines:-0}; blank=${blank:-0}; eight=${eight:-0}; nine=${nine:-0}; bad=${bad:-0}

if [ "$bad" -eq 0 ]; then
  printf 'index-rows gate: index=%s rows=%s lines=%s blank=%s self_row=excluded eight=%s nine=%s ok=%s fail=0 rc=0\n' \
    "$IDX" "$rows" "$lines" "$blank" "$eight" "$nine" "$rows"
  exit 0
fi

printf 'index-rows gate: index=%s rows=%s lines=%s blank=%s self_row=excluded eight=%s nine=%s ok=%s fail=%s rc=1\n' \
  "$IDX" "$rows" "$lines" "$blank" "$eight" "$nine" "$((rows - bad))" "$bad"
exit 1
