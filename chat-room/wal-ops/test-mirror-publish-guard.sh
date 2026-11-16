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

# --- G/H: a tree BEHIND the published head -> refused (G), hatch publishes (H) ------
# The arm's shape is buzzy's 2026-11-16 replay: a clean `git worktree` checkout of an OLD
# commit, where every source blob equals its HEAD blob (so the PUBLISH GUARD passes) and the
# published legs were written from a LATER commit. Nothing is dirty, nothing is untracked;
# the only fact is ancestry. That run used to publish old bytes over newer legs, and its
# --dry twin graded a checkout while reading as a grade of the commit.
# G runs FIRST with ALLOW_ROLLBACK=1 and REQUIRES rc=0, because a refusal arm that could
# pass for the blob guard's reason proves nothing: this tree must be publishable when the
# rollback is deliberate, and refused only because it is behind.
BR="$W/behind"; mkdir -p "$BR"
for f in $SRCS; do cp "$WALOPS/$f" "$BR/$f" || exit 2; done
( cd "$BR" && git init -q \
  && git -c user.email=t@example.invalid -c user.name=t add -A \
  && git -c user.email=t@example.invalid -c user.name=t commit -qm one \
  && printf '\n# newer than the checkout this arm grades\n' >> grade-slot.sh \
  && git -c user.email=t@example.invalid -c user.name=t commit -qam two ) || {
  echo "SETUP FAIL: could not build the two-commit repo at $BR" >&2; exit 2; }
OLD12=$(cd "$BR" && git rev-parse --short=12 HEAD~1)
PUB12=$(cd "$BR" && git rev-parse --short=12 HEAD~1 >/dev/null; git rev-parse --short=12 HEAD)
( cd "$BR" && git checkout -q --detach "$OLD12" ) || { echo "SETUP FAIL: cannot detach at $OLD12" >&2; exit 2; }
GJ="$W/nas/g-journal.log"
# one journal row, attributable to the LATER commit: that is the published coordinate
printf 'repair ts=2026-11-16T00:00:00Z head=%s dirty=0 leg=%s old=absent new=x root=%s\n' \
  "$PUB12" "$W/nas/../run-gate-logged.sh" "$W" > "$GJ"
stale_legs
out=$( cd "$BR" && env -i PATH="$PATH" HOME="$W/home" NAS="$W/nas" MIRROR_JOURNAL="$GJ" ALLOW_ROLLBACK=1 \
       bash ./install-mirror-legs.sh 2>&1 ); rc=$?
[ "$rc" = 0 ] && pass "H rc=0 at $OLD12 with ALLOW_ROLLBACK=1 (the tree is publishable: the refusal below is about ancestry, not blobs)" \
              || bad "H rc=$rc at $OLD12 with ALLOW_ROLLBACK=1 -- the arm would pass G for the wrong reason: $(grep '^REFUSE' <<< "$out" | tr '\n' '|')"
# H's own writes journal head=$OLD12 (the journal records the tree AT THE WRITE, so a
# deliberate rollback MOVES the published coordinate backward -- that is what the hatch
# means). So the published coordinate has to be re-seeded before G, or G would be grading
# a journal whose newest row is its own HEAD and the ancestry test would never fire.
seed_pub() { printf 'repair ts=2026-11-16T00:00:00Z head=%s dirty=0 leg=%s old=absent new=x root=%s\n' \
  "$PUB12" "$W/nas/../run-gate-logged.sh" "$W" > "$GJ"; }
seed_pub
stale_legs
before=$(leg_state); jbefore=$(wc -l < "$GJ" | tr -d ' ')
out=$( cd "$BR" && env -i PATH="$PATH" HOME="$W/home" NAS="$W/nas" MIRROR_JOURNAL="$GJ" \
       bash ./install-mirror-legs.sh 2>&1 ); rc=$?
after=$(leg_state); jafter=$(wc -l < "$GJ" | tr -d ' ')
[ "$rc" = 5 ] && pass "G rc=5 at a historical checkout (tree-behind refused)" || bad "G rc=$rc, expected 5: $(grep '^REFUSE' <<< "$out" | tr '\n' '|')"
[ "$before" = "$after" ] && pass "G nothing written (no leg rolled backward)" || bad "G a published leg was rolled BACKWARD by a historical checkout"
[ "$jbefore" = "$jafter" ] && pass "G no journal row for the refusal" || bad "G journal grew $jbefore -> $jafter"
grep -q '^REFUSE tree-behind' <<< "$out" && pass "G refusal names tree-behind" \
  || bad "G refusal does not name tree-behind: $(grep '^REFUSE' <<< "$out" | tr '\n' '|')"
grep -q "published head $PUB12" <<< "$out" && pass "G refusal names the published head $PUB12" \
  || bad "G refusal does not name the published head $PUB12"
grep -q 'ALLOW_ROLLBACK' <<< "$out" && pass "G refusal names the escape hatch" \
  || bad "G refusal does not name the escape hatch"
# --dry over the same tree must fail the same way: the gate grades with --dry, so a guard
# that only fired on a live run would leave the anachronism in every gate line.
stale_legs
out=$( cd "$BR" && env -i PATH="$PATH" HOME="$W/home" NAS="$W/nas" MIRROR_JOURNAL="$GJ" \
       bash ./install-mirror-legs.sh --dry 2>&1 ); rc=$?
[ "$rc" = 5 ] && pass "G --dry also refuses at a historical checkout (rc=5, not rc=3 drift)" \
              || bad "G --dry rc=$rc, expected 5 -- the gate would read anachronistic drift as a defect"
grep -q '^dry  would write' <<< "$out" && bad "G --dry still printed would-write lines (drift was measured for a tree that cannot publish)" \
  || pass "G --dry printed no would-write line"

echo "---"
if [ "$fail" = 0 ]; then echo "RESULT PASS (0 failed arms)"; exit 0; fi
echo "RESULT FAIL ($fail failed arms)"; exit 1
