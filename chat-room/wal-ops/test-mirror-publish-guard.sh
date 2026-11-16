#!/usr/bin/env bash
# test-mirror-publish-guard.sh -- acceptance arms for the publish guard in install-mirror-legs.sh.
#
# WHY THIS EXISTS (2026-11-16, frodo's residual on the dirty=unknown fix): the writer
# journalled `dirty=unknown` honestly and still PUBLISHED four legs it could not vouch
# for, exiting 0. Frodo ran exactly that shape on a scratch root with a failing-git shim
# and one tampered leg: four `wrote` lines, a four-row journal, rc=0. The honest token
# did not refuse anything -- the refusal did not exist. This script is that run turned
# into an arm: a failing-git publish must exit nonzero and write NOTHING.
#
# The guard asserts what "publishing the repo bytes" means, not tree cleanliness:
#     for each leg f:  git hash-object ./$f  ==  git rev-parse HEAD:./$f
# so unrelated dirt is indifferent (a tree-clean guard would refuse the very repair it
# exists for) and an edited source is refused.
#
# Arms:
#   A  committed sources + one unrelated dirty file + stale legs -> publishes, rc=0,
#      journal row carries head=<sha12> dirty=1, legs now equal their sources.
#   B  failing git (rc=1 shim) over stale legs                   -> rc=5, nothing written,
#      no journal row, head=unknown.
#   C  edited source, git working, clean legs                    -> rc=5, nothing written.
#   D  no repo (tree copied without .git)                        -> rc=5, head=none.
#   E  --dry over stale legs                                     -> rc=3, nothing written.
#   F  source present on disk, ABSENT FROM HEAD (untracked)      -> rc=5, nothing written,
#      no journal row, refusal says "does not resolve" and NOT the edited-source sentence.
#      Added 2026-11-16 from frodo's arm: the code has the branch, the message is
#      deliberately distinct, and the tracked test had no assertion for it. The repair
#      differs -- untracked needs `git add` AND a commit, an edited source needs only the
#      commit -- so a shared "uncommitted edits" sentence would be wrong for a file that
#      was never in HEAD.
#
# Usage: bash test-mirror-publish-guard.sh [--keep]
# Exit: 0 every arm held, 1 any arm failed, 2 setup failed.
set -uo pipefail

WALOPS=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1
W=$(mktemp -d "${TMPDIR:-/tmp}/mirror-guard.XXXXXX") || exit 2
cleanup() { [ "$KEEP" = 1 ] && { echo "kept: $W"; return; }; rm -rf "$W"; }
trap cleanup EXIT

SRCS="grade-slot.sh run-gate-logged.sh witness-split-pair-v2.sh verify-slot-manifest.sh install-mirror-legs.sh"
R="$W/repo"
mkdir -p "$R" "$W/nas" "$W/home" "$W/home/.local/bin" "$W/shim" || exit 2
for f in $SRCS; do cp "$WALOPS/$f" "$R/$f" || exit 2; done
printf 'unrelated\n' > "$R/unrelated.txt"
( cd "$R" && git init -q \
  && git -c user.email=t@example.invalid -c user.name=t add -A \
  && git -c user.email=t@example.invalid -c user.name=t commit -qm init ) || {
  echo "SETUP FAIL: could not build the scratch repo at $R" >&2; exit 2; }
HEAD12=$(cd "$R" && git rev-parse --short=12 HEAD)
printf '#!/bin/sh\nexit 1\n' > "$W/shim/git"; chmod +x "$W/shim/git"

fail=0
pass() { echo "PASS $*"; }
bad()  { echo "FAIL $*"; fail=$((fail+1)); }

# stale every published leg, so any publishing run must write all four
stale_legs() {
  for f in grade-slot.sh run-gate-logged.sh witness-split-pair-v2.sh verify-slot-manifest.sh; do
    printf 'stale copy of %s\n' "$f" > "$W/nas/$f"
    case "$f" in
      grade-slot.sh) printf 'stale copy of %s\n' "$f" > "$W/home/.local/bin/$f" ;;
      run-gate-logged.sh) printf 'stale copy of %s\n' "$f" > "$W/run-gate-logged.sh" ;;
    esac
  done
}
# md5 of every published leg, one line each: the "nothing was written" witness
leg_state() {
  { md5sum "$W/home/.local/bin/grade-slot.sh" "$W/nas/grade-slot.sh" "$W/run-gate-logged.sh" \
            "$W/nas/witness-split-pair-v2.sh" "$W/nas/verify-slot-manifest.sh" 2>&1; } | awk '{print $1}'
}
journal_rows() { [ -f "$W/nas/.mirror-repairs.log" ] && wc -l < "$W/nas/.mirror-repairs.log" | tr -d ' ' || echo 0; }
run_writer() { ( cd "$R" && env -i PATH="$1" HOME="$W/home" NAS="$W/nas" MIRROR_JOURNAL="$W/nas/.mirror-repairs.log" \
                 bash ./install-mirror-legs.sh "${@:2}" ); }

# --- A: committed sources, unrelated dirt, stale legs -> publishes ------------------
stale_legs
printf 'dirty but unrelated\n' >> "$R/unrelated.txt"
out=$(run_writer "$PATH" 2>&1); rc=$?
[ "$rc" = 0 ] && pass "A rc=0 with one dirty unrelated file (dirt outside the leg table is indifferent)" \
              || bad "A rc=$rc, expected 0 (guard refuses unrelated dirt?)"
mrow=$(journal_rows)
[ "$mrow" = 5 ] && pass "A journal rows=5 (one per write; grade-slot.sh has two legs)" || bad "A journal rows=$mrow, expected 5"
row=$(tail -1 "$W/nas/.mirror-repairs.log" 2>/dev/null)
grep -q "head=$HEAD12 " <<< "$row" && pass "A journal head=$HEAD12" || bad "A journal head is not $HEAD12: $row"
grep -q ' dirty=1 ' <<< "$row" && pass "A journal dirty=1 (unrelated dirt is recorded, not hidden)" \
                               || bad "A journal dirty is not 1: $row"
for f in grade-slot.sh witness-split-pair-v2.sh; do
  cmp -s "$R/$f" "$W/nas/$f" && pass "A published $f == repo source" || bad "A published $f differs from source"
done
[ -f "$W/run-gate-logged.sh" ] && cmp -s "$R/run-gate-logged.sh" "$W/run-gate-logged.sh" \
  && pass "A published run-gate-logged.sh == repo source" || bad "A run-gate-logged.sh leg not published to derived HOSTOPS root"
cd "$R" && git checkout -q -- unrelated.txt; cd - >/dev/null

# --- B: failing git shim over stale legs -> rc=5, NOTHING written ------------------
stale_legs
before=$(leg_state); jbefore=$(journal_rows); jmtime_before=$(stat -c %Y "$W/nas/.mirror-repairs.log" 2>/dev/null || echo none)
out=$(run_writer "$W/shim:$PATH" 2>&1); rc=$?
after=$(leg_state); jafter=$(journal_rows); jmtime_after=$(stat -c %Y "$W/nas/.mirror-repairs.log" 2>/dev/null || echo none)
[ "$rc" != 0 ] && pass "B rc=$rc (nonzero: a failing git does not publish)" || bad "B rc=0 -- the counter-example still publishes"
[ "$rc" = 5 ] && pass "B rc=5 (the named refusal state)" || bad "B rc=$rc, expected 5"
[ "$before" = "$after" ] && pass "B every published leg is byte-identical before/after (nothing written)" \
                         || bad "B a published leg changed bytes during a refused run"
[ "$jbefore" = "$jafter" ] && pass "B journal rows unchanged ($jbefore) -- no repair token for a repair that did not happen" \
                           || bad "B journal grew $jbefore -> $jafter during a refused run"
[ "$jmtime_before" = "$jmtime_after" ] && pass "B journal mtime unchanged" || bad "B journal mtime moved"
grep -q 'head=unknown' <<< "$out" && pass "B refusal names head=unknown (git ran and failed, not 'no repo')" \
                                 || bad "B refusal does not name head=unknown: $(head -3 <<< "$out" | tr '\n' '|')"
grep -qi 'nothing written' <<< "$out" && pass "B refusal states nothing was written" \
                                     || bad "B refusal does not state nothing was written"

# --- C: edited source, working git, clean legs -> rc=5 -----------------------------
stale_legs
printf '\n# uncommitted edit\n' >> "$R/grade-slot.sh"
before=$(leg_state); jbefore=$(journal_rows)
out=$(run_writer "$PATH" 2>&1); rc=$?
after=$(leg_state); jafter=$(journal_rows)
[ "$rc" = 5 ] && pass "C rc=5 on an edited source" || bad "C rc=$rc, expected 5"
[ "$before" = "$after" ] && pass "C nothing written" || bad "C a leg changed during a refused run"
[ "$jbefore" = "$jafter" ] && pass "C no journal row" || bad "C journal grew $jbefore -> $jafter"
grep -q 'disk blob' <<< "$out" && pass "C refusal prints both blobs" || bad "C refusal does not print the blob pair: $out"
( cd "$R" && git checkout -q -- grade-slot.sh )

# --- D: no repo -> rc=5, head=none -------------------------------------------------
NR="$W/norepo"; mkdir -p "$NR"; for f in $SRCS; do cp "$R/$f" "$NR/$f"; done
stale_legs
before=$(leg_state)
out=$( cd "$NR" && env -i PATH="$PATH" HOME="$W/home" NAS="$W/nas" MIRROR_JOURNAL="$W/nas/.mirror-repairs.log" \
       bash ./install-mirror-legs.sh 2>&1 ); rc=$?
after=$(leg_state)
[ "$rc" = 5 ] && pass "D rc=5 with no .git" || bad "D rc=$rc, expected 5"
grep -q 'head=none' <<< "$out" && pass "D refusal names head=none (a different fact from unknown)" \
                              || bad "D refusal does not name head=none: $out"
[ "$before" = "$after" ] && pass "D nothing written" || bad "D a leg changed during a refused run"

# --- E: --dry over stale legs -> rc=3, nothing written -----------------------------
stale_legs
before=$(leg_state)
out=$(run_writer "$PATH" --dry 2>&1); rc=$?
after=$(leg_state)
[ "$rc" = 3 ] && pass "E rc=3 on --dry drift" || bad "E rc=$rc, expected 3"
[ "$before" = "$after" ] && pass "E --dry wrote nothing" || bad "E --dry wrote a leg"

# --- F: source on disk, absent from HEAD -> rc=5, distinct message -----------------
# The fourth rc=5 cause. Repo built from the five sources with grade-slot.sh deliberately
# untracked: git status must show `?? grade-slot.sh`, so the disk bytes exist and HEAD has
# never carried them. This is the case where "commit the wal-ops source" is HALF a repair.
FR="$W/untracked"; mkdir -p "$FR"
for f in $SRCS; do cp "$WALOPS/$f" "$FR/$f" || exit 2; done
( cd "$FR" && git init -q \
  && git -c user.email=t@example.invalid -c user.name=t add -A \
  && git -c user.email=t@example.invalid -c user.name=t commit -qm init \
  && git rm -q --cached grade-slot.sh \
  && git -c user.email=t@example.invalid -c user.name=t commit -qm "grade-slot.sh is not in this commit" ) || {
  echo "SETUP FAIL: could not build the untracked-source repo at $FR" >&2; exit 2; }
if ( cd "$FR" && git status --porcelain ) | grep -q '^?? grade-slot.sh$'; then
  pass "F setup: grade-slot.sh is on disk and untracked (?? in git status)"
else
  bad "F setup: grade-slot.sh is not untracked -- the arm measures the wrong shape"
fi
stale_legs
before=$(leg_state); jbefore=$(journal_rows)
out=$( cd "$FR" && env -i PATH="$PATH" HOME="$W/home" NAS="$W/nas" MIRROR_JOURNAL="$W/nas/.mirror-repairs.log" \
       bash ./install-mirror-legs.sh 2>&1 ); rc=$?
after=$(leg_state); jafter=$(journal_rows)
[ "$rc" = 5 ] && pass "F rc=5 on a source absent from HEAD" || bad "F rc=$rc, expected 5"
[ "$before" = "$after" ] && pass "F nothing written" || bad "F a leg changed during a refused run"
[ "$jbefore" = "$jafter" ] && pass "F no journal row" || bad "F journal grew $jbefore -> $jafter"
grep -q 'does not resolve' <<< "$out" \
  && pass "F refusal says the source does not resolve at HEAD" \
  || bad "F refusal does not say 'does not resolve': $(grep '^REFUSE' <<< "$out" | tr '\n' '|')"
# The distinctness assertion: this cause must NOT borrow the edited-source sentence, or the
# repair a reader acts on is wrong for a file that was never in HEAD.
if grep -q 'uncommitted edits' <<< "$out"; then
  bad "F refusal reuses the edited-source sentence for an untracked file (repair would be half of one)"
else
  pass "F refusal is distinct from the edited-source message"
fi

echo "---"
if [ "$fail" = 0 ]; then echo "RESULT PASS (0 failed arms)"; exit 0; fi
echo "RESULT FAIL ($fail failed arms)"; exit 1
