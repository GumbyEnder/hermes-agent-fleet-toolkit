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
echo "manifest gate: head=$sha dirty=$dirty manifest_oid=$moid rows=$rows rows_seen=$seen ok=$ok fail=$fail rc=$rc"
exit $rc
