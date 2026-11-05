"""Test helpers: run the real services as subprocesses on throwaway ports/DBs."""
import contextlib
import hashlib
import hmac
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
SECRET = "test-secret-not-real"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def base_env(**over):
    e = dict(os.environ)
    e.pop("AGENT_CHAT_BIND", None)
    e.pop("WAKE_ALLOW", None)
    e.update({"AGENT_CHAT_SECRET": SECRET, "PYTHONUNBUFFERED": "1"})
    e.update(over)
    return e


def sign(raw: bytes) -> str:
    return hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()


def get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.read().decode()


def get_json(url, timeout=10):
    return json.loads(get(url, timeout)[1])


def post(url, payload, signed=True, sig=None, timeout=15):
    raw = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=raw, method="POST")
    req.add_header("Content-Type", "application/json")
    if sig is not None:
        req.add_header("X-Signature", sig)
    elif signed:
        req.add_header("X-Signature", sign(raw))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


class Service:
    """A subprocess service with a readiness poll and full output capture."""

    def __init__(self, script, env, health_url, cwd=None):
        self.script = script
        self.env = env
        self.health_url = health_url
        self.proc = None
        self.cwd = cwd or str(ROOT)

    def __enter__(self):
        self.proc = subprocess.Popen([sys.executable, self.script], env=self.env,
                                     cwd=self.cwd, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True)
        deadline = time.time() + 20
        last = ""
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"{self.script} exited: {(self.proc.stdout.read() or '')[-800:]}")
            try:
                if get(self.health_url, timeout=2)[0] == 200:
                    return self
            except (urllib.error.URLError, OSError) as e:
                last = str(e)
            time.sleep(0.15)
        raise RuntimeError(f"{self.script} never became ready ({last})")

    def __exit__(self, *exc):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.proc and self.proc.stdout:
            try:
                self.proc.stdout.close()
            except Exception:  # noqa: BLE001
                pass

    def output(self):
        try:
            return self.proc.stdout.read() if self.proc.stdout else ""
        except ValueError:
            return ""


@contextlib.contextmanager
def bus(**over):
    """Run the bus with a temp DB on a free port. Yields (base_url, tmpdir, service)."""
    tmp = tempfile.mkdtemp(prefix="fleet-test-")
    port = free_port()
    over.setdefault("AGENT_CHAT_DB", os.path.join(tmp, "room.db"))
    env = base_env(AGENT_CHAT_PORT=str(port), AGENT_CHAT_BACKUP="",
                   AGENT_CHAT_JANITOR_INTERVAL="3600", **over)
    with Service(str(ROOT / "chat-room/bus.py"), env, f"http://127.0.0.1:{port}/health") as svc:
        yield f"http://127.0.0.1:{port}", tmp, svc


@contextlib.contextmanager
def dispatcher(base_url, **over):
    port = free_port()
    env = base_env(AGENT_CHAT_URL=base_url, WAKE_PORT=str(port), **over)
    with Service(str(ROOT / "chat-room/wake_dispatcher.py"), env,
                 f"http://127.0.0.1:{port}/health") as svc:
        yield f"http://127.0.0.1:{port}", svc
