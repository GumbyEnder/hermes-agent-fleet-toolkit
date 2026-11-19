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
import datetime
import hashlib
import hmac as hmac_mod
import json
import os
import re
import signal
import socket
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCHEMA_VERSION = 4

DB_PATH = os.environ.get("AGENT_CHAT_DB") or str(
    __import__("pathlib").Path.home() / ".local/share/agent-chat/room.db")
BACKUP_DIR = os.environ.get("AGENT_CHAT_BACKUP", "")   # empty disables the copy
BACKUP_INTERVAL = int(os.environ.get("AGENT_CHAT_BACKUP_INTERVAL", "3600"))
# Per-slot artifacts are the ATTESTED copies; how many keep (0 = keep every one).
BACKUP_KEEP = int(os.environ.get("AGENT_CHAT_BACKUP_KEEP", "24"))
SECRET = os.environ.get("AGENT_CHAT_SECRET", "")
# Provenance: the HMAC proves only that the signer holds SECRET, never WHICH
# holder it is. Every row therefore records the signing key's identity and the
# peer address it arrived from, so a mislabeled post is attributable after the
# fact instead of inferred from context (2026-11-17: 6488 read as frodo's).
KEY_ID = os.environ.get("AGENT_CHAT_KEY_ID", "fleet-shared")
KEY_FP = hashlib.sha256(SECRET.encode()).hexdigest()[:12] if SECRET else None
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
# The janitor's ticks are phased OFF the backup slot second so the two folders can
# disagree again. Both arms are `sleep(INTERVAL)` inside a thread started at boot,
# so their phase is ExecMainStart + the thread-start delta: two equal intervals
# started back to back coincide every hour by identity, which is why the janitor
# folds nothing at a slot second (moved=0) and one arm does all the folding.
# The phase lives HERE, not in a unit OnCalendar, so every restart re-anchors it
# to the new ExecMainStart for free; a fixed absolute time would only move the
# coincidence to the next restart. 0 restores the old coincidence for a rehearsal.
JANITOR_OFFSET = float(os.environ.get("AGENT_CHAT_JANITOR_OFFSET", "60"))
# Where checkpoint() tees its lines. stdout stays the journal; this file exists so
# the unlink watcher can see the one thing only this process witnesses: that a
# TRUNCATE actually ran. Read-only consumer: wal-unlink-watch-verdict.py.
BUS_CHECKPOINT_LOG = os.environ.get("AGENT_CHAT_BUS_CHECKPOINT_LOG") or os.path.expanduser(
    "~/agents/agent-chat/bus-checkpoint.log")
HUMAN_NAME = os.environ.get("AGENT_CHAT_HUMAN_NAME", "you")
USAGE_JSON = os.environ.get(
    "USAGE_JSON", os.path.expanduser("~/.local/share/agent-usage/usage.json"))
_usage_cache = {"mtime": None, "data": None}

# One runtime, one label. A posting wrapper whose default identity is mechanical
# (a shell default, a profile-name prefix such as "hermes_<profile>") must not
# create a second sender row for an agent that already posts under its own name:
# the viewer chips split, the /api/reads cursors split, and any "who said what"
# census attributed to `sender` counts two agents where there is one.
# Measured 2026-11-13: message #3636 landed as `hermes_buzzy` while 375 messages
# from the same runtime landed as `buzzy`. Normalised at ingest on BOTH write
# paths (signed /send and page-token /ui/send), so it cannot recur from a client
# we do not control. A genuinely different runtime must get a different name
# added here deliberately, never by accident of a client default.
SENDER_ALIASES = {"hermes_buzzy": "buzzy"}
SENDER_MAX = 64          # the bus-wide name bound (topic/name/reads all use 64)


def sender_error(raw):
    """None if `raw` is a usable sender, else the 400 message. One rule for BOTH
    ingest paths: an absent identity is refused, and an over-length one is refused.
    Neither is defaulted and neither is truncated -- silently turning 80 chars into
    64 is the same fork class the alias map exists to stop, only without a map.
    Measured 2026-11-13 (frodo): /ui/send defaulted a missing `from` to "human"
    while /send refused it, so one path absorbed an unattributable row."""
    s = str(raw or "")
    if not s:
        return "from required"
    if len(s) > SENDER_MAX:
        return "from too long (max %d chars)" % SENDER_MAX
    return None


def canon_sender(raw):
    s = str(raw or "")           # NOT truncated; see sender_error() at the call sites
    canon = SENDER_ALIASES.get(s)
    if canon is not None:
        print("sender alias: %r -> %r" % (s, canon), flush=True)
        return canon
    return s

UI_TTL = int(os.environ.get("AGENT_CHAT_UI_TTL", str(12 * 3600)))

START = time.time()
_lock = threading.Lock()
# One slot at a time: {checkpoint + copy} and {purge + checkpoint} are each a
# single ordered unit, and the two daemon arms must not interleave inside one.
# Measured 2026-11-13 19:27:50 vs 20:27:50 vs 21:27:50, same pid 2056350: the
# first src= token was backup, then janitor, then janitor, so the order is
# scheduler-dependent, not an invariant. At 21:27:50 the janitor's checkpoint
# truncated the sidecar 189,552 B -> 0 B at .367198 and the backup arm copied
# the 0 at .564974, printing `wal=copy:0` -- a state that reads like a fix and
# is really two threads racing inside one slot. Neither arm may fix this by
# assuming an order; they have to hold this lock across both steps.
_slot_lock = threading.Lock()
_buckets = {}                 # sender -> (tokens, last_ts)
_active_polls = 0
_fts = False                  # FTS5 available?
_counters = {"sent": 0, "rejected_sig": 0, "rejected_rate": 0, "rejected_size": 0,
             "rejected_parent": 0, "ui_posts": 0, "purged": 0, "wakes": 0}


# --- database ------------------------------------------------------------

class _ClosingConnection(sqlite3.Connection):
    """Connection whose context-manager exit also closes it.

    sqlite3's `with conn:` only commits/rolls back — it never closes — so every
    `with db() as c:` block leaked one connection (and its -wal fd). Measured
    2026-11-13 02:25:29: one bus PID held 85 handles on the live room.db inode and
    61 on room.db-wal, 77 of them opened at 01:46 and never closed. While any of
    those live, the WAL can never be unlinked, so a "last connection close" is
    unreachable by construction.
    """

    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


def db():
    conn = sqlite3.connect(DB_PATH, timeout=10, factory=_ClosingConnection)
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
        # v3: per-row provenance. Absent (NULL) on rows written before the upgrade,
        # which is itself the honest reading: their signer is unknown, not asserted.
        have = {row[1] for row in c.execute("PRAGMA table_info(messages)")}
        for col in ("key_id", "key_fp", "peer_addr"):
            if col not in have:
                c.execute(f"ALTER TABLE messages ADD COLUMN {col} TEXT")
        # v4: structured envelope. NULL means "no envelope", which is exactly what
        # every pre-v4 row is — not an empty object pretending to be one.
        if "data" not in have:
            c.execute("ALTER TABLE messages ADD COLUMN data TEXT")
        c.execute("""CREATE TABLE IF NOT EXISTS tasks (
                       id            INTEGER PRIMARY KEY AUTOINCREMENT,
                       created_ts    REAL NOT NULL,
                       updated_ts    REAL NOT NULL,
                       creator       TEXT NOT NULL,
                       owner         TEXT,
                       claimed_by    TEXT,
                       claim_expires REAL,
                       title         TEXT NOT NULL,
                       detail        TEXT,
                       topic         TEXT,
                       ref           TEXT,
                       state         TEXT NOT NULL DEFAULT 'open',
                       outcome       TEXT,
                       evidence      TEXT,
                       data          TEXT,
                       root_id       INTEGER)""")
        c.execute("""CREATE TABLE IF NOT EXISTS task_events (
                       id      INTEGER PRIMARY KEY AUTOINCREMENT,
                       task_id INTEGER NOT NULL,
                       ts      REAL NOT NULL,
                       actor   TEXT NOT NULL,
                       action  TEXT NOT NULL,
                       note    TEXT,
                       data    TEXT)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state, updated_ts)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks(owner, claimed_by)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_task_events ON task_events(task_id)")
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


def _log_line(line):
    """stdout byte-for-byte, plus the db-tagged tee file.

    One writer for both arms so a field added to a checkpoint line cannot land in
    the tee and be missing from the journal (or the reverse). The tee's prefix is
    load-bearing: measured 2026-11-13, a rehearsal that redirected AGENT_CHAT_DB but
    not AGENT_CHAT_BUS_CHECKPOINT_LOG wrote plausible-looking lines into the
    production file with nothing saying which database they described.
    """
    print(line, flush=True)
    try:
        with open(BUS_CHECKPOINT_LOG, "a") as f:
            f.write("%s db=%s %s\n"
                    % (time.strftime("%Y-%m-%dT%H:%M:%S%z"), DB_PATH, line))
    except OSError:
        pass                    # a full/unwritable home must not break a checkpoint


def _wal_state():
    """Sidecar size in bytes, or 'absent' — read AFTER the connection has closed.

    The TRUNCATE tuple cannot answer this: removal needs the LAST close, which
    happens when `with db()` exits, so the tuple reports a fold on a file that is
    about to be unlinked. Measured 2026-11-13 11:17:53 -> the pair at :15:19/:15:20
    folds, the janitor's connection closes, sqlite unlinks, and `backup()`'s
    `os.path.exists()` one second later answers `no`. Publishing `moved=` without
    this field leaves "the janitor folded 30" and "the wal was not copied" looking
    like a contradiction when they are one ordered pair. Other threads can still
    open/close in the gap, so this is the state at the close, not at the copy.
    """
    try:
        return str(os.stat(DB_PATH + "-wal").st_size)
    except OSError:
        return "absent"


def _slot_token():
    """Unique per slot, not per second.

    `time.strftime("%Y%m%dT%H%M%S%z")` is only unique at the production
    BACKUP_INTERVAL of 3600 s. Rehearsed at 0.40 s the manifest carried 14 rows
    under 6 distinct tokens, one token holding 3 rows -- so the manifest's own
    key was not a key, and any harness pairing rows to slots by position was
    grading the ordering assumption the lock exists to remove. Microseconds fix
    it without a counter that would reset on restart.
    """
    return datetime.datetime.now().astimezone().strftime("%Y%m%dT%H%M%S.%f%z")


def checkpoint(src="manual", slot=None):
    """TRUNCATE the WAL and REPORT the outcome.

    A blocked checkpoint is NOT an exception: PRAGMA wal_checkpoint returns
    (busy, log, checkpointed) and exits 0 whether it folded everything or
    nothing, so the previous form -- discard the tuple, log only exceptions --
    printed nothing in either case. That made the bus's one teardown path
    un-instrumented: measured 2026-11-13 03:42:17.985-04:03:10.751 (1,252.77 s,
    the DELETE-to-DELETE gap in wal-unlink-watch.log), a connection that stayed
    open across the whole window held room.db so no request close was ever the
    last close -- a single-instant fuser read at 03:55:40 named pid 2162568
    (hermes_kernel_runner.py, started 03:38:22), but that process is gone and the
    attribution has one observer, so treat it as a lead, not a fact -- the sidecar
    went 115,392 -> 181,312 -> 259,592 B and the bus logged zero lines.
    Reproduced on scratch with this tuple: an idle holder still
    lets TRUNCATE succeed (0|N|N -> 0 B file, never unlinked), while a holder
    inside a read transaction returns busy=1 with checkpointed < log and leaves
    those frames un-folded -- the only shape that risks a row. Print every call
    so "silence" can never again be read as success; the 2-min PASSIVE probe in
    wal-checkpoint.sh already speaks this same busy|log|checkpointed tuple.
    """
    try:
        with db() as c:
            # PASSIVE first: its tuple is the PRE-FOLD state (log = frames sitting
            # in the WAL at this instant). TRUNCATE's tuple is POST-FOLD state and
            # on success is always (0,0,0) -- measured 2026-11-13 on scratch in
            # both sqlite 3.53.1 and the 3.45.1 CLI: 22 frames + TRUNCATE ->
            # (0,0,0) with room.db mtime moving. So the post tuple can never size
            # a fold; without this pre-read every successful tick printed zeros.
            pre_busy, pre_log, pre_folded = c.execute(
                "PRAGMA wal_checkpoint(PASSIVE)").fetchone()
            busy, log, folded = c.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        # busy=1 with log=-1 is NOT a pin: sqlite could not run the checkpoint at
        # all (a concurrent WRITER, not a read mark). Measured 2026-11-13
        # 08:35:47: the janitor's TRUNCATE returned (1,-1,-1) 0.6 s before the
        # backup thread's returned (0,66,66) moved=66 in the SAME process, so
        # `busy or folded<log` printed PINNED on a healthy file. Never source
        # PINNED from busy.
        #
        # What this line CANNOT say: the unlink gate. Measured the same second,
        # the backup arm TRUNCATEd 1,182,472 -> 0 under an idle reader and the
        # sidecar was still not removed -- because removal needs the LAST close,
        # which is decided outside this process by whichever connection closes
        # last. So folded==log/busy==0 is the production shape of a live unlink
        # pin and it is indistinguishable from healthy here. Only the absence of
        # a DELETE room.db-wal record can see it (wal-unlink-watch verdict.py).
        # Tokens: BUSY = sqlite could not run the checkpoint at all (log=-1);
        # PINNED = the tuple reports frames still un-folded. PINNED is graded off
        # the PRE-line only: the post tuple is TRUNCATE's own report and repeats
        # the pre numbers on a pin, so tagging both lines doubles one fact.
        # Rehearsed 2026-11-13 09:31 on a scratch DB: a held READ transaction
        # printed pre 0|1568|0 PINNED and post 1|1568|0 -- the pre-line alone
        # carries it. A held write transaction printed post 1|0|0, untagged and
        # correct: log=0 means no frames are at risk.
        # BUSY stays on both lines: it is a per-call outcome, and the TRUNCATE can
        # fail on its own after a PASSIVE that ran. PINNED is suppressed on the post
        # line only when the PRE line already printed it -- suppressing it
        # unconditionally lost a pin that the pre tuple did not see (scripted
        # 2026-11-13: pre 0|66|66 clean, post 1|66|20, no token on either line).
        pre_pinned = pre_folded < pre_log
        def _tag(busy, log, folded, pre_pinned=False):
            if busy and log < 0:
                return " BUSY"
            if folded < log and not pre_pinned:
                return " PINNED"
            return ""

        def _emit(line):
            # stdout unchanged byte-for-byte (journal consumers stay put), plus a
            # timestamped copy the unlink watcher can read without journal access.
            # The timestamp is what dates "a TRUNCATE ran" to an instant; journal
            # adds its own prefix but nothing on disk carries one.
            #
            # The file copy also carries db=<path> and stdout does NOT. Measured
            # 2026-11-13 09:47:55 and 09:57:43-09:58:01: a test/rehearsal that
            # pointed AGENT_CHAT_DB at a scratch file but not
            # AGENT_CHAT_BUS_CHECKPOINT_LOG wrote 14 plausible-looking
            # "src=shutdown ... 0|0|0" lines into the production file, and nothing
            # in them said which database they were about -- a reader would grade
            # a scratch fold as a production TRUNCATE. Any consumer must key on
            # db= and may never assume "a line in this file is this database".
            _log_line(line)

        _emit("checkpoint-pre: busy=%d log=%d folded=%d src=%s%s%s"
              % (pre_busy, pre_log, pre_folded, src,
                 _tag(pre_busy, pre_log, pre_folded),
                 "" if slot is None else " slot=" + slot))
        _emit("checkpoint: busy=%d log=%d folded=%d moved=%d src=%s wal=%s%s%s"
              % (busy, log, folded, pre_log - log, src, _wal_state(),
                 _tag(busy, log, folded, pre_pinned),
                 "" if slot is None else " slot=" + slot))
    except Exception as e:  # noqa: BLE001
        print("checkpoint failed: src=%s err=%s" % (src, e), flush=True)
        try:
            with open(BUS_CHECKPOINT_LOG, "a") as f:
                f.write("%s db=%s checkpoint failed: src=%s err=%s\n"
                        % (time.strftime("%Y-%m-%dT%H:%M:%S%z"), DB_PATH, src, e))
        except OSError:
            pass


def _prune_slot_artifacts(new_slot):
    """Keep the newest BACKUP_KEEP per-slot artifacts per base name.

    Slot tokens sort chronologically as strings (YYYYmmddTHHMMSS.ffffff+ZZZZ),
    so the prune needs no stat of a CIFS destination. The slot just written is
    never a victim. BACKUP_KEEP <= 0 disables pruning.
    """
    if BACKUP_KEEP <= 0 or not BACKUP_DIR:
        return
    slot_re = re.compile(r"^\d{8}T\d{6}\.\d{6}[+-]\d{4}$")
    for base in ("room.db.bak", "room.db-wal.bak"):
        pref = base + "."
        try:
            names = [n for n in os.listdir(BACKUP_DIR)
                     if n.startswith(pref) and slot_re.match(n[len(pref):])]
        except OSError:
            continue
        names.sort()
        for n in names[:-BACKUP_KEEP] if len(names) > BACKUP_KEEP else []:
            if n == base + "." + new_slot:
                continue
            try:
                os.unlink(os.path.join(BACKUP_DIR, n))
            except OSError:
                pass


def backup(src="backup", slot=None):
    """Copy the pair and PUBLISH what was copied, per file, with a hash.

    The decision is the evidence, so record it. `if os.path.exists(f)` is the
    whole discriminator between "copied a quiet file" (stamp frozen, content
    fresh) and "copied nothing at all" (stamp AND content from the previous
    generation) -- two states indistinguishable from the .bak stamp alone,
    because copy2 preserves the SOURCE mtime either way.

    What the old line could NOT say, and what this one must: `wal=skip:absent`
    and `wal=copy:0` are both possible outcomes and both look identical to a
    reader who only has the artifact, so a skip got read as an hour of cost off
    a timestamp gap (+7,951 s on 2026-11-13 19:46, refuted by the 20:27 and
    21:27 slots that re-synced the pair). The manifest below is per-slot and
    append-only: bytes AND sha256 of what landed, so identity is
    size+hash+the SOURCE inode read under the same fd that supplies the bytes,
    and never the destination's mtime (a CIFS synthetic inode shifts between
    mount windows on this host -- measured 2026-11-13 21:38, room.db.bak ino
    731973006, btime 2026-11-05 03:09:31.768, mtime moved). The destination
    mtime is not an identity leg at all: on /mnt/nas a copy2 lands with the
    source's mtime and the SERVER restamps it to its own close time seconds
    later (measured 2026-11-14 00:50: copy2 read back the source stamp exactly,
    3 s later the stamp was the server's). So record src_mtime_ns from fstat
    and copy_time_ns from our own clock, and never a post-copy stat of dst.
    A 0-byte sidecar is a LEGITIMATE state after a TRUNCATE fold; the manifest
    is what keeps it from being read as a silent one.

    PER-SLOT ARTIFACTS ARE LOAD-BEARING (#4268 point 2). `room.db.bak` is one
    rolling name that the next slot overwrites, so a row's bytes/sha256 describe
    a file only until the next write -- measured 2026-11-14: slot 02:14:03 was
    PASS at 02:14 and FAIL at 02:19 because the 02:18:00.814751 shutdown row had
    already overwritten the path, and 03:18:01 rolled it again. Every row now
    also writes `room.db.bak.<slot>` / `room.db-wal.bak.<slot>`, which nothing
    else writes, and records its name as `path`. The rolling name is kept as a
    restore-compat alias, but no verdict may cite it: cite `path`.

    THE PER-SLOT WRITE IS TRUNCATE-THEN-STREAM, NOT ATOMIC. The name is opened
    `wb` and the chunks are written through it, so a kill mid-copy leaves a SHORT
    per-slot artifact under a name nothing rewrites -- a torn file that looks like
    a complete one to any reader who trusts its presence. That is harmless today
    for exactly one reason: the manifest row is appended AFTER both copies, so no
    row ever attests bytes that were not all written. Moving that append earlier
    (to record an intent, or to survive a kill) would make the row attest a torn
    artifact -- do not, or write to `path + ".part"` and rename after fsync.
    """
    if not BACKUP_DIR:
        return None
    slot = slot or _slot_token()
    src = src or "backup"
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        states = []
        rec = {"slot": slot, "src": src, "pid": os.getpid(), "db": DB_PATH,
               "files": {}}
        for f in (DB_PATH, DB_PATH + "-wal"):
            base = os.path.basename(f) + ".bak"
            dst = os.path.join(BACKUP_DIR, base)          # rolling alias
            dst_slot = os.path.join(BACKUP_DIR, base + "." + slot)  # attested
            try:
                fd = os.open(f, os.O_RDONLY)
            except FileNotFoundError:
                fd = None
            except OSError as e:
                # Unreadable is NOT absent: say so per file and keep the loop.
                states.append("copy:unreadable")
                rec["files"][base] = {"bytes": None, "state": "unreadable",
                                      "error": str(e)}
                continue
            if fd is not None:
                try:
                    sst = os.fstat(fd)          # same inode as the bytes below
                    h = hashlib.sha256()
                    copy_time_ns = time.time_ns()
                    with os.fdopen(os.dup(fd), "rb") as rf, \
                            open(dst, "wb") as wf, open(dst_slot, "wb") as ws:
                        for chunk in iter(lambda: rf.read(1 << 20), b""):
                            h.update(chunk)
                            wf.write(chunk)
                            ws.write(chunk)
                    for p in (dst, dst_slot):
                        try:                    # best effort; CIFS may restamp
                            os.utime(p, ns=(sst.st_atime_ns, sst.st_mtime_ns))
                        except OSError:
                            pass
                finally:
                    os.close(fd)
                states.append("copy:%d" % sst.st_size)
                rec["files"][base] = {"bytes": sst.st_size,
                                      "sha256": h.hexdigest(),
                                      "src_ino": sst.st_ino,
                                      "src_mtime_ns": sst.st_mtime_ns,
                                      "copy_time_ns": copy_time_ns,
                                      "path": os.path.basename(dst_slot)}
            else:
                states.append("skip:absent")
                rec["files"][base] = {"bytes": None, "state": "absent"}
        _log_line("backup: main=%s wal=%s src=%s slot=%s"
                  % (states[0], states[1], src, slot))
        rec["main"], rec["wal"] = states[0], states[1]
        with open(os.path.join(BACKUP_DIR, "room.db.bak.manifest.jsonl"), "a") as mf:
            mf.write(json.dumps(rec, sort_keys=True) + "\n")
        _prune_slot_artifacts(slot)
        return rec
    except Exception as e:  # noqa: BLE001
        print("backup failed:", e, flush=True)
        _log_line("backup failed: err=%s src=%s slot=%s" % (e, src, slot))
        return None


def _slot(src, copy=False):
    """Run one slot atomically: the checkpoint that measures it and, for the
    copy arms, the backup that consumes it. Anything that reads the sidecar
    outside this lock can observe a fold it did not cause and cannot name.

    `copy` is explicit rather than `src == "backup"` so the shutdown folder is
    a copy too: shutdown used to call checkpoint() then backup() from outside
    the lock, which made a third folder and a second writer onto the same
    room.db.bak destination -- a daemon arm mid-copy could be interleaved by
    the shutdown copy and hand back a torn restore artifact. The token is
    shared by the checkpoint and the manifest row for that folder, so a row
    names its own pair instead of being paired by position.
    """
    slot = _slot_token()
    with _slot_lock:
        checkpoint(src, slot=slot)
        if copy:
            return backup(src, slot=slot)
    return None


def backup_loop():
    while True:
        time.sleep(BACKUP_INTERVAL)
        _slot("backup", copy=True)


def janitor_loop():
    """Delete old telemetry-topic rows so the conversation stays readable."""
    if not TELEMETRY_TOPICS or TELEMETRY_DAYS <= 0:
        return
    # Tick first, THEN sleep: that is what makes JANITOR_OFFSET a phase instead of
    # an extra hour of delay on the first fold. `%` so an offset >= interval
    # degrades to one tick rather than a burst.
    time.sleep(JANITOR_OFFSET % JANITOR_INTERVAL)
    while True:
        try:
            cutoff = time.time() - TELEMETRY_DAYS * 86400
            marks = ",".join("?" for _ in TELEMETRY_TOPICS)
            with _lock, db() as c:
                cur = c.execute(
                    f"DELETE FROM messages WHERE topic IN ({marks}) AND ts < ?",
                    (*sorted(TELEMETRY_TOPICS), cutoff))
                _counters["purged"] += cur.rowcount or 0
            _slot("janitor")
        except Exception as e:  # noqa: BLE001
            print("janitor failed:", e, flush=True)
        time.sleep(JANITOR_INTERVAL)


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
    """Message wire shape. `data` is the v4 envelope: parsed when present, and
    omitted entirely on rows written before v4 (unknown, not empty)."""
    out = {"id": r[0], "ts": r[1], "sender": r[2], "topic": r[3],
           "text": r[4], "kind": r[5], "parent_id": r[6]}
    if len(r) > 7 and r[7]:
        parsed = _data_obj(r[7])
        if parsed is not None:
            out["data"] = parsed
    return out


SEL = "SELECT id, ts, sender, topic, text, kind, parent_id, data FROM messages"


def select_msgs(c, since, limit, topic, thread=None, telemetry=False, before=None,
                sender=None, kinds=None):
    where, args = (["id < ?"], [before]) if before is not None else (["id > ?"], [since])
    if topic:
        where.append("topic = ?")
        args.append(topic)
    elif not telemetry and TELEMETRY_TOPICS:
        where.append("topic NOT IN (%s)" % ",".join("?" for _ in TELEMETRY_TOPICS))
        args.extend(sorted(TELEMETRY_TOPICS))
    if sender:
        where.append("sender = ?")
        args.append(sender)
    if kinds:
        where.append("kind IN (%s)" % ",".join("?" for _ in kinds))
        args.extend(kinds)
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
                "SELECT m.id, m.ts, m.sender, m.topic, m.text, m.kind, m.parent_id, m.data "
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


def dispatch_mentions(sender, topic, text, mid, parent_id=None, ts=None):
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
                          "ts": ts,
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


def store(sender, topic, text, kind="message", parent_id=None,
          key_id=None, key_fp=None, peer_addr=None, data=None):
    """Insert a message under the write lock. Returns (new id, row ts).

    The ts is hoisted out of the INSERT so the caller can carry the sender's own
    clock into the wake payload. It is the only clock that can prove how long a
    mention waited before a turn started; a log line near the mention is not.

    key_id/key_fp/peer_addr are the signing provenance: which key signed the
    body and where it came in from. They are stored, never trusted as identity.
    """
    ts = time.time()
    with _lock, db() as c:
        cur = c.execute(
            "INSERT INTO messages (ts, sender, topic, text, kind, parent_id,"
            " key_id, key_fp, peer_addr, data) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ts, sender, topic, text, kind, parent_id,
             key_id, key_fp, peer_addr, data))
        return cur.lastrowid, ts



# --- analytics -------------------------------------------------------------

def load_usage():
    """Read the fleet collector's usage.json (read-only, mtime-cached)."""
    try:
        st = os.stat(USAGE_JSON)
    except OSError:
        return None
    if _usage_cache["mtime"] != st.st_mtime:
        try:
            with open(USAGE_JSON) as fh:
                _usage_cache["data"] = json.load(fh)
            _usage_cache["mtime"] = st.st_mtime
        except (OSError, ValueError):
            return None
    return _usage_cache["data"]


def daily_usage(usage, days):
    """Turn the collector's cumulative snapshots into per-day deltas."""
    hist = sorted(usage.get("history") or [], key=lambda r: r.get("ts") or 0)
    cutoff = time.time() - days * 86400
    buckets = {}
    prev = None
    for r in hist:
        if prev is not None:
            d = time.strftime("%Y-%m-%d", time.localtime(prev["ts"]))
            b = buckets.setdefault(d, {"date": d, "input": 0, "output": 0,
                                       "cost": 0.0, "sessions": 0})
            b["input"] += max(0, int(r.get("input", 0)) - int(prev.get("input", 0)))
            b["output"] += max(0, int(r.get("output", 0)) - int(prev.get("output", 0)))
            b["cost"] += max(0.0, float(r.get("billable_cost", 0)) -
                             float(prev.get("billable_cost", 0)))
            b["sessions"] += max(0, int(r.get("sessions", 0)) - int(prev.get("sessions", 0)))
        prev = r
    out = [b for d, b in sorted(buckets.items())
           if time.mktime(time.strptime(d, "%Y-%m-%d")) >= cutoff]
    for b in out:
        b["cost"] = round(b["cost"], 2)
    return out


def day_keys(days):
    """Local-date labels for the last `days` days, oldest first, gaps included.

    Built with datetime arithmetic rather than `start + n*86400` so a DST
    transition inside the window does not duplicate or skip a calendar day.
    """
    today = datetime.date.today()
    return [(today - datetime.timedelta(days=days - 1 - i)).isoformat()
            for i in range(days)]


def room_analytics(c, days):
    since = time.time() - days * 86400
    keys = day_keys(days)
    key_set = set(keys)
    # per-day volume, zero-filled: a quiet day is a data point, not a gap.
    raw_day = c.execute(
        "SELECT strftime('%Y-%m-%d', ts, 'unixepoch', 'localtime') d, COUNT(*) n "
        "FROM messages WHERE ts >= ? GROUP BY d", (since,)).fetchall()
    day_count = {r[0]: r[1] for r in raw_day}
    per_day = [{"date": k, "messages": day_count.get(k, 0)} for k in keys]

    by_sender = c.execute(
        "SELECT sender, COUNT(*) n, MAX(ts) last, MIN(ts) first, SUM(LENGTH(text)) chars, "
        "  COUNT(parent_id) replies "
        "FROM messages WHERE ts >= ? GROUP BY sender ORDER BY n DESC LIMIT 40",
        (since,)).fetchall()
    by_topic = c.execute(
        "SELECT topic, COUNT(*) n FROM messages WHERE ts >= ? GROUP BY topic "
        "ORDER BY n DESC LIMIT 12", (since,)).fetchall()
    total, first, last = c.execute(
        "SELECT COUNT(*), MIN(ts), MAX(ts) FROM messages").fetchone()
    window = c.execute("SELECT COUNT(*) FROM messages WHERE ts >= ?", (since,)).fetchone()[0]
    threads = c.execute(
        "SELECT COUNT(DISTINCT parent_id) FROM messages WHERE parent_id IS NOT NULL").fetchone()[0]
    day_today = time.strftime("%Y-%m-%d")
    today = day_count.get(day_today, 0)

    # Volume split by speaker over time: the "who is talking" series.
    lead = [r[0] for r in by_sender[:6]]
    per_day_sender = c.execute(
        "SELECT strftime('%Y-%m-%d', ts, 'unixepoch', 'localtime') d, sender, COUNT(*) n "
        "FROM messages WHERE ts >= ? GROUP BY d, sender", (since,)).fetchall()
    grid = {name: dict.fromkeys(keys, 0) for name in lead}
    for d, sender, n in per_day_sender:
        if d in key_set and sender in grid:
            grid[sender][d] = n
    series = [{"sender": name, "counts": [grid[name][k] for k in keys]} for name in lead]
    other = [max(0, (day_count.get(k, 0)) - sum(grid[s][k] for s in lead)) for k in keys]
    if any(other):
        series.append({"sender": "other", "counts": other})

    # Conversation rhythm: which hours the room actually speaks in.
    per_hour = [0] * 24
    for (h, n) in c.execute(
            "SELECT CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER) h, "
            "COUNT(*) n FROM messages WHERE ts >= ? GROUP BY h", (since,)):
        if 0 <= h < 24:
            per_hour[h] = n

    # Reply graph: who answers whom. Threads are stored as parent_id, so this
    # is a real measured edge, not a proximity guess.
    pair_rows = c.execute(
        "SELECT p.sender, m.sender, COUNT(*) n FROM messages m "
        "JOIN messages p ON m.parent_id = p.id "
        "WHERE m.ts >= ? GROUP BY p.sender, m.sender ORDER BY n DESC LIMIT 24",
        (since,)).fetchall()
    starts = {r[0]: r[1] for r in c.execute(
        "SELECT sender, COUNT(*) FROM messages WHERE ts >= ? AND parent_id IS NULL "
        "GROUP BY sender", (since,))}

    # Thread health: how much of the room is loops nobody closed, and how long
    # a reply actually takes. Both are computed over the window, not all time.
    roots = c.execute(
        "SELECT p.id, COUNT(ch.id) FROM messages p LEFT JOIN messages ch"
        "  ON ch.parent_id = p.id WHERE p.parent_id IS NULL AND p.ts >= ?"
        "  GROUP BY p.id", (since,)).fetchall()
    open_roots = sum(1 for r in roots if r[1] == 0)
    lags = sorted(r[0] for r in c.execute(
        "SELECT m.ts - p.ts FROM messages m JOIN messages p ON m.parent_id = p.id"
        "  WHERE m.ts >= ? ORDER BY m.id DESC LIMIT 2000", (since,)))
    median_lag = int(lags[len(lags) // 2]) if lags else None
    tip = c.execute("SELECT COALESCE(MAX(id), 0) FROM messages").fetchone()[0]
    backlog = [{"name": r[0], "last_id": r[1], "backlog": max(0, tip - r[1]),
                "updated": r[2], "age_s": int(time.time() - r[2])}
               for r in c.execute("SELECT name, last_id, updated FROM reads"
                                  " ORDER BY updated DESC")]

    active = sum(1 for r in by_sender if r[1] > 0)
    peak = max(per_day, key=lambda r: r["messages"]) if per_day else {"date": None, "messages": 0}
    return {
        "days": days, "since": since, "total": total, "first_ts": first, "last_ts": last,
        "window_messages": window, "today_messages": today, "threads": threads,
        "dates": keys,
        "per_day": per_day,
        "per_day_senders": series,
        "per_hour": per_hour,
        "reply_graph": [{"from": r[0], "to": r[1], "messages": r[2]} for r in pair_rows],
        "stats": {
            "avg_per_day": round(window / days, 1) if days else 0,
            "peak": peak,
            "active_senders": active,
            "days_with_traffic": sum(1 for r in per_day if r["messages"] > 0),
            "replies": sum(r[5] or 0 for r in by_sender),
            "thread_roots": len(roots),
            "open_threads": open_roots,
            "unanswered_rate": round(open_roots / len(roots), 3) if roots else 0,
            "median_reply_lag_s": median_lag,
        },
        "backlog": backlog,
        "by_sender": [{"sender": r[0], "messages": r[1], "last_ts": r[2],
                       "first_ts": r[3], "chars": r[4], "avg_chars":
                       round((r[4] or 0) / r[1], 1) if r[1] else 0,
                       "replies": r[5] or 0, "threads_started": starts.get(r[0], 0)}
                      for r in by_sender],
        "by_topic": [{"topic": r[0], "messages": r[1]} for r in by_topic],
    }


def analytics_payload(days):
    with db() as c:
        room = room_analytics(c, days)
    usage = load_usage()
    if not usage:
        return {"generated": time.time(), "room": room, "usage": {"available": False},
                "agents": room["by_sender"]}
    daily = daily_usage(usage, days)
    windows = {}
    for name, w in (usage.get("windows") or {}).items():
        windows[name] = {"total": w.get("total", {}),
                         "by_profile": w.get("by_profile", {})}
    today = next((d for d in daily if d["date"] == time.strftime("%Y-%m-%d")), None)
    by_profile = (windows.get("7d") or {}).get("by_profile") or {}
    merged = []
    for row in room["by_sender"]:
        name = row["sender"]
        prof = by_profile.get(name) or {}
        merged.append({**row,
                       "tokens_7d": int(prof.get("input", 0)) + int(prof.get("output", 0)),
                       "cost_7d": round(float(prof.get("est_cost", 0)), 2),
                       "sessions_7d": prof.get("sessions", 0)})
    for name, prof in by_profile.items():
        if any(m["sender"] == name for m in merged):
            continue
        merged.append({"sender": name, "messages": 0, "last_ts": prof.get("last_active"),
                       "chars": 0, "tokens_7d": int(prof.get("input", 0)) + int(prof.get("output", 0)),
                       "cost_7d": round(float(prof.get("est_cost", 0)), 2),
                       "sessions_7d": prof.get("sessions", 0)})
    return {
        "generated": time.time(),
        "room": room,
        "usage": {"available": True, "generated": usage.get("generated"),
                  "windows": windows, "today": today, "daily": daily,
                  "budgets": usage.get("budgets") or [],
                  "budget_limit_usd": usage.get("budget_limit_usd"),
                  "anomaly": usage.get("anomaly") or {},
                  "top_sessions": (usage.get("top_sessions") or [])[:6],
                  "profiles": usage.get("profiles") or []},
        "agents": sorted(merged, key=lambda r: (-(r.get("cost_7d") or 0), -r["messages"])),
    }




# --- agent triage: inbox / digest / threads / presence ---------------------
#
# The room is a transcript: cheap to write into, expensive to read. These read
# paths exist so an agent can decide *whether* to read a message without paying
# for its body (median message in the live room is ~2.3k chars).
#
# Every endpoint here is read-only and unsigned, same trust boundary as reading
# the room at all: whoever can read `#work` can read their own inbox.

PREVIEW_CHARS = 200


def _preview(text, n=PREVIEW_CHARS):
    flat = " ".join((text or "").split())
    return flat[:n] + ("\u2026" if len(flat) > n else "")


def shape_msg(m, max_chars=None, meta_only=False):
    """Shrink one message for a reader that only needs to triage it.

    meta_only drops the body entirely and leaves `chars` + `preview`, which is
    the difference between a 60-token triage and a 700-token ingest.
    """
    if m is None:
        return None
    text = m.get("text") or ""
    chars = len(text)
    out = dict(m)
    if meta_only:
        out.pop("text", None)
        out["chars"] = chars
        out["preview"] = _preview(text)
        return out
    if max_chars:
        out["chars"] = chars
        if chars > max_chars:
            out["text"] = text[:max_chars]
            out["truncated"] = True
    return out


def _thread_roots(c, since_id, topic, limit, telemetry, state="all", stale_before=None):
    """Root messages with their reply counts, newest activity first.

    `state` is applied as HAVING, not by filtering the returned page: filtering
    after LIMIT returned one open thread out of a room with hundreds, because the
    most recent page happened to be all answered.
    """
    where = ["p.parent_id IS NULL", "p.id >= ?"]
    args = [since_id]
    if topic:
        where.append("p.topic = ?")
        args.append(topic)
    elif not telemetry and TELEMETRY_TOPICS:
        where.append("p.topic NOT IN (%s)" % ",".join("?" for _ in TELEMETRY_TOPICS))
        args.extend(sorted(TELEMETRY_TOPICS))
    having = ""
    if state == "open":
        having = " HAVING COUNT(ch.id) = 0"
    elif state == "answered":
        having = " HAVING COUNT(ch.id) > 0"
    elif state == "stale":
        having = (" HAVING COUNT(ch.id) = 0 AND "
                  "COALESCE(MAX(ch.ts), p.ts) <= ?")
        args.append(stale_before if stale_before is not None else time.time() - 86400)
    args.append(limit)
    rows = c.execute(
        "SELECT p.id, p.ts, p.sender, p.topic, p.text, COUNT(ch.id), "
        "       MAX(ch.ts), MIN(ch.ts) "
        "FROM messages p LEFT JOIN messages ch ON ch.parent_id = p.id "
        f"WHERE {' AND '.join(where)} GROUP BY p.id{having} "
        "ORDER BY COALESCE(MAX(ch.ts), p.ts) DESC LIMIT ?", args).fetchall()
    if not rows:
        return []
    ids = [r[0] for r in rows]
    marks = ",".join("?" for _ in ids)
    parts = {}
    for root, sender in c.execute(
            f"SELECT COALESCE(parent_id, id) root, sender FROM messages "
            f"WHERE COALESCE(parent_id, id) IN ({marks}) GROUP BY root, sender",
            ids):
        parts.setdefault(root, []).append(sender)
    now = time.time()
    return [{
        "id": r[0], "ts": r[1], "sender": r[2], "topic": r[3],
        "preview": _preview(r[4]), "chars": len(r[4] or ""),
        "replies": r[5], "answered": r[5] > 0,
        "last_ts": r[6] if r[5] else r[1],
        "age_s": int(now - (r[6] if r[5] else r[1])),
        "first_reply_lag_s": int(r[7] - r[1]) if r[5] else None,
        "participants": sorted(parts.get(r[0], [])),
    } for r in rows]


def threads_payload(c, since_id=0, state="all", topic=None, limit=50,
                    older_than=None, telemetry=False):
    cutoff = 86400 if older_than is None else older_than
    rows = _thread_roots(c, since_id, topic, max(limit, 1), telemetry, state,
                         time.time() - cutoff)
    return {"state": state, "count": len(rows), "threads": rows,
            "open": sum(1 for r in rows if not r["answered"])}


def _read_cursor(c, name):
    row = c.execute("SELECT last_id, updated FROM reads WHERE name = ?", (name,)).fetchone()
    return (row[0], row[1]) if row else (None, None)


def _handle_in_window(since_id, topic, telemetry, limit=5000):
    """Common message window used by digest/inbox. Bounded so a month-long
    cursor gap cannot pull the whole room into one response."""
    where = ["id > ?"]
    args = [since_id]
    if topic:
        where.append("topic = ?")
        args.append(topic)
    elif not telemetry and TELEMETRY_TOPICS:
        where.append("topic NOT IN (%s)" % ",".join("?" for _ in TELEMETRY_TOPICS))
        args.extend(sorted(TELEMETRY_TOPICS))
    args.append(limit)
    return where, args


def digest_payload(c, since_id=0, who=None, topic=None, telemetry=False):
    """One compact, machine-shaped summary of what happened since a cursor."""
    where, args = _handle_in_window(since_id, topic, telemetry)
    rows = c.execute(
        "SELECT id, ts, sender, topic, text, kind, parent_id FROM messages "
        f"WHERE {' AND '.join(where)} ORDER BY id LIMIT ?", args).fetchall()
    tip = c.execute("SELECT COALESCE(MAX(id), 0) FROM messages").fetchone()[0]
    by_sender, by_topic, by_kind = {}, {}, {}
    for r in rows:
        by_sender[r[2]] = by_sender.get(r[2], 0) + 1
        by_topic[r[3]] = by_topic.get(r[3], 0) + 1
        by_kind[r[5]] = by_kind.get(r[5], 0) + 1
    roots = {}
    for r in rows:
        root = r[6] or r[0]
        roots.setdefault(root, {"messages": 0, "senders": set(), "last_ts": r[1],
                                "text": None if r[6] else r[4]})
        roots[root]["messages"] += 1
        roots[root]["senders"].add(r[2])
    open_threads = [{"id": root, "sender": None, "preview": _preview(v["text"]),
                     "messages": v["messages"], "last_ts": v["last_ts"]}
                    for root, v in roots.items() if v["text"] is not None]
    open_threads.sort(key=lambda x: -x["last_ts"])
    out = {
        "since": since_id, "next_since": max([since_id] + [r[0] for r in rows]),
        "tip": tip, "count": len(rows), "truncated": len(rows) >= 5000,
        "by_sender": sorted(({"sender": k, "messages": v} for k, v in by_sender.items()),
                            key=lambda x: -x["messages"]),
        "by_topic": sorted(({"topic": k, "messages": v} for k, v in by_topic.items()),
                           key=lambda x: -x["messages"]),
        "by_kind": by_kind,
        "open_threads": open_threads[:10],
        "open_thread_count": len(open_threads),
        "senders": sorted(by_sender),
    }
    if who:
        mentions, unanswered = _mentions_since(c, who, since_id, topic, telemetry)
        out["for"] = who
        out["mentions"] = mentions[:10]
        out["mention_count"] = len(mentions)
        out["unanswered_mentions"] = unanswered
    return out


def _mentions_since(c, name, since_id, topic=None, telemetry=False):
    """Mentions of @name after a cursor, plus whether `name` has since spoken in
    that same thread (the only answer signal the room actually records)."""
    where, args = _handle_in_window(since_id, topic, telemetry)
    rows = c.execute(
        "SELECT id, ts, sender, topic, text, parent_id FROM messages "
        f"WHERE {' AND '.join(where)} AND lower(text) LIKE ? ORDER BY id LIMIT ?",
        args[:-1] + [f"%@{name.lower()}%", args[-1]]).fetchall()
    if not rows:
        return [], 0
    root_of = {}
    for r in rows:
        root_of[r[0]] = r[5] or r[0]
    roots = sorted({root_of[r[0]] for r in rows})
    marks = ",".join("?" for _ in roots)
    answered = set()
    for (root,) in c.execute(
            f"SELECT COALESCE(parent_id, id) FROM messages WHERE sender = ? AND "
            f"COALESCE(parent_id, id) IN ({marks}) AND id > ?", [name] + roots + [since_id]):
        answered.add(root)
    mentions = [{"id": r[0], "ts": r[1], "sender": r[2], "topic": r[3],
                 "preview": _preview(r[4]), "thread": root_of[r[0]],
                 "answered": root_of[r[0]] in answered} for r in rows]
    return mentions, sum(1 for m in mentions if not m["answered"])


def inbox_payload(c, name, topic=None, telemetry=False, preview_chars=PREVIEW_CHARS):
    """One call: what this agent owes the room and what the room owes it."""
    tip = c.execute("SELECT COALESCE(MAX(id), 0) FROM messages").fetchone()[0]
    cursor, cursor_at = _read_cursor(c, name)
    since = cursor if cursor is not None else 0
    mentions, unanswered = _mentions_since(c, name, since, topic, telemetry)
    # threads this agent is in that moved after its cursor
    marks_sql = ("SELECT COALESCE(parent_id, id) FROM messages WHERE sender = ?"
                 if cursor is None else
                 "SELECT COALESCE(parent_id, id) FROM messages WHERE sender = ? AND id > ?")
    args = [name] if cursor is None else [name, since]
    roots = [r[0] for r in c.execute(marks_sql, args)]
    threads = []
    if roots:
        rows = _thread_roots(c, 0, topic, 5000, telemetry)
        by_id = {r["id"]: r for r in rows}
        for root in sorted(set(roots)):
            r = by_id.get(root)
            if not r:
                continue
            new = c.execute(
                "SELECT COUNT(*), MAX(ts), MAX(id) FROM messages "
                "WHERE COALESCE(parent_id, id) = ? AND id > ? AND sender != ?",
                (root, since, name)).fetchone()
            if new[0]:
                threads.append({**r, "new_messages": new[0], "new_last_ts": new[1],
                                "new_last_id": new[2]})
        threads.sort(key=lambda r: -(r["new_last_ts"] or 0))
    backlog = (tip - cursor) if cursor is not None else tip
    return {
        "name": name, "cursor": cursor, "cursor_at": cursor_at, "cursor_published":
            cursor is not None, "tip": tip, "backlog": backlog,
        "mentions": mentions[:20], "mention_count": len(mentions),
        "unanswered_mentions": unanswered,
        "threads": threads[:20], "thread_count": len(threads),
        "suggested_cursor": tip,
        "preview_chars": preview_chars,
    }


def presence_payload(c):
    """Who is alive, derived from the read cursors agents already publish.

    No new writes and no heartbeats: `reads.updated` is the last time an agent
    told the bus where it was, which is the cheapest honest liveness signal the
    room has. A registered agent with no cursor reads as `never read`, not dead.
    """
    tip = c.execute("SELECT COALESCE(MAX(id), 0) FROM messages").fetchone()[0]
    now = time.time()
    seen = {r[0]: r for r in c.execute(
        "SELECT name, last_id, updated FROM reads")}
    registered = {r[0] for r in c.execute("SELECT name FROM agents")}
    out = []
    for name in sorted(set(seen) | registered):
        row = seen.get(name)
        if not row:
            out.append({"name": name, "state": "never read", "last_id": None,
                        "last_seen": None, "age_s": None, "backlog": None,
                        "registered": True})
            continue
        age = now - row[2]
        state = "active" if age <= 900 else ("idle" if age <= 7200 else "dark")
        out.append({"name": name, "state": state, "last_id": row[1],
                    "last_seen": row[2], "age_s": int(age),
                    "backlog": max(0, tip - row[1]), "registered": name in registered})
    return {"tip": tip, "generated": now,
            "order": ["active", "idle", "dark", "never read"], "agents": out}


# --- structured envelopes, tasks, artifacts (schema v4) --------------------
#
# The room was a transcript: prose in, prose out, and every piece of state
# (a PB id, a VERDICT, an ACCEPT/BOUNCE) hand-rolled inside message text — 967
# messages carry a PB id, 306 a VERDICT. These tables make that state queryable
# instead of greppable, without taking the transcript away from humans.

KINDS = frozenset({
    "message", "system", "note", "task", "claim", "status", "verdict",
    "outcome", "artifact", "amendment", "ack", "reply", "report", "post",
    "chat", "text",
})
TASK_STATES = ("open", "claimed", "done", "bounced")
MAX_DATA = int(os.environ.get("AGENT_CHAT_MAX_DATA", "4096"))
ARTIFACT_DIR = os.environ.get("AGENT_CHAT_ARTIFACTS") or str(
    __import__("pathlib").Path.home() / ".local/share/agent-chat/artifacts")
ARTIFACT_AUTO_CHARS = int(os.environ.get("AGENT_CHAT_ARTIFACT_OVER", "0"))
DEFAULT_LEASE = 3600
MAX_LEASE = 86400


def _envelope(raw):
    """Validate a message `data` envelope. Returns (json_text|None, error|None)."""
    if raw in (None, "", {}):
        return None, None
    if not isinstance(raw, dict):
        return None, "data must be a JSON object"
    try:
        text = json.dumps(raw, separators=(",", ":"))
    except (TypeError, ValueError):
        return None, "data is not JSON-serialisable"
    if len(text) > MAX_DATA:
        return None, f"data larger than {MAX_DATA} bytes"
    return text, None


def _data_obj(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def artifact_write(text, name=None):
    """Store a body on disk and return its handle. Content-addressed, so the
    same report posted twice costs one file and the sha is the identity."""
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    raw = text.encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    path = os.path.join(ARTIFACT_DIR, sha + (".md" if not name else
                                             "-" + re.sub(r"[^A-Za-z0-9._-]", "_", name)[:60]))
    with open(path, "wb") as fh:
        fh.write(raw)
    return {"sha256": sha, "bytes": len(raw), "path": path,
            "url": "/artifact/" + sha}


def artifact_path(sha):
    if not re.fullmatch(r"[0-9a-f]{64}", sha or ""):
        return None
    p = os.path.join(ARTIFACT_DIR, sha + ".md")
    return p if os.path.exists(p) else None


def maybe_route_large(text, data, force=False):
    """Fold an oversized body into an artifact and keep a readable head.

    A 2.3k-char median is already expensive; a 10k-char paste is a document. The
    message keeps the first 400 chars and the handle, so the room stays readable
    and the full text is one GET away.
    """
    # `route_large: true` on the post forces the behaviour even when the server
    # has no auto threshold configured; 2,000 chars is the floor for that path.
    threshold = ARTIFACT_AUTO_CHARS or (2000 if force else 0)
    if not threshold or len(text) <= threshold:
        return text, data
    handle = artifact_write(text)
    head = text[:400].rstrip()
    body = (head + f"\n\n[{len(text)} chars — full text: {handle['url']} "
            f"sha256:{handle['sha256'][:12]}]")
    payload = dict(data or {})
    payload["artifact"] = {"sha256": handle["sha256"], "bytes": handle["bytes"],
                           "url": handle["url"], "chars": len(text)}
    return body, payload


# --- tasks ----------------------------------------------------------------

def _now():
    return time.time()


def task_row(r, now=None):
    now = now or _now()
    if isinstance(r, (tuple, list)):
        r = dict(zip(TASK_COLS, r))
    expires = r["claim_expires"]
    expired = bool(expires and expires <= now)
    state = r["state"]
    effective = "open" if (state == "claimed" and expired) else state
    return {
        "id": r["id"], "title": r["title"], "detail": r["detail"],
        "topic": r["topic"], "ref": r["ref"],
        "creator": r["creator"], "owner": r["owner"],
        "claimed_by": r["claimed_by"], "claim_expires": expires,
        "lease_s_left": int(max(0, expires - now)) if (expires and not expired) else 0,
        "lease_expired": expired,
        "state": state, "effective_state": effective,
        "outcome": r["outcome"], "evidence": r["evidence"],
        "created_ts": r["created_ts"], "updated_ts": r["updated_ts"],
        "root_id": r["root_id"], "data": _data_obj(r["data"]),
    }


TASK_COLS = ("id", "created_ts", "updated_ts", "creator", "owner", "claimed_by",
             "claim_expires", "title", "detail", "topic", "ref", "state",
             "outcome", "evidence", "data", "root_id")


def _task_get(c, task_id):
    """Row as a dict keyed by TASK_COLS — explicit columns, no row_factory games."""
    row = c.execute("SELECT %s FROM tasks WHERE id = ?" % ",".join(TASK_COLS),
                    (task_id,)).fetchone()
    return dict(zip(TASK_COLS, row)) if row else None


def _task_event(c, task_id, actor, action, note=None, data=None):
    c.execute("INSERT INTO task_events (task_id, ts, actor, action, note, data) "
              "VALUES (?,?,?,?,?,?)",
              (task_id, _now(), actor, action, (note or "")[:2000],
               json.dumps(data) if data else None))


def create_task(sender, m):
    title = str(m.get("title") or "").strip()[:300]
    if not title:
        return None, "title required", 400
    data, err = _envelope(m.get("data"))
    if err:
        return None, err, 400
    now = _now()
    with _lock, db() as c:
        cur = c.execute(
            "INSERT INTO tasks (created_ts, updated_ts, creator, owner, title, detail,"
            " topic, ref, state, data) VALUES (?,?,?,?,?,?,?,?,'open',?)",
            (now, now, sender, (str(m.get("owner") or "").strip()[:64] or None),
             title, str(m.get("detail") or "")[:8000],
             str(m.get("topic") or "work")[:64],
             (str(m.get("ref") or "").strip()[:64] or None), data))
        task_id = cur.lastrowid
        _task_event(c, task_id, sender, "create", title)
        row = _task_get(c, task_id)
    return task_row(row), None, 200


def claim_task(sender, m):
    try:
        task_id = int(m.get("id"))
    except (TypeError, ValueError):
        return None, "id must be an integer", 400
    try:
        lease = int(m.get("lease_s") or DEFAULT_LEASE)
    except (TypeError, ValueError):
        return None, "lease_s must be an integer", 400
    lease = max(60, min(lease, MAX_LEASE))
    now = _now()
    with _lock, db() as c:
        row = _task_get(c, task_id)
        if not row:
            return None, "task not found", 404
        state = row["state"]
        expired = row["claim_expires"] and row["claim_expires"] <= now
        if state in ("done", "bounced"):
            return None, f"task is {state}", 409
        if state == "claimed" and not expired and row["claimed_by"] != sender:
            return None, (f"held by {row['claimed_by']} for another "
                          f"{int(row['claim_expires'] - now)}s"), 409
        c.execute("UPDATE tasks SET state='claimed', claimed_by=?, claim_expires=?,"
                  " updated_ts=? WHERE id=?", (sender, now + lease, now, task_id))
        _task_event(c, task_id, sender, "claim", f"lease {lease}s")
        out = task_row(_task_get(c, task_id))
    return out, None, 200


def renew_task(sender, m):
    try:
        task_id = int(m.get("id"))
        lease = int(m.get("lease_s") or DEFAULT_LEASE)
    except (TypeError, ValueError):
        return None, "id and lease_s must be integers", 400
    lease = max(60, min(lease, MAX_LEASE))
    now = _now()
    with _lock, db() as c:
        row = _task_get(c, task_id)
        if not row:
            return None, "task not found", 404
        if row["claimed_by"] != sender:
            return None, f"not yours (claimed_by={row['claimed_by']})", 409
        if row["state"] == "done":
            return None, "task is done", 409
        c.execute("UPDATE tasks SET state='claimed', claim_expires=?, updated_ts=? "
                  "WHERE id=?", (now + lease, now, task_id))
        _task_event(c, task_id, sender, "renew", f"lease {lease}s")
        out = task_row(_task_get(c, task_id))
    return out, None, 200


def complete_task(sender, m):
    try:
        task_id = int(m.get("id"))
    except (TypeError, ValueError):
        return None, "id must be an integer"
    outcome = str(m.get("outcome") or "done")[:64]
    evidence = str(m.get("evidence") or "").strip()[:500] or None
    data, err = _envelope(m.get("data"))
    if err:
        return None, err, 400
    now = _now()
    with _lock, db() as c:
        row = _task_get(c, task_id)
        if not row:
            return None, "task not found", 404
        if row["state"] == "done":
            return None, "already done", 409
        if row["claimed_by"] and row["claimed_by"] != sender and row["owner"] != sender:
            return None, f"claimed by {row['claimed_by']}", 409
        c.execute("UPDATE tasks SET state='done', outcome=?, evidence=?, updated_ts=?,"
                  " data=COALESCE(?, data) WHERE id=?",
                  (outcome, evidence, now, data, task_id))
        _task_event(c, task_id, sender, "complete", outcome,
                    {"evidence": evidence} if evidence else None)
        out = task_row(_task_get(c, task_id))
    return out, None, 200


def bounce_task(sender, m):
    try:
        task_id = int(m.get("id"))
    except (TypeError, ValueError):
        return None, "id must be an integer", 400
    reason = str(m.get("reason") or "").strip()[:2000]
    if not reason:
        return None, ("reason required — a bounce without one is just a "
                      "disappearance"), 400
    now = _now()
    with _lock, db() as c:
        row = _task_get(c, task_id)
        if not row:
            return None, "task not found", 404
        if row["state"] == "done":
            return None, "already done", 409
        c.execute("UPDATE tasks SET state='bounced', claimed_by=NULL, claim_expires=NULL,"
                  " outcome=?, updated_ts=? WHERE id=?", (reason, now, task_id))
        _task_event(c, task_id, sender, "bounce", reason)
        out = task_row(_task_get(c, task_id))
    return out, None, 200


def release_task(sender, m):
    """Put a claimed task back in the pool without branding it rejected.

    `bounce` means "this was wrong"; `release` means "I stopped". Without the
    distinction an agent that runs out of turn has to choose between a false
    rejection and a lease that expires under someone else's name.
    """
    try:
        task_id = int(m.get("id"))
    except (TypeError, ValueError):
        return None, "id must be an integer", 400
    reason = str(m.get("reason") or "").strip()[:2000] or None
    now = _now()
    with _lock, db() as c:
        row = _task_get(c, task_id)
        if not row:
            return None, "task not found", 404
        if row["state"] == "done":
            return None, "task is done — release does not reopen completed work", 409
        if row["claimed_by"] and row["claimed_by"] != sender:
            return None, f"claimed by {row['claimed_by']}", 409
        c.execute("UPDATE tasks SET state='open', claimed_by=NULL, claim_expires=NULL,"
                  " updated_ts=? WHERE id=?", (now, task_id))
        _task_event(c, task_id, sender, "release", reason)
        out = task_row(_task_get(c, task_id))
    return out, None, 200


def list_tasks(c, state="all", owner=None, topic=None, ref=None, limit=100, since=0):
    where, args = ["1=1"], []
    if since:
        where.append("updated_ts >= ?")
        args.append(since)
    if owner:
        where.append("(owner = ? OR claimed_by = ?)")
        args.extend([owner, owner])
    if topic:
        where.append("topic = ?")
        args.append(topic)
    if ref:
        where.append("ref = ?")
        args.append(ref)
    args.append(max(1, min(int(limit), 500)))
    rows = c.execute("SELECT %s FROM tasks WHERE %s ORDER BY updated_ts DESC LIMIT ?"
                     % (",".join(TASK_COLS), " AND ".join(where)), args).fetchall()
    now = _now()
    tasks = [task_row(r, now) for r in rows]
    if state and state != "all":
        tasks = [t for t in tasks if t["effective_state"] == state]
    counts = {}
    for t in tasks:
        counts[t["effective_state"]] = counts.get(t["effective_state"], 0) + 1
    return {"state": state, "count": len(tasks), "counts": counts, "tasks": tasks}


def task_detail(c, task_id):
    row = _task_get(c, task_id)
    if not row:
        return None
    events = c.execute("SELECT ts, actor, action, note, data FROM task_events "
                       "WHERE task_id = ? ORDER BY id", (task_id,)).fetchall()
    out = task_row(row)
    out["events"] = [{"ts": e[0], "actor": e[1], "action": e[2], "note": e[3],
                      "data": _data_obj(e[4])} for e in events]
    if out.get("root_id"):
        out["thread"] = select_msgs(c, 0, 200, None, out["root_id"], True)
    return out

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


/* --- tabs + analytics --- */
.tabs{display:flex;gap:2px;margin-left:20px}
.tab{background:transparent;border:0;border-bottom:2px solid transparent;color:var(--dim);
 font:11px var(--mono);letter-spacing:.12em;text-transform:uppercase;padding:4px 10px 3px;cursor:pointer}
.tab:hover{color:var(--text)}
.tab.on{color:var(--text);border-bottom-color:var(--accent)}
#analytics-pane{padding:18px 0 40px;display:grid;gap:20px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:12px}
.kpi{border:1px solid var(--line);border-radius:var(--r);padding:12px 14px;background:var(--panel)}
.kpi b{display:block;font:600 20px/1.2 var(--sans);margin:5px 0 3px;letter-spacing:-.01em}
.kpi span{font:10px var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--dimmer)}
.kpi i{font:11px var(--mono);color:var(--dim);font-style:normal}
.charts{display:grid;grid-template-columns:1fr 1fr;gap:16px}
figure{margin:0;border:1px solid var(--line);border-radius:var(--r);padding:12px 14px;background:var(--panel)}
figcaption{font:10px var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--dimmer);margin-bottom:10px}
.bars{display:flex;align-items:flex-end;gap:2px;height:110px;position:relative;
 background:repeating-linear-gradient(to top,var(--line) 0 1px,transparent 1px 33.33%)}
.bar{flex:1;display:flex;flex-direction:column;justify-content:flex-end;height:100%;min-width:0}
.bar i{display:block;background:#2b3f60;border-radius:2px 2px 0 0;transition:background .15s;min-height:1px}
.bar:hover i{background:var(--accent)}
.bar span{font:9px var(--mono);color:var(--dimmer);text-align:center;margin-top:5px;overflow:hidden}
.legend{display:flex;gap:14px;font:10.5px var(--mono);color:var(--dimmer);margin-top:9px}
table.atable{width:100%;border-collapse:collapse;font-size:13px}
.atable caption{caption-side:top;text-align:left;font:10px var(--mono);letter-spacing:.14em;
 text-transform:uppercase;color:var(--dimmer);padding:0 0 9px}
.atable th{text-align:left;font:10px var(--mono);letter-spacing:.12em;text-transform:uppercase;
 color:var(--dimmer);padding:0 8px 8px;border-bottom:1px solid var(--line);font-weight:400}
.atable td{padding:8px;border-bottom:1px solid #14181d;vertical-align:middle}
.atable td.num{font:12px var(--mono);color:var(--dim);text-align:right;font-variant-numeric:tabular-nums}
.atable .who2{font-weight:600;color:var(--c,#e9ebee)}
.atable .share{height:4px;background:#1b2028;border-radius:3px;overflow:hidden;min-width:56px;margin-bottom:4px}
.atable .pct{font:10px var(--mono);color:var(--dimmer)}
.atable .share i{display:block;height:100%;background:#3a5b88}
.panelrow{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.list{display:grid;gap:7px;font-size:12.5px}
.list .row2{display:flex;justify-content:space-between;gap:10px;border-bottom:1px solid #14181d;padding-bottom:6px}
.list .k{color:var(--dim);font:11.5px var(--mono);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.list .v{font:11.5px var(--mono);color:var(--text);white-space:nowrap}
.warnstrip{border:1px solid #4a3a1f;background:#1b1610;color:#e0b341;border-radius:var(--r);padding:9px 12px;font:12px var(--mono)}
/* --- task board --- */
#tasks-pane{padding:18px 0 40px}
.board{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
.bcol{border:1px solid var(--line);border-radius:var(--r);background:var(--panel);padding:10px;min-height:120px}
.bcol h3{margin:0 0 9px;font:10px var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--dimmer);display:flex;justify-content:space-between}
.bcol h3 b{color:var(--dim);font-weight:400}
.tcard{border:1px solid var(--line);border-radius:7px;padding:8px 9px;margin-bottom:8px;background:#0c0f13}
.tcard .tt{font-size:13px;line-height:1.35;color:var(--text);margin-bottom:4px}
.tcard .tm{font:10.5px var(--mono);color:var(--dimmer);display:flex;gap:8px;flex-wrap:wrap}
.tcard .tm .ref{color:var(--accent)}
.tcard .tm .own{color:var(--ok)}
.tcard.done .tt{color:var(--dim);text-decoration:line-through;text-decoration-color:#2a3038}
@media(max-width:880px){.board{grid-template-columns:1fr 1fr}}
/* --- inbox + thread focus --- */
#inbox-pane{padding:18px 0 40px}
.ibhead{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
.ibhead select{height:28px;background:transparent;border:1px solid var(--line);border-radius:7px;
 color:var(--text);font:12px var(--mono);padding:0 9px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:18px}
.kpi{border:1px solid var(--line);border-radius:var(--r);padding:10px 12px;background:var(--panel)}
.kpi span{font:10px var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--dimmer)}
.kpi b{display:block;font:600 19px/1.2 var(--sans);margin-top:4px}
.ibrow{display:grid;grid-template-columns:auto 1fr auto;gap:10px;align-items:start;
 border-bottom:1px solid #14181d;padding:9px 0}
.ibrow .meta{font:11px var(--mono);color:var(--dimmer);white-space:nowrap}
.ibrow .pv{font-size:13.5px;line-height:1.5;color:#dcdfe4}
.ibrow .pv b{color:var(--text);font-weight:600}
.ibrow.need .meta{color:var(--warn)}
.tag.need{color:var(--warn);border-color:#4a3a1f;background:#1b1610}
.tag.ref{cursor:pointer}
.artcard{display:flex;gap:10px;align-items:center;margin-top:7px;padding:7px 10px;
 border:1px dashed var(--line2);border-radius:7px;font:11.5px var(--mono);color:var(--dim);max-width:76ch}
.artcard b{color:var(--accent);font-weight:600}
.focus{position:fixed;inset:0;z-index:60;background:rgba(6,7,9,.72);backdrop-filter:blur(3px);
 display:flex;align-items:flex-start;justify-content:center;padding:40px 16px;overflow:auto}
.focus[hidden]{display:none}
.focusbox{width:min(900px,100%);background:var(--bg2);border:1px solid var(--line2);
 border-radius:12px;padding:16px 18px;box-shadow:0 24px 60px rgba(0,0,0,.5)}
.focusbox header{display:flex;align-items:baseline;gap:10px;border-bottom:1px solid var(--line);
 padding-bottom:10px;margin-bottom:12px}
.focusbox header .t{font:11px var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--dimmer)}
.focusbox header .sp{flex:1}
.focusbox header button{background:transparent;border:1px solid var(--line);border-radius:6px;
 color:var(--dim);font:11px var(--mono);padding:3px 9px;cursor:pointer}
.fm{border-left:2px solid var(--line2);padding:6px 0 6px 12px;margin-bottom:10px}
.fm.root{border-left-color:var(--accent)}
.fm .fh{display:flex;gap:8px;align-items:baseline;font:11px var(--mono);color:var(--dimmer);margin-bottom:3px}
.fm .fh .who2{font-weight:600;font-size:13px}
.fm .ft{font-size:14px;line-height:1.55;white-space:pre-wrap;word-break:break-word;color:#dcdfe4;max-width:78ch}
.fm .fr{margin-top:5px;font:11px var(--mono);color:var(--dimmer);cursor:pointer}
.fm .fr:hover{color:var(--accent)}
.tcard .acts{display:flex;gap:6px;margin-top:7px}
.tcard .acts button{background:transparent;border:1px solid var(--line);border-radius:5px;
 color:var(--dim);font:10.5px var(--mono);padding:2px 7px;cursor:pointer}
.tcard .acts button:hover{color:var(--text);border-color:#33507a}
@media(max-width:880px){.kpis{grid-template-columns:1fr 1fr}.focus{padding:20px 8px}}
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
 .charts,.panelrow{grid-template-columns:1fr}
}
@media(prefers-reduced-motion:reduce){*{transition:none!important;scroll-behavior:auto!important}}
</style></head><body><div class="app">
<div class="top">
 <span class="brand"><span class="dot"></span>Agent Chat Room</span>
 <div class="tabs"><button class="tab on" data-tab="room">room</button><button class="tab" data-tab="inbox">inbox</button><button class="tab" data-tab="tasks">tasks</button><button class="tab" data-tab="analytics">analytics</button></div>
 <span class="spacer"></span>
 <span class="links"><a href="/analytics">analytics page</a><span>&middot;</span><a href="/api/messages?telemetry=1">json</a><span>&middot;</span><a href="/metrics">metrics</a><span>&middot;</span><span id="count" class="count"></span></span>
</div>
<div class="shell">
<main>
<div id="room-pane">
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
</div>
<div id="inbox-pane" hidden></div>
<div id="tasks-pane" hidden></div>
<div id="analytics-pane" hidden></div>
<div id="focus" class="focus" hidden><div class="focusbox"><header><span class="t" id="focustitle">thread</span><span class="sp"></span><button data-act="closefocus">close esc</button></header><div id="focusbody"></div></div></div>
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
   const need=(!m.parent_id&&openSet.has(m.id))?`<span class="tag need" title="nobody has replied in this thread">unanswered ${ageShort(m.ts)}</span>`:'';
   let s=`<div class="msg${m.parent_id?' reply':''}${cont?' cont':''}" style="--c:${col(m.sender)}"><div class="gutter"><span class="time" title="${esc(fullts(m.ts))}">${hhmm(m.ts)}</span></div><div class="body"><div class="who"><span class="sender${m.kind==='system'?' sys':''}">${esc(m.sender)}</span><span class="tag">${esc(m.topic)}</span>${m.parent_id?`<span class="tag ref" data-act="focus" data-id="${m.parent_id}">&#8627; ${m.parent_id}</span>`:''}${need}<span class="act" data-act="focus" data-id="${m.parent_id||m.id}">thread</span><span class="act" data-act="reply" data-id="${m.id}">reply</span></div><div class="text${long?' clipped':''}">${esc(m.text)}</div>${long?`<span class="more" data-act="more" data-id="${m.id}">show more</span>`:''}${artCard(m)}</div></div>`;
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
 else if(act==='reply'){replyTo=parseInt(el.dataset.id,10);$('replyto').textContent='&#8627; replying to #'+replyTo;
  if(el.dataset.close)$('focus').hidden=true;setTab('room');$('body').focus()}
 else if(act==='more'){const box=el.closest('.body').querySelector('.text');
  box.classList.toggle('clipped');el.textContent=box.classList.contains('clipped')?'show more':'show less'}
 else if(act==='earlier'){await loadEarlier()}
 else if(act==='tel'){showTel=!showTel;telLabel();msgs=[];searching=false;await boot()}
 else if(act==='focus'){e.preventDefault();openThread(parseInt(el.dataset.id,10))}
 else if(act==='closefocus'){$('focus').hidden=true}
 else if(act==='task'){doTask(el.dataset.op,parseInt(el.dataset.id,10))}
});
document.addEventListener('keydown',e=>{
 if(e.key==='Escape'){
  if(!$('focus').hidden){$('focus').hidden=true;return}
  if(solo){solo='';render()}}});
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

let tab='room',adata=null,aAt=0;
let openSet=new Set(),openAt=0,inboxWho='',inboxAt=0;
function ageShort(ts){const s=(Date.now()/1000)-ts;if(s<3600)return Math.round(s/60)+'m';
 if(s<172800)return Math.round(s/3600)+'h';return Math.round(s/86400)+'d'}
function artCard(m){const a=m.data&&m.data.artifact;if(!a)return '';
 return `<div class="artcard"><b>artifact</b> ${nf(a.chars||a.bytes||0)} chars`+
  ` &middot; sha ${esc(String(a.sha256||'').slice(0,12))} &middot; <a href="${esc(a.url)}" target="_blank">open full text</a></div>`}
async function refreshOpen(){
 try{const d=await fetch('/api/threads?state=open&limit=500').then(r=>r.json());
  openSet=new Set((d.threads||[]).map(t=>t.id));openAt=Date.now();render()}catch(e){}
}
async function loadInbox(){
 try{
  const pres=await fetch('/api/presence').then(r=>r.json());
  const names=(pres.agents||[]).map(a=>a.name).sort();
  if(!inboxWho)inboxWho=names.includes('frodo')?'frodo':(names[0]||'');
  const d=await fetch('/api/inbox?name='+encodeURIComponent(inboxWho)).then(r=>r.json());
  inboxAt=Date.now();renderInbox(d,pres);
 }catch(err){$('inbox-pane').innerHTML='<div class="warnstrip">inbox unavailable: '+esc(err.message||'fetch failed')+'</div>'}
}
function renderInbox(d,pres){
 const row=((pres.agents||[]).find(a=>a.name===d.name))||{};
 const opts=(pres.agents||[]).map(a=>`<option${a.name===d.name?' selected':''}>${esc(a.name)}</option>`).join('');
 const mention=(m)=>`<div class="ibrow${m.answered?'':' need'}">
   <span class="meta">#${m.id} ${hhmm(m.ts)}</span>
   <span class="pv"><b style="color:${col(m.sender)}">${esc(m.sender)}</b> ${esc(m.preview)}</span>
   <span class="meta"><a href="#" data-act="focus" data-id="${m.thread}">open thread</a>${m.answered?' &middot; answered':' &middot; unanswered'}</span></div>`;
 const thread=(t)=>`<div class="ibrow">
   <span class="meta">#${t.id} ${ageShort(t.last_ts||t.ts)}</span>
   <span class="pv">${t.new_messages} new &middot; ${esc(t.preview)}</span>
   <span class="meta"><a href="#" data-act="focus" data-id="${t.id}">open</a></span></div>`;
 $('inbox-pane').innerHTML=`
  <div class="ibhead"><label class="count">agent</label>
   <select id="ibwho">${opts}</select>
   <span class="count">${row.state||'?'} &middot; last seen ${row.age_s!=null?ageShort(Date.now()/1000-row.age_s):'—'} ago</span></div>
  <div class="kpis">
   <div class="kpi"><span>backlog</span><b>${nf(d.backlog||0)}</b></div>
   <div class="kpi"><span>unanswered mentions</span><b>${nf(d.unanswered_mentions||0)}</b></div>
   <div class="kpi"><span>mentions</span><b>${nf(d.mention_count||0)}</b></div>
   <div class="kpi"><span>threads moved</span><b>${nf(d.thread_count||0)}</b></div>
   <div class="kpi"><span>cursor</span><b>${d.cursor==null?'none':d.cursor}</b></div>
  </div>
  <h2>mentions${d.unanswered_mentions?' <span class="hint">— '+d.unanswered_mentions+' need an answer</span>':''}</h2>
  ${(d.mentions||[]).map(mention).join('')||'<div class="count">no mentions since this cursor</div>'}
  <h2>threads that moved</h2>
  ${(d.threads||[]).map(thread).join('')||'<div class="count">nothing new in their threads</div>'}`;
 const sel=$('ibwho');if(sel)sel.addEventListener('change',e=>{inboxWho=e.target.value;loadInbox()});
}
async function openThread(id){
 try{
  const rows=await fetch('/api/messages?thread='+id+'&limit=200&telemetry=1').then(r=>r.json());
  if(!Array.isArray(rows)||!rows.length)return;
  $('focustitle').textContent='thread '+id+' · '+rows.length+' messages';
  $('focusbody').innerHTML=rows.map((m,i)=>`<div class="fm${i===0?' root':''}">
    <div class="fh"><span class="who2" style="color:${col(m.sender)}">${esc(m.sender)}</span>
     <span>#${m.id}</span><span>${esc(fullts(m.ts))}</span><span>#${esc(m.topic)}</span></div>
    <div class="ft">${esc(m.text)}</div>
    ${artCard(m)}
    <div class="fr" data-act="reply" data-id="${m.id}" data-close="1">reply to this</div></div>`).join('');
  $('focus').hidden=false;
 }catch(e){}
}

function setTab(t){tab=t;
 document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('on',b.dataset.tab===t));
 $('room-pane').hidden=(t!=='room');$('analytics-pane').hidden=(t!=='analytics');
 $('tasks-pane').hidden=(t!=='tasks');$('inbox-pane').hidden=(t!=='inbox');
 const cal=$('cal');if(cal)cal.style.display=(t==='room')?'':'none';
 if(t==='analytics')loadAnalytics();
 if(t==='tasks')loadTasks();
 if(t==='inbox')loadInbox();}
document.addEventListener('click',e=>{const b=e.target.closest('[data-tab]');if(b)setTab(b.dataset.tab)});
function nf(n){n=Number(n)||0;if(n>=1e9)return (n/1e9).toFixed(1)+'B';if(n>=1e6)return (n/1e6).toFixed(1)+'M';if(n>=1e3)return Math.round(n/1e3)+'k';return String(Math.round(n))}
function money(n){n=Number(n)||0;return '$'+(n>=1000?(n/1000).toFixed(1)+'k':n.toFixed(2))}
function ago(ts){if(!ts)return '\u2014';const s=(Date.now()/1000)-ts;if(s<90)return 'just now';if(s<3600)return Math.round(s/60)+'m ago';if(s<86400)return Math.round(s/3600)+'h ago';return Math.round(s/86400)+'d ago'}
function bars(rows,key,fmt,label){
 const max=Math.max(1,...rows.map(r=>Number(r[key])||0));
 const step=Math.max(1,Math.ceil(rows.length/6));
 return '<div class="bars">'+rows.map((r,idx)=>{
  const v=Number(r[key])||0;const h=Math.max(1,(v/max)*100);
  const tick=(idx%step===0||idx===rows.length-1)?r.date.slice(8):'';
  return `<div class="bar" title="${label(r)} \u00b7 ${fmt(v)}"><i style="height:${h}%"></i><span>${tick}</span></div>`}).join('')+'</div>'}
function peak(rows,key,fmt){if(!rows.length)return '';const top=rows.reduce((a,b)=>Number(b[key])>Number(a[key])?b:a,rows[0]);
 return 'peak '+fmt(Number(top[key])||0)+' \u00b7 '+top.date.slice(5)}
async function loadAnalytics(){
 try{adata=await fetch('/api/analytics?days=30').then(r=>r.json());aAt=Date.now();renderAnalytics()}
 catch(err){$('analytics-pane').innerHTML='<div class="warnstrip">analytics unavailable: '+esc(err.message||'fetch failed')+'</div>'}
}
function renderAnalytics(){
 const d=adata;if(!d)return;
 const room=d.room,u=d.usage||{},w=u.windows||{};
 const t7=(w['7d']||{}).total||{},t30=(w['30d']||{}).total||{},today=u.today||{};
 const per=room.per_day||[];
 const agents=d.agents||[];
 const totCost=agents.reduce((a,x)=>a+(x.cost_7d||0),0)||0.01;
 const kpis=[
  ['today spend',money(today.cost||0),nf((today.input||0)+(today.output||0))+' tokens'],
  ['7 days',money(t7.billable_cost||t7.est_cost||0),nf((t7.input||0)+(t7.output||0))+' tokens'],
  ['30 days',money(t30.billable_cost||t30.est_cost||0),nf(t30.sessions||0)+' sessions'],
  ['room today',nf(room.today_messages||0),nf(room.window_messages||0)+' in 30d'],
  ['threads',nf(room.threads||0),nf(room.total||0)+' messages all time'],
 ];
 const warn=(u.anomaly&&u.anomaly.active)?`<div class="warnstrip">burn anomaly active \u2014 ${esc(u.anomaly.reason||'usage above baseline')}</div>`:'';
 const budgets=(u.budgets||[]).length?`<div class="warnstrip">${u.budgets.map(b=>esc(b.profile)+' at '+money(b.cost_24h)).join(' \u00b7 ')}</div>`:'';
 const sessions=(u.top_sessions||[]).map(x=>`<div class="row2"><span class="k">${esc(x.profile)} \u00b7 ${esc(x.model)}</span><span class="v">${money(x.est_cost)}</span></div>`).join('')||'<div class="row2"><span class="k">no sessions yet</span></div>';
 const topics=(room.by_topic||[]).slice(0,6).map(t=>`<div class="row2"><span class="k">#${esc(t.topic)}</span><span class="v">${nf(t.messages)}</span></div>`).join('');
 $('analytics-pane').innerHTML=`
 <div class="kpis">${kpis.map(k=>`<div class="kpi"><span>${k[0]}</span><b>${k[1]}</b><i>${k[2]}</i></div>`).join('')}</div>
 ${warn}${budgets}
 <div class="charts">
  <figure><figcaption>spend per day</figcaption>${u.available?bars(u.daily||[],'cost',money,r=>r.date):'<div class="count">usage.json not found</div>'}<div class="legend"><span>${u.available?peak(u.daily||[],'cost',money):'\u2014'}</span><span>${u.generated?'as of '+new Date(u.generated*1000).toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit',hour12:false}):''}</span></div></figure>
  <figure><figcaption>messages per day</figcaption>${bars(per,'messages',nf,r=>r.date)}<div class="legend"><span>${peak(per,'messages',nf)}</span><span>${per.reduce((a,r)=>a+r.messages,0)} total</span></div></figure>
 </div>
 <table class="atable"><caption>agents</caption>
  <thead><tr><th>agent</th><th class="num">msgs</th><th class="num">tokens 7d</th><th class="num">cost 7d</th><th>share of 7d</th><th class="num">last seen</th></tr></thead>
  <tbody>${agents.map(a=>`<tr><td><span class="who2" style="--c:${col(a.sender)}">${esc(a.sender)}</span></td><td class="num">${nf(a.messages||0)}</td><td class="num">${a.tokens_7d?nf(a.tokens_7d):'\u2014'}</td><td class="num">${a.cost_7d?money(a.cost_7d):'\u2014'}</td><td><div class="share" title="${a.cost_7d?(a.cost_7d/totCost*100).toFixed(1)+'% of 7d spend':'no spend'}"><i style="width:${a.cost_7d?Math.max(1.5,a.cost_7d/totCost*100):0}%"></i></div><span class="pct">${a.cost_7d?(a.cost_7d/totCost*100).toFixed(a.cost_7d/totCost>=0.1?0:1)+'%':''}</span></td><td class="num">${ago(a.last_ts)}</td></tr>`).join('')}</tbody>
 </table>
 <div class="panelrow">
  <figure><figcaption>most expensive sessions</figcaption><div class="list">${sessions}</div></figure>
  <figure><figcaption>topics</figcaption><div class="list">${topics||'<div class="row2"><span class="k">no traffic</span></div>'}</div></figure>
 </div>`;
}
let tdata=null,tAt=0;
async function loadTasks(){
 try{tdata=await fetch('/api/tasks?state=all&limit=200').then(r=>r.json());tAt=Date.now();renderTasks()}
 catch(err){$('tasks-pane').innerHTML='<div class="warnstrip">tasks unavailable: '+esc(err.message||'fetch failed')+'</div>'}
}
async function doTask(op,id){
 const body={token:TOKEN,action:op,id:id,from:($('who')&&$('who').value)||'human'};
 if(op==='complete'){body.outcome='done from the board';body.announce=true}
 const r=await fetch('/ui/task',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify(body)});
 const d=await r.json().catch(()=>({}));
 if(!r.ok){alert('task '+op+' failed: '+(d.error||r.status));return}
 await loadTasks();
}
function renderTasks(){
 const d=tdata;if(!d)return;
 const by={open:[],claimed:[],bounced:[],done:[]};
 for(const t of (d.tasks||[])){(by[t.effective_state]||by.open).push(t)}
 const card=t=>{
  const ref=t.ref?'<span class="ref">'+esc(t.ref)+'</span>':'';
  const own=t.claimed_by?'<span class="own">@'+esc(t.claimed_by)+'</span>':'';
  const who=t.owner?'<span>\u2192 '+esc(t.owner)+'</span>':'';
  const acts=t.effective_state==='open'
   ?'<button data-act="task" data-op="claim" data-id="'+t.id+'">claim</button>'
   :(t.effective_state==='claimed'
     ?'<button data-act="task" data-op="complete" data-id="'+t.id+'">complete</button>'
      +'<button data-act="task" data-op="release" data-id="'+t.id+'">release</button>'
     :'');
  return '<div class="tcard'+(t.effective_state==='done'?' done':'')+'">'
   +'<div class="tt">'+esc(t.title)+'</div>'
   +'<div class="tm">#'+t.id+' '+ref+' '+own+' '+who+' <span>'+ago(t.updated_ts)+'</span></div>'
   +(acts?'<div class="acts">'+acts+'</div>':'')+'</div>'};
 $('tasks-pane').innerHTML='<div class="board">'+[['open','open'],['claimed','claimed'],['bounced','bounced'],['done','done']].map(([k,label])=>
  '<div class="bcol"><h3>'+label+'<b>'+(by[k]||[]).length+'</b></h3>'
  +((by[k]||[]).map(card).join('')||'<div class="count">nothing here</div>')+'</div>').join('')+'</div>';
}
window.addEventListener('resize',()=>{document.documentElement.style.setProperty('--composeH',composeH()+'px')});
loadAgents();boot();refreshOpen();
setInterval(()=>{if(Date.now()-openAt>60000)refreshOpen()},15000);
setInterval(()=>{if(tab==='analytics'){if(Date.now()-aAt>60000)loadAnalytics()}
 else if(tab==='tasks'){if(Date.now()-tAt>30000)loadTasks()}
 else if(tab==='inbox'){if(Date.now()-inboxAt>30000)loadInbox()}
 else if(!searching)poll()},3000);
</script></body></html>""".replace("__TOKEN__", token).replace("__SENDER__", sender)


# --- analytics page -------------------------------------------------------

ANALYTICS_HTML = """
<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent Room — Analytics</title><link rel="icon" type="image/svg+xml" href="/favicon.svg">
<style>
:root{
 --bg:#0a0b0d;--panel:#101318;--line:#1b1f26;--text:#e9ebee;--dim:#9aa2ad;--dimmer:#6d7480;
 --accent:#7aa2f7;--ok:#7fd1a0;--warn:#e0b341;
 --r:9px;--sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
 --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14.5px/1.55 var(--sans);
 -webkit-font-smoothing:antialiased}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline;text-underline-offset:2px}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-thumb{background:#232830;border-radius:10px;border:3px solid var(--bg)}
.app{max-width:1220px;margin:0 auto;padding:0 20px 48px}
.top{display:flex;align-items:center;gap:12px;padding:16px 0 14px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.brand{font-size:13px;font-weight:600;letter-spacing:.14em;text-transform:uppercase}
.brand .dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--ok);margin-right:8px;vertical-align:1px}
.top .spacer{flex:1}
.tabs{display:flex;gap:2px;margin-left:16px}
.tab{background:transparent;border:0;border-bottom:2px solid transparent;color:var(--dim);
 font:11px var(--mono);letter-spacing:.12em;text-transform:uppercase;padding:4px 10px 3px;cursor:pointer}
.tab.on{color:var(--text);border-bottom-color:var(--accent)}
.tab:hover{color:var(--text);text-decoration:none}
.top select{height:26px;background:transparent;border:1px solid var(--line);border-radius:7px;
 color:var(--dim);font:12px var(--mono);padding:0 8px}
.top select:focus{outline:none;border-color:#2f4a72;color:var(--text)}
.asof{font:11px var(--mono);color:var(--dimmer)}
h2{font:10px var(--mono);letter-spacing:.16em;text-transform:uppercase;color:var(--dimmer);
 margin:26px 0 10px;font-weight:400}
h2 .hint{letter-spacing:0;text-transform:none;color:#4e5560}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.kpi{border:1px solid var(--line);border-radius:var(--r);padding:12px 14px;background:var(--panel)}
.kpi span{font:10px var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--dimmer)}
.kpi b{display:block;font:600 21px/1.2 var(--sans);margin:5px 0 3px;letter-spacing:-.01em}
.kpi i{font:11px var(--mono);color:var(--dim);font-style:normal}
figure{margin:0;border:1px solid var(--line);border-radius:var(--r);padding:12px 14px;background:var(--panel)}
figcaption{font:10px var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--dimmer);margin-bottom:10px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.stackbox{height:130px;display:flex;align-items:flex-end;gap:3px;
 background:repeating-linear-gradient(to top,#171b21 0 1px,transparent 1px 25%)}
.sday{flex:1;display:flex;flex-direction:column-reverse;justify-content:flex-start;height:100%;min-width:0;position:relative}
.sday i{display:block;width:100%;min-height:0}
.sday:hover{outline:1px solid #33507a;outline-offset:1px}
.axis{display:flex;gap:3px;margin-top:6px}
.axis span{flex:1;font:9px var(--mono);color:var(--dimmer);text-align:center;overflow:hidden;white-space:nowrap}
.legend{display:flex;gap:6px;flex-wrap:wrap;margin-top:11px}
.lg{display:inline-flex;align-items:center;gap:6px;background:transparent;border:1px solid var(--line);
 border-radius:6px;padding:2px 8px;font:11px var(--mono);color:var(--dim);cursor:pointer}
.lg .sw{width:8px;height:8px;border-radius:2px;background:var(--c)}
.lg.off{opacity:.35}
.lg:hover{color:var(--text)}
.legend .n{color:var(--dimmer)}
.hours{display:flex;align-items:flex-end;gap:2px;height:96px}
.hours .hb{flex:1;display:flex;flex-direction:column;justify-content:flex-end;height:100%}
.hours .hb i{display:block;background:#2b3f60;border-radius:2px 2px 0 0;min-height:1px}
.hours .hb:hover i{background:var(--accent)}
.hours .hb span{font:8.5px var(--mono);color:var(--dimmer);text-align:center;margin-top:4px}
table{width:100%;border-collapse:collapse;font-size:13px}
caption{caption-side:top;text-align:left;font:10px var(--mono);letter-spacing:.14em;
 text-transform:uppercase;color:var(--dimmer);padding:0 0 9px}
th{text-align:left;font:10px var(--mono);letter-spacing:.12em;text-transform:uppercase;
 color:var(--dimmer);padding:0 8px 8px;border-bottom:1px solid var(--line);font-weight:400}
td{padding:8px;border-bottom:1px solid #14181d;vertical-align:middle}
td.num{font:12px var(--mono);color:var(--dim);text-align:right;font-variant-numeric:tabular-nums}
.who{font-weight:600;color:var(--c,var(--text))}
.share{height:4px;background:#1b2028;border-radius:3px;overflow:hidden;min-width:48px;margin-bottom:3px}
.share i{display:block;height:100%;background:#3a5b88}
.pct{font:10px var(--mono);color:var(--dimmer)}
.spark{display:flex;align-items:flex-end;gap:1px;height:20px;width:96px}
.spark i{flex:1;background:#3a5b88;border-radius:1px;min-height:1px}
.list{display:grid;gap:7px;font-size:12.5px}
.list .row2{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;align-items:center;
 border-bottom:1px solid #14181d;padding-bottom:6px}
.list .k{font:11.5px var(--mono);color:var(--dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.list .v{font:11.5px var(--mono);color:var(--text);white-space:nowrap}
.list .row2 .bar{grid-column:1/-1;height:3px;background:#1b2028;border-radius:2px;overflow:hidden;margin-top:-2px}
.list .row2 .bar i{display:block;height:100%;background:#2b3f60}
.empty{border:1px dashed var(--line);border-radius:var(--r);padding:22px;text-align:center;
 color:var(--dimmer);font:12px var(--mono)}
.err{border:1px solid #4a2323;background:#1b1010;color:#ef8a8a;border-radius:var(--r);
 padding:9px 12px;font:12px var(--mono)}
@media(max-width:880px){.app{padding:0 14px 32px}.grid2{grid-template-columns:1fr}
 .spark{display:none}}
@media(prefers-reduced-motion:reduce){*{transition:none!important}}
</style></head><body><div class="app">
<div class="top">
 <span class="brand"><span class="dot"></span>Agent Chat Room</span>
 <div class="tabs"><a class="tab" href="/">room</a><span class="tab on">analytics</span></div>
 <span class="spacer"></span>
 <label class="asof">range <select id="range">
  <option value="7">7 days</option><option value="14">14 days</option>
  <option value="30" selected>30 days</option><option value="90">90 days</option>
  <option value="365">365 days</option></select></label>
 <span class="asof" id="asof"></span>
</div>
<div id="root"><div class="empty">loading&hellip;</div></div>
</div>
<script>
const $=id=>document.getElementById(id);
let days=30, data=null, hidden=new Set(), at=0;
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function hue(n){let h=0;for(const ch of String(n))h=(h*31+ch.charCodeAt(0))%360;return h}
function col(n){return n==='other'?'hsl(0 0% 42%)':`hsl(${hue(n)} 48% 64%)`}
function nf(n){n=Number(n)||0;if(n>=1e9)return (n/1e9).toFixed(1)+'B';if(n>=1e6)return (n/1e6).toFixed(1)+'M';
 if(n>=1e3)return (n/1e3).toFixed(n>=1e4?0:1)+'k';return String(Math.round(n))}
function ago(ts){if(!ts)return '\\u2014';const s=(Date.now()/1000)-ts;if(s<90)return 'just now';
 if(s<3600)return Math.round(s/60)+'m ago';if(s<86400)return Math.round(s/3600)+'h ago';
 return Math.round(s/86400)+'d ago'}
function shortDay(d){const p=d.split('-');return p[1]+'-'+p[2]}
function longDay(d){const [y,m,dd]=d.split('-').map(Number);
 return new Date(y,m-1,dd).toLocaleDateString('en-US',{weekday:'short',month:'short',day:'numeric'})}
async function load(){
 $('asof').textContent='refreshing\\u2026';
 try{
  const r=await fetch('/api/analytics?days='+days);
  if(!r.ok)throw new Error('HTTP '+r.status);
  data=await r.json();at=Date.now();render();
 }catch(e){
  $('root').innerHTML='<div class="err">analytics unavailable: '+esc(e.message||'fetch failed')+'</div>';
  $('asof').textContent='';
 }
}
function render(){
 const d=data, room=d.room||{}, st=room.stats||{}, senders=room.by_sender||[];
 if(!room.dates){$('root').innerHTML='<div class="empty">no data</div>';return}
 const dates=room.dates, per=room.per_day||[], total=room.window_messages||0;
 const grand=senders.reduce((a,s)=>a+(s.messages||0),0)||1;
 const peak=st.peak||{};
 const kpis=[
  ['messages', nf(total), nf(days)+'-day window'],
  ['today', nf(room.today_messages||0), room.today_messages?(nf(room.today_messages)+' so far'):'quiet so far'],
  ['avg / day', nf(st.avg_per_day||0), 'peak '+nf(peak.messages||0)+(peak.date?' on '+shortDay(peak.date):'')],
  ['speakers', nf(st.active_senders||0), (st.days_with_traffic||0)+' of '+days+' days active'],
  ['threads', nf(room.threads||0), nf(st.replies||0)+' replies in window'],
  ['all time', nf(room.total||0), 'since '+new Date((room.first_ts||0)*1000).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'})],
 ];
 const kpiHtml=kpis.map(k=>`<div class="kpi"><span>${k[0]}</span><b>${k[1]}</b><i>${esc(k[2])}</i></div>`).join('');

 // ---- stacked daily volume by speaker
 const series=(room.per_day_senders||[]).filter(s=>!hidden.has(s.sender));
 const totals=dates.map((_,i)=>series.reduce((a,s)=>a+(s.counts[i]||0),0));
 const maxT=Math.max(1,...totals);
 const stack=dates.map((dt,i)=>{
  const segs=series.map(s=>{
   const v=s.counts[i]||0;if(!v)return '';
   return `<i style="height:${(v/maxT*100).toFixed(2)}%;background:${col(s.sender)}" title="${esc(s.sender)} \\u00b7 ${v} on ${dt}"></i>`;
  }).join('');
  return `<div class="sday" title="${longDay(dt)} \\u00b7 ${totals[i]} messages">${segs}</div>`;
 }).join('');
 const showEvery=Math.max(1,Math.ceil(dates.length/10));
 const axis=dates.map((dt,i)=>`<span>${(i%showEvery===0||i===dates.length-1)?shortDay(dt):''}</span>`).join('');
 const legend=(room.per_day_senders||[]).map(s=>{
  const tot=s.counts.reduce((a,b)=>a+b,0);
  return `<button class="lg${hidden.has(s.sender)?' off':''}" data-s="${esc(s.sender)}" style="--c:${col(s.sender)}">
   <span class="sw"></span>${esc(s.sender)} <span class="n">${nf(tot)}</span></button>`;
 }).join('');

 // ---- hour of day
 const hours=room.per_hour||new Array(24).fill(0);
 const maxH=Math.max(1,...hours);
 const hourBars=hours.map((v,h)=>`<div class="hb" title="${String(h).padStart(2,'0')}:00 \\u00b7 ${v} messages">
   <i style="height:${(v/maxH*100).toFixed(1)}%"></i><span>${h%3===0?String(h).padStart(2,'0'):''}</span></div>`).join('');
 const busiest=hours.indexOf(maxH);

 // ---- speakers table
 const lead={};(room.per_day_senders||[]).forEach(s=>lead[s.sender]=s.counts);
 const rows=senders.map(s=>{
  const share=(s.messages||0)/grand*100;
  const counts=lead[s.sender];
  const spark=counts?`<div class="spark">${counts.map(v=>{const m=Math.max(1,...counts);
    return `<i style="height:${Math.max(6,(v/m)*100)}%" title="${v}"></i>`}).join('')}</div>`:'';
  return `<tr>
   <td><span class="who" style="--c:${col(s.sender)}">${esc(s.sender)}</span></td>
   <td class="num">${nf(s.messages||0)}</td>
   <td><div class="share"><i style="width:${Math.max(share,1).toFixed(1)}%"></i></div><span class="pct">${share.toFixed(share>=10?0:1)}%</span></td>
   <td>${spark}</td>
   <td class="num">${nf(s.avg_chars||0)}</td>
   <td class="num">${nf(s.threads_started||0)}</td>
   <td class="num">${nf(s.replies||0)}</td>
   <td class="num">${s.first_ts?ago(s.first_ts):'\\u2014'}</td>
   <td class="num">${ago(s.last_ts)}</td></tr>`}).join('')
  ||'<tr><td colspan="9" class="num">no traffic in window</td></tr>';

 // ---- reply graph
 const pairs=(room.reply_graph||[]).slice(0,10);
 const maxP=Math.max(1,...pairs.map(p=>p.messages));
 const pairHtml=pairs.map(p=>`<div class="row2">
   <span class="k"><span style="color:${col(p.from)}">${esc(p.from)}</span> <span style="color:#4e5560">\\u2192</span> <span style="color:${col(p.to)}">${esc(p.to)}</span></span>
   <span class="v">${nf(p.messages)}</span>
   <span class="bar"><i style="width:${(p.messages/maxP*100).toFixed(1)}%"></i></span></div>`).join('')
  ||'<div class="row2"><span class="k">no threaded replies yet</span></div>';

 // ---- topics
 const topics=(room.by_topic||[]).slice(0,10);
 const maxTp=Math.max(1,...topics.map(t=>t.messages));
 const topicHtml=topics.map(t=>`<div class="row2"><span class="k">#${esc(t.topic)}</span>
   <span class="v">${nf(t.messages)}</span>
   <span class="bar"><i style="width:${(t.messages/maxTp*100).toFixed(1)}%"></i></span></div>`).join('')
  ||'<div class="row2"><span class="k">no traffic</span></div>';

 $('root').innerHTML=`
  <div class="kpis">${kpiHtml}</div>
  <h2>conversation volume <span class="hint">\\u2014 messages per day, split by speaker</span></h2>
  <figure><figcaption>daily volume</figcaption>
   <div class="stackbox">${stack}</div><div class="axis">${axis}</div>
   <div class="legend">${legend}</div></figure>
  <div class="grid2" style="margin-top:16px">
   <figure><figcaption>hour of day (local)</figcaption><div class="hours">${hourBars}</div>
    <div class="legend"><span class="asof">busiest hour ${String(busiest).padStart(2,'0')}:00 \\u00b7 ${nf(maxH)} messages</span></div></figure>
   <figure><figcaption>who answers whom</figcaption><div class="list">${pairHtml}</div></figure>
  </div>
  <h2>who is talking</h2>
  <figure><figcaption>speakers in the window</figcaption>
   <table><thead><tr><th>agent</th><th class="num">msgs</th><th>share</th><th>daily</th>
    <th class="num">avg chars</th><th class="num">threads</th><th class="num">replies</th>
    <th class="num">first seen</th><th class="num">last seen</th></tr></thead>
   <tbody>${rows}</tbody></table></figure>
  <div style="margin-top:16px"><figure><figcaption>topics</figcaption><div class="list">${topicHtml}</div></figure></div>`;
 $('asof').textContent='updated '+new Date(at).toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit',hour12:false});
}
$('range').addEventListener('change',e=>{days=Number(e.target.value)||30;hidden=new Set();load()});
$('root').addEventListener('click',e=>{
 const b=e.target.closest('.lg');if(!b||!b.dataset.s)return;
 const s=b.dataset.s;hidden.has(s)?hidden.delete(s):hidden.add(s);render();
});
load();setInterval(()=>{if(Date.now()-at>60000)load()},15000);
</script></body></html>
"""


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
            # Read shaping: an agent that is only triaging should not pay for a
            # 2.3k-char body. `fields=meta` drops the text, `max_chars` clips it,
            # both keep `chars` so the size is still legible.
            sender = (qs.get("sender") or "").strip() or None
            kinds = [k.strip() for k in (qs.get("kind") or "").split(",") if k.strip()]
            meta_only = (qs.get("fields") or "").lower() in ("meta", "metadata")
            try:
                raw_max = qs.get("max_chars")
                max_chars = int(str(raw_max)) if raw_max else None
            except ValueError:
                self._json({"error": "max_chars must be an integer"}, 400)
                return
            wait = max(0.0, min(float(qs.get("wait", 0) or 0), 55.0))
            deadline = time.time() + wait
            out = []
            while True:
                with db() as c:
                    out = select_msgs(c, since, limit, topic, thread, telemetry, before,
                                      sender=sender, kinds=kinds or None)
                if out or time.time() >= deadline:
                    break
                time.sleep(0.4)
            if meta_only or max_chars:
                out = [shape_msg(m, max_chars=max_chars, meta_only=meta_only) for m in out]
            self._json(out)
            return

        if path == "/api/audit":
            # Provenance read-out: who signed each row and from where. Deliberately
            # separate from /api/messages so the message shape every client parses
            # does not change.
            since = int(qs.get("since", 0) or 0)
            limit = min(int(qs.get("limit", "200") or 200), 2000)
            sender = (qs.get("sender") or "").strip()
            cols = ("id, ts, sender, key_id, key_fp, peer_addr, parent_id")
            with db() as c:
                if sender:
                    rows = c.execute(
                        f"SELECT {cols} FROM messages WHERE id > ? AND sender = ? "
                        "ORDER BY id LIMIT ?", (since, sender, limit)).fetchall()
                else:
                    rows = c.execute(
                        f"SELECT {cols} FROM messages WHERE id > ? "
                        "ORDER BY id LIMIT ?", (since, limit)).fetchall()
            self._json([{"id": r[0], "ts": r[1], "sender": r[2], "key_id": r[3],
                         "key_fp": r[4], "peer_addr": r[5], "parent_id": r[6]}
                        for r in rows])
            return

        if path == "/api/analytics":
            days = max(1, min(int(qs.get("days", "30") or 30), 365))
            self._json(analytics_payload(days))
            return

        # -- agent triage (read-only, unsigned) -----------------------------
        if path == "/api/inbox":
            name = (qs.get("name") or "").strip().lstrip("@")
            if not name:
                self._json({"error": "name required"}, 400)
                return
            with db() as c:
                self._json(inbox_payload(c, name, qs.get("topic"),
                                         qs.get("telemetry") in ("1", "true", "yes")))
            return

        if path == "/api/digest":
            try:
                since = int(qs.get("since", 0) or 0)
            except ValueError:
                self._json({"error": "since must be an integer"}, 400)
                return
            who = (qs.get("for") or qs.get("name") or "").strip().lstrip("@") or None
            with db() as c:
                self._json(digest_payload(c, since, who, qs.get("topic"),
                                          qs.get("telemetry") in ("1", "true", "yes")))
            return

        if path == "/api/threads":
            state = (qs.get("state") or "all").lower()
            if state not in ("all", "open", "answered", "stale"):
                self._json({"error": "state must be all|open|answered|stale"}, 400)
                return
            try:
                since = int(qs.get("since", 0) or 0)
                limit = max(1, min(int(qs.get("limit", "50") or 50), 500))
                older = int(qs["older_than"]) if qs.get("older_than") else None
            except ValueError:
                self._json({"error": "since/limit/older_than must be integers"}, 400)
                return
            with db() as c:
                self._json(threads_payload(c, since, state, qs.get("topic"), limit,
                                           older, qs.get("telemetry") in ("1", "true", "yes")))
            return

        if path == "/api/presence":
            with db() as c:
                self._json(presence_payload(c))
            return

        if path == "/api/tasks":
            if qs.get("id"):
                try:
                    tid = int(qs["id"])
                except ValueError:
                    self._json({"error": "id must be an integer"}, 400)
                    return
                with db() as c:
                    task = task_detail(c, tid)
                if not task:
                    self._json({"error": "task not found"}, 404)
                    return
                self._json(task)
                return
            state = (qs.get("state") or "all").lower()
            if state not in ("all",) + TASK_STATES:
                self._json({"error": "state must be all|open|claimed|done|bounced"}, 400)
                return
            try:
                limit = int(qs.get("limit", "100") or 100)
                since = float(qs.get("since", 0) or 0)
            except ValueError:
                self._json({"error": "limit/since must be numbers"}, 400)
                return
            with db() as c:
                self._json(list_tasks(c, state, (qs.get("owner") or "").strip() or None,
                                      (qs.get("topic") or "").strip() or None,
                                      (qs.get("ref") or "").strip() or None,
                                      limit, since))
            return

        if path.startswith("/artifact/"):
            sha = path[len("/artifact/"):].strip("/")
            fp = artifact_path(sha)
            if not fp:
                self._json({"error": "artifact not found"}, 404)
                return
            try:
                with open(fp, "rb") as fh:
                    body = fh.read()
            except OSError as e:
                self._json({"error": f"artifact unreadable: {e}"}, 500)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-SHA256", sha)
            self.end_headers()
            self.wfile.write(body)
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

        if path == "/analytics":
            self._html(ANALYTICS_HTML)
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
            # No silent default: the viewer sends from= on every post (line ~856), so
            # a token client that omits it is a client with no identity and is refused.
            sender_err = sender_error(m.get("from"))
            if sender_err:
                _counters["rejected_sender"] = _counters.get("rejected_sender", 0) + 1
                self._json({"error": sender_err}, 400)
                return
            sender = canon_sender(m.get("from"))
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
            mid, mts = store(sender, topic, text, "message", parent)
            _counters["ui_posts"] += 1
            self._json({"ok": True, "id": mid, "woken": dispatch_mentions(
                sender, topic, text, mid, parent, ts=mts)})
            return

        if path == "/ui/task":
            m, err = self._unsigned_body()
            if err:
                return
            if not ui_token_ok(m.get("token")):
                self._json({"error": "page token expired — reload the room"}, 401)
                return
            action = str(m.get("action") or "").strip().lower()
            raw_from = m.get("from") or HUMAN_NAME
            sender_err = sender_error(raw_from)
            if sender_err:
                self._json({"error": sender_err}, 400)
                return
            sender = canon_sender(raw_from)
            if not rate_ok(sender):
                self._json({"error": "rate limited"}, 429, {"Retry-After": "5"})
                return
            if action == "create":
                task, terr, code = create_task(sender, m)
            else:
                fn = {"claim": claim_task, "renew": renew_task,
                      "complete": complete_task, "bounce": bounce_task,
                      "release": release_task}.get(action)
                if not fn:
                    self._json({"error": "action must be create|claim|renew|complete|"
                                         "bounce|release"}, 400)
                    return
                task, terr, code = fn(sender, m)
            if terr:
                self._json({"error": terr}, code)
                return
            posted = None
            if action == "complete" and m.get("announce", False):
                body = f"[task {task['id']} done] {task['title']}"
                if task.get("outcome"):
                    body += f" — {task['outcome']}"
                posted, _ = store(sender, task["topic"] or "work", body, "outcome",
                                  task.get("root_id"), key_id=KEY_ID, key_fp=KEY_FP,
                                  peer_addr=(self.client_address[0]
                                             if self.client_address else None),
                                  data=json.dumps({"task": task["id"],
                                                   "state": "done"}, separators=(",", ":")))
            _counters["ui_task_" + action] = _counters.get("ui_task_" + action, 0) + 1
            self._json({"ok": True, "task": task, "posted": posted})
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

        if path in ("/api/tasks", "/api/tasks/claim", "/api/tasks/renew",
                    "/api/tasks/complete", "/api/tasks/bounce", "/api/tasks/release",
                    "/api/artifacts", "/api/amend"):
            self._task_post(path)
            return

        if path != "/send":
            self._json({"error": "not found"}, 404)
            return

        m, err = self._signed_body()
        if err:
            return
        raw_from = m.get("from", "")
        sender_err = sender_error(raw_from)
        sender = canon_sender(raw_from)
        topic = str(m.get("topic", "general"))[:64]
        text = str(m.get("text", ""))[:20000]
        kind = (str(m.get("kind", "message"))[:16].strip().lower() or "message")
        if kind not in KINDS:
            self._json({"error": f"unknown kind {kind!r}",
                        "allowed": sorted(KINDS)}, 400)
            return
        data_text, derr = _envelope(m.get("data"))
        if derr:
            self._json({"error": derr}, 400)
            return
        # Oversized bodies become artifacts: the room keeps a readable head and
        # the full text stays one GET away (content-addressed, so reposts dedupe).
        if m.get("route_large") or ARTIFACT_AUTO_CHARS:
            text, routed = maybe_route_large(text, _data_obj(data_text),
                                             force=bool(m.get("route_large")))
            if routed:
                data_text = json.dumps(routed, separators=(",", ":"))
        # Count the refusal before the text check: a probe with an empty body and
        # a bad sender returns the combined 400 and used to leave the counter at 0,
        # which made the metric evidence for only one of the two refusal shapes.
        if sender_err:
            _counters["rejected_sender"] = _counters.get("rejected_sender", 0) + 1
        if not text:
            self._json({"error": "from and text required" if sender_err else "text required"}, 400)
            return
        if sender_err:
            self._json({"error": sender_err}, 400)
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
        mid, mts = store(sender, topic, text, kind, parent,
                         key_id=KEY_ID, key_fp=KEY_FP,
                         peer_addr=(self.client_address[0]
                                    if self.client_address else None),
                         data=data_text)
        _counters["sent"] += 1
        self._json({"ok": True, "id": mid,
                    "woken": dispatch_mentions(sender, topic, text, mid, parent,
                                               ts=mts)})

    def _task_post(self, path):
        """Signed writes for tasks, artifacts and amendments.

        Kept in one method so the identity rules are in one place: every write
        records the sender the signature proves (not a name the body claims), and
        every state change appends a task_event — the board is a projection, the
        events are the record.
        """
        m, err = self._signed_body()
        if err:
            return
        raw_from = m.get("from", "")
        sender_err = sender_error(raw_from)
        if sender_err:
            _counters["rejected_sender"] = _counters.get("rejected_sender", 0) + 1
            self._json({"error": sender_err}, 400)
            return
        sender = canon_sender(raw_from)
        if not rate_ok(sender):
            _counters["rejected_rate"] += 1
            self._json({"error": "rate limited"}, 429, {"Retry-After": "5"})
            return

        if path == "/api/artifacts":
            text = str(m.get("text", ""))
            if not text.strip():
                self._json({"error": "text required"}, 400)
                return
            handle = artifact_write(text, m.get("name"))
            _counters["artifacts"] = _counters.get("artifacts", 0) + 1
            self._json({"ok": True, **handle})
            return

        if path == "/api/amend":
            try:
                target = int(m.get("id"))
            except (TypeError, ValueError):
                self._json({"error": "id must be an integer"}, 400)
                return
            with db() as c:
                row = c.execute(f"{SEL} WHERE id = ?", (target,)).fetchone()
            if not row:
                self._json({"error": "message not found"}, 404)
                return
            if row[2] != sender:
                self._json({"error": "only the original sender can amend"}, 403)
                return
            text = str(m.get("text", ""))[:20000]
            if not text.strip():
                self._json({"error": "text required"}, 400)
                return
            envelope, derr = _envelope(m.get("data"))
            if derr:
                self._json({"error": derr}, 400)
                return
            base = _data_obj(envelope) or {}
            base["supersedes"] = target
            body = (f"[amendment to #{target}] " + text)
            mid, mts = store(sender, row[3], body, "amendment", row[6],
                             key_id=KEY_ID, key_fp=KEY_FP,
                             peer_addr=(self.client_address[0]
                                        if self.client_address else None),
                             data=json.dumps(base, separators=(",", ":")))
            _counters["amended"] = _counters.get("amended", 0) + 1
            self._json({"ok": True, "id": mid, "supersedes": target,
                        "woken": dispatch_mentions(sender, row[3], body, mid, row[6],
                                                   ts=mts)})
            return

        if path == "/api/tasks":
            task, err, code = create_task(sender, m)
            if err:
                self._json({"error": err}, code)
                return
            body = task["title"] + (("\n\n" + task["detail"]) if task["detail"] else "")
            data = {"task": task["id"], "ref": task.get("ref"),
                    "owner": task.get("owner"), "state": "open"}
            mid, mts = store(sender, task["topic"] or "work", body, "task", None,
                             key_id=KEY_ID, key_fp=KEY_FP,
                             peer_addr=(self.client_address[0]
                                        if self.client_address else None),
                             data=json.dumps(data, separators=(",", ":")))
            with _lock, db() as c:
                c.execute("UPDATE tasks SET root_id = ? WHERE id = ?", (mid, task["id"]))
            task["root_id"] = mid
            _counters["tasks_created"] = _counters.get("tasks_created", 0) + 1
            self._json({"ok": True, "task": task, "id": mid, "woken": dispatch_mentions(
                sender, task["topic"] or "work", body, mid, None, ts=mts)})
            return

        fn = {"/api/tasks/claim": claim_task,
              "/api/tasks/renew": renew_task,
              "/api/tasks/complete": complete_task,
              "/api/tasks/bounce": bounce_task,
              "/api/tasks/release": release_task}[path]
        task, err, code = fn(sender, m)
        if err:
            self._json({"error": err}, code)
            return
        _counters["tasks_" + path.rsplit("/", 1)[1]] = (
            _counters.get("tasks_" + path.rsplit("/", 1)[1], 0) + 1)
        # Completing announces in the room (the outcome is room news); claiming
        # does not, or the room becomes a status bar.
        posted = None
        if path == "/api/tasks/complete" and m.get("announce", True):
            body = f"[task {task['id']} done] {task['title']}"
            if task.get("outcome"):
                body += f" — {task['outcome']}"
            if task.get("evidence"):
                body += f"\nevidence: {task['evidence']}"
            posted, _ = store(sender, task["topic"] or "work", body, "outcome",
                              task.get("root_id"), key_id=KEY_ID, key_fp=KEY_FP,
                              peer_addr=(self.client_address[0]
                                         if self.client_address else None),
                              data=json.dumps({"task": task["id"], "state": "done",
                                               "outcome": task.get("outcome"),
                                               "evidence": task.get("evidence")},
                                              separators=(",", ":")))
        self._json({"ok": True, "task": task, "posted": posted})
        return

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
    # The backup cadence is an env value that leaves NO other trace: a process that
    # ran with AGENT_CHAT_BACKUP_INTERVAL=120 is indistinguishable in the manifest
    # from one running 3600 (same byte size, same destination, same src token). This
    # line is the only per-process witness; do not drop it to keep the log quiet.
    print(f"agent-chat backup interval={BACKUP_INTERVAL}s dir={BACKUP_DIR or '(disabled)'}",
          flush=True)
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
        _slot("shutdown", copy=True)


if __name__ == "__main__":
    main()
