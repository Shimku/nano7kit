# SPDX-License-Identifier: MIT
"""Checks for Tiny LLM: engine, model file, device image and emulated runs.

The emulated runs load the device .hbapp the way the resident does and run
it in Unicorn with the OS heap at both memory levels the nano 7G shows
(3.9 MB free with a 2 MB largest block, and 6 MB free with a 4 MB block),
plus a missing, a corrupt and a truncated model file and a heap too small
for the model. TINYLLM_FULL=1 also runs one whole story to the end, and
TINYLLM_SHOTS=DIR copies the screenshots there.
"""

from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "nanoapps" / "apps" / "tinyllm"
DOC = ROOT / "docs" / "TINYLLM.md"
HBAPP = APP / "build" / "tinyllm.hbapp"
ELF = APP / "build" / "tinyllm.reloc.elf"
MODEL = ROOT / "untether" / "artifacts" / "extra" / "Data" / "TinyLLM" / "model.bin"
EMU = APP / "emu.py"
TIGHT = "2.0,1.0,0.9"   # 3.9 MB free, 2 MB largest block, before the image is placed
ROOMY = "4.0,1.0,1.0"   # 6 MB free, 4 MB largest block
# Karpathy's stories260K readme, greedy: the int8 engine keeps this opening.
GREEDY_OPENING = ("Once upon a time, there was a little girl named Lily. She loved to play "
                  "outside in the park. One day, she saw a big, red ball.")

sys.path.insert(0, str(ROOT))


def _tool(name):
    found = shutil.which(name)
    if found:
        return found
    for base in (Path("/opt/homebrew/bin"), Path("/usr/local/bin")):
        if (base / name).is_file():
            return str(base / name)
    return None


def _printable(text):
    return "".join(c for c in text if c == "\n" or 32 <= ord(c) < 127)


class TestTinyLlmSource(unittest.TestCase):
    def test_low_memory_check_comes_before_the_allocation(self):
        src = (APP / "tinyllm.c").read_text()
        self.assertIn("NOT ENOUGH", src)
        body = src[src.index("static int load_model"):]
        self.assertLess(body.index("return ST_LOWMEM"), body.index("hb_os_alloc"))
        self.assertIn("hb_os_heap_largest", src)
        # a relaunch frees the previous block before measuring
        init = src[src.index("void hb_raw_init"):]
        self.assertLess(init.index("hb_os_free"), init.index("hb_os_heap_free"))

    def test_buttons(self):
        src = (APP / "tinyllm.c").read_text()
        for b in ("HB_BTN_VOL_UP", "HB_BTN_VOL_DOWN", "HB_BTN_PLAY_PAUSE"):
            self.assertIn(b, src)
        # Home and Sleep/Wake stay with the iPod
        self.assertNotIn("HB_BTN_HOME", src)
        self.assertNotIn("HB_BTN_POWER", src)

    def test_never_writes(self):
        src = (APP / "tinyllm.c").read_text() + (APP / "tllm.c").read_text()
        for call in ("hb_fs_write", "hb_fs_stream", "hb_fs_remove", "hb_fs_mkdir"):
            self.assertNotIn(call, src)

    def test_device_like_flags(self):
        mk = (APP / "Makefile").read_text()
        for flag in ("cortex-a5", "vfpv4-d16", "HB_FLOAT_ABI ?= soft", "-mword-relocations", "RAW_SURFACE  := 1"):
            self.assertIn(flag, mk)

    def test_weights_are_not_committed(self):
        files = subprocess.run(["git", "ls-files", "nanoapps/apps/tinyllm", "untether/artifacts/extra/Data"],
                               cwd=ROOT, capture_output=True, text=True).stdout.split()
        self.assertFalse([f for f in files if f.endswith(".bin")], files)
        self.assertIn("untether/artifacts/extra/Data/TinyLLM/", (ROOT / ".gitignore").read_text())

    def test_pinned_download_and_licence(self):
        from host import tinyllm
        self.assertEqual(tinyllm.HF_COMMIT, "0bd21da7698eaf29a0d7de3992de8a46ef624add")
        self.assertEqual(tinyllm.LICENCE, "MIT")
        self.assertEqual(set(tinyllm.FILES), {"stories260K.bin", "tok512.bin"})
        for sha in tinyllm.FILES.values():
            self.assertRegex(sha, r"^[0-9a-f]{64}$")
        notice = (APP / "NOTICE").read_text()
        self.assertIn("llama2.c", notice)
        self.assertIn("tinyllamas", notice)
        self.assertIn("MIT", notice)

    def test_install_plumbing(self):
        from host.ports import PORT_DATA, LARGE_GL
        self.assertEqual(PORT_DATA["Tiny LLM"]["fat"], "/Apps/Data/TinyLLM")
        self.assertTrue(PORT_DATA["Tiny LLM"]["required"])
        self.assertNotIn("Tiny LLM", LARGE_GL)
        from host.app_catalog import resolve_name
        self.assertEqual(resolve_name("tinyllm"), "Tiny LLM")

    def test_docs(self):
        doc = DOC.read_text()
        for needle in ("3.9 MB", "2 MB", "6 MB", "4 MB", "mkrelocapp", "/Apps/Data/TinyLLM",
                       "./start nabase add 'Tiny LLM'", "vfpv4-d16", "no host libc unwinder",
                       "stories260K", "MIT", "tok/s"):
            self.assertIn(needle, doc)

    def test_no_em_dashes(self):
        for path in list(APP.glob("*.c")) + list(APP.glob("*.h")) + list(APP.glob("*.py")) + [
                APP / "README.md", APP / "NOTICE", DOC, ROOT / "host" / "tinyllm.py"]:
            self.assertNotIn("\u2014", path.read_text(), msg=str(path))


@unittest.skipUnless(_tool("cc") and MODEL.is_file(), "needs a C compiler and ./start sdk build tinyllm")
class TestTinyLlmEngineOnHost(unittest.TestCase):
    """The engine compiled for this computer against the real model file."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.td = Path(cls._td.name)
        cls.exe = cls.td / "tllm_host"
        subprocess.run([_tool("cc"), "-std=c11", "-O2", "-fno-strict-aliasing", "-o", str(cls.exe),
                        str(APP / "tllm_host.c"), str(APP / "tllm.c")], check=True)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def run_host(self, model, *args):
        return subprocess.run([str(self.exe), str(model), *args], capture_output=True, text=True)

    def test_model_file_matches_converter(self):
        from host import tinyllm
        files = tinyllm.fetch()
        sys.path.insert(0, str(APP))
        import mkmodel
        data = mkmodel.convert(files["stories260K.bin"], files["tok512.bin"], tinyllm.STORY_TOKENS)
        self.assertEqual(data, MODEL.read_bytes())
        self.assertEqual(data[:4], b"TLM1")
        self.assertLess(len(data), 300 * 1024)

    def test_greedy_story_keeps_llama2c_opening(self):
        r = self.run_host(MODEL, "-i", "Once upon a time", "-t", "0", "-n", "60")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith(GREEDY_OPENING), r.stdout)

    def test_byte_fallback_prompt(self):
        r = self.run_host(MODEL, "-i", "Zoe saw a caf\u00e9", "-t", "0", "-n", "40")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("caf\u00e9", r.stdout)

    def test_corrupt_truncated_and_foreign_files(self):
        blob = bytearray(MODEL.read_bytes())
        bad = self.td / "corrupt.bin"
        flipped = bytearray(blob)
        flipped[100000] ^= 0x40
        bad.write_bytes(flipped)
        self.assertIn("ERROR 5", self.run_host(bad).stdout)        # CRC
        short = self.td / "short.bin"
        short.write_bytes(blob[:-1000])
        self.assertIn("ERROR 4", self.run_host(short).stdout)      # size
        other = self.td / "other.bin"
        other.write_bytes(b"\x00" * len(blob))
        self.assertIn("ERROR 1", self.run_host(other).stdout)      # magic


@unittest.skipUnless(HBAPP.is_file() and ELF.is_file(), "needs ./start sdk build tinyllm")
class TestTinyLlmImage(unittest.TestCase):
    def test_hbapp_is_mkreloc_and_small(self):
        import struct
        data = HBAPP.read_bytes()
        magic, entry, image, span, count, align = struct.unpack_from("<6I", data)
        self.assertEqual(magic, 0x314C5248)  # HRL1
        self.assertEqual(align, 16)
        self.assertEqual(len(data), 24 + image + count * 4)
        self.assertLess(span, 160 * 1024)

    @unittest.skipUnless(_tool("arm-none-eabi-readelf"), "needs readelf")
    def test_no_host_libc_unwinder(self):
        out = subprocess.check_output([_tool("arm-none-eabi-readelf"), "-s", "-W", str(ELF)], text=True)
        for sym in ("_Unwind_", "__gxx_personality", "__aeabi_unwind_cpp", "malloc", "printf"):
            self.assertNotIn(sym, out)

    @unittest.skipUnless(_tool("arm-none-eabi-objdump"), "needs objdump")
    def test_cortex_a5_vfpv4_d16_soft(self):
        attrs = subprocess.check_output([_tool("arm-none-eabi-readelf"), "-A", str(ELF)], text=True)
        self.assertIn("Tag_CPU_arch: v7", attrs)
        self.assertIn("Thumb-2", attrs)
        self.assertNotIn("Advanced SIMD", attrs)
        dis = subprocess.check_output([_tool("arm-none-eabi-objdump"), "-d", str(ELF)], text=True)
        insn = re.compile(r"^\s*[0-9a-f]+:\t[0-9a-f ]+\t([a-z][a-z0-9.]*)(.*)$", re.M)
        found = insn.findall(dis)
        self.assertGreater(len(found), 3000)
        # soft float: no VFP or NEON at all, so nothing touches d16-d31 either
        self.assertEqual([m + o for m, o in found if m.startswith("v")], [])
        # the int8 dot products use the A5's DSP multiply-accumulate
        self.assertIn("smlad", {m for m, _ in found})


def _run(td, name, script, model=None, os_blocks=None, largest=None):
    out = Path(td) / name
    out.mkdir(parents=True, exist_ok=True)
    (out / "script").write_text(script)
    cmd = [sys.executable, str(EMU), str(HBAPP), str(out), str(out / "script")]
    if model is not None:
        cmd += ["--model", str(model)]
    if os_blocks:
        cmd += ["--os-blocks", os_blocks]
    if largest:
        cmd += ["--largest", str(largest)]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


# The keyboard is sideways (iPod on its side, Home on the right; `ltap` takes
# view coordinates): ten 43 px columns, rows of 42 px from y=72, then
# caps/space/DEL/GO at y=198. MENU and TURN sit at the right of the header.
def _key(col, row):
    return col * 432 // 10 + 21, 72 + row * 42 + 21


TYPE_A_DOG = "".join(f"ltap {x} {y}\n" for x, y in (_key(0, 1), (172, 219), _key(2, 1), _key(8, 0), _key(4, 1)))
GO = "ltap 388 219\n"
TURN_SIDEWAYS = "ltap 403 13\n"     # TURN while sideways
TURN_UPRIGHT = "tap 211 14\n"       # TURN while upright


@unittest.skipUnless(HBAPP.is_file() and MODEL.is_file(), "needs ./start sdk build tinyllm")
class TestTinyLlmEmulated(unittest.TestCase):
    """Unicorn runs of the device .hbapp. All scenarios run in parallel."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        td = Path(cls._td.name)
        cls.td = td
        blob = MODEL.read_bytes()
        corrupt = td / "corrupt.bin"
        c = bytearray(blob)
        c[150000:150016] = b"\xa5" * 16
        corrupt.write_bytes(c)
        short = td / "short.bin"
        short.write_bytes(blob[: len(blob) // 2])
        runs = {
            "tight": _run(td, "tight", "2\nshot menu\nclick select\n60\nshot story\n"
                          "relaunch\n2\nshot relaunch\n", MODEL, TIGHT),
            "roomy": _run(td, "roomy", "2\n" + "click down\n" * 6 + "shot menu-type\nclick select\n3\n" +
                          TYPE_A_DOG + "2\nshot keys\n" + GO + "60\nshot story\n"
                          "click select\n10\nshot paused\n" + TURN_SIDEWAYS + "3\nshot paused-upright\n",
                          MODEL, ROOMY),
            # turning: menu upright -> sideways, keyboard by touch, its MENU key,
            # then the volume buttons on the keyboard (Vol - deletes, Vol + is a space)
            "turn": _run(td, "turn", "2\nshot menu\n" + TURN_UPRIGHT + "3\nshot menu-side\n"
                         "ltap 100 225\n3\nshot keys\nltap 396 13\n3\nshot back\n" + TURN_SIDEWAYS +
                         "3\nshot back-upright\nclick select\n3\nshot keys2\n"
                         "ltap %d %d\nltap %d %d\nclick down\nclick up\n2\nshot typed\nclick select\n4\n"
                         "shot story\nltap 220 220\n3\nshot menu-after\n" % (_key(0, 0) + _key(0, 0)),
                         MODEL, TIGHT),
            "missing": _run(td, "missing", "2\nshot first\n", None, TIGHT),
            "corrupt": _run(td, "corrupt", "2\nshot first\n", corrupt, TIGHT),
            "short": _run(td, "short", "2\nshot first\n", short, TIGHT),
            "lowmem": _run(td, "lowmem", "2\nshot first\n", MODEL, None, 262144),
        }
        if os.environ.get("TINYLLM_FULL"):
            runs["full"] = _run(td, "full", "2\nclick select\n40\nshot story-early\nuntil-done 5000\n"
                                "30\nshot story-end\n", MODEL, TIGHT)
        cls.out = {}
        for name, proc in runs.items():
            cls.out[name] = proc.communicate()[0]
            cls.out[name + ".rc"] = proc.returncode
        shots = os.environ.get("TINYLLM_SHOTS")
        if shots:
            for f in td.glob("*/*.png"):
                shutil.copy2(f, Path(shots) / f"{f.parent.name}-{f.name}")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def kind(self, run, shot):
        path = self.td / run / f"{shot}.kind"
        self.assertTrue(path.is_file(), msg=self.out.get(run))
        from PIL import Image
        with Image.open(self.td / run / f"{shot}.png") as im:
            self.assertEqual(im.size, (432, 240) if self.orient(run, shot) == "sideways" else (240, 432))
        return path.read_text().strip()

    def assertLargest(self, out, mib):
        # the scenario's largest block, less the app image the loader placed in it
        m = re.search(r"largest ([\d,]+) \(image span ([\d,]+)", out)
        self.assertIsNotNone(m, out)
        largest, span = (int(g.replace(",", "")) for g in m.groups())
        self.assertAlmostEqual(largest + span, mib * 1024 * 1024, delta=64 * 1024)

    def orient(self, run, shot):
        return (self.td / run / f"{shot}.orient").read_text().strip()

    def story(self, run):
        m = re.search(r"^text: (.*)$", self.out[run], re.M)
        self.assertIsNotNone(m, self.out[run])
        return m.group(1).replace("\\n", "\n")

    def host_story(self, run, limit=256):
        m = re.search(r"^begin: seed (\d+) prompt '(.*)'$", self.out[run], re.M)
        self.assertIsNotNone(m, self.out[run])
        exe = self.td / "tllm_host"
        if not exe.is_file():
            subprocess.run([_tool("cc"), "-std=c11", "-O2", "-fno-strict-aliasing", "-o", str(exe),
                            str(APP / "tllm_host.c"), str(APP / "tllm.c")], check=True)
        r = subprocess.run([str(exe), str(MODEL), "-i", m.group(2), "-t", "0.9", "-p", "0.9",
                            "-s", m.group(1), "-n", str(limit)], capture_output=True, text=True)
        return _printable(r.stdout.rsplit("\nTOKENS", 1)[0])

    def test_tight_memory_menu_and_story(self):
        out = self.out["tight"]
        self.assertEqual(self.out["tight.rc"], 0, out)
        self.assertLargest(out, 2)
        self.assertEqual(self.kind("tight", "menu"), "menu", out)
        self.assertEqual(self.kind("tight", "story"), "story", out)
        self.assertTrue(self.story("tight").startswith("Once upon a time"), out)
        self.assertIn("writes 0", out)

    def test_roomy_memory_keyboard_prompt(self):
        out = self.out["roomy"]
        self.assertEqual(self.out["roomy.rc"], 0, out)
        self.assertLargest(out, 4)
        self.assertEqual(self.kind("roomy", "menu-type"), "menu", out)
        self.assertEqual(self.kind("roomy", "keys"), "keyboard", out)
        self.assertEqual(self.kind("roomy", "story"), "story", out)
        self.assertIn("prompt 'A dog'", out)
        self.assertTrue(self.story("roomy").startswith("A dog"), out)

    def test_keyboard_is_sideways_and_turns_the_app(self):
        out = self.out["roomy"]
        self.assertEqual(self.orient("roomy", "menu-type"), "upright", out)
        self.assertEqual(self.orient("roomy", "keys"), "sideways", out)
        self.assertEqual(self.orient("roomy", "story"), "sideways", out)
        self.assertEqual(self.kind("roomy", "paused-upright"), "story", out)
        self.assertEqual(self.orient("roomy", "paused-upright"), "upright", out)

    def test_turn_menu_keys_and_volume_buttons(self):
        out = self.out["turn"]
        self.assertEqual(self.out["turn.rc"], 0, out)
        expect = [("menu", "menu", "upright"), ("menu-side", "menu", "sideways"),
                  ("keys", "keyboard", "sideways"), ("back", "menu", "sideways"),
                  ("back-upright", "menu", "upright"), ("keys2", "keyboard", "sideways"),
                  ("typed", "keyboard", "sideways"), ("story", "story", "sideways"),
                  ("menu-after", "menu", "sideways")]
        for shot, kind, orient in expect:
            self.assertEqual((shot, self.kind("turn", shot), self.orient("turn", shot)), (shot, kind, orient), out)
        # Q, Q, Vol - deletes one, Vol + adds a space that GO trims
        self.assertIn("prompt 'Q'", out)
        self.assertIn("OS heap: blocks held 1 ", out)

    def test_device_matches_host_engine(self):
        # same prompt and seed: the emulated device writes exactly what the
        # engine writes on this computer (soft float and libgcc agree with IEEE)
        for run in ("tight", "roomy"):
            dev = self.story(run)
            self.assertGreater(len(dev), 20, self.out[run])
            self.assertTrue(self.host_story(run).startswith(dev), (run, dev))

    def test_tokens_were_generated_and_timed(self):
        m = re.search(r"tokens (\d+); instructions per token: average ([\d,]+)", self.out["tight"])
        self.assertIsNotNone(m, self.out["tight"])
        self.assertGreater(int(m.group(1)), 10)
        self.assertLess(int(m.group(2).replace(",", "")), 4_000_000)

    def test_stack_stays_small(self):
        m = re.search(r"stack: deepest ([\d,]+) bytes", self.out["tight"])
        self.assertLess(int(m.group(1).replace(",", "")), 4096)

    def test_relaunch_does_not_leak(self):
        out = self.out["tight"]
        self.assertEqual(self.kind("tight", "relaunch"), "menu", out)
        self.assertIn("OS heap: blocks held 1 ", out)
        self.assertIn("aux block: set", out)

    def test_pause(self):
        self.assertIn("paused", self.out["roomy"])
        self.assertEqual(self.kind("roomy", "paused"), "story", self.out["roomy"])

    def test_missing_model(self):
        self.assertEqual(self.kind("missing", "first"), "missing", self.out["missing"])
        self.assertIn("blocks held 0", self.out["missing"])

    def test_corrupt_model(self):
        self.assertEqual(self.kind("corrupt", "first"), "damaged", self.out["corrupt"])
        self.assertIn("blocks held 0", self.out["corrupt"])
        self.assertIn("aux block: none", self.out["corrupt"])

    def test_truncated_model(self):
        self.assertEqual(self.kind("short", "first"), "damaged", self.out["short"])
        self.assertIn("blocks held 0", self.out["short"])

    def test_low_memory_screen(self):
        self.assertEqual(self.kind("lowmem", "first"), "low-memory", self.out["lowmem"])
        self.assertIn("blocks held 0", self.out["lowmem"])

    @unittest.skipUnless(os.environ.get("TINYLLM_FULL"), "TINYLLM_FULL=1 runs a whole story")
    def test_full_story(self):
        out = self.out["full"]
        self.assertIn("finished 1", out)
        self.assertEqual(self.kind("full", "story-end"), "story", out)
        self.assertTrue(self.host_story("full").startswith(self.story("full")), out)


sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_nabase import Base as _NabaseBase  # noqa: E402


@unittest.skipUnless(HBAPP.is_file() and MODEL.is_file(), "needs ./start sdk build tinyllm")
class TestTinyLlmNabaseAdd(_NabaseBase):
    """./start nabase add 'Tiny LLM' copies the app and the model file."""

    def test_add_copies_model(self):
        from host import nabase
        self.nanoapps_disk()
        nabase.backup(self.root, out=self.out.append)
        names = nabase.add(self.root, ["Tiny LLM"], out=self.out.append)
        self.assertIn("Tiny LLM", names)
        self.assertTrue((self.root / "Apps/Executables/Tiny LLM.hbapp").is_file())
        self.assertEqual((self.root / "Apps/Data/TinyLLM/model.bin").read_bytes(), MODEL.read_bytes())
        self.music_untouched()


if __name__ == "__main__":
    unittest.main()
