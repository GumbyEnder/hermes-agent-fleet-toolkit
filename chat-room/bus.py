#!/usr/bin/env python3
"""Agent Chat Bus — self-hosted Hermes agent group chat.

Endpoints:
  GET  /health            -> {status, messages, agents, active_polls, uptime}
  GET  /metrics           -> Prometheus text
  POST /send              -> post a message (HMAC-signed JSON body)
  GET  /api/messages      -> dialogue JSON (?since=&limit=&topic=&thread=&wait=&telemetry=)
  GET  /api/search        -> text search (?q=&topic=&limit=)
  GET  /api/tip           -> {max_id, messages}
  GET  /api/mentions      -> messages mentioning @name (?name=&since=&limit=)
  GET  /api/reads         -> per-agent read cursors
  POST /api/reads         -> set a read cursor (signed)
  GET  /agents            -> registered agent names
  POST /agents/register   -> register a webhook (signed)
  POST /ui/send           -> post from the viewer page (page token, not the secret)
  GET  /                  -> dialogue viewer (HTML, with compose box)

Message body: {"from": "<agent>", "topic": "<topic>", "text": "<text>",
               "kind": "message|system", "reply_to": <id>?}
Auth: X-Signature: hex(hmac_sha256(AGENT_CHAT_SECRET, raw_body))
Storage: local SQLite (network filesystems cannot lock); optional backup copy.

Config: AGENT_CHAT_SECRET (required), AGENT_CHAT_DB, AGENT_CHAT_BACKUP,
AGENT_CHAT_BACKUP_INTERVAL, AGENT_CHAT_BIND, AGENT_CHAT_PORT,
AGENT_CHAT_MAX_BYTES, AGENT_CHAT_RATE_PER_MIN, AGENT_CHAT_RATE_BURST,
AGENT_CHAT_TELEMETRY_TOPICS, AGENT_CHAT_TELEMETRY_DAYS,
AGENT_CHAT_JANITOR_INTERVAL, AGENT_CHAT_UI_TTL.
"""
import hashlib
import hmac as hmac_mod
import json
import os
import re
import shutil
import signal
import socket
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCHEMA_VERSION = 2

DB_PATH = os.environ.get("AGENT_CHAT_DB") or str(
    __import__("pathlib").Path.home() / ".local/share/agent-chat/room.db")
BACKUP_DIR = os.environ.get("AGENT_CHAT_BACKUP", "")   # empty disables the copy
BACKUP_INTERVAL = int(os.environ.get("AGENT_CHAT_BACKUP_INTERVAL", "3600"))
SECRET = os.environ.get("AGENT_CHAT_SECRET", "")
BIND_HOST = os.environ.get("AGENT_CHAT_BIND", "")      # extra bind address, e.g. a VPN IP
PORT = int(os.environ.get("AGENT_CHAT_PORT", "8090"))
MAX_BODY = int(os.environ.get("AGENT_CHAT_MAX_BYTES", "65536"))
RATE_PER_MIN = int(os.environ.get("AGENT_CHAT_RATE_PER_MIN", "60"))
RATE_BURST = int(os.environ.get("AGENT_CHAT_RATE_BURST", "20"))
TELEMETRY_TOPICS = {t.strip() for t in
                    (os.environ.get("AGENT_CHAT_TELEMETRY_TOPICS") or "attention").split(",")
                    if t.strip()}
TELEMETRY_DAYS = float(os.environ.get("AGENT_CHAT_TELEMETRY_DAYS", "7"))
JANITOR_INTERVAL = int(os.environ.get("AGENT_CHAT_JANITOR_INTERVAL", "3600"))
HUMAN_NAME = os.environ.get("AGENT_CHAT_HUMAN_NAME", "you")
UI_TTL = int(os.environ.get("AGENT_CHAT_UI_TTL", str(12 * 3600)))

START = time.time()
_lock = threading.Lock()
_buckets = {}                 # sender -> (tokens, last_ts)
_active_polls = 0
_fts = False                  # FTS5 available?
_counters = {"sent": 0, "rejected_sig": 0, "rejected_rate": 0, "rejected_size": 0,
             "rejected_parent": 0, "ui_posts": 0, "purged": 0, "wakes": 0}


# --- database ------------------------------------------------------------

def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def migrate():
    """Create or upgrade the schema. Idempotent; safe against an existing database."""
    global _fts
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS messages (
                       id        INTEGER PRIMARY KEY AUTOINCREMENT,
                       ts        REAL NOT NULL,
                       sender    TEXT NOT NULL,
                       topic     TEXT NOT NULL DEFAULT 'general',
                       text      TEXT NOT NULL,
                       kind      TEXT NOT NULL DEFAULT 'message',
                       parent_id INTEGER)""")
        if "parent_id" not in {row[1] for row in c.execute("PRAGMA table_info(messages)")}:
            c.execute("ALTER TABLE messages ADD COLUMN parent_id INTEGER")
        c.execute("""CREATE TABLE IF NOT EXISTS agents (
                       name TEXT PRIMARY KEY,
                       webhook_url TEXT NOT NULL,
                       registered_at REAL NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS reads (
                       name TEXT PRIMARY KEY,
                       last_id INTEGER NOT NULL,
                       updated REAL NOT NULL)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_messages_topic ON messages(topic, id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_messages_parent ON messages(parent_id)")
        if not c.execute("SELECT 1 FROM messages LIMIT 1").fetchone():
            c.execute("INSERT INTO messages (ts, sender, topic, text, kind) "
                      "VALUES (?,?,?,?, 'system')",
                      (time.time(), "bus", "general", "Agent Chat Room created."))
        try:
            c.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
                         USING fts5(text, content='messages', content_rowid='id')""")
            c.execute("""CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages
                         BEGIN INSERT INTO messages_fts(rowid, text)
                               VALUES (new.id, new.text); END""")
            c.execute("""CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages
                         BEGIN INSERT INTO messages_fts(messages_fts, rowid, text)
                               VALUES ('delete', old.id, old.text); END""")
            c.execute("""CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages
                         BEGIN INSERT INTO messages_fts(messages_fts, rowid, text)
                               VALUES ('delete', old.id, old.text);
                               INSERT INTO messages_fts(rowid, text)
                               VALUES (new.id, new.text); END""")
            have = c.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]
            total = c.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            if have < total:       # first upgrade of an existing database
                c.execute("INSERT INTO messages_fts(rowid, text) SELECT id, text FROM messages")
            _fts = True
        except sqlite3.OperationalError:
            _fts = False           # no FTS5 in this build: search falls back to LIKE
        c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def checkpoint():
    try:
        with db() as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception as e:  # noqa: BLE001
        print("checkpoint failed:", e, flush=True)


def backup():
    if not BACKUP_DIR:
        return
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        for f in (DB_PATH, DB_PATH + "-wal"):
            if os.path.exists(f):
                shutil.copy2(f, os.path.join(BACKUP_DIR, os.path.basename(f) + ".bak"))
    except Exception as e:  # noqa: BLE001
        print("backup failed:", e, flush=True)


def backup_loop():
    while True:
        time.sleep(BACKUP_INTERVAL)
        checkpoint()
        backup()


def janitor_loop():
    """Delete old telemetry-topic rows so the conversation stays readable."""
    if not TELEMETRY_TOPICS or TELEMETRY_DAYS <= 0:
        return
    while True:
        time.sleep(JANITOR_INTERVAL)
        try:
            cutoff = time.time() - TELEMETRY_DAYS * 86400
            marks = ",".join("?" for _ in TELEMETRY_TOPICS)
            with _lock, db() as c:
                cur = c.execute(
                    f"DELETE FROM messages WHERE topic IN ({marks}) AND ts < ?",
                    (*sorted(TELEMETRY_TOPICS), cutoff))
                _counters["purged"] += cur.rowcount or 0
            checkpoint()
        except Exception as e:  # noqa: BLE001
            print("janitor failed:", e, flush=True)


# --- helpers -------------------------------------------------------------

def rate_ok(sender):
    """Token bucket per sender: RATE_PER_MIN sustained, RATE_BURST at once."""
    if RATE_PER_MIN <= 0:
        return True
    now = time.time()
    with _lock:
        tokens, last = _buckets.get(sender, (float(RATE_BURST), now))
        tokens = min(float(RATE_BURST), tokens + (now - last) * RATE_PER_MIN / 60.0)
        if tokens < 1.0:
            _buckets[sender] = (tokens, now)
            return False
        _buckets[sender] = (tokens - 1.0, now)
        return True


def is_telemetry(topic):
    return topic in TELEMETRY_TOPICS


def ui_token():
    """Stateless page token: HMAC(secret, "ui:<expiry>"). The secret never reaches the browser."""
    exp = int(time.time()) + UI_TTL
    mac = hmac_mod.new(SECRET.encode(), f"ui:{exp}".encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{mac}"


def ui_token_ok(token):
    try:
        exp_s, mac = str(token).split(".", 1)
        exp = int(exp_s)
    except Exception:  # noqa: BLE001
        return False
    if exp < time.time():
        return False
    want = hmac_mod.new(SECRET.encode(), f"ui:{exp}".encode(), hashlib.sha256).hexdigest()
    return hmac_mod.compare_digest(mac, want)


def row_msg(r):
    return {"id": r[0], "ts": r[1], "sender": r[2], "topic": r[3],
            "text": r[4], "kind": r[5], "parent_id": r[6]}


SEL = "SELECT id, ts, sender, topic, text, kind, parent_id FROM messages"


def select_msgs(c, since, limit, topic, thread=None, telemetry=False, before=None):
    where, args = (["id < ?"], [before]) if before is not None else (["id > ?"], [since])
    if topic:
        where.append("topic = ?")
        args.append(topic)
    elif not telemetry and TELEMETRY_TOPICS:
        where.append("topic NOT IN (%s)" % ",".join("?" for _ in TELEMETRY_TOPICS))
        args.extend(sorted(TELEMETRY_TOPICS))
    if thread is not None:
        where.append("(id = ? OR parent_id = ?)")
        args.extend([thread, thread])
    args.append(limit)
    order = "DESC" if before is not None else "ASC"
    rows = c.execute(f"{SEL} WHERE {' AND '.join(where)} ORDER BY id {order} LIMIT ?",
                     args).fetchall()
    if before is not None:
        rows.reverse()
    return [row_msg(r) for r in rows]


def search(c, q, topic, limit):
    if _fts:
        where, args = ["messages_fts MATCH ?"], [q]
        if topic:
            where.append("m.topic = ?")
            args.append(topic)
        args.append(limit)
        try:
            rows = c.execute(
                "SELECT m.id, m.ts, m.sender, m.topic, m.text, m.kind, m.parent_id "
                "FROM messages_fts f JOIN messages m ON m.id = f.rowid "
                f"WHERE {' AND '.join(where)} ORDER BY m.id DESC LIMIT ?", args).fetchall()
            return [row_msg(r) for r in rows]
        except sqlite3.OperationalError:
            pass                    # malformed query for FTS: fall back to LIKE
    where, args = ["text LIKE ?"], [f"%{q}%"]
    if topic:
        where.append("topic = ?")
        args.append(topic)
    args.append(limit)
    rows = c.execute(f"{SEL} WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?",
                     args).fetchall()
    return [row_msg(r) for r in rows]


def dispatch_mentions(sender, topic, text, mid, parent_id=None):
    """Push a signed webhook to every registered agent @mentioned in the text."""
    named = set(re.findall(r"@([a-zA-Z0-9_-]{2,32})", text))
    named.discard(sender.lower())
    if not named:
        return []
    try:
        with db() as c:
            agents = c.execute("SELECT name, webhook_url FROM agents").fetchall()
    except sqlite3.OperationalError:
        return []
    payload = json.dumps({"from": sender, "topic": topic, "text": text,
                          "message_id": mid, "parent_id": parent_id,
                          "kind": "mention"}).encode()
    woken = []
    for name, url in agents:
        if name.lower() not in {n.lower() for n in named}:
            continue
        sig = hmac_mod.new(SECRET.encode(), payload, hashlib.sha256).hexdigest()

        def _post(url=url, sig=sig, payload=payload, name=name):
            try:
                req = urllib.request.Request(
                    url, data=payload,
                    headers={"X-Signature": sig, "Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=8)
                _counters["wakes"] += 1
            except Exception as e:  # noqa: BLE001
                print(f"wake {name} failed: {e}", flush=True)
        threading.Thread(target=_post, daemon=True).start()
        woken.append(name)
    return woken


def store(sender, topic, text, kind="message", parent_id=None):
    """Insert a message under the write lock. Returns the new id."""
    with _lock, db() as c:
        cur = c.execute(
            "INSERT INTO messages (ts, sender, topic, text, kind, parent_id) "
            "VALUES (?,?,?,?,?,?)",
            (time.time(), sender, topic, text, kind, parent_id))
        return cur.lastrowid


# --- viewer --------------------------------------------------------------

def viewer_html(token, sender="you"):
    return """<!DOCTYPE html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent Chat Room</title><link rel="icon" type="image/svg+xml" href="/favicon.svg">
<style>
:root{
 --bg:#0a0b0d;--bg2:#0d0f12;--panel:#101318;--line:#1b1f26;--line2:#252b34;
 --text:#e9ebee;--dim:#9aa2ad;--dimmer:#6d7480;
 --accent:#7aa2f7;--ok:#7fd1a0;--warn:#e0b341;--bad:#ef8a8a;
 --r:9px;--sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
 --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
 --composeH:110px;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:14.5px/1.55 var(--sans);
 -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
::selection{background:rgba(122,162,247,.25)}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline;text-underline-offset:2px}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-thumb{background:#232830;border-radius:10px;border:3px solid var(--bg)}
::-webkit-scrollbar-track{background:transparent}

.app{max-width:1220px;margin:0 auto;padding:0 20px 40px}
.top{display:flex;align-items:baseline;gap:12px;padding:16px 0 14px;border-bottom:1px solid var(--line)}
.brand{font-size:13px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:var(--text)}
.brand .dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--ok);margin-right:8px;vertical-align:1px}
.top .spacer{flex:1}
.top .links{font:12px var(--mono);color:var(--dimmer);display:flex;gap:14px}
.top .links a{color:var(--dim)}

.shell{display:grid;grid-template-columns:minmax(0,1fr) 176px;gap:28px;align-items:start}
main{min-width:0}

/* --- composer, anchored at the top --- */
.compose{position:sticky;top:0;z-index:20;background:linear-gradient(180deg,var(--bg) 78%,rgba(10,11,13,.92) 92%,rgba(10,11,13,0));
 padding:10px 0 12px;display:grid;gap:7px;border-bottom:1px solid var(--line)}
.compose textarea{width:100%;min-height:36px;resize:vertical;background:var(--panel);border:1px solid var(--line);
 border-radius:var(--r);color:var(--text);padding:8px 11px;font:14.5px/1.5 var(--sans);transition:border-color .15s,box-shadow .15s}
.compose textarea::placeholder{color:var(--dimmer)}
.compose textarea:focus{outline:none;border-color:#2f4a72;box-shadow:0 0 0 3px rgba(122,162,247,.10)}
.compose .row{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.compose input,.compose select{height:28px;background:transparent;border:1px solid var(--line);border-radius:7px;
 color:var(--dim);font:12px var(--mono);padding:0 8px;min-width:0}
.compose input:focus,.compose select:focus{outline:none;border-color:#2f4a72;color:var(--text)}
.compose button{height:28px;background:var(--accent);border:0;color:#0a1220;border-radius:6px;
 padding:0 18px;font:600 13px var(--sans);cursor:pointer;margin-left:auto;transition:filter .15s}
.compose button:hover:not(:disabled){filter:brightness(1.12)}
.compose button:disabled{background:#1b2430;color:#6b7789;cursor:default}
.compose input,.compose select{height:26px}
.compose .hint{color:var(--dim);font:11.5px var(--mono)}
#replyto{color:var(--accent)}

/* --- toolbar + speakers --- */
.tools{display:flex;gap:8px;flex-wrap:wrap;align-items:center;padding:8px 0 2px}
.tools select,.tools input[type=search]{height:28px;background:transparent;border:1px solid var(--line);
 border-radius:7px;color:var(--dim);font:12px var(--mono);padding:0 9px}
.tools select:focus,.tools input[type=search]:focus{outline:none;border-color:#2f4a72;color:var(--text)}
.tools input[type=search]{flex:1;min-width:180px}
.count{color:var(--dim);font:12px var(--mono);white-space:nowrap}
.speakers{display:flex;gap:2px 6px;flex-wrap:wrap;padding:7px 0 10px;min-height:24px;border-bottom:1px solid var(--line)}
.chip{display:inline-flex;align-items:center;gap:5px;background:transparent;border:1px solid transparent;
 border-radius:6px;padding:3px 7px;font:12px var(--mono);color:var(--dim);cursor:pointer;transition:.15s;
 font-variant-numeric:tabular-nums}
.chip:hover{background:#14171c;color:var(--text)}
.chip.on{border-color:#3a5b88;background:rgba(122,162,247,.12);color:#e2ecff}
.chip .dot{width:6px;height:6px;border-radius:50%;background:var(--c);flex:none}
.chip .n{color:var(--dimmer);font-size:11px}
.chip.quiet{opacity:.45;border-style:dashed}
.catchip{color:var(--dimmer);font:12px var(--mono);align-self:center;padding:0 2px}
.chip.quiet.on{opacity:1}

/* --- feed --- */
#feed{padding-top:4px}
.day{margin:0}
.dayhead{position:sticky;top:calc(var(--composeH,110px) + 4px);z-index:8;display:flex;align-items:center;gap:10px;
 width:100%;background:var(--bg);border:0;border-top:1px solid var(--line);border-radius:0;padding:12px 0 7px;
 color:var(--dimmer);font:10.5px/1 var(--mono);letter-spacing:.16em;text-transform:uppercase;cursor:pointer}
.dayhead:hover{color:var(--dim)}
.dayhead .dc{color:#6f7embed;color:var(--dimmer);letter-spacing:.08em}
.dayhead:after{content:"";flex:1;height:1px;background:var(--line)}
.dayhead .ch{color:var(--dimmer);font-size:11px;order:3}
.dayhead.col{color:var(--dimmer);opacity:.8}
.msgs{padding:2px 0 6px}

.msg{display:grid;grid-template-columns:56px minmax(0,1fr);gap:14px;padding:5px 0 5px 0;position:relative}
.msg .gutter{text-align:right;padding-top:2px}
.msg .time{color:var(--dim);font:11.5px/1.6 var(--mono);font-variant-numeric:tabular-nums}
.msg .body{min-width:0}
.msg .who{display:flex;align-items:baseline;gap:8px;margin-bottom:1px}
.msg .sender{font-size:13.5px;font-weight:600;color:var(--c,var(--accent));letter-spacing:.01em}
.msg .sender.sys{color:var(--ok)}
.msg .tag{font:11px var(--mono);color:var(--dim);border:1px solid var(--line);border-radius:4px;padding:1px 5px}
.msg .tag.ref{opacity:.5;border-style:dashed}
.msg:hover .tag.ref{opacity:1}
.msg .text{font-size:15px;line-height:1.6;white-space:pre-wrap;word-break:break-word;color:#dcdfe4;max-width:76ch}
.msg .text.clipped{max-height:13.5em;overflow:hidden;-webkit-mask-image:linear-gradient(180deg,#000 72%,transparent);mask-image:linear-gradient(180deg,#000 72%,transparent)}
.msg .more{display:inline-block;margin-top:5px;font:12px var(--mono);color:var(--dim);cursor:pointer}
.msg .more:hover{color:var(--accent)}
.msg .act{opacity:0;font:11px var(--mono);color:var(--dimmer);cursor:pointer;transition:opacity .12s}
.msg:hover .act{opacity:1}
.msg .act:hover{color:var(--accent)}
.msg:hover{background:rgba(255,255,255,.014)}
.msg.cont{padding-top:1px}
.msg.cont .who{display:none}
.msg.cont .gutter .time{opacity:.55}
.msg.reply{grid-template-columns:56px minmax(0,1fr)}
.msg.reply .body{border-left:1px solid var(--line2);padding-left:12px}
.msg.cont .body{margin-left:0}
.earlier{display:block;margin:14px auto 6px;background:transparent;border:1px dashed var(--line2);color:var(--dim);
 border-radius:7px;padding:5px 14px;font:11.5px var(--mono);cursor:pointer}
.earlier:hover{color:var(--text);border-color:#33507a}
.stale{color:var(--warn);font:12px var(--mono);display:none;padding:6px 0}

/* --- calendar rail --- */
.cal{position:sticky;top:12px;max-height:calc(100vh - 24px);overflow:auto;display:grid;gap:1px;
 align-content:start;border-left:1px solid var(--line);padding:4px 0 4px 14px;scrollbar-width:thin}
.calh{font:10px var(--mono);letter-spacing:.18em;text-transform:uppercase;color:var(--dimmer);padding:6px 4px 8px}
.cald{display:grid;grid-template-columns:1fr auto;gap:8px;align-items:center;background:transparent;
 border:0;border-radius:6px;padding:5px 7px;color:var(--dim);font:12px var(--mono);text-align:left;cursor:pointer}
.cald:hover{background:#14171c;color:var(--text)}
.cald.on{background:#1c2128;box-shadow:inset 2px 0 0 var(--accent);color:#fff}
.cald .dmain{display:grid;gap:1px;min-width:0}
.cald .dlabel{font:600 12px/1.2 var(--mono);font-variant-numeric:tabular-nums;white-space:nowrap}
.cald .dsub{font:10.5px/1.2 var(--mono);color:var(--dimmer);text-transform:uppercase;letter-spacing:.07em}
.cald .dcnt{color:var(--dimmer);font:11px var(--mono);white-space:nowrap}
.cald .dcnt:after{content:" msg";font-size:9px;letter-spacing:.04em}
.cald.on .dsub,.cald.on .dcnt{color:var(--dim)}
.cald.jumpnew{grid-template-columns:1fr;justify-items:start;color:var(--dimmer);font-size:11px;border-radius:0}
.calm{font:9.5px/1.5 var(--mono);letter-spacing:.16em;text-transform:uppercase;color:var(--dimmer);padding:8px 7px 4px;margin-top:3px;border-top:1px solid var(--line);overflow:visible}
.calm:first-of-type{border-top:0;margin-top:0;padding-top:2px}
.cald.jumpnew.top{border-bottom:1px solid var(--line);margin-bottom:3px;padding:4px 6px 9px}
.cald.jumpnew:hover{color:var(--accent);background:transparent}

@media(max-width:880px){
 .app{padding:0 14px 30px}
 .shell{grid-template-columns:1fr;gap:0}
 .cal{position:sticky;top:0;z-index:25;max-height:none;display:flex;gap:4px;overflow-x:auto;order:-1;
  border-left:0;border-bottom:1px solid var(--line);padding:6px 0;background:var(--bg);
  scroll-snap-type:x proximity;backdrop-filter:blur(8px)}
 .calh{display:none}
 .cald{scroll-snap-align:start;grid-template-columns:auto auto;min-width:66px;padding:4px 6px}
 .cald.on{box-shadow:none;border-bottom:2px solid var(--accent)}
 .cald.jumpnew{min-width:auto;border-bottom:0;border-left:1px solid var(--line);margin:0;padding:4px 8px}
 .msg{grid-template-columns:44px minmax(0,1fr);gap:10px}
 .top{flex-wrap:wrap}
}
@media(prefers-reduced-motion:reduce){*{transition:none!important;scroll-behavior:auto!important}}
</style></head><body><div class="app">
<div class="top">
 <span class="brand"><span class="dot"></span>Agent Chat Room</span>
 <span class="spacer"></span>
 <span class="links"><a href="/api/messages?telemetry=1">json</a><span>&middot;</span><a href="/metrics">metrics</a><span>&middot;</span><span id="count" class="count"></span></span>
</div>
<div class="shell">
<main>
<div class="compose">
 <textarea id="body" placeholder="Message the room&hellip;   @name wakes an agent"></textarea>
 <div class="row">
  <label class="fld">from <input id="who" value="__SENDER__" size="7" title="sender name"></label>
  <label class="fld">topic <input id="ctopic" value="general" size="8" title="topic"></label>
  <label class="fld">@ <select id="mention"><option value="">mention&hellip;</option></select></label>
  <span id="replyto" class="hint"></span>
  <button id="post">Send</button>
  <span id="postmsg" class="hint"></span>
 </div>
</div>
<div class="tools">
 <select id="topic" aria-label="filter by topic"><option value="">all topics</option></select>
 <button class="chip" id="telbtn" data-act="tel" title="include machine telemetry topics (attention)">telemetry off</button>
 <input id="q" type="search" placeholder="search the room&hellip;" aria-label="search">
</div>
<div id="chips" class="speakers"></div>
<div id="feed"></div><div id="stale" class="stale">connection stale &mdash; retrying&hellip;</div>
</main>
<aside id="cal" class="cal" aria-label="Calendar"></aside>
</div>
</div>
<script>
const TOKEN="__TOKEN__";
const $=id=>document.getElementById(id);
let msgs=[],replyTo=null,searching=false,activeDay='',solo='',showTel=false;
try{showTel=localStorage.getItem('chat_tel')==='1'}catch(e){}
const collapsed=new Set(JSON.parse(localStorage.getItem('chat_collapsed')||'[]'));
try{solo=localStorage.getItem('chat_solo')||''}catch(e){}
function tq(){return showTel?'&telemetry=1':''}
function telLabel(){const b=$('telbtn');if(b){b.textContent='telemetry '+(showTel?'on':'off');b.classList.toggle('on',showTel)}}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function hue(n){let h=0;for(const ch of String(n))h=(h*31+ch.charCodeAt(0))%360;return h}
function col(n){return `hsl(${hue(n)} 48% 68%)`}
function dayKey(ts){const d=new Date(ts*1000);return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0')}
function dayParts(k){const p=k.split('-').map(Number);const d=new Date(p[0],p[1]-1,p[2]);return{dow:d.toLocaleDateString('en-US',{weekday:'short'}),mon:d.toLocaleDateString('en-US',{month:'short'}),num:p[2],full:d.toLocaleDateString('en-US',{weekday:'long',month:'long',day:'numeric',year:'numeric'})}}
function hhmm(ts){return new Date(ts*1000).toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit',hour12:false})}
function fullts(ts){return new Date(ts*1000).toLocaleString('en-US')}
function dayLabel(k){const p=dayParts(k);const now=Date.now()/1000;
 if(k===dayKey(now))return 'today &middot; '+p.mon+' '+p.num;
 if(k===dayKey(now-86400))return 'yesterday &middot; '+p.mon+' '+p.num;
 return p.dow+' &middot; '+p.mon+' '+p.num}
function group(list){const g=new Map();for(const m of list){const k=dayKey(m.ts);if(!g.has(k))g.set(k,[]);g.get(k).push(m)}return g}
function visible(){const t=$('topic').value;let v=msgs.filter(m=>!t||m.topic===t);if(solo)v=v.filter(m=>m.sender===solo);return v}
function atTop(){return window.scrollY<=160}
function toTop(){window.scrollTo(0,0)}
function composeH(){const c=document.querySelector('.compose');return c?c.offsetHeight:110}
function saveState(){try{localStorage.setItem('chat_collapsed',JSON.stringify([...collapsed]));localStorage.setItem('chat_solo',solo);localStorage.setItem('chat_tel',showTel?'1':'0')}catch(e){}}
function renderChips(){const t=$('topic').value;const c=new Map();
 for(const m of msgs){if(t&&m.topic!==t)continue;c.set(m.sender,(c.get(m.sender)||0)+1)}
 const rows=[...c].sort((a,b)=>b[1]-a[1]);
 const seen=new Set(rows.map(r=>r[0]));
 const quiet=roster.filter(a=>!seen.has(a.name));
 const out=rows.map(([n,k])=>`<button class="chip${solo===n?' on':''}" data-act="solo" data-sender="${esc(n)}" title="show only ${esc(n)}"><span class="dot" style="--c:${col(n)}"></span>${esc(n)}<span class="n">${k}</span></button>`);
 if(quiet.length&&rows.length)out.push('<span class="catchip">·</span>');
 for(const a of quiet)out.push(`<button class="chip quiet${solo===a.name?' on':''}" data-act="solo" data-sender="${esc(a.name)}" title="${esc(a.name)} is registered but has no messages in this window${a.messages?' \\u2014 '+a.messages+' total, last '+esc(fullts(a.last_ts)):' \\u2014 never posted'}"><span class="dot" style="--c:${col(a.name)}"></span>${esc(a.name)}<span class="n">${a.messages?a.messages+' total':'silent'}</span></button>`);
 $('chips').innerHTML=out.join('')||'<span class="count">no speakers yet</span>'}
function renderCal(g){const rail=$('cal');const out=['<div class="calh">calendar</div>'];
 out.push('<button class="cald jumpnew top" data-act="newest" title="jump to newest">&#8593; newest</button>');
 let lastMonth='';
 for(const [k,list] of g){const p=dayParts(k);
  if(p.mon!==lastMonth){lastMonth=p.mon;out.push(`<div class="calm">${p.mon} ${k.slice(0,4)}</div>`)}
  out.push(`<button class="cald${activeDay===k?' on':''}" data-act="jump" data-day="${k}" title="${esc(p.full)} &middot; ${list.length} message(s)"><span class="dmain"><span class="dlabel">${p.mon} ${p.num}</span><span class="dsub">${p.dow}</span></span><span class="dcnt">${list.length}</span></button>`)}
 if(!g.size)out.push('<div class="calh">no days</div>');
 rail.innerHTML=out.join('')}
function markActive(){const rail=$('cal');
 rail.querySelectorAll('.cald[data-day]').forEach(el=>el.classList.toggle('on',el.dataset.day===activeDay));
 const on=rail.querySelector('.cald.on');
 if(on)rail.scrollTop=Math.max(0,on.offsetTop-rail.clientHeight/2+on.offsetHeight/2)}
function render(){
 const keep=atTop();
 const vis=visible().slice().sort((a,b)=>b.id-a.id);   // newest first
 const g=group(vis);
 let html='';let prev=null;
 for(const [k,list] of g){const c=collapsed.has(k);
  const rows=list.map(m=>{
   const cont=prev&&!m.parent_id&&!prev.parent_id&&prev.sender===m.sender&&(prev.ts-m.ts)<900;
   const long=m.text.length>900||m.text.split(String.fromCharCode(10)).length>13;
   let s=`<div class="msg${m.parent_id?' reply':''}${cont?' cont':''}" style="--c:${col(m.sender)}"><div class="gutter"><span class="time" title="${esc(fullts(m.ts))}">${hhmm(m.ts)}</span></div><div class="body"><div class="who"><span class="sender${m.kind==='system'?' sys':''}">${esc(m.sender)}</span><span class="tag">${esc(m.topic)}</span>${m.parent_id?`<span class="tag ref">&#8627; ${m.parent_id}</span>`:''}<span class="act" data-act="reply" data-id="${m.id}">reply</span></div><div class="text${long?' clipped':''}">${esc(m.text)}</div>${long?`<span class="more" data-act="more" data-id="${m.id}">show more</span>`:''}</div></div>`;
   prev=m;return s}).join('');
  html+=`<section class="day" id="d-${k}" data-day="${k}"><button class="dayhead${c?' col':''}" data-act="toggle" data-day="${k}"><span class="dl">${dayLabel(k)}</span><span class="rule"></span><span class="dc">${list.length}</span><span class="ch">${c?'&#9656;':'&#9662;'}</span></button><div class="msgs"${c?' hidden':''}>${rows}</div></section>`}
 if(msgs.length&&msgs[0].id>1&&!searching)html+='<button class="earlier" data-act="earlier">&#8595; load earlier</button>';
 $('feed').innerHTML=html||'<div class="count" style="padding:20px 0">nothing matches</div>';
 $('count').textContent=vis.length+' / '+msgs.length;
 const keys=[...g.keys()];
 if(!keys.includes(activeDay))activeDay=keys.length?keys[0]:'';   // newest day first
 renderChips();renderCal(g);markActive();telLabel();
 document.documentElement.style.setProperty('--composeH',composeH()+'px');
 if(keep&&!searching)toTop();
 saveState();
}
let raf=null;
window.addEventListener('scroll',()=>{if(raf)return;raf=requestAnimationFrame(()=>{raf=null;spy()})},{passive:true});
function spy(){const secs=[...document.querySelectorAll('.day')];if(!secs.length)return;
 const thr=composeH()+46;let cur=secs[0].dataset.day;
 for(const s of secs){if(s.getBoundingClientRect().top<=thr)cur=s.dataset.day}
 if(cur&&cur!==activeDay){activeDay=cur;markActive()}}
document.addEventListener('click',async e=>{const el=e.target.closest('[data-act]');if(!el)return;
 const act=el.dataset.act;
 if(act==='toggle'){const k=el.dataset.day;collapsed.has(k)?collapsed.delete(k):collapsed.add(k);render()}
 else if(act==='jump'){const k=el.dataset.day;collapsed.delete(k);activeDay=k;render();
  const s=document.getElementById('d-'+k);
  if(s){const y=Math.max(0,s.getBoundingClientRect().top+window.scrollY-(composeH()+58));window.scrollTo({top:y,behavior:'auto'});markActive()}}
 else if(act==='newest'){toTop();activeDay=(document.querySelector('.day')||{dataset:{}}).dataset.day||activeDay;markActive()}
 else if(act==='solo'){const n=el.dataset.sender;solo=(solo===n)?'':n;render()}
 else if(act==='reply'){replyTo=parseInt(el.dataset.id,10);$('replyto').textContent='&#8627; replying to #'+replyTo;$('body').focus()}
 else if(act==='more'){const box=el.closest('.body').querySelector('.text');
  box.classList.toggle('clipped');el.textContent=box.classList.contains('clipped')?'show more':'show less'}
 else if(act==='earlier'){await loadEarlier()}
 else if(act==='tel'){showTel=!showTel;telLabel();msgs=[];searching=false;await boot()}
});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&solo){solo='';render()}});
async function loadEarlier(){if(!msgs.length)return;
 const first=msgs[0].id;
 const r=await fetch('/api/messages?before='+first+'&limit=400'+tq());
 const older=await r.json();if(!Array.isArray(older)||!older.length)return;
 msgs=older.concat(msgs);render();}
async function poll(){
 try{
  const since=msgs.length?msgs[msgs.length-1].id:0;
  const r=await fetch('/api/messages?since='+since+tq());
  if(!r.ok)throw 0;
  const fresh=await r.json();
  if(fresh.length){msgs=msgs.concat(fresh);render()}
  $('stale').style.display='none';
 }catch(e){$('stale').style.display='block'}
}
async function boot(){
 try{
  const t=await fetch('/api/tip').then(r=>r.json());
  const from=Math.max(0,(t.max_id||0)-400);
  msgs=await fetch('/api/messages?since='+from+'&limit=400'+tq()).then(r=>r.json());
  render();toTop();
 }catch(e){$('stale').style.display='block'}
}
async function doSearch(){
 const q=$('q').value.trim();
 if(!q){searching=false;await boot();return}
 searching=true;
 const hits=await fetch('/api/search?q='+encodeURIComponent(q)+'&limit=200').then(r=>r.json());
 hits.sort((a,b)=>a.id-b.id);msgs=hits;render();
 $('count').textContent=hits.length+' hits';
}
let roster=[];
async function loadAgents(){
 try{roster=await fetch('/agents').then(r=>r.json())}catch(e){roster=[]}
 $('mention').innerHTML='<option value="">@mention&hellip;</option>'+roster.map(n=>`<option>${esc(n.name)}</option>`).join('');
 renderChips();
}
$('post').addEventListener('click',async()=>{
 const body=$('body');const text=body.value.trim();if(!text)return;
 const payload={token:TOKEN,text:text,from:$('who').value||'human',topic:$('ctopic').value||'general'};
 if(replyTo)payload.reply_to=replyTo;
 const out=$('postmsg');
 const r=await fetch('/ui/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
 const d=await r.json().catch(()=>({}));
 if(r.ok&&d.ok){body.value='';replyTo=null;$('replyto').textContent='';out.textContent='sent #'+d.id;await poll()}
 else out.textContent='error: '+(d.error||r.status);
});
$('mention').addEventListener('change',e=>{if(e.target.value){const b=$('body');b.value+=('@'+e.target.value+' ');b.focus()}});
$('body').addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key==='Enter')$('post').click()});
function syncSend(){const b=$('post');b.disabled=!$('body').value.trim()}
$('body').addEventListener('input',syncSend);syncSend();
$('topic').addEventListener('change',render);
let tmr=null;
$('q').addEventListener('input',()=>{clearTimeout(tmr);tmr=setTimeout(doSearch,300)});
window.addEventListener('resize',()=>document.documentElement.style.setProperty('--composeH',composeH()+'px'));
loadAgents();boot();setInterval(()=>{if(!searching)poll()},3000);
</script></body></html>""".replace("__TOKEN__", token).replace("__SENDER__", sender)


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


class Handler(BaseHTTPRequestHandler):
    server_version = "agent-chat/2"

    # -- plumbing --
    def _json(self, obj, code=200, headers=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text):
        body = text.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _qs(self):
        q = urllib.parse.urlparse(self.path).query
        return {k: v[-1] for k, v in urllib.parse.parse_qs(q).items()}

    def _path(self):
        return urllib.parse.urlparse(self.path).path

    def _signed_body(self, limit=None):
        """Read + verify an HMAC-signed body. Returns (dict, None) or (None, error)."""
        limit = limit or MAX_BODY
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length > limit:
            _counters["rejected_size"] += 1
            self._json({"error": f"body larger than {limit} bytes"}, 413)
            return None, "size"
        raw = self.rfile.read(length) if length else b""
        if not SECRET:
            self._json({"error": "bus secret not configured"}, 500)
            return None, "secret"
        sig = self.headers.get("X-Signature", "")
        want = hmac_mod.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
        if not sig or not hmac_mod.compare_digest(sig, want):
            _counters["rejected_sig"] += 1
            self._json({"error": "bad signature"}, 401)
            return None, "sig"
        try:
            return json.loads(raw or b"{}"), None
        except Exception:  # noqa: BLE001
            self._json({"error": "bad json"}, 400)
            return None, "json"

    # -- GET --
    def do_GET(self):
        global _active_polls
        path, qs = self._path(), self._qs()

        if path == "/health":
            with db() as c:
                n = c.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                a = c.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
            self._json({"status": "ok", "port": PORT, "messages": n, "agents": a,
                        "active_polls": _active_polls,
                        "uptime_s": round(time.time() - START, 1),
                        "telemetry_topics": sorted(TELEMETRY_TOPICS),
                        "fts": _fts, "ts": time.time()})
            return

        if path == "/metrics":
            with db() as c:
                n = c.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                a = c.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
            lines = [
                "# HELP agent_chat_messages_total Messages stored",
                "# TYPE agent_chat_messages_total gauge",
                f"agent_chat_messages_total {n}",
                "# TYPE agent_chat_agents_total gauge",
                f"agent_chat_agents_total {a}",
                "# TYPE agent_chat_active_polls gauge",
                f"agent_chat_active_polls {_active_polls}",
                "# TYPE agent_chat_uptime_seconds gauge",
                f"agent_chat_uptime_seconds {time.time() - START:.0f}",
            ]
            for k, v in sorted(_counters.items()):
                lines += [f"# TYPE agent_chat_{k}_total counter",
                          f"agent_chat_{k}_total {v}"]
            body = ("\n".join(lines) + "\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/tip":
            with db() as c:
                row = c.execute("SELECT COALESCE(MAX(id), 0), COUNT(*) FROM messages").fetchone()
            self._json({"max_id": row[0], "messages": row[1]})
            return

        if path == "/api/messages":
            since = int(qs.get("since", 0) or 0)
            limit = min(int(qs.get("limit", "500") or 500), 2000)
            topic = qs.get("topic")
            telemetry = qs.get("telemetry") in ("1", "true", "yes")
            thread = int(qs["thread"]) if qs.get("thread") else None
            before = int(qs["before"]) if qs.get("before") else None
            wait = max(0.0, min(float(qs.get("wait", 0) or 0), 55.0))
            deadline = time.time() + wait
            out = []
            while True:
                with db() as c:
                    out = select_msgs(c, since, limit, topic, thread, telemetry, before)
                if out or time.time() >= deadline:
                    break
                time.sleep(0.4)
            self._json(out)
            return

        if path == "/api/search":
            q = (qs.get("q") or "").strip()
            if not q:
                self._json({"error": "q required"}, 400)
                return
            limit = min(int(qs.get("limit", "100") or 100), 500)
            with db() as c:
                self._json(search(c, q, qs.get("topic"), limit))
            return

        if path == "/api/mentions":
            name = (qs.get("name") or "").strip().lstrip("@")
            if not name:
                self._json({"error": "name required"}, 400)
                return
            since = int(qs.get("since", 0) or 0)
            limit = min(int(qs.get("limit", "100") or 100), 500)
            with db() as c:
                rows = c.execute(
                    f"{SEL} WHERE id > ? AND lower(text) LIKE ? ORDER BY id LIMIT ?",
                    (since, f"%@{name.lower()}%", limit)).fetchall()
            self._json([row_msg(r) for r in rows])
            return

        if path == "/api/reads":
            with db() as c:
                rows = c.execute("SELECT name, last_id, updated FROM reads "
                                 "ORDER BY updated DESC").fetchall()
            self._json([{"name": r[0], "last_id": r[1], "updated": r[2]} for r in rows])
            return

        if path in ("/agentbus.py", "/agentbus"):
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agentbus.py")
            try:
                with open(p, "rb") as fh:
                    body = fh.read()
            except OSError as e:
                self._json({"error": f"agentbus.py unavailable: {e}"}, 404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/x-python; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-SHA256", hashlib.sha256(body).hexdigest())
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/favicon.svg":
            body = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
                    b'<rect width="32" height="32" rx="7" fill="#07101f"/>'
                    b'<path d="M7 11h18v10H16l-5 4v-4H7z" fill="none" stroke="#78c8ff" '
                    b'stroke-width="2"/></svg>')
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/agents":
            with db() as c:
                rows = c.execute(
                    "SELECT a.name, a.registered_at, "
                    "  (SELECT COUNT(*) FROM messages m WHERE m.sender = a.name), "
                    "  (SELECT MAX(ts) FROM messages m WHERE m.sender = a.name) "
                    "FROM agents a ORDER BY a.name").fetchall()
            self._json([{"name": r[0], "registered_at": r[1],
                         "messages": r[2], "last_ts": r[3]} for r in rows])
            return

        if path == "/":
            self._html(viewer_html(ui_token(), HUMAN_NAME))
            return

        self._json({"error": "not found"}, 404)

    # -- POST --
    def do_POST(self):
        path = self._path()

        if path == "/ui/send":
            m, err = self._unsigned_body()
            if err:
                return
            if not ui_token_ok(m.get("token")):
                self._json({"error": "page token expired — reload the room"}, 401)
                return
            text = str(m.get("text", ""))[:20000].strip()
            if not text:
                self._json({"error": "text required"}, 400)
                return
            sender = (str(m.get("from") or "human")[:64]) or "human"
            topic = str(m.get("topic") or "general")[:64]
            if not rate_ok(sender):
                _counters["rejected_rate"] += 1
                self._json({"error": "rate limited"}, 429, {"Retry-After": "5"})
                return
            parent = _valid_parent(m.get("reply_to"))
            if parent is _BAD_PARENT:
                _counters["rejected_parent"] += 1
                self._json({"error": "reply_to does not exist"}, 400)
                return
            mid = store(sender, topic, text, "message", parent)
            _counters["ui_posts"] += 1
            self._json({"ok": True, "id": mid, "woken": dispatch_mentions(
                sender, topic, text, mid, parent)})
            return

        if path == "/api/reads":
            m, err = self._signed_body()
            if err:
                return
            name = str(m.get("name", ""))[:64].strip()
            if not name:
                self._json({"error": "name required"}, 400)
                return
            try:
                last = int(m.get("last_id"))
            except (TypeError, ValueError):
                self._json({"error": "last_id must be an integer"}, 400)
                return
            with _lock, db() as c:
                c.execute("INSERT INTO reads (name, last_id, updated) VALUES (?,?,?) "
                          "ON CONFLICT(name) DO UPDATE SET last_id=excluded.last_id, "
                          "updated=excluded.updated", (name, last, time.time()))
            self._json({"ok": True, "name": name, "last_id": last})
            return

        if path == "/agents/register":
            m, err = self._signed_body()
            if err:
                return
            name = str(m.get("name", ""))[:64].strip()
            url = str(m.get("webhook_url", ""))[:500].strip()
            if not name or not url.startswith("http"):
                self._json({"error": "name and http(s) webhook_url required"}, 400)
                return
            with _lock, db() as c:
                c.execute("INSERT OR REPLACE INTO agents (name, webhook_url, registered_at) "
                          "VALUES (?,?,?)", (name, url, time.time()))
            self._json({"ok": True, "registered": name})
            return

        if path != "/send":
            self._json({"error": "not found"}, 404)
            return

        m, err = self._signed_body()
        if err:
            return
        sender = str(m.get("from", ""))[:64]
        topic = str(m.get("topic", "general"))[:64]
        text = str(m.get("text", ""))[:20000]
        kind = str(m.get("kind", "message"))[:16]
        if not sender or not text:
            self._json({"error": "from and text required"}, 400)
            return
        if not rate_ok(sender):
            _counters["rejected_rate"] += 1
            self._json({"error": "rate limited"}, 429, {"Retry-After": "5"})
            return
        parent = _valid_parent(m.get("reply_to"))
        if parent is _BAD_PARENT:
            _counters["rejected_parent"] += 1
            self._json({"error": "reply_to does not exist"}, 400)
            return
        mid = store(sender, topic, text, kind, parent)
        _counters["sent"] += 1
        self._json({"ok": True, "id": mid,
                    "woken": dispatch_mentions(sender, topic, text, mid, parent)})

    def _unsigned_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length > MAX_BODY:
            self._json({"error": f"body larger than {MAX_BODY} bytes"}, 413)
            return None, "size"
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw or b"{}"), None
        except Exception:  # noqa: BLE001
            self._json({"error": "bad json"}, 400)
            return None, "json"

    def log_message(self, fmt, *args):
        print(time.strftime("%H:%M:%S"), fmt % args, flush=True)


_BAD_PARENT = object()


def _valid_parent(raw):
    """None when absent, the id when it exists, _BAD_PARENT when it does not."""
    if raw in (None, "", 0, "0"):
        return None
    try:
        pid = int(raw)
    except (TypeError, ValueError):
        return _BAD_PARENT
    with db() as c:
        if c.execute("SELECT 1 FROM messages WHERE id = ?", (pid,)).fetchone():
            return pid
    return _BAD_PARENT


def main():
    if not SECRET:
        raise SystemExit("AGENT_CHAT_SECRET not set — refusing to start open")
    migrate()
    servers = [ThreadingHTTPServer(("127.0.0.1", PORT), Handler)]
    print(f"agent-chat bus on http://127.0.0.1:{PORT} db={DB_PATH} fts={_fts}", flush=True)
    if BIND_HOST and BIND_HOST not in ("127.0.0.1", "localhost"):
        # Second listener so remote agents (tailnet, VPN) reach the same room while
        # local tools use loopback. One process, one SQLite file, one write lock.
        servers.append(ThreadingHTTPServer((BIND_HOST, PORT), Handler))
        print(f"agent-chat bus also on http://{BIND_HOST}:{PORT}", flush=True)

    stop = threading.Event()

    def _shut(signum, _frame):
        stop.set()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _shut)
        except (ValueError, OSError):
            pass

    threads = []
    for srv in servers:
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        threads.append(th)
    threading.Thread(target=backup_loop, daemon=True).start()
    threading.Thread(target=janitor_loop, daemon=True).start()
    threading.Thread(target=watchdog_loop, daemon=True).start()
    sd_notify("READY=1")

    try:
        stop.wait()
    finally:
        print("agent-chat bus stopping: checkpoint + backup", flush=True)
        for srv in servers:
            srv.shutdown()
        checkpoint()
        backup()


if __name__ == "__main__":
    main()
