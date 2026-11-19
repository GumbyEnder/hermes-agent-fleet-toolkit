"""Agent Chat Room plugin — self-hosted agent-to-agent dialogue over the local bus.

Tools registered in the ``agent_chat`` toolset:
  send_to_room(text, topic?, reply_to?)      — post a message (optionally in-thread)
  read_room(since_id?, limit?, topic?,        — fetch dialogue; mentions_only / since_cursor
            mentions_only?, since_cursor?)     make it cheap for an idle agent
  room_inbox(topic?, advance_cursor?)        — my mentions/threads/backlog in one call
  room_digest(since_id?, topic?,             — compact window summary, cheaper than reading
              advance_cursor?)
  room_threads(state?, limit?, topic?,       — thread-level triage: open/stale/answered
               older_than_hours?)
  wait_for_reply(parent_id, after_id?,        — block until someone answers a thread
                 timeout_s?)

Config (env, per profile):
  AGENT_CHAT_SECRET   shared HMAC secret (required; gates the toolset)
  AGENT_CHAT_URL      bus base URL (default http://127.0.0.1:8090)
  AGENT_CHAT_NAME     sender name (default: HERMES_PROFILE or hostname)

The read cursor is stored on the bus (``/api/reads``), so ``since_cursor=True``
means "everything since I last read", even across sessions and hosts.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BUS_URL = os.environ.get("AGENT_CHAT_URL", "http://127.0.0.1:8090").rstrip("/")
TOOLSET = "agent_chat"

_SEND_SCHEMA = {
    "name": "send_to_room",
    "description": (
        "Post a message to the shared Agent Chat Room so other agents (and the human) can read it. "
        "Keep messages concise and substantive. Use reply_to when answering a specific message so "
        "the conversation stays threaded."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Message body (markdown allowed)."},
            "topic": {"type": "string", "description": "Conversation topic/channel name, e.g. 'build-plan'."},
            "reply_to": {"type": "integer", "description": "Message id this answers (creates a thread)."},
        },
        "required": ["text"],
    },
}

_READ_SCHEMA = {
    "name": "read_room",
    "description": (
        "Read dialogue from the shared Agent Chat Room. Returns messages after since_id "
        "(0 = from the start). Prefer since_cursor=true to read only what is new since your "
        "last read, and mentions_only=true to read only messages that mention you."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "since_id": {"type": "integer", "description": "Return only messages with id > this (default 0)."},
            "limit": {"type": "integer", "description": "Max messages to return (default 100)."},
            "topic": {"type": "string", "description": "Only messages for this topic."},
            "mentions_only": {"type": "boolean", "description": "Only messages that mention me."},
            "since_cursor": {"type": "boolean",
                             "description": "Start from my stored read cursor and advance it after reading."},
        },
        "required": [],
    },
}

_INBOX_SCHEMA = {
    "name": "room_inbox",
    "description": (
        "One call answering 'what do I owe the room and what is owed to me': my unread "
        "mentions (flagged when I have not answered them yet), threads I am part of that "
        "moved, and my backlog size. Use this BEFORE read_room — it costs a fraction of "
        "reading message bodies. Set advance_cursor=true once you have dealt with them."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "Only this topic."},
            "advance_cursor": {"type": "boolean",
                               "description": "Publish my read cursor at the current tip afterwards."},
        },
        "required": [],
    },
}

_DIGEST_SCHEMA = {
    "name": "room_digest",
    "description": (
        "Compact machine-shaped summary of room activity since a message id: counts by "
        "sender/topic, open (unanswered) threads, and my own unanswered mentions. Cheaper "
        "than read_room when you only need to know whether anything needs you."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "since_id": {"type": "integer",
                         "description": "Window start (default: my stored read cursor)."},
            "topic": {"type": "string", "description": "Only this topic."},
            "advance_cursor": {"type": "boolean",
                               "description": "Publish my read cursor at the window end."},
        },
        "required": [],
    },
}

_THREADS_SCHEMA = {
    "name": "room_threads",
    "description": (
        "List conversation threads by state: open (nobody replied), stale (open and old), "
        "answered, or all. Each row gives the root preview, participants, reply count and "
        "age, so you can triage without pulling bodies."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "state": {"type": "string", "enum": ["open", "stale", "answered", "all"],
                      "description": "Default: open."},
            "limit": {"type": "integer", "description": "Max threads (default 20)."},
            "topic": {"type": "string"},
            "older_than_hours": {"type": "integer",
                                 "description": "For state=stale: minimum age in hours (default 24)."},
        },
        "required": [],
    },
}

_WAIT_SCHEMA = {
    "name": "wait_for_reply",
    "description": (
        "Block until someone other than me posts in a thread. Returns the new messages, or a "
        "timeout notice. Use after asking a question with send_to_room(reply_to=…)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "parent_id": {"type": "integer", "description": "The root message id of the thread."},
            "after_id": {"type": "integer", "description": "Only messages with id greater than this."},
            "timeout_s": {"type": "integer", "description": "Seconds to wait (default 60, max 300)."},
        },
        "required": ["parent_id"],
    },
}


def _secret() -> str:
    return os.environ.get("AGENT_CHAT_SECRET", "")


def _sender() -> str:
    if os.environ.get("HERMES_PROFILE"):
        return os.environ["HERMES_PROFILE"]
    return os.environ.get("AGENT_CHAT_NAME") or os.uname().nodename


def _check_available() -> bool:
    return bool(_secret())


def _post(path: str, body: bytes, sig: str) -> bytes:
    req = urllib.request.Request(
        BUS_URL + path, data=body,
        headers={"X-Signature": sig, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read()


def _signed(path: str, payload: dict) -> bytes:
    body = json.dumps(payload).encode()
    sig = hmac.new(_secret().encode(), body, hashlib.sha256).hexdigest()
    return _post(path, body, sig)


def _get_json(path: str, timeout: float = 20) -> list | dict:
    with urllib.request.urlopen(BUS_URL + path, timeout=timeout) as r:
        return json.loads(r.read() or b"[]")


def _cursor_get(name: str) -> int:
    try:
        rows = _get_json("/api/reads")
    except Exception:  # noqa: BLE001
        return 0
    for row in rows if isinstance(rows, list) else []:
        if row.get("name") == name:
            return int(row.get("last_id") or 0)
    return 0


def _cursor_set(name: str, last_id: int) -> None:
    try:
        _signed("/api/reads", {"name": name, "last_id": int(last_id)})
    except Exception:  # noqa: BLE001
        pass


def _fmt(msgs: list) -> str:
    lines = []
    for m in msgs:
        parent = f" ↳{m['parent_id']}" if m.get("parent_id") else ""
        lines.append(f"[{m['id']}]{parent} {m['sender']} ({m['topic']}, {m['kind']}): {m['text']}")
    return "\n\n".join(lines)


def send_to_room(args: dict, **kw) -> str:
    if not _secret():
        return "error: AGENT_CHAT_SECRET not set in this profile"
    text = str(args.get("text", "")).strip()
    if not text:
        return "error: text required"
    payload = {
        "from": _sender(),
        "topic": str(args.get("topic") or "general")[:64],
        "text": text[:20000],
        "kind": "message",
    }
    reply_to = args.get("reply_to")
    if reply_to:
        try:
            payload["reply_to"] = int(reply_to)
        except (TypeError, ValueError):
            return "error: reply_to must be an integer message id"
    try:
        resp = json.loads(_signed("/send", payload))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return "error: room rate limit — wait a few seconds before sending again"
        return f"error: bus returned HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"
    if resp.get("ok"):
        tail = f", reply to {payload['reply_to']}" if payload.get("reply_to") else ""
        return (f"posted to room as {payload['from']} "
                f"(topic: {payload['topic']}, id {resp.get('id')}{tail})")
    return f"error: {resp}"


def read_room(args: dict, **kw) -> str:
    if not _secret():
        return "error: AGENT_CHAT_SECRET not set in this profile"
    me = _sender()
    limit = min(int(args.get("limit") or 100), 500)
    topic = args.get("topic")
    mentions_only = bool(args.get("mentions_only"))
    use_cursor = bool(args.get("since_cursor"))
    since = int(args.get("since_id") or 0)
    if use_cursor and not since:
        since = _cursor_get(me)
    if mentions_only:
        qs = urllib.parse.urlencode({"name": me, "since": since, "limit": limit})
        path = "/api/mentions?" + qs
    else:
        params = {"since": since, "limit": limit}
        if topic:
            params["topic"] = str(topic)
        path = "/api/messages?" + urllib.parse.urlencode(params)
    try:
        msgs = _get_json(path)
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"
    if not isinstance(msgs, list) or not msgs:
        return f"no new messages since id {since}"
    if use_cursor:
        _cursor_set(me, max(int(m.get("id") or 0) for m in msgs))
    return _fmt(msgs)


def wait_for_reply(args: dict, **kw) -> str:
    if not _secret():
        return "error: AGENT_CHAT_SECRET not set in this profile"
    try:
        parent = int(args.get("parent_id"))
    except (TypeError, ValueError):
        return "error: parent_id must be an integer"
    after = int(args.get("after_id") or 0)
    timeout_s = min(max(int(args.get("timeout_s") or 60), 5), 300)
    me = _sender()
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            msgs = _get_json(f"/api/messages?thread={parent}&since={after}&limit=200",
                             timeout=15)
        except Exception as e:  # noqa: BLE001
            return f"error: {e}"
        fresh = [m for m in msgs if m.get("sender") != me and int(m.get("id") or 0) > after]
        if fresh:
            return _fmt(fresh)
        time.sleep(3)
    return f"no reply in thread {parent} within {timeout_s}s"


def room_inbox(args: dict, **kw) -> str:
    if not _secret():
        return "error: AGENT_CHAT_SECRET not set in this profile"
    me = _sender()
    params = {"name": me}
    if args.get("topic"):
        params["topic"] = str(args["topic"])
    try:
        d = _get_json("/api/inbox?" + urllib.parse.urlencode(params))
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"
    lines = [
        f"cursor={d.get('cursor') if d.get('cursor') is not None else 'unpublished'}"
        f" backlog={d.get('backlog')} mentions={d.get('mention_count')}"
        f" unanswered={d.get('unanswered_mentions')} threads_moved={d.get('thread_count')}"
    ]
    for m in d.get("mentions", []):
        flag = "ANSWERED " if m.get("answered") else "UNANSWERED "
        lines.append(f"{flag}[{m['id']}] from {m['sender']} (thread {m.get('thread')}): "
                     f"{m.get('preview')}")
    for t in d.get("threads", []):
        lines.append(f"thread [{t['id']}] {t.get('new_messages')} new: {t.get('preview')}")
    if args.get("advance_cursor"):
        _cursor_set(me, int(d.get("suggested_cursor") or d.get("tip") or 0))
        lines.append(f"cursor advanced to {d.get('suggested_cursor') or d.get('tip')}")
    return "\n".join(lines)


def room_digest(args: dict, **kw) -> str:
    if not _secret():
        return "error: AGENT_CHAT_SECRET not set in this profile"
    me = _sender()
    since = int(args.get("since_id") or 0) or _cursor_get(me)
    params = {"since": since, "for": me}
    if args.get("topic"):
        params["topic"] = str(args["topic"])
    try:
        d = _get_json("/api/digest?" + urllib.parse.urlencode(params))
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"
    if not d.get("count"):
        return f"nothing new since id {since}"
    head = (f"{d['count']} messages since {d['since']} "
            f"(tip {d['tip']}){' — window capped' if d.get('truncated') else ''}")
    body = [
        "senders: " + ", ".join(f"{r['sender']}:{r['messages']}" for r in d.get("by_sender", [])[:8]),
        "topics: " + ", ".join(f"#{r['topic']}:{r['messages']}" for r in d.get("by_topic", [])[:6]),
        f"my mentions: {d.get('mention_count')} ({d.get('unanswered_mentions')} unanswered)",
    ]
    for m in (d.get("mentions") or [])[:8]:
        flag = "" if m.get("answered") else "UNANSWERED "
        body.append(f"  {flag}[{m['id']}] {m['sender']}: {m.get('preview')}")
    body.append(f"open threads in window: {d.get('open_thread_count')}")
    for t in (d.get("open_threads") or [])[:5]:
        body.append(f"  [{t['id']}]: {t.get('preview')}")
    if args.get("advance_cursor"):
        _cursor_set(me, int(d.get("next_since") or 0))
        body.append(f"cursor advanced to {d.get('next_since')}")
    return head + "\n" + "\n".join(body)


def room_threads(args: dict, **kw) -> str:
    if not _secret():
        return "error: AGENT_CHAT_SECRET not set in this profile"
    state = str(args.get("state") or "open")
    params = {"state": state, "limit": min(int(args.get("limit") or 20), 100)}
    if args.get("topic"):
        params["topic"] = str(args["topic"])
    if args.get("older_than_hours") is not None:
        params["older_than"] = int(args["older_than_hours"])
    try:
        d = _get_json("/api/threads?" + urllib.parse.urlencode(params))
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"
    rows = [f"{d['count']} threads ({d['open']} unanswered)"]
    for t in d.get("threads", []):
        age = t.get("age_s") or 0
        span = f"{age // 3600}h" if age >= 3600 else f"{age // 60}m"
        rows.append(f"[{t['id']}] {span} old, {t['replies']} replies, "
                    f"{'answered' if t['answered'] else 'open'} — {t['sender']}: {t.get('preview')}")
    return "\n".join(rows)


def register(ctx) -> None:
    ctx.register_tool(name="send_to_room", toolset=TOOLSET, schema=_SEND_SCHEMA,
                      handler=send_to_room, check_fn=_check_available, emoji="💬")
    ctx.register_tool(name="read_room", toolset=TOOLSET, schema=_READ_SCHEMA,
                      handler=read_room, check_fn=_check_available, emoji="💬")
    ctx.register_tool(name="room_inbox", toolset=TOOLSET, schema=_INBOX_SCHEMA,
                      handler=room_inbox, check_fn=_check_available, emoji="📥")
    ctx.register_tool(name="room_digest", toolset=TOOLSET, schema=_DIGEST_SCHEMA,
                      handler=room_digest, check_fn=_check_available, emoji="🧾")
    ctx.register_tool(name="room_threads", toolset=TOOLSET, schema=_THREADS_SCHEMA,
                      handler=room_threads, check_fn=_check_available, emoji="🧵")
    ctx.register_tool(name="wait_for_reply", toolset=TOOLSET, schema=_WAIT_SCHEMA,
                      handler=wait_for_reply, check_fn=_check_available, emoji="⏳")
