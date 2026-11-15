#!/usr/bin/env bash
# relink-wal-docs.sh -- repair the DOCS link set after ANY doc edit, then grade it.
#
# CANONICAL COPY: chat-room/wal-ops/relink-wal-docs.sh (tracked, MANIFEST row).
# frodo's ~/.hermes/profiles/frodo/scripts/relink-wal-docs.sh is hardlinked to this
# inode so the two paths cannot drift into two answers. Written by frodo 2026-11-15,
# promoted here so the repair the gate's PROCEDURE names is a versioned artifact
# instead of one agent's home-dir script.
#
# WHY: the repo copy of each DOCS doc is hardlinked into every profile's
# agent-chat-bus references/. Any tool that saves by writing a temp file and
# renaming it over the path (patch tools, most editors) REPLACES the inode, so the
# repo leg moves to a new inode while the profile legs stay on the old one: bytes
# diverge silently, `-ef` goes FALSE repo-vs-profile and stays TRUE
# profile-vs-profile, and only the gate notices. Re-measured 2026-11-15 twice in one
# turn on wal-forensics.md (repo ino 17747974 vs profiles 17747972).
#
# The repair is `ln -f <repo> <leg>` per doc per profile -- operand order is
# load-bearing (the SECOND operand ends up on the FIRST one's inode), and it is
# easy to skip one leg. This script does every doc x every profile in one pass and
# refuses to report success unless the gate agrees.
#
# Usage:  relink-wal-docs.sh            # relink + grade
#         relink-wal-docs.sh --check    # grade only, no writes
# Exit: gate's exit code (0 = every leg shares the repo inode).
set -uo pipefail

REPO="${REPO:-$HOME/Agents/Projects/hermes-agent-fleet-toolkit}"
GATE="$REPO/chat-room/wal-ops/check-wal-doc-links.sh"
WALOPS="$REPO/chat-room/wal-ops"
SKILL_REL="skills/agent-chat-bus/references"

[ -r "$GATE" ] || { echo "relink: no gate at $GATE" >&2; exit 2; }

# DOCS and PROFILES are the gate's published contract -- parse, never restate, or
# the two lists drift and a doc silently stops being graded.
DOCS=$(sed -n 's/^DOCS="\(.*\)".*/\1/p' "$GATE")
PROFILES=$(sed -n 's/^PROFILES="\(.*\)".*/\1/p' "$GATE")
[ -n "$DOCS" ] && [ -n "$PROFILES" ] || { echo "relink: cannot parse DOCS/PROFILES from gate" >&2; exit 2; }

if [ "${1:-}" != "--check" ]; then
  for d in $DOCS; do
    src="$WALOPS/$d"
    [ -f "$src" ] || { echo "relink: MISSING repo doc $src" >&2; continue; }
    for p in $PROFILES; do
      dst="$HOME/.hermes/profiles/$p/$SKILL_REL/$d"
      [ -d "$(dirname "$dst")" ] || continue   # profile not onboarded: nothing to repair
      ln -f "$src" "$dst" || echo "relink: FAILED $d -> $p" >&2
    done
  done
fi

# Grade with the gate itself; capture then echo, so rc is the gate's and not tail's.
out=$(bash "$GATE" 2>&1); rc=$?
printf '%s\n' "$out" | grep -E '^(FAIL|doc-links gate:)' || printf '%s\n' "$out"
echo "relink: gate rc=$rc"
exit $rc