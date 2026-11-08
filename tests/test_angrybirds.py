"""Angry Birds Android 1.0.0 .so loader: memory plan, payload checks, CLI, source hygiene."""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from host import angrybirds as ab

ROOT = Path(__file__).resolve().parents[1]
GAME = ROOT / "games" / "angrybirds"
APK = os.environ.get("NANO7KIT_ANGRY_APK")


class FitTests(unittest.TestCase):
    def test_fresh_boot_fits_or_reduced(self):
        fit = ab.heap_fit(6_710_886, 4_194_304)
        self.assertIn(fit["verdict"], ("FITS", "FITS_REDUCED"))
        self.assertEqual(fit["block"], ab.BLOCK_MAX)
        self.assertTrue(fit["image_in_largest_ok"])
        self.assertLessEqual(ab.IMAGE_BYTES, 4_194_304 - ab.BLOCK_MIN - ab.SLOP)
        self.assertTrue(ab.BUDGET_MIN <= fit["budget"] <= ab.BUDGET_MAX)

    def test_music_playing_refuses_like_the_app(self):
        self.assertEqual(ab.heap_fit(6_000_000, 2_097_152)["verdict"], "NO_FIT")
        self.assertEqual(ab.heap_fit(5_000_000, 4_000_000)["verdict"], "NO_FIT")

    def test_constants_match_the_nano_source(self):
        src = (GAME / "nano" / "fn_nano.c").read_text()
        for name in ("BLOCK_MAX", "BLOCK_MIN", "GL_KEEP", "AFTER_MIN"):
            self.assertRegex(src, r"#define %s\s+0x%Xu" % (name, getattr(ab, name)))
        self.assertIn("#define BYTES_PER_TRIANGLE %du" % ab.BYTES_PER_TRIANGLE, src)
        self.assertIn("free0 < size + AFTER_MIN", src)
        self.assertLess(src.index("short_of_memory(free0"), src.index("block = hb_os_alloc(size);"))
        self.assertIn("ab_heap_init(block, size)", src)
        plat = (GAME / "nano" / "plat_nano.c").read_text()
        self.assertIn('#define DATA_DIR "%s"' % ab.FAT_DATA, plat)
        self.assertIn("plat_os_alloc", plat)
        self.assertIn("plat_os_free", plat)
        self.assertIn("plat_os_largest", plat)
        self.assertIn("hb_os_heap_largest", plat)
        self.assertIn("plat_os_alloc", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("plat_os_alloc", (GAME / "nano" / "be_nano.c").read_text())
        host = (GAME / "host" / "main.c").read_text()
        self.assertIn("AB_PLAY_TEST", host)
        self.assertIn("AB_GESTURE_TEST", host)
        self.assertIn("AB_LEVELS_TEST", host)
        self.assertIn("libangrybirds.so", host)

    def test_notice_allocates_nothing(self):
        n = (GAME / "nano" / "notice.c").read_text()
        self.assertNotIn("malloc", n)
        self.assertIn("NOT ENOUGH MEMORY", (GAME / "nano" / "fn_nano.c").read_text())

    def test_plan_without_payload_refuses(self):
        lines = ab.plan_lines(None, None, None)
        self.assertEqual(lines[-1], "PLAN_REFUSED")
        self.assertTrue(all("\u2014" not in l for l in lines))

    def test_plan_counts_the_pack(self):
        lines = ab.plan_lines(None, 6_710_886, 4_194_304, on_disk=["App%d" % i for i in range(11)], slot_b=True)
        self.assertTrue(any("with Angry Birds 12 of 16" in l for l in lines))
        full = ab.plan_lines(None, 6_710_886, 4_194_304, on_disk=["App%d" % i for i in range(16)], slot_b=True)
        self.assertTrue(any("over the 16-app cap" in l for l in full))
        noslot = ab.plan_lines(None, 6_710_886, 4_194_304, slot_b=False)
        self.assertTrue(any("slot B is not active" in l for l in noslot))


class PortTableTests(unittest.TestCase):
    def test_registered_as_large_gl_payload(self):
        from host.ports import ANGRY_CMD, LARGE_GL, missing_help

        self.assertIn("Angry Birds", LARGE_GL)
        self.assertEqual(ANGRY_CMD, ab.CMD)
        self.assertIn("JNI loader", missing_help("Angry Birds"))
        self.assertIn("c80ed55b", missing_help("Angry Birds"))
        self.assertIn("nabase add", ab.CMD)

    def test_installer_accepts_apk_zip(self):
        from host.installer import by_name

        item = by_name("Angry Birds")
        self.assertIsNotNone(item)
        self.assertEqual(item.kind, "apk")
        self.assertIn("APK", item.needs)

    def test_aliases(self):
        from host.app_catalog import resolve_name

        self.assertEqual(resolve_name("angry"), "Angry Birds")
        self.assertEqual(resolve_name("angrybirds"), "Angry Birds")
        self.assertEqual(resolve_name("Angry Birds"), "Angry Birds")

    def test_plan_routes(self):
        from host import cli

        args = SimpleNamespace(apps_cmd="add", names=["AngryBirds"], plan=True, dry_run=False,
                               heap_free="6.4M", largest="4.0M", payload=None, all=False,
                               extras=False, rom=None, device="/nonexistent")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.cmd_apps(args)
        text = buf.getvalue()
        self.assertEqual(rc, 1)
        self.assertIn("nabase add AngryBirds", text)
        self.assertIn("verdict", text)


class SourceTests(unittest.TestCase):
    def test_first_party_sources_only(self):
        allowed = {".c", ".h", ".py", ".S", ".sh", ".plist", ""}
        for p in GAME.rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts:
                self.assertIn(p.suffix, allowed, p)
                self.assertLess(p.stat().st_size, 120_000, p)
                self.assertNotIn("\u2014", p.read_text(encoding="utf-8"), p)

    def test_derived_outputs_ignored(self):
        gi = (ROOT / ".gitignore").read_text()
        self.assertIn("/build/", gi)
        self.assertIn("nanoapps/apps/angrybirds_rc/", gi)
        self.assertIn("untether/artifacts/extra/Executables/Angry Birds.hbapp", gi)
        self.assertIn("untether/artifacts/extra/Data/AngryBirds/", gi)
        self.assertIn("untether/artifacts/extra/Icons/Angry Birds.bin", gi)

    def test_loads_android_so_not_ios_recompile(self):
        self.assertFalse((GAME / "tools" / "recomp.py").exists())
        self.assertTrue((GAME / "tools" / "so_to_obj.py").is_file())
        so = (GAME / "rt" / "so_load.c").read_text()
        self.assertIn("ET_DYN", so)
        self.assertIn("R_ARM_RELATIVE", so)
        self.assertIn("ab_resolve", so)
        shims = (GAME / "rt" / "shims.c").read_text()
        self.assertIn("ab_glActiveTexture", shims)
        self.assertIn("ab_malloc", shims)
        self.assertIn("memset(h + 1, 0, n)", shims)
        self.assertIn("memset(c + 1, 0, n)", shims)
        self.assertIn("double ab_sin(double x)", shims)
        self.assertIn("A(sin, ab_sin)", shims)
        conv = (GAME / "tools" / "so_to_obj.py").read_text()
        self.assertIn("ab_gl", conv)
        self.assertIn('"malloc": "ab_malloc"', conv)
        self.assertIn("Do not re-export the .so's defined globals", conv)
        self.assertIn("void _fini(void)", shims)
        self.assertIn("void __libc_fini(void)", shims)
        self.assertIn("#ifdef FN_NANO", shims)
        app_mk = (GAME / "nano" / "app" / "Makefile").read_text()
        self.assertIn("allow-multiple-definition", app_mk)
        jni = (GAME / "rt" / "jni.c").read_text()
        self.assertIn("jni_fn[176]", jni)
        self.assertIn("jni_fn[200]", jni)
        self.assertIn("jni_fn[169]", jni)
        self.assertIn("MID_READFILE", jni)
        self.assertIn("isSilentProfile", jni)
        stub = (GAME / "rt" / "audio_stub.c").read_text()
        self.assertIn("ab_stub_audio", stub)
        self.assertIn("k_mp3", stub)
        self.assertIn("ab_stub_audio", (GAME / "host" / "main.c").read_text())
        self.assertIn("ab_stub_audio", (GAME / "nano" / "plat_nano.c").read_text())

    def test_landscape_and_pinch(self):
        entry = (GAME / "rt" / "ab_entry.c").read_text()
        self.assertIn("480", entry)
        self.assertIn("320", entry)
        self.assertIn("ab_apply_pinch", entry)
        self.assertIn("AB_FINGERS", entry)
        self.assertIn("nativeInput", entry)
        self.assertIn("ACTION_DOWN=0", entry)
        self.assertIn("ACTION_MOVE=2", entry)
        self.assertIn("ab_sling_world", entry)
        self.assertIn("ab_lua_dump_shot", entry)
        self.assertIn("ab_lua_xy", entry)
        self.assertIn("AB_LUA_OFF 0x294u", entry)
        self.assertIn("AB_WORLD_OFF 0x100u", entry)
        self.assertIn("allowResetToBirdCamera", entry)
        self.assertIn("ab_cam_fit_world", entry)
        self.assertIn("ab_cam_fit_birds", entry)
        self.assertIn("ab_set_body_velocity", entry)
        self.assertIn("ab_pouch_world", entry)
        self.assertIn("ab_world_to_panel", entry)
        self.assertIn("SpriteC1", entry)
        self.assertIn("hook_spr_ctor", entry)
        self.assertIn("AB_SPRITE", entry)
        self.assertIn("getSpriteBounds(\"\", sprite)", entry)
        self.assertIn("ab_lua_get_number", entry)
        self.assertIn("_ZNK3lua8LuaTable9getNumberEPKc", entry)
        self.assertIn("ab_phys_enable(0)", entry)
        self.assertIn("33333 us per frame", entry)
        self.assertIn("ShootingPlatform01", entry)
        self.assertIn("getBody", entry)
        self.assertIn("hook_set_scale", entry)
        self.assertIn("cam_hold", entry)
        nano = (GAME / "nano" / "fn_nano.c").read_text()
        self.assertIn("list_touch_q", nano)
        self.assertIn("snap_finger_now", nano)
        self.assertIn("emit_touch(1", nano)
        self.assertIn("*gy = fe_fb_h - (px - fe_off_x) / sx", entry)
        gl = (GAME / "rt" / "fngl.c").read_text()
        self.assertIn("Home-on-right", gl)
        self.assertIn("sv[i].x = fe_off_x + fy * fe_scale", gl)
        self.assertIn("fe_reset_matrices", gl)
        self.assertIn("fe_note_texfile", gl)
        self.assertIn("tex_log_w", gl)
        self.assertIn("POT_PAD", gl)
        self.assertIn("DRAW_NO_IMAGE", gl)
        self.assertIn("AB_TEX", gl)
        self.assertIn("tex_unit", gl)
        self.assertIn("bound_tex1", gl)
        self.assertIn("client_unit", gl)
        self.assertNotIn("void ab_glActiveTexture(GLenum t) { (void)t; }", gl)
        self.assertIn("m == 0x1701u", gl)
        self.assertIn("m == 0x1702u", gl)
        self.assertNotIn("m == 2u || m == 3u", gl)
        self.assertNotIn("color[0] > 0.92f", gl)
        self.assertIn("tex_unit = client_unit = 0", gl)
        self.assertIn("ortho_hud", gl)
        self.assertIn("commit_tex_identity", gl)
        self.assertIn("ts_shift_for", gl)
        self.assertIn("leftover FONT", gl)
        self.assertIn("Engine UVs are already", gl)
        self.assertNotIn("v.u *= (float)tex_log_w", gl)
        self.assertIn("icon_yel", (GAME / "host" / "main.c").read_text())
        self.assertIn("castle_clip", (GAME / "host" / "main.c").read_text())
        self.assertIn("pause_grn", (GAME / "host" / "main.c").read_text())
        host = (GAME / "host" / "main.c").read_text()
        play = host[host.index('getenv("AB_PLAY_TEST")'):host.index('getenv("AB_GESTURE_TEST")')]
        self.assertNotIn("ab_set_body_velocity", play)
        self.assertIn("ab_pouch_world", play)
        self.assertIn("ab_sling_world", play)
        self.assertIn("grab_rubber", play)
        self.assertIn("panel-aim", play)
        self.assertIn("panel-fly-1", play)
        self.assertIn("panel-aim2", play)
        self.assertIn("tex_ok", host)
        self.assertIn("AB_TEXVAR", host)
        self.assertIn("near-white", host)
        self.assertIn("cy < 36", host)
        self.assertIn("AB_HUD", host)
        self.assertIn("AB_FRAME", host)
        self.assertIn("pause_yel", host)
        self.assertIn("dump_castle_crop", host)
        self.assertIn("ab_cam_fit_world", host)
        self.assertIn("AB_SCORE", host)
        self.assertIn("AB_HOLD", host)
        self.assertIn("AB_LUA_POS", host)
        self.assertIn("panel-hit", host)
        self.assertIn("hold_ok", host)
        self.assertIn("match_level_file", host)
        self.assertIn("AB_FRAME_US", host)
        self.assertIn("minx -= 40.f", entry)
        self.assertNotIn("minx -= 180.f", entry)
        self.assertIn("AB_CAM fit", entry)
        self.assertIn("P6 432 240", host)
        self.assertIn("P6 240 432", host)
        self.assertIn("panel-l1", host)
        self.assertIn("pinch_out", host)
        self.assertIn("pinch_in", host)
        self.assertIn("panel-pinch-1", host)
        self.assertIn("panel-pinch-4", host)
        self.assertIn("panel-pause", host)
        self.assertIn("panel-resume", host)
        self.assertIn("tap_resume", host)
        self.assertIn("Level1", host)
        self.assertIn("CLAMP_TO_EDGE", (GAME / "nano" / "be_nano.c").read_text())
        self.assertIn("wrap_s", (GAME / "host" / "be_soft.c").read_text())
        mk = (GAME / "nano" / "Makefile").read_text()
        self.assertIn("-mfloat-abi=softfp", mk)
        self.assertIn("-marm", mk)
        self.assertIn("so_load", mk)
        self.assertIn("cxa_hook", mk)
        jmp = (GAME / "nano" / "fn_jmp.S").read_text()
        self.assertIn(".set setjmp, fn_setjmp", jmp)

    def test_convert_skips_audio_keep_512(self):
        src = (GAME / "tools" / "convert_assets.py").read_text()
        self.assertIn(".wav", src)
        self.assertIn(".mp3", src)
        self.assertIn("INGAME_KEEP = 512", src)
        self.assertIn("UI_KEEP = 512", src)
        self.assertIn("keep_for", src)
        self.assertIn("texscale", src)
        self.assertIn("scale_sheet_dat", src)
        self.assertIn('block_dat = {"INGAME", "INGAME_1"}', src)
        self.assertIn("empty sheet name", src)


@unittest.skipUnless(APK, "set NANO7KIT_ANGRY_APK to the Angry Birds 1.0.0 apk/zip to run")
class PayloadTests(unittest.TestCase):
    def test_payload_is_the_1_0_0_v7a_build(self):
        facts = ab.inspect_payload(Path(APK))
        self.assertTrue(facts["ok"], facts.get("problems"))
        self.assertEqual(facts["sha256"], ab.SO_SHA256)
        src = Path(APK)
        if src.is_file():
            self.assertEqual(facts["apk_sha256"], ab.APK_SHA256)


ABHOST = Path(os.environ.get("NANO7KIT_ABHOST", "/tmp/ab-android/abhost"))
ABDATA = Path(os.environ.get("NANO7KIT_ABDATA", "/tmp/ab-android/data"))
ABSO = Path(os.environ.get(
    "NANO7KIT_ABSO", "/tmp/ab-apk/lib/armeabi-v7a/libangrybirds.so"))


@unittest.skipUnless(ABHOST.is_file() and (ABDATA / "Level1.lua").is_file() and ABSO.is_file(),
                     "needs a built abhost, converted data/, and libangrybirds.so")
class PhysicsHarnessTests(unittest.TestCase):
    @staticmethod
    def _harness_env(**extra):
        env = os.environ.copy()
        for k in ("AB_PLAY_TEST", "AB_GESTURE_TEST", "AB_LEVELS_TEST",
                  "AB_LEVELS_MIN", "AB_LEVELS_MAX"):
            env.pop(k, None)
        env.update(extra)
        return env

    def test_levels_1_3_draw_objects(self):
        import subprocess
        env = self._harness_env(AB_LEVELS_TEST="1", AB_PHYS_WARM="200", AB_LEVELS_SETTLE="150")
        out = ROOT / "build" / "angrybirds_rc" / "levels-test"
        out.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            ["qemu-arm-static", "-cpu", "cortex-a8", str(ABHOST), str(ABDATA), str(ABSO), str(out), "1", "0"],
            env=env, capture_output=True, text=True, timeout=420)
        err = (r.stderr or "") + (r.stdout or "")
        self.assertIn("AB_LEVELS_TEST PASS", err, err[-8000:])
        self.assertEqual(r.returncode, 0, err[-4000:])
        self.assertIn("Level1", err)
        self.assertIn("Level3", err)
        self.assertIn("tex_ok=", err)
        self.assertNotRegex(err, r"AB_LEVELS_TEST Level[123].*tex_ok=0")
        self.assertIn("AB_HUD Level1", err)
        self.assertIn("AB_FRAME Level1", err)
        self.assertIn("AB_HUD Level2", err)
        self.assertIn("AB_FRAME Level2", err)
        self.assertIn("pause_yel=", err)
        self.assertIn("AB_SPRITE", err)
        import re
        for lvl in ("Level1", "Level2", "Level3"):
            self.assertRegex(err, rf"AB_SCORE {lvl} open=0")
            self.assertRegex(err, rf"AB_SCORE {lvl} hold=0")
            self.assertIn(f"AB_HOLD {lvl} ", err)
            pos = re.search(rf"AB_LUA_POS {lvl} file_xy=(\d+) matched=(\d+) miss=(\d+)", err)
            self.assertIsNotNone(pos, err[-4000:])
            self.assertGreaterEqual(int(pos.group(1)), 4, f"{lvl} file_xy")
            self.assertGreaterEqual(int(pos.group(2)), 4, f"{lvl} matched")
            self.assertLessEqual(int(pos.group(3)) * 4, int(pos.group(1)), f"{lvl} miss")
        for lvl in ("Level1", "Level2"):
            m = re.search(rf"AB_HUD {lvl} pause_yel=(\d+) icon_yel=(\d+) pause_dark=\d+ pause_grn=(\d+) score_ink=(\d+)", err)
            self.assertIsNotNone(m, err[-4000:])
            self.assertGreaterEqual(int(m.group(1)), 60, f"{lvl} pause_yel")
            self.assertGreaterEqual(int(m.group(2)), 40, f"{lvl} icon_yel")
            self.assertLess(int(m.group(3)), 50, f"{lvl} pause_grn")
            self.assertGreaterEqual(int(m.group(4)), 40, f"{lvl} score_ink")
            f = re.search(rf"AB_FRAME {lvl} pouch=([0-9.]+),([0-9.]+) in=\d+ clip=\d+ castle=\d+ castle_clip=(\d+) scale=([0-9.]+)", err)
            self.assertIsNotNone(f, err[-4000:])
            self.assertGreaterEqual(float(f.group(1)), 8.0, f"{lvl} pouch x")
            self.assertLessEqual(float(f.group(1)), 232.0)
            self.assertGreaterEqual(float(f.group(2)), 4.0, f"{lvl} pouch y")
            self.assertLessEqual(float(f.group(2)), 210.0)
            self.assertEqual(int(f.group(3)), 0, f"{lvl} castle_clip")
            self.assertGreaterEqual(float(f.group(4)), 0.22, f"{lvl} scale")

    def test_playthrough_level1_slingshot(self):
        import subprocess
        env = self._harness_env(AB_PLAY_TEST="1")
        out = ROOT / "build" / "angrybirds_rc" / "play-test"
        out.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            ["qemu-arm-static", "-cpu", "cortex-a8", str(ABHOST), str(ABDATA), str(ABSO), str(out), "1", "1"],
            env=env, capture_output=True, text=True, timeout=300)
        err = (r.stderr or "") + (r.stdout or "")
        self.assertIn("AB_PLAY_TEST PASS", err, err[-8000:])
        self.assertEqual(r.returncode, 0, err[-4000:])
        self.assertIn("flying=1", err)
        self.assertIn("AB_PLAY_TEST rbp", err)
        self.assertIn("AB_SHOT", err)
        self.assertRegex(err, r"AB_PLAY_TEST hit score=[1-9]")
        self.assertNotIn("setVelocity", err)
        self.assertTrue((out / "panel-aim.ppm").is_file(), err[-2000:])
        self.assertTrue((out / "panel-fly-1.ppm").is_file(), err[-2000:])
        self.assertTrue((out / "panel-fly-2.ppm").is_file(), err[-2000:])
        self.assertTrue((out / "panel-hit.ppm").is_file(), err[-2000:])
        self.assertTrue((out / "panel-aim2.ppm").is_file(), err[-2000:])

    def test_gesture_frame_fixed_pinch_and_l1_l2_l3(self):
        import subprocess
        env = self._harness_env(AB_GESTURE_TEST="1")
        out = ROOT / "build" / "angrybirds_rc" / "gesture-test"
        out.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            ["qemu-arm-static", "-cpu", "cortex-a8", str(ABHOST), str(ABDATA), str(ABSO), str(out), "1", "0"],
            env=env, capture_output=True, text=True, timeout=300)
        err = (r.stderr or "") + (r.stdout or "")
        self.assertIn("AB_GESTURE_TEST PASS", err, err[-8000:])
        self.assertEqual(r.returncode, 0, err[-4000:])
        self.assertIn("AB_GESTURE_TEST Level1", err)
        self.assertIn("AB_GESTURE_TEST Level2", err)
        self.assertIn("AB_GESTURE_TEST Level3", err)
        self.assertIn("tex_ok=", err)
        self.assertIn("pinch-out", err)
        self.assertIn("pinch-in", err)
        self.assertIn("pinch-in2", err)
        self.assertIn("pinch-out2", err)
        self.assertIn("AB_GESTURE_TEST resume", err)
        self.assertTrue((out / "panel-l1.ppm").is_file(), err[-2000:])
        self.assertTrue((out / "panel-l2.ppm").is_file(), err[-2000:])
        self.assertTrue((out / "panel-l3.ppm").is_file(), err[-2000:])
        self.assertTrue((out / "panel-pinch-1.ppm").is_file(), err[-2000:])
        self.assertTrue((out / "panel-pinch-2.ppm").is_file(), err[-2000:])
        self.assertTrue((out / "panel-pinch-3.ppm").is_file(), err[-2000:])
        self.assertTrue((out / "panel-pinch-4.ppm").is_file(), err[-2000:])
        self.assertTrue((out / "panel-pinch-out.ppm").is_file(), err[-2000:])
        self.assertTrue((out / "panel-pinch-in.ppm").is_file(), err[-2000:])
        self.assertTrue((out / "panel-pause.ppm").is_file(), err[-2000:])
        self.assertTrue((out / "panel-resume.ppm").is_file(), err[-2000:])


if __name__ == "__main__":
    unittest.main()
