"""./install: menu logic, the plan it builds, and the bring-your-own-file checks."""

import hashlib
import io
import plistlib
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host import buttons, installer  # noqa: E402
from host.installer import (  # noqa: E402
    ITEMS,
    MacInfo,
    NanoState,
    build_plan,
    by_name,
    check_file,
    classify,
    clean_path,
    failure_lines,
    home_count,
    numbered,
    read_home,
    render_menu,
    render_plan,
    scan_drop_dir,
    toggle,
    wait_disk_mode,
)

INTEL = MacInfo("Darwin", "x86_64", False, "13.5.1")
SILICON = MacInfo("Darwin", "arm64", False, "15.1")


def num(key):
    for i, item in numbered().items():
        if item.key == key:
            return str(i)
    raise KeyError(key)


def fresh():
    return NanoState(present=True, fstype="vfat", size=15806623744)


def installed(*extra, lean=False):
    return NanoState(present=True, fstype="vfat", size=15806623744, loader=True,
                     home=["Blocks", "Game Boy", "Probe", *extra], lean=lean)


def make_ipa(path: Path, bundle: str, version: str, app="Game.app"):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(f"Payload/{app}/Info.plist",
                   plistlib.dumps({"CFBundleIdentifier": bundle, "CFBundleShortVersionString": version}))
        z.writestr(f"Payload/{app}/Game", b"\0" * 16)


class MenuTests(unittest.TestCase):
    def test_every_item_says_what_to_supply(self):
        for item in ITEMS:
            if item.kind == "none":
                self.assertEqual(item.needs, "", item.key)
            else:
                self.assertTrue(item.needs.startswith("your "), item.key)

    def test_groups_and_order(self):
        groups = [i.group for i in ITEMS]
        self.assertEqual(groups, sorted(groups, key=["tools", "apps", "games", "pocketjs", "more"].index))
        self.assertEqual({i.key for i in ITEMS if i.group == "pocketjs"}, {"JS QuickJS", "JS Hero", "JS Samples", "Free RAM"})
        for key in ("Mario 64", "Temple Run", "Fruit Ninja", "PvZ Portable"):
            self.assertEqual(by_name(key).group, "games")
            self.assertTrue(by_name(key).tested)
        self.assertEqual({i.key for i in ITEMS if not i.tested},
                         {"Mario Kart 64", "Flappy Bird", "Doodle Jump", "Angry Birds"})
        self.assertEqual(by_name("Paint folder").name, "Paint.nanoapp")
        self.assertEqual(by_name("Free RAM folder").name, "Free RAM.nanoapp")

    def test_toggle_on_off_and_bad_input(self):
        sel, msg = toggle(f"{num('Notes')} {num('Chess')}", [], fresh())
        self.assertEqual(sel, ["Notes", "Chess"])
        self.assertEqual(msg, "")
        sel, msg = toggle(num("Notes"), sel, fresh())
        self.assertEqual(sel, ["Chess"])
        sel, msg = toggle("99 abc", sel, fresh())
        self.assertEqual(sel, ["Chess"])
        self.assertIn("not on the list", msg)

    def test_typing_an_on_item_marks_it_for_removal(self):
        st = installed("JS Hero", "JS Samples")
        st.na = True
        sel, msg = toggle(f"{num('Launcher')} {num('Screenshot')} {num('JS Hero')} {num('JS Samples')}", [], st)
        self.assertEqual(sel, ["Screenshot", "remove:JS Hero", "remove:JS Samples"])
        self.assertIn("Launcher: can't be removed here", msg)
        self.assertIn("./start resident restore", msg)
        menu = render_menu(INTEL, st, sel)
        self.assertIn("[remove] JS Hero", menu)
        self.assertIn("[remove] JS Samples", menu)
        self.assertIn("[on]     Launcher", menu)
        self.assertIn("Type a number again to undo it.", menu)
        self.assertIn("Type its number to remove it", menu)
        sel2, _ = toggle(num("JS Samples"), sel, st)
        self.assertEqual(sel2, ["Screenshot", "remove:JS Hero"])

    def test_removal_plan_and_prompt(self):
        st = installed("JS Hero", "JS Samples", "Notes")
        st.na = True
        sel, _ = toggle(f"{num('Chess')} {num('JS Hero')} {num('JS Samples')}", [], st)
        steps = build_plan(st, sel, {})
        self.assertEqual([s.argv for s in steps if s.kind == "remove"],
                         [["apps", "remove", "JS Hero", "--skip-wait"],
                          ["apps", "remove", "JS Samples", "--skip-wait"]])
        self.assertEqual([s.title for s in steps][-3:], ["Remove JS Hero", "Remove JS Samples", "Add Chess"])
        self.assertIn("Remove JS Hero  (deletes it from the iPod)", render_plan(steps))
        prompt, default = installer.start_prompt(steps)
        self.assertEqual(prompt, "This removes from your iPod: JS Hero, JS Samples. Start?")
        self.assertFalse(default)
        self.assertEqual(installer.start_prompt(build_plan(st, ["Chess"], {})), ("Start?", True))

    def test_removal_frees_home_slots(self):
        apps = [i.key for i in ITEMS if i.group == "apps"][:13]
        st = installed("Notes")
        full, _ = toggle(" ".join(num(k) for k in apps if k != "Notes"), [], st)
        self.assertEqual(home_count(st, full), 16)
        _, msg = toggle(num("Mario 64"), full, st)
        self.assertIn("16 apps at most", msg)
        sel, msg = toggle(f"{num('Notes')} {num('Mario 64')}", full, st)
        self.assertEqual(msg, "")
        self.assertIn("Mario 64", sel)
        self.assertEqual(home_count(st, sel), 16)

    def test_launcher_and_base_apps_never_removed(self):
        st = installed()
        st.na = True
        steps = build_plan(st, ["remove:Launcher", "remove:Blocks", "remove:Probe"], {})
        self.assertEqual(steps, [])
        self.assertFalse(any(i.name in installer.BASE_APPS for i in ITEMS))

    def test_remove_steps_wait_for_disk_mode(self):
        st = installed("Notes")
        st.na = True
        out = []
        with mock.patch.object(installer, "detect_nano", return_value=st), \
                mock.patch.object(installer, "detect_mac", return_value=INTEL), \
                mock.patch("host.upstream.ensure"), \
                mock.patch.object(installer, "ask", side_effect=[num("Notes"), "", ""]), \
                mock.patch("builtins.print", side_effect=lambda *a, **k: out.append(" ".join(map(str, a)))):
            self.assertEqual(installer.main([]), 0)
        text = "\n".join(out)
        self.assertIn("Remove Notes  (deletes it from the iPod)", text)
        self.assertIn("Nothing changed.", text)

    def test_toggle_commas(self):
        sel, _ = toggle(f"{num('Notes')},{num('Paint')}", [], fresh())
        self.assertEqual(sel, ["Notes", "Paint"])

    def test_already_on_is_not_added_again(self):
        sel, msg = toggle(num("Calculator"), [], installed("Calculator"))
        self.assertEqual(sel, ["remove:Calculator"])
        self.assertEqual(build_plan(installed("Calculator"), ["Calculator"], {}), [])

    def test_home_limit(self):
        apps = [i.key for i in ITEMS if i.group == "apps"][:13]
        sel, _ = toggle(" ".join(num(k) for k in apps), [], fresh())
        self.assertEqual(home_count(fresh(), sel), 16)
        sel2, msg = toggle(num("Mario 64"), sel, fresh())
        self.assertEqual(sel2, sel)
        self.assertIn("16 apps at most", msg)

    def test_menu_text(self):
        text = render_menu(INTEL, installed("Notes"), ["Mario 64"])
        self.assertIn("Intel Mac, macOS 13.5.1", text)
        self.assertIn("[on]     Notes", text)
        self.assertIn("[x]      Mario 64         your Super Mario 64 (USA) .z64 ROM", text)
        self.assertIn("\nMore NanoApps\n", text)
        self.assertIn("Paint folder     same app as Paint below", text)
        self.assertIn("Free RAM folder  same app as Free RAM below", text)
        untested = [l.split()[3] for l in text.splitlines() if l.endswith("(untested)")]
        self.assertEqual(untested, ["Mario", "Angry", "Flappy", "Doodle"])
        self.assertIn("Always included: Blocks, Game Boy, Probe.", text)
        self.assertIn("Home apps: 5 of 16", text)
        self.assertNotIn("\u2014", text)
        self.assertIn("Apple silicon Mac", render_menu(SILICON, fresh(), []))
        for line in text.splitlines():
            self.assertEqual(line, line.rstrip())

    def test_rosetta_label(self):
        self.assertIn("Rosetta", MacInfo("Darwin", "x86_64", True, "15.1").label())


class PlanTests(unittest.TestCase):
    def test_fresh_nano_gets_tools_loader_then_apps(self):
        steps = build_plan(fresh(), ["Notes"], {}, serial="ABC123DEF456")
        self.assertEqual([s.kind for s in steps], ["tools", "jailbreak", "disk"])
        jb = steps[1].argv
        self.assertEqual(jb[:4], ["--typed-confirm", "UNTETHER", "install", "--run"])
        self.assertIn("ABC123DEF456", jb)
        for bad in ("--replace", "--yes", "--i-accept-brick-risk", "--with-bootloader", "rescue", "fat32"):
            self.assertNotIn(bad, jb)

    def test_installed_nano_plain_apps_need_no_tools(self):
        steps = build_plan(installed(), ["Lap", "Free RAM"], {})   # ours ship prebuilt
        self.assertEqual([s.argv for s in steps], [
            ["apps", "add", "Lap", "--skip-wait"],
            ["apps", "add", "Free RAM", "--skip-wait"],
        ])

    def test_nanoapps_apps_get_the_compiler_first(self):
        from host.installer import prebuilt

        with mock.patch("host.installer.prebuilt", lambda n: n == "Lap"):
            steps = build_plan(installed(), ["Notes", "Golf"], {})
        self.assertEqual(steps[0].argv, ["deps"])
        self.assertEqual([s.argv[2] for s in steps[1:]], ["Notes", "Golf"])
        self.assertTrue(prebuilt("Lap"))
        self.assertIn("Golf", [i.key for i in ITEMS if i.group == "more"])

    def test_games_pass_the_file_and_need_tools(self):
        files = {"Mario 64": Path("/x/sm64.z64"), "Fruit Ninja": Path("/x/fn.ipa"), "PvZ Portable": Path("/x/pvz")}
        steps = build_plan(installed(), ["PvZ Portable", "Mario 64", "Fruit Ninja"], files)
        self.assertEqual(steps[0].kind, "tools")
        argvs = [s.argv for s in steps[1:]]
        self.assertEqual(argvs[0], ["apps", "add", "Mario 64", "--skip-wait", "--rom", "/x/sm64.z64"])
        self.assertEqual(argvs[1], ["apps", "add", "Fruit Ninja", "--skip-wait", "--payload", "/x/fn.ipa"])
        self.assertEqual(argvs[2], ["apps", "add", "PvZ Portable", "--skip-wait", "--payload", "/x/pvz"])

    def test_pocketjs_adds_packages_in_the_same_session(self):
        steps = build_plan(installed(), ["JS QuickJS", "JS Hero"], {})
        self.assertEqual(steps[0].then, [["pocketjs", "packages", "--skip-wait"]])
        self.assertEqual(steps[1].then, [])

    def test_more_than_eight_adds_cap_lean_once(self):
        sel = ["Calculator", "Notes", "Chess", "Books", "Wallet", "Screenshot"]
        steps = build_plan(installed(), sel, {})
        argvs = [s.argv for s in steps]
        self.assertLess(argvs.index(["apps", "cap", "lean", "--skip-wait"]),
                        min(i for i, a in enumerate(argvs) if a[:2] == ["apps", "add"]))
        self.assertEqual(sum(1 for s in steps if s.argv[:2] == ["apps", "cap"]), 1)
        steps = build_plan(installed(lean=True), sel, {})
        self.assertFalse(any(s.argv[:2] == ["apps", "cap"] for s in steps))
        steps = build_plan(installed(), sel[:5], {})
        self.assertFalse(any(s.argv[:2] == ["apps", "cap"] for s in steps))

    def test_launcher_ticked_by_default_and_plain_label(self):
        from host.installer import default_selection

        self.assertEqual(default_selection(fresh()), ["Launcher"])
        self.assertEqual(default_selection(installed()), ["Launcher"])
        st = installed()
        st.na = True
        self.assertEqual(default_selection(st), [])
        menu = render_menu(INTEL, installed(), default_selection(installed()))
        self.assertIn("[x]      Launcher", menu)
        self.assertNotIn("NA Heavy", menu)
        self.assertIn("[on]     Launcher", render_menu(INTEL, st, []))
        sel, msg = toggle(num("Launcher"), [], st)
        self.assertEqual(sel, [])
        self.assertIn("can't be removed here", msg)

    def test_launcher_never_runs_cap_lean(self):
        sel = ["Launcher", "Calculator", "Notes", "Chess", "Books", "Wallet", "Screenshot"]
        for st in (fresh(), installed()):
            steps = build_plan(st, sel, {}, serial="ABC123DEF456")
            argvs = [s.argv for s in steps]
            self.assertFalse(any(a[:2] == ["apps", "cap"] for a in argvs), argvs)
            na = argvs.index(["resident", "install-na", "--skip-wait"])
            self.assertLess(na, min(i for i, a in enumerate(argvs) if a[:2] == ["apps", "add"]))
        st = installed()
        st.na = True
        steps = build_plan(st, sel[1:], {})
        self.assertFalse(any(s.argv[:2] in (["apps", "cap"], ["resident", "install-na"]) for s in steps))

    def test_order_apps_games_pocketjs(self):
        steps = build_plan(installed(), ["JS Hero", "Mario 64", "Notes"], {"Mario 64": Path("/r.z64")})
        self.assertEqual([s.check for s in steps if s.kind == "disk"], ["Notes", "Mario 64", "JS Hero"])

    def test_already_installed_is_skipped(self):
        self.assertEqual(build_plan(installed("Notes"), ["Notes"], {}), [])

    def test_plan_text(self):
        text = render_plan(build_plan(fresh(), ["Notes"], {}, serial="X"))
        self.assertIn("1. Check build tools", text)
        self.assertIn("writes firmware, after a full backup", text)
        self.assertNotIn("\u2014", text)

    def test_never_restore_or_replace(self):
        sel = [i.key for i in ITEMS][:12]
        files = {i.key: Path("/f") for i in ITEMS if i.kind != "none"}
        for state in (fresh(), installed()):
            for step in build_plan(state, sel, files, serial="S"):
                for part in [step.argv, *step.then]:
                    for bad in ("--replace", "rescue", "fat32", "--full", "--i-accept-brick-risk", "bootloader"):
                        self.assertNotIn(bad, part)


class FileCheckTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_rom_checksum(self):
        rom = self.dir / "sm64.z64"
        rom.write_bytes(b"pretend rom")
        ok, msg = check_file(by_name("Mario 64"), rom)
        self.assertFalse(ok)
        self.assertIn("checksum differs", msg)
        with mock.patch.object(installer, "SM64_SHA1", hashlib.sha1(b"pretend rom").hexdigest()):
            ok, msg = check_file(by_name("Mario 64"), rom)
        self.assertTrue(ok, msg)
        ok, msg = check_file(by_name("Mario 64"), self.dir)
        self.assertFalse(ok)

    def test_mario_kart_uses_md5(self):
        rom = self.dir / "mk.z64"
        rom.write_bytes(b"kart")
        with mock.patch.object(installer, "MK64_MD5", hashlib.md5(b"kart").hexdigest()):
            self.assertTrue(check_file(by_name("Mario Kart 64"), rom)[0])

    def test_ipa_bundle_and_version(self):
        good = self.dir / "tr.ipa"
        make_ipa(good, "com.imangi.templerun", "1.0")
        self.assertTrue(check_file(by_name("Temple Run"), good)[0])
        newer = self.dir / "tr2.ipa"
        make_ipa(newer, "com.imangi.templerun", "1.6")
        ok, msg = check_file(by_name("Temple Run"), newer)
        self.assertFalse(ok)
        self.assertIn("needs 1.0", msg)
        other = self.dir / "fn.ipa"
        make_ipa(other, "com.imangi.templerun", "1.0")
        ok, msg = check_file(by_name("Fruit Ninja"), other)
        self.assertFalse(ok)
        self.assertIn("not Fruit Ninja", msg)
        junk = self.dir / "junk.ipa"
        junk.write_bytes(b"not a zip")
        self.assertFalse(check_file(by_name("Fruit Ninja"), junk)[0])

    def test_unpacked_app(self):
        app = self.dir / "Fruit.app"
        app.mkdir()
        (app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.Halfbrick.Fruit", "CFBundleVersion": "1.0"}))
        self.assertTrue(check_file(by_name("Fruit Ninja"), app)[0])

    def test_pvz_folder(self):
        game = self.dir / "pvz"
        game.mkdir()
        ok, msg = check_file(by_name("PvZ Portable"), game)
        self.assertFalse(ok)
        (game / "main.pak").write_bytes(b"pak")
        (game / "properties").mkdir()
        self.assertIn("checksum differs", check_file(by_name("PvZ Portable"), game)[1])
        with mock.patch.object(installer, "PVZ_PAK_SHA256", hashlib.sha256(b"pak").hexdigest()):
            self.assertTrue(check_file(by_name("PvZ Portable"), game)[0])

    def test_missing_file(self):
        ok, msg = check_file(by_name("Mario 64"), self.dir / "nope.z64")
        self.assertFalse(ok)
        self.assertIn("not found", msg)

    def test_drop_folder_scan(self):
        (self.dir / "random.z64").write_bytes(b"other")
        (self.dir / "mine.z64").write_bytes(b"mario")
        make_ipa(self.dir / "fn.ipa", "com.Halfbrick.Fruit", "1.0")
        with mock.patch.object(installer, "SM64_SHA1", hashlib.sha1(b"mario").hexdigest()):
            self.assertEqual(scan_drop_dir(by_name("Mario 64"), self.dir).name, "mine.z64")
        self.assertEqual(scan_drop_dir(by_name("Fruit Ninja"), self.dir).name, "fn.ipa")
        self.assertIsNone(scan_drop_dir(by_name("Temple Run"), self.dir))
        self.assertIsNone(scan_drop_dir(by_name("Temple Run"), self.dir / "missing"))

    def test_clean_path_from_drag_and_drop(self):
        self.assertEqual(clean_path("/Users/you/My\\ Games/sm64.z64 "), "/Users/you/My Games/sm64.z64")
        self.assertEqual(clean_path("'/Users/you/My Games/a.ipa'"), "/Users/you/My Games/a.ipa")
        self.assertEqual(clean_path(""), "")
        self.assertTrue(clean_path("~/x.z64").endswith("/x.z64"))

    def test_get_file_prompts_until_ok_or_skip(self):
        out = []
        with mock.patch.object(installer, "DROP_DIR", self.dir / "none"):
            answers = iter([str(self.dir / "nope.z64"), ""])
            got = installer.get_file(by_name("Mario 64"), inp=lambda _p: next(answers), out=out.append)
        self.assertIsNone(got)
        self.assertTrue(any("Skipping Mario 64" in o for o in out))


class RunTests(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(classify("  Waiting for USB DFU (Apple VID 05ac, PID 1234)..."), "__dfu__")
        self.assertIn("Recovery mode found", classify("  DFU device seen."))
        self.assertIsNone(classify("    volume /Volumes/IPOD (8 root entries)"))

    def test_failure_lines(self):
        lines = ["a\n", "REFUSED: backup missing\n", "b\n"]
        self.assertEqual(failure_lines(lines), ["REFUSED: backup missing"])
        self.assertEqual(failure_lines(["x\n", "y\n"]), ["x", "y"])

    def test_runner_argv_uses_sudo_and_flags(self):
        r = installer.Runner(verbose=False, flags=["--i-accept-2015-refresh"])
        with mock.patch("os.geteuid", return_value=501):
            argv = r.argv(["apps", "add", "Notes"], True)
        self.assertEqual(argv[:2], ["sudo", "-E"])
        self.assertIn("--i-accept-2015-refresh", argv)
        self.assertEqual(argv[-3:], ["apps", "add", "Notes"])
        with mock.patch("os.geteuid", return_value=501):
            self.assertNotEqual(r.argv(["deps"], False)[0], "sudo")

    def test_wait_disk_mode(self):
        out = []
        seen = iter([NanoState(), NanoState(present=True, mount=Path("/Volumes/IPOD"))])
        answers = iter(["", ""])
        res = wait_disk_mode(lambda _p: next(answers), out.append, detect=lambda: next(seen))
        self.assertEqual(res, "")
        self.assertTrue(any("can't see the nano" in o for o in out))
        self.assertEqual(wait_disk_mode(lambda _p: "r", out.append, detect=NanoState), "r")
        self.assertEqual(wait_disk_mode(lambda _p: "s", out.append, detect=NanoState), "s")

    def test_read_home(self):
        from host.apps import tested_pack_bytes

        with tempfile.TemporaryDirectory() as td:
            mnt = Path(td)
            (mnt / "Apps" / "Startup").mkdir(parents=True)
            self.assertEqual(read_home(mnt), (False, [], False))
            (mnt / "Apps" / "Startup" / "resident-A.hbapp").write_bytes(b"x")
            (mnt / "Apps" / "AllApps.pack").write_bytes(tested_pack_bytes())
            loader, names, lean = read_home(mnt)
            self.assertTrue(loader)
            self.assertEqual(sorted(names), ["Blocks", "Game Boy", "Probe"])
            self.assertFalse(lean)
            (mnt / "Apps" / "Startup" / "resident-lean.active").write_bytes(b"")
            self.assertTrue(read_home(mnt)[2])


class ButtonTextTests(unittest.TestCase):
    def test_dfu_wording(self):
        t = buttons.text("dfu")
        self.assertIn("Sleep/Wake (top) and Home", t)
        self.assertIn("Apple logo appears, then the screen goes black", t)
        self.assertIn('say "nano"', t)
        self.assertIn("Let go of both buttons", t)
        self.assertNotIn("ten seconds", t)

    def test_disk_mode_wording(self):
        t = buttons.text("disk")
        self.assertIn("As soon as the Apple logo appears, quickly switch to holding Volume Up and Volume Down", t)
        self.assertIn('"Do not disconnect"', t)
        self.assertNotIn("goes dark", t)

    def test_engine_uses_the_same_text(self):
        from host import sync

        buf = io.StringIO()
        with mock.patch("sys.stdout", buf), mock.patch.dict("os.environ", {"NANO7KIT_VERBOSE": ""}):
            sync.print_disk_mode_steps()
        self.assertIn(buttons.DISK_MODE[1], buf.getvalue())
        self.assertNotIn("Library.itdb", buf.getvalue())
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf), mock.patch.dict("os.environ", {"NANO7KIT_VERBOSE": "1"}):
            sync.print_disk_mode_steps()
        self.assertIn("Library.itdb", buf.getvalue())

    def test_no_old_wording_left_in_engine(self):
        root = Path(__file__).resolve().parent.parent / "host"
        for p in root.glob("*.py"):
            text = p.read_text()
            for old in ("Release both immediately", "until the screen goes dark", "about ten seconds"):
                self.assertNotIn(old, text, p.name)


if __name__ == "__main__":
    unittest.main()
