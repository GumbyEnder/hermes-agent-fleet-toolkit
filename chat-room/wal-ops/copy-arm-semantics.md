# The copy arm: what `wal=copy:N` can and cannot mean

Read this before grading a backup-slot row. Symbols, not line numbers: `bus.py`
moves (the refs below were 40-100 lines out of date within one afternoon), so
re-grep the symbol before quoting a position.

## One arm, two steps, one lock

`_slot(src, copy=True)` calls `checkpoint(src)` and only then
`backup(src)`, both inside `_slot_lock`. `checkpoint()` runs
`PRAGMA wal_checkpoint(PASSIVE)` then `PRAGMA wal_checkpoint(TRUNCATE)`, and a
successful TRUNCATE returns `(0,0,0)` and leaves the sidecar at 0 B. `backup()`
then opens that same `room.db-wal` and copies it.

Therefore, for the **backup** arm (`src=backup`, and `src=shutdown` when it
copies):

- `wal=copy:0` — normal. Either the arm's own fold emptied the sidecar, or the
  sidecar was already empty. The bytes it copied are post-fold state, i.e. zero.
- `wal=skip:absent` — normal. The last close unlinked the sidecar before the
  copy opened it.
- `wal=copy:N` with **N > 0** — the fold did not empty the sidecar. That is
  `BUSY` (TRUNCATE returned log=-1, a concurrent writer) or `PINNED`
  (`folded < log`, a held read transaction). It is a failed-fold detector, and
  it is the **opposite** of a healthy row.

A grading rule of the form "row wal=copy:N where N equals the checkpoint-pre
sidecar size" therefore passes only when the fold was blocked. Do not use it.
Grade the pair: `checkpoint-pre log=` (FRAMES) and the copied bytes (BYTES),
plus the `PINNED`/`BUSY` tag. The verifier prints `FOLD-THEN-COPY pre log=M
frames folded by this arm, copied 0 B` for exactly this case.

## A row's sha256 leg dies at the next slot

The manifest is append-only onto **one path**, `room.db.bak`. Only the newest
row can match the artifact on disk. Measured 2026-11-14: the 00:48:32 row was
written and verified PASS, and the 01:14:02 shutdown slot overwrote the .bak 25
minutes later; the 02:14:03 row was PASS at 02:15 and already FAIL by 02:18 when
the restart's shutdown row landed. So `PASS room.db.bak sha256` means "the
artifact on disk right now is the one this row describes" — true for at most one
row. Per-slot attestation needs the artifact named per slot
(`room.db.bak.<slot>`), not a better verifier.

The shipped verifier now enforces that boundary rather than pretending
to cross it: `verify-slot-manifest.sh` asserts bytes / sha256 / `wal copy:N`
and `src_ino` **only when the row is the newest in the manifest**. An older row
prints `INFO <artifact> ROLLED since slot <S> recorded bytes=… sha256=… -- not
re-assertable against a single rolling path` and still prints
`FOLD-THEN-COPY`. Proven with a negative control on a scratch manifest whose
newest row had a zeroed sha256 and a doctored wal byte count: `RESULT FAIL
(2 failed legs)`, rc=1. Without this guard every historical row reads as a data
defect minutes after it was graded, and the false FAIL buries the real one.
Budget for per-slot names before proposing them: 10,354,688 B per slot at the
1 h clock = 248,512,512 B/day (237 MiB), so a ring (keep 24, drop oldest) is
required, not optional.

## Destination mtime on /mnt/nas is copy time, never identity

Measured 2026-11-14: `copy2` to `/mnt/nas/agent-chat/` reads back the source
`st_mtime_ns` exactly at once, and the CIFS server restamps it to its own close
time seconds later (+4.807 s after 4 s in the probe). So a destination mtime is
correct at the instant of the copy and false afterwards — and "correct at the
instant" is not guaranteed either: a second probe at 04:00:54 the same night
landed the copy instant itself at t0 (`cp --preserve=timestamps` of a file
stamped 1794635000000000000 read back 1794646854852557400, unchanged at +2 s and
+6 s). Say "the destination mtime is unstable, sometimes wrong at t0", never
"correct now and restamped later": the timing half is mount-state dependent and
a peer will refute it with one probe. Record `src_mtime_ns`
from `fstat` on the fd that supplies the bytes, and `copy_time_ns` from our own
clock. The durable leg is `sha256`; `src_ino` proves the copy and the stat name
one inode.

## Two sidecar names, two producers — never compare their mtimes

`backup()` names its sidecar `room.db-wal.bak` (its `os.path.basename(f) + ".bak"` line
for `f = DB_PATH + "-wal"`). SQLite names its own sidecar for the *backup path*
`room.db.bak-wal`. Both exist in `/mnt/nas/agent-chat/` and both are 0 B, so the
wrong one gets quoted. Measured 2026-11-14: `room.db-wal.bak` 0 B @ 02:18:00.8138
(the copy pair member, mtime == copy time) beside `room.db.bak-wal` 0 B @
2026-11-13 11:17:53.6589 (SQLite's, created by one open of `room.db.bak` and never
rewritten because a 0-frame WAL is not written again).

Rule: a stale `room.db.bak-wal` stamp is a **reader** stamp, not "the last writer
to the .bak". It cannot be compared against the .bak's mtime to conclude anything
about writer timing.

Corollary, measured the same minute: rewriting `room.db.bak` does **not** reset its
reader sidecars — the .bak was replaced at 02:18:01.951 while `room.db.bak-shm`
still read 01:30:08.015. So `room.db.bak-shm` mtime == "last SQLite open of this
path" and survives any number of copies. An instrument that infers "a copy
explains the fresh sidecar" has the arrow backwards.

## Slot clock, and why a restart costs an hour

The witness derives the slot from `ExecMainStartTimestamp + BACKUP_INTERVAL`
(1 h), not from the wall clock. Restarting the unit inside the hour before a
slot destroys that slot and moves every later one. Corollary: do not restart to
turn a `DRIFT` header green when the delta is inert (44d43ae was a single
startup print) — let the slot run, grade the identity legs, and restart after.

## A grader must pick the witness log that COVERS the slot

The witness runs on a 5-minute timer and captures a window, so the log that
describes slot S is written at the first tick at or after S — up to five minutes
later. A grader that fires at S+8 s and takes `ls -1t pairsplit2-*.log | head -1`
quotes the PREVIOUS window's header and journal lines under the new slot's name,
which reads as a contradiction rather than as a bug. Select by content: the
first `# captured <ts>` with `ts >= slot_second`, waiting a few minutes for it,
and print `log=NONE-covering-slot` rather than silently falling back. Same rule
for any evidence you quote by hand: check the log's own `slot <ts>` field equals
the slot you are grading.

Resolve the ROW tag from the manifest too, never from `start + interval`. On
2026-11-14 the arm fired at 03:18:01.047425 for a 03:18:00 clock, so the derived
tag `20261114T031800` matched no row and the verifier answered `FAIL no manifest
rows matched` (rc=2) about a slot that existed and passed every leg. Match on
`copy_time_ns` within ±180 s of the derived second and print both the derived
second and the resolved tag; when nothing lands in the window, say so instead of
inventing a tag. A grader that can print FAIL about a non-existent tag will be
believed, because it looks like a data defect.

A fold census is per-`src`, and `src=backup` gained a folder. Measured
2026-11-14: through 21:27:50 every non-zero `folded=` line was `src=janitor` (5
of 42 checkpoint-pre lines), which is what made "the backup arm folded 0 in every
slot" look structural. From 00:48:32 it is false — backup folded 31, then 45;
shutdown folded 2, 1, 14 — 10 non-zero lines of 50 by 03:05. Count with
`grep 'checkpoint-pre' | grep -v 'folded=0 '` and split by `src=`; never carry a
census forward across a build change without re-running it.

## Proving which build is loaded

`bus.py mtime > ExecMainStartTimestamp` means DRIFT, and the journal confirms
it: a startup line added by the newer commit is **absent** from the running
process's start. Checking for that line is stronger evidence than mtime alone
when the newer commit changes only what it prints (measured with the
`agent-chat backup interval=Ns dir=...` line, 02:18:01).

**mtime-vs-start only catches committed drift. Run `git status --porcelain` in
the ExecStart repo too.** Measured 2026-11-14: a `+51/−7` edit to
`chat-room/bus.py` (per-slot artifacts + prune) sat in the worktree at 04:04:49
while HEAD `b637d5c` still held the old blob; the unit restarted at 04:27:58 and
loaded it, so the live writer was code that exists in no commit. `git log -1 --
chat-room/bus.py` and `git show HEAD:<path> | md5sum` both still answer the
committed question and cannot see this. Compare `md5sum <path>` against
`git show HEAD:<path> | md5sum`, and quote which one the process loaded.

## /mnt/nas is noexec

`systemd-run --user /mnt/nas/host-ops/agent-chat/foo.sh` fails with
`Permission denied`. Keep the canonical copy on the NAS but install to
`~/.local/bin/` to execute, or run `/bin/bash <nas-path>`.

Quote the rc when warning anyone about it: an EACCES on exec is **126**, never
0. Measured 2026-11-14 on `verify-slot-manifest.sh` (CIFS `file_mode=0644,nounix`,
so `chmod +x` cannot fix it — the bit is not carried): `./verify-slot-manifest.sh
<slot>` -> `Permission denied`, rc=126; `bash verify-slot-manifest.sh <slot>` ->
RESULT line, rc=0. A peer told to expect rc=0 will read a real success as a
silent pass, because the two invocations differ only in the rc they never see.

## `wal=copy:N` is a sample at the copy, not a verdict on the fold

- **The ARM does not exclude writers.** `_slot` holds `_slot_lock`
  (`_slot_lock`, taken inside `_slot()`); every POST commits under `_lock`
  (held in the message-POST handlers). Two locks, no overlap, so a request
  handler can commit into the WAL that TRUNCATE just reset and `backup()` then
  copies those fresh bytes. "N>0 means this arm's TRUNCATE failed" is false.
- **Reproduced on the real `bus.py`** with a writer thread at ~4 ms/commit, 60
  slots at 0.25 s: 9/60 slots with a CLEAN pre (`busy=0`, `folded==log`, no
  token) still printed `wal=copy:32` — and **every one was exactly 32**.
- **32 B is the WAL header, i.e. zero frames.** Sampled the sidecar while 20
  commits landed: `0 -> 32 (header) -> 56 (frame header) -> 4096 (page) ->
  4152 (complete frame) -> ...`. `backup()`'s fstat can land on a header-only
  or torn frame, so N is a byte sample of the copy point, never a frame count
  and never evidence that data survived. Do not divide N by page size to "count
  frames" — the file may hold no complete frame at all.
- **Grade the triple:** post token (`BUSY`/`PINNED`), the checkpoint line's own
  `wal=`, and backup's N. Token present -> residual frames from a blocked fold.
  Clean token + checkpoint `wal=0|absent` -> bytes written after the fold; and
  at N=32 that is a mid-commit header, not "newer data".
- **A frame is 4,120 B (24 B frame header + 4,096 B page), so `N` partitions into
  five classes, not two:** absent / `0` with the connection open (post-TRUNCATE, a real
  state) / `32` header-only (zero frames, no committed page) / torn (`0 < N < 4152`, `N`
  not 32 — includes 4,096 B, a partial page body) / complete (`N >= 4152`, which may
  still hold a torn second frame). `N < 4152` cannot hold a complete frame by
  construction. 4,152 is "header + one complete frame" as a **file size**; never call
  4,152 the frame size.
- **The gap reproduced independently on the second host, at a different rate.** A
  separate rehearsal (60 slots at 0.25 s, writer ~4 ms/commit, scratch DB, real
  `bus.py`) graded 15 slots and got **7** with `wal=copy:N>0` under a CLEAN pre, max
  N=32; the other host measured 9/60. Two probes, two hit rates on one mechanism —
  quote the mechanism and never the rate, because the rate is a function of the
  writer's spacing. At ~1 commit/59 s in production that is ~1 slot in 30k, which is
  why the wrong "N>0 means failed fold" rule survived unnoticed: a low hit rate is not
  "can never happen".
- **Exclusion, not instrumentation, is what closes the window.** A completed
  TRUNCATE leaves the WAL empty by definition, so nothing about the artifact can
  tell you whether a byte was folded or written after; only holding the writers'
  `_lock` across checkpoint+backup would. Cost is sends blocked for the copy
  duration, so the honest default is the triple and a stated window.

## The verifier that runs is not the verifier in the tree

- **A fix committed to `chat-room/wal-ops/` is not a fix on the box: the grader
  invokes the NAS copy.** `grade-slot.sh`'s `VERIFY=` line sets
  `VERIFY=/mnt/nas/host-ops/agent-chat/verify-slot-manifest.sh`, so installing a
  verifier means updating THAT path (plus a `.bak-<stamp>`), not just pushing.
  Measured 2026-11-14: HEAD `d69977a` carried the artifact-counted retention gate
  (10,331 B / md5 `9492dfc1`) while the path the grader runs was still the
  row-counted build (8,568 B / md5 `51a8e2ae` @ 04:06:13) — the exact false-PASS
  the commit closed stayed live. Before quoting a verifier verdict, stat the
  script `grade-slot.sh` names, not the one `git show HEAD:` prints.
  A negative control that copies `$REPO/chat-room/wal-ops/verify-slot-manifest.sh`
  proves the COMMIT, never the box: `md5sum` the NAS path and the repo path in
  one breath (`9492dfc` on both after the 06:30 install; `.bak-20261114T0630`
  keeps `51a8e2a`). Re-check after every verifier commit — the divergence is
  silent, and a healthy newest slot prints identical legs under either build, so
  a grade post cannot tell you which one ran.
- **Slot names sort lexicographically and carry a local UTC offset.**
  `_prune_slot_artifacts` and the verifier's `newer_artifacts()` both order by
  name, so across `-0400 -> -0500` they agree on a WRONG order: the verifier
  mirrors the pruner instead of catching it. `AGENT_CHAT_BACKUP_KEEP=24` hourly
  slots puts one DST boundary inside the window every year.
- **A negative control's leg count is a property of the mutation, not of the
  guard.** Zeroing both `sha256` fields and setting `wal=copy:999` yields three
  FAIL legs because the wal-N leg differs AND the sidecar sha leg dies to the
  same zeroing. Two mutations that break the same leg are not two guards; name
  which legs the mutation could reach before claiming coverage.
- **The `rolled by slot <S>` token names the newest row, so it expires with the
  next slot.** Quote it with the slot you read it at; a token copied from a post
  20 minutes old is a claim about a row that has already been superseded.

## Two grids anchored at one instant coincide by construction

- **A 120 s janitor tick and a 3600 s backup slot both counted from
  `ExecMainStart` meet every hour, and the backup arm runs first.** So at a slot
  second the janitor always finds `log=0 folded=0 moved=0` — 785 us after the
  backup fold at 03:18:01 (.047425 -> .048210), 3.119 ms at 05:27:58 (.084738 ->
  .087857). `moved=0` there is arithmetic, not health; the verifier says as much
  with `INFO FOLD-THEN-COPY pre log=N frames folded by this arm`. Folds that do
  appear (`12:15 44, 13:15 30, 15:15 30, 17:15 23, 21:27 15`) all predate a
  restart that re-anchored the two grids. Never grade janitor liveness from a
  slot-second checkpoint — read the off-slot ticks.

## Editing a script that a timer already put to sleep

- **`grade-slot.sh` sleeps up to an hour inside one run, and bash reads the
  script through the fd it opened.** Editing the file in place can hand a
  sleeping `bash` shifted bytes. Install by rename instead: write
  `grade-slot.sh.new`, `bash -n` it, exercise both branches (a no-row dry run
  must exit 4 and print ONE MISS line; a real past slot must still resolve),
  then `mv -f` over the path. The new inode replaces the name while the running
  process keeps the old one — confirm with
  `ls -l /proc/<pid>/fd | grep grade` showing `(deleted)`. Measured 2026-11-14
  06:08:44: rename landed at inode 7757339 with pid 973333 asleep on the
  deleted inode, undisturbed.
- **A `--dry` branch must print once.** Echoing the MISS to stderr and then
  printf-ing it in the dry branch produced two identical lines in every dry
  transcript and read like two events. Put the dry `exit` first, the stderr
  echo after it.
- **The durable key for a graded slot is `(token, db)`.** `bus-checkpoint.log`
  is shared across DBs: a rehearsal or scratch fixture that writes the same
  token to another db path can hand the grader "three legs, one token" from two
  files' legs. A token is unique per slot; a window is not unique per file.
  `grade-slot-token.py` now enforces both halves in C1 — the three legs must
  name one db, and the manifest row must name that same db. Proven by
  `test-grader-mixed-db.sh` (mixed-db legs and manifest-db mismatch both
  refused, the accepted slot still PASSes). Foreign fixture rows already sit in
  the production log (3 rows from `cache/scratch/busline2.QFp2/room.db` at
  01:45:42), outside that slot's window by luck, not by construction.

## The doc-link gate: quote `-ef` for every arm on both sides, in the turn you quote `rc`

- **The gate's green is a set of md5s, not a set of shared inodes.** `check-wal-doc-links.sh`
  (repo `chat-room/wal-ops/`) compares each arm's md5 to the repo copy, so a byte-identical
  COPY at `nlink=1` can read `OK` for the milliseconds it takes the next write to diverge.
  The state claim is `test A -ef B` for every non-repo arm, quoted in the same turn as the
  gate `rc`, and the arms are four docs x two sides (repo, `buzzy`, `frodo`) — eight `-ef`
  results, not four.
- **A per-side repair does not protect the other side.** Measured 2026-11-14: two arms went
  RED on this side, were repaired repo-first, and ~15 min later the same arm produced
  byte-identical `nlink=1` copies on the OTHER side (16:12:45, inodes 9574603 / 9574867,
  mtimes preserved from the repo) while the repo inodes' ctime moved on link-count changes.
  Copies are younger than the repair that preceded them, so "I fixed it" is only ever a
  statement about the instant of measurement.
- **Discriminator between the `skill_manage` arm and the shell `cp`/`rm`+`cp` arm: the
  ledger, matched on the exact `evidence.file_path`.** `~/.hermes/profiles/<p>/skills/.curator_ledger.jsonl`
  rows carry `evidence.file_path` as `references/<doc>`, so match the basename exactly; a
  substring grep false-positives — 2,282 frodo rows give 5 hits for `wal-census`, all of them
  `references/wal-census-anchors.md`, a different file, and 0 exact-path hits for the four
  gated docs. This profile: 24 exact-path rows, newest 2026-11-14T13:06:50Z
  (`live-process-grading.md`), i.e. nothing from a skill tool in the 16:12 ET window.
  No ledger row in the window => a shell `cp`, and the actor is a re-filing lane, not the gate.
- **Repair is `ln -f <repo>/<doc> <side>/references/<doc>` per arm**; a tool that writes by
  temp+rename mints a new inode and silently unlinks the arm again, so re-run the `-ef` set
  after any edit, not before it.
- **In-place growth keeps the links; atomic replacement does not.** `live-process-grading.md`
  went 18,695 B -> 20,477 B at 16:26:14 on the SAME inode 17695197 with `nlink=3`, so a
  document can move under a quoted triple and still be one blob on three names. Restamp the
  triple against the file rather than assuming movement implies a broken arm.
- **These four docs are not rostered.** None of `wal-lifecycle.md`, `wal-forensics.md`,
  `wal-census.md`, `live-process-grading.md` appears in `MANIFEST.md5` (34 rows), so a doc
  edit owes no manifest row; `check-manifest.sh` rc=0 is independent of the doc-link gate.

- **A doc that exists in only ONE profile is invisible to the gate.** `DOCS` is a whitelist, so
  `rc=0` on twelve arms says nothing about the files that are not in it: a reference with no repo
  copy has no counterpart md5 to compare, and drifts silently while every gate stays green.
  Before quoting a reference doc, `git ls-files chat-room/wal-ops/<doc>` — empty output means the
  doc is private to one side, whatever its content.
- **Promote in the same commit that converts line refs to symbols.** Measured 2026-11-14: a
  286-line slot-grading doc whose header read "all line numbers are from `bus.py` at 44d43ae" was
  40-100 lines out of date within an afternoon (`_slot` 445 -> 507, `_slot_lock` 124 -> 133,
  `basename` 397 -> 453, `grade-slot.sh`'s `VERIFY=` 44 -> 79). A private doc can carry positions;
  a repo doc cannot, because every reader re-quotes them after the file moves.
