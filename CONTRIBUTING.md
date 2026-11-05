# Contributing

Small, dependency-free tools. The whole point is that one file runs on a stock
Python 3.10+ with no pip install, so please keep it that way in runtime code
(`tests/` and `.github/` may use what CI can install).

## Before you send a patch

```bash
python3 -m unittest discover -s tests -v      # 47 tests, ~15 s, spawns real services
python3 -m compileall -q chat-room fleet-usage hermes-plugin-agent-chat tests
ruff check .                                  # config: ruff.toml
```

If you touched the bus or the dispatcher, also run it by hand once:

```bash
AGENT_CHAT_SECRET=dev AGENT_CHAT_DB=/tmp/room.db AGENT_CHAT_PORT=18090 python3 chat-room/bus.py &
curl -s localhost:18090/health && curl -s localhost:18090/metrics | head
```

## Rules of the road

- **No new runtime dependencies.** Stdlib only in `chat-room/`, `fleet-usage/`,
  `hermes-plugin-agent-chat/`.
- **Never break old data.** Schema changes go through the migration in
  `bus.py:migrate()` and keep existing rows readable.
- **Every write stays signed.** New write endpoints must verify `X-Signature`
  over the raw body (or use the viewer token scheme for the browser path).
- **Bound everything that can grow.** Queues, threads, history, telemetry: add a
  limit and a config knob, with the safe default on the restrictive side.
- **Config over constants.** Host-specific paths, addresses and profile names
  belong in env (documented in README + `config.example.env`), never in source.
- **Tests are behavioural.** Prefer spawning the real service on a throwaway
  port/DB (`tests/support.py`) over mocking the wire format.
- **One concern per PR**, with a CHANGELOG entry under an `Unreleased` or version
  heading.

## Layout

`chat-room/` bus · dispatcher · CLI · idle poller — `fleet-usage/` collector ·
viewer · weekly report — `hermes-plugin-agent-chat/` the Hermes plugin —
`systemd/` units — `tests/` behavioural tests.

## License

MIT. By contributing you agree your changes are licensed the same way.
