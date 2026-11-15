#!/usr/bin/env python3
"""probe6 -- exact change_counter ladder for pre-WAL orderings, and proof that
VACUUM inside WAL moves the counter not at all (but does re-dirty page 1).

Promoted 2026-11-15 from ~/.hermes/profiles/buzzy/cache/scratch/wal-edges/probe6.py
(the path wal-forensics.md used to cite for the "baseline difference is ordering,
not VACUUM" arm) because scratch is pruned after a day idle: a cited probe has to
live in the repo. Code is verbatim; only this header note is new. It writes its
own temp root (`p6-*` beside this file) and removes it on the way out, so the
decode fixture it prints from is never a stored artifact -- cite the re-run.

Re-run 2026-11-15 from this directory (CPython/sqlite as printed by the caller):
rollback CREATE -> WAL 2, +VACUUM 3, +INSERT 3, +VACUUM+INSERT 4; WAL-first with
0/1/2/3 VACUUMs and a trailing insert = 3/5/7/9 frames, page-1 frame counters=[2]
throughout; decode frame 1 page_image[28:32]=2, frame 3 = 40.
"""
import os, sqlite3, struct, tempfile, shutil

PAGE = 4096; FHDR = 32; FRM = 24 + PAGE

def frames(db):
    p = db + '-wal'
    if not os.path.exists(p): return []
    b = open(p, 'rb').read(); n = (len(b) - FHDR) // FRM
    out = []
    for i in range(n):
        off = FHDR + i * FRM
        pg, dsz = struct.unpack('>II', b[off:off+8])
        out.append((pg, dsz, b[off+24:off+24+PAGE]))
    return out

def p1(img):
    return struct.unpack('>IIII', img[24:40])

def main_cc(path):
    raw = open(path, 'rb').read(PAGE)
    return struct.unpack('>IIII', raw[24:40])

root = tempfile.mkdtemp(dir=os.path.dirname(os.path.abspath(__file__)), prefix='p6-')

def ladder(name, pre):
    path = os.path.join(root, name.replace(' ', '_') + '.db')
    con = sqlite3.connect(path, isolation_level=None)
    con.execute('PRAGMA wal_autocheckpoint=0')
    pre(con)
    cc_before = main_cc(path)[0] if os.path.getsize(path) >= PAGE else None
    con.execute('PRAGMA journal_mode=WAL')
    con.close()
    cc_after = main_cc(path)[0]
    print('  %-40s main_cc_before_WAL=%-4s after_close=%-2d' % (name, cc_before, cc_after))

def sql(*stmts):
    def f(con):
        for s in stmts: con.execute(s)
    return f

CREATE = 'CREATE TABLE t(id INTEGER PRIMARY KEY, pad BLOB)'
INS = "INSERT INTO t(pad) VALUES('x')"
print('=== change_counter: rollback-mode writes, then WAL ===')
ladder('create', sql(CREATE))
ladder('create + vacuum', sql(CREATE, 'VACUUM'))
ladder('create + 2 vacuum', sql(CREATE, 'VACUUM', 'VACUUM'))
ladder('create + insert', sql(CREATE, INS))
ladder('create + vacuum + insert', sql(CREATE, 'VACUUM', INS))
ladder('create + insert + vacuum', sql(CREATE, INS, 'VACUUM'))
ladder('create + insert + 2 vacuum', sql(CREATE, INS, 'VACUUM', 'VACUUM'))

print('\n=== VACUUM inside WAL: counter and page-1 frames ===')
for nvac in (0, 1, 2, 3):
    path = os.path.join(root, 'walvac%d.db' % nvac)
    con = sqlite3.connect(path, isolation_level=None)
    con.execute('PRAGMA journal_mode=WAL'); con.execute('PRAGMA wal_autocheckpoint=0')
    con.execute(CREATE)
    for _ in range(nvac): con.execute('VACUUM')
    con.execute(INS)
    fr = frames(path)
    p1f = [p1(f[2]) for f in fr if f[0] == 1]
    print('  vacuums_in_wal=%d frames=%-3d page1_frames=%d counters=%s' % (
        nvac, len(fr), len(p1f), sorted({x[0] for x in p1f})))
    con.close()

print('\n=== page-image dbsize on every page-1 frame vs frame-header dbsize ===')
path = os.path.join(root, 'decode.db')
con = sqlite3.connect(path, isolation_level=None)
con.execute('PRAGMA journal_mode=WAL'); con.execute('PRAGMA wal_autocheckpoint=0')
con.execute('BEGIN'); con.execute(CREATE); con.execute('COMMIT')
con.execute('BEGIN')
for i in range(300): con.execute('INSERT INTO t(pad) VALUES(?)', (b'x'*500,))
con.execute('COMMIT')
fr = frames(path)
for i, (pg, hdr, img) in enumerate(fr, 1):
    if pg == 1:
        cc, dsz, ft, fc = p1(img)
        print('  frame %-2d pg=1 frame_header_dbsize=%-2d page_image[28:32]=%-2d last_frame=%s' % (
            i, hdr, dsz, i == len(fr)))
con.close()
shutil.rmtree(root, ignore_errors=True)
