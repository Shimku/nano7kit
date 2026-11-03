#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host.compat import Refused, sha256_file
from host.const import IPODREPARTITION_DISK_SIZE, TESTED_DISK_SIZE, sha256sums, artifact
from host.layout import DiskLayout, Partition
from host.safety import check_disk_size, describe_size, verify_release_artifacts


class DiskSizeTests(unittest.TestCase):
    def test_tested_size_passes(self):
        info = check_disk_size(TESTED_DISK_SIZE)
        self.assertTrue(info["hardware_tested"])

    def test_unknown_size_refused(self):
        with self.assertRaises(Refused) as ctx:
            check_disk_size(12345)
        msg = str(ctx.exception)
        self.assertIn("REFUSED", msg)
        self.assertIn("--allow-disk-size 12345", msg)
        self.assertIn("--confirm-untested-capacity", msg)

    def test_override_requires_both_flags(self):
        with self.assertRaises(Refused):
            check_disk_size(12345, allow_size=12345, confirm_untested=False)
        with self.assertRaises(Refused):
            check_disk_size(12345, allow_size=999, confirm_untested=True)
        info = check_disk_size(12345, allow_size=12345, confirm_untested=True)
        self.assertTrue(info.get("override"))
        self.assertFalse(info["hardware_tested"])

    def test_size_zero_is_never_a_disk(self):
        from host.safety import require_usable_disk_size, usable_disk_size

        self.assertFalse(usable_disk_size(0))
        self.assertFalse(usable_disk_size(None))
        self.assertFalse(usable_disk_size(-1))
        self.assertTrue(usable_disk_size(64))
        self.assertTrue(usable_disk_size(512))
        with self.assertRaises(Refused) as ctx:
            check_disk_size(0)
        msg = str(ctx.exception)
        self.assertIn("never 0 bytes", msg)
        self.assertNotIn("device not in recovery diskmode", msg)
        with self.assertRaises(Refused) as ctx2:
            require_usable_disk_size(0, what="READ CAPACITY")
        self.assertIn("READ CAPACITY", str(ctx2.exception))
        self.assertIn("never 0 bytes", str(ctx2.exception))

    def test_describe_mentions_tested(self):
        self.assertIn("hardware-tested", describe_size(TESTED_DISK_SIZE))

    def test_ipodrepartition_size_accepted_when_layout_matches(self):
        layout = DiskLayout(
            scheme="mbr",
            sector_size=4096,
            size_bytes=IPODREPARTITION_DISK_SIZE,
            partitions=[
                Partition(
                    index=0,
                    type_id=0x0C,
                    type_name="FAT32-LBA(0x0c)",
                    start_lba=64,
                    size_lba=3_858_975,
                    scheme="mbr",
                    sector_size=4096,
                )
            ],
            mbr_signature=True,
            sector_resolved=True,
        )
        self.assertTrue(layout.is_ipodrepartition_4k_fat32())
        info = check_disk_size(IPODREPARTITION_DISK_SIZE, layout=layout)
        self.assertTrue(info.get("ipodrepartition_4k_fat32"))
        self.assertFalse(info["hardware_tested"])
        self.assertFalse(info.get("override"))

    def test_ipodrepartition_size_refused_without_matching_mbr(self):
        with self.assertRaises(Refused) as ctx:
            check_disk_size(IPODREPARTITION_DISK_SIZE)
        msg = str(ctx.exception)
        self.assertIn("15806623744", msg)
        self.assertIn("0x0c", msg)
        self.assertIn("--allow-disk-size", msg)

    def test_ipodrepartition_mismatch_override_needs_both_flags(self):
        with self.assertRaises(Refused):
            check_disk_size(
                IPODREPARTITION_DISK_SIZE, confirm_untested=True, ipodrepartition_4k=False
            )
        info = check_disk_size(
            IPODREPARTITION_DISK_SIZE,
            allow_size=IPODREPARTITION_DISK_SIZE,
            confirm_untested=True,
            ipodrepartition_4k=False,
        )
        self.assertTrue(info.get("override"))


class ArtifactTests(unittest.TestCase):
    def test_sha256sums_match(self):
        sums = sha256sums()
        self.assertGreater(len(sums), 10)
        for rel, expected in sums.items():
            path = artifact(rel)
            self.assertTrue(path.is_file(), f"missing {rel}")
            self.assertEqual(sha256_file(path), expected, rel)

    def test_verify_release(self):
        verify_release_artifacts()


if __name__ == "__main__":
    unittest.main()
