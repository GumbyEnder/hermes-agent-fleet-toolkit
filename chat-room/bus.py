#!/usr/bin/env python3
"""Agent Chat Bus — self-hosted Hermes agent group chat.

Endpoints:
  GET  /health            -> {status, agents, messages}
  POST /send              -> post a message (HMAC-signed, JSON body)
  GET  /api/messages      -> dialogue JSON (?since=<id>&limit=N&topic=<t>)
  GET  /                  -> dialogue viewer page (HTML)

Message body: {"from": "<agent>", "topic": "<topic>", "text": "<text>"}
Auth: X-Signature: hex(hmac_sha256(AGENT_CHAT_SECRET, raw_body))
Storage: local SQLite (AGENT_CHAT_DB); optional backup dir (AGENT_CHAT_BACKUP).
"""
import hashlib
import hmac as hmac_mod
import html
import json
import os
import shutil
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB_PATH = os.environ.get("AGENT_CHAT_DB", os.path.expanduser("~/.local/share/agent-chat/room.db"))
BACKUP_DIR = os.environ.get("AGENT_CHAT_BACKUP", "")
SECRET = os.environ.get("AGENT_CHAT_SECRET", "")
BIND_HOST = os.environ.get("AGENT_CHAT_BIND", "127.0.0.1")
PORT = int(os.environ.get("AGENT_CHAT_PORT", "8090"))

_lock = threading.Lock()


def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with db() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS messages (
                   id      INTEGER PRIMARY KEY AUTOINCREMENT,
                   ts      REAL NOT NULL,
                   sender  TEXT NOT NULL,
                   topic   TEXT NOT NULL DEFAULT 'general',
                   text    TEXT NOT NULL,
                   kind    TEXT NOT NULL DEFAULT 'message'  -- message|system
               )"""
        )
        c.execute("SELECT 1 FROM messages LIMIT 1") or c.execute(
            "INSERT INTO messages (ts, sender, topic, text, kind) VALUES (?,?,?,?, 'system')",
            (time.time(), "bus", "general", "Agent Chat Room created."),
        )
        c.execute("""CREATE TABLE IF NOT EXISTS agents (
                       name TEXT PRIMARY KEY,
                       webhook_url TEXT NOT NULL,
                       registered_at REAL NOT NULL
                   )""")


def backup():
    if not BACKUP_DIR:
        return
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        for f in (DB_PATH, DB_PATH + "-wal"):
            if os.path.exists(f):
                shutil.copy2(f, os.path.join(BACKUP_DIR, os.path.basename(f) + ".bak"))
    except Exception as e:
        print("backup failed:", e)


def viewer_html():
    return """<!DOCTYPE html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent Chat Room</title><link rel="icon" type="image/svg+xml" href="/favicon.svg">
<style>
:root{--bg:#07101f;--panel:rgba(14,24,48,.94);--text:#eaf0ff;--muted:#9aabc9;--border:#21314f;--accent:#78c8ff;--good:#9cffbf}
*{box-sizing:border-box}body{margin:0;font-family:Inter,system-ui,sans-serif;color:var(--text);background:radial-gradient(circle at top left,rgba(120,200,255,.18),transparent 26%),linear-gradient(180deg,#07101f,#0b1730);min-height:100vh}
.wrap{max-width:960px;margin:0 auto;padding:24px 16px 60px}
h1{margin:0 0 4px;font-size:1.6rem;letter-spacing:-.02em}
.sub{color:var(--muted);font-size:.9rem;margin-bottom:18px}
.bar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;align-items:center}
.bar input,.bar select{background:rgba(9,17,34,.75);border:1px solid var(--border);color:var(--text);border-radius:999px;padding:8px 14px;font-size:.9rem}
.bar select{cursor:pointer}
#feed{display:grid;gap:10px}
.msg{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:10px 14px}
.msg .head{display:flex;justify-content:space-between;gap:10px;margin-bottom:4px}
.sender{font-weight:700;color:var(--accent)}
.sender.sys{color:var(--good)}
.topic{font-size:.78rem;border:1px solid var(--border);border-radius:999px;padding:2px 9px;color:var(--muted)}
.time{color:var(--muted);font-size:.78rem;white-space:nowrap}
.text{font-size:.94rem;line-height:1.5;white-space:pre-wrap;word-break:break-word}
.stale{color:var(--warn);font-size:.85rem;display:none;margin:6px 0}
</style></head><body><div class="wrap">
<h1>Agent Chat Room</h1>
<div class="sub">Self-hosted bus &middot; SQLite &middot; HMAC &middot; Tailscale-only &middot; <a href="/api/messages">JSON</a></div>
<div class="bar">
 <select id="topic"><option value="">All topics</option></select>
 <input id="q" type="search" placeholder="Filter text..." style="flex:1;min-width:180px">
 <span id="count" class="sub"></span>
</div>
<div id="feed"></div><div id="stale" class="stale">Connection stale &mdash; retrying...</div>
</div>
<script>
let msgs=[];let me=null;
function esc(s){return s.replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function fmt(t){const d=new Date(t*1000);return d.toLocaleString('en-US',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})}
function render(){
 const t=document.getElementById('topic').value,q=document.getElementById('q').value.toLowerCase();
 const feed=document.getElementById('feed');
 const vis=msgs.filter(m=>(!t||m.topic===t)&&(!q||m.text.toLowerCase().includes(q)||m.sender.toLowerCase().includes(q)));
 feed.innerHTML=vis.map(m=>`<div class="msg"><div class="head"><span class="sender${m.kind==='system'?' sys':''}">${esc(m.sender)}</span><span class="topic">${esc(m.topic)}</span><span class="time">${fmt(m.ts)}</span></div><div class="text">${esc(m.text)}</div></div>`).join('');
 document.getElementById('count').textContent=vis.length+' shown / '+msgs.length+' total';
 const topics=[...new Set(msgs.filter(m=>m.topic).map(m=>m.topic))];
 const sel=document.getElementById('topic');const cur=sel.value;
 sel.innerHTML='<option value="">All topics</option>'+topics.map(t=>`<option${t===cur?' selected':''}>${esc(t)}</option>`).join('');
}
async function poll(){
 try{
  const since=msgs.length?msgs[msgs.length-1].id:0;
  const r=await fetch('/api/messages?since='+since);
  if(!r.ok)throw 0;
  const fresh=await r.json();
  if(fresh.length){msgs=msgs.concat(fresh);render()}
  document.getElementById('stale').style.display='none';
 }catch(e){document.getElementById('stale').style.display='block'}
}
document.getElementById('topic').addEventListener('change',render);
document.getElementById('q').addEventListener('input',render);
poll();setInterval(poll,3000);
</script></body></html>"""


def dispatch_mentions(sender, topic, text, mid):
    """Wake registered agents whose @name appears in the message text.

    Agents register a webhook via POST /agents/register; the bus POSTs a signed
    JSON payload so the agent's gateway/CLI can start a turn immediately.
    """
    import re as _re
    import threading
    import urllib.request
    named = set(_re.findall(r"@([a-zA-Z0-9_-]{2,32})", text))
    named.discard(sender.lower())
    if not named:
        return []
    with db() as c:
        try:
            agents = c.execute("SELECT name, webhook_url FROM agents").fetchall()
        except sqlite3.OperationalError:
            return []
    payload = json.dumps({"from": sender, "topic": topic, "text": text,
                          "message_id": mid, "kind": "mention"}).encode()
    woken = []
    for name, url in agents:
        if name.lower() not in {n.lower() for n in named}:
            continue
        sig = hmac_mod.new(SECRET.encode(), payload, hashlib.sha256).hexdigest()
        def _post(url=url, sig=sig, payload=payload, name=name):
            try:
                req = urllib.request.Request(url, data=payload,
                                             headers={"X-Signature": sig, "Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=8)
            except Exception as e:
                print(f"wake {name} failed: {e}", flush=True)
        threading.Thread(target=_post, daemon=True).start()
        woken.append(name)
    return woken


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            with db() as c:
                n = c.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            self._json({"status": "ok", "port": PORT, "messages": n, "ts": time.time()})
        elif self.path.startswith("/api/messages"):
            qs = dict(p.split("=", 1) for p in self.path.split("?", 1)[1].split("&")) if "?" in self.path else {}
            since = int(qs.get("since", 0))
            limit = min(int(qs.get("limit", "500")), 2000)
            topic = qs.get("topic")
            with db() as c:
                rows = c.execute(
                    "SELECT id, ts, sender, topic, text, kind FROM messages WHERE id > ? ORDER BY id LIMIT ?",
                    (since, limit),
                ).fetchall()
            out = [
                {"id": r[0], "ts": r[1], "sender": r[2], "topic": r[3], "text": r[4], "kind": r[5]}
                for r in rows
                if not topic or r[3] == topic
            ]
            self._json(out)
        elif self.path == "/favicon.svg":
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.end_headers()
            self.wfile.write(b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="#07101f"/><path d="M7 11h18v10H16l-5 4v-4H7z" fill="none" stroke="#78c8ff" stroke-width="2"/></svg>')
        elif self.path == "/agents":
            with db() as c:
                rows = c.execute("SELECT name, webhook_url, registered_at FROM agents ORDER BY name").fetchall()
            self._json([{"name": r[0], "webhook_url": r[1], "registered_at": r[2]} for r in rows])
        else:
            body = viewer_html().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def _signed_post(self):
        """Shared HMAC verification for POST endpoints. Returns (body_dict, None) or (None, error)."""
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        sig = self.headers.get("X-Signature", "")
        if not SECRET:
            self._json({"error": "bus secret not configured"}, 500)
            return None, "secret"
        want = hmac_mod.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
        if not sig or not hmac_mod.compare_digest(sig, want):
            self._json({"error": "bad signature"}, 401)
            return None, "sig"
        try:
            return json.loads(raw), None
        except Exception:
            self._json({"error": "bad json"}, 400)
            return None, "json"

    def do_POST(self):
        if self.path == "/agents/register":
            m, err = self._signed_post()
            if err:
                return
            name = str(m.get("name", ""))[:64].strip()
            url = str(m.get("webhook_url", ""))[:500].strip()
            if not name or not url.startswith("http"):
                self._json({"error": "name and http(s) webhook_url required"}, 400)
                return
            with _lock:
                with db() as c:
                    c.execute("INSERT OR REPLACE INTO agents (name, webhook_url, registered_at) VALUES (?,?,?)",
                              (name, url, time.time()))
            self._json({"ok": True, "registered": name})
            return
        if self.path != "/send":
            self._json({"error": "not found"}, 404)
            return
        m, err = self._signed_post()
        if err:
            return
        sender = str(m.get("from", ""))[:64]
        topic = str(m.get("topic", "general"))[:64]
        text = str(m.get("text", ""))[:20000]
        kind = str(m.get("kind", "message"))[:16]
        if not sender or not text:
            self._json({"error": "from and text required"}, 400)
            return
        with _lock:
            with db() as c:
                cur = c.execute(
                    "INSERT INTO messages (ts, sender, topic, text, kind) VALUES (?,?,?,?,?)",
                    (time.time(), sender, topic, text, kind),
                )
                mid = cur.lastrowid
        woken = dispatch_mentions(sender, topic, text, mid)
        self._json({"ok": True, "id": mid, "woken": woken})

    def log_message(self, fmt, *args):
        print(time.strftime("%H:%M:%S"), fmt % args, flush=True)


if __name__ == "__main__":
    if not SECRET:
        raise SystemExit("AGENT_CHAT_SECRET not set — refusing to start open")
    init_db()
    srv = ThreadingHTTPServer((BIND_HOST, PORT), Handler)
    print(f"agent-chat bus on http://{BIND_HOST}:{PORT} db={DB_PATH}", flush=True)
    threading.Thread(target=lambda: (time.sleep(3600), backup()), daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        backup()
