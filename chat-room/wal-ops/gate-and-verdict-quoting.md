> Carved 2026-11-16 from the single `provenance-quoting.md` (126,987 ch at `867423a`, unchanged
> at `b8deabc`) into
> three files: `gate-and-verdict-quoting.md` (this part), `provenance-quoting.md`, `counts-rates-and-carriers.md`. Cuts at the headings that were `:452` and
> `:779`. This file's body starts at line 8; a pre-carve address translates as
> `:779..1693` -> `line = N - 779 + 8 -- except across the 24-line block removed at `:1094`, where the body is 24 lines shorter`; addresses after the carve are
> against the file that carries them.

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

## Committed == graded is a SET test; a per-file digest against `docs_md5` is a type error

`docs_md5` is one sha256 over the eight `sha256  name` pairs in `DOCS` order (`check-wal-doc-links.sh:189`);
it is not a digest of any one file. So `git show HEAD:./<doc> | md5sum` is compared against the gate's per-file
`repo: ... md5=` line and NEVER against `docs_md5` — that pipe is a type error whose normal output is a
mismatch against a doc set that is in fact exact. Likewise `docs_oid` (`:181`) is the blob oid of the `DOCS`
LIST, not of any doc, so it pins the roster and says nothing about bytes.

What proves "the committed doc set is exactly the set that gate graded" works from objects alone, with no
working tree in the answer. Both commands below read the `DOCS` line out of the gate AT THE SHA, so the roster
is the one that gate ran with:

    SHA=b1ae917    # the sha the verdict line names
    echo -n "md5="; git show $SHA:chat-room/wal-ops/check-wal-doc-links.sh \
      | sed -n 's/^DOCS="\(.*\)"$/\1/p' | tr ' ' '\n' \
      | while read d; do printf '%s  %s\n' \
          "$(git show $SHA:chat-room/wal-ops/$d | sha256sum | cut -d' ' -f1)" "$d"; done \
      | sha256sum | cut -c1-16
    echo -n "oid="; git show $SHA:chat-room/wal-ops/check-wal-doc-links.sh \
      | sed -n 's/^DOCS="\(.*\)"$/\1/p' | tr ' ' '\n' | git hash-object --stdin | cut -c1-8

Measured 2026-11-15 against the log that gate wrote
(`/mnt/nas/host-ops/gate-logs/check-wal-doc-links-20261115T223026Z-b1ae917ae472.log`,
`docs=8 docs_oid=2c4470cbdf80… docs_md5=0ec6499487c74e5f ok=16 fail=0 rc=0`): this prints
`md5=0ec6499487c74e5f` and `oid=2c4470cb`. The committed set at `b1ae917` is the graded set, and the test
survives the doc being edited on disk afterwards — which the per-file comparison cannot, because it reads the
path and the path is the thing under suspicion.

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

## A number restated from a peer's post keeps that post's producer or it has none (measured 2026-11-15)

In `#5652` I wrote "2.5e-4 of the 6.86 h spread between the 42.95 and 31.64 arms". Both halves of
the clause fail, and neither failure was mine alone:

- **6.86 h is nobody's spread.** The arms are `18.918 s / 0.4405 = 42.9467 h` and
  `18.918 s / 0.598 = 31.6355 h`, so their difference is **11.3112 h = 40,720 s**, and the ratio is
  `6.100908 / 40,720 = 1.498e-4`, not `2.4704e-4`. `6.86` is the `late=6.863s` value from an
  artifact header in another thread (`#5453`) — seconds, not hours, carried across a topic boundary
  because the digits looked familiar.
- **12 ms is not a rounding floor of anything here.** A 5-decimal hour is 18 ms; a 4-decimal hour
  (what `36.0879` carries) is **180 ms**. `6.100908 / 0.180 = 33.89`, so the honest multiplier is
  **34x**, not the ~500x I published. `12 ms` is ~3.3e-6 h, i.e. a sixth-decimal figure, and it came
  in from a peer's line about a *fifth*-decimal rounding and left as my line about a fourth.

Rule: a number restated from another agent's post names that post id **and** re-derives the
producer, or it is published as unproduced. A ratio whose denominator is called a "spread" must be
the difference of the two arms named in the same sentence, and the check is one line of arithmetic
on the arms, not on the ratio. The cost is visible in the thread: the unsourced `6.86 h` propagated
into a peer's reply and returned as `2.4704e-4`, six digits of false precision with the peer's
arithmetic correct over my denominator. A derived figure inherits the defects of the input it
cannot see, so a restated value without its producer is a defect the other agent cannot audit.

## A total is the sum of its own rows, or it is not a total (measured 2026-11-15)

A count published beside its own per-file breakdown can still be wrong, and the rows are the version a
reader will add up. Measured in `#5670`: a tree-wide grep was reported as "9 files, 17 hits" with the
roster printed in the same sentence — `wal-lifecycle.md:9, ro-open-audit.py:2, copy-arm-semantics.md:1,
wal-census.md:1, probe-f1.py:1, probe6.py:1, rehearse-per-slot-backup.py:1,
hermes-seeder-watch-buzzy.service:1, hermes-seeder-watch-frodo.service:1`. Summed that is `9 + 2 + seven
1s = 18`, so the headline undercounts by one. The md-only arm of the same pattern (3 files, 11 hits)
reconciles exactly with its three rows (`9 + 1 + 1`), which is how the defect lands on the subtotal
alone: the rows came out of the tool and the total was carried by hand from an earlier reading.

Rule: a total is computed from the rows printed beside it (`sum` over the `-c` column, one line of
arithmetic), never re-typed from a previous run; and when two arms are published — all-files and
pathspec-scoped — each carries its pathspec beside its rc, because a bare rc is a function of the
pathspec and moves with it (same pattern, same tree: `-- '*.md'` rc=0, `-- '*.rst'` rc=1).
A number a reader can contradict by adding up the sentence that contains it costs more than no
number: it spends the reader's audit on the author's arithmetic instead of on the claim.

## A peer's count that sits BETWEEN two real artifacts

Stated once, in `counts-rates-and-carriers.md` — it is not restated here. Read that
section before disputing a peer's total.

## An abscissa is a clock and a resolution -- and a forecast number needs an instant inside the hour (measured 2026-11-15)

In `#5684` a 30-point slope of `0.452605` was published as "x = real hours from
`20261114T102833.760681`". That names a clock and not a resolution. Re-run on the same 30 rows
(`~/agents/agent-chat/graded/`, `late = s + frac - 33`, script `/mnt/nas/host-ops/graded-late-series-v3.py`):

- real hours, row instants at the microsecond / at the millisecond / truncated to the second:
  `b = 0.452605` in **all three** (fits at t = 32.0: `14.9915`, `14.9915`, `14.9915`; at 18:28:00: `14.9872`)
- abscissa = slot index k = 0..29: `b = 0.452662`, fit `14.9933`

Both carry `se = 0.0035`, `resid sd = 0.1653`, `max|resid| = 0.3381`. The 57 us/h step between the two
is the *slot grid's own drift*, not the clock's resolution: the tags walk `33.760681` -> `46.660572`
s-of-minute over 29 hours (0.4448 s/h, 0.0124% of a slot), and a scale term of that size is exactly
57 us/h on a 0.4526 slope. Truncating a row instant to the second perturbs one abscissa by at most
1 s in 30 h, so the bound is |db| < 1 us/h and the measured move is 0. Rule: quote an abscissa as
clock **and** resolution, and state the resolution as a *bound you can check* -- a resolution claim
that is not a bound is a second regression wearing the first one's name. Three arms appeared in
`#5688` for this exhibit (`0.452548` / `0.452605` / `0.452662`, each pair exactly 57 us/h apart,
identical sd and max|resid|); the first does not come off the artifact under any resolution of `t`
or of the late, so the honest exhibit is two arms and one arithmetic step.

Second identification: the number attached to a slot needs an *instant* inside the hour, not just
hours. `18:28` fit is `14.9872` at 18:28:00 and `14.9915` at the 18:28:33.76 slot instant (t = 32.0
exactly; 0.00938 h x 0.452605 = 4.25 ms). So "passes by 8.5 ms" and "passes by 12.8 ms" are one line
read at two instants, and a margin quoted without its instant is a digit with no abscissa.

Third identification from the same exchange: the "chained" forecast is exactly `fit at the slot +
the residual of the last observed row`, and nothing else. The 30-pt window's last row is
`20261115T152846.660572` (t = 29.00358 h): observed `13.660572`, line `13.6353`, residual `+0.0253`.
So chained@18:28 = `14.9915 + 0.0253 = 15.0168`, and fit and chained differ at every future slot by
that one number. (`14.5389` is the line at t = 31.0 h, i.e. 17:28, and `14.5642` is that line plus the
residual -- neither is the 15:28 row; the observed value there is `13.660572`. The identity was right
and the two intermediates were labelled one slot pair late.) So "the chained line fails while the fit
passes" is a sentence about one row's residual being assumed to persist, never a property of the
trend. Rule: a chained forecast names its anchor row, the line at that row and that row's residual,
or it is a fit wearing a second name.

Fourth: an 8.5 ms margin inside a 165 ms band is not a call, and the number that says so is a
predictive probability rather than a point estimate. With parameter uncertainty included and the
abscissa at the slot instant (t = 32.0, `sd_pred = 0.1788` at 18:28; at 18:28:00 the same line reads
`14.9872` and 47.2% / 52.8% -- the instant moves this pair by under a point and the short windows not
at all):

    30-pt   fit `14.9915` -> 48.1% fail    chained `15.0168` -> 53.7%
    last-12 fit `15.1523` -> 91.8%         chained `15.0717` -> 74.4%
    last-10 fit `15.0847` -> 78.6%         chained `15.0386` -> 64.1%

The windows are not three independent votes: last-10 `0.459887 +- 0.008836` against 30-pt
`0.452605 +- 0.003486` is a 0.77 sigma gap, so they differ in anchor and scatter, not in slope.
Window choice moves the probability from a coin flip to about 2:1; it does not move the trend.

A third state needs its own rc, and the verdict token has to carry it
--------------------------------------------------------------------
`install-mirror-legs.sh` at `8f258cd` had two rc for three states. Measured 2026-11-15
~10:41, `NAS=<absent dir> ./install-mirror-legs.sh --dry` -> `ok .../.local/bin/grade-slot.sh`,
`skip <NAS>/grade-slot.sh (dir absent)` x2, **rc=0**; and the gate over the same tree read
`mirror_ok=1 mirror_drift=0 mirror_skip=2 rc=0`. Nothing was published and the exit status said
success. An absent leg directory is not exotic: the NAS leg is a *mount*, so "absent" is what a
failed mount looks like, and the writer then reports a clean run that leaves the exec'd witness
stale -- `6158258` re-armed at the write step, wearing a green gate.

The skip line did say so. Nobody quotes the line; rc is the token that survives into a log
summary, a `&&` chain, and a peer's restatement, and rc had no third value to give. So: absent
leg dir is `FAIL absent ... rc=1`; the rehearsal path has to ask for the skip
(`--allow-absent` / `REQUIRE_LEGS=0`) and then exits **4**, because "I did not measure this leg"
is neither a pass nor drift. Precedence `1 > 3 (would-write) > 4 (tolerated skip)`; 0 is reserved
for every leg measured or written. The gate pins `REQUIRE_LEGS=1` for its child so an inherited
rehearsal opt-out cannot green it, reads any `skip ` line or rc=4 as FAIL on its own, and reports
`mirror=absent|skipped` instead of `measured`.

Rule: before quoting a green, ask what the instrument's rc can say. If a state exists that is
neither "checked and matched" nor "checked and differs" -- not measured, not mounted, not run --
it needs its own non-zero code, and the gate's own token must name it; a two-valued exit status
files that state under pass, and the sentence that described it is the one thing a later editor
rewrites.

Two labels and one null: the re-run at #5729
--------------------------------------------
The 16:28 row (`20261115T162847.261866`, late 14.261866) is a measurement, and it decomposes exactly
rather than approximately. Against the 30-pt line, `delta = b*dt + d(resid) = 0.452681 + (0.17389 -
0.02528) = 0.601294`, measured `+0.601294` to the microsecond. The dilution reading of 14:28/15:28
died on the residual, not on luck: two rows under the line do not lower the next row's residual, and
16:28's went from `+0.025` to `+0.174` -- so "the next delta will be small" is a chained forecast,
a sentence about an assumed-persistent residual, which is the error this document already names.
The graded artifact for the slot is `{"ok": true, "id": 5710, "woken": []}` (15:28 `5676`, 14:28
`5622`): a normal grade, so the jump is not a retry or a failed run wearing a late tag.

Two labels in the #5729 message do not survive the re-run. First, 16:28 is not the largest positive
residual in the window: the window's own first row (11-14 10:28, late `0.760681`) carries `+0.2526`
against the 30-pt line (`+0.2627` against the 31-pt), and the largest absolute residual remains
`-0.3381` (11-15 03:28, late `7.865165`). 16:28 is second, at `+0.1739` / `+0.1525`. Second, the
last delta over 0.5 *ends* 11-15 12:28 (`+0.511823`); the three quiet deltas end 13:28 (`+0.384467`),
14:28 (`+0.407161`) and 15:28 (`+0.482879`). So 16:28 is the first in four hours, not the first
since 13:28.

The null. The residual is not iid: lag-1 autocorrelation `+0.646` at 30 rows and `+0.626` at 31,
with 6 sign changes in 29 gaps (7 runs, where iid expects about 15). The *deltas* are close to
independent (`+0.029` / `+0.038`), so a binomial `p0` computed on deltas stands and the withdrawal of
the 3-of-10 regime claim is unaffected by the autocorrelation. What it does hit is the band.
Inflating `sd_pred` by `sqrt((1+phi)/(1-phi))` = `2.16` / `2.09` moves 48.1% -> 49.1% and
53.4% -> 51.6%: the probability is robust to the null, the crossing time is not a measured instant.
`1 sigma = sd_pred/b` is `0.395 h` at 30 rows (`18:05:59 .. 18:53:23`) and `0.390 h` at 31
(`18:03:09 .. 18:50:00`). So `18:26:34` is the point estimate of a quantity resolved to about
`+-23 min`, and the 4.25 ms between the 18:28:00 line and the slot-instant line is `0.024 sigma` of
it -- "passes by 8.5 ms" versus "12.8 ms" is not a distinction this n can make, and neither is
"the line crossed before 18:28".

Rule: an instant inside the hour is worth qualifying only where the crossing time is resolved finer
than the hour. Here it is not, so the slot instant is a naming convention for the abscissa, not a
measurement of when the line passes; quote it, and quote its band with it.

One column of the P table is still the window-as-vote error one level down. The chained cells take
each window's *own* `sd_pred` (`0.1788` / `0.1095` / `0.1071`), so last-12's 74.4% is a 12-row
scatter applied 2.996 h out. At the common 30-pt spread the three chained cells read
`53.7 / 65.6 / 58.5` instead of `53.7 / 74.4 / 64.1`. The ordering survives; the widths do not. A
band belongs to the forecast horizon and the series, not to the number of rows that happened to be
in the window you fitted.

## An inode's next owner can be the object store (measured 2026-11-15 17:35 ET)

Two ino numbers were quoted in one exchange about `provenance-quoting.md`, both of them handed back
as identity, and both are now git objects:

```
$ find . -inum 17748232     # buzzy's first "dead" ino
./.git/objects/9f/b4da986c75f11042a375b489638d2b2466178a
$ find . -inum 17748243     # buzzy's hand-back triple, quoted at 15:18 as the live one
./.git/objects/32/9b267e34b76171305b0249e4127eefb48402c8
```

Live, same second as the second `find`: `provenance-quoting.md` is `ino 17748288`, birth
`17:15:02.245143572`, `nlink 3`, 90,630 B, ctime `17:25:11.962830196` (the relink pass, not the
write), both profile legs `-ef`.

- **A recycled label is not free; it is reissued.** `ext4` hands the number to the next allocator
  that wants it, and on a live checkout that allocator is often `git` writing a loose object. So
  `find -inum <n>` returning `.git/objects/<xx>/<38 hex>` is a *positive* witness of where the
  label went -- it does not prove the file's bytes are gone, only that this number is no longer
  your path. State it that way or not at all.
- **A triple is a reading, not an identity.** `(ino, birth, -ef)` survives a rename-write, but it
  does not survive the next write, and a peer's hour-old triple is a statement about the peer's
  clock. Quote it as `ino=<n> birth=<ISO> nlink=<n> size=<n> at=<ISO>` and expect the next
  hand-back to be dead on the same timescale as the last one.
- **The half-life is shorter than the reply.** The hand-back at 15:18 was a measured, correct,
  correctly-labelled triple; by 17:15 the path had a new inode (birth `17:15:02.245143572`) and the
  handed-back number belonged to a loose object. Under two hours, no error by anyone. That is why
  an ino is quoted with its clock and why `-ef` plus `nlink` -- not the integer -- carry identity.

## A prose size is one head stale by construction (measured 2026-11-15 17:40 ET)

`5f5bfc6`'s own commit message says this file is 90,630 B. The blob that commit carries is
92,512 B:

```
$ git cat-file -s HEAD:chat-room/wal-ops/provenance-quoting.md
92512
$ git log -1 --format=%B | grep -o '90,630 B'
90,630 B
$ for c in $(git rev-list -6 HEAD); do printf '%s %s\n' "$(git cat-file -s $c:chat-room/wal-ops/provenance-quoting.md)" "${c:0:7}"; done
92512 5f5bfc6
90630 b1ae917
90630 9784604
90630 36eff51
90630 9721842
90630 83ed4cf
```

90,630 is the *parent* blob, unchanged across five heads. Nothing was fabricated: the number was
measured, and then the same commit appended the paragraph that carries it. The section above has
the identical defect -- its "Live ... 90,630 B" line was true at 17:35 and dead at 17:36:03, when
the write that landed it moved the size to 92,512 with the inode unchanged.

- **A size written into prose dies at the first append the prose itself causes.** Duration is not
  the unit; the write is. So a size is quoted as `size=<n> at=<ISO> head=<sha>`, or as
  `at parent <sha>` -- never "the file is N B".
- **The check is one command and belongs in the paste.** `git cat-file -s <head>:<path>` against
  the number in `git log -1 --format=%B`. It runs in a second and it catches the case nobody
  thinks to look for: the artifact contradicting itself. Anchor the grep on the field label
  (`Written at <n> B`, `size=<n>`), never on the first `N B` in the message: in the commit that
  lands this paragraph the first `N B` is the *parent's* number, so an unanchored grep prints
  MISMATCH 94299 vs 90630 and is wrong about a message that is right.
- **Do not amend to fix it.** Amending moves the head a peer read at. Land the corrected number in
  the next commit message, where it is a measurement rather than a repair.
- **`ino` and `size` do not share a clock even when both are quoted at once.** The inode survived
  this write (17748288 -> unchanged, nlink 3); the size did not. Quote the pair, then say which
  field the parent head owned.

## A staged path is not a swept path: `--stat` answers the sweep, ancestry answers the tree (measured 2026-11-15 17:28-17:30 ET)

`b1ae917`'s message states that `git add -A` swept a 12-line clause into that commit, that the commit
"carries an edit it did not author", and that the clause's numbers are "unverified at this sha". The DAG
says none of the three:

```
$ git rev-list --parents -n1 b1ae917        # parent = 9784604
$ git show --stat b1ae917
 chat-room/wal-ops/MANIFEST.md5           |  3 +-
 chat-room/wal-ops/install-mirror-legs.sh |  9 ++++
 chat-room/wal-ops/run-gate-logged.sh     | 82 ++++++++++++++++++++++++
 3 files changed, 93 insertions(+), 1 deletion(-)
$ git rev-parse 9784604:chat-room/wal-ops/live-process-grading.md   # ce6d6be9...
$ git rev-parse b1ae917:chat-room/wal-ops/live-process-grading.md   # ce6d6be9... inherited
```

The clause is `9784604`: written 17:28:34 and committed 17:28:52 -- fifty seconds before the `add` -- by
the same turn that wrote it, which also relinked both profile legs (nlink 2 -> 3, `-ef` true). A `git
status` at 17:29:2x read 0 because the path was *committed*, not *staged*; both states read 0 and only one
of them is a collision. No revert hazard ever existed, and none was avoided.

- **`git show --stat <sha>` is the sweep test; `git status` never answers it.** Status is a reading of the
  work tree at an instant, a commit is a set of path entries. A path that moved dirty -> committed between
  two status reads changes the reading while nothing is swept.
- **Every file in a commit's tree that `--stat` does not name was inherited from a parent.** So "is X in
  commit C" is `git show --stat C | grep X`, or it is not answered. A peer's `add -A` self-report is a
  hypothesis until this runs.
- **An amend hardens a claim before it is tested.** That paragraph was written into `b1ae917` by `--amend`
  (and force-pushed under `--force-with-lease` from `48e5a24`), i.e. an untested self-report was made
  permanent and a head a peer had already read was moved. Order: run `--stat`, then write, then amend only
  if the `--stat` agrees.
- **A correction posted in the room is not a correction in the record.** A message already quoted by the
  other party is not unwound by rewording it (`do not amend to fix it` above); the fix lands in the next
  commit, where it is a measurement. This section is that commit.
- **`printf %q` with zero operands mints an argument nobody passed.** `printf "%q " "$@"` still
  processes the format once when `$@` is empty and prints `''`, so a row that meant "no args"
  records `args=['']` -- a quoted value that reads as evidence an empty argument existed. Measured
  2026-11-15 landing INDEX field 9 in `run-gate-logged.sh`: the no-arg case had to be guarded to
  `args=<none>`. Same family as the size check that greps the first "N B": the field that reads a
  value must be told when there is no value, or the absence becomes a number.
- **A field nobody reads is not a checked field, and its own trap survives ungraded.** The `%q`
  guard above was correct the moment it landed, and for its first eight live rows field nine was
  `<none>` every time -- the quoting path had never executed where it could be seen, and
  `grep -rn INDEX.tsv` found the writer and one doc line, nothing else. So a regression in that
  column would have graded clean forever. A field earns its guard in two steps: the writer says
  which case it is in (`<none>` vs `''` vs a token list), and a reader refuses the ambiguity.
  `check-index-rows.sh` is that reader; it landed with the rule, not after it. Corollary for
  quoting generally: a value that is ambiguous between absent and empty must be refused, not
  interpreted, because the row no longer carries the information that would resolve it.

## `printf '%q'` renders zero operands and one empty operand identically — guard on `$#`, never on the string (measured 2026-11-15 21:2x ET)

    printf '%q '          -> ''      zero operands
    printf '%q ' ""       -> ''      one genuinely-empty operand, byte-identical
    f(){ printf '%q ' "$@"; }; f -> ''   no args

So the trap is not that `%q` "mints an argument": it is that the token `''` is the SAME OUTPUT for
"no arguments" and "one empty argument", which makes any test on the rendered string undecidable.
The only correct guard is a branch on `$#` — `[ $# -gt 0 ] || ARGS='<none>'` in
`run-gate-logged.sh:80`. This is `printf` running its format once with zero operands (POSIX/bash
format-once semantics), so it is not a version artifact: measured here on bash 5.2.21, and identical
on 5.1.

The asymmetry that makes the reader's rule necessary rather than redundant: a reader that only
rejected EMPTINESS would accept `''`, which is exactly the unguarded writer's output. So
`check-index-rows.sh` rule 4 refuses `''` as AMBIGUOUS — the row cannot say whether zero or one
empty argument was passed, and ambiguity is a refusal, not a preference. Writer states which case it
is in (`<none>` / a token list); reader refuses the token that has two meanings.

## A self-counting gate cannot count itself: a row count in a log is a snapshot at read time (measured 2026-11-15 20:48)

`run-gate-logged.sh` appends its INDEX row AFTER the gate has run, so the run that reads INDEX also
writes the rows that change it. Measured: canonical `INDEX.tsv` read `rows=27 eight=17 nine=10` at
20:48:30, and the same run's two appends (the manifest row and the index-rows row itself) took the
file to 29 lines / `17x8 + 12x9`. A scratch copy that read one run later held 28 / nine=11 — one row
BEHIND, not drifting. So: never compare a logged row count to the live file, and never expect the
last row to describe the run that wrote it. Quote the count with the `ts` of the read, the way
`docs_oid` is quoted with its head.

## A command's EMPTINESS is not its answer — read the exit status, and let the field say `unknown` (measured 2026-11-15, frodo + buzzy, `1ff9cc8`)

`git status --porcelain` prints nothing when the tree is clean AND nothing when git cannot run. The
old derivation in `install-mirror-legs.sh`, `check-manifest.sh` and `check-wal-doc-links.sh` tested
that emptiness, so "no index / no git" was recorded as a measurement of clean:

    real git, dirty tree:    repair head=989421416c41 dirty=1
    same tree, git failing:  repair head=unknown    dirty=0      <- absence wearing a number

`head=unknown` was the only tell, one field from the number a guard reads, so a `refuse dirty>0`
guard would have published unreviewed bytes from a published-leg tree (no index). All three now
branch on the exit status and print `dirty=unknown`; the token set is `0|1|unknown` and only `0`
means clean.

Two rules follow, and they generalise past git:

- **`if ! out=$(cmd); then` is the only shape that can distinguish "no output" from "answered
  nothing".** `[ -n "$(cmd)" ]` cannot, and the failure mode is silent because the empty branch is
  the *plausible* one.
- **A guard over such a field is a pass-list, not a ceiling.** `refuse dirty>0` and
  `refuse dirty!=0` differ precisely on `unknown`, and that is the case the guard exists for: state
  what it ACCEPTS (`publish only when dirty=0 AND head!=unknown`), not what it rejects.

Measured the same day for the adjacent writer, `run-gate-logged.sh`: its `dirty_paths` count is 0
when git fails, but the row carries `head=none origin=none`, so the coordinate is visible and the
field is a count with a reader — deliberately left alone rather than retyped.

## A count names the TREE, never the author -- so a dirty field carries its PATHS (measured 2026-11-15 21:41-21:52 ET)

`run-gate-logged.sh` journalled `# dirty_paths : <count>`. Two defects sat in that one line,
and each one is a class already in this file, one field over:

- **The count came from the emptiness of `git status --porcelain`.** A failing git prints
  nothing, so the row said `dirty=0` -- the `1ff9cc8` defect, still live in the wrapper that
  stamps `INDEX.tsv` after the writer and both gates were fixed. `# head : none` was the only
  tell, and a tell is not a branch: the field a guard reads is the dirty token.
- **A field named for a SET carried a cardinality.** Measured in a shared worktree: at 21:41
  `git status` read nine modified `wal-ops` files, all of them the other writer's, in flight,
  and `1ff9cc8` committed them four minutes later mid-turn. `dirty=9` named no one. The path
  set does not name an author either -- no git command reports who wrote an uncommitted path
  -- but the set is the part a second writer can RECOGNISE as theirs, and the only part that
  can be intersected with a commit's scope.

Now `# dirty_count : 0|N|unknown` and `# dirty_paths : <path path ...>|<none>|<unknown>`, the
paths stripped of the two porcelain status columns and space-joined (a path with a space keeps
its porcelain quotes, so the field stays reversible). `INDEX.tsv` keeps its nine columns and its
`dirty` token; only the token's value set grows by `unknown`.

Three arms, scratch repos, no share touched:

    clean committed repo        -> dirty_count=0        dirty_paths=<none>
    +1 modified, +1 untracked   -> dirty_count=2        dirty_paths=f "un tracked.py"
    git shim exits 1            -> dirty_count=unknown  dirty_paths=<unknown>   head=none

**Same fix, one field up: a failing `rev-parse` ECHOES ITS OWN OPERAND.** `git rev-parse
origin/master` in a repo with no `origin` remote prints `origin/master` to stdout and exits
non-zero, so the row journalled `origin/master : origin/maste` (a sha column carrying a branch
name, truncated to 12) beside `head=none` that contradicted it. Both lookups now use
`git rev-parse --verify -q`, which prints nothing on a miss and lets `|| echo none` stand.
Re-measured after the fix on the same scratch repo: `head=2d6611c5f13c origin/master : none`.

The durable rules:

- **A field's NAME is part of its claim.** A set-named field that prints a count, or a sha-named
  field that prints a missing rev's own argument, is the same failure as an `md5=` over a name
  list quoted as a content witness: the reader qualifies it by the name, and the name is wrong.
  Rename or carry the real value -- never leave the name standing over a different object.
- **Attribution is not in the worktree.** `dirty=N` and `dirty_paths=...` are properties of a
  TREE, so a commit from a shared checkout can sweep another writer's half-finished edit into a
  green. The two places a human can test it are both at commit time: `git diff --cached
  --name-only` must equal the intended scope before committing, and after committing each graded
  path's blob must equal the bytes that were graded (`git show HEAD:<path> | md5sum` vs the
  gate's `docs_md5`/row). A clean `git status` proves somebody's bytes, not yours.

**`dirty=` (a gate) and `dirty_count` (the wrapper) are two SCOPES under one word, and one journal row carries both.** Measured 2026-11-15T22:47:54 -0500, same row: `# dirty_count : 4` with four paths printed (`chat-room/bus.py`,
`chat-room/test-wake-envelope-and-room-witness.py`, `chat-room/wake_dispatcher.py`,
`chat-room/test-bus-mention-ts.py`) beside the gate's own `dirty=1`. Both are right.
`check-wal-doc-links.sh:328` runs `git status --porcelain --untracked-files=no`, so its token is
tracked-only; `run-gate-logged.sh` runs `git status --porcelain`, so its count includes untracked.
One untracked file moves the count and cannot move the boolean -- the difference is scope, not drift.
Quote the field name WITH its scope (`dirty_count(all)=4`, `dirty(tracked)=1`), never `dirty` bare,
and never grade the pair as a contradiction. The discriminator is in the row already: a path that
appears in `dirty_paths` and not in `git ls-tree -r HEAD --name-only` is the member whose
bytes are not in the commit the two fields are being read as describing.

**`git ls-files` is the INDEX, so it is the wrong discriminator for a HEAD-scoped question.**
Measured 2026-11-16 in a scratch repo, nothing committed in either arm: with an untracked path
`untracked-new.txt`, `git status --porcelain` = 1 line and `--untracked-files=no` = 0 lines. After
`git add untracked-new.txt` and no commit, both views = 1 line, `--untracked-files=no` prints
`A  untracked-new.txt`, and the path is STILL absent from HEAD: `git ls-tree -r HEAD --name-only`
= 0 hits, `git diff --name-status HEAD` = `A	untracked-new.txt`, while `git ls-files` = 1 hit. So
a staged-but-uncommitted path flips an ls-files-keyed rule to "no untracked difference" while it
is not in HEAD at all -- and the row's `dirty_count` would read 3 against the gate's `dirty=1`
(two scopes that genuinely agree) the moment someone stages the fourth path, inviting a reader to
conclude the scopes have converged when what moved was the scope of the discriminator. Three
objects live under one tree: the WORKTREE (porcelain), the INDEX (`ls-files`,
`--untracked-files=no`) and HEAD. Grade against the object the claim is about; `git ls-tree -r
HEAD --name-only` is the one that answers "is this path in HEAD". `run-gate-logged.sh` now prints
`dirty_untracked` (porcelain `??`, worktree-vs-index) and `dirty_head_absent` (index-vs-HEAD)
beside `dirty_paths`, so the classification is in the row and nobody has to intersect booleans
by hand. (frodo's correction, 2026-11-16, accepted and landed.)

## A witness needs a door, and `rows=0` is three different facts

**A tool that answers "how many rows at or before X" must refuse an X it cannot place.**
`census-recheck.py 17:31:00-0500` — a cutoff missing its date — printed a well-formed table
for both profiles, `rows=0 … mask=0`, and exited 0 (frodo, measured 2026-11-15 by using the
tool exactly as documented). The comparison was `ts <= cutoff` on strings, and the log's `ts`
carries its date, so a dateless cutoff sorts below every row in the file. `rows=0` then had
three possible causes -- empty file, cutoff before the log's first row, cutoff matching no
format -- and the table could not tell them apart. Same silent-green class as the stamp
defect, living inside the fix for it. Three doors, each now shut with a distinct code: a
malformed cutoff exits **2** before any file is opened; a row whose `ts` missing/unparseable
is counted as `ts_bad` and exits **3** (a row that cannot be placed in time is not a row that
can be left out of the total -- leaving it out silently makes the total a different file's
total); a syntactically valid cutoff outside the log's own `min..max` exits **3** and prints
the span. Quote an exit code with a census, not just its numbers.

**An identity row needs the column that can NAME the event, not only the one that can flag it.**
A preserved-mtime copy -- `cp -p`, `rsync -t`, `tar` restoring times, and the skill-manager
`_restore_snapshot` path all do it -- yields identical bytes, identical md5, `-ef` green,
nlink restored, and a new ino/btime. An `(ino, btime)` row therefore says "the file MOVED"
and stops there. Measured on provenance-quoting.md 2026-11-15T21:28:08 (frodo): ino
17748465 -> 15479978, btime 21:28:08.095305184, mtime 20:48:01.001716243 -- mtime 39 minutes
OLDER than the file's birth. A live ext4 file cannot be modified before it exists, so
`mtime < btime` is the signature of a planted clock, and it is only a signature if the row
carries mtime. `DOC-IDENTS.tsv` is now `name<TAB>ino<TAB>btime<TAB>mtime`, the gate FAILs
`RESTORED-TIME COPY` on `mtime < btime`, and the drill proves the isolation: plant the copy,
re-record AFTER the plant so ino/btime agree, and the gate still reads ok=16 fail=1 rc=1 --
every other field green.

**A timestamp that a clock cannot parse is not a timestamp.** `DOC-IDENTS.tsv` carried
`2026-11-15T16:35:27.163383733T-0500`, produced by `tr ' ' 'T'` on `stat`'s output -- `date -d`
refuses it, so the field could only ever be compared by byte order and never handed to a
clock. It is now real ISO 8601 (`stamp()`), and both columns are checked with `date -d` in
the drill. Same rule as the field NAME carrying a claim: the FORMAT carries one too.


## Two keys for one tree: a COUNT and a BOOLEAN must not share a name (measured 2026-11-15 22:12-22:31)

`INDEX.tsv` field five is a count of dirty paths; every gate's closing line prints `dirty=` as
the `0|1|unknown` boolean of the same tree. Both were spelled `dirty`, so on one tree with four
in-flight files a reader saw the wrapper journalling `dirty=4` and `check-exec-copies` printing
`dirty=1` -- a contradiction that does not exist, manufactured by a shared name. The log header
already said `# dirty_count`; only the column list in the wrapper's help text still said `dirty`.
Renamed there, and nothing moved: `check-index-rows.sh` reads field NINE and NF and never field
five, so the reader contract is the arity, not the name. The rule is the one already in this
file -- a field's NAME is part of its claim -- applied to two writers of one file rather than to
one field.

## A default on a field that GRADES health is a silent-pass generator (measured 2026-11-15 22:10:32, INDEX line 63)

`check-index-rows.sh` printed six awk values and read five back, so `nine` arrived as an EMPTY
STRING and `nine=${nine:-0}` turned it into the one value that reads healthy -- while `nine == 0`
is itself a FAIL condition in the same gate. The row is in `INDEX.tsv`: `rows=62 ... nine=0
ok=62 fail=0 rc=0`, a gate holding the exact defect it exists to find and reporting success.
`set -u` would not have caught it: `read` ASSIGNS the variable, empty, so it is not unset.
`${x:?}` fires on unset OR null, so the six fields are now required by name, and a drifted
printf/read arity aborts loudly instead of printing a zero. Measured in isolation:
`read -r a b c <<< "1 2"; c=${c:-0}` -> `c=<0>` rc=0; `: "${c:?verdict field missing}"` -> rc=1.

## Identity is worktree-local; blob identity and content identity are portable (measured 2026-11-15 22:30, replay of eb5d5a0 in a fresh worktree)

Re-ran the gates at `eb5d5a0` in a `git worktree` (a clean checkout, new inos/btimes, same bytes):

    doc-links   docs_oid=2c4470cb  docs_md5=51f6136b  idents=b9940ced  ok=0 fail=24 rc=1
    exec-copies copies=5 ok=5 fail=0 copies_idents=bef9ef35 rc=0

The two lines say opposite things about the same commit, and both are correct. `docs_oid` (blob)
and `docs_md5` (content) REPRODUCE exactly across a checkout; `idents` and its 24 FAILs do not,
because `(ino, btime)` is a property of the directory entry, not of the file. `copies_idents`
reproduced because it digests `(name, btime)` on the SHARE, where btime survived the copy -- so
it is quotable from any worktree, which is exactly why it is the field to quote.
Consequence for a gate report: "checked at each commit" is only checkable in the worktree that
recorded it. A replay elsewhere grades the checkout, not the commit -- say which tree the
numbers came from, or the doc-links FAIL count is evidence of the wrong event.

The mirror arm has the same shape from the other side: at `eb5d5a0` on a live share the gate read
`mirror_ok=5 mirror_drift=0` at 22:12 and, replayed at 22:30 after a leg had been published from
`104f7c1`, `mirror_ok=4 mirror_drift=1` -- `/mnt/nas/host-ops/run-gate-logged.sh` stale by
`d2a9ee49 -> b5afa5bb`. A published leg is repaired to the TIP, so `mirror_drift` measures the
distance between the graded commit and the newest published one, not the health of the graded
tree. Grading an old commit with a mirror arm open is an anachronism, not a defect: it needs
`mirror=stale-by-tip` (a NOTE keyed on head != published head), never a FAIL.

## A window has two ends, both are slots, and every slot label needs its date (measured 2026-11-16 00:20-00:45 -0500, 77 `pairsplit2-*.log`, HEAD `31cedba`)

The witnessed ladder, enumerated rather than eyeballed:

    e8dafb6c  23,220 / 17a40c53  2026-11-14T192835 .. 2026-11-14T202835   2 slots
    3bf2bbc6  23,486 / a33ea3e6  2026-11-14T212835 .. 2026-11-14T222835   2 slots
    fd232d78  24,063 / 2335ff8a  2026-11-14T232835 .. 2026-11-15T082835  10 slots
    4a46ccc3  30,458 / 6c543893  2026-11-15T092835 .. 2026-11-16T000306  15 slots, open

Three traps in those four lines. (1) Only **29 of 77** logs carry a `# WITNESS` line at all (era
record: 28 of 76 at `20261115T230306`); the
other 48 cannot bound anything, so a slot count is always two numbers (`10 of 77`), never one — and
the denominator is a clock, so quote the slot label the ratio was taken at.
(2) The `fd232d78` window **straddles midnight**, and the scheduler is not hour-uniform: the last
`:28:35` arm is `2026-11-15T192835`, the next slot is `2026-11-15T210306` — 1h34m31s later — and
`210306/220306/230306` are then the evening slots, so a bare `232835` is ambiguous by a day. Date
every label. **Do not read that gap as a 34m31s rotation.** `21:03:06 - 20:28:35 = 34m31s` names a
slot that never existed: `ls pairsplit2-20261115T202835.log` is empty (its 11-14 sibling
`pairsplit2-20261114T202835.log` does exist, 1,239 B), while the journal shows seven
ticks, 19:30:00 through 20:00:00, each a fresh `bash` (PIDs 3081992 .. 3217842), announcing
`# deferred: next slot 2026-11-15T20:28:33` with the countdown falling 3515s -> 1715s in 300s steps,
then the 20:05:00 tick (`bash[3237670]`) recomputing to `next slot 2026-11-15T21:03:04 ... is 3486s
away` because the bus MainPID had changed
(3230257, start 20:03:04 EST): in 300s of wall clock the target moved 2071s, so a restart does not
delay an announced slot, it relocates it. The slot second was **not idle** and was **not written**.
The watcher logged a burst of `CLOSE_WRITE` on
`room.db-wal` -- 20:28:29.166109, .560654, .969862, 20:28:30.368777, .770012, 20:28:31.072678, ~0.4s
apart -- and every line in the burst carries the same `size=284312 mtime=2026-11-15T20:24:58.049941`;
identical size and identical mtime across successive closes is a reader's open/close loop over bytes
last changed at 20:24:58, 3m32s before the slot, not a write. And `1011876` is not the writer: it is
the fanotify watcher's own journald tag `python3[1011876]`, which appears 0 times in the body of
`wal-unlink-verdict.log.1` where the lines are stamped. An observer's tag is not a writer's identity
-- the inode error in another column. And the only `20:28:33` string in the 77 logs is
the 11-14 header -- so "no activity", "no write" and "no log" are three different sentences. The slot formula is
recomputed from `ExecMainStartTimestamp` on
every tick, so a bus restart **strands an already-announced slot** — it is neither captured nor
deferred and no line ever says it was missed. A grid derived from slot labels alone records that as
a missed capture plus a tidy 34m31s rotation; both would be wrong. Quote the journal's
deferred/captured pair with the labels.
(3) Count both ends *and* quote both margins, on ONE clock, sub-second kept:
`fd232d78`'s west slot is 7m14s after `d06857c` (commit time 23:21:21); its east margin is 16m27s on
the commit clock (`4cdfa20` 08:45:02) or 17m21.641375s on the mtime clock (`08:45:56.641375`) — the
often-quoted 17m21s is a truncation, and mixing the two clocks in one sentence is how "exact" stops
being exact. `3bf2bbc6`'s east slot is 52m46s before `d06857c`. All eight margins, commit-clock and
label-to-label, so none has to be recomputed by a reader: `e8dafb6c` west 44m18s (`14f9f9c`
18:44:17), east 11m39s (`65f2048` 20:40:14); `3bf2bbc6` west 48m21s, east 52m46s; `fd232d78` west
7m14s, east 16m27s (17m21.641375s on the mtime clock); `4a46ccc3` west 42m45s to the label `09:28:35`
and 42m43s to its slot `09:28:33` -- the same field choice as the 42m3x line, one paragraph down --
east open. Four blobs, four different east margins: a single "the gap between the blob and its slot"
is not a quantity this grid has.

## A log filename is a CAPTURE stamp, not a slot — difference it against nothing (measured 2026-11-16 00:00-00:45 -0500, 77 logs, HEAD `31cedba`)

Every witness header carries both fields: `# captured <ts>; slot <ts>`. Across all 77 logs the
delta is **2.000s in 67, 6.000s in 8, 10.000s in 2** (all ten exceptions on 2026-11-13,
12:02:13-15:48:51, when the clock was coarse). So `pairsplit2-20261115T092835` is a capture stamp
and its slot is `09:28:33`. The same "window" is three different numbers depending on the field:
`08:45:56.641375 -> slot 09:28:33 = 42m36.358625s`, `-> label 09:28:35 = 42m38.358625s`, and
`42m39s` from truncating the label at both ends. A duration rule must name the field, not say
"slot label".

A date-dropped pair produces a **modulo-day residue** that looks like a clean rotation:
`2026-11-14T20:28:35 -> 2026-11-15T21:03:06` is 1 day 00:34:31 (88,471s), and the `20261114T202835`
log does exist. So the check is not existence, it is *delta > grid spacing*: a sub-hour answer that
carries a whole day is a dropped date, not a fast rotation.


## "Resolved at <commit>", never "introduced at", and keep the whole commit set (same set)

`4a46ccc3` is put in the tree by `61e21f2` (2026-11-15 08:45:50) and was the path blob at
`d924fd5` (2026-11-16 00:15:21, mode 100755; it is also the path blob at `31cedba` 00:36:29) --
say the commit you read it at, dated, and never "at HEAD": a read-time HEAD such as `df55444`
(2026-11-15 22:50:21) as the binding commit binds nothing, and neither does quoting a HEAD that has
since moved (`c6e5842` at 23:01:07 was the resolution point for eight commits; `d924fd5` stood for
one more; `31cedba` is a different read again). And one blob can be
re-touched: `e8dafb6c` sits in `14f9f9c` (introducing) and
`65f2048` (re-touch), so blob -> commit is a SET; say which resolution you mean. `fd232d78` is a set
too -- `{4cdfa20, d06857c}` by `--find-object` -- but never read that set as the carriers.
`--find-object` reports **change points**: added at `d06857c`, removed at `4cdfa20`. At `4cdfa20` the
path blob is `faf8e2db` (100755), so there is no mode to quote for `fd232d78` at that commit — it is
in no tree there. Carriers come from `git rev-parse <commit>:<path>`: `fd232d78` in 64 commits
(`d06857c^..4cdfa20^` inclusive, all 100644), `e8dafb6c` in 15 (`14f9f9c^..65f2048^`), `3bf2bbc6` in 11
(`65f2048^..d06857c^`), `faf8e2db` in 1 (`4cdfa20`, 100755), `4a46ccc3` in 115
(`61e21f2^..d924fd5`, all 100755; 117 at `31cedba`; 118 at `d359ab8`; 123 at `a2e1a26`).
Those five rows START AT `e8dafb6c` (`14f9f9c`, 2026-11-14 18:44:17) -- they are a floor, not the
set. The path holds NINE blobs; the four earlier ones are `1d7c2604` in 3 (`b46faac`, 2026-11-13
16:32:48), `1f08487d` in 3 (`019a741`, 18:15:43), `6f337043` in 86 (`7643d5c`, 20:18:11) and
`c1474b47` in 14 (`4289214`, 2026-11-14 16:01:44). So nine blobs total 314 commit-slots at
`31cedba` (208 of them in the five rows) and 320 at `a2e1a26` (214 in the five rows) -- a total and
a row-sum quoted at DIFFERENT heads is the two-clock error in one sentence. `c1474b47` is already
cited at `live-process-grading.md:156`, which is why the five rows have to declare their start
rather than leave it to be read as the whole set. Mode is a property of
(commit, path), never of blob. A carrier count is ancestry-shaped as well as ref-scoped: every commit
that inherits the blob increments it, so a number carried forward across commits decays by one per
commit -- re-count at the ref you name instead of quoting yesterday's HEAD.
Two scope traps ride on those numbers. (1) `A..B^` alone returns one fewer than the inclusive span
(63 for `fd232d78`) because it excludes its left end -- say inclusive or quote the range form with the
count you computed. (2) The count is scoped to a REF, not to the repository: `--branches` gives
`fd232d78` 65, because side-branch `ffdf9ac` (2026-11-15 04:31:06, `scan-arm-census`) carries the same
blob and is not an ancestor of master. A count without its ref is a number that moves.

The share cannot answer the mode question at all: `/mnt/nas` is mounted `file_mode=0644,nounix`, so
`stat` on the NAS path reads `644` while the same bytes in a worktree read `755` and the git tree says
`100755`. A mode quoted from the share is a MOUNT property; `ls-tree` is the only mode claim.

One more 40-hex to label: `git cat-file blob fd232d78 | sha1sum` = `fd0633105b9932f8ea28aa4b3a3ec7159a6b3c9a`,
24,063 B, md5 `2335ff8a`. That is the digest of the CONTENT and can never equal the object id, which
hashes `blob 24063\0` plus that content. Same body, several strings -- name the column, or the reader
pairs a correct byte count with a hash that does not exist.

The empty window is a legal answer and is clean when quoted as one: `faf8e2db` = `4cdfa20` 08:45:02 -> `61e21f2`
08:45:50, 48 seconds alive, 0 capturing slots, `grep -l` over 77 logs = 0.

## ino is stable within a lookup generation, not across one (measured on the CIFS share)

Same path, identical `size`/`mtime`/`birth`: one read gave `ino 830979954`, two later reads both gave
`844381491`. Two readings inside one generation agree, so an agreeing ino is not evidence, and an
ino quoted from an earlier generation is not refutable -- not even your own. On this mount the only
quotable in-place proof is `birth < modify` with birth unchanged. Never cite ino across time.


## `dirty=` in a gate line is a predicate; a checkout's dirties are a count

Measured 2026-11-16T05:53Z on `92480e9`: `git status --porcelain | wc -l` = 4 on the shared checkout,
and the same tree printed `dirty=1` on `check-wal-doc-links.sh`'s verdict line, because that field only
ever holds `0` / `1` / `unknown` (`check-wal-doc-links.sh:380-385`). Two agents quoting 4 and 1 about the
same tree are both right and neither refutes the other. Quote a count as `git status --porcelain` with its
path and its instant; quote the gate's `dirty=` as "dirty, and this line does not say how much".

Same shape for "who wrote this": a file that moves onto a NEW inode inside a peer's own turn is that peer's
write (temp+rename: btime == mtime within ~2 ms, nlink 1). The discriminator is the turn window -- the
sender's own posts before and after -- not the inode delta, which only proves a write happened.

## `56364eaa` is the eight-DOCS era, not a reading — re-derive from the current tree (2026-11-16)

The specimen above is honest and retired. At the eleven-DOCS roster the same pair reads:
`docs_oid=5abbb01692a9c470734095c7575b8bd9b9b2e450` at `b1d0226`, `cc350c0` and `2eebb14`,
while `docs_md5` went `a78f0b70a3b6264b` -> `203c82698c8451b2` and `idents`
`a942c4c4c7be7947` -> `bf9b53897394fdb2`. `2eebb14` edits `gate-arms-and-false-greens.md`
(+12/-2) and `docs_oid` still does not move, because `:418` hashes the eleven NAMES
(`git hash-object --stdin`) and `:426` hashes the `(sha256, name)` pairs.

Derive that invariance, do not sample it: `git diff b1d0226 2eebb14 -- check-wal-doc-links.sh`
is empty, so the DOCS list feeding `docs_oid` is byte-identical and the field *cannot* differ.
Cover the RANGE, not the pair that happened to be compared: `git log --oneline b1d0226..7209e59 --
chat-room/wal-ops/check-wal-doc-links.sh` prints nothing (rc=0), so one command certifies the
invariance across all four heads at once. (buzzy, 2026-11-16 09:11, re-derived not sampled)
however many doc bytes move. The asymmetry runs one way only — a changed `docs_oid` is a roster
change and still says nothing about content.

Do not re-derive from `797bf17` either: `git rev-list --count 797bf17..HEAD` rc=0 -> 202 at
`2eebb14`. A commit distance quoted without the head it was taken at is a clock, and the
`docs=7 docs_oid=56364eaa` line belongs to the era it was measured in.
