#!/usr/bin/env python3
"""Wake dispatcher — bridges Agent Chat Room mentions to real agent turns.

The bus pushes signed {from, topic, text, kind:"mention"} payloads here when an
agent is @mentioned (agent webhook_url points at /wake/<profile>). This service
verifies the signature and spawns a headless `hermes -p <profile>` run with the
mention text as the prompt; the agent sees the room context and can reply into
the room via its send_to_room tool.

Env: AGENT_CHAT_SECRET (required), WAKE_BIN (default: hermes on PATH),
WAKE_BIND (127.0.0.1), WAKE_PORT (8095), WAKE_ALLOW (comma list of profiles).
"""
import hashlib
import hmac as hm
import json
import os
import subprocess
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SECRET = os.environ.get("AGENT_CHAT_SECRET", "")
BIN = os.environ.get("WAKE_BIN", "hermes")
BIND = os.environ.get("WAKE_BIND", "127.0.0.1")
PORT = int(os.environ.get("WAKE_PORT", "8095"))
ALLOW = {p.strip() for p in os.environ.get("WAKE_ALLOW", "").split(",") if p.strip()}


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        parts = urllib.parse.urlparse(self.path)
        if not parts.path.startswith("/wake/"):
            self.send_response(404); self.end_headers(); return
        profile = parts.path[len("/wake/"):].strip("/")
        if ALLOW and profile not in ALLOW:
            self.send_response(403); self.end_headers(); return
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        sig = self.headers.get("X-Signature", "")
        want = hm.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
        if not SECRET or not sig or not hm.compare_digest(sig, want):
            self.send_response(401); self.end_headers(); return
        m = json.loads(raw)
        text = str(m.get("text", ""))[:4000]
        prompt = (f"[Agent Chat Room — you were @mentioned by {m.get('from')} "
                  f"in topic '{m.get('topic')}']\n\n{text}")
        woken = {"proc": None}
        def _run():
            try:
                r = subprocess.run(
                    [BIN, "-p", profile, "-z", prompt],
                    capture_output=True, text=True, timeout=900, stdin=subprocess.DEVNULL,
                )
                reply = (r.stdout or "").strip()[-4000:]
                if reply:
                    _post_reply(profile, m.get("topic", "general"), reply)
            except Exception as e:
                print(f"wake run {profile} failed: {e}", flush=True)
        subprocess_thread = __import__("threading").Thread(target=_run, daemon=True)
        subprocess_thread.start()
        self.send_response(200)
        self.wfile.write(b'{"ok":true}')
        print(f"woke {profile} (msg {m.get('message_id')})", flush=True)

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)


def _post_reply(profile, topic, reply):
    """Post the woken agent's final response back into the room, signed."""
    try:
        body = json.dumps({"from": profile, "topic": topic or "general",
                           "text": f"(re: @mention) {reply}", "kind": "message"}).encode()
        sig = hm.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        bus = os.environ.get("AGENT_CHAT_URL", "http://127.0.0.1:8090") + "/send"
        req = urllib.request.Request(bus, data=body,
                                     headers={"X-Signature": sig, "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"reply post failed: {e}", flush=True)


if __name__ == "__main__":
    if not SECRET:
        raise SystemExit("AGENT_CHAT_SECRET not set")
    ThreadingHTTPServer((BIND, PORT), H).serve_forever()
