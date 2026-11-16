#!/usr/bin/env bash
# test-gate-head-vocabulary.sh -- the head= field must carry ONE vocabulary across the
# writer, the wrapper and all three gates.
#
# WHY THIS EXISTS (2026-11-16, frodo's residual on the publish guard): the writer and
# run-gate-logged.sh were fixed to a three-ending head= (none | unknown | sha), and the
# gates were still printing `head=no-git` from
#     sha=$(git rev-parse --short HEAD 2>/dev/null || echo no-git)
# -- the same collapse one layer up. `no-git` covered BOTH "this is not a repo" and "git
# ran and failed", so the verdict line a reader greps could not tell a mount/publish tree
# from a broken git, and a grep for head=none over gate logs returned nothing ever.
# Three vocabularies for one field is a field nobody can compare across artifacts.
#
# Frodo measured two states by hand; these are the assertions, over three gates:
#   G1  clean committed repo            -> head=<sha>, dirty=0 on all three gates
#   G2  tree with no .git (export)      -> head=none,  dirty=unknown on all three
#   G3  failing-git shim in PATH        -> head=unknown, dirty=unknown on all three
#   G4  G2's check-manifest repair line -> must NOT say "commit the wal-ops source"
#   G5  G3's check-manifest repair line -> must NOT say "commit the wal-ops source"
#   G6  edited source in a real repo    -> mirror_refuse=edited, repair DOES say commit
#
# G4/G5 are the reader-facing half: the repair sentence must be derived from the refusal,
# because on head=none and head=unknown there is no commit to make. G6 is the guard on the
# derivation: the cause that a commit DOES fix must still be sent to the commit.
#
# Usage: bash test-gate-head-vocabulary.sh [--keep]
# Exit: 0 every arm held, 1 any arm failed, 2 setup failed.
set -uo pipefail

WALOPS=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1
W=$(mktemp -d "${TMPDIR:-/tmp}/gate-head.XXXXXX") || exit 2
cleanup() { [ "$KEEP" = 1 ] && { echo "kept: $W"; return; }; rm -rf "$W"; }
trap cleanup EXIT

GATES="check-manifest.sh check-wal-doc-links.sh check-exec-copies.sh"
R="$W/repo"; NR="$W/norepo"; SHIM="$W/shim"
mkdir -p "$R" "$NR" "$SHIM" "$W/nas" "$W/home/.local/bin" "$W/home" || exit 2

# the repo carries the whole wal-ops tree (minus .git) so MANIFEST rows and DOCS resolve
tar -C "$WALOPS" --exclude=.git --exclude=__pycache__ -cf - . 2>/dev/null | tar -C "$R" -xf - || {
  echo "SETUP FAIL: could not copy wal-ops into $R" >&2; exit 2; }
( cd "$R" && git init -q \
  && git -c user.email=t@example.invalid -c user.name=t add -A \
  && git -c user.email=t@example.invalid -c user.name=t commit -qm init ) || {
  echo "SETUP FAIL: could not commit the scratch repo" >&2; exit 2; }
HEAD_SHA=$(cd "$R" && git rev-parse --short HEAD)

# an export: the same bytes with no .git at all
( cd "$R" && git archive HEAD ) | tar -x -C "$NR" || {
  echo "SETUP FAIL: could not export the no-.git tree" >&2; exit 2; }

printf '#!/bin/sh\nexit 1\n' > "$SHIM/git"; chmod +x "$SHIM/git"

fail=0
pass() { echo "PASS $*"; }
bad()  { echo "FAIL $*"; fail=$((fail+1)); }
# verdict-line field readers: `head=` and `dirty=` appear once per verdict line
field() { sed -n "s/.*[[:space:]]$1=\\([^[:space:]]*\\).*/\\1/p" <<< "$2" | head -1; }
# one gate run: dir, PATH prefix, gate name -> stdout+stderr, rc discarded
run_gate() {
  ( cd "$1" && env -i PATH="$2" HOME="$W/home" NAS="$W/nas" \
      MIRROR_JOURNAL="$W/nas/.mirror-repairs.log" \
      bash "./$3" ) 2>&1
}
verdict() { grep -E '^(manifest|doc-links|exec-copies) gate:' <<< "$1" | tail -1; }

# --- G1: clean committed repo -> sha on all three gates ---------------------------
out_clean=""
for g in $GATES; do
  line=$(verdict "$(run_gate "$R" "$PATH" "$g")")
  h=$(field head "$line"); d=$(field dirty "$line")
  [ "$h" = "$HEAD_SHA" ] && pass "G1 $g head=$HEAD_SHA" || bad "G1 $g head=$h, expected $HEAD_SHA"
  [ "$d" = 0 ] && pass "G1 $g dirty=0" || bad "G1 $g dirty=$d, expected 0"
  out_clean="$out_clean$line"$'\n'
done
# the cause field must not spell `none` when nothing was refused: mirror_refuse=none beside
# head=1f94814 is two facts in one token, the exact defect this turn is about.
clean_cause=$(field mirror_refuse "$out_clean")
[ "$clean_cause" = - ] && pass "G1 mirror_refuse=- on a green run (not 'none', which head= already means)" \
                      || bad "G1 mirror_refuse=$clean_cause on a green run, expected -"

# --- G2: no .git (an export) -> none on all three gates ---------------------------
out_none=""; full_none=""
for g in $GATES; do
  raw=$(run_gate "$NR" "$PATH" "$g")
  line=$(verdict "$raw")
  [ "$g" = check-manifest.sh ] && full_none="$raw"   # G4 needs the repair LINE, not the verdict
  h=$(field head "$line"); d=$(field dirty "$line")
  [ "$h" = none ] && pass "G2 $g head=none (no repo here, and it says which fact)" \
                  || bad "G2 $g head=$h, expected none"
  [ "$d" = unknown ] && pass "G2 $g dirty=unknown (agrees with head= instead of contradicting it)" \
                     || bad "G2 $g dirty=$d, expected unknown"
  case "$h" in no-git) bad "G2 $g still prints the abolished token head=no-git";; esac
  out_none="$out_none$line"$'\n'
done

# --- G3: failing git -> unknown on all three gates --------------------------------
out_shim=""; full_shim=""
for g in $GATES; do
  raw=$(run_gate "$R" "$SHIM:$PATH" "$g")
  line=$(verdict "$raw")
  [ "$g" = check-manifest.sh ] && full_shim="$raw"
  h=$(field head "$line"); d=$(field dirty "$line")
  [ "$h" = unknown ] && pass "G3 $g head=unknown (git ran and failed -- a different fact from none)" \
                     || bad "G3 $g head=$h, expected unknown"
  [ "$d" = unknown ] && pass "G3 $g dirty=unknown" || bad "G3 $g dirty=$d, expected unknown"
  case "$h" in no-git) bad "G3 $g still prints the abolished token head=no-git";; esac
  out_shim="$out_shim$line"$'\n'
done

# --- G4/G5: the repair sentence on the two trees where committing cannot work ------
check_repair() {   # want, verdict-lines, full output of check-manifest.sh
  local want="$1" lines="$2" full="$3" line cause
  line=$(grep '^manifest gate:' <<< "$lines")
  cause=$(field mirror_refuse "$line")
  [ "$cause" = "$want" ] && pass "G4/G5 mirror_refuse=$want (the cause is a field, not only a sentence)" \
                        || bad "G4/G5 mirror_refuse=$cause, expected $want"
  if grep -q 'repair: commit the wal-ops source' <<< "$full"; then
    bad "G4/G5 cause=$want still tells the reader to commit -- nothing can be committed into a HEAD that does not resolve"
  else
    pass "G4/G5 cause=$want repair line does not say 'commit the wal-ops source'"
  fi
}
check_repair head_none "$out_none" "$full_none"
check_repair head_unknown "$out_shim" "$full_shim"

# --- G6: an edited source in a real repo -> the cause a commit DOES fix ------------
printf '\n# uncommitted edit\n' >> "$R/grade-slot.sh"
g6out=$(run_gate "$R" "$PATH" "check-manifest.sh")
line=$(verdict "$g6out")
cause=$(field mirror_refuse "$line")
[ "$cause" = edited ] && pass "G6 mirror_refuse=edited with git working" || bad "G6 mirror_refuse=$cause, expected edited"
if grep -q 'repair: commit the wal-ops source' <<< "$g6out"; then
  pass "G6 the edited-source case still gets the commit repair"
else
  bad "G6 the edited-source case lost its commit repair"
fi
( cd "$R" && git checkout -q -- grade-slot.sh )

echo "---"
if [ "$fail" = 0 ]; then echo "RESULT PASS (0 failed arms)"; exit 0; fi
echo "RESULT FAIL ($fail failed arms)"; exit 1
