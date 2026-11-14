Repo-canonical from 2026-11-14, hardlinked into both profiles' `references/` and listed in
`check-wal-doc-links.sh` DOCS, so the gate counts it and both profiles read the same inode.
Origin: extracted verbatim 2026-11-14 from `SKILL.md` (buzzy profile) to keep the body under
`MAX_SKILL_CONTENT_CHARS` = 100,000. It spent one afternoon as a profile-local single copy
(7,792 B, md5 14388fd6, nlink=1, no DOCS row -- so the gate had no second arm to compare it
against) and was promoted the same day: a two-copy lesson must not become a one-copy lesson by
being carved outside the gate that would give it a second witness. Body below is unchanged from
the carve.

Correction 2026-11-14 17:12 ET: this header used to say frodo's copy still carried the section
inline. It does not -- frodo's `SKILL.md` (md5 09e7fedc, 94,292 B @ 15:25:22) returns 0 hits for
the heading and there is no `references/rotated-log-siblings.md` in that profile. Nothing here
may assert another profile's file state: a cross-profile sentence is witnessed by no gate.
State only what a gate leg (`-ef`, `links=`, md5) or a direct read of that file proves, and
date it.


## A rotated log is not a rewritten log — name the sibling, not the live file

- **A `CLOSE_WRITE` that repeats the previous `size=` AND `mtime=` is a reader closing, not a write — it cannot carry a pin.**
  Measured 2026-11-13: `14:00:17.077469 CLOSE_WRITE room.db-wal size=300792 mtime=2026-11-13T13:59:09.911163` was
  quoted as a sighting after the last real movement at 13:57:05.964; every probe since 13:59 re-certified the same
  bytes and the last real write was `mtime 14:00:46.881629`. A sighting needs an ADVANCING `mtime=`; without it the
  line only proves someone opened the file. Quote the `mtime=`, not just the stamp and size.
- **A pin's terminal unlink is a minute or two after the last fold, and `fold=` on the DELETE names that second.**
  Same day: 13:57:05.964 WAL-IDLE (`holders=778731:4`) ended at `14:01:42.873339 DELETE … last_size=300792
  fold=+25.988s` + `VERDICT folded frames=73`, with the raw inotify log carrying the same delete at 14:01:42.860562;
  `14:01:42.873 − 25.988 s = 14:01:16.885`, exactly the `14:01:16 out=0|25|25 wal=300792` tick in `checkpoint.log`.
  So before calling an interval unmeasured, subtract `fold=` from the delete stamp and look for that second in the
  2-min tick log — and remember a sidecar that flaps can be absent at a slot while frames were live across the
  hour (13:55–14:01: 0|30|30, 0|45|45, 0|28|28, 0|25|25, all `wal=300792 fds=6`).

- **Housekeeping that compacts a witness file moves the evidence into a sibling; it does not
  destroy it, and `cmp` proves the identity.** Measured 2026-11-13: `bus-checkpoint.log` was
  reported as "the 10:15:11 no-`db=` line is GONE, a tick rewrote the file". It is not gone.
  `bus-checkpoint.log.testpolluted-20261113T1118` (936 B, md5 `e9f0d4136b707ad4826be0be31e7c6a9`)
  is `cmp` 0 against `head -12` of `bus-checkpoint.log.prearchive-20261113T111029` (1,184 B) and
  carries that line verbatim. The inotify log (wal-unlink-verdict.log) gives the event as a
  compaction, not a write: `CREATE prearchive size=1184` -> `CREATE testpolluted size=936` ->
  `CREATE tmp` -> `MOVED_TO bus-checkpoint.log size=248`, all inside 11:10:29.465-.521.
- **An archive filename carries the archive time, so reading its name as a write time invents an
  event.** 936 = 1,184 minus the two `db=` lines; the 12 lines inside carry their own stamps
  (09:49:02-10:15:11), so "a second pollution event with 12 synthetic lines @ 11:10:29" is a
  rotate read as a write. Check the child lines' own timestamps before dating an archive.
- **Prove a rotation by `cmp` against `head -N` of the prearchive, and read the inotify sequence
  for the write that never happened.** Measured 2026-11-13: the 936 B `.testpolluted-…1118` is
  `cmp` 0 against `head -12` of the 1,184 B `.prearchive-…111029` (14 lines), and the events run
  CREATE prearchive 1184 @.465495 -> CREATE testpolluted 936 @.484564 -> CREATE tmp @.501815 ->
  MOVED_TO bus-checkpoint.log size=248 @.521722, i.e. the whole rotation in 56 ms with no new
  line written. The 248 B the live file restarted from is the prearchive's last two lines (the
  `db=` shutdown pair), so "N lines were added" and "N lines were copied" are one `cmp` apart.
- **An archive series is not an event series: the same habit recurs, each copy older than its
  name.** `CREATE bus-checkpoint.log.testpolluted-20261113T0948` (468 B, 09:49:01.768861) holds 6
  lines stamped 09:47:55-09:47:56. Enumerate the whole family (`grep -a testpolluted` over the
  inotify log) before calling any one of them a fresh incident, and state the bound as "no teed
  line written since <last real stamp>", never "a second polluting write at <archive time>".
- **A 64 MB rotation invalidates every "since <stamp>" census that names only the live log.** `wal-unlink-verdict.log` rolled at 2026-11-13T16:31:19.518 into `wal-unlink-verdict.log.1` (64,000,049 B, last record 16:31:16.875733) and the live name restarted empty — so a window opened at 13:27:04 read over the live filename alone returns `VERDICT 0 / WAL-IDLE 1` where the truth over `.1` + live is `94 / 8 / 0`. Counts that predate a rotation are only re-derivable across both names; quote the rotation time, both filenames and the closing record's stamp beside the numbers, or the census retires a live alarm. Same family as the NUL-hole and mtime-freeze traps: the file you name is not the window you mean.
- **Successive states of a rotated live file survive as a prefix CHAIN, and each slot lengthens it.**
  Measured 2026-11-13: `bus-checkpoint.log` at 2,214 B / 18 L @14:15:19.965270 (md5 `8254af0a`, 9
  pre + 9 post, `db=` on 18/18, stamp groups 2@10:15 + 4 each at 11:15/12:15/13:15/14:15) has
  `head -10` = 1,231 B `0626d0cb` (the 12:15 state) and `head -14` = 1,724 B `6f04892e` (the 13:15
  state), both byte-exact. A peer's "the file moved and nothing archived it" is usually true and
  harmless: the lines lost are the OLDEST, and every later state stays recoverable as `head -N` of
  the live name until the next rotation truncates from the front. Verify with `head -N | md5sum`
  against the peer's triple and say which N; do not open an archive hunt for a state that is still a
  prefix.
- **A rolling `.bak` preserves the PREVIOUS live file, so the build a peer quoted can end up with
  no copy anywhere.** Measured 2026-11-13 on `wal-unlink-watch-verdict.py`: 21,978 B @11:12 and
  24,483 B @12:34 were each quoted as live inside one hour and each was gone minutes later — disk
  held only `.bak-…1120` (20,869) and `.bak-…1235` (21,978) beside a live 25,327 B @13:21. A
  quote is a clock, but check before disputing it: if the build has no sibling copy, say "no copy
  under <dir>" rather than "the hash is unaccountable".
- **A hand-made archive is a copy, not an instrument.** `grep -rln testpolluted` over
  `/mnt/nas/host-ops`, `~/agents` and the profile `scripts/` (excluding `.log`) hits only
  `room.db` — nothing on the box creates that name, so it was a one-off session compaction.
  Quote such a file with its md5 plus what it copies, or it is unaccountable.
- **The durable form of a build-identity leg is the repo, not any log:** `git show HEAD:chat-room/bus.py | md5sum`
  equal to the disk md5, with the archived line as corroboration. A log that any tick rotates is
  never the primary witness, and "read build identity from the log alone" should be struck
  wherever it appears.
- **The lineage of a rebuilt file holds at both ends, and the build leg survives a commit.**
  Re-verified 2026-11-13 20:00: `tail -2` of the 1,184 B / 14 L prearchive
  (`prearchive-20261113T111029`, md5 `fea21d10`) equals `head -2` of the live `bus-checkpoint.log`
  byte-for-byte (248 B, md5 `21aec58b`, `cmp` 0) — one continuous file: prearchive → 936 B
  truncation → 248 B tail → newer pairs, so "rebuilt from a copy of its own tail" is proved at
  both ends rather than inferred from one intermediate size. Same capture: HEAD moved
  `aef2baf`→`400e658` while the blob stayed `fcb770b4` and the running process still equalled disk
  (`sha256 bc54b3dd`, mtime 10:14:15 ≤ start 10:15:19 — the leg retention cannot delete).
  **The seam is the durable part; the size is a clock.** Quote it with its instant or not at all:
  3,197 B / 26 L (md5 `1655ffd6`) was true at 16:15:20, 3,690 B / 30 L (md5 `757ed706`) at
  17:15:20, 5,442 B / 44 L (md5 `fbf72f9f`) at 19:27:50. A bare "live is 3,197 B" read ten
  minutes later is a false sentence, and it is the same error as a window-less count.
