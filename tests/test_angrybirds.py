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
        self.assertIn("plat_os_alloc", (GAME / "rt" / "fngl.c").read_text())
        self.assertIn("plat_os_alloc", (GAME / "nano" / "be_nano.c").read_text())

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
        self.assertIn("ab_zoom_by", entry)
        self.assertIn("ab_pan_by", entry)
        nano = (GAME / "nano" / "fn_nano.c").read_text()
        self.assertIn("ab_zoom_by", nano)
        self.assertIn("ab_pan_by", nano)
        self.assertIn("HB_BTN_VOL_UP", nano)
        self.assertIn("fe_scale = 0.75f * view_zoom", entry)
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
        self.assertIn("n7soft_shot", host)
        self.assertIn("px = 239 - gy", host)
        self.assertIn("AB_PHYS_TEST", host)
        self.assertIn("ab_phys_snapshot", entry)
        self.assertIn("ab_phys_resting", entry)
        self.assertIn("o->contacts < 1", entry)
        self.assertIn("Level1", host)
        self.assertIn("Level4", host)
        self.assertIn("loadNextLevel", host)
        self.assertIn("300", host)
        self.assertIn("TUTORIAL_OK", host)
        self.assertIn("49.f, 324.f", host)
        self.assertIn("0x128d20u", entry)
        self.assertIn("b2Step", entry)
        nano_be = (GAME / "nano" / "be_nano.c").read_text()
        self.assertIn("tex fail", nano_be)
        self.assertIn("w > 512", (GAME / "host" / "be_soft.c").read_text())

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
        imports = (GAME / "rt" / "rt_imports.c").read_text()
        self.assertIn("silent_wav", imports)
        self.assertIn("silent stub", imports)
        self.assertIn("#define MAX_FILES 256", imports)
        self.assertIn("tex_file_shift", imports)
        self.assertIn("fe_pending_tex_shift", imports)
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
