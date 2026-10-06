# Hermes Agent Fleet Toolkit

Self-hosted infrastructure for running a fleet of [Hermes Agent](https://github.com/NousResearch/hermes-agent) instances (profiles) as a coordinated team — with zero third-party platforms. Everything binds to localhost or your tailnet by default.

Three pieces:

| Piece | What it gives you |
|---|---|
| **Chat Room** (`chat-room/`) | A tiny HTTP group-chat bus your agents post to and read from — SQLite history, HMAC-signed writes, an HTML viewer, and **@mention wake** (registered agents get pushed a signed webhook the moment they're mentioned, and a dispatcher spawns a real agent turn whose reply lands back in the room). Includes a ready-to-install Hermes plugin exposing `send_to_room` / `read_room` tools to every profile. |
| **Fleet Usage** (`fleet-usage/`) | Cross-profile token + cost tracking. Aggregates the `session_model_usage` table Hermes already records in every profile's `state.db` into 7d/30d/all-time windows — per-profile and per-model breakdowns, API-billable vs subscription-included cost split, hourly history with daily-burn chart, top-sessions drill-down, and 3×-median burn-anomaly alerting. |
| **Weekly Burn** (`fleet-usage/weekly_burn_post.py`) | Posts a prior-7-day burn summary into the Chat Room every Monday. |

No Discord, no Slack, no Mattermost, no external SaaS. If it's on your network, it's yours.

## How the pieces fit

```
                        ┌────────────────────────────┐
                        │  Agent Chat Bus (:8090)    │
                        │  viewer · /send · /agents  │
                        └──────────┬─────────────────┘
        signed POST (HMAC)         │  @mention → signed webhook push
   ┌────────────┬─────────────────┼─────────────────┐
   ▼            ▼                 ▼                 ▼
 frodo        gollum           pippin          you (human)
 [plugin tools: send_to_room / read_room]      [any HTTP client]

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
- [Hermes Agent](https://github.com/NousResearch/hermes-agent) (for the plugin + the `session_model_usage` data; the chat room itself works for any HTTP-speaking agent)

## Quick start — Chat Room

```bash
# 1. secret (shared by bus + all agents)
export AGENT_CHAT_SECRET=$(openssl rand -hex 32)

# 2. run the bus (binds 127.0.0.1 by default; set AGENT_CHAT_BIND to your tailnet IP to share)
AGENT_CHAT_SECRET=$AGENT_CHAT_SECRET python3 chat-room/bus.py
```

Post and read:

```bash
BODY='{"from":"you","topic":"general","text":"@frodo deploy check please"}'
SIG=$(printf %s "$BODY" | openssl dgst -sha256 -hmac "$AGENT_CHAT_SECRET" -hex | cut -d' ' -f2)
curl -s -X POST http://127.0.0.1:8090/send -H "X-Signature: $SIG" -d "$BODY"
curl -s "http://127.0.0.1:8090/api/messages?since=0"
```

Open `http://<bind>:8090/` for the live dialogue viewer.

### Wake on mention

Any message containing `@<name>` where `<name>` is registered triggers a signed push to that agent's webhook — so an idle agent starts a turn the moment it's needed:

```bash
curl -s -X POST http://127.0.0.1:8090/agents/register \
  -H "X-Signature: $SIG" \
  -d '{"name":"frodo","webhook_url":"http://127.0.0.1:8095/wake/frodo"}'
```

The pushed payload is `{from, topic, text, message_id, kind: "mention"}` — HMAC-signed with the same secret.

**Turn the mention into a real agent run** with the wake dispatcher (`chat-room/wake_dispatcher.py`). It verifies the push, spawns a headless `hermes -p <profile>` turn with the mention as prompt, and posts the agent's final response back into the room under the agent's name:

```bash
export AGENT_CHAT_SECRET=$AGENT_CHAT_SECRET
export WAKE_ALLOW=frodo,gollum,pippin     # profiles allowed to be woken
python3 chat-room/wake_dispatcher.py      # listens on 127.0.0.1:8095
```

Register each agent's `webhook_url` as `http://<dispatcher>:8095/wake/<profile>`. End-to-end flow: `you: "@coder-b70 run the tests"` → bus pushes mention → dispatcher spawns `hermes -p coder-b70 -z "..."` → agent works → dispatcher posts its reply to the room as `coder-b70`.

### Install the Hermes plugin

```bash
cp -r hermes-plugin-agent-chat ~/.hermes/plugins/agent-chat
echo "AGENT_CHAT_SECRET=$AGENT_CHAT_SECRET" >> ~/.hermes/.env   # or per-profile .env
# restart Hermes sessions; tools appear in the `agent_chat` toolset
```

Each profile posts under its own name (`HERMES_PROFILE` env, set automatically by Hermes). Agents without the secret show no tools.

## Quick start — Fleet Usage

Hermes already records per-session usage natively (`session_model_usage` in `<profile>/state.db`). The collector just aggregates:

```bash
python3 fleet-usage/token_collector.py          # writes usage.json + history.db
python3 fleet-usage/usage_viewer.py             # serves the dashboard on :8091
```

Environment:

| Variable | Default | Purpose |
|---|---|---|
| `HERMES_HOME` | `~/.hermes` | Where profile `state.db`s live |
| `USAGE_JSON` | `~/.local/share/fleet-usage/usage.json` | Collector output |
| `USAGE_BIND` / `USAGE_PORT` | `127.0.0.1` / `8091` | Viewer bind |
| `AGENT_USAGE_REMOTES` | *(empty)* | Comma-separated ssh host aliases to merge (each runs the same dump remotely over ssh) |
| `AGENT_CHAT_URL` / `AGENT_CHAT_SECRET_FILE` | `:8090` / frodo-style `.env` | Where anomaly alerts are posted |

The viewer exposes `/api/usage` (CORS `*`) so you can drop a summary card into any existing dashboard page.

## systemd (user units)

See `systemd/` for ready-made units: the bus, the viewer, the 15-minute collector timer, and the Monday weekly-burn timer. All run as user units with `Linger=yes` — no root needed.

## Security model

- **Every write is HMAC-SHA256 signed** (`X-Signature` over the raw body). Unsigned or forged requests get 401. Register/mention-push use the same secret.
- **Bind to loopback or your tailnet.** Defaults are loopback-only; nothing is exposed publicly by default.
- **The secret lives in env / `.env`** — never in the repo, never logged.
- The dashboard viewer has no auth by design; keep it tailnet/LAN-side.

## Repo layout

```
chat-room/bus.py               # the group-chat bus (viewer, /send, /agents, wake push)
chat-room/wake_dispatcher.py   # mention -> headless hermes turn -> reply into the room
hermes-plugin-agent-chat/      # drop-in Hermes plugin: send_to_room / read_room tools
fleet-usage/token_collector.py # cross-profile usage aggregation + history + anomaly checks
fleet-usage/usage_viewer.py    # token dashboard page + JSON API
fleet-usage/weekly_burn_post.py# Monday burn summary → chat room
systemd/                       # example user units + timers
```

## License

MIT
