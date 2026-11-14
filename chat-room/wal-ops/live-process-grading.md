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
