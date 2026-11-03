#!/usr/bin/env python3
import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host.compat import Refused, sha256_file
from host.layout import build_apm_macpod, build_mbr_winpod, inspect_image
from host.rescue import (
    copy_data_slice,
    inspect_ipsw_members,
    layouts_compatible_for_data,
    report_ipsw_payloads,
    rescue_nano,
)


def _fake_wind3x(td: Path, serial="SNRESCUE") -> Path:
    import stat

    path = td / "fake-wind3x"
    path.write_text(
        "#!/bin/sh\n"
        "if printf '%s' \"$*\" | grep -q -- --prepare-only; then\n"
        '  echo \'Found device device="Nano 7G" interface=diskmode\'\n'
        f"  echo 'SerialNumber: {serial}'\n"
        "  echo 'Recovery mode ready; no device partitions were written'\n"
        "  exit 0\n"
        "fi\n"
        "echo 'Writing firmware...'\n"
        "echo 'Reformatting system partition'\n"
        "echo 'Resetting...'\n"
        "exit 0\n"
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class IpswInspectTests(unittest.TestCase):
    def test_typical_nano7_ipsw_has_mse_no_dfu(self):
        with tempfile.TemporaryDirectory() as td:
            ipsw = Path(td) / "iPod_1.1.2_39A10023.ipsw"
            with zipfile.ZipFile(ipsw, "w") as z:
                z.writestr("Firmware.MSE", b"mse")
                z.writestr("N31.bootloader.rb3", b"boot")
            info = inspect_ipsw_members(ipsw)
            self.assertEqual(info["mse"], ["Firmware.MSE"])
            self.assertEqual(info["dfu"], [])
            self.assertTrue(info["bootloader"])
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                report_ipsw_payloads(ipsw)
            out = buf.getvalue()
            self.assertIn("none (expected", out)
            self.assertIn("0x124a", out)
            self.assertNotIn("\u2014", out)

    def test_dfu_members_are_listed_not_required(self):
        with tempfile.TemporaryDirectory() as td:
            ipsw = Path(td) / "odd.ipsw"
            with zipfile.ZipFile(ipsw, "w") as z:
                z.writestr("Firmware.MSE", b"mse")
                z.writestr("WTF.x1234.RELEASE.dfu", b"dfu")
            info = inspect_ipsw_members(ipsw)
            self.assertEqual(info["dfu"], ["WTF.x1234.RELEASE.dfu"])


class LayoutRestoreTests(unittest.TestCase):
    def test_hfs_onto_fat_refused(self):
        mac = inspect_image(
            self._write(build_apm_macpod(4096, fw_start=16, fw_size=50, data_start=80, data_size=100))
        )
        win = inspect_image(self._write(build_mbr_winpod(4096, fw_start=1, fw_size=50)))
        reason = layouts_compatible_for_data(mac, win)
        self.assertIsNotNone(reason)
        self.assertIn("HFS", reason)

    def test_finder_n7g_map_refuses_data_slice_restore(self):
        from host.layout import write_mkn52_finder_image

        fd, name = tempfile.mkstemp(prefix="nano7-mkn52-")
        os.close(fd)
        path = Path(name)
        self.addCleanup(path.unlink)
        write_mkn52_finder_image(path, sparse_full_size=False)
        finder = inspect_image(path)
        win = inspect_image(self._write(build_mbr_winpod(4096, fw_start=1, fw_size=50)))
        reason = layouts_compatible_for_data(finder, win)
        self.assertIsNotNone(reason)
        self.assertTrue("NAND" in reason or "scheme" in reason)

    def test_size_mismatch_refused(self):
        a = inspect_image(self._write(build_mbr_winpod(4096, fw_start=1, fw_size=50)))
        b = inspect_image(self._write(build_mbr_winpod(8192, fw_start=1, fw_size=50)))
        reason = layouts_compatible_for_data(a, b)
        self.assertIsNotNone(reason)
        self.assertIn("size", reason)

    def test_data_copy_leaves_firmware_marker(self):
        src_bytes = bytearray(build_mbr_winpod(4096, fw_start=1, fw_size=80, firmware_marker=b"FWREGION"))
        dst_bytes = bytearray(build_mbr_winpod(4096, fw_start=1, fw_size=80, firmware_marker=b"FWREGION"))
        src_layout = inspect_image(self._write(bytes(src_bytes)))
        # Fill data slice on source with a pattern.
        data = src_layout.data
        fw = src_layout.firmware
        assert data and fw
        pattern = b"DATA" * (data.size_bytes // 4)
        src_bytes[data.start_bytes : data.start_bytes + len(pattern)] = pattern
        dst_bytes[data.start_bytes : data.start_bytes + 4] = b"XXXX"
        src = self._write(bytes(src_bytes))
        dst = self._write(bytes(dst_bytes))
        copy_data_slice(src, dst, inspect_image(src), inspect_image(dst))
        out = dst.read_bytes()
        self.assertEqual(out[fw.start_bytes : fw.start_bytes + 8], b"FWREGION")
        self.assertEqual(out[data.start_bytes : data.start_bytes + 4], b"DATA")

    def _write(self, blob: bytes) -> Path:
        fd, name = tempfile.mkstemp(prefix="nano7-layout-")
        os.write(fd, blob)
        os.close(fd)
        path = Path(name)
        self.addCleanup(path.unlink)
        return path


class RescueFlowTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        os.environ["NANO7_STATE_DIR"] = self.td.name
        os.environ["NANO7KIT_CONFIG_DIR"] = str(Path(self.td.name) / "cfg")
        self.addCleanup(self.td.cleanup)
        self.backup = Path(self.td.name) / "nano.img"
        self.backup.write_bytes(build_mbr_winpod(4096, fw_start=1, fw_size=50))
        self.bhash = sha256_file(self.backup)
        self.wind3x = _fake_wind3x(Path(self.td.name))
        self.mse = Path(self.td.name) / "Firmware-stock.MSE"
        self.mse.write_bytes(b"stock-mse")

    def _run(self, **kw):
        from host.device import Nano, annotate_size

        nano = annotate_size(
            Nano(
                os_name="linux",
                device="/dev/loop9",
                raw_device="/dev/loop9",
                partition="/dev/loop9p2",
                usb_serial="USB1",
                size=self.backup.stat().st_size,
                fstype="vfat",
                mountpoints=[],
                model="iPod",
            )
        )
        defaults = dict(
            backup=self.backup,
            backup_sha256=self.bhash,
            serial="SNRESCUE",
            dry_run=True,
            allow_size=self.backup.stat().st_size,
            confirm_untested=True,
            accept_untested_flasher=True,
            mse=self.mse,
        )
        defaults.update(kw)
        with mock.patch("host.firmware.ensure_stock_ipsw", return_value=self.mse), mock.patch(
            "host.rescue.check_stock_mse", return_value="s" * 64
        ), mock.patch(
            "host.rescue.report_ipsw_payloads", return_value={"dfu": []}
        ), mock.patch(
            "host.rescue.select_wind3x", return_value=(self.wind3x, False, "fake")
        ), mock.patch("host.rescue.wait_dfu"), mock.patch(
            "host.rescue.late_wtf_present", return_value=False
        ):
            return rescue_nano(nano, **defaults)

    def test_dry_run_ok(self):
        out = self._run(dry_run=True)
        self.assertTrue(out["dry_run"])

    def test_typed_confirm_wrong_word_refused(self):
        with self.assertRaises(Refused) as ctx:
            self._run(dry_run=False, typed_confirm="FLASH")
        self.assertIn("did not type RESCUE", str(ctx.exception))

    def test_typed_confirm_ok_but_restore_mocked(self):
        node = Path(self.td.name) / "data-part"
        node.write_bytes(b"x" * 4096)
        with mock.patch("host.rescue.restore_full") as rf, mock.patch(
            "host.rescue.format_after_restore",
            return_value={"method": "kernel", "partition": str(node), "layout": None},
        ):
            out = self._run(dry_run=False, typed_confirm="RESCUE")
        self.assertTrue(out.get("ok"))
        rf.assert_called()

    def test_missing_serial_refused(self):
        with self.assertRaises(Refused) as ctx:
            self._run(serial="")
        self.assertIn("serial", str(ctx.exception).lower())


class TypedConfirmCliTests(unittest.TestCase):
    def test_help_lists_new_commands(self):
        import subprocess

        r = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent.parent / "start"), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        for word in ("rescue", "apps", "theme"):
            self.assertIn(word, r.stdout)
        self.assertIn("typed-confirm", r.stdout)
        self.assertIn("untested-firmware", r.stdout)


if __name__ == "__main__":
    unittest.main()
