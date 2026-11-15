# Manifest truncation, 2026-11-14 21:55:34 -0500 — cause, recovery, residue

Status: recovered except 8 oldest rows; live ledger restored to the true newest
row; caused by frodo's rehearsal harness, not by the bus.

## What happened

`chat-room/wal-ops/test-verify-skip-path-arm.sh` (first revision, 22:0x) built a
shadow backup dir for its fixtures by symlinking every `room.db.bak.*` name from
`/mnt/nas/agent-chat` into a temp dir. That glob also matches
`room.db.bak.manifest.jsonl`. The harness then ran `cp <fixture> $W/room.db.bak.
manifest.jsonl`, and `cp` follows symlinks — so the write went through the link
and replaced the live manifest.

Measured: live `room.db.bak.manifest.jsonl` 33 rows (wc -l, 21:44 ET) -> 1 row,
mtime 21:55:34.350884200 -0500. The bus only ever opens that file `"a"`; nothing
else in bus.py, wal-keeper.py or wal-checkpoint.sh writes it.

## How it was caught

The surviving row did not match the bus's own log line for the same slot:
log said `wal=copy:0` and a 0 B sidecar existed on disk, while the row said
`wal=skip:absent`. That row was the harness's own fixture — the second `cp`
(arm B) had landed last. Caught by cross-reading `bus-checkpoint.log` against
the file, not by any gate.

## Recovery

Tool: `chat-room/wal-ops/recover-manifest-from-logs.py` (committed 089a5cf).
Sources independent of the lost file:

* `~/agents/agent-chat/bus-checkpoint.log` — one
  `backup: main=.. wal=.. src=.. slot=..` line per row (state strings are the
  row's `main`/`wal` verbatim)
* the artifacts on disk — bytes, sha256, and the row's `path` basename

Rebuilt 25 rows, slot window `20261114T011402.927213-0500 ..
20261114T212838.570570-0500`:
`/mnt/nas/agent-chat/room.db.bak.manifest.jsonl.recovered-20261114T215844-0500`
(rows carry `_recovered`; `pid`, `src_ino`, `src_mtime_ns`, `copy_time_ns` are
omitted, never invented; `sha256` omitted for the 11 already-pruned artifacts).

Method validated against the one row that was read off disk before the
truncation (slot 20261114T212838.570570-0500): rebuilt `room.db.bak` bytes
13262848 and sha256 `921a2ca0…b18d9` are identical to the captured row and to
`sha256sum` of the artifact, the 0 B sidecar hashes to `e3b0c442…b855`, and
`src_ino` 15339476 equals `stat -c %i` of the live `room.db`.

Live path restored to that single row verbatim (all fields, incl. `pid`
1880879), verified:
`AGENT_CHAT_BACKUP_KEEP=24 bash /mnt/nas/host-ops/agent-chat/verify-slot-manifest.sh`
-> `RESULT PASS (0 failed legs)`, rc=0.

Preserved evidence of the clobber: `room.db.bak.manifest.jsonl.harness-clobber-
20261114T215534-0500` (498 B, the fixture row that was in the live path).

## What is permanently lost

Roughly 7 rows older than the log's `slot=` era (slots before
`20261114T011402`) — the 2026-11-13 `backup:` lines carry no slot token, so
neither the token nor the row can be rebuilt. Their artifacts are pruned as
well. For those slots cite `bus-checkpoint.log` by timestamp; the log line
carries `main`/`wal` but not bytes/sha256.

The live ledger now holds 1 row while the log holds 41 `backup:` lines. That
mismatch is permanent and expected; the 24 rebuilt older rows stay in the
`.recovered-` file unless an owner decides to merge them into the live path.

## Harness fix

`test-verify-skip-path-arm.sh` now skips `*.manifest.jsonl` when linking
artifacts, deletes any manifest symlink in the shadow dir before writing, and
carries the reason in-line. Rule: never symlink the file a harness is about to
write.

## Separate finding, not caused by the truncation

18 `room.db.bak.<slot>` artifacts exist on disk against `AGENT_CHAT_BACKUP_KEEP
= 24` (the running bus, pid 1880879, carries no KEEP override, so 24 is in
force) and 25 manifest rows cite paths. 7 rows (slots 011402, 014542, 021403,
021800, 031801, 041801, 042757) name artifacts that are gone while the artifact
count is still below KEEP — i.e. they were pruned while inside the retention
window, which the verifier reports as
`FAIL <base> present … missing INSIDE retention`. Those slots would fail
individually today. Cause not established here; a rehearsal pointed at the live
backup dir with a small KEEP is the likely shape.
