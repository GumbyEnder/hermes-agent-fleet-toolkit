#!/usr/bin/env python3
"""Prove the unit's KillMode is what decides whether a restart reaches the gate.

`hermes -p P -z` is a gateway client, and it holds the per-profile flock for as
long as the turn lives. With systemd's default KillMode=control-group a
`systemctl restart` SIGTERMs the client, the flock is released, and the resumed
record can spawn a rival turn (buzzy/4205). KillMode=process stops the kill at
the dispatcher.

This starts two transient user services with identical process trees — a parent
that takes a flock and hands the fd to a child that sleeps — and restarts both,
then reports which child survived and whether its lock is still held. It touches
nothing belonging to the real dispatcher: `/tmp/probe-km-*` only, and the two
transient units are stopped at the end.

Run: python3 tests/probe-unit-killmode.py      (exits non-zero if the two modes
                                                behave the same)
"""
import fcntl
import os
import subprocess
import sys
import time

HOLDER = r'''
import fcntl, os, subprocess, sys, time
name = sys.argv[1]
fd = os.open("/tmp/probe-km-%s.lock" % name, os.O_RDWR | os.O_CREAT, 0o644)
fcntl.flock(fd, fcntl.LOCK_EX)
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"],
                         pass_fds=(fd,))
open("/tmp/probe-km-%s.child" % name, "w").write(str(child.pid))
open("/tmp/probe-km-%s.ready" % name, "w").write(str(os.getpid()))
time.sleep(300)
'''


def sh(*cmd, check=False):
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def lock_held(name):
    path = f"/tmp/probe-km-{name}.lock"
    if not os.path.exists(path):
        return None
    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


def holders(name):
    """Pids with `name`'s lock file open — the processes the gate actually covers."""
    path = f"/tmp/probe-km-{name}.lock"
    try:
        ino = os.stat(path).st_ino
    except FileNotFoundError:
        return set()
    out = set()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            for fd in os.listdir(f"/proc/{pid}/fd"):
                if os.stat(f"/proc/{pid}/fd/{fd}").st_ino == ino:
                    out.add(int(pid))
                    break
        except OSError:
            continue
    return out


def start(name, killmode):
    holder = f"/tmp/probe-km-{name}.py"
    with open(holder, "w") as fh:
        fh.write(HOLDER)
    for suffix in ("lock", "child", "ready"):
        try:
            os.unlink(f"/tmp/probe-km-{name}.{suffix}")
        except FileNotFoundError:
            pass
    nonce = os.getpid()
    unit = f"probe-km-{name}-{nonce}"
    r = sh("systemd-run", "--user", f"--unit={unit}",
           f"--property=KillMode={killmode}",
           "--property=Restart=no", sys.executable, holder, name)
    if r.returncode != 0:
        raise SystemExit(f"systemd-run failed for {name}: {r.stderr.strip()}")
    for _ in range(100):
        if os.path.exists(f"/tmp/probe-km-{name}.child"):
            with open(f"/tmp/probe-km-{name}.child") as fh:
                return unit, int(fh.read().strip())
        time.sleep(0.1)
    raise SystemExit(f"{name} never wrote its child pid")


def main():
    results = {}
    units = {}
    for name, killmode in (("ctl", "control-group"), ("proc", "process")):
        unit, child = start(name, killmode)
        units[name] = unit
        results[name] = {"child": child, "killmode": killmode}
    print("started:",
          ", ".join(f"{n}({r['killmode']}) child={r['child']}"
                    for n, r in results.items()))
    print("before restart: lock held ctl=%s proc=%s"
          % (lock_held("ctl"), lock_held("proc")))

    try:
        # `systemctl stop` is the event: with control-group it SIGTERMs every
        # process in the cgroup, with process only the main one. (A restart is
        # the same kill followed by a new generation, which would take the lock
        # again and hide the release this probe is looking for.)
        for name, unit in units.items():
            r = sh("systemctl", "--user", "stop", unit)
            if r.returncode != 0:
                raise SystemExit(f"stop {unit} failed: {r.stderr.strip()}")
        time.sleep(0.7)

        for name, rec in results.items():
            rec["child_alive"] = alive(rec["child"])
            rec["lock_held"] = lock_held(name)
            rec["holders"] = holders(name)
        print("after stop:")
        for name, rec in results.items():
            print(f"  {name:4s} KillMode={rec['killmode']:13s} "
                  f"child {rec['child']} alive={rec['child_alive']} "
                  f"lock_held={rec['lock_held']} holders={sorted(rec['holders'])}")

        ctl, proc = results["ctl"], results["proc"]
        ok = True
        if ctl["child_alive"] or ctl["lock_held"]:
            print("FAIL control-group: the stop left the client alive or its lock "
                  "held — nothing reproduces the release this probe is about")
            ok = False
        if not proc["child_alive"] or not proc["lock_held"]:
            print("FAIL process: the client did not survive the stop, so the gate "
                  "would be released while the turn runs on")
            ok = False
        if proc["child"] not in proc["holders"]:
            print("FAIL process: the lock is held, but not by the surviving "
                  "client pid — the gate is not on the process we think")
            ok = False
        print("PASS control-group kills the gate holder and frees the lock; "
              "process keeps the client, and the client keeps the lock"
              if ok else "PROBE FAILED")
        return 0 if ok else 1
    finally:
        for unit in units.values():
            sh("systemctl", "--user", "stop", unit)
            sh("systemctl", "--user", "reset-failed", unit)


if __name__ == "__main__":
    sys.exit(main())
