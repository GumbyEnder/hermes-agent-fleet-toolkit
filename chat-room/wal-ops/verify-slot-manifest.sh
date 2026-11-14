#!/usr/bin/env bash
# verify-slot-manifest.sh -- read-only audit of one bus backup slot against the
# per-slot manifest, the checkpoint-pre lines, and the artifacts on disk.
#
# Legs that are asserted (FAIL when they disagree):
#   bytes, sha256 of each copied artifact   [newest row only -- see ROLLED]
#   src_ino in the row == st_ino of the live source (newest row only)
#   wal=copy:N  ==  the copied sidecar's size on disk   [newest row only]
#
# A row stops being content-checkable the moment the next slot writes.
# room.db.bak and room.db-wal.bak are single rolling names, so bytes/sha256/N
# describe a file that a later slot has overwritten; those legs print
# `INFO <artifact> ROLLED since slot <S>` instead of a FAIL. A FAIL on those
# legs therefore always means the newest row and the disk disagree.
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

python3 - "$MAN" "$BACKUP_DIR" "$BUSLOG" "$SLOT_FILTER" <<'PY'
import hashlib, json, os, re, sys
man, bdir, buslog, want_slot = sys.argv[1:5]

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
# room.db.bak / room.db-wal.bak are SINGLE ROLLING NAMES: every later slot
# overwrites them. So the content legs (bytes / sha256 / wal N) are only
# re-assertable while this row is the newest one in the manifest; for an older
# slot they would report the ROLL as a data defect. Same reason the src_ino leg
# is newest-only. Per-slot attestation needs per-slot artifact names
# (room.db.bak.<slot>), not a cleverer verifier.
newest = row['slot'] == all_rows[-1]['slot']
print('row slot=%s pid=%s src=%s main=%s wal=%s' %
      (row['slot'], row['pid'], row['src'], row['main'], row['wal']))

fails = 0
def leg(name, ok, detail=''):
    global fails
    print(('PASS ' if ok else 'FAIL ') + name + (' ' + detail if detail else ''))
    if not ok:
        fails += 1
def info(name, detail=''):
    print('INFO ' + name + (' ' + detail if detail else ''))

src_db = row['db']
for base, state in (('room.db.bak', row['main']), ('room.db-wal.bak', row['wal'])):
    dest = os.path.join(bdir, base)
    meta = row['files'].get(base, {})
    if state.startswith('skip'):
        leg('%s skipped-as-absent' % base, meta.get('state') == 'absent',
            'row says %s' % meta.get('state'))
        continue
    if not newest:
        info('%s ROLLED since slot %s' % (base, all_rows[-1]['slot']),
             'recorded bytes=%s sha256=%s -- not re-assertable against a '
             'single rolling path' % (meta.get('bytes'),
                                     str(meta.get('sha256'))[:16]))
        continue
    if not os.path.exists(dest):
        leg('%s present' % base, False, 'missing on disk'); continue
    st = os.stat(dest)
    leg('%s bytes' % base, st.st_size == meta.get('bytes'),
        'disk=%d row=%s' % (st.st_size, meta.get('bytes')))
    leg('%s sha256' % base, sha(dest) == meta.get('sha256'),
        'disk=%s row=%s' % (sha(dest)[:16], str(meta.get('sha256'))[:16]))
    if 'mtime_ns' in meta:
        info('%s legacy mtime_ns (not an identity leg)' % base,
             'disk=%d row=%s delta_s=%.6f' % (st.st_mtime_ns, meta['mtime_ns'],
             (st.st_mtime_ns - meta['mtime_ns']) / 1e9))
    if 'src_mtime_ns' in meta:
        info('%s copy identity' % base,
             'src_mtime_ns=%s copy_time_ns=%s src_ino=%s' %
             (meta['src_mtime_ns'], meta.get('copy_time_ns'), meta.get('src_ino')))
        if base == 'room.db.bak' and newest and os.path.exists(src_db):
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
    if not newest:
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
