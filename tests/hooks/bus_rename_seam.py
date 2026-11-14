#!/usr/bin/env python3
"""Run the real bus with one deliberate rename staged INSIDE the backup seam.

Why a wrapper instead of a flag on bus.py: a test-only branch in the production
copier would prove the branch, not the copier. This wrapper imports chat-room/bus.py
unchanged and wraps `os.fstat` -- the exact call that reads the source identity.
The wrapped call:

    1. calls the real fstat on the fd it was given,
    2. returns that stat to the caller,
    3. THEN renames the live DB out from under the path and plants a decoy with a
       different size and a different hash at the same path.

The rename therefore lands after the stat that answered "this fd is the live DB"
and before the copier reads a byte -- the gap buzzy named in §10c. A path-based
copier (`if os.path.exists(f)` -> `copy2(f, dst)`) opens the DECOY by name and
copies it; a descriptor-based copier reads the inode it already stat'ed.

SEAM_FLAG is written at the instant of the rename and carries the inode the
returned stat named, so the test can prove the seam was hit and that the manifest
row names the file that was actually copied.

Env: BUS_UNDER_TEST, AGENT_CHAT_DB (set by support.bus), SEAM_FLAG.
"""
import importlib.util
import os
import sys

BUS = os.environ["BUS_UNDER_TEST"]
DB = os.environ["AGENT_CHAT_DB"]
FLAG = os.environ["SEAM_FLAG"]

# Different size AND different hash from any real room.db: a bare size check
# must not be able to pass this test by accident.
DECOY = (b"decoy-after-the-stat: this is not the live database\n" * 4096)

spec = importlib.util.spec_from_file_location("bus_under_test", BUS)
assert spec is not None and spec.loader is not None
bus = importlib.util.module_from_spec(spec)
sys.modules["bus_under_test"] = bus
spec.loader.exec_module(bus)

_real_fstat = os.fstat
_real_getsize = os.path.getsize
_staged_ino = []
# SEAM_ON=fstat  : stage inside the copier's source stat (the shipped shape).
# SEAM_ON=getsize: stage after a path-based size probe, i.e. between the retired
#                  `os.path.exists(f)` and its `copy2(f, dst)`. Only used to
#                  rehearse the DEFECT against a pre-fix bus.py; the control
#                  proves the fixture can fail, not that the fix works.
SEAM_ON = os.environ.get("SEAM_ON", "fstat")


def _stage(st, trigger):
    os.rename(DB, DB + ".orig")
    with open(DB, "wb") as f:
        f.write(DECOY)
        f.flush()
        os.fsync(f.fileno())
    _staged_ino.append(st.st_ino)
    with open(FLAG, "w") as f:
        f.write("staged_after_stat ino=%d size=%d\n" % (st.st_ino, st.st_size))
    print("seam[%s]: renamed %s -> .orig after stat ino=%d, decoy planted"
          % (trigger, DB, st.st_ino), flush=True)


def fstat(fd):
    st = _real_fstat(fd)
    if not _staged_ino and SEAM_ON == "fstat":
        try:
            target = os.readlink("/proc/self/fd/%d" % fd)
        except OSError:
            target = None
        # /proc/self/fd on Linux reports the path the fd was opened through; a
        # same-second replacement of the name does not change it, which is the
        # whole point (the fd still names the old inode's path).
        if target and os.path.realpath(target) == os.path.realpath(DB):
            _stage(st, "fstat")
    return st


def getsize(path):
    st = _real_getsize(path)
    if not _staged_ino and SEAM_ON == "getsize":
        if os.path.realpath(str(path)) == os.path.realpath(DB):
            _stage(os.stat(path), "getsize")
    return st


os.fstat = fstat       # bus.py holds the same module object, so its lookup sees this
os.path.getsize = getsize
bus.main()
