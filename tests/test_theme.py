#!/usr/bin/env python3
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host.compat import Refused
from host.const import FIRMWARE_MSE_SHA256, FIRMWARE_MSE_SIZE, N7G_THEME_PREVIEWER
from host.safety import check_mse
from host.theme import (
    _osos_disk_name_swap,
    combine_theme,
    import_theme_art,
    preview_url,
    refuse_nanovault,
    would_replace_helvetica,
)


class NanoVaultTests(unittest.TestCase):
    def test_nanovault_path_refused(self):
        with self.assertRaises(Refused) as ctx:
            refuse_nanovault(Path("/tmp/NanoVault/7g/theme.ipsw"))
        self.assertIn("NanoVault", str(ctx.exception))

    def test_pseudostock_name_refused(self):
        with self.assertRaises(Refused):
            refuse_nanovault(Path("/tmp/PseudoStock_2015.ipsw"))

    def test_official_name_ok(self):
        refuse_nanovault(Path("/tmp/iPod_1.1.2_39A10023.ipsw"))

    def test_theme_in_filename_refused_by_default(self):
        with self.assertRaises(Refused) as ctx:
            refuse_nanovault(Path("/tmp/CoolTheme_2015.ipsw"))
        self.assertIn("community themed IPSW", str(ctx.exception))
        self.assertIn("theme import", str(ctx.exception))

    def test_theme_in_filename_ok_for_import(self):
        refuse_nanovault(
            Path("/tmp/CoolTheme_2015.ipsw"),
            allow_community_ipsw=True,
        )

    def test_import_still_refuses_nanovault_path(self):
        with self.assertRaises(Refused):
            refuse_nanovault(
                Path("/tmp/NanoVault/Brutalist.ipsw"),
                allow_community_ipsw=True,
            )


class HelveticaGuardTests(unittest.TestCase):
    def test_helvetica_filename_detected(self):
        hit = would_replace_helvetica([Path("Fonts/Helvetica.ttf")])
        self.assertIsNotNone(hit)
        self.assertIsNone(would_replace_helvetica([Path("Fonts/Arial.ttf")]))

    def test_combine_requires_flags(self):
        with tempfile.TemporaryDirectory() as td:
            silver = Path(td) / "SilverImagesDB.LE.bin"
            silver.write_bytes(b"db")
            with self.assertRaises(Refused) as ctx:
                combine_theme(silverdb=silver)
            self.assertIn("untested", str(ctx.exception).lower())

    def test_combine_refuses_helvetica_font(self):
        with tempfile.TemporaryDirectory() as td:
            silver = Path(td) / "SilverImagesDB.LE.bin"
            silver.write_bytes(b"db")
            font = Path(td) / "Helvetica.ttf"
            font.write_bytes(b"font")
            with self.assertRaises(Refused) as ctx:
                combine_theme(
                    silverdb=silver,
                    fonts=[font],
                    accept_untested=True,
                    accept_combine=True,
                )
            self.assertIn("Helvetica", str(ctx.exception))

    def test_combine_refuses_nanovault_silverdb(self):
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "nanovault" / "SilverImagesDB.LE.bin"
            vault.parent.mkdir()
            vault.write_bytes(b"db")
            with self.assertRaises(Refused) as ctx:
                combine_theme(
                    silverdb=vault,
                    accept_untested=True,
                    accept_combine=True,
                )
            self.assertIn("NanoVault", str(ctx.exception))

    def test_preview_url(self):
        self.assertEqual(preview_url(), N7G_THEME_PREVIEWER)
        self.assertIn("n7g_theme_previewer", preview_url())


class ThemeImportTests(unittest.TestCase):
    def test_import_raw_silverdb(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            silver = td / "SilverImagesDB.LE.bin"
            # paMB header; file_count 0 is enough for magic.
            blob = (
                b"\x03\x00\x00\x00"
                + b"\x00\x00\x00\x00"
                + b"\x01\x00\x00\x00"
                + b"paMB"
                + b"\x00\x00\x00\x00"
                + b"\x01\x00\x00\x00"
                + b"\x1c\x00\x00\x00"
            )
            silver.write_bytes(blob)
            out = td / "imported"
            report = import_theme_art(silver, out_dir=out)
            dest = Path(report["silverdb"])
            self.assertTrue(dest.is_file())
            self.assertEqual(dest.read_bytes(), blob)
            self.assertEqual(report["source_kind"], "silverdb")
            self.assertIsNone(report["combined_mse"])
            self.assertTrue((out / "import-report.json").is_file())

    def test_import_refuses_nanovault_tree(self):
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "nanovault" / "SilverImagesDB.LE.bin"
            vault.parent.mkdir()
            vault.write_bytes(b"\x03\x00\x00\x00" + b"\x00" * 12 + b"paMB" + b"\x00" * 12)
            with self.assertRaises(Refused) as ctx:
                import_theme_art(vault, out_dir=Path(td) / "out")
            self.assertIn("NanoVault", str(ctx.exception))

    def test_import_does_not_combine_without_flags(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            silver = td / "SilverImagesDB.LE.bin"
            silver.write_bytes(
                b"\x03\x00\x00\x00"
                + b"\x00\x00\x00\x00"
                + b"\x01\x00\x00\x00"
                + b"paMB"
                + b"\x00" * 12
            )
            with self.assertRaises(Refused) as ctx:
                import_theme_art(silver, out_dir=td / "out", combine=True)
            self.assertIn("untested", str(ctx.exception).lower())

    def test_osos_disk_name_swap_heuristic(self):
        import struct

        def mse_with(osos_len: int, disk_len: int) -> bytes:
            buf = bytearray(0x1000 + max(osos_len, disk_len) + 0x100)
            def slot(index: int, name: bytes, length: int) -> None:
                off = 0x5000 + index * 40
                buf[off : off + 4] = b"tset"
                buf[off + 4 : off + 8] = name
                struct.pack_into("<I", buf, off + 16, length)
            slot(0, b"soso", osos_len)
            slot(1, b"ksid", disk_len)
            return bytes(buf)

        self.assertTrue(_osos_disk_name_swap(mse_with(690195, 1_500_000)))
        self.assertFalse(_osos_disk_name_swap(mse_with(1_500_000, 690195)))


class UntestedMseGateTests(unittest.TestCase):
    def test_default_check_mse_refuses_other_hash(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "Firmware.MSE"
            flags = os.O_CREAT | os.O_RDWR
            fd = os.open(p, flags)
            os.ftruncate(fd, FIRMWARE_MSE_SIZE)
            os.close(fd)
            with self.assertRaises(Refused) as ctx:
                check_mse(p)
            self.assertIn("hardware-tested hash", str(ctx.exception))
            self.assertIn("--i-accept-untested-firmware", str(ctx.exception))
            digest = check_mse(p, allow_untested=True)
            self.assertEqual(len(digest), 64)
            self.assertNotEqual(digest, FIRMWARE_MSE_SHA256)


if __name__ == "__main__":
    unittest.main()
