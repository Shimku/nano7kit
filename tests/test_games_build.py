#!/usr/bin/env python3
"""Mario / Mario Kart host build: hb_shared ABI, aux block, macOS build fixes."""
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host.compat import sha256_file  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SDK = ROOT / "nanoapps" / "sdk"
RESIDENT = ROOT / "nanoapps" / "system" / "silver_resident"
BUILD_GAMES = ROOT / "scripts" / "build-games.sh"
ZSTD_FIX = ROOT / "scripts" / "darwin-arm-zstd.sh"
HBSH = (0x48534248).to_bytes(4, "little")

# The hardware-tested residents ./start installs (pinned, never rebuilt here).
PINNED_RESIDENTS = {
    "artifacts/device/Startup/resident.hbapp":
        "3ad91228e6388e5e3972c521f750ef232d83fdef6843fcd1a0cb95ea314590ad",
    "artifacts/device/Startup/resident-A.hbapp":
        "0f81cebdda2c12f33ff83c363cf6ccdbdeaa2b38fae4ae4efaa7c47bc5d86803",
    "artifacts/device/Startup/resident-B.hbapp":
        "b2e284ca0cff87bb92e5e22bac3bd75a2c7c7fdb7521b0617c0ea407ac3cd3ac",
    "artifacts/extra/Startup/resident-nogrow.hbapp":
        "1f9a8ca7e88d93976e015d4b90bd06b09899469648ea45955e05b0b5afe2baef",
    "artifacts/extra/Startup/resident-A-nogrow.hbapp":
        "cd55a5818696d7ca91af373aaf6be61923e2f0ac81e43580661d8e66914bdef0",
    "artifacts/extra/Startup/resident-B-nogrow.hbapp":
        "6b0581bd18a7121f2f7905c9c06c2c56fcdd39d5ae164baa91a6f44dc208cff4",
}


def strip_c_comments(text):
    return re.sub(r"/\*.*?\*/", "", re.sub(r"//[^\n]*", "", text), flags=re.S)


class SharedAbiTests(unittest.TestCase):
    """The SDK must speak the shared-block ABI the pinned residents already use."""

    def test_pinned_residents_unchanged_and_speak_hb_shared(self):
        for rel, want in PINNED_RESIDENTS.items():
            path = ROOT / "untether" / rel
            self.assertEqual(sha256_file(path), want, rel)
            self.assertIn(HBSH, path.read_bytes(), f"{rel} lacks the HBSH magic")

    def test_hb_shared_layout_is_version_2(self):
        """NanoApps 1.0 / rc1: ABI v2 adds prepare_unload for unload-on-switch."""
        text = strip_c_comments((SDK / "hb_shared.h").read_text())
        self.assertIn("#define HB_SHARED_MAGIC    0x48534248u", text)
        self.assertIn("#define HB_SHARED_VERSION  2u", text)
        body = text[text.index("typedef struct hb_shared {"):text.index("} hb_shared_t;")]
        fields = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:\[\s*\d+\s*\])?\s*;", body)
        fields += re.findall(r"\(\*\s*([a-z_]+)\s*\)", body)
        self.assertEqual(
            fields,
            ["magic", "version", "touch", "aux_block", "screenshot_mode",
             "statusbar_demo", "pad", "trace", "prepare_unload"],
            "hb_shared_t field order must match NanoApps 1.0 (ABI v2)",
        )

    def test_aux_block_macro(self):
        text = (SDK / "hb_heap.h").read_text()
        self.assertIn('#include "hb_shared.h"', text)
        self.assertRegex(text, r"#define HB_APP_AUX_BLOCK\s+\(hb_shared->aux_block\)")

    def test_mario_and_kart_patches_use_the_aux_block(self):
        for game in ("sm64", "mk64"):
            patch = (ROOT / "games" / game / "nano-port.patch").read_text()
            self.assertIn("HB_APP_AUX_BLOCK", patch, game)

    def test_sdk_links_shared_and_fpu(self):
        mk = (SDK / "hb_app.mk").read_text()
        for src in ("hb_shared.c", "hb_fpu.c"):
            self.assertIn(f"$(SDK_DIR)/{src}", mk)
            self.assertTrue((SDK / src).is_file(), src)
        self.assertIn("CHECK_NO_THUMB_FP ?= 0", mk)
        self.assertIn("PYTHON  ?= python3", mk)
        self.assertIn("hb_fpu_attach_current_task", (SDK / "hb_sdk.h").read_text())

    def test_surfaces_take_the_shared_block(self):
        for name in ("hb_gl_surface.c", "hb_lv_surface.c", "hb_raw_surface.c"):
            text = (SDK / name).read_text()
            self.assertIn(
                "payload_entry(int op, void *fb, int w, int h, hb_shared_t *sh)", text, name)
            self.assertIn("sh->magic != HB_SHARED_MAGIC", text, name)
            self.assertIn("hb_shared = sh;", text, name)

    def test_touch_reads_shared_block(self):
        text = strip_c_comments((SDK / "hb_touch_mb.c").read_text())
        self.assertIn("hb_shared->touch", text)
        self.assertNotIn("HB_TOUCH_MB_ADDR", text)

    def test_no_fixed_dram_scratch_outside_legacy_ifdefs(self):
        """Relocatable apps must not write fixed DRAM (live OS heap)."""
        trace = strip_c_comments((SDK / "hb_trace.c").read_text())
        if "0x09120000" in trace:
            self.assertEqual(trace.count("0x09120000"), 1)
            idx = trace.index("0x09120000")
            self.assertGreater(idx, trace.index("#ifdef HB_TRACE_LEGACY_FIXED"))
            self.assertLess(idx, trace.index("#else", idx))
        else:
            # NanoApps 1.0 / rc1: ring lives on the heap; no fixed DRAM scratch.
            self.assertNotIn("0x09120000", trace)
        fs = strip_c_comments((SDK / "hb_fs.c").read_text())
        self.assertNotRegex(fs, r"0x0911[0-9a-f]{4}")
        self.assertIn("static uint8_t s_fs_cache[FOBJ_CACHE_BYTES]", fs)
        self.assertIn("uint64_t sz = 0;", fs)  # the OS writes a 64-bit size

    def test_mkrelocapp_skips_absolute_symbols_and_rebases_veneers(self):
        text = (ROOT / "nanoapps" / "tools" / "mkrelocapp.py").read_text()
        self.assertIn('if sym["st_shndx"] == "SHN_ABS":', text)
        self.assertIn("_from_arm", text)
        self.assertIn("-mword-relocations", text)


class InTreeResidentTests(unittest.TestCase):
    """The legacy in-tree resident hands the same block to apps and frees aux."""

    def setUp(self):
        self.src = (RESIDENT / "silver_resident.c").read_text()

    def test_shared_block_and_entries(self):
        self.assertIn("static hb_shared_t s_shared;", self.src)
        self.assertIn("hb_shared = &s_shared;", self.src)
        self.assertIn("s_shared.magic     = HB_SHARED_MAGIC;", self.src)
        self.assertEqual(self.src.count("FB_W, FB_H, &s_shared)"), 3)
        self.assertNotRegex(self.src, r"\(0, (s_fb|0), FB_W, FB_H\)")

    def test_aux_block_freed_on_quit_and_switch(self):
        self.assertIn("s_pending_free_aux = s_shared.aux_block;", self.src)
        drain = self.src[self.src.index("static void hb_surface_drain_pending(void)"):]
        drain = drain[:drain.index("\n}\n")]
        self.assertIn("s_pending_free_aux", drain)
        self.assertEqual(self.src.count("hb_free_app_aux();"), 2)

    def test_touch_mirrored(self):
        self.assertIn("s_shared.touch[0] = (int32_t)TOUCH_MB_MAGIC;", self.src)

    def test_legacy_scratch_is_explicit(self):
        mk = (RESIDENT / "Makefile").read_text()
        self.assertIn("-DHB_TRACE_LEGACY_FIXED", mk)
        self.assertIn("-DHB_FS_FIXED_CACHE=0x09118000u", mk)
        self.assertIn("-DHB_FS_FIXED_STREAM=0x0911a000u", mk)

    @unittest.skip("rc1: legacy silver_resident is superseded by silver_resident_na (NanoApps 1.0 ABI)")
    def test_builds_and_stays_below_fixed_scratch(self):
        out = tempfile.mkdtemp(prefix="n7res-")
        try:
            work = Path(out) / "nanoapps"
            shutil.copytree(ROOT / "nanoapps" / "sdk", work / "sdk")
            shutil.copytree(ROOT / "nanoapps" / "tools", work / "tools",
                            ignore=shutil.ignore_patterns("dependencies", "__pycache__"))
            shutil.copytree(RESIDENT, work / "system" / "silver_resident",
                            ignore=shutil.ignore_patterns("build"))
            r = subprocess.run(["make", "-s", "-C", str(work / "system" / "silver_resident")],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            nm = subprocess.run(
                ["arm-none-eabi-nm", str(work / "system/silver_resident/build/silver_resident.elf")],
                capture_output=True, text=True, check=True).stdout
            syms = {l.split()[2]: int(l.split()[0], 16)
                    for l in nm.splitlines() if len(l.split()) == 3}
            # Code + data stay below the fixed scratch at 0x09135b00; .bss stays in
            # its parked window at 0x09200000 (the legacy layout, which must not move).
            self.assertLess(syms["__text_end__"], 0x09135B00)
            self.assertGreaterEqual(syms["__bss_start__"], 0x09200000)
            self.assertLess(syms["__bss_end__"], 0x09204000)
        finally:
            shutil.rmtree(out, ignore_errors=True)


class SharedLayoutCompileTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("arm-none-eabi-gcc"), "Arm toolchain not installed")
    def test_offsets_on_target(self):
        d = tempfile.mkdtemp(prefix="n7abi-")
        try:
            c = Path(d) / "abi.c"
            c.write_text(
                '#include <stddef.h>\n#include "hb_shared.h"\n'
                "_Static_assert(offsetof(hb_shared_t, magic) == 0, \"magic\");\n"
                "_Static_assert(offsetof(hb_shared_t, version) == 4, \"version\");\n"
                "_Static_assert(offsetof(hb_shared_t, touch) == 8, \"touch\");\n"
                "_Static_assert(offsetof(hb_shared_t, aux_block) == 24, \"aux\");\n"
                "_Static_assert(offsetof(hb_shared_t, screenshot_mode) == 28, \"shot\");\n"
                "_Static_assert(offsetof(hb_shared_t, statusbar_demo) == 29, \"bar\");\n"
                "_Static_assert(offsetof(hb_shared_t, trace) == 32, \"trace\");\n"
                "_Static_assert(sizeof(hb_shared_t) == 40, \"size\");\n")
            r = subprocess.run(
                ["arm-none-eabi-gcc", "-mcpu=cortex-a8", "-mthumb", "-ffreestanding",
                 f"-I{SDK}", "-c", str(c), "-o", str(Path(d) / "abi.o")],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class BuildGamesScriptTests(unittest.TestCase):
    def setUp(self):
        self.text = BUILD_GAMES.read_text()

    def test_syntax(self):
        subprocess.run(["bash", "-n", str(BUILD_GAMES)], check=True)
        subprocess.run(["bash", "-n", str(ZSTD_FIX)], check=True)

    def test_darwin_fixes(self):
        darwin = self.text[self.text.index("if [[ $(uname -s) == Darwin ]]; then"):]
        darwin = darwin[:darwin.index("\nfi\n")]
        self.assertIn('export CXXFLAGS="${CXXFLAGS:+$CXXFLAGS }-std=c++17"', darwin)
        self.assertIn("CPP=${GAME_CPP:-clang -E -P -x c -Wno-trigraphs}", darwin)
        self.assertIn("darwin-arm-zstd.sh\" --check-or-fix", darwin)

    def test_both_games_get_the_make_vars_and_fresh_sdk(self):
        self.assertEqual(self.text.count('${GAME_MAKE_VARS[@]+"${GAME_MAKE_VARS[@]}"}'), 2)
        self.assertEqual(self.text.count('sdk_fresh "$D" build'), 2)
        self.assertEqual(self.text.count('"${APP_PY[@]}"'), 2)
        self.assertIn('write_sizes "$D/build/us_nano/sm64data" 1', self.text)
        self.assertIn('write_sizes "$D/dc_data" 0', self.text)

    def test_venv_before_app_builds(self):
        self.assertLess(self.text.index('VENV="$BUILD/venv"'),
                        self.text.index('make -C "$NANOAPPS/apps/sm64"'))

    def _write_sizes_py(self):
        m = re.search(r"write_sizes\(\) \{.*?<<'PY'\n(.*?)\nPY\n", self.text, re.S)
        self.assertIsNotNone(m)
        return m.group(1)

    def test_write_sizes_matches_gnu_stat_format(self):
        code = self._write_sizes_py()
        d = Path(tempfile.mkdtemp(prefix="n7sizes-"))
        try:
            (d / "levels" / "bob").mkdir(parents=True)
            (d / "actors").mkdir()
            (d / "levels" / "bob" / "leveldata.bin").write_bytes(b"x" * 7)
            (d / "actors" / "group10.bin").write_bytes(b"y" * 300)
            (d / "actors" / "group1.bin").write_bytes(b"")
            (d / "notes.txt").write_text("ignored")
            subprocess.run([sys.executable, "-c", code, str(d), "1"], check=True,
                           capture_output=True)
            self.assertEqual(
                (d / "sizes.txt").read_text(),
                "actors/group1.bin 0\nactors/group10.bin 300\nlevels/bob/leveldata.bin 7\n")
            subprocess.run([sys.executable, "-c", code, str(d), "0"], check=True,
                           capture_output=True)
            self.assertEqual((d / "sizes.txt").read_text(), "")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class ZstdFixTests(unittest.TestCase):
    def _fake_bin(self, d, name, body):
        p = Path(d) / name
        p.write_text("#!/bin/sh\n" + body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)

    def _run(self, uname, gcc_body, *args):
        d = tempfile.mkdtemp(prefix="n7zbin-")
        try:
            self._fake_bin(d, "uname", f"echo {uname}\n")
            self._fake_bin(d, "arm-none-eabi-gcc", gcc_body)
            env = dict(os.environ, PATH=d + os.pathsep + os.environ.get("PATH", ""))
            env.pop("NANO7_ARM_GCC", None)
            return subprocess.run(["bash", str(ZSTD_FIX), *args], env=env,
                                  capture_output=True, text=True)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_noop_off_macos(self):
        r = self._run("Linux", "exit 1\n", "--check")
        self.assertEqual(r.returncode, 0)

    def test_working_compiler_passes(self):
        r = self._run("Darwin", 'for a; do :; done; touch "$a"; exit 0\n', "--check")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_missing_zstd_is_explained(self):
        body = ("echo 'dyld[1]: Library not loaded: /usr/local/opt/zstd/lib/libzstd.1.dylib'"
                " >&2\nexit 134\n")
        r = self._run("Darwin", body, "--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("libzstd", r.stderr)
        self.assertIn("--fix", r.stderr)

    def test_other_compiler_failure_is_not_patched(self):
        r = self._run("Darwin", "echo 'cc1: internal error' >&2\nexit 1\n", "--check-or-fix")
        self.assertEqual(r.returncode, 1)
        self.assertIn("cannot compile a test file", r.stderr)
        self.assertNotIn("libzstd fix", r.stdout)

    def test_fix_never_touches_system_paths(self):
        text = ZSTD_FIX.read_text()
        self.assertIn('find "$TC/bin" "$TC/libexec"', text)
        self.assertIn("zstd-fix-backup", text)
        self.assertIn('sudo -H -u "$SUDO_USER"', text)
        self.assertNotRegex(text, r"install_name_tool[^\n]*/usr/")


class PrebuiltScreensTests(unittest.TestCase):
    """Packaging on a Mac with old Rust falls back to a pinned screen DB."""

    def setUp(self):
        self.tools = ROOT / "nanoapps" / "tools"
        self.text = (self.tools / "build_apps.py").read_text()

    def _const(self, name):
        return re.search(name + r' = "([0-9a-f]{64})"', self.text).group(1)

    def test_pins_match_sources_and_blob(self):
        import hashlib
        src = self.tools / "screens"
        h = hashlib.sha256()
        for p in sorted(src.rglob("*")):
            if p.is_file():
                h.update(p.relative_to(src).as_posix().encode() + b"\0" + p.read_bytes())
        self.assertEqual(h.hexdigest(), self._const("SCREENS_SOURCES_SHA256"))
        self.assertEqual(sha256_file(self.tools / "hb_silverdb.prebuilt"),
                         self._const("SCREENS_PREBUILT_SHA256"))

    def test_fallback_only_when_cargo_fails(self):
        body = self.text[self.text.index("def build_screens():"):self.text.index("def main(apps):")]
        self.assertIn("except FileNotFoundError:", body)
        self.assertIn("use_prebuilt_screens(dest, why)", body)
        self.assertIn('"create"', body)

    def test_silverutil_reproduces_prebuilt(self):
        su = self.tools / "dependencies/silverutil/target/release/silverutil"
        if not su.is_file():
            self.skipTest("silverutil not built on this host")
        d = tempfile.mkdtemp(prefix="n7sdb-")
        try:
            out = Path(d) / "db.bin"
            subprocess.run([str(su), "create", str(self.tools / "screens"), str(out)],
                           check=True, capture_output=True)
            self.assertEqual(out.read_bytes(), (self.tools / "hb_silverdb.prebuilt").read_bytes())
        finally:
            shutil.rmtree(d, ignore_errors=True)


class CreditsTests(unittest.TestCase):
    def test_credits_name_olievans123(self):
        text = (ROOT / "CREDITS.md").read_text()
        self.assertIn("olievans123", text)


if __name__ == "__main__":
    unittest.main()
