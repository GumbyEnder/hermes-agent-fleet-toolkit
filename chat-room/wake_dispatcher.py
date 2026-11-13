#!/usr/bin/env python3
"""Wake dispatcher — bridges Agent Chat Room mentions to real agent turns.

The bus pushes a signed {from, topic, text, message_id, parent_id, kind:"mention"}
payload here when an agent is @mentioned (its webhook_url points at /wake/<profile>).
This service verifies the signature, drops duplicates, bounds concurrency, spawns a
headless `hermes -p <profile> -z ...` turn, and posts the agent's answer back into
the room as that profile — threaded under the message that woke it.

Endpoints:
  GET  /health          -> {status, allowed, active, queued, done, failed, deduped}
  POST /wake/<profile>  {from, topic, text, message_id?, parent_id?}

Env:
  AGENT_CHAT_SECRET      required; same secret as the bus
  AGENT_CHAT_URL         bus base URL used to post replies
  WAKE_BIN               agent binary (default: hermes on PATH)
  WAKE_BIND / WAKE_PORT  127.0.0.1 / 8095
  WAKE_ALLOW             comma list of profiles that may be woken (empty = none)
  WAKE_MAX_CONCURRENCY   parallel agent turns (default 2)
  WAKE_MAX_QUEUE         queued mentions allowed before 429 (default 20)
  WAKE_TIMEOUT           seconds per turn (default 900)
  WAKE_DEDUPE_TTL        seconds to remember a delivered mention (default 600)
  WAKE_TEXT_MAX          last-resort cap on the mention body (default 100000);
                         only reached when the re-fetch fails, and a cut is
                         always marked in the prompt
"""
import hashlib
import hmac as hm
import json
import os
import queue
import signal
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SECRET = os.environ.get("AGENT_CHAT_SECRET", "")
BIN = os.environ.get("WAKE_BIN", "hermes")
BIND = os.environ.get("WAKE_BIND", "127.0.0.1")
PORT = int(os.environ.get("WAKE_PORT", "8095"))
ALLOW = {p.strip() for p in os.environ.get("WAKE_ALLOW", "").split(",") if p.strip()}
MAX_CONC = max(1, int(os.environ.get("WAKE_MAX_CONCURRENCY", "2")))
MAX_QUEUE = max(1, int(os.environ.get("WAKE_MAX_QUEUE", "20")))
TIMEOUT = int(os.environ.get("WAKE_TIMEOUT", "900"))
TEXT_MAX = int(os.environ.get("WAKE_TEXT_MAX", "100000"))
BUS_URL = os.environ.get("AGENT_CHAT_URL", "http://127.0.0.1:8090").rstrip("/")


def _parse_overrides(raw):
    """`game=3600,frodo=1800` -> {"game": 3600, "frodo": 1800}."""
    out = {}
    for part in str(raw or "").split(","):
        if "=" not in part:
            continue
        name, _, val = part.partition("=")
        name = name.strip()
        try:
            secs = int(val.strip())
        except ValueError:
            continue
        if name and secs >= 60:
            out[name] = secs
    return out


TIMEOUT_OVERRIDES = _parse_overrides(os.environ.get("WAKE_TIMEOUT_OVERRIDES", ""))


def timeout_for(profile):
    """Per-profile turn budget; long-running agents (e.g. game) need more than 15 min."""
    return TIMEOUT_OVERRIDES.get(profile, TIMEOUT)


DEDUPE_TTL = int(os.environ.get("WAKE_DEDUPE_TTL", "600"))

START = time.time()
_work = queue.Queue(maxsize=MAX_QUEUE)
_lock = threading.Lock()
_seen = {}              # dedupe key -> first-seen ts
_gates = {}             # profile -> Lock (one concurrent turn per profile)
_gates_lock = threading.Lock()
_active = 0
_done = 0
_failed = 0
_deduped = 0
_rejected_full = 0


def log(**fields):
    fields["ts"] = round(time.time(), 3)
    print(json.dumps(fields, sort_keys=True), flush=True)


def _prune(now=None):
    now = now or time.time()
    for k, ts in list(_seen.items()):
        if now - ts > DEDUPE_TTL:
            _seen.pop(k, None)


def _claim(key):
    """True the first time a key is seen inside the TTL window."""
    now = time.time()
    with _lock:
        _prune(now)
        if key in _seen:
            return False
        _seen[key] = now
        return True


def _post_reply(profile, topic, reply, parent_id=None):
    """Post the woken agent's answer back into the room, signed and threaded."""
    try:
        body = {"from": profile, "topic": topic or "general",
                "text": f"(re: @mention) {reply}", "kind": "message"}
        if parent_id:
            body["reply_to"] = parent_id
        raw = json.dumps(body).encode()
        sig = hm.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
        bus = os.environ.get("AGENT_CHAT_URL", "http://127.0.0.1:8090").rstrip("/") + "/send"
        req = urllib.request.Request(bus, data=raw,
                                     headers={"X-Signature": sig,
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read() or b"{}").get("id", 0)
    except Exception as e:  # noqa: BLE001
        log(evt="reply_failed", profile=profile, error=str(e)[:200])
        return 0


def _row_text(message_id):
    """Full stored text for a message id, straight from the bus; None if unavailable.

    Reads are unsigned on this bus, so no secret is needed here. The row, not the
    push payload, is the authority: a payload that crosses a hop with a length
    bound arrives as a prefix, and a silently clipped post reads exactly like a
    short one (see _mention_text).
    """
    if not isinstance(message_id, int) or message_id <= 0:
        return None
    url = f"{BUS_URL}/api/messages?since={message_id - 1}&limit=5"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            rows = json.loads(r.read() or b"[]")
    except Exception as e:  # noqa: BLE001
        log(evt="text_refetch_failed", message_id=message_id, error=str(e)[:200])
        return None
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict) and row.get("id") == message_id:
            text = row.get("text")
            return text if isinstance(text, str) and text else None
    return None


def _mention_text(payload):
    """Return (text, source) for the mentioning post.

    The old shape was `str(payload.get("text", ""))[:4000]`, which cut every long
    post at 4,000 chars with no marker: the prompt ended mid-sentence and the
    turn could neither see the missing clause nor tell it was missing. Re-fetch
    the row by id (unbounded), and if that fails, cap at TEXT_MAX *visibly*.
    """
    mid = payload.get("message_id")
    fetched = _row_text(mid)
    if fetched is not None:
        return fetched, "refetch"
    text = str(payload.get("text", ""))
    if len(text) > TEXT_MAX:
        text = (text[:TEXT_MAX]
                + f"\n\n[wake: body cut at {TEXT_MAX} of {len(text)} chars — message_id {mid}, "
                  f"read the full row before answering]")
        return text, "payload-cut"
    return text, "payload"


def _run_one(profile, payload):
    global _done, _failed
    started = time.time()
    message_id = payload.get("message_id") or 0
    text, text_src = _mention_text(payload)
    prompt = (f"[Agent Chat Room — you were @mentioned by {payload.get('from')} "
              f"in topic '{payload.get('topic')}']\n\n{text}")
    budget = timeout_for(profile)
    rc, reply, err = None, "", ""
    try:
        r = subprocess.run([BIN, "-p", profile, "-z", prompt],
                           capture_output=True, text=True, timeout=budget,
                           stdin=subprocess.DEVNULL)
        rc, reply = r.returncode, (r.stdout or "").strip()[-4000:]
        err = (r.stderr or "")[:400]
    except Exception as e:  # noqa: BLE001
        err = str(e)[:400]
    posted = _post_reply(profile, payload.get("topic", "general"), reply,
                         message_id if isinstance(message_id, int) and message_id else None) \
        if reply else 0
    with _lock:
        if err and not reply:
            _failed += 1
        else:
            _done += 1
    log(evt="wake", profile=profile, message_id=message_id, rc=rc,
        dur_s=round(time.time() - started, 1), budget_s=budget,
        prompt_chars=len(text), text_src=text_src,
        reply_chars=len(reply), posted_id=posted, error=(err[:200] or None))


def _gate_for(profile):
    """Per-profile turn gate: one Hermes turn per profile at a time.

    Two concurrent turns for the same profile land in the same working tree and
    each reads the room before the other answers, so both post near-identical
    replies under the same mention. Serializing per profile (not per message
    id: separate mentions are separate keys) removes that race.
    """
    with _gates_lock:
        return _gates.setdefault(profile, threading.Lock())


def _worker():
    global _active, _rejected_full
    while True:
        profile, payload = _work.get()
        gate = _gate_for(profile)
        if not gate.acquire(blocking=False):
            # another turn for this profile is running: put this one back at the
            # tail so other profiles are not blocked behind it, and retry.
            try:
                _work.put_nowait((profile, payload))
            except queue.Full:
                with _lock:
                    _rejected_full += 1
            _work.task_done()
            time.sleep(0.5)
            continue
        with _lock:
            _active += 1
        try:
            _run_one(profile, payload)
        finally:
            with _lock:
                _active -= 1
            gate.release()
            _work.task_done()
def sd_notify(state):
    """Minimal sd_notify: READY=1 and WATCHDOG=1 without a dependency."""
    path = os.environ.get("NOTIFY_SOCKET")
    if not path:
        return
    if path.startswith("@"):
        path = "\0" + path[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.sendto(state.encode(), path)
    except OSError:
        pass


def watchdog_loop():
    """Ping systemd at half the configured WatchdogSec; no-op under a manual run."""
    usec = int(os.environ.get("WATCHDOG_USEC", "0") or 0)
    if not usec:
        return
    interval = max(1.0, usec / 2e6)
    while True:
        time.sleep(interval)
        sd_notify("WATCHDOG=1")


class H(BaseHTTPRequestHandler):
    server_version = "wake-dispatcher/2"

    def _json(self, obj, code=200, headers=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urllib.parse.urlparse(self.path).path != "/health":
            self._json({"error": "not found"}, 404)
            return
        with _lock:
            self._json({"status": "ok", "allowed": sorted(ALLOW),
                        "active": _active, "queued": _work.qsize(),
                        "max_concurrency": MAX_CONC, "max_queue": MAX_QUEUE,
                        "done": _done, "failed": _failed, "deduped": _deduped,
                        "rejected_full": _rejected_full,
                        "timeout_s": TIMEOUT,
                        "timeout_overrides": TIMEOUT_OVERRIDES,
                        "dedupe_ttl_s": DEDUPE_TTL,
                        "uptime_s": round(time.time() - START, 1)})

    def do_POST(self):
        global _deduped, _rejected_full
        parts = urllib.parse.urlparse(self.path)
        if not parts.path.startswith("/wake/"):
            self._json({"error": "not found"}, 404)
            return
        profile = parts.path[len("/wake/"):].strip("/")
        if not ALLOW or profile not in ALLOW:
            self._json({"error": f"profile {profile!r} not in WAKE_ALLOW"}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length > 262144:
            self._json({"error": "body too large"}, 413)
            return
        raw = self.rfile.read(length) if length else b""
        sig = self.headers.get("X-Signature", "")
        want = hm.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
        if not SECRET or not sig or not hm.compare_digest(sig, want):
            self._json({"error": "bad signature"}, 401)
            return
        try:
            m = json.loads(raw or b"{}")
        except Exception:  # noqa: BLE001
            self._json({"error": "bad json"}, 400)
            return

        key = (profile, m.get("message_id") or
               hashlib.sha256(str(m.get("text", "")).encode()).hexdigest()[:16])
        if not _claim(key):
            with _lock:
                _deduped += 1
            self._json({"ok": True, "duplicate": True, "profile": profile})
            return
        try:
            _work.put_nowait((profile, m))
        except queue.Full:
            with _lock:
                _rejected_full += 1
            self._json({"error": "wake queue full", "queued": _work.qsize()}, 429,
                       {"Retry-After": "10"})
            return
        self._json({"ok": True, "queued": True, "profile": profile,
                    "queued_depth": _work.qsize()})

    def log_message(self, fmt, *args):
        log(evt="http", line=(fmt % args))


def main():
    if not SECRET:
        raise SystemExit("AGENT_CHAT_SECRET not set")
    for _ in range(MAX_CONC):
        threading.Thread(target=_worker, daemon=True).start()
    srv = ThreadingHTTPServer((BIND, PORT), H)

    stop = threading.Event()

    def _shut(signum, _frame):
        stop.set()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _shut)
        except (ValueError, OSError):
            pass

    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=watchdog_loop, daemon=True).start()
    sd_notify("READY=1")
    log(evt="start", bind=BIND, port=PORT, allowed=sorted(ALLOW),
        workers=MAX_CONC, queue=MAX_QUEUE, timeout=TIMEOUT)
    try:
        stop.wait()
    finally:
        log(evt="stop", active=_active, queued=_work.qsize())
        srv.shutdown()


if __name__ == "__main__":
    main()
