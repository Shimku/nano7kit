#!/usr/bin/env python3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host.apps import (
    RECOVERY_HINT,
    assert_one_extra_add,
    assert_pack_writable,
    build_pack,
    bundle_name,
    catalog,
    decode_lasttrace,
    filter_pack,
    format_lasttrace,
    is_protected_name,
    merge_if_writable,
    normalize_selection,
    pack_app_names,
    pack_for_selection,
    pack_is_looping,
    parse_pack,
    tested_app_names,
)
from host.compat import Refused, sha256_file
from host.const import ALLAPPS_PACK_SHA256, artifact

ROOT = Path(__file__).resolve().parent.parent


class PackTests(unittest.TestCase):
    def setUp(self):
        self.pack = artifact("artifacts/device/AllApps.pack")
        self.data = self.pack.read_bytes()

    def test_tested_pack_hash_and_names(self):
        self.assertEqual(sha256_file(self.pack), ALLAPPS_PACK_SHA256)
        names = pack_app_names(self.data)
        self.assertEqual(names, ["Blocks", "Game Boy", "Probe"])
        self.assertEqual(tested_app_names(), names)

    def test_filter_subset_rewrites_offsets(self):
        rebuilt = filter_pack(self.data, ["Probe", "Blocks"])
        names = pack_app_names(rebuilt)
        self.assertEqual(set(names), {"Probe", "Blocks"})
        self.assertNotEqual(rebuilt, self.data)
        # round-trip
        again = build_pack(parse_pack(rebuilt))
        self.assertEqual(pack_app_names(again), names)

    def test_empty_pack_is_valid(self):
        empty = filter_pack(self.data, [])
        self.assertEqual(pack_app_names(empty), [])

    def test_unknown_name_refused(self):
        with self.assertRaises(Refused) as ctx:
            filter_pack(self.data, ["NotAnApp"])
        self.assertIn("does not contain", str(ctx.exception))

    def test_subset_pack_is_not_tested_hash(self):
        data, digest, tested = pack_for_selection(["Probe"])
        self.assertFalse(tested)
        self.assertNotEqual(digest, ALLAPPS_PACK_SHA256)
        self.assertEqual(pack_app_names(data), ["Probe"])
        full, full_hash, full_tested = pack_for_selection(["Blocks", "Game Boy", "Probe"])
        self.assertTrue(full_tested)
        self.assertEqual(full_hash, ALLAPPS_PACK_SHA256)
        self.assertEqual(full, self.data)

    def test_bundle_name_ascii(self):
        for bundle in parse_pack(self.data):
            name = bundle_name(bundle)
            self.assertTrue(name)
            self.assertNotIn("\x00", name)


class SelectionTests(unittest.TestCase):
    def test_normalize_case_and_all(self):
        self.assertEqual(normalize_selection(["probe", "BLOCKS"]), ["Probe", "Blocks"])
        self.assertEqual(normalize_selection(["all"]), tested_app_names())
        with self.assertRaises(Refused):
            normalize_selection(["Doom"])

    def test_protected_names(self):
        self.assertTrue(is_protected_name("resident"))
        self.assertTrue(is_protected_name("resident-B"))
        self.assertTrue(is_protected_name("ab"))
        self.assertFalse(is_protected_name("Probe"))

    def test_format_list_does_not_crash(self):
        from host.apps import format_list

        text = format_list(on_disk=["Blocks", "Game Boy", "Probe"], selected=["Calculator"])
        self.assertIn("Calculator", text)
        self.assertIn("hardware-tested", text)
        self.assertIn("installed", text)
        self.assertIn("Looping extras set", text)
        self.assertIn("hardware-safe", text)
        self.assertIn("Paint", text)
        self.assertIn("restore-tested", text)
        self.assertIn("one extra", text.lower())
        self.assertIn("--keep-files", text)

    def test_catalog_present(self):
        rows = catalog()
        names = [r["name"] for r in rows]
        for n in tested_app_names():
            self.assertIn(n, names)
        tested = [r for r in rows if r.get("tested")]
        self.assertTrue(all(r["present"] for r in tested))
        extras = [r for r in rows if r.get("origin") == "built"]
        self.assertGreaterEqual(len(extras), 2)  # shipped first-party; the rest build on demand
        calc = next(r for r in rows if r["name"] == "Calculator")
        self.assertTrue(calc["compatible"])
        self.assertFalse(calc["tested"])
        lap = next(r for r in rows if r["name"] == "Lap")
        self.assertTrue(lap["present"])

    def test_extras_and_aliases(self):
        self.assertEqual(
            normalize_selection(["calculator", "2048", "notes"], allow_extra=True),
            ["Calculator", "G2048", "Notes"],
        )
        self.assertEqual(
            normalize_selection(["Mario"], allow_extra=True),
            ["Mario 64"],
        )
        self.assertEqual(normalize_selection(["all"]), tested_app_names())
        with self.assertRaises(Refused) as ctx:
            normalize_selection(["Calculator"])
        self.assertIn("apps add", str(ctx.exception))
        with self.assertRaises(Refused) as extras_ctx:
            normalize_selection(["extras"], allow_extra=True)
        self.assertIn("reboot", str(extras_ctx.exception))
        self.assertIn("Paint", str(extras_ctx.exception))
        self.assertTrue(is_protected_name("resident.hbapp"))
        self.assertTrue(is_protected_name("AllApps.pack"))
        self.assertFalse(is_protected_name("Calculator"))

    def test_merge_keeps_tested_and_appends(self):
        from host.app_icons import shared_screen_blob
        from host.apps import make_hbap_bundle, merge_bundles

        calc = next(r for r in catalog() if r["name"] == "Calculator")
        bundle = make_hbap_bundle(calc, shared_screen_blob())
        data = artifact("artifacts/device/AllApps.pack").read_bytes()
        merged = merge_bundles(data, [bundle])
        self.assertEqual(
            pack_app_names(merged),
            ["Blocks", "Game Boy", "Probe", "Calculator"],
        )


class LoopGuardTests(unittest.TestCase):
    def test_hardware_safe_pack_is_writable(self):
        assert_pack_writable(tested_app_names())
        assert_pack_writable(tested_app_names() + ["Paint"])
        self.assertIsNone(pack_is_looping(tested_app_names()))

    def test_looping_eleven_refused(self):
        from host.app_catalog import FIRST_SLICE

        names = tested_app_names() + list(FIRST_SLICE)
        with self.assertRaises(Refused) as ctx:
            assert_pack_writable(names)
        msg = str(ctx.exception)
        self.assertIn("reboot", msg)
        self.assertIn("restore-tested", msg)
        self.assertIn("lasttrace", msg)

    def test_count_over_eight_refused(self):
        names = tested_app_names() + [
            "Paint",
            "Tetris",
            "Snake",
            "G2048",
            "Files",
            "Notes",
        ]
        self.assertEqual(len(names), 9)
        with self.assertRaises(Refused) as ctx:
            assert_pack_writable(names)
        self.assertIn("grow_cache", str(ctx.exception))

    def test_eight_is_under_grow_cache_limit(self):
        names = tested_app_names() + ["Paint", "Tetris", "Snake", "G2048", "Files"]
        self.assertEqual(len(names), 8)
        assert_pack_writable(names)

    def test_one_extra_add(self):
        self.assertEqual(assert_one_extra_add(["Paint"]), ["Paint"])
        self.assertEqual(
            assert_one_extra_add(["Blocks", "Game Boy", "Probe", "Paint"]),
            ["Paint"],
        )
        self.assertEqual(assert_one_extra_add(tested_app_names()), [])
        with self.assertRaises(Refused) as ctx:
            assert_one_extra_add(["Calculator", "Notes"])
        self.assertIn("one extra", str(ctx.exception))
        self.assertIn("Paint", str(ctx.exception))

    def test_merge_if_writable_drops_looping_set(self):
        from host.app_catalog import FIRST_SLICE
        from host.app_icons import shared_screen_blob
        from host.apps import make_hbap_bundle, merge_bundles

        data = artifact("artifacts/device/AllApps.pack").read_bytes()
        screen = shared_screen_blob()
        extras = []
        by = {row["name"]: row for row in catalog()}
        for name in FIRST_SLICE:
            extras.append(make_hbap_bundle(by[name], screen))
        looping = merge_bundles(data, extras)
        self.assertEqual(len(pack_app_names(looping)), 11)
        kept, ok = merge_if_writable(data, extras)
        self.assertFalse(ok)
        self.assertEqual(pack_app_names(kept), tested_app_names())
        paint = make_hbap_bundle(by["Paint"], screen)
        kept2, ok2 = merge_if_writable(data, [paint])
        self.assertTrue(ok2)
        self.assertEqual(
            pack_app_names(kept2),
            ["Blocks", "Game Boy", "Probe", "Paint"],
        )

    def test_catalog_risk_and_hardware_safe(self):
        rows = {r["name"]: r for r in catalog()}
        for name in tested_app_names():
            self.assertTrue(rows[name]["hardware_safe"])
            self.assertEqual(rows[name]["risk"], 0)
        self.assertEqual(rows["Paint"]["risk"], 1)
        self.assertFalse(rows["Paint"]["hardware_safe"])
        self.assertGreaterEqual(rows["Calendar"]["risk"], 4)
        self.assertIn("lowest-risk", rows["Paint"]["note"])

    def test_lasttrace_decode(self):
        import struct

        magic, max_n = 0x48425452, 256
        data = bytearray(16 + 16 * max_n)
        count = 3
        struct.pack_into("<4I", data, 0, magic, count % max_n, count, 7)
        for i, tag in enumerate((b"RESIDENT", b"PKICON  ", b"APPSREG ")):
            struct.pack_into("<8sII", data, 16 + 16 * i, tag, 0x09130000, i)
        decoded = decode_lasttrace(bytes(data))
        self.assertEqual(decoded["seq"], 7)
        self.assertEqual(decoded["count"], 3)
        self.assertEqual(decoded["tags"], ["RESIDENT", "PKICON", "APPSREG"])
        text = format_lasttrace(decoded)
        self.assertIn("RESIDENT", text)
        self.assertIn("APPSREG", text)
        with self.assertRaises(Refused):
            decode_lasttrace(b"nope")

    def test_lasttrace_absent_notes_watchdog(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "host" / "apps.py").read_text()
        self.assertIn("watchdog or hard reset", src)
        self.assertIn("/Apps/Data/<App>/log.txt", src)

    def test_recovery_hint_has_no_em_dash(self):
        self.assertNotIn("\u2014", RECOVERY_HINT)
        self.assertIn("restore-tested", RECOVERY_HINT)
        self.assertIn("lasttrace", RECOVERY_HINT)


class WritePackTests(unittest.TestCase):
    def test_write_pack_file(self):
        from host.apps import write_pack_file

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "AllApps.pack"
            rec = write_pack_file(dest, ["Blocks"])
            self.assertTrue(dest.is_file())
            self.assertEqual(pack_app_names(dest.read_bytes()), ["Blocks"])
            self.assertFalse(rec["hardware_tested_pack"])


class AddRemoveMountTests(unittest.TestCase):
    def test_add_merges_and_keeps_loader(self):
        import os
        import shutil
        from unittest import mock

        from host.apps import add_apps, pack_app_names, remove_apps
        from tests.built import need_built

        need_built(self, "Calculator", "Notes")
        from host.const import RESIDENT_SHA256, TESTED_DISK_SIZE
        from host.device import Nano, annotate_size
        from host.untether import check_core_loader_hashes

        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        os.environ["NANO7_STATE_DIR"] = td.name
        vol = Path(td.name) / "vol"
        shutil.copytree(ROOT / "untether" / "artifacts" / "device", vol / "Apps")
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

        with mock.patch(
            "host.fat.ensure_fat_mount", side_effect=fake_mount
        ), mock.patch("host.fat.assert_not_hfs"), mock.patch(
            "host.untether.unmount"
        ), mock.patch(
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
            with self.assertRaises(Refused) as two:
                add_apps(
                    nano,
                    ["Calculator", "Notes"],
                    backup=Path(td.name) / "missing.img",
                    backup_sha256="a" * 64,
                    skip_wait=True,
                    skip_backup_file=True,
                )
            self.assertIn("one extra", str(two.exception))
            rec = add_apps(
                nano,
                ["Calculator"],
                backup=Path(td.name) / "missing.img",
                backup_sha256="a" * 64,
                skip_wait=True,
                skip_backup_file=True,
            )
            rec2 = add_apps(
                nano,
                ["Notes"],
                backup=Path(td.name) / "missing.img",
                backup_sha256="a" * 64,
                skip_wait=True,
                skip_backup_file=True,
            )
        self.assertTrue(rec["ok"])
        self.assertTrue(rec2["ok"])
        self.assertIn("Calculator", rec["pack_names"])
        self.assertNotIn("Notes", rec["pack_names"])
        self.assertIn("Notes", rec2["pack_names"])
        self.assertIn("Calculator", rec2["pack_names"])
        self.assertIn("Blocks", rec["pack_names"])
        self.assertTrue((vol / "Apps" / "Executables" / "Calculator.hbapp").is_file())
        self.assertEqual(
            (vol / "Apps" / "Startup" / "resident.hbapp").stat().st_size > 0,
            True,
        )
        hashes = check_core_loader_hashes(nano)
        self.assertTrue(hashes.get("core_ok") or hashes.get("ok"))
        with mock.patch(
            "host.fat.ensure_fat_mount", side_effect=fake_mount
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
            with self.assertRaises(Refused) as ctx:
                remove_apps(
                    nano,
                    ["resident"],
                    backup=Path(td.name) / "missing.img",
                    backup_sha256="a" * 64,
                    skip_wait=True,
                    skip_backup_file=True,
                )
            self.assertIn("resident", str(ctx.exception).lower())
            gone = remove_apps(
                nano,
                ["Calculator"],
                backup=Path(td.name) / "missing.img",
                backup_sha256="a" * 64,
                skip_wait=True,
                skip_backup_file=True,
            )
        self.assertIn("Calculator", gone["removed"])
        self.assertIn("Notes", gone["kept"])
        self.assertIn("Blocks", gone["kept"])
        self.assertFalse((vol / "Apps" / "Executables" / "Calculator.hbapp").is_file())
        self.assertTrue((vol / "Apps" / "Startup" / "resident.hbapp").is_file())
        from host.apps import dump_lasttrace, restore_tested_pack

        import struct

        ring = bytearray(16 + 16 * 256)
        struct.pack_into("<4I", ring, 0, 0x48425452, 1, 1, 9)
        struct.pack_into("<8sII", ring, 16, b"APPSREG ", 3, 1)
        (vol / "Apps" / "Startup" / "lasttrace.bin").write_bytes(bytes(ring))
        with mock.patch(
            "host.fat.ensure_fat_mount", side_effect=fake_mount
        ), mock.patch("host.apps.assert_not_hfs"):
            lt = dump_lasttrace(nano, dest=Path(td.name) / "host-lasttrace.bin")
        self.assertTrue(lt["ok"])
        self.assertIn("APPSREG", lt["tags"])
        self.assertTrue((Path(td.name) / "host-lasttrace.bin").is_file())
        with mock.patch(
            "host.fat.ensure_fat_mount", side_effect=fake_mount
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
            restored = restore_tested_pack(
                nano,
                backup=Path(td.name) / "missing.img",
                backup_sha256="a" * 64,
                skip_wait=True,
                skip_backup_file=True,
            )
        self.assertTrue(restored["tested_pack"])
        self.assertEqual(restored["kept"], ["Blocks", "Game Boy", "Probe"])
        self.assertIn("Notes", restored["dropped"])
        self.assertEqual(
            pack_app_names((vol / "Apps" / "AllApps.pack").read_bytes()),
            ["Blocks", "Game Boy", "Probe"],
        )

    def test_remove_loader_flag_still_refused(self):
        from host.apps import remove_apps
        from unittest import mock

        nano = mock.Mock(usb_serial="X", size=1, raw_device="/dev/null")
        with self.assertRaises(Refused):
            remove_apps(
                nano,
                ["Probe"],
                backup=Path("/tmp/x"),
                backup_sha256="a" * 64,
                skip_wait=True,
                remove_loader=True,
            )


if __name__ == "__main__":
    unittest.main()

