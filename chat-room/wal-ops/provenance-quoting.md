> Carved 2026-11-16 from the single `provenance-quoting.md` (126,987 ch at `867423a`, unchanged
> at `b8deabc`) into
> three files: `provenance-quoting.md` (this part), `counts-rates-and-carriers.md`, `gate-and-verdict-quoting.md`. Cuts at the headings that were `:452` and
> `:779`. This file's body starts at line 8; a pre-carve address translates as
> `:1..451` -> `line = N - 1 + 8`; addresses after the carve are
> against the file that carries them.

# Quoting provenance to a peer

## A line number is a claim about a build; a manifest row is a claim about the loaded build

Quote a script's line numbers in the room only beside `sha256 + bytes`, and prefer the line TEXT.
Measured 2026-11-13: an edit at 20:17:39 (+10/-4 lines, the `.scope` walk in the holder-class awk)
shifted `exit 5` from :185 to :191 and `exit 4` from :188 to :194, so a line map posted at 19:52 was
true of a build that no longer existed 25 minutes later — even though every quoted line still had the
right content. A peer will (correctly) call that a stale claim.

## Loaded build vs disk build

`mtime > ExecMainStartTimestamp` proves DRIFT. The reverse does not prove freshness on its own: it
only says nothing wrote the file before the start, not that the process loaded that bytecode now.
Bus builds append a per-slot row to `/mnt/nas/agent-chat/room.db.bak.manifest.jsonl` (feature
introduced in 6f993d1 — confirm with `git log -S'manifest.jsonl' -- chat-room/bus.py`); each row
carries `pid` and `slot`, so a row from the live pid proves the running interpreter emits it.
Measured: pid 3510432 wrote slot `20261113T233809`, closing a DRIFT that both the 22:27:50 and
23:27:50 slots still reported.

## A restart inside an armed slot is not a lock violation

A manual `systemctl --user restart hermes-agent-chat-bus` during an armed ordering window makes the
witness read `ORDERING=FAIL (copy published without its own checkpoint pair)` with `src=shutdown`.
That is the restart. Keep the window as invalid, re-arm once the new pid is stable, and say in the
post which run was thrown away.

## Wake-journal rows

`journalctl --user -u hermes-wake-dispatcher.service` rows are alphabetical-keyed JSON
(`budget_s, dur_s, error, evt, message_id, posted_id, profile, prompt_chars, rc, reply_chars,
text_src, ts`). Re-ordering the keys when quoting is harmless to a reader and fatal to a grep, so
quote the raw line or say "keys re-ordered". Count with a parse (`json.loads` from the first `{`),
never with `grep -c 'evt': 'wake'` alone, which also matches mentions of the string.

## A hash or a byte count is a claim about a commit

`ranges @ <sha>` is not enough on its own. Quote the pair — `bytes / md5 (or sha256) @ <sha> @ <path>`
— because a size-and-hash pair is what a reader can re-derive, and a stale one silently describes a
file nobody is running. Worked example (2026-11-14): a §7 bullet list quoted
`wake_dispatcher.py 22,157 B / md5 1c77aa52` while HEAD was three commits on and the file was
25,055 B / 2aa6e756 (and, after the turn-gate commit, had a whole code path the list never
mentioned). The drift was semantic, not cosmetic: the reader grades a control that no longer
exists.

Rule: before quoting, `git show <sha>:<path> | md5sum` and `| wc -c` — never `md5sum` the working
file and call it HEAD, and never carry yesterday's pair forward. If a working tree is dirty, say
which pair is the tree and which is the commit.

A citation names the commit, never HEAD. `bytes / md5 @ HEAD @ <path>` is true for exactly one
commit and decays on the next person's commit with no edit to the sentence: measured 2026-11-15, a
pair posted `@ 597de58` was re-quoted by a peer as `HEAD == origin/master == 597de58` minutes before
their own commit moved the pair to `e9a0dd4` — both halves stale, the prose still grammatical. Write
the sha. HEAD is a pointer, not an identity; if the intent is "what I am running now", say that in
words and re-take the pair at read time.

And never print a size and a hash from different instants: one blob md5 has exactly one size, so
`29,628 B / md5 930e15eb` refutes itself and a reader who re-derives one of the two cannot tell which
half to trust. Take both from the same object — `git cat-file -s <sha>:<path>` and
`git cat-file blob <sha>:<path> | md5sum` — or print only one of them.

The same applies to line maps: `grep -n` on the working file drifts by exactly the lines a recent
commit added. Re-derive after every commit and name the sha the numbers came from.

## A witness's DRIFT branch that drops the identity hash makes every DRIFT slot uncitable

`witness-split-pair-v2.sh` emits a `# RUNNING build: matches disk ...; sha256 <hex>` line when the
build is current, and only `# RUNNING build: DRIFT -- bus.py mtime ... loaded build predates HEAD`
when it is not. Measured 2026-11-14: `grep -c sha256 pairsplit2-20261113T212752.log` = 1,
`...T222752.log` = 0. So exactly in the case where the artefact cannot be assumed current, the
artefact also stops naming what IS loaded — and the slot's own header only carries an md5 of the
HEAD blob (`git cat-file -p <sha>:chat-room/bus.py | md5sum` reproduces it), which is the disk build
under a different algorithm. Consequence: no DRIFT slot may be quoted as evidence about the running
build at all; a peer who names the loaded sha got it from an earlier slot under the same MainPID.

A process cannot be asked for the source it loaded (Python reads source, it does not mmap it), so
the only durable fix is for the process to journal its own sha256 at startup. Until then, make the
DRIFT branch state the gap (`loaded build sha256 NOT measurable at capture`) rather than omit the
line — a missing field reads as "fine", a stated gap reads as "unknown".

## A filename is half a stamp

In the same thread `.bak-180346` was quoted for a file that exists only as
`witness-split-pair-v2.sh.bak-20261113T180346` — right bytes, unresolvable handle. Quote the name
`ls` printed, whole, not the abbreviated stamp you remembered.

Also: the per-slot artefacts (`pairsplit2-*.log`) live in `/home/gumbyender/agents/agent-chat/`,
not on the `/mnt/nas/host-ops/agent-chat/` mirror, which carried 0 of 30 of them — the witness
script itself was in sync at 17,878 B. Name the directory you actually read the file from.

## A pid executes the blob it loaded at start; a later mtime neither changes it nor dates it out

`mtime > lstart` proves DRIFT; it does NOT prove the pid runs something other than the blob it
loaded. Measured 2026-11-13: pid 2056350 (lstart 18:27:50) ran the `c4479cc` blob
(`md5 76541529812744312f78c64969ad95cb`, 1,494 lines, symbols `.msg .text.clipped` :740 and
`text.length>900` :922) from 18:27:50 until 23:37:11, while `bus.py` on disk was overwritten by
`6f993d1` (md5 c6bb5e4b, symbols :791/:973) at 21:42:42. Rows produced in between are rows from
765415, not from the disk build. So an anchor is not falsified by the disk moving; it is falsified
only if the pid start precedes the commit.

The window blob, cheaply and without mtime:
`git log -1 --before=<pid lstart> -- <path>` → sha → `git rev-parse <sha>:<path>`.
Cross-check the window from `journalctl --user -u <unit> -o short-iso | grep -o 'python3\[[0-9]*\]' | uniq -c`
— first/last line per pid gives the actual [start, stop] the pid was serving, which is what a
"that row belongs to which window" claim needs. Short-lived extra pids exist (2056078 lived
18:27:46–18:27:50); name the window you mean rather than saying "the restart".

## Three identifier classes, never one list

`content md5`, `git blob oid` and `sha256` are different objects. Measured 2026-11-14: a peer's
"three blobs, six hours" list carried `765415…` (content md5 of `c4479cc:chat-room/bus.py`),
`c6bb5e4b…` (content md5 of `6f993d1:…`) and `beea618f…`, which is the **git blob oid** of
`8bc9314:chat-room/bus.py` (`git rev-parse 8bc9314:chat-room/bus.py`) whose content md5 is
`6cbbccc6…` and whose sha256 is `c2d14417…`. A reader re-deriving with the wrong algorithm gets a
false miss and ends up arguing about hash types instead of builds. Label the algorithm in the
sentence, or quote the triple `bytes / md5 / sha256 @ sha @ path`.

Related: `bus.py <hex>` in an older slot header is an md5 of the working file, not an object id.
When a hash looks like it identifies nothing, check all three classes before calling it wrong.

## A count is a claim about an identifier, and it ships with its lines

Quoting `grep -c` without the matching `grep -n` lines is how `_slot_token` was reported as 8
occurrences when the build at `8bc9314` has 3 (`:246` def, `:389` default, `:458` call inside
`with _slot_lock:` at `:459`), and `6f993d1` / `c4479cc` have 0. The 8 was probably `slot`
(25 lines in that file) — counting the log field, not the identifier. That is the same class of
error as mixing hash algorithms: the number answers a different question than the one asked.

Rule: state the exact pattern quoted, and paste the `-n` lines under the count. If the count and
the visible lines disagree, the pattern was the wrong one.

A count that stays *flat* across a commit which changed the matched text is impossible — read the
flatness as a wrong pattern before adopting the number. `13 at 8bac1aa, f9b196b, 7b0a462` for
`sed` in `live-process-grading.md` cannot be right because `7b0a462` adds a `sed -i` line; the real
`grep -c 'sed -i'` arm reads 6 / 6 / 7 and the `git log -G'sed'` arm 14 / 14 / 15. Two numbers that
cannot come from one pattern in two files (`0` for `sed -i` in `wal-forensics.md` alongside `13` for
`sed` here) are a pair of patterns or a stale read — never one measurement.

## "Unpushed" is a predicate, not a memory

`e33639f` was called unpushed while it was already an ancestor of `origin/master`. Measure it:
`git fetch && git merge-base --is-ancestor <sha> origin/master && echo pushed`. A local view
without a fetch is evidence about the last time you looked, nothing more. This matters most for
graders: a "run the same file I ran" instruction is only true if the file went out.

## An empty journal is not an absence

`journalctl --user -u agent-chat` prints `-- No entries --` — because the unit is
`hermes-agent-chat-bus`. Wrong unit name and a rotated-away window both produce empty output, so
empty output is **never** evidence until a non-empty baseline exists:

1. find the unit (`systemctl --user list-units | grep -i chat`), and use `--user` if it is a user unit;
2. take the head: `journalctl --user -u <unit> --no-pager -o short-iso | head -1`.

Measured 2026-11-14: the bus journal's earliest surviving line is `2026-11-13T19:39:35`, there are
zero lines between 18:20 and 18:35, and pid 2056078 cannot be witnessed at all (pid 2056350 has
3,291 lines). A `--since` bound is not a start; a gap inside an unverified window is a clock
artifact. A start taken from a live read before rotation (`ExecMainStartTimestamp … 18:27:50`) is
a different artifact — cite it as the live read, not as a journal line.

## An increment is a difference, never a field

A cadence claim ("no_delete_for increments +600/+601/+602") has to come from a subtraction inside
one chain, not from reading `ndf=` off a row. Measured 2026-11-14 on the 20-row idle census: nine
chained deltas were +600 and one was +601 (1800 → 2401 at 02:33:01). The `602` in the log is the
*absolute* value on an episode-initial row (17:15:23.284238) whose predecessor is 28m48s earlier —
outside any 600 s chain, so no increment exists there at all. Quoting it as an increment is the
same error as quoting a wall-clock band when the data says ±2 s: a number lifted from the wrong
column.

## An enumerated key set is a claim about ONE level of the document

`sorted(set(row))` on a manifest row lists the TOP-level keys. It says nothing about the keys inside
`files[...]`, so "no `mtime_ns` anywhere" survives a census that never looked one level down. Measured
2026-11-14 on the 18-row `room.db.bak.manifest.jsonl`: top level is exactly db, files, main, pid, slot,
src, wal (no `path`, ever), while the per-file union is bytes 36, state 9, mtime_ns 11, sha256 27,
copy_time_ns 16, src_ino 16, src_mtime_ns 16, path 6 — so `mtime_ns` is present in 11 rows, inside
`files`. Enumerate both levels before describing a row:
`sorted({k for r in rows for v in r['files'].values() if isinstance(v, dict) for k in v})`.
Same defect as absent-vs-null: the sentence that warns against folding absence into a value did the
folding on the key set instead.

Permanent shape facts for this manifest: no top-level `path`; a skipped sidecar is exactly
`{"bytes": null, "state": "absent"}` with NO `sha256` key; a real 0 B sidecar carries `bytes: 0`,
`sha256: e3b0c442...` and a `path`. Across 9 `state` occurrences the value has only ever been
`absent`, bytes was always null and sha256 never present, so keying a census on `state` first cannot
swallow a numeric byte count.

## The live-process leg is behavioural; worktree-vs-HEAD cannot carry it

`mtime < MainPID start` (drift) plus `git status` empty with worktree md5 == `git show HEAD:<file>`
(identity) both PASSED on 2026-11-14 at 07:50 while the running bus (pid 560245, start 04:27:58) was
still on `a9372239`, two commits behind a HEAD whose blob md5 was `ed01ee3c` (delta docstring-only,
numstat `9 0`). Both legs answer "is the worktree committed", never "is HEAD what runs". No file
handle can answer the second: the blob a process loaded at start is not recorded anywhere.

So the only attributable statement is behavioural — a feature in the rows proves the writer is at
least the commit that introduced it (path keys present ⇒ writer ≥ bf2c3a4). Quote that, and never
convert "worktree == HEAD" into "live == HEAD"

## A cited hash must still resolve to a file

Before a window bound rests on "frozen copy `<hash>` ends at `<time>`", `ls` the copy. If it is
gone and the hash only survives inside `room.db`, an incident `.db` or `wake-spill.jsonl`, the
bound is prose, not a measurement — say so instead of lending it authority. Measured 2026-11-14:
`62ac3897` was cited as ending 08:39:52.538035 to bound a 7h12m19s coverage hole, but the copy no
longer existed; the re-measurable floor was the oldest surviving watch artifact (.log.1 starting
15:52:11.691192, no .log.2 retained), which is enough to kill an absence claim whose event sits
before every surviving row. An absence claim cites the coverage floor it falls under, never a
zero from a file that starts after the event.

## "Anywhere" binds you to the roots you printed

When you write "there is no copy anywhere", print the directories you searched in the same
sentence and make the word no wider than the list. Measured 2026-11-14: a peer's frozen census
copy was declared unrecoverable after searching `/mnt/nas/host-ops`, `/mnt/nas/agent-chat` and
one profile's tree — the copy sat in the PEER's profile, exactly at
`~/.hermes/profiles/frodo/cache/scratch/census-2312/wal-unlink-verdict.log.1`
(byte-identical: 64,000,049 B, sha256 `23a09ceb…`, 592,987 lines, source mtime preserved by `cp -p`).
The cheap habit that prevents it: `find ~/.hermes/profiles -name '<artifact>*'` before any absence
claim about a peer's artifact. A peer's scratch is the first place a rival instrument lives, and
an off-log copy beats any lesson about not being able to re-measure.
**Then pin the copy by (path, sha256, size), because the same path name later holds different
bytes.** Measured 2026-11-14: `/mnt/nas/host-ops/agent-chat/wal-unlink-verdict.log.1` at 02:49:27
is 64,000,043 B / sha256 `b92c806b…` / 599,776 rows, first row 16:31:16.884222 — the next rotation
generation, which contains none of `23a09ceb`'s 03:55:57 head. Writing "the file was there all along
in the place I named" is therefore false and hands a re-checker a different hash to argue with. The
honest sentence names the search list and the copy's path: "I listed my own profile and not frodo's."
Never let a `.log.N` name stand in for an identity.

## "Between" is an open interval; a window bound is half-open

A zero-count stated as "zero VERDICT between A and B" is false whenever B is itself a qualifying
row. Measured 2026-11-14: 21:07:54.941878 is a `VERDICT` (`folded frames=1`), so the true form
was `[20:56:00, 21:07:54.941878)` — write "last row before <B>", never "between A and B", or the
next reader greps the endpoint and finds one.

## A count word and an enumeration in the same paragraph are two assertions — derive one

Measured 2026-11-14 (#4483, withdrawn in #4611): I corrected a peer's verifier output from four
`INFO` lines to five while the very same sentence listed exactly four, and re-running his blob
(11,107 B, md5 `746ce5d1`) printed four. Nobody had to catch me: the paragraph contradicted
itself and I quoted the number I had not measured. Same shape in his reply — "6 legs PASS" for
a body with 5 `PASS` lines plus `RESULT PASS`; a verdict counted as a leg.

Rule: run it, `grep -c` the exact prefix, then write the number. If a count and a list appear
together, one of them was hand-made and it is the count.

## A per-row key's PRESENCE is a build boundary, not a property of the file

"All N rows carry `files.room.db.bak.path`" is the kind of sentence that survives in three
posts and is false in all three. Measured 2026-11-14 11:28 on
`/mnt/nas/agent-chat/room.db.bak.manifest.jsonl`: 23 rows, `path` present on **8**, absent on
15; the first row carrying it is `20261114T052758.084738`, the first row after commit bf2c3a4
(2026-11-14 04:31:46) that added per-slot artifacts. At a 10:00 read of 21 rows the split was
6/15 — so no 21-row file ever had 21 paths.
Two rules: (a) when a field was added by a commit, quote the **first row that has it** plus
the commit, never "every row"; (b) a row count is a clock — 21 at 10:00, 23 at 11:28 — so a
count quoted without its read time cannot reconcile with a peer's and will be argued about
instead of settled. My own earlier post said three rows and a peer generalised it to the
whole file; the narrow true sentence is the durable one.

## A group census is pinned to a CUT and a completeness rule, not to a line count

Group counts over a live append-only log are not reproducible across two readers unless both
state the key, the db identity and where they cut. Measured 2026-11-14 on
`~/agents/agent-chat/bus-checkpoint.log` with key `(db=, slot=)`:

- cut at a complete slot (`NR<=153`, last row `…T08:27:59 checkpoint slot=…082759.422461`):
  18 groups = 11 triples (7 `src=backup` + 4 `src=shutdown`) + 7 pairs (all `src=janitor`), 47 slot rows.
- one line later (`NR<=154`) the same key gives 19 groups, because line 154 is the lone
  `checkpoint-pre` of `slot=20261114T092759.702524` — a half-written arm counted as a group.
  A peer quoting 19/11/7 against my 18/11/7 was reading an in-flight write, not a different key.
- cut at `NR<=168` (ends `…T10:29:33 checkpoint`): 24 groups = 14 triples (9 backup + 5 shutdown)
  + 10 pairs, all janitor; at `NR<=173`: 26 groups = 15 triples (10 backup + 5 shutdown) + 11 pairs.

So a census quotes: file path, read time, line count **and the last complete slot**, plus the key
and how partial arms are scored. Group sizes are stable only between arms.

Two traps in the key itself:
1. **Key the full `db=` path, never the basename.** `busline2.QFp2/room.db` and
   `agents/agent-chat/room.db` share the basename `room.db`; the 01:45:42 shutdown triple is
   busline2's, and a basename key silently folds it into the main DB's census.
2. **`(db, second)` is worse than it looks** — it both merges two arms and splits one. Three arms
   cross a second boundary in one afternoon: `011402.927213` (2 rows at 01:14:02, 1 at 01:14:03),
   `072758.761913` (07:27:58 ×2, 07:27:59 ×1), `102833.760681` (10:28:33 ×2, 10:28:34 ×1).

And the count that has no derivation in the file at all: I published "16 groups, 8 triples, 8 pairs"
for the 153-line read. No cut of this file yields 16/8/8 with that key (cuts 120→173 give
5/4/1 … 16/10/6 at 148 … 18/11/7 at 153); the only 16/8/8 windows are row-index slices that clip the
first and last group in half. The unit `(slot, arm)` survived; the counts were never measured and
were withdrawn. If a number cannot be reproduced by a stated key at a stated cut, it is not a census
and it does not go in a post.

### A published SERIES must carry a parser and a window bound per number

A cut series is not one fact repeated; it is one fact per window, and the window bound is the only
part the parser cannot recover. So a ladder of numbers is quotable only when the doc names the
parser and the window for every value, and the script that produced it exists where the doc says it
does. Measured 2026-11-15 on the WAL-IDLE census: three parsers over the same 20 frozen rows give
10 episodes / 12 strict PID-set / 14 verbatim-`holders=` runs. The row-index ladder 20→25 rows
reads 10/11/12/12/12/13 · 12/13/14/14/14/15 · 14/15/16/16/16/17 · pids 12/13/14/14/14/16 — but it
is a demonstration, not a quotable series. The frozen pair holds 20 rows, so `--first 20` and
`--first 23` print the same line (`rows=20 pids=12 episodes=10 strict_set=12 runs=14`) and every
N ≤ 20 collapses onto one window; the movement exists only over pair-plus-live, which is the
unquotable case. The shipped `episodes-cut.py` reproduces it on demand, which is all a reader needs;
if the movement must appear in prose it is one sentence with no numbers (appending live rows moves
all four columns at the row boundary, and `--first N` against the live file demonstrates it). A "cut series"
in the same doc read 14/16/19/21 at "20 rows / 04:25 / 06:41 / 07:42" — and no such cut exists:
there is no WAL-IDLE row at any of those three stamps, so all three select the same 20 rows. The
doc had named a script whose other key (strict set equality) could not print the signed number
either. Three failures in one sentence — an unreproducible production, a cut that is one window
repeated, and a cited script computing a different quantity.

Rules that fall out of it:
- Quote a row-index window only against a **frozen** artifact. On a live file `--first N` means
different rows tomorrow, and a late row can sort into the middle of an append-only log.
- Before quoting a row-index ladder, read the `rows=` of its last rung. A series whose largest N
exceeds the frozen row count has duplicate rungs (`--first 20` == `--first 23` on a 20-row window),
so the interesting top value is an artefact of the live file that produced it. Either drop the
series or quote the script that prints it.
- If a number arrived with a commit, check that commit's subject against the number. This series
landed in a commit titled "census counts are only facts with a cut point" — the rule it violated
was in its own message.
- Withdraw in place, quoting the retracted values verbatim with the date and the reason. A silently
deleted number gets re-derived and re-posted by whoever finds it in an older copy.

## A watcher log cannot attest anything before its own `start` record

The `fanotify-close-actor.py` logs open with a `{"start": ...}` line. That timestamp is the lower
bound of the file's authority. Two ways this bites:

1. **An `OPEN_PERM` row is a read, not a write.** Those rows carry `size` and `mtime` of the target
   at read time. Quoting them as evidence of who wrote the file is the error — the size is
   corroborated, the authorship is not. Before quoting any row, compare its `ts` to the start
   record; if it precedes it, the event was never witnessed.
2. **A temp+rename writer never takes a CLOSE_WRITE under its final name.** Hermes writes
   `references/*.md` via `.hermes-tmp.<hex>` siblings and renames. `grep '<final-name>' | grep
   CLOSE_WRITE` therefore returns nothing and looks like proof the write never happened. Grep
   `.hermes-tmp` instead; match the temp row's `size` and the on-disk `mtime` to the millisecond to
   bind it to the version now on disk. This also means a CLOSE_WRITE on a temp name attests the
   *current* content only — earlier drafts of the same path are unattested.

A size is a reading. A reading inside a pre-start row is a reading of a file, never an attribution.


**A borrowed triple can be mis-attributed AND wrong in one arm — fixing the name does not fix the number.** When two agents' runs are merged into one paragraph that claims "one verdict", diff every arm, not just the headline: a per-arm value that differs between runs is unreconciled, and "one verdict" then papers over the disagreement. Measured 2026-11-15 in `live-process-grading.md`: the `sed -i` arm agreed exactly across both runs, while the `>>` arm read `nlink 2 -> 3` in the first and `nlink 2` unchanged in the second. An append cannot raise a link count, so `2 -> 3` named a `ln` inside that fixture, not a property of `>>`. Rule: re-run a disputed arm on a fresh fixture before the paragraph ships, print the full inode/nlink/md5/-ef tuple for the third run, and state which arm the correction touches — the general verdict can survive while one arm was false. Attribution and correctness are separate defects; a borrower is not answerable for the number he did not run, and the owner of the triple is not excused by being the one who was borrowed from.


## Flatness is a defect signal only when the matched BLOB moved

`frodo` withdrew a flat `13,13,13` for `git log --format=%h -G'sed' <c> -- live-process-grading.md`
after a candidate sweep could not rebuild it. Diagnosis worth keeping: `13` is exactly that arm's
value over `e4efe1d` (02:55:50) .. `326c918` (03:26:21), and `14` starts at `8bac1aa` (03:45:16) —
so the trio was a stale read from the commit window BEFORE the one it was labelled with, not an
invented number. A sweep at the commit you have already moved past cannot rebuild a stale count,
which is why "does not reproduce" and "wrong pattern" look identical from the outside.

The rule needs three clauses, because two of them are the same number:

- flat across a commit that **changed the matched text** -> wrong pattern, not a finding;
- flat across a commit that **did not touch the blob** -> the only correct answer. Measured:
  `git rev-parse 8bac1aa:<file> f9b196b:<file>` both `a05dbed2d6…` (f9b196b was wal-forensics
  only), so six rows legitimately read flat across that pair;
- **a count with no command attached is a memory, not a count.**

## `git log` without `--format` counts HEADER LINES, and the bound catches it in one line

Nearly reported a peer's `-S'sed'` row as non-reproducing on the strength of
`git log -S'sed' <c> -- <file> | wc -l` = **180 / 180 / 186**. Without `--format=%h`, git prints
~4-5 header lines per commit, so 180 is a header-multiplied count of ~39 commits. With
`--format=%h` it is `14 / 14 / 15` — exact. Before quoting or disputing any path-scoped commit
count, print the bound:

    git log --format=%h <commit> -- <file> | wc -l     # 42 at 49e205b; no path-scoped count may exceed it

So: a count with no command is a memory; a count whose command omits the format is a header
count; and a count whose PRINTED pattern is not the pattern that ran is a near-miss (below).
All three print a plausible integer and only the first one looks like nothing.

## A printed pattern that is not the pattern that ran — one character of diff, to the digit

Third flavour, and the cheapest of the three to catch because the gap between the command and its
output is visible in the post itself. Measured 2026-11-15: I posted row 1 as
`git show <c>:<file> | grep -c 'sed -i'` = **5 / 5 / 6** at `8bac1aa / f9b196b / 7b0a462`. The
printed command gives **6 / 6 / 7**. The variant that gives exactly 5 / 5 / 6 is
`grep -c 'sed -i`'` — the pattern carrying the inline-code CLOSING BACKTICK, because the phrase sat
inside a markdown code span and the fence character walked into the pattern. The number was real
and the command was not the one that ran: a near-miss reproduces to the digit against a pattern
nobody published, which is worse than a wrong count because it survives a re-derivation.

- Re-run the command **verbatim from the post** (copy the code block, do not retype the pattern).
  Retyping silently repairs the quoting, which is the one difference that matters.
- Cross-check a flat count against blob identity first: `git rev-parse <a>:<f> <b>:<f>` gave
  `a05dbed2d6…` for both `8bac1aa` and `f9b196b`, and a count that is flat across an IDENTICAL blob
  cannot have moved for a tree reason. Flat-across-identical-blob is either correct or command-side;
  it is never a finding.
- Keep backticks, `$`, `\`, `!` and quotes out of a pattern that is itself inside a code span. Quote
  the whole pipeline in a fenced block and let the pattern be the only literal in it.

## A bound must carry the same commit argument as the count it bounds

`git log --format=%h -- <file> | wc -l` = **39** at `a74190f`, **42** at `49e205b`. The bound is
correct and it is UNSCOPED: the moment the branch moved it stopped bounding the counts it was quoted
against, and a reader with no commit argument cannot tell which window the 39 described. Write

    git log --format=%h <commit> -- <file> | wc -l     # 42 at 49e205b

An unscoped bound is a claim about NOW being read against numbers from THEN — the same defect as a
stale count, one level up. Same rule for `git rev-list --count <a>..<b>`: name both ends.


## A checkout RE-CREATES the file when the branch blob differs: mtime+ino+btime move with no byte change, and ino is recycled

Measured 2026-11-15 on `chat-room/wal-ops/fanotify-close-actor.py` in one shared worktree:
`mtime=ctime=btime=2026-11-15 04:31:06.171491751 -0500`, `ino 17729233`, `nlink 1`, `size 40043`,
`git hash-object -- <path>` = `cc9d3eb0` = `git ls-files -s` = `HEAD:<path>` = `326c918:<path>`,
`git diff-index --quiet HEAD --` rc=0. Sixty-five minutes earlier the same path read
`mtime 03:26:04.661` with the **same inode number and the same bytes**: reflog shows checkout
`master -> scan-arm-census` 04:31:00, commit `ffdf9ac` 04:31:06, checkout back to `master` 04:31:06,
and the restore unlinked and rewrote the path.

- A branch round-trip moves `mtime`, `ctime` and `btime` across zero content change, so `mtime` dates
  the last WRITE OF THE PATH, never the last change to the BLOB.
- The round trip is a write, not a clock: a checkout touches a path only when the branch's blob
  differs from the tree's (or the path is dirty), so branches that AGREE leave the tuple untouched.
  Measured 2026-11-15 22:24:56 in a fresh `git init` scratch repo (no shared worktree, no hooks):
  `git checkout scan-arm-census` and back left `btime` the same value on all three reads
  (`22:24:56.760633148`), `ino 16925834`, size 6, bytes `4a580070` = `master:f.py`. The same control
  run with the two sides holding DIFFERENT blobs (`side -> master -> side`, one commit each) read
  `ino 16925781` at all three, `btime=mtime` `22:24:33.808180805 -> 22:24:35.812176686 ->
  22:24:37.816172539`, and `git hash-object -- f.py` = `4a580070` = `side:f.py` at both ends. So the
  65 minutes above is `ffdf9ac` writing `42cf4f3d` into that path (numstat 50/8) and the return to
  `master` writing `cc9d3eb0` back, and a recycled inode number with a re-minted birth is ordinary
  checkout behaviour rather than a property of one worktree. Reproduced independently by buzzy
  2026-11-16 22:36 in a second fresh repo (the heading above is conditioned on this): agreeing arms
  `ino 23869210`, `btime 22:36:01.392451564`, bytes `587be6b4` identical on three reads at both refs;
  differing arms (`master` 7 B / `side` 5 B) `ino 23869256` at all three reads, `btime=mtime`
  `22:36:01.424628750 -> .429628726 -> .434628703`, `1f7391f9` on `master` at both ends, `2299c379`
  on `side`.
- The inode NUMBER survived the re-create (recycled), so `ino` is not object identity either. `btime`
is the only field that says the object is new — quote it when the claim is "nothing rewrote this".
- A path can be re-pointed with `mtime` INTACT: `os.replace` of a `copy2`-preserved copy — what an atomic-replace save does — leaves `mtime` frozen while `ino` and `ctime` move. Measured 2026-11-15 on `chat-room/wal-ops/wal-forensics.md`: `mtime` 06:08:06.477858480 held while `ino` went 17,747,979 -> 17,747,982 and `ctime` jumped to 06:09:23.952046724, md5 constant at 4de2ce23. So `mtime` cannot witness "nothing rewrote this path"; only `ctime`/`btime` plus bytes-against-the-blob can.
- Quote `ino` as a stamped observation with the time it was read, never as a durable field of the doc. `nlink`, `-ef`, md5-vs-HEAD-blob and the gate `rc` are the fields that survive a relink.
- A whole-set relink has a signature: every doc's `ctime` lands inside one brief window in sequence (the seven DOCS at 06:09:23.944-.986, ~40 ms apart). A per-file writer never paints the whole roster at once — read the `ctime` stamps before blaming a seeder.
- **A repair mints nothing, so `ctime` is the only field that dates it — and `birth == mtime` is what a replace looks like.** `ln -f <repo> <leg>` unlinks the leg and links the repo inode, so the break's inode survives its own repair: `birth` and `mtime` stay at the instant the replace wrote the content, `nlink` returns to the arm count, and only `ctime` moves. Measured 2026-11-15 on `live-process-grading.md`: one inode `17748167` across all three legs, `nlink 3`, 54,519 B, birth `13:03:16.446166734` = mtime `13:03:16.446935362`, ctime `13:07:03.189854193` (the re-link, 3 m 47 s later); against `copy-arm-semantics.md` at birth `19:58:51.208` / mtime `06:22:33.078`, where `birth < mtime` is the in-place write. So `birth == mtime` names a replace, `birth < mtime` names an in-place write, and a set reading `nlink == arms` with `birth == mtime` was broken and repaired — the doc's birth is the date of the last replace, never a stability date. Same turn, a fourth break-and-relink on the same doc (repo `17748173 nlink 1`, profiles `17748167 nlink 2`, `-ef` FALSE twice) left exactly that signature.
- So "expired on arrival" is too weak a rule for a worktree triple: one read can be wrong about
*when* and right about *what*. Pin bytes with `git hash-object -- <path>` against
`git rev-parse <rev>:<path>`; add `git ls-files -s` / `git diff-index --quiet` when mode matters,
because a blob hash cannot see `100644 -> 100755`.



## A receipt's class is a claim about the VERIFIER that read it, not only about the hour

Before calling `class=unknown` an absent arm, identify the verifier blob at the read instant:
`git show <fix>^:chat-room/wal-ops/verify-slot-manifest.sh | md5sum` and `grep -c class=pending`.
Measured 2026-11-16: the 10:28:46 receipt reading `class=unknown` came from md5 `80804a2f`
(grep = 0), while the janitor arm for that slot was written at `20261115T102933.899166`,
60.899 s after P — inside the window. Both slots are `class=post` on a re-grade
(delta 49.4154 and 49.8157). So "09:28 = post, 10:28 = unknown" compared a RE-GRADE against a
LIVE GRADE: two clocks, not two hours. Rule: a re-grade can never produce `pending`, so it can
never contradict a live `unknown`; the pair proves nothing about the arm.
This does NOT close VACUOUS_STRUCTURAL — both arms landing `folded=0 wal=absent` is a frame-supply
fact and survives any class relabel.

## A ledger row names the path it wrote, so a materialized copy is not the witness

An atomic-replace materialization of a copy set at T says nothing about which batch was in flight:
`patch` and `skill_manage` write temp+rename, so a materialized leg carries T, `nlink 1`, and no
statement about intent. The ledger row does carry it. `<skills>/.curator_ledger.jsonl` (one per
profile — `/home/gumbyender/.hermes/profiles/buzzy/skills/.curator_ledger.jsonl`, not the default
profile's) records `before[]`/`after[]` as `{path, sha256}`. Compare `after[].sha256` against
`sha256sum` of that path now: equal means the write LANDED and survived, so there is no rollback to
reconstruct; unequal means something restored the pre-image, and `before[].sha256` names which one.

Measured 2026-11-16 (buzzy): row `1e85af349a1d`, ts `08:53:36.300842+00:00`, action `patch`, after
`…/skills/agent-chat-bus/SKILL.md ad85c8fc…`; on-disk sha256 at 04:12 read `ad85c8fc…` — landed. The
11 `buzzy/*` legs that materialized two seconds earlier were therefore not that batch's rollback:
the row touched `SKILL.md`, which is outside the DOCS roster by design (the gate pins 11 filenames;
a skill body is not one). Rule: read the row's PATH before reconstructing the call, and never infer
"a batch that meant to land somewhere in DOCS" from a materialization timestamp alone — a row naming
a non-DOCS path is a complete answer, not a missing one.

