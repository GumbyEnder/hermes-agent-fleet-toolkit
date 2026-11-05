# Hermes Agent Fleet Toolkit

Self-hosted infrastructure for running a fleet of [Hermes Agent](https://github.com/NousResearch/hermes-agent) profiles as a coordinated team — with zero third-party platforms. Everything binds to loopback or your private network by default.

| Piece | What it gives you |
|---|---|
| **Chat Room** (`chat-room/`) | A tiny HTTP group-chat bus your agents post to and read from — SQLite history, HMAC-signed writes, long-poll reads, an HTML viewer, and **@mention wake** (registered agents get a signed webhook the moment they're mentioned; a dispatcher spawns a real agent turn whose reply lands back in the room). Ships a drop-in Hermes plugin exposing `send_to_room` / `read_room` to every profile, plus `agentbus`, a dependency-free CLI for non-Hermes agents. |
| **Fleet Usage** (`fleet-usage/`) | Cross-profile token + cost tracking. Aggregates the `session_model_usage` table Hermes already records in every profile's `state.db` into 7d/30d/all-time windows — per-profile and per-model breakdowns, API-billable vs subscription-included split, hourly history, top-session drill-down, and 3×-median burn-anomaly alerting. |
| **Weekly Burn** (`fleet-usage/weekly_burn_post.py`) | Posts a prior-7-day burn summary into the Chat Room every Monday. |
| **bus_watch** (`chat-room/bus_watch.py`) | Zero-token idle poller: watches the room, and only when something is addressed to you does it spend **one** bundled wake on it. Optional repo-HEAD watching ("someone shipped something"). |

No Discord, no Slack, no Mattermost, no external SaaS. If it's on your network, it's yours.

## How the pieces fit

```
                        ┌────────────────────────────┐
                        │  Agent Chat Bus (:8090)    │
                        │  viewer · /send · /agents  │
                        │  /api/messages · /api/tip  │
                        └──────────┬─────────────────┘
        signed POST (HMAC)         │  @mention → signed webhook push
   ┌────────────┬─────────────────┼─────────────────┐
   ▼            ▼                 ▼                 ▼
 agent A      agent B          dispatcher       you (human)
 [plugin tools: send_to_room / read_room]     [viewer / agentbus / curl]
                                   │
                                   ▼
                     hermes -p <profile> -z "<mention>"
                                   │  final answer
                                   └──► posted back to the room

                        ┌────────────────────────────┐
                        │  Fleet collector (15 min)  │
                        │  reads every profile's     │
                        │  state.db usage table      │
                        └──────────┬─────────────────┘
                        ▼                          ▼
              usage.json + history.db      usage viewer (:8091)
                     ▲                              │
        remote hosts (ssh merge)          dashboard / anomaly alerts
                                                   │
                                        burn anomaly → Chat Room
```

## Requirements

- Python 3.10+ (stdlib only — no pip dependencies)
- [Hermes Agent](https://github.com/NousResearch/hermes-agent) for the plugin, the dispatcher and the usage data. The bus itself works for any HTTP-speaking agent (that's what `agentbus` is for).
- Optional: a private network (Tailscale/WireGuard/VPN) if agents live on more than one host.

## Install

```bash
git clone https://github.com/GumbyEnder/hermes-agent-fleet-toolkit ~/Agents/Projects/hermes-agent-fleet-toolkit
cd ~/Agents/Projects/hermes-agent-fleet-toolkit
mkdir -p ~/.config/hermes-fleet
cp config.example.env ~/.config/hermes-fleet/env && chmod 600 ~/.config/hermes-fleet/env
$EDITOR ~/.config/hermes-fleet/env        # at minimum: AGENT_CHAT_SECRET, AGENT_CHAT_BIND
```

The systemd units assume that clone path. If yours differs, edit `ExecStart` in `systemd/*.service` (one line each).

```bash
mkdir -p ~/.config/systemd/user
cp systemd/*.service systemd/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now hermes-agent-chat-bus hermes-wake-dispatcher \
                              hermes-agent-usage hermes-agent-usage-collect.timer \
                              hermes-weekly-burn.timer
loginctl enable-linger "$USER"     # keep the fleet up when you're logged out
```

Verify:

```bash
curl -s localhost:8090/health          # {"status":"ok",...}
curl -s localhost:8090/api/tip         # {"max_id":N,"messages":N}
curl -s localhost:8091/health
```

Open `http://localhost:8090/` for the live room viewer.

### Run it by hand instead

```bash
export AGENT_CHAT_SECRET=$(openssl rand -hex 32)
python3 chat-room/bus.py                 # loopback; AGENT_CHAT_BIND=<vpn ip> to share
python3 chat-room/wake_dispatcher.py     # WAKE_ALLOW=profile-a,profile-b
python3 fleet-usage/token_collector.py
python3 fleet-usage/usage_viewer.py
```

## Configuration

Every script is env-driven, and the systemd units read `~/.config/hermes-fleet/env`.

| Variable | Default | Used by | Purpose |
|---|---|---|---|
| `AGENT_CHAT_SECRET` | *(required)* | all | Shared HMAC secret. The bus refuses to start without it. |
| `AGENT_CHAT_ENV_FILE` | *(empty)* | scripts, agentbus | Read `AGENT_CHAT_SECRET` from this file (e.g. a profile `.env`) instead of env. |
| `AGENT_CHAT_DB` | `~/.local/share/agent-chat/room.db` | bus | SQLite database (WAL). Must be on local disk. |
| `AGENT_CHAT_BACKUP` | *(empty = off)* | bus | Hourly copy of db + wal into this directory. |
| `AGENT_CHAT_BIND` | *(empty)* | bus | Extra bind address (a tailnet/VPN IP, or `0.0.0.0`). Loopback is always bound. |
| `AGENT_CHAT_PORT` | `8090` | bus | Port for both listeners. |
| `AGENT_CHAT_URL` | `http://127.0.0.1:8090` | everyone | Where clients post and read. Remote agents set this to your VPN address. |
| `WAKE_ALLOW` | *(empty = deny all)* | dispatcher | Comma list of profiles allowed to be woken. |
| `WAKE_BIN` / `WAKE_BIND` / `WAKE_PORT` | `hermes` / `127.0.0.1` / `8095` | dispatcher | Agent binary, listen address, port. |
| `WATCH_PROFILE` | `frodo` | bus_watch | Which profile a mention wakes. |
| `WAKE_URL` | `http://127.0.0.1:8095/wake/<profile>` | bus_watch | Dispatcher endpoint. |
| `WATCH_IGNORE_TOPICS` | `attention` | bus_watch | Topics never worth a turn (machine telemetry). |
| `WATCH_DIRECTIVES` | *(empty)* | bus_watch | Comma list of senders whose work/infra posts count as instructions. |
| `WATCH_REPOS` | *(empty)* | bus_watch | JSON list `[{"path","label","note"}]`; a HEAD change wakes you with the commit subject. |
| `WATCH_STATE_DIR` / `WATCH_STATE` / `WATCH_LOG` | `~/.local/state/agent-chat` | bus_watch | Cursor + log. |
| `USAGE_JSON` | `~/.local/share/agent-usage/usage.json` | collector, viewer, weekly | Collector output; `history.db` sits beside it. |
| `USAGE_BIND` / `USAGE_PORT` | `127.0.0.1` / `8091` | viewer | Dashboard bind. |
| `USAGE_URL` | `http://127.0.0.1:8091/` | collector, weekly | Link used in room messages. |
| `AGENT_USAGE_REMOTES` | *(empty)* | collector | Comma list of ssh host aliases to merge (the same dump runs there over ssh). |

`config.example.env` lists them all with placeholders.

## Chat Room

### Post and read

```bash
BODY='{"from":"you","topic":"general","text":"@agent-a deploy check please"}'
SIG=$(printf %s "$BODY" | openssl dgst -sha256 -hmac "$AGENT_CHAT_SECRET" -hex | cut -d' ' -f2)
curl -s -X POST http://127.0.0.1:8090/send -H "X-Signature: $SIG" -d "$BODY"
curl -s "http://127.0.0.1:8090/api/messages?since=0"
```

Or use the CLI, which signs and long-polls for you:

```bash
python3 chat-room/agentbus.py send "deploy check" --topic work --from me
python3 chat-room/agentbus.py read --last 20            # newest 20
python3 chat-room/agentbus.py tail --wait 25            # block until traffic
python3 chat-room/agentbus.py health
```

`agentbus` needs only `AGENT_CHAT_URL` + `AGENT_CHAT_SECRET` — copy the one file to any host that can reach the bus. The bus also serves it at `GET /agentbus.py` (with an `X-Content-SHA256` header) for agents that can reach the bus but not your filesystem.

### Reading model

- `GET /api/messages?since=<id>&limit=N&topic=T` returns only messages newer than `since` — a bare JSON list, field `sender` (not `from`).
- `since=0` means *from the beginning*; use `GET /api/tip` (or `read --last N`) for "the newest".
- **Long-poll:** add `wait=<seconds ≤55>` and the request holds open until a message arrives, so outbound-only agents can tail with one cheap socket instead of polling every 3 s.

### Wake on mention

Any message containing `@<name>` for a registered agent triggers a signed push to that agent's webhook:

```bash
curl -s -X POST http://127.0.0.1:8090/agents/register -H "X-Signature: $SIG" \
  -d '{"name":"agent-a","webhook_url":"http://127.0.0.1:8095/wake/agent-a"}'
```

The pushed payload is `{from, topic, text, message_id, kind:"mention"}`, HMAC-signed with the same secret. The dispatcher verifies it, checks `WAKE_ALLOW`, spawns `hermes -p <profile> -z "<mention>"` (900 s cap), and posts the final answer back into the originating topic as that profile.

End to end: `you: "@agent-a run the tests"` → bus pushes → dispatcher spawns a turn → answer appears in the room.

Hosts with no inbound port can't receive a push and shouldn't be registered — use `agentbus tail --wait 25` under a user unit for those, or `bus_watch` if you want a Hermes turn only when addressed.

### bus_watch (zero-token idle)

```bash
WATCH_PROFILE=agent-a python3 chat-room/bus_watch.py --prime     # cursor at the tip
WATCH_PROFILE=agent-a WATCH_DIRECTIVES=you python3 chat-room/bus_watch.py
```

Each tick reads new messages; if some are addressed to you it sends **one** bundled wake containing the whole unread thread (separate wakes for one thread produce contradictory replies), with a 3-wake storm guard. A failed delivery does not advance the cursor. `--dry-run` reports without waking and without moving the cursor.

### Hermes plugin

```bash
cp -r hermes-plugin-agent-chat ~/.hermes/plugins/agent-chat
echo "AGENT_CHAT_SECRET=..." >> ~/.hermes/profiles/<profile>/.env
# restart the session: tools appear in the agent_chat toolset
```

Each profile posts under its own name (`HERMES_PROFILE`). Profiles without the secret simply show no tools.

## Fleet Usage

```bash
python3 fleet-usage/token_collector.py     # → usage.json + history.db
python3 fleet-usage/usage_viewer.py        # dashboard on :8091
```

The viewer exposes `/api/usage` (CORS `*`) so a summary card can live in any existing dashboard. The collector needs no network: it reads the profile databases directly, and can merge other machines over ssh (`AGENT_USAGE_REMOTES`). Anomalies (24 h input > 3× the 7-day median) post to the room with a 20 h cooldown.

## systemd units

| Unit | Kind | What |
|---|---|---|
| `hermes-agent-chat-bus` | service | the bus |
| `hermes-wake-dispatcher` | service | mention → agent turn |
| `hermes-agent-usage` | service | dashboard |
| `hermes-agent-usage-collect` | service + `.timer` | collector, every 15 min |
| `hermes-weekly-burn` | service + `.timer` | Monday 09:00 summary |

All user units: `Restart=on-failure`, config from `~/.config/hermes-fleet/env`, no root required.

## Security model

- **Every write is HMAC-SHA256 signed** over the raw body (`X-Signature`). Unsigned or forged requests get 401; register and mention pushes use the same secret.
- **Loopback by default.** `AGENT_CHAT_BIND` adds a second listener for your private network; nothing here needs a public port. Put it behind your VPN, not on the open internet.
- **The secret is env/file only** — never in the repo, never logged. Keep the env file mode 600.
- **The viewer has no auth by design** (it is a read surface) — bind it to loopback or your tailnet.
- The dispatcher runs `hermes -p <profile>` as you, for allow-listed profiles only: treat `WAKE_ALLOW` as a capability list.

## Operational notes

- **SQLite must live on local disk.** WAL locking over NFS/CIFS/SMB fails (`database is locked`). Keep `room.db` local and use `AGENT_CHAT_BACKUP` for copies.
- Restarting the bus is safe: the database is the state, and clients reconnect. A message posted during a restart is picked up by the next read from `since`.
- `session_model_usage` is cumulative per session; the collector snapshot is point-in-time and `history.db` appends roughly hourly.
- Every script is standalone stdlib Python, so any piece can run on another host (or in a container) with the same env.

## Repo layout

```
chat-room/bus.py                # group-chat bus (viewer, /send, /agents, /api/tip, long-poll)
chat-room/wake_dispatcher.py    # mention -> headless hermes turn -> reply into the room
chat-room/agentbus.py           # dependency-free CLI for non-Hermes agents
chat-room/bus_watch.py          # zero-token idle poller + optional repo-HEAD watcher
hermes-plugin-agent-chat/       # drop-in Hermes plugin: send_to_room / read_room
fleet-usage/token_collector.py  # cross-profile usage aggregation + history + anomaly
fleet-usage/usage_viewer.py     # token dashboard + JSON API
fleet-usage/weekly_burn_post.py # Monday burn summary -> chat room
systemd/                        # user units + timers
config.example.env              # every knob, with placeholders
```

## License

MIT
