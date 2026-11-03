#!/usr/bin/env python3
import io
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
START = ROOT / "start"


class CliTests(unittest.TestCase):
    def test_help(self):
        r = subprocess.run([sys.executable, str(START), "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("untether", r.stdout)

    def test_doctor(self):
        r = subprocess.run([sys.executable, str(START), "doctor"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        for needle in (
            "MKN52",
            "./install",
            "./start install",
            "--with-bootloader",
            "apps list",
            "restore-tested",
            "lasttrace",
            "roms add",
            "library restore-from-backup",
            "Do not disconnect",
            "apps add Mario --rom",
            "pocketjs packages",
            "./start perf",
            "keep-files",
            "15806623744",
            "docs/BUTTONS.md",
        ):
            self.assertIn(needle, r.stdout)
        self.assertNotIn("\u2014", r.stdout)  # no em dash

    def test_apps_list(self):
        r = subprocess.run(
            [sys.executable, str(START), "apps", "list"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("Calculator", r.stdout)
        self.assertIn("Blocks", r.stdout)
        self.assertIn("hardware-tested", r.stdout)
        self.assertIn("hardware-safe", r.stdout)
        self.assertIn("Paint", r.stdout)
        self.assertIn("compatible", r.stdout)
        self.assertIn("Mario 64", r.stdout)
        self.assertIn("Temple Run", r.stdout)
        self.assertIn("Flappy Bird", r.stdout)
        self.assertIn("Doodle Jump", r.stdout)
        self.assertIn("Fruit Ninja", r.stdout)
        self.assertIn("apps add Mario --rom", r.stdout)
        self.assertIn("restore-tested", r.stdout)
        self.assertNotIn("\u2014", r.stdout)

    def test_apps_add_extras_refused_offline(self):
        r = subprocess.run(
            [sys.executable, str(START), "apps", "add", "--extras"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        blob = r.stdout + r.stderr
        self.assertIn("reboot", blob)
        self.assertIn("Paint", blob)
        self.assertIn("restore-tested", blob)
        self.assertNotIn("\u2014", blob)

    def test_apps_add_two_extras_refused_offline(self):
        r = subprocess.run(
            [sys.executable, str(START), "apps", "add", "Calculator", "Notes"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        blob = r.stdout + r.stderr
        self.assertIn("one extra", blob)
        self.assertIn("Paint", blob)

    def test_apps_add_mario_without_rom_refused_offline(self):
        r = subprocess.run(
            [sys.executable, str(START), "apps", "add", "Mario"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        blob = r.stdout + r.stderr
        self.assertIn("apps add Mario --rom", blob)
        self.assertIn("9bef1128", blob)
        self.assertNotIn("\u2014", blob)

    def test_apps_help_lists_lasttrace(self):
        r = subprocess.run(
            [sys.executable, str(START), "apps", "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("lasttrace", r.stdout)
        self.assertIn("restore-tested", r.stdout)
        self.assertIn("set", r.stdout)
        self.assertIn("cap", r.stdout)
        self.assertIn("yield", r.stdout)

    def test_apps_set_plan_offline(self):
        r = subprocess.run(
            [sys.executable, str(START), "apps", "set", "--plan"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        blob = r.stdout + r.stderr
        self.assertIn("Chess", blob)
        self.assertIn("Screenshot", blob)
        self.assertIn("apps cap enable", blob)
        self.assertIn("apps add Mario --rom", blob)
        self.assertNotIn("\u2014", blob)

    def test_apps_cap_status_offline(self):
        r = subprocess.run(
            [sys.executable, str(START), "apps", "cap"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        blob = r.stdout + r.stderr
        self.assertIn("nogrow", blob)
        self.assertIn("grow_cache", blob)
        self.assertIn("16", blob)

    def test_port_candidates_lists_fruit_ninja(self):
        r = subprocess.run(
            [sys.executable, str(START), "port", "candidates"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        blob = r.stdout
        for needle in (
            "Fruit Ninja",
            "com.Halfbrick.Fruit",
            "Plants vs. Zombies",
            "com.popcap.PvZ",
            "Mortar",
            "cryptid 0",
            "never commit",
            "apps add 'Fruit Ninja'",
            "./start port pvz",
            "ab6a66cd",
        ):
            self.assertIn(needle, blob)
        self.assertNotIn("\u2014", blob)

    def test_verify_release(self):
        r = subprocess.run([sys.executable, str(START), "verify-release"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("passed", r.stdout)

    def test_find_without_device(self):
        r = subprocess.run([sys.executable, str(START), "find"], capture_output=True, text=True)
        # No iPod in CI: non-zero, but must not crash.
        self.assertIn(r.returncode, (0, 1, 2))

    def test_no_em_dash_in_help(self):
        r = subprocess.run([sys.executable, str(START), "--help"], capture_output=True, text=True)
        self.assertNotIn("\u2014", r.stdout)

    def test_help_lists_one_command_paths(self):
        r = subprocess.run([sys.executable, str(START), "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        for word in (
            "untether",
            "scsi-probe",
            "scsi-dump",
            "install-stage",
            "setup",
            "sync",
            "fat32",
            "rescue",
            "apps",
            "theme",
            "bootloader",
            "install-untether",
            "install",
            "roms",
            "library",
            "ports",
            "sdk",
            "pocketjs",
            "fetch",
            "perf",
        ):
            self.assertIn(word, r.stdout)

    def test_parent_flags_work_before_or_after_fat32_flash_rescue(self):
        from host.cli import build_parser

        p = build_parser()
        ns = p.parse_args(
            [
                "--local",
                "--allow-disk-size",
                "64",
                "--confirm-untested-capacity",
                "--i-accept-untested-flasher",
                "--i-accept-2015-refresh",
                "flash",
                "--dry-run",
                "--serial",
                "X",
            ]
        )
        self.assertTrue(ns.local)
        self.assertEqual(ns.allow_disk_size, 64)
        self.assertTrue(ns.i_accept_2015_refresh)
        self.assertEqual(ns.serial, "X")
        after = p.parse_args(
            [
                "fat32",
                "--dry-run",
                "--i-accept-untested-flasher",
                "--i-accept-2015-refresh",
                "--serial",
                "ABOUT",
                "--backup",
                "/tmp/nano-Apple iPod Media-x.img",
                "--backup-sha256",
                "a" * 64,
            ]
        )
        self.assertTrue(after.i_accept_untested_flasher)
        self.assertTrue(after.i_accept_2015_refresh)
        self.assertEqual(after.backup, "/tmp/nano-Apple iPod Media-x.img")
        both = p.parse_args(
            [
                "--i-accept-2015-refresh",
                "rescue",
                "--dry-run",
                "--i-accept-untested-flasher",
                "--serial",
                "Y",
            ]
        )
        self.assertTrue(both.i_accept_2015_refresh)
        self.assertTrue(both.i_accept_untested_flasher)

    def test_sync_skip_wait_does_not_hang(self):
        r = subprocess.run(
            [sys.executable, str(START), "sync", "--skip-wait"],
            capture_output=True,
            text=True,
        )
        self.assertIn(r.returncode, (0, 1, 2), r.stderr + r.stdout)
        self.assertIn("Disk Mode", r.stdout)
        self.assertIn("iTunes_Control", r.stdout)
        self.assertIn("Do not disconnect", r.stdout)
        self.assertIn("Volume Up", r.stdout)
        self.assertIn("Restore", r.stdout)
        self.assertIn("/Apps", r.stdout)
        self.assertNotIn("\u2014", r.stdout)

    def test_fat32_inspect_image(self):
        import tempfile
        from host.layout import build_mbr_winpod

        with tempfile.TemporaryDirectory() as td:
            img = Path(td) / "winpod.img"
            img.write_bytes(build_mbr_winpod(4096, fw_start=63, fw_size=50))
            r = subprocess.run(
                [sys.executable, str(START), "fat32", "--inspect", str(img)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertIn("Winpod", r.stdout)
            self.assertIn("0x00", r.stdout)
            self.assertNotIn("\u2014", r.stdout)

    def test_fat32_inspect_mkn52_path_with_spaces(self):
        import tempfile
        from host.layout import MKN52_HFS_BYTES, write_mkn52_finder_image

        with tempfile.TemporaryDirectory() as td:
            img = Path(td) / "nano-Apple iPod Media-20261028T171800Z.img"
            write_mkn52_finder_image(img, sparse_full_size=False)
            r = subprocess.run(
                [sys.executable, str(START), "fat32", "--inspect", str(img)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertIn("Macpod data volume", r.stdout)
            self.assertIn(str(MKN52_HFS_BYTES), r.stdout)
            self.assertIn("4096", r.stdout)
            self.assertNotIn("1974763520", r.stdout)
            self.assertIn("Apple iPod Media", r.stdout)

    def test_typed_confirm_is_a_global_flag_before_fat32(self):
        from host.cli import build_parser

        p = build_parser()
        ns = p.parse_args(
            [
                "--i-accept-untested-flasher",
                "--i-accept-2015-refresh",
                "--typed-confirm",
                "FAT32",
                "fat32",
                "--run",
                "--format-only",
                "--serial",
                "XYZ123ABC456",
                "--backup",
                "/tmp/nano.img",
                "--backup-sha256",
                "a" * 64,
            ]
        )
        self.assertEqual(ns.typed_confirm, "FAT32")
        self.assertTrue(ns.format_only)
        self.assertTrue(ns.run)
        self.assertEqual(ns.serial, "XYZ123ABC456")
        with self.assertRaises(SystemExit):
            p.parse_args(
                [
                    "fat32",
                    "--run",
                    "--format-only",
                    "--typed-confirm",
                    "FAT32",
                    "--serial",
                    "X",
                ]
            )

    def test_fat32_sudo_argv_always_includes_resolved_backup(self):
        from argparse import Namespace
        from host.cli import _fat32_forward_argv

        args = Namespace(
            allow_disk_size=None,
            confirm_untested_capacity=False,
            yes=False,
            i_accept_brick_risk=False,
            i_accept_untested_flasher=True,
            i_accept_2015_refresh=True,
            local=False,
            json=False,
            backup=None,
            replace=False,
            format_only=False,
            device=None,
            typed_confirm="FAT32",
            i_accept_untested_firmware=False,
            i_accept_themed_untether=False,
        )
        argv = _fat32_forward_argv(
            args,
            backup=Path("/Users/you/nano7-backups/nano.img"),
            bhash="a" * 64,
            serial="MKN52",
            dry=True,
            mse=Path("/tmp/Firmware-stock-39A10023.MSE"),
            skip_backup_file=False,
        )
        self.assertIn("fat32", argv)
        self.assertLess(argv.index("--i-accept-2015-refresh"), argv.index("fat32"))
        self.assertLess(argv.index("--typed-confirm"), argv.index("fat32"))
        self.assertEqual(argv[argv.index("--typed-confirm") + 1], "FAT32")
        self.assertIn("--backup", argv)
        self.assertEqual(argv[argv.index("--backup") + 1], "/Users/you/nano7-backups/nano.img")
        self.assertEqual(argv[argv.index("--backup-sha256") + 1], "a" * 64)
        self.assertNotIn("--skip-backup-file", argv)
        self.assertIn("--dry-run", argv)
        self.assertIn("--i-accept-2015-refresh", argv)
        args.format_only = True
        fmt = _fat32_forward_argv(
            args,
            backup=Path("/Users/you/nano7-backups/nano.img"),
            bhash="a" * 64,
            serial="MKN52",
            dry=False,
            mse=None,
            skip_backup_file=False,
        )
        self.assertIn("--format-only", fmt)
        self.assertIn("--run", fmt)
        self.assertNotIn("--mse", fmt)
        args.scsi_dump = "/tmp/nano7-scsi-dump"
        dumped = _fat32_forward_argv(
            args,
            backup=Path("/Users/you/nano7-backups/nano.img"),
            bhash="a" * 64,
            serial="MKN52",
            dry=True,
            mse=None,
            skip_backup_file=False,
        )
        self.assertIn("--scsi-dump", dumped)
        self.assertEqual(dumped[dumped.index("--scsi-dump") + 1], "/tmp/nano7-scsi-dump")
        self.assertIn("--dry-run", dumped)
        ssh = _fat32_forward_argv(
            args,
            backup=Path("/Users/you/nano7-backups/nano.img"),
            bhash="a" * 64,
            serial="MKN52",
            dry=False,
            mse=None,
            skip_backup_file=True,
        )
        self.assertIn("--skip-backup-file", ssh)
        self.assertIn("--run", ssh)

    def test_scsi_dump_refuses_run_replace_format_only(self):
        r = subprocess.run(
            [
                sys.executable,
                str(START),
                "fat32",
                "--scsi-dump",
                "/tmp/nano7-scsi-dump",
                "--run",
                "--serial",
                "X",
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("read-only", (r.stderr or "") + (r.stdout or ""))
        r2 = subprocess.run(
            [
                sys.executable,
                str(START),
                "fat32",
                "--scsi-dump",
                "/tmp/nano7-scsi-dump",
                "--replace",
                "--serial",
                "X",
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(r2.returncode, 0)
        self.assertIn("replace", ((r2.stderr or "") + (r2.stdout or "")).lower())

    def test_typed_confirm_untether_is_global(self):
        from host.cli import build_parser

        p = build_parser()
        ns = p.parse_args(
            [
                "--i-accept-untested-flasher",
                "--i-accept-2015-refresh",
                "--typed-confirm",
                "UNTETHER",
                "untether",
                "--run",
                "--serial",
                "XYZ123ABC456",
            ]
        )
        self.assertEqual(ns.typed_confirm, "UNTETHER")
        self.assertTrue(ns.run)
        self.assertEqual(ns.serial, "XYZ123ABC456")
        with self.assertRaises(SystemExit):
            p.parse_args(
                [
                    "untether",
                    "--run",
                    "--typed-confirm",
                    "UNTETHER",
                    "--serial",
                    "X",
                ]
            )

    def test_untether_replace_is_refused(self):
        r = subprocess.run(
            [sys.executable, str(START), "--local", "untether", "--replace"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(r.returncode, 0)
        text = (r.stderr or "") + (r.stdout or "")
        self.assertIn("REFUSED", text)
        self.assertIn("--replace", text)
        self.assertNotIn("\u2014", text)

    def test_untether_help_lists_guarded_flags(self):
        r = subprocess.run(
            [sys.executable, str(START), "untether", "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--check-only", r.stdout)
        self.assertIn("--verify", r.stdout)
        self.assertIn("UNTETHER", r.stdout)
        self.assertNotIn("\u2014", r.stdout)
        top = subprocess.run(
            [sys.executable, str(START), "--help"], capture_output=True, text=True
        )
        self.assertIn("no --replace", top.stdout)

    def test_port_pvz_offline(self):
        r = subprocess.run(
            [sys.executable, str(START), "port", "pvz"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        blob = r.stdout
        for needle in (
            "FMOD",
            "UIKit",
            "MediaPlayer",
            "SexyApp",
            "slot B",
            "ab6a66cd",
            "com.popcap.PvZ",
            "research only",
            "No apps add",
        ):
            self.assertIn(needle, blob)
        self.assertNotIn("apps add 'PvZ'", blob)
        self.assertNotIn("\u2014", blob)

    def test_port_fruit_offline(self):
        r = subprocess.run(
            [sys.executable, str(START), "port", "fruit"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        blob = r.stdout
        self.assertIn("OpenAL", blob)
        self.assertIn("fileArcive.bin", blob)
        self.assertIn("silent", blob)
        self.assertNotIn("\u2014", blob)


if __name__ == "__main__":
    unittest.main()
