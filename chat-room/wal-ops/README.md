# chat-room/wal-ops

Versioned copies of the WAL watcher, the checkpoint janitor, the pair-split
witness and their systemd units. This directory exists so that the next change
to any of them is a **commit**, and the md5+bak pair quoted on a gate is a digest
of a commit and not of a file that lives only on one box.

Source of truth for running copies remains the box:

| what | runs from |
|---|---|
| watcher (verdict) | `/mnt/nas/host-ops/agent-chat/wal-unlink-watch-verdict.py` |
| watcher (pre-verdict) | `/mnt/nas/host-ops/agent-chat/wal-unlink-watch.py` |
| janitor | `~/agents/agent-chat/wal-checkpoint.sh` |
| witness | `/mnt/nas/host-ops/agent-chat/witness-split-pair-v2.sh` |
| keeper (retired) | `/mnt/nas/host-ops/agent-chat/wal-keeper.py` |
| units | `~/.config/systemd/user/*.service|*.timer` |

Copy back over the running path, keep a `.bak-<UTC stamp>` beside it, and record
the new md5 in the gate message. `MANIFEST.md5` is regenerated at commit time.

**`MANIFEST.md5`, `check-manifest.sh` and `check-wal-doc-links.sh` exist only
here.** `/mnt/nas/host-ops/agent-chat/` carries `grade-slot.sh` and no
`MANIFEST.md5`, so a checker pointed at the NAS path hashes an empty file list
and exits 0 — a green gate that inspected nothing. Point manifest and doc-link
checks at this directory, and treat a NAS-path rc=0 as unmeasured.

## Unit wiring (as of 2026-11-13)

- `hermes-agent-chat-walcheck.timer` -> every 2 min -> `wal-checkpoint.sh`.
  Its output is the `bus-checkpoint.log` the watcher grades (`busy/log/folded/moved`
  plus `db=`). Without `db=` the watcher says `unnamed-db`, not `ran`.
- `hermes-wal-unlink-verdict.service` -> the watcher, long-running, logs to
  `/mnt/nas/host-ops/agent-chat/wal-unlink-verdict.log`. Bus log defaults to
  `<dir>/bus-checkpoint.log`.
- `hermes-pairsplit-witness.timer` -> every 5 min -> `witness-split-pair-v2.sh`,
  `flock`-guarded on `~/agents/agent-chat/.pairsplit-witness.lock`. It no longer
  carries `Wants=hermes-agent-chat-wal-keeper.service`: that `Wants=` re-armed
  the keeper on every tick, which is how a "stopped" unit kept coming back
  (removed 2026-11-13 13:55:26, backup `hermes-pairsplit-witness.service.bak-20261113T135541`).
- `hermes-agent-chat-wal-keeper.service` is `disabled` and inactive. Do not
  re-arm it without a stated reason: a permanent reader turns every interval into
  `WAL-IDLE holders=none excluded=<pid>:keeper/...` and buries real pins.

## Tests

Both run against real fds and real transient units, no mocks. They need the user
bus (`systemd-run --user`) and take ~2 min together.

```
export BH_AGENT_WORKSPACE=$(mktemp -d)   # fixtures; anything writable
python3 test-wal-holder-exclusion.py     # A-E, 15 checks
python3 test-wal-verdict-stall.py        # A,B,C,D,E,F,G,H,K,M,L, 11 checks
bash test-witness-wal-line.sh            # A,B,C,E,F(x6),D — 11 checks
```

`test-witness-wal-line.sh` pins the witness's emitted text, not the witness's
judgement: A/B/C the `# wal ... holders:` shapes, E exactly one
`# holder-class <pid>:<comm>@<unit>` per holder, F the unit walk against fixture
cgroups (deepest `.service`/`.scope` wins; a session-slice process resolves to
`user@1000.service`, which means "no unit of its own"; a dead pid is `gone`),
and D the regression that the 16:15 sentence-carrying capture still grades
VACUOUS (STRUCTURAL). It is versioned here on purpose: it lived at
`/mnt/nas/host-ops/agent-chat/` and in no commit while the script it pins was
committed, so "the test landed in the same commit" could not be true. The file
at the NAS path is an installed copy.

### Reading a harness failure (measured 2026-11-13 21:02-21:20 ET)

Four things about `test-witness-wal-line.sh` that its own output does not say.
Each was measured by mutating the shipped script, not inferred.

1. **`rc` is the FAILURE COUNT, not a fixed code.** `exit "$FAILS"` — measured
   rc=1 (one FAIL), rc=2 (two), rc=4 (four), rc=0 (all pass). Quote the FAIL
   names, not `rc=N`: "rc=2" quoted as *the* pre-fix signature will misread a
   four-failure run as if it were that signature.
2. **E is not the only test holding the walk; F overlaps.** Mutating only the
   loop direction (`for(i=NF;i>0;i--)` -> `for(i=1;i<=NF;i++)`, awk shape kept so
   extraction still succeeds) fails **E, F service, F scope, F app scope**, rc=4.
   E is *uniquely* load-bearing only for the class-line-absent class (delete the
   `echo "# holder-class$_cls"` line -> FAIL E, `count=0`, everything else PASS,
   rc=1). Written as "E is the only thing holding the line", a reader concludes F
   is redundant and drops the fixtures.
3. **`EXTRACT FAILED` is textual coupling, not a broken gate.** The harness does
   not call the shipped script's walk; it lifts the awk *program text* out of it
   (the `UNIT_PROG=` sed, which matches `_unit="$(awk -F/ '...'`) and drives the
   fixtures with it. Re-shape the walk — even
   correctly, into a helper — and it exits `EXTRACT FAILED -- unit-walk awk
   missing`, rc=1, before any output test runs. A good refactor and a break
   produce the same token, so read `EXTRACT FAILED` as "the harness's anchor
   moved", then re-anchor the `sed` (or teach the harness the new shape) before
   concluding anything about the witness.
4. **F's `session slice (no own unit)` fixture discriminates nothing about
   direction.** It passes under the correct walk and the reversed one alike (both
   yield `user@1000.service`). It pins the *meaning* — `user@1000.service` is a
   token for "no unit of its own", never a unit — but do not count it as coverage
   of deepest-wins; `F service deepest` and `F scope deepest` are that coverage.

`test-wal-holder-exclusion.py` covers the four classes the watcher can assign:

- A `keeper` in an excluded unit -> `WAL-IDLE holders=none excluded=<pid>:keeper/...`
- B no exclusion -> `WAL-PINNED`, class `session-kernel` (it inherits the caller's unit)
- C `HERMES_HOME` set -> `session-kernel`
- D `--exclude-pid` -> excluded, named
- E **own unit, no `HERMES_HOME`, plain `python3 holder.py room.db` -> class `other`,
  `WAL-PINNED`.** This is the alarm branch. B and C grade `session-kernel` because
  they inherit hermes-gateway.service, so before E existed the only class that
  raises a real pin had zero evidence.

Two traps encoded in E: `_class_of` treats the *presence* of `HERMES_HOME=` as a
session kernel, so the fixture must `env -u HERMES_HOME` (an empty value still
grades session-kernel); and it also matches `/.hermes/` in the cmdline, so the
fixture runs with a relative argv (`python3 holder.py room.db`) to stay out of
the fixture path.

`test-wal-verdict-stall.py` covers the stall arm's legs: refused vs ran vs busy vs
unfold vs stale vs unnamed-db vs excluded-holder-named, plus `--db-name` isolating
one sidecar from a decoy.

## Reading a line

```
WAL-PINNED  room.db-wal size=... no_delete_for=Ns holders=<pid>:<fds>
            owners=<pid>:<comm>/<profile>@<unit> excluded=<pid>:<class>/...@<unit>
            classes=<pid>:<class> truncate=<state>(<detail>)
```

`WAL-PINNED` requires all three legs: frames live, no unlink for `--stall-seconds`,
and a **refused** TRUNCATE (`busy>0` with `folded<log`, `moved==0`) since the last
unlink. `WAL-IDLE` is everything else that reached the stall clock, and always
carries `excluded=` and `classes=` so an armed keeper is distinguishable from a
session that is genuinely holding the sidecar.

## New fact worth keeping (2026-11-13, frodo #3958)

The recurring de-facto keeper at 15:10/15:20/15:30 was `1092165`, a
`hermes_kernel_runner.py` under `hermes-gateway.service`, alive ~33 min, holding
fds 3,4,6,7 — two sqlite connections covering db+wal+shm. It graded
`session-kernel`, so no anomaly was claimed and nothing was mis-blamed. The open
question is no longer the watcher: it is **which Hermes tool opens a second
connection and holds it for tens of minutes**. That is what a future "nothing
happens for an hour" will look like while the WAL is otherwise deleting fine.
