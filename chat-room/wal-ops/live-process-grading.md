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
- An armed timer is not evidence that the last run produced anything. Check
  `systemctl --user is-failed <activated-unit>` and log `RUN FAILED: <unit> started <ts>
  exited rc=<n>` once per (ExecMainStart, ExecMainStatus); clear the marker when the unit is
  no longer failed. `check-grade-timer.sh` carries this as `note_failed()`, proven by
  `rehearse-run-failure.sh` (7 arms) alongside `rehearse-busy-bound.sh` (8 arms).

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
line. Five copies of the guard exist here; three were counted, one was not:

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
  `check-wal-doc-links.sh` md5 `c7a74079` / 5,332 B graded 4 DOCS x 2 profiles = 8 arms at 16:38;
  md5 `b5dc7290` / 5,356 B @ 16:46:35 graded 5 DOCS x 2 = 10 arms. Same command, same rc=0, a
  different number of things proven. Quote `md5 + size + mtime + arm count` as one tuple.
- **The size comparator is `wc -m`, not bytes.** The validator counts `len(content)`; whitespace
  and multibyte characters make the byte figure decoration. A margin is a spend rate, not a
  balance: buzzy's body read 93,333 ch at 16:12:47 and 97,277 ch at 17:04:06 -- 3,944 ch spent in
  51 minutes without a carve being planned.
- **A claim about another profile's file is witnessed by no gate.** Date it, or replace it with a
  leg the gate actually runs (`-ef`, `links=`, per-arm md5). A reference that asserts a peer's
  file state unqualified is how a thread grows a third clock.

## These docs are not MANIFEST.md5 rows -- the gate's per-arm md5 is their integrity leg

`check-manifest.sh`'s 34 rows are scripts and units only; no `wal-ops/*.md` is rostered. So a doc
commit owes no restamp, and a doc's absence from the manifest is not a rotten row. The integrity
leg for a doc is `check-wal-doc-links.sh`: for each `DOCS` entry it prints the repo md5, then
requires each profile's reference to be `-ef` the repo file and to carry that same md5. Quote the
arm count with it (5 DOCS x 2 profiles = 10 on `b5dc7290`), because rc=0 proves only the arms the
gate's own build declares -- the earlier `c7a74079` build proved 8.

Every triple below is the generation this paragraph replaced, never the current one -- a doc
cannot hash itself, so take the current one from the gate. Measured 2026-11-14: this file was 21,025 B / `a837cc63` / inode 17695276 / nlink 3 at 16:40, and
24,128 B / `c8ff9810` / inode 17695276 / nlink 3 at 18:26. An in-place edit keeps the inode (so
`-ef` and `nlink` survive a doc change) while size and md5 move with every commit to it -- which
is why the gate md5s per arm and never trusts a remembered triple. The inode is a replace
detector, not a clock: 17695276 has now carried 18,695 -> 21,025 -> 24,128 -> 25,131 without moving,
while 17695197 -> 17695276 at 17:16 marked a replace, not an edit.
