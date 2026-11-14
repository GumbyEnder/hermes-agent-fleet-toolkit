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
# Exit 0 = every profile path resolves to the SAME INODE as the repo file (symlink
# or hardlink). Exit 1 = missing, or a copy: a byte-identical copy is the
# materialization signature and still FAILS, because it drifts at the next doc commit.
#
# Why hardlink is also accepted (measured 2026-11-14 by both agents): skill_view()
# REFUSES a supporting file whose symlink resolves outside the skill dir ("Path escapes
# allowed directory"), so a symlinked reference is unreadable through the tool that is
# meant to open it -- `read_file` on the repo path is the only way in. A hardlink has
# no link to resolve: the profile path IS the inode, skill_view reads it, and it cannot
# drift because there is still exactly one file. Same-fs required (`stat -c %d` equal);
# a tool that writes via temp+rename breaks the link, and `-ef` catches that as a FAIL.
# A doc listed in DOCS must be linked for every profile in PROFILES: a missing link is a FAIL,
# because DOCS is the contract this gate publishes. The old "profile has no skill yet" pass no
# longer applies -- both profiles carry agent-chat-bus.
set -u

# Docs covered. Every entry here is repo-canonical in chat-room/wal-ops/ and symlinked into
# each profile's references/. Add a doc here in the same commit that adds its symlinks, or the
# new doc drifts with no detector -- measured 2026-11-14: the carve of wal-forensics.md was
# proposed with this gate covering only wal-lifecycle.md, so the new file would have had two
# uncontrolled copies from birth.
DOCS="wal-lifecycle.md wal-forensics.md live-process-grading.md"
PROFILES="frodo buzzy"
WALOPS=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)

rc=0
for d in $DOCS; do
  REPO="$WALOPS/$d"
  if [ ! -f "$REPO" ]; then echo "FAIL repo doc missing: $REPO"; rc=2; continue; fi
  want=$(md5sum "$REPO" | awk '{print $1}')
  echo "repo: $REPO  md5=$want"
  for p in $PROFILES; do
    f="$HOME/.hermes/profiles/$p/skills/agent-chat-bus/references/$d"
    if [ ! -e "$f" ] && [ ! -L "$f" ]; then
      echo "FAIL $p/$d: MISSING $f -- the doc is declared in DOCS but has no reference link here"
      rc=1
      continue
    fi
    kind=link
    if [ -L "$f" ]; then
      tgt=$(readlink -f "$f") || { echo "FAIL $p/$d: dangling symlink -> $(readlink "$f")"; rc=1; continue; }
      if [ "$tgt" != "$(readlink -f "$REPO")" ]; then
        echo "FAIL $p/$d: symlink -> $tgt (not the repo doc)"; rc=1; continue
      fi
    elif [ "$f" -ef "$REPO" ]; then
      kind=hardlink
    fi
    # Identity is `-ef` (same device+inode), not md5: a symlink resolves to the repo
    # inode and a hardlink IS that inode, so both are one file by construction. A
    # byte-identical COPY is still a FAIL -- it is the materialization signature and
    # it drifts on the next doc commit.
    if [ "$f" -ef "$REPO" ]; then
      got=$(md5sum "$f" 2>/dev/null | awk '{print $1}')
      echo "OK   $p/$d: $kind -> $(readlink -f "$f")  md5=$got"
    else
      got=$(md5sum "$f" 2>/dev/null | awk '{print $1}')
      if [ "$got" = "$want" ]; then
        echo "FAIL $p/$d: byte-identical COPY, not a link (md5=$got == repo) -- it will drift at the next doc commit"
      else
        echo "FAIL $p/$d: COPY whose bytes already differ (md5=$got repo=$want)"
      fi
      rc=1
    fi
  done
done
exit $rc
