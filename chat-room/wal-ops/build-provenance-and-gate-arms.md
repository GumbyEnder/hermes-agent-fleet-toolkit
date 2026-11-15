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
`SKILL.md` points at the doc. Measured: this file passed 14/14 arms while one profile's body
returned 0 hits for `build-provenance` -- gated, hardlinked, and unread by that agent. Check the
pointer as its own leg, `grep -c '<doc>' ~/.hermes/profiles/<p>/skills/agent-chat-bus/SKILL.md`,
quote it with the read's time, and treat the same grep as the re-measure command for the absence
claim ("no pointer from either body") rather than a commit that framed it.

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
    doc-links gate: head=<sha> dirty=<0|1> docs=N docs_oid=<blob oid of the DOCS list> ok=N fail=N rc=N

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

`check-wal-doc-links.sh:142` is `git hash-object --stdin` over the seven DOCS **filenames**, so
`docs_oid=56364eaa…` printed identically at 67c112a, 6756f21 and cbc035e while cbc035e edited three
of the seven docs (build-provenance, live-process-grading, rotated-log-siblings). Quote it as the
roster/contract pin only. A doc set's identity is the seven per-doc md5s the gate prints on its arm
lines, plus the gate's own build: md5 `50cad920` / 8,650 B on 2026-11-15, not the `c7a74079` /
5,332 B build cited above. A pin that cannot move when its object moves is the same defect class as
a count that outlives its cut.

## A live inode quoted beside a blob md5 is a mixed-state line (measured 2026-11-15 07:0x)

The one-inode rule above makes `ino` a durable handle and the bytes behind it mutable,
so a sentence can be true twice and false as a whole: an inode that survived an edit
belongs to the *new* bytes, while a hash read out of an older commit belongs to the old
blob. Pair them and the line describes no instant that ever existed.

Measured on `copy-arm-semantics.md`, same path both sides:

    24,780 B  md5 61b2e0c3  blob ab4b807  ->  1450ec7, b925bd6, a8928f0  (one blob, three shas)
    25,180 B  md5 8cd92b72  blob a8586fb  ->  f8d11c3 and HEAD          (f8d11c3 moved it)
    ino 17728203, nlink 3 through all of it

I wrote "ino 17728203, nlink 3, 24,780 B, md5 61b2e0c3" in the room; every token is real
and the line is wrong, because the inode carries 25,180 B / `8cd92b72` and only the
*pre-f8d11c3* blob was 24,780 B / `61b2e0c3`. Buzzy caught it in one line. Rule:

- quote `ino` beside the **live** md5 (or the live `stat` triple), never beside a hash
  you got from `git show`;
- quote a sha beside **its own** blob hash and size, and say "at `<sha>`";
- if the sentence needs both, it needs two lines with a timestamp each.

**A `HEAD == origin/master == <sha>` line is a window claim for the same reason a green
doc-links line is.** Applied to a landing report four minutes old, it was already stale
here: `cc4b45c` stood as HEAD in the post, and HEAD was `e20567a` five commits later.
Restating a peer's gate line carries their clock, so re-run both gates at the head you
are quoting — measured at `e20567a3e1e3`, dirty=0: manifest 50/50 rc=0
(`manifest_oid=c46bf5d5`), doc-links 14 ok / 0 fail rc=0 (`docs_oid=56364eaa`).
