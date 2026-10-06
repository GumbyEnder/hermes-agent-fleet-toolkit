#!/usr/bin/env python3
"""Fleet token collector — aggregates session_model_usage across ALL Hermes profiles.

Run periodically (systemd timer). Writes usage.json for the viewer at :8091.
"""
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

HOME = Path.home()
HERMES = Path(os.environ.get("HERMES_HOME", str(HOME / ".hermes")))
OUT = Path(os.environ.get("USAGE_JSON", os.path.expanduser("~/.local/share/fleet-usage/usage.json")))
D = 86400

WINDOWS = {"7d": 7 * D, "30d": 30 * D, "all": None}

# Tailscale/SSH hosts running Hermes: entries "ssh-alias" (assumes ~/.hermes/state.db).
# Unreachable hosts are skipped silently and retried next run.
REMOTES = [h.strip() for h in os.environ.get("AGENT_USAGE_REMOTES", "").split(",") if h.strip()]


def query_profile(profile: str, db_path: Path):
    if not db_path.exists() or db_path.stat().st_size == 0:
        return []
    out = []
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """SELECT session_id, model, billing_mode, api_call_count,
                      input_tokens, output_tokens, cache_read_tokens,
                      cache_write_tokens, reasoning_tokens,
                      estimated_cost_usd, actual_cost_usd, first_seen, last_seen
               FROM session_model_usage"""
        ).fetchall()
        db.close()
        for r in rows:
            out.append(dict(profile=profile, **{k: r[k] for k in r.keys()}))
    except Exception:
        pass
    return out


def query_remote(host: str):
    """Pull remote home's usage rows over ssh (collector must exist remotely, or query its DB directly)."""
    try:
        # run the same collector logic remotely in one shot: dump rows as JSON
        script = (
            "import json,sqlite3,os;rows=[];"
            "dbs=[os.path.expanduser('~/.hermes/state.db')]+"
            "[os.path.expanduser(f'~/.hermes/profiles/{p}/state.db') for p in sorted(os.listdir(os.path.expanduser('~/.hermes/profiles'))) if os.path.isdir(os.path.expanduser(f'~/.hermes/profiles/{p}'))];"
            "for db in dbs:\n"
            " try:\n"
            "  c=sqlite3.connect(f'file:{db}?mode=ro',uri=True,timeout=5);c.row_factory=sqlite3.Row\n"
            "  for r in c.execute('SELECT session_id, model, billing_mode, api_call_count, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens, estimated_cost_usd, actual_cost_usd, first_seen, last_seen FROM session_model_usage'):\n"
            "   rows.append(dict(r))\n"
            " except Exception: pass\n"
            "print(json.dumps(rows))"
        )
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=6", "-o", "BatchMode=yes", host, "python3", "-c", script],
                           capture_output=True, text=True, timeout=40)
        if r.returncode != 0:
            print(f"remote {host}: unreachable ({r.stderr.strip()[:80]})")
            return []
        out = []
        for row in json.loads(r.stdout.strip().splitlines()[-1]):
            row["profile"] = host
            out.append(row)
        print(f"remote {host}: {len(out)} rows")
        return out
    except Exception as e:
        print(f"remote {host}: failed ({e})")
        return []


def top_sessions(raw, now, days=7, limit=10):
    """Most expensive sessions in the last N days (drill-down table)."""
    cut = now - days * D
    rows = [r for r in raw if (r["last_seen"] or 0) >= cut]
    rows.sort(key=lambda r: (r["estimated_cost_usd"] or 0.0), reverse=True)
    return [{
        "session_id": r["session_id"], "profile": r["profile"], "model": r["model"],
        "input": r["input_tokens"] or 0, "output": r["output_tokens"] or 0,
        "reasoning": r["reasoning_tokens"] or 0,
        "est_cost": round(r["estimated_cost_usd"] or 0.0, 2),
        "last_seen": r["last_seen"],
    } for r in rows[:limit]]


def check_anomaly(raw, now, mult=3.0):
    """Alert if last-24h input burn exceeds mult x the 7-day daily median. Cooldown 20h via state file."""
    state = OUT.parent / "anomaly_state.json"
    try:
        cooldown = json.loads(state.read_text()) if state.exists() else {}
    except Exception:
        cooldown = {}
    if now - cooldown.get("last_alert", 0) < 20 * 3600:
        return {"active": False, "reason": "cooldown"}

    day = sum(r["input_tokens"] or 0 for r in raw if (r["last_seen"] or 0) >= now - D)
    # per-day input over last 7 days from cumulative last_seen windows is approximate:
    # use per-row first_seen to bucket each row's day for a stable median
    daily = {}
    for r in raw:
        fs = r["first_seen"] or 0
        if now - 7 * D <= fs <= now - D:
            daily[int((now - fs) // D)] = daily.get(int((now - fs) // D), 0) + (r["input_tokens"] or 0)
    if len(daily) < 3:
        return {"active": False, "reason": "not enough history"}
    vals = sorted(daily.values())
    median = vals[len(vals) // 2]
    if median > 0 and day > mult * median:
        result = {"active": True, "day_input": day, "median_input": median,
                  "multiple": round(day / median, 1)}
        # post to agent chat room (best effort)
        try:
            secret = ""
            for line in (HOME / ".hermes/profiles/frodo/.env").read_text().splitlines():
                if line.startswith("AGENT_CHAT_SECRET="):
                    secret = line.split("=", 1)[1].strip()
            if secret:
                import hashlib, hmac as hm, urllib.request
                text = (f"⚠ Token burn anomaly: last 24h input = {day:,} "
                        f"({result['multiple']}× the 7-day median of {median:,}). "
                        f"Check the Tokens tab: {os.environ.get('USAGE_PUBLIC_URL', 'http://127.0.0.1:8091')}/")
                body = json.dumps({"from": "fleet-usage", "topic": "alerts", "text": text,
                                   "kind": "system"}).encode()
                sig = hm.new(secret.encode(), body, hashlib.sha256).hexdigest()
                req = urllib.request.Request(os.environ.get("AGENT_CHAT_URL", "http://127.0.0.1:8090") + "/send", data=body,
                                             headers={"X-Signature": sig})
                urllib.request.urlopen(req, timeout=8)
                result["posted_to_room"] = True
        except Exception as e:
            result["post_error"] = str(e)
        state.write_text(json.dumps({"last_alert": now}))
        return result
    return {"active": False, "day_input": day, "median_input": median}


def main():
    profiles = sorted(d.name for d in (HERMES / "profiles").iterdir() if d.is_dir())
    raw = []
    for p in profiles:
        raw.extend(query_profile(p, HERMES / "profiles" / p / "state.db"))
    # also the default home (non-profile agents)
    raw.extend(query_profile("default", HERMES / "state.db"))
    for host in REMOTES:
        raw.extend(query_remote(host))

    now = time.time()
    windows = {}
    for wname, span in WINDOWS.items():
        cut = now - span if span else 0
        rows = [r for r in raw if (r["last_seen"] or 0) >= cut]
        by_profile, by_model = {}, {}
        for r in rows:
            pf = r["profile"]
            b = by_profile.setdefault(pf, {"sessions": 0, "input": 0, "output": 0, "cache_read": 0, "reasoning": 0, "est_cost": 0.0, "actual_cost": 0.0, "api_calls": 0})
            m = by_model.setdefault(r["model"] or "?", {"sessions": 0, "input": 0, "output": 0, "cache_read": 0, "reasoning": 0, "est_cost": 0.0, "actual_cost": 0.0})
            vals = (1, r["input_tokens"] or 0, r["output_tokens"] or 0, r["cache_read_tokens"] or 0,
                    r["reasoning_tokens"] or 0, r["estimated_cost_usd"] or 0.0, r["actual_cost_usd"] or 0.0, r["api_call_count"] or 0)
            for k, v in zip(("sessions", "input", "output", "cache_read", "reasoning", "est_cost", "actual_cost"), vals):
                b[k] += v
            for k, v in zip(("sessions", "input", "output", "cache_read", "reasoning", "est_cost", "actual_cost"), vals):
                m[k] += v
            b["api_calls"] += r["api_call_count"] or 0
        total = {"sessions": sum(b["sessions"] for b in by_profile.values()),
                 "input": sum(b["input"] for b in by_profile.values()),
                 "output": sum(b["output"] for b in by_profile.values()),
                 "cache_read": sum(b["cache_read"] for b in by_profile.values()),
                 "reasoning": sum(b["reasoning"] for b in by_profile.values()),
                 "est_cost": sum(b["est_cost"] for b in by_profile.values()),
                 "actual_cost": sum(b["actual_cost"] for b in by_profile.values())}
        # last active timestamp per profile
        last_active = {}
        for r in rows:
            if (r["last_seen"] or 0) > last_active.get(r["profile"], 0):
                last_active[r["profile"]] = r["last_seen"]
        windows[wname] = {"total": total, "by_profile": by_profile, "by_model": by_model,
                          "last_active": last_active, "cutoff": cut}

    # ---- billing split: subscription-included vs API-billable ----
    for wname in windows:
        w = windows[wname]
        rows = [r for r in raw if (r["last_seen"] or 0) >= windows[wname]["cutoff"]]
        billable = included = 0
        for r in rows:
            if r["billing_mode"] == "subscription_included":
                included += r["estimated_cost_usd"] or 0.0
            else:
                billable += r["estimated_cost_usd"] or 0.0
        w["total"]["billable_cost"] = round(billable, 2)
        w["total"]["included_cost"] = round(included, 2)

    # ---- history: append one snapshot per run (sqlite, local) ----
    hist_db = OUT.parent / "history.db"
    conn = sqlite3.connect(hist_db, timeout=10)
    conn.execute("""CREATE TABLE IF NOT EXISTS snapshots (
                       ts REAL PRIMARY KEY, sessions INTEGER, input INTEGER,
                       output INTEGER, cache_read INTEGER, reasoning INTEGER,
                       billable_cost REAL, included_cost REAL)""")
    last = conn.execute("SELECT ts FROM snapshots ORDER BY ts DESC LIMIT 1").fetchone()
    if not last or now - last[0] >= 3500:  # ~hourly, avoid timer-restart dupes
        conn.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?)",
                     (now, windows["all"]["total"]["sessions"], windows["all"]["total"]["input"],
                      windows["all"]["total"]["output"], windows["all"]["total"]["cache_read"],
                      windows["all"]["total"]["reasoning"],
                      windows["all"]["total"]["billable_cost"], windows["all"]["total"]["included_cost"]))
    conn.commit()
    hist = [dict(zip(("ts", "sessions", "input", "output", "cache_read", "reasoning", "billable_cost", "included_cost"), r))
            for r in conn.execute("SELECT * FROM snapshots WHERE ts >= ? ORDER BY ts", (now - 60 * D,))]
    conn.close()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"generated": now, "profiles": profiles, "windows": windows, "history": hist,
                               "top_sessions": top_sessions(raw, now),
                               "anomaly": check_anomaly(raw, now)}))
    print(f"collected {len(raw)} usage rows across {len(profiles)+1} homes -> {OUT}")


if __name__ == "__main__":
    main()
