"""NanoApps 1.0 firmware through DFU: pinned image, dry run first, typed FLASH, one write."""

import hashlib
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host import nabase_fw  # noqa: E402
from host.compat import Refused  # noqa: E402

# Release files, when this machine has them (not committed).
IPSW = Path(os.environ.get("NANOAPPS_2015_IPSW", "build/nanoapps-release/NanoApps-2015-Model.ipsw"))
STOCK_MSE = Path(os.environ.get("STOCK_39A10023_MSE", "build/stock/Firmware.MSE"))


class Flow(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        state = Path(self.td.name) / "state"
        state.mkdir()
        self.mse = Path(self.td.name) / nabase_fw.MSE_NAME
        self.mse.write_bytes(b"mse")
        self.flashed = []
        patches = [
            mock.patch.dict(os.environ, {"NANO7_STATE_DIR": str(state)}),
            mock.patch.object(nabase_fw, "check_mse", return_value=nabase_fw.MSE_SHA256),
            mock.patch.object(nabase_fw, "check_backup_image", return_value=15_806_623_744),
            mock.patch("host.usb.select_wind3x", return_value=(Path("/x/wInd3x"), False, "host-built")),
            mock.patch("host.usb.wait_dfu"),
            mock.patch("host.usb.prepare_restore", return_value="SerialNumber: SER1\n"),
            mock.patch("host.usb.flash_restore", side_effect=self._flash),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _flash(self, wind3x, mse):
        self.flashed.append(Path(mse))
        return "Writing firmware...\nResetting...\n"

    def go(self, **kw):
        args = dict(mse=self.mse, backup=Path("/b.img"), backup_sha256="a" * 64, serial="SER1",
                    accept_2015_refresh=True, accept_untested_flasher=True, out=lambda *_: None)
        args.update(kw)
        return nabase_fw.flash(**args)

    def test_dry_run_writes_nothing(self):
        self.assertTrue(self.go(dry_run=True)["dry_run"])
        self.assertEqual(self.flashed, [])
        self.assertFalse(nabase_fw.attempt_receipt().exists())

    def test_run_needs_dry_run_first(self):
        with self.assertRaises(Refused):
            self.go(dry_run=False, typed_confirm="FLASH")
        self.assertEqual(self.flashed, [])

    def test_run_needs_typed_flash(self):
        self.go(dry_run=True)
        with self.assertRaises(Refused):
            self.go(dry_run=False, typed_confirm="flash")
        self.assertEqual(self.flashed, [])

    def test_dry_run_must_match_backup(self):
        self.go(dry_run=True)
        with self.assertRaises(Refused):
            self.go(dry_run=False, typed_confirm="FLASH", backup_sha256="b" * 64)
        self.assertEqual(self.flashed, [])

    def test_one_write_no_retry(self):
        self.go(dry_run=True)
        self.assertTrue(self.go(dry_run=False, typed_confirm="FLASH")["ok"])
        self.assertEqual(self.flashed, [self.mse])
        self.assertTrue(nabase_fw.attempt_receipt().exists())
        for dry in (True, False):
            with self.assertRaises(Refused):
                self.go(dry_run=dry, typed_confirm="FLASH")
        self.assertEqual(len(self.flashed), 1)

    def test_serial_mismatch_writes_nothing(self):
        self.go(dry_run=True)
        with mock.patch("host.usb.prepare_restore", return_value="SerialNumber: OTHER\n"):
            with self.assertRaises(Refused):
                self.go(dry_run=False, typed_confirm="FLASH")
        self.assertEqual(self.flashed, [])
        self.assertFalse(nabase_fw.attempt_receipt().exists())

    def test_needs_2015_and_flasher_flags(self):
        with self.assertRaises(Refused):
            self.go(accept_2015_refresh=False)
        with self.assertRaises(Refused):
            self.go(accept_untested_flasher=False)
        with self.assertRaises(Refused):
            self.go(serial="")


class BackupImage(unittest.TestCase):
    """The backup's own partition map must pass the disk-size rule (no overrides)."""

    def check(self, prefix, size):
        with tempfile.TemporaryDirectory() as td:
            img = Path(td) / "nano.img"
            img.write_bytes(prefix)
            with mock.patch.object(nabase_fw, "file_size", return_value=size), \
                    mock.patch("host.layout.read_prefix", return_value=(prefix, size)), \
                    mock.patch("host.safety.check_backup", return_value="ok") as cb:
                out = nabase_fw.check_backup_image(img, "a" * 64)
            cb.assert_called_once_with(img, "a" * 64, size)
            return out

    def test_ipodrepartition_backup_passes(self):
        from host.layout import MKN52_RECOVERY_BYTES, encode_mkn52_repartition_mbr

        self.assertEqual(self.check(encode_mkn52_repartition_mbr(), MKN52_RECOVERY_BYTES),
                         MKN52_RECOVERY_BYTES)

    def test_unknown_backup_refused(self):
        with self.assertRaises(Refused):
            self.check(b"\0" * 512, 1 << 30)
        with self.assertRaises(Refused):
            nabase_fw.check_backup_image(Path("/nonexistent/nano.img"), "a" * 64)


class Image(unittest.TestCase):
    def test_wrong_ipsw_refused(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.ipsw"
            with zipfile.ZipFile(p, "w") as z:
                z.writestr("Firmware.MSE", b"x")
            with self.assertRaises(Refused):
                nabase_fw.check_ipsw(p)

    def test_mse_must_be_pinned(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "Firmware.MSE"
            p.write_bytes(b"x")
            with self.assertRaises(Refused):
                nabase_fw.check_mse(p)

    @unittest.skipUnless(IPSW.is_file(), "NanoApps-2015-Model.ipsw not downloaded")
    def test_release_ipsw(self):
        with tempfile.TemporaryDirectory() as td:
            mse = nabase_fw.extract_mse(IPSW, Path(td), out=lambda *_: None)
            self.assertEqual(nabase_fw.check_mse(mse), nabase_fw.MSE_SHA256)
            if STOCK_MSE.is_file():
                self._only_resources_differ(STOCK_MSE.read_bytes(), mse.read_bytes())

    def _only_resources_differ(self, stock, na):
        """Apple's 39A10023 MSE with only rsrc changed: same header, osos and other sections."""
        from host.mse import parse_sections

        self.assertEqual(len(stock), len(na))
        self.assertEqual(stock[:0x5000], na[:0x5000])
        for a, b in zip(parse_sections(stock), parse_sections(na)):
            self.assertEqual(a["slot"], b["slot"])
            if a["name_ascii"] != "rsrc":
                self.assertEqual(hashlib.sha256(stock[a["head_start"]:a["body_end"]]).digest(),
                                 hashlib.sha256(na[b["head_start"]:b["body_end"]]).digest(),
                                 a["name_ascii"])


if __name__ == "__main__":
    unittest.main()
