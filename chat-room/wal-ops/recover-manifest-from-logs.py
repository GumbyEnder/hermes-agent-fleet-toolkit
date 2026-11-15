#!/usr/bin/env python3
"""recover-manifest-from-logs.py -- rebuild per-slot manifest rows from the
bus's own evidence after the live manifest was truncated.

WHY THIS EXISTS
  2026-11-14 21:55:34 -- a rehearsal harness (wal-ops/test-verify-skip-path-arm.sh,
  first revision) symlinked every room.db.bak.* name from the live backup dir
  into its shadow dir, INCLUDING room.db.bak.manifest.jsonl, then `cp`'d its
  crafted row onto that symlink. cp follows symlinks, so the write went THROUGH
  the link and replaced the live manifest: 33 rows -> 1. Nothing else wrote it;
  the bus only ever opens it "a".

WHAT CAN AND CANNOT BE RECOVERED
  Sources, both independent of the lost file:
    * $HOME/agents/agent-chat/bus-checkpoint.log -- one `backup: main=.. wal=..
      src=.. slot=..` line per row (state strings are the row's main/wal verbatim)
    * the artifacts on disk -- bytes, sha256, and the row's `path` basename
  Recoverable: slot, src, main, wal, files.<base>.{bytes,path,sha256?}
  NOT recoverable, and therefore OMITTED (never nulled, never invented):
    pid, src_ino, src_mtime_ns, copy_time_ns, and sha256 for any artifact that
    retention has already pruned.
  Rows are stamped "_recovered" with the fields that are missing, so no reader
  can mistake a rebuilt row for one the bus wrote.

USAGE
  recover-manifest-from-logs.py [--slots N] [--out FILE]
  Default: rebuild the newest 33 rows (the pre-truncation count, measured with
  wc -l at 21:44) and write them to
  /mnt/nas/agent-chat/room.db.bak.manifest.jsonl.recovered-<stamp>.
  It NEVER writes the live path. Restoring is a human/owner decision.
"""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime

BUSLOG = os.path.expanduser('~/agents/agent-chat/bus-checkpoint.log')
BDIR = '/mnt/nas/agent-chat'
DB = os.path.expanduser('~/agents/agent-chat/room.db')
BASES = ('room.db.bak', 'room.db-wal.bak')
SLOT_RE = re.compile(r'^\d{8}T\d{6}\.\d{6}[+-]\d{4}$')
REC = re.compile(r'backup: main=(\S+) wal=(\S+) src=(\S+) slot=(\S+)')
MISSING = ['pid', 'src_ino', 'src_mtime_ns', 'copy_time_ns']


def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--slots', type=int, default=33)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    stamp = datetime.now().astimezone().strftime('%Y%m%dT%H%M%S%z')
    out = a.out or os.path.join(
        BDIR, 'room.db.bak.manifest.jsonl.recovered-%s' % stamp)

    lines = []
    with open(BUSLOG, errors='replace') as f:
        for ln in f:
            m = REC.search(ln)
            if not m:
                continue
            main_s, wal_s, src, slot = m.groups()
            lines.append({'slot': slot, 'main': main_s, 'wal': wal_s, 'src': src})
    if not lines:
        sys.exit('no backup: lines in %s' % BUSLOG)
    rows = lines[-a.slots:]

    stats = {'rows': len(rows), 'sha_ok': 0, 'pruned': 0, 'skip_rows': 0}
    with open(out, 'w') as o:
        for r in rows:
            rec = {'db': DB, 'slot': r['slot'], 'src': r['src'],
                   'main': r['main'], 'wal': r['wal'], 'files': {}}
            for base, state in (('room.db.bak', r['main']),
                                ('room.db-wal.bak', r['wal'])):
                if state.startswith('skip'):
                    # bus.py writes exactly this for a skipped copy
                    rec['files'][base] = {'bytes': None, 'state': 'absent'}
                    stats['skip_rows'] += 1
                    continue
                mm = re.match(r'copy:(\d+)$', state)
                if not mm:
                    rec['files'][base] = {'bytes': None, 'state': state}
                    stats['skip_rows'] += 1
                    continue
                path = '%s.%s' % (base, r['slot'])
                ent = {'bytes': int(mm.group(1)), 'path': path}
                dest = os.path.join(BDIR, path)
                if os.path.exists(dest):
                    ent['sha256'] = sha(dest)
                    stats['sha_ok'] += 1
                else:
                    stats['pruned'] += 1
                rec['files'][base] = ent
            rec['_recovered'] = {'from': 'bus-checkpoint.log + on-disk artifacts',
                                 'not_recovered': MISSING
                                 + ['sha256 (pruned artifacts only)']}
            o.write(json.dumps(rec, sort_keys=True) + '\n')
    print('wrote %s' % out)
    print('rows=%d sha256_recovered=%d artifacts_pruned=%d skip_rows=%d'
          % (stats['rows'], stats['sha_ok'], stats['pruned'], stats['skip_rows']))
    print('window: %s .. %s' % (rows[0]['slot'], rows[-1]['slot']))


if __name__ == '__main__':
    main()
