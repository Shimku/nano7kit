#!/usr/bin/env python3
"""Darwin /dev/rdiskN reports st_size 0 and SEEK_END 0. Use discovery size."""
from __future__ import annotations

import os
import plistlib
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host.compat import Refused
from host.const import IPODREPARTITION_DISK_SIZE
from host.layout import (
    DKIOCGETBLOCKCOUNT,
    DKIOCGETBLOCKSIZE,
    MKN52_RECOVERY_BYTES,
    _bsd_whole_disk_ident,
    _darwin_diskutil_total_size,
    _darwin_ioctl_disk_bytes,
    encode_mkn52_repartition_mbr,
    inspect_device,
    read_prefix,
)
from host.safety import layout_for_disk_size
from host.untether import _layout_for_nano


class _SeekEndZero:
    """Character device: prefix is readable, SEEK_END reports 0."""

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            out = self._data[self._pos :]
            self._pos = len(self._data)
            return out
        out = self._data[self._pos : self._pos + n]
        self._pos += len(out)
        return out

    def seek(self, off: int, whence: int = 0) -> int:
        if whence == 2:
            self._pos = 0
            return 0
        if whence == 1:
            self._pos += off
        else:
            self._pos = off
        return self._pos

    def tell(self) -> int:
        return self._pos

    def fileno(self) -> int:
        raise OSError("no fileno")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _open_seek_end_zero(data: bytes):
    def _open(path, mode="rb", *a, **kw):
        if "b" not in str(mode):
            raise AssertionError(mode)
        return _SeekEndZero(data)

    return _open


class DarwinCharDevSizeTests(unittest.TestCase):
    def setUp(self):
        self.prefix = encode_mkn52_repartition_mbr()
        fd, name = tempfile.mkstemp(prefix="rdisk-sim-")
        Path(name).write_bytes(self.prefix)
        os.close(fd)
        self.path = Path(name)
        self.addCleanup(self.path.unlink)
        orig_stat = Path.stat
        target = os.path.normpath(str(self.path))

        def stat_zero(this, *a, **k):
            if os.path.normpath(str(this)) == target:
                return mock.Mock(st_size=0, st_mode=stat.S_IFCHR | 0o644)
            return orig_stat(this, *a, **k)

        self.stat_zero = stat_zero

    def test_ioctl_request_numbers_match_sys_disk_h(self):
        # _IOR('d', 24, uint32_t) and _IOR('d', 25, uint64_t) on Darwin.
        self.assertEqual(DKIOCGETBLOCKSIZE, 0x40046418)
        self.assertEqual(DKIOCGETBLOCKCOUNT, 0x40086419)
        self.assertEqual(MKN52_RECOVERY_BYTES, IPODREPARTITION_DISK_SIZE)
        self.assertEqual(MKN52_RECOVERY_BYTES, 3859039 * 4096)

    def test_known_size_beats_stat_zero_and_seek_end_zero(self):
        with mock.patch.object(Path, "stat", self.stat_zero), mock.patch(
            "host.layout.open", _open_seek_end_zero(self.prefix)
        ):
            data, size = read_prefix(self.path, size_bytes=MKN52_RECOVERY_BYTES)
            layout = inspect_device(self.path, MKN52_RECOVERY_BYTES)
        self.assertEqual(size, MKN52_RECOVERY_BYTES)
        self.assertEqual(data[:512], self.prefix)
        self.assertEqual(layout.size_bytes, MKN52_RECOVERY_BYTES)
        self.assertTrue(layout.is_ipodrepartition_4k_fat32())

    def test_size_zero_without_known_capacity_is_still_refused(self):
        with mock.patch.object(Path, "stat", self.stat_zero), mock.patch(
            "host.layout.open", _open_seek_end_zero(self.prefix)
        ), mock.patch("host.layout.is_macos", return_value=False):
            with self.assertRaises(Refused) as ctx:
                read_prefix(self.path)
            with self.assertRaises(Refused) as ctx2:
                inspect_device(self.path, 0)
        for msg in (str(ctx.exception), str(ctx2.exception)):
            self.assertIn("never 0 bytes", msg)
            self.assertIn("size 0", msg)

    def test_ioctl_count_times_block_size(self):
        fake = mock.Mock()
        fake.fileno.return_value = 3

        def ioctl(fd, req, buf, mutate=True):
            if req == DKIOCGETBLOCKSIZE:
                buf[0] = 4096
                return 0
            if req == DKIOCGETBLOCKCOUNT:
                buf[0] = MKN52_RECOVERY_BYTES // 4096
                return 0
            raise OSError("unexpected ioctl")

        import fcntl

        with mock.patch.object(fcntl, "ioctl", side_effect=ioctl):
            self.assertEqual(_darwin_ioctl_disk_bytes(fake), MKN52_RECOVERY_BYTES)

    def test_ioctl_fallback_when_stat_and_seek_are_zero(self):
        with mock.patch.object(Path, "stat", self.stat_zero), mock.patch(
            "host.layout.open", _open_seek_end_zero(self.prefix)
        ), mock.patch("host.layout.is_macos", return_value=True), mock.patch(
            "host.layout._darwin_ioctl_disk_bytes",
            return_value=MKN52_RECOVERY_BYTES,
        ), mock.patch(
            "host.layout._darwin_diskutil_total_size", return_value=0
        ):
            data, size = read_prefix(self.path)
            layout = inspect_device(self.path)
        self.assertEqual(size, MKN52_RECOVERY_BYTES)
        self.assertEqual(data[:512], self.prefix)
        self.assertTrue(layout.is_ipodrepartition_4k_fat32())

    def test_diskutil_totalsize_fallback(self):
        with mock.patch.object(Path, "stat", self.stat_zero), mock.patch(
            "host.layout.open", _open_seek_end_zero(self.prefix)
        ), mock.patch("host.layout.is_macos", return_value=True), mock.patch(
            "host.layout._darwin_ioctl_disk_bytes", return_value=0
        ), mock.patch(
            "host.layout._darwin_diskutil_total_size",
            return_value=MKN52_RECOVERY_BYTES,
        ):
            _data, size = read_prefix(self.path)
        self.assertEqual(size, MKN52_RECOVERY_BYTES)

    def test_diskutil_leftover_node_total_size_zero_is_refused(self):
        with mock.patch.object(Path, "stat", self.stat_zero), mock.patch(
            "host.layout.open", _open_seek_end_zero(self.prefix)
        ), mock.patch("host.layout.is_macos", return_value=True), mock.patch(
            "host.layout._darwin_ioctl_disk_bytes", return_value=0
        ), mock.patch(
            "host.layout._darwin_diskutil_total_size", return_value=0
        ):
            with self.assertRaises(Refused) as ctx:
                read_prefix(self.path)
        self.assertIn("never 0 bytes", str(ctx.exception))

    def test_bsd_ident_and_diskutil_plist(self):
        self.assertEqual(_bsd_whole_disk_ident(Path("/dev/rdisk4")), "disk4")
        self.assertEqual(_bsd_whole_disk_ident(Path("/dev/disk4")), "disk4")
        self.assertEqual(_bsd_whole_disk_ident(Path("/dev/rdisk4s1")), "")
        blob = plistlib.dumps(
            {"TotalSize": MKN52_RECOVERY_BYTES, "DeviceBlockSize": 4096, "Size": MKN52_RECOVERY_BYTES}
        )
        with mock.patch("host.layout.is_macos", return_value=True), mock.patch(
            "subprocess.check_output", return_value=blob
        ) as co:
            n = _darwin_diskutil_total_size(Path("/dev/rdisk4"))
        self.assertEqual(n, MKN52_RECOVERY_BYTES)
        self.assertEqual(co.call_args[0][0], ["diskutil", "info", "-plist", "disk4"])
        blob0 = plistlib.dumps({"TotalSize": 0, "Size": 0})
        with mock.patch("host.layout.is_macos", return_value=True), mock.patch(
            "subprocess.check_output", return_value=blob0
        ):
            self.assertEqual(_darwin_diskutil_total_size(Path("/dev/rdisk4")), 0)

    def test_untether_callers_pass_discovered_size(self):
        from host.device import Nano, annotate_size

        nano = annotate_size(
            Nano(
                os_name="darwin",
                device="/dev/disk4",
                raw_device="/dev/rdisk4",
                partition="/dev/rdisk4s1",
                usb_serial="XYZ123ABC456",
                size=IPODREPARTITION_DISK_SIZE,
                fstype="vfat",
                mountpoints=["/Volumes/IPOD"],
                model="iPod",
            )
        )
        with mock.patch("host.untether.inspect_device") as ins:
            ins.return_value = mock.Mock()
            _layout_for_nano(nano)
        ins.assert_called_once_with(Path("/dev/rdisk4"), IPODREPARTITION_DISK_SIZE)

        with mock.patch("host.layout.inspect_device") as ins2:
            ins2.return_value = mock.Mock()
            self.assertIsNone(layout_for_disk_size(self.path, 0))
            ins2.assert_not_called()
            layout_for_disk_size(self.path, IPODREPARTITION_DISK_SIZE)
        ins2.assert_called_once_with(self.path, IPODREPARTITION_DISK_SIZE)


if __name__ == "__main__":
    unittest.main()
