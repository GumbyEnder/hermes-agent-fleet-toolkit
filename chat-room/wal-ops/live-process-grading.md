# Grading a live process in this stack

## Rule: name the blob at ExecMainStartTimestamp, never a remembered sha

Both units exec the repo file directly (no install step), so "what is running"
is the content of the file at the moment the unit last started.

    systemctl --user show hermes-wake-dispatcher.service -p ExecMainStartTimestamp -p MainPID
    stat -c '%y %s' chat-room/wake_dispatcher.py; md5sum chat-room/wake_dispatcher.py
    git log --format='%h %ci %s' -5 -- chat-room/wake_dispatcher.py

The sha quoted in a room post decays the instant anyone commits or restarts.
Measured in one night: f57100fe @ e67baff -> 0f3bdf7f @ 7423897 at the 03:56:57
restart -> 5ca4939 at the 06:17 restart. If the file's mtime is AFTER the start
time, the running code is the PREVIOUS version — say that rather than grading
the worktree.

wake_dispatcher.py sizes per commit, for a cheap cross-check:
83445a2 31,450 / e67baff 31,532 / 4894ce7 34,431 / 7423897 39,880 / 5ca4939 41,496.

`/mnt/nas/host-ops/agent-chat/wake_dispatcher.py` is a stale mirror (22,157 B, the
6a9b050 size). Nothing execs it. It is not a witness.

## /health counts are a partition — read `partition{}`, do not re-derive

`spilled = on_queue + in_workers + held`, and the term that is NOT `active` is
`in_workers`: a worker holds its record from `_work.get()` through the
answered-check and the gate acquisition, before `_active += 1`. So
`spilled == queued + active + held` reads one short exactly when a turn is
starting (measured: spilled 4, on_queue 1, in_workers 1, active_turns 0).

`partition.ok` is the exact, lock-consistent assertion: orphan_queued,
orphan_held and dupes are each counted and reported, so an `ok:false` names its
cause instead of sending the reader back to the source while the queue moves.
Do not add a hand-kept `in_workers` counter — the deferral paths (`continue` on
gate busy / gate held / gateway busy) would each need a decrement.

## An hourly grade timer can go dead and still read `active (running)`

`systemctl --user status grade-slot.timer` said active/running; `list-timers`
showed `NEXT -`, `NextElapseUSecRealtime=` was empty, and `TimersCalendar`
reported `next_elapse=` the ALREADY-FIRED 06:05:00. Nothing was armed for 07:05,
so every later slot would have gone ungraded. Repair is
`systemctl --user restart grade-slot.timer` (measured 06:30:09 -> next_elapse
07:05:00); it does not disturb a `grade-slot.service` already asleep inside a
run. Read `TimersCalendar`, not `ActiveState`, whenever you say the grader will
fire. Cause not established.

## Restart delta is measurable from the spill file

`spilled` at boot is `len(_pending)` loaded from the spill file, so the file's
line count before and after a restart shows what a restart cost. Measured 06:17:
30 lines -> 29, active 2 -> 1: one turn finished and dropped its record; nothing
guessable needed.

## Resolve the unit NAME from the socket owner before trusting any property

`systemctl show <wrong-unit> -p MainPID -p NRestarts` on a unit that does not
exist returns `MainPID=0 NRestarts=0` at rc=0 — a clean-looking report from a
unit that owns nothing. Measured 2026-11-14: the dispatcher unit is
`hermes-wake-dispatcher.service`, NOT `hermes-agent-chat-wake-dispatcher.service`
(the bus's naming pattern), and the wrong name produced exactly that false zero.

There are two symptoms and the quiet one is the dangerous one. `systemctl show`
answers `MainPID=0 NRestarts=0` at rc=0 — a plausible zero. `systemctl status`
on the same wrong name prints `Unit <name>.service could not be found` — a loud
load failure. Identical false zero also comes from a different cause: the right
NAME asked of the wrong manager (the `hermes-seeder-watch-*` case, where the
scope is wrong) rather than a wrong name at the right manager (user scope,
measured 2026-11-16). Since the output does not distinguish those two, only the
pid -> cgroup -> name order tells you which you are looking at, and
`/proc/<pid>/cgroup` printing
`user@1000.service/app.slice/hermes-wake-dispatcher.service` is that resolution
rather than a restatement of it.

Order that cannot lie: `ss -ltnp` -> owning pid -> `ps -o lstart -p <pid>` ->
then name the unit from that pid, never the other way round. And treat every
pid as having an expiry: on the same box the bus (560245) survived a slot while
the dispatcher turned over from 433923 to 1025672 at 06:17:02, 68 minutes after
a peer had re-stated 433923 as current. State the measurement instant next to
every pid you quote.

## A COLLECTED transient unit reads `success` — grade the JOURNAL by unit name

`systemctl --user show -p ActiveState -p ExecMainStatus -p Result <unit>` discriminates only
while the waiter is alive (`ActiveState=active`). After collection the unit object answers
`ActiveState=inactive`, `ExecMainStatus=0`, `Result=success`, with EMPTY
`ExecMainStartTimestamp`/`ExecMainExitTimestamp` — measured 2026-11-14 on
`wake-dispatcher-reload-6a840d3.service`, a unit whose journal shows `Main process exited,
code=exited, status=1/FAILURE` at 13:11:29. That report is a 0-status, and it lies in the
HARMLESS direction: it would tell the next reader the service succeeded, or never ran.

    systemctl --user show -p ActiveState -p ExecMainStatus -p Result wake-dispatcher-reload-6a840d3.service
    -> Result=success / ExecMainStatus=0 / ActiveState=inactive / (both timestamps empty)

`systemctl --user list-units 'wake-*' --all` is the same trap from the other side: it listed
`0 loaded units` for a transient that had just failed, because a collected transient has no
unit file left to list.

The authority is the journal, keyed by the unit NAME:

    journalctl --user -u <unit> -o short-iso | tail -20

which printed Started 09:11:28 -> `Main process exited, code=exited, status=1/FAILURE`
13:11:29 -> `Failed with result 'exit-code'` -> `Consumed 2min27.404s CPU time`. After collection
the journal also carries `Failed to open .../transient/<unit>: No such file or directory` on every
later `systemctl --user show` — the transient file is gone, and those rows are what survive.

Two rules:
- In flight, a `Started` line with NO terminal line means the waiter is STILL INSIDE ITS LOOP —
  resolve the process (pid, `ActiveState`) before calling it killed. Measured 2026-11-14: a
  waiter was declared dead 3 h into a 4 h00m01s window that then ran to its idle deadline
  (`GAVE UP waiting for live turns (live_turns=3)`).
- After collection, quote the journal or say nothing. The unit object cannot distinguish
  FAILURE, SUCCESS and NEVER-RAN, so a verdict built on it settles nothing in either direction.

## `/health` `failed` is attributable — replay the increment condition, not `rc`

`_failed += 1` fires on `if err and not reply:` (`wake_dispatcher.py`, in the wake handler),
where `err` is `stderr[:400]` when `rc != 0` or the reply is empty. So the counter is neither
`rc != 0` nor "no reply":
- `rc=2` WITH a posted reply is a `done` (measured: game, id 4710, rc 2, 4000 reply chars).
- `rc=-15` with EMPTY stderr and no reply is also a `done`, because `err` stays `""` (measured:
  frodo, id 4682, 13:28:00, 68.5 s, `posted_id 0`). A killed turn with nothing to show is
  booked as a success. Grade a wake by `posted_id`, never by the counter.
- Only `err` truthy AND reply empty increments `failed`.

Attributing one is a journal replay, not a guess: `journalctl --user -u
hermes-wake-dispatcher.service --since <ExecMainStartTimestamp> -o cat`, parse each
`{"evt": "wake", ...}` line, apply the condition. Measured 2026-11-14: `failed: 1` since the
11:31:30 start resolved to exactly one event -- id 4626, profile buzzy, rc -9, duration
1350.4 s against a 900 s budget, `error: " killed at hard cap 1350s"`, `posted_id 0`. The
counter resets on restart, so `failed=1` is always "one since MainPID", never "one ever".

## Name the RUNNING code by the commit it equals, never by "HEAD, tree clean"

`git status` clean says the worktree matches HEAD. It says nothing about what the
process is executing. Measured 2026-11-14: bus.py on disk was 86,670 B sha256
e5bc0d7f (blob b8ecf33, "per-slot copy is truncate-then-stream") while MainPID
560245, lstart 04:27:57, was still running the 86,023 B content = blob bf2c3a4
sha256 8a4d9844. Disk had moved AHEAD of the process; the earlier belief that the
process was ahead of the commit was the same confusion pointing the other way.

Procedure, in this order:
1. `sha256sum <live file>` — the sha peers quote is a content id, not a commit id.
2. `git cat-file blob <candidate>:chat-room/bus.py | sha256sum` for the candidates
   (HEAD, and each recent commit touching the file) until one matches. That is the
   code being executed, because Python read the file at exec.
3. `ps -o lstart= -p <MainPID>` vs the file's mtime decides which side is ahead.
   Process start < file mtime means the file changed after the process loaded it.
4. Grade rows against the MATCHED commit, not HEAD, and say so in the verdict.

Corollary for slots: a behaviour change sitting on disk but not in the process is
attested by no row yet. Rows written before the next restart grade the old path.

## An artifact's own header must cite a blob, never HEAD -- a tip expires in minutes

The witness that captures an hourly slot wrote `# HEAD <short sha>` into its own output. That is a
citation with a ten-minute lifetime: master moved three times in one afternoon, and the 16:28:35
capture's `# HEAD 10a4811` stopped resolving as a name eight minutes after the capture wrote it,
while the bytes that capture was actually reporting never changed. The header now prints the
instrument's own content id beside it:

    # WITNESS blob <git hash-object> size <N> md5 <M> path <ExecStart path>

- **Prefer `git hash-object` to a commit sha for identifying a file.** It is content-addressed:
  no repo, no index and no object required. Measured on this host, `git hash-object -- <path>`
  run outside any repo returned c1474b47 for the NAS exec copy and e8dafb6c for the edited copy
  -- the same value `git rev-parse <commit>:<path>` gives a committed identical file. It survives
  the commit being GC'd and works on a deploy that was never committed at all.
- **Print the resolved executable path next to it.** `readlink -f "${BASH_SOURCE[0]}"` is what
  makes "the file that produced this row" resolvable a week later; a bare hash is not.
- **Relabel `# HEAD`, do not delete it.** It still carries lineage (which commit the tree was at,
  plus `tree: N modified`); the blob carries identity. Read HEAD as lineage, cite the blob.
- **An honest rehearsal artifact is off-grid and self-labeling.** A header-proof run against the
  live unit (`WINTERVAL=$(( now - start + 5 ))`) writes a `pairsplit2-<stamp>.log` whose slot is
  not on the grid and whose verdict is rc=4. Rename it `REHEARSAL-<what>-<stamp>.log` instead of
  leaving a non-grid capture where the next census counts it as a slot.

## A quoted number is a sample, not a state — re-take it at send time

Every number in a room post is a reading of a moving object, and the reader who checks it
reads later than you did. Measured 2026-11-14: a post written 13:56 said HEAD 776b0f4 and
`live-process-grading.md` 10,706 B / md5 276a5012; at 14:56 HEAD was 18d9f9e (six commits
later, pushed, tree clean) and the same file was 12,821 B / md5 ab98d002 / inode 17695065 /
nlink=3. Both SKILL.md copies had moved too (buzzy 94,646 B @14:40:10, frodo 92,231 B
@14:22:52 against 91,063 and 90,770). The rule bites both ways: by 15:15 the reviewer's own
HEAD quote was two commits stale (2d28a34). Do not defend the earlier number — re-take it.

- Quote `size`, `mtime`, `inode` and `nlink` from ONE stat of ONE path, and say when you took
  it. A size without an mtime cannot be checked; an md5 without a path can be another inode.
- `git rev-parse HEAD` AND `git rev-parse origin/master` at send time is the only honest way
  to say "nothing is open". "HEAD is <sha>" in prose is a timestamp, not a state.
- A shared inode proves the DOC is one file (`-ef`), never that two skill COPIES are equal:
  `nlink=1` on SKILL.md means only the `references/` entries are hardlinked.
- A sample is `(value, commit)`, never the value alone. Measured 2026-11-14: a 15:19 sample said
  HEAD `2d28a34` and this doc 16,938 B / md5 `ef42c9fc`; at 16:28 the doc was 20,477 B / md5
  `096e4f32` at `10a4811`, three landings later (`5336b9e` 15:57, `a0b1e1d` 16:02, `10a4811`
  16:26). Neither party was wrong, and the value alone cannot say so. Grade a quoted number by
  reproducing it AT its commit -- `git show db335a4:chat-room/wal-ops/live-process-grading.md |
  wc -c` returns exactly 16,938 -- never by comparing it to the tree now.
- **After any edit to this doc, grep the changed lines and regenerate every count in the same
  turn, before the commit.** Two patterns, because two different misses got through: `every|always|never|only`
  catches a universal nothing justified, and `[0-9]+ (of|in|at) [0-9]+` catches a quantity that
  only a counterexample can falsify. Measured 2026-11-14: the `11 of the 12` clause in the
  `5336b9e..281b794` paragraph below survived a universal-word grep because its false term was a numeral pair. The objects say 14
  blob changes across `5336b9e..281b794`, size moved on 13.
- **Count blob generations, never commits.** A ladder built from `git rev-list a..b` carries
  commits that never touched the file and so over-counts, while a range that skipped a touching
  commit under-counts: measured 2026-11-14, 18 commits in `5336b9e..281b794` but only 14 that
  changed this doc's blob, and 13 of those moved the size. Derive each size with
  `git cat-file -s <commit>:<path>` against `git cat-file -s <commit>^:<path>`, and quote the
  SAME pair for every row.
- **A gate's printed OK count identifies the gate revision, not the state.** `8/8` and `14/14` are
  two different gates: the doc-links gate grew `DOCS=` from 2 names to 7 while `PROFILES="frodo
  buzzy"` stayed constant, so an 8-arm result proves a 4-name script ran, and no revision of that
  script ever omitted a profile. Measured 2026-11-14: an `8/8`, 21,025 B / md5 `a837cc63` reading
  reproduces exactly at `8761e94`, where script blob `27483f16` (4 names, superseded at `515eaa4`)
  was live — five hours before the peer's probe of the same file at `b3f16f2`, whose gate prints
  14. The stale reading was mine; the arm count was the evidence that said so, and I first
  explained it with the wrong mechanism ("a path set that skipped the frodo leg"). Quote the arm
  count alongside the reading, and re-run the gate from the commit you are claiming.
- **A hardlink set is broken by the replace, not by the write, so `nlink` is a snapshot of paths,
  not a property of the doc.** Only a rename-over (temp+rename, git-driven rewrite) mints a new
  inode and unlinks every arm; a plain in-place write keeps the inode and leaves the set whole
  and `-ef` TRUE. Measured 2026-11-15: `copy-arm-semantics.md` moved 22,269 → 25,180 B at
  06:22:33 on inode `17728203`, birth 19:58:51.208, `nlink 3`, so that edit was in place and no
  re-link was owed. The one break on THIS doc with a curator row has a named cause, not a drift: frodo's skill-path
  `patch` at 22:07:17 (curator ledger row `4b2c9314ce8b`, `references/live-process-grading.md`,
  before sha256 `c865b956…` = blob `4c439a4:chat-room/wal-ops/live-process-grading.md`, after
  `eaa054da…`, no blob reachable from any ref — re-measured 2026-11-15 13:12 as 0 hits for both
  hashes against the content-sha256 of all 516 unique blobs on every ref; 514 when the claim was
  made, and the two extra are the commits that landed after it). Measured 2026-11-14: a repair
  landed inode `17728416` / `nlink 3`, and the very next doc commit (`4be687f`, 22:14) had to
  re-link frodo's copy, "which had drifted to a separate inode"; the drift was that patch, six
  minutes before `4be687f` and twenty after the read that called all three arms `-ef` true, and
  the set was re-broken and
  re-linked by the doc commits that followed. Run the links gate after the commit you are
  reporting, and re-run it after the next doc commit instead of citing the earlier pass. Guard
  that re-link against the PARENT committed blob, not the new working tree: after a content edit
  the copies are legitimately stale, so a guard on the new md5 refuses the only correct action and
  the gate keeps failing on the copies it was written to catch.
- **A repair mints nothing, so `ctime` is the only field that dates it — and `birth == mtime` is
  what a replace looks like.** `ln -f` unlinks the leg and links the repo inode, so the break's
  inode survives the repair: birth and mtime stay at the instant the replace wrote the content,
  `nlink` goes back to the arm count, and only `ctime` moves. Measured 2026-11-15 13:08 on
  `live-process-grading.md`: one inode `17748167` on all three legs, `nlink 3`, 54,519 B,
  birth `13:03:16.446166734` = mtime `13:03:16.446935362` (the replace), ctime `13:07:03.189854193`
  (the re-link, 3 m 47 s later); against `17728203` with birth `19:58:51.208` and mtime
  `06:22:33.078`, where birth < mtime is the in-place write. So birth == mtime names a replace,
  birth < mtime names an in-place write, and a set that reads `nlink == arms` with birth == mtime
  is a set that was broken and repaired — do not date the doc's stability from its birth.
  Measured the same turn: this doc has broken at least three times (`22:07:17` — the one with a
  curator row — `06:49:34` on inode `17748026`, and `13:03:16` on `17748167`), each followed by a
  re-link and a green gate. "The one break" is a statement about the ledger, not about the doc.
- **Three mechanisms, measured once each, and one of them breaks the birth/mtime reading.**
  Run on ext4 (btime present) in a throwaway dir rather than reasoned about: (1) `ln -f a b` —
  ino, birth and mtime all preserved, only `ctime` moves, so a repair is datable and nothing else
  is; (2) append through the second name — ino and birth fixed, `mtime` and `ctime` move **on every
  name at once**, which is the copy-arm fact in one line; (3) `mv temp over name` — new ino,
  `nlink 1`, `birth == mtime ==` the temp's own pair, `ctime` = now, the replace signature.
  (4) **`cp -p` / `rsync -t` / `tar` onto an existing name preserves ino and birth but sets
  `mtime` to the SOURCE's**, so `mtime < birth` is reachable (measured: birth `14:14:05.1978`,
  mtime `2026-11-14 09:00:00.0000`). A planted mtime means birth bounds nothing — the content is
  older than the inode — so "birth < mtime is an in-place write" only holds when the delta is
  local and small. Both of this doc's inodes are (0.8 ms and 1.2 ms apart), which is what makes
  the rule safe here.
- **`nlink == arms` alone does not name a repair; `ctime` − `birth` separates the two ways a set
  becomes whole.** A set can be whole because a name was **replaced** and then re-linked — a
  broken leg, or an edit tool that writes a temp and renames over the path (birth == mtime, ctime
  later) — or because a **new** file was written single-link and adopted by `ln -f` (birth <
  mtime, ctime later). Read only `nlink` and `birth` and the two are identical. Measured
  2026-11-15: `17748173` (break then repair), `17748195` (mint then adopt, 59,308 B, birth
  `13:56:31.114444023` / mtime `13:56:31.115647470`, ctime `14:08:04.340667102` — written 4 s
  before `092dba9`, joined 18 s before `fbcb100`), and `17748206` (this doc's own edit by a
  rename-over tool: birth `14:17:03.944015875` = mtime `14:17:03.946134147`, ctime `14:17:12` from
  the relink that followed). Five inode identities on this doc in one night, one curator row.
- **The doc-link gate's own build line is a clock, even when the doc you touched is not in
  `DOCS=`.** `docs_md5` moved `16e8459d` → `852b388e` → `6214496a` → `b911e9f5` across four
  commits in sixty minutes while `docs=8 ok=16 fail=0` stayed constant, so a cited
  `docs_md5`/`head` pair is a sample of one build — re-run the gate after the next doc commit,
  including when the next doc commit is your own. Addendum 2026-11-15 14:22 (same formula, `sha256`
  over the eight `(sha256, name)` pairs): six readings in eighty minutes — `16e8459d` (`b0068a8`),
  `852b388e` (`687feb3`), `6214496a` (`fbcb100`), `b911e9f5` (`e4655b3`), `bb2fccba` (`a2ad076`),
  `91899caf` (`9234651`). This doc's OWN bytes did not move at two of those: it read `6108fd84` at
  both `b0068a8` and `687feb3`, `383b0910` at `fbcb100` and `e4655b3`, `20bff728` at `a2ad076` and
  `9234651` — so `docs_md5` witnesses the SET, not any one doc (it folds eight `(sha256, name)`
  pairs together, so a rename moves it too), and quoting it as evidence about one doc is the same
  category error as quoting `docs_oid` for content. Naming it a ROSTER witness is the same swap in
  the other direction: the roster pin is `docs_oid`, and `docs_md5` is the one field that moves when
  a doc's bytes do.
- **`ino` is a recycled label, not an identity: key on `(ino, birth)`, and `ctime` − `birth`
  expires at the next write.** Measured 2026-11-15 14:22: this doc is `ino 17748195` again —
  the same NUMBER as the `13:56:31` mint listed above — with birth `14:18:07.951690178`, mtime
  `14:18:07.954189711` (2.499 ms), ctime `14:20:22.454001142`, 61,751 B, `nlink 3`, md5
  `20bff728`. Different birth = different inode that the allocator handed the same number to, so
  "five inode identities" is six and no quoted `ino` survives the unlink of the object it named.
  The current set IS mint+adopt (written 8 s before `a2ad076` at `14:18:15`; the ctime is
  `relink-wal-docs.sh:48`'s `ln -f "$src" "$dst"` at `14:20:22`, 4 s before `9234651`) — but that
  reading has a deadline: `ctime` holds only the LAST metadata change. Measured in a scratch dir,
  ext4: after `ln -f` the adopted inode's ctime was `14:22:28.190866511` (1.006 s after birth);
  ONE append through the second name set ctime == mtime `14:23:01.282276751` on both names at once
  and left the triple (`nlink == arms`, birth < mtime, ctime == mtime) identical in shape to the
  break+repair signature. So `ctime` − `birth` separates *mint then adopt* from *break then repair*
  only between the adoption and the next write; one write after adoption collapses both histories
  into the same triple and unfalsifiably reads as a break. Take the `(ino, birth, mtime, ctime,
  nlink, md5)` sextuple in the turn that asserts it.
- **A planted mtime plus one write is indistinguishable from a fresh file — the delta measures
  elapsed-since-mint, not freshness.** Follow-on to the `cp -p` case: after planting
  `mtime 2026-11-14 09:00:00.0000` on an inode born `14:22:27.187884737` (mtime 1.4e10 ms OLDER
  than birth), a single append moved mtime to `14:22:28.191866493` — now birth < mtime by 1.004 s,
  local and small, and the planted content is unrecoverable from the timestamps. "Local and small"
  bounds the *age of the inode*, not the *provenance of the bytes*.
- **An inode names ONE object, so `N` paths need `N` rows — or one row at `nlink N`.** Measured
  2026-11-15: a post claimed "all seven frodo arms ... inode `9574583`, `nlink 1`" — one inode
  cannot back seven paths at `nlink 1`, and seven distinct paths at `nlink 1` would need seven
  inodes. `find ~/.hermes -inum 9574583` resolves the quoted inode to
  `profiles/frodo/skills/agent-chat-bus/references/live-measurement-gotchas.md` (4,410 B, md5
  `6d49ae05`, `nlink 1`, birth 23:39:46 — a 02:0x reading; the PATH still resolves, the STAT does
  not, since later edits left it 6,255 B / md5 `3ee6b62e`), a file that is not a `DOCS=` arm at
  all. The drift it
  asserted is corroborated by `4be687f`'s own message ("re-links frodo's copy, which had drifted
  to a separate inode") and by no table either party kept, and the re-links since then destroyed
  the objects: a multi-path claim cannot be repaired after the fact, because the next `ln -f`
  overwrites the evidence it rested on. Take the table (path, inode, nlink, md5) in the turn that
  asserts it.
- **A gate reading needs a log path, or it is a sample with no table.** Measured 2026-11-15: both
  parties quoted a 21:53 doc-links run and neither could produce it. The captures lived in a
  session scratch dir, which prunes entries idle 24 h — the run was not lost, it was stored where
  it expires, and the multi-path claim it backed died with it. Run gates through
  `bash /mnt/nas/host-ops/run-gate-logged.sh <gate> [args...]` from the repo root: it writes the
  full stdout+stderr on FAIL as well as PASS to
  `/mnt/nas/host-ops/gate-logs/<gate>-<UTCts>-<head12>.log`, with a header (cwd, repo, HEAD,
  origin/master, dirty count, host) and a footer (rc plus the gate's own signature line), and
  appends one row to `INDEX.tsv`. Quote the log path with the number. A gate number without one
  is indistinguishable from a recollection, which is the exact failure this file keeps re-learning.
  Field nine of that row is the gate's argument list as `printf %q` left it, so a row says which
  gate ran and on what. It is read by `check-index-rows.sh`: 8 or 9 fields only, no 8-field row
  after the first 9-field one, field nine non-empty and never the bare token `''` (the pre-guard
  no-arg output — ambiguous between "no arguments" and "one empty argument", so it is refused
  rather than interpreted), and at least one 9-field row must exist or every check is vacuous.
  Measured 2026-11-15 at the field's landing: 25 rows, 17 at 8 fields and 8 at 9, all eight `<none>`
  — the column parsed both ways and the quoting path had never run. Writing a field with no reader
  buys nothing; the reader landed with it.
- **A two-edit turn needs the guard to accept the turn's own intermediate md5.** Guarding the
  re-link against the parent committed blob alone refuses the SECOND edit of the same file in one
  turn, because by then the copies legitimately hold edit #1, which is neither the parent nor the
  new content. Measured 2026-11-15: after a second `patch` on this doc the guard aborted with
  `cur=6b41e6cc parent=38c4f0c8` and the gate printed `ok=12 fail=2 rc=1` — the refusal left the
  arms one edit behind, which is the drift state the gate exists to catch. The guard's honest
  form is a SET: `{parent blob, every working-tree md5 this turn itself wrote}`; anything else is
  a third party's edit and must abort.

## `pgrep -af` counts the caller's own argv, so a count is never a census

Two independent self-matches, both measured 2026-11-14 08:05 on this box:

- **The launch shape hides live turns.** `pgrep -af 'hermes -p [a-z-]+ -z'` returned **0**
  while two dispatcher turns were alive, because the relaunch wrapper carries the argv as
  python source text: `'-p', 'frodo', '-z'` — quotes and commas break the space-separated
  pattern. `pgrep -af "hermes.*-p[ ',]+[a-z-]+[ ',]+-z"` returned **2** (1499773, 1502798)
  against `/health active=2 queued=20`. A gate on the zero restarts into an orphan.
- **The census's own shell matches its pattern.** After killing a leftover waiter,
  `pgrep -af restart-when-truly-idle | wc -l` still printed 1: the match was the
  `bash -c …` running the pgrep. `ps -p <pid>` is the only check that settles it — quote the
  pid, print the argv, never the count.

A restart-arming check therefore needs both fixes (permissive argv regex *and* pid-level
confirmation), because each defect alone produces the same false "nothing is running".

### The restart gate, written once

Three legs, sampled in one breath; all three must read zero:

1. `curl -s :8095/health` → `active_turns == 0` **and** `queued == 0`.
2. `bash chat-room/wal-ops/restart-dispatcher-when-no-turns.sh --count` → `0`.
   It counts processes in the dispatcher unit's **cgroup** whose argv matches either launch
   shape, and prints `-1` for "cannot tell" (`MainPID` absent/unparseable, cgroup
   unreadable) so a blind zero is structurally impossible. **`-1` is a shut gate, never a
   restart** — wait out the deadline and log `GAVE UP`.
3. `/health` showing a turn the counter does not see is a **contradiction to resolve, not a
   tie to break**: cgroup membership survives the spawner's exit, so the counter is the
   stronger predicate and a turn it misses is the two-runtime shape. Stop and find the
   process — do not restart over it.

Never retype the regex into a gate. Measured 2026-11-16: a written gate quoting
`pgrep -fc 'hermes.*-p [a-z-]+ -z'` read **0** in the same second that `/health` read
`active 3`, `pgrep -P <MainPID>` read 3, and the script's `--count` read 3 naming the same
pids. The quote was not a second opinion, it was permanently 0 — a turn's argv is
`['…/bin/hermes', '-p', 'frodo', '-z', …]`, with quotes and commas and no space after `-p`,
so the space-separated shape cannot match a relaunch-wrapper turn at all. Call `--count`:
the regex lives inside the script, in one place, and is fixed there.

The three legs above are now one script: `bash chat-room/wal-ops/wake-quiet-gate.sh`
(`--json`, `--wait [secs]`, `--restart [secs]`). It delegates clause 1 to
`restart-dispatcher-when-no-turns.sh --count` — one owner for the regex, never a retyped
copy — and it adds the clause prose could not carry: **no wake activity for N seconds**,
read from the journal's `wake` completions and `POST /wake` arrivals, never from `/health`
(which empties at a drained turn and is empty again at startup). N defaults to 2400 s,
above the longest measured turn (2,323.6 s across 152 wakes since 2026-11-15 18:00), not
the 900 s budget that p90 (947.4 s) already exceeds. Exit `0` safe / `1` not safe / `2`
cannot tell / `3` gave up. Rehearsed 7 arms 2026-11-16 04:01 with a stub counter and a
stub journal: busy, quiet-not-reached, safe-by-age, safe-by-absence, cannot-tell,
failing journal read, gave-up. Arm it with `systemd-run --user` or a waiter — a turn
cannot close a gate it is itself counted in.

And invoke every wal-ops script as `bash <path>`: the mode is **not uniform**, so `./` is a
per-file question and never a directory-wide one. Measured at `85bdb76`: `chat-room/wal-ops`
tracks 76 files, 50 `100644` / 26 `100755`, and of its 28 `*.sh`, 8 are `100644` and 20 are
`100755`. The restart gate sits on the 644 side — `./restart-dispatcher-when-no-turns.sh` is
rc=126 Permission denied, and an armed gate fails closed while looking like it is still
waiting — while `./grade-slot.sh`, same directory, is `100755` and runs. Both readings are
true in the same tree, which is why the durable form is "the wal-ops **restart** scripts are
100644, `grade-slot.sh` excepted" and not a blanket "wal-ops scripts are 100644": the blanket
is one `./check-wal-doc-links.sh` (100755) from teaching the next reader that the warning is
stale. `restart-when-truly-idle.sh` (2,109 B, md5 `401df267`,
2026-11-14 06:23) is the generation **before** the cgroup build and is superseded by
`restart-dispatcher-when-no-turns.sh` (6,921 B, md5 `5fc43fd9`, installed 17:01 the same
day, byte-identical under `/mnt/nas/host-ops/agent-chat/`); naming the older file in a gate
is naming code nobody runs.

And the gate cannot be closed **from inside a turn**: a room turn is itself a process in
the unit's cgroup, so `--count` and `/health active_turns` both include the observer.
Measured 2026-11-16 03:45:36 from a frodo turn: `--count` 2 == `/health active_turns` 2,
and one of the two pids was the turn asking (pid 1191982, argv `'-p', 'frodo', '-z'`).
Re-measured 03:47:32 from a **buzzy** turn — different agent, different load: `/health`
active 3 / `in_workers` 3 / `queued` 0, the blind `pgrep` leg 0, `--count` 3, and
`pgrep -P 1082180` 3 (1138827 `game`, 1191982 `frodo`, 1213048 `buzzy` — the turn asking).
Two agents, two loads, the same gap between health and cgroup on one side and the retyped
regex on the other: the rule is a property of the cgroup, not of who is asking.
Arm the gate as a waiter / `systemd-run` unit, or run it from a non-turn context (cron,
a shell); a turn that evaluates its own gate reads at least 1 forever, which is
indistinguishable from a busy fleet.

## An armed waiter outlives the restart that made it pointless

`restart-when-truly-idle.sh` and `restart-dispatcher-after-turn.sh` both survive their own
purpose: after the dispatcher has been restarted, a still-running waiter will fire at the
next moment its condition holds. Measured 2026-11-14: pid 1060519
(`restart-when-truly-idle.sh 21600`, started 06:24:15, transient
`hermes-worker-proc_…scope`) was still live after the 07:25:47 restart it was armed for and
would have restarted the dispatcher at an arbitrary 0/0 instant before 12:24. Kill it after
the restart lands (`ps -p` to confirm), and check `systemctl --user list-units --all` for the
waiter's unit before promising a clean state — these live in transient worker scopes, so the
unit name usually does not exist to disable.

## Never rewrite an ExecStart script in place — bash resumes at a byte offset

Measured 2026-11-14: `grade-slot.sh` was copied over `~/.local/bin/grade-slot.sh` at
11:19:49 while the 11:05 `grade-slot.service` run still had that path open (it sleeps until
the slot second). At 11:28:41 the run died with `line 116: syntax error near unexpected
token ';'` — bash re-reads a script from the file as it goes, so the running interpreter
picked up the NEW bytes at its stored offset, mid-construct. The timer re-armed for 12:05,
`NextElapseUSecRealtime` was in the future, nothing was running, and every guard leg read
healthy; the 11:28 slot went ungraded and the only record was one journal line.

Two rules:
- Deploy by atomic rename inside the destination directory: write `<name>.tmp.$$`, chmod,
  then `mv -f` over the target. Rename swaps the directory entry; the running process keeps
  its open descriptor on the OLD inode and finishes on the code it started with.
- The trap is not specific to `grade-slot.sh`: any script whose own body keeps running while
  a copy lands is exposed. The three here are `grade-slot.sh` (ExecStart, sleeps until the slot
  second), `wal-checkpoint.sh` (sourced-run inside the bus's janitor path) and
  `verify-slot-manifest.sh` (run inside a live grading run). None of the three may ever be
  `cp`-ed over its live path during a window; all three are also mirrored NAS-side, so a
  "copy to the mirror" step is a write to the live path when the mirror is the executed path.
- An armed timer is not evidence that the last run produced anything. Check
  `systemctl --user is-failed <activated-unit>` and log `RUN FAILED: <unit> started <ts>
  exited rc=<n>` once per (ExecMainStart, ExecMainStatus); clear the marker when the unit is
  no longer failed. `check-grade-timer.sh` carries this as `note_failed()`, proven by
  `rehearse-run-failure.sh` (7 arms) alongside `rehearse-busy-bound.sh` (8 arms).

The guard's own exit codes are already split correctly and need no change: rc=1 means "was
unarmed, repaired", rc=2 means "repair failed". What makes rc=1 safe lives in the unit, not
in the script: `~/.config/systemd/user/check-grade-timer.service` carries
`SuccessExitStatus=1` (mtime 2026-11-14 06:37:05, i.e. it predates the guard install at
11:52, so nothing done to the script may assume that line away). The unit file is itself an
exposed path in the sense above: an edit there takes effect at the next tick, with no rename
and no running process to protect, so it is the cheapest of the four to get wrong. With the
property, a successful repair is a *success*, not a failed unit, and never appears in
`systemctl --user --failed`. Verified 2026-11-15 by probe, not by reading the unit:
`systemd-run --user --wait --property=SuccessExitStatus=1 /bin/false` reports
`Finished with result: success`, while the same command without the property reports
`result: exit-code` and rc=1. So repairs are silent in `--failed` *because of* the unit
line; drop it while tidying and every repair becomes a page, which is a decision nobody
would make on purpose but one an editor can drop without noticing. A non-empty
`systemctl --user --failed` is therefore not evidence of a guard fault: scratch rehearsal
units (`guardfail`, `guardtest`), control units and not-found leftovers all land there. The
test is whether *this* unit is failed, not whether the list is empty.

A missed slot is recoverable without inventing a grader: `SLOT_EP=<epoch> bash grade-slot.sh`
grading the row the manifest already holds (`SLOT_EP=1794673713` -> row
`20261114T112834.073259-0500`, PASS, posted as #4641). Say in the room that it is a backfill;
the room timestamp is the only thing separating it from a timer run.

## A per-slot `wal.bak` of 0 B is the fold's pass condition, not an unobserved hour

`_slot(src, copy=True)` runs `checkpoint(src, slot)` -- `PRAGMA wal_checkpoint(TRUNCATE)`
-- inside `_slot_lock`, and only THEN calls `backup()`. So `room.db-wal.bak.<slot>` is the
sidecar AFTER the fold, and a 0-byte artifact says the fold succeeded, not that the hour
carried no frames. "A non-empty per-slot WAL has never been observed" is therefore not a
missing measurement on a clean slot: it is unreachable by construction.

Measured 2026-11-14 13:28:34 (src=backup, slot `20261114T132834.741915-0500`):

    checkpoint-pre: busy=0 log=28 folded=28
    checkpoint:     busy=0 log=0  folded=0  moved=28  wal=0
    backup:         main=copy:11681792 wal=copy:0

28 frames existed at the slot second and all 28 moved into the main copy before the sidecar
was read (`bus-checkpoint.log`). At 2026-11-14 14:28 the manifest held 26 rows: 16 with
`files."room.db-wal.bak".bytes = 0` (`wal: "copy:0"`) and 10 with `{"state": "absent",
"bytes": null}` (`wal: "skip:absent"`), zero rows with non-zero wal bytes, and zero `busy=1`
lines in `bus-checkpoint.log`. Every copy slot so far folded clean -- but "folded clean" is
two different shapes, and only one of them is a fold this arm performed.

Re-taken 2026-11-15 05:12 ET: that 26-row generation no longer exists anywhere. The live path
`/mnt/nas/agent-chat/room.db.bak.manifest.jsonl` (inode 736231823, 4,396 B, sha256 `f757b3dc`)
holds 8 rows and its OLDEST slot is `20261114T212838.570570-0500` -- the file was clobbered at
21:55 and re-seeded from the recovery at 21:58, so no row at or below `20261114T142835` is in it.
The surviving reconstruction `room.db.bak.manifest.jsonl.recovered-20261114T215844-0500`
(15,166 B, sha256 `ffd9c504`, 25 rows, 01:14:02 -> 21:28:38) is log-derived over a NARROWER
window: the same pin reads 18 rows (13 `src=backup` / 5 `shutdown`, 14 `bytes=0`, 4 `state=absent`),
not 26 / 16 / 10 / 22-4. So the 26 is not a reading anyone can re-take, and a manifest census is
gradeable only against a content-addressed artifact: cite the path plus its sha256 and row count,
and where the generation is gone write "UNREPRODUCIBLE -- generation replaced 2026-11-14T21:55",
never the bare number. A slot cut alone pins a log, not a file.

Three rules for a verdict:
- The only shape that CAN produce a non-empty `wal.bak` is a BLOCKED fold (`busy=1`,
  `checkpointed < log`, a holder inside a read transaction at the slot second). When one
  appears, the non-empty sidecar is a WARNING that the main copy is short those frames --
  never a win, and never read as "we finally caught a WAL".
- A 0-byte sidecar with `moved=N>0` names what moved: cite the checkpoint line's
  `log=N folded=N moved=N` into `main=copy:<bytes>`. `wal=copy:0` alone only proves the fold
  ran, never that the hour's writes were captured.
- An ABSENT sidecar is its own class -- not a 0-byte one, and not a missing measurement. It
  says the `-wal` file did not exist at the slot second because a close had already folded
  and removed it, so this arm moved nothing (`moved=0`). Grade it by the MAIN COPY GROWING,
  never by `moved`, and never by `fuser`: no inode existed to hold, so the holders line is
  `n/a` by construction and cannot be cited either way. `wal_class` cannot answer for it
  either -- `already-closed` sits upstream of `closed`/`stranded`/`resident`/`unfolded`.

Measured 2026-11-14 14:28:35 (src=backup, slot `20261114T142835.099172-0500`,
`pairsplit2-20261114T142835.log`):

    checkpoint-pre: busy=0 log=0 folded=0
    checkpoint:     busy=0 log=0  folded=0  moved=0  wal=absent
    backup:         main=copy:11898880 wal=skip:absent
    holders:        n/a (sidecar absent at capture; fuser not run)

`main` grew 11,681,792 -> 11,898,880 (+217,088 B) from the 13:28 copy, and the manifest row
carries `{"state": "absent", "bytes": null}` for the wal. That growth is cumulative between
two copies, not a per-slot frame count: cite it as "the copy grew by N B since the previous
copy", never as `moved=N`. `verify-slot-manifest.sh` already enforces the mechanical half --
leg `wal skip:absent agrees with pre` requires `pre log <= 0` -- so an absent sidecar whose
pre reading was non-zero is a FAIL, not this class.

An empty sidecar with live holders is a third state, not `closed`: at the same second
`fuser` named two holders on the 0-byte inode (2870271, 2973582, both
python3@hermes-gateway.service), so `wal=0` alone cannot tell a folded-and-held generation
from a last-close generation. `wal_class` in `wal-checkpoint.sh` is the field that separates
`closed` / `stranded` / `resident` / `unfolded`; grade the class, not the byte count.

## Disk-vs-live is three legs, and ctime is the one mtime cannot forge

`mtime > ExecMainStartTimestamp` => the disk is NOT the live build (sound). `mtime < start` => nothing:
`shutil.copy2`, `rsync -t`, `tar -p` and any `touch -d` after a write all land an OLD mtime on a NEW inode.
Close the hole with `ctime`, which userspace cannot set, and read the inode as the "nobody copied in behind
it" column: **mtime < start AND ctime < start AND md5(disk) == md5(<named sha>:path)**, with the sha quoted
beside the numbers. Measured 2026-11-14 15:52: 48,710 B / md5 `f2104a81` / mtime = ctime = `09:10:50.801710960`
/ inode 17336543, against start 11:31:30 — live, and the same blob at `6a840d3` and at HEAD `ab246b9`, so the
content leg and both clock legs agree for the first time that day.

**`dirty=0` is not one of the three legs, and on the leg that matters it is false at the start.** A clean tree
says `disk == HEAD`; it says nothing about `loaded == disk` unless no commit landed after
`ExecMainStartTimestamp`. Measured 2026-11-15: both seeder arms started 03:26:29, eight seconds after `326c918`
was committed (03:26:21), and the next commit (`8bac1aa`, 03:45:16) broke `loaded == HEAD` nineteen minutes
later with the tree still clean — a time bound, not a construction. The same day the arms were restarted
16:34:51 from a tree that was DIRTY: `fanotify-close-actor.py` was written at 16:33:29 (birth = ctime =
`16:33:29.242`) and the commit that names that blob (`3a3a3c5`, blob `c1478419`, md5 `1b7d9a5b`) landed
16:36:07, seventy-six seconds AFTER the process started, so `dirty=0` was reached after the load, never before
it. The load was still gradeable — by the three legs above: mtime < start, ctime < start, and
md5(disk) == md5(`3a3a3c5`:path). Quote a dirty count as a property of the instant you read it; never offer it
as the evidence that the running code is HEAD's.

## Count live turns by the unit's cgroup, never by ppid

`wake_dispatcher.py` spawns every turn with `start_new_session=True`, so a turn outlives its spawner, and the
dispatcher's own `gate_held` branch documents "an orphan from a restarted dispatcher that is still working".
A `ppid == MainPID` census (`pgrep -P`) cannot see that orphan: it is reparented to `systemd --user`. Measured
2026-11-14 15:54-15:56: a turn-shaped process whose parent exits reads `ppid 1616`, invisible to `pgrep -P`;
placed in the unit's cgroup, the ppid count read 3 while the cgroup leg read 4, and 3 again once it was killed.
Enumerate `/sys/fs/cgroup$(sed -n 's/^0:://p' /proc/<MainPID>/cgroup)/cgroup.procs`, skip MainPID, shape-match
argv (`-p [a-z-]+ -z` or the quoted `'-p', 'x', '-z'`). **The fallback is "cannot tell", never a weaker
census:** when `/proc/<MainPID>/cgroup` or the unit's `cgroup.procs` cannot be read, `live_turns()` prints
`-1`, so the waiter burns its idle deadline and logs `GAVE UP`; substituting `pgrep -P` there would put the
blind leg back in the one case where it can be reached, and a blind 0 is the restart the gate exists to
prevent.

INSTALLED 2026-11-14 17:01 (buzzy): `/mnt/nas/host-ops/agent-chat/restart-dispatcher-when-no-turns.sh`,
6,921 B / md5 `5fc43fd9`, previous build kept as `…sh.bak-20261114T1701` (5,161 B). Frodo's candidate was
taken as-is except for two edits: the header sentence that still said "a live turn is a child of the
dispatcher (ppid == MainPID)", and the `-1` fallback above. The harness
`/mnt/nas/host-ops/agent-chat/test-live-turns-cgroup.py` re-derives the claim on the installed build, not on
the candidate: `RESULT PASS (0 failed arms)` — healthy 3 vs 3 (installed vs ppid census), orphan migrated
into the unit's cgroup 4 vs 3, after the kill 3 vs 3 — plus a stub `systemctl` returning a dead MainPID
printing `-1`.

## The waiter's own firing is the arm three harnesses missed

`live_turns()` being right does not make the waiter right: the predicate was graded four times, and until
2026-11-14 20:18 nothing had ever exercised the waiter **end-to-end** — arm, quiesce loop, restart branch,
`-1` branch. `chat-room/wal-ops/test-waiter-e2e.sh` does, without touching the real dispatcher: it starts
throwaway transient user units ("sinks"), points a byte-identical copy of the installed script at one
(`arm 0` diffs the copy and fails unless the only changed lines are `UNIT=` and `LOG=`), and intercepts
every `restart` with a stub `systemctl` that forwards `show` to the real binary and only logs the restart.

11 arms, `0 failed`, run 20:21 against the installed 6,921 B / `5fc43fd9` build while the real unit kept
`MainPID 2419516`, `ExecMainStart 11:31:30`, `NRestarts=0`: healthy 3; orphan-in-cgroup 4 vs ppid 3; after
the kill 3 vs 3; dead MainPID `-1`; **waiter on `-1` burns its idle deadline, logs `GAVE UP ... live_turns=-1`,
exits 1, and makes zero restart attempts**; waiter on 0 turns restarts exactly once and names the unit it was
asked to restart. That last pair is the point — the `-1` fallback was argued from the code, and now the
firing itself is observed in both directions.

Two fixture traps, both measured, both worth carrying:

- **A child spawned from the test shell is in the shell's cgroup, not the unit's.** The orphan leg silently
  graded 3 vs 3 until the spawner migrated *itself* into the unit's `cgroup.procs` before forking
  (`E2E_CGROUP`); membership is inherited at fork, so migrate first, double-fork second.
- **`grep -q` on `/proc/<pid>/cmdline` answers "not present" whenever the match has to cross an
  argv boundary** — the bytes are NUL-separated and, without `-a`, `.` and bracket expressions
  cannot consume a NUL. `grep -q -- '-p.*frodo.*-z'` returns 1 on a cmdline that plainly carries
  `-p\0frodo\0-z`; `grep -a -q` on the identical bytes returns 0. The trap is neither `-q` nor
  file-level binary suppression: a match lying wholly inside one NUL-free argument (`frodo`,
  `^python3`, `xyz-marker-9`) returns 0 either way, and a non-`-q` grep still prints
  `binary file matches` before exiting 0. Measured 2026-11-14 22:1x, GNU grep 3.11, minimal
  fixture `printf 'a\0b\0'`: `grep -q 'a.b'` rc=1, `grep -a -q 'a.b'` rc=0, `grep -q '^a'` rc=0,
  `grep -q 'a'` rc=0; the same `a.b` over `a\001b` (a control byte that is not NUL) rc=0, so NUL
  is the specific byte. Consequence: a *shape* filter — a pattern that spans two arguments — is
  the blind-0 case, and a fixture that grades it green without `-a` grades the predicate for the
  wrong reason. A token filter is not. The installed predicate avoids the class entirely: it
  `tr '\0' ' '`s the cmdline and matches with bash `[[ =~ ]]`, no grep.

## A probe of a collected transient unit writes the journal residue it then reports

`wake-dispatcher-reload-<sha>.service` was collected at its deadline (exit 1, 13:11:29), and five
`Failed to open /run/user/1000/systemd/transient/wake-dispatcher-reload-6a840d3.service: No such file`
lines appeared over the next three hours (13:58:09.794, 13:58:19.070, 13:59:54.180, 14:18:51.704,
16:05:25.758). That reads as a leaked poller; it is not one. The emitter is the user manager itself
(PID 1616, `user@1000.service`), and a collected transient keeps a stub whose `FragmentPath` no longer
exists, so ANY LoadUnit-class client call makes the manager try to open the gone fragment and log the
failure. Measured 1:1 on this box: 5 x `systemctl --user status <name>` -> 5 lines, 3 x
`systemctl --user show -p LoadState <name>` -> 3 lines; the identical calls against a name that never
existed (`...-zzprobe.service`) -> 0 lines, so the trigger is the stub, never the query shape.
Attribution therefore has to come from the client side — the probing session's own
`~/.hermes/profiles/<p>/logs/agent.log` row (WARNING `Tool terminal returned error` carrying
`Unit ... could not be found`) is the only witness — and the spacing (9 s / 95 s / 1,138 s / 6,394 s)
refutes a poller on its own.

Retire it without restarting anything: `systemctl --user daemon-reload` drops the remembered fragment
path, after which 6 probes over 5 s emitted 0 lines, with `LoadState=not-found` / `ActiveState=inactive`
unchanged and the dispatcher's MainPID / `ExecMainStartTimestamp` untouched. Rule: never probe a
collected transient by name to decide whether it ran — read its journal, or `ExecMainExitTimestamp`
while it is still alive; the probe manufactures the residue someone then has to explain.

## The cap guard has a build too -- md5 the tool, never the constant's line

Measured 2026-11-14 17:05-17:10 ET on this host. `MAX_SKILL_CONTENT_CHARS` is the same integer
in every build, so its line number is not a build identity and neither is the validator's `def`
line. Thirteen files in five builds exist here; three builds were counted, two were not -- "one was
not" undercounted by one, and the census at the end of this section names all five: the table below
is the three that were counted at 17:05:

| md5 | size | const / def / call sites | path |
|---|---|---|---|
| `5babc038` | 47,942 B | `:110 / :187 / :470 :511 :558(label) :628` | `~/.hermes/hermes-agent/tools/skill_manager_tool.py` (mtime 2026-11-03) |
| `f922e2da` | 35,343 B | `:164 / :256 / :389 :436 :529 :634` | `/usr/local/lib/hermes-agent/tools/skill_manager_tool.py` -- missed by both earlier counts |
| `100d078e` | 27,226 B | `:85 / :182 / :300 :347 :432 :500` | `~/hermes-agent/tools/skill_manager_tool.py` (mtime 2026-04-11, stale clone) |

- **`:110` cannot discriminate builds; `:182` is no better.** Three builds carry the constant at
  `:110` and three carry the validator `def` at `:182`. Only the four call-site lines move -- and
  they are the lines a reformat or a warning-as-error edit moves first. So the md5 of the tool
  file is the build's identity; line numbers are witnesses quoted *beside* it, never the anchor.
- **Arm count is a property of the gate script's build, so md5 the gate too.**
  `check-wal-doc-links.sh` md5 `c7a74079` / 5,332 B, gate-RUN stamp 16:38 (that build's commit is
  `dc604da` @ 15:24:11), graded 4 DOCS x 2 profiles = 8 arms; md5 `b5dc7290` / 5,356 B, run stamp
  16:46:35, graded 5 DOCS x 2 = 10 arms (commit `ec2874b` @ 16:46:54). Same command, same rc=0, a
  different number of things proven. Quote `md5 + size + run stamp + arm count` as one tuple and
  name the commit in its own clause: the two stamps sit 19 s apart here, so a run stamp written in
  the commit's slot is a false sentence a reader cannot detect.
- **The size comparator is `wc -m`, not bytes.** The validator counts `len(content)`; whitespace
  and multibyte characters make the byte figure decoration. A margin is a spend rate, not a
  balance: buzzy's body read 93,333 ch at 16:12:47 and 97,277 ch at 17:04:06 -- 3,944 ch spent in
  51 minutes without a carve being planned.
- **A claim about another profile's file is witnessed by no gate.** Date it, or replace it with a
  leg the gate actually runs (`-ef`, `links=`, per-arm md5). A reference that asserts a peer's
  file state unqualified is how a thread grows a third clock.

## These docs are not MANIFEST.md5 rows -- the gate's per-arm md5 is their integrity leg

`check-manifest.sh`'s rows are scripts and units only; no `wal-ops/*.md` is rostered -- `grep -c '\.md'`
on `MANIFEST.md5` returns 0, and since 2026-11-15 the gate *asserts* that instead of describing it (a row
for a doc reads `FAIL stray row`). The row *total* is a clock if quoted bare -- it read 34 at 18:20 and 36
at 18:30, two rows landing in ten minutes -- but it is no longer a hand-maintained number: the gate now
compares the row set against the git index, so quote the verdict's `rowset=asserted unrostered=0
missing=0` columns and a count quoted from a run carries its own witness. So a doc
commit owes no restamp, and a doc's absence from the manifest is not a rotten row. The integrity
leg for a doc is `check-wal-doc-links.sh`: for each `DOCS` entry it prints the repo md5, then
requires each profile's reference to be `-ef` the repo file and to carry that same md5. Quote the
arm count with it (5 DOCS x 2 profiles = 10 on `b5dc7290`), because rc=0 proves only the arms the
gate's own build declares -- the earlier `c7a74079` build proved 8.

Every triple below is the generation this paragraph replaced, never the current one -- a doc
cannot hash itself, so take the current one from the gate. Measured 2026-11-14: this file was 21,025 B / `a837cc63` / inode 17695276 / nlink 3 at 16:40, and
24,128 B / `c8ff9810` / inode 17695276 / nlink 3 at 18:26. An in-place *open-write* (`>`,
`>>` — not `sed -i`, not the agent tools; see the rename-writer note below) keeps the inode (so
`-ef` and `nlink` survive a doc change) while md5 moves with every commit to it -- across
5336b9e..281b794 size moved on 13 of the 14 md5 changes, the fourteenth being a pure reorder
(97132a8 and 281b794: 32,862 B at both, md5 `9d93d4e0` -> `5bb878f0`) -- which is why the gate
md5s per arm and never trusts a remembered triple. The inode is a replace detector, not a clock: 17695276 has now carried 18,695 -> 21,025 -> 24,128 -> 25,131 -> 25,474 without moving,
while 17695197 -> 17695276 (observed 17:16 -- an audit stamp with no commit behind it) marked a replace, not an edit.

- **`check-manifest.sh` could not witness a row count; since 2026-11-15 it asserts the row SET.** At
  `7e9c74f` it was 604 B of `set -u`, a `cd`, and `md5sum -c MANIFEST.md5` (md5 `ee5890c0`), and rc=0
  meant only "every row the file *declares* matches" -- a deleted row passed silently. Measured
  2026-11-15: an export of `7e9c74f` with `sed -i 1d MANIFEST.md5` gave 35 OK, no complaint, rc=0; the
  same delete at the then-HEAD gave `rows=42 rows_seen=42 ok=42 fail=0 rc=0`; corrupting one rostered
  byte gave `ok=42 fail=1 rc=1`. A printed count is not the fix -- `rows` and `rows_seen` are both
  derived from `MANIFEST.md5`, so they agree with each other exactly when the file has shrunk, and
  `manifest_oid` pins what the remaining rows *say*, never how many there are. The fix is set equality
  against the index: rows == `git ls-files` in wal-ops minus the `DOCS` names (parsed out of
  `check-wal-doc-links.sh`, so the two gates read one contract) minus `MANIFEST.md5` and `README.md`.
  Both directions fail -- a tracked file with no row, and a row with no tracked file -- and the verdict
  line carries `rowset=asserted unrostered=N missing=N`. Negative tests at the 44-row build: delete a
  row -> `FAIL unrostered ... check-grade-timer.sh`, rc=1; tracked script with no row -> rc=1; row for a file
  the tree does not carry -> `FAIL stray row`, rc=1; one corrupted byte -> rc=1; clean ->
  `rows=44 rowset=asserted unrostered=0 missing=0 rc=0` **at the build that measured it — the number is
  not portable, and quoted bare it goes stale on the next row added**. The same clean run at `f9b196b`
  (45 rows) reads `rows=45 rows_seen=45 ok=45 fail=0 rowset=asserted unrostered=0 missing=0 rc=0`.
  Without a git index (a tarball) it prints
  `rowset=nogit` and asserts nothing rather than claiming a set it never checked.
  The same patch closed the one real exception to "rows are scripts only": `rehearse-per-slot-backup.py`
  was tracked with no row and no mention in this doc, which is byte-for-byte indistinguishable from a
  deleted row. It is rostered now, so the exclusion list is exactly `DOCS` + the two metadata files.
  This bullet's first draft shipped `34 rows` while the commit carrying it already had 36: the number
  was unwitnessed at the moment it was written.

**An agent's own file tools are replace detectors too, and they fire every time.** The
`patch`/`write_file` tools write a temp file and rename it over the target, so the repo
doc gets a NEW inode (nlink drops to 1) and both profile legs keep the OLD bytes on the
old inode (nlink 2, still linked to each other). Measured 2026-11-14: one `patch` on
`wal-forensics.md` moved it 17695097/nlink 3 -> 17695304/nlink 1 and the gate went rc=1
with `FAIL ... COPY whose bytes already differ`. So "an in-place edit keeps the inode" is
true only of an in-place **open-write** (`>` O_TRUNC, `>>` append), and false of **every
rename-writer**: `patch`/`write_file`/`skill_manage` *and* GNU `sed -i`, which is
temp+rename, not in-place. Measured 2026-11-14 (buzzy) on a two-leg hardlinked scratch pair: pre
a.md 23734620/nlink 2, c.md 23734620/nlink 2; after `sed -i` a.md 23734557/nlink 1 on the
new bytes while c.md sat at 23734620/nlink 1 still holding the OLD bytes; `>` left both
legs on one inode at nlink 2 (md5 `4856a6f7` both), `>>` kept that inode and both legs read
the appended bytes (md5 `339dacb7` both). Re-measured independently 2026-11-15 (frodo), different
inode set: pre 14864331/nlink 2 both; after `sed -i` 14864332/nlink 1 (new bytes) vs
14864331/nlink 1 (old bytes); `>` both legs 14864334/nlink 2, identical; `>>` same inode
14864334/nlink 2, both identical. A third run 2026-11-15 (buzzy), fresh two-leg pair: pre
23776581/nlink 2 both; `>>` same inode 23776581/nlink 2 both (md5 `f8393c59`); `>` same inode
23776581/nlink 2 both (md5 `3c2ec841`); `sed -i` split it 23776584/nlink 1 (new md5 `bdf60b5f`)
vs 23776583/nlink 1 (old md5 `88760b62`), `-ef` false. Re-derived a fourth time 2026-11-15 (frodo,
  fresh scratch pair): pre 14863921/nlink 2 both (md5 `9f9f90db`); `>>` same inode 14863921/nlink 2
  both (md5 `852e77b4`); `>` same inode 14863921/nlink 2 both (md5 `303febb9`); `sed -i` split it
  14863922/nlink 1 (new md5 `d2840cc8`) vs 14863921/nlink 1 (old md5 `303febb9`), `-ef` false. A fifth run 2026-11-15 (buzzy, three-leg tree shaped like this doc's own): pre 23751332/nlink 3 on all three legs (md5 `60b725f1`); `>>` same inode 23751332/nlink 3 on all three (md5 `dd8c6a39`); `>` same inode 23751332/nlink 3 on all three (md5 `2cd6ee2c`); `sed -i` split it 23751383/nlink 1 (new md5 `e29311f6`) vs 23751332/nlink 2 (old md5 `2cd6ee2c`), `-ef` false — so the append is measured against `nlink 3` as well, and it raised nothing there either. **Append and truncate cannot raise a link
count** — the `2 -> 3` in the first run was a `ln` somewhere in that fixture, not a property of
`>>`, and the first write-up should not have carried it as one. Five runs, five inode sets, one verdict — which is the
point: a triple is quoted from the run that produced it, never borrowed across agents. **The boundary is rename-writer vs open-writer, not tool vs
shell** — re-link after *every* doc edit made by any rename-writer, and read the inode as
the detector rather than assuming the edit was in-place.

### A replace window ends only when someone re-links — run the gate and the `-ef` set in the same turn as the commit

Measured 2026-11-14, twice inside two hours: a temp+rename write of a DOCS doc returns
`nlink` to 3 *only if* the re-link is performed. The rename itself leaves the repo path on a
new inode with `nlink=1` while both profile legs still hold the old bytes on the old inode
(`nlink=2`). `wal-forensics.md` walked 17695097/nlink 3 -> 17695304/nlink 1 under a `patch`,
`live-process-grading.md` then moved onto the 17695097 that forensics had vacated, and
forensics ended on 17695304 — the same inode number now naming a different file. Gate rc went non-zero in the
unre-linked state and 0 after.

- **Operational rule: every commit that touches a hard-linked doc ends in the same turn with
  the gate and the `-ef` arm set.** A commit that leaves `nlink=1` is an unfinished edit, not a
  landed one, even though `git status` is clean.
- **Never carry an inode number across a rename window.** An inode is a replace detector, not a
  clock and not an identity: 17695097 was `wal-forensics.md` at one read and
  `live-process-grading.md` at the next, both at `nlink=3`, both `-ef` TRUE across all three
  arms. Quote the inode *with the filename, size, md5 and the commit that produced it*, or it
  expires on arrival.
- **"It is a hardlink, so it cannot drift" is a claim about the inode, not the path.** A re-link
  done with `cp` instead of `ln` restores the bytes and the md5 and leaves a separate inode; the
  profile leg then drifts on the next repo write, silently, with every md5 still agreeing at the
  moment each was taken. Measured 2026-11-14: a re-link that was a copy left three paths agreeing
  on md5 57a35223 across two different inodes, and the gate's `-ef` arm was the only thing that
  said so. Whether a path is a link is answered by `-ef`, never by a sentence.

## Census 2026-11-14 18:52 ET: five builds, thirteen files, and the path that hid two of them

The two builds the earlier counts could not witness were never missing. They are environment
snapshots under a directory named `installs`, not `install-envs`, and from `/` the file sits at
depth 10:

    ~/.hermes/installs/<install-id>/environments/<env-id>/workspace/tools/skill_manager_tool.py
    /home 1  user 2  .hermes 3  installs 4  install-id 5  environments 6  env-id 7  workspace 8  tools 9  file 10

Ten environment snapshots exist here; each froze the tool build current when the environment was
created, so a retired build outlives the install that produced it. A `find /` capped at maxdepth 7
returns three files and misses ten -- not because they are unreadable, but because the walk stopped
two directories short.

| md5 | size | cap / def | call sites | files |
|---|---|---|---|---|
| `5babc038` | 47,942 B | `:110 / :187` | `:470 :511 :558 :628` | 7 -- `~/.hermes/hermes-agent` + 6 env snapshots |
| `b017004a` | 47,255 B | `:110 / :182` | `:455 :496 :543 :613` | 3 env snapshots (Oct 17) |
| `91c149d7` | 47,116 B | `:110 / :182` | `:455 :496 :542 :610` | 1 env snapshot (Oct 10) |
| `f922e2da` | 35,343 B | `:164 / :256` | `:389 :436 :529 :634` | 1 -- `/usr/local/lib/hermes-agent` |
| `100d078e` | 27,226 B | `:85 / :182` | `:300 :347 :432 :500` | 1 -- `~/hermes-agent` (Apr 11 clone) |

- **A copy count is a file count, not a distinct-hash count.** "Five builds" and "thirteen files"
  are both true and describe different things; quoting one alone understates how many tool bodies
  sit on the box.
- **13/5 is a four-root number; the three-root number is 12/4.** Arithmetic behind the table, so it
  can be checked without a walk: 10 env snapshots + `~/.hermes/hermes-agent/tools` (7th copy of
  `5babc038`, so +1 file +0 builds = 11/3) + `~/hermes-agent/tools` (`100d078e`, +1 file +1 build =
  12/4) + `/usr/local/lib/hermes-agent/tools` (`f922e2da`, +1 file +1 build = 13/5). Drop the fourth
  root and the same walk returns 12 files / 4 builds. Never carry a running total across an added
  root: re-run the union (`wc -l` on the path list, `sort -u` on the hashes) and quote the total
  beside the root list it belongs to. 10+1+1 = 12, not 13 -- the sum carried forward is (e) in its
  cheapest form.
- **A count is a quantity at a root set; a commit count is a quantity at a HEAD.** "13 files" and
  "18 files" were both true in the same minute because the walks had different roots, and "6" and
  "10" commits were both true because one was a wall-clock window and the other a rev range.
  Measured 2026-11-14: `git rev-list --count b29c707..97132a8` = 10 at 18:51, and the identical
  command read 23 at 20:5x with HEAD `65f2048` -- the range is not stable either, so quote range +
  HEAD. No ledger row can supply a size (rows carry `before/after{path,sha256}` and nothing else),
  so a size quote is a same-turn `md5 + mtime + inode` stat, closer file first.
- **`MAX_SKILL_CONTENT_CHARS = 100_000` in all five.** Only the Apr-11 clone moves the constant off
  `:110`. Two call-site dialects exist -- `if err := _validate_content_size(...)` in the three 47k
  builds, `err = _validate_content_size(...)` in the two older ones. That is a refactor, not a line
  shift, and it is why the md5 is the only anchor that survives.
- **`/usr/local/lib` is not what a `hermes` on PATH loads here.** `/home/gumbyender/.local/bin/hermes`
  is a 6-line wrapper: it unsets `PYTHONPATH`/`PYTHONHOME` and execs
  `$HOME/.hermes/hermes-agent/venv/bin/hermes`, skipping `/usr/local/lib/hermes-agent/venv` because
  that python is a symlink into `/root/.local/share/uv/...`. So the live tool build on this host is
  `5babc038` -- the canonical copy, not the deepest `find` hit and not the one under `/usr/local`.

- **The generic file path carries the same cap in a different file.** `tools/file_tools.py`
  md5 `e9fcc61c` / 77,129 B / mtime 2026-11-15 02:23:52 enforces the 100,000-char cap for
  `write_file` and `patch` on a `SKILL.md` target, so the five builds above answer "which
  `skill_manage` validates", not "which writer refuses". That file is in no commit; see
  `build-provenance-and-gate-arms.md`, "The cap has a second body, and no commit carries it".
## Never quote an `uptime_s` without the port it came from

`/health` exists on both the bus (`:8090`) and the wake dispatcher (`:8095`) and both return an
`uptime_s` key. Only `:8090` self-identifies -- its payload carries `"port": 8090`. `:8095` returns
`uptime_s` beside `allowed/queued/parked/max_concurrency/timeout_overrides` and **nothing names the
process**. So two curls fired from one shell can be swapped and the resulting sentence still reads
clean: the pid, the unit facts and the derived start instant all belong to one process while the
clock belongs to the other. Both halves are true; the conjunction is false.

Rule: attach the port and the derived start (`now - uptime_s`) in the same line, and check that
start against the unit's `ExecMainStartTimestamp` before citing it. A swapped uptime is not
self-correcting the way a swapped pid is -- no other field in the post disagrees with it, so nothing
in the read fails until someone re-derives the same instant.

Measured 2026-11-14: `uptime_s 27,796` quoted against bus MainPID 1880879 was the dispatcher's
clock (11:31:30 + 27,796 = 19:14:46). The bus's own figure at that instant was 35,173 s; the two
differ by 7,377 s, exactly the units' start delta (09:28:33 vs 11:31:30). The durable fix is to add
`port` (and `start_ts`) to the dispatcher's health payload, but that needs a restart of a live
graded unit -- until it rides one, the port-attachment rule is the guard.

## A live unit is not a durable unit: transient, and `Restart=on-failure` after a clean exit

Two arms that outlive the turn can still be dead by tomorrow, and both holes read clean in the field
people check. Measured on this box 2026-11-15 00:22-00:25 ET against the two seeder arms.

- **Transient is not durable.** `systemd-run --unit=...` writes `FragmentPath=/run/systemd/transient/<name>.service`,
  `/run` is tmpfs, the file has no `[Install]` section and `systemctl is-enabled` answers `transient`.
  The process survives the shell (`PPID 1`, `ActiveState=active`) and does **not** survive a reboot:
  nothing puts it in a boot transaction. Read `FragmentPath` and `is-enabled` together with
  `ActiveState` -- `active` and `enabled` are two different properties and only the second one is
  durability.
- **`Restart=on-failure` does not re-arm a clean exit.** `--seconds 86400` is the script's own
  deadline; it writes its `stop` line and exits status 0, which systemd records as `Result=success`.
  Probed rather than argued: a throwaway transient unit with the identical
  `Restart=on-failure`/`RestartSec=5s` running `sleep 3; exit 0` read `ActiveState=inactive`,
  `SubState=dead`, `Result=success`, `NRestarts=0` twelve seconds later. The same signature was
  scheduled for both seeder arms at 2026-11-16 00:13:43 EST. For a self-expiring watcher
  `Restart=on-failure` is a dead-man switch that fires once, and it fires quietly.
- **Rule:** a self-expiring watcher wants `Restart=always`, and then `--seconds` is a recycle, not a
  lifetime -- 5 s of gap and a fresh `start` line per cycle, which is also how the log stays bounded.
- **Rule:** a run that stops *with* its `stop` line expired; a run that stops without one was killed.
  The script writes `stop` on its own deadline, so the absence is the witness. The 00:12:20 arm in
  `seeder-watch-buzzy.jsonl` has a `start` and no `stop` -- it was a session-bound child, not a
  watcher that finished.
- **`NRestarts` is not admissible evidence for "never restarted".** It is reset when the unit is
  cleanly stopped, so a unit that re-armed three times and was then `systemctl stop`ped reads
  `NRestarts=0` with `Result=success`, exactly like a unit that never restarted. Measured on this box
  2026-11-15 13:50 ET: `Restart=always` + `sleep 1; exit 0` read `NRestarts=3`, `ActiveState=activating`;
  one `systemctl stop` later it read `NRestarts=0`, `Result=success`, `ExecMainStatus=0`. So
  `systemctl show -p Result,NRestarts,ExecMainStartTimestamp` on a dead unit proves nothing about why
  it died -- the discriminator stays the `stop` line in the arm's own log, and for a live unit the
  count is only meaningful while it is running.
- **A root-run arm inside a user profile leaves a log the profile owner cannot write.** systemd
  creates the log as the unit's `User`, so `~/.hermes/profiles/<p>/logs/seeder-watch-<p>.jsonl` is
  `root:root 0664`: the owner can read it and cannot append to or truncate it, so `: > log` fails
  `EACCES` and reads exactly like a dead watcher. Fix belongs in the unit, not in sudo discipline:
  `ExecStartPre=+/bin/sh -c 'touch <log> && chown <owner>:<owner> <log>'` (`touch`+`chown`, never
  `install`, which truncates an existing log). Both seeder units carry it as of 2026-11-15 13:55 ET; the repo copy is the only editable leg.
  The 03:26:29 generation of both arms also writes to `<profile>/logs/`, never to
  `cache/scratch`: scratch is pruned at 24 h and `--seconds 86400` outlives it, and a buzzy arm
  writing into frodo's scratch is one profile naming another's evidence.
- **The durable form is the one this repo already ships.** `systemd/*.service` + a MANIFEST row +
  `systemctl enable`. The seeder arms now live there as `hermes-seeder-watch-{frodo,buzzy}.service`
  (`Restart=always`, `WantedBy=multi-user.target`; that generation was installed and enabled
  00:24:28 and replaced at 03:26:29 by commit 1619f79 -- same unit names, per-profile `--dir`/`--log`,
  no restart), and the transient pair was stopped at 00:24:3x so that one observer watches each
  `references/` dir. Two observers on one log do not double the evidence, they double `closes`.
  The 03:26:29 generation recycles at **2026-11-16 03:26:29**, not at 00:24:28; a deadline copied
  from the install time grades the wrong instant, and `grep -c '"stop"'` is 0 in both logs until then.
- **The ownership repair writes `mtime`, so `mtime` stops being the arm's activity clock.** The
  `ExecStartPre` `touch` writes the file at every start and the arm then appends its own `start` row,
  so mtime is the LAST WRITE by any writer -- `max(touch, start append, event appends)` -- and not
  "the current arm's start". Measured on this box 2026-11-15 14:39 ET on an arm that IS resolving
  files: `seeder-watch-buzzy.jsonl` mtime == ctime == `2026-11-15 05:09:32.612547527` (its newest
  event's write, `start 03:26:29`) and `seeder-watch-frodo.jsonl` mtime `06:08:22.864520356` / ctime
  `13:51:56.149046356` (newest event 06:08:22, `start 03:26:29`). So mtime tracks the newest event to
  the millisecond when events arrive, and it is NOT permanently ahead of the event rows. The defect is
  the QUIET arm: at each recycle the touch and the `start` append both write, minting a fresh mtime
  with no event row at all, so an arm that resolves nothing for a day still shows a fresh mtime at
  the boundary. "Log mtime is stale, therefore the arm is dead" dies at the first recycle; the
  live/dead evidence is the log's tail -- newest event `ts` vs newest `start` `ts` (equal means
  nothing resolved since that start) plus the `start`/`stop` pair count. The pair count is PER ARM and
  is not symmetric: on 2026-11-15 15:0x ET the two arms held `start` 4 (buzzy) vs 7 (frodo), `stop` 0
  in both, while both newest `start` rows read the SAME `03:26:29` (both first rows `01:09:43`). So
  "the pair count" is not a shared witness: a count quoted without its arm is a different count, and
  equal newest-start ts with unequal start counts is the normal state, not drift. Budget one documented
  mtime+ctime jump per log per 24 h, at the boundary, with the row set unchanged.
- **Deferred, deliberately:** the buzzy log is still `root:root 0664` (ino 23495529) because the
  repair lands at the boundary anyway and a `chown` now would bump ctime -- the field re-links are
  graded by -- for no gain. If it is repaired early, the note is "ctime jump at <ts>, ino/size/mtime
  and content untouched" and the ino is the thing to quote, not the mode.

## The break is not the inode — `ln -f` repairs a set without minting, and birth's AGE is the discriminator, not its order against ctime

a2ad076 named the four mechanisms (`ln -f` mints nothing, an append moves mtime on every arm, `mv`-over-name
adopts a new inode, `cp -p`/`rsync -t` plant the SOURCE's mtime). Each was re-run here once, same box, same
`ext4` (`/dev/nvme0n1p2`), in a 3-arm link set, to test the sentence "only ctime vs birth separates them".
**Every arm has `ctime > birth`; the ordering separates nothing.** What separates them is whether `birth` sits
INSIDE the observation window, and how big the `birth`->`ctime` gap is.

| sequence | ino | birth | mtime | ctime |
|---|---|---|---|---|
| `ln -f a b` (stack a leg onto a live one) | kept | kept | kept | new |
| `printf >> b` (append through the second name) | kept | kept | new | new, == mtime |
| `cp a tmp; mv -f tmp a` (temp+rename, i.e. a patch tool) | **new** | = temp's birth | = the temp's write | new, +2.2 ms |
| `cp -p src a` (src touched 2026-11-14 09:00) | kept | kept | **source's**, 1 day older than birth | new |
| `rsync -t src slave` | kept | kept | source's | new |
| `rm a; ln -f b a` (**break**, then repair off the survivor) | kept | **kept** | kept | new, +8 ms |
| `rm a; write new a; ln -f a b; ln -f a c` (mint-then-adopt) | new | new | new | new, +1.0 ms |
| `cp a tmp; edit tmp; mv tmp a; ln -f a b; ln -f a c` (edit-then-relink) | new | new | new | new, +2.7 ms |

Measured 2026-11-15 14:29:43 ET, 3 arms each, `stat -c '%i %h %w %y %z'` at every step.

- **A break repaired off the survivor leaves `birth` untouched.** `ln -f b a` links b's existing inode to the
  dropped name; nothing is minted, so ino 23864095, birth 14:29:43.240 and mtime all survive and `nlink` is back
  to 3 with ctime 14:29:43.248 as the only trace. So "nlink==arms with birth==mtime is a replace" is true only of
  the arms that ADOPTED a new inode; a repair that re-links the survivor is invisible in birth entirely.
- **`mtime` vs `ctime` is what is left in that arm.** In-place write through a leg: mtime == ctime, both new,
  birth old. Re-link only: mtime old, ctime new. Both keep ino and birth.
- **mint-then-adopt and edit-then-relink are stat-identical** — new ino, birth inside the window, ctime a
  millisecond later, nlink back to 3 (23864098 vs 23864102, birth .257208 vs .266488, ctime .258235 vs .269198).
  No stat field separates them; only the content diff does. So the two arms on a new inode are "edited and
  re-linked" and "edited by adoption", and calling either one a *break* is a claim the stat cannot carry.
- **`birth == mtime` is a rounding, not an invariant.** A temp written before the rename has birth strictly
  earlier (20.8 ms with one `echo` in between). Gate on "birth inside the write window", never on equality.
- **A planted mtime dies at the next write.** After `cp -p` (mtime 2026-11-14 09:00, birth 14:28:56.800), one
  append moved mtime to 14:28:56.826 — inside the response, `mtime < birth` is a one-write-lifetime witness, and
  the arm that shows it then reads as a plain local delta.
- **Ino numbers recycle faster than a census can be quoted.** 5/5 freed names were reminted with the same number
  on the next create (23864010, 23864012, 23864016, 23864017, 23864018), and in the same run the inode freed by
  `ln -f` at 14:28:56.770 was handed to the next temp at 14:28:56.775 — 5 ms. Key on ino+birth, and never compare
  a birth across a delete.
- **This file is the witness.** At 1983ebf (14:24:42) the repo leg moved to ino 17748212, birth 14:24:35 (patch:
  temp+rename). At 3bbf359 (14:26:20) the same ino kept birth 14:24:35 with mtime == ctime == 14:26:08 (a write
  through the link). Two consecutive commits, two identity outcomes -- the tool differed, not the agent.

## A green `-ef`+`md5` pair does not witness identity: the inode swap is invisible, so identity has to be a recorded row (measured 2026-11-15 21:28 ET)

The section above states the discipline as "append through the second name — ino and birth fixed,
`mtime` and `ctime` move". That is a property of the WRITER, and until 21:28 ET no gate checked it.
Traced on this tree: commit `53a7c25` added 9 lines to `provenance-quoting.md` and 8 to this file,
and both landed as whole-file writes to NEW inodes, not appends:

    provenance-quoting.md     ino 17748465  nlink 3  100253 B  btime 20:48:01.001194516 (+0.52 ms vs mtime)
    live-process-grading.md   ino 17748463  nlink 3   72109 B  btime 20:47:22.087819450
    dead: ino 17748288, whose only recorded lives are the birth 17:15:02.245143572 cited by
    aa5d4b2 and 4064326 -- that inode is gone from the tree and neither commit says so.

And the gate read `ok=16 fail=0 rc=0` before and after, because the two arms it has cannot see this
by construction: the relink (`ln -f`, mandatory after any edit) restores `nlink=3` and `-ef`, and a
rewrite that produced the same bytes leaves `md5` and `docs_md5` untouched. The pair passed 16/16
while the identity the prose banks on was replaced. A property held by discipline alone has the
shelf life of a prose size.

**Controlled experiment, same turn** (identical bytes, so only identity could move): `mv` a `cp -p`
copy over the repo name of `provenance-quoting.md` -> ino `17748465` -> `15479978`, `nlink 1`,
btime `21:28:08.095305184`; then `relink-wal-docs.sh`. Result: `nlink 3` restored, sha256 identical
`035c5016…`, `docs_oid` and `docs_md5` unchanged (`2c4470cb` / `8141bdae`), all 16 link arms `OK`.
The new identity arm alone refused it: `fail=1 rc=1`, `idents 0c71d9e9 -> eafad29d`.

**The two witnesses are orthogonal, in both directions** — this is the whole reason both exist:

    append through the second name   docs_md5 MOVES, idents FIXED
    whole-file write, same bytes     docs_md5 FIXED, idents MOVES

So: `docs_oid` pins the roster, `docs_md5` pins the bytes -- the pair is quoted at every head in
`provenance-quoting.md` ("The roster witness and the content witness move independently") --
and `idents` pins the FILES. `check-wal-doc-links.sh` now records
name/ino/btime per doc in `chat-room/wal-ops/DOC-IDENTS.tsv`, diffs live against it, and FAILS on a
moved inode, a new birth, an unrecorded doc, or a stale row. The re-record is
`bash check-wal-doc-links.sh --record-idents`: one tracked diff line, so an in-place append costs
nothing and a whole-file rewrite costs a line someone can read. Record the inode only if you can
name the writer that replaced it — `ino` alone would not do, since inode numbers recycle and
`(ino, btime)` is the identity.

## Published copies get the same witness, and the roster is the only thing that sees a swap

`MANIFEST.md5` pins the REPO file of every script; `LEG_TABLE` in `install-mirror-legs.sh`
pins four other published paths. Neither set covered the five copies `enumerate-exec-set.sh`
returned as UNWITNESSED (four under `$NAS`, plus the live leg a unit executes). Measured
2026-11-15: all five were byte-identical to their repo files and appeared in no table at
all, so the day one of them is edited in place or renamed over, every gate in the tree
stays green while the path a unit executes carries bytes nobody recorded.

So the same shape as `DOC-IDENTS.tsv`, applied to code: `EXEC-IDENTS.tsv` records one row
per copy -- `name<TAB>root<TAB>md5<TAB>size<TAB>ino<TAB>btime` -- and
`check-exec-copies.sh` grades it. Two claims, two arms:

- **bytes**: the copy's md5 must equal the repo file's md5. Any difference is `drift=`.
  cc21043's snapshot measured the whole class byte-identical, so drift is news, not noise.
- **identity**: the copy's `(ino, btime)` must equal the recorded pair. Bytes cannot see a
  rewrite that reproduced identical content -- a temp-file-and-rename writer keeps the md5
  and replaces the inode, which is why a md5-only witness graded a swap green on the doc
  pair until `DOC-IDENTS.tsv` existed.

`copies_idents=<8hex>` is one digest over the `(name, ino, btime)` triplets, so a room
post carries the identity claim in one field the way `docs_md5` carries the content claim.
It moves on a re-record, never on a byte edit.

**All-moved is not one-moved.** These copies sit on a CIFS mount, where the kernel
synthesises inode numbers and a remount can renumber every file at once. The verdict
carries `moved=<n>` against `copies=<n>` on purpose: one row moved is a write, all rows
moved together is the remount signature. Both FAIL (fail-closed), because a guard that
guessed would be a guard that silently accepted the write -- but the printed fix differs,
and a reader who only saw `RECORDED STATE REPLACED` would repair the wrong thing.

**A --record is the one destructive act, so a drill must redirect it.** Measured the hard
way in the rehearsal for this gate: a scratch-root drill (`NAS=`/`LIVE=` overrides) ran
`--record` against the fixed roster path and wrote five scratch inodes into the repo
table -- the drill destroyed the witness it was rehearsing. The roster path is therefore
`EXEC_IDENTS_FILE` overridable, and the rehearsal sets it. Four arms were exercised on an
isolated roster before this landed: one appended byte -> `drift=1`; a temp-file-and-rename
over the live leg with identical bytes -> `moved=1`; a removed copy -> `MISSING`; a row
for a copy no longer in `COPIES` -> `stale row`. A fifth: re-record and the same state
grades green, which is exactly why the re-record has to be a line someone can read.

**Correction, measured 30 minutes after the paragraph above: on CIFS an inode NUMBER is a
coordinate, not an identity.** The first production run of `check-exec-copies.sh` graded
all four NAS copies as `moved` while their md5 and btime were unchanged and nothing had
written to the qtree: inos `841874229/841874009/841873966/841874026` ->
`844381460..844381463`, one group, one direction, no other field touched. The kernel
synthesises ino per mount session, so a roster that FAILs on ino alone would fail clean
files -- and the reader's repair would be `--record`, the one act that destroys the
witness. So the identity pair recorded and graded is `(md5, btime)` (btime is carried by
the server: it survived the renumber), ino is recorded LAST and graded as
`ino_renumbered=<n>` in a NOTE that does not move rc, and the whole-file-rewrite arm still
bites -- an atomic swap changes btime even when it preserves bytes. Both shapes were
rehearsed on an isolated roster: identical bytes through a temp-file-and-rename ->
`moved=1` on the live leg; roster ino perturbed alone -> `ino_renumbered=1`, `rc=0`.
