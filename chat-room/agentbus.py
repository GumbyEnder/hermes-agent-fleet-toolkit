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
  agentbus inbox [--name N] [--topic T] [--mark] [--json]
  agentbus digest [--since ID] [--for NAME] [--mark] [--json]
  agentbus threads [--state open|answered|stale|all] [--limit N] [--older-than H]
  agentbus presence [--active-only]
  agentbus task create --title T [--detail D] [--owner N] [--ref PB-1] [--data JSON]
  agentbus task claim ID [--lease 3600] | renew ID | done ID [--outcome --evidence]
  agentbus task bounce ID --reason R | show ID | list [--state open] [--owner N]
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
    if "text" in m:
        body = m.get("text") or ""
    else:  # --meta: the body was dropped on purpose, print the preview + real size
        body = f"({m.get('chars', 0)} chars) {m.get('preview', '')}"
    scope = "".join(
        x for x in (
            f" {{{m['project']}}}" if m.get("project") else "",
            f" [{m['ticket']}]" if m.get("ticket") else "",
        )
    )
    print(f"[{m.get('id')}]{reply} {when} <{who}> #{m.get('topic', 'general')}{scope}: {body}")


def _fetch(since, limit, topic, wait=0, thread=None, telemetry=False,
           meta=False, max_chars=None, project=None, ticket=None, unscoped=False):
    q = {"since": since, "limit": limit}
    if topic:
        q["topic"] = topic
    if project:
        q["project"] = project
    if ticket:
        q["ticket"] = ticket
    if unscoped:
        q["unscoped"] = 1
    if wait:
        q["wait"] = wait
    if thread:
        q["thread"] = thread
    if telemetry:
        q["telemetry"] = 1
    if meta:
        q["fields"] = "meta"
    if max_chars:
        q["max_chars"] = max_chars
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
    payload = {"from": _name(a.sender), "topic": a.topic or "work", "text": text}
    if getattr(a, "project", None):
        payload["project"] = a.project
    if getattr(a, "ticket", None):
        payload["ticket"] = a.ticket
    if getattr(a, "kind", None):
        payload["kind"] = a.kind
    data = _json_arg(getattr(a, "data", None))
    if data is not None:
        payload["data"] = data
    if getattr(a, "route_large", False):
        payload["route_large"] = True
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
        st, body = _fetch(since, limit, a.topic, a.wait, a.thread, a.telemetry,
                          getattr(a, "meta", False), getattr(a, "max_chars", None),
                          getattr(a, "project", None), getattr(a, "ticket", None),
                          getattr(a, "unscoped", False))
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


def _when(ts):
    return time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "?"


def _age(sec):
    if sec is None:
        return "?"
    sec = int(sec)
    if sec < 90:
        return f"{sec}s"
    if sec < 5400:
        return f"{sec // 60}m"
    if sec < 172800:
        return f"{sec // 3600}h"
    return f"{sec // 86400}d"


def cmd_inbox(a):
    """What I owe the room and what the room owes me, in one call."""
    name = a.name or _name(a.sender)
    q = {"name": name}
    if a.topic:
        q["topic"] = a.topic
    if a.telemetry:
        q["telemetry"] = 1
    st, body = _get("/api/inbox?" + urllib.parse.urlencode(q))
    if st != 200:
        print(f"inbox failed {st}: {body}", file=sys.stderr)
        return 1
    d = json.loads(body)
    if a.json:
        print(json.dumps(d, indent=2))
    else:
        cur = d.get("cursor")
        print(f"{name}: cursor={cur if cur is not None else 'unpublished'}"
              f" backlog={d.get('backlog')} mentions={d.get('mention_count')}"
              f" unanswered={d.get('unanswered_mentions')}"
              f" threads={d.get('thread_count')}")
        for m in d.get("mentions", []):
            flag = "  " if m.get("answered") else "!!"
            print(f"{flag}[{m['id']}] {_when(m['ts'])} <{m['sender']}> "
                  f"thread={m.get('thread')}: {m.get('preview')}")
        for t in d.get("threads", []):
            print(f"   ~[{t['id']}] {_age(t.get('age_s'))} "
                  f"{t.get('new_messages')} new from {','.join(t.get('participants', []))}: "
                  f"{t.get('preview')}")
    if a.mark:
        _mark_read(name, d.get("suggested_cursor") or d.get("tip") or 0)
    return 0


def cmd_digest(a):
    q = {"since": a.since}
    who = a.who or a.name
    if who:
        q["for"] = who
    if a.topic:
        q["topic"] = a.topic
    if a.telemetry:
        q["telemetry"] = 1
    st, body = _get("/api/digest?" + urllib.parse.urlencode(q))
    if st != 200:
        print(f"digest failed {st}: {body}", file=sys.stderr)
        return 1
    d = json.loads(body)
    if a.json:
        print(json.dumps(d, indent=2))
    else:
        print(f"since={d['since']} next_since={d['next_since']} "
              f"count={d['count']}{' (capped)' if d.get('truncated') else ''} tip={d['tip']}")
        print("  senders " + ", ".join(f"{r['sender']}:{r['messages']}"
                                       for r in d.get("by_sender", [])[:8]))
        print("  topics  " + ", ".join(f"#{r['topic']}:{r['messages']}"
                                       for r in d.get("by_topic", [])[:6]))
        if d.get("for"):
            print(f"  for {d['for']}: {d.get('mention_count')} mentions, "
                  f"{d.get('unanswered_mentions')} unanswered")
            for m in d.get("mentions", []):
                flag = "  " if m.get("answered") else "!!"
                print(f"{flag}[{m['id']}] <{m['sender']}> {m.get('preview')}")
        print(f"  open threads in window: {d.get('open_thread_count')}")
        for t in d.get("open_threads", [])[:5]:
            print(f"    [{t['id']}] {_when(t['last_ts'])}: {t.get('preview')}")
    if a.mark and d.get("next_since"):
        _mark_read(who or _name(a.sender), d["next_since"])
    return 0


def cmd_threads(a):
    q = {"state": a.state, "limit": a.limit}
    if a.since:
        q["since"] = a.since
    if a.topic:
        q["topic"] = a.topic
    if a.older_than is not None:
        q["older_than"] = a.older_than
    if a.telemetry:
        q["telemetry"] = 1
    st, body = _get("/api/threads?" + urllib.parse.urlencode(q))
    if st != 200:
        print(f"threads failed {st}: {body}", file=sys.stderr)
        return 1
    d = json.loads(body)
    if a.json:
        print(json.dumps(d, indent=2))
        return 0
    print(f"{d['state']}: {d['count']} threads ({d['open']} unanswered)")
    for t in d["threads"]:
        lag = f"lag {_age(t['first_reply_lag_s'])}" if t.get("first_reply_lag_s") is not None \
            else f"open {_age(t['age_s'])}"
        print(f"[{t['id']}] {_when(t['last_ts'])} {lag} "
              f"{t['replies']}r <{t['sender']}> {t.get('preview')}")
    return 0


def _json_arg(raw):
    if raw in (None, ""):
        return None
    try:
        return json.loads(raw)
    except ValueError:
        sys.exit("agentbus: --data must be valid JSON")


def cmd_task(a):
    """Task lifecycle: create / claim / renew / done / bounce / show / list."""
    act = a.action
    if act == "list":
        q = {"state": a.state or "open", "limit": a.limit}
        for k in ("owner", "topic", "ref"):
            if getattr(a, k):
                q[k] = getattr(a, k)
        st, body = _get("/api/tasks?" + urllib.parse.urlencode(q))
        if st != 200:
            print(f"tasks failed {st}: {body}", file=sys.stderr)
            return 1
        d = json.loads(body)
        if a.json:
            print(json.dumps(d, indent=2))
            return 0
        counts = " ".join(f"{k}={v}" for k, v in sorted(d.get("counts", {}).items()))
        print(f"{d['count']} tasks ({counts})")
        for t in d["tasks"]:
            who = t.get("claimed_by") or t.get("owner") or "-"
            ref = f"[{t['ref']}] " if t.get("ref") else ""
            print(f"#{t['id']} {t['effective_state']:<8} {who:<12} "
                  f"{_age(int(time.time() - (t.get('updated_ts') or 0)))} {ref}{t['title']}")
        return 0

    if act == "show":
        st, body = _get(f"/api/tasks?id={int(a.id)}")
        if st != 200:
            print(f"task failed {st}: {body}", file=sys.stderr)
            return 1
        d = json.loads(body)
        if a.json:
            print(json.dumps(d, indent=2))
            return 0
        print(f"#{d['id']} [{d['state']}] {d['title']}")
        print(f"  creator={d['creator']} owner={d['owner']} claimed_by={d['claimed_by']} "
              f"lease_left={d['lease_s_left']}s ref={d['ref']}")
        if d.get("outcome"):
            print(f"  outcome={d['outcome']} evidence={d['evidence']}")
        for e in d.get("events", []):
            print(f"  {_when(e['ts'])} {e['actor']:<10} {e['action']:<9} {e.get('note') or ''}")
        return 0

    if act == "create":
        payload = {"from": _name(a.sender), "title": a.title,
                   "topic": a.topic or "work"}
        for src, dst in (("detail", "detail"), ("owner", "owner"), ("ref", "ref")):
            val = getattr(a, src)
            if val:
                payload[dst] = val
        data = _json_arg(a.data)
        if data is not None:
            payload["data"] = data
        st, body = _post("/api/tasks", payload)
        if st != 200:
            print(f"task create failed {st}: {body}", file=sys.stderr)
            return 1
        d = json.loads(body)
        print(f"created task #{d['task']['id']} (message {d['id']})")
        return 0

    if not a.id:
        sys.exit("agentbus: this action needs a task id")
    path = {"claim": "/api/tasks/claim", "renew": "/api/tasks/renew",
            "done": "/api/tasks/complete", "bounce": "/api/tasks/bounce",
            "release": "/api/tasks/release"}[act]
    payload = {"from": _name(a.sender), "id": int(a.id)}
    if act in ("claim", "renew"):
        if a.lease:
            payload["lease_s"] = a.lease
    if act == "done":
        if a.outcome:
            payload["outcome"] = a.outcome
        if a.evidence:
            payload["evidence"] = a.evidence
        if a.no_announce:
            payload["announce"] = False
    if act == "bounce":
        if not a.reason:
            sys.exit("agentbus: bounce needs --reason")
        payload["reason"] = a.reason
    if act == "release" and a.reason:
        payload["reason"] = a.reason
    data = _json_arg(a.data)
    if data is not None:
        payload["data"] = data
    st, body = _post(path, payload)
    if st != 200:
        print(f"task {act} failed {st}: {body}", file=sys.stderr)
        return 1
    d = json.loads(body)
    t = d["task"]
    print(f"#{t['id']} {t['effective_state']} "
          f"claimed_by={t['claimed_by']} lease_left={t['lease_s_left']}s"
          + (f" posted={d['posted']}" if d.get("posted") else ""))
    return 0


def cmd_presence(a):
    st, body = _get("/api/presence")
    if st != 200:
        print(f"presence failed {st}: {body}", file=sys.stderr)
        return 1
    d = json.loads(body)
    if a.json:
        print(json.dumps(d, indent=2))
        return 0
    print(f"tip={d['tip']}")
    for row in d["agents"]:
        if a.active_only and row["state"] not in ("active",):
            continue
        print(f"{row['name']:<18} {row['state']:<11} "
              f"backlog={row['backlog'] if row['backlog'] is not None else '-'} "
              f"last_seen={_age(row['age_s']) if row['age_s'] is not None else '-'}")
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


def cmd_search(a):
    q = {"q": a.query, "limit": a.limit}
    for k in ("topic", "project", "ticket"):
        v = getattr(a, k, None)
        if v:
            q[k] = v
    st, body = _get("/api/search?" + urllib.parse.urlencode(q))
    if st != 200:
        print(f"search failed {st}: {body}", file=sys.stderr)
        return 1
    hits = _parse(body)
    if a.json:
        print(json.dumps(hits, indent=2))
        return 0
    if not hits:
        print("no hits")
        return 0
    for m in hits:
        _print_msg(m)
    print(f"-- {len(hits)} hit(s), newest first", file=sys.stderr)
    return 0


def cmd_projects(a):
    st, body = _get("/api/projects")
    if st != 200:
        print(f"projects failed {st}: {body}", file=sys.stderr)
        return 1
    d = json.loads(body)
    if a.json:
        print(json.dumps(d, indent=2))
        return 0
    rows = d.get("projects") or []
    if not rows:
        print("no projects yet (every message is unscoped)")
        return 0
    for r in rows:
        tk = f" tickets={r['tickets']}" if r.get("tickets") else ""
        age = r.get("age_s")
        since = f"{int(age // 3600)}h ago" if isinstance(age, int) else ""
        print(f"{r['project']:<28} msgs={r['messages']:<5} senders={r.get('senders', 0):<3}"
              f" roots={r.get('roots', 0):<4}{tk:<14} last {since}")
    un = d.get("unscoped") or 0
    if un:
        print(f"{'(unscoped)':<28} msgs={un}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="agentbus", description="Agent Chat Bus client")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health").set_defaults(fn=cmd_health)

    s = sub.add_parser("send")
    s.add_argument("text", help="message body, or - for stdin")
    s.add_argument("--project", help="which project/product this belongs to, e.g. chat-room")
    s.add_argument("--ticket", help="optional ticket, <system>:<id> (bare id assumes devboards)")
    s.add_argument("--topic", default="work")
    s.add_argument("--from", dest="sender")
    s.add_argument("--reply-to", type=int, help="message id this answers")
    s.add_argument("--kind", default="message",
                   help="message kind (message|note|status|verdict|outcome|report|...)")
    s.add_argument("--data", help="JSON object envelope, e.g. '{\"task\":12,\"verdict\":\"ACCEPT\"}'")
    s.add_argument("--route-large", dest="route_large", action="store_true",
                   help="fold a long body into an artifact and keep a 400-char head")
    s.set_defaults(fn=cmd_send)

    r = sub.add_parser("read")
    r.add_argument("--since", type=int, default=0)
    r.add_argument("--last", type=int, default=0, help="show the N most recent messages")
    r.add_argument("--limit", type=int, default=50)
    r.add_argument("--topic")
    r.add_argument("--project", help="only messages scoped to this project")
    r.add_argument("--ticket", help="only messages carrying this ticket")
    r.add_argument("--unscoped", action="store_true", help="only messages with no project")
    r.add_argument("--wait", type=int, default=0, help="long-poll seconds")
    r.add_argument("--thread", type=int, help="root message id: it plus its replies")
    r.add_argument("--mentions", metavar="NAME", help="only messages mentioning @NAME")
    r.add_argument("--telemetry", action="store_true",
                   help="include telemetry topics (hidden by default)")
    r.add_argument("--mark", action="store_true",
                   help="store a read cursor for my sender name")
    r.add_argument("--from", dest="sender", help="sender name used by --mark")
    r.add_argument("--meta", action="store_true",
                   help="drop bodies; return id/sender/chars/preview only")
    r.add_argument("--max-chars", dest="max_chars", type=int,
                   help="clip each body to N chars (chars keeps the real size)")
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

    ib = sub.add_parser("inbox", help="my mentions, threads and backlog in one call")
    ib.add_argument("--name", help="defaults to AGENT_NAME/$USER")
    ib.add_argument("--from", dest="sender")
    ib.add_argument("--topic")
    ib.add_argument("--telemetry", action="store_true")
    ib.add_argument("--mark", action="store_true", help="publish cursor at the tip")
    ib.add_argument("--json", action="store_true")
    ib.set_defaults(fn=cmd_inbox)

    dg = sub.add_parser("digest", help="compact summary of a window since a cursor")
    dg.add_argument("--since", type=int, default=0)
    dg.add_argument("--for", dest="who", metavar="NAME",
                    help="also report that agent's mentions (default: --name)")
    dg.add_argument("--name")
    dg.add_argument("--topic")
    dg.add_argument("--telemetry", action="store_true")
    dg.add_argument("--mark", action="store_true", help="publish cursor at next_since")
    dg.add_argument("--json", action="store_true")
    dg.set_defaults(fn=cmd_digest)

    th = sub.add_parser("threads", help="thread-level view: answered, open, stale")
    th.add_argument("--state", default="open", choices=["all", "open", "answered", "stale"])
    th.add_argument("--since", type=int, default=0)
    th.add_argument("--limit", type=int, default=25)
    th.add_argument("--topic")
    th.add_argument("--older-than", dest="older_than", type=int,
                    help="hours, for --state stale (default 24)")
    th.add_argument("--telemetry", action="store_true")
    th.add_argument("--json", action="store_true")
    th.set_defaults(fn=cmd_threads)

    tk = sub.add_parser("task", help="task lifecycle: create|claim|renew|done|bounce|show|list")
    tk.add_argument("action", choices=["create", "claim", "renew", "done", "bounce",
                                       "release", "show", "list"])
    tk.add_argument("id", nargs="?", type=int)
    tk.add_argument("--title", help="create: what needs doing")
    tk.add_argument("--detail", help="create: specifics, acceptance criteria")
    tk.add_argument("--topic", help="create/list: topic (default work)")
    tk.add_argument("--owner", help="create/list: who it is for")
    tk.add_argument("--ref", help="create/list: external id, e.g. PB-114")
    tk.add_argument("--data", help="JSON object attached to the create")
    tk.add_argument("--lease", type=int, help="claim/renew: lease seconds (default 3600)")
    tk.add_argument("--outcome", help="done: short result")
    tk.add_argument("--evidence", help="done: handle that proves it (sha, url, path)")
    tk.add_argument("--no-announce", dest="no_announce", action="store_true",
                    help="done: do not post the outcome into the room")
    tk.add_argument("--reason", help="bounce/release: why")
    tk.add_argument("--state", help="list: open|claimed|done|bounced|all")
    tk.add_argument("--limit", type=int, default=30)
    tk.add_argument("--from", dest="sender")
    tk.add_argument("--json", action="store_true")
    tk.set_defaults(fn=cmd_task)

    pr = sub.add_parser("presence", help="who is alive, from published read cursors")
    pr.add_argument("--active-only", action="store_true")
    pr.add_argument("--json", action="store_true")
    pr.set_defaults(fn=cmd_presence)

    t = sub.add_parser("tail")
    t.add_argument("--since", type=int, default=0)
    t.add_argument("--interval", type=int, default=3)
    t.add_argument("--topic")
    t.add_argument("--wait", type=int, default=25, help="long-poll seconds")
    t.set_defaults(fn=cmd_tail)

    sr = sub.add_parser("search", help="full-text search, newest first")
    sr.add_argument("query", help="FTS query; multiple words are ANDed")
    sr.add_argument("--topic")
    sr.add_argument("--project")
    sr.add_argument("--ticket")
    sr.add_argument("--limit", type=int, default=50)
    sr.add_argument("--json", action="store_true")
    sr.set_defaults(fn=cmd_search)

    pj = sub.add_parser("projects", help="the scope registry derived from the room")
    pj.add_argument("--json", action="store_true")
    pj.set_defaults(fn=cmd_projects)

    g = sub.add_parser("register")
    g.add_argument("--name", required=True)
    g.add_argument("--webhook", required=True)
    g.set_defaults(fn=cmd_register)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
