#!/usr/bin/env python3
"""FAT32 prefix + SCSI BOT format + format-only DFU path (no restore --full)."""
from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host.compat import Refused, sha256_file
from host.fat32fs import (
    FAT32_EOC,
    FAT32_FAT1_WINDOWS,
    FAT32_MEDIA,
    LIVE_32K_PREFIX_BYTES,
    LIVE_32K_PREFIX_SECTORS,
    analyze_fat32_prefix,
    build_fat32_prefix,
    fat32_bytes_per_sector,
    fat32_geometry,
    fat32_prefix_layout,
    is_fat32_boot,
    microsoft_fat32_cluster_bytes,
    prefix_span_sectors,
)
from host.usbms import (
    Capacity,
    describe_capacity,
    dump_data_slice_via_bot,
    format_data_slice_via_bot,
    parse_capacity_text,
    probe_read_lba0,
)
from host.layout import (
    MKN52_RECOVERY_BYTES,
    MKN52_RECOVERY_FAT_BYTES,
    MKN52_RECOVERY_FAT_SIZE,
    MKN52_RECOVERY_FAT_START,
    MKN52_RECOVERY_FAT_START_BYTES,
    SECTOR,
    build_mbr_winpod,
    encode_mbr,
    encode_mkn52_repartition_mbr,
    inspect_bytes,
    inspect_image,
    mbr_extent_report,
    pick_format_partition,
)
from host.usb import recovery_usb_state
from host.winpod import convert_nano


class MemoryBot:
    def __init__(self, image: bytearray, block_size: int = 512):
        self.image = image
        self.block_size = block_size
        self.writes = []
        self.reset_called = False
        self.synced = False

    def capacity(self) -> Capacity:
        last = len(self.image) // self.block_size - 1
        return Capacity(last_lba=last, block_size=self.block_size)

    def read10(self, lba: int, count: int) -> bytes:
        off = lba * self.block_size
        n = count * self.block_size
        return bytes(self.image[off : off + n])

    def write10(self, lba: int, data: bytes) -> None:
        self.writes.append((lba, bytes(data)))
        off = lba * self.block_size
        self.image[off : off + len(data)] = data

    def reset(self) -> None:
        self.reset_called = True

    def synccache(self) -> None:
        self.synced = True


class SparseBot:
    """Capacity-sized disk without allocating 15 GB. MBR in LBA 0."""

    def __init__(self, prefix: bytes, size_bytes: int, block_size: int):
        if size_bytes % block_size:
            raise ValueError("size_bytes must be a multiple of block_size")
        self.size_bytes = size_bytes
        self.block_size = block_size
        self.blocks = {}
        self.writes = []
        self.reset_called = False
        self.synced = False
        blk0 = bytearray(block_size)
        blk0[: min(len(prefix), block_size)] = prefix[:block_size]
        self.blocks[0] = bytes(blk0)

    def capacity(self) -> Capacity:
        last = self.size_bytes // self.block_size - 1
        return Capacity(last_lba=last, block_size=self.block_size)

    def read10(self, lba: int, count: int) -> bytes:
        out = bytearray()
        for i in range(count):
            out.extend(self.blocks.get(lba + i, b"\x00" * self.block_size))
        return bytes(out)

    def write10(self, lba: int, data: bytes) -> None:
        if len(data) % self.block_size:
            raise AssertionError("write length not a SCSI block multiple")
        n = len(data) // self.block_size
        self.writes.append((lba, bytes(data)))
        for i in range(n):
            self.blocks[lba + i] = data[i * self.block_size : (i + 1) * self.block_size]

    def reset(self) -> None:
        self.reset_called = True

    def synccache(self) -> None:
        self.synced = True


class HaltAwareBot:
    """RC16 stall leaves bulk-IN halted; the next CBW is cancelled until clear-halt."""

    def __init__(self, inner, *, send_rc16: bool = False):
        self.inner = inner
        self.send_rc16 = send_rc16
        self.halted = False
        self.clear_halt_calls = 0
        self.bot_resets = 0
        self.cbw_fails = 0
        self.capacity_calls = 0
        self.read_calls = 0
        self.write_calls = 0

    def _cbw(self, op: str) -> None:
        if self.halted:
            self.cbw_fails += 1
            raise Refused("usbscsi: CBW: transfer was cancelled")

    def capacity(self):
        self._cbw("capacity")
        self.capacity_calls += 1
        cap = self.inner.capacity()
        if self.send_rc16:
            cap.rc16_error = "data IN: halt condition detected (endpoint stalled)"
            cap.rc16_skipped = False
            self.halted = True
        else:
            cap.rc16_skipped = True
            cap.rc16_error = ""
        return cap

    def read10(self, lba, count):
        self._cbw("read10")
        self.read_calls += 1
        return self.inner.read10(lba, count)

    def write10(self, lba, data):
        self._cbw("write10")
        self.write_calls += 1
        return self.inner.write10(lba, data)

    def synccache(self):
        self._cbw("synccache")
        return self.inner.synccache()

    def clear_halts(self):
        self.clear_halt_calls += 1
        self.halted = False

    def bot_reset(self):
        self.bot_resets += 1
        self.halted = False

    def close(self):
        pass


class UsbscsiSourceTests(unittest.TestCase):
    def test_standard_scsi_commands_present(self):
        src = Path(__file__).resolve().parent.parent / "host" / "cmd" / "usbscsi" / "main.go"
        text = src.read_text(encoding="utf-8")
        for needle in (
            "write10",
            "read10",
            "capacity",
            "capacity16",
            "synccache",
            "doReset",
            "0x2A",
            "0x28",
            "0x25",
            "0x9E",
            "0x35",
            "read_capacity_10",
            "read_capacity_16",
            "SYNCHRONIZE CACHE",
            "shouldSendCapacity16",
            "0xFFFFFFFF",
            "CLEAR_FEATURE",
            "ENDPOINT_HALT",
            "reqBotReset",
            "0xFF",
            "session",
            "read_capacity_16 skipped",
            "WRITE not retried",
            "probe",
            "C6_96_04_OK",
            "COULD_NOT_SEND",
            "DEVICE_REJECTED",
        ):
            self.assertIn(needle, text)
        self.assertIn("libusb_reset_device", text)
        self.assertIn("USBDeviceReEnumerate-free", text)
        logic = src.parent / "logic.go"
        logic_text = logic.read_text(encoding="utf-8")
        self.assertIn("shouldSendCapacity16", logic_text)
        self.assertIn("lastLBA == 0xFFFFFFFF", logic_text)
        self.assertIn("retryReadAfterPipe", logic_text)
        self.assertIn("classifyXferErr", logic_text)
        self.assertIn("could_not_send", logic_text)


class Fat32PrefixTests(unittest.TestCase):
    def test_boot_signature_and_fat32_id(self):
        prefix = build_fat32_prefix(64 * 1024 * 1024, label="IPOD", hidden_sectors=100)
        self.assertTrue(is_fat32_boot(prefix[:512]))
        self.assertLess(len(prefix), 8 * 1024 * 1024)
        total, spc, reserved, fat, clusters = fat32_geometry(64 * 1024 * 1024)
        self.assertGreaterEqual(clusters, 1000)
        self.assertGreaterEqual(spc, 1)
        self.assertEqual(prefix[0x47:0x4B], b"IPOD")

    def test_16gb_prefix_is_small(self):
        prefix = build_fat32_prefix(15_000_000_000)
        self.assertTrue(is_fat32_boot(prefix[:512]))
        self.assertLess(len(prefix), 16 * 1024 * 1024)

    def test_mkn52_4k_prefix_geometry(self):
        prefix = build_fat32_prefix(
            MKN52_RECOVERY_FAT_BYTES,
            label="IPOD",
            hidden_sectors=MKN52_RECOVERY_FAT_START,
            bytes_per_sector=4096,
        )
        self.assertTrue(is_fat32_boot(prefix, bytes_per_sector=4096))
        self.assertEqual(fat32_bytes_per_sector(prefix), 4096)
        self.assertEqual(prefix[0x0D], 2)  # 8 KiB clusters / 4096 (MS default)
        hidden = int.from_bytes(prefix[0x1C:0x20], "little")
        self.assertEqual(hidden, 64)
        geo = fat32_prefix_layout(MKN52_RECOVERY_FAT_BYTES, bytes_per_sector=4096)
        self.assertEqual(geo["total_sectors"], MKN52_RECOVERY_FAT_SIZE)
        self.assertEqual(geo["spc"], 2)
        self.assertEqual(geo["cluster_bytes"], 8192)
        self.assertGreaterEqual(geo["clusters"], 65525)
        self.assertEqual(
            geo["prefix_sectors"],
            geo["reserved"] + 2 * geo["fat_sectors"] + geo["spc"],
        )
        self.assertEqual(len(prefix), geo["prefix_bytes"])
        self.assertLess(len(prefix), 32 * 1024 * 1024)
        self.assertEqual(len(prefix) % 4096, 0)
        self.assertEqual(prefix[4094:4096], b"\x55\xaa")
        fat0, fat1, clus2 = __import__("struct").unpack_from(
            "<III", prefix, geo["fat1_byte"]
        )
        self.assertEqual(fat0, FAT32_MEDIA)
        self.assertEqual(fat1, FAT32_FAT1_WINDOWS)
        self.assertEqual(clus2, FAT32_EOC)
        root = prefix[geo["root_byte"] : geo["root_byte"] + 32]
        self.assertEqual(root[0:4], b"IPOD")
        self.assertEqual(root[11], 0x08)
        self.assertTrue(
            prefix[geo["root_byte"] + 32 : geo["root_byte"] + geo["cluster_bytes"]]
            == b"\x00" * (geo["cluster_bytes"] - 32)
        )

    def test_live_prefix_covered_reserved_fats_and_root(self):
        span = prefix_span_sectors(reserved=32, nfat=2, fat_sectors=472, spc=8)
        self.assertEqual(span, LIVE_32K_PREFIX_SECTORS)
        self.assertEqual(span, 984)
        self.assertEqual(span * 4096, LIVE_32K_PREFIX_BYTES)
        self.assertEqual(LIVE_32K_PREFIX_BYTES, 4_030_464)

    def test_microsoft_cluster_table_8_to_16_gib_is_8k(self):
        self.assertEqual(microsoft_fat32_cluster_bytes(MKN52_RECOVERY_FAT_BYTES), 8192)
        self.assertEqual(microsoft_fat32_cluster_bytes(8 * 1024**3), 8192)
        self.assertEqual(microsoft_fat32_cluster_bytes(16 * 1024**3), 16384)


class InspectBytesTests(unittest.TestCase):
    def test_size_zero_is_never_a_disk(self):
        with self.assertRaises(Refused) as ctx:
            inspect_bytes(b"\x00" * 512, 0)
        msg = str(ctx.exception)
        self.assertIn("never 0 bytes", msg)
        self.assertNotIn("shorter than one sector", msg)
        self.assertNotIn("device not in recovery diskmode", msg)

    def test_matches_inspect_image(self):
        blob = build_mbr_winpod(8192, fw_start=63, fw_size=200, firmware_marker=b"FWREGION")
        a = inspect_bytes(blob[:4096], len(blob))
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(blob)
            path = Path(tmp.name)
        self.addCleanup(path.unlink)
        b = inspect_image(path)
        self.assertEqual(a.scheme, b.scheme)
        self.assertEqual(a.firmware.start_lba, b.firmware.start_lba)
        self.assertEqual(a.data.start_lba, b.data.start_lba)


class BotFormatTests(unittest.TestCase):
    def test_writes_only_data_lbas(self):
        blob = bytearray(
            build_mbr_winpod(16384, fw_start=63, fw_size=200, firmware_marker=b"FWREGION")
        )
        layout = inspect_bytes(bytes(blob[:1024]), len(blob))
        data = layout.data
        fw = layout.firmware
        assert data and fw
        bot = MemoryBot(blob)
        out = format_data_slice_via_bot(bot=bot, write=True, label="IPOD")
        self.assertTrue(out["wrote"])
        self.assertEqual(out["method"], "scsi")
        self.assertFalse(bot.reset_called)
        data_lba0 = data.start_bytes // 512
        fw_lba0 = fw.start_bytes // 512
        fw_lba1 = fw.end_lba
        for lba, chunk in bot.writes:
            self.assertGreaterEqual(lba, data_lba0)
            self.assertLess(lba, data.end_lba)
            self.assertFalse(fw_lba0 <= lba < fw_lba1)
        self.assertEqual(bytes(blob[fw.start_bytes : fw.start_bytes + 8]), b"FWREGION")
        boot = bytes(blob[data.start_bytes : data.start_bytes + 512])
        self.assertTrue(is_fat32_boot(boot))
        self.assertTrue(bot.synced)

    def test_dry_inspect_does_not_write(self):
        blob = bytearray(build_mbr_winpod(8192, fw_start=63, fw_size=100))
        bot = MemoryBot(blob)
        out = format_data_slice_via_bot(bot=bot, write=False)
        self.assertFalse(out["wrote"])
        self.assertEqual(bot.writes, [])

    def test_refuses_type0_firmware_slot(self):
        img = bytearray(build_mbr_winpod(4096, fw_start=63, fw_size=100))
        img[0:512] = encode_mbr([(0x00, 63, 100), (0x00, 163, 4096 - 163)], 4096)
        bot = MemoryBot(img)
        with self.assertRaises(Refused) as ctx:
            format_data_slice_via_bot(bot=bot, write=True)
        self.assertIn("0x00", str(ctx.exception))
        self.assertEqual(bot.writes, [])

    def test_refuses_apm(self):
        from host.layout import mkn52_finder_apm_prefix

        prefix = mkn52_finder_apm_prefix()
        img = bytearray(prefix + b"\x00" * (1024 * 1024 - len(prefix)))
        bot = MemoryBot(img)
        with self.assertRaises(Refused) as ctx:
            format_data_slice_via_bot(bot=bot, write=True)
        self.assertIn("Partition Map", str(ctx.exception))
        self.assertEqual(bot.writes, [])


class Mkn52RepartitionSectorTests(unittest.TestCase):
    """Exact 2026-10-28 dry-run numbers: 15806623744 / start 64 / size 3858975."""

    def test_constants_match_field_report(self):
        self.assertEqual(MKN52_RECOVERY_BYTES, 15_806_623_744)
        self.assertEqual(MKN52_RECOVERY_FAT_START, 64)
        self.assertEqual(MKN52_RECOVERY_FAT_SIZE, 3_858_975)
        self.assertEqual(64 * 4096, 262_144)
        self.assertEqual(3_858_975 * 4096, 15_806_361_600)
        self.assertEqual(64 * 4096 + 3_858_975 * 4096, 15_806_623_744)
        self.assertEqual(3_858_975 * 512, 1_975_795_200)
        from host.layout import MKN52_DISK_BYTES, MKN52_IPODREPARTITION_DELTA

        self.assertEqual(MKN52_IPODREPARTITION_DELTA, 8_212_480)
        self.assertEqual(
            MKN52_RECOVERY_BYTES - MKN52_DISK_BYTES, MKN52_IPODREPARTITION_DELTA
        )
        self.assertEqual(MKN52_IPODREPARTITION_DELTA % 4096, 0)

    def test_512_does_not_fill_4096_does(self):
        mbr = encode_mkn52_repartition_mbr()
        layout = inspect_bytes(mbr, MKN52_RECOVERY_BYTES)
        r512 = mbr_extent_report(layout.partitions, MKN52_RECOVERY_BYTES, 512)
        r4096 = mbr_extent_report(layout.partitions, MKN52_RECOVERY_BYTES, 4096)
        self.assertFalse(r512["fill"])
        self.assertEqual(r512["rows"][0]["size_byte"], 1_975_795_200)
        self.assertEqual(r512["rows"][0]["start_byte"], 32_768)
        self.assertTrue(r4096["fill"])
        self.assertEqual(r4096["slack"], 0)
        self.assertEqual(r4096["rows"][0]["start_byte"], 262_144)
        self.assertEqual(r4096["rows"][0]["size_byte"], 15_806_361_600)

    def test_inspect_resolves_4096_not_unknown(self):
        layout = inspect_bytes(encode_mkn52_repartition_mbr(), MKN52_RECOVERY_BYTES)
        self.assertEqual(layout.sector_size, 4096)
        self.assertTrue(layout.sector_resolved)
        self.assertTrue(layout.is_ipodrepartition_data())
        self.assertTrue(layout.is_ipodrepartition_4k_fat32())
        self.assertFalse(layout.is_winpod())
        self.assertNotIn("unknown (fail closed", layout.summary())
        self.assertIn("IPodRepartition", layout.summary())
        data = layout.data
        assert data is not None
        self.assertEqual(data.start_lba, 64)
        self.assertEqual(data.size_lba, 3_858_975)
        self.assertEqual(data.start_bytes, MKN52_RECOVERY_FAT_START_BYTES)
        self.assertEqual(data.size_bytes, MKN52_RECOVERY_FAT_BYTES)
        self.assertEqual(data.start_bytes + data.size_bytes, MKN52_RECOVERY_BYTES)
        self.assertNotEqual(data.size_bytes, 1_975_795_200)
        pick_format_partition(layout)

    def test_tiny_slice_does_not_resolve(self):
        raw = encode_mbr([(0x0C, 64, 100)], 64 + 100)
        layout = inspect_bytes(raw, MKN52_RECOVERY_BYTES)
        self.assertFalse(layout.sector_resolved)
        with self.assertRaises(Refused) as ctx:
            pick_format_partition(layout)
        msg = str(ctx.exception)
        self.assertIn("sector size", msg.lower())
        self.assertNotIn("DRY_RUN_OK", msg)

    def test_bot_dry_run_scsi_4096_write_plan(self):
        bot = SparseBot(encode_mkn52_repartition_mbr(), MKN52_RECOVERY_BYTES, 4096)
        out = format_data_slice_via_bot(bot=bot, write=False)
        self.assertFalse(out["wrote"])
        self.assertEqual(bot.writes, [])
        self.assertEqual(out["byte_offset"], 262_144)
        self.assertGreater(out["byte_length"], 0)
        self.assertEqual(out["scsi_lba"], 64)
        self.assertEqual(out["scsi_block_size"], 4096)
        self.assertEqual(out["fat_bytes_per_sector"], 4096)
        self.assertEqual(out["sectors_per_cluster"], 2)
        self.assertEqual(out["cluster_bytes"], 8192)
        self.assertEqual(out["hidden_sectors"], 64)
        self.assertEqual(out["byte_length"], 15_581_184)
        self.assertGreater(out["byte_length"], 4_030_464)  # 8 KiB clusters, not the earlier 32 KiB
        self.assertEqual(out["start_lba"], 64)
        self.assertEqual(out["size_lba"], 3_858_975)
        self.assertEqual(out["partition_bytes"], 15_806_361_600)

    def test_bot_dry_run_scsi_512_maps_to_lba_512(self):
        bot = SparseBot(encode_mkn52_repartition_mbr(), MKN52_RECOVERY_BYTES, 512)
        out = format_data_slice_via_bot(bot=bot, write=False)
        self.assertEqual(out["byte_offset"], 262_144)
        self.assertEqual(out["scsi_lba"], 512)
        self.assertEqual(out["scsi_block_size"], 512)
        self.assertEqual(out["fat_bytes_per_sector"], 4096)
        self.assertEqual(bot.writes, [])

    def test_bot_write_4k_skips_mbr_and_verifies_boot(self):
        bot = SparseBot(encode_mkn52_repartition_mbr(), MKN52_RECOVERY_BYTES, 4096)
        mbr_before = bot.read10(0, 1)
        out = format_data_slice_via_bot(bot=bot, write=True)
        self.assertTrue(out["wrote"])
        self.assertEqual(bot.read10(0, 1), mbr_before)
        for lba, chunk in bot.writes:
            self.assertGreaterEqual(lba, 64)
            self.assertEqual(len(chunk) % 4096, 0)
        boot = bot.read10(64, 1)
        self.assertTrue(is_fat32_boot(boot, bytes_per_sector=4096))
        self.assertEqual(fat32_bytes_per_sector(boot), 4096)
        self.assertEqual(boot[4094:4096], b"\x55\xaa")
        self.assertTrue(bot.synced)
        self.assertTrue(out["synccache"])
        self.assertEqual(boot[0x0D], 2)

    def test_scsi_dump_is_read_only_and_analyzes_empty_slice(self):
        bot = SparseBot(encode_mkn52_repartition_mbr(), MKN52_RECOVERY_BYTES, 4096)
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        dest = Path(td.name) / "dump"
        out = dump_data_slice_via_bot(dest, bot=bot, data_bytes=64 * 1024)
        self.assertFalse(out["wrote"])
        self.assertEqual(bot.writes, [])
        self.assertFalse(bot.synced)
        self.assertEqual(out["scsi_lba"], 64)
        self.assertTrue((dest / "dump-mbr.bin").is_file())
        self.assertTrue((dest / "dump-data-prefix.bin").is_file())
        text = (dest / "dump.txt").read_text(encoding="utf-8")
        self.assertIn("SCSI_DUMP_OK", text)
        self.assertIn("read-only", text.lower())
        self.assertIn("IPodRepartition", text)
        self.assertFalse(out["analysis"]["fat32"])

    def test_scsi_dump_after_format_sees_fat32_and_refuses_overwrite(self):
        bot = SparseBot(encode_mkn52_repartition_mbr(), MKN52_RECOVERY_BYTES, 4096)
        format_data_slice_via_bot(bot=bot, write=True)
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        dest = Path(td.name) / "dump"
        out = dump_data_slice_via_bot(dest, bot=bot, data_bytes=256 * 1024)
        self.assertFalse(out["wrote"])
        self.assertTrue(out["analysis"]["fat32"])
        self.assertEqual(out["analysis"]["spc"], 2)
        self.assertEqual(out["analysis"]["hidden_sectors"], 64)
        text = (dest / "dump.txt").read_text(encoding="utf-8")
        self.assertIn("FAT32 prefix analysis", text)
        self.assertIn("8192", text)
        self.assertTrue((dest / "dump-fat1-head.bin").is_file())
        self.assertTrue((dest / "dump-root.bin").is_file())
        with self.assertRaises(Refused) as ctx:
            dump_data_slice_via_bot(dest, bot=bot, data_bytes=4096)
        self.assertIn("already exists", str(ctx.exception))

    def test_analyze_built_prefix(self):
        prefix = build_fat32_prefix(
            MKN52_RECOVERY_FAT_BYTES,
            label="IPOD",
            hidden_sectors=64,
            bytes_per_sector=4096,
        )
        info = analyze_fat32_prefix(
            prefix,
            volume_bytes=MKN52_RECOVERY_FAT_BYTES,
            hidden_expect=64,
            bytes_per_sector=4096,
        )
        self.assertTrue(info["fat32"])
        self.assertTrue(info["covers_reserved_fats_root"])
        self.assertTrue(info["root_rest_zero"])
        cluster_issues = [i for i in info["issues"] if "sectors_per_cluster" in i]
        self.assertEqual(cluster_issues, [])

    def test_unresolved_extents_refuse_without_write(self):
        raw = encode_mbr([(0x0C, 64, 100)], 64 + 100)
        bot = SparseBot(raw, MKN52_RECOVERY_BYTES, 4096)
        with self.assertRaises(Refused) as ctx:
            format_data_slice_via_bot(bot=bot, write=True)
        self.assertIn("sector", str(ctx.exception).lower())
        self.assertEqual(bot.writes, [])
        self.assertNotIn("DRY_RUN_OK", str(ctx.exception))

    def test_parse_capacity_10_and_16(self):
        text = (
            "read_capacity_10 last_lba=3859038 block_size=4096 bytes=15806623744\n"
            "read_capacity_16 last_lba=3859038 block_size=4096 bytes=15806623744\n"
        )
        cap = parse_capacity_text(text)
        self.assertEqual(cap.last_lba, 3_859_038)
        self.assertEqual(cap.block_size, 4096)
        self.assertEqual(cap.size_bytes, MKN52_RECOVERY_BYTES)
        self.assertEqual(cap.last_lba_16, 3_859_038)
        self.assertEqual(cap.block_size_16, 4096)

    def test_parse_capacity_16_skipped(self):
        text = (
            "read_capacity_10 last_lba=3859038 block_size=4096 bytes=15806623744\n"
            "read_capacity_16 skipped (last_lba=3859038 < 0xFFFFFFFF)\n"
        )
        cap = parse_capacity_text(text)
        self.assertTrue(cap.rc16_skipped)
        self.assertEqual(cap.rc16_error, "")
        self.assertIsNone(cap.last_lba_16)
        shown = describe_capacity(cap)
        self.assertIn("skipped", shown)
        self.assertNotIn("error:", shown)


class BotStallTests(unittest.TestCase):
    def _inner(self):
        return SparseBot(encode_mkn52_repartition_mbr(), MKN52_RECOVERY_BYTES, 4096)

    def test_rc16_stall_cancels_next_cbw_unless_clear_halt(self):
        inner = self._inner()
        bot = HaltAwareBot(inner, send_rc16=True)
        cap = bot.capacity()
        self.assertTrue(cap.rc16_error)
        with self.assertRaises(Refused) as ctx:
            bot.read10(0, 1)
        self.assertIn("cancelled", str(ctx.exception))
        self.assertEqual(bot.cbw_fails, 1)
        bot.clear_halts()
        data = bot.read10(0, 1)
        self.assertGreaterEqual(len(data), 512)
        self.assertEqual(bot.clear_halt_calls, 1)

    def test_skip_rc16_keeps_next_cbw_alive(self):
        bot = HaltAwareBot(self._inner(), send_rc16=False)
        cap = bot.capacity()
        self.assertTrue(cap.rc16_skipped)
        data = bot.read10(0, 1)
        self.assertGreaterEqual(len(data), 512)
        self.assertEqual(bot.cbw_fails, 0)

    def test_format_inspect_and_write_share_one_bot(self):
        inner = self._inner()
        bot = HaltAwareBot(inner, send_rc16=False)
        plan = format_data_slice_via_bot(bot=bot, write=False)
        out = format_data_slice_via_bot(bot=bot, write=True, plan=plan)
        self.assertTrue(out["wrote"])
        self.assertEqual(bot.capacity_calls, 1)
        self.assertTrue(inner.writes)
        self.assertTrue(inner.synced)

    def test_snapshot_failure_refuses_if_lba0_also_fails(self):
        from host.winpod import _save_preformat_snapshot

        class DeadBot:
            def read10(self, lba, count):
                raise Refused("usbscsi: CBW: transfer was cancelled")

            def capacity(self):
                raise Refused("usbscsi: CBW: transfer was cancelled")

        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        os.environ["NANO7_STATE_DIR"] = td.name
        with mock.patch(
            "host.usbms.dump_data_slice_via_bot",
            side_effect=Refused("usbscsi read10 failed: CBW: transfer was cancelled"),
        ):
            with self.assertRaises(Refused) as ctx:
                _save_preformat_snapshot(serial="SN1", bot=DeadBot())
        msg = str(ctx.exception)
        self.assertIn("BOT reads are broken", msg)
        self.assertIn("not writing", msg)

    def test_snapshot_failure_continues_if_lba0_reads(self):
        from host.winpod import _save_preformat_snapshot

        inner = self._inner()
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        os.environ["NANO7_STATE_DIR"] = td.name
        with mock.patch(
            "host.usbms.dump_data_slice_via_bot",
            side_effect=Refused("usbscsi read10 failed: snapshot boom"),
        ):
            dest = _save_preformat_snapshot(serial="SN1", bot=inner)
        self.assertIsNone(dest)
        probe_read_lba0(inner)

    def test_second_session_cbw_cancelled_until_open_clear_halt(self):
        inner = self._inner()
        first = HaltAwareBot(inner, send_rc16=True)
        first.capacity()
        self.assertTrue(first.halted)
        second = HaltAwareBot(inner, send_rc16=False)
        second.halted = first.halted
        with self.assertRaises(Refused) as ctx:
            second.capacity()
        self.assertIn("cancelled", str(ctx.exception))
        second.clear_halts()
        cap = second.capacity()
        self.assertTrue(cap.rc16_skipped)
        self.assertGreaterEqual(len(second.read10(0, 1)), 512)

    def test_format_inspect_fails_if_rc16_stalls_same_session(self):
        bot = HaltAwareBot(self._inner(), send_rc16=True)
        with self.assertRaises(Refused) as ctx:
            format_data_slice_via_bot(bot=bot, write=False)
        self.assertIn("cancelled", str(ctx.exception))
        self.assertEqual(bot.write_calls, 0)

    def test_write_not_issued_when_halted(self):
        inner = self._inner()
        bot = HaltAwareBot(inner, send_rc16=False)
        bot.halted = True
        with self.assertRaises(Refused):
            bot.write10(64, b"\x00" * 4096)
        self.assertEqual(inner.writes, [])

    def test_scsi_dump_skips_rc16_and_never_writes(self):
        dest = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(dest, ignore_errors=True))
        bot = HaltAwareBot(self._inner(), send_rc16=False)
        out = dump_data_slice_via_bot(dest, bot=bot, data_bytes=4096)
        self.assertEqual(out.get("method"), "scsi-dump")
        self.assertFalse(out.get("wrote"))
        self.assertEqual(bot.cbw_fails, 0)
        self.assertEqual(bot.write_calls, 0)
        self.assertTrue(bot.capacity_calls >= 1)
        self.assertTrue((dest / "dump.txt").is_file())


class RecoveryUsbStateTests(unittest.TestCase):
    def test_disk_wins(self):
        with mock.patch(
            "host.usb.apple_pids",
            return_value=[0x1234, 0x1267],
        ):
            self.assertEqual(recovery_usb_state(), "disk")

    def test_wtf_late(self):
        with mock.patch("host.usb.apple_pids", return_value=[0x124A]):
            self.assertEqual(recovery_usb_state(), "wtf")

    def test_dfu(self):
        with mock.patch("host.usb.apple_pids", return_value=[0x1234]):
            self.assertEqual(recovery_usb_state(), "dfu")

    def test_none(self):
        with mock.patch("host.usb.apple_pids", return_value=[]):
            self.assertEqual(recovery_usb_state(), "none")


class FormatOnlyFlowTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        os.environ["NANO7_STATE_DIR"] = self.td.name
        os.environ["NANO7KIT_CONFIG_DIR"] = str(Path(self.td.name) / "cfg")
        # Match build_mbr_winpod(16384) so live SCSI size == hashed backup size.
        self.size = 16384 * 512
        self.backup = Path(self.td.name) / "nano.img"
        with open(self.backup, "wb") as f:
            f.truncate(self.size)
        self.bhash = sha256_file(self.backup)
        self.wind3x = Path(self.td.name) / "fake-wind3x"
        self.wind3x.write_text("#!/bin/sh\nexit 0\n")
        self.wind3x.chmod(self.wind3x.stat().st_mode | stat.S_IXUSR)
        from host.state import remember_backup

        remember_backup("USB1", self.backup, self.bhash, self.size)

    def _nano(self):
        from host.device import Nano, annotate_size

        return annotate_size(
            Nano(
                os_name="linux",
                device="/dev/loop9",
                raw_device="/dev/loop9",
                partition="/dev/loop9p2",
                usb_serial="USB1",
                size=self.size,
                fstype="hfs",
                mountpoints=[],
                model="iPod",
            )
        )

    def test_format_only_run_one_usbscsi_session(self):
        from host.layout import write_mkn52_finder_image
        from host.state import write_receipt

        finder = write_mkn52_finder_image(Path(self.td.name) / "finder-one-session.img")
        fake_hash = "ab" * 32
        write_receipt("FAT32_STARTED", {"serial": "XYZ123ABC456", "utc": "t"})
        inner = SparseBot(encode_mkn52_repartition_mbr(), MKN52_RECOVERY_BYTES, 4096)
        opens = []

        class FakeBot(HaltAwareBot):
            def __init__(self):
                opens.append(1)
                HaltAwareBot.__init__(self, inner, send_rc16=False)

        buf = __import__("io").StringIO()
        with mock.patch("host.safety.sha256_file", return_value=fake_hash), mock.patch(
            "host.winpod.find_nanos", return_value=[]
        ), mock.patch(
            "host.winpod.diskmode_usb_present", return_value=True
        ), mock.patch(
            "host.winpod.restore_full",
            side_effect=AssertionError("restore --full must not run"),
        ), mock.patch(
            "host.usbms.UsbScsiBot", FakeBot
        ), mock.patch("sys.stdout", buf):
            out = convert_nano(
                None,
                backup=finder,
                backup_sha256=fake_hash,
                serial="XYZ123ABC456",
                dry_run=False,
                format_only=True,
                accept_untested_flasher=True,
                accept_2015_refresh=True,
                typed_confirm="FAT32",
            )
        printed = buf.getvalue()
        self.assertTrue(out.get("ok"))
        self.assertTrue(out.get("format_only"))
        self.assertEqual(len(opens), 1)
        self.assertTrue(inner.writes)
        self.assertTrue(inner.synced)
        self.assertEqual(inner.writes[0][0], 64)
        self.assertEqual(inner.writes[0][1][4094:4096], b"\x55\xaa")
        self.assertIn("skipped", printed)
        self.assertNotIn("READ CAPACITY(16) error", printed)
        self.assertIn("pre-format snapshot", printed)
        self.assertIn("SYNCHRONIZE CACHE", printed)

    def test_format_only_dry_run_one_usbscsi_session_skips_rc16(self):
        from host.layout import write_mkn52_finder_image
        from host.state import write_receipt

        finder = write_mkn52_finder_image(Path(self.td.name) / "finder-dry-session.img")
        fake_hash = "ab" * 32
        write_receipt("FAT32_STARTED", {"serial": "XYZ123ABC456", "utc": "t"})
        inner = SparseBot(encode_mkn52_repartition_mbr(), MKN52_RECOVERY_BYTES, 4096)
        opens = []

        class FakeBot(HaltAwareBot):
            def __init__(self):
                opens.append(1)
                HaltAwareBot.__init__(self, inner, send_rc16=False)

        buf = __import__("io").StringIO()
        with mock.patch("host.safety.sha256_file", return_value=fake_hash), mock.patch(
            "host.winpod.find_nanos", return_value=[]
        ), mock.patch(
            "host.winpod.diskmode_usb_present", return_value=True
        ), mock.patch(
            "host.winpod.restore_full",
            side_effect=AssertionError("restore --full must not run"),
        ), mock.patch(
            "host.usbms.UsbScsiBot", FakeBot
        ), mock.patch("sys.stdout", buf):
            out = convert_nano(
                None,
                backup=finder,
                backup_sha256=fake_hash,
                serial="XYZ123ABC456",
                dry_run=True,
                format_only=True,
            )
        printed = buf.getvalue()
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["byte_offset"], 262_144)
        self.assertEqual(out["byte_length"], 15_581_184)
        self.assertEqual(len(opens), 1)
        self.assertEqual(inner.writes, [])
        self.assertFalse(inner.synced)
        self.assertIn("skipped", printed)
        self.assertIn("DRY_RUN_OK", printed)
        self.assertNotIn("READ CAPACITY(16) error", printed)

    def test_scsi_dump_one_usbscsi_session_skips_rc16(self):
        dest = Path(self.td.name) / "scsi-one-session"
        inner = SparseBot(encode_mkn52_repartition_mbr(), MKN52_RECOVERY_BYTES, 4096)
        opens = []

        class FakeBot(HaltAwareBot):
            def __init__(self):
                opens.append(1)
                HaltAwareBot.__init__(self, inner, send_rc16=False)

            def write10(self, lba, data):
                raise AssertionError("scsi-dump must not WRITE(10)")

        buf = __import__("io").StringIO()
        with mock.patch("host.winpod.find_nanos", return_value=[]), mock.patch(
            "host.winpod.diskmode_usb_present", return_value=True
        ), mock.patch(
            "host.winpod.restore_full",
            side_effect=AssertionError("restore --full must not run on scsi-dump"),
        ), mock.patch(
            "host.usbms.UsbScsiBot", FakeBot
        ), mock.patch("sys.stdout", buf):
            out = convert_nano(
                None,
                backup=self.backup,
                backup_sha256=self.bhash,
                serial="SN1",
                dry_run=True,
                scsi_dump=dest,
                allow_size=self.size,
                confirm_untested=True,
                skip_backup_file=True,
            )
        printed = buf.getvalue()
        self.assertEqual(out.get("method"), "scsi-dump")
        self.assertFalse(out.get("wrote"))
        self.assertEqual(len(opens), 1)
        self.assertEqual(inner.writes, [])
        self.assertIn("SCSI_DUMP_OK", printed)
        self.assertIn("skipped", printed)
        self.assertTrue((dest / "dump.txt").is_file())

    def test_format_only_dry_run_does_not_restore_full_or_write(self):
        called = {"full": False, "write": False}

        def boom(*a, **k):
            called["full"] = True
            raise AssertionError("restore --full must not run on format-only")

        blob = bytearray(build_mbr_winpod(16384, fw_start=63, fw_size=200))
        bot = MemoryBot(blob)

        def bot_call(*a, write=True, **k):
            if write:
                called["write"] = True
                raise AssertionError("BOT write must not run on dry-run")
            return format_data_slice_via_bot(bot=bot, write=False)

        with mock.patch("host.winpod.find_nanos", return_value=[]), mock.patch(
            "host.winpod.diskmode_usb_present", return_value=True
        ), mock.patch("host.winpod.restore_full", side_effect=boom), mock.patch(
            "host.usbms.format_data_slice_via_bot", side_effect=bot_call
        ):
            out = convert_nano(
                None,
                backup=self.backup,
                backup_sha256=self.bhash,
                serial="SN1",
                dry_run=True,
                format_only=True,
                allow_size=self.size,
                confirm_untested=True,
                skip_backup_file=True,
            )
        self.assertTrue(out["dry_run"])
        self.assertTrue(out.get("format_only"))
        self.assertFalse(called["full"])
        self.assertFalse(called["write"])
        self.assertEqual(bot.writes, [])
        self.assertIn("byte_offset", out)

    def test_format_only_run_uses_bot_not_restore_full(self):
        blob = bytearray(
            build_mbr_winpod(16384, fw_start=63, fw_size=200, firmware_marker=b"FWREGION")
        )
        bot = MemoryBot(blob)
        called = {"full": False}

        def boom(*a, **k):
            called["full"] = True
            raise AssertionError("restore --full must not run on format-only")

        with mock.patch("host.winpod.find_nanos", return_value=[]), mock.patch(
            "host.winpod.diskmode_usb_present", return_value=True
        ), mock.patch("host.winpod.restore_full", side_effect=boom), mock.patch(
            "host.usbms.inspect_via_bot",
            return_value=inspect_bytes(bytes(blob[:1024]), len(blob)),
        ), mock.patch(
            "host.usbms.format_data_slice_via_bot",
            side_effect=lambda *a, write=True, **k: format_data_slice_via_bot(
                bot=bot, write=write
            ),
        ), mock.patch(
            "host.winpod._save_preformat_snapshot", return_value=None
        ):
            out = convert_nano(
                None,
                backup=self.backup,
                backup_sha256=self.bhash,
                serial="SN1",
                dry_run=False,
                format_only=True,
                allow_size=self.size,
                confirm_untested=True,
                skip_backup_file=True,
                typed_confirm="FAT32",
            )
        self.assertTrue(out.get("ok"))
        self.assertTrue(out.get("format_only"))
        self.assertFalse(called["full"])
        self.assertTrue(bot.writes)
        self.assertEqual(bytes(blob[63 * 512 : 63 * 512 + 8]), b"FWREGION")

    def _ghost_nano(self):
        from host.device import Nano

        return Nano(
            os_name="darwin",
            device="/dev/disk9",
            raw_device="/dev/rdisk9",
            partition="",
            usb_serial="USB1",
            size=0,
            fstype="",
            mountpoints=[],
            model="iPod",
        )

    def test_format_only_dfu_calls_prepare_not_full(self):
        called = {"full": 0, "prepare": 0}
        diskmode = {"on": False}

        def prepare(*a, **k):
            called["prepare"] += 1
            diskmode["on"] = True
            return (
                'Found device device="Nano 7G (Mid-2015)" interface=diskmode\n'
                "SerialNumber: SN1\n"
                "Recovery mode ready; no device partitions were written\n"
            )

        def boom(*a, **k):
            called["full"] += 1
            raise AssertionError("restore --full must not run")

        blob = bytearray(build_mbr_winpod(16384, fw_start=63, fw_size=200))
        bot = MemoryBot(blob)
        with mock.patch("host.winpod.find_nanos", return_value=[]), mock.patch(
            "host.winpod.diskmode_usb_present", side_effect=lambda: diskmode["on"]
        ), mock.patch("host.winpod.late_wtf_present", return_value=False), mock.patch(
            "host.winpod.select_wind3x", return_value=(self.wind3x, False, "fake")
        ), mock.patch(
            "host.winpod.wait_recovery_entry", return_value="dfu"
        ), mock.patch(
            "host.winpod.prepare_restore", side_effect=prepare
        ), mock.patch(
            "host.winpod.restore_full", side_effect=boom
        ), mock.patch(
            "host.winpod._save_preformat_snapshot", return_value=None
        ), mock.patch(
            "host.usbms.format_data_slice_via_bot",
            side_effect=lambda *a, write=True, **k: format_data_slice_via_bot(
                bot=bot, write=write
            ),
        ):
            out = convert_nano(
                None,
                backup=self.backup,
                backup_sha256=self.bhash,
                serial="SN1",
                dry_run=False,
                format_only=True,
                allow_size=self.size,
                confirm_untested=True,
                skip_backup_file=True,
                accept_untested_flasher=True,
                accept_2015_refresh=True,
                typed_confirm="FAT32",
            )
        self.assertTrue(out.get("ok"))
        self.assertEqual(called["prepare"], 1)
        self.assertEqual(called["full"], 0)
        self.assertTrue(bot.writes)
        self.assertTrue(bot.synced)

    def test_format_only_run_enters_recovery_before_size_check(self):
        """--run --format-only from USB none: DFU -> prepare-only -> 1267, then size."""
        from host.layout import write_mkn52_finder_image
        from host.state import write_receipt
        from host.winpod import check_format_only_backup_vs_live as real_gate

        finder = write_mkn52_finder_image(Path(self.td.name) / "finder-none.img")
        fake_hash = "ab" * 32
        write_receipt("FAT32_STARTED", {"serial": "XYZ123ABC456", "utc": "t"})
        bot = SparseBot(encode_mkn52_repartition_mbr(), MKN52_RECOVERY_BYTES, 4096)
        order = []
        diskmode = {"on": False}

        def present():
            return diskmode["on"]

        def wait_entry(*a, **k):
            order.append("wait_dfu")
            return "dfu"

        def prepare(*a, **k):
            order.append("prepare")
            diskmode["on"] = True
            return (
                'Found device device="Nano 7G (Mid-2015)" interface=diskmode\n'
                "SerialNumber: XYZ123ABC456\n"
                "Recovery mode ready; no device partitions were written\n"
            )

        def bot_call(*a, write=True, **k):
            order.append("bot_write" if write else "bot_inspect")
            return format_data_slice_via_bot(bot=bot, write=write)

        def gate(*a, **k):
            order.append("size_gate")
            return real_gate(*a, **k)

        def boom(*a, **k):
            raise AssertionError("restore --full must not run")

        buf = __import__("io").StringIO()
        with mock.patch("host.safety.sha256_file", return_value=fake_hash), mock.patch(
            "host.winpod.find_nanos", return_value=[]
        ), mock.patch(
            "host.winpod.diskmode_usb_present", side_effect=present
        ), mock.patch("host.winpod.late_wtf_present", return_value=False), mock.patch(
            "host.winpod.select_wind3x", return_value=(self.wind3x, False, "fake")
        ), mock.patch(
            "host.winpod.wait_recovery_entry", side_effect=wait_entry
        ), mock.patch(
            "host.winpod.prepare_restore", side_effect=prepare
        ), mock.patch("host.winpod.restore_full", side_effect=boom), mock.patch(
            "host.winpod.check_format_only_backup_vs_live", side_effect=gate
        ), mock.patch(
            "host.usbms.format_data_slice_via_bot", side_effect=bot_call
        ), mock.patch(
            "host.winpod._save_preformat_snapshot", return_value=None
        ), mock.patch("sys.stdout", buf):
            out = convert_nano(
                None,
                backup=finder,
                backup_sha256=fake_hash,
                serial="XYZ123ABC456",
                dry_run=False,
                format_only=True,
                accept_untested_flasher=True,
                accept_2015_refresh=True,
                typed_confirm="FAT32",
            )
        printed = buf.getvalue()
        self.assertTrue(out.get("ok"))
        self.assertTrue(out.get("format_only"))
        self.assertIn("wait_dfu", order)
        self.assertIn("prepare", order)
        self.assertIn("bot_inspect", order)
        self.assertIn("size_gate", order)
        self.assertIn("bot_write", order)
        self.assertLess(order.index("wait_dfu"), order.index("prepare"))
        self.assertLess(order.index("prepare"), order.index("bot_inspect"))
        self.assertLess(order.index("bot_inspect"), order.index("size_gate"))
        self.assertLess(order.index("size_gate"), order.index("bot_write"))
        self.assertTrue(bot.writes)
        self.assertTrue(bot.synced)
        self.assertEqual(bot.writes[0][0], 64)
        self.assertIn("IPodRepartition grew the USB map", printed)
        self.assertNotIn("shorter than one sector", printed)

    def test_format_only_ignores_size_zero_kernel_then_enters_recovery(self):
        from host.layout import write_mkn52_finder_image
        from host.state import write_receipt
        from host.winpod import check_format_only_backup_vs_live as real_gate

        finder = write_mkn52_finder_image(Path(self.td.name) / "finder-ghost.img")
        fake_hash = "ab" * 32
        write_receipt("FAT32_STARTED", {"serial": "XYZ123ABC456", "utc": "t"})
        bot = SparseBot(encode_mkn52_repartition_mbr(), MKN52_RECOVERY_BYTES, 4096)
        order = []
        diskmode = {"on": False}
        ghost = self._ghost_nano()

        def wait_entry(*a, **k):
            order.append("wait_dfu")
            return "dfu"

        def prepare(*a, **k):
            order.append("prepare")
            diskmode["on"] = True
            return (
                'Found device device="Nano 7G (Mid-2015)" interface=diskmode\n'
                "SerialNumber: XYZ123ABC456\n"
                "Recovery mode ready; no device partitions were written\n"
            )

        def bot_call(*a, write=True, **k):
            order.append("bot_write" if write else "bot_inspect")
            return format_data_slice_via_bot(bot=bot, write=write)

        def gate(*a, **k):
            order.append("size_gate")
            self.assertEqual(k["live_size"], MKN52_RECOVERY_BYTES)
            self.assertGreater(k["live_size"], 0)
            return real_gate(*a, **k)

        buf = __import__("io").StringIO()
        with mock.patch("host.safety.sha256_file", return_value=fake_hash), mock.patch(
            "host.winpod.find_nanos", return_value=[ghost]
        ), mock.patch(
            "host.winpod.diskmode_usb_present", side_effect=lambda: diskmode["on"]
        ), mock.patch("host.winpod.late_wtf_present", return_value=False), mock.patch(
            "host.winpod.select_wind3x", return_value=(self.wind3x, False, "fake")
        ), mock.patch(
            "host.winpod.wait_recovery_entry", side_effect=wait_entry
        ), mock.patch(
            "host.winpod.prepare_restore", side_effect=prepare
        ), mock.patch("host.winpod.restore_full") as rf, mock.patch(
            "host.winpod.check_format_only_backup_vs_live", side_effect=gate
        ), mock.patch(
            "host.usbms.format_data_slice_via_bot", side_effect=bot_call
        ), mock.patch(
            "host.winpod._save_preformat_snapshot", return_value=None
        ), mock.patch("sys.stdout", buf):
            out = convert_nano(
                ghost,
                backup=finder,
                backup_sha256=fake_hash,
                serial="XYZ123ABC456",
                dry_run=False,
                format_only=True,
                accept_untested_flasher=True,
                accept_2015_refresh=True,
                typed_confirm="FAT32",
            )
        printed = buf.getvalue()
        self.assertTrue(out.get("ok"))
        self.assertIn("ignoring kernel node", printed)
        self.assertIn("size 0", printed)
        self.assertLess(order.index("prepare"), order.index("size_gate"))
        self.assertLess(order.index("prepare"), order.index("bot_inspect"))
        rf.assert_not_called()
        self.assertTrue(bot.writes)

    def test_format_only_dfu_requires_untested_flasher(self):
        with mock.patch("host.winpod.find_nanos", return_value=[]), mock.patch(
            "host.winpod.diskmode_usb_present", return_value=False
        ), mock.patch(
            "host.winpod.select_wind3x", return_value=(self.wind3x, False, "fake")
        ), mock.patch("host.winpod.restore_full") as rf:
            with self.assertRaises(Refused) as ctx:
                convert_nano(
                    None,
                    backup=self.backup,
                    backup_sha256=self.bhash,
                    serial="SN1",
                    dry_run=False,
                    format_only=True,
                    allow_size=self.size,
                    confirm_untested=True,
                    skip_backup_file=True,
                    accept_untested_flasher=False,
                    typed_confirm="FAT32",
                )
        self.assertIn("untested-flasher", str(ctx.exception))
        rf.assert_not_called()

    def test_format_only_wrong_typed_word_refused(self):
        blob = bytearray(build_mbr_winpod(16384, fw_start=63, fw_size=200))
        layout = inspect_bytes(bytes(blob[:1024]), len(blob))

        def plan_only(*a, write=True, **k):
            self.assertFalse(write)
            return {
                "layout": layout,
                "byte_offset": 63 * 512,
                "byte_length": 1024,
                "method": "scsi",
            }

        with mock.patch("host.winpod.find_nanos", return_value=[]), mock.patch(
            "host.winpod.diskmode_usb_present", return_value=True
        ), mock.patch(
            "host.usbms.inspect_via_bot",
            return_value=layout,
        ), mock.patch(
            "host.usbms.format_data_slice_via_bot", side_effect=plan_only
        ), mock.patch("host.winpod.restore_full") as rf:
            with self.assertRaises(Refused) as ctx:
                convert_nano(
                    None,
                    backup=self.backup,
                    backup_sha256=self.bhash,
                    serial="SN1",
                    dry_run=False,
                    format_only=True,
                    allow_size=self.size,
                    confirm_untested=True,
                    skip_backup_file=True,
                    typed_confirm="FLASH",
                )
        self.assertIn("FAT32", str(ctx.exception))
        rf.assert_not_called()

    def test_format_only_dry_run_mkn52_4k_plan(self):
        from host.layout import write_mkn52_finder_image
        from host.state import write_receipt

        finder = write_mkn52_finder_image(Path(self.td.name) / "finder.img")
        fake_hash = "ab" * 32
        write_receipt("FAT32_STARTED", {"serial": "XYZ123ABC456", "utc": "t"})
        bot = SparseBot(encode_mkn52_repartition_mbr(), MKN52_RECOVERY_BYTES, 4096)

        def bot_call(*a, write=True, **k):
            self.assertFalse(write)
            return format_data_slice_via_bot(bot=bot, write=False)

        buf = __import__("io").StringIO()
        with mock.patch("host.safety.sha256_file", return_value=fake_hash), mock.patch(
            "host.winpod.find_nanos", return_value=[]
        ), mock.patch(
            "host.winpod.diskmode_usb_present", return_value=True
        ), mock.patch("host.winpod.restore_full") as rf, mock.patch(
            "host.usbms.format_data_slice_via_bot", side_effect=bot_call
        ), mock.patch("sys.stdout", buf):
            out = convert_nano(
                None,
                backup=finder,
                backup_sha256=fake_hash,
                serial="XYZ123ABC456",
                dry_run=True,
                format_only=True,
            )
        printed = buf.getvalue()
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["byte_offset"], 262_144)
        self.assertEqual(bot.writes, [])
        rf.assert_not_called()
        self.assertIn("IPodRepartition grew the USB map", printed)
        self.assertIn("15798411264", printed)
        self.assertIn("15806623744", printed)
        self.assertIn("8212480", printed)
        self.assertIn("FAT32_STARTED", printed)

    def test_format_only_dry_run_unresolved_refuses(self):
        raw = encode_mbr([(0x0C, 64, 100)], 64 + 100)
        bot = SparseBot(raw, MKN52_RECOVERY_BYTES, 4096)

        def bot_call(*a, write=True, **k):
            return format_data_slice_via_bot(bot=bot, write=False)

        with mock.patch("host.winpod.find_nanos", return_value=[]), mock.patch(
            "host.winpod.diskmode_usb_present", return_value=True
        ), mock.patch("host.winpod.restore_full") as rf, mock.patch(
            "host.usbms.format_data_slice_via_bot", side_effect=bot_call
        ):
            with self.assertRaises(Refused) as ctx:
                convert_nano(
                    None,
                    backup=self.backup,
                    backup_sha256=self.bhash,
                    serial="SN1",
                    dry_run=True,
                    format_only=True,
                    allow_size=self.size,
                    confirm_untested=True,
                    skip_backup_file=True,
                )
        self.assertNotIn("DRY_RUN_OK", str(ctx.exception))
        self.assertEqual(bot.writes, [])
        rf.assert_not_called()

    def test_format_only_dfu_dry_run_without_1267_refuses(self):
        with mock.patch("host.winpod.find_nanos", return_value=[]), mock.patch(
            "host.winpod.diskmode_usb_present", return_value=False
        ), mock.patch("host.winpod.late_wtf_present", return_value=False), mock.patch(
            "host.winpod.select_wind3x", return_value=(self.wind3x, False, "fake")
        ), mock.patch(
            "host.winpod.wait_recovery_entry", return_value="dfu"
        ), mock.patch(
            "host.winpod.prepare_restore",
            return_value="SerialNumber: SN1\nRecovery mode ready; no device partitions were written\n",
        ), mock.patch("host.winpod.restore_full") as rf:
            with self.assertRaises(Refused) as ctx:
                convert_nano(
                    None,
                    backup=self.backup,
                    backup_sha256=self.bhash,
                    serial="SN1",
                    dry_run=True,
                    format_only=True,
                    allow_size=self.size,
                    confirm_untested=True,
                    skip_backup_file=True,
                    accept_untested_flasher=True,
                    accept_2015_refresh=True,
                )
        msg = str(ctx.exception)
        self.assertIn("device not in recovery diskmode", msg)
        self.assertIn("1267", msg)
        self.assertNotIn("DRY_RUN_OK", msg)
        self.assertNotIn("shorter than one sector", msg)
        rf.assert_not_called()

    def test_scsi_dump_never_restore_full_or_write(self):
        dest = Path(self.td.name) / "scsi-dump"
        called = {"full": False}

        def boom(*a, **k):
            called["full"] = True
            raise AssertionError("restore --full must not run on scsi-dump")

        dumped = {"n": 0}

        def dump_ok(d, **k):
            dumped["n"] += 1
            Path(d).mkdir(parents=True, exist_ok=True)
            return {"method": "scsi-dump", "wrote": False, "dest": str(d)}

        with mock.patch("host.winpod.find_nanos", return_value=[]), mock.patch(
            "host.winpod.diskmode_usb_present", return_value=True
        ), mock.patch("host.winpod.restore_full", side_effect=boom), mock.patch(
            "host.usbms.dump_data_slice_via_bot", side_effect=dump_ok
        ):
            out = convert_nano(
                None,
                backup=self.backup,
                backup_sha256=self.bhash,
                serial="SN1",
                dry_run=True,
                scsi_dump=dest,
                allow_size=self.size,
                confirm_untested=True,
                skip_backup_file=True,
            )
        self.assertEqual(out.get("method"), "scsi-dump")
        self.assertFalse(out.get("wrote"))
        self.assertFalse(called["full"])
        self.assertEqual(dumped["n"], 1)

    def test_scsi_dump_refuses_with_format_only(self):
        with mock.patch("host.winpod.find_nanos", return_value=[]), mock.patch(
            "host.winpod.restore_full"
        ) as rf:
            with self.assertRaises(Refused) as ctx:
                convert_nano(
                    None,
                    backup=self.backup,
                    backup_sha256=self.bhash,
                    serial="SN1",
                    dry_run=True,
                    format_only=True,
                    scsi_dump=Path(self.td.name) / "d",
                    allow_size=self.size,
                    confirm_untested=True,
                    skip_backup_file=True,
                )
        self.assertIn("scsi-dump", str(ctx.exception).lower())
        rf.assert_not_called()

    def test_scsi_dump_from_none_enters_prepare_then_dumps(self):
        dest = Path(self.td.name) / "scsi-dump-none"
        order = []
        diskmode = {"on": False}

        def wait_entry(*a, **k):
            order.append("wait_dfu")
            return "dfu"

        def prepare(*a, **k):
            order.append("prepare")
            diskmode["on"] = True
            return (
                'Found device device="Nano 7G (Mid-2015)" interface=diskmode\n'
                "SerialNumber: SN1\n"
                "Recovery mode ready; no device partitions were written\n"
            )

        def dump_ok(d, **k):
            order.append("scsi_dump")
            Path(d).mkdir(parents=True, exist_ok=True)
            return {"method": "scsi-dump", "wrote": False, "dest": str(d)}

        with mock.patch("host.winpod.find_nanos", return_value=[]), mock.patch(
            "host.winpod.diskmode_usb_present", side_effect=lambda: diskmode["on"]
        ), mock.patch("host.winpod.late_wtf_present", return_value=False), mock.patch(
            "host.winpod.select_wind3x", return_value=(self.wind3x, False, "fake")
        ), mock.patch(
            "host.winpod.wait_recovery_entry", side_effect=wait_entry
        ), mock.patch(
            "host.winpod.prepare_restore", side_effect=prepare
        ), mock.patch("host.winpod.restore_full") as rf, mock.patch(
            "host.usbms.dump_data_slice_via_bot", side_effect=dump_ok
        ), mock.patch(
            "host.usbms.format_data_slice_via_bot"
        ) as fmt:
            out = convert_nano(
                None,
                backup=self.backup,
                backup_sha256=self.bhash,
                serial="SN1",
                dry_run=True,
                scsi_dump=dest,
                allow_size=self.size,
                confirm_untested=True,
                skip_backup_file=True,
                accept_untested_flasher=True,
                accept_2015_refresh=True,
            )
        self.assertEqual(out.get("method"), "scsi-dump")
        self.assertFalse(out.get("wrote"))
        self.assertEqual(order, ["wait_dfu", "prepare", "scsi_dump"])
        rf.assert_not_called()
        fmt.assert_not_called()

    def test_scsi_dump_ignores_size_zero_kernel_then_enters_recovery(self):
        dest = Path(self.td.name) / "scsi-dump-ghost"
        order = []
        diskmode = {"on": False}
        ghost = self._ghost_nano()

        def wait_entry(*a, **k):
            order.append("wait_dfu")
            return "dfu"

        def prepare(*a, **k):
            order.append("prepare")
            diskmode["on"] = True
            return (
                'Found device device="Nano 7G (Mid-2015)" interface=diskmode\n'
                "SerialNumber: SN1\n"
                "Recovery mode ready; no device partitions were written\n"
            )

        def dump_ok(d, **k):
            order.append("scsi_dump")
            Path(d).mkdir(parents=True, exist_ok=True)
            return {"method": "scsi-dump", "wrote": False, "dest": str(d)}

        buf = __import__("io").StringIO()
        with mock.patch("host.winpod.find_nanos", return_value=[ghost]), mock.patch(
            "host.winpod.diskmode_usb_present", side_effect=lambda: diskmode["on"]
        ), mock.patch("host.winpod.late_wtf_present", return_value=False), mock.patch(
            "host.winpod.select_wind3x", return_value=(self.wind3x, False, "fake")
        ), mock.patch(
            "host.winpod.wait_recovery_entry", side_effect=wait_entry
        ), mock.patch(
            "host.winpod.prepare_restore", side_effect=prepare
        ), mock.patch("host.winpod.restore_full") as rf, mock.patch(
            "host.usbms.dump_data_slice_via_bot", side_effect=dump_ok
        ), mock.patch("sys.stdout", buf):
            out = convert_nano(
                ghost,
                backup=self.backup,
                backup_sha256=self.bhash,
                serial="SN1",
                dry_run=True,
                scsi_dump=dest,
                allow_size=self.size,
                confirm_untested=True,
                skip_backup_file=True,
                accept_untested_flasher=True,
                accept_2015_refresh=True,
            )
        printed = buf.getvalue()
        self.assertEqual(out.get("method"), "scsi-dump")
        self.assertFalse(out.get("wrote"))
        self.assertIn("ignoring kernel node", printed)
        self.assertEqual(order, ["wait_dfu", "prepare", "scsi_dump"])
        rf.assert_not_called()


if __name__ == "__main__":
    unittest.main()
