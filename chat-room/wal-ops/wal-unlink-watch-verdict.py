#!/usr/bin/env python3
"""wal-unlink-watch.py + a deferred fold verdict + a WAL-pinned stall alarm.

Identical to /mnt/nas/host-ops/agent-chat/wal-unlink-watch.py except:

1. It caches each name's mtime as well as its size (`last_mtime`), as datetime.
2. A frames-bearing `DELETE room.db-wal` is queued instead of judged. The
   `fold=+<s>` age it used to print is measured at DELETE time, but room.db's
   IN_CLOSE_WRITE is delivered AFTER the sidecar's IN_DELETE, so the first
   frames-bearing delete of every burst printed the *previous* fold's age.
3. The verdict is decided when room.db's next CLOSE_WRITE arrives: `folded` iff
   room.db's mtime >= the WAL's own last mtime. A queue entry older than
   --verdict-timeout seconds with no such write is re-checked against room.db's
   mtime once more (`folded-late`) before it prints `unfolded`.
4. --stall-seconds (default 600): if no `DELETE room.db-wal` has been seen for
   that long while the sidecar exists and is non-empty, print one
   `WAL-PINNED room.db-wal ... holders=<pid:...>` line per stall window. A live
   reader pins the WAL so the last close's checkpoint returns busy and no unlink
   ever fires: the frames test has nothing to test, and the leak is silent.
   Holders are found by inode match across /proc/<pid>/fd (own uid only).
   The arm needs THREE legs, not two: frames live, no DELETE for stall-seconds,
   AND a `checkpoint:` line from the bus showing a TRUNCATE that completed
   (busy=0, folded==log) after that unlink. Without the third leg a merely
   un-truncated sidecar held by an idle reader grades as a pin -- measured
   08:30:56-08:34:56 on 11-13, 1,182,472 B, nothing had asked for its removal.
   The bus tees those lines to <dir>/bus-checkpoint.log (bus.py's
   AGENT_CHAT_BUS_CHECKPOINT_LOG) because this probe cannot read its stdout.

The old line format is unchanged, so existing readers keep working; the new lines
are `VERDICT     room.db-wal ... folded|folded-late|unfolded frames=<n> wal_mtime= db_mtime=`
and `WAL-PINNED  room.db-wal ...`.

Usage: wal-unlink-watch-verdict.py [--dir DIR] [--log FILE] [--seconds N] [--max-bytes N]
                                   [--verdict-timeout S] [--stall-seconds S]
"""
import argparse, ctypes, ctypes.util, datetime, os, re, select, shutil, struct, sys, time

IN_CREATE, IN_DELETE, IN_MOVED_FROM, IN_MOVED_TO, IN_DELETE_SELF, IN_ATTRIB = 0x100, 0x200, 0x40, 0x80, 0x400, 0x4
IN_CLOSE_WRITE, IN_OPEN, IN_MODIFY = 0x8, 0x20, 0x2
# IN_MODIFY is cache-only: it keeps last_size current on sub-second growth, so a
# re-created sidecar is not still recorded at size=0 when its DELETE lands 200 ms later.
MASK = IN_CREATE | IN_DELETE | IN_MOVED_FROM | IN_MOVED_TO | IN_CLOSE_WRITE | IN_ATTRIB | IN_MODIFY
NAMES = {IN_CREATE: "CREATE", IN_DELETE: "DELETE", IN_MOVED_FROM: "MOVED_FROM",
         IN_MOVED_TO: "MOVED_TO", IN_CLOSE_WRITE: "CLOSE_WRITE", IN_ATTRIB: "ATTRIB"}

libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
EVENT = struct.Struct("iIII")
SIDECARS = ("room.db-wal", "room.db-shm")


def frames_of(n):
    """WAL payload frames: 32 B header, then 24 B frame header + 4096 B page."""
    return (n - 32) // 4120 if n >= 32 else 0


def mtime_of(path):
    """Sidecar/DB mtime as a datetime. A float/string never gets compared here."""
    return datetime.datetime.fromtimestamp(os.stat(path).st_mtime)


def _owner_of(pid):
    """<comm>/<profile> for a pid, so a pin names its owner while it is still alive.

    Measured 2026-11-13: the 04:55-05:24 pin was named by holders= as
    2442406:5+8+10 but nothing said WHICH runtime owned it; it exited at 05:23:37
    and the question could only be closed by a later census of live kernels.
    HERMES_HOME's basename is the profile name when the holder is a Hermes kernel.
    """
    try:
        comm = open("/proc/%s/comm" % pid, "rb").read().strip().decode(errors="replace")
    except OSError:
        comm = "?"
    prof = "-"
    try:
        env = open("/proc/%s/environ" % pid, "rb").read()
        for kv in env.split(b"\0"):
            if kv.startswith(b"HERMES_HOME="):
                prof = os.path.basename(os.fsdecode(kv.split(b"=", 1)[1])).strip() or "-"
                break
    except OSError:
        pass
    return "%s/%s" % (comm or "?", prof)


def _unit_of(pid):
    """The systemd unit or scope that owns a pid, or '-' when it has none.

    Measured 2026-11-13 (frodo #3918): the keeper is a USER unit, so
    `systemctl is-active` on the system bus says inactive forever while the unit
    runs. The cgroup path is the only per-pid truth about which unit a holder
    belongs to, which is what an exclusion has to key on -- pid alone dies with
    the process, and a name alone survives a restart.
    """
    try:
        with open("/proc/%s/cgroup" % pid, "r", errors="replace") as f:
            for line in f:
                parts = line.rstrip("\n").split(":", 2)[-1].split("/")
                # DEEPEST match, not the first: every user unit sits under
                # user@1000.service, so taking the first component named
                # user@1000.service for every holder and no unit ever matches an
                # exclusion (measured 14:37 -- a keeper-shaped test unit graded as
                # user@1000.service and the exclusion silently did nothing).
                for part in reversed(parts):
                    if part.endswith(".service") or part.endswith(".scope"):
                        return part
    except OSError:
        pass
    return "-"


def _class_of(pid, unit, exclude_units):
    """Why this pid holds the sidecar: a named class, never just "a holder".

    Measured 2026-11-13: an armed keeper hour and a genuinely busy Hermes session
    produced the same `holders=<pid>:4` shape (12:34-13:24 every line named
    424048; 13:37-13:57 named 688734/778731 with the keeper stopped). Without
    classes the log cannot tell "the instrument was armed" from "a session kernel
    is a de-facto keeper", and both read as an anomaly (frodo #3918).

      keeper         unit matches an --exclude-unit pattern: excluded from pins
      bus            hermes-agent-chat-bus.service (the product connection)
      session-kernel a live Hermes kernel: HERMES_HOME set / hermes-agent cmdline
      other          anything else; the class that normally raises a real pin
    """
    if unit != "-" and any(p and p in unit for p in exclude_units):
        return "keeper"
    if "hermes-agent-chat-bus" in unit:
        return "bus"
    try:
        cl = open("/proc/%s/cmdline" % pid, "rb").read().decode(errors="replace")
    except OSError:
        cl = ""
    try:
        env = open("/proc/%s/environ" % pid, "rb").read()
    except OSError:
        env = b""
    if b"HERMES_HOME=" in env or "hermes-agent" in cl or "/.hermes/" in cl:
        return "session-kernel"
    return "other"


def holders_of(path, exclude_units=(), exclude_pids=()):
    """Live holders of this inode (own uid only), split into counted and excluded.

    Returns (holders, owners, excluded, classes):
      holders   `pid:fd+fd` for holders that COUNT as pins -- shape unchanged, so
                existing readers keep working, and "holders named" keeps meaning
                "a holder that could pin".
      owners    additive `pid:comm/profile@unit` for the same pids.
      excluded  `pid:class/comm@unit` for holders removed by name policy (a keeper
                unit, or an explicit --exclude-pid). Named, never silently
                dropped: an exclusion that hides itself is indistinguishable from
                a pin that did not happen (frodo #3918).
      classes   `pid:class` for every live holder, counted or excluded, so an
                armed hour (keeper) and a busy session (session-kernel) differ in
                the log instead of both looking like noise.

    The guard's OWN pid is never a holder: an instrument that names itself in the
    noise floor it exists to raise has no floor. Measured 2026-11-13 11:35: the
    only holder on room.db was the survey kernel doing the reading, not a product
    connection (buzzy #3830 §4).

    The 12:07 PINNED line named 303518/308079 -- two frodo python3 kernels -- and
    NOT the keeper. So a same-shaped line proves nothing about the keeper; only
    the unit of the named pid does (frodo #3918 correction 3).
    """
    try:
        st = os.stat(path)
    except OSError:
        return "none", "none", "none", "none"
    out, who, gone, cls = [], [], [], []
    uid = os.getuid()
    own = str(os.getpid())
    excl_pids = set(str(p) for p in exclude_pids)
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or pid == own:
            continue
        try:
            if os.stat("/proc/" + pid).st_uid != uid:
                continue
            base = "/proc/%s/fd" % pid
            fds = []
            for fd in os.listdir(base):
                try:
                    s = os.stat(os.path.join(base, fd))
                except OSError:
                    continue
                if s.st_ino == st.st_ino and s.st_dev == st.st_dev:
                    fds.append(fd)
            if not fds:
                continue
        except OSError:
            continue
        unit = _unit_of(pid)
        klass = _class_of(pid, unit, exclude_units)
        cls.append("%s:%s" % (pid, klass))
        if klass == "keeper" or pid in excl_pids:
            gone.append("%s:%s/%s@%s" % (pid, klass, _owner_of(pid), unit))
            continue
        out.append("%s:%s" % (pid, "+".join(fds)))
        who.append("%s:%s@%s" % (pid, _owner_of(pid), unit))
    return (",".join(out) or "none"), (",".join(who) or "none"), \
           (",".join(gone) or "none"), (",".join(cls) or "none")


BUS_LINE = re.compile(
    r"^(\S+)(?: db=(\S+))? checkpoint: busy=(-?\d+) log=(-?\d+) folded=(-?\d+) moved=(-?\d+) src=(\S+)")


def bus_truncate_evidence(path, now, last_delete, max_age, db_name=None):
    """Did a TRUNCATE actually run since the last unlink? (the stall arm's third leg)

    The stall arm used to fire on "frames present + no DELETE + a named holder".
    That is not enough: between two TRUNCATEs a healthy WAL is non-empty and can be
    held by an idle reader, so a merely un-truncated 1,182,472 B sidecar (measured
    08:30:56-08:34:56 on 11-13) looks identical to a refused removal. What separates
    them is whether a removal was REQUESTED after the last unlink, which only the bus
    witnesses. Returns (state, detail):

      ran    busy=0 and folded==log -- the TRUNCATE completed and folded everything
             foldable (covers 0|0|0, 0|66|66 and moved>0 alike). Nothing was left
             un-folded. NOT a pin: with a keeper holding the sidecar open this is the
             healthy every-interval state, so it now grades WAL-IDLE (it used to be
             the pin leg, which is what made the alarm noise -- buzzy #3851).
      refused busy>0 with frames still unfolded and moved==0: sqlite was refused, so
             nothing could be folded. This is the pin (measured 12:23:01, frodo
             #3855 take 2), and the only reading that raises WAL-PINNED.
      busy   busy=1 log=-1 -- sqlite could not run at all (a concurrent writer, not a
             read mark). Measured 08:35:47 from the janitor thread beside a healthy
             backup arm in the same process. Not evidence either way.
      unfold busy==0 and folded<log -- frames could not be folded. That is the fold
             alarm, a different arm; it is not an unlink pin and never becomes one here.
      none   no checkpoint: line after the last unlink within max_age. Nothing has
             asked sqlite to remove the file, so silence is correct, not a stall.
             With --db-name set, a line that names no database at all is reported
             as `unnamed-db` rather than folded into this case: it is unknown, not
             this database's evidence, and the token keeps the two apart in the log.
             A `src=shutdown` line that moved nothing is the same kind of non-evidence:
             a restart must not be able to silence the window after it.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "r", errors="replace") as f:
            f.seek(max(0, size - 256 * 1024))
            lines = f.readlines()
    except OSError:
        return "none", "no bus log"
    saw_unnamed = False

    def _done(state, detail):
        # A dated line that is NOT evidence (pre-delete / stale / shutdown) does not
        # prove nothing was written after it. Returning straight out of those branches
        # dropped the one fact the tag exists to preserve: a nameless producer wrote
        # after the anchor. State stays "none" -- nothing can silence the alarm -- so
        # this is a reader's cost, not a gate failure, but the log said "newest
        # evidence predates the unlink" while a nameless line sat after it
        # (buzzy #3809 C/D, probe-unnamed-precedence.py). Keep the suffix on every
        # "none" outcome so silence stays distinguishable from an untagged producer.
        if state == "none" and saw_unnamed:
            return "none", detail + " + unnamed-db"
        return state, detail

    for line in reversed(lines):
        m = BUS_LINE.match(line)
        if not m:
            continue
        # A line may name its database (bus.py's tee carries db=<path> since the
        # 09:57 pollution; measured 2026-11-13: a rehearsal pointed AGENT_CHAT_DB at
        # scratch but not the log, and 4 src=shutdown 0|0|0 lines landed in the
        # production file). A line about another database is not evidence about this
        # one -- grade it as absent, never as a TRUNCATE for room.db.
        #
        # "Names another database" is not the same test as "names one at all": a
        # line with NO db= token is UNKNOWN, not this database's. Measuring the
        # first form only, an archived pre-tee line graded ('ran', ...) when the
        # DELETE anchor was old enough to sit behind it -- i.e. a rehearsal line
        # could silence the very alarm the third leg exists to raise. So with a
        # db_name set, a nameless line is skipped AND remembered, so the log says
        # "unnamed-db" instead of a bare "no checkpoint: line": silence must stay
        # distinguishable from "the producer forgot its tag".
        if db_name:
            if not m.group(2):
                saw_unnamed = True
                continue
            if os.path.basename(m.group(2)) != db_name:
                continue
        try:
            ts = datetime.datetime.fromisoformat(m.group(1)).timestamp()
        except ValueError:
            continue
        busy, log, folded, moved = (int(m.group(i)) for i in (3, 4, 5, 6))
        detail = "src=%s busy=%d log=%d folded=%d moved=%d age=%ds" % (
            m.group(7), busy, log, folded, moved, int(now - ts))
        if ts <= last_delete:
            return _done("none", "pre-delete " + detail)
        if now - ts > max_age:
            return _done("none", "stale " + detail)
        # A shutdown checkpoint is not evidence about a refusal: it runs with no
        # traffic and folds what it can on the way down (measured 10:15:19/11:15:19,
        # src=shutdown 0|0|0). Grading it as "ran" would let a restart silence the
        # very window the alarm exists to judge (buzzy 3730 §3, still queued).
        if m.group(7) == "shutdown" and not moved:
            return _done("none", "shutdown " + detail)
        if busy and log < 0:
            return "busy", detail
        if folded < log:
            # THE PIN IS busy=1 WITH FRAMES STILL UNFOLDED, and only that: sqlite
            # could not start, so nothing was moved. Measured on the real contended
            # TRUNCATE at 12:23:01 (frodo #3855 take 2): `checkpoint: busy=1 log=11
            # folded=0 moved=0 src=backup`. A busy=0 line that leaves frames behind
            # (log>0, moved=0) is a partial fold, the fold arm's business, so it
            # stays "unfold" -- tests F/G pin both readings.
            #
            # What this deliberately does NOT call a pin: busy=0 with folded==log and
            # moved==0. That is an already-drained sidecar whose removal was refused,
            # and since a keeper holds the sidecar open by design it is also the
            # healthy steady state every interval -- measured 12:15:19 as the backup
            # arm's 0|0|0 moved=0 (frodo #3853). Grading it as a pin is how a
            # permanent keeper turns WAL-PINNED into background noise (buzzy #3851).
            if busy and moved == 0:
                return "refused", detail
            return "unfold", detail
        return "ran", detail
    if saw_unnamed:
        return "none", "unnamed-db checkpoint: line (not this database's evidence)"
    return "none", "no checkpoint: line"


def seed_last_delete(log_path, wal_name, now):
    """Anchor the stall clock to the newest unlink already recorded in the log.

    Anchoring on time.time() at start hands every restart a blind window as long as
    --stall-seconds. Measured: the sidecar was pinned 03:42:17 -> 04:03:10 on 11-13,
    the rule would have fired at 03:52:17, the watcher itself only started 03:55:57,
    so its earliest possible alert was 04:05:57 -- 2m47s after the WAL had already
    been unlinked. Result: zero WAL-PINNED lines in that window, i.e. the alarm
    looked like it had cleared the stall it never saw. Seeding from the log's own
    last DELETE keeps the clock continuous across restarts.
    """
    try:
        size = os.path.getsize(log_path)
        with open(log_path, "r", errors="replace") as f:
            f.seek(max(0, size - 4 * 1024 * 1024))
            tail = f.readlines()
    except OSError:
        return now, "now(no log)"
    pat = re.compile(r"^(\S+) DELETE\s+%s\s" % re.escape(wal_name))
    for line in reversed(tail):
        m = pat.match(line)
        if not m:
            continue
        try:
            ts = datetime.datetime.fromisoformat(m.group(1)).timestamp()
        except ValueError:
            continue
        return (min(ts, now), "log:" + m.group(1))
    return now, "now(no prior DELETE)"


def watch(target_dir, log_path, seconds=0, max_bytes=64000000, verdict_timeout=2.0, stall_seconds=600,
          fold_window=0.5, db_name="room.db", bus_log=None, truncate_max_age=7200.0,
          exclude_units=(), exclude_pids=()):
    fd = libc.inotify_init1(os.O_NONBLOCK)
    if fd < 0:
        raise OSError(ctypes.get_errno(), "inotify_init1")
    wd = libc.inotify_add_watch(fd, target_dir.encode(), MASK)
    if wd < 0:
        raise OSError(ctypes.get_errno(), "inotify_add_watch " + target_dir)
    # --db-name: the sidecar names derive from the db, never hardcoded
    wal_name = db_name + "-wal"
    shm_name = db_name + "-shm"

    last, last_mtime, last_seen = {}, {}, {}
    fold = {"ts": 0.0, "mtime": None, "size": 0}
    pending = []            # frames-bearing deletes awaiting a room.db write
    last_delete, last_delete_seed = seed_last_delete(log_path, wal_name, time.time())
    stalled_until = 0.0     # one WAL-PINNED line per stall window
    bus_log = bus_log or os.path.join(target_dir, "bus-checkpoint.log")
    # A seeded clock ("now(...)") is synthetic, so it cannot date a TRUNCATE: grading
    # against it would call every line already on disk "pre-delete" right after a
    # restart. Only a real DELETE record in the log is a floor.
    evidence_floor = 0.0 if last_delete_seed.startswith("now") else last_delete

    def rotate():
        try:
            if max_bytes and os.path.getsize(log_path) >= max_bytes:
                shutil.copyfile(log_path, log_path + ".1")
                open(log_path, "w").close()
        except OSError:
            pass

    def write(line):
        rotate()
        with open(log_path, "a") as f:
            f.write(line + "\n")
        print(line, flush=True)

    def now_iso():
        return datetime.datetime.now().isoformat(timespec="microseconds")

    log_base = os.path.basename(log_path)

    def emit(ev, name, extra=""):
        nonlocal last_delete, stalled_until
        if name and name in (log_base, log_base + ".1"):
            return                      # our own log: watching it makes a write loop
        p = os.path.join(target_dir, name) if name else ""
        state = extra
        if p:
            try:
                st = os.stat(p)
                mt = mtime_of(p)
                state = "size=%d mtime=%s" % (st.st_size, mt.isoformat())
                last[name] = st.st_size
                last_mtime[name] = mt
                last_seen[name] = time.time()
                if name == db_name and ev == IN_CLOSE_WRITE:
                    if mt != fold["mtime"] or st.st_size != fold["size"]:
                        fold.update(ts=time.time(), mtime=mt, size=st.st_size)
                    # resolve anything waiting on this write
                    for item in list(pending):
                        gap = time.time() - item["ts"]
                        if mt < item["wal_mtime"]:
                            verdict = "unfolded"
                        elif gap <= fold_window:
                            verdict = "folded"
                        else:
                            verdict = "folded-late"
                        write("%s VERDICT     %-28s %s frames=%d gap=%.3fs wal_mtime=%s db_mtime=%s" % (
                            now_iso(), item["name"], verdict, item["frames"], gap,
                            item["wal_mtime"].isoformat(), mt.isoformat()))
                        pending.remove(item)
            except FileNotFoundError:
                age = ("+%.2fs" % (time.time() - last_seen[name])) if name in last_seen else "unknown"
                state = "gone last_size=%s size_age=%s" % (last.get(name, "unknown"), age)
                if name == wal_name and ev == IN_DELETE:
                    last_delete = time.time()
                    stalled_until = 0.0
                if name == wal_name and ev == IN_DELETE and (last.get(name) or 0) > 0:
                    wm = last_mtime.get(name)
                    state += " fold=%s" % (("+%.3fs" % (time.time() - fold["ts"])) if fold["ts"] else "NONE")
                    n = frames_of(last[name])
                    if n and wm is not None:
                        pending.append({"ts": time.time(), "name": name, "frames": n,
                                        "last_size": last[name], "wal_mtime": wm})
        write("%s %-11s %-28s %s" % (now_iso(), NAMES.get(ev, hex(ev)), name or "-", state))

    write("%s WATCH %s mask=%s verdict_timeout=%ss fold_window=%ss stall_seconds=%ss last_delete_seed=%s bus_log=%s truncate_max_age=%ss exclude_units=%s exclude_pids=%s" % (
        now_iso(), target_dir, hex(MASK), verdict_timeout, fold_window, stall_seconds, last_delete_seed,
        bus_log, truncate_max_age, ",".join(exclude_units) or "none", ",".join(str(x) for x in exclude_pids) or "none"))
    deadline = time.time() + seconds if seconds else None
    buf = b""
    while True:
        if deadline and time.time() > deadline:
            return
        r, _, _ = select.select([fd], [], [], 1.0)
        # 1 Hz stat refresh: last_size/last_mtime only move on *watched* events, so a
        # sidecar that grows while its connection stays open (the external-unlinker
        # case, and the pinned-reader case) would be recorded at its old size.
        for nm in (wal_name, shm_name, db_name):
            try:
                st = os.stat(os.path.join(target_dir, nm))
                last[nm] = st.st_size
                last_mtime[nm] = datetime.datetime.fromtimestamp(st.st_mtime)
                last_seen[nm] = time.time()
            except OSError:
                pass
        now = time.time()
        # a frames-bearing delete whose room.db write never came: re-stat room.db
        # once before calling it unfolded, so a slow fsync is not a phantom.
        for item in list(pending):
            if now - item["ts"] > verdict_timeout:
                db_mtime = None
                try:
                    db_mtime = mtime_of(os.path.join(target_dir, db_name))
                except OSError:
                    pass
                if db_mtime is not None and db_mtime >= item["wal_mtime"]:
                    verdict = "folded-late"
                else:
                    verdict = "unfolded"
                write("%s VERDICT     %-28s %s frames=%d gap=%.3fs wal_mtime=%s db_mtime=%s" % (
                    now_iso(), item["name"], verdict, item["frames"], now - item["ts"],
                    item["wal_mtime"].isoformat(),
                    db_mtime.isoformat() if db_mtime else "none"))
                pending.remove(item)
        # no unlink for a long time while the sidecar is live and non-empty: the
        # last close's checkpoint is blocked by a reader, so the instrument sees
        # nothing at all. Name the holders.
        if stall_seconds > 0 and now > stalled_until:
            wal = os.path.join(target_dir, wal_name)
            try:
                wsize = os.stat(wal).st_size
            except OSError:
                wsize = 0
            ref = last_delete
            if frames_of(wsize) > 0 and now - ref > stall_seconds:
                hs, owners, excluded, classes = holders_of(wal, exclude_units, exclude_pids)
                state, detail = bus_truncate_evidence(bus_log, now, evidence_floor,
                                                      truncate_max_age, db_name)
                # THREE legs, all required. (a) frames are live, (b) no unlink has
                # been seen for stall_seconds, (c) sqlite was actually REFUSED since
                # that unlink -- measured as busy=1 with frames still unfolded
                # (frodo #3855 take 2, `busy=1 log=11 folded=0 moved=0`). "A TRUNCATE
                # completed" was the old third leg and it is exactly wrong once a
                # keeper is armed: the janitor keeps draining frames, the keeper keeps
                # the sidecar alive, so a completed fold with moved>0 recurs every
                # interval forever (12:15:19: 0|0|0 moved=44 then 0|0|0 moved=0).
                # holders=none is still not a pin: orphaned frames, no open fd.
                if state == "refused" and hs != "none":
                    write("%s WAL-PINNED  %-28s size=%d no_delete_for=%ds holders=%s owners=%s excluded=%s classes=%s truncate=%s" % (
                        now_iso(), wal_name, wsize, int(now - ref), hs, owners, excluded, classes, detail))
                else:
                    # holders=none after exclusions is NOT the same fact as holders=none
                    # with nobody there: excluded= names what was removed and classes=
                    # says what each live holder IS. A keeper-only hour therefore reads
                    # WAL-IDLE holders=none excluded=<pid>:keeper/... instead of posing
                    # as an orphan-frames pin (frodo #3918).
                    write("%s WAL-IDLE    %-28s size=%d no_delete_for=%ds holders=%s excluded=%s classes=%s truncate=%s(%s)" % (
                        now_iso(), wal_name, wsize, int(now - ref), hs, excluded, classes, state, detail))
                stalled_until = now + stall_seconds
        if not r:
            continue
        buf += os.read(fd, 65536)
        while len(buf) >= EVENT.size:
            wd_, mask, cookie, ln = EVENT.unpack_from(buf)
            name = buf[EVENT.size:EVENT.size + ln].rstrip(b"\0").decode(errors="replace")
            buf = buf[EVENT.size + ln:]
            if mask & IN_ATTRIB and not (mask & (IN_DELETE | IN_CREATE)):
                continue
            if mask & IN_MODIFY and name in (wal_name, shm_name):
                try:
                    st = os.stat(os.path.join(target_dir, name))
                    last[name] = st.st_size
                    last_mtime[name] = datetime.datetime.fromtimestamp(st.st_mtime)
                    last_seen[name] = time.time()
                except OSError:
                    pass
                continue                      # cache-only: no line, no verdict
            emit(mask, name)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.expanduser("~/agents/agent-chat"))
    ap.add_argument("--log", default="/mnt/nas/host-ops/agent-chat/wal-unlink-watch.log")
    ap.add_argument("--seconds", type=int, default=0, help="0 = run until killed")
    ap.add_argument("--max-bytes", type=int, default=64000000,
                    help="rotate the log to <log>.1 past this byte size (0 = never)")
    ap.add_argument("--verdict-timeout", type=float, default=2.0,
                    help="seconds to wait for room.db's fold before calling it unfolded")
    ap.add_argument("--fold-window", type=float, default=0.5,
                    help="a db write further than this from the unlink is folded-late, not folded")
    ap.add_argument("--db-name", default="room.db",
                    help="database basename inside --dir; -wal/-shm names derive from it")
    ap.add_argument("--stall-seconds", type=float, default=600.0,
                    help="alert WAL-PINNED when no -wal unlink is seen for this long (0 = off)")
    ap.add_argument("--bus-log", default=None,
                    help="bus.py's tee'd checkpoint lines (default <dir>/bus-checkpoint.log); "
                         "the stall arm's third leg reads it")
    ap.add_argument("--truncate-max-age", type=float, default=7200.0,
                    help="ignore a TRUNCATE line older than this when grading the stall arm")
    ap.add_argument("--exclude-unit", action="append", default=None,
                    help="a holder whose cgroup unit contains this string is named in "
                         "excluded= and never counts as a pin (default: the keeper unit). "
                         "Repeatable. An instrument must not pin on its own scaffolding.")
    ap.add_argument("--exclude-pid", action="append", type=int, default=None,
                    help="an exact pid to name in excluded= and never count as a pin")
    a = ap.parse_args()
    try:
        watch(a.dir, a.log, a.seconds, a.max_bytes, a.verdict_timeout, a.stall_seconds,
              a.fold_window, a.db_name, a.bus_log, a.truncate_max_age,
              exclude_units=a.exclude_unit or ["hermes-agent-chat-wal-keeper.service"],
              exclude_pids=a.exclude_pid or [])
    except KeyboardInterrupt:
        sys.exit(0)
