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

Emit one JSON line per event: ts, mono, mask, file, file_err, size, mtime,
actor_pid, actor_pidfd, actor_start, actor_state, actor_err, comm, unit,
unit_err, cmdline, argv_seen, argv_seen_start.

`cmdline` is what THIS row read. `argv_seen` is what this arm read from the same
task while it was alive (matched by starttime, or by pid when the task is already
gone), emitted as a separate field with `argv_seen_start` so a reader can reject
the join. It exists because a milliseconds-long copier is reaped between its
CLOSE_WRITE and the read, and a cached name that is labelled as cached is worth
more than a null.

THREE ARMS, AND ONLY ONE OF THEM ALWAYS NAMES THE ACTOR (all measured
2026-11-15 on 7.0.0-38-generic):

  default (NOTIF, closed)  CLOSE_WRITE + FAN_OPEN. A plain `cp` of one 15 B file
                           is reaped before the row can read /proc: the failing
                           row carried pid plus four nulls. FAN_OPEN helps -- the
                           opener provably exists at open time -- but it does NOT
                           guarantee the read: the arm wakes, drains and reads
                           /proc in ~12-350 us, a 10-file `cp` lives ~130 us to
                           1 ms, and the arm's own per-event cost makes the later
                           events of a burst arrive after the copier is reaped.
                           Measured on the exact shape this instrument exists for
                           (10-file flat copy): 1-2 of 10 rows named, the rest
                           actor_err=GONE. Never claim N-1 clean chances.
                           Also: FAN_OPEN fires on READS, so a read of a file in
                           the marked dir produces rows (join an OPEN row to its
                           CLOSE_WRITE row on (file, actor_pid)).

  --no-open                CLOSE_WRITE only. No actor at open time at all.

  --block-on-open          FAN_OPEN_PERM under FAN_CLASS_PRE_CONTENT: the kernel
                           PARKS the opener inside open() until this process
                           writes FAN_ALLOW. The /proc reads then happen against a
                           process that cannot go anywhere -- measured 23/23 rows
                           fully named (pid, comm, unit, cmdline) on the same
                           10-file `cp` plus three readers, where the default arm
                           named 1-2. This is the only arm that is an identity
                           instrument. Costs: every open in the dir is serialized
                           through userspace (~100 us here), and the group must
                           run in LEGACY mode (see below).

LEGACY MODE IS FORCED, not chosen: FAN_CLASS_PRE_CONTENT | FAN_REPORT_PIDFD is
EINVAL (errno 22), and FAN_CLASS_PRE_CONTENT | FAN_REPORT_FID carries no actor at
all (header pid/fd are FAN_NOFD). Only "no FAN_REPORT_* flags" hands back both a
real fd on the opened object and the opener's pid, which is also why the file name
under --block-on-open comes from that fd and not from a FID handle.

Losing the listener does NOT wedge the directory: with the arm SIGSTOPped and then
SIGKILLed mid-block, the parked opener was released and its read succeeded (rc=0),
and the next open behaved normally. A listener that stays alive but stops
responding would hold opens indefinitely -- that is inherent to permission events.

`actor_err` disambiguates what `cmdline: null` and `cmdline: ""` cannot:
  null            -> /proc/<pid>/comm read succeeded; cmdline is genuinely empty
                     (kernel thread, or exec with no argv)
  "GONE"          -> no /proc entry: the actor was reaped before the read
  "ZOMBIE"        -> /proc entry in state Z: exited, not yet reaped, empty cmdline
  "GONE_RACE"     -> the reads straddled the exit: cmdline read back empty (mm
                     already torn down) but the task was NOT yet marked Z. A row
                     carrying comm plus an empty argv plus unit_err=ENOENT is this
                     shape, not a fact about a live process.
  "ARGV_TORN_DOWN"-> the same task had ALREADY been read with a non-empty argv in
                     this process's lifetime, so the empty read now is exit_mm(),
                     not an argv-less process. `cmdline` on the row carries the
                     earlier live read, so the name is relocated, not lost.
  "EXIT_WINDOW_UNSEEN" -> the same exit window, but this arm never read this task
                     with an argv: empty cmdline, comm readable, state not Z, the
                     cgroup still readable, and PF_KTHREAD clear -- so a userspace
                     task that cannot be argv-less. The argv is gone, not absent.
                     (Measured 2026-11-15T02:28:04: comm="cp", cmdline="",
                     state R, unit readable, actor_err=null -- a row asserting a
                     false fact. Two other generators are indistinguishable at
                     the row and both mean the same thing operationally -- the
                     argv is not readable as a fact, never "empty": execve()
                     with argv={NULL}, and a daemon that zeroes its own argv
                     area (setproctitle-style scrubbing, usually alive in S).)
  "NO_PID"        -> the event carried no PIDFD handle at all
  "CMDLINE_UNREAD"-> comm readable, cmdline read failed (transient)
Only actor_err=null makes an empty cmdline a fact about the process, and after
2026-11-15 that fact is only ever emitted for a PF_KTHREAD task.

`unit` has the SAME two readings and is the reason `unit_err` exists: the cgroup
read happens in pass 2, after the actor reads, so it is the first field to lose
its subject on a fast copier. unit=null with unit_err=null means "no .service in
the cgroup"; unit=null with unit_err=ENOENT means the task was gone by then.
`actor_state` is the pass-1 task state and is emitted so a reader can see the Z
boundary instead of inferring it.
With --only, events whose file could not be named are still emitted (file=null)
and counted in the closing `stop` line's `unresolved` field -- a name filter
must never silently drop the events it cannot classify.

THE `stop` FIELDS HAVE THREE DIFFERENT SCOPES; do not read them as one set.
`closes` counts every CLOSE_WRITE event DELIVERED in the marked directory,
before the --only filter (and after fanotify coalescing, so it is an event
count, not a close count). `emitted` counts the rows actually written, i.e.
named-and-matching plus every unnamed event. `unresolved` counts the events
whose file could not be named; each one is also emitted, so `unresolved` is a
SUBSET of `emitted` and equals the number of `file:null` rows in this log --
never a count of rows the filter removed. Measured 2026-11-14 on a scratch dir,
120 closes of other.txt + 120 of room.db-wal + 120 closed-and-unlinked:
`closes=359 emitted=240 unresolved=120`, 120/120 file=null rows present with
file_err=ESTALE, other.txt rows emitted=0.
"""
import argparse, ctypes, ctypes.util, errno, json, os, platform, select, signal, stat, struct, sys, time

libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
libc.syscall.restype = ctypes.c_long
NR_fanotify_init, NR_fanotify_mark = 300, 301
_OPEN_BY_HANDLE = {"x86_64": 304, "aarch64": 265}
FAN_CLOEXEC, FAN_NONBLOCK = 0x1, 0x2
FAN_CLASS_NOTIF = 0x0
FAN_CLASS_PRE_CONTENT = 0x8
FAN_REPORT_PIDFD = 0x80
FAN_REPORT_FID = 0x200
FAN_MARK_ADD, FAN_MARK_ONLYDIR = 0x1, 0x4
FAN_CLOSE_WRITE, FAN_OPEN = 0x8, 0x20
FAN_CREATE = 0x100
FAN_OPEN_PERM, FAN_ALLOW = 0x10000, 0x01
FAN_EVENT_ON_CHILD = 0x08000000
AT_FDCWD = -100
INFO_TYPE_FID, INFO_TYPE_PIDFD = 1, 4
DRAIN_S = 0.05
PF_KTHREAD = 0x00200000      # /proc/<pid>/stat field 9, bit 21


def _pid_stat(pid):
    """ONE open of /proc/<pid>/stat -> (state, starttime, flags). Field 3 is the
    state: 'Z' separates "exited, cmdline empty because nothing is left of it"
    from "has an empty argv", the two readings a bare empty string cannot tell
    apart. Field 22 is starttime in clock ticks: a pid reused across a merge
    would otherwise look like one actor across two lifetimes.

    Field 9 (flags) is the third value and costs nothing -- same read. PF_KTHREAD
    is the one STRUCTURAL witness that an empty cmdline is a fact rather than a
    torn-down mm: a kernel thread has no argv for its whole life, while a
    userspace task can only read empty because exit_mm() already ran. Ask the
    kernel for the flag; never pattern-match `comm` for "kworker".

    One open and not two on purpose -- every extra open is another chance for the
    task to be released between the event and the read."""
    try:
        with open("/proc/%d/stat" % pid, "rb") as f:
            raw = f.read()
        rest = raw[raw.rindex(b")") + 2:].split()
        return (rest[0].decode("ascii", "replace"), int(rest[19]),
                int(rest[6]))
    except (OSError, ValueError, IndexError):
        return None, None, None


def _pid_start(pid):
    return _pid_stat(pid)[1]


def empty_argv_label(pstate, unit_err, kthread):
    """-> actor_err for a row whose pass-1 read gave an EMPTY cmdline and which
    was never read with an argv while alive. Pure, so every shape is testable
    without racing a millisecond-long copier.

    Return None only for a kernel thread: there the empty cmdline is true. Any
    other shape is a userspace task (comm readable, PF_KTHREAD clear) that cannot
    be argv-less, so the empty read is exit_mm() and the argv is LOST:
      - task already released by the pass-2 cgroup read (state unknown or
        unit_err=ENOENT) -> GONE_RACE
      - state known, cgroup readable              -> EXIT_WINDOW_UNSEEN
    Kept as one function because the label is a rule, not a line of code: the
    residual this closes (2026-11-15T02:28:04, comm="cp", cmdline="", state R,
    actor_err=null) survived because the ARGV_TORN_DOWN arm only fires for a task
    this arm happened to read alive first. The label says "the argv is not
    readable as a fact", not "the task was exiting": a NULL-argv execve() and a
    self-scrubbing daemon land here too, alike at the row."""
    if kthread:
        return None
    if pstate is None or unit_err == "ENOENT":
        return "GONE_RACE"
    return "EXIT_WINDOW_UNSEEN"


def _name(pid, field):
    """-> (value|None, errname|None). None means the read FAILED; "" means the
    read succeeded and the file is empty. The errname is carried into actor_err
    so a failed read names its own errno instead of hiding behind a null."""
    try:
        with open("/proc/%d/%s" % (pid, field), "rb") as f:
            raw = f.read()
        if field == "cmdline":
            return raw.replace(b"\0", b" ").decode("utf-8", "replace").strip(), None
        return raw.decode("utf-8", "replace").strip(), None
    except OSError as ex:
        return None, errno.errorcode.get(ex.errno or 0, "E?")


def _unit(pid):
    """-> (unit|None, errname|None). DEEPEST .service component of the cgroup --
    every user unit sits under user@1000.service, so a first-component walk
    excludes nothing. The errname exists because this read happens in PASS 2, a
    few hundred us to a few ms after the /proc reads in pass 1: measured
    2026-11-15T02:24:39 on a plain 15 B `cp`, the CLOSE_WRITE row carried
    comm="cp" with unit=null, i.e. the task was alive for pass 1 and reaped
    before this read. A bare null here has the same two readings an empty
    cmdline has, so it gets the same treatment: name the failure."""
    try:
        with open("/proc/%d/cgroup" % pid, "r") as f:
            lines = f.read().splitlines()
    except OSError as ex:
        return None, errno.errorcode.get(ex.errno or 0, "E?")
    units = []
    for ln in lines:
        parts = ln.split(":", 2)[-1].split("/")
        for p in reversed(parts):
            if p.endswith(".service"):
                units.append(p)
                break
    return (units[0] if units else None), None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.expanduser("~/agents/agent-chat"))
    ap.add_argument("--seconds", type=float, default=15.0)
    ap.add_argument("--log", default=None, help="append JSON lines here instead of stdout")
    ap.add_argument("--only", default=None,
                    help="emit only this basename (unresolvable events are still reported)")
    ap.add_argument("--no-open", action="store_true",
                    help="arm CLOSE_WRITE only (no actor at open time; for read-heavy dirs)")
    ap.add_argument("--block-on-open", action="store_true",
                    help="FAN_OPEN_PERM: park each opener until this process allows it, "
                         "so the actor is alive by construction and is always named")
    ap.add_argument("--depth", type=int, default=1,
                    help="mark subdirectories of --dir down to this depth (default 1: the direct "
                         "children). 0 = --dir only. FAN_MARK_MOUNT is never used: it would stall "
                         "every open on the filesystem whenever the listener stops answering.")
    a = ap.parse_args()

    out = open(a.log, "a", buffering=1) if a.log else sys.stdout
    emit = lambda d: (out.write(json.dumps(d) + "\n"), out.flush())

    nr_obh = _OPEN_BY_HANDLE.get(platform.machine())
    ctypes.set_errno(0)
    # MEASURED 2026-11-15 on 7.0.0-38-generic: FAN_CLASS_PRE_CONTENT |
    # FAN_REPORT_PIDFD is EINVAL (errno 22), and FAN_CLASS_PRE_CONTENT |
    # FAN_REPORT_FID carries NO actor (the header pid/fd are FAN_NOFD). So the
    # permission arm has to run in LEGACY mode: no FAN_REPORT_* at all, which is
    # the only form that hands us both a real event fd and the opener's pid.
    legacy = a.block_on_open
    fclass = FAN_CLASS_PRE_CONTENT if legacy else FAN_CLASS_NOTIF
    rflags = 0 if legacy else (FAN_REPORT_PIDFD | FAN_REPORT_FID)
    fd = libc.syscall(ctypes.c_long(NR_fanotify_init),
                      ctypes.c_uint(fclass | rflags | FAN_CLOEXEC | FAN_NONBLOCK),
                      ctypes.c_uint(0))
    if fd < 0:
        e = ctypes.get_errno()
        emit({"error": "fanotify_init(FAN_REPORT_PIDFD|FAN_REPORT_FID) failed", "errno": e,
              "uid": os.getuid(), "note": "EPERM for uid!=0 on this kernel"})
        return 2
    if a.block_on_open:
        mark_mask = FAN_CLOSE_WRITE | FAN_OPEN_PERM | FAN_EVENT_ON_CHILD
    else:
        mark_mask = FAN_CLOSE_WRITE | FAN_EVENT_ON_CHILD | (0 if a.no_open else FAN_OPEN)
    # FAN_CREATE IS NOT AVAILABLE HERE, measured on 7.0.0-38-generic: the kernel accepts
    # CREATE/DELETE/MOVED_* ONLY with FAN_REPORT_FID (rc=0 with FID, EINVAL=22 for every
    # combination without it), and FAN_REPORT_FID + FAN_CLASS_PRE_CONTENT is itself EINVAL at
    # init. So the permission arm -- the only one that can name the opener -- can NEVER see a
    # directory created after arm time. Static marks are the whole coverage in this mode.
    rc = libc.syscall(ctypes.c_long(NR_fanotify_mark), ctypes.c_int(fd),
                      ctypes.c_uint(FAN_MARK_ADD | FAN_MARK_ONLYDIR),
                      ctypes.c_uint(mark_mask),
                      ctypes.c_int(AT_FDCWD), ctypes.c_char_p(a.dir.encode()))
    if rc < 0:
        emit({"error": "fanotify_mark failed", "errno": ctypes.get_errno(), "dir": a.dir})
        return 2

    # PER-SUBDIRECTORY MARKS, and the reason is measured, not stylistic: FAN_MARK_ONLYDIR on
    # --dir reports events for its DIRECT children only, so `cp -a tree/ <dir>/` produced ZERO
    # rows while the same files copied in directly produced one row each. A mount mark would
    # cover the recursion but under --block-on-open it parks EVERY open on the filesystem when
    # this listener stops answering (there is no CLOSE_PERM to bound it), which is an
    # availability failure, not a measurement cost. So: walk, mark each directory, dedupe by
    # (dev,ino) so a bind/loop cannot burn the mark budget, never follow a symlinked dir.
    marks = {"armed": 1, "failed": []}
    # Every directory that actually took a mark, in walk order. The resolver has to scan the
    # SAME set: an ino map built from --dir alone can name nothing it marked below the top,
    # which is how a --depth 2 arm fires and still reports file=null/UNLINKED for every row
    # (measured 2026-11-15).
    marked_dirs: list[str] = [a.dir]

    def _mark_dir(path: str) -> None:
        r = libc.syscall(ctypes.c_long(NR_fanotify_mark), ctypes.c_int(fd),
                         ctypes.c_uint(FAN_MARK_ADD | FAN_MARK_ONLYDIR),
                         ctypes.c_uint(mark_mask),
                         ctypes.c_int(AT_FDCWD), ctypes.c_char_p(path.encode()))
        if r < 0:
            marks["failed"].append({"path": path, "errno": ctypes.get_errno()})
        else:
            marks["armed"] += 1
            marked_dirs.append(path)

    def _walk(path: str, depth: int, seen: set) -> None:
        if depth <= 0:
            return
        try:
            with os.scandir(path) as it:
                kids = [e.path for e in it
                        if e.is_dir(follow_symlinks=False)]
        except OSError as ex:
            marks["failed"].append({"path": path, "errno": ex.errno, "scan": True})
            return
        for kid in sorted(kids):
            try:
                st = os.stat(kid, follow_symlinks=False)
            except OSError:
                continue
            key = (st.st_dev, st.st_ino)
            if key in seen:
                continue
            seen.add(key)
            _mark_dir(kid)
            _walk(kid, depth - 1, seen)

    if a.depth > 0:
        try:
            root_st = os.stat(a.dir, follow_symlinks=False)
            _walk(a.dir, a.depth, {(root_st.st_dev, root_st.st_ino)})
        except OSError as ex:
            marks["failed"].append({"path": a.dir, "errno": ex.errno, "scan": True})
    try:
        mount_fd = os.open(a.dir, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as ex:
        emit({"error": "open(dir) failed", "errno": ex.errno, "dir": a.dir})
        return 2

    ino_map = {}          # st_ino -> (name, st) for ONE scandir of the marked dir
    ino_gen = [None]      # which batch ino_map was built for
    seen_argv = {}        # (pid, starttime) -> cmdline already read from a live task
    pid_argv = {}         # pid -> (starttime, cmdline): the join for a row that
                          # reads /proc too late to have an argv of its own

    def _ino_from_handle(handle):
        """file_handle{handle_bytes(u32), handle_type(i32), f_handle[]}.
        FILEID_INO32_GEN(1) carries a u32 inode, FILEID_INO64_GEN(2) a u64."""
        try:
            hb, ht = struct.unpack_from("<Ii", handle, 0)
            fh = handle[8:8 + hb]
            if ht == 1 and len(fh) >= 4:
                return int.from_bytes(fh[:4], "little")
            if ht == 2 and len(fh) >= 8:
                return int.from_bytes(fh[:8], "little")
        except struct.error:
            pass
        return None

    def _scan_ino_map(gen):
        ino_map.clear()
        seen_dirs = set()
        for d in marked_dirs:
            try:
                st_d = os.stat(d, follow_symlinks=False)
            except OSError:
                continue  # marked dir removed since arm: its events are UNLINKED by definition
            key = (st_d.st_dev, st_d.st_ino)
            if key in seen_dirs:
                continue
            seen_dirs.add(key)
            try:
                with os.scandir(d) as it:
                    for e in it:
                        try:
                            st = e.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        # name is relative to --dir so a row below the top is still an address,
                        # not a basename that could collide with a same-named sibling.
                        rel = os.path.relpath(e.path, a.dir)
                        ino_map[st.st_ino] = (rel, st)
            except OSError:
                continue
        ino_gen[0] = gen

    def resolve(handle, gen):
        """-> (basename|None, errname|None, st|None) for the event's inode.

        NAMED BY INODE, NOT BY open_by_handle_at. The handle already carries the
        inode; matching it against one scandir of each marked directory costs stat
        calls and NO opens, which matters twice over:
          - open_by_handle_at OPENS the watched file, and with FAN_OPEN armed that
            open is itself an event: the arm doubles its own queue and falls
            behind the copier it is trying to name (measured 2026-11-15: 8/10
            rows GONE on a 10-file burst with the open path, 2/10 without);
          - on the WAL path the open was a race against unlink (ESTALE). Inode
            matching has the same semantics for a deleted file -- it simply is
            not in the listing -- without the syscall.
        A miss is reported as UNLINKED, which is the ESTALE case by another name.
        """
        ino = _ino_from_handle(handle)
        if ino is not None:
            if ino_gen[0] != gen:
                _scan_ino_map(gen)
            hit = ino_map.get(ino)
            if hit is not None:
                return hit[0], None, hit[1]
            return None, "UNLINKED", None
        # Unknown handle type: fall back to the open path. Rare, and the self-skip
        # above keeps it from re-entering the queue unbounded.
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

    stop_req = {"sig": None}

    def _on_sig(signum, _frame):
        # A kill and a crash are indistinguishable in this log without this line:
        # both leave a `start` with no matching record, and the next `start` is
        # inferred rather than read. Counts are captured HERE so the record says
        # how much the arm had delivered before it went down.
        stop_req["sig"] = signal.Signals(signum).name

    for _s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(_s, _on_sig)

    emit({"start": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "dir": a.dir, "seconds": a.seconds,
          "uid": os.getuid(), "filter": a.only, "drain_s": DRAIN_S,
          "marks": marks["armed"], "mark_failures": marks["failed"][:8], "depth": a.depth,
          "masks": ("OPEN_PERM|CLOSE_WRITE" if a.block_on_open else
                    "CLOSE_WRITE" if a.no_open else "OPEN|CLOSE_WRITE"),
          "resolve": "ino-scan" + ("" if nr_obh else " (no open_by_handle_at fallback)")})

    deadline = time.time() + a.seconds
    n_open = n_close = emissive = unresolved = self_n = gen = n_scan = 0
    while time.time() < deadline and stop_req["sig"] is None:
        r, _, _ = select.select([fd], [], [], DRAIN_S)
        if not r:
            continue
        buf = os.read(fd, 65536)
        gen += 1
        off, total = 0, len(buf)
        batch = []
        # Stamped at PARSE time, not at emit time: pass 2 can run milliseconds
        # later on a large burst, and the row's ts must not carry that delay.
        ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        mono = round(time.monotonic(), 6)
        while off + 24 <= total:
            elen, vers, _res, mlen, mask, efd, epid = struct.unpack_from("<IBBHQii", buf, off)
            if elen <= 0:
                break
            if not mask & (FAN_OPEN | FAN_OPEN_PERM | FAN_CLOSE_WRITE):
                off += elen
                continue
            mtype = "OPEN" if mask & FAN_OPEN else ("OPEN_PERM" if mask & FAN_OPEN_PERM
                                                   else "CLOSE_WRITE")
            if mtype in ("OPEN", "OPEN_PERM"):
                n_open += 1
            else:
                n_close += 1
            pid, handle, pidfd, fdinfo_pid = (epid if epid > 0 else None), None, None, None
            ev_fd = None
            if legacy:
                # header pid/fd are real: fd is an O_PATH fd on the opened file,
                # valid until we close it, and pid is the opener.
                ev_fd = efd if efd >= 0 else None
                fdinfo_pid = pid
            io = off + elen if legacy else off + (mlen or 24)
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
                                        fdinfo_pid = int(ln.split()[1])
                        except OSError:
                            pass
                    else:
                        pidfd = None
                elif itype == INFO_TYPE_FID:
                    handle = buf[io + 12:io + ilen]  # hdr(4) + fsid(8)
                io += ilen
            # A pidfd whose task is fully released reads back `Pid: -1`. That is a
            # LIVENESS answer, not a pid: the pid is unrecoverable at that point.
            # Keep the raw value in actor_pidfd so "we were told -1" is on the row.
            if fdinfo_pid is not None and fdinfo_pid > 0:
                pid = fdinfo_pid
            elif fdinfo_pid is not None:
                pid = None
            # SELF-SKIP, and it must happen BEFORE resolve(): open_by_handle_at
            # opens the watched file, and with FAN_OPEN armed that open raises a
            # new OPEN event attributed to this process -- an unbounded feedback
            # loop. Measured 2026-11-15 without this guard: 270,723 OPEN events in
            # 12 s from 4 file opens. The arm's own reads are not evidence about
            # the copier; count them in `self` and drop them.
            if pid is not None and pid == os.getpid():
                self_n += 1
                if pidfd is not None:
                    try:
                        os.close(pidfd)
                    except OSError:
                        pass
                off += elen
                continue
            # ACTOR FIRST, and the pidfd is closed only after these reads. The
            # pidfd is not the source of these fields, but ordering is the entire
            # margin on a copier that lives milliseconds: resolve() below is a
            # syscall against a possibly-unlinked inode, and nothing that costs
            # time may sit between the event and the /proc reads.
            # cmdline FIRST: on a copier that lives milliseconds, the field that
            # costs the most to lose is the argv, and every read after the event
            # is a race against reap. Measured, in this order, at 00:36: cp's comm
            # survived on both OPEN rows while the argv of the very same task did
            # not. Read the expensive fact before the cheap one.
            try:
                cmd, cmd_err = _name(pid, "cmdline") if pid else (None, "NO_PID")
                comm, _comm_err = _name(pid, "comm") if pid else (None, "NO_PID")
                pstate, astart, pflags = (_pid_stat(pid) if pid
                                          else (None, None, None))
                # The argv of an actor that has already been seen WITH an argv is
                # not lost when a later row reads it empty: exit_mm() empties
                # cmdline before the task is marked ZOMBIE, so the empty read is
                # the exit window, not a fact about the process. Keyed by
                # (pid, starttime) so a reused pid cannot inherit a lifetime.
                if cmd:
                    if len(seen_argv) > 512:
                        seen_argv.clear()
                        pid_argv.clear()
                    seen_argv[(pid, astart)] = cmd
                    pid_argv[pid] = (astart, cmd)
            finally:
                # RELEASE THE OPENER, always, even if a read raised. Under
                # --block-on-open the copier is parked inside open() until this
                # write lands; that is what makes the /proc reads above a fact
                # rather than a race. It is also the only thing that can wedge
                # the watched directory, so it goes in a finally.
                if mtype == "OPEN_PERM":
                    try:
                        os.write(fd, struct.pack("<iI", efd, FAN_ALLOW))
                    except OSError:
                        pass
            # cgroup is the most expensive read here (multi-line) and the least
            # time-critical: it survives into zombie-hood. Read it in pass 2.
            if pid is None:
                aerr = "GONE" if pidfd is not None else "NO_PID"
            elif comm is None:
                aerr = "GONE"
            elif pstate == "Z":
                aerr = "ZOMBIE"
            elif cmd is None:
                aerr = "CMDLINE_UNREAD:" + (cmd_err or "E?")
            elif cmd == "" and seen_argv.get((pid, astart)):
                # Already read WITH an argv in this process's lifetime: the empty
                # read now is exit_mm(), not an argv-less exec. The argv itself is
                # in `argv` on the row, so the name is not lost, only relocated.
                cmd = seen_argv[(pid, astart)]
                aerr = "ARGV_TORN_DOWN"
            else:
                aerr = None
            if pidfd is not None:
                try:
                    os.close(pidfd)
                except OSError:
                    pass
            batch.append({"mtype": mtype, "ts": ts, "mono": mono,
                          "pid": pid, "pidfd_pid": fdinfo_pid, "handle": handle,
                          "ev_fd": ev_fd,
                          "cmd": cmd, "comm": comm, "astart": astart,
                          "pstate": pstate, "aerr": aerr,
                          # Read in pass 1 (same stat open) so the pass-2 label
                          # never has to race for it: a kernel thread's empty
                          # cmdline is a fact, a userspace task's is exit_mm().
                          "kthread": bool(pflags is not None
                                          and pflags & PF_KTHREAD)})
            off += elen

        # PASS 2 -- name the files, and only now. resolve() is the expensive,
        # NON-time-critical half: a file's name is recoverable from the FID after
        # the actor is long gone, an actor's name is not recoverable at all. Doing
        # it inline in pass 1 makes the arm fall behind its own event stream and
        # lose the actor on every file after the first -- measured 2026-11-15 on a
        # 10-file burst: 9/10 OPEN rows came back actor_err=GONE, one named.
        for ev in batch:
            fname, ferr, st = (None, "NO_FID", None)
            if ev.get("ev_fd") is not None:
                try:
                    st = os.fstat(ev["ev_fd"])
                    try:
                        fname = os.path.basename(os.readlink("/proc/self/fd/%d" % ev["ev_fd"]))
                    except OSError:
                        fname = "inode:%d" % st.st_ino
                    ferr = None      # named from the event's own fd
                except OSError as ex:
                    ferr = errno.errorcode.get(ex.errno or 0, "E?")
                finally:
                    try:
                        os.close(ev["ev_fd"])
                    except OSError:
                        pass
            elif ev["handle"]:
                fname, ferr, st = resolve(ev["handle"], gen)
            if fname is None:
                # Counted HERE, above the filter, on purpose: the filter below
                # only skips NAMED rows, so every unnamed event is emitted too.
                # unresolved is the count of file:null rows in this log, not a
                # count of rows removed. Moving this below the filter would
                # undercount it -- and would need a filter that drops unnamed
                # events, which is the thing this instrument refuses to do.
                unresolved += 1
            if a.only and fname is not None and fname != a.only:
                continue
            aerr = ev["aerr"]
            # The cgroup read is pass 2, so it is the one field that can be gone
            # while pass 1 succeeded. A bare null unit therefore reads two ways,
            # exactly like an empty cmdline: "no .service in the cgroup" and "the
            # task was reaped before this read". unit_err names which.
            uval, uerr = (_unit(ev["pid"]) if ev["pid"] else (None, "NO_PID"))
            # BEST-EFFORT JOIN, separate from `cmdline` on purpose: this arm may
            # have already read THIS task with an argv while it was alive (the
            # parking OPEN_PERM row, or an earlier row of the same burst). A row
            # whose own read missed the argv (GONE, or CMDLINE_UNREAD) still gets
            # a name -- next to its own evidence, in its own field, never
            # overwriting a measured value with a cached one. Matched by starttime
            # when that is known; by pid alone only when the task is already gone
            # (starttime unknowable), which is why argv_seen_start rides along and
            # lets a reader reject the join.
            argv_seen = argv_seen_start = None
            if ev["pid"]:
                if ev["astart"] is not None:
                    argv_seen = seen_argv.get((ev["pid"], ev["astart"]))
                    if argv_seen:
                        argv_seen_start = ev["astart"]
                else:
                    hit = pid_argv.get(ev["pid"])
                    if hit:
                        argv_seen_start, argv_seen = hit
            if aerr is None and (ev["cmd"] or "") == "" and ev["pstate"] != "Z":
                # Empty argv with a state that is not yet Z. exit_mm() empties
                # cmdline BEFORE the task is marked ZOMBIE, so for every task
                # that is not a kernel thread this shape is the exit window --
                # "reaped mid-read" when the pass-2 cgroup read also lost its
                # subject, and EXIT_WINDOW_UNSEEN when the cgroup survived.
                # Measured 2026-11-15T02:28:04 on a 15 B `cp`: CLOSE_WRITE row,
                # comm="cp", cmdline="", state=R, unit=hermes-gateway.service,
                # actor_err=null -- a row asserting a fact about a process that
                # no longer existed, left unlabelled because only the
                # ARGV_TORN_DOWN arm names the exit window and it needs an
                # earlier live read of the same task.
                aerr = empty_argv_label(ev["pstate"], uerr, ev["kthread"])
            emit({"ts": ev["ts"], "mono": ev["mono"],
                  "mask": ev["mtype"], "file": fname, "file_err": ferr,
                  "size": st.st_size if st else None,
                  "mtime": time.strftime("%Y-%m-%dT%H:%M:%S",
                                         time.localtime(st.st_mtime)) if st else None,
                  "actor_pid": ev["pid"], "actor_pidfd": ev["pidfd_pid"],
                  "actor_start": ev["astart"], "actor_state": ev["pstate"],
                  "actor_err": aerr,
                  "comm": ev["comm"],
                  "unit": uval, "unit_err": uerr,
                  # 1024, not 160: a hermes gateway wrapper's first 160 chars are
                  # the generic preamble (hermes-snap-<hash>.sh + AI_AGENT=...),
                  # which discriminates nothing between gateway children; the
                  # distinguishing argv is at the tail. The /proc read is free.
                  # Emitted only when this row's own read is a fact about the
                  # process (aerr null) or the name was recovered from the live
                  # read of the same task (ARGV_TORN_DOWN). Otherwise null, and
                  # actor_err says why -- so `cmdline: null` has exactly one
                  # reading again, and "" always means "read succeeded, argv
                  # empty". argv_seen carries the cached name when it exists.
                  "cmdline": (ev["cmd"][:1024] if aerr in (None, "ARGV_TORN_DOWN")
                              and ev["cmd"] is not None else None),
                  "argv_seen": argv_seen, "argv_seen_start": argv_seen_start})
            emissive += 1
    counts = {"closes": n_close, "opens": n_open, "self": self_n,
              "emitted": emissive, "unresolved": unresolved, "scan": n_scan,
              "marks": marks["armed"]}
    if stop_req["sig"]:
        emit(dict({"signal": stop_req["sig"],
                   "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, **counts))
    else:
        emit(dict({"stop": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, **counts))
    os.close(mount_fd)
    os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
