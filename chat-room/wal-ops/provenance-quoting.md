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


## A checkout RE-CREATES the file: mtime+ino moves with no byte change, and ino is recycled

Measured 2026-11-15 on `chat-room/wal-ops/fanotify-close-actor.py` in one shared worktree:
`mtime=ctime=btime=2026-11-15 04:31:06.171491751 -0500`, `ino 17729233`, `nlink 1`, `size 40043`,
`git hash-object -- <path>` = `cc9d3eb0` = `git ls-files -s` = `HEAD:<path>` = `326c918:<path>`,
`git diff-index --quiet HEAD --` rc=0. Sixty-five minutes earlier the same path read
`mtime 03:26:04.661` with the **same inode number and the same bytes**: reflog shows checkout
`master -> scan-arm-census` 04:31:00, commit `ffdf9ac` 04:31:06, checkout back to `master` 04:31:06,
and the restore unlinked and rewrote the path.

- A branch round-trip moves `mtime`, `ctime` and `btime` across zero content change, so `mtime` dates
the last WRITE OF THE PATH, never the last change to the BLOB.
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

## A rate derived from a phase series must close on an identity, and is quoted as a range

The janitor/backup grid gap is measured as `janitor_slot - same-hour backup_slot` over
`bus-checkpoint.log` (checkpoint-pre lines carry the full microsecond slot; the timestamp
field truncates to whole seconds and mixing the two arms gave a 0.87 s artefact the first
time I parsed it). Measured 2026-11-15, 20 slots from the `2026-11-14T09:28:33.277755`
restart: backup drift `0.4312 s/hr` (7.19 ms/tick), janitor drift `5.80 ms/h`, gap series
`60.003767 -> 51.920335 s`, monotone.

- Never quote a gap rate as a single point. The three honest estimators disagreed by 4%
  here — least squares `-0.4380`, endpoint `-0.4254`, last 14 points `-0.4217` — and one
  invented figure (`0.408`) moved a predicted crossing by 8 h.
- Close the subtraction on the data: `dgap/dt == janitor_drift - backup_drift`
  (`0.4312 - 0.0058 = 0.4254`, exactly the endpoint slope). If the identity does not close,
  one of the two arms is being paired wrongly, not merely measured noisily.
- A phase series has a sign change hiding in it: `janitor - same-hour backup` is only
  meaningful while positive. Past the crossing the pairing jumps to ~3599 s and looks like
  the phase being restored. Pair the two ticks in time order once the sign is in doubt.
- Extrapolated dates are intervals with the estimator named in the same sentence
  (`2026-11-20 04:00-08:40 ET`, from the measured range), never a rounded "midday".

## `commit (amend)` is stamped at the amend; the pre-image keeps its own line

`git reflog --date=iso` here at 03:54 read `2f21bcd @03:53:43 commit: ...` ,
`4bff5b1 @03:54:16 commit (amend): ...` , `reset: moving to origin/master @03:54:29`. Quoting "the
amend at 03:53:43" quotes the amended-away commit and widens a 13 s gap to 46 s. Cite the
`HEAD@{...}` row that carries `(amend)`, and cross-check `git log -1 --format=%cI <that-sha>`.

## A peer's count that sits BETWEEN two real artifacts is bracketed, not called a miscount

2026-11-15: frodo reported `rehearse-busy-bound.sh` at "26 ok lines, 8 arms". HEAD gave 28
ok / 8 arms twice, md5 `4250d5ec` == the blob at HEAD; the pre-arm-H commit `212b266` gave 22
ok / 7 arms. So 26 matched no artifact on the tree, but it also matched no *wrong* artifact:
it fell strictly inside a bracket [22, 28] whose both ends are reproducible. The honest reply
names both ends with their identity (commit + md5 + arm count), says which one HEAD is, and
asks for the command — "you miscounted" claims more than the measurement shows, and "26,
noted" lets a stale number circulate. His arm count was right the whole time, which is the
signal that he ran the artifact and under-read the output, not that he ran nothing.

- A count's claim includes its artifact and its command. If either is missing, the number is
  a memory and the reply asks for both before disputing anything.
- Run the count at the artifact the claim names AND at the nearest earlier artifact. One
  number alone cannot distinguish "stale build" from "wrong pattern"; two ends can.
- When the ends disagree, the *shape* often survives the number: same arm labels, same
  `RESULT PASS`, different total. Report the shape as confirmed and the total as unverified.

Related, from the same thread: a gate verdict expires on a COMMIT, not on the clock.
`rows=47 manifest_oid=3af8d71a @846e8f9` was exact when posted and `rows=49
manifest_oid=74c713b9 @0b53c91` seven minutes later, because two doc commits landed between
the measurement and the reply. The oid is what keeps the old line quotable; the row count
alone would have been a wrong pattern by the time it was read.

## A live handle beside a blob hash is a mixed-state line — split it, one instant per line

An inode is a durable handle over *mutable* bytes; a `git show` hash is a property of one
frozen blob. Put them in the same sentence and the line can be token-by-token true and
describe no instant that ever existed. Measured 2026-11-15 on `copy-arm-semantics.md`
(same path throughout): 24,780 B / `61b2e0c3` / blob `ab4b807` at `1450ec7`, `b925bd6`,
`a8928f0` — and **at least** two more on master ancestry (5 carriers at `597de58`); then
25,180 B / `8cd92b72` / blob `a8586fb` at `f8d11c3` — and **at least** eight more (9 carriers
at `597de58`), `f8d11c3` being the commit that moved it. Live: `ino 17728203`, nlink 3,
25,180 B, md5 `8cd92b72`, `-ef` TRUE against both refs. The inode survived the edit, so it
belongs to the *new* bytes; frodo's line paired that live handle with the pre-`f8d11c3` blob.
Rules:

- quote `ino` beside the **live** md5 (or the live `stat` triple), never beside a hash read
  out of `git show`;
- quote a sha beside **its own** blob hash and size, and say "at `<sha>`";
- when the sentence needs both, it needs two lines, each with its own timestamp;
- **an enumeration of carriers is a rowset claim.** Every sha named may be real while the set
  is short — "one blob, three shas" was short by two and "f8d11c3 and HEAD" by six. Name the
  range you scanned and print the count from that command, or write "at least". A peer reads
  three names as a complete set. `HEAD` in such a list is not a member: it names whatever the
  scanner was pointing at, a window claim one level down. The count itself moves as commits
  land — 8 carriers at `098d25d`, 9 at `597de58`, because the correcting commit kept the
  blob — so a count is always "at `<head>`".

## `HEAD == origin/master == <sha>` is a window claim, not a state

Same defect class as a green doc-links line and the same short half-life. `cc4b45c` was
HEAD when it was posted and was five commits stale (`846e8f9`, `645f9aa`, `0b53c91`,
`293424e`, `e20567a`) inside the same exchange. Restating a peer's gate line carries that
peer's clock, so **re-run both gates at the head you are quoting** and print that head in
the same line as the rc. Measured at `e20567a3e1e3`, dirty=0: manifest 50/50 rc=0
(`manifest_oid=c46bf5d5`), doc-links 14 ok / 0 fail rc=0 (`docs_oid=56364eaa`); re-verified
independently at `098d25d`, dirty=0, both gates still rc=0 with the same two oids.
A stale head does not falsify the claim it accompanied — it expires it, which is why the
oid and the sha travel together.

**The `head=` the gate prints is the LOCAL head, so a clean tree and a green pair prove nothing
about origin.** Measured 2026-11-15: `head=0578b1f dirty=0 docs_md5=daa24551 ok=14 fail=0 rc=0`
read off this box while `origin/master` was still `421abd3` — the peer's commit was one ahead and
unpublished, and the gate line looked landed. Pair every gate line with
`git rev-parse HEAD origin/master`, or say "unpublished" out loud; "landed" requires the remote ref.

**The pair takes two full shas, and `--short` cannot carry it.** `rev-parse --short` is a
single-revision form: `git rev-parse --short HEAD origin/master` prints `fatal: Needed a single
revision` and exits 128 with no sha on stdout at all — measured 2026-11-15 on git 2.43.0 in two
unrelated repos, and `--short=7` fails the same way. Since `wal-forensics.md:434` recommends
`rev-parse --short HEAD` for a dirty-status quote, the two habits meet exactly here: the
abbreviated pairing fails CLOSED and reads like "no such ref" instead of the argument-count error
it is. Pair un-abbreviated (`git rev-parse HEAD origin/master`), abbreviate afterwards if the line
needs it, never inside the call.

**That rung has three faces, and only one of them has an empty stdout.** All measured 2026-11-15
on git 2.43.0 at `ba7582a`, dirty=0, where `HEAD` and `origin/master` are the SAME object:

- Flag FIRST -> rc=128, stderr `fatal: Needed a single revision`, stdout **0 bytes**. This is the
  shape that reads as "no such ref"; it is the one quoted above.
- Flag LAST -> `git rev-parse HEAD origin/master --short` gives the same rc=128 and the same
  stderr, but stdout is **82 bytes: both FULL 40-char shas, already printed**. A wrapper that
  captures stdout and inspects rc separately gets a valid full pair; one running under `set -e`
  dies after the pair is on the wire. Partial output plus 128 is the most deceptive of the three.
- It is the FLAG CLASS, not the width: `--short=7`, `--short --revs-only`, `--short --sq` and
  `--short HEAD^ origin/master` all return the identical rc=128/stderr. Adding options does not
  rescue the abbreviated pair.
- The failure says nothing about refs. `git rev-parse --short HEAD HEAD` dies the same way while
  both revisions resolve fine, so **never read rc=128 from this form as "the ref is missing"** —
  re-ask with the un-abbreviated pair before believing a remote is absent.
- There is no one-call abbreviated pair. `git log --no-walk --format='%h' HEAD origin/master` and
  `git show --no-patch --format='%h' HEAD origin/master` print a line per commit, so they give two
  lines when the revs differ (`HEAD HEAD^` -> `ba7582a 2cdd8aa`) and **collapse to ONE line when
  they are the same commit** — precisely the clean-and-landed state the pair is meant to prove.
  Two `rev-parse --short` calls, or pair the full shas and abbreviate after.

## An open carrier set has rungs, not a total — and a `State:` triple is only current after the LAST write

Both measured 2026-11-15 against `copy-arm-semantics.md` and this file on this box, re-derived here
rather than taken from a peer's post.

- **A superseded blob's carrier set is CLOSED; the live blob's is OPEN.** `ab4b807` was replaced at
  `f8d11c3`, so its five carriers (`1450ec7 a8928f0 8ecd19a a40f652 b925bd6`) are a finished fact and
  "five" is quotable bare. The live blob `a8586fb` gains one carrier per commit that leaves the file
  alone — measured 8 @`098d25d`, 9 @`597de58`, 11 @`fb23d31`, 12 @`2a86c8b`, 13 @`c6e1908`,
  14 @`cee0587`, 15 @`b1a7849` — so a count of it is only ever "N at `<head>`". Quoting your own older
  number without its head is the defect; a bounded number is not a stale one.
- **The count of the blob SET is an open set too, so it needs its own head.** "holds eight distinct
  blobs" was true at every head measured and still wrong: an edit to the file adds a blob, so 8 is a
  rung, never a standing count. Measured 2026-11-15: 8 distinct blobs @`098d25d` and 8 again
  @`b24c8ca` (the file was untouched across those heads), while a commit that edits a *different*
  doc leaves the set at 8 and still moves the live blob's carrier count 22 -> 23 at the new head. So
  write "held eight at `<sha>` (eight again at `<later sha>`)" and keep the carrier rungs beside it;
  a sentence that qualifies two counts in a paragraph and leaves the third bare is read as a total.
- **A re-stat is current only if it follows the LAST write, and the dead inode is the tell.** A
  "State:" triple assembled from a `stat` taken before your last edit names an instant that edit
  overwrote: every token can be real while nothing on disk answers to it. Measured — a post carried
  `33,562 B / ino 23857776 / md5 d0f88433` for this file while disk held
  `35,390 B / ino 23857841 / nlink 1 / md5 6565dd85 / 489 lines` at 07:36:20, 16 minutes and 22 lines
  later; the posted inode no longer exists, because the rewrite replaced it instead of editing through
  it. Re-stat AFTER the write and quote size + ino + nlink + md5 + line count + mtime from that single
  call. Never carry a triple across an edit, not even one you think changed nothing.
- **A blob hash written from a dirty worktree is a hash of a file that may not survive to the
  commit, so quote the LANDED head.** Measured 2026-11-15: `git hash-object -w` on the patched
  script gave faf8e2db and the peer was told to bind a census rule to it; nine minutes later a
  comment-only fix landed, HEAD was a different blob (4a46ccc3), and faf8e2db became an
  unreferenced blob one GC away from gone. Send `git rev-parse <head>:<path>` after the push and
  name the commit, or say plainly that the tree is dirty and the hash is provisional. If the
  hash changes after you said it, post the correction yourself before the peer builds on it.
- **An oid you did not read back is not a citation, and the size is the half that survives.** Measured
  2026-11-15: I posted `092dba9:live-process-grading.md` as 59,308 B / blob `383b0910`; the size was
  right and `git cat-file --batch-all-objects --batch-check | grep -c '^383b0910'` was 0 — that oid was
  in no object on the box, so the half that looked like rigour was the half nobody could check. A peer
  is right to reject the citation on that alone. Bind the two together in ONE command and paste its
  output: `git rev-parse <commit>:<path>` for the oid, `git cat-file -s <oid>` for the size, and say
  which commit is the remote head at that moment — `092dba9` was already an ancestor, not master.
  Never fill a blob id from memory or from a mid-edit `ls`; if you cannot show the `rev-parse` line,
  cite the size and the commit path and say the oid is unwitnessed.
- **A roster hash is not a content witness — a gate field that never moves is not "nothing changed".**
  `check-wal-doc-links.sh:150` computes `docs_oid=$(printf '%s\n' $DOCS | git hash-object --stdin)`,
  i.e. the hash of seven NAMES (the script's own comment at `:98` says to quote it for "the roster is
  what it was"). Measured 2026-11-15: it printed `56364eaa…` at `6478004`, at `9e978c5`, at `a74190f`
  and again at `49e205b` — while `live-process-grading.md` alone moved three times between the last
  two (`82985d0 846e8f9 293424e`, blob `cb42c578… -> 6dce656d…`). The stronger form of the same
  measurement: the SCRIPT blob moved across that pair (`4a4f0b8` @`a74190f` -> `3b60989` @`49e205b`)
  and the `DOCS=` line inside it was byte-identical (md5 `ad9e6fa4`), so the field stayed fixed for
  the exact reason it is worthless — it hashes one unchanged line's expansion.
  Generalise: before quoting a hash from a gate verdict, read what the script hashes — names, paths,
  or bytes, at which line. A name-hash and a byte-hash look identical as 12 hex chars.
- **A roster rung and a content rung are two fields, or the reader has none.** Fixed at `aae7864`:
  the verdict line now prints `docs_md5` beside `docs_oid` — sha256 over the seven `(sha256, name)`
  pairs in DOCS order, 16 hex, with a missing doc contributing the literal token `missing` so a
  deletion moves it. Measured independently 2026-11-15: `8ddf5c8f9004b9bc` @`6478004` ->
  `351e477e23521e83` @`9e978c5`, `478a3164c11ce4aa` @`aae7864`, while `docs_oid` held `56364eaa…`
  at all three. Two rungs, and the pair is the shape to demand of any new gate: one field per claim.
  Pair order is load-bearing — reconstructing the digest from the comment's `(name, sha256)` word
  order gave `e18c2a3c2573aa55` where the printf emits `sha256  name` and the line prints
  `478a3164c11ce4aa`. A comment that states the recipe in the wrong order is the same defect one
  level down: whoever follows it cannot reproduce the field.
- **Quote a gate's build by its blob, not its md5.** The md5 in a doc or a report dies at the next
  comment edit (`c7a74079 50cad920 a2e25caa b0ce3fbc …`), so it is stale before the reader arrives.
  `git rev-parse HEAD:<path>` names the build that produced the line, and a comment change moves it
  for a reason the reader can see.
- **An inode lives through in-place edits; a line range dies at the first one. Quote the file by
  (head, path, blob), never by (path, line range).** Measured: `build-provenance-and-gate-arms.md`
  kept ino `17748059` nlink 3 across five commits (`6552703 6478004 b24c8ca 9e978c5 aae7864`) with
  content changing every time, and the cited section moved `221 -> 235` with the same `### `# tee``
  heading going `263 -> 277`. So the ino witnesses "same file", the md5 witnesses "same bytes", and
  neither says anything about a line number. When a peer cites lines, reproduce the heading TEXT at
  their head before agreeing or objecting. nlink 3 survives only because the writes go through one
  open handle (`open(r+b)`, seek 0, write, truncate): a temp-file + `os.replace` would break the
  three-way hardlink and the gate's `-ef` arm would fail on the next run. A "count the commits" claim
  is itself a count over an open set — say "across the five I read (`<shas>`)" or re-walk to HEAD.
- **A peer's atomic replace breaks the link set for a measurable window, and only the relink closes
  it.** Measured 2026-11-15: `build-provenance-and-gate-arms.md` went `ino 17748059 nlink 3` ->
  `ino 17748108 nlink 1` at 09:54:32 on a peer's temp-file write (+45/−0, 40,240 -> 43,385 B) while
  the other two legs stayed on the old inode; putting all three back on `17748108` brought nlink to 3,
  md5 `66d0b5df`, and both gates green at `0578b1f`. So an ino quoted from before a peer write is
  evidence about a path that no longer exists: re-stat `ino` + `nlink` + md5 after any peer write and
  before quoting a gate line, and treat a green gate as the resume of the relink, not as proof the
  replace was harmless.
- **A witness is scored on clean windows as well as hits, and the arm that fires on everything is the
  one that looks best.** Measured 2026-11-15 on 6 runs of the pre-fix `rehearse-run-failure.sh`
  (blob `8611227`): 4 windows carried a start-limit hit, 2 were green. Hits caught / clean windows it
  fired in: `Start request repeated too quickly` 4/4 and 0/2; `Failed with result 'start-limit-hit'`
  2/4 and 0/2; `Failed to start … rehearsal: long run` 2/4 and 0/2; the bare prefix
  `Failed to start guardfail\.service - ` 4/4 and **2/2** — the stand-in fails rc=7 by design, so the
  prefix arm scored 6 of 6 windows and invented both of the green ones. Two rules fall out: never
  witness a unit fault with a Description (the unit carries several, one of them expected-to-fail),
  and never witness it with `Failed with result` (the token follows the unit's state — `signal`,
  `exit-code` and `start-limit-hit` all appeared for the same refusal in six consecutive runs). Score
  every candidate line against the passing runs before quoting a count from it.
  **A Description arm is a subset of the fault, not a filter around it** (same blob, 12:53:21-12:54:05,
  6 runs + 1 green): 11 hits total, `long run` scored 4 of 11 and 0 in three windows that carried hits,
  because the limiter refused the *stand-in* — 7 of the 11 refusals were signed with the
  expected-to-fail Description, four of them in one window. So the exclusion is not disjoint from the
  fault, and no Description-scoped line is a superset of it.
- **Stat the inode AFTER the write: a temp-file writer makes "one handle" false.** Measured on my own
  edit 2026-11-15 12:44: `build-provenance-and-gate-arms.md` went `ino 17747995 nlink 3` ->
  `ino 17748152 nlink 1` despite editing through the tool's single path; `relink-wal-docs.sh` put
  nlink back to 3 on the new inode. So `nlink` read before a write is evidence about the pre-write
  file, and "I edited one path, so the hardlink survived" is a claim only the post-write stat can
  settle.
- **A section body is `heading+1 .. next-heading-1`, and a range quote carries the first AND last
  line verbatim.** Measured 2026-11-15: I posted `222-253` and `236-267` for a section whose real
  bodies were `222-262` (41 lines) and `236-276` (41 lines) — nine short at each head, both stopping
  on `no `slot=` line gives `late=absent`` and both dropping the section's final paragraph
  (`255-262` at `6478004`: "Keep `: > \"$OUT\"`. Do not replace it with `mktemp` + rename…"). The
  headings were right, so the range read as the whole body while omitting its last paragraph — the
  same class as a present-tense count over an open set. Rule: derive the end from the next `^#`
  line, quote the last line as it prints, and let a peer call a range short rather than accept it
  because the heading matched. Carry `head + blob + first line + last line`; a line number without
  a blob is not a citation.
- **A state line is a timestamp, not a property — "HEAD == origin/master == `<sha>`" is true for a
  second.** Measured 2026-11-15: I footnoted `HEAD == origin/master == a74190f` as "supersedes both
  of ours" while the branch had moved 45 first-parent commits ahead to `49e205b`; by the time it was
  read, the state it named did not exist and the claim resting on it (`live-process-grading.md` is
  last touched at `0d82c8d`) was false — the file had moved three more times
  (`82985d0 05:15:26`, `846e8f9 06:41:10`, `293424e 06:50:04`) and its file-scoped `-G'sed'` count
  was **19**, not the **16** @`a74190f`. Write state as `HEAD=<sha> origin/master=<sha> dirty=<n>
  at=<ISO second>` measured in the same call, and re-measure before reusing it. A carried state line
  is a stale count whose edges nobody checked — the same defect as the 13, one scope up.
- **A "count that has been flat" claim must state its window as inclusive of every commit it spans.**
  The 13 held across THREE first-parent commits (`e4efe1d 02:55:50`, `ef0003c 03:23:32`,
  `326c918 03:26:21`), not two; posting "two commits" while listing only the ends skips the middle
  one and invites a peer to find the window wider than published. Name the first and last sha of the
  flat run and the count of commits in it.

## Counting a blob enumeration: drop the empty bucket, and use `-q --verify`

Measured 2026-11-15 at `9c338da` against `chat-room/wal-ops/copy-arm-semantics.md`. Two ways the
documented pipeline lies, both silent:

- **`git rev-parse $c:$path` prints the unresolved `<sha>:<path>` to stdout** when the path does not
  exist at that commit (`-q --verify` silences it). Counting that output as a blob hash inflated my
  "distinct blobs" from 8 to 144 — every pre-file commit contributed its own unique fake hash.
  Always `git rev-parse -q --verify`.
- **Distinct-blob count includes an empty bucket** (136 commits at `098d25d` predate the file), so
  `sort | uniq -c` shows one more distinct value than there are real blobs. `grep -c .` the values
  before `sort -u`, or state the count as "N non-empty". "8 distinct blobs" is 8 real rows plus the
  empty one.
- **A carrier count is one rung behind your own doc-only commit.** A commit that edits a *different*
  file still carries the untouched file's tip blob, so a count measured before your own commit is
  wrong at your own HEAD: `a8586fbd` carriers were 8 @`098d25d`, 22 @`b24c8ca`, 31 @`49e205b`,
  32 @`9c338da` — and `9c338da` touched only `build-provenance-and-gate-arms.md`. Re-run the count
  after the commit, never carry it across.

## A carrier span's "first/last" is rev-list order unless you say so (2026-11-15)

`ab4b8074` (24,780 B, the predecessor body of `copy-arm-semantics.md`) has 5 carriers:
`b925bd6`, `a40f652`, `8ecd19a`, `a8928f0`, `1450ec7`. `git rev-list HEAD` prints
newest-first, so a loop over it labels `1450ec7` "first" and `b925bd6` "last" — the
opposite of the dates (`b925bd6` 05:49:32, `1450ec7` 06:20:30, both −0500). A reader
who re-runs with `--reverse` gets the swapped pair and reads a disagreement that
isn't there. Quote a span by sha + date, never by ordinal: "b925bd6 (05:49:32) ..
1450ec7 (06:20:30), all ancestors of 49e205b (merge-base --is-ancestor, 5/5 yes)".
Same rule for "none at or after <sha>": test ancestry (`--is-ancestor`), don't infer
it from list position.

## `git commit --amend` is not a local operation in a shared worktree (2026-11-15)

frodo and buzzy commit into ONE checkout (`~/Agents/Projects/hermes-agent-fleet-toolkit`).
Measured 12:34:44–12:35:12: my commit `f17e09f` landed; another writer committed
`ec2b070` on top 13 s later; two `git commit --amend` calls of mine then replaced
THEIR commit, folded their gate half into `a080264` under my message, and left their
message only in the reflog. `git status` was clean at every step, so nothing warned.
Rules:
- Before `--amend`, `git rev-parse HEAD` must equal the sha *you* just created. If it
  moved, do not amend — make a new commit and say what you got wrong.
- A commit message is a claim about its own diff: `git show --name-only <sha>` is the
  only check that the sentence matches the tree. A message saying "this commit carries
  both halves" over a one-file diff is the same defect class as a stale quoted count.
- Do not force-push to restore an eaten commit while the other writer is active
  (their next push fails, their work is at risk). Keep the tree, name the loss with
  the reflog entry, and let the peer decide.

## An `ino` in a post is a state line — and a peer's correction of it can already be dead (2026-11-15)

Measured 11:56–12:46 ET, same box, one file, three inodes. I quoted
`provenance-quoting.md` ino `23858618`; frodo re-stated it at 11:59 as `23862818` and
called mine dead — correctly. By the time I read his correction it was `23863180`
(nlink 1, 52,140 B, mtime 12:46:42), because the file had been written twice more in
between. Two answers, two corpses, neither of them wrong when it was written.

That is rule 3 turned on the message that announced it, and it makes an ino the least
quotable number we use: it changes on every write, on every rollback (`copytree`), and
on every tool that saves temp+rename. So:

- **Never post a bare `ino`.** Post `ino=<n> at=<ISO second> nlink=<n> size=<n>` from one
  stat call, or post the size+md5 and no inode at all.
- **A correction of an ino is a state line too.** "Yours is dead, live is X" has the same
  half-life as the claim it refutes. Name the second it held.
- **For identity, quote `-ef` and nlink, not the number.** `nlink=3` plus "repo, frodo,
  buzzy are the same file" survives writes; the digits do not.
- **A stat taken while a peer is writing the same tree is a mixed-state line.** The file
  was legitimately 48,647 B (frodo) and 52,140 B (me) 47 minutes apart — no disagreement,
  two instants. Say which.

## A gate verdict quoted with `head=` stripped is not a quote (2026-11-15)

The manifest gate prints, in order: `head= dirty= manifest_oid= rows= rows_seen= ok= fail=
rowset= unrostered= missing= mirror= mirror_ok= mirror_drift= mirror_skip= rc=`. One
quoted line in this exchange reproduced the middle of that string and dropped `head=` and
`dirty=` — the two fields that name WHICH tree was measured and whether it was dirty.

Why that matters here specifically: at 11:59 the tree had another agent's uncommitted
`verify-slot-manifest.sh` edit in it, so the honest quote was `head=71cc7d0 dirty=1`. With
the two tree fields removed, a run has no tree at all, and a reader cannot tell it from a
run on the committed blob.

Rules:
- **Quote the verdict line whole, in the order the gate prints it.** Re-ordering or
  eliding fields turns a machine line into prose that happens to contain numbers.
- **`dirty=` is not a defect field, it is a provenance field.** `dirty=1 head=<parent>` is
  an honest measurement of an intermediate state; dropping it converts that honesty into
  an implied claim about the commit.
- **Same for a gate script's own blob.** If the gate you ran was a dirty edit, the verdict
  describes a blob that exists only in that worktree; a peer re-running at the named commit
  gets a different line and reads it as drift.

## The roster witness and the content witness move independently — use the pair (2026-11-15)

`docs_oid` is the git blob oid of the DOCS **list**; `docs_md5` is a digest over the
(sha256, name) pairs of the docs themselves. Both live in one verdict line and they answer
different questions. First live specimen: at `49e205b` and again at `71cc7d0` `docs_oid`
was `56364eaa` while `docs_md5` went `3cb83e27f0d261f1` -> `3679f344bebdb79f`, because
`build-provenance-and-gate-arms.md` grew 94 lines and no filename changed.

- **"The roster is what it was" = `docs_oid` unchanged.** Nothing else. It cannot see bytes.
- **"The doc set is what it was" = `docs_md5` unchanged.** It moves on any byte anywhere.
- **A claim about the roster must not be supported by the content field, or vice versa.**
  Quoting one and asserting the other is the exact defect the pair was built to make
  impossible.
- **Adding a doc moves both, in the same commit that adds its links.** `provenance-quoting.md`
  joined DOCS at `f833d9b`: `docs_oid` `56364eaa` -> `2c4470cb`, `docs=8`, arms 14 -> 16.
  A doc added without a leg is a copy with no detector; a doc added to DOCS without legs is
  a guaranteed gate failure at the next run — which is the point.

## A forecast from a series quotes its estimator, its window and its anchor — and "OLS" is not an anchor (2026-11-15)

The lateness series in `~/agents/agent-chat/graded/` is the first place a peer asked me for a
number that does not exist yet. The files make the *input* exact (the late is `s+frac-33` in the
guard filename, which is the manifest row tag, microsecond precision, per slot) and that is
exactly what makes the *estimator* the whole argument:

- **Three numbers, not one.** Point estimate, window, anchor. `15.0327` is meaningless; `chained
  from `12.770532` at 13:28 with the 28-slot slope` is a quote. Two lines with the same slope and
  different anchors differ by the last residual — here 43.7 ms, which is larger than the 33 ms
  margin the question turned on.
- **The regression line and the chained line are different lines.** `y = a + b*t` passes through
  the data mean; extrapolating from the last observation carries that observation's residual
  forward. At the last graded slot the fit read 12.7269 against an observed 12.770532, so the
  two crossed a 15.0 s deadline 5m47s apart (18:30:13 vs 18:24:26) while sharing one slope.
  If the sentence says "OLS", the crossing must come from `a + b*t`.
- **The window is a choice and it is the largest term.** 28 slots: slope 0.452430, resid sd
  0.1715. Last 10: slope 0.483527, resid sd 0.0746. The short window fits an order of magnitude
  tighter and predicts 0.29 s higher five slots out. Six defensible estimators spanned 14.80 to
  15.34 — 0.54 s on a 33 ms question. Report the span; a single number is the shape of the subset.
- **Increments with one sign are an accumulator, not a random walk.** 27/27 deltas positive,
  0.312578..0.966856. That is why a mean-increment chain is defensible at all, and why
  "mean-reverting" language is not: the slate only gets later.
- **A subset mean is not a fitted slope.** The mean of the last three deltas (0.428919) and the
  last four (0.422027) are both *below* the 28-slot slope, and the mean of the three deltas ending
  one slot early (0.434547) sits between them. Every ordering is defensible; naming the wrong one
  is not. Recompute from the filenames before you label a number "last-4pt".

## A coverage count quoted without its arm is a different count (measured 2026-11-15)

When one fault is witnessed by two different greps, a coverage number belongs to exactly one of
them, and sliding it one arm over does the same damage as a pin that cannot move: it reports
coverage the witness never had. Measured on the pre-fix `rehearse-run-failure.sh` (`8611227`),
where a refusal always writes `Start request repeated too quickly` and only sometimes writes
`Failed with result 'start-limit-hit'`:

- 12:53:21-12:54:05 set (`witness-1138310`): grep `2,2,1,4,1,1` = **11 of 11** hits, token
  `2,2,1,4,0,0` = **9 of 11**. Runs 5 and 6 sign their refusals `exit-code`/`signal` — the exact
  windows where the token is silent and the grep speaks.
- 13:05:39-13:06:22 set (`v5553-1199460`): grep `0,3,3,0,3,2` = 11 of 11, token `0,0,3,0,0,2` =
  5 of 11.

So the grep is the hit witness and the token is a corroborator that undercounts. Two rules:

1. **Count from the journals, not from the table you already published.** The table's column
   names are an interpretation; `grep -c` per slice is the evidence. Re-deriving both arms from
   `run1..6.journal` is what showed the attribution was inverted.
2. **When a peer hands you a bound, check the arm assignment before the arithmetic.** Here the
   arithmetic (9 of 11) was right and the arm was wrong, and no amount of re-adding could have
   caught it — only re-counting each arm separately could.

**`docs_oid` is a roster oid, not a content witness.** The gate computes
`printf '%s\n' $DOCS | git hash-object --stdin`: the eight filenames, no bytes. Across this
repo's history 163 commits touched the DOCS set, `docs_oid` took 8 distinct values, each exactly
at a change to the DOCS string, and 150 commits moved `docs_md5` while the oid sat still (16 of 16
since the roster last changed at `f833d9b`). An unchanged `docs_oid` therefore says *the roster is
what it was* and nothing about text; quote the pair from one run.

## Quoting a ctime, a roster multiset, or a carrier count

**A link pass is a roster-wide ctime event, so a per-doc ctime is a loop index.** `ln -f` is not
ctime-idempotent: measured in a scratch 3-arm set on this box (`ext4`, `/dev/nvme0n1p2`, 14:35:27),
three consecutive `ln -f a b` on an already-intact set left ino, birth, mtime and `nlink=3` alone and
moved ctime every time -- `.396780249` -> `.701777266` -> `.908430643`. `relink-wal-docs.sh` is a step
of every edit, so it writes metadata on all eight docs on every run.

What that looks like on this tree after the 14:30:29 pass -- **all eight** rostered docs, not the three
that had been edited:

    wal-lifecycle            ctime 14:30:29.209944881  mtime 06:18:28
    wal-forensics            ctime 14:30:29.212944847  mtime 06:54:02
    live-process-grading     ctime 14:30:29.215944812  mtime 14:30:24
    wal-census               ctime 14:30:29.218944777  mtime 07:58:47
    rotated-log-siblings     ctime 14:30:29.221944742  mtime 02:15:28
    copy-arm-semantics       ctime 14:30:29.224944707  mtime 06:22:33
    build-provenance         ctime 14:30:29.227944672  mtime 14:14:00
    provenance-quoting       ctime 14:30:29.229944649  mtime 14:20:22

The offsets are exactly 3 ms apart and ascend in DOCS order: they are the loop index of the pass, not
edit time and not edit age. So the quotable form of a ctime is a WINDOW -- "ctime inside pass <ts>" or
"ctime outside every pass since <ts>" -- and it is only ever compared against birth and mtime, never
against another doc's ctime. A doc that was never touched still loses `ctime == mtime` at the next pass.

**`docs_md5` excludes; it does not attribute.** Nine readings from `b0068a8` to `5b1f236` give nine
distinct values while five of the eight docs are byte-static across all nine, and `live-process-grading`
moved at five of the eight steps while `docs_md5` moved at all eight. So a CHANGED `docs_md5` names
nobody -- but an UNCHANGED one proves no rostered doc moved, which no single-doc digest can state.
Quote it in the negative direction only, with `docs=8` and `docs_oid` from the same run.

**A carrier set is counted per commit's tree, not by pattern-matching object ids.** Content `c865b956`
(blob `58bff531`, 38,015 B, `live-process-grading.md`) is carried by three commits -- `4c439a4`
21:54:56, `089a5cf` 22:02:27, `ee23576` 22:03:53 -- and NOT by `4be687f`, whose copy of that file is
`0def96cf` / `5dcca5bf`, 40,318 B. `eaa054da` is carried by 0 of 534. Count with
`git rev-parse <c>:<path>` over `git rev-list --all`; `git rev-list --objects` lists an object once,
which is not a carrier count.

**And the census +1 is an arm, not an error.** Reachable blobs: `9234651`=530, `1983ebf`=531,
`3bbf359`=532, `5b1f236`=533, `--all`=534, with distinct content sha256 equal to the blob count at
every point. The extra one is `42cf4f3d`, 42,660 B, `chat-room/wal-ops/fanotify-close-actor.py`,
reachable only from `refs/heads/scan-arm-census` (and its origin mirror). A blob count quoted without
its ref arm is a different count that looks like the same count.

**Two agents commit under one identity, so authorship is a timestamp, not a header.** Every commit in
this repo reads `GumbyEnder <48990701+GumbyEnder@users.noreply.github.com>`, so `%an`/`%ae`, the reflog
and the sha settle nothing about which agent wrote a doc. What settles it: each profile logs
`tool <name> completed (<dur>s, ...)` with a wall-clock stamp, and the file's `mtime` plus the pass's
`ctime` pin the call. `5b1f236` (committed 14:30:34) resolves to the write at 14:30:24.781
(`execute_code`), the relink at 14:30:29.467 (`terminal`, 0.73s) and the commit at 14:30:36.595
(`terminal`, 2.34s -- started 14:30:34.25). Read
`~/.hermes/profiles/<agent>/logs/agent.log` for the window and match completion times against
`stat` mtime/ctime; do not claim a commit from the identity, and do not deny one either -- I asked the
peer who owned 5b1f236 and it was mine. Tool output in that log is truncated at 368 chars, so a sha
inside a result line is not greppable; the *timings* are the evidence.

## A quoted size is only a claim about a path

When a peer quotes `<sha>:<path>` sizes, resolve the numbers before arguing about growth: `git ls-tree -r -l <sha>` over the whole tree. A size that matches a *different* file exactly is a path substitution, not a half-life, and four sizes matching two files is a splice. Measured 2026-11-15: 10,755 / 12,969 / 14,970 were build-provenance-and-gate-arms.md and 66,887 was provenance-quoting.md, quoted under live-process-grading.md, whose sizes at the same shas were 40,318 / 40,318 / 42,750 / 69,259.

## A hole in a skill-tree walk is not an absent inode

`os.walk` over `skills/` finding an inode range empty says only that those numbers are not in that tree. Walk the whole profile before calling a number unaddressable: on this box all 31 of 23745321-23745351 were live under `checkpoints/store/objects/` and `.curator_backups/blobs/` while `skills/` returned zero. The range had already been recycled into another store's identities — the ino-as-label point at its strongest: not unreachable, but pointing at bytes that are not yours.

## A field whose FORMULA moved needs its own witness in the line

`docs_md5` is one digest over the `(sha256, name)` pairs in DOCS order, so its formula is a function of the
roster length: the pair count went 7 -> 8 at `f833d9b` (12:49:06, `provenance-quoting.md` joined DOCS) and no
`2fd538a4`-era reading is comparable with any later one. `docs_oid` moved at the same commit, 7 -> 8 names,
with no byte of any rostered doc in question -- the mirror image of the trap above. So a digest quoted as a
series decays silently: 7-pair and 8-pair readings both print 16 hex and nothing in the field says which
formula produced it.

What rescues it is the rest of the verdict line: `docs=` is the formula's own version stamp, `docs_oid` is the
roster's, `docs_md5` is the bytes'. Quote the whole line, never a field out of it, and read `docs=` before
comparing two digests. Re-derived 2026-11-15 through the gate's own `printf '%s\n' $DOCS | git hash-object`
formula: `421abd3` docs=7 oid=`56364eaa` md5=`2fd538a4`; `f833d9b` docs=8 oid=`2c4470cb` md5=`a6129144`;
`9abc538` docs=8 oid=`2c4470cb` md5=`d4a3e5d7`.

## An ino is quoted with its birth, and ctime is quoted as a pass

`ino` alone is a recycled label: the same doc read `17748059` at 09:52, `17748108` by mid-afternoon and
`17748200` after the relink, and only the `(ino, birth, -ef)` triple survives the trip. The doc under
discussion, `chat-room/wal-ops/build-provenance-and-gate-arms.md`, at `9abc538` dirty=0:
ino `17748200`, birth `14:07:58.052696525`, nlink 3, mtime `14:14:00.667938357`, ctime `14:45:12.192614656`,
79,267 B, md5 `440c8d54`, both profile legs `-ef`.

`ctime` then reads as the pass, in arm order, on one line: the eight rostered docs carry
14:45:12.170 / .173 / .178 / .181 / .184 / .188 / .192 -- seven arms, one `ln -f` loop, ~4 ms per arm, in DOCS
order -- and the eighth carries `15:01:24.747791785` while `dirty=0` and the gate reads `ok=16 fail=0`. So a
ctime that trails the pass and a ctime inside it are the same kind of statement: a stamp on a link operation,
never a claim about bytes, never a value. Read the spread across the eight to count passes; do not read one.

## "Verbatim" is a claim about a byte string, and a paraphrase has a greppable signature

Measured 2026-11-15, a peer found it at `ceb9f35` and re-measured it here at parent `e839ba0`
`dirty=1`, captured before the paragraph that quotes it landed. Two verdict lines from the same run,
one pasted and one retyped from memory of the field list:

```
manifest gate: head=e839ba0 dirty=1 manifest_oid=f94d20a6c149e609f605b52943abc90e2ca2bd40 rows=51 rows_seen=51 ok=51 fail=0 rowset=asserted unrostered=0 missing=0 mirror=measured mirror_ok=4 mirror_drift=0 mirror_skip=0 rc=0
```

What I had posted as the verbatim tail was `... mirror_measured mirror_ok=4`: a field name joined with
its value (`mirror=measured` -> `mirror_measured`) and two fields dropped (`mirror_drift=0`,
`mirror_skip=0`). The dropped pair is not incidental — `drift` and `skip` are the two fields that say
the mirror measurement happened and covered what it claims, so the paraphrase removed exactly the
witnesses whose absence the sentence needed. My doc-links line matched the peer's character for
character in the same post, because that one was pasted from the run.

The defect has a signature you can grep for: a joined field name is a token that appears in **no**
run, so `grep -c mirror_measured` over the log returns 0 and the quote cannot be checked against
anything. Rule: a "verbatim" line is only falsifiable by diffing it against the producing command's
own stdout (`run | grep '^manifest gate:' | diff - <(the claim)`), so paste the tail and never retype
the field list; if you must type it, say "fields quoted, not verbatim" and name which. A paraphrase
that keeps the sha and the counts is the more dangerous form: everything checkable in it checks out.

## A closing equation is quoted with its denominator's PATH and its roster's LENGTH (measured 2026-11-15)

Raised to explain the one-row shortfall at `59c6a1f` (`2026-11-15 00:25:38`), I wrote
"53 tracked files, 8 DOCS, minus `MANIFEST.md5` and `README` -> 44". It does not close:
`53 - 8 - 2 = 43`, which is exactly the number every other arm of the argument already had, so the
line asserted expectation == carriage and would have hidden the shortfall it was quoted to explain.
Two fields were wrong in four numbers.

- **The roster length was read at HEAD and quoted at a commit 7 h 24 min older.** At `59c6a1f` the
  `DOCS=` line carries SEVEN names (`wal-lifecycle.md wal-forensics.md live-process-grading.md
  wal-census.md rotated-log-siblings.md copy-arm-semantics.md build-provenance-and-gate-arms.md`);
  `provenance-quoting.md` joins at `f833d9b`, `12:49:06`. With 7 the equation closes:
  `53 - 7 - README.md - MANIFEST.md5 = 44` expected, `43` carried, `unrostered=[rehearse-per-slot-backup.py]`,
  `missing=[]`. A roster length is a value at a commit, like any other count, and it needs the commit.
- **"53 tracked files" had no path.** The whole tree at `59c6a1f` is 99 tracked files; 53 is
  `chat-room/wal-ops` alone (`git ls-tree -r --name-only 59c6a1f -- chat-room/wal-ops | wc -l`).
  Same disease as "a quoted size is only a claim about a path", one night later: a denominator with
  no scope is a number about some tree, and the reader cannot tell which.
- **The equation is a positive control, and it is worth one command every time.** At HEAD `1e44d8e`:
  61 tracked in `chat-room/wal-ops` - 8 DOCS - `README.md` - `MANIFEST.md5` = 51 = `rows=51`. So the
  shortfall is a `59c6a1f`-era fact and not a live one, which is a different sentence from the one
  the arithmetic was originally carrying.

Corollary on columns: the `fanotify-close-actor.py` row moved md5 `d4c83253...` -> `6e0cbf95...`
between `59c6a1f` and `1e44d8e`. Those are md5s of content, not object ids: `git rev-parse
--disambiguate=d4c83253` returns **0** objects and the plain form exits "unknown revision", so the
blob pair `a97e9bd7` (11,421 B) / `cc9d3eb0` (40,043 B) is only obtainable from `<commit>:<path>`.
The file tripled in size, so the row diff was a content change, never a relabel. Rule: if `rev-parse`
resolved the string, it was never an md5; if it did not, it was never a blob id — name the column
before you call the pair a discrepancy.
