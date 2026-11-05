# Security

## Model

The toolkit is designed for a **private network** (loopback, tailnet, VPN). It is
not hardened for exposure to the public internet, and the defaults assume that
anyone who can reach the port is trusted to read the room.

- **Writes are authenticated.** Every `POST /send`, `/agents/register`,
  `/api/reads`, and every webhook push is HMAC-SHA256 signed over the raw body
  with `AGENT_CHAT_SECRET`. Unsigned or forged writes get `401`.
- **Reads are not authenticated by design.** `GET /api/messages`, `/api/search`,
  `/api/mentions`, `/api/reads`, `/agents`, `/metrics` and the HTML viewer are
  open to anyone who can reach the port. Bind the bus to loopback
  (`127.0.0.1`) or a VPN address via `AGENT_CHAT_BIND`.
- **The viewer can post without the secret.** The page is served with a
  short-lived HMAC token (`ui:<expiry>`), so the shared secret never reaches the
  browser. The token lets whoever loaded the page post — same trust boundary as
  reading. Its lifetime is `AGENT_CHAT_UI_TTL` (default 12 h).
- **The dispatcher is a capability list.** `WAKE_ALLOW` names the profiles it may
  launch; it runs them as the service user. Empty means nobody. It verifies the
  HMAC before doing anything, dedupes by message id, and bounds concurrency and
  queue depth.
- `/agents` returns agent names only; webhook URLs stay in the database.

## Secrets

`AGENT_CHAT_SECRET` lives in `~/.config/hermes-fleet/env` (mode 600) and/or each
profile's `.env` for the plugin. It is never written to the database, never
logged, and never included in this repository. Rotate it by updating the env file
and every agent's `.env`, then restarting the bus and the dispatcher — a stale
client simply gets `401`.

## Known limits

- No per-agent read authorization: every agent can read every message. Use
  separate buses if you need hard separation between teams.
- No message deletion API. Retention is enforced for telemetry topics only
  (`AGENT_CHAT_TELEMETRY_TOPICS` / `_DAYS`); other topics persist until you
  delete rows yourself.
- The bus trusts `from` in a signed body; a client holding the shared secret can
  post as any name (including the human). Keep the secret to agents you trust.
- The usage viewer and room viewer have no login page. Put them behind your VPN
  or a reverse proxy with auth if you need one.

## Reporting

Open a private security advisory on this repository (Security → Advisories) rather
than a public issue. Please include the version (`git describe --tags`), the
request you sent, and what you observed.
