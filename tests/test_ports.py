#!/usr/bin/env python3
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host.app_catalog import catalog, reset_catalog_cache, resolve_name
from host.apps import (
    add_apps,
    pack_app_names,
    restore_tested_pack,
)
from host.compat import Refused
from host.const import SLOT_B_SHA256, TESTED_DISK_SIZE, artifact
from host.device import Nano, annotate_size
from host.ports import (
    MARIO_CMD,
    TEMPLE_BUNDLE_ID,
    _payload_app_dir,
    assert_sm64_rom,
    extra_data_dir,
    missing_help,
    prepare_for_add,
)

ROOT = Path(__file__).resolve().parent.parent


class PortCatalogTests(unittest.TestCase):
    def setUp(self):
        reset_catalog_cache()

    def tearDown(self):
        reset_catalog_cache()

    def test_mario_aliases(self):
        self.assertEqual(resolve_name("Mario"), "Mario 64")
        self.assertEqual(resolve_name("mario"), "Mario 64")
        self.assertEqual(resolve_name("sm64"), "Mario 64")
        self.assertEqual(resolve_name("super-mario"), "Mario 64")
        self.assertEqual(resolve_name("Mario 64"), "Mario 64")
        self.assertEqual(resolve_name("flappy"), "Flappy Bird")
        self.assertEqual(resolve_name("temple"), "Temple Run")
        self.assertEqual(resolve_name("doodle"), "Doodle Jump")

    def test_catalog_lists_olievans_ports(self):
        rows = {r["name"]: r for r in catalog()}
        for name in ("Mario 64", "Mario Kart", "Temple Run", "Flappy Bird", "Doodle Jump"):
            self.assertIn(name, rows)
            self.assertIn("olievans123", rows[name]["author"])
        self.assertTrue(rows["Mario 64"]["needs_rom"])
        self.assertTrue(rows["Mario 64"]["large_gl"])
        self.assertTrue(rows["Temple Run"]["needs_payload"])
        self.assertTrue(rows["Temple Run"]["large_gl"])
        self.assertTrue(rows["Flappy Bird"]["needs_payload"])
        self.assertFalse(rows["Flappy Bird"]["large_gl"])
        self.assertIn("576", rows["Mario 64"]["note"])
        self.assertIn("slot b", rows["Mario 64"]["note"].lower())

    def test_mario_help_is_the_command(self):
        text = missing_help("Mario 64")
        self.assertIn(MARIO_CMD, text)
        self.assertIn("not a Game Boy ROM", text)
        self.assertNotIn("\u2014", text)

    def test_prepare_mario_without_rom_refuses(self):
        with self.assertRaises(Refused) as ctx:
            prepare_for_add("Mario 64")
        self.assertIn("apps add Mario --rom", str(ctx.exception))
        self.assertIn("9bef1128", str(ctx.exception))

    def test_sm64_rom_hash_refused(self):
        with tempfile.NamedTemporaryFile(suffix=".z64", delete=False) as fh:
            fh.write(b"not a mario rom")
            path = Path(fh.name)
        self.addCleanup(path.unlink)
        with self.assertRaises(Refused) as ctx:
            assert_sm64_rom(path)
        self.assertIn("USA Super Mario 64", str(ctx.exception))


class MarioAddSlotBTests(unittest.TestCase):
    def setUp(self):
        reset_catalog_cache()
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        os.environ["NANO7_STATE_DIR"] = self.td.name
        extra_exe = artifact("artifacts/extra/Executables")
        extra_exe.mkdir(parents=True, exist_ok=True)
        extra_icon = artifact("artifacts/extra/Icons")
        extra_icon.mkdir(parents=True, exist_ok=True)
        self.hbapp = extra_exe / "Mario 64.hbapp"
        self.hbapp.write_bytes(b"HRL1-dummy-mario")
        self.icon = extra_icon / "Mario 64.bin"
        self.icon.write_bytes(b"\x00" * 50208)
        data = extra_data_dir("Mario 64")
        data.mkdir(parents=True, exist_ok=True)
        self.data_file = data / "manifest.txt"
        self.data_file.write_text("dummy sm64 data\n")
        reset_catalog_cache()

    def tearDown(self):
        if self.hbapp.exists():
            self.hbapp.unlink()
        data = extra_data_dir("Mario 64")
        if data.is_dir():
            shutil.rmtree(data)
        if self.icon.is_file():
            self.icon.unlink()
        reset_catalog_cache()

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

    def test_add_mario_writes_slot_b_not_slot_a_pack(self):
        vol = Path(self.td.name) / "vol"
        shutil.copytree(ROOT / "untether" / "artifacts" / "device", vol / "Apps")
        nano = self._nano(vol)

        def fake_mount(_nano, **_kw):
            _nano.mountpoints = [str(vol)]
            return vol

        with mock.patch("host.fat.ensure_fat_mount", side_effect=fake_mount), mock.patch(
            "host.fat.assert_not_hfs"
        ), mock.patch("host.untether.unmount"), mock.patch(
            "host.untether.ensure_fat_mount", side_effect=fake_mount
        ), mock.patch(
            "host.untether.close_finder_ipod_windows", return_value=0
        ), mock.patch(
            "host.apps.check_usb_serial"
        ), mock.patch(
            "host.apps.check_disk_size", return_value={"size": TESTED_DISK_SIZE}
        ), mock.patch(
            "host.apps.check_backup", return_value="a" * 64
        ), mock.patch(
            "host.apps.layout_for_disk_size", return_value=None
        ), mock.patch(
            "host.apps.assert_not_hfs"
        ):
            rec = add_apps(
                nano,
                ["Mario"],
                backup=Path(self.td.name) / "missing.img",
                backup_sha256="a" * 64,
                skip_wait=True,
                skip_backup_file=True,
            )
            self.assertTrue((vol / "Apps" / "Startup" / "resident-B.active").is_file())
            restored = restore_tested_pack(
                nano,
                backup=Path(self.td.name) / "missing.img",
                backup_sha256="a" * 64,
                skip_wait=True,
                skip_backup_file=True,
            )
        self.assertTrue(rec["ok"])
        self.assertTrue(rec["slot_b"])
        self.assertIn("Mario 64", rec["pack_names"])
        self.assertEqual(
            pack_app_names((vol / "Apps" / "AllApps.pack").read_bytes()),
            ["Blocks", "Game Boy", "Probe"],
        )
        bpack = vol / "Apps" / "AllApps-B.pack"
        self.assertTrue(bpack.is_file())
        self.assertEqual(
            pack_app_names(bpack.read_bytes()),
            ["Blocks", "Game Boy", "Probe", "Mario 64"],
        )
        self.assertTrue((vol / "Apps" / "Executables" / "Mario 64.hbapp").is_file())
        self.assertTrue((vol / "Apps" / "Data" / "SM64" / "manifest.txt").is_file())
        from host.compat import sha256_file

        self.assertEqual(
            sha256_file(vol / "Apps" / "Startup" / "resident-B.hbapp"),
            SLOT_B_SHA256,
        )
        self.assertTrue(restored["slot_b_deactivated"])
        self.assertFalse((vol / "Apps" / "Startup" / "resident-B.active").exists())
        self.assertEqual(restored["kept"], ["Blocks", "Game Boy", "Probe"])


class IpaPayloadTests(unittest.TestCase):
    def _ipa(self, td: Path, bundle: str, app_name: str = "TempleRun") -> Path:
        import plistlib
        import zipfile

        stage = td / "stage"
        app = stage / "Payload" / f"{app_name}.app"
        app.mkdir(parents=True)
        info = {
            "CFBundleIdentifier": bundle,
            "CFBundleVersion": "1.0",
            "CFBundleExecutable": app_name,
            "MinimumOSVersion": "3.0",
            "CFBundleName": app_name,
        }
        (app / "Info.plist").write_bytes(plistlib.dumps(info))
        (app / app_name).write_bytes(b"\xca\xfe\xba\xbe dummy")
        ipa = td / "game.ipa"
        with zipfile.ZipFile(ipa, "w") as zf:
            for path in app.rglob("*"):
                zf.write(path, path.relative_to(stage).as_posix())
        return ipa

    def test_ipa_extracts_and_checks_temple_bundle(self):
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            ipa = self._ipa(td, TEMPLE_BUNDLE_ID)
            app = _payload_app_dir(
                ipa,
                ("TempleRun.app", "Temple Run.app"),
                expected_bundle=TEMPLE_BUNDLE_ID,
            )
            self.assertTrue(app.is_dir())
            self.assertEqual(app.name, "TempleRun.app")
            self.assertTrue((app / "TempleRun").is_file())

    def test_wrong_bundle_refused(self):
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            ipa = self._ipa(td, "com.popcap.PvZ", app_name="PvZ")
            with self.assertRaises(Refused) as ctx:
                _payload_app_dir(
                    ipa,
                    ("TempleRun.app", "Temple Run.app"),
                    expected_bundle=TEMPLE_BUNDLE_ID,
                )
            self.assertIn("com.popcap.PvZ", str(ctx.exception))
            self.assertIn(TEMPLE_BUNDLE_ID, str(ctx.exception))

    def test_unpacked_app_still_works(self):
        import plistlib

        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            app = td / "TempleRun.app"
            app.mkdir()
            (app / "Info.plist").write_bytes(
                plistlib.dumps({"CFBundleIdentifier": TEMPLE_BUNDLE_ID})
            )
            (app / "TempleRun").write_bytes(b"x")
            got = _payload_app_dir(
                app,
                ("TempleRun.app",),
                expected_bundle=TEMPLE_BUNDLE_ID,
            )
            self.assertEqual(got, app)


if __name__ == "__main__":
    unittest.main()
