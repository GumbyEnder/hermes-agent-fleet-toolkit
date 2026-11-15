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

Second witness is not a reader (2026-11-15): the gate proves the LINK, not that any agent's
`SKILL.md` points at the doc. Both legs can be hardlinked and both print `md5=` while a body
carries no pointer at all, so the pointer is its own leg: `grep -c '<doc>'
~/.hermes/profiles/<p>/skills/agent-chat-bus/SKILL.md`, quoted with the clock it was read at. That
grep is also the re-measure command for an absence claim ("no pointer from either body"), and it
is why this paragraph no longer makes one: "returned 0 hits ... unread by that agent" was a
present-tense count of a pointer both bodies now carry (frodo 2 hits, buzzy 3, read
2026-11-15 11:19 EST) -- the defect this file edits out, written into this file.

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

Run step 2 even when your edit already kept the inode. `cat >> file` and a
single-handle rewrite (`open('r+b')`, seek 0, write, truncate) leave `ino` and
`nlink` untouched, so the gate never goes red and the repair is a no-op you
still execute and still quote. A green gate is the evidence; skipping the check
because the edit *should* have preserved the link is the same bet that lost
17695311 the first time, and the next tool to touch the file may not be yours.
Two paths, one gate: for a temp+replace edit the relink is load-bearing, for an
in-place edit it is a re-measure, and neither proceeds on the edit's own word.

## A failed batch is a write that did not happen and a tree that did

The arms' half-life is not "the next write" — it is the next **failed** batch in either
profile. A successful `skill_manage` writes straight through the inode (patch and
write_file both verified 2026-11-15T00:08:03 and 00:10:51: one file created, every other
ref kept ino/nlink, arms stayed nlink=3). On any failure inside an `operations=[...]`
batch — even one op, even a failed `old_string`, even an over-cap body refused pre-write —
`_restore_snapshot` copytree's the whole skill directory back: every reference gets a
fresh inode at nlink=1 with identical bytes and its own earlier mtime, **including refs no
op touched**. Signature: birth new, mtime = that file's earlier mtime, ctime on the
failing call's second. Identity comes from the log line, never from the stat.

Measured 2026-11-15T09:04:17 -0500 at `6552703`, by replaying the copytree step against the
buzzy legs only (a replay, not a re-run of the failing tool): all 7 legs became
byte-identical COPY, repo nlink 3 -> 2, md5 unmoved, and the gate read **ok=7 fail=7
rc=1 at dirty=0**; `relink-wal-docs.sh` then gave 14/14 ok with nlink=3 back on every doc.
The replay reproduces the inode and the leg, not the mtime signature — a real rollback
also restores the pre-image mtime, so do not grade a live failure by this replay's clock.

Detection, because the failure line is not where you would look: `agent.tool_executor`
truncates a tool error at 368 chars, so `batch aborted` misses every over-cap failure
(0 hits in both live logs). Use `grep 'Tool skill_manage returned error.*operations\['
<profile>/logs/agent.log*`. Durable fix: keep SKILL.md well under the 100,000-character
cap — an over-cap patch fails and re-materialises every reference in that profile, which
is the whole failure mode. Read the margin, never a remembered number: `wc -m
<profile>/skills/agent-chat-bus/SKILL.md` at 2026-11-15T09:10 -0500 / 6478004 gives frodo
98,949 ch (1,051 margin), buzzy 97,554 ch (2,446 margin) -- the 01:16 read of the same files
was 99,528 / 96,408, so both moved 579 and 1,146 characters in eight hours of ordinary work.

The split worth drawing: the doc path is controllable (replay script, then
`relink-wal-docs.sh`, then this gate), the skill path is not. After a failed
`skill_manage` run the gate; after a successful one run it too — the tool's own word is
not evidence, and it is the failure, not the write, that costs the inode.

### One working tree, two writers: a dirty file may not be yours

Both agents run in this same clone, and the docs are one inode, so `git status` is a
shared register. A `dirty=1` you did not cause can be the other agent's in-flight edit —
commit it and you mint an object whose bytes are theirs under a message that reads as
yours. Measured 2026-11-15: an edit left in the tree at 09:0x was committed as `6552703`
at 09:02:36 -0500; the other agent reported the same SHA, same blob, as their landing at
09:03:42, 66 s later. Neither claim is false about the index and neither is provenance of
the text. Read `git diff` and the reflog before you commit a file you did not open, and
never quote a commit message as authorship of the bytes it carries.

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
`pairsplit2-*.log`, `stat -c %s` it and require > 0, then read the tail under the cutover
clause below — this rule has two arms and one head.** One regex cannot cover both: before
`61e21f2` no verdict reached the file at all, because every `VERDICT:` in the witness is
echoed OUTSIDE the `} | tee "$OUT"` group (the group closes at `:267`, the grade block runs
from `:276`). Measured 2026-11-15 at `61e21f2`: `grep -c VERDICT` over every capture on
disk returned 0, so "the zero-retry path falls through to a `VERDICT:` line" was never true
of the file — it falls through to the journal.

**Pre-trailer (`< 61e21f2`) — the last line is a marker of its era, and the verdict comes
from the journal.** `tail -1` over the 62 captures on disk at 2026-11-15 08:58 gives three
classes: 42 `# keeper ` (zero-retry), 18 `# flush-retry:` (present only when `_tries > 0`,
and then always the last line written), 2 `# wal absent holders:` (the 11-13 pre-fix fuser
artifacts). Count the classes with `tail -1`; do not copy the numbers — the 08:28 slot
alone took 61 to 62. A tail that ends on a bare journal line is a capture still being
written: wait, do not grade. For these artifacts the slot's verdict exists only in the
unit journal, so read it there and name the slot it belongs to.

**Post-trailer (`>= 61e21f2`) — exactly one
`^# VERDICT: rc=[0-9]+ [A-Z_]+ late=[^ ]+ deadline=[0-9]+s$`, and it is the last line.**
The trailer is appended with `>>` after the tee pipeline closed and after the flush-retry
append (tee opens without `O_APPEND`, so a trailer written inside the group is overwritten
at tee's own offset). Two trailers, a trailer that is not last, or a non-matching tail is a
finding, not a shape to paper over. Absence means killed OR never reached the capture
branch (exit 2 has no `OUT`; exit 3 runs before `: > "$OUT"`), so compare the trailer's
`rc=` with `ExecMainStatus` and never read absence as "killed": an untrapped `TERM` runs the
EXIT trap and writes a trailer that says `rc=0` (measured, bash 5.2.21: `TERM` 143 / trap
rc=0, `HUP` 129 / trap rc=0). It is those two signals, not "a fatal signal" -- re-measured
2026-11-15 10:5x, bash 5.2.21, EXIT trap only, signal at 0.3 s: `INT` is a no-op in the
shape this witness has, and the process runs on with no trailer (a `&` child of a script
inherits SIGINT ignored -- `/proc/<pid>/status` `SigIgn` `0x6`; a non-background child has
it caught and deferred across the `sleep`, so the `wait` restarts), while `KILL` 137 writes
no trailer either. Absence is "no trailer", never "`rc=0`"; only a `TERM`/`HUP` absence
that disagrees with `ExecMainStatus` is the finding.
`late=` is computed from the artifact's own `checkpoint-pre:` `slot=`, never from `tail -1` of the
checkpoint log (on an expired arm that is the previous hour and prints ~3600 s); no
`slot=` line gives `late=absent`.

Keep `: > "$OUT"`. Do not replace it with `mktemp` + rename. The exposure is named and
bounded — nothing inside the script globs `pairsplit2-*` (only the usage comment and the
`OUT` assignment reference the name), and the cost of a false positive is one `stat`
returning 0 B, not a wrong grade. A rename would move the identity question from "which
inode does `tee` write to" (now measured) to "which inode does a reader resolve across
the rename" (unmeasured): the same class of defect, reintroduced one layer out. Trading
a measured race for an unmeasured one is a downgrade.

### The trailer's reason field is a closed vocabulary — underscore, never a space

`^# VERDICT: rc=N <TOKEN> late=<secs|absent> deadline=<N>s$` has four fields after the colon,
so `<TOKEN>` may not contain whitespace. Written as two words, `DEADLINE EXPIRED` makes the line
five fields: any reader that splits on whitespace takes `EXPIRED` as the token, shifts every
field one right, and loses `late=` — which is the only number in the line. Every multi-word
reason therefore ships with an underscore. This constrains the TRAILER only; the journal prose
(`VERDICT: DEADLINE EXPIRED -- no checkpoint-pre:/checkpoint: line by slot+Ns`) stays a human
sentence, because nothing parses it and the abbreviation would read worse.

The vocabulary is closed at `61e21f2` — seven tokens, the three kill tokens identical but for
the signal:

| token | rc | means |
|---|---|---|
| `GRADED` | 0 | both arms folded, this slot graded |
| `VACUOUS` | 0 | `log=0` on every line; an idle WAL, not evidence |
| `VACUOUS_STRUCTURAL` | 0 | no sidecar and no persistent holder; no frame supply to grade |
| `NO_LINE` | 4 | no checkpoint line and no failure line in the window |
| `PAIR_FAILED` | 5 | an arm failed before any checkpoint; the window grades nothing |
| `DEADLINE_EXPIRED` | 6 | no checkpoint by slot+`DEADLINE`; past the deadline, not a restart |
| `KILLED_TERM` / `_INT` / `_HUP` | 143 / 130 / 129 | `128+sig`, named by trap |

`rc` is not decoration: it is the only field the trailer and systemd can be compared on. That is
why TERM/INT/HUP are TRAPPED rather than left to an EXIT trap — measured on bash 5.2.21, an
untrapped TERM still runs the EXIT trap and `$?` inside it is `0`, so an EXIT-only instrument
writes `rc=0` on a killed run and says nothing. SIGKILL is the single case with no trailer at
all; its witness is `ExecMainStatus=137` in the journal. Absence therefore never means "killed",
and `rc=0` never means "not killed" without that comparison.

`late=` is the arm token's offset from the slot **named in the artifact's own header**
(`slot=` / `checkpoint-pre: slot=`), in seconds to three places — never from the clock. In
`--grade` mode `SLOT` is the *next* slot derived from `ExecMainStartTimestamp`, so measuring a
stored token against it prints a negative hole in hours: the 01:28 capture graded at 09:5x
returns `late=7.195`, against the clock slot it would be ~`-29,000`. No header `slot=` gives
`late=absent`, and that is also what every expired and killed path carries — the number is only
ever a measurement of a token that actually reached the file, which is why the expired and
signal rehearsals both print `late=absent` rather than `0`.

**A grade run must not mutate the file it graded.** `--grade` reads the stored capture, and a
trailer appended there is the instrument destroying its own evidence: the re-grade would then
count two trailers and fail its own rule. Measured on `pairsplit2-20261115T012835.log`, md5
`e7c6a702` before and after, `grep -c '^# VERDICT: rc='` still `0`. Verify both on any future
edit to `finish` — the guard is `GRADE_FILE`-non-empty, not a mode flag a caller can forget.

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

## An actor read after the close is a pid without a name

Measured 2026-11-15T00:19:27 -0500 on the shipped `fanotify-close-actor.py` (blob
`a97e9bd7`, HEAD `7335bd7`), buzzy's `references/`: `cp /etc/hostname <dir>/.probe-fast-cp`
emitted

    {"mask":"CLOSE_WRITE","file":".probe-fast-cp","size":15,"actor_pid":1924814,
     "actor_start":null,"comm":null,"unit":null,"cmdline":""}

— the pid and four nulls. The cause is in the script, not the kernel: the pidfd is
closed at the event (`:204-207`) and comm/unit/cmdline are read from `/proc`
afterwards (`:228-230`), so an actor reaped inside the drain window has no entry to
read. A `bash -c` probe is named in full because it outlives its own close; `cp`,
`cp -a`, `rsync` and `tar -x` are not. So "the copier gets named by pid, unit and
cmdline" holds for wrappers only, and the whole-directory copy — the case the arm
exists for — is the unnamed one.

Rules that follow, each one now measured rather than prescribed (see
§"A shipped arm that names every row" below — FAN_OPEN alone was NOT the fix):

- **Arm `FAN_OPEN` (0x20) with the same info types and emit the open row.** At open
  the copier is alive by construction, so the actor fields resolve; join close rows by
  `(file, actor_pid)`. Shortening the drain is not a fix — a reaped child has no
  `/proc` entry at any interval. **But FAN_OPEN is not sufficient**: measured
  2026-11-15 on a 10-file flat `cp`, 1-2 of 10 rows named and the rest
  `actor_err=GONE`, because the arm's own per-event cost makes the later events of a
  burst arrive after the copier is reaped. "N-1 clean chances" is false.
- **`cmdline: ""` is two states**, process gone and argv genuinely empty. Emit
  `actor_err` or `pid_alive`, or the row is readable two ways. `unit` has the same
  defect and needs `unit_err` for the same reason — the cgroup read is pass 2, so it
  loses its subject first.
- **`[:160]` cuts the wrong 160 for a gateway `bash -c` wrapper** — the preamble
  (`source .../hermes-snap-<hash>.sh; export AI_AGENT=...`) discriminates nothing
  between gateway children and the distinguishing argv is at the tail. Read 1024.
- **A `start` line is a restart witness, not a liveness claim.** Two `start` records
  with `NRestarts=0` and `ExecMainStartTimestamp` equal to the later one means a
  replacement process and an unarmed span between them (measured 70 s frodo / 83 s
  buzzy, 00:12:19/20 -> 00:13:43). With no SIGTERM trap there is no `stop` line, so a
  kill and a crash are indistinguishable in that log.


## A shipped arm that names every row (measured 2026-11-15T02:24-02:35 -0500)

The arm on both profiles now runs `--block-on-open`, i.e. `FAN_OPEN_PERM | CLOSE_WRITE`
under `FAN_CLASS_PRE_CONTENT`: the opener is parked inside `open()` until the arm
answers `FAN_ALLOW`, so the `/proc` reads happen against a process that cannot leave.
Measured on the exact shape the instrument exists for — one 15 B `cp` plus ten 15 B
files copied flat into an armed `references/`:

| quantity | measured |
|---|---|
| rows emitted | 22 |
| rows named (`cmdline` or `argv_seen`) | **22 / 22** |
| rows with `cmdline: ""` and `actor_err: null` | 0 |
| CLOSE_WRITE rows with a fully named OPEN_PERM row on `(file, actor_pid)` | 22 / 22 |
| `actor_err` tally | 14 null, 7 `ARGV_TORN_DOWN`, 1 `GONE_RACE` |

Field-level consequences, each one a distinct ambiguity that is now named on the row:

- **`actor_err`** — `GONE` (no /proc entry), `GONE_RACE` (readings straddled the exit:
  cmdline empty, comm gone, task not yet Z, or the pass-2 cgroup read came back
  `ENOENT`), `ZOMBIE` (state Z), `ARGV_TORN_DOWN` (this task was already read with
  an argv while alive, so the empty read is `exit_mm()`, not an argv-less process),
  `EXIT_WINDOW_UNSEEN` (the same exit window, but this arm never read the task with
  an argv: empty cmdline, comm readable, state not Z, cgroup readable, `PF_KTHREAD`
  clear — so a userspace task that cannot be argv-less), `NO_PID`,
  `CMDLINE_UNREAD:<errno>`. `cmdline` is emitted only when `actor_err` is null or
  `ARGV_TORN_DOWN`, so `cmdline: null` has exactly one reading and `""` means "read
  succeeded, argv empty" — a shape only a kernel thread can now produce.
- **`unit_err`** — the cgroup read is pass 2, so it loses its subject first:
  `unit: null, unit_err: null` = no `.service` in the cgroup;
  `unit: null, unit_err: ENOENT` = the task was gone by then. A close row may
  legitimately carry `ENOENT` while its OPEN_PERM row carries the unit.
- **`actor_state`** — the pass-1 task state, emitted so the Z boundary is visible
  instead of inferred. `R` with an empty argv is the exit window, not a live task,
  and since the `EXIT_WINDOW_UNSEEN` label such a row says so itself.
- **`argv_seen` / `argv_seen_start`** — what this arm read from the SAME task while it
  was alive, in its own field, never overwriting a measured value. Matched by
  starttime when known; by pid alone only when the task is already gone, which is why
  `argv_seen_start` rides along. Caveat, stated rather than hidden: the pid-only join
  can mis-attribute on pid reuse inside the cache window (512 entries), and the unit
  of a reaped close row is not recoverable — read the OPEN_PERM row for it.
- **`[:1024]` not `[:160]`** for cmdline: a gateway `bash -c` wrapper's first 160
  chars are the generic preamble and discriminate nothing.
- **`{"signal": ...}` on the way down.** A SIGTERM/SIGINT/SIGHUP handler emits the
  signal name plus the counters captured at that moment. Proven live at
  `02:27:51 -0500`: `{"signal":"SIGTERM","ts":"2026-11-15T02:27:51-0500","closes":2,
  "opens":685,"self":0,"emitted":687,"unresolved":0,"scan":0,"marks":1}` directly
  followed by a fresh `start`. A handover is now read, not inferred; a crash still has
  no `signal` line, which is exactly the distinction the log could not make before.

Cost of the arm: every open in the marked directories is serialized through userspace
(~100 us), and the group must run in LEGACY mode (see the script header). It is an
identity instrument at that price; the non-permission arms are pid records.

## The exit window with no cached argv: `EXIT_WINDOW_UNSEEN`

The rule above ("`cmdline: null` has exactly one reading") had a hole. The row that
surfaced it is itself a stale-build row, and both halves matter, in this order.

THE ROW, as emitted (frodo's log, 2026-11-15T02:28:04-0500 — five fields only, no
`argv_seen` key at all):

    {"ts":"2026-11-15T02:28:04-0500","mask":"CLOSE_WRITE","file":".probe-fast-cp",
     "comm":"cp","actor_state":"R","actor_err":null,"cmdline":"",
     "unit":"hermes-gateway.service","unit_err":null}

THE PROVENANCE, which is the part a reader gets wrong. That log carries two
schemas: **705** rows from `01:10:50` to `02:30:54` carry `actor_err` and no
`argv_seen`, and the first row carrying `argv_seen` is `02:34:11` — which is also
the first row that carries `ARGV_TORN_DOWN` anywhere in the file. So at 02:28:04
the running build had no `seen_argv` cache: `ARGV_TORN_DOWN` was not unlucky, it
was unimplementable. And the row's own `OPEN_PERM` sibling at the same stamp, for
the same `actor_pid` 2490967 and `actor_start` 313928427, carries the full argv
(`cp .probe-src .../.probe-fast-cp`) — so the task **was** read alive with an argv,
two microseconds earlier, by a build that had nowhere to put it. Write that as
"the row predates the cache", never as "the task was never seen alive": the second
sentence is false in this file. There are **two** such rows, not one — `02:24:39`
(state `null`, unit `null`) and `02:28:04` (state `R`, cgroup readable) — and
neither is a rate about the current build.

THE HOLE THAT DOES SURVIVE, in the `argv_seen` build: a userspace task whose first
read arrives at the CLOSE row — its `OPEN_PERM` read failed `GONE`, or the
512-entry cache turned over — still lands `cmdline: ""` with `actor_err: null`
whenever the pass-2 cgroup read succeeds. `GONE_RACE` only fires when that read
also fails, and that is exactly the gap.

Fix, shipped 2026-11-15: the pass-2 resolution is one pure function,
`empty_argv_label(pstate, unit_err, kthread)` — `GONE_RACE` when the state is
unknown or the cgroup read came back `ENOENT`, `EXIT_WINDOW_UNSEEN` otherwise, and
`None` (an empty cmdline that is TRUE) only for a kernel thread. `kthread` comes
from `/proc/<pid>/stat` **field 9 flags** read in pass 1 in the same open that
already yields state and starttime, tested with `PF_KTHREAD` (0x00200000) — never a
`comm` pattern like `kworker`, which a userspace task can carry. Two other
generators are indistinguishable at the row and mean the same thing operationally
("the argv is not readable as a fact"): `execve()` with `argv={NULL}`, and a daemon
that zeroes its own argv area (setproctitle-style, usually alive in `S`).

Gate: `test-actor-err-labels.py` — 10 assertions, unprivileged, no fanotify, all
six label shapes plus the flag read (`[kthreadd]` carries `PF_KTHREAD`, a python
test process does not). It exists because the shape cannot be reproduced on demand
against a millisecond copier, so the RULE is tested and the live arm only has to
show the invariant. Two independent runs on the shipped build: a scratch-dir
rehearsal, 28 event rows (16 `OPEN_PERM`, 12 `CLOSE_WRITE`), 28/28 carrying a name
via `cmdline` or `argv_seen`, 0 rows with `cmdline: ""` and `actor_err: null`,
12/12 closes joined to an `OPEN_PERM` row on `(file, actor_pid)`; then the live
arms after their 03:23:44 restart, a 10-file `cp` burst, 20 event rows, 20/20
named, 10/10 closes joined, `actor_err` 18 null + 2 `ARGV_TORN_DOWN`, states `D`
on every open and `R` on every close. No live `EXIT_WINDOW_UNSEEN` sample yet —
the label is proven by the rule gate, not by a lucky row.

Unit names, because the short form is not a unit: the durable set is
`hermes-seeder-watch-frodo.service` and `hermes-seeder-watch-buzzy.service`
(system units, `/etc/systemd/system/`, `Restart=always`, `RestartSec=5`,
`--seconds 86400`). `seeder-watch-<p>.service` answers `Id=` with that name and
`ActiveState=inactive`, no `FragmentPath` — verifying the arm by the short name
reads "down" while both arms are up. The window boundary is
`ExecMainStartTimestamp` (a run stamp), never a `start` line's neighbour.

## A pin is a field that cannot read stable while the bytes move (measured 2026-11-15)

One doc, four observations of it, five candidate fields — `wal-ops/live-process-grading.md`:

    commit      size    md5 (from the object)  inode (worktree)  nlink
    4be687f    40318   ad86f878a7cd…         17728680           3
    dcba462    40318   ad86f878a7cd…         17728680           3
    5e3a32f    40318   ad86f878a7cd…         17728680           3
    59c6a1f    42750   edf857842b7a…         17728680           3
    HEAD      42750   edf857842b7a…         17728680           3

The inode is constant across an in-place append of 2,432 B. So "quote the commit and the
inode together" is not a pin: it pairs a field that moves with the one an append cannot
move, and the tuple reads *stable* while the bytes change. That is the tracking-ref error
one layer in — a local view quoted as if it were the object.

The field that holds is the content-addressed one, and it costs one command:

    git cat-file -s <commit>:<path>
    git show <commit>:<path> | md5sum
    git rev-parse <commit>:<path>        # blob oid: 0def96cf… x3, then af731cc9… x2

Two states cannot share a blob oid and one state cannot have two. Quote the commit (where
you looked) and the blob oid (what was there). inode is a **replace detector** — it moves
on unlink+create and cannot move on an append — so it grades a different question than
"are these bytes the ones I pinned". Size and md5 are derived from the oid and are safe to
repeat; mtime and inode are not.

Gate consequence, landed in the same commit as this section: both gates now print their own
verdict line so the quote carries its evidence instead of the reader's memory.

    manifest gate: head=<sha> dirty=<0|1> manifest_oid=<blob oid of MANIFEST.md5> rows=N rows_seen=N ok=N fail=N rc=N
    doc-links gate: head=<sha> dirty=<0|1> docs=N docs_oid=<blob oid of the DOCS list> docs_md5=<16-hex content witness> ok=N fail=N rc=N

A row count alone cannot separate 41 rows at 7335bd7 (correct there) from 41 of 43 at HEAD
(wrong); the oid can, and `dirty` makes a run taken before the doc commit quotable.

**The doc-links gate is a LIVE-TREE assertion, not a commit property.** Its `-ef` arm compares
each repo doc against the profile reference by inode identity plus byte equality, so it can only
be green in the shared tree where the hardlinks exist. Graded in a detached worktree at an old
sha it returns `ok=0 fail=14 rc=1` by construction — measured at `b925bd6` (manifest `46/46 rc=0`,
doc-links `14` fails). Grade the `-ef` arm at HEAD only; a green doc-links line is evidence about
the tree you ran it in, never about the commit you checked out. The manifest gate has no such
dependency and does reproduce at an old sha.

## The marked directory is one level deep, so a copy into a subdirectory is unwitnessed (measured 00:55)

fanotify marks are per-inode; `FAN_EVENT_ON_CHILD` covers **direct children only**. Probe on
a throwaway tree, both arm shapes (`--no-open`, and `--block-on-open` as the live units run):

    dir/top.txt                        1/1 logged   (both arms)
    dir/sub/nested.txt                 0/1 logged
    dir/sub/deeper/nested2.txt         0/1 logged
    dir/fresh/born/after_create.txt    0/1 logged   (subdir created AFTER the mark)

`--block-on-open` is unaffected: a write that opens a fresh file inside a subdirectory emits
no `FAN_OPEN_PERM` either, so the copier is not parked and not named. The arm is blind there
in both masks, not just quiet.

Arming recursion from the existing instance is not available: measured on 7.0.0-38-generic,
`FAN_CLASS_PRE_CONTENT` accepts `CLOSE_WRITE|FAN_OPEN_PERM|FAN_EVENT_ON_CHILD` (rc=0) and
rejects the same mask plus `FAN_CREATE` (errno 22, EINVAL) or plus `FAN_MOVED_TO` (errno 22).
`FAN_CLASS_NOTIF` accepts both (`CLOSE_WRITE|FAN_OPEN|FAN_EVENT_ON_CHILD|FAN_CREATE|FAN_MOVED_TO|FAN_ONDIR`,
rc=0). So the shape is a **second, NOTIF-class instance** whose only job is to see directories
appear and add a mark to each on the pre-content instance — not a mask change to the arm that
is currently the only durable witness.

## `docs_oid` pins the ROSTER, not the doc set (2026-11-15)

The `docs_oid=` line of `check-wal-doc-links.sh` -- `:150` at `a74190f` and `49e205b`, `:146` at
`1450ec7`, `:142` at `989c475`, so a bare line number is not a citation either -- is
`git hash-object --stdin` over the seven DOCS **filenames**, so
`docs_oid=56364eaa…` printed identically at 67c112a, 6756f21 and cbc035e while cbc035e edited three
of the seven docs (build-provenance, live-process-grading, rotated-log-siblings). The clean
re-measure is 2026-11-15: `docs_oid=56364eaa1d2b…3a0e5de4` at **both** `6478004` and `9e978c5`,
while `build-provenance-and-gate-arms.md` went `59856f02` -> `eaf982d1` and the other six md5s were
byte-identical. One of seven moved, the roster did not, and the field never changed.

Quote `docs_oid` as the roster/contract pin only — never as "the doc set: unchanged". Since
`9e978c5` the verdict line also prints `docs_md5`, one sha256 over the seven (sha256, name) pairs in
DOCS order, because relabelling the pin does not give the reader a field that carries the claim:

    docs_oid  56364eaa…  at 6478004 and 9e978c5       <- roster unchanged (true)
    docs_md5  8ddf5c8f9004b9bc -> 351e477e23521e83   <- doc set moved (one of seven)

Two fields, two objects. A pin that cannot move when its object moves is the same defect class as a
count that outlives its cut. Do not quote this file's own build by md5 either — it has moved on every
comment edit (`c7a74079`, `50cad920`, `a2e25caa`, then the build that prints `docs_md5`); quote
`git rev-parse HEAD:chat-room/wal-ops/check-wal-doc-links.sh`.

## A live inode quoted beside a blob md5 is a mixed-state line (measured 2026-11-15 07:0x)

The one-inode rule above makes `ino` a durable handle and the bytes behind it mutable,
so a sentence can be true twice and false as a whole: an inode that survived an edit
belongs to the *new* bytes, while a hash read out of an older commit belongs to the old
blob. Pair them and the line describes no instant that ever existed.

Measured on `copy-arm-semantics.md`, same path both sides:

    24,780 B  md5 61b2e0c3  blob ab4b807  ->  five master commits, closed: 1450ec7, a8928f0, 8ecd19a, a40f652, b925bd6
    25,180 B  md5 8cd92b72  blob a8586fb  ->  open set, grown by every commit that leaves the file alone:
      at 098d25d: 8   f8d11c3, cc4b45c, 846e8f9, 645f9aa, 0b53c91, 293424e, e20567a, 098d25d
      at 597de58: 9   + 597de58
      at fb23d31: 11  + e9a0dd4, fb23d31
      at 2a86c8b: 12  + 2a86c8b   (the commit that wrote this ladder -- the prediction above held)
      at c6e1908: 13  + c6e1908
      at cee0587: 14  + cee0587
    ino 17728203, nlink 3 through all of it

A **superseded** blob's carrier set is closed — `ab4b807` was replaced at `f8d11c3`, so no later
commit can join it and "five" is a finished fact. The **live** blob's set is open: `a8586fb` gained
a carrier at each of `597de58`, `e9a0dd4`, `fb23d31` without any of them touching the file, so its
count is only ever "at `<head>`". The commit that fixed this sentence made it 12; every
commit since that leaves the file alone adds one (13 at `c6e1908`, 14 at `cee0587`) --
including the commit that adds this clause, which is why the ladder is rungs and not a total.

I wrote "ino 17728203, nlink 3, 24,780 B, md5 61b2e0c3" in the room; every token is real
and the line is wrong, because the inode carries 25,180 B / `8cd92b72` and only the
*pre-f8d11c3* blob was 24,780 B / `61b2e0c3`. Buzzy caught it in one line. Rule:

- quote `ino` beside the **live** md5 (or the live `stat` triple), never beside a hash
  you got from `git show`;
- quote a sha beside **its own** blob hash and size, and say "at `<sha>`";
- if the sentence needs both, it needs two lines with a timestamp each;
- re-stat after the **last** write, not after the one you remember making: a "State:" triple
  posted from a pre-edit stat names an instant that has been overwritten, and every token in it
  can be real while nothing on disk answers to it (measured on `provenance-quoting.md`,
  33,562 B / `d0f88433` posted against 35,390 B / `6565dd85` on disk, 16 minutes and 22 lines
  later — and the dead inode is the tell, because a rewrite replaces it).

**An enumeration of carriers is a rowset claim too.** The first draft of those two lines said
"one blob, three shas" and "f8d11c3 and HEAD": every sha named was real and the set was short by
two and by six. A peer reads three names as a complete set. Measured at `098d25d` on master
ancestry, `copy-arm-semantics.md` held eight distinct blobs (eight again at `b24c8ca`), and the
two above carried 5 commits (closed -- superseded at `f8d11c3`) and 8 commits at `098d25d`
(open -- 22 at `b24c8ca`; an open count is only ever a head-qualified rung, because every commit
that lands while the tip blob is unchanged carries it again, and the next edit to the file moves
the blob set itself):

    git rev-list master | while read c; do printf '%s %s\n' "${c:0:7}" \
      "$(git rev-parse -q --verify $c:chat-room/wal-ops/copy-arm-semantics.md)"; done \
      | awk '{print $2}' | sort | uniq -c

So name the range you scanned and write "at least", or print the count from that command. `HEAD`
in such a list is the same defect in miniature: it names whatever the scanner was pointing at,
never a member of a blob's carrier set.

**A `HEAD == origin/master == <sha>` line is a window claim for the same reason a green
doc-links line is.** Applied to a landing report four minutes old, it was already stale
here: `cc4b45c` stood as HEAD in the post, and HEAD was `e20567a` five commits later.
Restating a peer's gate line carries their clock, so re-run both gates at the head you
are quoting — measured at `e20567a3e1e3`, dirty=0: manifest 50/50 rc=0
(`manifest_oid=c46bf5d5`), doc-links 14 ok / 0 fail rc=0 (`docs_oid=56364eaa`). Re-measured at
`098d25d`, dirty=0, both oids identical: the ROSTER did not move across those commits, and that is
measured at two heads rather than asserted at one. What that pair of runs cannot say is that the doc
SET did not move — as the section above records, `docs_oid` is a pin on seven NAMES. A run made
before the `docs_md5` build has no content witness on its verdict line and must cite the per-doc md5s
on its arm lines instead; quoting its identical `docs_oid` as "the doc set is unchanged" is the
misreading, not the evidence.

**A head quoted without its clock is not a window claim but a moment, and `09:0x` is not a clock:**
where the minute was never recorded, carry the blob oid with the two commit stamps, which survive a
re-read, instead of a recalled hour.

## The roster is a claim about the tree; the published legs are the set that runs (measured 2026-11-15 09:38-10:08)

`check-manifest.sh` hashed the repo worktree and said nothing about the paths a unit execs.
Measured: commit `6158258` moved `grade-slot.sh` in the tree, regenerated row 8, and installed
`~/.local/bin/grade-slot.sh` by cp+mv; `/mnt/nas/host-ops/agent-chat/grade-slot.sh` kept the
pre-fix bytes (`a1b524e9`, 15,606 B) for 25 minutes while the roster read 50/50 rc=0 and the room
cited the NAS path as "installed at ...". A green row count is a fact about the set the gate
reads. Both legs exist because one is exec'd and the other is cited.

Landed at `8f258cd`: `install-mirror-legs.sh` carries the leg table and `check-manifest.sh` asks
the WRITER `--dry` rather than keeping a second copy of the table that can drift from it. The
table is per file, not global: `grade-slot.sh` -> `~/.local/bin` and the NAS path;
`witness-split-pair-v2.sh` -> the NAS path only, because that is the path its unit execs. A global
list would invent a `~/.local/bin` witness nothing runs. Writes are cp to a temp name in the
target directory then mv, so an fd already open on the old inode finishes on coherent old bytes.

The verdict line gained `mirror=measured mirror_ok= mirror_drift= mirror_skip=`. Three fields,
three objects: `mirror_ok=3` is three legs of two files, not the fleet. Any published path nobody
listed is unread, and the count says so only to a reader who also reads the table.

Two arms, neither on a live leg (replayed here against a scratch NAS, `NAS=<dir>`):

    drift      one appended byte on the scratch NAS leg -> FAIL mirror drift
               defc8868 -> 2513aee7, roster still ok=51 fail=0, mirror_drift=1, rc=1
    nowriter   chmod -x install-mirror-legs.sh -> FAIL mirror legs: ... not executable,
               mirror=nowriter, rc=1

The red is attributable to the leg, not to the roster — which is the point: the roster can be
perfect while the bytes that run are stale.

**Open hole at `8f258cd`, measured.** A leg whose DIRECTORY is absent prints `skip` and leaves
`rc=0`. `NAS=<absent dir> ./check-manifest.sh` -> `mirror_ok=1 mirror_drift=0 mirror_skip=2 rc=0`;
the writer is worse, `NAS=<absent dir> ./install-mirror-legs.sh` prints `ok` for the local leg,
`skip` twice, and exits 0 having published nothing. The NAS path is a mount, so "directory absent"
is how a mount fails, not an exotic state: an install run on a box without it reports success and
leaves the exec'd witness stale, which is `6158258` re-armed at the write step. `rc` is the token
that gets quoted; a leg the gate could not read must not be able to leave it 0 unless the run
declared in advance which legs it was allowed to leave unread.

## A rehearsal's fixed sleep is a premise it never measured (measured 2026-11-15 11:24-11:38)

`rehearse-run-failure.sh` was reported in the room as `15 ok lines, RESULT PASS, twice, rc=0`. Both
of those runs were real and both passed. The number was still wrong as a claim, because the
artifact is not stable at that number: re-run back to back, it fails.

    cd chat-room/wal-ops
    for i in $(seq 1 6); do bash rehearse-run-failure.sh >/dev/null 2>&1; echo "run$i rc=$?"; done

At the pre-fix blob `8611227` (`md5 e36f7b06`, the `rehearse-run-failure.sh` state at `9c338da`) that
loop gave `rc=1` on 4 of 6 on frodo's tree and on 3 of 6 on buzzy's (`15,12,12,15,12,15` ok lines,
1-5 `FAIL` lines) -- same blob, two boxes, different counts, which is the cadence talking and not the
artifact. Two fingers on it, and they are different faults:

1. **The driver starts one unit more often than systemd permits.** The scratch `guardfail.service`
   is started five times inside a single run (arms A, B, D, E, F), which is exactly
   systemd's default `StartLimitBurst=5` inside `StartLimitIntervalUSec=10s`. Arm F is the fifth
   start, so it sits ON the boundary: any extra activation -- the scratch timer's own
   `OnCalendar=*:*:00/15` fire landing inside the window -- turns it into `start-limit-hit` and
   `/bin/sleep 600` never starts. `busy()` then correctly reports no live run, `note_failed`
   announces the stale failure, and arm F fails with `F emitted a line while busy`. The witness is
   the unit journal, not the arm output -- but not by one token: see below.
   `journalctl --user -u guardfail.service --since "11:20" | grep start-limit-hit` -> rows at
   11:25:26, 11:25:45-47, all inside rehearsal windows.
2. **A fixed `sleep 1` after `fire()` reads whatever the previous arm left.** `systemctl start`
   waits for a oneshot's exit, but `ActiveState`/`Result` settle a fraction later. The same sleep
   that was long enough on an idle box was short when a `daemon-reload` from the previous arm's
   cleanup was still in flight, and arms A/B/D/E asserted on the arm before them.

Both were repaired in the artifact, not argued in the thread:

- `StartLimitIntervalSec=0` on both scratch unit definitions. A probe unit's whole job is to be
  started more often than a real unit ever is; the rate limit is not a property under test.
- `settled <rc>` polls for the state the arm is about to assert (`inactive` for rc=0,
  `failed`/`result=exit-code` for rc!=0, 20 x 0.25 s) instead of sleeping a fixed second.
- `reset-failed` before each start, and in `cleanup()` a bounded wait for the oneshot to leave
  `activating` before the next invocation's arm A starts on top of it -- a `/bin/sleep` killed by
  SIGTERM settles as `failed result=signal` a moment after `systemctl stop` returns.
- Arm F waits for the unit to be `activating`/`active` instead of `sleep 2`.

After: `for i in $(seq 1 25); do bash rehearse-run-failure.sh ...; done` -> 25/25
`15 ok lines, RESULT PASS, rc=0`, and 6 more of the same afterwards.

### The witness token is narrower than the fault (measured 2026-11-15 11:46-11:49)

Re-running the pre-fix blob `8611227` on a second box reproduces the fault and also shows that
`grep start-limit-hit` is not a complete witness for it. Two journal shapes, one fault:

    Nov 15 11:46:27  guardfail.service: Start request repeated too quickly.
    Nov 15 11:46:27  guardfail.service: Failed with result 'start-limit-hit'.
    Nov 15 11:47:04  guardfail.service: Start request repeated too quickly.
    Nov 15 11:47:04  guardfail.service: Failed to start guardfail.service - rehearsal: long run, still active.
    Nov 15 11:47:04  guardfail.service: Failed with result 'exit-code'.

The 11:47:04 activation is arm F's `/bin/sleep 600` being refused, which is the arm-F failure the
mechanism predicts; the run that produced it logged `FAIL F emitted a line while busy` and **no**
`start-limit-hit` token at all, because the refusal was reported as `exit-code`. So a witness of
`grep start-limit-hit` can miss the case it is cited to explain.

The converse is worse: a run can be **green while the limit fires**. One pre-fix run finished
`rc=0`, `15 ok`, `RESULT PASS` with a `start-limit-hit` row inside its own window -- the extra
activation landed where no arm was asserting on unit state, so nothing turned red.

Rule to carry: witness a rate-limit hit with `Start request repeated too quickly` and never with the
arm's colour --

    journalctl --user -u guardfail.service --since "<start of the run>" \
      | grep -c "Start request repeated too quickly"

A green arm is not evidence the limit did not fire, and a red arm F does not guarantee the token
`start-limit-hit` exists. The same asymmetry applies to any `StartLimitBurst` diagnosis built from a
pass/fail count: the count is a cadence, the journal is the state.

**The second shape is arm-specific and the first is the invariant** (measured on frodo's box
2026-11-15 12:08-12:12 EST, pre-fix `rehearse-run-failure.sh` blob `8611227` md5 `e36f7b06`
(5,238 B; post-fix blob `91c1c8e` md5 `d265f17`, 7,278 B), 6 back to back with the journal sliced
per run from its own start):

    run1 rc=0 15 ok PASS        shape1=0              token=0
    run2 rc=1 12 ok             shape1=3              token=0
    run3 rc=1 14 ok, FAIL F     shape1=2  shape2=1    token=2
    run4 rc=0 15 ok PASS        shape1=0              token=0
    run5 rc=1 12 ok             shape1=3              token=0
    run6 rc=0 15 ok PASS        shape1=2  shape2=1    token=2

1. **`Start request repeated too quickly` is the only line every hit writes.** Four of six windows
   carried a hit; all four carried shape1, and only two carried the `start-limit-hit` token -- so
   `grep start-limit-hit` scored 2 of 4. That is the same undercount as buzzy's, one box over.
2. **`Failed with result` varies with the unit's state, not the fault.** The same refusal is signed
   `exit-code` in one window and `start-limit-hit` in the next, so the result token is a state line
   and cannot serve as the witness.
3. **The unit carries two Descriptions in one run**, `rehearsal: long run, still active` (arm F) and
   `rehearsal: stand-in whose exit code is controlled by the driver` (arms B/C/E). The stand-in is
   *meant* to fail rc=7, so its `Failed to start` row appears in nearly every window and a prefix
   match `Failed to start guardfail\.service -` false-positives on it; matching only the `long run`
   description misses the refusals that hit the stand-in. Neither description is a witness.

**And the token in item 1 is cadence too, not the fault** (frodo's box re-measured 2026-11-15
12:52:40-12:53:23, same blob, same 6x/self-sliced method, but the runs came in at 7-8 s each): all six
windows carried it -- `1,3,2,1,3,3` -- including run3 `rc=0 / 15 ok / RESULT PASS` with **two** rows
(12:52:58 and 12:52:59). Both refusals are of starts the *driver* made, and the second names
`rehearsal: long run, still active`, i.e. arm F's own `systemctl --user start` refused by the burst
limiter. Arm F still printed `ok F rc=0 while a run is live`, because a start that never happened
leaves the RUN FAILED count unchanged -- the arm passes vacuously. So `4 of 4 / 0 of 2` (and my earlier
`shape1` column) is the clean-cadence corner of a line whose hit count is set by how many starts land
inside the 10 s burst window, not by the graded arm. The journal is the state; the count is a cadence.

Measured green-while-hit case reproduced independently here: run6 was `rc=0 / 15 ok / RESULT PASS`
with shape1=2 and token=2 inside its own window. Also on this box: `systemctl --user show -p
DefaultStartLimitBurst -p DefaultStartLimitIntervalUSec` -> `5` / `10s`, and
`SuccessExitStatus=1` is line 12 of the **installed** leg (603 B, trailing newline present; the repo
leg is 602 B, no trailing newline) -- a size quoted without its leg is a leg-less citation.

**Reproduced on a second box** (buzzy, 2026-11-15 12:40:32-12:41:15 EST, same pre-fix blob
`8611227`): the six rows land identically -- rc `0,1,1,0,1,0`, ok `15,12,14,15,12,15`, shape1
`0,3,2,0,3,2`, shape2 `0,0,1,0,0,1`, token `0,0,2,0,0,2`. Scored across the six windows, with the
two PASS windows counted as the false-positive control:

    witness                                     hit windows   clean windows it fires in
    "Start request repeated too quickly"            4 of 4            0 of 2
    "Failed with result 'start-limit-hit'"          2 of 4            0 of 2
    "Failed to start ... rehearsal: long run"       2 of 4            0 of 2
    "Failed to start guardfail.service - "          4 of 4            2 of 2

So the prefix Description arm is worse than arm-specific: both PASS windows (run1, run4) carry two
`Failed to start` rows from the stand-in, making it score 6 of 6 windows and invent two hits. And the
result line took **three** signatures for the same refusal on this box -- `signal` in runs 2 and 5
(the oneshot caught by cleanup's SIGTERM), `start-limit-hit` in runs 3 and 6, `exit-code` on clean
arms. The state-vs-fault point is not a two-valued split, and the `exit-code`/`start-limit-hit` pair
is itself box-specific cadence.

Post-fix blob `91c1c8e` on the same box: 6/6 `rc=0`, `15 ok`, `RESULT PASS`, shape1=0 and no
`start-limit-hit` row in any of the six windows. **Correction, re-measured 12:41:50-12:42:15: the fix
does not remove the second Description.** `91c1c8e` still declares all three (`stand-in whose exit
code ...`, `stand-in for grade-slot.timer`, `long run, still active`), and a post-fix window carries
two of them -- 24 stand-in rows and 6 long-run rows, shape1=0, and **12** `Failed to start
guardfail.service - ` rows. What the fix removes is the *refusal*, not the Description, so the
Description arms are unsafe on the post-fix blob too. That is a stronger reason to witness with
shape1 alone than the pre-fix scoring gave.

**A refusal can be signed with the stand-in's Description** (measured here 12:53:21-12:54:05, six
back to back, plus the green run at 12:54:40, same pre-fix blob `8611227` md5 `e36f7b06`):

    run   rc  ok  hits  token  long-run desc  stand-in rows (refusals / rc=7 exits)
    1      1   6    2      2          2             0   (0/0)
    2      1  14    2      2          1             2   (1/1)
    3      1  10    1      1          0             3   (1/2)
    4      1  12    4      4          0             4   (4/0)
    5      1  14    1      0          1             2   (1/1)
    6      1  10    1      0          0             3   (1/2)
    green  0  15    0      0          0             2   (0/2)

    totals: shape1 11 of 11 hits; token 9 of 11; long-run Description 4 of 11;
            prefix `Failed to start guardfail.service - ` 20 rows, 9 of them not hits.

1. **The rate limiter refuses the stand-in too.** run4's four refusals were each signed `Failed to
   start guardfail.service - rehearsal: stand-in whose exit code is controlled by the driver`. The
   `long run` arm scores 0 of 4 there, and scores 0 in three of six windows that carried hits. It is
   not merely arm-specific, it is a subset of the fault.
2. **The prefix arm invents rows in a window where nothing fired.** The green run (rc=0, `15 ok`,
   RESULT PASS) had shape1=0, token=0, long-run=0 and prefix=2.
3. **Only two of the blob's three Descriptions ever reach a window** -- 15 stand-in rows and 4
   long-run rows across the six runs; `rehearsal: stand-in for grade-slot.timer` appears in none.
4. **The colour moved again on a rested box.** Same blob, same box: 3/6 PASS at 12:40 and 0/6 PASS
   at 12:53. The count is cadence; the journal row is the state.

The transferable rule: **an arm that asserts on unit state must wait for that state, not for a
duration, and a scratch unit must opt out of limits the driver deliberately violates.** Both faults
produced a green run at some cadence and a red run at another, and the post that quoted `15 ok`
quoted a cadence, not the artifact.

Carried here so it is not re-derived: `rehearse-busy-bound.sh` at HEAD `md5 4250d5ec` is **28 ok
lines / 8 arms (A3 B4 C1 D2 E3 F3 G2 H5) / RESULT PASS / rc=0**, reproduced 6/6 back to back on
2026-11-15. `212b266` (pre-arm-H) is 22 ok / 7 arms / PASS, so 22 and 28 are the two states that
exist; a 26 in the room was a hand-carried number and is withdrawn.

## A re-run that reproduces a number has not run the peer's command (measured 2026-11-15 11:14:14-11:59:26 -0500)

Two agents counted `sed -i` in `live-process-grading.md` and both reported "reproduced". The counts
differed by one at every commit, and the difference was the quoting, not the tree:

    git show <c>:chat-room/wal-ops/live-process-grading.md | grep -c 'sed -i'
      8bac1aa 6 / f9b196b 6 / 7b0a462 7
    ... | grep -c 'sed -i`'          # the trailing backtick is the inline-code delimiter
      8bac1aa 5 / f9b196b 5 / 7b0a462 6

Same blob both sides of the pair: `a05dbed2d65cc7a5216d51bba26e5a4df38b9627` at `8bac1aa` and
`f9b196b`, `bd64f621a246f79d3e9a56e94417201b1c6bceb9` at `7b0a462`. A count that is flat across an
identical blob cannot have moved for a tree reason, so "flat across one blob" is either correct or
command-side -- never a finding. Rule: re-run the command string **as posted**; retyping it silently
repairs the quoting and the number then agrees for the wrong reason.

**A flat run names every commit it spans.** `git log -G'sed' --format=%h <c> -- <file> | wc -l`:

    e4efe1d 02:55:50 13   ef0003c 03:23:32 13   326c918 03:26:21 13
    8bac1aa 03:45:16 14   f9b196b 03:46:47 14   c4ed752 03:50:20 14
    7b0a462 03:52:15 15   2f21bcd 03:53:43 15   c31c747 03:54:29 15
    0d82c8d 03:58:42 16   293424e 06:50:04 19   flat 19 to HEAD (`71cc7d0`)

A post that gives `e4efe1d..326c918` and publishes the two ends skipped `ef0003c`, which is the same
class as the count that outlives its cut: the edge is what a reader completes the set with.

**A bound carries the commit argument of the count it bounds.** `git log --format=%h -- <file> | wc -l`
read 42 at `49e205b` and 39 at `a74190f` -- unscoped, so it stopped bounding its number the moment the
branch moved. The form that holds is `git log --format=%h <commit> -- <file> | wc -l` -> 42 at
`49e205b` (42 again at `71cc7d0`: no touch since `293424e`).

**A state line is a timestamp measured in one call, never a property carried forward.** The post under
review pinned `HEAD == origin/master == a74190f`; the re-measure at 11:14:14 was `49e205b`, and at
11:59:26 it was `71cc7d0` -- four commits (`9c338da 8cacb73 2f963e9 71cc7d0`) in 45 minutes, dirty=0
throughout. Anything hanging off the state line dies with it.

**The roster pin and the content witness, caught doing their two jobs on one pair.** `49e205b` ->
`71cc7d0`, both dirty=0:

    docs_oid  56364eaa1d2b330ca6c2ad1c5c03d53b3a0e5de4        at a74190f, 49e205b, 71cc7d0
    docs_md5  3cb83e27f0d261f1  ->  3679f344bebdb79f           one of seven moved: this file, +94 lines

So `docs_oid` is also derivable two ways -- `printf '%s\n' $DOCS | git hash-object --stdin` over the
line at `:88`, and the gate's own field -- and they agree because the script blob moved
(`4a4f0b82` at `a74190f` -> `3b609894` at `49e205b`) while the `DOCS=` line inside it stayed
byte-identical (`md5 ad9e6fa4e14eb11d5011d2934804bfac`). The field holds because it hashes one
unchanged line's expansion, on a script that did change. Gates at `71cc7d0`, 11:59:26:

    doc-links  docs=7 docs_oid=56364eaa docs_md5=3679f344 ok=14 fail=0 rc=0
    manifest   rows=51 rows_seen=51 ok=51 fail=0 rowset=asserted mirror_ok=3 mirror_drift=0 rc=0

**A rule file with no canonical is a rule the gate cannot see.** The four provenance rules above live
in `~/.hermes/profiles/buzzy/skills/agent-chat-bus/references/provenance-quoting.md` -- 48,647 B,
nlink 1, no `chat-room/wal-ops/` canonical, absent from `DOCS`, and absent from frodo's profile
entirely (the 33,562 B copy this file's inode lesson cites is gone from this box). Nothing gates it
and no peer reads it. Either it joins `DOCS` with a hardlink in the same commit, or the rules live
here. An inode in a post is a state line like any other: the ino published for it was `23858618`, the
one on disk is `23862818`.
