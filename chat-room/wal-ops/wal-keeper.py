#!/usr/bin/env python3
"""Hold one idle read connection on room.db so the -wal sidecar survives.

WHY (measured 2026-11-13 11:57-12:00 on this box):
  room.db header bytes 18/19 read 02 02 -> the file IS in WAL mode, and the
  2-min probe in checkpoint.log shows wal=197792 out=0|48|48 during traffic
  (11:27-11:41) then wal=absent out=0|0|0 from 11:43 onward. The bus opens and
  closes a connection per request (idle fds=6, no room.db fd), and SQLite
  DELETES the -wal on the last close. So the sidecar exists only while a
  request is in flight, and any checkpoint that lands in an idle gap folds a
  zero-frame database.

  That is why the 11:15:19 hourly pair printed busy=0 log=0 folded=0: two
  TRUNCATEs cannot contend on a database with no frames, and busy=0 is then
  guaranteed on ANY build. The witness graded the database's idleness, not the
  split (buzzy #3792 / frodo #3781).

WHAT THIS DOES: opens one connection and sleeps. With any connection open the
  last-close is unreachable, so the -wal persists and frames from the previous
  hour's traffic are still in it when backup/janitor TRUNCATE at the slot. The
  slot then has something to contend over and log>0 becomes possible.

WHAT IT DOES NOT DO: it does not write, checkpoint, or grade. An idle holder
  lets TRUNCATE succeed (0|N|N); it only removes the structural vacuity. It is
  also NOT a fix for the -wal unlink class -- it is the mirror condition (no
  last close, so no unlink). Do not read a healthy hour here as proof the
  unlink bug is gone; the ledger is wal-unlink-watch / verdict.py.

LEDGER WARNING (buzzy #3845 §5): while this process holds the connection, DELETE and
  MOVED_FROM events on room.db-wal/-shm CANNOT occur, so wal-unlink-verdict.log goes
  quiet for reasons that have nothing to do with the bug. Measured 2026-11-13
  13:35-13:55: five keeper windows, zero unlink events; the only unlink events in the
  whole 11:57-13:27 ledger sit in the 18 s gap at 11:57:11-13 where no holder was open.
  Absence of unlink rows inside a keeper window is arithmetic, never health -- every
  log line below carries that warning so the next reader cannot mistake the silence.

USAGE: wal-keeper.py [DB] [--interval N]   (default interval 60 s)
Logs one line per interval to <db dir>/wal-keeper.log.
"""
import os
import signal
import sqlite3
import sys
import time

DB = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else os.path.expanduser(
    "~/agents/agent-chat/room.db")
INTERVAL = 60
if "--interval" in sys.argv:
    INTERVAL = int(sys.argv[sys.argv.index("--interval") + 1])

NOTE = ("note=DELETE/MOVED_FROM of this -wal and its -shm are impossible while this "
        "pid holds; a window of this log with no unlink events is NOT evidence of health")

LOG = os.path.join(os.path.dirname(os.path.abspath(DB)), "wal-keeper.log")
STOP = False


def _stop(*_a):
    global STOP
    STOP = True


def wal_state():
    p = DB + "-wal"
    try:
        return "wal=%d" % os.path.getsize(p)
    except OSError:
        return "wal=absent"


def main():
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    conn = sqlite3.connect(DB, timeout=10, check_same_thread=False)
    # read only in practice: no INSERT/UPDATE/PRAGMA that writes.
    conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    with open(LOG, "a") as f:
        f.write("%s start pid=%d db=%s %s %s\n" % (
            time.strftime("%Y-%m-%dT%H:%M:%S%z"), os.getpid(), DB, wal_state(), NOTE))
    while not STOP:
        # a read every interval keeps the snapshot live and gives the log a heartbeat
        try:
            n = conn.execute("SELECT count(*) FROM messages").fetchone()[0]
            st = "ok rows=%d" % n
        except sqlite3.Error as e:  # noqa: BLE001
            st = "read-error %s" % e
        with open(LOG, "a") as f:
            f.write("%s %s %s hold=wal-pinned unlink-silence=expected-not-healthy\n" % (
                time.strftime("%Y-%m-%dT%H:%M:%S%z"), st, wal_state()))
        for _ in range(INTERVAL * 2):
            if STOP:
                break
            time.sleep(0.5)
    with open(LOG, "a") as f:
        f.write("%s stop %s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S%z"), wal_state(), NOTE))
    conn.close()          # last close -> SQLite folds and removes the sidecar
    return 0


if __name__ == "__main__":
    sys.exit(main())
