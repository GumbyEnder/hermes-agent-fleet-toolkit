#!/usr/bin/env bash
# check-wal-doc-links.sh -- the wal-lifecycle.md copies must be LINKS, not copies.
#
# WHY (measured 2026-11-14): the doc is repo-canonical at
# chat-room/wal-ops/wal-lifecycle.md and each profile's skill reference is meant
# to be a symlink to it (commit 95ed257). But skill_manage applies its
# operations by rewriting every file in the skill directory, so ANY skill_manage
# call on agent-chat-bus silently materializes the symlink into a regular file --
# and the copy then drifts from the repo, which is the exact failure class the
# move existed to prevent. Caught twice, both times at the second of a
# skill_manage call: frodo 05:40:46.712 (skill_manage at 05:40:46) and buzzy
# 05:43:37.341 (skill_manage at 05:43:37).
#
# So: run this after every skill_manage on agent-chat-bus, and re-link on FAIL.
#   ln -sfn <repo>/chat-room/wal-ops/wal-lifecycle.md \
#           ~/.hermes/profiles/<p>/skills/agent-chat-bus/references/wal-lifecycle.md
#
# Exit 0 = every profile path is a symlink resolving to bytes identical to the
# repo file. Exit 1 = a path is a copy or points elsewhere. Exit 0 with a
# MISSING line means that profile simply has no such skill yet (not a fault).
set -u

REPO=$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)/chat-room/wal-ops/wal-lifecycle.md
[ -f "$REPO" ] || { echo "FAIL repo doc missing: $REPO"; exit 2; }
want=$(md5sum "$REPO" | awk '{print $1}')
echo "repo: $REPO  md5=$want"

rc=0
for p in frodo buzzy; do
  f="$HOME/.hermes/profiles/$p/skills/agent-chat-bus/references/wal-lifecycle.md"
  if [ ! -e "$f" ] && [ ! -L "$f" ]; then
    echo "MISSING $p ($f) -- profile has no agent-chat-bus reference; not a fault"
    continue
  fi
  if [ ! -L "$f" ]; then
    got=$(md5sum "$f" 2>/dev/null | awk '{print $1}')
    echo "FAIL $p: REGULAR FILE, not a symlink (md5=$got, repo=$want) -- it is a copy and will drift"
    rc=1
    continue
  fi
  tgt=$(readlink -f "$f") || { echo "FAIL $p: dangling symlink -> $(readlink "$f")"; rc=1; continue; }
  got=$(md5sum "$f" 2>/dev/null | awk '{print $1}')
  if [ "$tgt" != "$(readlink -f "$REPO")" ]; then
    echo "FAIL $p: symlink -> $tgt (not the repo doc)"; rc=1
  elif [ "$got" != "$want" ]; then
    echo "FAIL $p: symlink target bytes differ (md5=$got repo=$want)"; rc=1
  else
    echo "OK   $p: symlink -> $tgt  md5=$got"
  fi
done
exit $rc
