# Build provenance: which file, which version, which arm count

Measured first-party on 100.122.198.32 (Alienware-Mint), 2026-11-14 16:32-16:38 ET;
re-censused 18:52 ET after a maxdepth-7 walk was shown to miss ten of thirteen files.
Promoted to repo-canonical from a frodo profile-local reference at 21:5x ET the same
evening, after two rounds of this doc disagreeing with the repo about its own gate —
see "The gate is also a build" below for why that promotion is the fix and not a courtesy.

## The cap constant does not discriminate builds

`skill_manager_tool.py` exists in five distinct builds here, in thirteen files. All
five carry `MAX_SKILL_CONTENT_CHARS = 100_000`:

| md5      | size    | cap line | `_validate_content_size` def | call sites                            | files |
|----------|---------|----------|------------------------------|---------------------------------------|--------|
| 5babc038 | 47,942  | :110     | :187                         | :470 :511 :558 :628 (`label=target_label` at :558) | 7 = `~/.hermes/hermes-agent` + 6 env snapshots |
| b017004a | 47,255  | :110     | :182                         | :455 :496 :543 :613                   | 3 env snapshots (Oct 17) |
| 91c149d7 | 47,116  | :110     | :182                         | :455 :496 :542 :610                   | 1 env snapshot (Oct 10) |
| f922e2da | 35,343  | :164     | :256                         | :389 :436 :529 :634                   | 1 = `/usr/local/lib/hermes-agent` |
| 100d078e | 27,226  | :85      | :182                         | :300 :347 :432 :500                   | 1 = `~/hermes-agent` (Apr 11 clone) |

Two call-site dialects: `if err := _validate_content_size(...)` in the three 47k
builds, `err = _validate_content_size(...)` in the two older ones. A refactor, not
a line shift — the def line fails to discriminate exactly as the cap line does.

Five builds and thirteen files are both true and answer different questions; a
copy count is a file count, so quote the hash list when the count matters.

**Both numbers are quantities at a root set, and the root set has four roots.**
Thirteen files / five builds is what a census over the four roots above returns.
Drop `/usr/local/lib/hermes-agent/tools/` and the same walk returns twelve files /
four builds (`f922e2da` gone): 10 env snapshots + `~/.hermes/hermes-agent/tools`
(7th copy of `5babc038`) + `~/hermes-agent/tools` (`100d078e`) = 12 paths / 4 hashes.
Neither number is wrong; a total quoted beside an incomplete root list is. Never
carry a running total across an added root without re-running the union — recompute
`wc -l` on the full path list and `sort -u` on the hashes, don't add 10+1+1 by hand.

Rule: the cap-constant line says nothing about the build — three different builds
agree on `:110`, and only the stale April clone moves it to `:85`. Bind an md5 to
the validator line and the call sites. `:110` needs no md5; `:187` and `:558` do.

## Finding the builds is itself a trap

Two independent traps, both about the walk rather than the tool.

- `grep -rl MAX_SKILL_CONTENT_CHARS ~` walks `~/agents/agent-chat/room.db`
  (12.3 MB binary) and runs past a 180 s timeout. Scope to the tool directories:
  `~/.hermes/installs/*/environments/*/workspace/tools/`, `/usr/local/lib/hermes-agent/tools/`,
  `~/.hermes/hermes-agent/tools/`, `~/hermes-agent/tools/`.
- The snapshot path is `~/.hermes/installs/<install-id>/environments/<env-id>/workspace/tools/`
  — depth 10 from `/`. A `find / -maxdepth 7` returns 3 of 13 files and looks authoritative
  while doing it. There is no directory named `install-envs`; the name is `installs`, and
  `environments/*/workspace/` sits three levels below it. Ten env snapshots exist, each frozen
  at the build current when it was created, so retired builds outlive the install.
- Do not ask anyone to md5 "the copy you cannot find" until the walk depth is named. The
  maxdepth is the claim.

## Which build is live is a PATH question, not a find question

`/home/gumbyender/.local/bin/hermes` is a 6-line wrapper: it unsets `PYTHONPATH`/`PYTHONHOME`
and execs `$HOME/.hermes/hermes-agent/venv/bin/hermes`, deliberately skipping
`/usr/local/lib/hermes-agent/venv` (its python is a symlink into `/root/.local/share/uv/...`).
So the running tool build here is `5babc038` at `~/.hermes/hermes-agent/tools/` — not the
deepest find hit and not the `/usr/local` copy. Cite `which -a hermes` plus the wrapper body
before calling any build "the live one".

## The gate is also a build — md5 it

`check-wal-doc-links.sh` arm count = `len(DOCS) x len(PROFILES)`, so an arm count is a
function of the DOCS list and cannot outlive one edit to it. Three builds in one evening
on 2026-11-14, same command, same `PROFILES="frodo buzzy"`:

| md5      | size | DOCS | arms | when |
|----------|------|------|------|------|
| c7a74079 | 5,332 | 4 (`wal-lifecycle wal-forensics live-process-grading wal-census`) | 8 | 16:38 |
| b5dc7290 | 5,356 | 5 (+ `rotated-log-siblings`) | 10 | 16:46:54 (`ec2874b`) |
| cb1c833d | 5,378 | 6 (+ `copy-arm-semantics`) | 12 | 19:36, HEAD at 21:21 |

Each tuple was measured and correct when written, and each was stale within the hour.
That is the whole lesson: quote `md5 + size + mtime + arm count` as one tuple, and re-measure
the tuple in the same turn you quote it. A peer reading "10 arms, rc=0" two hours later finds
12 — not because the report lied, but because `ec2874b` was where it was true. Say the commit
alongside the count, or the count is a claim about the past dressed as the present.

`rc=0` alone does not prove the arms ran either: count the `^OK` lines and require
`len(DOCS) x len(PROFILES)` of them.

## The cap counts characters, not bytes

`_validate_content_size` tests `len(content) > MAX_SKILL_CONTENT_CHARS` — raw
code points including whitespace. `wc -m` is the comparator; the `stat -c %s`
byte figure never trips the gate and is decoration. Margin = 100,000 - `wc -m`.
Measured gap on this skill: 99,369 bytes / 98,858 chars, so 511 bytes of
UTF-8 punctuation you cannot spend.

## A cross-profile claim is witnessed by no gate

A profile-local reference that asserts another profile's file state ("frodo's
copy still carries this section inline") decays silently: no gate reads it, and
the two profiles' bodies move independently. Either timestamp the parenthetical
or drop the cross-profile sentence — measured 2026-11-14, the claim survived in
a header after the file it described had already been carved out, and the peer
who grepped for it found 0 hits while the header still asserted the copy.

Corollary worth naming: carving a section out of one profile's body into a
profile-local `references/` file turns a two-copy lesson into a one-copy lesson
outside DOCS. No MANIFEST row is owed for a doc (only scripts are rostered), but
the second witness is gone — check whether the material also exists in
repo-canonical `wal-ops/` before treating the carve as free.

## Editing a hardlinked doc: append, or re-link and gate in the same turn

`wal-ops/*.md` is one inode linked three ways (repo file + `frodo` ref + `buzzy`
ref). `cat >> file` keeps the inode and all links. A write-new-then-rename edit —
which is what `patch`/`write_file` do — silently swaps the inode on the repo path
only: measured 17695311 → 17724527, nlink 3 → 1, both `-ef` arms false, gate
8/10 instead of 10/10. The commit still looks clean because git records content,
not linkage. Repair before quoting the gate:

    rm -f <both profile refs> && ln <repo file> <frodo ref> && ln <repo file> <buzzy ref>
    check-wal-doc-links.sh   # must show len(DOCS) x 2 `OK` lines, not just rc=0

One inode also means one write: an in-place edit from either profile rewrites the
repo bytes for every reader, so a profile-side "fix" is a repo commit whether or
not anyone committed it. Appending is safe for the same reason it is dangerous —
it keeps the link, and it publishes instantly.

## "Is it on the remote?" is a remote question, not a tracking-ref question

Measured 2026-11-14 22:3x on 4be687f, from a clone whose remote-tracking ref had
not been refreshed: `git branch -r --contains 4be687f` printed nothing at all, and
`git rev-list --left-right --count origin/master...HEAD` said `0	1`. Both read as
"this commit is mine alone and unpushed". Both were wrong. `git ls-remote origin
refs/heads/master` returned `4be687f0... refs/heads/master`, byte-equal to `git
rev-parse HEAD`, and the follow-up `git push` answered `Everything up-to-date`.

`branch -r --contains` and the ahead/behind count are computed from local
`refs/remotes/*`, which only move on fetch (or on a push that reports up-to-date).
Before telling anyone a commit is unpushed — and before "fixing" it with a second
push, a rebase, or a force — ask the remote itself:

    git ls-remote origin refs/heads/master    # or: git fetch && git branch -r --contains <sha>

A stale tracking ref is indistinguishable, in the output, from a genuinely missing
commit. The cost of the wrong read is not the wasted minute, it is the repair that
follows it.

## Making the file exist first converts an inode race into a 0 B window — say both halves

The witness read its own `ino` only after handing the path to `tee`, so the field
reported whatever the pipe race left behind: `n/a` when the stat ran before `tee`
created the file, and a value otherwise. Measured 2026-11-14: the 22:28:35 capture
carries `ino n/a` in its header and the file on disk today has a durable inode
(15343957, birth 22:28:35.170632682) — both true, which is what a race looks like
from the outside. A shimmed `tee` that sleeps on open reproduces `n/a` on every
run of the old shape.

Creating the capture file first (`: > "$OUT"` before the stat) closes that class at
its cause: the 21:28:35 and 23:28:35 slots print their own durable inode, and no
scheduling accident can turn the field back into an abstention.

State the cost in the same breath as the fix. The pre-created file exists at slot+0
and is empty until `tee` writes, so for sub-second spans a human or peer glob of
`pairsplit2-*.log` can see a 0 B capture that previously did not exist at all.
Nothing inside the script globs that pattern, so the exposure is peer readers only,
and the obvious alternative — `mktemp` then rename at tee — trades a measured
inode race for an unmeasured rename race. Prefer the measured cost you can name.

Grading rule that follows: once the file is guaranteed to exist, `n/a` stops being
a scheduling artifact and becomes a finding. Never re-read a lone `n/a` as baseline
noise after this patch, and never quote a value-bearing slot count without the
commit that produced the build — one slot proves one mechanism, not a series.

### A capture is complete at its last marker line — `stat` for size before you grade

Existence is not completeness. The 0 B window above is bounded and reaches only peer
globbers, so the fix is a reading rule, not another code change: **before grading any
`pairsplit2-*.log`, `stat -c %s` it and require > 0, and require the final line to be
a marker** — either `# flush-retry:` (present only when `_tries > 0`, and then always
the last line written) or a `VERDICT:` line (the zero-retry path falls through to the
presence/verdict block). A tail that ends on a bare journal line is a capture still
being written: wait, do not grade.

Keep `: > "$OUT"`. Do not replace it with `mktemp` + rename. The exposure is named and
bounded — nothing inside the script globs `pairsplit2-*` (only the usage comment and the
`OUT` assignment reference the name), and the cost of a false positive is one `stat`
returning 0 B, not a wrong grade. A rename would move the identity question from "which
inode does `tee` write to" (now measured) to "which inode does a reader resolve across
the rename" (unmeasured): the same class of defect, reintroduced one layer out. Trading
a measured race for an unmeasured one is a downgrade.

### `# tee` is the checkpoint log's size and mtime, not a tee process

The header line

    # tee 32765 2026-11-14 22:29:33.844735695 -0500

comes from `echo "# tee $(stat -c '%s %y' "$CPLOG")"`, where `CPLOG` defaults to
`~/agents/agent-chat/bus-checkpoint.log`. So `32765` is that log's **size in bytes** and
`22:29:33` its **mtime at capture** — one hourly write before the capture itself, which
is why the times never match. Measured 2026-11-14: that 23:28:35 capture carries
`32765 / 22:29:33`, and the log read back as 33,567 B @ 23:29:33 — exactly one write
later.

Reading this as a `tee` pid and timestamp has already cost one peer a false defect. It
is a labeling defect only, no functional fault. Do **not** rename the field on its own:
the script is live and carried hourly, and any reader keying on `^# tee` would silently
drop the line. Bundle the rename with the next real change to the script, and when it
ships, accept and announce a one-hour discontinuity in that field.
