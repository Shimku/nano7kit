#!/usr/bin/env python3
import os
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host.apps import (
    assert_pack_writable,
    pack_is_looping,
    plan_app_set,
    restore_tested_pack,
    tested_app_names,
)
from host.app_catalog import SUGGESTED_HOME, SKIP_FROM_SET
from host.cap import (
    B_N,
    NOGROW_A_SHA256,
    NOGROW_B_SHA256,
    NOGROW_PACK_LIMIT,
    NOGROW_WORKING_SHA256,
    STOCK_PACK_LIMIT,
    build_nogrow_artifacts,
    enable_cap,
    pack_limit,
)
from host.compat import Refused, sha256_file
from host.const import RESIDENT_SHA256, SLOT_A_SHA256, SLOT_B_SHA256, TESTED_DISK_SIZE, artifact
from host.device import Nano, annotate_size

ROOT = Path(__file__).resolve().parent.parent


class NogrowPatchTests(unittest.TestCase):
    def test_pinned_hashes_and_branch(self):
        hashes = build_nogrow_artifacts()
        self.assertEqual(
            hashes["artifacts/extra/Startup/resident-nogrow.hbapp"],
            NOGROW_WORKING_SHA256,
        )
        self.assertEqual(
            hashes["artifacts/extra/Startup/resident-A-nogrow.hbapp"],
            NOGROW_A_SHA256,
        )
        self.assertEqual(
            hashes["artifacts/extra/Startup/resident-B-nogrow.hbapp"],
            NOGROW_B_SHA256,
        )
        working = artifact("artifacts/extra/Startup/resident-nogrow.hbapp")
        slot_a = artifact("artifacts/extra/Startup/resident-A-nogrow.hbapp")
        slot_b = artifact("artifacts/extra/Startup/resident-B-nogrow.hbapp")
        self.assertEqual(working.stat().st_size, 16240)
        self.assertEqual(slot_a.stat().st_size, 16244)
        self.assertEqual(slot_b.stat().st_size, 17028)
        img_a = working.read_bytes()[24 : 24 + struct.unpack_from("<I", working.read_bytes(), 8)[0]]
        data_b = slot_b.read_bytes()
        image_b = struct.unpack_from("<I", data_b, 8)[0]
        img_b = data_b[24 : 24 + image_b]
        self.assertEqual(img_a[0x220C:0x220E], B_N)
        self.assertEqual(img_b[0x24B8:0x24BA], B_N)
        self.assertNotEqual(NOGROW_WORKING_SHA256, RESIDENT_SHA256)
        self.assertNotEqual(NOGROW_A_SHA256, SLOT_A_SHA256)
        self.assertNotEqual(NOGROW_B_SHA256, SLOT_B_SHA256)

    def test_pack_limit(self):
        self.assertEqual(pack_limit(nogrow=False), STOCK_PACK_LIMIT)
        self.assertEqual(pack_limit(nogrow=True), NOGROW_PACK_LIMIT)

    def test_nogrow_allows_suggested_home_not_looping_set(self):
        self.assertEqual(len(SUGGESTED_HOME), 9)
        self.assertIsNone(pack_is_looping(SUGGESTED_HOME, nogrow=True))
        assert_pack_writable(SUGGESTED_HOME, nogrow=True)
        with self.assertRaises(Refused) as ctx:
            assert_pack_writable(SUGGESTED_HOME, nogrow=False)
        self.assertIn("grow_cache", str(ctx.exception))
        self.assertIn("apps cap enable", str(ctx.exception))

    def test_nogrow_still_refuses_looping_eleven(self):
        from host.app_catalog import FIRST_SLICE

        names = tested_app_names() + list(FIRST_SLICE)
        self.assertEqual(len(names), 11)
        with self.assertRaises(Refused) as ctx:
            assert_pack_writable(names, nogrow=True)
        self.assertIn("reboot", str(ctx.exception))
        self.assertIn("nogrow does not make this set writable", str(ctx.exception))


class AppSetPlanTests(unittest.TestCase):
    def test_default_plan_inserts_cap_before_ninth(self):
        plan = plan_app_set()
        self.assertEqual(plan["target"], list(SUGGESTED_HOME))
        self.assertTrue(plan["need_cap"])
        actions = [s["action"] for s in plan["steps"]]
        self.assertIn("cap", actions)
        self.assertEqual(actions[-1], "add")
        self.assertEqual(plan["steps"][-1]["name"], "Screenshot")
        cap_at = actions.index("cap")
        self.assertEqual(cap_at, 5)
        added = [s["name"] for s in plan["steps"] if s["action"] == "add"]
        self.assertEqual(
            added,
            ["Chess", "Books", "Calculator", "Notes", "Wallet", "Screenshot"],
        )

    def test_plan_with_mario_caps_after_notes(self):
        current = tested_app_names() + ["Mario 64"]
        plan = plan_app_set(on_disk=current)
        actions = [(s["action"], s.get("name")) for s in plan["steps"]]
        self.assertEqual(
            actions,
            [
                ("add", "Chess"),
                ("add", "Books"),
                ("add", "Calculator"),
                ("add", "Notes"),
                ("cap", None),
                ("add", "Wallet"),
                ("add", "Screenshot"),
            ],
        )

    def test_skips_graphics_and_sensors(self):
        for name in ("Sensors", "OpenGL", "Widgets", "Benchmark"):
            self.assertIn(name, SKIP_FROM_SET)
            with self.assertRaises(Refused) as ctx:
                plan_app_set([name])
            self.assertIn("skips", str(ctx.exception))


class CapEnableMountTests(unittest.TestCase):
    def _nano(self, vol: Path) -> Nano:
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

    def test_enable_then_restore_rolls_back(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        os.environ["NANO7_STATE_DIR"] = td.name
        vol = Path(td.name) / "vol"
        shutil.copytree(ROOT / "untether" / "artifacts" / "device", vol / "Apps")
        nano = self._nano(vol)

        def fake_mount(_nano, **_kw):
            _nano.mountpoints = [str(vol)]
            return vol

        patches = [
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
            mock.patch("host.cap.check_usb_serial"),
            mock.patch("host.cap.check_disk_size", return_value={"size": TESTED_DISK_SIZE}),
            mock.patch("host.cap.check_backup", return_value="a" * 64),
            mock.patch("host.safety.check_usb_serial"),
            mock.patch("host.safety.check_disk_size", return_value={"size": TESTED_DISK_SIZE}),
            mock.patch("host.safety.check_backup", return_value="a" * 64),
            mock.patch("host.safety.layout_for_disk_size", return_value=None),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[
            6
        ], patches[7], patches[8], patches[9], mock.patch(
            "host.fat.mounted_root", return_value=vol
        ):
            rec = enable_cap(
                nano,
                backup=Path(td.name) / "missing.img",
                backup_sha256="a" * 64,
                skip_wait=True,
                skip_backup_file=True,
            )
            self.assertTrue(rec["ok"])
            self.assertTrue((vol / "Apps" / "Startup" / "resident-nogrow.active").is_file())
            self.assertEqual(
                sha256_file(vol / "Apps" / "Startup" / "resident.hbapp"),
                NOGROW_WORKING_SHA256,
            )
            self.assertEqual(
                sha256_file(vol / "Apps" / "Startup" / "resident-A.hbapp"),
                NOGROW_A_SHA256,
            )
            restored = restore_tested_pack(
                nano,
                backup=Path(td.name) / "missing.img",
                backup_sha256="a" * 64,
                skip_wait=True,
                skip_backup_file=True,
            )
        self.assertTrue(restored["nogrow_rolled_back"])
        self.assertFalse((vol / "Apps" / "Startup" / "resident-nogrow.active").exists())
        self.assertEqual(
            sha256_file(vol / "Apps" / "Startup" / "resident.hbapp"),
            RESIDENT_SHA256,
        )
        self.assertEqual(
            sha256_file(vol / "Apps" / "Startup" / "resident-A.hbapp"),
            SLOT_A_SHA256,
        )


class CapSlotBTests(unittest.TestCase):
    """Tested MKN52 state: slot B active, 8-app AllApps-B.pack with Mario."""

    A_NAMES = ["Game Boy", "Probe", "Paint", "Tetris", "Snake", "Blocks", "Calculator"]

    def setUp(self):
        from host.app_catalog import by_name
        from host.app_icons import shared_screen_blob
        from host.apps import build_pack, make_hbap_bundle
        from tests.built import need_built

        need_built(self, "Paint", "Tetris", "Snake", "Calculator", "Notes", "Chess",
                   "Wallet", "Screenshot", "Books")

        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        old_state = os.environ.get("NANO7_STATE_DIR")
        os.environ["NANO7_STATE_DIR"] = td.name
        self.addCleanup(
            lambda: os.environ.__setitem__("NANO7_STATE_DIR", old_state)
            if old_state is not None
            else os.environ.pop("NANO7_STATE_DIR", None)
        )
        self.td = Path(td.name)
        vol = self.td / "vol"
        self.vol = vol
        shutil.copytree(ROOT / "untether" / "artifacts" / "device", vol / "Apps")
        extra = ROOT / "untether" / "artifacts" / "extra" / "Executables"
        for name in self.A_NAMES:
            src = extra / f"{name}.hbapp"
            if src.is_file():
                shutil.copy(src, vol / "Apps" / "Executables" / src.name)
        cat = by_name()
        screen = shared_screen_blob()
        a_pack = build_pack([make_hbap_bundle(cat[n], screen) for n in self.A_NAMES])
        b_pack = build_pack(
            [make_hbap_bundle(cat[n], screen) for n in self.A_NAMES + ["Mario 64"]]
        )
        (vol / "Apps" / "AllApps.pack").write_bytes(a_pack)
        (vol / "Apps" / "AllApps-B.pack").write_bytes(b_pack)
        (vol / "Apps" / "Startup" / "resident-B.active").write_bytes(b"")
        self.nano = annotate_size(
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

        patches = [
            mock.patch("host.fat.ensure_fat_mount", side_effect=fake_mount),
            mock.patch("host.fat.assert_not_hfs"),
            mock.patch("host.fat.mounted_root", return_value=vol),
            mock.patch("host.untether.unmount"),
            mock.patch("host.untether.ensure_fat_mount", side_effect=fake_mount),
            mock.patch("host.untether.close_finder_ipod_windows", return_value=0),
            mock.patch("host.apps.assert_not_hfs"),
        ]
        for mod in ("host.apps", "host.safety"):
            patches += [
                mock.patch(mod + ".check_usb_serial"),
                mock.patch(mod + ".check_disk_size", return_value={"size": TESTED_DISK_SIZE}),
                mock.patch(mod + ".check_backup", return_value="a" * 64),
                mock.patch(mod + ".layout_for_disk_size", return_value=None),
            ]
        for p_ in patches:
            p_.start()
            self.addCleanup(p_.stop)
        self.kw = dict(
            backup=self.td / "missing.img",
            backup_sha256="a" * 64,
            skip_wait=True,
            skip_backup_file=True,
        )

    def startup(self, name: str) -> Path:
        return self.vol / "Apps" / "Startup" / name

    def pack(self, fat: str):
        from host.apps import pack_app_names

        return pack_app_names((self.vol / fat.lstrip("/")).read_bytes())

    def test_enable_patches_slot_b_and_keeps_packs(self):
        from host.apps import add_apps

        with self.assertRaises(Refused) as ctx:
            add_apps(self.nano, ["Notes"], **self.kw)
        self.assertIn("apps cap enable", str(ctx.exception))
        rec = enable_cap(self.nano, **self.kw)
        self.assertTrue(rec["slot_b"])
        self.assertEqual(sha256_file(self.startup("resident-B.hbapp")), NOGROW_B_SHA256)
        self.assertEqual(sha256_file(self.startup("resident-B.tested.hbapp")), SLOT_B_SHA256)
        self.assertEqual(sha256_file(self.startup("resident.tested.hbapp")), RESIDENT_SHA256)
        self.assertEqual(sha256_file(self.startup("resident-A.tested.hbapp")), SLOT_A_SHA256)
        self.assertTrue(self.startup("resident-B.active").is_file())
        self.assertTrue(self.startup("resident-nogrow.active").is_file())
        self.assertEqual(len(self.pack("/Apps/AllApps-B.pack")), 8)
        self.assertEqual(len(self.pack("/Apps/AllApps.pack")), 7)
        # ab selector is never touched
        self.assertEqual(
            sha256_file(self.startup("ab.hbapp")),
            sha256_file(ROOT / "untether/artifacts/device/Startup/ab.hbapp"),
        )
        # idempotent re-run keeps the tested backups
        enable_cap(self.nano, **self.kw)
        self.assertEqual(sha256_file(self.startup("resident-B.tested.hbapp")), SLOT_B_SHA256)

    def test_notes_then_chess_land_in_slot_b_pack(self):
        from host.apps import add_apps

        enable_cap(self.nano, **self.kw)
        add_apps(self.nano, ["Notes"], **self.kw)
        add_apps(self.nano, ["Chess"], **self.kw)
        names = self.pack("/Apps/AllApps-B.pack")
        self.assertEqual(len(names), 10)
        self.assertEqual(names[-3:], ["Mario 64", "Notes", "Chess"])
        self.assertEqual(len(self.pack("/Apps/AllApps.pack")), 7)
        self.assertEqual(sha256_file(self.startup("resident-B.hbapp")), NOGROW_B_SHA256)

    def test_remove_with_dormant_slot_a_pack_then_add(self):
        """apps remove Paint was refused (core hashes fine).

        The post-remove check inspected the dormant slot-A AllApps.pack,
        which still names Paint after Paint.hbapp is deleted. With slot B
        active only AllApps-B.pack must have every exe.
        """
        from host.apps import add_apps, remove_apps
        from host.untether import check_core_loader_hashes

        exe = self.vol / "Apps" / "Executables" / "Mario 64.hbapp"
        exe.write_bytes(b"\0" * 64)  # real Mario is on the nano; test needs an exe
        (self.vol / "Apps" / "Icons" / "Mario 64.bin").write_bytes(b"\0" * 64)
        enable_cap(self.nano, **self.kw)
        add_apps(self.nano, ["Notes"], **self.kw)
        add_apps(self.nano, ["Chess"], **self.kw)
        before = check_core_loader_hashes(self.nano)
        self.assertTrue(before["ok"], before)
        for name in ("Paint", "Tetris", "Snake"):
            remove_apps(self.nano, [name], **self.kw)
            self.assertFalse((self.vol / "Apps" / "Executables" / f"{name}.hbapp").exists())
        after = check_core_loader_hashes(self.nano)
        self.assertTrue(after["ok"], after)
        self.assertEqual(after["pack"]["pack_fat"], "/Apps/AllApps-B.pack")
        self.assertEqual(sorted(after["pack"]["dormant_missing"]), ["Paint", "Snake", "Tetris"])
        # slot-A pack is left alone (restore-tested rewrites it before slot A boots)
        self.assertEqual(self.pack("/Apps/AllApps.pack"), self.A_NAMES)
        for name in ("Wallet", "Screenshot", "Books"):
            add_apps(self.nano, [name], **self.kw)
        names = self.pack("/Apps/AllApps-B.pack")
        self.assertEqual(
            sorted(names),
            sorted(["Game Boy", "Probe", "Blocks", "Calculator", "Mario 64", "Notes",
                    "Chess", "Wallet", "Screenshot", "Books"]),
        )
        self.assertTrue(check_core_loader_hashes(self.nano)["ok"])
        self.assertEqual(sha256_file(self.startup("resident-B.hbapp")), NOGROW_B_SHA256)

    def test_active_pack_missing_exe_still_fails(self):
        from host.untether import check_core_loader_hashes

        enable_cap(self.nano, **self.kw)
        # Mario 64 is in AllApps-B.pack but has no exe on this test volume.
        r = check_core_loader_hashes(self.nano)
        self.assertTrue(r["core_ok"])
        self.assertFalse(r["ok"])
        self.assertIn("Mario 64", r["pack"]["missing_executables"])

    def test_disable_refuses_over_eight_then_keeps_mario(self):
        from host.apps import add_apps, remove_apps
        from host.cap import disable_cap

        enable_cap(self.nano, **self.kw)
        add_apps(self.nano, ["Notes"], **self.kw)
        with self.assertRaises(Refused) as ctx:
            disable_cap(self.nano, **self.kw)
        self.assertIn("AllApps-B.pack has 9", str(ctx.exception))
        self.assertTrue(self.startup("resident-nogrow.active").is_file())
        remove_apps(self.nano, ["Notes"], keep_files=True, **self.kw)
        rec = disable_cap(self.nano, **self.kw)
        self.assertTrue(rec["slot_b"])
        self.assertFalse(self.startup("resident-nogrow.active").exists())
        self.assertTrue(self.startup("resident-B.active").is_file())
        self.assertEqual(sha256_file(self.startup("resident-B.hbapp")), SLOT_B_SHA256)
        self.assertEqual(sha256_file(self.startup("resident.hbapp")), RESIDENT_SHA256)
        self.assertEqual(sha256_file(self.startup("resident-A.hbapp")), SLOT_A_SHA256)
        self.assertIn("Mario 64", self.pack("/Apps/AllApps-B.pack"))

    def test_restore_tested_then_mario_reseeds_b_pack(self):
        from host.app_catalog import by_name
        from host.apps import add_apps

        enable_cap(self.nano, **self.kw)
        add_apps(self.nano, ["Notes"], **self.kw)
        add_apps(self.nano, ["Chess"], **self.kw)
        restored = restore_tested_pack(self.nano, **self.kw)
        self.assertTrue(restored["nogrow_rolled_back"])
        self.assertTrue(restored["slot_b_deactivated"])
        self.assertEqual(sha256_file(self.startup("resident-B.hbapp")), SLOT_B_SHA256)
        self.assertEqual(len(self.pack("/Apps/AllApps-B.pack")), 10)
        # Re-add Mario on stock residents: must not merge into the stale 10-app B pack.
        cat = dict(by_name())
        mario = dict(cat["Mario 64"])
        mario["present"] = True
        cat["Mario 64"] = mario
        with mock.patch("host.app_catalog.by_name", return_value=cat), mock.patch(
            "host.apps._copy_port_data"
        ), mock.patch(
            "host.apps.local_app_files",
            return_value=(
                ROOT / "untether/artifacts/extra/Executables/Notes.hbapp",
                ROOT / "untether/artifacts/extra/Icons/Notes.bin",
            ),
        ):
            add_apps(self.nano, ["Mario 64"], **self.kw)
        names = self.pack("/Apps/AllApps-B.pack")
        self.assertEqual(names, tested_app_names() + ["Mario 64"])
        self.assertTrue(self.startup("resident-B.active").is_file())


if __name__ == "__main__":
    unittest.main()
