#!/usr/bin/env python3
"""Discriminating tests for wal-unlink-watch-verdict.py's stall arm and --db-name.

Why each case exists:
  A. CHURN  — real filenames, 4152 B WAL (1 frame) churned every 0.4 s with a
              room.db write each cycle, so no unlink is ever older than ~0.45 s.
              With --stall-seconds 3 the frozen clock (emit()'s local last_delete)
              fires WAL-PINNED at ~3 s, 6 s, 9 s. A correct clock fires zero.
  B. PIN    — a real pin: one fd held open on a non-empty WAL, no deletes at all.
              PINNED must fire and must name the holder (holders=<pid>:..., owners=).
  C. DBNAME — --db-name other.db with a decoy room.db-wal that is never unlinked
              and non-empty. If the flag is ignored the decoy is the watched name
              and PINNED fires on it. Also asserts the IN_MODIFY cache is threaded
              per db name: a hardcoded SIDECARS tuple leaks " 0x2 " lines for
              other.db-wal.

Usage: test-wal-verdict-stall.py [path-to-instrument]
Exit 0 = all cases pass; prints a per-case PASS/FAIL with the evidence.
"""
import os, re, subprocess, sys, tempfile, time

INSTR = sys.argv[1] if len(sys.argv) > 1 else \
    "/mnt/nas/host-ops/agent-chat/wal-unlink-watch-verdict.py"
WAL_BYTES = 4152          # 32 B header + one 4120 B frame -> frames_of() == 1
results = []


def run_instr(d, log, stall=3.0, seconds=11, db_name=None, extra=None):
    cmd = [sys.executable, INSTR, "--dir", d, "--log", log,
           "--stall-seconds", str(stall), "--seconds", str(seconds),
           "--verdict-timeout", "1.0"]
    if db_name:
        cmd += ["--db-name", db_name]
    if extra:
        cmd += extra
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=seconds + 30)
    return p, (open(log, errors="replace").read() if os.path.exists(log) else "")


def lines_with(text, token):
    return [l for l in text.splitlines() if token in l]


def bus_line(d, line, age=0.0, db="room.db", tagged=True):
    """Append one line to the dir's bus-checkpoint.log, `age` seconds in the past.

    Same format bus.py writes SINCE 7cd60c0: ISO timestamp (local offset, no colon),
    space, `db=<path>`, space, the unchanged stdout line. The db= token is the whole
    point of that commit, so a fixture that omits it is testing a format no producer
    emits any more; `tagged=False` writes the pre-tee shape on purpose, and case H
    pins what a nameless line must grade as (unknown, never this database's evidence).
    Age is what the third leg grades on, so it must be real.
    """
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(time.time() + age))
    body = ("db=%s " % os.path.join(d, db)) if tagged else ""
    with open(os.path.join(d, "bus-checkpoint.log"), "a") as f:
        f.write("%s %s%s\n" % (ts, body, line))


def churn(d, wal, db, seconds, hold=0.30, gap=0.06):
    """Create a 1-frame WAL, hold it present most of the cycle, unlink, write db.

    hold must dominate the cycle: the stall arm samples the WAL at most once a
    second, so a WAL that only exists 50 ms per cycle is never seen and the test
    stops discriminating (measured: a frozen clock passed a 0.05 s hold).
    """
    t0 = time.time()
    cycles = 0
    while time.time() - t0 < seconds:
        with open(wal, "wb") as f:            # CLOSE_WRITE; size cached
            f.write(b"\0" * WAL_BYTES)
        time.sleep(hold)                      # window where the WAL is non-empty
        os.unlink(wal)                        # DELETE, last_size=4152
        with open(db, "ab") as f:             # room.db write, folds the queue
            os.utime(db, None)
        time.sleep(gap)
        cycles += 1
    return cycles


def case_churn():
    d = tempfile.mkdtemp(prefix="waltest-churn-")
    db = os.path.join(d, "room.db")
    wal = os.path.join(d, "room.db-wal")
    log = os.path.join(d, "out.log")
    with open(db, "wb") as f:
        f.write(b"\0" * 8192)
    p = subprocess.Popen(
        ["/usr/bin/python3", INSTR, "--dir", d, "--log", log,
         "--stall-seconds", "3", "--verdict-timeout", "1.0"], 
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cycles = churn(d, wal, db, 11)
    p.terminate(); p.wait(timeout=10)
    text = open(log, errors="replace").read()
    deletes = len(re.findall(r"DELETE\s+room\.db-wal", text))
    pins = lines_with(text, "WAL-PINNED")
    # WAL-IDLE is the discriminating token: with the holders gate in place a
    # frozen clock can no longer print PINNED on a churn (nothing holds the
    # file), it prints WAL-IDLE with no_delete_for == process uptime instead.
    idles = lines_with(text, "WAL-IDLE")
    ok = deletes > 15 and not pins and not idles
    results.append(("A churn: 0 pins, 0 idle", ok,
                    "cycles=%d deletes=%d pins=%d idle=%d %s" %
                    (cycles, deletes, len(pins), len(idles), (pins + idles)[:3])))


def case_genuine_pin():
    d = tempfile.mkdtemp(prefix="waltest-pin-")
    db = os.path.join(d, "room.db")
    wal = os.path.join(d, "room.db-wal")
    log = os.path.join(d, "out.log")
    with open(db, "wb") as f:
        f.write(b"\0" * 8192)
    with open(wal, "wb") as f:
        f.write(b"\0" * WAL_BYTES)
    # the third leg: sqlite REFUSED since the last unlink (there is none). The tuple
    # is the measured one from the real contended TRUNCATE at 12:23:01 (frodo #3855
    # take 2): busy=1 with 11 frames still unfolded and moved=0. The old fixture here
    # was `busy=0 log=1 folded=1 moved=0`, the shape a keeper produces every interval,
    # which is exactly the reading this arm must NOT call a pin (buzzy #3851).
    bus_line(d, "checkpoint: busy=1 log=11 folded=0 moved=0 src=backup", -5)
    held = open(wal, "rb")                    # the pin: an open fd, no deletes
    p, text = run_instr(d, log, stall=2.0, seconds=7)
    held.close()
    pins = lines_with(text, "WAL-PINNED")
    mypid = str(os.getpid())
    ok = bool(pins) and all(("holders=none" not in l) and (mypid in l)
                            and "truncate=src=backup" in l and "truncate=refused" not in l
                            for l in pins)
    results.append(("B genuine pin: PINNED names holder + refused TRUNCATE", ok,
                    "pins=%d %s" % (len(pins), pins[:2])))


def case_no_evidence():
    """Same pin, no bus tee: nothing asked for a removal, so it is not a pin."""
    d = tempfile.mkdtemp(prefix="waltest-noev-")
    db = os.path.join(d, "room.db")
    wal = os.path.join(d, "room.db-wal")
    log = os.path.join(d, "out.log")
    with open(db, "wb") as f:
        f.write(b"\0" * 8192)
    with open(wal, "wb") as f:
        f.write(b"\0" * WAL_BYTES)
    held = open(wal, "rb")
    p, text = run_instr(d, log, stall=2.0, seconds=7)
    held.close()
    pins = lines_with(text, "WAL-PINNED")
    idles = lines_with(text, "WAL-IDLE")
    ok = not pins and bool(idles) and all("truncate=none" in l for l in idles)
    results.append(("D no bus tee: 0 pins, WAL-IDLE truncate=none", ok,
                    "pins=%d idle=%d %s" % (len(pins), len(idles), (pins + idles)[:2])))


def case_busy_only():
    """The 08:35:47 janitor shape: busy=1 log=-1 is sqlite unable to run, not a pin."""
    d = tempfile.mkdtemp(prefix="waltest-busy-")
    db = os.path.join(d, "room.db")
    wal = os.path.join(d, "room.db-wal")
    log = os.path.join(d, "out.log")
    with open(db, "wb") as f:
        f.write(b"\0" * 8192)
    with open(wal, "wb") as f:
        f.write(b"\0" * WAL_BYTES)
    bus_line(d, "checkpoint: busy=1 log=-1 folded=-1 moved=0 src=janitor", -5)
    held = open(wal, "rb")
    p, text = run_instr(d, log, stall=2.0, seconds=7)
    held.close()
    pins = lines_with(text, "WAL-PINNED")
    idles = lines_with(text, "WAL-IDLE")
    ok = not pins and bool(idles) and all("truncate=busy" in l for l in idles)
    results.append(("E busy-only tee: 0 pins, WAL-IDLE truncate=busy", ok,
                    "pins=%d idle=%d %s" % (len(pins), len(idles), (pins + idles)[:2])))


def case_stale_and_unfolded():
    """A TRUNCATE too old to date this window, and one that left frames un-folded."""
    out = []
    for tag, line, extra, want in (
            ("F stale tee: nothing dates this window",
             "checkpoint: busy=0 log=1 folded=1 moved=0 src=janitor",
             ["--truncate-max-age", "1"], "truncate=none(stale"),
            ("G un-folded tee: the fold arm, not the unlink pin",
             "checkpoint: busy=0 log=9 folded=0 moved=0 src=janitor", [], "truncate=unfold")):
        d = tempfile.mkdtemp(prefix="waltest-" + tag.split()[0].lower() + "-")
        db = os.path.join(d, "room.db")
        wal = os.path.join(d, "room.db-wal")
        log = os.path.join(d, "out.log")
        with open(db, "wb") as f:
            f.write(b"\0" * 8192)
        with open(wal, "wb") as f:
            f.write(b"\0" * WAL_BYTES)
        bus_line(d, line, -240 if "stale" in tag.lower() else -5)
        held = open(wal, "rb")
        p, text = run_instr(d, log, stall=2.0, seconds=7, extra=extra)
        held.close()
        pins = lines_with(text, "WAL-PINNED")
        idles = lines_with(text, "WAL-IDLE")
        ok = not pins and bool(idles) and all(want in l for l in idles)
        out.append("%s pins=%d idle=%d" % (tag.split(":")[0], len(pins), len(idles)))
        results.append((tag, ok, "pins=%d idle=%d %s" % (len(pins), len(idles), (pins + idles)[:1])))
    return out


def case_db_name():
    d = tempfile.mkdtemp(prefix="waltest-dbname-")
    odb = os.path.join(d, "other.db")
    owal = os.path.join(d, "other.db-wal")
    decoy = os.path.join(d, "room.db-wal")
    log = os.path.join(d, "out.log")
    with open(odb, "wb") as f:
        f.write(b"\0" * 8192)
    with open(decoy, "wb") as f:              # never unlinked, non-empty
        f.write(b"\0" * WAL_BYTES)
    p = subprocess.Popen(
        ["/usr/bin/python3", INSTR, "--dir", d, "--log", log,
         "--stall-seconds", "3", "--verdict-timeout", "1.0", "--db-name", "other.db"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    churn(d, owal, odb, 11)
    p.terminate(); p.wait(timeout=10)
    text = open(log, errors="replace").read()
    owal_deletes = len(re.findall(r"DELETE\s+other\.db-wal", text))
    pins = lines_with(text, "WAL-PINNED")
    idles = lines_with(text, "WAL-IDLE")
    leak = [l for l in text.splitlines()
            if re.match(r"^\S+ 0x2\s+other\.db-wal\b", l)]
    ok = owal_deletes > 15 and not pins and not idles and not leak
    results.append(("C --db-name: watches other.db-wal, decoy quiet", ok,
                    "other_deletes=%d pins=%d idle=%d hardcoded-SIDECARS-leak=%d %s" %
                    (owal_deletes, len(pins), len(idles), len(leak), (pins + idles + leak)[:2])))


def case_unnamed_db():
    """A fresh checkpoint: line that names NO database. Unknown is not evidence.

    The production log was cleaned of pre-tee lines on 2026-11-13, so no producer
    emits this shape now -- which is exactly when it must be graded explicitly. With
    the db_name filter written as "skip if it names another db", a nameless line fell
    through and graded ('ran', ...) whenever the DELETE anchor was old enough to sit
    behind it: a rehearsal line silencing the alarm it was meant to raise. Expected
    here is WAL-IDLE with truncate=none(unnamed-db ...) -- fail closed, and say why.
    """
    d = tempfile.mkdtemp(prefix="waltest-unnamed-")
    db = os.path.join(d, "room.db")
    wal = os.path.join(d, "room.db-wal")
    log = os.path.join(d, "out.log")
    with open(db, "wb") as f:
        f.write(b"\0" * 8192)
    with open(wal, "wb") as f:
        f.write(b"\0" * WAL_BYTES)
    bus_line(d, "checkpoint: busy=0 log=1 folded=1 moved=0 src=janitor", -5, tagged=False)
    held = open(wal, "rb")
    p, text = run_instr(d, log, stall=2.0, seconds=7)
    held.close()
    pins = lines_with(text, "WAL-PINNED")
    idles = lines_with(text, "WAL-IDLE")
    ok = not pins and bool(idles) and all("truncate=none(unnamed-db" in l for l in idles)
    results.append(("H unnamed db= line: not this database's evidence", ok,
                    "pins=%d idle=%d %s" % (len(pins), len(idles), (pins + idles)[:1])))


def case_keeper_shape():
    """A permanently armed keeper: frames folded, sidecar alive, holder named.

    This is the live 12:15:19 tick (frodo #3853), verbatim: janitor `0|0|0 moved=44`
    (it drained 44 frames), backup `0|0|0 moved=0` a moment later (nothing left), and
    a holder on the sidecar that is NOT a pin. NOTE since 2026-11-13 14:37 this
    fixture's holder is a test process, not the keeper unit, so it grades
    `classes=<pid>:session-kernel` and `excluded=none`; the keeper-unit path is
    exercised by case M's --exclude-pid policy branch, not here. The old third leg graded `0|0|0` as a
    completed TRUNCATE and raised WAL-PINNED on this every interval -- buzzy measured
    exactly one such line at the 600 s threshold on 12:07:13 (his #3851). A healthy
    fold must grade WAL-IDLE, and the line must still name the holder so the reader
    can see who is keeping the file alive.
    """
    d = tempfile.mkdtemp(prefix="waltest-keeper-")
    db = os.path.join(d, "room.db")
    wal = os.path.join(d, "room.db-wal")
    log = os.path.join(d, "out.log")
    with open(db, "wb") as f:
        f.write(b"\0" * 8192)
    with open(wal, "wb") as f:
        f.write(b"\0" * WAL_BYTES)
    bus_line(d, "checkpoint: busy=0 log=0 folded=0 moved=44 src=janitor", -5)
    bus_line(d, "checkpoint: busy=0 log=0 folded=0 moved=0 src=backup", -4)
    held = open(wal, "rb")
    p, text = run_instr(d, log, stall=2.0, seconds=7)
    held.close()
    pins = lines_with(text, "WAL-PINNED")
    idles = lines_with(text, "WAL-IDLE")
    mypid = str(os.getpid())
    ok = (not pins and bool(idles)
          and all("truncate=ran" in l and mypid in l for l in idles))
    results.append(("K keeper shape: 0 pins, WAL-IDLE truncate=ran", ok,
                    "pins=%d idle=%d %s" % (len(pins), len(idles), (pins + idles)[:1])))


def case_exclusion():
    """A holder removed BY NAME POLICY must say so, and must not read as orphan frames.

    The keeper is a user unit; while it holds the sidecar no DELETE can occur, so a
    keeper-only hour used to print `holders=<pid>:4` and pose as a real pin (buzzy
    #3882 §4 named that as the cost of a permanent holder). The instrument now takes
    --exclude-unit / --exclude-pid, names what it removed in `excluded=`, and says
    what each live holder IS in `classes=`. Two assertions in one case, because the
    exemption is only meaningful if the same fixture still raises without it:

      1. no exclusion  -> holders=<pid> and NO excluded= naming it (the control)
      2. --exclude-pid -> holders=none, excluded=<pid>:..., classes=<pid>:...

    Without (1) a bug that excluded everything would pass (2) and hide real pins.
    Note the class token is not pinned: a test process has no systemd unit, so its
    cgroup unit is a session scope and the class may be session-kernel or other --
    the assertion is that the pid is named in excluded=, not what it is called.
    """
    d = tempfile.mkdtemp(prefix="waltest-excl-")
    db = os.path.join(d, "room.db")
    wal = os.path.join(d, "room.db-wal")
    with open(db, "wb") as f:
        f.write(b"\0" * 8192)
    with open(wal, "wb") as f:
        f.write(b"\0" * WAL_BYTES)
    bus_line(d, "checkpoint: busy=1 log=11 folded=0 moved=0 src=backup", -5)
    mypid = str(os.getpid())

    log0 = os.path.join(d, "control.log")
    held = open(wal, "rb")
    p, text0 = run_instr(d, log0, stall=2.0, seconds=7)
    held.close()
    ctrl_pins = lines_with(text0, "WAL-PINNED")
    ctrl_ok = bool(ctrl_pins) and all(mypid in l and "excluded=none" in l for l in ctrl_pins)

    log1 = os.path.join(d, "excluded.log")
    held = open(wal, "rb")
    p, text1 = run_instr(d, log1, stall=2.0, seconds=7, extra=["--exclude-pid", mypid])
    held.close()
    pins = lines_with(text1, "WAL-PINNED")
    idles = lines_with(text1, "WAL-IDLE")
    excl_ok = (not pins and bool(idles)
               and all("holders=none" in l and ("excluded=" + mypid + ":") in l
                       and ("classes=" + mypid + ":") in l for l in idles))
    ok = ctrl_ok and excl_ok
    results.append(("M excluded holder is named, not silently dropped", ok,
                    "control_pins=%d control_ok=%s excl_pins=%d excl_idle=%d excl_ok=%s %s" %
                    (len(ctrl_pins), ctrl_ok, len(pins), len(idles), excl_ok, (idles or pins)[:1])))


def case_unnamed_precedence():
    """A nameless line NEWER than the line that ends the read must survive in detail.

    The pre-delete / stale / shutdown branches return before the loop's `if
    saw_unnamed` tail, so a nameless producer writing AFTER the anchor was invisible:
    state stayed `none` (nothing silenced), but the log said "newest evidence for this
    db predates the unlink" when an untagged writer had written since (buzzy #3809
    C/D). Unit-level, because the shape needs an exact age ordering that a 7 s
    subprocess run cannot place. Asserts, in one place:
      1. nameless newer + tagged pre-anchor  -> none, detail ends " + unnamed-db"
      2. nameless newer + tagged stale       -> none, detail ends " + unnamed-db"
      3. nameless OLDER  + tagged pre-anchor -> none, NO suffix (no over-claim)
      4. nameless newer + graded `ran` line  -> still "ran" (no over-suppression)
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("walv_parent", INSTR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    now = time.time()

    def stamp(age):
        return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now - age))

    body = "checkpoint: busy=0 log=1 folded=1 moved=0 src=janitor"
    def line(age, tagged):
        d = "db=/x/room.db " if tagged else ""
        return "%s %s%s\n" % (stamp(age), d, body)

    def grade(lines, anchor_age):
        d = tempfile.mkdtemp(prefix="waltest-precedence-")
        p = os.path.join(d, "bus-checkpoint.log")
        with open(p, "w") as f:
            f.write("".join(lines))
        return mod.bus_truncate_evidence(p, now, now - anchor_age, 300, db_name="room.db")

    s1, d1 = grade([line(50, True), line(2, False)], 20)
    s2, d2 = grade([line(400, True), line(2, False)], 20)
    s3, d3 = grade([line(2, False), line(50, True)], 20)
    s4, d4 = grade([line(50, True), line(2, False)], 100)
    ok = (s1 == "none" and d1.endswith(" + unnamed-db")
          and s2 == "none" and d2.endswith(" + unnamed-db")
          and s3 == "none" and not d3.endswith(" + unnamed-db")
          and s4 == "ran")
    results.append(("L unnamed newer than anchor keeps its token", ok,
                    "1=%s|%s 2=%s|%s 3=%s|%s 4=%s|%s" % (s1, d1, s2, d2, s3, d3, s4, d4)))


if __name__ == "__main__":
    if not os.access(INSTR, os.R_OK):
        sys.exit("instrument not readable: " + INSTR)
    for fn in (case_churn, case_genuine_pin, case_no_evidence, case_busy_only,
               case_stale_and_unfolded, case_db_name, case_unnamed_db,
               case_keeper_shape, case_exclusion, case_unnamed_precedence):
        try:
            fn()
        except Exception as e:
            results.append((fn.__name__, False, "EXC %r" % (e,)))
    for name, ok, ev in results:
        print("%s  %-45s %s" % ("PASS" if ok else "FAIL", name, ev))
    sys.exit(0 if all(r[1] for r in results) else 1)
