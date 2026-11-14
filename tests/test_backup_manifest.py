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
import re
import tempfile
import time
import unittest

from support import bus


def _sha(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


class BackupManifestTests(unittest.TestCase):
    def _run(self, hold=0.0, **over):
        """One bus lifetime whose shutdown writes the manifest."""
        bdir = tempfile.mkdtemp(prefix="fleet-bak-")
        over.setdefault("AGENT_CHAT_BACKUP_INTERVAL", "3600")
        with bus(AGENT_CHAT_BACKUP=bdir, **over) as (base, tmp, svc):
            db = os.path.join(tmp, "room.db")
            if hold:
                time.sleep(hold)
        return bdir, db, tmp

    def _rows(self, bdir):
        man = os.path.join(bdir, "room.db.bak.manifest.jsonl")
        self.assertTrue(os.path.exists(man), "no manifest written at shutdown")
        with open(man) as f:
            rows = [json.loads(l) for l in f if l.strip()]
        self.assertTrue(rows)
        return rows

    def test_shutdown_rows_carry_source_identity_and_hash(self):
        bdir, db, _tmp = self._run()
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
        bdir, _db, _tmp = self._run()
        wal = self._rows(bdir)[-1]["files"]["room.db-wal.bak"]
        # a clean shutdown checkpoints + TRUNCATEs, so either state is legal,
        # but the two must never be confused: absent carries no bytes key.
        if wal.get("state") == "absent":
            self.assertIsNone(wal["bytes"])
        else:
            self.assertEqual(wal["bytes"], 0)
            self.assertEqual(wal["sha256"], hashlib.sha256(b"").hexdigest())

    def test_slot_tokens_are_unique_and_own_their_pair(self):
        """Every copy slot has its own token, on its checkpoint lines and on its
        backup line, and its manifest row names the same src and pair.

        Pins the two defects this test exists for: a second-resolution token
        collides when slots run faster than the production interval (14 rows /
        6 tokens, one token holding 3 rows), and pairing rows to slots by
        position grades the ordering assumption the lock exists to remove.
        """
        bdir, _db, tmp = self._run(hold=3.6, AGENT_CHAT_BACKUP_INTERVAL="1")
        rows = self._rows(bdir)
        self.assertGreaterEqual(len(rows), 3, "rehearsal produced too few slots")
        slots = [r["slot"] for r in rows]
        self.assertEqual(len(set(slots)), len(slots), "slot token is not a key")
        with open(os.path.join(tmp, "bus-checkpoint.log")) as f:
            log = f.read().splitlines()
        for r in rows:
            mine = [l for l in log if "slot=" + r["slot"] in l]
            self.assertEqual(len(mine), 3, "token missing from its own slot: %r" % (mine,))
            self.assertTrue(mine[0].split(" ", 2)[2].startswith("checkpoint-pre:"))
            self.assertIn("src=%s" % r["src"], mine[1])
            copy = mine[2].split(" ", 2)[2]
            self.assertTrue(copy.startswith("backup:"), copy)
            toks = dict(re.findall(r"(main|wal)=([^ ]+)", copy))
            self.assertEqual((toks["main"], toks["wal"]), (r["main"], r["wal"]))
        self.assertEqual(rows[-1]["src"], "shutdown",
                         "shutdown must run as a slot, labelled src=shutdown")


if __name__ == "__main__":
    unittest.main()
