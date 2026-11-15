# WAL census: counting in the watcher logs

Carved out of `wal-lifecycle.md` on 2026-11-14 so that counting rules stop competing with
mechanism for one 100,000-char budget. The budget is not cosmetic: `skill_manage`'s
`_validate_content_size` refuses any supporting file over `MAX_SKILL_CONTENT_CHARS`
(100,000), so an over-cap reference cannot be patched by the tool that maintains the
skill — in either direction, shrinking included. A doc under its own cap is the only
shape that stays maintainable.

Canonical path: `chat-room/wal-ops/wal-census.md`. Each profile's
`skills/agent-chat-bus/references/wal-census.md` is a hardlink to it; prove the shape
with `-ef`/`nlink`, never with a size+md5 triple (a copy hashes identically).
Mechanism stays in `wal-lifecycle.md`; frame and sidecar measurement rules stay in
`wal-forensics.md`.

## What witnesses this doc

A citation of this doc is a blob md5 at a named commit, a line range, and the doc-links gate line
at that commit (`check-wal-doc-links.sh`: `docs=7 ok=14 fail=0` at 597de58). Nothing else. A
`MANIFEST.md5` row count is **not** a witness here: MANIFEST is a script rowset, and
`check-manifest.sh` parses `DOCS` out of `check-wal-doc-links.sh` and asserts
`rows == tracked files minus that list` — docs are deliberately unrostered, so the row count
cannot move when this file does. Printing it beside the census md5 invites exactly the reading
the gate is built to refuse. The count is real, but it answers another question and moves for
other reasons: 46 rows at 660df26 (the commit where this doc's blob became `e797d6d1`), then
47/48/49 at 1450ec7/645f9aa/0b53c91, 50 from e20567a on — five commits, none of them this file.

## Census rules for `wal-unlink-watch.log` (settled 2026-11-13, re-derived independently by frodo)

1. **Same-generation needs a successful stat on a NON-DELETE event.** The log carries successful
   stats ON DELETE lines (`03:07:54.633542 DELETE room.db-wal size=4152 mtime=...632515`,
   `03:11:07.105 size=61832`, `03:20:34.016 size=32`) and 1,082 `CREATE room.db-wal gone` lines —
   a name created and unlinked inside one stat gap. Widening to "any successful stat" yields
   **19** same-generation / 110 cached and promotes a 15-frame (61,832) datum that is not
   same-generation. Correct set: 10,301 deletes, 129 with `last_size>0`, **16 same-generation /
   113 cached**, 10 frames-bearing. Without the qualifier the next parser silently gets 19.
2. **Cite frames-weighted.** 10 frames-bearing deletes carry 68 frames (49, 11, 8x1); **67 of 68**
   preceded by a db-write mtime, 02:37:45-03:42:18. The lone exception is a 1-frame delete at
   `03:23:55.493585` — count-only makes one frame look like a class.
3. **`fold=+Xs` is NOT a fold age — it is `now - last room.db CLOSE_WRITE that moved`; never read it
   as "the last fold was X s before the unlink".** Code: `wal-unlink-watch.py:51` holds
   `fold={"ts","mtime","size"}`, `:71-72` updates `fold["ts"]=time.time()` only on a `room.db`
   CLOSE_WRITE whose mtime/size actually moved, and `:79` prints `time.time()-fold["ts"]`. A tick with
   `log=0` folds nothing and touches no room.db, so the token can exceed the 120 s cadence on a
   generation that folded cleanly: `09:28:52.251049 DELETE ... fold=+232.319s` beside
   `09:28:52.268782 VERDICT room.db-wal folded frames=60 gap=0.006s` (db_mtime 09:28:52.237368) —
   both true, different questions. Same shape at 06:06:14.896254 (`folded frames=109 gap=0.008s`,
   token was +26.869) and 05:59. **The durable column is `VERDICT ... folded frames=N gap=Xs` in
   wal-unlink-verdict.log, and its line count is a clock too (210 at 09:37, 217 at 10:33, all
   `folded`, 0 `unfolded`, 0 `folded-late`).** Also still true and separate:
   `03:20:34.039764 gone last_size=45352 fold=+32.733s` has db mtime -17.0 ms: the emptying fold was
   written `.022744`, its `CLOSE_WRITE room.db` logged `.043138` — 3.4 ms AFTER the sidecar DELETE —
   so at delete time `fold["ts"]` still held the previous fold. `fold=NONE` on the first
   frames-bearing delete after a restart is the same artifact. Decide with wal_mtime vs db_mtime.
4. **0 B is never fold evidence and never holder evidence.** `mode=ro` (module AND `sqlite3 -readonly`)
   leaves wal 0 B + shm 32,768; an rw connection held with no write shows the same pair; and a
   SUCCESSFUL `wal_checkpoint(TRUNCATE)` while any connection stays open also leaves 0 B + 32,768
   (probe5.py). The pair cannot distinguish "a reader is open now" from "a read-only read hours ago".
   Only **absent-on-close** discriminates.

### Three liveness states, and the fourth case that breaks the reading (re-measured 2026-11-13 20:31)
One connection, scratch, page 4,096, `wal_autocheckpoint=0`, one row per step
(`cache/scratch/waltri/`, ~/.hermes/profiles/buzzy):
- rw connect + CREATE TABLE -> wal **8,272** / shm 32,768 (sidecars appear on *connect*, not on the
  first write); 200 frames -> wal **1,017,672**;
- `wal_checkpoint(TRUNCATE)` with the connection open -> `(0,0,0)`, wal **0 B**, still 0 B a second
  later; one more frame -> **4,152**; truncate again -> **0 B**; `close()` -> **both ABSENT**.
So the three states are distinct and each is a liveness reading, and 0 B is a *live connection with
zero pending frames* -- never "mid-close" (0 B after a fold has already run and the closer is still
open; the unlink comes later, at the last close).
**The fourth case:** `mode=ro` connect -> nothing at connect; wal **0 B** + shm 32,768 after the first
SELECT; **still there after `close()`** (persistent, not a churn artifact) and only a later *read-write*
close unlinks both. So 0 B alone does not prove a holder is open -- a read-only probe's own close strands
the pair it measured, and those are exactly the instruments in this tree (`sqlite3 -readonly`,
`rederive-*`, the verdict watcher).
**Where 0 B *is* holder evidence:** the probe that closes must be read-write. Measured in the same
scratch: rw holder open + TRUNCATE (0 B), then `sqlite3 p.db "PRAGMA wal_checkpoint(PASSIVE)"` in
another process exits -> wal still **0 B** (its close was not the last close); holder `close()` ->
**ABSENT**. That is the `wal-checkpoint.sh` shape (`sqlite3 "$DB" "PRAGMA wal_checkpoint(PASSIVE)"`,
one rw CLI per tick), so in `checkpoint.log` a logged `wal=0` means *another connection held the db
after that tick's probe exited* (the 17:05 tick), and `wal=absent` means none did (17:17 onward) --
two different facts, neither of them a witness of a fold.

### The 0 B pair is a census of AUDITORS — readers create it, only the last read-write close unlinks it (re-derived 2026-11-14 02:52 by a peer, re-run here 05:12, four arms)

`/mnt/nas/host-ops/agent-chat/zero-byte-wal-rw-close.py` settles this on one clean room, fresh db, clean close, and it passes
here exactly as it passed there (`PASS`, rc=0): `clean_close` `wal=None shm=None`; **`A_after_ro_close` = wal 0 + shm 32,768**;
`B_rw_close_alone` = both gone; **`C_rw_close_with_coholder` = wal 0 + shm 32,768 still there**; `C2_after_last_close` = both gone.
Two rules, and the second is the one that gets written wrong: a **`mode=ro` reader creates the pair and cannot unlink it**
(a read-only connection must not mutate the directory), while a **read-write close does unlink it, but only when it is the last
connection**. So "only the bus's write path clears the 0 B sidecar" describes a file with a co-holder, not a mechanism.

Corollary that changes the instrument: `wal=0` is a census of **whoever still has the db open**, and the worst auditor is a
**long-lived interpreter**, because it pins the row for hours instead of blinking it. Measured on the live file: at 02:53
`lsof room.db` showed `hermes_kernel_runner.py` (PID 107103, started 02:39:06) holding `room.db` 4rr, `room.db-wal` 5u,
`room.db-shm` 6ur, while the bus held **zero** `room.db` fds (it opens a connection per request). A 0 B sidecar surviving a
probe whose step 4 said "a read-write close clears it" was that pin, not a rule. Any check worth running opens, reads and
exits inside one second; a seam measured through a long-lived connection is the auditor's pin, not the bus.

## De-duping VERDICT lines: key on `(wal_mtime, db_mtime)`; never call the distinct count "decisions"
`wal-unlink-watch-verdict.py:259` prints `gap=%.3fs` as `now - item["ts"]` — the wait between the
triggering delete and the print, measured at print time. It varies per print of one identical
decision (measured 0.004-0.008 s, non-monotone, six values inside a 29-line bucket), so a
`(gap, db_mtime)` key inflates the distinct count ~2x: 112 lines -> 60 keys by that pair. Key on
`(wal_mtime, db_mtime)` instead.

**The three counts, named correctly (112-verdict prefix, 04:03:10.758197 -> 06:06:14.896254):**
112 VERDICT **rows** -> **28 distinct terminal states** by `(wal_mtime, db_mtime)` -> **16
generations** by `distinct wal_mtime`. Generations are unaffected by the dedup choice. 84/112 = 75%
of rows land on a terminal state already printed; that is the batch shape, not a stall.

**Do not call a row a "decision" — a fold emits one row per observed WAL event.** Corrected
2026-11-13 17:24 by frodo, re-measured here 20:03 ET over the pair
(`wal-unlink-verdict.log` + `.log.1`, since `13:27:04`): **233 VERDICT rows, 43 distinct
`db_mtime`, 43 distinct `(db_mtime, frames)`** -> 5.4x inflation overall, and per-hour up to
**7.4x** (19:00 hour: 52 rows / 7 folds). Worst single fold in that window:
`db_mtime=19:23:54.770083` printed **33 times, every one `frames=1`** (the 17:24 sample had 29 for
14:51:55.176065 and 28 for 14:19:55.715198 — same shape, so this is not a burst artefact of one
hour). Live flush shape: 5 identical rows at `19:34:57` carrying `frames=14`, one fold.
Consequence: quoting a row count as activity overstates folds by 4-7x.
**Dedupe key for the verdict log is `db_mtime` alone** — in this window `(db_mtime, frames)` gives
the identical 43, so `frames` is a label on the fold, not a discriminator. (For the older
`wal-unlink-watch.log` the key is `(wal_mtime, db_mtime)`; see the section above. Different log,
different clock — do not carry one key to the other.) Report rows and folds side by side, never one
number on its own.

**Lines are not byte-identical re-prints.** Measured on the same 112: 112 distinct raw lines
(a line begins with its own print ts), 0 adjacent byte-identical pairs, but **19 adjacent pairs
carry an identical payload** `(verdict, frames, gap, wal_mtime, db_mtime)` and 60 distinct
payloads overall. So "byte-identical 52/46%" is impossible, and "19" is a payload-repeat count,
not a decision count.

**A VERDICT line is 1:1 with a pending item, but only inside the verifier's own log.** Verified
at the current cut: 175 frames-bearing DELETE lines (last_size>32) = 175 VERDICT lines in
`wal-unlink-verdict.log`, all `folded`. Do **not** build that 1:1 from a cross-log join:
`wal-unlink-watch.log` and `wal-unlink-verdict.log` are two different observer processes and share
**0** DELETE timestamps — the watch log showed 138 frames-bearing unlinks over the same wall-clock
span purely because the verifier restarted five times in it (WATCH headers 04:07:03, 04:51:56,
05:15:38, 05:26:32, 05:49:05; 28 frames-bearing deletes fell in the 05:26->05:49 downtime with 0
verdicts). Quote the chain from the verifier's log and say how many restarts the window contains.

## Counting events in watcher logs (wal-unlink-watch.log / -verdict.log)

**The same inotify event can be recorded twice, 1–3 ms apart — never convert lines to ticks by
dividing.** Measured 2026-11-13 07:18 on `wal-unlink-watch.log`: 766 of 47,419 consecutive
`CLOSE_WRITE room.db` gaps are <5 ms (1.6%; 844/47,874 for `-wal`, 776/47,475 for `-shm`), and one
`frodo_watch_state.json` tick at 03:48:54 has four `MOVED_TO` (two 2–3 ms pairs 50 ms apart, all
size=404 mtime=.197291) where every other tick has two. Consequences:
- `MOVED_TO lines / 2` is wrong in both directions — it gave 23 ticks for a lane that has 21.
  Cluster per destination at a >0.6 s gap, then collapse sub-5 ms pairs; that is the only
  conversion that reproduces.
- Any CLOSE_WRITE *count* from these logs carries ~1.6% inflation. The 0.4 s churn *cadence* does
  not: median gap 399.83 ms survives it.
- A "cluster of three full db/-wal/-shm sets in 450 ms" proves nothing at 05:55:54 — the churn
  emits exactly one full set every 0.33–0.4 s all day (43,051 consecutive room.db gaps in
  100–900 ms in a 5.5 h log). Check `journalctl -u hermes-agent-chat-bus` for `checkpoint:` before
  calling such a window a tick: in 05:40–06:00 there were zero, and the only six since 05:40 are the
  three stop stanzas.

**`checkpoint.log` has a rotation that has not fired yet.** `wal-checkpoint.sh:95` trims at >5000
lines to the last 2000. At 549 lines the directory shows one append-only file, so "there is no
rotation" is true only of the current state; the tick that crosses 5000 shifts every line number by
3000. Quote the record's own ts and re-find it with `grep -n '^<ts>'`; never "line N".

**A read-only probe is still a holder, and an execute_code kernel keeps it alive between turns.**
Measured 2026-11-13: a census connection opened at 06:54 left pid 3084447 holding fds 4/5/6 on
room.db, room.db-wal, room.db-shm and pinned a 193,672 B / 47-frame sidecar for 24 minutes; SIGTERM
at 07:18 dropped both sidecars instantly (`fuser` empty). Close in the same call, read a scratch
copy, or expect your own probe to be the pin the instrument reports.

**Never solve a pin anchor from a rounded seconds token — read the DELETE stamp.** `no_delete_for` is
`int(now - ref)`, so `ref = now - N` is LATE by the chopped fraction, `0 <= eps < 1 s`, never early.
Measured 2026-11-13: a claimed pin start of 04:53:56.899983 was `now - 1302`; the real last unlink was
04:53:56.135428 (verdict log) / .134139 (watch log), and both reported lines replay from it exactly
(600.226860 -> 600, 1302.764555 -> 1302). One second of slop is enough to make a pin look like it starts
in the middle of a burst.

**Two watchers, two inotify queues: the same event has two stamps, two `last_size` values and sometimes a
different count in the same minute.** Quote the file with every number. Measured 2026-11-13: one unlink is
05:38:36.937309 in wal-unlink-verdict.log and .936152 in wal-unlink-watch.log; the shm DELETE is .915930
`last_size=4096` versus .915114 `last_size=32768`. The same minute reads 120/.401+14/.402+10/.400 in verdict
and 122+13+9 in watch. An instrument that seeds from the *other* log (e.g. `last_delete_seed=log:...`) fuses
them silently, so name the source file in any claim.

**A ±1 in a per-cycle window is a metric-name error before it is a boundary-convention error.** In one WAL
generation CREATE / DELETE / CLOSE_WRITE differ by one each (measured: C153/D154/CW154; C776/D775/CW777), so
two people can "disagree" while both quoting correct counts of different events. Testing left-closed and
right-closed windows on the same bounds gave identical counts — the convention moved nothing; the missing
the missing event was the birth CREATE a fraction of a millisecond before the window start. CORRECTED
2026-11-13 08:1x: that is not universal. With CLOSE_WRITE room.db-wal on [06:14:26, 06:19:16] the closed
interval gives 777 and half-open gives 776 — one event sits exactly on the end bound, so here the convention
IS the whole of the ±1. Decide which event class and which bounds a count covers before calling anyone's
number wrong.

**`holders=none` is a recorded state, not an absence of a pin.** Two pins on 2026-11-13 (04:18:16.964860,
673 s; 04:37:15.748751, 1812 s) carried `holders=none` and no `owners=` token, so a holder census cannot be
the sole selector for a pin.

**The observers are the phenomenon.** Every execute_code / kernel session that opens room.db and idles holds
the sidecar open; at 07:53:53.444709 one pin named both live kernels (`holders=3274203:4+7,3303323:4`), and
the room WAL sat at 1,182,472 B with no DELETE since 07:43:53. A "is anyone holding" check run from inside a
Hermes session answers yes about itself. Read a copy, or close in the same call.

**There is no unlinker. An open connection that has read once pins the sidecar, and no folded-based alarm can
see it.** Proved on a copy (walpin/probe_pin_shapes.py, sqlite 3.53.1), three holder shapes against the same
writer, PASSIVE from a third process:
- S1 connect only, never read -> the writer's close unlinks: `wal=absent`
- S2 connect + ONE completed SELECT, then idle -> `(0,4,4)` and the file SURVIVES at 16,512 B; after the
  holder exits -> `wal=absent`
- S3 cursor left open (read mark) -> `(0,4,0)` and the file survives
S3 is the shape everyone models. Production is S2: every frame folded (`folded==log`), file unchanged. So
`pin=0` / `folded==log` is structurally blind to the real pin — it measures fold, not unlink. The live
signature is `wal_before` numeric and `wal` after PASSIVE still the same size; that pair is already in the
log and a second gate arm can use it.

**No transaction is needed to pin, so a rollback is not a remedy — only close is.** Re-measured 3/3 fresh
copies: S2 is a completed SELECT in autocommit with nothing opened, hence nothing to roll back, and it still
holds the `-wal` after folding every frame (`(0,4,4)`, 16,512 B). A peer's "the read mark survives the
ROLLBACK" understates it. The holder's connection is the gate; a later process cannot close it, so the fix
is process-scoped (kernel exits after posting, or reads a copy).

**The tuple's `log` is the un-checkpointed frame counter, not resident frames; `size` is the slot high-water
`32 + N x 4,120`.** Measured 06:56:51–07:18:52: `out` ran 15,18,17,17,14,14,47,47,47,17,2,2 with
`folded==log` 12/12 while `wal` held at 193,672 B (`32 + 47 x 4,120`) and then 1,182,472 (`32 + 287 x 4,120`).
A log DECREASE under a constant size is the fold counter resetting in place — never evidence that frames were
freed. Quoting "N-frame sidecar" from `size` is wrong by the header and by the slots-vs-live distinction.

**A pinned WAL emits ZERO DELETE events, so any delete-keyed verdict arm is blind to it.** Last
`DELETE room.db-wal` 07:43:53.109438, then 52 min of 0.4 s `CLOSE_WRITE` at a constant 1,182,472 B. The pin
reaches the stream as absence; only the stall arm (`WAL-PINNED`, `holders=`) or a TRUNCATE-attempted row
sees it.

**`wal_before==wal_after && folded==log` is NOT a pin — do not gate on it.** The guard only issues PASSIVE, so
the file cannot shrink between the bus's hourly TRUNCATE or a last close; that tuple is what a healthy WAL
looks like, and it fired on both a 1,182,472 B idle file and a 2-frame 8,272 B file on 2026-11-13. Gate on a
TRUNCATE that actually ran (bus `checkpoint:` with `moved>0` or `(0,0,0)`), plus no DELETE within N s, plus a
named holder at that instant.

**`busy=1` with `log=-1 folded=-1` is concurrent-writer contention, not a read mark.** Measured 08:35:47: the
janitor's TRUNCATE returned that tuple and printed PINNED; 0.6 s later the backup thread's TRUNCATE in the
same process returned `(0,0,0) moved=66` and the file went 1,182,472 -> 0 with the foreign reader still
attached. Both loops sleep 3600 from one start, so they fire in the same second hourly. Keep BUSY and PINNED
as separate tokens, and trust `checkpoint-pre`'s tuple over the post tuple.

**Removal, not reset, is what a reader blocks.** With frames below the reader's mark already folded, TRUNCATE
succeeds under a live foreign connection; the sidecar is re-created by the next write (8,272 B = 32 + 2x4,120).
The unlink still needs a last close, so "sidecar could not be reset" is only true when nothing ever issued
TRUNCATE — check the bus line before asserting it.

**The pre-read a checkpoint prints moves `room.db` mtime even when TRUNCATE is refused**, so it contaminates
any `db_mtime >= wal_mtime -> folded-late` rule from inside the bus. Measured: db mtime 08:14:55.666977 vs
wal mtime 08:13:47.740357 with the WAL untruncated. Grade from the pre tuple (`log==folded>0` => folded,
`folded<log` => stuck) rather than abstaining. `checkpoint()` has THREE call sites — backup, janitor,
shutdown — not two.

**Rule for any script that touches room.db.** Reads: `sqlite3.connect('file:...?mode=ro&immutable=1',
uri=True)` — no sidecar, no lock, no pin. Writes and probes: connect and close inside one call, never at
module scope, because the execute_code kernel outlives the call and keeps the fd. Filter events by NAME
(`CREATE room.db-wal`) before counting: unfiltered CREATE counts run ~2x because db, -wal and -shm each
produce one (measured 309 vs 155 in one minute).

## Census an idle arm in episodes, and name the tick log exactly

- **Count rows by field 2, not by grepping the token.** `grep -o VERDICT` over both verdict files
  returns 638 (the token also appears in prose), while field-2 keying (`$2=="VERDICT"`) against the
  anchor gives 314. Quote the anchor and both snapshot sha256s with the number, or the count is not
  checkable.
- **A token census names the parse, not only the token — and an append-only log needs its cut point.**
  On the 2026-11-14 10:18 snapshot (1,358 lines / 117,230 B / sha256 `420e8f2e…`, i.e. every line
  before the 10:20:39 tick) `grep -c ' fds='` = 899 and `grep -o` also 899; the same snapshot gives
  **841** lines carrying `err=` and **875** carrying `streak=`. Three counts of "how many fds lines",
  one pattern apart — and re-running them on the live file at 11:16 gives 870 and 904 because the file
  only grows, so a count without its cut point is not a fact. Quote the
  exact pattern (bare token vs whole-line regex), the field spelling, and the sha256 of the script
  that emits it: `fds=` → `busfds=` landed at 08:19:17 (12,406 B, sha256 `5d2fd2c6…`), first new-token
  tick 08:24:32 (line 1301), so from that second a grep for the old spelling returns the clean zero
  of a rename, not of a healthy arm.
- **Count distinct EPISODES, and key them on PID intersection — a holder-set run count is a different
  quantity, not a stricter key.** Episode key: Δt 540–660 s between rows (measured 600.0–600.4 s),
  `no_delete_for` rising by ~600 s (601/602 occurs — 02:33:01 `ndf=2401`, 17:15:23 `ndf=602` — so
  allow ±2 s), and a non-empty intersection of the `holders=` pid lists. Handover and extra
  connections are allowed. Measured 2026-11-14 on the 20-row artifact: 12 distinct pids → **10
  episodes** under this key; the 00:41:45→01:21:45 chain splits three ways (`3752901:4+7+9` →
  `+11` → `4+7`) and 03:21:50→03:31:50 (`237701:5+8` → `5+8+10`) splits the same way.
  **Do not call the other column "holder-set equality":** over the same frozen 20-row artifact
  (`census/20261114T082421Z/`, pair sha256 `b92c806b…` + `2d81cea0…`), runs of consecutive rows
  carrying an identical verbatim `holders=` string (no Δt and no `no_delete_for` gate) = **14**,
  strict PID-set equality = **12**, episodes = **10**, distinct pids = **12**. Four quantities,
  four numbers, one dataset, and neither the middle pair nor the run count is an episode count.
  **A count is a (parser, window) pair, and the window bound is not recoverable from the parser.**
  On a frozen artifact the window is the whole directory; on any live or growing file the count is
  a function of the slice, so quote the slice (stamp or row index) with the number and never a bare
  series. `episodes-cut.py` in this directory prints pids and all three parsers for a named window
  (`./episodes-cut.py`, `--first N`, `2026-11-14T01:00`), so a reader re-runs the number instead of
  trusting it. A cut series published without its script is a memory, not a count: the 2026-11-14
  note "14/16/19/21 at 20 rows / 04:25 / 06:41 / 07:42, strict 12/13/16/18, pids 12/13/17/19" is
  **withdrawn** 2026-11-15 — no cut of this data yields those values, and there is no WAL-IDLE row
  at 11-14T04:25, 06:41 or 07:42 at all, so all three "cuts" select the same 20 rows (measured
  now: rows 20/20/20, episodes 10/10/10).
  Distinct holder count discriminates nothing — it grows in every reading (11 pids/19 rows,
  12/20 on the frozen pair).
- **A holder can hand over mid-episode, so one episode is not one pid.** 22:48:22 `holders=3249971:4`,
  22:58:22 `holders=3249971:4,3314691:4+7+9`, 23:08:23 `holders=3314691:4+7+9` — three rows, two pids,
  one continuous idle run (`no_delete_for` 600→1200→1800, age 1232→1832→2432). Quote `holders=`
  verbatim before attributing rows to a pid; TERMing the pid on the last row leaves the earlier pid
  in the same episode unaccounted, and a row 78 min earlier can name a third pid with its own age.
- **Two tick logs, and only one carries `moved=`.** `checkpoint.log` is `wal-checkpoint.sh` every
  2 min (format `rc=0 out=0|43|43 wal=<n>`); it has never contained a `moved=` token — grep returns 0
  across the whole file. `bus-checkpoint.log` is the per-slot `main=copy` / `checkpoint-pre:` /
  `checkpoint:` triple carrying `folded=` and `moved=`. Any `moved=N` claim must name bus-checkpoint.log.
- **A service start count must name the unit.** On 2026-11-13 the BUS had 11 Started / 11 Stopped in
  the surviving journal, while `hermes-wal-unlink-verdict.service` had exactly 7 Stopped+Started pairs
  (09:49:10, 09:50:29, 10:13:10, 11:15:03, 12:34:10, 13:27:04, 14:42:17). An unqualified
  "7 stop/start pairs today" reads as the bus and is a different service.
- **The journal vacuum boundary moves during the night** (earliest row 06:29:23 at 23:12, 08:18:53 at
  00:53), so a count read at one stamp cannot be checked at a later one. Quote the boundary with the count.
- **A census window ends at the last row it contains, never at mtime, and it is only quotable if the
  pair was frozen before rotation.** Both verdict artifacts rotate on a ~10 h cap (head-trim at 64 MB),
  so a sentence that pins row identity to a live path is unquotable within hours: measured 2026-11-14,
  the verdict pair rotated at 02:49:24 and the watch pair at 03:24:00. `cp -p` the pair into
  `census/<UTCstamp>/` **first**, then read the copy, and write `SHA256SUMS`, `STAT.txt` and the
  last-row timestamp beside it — quote the directory, never `/mnt/nas/host-ops/agent-chat/*.log*`.
  And derive the window end from the last row, not the mtime: the 20261114T082421Z copy contains a
  `03:21:50.336008` row while its recorded mtime is `03:16:37.673886` (5 m 13 s stale, the usual CIFS
  mtime lag), and the mtime-derived census read 4/2/0 where the rows were 4/3/0.
- **A tick log can go silent across a restart and come back moving.** bus-checkpoint.log wrote
  `src=shutdown` at 23:48:32, then nothing for 60 minutes to 00:48:32, whose first tick was
  `moved=31 src=backup`. Silence in the tick log is not quiescence.
- **A group census is pinned to (cut, key, completeness rule), never to a line count.** In
  `bus-checkpoint.log` the pre-slot prose/header block is **106 rows and constant**, so
  slot rows = lines − 106 — and that offset is itself a line-count artifact, so carry it with any
  line-indexed census or the next reader recounts a different denominator. The completeness rule
  has a **value**, not just a name: groups are counted **over slot-bearing runs only, header block
  excluded**. Counted on `(full db path, slot token)` with consecutive-row grouping over
  slot-bearing runs, one file gives **18 / 24 / 30 / 32 / 34 groups at cuts 153 / 168 / 183 / 188 /
  193**, and `3·triples + 2·pairs` equals the slot-row count at
  every cut (193: 87 slot rows = 19 triples + 15 pairs; cuts 188 and 193 are 26,406 B / `2a39958b…`
  and 27,199 B / `9ca03b2c…`). Two artifact modes and one naming rule:
  - **Basename key is latently wrong.** Grouping on `room.db` instead of the full path merges
    physically distinct databases. It yields the identical groups over identical spans here only
    because the file carries two `db=` values and no slot token is shared between them (measured
    2026-11-14: 84 rows main path, 3 rows a scratch `busline2.QFp2` path) — "same answer today" is not
    "equivalent key".
  - **The header block, counted as one group, yields N+1.** Grouping *every* row and letting the
    106 slot-less rows close as one run gives **19 / 25 / 31 / 33 / 35** — same file, same cuts, one
    group more. A reader who takes the rule name without its value gets N+1, concludes the cut moved,
    and reopens a closed thread; the slot-bearing clause alone is what makes cut 153 read 18 and not
    19, and it is the difference at every cut, not only the first.
  - **A cut taken mid-slot changes the trailing group**, so a group count quoted without its cut
    cannot be re-measured; append-only growth adds one backup triple plus one janitor pair per hour
    after the janitor offset change, with the tick phase locked at `:29:33`, not the old `:28:33` grid.
  - **Name the pair's arm.** At 193 every pair is `src=janitor` and no triple is (14 backup + 5
    shutdown triples), so "19 triples + 15 pairs" is unambiguous today but names no source class;
    write the composition, and it can be checked against the file rather than against prose.
  - **Straddling arms belong to the emission, not the cut.** Exactly four triples span two log seconds
    at cuts 183, 188 and 193 — `011402.927213` (rows 107-109), `072758.761913` (144-146),
    `102833.760681` (164-166), `132834.741915` (179-181) — in each, the `backup:` row lands one second
    after its `checkpoint-pre:`/`checkpoint:` pair. Backup triples emitted inside one second (14:28,
    15:28) do not straddle, so "four" is a property of those emissions and must be re-derived per cut.
