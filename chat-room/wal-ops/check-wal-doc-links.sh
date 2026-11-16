#!/usr/bin/env bash
# check-wal-doc-links.sh -- the wal-lifecycle.md copies must be LINKS, not copies.
#
# WHY (measured 2026-11-14, mechanism PROVEN 07:05 by controlled experiment): the doc
# is repo-canonical at chat-room/wal-ops/wal-lifecycle.md and each profile's skill
# reference is meant to be a symlink to it (commit 95ed257). There are TWO
# materialisers, success and failure, and they are not the same function:
#   SUCCESS materialises the file it writes. _guarded_write -> atomic_write_text
#   (utils.py:428, _atomic_write :379) is temp file + fsync + os.replace, so the
#   WRITTEN path gets a NEW inode on every successful patch/write_file and a link on
#   that path is BROKEN by the success. Refs no op touched keep their inode/nlink.
#   Measured 2026-11-15T16:42:33 ET (throwaway skill, outside hardlink): written
#   reference ino 14552820 nlink=2 -> ino 14552821 nlink=1 at 71 B, the outside name
#   still 14552820 holding the 30 B pre-image, `cmp` DIVERGED, SKILL.md untouched at
#   14552818. The earlier note here ("a SUCCESSFUL batch writes straight through the
#   link and leaves it intact") was FALSE for the written path and licensed skipping
#   the relink after exactly the edit this file's PROCEDURE says to relink; it is
#   withdrawn, not softened.
# skill_manage also REFUSES to write through a link that resolves outside the skill
# dir ("Path escapes allowed directory", patch and write_file both).
# The second materialiser is the ROLLBACK of a FAILED batch: ops applied before
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

# PROCEDURE -- this file states the rule it enforces; there is no second canonical.
#
#   edit -> relink-wal-docs.sh (this directory: it parses DOCS/PROFILES out of THIS
#   file and `ln -f`s every doc onto every profile leg, then re-runs THIS gate and
#   exits with its rc; `--check` grades without writing) -> check-manifest.sh ->
#   commit. Relink is a step of the edit, not a repair: an edit leaving nlink=1 is
#   incomplete. One call per doc edit, because two hand-typed `ln -f` lines is where
#   a half-repair happens -- done twice by hand on 2026-11-15, once by each agent.
#
#   IDENTITY IS A STEP TOO, and it is the one the link set cannot carry. `-ef` is
#   restored by the relink by construction and identical bytes keep md5, so a
#   whole-file rewrite grades 16/16 green (measured 2026-11-15 21:28). An edit that
#   appends through the name preserves (ino, btime) and needs nothing; an edit whose
#   TOOL replaced the inode must re-record -- `bash check-wal-doc-links.sh
#   --record-idents` -- in the same commit, with the writer named in the message.
#   Never re-record to silence a FAIL you cannot explain: the FAIL is the only arm
#   that sees the swap.
#
#   The copier in the FAILURE arm is the BATCH ROLLBACK. A SUCCESSFUL write materialises
#   the file it writes (atomic_write_text: temp + fsync + os.replace -> new inode, so the
#   link on THAT path is broken and the outside name keeps the pre-image; other refs keep
#   ino/nlink -- measured 2026-11-15T16:42:33 ET, ino 14552820 nlink=2 -> 14552821 nlink=1).
#   A FAILED batch materialises every file in the skill dir (copytree of the dir snapshot).
#   Relink is therefore part of every edit, success or failure -- never only after a fail.
#   On ANY failure inside an operations=[...] batch -- even one op, even a failed old_string
#   match, even an over-cap body refused pre-write -- `_restore_snapshot` in
#   `tools/skill_manager_batch.py` copytree's the whole
#   skill dir back: every reference gets a fresh inode at nlink=1 with identical bytes and its
#   own earlier mtime, including refs no op touched. Signature: birth new, mtime = that file's
#   earlier mtime, ctime on the failing call's second. Reproduced 2026-11-15T16:42:46 ET on a
#   throwaway skill: SKILL.md 14552818 -> 14552826 and probe-b 14552821 -> 14552825, both
#   nlink=1, mtimes preserved (16:42:28.755 / 16:42:33.444), ctime 16:42:46.30. So an arm's
#   half-life is the next EDIT of that doc (success breaks it) and, for every ref in the dir,
#   the next FAILED batch in that profile; identity comes from the log line, never the stat.
#     Detect (tool_executor.py:1561 logs error_preview = the payload truncated at 200 chars,
#     so a full log line tops out at 368 with its ~168-char prefix; `batch aborted` survives
#     only when the body is short -- measured 2026-11-15T16:43 ET: frodo 22 wrapper hits / 1
#     with `batch aborted`, buzzy 56 / 2, max payload 200 in both):
#       grep 'Tool skill_manage returned error.*operations\[' <profile>/logs/agent.log*
#   UNWINDOW -- a third defect class, in `tools/skill_manager_batch.py`: `_restore_snapshot`
#   (:158; :186 `aside = name + ".rollback-broken"`, :188 `post_dir.rename(aside)`, :190
#   `shutil.copytree(snap, pre_dir)`). NOT the module that owns the cap above -- :188 there is
#   the size validator, and a reader who takes these numbers to `skill_manager_tool.py`
#   concludes the window is imaginary. Between :188 and the end of that copytree the path holds
#   neither the old tree nor a complete new one. Writers do not race it (a batch holds
#   skill_file_lock, a cross-process flock), but LOCK-FREE READERS do -- skill_view, the curator
#   walk, and anything calling find_skill see THREE states, not one: (1) no directory at all;
#   (2) a directory with no SKILL.md yet, because copytree mkdir's first and fills after; (3) a
#   SKILL.md from the snapshot sitting beside a still-incomplete references/ set. States 2 and 3
#   are partial materialisation, and quoting the window as "a missing skill" understates it.
#   Measured 2026-11-16 on a throwaway skill through the running module's own _restore_snapshot
#   (one 64 MB file to hold the copy open): 378 samples, 55 distinct runs, 1 absent, refs
#   appearing 0/1/4/6/9/11/.../22 in a dir whose SKILL.md still raised FileNotFoundError, then
#   SKILL.md readable at 121 and 123 of 124 entries.
#   An fd opened BEFORE the arm is safe and stays pinned: :188 RENAMES, so the old inodes
#   survive under `.rollback-broken` until the success path rmtree's them, and a handle keeps
#   whatever it opened (measured: fd ino 16925852 read b'HALF-APPLIED-VALUE\n' after the call
#   while the path's new ino 16925859 read b'SNAPSHOT-VALUE\n'). Only PATH lookups fail -- the
#   same inode-vs-name distinction the success arm turns on, so it belongs in this sentence and
#   not as a caveat. Sub-second; a visibility hole, not a corruption path.
#   Durable fix: keep SKILL.md well under the 100,000-character cap. The cap is len(content)
#   in Python characters (skill_manager_tool.py:110 / :188), NOT bytes -- a file can read
#   100,010 bytes and still pass, so a byte-based margin panics 10 bytes early. Quote the rule
#   and the read, never a margin pair (a quoted margin was stale inside five minutes):
#     python3 -c "import sys;print(100000-len(open(sys.argv[1],encoding='utf-8').read()))" \
#       ~/.hermes/profiles/<p>/skills/agent-chat-bus/SKILL.md
#   At 2026-11-15T16:42 ET that read 748 ch (frodo) and 42 ch (buzzy) -- buzzy was one append
#   from the cap, where every later batch in that profile fails and re-materialises every ref.
#   A byte margin is NOT a char margin: the cap counts characters, so the two coincide only for a
#   pure-ASCII body (ch == B, and then 100,001 ch IS 100,001 B -- the cap alone decides). Any
#   non-ASCII punctuation makes B > ch, and a byte reading then UNDERSTATES the headroom by the
#   gap: measured frodo/SKILL.md 86,069 ch / 86,516 B, gap 447, at 2026-11-15T22:54 ET -- the same
#   file read 99,252 ch / 99,768 B, gap 516, in frodo's own 22:53:12 read, so margin AND gap are
#   per-instant. Subtract the gap before comparing a byte reading to the char cap; quote the char
#   read.
#
# SCOPE -- what this gate does NOT cover. Counted, not assumed:
#   repo 17 tracked `.md` (`git ls-files '*.md'`: 4 root + chat-room/wal-ops 13), DOCS 11 -> 22 arms.
#   The 4 root docs and the 2 wal-ops docs outside DOCS (README.md,
#   manifest-truncation-2026-11-14.md) have no reference in the frodo, buzzy, aragorn or sam
#   profile dirs, so they have no drift path. Everywhere else this gate is blind by
#   construction: names in a profile's references/ that are not in DOCS are neither canonical
#   here nor graded: 40 in frodo's dir, 37 in buzzy's, 69 distinct across both -- and the 8 DOCS
#   names are, measured 2026-11-15T22:54 ET, the ENTIRE intersection of the two dirs (`comm -12`
#   over the two sorted `ls` lists returns exactly those 8 names, nothing else). So the blind set
#   is the 61 names that exist on ONE leg only (32 frodo-only + 29 buzzy-only), never "69 against
#   8": an 8-of-69 phrasing inverts the claim, because the graded set is every name present on
#   BOTH legs and the uncovered set is every name present on one. Coverage is exhaustive for the
#   8 DOCS names x 2 profiles and for nothing else. Never restate it as "exhaustive for drift".
#   (Counts are per-leg and per-instant. frodo's dir read 39 at 22:53:12 (frodo's count) and 40 at
#   22:54:13 (this one) because references/room-turns-without-duplicates.md was born at
#   22:53:52.456 (btime; mtime .457, nlink 1, frodo leg only) -- 40 s after that count: both
#   honest, one leg, two instants. Earlier the same evening the pair moved 38->39, 35->37. The
#   cause of a delta is a claim about someone else's turn, so quote the leg and the instant with
#   the count and not where it went. A count in a comment decays silently -- re-count it in the
#   same edit, never carry it.)
#
# Docs covered. Every entry here is repo-canonical in chat-room/wal-ops/ and symlinked into
# each profile's references/. Add a doc here in the same commit that adds its symlinks, or the
# new doc drifts with no detector -- measured 2026-11-14: the carve of wal-forensics.md was
# proposed with this gate covering only wal-lifecycle.md, so the new file would have had two
# uncontrolled copies from birth.
DOCS="wal-lifecycle.md wal-forensics.md live-process-grading.md wal-census.md rotated-log-siblings.md copy-arm-semantics.md build-provenance-and-gate-arms.md provenance-quoting.md counts-rates-and-carriers.md gate-and-verdict-quoting.md gate-arms-and-false-greens.md"
PROFILES="frodo buzzy"
WALOPS=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)

# The last line is the verdict, built so that a quote of this gate carries its own
# evidence: head/dirty name the tree measured, ok/fail remove the need to count OK
# lines by hand, and the pair of oids names two different objects. docs_oid is the git
# blob oid of the DOCS LIST -- it pins the ROSTER (which filenames are in the
# contract) and cannot move when a doc's bytes move. docs_md5 is one digest over the
# (sha256, name) pairs, one per DOCS entry, in DOCS order  [order matters: the digest is printed that way] -- it pins the DOC SET's CONTENT and moves
# on any byte anywhere. Quote docs_oid for "the roster is what it was"; quote docs_md5
# for "the doc set is what it was". Neither one carries the other's claim.
rc=0
GATE_TMP=$(mktemp) || exit 2

# --- identity witness: ino + btime, recorded and refutable -------------------
# WHY (measured 2026-11-15T20:48 on 53a7c25, by the author of that commit): the two
# checks above CANNOT carry the claim live-process-grading.md makes -- "append through
# the second name -- ino and birth fixed, mtime and ctime move". `-ef` is restored by
# the relink by construction (ln -f puts all three names on the new inode) and md5 is
# unchanged when the rewrite produced identical bytes, so an inode SWAP grades 16/16
# green. Measured on the real pair: provenance-quoting.md ino 17748288 -> 17748465
# (btime 2026-11-15T20:48:01.001194516, 0.52 ms before its mtime -- a whole-file write
# to a new inode, not an append), live-process-grading.md -> ino 17748463 (btime
# 20:47:22.087819450), both nlink=3 again, 100253 B / 72109 B, gate ok=16 fail=0.
# Every recorded life of 17748288 ended there (aa5d4b2, 4064326 cite birth
# 17:15:02.245143572) and no gate said a word. A property held by discipline alone has
# the shelf life of a prose size.
#
# So identity is now a ROW, not a sentence: DOC-IDENTS.tsv records
# name<TAB>ino<TAB>btime<TAB>mtime per doc, the gate diffs live against it, and a moved
# inode or a new birth FAILS. The re-record is a tracked diff, so a whole-file rewrite
# costs one visible line and an in-place append costs none. ino alone would not do it --
# inode numbers recycle, so the pair (ino, btime) is the identity, and btime is the field
# that makes a recycled ino distinguishable from a surviving one.
#
# WHY THERE IS A FOURTH COLUMN (mtime), measured 2026-11-15T21:28:08 by frodo on this doc:
# provenance-quoting.md was rewritten whole by a preserved-mtime copy -- ino 17748465 ->
# 15479978, nlink back to 3, btime 21:28:08.095305184, mtime 20:48:01.001716243. Content
# identical (md5 = the committed blob), -ef green on all three names, md5 green. Every
# field agreed the file was untouched except one: mtime was 39 minutes OLDER than the
# file's birth. An (ino, btime) row flags that the file MOVED but cannot name what did
# it; a normal ext4 file cannot be modified before it exists, so `mtime < btime` is the
# signature of a restored-time copy, and it is only a signature if the row carries mtime.
# It is also the field that lets a reader check the write, not just the identity: an
# append through a second name keeps (ino, btime) and moves mtime.
# The timestamps are emitted as real ISO 8601 (YYYY-MM-DDTHH:MM:SS.fffffffff[+-]HHMM).
# The previous form used `tr ' ' 'T'` on stat's output and produced
# 2026-11-15T16:35:27.163383733T-0500 -- a string `date -d` refuses, so it could only ever
# be compared by byte order and never handed to a clock.
# PORTABILITY, because a correct gate on one filesystem is a false one on another:
# (ino, btime) is the identity pair on ext4, where an inode number is stable for the life of a
# mount. It is NOT a portable recipe. On the CIFS shares the kernel synthesises inode numbers per
# mount session: frodo's first production run of check-exec-copies.sh saw four files nothing had
# written renumber in one group, one direction -- 841874229/841874009/841873966/841874026 ->
# 844381460..844381463 -- with md5 and btime untouched on all four (2026-11-15). There ino is a
# COORDINATE, not an identity, and the pair is (md5, btime); a gate that FAILs on the number fails
# clean files, and the reader's repair is --record, the one act that destroys the witness.
# This gate is scoped to a LOCAL checkout and safe only there: WALOPS derives from
# `readlink -f $0` and both the recorded objects and the roster resolve under it, so they share
# one root -- either both local or both remote, never one of each. Do not port --record-idents
# onto a share; and note that the same self-rooting is why a rehearsal against a COPY of this
# tree writes the copy's DOC-IDENTS.tsv, measured 2026-11-15 (repo md5 6f4791ae / ino 17748506
# unchanged; the copy recorded its own c10a83a3). A roster path that does NOT share the recorded
# objects' root -- the shape check-exec-copies.sh has, with absolute NAS/live COPIES and one repo
# roster -- is the shape that needs an env override before it can be drilled.
#
# GRADE ONLY FROM THE CANONICAL CHECKOUT -- NEVER FROM A COPY OR A DETACHED WORKTREE.
# The identities arm grades the INSTALLED HARDLINK FARM: repo doc (ino, btime) against
# DOC-IDENTS.tsv, and every profile reference must be -ef with the repo doc. A detached
# worktree or a clean export of HEAD has fresh inodes at nlink=1 by construction, so it
# reads `FAIL identities/<doc>: IDENTITY REPLACED` for every doc while the canonical tree
# is green -- measured by frodo on a clean eb5d5a0 export: fail=24 on IDENTITY REPLACED
# with 16/16 real OK arms. That is the instrument describing its own copy, not 24 broken
# docs, and the fix is to grade where the farm lives. The MANIFEST gate IS safe on a
# detached worktree (its rows name blobs, not inodes); this gate is not, and its -ef and
# identity arms are how a reader tells which one was run.
IDENTS_FILE="$WALOPS/DOC-IDENTS.tsv"
# stat prints "YYYY-MM-DD HH:MM:SS.fffffffff +HHMM"; anything else (notably "-" for a
# filesystem with no birth time) is emitted as "-" rather than mangled into a timestamp.
stamp() { awk '{ if (NF == 3) print $1 "T" $2 $3; else print "-" }'; }
# ORDERING TWO STAMPS. A stamp is LOCAL time with a UTC offset, so a lexicographic
# compare orders WALL CLOCK, not time: identical offsets make the two agree, and a DST
# change makes them disagree. Measured 2026-11-16 (frodo, reproduced here before the
# repair): recorded 2026-11-01T01:15:00.000000000-0500 = epoch 1793513700, live
# 2026-11-01T01:30:00.000000000-0400 = epoch 1793511000. `[[ $live > $recorded ]]` reads
# TRUE while live is 2700 s EARLIER -- a real backdate straddling fall-back would print
# nothing, which is a false PASS in the one direction this gate exists to see. The
# spring-forward mirror is a false FAIL: noisy, safe, still wrong. epoch() PARSES the
# string stamp() renders (GNU date reads the trailing offset); it is not a second
# renderer, so precision and shape do not drift and stamp() stays the only formatter.
# Every caller must treat an empty result as UNMEASURED and say so: a compare that
# cannot be made is not a compare that passed.
epoch() { date -d "$1" +%s 2>/dev/null || true; }
idents_now() {
  for d in $DOCS; do
    r="$WALOPS/$d"
    if [ ! -f "$r" ]; then printf '%s\tmissing\tmissing\tmissing\n' "$d"; continue; fi
    i=$(stat -c %i "$r" 2>/dev/null || echo '?')
    b=$(stat -c %w "$r" 2>/dev/null | stamp)
    m=$(stat -c %y "$r" 2>/dev/null | stamp)
    printf '%s\t%s\t%s\t%s\n' "$d" "$i" "${b:--}" "${m:--}"
  done
}
if [ "${1:-}" = "--record-idents" ]; then
  idents_now > "$IDENTS_FILE.new" && mv -f "$IDENTS_FILE.new" "$IDENTS_FILE" \
    || { echo "FAIL: cannot write $IDENTS_FILE"; rc=2; }
  echo "recorded: $(wc -l < "$IDENTS_FILE") identity rows -> $IDENTS_FILE"
fi
exec 4>&1
{
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
} > "$GATE_TMP" 2>&1

# Grade the identity rows against the live inodes. Deliberately outside the block above:
# this compares the FILE to the tree, so it is the one check whose input is not the doc
# itself. Fail-closed on all three shapes -- moved, unrecorded, stale row -- because a
# witness you can choose not to record is not a witness.
declare -A R_INO=() R_BT=() R_MT=()
if [ -f "$IDENTS_FILE" ]; then
  while IFS=$'\t' read -r n i b m; do
    [ -n "${n:-}" ] || continue
    R_INO["$n"]="$i"; R_BT["$n"]="${b:-}"; R_MT["$n"]="${m:-}"
  done < "$IDENTS_FILE"
fi
for d in $DOCS; do
  if [ -z "${R_INO[$d]:-}" ]; then
    echo "FAIL identities/$d: no row in DOC-IDENTS.tsv -- an unrecorded inode is a claim nothing can refute; record it: bash check-wal-doc-links.sh --record-idents" >> "$GATE_TMP"
    rc=1
    continue
  fi
  if [ -f "$WALOPS/$d" ]; then
    li=$(stat -c %i "$WALOPS/$d"); lb=$(stat -c %w "$WALOPS/$d" 2>/dev/null | stamp)
    lm=$(stat -c %y "$WALOPS/$d" 2>/dev/null | stamp)
    if [ "${R_INO[$d]}" != "$li" ] || [ "${R_BT[$d]}" != "$lb" ]; then
      echo "FAIL identities/$d: IDENTITY REPLACED ino ${R_INO[$d]} -> $li, btime ${R_BT[$d]} -> ${lb} -- a recorded inode died; relink restores -ef and identical bytes keep md5, so this is the only check that sees a whole-file rewrite. If the rewrite was intended, re-record (one visible diff line) and name the writer in the commit: bash check-wal-doc-links.sh --record-idents" >> "$GATE_TMP"
      rc=1
    fi
    # mtime < btime cannot happen on a live file: a file is not modified before it exists.
    # It is the signature of a preserved-mtime copy -- bytes, -ef and md5 all green, and
    # the only field that says a write happened is this one. Ordered by epoch, not by
    # string: these two cells can carry different UTC offsets across a DST change (see
    # epoch()), and a wall-clock compare would read such a copy as green.
    if [ "$lm" != "-" ] && [ "$lb" != "-" ]; then
      lm_s=$(epoch "$lm"); lb_s=$(epoch "$lb")
      if [ -z "$lm_s" ] || [ -z "$lb_s" ]; then
        echo "FAIL identities/$d: CLOCK UNMEASURED -- mtime $lm / btime $lb did not parse to an epoch, so the restored-time and record arms could not be ordered and neither ran. Do not read this doc as green: re-record it or fix the cell: bash check-wal-doc-links.sh --record-idents" >> "$GATE_TMP"
        rc=1
      elif [ "$lm_s" -lt "$lb_s" ]; then
        echo "FAIL identities/$d: RESTORED-TIME COPY -- mtime $lm is OLDER than btime $lb, so these bytes were planted with someone else's clock. Content and -ef cannot see this; only this column can. Name the writer and re-record only if the replacement was intended: bash check-wal-doc-links.sh --record-idents" >> "$GATE_TMP"
        rc=1
      fi
    fi
    # The RECORD arm: the live mtime cell against the RECORDED one, DIRECTIONAL, and only
    # while ino and btime are unchanged. Distinct from the restored-time arm above and
    # deliberately not folded into it: that arm grades the file's own clock against its
    # btime, and btime survives a re-record, so it is the one arm a re-record cannot
    # launder. This arm grades the RECORD -- the cell in DOC-IDENTS.tsv -- and a re-record
    # clears it. Two records, two questions.
    # Why it is free: patch/write_file land via os.replace, so btime moves and the
    # identity arm above is already red on every ordinary edit; the re-record is already
    # mandatory, so comparing this cell adds no step to anyone's workflow. It closes the
    # one case nothing else sees -- a same-byte in-place rewrite (r+b/write,
    # dd conv=notrunc, a restore that keeps mtime out of it) or a bare utimensat: ino,
    # btime, md5 and -ef all stay green, so the write leaves no trace anywhere while the
    # column reads as coverage. A witness you can choose not to record is not a witness.
    # Both cells come out of stamp() (idents_now and --record-idents share it) and are
    # ORDERED by epoch, not by string: the two cells can be recorded on opposite sides of
    # a DST change, where the same instant carries a different UTC offset and a
    # lexicographic compare orders wall clock instead of time (measured example in
    # epoch()'s comment: a 2700 s backdate sorts FORWARD). Skips "-" (no-btime filesystem),
    # the same guard the restored-time arm carries, and FAILs UNMEASURED rather than
    # skipping when a cell will not parse -- a branch the tree does not enter is not an arm.
    # The FAIL prints both stamps: the repair is one visible diff line, not a silent
    # re-record.
    rmt=${R_MT[$d]:-}
    if [ "${R_INO[$d]}" = "$li" ] && [ "${R_BT[$d]}" = "$lb" ] \
       && [ -n "$rmt" ] && [ "$rmt" != "-" ] && [ "$lm" != "-" ] && [ "$rmt" != "$lm" ]; then
      rr=$(epoch "$rmt"); rl=$(epoch "$lm")
      if [ -z "$rr" ] || [ -z "$rl" ]; then
        echo "FAIL identities/$d: DIRECTION UNMEASURED -- the record cell $rmt or the live cell $lm did not parse to an epoch, so WRITTEN SINCE and MTIME MOVED BACKWARDS cannot be told apart and neither was printed. The mtime cell is graded by nothing until it is re-recorded: bash check-wal-doc-links.sh --record-idents" >> "$GATE_TMP"
      elif [ "$rl" -gt "$rr" ]; then
        echo "FAIL identities/$d: WRITTEN SINCE THE RECORD: mtime $rmt -> $lm, ino and btime unchanged; a same-byte in-place write or a utimensat is invisible to md5, -ef and the identity arm. Name the writer and re-record in this commit: bash check-wal-doc-links.sh --record-idents" >> "$GATE_TMP"
      else
        echo "FAIL identities/$d: MTIME MOVED BACKWARDS: $rmt -> $lm, ino and btime unchanged -- a clock set on a live inode. Content, -ef and btime cannot see it. Name the writer and re-record in this commit: bash check-wal-doc-links.sh --record-idents" >> "$GATE_TMP"
      fi
      rc=1
    fi
  fi
done
for n in "${!R_INO[@]}"; do
  case " $DOCS " in
    *" $n "*) ;;
    *) echo "FAIL identities/$n: stale row -- recorded but no longer in DOCS; a deleted doc keeps a row that grades nothing: bash check-wal-doc-links.sh --record-idents" >> "$GATE_TMP"; rc=1 ;;
  esac
done
ok_n=$(grep -c '^OK ' "$GATE_TMP")
fail_n=$(grep -c '^FAIL' "$GATE_TMP")
cat "$GATE_TMP" >&4
# head= carries THREE states, not two -- copied from run-gate-logged.sh:63-68 and its twin
# in check-manifest.sh. `rev-parse --short HEAD || echo no-git` put "no repo here" and "git
# ran and failed" in one token, so this verdict line could not be compared to the writer's
# head=none|unknown on the same tree. Measured 2026-11-16 (frodo): failing-git shim ->
# head=no-git, no-.git copy -> head=no-git, writer -> head=unknown and head=none. `none` =
# no repo here; `unknown` = git ran and failed.
if ! git --version >/dev/null 2>&1; then
  sha=unknown
elif ! git -C "$WALOPS" rev-parse --git-dir >/dev/null 2>&1; then
  sha=none
elif ! sha=$(git -C "$WALOPS" rev-parse --short HEAD 2>/dev/null); then
  sha=unknown
fi
# dirty= is 0, 1, or unknown -- see the same three-ending rule in check-manifest.sh: a
# failing git prints nothing, and emptiness is not a measurement of clean.
if ! DIRTY_RAW=$(git -C "$WALOPS" status --porcelain --untracked-files=no 2>/dev/null); then
  dirty=unknown
elif [ -n "$DIRTY_RAW" ]; then dirty=1
else dirty=0
fi
unset DIRTY_RAW
docs_n=$(printf '%s\n' $DOCS | wc -l)
docs_oid=$(printf '%s\n' $DOCS | git hash-object --stdin 2>/dev/null || echo no-git)
# docs_md5 is the CONTENT witness: sha256 over the (sha256, name) pairs in DOCS
# order, truncated to 16 hex. A missing doc contributes the token `missing` rather than
# being skipped, so a deletion moves this field too. Measured 2026-11-15: docs_oid
# printed 56364eaa at 6478004 and again at 9e978c5 while build-provenance-and-gate-arms.md
# went 59856f02 -> eaf982d1; docs_md5 read 8ddf5c8f9004b9bc -> 351e477e23521e83 across
# the same two heads. One of seven moved, the roster did not, and the field that can
# carry the move is this one.
docs_md5=$({ for d in $DOCS; do if [ -f "$WALOPS/$d" ]; then printf '%s  %s\n' "$(sha256sum "$WALOPS/$d" | awk '{print $1}')" "$d"; else printf 'missing  %s\n' "$d"; fi; done; } | sha256sum | awk '{print substr($1,1,16)}')
# idents is the IDENTITY witness: one digest over the live (name, ino, btime, mtime)
# quadruplets in DOCS order. docs_oid pins the roster, docs_md5 pins the bytes, idents
# pins the FILES -- three claims, and the third one is the only one a relink cannot
# restore. mtime is in the digest because the restored-time copy leaves every other
# field green (mtime < btime is its whole signature). Quote it with the head, and compare
# it to DOC-IDENTS.tsv at that head, never to a later stat.
idents=$({ idents_now; } | sha256sum | awk '{print substr($1,1,16)}')
echo "doc-links gate: head=$sha dirty=$dirty docs=$docs_n docs_oid=$docs_oid docs_md5=$docs_md5 idents=$idents ok=$ok_n fail=$fail_n rc=$rc" >&4
rm -f "$GATE_TMP"
exit $rc
