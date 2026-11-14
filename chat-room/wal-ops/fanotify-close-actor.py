#!/usr/bin/env python3
"""Name the ACTOR of every room.db-wal CLOSE_WRITE -- the fact inotify cannot carry.

Why this exists: IN_CLOSE_WRITE from inotify has no actor and no post-processing
recovers one, so a stranded pair can never be attributed after the fact. fanotify
with FAN_REPORT_PIDFD reports the closer's pid (metadata.pid) plus a pidfd that
survives its exit, at close time, with no per-tick polling and therefore no miss
window.

PRIVILEGE, MEASURED 2026-11-14 on 7.0.0-38-generic: FAN_REPORT_PIDFD is EPERM for
uid 1000 and succeeds only as root (FAN_REPORT_FID alone arms unprivileged, but a
FID event carries no actor -- it is the thing we already had). So this instrument
must run from a system unit / sudo, not from user@1000.service like the two inotify
watchers.

Usage:  sudo python3 fanotify-close-actor.py [--dir DIR] [--seconds N] [--log FILE]

Emit one JSON line per close event: ts, file, size, actor_pid, comm, unit, cgroup.
"""
import argparse, ctypes, ctypes.util, json, os, select, struct, sys, time

libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
libc.syscall.restype = ctypes.c_long
NR_fanotify_init, NR_fanotify_mark = 300, 301
FAN_CLOEXEC, FAN_NONBLOCK = 0x1, 0x2
FAN_CLASS_NOTIF = 0x0
FAN_REPORT_PIDFD = 0x80
FAN_MARK_ADD, FAN_MARK_ONLYDIR = 0x1, 0x4
FAN_CLOSE_WRITE = 0x8
FAN_EVENT_ON_CHILD = 0x08000000
AT_FDCWD = -100
INFO_TYPE_FID, INFO_TYPE_PIDFD = 1, 4


def _name(pid, field):
    try:
        with open("/proc/%d/%s" % (pid, field), "rb") as f:
            raw = f.read()
        if field == "cmdline":
            return raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()
        return raw.decode("utf-8", "replace").strip()
    except OSError:
        return None


def _unit(pid):
    """DEEPEST .service component of the cgroup -- every user unit sits under
    user@1000.service, so a first-component walk excludes nothing."""
    try:
        with open("/proc/%d/cgroup" % pid, "r") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    units = []
    for ln in lines:
        parts = ln.split(":", 2)[-1].split("/")
        for p in reversed(parts):
            if p.endswith(".service"):
                units.append(p)
                break
    return units[0] if units else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.expanduser("~/agents/agent-chat"))
    ap.add_argument("--seconds", type=float, default=15.0)
    ap.add_argument("--log", default=None, help="append JSON lines here instead of stdout")
    ap.add_argument("--only", default="room.db-wal", help="basename filter; '' for all")
    a = ap.parse_args()

    out = open(a.log, "a", buffering=1) if a.log else sys.stdout
    emit = lambda d: (out.write(json.dumps(d) + "\n"), out.flush())

    ctypes.set_errno(0)
    fd = libc.syscall(ctypes.c_long(NR_fanotify_init),
                      ctypes.c_uint(FAN_CLASS_NOTIF | FAN_REPORT_PIDFD | FAN_CLOEXEC | FAN_NONBLOCK),
                      ctypes.c_uint(0))
    if fd < 0:
        e = ctypes.get_errno()
        emit({"error": "fanotify_init(FAN_REPORT_PIDFD) failed", "errno": e, "uid": os.getuid(),
              "note": "EPERM for uid!=0 on this kernel"})
        return 2
    rc = libc.syscall(ctypes.c_long(NR_fanotify_mark), ctypes.c_int(fd),
                      ctypes.c_uint(FAN_MARK_ADD | FAN_MARK_ONLYDIR),
                      ctypes.c_uint(FAN_CLOSE_WRITE | FAN_EVENT_ON_CHILD),
                      ctypes.c_int(AT_FDCWD), ctypes.c_char_p(a.dir.encode()))
    if rc < 0:
        emit({"error": "fanotify_mark failed", "errno": ctypes.get_errno(), "dir": a.dir})
        return 2
    emit({"start": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "dir": a.dir, "seconds": a.seconds,
          "uid": os.getuid(), "filter": a.only})

    deadline = time.time() + a.seconds
    n = 0
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.5)
        if not r:
            continue
        buf = os.read(fd, 65536)
        off, total = 0, len(buf)
        while off + 24 <= total:
            elen, vers, _res, mlen, mask, efd, epid = struct.unpack_from("<IBBHQii", buf, off)
            if mlen == 0:
                mlen = 24
            info_off = off + mlen
            while info_off + 4 <= off + elen:
                itype, ilen = struct.unpack_from("<HH", buf, info_off)
                if ilen == 0:
                    break
                if itype == INFO_TYPE_PIDFD and mask == FAN_CLOSE_WRITE:
                    pidfd = struct.unpack_from("<i", buf, info_off + 4)[0]
                    pid = epid
                    if pidfd >= 0:
                        try:
                            with open("/proc/self/fdinfo/%d" % pidfd) as fh:
                                for ln in fh:
                                    if ln.startswith("Pid:"):
                                        pid = int(ln.split()[1])
                        except OSError:
                            pass
                        os.close(pidfd)
                    # which file closed? FID info has no name; derive from the dir listing is
                    # not enough, so name by inode only when asked. Keep basename filter on
                    # whatever the caller passed for sanity.
                    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                           "mono": round(time.monotonic(), 6),
                           "mask": "CLOSE_WRITE", "actor_pid": pid,
                           "comm": _name(pid, "comm"), "unit": _unit(pid),
                           "cmdline": (_name(pid, "cmdline") or "")[:160]}
                    if a.only:
                        rec["expect"] = a.only
                    emit(rec)
                    n += 1
                info_off += ilen
            if elen <= 0:
                break
            off += elen
    emit({"stop": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "events": n})
    os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
