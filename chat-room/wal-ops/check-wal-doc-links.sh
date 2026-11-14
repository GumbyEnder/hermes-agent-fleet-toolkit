#!/usr/bin/env bash
# check-wal-doc-links.sh -- the wal-lifecycle.md copies must be LINKS, not copies.
#
# WHY (measured 2026-11-14, mechanism PROVEN 07:05 by controlled experiment): the doc
# is repo-canonical at chat-room/wal-ops/wal-lifecycle.md and each profile's skill
# reference is meant to be a symlink to it (commit 95ed257). The trigger is NOT
# "any skill_manage call" -- a SUCCESSFUL batch writes straight through the link and
# leaves it intact (verified: patch of SKILL.md with an outside link in references/,
# patch of an inside link -> link survived, .curator_ledger records both paths).
# skill_manage also REFUSES to write through a link that resolves outside the skill
# dir ("Path escapes allowed directory", patch and write_file both).
# The materialization happens on the ROLLBACK of a FAILED batch: ops applied before
# the failure are restored by writing the pre-image bytes with an atomic replace,
# which puts a regular file where the symlink was. Signature: bytes identical to the
# pre-image, mtime preserved from before the call, ctime on the failed call's second.
# Reproduced: op[0] patch on a linked reference + op[1] SKILL.md patch over the
# 100,000-char cap -> "batch aborted, all touched skills rolled back", reference now
# a regular file, mtime kept, ctime == call.
# The failure generator here is the SKILL.md 100K cap: the journal shows the three
# seconds that caught it -- 05:39:42, 05:40:46, 05:43:37 -- all skill_manage
# FAILURES ("SKILL.md content is 102,463 / 104,304 / 104,910 characters
# (limit: 100,000)"). So a skill whose SKILL.md is over the cap strands a link on
# every reference patch in the same batch.
#
# So: run this after every skill_manage on agent-chat-bus -- after FAILURES too, not
# just successes -- and re-link on FAIL. The durable fix is to keep SKILL.md under
# the cap; this gate is the detector, not the fix.
#
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
