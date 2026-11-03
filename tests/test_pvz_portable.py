"""PvZ Portable (PvZ-Portable engine + your own PvZ GOTY files): memory plan, pre-flight constants, data rules,
CLI routing, mkrelocapp --all-alloc, source hygiene (no game files, no em dashes)."""
import os
import struct
import tempfile
import unittest
from pathlib import Path

from host import pvz_portable as pp

ROOT = Path(__file__).resolve().parents[1]
GAME = ROOT / "games" / "pvz_portable"
GOTY = os.environ.get("NANO7KIT_PVZ_GOTY")


class FitTests(unittest.TestCase):
    def test_fresh_boot_depends_on_where_the_image_goes(self):
        fit = pp.heap_fit(6_710_886, 4_194_304)                     # Probe 6.4M / 4.0M
        self.assertEqual(fit["best"]["verdict"], "FITS")
        self.assertEqual(fit["best"]["block"], pp.BLOCK_MAX)
        self.assertTrue(pp.TRIS_NEEDED["1-1"] <= fit["best"]["budget"]["1-1"] <= pp.BUDGET_MAX)   # baked
        self.assertEqual(fit["worst"]["verdict"], "NO_FIT")           # image out of the 4.0 MB block

    def test_short_heap_refuses_like_the_app(self):
        self.assertEqual(pp.heap_fit(5_000_000, 4_000_000)["best"]["verdict"], "NO_FIT")
        self.assertEqual(pp.heap_fit(8_000_000, 2_500_000)["best"]["verdict"], "NO_FIT")

    def test_arena_is_never_below_the_dry_run_need(self):
        self.assertGreaterEqual(pp.BLOCK_MIN, pp.ARENA_NEED["1-1"])
        self.assertGreaterEqual(pp.BLOCK_MAX, pp.ARENA_NEED["1-1..1-10"])

    def test_constants_match_the_nano_source(self):
        src = (GAME / "nano" / "pvz_nano.c").read_text()
        for name in ("BLOCK_MAX", "BLOCK_MIN", "AFTER_MIN", "SLOP"):
            self.assertRegex(src, r"#define %s\s+0x%Xu" % (name, getattr(pp, name)))
        self.assertIn('#define DATA_DIR    "%s"' % pp.FAT_DATA, src)
        self.assertLess(src.index("NOT ENOUGH MEMORY"), src.index("void* block = hb_os_alloc(size);"))
        self.assertLess(src.index("n7_arena_init(block, size)"), src.index("__init_array_start; f < __init_array_end"))
        plat = (GAME / "engine" / "nano7dev" / "hb" / "n7plat_hb.c").read_text()
        self.assertIn('#define DATA_DIR "%s"' % pp.FAT_DATA, plat)
        main = (GAME / "engine" / "nano7dev" / "n7main.cpp").read_text()
        self.assertIn("#define N7_BYTES_PER_TRI %du" % pp.BYTES_PER_TRIANGLE, main)
        self.assertIn("#define N7_TRI_MIN %du" % pp.BUDGET_MIN, main)
        self.assertIn("#define N7_TRI_MAX %du" % pp.BUDGET_MAX, main)

    def test_notice_allocates_nothing(self):
        self.assertNotIn("malloc", (GAME / "nano" / "notice.c").read_text())

    def test_plan_without_payload_refuses(self):
        lines = pp.plan_lines(None, None, None)
        self.assertEqual(lines[-1], "PLAN_REFUSED")

    def test_plan_counts_the_pack(self):
        lines = pp.plan_lines(None, 6_710_886, 4_194_304, on_disk=["App%d" % i for i in range(11)], slot_b=True)
        self.assertTrue(any("with PvZ Portable 12 of 16" in l for l in lines))
        full = pp.plan_lines(None, 6_710_886, 4_194_304, on_disk=["App%d" % i for i in range(16)], slot_b=True)
        self.assertTrue(any("over the 16-app cap" in l for l in full))

    def test_wrong_folder_refused(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "properties").mkdir()
            (Path(d) / "main.pak").write_bytes(b"not the game")
            facts = pp.inspect_payload(Path(d))
            self.assertFalse(facts["ok"])
            self.assertTrue(any("sha256" in p for p in facts["problems"]))

    @unittest.skipUnless(GOTY, "set NANO7KIT_PVZ_GOTY to your own PvZ GOTY folder")
    def test_your_files(self):
        self.assertTrue(pp.inspect_payload(Path(GOTY))["ok"])


class PortTableTests(unittest.TestCase):
    def test_registered_as_its_own_large_gl_port(self):
        from host import ports
        from host.pvz_fit import PVZ_NAMES

        self.assertIn(pp.NAME, ports.LARGE_GL)
        self.assertIn(pp.NAME, ports.NEEDS_PAYLOAD)
        self.assertEqual(ports.PORT_DATA[pp.NAME]["fat"], pp.FAT_DATA)
        self.assertNotIn(pp.NAME.lower().replace(" ", ""), PVZ_NAMES)   # not the iOS research path

    def test_sounds_are_copied_for_this_port_only(self):
        from host import ports

        self.assertFalse(ports.skip_data_file(Path("sounds/plant.wav"), pp.NAME))
        self.assertTrue(ports.skip_data_file(Path("sounds/plant.wav"), "Mario 64"))
        self.assertTrue(ports.skip_data_file(Path(".DS_Store"), pp.NAME))

    def test_aliases(self):
        from host.app_catalog import ALIASES

        for a in ("pvzportable", "pvz-portable"):
            self.assertEqual(ALIASES[a], pp.NAME)
        self.assertNotIn("pvz", ALIASES)

    def test_plist_name_matches(self):
        import plistlib

        info = plistlib.loads((GAME / "nano" / "app" / "Info.plist").read_bytes())
        self.assertEqual(info["CFBundleName"], pp.NAME)
        self.assertEqual(info["HBAppKind"], 2)


class RelocTests(unittest.TestCase):
    def test_all_alloc_is_opt_in(self):
        src = (ROOT / "nanoapps" / "tools" / "mkrelocapp.py").read_text()
        self.assertIn('all_alloc = "--all-alloc" in args', src)
        self.assertIn('return s["sh_type"] != "SHT_NOBITS" if all_alloc else s["sh_type"] == "SHT_PROGBITS"', src)
        mk = (GAME / "nano" / "app" / "Makefile").read_text()
        self.assertIn("MKRELOC_FLAGS := --all-alloc", mk)
        self.assertIn("$(MKRELOC_FLAGS)", (ROOT / "nanoapps" / "sdk" / "hb_app.mk").read_text())

    def test_thumb_to_arm_stub_needs_no_relocation(self):
        src = (ROOT / "nanoapps" / "tools" / "mkrelocapp.py").read_text()
        self.assertIn("(words[0] & 0xFFFF) == 0x4778", src)


class InitArrayRelocTests(unittest.TestCase):
    """the .init_array words (R_ARM_TARGET1) must be rebased; earlier builds left them at base 0 (reboot on launch)."""

    def _build(self, tmp, pad):
        import shutil
        import subprocess
        import sys
        cc = shutil.which("arm-none-eabi-gcc")
        if not cc:
            self.skipTest("arm-none-eabi-gcc not installed")
        try:
            import elftools  # noqa: F401
        except ImportError:
            self.skipTest("pyelftools not installed")
        src = Path(tmp) / "t.c"
        src.write_text("int hits;\n__attribute__((constructor)) static void c1(void) { hits++; }\n"
                       "__attribute__((constructor)) static void c2(void) { hits += 2; }\n"
                       "__attribute__((section(\".text.entry\"), used)) int payload_entry(void) { return hits; }\n")
        elf = Path(tmp) / "t.elf"
        subprocess.run([cc, "-mthumb", "-mcpu=cortex-a5", "-mfloat-abi=soft", "-Os", "-nostdlib", "-Wl,-q",
                        "-Wl,-e,payload_entry", "-Wl,-Ttext,0", "-o", str(elf), str(src)], check=True)
        out = Path(tmp) / "t.hbapp"
        args = [sys.executable, str(ROOT / "nanoapps" / "tools" / "mkrelocapp.py"), "--all-alloc"]
        subprocess.run(args + (["--pad-sectors"] if pad else []) + [str(elf), str(out), "payload_entry"],
                       check=True, capture_output=True)
        from elftools.elf.elffile import ELFFile
        with open(elf, "rb") as f:
            ia = ELFFile(f).get_section_by_name(".init_array")
            start, n = ia["sh_addr"], ia["sh_size"] // 4
        data = out.read_bytes()
        h = struct.unpack("<6I", data[:24])
        relocs = set(struct.unpack("<%dI" % h[4], data[24 + h[2]:24 + h[2] + 4 * h[4]]))
        return data, start, n, relocs

    def test_constructors_are_rebased(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, start, n, relocs = self._build(tmp, False)
            self.assertGreaterEqual(n, 2)
            for i in range(n):
                self.assertIn(start + 4 * i, relocs)

    def test_pad_sectors(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, *_ = self._build(tmp, True)
            self.assertEqual(len(data) % 4096, 0)

    def test_app_uses_the_fix_and_breadcrumbs(self):
        mk = (GAME / "nano" / "app" / "Makefile").read_text()
        self.assertIn("MKRELOC_FLAGS := --all-alloc --pad-sectors", mk)
        self.assertIn("pvz_crumb.c", mk)
        crumb = (GAME / "nano" / "app" / "pvz_crumb.c").read_text()
        self.assertNotIn("printf(", crumb.split("*/", 1)[1])   # printf saves VFP registers: not before the FPU attach
        entry = (GAME / "nano" / "app" / "pvz_entry.c").read_text()
        self.assertLess(entry.index("pvz_step_reset();"), entry.index("return (void*)frame_callback;"))
        nano = (GAME / "nano" / "pvz_nano.c").read_text()
        self.assertLess(nano.index('n7p_step_a("constructor"'), nano.index("(*f)();"))
        self.assertIn("hello.txt", nano)


class V33ConfigAndFilesTests(unittest.TestCase):
    """An earlier build died on the iPod because the NANO7_* switches were all off there (newlib's getenv), so the first reanim
    went to the XML compiler and aborted. Getenv is defined by n7cfg.c on the iPod, the device refuses to
    compile definitions at run time, and lookups of files that were never shipped are answered from files.lst."""
    ENG = GAME / "engine"

    def test_getenv_is_the_fixed_table_on_the_ipod(self):
        cfg = (self.ENG / "nano7dev" / "n7cfg.c").read_text()
        self.assertIn('char* getenv(const char* name) { return n7_getenv(name); }', cfg)
        self.assertIn('#ifndef N7_HOST_ENV', cfg)
        self.assertIn('{ "NANO7_N7R", "1" }', cfg)
        self.assertNotIn("'-Dgetenv=n7_getenv'", (self.ENG / "build_arm.py").read_text())   # libstdc++ undefines it
        self.assertIn("n7cfg.c", (self.ENG / "build_arm.py").read_text())

    def test_engine_checks_its_switches_and_never_compiles(self):
        main = (self.ENG / "nano7dev" / "n7main.cpp").read_text()
        self.assertIn("engine: config switches", main)
        self.assertIn("n7_fatal(\"the NANO7_* switches are not set", main)
        patch = (self.ENG / "pvzp-nano7.patch").read_text()
        self.assertIn("nano7 refused to compile", patch)
        self.assertIn("no cache32 on the device", patch)

    def test_file_index(self):
        import shutil, subprocess
        cc = shutil.which("cc") or shutil.which("gcc")
        if not cc:
            self.skipTest("no host C compiler")
        with tempfile.TemporaryDirectory() as t:
            c = Path(t) / "t.c"
            c.write_text(r"""
#include <stdio.h>
#include <string.h>
#include <stdint.h>
void n7x_build(const char*, uint32_t); int n7x_absent(const char*, const char*); int n7x_state(void);
int main(void) {
  const char* l = "reanim/sunflower.n7t\t10\tab\nimages/Blank.n7t\t5\tcd\n";
  n7x_build(l, (uint32_t)strlen(l));
  const char* d = "/Apps/Data/PvZ";
  printf("%d %d%d%d%d %d%d%d%d%d\n", n7x_state(),
    n7x_absent("/Apps/Data/PvZ/reanim/SUNFLOWER.n7t", d), n7x_absent("/Apps/Data/PvZ//images/blank.n7t", d),
    n7x_absent("/Apps/Data/PvZ/./reanim", d), n7x_absent("/Apps/Data/PvZ/userdata/users.dat", d),
    n7x_absent("/Apps/Data/PvZ/REANIM_SUNFLOWER.png", d), n7x_absent("/Apps/Data/PvZ/particles/x.png", d),
    n7x_absent("/Apps/Data/PvZ/main.pak", d), n7x_absent("/Apps/Data/PvZ/log.txt", d) == 0,
    n7x_absent("/Other/reanim/x", d) == 0);
  return 0; }
""")
            exe = Path(t) / "t"
            subprocess.run([cc, "-O1", "-I", str(self.ENG / "nano7dev"), "-o", str(exe), str(c),
                            str(self.ENG / "nano7dev" / "n7index.c")], check=True)
            out = subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout.split()
        # built; shipped files and folders (any case, // and ./) present; userdata never answered;
        # unshipped probes absent; root .txt and paths outside the data folder never answered
        self.assertEqual(out, ["1", "0000", "11111"])

    def _repack(self):
        import importlib, sys
        sys.path.insert(0, str(GAME / "repack"))
        try:
            return importlib.import_module("repack")
        finally:
            sys.path.pop(0)

    def test_v34_hashed_layout_index_matches_repacker(self):
        import shutil, subprocess
        cc = shutil.which("cc") or shutil.which("gcc")
        if not cc:
            self.skipTest("no host C compiler")
        rp = self._repack()
        rels = ["reanim/sunflower.n7t", "images/blank.n7t", "data/lawnstrings.txt"]
        salt = rp.dxx_salt(rels)
        hdr = "0042007b001b003200030000"
        lst = "#n7x 2 salt=%d\n" % salt + "".join("%s\t%d\tab%s\n" % (r, 10 + i, "\t" + hdr if r.endswith(".n7t") else "")
                                              for i, r in enumerate(rels))
        with tempfile.TemporaryDirectory() as t:
            c = Path(t) / "t.c"
            c.write_text(r"""
#include <stdio.h>
#include <string.h>
#include <stdint.h>
void n7x_build(const char*, uint32_t); int n7x_hashed(void);
int n7x_find(const char*, const char*, char*, size_t, uint32_t*, const uint16_t**);
int n7x_head(const uint16_t*, void*, uint32_t);
int main(void) {
  const char* l = "LST";
  n7x_build(l, (uint32_t)strlen(l));
  const char* d = "/Apps/Data/PvZ"; char dev[160]; uint32_t sz = 0; const uint16_t* h = 0; unsigned char b[16];
  int r = n7x_find("/Apps/Data/PvZ/Reanim/SunFlower.n7t", d, dev, sizeof dev, &sz, &h);
  int hn = n7x_head(h, b, 16);
  printf("%d %d %s %u %d %02x%02x ", n7x_hashed(), r, dev, sz, hn, b[4], b[5]);
  int f = n7x_find("/Apps/Data/PvZ/reanim", d, 0, 0, 0, 0), s = n7x_find("/Apps/Data/PvZ/sounds/plant.wav", d, 0, 0, 0, 0);
  int t = n7x_find("/Apps/Data/PvZ/data/lawnstrings.txt", d, dev, sizeof dev, &sz, &h) == 1 && h == 0;
  printf("%d %d %d\n", f, s, t);
  return 0; }
""".replace("LST", lst.replace("\t", "\\t").replace("\n", "\\n")))
            exe = Path(t) / "t"
            subprocess.run([cc, "-O1", "-I", str(self.ENG / "nano7dev"), "-o", str(exe), str(c),
                            str(self.ENG / "nano7dev" / "n7index.c")], check=True)
            out = subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout.split()
        want = "/Apps/Data/PvZ/" + rp.dxx_path("reanim/sunflower.n7t", salt)
        # hashed layout; the file found in any case at the repacker's d/XX/HHHHHHHH with its size and header;
        # its folder is a folder; an unshipped sound is absent without an OS call; a non-texture has no header
        self.assertEqual(out, ["1", "1", want, "10", "16", "4200", "2", "0", "1"])

    def test_v34_repacker_ships_no_sounds_in_83_names(self):
        rp = self._repack()
        dev = [("reanim\\Zombie_Head.n7t", 0, b"N7T1" + bytes([1, 0, 1, 0, 1, 0, 1, 0, 3, 0, 0, 0]) + b"pq"), ("sounds\\plant.ogg", 0, b"OggS"),
               ("sounds\\groan.au", 0, b".snd"), ("data\\LawnStrings.txt", 0, b"hi")]
        with tempfile.TemporaryDirectory() as t:
            props = Path(t) / "props"; props.mkdir(); (props / "resources.xml").write_bytes(b"<r/>")
            root = Path(t) / "out"
            n, nbytes, fails = rp.write_nano_tree(str(root), dev, str(props), None, 22050, t)
            files = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
            lst = (root / "files.lst").read_text().splitlines()
        self.assertEqual(n, 3)
        self.assertTrue(lst[0].startswith("#n7x 2 salt="))
        self.assertFalse(any("sounds/" in l for l in lst))
        self.assertIn("reanim/zombie_head.n7t\t18\t", "\n".join(lst))
        self.assertTrue(any(l.startswith("reanim/zombie_head.n7t") and l.endswith("\t000100010001000100030000") for l in lst))
        import re
        for f in files:
            if f != "files.lst":
                self.assertRegex(f, r"^d/[0-9A-F]{2}/[0-9A-F]{8}$")

    def test_v34_display_hud_and_sound_switches(self):
        a = (self.ENG / "nano7dev" / "n7gl_dev_a.inc").read_text()
        e = (self.ENG / "nano7dev" / "n7engine.cpp").read_text()
        snd = (self.ENG / "nano7dev" / "n7snd.cpp").read_text()
        cfg = (self.ENG / "nano7dev" / "n7cfg.c").read_text()
        patch = (self.ENG / "pvzp-nano7.patch").read_text()
        self.assertIn('"letterbox.txt"', a)                 # fullscreen by default, the older letterboxed picture on request
        self.assertIn("sN7ScaleX = ky; sN7ScaleY = kx;", a)
        self.assertIn('n7p_exists_rel("debug_overlay.txt") ? 1 : 0', e)   # overlay off by default
        self.assertIn('{ "NANO7_NO_SOUND", "1" }', cfg)
        self.assertIn('n7_getenv("NANO7_NO_SOUND")', snd)
        self.assertIn("loading: sounds skipped (silent build)", patch)
        for crumb in ("menu: showing the main menu", "menu: button clicked", "board: MakeNewBoard", "board: first frame drawn"):
            self.assertIn(crumb, patch)

    def test_v35_intro_skipped_pool_guarded_small_draw_frames(self):
        patch = (self.ENG / "pvzp-nano7.patch").read_text()
        main = (self.ENG / "nano7dev" / "n7main.cpp").read_text()
        hb = (self.ENG / "nano7dev" / "hb" / "n7plat_hb.c").read_text()
        # an earlier build rebooted in the first frame of the first-run intro (game mode 72): the pool was never initialised
        self.assertIn('if (getenv("NANO7_INTRO") && mApp->IsFirstTimeAdventureMode()', patch)
        self.assertEqual(patch.count("if (!mCausticImage) return;"), 2)
        # the menu's draw chain reached about 11 KB of stack: vertex lists and triangle groups off the stack
        self.assertIn("enum { MAX_STACK_VERTS = 12 };", patch)
        self.assertEqual(patch.count("PVZP_TRIANGLE_GROUP(aTriangleGroup);"), 2)
        # per-frame crumbs for a new board, a line each second, sync.txt = the last log line
        self.assertIn("frame: board frame starts", main)
        self.assertIn("nano7 each second:", main)
        self.assertIn("write_sync();", hb.split("void n7p_log_flush(void)")[1].split("}")[0] + "}")

    def test_v36_baked_plants_zombies_protected_classes_budget(self):
        patch = (self.ENG / "pvzp-nano7.patch").read_text()
        main = (self.ENG / "nano7dev" / "n7main.cpp").read_text()
        gl = (self.ENG / "nano7dev" / "n7glcommon.c").read_text()
        devb = (self.ENG / "nano7dev" / "n7gl_dev_b.inc").read_text()
        bake = (self.ENG / "nano7" / "n7bake.cpp").read_text()
        arm = (self.ENG / "build_arm.py").read_text()
        # plants, mowers and zombies are drawn from one baked picture each, made on the iPod from the .n7t parts
        self.assertIn("Nano7BakedDraw(this, g)", patch)
        self.assertIn("mN7Pixels", patch)
        self.assertIn("'nano7/n7bake.cpp'", arm)                      # ARM state (VFP), not Thumb
        self.assertIn("gPlantDefs[i].mReanimationType", bake)
        self.assertIn("gZombieDefs[i].mReanimationType", bake)
        self.assertIn('n7p_exists_rel("nobake.txt")', bake)
        self.assertIn("kBakeBytesCap = 320u * 1024u", bake)              # was 512 KB
        self.assertIn("static uint8_t sRing[kRing]", bake)              # fixed buffers, not the arena
        self.assertNotIn("calloc(", bake)
        self.assertIn("if (img->mN7Pixels)", devb)
        # the sun, coins, UI and baked plants are protected; baked zombies are class 3; the plan has hysteresis
        self.assertIn('p.compare(0, 10, "reanim/sun") == 0', devb)
        self.assertIn('p.compare(0, 11, "nano7bakez/") == 0) return 3;', devb)
        self.assertIn("#define N7_CLS_N 5", gl)
        self.assertIn("++s_calm >= 90", gl)
        self.assertIn("uint32_t reserve = want > s_prot_now", gl)
        # the budget follows the measured OS heap only (textures do not come out of it), at most 600; calibrate.txt
        self.assertIn("#define N7_TRI_MAX 600u", main)
        self.assertNotIn("sFree0 > grown", main)
        self.assertIn('n7p_exists_rel("calibrate.txt")', main)
        self.assertIn("calibrate: the last frame drew a triangles", main)

    def test_v37_still_bakes_capped_and_released(self):
        patch = (self.ENG / "pvzp-nano7.patch").read_text()
        main = (self.ENG / "nano7dev" / "n7main.cpp").read_text()
        bake = (self.ENG / "nano7" / "n7bake.cpp").read_text()
        r = (self.ENG / "nano7" / "n7r.cpp").read_text()
        posix = (self.ENG / "nano7dev" / "n7plat_posix.c").read_text()
        nano = (GAME / "nano" / "pvz_nano.c").read_text()
        # one still picture per look: no zombie frame steps by default, walk/walk2/eat share anim_walk, no blinking
        self.assertNotIn("kZombieStep", bake)
        self.assertIn('n7p_exists_rel("zombiewalk.txt")', bake)
        self.assertIn("r->mFrameStart == di.walk2 || r->mFrameStart == di.eat", bake)
        self.assertIn('"anim_tongue"', bake)
        self.assertIn("if (Nano7BakeOn())", patch)
        self.assertIn("AttachmentUpdateAndSetMatrix(ti->mAttachmentID, ov)", bake)   # the head held still too
        # hard caps on count and GL bytes (+ per-texture cost), LRU deletes textures, made after the frame is drawn
        self.assertIn("kBakeMax = 64", bake)
        self.assertIn("kTexCost = 1024u", bake)
        self.assertIn("ReleaseTextures();   // kept for reuse", bake)
        self.assertIn("BakeQueued();", bake)
        self.assertIn("DataArrayTryToGet(sQ[i].id)", bake)
        self.assertIn("kOsBakeFloor", bake)
        # low OS heap stops with a message; every texture released at level end and on quit; names saved often
        self.assertIn("#define N7_OS_STOP (640u * 1024u)", main)
        self.assertIn("Nano7QuitFree(gLawnApp); LogMem(\"quit\")", main)
        self.assertIn("app->Remove3DData(im); tex++;", r)
        self.assertIn("bakes %u kept of %u made", main)
        self.assertIn("NANO7_TEX_OS", posix)                      # dry run: OS cost per live texture
        self.assertIn("if (++frames % 60 == 0) gll_save();", nano)

    def test_v38_saves_award_bakes_still_shooters_and_sun(self):
        patch = (self.ENG / "pvzp-nano7.patch").read_text()
        main = (self.ENG / "nano7dev" / "n7main.cpp").read_text()
        bake = (self.ENG / "nano7" / "n7bake.cpp").read_text()
        cache = (self.ENG / "nano7dev" / "n7cache_dev.inc").read_text()
        hb = (self.ENG / "nano7dev" / "hb" / "n7plat_hb.c").read_text()
        # saves: one whole-file OS write (no ofstream / std::filesystem), logged; users.dat written; old user1.dat read
        self.assertIn("return Nano7WriteFile(theFileName, theData, theDataLen);", patch)
        self.assertIn("Nano7MkDirs(std::string(theDir));", patch)
        self.assertIn("int r = n7p_write_whole(rel, theData, (uint32_t)theLen);", main)
        self.assertIn('"nano7 save %s: %lu bytes %s (%u ms)"', main)
        self.assertIn("gLawnApp->mPlayerInfo->LoadDetails();", main)
        self.assertIn("gLawnApp->mProfileMgr->Save();", main)
        self.assertIn("if (mProfileMgr) mProfileMgr->Save();", patch)
        self.assertIn("nano7 load userdata/user%u.dat", patch)
        self.assertIn("int n7p_mkdir_abs(const char* p)", hb)
        # award: cache pictures are made at the frame end, never inside the draw; crumbs around level end and award
        self.assertIn("if (!sCur.on) return false;", bake)
        self.assertIn("BakeCacheQueued();", bake)
        self.assertNotIn("e = Bake(r, key, skey, kind, &f2)", bake)
        self.assertIn("void Nano7CacheBakeBuild(int theType", cache)
        self.assertIn("Nano7CacheDrawBegin((int)theReanimationType, theTrackName, (int)theDrawVariation);", patch)
        self.assertIn('"level end: board killed"', patch)
        self.assertIn('"award screen: making it (a = award type)"', patch)
        self.assertIn('Nano7WatchFrames("award: level award picked up (a = coin type)"', patch)
        self.assertIn('"scene: changed (a = scene: 1 menu 2 intro 3 playing 5 award)"', main)
        # shooters keep their still idle picture; the sun is still, with no outer glow
        self.assertIn('if (!n7Still) aHeadReanim->SetFramesForLayer("anim_shooting");', patch)
        self.assertIn('aSunReanim->AssignRenderGroupToTrack("Sun3", RENDER_GROUP_HIDDEN);', patch)
        self.assertIn("aSunReanim->mAnimRate = 0.0f;", patch)

    def test_v39_global_effect_trim_saves_log_and_stops(self):
        patch = (self.ENG / "pvzp-nano7.patch").read_text()
        fx = (self.ENG / "nano7" / "n7fx.cpp").read_text()
        main = (self.ENG / "nano7dev" / "n7main.cpp").read_text()
        hb = (self.ENG / "nano7dev" / "hb" / "n7plat_hb.c").read_text()
        cfg = (self.ENG / "nano7dev" / "n7cfg.c").read_text()
        pool = (self.ENG / "nano7" / "poolcap.cpp").read_text()
        arm = (self.ENG / "build_arm.py").read_text()
        # one whitelist for the whole game: particle systems are never made, effect animations never drawn or preloaded
        self.assertIn("if (!Nano7FxParticleAllowed(theEffect))", patch)
        self.assertIn("if (!Nano7FxReanimShown((int)mReanimationType))", patch)
        self.assertIn("if (theIsPreloading && Nano7FxReanimIsEffect((int)theReanimType))", patch)
        self.assertIn("if (!Nano7FxParticleAllowed(i))", patch)
        for keep in ("PARTICLE_AWARD_PICKUP_ARROW", "PARTICLE_SEED_PACKET_PICK", "REANIM_SUN", "REANIM_LAWNMOWER", "gZombieDefs", "gPlantDefs"):
            self.assertIn(keep, fx)
        # burned or mowed zombies vanish; banners are plain; the mower slides still; explosions are one flash
        self.assertIn("DieWithLoot();  // nano7 v39: a burned zombie vanishes", patch)
        self.assertIn("DieWithLoot();  // nano7 v39: a mowed zombie vanishes", patch)
        self.assertIn("if (Nano7FxOn()) mReanimType = ReanimationType::REANIM_NONE;", patch)
        self.assertIn("if (!Nano7FxOn())  // nano7 v39: no Final Wave banner animation", patch)
        self.assertIn("if (Nano7FxOn()) aMowerReanim->mAnimRate = 0.0f;", patch)
        self.assertIn("Nano7FxDraw(g);", patch)
        self.assertIn("nano7 effects left out:", fx)
        # pictures of pruned groups (the achievements screen background) draw as nothing instead of crashing
        self.assertGreaterEqual(patch.count("{ Nano7FxMissingImage(); return; }"), 20)
        self.assertIn("if (IMAGE_CHALLENGE_BACKGROUND) g->DrawImage(IMAGE_CHALLENGE_BACKGROUND, 0, 0);", patch)
        # saves are logged to their own file that the log ring cannot scroll away
        self.assertIn('n7p_write_whole("saves.txt", sSaveLog, sSaveLogN);', main)
        # clear stops instead of reboots: arena full, pool full; smaller particle pools, more plants (roof pots)
        self.assertIn("out of memory: the game arena is full", hb)
        self.assertIn("stopped so it does not reboot", pool)
        self.assertIn("particle systems=74,particles=96", cfg)
        self.assertIn("plants=135", cfg)
        self.assertIn("'nano7/n7fx.cpp'}", arm)
        # trimmed particle effects are still made, as empty systems (never null to the callers)
        self.assertNotIn("return nullptr;\n\t}\n#endif\n\treturn mEffectSystem->mParticleHolder->AllocParticleSystem", patch)
        self.assertIn("if (aParticleIce)  // nano7 v39", patch)
        # no dialog waits inside a frame on the iPod (Crazy Dave's seed-slot offer froze the dry run at 2-4)
        self.assertIn("would wait for an answer: answered no", patch)
        # big animation blocks: no throw, release unused animations and bakes, retry, then a clear stop; Zombie.reanim kept
        n7r = (self.ENG / "nano7" / "n7r.cpp").read_text()
        self.assertIn("static void* N7rAlloc(size_t n, const std::string& what)", n7r)
        self.assertIn("block = (char*)N7rAlloc(total, theFileName);", n7r)
        self.assertEqual(n7r.count("i != (unsigned)ReanimationType::REANIM_ZOMBIE"), 2)

    def test_repacker_keeps_alpha_only_trailing_underscore_images(self):
        import importlib, sys
        sys.path.insert(0, str(GAME / "repack"))
        try:
            rp = importlib.import_module("repack")
        finally:
            sys.path.pop(0)
        files = {"images\\blank_.gif": b"", "images\\zombatar_accessory_.png": b"", "images\\a.png": b"",
                 "images\\a_.png": b""}
        plans = rp.plan_images(files, '<Image id="BLANK" path="blank" /><Image id="A" path="a" />')
        self.assertEqual(plans["images/blank"], (None, "images\\blank_.gif"))   # IMAGE_BLANK, on the main menu
        self.assertEqual(plans["images/a"], ("images\\a.png", "images\\a_.png"))
        self.assertNotIn("images/zombatar_accessory", plans)                      # not asked for by resources.xml


class HygieneTests(unittest.TestCase):
    def test_no_game_files_in_git_tree(self):
        bad = [p for p in GAME.rglob("*") if p.is_file() and p.suffix.lower() in
               {".pak", ".n7t", ".n7r", ".wav", ".ogg", ".png", ".jpg", ".reanim", ".xml", ".hbapp"}]
        self.assertEqual(bad, [])

    def test_engine_fetched_not_vendored(self):
        self.assertFalse((GAME / "engine" / "src").exists())
        self.assertFalse((GAME / "engine" / "nano7dev" / "third_party").exists())
        self.assertEqual(len(pp.ENGINE_COMMIT), 40)
        for url, sha in pp.FETCH.values():
            self.assertTrue(url.startswith("https://"))
            self.assertEqual(len(sha), 64)

    def test_credits(self):
        self.assertIn("wszqkzqk", (GAME / "engine" / "LICENSE-PvZ-Portable").read_text()
                      + (ROOT / "host" / "app_catalog.py").read_text())

    def test_no_em_dashes_in_new_text(self):
        for p in [ROOT / "host" / "pvz_portable.py", GAME / "nano" / "pvz_nano.c", GAME / "nano" / "app" / "Makefile",
                  GAME / "nano" / "app" / "pvz_crumb.c", GAME / "nano" / "app" / "pvz_entry.c",
                  GAME / "engine" / "nano7dev" / "n7index.c", GAME / "engine" / "nano7dev" / "n7host_files.c",
                  GAME / "engine" / "nano7dev" / "audit_files.py", GAME / "engine" / "nano7dev" / "n7cfg.c",
                  GAME / "repack" / "repack.py"]:
            self.assertNotIn("\u2014", p.read_text(), p)


if __name__ == "__main__":
    unittest.main()
