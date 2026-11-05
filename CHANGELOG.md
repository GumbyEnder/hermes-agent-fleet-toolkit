# Changelog

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
