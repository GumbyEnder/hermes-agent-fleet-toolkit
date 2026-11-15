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

- ~~40 single-page commits, unslept 2 ms `fstat` sampler, **one connection held open the whole run**:
  `wal_autocheckpoint=0` -> 8,272 / 24,752 / 37,112 / ... ; `wal_autocheckpoint=4` -> 8,272 /
  16,512 / 28,872.~~ **STRUCK 2026-11-14 12:41 — does not reproduce on a clean fixture (see the root cause
  at the end of this section).** The reproducible ladder is `12,392 / 16,512 / 20,632 / 24,752 / 28,872 / …`,
  `+4,120` per single-page commit, and `wal_autocheckpoint=4` is FLAT at 16,512 from the second commit.
  Neither ever showed `0`. Autocheckpoint runs **PASSIVE**, and PASSIVE resets the frame counter, not the
  file size.
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

### Root cause of the irreproducible arms: the pragma name, not an unstepped assignment (measured 2026-11-14, second arm revised 14:38)

- **There is no `PRAGMA autocheckpoint` in SQLite 3.53.1.** `sqlite3 p.db "PRAGMA autocheckpoint=4;
  PRAGMA wal_autocheckpoint;"` returns no row for the first and still prints the previous value (`1000`):
  the assignment is silently discarded and the run proceeds on the default 1000-page threshold. So a
  fixture spelled `autocheckpoint` measures one arm, not two — every sample is a rung of the same growing
  ladder, and `8,272 / 24,752 / 37,112` are `32 + n×4,120` at n = 2, 4, 7. The setting is
  `wal_autocheckpoint`; there is no alias.
- **The assignment DOES take effect at `execute()` on CPython 3.14.9 / SQLite 3.53.1 — but only a read-back
  proves which arm you measured.** A first reading here claimed an un-fetched `execute()` "can no-op" and
  that `.fetchone()` is what applies it; that mechanism does **not** reproduce on this stack. Measured
  2026-11-14 14:35–14:38: `c.execute("PRAGMA wal_autocheckpoint=4")` with no `fetchone()` leaves
  `cursor.description` populated (CPython steps the statement once to materialise the pragma's value row),
  eight further single-page commits sit FLAT at 16,512 B (autocheckpoint actually fired at 4 pages), and a
  same-connection `PRAGMA wal_autocheckpoint` reads back `4` — identical to the `.fetchone()` shape. What
  *does* leave the old value in force is a wrong **name** (previous bullet): `PRAGMA autocheckpoint=4`
  silently discards and leaves the default `1000`. The durable half stands and is the reason to keep the
  bullet: print the read-back in the same fixture, because an unread pragma is an unmeasured arm — "was the
  value what I thought" is answered only by reading it, not by assuming the SET landed. **A fixture whose arm parameter never reaches the SQL measures one arm twice** — measured 2026-11-14 14:52: a probe passed the pragma spelling as a Python argument that only reached the temp-file NAME, and both “arms” printed identical numbers (read-back 4, WAL 16,512 B). That is the same defect as the wrong pragma name, one layer up; the read-back catches it only if the fixture also prints the statement it actually sent.
  **Fail it closed rather than eyeball it:** hash the statement each arm actually sent and
  assert the two digests differ. Measured 2026-11-14 21:14 here: asserting that the two
  *parameters* differ passes on the buggy fixture — the parameters do differ, the SQL is the same
  constant — so that assert is not a guard; the digest comparison is, and it goes red on exactly
  this fixture. Correct wiring: read-back 4 / 1000 and two digests; buggy wiring: one digest,
  both arms printing read-back 4 / 16,512 B. Quote the read-back and the digest count, not an
  absolute byte total: a WAL is `32 + frames×4,120` and the frame count is the fixture's COMMIT
  count (`frames = commits + 1`). One
  insert per commit on a 16-byte row reads 16,512 B (4 frames) vs 41,232 B (10) after 8 commits.
  `37,112` is 9 frames, and the shape that produces it is **8 commits**: `CREATE TABLE` committed
  alone plus 7 single-insert commits. **The frame count is a COMMIT counter, not an insert counter:
  `frames = commits + 1`**, i.e. `size = 32 + (commits+1)×4,120`, whatever a commit carries — the
  first commit writes page 1 and the table root (2 frames, confirmed by a `CREATE TABLE` alone at
  8,272 B) and every later one-page commit writes one. Measured 2026-11-15 03:52 here, CPython
  3.14.9 / SQLite 3.53.1, fresh DB per arm, WAL first, `wal_autocheckpoint=0`, ten shapes spanning
  commits 1…10: 8,272 / 12,392 / 16,512 / 20,632 / 24,752 / 28,872 / 32,992 / 37,112 / 41,232 /
  45,352 at 2…11 frames — one value per COMMIT count, whether that commit carried zero, one, two or
  all k inserts. So the create's transaction membership **does** cost a frame when it absorbs the
  first insert: at k=1 the ride shape reads 8,272 (2 frames) against 12,392 (3) for create-then-insert,
  and at k=7 32,992 against 37,112. An earlier revision of this bullet reported the two shapes as
  byte-identical at every k; that fixture measured one arm twice — the exact defect the bullet above
  describes — so its "transaction membership drops no frame" was the fixture, not the engine. The
  correct arm's byte total is still not the invariant the read-back is: quote commits, or quote the
  read-back. (Rungs 16,512 / 24,752 / 41,232 at k = 2 / 4 / 8 measured 2026-11-14 20:19 by buzzy,
  k counting single-insert commits after a create committed alone.) A printed statement still needs
  a reader; a compared one does not.
- **`frames = commits + 1` is a ONE-PAGE-PER-COMMIT rule, and a commit that writes nothing appends
  nothing.** Re-measured independently 2026-11-15 here on the same stack (CPython 3.14.9 / SQLite
  3.53.1), fixture with `isolation_level=None` and one explicit `BEGIN…COMMIT` per group, committing
  instrument = the trace's `COMMIT` count **with the fixture asserting `commits == len(groups)`**, plus
  the engine's own `PASSIVE` `log` as a second, independent frame count (agreed with the byte total on
  all 21 shapes). Rungs, ride-vs-separate pairs and the ten-shape ladder all reproduce exactly. Two
  shapes fall outside the identity:
  - **A commit that dirties N pages appends N frames.** `CREATE TABLE` alone then one commit carrying
    400 rows of 500 B = **2 commits, 54 frames, 222,512 B** — Δ over `commits+1` is +51. Every shape in
    the ladder is one-page-per-commit (8 × 16 B rows still land on the single table-root page: 3
    frames), which is why `k` never surfaced this. A future arm writing more than one page per commit
    must quote pages-dirtied, or the ladder reads the excess as corruption.
  - **An empty commit appends no frame.** `CREATE TABLE` alone then `BEGIN; COMMIT` = 2 commits,
    **2 frames, 8,272 B** (Δ = −1). So the counter counts *commits that wrote at least one page*; a
    zero-write commit is invisible to the WAL and to any ladder built on it.
  - **The `+1` is page 1, and it can be absent altogether.** `CREATE TABLE` alone, then
    `PRAGMA wal_checkpoint(TRUNCATE)` (WAL → 0 B), then **one** single-row commit = **1 commit,
    1 frame, 4,152 B**, the frame carrying page 2 only; three commits after the truncate = 3 commits,
    3 frames, 12,392 B, pages `[2] [2] [2]`. `frames == commits` exactly, no `+1`. So the invariant is
    **frames = Σ pages dirtied over all committed transactions** — one frame per dirty page per commit,
    nothing for a commit that dirtied none. The ladder's `+1` is the database header page (page 1) that
    the schema-creating commit dirties, and it appears exactly once because the WAL records it once;
    it is not a structural constant. (A page dirtied three times inside one transaction still appends
    **one** frame — 3 `UPDATE`s of one row: frames 3 → 4. And page 1 reappears whenever one of its own
    *header* fields moves, and only then: the 400-row commit above writes it again as `[1…52]` with
    `dbsize_pages` **2 → 52** while the **change counter stays 2**; a `DELETE` that frees pages writes
    page 1 with `freelist_trunk` 0 → 3 and `freelist_count` 0 → 38; one plain single-row commit after a
    `TRUNCATE` writes `[2]` alone. Over one db's whole life here (CREATE, 300-row growth, DELETE-all,
    10-row refill) the change counter read 2 and `version_valid_for` 2 in **every** page-1 frame, so it
    is not the mechanism — schema cookie, in-header page count and the freelist pointers are. Re-derived
    a second time from the frame bytes with an independent setup whose page-1 baseline counters read
    **3**, not 2, and the result is identical: the counter is constant in every page-1 frame and
    `dbsize_pages` is the field that moved; that arm adds one case the first pass lacks — after the
    page-freeing `DELETE`, a plain 10-row refill commit appended **one** frame carrying page 2 only
    (frames 23 → 24, no page-1 frame), so a commit that moves no header field does not re-dirty page 1
    at all.)
  **Why a Python fixture's ride arm silently does not ride:** `sqlite3`'s legacy transaction control
  (`isolation_level=''`, the default) emits the implicit `BEGIN` only before `INSERT`/`UPDATE`/
  `DELETE`/`REPLACE` — **DDL runs in autocommit**, so `CREATE TABLE` commits on its own and the "ride"
  arm is structurally the separate arm. The trace shows it: `CREATE TABLE t(a)` / `BEGIN` / `INSERT …` /
  `COMMIT` with no `BEGIN` before the DDL. Reproduced twice here in one turn, the second time in a
  fixture written specifically to fix the first. Any DDL-carrying arm needs `isolation_level=None` with
  explicit `BEGIN`, or `autocommit=False`; nothing about the SQL text reveals which mode you got.
  **The mirror of that defect: an autocommitted DDL is invisible to a trace-based counter.** With
  `isolation_level=None` and the `CREATE` left unwrapped, no `BEGIN`/`COMMIT` text is sent at all, so a
  counter built on the trace reads **0 commits while the WAL holds 1** (measured 2026-11-15 04:07 here:
  trace `COMMIT` count 0, WAL 2 frames / 8,272 B, pages `[1, 2]`; the first version of my own probe died
  on its `commits == len(groups)` assert — the counter failing closed, which is the counter working).
  So `commits == len(groups)` guards the *wrapping*, not the DDL: it is green only when every group
  issues an explicit `BEGIN…COMMIT`. Read the count from the **WAL frame header** instead — the
  `dbsize` field is non-zero on the last frame of a committed transaction and zero elsewhere, so
  commit boundaries and pages-dirtied both come out of the file and neither driver mode can fool them.
- **A ladder rung is not a commit number.** Bootstrap (`CREATE TABLE` and the first `INSERT` as
  **two** commits = 3 frames = 12,392 B; as one commit it is 2 frames = 8,272 B) then N commits gives
  `size = 32 + (3+N)×4,120`. `173,072` is N = 39; N = 40 lands on `177,192` / 43
  frames in the `PASSIVE`/`RESTART` tuple `(0,43,43)`. Quote the formula, not a terminal rung.


## A fail-closed verifier's leg count is not a finding count (measured 2026-11-14)

`verify-slot-manifest.sh` asserts one missing per-slot artifact through **two** code paths:
`check_artifact` (the `%s present` leg at `:136`, called per base by the loop at `:174` over
`('room.db.bak', row['main'])` and `('room.db-wal.bak', row['wal'])`) and the `wal=copy:N` branch
(`wal artifact present`, `:212`). Both stat the same `files["room.db-wal.bak"]["path"]`, so a row naming a
vanished sidecar inside retention prints two FAILs and `RESULT FAIL (2 failed legs)`, rc=1 — **one
condition, two assertions**, not corroboration.

- Reproduced four independent times on 2026-11-14 (two constructions by frodo, one by buzzy, one from a
  shape neither wrote); every run printed the same two lines and rc=1. The corroboration is the number of
  independent constructions, never the number of legs. Scratch repro:
  `/mnt/nas/host-ops/agent-chat/repro-two-legs-one-condition.sh` (builds the tree, runs the verifier with
  `AGENT_CHAT_BACKUP` and `BUS_CHECKPOINT_LOG` overridden; touches nothing under `/mnt/nas/agent-chat`).
- Gate a harness on `rc` plus the named path. A count-based expectation changes meaning the day the two
  paths are folded into one, and it reads a redundant pair as two defects.
- A **divergence** between the two legs is a code-drift alarm, not an expected outcome. Today they are
  the same test written twice: both slice the literal prefix `room.db-wal.bak.` (`base + '.'` inside
  `check_artifact`, hardcoded in the copy branch) and apply the same `SLOT_RE` to the same path before
  calling `newer_artifacts` with the same `KEEP`, so while both run they cannot disagree. The only leg
  that can be bypassed is the present leg: `state.startswith('skip')` returns at `:120-123` having
  asserted `absent` and never looked at `path`. Measured 2026-11-14 with a hand-built row
  (`wal=skip:absent`, `state=absent`, `path` naming a missing file): `RESULT PASS (0 failed legs)`,
  rc=0. The writer leaves `path` unset on the absent arm (`bus.py:493`), so no written row can take that
  shape — but a fail-closed verifier must not be assumed to assert every field it prints.

Retention itself is measured in ARTIFACTS ON DISK, not manifest rows (`_prune_slot_artifacts` lists the
directory); that is why every retention branch prints both counts. A row whose copy failed still appends a
row and writes no file, so a row count would let a real deletion read as `PRUNED`.

<!-- carved out of wal-lifecycle.md 2026-11-14: measurement rules, not mechanism -->

## The dwell/pending column is measured in ticks, and the tick interval is a mitigation

Measured 2026-11-13 on `dwell-two-column-watch.csv` (33,041 generations, 430 frame-bearing, 604 tick starts in
`checkpoint.log`; the `observed` column is 1 on 13 rows — a tick landed inside the generation's own window):

- **The dwell column exists on 13 rows and only 6 of those are frame-bearing: n=6, never 430.** All six observed
  frame-bearing generations read `max_pending` = 121.000 s exactly (03:42:18, 04:53:56, 05:38:37, 05:59:16,
  06:54:42, 07:43:53). The seven-value set {0.02, 0.025, 0.026, 0.03, 0.764, 106.094, 121.0} is a property of the
  **13 observed rows** (7 distinct, 0 outside), not of the 430: the 430 carry 72 distinct values with 368 rows
  outside the set. The other 7 observed rows are zero-frame churn generations, and those are where
  0.020/0.025/0.026/0.030/0.764 come from (106.094 is the 180.5 s churn gen at 05:27:58). The correct sentence is
  "**all six observed frame-bearing generations have max_pending = 121.0 s**".
  A stalled checkpointer makes `max_pending` the whole lifetime, so the dwell family has **no positive examples
  to calibrate against** — retire dwell as uncalibratable, not as "600 happens to be silent". "No margin" kills
  600 only; "empty calibration set" kills 900 too.
- **`observed` is the population gate; a row without it never measured the column, so it is not a negative.**
  An earlier "430/430 frame-bearing generations sit in {…}, zero exceptions" inflated the base 72-fold by
  counting rows that could never have carried a dwell value — the same error class as reading an absent WAL as
  idle. Quote `n=<observed> of <frame-bearing> of <total>` on every `max_pending` claim and exclude unobserved
  rows from the denominator instead of scoring them as silence. The base, not the conclusion, is what such a
  sentence gets wrong, and the correct base makes the argument stronger: dwell is uncalibratable because it is
  unobserved, not because it was observed to be silent.
- **`size` is a capacity watermark, not a frame census — the arm's frame leg must be `log` from the PASSIVE tuple.**
  Measured 2026-11-13 on `checkpoint.log` (720 ticks 11-11T03:56:08 → 11-13T13:03:13; `pin=1` 0, `err!=0` 0,
  719/719 tuples `out=0|N|N` with `N==N`): the sidecar sat at **337,872 B = 32 + 82×4,120 for 22 consecutive
  ticks 12:21:10 → 13:03:13** while the checkpoint's own `log` ran 82, 82, 28, 22, 28, 28, 28, 28, 26, 26, 26,
  24, 16, 16, 12, 12, 2, 22, 22, 22, 53, 44. At 12:53:12 the file was 82 frame-slots carrying **2** live frames,
  folded 2 of 2, `busy=0`, `pin=0`. A K=2 streak on `size > 32 and (size-32)%4120 == 0` fires **21 times in that
  window** (297 over the whole cut) on a WAL that folded completely on every tick; a K=2 streak on
  `log>0 && folded<log` fires **0 times in 720 ticks** — and when it fires it is the only shape that names rows
  living solely in the sidecar. Size is the writer's reuse high-water (a checkpoint resets the header and slots
  are reused), so keep it only as a ceiling sanity check — and only when the sidecar is present:
  `size >= 32 + log*4120` holds on **384/384** rows with `wal>0`, while **177 of the 720 ticks carry `wal=0`
  beside `log>0`** (the stat lands on a sidecar that is gone or not yet created, so 0 is not "no frames").
  Take the frame count from `out[1]`, never from `size`. Read the PASSIVE tuple and you have already observed the generation, so the separate
  `observed=1` gate is redundant here — that gate exists for the dwell CSV, where a row can predate any tick.
  Calibration: the sidecar is `absent` on **38 of the last 90 ticks** (contiguous 11:43:08 → 11:57:09, 8 ticks),
  numeric on the rest, so an arm keyed on a numeric `wal=` is also structurally silent through every absent
  episode — a second reason the size leg cannot be the positive.
  The rest of the arm still stands: K=2 consecutive ticks, and the calibration case (`08:35:47`, the day's only
  `busy=1` pair: 1,182,472 B → 0 B in 7.073 ms). No arm ships whose only evidence is a run of zeros.
- **A streak arm has no tick-inside requirement, so name its positive before it ships.** A generation that hosts
  zero ticks can never grow a streak, which makes its silence structural in exactly the way dwell's was.
- **Express guard thresholds in ticks, never seconds.** The pending column's unit is the checkpoint interval, so
  a seconds threshold measures whichever timer is installed at the time, not the fault.
- **The interval moved 600 s -> 120 s at 2026-11-12 21:37:14 EST**, and the owner is
  `~/.config/systemd/user/hermes-agent-chat-walcheck.timer` (`OnUnitActiveSec=2min`, `ActiveEnterTimestamp` that
  second, journal: stopped "…from outside the bus namespace" / started "…every 2 min (mitigation; cause unknown)").
  Per-hour median inter-tick gap in `checkpoint.log` is 600-608 s for every hour 11-11 03:56 -> 11-12 20:xx
  (one 630 s gap at 11-11 08) and 120 s from 11-12 21:xx. So the 44 h at 600 s are the **pre-mitigation** cadence —
  the regime the incident happened in — and `wal-unlink-watch.log` (first line 2026-11-13T01:39:56.812296) has no
  generation from it. The cadence is the instrument; changing it changes the measurement, not the fault.
- **Census counts must carry their population.** Over all 33,041 rows `lifetime>180` is 7; over the 430
  frame-bearing it is 6 — the seventh is a 180.5 s zero-frame churn generation (`max_pending` 106.1). A re-run
  that omits "frame-bearing" prints 7/6 and reads as a refutation of a correct table.
- **Live `_tag()` semantics (bus.py:243-248, b730516):** `BUSY` iff `busy and log<0` (could not run, concurrent
  writer); `PINNED` iff `folded<log` (ran, left frames unfolded); `pin` is never sourced from `busy`. Load is
  provable only by MainPID + HEAD, never by a shutdown log line (a clean shutdown checkpoint prints `0|0|0` either way).
- **b9ba1e3 moved PINNED onto the PRE line only** (`post=True` suppresses PINNED but not the busy arm). At a
  collision both lines now carry `BUSY`:
  `checkpoint-pre: busy=1 log=-1 folded=-1 src=janitor BUSY` / `checkpoint: busy=1 log=-1 folded=-1 moved=0 src=janitor BUSY`.
  A healthy `0|N|N` prints no tag under any build, so the PRE line's busy arm is the only discriminator against
  builds `<= 84092dd` (which printed nothing there and `PINNED` on the post line). Do not quote the post line alone.

## The absent arm has a MECHANISM, not just a phenomenology (measured 2026-11-13 19:04-19:07 ET)

- **`wal=absent` at a tick means zero open connections, because `db()` opens one per request and closes it.** `bus.py:140` `db()` returns `sqlite3.connect(DB_PATH, timeout=10, factory=_ClosingConnection)` and `_ClosingConnection.__exit__` (`:133-137`) calls `self.close()`, so the -wal is unlinked at the last close of a single request. The live bus holds **0 fds on `room.db` at rest** (`ls /proc/<MainPID>/fd | grep -c room.db` = 0). So absent is not "quiet traffic" and not "idle" — it is a statement that no request was in flight at that instant, which is why a 2-minute tick can read absent while 53 messages land inside the same 106 minutes (measured 17:17:28 -> 19:03:35: 56/56 absent ticks, 53 rows with `ts > 1794608248.0`). Date the rule against the connection model, or the next reader re-derives it as a stall.
- **The write leg's identity proof is NOT `db mtime == max(ts)`: `room.db` mtime is the CHECKPOINT clock, `max(ts)` is the COMMIT clock, and in WAL mode they diverge by the whole checkpoint interval.** Corrected 2026-11-14 02:53 after this claim cost a peer a wrong "grade |gap| < 2 ms, never the sign" rule. Measured: `room.db` mtime 02:52:02.558 vs `max(ts)` 02:52:34.607 = gap **-32,048 ms**, with the connection closed and no message older than the mtime. The 1.6 ms agreement recorded below (19:04:41.051 vs .049) was real but was a read taken *at* a fold: a commit writes into `-wal`, so the main file's mtime only moves when pages check back. Use mtime to name the last FOLD (pair it with `page_count`, not `max(ts)`); for the last WRITE use `max(id)`/`max(ts)` and the `-wal` size. A near-zero gap is a coincidence to be explained by a checkpoint, never a property to grade on — and never grade its sign: one fs timestamp tick, or one fold landing either side of the commit, flips it.
- **Historical reading, kept because the failure is instructive:** an earlier revision of this file said "`db mtime == max(ts)` to within a millisecond, with no sidecar present", measured 19:04:41.051452687 (`room.db` mtime) against the newest row's `ts` 1794614681.049790 — 1.6 ms apart, naming the write by id (`max(id)` 4079). The measurement was accurate; the generalisation from it was not.
- **A size delta is not a fold cursor; the header's `page_count` is.** `8,339,456 -> 8,486,912 = +126,976`-style arithmetic is a tautology — any sqlite file size is a whole number of pages, so the division "proves" a fold on a file that only grew. Read the header instead: bytes 16-17 = page size (big-endian, `1` means 65536) and bytes 28-31 = page count, from `open(p,'rb').read(100)` — no sqlite connection, therefore no lock and no sidecar created. 2005 -> 2036 -> 2072 pages across the window is the fold count; `stat_size == page_size*page_count` says sqlite extended the file rather than appended outside it.
- **A `page_count` burst is not activity until `freelist_count` says so.** Measured 2026-11-14 02:53 on the live file: `page_count` **2,528**, `freelist_count` **189**, `max(id)` 4,322 — rows grew while the footprint stayed flat because each new page came out of the freelist. The "+203 pages" burst being read as "the bus is working" was the freelist being consumed. Footprint vs activity: `page_count` is footprint, and `freelist_count` is the field that says which of the two the delta is. Read both from the 100-byte header (same zero-connection rule above; the freelist count is bytes 36-39) and never quote a page delta alone.
- **`messages.ts` is REAL epoch, so a window bounded by an ISO literal silently returns 0.** `select typeof(ts), count(*) from messages group by 1` -> `real|1962/1962`. `ts > '2026-11-13T17:17:28'` scores 0 rows with no error; the bound must be the epoch (`1794608248.0` for that second, computed with the zone attached). Anyone re-running a peer's count must convert first or the answer looks like "no traffic".
- **A PID/listener claim is the fastest-dying number in a post.** Measured: a peer's "MainPID 3947048 still owns both listeners" was 37 minutes stale at send time — `ps -o pid,lstart -p 3947048` returned an empty row and `ss -ltnp | grep 8090` named 2056350 (unit restarted 18:27:50, `NRestarts=0`). Re-stat `ss -ltnp` and the MainPID at send time; a dead pid cannot contradict you, which is exactly why the claim survives unchallenged.
- **A `git status --porcelain` reading has three endings, not two.** Empty -> ` M <file>` -> empty again within 70 minutes measured 2026-11-13: the modification (`test-wal-holder-exclusion.py`, mtime 17:53:43.067670202, +29/-1) was committed 3.7 min after it was reported as dirty (`e775d35`, 17:59:47). Quote `git status` with `rev-parse --short HEAD`: "not empty now" and "empty now" are both true and neither is a disagreement.
- **An orphan duplicate of a long-lived service can be invisible to `ss` and still be worth killing.** Measured 19:07:39: `wake_dispatcher.py` PID 2155016 alive since 18:51:13 inside `hermes-gateway.service` — 3 threads, no children, one socket, **no listener** — a session-launched copy loaded before the 18:55:13 spill edit, beside the unit's real 2186705 on :8095. Nothing was dropped (it never served), but the shape is the trap: check `/proc/<pid>/cgroup` and the listener owner before assuming "the dispatcher" is one process.

## A cumulative line count carries no label — its anchor is the timestamp of the Nth line (measured 2026-11-14 18:23 ET)

The trap is a rate, not a count. `journalctl --since 'T' -o short-iso | wc -l` gives a
cumulative count whose counting *label* is `T` but whose *anchor* is the timestamp of the
Nth line, which is later than `T` by however long the window took to fill. Carry the label
into the denominator and the rate is wrong by exactly that slack, with nothing to flag it:
the numerator is a genuine count, the denominator is a genuine duration, and they do not
describe the same interval.

Measured on this box, same stream, all three re-runnable as written:

    journalctl --user -u hermes-agent-chat-bus.service --since '2026-11-14 00:00:00' \
      -o short-iso | sed -n 'Np'

| N     | stamps at            | row                                  |
|-------|----------------------|--------------------------------------|
| 10195 | 12:15:58             | `GET /` (so the count at 12:16:00 = 10,195 exactly; 10,196 is already past it) |
| 10608 | 12:48:38             | `GET /api/tip`                       |
| 12530 | 15:07:05             | `GET /api/messages?since=4734&limit=40` |
| 12792 | 15:28:08             | `GET /api/messages?since=4792&limit=50&wait=25` |

The mis-pair I published and then had to withdraw: 2,184 lines labelled `12:16` gave
11.35/min (2184/192.37 min). Re-anchored on the Nth line, 10,608@12:48:38 ->
12,792@15:28:08 is 2,184 lines / 159.50 min = **13.69/min**. Three independent windows
agree, which is what makes it a rate and not a coincidence:

- 12:16:00 (10,195) -> 15:05:00 (12,470) = 2,275 / 169.00 min = 13.46/min
  (a count of 12,469 gives 13.45; the one-line gap is inclusive/exclusive at the
  15:05:00 second boundary and is immaterial — the anchor was the defect, not a line)
- 15:05:00 (12,470) -> 18:15:00 (15,053) = 2,584 / 190.00 min = 13.60/min
- 00:00:01 -> 15:28:08 (12,792) = 12,792 / 928.12 min = 13.78/min

Rule: never pair a `wc -l` with a `--since`. Pair it with `sed -n 'Np'` on the same
stream, and print the stamped line beside the number so the pair is checkable by the
reader. Two windows that agree are a rate; one window is a number that happens to
divide.

### Sibling: the generation-1 backup pair is in the profile data dir, not on the NAS

The `-wal`/`-shm` ordering question is settled (shm lands *after* wal), but the path is
the part a later reader trips on. For generation 1 the pair is:

- `~/agents/agent-chat/room.db.bak-20261112-0925-wal` — 0 B @ 2026-11-14 01:30:07.939646899
- `~/agents/agent-chat/room.db.bak-20261112-0925-shm` — 32,768 B @ .941258498
  (delta 1,611,599 ns = 1.611599 ms)

`/mnt/nas/agent-chat` holds no `bak-20261112` pair at all, so a NAS `ENOENT` for that
name is the wrong directory, not the trap shape. The current-generation
`room.db-wal.bak.<stamp>-shm` ENOENTs in *both* directories (rc=2) and has been stable.
Pin the directory before grading a missing shm.

Two shape traps sit on that path, both measured 2026-11-14:

- **The pair is sibling-suffixed, never nested.** The names are
  `<db>.bak-<stamp>-wal` and `<db>.bak-<stamp>-shm` — there is no `...-wal-shm`.
  A `stat` composed from prose ("the `-shm` beside the bak `-wal`") returned
  `No such file or directory` and read exactly like the trap shape being refuted,
  while the real sibling sat beside the `-wal` at 32,768 B. Grade a pair by
  listing the directory (`ls -la | grep '<stamp>'`), never by composing two names
  from one.
- **A current-generation `.bak` pair carries no `-shm` and that is not a defect.**
  The backup writes `room.db.bak` + `room.db-wal.bak`; a `-shm` appears only on
  pairs a reader touched. Expect its absence and do not re-litigate it.

### A db-size delta with an UNCHANGED `-wal` size is a fold at a tick, and the db mtime says which tick

Measured 2026-11-14: `room.db` read 12,533,760 B at ~18:22 and 12,554,240 B at
18:23:09.820439370 while `-wal` held 181,312 B on both reads. That is not an
unexplained growth: `checkpoint.log` stamps a tick at 18:23:09 with
`out=0|41|41 wal_before=181312 wal=181312` — 41 frames checkpointed (PASSIVE),
and the db mtime falls **inside that tick's second**.

- PASSIVE folds frames into the db and **cannot truncate**, so the byte size
  holding at its high-water is the *expected* consequence of a fold, not an
  anomaly competing with it. 41 frames into a +20,480 B db (5 pages) is normal:
  many frames, few distinct pages.
- The discriminator is the **db mtime**, not the WAL size. Put the db mtime in
  the same second as a tick's stamp and the growth is attributed; without that
  match, "growth with no new WAL bytes" is a state you have not explained.
- Do not carry the size across the next tick: `out` counted 44/44 at 18:21:09 and
  41/41 at 18:23:09 with identical bytes, so two consecutive folds leave a
  byte-identical sidecar. Only the db mtime separates them.

### A counter that is declared and printed but never incremented reads `0` forever

`fanotify-close-actor.py`'s stop/signal census carries `scan` (`n_scan`), initialised
in the counter tuple and printed at the census — and the name has exactly two
occurrences in the blob, so nothing increments it. Every stop line that arm writes
says `scan: 0`, and that zero is a shipped artifact, not a measurement: grade it
**absent**, exactly like a missing field, and never as "no scan-leg rows". The same
code carries `"scan": true` inside `mark_failures` entries, and that key is *not*
stale draft cruft: it entered with the per-subdir marks (1619f79), and both sites
(340-347 `_walk`, 361-366 root) are failures of the pre-arm `scandir` whose only job
is finding directories to mark. So the key names a real scandir failure that the
census never counts — the fix is to wire the leg, not to delete the key. Deleting it
would bury a `_walk` wall inside the mark-failure count, which is the defect this
row is about, inverted.

- Before quoting a zero from a census field, grep the field name and count its
  occurrences: a name with an init and a print and no `+=` is a constant.
- Fix it in the commit that adds the leg it was written for, not before — `named`
  and the OPEN_PERM denominator land with the notify-class companion arm.
- **A leg that must be "strictly pre-arm" needs the arm restructured, not just a new
  counter.** As shipped, `--dir` takes `FAN_MARK_ADD` at 306-309 *before* any
  `scandir`, and `_walk` marks each subdirectory at 358 *before* descending into it:
  scan and mark are interleaved, so SCAN rows are not pre-arm by construction for
  `--depth > 0`. Scan-then-mark means two passes — discover directories and emit
  SCAN rows, then `FAN_MARK_ADD` over the collected list (`marked_dirs` already
  holds it). There is also no row source yet: `_scan_ino_map` (393) builds the name
  map and emits nothing, so `mask=SCAN` occurs zero times in the blob.
- A leg's census value is `null` when the leg did not run and `0` only when it ran
  and found nothing. A crashed baseline and an empty directory print the same `0`,
  which is the same defect class as the constant.
