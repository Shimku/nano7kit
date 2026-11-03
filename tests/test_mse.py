#!/usr/bin/env python3
import struct
import sys
import unittest
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host.compat import Refused, sha256_file
from host.mse import (
    extract_rsrc_fat,
    inspect_mse_flash_identity,
    parse_img1,
    parse_sections,
    splice_rsrc_fat,
)


def _mini_mse(fat: bytes) -> bytes:
    body_len = len(fat)
    cert_len = 16
    img1 = bytearray(0x400 + body_len + 0x80 + cert_len)
    img1[:7] = b"87402.0"
    img1[7] = 0x03
    struct.pack_into("<I", img1, 12, body_len)
    struct.pack_into("<I", img1, 16, body_len + 0x80 + cert_len)
    struct.pack_into("<I", img1, 20, body_len + 0x80)
    struct.pack_into("<I", img1, 24, cert_len)
    img1[0x400 : 0x400 + body_len] = fat
    section_len = len(img1)
    dev_offset = 0x6000
    mse = bytearray(dev_offset + 0x1000 + section_len)
    mse[0x5000:0x5004] = b"tset"
    mse[0x5004:0x5008] = b"crsr"
    struct.pack_into("<I", mse, 0x5000 + 12, dev_offset)
    struct.pack_into("<I", mse, 0x5000 + 16, section_len)
    mse[dev_offset + 0x1000 : dev_offset + 0x1000 + section_len] = img1
    return bytes(mse)


class MseHelpersTests(unittest.TestCase):
    def test_extract_and_identity_splice(self):
        fat = b"FAT16VOL" + b"\x00" * 200
        mse = _mini_mse(fat)
        extracted, details = extract_rsrc_fat(mse)
        self.assertEqual(extracted, fat)
        self.assertEqual(details["img1"]["format"], 0x03)
        self.assertEqual(details["img1"]["body_length"], len(fat))
        sections = parse_sections(mse)
        self.assertEqual(sections[0]["name_ascii"], "rsrc")
        spliced = splice_rsrc_fat(mse, fat)
        self.assertEqual(spliced, mse)

    def test_splice_changes_only_fat_bytes(self):
        fat = bytearray(b"A" * 256)
        mse = _mini_mse(bytes(fat))
        new = bytes(b"B" * 256)
        out = splice_rsrc_fat(mse, new)
        extracted, _ = extract_rsrc_fat(out)
        self.assertEqual(extracted, new)
        start = parse_sections(mse)[0]["body_start"]
        self.assertEqual(out[: start + 0x400], mse[: start + 0x400])
        self.assertEqual(out[start + 0x400 + 256 :], mse[start + 0x400 + 256 :])

    def test_splice_rejects_size_change(self):
        mse = _mini_mse(b"X" * 128)
        with self.assertRaises(Refused):
            splice_rsrc_fat(mse, b"Y" * 64)

    def test_parse_img1_rejects_stock_format(self):
        img1 = bytearray(0x500)
        img1[:7] = b"87402.0"
        img1[7] = 0x04
        struct.pack_into("<I", img1, 12, 16)
        with self.assertRaises(Refused) as ctx:
            parse_img1(bytes(img1))
        self.assertIn("0x03", str(ctx.exception))

    def test_parse_img1_allows_stock_when_not_required(self):
        fat = b"FAT16VOL" + b"\x00" * 200
        mse = _mini_mse(fat)
        # Flip format byte to stock-signed 0x04 inside the IMG1.
        start = parse_sections(mse)[0]["body_start"]
        raw = bytearray(mse)
        raw[start + 7] = 0x04
        mse04 = bytes(raw)
        with self.assertRaises(Refused):
            extract_rsrc_fat(mse04)
        extracted, details = extract_rsrc_fat(mse04, require_patched=False)
        self.assertEqual(extracted, fat)
        self.assertEqual(details["img1"]["format"], 0x04)


def _identity_mse(
    path: Path,
    *,
    rsrc_fmt: int = 0x03,
    osos_len: int = 1_500_000,
    disk_len: Optional[int] = 100,
) -> Path:
    rsrc_dev = 0x6000
    rsrc_len = 0x500
    blob = bytearray(0x8000)

    def slot(index: int, name: bytes, dev_offset: int, length: int) -> None:
        off = 0x5000 + index * 40
        blob[off : off + 4] = b"tset"
        blob[off + 4 : off + 8] = name
        struct.pack_into("<I", blob, off + 12, dev_offset)
        struct.pack_into("<I", blob, off + 16, length)

    slot(0, b"soso", 0x10000, osos_len)
    next_i = 1
    if disk_len is not None:
        slot(1, b"ksid", 0x20000, disk_len)
        next_i = 2
    slot(next_i, b"crsr", rsrc_dev, rsrc_len)
    img1 = rsrc_dev + 0x1000
    blob[img1 : img1 + 7] = b"87402.0"
    blob[img1 + 7] = rsrc_fmt
    path.write_bytes(bytes(blob))
    return path


class MseFlashIdentityTests(unittest.TestCase):
    def test_39a10023_helvetica_identity(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            path = _identity_mse(Path(td) / "fw.mse")
            ident = inspect_mse_flash_identity(
                path,
                expected_sha256=sha256_file(path),
                expected_size=path.stat().st_size,
            )
            self.assertEqual(ident["identity"], "39A10023")
            self.assertEqual(ident["rsrc_format"], 0x03)
            self.assertGreaterEqual(ident["osos_length"], 1_000_000)

    def test_refuses_ipems_stock_rsrc_format(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            path = _identity_mse(Path(td) / "fw.mse", rsrc_fmt=0x04)
            with self.assertRaises(Refused) as ctx:
                inspect_mse_flash_identity(
                    path,
                    expected_sha256=sha256_file(path),
                    expected_size=path.stat().st_size,
                )
            msg = str(ctx.exception)
            self.assertIn("0x04", msg)
            self.assertIn("iPEMS", msg)

    def test_refuses_small_osos_and_disk_swap(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            tiny = _identity_mse(Path(td) / "tiny.mse", osos_len=100)
            with self.assertRaises(Refused) as ctx:
                inspect_mse_flash_identity(
                    tiny,
                    expected_sha256=sha256_file(tiny),
                    expected_size=tiny.stat().st_size,
                )
            self.assertIn("osos", str(ctx.exception).lower())
            swapped = _identity_mse(
                Path(td) / "swap.mse", osos_len=1_500_000, disk_len=2_000_000
            )
            with self.assertRaises(Refused) as ctx2:
                inspect_mse_flash_identity(
                    swapped,
                    expected_sha256=sha256_file(swapped),
                    expected_size=swapped.stat().st_size,
                )
            self.assertIn("disk", str(ctx2.exception).lower())

    def test_refuses_wrong_hash(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            path = _identity_mse(Path(td) / "fw.mse")
            with self.assertRaises(Refused) as ctx:
                inspect_mse_flash_identity(
                    path,
                    expected_sha256="0" * 64,
                    expected_size=path.stat().st_size,
                )
            self.assertIn("hash", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
