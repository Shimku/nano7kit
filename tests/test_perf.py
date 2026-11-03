#!/usr/bin/env python3
"""Resident heap readout, Blocks A/B pack hide, yield flag."""
import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host.apps import (
    assert_yield_flag,
    format_yield_plan,
    pack_app_names,
    pack_drop_plan,
    plan_yield,
    tested_app_names,
    tested_pack_bytes,
    yield_apps,
    YIELD_FLAG,
    YIELD_REFUSE,
)
from host.compat import Refused
from host.perf import (
    ADD_BLOCKS,
    BLOCKS_SAVE,
    FB_BYTES,
    PAINT_ADD,
    PERF_CMD,
    PROBE_FREE_MB,
    REMOVE_BLOCKS,
    YIELD_BLOCKS,
    ab_steps,
    as_text,
    blob_sizes,
    parse_probe_txt,
    print_status,
)
from host.ports import SLOT_A_GL_STAGING

ROOT = Path(__file__).resolve().parent.parent
START = ROOT / "start"


class PerfHostTests(unittest.TestCase):
    def test_blob_sizes_and_as_text(self):
        from tests.built import need_built

        need_built(self, "Paint", "Tetris", "Snake")
        sizes = blob_sizes()
        self.assertGreater(sizes["Blocks"], 0)
        self.assertGreater(sizes["Paint"], 0)
        self.assertGreater(sizes["Tetris"], sizes["Paint"])
        self.assertGreater(sizes["Snake"], sizes["Paint"])
        text = as_text(live={"present": False, "note": "unplugged"})
        for needle in (
            PERF_CMD,
            REMOVE_BLOCKS,
            ADD_BLOCKS,
            YIELD_BLOCKS,
            PAINT_ADD,
            BLOCKS_SAVE,
            "8 ms",
            "one Home app at a time",
            str(SLOT_A_GL_STAGING),
            str(FB_BYTES),
            str(PROBE_FREE_MB),
            "world.bin is not deleted",
            "no NAND",
        ):
            self.assertIn(needle, text)
        self.assertNotIn("\u2014", text)
        self.assertEqual(REMOVE_BLOCKS, "./start apps remove Blocks --keep-files")
        self.assertEqual(
            YIELD_BLOCKS, "./start apps yield Blocks --i-accept-untested-pack"
        )

    def test_parse_probe_txt(self):
        parsed = parse_probe_txt(
            "HEAP FREE 8.7MB\nLARGEST 8.0MB\nALLOC OK\nCPU ~324 MHz\nMEMCPY 33MB/s\n"
        )
        self.assertEqual(parsed["free"], "8.7MB")
        self.assertEqual(parsed["largest"], "8.0MB")
        self.assertIn("ALLOC", parsed["alloc"])
        self.assertIn("MHz", parsed["cpu"])
        self.assertIn("MB/s", parsed["copy"])

    def test_ab_steps_order(self):
        steps = "\n".join(ab_steps())
        self.assertLess(steps.find(PERF_CMD), steps.find(REMOVE_BLOCKS))
        self.assertLess(steps.find(REMOVE_BLOCKS), steps.find(ADD_BLOCKS))
        self.assertLess(steps.find(ADD_BLOCKS), steps.find(YIELD_BLOCKS))
        self.assertIn("restore-tested", steps)

    def test_print_status_cli_shape(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = print_status()
        self.assertEqual(rc, 0)
        self.assertIn("nano7kit perf", buf.getvalue())


class YieldPlanTests(unittest.TestCase):
    def test_pack_drop_blocks_keeps_files_flag(self):
        data = tested_pack_bytes()
        plan = pack_drop_plan(data, ["Blocks"], keep_files=True)
        self.assertEqual(plan["drop"], ["Blocks"])
        self.assertEqual(plan["keep"], ["Game Boy", "Probe"])
        self.assertEqual(pack_app_names(plan["pack"]), ["Game Boy", "Probe"])
        self.assertTrue(plan["keep_files"])
        self.assertTrue(plan["drops_tested"])
        self.assertFalse(plan["tested_pack"])
        self.assertEqual(plan["missing"], [])

    def test_plan_yield_and_format(self):
        plan = plan_yield(["Blocks"])
        text = format_yield_plan(plan)
        for needle in (
            "apps yield",
            YIELD_FLAG,
            YIELD_BLOCKS,
            ADD_BLOCKS,
            "restore-tested",
            "Game Boy",
            "Probe",
            "stay on disk",
            "world.bin",
            "not a runtime suspend",
        ):
            self.assertIn(needle, text)
        self.assertNotIn("\u2014", text)
        self.assertNotIn("Blocks", plan["keep"])

    def test_assert_yield_flag_refuses_tested_without_accept(self):
        with self.assertRaises(Refused) as ctx:
            assert_yield_flag(["Blocks"], False)
        self.assertIn(YIELD_FLAG, str(ctx.exception))
        self.assertIn("--keep-files", str(ctx.exception))
        self.assertEqual(assert_yield_flag(["Blocks"], True), ["Blocks"])
        self.assertIn(YIELD_REFUSE, str(ctx.exception))

    def test_yield_apps_refuses_without_flag(self):
        nano = mock.Mock(usb_serial="X", size=1, raw_device="/dev/null")
        with self.assertRaises(Refused) as ctx:
            yield_apps(
                nano,
                ["Blocks"],
                backup=Path("/tmp/x"),
                backup_sha256="a" * 64,
                skip_wait=True,
            )
        self.assertIn(YIELD_FLAG, str(ctx.exception))


class KeepFilesMountTests(unittest.TestCase):
    def test_remove_keep_files_then_yield_then_restore(self):
        import os
        import shutil

        from host.apps import add_apps, remove_apps, restore_tested_pack
        from host.const import TESTED_DISK_SIZE
        from host.device import Nano, annotate_size
        from host.untether import check_core_loader_hashes

        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        os.environ["NANO7_STATE_DIR"] = td.name
        vol = Path(td.name) / "vol"
        shutil.copytree(ROOT / "untether" / "artifacts" / "device", vol / "Apps")
        blocks = vol / "Apps" / "Executables" / "Blocks.hbapp"
        self.assertTrue(blocks.is_file())
        nano = annotate_size(
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

        def fake_mount(_nano, **_kw):
            _nano.mountpoints = [str(vol)]
            return vol

        patches = (
            mock.patch("host.fat.ensure_fat_mount", side_effect=fake_mount),
            mock.patch("host.fat.assert_not_hfs"),
            mock.patch("host.untether.unmount"),
            mock.patch("host.untether.ensure_fat_mount", side_effect=fake_mount),
            mock.patch("host.untether.close_finder_ipod_windows", return_value=0),
            mock.patch("host.apps.check_usb_serial"),
            mock.patch(
                "host.apps.check_disk_size", return_value={"size": TESTED_DISK_SIZE}
            ),
            mock.patch("host.apps.check_backup", return_value="a" * 64),
            mock.patch("host.apps.layout_for_disk_size", return_value=None),
            mock.patch("host.apps.assert_not_hfs"),
        )
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        hidden = remove_apps(
            nano,
            ["Blocks"],
            backup=Path(td.name) / "missing.img",
            backup_sha256="a" * 64,
            skip_wait=True,
            skip_backup_file=True,
            keep_files=True,
        )
        self.assertIn("Blocks", hidden["removed"])
        self.assertEqual(hidden["kept"], ["Game Boy", "Probe"])
        self.assertTrue(hidden["keep_files"])
        self.assertTrue(blocks.is_file(), "A/B must leave Blocks.hbapp")
        pack = vol / "Apps" / "AllApps.pack"
        self.assertEqual(pack_app_names(pack.read_bytes()), ["Game Boy", "Probe"])
        self.assertTrue(
            (vol / "Apps" / "Startup" / "resident.hbapp").is_file()
        )

        back = add_apps(
            nano,
            ["Blocks"],
            backup=Path(td.name) / "missing.img",
            backup_sha256="a" * 64,
            skip_wait=True,
            skip_backup_file=True,
        )
        self.assertIn("Blocks", back["pack_names"])
        self.assertEqual(
            set(pack_app_names(pack.read_bytes())), set(tested_app_names())
        )

        yielded = yield_apps(
            nano,
            ["Blocks"],
            backup=Path(td.name) / "missing.img",
            backup_sha256="a" * 64,
            skip_wait=True,
            skip_backup_file=True,
            accept_untested_pack=True,
        )
        self.assertTrue(yielded["yield_mode"])
        self.assertTrue(yielded["keep_files"])
        self.assertEqual(yielded["kept"], ["Game Boy", "Probe"])
        self.assertTrue(blocks.is_file())
        self.assertEqual(pack_app_names(pack.read_bytes()), ["Game Boy", "Probe"])

        restored = restore_tested_pack(
            nano,
            backup=Path(td.name) / "missing.img",
            backup_sha256="a" * 64,
            skip_wait=True,
            skip_backup_file=True,
        )
        self.assertTrue(restored["tested_pack"])
        self.assertEqual(restored["kept"], tested_app_names())
        self.assertEqual(pack_app_names(pack.read_bytes()), tested_app_names())
        hashes = check_core_loader_hashes(nano)
        self.assertTrue(hashes.get("core_ok") or hashes.get("ok"))


class PerfCliTests(unittest.TestCase):
    def test_start_perf(self):
        r = subprocess.run(
            [sys.executable, str(START), "perf"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        for needle in (
            "nano7kit perf",
            REMOVE_BLOCKS,
            "8.7",
            str(SLOT_A_GL_STAGING),
        ):
            self.assertIn(needle, r.stdout)
        self.assertNotIn("\u2014", r.stdout)

    def test_yield_plan_offline(self):
        r = subprocess.run(
            [sys.executable, str(START), "apps", "yield", "--plan"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        blob = r.stdout
        self.assertIn("Blocks", blob)
        self.assertIn(YIELD_FLAG, blob)
        self.assertIn("restore-tested", blob)
        self.assertNotIn("\u2014", blob)

    def test_yield_without_flag_refused_offline(self):
        r = subprocess.run(
            [sys.executable, str(START), "apps", "yield", "Blocks"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        blob = r.stdout + r.stderr
        self.assertIn(YIELD_FLAG, blob)
        self.assertIn("--keep-files", blob)

    def test_help_lists_perf_yield_keep_files(self):
        help_apps = subprocess.run(
            [sys.executable, str(START), "apps", "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(help_apps.returncode, 0, help_apps.stderr)
        self.assertIn("yield", help_apps.stdout)
        rm = subprocess.run(
            [sys.executable, str(START), "apps", "remove", "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(rm.returncode, 0, rm.stderr)
        self.assertIn("keep-files", rm.stdout)
        yd = subprocess.run(
            [sys.executable, str(START), "apps", "yield", "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(yd.returncode, 0, yd.stderr)
        self.assertIn("i-accept-untested-pack", yd.stdout)
        top = subprocess.run(
            [sys.executable, str(START), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(top.returncode, 0, top.stderr)
        self.assertIn("perf", top.stdout)


if __name__ == "__main__":
    unittest.main()
