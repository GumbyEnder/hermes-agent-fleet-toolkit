#!/usr/bin/env bash
# install-mirror-legs.sh -- write the published mirror legs from the repo, in one step.
#
# WHY THIS EXISTS (measured 2026-11-15, 09:38): commit 6158258 moved
# chat-room/wal-ops/grade-slot.sh (blob 9bbc8f3, md5 2513aee7), regenerated MANIFEST
# row 8 and installed ~/.local/bin/grade-slot.sh by cp+mv. The third leg,
# /mnt/nas/host-ops/agent-chat/grade-slot.sh, kept a1b524e9 (15,606 B) for 25 minutes
# while every gate read green: check-manifest hashes the repo worktree,
# check-grade-timer asks whether the timer ARMED, and nothing hashed either extra leg.
# The room had already cited the stale path as "installed at ..." while MANIFEST row 8
# had moved under it. A leg no gate reads has to be written by the same step that writes
# the roster, or it drifts on the first hurried install.
#
# WHY A PER-FILE TABLE, NOT ONE GLOBAL LEG LIST: the legs are not the same set.
# grade-slot.sh is exec'd from ~/.local/bin and CITED at the NAS path; the witness is
# exec'd from the NAS path only. A global list would invent a ~/.local/bin copy of the
# witness that nothing runs -- one more leg to drift.
#
# usage: install-mirror-legs.sh [--dry] [--allow-absent]
#   --dry           print the plan and the md5 each leg would get; write nothing
#   --allow-absent  tolerate a leg DIRECTORY that is absent, and report it as a skip
#                   (also `REQUIRE_LEGS=0` in the environment). Without it an absent
#                   directory is a FAIL.
#   ALLOW_ROLLBACK=1 (env only) publish from a tree BEHIND the published head, i.e. a
#                   deliberate rollback of a leg. Without it that is refused (rc=5).
# rc: 0 every published leg already matches the repo, or was written;
#     1 a write failed / no repo source / a leg directory is absent (default);
#     2 usage;
#     3 --dry found at least one leg it WOULD write (i.e. drift), nothing written;
#     4 --allow-absent tolerated an absent leg directory, so at least one leg was
#       never measured (nothing about that leg is known: not ok, not drift);
#     5 REFUSED before any write: HEAD is not a resolvable sha (no repo, or git ran
#       and failed), at least one source blob differs from its blob at HEAD, or this
#       tree's HEAD is an ANCESTOR of the published head (a historical checkout; the
#       write would roll a leg backward). Nothing was published. See THE PUBLISH
#       GUARD and THE ROLLBACK GUARD below.
# rc=3 exists because a --dry run that prints drift and exits 0 makes the caller's
# verdict a string match on this script's phrasing: reword "would write" and a gate
# that greps for it reads green over a stale leg (measured 2026-11-15 11:5x on a
# shadow tree: tampered NAS leg -> `dry  would write ...` and rc=0). The exit status
# is the fact; the text is only its label. Callers must OR rc with the text.
#
# WHY AN ABSENT LEG DIRECTORY IS A FAIL, NOT A SKIP (measured 2026-11-15 ~10:41):
# `NAS=<absent dir> ./install-mirror-legs.sh` printed `ok .../.local/bin/grade-slot.sh`,
# `skip ... (dir absent)` x2 and exited 0 -- "nothing published", successful exit. The
# absent-directory case is not exotic: the NAS leg is a mount, so on a box where it is
# not mounted the mount point is simply an empty directory (or absent) and the writer
# reports success having written nothing, leaving the exec'd witness stale. That is
# 6158258 re-armed at the write step, with a green return code on top of it. The skip
# line said so; rc -- the token a caller quotes -- did not.
#
# So the absent branch fails by default and the skip path has to be ASKED for
# (--allow-absent / REQUIRE_LEGS=0), which keeps the rehearsal arms able to arm it.
# A tolerated skip is still not green: it exits 4, because "I did not measure this leg"
# is a third state, not a pass and not drift. Two states, two rc, neither of them 0.
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")" || exit 2

DRY=0
ALLOW_ABSENT=0
case "${REQUIRE_LEGS:-1}" in 0|no|false|off) ALLOW_ABSENT=1 ;; esac
for a in "$@"; do
  case "$a" in
    --dry) DRY=1 ;;
    --allow-absent) ALLOW_ABSENT=1 ;;
    *) echo "usage: install-mirror-legs.sh [--dry] [--allow-absent]" >&2 ; exit 2 ;;
  esac
done
NAS=${NAS:-/mnt/nas/host-ops/agent-chat}
# run-gate-logged.sh sits one level ABOVE the agent-chat share root
# (/mnt/nas/host-ops/run-gate-logged.sh), because it wraps any gate in the repo, not
# just the agent-chat ones. Its leg root is therefore derived from NAS's parent rather
# than pinned to /mnt/nas/host-ops: pinning it would keep the wrapper leg on the real
# share while a rehearsal (NAS=<scratch tree>) moved every other leg, so an absent-leg
# drill would write the production dir -- the "trustworthy 4/0 for the wrong root" hazard
# this table exists to kill, re-armed. One knob (NAS) still moves every leg.
HOSTOPS=${HOSTOPS:-$(dirname "$NAS")}
# --- THE REPAIR JOURNAL -------------------------------------------------------
# A repair is a state change; a repair nobody can see is indistinguishable from a leg
# that never went stale. Measured 2026-11-15 (frodo, accepted): wiring this writer into
# the grade timer without an artifact turns a LOUD stale leg (rc=1, FAIL + repair line)
# into a SILENT green one -- the gate re-reads the repaired leg, prints mirror_ok=5, and
# nothing anywhere says a write happened. So every executed write appends one line to a
# journal that the gate reads and prints a count of:
#     repair ts=<utc> head=<sha12> dirty=<0|1|unknown> leg=<path> old=<md5|absent> new=<md5> root=<dir>
# head+dirty are the repo state AT THE WRITE, not at the read: that is the whole point.
# dirty=unknown means git could not answer (no index, or git missing): it is not clean and
# not dirty, and a guard that publishes must refuse it.
# The journal lives beside the legs ($NAS/.mirror-repairs.log) so the NAS knob moves it
# with everything else and a scratch drill never touches the production file.
# A failed append is rc=1 -- a write with no token is exactly the silent-green hazard.
JOURNAL=${MIRROR_JOURNAL:-$NAS/.mirror-repairs.log}
# head= distinguishes THREE states, not two: `none` = this tree is not a git repo,
# `unknown` = git ran and could not resolve HEAD (no commits, corrupt index, git
# missing), otherwise the sha. `git rev-parse HEAD 2>/dev/null || echo none` collapsed
# the first two into one token; they are different facts and the guard below refuses
# both for different reasons. Same rule as dirty=: the exit status decides, never the
# emptiness of stdout.
# The --version probe is what separates "not a repo" from "git itself is broken": a
# shim or a missing binary fails `rev-parse --git-dir` too, and reading that as `none`
# would blame the directory for the tool. Measured 2026-11-15 with a failing git shim.
if ! git --version >/dev/null 2>&1; then
  GIT_HEAD=unknown
elif ! git rev-parse --git-dir >/dev/null 2>&1; then
  GIT_HEAD=none
elif ! GIT_HEAD=$(git rev-parse --verify -q HEAD 2>/dev/null); then
  GIT_HEAD=unknown
fi
HOSTSHA=$(printf '%s' "$GIT_HEAD" | cut -c1-12)
# dirty= can say "unknown", and it must. Measured 2026-11-15 (frodo, independent worktree):
# the same tree with an uncommitted file printed `dirty=1` with git present and `dirty=0`
# with git failing, because a failing `git status --porcelain` prints NOTHING and empty
# output was read as clean. That is a coordinate reading as a measurement, and it lies in
# the one direction a guard trusts: a published leg (a file on a share or in ~/.local/bin)
# has no git index, so an hourly repair sourced from such a tree would journal `dirty=0`
# and pass a `refuse dirty>0` guard while publishing unreviewed bytes. `head=unknown` was
# the only tell and it sits one field from the number the guard reads, so the guard could
# not be written against it alone.
# So: the exit status of git decides, never the emptiness of its stdout. Any token other
# than `0` means "not measured as clean", and a guard is a PASS-LIST -- publish only when
# dirty=0 AND head!=unknown -- not a ceiling (`dirty>0`), which lets `unknown` through.
if ! HOSTDIRTY_RAW=$(git status --porcelain 2>/dev/null); then
  HOSTDIRTY=unknown
elif [ -n "$HOSTDIRTY_RAW" ]; then
  HOSTDIRTY=1
else
  HOSTDIRTY=0
fi
unset HOSTDIRTY_RAW

LEG_TABLE="
grade-slot.sh|$HOME/.local/bin:$NAS
run-gate-logged.sh|$HOSTOPS
witness-split-pair-v2.sh|$NAS
verify-slot-manifest.sh|$NAS
"
# verify-slot-manifest.sh is in this table because it IS the gate grade-slot
# executes (AGENT_CHAT_VERIFIER defaults to the NAS path) and it had no writer:
# until 2026-11-15 the NAS copy was maintained by hand, so an edit to the repo
# verifier changed no verdict until somebody remembered to copy it, and no gate
# hashed it. One leg, the NAS one -- ~/.local/bin carries no verifier, and
# inventing one would be a leg nothing runs.

# --- THE COORDINATE ----------------------------------------------------------
# Output from this script is quoted as evidence that a published leg was measured.
# Two runs over two different directories print byte-identical `ok`/`dry`/`skip`
# lines, so a reader cannot tell a scratch rehearsal from a run over the real share.
# Measured 2026-11-15: with NAS pointed at a scratch tree, an absent-leg drill and a
# green run over /mnt/nas produce the same shape of line; the verdict travels but the
# directory does not, and the drill is precisely the run that mints a trustworthy 4/0
# for the wrong root. So every run names the roots it is about to read, before it
# reads them, one line each in first-seen order:
#     root  <dir>
# `--dry` prints it too. A caller quoting a verdict without this line is quoting a
# number with no coordinate, and the gate treats a missing coordinate as a FAIL.
roots=$(printf '%s\n' "$LEG_TABLE" | sed '/^[[:space:]]*$/d' \
        | awk -F'|' '{print $2}' | tr ':' '\n' | awk 'NF && !seen[$0]++')
while IFS= read -r d; do
  [ -n "$d" ] && echo "root  $d"
done <<< "$roots"
# The journal is a second coordinate: it says WHERE the repair token would land, so the
# gate can read the same file this writer appends to instead of assuming a path.
echo "journal  $JOURNAL"
# --- THE PUBLISHED COORDINATE -------------------------------------------------
# `head=` says which tree is being graded. It does NOT say which commit the legs were
# last written from, and a reader who compares `mirror_drift` to `head=` will read a
# contradiction that is not there. Measured 2026-11-16 (frodo, live): head=8965b9f,
# mirror_repair_last=b0ac1fce900a, mirror_drift=0, rc=0 -- every commit since b0ac1fce
# was docs-only, so the legs still match their sources and there is nothing to write.
# `head != published` therefore never means stale; the drift is measured on MD5s of the
# source bytes, which are commit-independent. The newest `head=` in the journal IS the
# published coordinate, so print it beside `journal` and let both parties read one number.
# PUB_RAW keeps WHATEVER the newest row says, even when this writer cannot use it.
# The first version kept only a 12-char hex coordinate and dropped everything else on the
# floor, so a row written with a full 40-char sha -- or by a tool that spelled it
# differently -- read as `published none`, i.e. "first publish", and the ancestry test was
# skipped without a word. That is a field that grades health defaulting to safe, which is
# the bug this whole guard exists to remove (measured 2026-11-16: journal head=deadbeef1234
# -> `published deadbeef1234`, guard passed, would-write line printed).
PUB_HEAD=; PUB_RAW=
if [ -r "$JOURNAL" ] && [ -s "$JOURNAL" ]; then
  _last_pub=$(sed -n 's/.*[[:space:]]head=\([0-9a-f][0-9a-f]*\).*/\1/p' "$JOURNAL" | tail -n 1)
  case "$_last_pub" in
    ''|*[!0-9a-f]*) PUB_HEAD= ;;
    *) PUB_RAW=$_last_pub; [ "${#_last_pub}" = 12 ] && PUB_HEAD=$_last_pub ;;
  esac
  unset _last_pub
fi
echo "published  ${PUB_HEAD:-${PUB_RAW:-none}}"

# --- THE PUBLISH GUARD --------------------------------------------------------
# WHY NOT `refuse dirty!=0`: the writer runs from the repo worktree, which is normally
# dirty for reasons that have nothing to do with the legs (bus.py, wake_dispatcher.py
# are modified right now). A tree-clean guard refuses the repair that fixes the leg,
# and it leans on the field (dirty=) that can itself read `unknown`. So assert what
# "publishing the repo bytes" actually MEANS:
#
#     for each leg f:  git hash-object ./$f  ==  git rev-parse HEAD:./$f
#
# Pass-list, not ceiling -- publish only when HEAD resolves to a sha (never none,
# never unknown) AND every source blob equals its HEAD blob. That refuses when git
# fails (there is nothing to compare against), refuses an edited source (the disk
# bytes are not the committed bytes), and is indifferent to unrelated dirt (measured
# 2026-11-15: four sources equal their HEAD blobs at 5c0df4c -- grade-slot.sh
# 100c7c38, run-gate-logged.sh 8e4cacee, witness-split-pair-v2.sh 4a46ccc3,
# verify-slot-manifest.sh 0c3e8fea -- on a tree with three modified files outside
# wal-ops).
#
# IT REFUSES BEFORE THE FIRST WRITE. Version one of this guard would have been
# worthless if it checked and then wrote: a half-published leg set is the drift this
# script exists to remove. So the whole table is graded here, then the loop below is
# free of the question.
#
# rc=5 is a separate state from rc=1 (a leg is broken) and from rc=3 (a leg would be
# written): nothing was measured about the published legs at all, because the source
# was not vouched for.
guard_rc=0
if [ "$GIT_HEAD" = none ]; then
  echo "REFUSE head=none: this tree is not a git repo, so published bytes cannot be tied to a commit" >&2
  guard_rc=1
elif [ "$GIT_HEAD" = unknown ]; then
  echo "REFUSE head=unknown: git ran and could not resolve HEAD (no commits, or git failing), so published bytes cannot be tied to a commit" >&2
  guard_rc=1
else
  while IFS='|' read -r f legs; do
    [ -n "${f:-}" ] || continue
    src="./$f"
    [ -f "$src" ] || continue          # the loop below reports a missing source as rc=1
    disk_blob=$(git hash-object "$src" 2>/dev/null)
    if [ -z "$disk_blob" ]; then
      echo "REFUSE $f: git hash-object failed on $src -- disk bytes unreadable as a blob" >&2
      guard_rc=1; continue
    fi
    head_blob=$(git rev-parse --verify -q "HEAD:./$f" 2>/dev/null)
    if [ -z "$head_blob" ]; then
      echo "REFUSE $f: HEAD:./$f does not resolve -- the file is untracked or not in the commit, so the source is not a published byte" >&2
      guard_rc=1; continue
    fi
    if [ "$disk_blob" != "$head_blob" ]; then
      echo "REFUSE $f: disk blob $disk_blob != HEAD blob $head_blob -- the source has uncommitted edits; commit it, then publish" >&2
      guard_rc=1
    fi
  done < <(printf '%s\n' "$LEG_TABLE" | sed '/^[[:space:]]*$/d')
fi
# --- THE ROLLBACK GUARD: a tree BEHIND the published head must not publish ----------
# The blob guard above proves "the disk bytes are the committed bytes". It cannot see
# WHICH commit they are, so a clean checkout of an OLD commit passes it and publishes
# old bytes over newer legs -- a silent downgrade of a published leg, from a worktree
# that did nothing wrong. Measured 2026-11-16 (buzzy, in a fresh `git worktree` at
# eb5d5a0): the four gates reproduced byte-for-byte, including copies_idents, EXCEPT
# the mirror arm, which read mirror_drift=1 against a journal whose newest row was
# 104f7c1 -- a commit that postdates the graded one. So the arm was not measuring the
# health of eb5d5a0; it was measuring the distance from eb5d5a0 to the published tip,
# and its FAIL was an anachronism. Grading a historical commit with this arm open is
# not a defect in the commit, and it is not a NOTE either: a NOTE invites a reader to
# keep using numbers that cannot mean what they say. Reachability is the fact, not
# inequality (`head != published` is satisfied by every docs-only commit and by every
# healthy run -- see the published coordinate above), so ask git whether this HEAD is
# an ANCESTOR of the published head. If it is, this tree is older than the bytes it
# would write: refuse, before the first write, exactly like the blob guard.
# ALLOW_ROLLBACK=1 is the deliberate-rollback escape hatch and is the only one.
#
# REACHABILITY IS NOT `--is-ancestor HEAD PUB`. That one question catches a tree BEHIND
# the published head and misses its sibling: a DIVERGENT tree (two branches off a common
# fork) is neither an ancestor nor a descendant, so `is-ancestor HEAD PUB` is false and the
# run publishes. Measured 2026-11-16 on a two-branch scratch repo (probe:
# cache/scratch/probe-divergent-publish.sh): side=50809a3542d7, published=4f0a0e594c88,
# merge-base f582486ec2ed -- the guard printed `sources=4/4 match their HEAD blobs` and a
# `dry would write` line that moved grade-slot.sh ba231ec6 -> de1e3972, rc=3, no REFUSE.
# The side branch's instruction bytes were OLDER than the published leg and it wrote them.
# So ask the question in the direction that can be answered: only a tree that DESCENDS from
# the published coordinate (or IS it) is publishable. Sibling and behind are both "not a
# descendant" and both refuse, with distinct tokens so the repair sentence stays true.
PUB_SHA=; PUB_STATE=none
if [ -n "$PUB_RAW" ]; then
  if ! PUB_SHA=$(git rev-parse --verify -q "${PUB_RAW}^{commit}" 2>/dev/null); then
    PUB_STATE=unresolvable      # the journal names a commit this tree cannot see
  elif [ "$PUB_SHA" = "$GIT_HEAD" ]; then
    PUB_STATE=same
  elif git merge-base --is-ancestor "$PUB_SHA" "$GIT_HEAD" 2>/dev/null; then
    PUB_STATE=ahead             # this tree descends from the published bytes
  elif git merge-base --is-ancestor "$GIT_HEAD" "$PUB_SHA" 2>/dev/null; then
    PUB_STATE=behind
  else
    PUB_STATE=divergent
  fi
fi
if [ "$guard_rc" = 0 ] && [ "${ALLOW_ROLLBACK:-0}" != 1 ] \
   && [ "$PUB_STATE" != none ] && [ "$PUB_STATE" != same ] && [ "$PUB_STATE" != ahead ]; then
  case "$PUB_STATE" in
    unresolvable)
      echo "REFUSE tree-unresolvable: the journal's newest repair names head=$PUB_RAW but that commit is not in this tree (shallow clone, partial fetch, or a hand-written row), so ancestry cannot be checked and this writer will not guess that it is safe; fetch the published head or pass ALLOW_ROLLBACK=1 if the rollback is deliberate" >&2 ;;
    behind)
      echo "REFUSE tree-behind: HEAD $HOSTSHA is an ancestor of the published head $PUB_SHA (newest head= row in $JOURNAL) -- this tree is older than the legs it would write, so publishing would roll a published leg BACKWARD to bytes that commit already replaced; grade and publish at the tip (ALLOW_ROLLBACK=1 only for a deliberate rollback)" >&2 ;;
    divergent)
      echo "REFUSE tree-divergent: HEAD $HOSTSHA and the published head $PUB_SHA share a fork but neither descends from the other -- this tree's sources are not newer than the published legs, they are a different line of history, so publishing would overwrite them with bytes no published commit vouches for; rebase onto the published head, or pass ALLOW_ROLLBACK=1 if this is the branch that should own the legs" >&2 ;;
  esac
  guard_rc=1
fi
if [ "$guard_rc" != 0 ]; then
  echo "REFUSE nothing written (head=$GIT_HEAD); the published legs still carry whatever they carried before this run" >&2
  exit 5
fi
n_src=$(printf '%s\n' "$LEG_TABLE" | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' ')
echo "guard head=$HOSTSHA sources=$n_src/$n_src match their HEAD blobs"


rc=0
would=0
skipped=0
while IFS='|' read -r f legs; do
  [ -n "${f:-}" ] || continue
  src="./$f"
  if [ ! -f "$src" ]; then echo "FAIL no repo source: $src" >&2 ; rc=1 ; continue ; fi
  want=$(md5sum "$src" | awk '{print $1}')
  oldifs=$IFS; IFS=:
  for d in $legs; do
    IFS=$oldifs
    m="$d/$f"
    have=$(md5sum "$m" 2>/dev/null | awk '{print $1}')
    if [ "$have" = "$want" ]; then echo "ok   $m ($want)" ; continue ; fi
    if [ ! -d "$d" ]; then
      # Absent leg directory = FAIL by default (see the header): the NAS leg is a
      # mount, so "absent" is what a failed mount looks like, and reporting success
      # after writing nothing is how a stale exec'd witness survives a green gate.
      if [ "$ALLOW_ABSENT" = 1 ]; then
        echo "skip $m (dir absent, tolerated by --allow-absent/REQUIRE_LEGS=0)"
        skipped=$((skipped+1))
      else
        echo "FAIL absent $m (dir absent: $d -- a leg that was not measured is not an ok)" >&2
        rc=1
      fi
      continue
    fi
    if [ "$DRY" = 1 ]; then
      echo "dry  would write $m  ${have:-absent} -> $want"
      would=$((would+1))
      continue
    fi
    # cp to a temp name IN THE SAME DIRECTORY then mv: an open fd keeps the old inode,
    # so a unit already running this script finishes on coherent old bytes. Never rewrite
    # the destination in place (the in-place hazard is documented at the top of grade-slot.sh).
    tmp="$d/.$f.tmp-$$"
    if cp "$src" "$tmp" && mv -f "$tmp" "$m"; then
      newh=$(md5sum "$m" | awk '{print $1}')
      echo "wrote $m  ${have:-absent} -> $newh"
      # The token. If it cannot be written the repair is invisible, so the run fails:
      # fail-closed is the same rule as an absent leg dir, applied to the artifact.
      if ! printf 'repair ts=%s head=%s dirty=%s leg=%s old=%s new=%s root=%s\n' \
             "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$HOSTSHA" "$HOSTDIRTY" "$m" \
             "${have:-absent}" "$newh" "$d" >> "$JOURNAL"; then
        echo "FAIL journal $JOURNAL (leg was written but no repair token could be recorded)" >&2
        rc=1
      fi
    else
      echo "FAIL write $m" >&2 ; rm -f "$tmp" ; rc=1
    fi
    IFS=:
  done
  IFS=$oldifs
done < <(printf '%s\n' "$LEG_TABLE" | sed '/^[[:space:]]*$/d')
# A --dry run that would change a published leg exits 3. rc stays the writer's verdict
# whether or not the caller can parse the line above it.
# Precedence when several states hold at once: 1 (a leg is broken/unreadable) beats
# 3 (a leg would be written) beats 4 (a leg was tolerated absent). All three are
# non-zero; 0 is reserved for "every leg was measured and matches, or was just written".
if [ "$rc" = 0 ]; then
  if [ "$DRY" = 1 ] && [ "$would" -gt 0 ]; then
    rc=3
  elif [ "$skipped" -gt 0 ]; then
    rc=4
  fi
fi
exit $rc
