#!/usr/bin/env python3
"""PocketJS nano7g target: pinned fetch, patch, staged apps, slot B, docs."""
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host import const
from host.app_catalog import catalog, reset_catalog_cache, resolve_name
from host.apps import add_apps, pack_app_names
from host.const import SLOT_B_SHA256, TESTED_DISK_SIZE
from host.device import Nano, annotate_size
from host.app_stage import proven_lookalikes
from host.fixed_addrs import legacy_hits

ROOT = Path(__file__).resolve().parent.parent
START = ROOT / "start"
PJ = ROOT / "pocketjs"
EXTRA = ROOT / "untether" / "artifacts" / "extra"
EM_DASH = "\u2014"


def apps_manifest():
    return json.loads((PJ / "apps.json").read_text(encoding="utf-8"))


def patch_text():
    return (PJ / "nano7g.patch").read_text(encoding="utf-8", errors="replace")


def added_lines(path_prefix):
    """The '+' lines the patch adds to files under path_prefix."""
    out, current = [], None
    for line in patch_text().splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
            continue
        if current and current.startswith(path_prefix) and line.startswith("+"):
            out.append(line[1:])
    return out


class PinTests(unittest.TestCase):
    def test_commit_is_pinned_once(self):
        self.assertRegex(const.POCKETJS_COMMIT, r"^[0-9a-f]{40}$")
        self.assertEqual(apps_manifest()["commit"], const.POCKETJS_COMMIT)
        self.assertEqual(const.POCKETJS_LICENSE, "MIT")

    def test_nothing_vendored(self):
        tracked = subprocess.run(["git", "ls-files", "pocketjs"], cwd=ROOT, capture_output=True,
                                 text=True).stdout.split()
        self.assertTrue(tracked, "pocketjs/ is tracked")
        for path in tracked:
            self.assertFalse(path.endswith((".rs", ".ts", ".tsx")), path)
        self.assertIn("/build/", (ROOT / ".gitignore").read_text())


class PatchTests(unittest.TestCase):
    def test_patch_adds_a_clean_nano7g_target(self):
        text = patch_text()
        for path in (
            "hosts/nano7g/pocket.host.json",
            "programs/nano7g/Cargo.toml",
            "programs/nano7g/src/lib.rs",
            "programs/nano7g/preview/src/main.rs",
            "programs/nano7g/nanoapps/pocketnano.c",
            "programs/nano7g/nanoapps/Makefile",
            "programs/nano7g/README.md",
            "programs/nano7g/pocket.host.config.ts",
            "tools/build/nano7g/build.ts",
            "apps/nano7g-hero/pocket.app.json",
            "hosts/index.ts",
            "hosts/ids.ts",
        ):
            self.assertIn(f"+++ b/{path}", text, path)
        host = json.loads("\n".join(added_lines("hosts/nano7g/pocket.host.json")))
        self.assertEqual(host["id"], "nano7g")
        self.assertEqual(host["panel"]["primary"], [240, 432])
        self.assertIn([240, 432], host["display"]["logicalViewports"])
        self.assertIn("input.touch", host["capabilities"])
        self.assertNotIn("audio.pcm", host["capabilities"])
        self.assertNotIn("net.http", host["capabilities"])

    def test_device_build_flags(self):
        build = "\n".join(added_lines("tools/build/nano7g/build.ts"))
        self.assertIn('"thumbv7a-none-eabi"', build)
        self.assertIn("+no-movt", build)
        self.assertIn("+soft-float,-neon", build)
        self.assertIn("relocation-model=static", build)

    def test_shim_frees_through_the_aux_block(self):
        shim = "\n".join(added_lines("programs/nano7g/nanoapps/pocketnano.c"))
        self.assertIn("HB_APP_AUX_BLOCK = s_heap", shim)
        self.assertIn("hb_os_free(HB_APP_AUX_BLOCK)", shim)
        self.assertIn("POCKETNANO_OS_KEEP_LARGEST", shim)
        self.assertIn("POCKETNANO_OS_KEEP_FREE", shim)
        # the only file writes are the app's own log files.
        for chunk in shim.split("hb_fs_write(")[1:]:
            self.assertTrue(chunk.startswith(("PN_LOG_FILE,", "PN_LOG_PREV,")), chunk[:40])
        self.assertIn('#define PN_LOG_ROOT "/Apps/Data/PocketJS"', shim)
        self.assertNotIn("hb_audio", shim)
        self.assertIn("__builtin_longjmp", shim)

    def test_core_runs_on_the_private_stack(self):
        shim = "\n".join(added_lines("programs/nano7g/nanoapps/pocketnano.c"))
        self.assertIn("on_stack(guarded_entry, s_stack_hi)", shim)
        self.assertIn("s_heap_len = want - s_stack_len", shim)
        # An app may ask for more (Vue Lab: nested setup() recursion).
        self.assertIn("s_stack_len = POCKETNANO_BOOT_STACK + pocketnano_stack_extra(index)", shim)
        self.assertIn('"PJSTK"', shim)
        # Every core call (boot and frames) goes through run_core.
        self.assertIn("run_core(&t, t0);", shim)
        self.assertNotIn("    guarded(&t, t0);", shim)

    def test_private_stack_can_be_switched_off(self):
        shim = "\n".join(added_lines("programs/nano7g/nanoapps/pocketnano.c"))
        self.assertIn('"/Apps/Data/PocketJS/no_private_stack.txt"', shim)
        self.assertIn("s_boot_stack = !hb_fs_exists(PN_NO_STACK_FLAG)", shim)
        self.assertIn('pocketnano_log("PJOPEN", s_rows, s_boot_stack)', shim)
        doc = (ROOT / "docs" / "POCKETJS.md").read_text(encoding="utf-8")
        self.assertIn("no_private_stack.txt", doc)

    def test_samples_library(self):
        m = apps_manifest()
        samples = {a["name"]: a for a in m["apps"]}["JS Samples"]
        self.assertEqual(samples["folder"], "pjsamples")
        self.assertEqual(set(samples["samples"]), set(m["samples"]))
        self.assertEqual(len(samples["samples"]), 7)
        build_rs = "\n".join(added_lines("programs/nano7g/build.rs"))
        for key in samples["samples"]:
            self.assertIn(f'"{key}"', build_rs, key)
        for up in ("gba-hero", "ipodnano-hero", "hero", "solid-aot-lab", "vue-sfc-lab"):
            path = f"apps/nano7g-samples/{up}.app.json"
            man = json.loads("\n".join(added_lines(path)))
            self.assertTrue(man["id"].endswith(".nano7g"), path)
            self.assertEqual(man["app"]["viewport"]["fixed"]["presentation"], "fit", path)
        shim = "\n".join(added_lines("programs/nano7g/nanoapps/pocketnano.c"))
        for word in ("draw_menu", "pocketnano_wants_menu()", '"PJFREE"', "pocketnano_stop()"):
            self.assertIn(word, shim)

    def test_heap_is_sized_per_app_and_given_back(self):
        shim = "\n".join(added_lines("programs/nano7g/nanoapps/pocketnano.c"))
        self.assertIn("pocketnano_heap_need(index) + s_stack_len", shim)
        self.assertIn("if (!grab_heap(index))", shim)
        self.assertIn('pocketnano_log("PJFREE", left, pocketnano_heap_peak());\n        log_flush();\n        drop_heap();', shim)
        self.assertNotIn("POCKETNANO_HEAP_WANT", shim)
        samples = "\n".join(added_lines("programs/nano7g/src/samples/mod.rs"))
        import re
        needs = [int(k) for k in re.findall(r"heap: (\d+) << 10, // ARM peak", samples)]
        self.assertEqual(len(needs), 7)
        # Every app's block (heap plus the 48 KB stack) fits in what a 4.0 MB
        # largest block has left after the resident placed the ~2.2 MB image.
        self.assertLessEqual(max(needs) + 48, 4096 - 2200)

    def test_baked_fonts_are_not_copied_to_the_heap(self):
        build_rs = "\n".join(added_lines("programs/nano7g/build.rs"))
        self.assertIn('".load_font_atlas_static(include_bytes!("', build_rs)
        core = "\n".join(added_lines("engine/core/src/text.rs"))
        self.assertIn("pub fn parse_static(bytes: &'static [u8])", core)
        self.assertIn("Cow::Borrowed(&bytes[range])", core)

    def test_motion_reads_the_accelerometer_only_when_asked(self):
        shim = "\n".join(added_lines("programs/nano7g/nanoapps/pocketnano.c"))
        self.assertIn("if (pocketnano_motion()) {\n        hb_accel_read_raw(accel);", shim)
        lib = "\n".join(added_lines("programs/nano7g/src/motion.rs"))
        self.assertIn("atan2", lib)

    def test_public_copy_has_no_em_dashes(self):
        for prefix in ("programs/nano7g/", "apps/nano7g-hero/", "apps/nano7g-tilt/", "apps/nano7g-samples/",
                       "hosts/nano7g/", "tools/build/nano7g/"):
            for line in added_lines(prefix):
                self.assertNotIn(EM_DASH, line, prefix)
        for doc in (PJ / "README.md", ROOT / "docs" / "POCKETJS.md"):
            self.assertNotIn(EM_DASH, doc.read_text(encoding="utf-8"), doc)


class QuickJsTests(unittest.TestCase):
    """JavaScript packages on QuickJS, read from /Apps/Data."""

    def test_quickjs_is_pinned_not_vendored(self):
        m = apps_manifest()
        q = m["quickjs"]
        self.assertRegex(q["commit"], r"^[0-9a-f]{40}$")
        self.assertTrue(q["licence"].startswith("MIT"))
        self.assertTrue(q["sha256"])
        for digest in q["sha256"].values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
        self.assertFalse([t for t in tracked if t.endswith(("quickjs.c", "malloc-2.8.6.c"))])

    def test_packages_and_bytecode_are_staged(self):
        # Built from the pinned PocketJS (MIT) like the .hbapp files, so the
        # Mac needs no bun or compiler for ./start pocketjs packages.
        from host.pocketjs import package_files
        for key in apps_manifest()["packages"]:
            pkg, bc = package_files(key)[:2]
            self.assertTrue(pkg.is_file(), pkg)
            self.assertEqual(pkg.read_bytes()[:4], b"PCKT")
            data = bc.read_bytes()
            self.assertEqual(data[:4], b"PQBC")
            version, src_len, _fnv, bc_len = struct.unpack_from("<4I", data, 4)
            self.assertEqual(version, 1)
            self.assertEqual(bc_len, len(data) - 20)
            self.assertGreater(src_len, bc_len // 2)

    def test_boot_is_spread_over_heartbeats(self):
        shim = "\n".join(added_lines("programs/nano7g/nanoapps/pocketnano.c"))
        self.assertIn("pocketnano_boot_step()", shim)
        self.assertIn('"PJSTEP"', shim)
        self.assertIn('"PJBC"', shim)
        rs = "\n".join(added_lines("programs/nano7g/src/lib.rs"))
        self.assertIn("pub fn boot_step(&mut self) -> u32", rs)
        c = "\n".join(added_lines("programs/nano7g/nanoapps/pocketqjs.c"))
        # the functions are copied out of the bytecode (an app's
        # atoms never match the runtime's, so in place never happened), the
        # buffer is freed after the read and the bundle text becomes a spare
        # allocator space.
        self.assertIn("JS_ReadObject(s_ctx, bc + PQBC_HEADER, s_bc_len - PQBC_HEADER, JS_READ_OBJ_BYTECODE);", c)
        self.assertIn("drop_bytecode();", c)
        self.assertIn("pocketqjs_donate((void *)code, length);", c)
        self.assertIn('"__pocketTapeFrames"', c)
        dev = "\n".join(added_lines("framework/src/devtools.ts"))
        self.assertIn("__pocketTapeFrames", dev)
        self.assertIn("pocketqjs_fnv1a(code, source)", c)
        self.assertIn("int main", "\n".join(added_lines("programs/nano7g/nanoapps/pocketqjsc.c")))

    def test_app_row_and_packages(self):
        m = apps_manifest()
        app = {a["name"]: a for a in m["apps"]}["JS QuickJS"]
        self.assertTrue(app["quickjs"])
        self.assertEqual(app["folder"], "pjquickjs")
        self.assertEqual(m["packages_dir"], "/Apps/Data/PocketJS")
        self.assertEqual(set(app["packages"]), {"hero", "cafe", "physics", "type", "fonts", "gestures", "vue", "notes",
                                                "home", "notebook", "motion", "talk"})
        self.assertEqual({k for k in app["packages"] if m["packages"][k].get("home_only")},
                         {"home", "notebook", "motion", "talk"})
        js = "\n".join(added_lines("programs/nano7g/src/js.rs"))
        for key in app["packages"]:
            self.assertIn(f'"{m["packages_dir"]}/{m["packages"][key]["file"]}\\0"', js, key)

    def test_heap_fits_the_probe_model(self):
        # Probe after a fresh boot: 7.0 MB free, 4.0 MB largest. The block is
        # heap + package (read whole) + the 160 KB private stack, and must
        # fit next to the ~1.2 MB image in the 4.0 MB block.
        import re
        from host.pocketjs import package_files
        js = "\n".join(added_lines("programs/nano7g/src/js.rs"))
        pairs = re.findall(r'key: "(\w+)",.*?heap: (\d+) << 10', js, re.S)
        self.assertEqual([k for k, _ in pairs], ["hero", "cafe", "physics", "type", "fonts", "gestures", "vue", "notes",
                                                  "home", "notebook", "motion", "talk"])
        # Vue Lab takes 80 KiB more private stack (nested setup()).
        self.assertIn("stack: 80 << 10", js)
        stacks = {k: 160 + 80 for k in ("vue", "home", "notebook", "motion", "talk")}
        hbapp = EXTRA / "Executables" / "JS QuickJS.hbapp"
        image = struct.unpack_from("<6I", hbapp.read_bytes())[3] // 1024
        for key, heap in pairs:
            # Only the package and its bytecode are read into the block up
            # front; data files (a font archive) are inside the heap figure.
            files = sum(p.stat().st_size for p in package_files(key)[:2]) // 1024
            # at least about 300 KB to spare in that block for
            # every app (Type was 20 KB short of it before the keyboard's
            # nodes shared one prototype).
            self.assertLessEqual(int(heap) + files + stacks.get(key, 160) + image, 4096 - 300, key)
        shim = "\n".join(added_lines("programs/nano7g/nanoapps/pocketnano.c"))
        self.assertIn("(160u << 10)", shim)

    def test_one_allocator_over_the_app_block(self):
        c = "\n".join(added_lines("programs/nano7g/nanoapps/pocketqjs.c"))
        for word in ("ONLY_MSPACES", "create_mspace_with_base", "JS_SetMemoryLimit", "JS_SetMaxStackSize",
                     "JS_STRIP_SOURCE", "POCKET_QJS_CANONICAL", "JS_RunGC"):
            self.assertIn(word, c)
        self.assertNotIn("hb_fs_write", c)
        rs = "\n".join(added_lines("programs/nano7g/src/nanoapps.rs"))
        self.assertIn("pocketqjs_alloc", rs)
        core = "\n".join(added_lines("engine/core/src/text.rs"))
        self.assertIn("pub unsafe fn set_resident_region", core)

    def test_missing_package_says_so(self):
        shim = "\n".join(added_lines("programs/nano7g/nanoapps/pocketnano.c"))
        self.assertIn('"PJNOPKG"', shim)
        self.assertIn("PACKAGE MISSING", shim)

    def test_on_device_log_tags(self):
        shim = "\n".join(added_lines("programs/nano7g/nanoapps/pocketnano.c"))
        for tag in ("PJFPS", "PJACCXY", "PJACCZ", "PJEND", "PJQJS"):
            self.assertIn(f'"{tag}"', shim)
        from host.pocketjs import summarize_trace
        neg = (1 << 32) - 6554
        lines = summarize_trace([("NOTPJ", 1, 2), ("PJBOOT", 6, 0), ("PJFPS", 300, 33400),
                                 ("PJACCXY", neg, 65536), ("PJACCZ", 0, 6), ("PJEND", 900, 30000)])
        text = "\n".join(lines)
        self.assertIn("fps 30.0", text)
        self.assertIn("x -6554", text)
        self.assertIn("1.00 g", text)
        self.assertIn("(30.0 fps)", text)
        self.assertNotIn("NOTPJ", text)
        named = "\n".join(summarize_trace([("PJOPEN", 0x109, 1), ("PJBOOT", 2, 0)]))
        self.assertIn("app #2 Cafe (QuickJS) boot", named)
        mem = "\n".join(summarize_trace([("PJSPARE", 358448, 1), ("PJMEMC", 80805, 45047), ("PJMEMU", 930518, 15656)]))
        self.assertIn("bundle text reused as heap: 350 KB", mem)
        self.assertIn("line tables 43 KB", mem)
        self.assertIn("QuickJS in use 908 KB (15656 allocations)", mem)

    def test_emulator_writes_only_data_fs(self):
        # packages stay read-only; writes are emulated in memory
        # under /Apps/Data/PocketJS/ only, anything else stops the run.
        src = (PJ / "emu.py").read_text(encoding="utf-8")
        self.assertIn("--serve", src)
        self.assertIn("--fs-dir", src)
        self.assertIn('WRITABLE = "/Apps/Data/PocketJS/"', src)
        self.assertIn("the app wrote outside {WRITABLE}", src)
        for hook in ("A_FS_REMOVE", "A_FS_MKDIR", "A_FS_GETATTR", "A_FS_RMDIR", "A_DIR_NEXT", "A_PATH_ASCHAR"):
            self.assertIn(f"address == {hook}", src)

    def test_data_fs(self):
        # globalThis.fs (upstream's nine ops) over the canonical
        # host-services fs.c, the SDK's whole-file writes underneath, a
        # per-app folder and quota, log lines, the Notes sample.
        shim = "\n".join(added_lines("programs/nano7g/nanoapps/nanofs.c"))
        self.assertIn("#include FS_C", shim)
        self.assertIn("if (!hb_fs_write(path, len ? data : (const uint8_t *)\"\", (uint32_t)len))", shim)
        self.assertIn('hb_trace_log("PJFSERR", v1, nanofs_error_code());', shim)
        self.assertIn('hb_trace_log("PJFSB", ok ? 1 : 0, (uint32_t)quota);', shim)
        qjs = "\n".join(added_lines("programs/nano7g/nanoapps/pocketqjs.c"))
        self.assertIn('#define PQFS_BASE "/Apps/Data/PocketJS"', qjs)
        self.assertIn("#define PQFS_QUOTA (128u << 10)", qjs)
        self.assertIn('"read", "write", "remove", "list", "stat", "mkdir", "rename", "usage", "lastError"', qjs)
        self.assertIn('PQFS_BASE "/.fs-tmp/%s"', qjs)
        self.assertIn("register_fs(s_ctx, data);", qjs)
        jscan = "\n".join(added_lines("programs/nano7g/nanoapps/nanojscan.c"))
        self.assertIn('if (!strcmp(format, "%llu"))', jscan)
        js = "\n".join(added_lines("programs/nano7g/src/js.rs"))
        self.assertIn('data: "notes\\0",', js)
        host = "\n".join(added_lines("hosts/nano7g/pocket.host.json"))
        self.assertIn('"data.fs"', host)
        app = "\n".join(added_lines("apps/nano7g-notes/app.tsx"))
        self.assertIn('write("launches.txt", String(before + 1));', app)
        self.assertIn('from "@pocketjs/framework/fs"', app)
        self.assertIn("tests/fs-nano7g.test.ts", (PJ / "nano7g.patch").read_text(encoding="utf-8"))
        from host.pocketjs import summarize_trace
        lines = "\n".join(summarize_trace([("PJFSB", 1, 131072), ("PJFS", 0x02000018, 0),
                                            ("PJFS", 0x03000000, 0), ("PJFSERR", 0x02000010, 3)]))
        self.assertIn("data.fs: folder ready, quota 128 KB", lines)
        self.assertIn("data.fs write ok (24 bytes)", lines)
        self.assertIn("data.fs remove ok", lines)
        self.assertIn("data.fs write FAILED: quota exceeded", lines)

    def test_menu_fits_nine_rows(self):
        shim = "\n".join(added_lines("programs/nano7g/nanoapps/pocketnano.c"))
        self.assertIn("return (PN_MENU_END - PN_MENU_TOP) / (int)s_rows;", shim)

    def test_catalog_row(self):
        reset_catalog_cache()
        for alias in ("jsquickjs", "pjquickjs", "quickjs"):
            self.assertEqual(resolve_name(alias), "JS QuickJS")
        row = {r["name"]: r for r in catalog()}["JS QuickJS"]
        self.assertEqual(row["category"], "first-party")
        self.assertEqual(row["bundle_id"], "org.nano7kit.pocketjs.quickjs")
        self.assertIn("QuickJS", row["author"])
        self.assertIn("/Apps/Data/PocketJS", row["note"])
        self.assertTrue(row["large_gl"])


class HardwareFixTests(unittest.TestCase):
    """Fixes from an early hardware run."""

    def test_spinner_stops_when_the_bar_is_full(self):
        ts = "\n".join(added_lines("apps/nano7g-hero/app.ts"))
        self.assertIn("SPIN_STEPS", ts)
        self.assertIn("setPhase(-1)", ts)

    def test_tilt_y_sign_and_neutral_hold(self):
        ts = "\n".join(added_lines("apps/nano7g-tilt/app.ts"))
        self.assertIn("setY(clamp(y() - round((b - beta0()) / 4.0)", ts)
        self.assertIn("setX(clamp(x() + round((g - gamma0()) / 4.0)", ts)
        self.assertIn("setCalibrated(false)", ts)

    def test_probe_figures_are_current(self):
        for path in ("host/apps.py", "host/app_catalog.py", "host/ports.py"):
            text = (ROOT / path).read_text(encoding="utf-8")
            self.assertNotIn("8.7/8.0", text, path)
        self.assertIn("7.0 MB free / 4.0 MB largest", (ROOT / "host" / "apps.py").read_text(encoding="utf-8"))


class StagedAppTests(unittest.TestCase):
    def setUp(self):
        reset_catalog_cache()

    def tearDown(self):
        reset_catalog_cache()

    def test_every_manifest_app_is_staged_and_relocatable(self):
        for app in apps_manifest()["apps"]:
            hbapp = EXTRA / "Executables" / f"{app['name']}.hbapp"
            icon = EXTRA / "Icons" / f"{app['name']}.bin"
            self.assertTrue(hbapp.is_file(), hbapp)
            self.assertEqual(icon.stat().st_size, 50208)
            data = hbapp.read_bytes()
            magic, entry, image, span, count, align = struct.unpack_from("<6I", data)
            self.assertEqual(magic, 0x314C5248)
            self.assertEqual(align, 16)
            self.assertLess(entry, image)
            self.assertEqual(len(data), 24 + image + count * 4)
            for (offset,) in struct.iter_unpack("<I", data[24 + image:]):
                self.assertLessEqual(offset + 4, image)
            # Baked fonts can hold a word equal to an old fixed address; the
            # build proves each one is asset bytes and records it per SHA-256.
            hits = [h["offset"] for h in legacy_hits(hbapp)]
            self.assertTrue(set(hits) <= proven_lookalikes(hbapp), (app["name"], hits))
            self.assertTrue((ROOT / "nanoapps" / "apps" / app["folder"] / "Info.plist").is_file())

    def test_catalog_row(self):
        self.assertEqual(resolve_name("jshero"), "JS Hero")
        self.assertEqual(resolve_name("pjhero"), "JS Hero")
        row = {r["name"]: r for r in catalog()}["JS Hero"]
        self.assertEqual(row["category"], "first-party")
        self.assertEqual(row["bundle_id"], "org.nano7kit.pocketjs.hero")
        self.assertTrue(row["present"])
        self.assertTrue(row["large_gl"], "over 576 KB: slot B")
        self.assertFalse(row["needs_payload"])
        self.assertTrue(row["compatible"])
        self.assertIn("PocketJS", row["author"])
        self.assertIn("MIT", row["author"])
        self.assertIn("slot B", row["note"])
        self.assertGreater(row["size"], 576 * 1024)
        self.assertLess(row["size"], 2 * 1024 * 1024)

    def test_samples_catalog_row(self):
        for alias in ("jssamples", "pjsamples", "pocketjs-samples"):
            self.assertEqual(resolve_name(alias), "JS Samples")
        row = {r["name"]: r for r in catalog()}["JS Samples"]
        self.assertEqual(row["category"], "first-party")
        self.assertEqual(row["bundle_id"], "org.nano7kit.pocketjs.samples")
        self.assertTrue(row["present"])
        self.assertTrue(row["large_gl"], "over 576 KB: slot B")
        self.assertTrue(row["compatible"])
        self.assertIn("PocketJS", row["author"])
        self.assertIn("slot B", row["note"])
        self.assertLess(row["size"], 3 * 1024 * 1024)

    def test_lookalike_proof_is_bound_to_the_bytes(self):
        exe = EXTRA / "Executables" / "JS Samples.hbapp"
        with tempfile.TemporaryDirectory() as td:
            other = Path(td) / "x.hbapp"
            data = bytearray(exe.read_bytes())
            data[-1] ^= 1
            other.write_bytes(bytes(data))
            self.assertEqual(proven_lookalikes(other), set())


class AddSlotBTests(unittest.TestCase):
    def setUp(self):
        reset_catalog_cache()
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self._state = os.environ.get("NANO7_STATE_DIR")
        os.environ["NANO7_STATE_DIR"] = self.td.name

    def tearDown(self):
        if self._state is None:
            os.environ.pop("NANO7_STATE_DIR", None)
        else:
            os.environ["NANO7_STATE_DIR"] = self._state
        reset_catalog_cache()

    def test_add_js_hero_uses_slot_b_and_copies_no_data(self):
        vol = Path(self.td.name) / "vol"
        shutil.copytree(ROOT / "untether" / "artifacts" / "device", vol / "Apps")
        nano = annotate_size(Nano(os_name="linux", device="/dev/loop9", raw_device="/dev/loop9",
                                  partition="/dev/loop9p1", usb_serial="TESTSERIAL",
                                  size=TESTED_DISK_SIZE, fstype="vfat", mountpoints=[str(vol)],
                                  model="iPod"))

        def fake_mount(_nano, **_kw):
            _nano.mountpoints = [str(vol)]
            return vol

        with mock.patch("host.fat.ensure_fat_mount", side_effect=fake_mount), mock.patch(
            "host.fat.assert_not_hfs"
        ), mock.patch("host.untether.unmount"), mock.patch(
            "host.untether.ensure_fat_mount", side_effect=fake_mount
        ), mock.patch("host.untether.close_finder_ipod_windows", return_value=0), mock.patch(
            "host.apps.check_usb_serial"
        ), mock.patch("host.apps.check_disk_size", return_value={"size": TESTED_DISK_SIZE}), mock.patch(
            "host.apps.check_backup", return_value="a" * 64
        ), mock.patch("host.apps.layout_for_disk_size", return_value=None), mock.patch(
            "host.apps.assert_not_hfs"
        ):
            rec = add_apps(nano, ["JS Hero"], backup=Path(self.td.name) / "missing.img",
                           backup_sha256="a" * 64, skip_wait=True, skip_backup_file=True)
        self.assertTrue(rec["ok"])
        self.assertTrue(rec["slot_b"])
        self.assertEqual(pack_app_names((vol / "Apps" / "AllApps.pack").read_bytes()),
                         ["Blocks", "Game Boy", "Probe"])
        self.assertEqual(pack_app_names((vol / "Apps" / "AllApps-B.pack").read_bytes()),
                         ["Blocks", "Game Boy", "Probe", "JS Hero"])
        copied = vol / "Apps" / "Executables" / "JS Hero.hbapp"
        self.assertEqual(copied.read_bytes(), (EXTRA / "Executables" / "JS Hero.hbapp").read_bytes())
        self.assertFalse((vol / "Apps" / "Data" / "JS Hero").exists())
        from host.compat import sha256_file

        self.assertEqual(sha256_file(vol / "Apps" / "Startup" / "resident-B.hbapp"), SLOT_B_SHA256)


class AddPackagesTests(unittest.TestCase):
    def test_packages_copy_to_apps_data_only(self):
        from host.pocketjs import add_packages, device_name, package_files
        with tempfile.TemporaryDirectory() as td:
            vol = Path(td) / "vol"
            shutil.copytree(ROOT / "untether" / "artifacts" / "device", vol / "Apps")
            before = {p.relative_to(vol): p.read_bytes() for p in vol.rglob("*") if p.is_file()}
            nano = mock.Mock()
            with mock.patch("host.device.find_nanos", return_value=[nano]), mock.patch(
                "host.device.format_nano", return_value="nano"
            ), mock.patch("host.fat.assert_not_hfs"), mock.patch("host.fat.ensure_fat_mount", return_value=vol), mock.patch(
                "host.untether.check_loader_hashes", return_value={"ok": True}
            ), mock.patch("host.untether.durable_flush_and_rehash"):
                rec = add_packages(nano, skip_wait=True)
            self.assertTrue(rec["ok"])
            data = vol / "Apps" / "Data" / "PocketJS"
            for key in apps_manifest()["packages"]:
                for src in package_files(key):
                    self.assertEqual((data / device_name(src)).read_bytes(), src.read_bytes())
            # Nothing else on the volume changed.
            for rel, blob in before.items():
                self.assertEqual((vol / rel).read_bytes(), blob, rel)
            self.assertEqual(sorted(p.relative_to(data).as_posix() for p in data.rglob("*") if p.is_file()),
                             sorted(device_name(p) for k in apps_manifest()["packages"] for p in package_files(k)))

    def test_packages_pass_counts_to_the_rehash(self):
        # the Mac run ended in KeyError: 'wrote' after a good copy.
        from host.pocketjs import add_packages
        with tempfile.TemporaryDirectory() as td:
            vol = Path(td) / "vol"
            shutil.copytree(ROOT / "untether" / "artifacts" / "device", vol / "Apps")
            nano = mock.Mock()
            with mock.patch("host.device.find_nanos", return_value=[nano]), mock.patch(
                "host.device.format_nano", return_value="nano"
            ), mock.patch("host.fat.assert_not_hfs"), mock.patch("host.fat.ensure_fat_mount", return_value=vol), mock.patch(
                "host.untether.check_loader_hashes", return_value={"ok": True}
            ), mock.patch("host.untether.durable_flush_and_rehash") as rehash:
                add_packages(nano, skip_wait=True)
                add_packages(nano, skip_wait=True)
            first, second = (c.args[1] for c in rehash.call_args_list)
            total = len(first["file_hashes"])
            self.assertEqual((first["wrote"], first["already"]), (str(total), "0"))
            self.assertEqual((second["wrote"], second["already"]), ("0", str(total)))

    def test_rehash_tolerates_a_record_without_counts(self):
        import io
        from contextlib import redirect_stdout
        from host import untether
        with tempfile.TemporaryDirectory() as td:
            vol = Path(td)
            rec = {"file_hashes": {"/Apps/Data/PocketJS/a.pocket": "0" * 64}, "verified": "yes"}
            out = io.StringIO()
            with mock.patch.object(untether, "mounted_root", return_value=vol), mock.patch.object(
                untether, "sync_mount"), mock.patch.object(untether, "unmount"), mock.patch.object(
                untether, "ensure_fat_mount", return_value=vol), mock.patch.object(
                untether, "volume_root_listing", return_value={"apps_name": "Apps"}), mock.patch.object(
                untether, "print_volume_root_listing"), mock.patch.object(
                untether, "verify_expected_hashes"), mock.patch.object(
                untether, "close_finder_ipod_windows", return_value=0), mock.patch(
                "host.fat.flush_library_dbs", return_value=[]), mock.patch("host.fat.sync_mount"), redirect_stdout(out):
                untether.durable_flush_and_rehash(mock.Mock(), rec)
            self.assertIn("DEVICE_FILES_OK: 1 written, 0 already present", out.getvalue())
            self.assertEqual(rec["durable"], "yes")

    def test_enter_prompt_says_typed_text_is_ignored(self):
        src = (ROOT / "host" / "sync.py").read_text()
        self.assertIn("Anything typed or pasted here is ignored", src)


class HomeScreenTests(unittest.TestCase):
    """JS QuickJS boots into the PocketJS home screen."""

    def test_shim_boots_home_and_launches_apps(self):
        shim = "\n".join(added_lines("programs/nano7g/nanoapps/pocketnano.c"))
        self.assertIn('"PJHOME"', shim)
        for fn in ("pocketnano_home_launch", "pocketnano_home_has", "pocketnano_home_from",
                   "pocketnano_home_clock", "pocketnano_home_date", "pocketnano_home_battery"):
            self.assertIn(fn, shim)
        self.assertIn("TAP TO GO BACK HOME", shim)
        qjs = "\n".join(added_lines("programs/nano7g/nanoapps/pocketqjs.c"))
        self.assertIn('"nano"', qjs)

    def test_home_only_apps_stay_off_the_list(self):
        js = "\n".join(added_lines("programs/nano7g/src/js.rs"))
        self.assertEqual(js.count("home_only: true"), 4)
        lib = "\n".join(added_lines("programs/nano7g/src/lib.rs"))
        self.assertIn("take_while(|app| !app.home_only)", lib)

    def test_apps_and_credit(self):
        src = ROOT / "build" / "pocketjs-6ca9f914f6"
        if not (src / "apps" / "nano7g-home").is_dir():
            self.skipTest("pocketjs checkout not built")
        home = (src / "apps" / "nano7g-home" / "app.tsx").read_text()
        for key in ("notebook", "talk", "motion", "cafe", "physics", "tilt"):
            self.assertIn(f'"{key}"', home)
        self.assertIn("(yui540)", (src / "apps" / "motions" / "app.tsx").read_text())

    def test_emulator_has_a_clock_and_no_home_switch(self):
        src = (PJ / "emu.py").read_text(encoding="utf-8")
        self.assertIn("--no-home", src)
        self.assertIn("A_RTC_INSTANCE = 0x0842BF60", src)
        self.assertIn("BATTERY = 0x0890C070", src)


class Pocketjs15Tests(unittest.TestCase):
    """readable panics, Home retries, lean mode, Free RAM deltas."""

    def test_panic_says_what_and_where(self):
        rs = "\n".join(added_lines("programs/nano7g/src/nanoapps.rs"))
        self.assertIn("OUT OF MEMORY: ", rs)
        self.assertIn("memory allocation of ", rs)
        self.assertIn("pocketnano_fail_msg(1, line", rs)
        shim = "\n".join(added_lines("programs/nano7g/nanoapps/pocketnano.c"))
        self.assertIn("void pocketnano_fail_msg(", shim)
        self.assertIn('" STOPPED"', shim)               # MOTION LAB STOPPED
        self.assertIn("pocketnano_title(s_index)", shim)

    def test_home_boot_retry_and_create_node_status(self):
        shim = "\n".join(added_lines("programs/nano7g/nanoapps/pocketnano.c"))
        self.assertIn('"PJRETRY"', shim)
        self.assertIn("s_home_retries < 2", shim)
        self.assertIn("LIFT, THEN TAP TO RETRY", shim)
        guest = "\n".join(added_lines("engine/guest-quickjs/src/guest.c"))
        self.assertIn("status %d, %u nodes made", guest)

    def test_lean_mode_with_a_split_heap(self):
        shim = "\n".join(added_lines("programs/nano7g/nanoapps/pocketnano.c"))
        self.assertIn("PN_LEAN_CUT (640u << 10)", shim)
        self.assertIn('"PJLEAN"', shim)
        self.assertIn("MUSIC OR BLUETOOTH HOLDS THE", shim)
        home = "\n".join(added_lines("apps/nano7g-home/app.tsx"))
        self.assertIn("LOW MEMORY: MUSIC OR BLUETOOTH", home)
        self.assertIn("nano.memory(3)", home)
        from host import pocketjs
        self.assertIn("PJLEAN", pocketjs.LOG_TAGS)
        self.assertIn("PJRETRY", pocketjs.LOG_TAGS)

    def test_free_ram_lists_what_each_call_freed(self):
        src = (ROOT / "nanoapps/apps/freeram/freeram.c").read_text()
        self.assertIn("FRAMDLT", src)
        self.assertIn("remember(s_action", src)
        self.assertIn("JS QuickJS: lean mode", src)
        self.assertIn("PJ_HOME_BLOCK (2508u << 10)", src)

    def test_motion_lab_keeps_the_credit(self):
        app = (ROOT / "build/pocketjs-6ca9f914f6/apps/motions/app.tsx")
        if app.exists():
            self.assertIn("(yui540)", app.read_text())


class Pocketjs14Tests(unittest.TestCase):
    """failures leave a reason, cheaper home transitions, Free RAM."""

    def test_free_ram_app_is_staged_and_read_only(self):
        from host.app_catalog import resolve_name
        self.assertEqual(resolve_name("freeram"), "Free RAM")
        self.assertTrue((ROOT / "untether/artifacts/extra/Executables/Free RAM.hbapp").is_file())
        src = (ROOT / "nanoapps/apps/freeram/freeram.c").read_text()
        for banned in ("hb_fs_write", "fopen", "iPod_Control", "prefs"):
            self.assertNotIn(banned, src.replace("no file or prefs writes", ""))

    def test_compose_one_to_one_and_no_home_circles(self):
        compose = "\n".join(added_lines("programs/nano7g/src/compose.rs"))
        self.assertIn("if inv == pad::ONE", compose)
        home = "\n".join(added_lines("apps/nano7g-home/app.tsx"))
        self.assertNotIn("rounded-full bg-[#a855f733]", home)

    def test_failures_are_diagnosable(self):
        shim = "\n".join(added_lines("programs/nano7g/nanoapps/pocketnano.c"))
        self.assertIn('"# fail: "', shim)
        self.assertIn('"PJFAILM"', shim)
        self.assertIn('"PJSTKX"', shim)
        self.assertIn("stack_guard_ok", shim)
        self.assertIn("s_fail_armed", shim)          # the typing finger cannot dismiss it
        self.assertIn("2000000u", shim)
        self.assertIn("void pocketnano_log_soon(void)", shim)
        fs = "\n".join(added_lines("programs/nano7g/nanoapps/nanofs.c"))
        self.assertIn("pocketnano_log_soon();", fs)
        qjs = "\n".join(added_lines("programs/nano7g/nanoapps/pocketqjs.c"))
        self.assertIn("void pocketqjs_exit_hook(void)", qjs)
        self.assertIn('"onexit"', qjs)
        self.assertIn('"memory"', qjs)
        self.assertIn('"media"', qjs)

    def test_log_reader_knows_the_new_lines(self):
        text = ("# head\n10 PJFAIL 5 0\n11 PJFAILM 2000000 2300000\n# fail: OUT OF MEMORY\n"
                "12 PJFS 33554442 210\n13 PJMUSIC 2 0\n14 PJSTKX 245000 245760\n15 PJLOGW 380000 9000\n")
        from host import pocketjs
        out = "\n".join(pocketjs.summarize_log_text(text))
        self.assertIn("failure message: OUT OF MEMORY", out)
        self.assertIn("in 210 ms", out)
        self.assertIn("music stop (player playing)", out)
        self.assertIn("PRIVATE STACK FULL", out)
        self.assertIn("log file write took 380 ms", out)

    def test_raster_hoists_texture_math(self):
        raster = "\n".join(added_lines("engine/raster/src/lib.rs"))
        self.assertIn("let same = output_scale == atlas_density;", raster)
        self.assertIn("fn matches_the_per_pixel_loop()", raster)

    def test_home_zoom_moves_one_box(self):
        home = "\n".join(added_lines("apps/nano7g-home/app.tsx"))
        self.assertIn('["insetL", r.x, 0]', home)
        self.assertNotIn('animate(desk as never, "scale"', home)
        self.assertIn('key: "system"', home)
        self.assertIn("clock.txt", home)

    def test_notes_saves_less(self):
        notes = "\n".join(added_lines("apps/nano7g-notebook/app.tsx"))
        self.assertIn("dirty = 90;", notes)
        self.assertIn("createMemo(() => wrap(text()))", notes)
        self.assertIn("if (line === lastIndex) return;", notes)
        self.assertIn("nanoHost.onexit", notes)
        kb = "\n".join(added_lines("apps/nano7g-notebook/keyboard.tsx"))
        self.assertNotIn("shadow-lg", kb)

    def test_free_ram_app(self):
        app = ROOT / "nanoapps" / "apps" / "freeram"
        self.assertIn("<string>Free RAM</string>", (app / "Info.plist").read_text())
        src = (app / "freeram.c").read_text()
        self.assertIn("A_TASK_STOP 0x083bf040u", src)
        self.assertIn("hb_media_set_paused(true)", src)
        self.assertIn("hb_os_heap_largest()", src)
        self.assertNotIn("hb_fs_write", src)        # reads and media calls only
        self.assertNotIn("hb_prefs", src)

    def test_emulator_has_a_media_player(self):
        src = (PJ / "emu.py").read_text(encoding="utf-8")
        self.assertIn("A_TASK_STOP = ", src.replace(", A_TASK_STOP = ", "A_TASK_STOP = "))
        self.assertIn("media: ", src)


class DocsAndCliTests(unittest.TestCase):
    def test_credits(self):
        text = (PJ / "README.md").read_text(encoding="utf-8")
        for name in ("PocketJS", "wInd3x", "ipod_sun", "NanoApps", "nano7-untethered", "MIT"):
            self.assertIn(name, text)
        section = (ROOT / "CREDITS.md").read_text(encoding="utf-8")
        for name in ("PocketJS", "wInd3x", "ipod_sun", "NanoApps", "nano7-untethered", "yui540"):
            self.assertIn(name, section)
        self.assertNotIn(EM_DASH, section)

    def test_cli_lists_apps_and_commands(self):
        r = subprocess.run([sys.executable, str(START), "pocketjs", "apps"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("JS Hero", r.stdout)
        self.assertIn("./start apps add 'JS Hero'", r.stdout)
        h = subprocess.run([sys.executable, str(START), "pocketjs", "--help"], capture_output=True, text=True)
        for word in ("build", "emu", "apps", "hello"):
            self.assertIn(word, h.stdout)

    def test_unknown_app_is_refused(self):
        r = subprocess.run([sys.executable, str(START), "pocketjs", "emu", "nope"], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("unknown PocketJS app", r.stdout + r.stderr)


@unittest.skipUnless(os.environ.get("NANO7KIT_EMU") == "1", "set NANO7KIT_EMU=1 (needs unicorn; about a minute)")
class EmulatorSmokeTests(unittest.TestCase):
    def test_boot_and_first_frames(self):
        sys.path.insert(0, str(PJ))
        import emu  # noqa: E402

        hbapp = EXTRA / "Executables" / "JS Hero.hbapp"
        e = emu.Emu(str(hbapp), 3 * 1024 * 1024)
        frame = e.call(emu.BASE + e.entry, [0, emu.FB, emu.W, emu.H, emu.SHARED])
        self.assertTrue(frame)
        for _ in range(2):
            e.call(frame, [])
        tags = [t[0] for t in e.trace]
        self.assertEqual(tags[:5], ["PJOPEN", "PJHEAP", "PJOS", "PJBOOT", "PJLIVE"])
        self.assertNotIn("PJFAIL", tags)
        self.assertEqual(len(e.heap.blocks), 1)
        self.assertIn("PJSTK", tags)
        # The core ran on the private stack: the OS stack stays shallow.
        self.assertLess(emu.STACK_TOP - 16 - e.min_sp, 2 * 1024)
        fb = bytes(e.uc.mem_read(emu.FB, emu.W * emu.H * 4))
        self.assertGreater(len(set(fb[i:i + 4] for i in range(0, len(fb), 4 * 97))), 8)

    def test_flag_file_puts_the_core_back_on_the_os_stack(self):
        sys.path.insert(0, str(PJ))
        import emu  # noqa: E402

        hbapp = EXTRA / "Executables" / "JS Hero.hbapp"
        e = emu.Emu(str(hbapp), 3 * 1024 * 1024, files=["/Apps/Data/PocketJS/no_private_stack.txt"])
        frame = e.call(emu.BASE + e.entry, [0, emu.FB, emu.W, emu.H, emu.SHARED])
        for _ in range(2):
            e.call(frame, [])
        opened = [t for t in e.trace if t[0] == "PJOPEN"]
        self.assertEqual(opened[0][2], 0)
        tags = [t[0] for t in e.trace]
        self.assertIn("PJLIVE", tags)
        self.assertNotIn("PJSTK", tags)
        self.assertNotIn("PJFAIL", tags)
        self.assertGreater(emu.STACK_TOP - 16 - e.min_sp, 8 * 1024)

    def test_samples_refuse_cleanly_when_the_heap_is_fragmented(self):
        sys.path.insert(0, str(PJ))
        import emu  # noqa: E402

        mib = 1024 * 1024
        hbapp = EXTRA / "Executables" / "JS Samples.hbapp"
        # Modelled OS heap: the image takes most of the 2.5 MB block, so Hero
        # (about 1.4 MB) finds no block and must say so instead of starting.
        e = emu.Emu(str(hbapp), 0, regions=[int(2.5 * mib), mib, mib])
        frame = e.call(emu.BASE + e.entry, [0, emu.FB, emu.W, emu.H, emu.SHARED])
        for down in (True, True, False, False):
            e.touch(120, 229, down) if down else e.touch(0, 0, False)
            e.call(frame, [])
        tags = [t[0] for t in e.trace]
        self.assertEqual(tags[:2], ["PJOPEN", "PJNOMEM"])
        self.assertNotIn("PJBOOT", tags)
        self.assertEqual(len(e.heap.blocks), 0)


if __name__ == "__main__":
    unittest.main()


class Pocketjs6Tests(unittest.TestCase):
    """ui.physics, text input through the on-screen keyboard,
    streamed glyphs from a device-local io.offload provider."""

    def host_caps(self):
        import json
        text = "\n".join(added_lines("hosts/nano7g/pocket.host.json"))
        start = text.index("{")
        return json.loads(text[start:])["capabilities"]

    def test_capabilities(self):
        caps = self.host_caps()
        for cap in ("ui.physics", "io.offload", "text.glyphs.streamed", "text.glyphs.baked", "input.touch"):
            self.assertIn(cap, caps)
        # input.text means a hardware keyboard; the nano types through the OSK.
        self.assertNotIn("input.text", caps)
        self.assertIn("data.fs", caps)  # approved storage under /Apps/Data/PocketJS

    def test_samples_are_packages(self):
        m = apps_manifest()["packages"]
        self.assertEqual(m["physics"]["manifest"], "apps/nano7g-physics/pocket.app.json")
        self.assertEqual(m["type"]["manifest"], "apps/nano7g-type/pocket.app.json")
        self.assertEqual(m["fonts"]["manifest"], "apps/nano7g-fonts/pocket.app.json")
        physics = "\n".join(added_lines("apps/nano7g-physics/app.tsx"))
        self.assertIn("createWorld(", physics)
        self.assertIn("onHit(", physics)
        typing = "\n".join(added_lines("apps/nano7g-type/app.tsx"))
        self.assertIn("<TextField", typing)
        fonts = "\n".join(added_lines("apps/nano7g-fonts/app.tsx"))
        self.assertIn('provider: "local"', fonts)
        self.assertIn('"fs.read-text"', fonts)

    def test_font_data_is_staged(self):
        from host.pocketjs import data_files, device_name
        files = dict((name, path) for path, name in data_files("fonts"))
        self.assertEqual(set(files), {"fonts/phrases.txt", "fonts/intl.pjfa"})
        self.assertEqual(files["fonts/intl.pjfa"].read_bytes()[:4], b"PJFA")
        self.assertLess(files["fonts/intl.pjfa"].stat().st_size, 1 << 20)
        self.assertIn("Привет", files["fonts/phrases.txt"].read_text(encoding="utf-8"))
        self.assertEqual(device_name(files["fonts/intl.pjfa"]), "fonts/intl.pjfa")

    def test_local_provider_reads_only_the_data_folder(self):
        rs = "\n".join(added_lines("programs/nano7g/src/local.rs"))
        self.assertIn('const ROOT: &[u8] = b"/Apps/Data/PocketJS/";', rs)
        self.assertIn('path.windows(2).any(|w| w == b"..")', rs)
        self.assertIn("pub const MAX_ARCHIVE: u32 = 1 << 20;", rs)
        self.assertNotIn("write(", rs.replace("write!(", "").replace("Write", ""))
        c = "\n".join(added_lines("programs/nano7g/nanoapps/pocketqjs.c"))
        self.assertIn("register_offload(s_ctx);", c)
        for method in ("font.open", "font.glyphs", "font.stats", "fs.read-text", "font.close"):
            self.assertIn(f'"{method}"', c)
        self.assertNotIn("hb_fs_write", c)

    def test_keyboard_return_key_is_reachable(self):
        rs = "\n".join(added_lines("programs/nano7g/src/pad.rs"))
        self.assertIn("key(PANEL_W - 44, 4, 40, 32, Glyph::Menu, Action::Menu)", rs)
        js = "\n".join(added_lines("programs/nano7g/src/js.rs"))
        i = js.index('key: "type"')
        self.assertIn("pad: Pad::TopMenu", js[i:i + 300])

    def test_log_has_the_heap_split(self):
        from host.pocketjs import summarize_trace
        lines = summarize_trace([("PJSPLIT", 961264, 714492)])
        self.assertIn("QuickJS 938 KB, core and files 697 KB", "\n".join(lines))

    def test_other_fingers_come_from_a_tolerant_tracker(self):
        # the iPod dropped the second finger (status 1 samples
        # were skipped). Every queue node is read once, contacts live until a
        # learned lift status or 600 ms without samples.
        shim = "\n".join(added_lines("programs/nano7g/nanoapps/pocketnano.c"))
        self.assertNotIn("hb_touch_poll_multi(f)", shim)
        self.assertIn("static void read_queues(", shim)
        self.assertIn("#define PN_TRACK_HOLD_US 600000u", shim)
        # touch-snapshot (3 Nov iPod logs: land 0, move 2, lift 1): fixed, not learned
        self.assertIn("static int s_up_status = 1;", shim)
        self.assertNotIn('"PJTUP"', shim)
        self.assertIn('pn_log_raw("PJTRAW"', shim)
        self.assertIn('pocketnano_log("PJTOUCH"', shim)
        self.assertIn("pocketnano_fingers(out, k);", shim)
        self.assertIn('pocketnano_log("PJFING"', shim)
        self.assertIn("hb_touch_drain_all();", shim)
        # The lift status is learned in every state (menu taps teach it), and
        # a queue that repeats a still sample ends its contact when it empties.
        self.assertIn("    touch_poll(touch);\n    if (s_state == PN_MENU) {", shim)
        self.assertIn("s_q_ended[q] = s_q_stream[q];", shim)
        lib = "\n".join(added_lines("programs/nano7g/src/lib.rs"))
        self.assertIn("chord |= 1 << k;", lib)

    def test_pj_lines_also_go_to_a_log_file(self):
        shim = "\n".join(added_lines("programs/nano7g/nanoapps/pocketnano.c"))
        self.assertIn('#define PN_LOG_FILE PN_LOG_DIR "/" POCKETNANO_KEY ".txt"', shim)
        self.assertIn('#define PN_LOG_PREV PN_LOG_DIR "/" POCKETNANO_KEY "-prev.txt"', shim)
        self.assertIn("#define PN_FLUSH_US 30000000u", shim)
        self.assertIn('hb_trace_log("PJLOGNO"', shim)
        self.assertNotIn('hb_trace_log("PJ', shim.replace('hb_trace_log("PJLOGNO"', ""))
        for f in ("programs/nano7g/nanoapps/pocketqjs.c", "programs/nano7g/nanoapps/nanofs.c"):
            self.assertIn("#define hb_trace_log pocketnano_log", "\n".join(added_lines(f)), f)
        self.assertIn("-DPOCKETNANO_KEY=", "\n".join(added_lines("programs/nano7g/nanoapps/Makefile")))

    def test_hero_samples_stop_spinning_and_keep_the_bar_on_screen(self):
        text = patch_text()
        for d in ("gba-hero", "ipodnano-hero"):
            self.assertIn(f"apps/nano7g-samples/{d}/", text)
        self.assertIn("SPIN_STEPS: i32 = 9", text)
        self.assertIn("imod(count(), 8) * 2", text)
        self.assertIn("setSpinnerFrame(-1)", text)

    def test_gestures_explains_itself(self):
        app = "\n".join(added_lines("apps/nano7g-gestures/app.tsx"))
        self.assertIn("PINCH WITH 2 FINGERS, TAP: +1", app)
        self.assertIn("onDown: down, onUp: up", app)

    def test_log_reads_the_text_file(self):
        from host.pocketjs import parse_log_text, summarize_log_text
        text = ("# JS QuickJS log: ms TAG v1 v2\n0 PJOPEN 265 1\n10 PJTUP 2 3\n"
                "20 PJTRAW 16842757 6553800\n30 PJTOUCH 67331 131076\n40 PJFING 2 3\n41 PJFI")
        self.assertEqual(len(parse_log_text(text)), 5)
        lines = "\n".join(summarize_log_text(text))
        self.assertIn("lift status learned: 2 (statuses while down: 0,1)", lines)
        self.assertIn("raw touch list 1 status 1 id 5 at 100,200", lines)
        self.assertIn("OS lists used 0,1, statuses 0,1,2, at most 1 extra fingers", lines)
        self.assertIn("2 fingers at once", lines)

    def test_log_reads_every_app_file_and_explains_absence(self):
        from host import pocketjs
        files = {"/Apps/Data/PocketJS/logs/pjquickjs.txt": b"0 PJFING 2 3\n"}

        class Ops:
            def read_bytes(self, fat):
                return files.get(fat)

        with tempfile.TemporaryDirectory() as td:
            found = pocketjs.read_log_files(Ops(), Path(td))
            self.assertEqual([f[1] for f in found], ["/Apps/Data/PocketJS/logs/pjquickjs.txt"])
            self.assertTrue((Path(td) / "pjquickjs.txt").is_file())
        paths = [f for _n, f, _l in pocketjs.log_files()]
        self.assertIn("/Apps/Data/PocketJS/logs/pjhero-prev.txt", paths)
        steps = "\n".join(pocketjs.LOG_STEPS)
        self.assertIn("sudo -E ./start pocketjs log", steps)
        self.assertIn("Sleep/Wake and Home", steps)

    def test_sudo_runs_give_repo_files_back(self):
        from host import owner
        self.assertIsNone(owner.sudo_ids({"SUDO_UID": "501", "SUDO_GID": "20"}, euid=501))
        self.assertIsNone(owner.sudo_ids({}, euid=0))
        self.assertEqual(owner.sudo_ids({"SUDO_UID": "501", "SUDO_GID": "20"}, euid=0), (501, 20))
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "a" / "b").mkdir(parents=True)
            (Path(td) / "a" / "b" / "f").write_text("x")
            calls = []
            real = os.lstat

            class St:
                st_uid = 0

            n = owner.restore_owner(Path(td), 501, 20, lchown=lambda p, u, g: calls.append((p, u, g)),
                                    lstat=lambda p: St())
            self.assertEqual(n, 4)
            self.assertTrue(all(u == 501 and g == 20 for _p, u, g in calls))
            calls.clear()
            owner.restore_owner(Path(td), 501, 20, lchown=lambda p, u, g: calls.append(p))
            if real(td).st_uid != 0:
                self.assertEqual(calls, [])
        start = (Path(__file__).resolve().parents[1] / "start").read_text()
        self.assertIn("restore_repo_owner(repo)", start)

    def test_log_has_the_finger_count(self):
        from host.pocketjs import summarize_trace
        self.assertIn("2 fingers at once (slots 0x3)", "\n".join(summarize_trace([("PJFING", 2, 3)])))

    def test_log_has_the_quickjs_breakdown(self):
        from host.pocketjs import summarize_trace
        lines = "\n".join(summarize_trace([("PJMEMO", 171360, 4284), ("PJMEMP", 409600, 900)]))
        self.assertIn("objects 167 KB (4284)", lines)
        self.assertIn("properties and shapes 400 KB (900)", lines)

    def test_frames_skip_unchanged_scenes_and_use_faster_soft_float(self):
        # a frame with no op, no input and nothing animating keeps
        # the frame on screen instead of rebuilding the DrawList.
        rt = "\n".join(added_lines("engine/runtime/src/lib.rs"))
        self.assertIn('cfg!(feature = "quiet-frames")', rt)
        self.assertIn("self.quiet = untouched && state.ui.settled();", rt)
        core = "\n".join(added_lines("engine/core/src/lib.rs"))
        self.assertIn("pub fn settled(&self) -> bool {", core)
        self.assertIn("n.alive && n.sprite_frames > 0", core)
        cargo = "\n".join(added_lines("programs/nano7g/Cargo.toml"))
        self.assertIn('features = ["quiet-frames"]', cargo)
        # libgcc's assembly add/multiply and integer compares replace the
        # generic weak soft-float routines; still no FP instructions.
        mk = "\n".join(added_lines("programs/nano7g/nanoapps/Makefile"))
        self.assertIn("_arm_addsubsf3.o", mk)
        self.assertIn("LDLIBS := $(FASTFP_OBJS) $(LDLIBS)", mk)
        self.assertNotIn("_arm_cmpsf2.o", mk)
        cmp = "\n".join(added_lines("programs/nano7g/nanoapps/fastcmp.c"))
        self.assertIn("int __aeabi_fcmplt(float a, float b)", cmp)
        self.assertIn("int __aeabi_dcmpeq(double a, double b)", cmp)
        raster = "\n".join(added_lines("engine/raster/src/surface.rs"))
        self.assertIn("fn blend_span(", raster)
