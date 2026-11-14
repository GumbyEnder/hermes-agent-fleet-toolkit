"""Backup manifest: identity is size+hash+src inode, never a post-copy dst mtime.

The defect this pins (measured 2026-11-14 on /mnt/nas): a copy2 lands with the
source's mtime and the CIFS server restamps the destination to its own close
time seconds later, so a row that records `os.stat(dst).st_mtime_ns` is right
at the instant of the copy and false when anyone reads it afterwards. The row
must therefore carry the SOURCE identity read under the same fd that supplied
the bytes, plus our own copy clock.
"""
import hashlib
import json
import os
import tempfile
import unittest

from support import bus


def _sha(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


class BackupManifestTests(unittest.TestCase):
    def _run(self):
        """One bus lifetime whose shutdown writes the manifest."""
        bdir = tempfile.mkdtemp(prefix="fleet-bak-")
        with bus(AGENT_CHAT_BACKUP=bdir, AGENT_CHAT_BACKUP_INTERVAL="3600") as (base, tmp, svc):
            db = os.path.join(tmp, "room.db")
        return bdir, db

    def _rows(self, bdir):
        man = os.path.join(bdir, "room.db.bak.manifest.jsonl")
        self.assertTrue(os.path.exists(man), "no manifest written at shutdown")
        with open(man) as f:
            rows = [json.loads(l) for l in f if l.strip()]
        self.assertTrue(rows)
        return rows

    def test_shutdown_rows_carry_source_identity_and_hash(self):
        bdir, db = self._run()
        row = self._rows(bdir)[-1]
        meta = row["files"]["room.db.bak"]
        dst = os.path.join(bdir, "room.db.bak")
        self.assertEqual(meta["bytes"], os.path.getsize(dst))
        self.assertEqual(meta["sha256"], _sha(dst))
        self.assertEqual(meta["src_ino"], os.stat(db).st_ino)
        self.assertIn("src_mtime_ns", meta)
        self.assertIn("copy_time_ns", meta)
        # the identity leg this test exists to keep retired
        self.assertNotIn("mtime_ns", meta)

    def test_absent_sidecar_is_recorded_absent_not_zero(self):
        bdir, _db = self._run()
        wal = self._rows(bdir)[-1]["files"]["room.db-wal.bak"]
        # a clean shutdown checkpoints + TRUNCATEs, so either state is legal,
        # but the two must never be confused: absent carries no bytes key.
        if wal.get("state") == "absent":
            self.assertIsNone(wal["bytes"])
        else:
            self.assertEqual(wal["bytes"], 0)
            self.assertEqual(wal["sha256"], hashlib.sha256(b"").hexdigest())


if __name__ == "__main__":
    unittest.main()
