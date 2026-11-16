> Carved 2026-11-16 from the single `provenance-quoting.md` (126,987 ch at `867423a`, unchanged
> at `b8deabc`) into
> three files: `counts-rates-and-carriers.md` (this part), `provenance-quoting.md`, `gate-and-verdict-quoting.md`. Cuts at the headings that were `:452` and
> `:779`. This file's body starts at line 8; a pre-carve address translates as
> `:452..778` -> `line = N - 452 + 8`; addresses after the carve are
> against the file that carries them.

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

