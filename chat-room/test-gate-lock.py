#!/usr/bin/env python3
"""Prove the per-profile turn gate survives a dispatcher restart.

Reproduces the 2026-11-13 18:58:40 game race in miniature:
  1. "dispatcher A" acquires the profile lock and spawns a turn child that
     inherits the fd (exactly what _run_one does with pass_fds).
  2. A is SIGKILLed while the child is still alive — the orphan case.
  3. "dispatcher B" (fresh process, empty in-memory gate) tries to acquire:
     it must FAIL, i.e. no rival turn under the same sender name.
  4. The child exits; B must then acquire.

Run: python3 test-gate-lock.py
"""
import os
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

LOCK_DIR = os.environ.get("WAKE_LOCK_DIR") or tempfile.mkdtemp(prefix="gate-lock-test-")
os.environ["WAKE_LOCK_DIR"] = LOCK_DIR

import wake_dispatcher as wd  # noqa: E402  (module-level config only; main() not run)

wd.LOCK_DIR = LOCK_DIR

SLEEPER = "import time; time.sleep(120)"


def hold_speaking(mode):
    """Child process body: 'hold' acquires and orphans a turn; 'try' probes."""
    fd, holder = wd._acquire_profile_lock("game")
    if mode == "hold":
        # The turn child inherits the fd, so the lock outlives this process.
        if fd is None:
            print(f"HOLD_FAILED holder={holder!r}", flush=True)
            sys.exit(2)
        child = subprocess.Popen([sys.executable, "-c", SLEEPER], pass_fds=(fd,))
        print(f"HOLD acquired fd={fd} child={child.pid}", flush=True)
        time.sleep(120)
        return
    if fd is None:
        print(f"TRY_BLOCKED holder={holder!r}", flush=True)
        sys.exit(0)
    print(f"TRY_ACQUIRED fd={fd}", flush=True)
    os.close(fd)


def run(mode, timeout=10):
    p = subprocess.run([sys.executable, os.path.abspath(__file__), mode],
                       capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def main():
    if len(sys.argv) > 1:
        hold_speaking(sys.argv[1])
        return

    print("lock dir:", LOCK_DIR)
    a = subprocess.Popen([sys.executable, os.path.abspath(__file__), "hold"],
                         stdout=subprocess.PIPE, text=True)
    line = a.stdout.readline().strip()
    print("A:", line)
    child_pid = int(line.rsplit("=", 1)[1])

    # 2. kill the dispatcher, leave the turn child alive (the orphan case)
    a.kill()
    a.wait()
    print("A killed; orphan turn child still alive:", os.path.exists(f"/proc/{child_pid}"))

    # 3. fresh process, empty gate — must be blocked by the orphan's fd
    rc, out, err = run("try")
    print("B while orphan lives:", out or err)
    blocked = out.startswith("TRY_BLOCKED")
    print("PASS gate blocked by orphan" if blocked else "FAIL gate opened under a live orphan")

    # 4. orphan exits -> lock free
    os.kill(child_pid, signal.SIGTERM)
    for _ in range(50):
        if not os.path.exists(f"/proc/{child_pid}"):
            break
        time.sleep(0.1)
    time.sleep(0.3)
    rc, out, err = run("try")
    print("B after orphan exit:", out or err)
    reopened = out.startswith("TRY_ACQUIRED")
    print("PASS gate reopened" if reopened else "FAIL gate stuck after orphan exit")

    print("RESULT:", "PASS" if (blocked and reopened) else "FAIL")
    sys.exit(0 if (blocked and reopened) else 1)


if __name__ == "__main__":
    main()
