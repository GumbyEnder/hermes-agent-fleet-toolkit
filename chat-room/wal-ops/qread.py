#!/usr/bin/env python3
"""qread.py -- read a SQLite db without leaving a sidecar pair behind.

    qread.py <db-path> "<sql>"        # prints rows, tab separated
    qread.py <db-path> --check        # max(id)/count/integrity/journal_mode

WHY: a `mode=ro` (or `sqlite3 -readonly`) open of a WAL-mode db whose -wal/-shm pair
is ABSENT manufactures 0 B -wal + 32,768 B -shm on the first SELECT and leaves both
after close. Nothing logs it, so every probe that watches the sidecars reads the
reader as a holder. Measured 2026-11-14 on scratch copies and on the live room db.

WHAT THIS DOES INSTEAD: a normal read-write handle with `PRAGMA query_only=1`, which
refuses writes at the VM level and closes clean -> pair ABSENT (measured, same run).
Use it for identity/count checks. For byte-exact copies use cp / sqlite3 .backup;
never a read-only open of a file you are not prepared to change.

Exit 2 on sqlite error.
"""
import sqlite3
import sys


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    path = sys.argv[1]
    check = len(sys.argv) == 3 and sys.argv[2] == "--check"
    sql = "select max(id), count(*) from messages" if check else (
        sys.argv[2] if len(sys.argv) > 2 else "pragma integrity_check")
    con = sqlite3.connect(path, timeout=10)
    try:
        con.execute("pragma query_only=1")
        if check:
            for pragma in ("pragma journal_mode", "pragma integrity_check"):
                print(pragma, "->", con.execute(pragma).fetchone())
        for row in con.execute(sql):
            print("\t".join("" if v is None else str(v) for v in row))
    except sqlite3.Error as e:
        print("sqlite error: %s" % e, file=sys.stderr)
        return 2
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
