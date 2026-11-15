#!/usr/bin/env python3
"""E2E fixture for test-waiter-e2e.sh.

Runs as the MainPID of a throwaway transient user unit and spawns turn-shaped
children so live_turns() has something to count inside that unit's cgroup.

argv of every child matches the waiter's turn shape: "... -p frodo -z".
The orphan child double-forks, so the grandchild is reparented to systemd --user
(ppid != MainPID) while its /proc/<pid>/cgroup still names this unit -- the exact
divergence the cgroup predicate exists for.

usage: e2e-sink-spawner.py <healthy_n> <orphan_n> [sleep_s]
prints: MAINPID=<pid> HEALTHY=<n> ORPHAN=<n> then sleeps.
"""
import os
import sys
import time

# Optional: migrate this process into a foreign cgroup BEFORE forking, so the
# children inherit that membership. Without it a child spawned from the test
# shell stays in the shell's cgroup and the unit's cgroup never sees it.
_cg = os.environ.get("E2E_CGROUP")
if _cg:
    with open(_cg, "w") as fh:
        fh.write(str(os.getpid()))

healthy = int(sys.argv[1]) if len(sys.argv) > 1 else 0
orphan = int(sys.argv[2]) if len(sys.argv) > 2 else 0
dur = int(sys.argv[3]) if len(sys.argv) > 3 else 300

CHILD = (
    "import time,sys;"
    "d=float(sys.argv[1]);"
    "time.sleep(d)"
)
# Each child is: python3 -c CHILD <dur> -p frodo -z   (argv carries the turn shape)
def spawn_turn(dur_s):
    pid = os.fork()
    if pid == 0:
        os.execvp("python3", ["python3", "-c", CHILD, str(dur_s), "-p", "frodo", "-z"])
    return pid


def spawn_orphan(dur_s):
    """Double-fork: middle process exits immediately, grandchild is reparented."""
    pid = os.fork()
    if pid == 0:
        if os.fork() == 0:
            # grandchild: same argv shape, ppid becomes 1 / systemd --user
            os.execvp("python3", ["python3", "-c", CHILD, str(dur_s), "-p", "frodo", "-z"])
        os._exit(0)
    os.waitpid(pid, 0)
    return None


for _ in range(healthy):
    spawn_turn(dur)
for _ in range(orphan):
    spawn_orphan(dur)

print("MAINPID=%d HEALTHY=%d ORPHAN=%d" % (os.getpid(), healthy, orphan), flush=True)
time.sleep(dur + 60)
