#!/usr/bin/env python3
"""Weekly burn report — posts last-7-day fleet token totals to the Agent Chat Room.

Runs Mondays 09:00 via user systemd timer.
"""
import hashlib
import os
import hmac
import json
import time
import urllib.request
from pathlib import Path

HOME = Path.home()
OUT = Path(os.environ.get("USAGE_JSON", os.path.expanduser("~/.local/share/fleet-usage/usage.json")))
ROOM = os.environ.get("AGENT_CHAT_URL", "http://127.0.0.1:8090") + "/send"
STATE = Path(os.environ.get("WEEKLY_STATE", os.path.expanduser("~/.local/share/fleet-usage/.weekly_state.json")))


def fmt(n):
    return f"{n/1e9:.2f}B" if n >= 1e9 else f"{n/1e6:.1f}M" if n >= 1e6 else f"{n/1e3:.0f}k"


def main():
    data = json.loads(OUT.read_text())
    w = data["windows"]["7d"]
    last_run = json.loads(STATE.read_text())["ts"] if STATE.exists() else 0
    now = time.time()
    if now - last_run < 6 * 86400:  # already ran this week
        return

    t = w["total"]
    lines = [f"📊 Weekly fleet burn (last 7 days, {time.strftime('%b %d')}):",
             f"• {t['sessions']:,} sessions · {fmt(t['input'])} input · {fmt(t['output'])} output · {fmt(t['cache_read'])} cache-read",
             f"• Est. cost: ${t['est_cost']:,.2f} (API-billable ${t.get('billable_cost', 0):,.2f} / subscription ${t.get('included_cost', 0):,.2f})",
             "• Top agents by input:"]
    for p, b in sorted(w["by_profile"].items(), key=lambda x: -x[1]["input"])[:5]:
        lines.append(f"   – {p}: {fmt(b['input'])} in · ${b['est_cost']:,.2f}")
    lines.append("Full breakdown: " + os.environ.get("USAGE_PUBLIC_URL", "http://127.0.0.1:8091") + "/")

    secret = ""
    for line in (HOME / ".hermes/profiles/frodo/.env").read_text().splitlines():
        if line.startswith("AGENT_CHAT_SECRET="):
            secret = line.split("=", 1)[1].strip()
    body = json.dumps({"from": "fleet-usage", "topic": "weekly-burn",
                       "text": "\n".join(lines), "kind": "system"}).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(ROOM, data=body, headers={"X-Signature": sig})
    urllib.request.urlopen(req, timeout=10)
    STATE.write_text(json.dumps({"ts": now}))
    print("weekly burn posted")


if __name__ == "__main__":
    main()
