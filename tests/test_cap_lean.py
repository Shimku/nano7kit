#!/usr/bin/env python3
"""Lean residents: nogrow + Home icon LRU 16 x 0xC800."""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host import cap
from host.cap import (
    LEAN_A_SHA256,
    LEAN_B_SHA256,
    LEAN_ICON_BUF,
    LEAN_SAVED_BYTES,
    LEAN_WORKING_SHA256,
    NOGROW_A_SHA256,
    NOGROW_B_SHA256,
    NOGROW_WORKING_SHA256,
    build_lean_artifacts,
    enable_cap,
)
from host.compat import Refused, sha256_file
from host.const import RESIDENT_SHA256, SLOT_A_SHA256, SLOT_B_SHA256, artifact

from tests import test_cap as _tc


def _image(data: bytes) -> bytes:
    return data[24 : 24 + struct.unpack_from("<I", data, 8)[0]]


class LeanPatchTests(unittest.TestCase):
    def test_pinned_and_four_bytes_from_nogrow(self):
        hashes = build_lean_artifacts()
        self.assertEqual(hashes[cap.LEAN_WORKING_REL], LEAN_WORKING_SHA256)
        self.assertEqual(hashes[cap.LEAN_A_REL], LEAN_A_SHA256)
        self.assertEqual(hashes[cap.LEAN_B_REL], LEAN_B_SHA256)
        for nog_rel, trailer, _edits, rel in cap.LEAN_SPECS:
            nog = artifact(nog_rel).read_bytes()
            lean = artifact(rel).read_bytes()
            self.assertEqual(len(nog), len(lean))
            # header and relocation table identical, image differs in 4 bytes
            self.assertEqual(nog[:24], lean[:24])
            img_n, img_l = _image(nog), _image(lean)
            end = 24 + len(img_n)
            self.assertEqual(nog[end : end + 4 * struct.unpack_from("<I", nog, 16)[0]],
                             lean[end : end + 4 * struct.unpack_from("<I", lean, 16)[0]])
            diff = [i for i in range(len(img_n)) if img_n[i] != img_l[i]]
            self.assertEqual(len(diff), 4, rel)
            # 20 -> 16 twice, 0xE000 -> 0xC800 twice
            self.assertEqual(sorted((img_n[i], img_l[i]) for i in diff),
                             [(0x14, 0x10), (0x14, 0x10), (0x60, 0x48), (0x60, 0x48)])
            # trailer (A, B) re-validates
            cap._split_hrl1(lean, trailer=trailer)
            self.assertNotIn(bytes.fromhex("4ff46040"), img_l)
            self.assertNotIn(bytes.fromhex("4ff46042"), img_l)

    def test_lean_keeps_the_nogrow_branch(self):
        build_lean_artifacts()
        data_b = artifact(cap.LEAN_B_REL).read_bytes()
        self.assertEqual(_image(data_b)[0x24B8:0x24BA], cap.B_N)

    def test_icon_fits_and_saving(self):
        self.assertGreaterEqual(LEAN_ICON_BUF, 50208 + 512)
        self.assertEqual(LEAN_SAVED_BYTES, 20 * 0xE000 - 16 * 0xC800)
        self.assertEqual(LEAN_SAVED_BYTES, 327_680)
        self.assertEqual(cap.LEAN_ICON_LRU_N, cap.NOGROW_PACK_LIMIT)
        for h in (LEAN_WORKING_SHA256, LEAN_A_SHA256, LEAN_B_SHA256):
            self.assertNotIn(h, cap.NOGROW_HASHES)
            self.assertNotIn(h, {RESIDENT_SHA256, SLOT_A_SHA256, SLOT_B_SHA256})

    def test_unknown_resident_refused(self):
        img = _image(artifact(cap.NOGROW_B_REL).read_bytes())
        with self.assertRaises(Refused):
            cap._apply_lean(img + img, cap._LEAN_SLOT_B)
        with self.assertRaises(Refused):
            cap._apply_lean(img, cap._LEAN_SLOT_A)  # wrong slot layout

    def test_pack_buffer_is_exact_size_in_pinned_b(self):
        """The device resident reads the pack into hb_fs_size() bytes, not 1 MiB."""
        img = _image(artifact("artifacts/device/Startup/resident-B.hbapp").read_bytes())
        # mov.w r0, #0x100000 appears once: the grow_cache call nogrow skips
        self.assertEqual(img.count(bytes.fromhex("4ff48010")), 1)
        # size check `cmp size-16, #0xffff0` literal next to the pack path ref
        self.assertEqual(struct.unpack_from("<I", img, 0x634)[0], 0xFFFF0)
        self.assertEqual(struct.unpack_from("<I", img, 0x630)[0],
                         img.find(b"/Apps/AllApps-B.pack"))


class LauncherGuardTests(unittest.TestCase):
    def test_cap_refuses_over_the_launcher(self):
        from host.cap import refuse_over_launcher
        from host.const import NA_B_SHA256, NA_B_OLD_SHA256S
        from host.ports import SLOT_B_FAT

        class Ops:
            def __init__(self, digest):
                self.digest = digest

            def exists(self, fat):
                return fat == SLOT_B_FAT and self.digest is not None

            def read_hash(self, fat):
                return self.digest

        for digest in [NA_B_SHA256, *NA_B_OLD_SHA256S]:
            with self.assertRaises(Refused) as ctx:
                refuse_over_launcher(Ops(digest))
            self.assertIn("Launcher", str(ctx.exception))
        refuse_over_launcher(Ops(SLOT_B_SHA256))
        refuse_over_launcher(Ops(None))

    def test_launcher_lifts_the_pack_cap_without_cap_lean(self):
        from host.cap import cap_lifted
        from host.const import NA_B_SHA256
        from host.ports import SLOT_B_FAT, SLOT_B_MARKER

        class Ops:
            def __init__(self, files):
                self.files = files

            def exists(self, fat):
                return fat in self.files

            def read_hash(self, fat):
                return self.files[fat]

        self.assertTrue(cap_lifted(Ops({SLOT_B_FAT: NA_B_SHA256, SLOT_B_MARKER: ""})))
        self.assertFalse(cap_lifted(Ops({SLOT_B_FAT: NA_B_SHA256})))          # slot B not active
        self.assertFalse(cap_lifted(Ops({SLOT_B_FAT: SLOT_B_SHA256, SLOT_B_MARKER: ""})))


class LeanSlotBTests(unittest.TestCase):
    A_NAMES = _tc.CapSlotBTests.A_NAMES
    setUp = _tc.CapSlotBTests.setUp
    startup = _tc.CapSlotBTests.startup
    pack = _tc.CapSlotBTests.pack

    def test_lean_needs_nogrow_first(self):
        with self.assertRaises(Refused) as ctx:
            enable_cap(self.nano, lean=True, **self.kw)
        self.assertIn("apps cap enable", str(ctx.exception))
        self.assertEqual(sha256_file(self.startup("resident-B.hbapp")), SLOT_B_SHA256)
        self.assertFalse(self.startup("resident-lean.active").exists())

    def test_lean_add_undo_and_restore(self):
        from host.apps import add_apps, restore_tested_pack
        from host.untether import check_core_loader_hashes

        enable_cap(self.nano, **self.kw)
        rec = enable_cap(self.nano, lean=True, **self.kw)
        self.assertTrue(rec["lean"])
        self.assertEqual(sha256_file(self.startup("resident.hbapp")), LEAN_WORKING_SHA256)
        self.assertEqual(sha256_file(self.startup("resident-A.hbapp")), LEAN_A_SHA256)
        self.assertEqual(sha256_file(self.startup("resident-B.hbapp")), LEAN_B_SHA256)
        self.assertTrue(self.startup("resident-lean.active").is_file())
        self.assertTrue(self.startup("resident-nogrow.active").is_file())
        # tested pins still in place for rollback
        self.assertEqual(sha256_file(self.startup("resident-B.tested.hbapp")), SLOT_B_SHA256)
        self.assertTrue(check_core_loader_hashes(self.nano)["core_ok"])
        # adding an app keeps the lean slot-B resident
        add_apps(self.nano, ["Notes"], **self.kw)
        self.assertEqual(sha256_file(self.startup("resident-B.hbapp")), LEAN_B_SHA256)
        self.assertIn("Notes", self.pack("/Apps/AllApps-B.pack"))
        # undo lean: plain nogrow, cap stays 16
        enable_cap(self.nano, **self.kw)
        self.assertFalse(self.startup("resident-lean.active").exists())
        self.assertEqual(sha256_file(self.startup("resident-B.hbapp")), NOGROW_B_SHA256)
        self.assertEqual(sha256_file(self.startup("resident.hbapp")), NOGROW_WORKING_SHA256)
        self.assertEqual(sha256_file(self.startup("resident-A.hbapp")), NOGROW_A_SHA256)
        self.assertTrue(check_core_loader_hashes(self.nano)["core_ok"])
        # lean again, then full rollback
        enable_cap(self.nano, lean=True, **self.kw)
        restored = restore_tested_pack(self.nano, **self.kw)
        self.assertTrue(restored["nogrow_rolled_back"])
        self.assertFalse(self.startup("resident-lean.active").exists())
        self.assertFalse(self.startup("resident-nogrow.active").exists())
        self.assertEqual(sha256_file(self.startup("resident.hbapp")), RESIDENT_SHA256)
        self.assertEqual(sha256_file(self.startup("resident-A.hbapp")), SLOT_A_SHA256)
        self.assertEqual(sha256_file(self.startup("resident-B.hbapp")), SLOT_B_SHA256)

    def test_disable_from_lean(self):
        from host.cap import disable_cap

        enable_cap(self.nano, **self.kw)
        enable_cap(self.nano, lean=True, **self.kw)
        rec = disable_cap(self.nano, **self.kw)
        self.assertTrue(rec["ok"])
        self.assertFalse(self.startup("resident-lean.active").exists())
        self.assertEqual(sha256_file(self.startup("resident-B.hbapp")), SLOT_B_SHA256)
        self.assertEqual(sha256_file(self.startup("resident.hbapp")), RESIDENT_SHA256)

    def test_tidy_up_remove_four_in_one_step(self):
        from host.apps import add_apps, remove_apps
        from host.untether import check_core_loader_hashes

        exe = self.vol / "Apps" / "Executables" / "Mario 64.hbapp"
        exe.write_bytes(b"\0" * 64)
        (self.vol / "Apps" / "Icons" / "Mario 64.bin").write_bytes(b"\0" * 64)
        enable_cap(self.nano, **self.kw)
        enable_cap(self.nano, lean=True, **self.kw)
        for name in ("Wallet", "Screenshot", "Books"):
            add_apps(self.nano, [name], **self.kw)
        remove_apps(self.nano, ["Wallet", "Screenshot", "Books", "Game Boy"],
                    keep_files=True, **self.kw)
        names = self.pack("/Apps/AllApps-B.pack")
        for gone in ("Wallet", "Screenshot", "Books", "Game Boy"):
            self.assertNotIn(gone, names)
        self.assertIn("Mario 64", names)
        # --keep-files leaves the executables on disk
        self.assertTrue((self.vol / "Apps" / "Executables" / "Game Boy.hbapp").exists())
        self.assertTrue(check_core_loader_hashes(self.nano)["core_ok"])
        self.assertEqual(sha256_file(self.startup("resident-B.hbapp")), LEAN_B_SHA256)

    def test_lean_refuses_unknown_resident(self):
        enable_cap(self.nano, **self.kw)
        self.startup("resident-B.hbapp").write_bytes(b"not a resident")
        with self.assertRaises(Refused):
            enable_cap(self.nano, lean=True, **self.kw)


if __name__ == "__main__":
    unittest.main()
