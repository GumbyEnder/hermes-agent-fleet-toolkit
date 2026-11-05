#!/usr/bin/env python3
"""agentbus — stdlib client for the self-hosted Agent Chat Bus (:8090).

Lets NON-Hermes agents (grokbot, claude-code, codex, node/python bots, shell
scripts, cron jobs) join the same room Hermes profiles already use.

No dependencies. Copy this one file to any host that can reach the bus over a
private network, or call it over ssh.

Env (or flags):
  AGENT_CHAT_URL       bus base URL (default http://127.0.0.1:8090)
  AGENT_CHAT_SECRET    shared HMAC secret (required for send/register/reads)
  AGENT_CHAT_ENV_FILE  optional file to read the secret from
  AGENT_NAME           sender identity (default: $AGENT_NAME, $USER, hostname)

Usage:
  agentbus health
  agentbus send "text" [--topic general] [--from NAME] [--reply-to ID]
  agentbus read [--since ID] [--last N] [--limit 50] [--topic T] [--wait 30]
                [--thread ID] [--mentions NAME] [--telemetry] [--mark] [--json]
  agentbus tail [--since ID] [--interval 3] [--topic T] [--wait 25]
  agentbus mentions --name NAME [--since ID]
  agentbus reads
  agentbus register --name N --webhook URL

Pull-mode (outbound-only hosts that expose no inbound ports):
  agentbus tail                 # run under systemd/nohup, prints new msgs
Exit code 0 on success, 1 on error.
"""
import argparse
import hashlib
import hmac
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8090"
SECRET_PATHS = [
    os.path.expanduser("~/.agentbus/secret"),
]
# Point this at any agent's Hermes .env instead of managing a separate file:
_ENV_FILE = os.environ.get("AGENT_CHAT_ENV_FILE")
if _ENV_FILE:
    SECRET_PATHS.append(os.path.expanduser(_ENV_FILE))


def _base_url():
    return (os.environ.get("AGENT_CHAT_URL") or DEFAULT_URL).rstrip("/")


def _secret():
    s = os.environ.get("AGENT_CHAT_SECRET")
    if s:
        return s.strip()
    for p in SECRET_PATHS:
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith("AGENT_CHAT_SECRET"):
                        _, _, val = line.partition("=")
                        return val.strip().strip("'\"")
        except OSError:
            continue
    sys.exit("agentbus: AGENT_CHAT_SECRET not set (env or ~/.agentbus/secret)")


def _name(flag=None):
    return (flag or os.environ.get("AGENT_NAME") or os.environ.get("USER")
            or socket.gethostname())


def _post(path, payload, signed=True):
    raw = json.dumps(payload).encode()
    req = urllib.request.Request(_base_url() + path, data=raw, method="POST")
    req.add_header("Content-Type", "application/json")
    if signed:
        req.add_header(
            "X-Signature",
            hmac.new(_secret().encode(), raw, hashlib.sha256).hexdigest(),
        )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode(errors="replace")
            return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except urllib.error.URLError as e:
        sys.exit(f"agentbus: cannot reach {_base_url()} ({e.reason})")


def _get(path):
    try:
        with urllib.request.urlopen(_base_url() + path, timeout=60) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except urllib.error.URLError as e:
        sys.exit(f"agentbus: cannot reach {_base_url()} ({e.reason})")


def _parse(body):
    """Bus returns a bare JSON list of messages; tolerate dict-wrapped shapes too."""
    try:
        data = json.loads(body)
    except Exception:
        return []
    if isinstance(data, dict):
        return data.get("messages", []) or []
    return data if isinstance(data, list) else []


def _print_msg(m):
    ts = m.get("ts")
    when = time.strftime("%m-%d %H:%M", time.localtime(ts)) \
        if isinstance(ts, (int, float)) else str(ts or "")
    who = m.get("sender") or m.get("from") or "?"
    reply = f" (↳{m['parent_id']})" if m.get("parent_id") else ""
    print(f"[{m.get('id')}]{reply} {when} <{who}> #{m.get('topic', 'general')}: "
          f"{m.get('text', '')}")


def _fetch(since, limit, topic, wait=0, thread=None, telemetry=False):
    q = {"since": since, "limit": limit}
    if topic:
        q["topic"] = topic
    if wait:
        q["wait"] = wait
    if thread:
        q["thread"] = thread
    if telemetry:
        q["telemetry"] = 1
    return _get("/api/messages?" + urllib.parse.urlencode(q))


def _mark_read(name, last_id):
    st, body = _post("/api/reads", {"name": name, "last_id": int(last_id)})
    if st != 200:
        print(f"cursor not saved ({st}): {body[:120]}", file=sys.stderr)


def cmd_health(a):
    st, body = _get("/health")
    print(body)
    return 0 if st == 200 else 1


def cmd_send(a):
    text = a.text if a.text != "-" else sys.stdin.read()
    payload = {"from": _name(a.sender), "topic": a.topic or "general", "text": text}
    if a.reply_to:
        payload["reply_to"] = a.reply_to
    st, body = _post("/send", payload)
    if st not in (200, 201):
        print(f"send failed {st}: {body}", file=sys.stderr)
        return 1
    print(body)
    return 0


def cmd_read(a):
    since, limit = a.since, a.limit
    if a.mentions:
        path = "/api/mentions?" + urllib.parse.urlencode(
            {"name": a.mentions, "since": since, "limit": limit})
        st, body = _get(path)
        if st != 200:
            print(f"read failed {st}: {body}", file=sys.stderr)
            return 1
        msgs = _parse(body)
    else:
        if a.last:  # "the N most recent messages"
            st, tip_body = _get("/api/tip")
            tip = int(json.loads(tip_body).get("max_id", 0)) if st == 200 else 0
            since, limit = max(0, tip - a.last), a.last
        st, body = _fetch(since, limit, a.topic, a.wait, a.thread, a.telemetry)
        if st != 200:
            print(f"read failed {st}: {body}", file=sys.stderr)
            return 1
        msgs = _parse(body)
    if a.json:
        print(json.dumps(msgs, indent=2))
    else:
        for m in msgs:
            _print_msg(m)
    if a.mark and msgs:
        _mark_read(_name(a.sender), max(int(m.get("id") or 0) for m in msgs))
    return 0


def cmd_mentions(a):
    path = "/api/mentions?" + urllib.parse.urlencode(
        {"name": a.name, "since": a.since, "limit": a.limit})
    st, body = _get(path)
    if st != 200:
        print(f"mentions failed {st}: {body}", file=sys.stderr)
        return 1
    msgs = _parse(body)
    if a.json:
        print(json.dumps(msgs, indent=2))
    else:
        for m in msgs:
            _print_msg(m)
    return 0


def cmd_reads(a):
    st, body = _get("/api/reads")
    if st != 200:
        print(f"reads failed {st}: {body}", file=sys.stderr)
        return 1
    rows = json.loads(body)
    if a.json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            when = time.strftime("%m-%d %H:%M", time.localtime(r.get("updated") or 0))
            print(f"{r.get('name', '?'):<20} last_id={r.get('last_id')} ({when})")
    return 0


def cmd_tail(a):
    since = a.since
    # Prime: show only NEW traffic unless an explicit --since was given.
    if not a.since:
        st, body = _get("/api/tip")
        if st == 200:
            try:
                since = int(json.loads(body).get("max_id", 0))
            except Exception:
                since = 0
    print(f"# tailing {_base_url()} since={since} (ctrl-c to stop)", flush=True)
    while True:
        st, body = _fetch(since, 100, a.topic, a.wait)
        if st == 200:
            for m in _parse(body):
                since = max(since, m.get("id", since))
                _print_msg(m)
                sys.stdout.flush()
        else:
            print(f"# read failed {st}: {body}", flush=True)
            time.sleep(a.interval)


def cmd_register(a):
    st, body = _post("/agents/register", {"name": a.name, "webhook_url": a.webhook})
    print(body)
    return 0 if st in (200, 201) else 1


def main(argv=None):
    p = argparse.ArgumentParser(prog="agentbus", description="Agent Chat Bus client")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health").set_defaults(fn=cmd_health)

    s = sub.add_parser("send")
    s.add_argument("text", help="message body, or - for stdin")
    s.add_argument("--topic", default="general")
    s.add_argument("--from", dest="sender")
    s.add_argument("--reply-to", type=int, help="message id this answers")
    s.set_defaults(fn=cmd_send)

    r = sub.add_parser("read")
    r.add_argument("--since", type=int, default=0)
    r.add_argument("--last", type=int, default=0, help="show the N most recent messages")
    r.add_argument("--limit", type=int, default=50)
    r.add_argument("--topic")
    r.add_argument("--wait", type=int, default=0, help="long-poll seconds")
    r.add_argument("--thread", type=int, help="root message id: it plus its replies")
    r.add_argument("--mentions", metavar="NAME", help="only messages mentioning @NAME")
    r.add_argument("--telemetry", action="store_true",
                   help="include telemetry topics (hidden by default)")
    r.add_argument("--mark", action="store_true",
                   help="store a read cursor for my sender name")
    r.add_argument("--from", dest="sender", help="sender name used by --mark")
    r.add_argument("--json", action="store_true")
    r.set_defaults(fn=cmd_read)

    m = sub.add_parser("mentions")
    m.add_argument("--name", required=True)
    m.add_argument("--since", type=int, default=0)
    m.add_argument("--limit", type=int, default=100)
    m.add_argument("--json", action="store_true")
    m.set_defaults(fn=cmd_mentions)

    rd = sub.add_parser("reads", help="per-agent read cursors")
    rd.add_argument("--json", action="store_true")
    rd.set_defaults(fn=cmd_reads)

    t = sub.add_parser("tail")
    t.add_argument("--since", type=int, default=0)
    t.add_argument("--interval", type=int, default=3)
    t.add_argument("--topic")
    t.add_argument("--wait", type=int, default=25, help="long-poll seconds")
    t.set_defaults(fn=cmd_tail)

    g = sub.add_parser("register")
    g.add_argument("--name", required=True)
    g.add_argument("--webhook", required=True)
    g.set_defaults(fn=cmd_register)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
