#!/usr/bin/env python3
"""Darwin /Apps copy via the mounted volume; no fat32 suggestion when BPB exists."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host.compat import Refused, sha256_file
from host.const import IPODREPARTITION_DISK_SIZE, RESIDENT_SHA256, TESTED_DISK_SIZE
from host.device import Nano, annotate_size
from host.fat import (
    close_finder_ipod_windows,
    ensure_fat_mount,
    fat32_bpb_present,
    fsync_path,
    mounted_root,
    read_aligned,
    refuse_unreadable_fat,
    unmount,
)
from host.fat32fs import is_fat32_boot
from host.safety import check_backup
from host.untether import install_loader_files


def _nano(**kw) -> Nano:
    defaults = dict(
        os_name="darwin",
        device="/dev/disk4",
        raw_device="/dev/rdisk4",
        partition="/dev/rdisk4s1",
        usb_serial="000A2700000000AA",
        size=TESTED_DISK_SIZE,
        fstype="vfat",
        mountpoints=[],
        model="iPod",
        bsd_name="disk4",
    )
    defaults.update(kw)
    return annotate_size(Nano(**defaults))


def _fat32_boot(bps: int = 4096) -> bytes:
    sector = bytearray(max(512, bps))
    sector[0x0B:0x0D] = (bps).to_bytes(2, "little")
    sector[0x52:0x5A] = b"FAT32   "
    sector[510] = 0x55
    sector[511] = 0xAA
    return bytes(sector)


class AlignedReadTests(unittest.TestCase):
    def test_unaligned_512_read_uses_4096_window(self):
        blob = _fat32_boot(4096) + b"\x00" * 4096
        fd, name = tempfile.mkstemp(prefix="rdisk-align-")
        os.write(fd, blob)
        os.close(fd)
        path = Path(name)
        self.addCleanup(path.unlink)
        data = read_aligned(path, 0, 512, align=4096)
        self.assertEqual(len(data), 512)
        self.assertTrue(is_fat32_boot(data))
        self.assertEqual(read_aligned(path, 100, 20, align=4096), blob[100:120])

    def test_char_dev_rejects_unaligned_but_aligned_works(self):
        blob = _fat32_boot(4096)

        class _AlignOnly:
            def __init__(self):
                self.pos = 0

            def seek(self, off, whence=0):
                if off % 4096:
                    raise OSError(22, "Invalid argument")
                self.pos = off
                return off

            def read(self, n=-1):
                if n % 4096:
                    raise OSError(22, "Invalid argument")
                out = blob[self.pos : self.pos + n]
                self.pos += len(out)
                return out

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch("host.fat.open", lambda *a, **k: _AlignOnly()):
            data = read_aligned(Path("/dev/rdisk4s1"), 0, 512, align=4096)
        self.assertTrue(is_fat32_boot(data))


class FatBpbRefuseTests(unittest.TestCase):
    def test_bpb_present_does_not_suggest_running_fat32(self):
        fd, name = tempfile.mkstemp(prefix="fat-bpb-")
        os.write(fd, _fat32_boot(4096))
        os.close(fd)
        path = Path(name)
        self.addCleanup(path.unlink)
        nano = _nano(partition=str(path), raw_device=str(path), size=4096)
        self.assertTrue(fat32_bpb_present(nano))
        with self.assertRaises(Refused) as ctx:
            refuse_unreadable_fat(nano)
        msg = str(ctx.exception)
        self.assertIn("FAT32 boot record", msg)
        self.assertIn("Do not run ./start fat32", msg)
        self.assertIn("wipe music", msg)
        self.assertNotIn("Run ./start fat32", msg)
        self.assertNotIn("then retry", msg)


class MountedAppsCopyTests(unittest.TestCase):
    def test_install_loader_files_copies_via_mount_not_mtools(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        os.environ["NANO7_STATE_DIR"] = td.name
        vol = Path(td.name) / "IPOD"
        music = vol / "iPod_Control" / "Music" / "F00"
        music.mkdir(parents=True)
        (music / "song.mp3").write_bytes(b"id3")
        backup = Path(td.name) / "nano.img"
        payload = b"B" * 4096
        backup.write_bytes(payload)
        nano = _nano(
            mountpoints=[str(vol)],
            size=len(payload),
            raw_device=str(backup),
            partition=str(backup),
        )
        mtools_calls = []
        unmount_kw = []
        mount_n = {"n": 0}

        def boom_run(cmd, *a, **k):
            mtools_calls.append(list(cmd))
            raise AssertionError(f"mtools/diskutil must not run: {cmd}")

        def fake_unmount(_nano, **kw):
            unmount_kw.append(kw)

        def fake_ensure(n, **_kw):
            mount_n["n"] += 1
            n.mountpoints = [str(vol)]
            return vol

        with mock.patch("host.untether.verify_release_artifacts"), mock.patch(
            "host.untether.unmount", side_effect=fake_unmount
        ), mock.patch(
            "host.untether.ensure_fat_mount", side_effect=fake_ensure
        ), mock.patch(
            "host.untether.close_finder_ipod_windows", return_value=0
        ), mock.patch("host.fat.run", side_effect=boom_run), mock.patch(
            "host.untether.need_mtools", side_effect=AssertionError("no mtools")
        ):
            rec = install_loader_files(
                nano,
                backup=backup,
                backup_sha256=sha256_file(backup),
                allow_size=len(payload),
                confirm_untested=True,
                verify_bytes=False,
            )
        self.assertEqual(rec["verified"], "yes")
        self.assertEqual(rec["resident_sha256"], RESIDENT_SHA256)
        self.assertEqual(rec.get("durable"), "yes")
        self.assertTrue((vol / "Apps" / "Startup" / "resident.hbapp").is_file())
        self.assertTrue((vol / "Apps" / "AllApps.pack").is_file())
        self.assertTrue((vol / "iPod_Control" / "Music" / "F00" / "song.mp3").is_file())
        self.assertEqual(mtools_calls, [])
        self.assertGreaterEqual(mount_n["n"], 2)
        self.assertTrue(unmount_kw)
        self.assertTrue(unmount_kw[0].get("force"))

    def test_remount_rehash_refuses_when_apps_missing_after_unmount(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        os.environ["NANO7_STATE_DIR"] = td.name
        vol = Path(td.name) / "IPOD"
        vol.mkdir()
        backup = Path(td.name) / "nano.img"
        payload = b"B" * 4096
        backup.write_bytes(payload)
        nano = _nano(
            mountpoints=[str(vol)],
            size=len(payload),
            raw_device=str(backup),
            partition=str(backup),
        )
        empty = Path(td.name) / "empty"
        empty.mkdir()
        rounds = {"n": 0}

        def fake_ensure(n, **_kw):
            rounds["n"] += 1
            if rounds["n"] == 1:
                n.mountpoints = [str(vol)]
                return vol
            n.mountpoints = [str(empty)]
            return empty

        with mock.patch("host.untether.verify_release_artifacts"), mock.patch(
            "host.untether.unmount"
        ), mock.patch(
            "host.untether.ensure_fat_mount", side_effect=fake_ensure
        ), mock.patch(
            "host.untether.close_finder_ipod_windows", return_value=0
        ), mock.patch(
            "host.untether.need_mtools", side_effect=AssertionError("no mtools")
        ):
            with self.assertRaises(Refused) as ctx:
                install_loader_files(
                    nano,
                    backup=backup,
                    backup_sha256=sha256_file(backup),
                    allow_size=len(payload),
                    confirm_untested=True,
                    verify_bytes=False,
                )
        msg = str(ctx.exception)
        self.assertIn("Apps is missing", msg)
        self.assertIn("Do not DFU", msg)
        self.assertIn("Do not flash", msg)

    def test_copy_verified_fsyncs_dest_file(self):
        from host.fat import MountFat

        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        vol = Path(td.name) / "IPOD"
        vol.mkdir()
        src = Path(td.name) / "src.bin"
        src.write_bytes(b"hello-fat")
        synced = []

        real_fsync = os.fsync

        def spy_fsync(fd):
            synced.append(fd)
            return real_fsync(fd)

        with mock.patch("os.fsync", side_effect=spy_fsync):
            MountFat(vol).copy_verified(src, "/Apps/Startup/ab.hbapp", replace=True)
        self.assertTrue((vol / "Apps" / "Startup" / "ab.hbapp").is_file())
        self.assertGreaterEqual(len(synced), 1)

    def test_fat_source_has_fullfsync_and_remount_rehash(self):
        text = (Path(__file__).resolve().parent.parent / "host" / "fat.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("F_FULLFSYNC", text)
        self.assertIn("verify_expected_hashes", text)
        self.assertIn("fsync_path", text)
        self.assertNotIn("\u2014", text)
        self.assertTrue(callable(fsync_path))


class EnsureFatMountTests(unittest.TestCase):
    def test_mounted_root_skips_stale_volumes_dir(self):
        nano = _nano(mountpoints=["/Volumes/IPOD"])
        real_is_dir = Path.is_dir

        def is_dir(self):
            if str(self) == "/Volumes/IPOD":
                return True
            return real_is_dir(self)

        with mock.patch.object(Path, "is_dir", is_dir), mock.patch(
            "os.path.ismount", return_value=False
        ):
            self.assertIsNone(mounted_root(nano))
        with mock.patch.object(Path, "is_dir", is_dir), mock.patch(
            "os.path.ismount", return_value=True
        ):
            self.assertEqual(mounted_root(nano), Path("/Volumes/IPOD"))

    def test_ensure_fat_mount_tries_disk4s1_first_then_retries(self):
        vol = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(vol, ignore_errors=True))
        nano = _nano(mountpoints=[])
        calls = []
        rounds = {"n": 0}

        def fake_refresh(n):
            rounds["n"] += 1
            if rounds["n"] >= 2:
                n.mountpoints = [str(vol)]

        def fake_run(cmd, **_kw):
            calls.append(list(cmd))
            return mock.Mock(returncode=1, stdout="", stderr="")

        with mock.patch("host.fat.is_macos", return_value=True), mock.patch(
            "host.fat.run", side_effect=fake_run
        ), mock.patch("host.fat.time.sleep"), mock.patch(
            "host.fat._refresh_darwin_mounts", side_effect=fake_refresh
        ):
            root = ensure_fat_mount(nano, retries=4, delay=0.01)
        self.assertEqual(root, vol)
        self.assertEqual(calls[0], ["diskutil", "mount", "/dev/disk4s1"])
        self.assertTrue(any(c == ["diskutil", "mount", "disk4s1"] for c in calls))
        self.assertTrue(any("mountDisk" in c for c in calls))
        self.assertGreaterEqual(rounds["n"], 2)

    def test_unmount_force_passes_diskutil_force(self):
        nano = _nano(mountpoints=["/Volumes/IPOD"])
        calls = []

        def fake_run(cmd, **_kw):
            calls.append(list(cmd))
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("host.fat.is_macos", return_value=True), mock.patch(
            "host.fat.run", side_effect=fake_run
        ):
            unmount(nano, force=True)
        self.assertEqual(calls[0], ["diskutil", "unmountDisk", "force", "disk4"])

    def test_close_finder_ipod_windows_uses_osascript_as_sudo_user(self):
        calls = []

        def fake_run(cmd, **_kw):
            calls.append(list(cmd))
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("host.fat.is_macos", return_value=True), mock.patch(
            "host.fat.is_root", return_value=True
        ), mock.patch.dict(os.environ, {"SUDO_USER": "you"}, clear=False), mock.patch(
            "host.fat.run", side_effect=fake_run
        ), mock.patch("host.fat.time.sleep"):
            n = close_finder_ipod_windows(retries=2, delay=0.0)
        self.assertEqual(n, 2)
        self.assertTrue(calls)
        self.assertEqual(calls[0][0:4], ["sudo", "-u", "you", "-H"])
        self.assertIn("osascript", calls[0])
        self.assertTrue(any("IPOD" in str(c) for c in calls))
        joined = " ".join(" ".join(c) for c in calls)
        self.assertNotIn("OpenWindowForNewRemovableDisk", joined)
        self.assertNotIn("defaults write", joined)


class BackupReuseTests(unittest.TestCase):
    def test_recorded_backup_skips_rehash(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        backup = Path(td.name) / "nano-000A2700000000AA-20261029T103705Z.img"
        backup.write_bytes(b"x" * 2048)
        digest = "b" * 64
        nano = _nano(size=2048)
        with mock.patch("host.safety.sha256_file", side_effect=AssertionError("no 15GB re-read")):
            out = check_backup(backup, digest, 2048, verify_bytes=False)
        self.assertEqual(out, digest)

    def test_select_untether_backup_uses_flags(self):
        from host.backup import select_untether_backup

        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        backup = Path(td.name) / "nano.img"
        backup.write_bytes(b"y" * 1024)
        digest = "c" * 64
        nano = _nano(size=1024)
        with mock.patch("host.backup.backup_local", side_effect=AssertionError("no new backup")):
            info = select_untether_backup(nano, backup, digest)
        self.assertTrue(info["reused"])
        self.assertEqual(info["path"], str(backup))
        self.assertEqual(info["sha256"], digest)

    def test_select_requires_both_flags(self):
        from host.backup import select_untether_backup

        nano = _nano(size=1024)
        with self.assertRaises(Refused) as ctx:
            select_untether_backup(nano, Path("/tmp/nano.img"), None)
        self.assertIn("--backup-sha256", str(ctx.exception))
        with self.assertRaises(Refused):
            select_untether_backup(nano, None, "a" * 64)

    def test_ipodrepartition_size_constant(self):
        self.assertEqual(IPODREPARTITION_DISK_SIZE, 15806623744)


if __name__ == "__main__":
    unittest.main()
