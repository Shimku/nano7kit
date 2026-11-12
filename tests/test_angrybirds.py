#!/usr/bin/env python3
"""Angry Birds (olievans123/nano-angrybirds) from the user's 1.0 ipa. Host only."""
import plistlib
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host import angrybirds as ab
from host import angrybirds_patch as ap
from host.app_catalog import by_name, reset_catalog_cache, resolve_name
from host.compat import Refused

ROOT = Path(__file__).resolve().parent.parent
PINNED = ROOT / "build" / "ports" / "angrybirds"


def make_app(td: Path, logic: bytes = b"\x1bLuaQ fake", bundle=ab.BUNDLE_ID, version="1.0") -> Path:
    app = td / "Payload" / "AngryBirds.app"
    (app / "data").mkdir(parents=True)
    (app / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleIdentifier": bundle, "CFBundleVersion": version, "CFBundleExecutable": "AngryBirds"})
    )
    (app / "AngryBirds").write_bytes(b"\xce\xfa\xed\xfe" + b"\0" * 60)
    (app / "data" / "gamelogic.lua").write_bytes(logic)
    return app


class PayloadTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.dir = Path(self.td.name)

    def test_right_script_accepted(self):
        app = make_app(self.dir, b"the 1.0 script")
        digest = ab._sha256(app / "data" / "gamelogic.lua")
        with mock.patch.object(ab, "GAMELOGIC_SHA256", digest):
            facts = ab.assert_payload(app)
        self.assertTrue(facts["ok"])
        self.assertEqual(facts["version"], "1.0")
        self.assertEqual(facts["data"].resolve(), (app / "data").resolve())

    def test_other_script_refused(self):
        app = make_app(self.dir, b"a later version")
        with self.assertRaises(Refused) as ctx:
            ab.assert_payload(app)
        self.assertIn("gamelogic.lua", str(ctx.exception))
        self.assertIn("nothing was built", str(ctx.exception))

    def test_missing_script_refused(self):
        app = make_app(self.dir)
        (app / "data" / "gamelogic.lua").unlink()
        with self.assertRaises(Refused):
            ab.assert_payload(app)

    def test_wrong_bundle_refused(self):
        app = make_app(self.dir, bundle="com.rovio.angrybirdsrio")
        with self.assertRaises(Refused) as ctx:
            ab.assert_payload(app)
        self.assertIn("com.clickgamer.AngryBirds", str(ctx.exception))


def _synthetic_repo(root: Path) -> Path:
    """Files holding exactly the anchors nano7kit edits, nothing else of his."""
    nano = root / "port" / "nano"
    (nano / "app").mkdir(parents=True)
    (root / "port" / "src").mkdir(parents=True)
    for rel, edits in ap.EDITS.items():
        body = "head\n" + "".join(old + "mid\n" for old, _ in edits) + "tail\n"
        (root / rel).write_text(body, encoding="utf-8")
    return root


class PatchTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.repo = _synthetic_repo(Path(self.td.name))

    def read(self, rel):
        return (self.repo / rel).read_text(encoding="utf-8")

    def test_apply_once_and_refuse_twice(self):
        done = ap.apply(self.repo, restore=False)
        self.assertEqual(sorted(done), sorted(ap.EDITS))
        for rel in ap.EDITS:
            if rel.endswith("Makefile"):
                continue
            self.assertIn(ap.MARK, self.read(rel))
        with self.assertRaises(Refused):
            ap.apply(self.repo, restore=False)

    def test_moved_anchor_stops_the_build(self):
        path = self.repo / "port/nano/nano_platform.c"
        path.write_text(path.read_text().replace("load_manifest();", "load_manifest(1);"))
        with self.assertRaises(Refused) as ctx:
            ap.apply(self.repo, restore=False)
        self.assertIn("no longer matches", str(ctx.exception))

    def test_no_malloc_before_the_gl_sweep(self):
        """The arena is made by the first malloc; leftovers must be deleted before it."""
        ap.apply(self.repo, restore=False)
        e = self.read("port/nano/app/ab_entry.c")
        self.assertNotIn('port_crumb("entry"', e)
        self.assertIn("n7_entry_free = hb_os_heap_free();", e)
        # after the FPU is attached (n7_ab.c is ARM + VFP), before the "fpu" breadcrumb's malloc
        self.assertLess(e.index("hb_fpu_attach_current_task(&st);"), e.index("n7_ab_early();"))
        self.assertIn("if (r >= 0) { extern void n7_ab_early(void); n7_ab_early(); }", e)
        glue = (ROOT / "games/angrybirds/nano/n7_ab.c").read_text()
        early = glue[glue.index("void n7_ab_early(void)"):glue.index("static void memory_notice")]
        for word in ("malloc", "memalign", "printf", "calloc"):
            self.assertNotIn(word, early)

    def test_preflight_budget_and_screens(self):
        ap.apply(self.repo, restore=False)
        p = self.read("port/nano/nano_platform.c")
        self.assertIn('#include "n7_ab.h"', p)
        self.assertLess(p.index("n7_ab_preflight()"), p.index("load_manifest();"))
        self.assertIn("if (n7_ab_preflight()) { port_log_flush(LOG_PATH); sDead = 1; return; }", p)
        self.assertIn("assets_set_texture_budget(n7_ab_texture_budget(TEXTURE_BUDGET));", p)
        self.assertNotIn("glClearColor(0.6f", p)
        self.assertEqual(p.count("n7_ab_dead_screen(w, h);"), 2)
        self.assertIn("if (game_failed()) { n7_ab_dead_screen(w, h); return; }", p)
        self.assertLess(p.index("n7_ab_after_init(rc);"), p.index("port_log_flush(LOG_PATH);\nmid"))
        self.assertLess(p.index("keep_awake();"), p.index("n7_ab_tick();"))
        g = self.read("port/src/gfx.c")
        self.assertLess(g.index("glGenTextures(1, &name);"), g.index("n7_ab_gl_name(name);"))
        self.assertIn("#ifdef AB_NANO", g)
        self.assertIn("notice gl_left n7_ab", self.read("port/nano/Makefile"))

    def test_own_files_copied(self):
        ap.apply(self.repo, restore=False)
        for _, name in ap.OWN_FILES:
            self.assertTrue((self.repo / "port/nano" / name).is_file(), name)
        g = self.read("port/nano/gl_left.c")
        for game in ("FruitNinja", "TempleRun", "PvZ", "AngryBirds"):
            self.assertIn(f"/Apps/Data/{game}/gl_names.txt", g)

    @unittest.skipUnless((PINNED / ".git").is_dir(), "pinned nano-angrybirds checkout not fetched")
    def test_anchors_match_the_pinned_commit(self):
        from host.ports import PORT_REPOS

        commit = PORT_REPOS["angrybirds"]["commit"]
        for rel, edits in ap.EDITS.items():
            text = subprocess.check_output(["git", "-C", str(PINNED), "show", f"{commit}:{rel}"], text=True)
            for old, _ in edits:
                self.assertEqual(text.count(old), 1, f"{rel}: {old.splitlines()[0]}")


class GlueTests(unittest.TestCase):
    def setUp(self):
        self.c = (ROOT / "games/angrybirds/nano/n7_ab.c").read_text()

    def test_numbers(self):
        n = ap.glue_numbers()
        self.assertEqual(n["N7_ARENA_NEED"], 0x200000)    # nano_alloc.c PORT_ARENA_MIN
        self.assertEqual(n["N7_TEX_MAX"], 0x300000)       # nano_platform.c TEXTURE_BUDGET
        self.assertLess(n["N7_TEX_MIN"], n["N7_TEX_MAX"])
        self.assertGreaterEqual(n["N7_DRIVER"], 1 << 20)
        self.assertEqual(ab.needs_free()["free"], 5 << 20)
        self.assertEqual(ab.needs_free()["largest"], 2 << 20)

    def test_notice_lines_fit_the_font(self):
        """notice.c draws up to 21 characters a line from its own 5x7 font."""
        nt = (ROOT / "games/fruitninja/nano/notice.c").read_text()
        chars = set(re.search(r'notice_chars\[\] = "([^"]*)"', nt).group(1))
        lines = re.findall(r'lines\[\d\] = "([^"]*)"', self.c)
        self.assertGreaterEqual(len(lines), 12)
        for line in lines + ["FREE 12.3M BIG 12.3M", "NEEDS 5.0M BIG 2.0M"]:
            self.assertLessEqual(len(line), 21, line)
            self.assertTrue(set(line) <= chars, line)

    def test_names_saved_rarely(self):
        self.assertIn("3000000u", self.c)
        self.assertIn("gll_save();", self.c)

    def test_gl_left_copies_know_every_port(self):
        for copy in ("games/fruitninja/nano/gl_left.c", "games/pvz_portable/nano/gl_left.c"):
            text = (ROOT / copy).read_text()
            for game in ("FruitNinja", "TempleRun", "PvZ", "AngryBirds"):
                self.assertIn(f"/Apps/Data/{game}/gl_names.txt", text, copy)


class WiringTests(unittest.TestCase):
    def test_catalog_row(self):
        reset_catalog_cache()
        row = by_name()["Angry Birds"]
        self.assertTrue(row["needs_payload"])
        self.assertTrue(row["large_gl"])
        self.assertEqual(row["fat_exe"], ab.FAT_EXE)
        self.assertEqual(row["fat_data"], ab.FAT_DATA)
        self.assertEqual(row["bundle_id"], "org.nanoapps.angrybirds")
        for alias in ("angrybirds", "angry-birds", "angry"):
            self.assertEqual(resolve_name(alias), "Angry Birds")

    def test_pinned_and_payload_dispatch(self):
        from host import ports

        meta = ports.PORT_REPOS["angrybirds"]
        self.assertRegex(meta["commit"], r"^[0-9a-f]{40}$")
        self.assertTrue(meta["url"].startswith("https://github.com/olievans123/"))
        self.assertIn("nabase add 'Angry Birds'", ports.missing_help("Angry Birds"))
        with mock.patch.object(ports, "build_angry", return_value={}) as build:
            ports.prepare_for_add("Angry Birds", payload=Path("/x/AngryBirds.ipa"))
        build.assert_called_once()
        with self.assertRaises(Refused):
            with mock.patch.object(ports, "data_ready", return_value=False):
                reset_catalog_cache()
                ports.prepare_for_add("Angry Birds")

    def test_installer_item(self):
        from host import installer

        items = [i for i in installer.ITEMS if i.name == "Angry Birds"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].bundle, ab.BUNDLE_ID)
        self.assertFalse(items[0].tested)

    def test_no_long_dashes(self):
        for rel in ("host/angrybirds.py", "host/angrybirds_patch.py", "games/angrybirds/nano/n7_ab.c",
                    "games/angrybirds/nano/n7_ab.h", "tests/test_angrybirds.py"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            for code in (0x2013, 0x2014):
                self.assertNotIn(chr(code), text, rel)


if __name__ == "__main__":
    unittest.main()
