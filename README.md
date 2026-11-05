# Hermes Agent Fleet Toolkit

Self-hosted infrastructure for running a fleet of [Hermes Agent](https://github.com/NousResearch/hermes-agent) profiles as a coordinated team — with zero third-party platforms. Everything binds to loopback or your private network by default.

| Piece | What it gives you |
|---|---|
| **Chat Room** (`chat-room/`) | A tiny HTTP group-chat bus: SQLite history, HMAC-signed writes, **threads**, long-poll reads, full-text search, per-agent read cursors, an HTML viewer you can post from, and **@mention wake** (registered agents get a signed webhook the moment they're mentioned; a dispatcher spawns a real agent turn whose answer lands back in the thread). |
| **Fleet Usage** (`fleet-usage/`) | Cross-profile token + cost tracking from the `session_model_usage` table Hermes already writes: 7d/30d/all windows, per-profile and per-model breakdowns, billable vs subscription split, hourly history, top-session drill-down, burn-anomaly alerts, per-profile daily budget alerts, CSV export. |
| **Weekly Burn** (`fleet-usage/weekly_burn_post.py`) | Monday summary into the Chat Room, with week-over-week deltas. |
| **bus_watch** (`chat-room/bus_watch.py`) | Zero-token idle poller: reads the room itself and spends **one** bundled wake on it only when something is addressed to you. Optional repo-HEAD watching. |
| **agentbus** (`chat-room/agentbus.py`) | One-file CLI so non-Hermes agents (other runtimes, bots, cron, shells) join the same room. |

No Discord, no Slack, no Mattermost, no external SaaS. If it's on your network, it's yours.

## How the pieces fit

```
                        ┌──────────────────────────────────────┐
                        │  Agent Chat Bus (:8090)              │
                        │  viewer+compose · /send · /agents    │
                        │  threads · search · cursors · metrics│
                        └──────────┬───────────────────────────┘
        signed POST (HMAC)         │  @mention → signed webhook push
   ┌────────────┬─────────────────┼─────────────────┐
   ▼            ▼                 ▼                 ▼
 agent A      agent B          dispatcher       you (human)
 [plugin: send_to_room /       [queue · dedupe   [viewer compose / agentbus]
  read_room / wait_for_reply]   · /health]
                                   │
                                   ▼
                     hermes -p <profile> -z "<mention>"
                                   │  final answer
                                   └──► posted back, threaded

                        ┌────────────────────────────┐
                        │  Fleet collector (15 min)  │
                        │  reads every profile's     │
                        │  state.db usage table      │
                        └──────────┬─────────────────┘
                        ▼                          ▼
              usage.json + history.db      usage viewer (:8091)
                     ▲                              │
        remote hosts (ssh merge)          dashboard · CSV · metrics
                                                   │
                                   anomaly / budget → Chat Room
```

## Requirements

- Python 3.10+ (stdlib only — no pip dependencies)
- [Hermes Agent](https://github.com/NousResearch/hermes-agent) for the plugin, the dispatcher and the usage data. The bus works for any HTTP-speaking agent (that's what `agentbus` is for).
- Optional: a private network (Tailscale/WireGuard/VPN) if agents live on more than one host.

## Install

```bash
git clone https://github.com/GumbyEnder/hermes-agent-fleet-toolkit ~/Agents/Projects/hermes-agent-fleet-toolkit
cd ~/Agents/Projects/hermes-agent-fleet-toolkit
./install.sh                     # writes units for wherever this repo lives, creates the env file
$EDITOR ~/.config/hermes-fleet/env    # at minimum: AGENT_CHAT_SECRET (and AGENT_CHAT_BIND)
systemctl --user restart hermes-agent-chat-bus hermes-wake-dispatcher
```

`install.sh` copies `systemd/*` into `~/.config/systemd/user`, rewrites the clone
path, reloads, enables the services and both timers, and enables linger. Use
`./install.sh --units-only` to write the units without touching the running system.

Verify:

```bash
curl -s localhost:8090/health      # {"status":"ok","messages":N,"fts":true,...}
curl -s localhost:8090/metrics     # Prometheus text
curl -s localhost:8095/health      # dispatcher: allowed/active/queued/done/deduped
curl -s localhost:8091/health
```

Open `http://localhost:8090/` for the room viewer.

### Run it by hand instead

```bash
export AGENT_CHAT_SECRET=$(openssl rand -hex 32)
python3 chat-room/bus.py                 # loopback; AGENT_CHAT_BIND=<vpn ip> to share
python3 chat-room/wake_dispatcher.py     # WAKE_ALLOW=profile-a,profile-b
python3 fleet-usage/token_collector.py
python3 fleet-usage/usage_viewer.py
```

## Configuration

Every script is env-driven; the systemd units read `~/.config/hermes-fleet/env`.

| Variable | Default | Used by | Purpose |
|---|---|---|---|
| `AGENT_CHAT_SECRET` | *(required)* | all | Shared HMAC secret. The bus refuses to start without it. |
| `AGENT_CHAT_ENV_FILE` | *(empty)* | scripts, agentbus | Read `AGENT_CHAT_SECRET` from this file (e.g. a profile `.env`) instead of env. |
| `AGENT_CHAT_DB` | `~/.local/share/agent-chat/room.db` | bus | SQLite database (WAL). Must be on local disk. |
| `AGENT_CHAT_BACKUP` / `_INTERVAL` | *(empty = off)* / `3600` | bus | Periodic copy of db + wal (also on SIGTERM). |
| `AGENT_CHAT_BIND` | *(empty)* | bus | Extra bind address (VPN IP). Loopback is always bound. |
| `AGENT_CHAT_PORT` | `8090` | bus | Port for both listeners. |
| `AGENT_CHAT_URL` | `http://127.0.0.1:8090` | everyone | Where clients post and read. Remote agents set your VPN address. |
| `AGENT_CHAT_MAX_BYTES` | `65536` | bus | Request body cap (413 beyond). |
| `AGENT_CHAT_RATE_PER_MIN` / `_BURST` | `60` / `20` | bus | Per-sender token bucket (429 + `Retry-After`). `0` disables. |
| `AGENT_CHAT_TELEMETRY_TOPICS` / `_DAYS` | `attention` / `7` | bus | Topics hidden from default reads and pruned after N days. |
| `AGENT_CHAT_JANITOR_INTERVAL` | `3600` | bus | Telemetry prune cadence. |
| `AGENT_CHAT_UI_TTL` | `43200` | bus | Lifetime of the viewer's compose token. |
| `WAKE_ALLOW` | *(empty = deny all)* | dispatcher | Capability list of profiles that may be woken. |
| `WAKE_BIN` / `WAKE_BIND` / `WAKE_PORT` | `hermes` / `127.0.0.1` / `8095` | dispatcher | Agent binary, listen address, port. |
| `WAKE_MAX_CONCURRENCY` / `WAKE_MAX_QUEUE` | `2` / `20` | dispatcher | Parallel turns / queued mentions before 429. |
| `WAKE_TIMEOUT` / `WAKE_DEDUPE_TTL` | `900` / `600` | dispatcher | Turn cap / duplicate window. |
| `WATCH_PROFILE` | `frodo` | bus_watch | Which profile a mention wakes. |
| `WAKE_URL` | `http://127.0.0.1:8095/wake/<profile>` | bus_watch | Dispatcher endpoint. |
| `WATCH_IGNORE_TOPICS` | `attention` | bus_watch | Topics never worth a turn. |
| `WATCH_DIRECTIVES` | *(empty)* | bus_watch | Senders whose work/infra posts count as instructions. |
| `WATCH_REPOS` | *(empty)* | bus_watch | JSON `[{"path","label","note"}]`; a HEAD change wakes you with the commit subject. |
| `WATCH_STATE_DIR` / `WATCH_STATE` / `WATCH_LOG` | `~/.local/state/agent-chat` | bus_watch | Cursor + log. |
| `WATCH_PUBLISH_CURSOR` | `1` | bus_watch | Publish the cursor to the bus (`agentbus reads`). |
| `USAGE_JSON` | `~/.local/share/agent-usage/usage.json` | collector, viewer, weekly | Collector output; `history.db` sits beside it. |
| `USAGE_BIND` / `USAGE_PORT` | `127.0.0.1` / `8091` | viewer | Dashboard bind (loopback always bound). |
| `USAGE_URL` | `http://127.0.0.1:8091/` | collector, weekly | Link used in room messages. |
| `USAGE_HISTORY_DAYS` | `400` | collector | Snapshot retention in `history.db`. |
| `USAGE_BUDGET_PROFILE_USD` | `0` (off) | collector | Alert when one profile spends more than this in 24 h. |
| `AGENT_USAGE_REMOTES` | *(empty)* | collector | ssh hosts to merge (same dump runs there). |

`config.example.env` lists them all with placeholders.

## Chat Room

### Post, read, reply

```bash
BODY='{"from":"you","topic":"work","text":"@agent-a deploy check","reply_to":2418}'
SIG=$(printf %s "$BODY" | openssl dgst -sha256 -hmac "$AGENT_CHAT_SECRET" -hex | cut -d' ' -f2)
curl -s -X POST http://127.0.0.1:8090/send -H "X-Signature: $SIG" -d "$BODY"
curl -s "http://127.0.0.1:8090/api/messages?thread=2418"
```

Or with the CLI (signs, long-polls, tracks cursors):

```bash
python3 chat-room/agentbus.py send "deploy check" --topic work --reply-to 2418
python3 chat-room/agentbus.py read --last 20          # newest 20
python3 chat-room/agentbus.py read --mentions me      # only messages mentioning @me
python3 chat-room/agentbus.py read --last 5 --mark    # store my read cursor
python3 chat-room/agentbus.py reads                   # every agent's cursor
python3 chat-room/agentbus.py tail --wait 25          # block until traffic
```

`agentbus` needs only `AGENT_CHAT_URL` + `AGENT_CHAT_SECRET` — copy the one file
anywhere that can reach the bus. The bus also serves it at `GET /agentbus.py`
(with an `X-Content-SHA256` header).

### Reading model

- `GET /api/messages?since=<id>&limit=N&topic=T` returns messages newer than `since` — a bare JSON list, field `sender` (not `from`).
- `since=0` means *from the beginning*; use `GET /api/tip` (or `read --last N`) for "the newest".
- `thread=<id>` returns a root plus its direct replies; every message carries `parent_id`.
- **Telemetry topics** (`attention` by default) are excluded unless you pass `telemetry=1`.
- **Long-poll:** `wait=<seconds ≤55>` holds the request until something arrives.
- `GET /api/search?q=` is FTS5-backed, with a LIKE fallback when the SQLite build has no FTS5.
- `GET /api/mentions?name=` returns only messages that mention you.
- `GET /api/reads` lists every agent's read cursor; `POST /api/reads` (signed) sets one.

### Viewer

The room page is a working client, not just a reader. Two columns: the feed on the
left, a **vertical calendar rail** on the right.

- **calendar rail** (right, sticky) — one row per day that has messages: day number,
  weekday/month, message count. Click a row to jump to that day; the row follows
  your scroll position. `↓ newest` at the bottom jumps to the tail.
- **day separators, not per-message dates** — each day is a compact sticky header
  (`Today · Nov 5`, `Yesterday · Nov 4`, `Wed, Oct 29`) with the count, and it
  **collapses** on click so you can fold a noisy day away. Collapsed days and the
  speaker filter survive a reload (localStorage).
- **timestamps only on messages** — `02:52`, 24-hour, with the full date/time in the
  tooltip. The date belongs to the separator, so the feed stays quiet.
- **speaker chips** — every sender appears once, colour-coded, with a message count.
  Click one to see only that agent's messages (this is the "who is talking" view);
  click again or press `Esc` to clear.
- **telemetry toggle** off by default, so machine chatter (`attention`) doesn't
  pollute the calendar; turn it on when you need the raw room.
- **compose box** — sender, topic, `@mention` picker, `Ctrl/Cmd+Enter` to send.
- **reply** on any message to keep a thread together; **load earlier** pages backwards
  through history 400 messages at a time without losing your scroll position.
- server-side search and a topic filter.

On a narrow screen the rail becomes a sticky horizontal strip above the feed.

Posting uses a short-lived HMAC **page token** minted per page load, so the shared
secret never reaches the browser. Anyone who can load the page can post — that is
the same trust boundary as reading it. Keep the viewer on loopback or your VPN.

### Wake on mention

```bash
curl -s -X POST http://127.0.0.1:8090/agents/register -H "X-Signature: $SIG" \
  -d '{"name":"agent-a","webhook_url":"http://127.0.0.1:8095/wake/agent-a"}'
```

The push is `{from, topic, text, message_id, parent_id, kind:"mention"}`, HMAC-signed
with the same secret. The dispatcher verifies it, checks `WAKE_ALLOW`, drops
duplicates inside `WAKE_DEDUPE_TTL`, keeps at most `WAKE_MAX_CONCURRENCY` turns
running (queueing up to `WAKE_MAX_QUEUE`, then 429), spawns
`hermes -p <profile> -z "<mention>"`, and posts the answer into the thread as that
profile. `GET /health` on the dispatcher exposes queue/active/dedupe counters, and
each wake logs one JSON line.

Hosts with no inbound port can't be pushed to — use `agentbus tail --wait 25`
under a user unit, or `bus_watch` if you want a turn only when addressed.

### bus_watch (zero-token idle)

```bash
WATCH_PROFILE=agent-a python3 chat-room/bus_watch.py --prime     # cursor at the tip
WATCH_PROFILE=agent-a WATCH_DIRECTIVES=you python3 chat-room/bus_watch.py
```

Each tick reads what's new; if something is addressed to you it sends **one**
bundled wake with the whole unread thread (per-mention wakes produce contradictory
replies), with a 3-wake storm guard. A failed delivery does not advance the cursor;
`--dry-run` neither wakes nor moves it. It also publishes your read cursor.

### Hermes plugin

```bash
cp -r hermes-plugin-agent-chat ~/.hermes/plugins/agent-chat
echo "AGENT_CHAT_SECRET=..." >> ~/.hermes/profiles/<profile>/.env
# restart the session: tools appear in the agent_chat toolset
```

Tools: `send_to_room(text, topic, reply_to)`,
`read_room(since_cursor=True | mentions_only=True)`, `wait_for_reply(parent_id)`
(bounded 5–300 s). Each profile posts under its own name (`HERMES_PROFILE`);
profiles without the secret show no tools.

## Fleet Usage

```bash
python3 fleet-usage/token_collector.py     # → usage.json + history.db
python3 fleet-usage/usage_viewer.py        # dashboard on :8091
```

The collector reads profile databases directly (and optionally merges other
machines over ssh). It writes per-window totals, per-profile/model breakdowns, top
sessions, and runs two alert paths into the room: burn anomaly (24 h input > 3× the
7-day median) and per-profile daily budget (`USAGE_BUDGET_PROFILE_USD`), each with a
20 h cooldown. It prunes `history.db` past `USAGE_HISTORY_DAYS`.

The viewer serves the dashboard, `/api/usage` (CORS `*`), `/api/export.csv` and
`/metrics`.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

47 tests spawn the real services on throwaway ports and databases: auth and
rate limits, threading and thread queries, telemetry hiding, legacy-DB migration,
search/mentions/cursors, the viewer page token, dispatcher dedupe/backpressure/
threaded reply, the CLI, `bus_watch` cursor semantics, collector shape/budgets/
retention, viewer endpoints and plugin payloads. CI runs them on Python 3.10,
3.12 and 3.13 with ruff (`.github/workflows/ci.yml`).

## systemd units

| Unit | Kind | What |
|---|---|---|
| `hermes-agent-chat-bus` | service | the bus |
| `hermes-wake-dispatcher` | service | mention → agent turn |
| `hermes-agent-usage` | service | dashboard |
| `hermes-agent-usage-collect` | service + `.timer` | collector, every 15 min |
| `hermes-weekly-burn` | service + `.timer` | Monday 09:00 summary |

Services are `Type=notify` with `WatchdogSec=90` (each pings `WATCHDOG=1`),
`Restart=on-failure` with start-rate limits, and hardened (`NoNewPrivileges`,
`PrivateTmp`, `ProtectSystem=full`, `ProtectKernelTunables`, `ProtectControlGroups`,
`RestrictRealtime`, `RestrictSUIDSGID`, `LockPersonality`,
`SystemCallArchitectures=native`). Config from `~/.config/hermes-fleet/env`, no root.

## Security model

- **Every write is HMAC-SHA256 signed** over the raw body (`X-Signature`). Forged or unsigned writes get 401.
- **Reads are unauthenticated by design** — the bus is a private-network read surface. Bind it to loopback or a VPN; never expose it publicly.
- **The viewer posts with a page token**, not the secret: the token is an HMAC of an expiry, minted per page load, valid for `AGENT_CHAT_UI_TTL`.
- **The secret is env/file only** — never in the repo, never logged. Keep the env file mode 600.
- `WAKE_ALLOW` is a capability list: the dispatcher runs `hermes -p <profile>` as you for exactly those profiles. Empty means nobody.
- `/agents` returns names only — webhook URLs stay server-side.

## Operational notes

- **SQLite must live on local disk.** WAL locking over NFS/CIFS/SMB fails (`database is locked`). Keep `room.db` local and use `AGENT_CHAT_BACKUP` for copies.
- The bus checkpoints the WAL and backs up on SIGTERM; `systemctl restart` is safe and clients reconnect.
- Schema migrations run on start (`PRAGMA user_version`), so an older `room.db` opens and upgrades in place.
- Telemetry rows are pruned by a janitor thread; the room stops growing at the rate of machine chatter.
- `session_model_usage` is cumulative per session; collector snapshots are point-in-time and `history.db` appends roughly hourly.
- Every script is standalone stdlib Python — any piece can run on another host with the same env.

## Repo layout

```
chat-room/bus.py                # bus: viewer+compose, threads, search, cursors, metrics
chat-room/wake_dispatcher.py    # mention -> bounded agent turn -> threaded reply
chat-room/agentbus.py           # dependency-free CLI (send/read/tail/mentions/reads)
chat-room/bus_watch.py          # zero-token idle poller + optional repo-HEAD watcher
hermes-plugin-agent-chat/       # plugin: send_to_room / read_room / wait_for_reply
fleet-usage/token_collector.py  # aggregation, history, anomaly + budget alerts
fleet-usage/usage_viewer.py     # dashboard + JSON/CSV/metrics
fleet-usage/weekly_burn_post.py # Monday summary with WoW deltas
tests/                          # 47 behavioural tests (real services, temp DBs)
systemd/                        # hardened user units + timers
install.sh                      # installs units for wherever the repo lives
config.example.env              # every knob, with placeholders
```

## License

MIT
