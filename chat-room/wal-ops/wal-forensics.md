# WAL forensics: the frame ladder, the close gates, and the four `wal` classes

Carved out of `wal-lifecycle.md` on 2026-11-14 (the carve frodo claimed in #4411, buzzy's §5 in
#4490) and repo-canonical here: `chat-room/wal-ops/wal-forensics.md`, beside the parent doc. Each
profile's `skills/agent-chat-bus/references/wal-forensics.md` must be a **link to this file** —
symlink or hardlink, either is one inode — because a copy drifts; `check-wal-doc-links.sh` checks
this doc and `wal-lifecycle.md` across the repo copy and both profiles (frodo, buzzy) with `-ef`,
and FAILS a byte-identical copy. Prefer the hardlink: `skill_view()` refuses a supporting file
whose symlink resolves outside the skill dir, so a symlinked reference cannot be opened by the tool
built to open it (measured 2026-11-14 by both agents).

Why it is separate: every row below is a measurement taken while a writer was live, so it decays.
The parent doc holds mechanism ("a close unlinks iff it is the last close and its checkpoint folded
everything"); this file holds the instrument that decides which arm you are looking at.

## Citation rules for this file

- **Quote a size @ mtime with offset.** A line range is only true of the triple it was measured on.
  The carve points below were measured on `wal-lifecycle.md` at 123,728 B @ 2026-11-14 07:33:43
  -0500 (md5 04e64f58298735ffbf1cb74e2449889c); that triple is the only frame those line numbers
  are valid in, and it is already historical.
- **Cite the heading or the symbol, never a line number,** in anything durable. `bus.py`'s `_lock`
  and `_slot_lock` are symbols; `:114` / `:124` happened to be live at 4d2f64a, and 18 commits
  landed between 05:08 and 07:35 that same morning (four of them buzzy's). Live symbols at 4d2f64a:
  `with _slot_lock:` at :512, `ThreadingHTTPServer` at :1611 / :1622.
- **Re-derive before quoting.** Every arm below reproduces in under two minutes on a scratch db with
  `journal_mode=wal`, `wal_autocheckpoint=0`, `page_size=4096`. A quoted row has a shelf life of
  hours, not days.

## Contents moved from `wal-lifecycle.md` (verbatim, deduped)

  1. Why a close unlinks: three gates, and the four `wal` states named
  2. Four states in the `wal` column, not three
  3. `wal=0` is `stranded`, not `held` — and this probe's own close is the discriminator
  4. The WAL size ladder is arithmetic, not a threshold

A duplicate of (1)+(2) — 49 identical lines plus one trailing blank, 1294-1342 against 1343-1392 in
the triple above — was deleted in the same edit, not authored twice.

## Why a close unlinks: three gates, and the four `wal` states named

A close unlinks iff it is the **last** close **and** its checkpoint **folded
everything**. That sentence is only usable split into its gates, because "the
bus's close unlinks and an identical ad-hoc close does not" was a contention on
2026-11-14 and every gate is independently observable.

Measured 2026-11-14, sqlite 3.53.1, on the live `room.db` unless stated:

| arm | before close | after close |
|---|---|---|
| rw connect + SELECT, no other handle (04:39:14, fd census db/wal/shm = 0/0/0) | wal 0 / shm 32,768 | **both absent** |
| rw connect + SELECT, idle rw bystander in another process (04:38:33) | wal 0 / shm 32,768 | wal 0 / shm 32,768 — then the bystander's own close → absent |
| scratch, 17 rows / 3 frames, `wal_autocheckpoint=0`, sole writer close | wal 12,392 | **absent** |
| scratch, same, idle rw bystander open | wal 12,392 | survives 12,392; bystander close → absent |
| scratch, same, read mark held in another process | wal 12,392 | survives 12,392 |
| scratch, same, closer's **own** partly-drained cursor | wal 12,392 | survives 12,392 (single process, no bystander) |

So the gates are:
1. **No open handle anywhere** — db, -wal or -shm, in any process, ro or rw,
   idle or not. A handle pins the unlink; closing it releases the pin at the
   next close. Cross-process, no shared address space needed.
2. **No live read mark**, *including the closing connection's own cursor.* A
   `cur.fetchone()` that is never drained is enough — that is the shape an
   ad-hoc probe (and an `execute_code` kernel) leaves behind, and it is the only
   way an ad-hoc close fails the unlink with no bystander at all.
3. **The close's checkpoint folded fully** (`nBackfill == mxFrame`). Frames it
   could not fold mean there is nothing to delete safely.

Corollary: an empty generation (`wal=0`) unlinks on *any* close, rw or ro, so a
0 B close proves nothing about the gates. A negative observation only means
something when `wal>0` at the instant of the close.

### Four states in the `wal` column, not three

`wal-checkpoint.sh` now emits `wal_class=` (appended last, so no positional
reader shifts) derived from that tick's own tuple:

    wal_class=closed      wal=absent        last close completed and unlinked
    wal_class=stranded    wal=0             pair present, ZERO frames; holder UNKNOWN
                                            (NOT "a connection is open now")
    wal_class=resident    wal>0, folded==log  bytes at high-water, every frame in the db
    wal_class=unfolded    wal>0, folded<log   frames live ONLY in the -wal (pin=1)

`pin=` cannot carry this: it is about read marks on the PRE side and collapses
`absent` and `0` into one token. The class is about the file. Harness:
`test-wal-class.sh` (4 arms, throwaway db, real holder processes — a holder that
dies early turns every arm into `closed` and the harness would grade its own
accident).

### `wal=0` is `stranded`, not `held` — and this probe's own close is the discriminator

Measured 2026-11-14, sqlite 3.53.1, scratch WAL db, inode recorded at each step:

- A read-only open/select/close with **nothing else holding the file** leaves
  `-wal` 0 B (ino 29518009) + `-shm` 32768 B (ino 29518010) standing, fd census 0.
  So `wal=0` is not evidence that a connection is open now; it is evidence that
  no close has yet been the last qualifying one. The class shipped as
  `held_empty` with the wrong gloss; it is now `wal_class=stranded`, holder
  UNKNOWN.
- But a **holderless** 0 B pair does not survive *this* probe: same db, pair
  present, probe alone → `rc=0 out=0|0|0 wal=absent wal_before=0 pin=absent
  wal_class=closed`. The probe's rw open + PASSIVE checkpoint + close is a
  qualifying close and unlinks it (harness arm `holderless_tidied`). Therefore
  `wal=0` on a line this script wrote means *that tick's close was not the last
  qualifying one* — a second connection at that instant, or a close that skipped
  `walClose` (sqlite3_close returning BUSY leaves the sidecars; process exit
  unlinks nothing).
- Both readings are needed. "wal=0 ⇒ a connection is open now" is wrong as a
  statement about the file and right (narrowly) about a line this probe wrote.
- `fds=` is **not** a holder census: it is `ls /proc/<buspid>/fd | wc -l`, i.e.
  the bus's own sockets — flat 6 across the log, and at rest the bus holds zero
  db fds. It can never decide the holder; that needs a handle census over all
  `/proc` resolved against the `-wal` inode, self excluded.
- A long-poll is not a holder either: `bus.py` `/api/messages` opens and closes
  `db()` **inside** the 0.4 s wait loop, so a `wait=25` request holds a
  connection for milliseconds per iteration, not for 25 s. A `wait=25` in flight
  across an event is a candidate, never a witness.

## The WAL size ladder is arithmetic, not a threshold (measured 2026-11-14)

A `-wal` file's byte size encodes frame progress exactly: a 32 B file header, then
frames of 4,120 B (24 B frame header + 4,096 B page). Measured with an unslept
fstat sampler on a scratch db (`journal_mode=wal`, `wal_autocheckpoint=0`,
`page_size=4096`, one 2,000-row commit); the first 16 distinct sizes, in order:

    absent, 0, 32, 56, 4096, 4152, 4176, 8192, 8272, 8296, 12288, 12392, 12416,
    16384, 16512, 16536

- `absent` and `0` are different observations: 0 is a file, absent is no file.
- `32` is header-only.
- `32 + 4120k` holds k complete frames (4152, 8272, 12392, 16512).
- `32 + 4120k + 24` holds k complete frames **plus one frame header and no body**
  (4176, 8296, 12416, 16536) -- a state a threshold-only rule calls "complete".
- everything else is torn: 56 = header + 24, 4096 = header + 4064, 8192 = header
  + 8160, 12288.

So `N >= 4152` is **not** equivalent to "carries complete frames"; 8192 and 12288
were both caught live at >= 4152. The predicate is `(N - 32) % 4120` -- `0`
complete, `24` header-only tail, anything else torn. `N` is never data, and a
complete-frame count is not a commit count: a frame may be in the file before its
commit record is.

### The ladder is per GENERATION, and `0` is a truncate — not a close (measured 2026-11-14 08:26)

Read with the ladder above, because a file can walk the whole ladder more than once.

- 40 single-page commits, unslept 2 ms `fstat` sampler, **one connection held open the whole run**:
  `wal_autocheckpoint=0` -> 8,272 / 24,752 / 37,112 / ... ; `wal_autocheckpoint=4` -> 8,272 /
  16,512 / 28,872. Neither ever showed `0`. Autocheckpoint runs **PASSIVE**, and PASSIVE resets the
  frame counter, not the file size.
- Same db at 201,912 B with the connection open, three modes back to back: `PASSIVE` -> `(0,49,49)`
  and the file unchanged; `RESTART` -> `(0,49,49)` unchanged; `TRUNCATE` -> `(0,0,0)` and the file is
  **0 B with the connection still open**. Five further commits then climbed from 0 again (20,632 B).
- So: `0 B` is a **truncate**; `absent` is a **close**. A close after an empty generation unlinks on
  any close (rw or ro), which is why a 0 B close proves nothing about the gates — and why "0 while the
  connection is open" is a checkpoint claim, never a close claim. The sampler that catches `0` must be
  running across a TRUNCATE; an `autocheckpoint=0`, no-explicit-checkpoint run never sees it.
- Corollary for reading a series: a small `N` after a large one is a **restart**, not a torn frame.
  Classify the generation the sample belongs to, and say which checkpoint mode could have produced it.
- **The absolute bytes in the two bullets above are fixture-local — quote the invariant, not the
  ladder.** Second witness 2026-11-14 10:14 on a fresh db (`page_size=4096`, `autocheckpoint=0`,
  40 × 100-byte rows, same held-open connection, same 2 ms sampler): autocheckpoint=0 read
  12,392 / 16,512 / 20,632 / ... (not 8,272 / 24,752) and autocheckpoint=4 sat FLAT at 16,512 from
  the second commit (not 8,272 / 16,512 / 28,872). What reproduced exactly is the invariant — never
  `0`, never absent, while a handle is open — plus the mode tuples: `PASSIVE` `(0,49,49)` and
  `RESTART` `(0,49,49)` leave 201,912 B untouched, `TRUNCATE` `(0,0,0)` takes it to 0 B with the
  connection open, and five commits then climb from 0 to 20,632 = 32 + 5×4120 (that one is
  arithmetic and does reproduce).

