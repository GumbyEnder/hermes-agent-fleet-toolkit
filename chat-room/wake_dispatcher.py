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
  WAKE_TIMEOUT           seconds per turn (default 900). This is a REPORT, not a
                         kill: `hermes -p P -z` is a gateway client holding the
                         per-profile flock, so killing it at the budget released
                         the gate while the turn ran on (buzzy/4205 -> 4254+4257)
  WAKE_HARD_CAP          seconds per turn before the client and its process group
                         are actually killed (default: budget + max(60, budget/2))
  WAKE_DEDUPE_TTL        seconds to remember a delivered mention (default 600)
  WAKE_RETRY_MAX         re-spills for a turn that died with no reply, before it
                         is abandoned (default 2; 0 disables retries)
  WAKE_TEXT_MAX          last-resort cap on the mention body (default 100000);
                         only reached when the re-fetch fails, and a cut is
                         always marked in the prompt
  WAKE_SPILL             JSONL file holding accepted-but-unfinished wakes
                         (default ~/agents/agent-chat/wake-spill.jsonl)
  WAKE_GATEWAY_HOME      hermes home whose gateway answers the turn gate
                         (default ~/.hermes; a profile's own home is also asked).
                         The gate is the gateway's `active-work` answer, because
                         the flock belongs to the client that asks for a turn,
                         not to the turn itself.
"""
import contextlib
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


HARD_CAP = int(os.environ.get("WAKE_HARD_CAP", "0") or 0)


def hard_cap_for(profile):
    """Point at which a turn is killed, as opposed to merely reported as slow.

    The budget is no longer a kill (see `_run_one`): the wrapper is a *client*
    of the gateway, so killing it releases the gate while the turn runs on. The
    hard cap exists only so a wedged client cannot hold a profile's gate for
    ever; it is deliberately past the budget, so an honest overshoot posts its
    reply instead of being killed and re-run as a duplicate.
    """
    if HARD_CAP:
        return HARD_CAP
    budget = timeout_for(profile)
    return budget + max(60, budget // 2)


DEDUPE_TTL = int(os.environ.get("WAKE_DEDUPE_TTL", "600"))
RETRY_MAX = max(0, int(os.environ.get("WAKE_RETRY_MAX", "2")))

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
_retried = 0
_abandoned = 0
_skipped_answered = 0
_gate_busy = 0
_inqueue = set()        # seqs currently in _work (one copy each)

# --- spill: the queue lives in this process, so a restart used to eat it -------
# A SIGTERM dropped every accepted-but-unfinished wake: the HTTP 200 had already
# been handed to the bus, so nothing retried them and the mentions went unanswered
# (bus_watch advances its cursor on 200). Every accepted payload is therefore
# written to WAKE_SPILL until its turn finishes, and re-queued at startup.
_spill_lock = threading.Lock()
_pending = {}           # seq -> {"profile": str, "payload": dict, "attempts": int}
_overflow = []          # seqs HELD because _work was full, oldest first
_seq = int(time.time() * 1000)
_draining = False
SPILL_MAX = max(1, int(os.environ.get("WAKE_SPILL_MAX", "200")))


def _spill_key(profile, payload):
    """Identity of a wake: one turn per (profile, message). None when unknown."""
    mid = payload.get("message_id") if isinstance(payload, dict) else None
    return (profile, mid) if isinstance(mid, int) and mid > 0 else None


def _key_held_locked(profile, payload):
    """Seq already holding this (profile, message) in the spill, or None.

    The TTL dedupe in `_claim` forgets a mention after 10 minutes, which is why
    the same message woke a profile twice 37 minutes apart (4148 -> 4173, 4188)
    and why the spill file carried the same key twice. The spill outlives the
    TTL, so the file itself is the durable answer to "is this already queued".
    """
    k = _spill_key(profile, payload)
    if k is None:
        return None
    for seq, rec in _pending.items():
        if _spill_key(rec["profile"], rec["payload"]) == k:
            return seq
    return None


def _spill_flush_locked():
    """Rewrite the spill file from `_pending`; caller holds `_spill_lock`.

    Whole-file rewrite inside one process, at most MAX_QUEUE+MAX_CONC records, so
    cost is a few hundred bytes. os.replace keeps the file readable to any
    observer (a truncated file would read as "queue empty" at the next boot).
    The attempt count is persisted: a turn that dies on a budget kill must not
    restart its retries from zero every time the dispatcher is restarted.
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


def _spill_add(profile, payload, attempts=0):
    """Add a wake to the spill. Returns its seq, or None when the key is held."""
    global _seq
    with _spill_lock:
        held = _key_held_locked(profile, payload)
        if held is not None:
            return None
        _seq += 1
        seq = _seq
        _pending[seq] = {"profile": profile, "payload": payload,
                         "attempts": attempts}
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


def _spill_retry(seq, profile, payload):
    """A turn died with no reply: hold its record for another attempt.

    Timeout is the case that forced this. `subprocess.run(timeout=...)` returns
    control to the wrapper, but the turn child can outlive it and post later
    (4164: rc null at 900.1 s, posted_id 0 — the orphan posted #4200 eleven
    seconds after the dispatcher gave up). Dropping the record loses the mention
    when the orphan dies, and re-running it blindly double-posts when it does
    not. So: hold it, and let the answered-check at re-entry decide.
    """
    global _retried, _abandoned
    with _spill_lock:
        rec = _pending.get(seq)
        if rec is None:
            return False
        attempts = rec.get("attempts", 0)
        if attempts >= RETRY_MAX:
            _pending.pop(seq, None)
            try:
                _overflow.remove(seq)
            except ValueError:
                pass
            _spill_flush_locked()
            give_up = True
        else:
            rec["attempts"] = attempts + 1
            if seq not in _overflow:
                _overflow.append(seq)
            _spill_flush_locked()
            give_up = False
    mid = payload.get("message_id") if isinstance(payload, dict) else None
    if give_up:
        with _lock:
            _abandoned += 1
        log(evt="wake_abandoned", profile=profile, message_id=mid,
            attempts=attempts, retry_max=RETRY_MAX)
        return False
    with _lock:
        _retried += 1
    log(evt="wake_retry_spilled", profile=profile, message_id=mid,
        attempt=attempts + 1, retry_max=RETRY_MAX)
    return True


def _enqueue(profile, payload, seq):
    """Put a record on the work queue, or hold it for the pump when the queue is full.

    A full queue used to answer 429. The bus pushes the wake fire-and-forget and
    never retries, so a 429 was a permanently unanswered mention (14 of them in
    the six hours to 18:50 on 2026-11-13). A record is now accepted once it is on
    disk; the in-memory queue is just the working window.

    A seq already queued is never queued twice: a dump-and-re-enqueue deferral
    (gate busy, gate held, gate lock) runs per worker, and two workers deferring
    the same record used to leave two copies in the queue — two turns under one
    mention, which is the duplicate this file exists to prevent.
    """
    if seq is None:          # duplicate key already held; nothing to enqueue
        return False
    with _lock:
        if seq in _inqueue:
            return True
    try:
        _work.put_nowait((profile, payload, seq))
        with _lock:
            _inqueue.add(seq)
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
                with _lock:
                    _inqueue.add(seq)
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
            attempts = int(rec.get("attempts", 0) or 0)
            if attempts < 0:
                raise ValueError("bad attempts")
        except Exception:  # noqa: BLE001
            bad += 1
            continue
        out.append((profile, payload, attempts))
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


def _stamp_holder(fd, profile, pid):
    """Write 'pid profile ts' into the lock file for diagnosis only.

    The pid is the *waiter*, not the turn: `hermes -p P -z` is a gateway client,
    so the turn runs under hermes-gateway.service. That is why the flock, not
    this line, is the gate — and why `_run_one` re-stamps it with the waiter's
    pid once there is one (the dispatcher's own pid was dead by construction).
    """
    if not isinstance(fd, int) or fd < 0:
        return
    try:
        os.ftruncate(fd, 0)
        os.pwrite(fd, f"{pid} {profile} {int(time.time())}\n".encode(), 0)
    except OSError:
        pass


def _control_socket_homes(profile):
    """Candidate unix sockets: the fleet gateway, then a profile's own gateway."""
    homes = [os.path.expanduser(os.environ.get("WAKE_GATEWAY_HOME", "~/.hermes"))]
    homes.append(os.path.expanduser(f"~/.hermes/profiles/{profile}"))
    return homes


def _control(home, verb, params=None, timeout=2.0):
    """Ask a gateway control verb over its unix socket; None when nothing answers.

    Deliberately stdlib-only and dependency-free: this service runs on the system
    python, not the hermes venv, so it cannot import gateway.control_socket.
    """
    for name in ("gateway.sock", "gateway.sock.path"):
        path = os.path.join(home, name)
        try:
            if name.endswith(".path"):
                with open(path, encoding="utf-8-sig") as fh:
                    path = fh.read().strip()
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        except OSError:
            continue
        try:
            sock.settimeout(timeout)
            sock.connect(path)
            req = {"verb": verb, "id": 1, "protocol": 1}
            if params:
                req["params"] = params
            sock.sendall(json.dumps(req).encode() + b"\n")
            buf = b""
            while b"\n" not in buf and len(buf) < 262144:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
            answer = json.loads(buf.split(b"\n", 1)[0].decode("utf-8", "replace"))
            return answer.get("result") if answer.get("ok") is True else None
        except (OSError, ValueError):
            continue
        finally:
            with contextlib.suppress(OSError):
                sock.close()
    return None


def _gateway_turn_active(profile):
    """Is a *turn* running for `profile` in the process that runs turns?

    Returns (state, units, pid): state is "busy", "idle" or "unknown".

    The flock is held by the process that ASKS for a turn (`hermes -p P -z` is a
    gateway client), so it proves nothing once that client is gone while the
    gateway's turn runs on: buzzy/4205 -> 4254 (client rc -15, turn posted 419.5 s
    later) and the duplicate 4257 beside it. The gateway answers from its own
    running state (`active-work` control verb), and the pid it names is the one
    that actually owns the turn — evidence, not a pid we do not own.
    """
    answered = False
    for home in _control_socket_homes(profile):
        result = _control(home, "active-work", {"profile": profile})
        if not isinstance(result, dict):
            continue
        answered = True
        units = [u for u in (result.get("active_work") or []) if isinstance(u, dict)]
        if units:
            return "busy", units, result.get("pid")
    return ("idle" if answered else "unknown"), [], None


def _run_one(profile, payload, lock_fd=-1):
    global _done, _failed
    started = time.time()
    message_id = payload.get("message_id") or 0
    text, text_src = _mention_text(payload)
    prompt = (f"[Agent Chat Room — you were @mentioned by {payload.get('from')} "
              f"in topic '{payload.get('topic')}']\n\n{text}")
    budget = timeout_for(profile)
    hard = hard_cap_for(profile)
    rc, reply, err = None, "", ""
    kwargs: dict = {"start_new_session": True}   # so the hard cap can kill the group
    if isinstance(lock_fd, int) and lock_fd >= 0:
        kwargs["pass_fds"] = (lock_fd,)
    late, abandoned = False, False
    try:
        p = subprocess.Popen([BIN, "-p", profile, "-z", prompt],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, stdin=subprocess.DEVNULL, **kwargs)
        # The flock this child inherits is the only thing standing between a
        # restarted dispatcher and a rival turn under the same mention, so the
        # budget must not be a kill: `hermes -z` is a gateway *client*, and
        # killing it releases the fd while the turn runs on (buzzy's 4205:
        # wrapper rc -15, the turn posted 4254 419.5 s later).
        _stamp_holder(lock_fd, profile, p.pid)
        while True:
            try:
                out, errs = p.communicate(timeout=1.0)
                break
            except subprocess.TimeoutExpired:
                now = time.time()
                if not late and now - started >= budget:
                    late = True
                    log(evt="wake_over_budget", profile=profile,
                        message_id=message_id, budget_s=budget,
                        hard_cap_s=hard, gate="held")
                if now - started >= hard:
                    abandoned = True
                    try:
                        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                    except OSError:
                        p.kill()
                    out, errs = p.communicate()
                    break
        rc = p.returncode
        reply = (out or "").strip()[-4000:]
        err = (errs or "")[:400] if rc != 0 or not reply else ""
        if abandoned:
            err = f"{err or ''} killed at hard cap {hard}s (budget {budget}s)"
    except Exception as e:  # noqa: BLE001  (no argv in the message: it is the prompt)
        err = f"{type(e).__name__}: {str(e)[:200]}"
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
    return {"rc": rc, "reply": reply, "err": err, "posted": posted}


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
    global _active, _rejected_full, _skipped_answered, _gate_busy
    while True:
        profile, payload, seq = _work.get()
        with _lock:
            _inqueue.discard(seq)
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
        # An acquired flock means the wrapper that asked for the turn is gone — not
        # that the turn is. Ask the process that runs turns; only it can answer.
        # No fixed hold: the record is deferred until this answers idle, so a
        # restart does not stall the queue for a budget (the cheap guard buzzy
        # tried and reverted: spilled 2 -> queued 1 held_for_repump 1 for 900 s).
        gstate, gunits, gpid = _gateway_turn_active(profile)
        if gstate == "busy":
            with _lock:
                _gate_busy += 1
            log(evt="gate_held_busy", profile=profile,
                message_id=payload.get("message_id"),
                gateway_pid=gpid, units=[(u.get("kind"), u.get("session"),
                                          u.get("elapsed_s"), u.get("current_tool"))
                                         for u in gunits],
                holder=(holder or None))
            gate.release()
            try:
                os.close(lock_fd)
            except OSError:
                pass
            _enqueue(profile, payload, seq)
            _work.task_done()
            time.sleep(3.0)
            continue
        if gstate == "unknown":
            # No gateway answered: the gate is the flock alone, as before. Fail
            # open — a gateway that is down cannot run the turn anyway, and the
            # retry/spill path holds the record.
            log(evt="gate_unverified", profile=profile,
                message_id=payload.get("message_id"))
        with _lock:
            _active += 1
        retry = False
        try:
            # The answered-check belongs at the choke point, not only at boot.
            # Boot-time was the only place it ran, so a re-delivered or repumped
            # mention ran a whole turn under a message that already had this
            # profile's reply: 4148 -> 4173 then 4188 (37 min apart, past the
            # 600 s TTL), 4073 -> 4136 then a 900 s turn that could only post
            # duplicate coverage (4204). Inside the profile gate no turn of ours
            # is in flight, so "a reply exists" cannot be our own work in progress.
            mid = payload.get("message_id")
            if _already_answered(profile, mid):
                with _lock:
                    _skipped_answered += 1
                log(evt="wake_skip_answered", profile=profile, message_id=mid)
            else:
                out = _run_one(profile, payload, lock_fd)
                # "Finished" means a clean exit. rc=-15 (the service was stopped
                # under the turn) and a budget kill (rc None) both leave a turn
                # with no reply; neither is an answer. rc=0 with empty stdout is
                # a model that chose silence, and that is allowed.
                retry = bool(not out["reply"] and (out["err"] or out["rc"] != 0))
        finally:
            with _lock:
                _active -= 1
            gate.release()
            if isinstance(lock_fd, int) and lock_fd >= 0:
                try:
                    os.close(lock_fd)   # the child's inherited copy owns it now
                except OSError:
                    pass
            if retry:
                _spill_retry(seq, profile, payload)
            else:
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
                        "spilled": spilled,
                        # HELD, not rejected: records on disk waiting for a queue
                        # slot. The old name ("overflow") read as "dropped at the
                        # cap" and oscillated 1..9 on every pump tick, which is
                        # what a hold looks like. Rejections are rejected_full.
                        "held_for_repump": len(_overflow),
                        "retried": _retried, "abandoned": _abandoned,
                        "skipped_answered": _skipped_answered,
                        "gate_busy": _gate_busy,
                        "retry_max": RETRY_MAX,
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
        if seq is None:
            # Same (profile, message_id) is still queued: the durable spill, not
            # the 600 s TTL window, is what makes this idempotent.
            with _lock:
                _deduped += 1
            self._json({"ok": True, "duplicate": True, "held": True,
                        "profile": profile})
            return
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
    global _draining, _seq, _skipped_answered
    if not SECRET:
        raise SystemExit("AGENT_CHAT_SECRET not set")

    # Re-queue whatever the previous process accepted but never finished. The
    # already-answered check is what makes a restart safe: a turn killed after it
    # posted is dropped here instead of double-posting under the same mention.
    # Two lines for the same (profile, message_id) collapse to one: the file
    # outlives the 600 s dedupe window, so it is the durable duplicate guard.
    resumed = []
    for profile, payload, attempts in _spill_load():
        mid = payload.get("message_id")
        if _already_answered(profile, mid):
            with _lock:
                _skipped_answered += 1
            log(evt="spill_skip_answered", profile=profile, message_id=mid)
            continue
        seq = _spill_add(profile, payload, attempts)
        if seq is None:
            log(evt="spill_dup_dropped", profile=profile, message_id=mid)
            continue
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
