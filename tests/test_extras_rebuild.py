#!/usr/bin/env python3
"""Rebuilt NanoApps extras: no fixed live-OS addresses; apps refresh."""
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host.compat import Refused, sha256_file
from host.const import artifact
from host.fixed_addrs import LEGACY_PAGES, NotRelocatable, legacy_hits
from tests.built import need_built

ROOT = Path(__file__).resolve().parent.parent
EXTRA = artifact("artifacts/extra")
APPS = ROOT / "nanoapps" / "apps"


def hrl1(words, relocs=(), extra=b""):
    image = b"".join(struct.pack("<I", w) for w in words) + extra
    hdr = struct.pack("<6I", 0x314C5248, 0, len(image), len(image), len(relocs), 4)
    return hdr + image + b"".join(struct.pack("<I", r) for r in relocs)


def manifest():
    return json.loads((EXTRA / "MANIFEST.json").read_text())


class FixedAddressScannerTests(unittest.TestCase):
    def test_flags_each_legacy_page(self):
        for page in LEGACY_PAGES:
            hits = legacy_hits(hrl1([0, page + 0x40]))
            self.assertEqual(len(hits), 1, hex(page))
            self.assertEqual(hits[0]["value"], page + 0x40)

    def test_known_old_sdk_addresses(self):
        for addr in (0x09118000, 0x0911A000, 0x0911A200, 0x09120000, 0x09135F40,
                     0x09135FF0, 0x09135FEC, 0x09135B00, 0x092E0000, 0x09400000):
            self.assertTrue(legacy_hits(hrl1([addr])), hex(addr))

    def test_relocated_and_unaligned_words_are_not_hits(self):
        self.assertEqual(legacy_hits(hrl1([0x09120000], relocs=[0])), [])
        self.assertEqual(legacy_hits(hrl1([0x0940011B])), [])
        self.assertEqual(legacy_hits(hrl1([0x09300000, 0x08A9CDC8, 0x3C700484])), [])

    def test_movt_of_a_legacy_page(self):
        # movt r0, #0x0912  ->  f6c0 1012 (Thumb-2 T1)
        movt = struct.pack("<HH", 0xF6C0, 0x1012)
        hits = legacy_hits(hrl1([], extra=movt))
        self.assertEqual([h["kind"] for h in hits], ["movt"])
        self.assertEqual(hits[0]["value"], 0x09120000)

    def test_not_hrl1(self):
        with self.assertRaises(NotRelocatable):
            legacy_hits(b"\x7fELF" + b"\0" * 40)


class StagedExtrasTests(unittest.TestCase):
    def test_manifest_lists_the_rebuilt_set(self):
        names = {a["name"] for a in manifest()["apps"]}
        for n in ("Paint", "Chess", "Books", "Calculator", "Notes", "Wallet",
                  "Screenshot", "Lap", "JS Hello", "Tetris", "Snake"):
            self.assertIn(n, names)
        for n in ("Blocks", "Game Boy", "Probe", "Mario 64", "Mario Kart",
                  "Temple Run", "Flappy Bird", "Doodle Jump", "Fruit Ninja", "Angry Birds"):
            self.assertNotIn(n, names)      # Fruit Ninja / Angry Birds are built from the user's ipa
        self.assertIn("hb_shared", manifest()["sdk"])

    def test_manifest_hashes_match_files(self):
        from host.app_catalog import SHIPPED_EXTRAS

        shipped = [a for a in manifest()["apps"] if a["name"] in SHIPPED_EXTRAS]
        self.assertTrue(shipped)
        for a in shipped:
            exe = EXTRA / "Executables" / f"{a['name']}.hbapp"
            self.assertEqual(sha256_file(exe), a["exe_sha256"], a["name"])
            self.assertEqual(exe.stat().st_size, a["size"], a["name"])

    def test_no_staged_extra_references_old_fixed_addresses(self):
        for a in manifest()["apps"]:
            exe = EXTRA / "Executables" / f"{a['name']}.hbapp"
            if not exe.is_file():
                continue  # built on demand; stage_named runs the same check
            self.assertEqual(legacy_hits(exe), [], a["name"])

    def test_hardware_tested_pins_are_clean_too(self):
        for name in ("Blocks", "Game Boy", "Probe"):
            exe = artifact(f"artifacts/device/Executables/{name}.hbapp")
            self.assertEqual(legacy_hits(exe), [], name)

    def test_stage_refuses_a_legacy_build(self):
        from host.app_stage import assert_no_legacy_addresses

        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "old.hbapp"
            bad.write_bytes(hrl1([0x09135F40]))
            with self.assertRaises(Refused) as ctx:
                assert_no_legacy_addresses(bad, "Paint")
            self.assertIn("0x09135f40", str(ctx.exception))
            good = Path(td) / "new.hbapp"
            good.write_bytes(hrl1([0x1234]))
            assert_no_legacy_addresses(good, "Paint")

    def test_sdk_built_extra_classification(self):
        from host.app_catalog import by_name
        from host.apps import _sdk_built_extra

        need_built(self, "Paint", "Books", "Tetris")

        rows = by_name()
        for n in ("Paint", "Books", "Lap", "JS Hello", "Tetris"):
            self.assertTrue(_sdk_built_extra(rows[n]), n)
        for n in ("Blocks", "Game Boy", "Probe", "Mario 64", "Mario Kart", "Temple Run", "Fruit Ninja", "Angry Birds"):
            self.assertFalse(_sdk_built_extra(rows[n]), n)


ADDR_RE = re.compile(r"0x09[0-9A-Fa-f]{6}")


def strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


class SourceTests(unittest.TestCase):
    def test_app_sources_have_no_fixed_dram_addresses(self):
        bad = []
        for c in sorted(APPS.glob("*/*.c")):
            if c.parent.name in {"sm64", "mk64"}:
                continue
            code = strip_comments(c.read_text(encoding="utf-8", errors="replace"))
            if ADDR_RE.search(code) or "HB_TOUCH_MB_ADDR" in code:
                bad.append(c.relative_to(ROOT).as_posix())
        self.assertEqual(bad, [])

    def test_screenshot_uses_shared_block_with_null_guard(self):
        src = (APPS / "screenshot" / "screenshot.c").read_text()
        self.assertIn('#include "hb_shared.h"', src)
        self.assertIn("hb_shared ? hb_shared->screenshot_mode", src)
        self.assertIn("if (hb_shared) hb_shared->screenshot_mode", src)
        self.assertNotIn("SCREENSHOT_FLAG_ADDR", strip_comments(src))

    def test_books_text_is_the_aux_block(self):
        src = (APPS / "books" / "books.c").read_text()
        self.assertIn("HB_APP_AUX_BLOCK = s_book_blob", src)
        self.assertIn("hb_os_free(HB_APP_AUX_BLOCK)", src)

    def test_canvas_games_use_their_own_arena(self):
        for app in ("breakout", "chess", "connect4", "crosswords", "elements", "freecell",
                    "mahjong", "match3", "minesweeper", "rhythm", "solitaire", "sudoku", "tower"):
            src = (APPS / app / f"{app}.c").read_text()
            self.assertRegex(src, r"static uint8_t s_canvas_buf\[[^\]]+\] __attribute__\(\(aligned\(4\)\)\);", app)

    def test_chess_keeps_its_clockwise_directions(self):
        src = (APPS / "chess" / "chess.c").read_text()
        self.assertIn("{{-1,-1},{-1,0},{-1,1},{0,1},{1,1},{1,0},{1,-1},{0,-1}}", src)


def make_nano(vol):
    from host.const import TESTED_DISK_SIZE
    from host.device import Nano, annotate_size

    return annotate_size(
        Nano(
            os_name="linux",
            device="/dev/loop9",
            raw_device="/dev/loop9",
            partition="/dev/loop9p1",
            usb_serial="TESTSERIAL",
            size=TESTED_DISK_SIZE,
            fstype="vfat",
            mountpoints=[str(vol)],
            model="iPod",
        )
    )


class DiskModeTests(unittest.TestCase):
    def setUp(self):
        need_built(self, "Paint", "Notes")
        from host.app_icons import shared_screen_blob
        from host.app_catalog import by_name
        from host.apps import make_hbap_bundle, merge_bundles

        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.state = Path(self.td.name) / "state"
        os.environ["NANO7_STATE_DIR"] = str(self.state)
        self.addCleanup(os.environ.pop, "NANO7_STATE_DIR", None)
        self.vol = Path(self.td.name) / "vol"
        shutil.copytree(artifact("artifacts/device"), self.vol / "Apps")
        apps = self.vol / "Apps"
        rows = by_name()
        screen = shared_screen_blob()
        pack = merge_bundles(
            (apps / "AllApps.pack").read_bytes(),
            [make_hbap_bundle(rows["Paint"], screen), make_hbap_bundle(rows["Notes"], screen)],
        )
        (apps / "AllApps.pack").write_bytes(pack)
        self.old_paint = hrl1([0x09135F40, 0x09120000])
        (apps / "Executables" / "Paint.hbapp").write_bytes(self.old_paint)
        shutil.copyfile(EXTRA / "Executables" / "Notes.hbapp", apps / "Executables" / "Notes.hbapp")
        for n in ("Paint", "Notes"):
            shutil.copyfile(EXTRA / "Icons" / f"{n}.bin", apps / "Icons" / f"{n}.bin")
        (apps / "Data" / "Notes").mkdir(parents=True)
        (apps / "Data" / "Notes" / "notes.txt").write_bytes(b"keep me")
        self.nano = make_nano(self.vol)

    def patches(self):
        from host.const import TESTED_DISK_SIZE

        vol = self.vol

        def fake_mount(_nano, **_kw):
            _nano.mountpoints = [str(vol)]
            return vol

        return [
            mock.patch("host.fat.ensure_fat_mount", side_effect=fake_mount),
            mock.patch("host.fat.assert_not_hfs"),
            mock.patch("host.untether.unmount"),
            mock.patch("host.untether.ensure_fat_mount", side_effect=fake_mount),
            mock.patch("host.untether.close_finder_ipod_windows", return_value=0),
            mock.patch("host.apps.check_usb_serial"),
            mock.patch("host.apps.check_disk_size", return_value={"size": TESTED_DISK_SIZE}),
            mock.patch("host.apps.check_backup", return_value="a" * 64),
            mock.patch("host.apps.layout_for_disk_size", return_value=None),
            mock.patch("host.apps.assert_not_hfs"),
        ]

    def run_patched(self, fn, *a, **kw):
        ps = self.patches()
        for p in ps:
            p.start()
        try:
            return fn(*a, backup=Path(self.td.name) / "missing.img", backup_sha256="a" * 64,
                      skip_wait=True, skip_backup_file=True, **kw)
        finally:
            for p in reversed(ps):
                p.stop()

    def snapshot(self, rel):
        return sha256_file(self.vol / "Apps" / rel)

    def test_plan_is_read_only(self):
        from host.app_refresh import format_plan, plan_refresh
        from host.fat import MountFat

        before = self.snapshot("Executables/Paint.hbapp")
        plan = plan_refresh(MountFat(self.vol).read_hash)
        self.assertEqual([e["name"] for e in plan["update"]], ["Paint"])
        self.assertEqual([e["name"] for e in plan["current"]], ["Notes"])
        self.assertIn("Books", plan["absent"])
        self.assertNotIn("Blocks", plan["absent"] + [e["name"] for e in plan["current"]])
        text = format_plan(plan)
        self.assertIn("update    /Apps/Executables/Paint.hbapp", text)
        self.assertNotIn("\u2014", text)
        self.assertEqual(self.snapshot("Executables/Paint.hbapp"), before)

    def test_refresh_replaces_only_exes_then_undo(self):
        from host.app_refresh import refresh_apps, undo_refresh

        keep = {rel: self.snapshot(rel) for rel in (
            "AllApps.pack", "Icons/Paint.bin", "Icons/Notes.bin", "Data/Notes/notes.txt",
            "Startup/resident.hbapp", "Startup/resident-A.hbapp", "Startup/resident-B.hbapp",
            "Startup/ab.hbapp", "Executables/Blocks.hbapp", "Executables/Notes.hbapp",
        )}
        rec = self.run_patched(refresh_apps, self.nano)
        self.assertEqual(rec["refreshed"], ["Paint"])
        self.assertIn("Notes", rec["current"])
        self.assertEqual(
            self.snapshot("Executables/Paint.hbapp"),
            sha256_file(EXTRA / "Executables" / "Paint.hbapp"),
        )
        for rel, digest in keep.items():
            self.assertEqual(self.snapshot(rel), digest, rel)
        self.assertFalse((self.vol / "Apps" / "Executables" / "Books.hbapp").exists())
        saved = Path(rec["backup_dir"])
        self.assertTrue(str(saved).startswith(str(self.state)))
        self.assertEqual((saved / "Paint.hbapp").read_bytes(), self.old_paint)

        again = self.run_patched(refresh_apps, self.nano)
        self.assertEqual(again["refreshed"], [])
        self.assertIsNone(again["backup_dir"])

        undo = self.run_patched(undo_refresh, self.nano)
        self.assertEqual(undo["restored"], ["Paint"])
        self.assertEqual((self.vol / "Apps" / "Executables" / "Paint.hbapp").read_bytes(),
                         self.old_paint)
        for rel, digest in keep.items():
            self.assertEqual(self.snapshot(rel), digest, rel)

    def test_undo_without_a_refresh_is_refused(self):
        from host.app_refresh import undo_refresh

        with self.assertRaises(Refused):
            self.run_patched(undo_refresh, self.nano)

    def test_readd_replaces_an_installed_extra(self):
        from host.apps import add_apps

        rec = self.run_patched(add_apps, self.nano, ["Paint"])
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["replaced"], ["/Apps/Executables/Paint.hbapp"])
        self.assertEqual(
            self.snapshot("Executables/Paint.hbapp"),
            sha256_file(EXTRA / "Executables" / "Paint.hbapp"),
        )
        self.assertEqual(rec["pack_names"].count("Paint"), 1)
        self.assertEqual((self.vol / "Apps" / "Data" / "Notes" / "notes.txt").read_bytes(), b"keep me")


class CliTests(unittest.TestCase):
    def test_refresh_parser(self):
        from host.cli import build_parser

        args = build_parser().parse_args(["apps", "refresh", "--plan"])
        self.assertEqual(args.apps_cmd, "refresh")
        self.assertTrue(args.plan)
        args = build_parser().parse_args(["apps", "refresh", "--undo"])
        self.assertTrue(args.undo)


if __name__ == "__main__":
    unittest.main()
