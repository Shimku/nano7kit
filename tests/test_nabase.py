"""NanoApps 1.0 base: launcher slot, write allow-list, base detection, backup/restore, add/remove, plan."""

import hashlib
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host import installer, nabase  # noqa: E402
from host.compat import Refused  # noqa: E402
from host.installer import NanoState, build_plan, by_name, home_count, is_on, render_menu, toggle  # noqa: E402

SILICON = installer.MacInfo("Darwin", "arm64", False, "15.1")


def their_slot(version=nabase.NA_RESIDENT_VERSION) -> bytes:
    """A launcher that is not ours: our HRL1 with one code byte changed."""
    hrl = bytearray(nabase.our_hrl())
    hrl[40] ^= 0xFF
    return nabase.wrap_slot(bytes(hrl), version)


def folder(root: Path, name: str) -> None:
    d = root / "Apps" / f"{name}.nanoapp"
    d.mkdir(parents=True)
    (d / "manifest.napp").write_bytes(b"NAPP" + bytes(60))
    (d / "app.hbapp").write_bytes(b"x" * 32)


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name) / "IPOD"
        self.root.mkdir()
        music = self.root / "iPod_Control" / "Music" / "F00"
        music.mkdir(parents=True)
        (music / "a.mp3").write_bytes(b"song")
        self.backups = Path(self.td.name) / "backups"
        self.backups.mkdir()
        p = mock.patch.object(nabase, "backup_base_dir", return_value=self.backups)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.td.cleanup)
        self.out = []

    def nanoapps_disk(self, names=("Blocks", "Pong", "Paint")):
        (self.root / "Library").mkdir(exist_ok=True)
        (self.root / "Library" / "resident.hbapp").write_bytes(their_slot())
        for n in names:
            folder(self.root, n)

    def music_untouched(self):
        self.assertEqual((self.root / "iPod_Control/Music/F00/a.mp3").read_bytes(), b"song")


class SlotTests(unittest.TestCase):
    def test_our_slot_is_the_pinned_launcher_rewrapped(self):
        slot = nabase.our_slot()
        hrl, version = nabase.read_slot(slot)
        self.assertEqual(version, nabase.OUR_RESIDENT_VERSION)
        self.assertGreater(nabase.OUR_RESIDENT_VERSION, nabase.NA_RESIDENT_VERSION)
        self.assertEqual(hrl, nabase.our_hrl())
        self.assertEqual(len(slot), len(hrl) + 20)
        magic, abi, fw, _ = struct.unpack_from("<4I", slot, len(hrl))
        self.assertEqual((magic, abi, fw), (0x52564248, 2, 0x00010102))
        self.assertEqual(int.from_bytes(slot[-4:], "little"), nabase.fnv1a32(slot[:-4]))
        # Same code bytes as the untethered Launcher file (v0: HRL1 + FNV).
        v0 = Path(nabase.our_hrl.__globals__["const"].artifact("artifacts/extra/Startup/resident-B-na.hbapp"))
        self.assertEqual(v0.read_bytes()[:-4], hrl)

    def test_bad_slots_refused(self):
        slot = bytearray(nabase.our_slot())
        bad = bytes(slot[:-1]) + bytes([slot[-1] ^ 1])
        with self.assertRaises(Refused):
            nabase.read_slot(bad)
        hrl = nabase.our_hrl()
        wrong_abi = hrl + struct.pack("<4I", 0x52564248, 3, 0x00010102, 5)
        with self.assertRaises(Refused):
            nabase.read_slot(wrong_abi + struct.pack("<I", nabase.fnv1a32(wrong_abi)))
        big = bytearray(hrl)
        struct.pack_into("<I", big, 12, nabase.MAX_RESIDENT_SPAN + 4)
        with self.assertRaises(Refused):
            nabase.read_slot(nabase.wrap_slot(bytes(big), 7))
        with self.assertRaises(Refused):
            nabase.wrap_slot(hrl, 0)

    def test_unpinned_launcher_refused(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "r.hbapp"
            p.write_bytes(their_slot())
            with self.assertRaises(Refused):
                nabase.our_hrl(p)


class VolumeTests(Base):
    def test_allow_list(self):
        vol = nabase.Volume(self.root)
        vol.write_bytes("/Apps/Data/x.bin", b"1")
        vol.write_bytes(nabase.RESIDENT, b"2")
        for bad in ("/iPod_Control/Music/F00/b.mp3", "/Library/AllApps.pack", "/NanoApps/loader.hbapp",
                    "/Data/Notes/note1.txt", "/x.txt"):
            with self.assertRaises(Refused, msg=bad):
                vol.write_bytes(bad, b"3")
        with self.assertRaises(Refused):
            vol.delete_tree("/iPod_Control")
        self.music_untouched()


class DetectTests(Base):
    def test_cases(self):
        self.assertEqual(nabase.detect_base(None), "none")
        self.assertEqual(nabase.detect_base(self.root), "none")
        folder(self.root, "Pong")
        self.assertEqual(nabase.detect_base(self.root), "nanoapps")
        st = self.root / "Apps" / "Startup"
        st.mkdir(parents=True)
        (st / "ab.hbapp").write_bytes(b"x")
        self.assertEqual(nabase.detect_base(self.root), "mixed")
        nabase.mark_base(nabase.Volume(self.root), "nanoapps")
        self.assertEqual(nabase.detect_base(self.root), "nanoapps")
        nabase.mark_base(nabase.Volume(self.root), "untethered")
        self.assertEqual(nabase.detect_base(self.root), "untethered")

    def test_launcher_state(self):
        self.assertEqual(nabase.launcher_state(self.root)["kind"], "none")
        self.nanoapps_disk()
        st = nabase.launcher_state(self.root)
        self.assertEqual((st["kind"], st["version"]), ("theirs", nabase.NA_RESIDENT_VERSION))
        (self.root / "Library/resident.hbapp").write_bytes(nabase.our_slot())
        self.assertEqual(nabase.launcher_state(self.root)["kind"], "ours")
        (self.root / "Library/resident.hbapp").write_bytes(b"junk" * 10)
        self.assertEqual(nabase.launcher_state(self.root)["kind"], "invalid")

    def test_older_nano7kit_launcher_is_replaced(self):
        """The 1.1 Launcher installed on 3 Nov (slot e0e6f695...) reads as ours-old, so the
        installer offers the current one, and install_launcher replaces it without a
        backup that holds it (it is ours, not NanoApps')."""
        self.nanoapps_disk()
        nabase.backup(self.root, out=self.out.append)
        old = their_slot(nabase.OUR_RESIDENT_VERSION)
        old_hrl = nabase.read_slot(old)[0]
        with mock.patch.object(nabase.const, "NA_B_OLD_HRL_SHA256S",
                               frozenset({hashlib.sha256(old_hrl).hexdigest()})):
            (self.root / "Library/resident.hbapp").write_bytes(old)
            self.assertEqual(nabase.launcher_state(self.root)["kind"], "ours-old")
            st = NanoState(mount=self.root)
            installer.read_base(st, force="nanoapps")
            self.assertFalse(st.na)
            nabase.install_launcher(self.root, out=self.out.append)
            self.assertEqual(nabase.launcher_state(self.root)["kind"], "ours")
        self.music_untouched()

    def test_known_old_launcher_code_pinned(self):
        self.assertIn("db3f0612aef947028bae4bb1563dca2d59dd210f8417bf982a3e7f9a6733b7e9",
                      nabase.const.NA_B_OLD_HRL_SHA256S)
        self.assertNotIn(hashlib.sha256(nabase.our_hrl()).hexdigest(),
                         nabase.const.NA_B_OLD_HRL_SHA256S)


class InstallRestoreTests(Base):
    def test_launcher_needs_backup(self):
        self.nanoapps_disk()
        with self.assertRaises(Refused):
            nabase.install_launcher(self.root, out=self.out.append)
        self.assertEqual(nabase.launcher_state(self.root)["kind"], "theirs")

    def test_newer_launcher_refused(self):
        self.nanoapps_disk()
        (self.root / "Library/resident.hbapp").write_bytes(their_slot(nabase.OUR_RESIDENT_VERSION + 5))
        nabase.backup(self.root, out=self.out.append)
        with self.assertRaises(Refused):
            nabase.install_launcher(self.root, out=self.out.append)

    def test_backup_must_hold_current_launcher(self):
        self.nanoapps_disk()
        nabase.backup(self.root, out=self.out.append)
        (self.root / "Library/resident.hbapp").write_bytes(their_slot(nabase.NA_RESIDENT_VERSION - 1))
        with self.assertRaises(Refused):
            nabase.install_launcher(self.root, out=self.out.append)

    def test_install_then_restore(self):
        self.nanoapps_disk()
        before = (self.root / "Library/resident.hbapp").read_bytes()
        b = nabase.backup(self.root, out=self.out.append)
        meta = json.loads((b / "backup.json").read_text())
        self.assertIn("/Library/resident.hbapp", meta["files"])
        self.assertFalse(any(k.startswith("/iPod_Control") for k in meta["files"]))
        nabase.install_launcher(self.root, out=self.out.append)
        self.assertEqual(nabase.launcher_state(self.root)["kind"], "ours")
        self.assertTrue(any(nabase.WARN_REPLACE in line for line in self.out))
        self.assertEqual(nabase.detect_base(self.root), "nanoapps")
        nabase.restore_launcher(self.root, out=self.out.append)
        self.assertEqual((self.root / "Library/resident.hbapp").read_bytes(), before)
        self.music_untouched()


class AppTests(Base):
    def setUp(self):
        super().setUp()
        self.nanoapps_disk()
        nabase.backup(self.root, out=self.out.append)

    def test_add_writes_pack_our_launcher_reads(self):
        names = nabase.add(self.root, ["Lap"], out=self.out.append)
        self.assertEqual(nabase.active_pack(self.root), "/Apps/AllApps.pack")
        self.assertEqual(names, ["Game Boy", "Probe", "Lap"])   # Blocks comes from NanoApps' folder
        self.assertTrue((self.root / "Apps/Executables/Lap.hbapp").is_file())
        self.assertFalse((self.root / "Library/AllApps.pack").exists())   # their launcher's pack untouched
        home = nabase.home_names(self.root)
        self.assertEqual(home[:3], ["Game Boy", "Probe", "Lap"])
        self.assertIn("Pong", home)
        self.music_untouched()

    def test_blocks_added_when_no_folder(self):
        import shutil

        shutil.rmtree(self.root / "Apps/Blocks.nanoapp")
        self.assertEqual(nabase.add(self.root, [], out=self.out.append), ["Blocks", "Game Boy", "Probe"])

    def test_existing_untethered_pack_is_kept(self):
        nabase.add(self.root, ["Lap"], out=self.out.append)
        src = self.root / "Apps/AllApps.pack"
        (self.root / "Apps/AllApps-B.pack").write_bytes(src.read_bytes())
        src.unlink()
        nabase.add(self.root, ["JS Hero"], out=self.out.append)
        self.assertEqual(nabase.active_pack(self.root), "/Apps/AllApps-B.pack")
        self.assertIn("JS Hero", nabase.pack_names(self.root))

    def test_remove_pack_app_and_folder(self):
        nabase.add(self.root, ["Lap"], out=self.out.append)
        done = nabase.remove(self.root, ["Lap", "Pong"], out=self.out.append)
        self.assertEqual(done, ["Lap", "Pong"])
        self.assertFalse((self.root / "Apps/Executables/Lap.hbapp").exists())
        self.assertFalse((self.root / "Apps/Pong.nanoapp").exists())
        self.assertNotIn("Lap", nabase.home_names(self.root))

    def test_folder_not_in_backup_is_kept(self):
        folder(self.root, "Later")
        with self.assertRaises(Refused):
            nabase.remove(self.root, ["Later"], out=self.out.append)
        self.assertTrue((self.root / "Apps/Later.nanoapp").is_dir())

    def test_home_limit(self):
        for i in range(nabase.HOME_LIMIT - 4):
            folder(self.root, f"F{i}")
        with self.assertRaises(Refused):
            nabase.add(self.root, ["Lap"], out=self.out.append)

    def test_staged_folder_copy(self):
        nabase.add(self.root, ["Free RAM.nanoapp"], out=self.out.append)
        self.assertIn("Free RAM", nabase.folder_apps(self.root))
        with self.assertRaises(Refused):
            nabase.add(self.root, ["Paint.nanoapp"], out=self.out.append)   # theirs: never replaced

    def test_packages(self):
        n = nabase.packages(self.root, out=self.out.append)
        self.assertGreater(n, 0)
        self.assertTrue(any((self.root / "Apps/Data/PocketJS").iterdir()))


class InstallerNanoAppsTests(Base):
    def state(self):
        st = NanoState(present=True, mount=self.root, fstype="hfs", size=16_000_000_000)
        installer.read_base(st)
        return st

    def test_state_and_menu(self):
        self.nanoapps_disk()
        st = self.state()
        self.assertTrue(st.nanoapps and st.loader and not st.na)
        self.assertIn("NanoApps 1.0 base", st.label())
        self.assertTrue(is_on(by_name("Paint folder"), st))
        self.assertEqual(installer.home_limit(st), nabase.HOME_LIMIT)
        self.assertEqual(home_count(st, []), 3 + 2)   # Blocks, Pong, Paint + Game Boy, Probe
        menu = render_menu(SILICON, st, installer.default_selection(st))
        self.assertIn("of 60", menu)
        self.assertIn("./start nabase restore", menu)

    def test_plan_backup_first_no_root_no_firmware(self):
        self.nanoapps_disk()
        st = self.state()
        sel = installer.default_selection(st) + ["Lap", installer.REMOVE + "Pong"]
        steps = build_plan(st, sel, {})
        argvs = [s.argv for s in steps if s.kind == "na"]
        self.assertEqual(argvs[0], ["nabase", "backup"])
        self.assertEqual(argvs[1], ["nabase", "launcher"])
        self.assertIn(["nabase", "remove", "Pong"], argvs)
        self.assertIn(["nabase", "add", "Lap"], argvs)
        self.assertFalse(any(s.root for s in steps))
        self.assertFalse(any(s.kind == "jailbreak" or "install" in s.argv or "cap" in s.argv for s in steps))
        prompt, default = installer.start_prompt(steps)
        self.assertIn("Pong", prompt)
        self.assertFalse(default)

    def test_plan_fresh_copies_disk_files(self):
        st = NanoState(present=True, mount=self.root, fstype="hfs")
        installer.read_base(st, "nanoapps")
        steps = build_plan(st, installer.default_selection(st), {}, disk_files=True)
        self.assertEqual([s.argv for s in steps][:3],
                         [["nabase", "backup"], ["nabase", "disk-files"], ["nabase", "launcher"]])

    def test_nothing_to_do(self):
        self.nanoapps_disk()
        (self.root / "Library/resident.hbapp").write_bytes(nabase.our_slot())
        st = self.state()
        self.assertTrue(st.na)
        self.assertEqual(build_plan(st, installer.default_selection(st), {}), [])

    def test_pocketjs_on_nanoapps(self):
        self.nanoapps_disk()
        st = self.state()
        steps = build_plan(st, ["JS QuickJS"], {})
        js = [s for s in steps if s.argv[:3] == ["nabase", "add", "JS QuickJS"]][0]
        self.assertEqual(js.then, [["nabase", "packages"]])

    def test_untethered_plan_unchanged(self):
        st = self.untethered_state()
        steps = build_plan(st, installer.default_selection(st), {})
        self.assertEqual(steps[-1].argv, ["resident", "install-na", "--skip-wait"])
        self.assertFalse(any(s.kind == "na" for s in steps))

    def untethered_state(self):
        st = self.root / "Apps" / "Startup"
        st.mkdir(parents=True)
        (st / "resident-A.hbapp").write_bytes(b"x")
        s = NanoState(present=True, mount=self.root, fstype="vfat")
        s.loader, s.home, s.lean = installer.read_home(self.root)
        installer.read_base(s)
        return s

    def test_mixed_asks(self):
        self.nanoapps_disk()
        st = self.untethered_state()
        self.assertEqual(st.base, "mixed")
        answers = iter(["1"])
        self.assertFalse(installer.choose_base(st, inp=lambda _: next(answers), out=self.out.append))
        self.assertTrue(st.nanoapps)
        s = NanoState(present=True, mount=self.root, base="mixed")
        answers = iter(["2"])
        installer.choose_base(s, inp=lambda _: next(answers), out=self.out.append)
        self.assertEqual(s.base, "untethered")

    def test_fresh_offers_three_ways(self):
        s = NanoState(present=True, mount=self.root, base="none")
        answers = iter(["q"])
        self.assertIsNone(installer.choose_base(s, inp=lambda _: next(answers), out=self.out.append))
        text = "\n".join(self.out)
        self.assertIn("Check for Update", text)
        self.assertIn("FAT32 only", text)


class CliTests(Base):
    def test_status_and_no_sudo(self):
        from host import cli

        src = Path(cli.__file__).read_text()
        self.assertIn('"nabase": cmd_nabase', src)
        self.nanoapps_disk()
        lines = nabase.status_lines(self.root)
        self.assertIn("base      nanoapps", lines)
        none = nabase.status_lines(None)
        self.assertTrue(none)
        self.assertIn("/Volumes/IPOD", none[0])
        self.assertIn("Disk Mode only if it is not mounted", none[0])
        self.assertIn("mounted iPod volume", src)
        self.assertIn("Disk Mode only if that volume is not mounted yet", src)
        readme = Path(__file__).resolve().parent.parent / "README.md"
        docs = Path(__file__).resolve().parent.parent / "docs" / "NANOAPPS-BASE.md"
        self.assertIn("only if that volume is not mounted yet", readme.read_text())
        self.assertIn("asks for Disk Mode only when that volume is not mounted", docs.read_text())

    def test_command_file(self):
        p = Path(__file__).resolve().parent.parent / "Install nano7kit.command"
        self.assertTrue(p.is_file())
        self.assertTrue(p.stat().st_mode & 0o111)
        self.assertIn("./install", p.read_text())


class OldLayoutGuardTests(Base):
    """./start apps (untethered layout) refuses a NanoApps-base iPod; nabase add is the way."""

    def ops(self):
        from host.fat import MountFat

        return MountFat(self.root)

    def test_refuses_with_our_launcher_and_with_theirs(self):
        from host.apps import refuse_nanoapps_base

        self.nanoapps_disk()
        with self.assertRaises(Refused) as cm:
            refuse_nanoapps_base(self.ops(), "apps add")
        self.assertIn("NanoApps' launcher", str(cm.exception))
        self.assertIn("./start nabase add", str(cm.exception))
        (self.root / "Library/resident.hbapp").write_bytes(nabase.our_slot())
        nabase.mark_base(nabase.Volume(self.root), "nanoapps")
        with self.assertRaises(Refused) as cm:
            refuse_nanoapps_base(self.ops(), "apps add")
        self.assertIn("nano7kit Launcher", str(cm.exception))
        self.assertNotIn("mark untethered", str(cm.exception))

    def test_untethered_still_allowed(self):
        (self.root / "Apps/Startup").mkdir(parents=True)
        (self.root / "Apps/Startup/ab.hbapp").write_bytes(b"ab")
        folder(self.root, "Paint")              # ./start nanoapp copy on an untethered nano
        self.assertIsNone(nabase.old_layout_refusal(self.ops()))
        self.nanoapps_disk(names=())
        self.assertIsNotNone(nabase.old_layout_refusal(self.ops()))
        nabase.mark_base(nabase.Volume(self.root), "untethered")
        self.assertIsNone(nabase.old_layout_refusal(self.ops()))

    def test_apps_add_stops_before_build_or_sudo(self):
        from types import SimpleNamespace

        from host import cli, ports

        self.nanoapps_disk()
        nabase.mark_base(nabase.Volume(self.root), "nanoapps")
        args = SimpleNamespace(apps_cmd="add", names=["Fruit Ninja"], payload="/x/fruitninja.ipa",
                               rom=None, all=False, extras=False, plan=False, dry_run=False)
        with mock.patch.object(cli, "_pick_nano", return_value=object()), \
                mock.patch("host.fat.mounted_root", return_value=self.root), \
                mock.patch.object(ports, "prepare_for_add") as build, \
                mock.patch.object(cli, "reexec_as_root") as sudo:
            for plan in (False, True):
                args.plan = plan
                with self.assertRaises(Refused) as cm:
                    cli.cmd_apps(args)
                self.assertIn("nabase add", str(cm.exception))
            args.apps_cmd = "remove"
            with self.assertRaises(Refused):
                cli.cmd_apps(args)
        build.assert_not_called()
        sudo.assert_not_called()
        self.assertFalse((self.root / "Apps/AllApps-B.pack").exists())
        self.assertFalse((self.root / "Apps/Startup/resident-B.active").exists())


class PaintTwiceTests(Base):
    """'2 Paint folder' and '10 Paint' are one folder: removed once, never a crash."""

    def test_plan_removes_the_folder_once(self):
        self.nanoapps_disk()
        st = NanoState(present=True, mount=self.root, fstype="hfs", size=16_000_000_000)
        installer.read_base(st)
        self.assertTrue(is_on(by_name("Paint folder"), st) and is_on(by_name("Paint"), st))
        sel = [installer.REMOVE + by_name("Paint folder").key, installer.REMOVE + by_name("Paint").key]
        removes = [s.argv for s in build_plan(st, sel, {}) if s.argv[:2] == ["nabase", "remove"]]
        self.assertEqual(removes, [["nabase", "remove", "Paint"]])

    def test_remove_both_names_and_again(self):
        self.nanoapps_disk()
        nabase.backup(self.root, out=self.out.append)
        self.assertEqual(nabase.remove(self.root, ["Paint.nanoapp", "Paint"], out=self.out.append), ["Paint"])
        self.assertFalse((self.root / "Apps/Paint.nanoapp").exists())
        self.assertEqual(nabase.remove(self.root, ["Paint"], out=self.out.append), [])
        self.assertNotIn("Paint", nabase.home_names(self.root))
        self.music_untouched()

    def test_appledouble_vanishing_mid_delete(self):
        """macOS drops ._name with name on FAT/HFS, so rmtree finds ._name gone."""
        self.nanoapps_disk()
        d = self.root / "Apps/Paint.nanoapp"
        (d / "._app.hbapp").write_bytes(b"ad")
        nabase.backup(self.root, out=self.out.append)
        real = os.unlink

        def unlink(path, *a, **kw):
            real(path, *a, **kw)
            if str(path).endswith("._app.hbapp"):
                raise FileNotFoundError(2, "No such file or directory", str(path))

        with mock.patch("os.unlink", unlink):
            self.assertEqual(nabase.remove(self.root, ["Paint"], out=self.out.append), ["Paint"])
        self.assertFalse(d.exists())

    def test_half_deleted_folder_is_finished(self):
        self.nanoapps_disk()
        nabase.backup(self.root, out=self.out.append)
        (self.root / "Apps/Paint.nanoapp/manifest.napp").unlink()   # what the crash left
        nabase.backup(self.root, self.backups / "zz-later", out=self.out.append)   # latest lacks the manifest
        self.assertNotIn("Paint", nabase.folder_apps(self.root))
        self.assertEqual(nabase.remove(self.root, ["Paint"], out=self.out.append), ["Paint"])
        self.assertFalse((self.root / "Apps/Paint.nanoapp").exists())


NA_SRC = os.environ.get("NANO7KIT_NANOAPPS_SRC", "")


@unittest.skipUnless(NA_SRC and (Path(NA_SRC) / "system/disk_bootstrap/build/disk_bootstrap.reloc.elf").is_file(),
                     "set NANO7KIT_NANOAPPS_SRC to a NanoApps checkout with disk_bootstrap built (needs unicorn)")
class NanoAppsLoaderTests(unittest.TestCase):
    """Our launcher file through NanoApps' own compiled checked loader, in the emulator."""

    def setUp(self):
        sys.path.insert(0, str(Path(NA_SRC) / "tools"))
        import test_checked_reloc_loader as t   # noqa: E402
        import wrap_resident_slot as w          # noqa: E402

        self.t, self.w = t, w

    def test_their_wrapper_matches_ours(self):
        hrl = nabase.our_hrl()
        self.assertEqual(self.w.wrap_slot(hrl, nabase.OUR_RESIDENT_VERSION), nabase.our_slot())
        self.assertEqual(self.w.validate_slot(nabase.our_slot()), (hrl, nabase.OUR_RESIDENT_VERSION))

    def test_their_loader_accepts_and_relocates_ours(self):
        t = self.t
        for slot, version in ((nabase.our_slot(), nabase.OUR_RESIDENT_VERSION),
                              (nabase.our_hrl() + nabase.fnv1a32(nabase.our_hrl()).to_bytes(4, "little"), 0)):
            with self.subTest(version=version):
                hrl = nabase.our_hrl()
                entry_off, image, span, align, _ = t.hrl_info(hrl)
                m = t.CheckedLoaderMachine(slot)
                result = m.run()
                base = (t.ARENA + align - 1) & ~(align - 1)
                self.assertEqual(result, base + entry_off | 1)
                self.assertEqual(m.word(t.OUT + 12), span)
                self.assertEqual(m.word(t.OUT + 20), version)
                self.assertEqual(bytes(m.uc.mem_read(base, span)), t.relocated_hrl(hrl, base))


if __name__ == "__main__":
    unittest.main()
