#!/usr/bin/env python3
import os
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host.compat import Refused, sha256_file
from host.const import FIRMWARE_MSE_SIZE, FIRMWARE_MSE_SHA256, RESCUE_MSE_SHA256, TESTED_DISK_SIZE
from host.safety import check_backup, check_mse


class BackupAndMseTests(unittest.TestCase):
    def test_backup_happy_path(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "nano.img"
            data = b"ipod-backup-fixture"
            p.write_bytes(data)
            digest = sha256_file(p)
            self.assertEqual(check_backup(p, digest, len(data)), digest)

    def test_backup_size_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "nano.img"
            p.write_bytes(b"abc")
            digest = sha256_file(p)
            with self.assertRaises(Refused) as ctx:
                check_backup(p, digest, 99)
            self.assertIn("backup size", str(ctx.exception))

    def test_backup_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "nano.img"
            p.write_bytes(b"abc")
            with self.assertRaises(Refused) as ctx:
                check_backup(p, "0" * 64, 3)
            self.assertIn("hash mismatch", str(ctx.exception))

    def test_backup_symlink_refused(self):
        with tempfile.TemporaryDirectory() as td:
            real = Path(td) / "real.img"
            link = Path(td) / "link.img"
            real.write_bytes(b"abc")
            os.symlink(real, link)
            with self.assertRaises(Refused) as ctx:
                check_backup(link, sha256_file(real), 3)
            self.assertIn("symlink", str(ctx.exception))

    def test_mse_missing(self):
        with self.assertRaises(Refused) as ctx:
            check_mse(Path("/no/such/firmware.MSE"))
        self.assertIn("missing", str(ctx.exception))

    def test_mse_wrong_size(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "Firmware.MSE"
            p.write_bytes(b"short")
            with self.assertRaises(Refused) as ctx:
                check_mse(p)
            self.assertIn("size", str(ctx.exception))

    def test_const_sizes_line_up(self):
        self.assertEqual(FIRMWARE_MSE_SIZE, 193114112)
        self.assertEqual(len(FIRMWARE_MSE_SHA256), 64)
        self.assertEqual(len(RESCUE_MSE_SHA256), 64)
        self.assertEqual(TESTED_DISK_SIZE, 15_798_411_264)


if __name__ == "__main__":
    unittest.main()
