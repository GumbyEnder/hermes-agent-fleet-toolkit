"""Agent Chat Room plugin — self-hosted agent-to-agent dialogue over the local bus.

Tools registered in the ``agent_chat`` toolset:
  send_to_room(text, topic?)  — post a message to the room
  read_room(since_id?, limit?, topic?) — fetch dialogue since a message id

Config (env, per profile):
  AGENT_CHAT_SECRET   shared HMAC secret (required; gates the toolset)
  AGENT_CHAT_URL      bus base URL (default http://127.0.0.1:8090)
  AGENT_CHAT_NAME     sender name (default: HERMES_PROFILE or hostname)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

BUS_URL = os.environ.get("AGENT_CHAT_URL", "http://127.0.0.1:8090").rstrip("/")
TOOLSET = "agent_chat"

_SEND_SCHEMA = {
    "name": "send_to_room",
    "description": "Post a message to the shared Agent Chat Room so other agents (and the human) can read it. Keep messages concise and substantive.",
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Message body (markdown allowed)."},
            "topic": {"type": "string", "description": "Conversation topic/channel name, e.g. 'build-plan'."},
        },
        "required": ["text"],
    },
}

_READ_SCHEMA = {
    "name": "read_room",
    "description": "Read dialogue from the shared Agent Chat Room. Returns messages after since_id (0 = from the start).",
    "parameters": {
        "type": "object",
        "properties": {
            "since_id": {"type": "integer", "description": "Return only messages with id > this (default 0)."},
            "limit": {"type": "integer", "description": "Max messages to return (default 100)."},
            "topic": {"type": "string", "description": "Only messages for this topic."},
        },
        "required": [],
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


def send_to_room(args: dict, **kw) -> str:
    secret = _secret()
    if not secret:
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
    body = json.dumps(payload).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    try:
        resp = json.loads(_post("/send", body, sig))
    except urllib.error.HTTPError as e:
        return f"error: bus returned HTTP {e.code}"
    except Exception as e:
        return f"error: {e}"
    if resp.get("ok"):
        return f"posted to room as {payload['from']} (topic: {payload['topic']}, id {resp.get('id')})"
    return f"error: {resp}"


def read_room(args: dict, **kw) -> str:
    secret = _secret()
    if not secret:
        return "error: AGENT_CHAT_SECRET not set in this profile"
    since = int(args.get("since_id") or 0)
    limit = min(int(args.get("limit") or 100), 500)
    topic = args.get("topic")
    qs = f"?since={since}&limit={limit}"
    if topic:
        qs += f"&topic={urllib.request.quote(str(topic))}"
    try:
        with urllib.request.urlopen(BUS_URL + "/api/messages" + qs, timeout=15) as r:
            msgs = json.loads(r.read())
    except Exception as e:
        return f"error: {e}"
    if not msgs:
        return f"no new messages since id {since}"
    lines = [
        f"[{m['id']}] {m['sender']} ({m['topic']}, {m['kind']}): {m['text']}"
        for m in msgs
    ]
    return "\n\n".join(lines)


def register(ctx) -> None:
    ctx.register_tool(name="send_to_room", toolset=TOOLSET, schema=_SEND_SCHEMA,
                      handler=send_to_room, check_fn=_check_available, emoji="💬")
    ctx.register_tool(name="read_room", toolset=TOOLSET, schema=_READ_SCHEMA,
                      handler=read_room, check_fn=_check_available, emoji="💬")
