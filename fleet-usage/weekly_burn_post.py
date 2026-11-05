#!/usr/bin/env python3
"""Weekly burn report — posts last-7-day fleet token totals to the Agent Chat Room.

Runs Mondays 09:00 via user systemd timer. Includes a week-over-week delta from
the totals stored at the previous post, so a doubling is visible without opening
the dashboard.

Env: USAGE_JSON, AGENT_CHAT_URL, AGENT_CHAT_SECRET, AGENT_CHAT_ENV_FILE, USAGE_URL.
"""
import hashlib
import hmac
import json
import os
import time
import urllib.request
from pathlib import Path

HOME = Path.home()
OUT = Path(os.environ.get("USAGE_JSON") or (HOME / ".local/share/agent-usage/usage.json"))
ROOM = os.environ.get("AGENT_CHAT_URL", "http://127.0.0.1:8090").rstrip("/") + "/send"
STATE = OUT.parent / ".weekly_state.json"
USAGE_URL = os.environ.get("USAGE_URL") or "http://127.0.0.1:8091/"


def fmt(n):
    return f"{n/1e9:.2f}B" if n >= 1e9 else f"{n/1e6:.1f}M" if n >= 1e6 else f"{n/1e3:.0f}k"


def delta(cur, prev, money=False):
    """' (+12.3%)' / ' (−4.1%)' / '' when there is nothing to compare against."""
    if not prev:
        return ""
    pct = (cur - prev) / prev * 100.0
    sign = "+" if pct >= 0 else "−"
    return f" ({sign}{abs(pct):.1f}% WoW)"


def main():
    data = json.loads(OUT.read_text())
    w = data["windows"]["7d"]
    try:
        state = json.loads(STATE.read_text())
    except (OSError, ValueError):
        state = {}
    now = time.time()
    if now - state.get("ts", 0) < 6 * 86400:      # already ran this week
        return

    t = w["total"]
    prev = state.get("totals") or {}
    lines = [f"📊 Weekly fleet burn (last 7 days, {time.strftime('%b %d')}):",
             "• {:,} sessions · {} input{} · {} output · {} cache-read".format(
                 t["sessions"], fmt(t["input"]), delta(t["input"], prev.get("input")),
                 fmt(t["output"]), fmt(t["cache_read"])),
             "• Est. cost: ${:,.2f}{} (API-billable ${:,.2f} / subscription ${:,.2f})".format(
                 t["est_cost"], delta(t["est_cost"], prev.get("est_cost")),
                 t.get("billable_cost", 0), t.get("included_cost", 0)),
             "• Top agents by input:"]
    for p, b in sorted(w["by_profile"].items(), key=lambda x: -x[1]["input"])[:5]:
        pct = ""
        if prev.get("by_profile", {}).get(p):
            before = prev["by_profile"][p].get("input") or 0
            if before:
                pct = f" ({((b['input'] - before) / before * 100):+.0f}%)"
        lines.append(f"   – {p}: {fmt(b['input'])} in{pct} · ${b['est_cost']:,.2f}")
    budgets = data.get("budgets") or []
    if budgets:
        lines.append("• Budget alerts this week: " + ", ".join(
            f"{b['profile']} ${b['cost_24h']:,.0f}" for b in budgets[:5]))
    lines.append(f"Full breakdown: {USAGE_URL}")

    secret = os.environ.get("AGENT_CHAT_SECRET", "")
    env_file = os.environ.get("AGENT_CHAT_ENV_FILE")
    if not secret and env_file:
        for line in Path(env_file).expanduser().read_text().splitlines():
            if line.startswith("AGENT_CHAT_SECRET="):
                secret = line.split("=", 1)[1].strip().strip("'\"")
    body = json.dumps({"from": "fleet-usage", "topic": "weekly-burn",
                       "text": "\n".join(lines), "kind": "system"}).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(ROOM, data=body, headers={"X-Signature": sig})
    urllib.request.urlopen(req, timeout=10)
    STATE.write_text(json.dumps({"ts": now, "totals": t,
                                 "by_profile": w["by_profile"]}))
    print("weekly burn posted")


if __name__ == "__main__":
    main()
