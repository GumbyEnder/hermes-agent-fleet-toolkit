#!/usr/bin/env python3
"""Prove the watcher's named-holder exclusion and holder classes against real fds.

Five scenarios, each with a live holder that really has room.db-wal open:
  A  holder runs in a systemd unit matching --exclude-unit  -> excluded=, no PINNED
  B  same holder, no --exclude-unit                        -> PINNED names the pid
  C  plain python3 holder with HERMES_HOME (a session kernel) -> classed, PINNED
  D  --exclude-pid names the holder                        -> excluded=, no PINNED
  E  holder is its OWN systemd unit (systemd-run --user), no HERMES_HOME, plain
     `python3 holder.py room.db` (relative argv, so no /.hermes/ in the cmdline)
     -> classes=<pid>:other and WAL-PINNED. This is the only case that exercises
     the class that raises a real pin; B and C inherit hermes-gateway.service and
     grade session-kernel, so without E the alarm branch has zero evidence
     (frodo #3974).
Every scenario feeds a `busy=1 log=11 folded=0 moved=0` bus line, i.e. the
refused-TRUNCATE leg, so A's silent outcome can only be the exclusion working.
"""
import datetime, os, sqlite3, subprocess, sys, time

WATCHER = "/mnt/nas/host-ops/agent-chat/wal-unlink-watch-verdict.py"
BASE = os.environ.get("BH_AGENT_WORKSPACE") or "/tmp"
ROOT = os.path.join(BASE, "holder-excl-test")
HOLDER = r'''
import sqlite3, sys, time
db = sys.argv[1]
c = sqlite3.connect(db, timeout=5)
c.execute("pragma journal_mode=wal")
c.execute("create table if not exists t(x blob)")
c.executemany("insert into t values(?)", [(b"x" * 4000,)] * 40)
c.commit()
sys.stdout.write("held\n"); sys.stdout.flush()
time.sleep(180)
'''


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw)


def make_case(name, exclude_flag=None, hermes_home=False, exclude_own_pid=False,
              own_unit=False):
    d = os.path.join(ROOT, name)
    sh("rm -rf %s && mkdir -p %s" % (d, d))
    holder_py = os.path.join(d, "holder.py")
    open(holder_py, "w").write(HOLDER)
    unit = "bztest-holder-%s-%d" % (name, int(time.time()))
    env_prefix = ("HERMES_HOME=/home/gumbyender/.hermes/profiles/buzzy " if hermes_home else "")
    if exclude_flag or own_unit:
        if own_unit:
            # Its own unit, and the environment is scrubbed rather than emptied:
            # _class_of keys on b"HERMES_HOME=" being PRESENT, so -E HERMES_HOME=
            # would still grade session-kernel. `env -u` removes the token.
            # Relative argv keeps the fixture path (which may sit under
            # .../.hermes/...) out of the cmdline, because _class_of also matches
            # "/.hermes/" there; the shape under test is a plain `python3 holder.py`.
            start = ("systemd-run --user --unit=%s --collect --working-directory=%s "
                     "env -u HERMES_HOME python3 holder.py room.db" % (unit, d))
        else:
            start = "systemd-run --user --unit=%s --collect %spython3 %s %s/room.db" % (
                unit, env_prefix, holder_py, d)
        started = sh(start)
        if started.returncode != 0:
            print("SKIP %s: %s" % (name, started.stderr.strip()[:200]))
            return None
    else:
        p = subprocess.Popen(["bash", "-c", "%spython3 %s %s/room.db" % (env_prefix, holder_py, d)],
                             stdout=subprocess.PIPE, text=True)
        p.stdout.readline()
        unit = "none(plain pid %d)" % p.pid
    time.sleep(1.5)
    wal = os.path.join(d, "room.db-wal")
    size = os.path.getsize(wal) if os.path.exists(wal) else 0
    frames = (size - 32) // 4120 if size >= 32 else 0
    now = datetime.datetime.now().astimezone()
    # bus fixture: a refused TRUNCATE, dated now, naming this database
    with open(os.path.join(d, "bus.log"), "w") as f:
        f.write("%s db=%s/room.db checkpoint: busy=1 log=11 folded=0 moved=0 src=backup\n"
                % (now.isoformat(), d))
    # watcher's own log carries a DELETE anchor 60 s old, so the stall window is open
    old = (now - datetime.timedelta(seconds=60)).isoformat()
    with open(os.path.join(d, "watch.log"), "w") as f:
        f.write("%s DELETE      room.db-wal                  gone last_size=%d\n" % (old, size))
    held = p.pid if not (exclude_flag or own_unit) else int(
        sh("systemctl --user show -p MainPID --value %s" % unit).stdout.strip() or 0)
    cmd = ("python3 %s --dir %s --log %s/watch.log --bus-log %s/bus.log "
           "--stall-seconds 5 --seconds 12 --db-name room.db" % (WATCHER, d, d, d))
    if exclude_flag:
        cmd += " --exclude-unit %s" % exclude_flag
    if exclude_own_pid:
        cmd += " --exclude-pid %d" % held
    run = sh(cmd, timeout=60)
    lines = [l for l in open(os.path.join(d, "watch.log")).read().splitlines()
             if " WAL-" in l]
    sh("pkill -f 'holder.py %s/room.db'" % d)
    sh("systemctl --user stop %s; systemctl --user reset-failed %s" % (unit, unit))
    return {"name": name, "unit": unit, "pid": held, "frames": frames, "lines": lines,
            "rc": run.returncode, "err": run.stderr[-400:]}


cases = [
    ("A_excluded", "bztest-holder", False),
    ("B_unexcluded", None, False),
    ("C_session_kernel", None, True),
    ("D_excluded_pid", None, False),
]
results = []
for name, excl, hh in cases:
    # D needs the holder pid before the watcher starts, so start it twice: the
    # first run only reports the pid it held with.
    r = make_case(name, excl, hh, exclude_own_pid=(name == "D_excluded_pid"))
    results.append(r)
    print("=== %s (holder unit=%s, frames=%s)" % (name, r and r["unit"], r and r["frames"]))
    if not r:
        continue
    for l in r["lines"]:
        print("   ", l)
    if not r["lines"]:
        print("    NO WAL LINE; rc=%s err=%s" % (r["rc"], r["err"]))
# E: its own transient unit, no HERMES_HOME, plain `python3 holder.py room.db`.
e = make_case("E_plain_other", None, False, own_unit=True)
results.append(e)
print("=== E_plain_other (holder unit=%s, frames=%s)" % (e and e["unit"], e and e["frames"]))
if e:
    for l in e["lines"]:
        print("   ", l)
    if not e["lines"]:
        print("    NO WAL LINE; rc=%s err=%s" % (e["rc"], e["err"]))

def one(name):
    for r in results:
        if r and r["name"] == name:
            return r
    return None

a, b, c = one("A_excluded"), one("B_unexcluded"), one("C_session_kernel")
checks = []
if a and a["lines"]:
    l = a["lines"][-1]
    checks.append(("A WAL-IDLE not PINNED", "WAL-IDLE" in l))
    checks.append(("A excluded names keeper class", "excluded=none" not in l and "keeper" in l))
    checks.append(("A holders=none after exclusion", "holders=none" in l))
    checks.append(("A classes names keeper", ":keeper" in l))
if b and b["lines"]:
    l = b["lines"][-1]
    checks.append(("B PINNED fires without exclusion", "WAL-PINNED" in l))
    checks.append(("B PINNED names the holder pid", ("%d" % b["pid"]) in l))
d = one("D_excluded_pid")
if d and d["lines"]:
    l = d["lines"][-1]
    checks.append(("D --exclude-pid stops the pin", "WAL-IDLE" in l))
    checks.append(("D excluded names the pid + class", ("excluded=%d:" % d["pid"]) in l or ("excluded=" in l and str(d["pid"]) in l)))
if c and c["lines"]:
    l = c["lines"][-1]
    checks.append(("C session kernel classed", "session-kernel" in l))
    checks.append(("C still counted as a pin", "WAL-PINNED" in l and "holders=none" not in l))
# E is the alarm branch: class=other is the only class that raises a real pin.
ee = one("E_plain_other")
if not ee:
    checks.append(("E own-unit holder started", False))
elif ee["lines"]:
    l = ee["lines"][-1]
    checks.append(("E PINNED fires for class=other", "WAL-PINNED" in l))
    checks.append(("E classes names pid:other", ("%d:other" % ee["pid"]) in l))
    checks.append(("E not misgraded session-kernel", ("%d:session-kernel" % ee["pid"]) not in l))
    checks.append(("E holders names the pid", ("holders=%d:" % ee["pid"]) in l))
    checks.append(("E excluded=none (nothing removed)", "excluded=none" in l))
else:
    checks.append(("E produced a WAL line", False))
print()
for k, ok in checks:
    print("%-42s %s" % (k, "PASS" if ok else "FAIL"))
sys.exit(0 if all(ok for _, ok in checks) else 1)
