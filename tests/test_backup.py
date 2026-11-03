#!/usr/bin/env python3
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host.backup import backup_path_for, receive_stream
from host.compat import Refused, quoted_path
from host.device import Nano
from host.safety import backup_filename_token


class ReceiveStreamTests(unittest.TestCase):
    def test_hashes_and_size(self):
        payload = b"A" * 4096 + b"B" * 1024
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "nano.img"
            digest = receive_stream([payload[:2000], payload[2000:]], dest, len(payload))
            self.assertEqual(dest.stat().st_size, len(payload))
            self.assertEqual(len(digest), 64)
            self.assertEqual(dest.read_bytes(), payload)

    def test_short_stream_refused(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "nano.img"
            with self.assertRaises(Refused):
                receive_stream([b"too-short"], dest, 99)
            self.assertFalse(dest.exists())

    def test_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "nano.img"
            dest.write_bytes(b"existing")
            with self.assertRaises(Refused):
                receive_stream([b"x"], dest, 1)


class BackupNameTests(unittest.TestCase):
    def test_product_string_is_sanitized(self):
        self.assertEqual(
            backup_filename_token("Apple iPod Media"), "Apple-iPod-Media"
        )
        nano = Nano(
            os_name="darwin",
            device="/dev/disk12",
            raw_device="/dev/rdisk12",
            partition="/dev/rdisk12s1",
            usb_serial="Apple iPod Media",
            size=64,
            fstype="hfs",
        )
        with tempfile.TemporaryDirectory() as td:
            from unittest import mock

            with mock.patch("host.backup.default_backup_dir", return_value=Path(td)):
                dest = backup_path_for(nano)
            self.assertNotIn(" ", dest.name)
            self.assertIn("Apple-iPod-Media", dest.name)
            spaced = Path(td) / "nano-Apple iPod Media-20261028T171800Z.img"
            quoted = quoted_path(spaced)
            self.assertTrue(quoted.startswith("'") or "\\" in quoted)
            self.assertIn("Apple", quoted)


if __name__ == "__main__":
    unittest.main()
