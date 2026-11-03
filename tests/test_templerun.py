#!/usr/bin/env python3
"""Temple Run (olievans123/nano-templerun) from the user's 1.0 ipa. Host only."""
import hashlib
import os
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host import templerun as tr
from host.app_catalog import reset_catalog_cache, resolve_name
from host.apps import add_apps, pack_app_names, remove_apps
from host.compat import Refused
from host.const import TESTED_DISK_SIZE, artifact
from host.device import Nano, annotate_size
from host.ports import TEMPLE_CMD, extra_data_dir, missing_help

ROOT = Path(__file__).resolve().parent.parent


def thin_macho(subtype=9, cryptid=0, pad=b"") -> bytes:
    lc = struct.pack("<5I", 0x21, 20, 4096, 8192, cryptid)
    hdr = struct.pack("<7I", 0xFEEDFACE, 12, subtype, 2, 1, len(lc), 0)
    return hdr + lc + pad


def fat_macho(slices) -> bytes:
    out = bytearray(struct.pack(">II", 0xCAFEBABE, len(slices)))
    off = 4096
    table, body = [], bytearray()
    for subtype, blob in slices:
        table.append(struct.pack(">5I", 12, subtype, off + len(body), len(blob), 12))
        body += blob
    for t in table:
        out += t
    out += b"\0" * (off - len(out))
    return bytes(out + body)


def make_app(td: Path, exe: bytes, bundle=tr.BUNDLE_ID, version="1.0") -> Path:
    app = td / "Payload" / "TempleRun.app"
    app.mkdir(parents=True)
    (app / "Info.plist").write_bytes(
        plistlib.dumps(
            {"CFBundleIdentifier": bundle, "CFBundleVersion": version, "CFBundleExecutable": "TempleRun"}
        )
    )
    (app / "TempleRun").write_bytes(exe)
    return app


class MachOTests(unittest.TestCase):
    def test_thin_picks_armv7_from_fat(self):
        v6 = thin_macho(subtype=6, pad=b"six")
        v7 = thin_macho(subtype=9, pad=b"seven")
        self.assertEqual(tr.thin_armv7(fat_macho([(6, v6), (9, v7)])), v7)

    def test_thin_input_passes_through(self):
        v7 = thin_macho()
        self.assertEqual(tr.thin_armv7(v7), v7)

    def test_no_armv7_refused(self):
        with self.assertRaises(Refused) as ctx:
            tr.thin_armv7(fat_macho([(6, thin_macho(subtype=6))]))
        self.assertIn("armv7", str(ctx.exception))

    def test_not_macho_refused(self):
        with self.assertRaises(Refused):
            tr.thin_armv7(b"\x7fELF" + b"\0" * 64)

    def test_cryptid(self):
        self.assertEqual(tr.cryptid(thin_macho(cryptid=0)), 0)
        self.assertEqual(tr.cryptid(thin_macho(cryptid=1)), 1)


class PayloadTests(unittest.TestCase):
    def test_encrypted_refused(self):
        with tempfile.TemporaryDirectory() as raw:
            app = make_app(Path(raw), fat_macho([(9, thin_macho(cryptid=1))]))
            facts = tr.inspect_payload(app)
            self.assertFalse(facts["ok"])
            self.assertIn("encrypted", facts["problems"][0])
            with self.assertRaises(Refused):
                tr.assert_payload(app)

    def test_other_version_refused_with_needed_hash(self):
        with tempfile.TemporaryDirectory() as raw:
            app = make_app(Path(raw), fat_macho([(9, thin_macho())]), version="1.5")
            facts = tr.inspect_payload(app)
            self.assertFalse(facts["ok"])
            msg = facts["problems"][0]
            self.assertIn("Temple Run 1.5", msg)
            self.assertIn("only translates Temple Run 1.0", msg)
            self.assertIn("91d85b35", msg)
            self.assertNotIn("\u2014", msg)

    def test_matching_slice_accepted(self):
        v7 = thin_macho()
        with tempfile.TemporaryDirectory() as raw:
            app = make_app(Path(raw), fat_macho([(9, v7)]))
            with mock.patch.object(tr, "ARMV7_SHA256", hashlib.sha256(v7).hexdigest()):
                facts = tr.assert_payload(app)
        self.assertTrue(facts["ok"])
        self.assertEqual(facts["armv7_size"], len(v7))

    def test_ipa_wrong_bundle_refused(self):
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            app = make_app(td / "s", fat_macho([(9, thin_macho())]), bundle="com.popcap.PvZ")
            ipa = td / "x.ipa"
            with zipfile.ZipFile(ipa, "w") as zf:
                for p in app.rglob("*"):
                    zf.write(p, p.relative_to(td / "s").as_posix())
            with self.assertRaises(Refused) as ctx:
                tr.inspect_payload(ipa)
            self.assertIn("com.popcap.PvZ", str(ctx.exception))


class PlanTests(unittest.TestCase):
    def test_missing_payload(self):
        lines = tr.plan_lines(None)
        self.assertEqual(lines[-1], "PLAN_REFUSED")
        self.assertTrue(any("--ipa" in l for l in lines))

    def test_pack_count_with_nogrow(self):
        ten = ["Game Boy", "Probe", "Blocks", "Calculator", "Mario 64", "Notes",
               "Chess", "Wallet", "Screenshot", "Books"]
        v7 = thin_macho()
        with tempfile.TemporaryDirectory() as raw:
            app = make_app(Path(raw), fat_macho([(9, v7)]))
            with mock.patch.object(tr, "ARMV7_SHA256", hashlib.sha256(v7).hexdigest()), \
                    mock.patch("host.compat.which", return_value="/usr/bin/x"):
                lines = tr.plan_lines(app, on_disk=ten, slot_b=True, nogrow=True)
        text = "\n".join(lines)
        self.assertIn("10 now -> 11 after; cap 16", text)
        self.assertIn("B active", text)
        self.assertIn("apps remove TempleRun --keep-files", text)
        self.assertEqual(lines[-1], "PLAN_OK")
        self.assertNotIn("\u2014", text)

    def test_over_cap_without_nogrow(self):
        eight = [f"A{i}" for i in range(8)]
        with mock.patch("host.compat.which", return_value="/usr/bin/x"):
            lines = tr.plan_lines(None, on_disk=eight, slot_b=True, nogrow=False)
        self.assertIn("    REFUSED   over the pack cap", lines)

    def test_arena_matches_v25_nano_alloc(self):
        from host import templerun_patch as tp

        self.assertEqual(tr.ARENA_FLOOR, tp.ARENA_FLOOR)
        self.assertEqual(tr.arena_for(8_700_000), tr.ARENA_BYTES)
        self.assertEqual(tr.arena_for(5_500_000, 4 << 20), tr.ARENA_BYTES)
        self.assertEqual(tr.arena_for(5_500_000, 0x328000), 0x320000)
        self.assertEqual(tr.arena_for(5_500_000, 2 << 20), 0)

    def test_parse_size(self):
        self.assertEqual(tr.parse_size("4.0M"), 4 << 20)
        self.assertEqual(tr.parse_size("6.6 MB"), int(6.6 * (1 << 20)))
        self.assertEqual(tr.parse_size("1984K"), 1984 << 10)
        self.assertEqual(tr.parse_size("6.5"), int(6.5 * (1 << 20)))
        self.assertEqual(tr.parse_size("6920601"), 6920601)
        self.assertIsNone(tr.parse_size(None))
        with self.assertRaises(Refused):
            tr.parse_size("lots")

    def test_heap_fit_cases(self):
        M = 1 << 20
        # olievans123's slot B after a first launch (9.0 / 4.0) ran at about 1,300 triangles
        self.assertGreater(tr.heap_fit(int(9.0 * M), 4 * M)["budget"], 1200)
        self.assertEqual(tr.heap_fit(int(10.8 * M), 8 * M)["verdict"], "FITS")
        nik = tr.heap_fit(int(6.6 * M), 4 * M)
        self.assertEqual(nik["verdict"], "FITS_REDUCED")
        self.assertEqual(nik["arena"], tr.ARENA_BYTES)
        self.assertEqual(nik["arena_if_image_in_largest"], 0)
        self.assertEqual(tr.heap_fit(5 * M, 2 * M)["verdict"], "NO_FIT")

    def test_plan_heap_lines(self):
        with mock.patch("host.compat.which", return_value="/usr/bin/x"):
            ok = tr.plan_lines(None, heap_free=int(6.6 * (1 << 20)), largest=4 << 20)
            bad = tr.plan_lines(None, heap_free=5 << 20, largest=2 << 20)
        self.assertTrue(any(l.startswith("    verdict   FITS with a reduced") for l in ok))
        self.assertTrue(any("DOES NOT FIT" in l for l in bad))
        self.assertEqual(bad[-1], "PLAN_REFUSED")
        for line in ok + bad:
            self.assertNotIn("\u2014", line)


def _pvr(w, h, mips, flags=0x319):
    levels, x, y = [], w, h
    for _ in range(mips + 1):
        levels.append(bytes([len(levels) + 1]) * (max(x, 8) * max(y, 8) // 2))
        x, y = max(x // 2, 1), max(y // 2, 1)
    body = b"".join(levels)
    head = struct.pack("<13I", 52, h, w, mips, flags, len(body), 4, 0, 0, 0, 0, 0x21525650, 1)
    return head + body


class PatchTests(unittest.TestCase):
    """templerun_patch: nano7kit's own build-time edits, on synthetic files."""

    def setUp(self):
        from host import templerun_patch as tp

        self.tp = tp
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.repo = Path(self.td.name)
        nano = self.repo / "port" / "nano"
        nano.mkdir(parents=True)
        alloc = "x\n" + tp._ARENA_DECL_OLD + "    uint32_t size = PORT_ARENA_SIZE, freeb = hb_os_heap_free();\n" + tp._ARENA_OLD + "}\n"
        plat = ("a\n" + tp._GLL_INC_OLD + tp._GLL_SAVE_OLD + tp._GLL_NOTE_OLD + tp._SHORT_DECL_OLD
                + tp._FRAME_OLD + tp._EARLY_OLD + tp._GLL_SWEEP_OLD + tp._MANIFEST_OLD + tp._SPLASH_OLD
                + tp._PREFLIGHT_OLD + tp._FAIL_OLD + "\n"
                + tp._OK_OLD + tp._BUDGET_OLD + "b\n")
        (nano / "nano_alloc.c").write_text(alloc)
        (nano / "nano_platform.c").write_text(plat)
        (nano / "Makefile").write_text("x\n" + tp._MAKE_OLD)

    def test_apply_once_and_refuse_twice(self):
        done = self.tp.apply(self.repo, restore=False)
        self.assertEqual(len(done), 3)
        a = (self.repo / "port/nano/nano_alloc.c").read_text()
        p = (self.repo / "port/nano/nano_platform.c").read_text()
        self.assertIn("0x320000U", a)
        self.assertIn("n7_arena_largest = hb_os_heap_largest();", a)
        self.assertIn("fe_option_budget=b;", p)
        self.assertEqual(p.count("nano7kit arena %u bytes"), 2)
        # still a single hb_os_alloc'd arena: no second aux block
        self.assertNotIn("HB_APP_AUX_BLOCK", a.split("/* nano7kit memory edits */", 2)[2].split("n7_arena_size =")[0])
        with self.assertRaises(Refused):
            self.tp.apply(self.repo, restore=False)

    def test_v28_gl_leftovers_swept_and_recorded(self):
        self.tp.apply(self.repo, restore=False)
        p = (self.repo / "port/nano/nano_platform.c").read_text()
        self.assertIn('#include "gl_left.h"', p)
        # leftovers of the previous port are deleted before Temple Run's first texture
        self.assertLess(p.index("gll_sweep();"), p.index('port_crumb("init",0,0);'))
        self.assertLess(p.index("gll_sweep();"), p.index("the previous run's trail"))
        self.assertLess(p.index('gll_init(DATA_DIR "/gl_names.txt");'), p.index("gll_sweep();"))
        self.assertLess(p.index('glGenTextures(1,&t);'), p.index("gll_note(t);"))
        self.assertLess(p.index("static void flush_log(void) {"), p.index("gll_save();"))
        self.assertIn("gl_left", (self.repo / "port/nano/Makefile").read_text())
        g = (self.repo / "port/nano/gl_left.c").read_text()
        self.assertIn("/Apps/Data/FruitNinja/gl_names.txt", g)
        self.assertIn("/Apps/Data/TempleRun/gl_names.txt", g)
        self.assertIn("glDeleteTextures", g)
        self.assertNotIn("malloc", g)
        from host import templerun as tr

        self.assertGreaterEqual(tr.RECIPE, 5)

    def test_v29_preflight_before_the_arena(self):
        """On hardware: 6.27 MB / 4.0 MB at launch, refused at 2.93 / 1.0: the check ran after
        the first malloc (the breadcrumb ring) had made Temple Run's own 3.2 MB arena."""
        self.tp.apply(self.repo, restore=False)
        p = (self.repo / "port/nano/nano_platform.c").read_text()
        a = (self.repo / "port/nano/nano_alloc.c").read_text()
        early = p.index("if(l<0x%Xu||f<%uu)" % (self.tp.NEED_LARGEST, self.tp.NEED_FREE))
        self.assertLess(early, p.index("the previous run's trail"))   # may memalign
        self.assertLess(early, p.index('port_crumb("init",0,0);'))    # first malloc
        # the late check counts the arena already made instead of asking for a second one
        late = p.index('n7_step("pre-flight");')
        self.assertLess(late, p.index('port_crumb("heap0"'))
        self.assertIn("n7_arena_size<0x%Xu||f<%uu" % (self.tp.ARENA_FLOOR, self.tp.NEED_FREE - self.tp.ARENA_FLOOR), p)
        # every step logs free and largest
        for step in ("previous trail", "first allocation", "files.lst", "splash texture", "pre-flight"):
            self.assertIn('n7_step("%s' % step, p)
        self.assertIn("heap free %u largest %u, arena %u", p)
        # refusing keeps the arena the log flush makes small
        self.assertIn("if(!n7_arena_size) n7_arena_cap=0x10000u;", p)
        self.assertIn("if (n7_arena_cap && n7_arena_cap < size) size = n7_arena_cap;", a)
        self.assertIn("if (!sArena && !n7_arena_cap) {", a)
        self.assertNotIn("n7_short_set(f,l);flush_log()", p)
        from host import templerun as tr

        self.assertGreaterEqual(tr.RECIPE, 6)

    def test_budget_txt_knob_clamped(self):
        self.tp.apply(self.repo, restore=False)
        p = (self.repo / "port/nano/nano_platform.c").read_text()
        self.assertIn('plat_read_file("budget.txt",&sz,1)', p)
        self.assertIn("n7_bpt=(v>=2600u&&v<=6000u)?v:3000u;", p)
        self.assertIn("int b=(int)(f/n7_bpt);", p)
        self.assertGreaterEqual(self.tp.BPT_MIN, 2600)
        self.assertEqual(self.tp.BYTES_PER_TRIANGLE, 3000)

    def test_preflight_before_engine_and_notice(self):
        self.tp.apply(self.repo, restore=False)
        p = (self.repo / "port/nano/nano_platform.c").read_text()
        # the check runs before the engine's first allocation and shows the screen after
        self.assertLess(p.index("hb_os_heap_largest();\n                if(l<"), p.index('port_crumb("heap0"'))
        self.assertIn("if(l<0x%Xu||f<%uu)" % (self.tp.NEED_LARGEST, self.tp.NEED_FREE), p)
        self.assertIn("if(failed){ if(n7_short) n7_short_notice(w,h); return; }", p)
        self.assertIn("notice", (self.repo / "port/nano/Makefile").read_text())
        n = (self.repo / "port/nano/notice.c").read_text()
        self.assertNotIn("malloc", n)       # no allocation when memory is short
        from host import templerun as tr

        self.assertEqual(tr.heap_fit(6_710_886, 4_194_304)["verdict"], "TIGHT")
        self.assertEqual(tr.heap_fit(6_000_000, 2_097_152)["verdict"], "NO_FIT")   # music playing

    def test_moved_anchor_refused(self):
        (self.repo / "port/nano/nano_platform.c").write_text("nothing here\n")
        with self.assertRaises(Refused):
            self.tp.apply(self.repo, restore=False)

    def test_shrink_pvr_drops_two_levels(self):
        big = _pvr(1024, 1024, 10)
        self.assertEqual(len(big), 699188)
        small = self.tp.shrink_pvr(big)
        self.assertEqual(len(small), 43828)
        h, w, mips, flags, dlen = struct.unpack_from("<5I", small, 4)
        self.assertEqual((w, h, mips, flags, dlen), (256, 256, 8, 0x319, 43776))
        self.assertEqual(small[52:], big[52 + 524288 + 131072:])

    def test_shrink_leaves_splash_ui_and_256(self):
        for args in ((512, 512, 9), (512, 512, 0, 0x8019), (256, 256, 8)):
            raw = _pvr(*args)
            self.assertEqual(self.tp.shrink_pvr(raw), raw)

    def test_shrink_textures_rewrites_files_lst(self):
        d = self.repo / "nano"
        d.mkdir()
        (d / "wallTexture.pvr").write_bytes(_pvr(1024, 1024, 10))
        (d / "splash.pvr").write_bytes(_pvr(512, 512, 9))
        (d / "a.bksb").write_bytes(b"m" * 7)
        (d / "files.lst").write_text("a.bksb 7\nsplash.pvr 174900\nwallTexture.pvr 699188\n")
        self.assertEqual(self.tp.shrink_textures(d), ["wallTexture.pvr"])
        self.assertEqual((d / "files.lst").read_text(),
                         "a.bksb 7\nsplash.pvr 174900\nwallTexture.pvr 43828\n")
        self.assertEqual(self.tp.shrink_textures(d), [])

    def test_help_and_alias(self):
        self.assertEqual(resolve_name("TempleRun"), "Temple Run")
        text = missing_help("Temple Run")
        self.assertIn(TEMPLE_CMD, text)
        self.assertIn("--plan", text)
        self.assertIn("1.0", text)
        self.assertNotIn("\u2014", text)


class BuildTests(unittest.TestCase):
    """build() with every external step mocked: order, flags, size gate, stamp."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.tmp = Path(self.td.name)
        self.repo = self.tmp / "repo"
        (self.repo / "tools").mkdir(parents=True)
        self.app_dir = self.tmp / "appdir"
        self.app_dir.mkdir()
        self.v7 = thin_macho(pad=b"engine")
        self.app = make_app(self.tmp / "p", fat_macho([(6, thin_macho(subtype=6)), (9, self.v7)]))
        self.calls = []
        self.staged = []

    def _run(self, args, cwd, log):
        self.calls.append([str(a) for a in args])
        a = [str(x) for x in args]
        if a[1].endswith("convert_assets.py"):
            nano = Path(a[2]) / "nano"
            nano.mkdir(parents=True)
            (nano / "files.lst").write_text("engine.img 4\n")
            (nano / "engine.img").write_bytes(b"\0" * 4)
        if a[0] == "make" and any(x.startswith("TR_DIR=") for x in a):
            (self.app_dir / "build").mkdir(exist_ok=True)
            (self.app_dir / "build" / "templerun.hbapp").write_bytes(b"H" * self.hb_size)

    def _patches(self):
        return [
            mock.patch("host.ports.fetch_ios_port", return_value=self.repo),
            mock.patch("host.ports._copy_app_shell", return_value=self.app_dir),
            mock.patch("host.ports._run_make", side_effect=self._run),
            mock.patch("host.ports._stage_hbapp", side_effect=lambda *a: self.staged.append(a)),
            mock.patch.object(tr, "ensure_venv", return_value=Path("/venv/bin/python")),
            mock.patch.object(tr, "build_dir", return_value=self.tmp / "b"),
            mock.patch.object(tr, "ARMV7_SHA256", hashlib.sha256(self.v7).hexdigest()),
            mock.patch.object(tr.templerun_patch, "apply", side_effect=self._apply),
        ]

    def _apply(self, repo, **_kw):
        self.calls.append(["patch", str(repo)])
        return ["port/nano/nano_alloc.c", "port/nano/nano_platform.c"]

    def _build(self, **kw):
        ps = self._patches()
        for p in ps:
            p.start()
        try:
            return tr.build(self.app, **kw)
        finally:
            for p in reversed(ps):
                p.stop()

    def test_build_order_and_flags(self):
        self.hb_size = 688744
        rec = self._build()
        self.assertTrue(rec["built"])
        self.assertEqual((self.repo / "build" / "TempleRun-armv7").read_bytes(), self.v7)
        link = self.repo / "original" / "v1.0" / "Payload" / "TempleRun.app"
        self.assertTrue((link / "Info.plist").is_file())
        steps = [c[0] if c[0] in ("make", "patch") else c[1].rsplit("/", 1)[-1] for c in self.calls]
        self.assertEqual(steps, ["recomp.py", "convert_assets.py", "patch", "make", "make"])
        self.assertEqual(self.calls[0][0], "/venv/bin/python")
        self.assertEqual(self.calls[2], ["patch", str(self.repo)])
        self.assertIn("PYTHON=/venv/bin/python", self.calls[4])
        self.assertTrue(any(x.startswith("RECOMP=") for x in self.calls[3]))
        self.assertEqual(self.staged[0][0], "Temple Run")
        self.assertTrue((self.tmp / "b" / "stamp.txt").is_file())

    def test_oversize_image_not_staged(self):
        self.hb_size = tr.RELAUNCH_IMAGE_LIMIT + 1
        with self.assertRaises(Refused) as ctx:
            self._build()
        self.assertIn("cannot reload", str(ctx.exception))
        self.assertEqual(self.staged, [])

    def test_stamp_skips_rebuild(self):
        self.hb_size = 1000
        self._build()
        self.calls.clear()
        with mock.patch.object(tr, "_staged", return_value=True):
            rec = self._build()
        self.assertFalse(rec["built"])
        self.assertEqual(self.calls, [])

    def test_bad_payload_builds_nothing(self):
        self.hb_size = 1000
        ps = [p for p in self._patches() if getattr(p, "attribute", "") != "ARMV7_SHA256"]
        # real hash: the dummy slice cannot match
        for p in ps:
            p.start()
        try:
            with self.assertRaises(Refused):
                tr.build(self.app)
        finally:
            for p in reversed(ps):
                p.stop()
        self.assertEqual(self.calls, [])


class CliTests(unittest.TestCase):
    def test_ipa_alias_and_plan_need_no_nano(self):
        env = dict(os.environ)
        r = subprocess.run(
            [sys.executable, str(ROOT / "start"), "apps", "add", "TempleRun",
             "--ipa", "/nonexistent/TempleRun.ipa", "--plan"],
            cwd=str(ROOT), capture_output=True, text=True, env=env, timeout=120,
        )
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("payload missing", r.stdout)
        self.assertIn("PLAN_REFUSED", r.stdout)
        self.assertNotIn("Do not disconnect", r.stdout)

    def test_parser_maps_ipa_to_payload(self):
        from host import cli

        ap = cli.build_parser() if hasattr(cli, "build_parser") else None
        if ap is None:
            self.skipTest("no build_parser")
        args = ap.parse_args(["apps", "add", "TempleRun", "--ipa", "x.ipa", "--plan"])
        self.assertEqual(args.payload, "x.ipa")
        self.assertTrue(args.plan)


class AddRemoveWithMarioTests(unittest.TestCase):
    """Slot B: Mario then Temple Run, then remove Temple Run; Mario and the rest stay."""

    NAMES = ("Mario 64", "Temple Run")

    def setUp(self):
        reset_catalog_cache()
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        os.environ["NANO7_STATE_DIR"] = self.td.name
        self.saved = Path(self.td.name) / "saved"
        self.paths = []
        for name in self.NAMES:
            for p in (artifact(f"artifacts/extra/Executables/{name}.hbapp"),
                      artifact(f"artifacts/extra/Icons/{name}.bin"),
                      extra_data_dir(name)):
                self.paths.append(p)
        # Keep a real staged build (if any) out of the way and put it back after.
        for i, p in enumerate(self.paths):
            if p.exists():
                dest = self.saved / str(i)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), str(dest))
        for name, data_rel in (("Mario 64", "manifest.txt"), ("Temple Run", "files.lst")):
            exe = artifact(f"artifacts/extra/Executables/{name}.hbapp")
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.write_bytes(b"HRL1-dummy-" + name.encode())
            icon = artifact(f"artifacts/extra/Icons/{name}.bin")
            icon.parent.mkdir(parents=True, exist_ok=True)
            icon.write_bytes(b"\x00" * 50208)
            data = extra_data_dir(name)
            data.mkdir(parents=True, exist_ok=True)
            (data / data_rel).write_text("dummy\n")
        reset_catalog_cache()

    def tearDown(self):
        for i, p in enumerate(self.paths):
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()
            src = self.saved / str(i)
            if src.exists():
                shutil.move(str(src), str(p))
        reset_catalog_cache()

    def _nano(self, vol):
        return annotate_size(Nano(
            os_name="linux", device="/dev/loop9", raw_device="/dev/loop9",
            partition="/dev/loop9p1", usb_serial="TESTSERIAL", size=TESTED_DISK_SIZE,
            fstype="vfat", mountpoints=[str(vol)], model="iPod",
        ))

    def test_add_then_remove_keeps_mario(self):
        vol = Path(self.td.name) / "vol"
        shutil.copytree(ROOT / "untether" / "artifacts" / "device", vol / "Apps")
        nano = self._nano(vol)

        def fake_mount(_nano, **_kw):
            _nano.mountpoints = [str(vol)]
            return vol

        common = dict(backup=Path(self.td.name) / "missing.img", backup_sha256="a" * 64,
                      skip_wait=True, skip_backup_file=True)
        with mock.patch("host.fat.ensure_fat_mount", side_effect=fake_mount), \
                mock.patch("host.fat.assert_not_hfs"), mock.patch("host.untether.unmount"), \
                mock.patch("host.untether.ensure_fat_mount", side_effect=fake_mount), \
                mock.patch("host.untether.close_finder_ipod_windows", return_value=0), \
                mock.patch("host.apps.check_usb_serial"), \
                mock.patch("host.apps.check_disk_size", return_value={"size": TESTED_DISK_SIZE}), \
                mock.patch("host.apps.check_backup", return_value="a" * 64), \
                mock.patch("host.apps.layout_for_disk_size", return_value=None), \
                mock.patch("host.apps.assert_not_hfs"):
            add_apps(nano, ["Mario"], **common)
            rec = add_apps(nano, ["TempleRun"], **common)
            bpack = vol / "Apps" / "AllApps-B.pack"
            # re-add over an installed Temple Run replaces files, no remove first
            add_apps(nano, ["TempleRun"], **common)
            self.assertTrue(rec["slot_b"])
            self.assertEqual(pack_app_names(bpack.read_bytes()),
                             ["Blocks", "Game Boy", "Probe", "Mario 64", "Temple Run"])
            exe = vol / "Apps" / "Executables" / "Temple Run.hbapp"
            self.assertTrue(exe.is_file())
            self.assertTrue((vol / "Apps" / "Data" / "TempleRun" / "files.lst").is_file())

            remove_apps(nano, ["TempleRun"], keep_files=True, **common)
            self.assertEqual(pack_app_names(bpack.read_bytes()),
                             ["Blocks", "Game Boy", "Probe", "Mario 64"])
            self.assertTrue(exe.is_file())

            add_apps(nano, ["TempleRun"], **common)
            remove_apps(nano, ["Temple Run"], **common)
            self.assertFalse(exe.exists())
            self.assertFalse((vol / "Apps" / "Icons" / "Temple Run.bin").exists())
            self.assertTrue((vol / "Apps" / "Executables" / "Mario 64.hbapp").is_file())
            self.assertEqual(pack_app_names(bpack.read_bytes()),
                             ["Blocks", "Game Boy", "Probe", "Mario 64"])
        self.assertEqual(pack_app_names((vol / "Apps" / "AllApps.pack").read_bytes()),
                         ["Blocks", "Game Boy", "Probe"])


if __name__ == "__main__":
    unittest.main()
