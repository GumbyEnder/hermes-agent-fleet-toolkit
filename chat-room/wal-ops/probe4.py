#!/usr/bin/env python3
"""probe4 -- independent re-derivation of the 'page 1 is not dirtied by a commit'
claim, on frodo's own instrument. Promoted 2026-11-15 from frodo's scratch
(wal-edges/probe4.py) because wal-forensics.md's decode rule cites its delete-all
group; scratch is pruned, so a cited probe has to live in the repo.

Arms (fresh db per arm, isolation_level=None, explicit BEGIN..COMMIT):
  A  VACUUM baseline -> 300 rows -> DELETE all -> 10-row refill
     For each commit: frames appended, page numbers in those frames,
     and page-1 header fields decoded from the frame's own page image.
     Asserts log == frames via PRAGMA wal_checkpoint(PASSIVE) on a COPY.

Nothing here reads probe3.json or probe3.py.
"""
import os, shutil, sqlite3, struct, tempfile, sys

PAGE = 4096
FHDR = 32          # wal header
FRM = 24 + PAGE    # frame header + page

def wal_frames(path):
    """-> list of (pgno, dbsize_after_commit, page_image) from WAL bytes."""
    b = open(path + '-wal', 'rb').read()
    n = (len(b) - FHDR) // FRM
    out = []
    for i in range(n):
        off = FHDR + i * FRM
        pgno, dbsz = struct.unpack('>II', b[off:off + 8])
        img = b[off + 24: off + 24 + PAGE]
        out.append((pgno, dbsz, img))
    return out

def hdr(img):
    """page-1 header fields, offsets from the start of the page image."""
    cc, = struct.unpack('>I', img[24:28])
    dsz, = struct.unpack('>I', img[28:32])
    ft, = struct.unpack('>I', img[32:36])
    fc, = struct.unpack('>I', img[36:40])
    return dict(change_counter=cc, dbsize_pages=dsz,
                freelist_trunk=ft, freelist_count=fc)

def wal_log_via_copy(db):
    """PRAGMA wal_checkpoint(PASSIVE) on a COPY -> (busy, log, checkpointed)."""
    d = tempfile.mkdtemp(dir=os.path.dirname(db))
    c = os.path.join(d, 'copy.db')
    shutil.copy2(db, c)
    if os.path.exists(db + '-wal'):
        shutil.copy2(db + '-wal', c + '-wal')
    if os.path.exists(db + '-shm'):
        shutil.copy2(db + '-shm', c + '-shm')
    con = sqlite3.connect(c)
    row = con.execute('PRAGMA wal_checkpoint(PASSIVE)').fetchone()
    con.close()
    shutil.rmtree(d, ignore_errors=True)
    return tuple(row)

def open_db(path):
    con = sqlite3.connect(path, isolation_level=None)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA wal_autocheckpoint=0')   # keep every frame until we look
    return con

def group(con, seen, label):
    """record the frames appended by the last committed group."""
    frames = wal_frames(db_path_of(con))
    new = frames[seen:]
    pages = [p for p, _, _ in new]
    p1 = [hdr(img) for p, _, img in new if p == 1]
    return dict(label=label, frames=len(frames), new=len(new), pages=pages,
                page1=p1), len(frames)

def db_path_of(con):
    for _, name, f in con.execute('PRAGMA database_list'):
        if name == 'main':
            return f

work = os.path.dirname(os.path.abspath(__file__))
root = tempfile.mkdtemp(dir=work, prefix='arm-')
res = {}

# ---- arm A: the refill arm, VACUUM baseline --------------------------------
db = os.path.join(root, 'a.db')
con = open_db(db)
con.execute('CREATE TABLE t(id INTEGER PRIMARY KEY, pad BLOB)')
con.execute('VACUUM')
seen = len(wal_frames(db))
rows = []

def ev(label):
    global seen
    r, seen = group(con, seen, label)
    rows.append(r)

con.execute('BEGIN')
for i in range(300):
    con.execute('INSERT INTO t(id,pad) VALUES(?,?)', (i, b'x' * 500))
con.execute('COMMIT'); ev('insert-300')

con.execute('BEGIN')
con.execute('DELETE FROM t')
con.execute('COMMIT'); ev('delete-all')

con.execute('BEGIN')
for i in range(10):
    con.execute('INSERT INTO t(id,pad) VALUES(?,?)', (1000 + i, b'y' * 500))
con.execute('COMMIT'); ev('refill-10')

# assertion: WAL log on a copy == frames we counted
log = wal_log_via_copy(db)
res['assert_log_eq_frames'] = (log[1] == seen, log, seen)
con.close()

for r in rows:
    print('A', r['label'], 'frames_total=%d new=%d pages=%s' % (r['frames'], r['new'], r['pages']))
    for h in r['page1']:
        print('   page1', h)
print('A assert log==frames:', res['assert_log_eq_frames'])

# ---- arm B: same arm WITHOUT the VACUUM baseline --------------------------
db2 = os.path.join(root, 'b.db')
con = open_db(db2)
con.execute('CREATE TABLE t(id INTEGER PRIMARY KEY, pad BLOB)')
seen = len(wal_frames(db2))
con.execute('BEGIN')
con.execute('INSERT INTO t(id,pad) VALUES(1,?)', (b'x' * 500,))
con.execute('COMMIT')
fr = wal_frames(db2)
p1 = [hdr(img) for p, _, img in fr if p == 1]
print('B no-vacuum baseline: frames=%d page1_frames=%d' % (len(fr), len(p1)))
for h in p1:
    print('   page1', h)
con.close()

shutil.rmtree(root, ignore_errors=True)
