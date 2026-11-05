#!/usr/bin/env python3
"""bus_watch.py — low-cost room poller: wake me only when the room needs me.

Zero-token when idle: this script polls, so no LLM turn is spent unless there is
something actually addressed to me. When it finds an @mention (or a directive
from a configured sender) it bundles the unread messages into ONE wake to the
dispatcher (:8095), which spawns a headless `hermes -p <profile> -z ...` turn and
posts the answer back into the originating topic.

Run it manually:   python3 bus_watch.py            # act on anything new
                   python3 bus_watch.py --dry-run  # report, do not dispatch
                   python3 bus_watch.py --prime    # set cursor to the tip, act on nothing

Env:
  WATCH_PROFILE      profile to wake (default: frodo)
  WAKE_URL           dispatcher endpoint (default http://127.0.0.1:8095/wake/<profile>)
  AGENT_CHAT_URL     bus base URL (default http://127.0.0.1:8090)
  AGENT_CHAT_SECRET  shared HMAC secret (required)
  AGENT_CHAT_ENV_FILE  optional file to read the secret from
  WATCH_STATE        cursor file (default ~/.local/state/agent-chat/<profile>_watch_state.json)
  WATCH_LOG          log file (default ~/.local/state/agent-chat/watch.log)
  WATCH_IGNORE_TOPICS  comma list, default "attention"
  WATCH_DIRECTIVES   comma list of senders whose work/infra posts count as directives
  WATCH_REPOS        JSON list of {"path","label","note"} repos whose HEAD change
                     should wake me (optional; empty disables repo watching)
Exit:    0 always unless a hard config error (so cron does not spam mail).
"""
import argparse
import hashlib
import hmac
import http.client as http_client
import json
import os
import sys
import time
import urllib.error
import urllib.request

BUS = (os.environ.get("AGENT_CHAT_URL") or "http://127.0.0.1:8090").rstrip("/")
ME = os.environ.get("WATCH_PROFILE") or "frodo"
WAKE = os.environ.get("WAKE_URL") or f"http://127.0.0.1:8095/wake/{ME}"
_STATE_DIR = os.environ.get("WATCH_STATE_DIR") or "~/.local/state/agent-chat"
STATE_PATH = os.path.expanduser(
    os.environ.get("WATCH_STATE") or f"{_STATE_DIR}/{ME}_watch_state.json")
LOG_PATH = os.path.expanduser(os.environ.get("WATCH_LOG") or f"{_STATE_DIR}/watch.log")
MAX_WAKES_PER_RUN = int(os.environ.get("WATCH_MAX_WAKES", "3"))   # storm guard
IGNORE_TOPICS = {t.strip() for t in (os.environ.get("WATCH_IGNORE_TOPICS") or "attention").split(",") if t.strip()}
SELF_TOPICS_OK = {t.strip() for t in (os.environ.get("WATCH_DIRECTIVE_TOPICS") or "work,infra").split(",") if t.strip()}
DIRECTIVE_SENDERS = {s.strip().lower() for s in (os.environ.get("WATCH_DIRECTIVES") or "").split(",") if s.strip()}

SECRET_PATHS = [os.path.expanduser("~/.agentbus/secret")]
if os.environ.get("AGENT_CHAT_ENV_FILE"):
    SECRET_PATHS.append(os.path.expanduser(os.environ["AGENT_CHAT_ENV_FILE"]))


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def secret():
    s = os.environ.get("AGENT_CHAT_SECRET")
    if s:
        return s.strip()
    for p in SECRET_PATHS:
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.startswith("AGENT_CHAT_SECRET"):
                        return line.split("=", 1)[1].strip().strip("'\"")
        except OSError:
            continue
    sys.exit("bus_watch: AGENT_CHAT_SECRET not found")


def get(path, timeout=30):
    with urllib.request.urlopen(BUS + path, timeout=timeout) as r:
        return json.loads(r.read().decode())


def signed_post(url, payload, timeout=20):
    raw = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=raw, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Signature",
                   hmac.new(secret().encode(), raw, hashlib.sha256).hexdigest())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except urllib.error.URLError as e:
        return 0, str(e.reason)
    except http_client.HTTPException as e:
        # Older dispatcher builds answered without end_headers(), which surfaces as
        # BadStatusLine carrying the body. Treat that as delivered rather than
        # re-waking the same mention every tick.
        return 200, f"delivered (malformed response: {e})"


def load_state():
    """Returns the whole state dict, or None when no cursor has been primed yet."""
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        data["last_id"] = int(data.get("last_id", 0))
        return data
    except (OSError, ValueError):
        return None


def save_state(last_id, note="", repo_heads_seen=None):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    payload = {"last_id": last_id, "updated": time.time(), "note": note,
               "repo_heads": repo_heads_seen or {}}
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.replace(tmp, STATE_PATH)
    # Publish the cursor so humans and other agents can see how far I have read
    # (`agentbus reads`). Best effort: the local file stays authoritative.
    if os.environ.get("WATCH_PUBLISH_CURSOR", "1") == "1":
        try:
            signed_post(BUS + "/api/reads", {"name": ME, "last_id": int(last_id)}, timeout=8)
        except Exception as e:  # noqa: BLE001
            log(f"cursor publish failed (ignored): {e}")


def wants_me(m):
    """True when this message needs a turn for ME (own posts never do)."""
    if m.get("sender") == ME:
        return False
    if (m.get("topic") or "") in IGNORE_TOPICS:
        return False
    text = (m.get("text") or "").lower()
    if f"@{ME.lower()}" in text:
        return True
    sender = (m.get("sender") or "").lower()
    # A directive from the human (or a configured reporter) in a work lane counts too.
    if sender in DIRECTIVE_SENDERS and (m.get("topic") or "") in SELF_TOPICS_OK:
        return True
    return False


# Repos whose HEAD change should wake me (optional). Configure with e.g.
#   WATCH_REPOS='[{"path":"/srv/app","label":"App (prod)","note":"Verify the shipped state"}]'
# A change wakes ME with the commit subject and the note, so the turn knows what
# to check. Empty = repo watching disabled.
def _load_repos():
    raw = os.environ.get("WATCH_REPOS", "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError as e:
        log(f"WATCH_REPOS is not valid JSON: {e}")
        return []
    return [r for r in data if isinstance(r, dict) and r.get("path")]


REPO_WATCH = _load_repos()


def repo_heads():
    import subprocess
    out = {}
    for r in REPO_WATCH:
        try:
            head = subprocess.run(["git", "-C", r["path"], "rev-parse", "HEAD"],
                                  capture_output=True, text=True, timeout=15).stdout.strip()
            subject = subprocess.run(["git", "-C", r["path"], "log", "-1", "--pretty=%s"],
                                     capture_output=True, text=True, timeout=15).stdout.strip()
            if head:
                out[r["path"]] = {"head": head, "subject": subject}
        except Exception:
            continue
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--prime", action="store_true", help="set cursor to tip, dispatch nothing")
    a = ap.parse_args(argv)

    try:
        tip = int(get("/api/tip", timeout=15).get("max_id", 0))
    except Exception as e:                       # bus down: say nothing, exit 0
        log(f"bus unreachable: {e}")
        return 0

    state = load_state()
    heads_now = repo_heads()
    if a.prime or state is None:
        save_state(tip, "primed", heads_now)
        log(f"primed cursor at {tip} repo_heads={len(heads_now)} (no dispatch)")
        return 0

    last = state["last_id"]
    repo_wakes = 0
    heads_seen = dict(state.get("repo_heads") or {})
    new_heads = dict(heads_seen)
    for r in REPO_WATCH:
        info = heads_now.get(r["path"])
        if not info:
            continue
        prev = heads_seen.get(r["path"])
        prev_head = prev.get("head") if isinstance(prev, dict) else prev
        if prev_head and info["head"] != prev_head:
            log(f"repo changed: {r['label']} {prev_head[:8]} -> {info['head'][:8]} ({info['subject'][:80]})")
            if repo_wakes < MAX_WAKES_PER_RUN and not a.dry_run:
                st, body = signed_post(WAKE, {
                    "from": "watcher", "topic": "work",
                    "text": f"{r['note']}\n\nChanged: {r['label']}\nCommit: {info['subject']}",
                    "message_id": 0, "kind": "mention"})
                if st == 200:
                    repo_wakes += 1
                    log(f"woke {ME} for repo change in {r['label']}")
            elif a.dry_run:
                log(f"DRY-RUN would wake for repo change in {r['label']}")
                repo_wakes += 1
        new_heads[r["path"]] = {"head": info["head"], "subject": info["subject"]}
    if not a.dry_run:
        save_state(last, "repo check", new_heads)

    if tip <= last:
        return 0                                 # silent: nothing new, nothing logged

    try:
        msgs = get(f"/api/messages?since={last}&limit=200", timeout=30)
    except Exception as e:
        log(f"read failed at since={last}: {e}")
        return 0
    if not isinstance(msgs, list):
        msgs = []

    todo = [m for m in msgs if wants_me(m)]
    log(f"new={len(msgs)} addressed_to_me={len(todo)} since={last} tip={tip}")

    # ONE wake per tick, not one per mention. Separate turns per mention in the
    # same thread produce overlapping frodo posts that contradict each other
    # (2026-10-25: three wakes for 1819/1820/1821 -> five frodo posts in four
    # minutes). Bundling the unread mentions gives the turn the whole thread at
    # once, which is both cheaper and the only way it can be internally consistent.
    dispatched = 0
    if todo:
        ids = [int(m.get("id") or 0) for m in todo]
        senders = sorted({(m.get("sender") or "?") for m in todo})
        topic = max({(m.get("topic") or "general") for m in todo},
                    key=lambda t: sum(1 for m in todo if (m.get("topic") or "general") == t))
        budget = 12000
        parts = [f"{len(todo)} new message(s) addressed to @{ME} since id={last}: "
                 f"ids {', '.join(str(i) for i in ids)}. Read them as one thread and "
                 f"answer once. Do not re-post what an earlier message already covers."]
        for m in todo:
            head = f"\n--- id={m.get('id')} sender={m.get('sender','?')} " \
                   f"topic={m.get('topic','general')}\n"
            body = (m.get("text") or "")
            room = budget - sum(len(p) for p in parts) - len(head)
            if room <= 200:
                parts.append(f"\n--- id={m.get('id')} (truncated by budget)")
                continue
            parts.append(head + body[:room])
        payload = {
            "from": ",".join(senders),
            "topic": topic,
            "text": "".join(parts),
            "message_id": max(ids),
            "kind": "mention",
        }
        if a.dry_run:
            log(f"DRY-RUN would wake once for {len(todo)} mention(s) ids={ids} topic={topic}")
            dispatched = len(todo)
        else:
            st, body = signed_post(WAKE, payload)
            if st == 200:
                log(f"woke {ME} once for {len(todo)} mention(s) ids={ids} topic={topic}")
                dispatched = len(todo)
            else:
                log(f"wake FAILED ids={ids} status={st} body={body[:200]}")
                # Do not advance past a mention we failed to deliver — retry next tick.
                save_state(max(last, min(ids) - 1), "wake failed", new_heads)
                return 0

    new_last = max([last] + [int(m.get("id", last)) for m in msgs])
    if a.dry_run:
        # A dry run must not move the cursor: it would swallow the very mentions
        # it just reported on.
        log(f"DRY-RUN cursor unchanged at {last} (would advance to {new_last})")
        return 0
    save_state(new_last, f"dispatched={dispatched}", new_heads)   # keep the repo baseline
    return 0


if __name__ == "__main__":
    sys.exit(main())
