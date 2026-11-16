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

# head= carries THREE states, not two -- the same branch run-gate-logged.sh:63-68 carries,
# one layer up. `git rev-parse --short HEAD 2>/dev/null || echo no-git` collapsed "this is
# not a repo" and "git ran and failed" into one token, so the field a reader greps could
# not separate a mount/publish tree from a broken git -- while the WRITER on those same two
# trees prints head=none and head=unknown respectively. Three vocabularies for one field
# (writer none|unknown|sha, wrapper none|unknown|sha, gates no-git|sha) is a field nobody
# can compare across artifacts. Measured 2026-11-16 (frodo): gates printed head=no-git
# dirty=unknown on both a failing-git shim and a copied no-.git tree, and a grep for
# head=none over gate logs returned nothing ever. `none` = no repo here; `unknown` = git
# ran and failed (missing git, unborn HEAD, corrupt index).
if ! git --version >/dev/null 2>&1; then
  sha=unknown
elif ! git rev-parse --git-dir >/dev/null 2>&1; then
  sha=none
elif ! sha=$(git rev-parse --short HEAD 2>/dev/null); then
  sha=unknown
fi
# dirty= has three endings, not two: 0, 1, and unknown. The old one-liner read the
# EMPTINESS of `git status --porcelain` as clean, but a failing git also prints nothing --
# so "no index / no git" was reported as a measurement of clean (measured 2026-11-15 on a
# published-leg tree, frodo: same dirty tree -> dirty=1 with git, dirty=0 with git
# failing). `head=no-git` said so and `dirty=0` contradicted it on the same line, and a
# guard reads the number. The exit status decides now, never the emptiness.
if ! DIRTY_RAW=$(git status --porcelain --untracked-files=no 2>/dev/null); then
  dirty=unknown
elif [ -n "$DIRTY_RAW" ]; then dirty=1
else dirty=0
fi
unset DIRTY_RAW
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
META="MANIFEST.md5 README.md DOC-IDENTS.tsv EXEC-IDENTS.tsv"
# EXEC-IDENTS.tsv joins DOC-IDENTS.tsv as unrostered on purpose (2026-11-15): it is
# the roster check-exec-copies.sh diffs, and it is REWRITTEN by --record, so its own md5
# row would have to be regenerated by the action it witnesses (the same circularity).
# Its integrity leg is the git diff (a re-record is one visible line per copy) plus the
# exec-copies gate's FAIL on a swap it cannot explain.
# DOC-IDENTS.tsv is unrostered on purpose (2026-11-15): it is the identity witness
# check-wal-doc-links.sh diffs, and it is REWRITTEN on every legitimate re-record, so a
# row holding its md5 would be circular -- it would have to be regenerated by the very
# action it is meant to witness, and a green row count would then say nothing about the
# inodes. Its integrity leg is the git diff: a re-record is one tracked line, and an
# unrecorded swap is what the doc-links gate FAILS on.
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

# --- A FAILING ROW CARRIES A CLASS, AND THE CLASS NAMES THE ACTION ------------
# `md5sum -c` collapses two opposite situations into one `FAILED` token:
#   (a) the row no longer describes the COMMITTED file -- the roster is the artifact at
#       fault, or the commit landed content without its row: bounce the tree; and
#   (b) the working file was written seconds ago and the row's own update has not landed
#       yet: the tree is mid-edit -- wait, then re-measure. Bouncing (b) is a false red and
#       it is the expensive direction, because it lands a correction on a peer's in-flight work.
# NOTE 2026-11-16: this paragraph records a TRAP; it is not a gate reading. Both stamps
# predate /mnt/nas/host-ops/gate-logs/INDEX.tsv (first row 2026-11-15T17:23:41-0500), so
# neither 02:28 nor 02:34 has a .log leg. A verdict cites a gate line AND its leg.
# Pair-equality is as blind as pair-inequality: the 02:34 agreement (row == disk at
# 36,135 / ec592cee) was a WORK-IN-PROGRESS agreement, superseded five minutes later by
# 379e430's 36,441 / 3ff82bc3. Neither read is a state of the tree that ever existed at
# a commit boundary.
#
# The third hash is the discriminator, and it costs one `git cat-file` per FAILING row:
#   R = the row's md5 (MANIFEST.md5), D = md5 on disk, H = md5 of the blob at HEAD.
#   D == H, R != D  -> mismatch_row       the row is stale against a COMMIT   (bounce)
#   R == H, D != R  -> mismatch_worktree  row agrees with HEAD, disk is ahead (wait)
#   R != H, D != H  -> mismatch_both      neither matches HEAD                (bounce + re-derive)
#   no H (untracked, or git unavailable)   -> mismatch_unclass                (cannot classify)
# rc stays 1 for every one of them: a classification is not a green, it is a repair
# sentence. When the tree is not a repo the rowset assertion already reads nogit and the
# class tokens read 0 with `fail` still carrying the count.
mismatch_row=0; mismatch_worktree=0; mismatch_both=0; mismatch_unclass=0
failing_names=$(printf '%s\n' "$out" | grep -v '^md5sum: ' | grep -v ': OK$' \
                | sed -e 's/: FAILED open or read$//' -e 's/: FAILED$//')
if [ -n "$failing_names" ]; then
  while IFS= read -r f_row; do
    [ -n "$f_row" ] || continue
    row_h=$(awk -v f="$f_row" '{p=$2; sub(/^\.\//,"",p); if (p==f) {print $1; exit}}' MANIFEST.md5)
    disk_h=$(md5sum -- "$f_row" 2>/dev/null | awk '{print $1}')
    head_h=
    if [ -n "$row_h" ] && [ "$rs_state" = asserted ]; then
      head_h=$(git cat-file blob "HEAD:./$f_row" 2>/dev/null | md5sum | awk '{print $1}')
      # an empty blob hashes to d41d8cd9...; an unavailable path yields no bytes at all,
      # so treat the git-failure case as "no H" rather than as a real digest.
      git cat-file -e "HEAD:./$f_row" 2>/dev/null || head_h=
    fi
    coord="row=${row_h:--} disk=${disk_h:--} head=${head_h:--}"
    if [ -z "$disk_h" ]; then
      mismatch_unclass=$((mismatch_unclass+1))
      echo "FAIL row unreadable ($f_row): $coord; repair: restore the file -- a row it cannot open is neither stale nor in flight"
    elif [ -z "$head_h" ]; then
      mismatch_unclass=$((mismatch_unclass+1))
      echo "FAIL row unclassifiable ($f_row): $coord; repair: this file is not a blob at HEAD -- if it is new, git add it and restamp its row; if it was deleted in the worktree, restore it or drop the row"
    elif [ "$disk_h" = "$head_h" ]; then
      mismatch_row=$((mismatch_row+1))
      echo "FAIL row stale-against-commit ($f_row): $coord; the disk bytes ARE the committed bytes, so the ROW is the defect -- repair: restamp the row from $(git rev-parse --short HEAD), never 'revert the file'"
    elif [ "$row_h" = "$head_h" ]; then
      mismatch_worktree=$((mismatch_worktree+1))
      echo "FAIL row mid-edit ($f_row): $coord; the row agrees with HEAD, so the working file is an uncommitted edit -- action: WAIT for the writer to finish, then commit content+row together and re-run; do not bounce the artifact"
    else
      mismatch_both=$((mismatch_both+1))
      echo "FAIL row three-way drift ($f_row): $coord; neither the row nor the worktree matches HEAD -- repair: re-derive the row from the intended content and re-run before quoting rc"
    fi
  done <<< "$failing_names"
fi

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
#
# A LEG THE WRITER SKIPPED IS ALSO A FAIL. Measured 2026-11-15 ~10:41: with the NAS
# directory absent, `install-mirror-legs.sh --dry` printed `ok` for the local leg,
# `skip ... (dir absent)` x2 and exited 0, so this gate printed
# `mirror_ok=1 mirror_drift=0 mirror_skip=2 rc=0` -- green, over a witness leg nothing
# had measured. An absent leg directory is how a failed /mnt/nas mount presents, so it
# is the ordinary case, not an exotic one. Fixed in two places, because either alone
# can be reworded away: the writer now FAILs on an absent leg dir (rc=1) and only
# skips when explicitly told (--allow-absent / REQUIRE_LEGS=0, then rc=4), and this
# gate reads rc=4 or any `skip ` line as FAIL on its own. The gate also pins
# REQUIRE_LEGS=1 for the child so an inherited opt-out cannot green it.
#
# THE WRITER'S EXIT STATUS IS THE VERDICT; ITS SENTENCES ARE ONLY LABELS. Measured
# 2026-11-15 on a shadow tree: a tampered NAS leg produced `dry  would write ...` and
# the writer exited 0, so this branch was a grep for a phrase -- reword the phrase and
# the gate prints mirror_ok=3 over a stale leg. install-mirror-legs.sh --dry now exits
# 3 when it would write anything; this gate treats rc=3 as drift on its own, and only
# uses the text to say WHICH leg. A gate is a fact about the sentence it parses, so the
# parse never decides green alone.
# A VERDICT WITHOUT A COORDINATE IS NOT EVIDENCE. Measured 2026-11-15: this gate read
# `mirror_ok=4 mirror_drift=0` over /mnt/nas at 16:05, and an absent-leg drill run with
# NAS pointed at a scratch tree prints a line of exactly the same shape. So `mirror=`
# alone cannot be compared to another run's `mirror=` -- the drill that mints a
# trustworthy 4/0 is the one most likely to be quoted as if it were the share. The
# writer now names every leg root it reads (`root  <dir>`), and the verdict line
# carries them as mirror_root=<roots>. A writer that prints no root line gets
# mirror_root=unreported and rc=1: an unread coordinate cannot pass, for the same
# reason an unread contract cannot pass.
m_ok=0; m_drift=0; m_skip=0; m_state=measured; m_roots=unreported
m_journal=unreported; m_repairs=0; m_last=none; m_cause=-; m_pub=unreported
if [ -x ./install-mirror-legs.sh ]; then
  # REQUIRE_LEGS=1 pinned: an inherited REQUIRE_LEGS=0 is a rehearsal opt-out and must
  # not be able to turn this gate's absent-directory FAIL back into a silent skip.
  m_out=$(REQUIRE_LEGS=1 ./install-mirror-legs.sh --dry 2>&1); m_rc=$?
  m_roots=$(printf '%s\n' "$m_out" | sed -n 's/^root  //p' | paste -sd, -)
  [ -n "$m_roots" ] || m_roots=unreported
  # THE PUBLISHED COORDINATE. Only available on writer builds that print it (`published  <sha12|none>`).
  # It is what makes mirror_drift= readable beside head=: drift is an MD5 comparison of source bytes,
  # so head != published is the normal state (measured 2026-11-16: head=8965b9f vs
  # mirror_repair_last=b0ac1fce900a with mirror_drift=0, rc=0, every commit between them docs-only).
  # `unreported` when the writer is older than this gate, never `none` -- `none` is a state.
  m_pub=$(printf '%s\n' "$m_out" | sed -n 's/^published  //p' | tail -1)
  [ -n "$m_pub" ] || m_pub=unreported
  if [ "$m_roots" = unreported ]; then
    echo "FAIL mirror coordinate (writer printed no 'root  <dir>' line: the directory this run measured is unknown, so its verdict cannot be attributed to a share)"
    rc=1
    [ "$m_state" = measured ] && m_state=unrooted
  fi
  # THE REPAIR TOKEN. A writer that repairs a leg and records nothing makes a stale leg
  # and a healthy one print the same verdict: the FAIL disappears and only the repair's
  # absence-of-a-FAIL is left, which is not an artifact. So the journal path is a
  # coordinate like mirror_root -- no journal line is a FAIL -- and every repair line in
  # it is counted onto the verdict, with the head and leg hash of the last one. A repair
  # is a token on the line, never an inferred state.
  m_journal=$(printf '%s\n' "$m_out" | sed -n 's/^journal  //p' | head -1)
  [ -n "$m_journal" ] || m_journal=unreported
  if [ "$m_journal" = unreported ]; then
    echo "FAIL mirror journal (writer printed no 'journal  <path>' line: a repair it made would be unrecorded, so mirror_ok cannot distinguish repaired from never-stale)"
    rc=1
    [ "$m_state" = measured ] && m_state=unjournaled
  elif [ -f "$m_journal" ]; then
    m_repairs=$(grep -c '^repair ' "$m_journal" 2>/dev/null || true)
    m_repairs=${m_repairs:-0}
    m_last=$(grep '^repair ' "$m_journal" 2>/dev/null | tail -1 \
             | sed -n 's/.* head=\([^ ]*\).* new=\([^ ]*\).*/\1:\2/p')
    [ -n "$m_last" ] || m_last=none
  fi
  m_ok=$(printf '%s\n' "$m_out" | grep -c '^ok   ')
  m_drift=$(printf '%s\n' "$m_out" | grep -c '^dry  would write')
  m_skip=$(printf '%s\n' "$m_out" | grep -c '^skip ')
  m_absent=$(printf '%s\n' "$m_out" | grep -c '^FAIL absent')
  if [ "$m_absent" -gt 0 ]; then m_state=absent; fi
  if [ "$m_skip" -gt 0 ]; then
    printf '%s\n' "$m_out" | grep '^skip ' \
      | sed 's|^skip |FAIL mirror skip (published leg was NOT measured, dir absent): |; s|$|; repair: mount the leg root, then ./install-mirror-legs.sh|'
    m_state=skipped
    rc=1
  fi
  if [ "$m_drift" -gt 0 ]; then
    # The FAIL line carries the command that clears it. A gate that only says "stale"
    # makes the reader hunt for the writer; the repair is one path and it belongs on
    # the artifact, not in a doc.
    printf '%s\n' "$m_out" | grep '^dry  would write' \
      | sed 's|^dry  would write |FAIL mirror drift (published leg is stale): |; s|$|; repair: ./install-mirror-legs.sh|'
    rc=1
  fi
  if [ "$m_rc" -eq 3 ]; then
    # drift, whatever the wording: if the parse above found none, say so and still fail.
    [ "$m_drift" -gt 0 ] || {
      printf '%s\n' "$m_out"
      echo "FAIL mirror drift (writer rc=3: it would write a published leg; no parseable would-write line)"
      m_drift=1
      rc=1
    }
  elif [ "$m_rc" -eq 4 ]; then
    # a tolerated skip: the writer was told an absent leg is acceptable, but a leg it
    # did not measure is not a green gate.
    printf '%s\n' "$m_out"
    echo "FAIL mirror skip (writer rc=4: an absent leg directory was tolerated; one or more published legs were never measured)"
    m_state=skipped
    rc=1
  elif [ "$m_rc" -eq 5 ]; then
    # THE PUBLISH GUARD REFUSED, before any write: the sources on disk are not the
    # bytes at HEAD, or HEAD itself does not resolve. Nothing was measured about the
    # published legs, so no ok/drift/skip line exists to parse -- the writer's REFUSE
    # lines are the whole evidence. A reader who sees only "exited 5" runs the writer
    # again and gets the same refusal.
    #
    # THE REPAIR SENTENCE IS DERIVED FROM THE REFUSAL, NEVER FROM THE rc. Measured
    # 2026-11-16 (frodo): this branch printed one fixed sentence, "repair: commit the
    # wal-ops source", over all four causes -- and on a tree with no .git that sentence
    # is not merely incomplete, it is impossible: there is no commit to make. On an
    # UNTRACKED source it is half the repair (`git add` AND commit; a commit alone
    # publishes nothing), and the writer gives that case its own message ("does not
    # resolve") precisely because the repair differs. The cause token is carried on the
    # verdict line as mirror_refuse= so a grep for mirror=refused is not the only handle.
    # The tokens are head_none | head_unknown | untracked | edited, and `-` when nothing was
    # refused -- NOT `none`, which is what the head= field already means on a tree with no
    # repo. A cause field whose healthy default spells the same word as a state field is the
    # two-facts-one-token bug in the sentence that reports it (caught on this file's first
    # run: a green gate printed mirror_refuse=none beside head=1f94814).
    m_refuse=$(printf '%s\n' "$m_out" | grep '^REFUSE' || true)
    printf '%s\n' "$m_out" | grep '^REFUSE' \
      | sed 's|^REFUSE |FAIL mirror publish-refused (guard refused before any write): |'
    m_cause=unstated
    m_repair="read the REFUSE lines above: the guard refused before any write and named the cause"
    if grep -q '^REFUSE head=none' <<< "$m_refuse"; then
      m_cause=head_none
      m_repair="run this gate and the writer from a repo worktree (git rev-parse --git-dir must succeed); a copied tree or an export has no HEAD, so no commit exists to make -- committing cannot clear this"
    elif grep -q '^REFUSE head=unknown' <<< "$m_refuse"; then
      m_cause=head_unknown
      m_repair="make HEAD resolve in this tree (git rev-parse --verify HEAD) and re-run ./install-mirror-legs.sh; an unborn HEAD or a failing git is not fixed by committing the source"
    fi
    if grep -q 'does not resolve' <<< "$m_refuse"; then
      m_cause=untracked
      m_repair="git add the source(s) named above AND commit them, then ./install-mirror-legs.sh -- an untracked file needs both, a commit alone does not publish it"
    elif grep -q 'uncommitted edits' <<< "$m_refuse"; then
      m_cause=edited
      m_repair="commit the wal-ops source (the disk bytes are not the committed bytes), then ./install-mirror-legs.sh"
    elif grep -q '^REFUSE tree-behind' <<< "$m_refuse"; then
      # THE ANACHRONISM, REFUSED. A tree whose HEAD is an ancestor of the published head cannot
      # grade these legs at all: the comparison would measure the distance from this commit to the
      # published tip (a fact about the checkout, not about the commit), and a live run would roll a
      # published leg BACKWARD. This is a refusal, not a NOTE: a NOTE invites a reader to keep using
      # numbers that cannot mean what they say. Measured 2026-11-16 in a fresh worktree at eb5d5a0.
      m_cause=behind
      m_repair="grade and publish from the tree at the published head (mirror_published=$m_pub): this worktree's HEAD $sha is an ancestor of it, so the leg comparison measures the checkout, not the commit -- re-run there, or ALLOW_ROLLBACK=1 for a deliberate rollback"
    elif grep -q '^REFUSE tree-divergent' <<< "$m_refuse"; then
      # THE SIBLING THE ANCESTRY TEST MISSES. A branch off a common fork is neither behind
      # nor ahead, so `--is-ancestor HEAD PUB` is false and a guard built on that one question
      # publishes its bytes. Measured 2026-11-16 (buzzy, probe-divergent-publish.sh): the side
      # branch's grade-slot.sh was older than the published leg and the run printed drift, not
      # a refusal. Distinct token, because the repair is not "go to the tip" -- it is "this
      # tree is a different line of history; the legs are not yours to write yet".
      m_cause=divergent
      m_repair="rebase this branch onto the published head (mirror_published=$m_pub) so its leg sources descend from the published bytes, then re-run; a sibling branch is not ahead of the published legs, it is beside them, so ALLOW_ROLLBACK=1 is the only way to publish from here and it should be a decision, not a default"
    elif grep -q '^REFUSE tree-unresolvable' <<< "$m_refuse"; then
      # A COORDINATE THAT CANNOT BE CHECKED IS NOT A GREEN. The journal's newest row names a
      # commit this tree does not have, so ancestry is unanswerable; the first version skipped
      # the test silently and printed would-write lines (measured: journal head=deadbeef1234
      # -> published deadbeef1234, guard passed).
      m_cause=unresolvable
      m_repair="fetch the commit the journal names (mirror_published=$m_pub) into this tree, or grade from the tree that published it; the coordinate exists but is not reachable here, so no ancestry claim can be made about this checkout"
    fi
    echo "FAIL mirror legs: publish guard refused (rc=5) over mirror_root=$m_roots; repair: $m_repair"
    m_state=refused
    rc=1
  elif [ "$m_rc" -ne 0 ]; then
    if [ "$m_absent" -gt 0 ]; then
      printf '%s\n' "$m_out" | grep '^FAIL absent' \
        | sed 's|^FAIL absent |FAIL mirror absent (leg directory is not there, so the leg was never measured): |; s|$|; repair: mount the leg root, then ./install-mirror-legs.sh|'
    fi
    printf '%s\n' "$m_out" | grep '^FAIL' | grep -v '^FAIL absent'
    echo "FAIL mirror legs: ./install-mirror-legs.sh --dry exited $m_rc over mirror_root=$m_roots; repair: ./install-mirror-legs.sh"
    rc=1
  fi
else
  m_state=nowriter; rc=1
  echo "FAIL mirror legs: ./install-mirror-legs.sh is not executable -- the published-leg contract is not readable"
fi

echo "manifest gate: head=$sha dirty=$dirty manifest_oid=$moid rows=$rows rows_seen=$seen ok=$ok fail=$fail mismatch_row=$mismatch_row mismatch_worktree=$mismatch_worktree mismatch_both=$mismatch_both mismatch_unclass=$mismatch_unclass rowset=$rs_state unrostered=$rs_unrostered missing=$rs_missing mirror=$m_state mirror_refuse=$m_cause mirror_root=$m_roots mirror_published=$m_pub mirror_ok=$m_ok mirror_drift=$m_drift mirror_skip=$m_skip mirror_journal=$m_journal mirror_repairs=$m_repairs mirror_repair_last=$m_last rc=$rc"
exit $rc
