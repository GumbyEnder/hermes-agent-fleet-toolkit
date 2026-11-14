#!/usr/bin/env bash
# verify-slot-manifest.sh -- read-only audit of one bus backup slot against the
# per-slot manifest, the checkpoint-pre lines, and the artifacts on disk.
#
# Legs that are asserted (FAIL when they disagree):
#   bytes, sha256 of each copied artifact      [the row's own `path`]
#   src_ino in the row == st_ino of the live source (newest row only)
#   wal=copy:N  ==  the copied sidecar's size on disk   [the row's own `path`]
#
# PER-SLOT ARTIFACTS (#4268 point 2). Rows written from 2026-11-14 carry
# files.<base>.path = <base>.<slot>, a name nothing else writes, so bytes and
# sha256 stay re-assertable for as long as retention holds the file. Retention
# is measured in ARTIFACTS ON DISK, not manifest rows: `_prune_slot_artifacts`
# lists the directory and keeps the newest AGENT_CHAT_BACKUP_KEEP names per base,
# while a row whose copy failed (`skip:absent`, `copy:unreadable`) still appends
# a row and writes no file. Counting rows let a real deletion read as
# `INFO PRUNED by retention` once enough failed rows had accumulated (found by
# buzzy 2026-11-14). An artifact missing while fewer than KEEP newer artifacts
# exist on disk is a FAIL (a real deletion); outside retention it is
# `INFO <artifact> PRUNED`.
#
# LEGACY rolling rows (no `path`) are content-checkable only while they are the
# newest row: room.db.bak / room.db-wal.bak are single rolling names that each
# later slot overwrites -- measured 2026-11-14, slot 02:14:03 was PASS at 02:14
# and FAIL at 02:19 because the 02:18:00.814751 shutdown row had already
# overwritten the path. Those legs print `INFO <artifact> ROLLED since slot <S>`
# instead of a FAIL; a FAIL there always means the newest row and disk disagree.
#
# Legs that are reported but NOT asserted:
#   mtime  -- on this CIFS mount a copy2 lands with the source's mtime and the
#             server restamps the destination seconds later, so no destination
#             mtime is an identity leg. Rows written before 2026-11-14 record
#             `mtime_ns` (a post-copy stat) and will report a delta here; that
#             is the defect, not the slot.
#   frames -- the slot's checkpoint-pre `log=` is a FRAME count; wal=copy:N is
#             BYTES. They are not equal and never were. `log>0 with N==0` is
#             reported as FOLD-THEN-COPY: this arm folded the frames itself and
#             then copied the empty sidecar (proven live 2026-11-14 00:48:32).
#
# Usage: verify-slot-manifest.sh [slot-prefix]      (default: newest row)
set -u
BACKUP_DIR=${AGENT_CHAT_BACKUP:-/mnt/nas/agent-chat}
MAN=${BACKUP_DIR}/room.db.bak.manifest.jsonl
BUSLOG=${BUS_CHECKPOINT_LOG:-$HOME/agents/agent-chat/bus-checkpoint.log}
SLOT_FILTER=${1:-}
export AGENT_CHAT_BACKUP_KEEP=${AGENT_CHAT_BACKUP_KEEP:-24}

python3 - "$MAN" "$BACKUP_DIR" "$BUSLOG" "$SLOT_FILTER" <<'PY'
import hashlib, json, os, re, sys
man, bdir, buslog, want_slot = sys.argv[1:5]
KEEP = int(os.environ.get('AGENT_CHAT_BACKUP_KEEP', '24'))

def sha(p, chunk=1 << 20):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(chunk), b''):
            h.update(b)
    return h.hexdigest()

with open(man) as f:
    all_rows = [json.loads(l) for l in f if l.strip()]
rows = all_rows
if want_slot:
    rows = [r for r in rows if r['slot'].startswith(want_slot)]
if not rows:
    print('FAIL no manifest rows matched'); sys.exit(2)
row = rows[-1]
newest = row['slot'] == all_rows[-1]['slot']
newer = len(all_rows) - 1 - all_rows.index(row)      # rows written after this one

SLOT_RE = re.compile(r'^\d{8}T\d{6}\.\d{6}[+-]\d{4}$')
def newer_artifacts(base, slot):
    """Count EXISTING per-slot artifacts of `base` that sort after `slot`.

    Retention is decided by `_prune_slot_artifacts`, which lists the directory
    and keeps the newest KEEP names per base -- so the window is measured in
    artifacts on disk, never in manifest rows. A row whose copy failed still
    appends a row (`skip:absent`/`copy:unreadable`) and writes no artifact;
    counting rows would let a real deletion read as `INFO PRUNED`.
    """
    pref = base + '.'
    try:
        names = os.listdir(bdir)
    except OSError:
        return None
    after = {n[len(pref):] for n in names
             if n.startswith(pref) and SLOT_RE.match(n[len(pref):])
             and n[len(pref):] > slot}
    return len(after)
print('row slot=%s pid=%s src=%s main=%s wal=%s%s' %
      (row['slot'], row['pid'], row['src'], row['main'], row['wal'],
       '' if newest else ' (not newest; %d newer)' % newer))

fails = 0
def leg(name, ok, detail=''):
    global fails
    print(('PASS ' if ok else 'FAIL ') + name + (' ' + detail if detail else ''))
    if not ok:
        fails += 1
def info(name, detail=''):
    print('INFO ' + name + (' ' + detail if detail else ''))

def check_artifact(base, state, meta):
    """Assert bytes/sha256 against the artifact this row names."""
    global fails
    if state.startswith('skip'):
        leg('%s skipped-as-absent' % base, meta.get('state') == 'absent',
            'row says %s' % meta.get('state'))
        return
    path = meta.get('path')
    if path:
        dest = os.path.join(bdir, path)
        if not os.path.exists(dest):
            slot_of_path = path[len(base) + 1:] if path.startswith(base + '.') else ''
            n_art = (newer_artifacts(base, slot_of_path)
                     if SLOT_RE.match(slot_of_path) else None)
            if KEEP > 0 and n_art is not None and n_art >= KEEP:
                info('%s PRUNED by retention' % base,
                     'row names %s; %d newer artifacts ON DISK, keep=%d'
                     % (path, n_art, KEEP))
            else:
                leg('%s present' % base, False,
                    'row names %s -- missing INSIDE retention (%s newer artifacts '
                    'on disk, keep=%d)' % (path, n_art, KEEP))
            return
        st = os.stat(dest)
        h = sha(dest)
        leg('%s bytes' % base, st.st_size == meta.get('bytes'),
            'disk=%d row=%s path=%s' % (st.st_size, meta.get('bytes'), path))
        leg('%s sha256' % base, h == meta.get('sha256'),
            'disk=%s row=%s path=%s' % (h[:16], str(meta.get('sha256'))[:16], path))
    else:
        # legacy rolling name: only the newest row describes what is on disk now
        if not newest:
            info('%s ROLLED since slot %s' % (base, all_rows[-1]['slot']),
                 'recorded bytes=%s sha256=%s -- not re-assertable against a '
                 'single rolling path' % (meta.get('bytes'),
                                         str(meta.get('sha256'))[:16]))
            return
        dest = os.path.join(bdir, base)
        if not os.path.exists(dest):
            leg('%s present' % base, False, 'missing on disk'); return
        st = os.stat(dest)
        h = sha(dest)
        leg('%s bytes' % base, st.st_size == meta.get('bytes'),
            'disk=%d row=%s' % (st.st_size, meta.get('bytes')))
        leg('%s sha256' % base, h == meta.get('sha256'),
            'disk=%s row=%s' % (h[:16], str(meta.get('sha256'))[:16]))
    if 'mtime_ns' in meta:
        info('%s legacy mtime_ns (not an identity leg)' % base,
             'disk=%d row=%s delta_s=%.6f' % (st.st_mtime_ns, meta['mtime_ns'],
             (st.st_mtime_ns - meta['mtime_ns']) / 1e9))
    if 'src_mtime_ns' in meta:
        info('%s copy identity' % base,
             'src_mtime_ns=%s copy_time_ns=%s src_ino=%s' %
             (meta['src_mtime_ns'], meta.get('copy_time_ns'), meta.get('src_ino')))

src_db = row['db']
for base, state in (('room.db.bak', row['main']), ('room.db-wal.bak', row['wal'])):
    meta = row['files'].get(base, {})
    check_artifact(base, state, meta)
    if (base == 'room.db.bak' and newest and 'src_ino' in meta
            and os.path.exists(src_db)):
        leg('src_ino is the live source', os.stat(src_db).st_ino == meta.get('src_ino'),
            'live=%d row=%s' % (os.stat(src_db).st_ino, meta.get('src_ino')))

s = row['slot']
second = '%s-%s-%sT%s:%s:%s' % (s[0:4], s[4:6], s[6:8], s[9:11], s[11:13], s[13:15])
pre = {}
for line in open(buslog, errors='replace'):
    if second in line and 'checkpoint-pre' in line:
        if m := re.search(r'src=(\w+)', line):
            log = re.search(r'log=(-?\d+)', line)
            fold = re.search(r'folded=(-?\d+)', line)
            pre[m.group(1)] = (int(log.group(1)), int(fold.group(1)))
info('slot second %s checkpoint-pre' % second, str(pre or 'none'))

m = re.match(r'copy:(\d+)', row['wal'])
if m:
    n = int(m.group(1))
    wmeta = row['files'].get('room.db-wal.bak', {})
    wpath = wmeta.get('path')
    wslot = (wpath[len('room.db-wal.bak') + 1:]
             if wpath and wpath.startswith('room.db-wal.bak.') else '')
    w_newer = newer_artifacts('room.db-wal.bak', wslot) if SLOT_RE.match(wslot) else None
    if wpath:
        side = os.path.join(bdir, wpath)
        if os.path.exists(side):
            got = os.path.getsize(side)
            leg('wal copy:N == artifact size', got == n,
                'row N=%d disk=%d path=%s' % (n, got, wpath))
        elif KEEP > 0 and w_newer is not None and w_newer >= KEEP:
            info('wal copy:%d PRUNED by retention' % n,
                 'row names %s; %d newer artifacts on disk' % (wpath, w_newer))
        else:
            leg('wal artifact present', False, '%s missing inside retention' % wpath)
    elif not newest:
        info('wal copy:%d not re-assertable' % n,
             'sidecar rolled by slot %s' % all_rows[-1]['slot'])
    else:
        side = os.path.join(bdir, 'room.db-wal.bak')
        got = os.path.getsize(side) if os.path.exists(side) else -1
        leg('wal copy:N == artifact size', got == n, 'row N=%d disk=%d' % (n, got))
    if 'backup' in pre:
        frames = pre['backup'][0]
        if frames > 0 and n == 0:
            info('FOLD-THEN-COPY', 'pre log=%d frames folded by this arm, copied 0 B' % frames)
        elif frames == 0 and n == 0:
            info('copy of an empty sidecar', 'pre log=0 N=0')
elif row['wal'].startswith('skip'):
    if 'backup' in pre:
        leg('wal skip:absent agrees with pre', pre['backup'][0] <= 0,
            'pre log=%d' % pre['backup'][0])

print('RESULT %s (%d failed legs)' % ('PASS' if not fails else 'FAIL', fails))
sys.exit(0 if not fails else 1)
PY
