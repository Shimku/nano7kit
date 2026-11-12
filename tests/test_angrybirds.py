"""Angry Birds recompile: memory plan, payload checks, CLI routing, source hygiene."""
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
IPA = os.environ.get("NANO7KIT_ANGRY_IPA")


class FitTests(unittest.TestCase):
    def test_fresh_boot_fits_or_reduced(self):
        fit = ab.heap_fit(6_710_886, 4_194_304)          # Probe 6.4M / 4.0M
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
        plat = (GAME / "nano" / "plat_nano.c").read_text()
        self.assertIn('#define DATA_DIR "%s"' % ab.FAT_DATA, plat)
        self.assertNotIn("FN_CHUNK", plat)
        self.assertIn("plat_os_alloc", plat)
        self.assertIn("plat_os_free", plat)
        self.assertIn("plat_os_largest", plat)
        self.assertIn("hb_os_heap_largest", plat)
        self.assertIn("plat_os_alloc", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("plat_os_alloc", (GAME / "nano" / "be_nano.c").read_text())
        core = (GAME / "rt" / "rt_core.c").read_text()
        self.assertIn("OUT_BASE", core)
        self.assertIn("0xB0000000", core)
        self.assertIn("128u * 1024u", core)
        self.assertIn("plat_os_largest", core)
        self.assertIn("AB_NO_OUTSIDE", core)
        self.assertIn("Address-ordered", core)
        self.assertIn("guest overflow", core)
        rth = (GAME / "rt" / "rt.h").read_text()
        self.assertIn("#define AT(a, n) (M + (uint32_t)(a))", rth)
        self.assertIn("rt_outside_ptr", rth)
        self.assertIn("host_ptr - g_mem", rth)
        host = (GAME / "host" / "main.c").read_text()
        self.assertIn("0x200000u", host)
        self.assertNotIn("24u << 20", host)
        self.assertIn("3865672u", host)
        self.assertIn("2097152u", host)
        self.assertIn("1048576u", host)
        self.assertIn("524288u", host)
        self.assertIn("6014264u", host)
        self.assertIn("4194304u", host)
        self.assertIn("AB_OS_HEAP", host)
        self.assertIn("os_profile", host)
        self.assertIn("panel-lowmem", host)
        self.assertIn("AB_LOWMEM_TEST", host)
        self.assertIn("plat_os_alloc(size)", host)
        self.assertIn("print_cam", host)
        self.assertIn("pan_drag", host)
        self.assertIn("panel-menu", host)
        self.assertIn("AB_NO_OUTSIDE", (GAME / "rt" / "rt_core.c").read_text())
        self.assertIn("AB_PLAY_TEST", host)
        self.assertIn("AB_GESTURE_TEST", host)
        self.assertIn("AB_DEVICE_TEST", host)
        self.assertIn("AB_BAD_SAVE", host)
        self.assertIn("rt_malloc_failures", host)
        self.assertIn("HEAP_HDR", core)
        self.assertIn("4-mod-8", core)
        self.assertIn("rt_check_align8", core)
        self.assertIn("rt_align_faults", (GAME / "rt" / "rt.h").read_text())

    def test_notice_allocates_nothing(self):
        n = (GAME / "nano" / "notice.c").read_text()
        self.assertNotIn("malloc", n)
        self.assertIn("notice_fill_rgb", n)
        nano = (GAME / "nano" / "fn_nano.c").read_text()
        self.assertIn("NOT ENOUGH MEMORY", nano)
        self.assertIn("CHECK LOG.TXT", nano)
        self.assertIn("fe_enter_lowmem", nano)
        self.assertIn("fe_enter_lowmem", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("fe_lowmem", (GAME / "rt" / "ab_entry.c").read_text())

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
        self.assertIn("recompile", missing_help("Angry Birds"))
        self.assertIn("cdea1290", missing_help("Angry Birds"))

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
        self.assertIn("apps add AngryBirds --plan", text)
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

    def test_recomp_roots_and_silent_stub(self):
        src = (GAME / "tools" / "recomp.py").read_text()
        self.assertIn("__ZN9framework4initEPNS_11OSInterfaceEPN2gr7ContextE", src)
        self.assertIn("__ZN7GameApp13activateAudioEb", src)
        self.assertIn("__ZN2gr13createShadersEPNS_11EGL_ContextE", src)
        self.assertIn("ARM+VFP", src)
        # VMLS is Fd-Fn*Fm (op=00 bit6=1); swapping it with VNMLS sheared Image::draw.
        self.assertIn("VMLS  (op=00 bit6=1) Fd=Fd-Fn*Fm", src)
        self.assertIn('"%s-(%s*%s)" % (S(dd), S(dn), S(dm))', src)
        self.assertIn('"%s*%s-(%s)" % (S(dn), S(dm), S(dd))', src)
        self.assertIn("fold_pc", src)
        self.assertIn("__cstring", src)
        self.assertIn("GUEST_STACK 0x20000u", src)
        self.assertIn("createAudio", src)
        self.assertIn("init_mp3", src)
        self.assertIn("vmov_rr_d", src)
        self.assertIn("0xec532b16", src)
        self.assertIn("0xa112c", src)
        # VCMP writes FPSCR only. APSR (n_/z_/c_/v_) is copied by FMSTAT.
        # Assigning n_ inside VCMP made Box2D ComputeAABB keep the last vertex.
        self.assertIn("uint32_t nn_=a_<b_", src)
        self.assertIn("FMSTAT", src)
        self.assertNotIn("n_=a_<b_;z_=a_==b_;c_=!(a_<b_);v_=a_!=a_||b_!=b_;", src)

    def test_nano_image_is_thumb_soft_with_arm_setjmp(self):
        mk = (GAME / "nano" / "Makefile").read_text()
        self.assertIn("ARCH_RECOMP := -mthumb", mk)
        self.assertIn("-mfloat-abi=soft", mk)
        self.assertIn("recomp_14.o", mk)
        self.assertIn("recomp_15.o", mk)
        self.assertIn("Box2D", mk)
        jmp = (GAME / "nano" / "fn_jmp.S").read_text()
        self.assertIn(".set setjmp, fn_setjmp", jmp)
        self.assertIn(".set longjmp, fn_longjmp", jmp)

    def test_landscape_and_slingshot(self):
        entry = (GAME / "rt" / "ab_entry.c").read_text()
        self.assertIn("480", entry)
        self.assertIn("320", entry)
        self.assertNotIn("ab_zoom_by", entry)
        self.assertNotIn("ab_pan_by", entry)
        self.assertIn("ab_apply_pan", entry)
        self.assertIn("cam_user", entry)
        self.assertIn("minWorldScale", entry)
        self.assertNotIn("view_zoom", entry)
        nano = (GAME / "nano" / "fn_nano.c").read_text()
        self.assertNotIn("ab_zoom_by", nano)
        self.assertNotIn("ab_pan_by", nano)
        self.assertNotIn("HB_BTN_VOL_UP", nano)
        self.assertNotIn("y < 28.f", nano)
        self.assertIn("list_touch_q", nano)
        self.assertIn("snap_finger_now", nano)
        self.assertIn("emit_touch(1", nano)
        self.assertIn("*gy = fe_fb_h - (px - fe_off_x) / sx", entry)
        self.assertIn("Nano7PanelToGame", entry)
        self.assertIn("lua y-down", entry)
        self.assertIn("AB_LBUTTON", entry)
        self.assertIn("mouseMove", entry)
        self.assertIn("ab_call_lua", entry)
        self.assertIn("0x7497cu", entry)
        self.assertIn("LuaCall", (GAME / "tools" / "recomp.py").read_text())
        self.assertIn("for(int i_=0;i_<%du;i_++)", (GAME / "tools" / "recomp.py").read_text())
        gl = (GAME / "rt" / "fngl.c").read_text()
        self.assertIn("Home on the right", gl)
        self.assertIn("n7gl_dev_a.inc", gl)
        self.assertIn("sv[i].x = fe_off_x + fy * fe_scale", gl)
        self.assertNotIn("gyd = fe_fb_h - fy", gl)
        self.assertIn("init_once()", gl)
        self.assertIn("frontface == 0x0900u", gl)
        self.assertIn("_glFrontFace", gl)
        self.assertIn("_glTexSubImage2D", gl)
        self.assertIn("I__glTexImage2D", gl)
        host = (GAME / "host" / "main.c").read_text()
        self.assertIn("P6 432 240", host)
        self.assertIn("P6 240 432", host)
        self.assertIn("n7soft_shot", host)
        self.assertIn("px = 239 - gy", host)
        self.assertIn("AB_PHYS_TEST", host)
        self.assertIn("AB_LEVELS_TEST", host)
        self.assertIn("AB_GESTURE_TEST", host)
        self.assertIn("panel-l1", host)
        self.assertIn("panel-l2", host)
        self.assertIn("panel-pause", host)
        self.assertIn("pinch_out", host)
        self.assertIn("fe_off_x != 0.f", host)
        self.assertIn("play_pixels", host)
        self.assertIn("panel_black", host)
        self.assertIn("AB_LEVELS_MIN", host)
        self.assertIn("ab_load_lua_level", host)
        self.assertIn("ab_goto_level", host)
        self.assertIn("ab_phys_snapshot", entry)
        self.assertIn("ab_phys_resting", entry)
        self.assertIn("ab_cam_fit_play", entry)
        self.assertIn("ab_load_lua_level", entry)
        self.assertIn("ab_goto_level", entry)
        self.assertIn("castleCameraData", entry)
        self.assertIn("cam %s iphone", entry)
        self.assertIn("1.05f", entry)
        self.assertIn("1024.f", entry)
        self.assertIn("iphone-rescale", entry)
        self.assertIn("fe_reset_matrices", entry)
        self.assertIn("fe_reset_matrices", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("AB_SetTheme", entry)
        self.assertIn("cameraFunction", entry)
        self.assertIn("drawGame", entry)
        self.assertNotIn('rt_log("camfit', entry)
        self.assertIn("castleCameraTimer", entry)
        self.assertIn('"castleCameraTimer", 3.f', entry)
        self.assertIn("o->contacts < 1", entry)
        self.assertIn("Level1", host)
        self.assertIn("Level4", host)
        self.assertIn("loadNextLevel", host)
        self.assertIn("300", host)
        self.assertIn("TUTORIAL_OK", host)
        self.assertIn("49.f, 324.f", host)
        self.assertIn("55.f, 333.f", host)
        self.assertIn("pouch bird", host)
        self.assertIn("pix < 800", host)
        self.assertIn("0.25f", host)
        self.assertIn("0.22f", host)
        self.assertIn("215.f, 24.f", host)
        self.assertIn("pin_touch_count", entry)
        self.assertIn("AB_FINGERS", entry)
        self.assertIn("finger_down_count", entry)
        self.assertIn("ab_apply_pinch", entry)
        self.assertIn("pinch_play", entry)
        self.assertIn("ab_watch_level", entry)
        self.assertIn("LEVEL-PRE", entry)
        self.assertIn("LEVEL %s", entry)
        self.assertIn("shape_bad", entry)
        self.assertIn("plat_log_step", entry)
        plat = (GAME / "nano" / "plat_nano.c").read_text()
        self.assertIn("sync.txt", plat)
        self.assertIn("plat_log_step", plat)
        self.assertNotIn("log_disk", plat)
        self.assertNotIn("hb_fs_write_parts", plat)
        self.assertIn("vfpv4-d16", (GAME / "nano" / "Makefile").read_text())
        self.assertIn("__ZNK3lua8LuaTable8getTableEPKc", (GAME / "tools" / "recomp.py").read_text())
        self.assertIn("rt_check_align8", (GAME / "tools" / "recomp.py").read_text())
        self.assertIn("vfp_d16", (GAME / "tools" / "recomp.py").read_text())
        self.assertEqual(ab.RECIPE, 21)
        self.assertIn("1||(n_!=v_)", (GAME / "tools" / "recomp.py").read_text())
        self.assertIn("0&&(n_==v_)", (GAME / "tools" / "recomp.py").read_text())
        self.assertIn("uint64_t F_b15c4(", (GAME / "tools" / "recomp.py").read_text())
        self.assertIn("bh > 280.f", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("cam_xy_baked", entry)
        self.assertIn("ingamePausePageScroll", (GAME / "rt" / "ab_entry.c").read_text())
        self.assertIn("ab_apply_bird_cam", entry)
        self.assertIn("lua_gc", entry)
        self.assertIn("panel_check", host)
        self.assertIn("panel_ground_pixels", host)
        self.assertIn("panel_max_void_run", host)
        self.assertIn("panel_vis_drawn", host)
        self.assertIn("ppm_pixel_diff", host)
        self.assertIn("dump_and_check", host)
        self.assertIn("AB_VOID_COL_MAX", host)
        self.assertIn("vis_wx", entry)
        self.assertIn("AB_PHYS_VIS", entry)
        self.assertIn("ab_cam_write_table", entry)
        self.assertIn("play_ground_b", entry)
        self.assertIn("lvl_castle_px", entry)
        self.assertIn("ab_clamp_user_view", entry)
        self.assertNotIn("if (mn < 0.32f) mn = 0.32f", entry)
        self.assertIn("panel_check_pigs", host)
        self.assertIn("pinch_out_pigs_ok", host)
        self.assertIn("pinch-out needs pan", host)
        self.assertIn("in_bg_draw", entry)
        self.assertIn("bg-tile dest", entry)
        self.assertIn("AB_FLAT_PCT_MAX", host)
        self.assertIn("#define AB_FLAT_PCT_MAX 40", host)
        self.assertIn("panel_flat_pct", host)
        self.assertIn("panel_ground_variance", host)
        self.assertIn("AB_GROUND_VAR_MIN", host)
        self.assertIn("fe_ground_wrap", entry)
        self.assertIn("PVRTC-black", entry)
        self.assertIn("fe_sky_quad", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("theme-ground", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("fe_note_sheet", (GAME / "rt" / "rt_imports.c").read_text())
        self.assertIn("fe_ui_tex", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("pending_ui", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("extend_sky_fill", host)
        self.assertIn("pix_fill_sky", host)
        self.assertIn("pix_painted_sky", host)
        self.assertIn("MENU wedges", host)
        self.assertIn("be_set_theme_ground", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("theme_ground_be", (GAME / "host" / "be_soft.c").read_text())
        self.assertIn("collapsed UV", (GAME / "host" / "be_soft.c").read_text())
        self.assertNotIn("!already", (GAME / "host" / "be_soft.c").read_text())
        self.assertIn("splash_check", host)
        self.assertIn("splash_probe", host)
        self.assertIn("pause_label_check", host)
        self.assertIn("PAUSED", host)
        self.assertIn("ab_show_pause_page", host)
        self.assertIn("Best-fit", (GAME / "rt" / "rt_core.c").read_text())
        self.assertIn("fe_rot_sky_quad", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("StaticBlockTheme", entry)
        self.assertIn("GROUND_BLOCK_*_04", entry)
        self.assertIn("in_bg_draw && srcw >= 479u", entry)
        self.assertIn("fe_wrap_hold", entry)
        self.assertIn("user_scale shrinks them", entry)
        self.assertIn("no dist/hash smear", host)
        self.assertIn("Clamp fill-sky to the last painted sky pixel", host)
        self.assertIn("x0 < 60", (GAME / "host" / "be_soft.c").read_text())
        self.assertIn("128u * 1024u", (GAME / "rt" / "rt_core.c").read_text())
        self.assertIn("guest skip %u keep hole %u", (GAME / "rt" / "rt_core.c").read_text())
        self.assertIn("8 KB+ Image/Box2D objects must stay in-guest", (GAME / "rt" / "rt_core.c").read_text())
        self.assertIn("fe_in_level", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("ab_lua_collect", (GAME / "rt" / "ab_entry.c").read_text())
        self.assertIn("title sunburst", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("slides PAUSED off the portrait-right", entry)
        self.assertIn("xf_ui_full", entry)
        self.assertIn("xf_keep_2x2", entry)
        self.assertIn("skip=%d", entry)
        self.assertIn("atlas UVs", entry)
        self.assertIn("fe_atlas_uv", (GAME / "rt" / "fngl.h").read_text())
        self.assertIn("along the rotated quad", entry)
        self.assertIn("thin fillRect", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("y ~380-420", host)
        self.assertIn("panel_out_fill_bars", host)
        self.assertIn("matching clean reference", host)
        self.assertIn("3 or more px", host)
        self.assertIn("sky texture", host)
        self.assertIn("panel_slope_variance", host)
        self.assertIn("AB_SLOPE_VAR_MIN", host)
        self.assertIn("CLAMP_TO_EDGE", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("be_sky_explains_run", (GAME / "rt" / "fngl.h").read_text())
        self.assertIn("fe_tex_clamp", (GAME / "rt" / "fngl.h").read_text())
        self.assertIn("rotated static", host)
        self.assertIn("iPod log.txt", (GAME / "nano" / "fn_nano.c").read_text())
        self.assertIn("texture POT pad: no memory", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("guest heap exhausted", (GAME / "rt" / "rt_core.c").read_text())
        self.assertIn("8% of the panel", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("Stop before x=188", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("pix_pig_green", host)
        self.assertIn("pan_to_right_limit", host)
        self.assertIn("play_level_camera", host)
        self.assertIn("Level8", host)
        self.assertIn("Level21", host)
        self.assertIn("pig_n", entry)
        self.assertIn("AB_PHYS_PIG", entry)
        self.assertIn("leftLimit", entry)
        self.assertIn("ab_latch_user_from_play", entry)
        self.assertIn("ab_touch_near_bird", entry)
        self.assertIn("ab_icall_patch", entry)
        self.assertIn("rt_icall_patch", (GAME / "rt" / "rt.h").read_text())
        self.assertIn("rt_icall_after", (GAME / "rt" / "rt.h").read_text())
        self.assertIn("ab_icall_after", entry)
        self.assertIn("0.655f", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("240 * 432 * 9 / 10", host)
        self.assertIn("pix < 20000", host)
        self.assertIn("luaerr", (GAME / "rt" / "rt_imports.c").read_text())
        self.assertIn("ab_ready_slingshot", entry)
        self.assertIn("birdReady", entry)
        self.assertIn("0x128d20u", entry)
        self.assertIn("b2Step", entry)
        nano_be = (GAME / "nano" / "be_nano.c").read_text()
        self.assertIn("tex fail", nano_be)
        self.assertIn("tex upload null: solid fallback", nano_be)
        self.assertIn("w > 512", (GAME / "host" / "be_soft.c").read_text())
        self.assertIn("col[3] <= 0.04f", (GAME / "host" / "be_soft.c").read_text())
        self.assertIn("pan_on || pinch_on", (GAME / "rt" / "ab_entry.c").read_text())
        self.assertIn("b < 24", (GAME / "host" / "main.c").read_text())
        self.assertIn("ab_force_draw_cam", (GAME / "rt" / "ab_entry.c").read_text())
        self.assertIn("0x7eb0u", (GAME / "rt" / "ab_entry.c").read_text())
        self.assertIn("lime=%d", (GAME / "host" / "main.c").read_text())
        self.assertIn("ab_live_span", (GAME / "rt" / "ab_entry.c").read_text())
        self.assertIn("ab_cam_xf_ctx", (GAME / "rt" / "ab_entry.c").read_text())
        self.assertIn("ab_cam_xf_write", (GAME / "rt" / "ab_entry.c").read_text())
        self.assertIn("ab_patch_returned_xf", (GAME / "rt" / "ab_entry.c").read_text())
        self.assertIn("uint32_t r0, uint32_t ret", (GAME / "rt" / "rt.h").read_text())
        self.assertIn("ab_unpause", (GAME / "rt" / "ab_entry.c").read_text())
        self.assertIn("area > 2000.f", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("0x130268", (GAME / "tools" / "recomp.py").read_text())
        self.assertIn("0x130a48", (GAME / "tools" / "recomp.py").read_text())
        self.assertIn("0x6c1b4", (GAME / "tools" / "recomp.py").read_text())
        self.assertIn("0x6c1b4, 0xb15c4", (GAME / "tools" / "recomp.py").read_text())
        self.assertIn("ab_masked_xf_save", entry)
        self.assertIn("MaskedImage::add", entry)
        self.assertIn("showTutorials", entry)
        self.assertIn("dismiss_tutorial", host)
        self.assertIn("loadedObjects", entry)
        self.assertIn("ab_drop_stale_render_objects", entry)
        self.assertIn("ro-stale", entry)
        self.assertIn("ab_on_create_object", entry)
        self.assertIn("ro-clear", entry)
        self.assertIn("rt_heap_poisoned", (GAME / "rt" / "rt_core.c").read_text())
        self.assertIn("AB_HEAP_NOREUSE", (GAME / "rt" / "rt_core.c").read_text())
        self.assertIn("AB_ISOLATE_LEVEL", host)
        self.assertIn("in_sprite_draw", entry)
        self.assertIn("ab_on_create_object();", (GAME / "tools" / "recomp.py").read_text())
        self.assertIn("ab_after_remove_physics", entry)
        self.assertIn("Removing physics", (GAME / "rt" / "rt_imports.c").read_text())
        self.assertIn("p + 36u", entry)
        self.assertIn("alloc = chosen + size - need", (GAME / "rt" / "rt_core.c").read_text())
        self.assertIn("!texh[bound_tex]", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("tex_named[bound_tex]) en_tex = 1", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("fe_skip_fbo_blit", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("ab_disarm_fbo_masks", entry)
        self.assertIn("lvl_pig_minx - 80.f", entry)
        self.assertIn("if (r0 == 0x0DE1u) return 0", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("ro-after-physics", entry)
        self.assertIn("masked-reset", entry)
        self.assertIn("0x1315d8u", entry)
        self.assertIn("y < -20.f", entry)
        self.assertIn("fe_drop_fullscreen", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("StoneBlock8", (GAME / "rt" / "ab_entry.c").read_text())
        self.assertIn("panel-select", host)
        self.assertIn("level_wanted", host)
        self.assertIn("L_b1860", (GAME / "tools" / "recomp.py").read_text())
        self.assertIn("0&&n_", (GAME / "tools" / "recomp.py").read_text())
        self.assertIn("cam_xy_baked", (GAME / "rt" / "ab_entry.c").read_text())
        self.assertIn("in_masked_add", entry)
        self.assertIn("xf + 48u", (GAME / "rt" / "ab_entry.c").read_text())
        self.assertNotIn("col[0] = 0.40f; col[1] = 0.93f", (GAME / "host" / "be_soft.c").read_text())

    def test_convert_png_is_pvr_4444(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("ab_convert_assets", GAME / "tools" / "convert_assets.py")
        ca = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ca)
        td = tempfile.TemporaryDirectory()
        src = Path(td.name) / "MENU.png"
        dst = Path(td.name) / "out.png"
        ca.write_png(src, [(255, 0, 0, 255), (0, 255, 0, 128), (0, 0, 255, 64), (255, 255, 0, 0)], 2, 2)
        w, h, _ = ca.convert_png(src, dst, 0, 512)
        raw = dst.read_bytes()
        self.assertEqual(w, 2)
        self.assertEqual(h, 2)
        self.assertEqual(raw[:4], b"\x34\x00\x00\x00")  # PVR header size 52
        self.assertIn(b"PVR!", raw[44:48])
        src3 = Path(td.name) / "odd.png"
        dst3 = Path(td.name) / "odd.pvr"
        ca.write_png(src3, [(255, 0, 0, 255)] * 9, 3, 3)
        w3, h3, _ = ca.convert_png(src3, dst3, 0, 512)
        raw3 = dst3.read_bytes()
        self.assertEqual(ca.pvr_header(raw3)["w"], 4)
        self.assertEqual(ca.pvr_header(raw3)["h"], 4)
        self.assertEqual(w3, 3)
        self.assertEqual(h3, 3)
        td.cleanup()

    def test_convert_skips_audio(self):
        src = (GAME / "tools" / "convert_assets.py").read_text()
        self.assertIn(".wav", src)
        self.assertIn(".mp3", src)
        self.assertIn("INGAME_KEEP = 256", src)
        self.assertIn("UI_KEEP = 512", src)
        self.assertIn("keep_for", src)
        self.assertIn("HQ_KEEP", src)
        self.assertIn('"MENU"', src)
        self.assertIn('"SPLASHES"', src)
        self.assertIn('"TUTORIALS"', src)
        self.assertIn("needed_shift", src)
        self.assertIn("texscale", src)
        self.assertIn('dest / "texscale"', src)
        self.assertIn("write_pvr_4444", src)
        self.assertIn("write_pvr_4444(dst, w, h, rgba)", src)
        self.assertIn("515100", src)
        self.assertIn("pad_rgba_pot", src)
        self.assertIn('.with_suffix(".pvr")', src)
        self.assertIn('raw.replace(b".png", b".pvr")', src)
        gl = (GAME / "rt" / "fngl.c").read_text()
        self.assertIn("fe_os_alloc", gl)
        self.assertIn("tex_convert_fail", gl)
        self.assertIn("solid fallback", gl)
        self.assertIn("One POT 16-bit store", gl)
        self.assertIn("AB_REPRO_UNINIT", gl)
        self.assertIn("tex_drv_charge", gl)
        self.assertIn("fe_enter_lowmem", gl)
        self.assertIn("check log.txt", gl)
        entry = (GAME / "rt" / "ab_entry.c").read_text()
        self.assertIn("ab_level_min_scale", entry)
        imports = (GAME / "rt" / "rt_imports.c").read_text()
        self.assertIn("silent_wav", imports)
        self.assertIn("silent stub", imports)
        self.assertIn("#define MAX_FILES 256", imports)
        self.assertIn("tex_file_shift", imports)
        self.assertIn("fe_queue_tex_shift", imports)
        self.assertIn("fe_pending_tex_shift", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("uv_div", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("static int pot2(int n)", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("pixel / POT", (GAME / "rt" / "fngl.c").read_text())


@unittest.skipUnless(IPA, "set NANO7KIT_ANGRY_IPA to the Angry Birds 1.0 ipa to run")
class PayloadTests(unittest.TestCase):
    def test_payload_is_the_1_0_build(self):
        facts = ab.inspect_payload(Path(IPA))
        self.assertTrue(facts["ok"], facts.get("problems"))
        self.assertEqual(facts["sha256"], ab.EXE_SHA256)
        self.assertEqual(facts["cryptid"], 0)


ABHOST = ROOT / "build" / "angrybirds_rc" / "recomp" / "abhost"
ABDATA = ROOT / "build" / "angrybirds_rc" / "data"


@unittest.skipUnless(ABHOST.is_file() and (ABDATA / "guest.img").is_file(),
                     "needs a built abhost and converted data/")
class PhysicsHarnessTests(unittest.TestCase):
    def test_levels_1_4_bodies_rest_on_ground(self):
        import subprocess
        env = os.environ.copy()
        env["AB_PHYS_TEST"] = "1"
        env["AB_PHYS_WARM"] = "200"
        env["AB_PHYS_STEPS"] = "300"
        out = ROOT / "build" / "angrybirds_rc" / "phys-test"
        out.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            [str(ABHOST), str(ABDATA), str(ABDATA / "guest.img"), str(out), "1", "0"],
            env=env, capture_output=True, text=True, timeout=300)
        err = (r.stderr or "") + (r.stdout or "")
        self.assertIn("AB_PHYS_TEST PASS", err, err[-6000:])
        self.assertEqual(r.returncode, 0, err[-3000:])
        self.assertIn("Level1", err)
        self.assertIn("Level4", err)
        self.assertIn("guest block 2031616", err)
        self.assertNotIn("guest heap exhausted", err)

    def test_menu_png_fits_guest_with_address_ordered_first_fit(self):
        """Same 2 MB first-fit heap and splash->INGAME->MENU load as the iPod.

        HQ MENU 512 is 505x255 RGBA = 515100. Address-ordered fpush coalesces
        so that decode fits in-guest when AB_NO_OUTSIDE forces it. Play still
        puts 256 KB+ on the OS heap so lua/Box2D keep the 2 MB block.
        """
        import subprocess
        env = os.environ.copy()
        env["AB_NO_OUTSIDE"] = "1"
        out = ROOT / "build" / "angrybirds_rc" / "oom-test"
        out.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            [str(ABHOST), str(ABDATA), str(ABDATA / "guest.img"), str(out), "300", "0"],
            env=env, capture_output=True, text=True, timeout=180)
        err = (r.stderr or "") + (r.stdout or "")
        self.assertEqual(r.returncode, 0, err[-4000:])
        self.assertIn("Loading image data/data/MENU.pvr", err)
        self.assertNotIn("guest heap exhausted", err)
        self.assertNotIn("out of guest memory", err)

    def test_playthrough_splash_menu_level1_slingshot(self):
        import subprocess
        env = os.environ.copy()
        env["AB_PLAY_TEST"] = "1"
        out = ROOT / "build" / "angrybirds_rc" / "play-test"
        out.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            [str(ABHOST), str(ABDATA), str(ABDATA / "guest.img"), str(out), "1", "1"],
            env=env, capture_output=True, text=True, timeout=300)
        err = (r.stderr or "") + (r.stdout or "")
        self.assertIn("AB_PLAY_TEST PASS", err, err[-8000:])
        self.assertEqual(r.returncode, 0, err[-4000:])
        self.assertIn("MENU.pvr", err)
        self.assertNotIn("outside alloc 515100", err)
        self.assertNotIn("no memory to convert", err)
        self.assertIn("flying=1", err)
        self.assertIn("before rest=1", err)
        self.assertNotIn("guest heap exhausted", err)
        self.assertNotIn("out of guest memory", err)

    def test_every_shipped_level_draws_objects(self):
        import subprocess
        levels = sorted(
            int(p.stem[5:]) for p in ABDATA.glob("Level*.lua") if p.stem[5:].isdigit())
        self.assertGreaterEqual(len(levels), 2)
        env = os.environ.copy()
        env["AB_LEVELS_TEST"] = "1"
        env["AB_PHYS_WARM"] = "180"
        env["AB_LEVELS_SETTLE"] = "90"
        out = ROOT / "build" / "angrybirds_rc" / "levels-test"
        out.mkdir(parents=True, exist_ok=True)
        # Fresh guest heap per batch: one process through many levels packs the
        # 2 MB block. Each batch is a reboot-sized session like picking a
        # level from the map. If a sequential pair (L14 then L15, same theme)
        # leaves sky tiles behind, retry each level alone the way the map does.
        def run_range(lo, hi):
            env["AB_LEVELS_MIN"] = str(lo)
            env["AB_LEVELS_MAX"] = str(hi)
            r = subprocess.run(
                [str(ABHOST), str(ABDATA), str(ABDATA / "guest.img"), str(out), "1", "0"],
                env=env, capture_output=True, text=True, timeout=180)
            err = (r.stderr or "") + (r.stdout or "")
            return r, err

        for i in range(0, len(levels), 5):
            batch = levels[i:i + 5]
            r, err = run_range(batch[0], batch[-1])
            if "AB_LEVELS_TEST PASS" not in err or r.returncode != 0:
                for lv in batch:
                    r2, err2 = run_range(lv, lv)
                    self.assertIn("AB_LEVELS_TEST PASS", err2, err2[-6000:])
                    self.assertEqual(r2.returncode, 0, err2[-3000:])
                    self.assertNotIn("guest heap exhausted", err2)
                    self.assertNotIn("out of guest memory", err2)
            else:
                self.assertNotIn("guest heap exhausted", err)
                self.assertNotIn("out of guest memory", err)
        self.assertIn(1, levels)
        self.assertIn(2, levels)

    def test_gesture_frame_fixed_pinch_and_l1_l2_l3(self):
        import subprocess
        env = os.environ.copy()
        env["AB_GESTURE_TEST"] = "1"
        out = ROOT / "build" / "angrybirds_rc" / "gesture-test"
        out.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            [str(ABHOST), str(ABDATA), str(ABDATA / "guest.img"), str(out), "1", "0"],
            env=env, capture_output=True, text=True, timeout=480)
        err = (r.stderr or "") + (r.stdout or "")
        self.assertIn("AB_GESTURE_TEST PASS", err, err[-8000:])
        self.assertEqual(r.returncode, 0, err[-4000:])
        self.assertIn("LEVEL Level1", err)
        self.assertIn("LEVEL Level2", err)
        self.assertIn("LEVEL Level3", err)
        self.assertIn("LEVEL Level8", err)
        self.assertIn("LEVEL Level21", err)
        self.assertIn("pinch L2", err)
        self.assertRegex(err, r"fe_off=0\.0,0\.0 fe_scale=0\.750,0\.900")
        self.assertTrue((out / "panel-l1.ppm").is_file(), err[-2000:])
        self.assertTrue((out / "panel-l1-pan.ppm").is_file(), err[-2000:])
        self.assertTrue((out / "panel-l2.ppm").is_file(), err[-2000:])
        self.assertTrue((out / "panel-l2-pan.ppm").is_file(), err[-2000:])
        self.assertTrue((out / "panel-l3-pan.ppm").is_file(), err[-2000:])
        self.assertTrue((out / "panel-l8-pan.ppm").is_file(), err[-2000:])
        self.assertTrue((out / "panel-l21-pan.ppm").is_file(), err[-2000:])
        self.assertTrue((out / "panel-pause.ppm").is_file(), err[-2000:])
        self.assertIn("lime=", err)
        self.assertNotIn("guest heap exhausted", err)
        self.assertNotIn("out of guest memory", err)

    def test_device_path_l1_l2_l3_pinch_and_log(self):
        import subprocess
        env = os.environ.copy()
        env.pop("AB_REPRO_UNINIT", None)
        env["AB_DEVICE_TEST"] = "1"
        env["AB_DEVICE_CH1"] = "3"
        out = ROOT / "build" / "angrybirds_rc" / "device-test"
        out.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            [str(ABHOST), str(ABDATA), str(ABDATA / "guest.img"), str(out), "1", "0"],
            env=env, capture_output=True, text=True, timeout=400)
        err = (r.stderr or "") + (r.stdout or "")
        self.assertIn("AB_DEVICE_TEST PASS", err, err[-8000:])
        self.assertEqual(r.returncode, 0, err[-4000:])
        self.assertIn("LEVEL Level1", err)
        self.assertIn("LEVEL Level2", err)
        self.assertIn("LEVEL Level3", err)
        self.assertIn("pinch-in", err)
        self.assertTrue((out / "panel-l1.ppm").is_file())
        self.assertTrue((out / "panel-l2.ppm").is_file())
        self.assertTrue((out / "panel-l3.ppm").is_file())
        self.assertTrue((out / "panel-l2-out.ppm").is_file())
        self.assertTrue((out / "panel-l2-in.ppm").is_file())
        self.assertTrue((out / "panel-l2-pan.ppm").is_file())
        self.assertTrue((out / "panel-menu.ppm").is_file())
        self.assertTrue((out / "panel-pause.ppm").is_file())
        self.assertIn("pinch-out", err)
        self.assertIn("pan-pigs", err)
        self.assertIn("lime=", err)
        self.assertTrue((out / "panel-l1-pan.ppm").is_file())
        self.assertTrue((out / "panel-l8-pan.ppm").is_file())
        self.assertTrue((out / "panel-l21-pan.ppm").is_file())
        self.assertNotIn("no memory to convert", err)
        self.assertNotIn("POT pad: no memory", err)
        log = (out / "docs" / "log.txt").read_text(errors="replace")
        self.assertIn("LEVEL Level1", log)
        self.assertIn("LEVEL Level2", log)
        self.assertTrue((out / "docs" / "sync.txt").is_file())
        self.assertNotIn("guest heap exhausted", err)
        self.assertIn("profile=low", err)
        self.assertIn("heap free 3865672", err)
        self.assertIn("largest 2097152", err)
        env["AB_OS_HEAP"] = "clean"
        outc = ROOT / "build" / "angrybirds_rc" / "device-test-clean"
        outc.mkdir(parents=True, exist_ok=True)
        rc = subprocess.run(
            [str(ABHOST), str(ABDATA), str(ABDATA / "guest.img"), str(outc), "1", "0"],
            env=env, capture_output=True, text=True, timeout=400)
        errc = (rc.stderr or "") + (rc.stdout or "")
        self.assertIn("AB_DEVICE_TEST PASS", errc, errc[-8000:])
        self.assertEqual(rc.returncode, 0, errc[-4000:])
        self.assertIn("profile=clean", errc)
        self.assertIn("heap free 6014264", errc)
        self.assertIn("largest 4194304", errc)
        self.assertNotIn("no memory to convert", errc)
        self.assertNotIn("POT pad: no memory", errc)

    def test_busy_os_heap_shows_red_check_log_screen(self):
        import subprocess
        env = os.environ.copy()
        env.pop("AB_REPRO_UNINIT", None)
        env["AB_OS_HEAP"] = "busy"
        env["AB_LOWMEM_TEST"] = "1"
        out = ROOT / "build" / "angrybirds_rc" / "lowmem-test"
        out.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            [str(ABHOST), str(ABDATA), str(ABDATA / "guest.img"), str(out), "1", "0"],
            env=env, capture_output=True, text=True, timeout=60)
        err = (r.stderr or "") + (r.stdout or "")
        self.assertIn("AB_LOWMEM_TEST PASS", err, err[-4000:])
        self.assertEqual(r.returncode, 0, err[-2000:])
        self.assertIn("check log.txt", err)
        self.assertIn("profile=busy", err)
        self.assertTrue((out / "panel-lowmem.ppm").is_file(), err[-2000:])
        ppm = (out / "panel-lowmem.ppm").read_bytes()
        self.assertTrue(ppm.startswith(b"P6 240 432 255\n"))
        pix = ppm.split(b"\n", 1)[1]
        self.assertGreater(len(pix), 240 * 3)
        self.assertGreater(pix[0], 50)
        self.assertLess(pix[1], 40)
        self.assertLess(pix[2], 40)
