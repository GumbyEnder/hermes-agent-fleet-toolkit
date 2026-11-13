#!/usr/bin/env python3
"""Event-driven watcher for the chat-room DB pair: catches -wal/-shm delete/move/rename.

Why: the fault class is "frames present at the instant the sidecar disappears"; a
0 B-vs-absent ambiguity in checkpoint.log cannot tell us whether that happened, and
1 Hz stat sampling can miss the window. inotify is edge-triggered, needs no root and
no ptrace, and records the exact instant. It cannot name the PID (auditd/perf/bpftrace
can, all unavailable/blocked on this host: perf_event_paranoid=4, no auditd).

SIDECAR DELETE SEMANTICS (measured 2026-11-13 02:37-02:42, after bus commit 59bef9f
closed the per-request connection leak):
  - Before the fix the sidecar was never unlinked at all: 1 DELETE in 2 h (a canary),
    because ~85 leaked handles pinned it.
  - After the fix a sidecar DELETE is normal and constant: 629 wal + 629 shm deletes in
    ~4 min, 628 of them `last_size=0` (a read-only open/close).
  - `last_size>0` is NOT by itself the wedge. The first post-fix delete carried
    last_size=201912 and was benign: room.db CLOSE_WRITE 0.013 s earlier had folded all
    49 frames (mtime moved), then the last close unlinked the emptied file.
  So the wedge signature is a delete with frames AND no preceding fold. Each
  frames-bearing delete now prints `fold=+<s>` (time since room.db last changed) or
  `fold=NONE` (no fold ever observed) — that token, not the byte count, is the test.

Usage:
  wal-unlink-watch.py [--dir DIR] [--log FILE] [--seconds N] [--max-bytes N]
Log lines: ISO-8601 ts, event name, filename, and the sidecar's size+mtime at event time
(stat immediately after the event: 'gone' means already unlinked). The log rotates to
<log>.1 (copy then truncate — this is a CIFS mount, no rename) once it passes --max-bytes.
"""
import argparse, ctypes, ctypes.util, datetime, json, os, select, shutil, struct, sys, time

IN_CREATE, IN_DELETE, IN_MOVED_FROM, IN_MOVED_TO, IN_DELETE_SELF, IN_ATTRIB = 0x100, 0x200, 0x40, 0x80, 0x400, 0x4
IN_CLOSE_WRITE, IN_OPEN = 0x8, 0x20
MASK = IN_CREATE | IN_DELETE | IN_MOVED_FROM | IN_MOVED_TO | IN_CLOSE_WRITE | IN_ATTRIB
NAMES = {IN_CREATE: "CREATE", IN_DELETE: "DELETE", IN_MOVED_FROM: "MOVED_FROM",
         IN_MOVED_TO: "MOVED_TO", IN_CLOSE_WRITE: "CLOSE_WRITE", IN_ATTRIB: "ATTRIB"}

libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
EVENT = struct.Struct("iIII")
SIDECARS = ("room.db-wal", "room.db-shm")


def watch(target_dir, log_path, seconds=0, max_bytes=64000000):
    fd = libc.inotify_init1(os.O_NONBLOCK)
    if fd < 0:
        raise OSError(ctypes.get_errno(), "inotify_init1")
    wd = libc.inotify_add_watch(fd, target_dir.encode(), MASK)
    if wd < 0:
        raise OSError(ctypes.get_errno(), "inotify_add_watch " + target_dir)

    last = {}
    fold = {"ts": 0.0, "mtime": None, "size": 0}   # last room.db CLOSE_WRITE that moved

    def rotate():
        try:
            if max_bytes and os.path.getsize(log_path) >= max_bytes:
                shutil.copyfile(log_path, log_path + ".1")
                open(log_path, "w").close()
        except OSError:
            pass

    def emit(ev, name, extra=""):
        p = os.path.join(target_dir, name) if name else ""
        state = extra
        if p:
            try:
                st = os.stat(p)
                state = "size=%d mtime=%s" % (st.st_size, datetime.datetime.fromtimestamp(st.st_mtime).isoformat())
                last[name] = st.st_size
                if name == "room.db" and ev == IN_CLOSE_WRITE:
                    mt = datetime.datetime.fromtimestamp(st.st_mtime).isoformat()
                    if mt != fold["mtime"] or st.st_size != fold["size"]:
                        fold.update(ts=time.time(), mtime=mt, size=st.st_size)
            except FileNotFoundError:
                # A sidecar DELETE: report bytes at the instant it went away, and — for a
                # frames-bearing delete — whether a fold preceded it. fold=NONE is the
                # condition to chase; fold=+small means SQLite emptied it first.
                state = "gone last_size=%s" % last.get(name, "unknown")
                if name == "room.db-wal" and ev == IN_DELETE and (last.get(name) or 0) > 0:
                    state += " fold=%s" % (("+%.3fs" % (time.time() - fold["ts"])) if fold["ts"] else "NONE")
        line = "%s %-11s %-28s %s" % (datetime.datetime.now().isoformat(timespec="microseconds"),
                                      NAMES.get(ev, hex(ev)), name or "-", state)
        rotate()
        with open(log_path, "a") as f:
            f.write(line + "\n")
        print(line, flush=True)

    with open(log_path, "a") as f:
        f.write("%s WATCH %s mask=%s\n" % (datetime.datetime.now().isoformat(timespec="microseconds"),
                                           target_dir, hex(MASK)))
    deadline = time.time() + seconds if seconds else None
    buf = b""
    while True:
        if deadline and time.time() > deadline:
            return
        r, _, _ = select.select([fd], [], [], 1.0)
        if not r:
            continue
        buf += os.read(fd, 65536)
        while len(buf) >= EVENT.size:
            wd_, mask, cookie, ln = EVENT.unpack_from(buf)
            name = buf[EVENT.size:EVENT.size + ln].rstrip(b"\0").decode(errors="replace")
            buf = buf[EVENT.size + ln:]
            if mask & IN_ATTRIB and not (mask & (IN_DELETE | IN_CREATE)):
                continue  # ATTRIB alone is noise (chmod/utime); the size line already carries mtime
            emit(mask, name)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.expanduser("~/agents/agent-chat"))
    ap.add_argument("--log", default="/mnt/nas/host-ops/agent-chat/wal-unlink-watch.log")
    ap.add_argument("--seconds", type=int, default=0, help="0 = run until killed")
    ap.add_argument("--max-bytes", type=int, default=64000000,
                    help="rotate the log to <log>.1 past this size (0 = never)")
    a = ap.parse_args()
    try:
        watch(a.dir, a.log, a.seconds, a.max_bytes)
    except KeyboardInterrupt:
        sys.exit(0)
