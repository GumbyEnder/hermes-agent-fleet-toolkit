#!/usr/bin/env bash
# check-manifest.sh -- cwd-independent provenance gate for wal-ops.
#
# MANIFEST.md5 rows are bare basenames (and systemd/<name>), i.e. relative to
# this directory. `md5sum -c MANIFEST.md5` therefore passes only when the shell
# happens to be in wal-ops/ and reports "FAILED open or read" for every row from
# the repo root -- a gate whose verdict depends on cwd (measured 2026-11-14:
# 20/20 OK here, 0 OK from the repo root). Always run this wrapper, from
# anywhere: it cds to its own directory first.
#
# The last line is the verdict, and it is designed to be the thing that gets
# quoted. A bare row count is a number any reader can mis-carry (41 rows is
# correct at 7335bd7 and reads as WRONG to anyone running this gate at HEAD); so
# the line carries manifest_oid -- the git blob oid of MANIFEST.md5, which is
# content-addressed: the same rows cannot produce a different oid and different
# rows cannot produce the same one, so a count can no longer drift away from the
# rows it describes. `head` and `dirty` say which tree was measured, which is
# what makes a run that happens BEFORE the doc commit still honest to quote
# (dirty=1, head=parent commit).
set -u
cd "$(dirname "$(readlink -f "$0")")" || exit 2

sha=$(git rev-parse --short HEAD 2>/dev/null || echo no-git)
dirty=$(git status --porcelain --untracked-files=no 2>/dev/null | head -c 1 | wc -c)
rows=$(grep -c '[^[:space:]]' MANIFEST.md5)
moid=$(git hash-object MANIFEST.md5 2>/dev/null || md5sum MANIFEST.md5 | awk '{print $1}')

out=$(md5sum -c MANIFEST.md5 2>&1)
rc=$?
printf '%s\n' "$out"
# Count ROW lines only: md5sum's own "md5sum: WARNING: N computed checksums did not
# match" line is a summary, not a row, and counting it made fail=3 for two bad rows
# (measured on the first run of this version).
ok=$(printf '%s\n' "$out" | grep -c ': OK$')
fail=$(printf '%s\n' "$out" | grep -v '^md5sum: ' | grep -vc ': OK$')
seen=$(printf '%s\n' "$out" | grep -vc '^md5sum: ')

# --- ROW SET ASSERTION -------------------------------------------------------
# `md5sum -c` is a PER-ROW predicate. It says "every row the file declares matches";
# it never reads the total, so deleting a row leaves rc=0. Measured 2026-11-15: an
# exported 7e9c74f with `sed -i 1d MANIFEST.md5` -> 35 OK lines, no complaint, rc=0;
# the same one-line delete at HEAD -> rows=42 rows_seen=42 ok=42 fail=0 rc=0. And a
# count cannot fix it, because `rows` and `rows_seen` are both derived from the same
# file: they agree with each other precisely when the file has shrunk. The oid pins
# what the remaining rows SAY; it pins nothing about how many there are.
#
# The only witness that is not circular is the tree. Assert set equality:
#     rows  ==  (files git tracks in wal-ops)  -  (deliberately unrostered files)
# Unrostered = every DOCS name (a doc's integrity leg is check-wal-doc-links.sh, never
# a row) plus MANIFEST.md5 and README.md. DOCS is parsed out of check-wal-doc-links.sh
# so the two gates read ONE contract: adding a doc to DOCS does not need a second edit
# here. If that parse ever comes back empty the gate fails loud (every doc then reads
# as an unrostered tracked file) rather than passing quiet.
#
# Both directions fail: a tracked script with no row, and a row for a file the tree
# does not carry. Nothing is asserted when git is unavailable (an export or a tarball
# has no index to compare against) -- that case prints rowset=nogit and says so.
META="MANIFEST.md5 README.md"
docs_line=$(sed -n 's/^DOCS="\(.*\)"[[:space:]]*$/\1/p' check-wal-doc-links.sh 2>/dev/null)
rs_tmp=$(mktemp -d) || exit 2
rs_unrostered=0
rs_missing=0
rs_state=nogit
if tracked=$(git ls-files . 2>/dev/null) && [ -n "$tracked" ]; then
  rs_state=asserted
  printf '%s\n' $META $docs_line | sed '/^$/d' | sort -u > "$rs_tmp/exc"
  printf '%s\n' "$tracked" | sort -u > "$rs_tmp/trk"
  comm -23 "$rs_tmp/trk" "$rs_tmp/exc" > "$rs_tmp/want"      # rows that must exist
  awk '{print $2}' MANIFEST.md5 | sed 's|^\./||' | sort -u > "$rs_tmp/have"
  comm -23 "$rs_tmp/want" "$rs_tmp/have" > "$rs_tmp/extra"   # tracked, no row
  comm -13 "$rs_tmp/want" "$rs_tmp/have" > "$rs_tmp/gone"    # row, not in the tree
  rs_unrostered=$(grep -c '[^[:space:]]' "$rs_tmp/extra")
  rs_missing=$(grep -c '[^[:space:]]' "$rs_tmp/gone")
  sed 's|^|FAIL unrostered (tracked file has no MANIFEST row): |' "$rs_tmp/extra"
  sed 's|^|FAIL stray row (no such tracked file): |' "$rs_tmp/gone"
  if [ "$rs_unrostered" -gt 0 ] || [ "$rs_missing" -gt 0 ]; then rc=1; fi
fi
rm -rf "$rs_tmp"

# --- MIRROR LEGS -------------------------------------------------------------
# The roster hashes the REPO worktree. Some of these files are also published at a
# path a unit execs or a room post cites as "installed at ...", and the rowset
# assertion above says nothing about that path. Measured 2026-11-15 09:38: commit
# 6158258 moved grade-slot.sh in the tree, in the roster AND in ~/.local/bin, while
# /mnt/nas/host-ops/agent-chat/grade-slot.sh kept the pre-fix bytes (a1b524e9,
# 15,606 B) for 25 minutes and this gate read green throughout. A green row count is
# a fact about the set the gate reads; the published legs are the set that runs.
#
# The leg table lives in install-mirror-legs.sh, so the gate asks the WRITER what it
# would do rather than carrying a second copy of the table that can drift from it.
# A missing or erroring writer is a FAIL, never a skip: an unread contract cannot pass.
m_ok=0; m_drift=0; m_skip=0; m_state=measured
if [ -x ./install-mirror-legs.sh ]; then
  m_out=$(./install-mirror-legs.sh --dry 2>&1); m_rc=$?
  m_ok=$(printf '%s\n' "$m_out" | grep -c '^ok   ')
  m_drift=$(printf '%s\n' "$m_out" | grep -c '^dry  would write')
  m_skip=$(printf '%s\n' "$m_out" | grep -c '^skip ')
  if [ "$m_drift" -gt 0 ]; then
    printf '%s\n' "$m_out" | grep '^dry  would write' \
      | sed 's|^dry  would write |FAIL mirror drift (published leg is stale): |'
    rc=1
  fi
  [ "$m_rc" -eq 0 ] || { printf '%s\n' "$m_out" | grep '^FAIL' ; rc=1 ; }
else
  m_state=nowriter; rc=1
  echo "FAIL mirror legs: ./install-mirror-legs.sh is not executable -- the published-leg contract is not readable"
fi

echo "manifest gate: head=$sha dirty=$dirty manifest_oid=$moid rows=$rows rows_seen=$seen ok=$ok fail=$fail rowset=$rs_state unrostered=$rs_unrostered missing=$rs_missing mirror=$m_state mirror_ok=$m_ok mirror_drift=$m_drift mirror_skip=$m_skip rc=$rc"
exit $rc
