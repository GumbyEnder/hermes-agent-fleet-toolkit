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
  WAKE_SPILL             JSONL file holding accepted-but-unfinished wakes
                         (default ~/agents/agent-chat/wake-spill.jsonl)
"""
import fcntl
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
SPILL = os.path.abspath(os.path.expanduser(
    os.environ.get("WAKE_SPILL", "~/agents/agent-chat/wake-spill.jsonl")))
LOCK_DIR = os.path.abspath(os.path.expanduser(
    os.environ.get("WAKE_LOCK_DIR", "~/agents/agent-chat/locks")))


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

# --- spill: the queue lives in this process, so a restart used to eat it -------
# A SIGTERM dropped every accepted-but-unfinished wake: the HTTP 200 had already
# been handed to the bus, so nothing retried them and the mentions went unanswered
# (bus_watch advances its cursor on 200). Every accepted payload is therefore
# written to WAKE_SPILL until its turn finishes, and re-queued at startup.
_spill_lock = threading.Lock()
_pending = {}           # seq -> {"profile": str, "payload": dict}
_overflow = []          # seqs spilled because _work was full, oldest first
_seq = int(time.time() * 1000)
_draining = False
SPILL_MAX = max(1, int(os.environ.get("WAKE_SPILL_MAX", "200")))


def _spill_flush_locked():
    """Rewrite the spill file from `_pending`; caller holds `_spill_lock`.

    Whole-file rewrite inside one process, at most MAX_QUEUE+MAX_CONC records, so
    cost is a few hundred bytes. os.replace keeps the file readable to any
    observer (a truncated file would read as "queue empty" at the next boot).
    """
    tmp = SPILL + ".tmp"
    try:
        os.makedirs(os.path.dirname(SPILL), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            for seq in sorted(_pending):
                rec = dict(_pending[seq], seq=seq)
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, SPILL)
    except OSError as e:  # noqa: BLE001
        log(evt="spill_write_failed", path=SPILL, error=str(e)[:200])


def _spill_add(profile, payload):
    global _seq
    with _spill_lock:
        _seq += 1
        seq = _seq
        _pending[seq] = {"profile": profile, "payload": payload}
        _spill_flush_locked()
        return seq


def _spill_drop(seq):
    with _spill_lock:
        if _pending.pop(seq, None) is not None:
            try:
                _overflow.remove(seq)
            except ValueError:
                pass
            _spill_flush_locked()


def _enqueue(profile, payload, seq):
    """Put a record on the work queue, or hold it for the pump when the queue is full.

    A full queue used to answer 429. The bus pushes the wake fire-and-forget and
    never retries, so a 429 was a permanently unanswered mention (14 of them in
    the six hours to 18:50 on 2026-11-13). A record is now accepted once it is on
    disk; the in-memory queue is just the working window.
    """
    try:
        _work.put_nowait((profile, payload, seq))
        return True
    except queue.Full:
        with _spill_lock:
            if seq in _pending and seq not in _overflow:
                _overflow.append(seq)
        return False


def _pump_loop():
    """Feed held records into the work queue as it frees up."""
    while True:
        time.sleep(1.0)
        moved = 0
        while True:
            with _spill_lock:
                if not _overflow or _work.qsize() >= MAX_QUEUE:
                    break
                seq = _overflow[0]
                rec = _pending.get(seq)
                if rec is None:
                    _overflow.pop(0)
                    continue
                try:
                    _work.put_nowait((rec["profile"], rec["payload"], seq))
                except queue.Full:
                    break
                _overflow.pop(0)
                moved += 1
        if moved:
            log(evt="spill_repump", moved=moved, overflow=len(_overflow))


def _spill_load():
    """Records left over from a previous process. Malformed lines are counted."""
    try:
        with open(SPILL, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        return []
    except OSError as e:  # noqa: BLE001
        log(evt="spill_read_failed", path=SPILL, error=str(e)[:200])
        return []
    out, bad = [], 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            profile, payload = rec["profile"], rec["payload"]
            if not isinstance(profile, str) or not isinstance(payload, dict):
                raise ValueError("bad record shape")
        except Exception:  # noqa: BLE001
            bad += 1
            continue
        out.append((profile, payload))
    log(evt="spill_load", path=SPILL, records=len(out), malformed=bad)
    return out


def _already_answered(profile, message_id):
    """True when this mention already has a reply from this profile in the room.

    A turn killed mid-flight may have posted before it died; re-running it under
    the same mention would double-post. Fail OPEN (run the turn again) when the
    bus cannot be read: a duplicate post is cheaper than an unanswered mention.
    """
    if not isinstance(message_id, int) or message_id <= 0:
        return False
    url = f"{BUS_URL}/api/messages?thread={message_id}&since=0&limit=50"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            rows = json.loads(r.read() or b"[]")
    except Exception as e:  # noqa: BLE001
        log(evt="spill_child_check_failed", profile=profile,
            message_id=message_id, error=str(e)[:200])
        return False
    for row in rows if isinstance(rows, list) else []:
        if (isinstance(row, dict) and row.get("parent_id") == message_id
                and row.get("sender") == profile):
            return True
    return False


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


def _acquire_profile_lock(profile):
    """Machine-wide, restart-proof per-profile turn lock.

    Returns (fd, "") when this process may run a turn for `profile`, else
    (None, holder_line). The fd must be handed to the child and closed only
    after the child exits: the lock then belongs to the *turn*, not to this
    dispatcher process.

    A threading.Lock dies with the process. On 2026-11-13 the dispatcher was
    restarted at 18:58:40 with two turns in flight; the `hermes -p game -z`
    child survived the SIGTERM (recorded rc=-15, i.e. "killed"), the new
    process found an empty gate, re-ran the same mention, and two `game`
    runtimes worked the room under one sender name from 18:58:43 to 19:52:39
    (4091 from the re-run, 4100/4101 from the orphan). flock is held on an
    open file description, so an inherited fd keeps the gate shut for as long
    as the orphan lives — a fresh dispatcher cannot start a rival turn.
    """
    try:
        os.makedirs(LOCK_DIR, exist_ok=True)
        path = os.path.join(LOCK_DIR, f"{profile}.lock")
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as e:  # noqa: BLE001
        log(evt="gate_lock_unavailable", profile=profile, error=str(e)[:200])
        return -1, ""          # fail OPEN on a broken lock dir, as before
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        holder = ""
        try:
            holder = os.pread(fd, 200, 0).decode("utf-8", "replace").strip()
        except OSError:
            pass
        os.close(fd)
        return None, holder
    try:
        os.ftruncate(fd, 0)
        os.pwrite(fd, f"{os.getpid()} {profile} {int(time.time())}\n".encode(), 0)
    except OSError:
        pass
    return fd, ""


def _run_one(profile, payload, lock_fd=-1):
    global _done, _failed
    started = time.time()
    message_id = payload.get("message_id") or 0
    text, text_src = _mention_text(payload)
    prompt = (f"[Agent Chat Room — you were @mentioned by {payload.get('from')} "
              f"in topic '{payload.get('topic')}']\n\n{text}")
    budget = timeout_for(profile)
    rc, reply, err = None, "", ""
    kwargs = {}
    if isinstance(lock_fd, int) and lock_fd >= 0:
        kwargs["pass_fds"] = (lock_fd,)
    try:
        r = subprocess.run([BIN, "-p", profile, "-z", prompt],
                           capture_output=True, text=True, timeout=budget,
                           stdin=subprocess.DEVNULL, **kwargs)
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
        profile, payload, seq = _work.get()
        gate = _gate_for(profile)
        if not gate.acquire(blocking=False):
            # another turn for this profile is running: put this one back at the
            # tail (or hold it on disk) so other profiles are not blocked.
            _enqueue(profile, payload, seq)
            _work.task_done()
            time.sleep(0.5)
            continue
        lock_fd, holder = _acquire_profile_lock(profile)
        if lock_fd is None:
            # A turn for this profile is already in flight on this host — usually
            # an orphan from a restarted dispatcher that is still working. Re-queue
            # rather than race it under the same sender name.
            log(evt="gate_held", profile=profile,
                message_id=payload.get("message_id"), holder=(holder or None))
            gate.release()
            _enqueue(profile, payload, seq)
            _work.task_done()
            time.sleep(5.0)
            continue
        with _lock:
            _active += 1
        try:
            _run_one(profile, payload, lock_fd)
        finally:
            with _lock:
                _active -= 1
            gate.release()
            if isinstance(lock_fd, int) and lock_fd >= 0:
                try:
                    os.close(lock_fd)   # the child's inherited copy owns it now
                except OSError:
                    pass
            _spill_drop(seq)
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
        with _spill_lock:
            spilled = len(_pending)
        with _lock:
            self._json({"status": "ok", "allowed": sorted(ALLOW),
                        "active": _active, "queued": _work.qsize(),
                        "max_concurrency": MAX_CONC, "max_queue": MAX_QUEUE,
                        "done": _done, "failed": _failed, "deduped": _deduped,
                        "rejected_full": _rejected_full,
                        "draining": _draining,
                        "spilled": spilled, "overflow": len(_overflow),
                        "spill_path": SPILL,
                        "lock_dir": LOCK_DIR,
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
        seq = _spill_add(profile, m)
        with _lock:
            draining = _draining
        with _spill_lock:
            backlog = len(_pending)
        if draining:
            # Stop-and-restart window: accept (the bus already believes the push
            # landed) but spill only. The next process re-queues it.
            self._json({"ok": True, "spilled": True, "draining": True,
                        "profile": profile})
            return
        if backlog > SPILL_MAX:
            # Only a durable backlog this deep is real backpressure; SPILL_MAX is
            # the guard against an unbounded file if every turn stalls.
            _spill_drop(seq)
            with _lock:
                _rejected_full += 1
            self._json({"error": "wake backlog full", "spilled": backlog}, 429,
                       {"Retry-After": "60"})
            return
        queued = _enqueue(profile, m, seq)
        self._json({"ok": True, "queued": queued, "spilled": not queued,
                    "profile": profile, "queued_depth": _work.qsize(),
                    "spilled_depth": backlog})

    def log_message(self, fmt, *args):
        log(evt="http", line=(fmt % args))


def main():
    global _draining, _seq
    if not SECRET:
        raise SystemExit("AGENT_CHAT_SECRET not set")

    # Re-queue whatever the previous process accepted but never finished. The
    # already-answered check is what makes a restart safe: a turn killed after it
    # posted is dropped here instead of double-posting under the same mention.
    resumed = []
    for profile, payload in _spill_load():
        mid = payload.get("message_id")
        if _already_answered(profile, mid):
            log(evt="spill_skip_answered", profile=profile, message_id=mid)
            continue
        with _spill_lock:
            _seq += 1
            seq = _seq
            _pending[seq] = {"profile": profile, "payload": payload}
        resumed.append((seq, profile, payload))
    with _spill_lock:
        _spill_flush_locked()
    log(evt="spill_resume", records=len(resumed))

    for _ in range(MAX_CONC):
        threading.Thread(target=_worker, daemon=True).start()
    srv = ThreadingHTTPServer((BIND, PORT), H)

    stop = threading.Event()

    def _shut(signum, _frame):
        global _draining
        with _lock:
            first = not _draining
            _draining = True
        if first:
            log(evt="draining", signal=signum, active=_active, queued=_work.qsize())
        stop.set()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _shut)
        except (ValueError, OSError):
            pass

    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=watchdog_loop, daemon=True).start()
    threading.Thread(target=_pump_loop, daemon=True).start()
    sd_notify("READY=1")
    log(evt="start", bind=BIND, port=PORT, allowed=sorted(ALLOW),
        workers=MAX_CONC, queue=MAX_QUEUE, timeout=TIMEOUT, spill=SPILL)
    for seq, profile, payload in resumed:
        if not _enqueue(profile, payload, seq):
            log(evt="spill_requeue_held", profile=profile,
                message_id=payload.get("message_id"))
    try:
        stop.wait()
    finally:
        with _spill_lock:
            _spill_flush_locked()
            pending = len(_pending)
        log(evt="stop", active=_active, queued=_work.qsize(), spilled=pending)
        srv.shutdown()


if __name__ == "__main__":
    main()
