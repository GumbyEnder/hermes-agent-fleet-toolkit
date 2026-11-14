#!/usr/bin/env bash
# check-manifest.sh -- cwd-independent provenance gate for wal-ops.
#
# MANIFEST.md5 rows are bare basenames (and systemd/<name>), i.e. relative to
# this directory. `md5sum -c MANIFEST.md5` therefore passes only when the shell
# happens to be in wal-ops/ and reports "FAILED open or read" for every row from
# the repo root -- a gate whose verdict depends on cwd (measured 2026-11-14:
# 20/20 OK here, 0 OK from the repo root). Always run this wrapper, from
# anywhere: it cds to its own directory first.
set -u
cd "$(dirname "$(readlink -f "$0")")" || exit 2
md5sum -c MANIFEST.md5
