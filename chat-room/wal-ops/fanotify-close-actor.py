#!/usr/bin/env python3
"""Name the ACTOR of every room.db-wal CLOSE_WRITE -- the fact inotify cannot carry.

Why this exists: IN_CLOSE_WRITE from inotify has no actor and no post-processing
recovers one, so a stranded pair can never be attributed after the fact. fanotify
with FAN_REPORT_PIDFD reports the closer's pid at close time.

TWO THINGS THIS INSTRUMENT DOES NOT DO, both measured 2026-11-14 on
7.0.0-38-generic:

  1. It does not see one event per close. fanotify coalesces identical events at
     the QUEUE TAIL with no time limit, and the coalescing key includes the
     actor: two closes by DIFFERENT pids -> 2 events at every gap tested
     (0.05/0.30/1.00/2.50 s); two closes by the SAME pid -> 1 event at every
     gap; 200 closes by one child -> 1 event. So a merged group carries exactly
     one actor (merging cannot invent a false bystander) and the only thing
     merging costs is the close COUNT. Drain continuously (the loop below is a
     50 ms select) and never infer a close count from an event count.

  2. It cannot always name the file. A per-file mark is impossible for
     room.db-wal/-shm because the pair is unlinked between cycles (FAN_MARK_ADD
     returns ENOENT), so this marks the DIRECTORY and resolves each event's FID
     with open_by_handle_at. That resolution is a race against the unlink: it
     succeeds while the inode is still linked (measured 153/153 over 20 s in
     steady state) and returns ESTALE once it is gone -- which is precisely the
     window where the -wal matters most. So: `file` is null with a `file_err`
     reason rather than a guess, and size/mtime are taken by fstat on the
     resolved handle, never inferred from a name.

PRIVILEGE, MEASURED 2026-11-14: FAN_REPORT_PIDFD is EPERM for uid 1000 and
succeeds only as root (FAN_REPORT_FID alone arms unprivileged, but a FID event
carries no actor -- it is the thing we already had). Resolution additionally
needs CAP_DAC_READ_SEARCH, which root has. So this instrument must run from a
system unit / sudo, not from user@1000.service like the two inotify watchers.

Usage:  sudo python3 fanotify-close-actor.py [--dir DIR] [--seconds N] [--log FILE]
                                                 [--only BASENAME]

Emit one JSON line per close event: ts, mono, mask, file, file_err, size, mtime,
actor_pid, actor_start, comm, unit, cmdline.
With --only, events whose file could not be named are still emitted (file=null)
and counted in the closing `stop` line's `unresolved` field -- a name filter
must never silently drop the events it cannot classify.
"""
import argparse, ctypes, ctypes.util, errno, json, os, platform, select, struct, sys, time

libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
libc.syscall.restype = ctypes.c_long
NR_fanotify_init, NR_fanotify_mark = 300, 301
_OPEN_BY_HANDLE = {"x86_64": 304, "aarch64": 265}
FAN_CLOEXEC, FAN_NONBLOCK = 0x1, 0x2
FAN_CLASS_NOTIF = 0x0
FAN_REPORT_PIDFD = 0x80
FAN_REPORT_FID = 0x200
FAN_MARK_ADD, FAN_MARK_ONLYDIR = 0x1, 0x4
FAN_CLOSE_WRITE = 0x8
FAN_EVENT_ON_CHILD = 0x08000000
AT_FDCWD = -100
INFO_TYPE_FID, INFO_TYPE_PIDFD = 1, 4
DRAIN_S = 0.05


def _pid_start(pid):
    """Field 22 of /proc/<pid>/stat: starttime in clock ticks. A pid reused across
    a merge would otherwise look like the same actor across two lifetimes."""
    try:
        with open("/proc/%d/stat" % pid, "rb") as f:
            raw = f.read()
        return int(raw[raw.rindex(b")") + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return None


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
    ap.add_argument("--only", default=None,
                    help="emit only this basename (unresolvable events are still reported)")
    a = ap.parse_args()

    out = open(a.log, "a", buffering=1) if a.log else sys.stdout
    emit = lambda d: (out.write(json.dumps(d) + "\n"), out.flush())

    nr_obh = _OPEN_BY_HANDLE.get(platform.machine())
    ctypes.set_errno(0)
    fd = libc.syscall(ctypes.c_long(NR_fanotify_init),
                      ctypes.c_uint(FAN_CLASS_NOTIF | FAN_REPORT_PIDFD | FAN_REPORT_FID
                                    | FAN_CLOEXEC | FAN_NONBLOCK),
                      ctypes.c_uint(0))
    if fd < 0:
        e = ctypes.get_errno()
        emit({"error": "fanotify_init(FAN_REPORT_PIDFD|FAN_REPORT_FID) failed", "errno": e,
              "uid": os.getuid(), "note": "EPERM for uid!=0 on this kernel"})
        return 2
    rc = libc.syscall(ctypes.c_long(NR_fanotify_mark), ctypes.c_int(fd),
                      ctypes.c_uint(FAN_MARK_ADD | FAN_MARK_ONLYDIR),
                      ctypes.c_uint(FAN_CLOSE_WRITE | FAN_EVENT_ON_CHILD),
                      ctypes.c_int(AT_FDCWD), ctypes.c_char_p(a.dir.encode()))
    if rc < 0:
        emit({"error": "fanotify_mark failed", "errno": ctypes.get_errno(), "dir": a.dir})
        return 2
    try:
        mount_fd = os.open(a.dir, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as ex:
        emit({"error": "open(dir) failed", "errno": ex.errno, "dir": a.dir})
        return 2

    def resolve(handle):
        """-> (basename|None, errname|None, st|None) on the closed inode."""
        if nr_obh is None:
            return None, "NO_OPEN_BY_HANDLE", None
        buf = ctypes.create_string_buffer(handle, len(handle))
        ctypes.set_errno(0)
        h = libc.syscall(ctypes.c_long(nr_obh), ctypes.c_int(mount_fd),
                         ctypes.cast(buf, ctypes.c_void_p), ctypes.c_int(os.O_RDONLY))
        if h < 0:
            return None, errno.errorcode.get(ctypes.get_errno(), "E?"), None
        try:
            st = os.fstat(h)
            try:
                return os.path.basename(os.readlink("/proc/self/fd/%d" % h)), None, st
            except OSError:
                return "inode:%d" % st.st_ino, None, st
        finally:
            os.close(h)

    emit({"start": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "dir": a.dir, "seconds": a.seconds,
          "uid": os.getuid(), "filter": a.only, "drain_s": DRAIN_S,
          "resolve": "open_by_handle_at" if nr_obh else "unavailable"})

    deadline = time.time() + a.seconds
    n = emissive = unresolved = 0
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], DRAIN_S)
        if not r:
            continue
        buf = os.read(fd, 65536)
        off, total = 0, len(buf)
        while off + 24 <= total:
            elen, vers, _res, mlen, mask, efd, epid = struct.unpack_from("<IBBHQii", buf, off)
            if elen <= 0:
                break
            if mask == FAN_CLOSE_WRITE:
                n += 1
                pid, handle = epid, None
                io = off + (mlen or 24)
                while io + 4 <= off + elen:
                    itype, ilen = struct.unpack_from("<HH", buf, io)
                    if ilen == 0:
                        break
                    if itype == INFO_TYPE_PIDFD:
                        pidfd = struct.unpack_from("<i", buf, io + 4)[0]
                        if pidfd >= 0:
                            try:
                                with open("/proc/self/fdinfo/%d" % pidfd) as fh:
                                    for ln in fh:
                                        if ln.startswith("Pid:"):
                                            pid = int(ln.split()[1])
                            except OSError:
                                pass
                            try:
                                os.close(pidfd)
                            except OSError:
                                pass
                    elif itype == INFO_TYPE_FID:
                        handle = buf[io + 12:io + ilen]  # hdr(4) + fsid(8)
                    io += ilen
                fname, ferr, st = (None, "NO_FID", None)
                if handle:
                    fname, ferr, st = resolve(handle)
                if fname is None:
                    unresolved += 1
                rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                       "mono": round(time.monotonic(), 6),
                       "mask": "CLOSE_WRITE", "file": fname, "file_err": ferr,
                       "size": st.st_size if st else None,
                       "mtime": time.strftime("%Y-%m-%dT%H:%M:%S",
                                              time.localtime(st.st_mtime)) if st else None,
                       "actor_pid": pid, "actor_start": _pid_start(pid),
                       "comm": _name(pid, "comm"), "unit": _unit(pid),
                       "cmdline": (_name(pid, "cmdline") or "")[:160]}
                if a.only and fname is not None and fname != a.only:
                    off += elen
                    continue
                emit(rec)
                emissive += 1
            off += elen
    emit({"stop": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "closes": n,
          "emitted": emissive, "unresolved": unresolved})
    os.close(mount_fd)
    os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
