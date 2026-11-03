"""Fruit Ninja recompile: memory plan and pre-flight, archive pieces, CLI routing, source hygiene."""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from host import fruitninja_rc as fr

ROOT = Path(__file__).resolve().parents[1]
GAME = ROOT / "games" / "fruitninja"
IPA = os.environ.get("NANO7KIT_FRUIT_IPA")


class FitTests(unittest.TestCase):
    def test_fresh_boot_fits_reduced(self):
        fit = fr.heap_fit(6_710_886, 4_194_304)          # Probe 6.4M / 4.0M
        self.assertEqual(fit["verdict"], "FITS_REDUCED")
        self.assertEqual(fit["block"], fr.BLOCK_MAX)
        self.assertEqual(fit["policy"], (1, 2))
        self.assertTrue(fr.BUDGET_MIN <= fit["budget"] < fr.TRIS_DRAWN_MAX)

    def test_music_playing_refuses_like_the_app(self):
        self.assertEqual(fr.heap_fit(6_000_000, 2_097_152)["verdict"], "NO_FIT")
        self.assertEqual(fr.heap_fit(5_000_000, 4_000_000)["verdict"], "NO_FIT")

    def test_more_memory_keeps_bigger_textures(self):
        self.assertEqual(fr.heap_fit(7_864_320, 4_194_304)["policy"], (1, 1))

    def test_constants_match_the_nano_source(self):
        src = (GAME / "nano" / "fn_nano.c").read_text()
        for name in ("BLOCK_MAX", "BLOCK_MIN", "GL_KEEP", "AFTER_MIN"):
            self.assertRegex(src, r"#define %s\s+0x%Xu" % (name, getattr(fr, name)))
        self.assertIn("#define BYTES_PER_TRIANGLE %du" % fr.BYTES_PER_TRIANGLE, src)
        self.assertIn("free0 < size + AFTER_MIN", src)            # the pre-flight
        self.assertLess(src.index("short_of_memory(free0"), src.index("block = hb_os_alloc(size);"))
        plat = (GAME / "nano" / "plat_nano.c").read_text()
        self.assertIn("#define FN_CHUNK (%du * 1024u)" % (fr.CHUNK // 1024), plat)

    def test_notice_allocates_nothing(self):
        n = (GAME / "nano" / "notice.c").read_text()
        self.assertNotIn("malloc", n)
        self.assertIn("NOT ENOUGH MEMORY", (GAME / "nano" / "fn_nano.c").read_text())

    def test_plan_without_payload_refuses(self):
        lines = fr.plan_lines(None, None, None)
        self.assertEqual(lines[-1], "PLAN_REFUSED")
        self.assertTrue(all("\u2014" not in l for l in lines))

    def test_plan_counts_the_pack(self):
        lines = fr.plan_lines(None, 6_710_886, 4_194_304, on_disk=["App%d" % i for i in range(11)], slot_b=True)
        self.assertTrue(any("with Fruit Ninja 12 of 16" in l for l in lines))
        full = fr.plan_lines(None, 6_710_886, 4_194_304, on_disk=["App%d" % i for i in range(16)], slot_b=True)
        self.assertTrue(any("over the 16-app cap" in l for l in full))
        noslot = fr.plan_lines(None, 6_710_886, 4_194_304, slot_b=False)
        self.assertTrue(any("slot B is not active" in l for l in noslot))


class ArchiveTests(unittest.TestCase):
    def test_split_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "fileArcive.bin"
            blob = os.urandom(fr.CHUNK * 2 + 1234)
            src.write_bytes(blob)
            out = Path(d) / "data"
            n = fr.split_archive(src, out)
            self.assertEqual(n, 3)
            self.assertEqual((out / "fa" / "size.txt").read_text().strip(), str(len(blob)))
            joined = b"".join((out / "fa" / f"{i:03d}.bin").read_bytes() for i in range(n))
            self.assertEqual(joined, blob)


class CliTests(unittest.TestCase):
    def test_plan_routes_to_fruit_ninja(self):
        from host import cli

        args = SimpleNamespace(apps_cmd="add", names=["FruitNinja"], plan=True, dry_run=False,
                               heap_free="6.4M", largest="4.0M", payload=None, all=False,
                               extras=False, rom=None, device="/nonexistent")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.cmd_apps(args)
        text = buf.getvalue()
        self.assertEqual(rc, 1)                      # no payload given
        self.assertIn("apps add FruitNinja --plan", text)
        self.assertIn("verdict   FITS", text)

    def test_large_gl_and_command(self):
        from host.ports import FRUIT_CMD, LARGE_GL, missing_help

        self.assertIn("Fruit Ninja", LARGE_GL)
        self.assertEqual(FRUIT_CMD, fr.CMD)
        self.assertIn("recompile", missing_help("Fruit Ninja"))


class SourceTests(unittest.TestCase):
    def test_first_party_sources_only(self):
        allowed = {".c", ".h", ".py", ".S", ".sh", ".plist", ""}
        for p in GAME.rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts:
                self.assertIn(p.suffix, allowed, p)
                self.assertLess(p.stat().st_size, 120_000, p)   # code, never game data
                self.assertNotIn("\u2014", p.read_text(encoding="utf-8"), p)

    def test_derived_outputs_ignored(self):
        gi = (ROOT / ".gitignore").read_text()
        self.assertIn("/build/", gi)
        self.assertIn("nanoapps/apps/fruitninja_rc/", gi)
        self.assertIn("untether/artifacts/extra/Executables/Fruit Ninja.hbapp", gi)

    def test_recomp_roots_and_silent_stub(self):
        src = (GAME / "tools" / "recomp.py").read_text()
        self.assertIn("__ZN15IPhoneInterface13InitializeAppEv", src)
        self.assertIn("__Z16ChangeMusicTrackP11IphoneMusic", src)


@unittest.skipUnless(IPA, "set NANO7KIT_FRUIT_IPA to the Fruit Ninja 1.0 ipa to run")
class PayloadTests(unittest.TestCase):
    def test_inspect_payload(self):
        facts = fr.inspect_payload(Path(IPA))
        self.assertTrue(facts["ok"], facts["problems"])
        self.assertEqual(facts["sha256"], fr.EXE_SHA256)


if __name__ == "__main__":
    unittest.main()


class V28Tests(unittest.TestCase):
    """swipes, full panel, and nothing left behind after quitting."""

    def test_fills_the_panel(self):
        src = (GAME / "rt" / "fngl.c").read_text()
        self.assertIn("fe_scale = 0.75f; fe_scale_y = 0.9f; fe_off_x = 0.f; fe_off_y = 0.f;", src)
        entry = (GAME / "rt" / "fn_entry.c").read_text()
        self.assertIn("/ fe_scale_y", entry)               # touch maps back through the same stretch

    def test_several_touch_samples_per_frame(self):
        entry = (GAME / "rt" / "fn_entry.c").read_text()
        self.assertIn("void fn_frame_q(float dt, const fn_tsample *q, int n)", entry)
        # the engine only takes a touch on UpdateApp(dt, 0): one step per sample, second arg 0
        self.assertIn('rt_call("UpdateApp", FN_UpdateApp, F2U(h), 0,', entry)
        self.assertIn("#define FN_MAX_STEPS 4", (GAME / "rt" / "fn_entry.h").read_text())
        nano = (GAME / "nano" / "fn_nano.c").read_text()
        self.assertIn("0x089a5298u", nano)                  # live OS touch list, not only the mailbox
        self.assertIn("fn_frame_q(dt, q, nq);", nano)
        self.assertIn("fn_nano_poll();", (GAME / "nano" / "be_nano.c").read_text())

    def _run_tpick(self, body):
        import shutil, subprocess
        cc = shutil.which("cc") or shutil.which("gcc")
        if not cc:
            self.skipTest("no host C compiler")
        with tempfile.TemporaryDirectory() as t:
            c = Path(t) / "t.c"
            c.write_text("#include <stdio.h>\n#include \"touch_pick.h\"\nint main(void) {\n"
                         "  fn_tpick_t s = {0}; uint32_t now = 1000000;\n" + body + "\n  printf(\"\\n\"); return 0; }\n")
            exe = Path(t) / "t"
            subprocess.run([cc, "-O1", "-Wall", "-Werror", "-I", str(GAME / "nano"), "-o", str(exe), str(c)], check=True)
            return subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout.strip()

    def test_stale_mailbox_cannot_cut_the_next_swipe(self):
        """1.0.1: a lift and the next press can reach the mailbox between two polls; the
        distrusted mailbox must count as fresh once its place changes, and stay distrusted
        while it holds the stale place (no ghost hold after a lift). Polls 10 ms apart."""
        nano = (GAME / "nano" / "fn_nano.c").read_text()
        self.assertIn('#include "touch_pick.h"', nano)
        self.assertIn("fn_tpick(&pick, (uint32_t)plat_time_us(), listed, lx, ly, f.down, f.x, f.y)", nano)
        out = self._run_tpick(r"""
#define P(l, lx, ly, d, mx, my) do { printf("%d", fn_tpick(&s, now, l, lx, ly, d, mx, my)); now += 10000; } while (0)
  P(1, 200, 380, 1, 180, 350);   /* swipe A, list down */
  P(2, 0, 0, 1, 209, 381);       /* only its lift listed, mailbox still down */
  P(0, 0, 0, 1, 209, 381);       /* list drained, mailbox unchanged: stale */
  P(0, 0, 0, 1, 209, 381);
  P(1, 220, 60, 1, 220, 60);     /* lift and swipe B both reached the mailbox: new place */
  P(0, 0, 0, 1, 177, 128);       /* list drained mid-swipe: mailbox is believed */
  P(0, 0, 0, 1, 120, 220);
  P(0, 0, 0, 0, 120, 220);       /* lifted */
  P(0, 0, 0, 0, 120, 220);""")
        # 1 list, 3 stale, 3, 3, 1 list, 2 mailbox, 2, 0 up, 0 (1.0 gave 3 at steps 5 and 6: the swipe cut)
        self.assertEqual(out, "133312200")

    def test_mailbox_stuck_down_after_a_lift_ends(self):
        """Launcher 485bef2 can leave the mailbox down at the last place after a lift, and the
        list may never show the lift. Fruit Ninja must not see one endless press: once nothing
        has confirmed the down mailbox for FN_TP_HOLD_US it reads up, and the next swipe (the
        mailbox moves) is a new press."""
        out = self._run_tpick(r"""
#define P(l, lx, ly, d, mx, my, dt) do { printf("%d", fn_tpick(&s, now, l, lx, ly, d, mx, my)); now += (dt); } while (0)
  P(1, 100, 100, 1, 100, 100, 20000);   /* swipe A from the list */
  P(0, 0, 0, 1, 140, 120, 20000);       /* list empty, mailbox moving: believed */
  P(0, 0, 0, 1, 160, 130, 20000);       /* the finger lifts here; the mailbox stays down */
  P(0, 0, 0, 1, 160, 130, 60000);       /* 20 ms since it last moved: still believed */
  P(0, 0, 0, 1, 160, 130, 60000);       /* 80 ms */
  P(0, 0, 0, 1, 160, 130, 60000);       /* 140 ms */
  P(0, 0, 0, 1, 160, 130, 20000);       /* 200 ms: held unconfirmed, up */
  P(0, 0, 0, 1, 160, 130, 20000);
  P(0, 0, 0, 1, 30, 300, 20000);        /* swipe B: the mailbox moves, a new press */
  P(0, 0, 0, 1, 60, 280, 20000);
  P(1, 90, 260, 1, 90, 260, 20000);""")
        self.assertEqual(out, "12222244221")

    def test_moving_swipe_never_times_out(self):
        """A long swipe that only the mailbox sees (list empty every poll) stays down while
        it moves, even past FN_TP_HOLD_US in total."""
        out = self._run_tpick(r"""
  for (int i = 0; i < 40; i++) { printf("%d", fn_tpick(&s, now, 0, 0, 0, 1, (int16_t)(10 + i), 50)); now += 30000; }
  printf("%d", fn_tpick(&s, now, 0, 0, 0, 0, 49, 50));""")
        self.assertEqual(out, "2" * 40 + "0")

    def test_touch_summary_logged_after_the_first_presses(self):
        """The press lines stop after three; the periodic frame log carries the touch counters."""
        nano = (GAME / "nano" / "fn_nano.c").read_text()
        self.assertIn("++t_presses <= 3", nano)
        self.assertIn('"nano7kit touch: %u presses; polls %u from the OS list (%u of them from the launcher snapshot), %u from the mailbox, %u stale, %u held unconfirmed"', nano)

    def test_front_end_allocations_live_in_the_block(self):
        plat = (GAME / "nano" / "plat_nano.c").read_text()
        self.assertIn("#define GMAGIC", plat)
        self.assertIn("guest_room(", plat)
        self.assertLess(plat.index("guest_room(want)"), plat.index("hb_os_alloc(", plat.index("guest_room(want)")))

    def test_gl_names_recorded_and_swept_before_measuring(self):
        nano = (GAME / "nano" / "fn_nano.c").read_text()
        self.assertLess(nano.index("gll_sweep();"), nano.index("short_of_memory(free0"))
        self.assertIn("gll_note(t)", (GAME / "nano" / "be_nano.c").read_text())
        self.assertIn("gll_note(tex)", (GAME / "nano" / "notice.c").read_text())
        self.assertIn("gl_left", (GAME / "nano" / "Makefile").read_text())
        g = (GAME / "nano" / "gl_left.c").read_text()
        self.assertIn("#define GLL_MAX_SPAN 4096u", g)      # never a wild range
        self.assertNotIn("glIsTexture(", g)                 # not in the SDK: only names we were given
