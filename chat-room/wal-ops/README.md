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
```

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
