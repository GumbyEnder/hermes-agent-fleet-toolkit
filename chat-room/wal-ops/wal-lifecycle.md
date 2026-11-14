# WAL lifecycle: the `disk I/O error` class, the tick, and the sidecar gates

Moved out of SKILL.md 2026-11-13 (body-size split; nothing deleted). Everything below is measured
on the live bus (`~/agents/agent-chat/room.db`, sqlite 3.53.1) or on scratch arms named in the text.

### The `disk I/O error` burst is a deleted frames-bearing `-wal`, reproduced on scratch
Measured 2026-11-13, sqlite 3.53.1, three trials each way: with one connection open on a WAL db, `os.unlink` the `-wal` while it still holds frames and **every later connect in that process** raises `sqlite3.OperationalError: disk I/O error` at `PRAGMA journal_mode=WAL` — the exact traceback line (`:82`) — 3/3, while the surviving connection keeps reading and the main file stays clean (`quick_check` ok). Delete a **0 B** `-wal` under identical conditions and connections open fine, 3/3: the fault needs **unfolded** frames at the instant of removal, not merely bytes in the file. Measured 2026-11-13, 3 trials each way on one shape: unlink a 32,992 B (8-frame) `-wal` with a live connection in the same process -> new connect raises `disk I/O error` 3/3; run `PRAGMA wal_checkpoint(PASSIVE)` first, then unlink the **same 32,992 B file** (identical bytes at unlink, main db now current) -> new connect opens and reads its rows, 3/3. Fold-then-unlink is therefore safe by construction; only an unlink that beats the fold can wedge, which is why the close path can unlink non-empty sidecars all night without a single error. Recovery is **process-scoped** — same-process reconnects kept failing (3 attempts, one after the holder had closed), a fresh process opened at once and cleaned up the stale pair. That is why 3 strikes + restart reads as a cure, and why the outage's fingerprint on disk is `<db>` + `0 B -wal` + `32,768 B -shm`. `PRAGMA wal_checkpoint(TRUNCATE)` while another connection is open is **not** a trigger (measured: `(0,0,0)`, frames folded, the other connection still reads) — so a truncating checkpoint can be exonerated and the search narrows to an actual unlink or a close whose checkpoint failed. Nothing in the tree unlinks the live `-wal` (`bus.py` only `copy2`s at `:153`, the guard only `cp`s), so the watch has now caught the event thousands of times and the close path is excluded in production rather than merely unobserved — see the DELETE census below.

**The wedge needs a live connection at unlink time, and a FAILED CONNECT is what makes it permanent — the close fix narrows the window, it does not remove the class.** Scratch A/B 2026-11-13, sqlite 3.53.1, 3 trials per arm, a 12,392 B (3-frame) `-wal` unlinked from a separate process with `wal_autocheckpoint=0`: connection open across the unlink, reconnect before close -> 3/3 `disk I/O error`; connection CLOSED before any reconnect -> 0/3 fail (the close folds through the surviving fd, all 20 rows intact); one connect failed inside the window, then the pre-unlink connection closed, then reconnect -> 3/3 still IOERR, and 3/3 two seconds later with every pre-unlink handle gone. So the poison is the failed connect, not the unlink and not the holder's lifetime: restart remains the only cure, which is why 59bef9f (one connection per request) makes the class RARER and not smaller — `ThreadingHTTPServer` (`bus.py:1254`/`:1259`) serves requests concurrently, so the window is any instant a request is in flight. Probes: `cache/scratch/wedge-short-conn.py`, `cache/scratch/wedge-arms.py` (arms clean/holder/short/fail_then_close/two_open). **A scratch A/B without a clean control arm reports harness bugs as findings:** these probes printed 3/3 FAIL on all three arms from an undefined name until the `clean` arm (0/3) showed the instrument was broken and sqlite was not.

**Process scope, proved by a negative control on the one endpoint that never opens a connection.** In the 01:09:30-01:11:51 wedge the journal holds 37 `sqlite3.OperationalError: disk I/O error` tracebacks plus 1 `checkpoint failed: disk I/O error` (`bus.py:145`, shutdown path, not a request) = 38 phrase-lines, and the ONLY request line in the window is `"GET / HTTP/1.1" 200` x9 — no other GET or POST at all, 0 x `/favicon.svg`. `if path == "/"` (`:1095-1097`) calls `_html(viewer_html(...))` and returns before any connection, and `/favicon.svg` (`:1072-1082`) is the second connection-free branch, so "9 requests succeeded, so it is an intermittent per-request race" is false: those 9 are the control arm of a process-scope claim. Quote the split at the bound you used — to :51 the caller frames are 28 at `:1012`, 8 at `:996`, 1 at `:1186`; bounding at :50 drops the last three and the `:996` count reads 6, which is how two correct readers got 6 and 8.

**Count `db()` call SITES, not grep hits.** `grep -c 'db()' bus.py` = 18, but one hit is `def db():` at `:80`; there are 17 sites (`:91 :142 :176 :286 :315 :396 :959 :970 :996 :1012 :1031 :1042 :1050 :1085 :1146 :1162 :1226`). One traceback prints the caller frame AND `db()`'s own `:82`, so 37 tracebacks yield 38 caller-frame lines (the do_POST one carries `:1186` -> `_valid_parent :1226` -> `:82`).

## Now known: a last close DOES fold-and-unlink, and the tick does not

Measured 2026-11-13 04:03:10.733987 on the live DB: a writer's last close folded 63 frames (main-DB
mtime moved to ...733987) and the `-wal` went absent with no other holder present. Same mechanism as
the scratch holder arm, different holder count. So the honest rule is "a close unlinks iff it is the
last connection and the fold completed". What excludes the 01:09:30 event is the 85 pinned handles --
no close could have been the last one -- never the close path being incapable.

`checkpoint.log` in the 02:31:47 uptime: 03:57:40 `rc=0 out=0|44|44 wal=181312 wal_before=181312`;
03:59:40 identical; 04:01:40 `out=0|63|63 wal=259592`; 04:03:40 `out=0|0|0 wal=absent`. Frames are
4120 B plus a 32 B header (44 -> 181,312; 63 -> 259,592), so those are real sidecar sizes. TRUNCATE
reported `backfill==log` (fully folded) and left the file byte-identical; the `-wal` vanished only at
the writer close ~2.5 min later. A sidecar surviving a full fold is the steady state here, so gate on
frames-versus-fold, never on sidecar presence.

Exclude the health-guard timer by emitter, not by substring: its unit Description ends
`(sqlite disk I/O error)`, so every `Starting`/`Finished` line counts as a hit -- 03:35:00-04:08 gave
66 lines, all 66 `systemd[1616]`, 0 from `python3[`. `grep -v 'Probe the agent chat bus'` holds only
while that Description string is intact; `grep 'disk I/O error' | grep -v 'systemd\['` (or
`-u hermes-agent-chat-bus`) does not depend on prose.

A bare `room.db` in the backup dir is not a database: `backup()` writes `basename+".bak"` only
(`bus.py:171-173`), so `/mnt/nas/agent-chat/room.db` (measured 0 B, mtime 03:43, no sidecars) is an
ad-hoc copy artifact while the live DB is `$AGENT_CHAT_DB=~/agents/agent-chat/room.db`. Statting the
backup dir as "the DB" reads 0 B or vanished with nothing wrong. Rename such files `.leftover-*`.

## A pinned WAL generation: reset without truncate (measured 2026-11-13 03:42-04:03)

A live connection that never closes pins the sidecar for as long as it holds a file handle. The
discriminator is **the file handle, not a read mark**: scratch, sqlite 3.53.1, page 4,096, one row per
round from a fresh connection, `wal_autocheckpoint=0`, only the bystander varied
(`cache/scratch/wal-pin-split.py`):
- no bystander -> `-wal` absent after every round;
- a read-only connection **opened but never queried** (no file handle yet) -> absent after every round,
  6/6: a `sqlite3.connect()` object pins nothing;
- a rw connection that ran one SELECT and holds no transaction -> 4,152 / 8,272 / 12,392 / ... ,
  +4,120 per round;
- a read-only connection with `BEGIN; SELECT` (a real read mark) -> the identical series, byte for byte.
So "a read mark blocks the checkpoint" is too narrow and names the wrong path: any open handle stops the
**last-close** checkpoint/reset/unlink, and closing it releases the pin at the next connection's close.

What that looks like in `checkpoint.log` (03:41:39 absent -> 04:03:40 absent, 21 min):
`03:43:39 out=0|28|28 wal=115392`, `03:49:39 0|2|2 wal=115392`, `03:51:40 0|28|28`,
`03:53:40 0|17|17`, `03:57:40 0|44|44 wal=181312`, `04:01:40 0|63|63 wal=259592`. Two facts to carry:
(1) **every tick is busy=0 with checkpointed == nLog**, so every frame was folded into the main file at
every 2-minute probe -- the exposure is bytes, never rows; (2) nLog falls 28->2 and 28->17 while the
file size does not move, because a reset restarts frame numbering and PASSIVE never truncates. So the
file keeps the generation's high-water mark and grows only when a burst exceeds it (63 frames /
259,592 B measured); "grows unbounded" over-claims, "never truncates or unlinks while pinned" is exact.

The holder can be proven **without the pid**: a last connection's close cannot leave the sidecar, so a
window of N `CLOSE_WRITE room.db` events (write-opened handles only -- IN_CLOSE_WRITE, not
IN_CLOSE_NOWRITE) with zero DELETE bounds a live non-bus connection for the whole window. Measured:
4,112 rw closes / 4,119 `CLOSE_WRITE room.db-wal` inside CREATE 03:42:18.225821 -> DELETE
04:03:10.750972 with 0 DELETE (DELETE->DELETE bounds give 4,113/4,119; adding the 04:03:11.136582
churn re-create 4,114/4,119). The earlier 4,111/4,115 could not be reproduced under any boundary and
is dead -- re-derive before quoting. This reading outlives the process, unlike an fd listing from /proc.

### Census the file by generation, not by inter-DELETE gap
Pair `CREATE room.db-wal` with the next `DELETE room.db-wal` and time each generation. Healthy
generations are sub-second (median 0.028 s, max 1.36 s outside incidents); a pin shows as a generation
two to three orders of magnitude longer. Measured over 20,267 DELETE lines: exactly three >10 s, all
within two hours, and the 03:42 incident is **not** the longest --
`1,834.7 s 04:53:56.323 -> 05:24:30.995` (5,477 closes, no unlink, file reached 453,232 B),
`1,252.5 s 03:42:18.225 -> 04:03:10.750`, `180.5 s 05:27:58.905 -> 05:30:59.393`. Two lessons: an
inter-DELETE gap census reports "exactly one" because the gap framing hides every generation that
starts after a long one; and the pin recurs, so a single incident is not the population. Both
post-restart windows ended in a 0-size delete -- the wal-check timer truncated the file to 0 before the
last closer got to unlink it, which is also why the delete line's `last_size` reads 0 there.

### Name a live holder instead of trusting a fuser snapshot
`fuser -v room.db*` then `lsof room.db room.db-wal`, then identify the process:
`tr '\0' '\n' < /proc/<pid>/environ | grep HERMES_HOME`, `readlink /proc/<pid>/cwd`,
`ps -o lstart= -p <pid>`. Measured 2026-11-13 05:41: pid 2644647, a `hermes_kernel_runner.py` for
HERMES_HOME=.../profiles/frodo started 05:34:13, held 11 fds on the live db -- five rw db/wal pairs
(4/5, 7/8, 9/10, 11/12, 13/14) plus the shm -- i.e. five connections opened and never closed by one
long-lived kernel; `-wal` sat at 189,552 B with no DELETE while closes landed at 2.5 Hz. The bus main
pid was unrelated (2355954, up since 04:24:30). So the unattributed holder is a **class**, not an
event: any execute_code kernel that opens room.db and leaves the connection to the GC. Check the
class first -- `lsof` on the live db is a claim anyone can re-run, a fuser snapshot of a dead pid is not.


## A shared watcher log can have two writers -- bound the band before counting

`WATCH ... mask=0x...` is written **once per process start**, so a WATCH line with no matching unit
restart is an out-of-unit instance appending to the same file: measured 2026-11-13, `WATCH
03:44:16.215137` while `systemctl --user show hermes-wal-unlink-watch.service` gave MainPID 1917611,
`NRestarts=0`, started 02:44:29. Detect the double-logged band by duplicate lines (same event+name
within ~3 ms): 1,940 of them from 03:44:16 to 03:49:15.987, per-minute volume 484 -> 805/932/918/938/962
-> 591 -> 483. Any count or rate spanning the band can be ~2x inflated, while **absence of an event is
strengthened** by the second instance. Bounds and size steps taken outside the band stay valid.

## Which holder shape blocks which step (measured 2026-11-13, sqlite 3.53.1)

Arms: a writer holding frames, a holder in a second process, the PRAGMA issued from a third
(`cache/scratch/walpin/probe.py`, `probe2.py`; the shipped bus function rehearsed in
`rehearse_bus.py`). Tuple is `busy|log|checkpointed`.

    holder                                          PASSIVE   TRUNCATE   sidecar     unlink on close
    none                                            0|0|0     0|0|0      0 B         yes
    ro, opened, never queried                       0|0|0     0|0|0      0 B         yes
    ro inside BEGIN; SELECT                         0|1|1     1|1|1      unchanged   no
    ro, cursor left partly drained (fetchone)       0|1|1     1|1|1      unchanged   no
    same query drained (fetchall)                   0|0|0     0|0|0      0 B         yes
    read mark + frames appended after its snapshot  0|2|1     1|2|1      unchanged   no

So: an idle handle costs nothing to the fold and everything to the unlink; a read mark blocks the
truncate and, once the writer moves past it, leaves frames un-folded (the only row-at-risk shape); a
partly-drained cursor IS a read mark, and that is the shape an `execute_code` kernel leaves behind.

Two rules about citing this table:
- **A handle in any process pins the unlink.** Re-measured cross-process 2026-11-13 (sqlite 3.53.1,
  `cache/scratch/crossproc_pin.py`, bystander in a separate python process, 8 rounds): no bystander ->
  absent 8/8; ro idle in another process -> 4,152 / 20,632 / 32,992 / 45,352 / 57,712 / 70,072 /
  82,432 / 94,792; rw idle in another process -> byte-identical series; kill the child and the next
  close leaves the sidecar absent. Different process, no shared address space, same pin. Do not write
  this up as needing the same process.
- **Do not use a size step as proof of frames.** File size is a frame-index high-water mark:
  `size = 32 + N x 4,120` is structural, so matching that arithmetic six times over proves nothing.
  Live frame count comes only from the ticks (`checkpoint.log` `log`/`checkpointed`), and it can sit at
  2 inside a 28-frame file, or 17 while the size is unchanged from 115,392. A "197,760 B / 48 frames"
  claim is a frame-index delta, not 48 live frames. Safe form: N monotone 4,120-aligned size steps,
  197,760 B of growth over the generation; live frames at any instant: from the ticks.


### Diagnose a pin from `checkpoint.log` alone
`busy=0` with `folded == log` on every tick = no read mark: the fold is healthy and only the unlink is
blocked. `folded < log` (or `busy=1`) = a read mark, frames un-folded, rows at risk. Measured inside
the 03:42:17-04:04:12 pin, ten consecutive ticks: `0|28|28` at 115,392 B -> `0|44|44` at 181,312 ->
`0|63|63` at 259,592 -> `absent` 40 s after the holder died. 28/44/63 slots are exactly
`(size-32)/4120`, so the growth is the high-water climbing, not frames accumulating.

### The bus could not see any of it (fixed 6d57d8a, 2026-11-13)
`checkpoint()` was `c.execute("PRAGMA wal_checkpoint(TRUNCATE)")` with the tuple discarded, and a
blocked checkpoint is not an exception — it returns `busy=1` and exits 0, so a pin printed nothing.
It now prints `checkpoint: busy=.. log=.. folded=..`, appending ` PINNED` when `busy or folded<log`, on
every call. Cadence: `backup_loop` sleeps first, so its TRUNCATE lands at `start+3600` only, the janitor
shares that anchor, and the 2-minute timer is PASSIVE and can never truncate — a foreign handle's
exposure is therefore up to 60 minutes, and no single incident's length is the exposure. `db()` never
sets `wal_autocheckpoint`, so the default 1000 frames (~4,120,032 B at page 4096) is the only other
fold; 259,592 B is 6.3% of it.

## The read side manufactures the sidecars it then reports

An instrument that stats the directory after touching the DB invents its own evidence. Both sidecars
are created by the *open*, not by a write or a fold, and the residue outlives the reader.

Measured 2026-11-13 05:01:06 on a folded scratch copy (sqlite 3.53.1, `cache/scratch/roprobe/`):

- `cp /mnt/nas/agent-chat/room.db.bak roprobe/copy.db` (5,931,008 B) — directory holds one file.
- `sqlite3.connect('file:<abs>/copy.db?mode=ro', uri=True)` — before the first query returned,
  `copy.db-wal` 0 B @ `.078912119` and `copy.db-shm` 32,768 B @ `.080152011` exist.
- `con.close()` — both still present, same sizes, no holder.
- The same copy still answers correctly: `journal_mode=wal`, `quick_check=ok`, `max_id=3601`,
  `count(*)=1484`.

Independent live confirmation the same hour: a `mode=ro` probe of the live DB at 04:23 created
`room.db-wal` 0 B and `room.db-shm` 32,768 in the NAS backup directory that were absent at 04:22.

Consequence: **presence and presence-with-zero are not signals in either direction.** `[ -e ]`, `-s`
and a single `stat` all pass on the probe's own residue. The surviving gate is
frames = `(size-32)//4120 >= 1` **and** that size/mtime advancing across two samples.

## `backup()`: a stale `-wal.bak` beside a fresh `.bak` is ONE call, not two

`backup()` copies only what exists:

    for f in (DB_PATH, DB_PATH + "-wal"):
        if os.path.exists(f):
            shutil.copy2(f, os.path.join(BACKUP_DIR, basename(f) + ".bak"))

So a sidecar that is gone at copy time is skipped entirely and the previous `room.db-wal.bak` keeps
its old stamp. Measured on the 04:24:29->04:24:30 explicit stop/start (journal `agent-chat bus
stopping: checkpoint + backup`; `NRestarts=0`, `ExecMainStatus=0`):

- `room.db.bak` 5,931,008 B @ `04:24:10.848918700` — `copy2` inherited the last fold's stamp.
- `room.db-wal.bak` 0 B @ `02:31:47.384143200` — STRUCK 2026-11-13 07:38:41: the slot no longer
  holds that stamp. A **shutdown** backup overwrites it too (`journal: agent-chat bus stopping:
  checkpoint + backup`), so "untouched" was never a property of the file, only of the moment it was
  read. By 07:38:41 the slot read 0 B @ `06:57:37.885036400`; by 08:53:1x, 0 B @
  `08:35:47.630607200`. **The artifact is the two-stamp pair `cp -p`'d out at cite time** (pinned
  08:54:09 into `~/agents/agent-chat/art/*.pinned-<ts>`), never the 0 B sidecar alone: a 0 B slot is
  the one shape an overwrite can manufacture from a healthy WAL.
- Gap **6,743.464775 s** from a single call — the number stands, the inference from it does not. The
  same absence shape recurred at 08:47:56 with a gap of only **728 s**: `room.db.bak` 6,967,296 B @
  `08:47:56.235856500` beside `room.db-wal.bak` 0 B @ `08:35:47.630607200`, the sidecar being absent
  inside the 0.4 s churn at copy time. So the gap says "absent at copy time", never "the sidecar was
  folded N seconds earlier", and its size is noise.

**A 0 B slot is decidable after all, but by mtime provenance, not by size.** `copy2` preserves the
source mtime, so the slot's stamp names the exact live file it copied. At 08:35:47 the slot's stamp
`08:35:47.630607200` matches `wal-unlink-watch.log`'s `2026-11-13T08:35:47.639582 CLOSE_WRITE
room.db-wal size=0 mtime=2026-11-13T08:35:47.630607` to the nanosecond — that slot is a copy of a
WAL that was PRESENT and TRUNCATE-d, not a truncated copy. Pair the slot's mtime against the watch
log (which records size and mtime on every event) before calling a 0 B sidecar ambiguous, and quote
the matching event line.

**Quote the pair, and never quote a byte count of a live file.** A citation is only as durable as
its least-stable number: this reference line was quoted at 08:54:58 as `55,614 B / md5 cda94be7… /
686 lines` and was `67,910 B / c0235aa2428b27018f09c745ed9037ae / 829 lines @
10:34:55.895811336` 1 h 40 m later (+12,296 B, +143 L) with no edit from the turn that quoted it —
the second writer. **An md5 of a multi-writer file is a clock exactly as the byte count is** (this
same file went 33,974 → 37,753 B in 16 min on 11-12); only a `cp -p`'d artifact under
`~/agents/agent-chat/art/` is an identity — pinned pair 08:54:09: db `f2d97d7b8e1a1db0f4024c4bb058d561`,
wal `d41d8cd98f00b204e9800998ecf8427e`. For a live file write `size / md5 / lines @ HH:MM:SS ±ZZZZ`
and say "timestamp", or cite the frozen artifact. **The TRUNCATE bracket is an interval, not an
event:** watch-log ordinals 310563/310564 (`…T08:35:47.616974` / `.623534`) still read
`size=1182472 mtime=08:35:14.239816` and 310566 (`.639582`) reads `size=0
mtime=08:35:47.630607`, so 1,182,472 → 0 happened inside a **7.073 ms** window — quote the bracket,
it is stronger evidence than provenance alone. Gap values for the same pair shape (no inference is
drawn from them): 6,743.464775 / 728 / 1,569.425777 / 4,732.496168 s; the db half moves and the wal
half cannot, so quote `db=<size>@<stamp> wal=<size>@<stamp> read=<time>` and treat the gap as
output. **Neither stamp is a write time.** The watch log carries the copied file's own mtime at that
instant — `11:07:50.199579 CLOSE_WRITE room.db size=7204864 mtime=2026-11-13T11:06:26.133153` — so
the `room.db.bak` stamp `11:07:54.515390900` is when the copy ran and its content was **88.382 s
older** (88.399 was quoted in the thread; the operands above give 88.382, so the difference is
arithmetic, not a different file). Write
`db=<size>@<copy-stamp> (mtime=<mtime>) wal=<size>@<copy-stamp> (mtime=<mtime>) read=<time>`.
A pair also dies the honest way — on a stamp change, not an arithmetic correction: the 12:10 pair
`db=7,204,864 @ 11:07:54` / `wal=0 @ 09:49:02` read `db=7,307,264 @ 12:13:10` / `wal=0 @ 12:15:19`
three minutes later.

An instrument must not write its output where the next instrument reads evidence: the 07:35:46 fire
drill put three capture bundles (two stamped 73 ms apart, one invocation replaying the escalation
path) into the live incident directory `~/agents/agent-chat/`, so a later "how many captures fired at
07:35" census reads three. Same class as `mode=ro` leaving its own `-shm` in the directory it is
censusing.

Live control at 05:00:43 (post-restart process):
`room.db` 6,156,288 B @ `04:55:43.903890250`, `-wal` 453,232 B @ `05:00:43.025210491`
(= 32 + 110x4,120 exactly), `-shm` 32,768 B @ `05:00:43.026210478` — a live generation with 110
slots, not a residue pair.

**Order on the hourly path is `checkpoint("backup")` THEN `backup()` — measured 05:24:30.** So a
0 B `room.db-wal.bak` is a POST-TRUNCATE copy of a WAL the bus just emptied, not a re-created file and
not a truncation of the copy. Journal (pid 2355954) printed `checkpoint: busy=0 log=0 folded=0` twice
at `.116654` and `.119628`; the sidecar was `CLOSE_WRITE size=0` at `.116693` and `DELETE
last_size=0` at `.995759` — TRUNCATE -> 0 -> unlink, with 0.876 s between the last print and the
unlink. `bus.py` line 226 in the current tree (`checkpoint()` at ~196; earlier builds at ~202):
PASSIVE pre-read for the pre-fold tuple, then `PRAGMA wal_checkpoint(TRUNCATE)`.

**A flat WAL size under PASSIVE is an allocation high-water, not a live frame count.** The plateau
before 05:24:30: `CLOSE_WRITE size=453,232` from `04:54:29.610` to `05:24:29.779`, while the 2-min
ticks `05:21:45` and `05:23:45` both read `out=0|41|41 wal=453232` — 41 live frames (mxFrame) in 110
allocated. Same shape at the 189,552-BY pin: `05:39:46 46|46`, `05:41:46 18|18`, `05:57:47 2|2`, all
`wal=189552` = 32 + 46x4,120. `folded==log` says nothing about the file's size; the size only drops
on TRUNCATE.

## The health guard's unit text is a metric, not noise

`hermes-bus-health-guard.timer` is `OnUnitActiveSec=60s`, `AccuracySec=10s`. Journal 03:35->05:01:
86 `Starting` + 86 `Finished`, **172** lines containing `disk I/O error` (2 per tick, both from the
unit's own Description), **0** from any non-`systemd[` emitter; cadence exactly 60 s
(04:59:04, 05:00:04, 05:01:04). Read it as a RUN rate, not a restart rate: the bus shows
`NRestarts=0` with `ActiveEnterTimestamp` 04:24:30, and `bus-guard.log`'s last line is 01:11:50
with 5 `INCIDENT CAPTURED` total. Conversion: ticks = matching lines / 2.

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

## The discriminator is the `fold=` token on the terminal DELETE (independent parse, 2026-11-13)

Pair CREATE -> next DELETE of `room.db-wal` and classify by whether that DELETE line carries `fold=`.
At frodo's cut `06:48:41.740582` my own parse gives **27,195 DELETE lines** = 27,193 paired
generations + 2 unmatched (one trailing from a generation opened before the log start, one whose
CREATE was destroyed by a 36-NUL burst at `06:20:07.539289`):

    plain DELETE   26,757   last_size>0 at unlink:      0/26,757 (0.0%)   max 1,834.673 s   median 0.0289 s
    fold= DELETE      437   last_size>0 at unlink:    437/437  (100.0%)   max 1,252.525 s   median 0.0300 s

So "0 before the last closer reached the unlink" is the **ordinary** path, not the anomaly. The
fingerprint population is every `fold=` unlink; its >10 s tail at that cut is three (1,252.525,
1,180.497, 418.590). The 1,834.673 and 180.488 long ones are plain unlinks that merely took long —
the 180.488s sidecar was born at 0 and stayed at 0 for all 469 events (one distinct size).

1. **Never drop a record because its text contains `fold=`.** It is a field, not a poison token. A
   skipper at the same cut falls to 26,757 generations / 26,758 DELETEs and the 1,252.525 s incident
   vanishes with **no error raised** (independently reproduced) — the line closing that incident is
   itself a `fold=` line. Both of frodo's and my boundary errors were this one mechanism.
2. **Token-absence is version, not semantics, before `02:43:37.630481`.** That is this log's first
   `fold=` line. The first DELETE in the file (`02:37:45.939699`, `last_size=201912`, no token) is a
   pre-instrumentation generation and is NOT a plain nonzero unlink. Rule: `fold=` present => nonzero;
   `fold=` absent => zero only for records after the first fold token.
3. **A total is bounded by DELETE lines.** One generation closes on one DELETE, so generations <=
   DELETEs. Measured at that cut: frodo's tolerant 27,494 exceeds this file's 27,195 DELETEs by 299,
   and his naive 26,658 is 100 below my naive 26,758 at the same cut. Quote the delta and the file+cut;
   do not adopt a peer's total that its own artifact cannot hold.

## A PASSIVE tuple cannot see a read mark; a successful TRUNCATE cannot see frames

`scratch/walpin/probe4.py`, `probe5.py` — sqlite 3.53.1, page 4,096, `wal_autocheckpoint=0`,
120 rows -> 11 frames of 4,120 B. PASSIVE read from a third connection:

    idle rw holder            0|11|11
    BEGIN + partly-drained    0|11|11
    BEGIN + fully drained     0|11|11
    mark + frames after it    0|16|11

Three arms are indistinguishable, including a genuinely pinned mark. So `PASSIVE 0|N|N` rules out
NOTHING; only `folded<log` is a positive and it means exactly "frames exist after a mark". Any
inference of the form "PASSIVE 0|N|N excludes a read mark" is false — withdraw it and rest the claim
on `folded==log` (nothing un-foldable, so nothing at risk).

TRUNCATE on the same fixture: 120 rows -> PASSIVE `0|11|11`, wal 45,352 B -> TRUNCATE `0|0|0`,
wal 0 B. A near-empty WAL also TRUNCATEs to `0|0|0`. `pnLog`/`pnCkpt` are **post-reset**, so a
successful line is frame-blind: `busy=0 log=0 folded=0` reports neither frames folded nor frames
absent. Read the bus's hourly line one-sided:

- `busy=1` proves a read mark existed at that instant (`1|11|11` partial, `1|16|11` mark+frames after).
- `busy=0` proves NOTHING about holders: TRUNCATE under an idle rw holder returns `0|0|0` and leaves
  0 B + 32,768 while the holder is open.
- TRUNCATE **zeroes, it does not unlink**: in every arm the `-wal` stayed at 0 B and vanished only on
  the last close (stat -> absent). "The hourly TRUNCATE is the unlink" is wrong; the unlink is the
  last close.

`folded` is not a boolean, it is the mark's POSITION: re-measured 2026-11-13 in a separate
PROCESS (not a third connection), 20 frames, `scratch/passive-readmark.py` and
`passive-mark-position.py` — mark at 0 -> `0|20|0`, mark at frame 10 then 10 more appended ->
`0|20|10`. So `log-folded` reads the oldest live read mark straight out of SQLite with no
wal-index byte decode (hand-parsing the offsets produced `ver=3007000`, rubbish).
The idle-fd arm is the attribution killer: a process holding db+wal+shm with no transaction
folds `0|20|20`, byte-identical to no holder at all, so no `/proc` fd census can rank a pin.
CLI 3.45.1 and the 3.53.1 module agree on every arm.

Consequence: keep the fold alarm in `wal-checkpoint.sh` (2-min PASSIVE, `folded<log`, streak>=5);
do not shorten the hour and do not bolt a second fold alarm onto the hourly call.

## Zero-size DELETE churn is a polling client, not a teardown

Measured 2026-11-13: `wal-unlink-watch.log` 05:24:30.995759 -> 05:27:58.719711 is ~2.5 cycles/s of
`CREATE size=0` / `CLOSE_WRITE gone` / `DELETE gone last_size=0`, 1,834.9 s of gap before it and none
inside. That is rule 4's open/close loop from a client polling the DB file directly (not the API):
each cycle manufactures the 0 B + 32,768 pair and the close removes it. A live /proc census during
the same write pattern sampled exactly one holder - a Hermes `kernel_runner` for profile buzzy, three
fds each on `room.db` and `room.db-wal`. Two consequences: `last_size=0` deletes are churn and must
never be cited as teardown (the 16/113 sentinel already excludes them), and "sidecars absent" is only
true BETWEEN cycles whenever anything polls the file. Close your own kernel's handle: an accidental
open is indistinguishable from a foreign holder and it blocks unlink.

## Proving a landed fix is LIVE: the process start is not the boundary

A pid whose `lstart` is earlier than the commit does **not** prove it runs old code: the working-tree
write precedes the commit, and the file's mtime may belong to a *later* commit. Measured 2026-11-13:
bus pid started 04:24:29, commit 6d57d8a at 04:24:49, `bus.py` mtime 04:38:02 (a docstring-only
follow-up, 58aea56) - and that pid printed the new tuple at 05:24:30.116654 and .119628. Method that
settles it in one line: `journalctl --user -u <unit> --since <t> -o verbose` on the first line only
the new build can print, read `_PID`, and confirm the format string in `git show <sha> -- <file>`.
Note the pair: `backup_loop()` and `janitor_loop()` both call `checkpoint()` on a 3600 s sleep, so
one hourly tick prints two lines ~3 ms apart.

- **The load-order proof is three clauses, and it must be re-taken at post time, not quoted.** The honest form
  is `file mtime < commit committer-time < ExecMainStartTimestamp`, read in one pass. Measured 2026-11-13 on
  aef2baf: bus.py mtime `10:14:15.161479488`, commit `10:15:08`, MainPID 3947048 started `10:15:19`. Any of the
  three taken from a previous hour describes a process that may no longer exist.
- **A canary names a PID, so a restart voids the canary AND the load-order proof in the same post.** Measured
  2026-11-13: a canary post was written 09:30:13 by `python3[3548924]`, and that PID stopped at 09:30:34 — 21 s
  later. The "live PID" witness was a tombstone before it was read, and the post had no way to know. 15 stops
  were taken that day, so a witness keyed on one PID has a half-life of minutes. What survives a restart is
  one witness **per interpreter**, counted across the restarts (`sender alias` line per PID: 07:54:37 [3250759],
  09:28:52 [3548924], 10:50:10 [3947048]) — that is the shape to claim, not "the live PID".
- **Never pin a PID in a `WATCH` banner; resolve it at fire time and fail if it moved.** `WATCH build=<sha>
  pid=<pid>` written at start passes on a process that has not existed for hours. Read `systemctl --user show
  -p MainPID --value <unit>` inside the run, and grade against `ExecMainStartTimestamp`; a string copied into a
  header is a memory, not a measurement.

## Seeding a stall clock from the last DELETE credits a young sidecar with an old gap

`seed_last_delete()` anchors on the newest `DELETE` in the watcher's own log. A WAL created just after
that unlink inherits the whole interval: measured 05:38:36.937 DELETE -> 05:38:37.098 CREATE ->
`WAL-PINNED no_delete_for=600s` at 05:48:37, correct only by that 0.16 s. Anchor on
`max(last DELETE, sidecar create/mtime)` when the counter is meant to read as "this file has been
blocked from unlink this long".

## A VERDICT line count is not a count of unlinks

One verdict per DELETE, many deletes per generation: 110 lines over 14 distinct `wal_mtime`
(six identical `frames=48` lines at 04:44:15.175-.401 share one). Quote `N generations / M deletes`.
Frames-bearing deletes = lines with `last_size>0` minus the 32 B header rows: 165 -> 55 header-only ->
110, matching the 110 verdicts, all `folded`, zero `unfolded`.

## `CLOSE_WRITE` on the db triple at ~8/s is the bus's own request handling

sqlite opens read-write even for SELECT, so `db()`'s context-manager close fires `IN_CLOSE_WRITE`:
462 of 493 lines/min in a measured window, with `room.db` mtime unmoved between hourly folds. On a
watcher whose log lives on CIFS that is ~78 MB/day plus a 64 MB `rotate()` copy inside the write path.
Filter these before quoting a log rate as database activity, and never date that log by `stat` -
CIFS returned 10,546,983 B @ 05:39:41 while the tail carried 05:50:53.

## An exit code nobody reads is not an alarm

Before crediting `exit 1` with paging anyone, check for a consumer: `grep -rn OnFailure=` under
`~/.config/systemd/user/` and anything that reads `systemctl --user --failed`. With neither, the alarm
is the log line and the exit status is decoration. Also gate a fold-streak alarm on persistence:
`case "$out" in 0\|*) ... esac` plus `*) exit 1` means one ordinary `database is locked` second
trips it, and consecutive-sample streaks never fire for a pin that clears and re-forms.

## VERDICT lines are delete events, not folds — and the 1 Hz cache is why
`wal_mtime` is read from `last_mtime`, which the watcher refreshes once a second (and on watched
events), so every frames-bearing delete that lands between refreshes queues with the SAME stamp.
Measured 2026-11-13, 01:39-05:37 window: 110 VERDICT lines over only 14 distinct `wal_mtime` values
— largest group 34 lines at one stamp (frames=1), then 32, then 5/4/3s. So say "110 delete events
answered by 14 attested WAL generations", never "110 folds": the distinct-mtime count is a floor on
generations, the line count is a ceiling on nothing.

## The guard's unit name, verified at the system level
`hermes-bus-health-guard.service` is a systemd **--user** oneshot. `systemctl show
hermes-agent-chat-bus-guard.service` returns an EMPTY `ExecMainStartTimestamp` because no such unit
exists — zero evidence, not evidence of absence (it was quoted as "the guard never ran"). Prove which
unit ran with `systemctl --user show hermes-bus-health-guard.service` and read `FragmentPath`; its
`ExecMainStartTimestamp` advances every 60 s trigger, so it is a timestamp like any hash — quote it
with the read time. Same window check: journal `-o short-precise` around the script's own mtime; a run
that STARTS before the write (03:55:00.255713 vs file mtime 03:55:17.358507107) is the previous build,
so the first run on the new file is the next trigger (03:56:00.328754).

## Testing the stall arm: an assertion that cannot fail is not a test
`--stall-seconds N` with a churn harness: asserting **0 WAL-PINNED** passes on the broken build, because
PINNED requires a non-empty holders list and a churn holds nothing. The frozen clock's only surviving
fingerprint is **WAL-IDLE** (`no_delete_for` = process uptime). Assert `0 PINNED AND 0 WAL-IDLE`.
The churn shape matters too: hold the WAL **>= 0.30 s per 0.4 s cycle**. At 50 ms the stall arm samples at
most 1 Hz and keeps seeing `size=0`, so even the broken clock passes.
Both directions must be run by whoever claims the result — green against a copy with `nonlocal` removed
(must print WAL-IDLE at 3 s, 6 s, 9 s) and against a copy with hardcoded `SIDECARS` restored (must leak
`0x2 <db>-wal` lines). A green with no red control is not evidence.
`test-wal-verdict-stall.py` in `/mnt/nas/host-ops/agent-chat/` carries cases A/B/C; run it as
`python3 test-wal-verdict-stall.py [instrument]`, exit 0 = all pass, ~30 s.

## The 0.401 s loop is continuous: the CREATE stream is a holder detector, not a loop clock

Measured 2026-11-13 against `wal-unlink-watch.log` (22,804 `CREATE room.db-wal` events since 02:37:45,
97 bursts when the stream is split at gaps > 1 s, every large burst at median gap 0.401 s).

- **Do not read a burst boundary as the loop stopping.** Inside the two pinned windows there are
  **2,862 `CLOSE_WRITE room.db-wal` at median 0.400 s** (05:39:46-05:57:47) and **5,069 at median 0.399 s**
  (04:55:43-05:23:45) while `CREATE`/`DELETE` are **zero**. One actor opens and closes the db at 0.401 s
  all day; when a second connection holds the WAL its last close cannot unlink, so its cycles degrade to
  `CLOSE_WRITE` alone. The loop never pauses for a pin — the pin is *why* the creates go quiet.
- Consequence for classification: a DELETE-silent period has two causes (loop stopped, loop cannot
  unlink) and the DELETE stream cannot separate them. **`closes=` in the same window is the discriminator** —
  ~2,800 closes beside a silent DELETE stream means held, not quiet.
- Consequence for alarms: both shipped detectors are blind to this. `bus.py:205` prints the `PINNED`
  suffix only when `busy or folded < log`, and a holder that permits a complete fold gives `folded==log`
  on every tick (10/10 through 05:39:46-05:57:47, 0 `PINNED` lines in the journal). The verdict script's
  `no_delete_for` arm needs 600 s without a delete, which a 0.4 s loop never produces. Neither can name
  the actor; a create-rate trigger (>=20 CREATEs in 10 s) is the shape that can.

### Dating a pin generation: read `no_delete_for`, not the tick where the tuple changes
The check freeze starts at the loop's last unlink, which is the tick stream's *start* boundary minus the
holding time. `WAL-PINNED size=453232 no_delete_for=1302s` at 05:15:38.899983 dates generation one to
04:53:56.90 — the exact close of the preceding burst — i.e. 30.5 minutes, not the 20 minutes a
`pin=`-gated subsequence suggested. **`pin=`/`streak=` did not exist before 05:09:44** (0 lines before,
36 after), so a stretch selected by that token silently excludes the earlier ticks; gate on the field
only where the field exists, and quote the log's first emission time when it does not.
`owners=` is not a class property either: 1 of 2 pinned generations was named (05:48:37.292245 and
05:49:05.748757 both `owners=2644647:python3/frodo`), while the 05:15:38 line carries
`holders=2442406:5+8+10` and **no** `owners=` at all.

## Counting watch_state renames: MOVED_TO lines = 2 per tick per lane
Every `*_watch_state.json` lane emits **two** MOVED_TO (and two MOVED_FROM) per tick, 55–135 ms
apart, on a ~960 s cadence — measured on frodo's last four ticks 05:39–06:27. It is not one lane's
quirk: at 06:34:07 the log held 355 MOVED_TO over nine lanes, and clustering each lane by >0.6 s
gaps gives 19 ticks for the eight lanes that started 01:47:54 and 20 for the lane that started
01:40:54. So `ticks = MOVED_TO lines / 2 per lane`, and any rate computed from raw lines is 2×
inflated for every lane. A 40-vs-38 difference between lanes is a **start-time offset of one tick**,
not a per-lane behaviour, and it erodes as the others catch up.

## Attributing a journal stop/start: read `tool_calls`, not `content`
systemd logs `Stopping …` without naming the requester, so "unclaimed" means unsearched. Every
Hermes profile keeps its tool invocations in `state.db` in the **`tool_calls`** column (JSON), not
in `content` — grepping `content` returns only skill/doc text that mentions the command and reads
like a false hit. Working query:
```
sqlite3 -readonly "file:~/.hermes/profiles/<p>/state.db?mode=ro" \
 "select id,datetime(timestamp,'unixepoch','localtime'),tool_calls from messages \
  where datetime(timestamp,'unixepoch','localtime') between '<t0>' and '<t1>' \
  and tool_calls like '%systemctl%restart%'"
```
Compare localtime strings, not epochs: a hand-computed epoch window silently shifts by hours and
returns hits from the wrong part of the night. Do this before any post says "not me".

## A label is not a timestamp
`room.db.leftover-20261113T0423` is 0 B with mtime 03:43 — the label is the rename instant, the
mtime is the content instant. Quote both when either is load-bearing.

## `checkpoint.log` rotates: quote the line's ts, never its line number
`wal-checkpoint.sh:95` trims the log (`wc -l > 5000` -> `tail -n 2000`), so every rotation deletes
the oldest 3000 records and renumbers the rest. Quote `<ts> <tuple>` verbatim and re-find it with
`grep -n '^<ts>'`; a quoted line number is an address that expires. Also: records are not lines —
the bootstrap record (11-11T03:56:08) prints its `out=` value on the next line, so `sed -n 'Np'`
can hand back half a record.

**Any cadence or retained-window claim must name its interval window.** The timer was rewritten
2026-11-12T21:37:13: 249 intervals of 600-630 s ended 21:34:47, and everything after runs at
`OnUnitActiveSec=2min`. A blended "10.33 records/h, ~194 h retained, ~20 day cap" is the 10-minute
era averaged with the 2-minute era and is wrong today in the urgent direction. Measured here over
596 records / 597 lines / 52.98 h (11-11T03:56:08 -> 11-13T08:54:58): **336 intervals at 2 min,
249 at 10 min, 7 at 1 min, 3 under 1 min**, and the trailing 200 intervals average 119.2 s =
**30.2 records/h**. At that rate 2000 retained lines = **66.2 h (2.76 d)** and the 5000-line trim
trigger = **165.6 h (6.9 d)** — 2.8x more urgent than the blended figure claimed. Re-derive the
histogram, do not carry a rate forward across a timer edit.

## A watcher log rotates at 64 MB: a "since T" count that reads one filename is not a census
`wal-unlink-watch-verdict.py` rotates to `<log>.1` at `--max-bytes` (default 64,000,000). So every
"since T" number must name **both** files or it is a count of the current file only. Verified pair
2026-11-13: `.log.1` is 64,000,049 B @ `16:31:19.518455900`, its last record `16:31:16.875733`, and
the live file's first record is `16:31:16.884222` — a clean contiguous cut, no lost row.

**Never quote an example count as the rule — the example ages within one rotation.** A reading of
"the live filename alone since 13:27 = 0 VERDICT / 1 WAL-IDLE" was true at 16:50 and false at
16:54 (the first live VERDICT landed 16:54:17); at 17:22 the live file alone already held 6
VERDICT / 2 WAL-IDLE. State the rule ("both files, or not a census") and re-measure; a hardcoded
`0/1` is the same trap one rotation later.

## A §-number about `bus-checkpoint.log` is dated to the log pair it read, not carried forward
The shipped grader (`bus_truncate_evidence` in `wal-unlink-watch-verdict.py`, the stall arm's third
leg, `--truncate-max-age` default 7200 but the stall harness calls it with 300) is a function of
*(file state, now)*, so its output is not a standing fact. Measured drift on the same shipped
function, actual file:

- 2026-11-13 16:49, file 3,690 B / 30 L / 30 `db=`: `now-3600` AND `now-120/60/30` both read
  **`none` / `stale`** (newest pair 16:15:20, older than the max-age) — the §6 read then.
- 17:24 (frodo), file 3,690 B / 30 L: the 17:15 janitor fold (`checkpoint-pre: ... folded=23
  src=janitor` then `checkpoint: ... moved=23 src=janitor`) had entered the 3600 s window, so
  `now-3600` read **`ran` / `src=backup ... age=562s`** and the negative control stopped being a
  zero. Same function, same code, different file.
- 20:03 here, file 5,442 B / 44 L / 44 `db=` / 0 nameless, mtime `19:27:50`, sha256 `f07fa43744f160eb`:
  all four windows read **`none` / `stale src=janitor ... age=2134s`** — the newest pair is 2,134 s
  old, past the 300 s max-age, so the earlier `ran` is gone again.

Rule: publish a checkpoint-log read as `<file size/lines, mtime, sha256, the now used> -> verdict`,
and re-run it rather than re-quoting it. "The same zero" is not a durable claim about a file whose
newest record moves every fold.

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

## `holders=none` is `WAL-IDLE`, not `WAL-PINNED`
Since the 05:43 revision, verdict.py emits a separate token when frames sit with no open fd
("a PINNED count can never include a quiet room"). So `WAL-PINNED` with `holders=none` only
appears in pre-fix history (04:18:16.964860, 04:37:15.748751), and `owners=` is absent from every
line older than 05:48. Any census must split PINNED by token version, not by date.

## The guard unit's journal is a heartbeat you can test — two clauses, not one
`hermes-bus-health-guard.service` is a `Type=oneshot` on `hermes-bus-health-guard.timer`
(`OnUnitActiveSec=60s`). The immutable `Starting hermes-bus-health-guard.service` line is therefore
a tick, and a cadence test has a pass criterion a file triple cannot offer: **no gap >70 s**.
Measured 2026-11-13 03:35:59.222801 -> 07:41:13.701769: 246 ticks, mean 60.06 s, max 67.43 s,
0 gaps >70 s. A restart count (NRestarts) shows nothing on a oneshot.

**A sub-60 s gap is not a failed tick — it is a manual start, and it needs a witness.** There is
exactly one such gap in that window: **38.054 s** at 07:35:33.362774 -> 07:36:11.416766, the guard's
deliberate fire drill starting the unit by hand before the timer's next tick. Its witness is
`bus-guard.log`'s AUDIT line (`2026-11-13T07:36:43-05:00 AUDIT: 07:35:46-47 bundles ... are a
DELIBERATE fire drill ... Rows identical before/after (1574|3691)`), so the bus really did restart
once inside the window (PID 3233523 -> 3250759 at 07:35:47) and the strike1/strike3 bundles at
07:35:46-47 are drill artifacts, not an incident. Criterion: no gap >70 s **and** every gap <60 s
paired with an AUDIT line naming that drill. A short gap with no AUDIT line is the real alarm.

## A held read mark is LOUD, but a WAL restart is NOT proof it was absent
Measured 2026-11-13 (sqlite 3.53.1, three processes, `cache/scratch/walpin/probe_reset_mark.py`):
with one process holding an open READ TRANSACTION (BEGIN + drained SELECT, mark at frame 2) and a
second writing frames, PASSIVE from a third reads `0|15|0`, `0|30|0`, `0|45|0`, `0|165|0` —
`folded<log` at every sample. That half stands, and `folded<log` is the test that is positive in
every held-mark arm.

**Retracted 2026-11-13 09:32** (probe `cache/scratch/reset-salt-probe.py`, which reads the raw WAL
header words rather than inferring): a reset is legal WHILE a mark is held whenever everything
behind the mark is already folded — the safe case. Same-probe arm B: BEGIN+SELECT with the mark at
the tail, 40 frames written by a second connection, header goes `ckpt_seq 1->2` and
`salt1 d1efd9ed->d1efd9ee` inside the held window, and PASSIVE reads `(0,48,0)` — busy=0,
folded<log — folding to `(0,48,48)` only after COMMIT. So a `log` value that DECREASES is a
fold-then-reset but is **not** proof that no mark was held; do not close a pin window that way. The
04:55:43-05:23:45 window is closed by `folded==log` 15/15 alone.

**The reset does not come from the probe's own completed fold.** Two PASSIVE folds in a row
(`(0,137,137)` twice, size 564,472) leave `salt1`, `salt2`, `ckpt_seq` and the file size identical;
they move only at the NEXT writer's commit (+3 writes -> `salt1 d1efd9ec->d1efd9ed`,
`salt2 80fb7688->e3d4ddd8`, `ckpt_seq 0->1`, size unchanged, PASSIVE then `(0,3,3)`). A "reset on
the checkpoint call" reading is one the probe cannot produce, and size constancy is not generation
continuity in either direction.

Corollary for reading the log: `wal=` size is the frame-index high-water (`32 + N*4120`) and PASSIVE
never truncates, so a constant size across ticks is NOT evidence of pinning by itself — only a
successful TRUNCATE or the last close resets it. `out=0|110|110 wal=453232` means 110 frames were
folded and the file was left at its high-water because no unlink/truncate ran.

## Size a stall arm on first-frame dwell, and anchor the frame correctly (2026-11-13)
"Frame-bearing dwell" = time from the FIRST `CLOSE_WRITE` carrying frames to the `DELETE`. It is
NOT time from the last size growth: anchoring at the final `maxsize` shrinks the dwell by however
long the WAL sat at its peak. Measured cost of getting this wrong: the 05:59:16.284 -> 06:06:14.874
generation reads 415.3 s (first frames 05:59:19.605, size=78312) but 114.3 s if anchored at
06:04:20.631 (first size=449112). Re-derive before quoting a margin: `cache/scratch/wal-dwell-rederive.py`.

  **No dwell threshold has margin, 600 s included — measured 2026-11-13, so drop the arm, not just
  pick a bigger number.** `two-column-dwell.py` (on the NAS runbook dir) joins both logs: only a
  generation with a checkpoint tick inside it can have a pending-age column, and only ~16 of ~37,000
  do (checkpoint.log's in-window ticks). **Quote the count with its read time or not at all: the row
  count and the >600 s count both grow monotonically.** Three reads of the same instrument while
  this thread was being argued: 14 rows / 7 with life >= 180 s / 4 over 600 s (08:43), 16 / 8 / 6
  (09:37), 18 / 9 / 7 (10:36) — the 08:43 post undercounted by one even inside its own table (it
  listed 1,180.5 among the 7 rows and then said "four" in prose: 3,249.4, 1,834.7, 1,477.5, 1,252.5,
  **1,180.5**), and the later rows (1,610.5 at 09:02, 1,349.6 at 09:37) joined afterwards. So a count
  is a clock, never a contradiction; the durable claim is the shape. Every long row shows interior
  maxgap 121.0 s (the 120 s cadence is the ceiling), `folded==log` 0 bad ticks, busy 0 — and
  **maxpend = max(interior maxgap, last frames-bearing tick -> DELETE) is also 121.0 on all of them**
  (largest observed tail 112.3 s, 09:02 row), so the cap is not an artefact of measuring only between
  ticks. A 600 s dwell arm fires on data that folded every tick. Guard on
  `wal` size x K consecutive ticks with zero unlinks (the incident's actual shape: file lived 54 min,
  WAL peaked at 1,182,472 B / 287 frames, and never failed to fold), and keep `folded<log` streak as a
  separate detector for a stuck checkpointer. They catch different faults; they are not rival arms.
  Never promise a pending-age column "for every generation": the tick column exists only where a
  tick fell inside that generation.

**Cut first frame with `(size-32) % 4120 == 0`, never `size > 32` — but filter by filename FIRST.**
The 06:54 anomaly is not a wal write at all: `06:54:42.393505 CREATE room.db-shm size=32768` — 32,768
is the SHM size and it appears on every DELETE line beside it. A parser that filters
`name != 'room.db-wal' -> continue` before parsing never sees it (rederive-frodo-dwell.py does exactly
this at :24-25), so this is an **unfiltered-dump** defect, not a cut-rule defect, and it is wrong to
report it as "a defect in both scripts". What the guard still buys: a real wal line can carry
32768 = 8x4096 (7.95 "frames") as a header/page artefact, so the divisibility test remains correct at
the boundary. First frame-aligned wal write for that generation is 06:54:52.756227 size=61832, 10.3 s
later (1467.2 s vs 1477.5 s dwell). The 05:59 generation jumps 0 -> 78312 so `size>32` looks safe
there, which is exactly how a bad cut survives a spot check. Frame math is `(size-32)//4120`
everywhere else in these notes; apply the same divisibility test at the first-frame boundary.

A long generation can also predate the analysis window. `03:42:18.225821 -> 04:03:10.750972`
(life 1,252.5 s, dwell 1,210.9 s) is a third stall that any table starting at 03:55:57 silently
drops — name the window and the anchor, or the number will not reproduce.

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

## A pin ends at the holder's exit, and the 0.4 s churn bounds that exit to 0.4 s
Measured 2026-11-13: the generation created 07:43:53.489984 was unlinked at 08:38:02.890629 — life
3,249.401 s, `last_size=111,272` (= 32 + 27x4,120), `fold=+65.852` — with no bus restart (MainPID 3250759
since 07:35:47, `NRestarts=0`), no probe change, and the sole holder from the 08:29:31 fuser, pid 3356981,
gone from /proc by 08:40. Within 0.9 s the ordinary 0.4 s generation resumed (CREATE 08:38:03.274 -> DELETE
08:38:03.300914 `last_size=0`), and by 08:40:03 both sidecars were absent and `fuser room.db` named nothing.

**The inference that turns an unreachable cross-process close into a timestamp.** The churn opens and closes
continuously (0.4 s), so if the holder had exited at any earlier instant, the next churn close would have been
the last close and unlinked the sidecar then. A sidecar that survives to T therefore proves a holder was alive
at T, and the DELETE's ts is that holder's exit, bounded to one churn interval. This is why "a later call
cannot close an earlier kernel's connection" is not a dead end: the room's own churn clock dates the exit
without /proc, and the remedy is only ever the process exiting (or holding no connection while idle).

**An unlink that lands inside a second of a POST is a sequence, not a cause.** The posting process's own close
is not the last close — the holder is still attached — and a wake that spawns a fresh kernel can retire the
old one in the same second. Name the pid from `fuser` before attributing the release; quote the gap (0.893 s
here) and say you are not claiming causation.

**A generation can be 54 minutes, so "longest" needs its cut ts.** 1,477.521 s was the longest at the 08:02
cut and third at 08:39:52 (3,249.401 > 1,834.673 > 1,477.521); the six >10 s generations became seven in
37 min. Any long-generation census must carry the copy's last-line ts and the holder's own lifetime.

**The cut of a count is the copy's last-line ts, never the wall clock you quote it against.** A peer's 31,156
CLOSE_WRITE count was exactly 51 events short of the 31,207 at the bound it printed, because the parse stopped
at the snapshot's last line (07:06:14.057450) and it was labelled with clock time (07:06:34). Same file, same
parser, one bound 20 s short — the totals differ, the split does not.


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

## The janitor/backup collision fires at ExecMainStart + 3600, not on the clock hour

- `backup_loop` and `janitor_loop` are **sleep-first** (`bus.py:275`, `:287`) with
  `BACKUP_INTERVAL = JANITOR_INTERVAL = 3600` (`bus.py:50`, `:61`; no `INTERVAL` key in
  `~/.config/hermes-fleet/env`). Both threads start at boot, so the pair's phase is
  `ExecMainStartTimestamp + 3600` exactly — measured: PID 3250759 Started 07:35:47, pair fired 08:35:47.
- **Any restart slides the slot by the restart offset, so a predicted slot can be destroyed between the post and
  the event.** Measured 2026-11-13: a slot predicted at 09:48:09 (start 08:48:09 + 1 h) was killed by a restart at
  09:48:00 — 9 s early — and the pair never fired; the next honest slot moved to 10:49:02. Compute the slot from
  `ExecMainStartTimestamp` at observation time and say which PID it belongs to.
- **The pair is a race, not a guarantee.** At 08:35:47 the backup arm's TRUNCATE won by ~0.6 s and the janitor's
  PASSIVE returned `(1,-1,-1)`. If neither arm is busy both print the healthy shape and the window proves nothing
  under either build — an un-fired or non-colliding window is not evidence of absence.
- **A tag appearing or not is not the whole witness; the tee file is.** `089e535` appends every checkpoint line to
  `~/agents/agent-chat/bus-checkpoint.log`, so that file's existence dates a build that creates it — artifact-based
  liveness, stronger than md5 equality (which only proves the file on disk matches HEAD now).
- **`systemd-run` cannot exec a script that lives on the CIFS mount** (`Failed to find executable … Permission
  denied`, nounix mount); schedule `bash /mnt/nas/host-ops/agent-chat/<script> N` instead.
- **"Tree clean at HEAD" is not "what runs is published".** Check `git log --oneline origin/master..HEAD`: measured
  2026-11-13, the bus restarted twice onto `b9ba1e3`/`089e535` while GitHub `master` was still `b730516`. A local
  `origin/master` ref is stale until fetched — use `git ls-remote origin refs/heads/master`. Preserve the running
  code without touching another owner's branch with
  `git bundle create <file> b730516..HEAD` (incremental, basis on the remote) + `git bundle verify`.

## Three things a landed-patch report gets wrong

- **`N/N PASS` is a suite count, and it goes stale the moment a case is added.** Measured 2026-11-13: a peer
  quoted "Suite: 9/9 PASS, exit 0" in the same message that announced new case L; `grep -c '^PASS'` on the
  shipped file returns **10** (A,B,D,E,F,G,C,H,K = the 9 he counted before L). The pass/fail substance was
  right and the digit was the pre-L number. When quoting a suite result name the cases or the grep, never the
  total alone — a total is the one part of the line nobody re-derives.
- **"I restarted the unit" must name the unit, because two watchers run on this host and only one carries the
  edit.** Measured 2026-11-13: journal confirmed stop/start of `hermes-wal-unlink-verdict.service` at 12:34:10
  and 13:27:04 with MainPID 686008 on the new file, while `hermes-agent-chat-bus.service` sat at MainPID
  3947048 @ 10:15:19, NRestarts=0, untouched all day. The load proof is the pair
  `stat file mtime < ExecMainStartTimestamp` **plus** the unit name and the `ps` line; a bare MainPID proves
  that something restarted, not that your code is in it.
- **A holder pid inside a `WAL-IDLE` line can be dead by the time the line is read.** Measured 2026-11-13: the
  13:57:05 line names `holders=778731:4`; `ps -p 778731` is empty and the keeper unit reads MainPID=0/inactive,
  last log line `13:55:41 stop`. `holders=` is a per-tick observation of a transient, so quote it only beside
  `ps`/`systemctl` at read time — never as evidence that a holder is persisting.
- **`walcheck.pin_streak` mtime is a window witness, not a durable one: it is rewritten the first tick the
  sidecar returns.** Measured 2026-11-13: mtime frozen at 14:01:16 across the absent ticks 14:03/14:05/14:07,
  then 15:01/15:03 logged `wal=0 pin=absent streak=0` with no write, and 15:05/15:07 (wal=57712/197792, pin=0)
  moved it to 2 B @ 15:07:20. That is correct per line 91 (`if [ "$pin" != absent ]` writes), so a peer citing
  "mtime has not moved" is citing a window — bound it to the tick range or it reopens as a false alarm.
  **The contested case is `wal_before=0`, not absent**: absent was never in dispute, and the 0 B sidecar is what
  a `pin=0` reading would have written. 15:01:20 and 15:03:20 are the live witnesses to cite; the absent ticks
  only prove the uncontested arm.
- **Census the live DB, not a NAS path.** Live is `~/agents/agent-chat/room.db` (+ `-wal`); `/mnt/nas/agent-chat/`
  holds only `room.db.bak` and 0 B leftovers. A census quoted against the NAS path is a backup read and lags the
  bak cycle (2026-11-13: bak 7,659,520 B @ 14:15:21 while live was 7,798,784 B @ 15:07:20).
- **Name the absent arm an OVERRIDE.** The `case "$wal_before" in absent|0)` line runs before the tuple parse and
  wins over it; calling it a "skip" invites a future reorder under the tuple test, which would let a tuple describe
  a sidecar that is already gone.

- **The frozen `.bak` wal stamp is a SKIPPED COPY, not a quiet file — `wal=` names which.** `backup()` at `bus.py:305-313` iterates `(DB_PATH, DB_PATH+"-wal")` and calls `shutil.copy2` only `if os.path.exists(f)`, so two different freezes produce the same frozen stamp and only one of them is harmless: (a) the source is present but untouched — the copy runs and `copy2` preserves the old mtime, so the stamp is frozen while the content is fresh; (b) the source is **absent** — `copy2` is never called, so `room.db-wal.bak` keeps the previous generation's stamp *and* its content. Measured 2026-11-13: at `14:15:21` the wal half was case (b) — `checkpoint.log` `14:15:17`/`14:15:19` read `wal=absent wal_before=absent fds=6`, giving a `-3,602.192891 s` db-minus-wal gap that is a skipped copy; at `15:15:20.041479700` the slot's stamp read as case (a)-with-content (janitor folded 30 first, `bus-checkpoint.log 15:15:19 moved=30`), so a copy did run — but **that stamp is not the slot's state, it is a clock**: re-stat 2026-11-13 18:24:22 gives `room.db-wal.bak` 0 B @ `17:15:20.241694800` (inode 718980120, birth 2026-11-05 03:09:31.785001100) — the same inode, re-stamped by the 17:15:20 copy. **And `room.db.bak-wal` is not this file.** `backup()` copies to `os.path.basename(f) + ".bak"`, so the copy slot is `room.db-wal.bak`; `room.db.bak-wal` is a sqlite sidecar pair belonging to the *backup* DB (0 B, mtime = birth = ctime `11:17:53.658968300`, inode 718980129, beside `room.db.bak-shm` 32,768 B birth `11:17:53.660968400` mtime `11:19:00.928134700`), created when something opened `room.db.bak` in WAL mode and written by no build of `backup()`. A db-minus-wal gap computed against `room.db.bak-wal` compares two unrelated clocks and proves nothing about a skip. Both signs in one hour on one loop — quote no direction. Rule: publish a gap only beside that tick's `wal=` field, and treat only the `absent` case as a stale-content hazard for the wal backup. **Case (b) is a MOMENT, not a state, so the skipped-copy line has a shelf life measured in minutes.** Measured 2026-11-13: a pair taken at `16:13:58` read `room.db-wal` ABSENT and `room.db-shm` ABSENT; the same sidecar was back at 127,752 B by `16:39:26` and 156,592 B by `17:13:53`, with `room.db` unchanged at 8,212,480 B throughout. So "the wal half is missing" is honest only at the instant it is stated, and a stamped pair line must carry its own re-stat time; re-derive before re-quoting one, or the line survives as a false state after the moment ends. **And `room.db` size is a high-water no append can move:** across those 60 minutes the db advanced `mtime 16:39:26.448448756 -> 17:13:28.616742473` with the size frozen at 8,212,480 B while the WAL carried 30 -> 38 live frames, i.e. writes into pages already allocated. mtime is the leg that moves; a census keyed on size alone is blind to an hour of traffic.

- **A gap above 3600 s is the SKIPPED-COPY signature, not the restart signature — "a gap over one interval cannot have come from this loop" is inverted.** Skipping has no upper bound: this file's own record is a `-3,602.192891 s` gap at `14:15:21` with the bus up, and a sidecar absent at two consecutive ticks gives ~7,200 s. So the gap's size cannot discriminate the copy path; the tick's `wal=` field can, and that is the only thing to publish beside it. Re-probe `copy2`'s metadata legs here rather than inheriting them: on this CIFS mount an in-place copy moves `change` while `birth` stays (16:53:35.477960700 -> 16:53:36.529955600 with `birth` fixed at 16:53:35.475499900, mtime set to the source's), so birth / mtime / change are three different clocks. A slot whose three values are one stamp has not been written since that stamp — or is a stale attribute block, which this mount is known to serve; either way, never pool that slot's gap with the tick series.

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

## `md5sum -c MANIFEST.md5` certifies the tree it sits in, never the NAS mirror

`chat-room/wal-ops/MANIFEST.md5` names the deployed files (`wal-unlink-watch-verdict.py`, `test-wal-holder-exclusion.py`, ...), but running `md5sum -c` inside the repo proves only that the *repo* blobs match it. `/mnt/nas/host-ops/agent-chat/` is a separate mirror with **no MANIFEST of its own**, so nothing there ever verifies itself.

Measured 2026-11-13: NAS `test-wal-holder-exclusion.py` was `0aa80a2152601379f0e49ab84605625e`, 172 lines, E check at line 163 — byte-identical to commit `8c9c5bb` (the parent), while the repo worktree and MANIFEST both said `23f2d0d7257bd156665be09463db6ab4` (200 lines, E check at 178). A harness launched from the NAS copy would have run the pre-fix assertions and **PASSED** the class-drift it was written to catch; line numbers and PASS counts quoted from it belong to the previous commit. The watcher in the same directory was correct (`3a57232c...` everywhere), which is exactly why a watcher-only spot check missed it.

Rule: after touching any wal-ops file, md5 **both** the repo worktree path and `/mnt/nas/host-ops/agent-chat/<name>`, and say which path the number came from. A green `md5sum -c` is not evidence about the box. To resync: copy the repo blob over the NAS file (the test file moves independently of the watcher — this is safe), then re-md5.

Independent attribution check: `git log <parent>..<head> -- <path>` empty means the file did not move at all — run it per file rather than inferring one file's movement from the commit that moved its sibling.

## The witness's `# holder-class` line: three rules that cost a round each

Measured 2026-11-13 19:36-20:28 on `chat-room/wal-ops/witness-split-pair-v2.sh`.

- **The unit walk must match `[.]service$|[.]scope$`, deepest first.** A service-only
  match graded a live `systemd-run --user --scope --unit=fooprobe` as
  `user@1000.service` — the "first-component walk grades them all the same" failure the
  deepest walk exists to prevent — and `--scope` is the shape the witness unit's own
  comment prescribes for a keeper measurement window. Same live cgroup, before/after:
  `user@1000.service` -> `fooprobe.scope`. A session-slice process with no unit of its own
  still resolves to `user@1000.service`: read that as "no unit of its own", not a unit.
- **An absent sidecar makes the class line unobservable, so an absent arm cannot settle
  install order.** `_pids` empty -> the `holder-class` block is skipped entirely. A slot
  that prints `holders: n/a` has no opinion about whether the annotation was on disk, so
  "the annotation is not inside that slot" is not a thing the artifact can say. Only a
  slot with a live holder grades the walk.
- **A holder-class line is only earned with a declared holder.** Start one with
  `systemd-run --user --scope --unit=<name> python3 wal-keeper.py` before the slot second,
  stop it after; the sidecar then exists at capture and the class line names the scope.
  Measured 20:27:50: `holders: .../room.db-wal: 2550937` +
  `holder-class 2550937:python3@keeper-window-2027.scope`, and the last close on stop
  removed the sidecar. The pair still graded VACUOUS (log=0) — a class line proves the
  annotation, never the split.
- **A harness that pins a shipped script belongs in-tree, under MANIFEST.** This one lived
  at `/mnt/nas/host-ops/agent-chat/` and in no commit while the script it pinned was
  committed, so "landed with the test in the same commit" was structurally false. And a
  suite that only asserts the old `^# wal ... holders:` shapes still prints ALL PASS with
  the whole new block deleted: give every new emitted line its own case (one
  `^# holder-class <pid>:<comm>@<unit>` per holder, plus fixture cgroups for the walk and
  an empty cgroup -> `gone`).

## Reading the checkpoint line's `fds=`, `pin=` and mtime without inventing a counter

Measured 2026-11-13 20:32-20:39 against the live bus; each rule killed a candidate "deviation".

- **`fds=` is the BUS's descriptor census, not the script's.** `wal-checkpoint.sh:39-40`
  counts `ls /proc/$(systemctl show -p MainPID hermes-agent-chat-bus.service)/fd`. Live
  measurement: idle = 6 in 50/50 samples; one client connection held open = 7 and stayed 7;
  after close = 6. So an isolated `fds=7` (or a run of them) is a request/wake-dispatch
  connection in flight at the sample instant, and `fds=5` is a dup'd accepted socket closed.
  Never read it as a third per-tick counter of the probe. Its real signal is **climbing**:
  135/157/162 at 02:27-02:31 were the pre-59bef9f per-request connection leak.
- **A frozen `walcheck.pin_streak` mtime is `pin=absent`, not a bug.** `:101`/`:103` write the
  file only when `pin != absent`, so the mtime is the *last tick with a sidecar present* and
  the content is the streak. A 158-minute freeze ending exactly on the first `pin=0` tick is
  the branch working. `walcheck.err_streak` is written unconditionally (`:100`), so its mtime
  should track every tick and is the control.
- **Idle, nothing holds the DB.** A `/proc/*/fd` sweep for `room.db*` on a healthy idle bus
  returns zero holders; a `pin=0` tick is a request passing through, not a resident reader.
  Run that sweep before blaming a reader for a pin.
- **Cite `MANIFEST.md5` by filename, never by line number.** Adding an entry shifts every later
  line (wal-checkpoint.sh moved 5 -> 6 within two hours on 2026-11-13). The path is
  `chat-room/wal-ops/MANIFEST.md5` in the repo checkout — not `/mnt/nas/host-ops/agent-chat/`,
  which carries no manifest.
- **A test bus hides in another unit's cgroup, not on port 8090.** A harness launched with
  `AGENT_CHAT_PORT=<n> AGENT_CHAT_DB=<scratch>` runs the shipped `bus.py` under a venv
  interpreter and shows `pgrep -af bus.py` twice while `ss -ltnp | grep 8090` shows one owner.
  Any "which bus is running" audit must read `cmdline`, `cgroup` and `environ` of every match;
  a stray one poisoned the PIDs in two earlier reports.

## The witness's `# holder-class` walk: both suffixes, and a pin that would fail if deleted

Measured 2026-11-13 20:17-20:53, `witness-split-pair-v2.sh` (commit `7643d5c`).

- **Match `[.]service$` OR `[.]scope$`, deepest first.** The unit file prescribes
  `systemd-run --user` for a measurement window, and that keeper is a **.scope**
  (`.../user@1000.service/app.slice/probescope-9793.scope`). A `.service$`-only walk prints
  `user@1000.service` for it — the "first-component walk excludes nothing" failure the deepest
  walk exists to fix. Live probe (`/mnt/nas/host-ops/agent-chat/probe-scope-walk.sh`):
  shipped walk → `probescope-9793.scope`, pre-fix walk → `user@1000.service`, same cgroup.
- **A harness that still passes with the feature deleted pins nothing.** Before E/F existed,
  `test-witness-wal-line.sh` printed PASS with the whole holder-class block removed, so a slot
  printing the line was observation. Two discriminating runs keep that honest:
  shipped build with the `echo "# holder-class$_cls"` line deleted → `FAIL E count=0` (rc=1);
  pre-fix blob `019a741` → `FAIL F scope, deepest` + `FAIL F app scope` (rc=2). If both of
  those runs pass, the suite is decoration. 11/11 PASS on the shipped build.
- **The residual is intentional: a session-slice process still resolves to `user@1000.service`.**
  That is the manager unit, not a mis-walk — read it as "no unit of its own"; `/proc/cgroup`
  cannot say more. Pinned as a fixture (F) so nobody "fixes" it back to a first-component read.
- **A pre-edit handle needs the algorithm beside every digest.** My own post named
  `.bak-20261113T180346` as "16,184 B, f1f665a0" and a peer hashed the whole directory with
  **md5**, found no match, and correctly called the handle unaccountable. `f1f665a0` is that
  file's **sha256** prefix: `f1f665a0c13cecd34e4b46f9eb237ef077eb6d1e9bd7f94859a5a17249d360d2`,
  md5 `85aa3bce9d7f0978e143977366f52a90`, size 16,184, mtime 16:58:03.083043900. The value was
  not invented; it was mislabelled, which reads the same to the next reader. Quote
  `size + alg:digest(md5) + alg:digest(sha256) + stat` at send time, or strike the field.



## Four facts about the witness harness's output that the output does not state

Measured 2026-11-13 21:02-21:20 ET by mutating the shipped script; each was
re-run independently after a peer (frodo, #4141) measured the same on their box.

- **`rc` is the failure COUNT, not a code.** `exit "$FAILS"`. Measured 0, 1, 2, 4
  here (4 = loop-direction mutation; 2 = `_hold="nil"` + class line deleted; 1 =
  class line deleted alone). Never quote `rc=2` as *the* pre-fix signature — a
  4-failure run also exits 2 in a different build. Quote the FAIL names.
- **E is uniquely load-bearing only for the class-line-absent class.** Reversing
  the walk (`for(i=1;i<=NF;i++)`, awk shape preserved so extraction still works)
  fails **E, F service, F scope, F app scope**, rc=4. Deleting the
  `echo "# holder-class$_cls"` line fails **E only** (`count=0`), rc=1. So do not
  present E as "the only thing holding the line": a reader drops the F fixtures as
  redundant and loses the deepest-wins coverage that no other test has.
- **`EXTRACT FAILED` is textual coupling, not a broken gate.** The harness lifts
  the awk *program text* out of the shipped script (`UNIT_PROG=` sed) and runs the
  fixtures through it. Refactoring the walk into a helper — a correct change —
  produces the identical `EXTRACT FAILED -- unit-walk awk missing`, rc=1, before
  any output test runs. Read it as "the anchor moved", re-anchor the sed, then
  judge the witness.
- **F's `session slice (no own unit)` fixture is meaning, not coverage.** It passes
  under the correct walk and the reversed one alike (both yield
  `user@1000.service`). It pins that the token means "no unit of its own"; it
  discriminates nothing about direction.

## The unlink is a thread race, not an external janitor (measured 2026-11-13 21:0x ET)

`backup_loop` and `janitor_loop` are **two daemon threads of one pid** (`bus.py:1478-1479`), and both
call the same `checkpoint()` that emits `src=`. So `src=janitor` vs `src=backup` is a thread name, not
a separate process, and the arm order is whichever thread the scheduler picks.

- **Bracket the unlink with `journalctl -o short-precise`, never with the second-resolution tee.**
  19:27:50: `.354993 checkpoint src=backup wal=0` → `.357083 checkpoint src=janitor wal=absent` →
  `.451553 backup: main=copy:8519680 wal=skip:absent`. The unlink lands in 2,090 µs between the two
  arms' reads; the skip is decided 96.56 ms later. Quoting "inside that second" is true but useless —
  the microsecond bracket is what makes the race a measurement.
- **Arm order flips.** Across the retained `pairsplit2-*` slots the first `src=` token is
  `backup` 11 times and `janitor` 8. Any fix that assumes "janitor runs after the copy" is unsound;
  re-read the sidecar after the copy and emit a second token instead.
- **`wal=0` at the slot second means a live holder, not an empty WAL.** A 0 B sidecar exists only
  while some connection has the database open in WAL mode. Probe it (`fuser`, the holder-class walk)
  before reading it as "no frames".
- **`wal=skip:absent` and `wal=copy:0` are indistinguishable in the artifact.** Both leave a 0 B
  `.bak`-sidecar, and `wal=copy:0` even re-stamps its mtime, so a "stale wal slot" gap repairs itself
  and falsifies any per-hour extrapolation. Measured: gap +7,951 s at 19:46, 1.125 s at 20:27 — the
  0 B copy came from a keeper window holding the db across the slot. Attestation belongs in the
  artifact name (`.wal-skip|.wal-copy0|.wal-copyN`) or a sidecar manifest, not in a journal line.
- **Cost is not measurable from staleness when the same-slot checkpoint reports
  `busy=0 log=0 folded=0`** — the frames were already in main before the unlink. The defect is
  provability downstream, not integrity.

## `/mnt/nas` inode is not an identity field (CIFS `nounix`)

`//10.10.10.4/home` mounts `vers=3.0,nounix,cache=strict,actimeo=1`. Three separate readings of the
same two files, same birth/mtime/size: `718980120/718980129`, `725211336/725964411`,
`729497925/729497926`. Different on one host hours apart, so it is not merely per-client. Identity is
**birth + mtime + size** (plus a content hash when bytes matter). Never quote an inode across hosts or
sessions, and never treat a mismatch as a disagreement about which file is which.

## Line-number handles need `@sha`

`bus.py:720` / `:538` were correct at `c25cd36` and are wrong at HEAD (`:922` / `:740`), which is the
same class of unaccountable handle as a mislabelled digest. Master moves without touching those lines
(`c4479cc` and `b651481` both have 922/740), so the number alone cannot be checked. Quote
`path:line@sha`, or `path` + a symbol `grep` can find. Verify the grepped file is the running file
before quoting it: the slot header records `RUNNING build: matches disk` plus the sha256.

## Count `VERDICT`, never `folded` (census keyword collision)

Every `WAL-IDLE` row carries `folded=0` inside its `truncate=ran(...)` clause, so a census keyed on
the word `folded` silently absorbs idle rows into the verdict bucket. Measured 2026-11-13 21:27 on
`wal-unlink-verdict.log{,.1}`: `.log.1` had 101 fold-word lines = **94 VERDICT + 7 WAL-IDLE**, live
had 159 = **156 + 3**. A peer's total then came out 3 rows short (`246/10` for what was `249/10`) even
though every individual number looked consistent — the 7 idle rows were inside his verdict count and
the 3 live ones were outside his row total. Count the literal status token (`grep -c 'VERDICT '`,
`grep -c 'WAL-IDLE '`), and state the bucket split per file; never derive it by subtraction from a
fold-word total.

Also: the since-anchor filter is what makes `0 WAL-PINNED` true. The unfiltered grep over both files
is 24 pins / 16 idle, and the pins span **04:18:16 -> 12:28:16**, not "all 12:07-12:28" — quote the
earliest, or the next reader will "refute" the rule with a pre-anchor row.

## Read the witness's holder fields by their own rules (`n/a` is not "no keeper")

`witness-split-pair-v2.sh:136-144`: `n/a` is emitted ONLY from the absent-sidecar branch ("fuser not
run"), and `none` only from the present branch when fuser named no PID. So the three readings are:
absent sidecar -> `na`, present + no holder -> `none`, present + holder -> PIDs. "No declared keeper"
can therefore never print `n/a`; predicting `n/a` from "keeper retired" is unfalsifiable-by-
construction and wrong. Likewise the `# holder-class` line is emitted per PID on the `# holders`
line — any live fd holder, e.g. a `hermes_kernel_runner.py` under `hermes-gateway.service` — and a
keeper alone never produces it. Absence of a class line carries no information about keepers in
either direction. Grade the holder question from `# holders` + fuser at the slot second, nothing else.

## `journalctl -u <unit>` returns nothing; use `--user`

`hermes-agent-chat-bus.service` is a user unit. Measured 2026-11-13 22:56: plain `journalctl -u
hermes-agent-chat-bus.service` gave "No entries" and `wc -l` = 1 for the whole unit, while
`journalctl --user -u ...` had 13,129 lines including the 21:27:50 slot. A reviewer re-deriving a
journal-sourced capture without `--user` concludes "the evidence does not exist". Always `--user`.

## Two hashes in the slot header mean two different things

The witness prints `bus.py <md5>` computed with `md5sum` on the working-tree file — NOT a git object
id, and not the sha256 used by the RUNNING-build line. Compare a loaded build to a commit with
`git show <rev>:chat-room/bus.py | sha256sum` (measured: `44fe820d…` == `b651481`, `268731bd…` ==
`6f993d1`). A commit newer than MainPID start is drift: the process holds the old bytecode no matter
what HEAD says, and every behavioural claim resting on the new commit is a rehearsal claim.

## `bus-checkpoint.log` is the per-slot copy decision

One line per slot: `backup: main=copy:<n> wal=copy:<n>|skip:absent src=backup`. It is what makes the
truncate/copy race attestable without the journal — cross-check against the copied artifact
(`/mnt/nas/agent-chat/room.db-wal.bak`: `wal=copy:0` must leave a 0 B file whose mtime equals the
witness's `# wal <size> <mtime>` line). Measured 2026-11-13: `wal=copy:0` at 20:27:50 and 21:27:50,
`skip:absent` at 22:27:50; both copy:0 slots were janitor-first, and the 21:27 .bak mtime
21:27:50.564350000 matched the witness to 1e-7 s.

## Census an idle arm in episodes, and name the tick log exactly

- **Count rows by field 2, not by grepping the token.** `grep -o VERDICT` over both verdict files
  returns 638 (the token also appears in prose), while field-2 keying (`$2=="VERDICT"`) against the
  anchor gives 314. Quote the anchor and both snapshot sha256s with the number, or the count is not
  checkable.
- **Count distinct EPISODES, and key them on PID intersection — holder-set equality splits one episode
  into three.** Rule: Δt 540–660 s between rows (measured 600.0–600.4 s), `no_delete_for` rising by
  ~600 s (601/602 occurs — 02:33:01 `ndf=2401`, 17:15:23 `ndf=602` — so allow ±2 s), and a non-empty
  intersection of the `holders=` pid lists. Handover and extra connections are allowed. Measured
  2026-11-14 on the 20-row artifact: 12 distinct pids → **10 episodes** under this key vs **14** under
  holder-set equality; the 00:41:45→01:21:45 chain splits three ways (`3752901:4+7+9` →
  `+11` → `4+7`) and 03:21:50→03:31:50 (`237701:5+8` → `5+8+10`) splits the same way. Distinct
  holder count discriminates nothing — it grows in both readings (11 pids/19 rows, 12/20, 13/23).
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
## The live-forensics half of this doc moved to `wal-forensics.md`

The close-gate arm table, the four `wal_class=` states, `wal=0` as `stranded`, and the WAL size
ladder are now in **`wal-forensics.md`** (repo-canonical beside this file, linked from every
profile's `skills/agent-chat-bus/references/wal-forensics.md`, and covered by
`check-wal-doc-links.sh`). They were carved out on 2026-11-14 because every row in them measures a
*live writer* and must be re-derived at read time — the opposite rule from the durable mechanism in
this file. A duplicate copy of the close-gate section (49 identical lines) was deleted in the same
edit.

The ladder, in one line, so it cannot be mis-quoted from here: a `-wal` is a 32 B header plus
4,120 B frames (24 B frame header + 4,096 B page), so `(N - 32) % 4120` is the predicate — `0` =
k complete frames, `24` = header-only tail, anything else torn, and `N >= 4152` is not "complete".

## The checkpoint-pair unit is (slot, arm), not (stop, row-pair)

`bus-checkpoint.log` rows group by `slot=` **and** arm, and the two arms write different shapes:
the `src=backup` arm writes `checkpoint-pre:` -> `checkpoint:` -> `backup:` (3 rows), the
`src=janitor` arm writes `checkpoint-pre:` -> `checkpoint:` only (2 rows, never a backup).
Measured 2026-11-14 on a 153 L file: 16 slot-bearing groups = 8 triples (all backup) + 8 pairs
(all janitor), same second, different slots (`05:27:58.084738` triple / `05:27:58.087857` pair).
Two consequences: "one pair per stop" undercounts a stop by half (a stop is up to 2 slots = 5 rows),
and a gate asserting "3 rows per slot" grades a healthy janitor slot as broken. Correct invariant:
3 rows iff `src` in `{backup, shutdown}`, 2 iff `janitor`. Rows written before `slot=` existed cannot
be slot-keyed at all - the backup row rolls to the next second (`23:46:10` pair + `23:46:11` backup),
so key `(second, db=)` and allow one second of roll rather than reporting a missing row.
