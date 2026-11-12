"""Gravity Defied: payload checks, memory plan, catalog row, source hygiene."""
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host import gravity_defied as gd  # noqa: E402
from host.compat import Refused  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GAME = ROOT / "games" / "gravity_defied"


class PayloadTests(unittest.TestCase):
    def test_wrong_file_is_refused(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as fh:
            fh.write(b"not a jar")
            path = Path(fh.name)
        self.addCleanup(path.unlink)
        facts = gd.inspect_payload(path)
        self.assertFalse(facts["ok"])
        blob = " ".join(facts["problems"])
        self.assertIn("GDTR.jar", blob)

    def test_zip_without_mrg_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            jar = Path(d) / "other.jar"
            with zipfile.ZipFile(jar, "w") as zf:
                zf.writestr("readme.txt", "nope")
            facts = gd.inspect_payload(jar)
            self.assertFalse(facts["ok"])
            self.assertTrue(any("levels.mrg" in p for p in facts["problems"]))

    def test_generated_jar_is_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            jar = gd.make_test_jar(Path(d) / "GDTR.jar")
            facts = gd.inspect_payload(jar)
            self.assertTrue(facts["ok"], facts["problems"])
            self.assertEqual(facts["kind"], "jar")
            self.assertEqual(sum(len(lg) for lg in facts["header"]["leagues"]), 30)
            self.assertEqual(len(facts["header"]["leagues"]), 3)
            for lg in facts["header"]["leagues"]:
                self.assertEqual(len(lg), 10)

    def test_bare_mrg_and_folder(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "payload"
            folder.mkdir()
            mrg = folder / "levels.mrg"
            mrg.write_bytes(gd.build_test_mrg())
            self.assertTrue(gd.inspect_payload(mrg)["ok"])
            self.assertTrue(gd.inspect_payload(folder)["ok"])

    def test_assert_payload_raises(self):
        with tempfile.NamedTemporaryFile(suffix=".ipa", delete=False) as fh:
            fh.write(b"PK\x03\x04not really")
            path = Path(fh.name)
        self.addCleanup(path.unlink)
        with self.assertRaises(Refused) as ctx:
            gd.assert_payload(path)
        self.assertIn("local", str(ctx.exception).lower())


class MemoryTests(unittest.TestCase):
    def test_constants_match_the_nano_source(self):
        src = (GAME / "nano" / "gd_nano.c").read_text()
        for name in ("BLOCK_MAX", "BLOCK_MIN", "AFTER_MIN", "SLOP"):
            self.assertRegex(src, r"#define %s\s+0x%Xu" % (name, getattr(gd, name)))
        self.assertIn('#define DATA_DIR  "%s"' % gd.FAT_DATA, src)
        self.assertLess(src.index("NOT ENOUGH MEMORY"), src.index("hb_os_alloc(size)"))
        plat = (GAME / "engine" / "overlay" / "nano" / "gd_plat.c").read_text()
        self.assertIn("GD_DATA_DIR", (GAME / "engine" / "overlay" / "gd_config.h").read_text())
        self.assertIn("/Apps/Data/GravityDefied", plat + src)

    def test_notice_allocates_nothing(self):
        text = (GAME / "nano" / "notice.c").read_text()
        self.assertNotIn("malloc(", text)
        self.assertNotIn("hb_os_alloc", text)
        self.assertNotIn("new ", text)
        self.assertIn("NOT ENOUGH MEMORY", (GAME / "nano" / "gd_nano.c").read_text())

    def test_bluetooth_music_and_one_mb_fit_tiny_does_not(self):
        self.assertLessEqual(gd.BLOCK_MAX, 768 * 1024)
        self.assertGreaterEqual(gd.BLOCK_MIN, 256 * 1024)
        clean = gd.heap_fit(6 * 1024 * 1024, 4 * 1024 * 1024)
        self.assertEqual(clean["best"]["verdict"], "FITS")
        self.assertGreaterEqual(clean["best"]["block"], gd.BLOCK_MIN)
        short = gd.heap_fit(int(3.9 * 1024 * 1024), 2 * 1024 * 1024)
        self.assertEqual(short["best"]["verdict"], "FITS")
        one = gd.heap_fit(6 * 1024 * 1024, 1024 * 1024)
        self.assertEqual(one["best"]["verdict"], "FITS")
        tiny = gd.heap_fit(6 * 1024 * 1024, 256 * 1024)
        self.assertEqual(tiny["best"]["verdict"], "NO_FIT")

    def test_plan_without_payload_refuses(self):
        lines = gd.plan_lines(None, None, None)
        self.assertEqual(lines[-1], "PLAN_REFUSED")
        self.assertTrue(any("GDTR.jar" in l for l in lines))
        self.assertNotIn("\u2014", "\n".join(lines))

    def test_plan_with_generated_jar(self):
        with tempfile.TemporaryDirectory() as d:
            jar = gd.make_test_jar(Path(d) / "GDTR.jar")
            lines = gd.plan_lines(jar, 6 * 1024 * 1024, 4 * 1024 * 1024)
        self.assertTrue(any("30 tracks" in l for l in lines))
        self.assertFalse(any("REFUSED" in l for l in lines[:-1]))
        if lines[-1] == "PLAN_REFUSED":
            self.assertTrue(any("missing" in l for l in lines))
        else:
            self.assertEqual(lines[-1], "PLAN_OK")


class PortTableTests(unittest.TestCase):
    def test_registered_as_payload_not_large_gl(self):
        from host import ports
        from host.app_catalog import ALIASES, catalog, reset_catalog_cache, resolve_name

        self.assertIn(gd.NAME, ports.NEEDS_PAYLOAD)
        self.assertNotIn(gd.NAME, ports.LARGE_GL)
        self.assertEqual(ports.PORT_DATA[gd.NAME]["fat"], gd.FAT_DATA)
        self.assertEqual(resolve_name("gd"), gd.NAME)
        self.assertEqual(resolve_name("gdtr"), gd.NAME)
        self.assertEqual(ALIASES["gravity-defied"], gd.NAME)
        reset_catalog_cache()
        rows = {r["name"]: r for r in catalog()}
        self.assertIn(gd.NAME, rows)
        self.assertTrue(rows[gd.NAME]["needs_payload"])
        self.assertFalse(rows[gd.NAME]["large_gl"])
        self.assertEqual(rows[gd.NAME]["kind"], 1)
        self.assertIn("rgimad", rows[gd.NAME]["author"])
        self.assertIn("Codebrew", rows[gd.NAME]["author"])
        self.assertIn(ports.GD_CMD, ports.missing_help(gd.NAME))

    def test_plist_and_makefile(self):
        import plistlib

        info = plistlib.loads((GAME / "nano" / "app" / "Info.plist").read_bytes())
        self.assertEqual(info["CFBundleName"], gd.NAME)
        self.assertEqual(info["HBAppKind"], 1)
        self.assertEqual(info["HBIconGlyph"], "motorcycle")
        mk = (GAME / "nano" / "app" / "Makefile").read_text()
        self.assertIn("RAW_SURFACE := 1", mk)
        self.assertIn("CHECK_NO_THUMB_FP := 1", mk)
        self.assertIn("-marm", mk)
        self.assertIn("gd_sys.c", mk)
        self.assertIn("MKRELOC_FLAGS := --all-alloc --pad-sectors", mk)

    def test_float_in_arm_state(self):
        arm = (GAME / "engine" / "build_arm.py").read_text()
        self.assertIn("-marm", arm)
        self.assertIn("vfpv4-d16", arm)
        self.assertIn("cortex-a5", arm)
        self.assertIn("-fno-exceptions", arm)


class HygieneTests(unittest.TestCase):
    def test_no_game_files_in_git_tree(self):
        bad = [
            p for p in GAME.rglob("*")
            if p.is_file() and p.suffix.lower() in {".mrg", ".png", ".jpg", ".jar", ".hbapp", ".raw"}
        ]
        self.assertEqual(bad, [])

    def test_engine_fetched_not_vendored(self):
        self.assertFalse((GAME / "engine" / "src").exists())
        self.assertEqual(gd.ENGINE_COMMIT, "91bd283959b96a7ea07e1c4c0040460334c85458")
        self.assertTrue(gd.ENGINE_URL.startswith("https://github.com/rgimad/"))

    def test_menu_bar_and_font_are_readable(self):
        cfg = (GAME / "engine" / "overlay" / "gd_config.h").read_text()
        self.assertIn("#define GD_PAD_CELL_H 42", cfg)
        self.assertIn("#define GD_PAD_ROWS 3", cfg)
        self.assertIn("#define GD_MENU_ROW_MIN 36", cfg)
        font = (GAME / "engine" / "overlay" / "lcdui" / "Font.cpp").read_text()
        self.assertIn("px = 15", font)
        self.assertIn("px = 22", font)
        gfx = (GAME / "engine" / "overlay" / "lcdui" / "Graphics.cpp").read_text()
        self.assertIn("gd_ui_draw_text", gfx)
        plat = (GAME / "engine" / "overlay" / "nano" / "gd_plat.c").read_text()
        self.assertIn("gd_draw_touch_bar", plat)
        self.assertIn("gd_pad_digit", plat)
        self.assertIn("gd_set_key_mask", plat)
        self.assertIn("Inter", (GAME / "engine" / "overlay" / "nano" / "gd_ui_font.h").read_text())
        self.assertIn("OFL-1.1", (GAME / "engine" / "overlay" / "nano" / "gd_ui_font.h").read_text())
        self.assertGreater((GAME / "engine" / "overlay" / "nano" / "gd_ui_font.c").stat().st_size, 8000)
        self.assertIn("SIL OFL", (GAME / "NOTICE").read_text())
        patch = (GAME / "engine" / "patch_engine.py").read_text()
        self.assertIn("spawn lift", patch)
        self.assertIn("36 - font2->getBaselinePosition()", patch)
        self.assertIn("fillCircle(10", patch)
        self.assertIn("default phone keypad", patch)
        self.assertIn("settingStringExitGame", patch)
        self.assertIn("hostSetUnlocks", patch)
        self.assertIn("cancelPending", (GAME / "engine" / "overlay" / "Micro.cpp").read_text())
        host = (GAME / "engine" / "host" / "main.c").read_text()
        self.assertIn("gd_engine_set_unlocks", host)
        self.assertIn("gd_engine_select_row", host)
        self.assertIn("gd_engine_pick_row", host)
        save = gd._save_script()
        self.assertIn("unlocks 2 4 1", save)
        self.assertIn("pick 1", save)
        self.assertIn("row 4", save)
        self.assertIn("row 5", gd._pause_quit_script())

    def test_upstream_fixes_are_in_the_patcher(self):
        patch = (GAME / "engine" / "patch_engine.py").read_text()
        self.assertIn("spins > 16", patch)
        self.assertIn("steps > 1024", patch)
        rms = (GAME / "engine" / "overlay" / "rms" / "RecordEnumerationImpl.cpp").read_text()
        self.assertIn("Leave the records in place", rms)
        self.assertIn("corrupt record", rms)
        self.assertIn("int32_t nrec", rms)
        menu = (GAME / "engine" / "overlay" / "Micro.cpp").read_text()
        self.assertIn("tick()", menu)
        self.assertIn("requestQuit", menu)

    def test_gitignore_covers_staged_files(self):
        gi = (ROOT / ".gitignore").read_text()
        self.assertIn("untether/artifacts/extra/Data/GravityDefied/", gi)
        self.assertIn("nanoapps/apps/gravity_defied/", gi)
        self.assertIn("Gravity Defied.hbapp", gi)

    def test_credits_and_licence(self):
        credits = (ROOT / "CREDITS.md").read_text()
        for needle in ("rgimad", "AntonEvmenenko", "Max Logaev", "Codebrew", "91bd283"):
            self.assertIn(needle, credits)
        self.assertIn("GPL-2.0", (GAME / "LICENSE").read_text())

    def test_no_em_dashes_in_new_text(self):
        seen = []
        roots = [ROOT / "host" / "gravity_defied.py", GAME]
        files = []
        for r in roots:
            if r.is_file():
                files.append(r)
            else:
                files.extend(p for p in r.rglob("*") if p.suffix in {".c", ".h", ".cpp", ".md", ".py"})
        for p in files:
            if "\u2014" in p.read_text(encoding="utf-8", errors="replace"):
                seen.append(str(p.relative_to(ROOT)))
        self.assertEqual(seen, [])


if __name__ == "__main__":
    unittest.main()
