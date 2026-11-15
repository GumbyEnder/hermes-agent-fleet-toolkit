#!/usr/bin/env python3
"""frodo independent re-derivation, 2026-11-15.
Six arms, fresh db each, CPython/sqlite printed at top. Promoted 2026-11-15 from
~/.hermes/profiles/frodo/cache/scratch/wal-edges/probe-f1.py (the path the
wal-forensics citation used to name) so the doc cites a tracked file, not scratch.

A1 ladder        : main-header bytes 24-27 (change counter) after close
A2 reading point : WAL-first CREATE + n VACUUMs, NO trailing insert
A3 switch        : fresh 0 B db -> journal_mode=WAL, instant snapshot
A4 refill        : DELETE-all + TRUNCATE, then 10-row refill at 5 sizes
A5 decode        : 42-frame group, pages [1..42]; the only nonzero frame-header
                   dbsize sits at index 41 (the 42nd frame) -- an index into the
                   group and the group's count are not the same number
A6 same arm      : + trailing INSERT = 3/5/7/9 frames, the extra frame is the
                   insert, not a VACUUM difference
"""
import os, sqlite3, struct, sys, tempfile, shutil

BASE = tempfile.mkdtemp(prefix="frodo-wal-f1-")


def db(name):
    return os.path.join(BASE, name + ".db")


def wal_frames(p):
    """[(offset, pgno, fh_dbsize, page_image)] from a WAL file."""
    b = open(p, "rb").read()
    if len(b) < 32:
        return []
    magic, ver, pgsz, ckpt, salt1, salt2, c1, c2 = struct.unpack(">8I", b[:32])
    out, off = [], 32
    while off + 24 + pgsz <= len(b):
        pgno, dbsz = struct.unpack(">2I", b[off:off + 8])
        out.append((off, pgno, dbsz, b[off + 24:off + 24 + pgsz]))
        off += 24 + pgsz
    return out


def hdr(blob, off):
    return struct.unpack(">I", blob[off:off + 4])[0]


def main_hdr(p):
    with open(p, "rb") as f:
        h = f.read(100)
    if len(h) < 100:
        return None
    return dict(cc=hdr(h, 24), dbsize=hdr(h, 28), fl_trunk=hdr(h, 32),
                fl_count=hdr(h, 36), cookie=hdr(h, 40),
                wver=h[18], rver=h[19], size=os.path.getsize(p))


def connect(name, wal_first=False):
    p = db(name)
    c = sqlite3.connect(p, isolation_level=None)
    if wal_first:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("CREATE TABLE t(a INTEGER)")
    return p, c


DDL = "CREATE TABLE t(a INTEGER, b BLOB)"


# ---------------- A1: ladder ----------------
def a1():
    print("\n== A1 ladder (main header bytes 24-27, read after close)")
    arms = [
        ("rollback CREATE -> WAL", ["CREATE"], False),
        ("CREATE+INSERT -> WAL", ["CREATE", "INSERT"], False),
        ("CREATE+VACUUM -> WAL", ["CREATE", "VACUUM"], False),
        ("CREATE+VACUUM+INSERT -> WAL", ["CREATE", "VACUUM", "INSERT"], False),
    ]
    for label, ops, walfirst in arms:
        p = db("a1_" + label.replace(" ", "_").replace("+", "_").replace(">", ""))
        c = sqlite3.connect(p, isolation_level=None)
        for op in ops:
            if op == "CREATE":
                c.execute(DDL)
            elif op == "INSERT":
                c.execute("INSERT INTO t(a,b) VALUES (1,x'00')")
            elif op == "VACUUM":
                c.execute("VACUUM")
        c.execute("PRAGMA journal_mode=WAL").fetchone()
        c.close()
        print(f"  {label:34s} cc={main_hdr(p)['cc']}")


# ---------------- A2: reading point ----------------
def a2():
    print("\n== A2 reading point: WAL-first CREATE + n VACUUMs, NO trailing INSERT")
    for n in range(4):
        p, c = connect(f"a2_{n}", wal_first=True)
        for _ in range(n):
            c.execute("VACUUM")
        wp = p + "-wal"
        frames = wal_frames(wp)
        pgs = [f[1] for f in frames]
        p1 = [f for f in frames if f[1] == 1]
        p1_cc = hdr(p1[-1][3], 24) if p1 else None
        live_main = main_hdr(p)
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        after = main_hdr(p)["cc"]
        print(f"  n={n} frames={len(frames):2d} pages={pgs} "
              f"page1_image_cc={p1_cc} main_cc_live={live_main['cc']} "
              f"main_cc_after_TRUNCATE={after} filesz={live_main['size']}")
        c.close()


# ---------------- A3: switch instant ----------------
def a3():
    print("\n== A3 switch instant: fresh 0 B db -> journal_mode=WAL")
    p = db("a3")
    open(p, "wb").close()
    print(f"  before: size={os.path.getsize(p)} wal_exists={os.path.exists(p+'-wal')}")
    c = sqlite3.connect(p, isolation_level=None)
    mode = c.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    h = main_hdr(p)
    print(f"  after switch: mode={mode} size={h['size']} cc={h['cc']} "
          f"wver={h['wver']} rver={h['rver']} dbsize={h['dbsize']} "
          f"wal_exists={os.path.exists(p+'-wal')}")
    c.execute(DDL)
    h = main_hdr(p)
    frames = wal_frames(p + "-wal")
    print(f"  after CREATE: wal_exists={os.path.exists(p+'-wal')} "
          f"wal_size={os.path.getsize(p+'-wal')} frames={len(frames)} "
          f"main_cc={h['cc']} p1_image_cc={hdr(frames[0][3],24) if frames else None}")
    c.close()
    h = main_hdr(p)
    print(f"  after close: main_cc={h['cc']} wal_exists={os.path.exists(p+'-wal')}")


# ---------------- A4: refill ----------------
def a4():
    print("\n== A4 refill after DELETE-all + wal_checkpoint(TRUNCATE), matched base")
    # one base: single transaction, 500 B rows, DELETE-all, TRUNCATE; copied per arm
    bp = db("a4_base")
    c = sqlite3.connect(bp, isolation_level=None)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE t(a INTEGER, b BLOB)")
    c.execute("BEGIN")
    for i in range(300):
        c.execute("INSERT INTO t(a,b) VALUES (?,?)", (i, b"x" * 500))
    c.execute("COMMIT")
    c.execute("DELETE FROM t")
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    fl_before = c.execute("PRAGMA freelist_count").fetchone()[0]
    pages = c.execute("PRAGMA page_count").fetchone()[0]
    c.close()
    for f in (bp + "-wal", bp + "-shm"):
        if os.path.exists(f):
            os.unlink(f)
    print(f"  base: page_count={pages} freelist_count={fl_before} "
          f"size={os.path.getsize(bp)}")
    for sz in (16, 100, 200, 500, 1000):
        p = db(f"a4_{sz}")
        shutil.copyfile(bp, p)
        c = sqlite3.connect(p, isolation_level=None)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("BEGIN")
        for i in range(10):
            c.execute("INSERT INTO t(a,b) VALUES (?,?)", (i, b"y" * sz))
        c.execute("COMMIT")
        frames = wal_frames(p + "-wal")
        pgs = [f[1] for f in frames]
        p1 = [f for f in frames if f[1] == 1]
        img_fl = hdr(p1[-1][3], 36) if p1 else None
        pragma_fl = c.execute("PRAGMA freelist_count").fetchone()[0]
        mh = main_hdr(p)
        print(f"  {sz:4d} B: frames={len(frames)} pages={pgs} "
              f"image_fl={img_fl} pragma_fl={pragma_fl} "
              f"main_bytes36-39={mh['fl_count']}")
        c.close()


# ---------------- A5: decode ----------------
def a5():
    print("\n== A5 decode: frame-header dbsize vs page image dbsize")
    p, c = connect("a5", wal_first=True)
    c.execute("DROP TABLE t")
    c.execute("CREATE TABLE t(a INTEGER, b BLOB)")
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    base = len(wal_frames(p + "-wal"))
    c.execute("BEGIN")
    for i in range(40):
        c.execute("INSERT INTO t(a,b) VALUES (?,?)", (i, b"z" * 4000))
    c.execute("COMMIT")
    frames = wal_frames(p + "-wal")[base:]
    print(f"  group frames={len(frames)} pages=[{frames[0][1]}..{frames[-1][1]}]")
    nz = [(i, f[1], f[2]) for i, f in enumerate(frames) if f[2] != 0]
    f0 = frames[0]
    print(f"  frame[0]: pgno={f0[1]} fh_dbsize={f0[2]} "
          f"page_image[28:32]={hdr(f0[3],28)}")
    print(f"  nonzero fh_dbsize in group: {nz}")
    c.close()


def a6():
    print("\n== A6 same arm WITH a trailing INSERT (buzzy probe6 shape)")
    for n in range(4):
        p, c = connect(f"a6_{n}", wal_first=True)
        for _ in range(n):
            c.execute("VACUUM")
        c.execute("INSERT INTO t(a) VALUES (7)")
        frames = wal_frames(p + "-wal")
        pgs = [f[1] for f in frames]
        p1 = [f for f in frames if f[1] == 1]
        print(f"  n={n} frames={len(frames):2d} pages={pgs} "
              f"page1_image_cc={hdr(p1[-1][3],24) if p1 else None} "
              f"main_cc_live={main_hdr(p)['cc']}")
        c.close()


print(f"CPython {sys.version.split()[0]} / sqlite {sqlite3.sqlite_version} / base={BASE}")
for fn in (a1, a2, a3, a4, a5, a6):
    fn()
print(f"\n(base kept: {BASE})")
