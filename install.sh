#!/usr/bin/env bash
# Install the fleet toolkit as user systemd services.
#
#   ./install.sh              install + enable + start
#   ./install.sh --units-only just write the unit files (no daemon-reload/start)
#
# Creates ~/.config/hermes-fleet/env from the example if it does not exist.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
ENV_DIR="$HOME/.config/hermes-fleet"
UNITS_ONLY=0
[[ "${1:-}" == "--units-only" ]] && UNITS_ONLY=1

mkdir -p "$UNIT_DIR" "$ENV_DIR"

if [[ ! -f "$ENV_DIR/env" ]]; then
  cp "$REPO/config.example.env" "$ENV_DIR/env"
  chmod 600 "$ENV_DIR/env"
  echo "wrote $ENV_DIR/env — set AGENT_CHAT_SECRET (and AGENT_CHAT_BIND for remote agents) before starting"
fi

for f in "$REPO"/systemd/*.service "$REPO"/systemd/*.timer; do
  [[ -e "$f" ]] || continue
  # The units use %h/Agents/Projects/hermes-agent-fleet-toolkit as the conventional
  # clone path; rewrite it to wherever this repo actually lives.
  sed "s#%h/Agents/Projects/hermes-agent-fleet-toolkit#${REPO}#g" "$f" > "$UNIT_DIR/$(basename "$f")"
  echo "installed $(basename "$f")"
done

if [[ "$UNITS_ONLY" == "1" ]]; then
  echo "units written to $UNIT_DIR (not reloaded)"
  exit 0
fi

systemctl --user daemon-reload
systemctl --user enable --now \
  hermes-agent-chat-bus.service \
  hermes-wake-dispatcher.service \
  hermes-agent-usage.service \
  hermes-agent-usage-collect.timer \
  hermes-weekly-burn.timer

loginctl enable-linger "$USER" 2>/dev/null || true

echo
echo "status:"
systemctl --user --no-pager --lines=0 status \
  hermes-agent-chat-bus hermes-wake-dispatcher hermes-agent-usage 2>/dev/null || true
echo
echo "next:"
echo "  1. set AGENT_CHAT_SECRET in $ENV_DIR/env, then: systemctl --user restart hermes-agent-chat-bus hermes-wake-dispatcher"
echo "  2. add the same secret to each Hermes profile .env and copy hermes-plugin-agent-chat to ~/.hermes/plugins/agent-chat"
echo "  3. open http://127.0.0.1:8090/ and post something"
