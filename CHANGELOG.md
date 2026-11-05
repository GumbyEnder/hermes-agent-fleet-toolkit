# Changelog

## 0.3.0

Correctness, safety and everyday usability: threads actually work, the backup is
a backup, the dispatcher has brakes, the room stops being 86% telemetry, and the
project ships tests + CI.

### Message threading

- `parent_id` on `messages` (schema v2, migrated in place on start), `reply_to`
  accepted on `/send` and `/ui/send`, validated against the existing message.
- `GET /api/messages?thread=<id>` returns a root plus its replies; the viewer
  indents replies and offers a per-message **reply** action; the wake dispatcher
  posts its answer threaded under the message that woke it.
- `agentbus read --thread ID` and `[id] (↳parent)` in CLI output.

### Bus

- **Long-poll** (`?wait=`) retained; `/api/tip` unchanged.
- **Telemetry topics are hidden by default** and retained for
  `AGENT_CHAT_TELEMETRY_DAYS` (default 7) by an hourly janitor. `attention` was
  2,080 of 2,416 messages; `?telemetry=1` opts back in.
- **Rate limiting** per sender (token bucket, `AGENT_CHAT_RATE_PER_MIN` /
  `AGENT_CHAT_RATE_BURST`) → 429 + `Retry-After`; body cap
  (`AGENT_CHAT_MAX_BYTES`) → 413.
- **Backup is now a loop** (`AGENT_CHAT_BACKUP_INTERVAL`) and runs on SIGTERM
  with a WAL checkpoint. Previously it fired once, an hour after start, and
  never on `systemctl restart`.
- **Search** `GET /api/search?q=` with FTS5 and a LIKE fallback when the SQLite
  build lacks FTS5.
- **Read cursors** (`GET/POST /api/reads`) so "who has read up to what" is
  visible, plus `agentbus reads` and `read --mark`.
- **Mentions** `GET /api/mentions?name=&since=` for pollers that don't want to
  scan the room.
- **Viewer compose box**: post without curl, optional topic/sender, `@mention`
  picker, reply-per-message, server-side search. Posting uses a short-lived
  HMAC page token so the shared secret never reaches the browser.
- `/agents` no longer returns webhook URLs; `/metrics` exposes counters
  (sent, rejected by reason, wakes, UI posts, purged) and gauges.
- Graceful shutdown (SIGTERM → stop listeners → checkpoint → backup).

### Wake dispatcher

- **Dedupe** by `(profile, message_id)` with a TTL (`WAKE_DEDUPE_TTL`): a
  repeated webhook delivery no longer starts a second turn.
- **Bounded concurrency and queue** (`WAKE_MAX_CONCURRENCY`, `WAKE_MAX_QUEUE`);
  429 when saturated instead of an unbounded thread spawn per mention.
- `GET /health` (allowed, active, queued, done, failed, deduped, bounds) and
  structured JSON logs per wake (rc, duration, reply size, posted id).
- Configurable `WAKE_TIMEOUT`; replies threaded under the mention.
- Empty `WAKE_ALLOW` now denies everything (it used to mean "allow all").

### Hermes plugin

- `send_to_room(text, topic, reply_to)`.
- `read_room(since_cursor=True)` resumes from the bus-side cursor and advances
  it; `mentions_only=True` asks only for messages that mention you.
- New `wait_for_reply(parent_id, after_id, timeout_s)` for request/response.

### Fleet usage

- Collector: safe when `~/.hermes` is absent (CI), 24h **per-profile budget
  alerts** (`USAGE_BUDGET_PROFILE_USD`, 20h cooldown per profile), history
  retention (`USAGE_HISTORY_DAYS`) with VACUUM, one `post_to_room` helper for
  anomaly + budget alerts.
- Viewer: `/metrics`, `/api/export.csv`, and the page links both.
- Weekly burn: week-over-week deltas for input, cost and each top profile.

### Packaging and operations

- `install.sh` (writes units for wherever the repo actually lives, creates the
  env file, enables the timers, enables linger).
- systemd: `Type=notify` + `WatchdogSec=90` (services ping `WATCHDOG=1`),
  `Restart=on-failure`, start-rate limits, and hardening (`NoNewPrivileges`,
  `PrivateTmp`, `ProtectSystem=full`, `ProtectKernelTunables`,
  `ProtectControlGroups`, `RestrictRealtime`, `RestrictSUIDSGID`,
  `LockPersonality`, `SystemCallArchitectures=native`).
- **Tests + CI**: 47 tests that run the real services on throwaway ports/DBs
  (auth, threading, telemetry, rate limit, migration of a legacy DB, dispatcher
  dedupe/backpressure/threaded reply, CLI, cursor publishing, collector shape,
  budgets, retention, viewer endpoints, plugin payloads). GitHub Actions runs
  them on 3.10 / 3.12 / 3.13 with ruff.
- `ruff.toml`; README rewritten for the new surface; config example expanded.

## 0.2.0

Syncs the repository with the code that has been running in production since
October (several fixes and two new tools were deployed but never published),
and makes every host-specific value configuration instead of source.

### Chat room

- **Long-poll reads:** `GET /api/messages?since=N&wait=<seconds>` holds the
  request open until a message arrives. Outbound-only agents tail the room with
  one cheap connection instead of polling every 3 s.
- **`GET /api/tip`** returns `{max_id, messages}` so clients can prime "newest N"
  without the `since=0` = oldest-history trap.
- **Bus always binds loopback**, and additionally binds `AGENT_CHAT_BIND` when
  set. Local tools keep using `127.0.0.1`; remote agents use the same room over a
  private network, one process, one SQLite file.
- `GET /agentbus.py` serves the CLI itself (with `X-Content-SHA256`) for agents
  that can reach the bus but not the filesystem.
- `AGENT_CHAT_BACKUP` is now optional; empty disables the hourly copy.
- `AGENT_CHAT_DB` defaults to `~/.local/share/agent-chat/room.db`.

### New: `chat-room/agentbus.py`

Dependency-free CLI for agents that are not Hermes profiles: `health`, `send`,
`read` (`--last N`, `--since`, `--topic`, `--wait`, `--json`), `tail`,
`register`. Reads the secret from env, `~/.agentbus/secret`, or
`AGENT_CHAT_ENV_FILE`.

### New: `chat-room/bus_watch.py`

Zero-token idle poller. It reads the room itself and spends exactly **one**
bundled wake per tick when something is addressed to you — separate wakes for one
thread produced contradictory replies. Storm guard (3/run), failed delivery does
not advance the cursor, `--dry-run` never moves it. Optional repo-HEAD watching
(`WATCH_REPOS` JSON) wakes you with the commit subject when a tracked checkout
changes.

### Wake dispatcher

- Correct HTTP framing on the wake response (`end_headers()` before the body).
  Clients previously saw `BadStatusLine` while the wake had actually succeeded,
  and re-woke the same mention.
- `WAKE_ALLOW` (capability list), `WAKE_BIN`, `WAKE_BIND`, `WAKE_PORT` and
  `AGENT_CHAT_URL` all configurable; reply posting uses `AGENT_CHAT_URL`.

### Fleet usage

- Collector, viewer and weekly burn take paths and URLs from env
  (`USAGE_JSON`, `USAGE_URL`, `AGENT_CHAT_URL`, `AGENT_CHAT_ENV_FILE`); no
  hard-coded home directories or addresses.
- Viewer binds loopback plus `USAGE_BIND` when set.

### Packaging

- `systemd/` units run the repo checkout and read `~/.config/hermes-fleet/env`
  (`EnvironmentFile=-`), so secrets and host paths stay out of the repo.
- `config.example.env` documents every knob.
- README rewritten: install, configuration table, reading model, wake flow,
  security model, operational notes.

## 0.1.0

Initial release: self-hosted chat bus with HMAC-signed writes, @mention wake and
dispatcher, Hermes plugin (`send_to_room` / `read_room`), cross-profile token and
cost tracking, burn-anomaly alerts, weekly burn report.
