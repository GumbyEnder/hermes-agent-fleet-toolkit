#!/usr/bin/env python3
"""test-live-turns-cgroup.py -- proof that live_turns() counts by cgroup, not by ppid.

Why: :747 spawns every turn with start_new_session=True, so a turn outlives its
spawner. A turn orphaned by a restart is reparented to systemd --user (ppid 1616, not
MainPID) and is invisible to `pgrep -P <MainPID>` while still living in the unit's
cgroup. A waiter that counts by ppid sees 0 live turns and restarts over real work;
that is the defect this test exists to keep closed.

Method: spawn a turn-shaped process, orphan it (double fork), migrate it into the
dispatcher unit's cgroup, then assert three things in one run:
  A  healthy (no orphan): installed --count == ppid census == /health in_workers-ish baseline
  B  orphan in the cgroup: installed --count == baseline+1 AND ppid census == baseline
  C  orphan killed:        installed --count == ppid census == baseline
Exit 0 only if all three arms hold. Cleans up: kills the orphan, never touches the unit.

Run as the same user that owns the user manager:  python3 test-live-turns-cgroup.py
"""
import os, subprocess, sys, time, signal

SCRIPT = "/mnt/nas/host-ops/agent-chat/restart-dispatcher-when-no-turns.sh"
UNIT = "hermes-wake-dispatcher.service"
SHAPE = "python3 -I -c import sys, runpy; -p frodo -z hello"
PIDF = "/tmp/live-turns-cgroup-decoy.pid"
fails = []

def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()

def installed_count():
    return int(sh("bash", SCRIPT, "--count"))

def mainpid():
    return sh("systemctl", "--user", "show", "-p", "MainPID", "--value", UNIT)

def cg_of(pid):
    try:
        return open("/proc/%d/cgroup" % pid).read().strip().split("::")[-1]
    except FileNotFoundError:
        return ""

def leg(name, ok, detail):
    print("%-3s %-58s %s" % ("PASS" if ok else "FAIL", name, detail))
    if not ok:
        fails.append(name)

main = mainpid()
cgdir = "/sys/fs/cgroup" + cg_of(int(main))
print("dispatcher MainPID=%s  cgroup=%s" % (main, cgdir))
if not os.path.isdir(cgdir):
    print("CANNOT GRADE: unit cgroup not readable")
    sys.exit(2)

def ppid_census():
    re_ = r"-p [a-z-]+ -z|'-p', '[a-z-]+', '-z'"
    n = 0
    for pid in sh("pgrep", "-P", main).split():
        cmd = open("/proc/%s/cmdline" % pid, "rb").read().replace(b"\0", b" ").decode(errors="replace") \
            if os.path.exists("/proc/%s/cmdline" % pid) else ""
        import re
        if re.search(re_, cmd):
            n += 1
    return n

base = installed_count()
base_ppid = ppid_census()
leg("A healthy: installed --count == ppid census", base == base_ppid, "%d vs %d" % (base, base_ppid))

if os.path.exists(PIDF):
    os.unlink(PIDF)
if os.fork() == 0:
    os.setsid()
    if os.fork() == 0:
        try:
            open(PIDF, "w").write(str(os.getpid()))
            dn = os.open("/dev/null", os.O_WRONLY)
            os.dup2(dn, 1); os.dup2(dn, 2)
            os.execv("/bin/bash", ["bash", "-c", "exec -a %r sleep 240" % SHAPE])
        except Exception:
            os._exit(3)
    os._exit(0)
time.sleep(1.5)
decoy = int(open(PIDF).read())
try:
    leg("orphan: ppid is not MainPID", open("/proc/%d/stat" % decoy).read().split()[3] != main,
        "ppid=%s" % open("/proc/%d/stat" % decoy).read().split()[3])
    with open(cgdir + "/cgroup.procs", "w") as f:
        f.write(str(decoy))
    time.sleep(0.7)
    leg("orphan: now in the unit cgroup", cg_of(decoy) == cg_of(int(main)), cg_of(decoy))
    with_orphan = installed_count()
    ppid_with = ppid_census()
    leg("B cgroup leg counts the orphan (+1)", with_orphan == base + 1, "%d (base %d)" % (with_orphan, base))
    leg("B ppid leg is blind to it (unchanged)", ppid_with == base_ppid, "%d (base %d)" % (ppid_with, base_ppid))
finally:
    try:
        os.kill(decoy, signal.SIGKILL)
    except ProcessLookupError:
        pass
time.sleep(0.8)
leg("C after kill: installed --count back to base", installed_count() == base,
    "%d (base %d)" % (installed_count(), base))
leg("C after kill: ppid census back to base", ppid_census() == base_ppid,
    "%d (base %d)" % (ppid_census(), base_ppid))

print("\nRESULT %s (%d failed arms)" % ("PASS" if not fails else "FAIL", len(fails)))
sys.exit(0 if not fails else 1)
