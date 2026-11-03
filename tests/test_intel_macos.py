#!/usr/bin/env python3
"""Intel Mac bootstrap, TZ-stable firmware, libusb CGO, rustup, Python 3.10."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from host.compat import force_utc_timezone
from host.deps import _pip, rustup_nightly
from host.firmware import FIRMWARE_BUILD_FAILED, firmware_build_env
from host.usb import (
    _append_macos_rpath,
    ipod_sg_raw_bin,
    ipod_sg_raw_src_hash,
    libusb_cgo_env,
    libusb_pkgconfig_ok,
    usbscsi_bin,
    usbscsi_sources,
    usbscsi_src_hash,
)


def _load_start():
    # `start` has no .py suffix, so spec_from_file_location has no loader.
    loader = importlib.machinery.SourceFileLoader("nano7kit_start", str(ROOT / "start"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class RequirePythonTests(unittest.TestCase):
    def test_accepts_3_10(self):
        start = _load_start()
        buf = io.StringIO()
        self.assertEqual(
            start.require_python(
                version_info=(3, 10, 14),
                executable="/usr/bin/python3",
                stream=buf,
            ),
            0,
        )
        self.assertEqual(buf.getvalue(), "")

    def test_rejects_anaconda_3_8(self):
        start = _load_start()
        buf = io.StringIO()
        code = start.require_python(
            version_info=(3, 8, 16),
            executable="/opt/anaconda3/bin/python",
            stream=buf,
        )
        self.assertEqual(code, 2)
        text = buf.getvalue()
        self.assertIn("3.10", text)
        self.assertIn("3.8.16", text)
        self.assertIn("Pillow==12.1.1", text)
        self.assertIn("macos-intel-bootstrap.sh", text)
        self.assertNotIn("\u2014", text)


class TimezoneTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("TZ", None)
        if hasattr(time, "tzset"):
            time.tzset()

    def test_firmware_build_env_forces_utc(self):
        env = firmware_build_env({"PATH": "/bin", "HOME": "/tmp"}, python=Path("/venv/bin/python"))
        self.assertEqual(env["TZ"], "UTC")
        self.assertEqual(env["PYTHON"], "/venv/bin/python")
        self.assertEqual(os.environ.get("TZ"), "UTC")

    def test_helvetica_script_exports_tz_utc(self):
        text = (ROOT / "untether" / "scripts" / "build-helvetica-cff-candidate.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("export TZ=UTC", text)
        self.assertIn("041d4f61", text)
        self.assertIn("39579dba", text)
        self.assertIn("fat_patch", text)

    def test_refused_message_blames_timezone_before_tools(self):
        self.assertIn("timezone", FIRMWARE_BUILD_FAILED.lower())
        self.assertIn("041d4f61", FIRMWARE_BUILD_FAILED)
        self.assertIn("39579dba", FIRMWARE_BUILD_FAILED)
        self.assertIn("TZ=UTC", FIRMWARE_BUILD_FAILED)
        tools_at = FIRMWARE_BUILD_FAILED.find("rustup")
        tz_at = FIRMWARE_BUILD_FAILED.find("timezone")
        self.assertGreater(tools_at, tz_at)
        self.assertNotIn("Need rustup nightly-2023-12-20, arm-none-eabi-gcc", FIRMWARE_BUILD_FAILED)

    def test_fat_patch_still_setinfo_roundtrips(self):
        text = (
            ROOT / "untether" / "ipod_sun_untethered" / "helpers" / "fat_patch.py"
        ).read_text(encoding="utf-8")
        self.assertIn("setinfo", text)
        self.assertIn("original_info.raw", text)

    def test_combine_theme_calls_force_utc(self):
        src = (ROOT / "host" / "theme.py").read_text(encoding="utf-8")
        self.assertIn("force_utc_timezone()", src)
        self.assertGreaterEqual(src.count("force_utc_timezone()"), 2)

    def test_force_utc_timezone_sets_tz(self):
        os.environ["TZ"] = "Europe/London"
        if hasattr(time, "tzset"):
            time.tzset()
        force_utc_timezone()
        self.assertEqual(os.environ["TZ"], "UTC")


class LibusbCgoTests(unittest.TestCase):
    def test_pkgconfig_ok_uses_exists_not_have_only(self):
        with mock.patch("host.usb.have", return_value=True):
            with mock.patch("host.usb.run") as run_m:
                run_m.return_value = mock.Mock(returncode=0)
                self.assertTrue(libusb_pkgconfig_ok())
                cmd = run_m.call_args[0][0]
                self.assertEqual(cmd[:3], ["pkg-config", "--exists", "libusb-1.0"])
        with mock.patch("host.usb.have", return_value=True):
            with mock.patch("host.usb.run") as run_m:
                run_m.return_value = mock.Mock(returncode=1)
                self.assertFalse(libusb_pkgconfig_ok())
        with mock.patch("host.usb.have", return_value=False):
            self.assertFalse(libusb_pkgconfig_ok())

    def test_cgo_keeps_preset_flags_and_adds_macos_rpath(self):
        preset = {
            "CGO_CPPFLAGS": "-I/mamba/include/libusb-1.0",
            "CGO_LDFLAGS": "-L/mamba/lib -lusb-1.0",
        }
        with mock.patch("host.usb.is_macos", return_value=True):
            with mock.patch("host.usb.libusb_pkgconfig_ok", return_value=False):
                env = libusb_cgo_env(preset)
        self.assertEqual(env["CGO_CPPFLAGS"], "-I/mamba/include/libusb-1.0")
        self.assertIn("-L/mamba/lib", env["CGO_LDFLAGS"])
        self.assertIn("-Wl,-rpath,/mamba/lib", env["CGO_LDFLAGS"])

    def test_cgo_uses_pkg_config_when_cgo_unset(self):
        def fake_out(cmd, **_kwargs):
            joined = " ".join(cmd)
            if "--cflags" in joined:
                return "-I/opt/libusb/include/libusb-1.0\n"
            if "--libs" in joined:
                return "-L/opt/libusb/lib -lusb-1.0\n"
            if "--variable=libdir" in joined:
                return "/opt/libusb/lib\n"
            return ""

        with mock.patch("host.usb.is_macos", return_value=True):
            with mock.patch("host.usb.libusb_pkgconfig_ok", return_value=True):
                with mock.patch("host.usb.run_out", side_effect=fake_out):
                    env = libusb_cgo_env({})
        self.assertIn("-I/opt/libusb/include", env["CGO_CPPFLAGS"])
        self.assertIn("-L/opt/libusb/lib", env["CGO_LDFLAGS"])
        self.assertIn("-Wl,-rpath,/opt/libusb/lib", env["CGO_LDFLAGS"])

    def test_rpath_not_duplicated(self):
        with mock.patch("host.usb.is_macos", return_value=True):
            once = _append_macos_rpath("-L/a/lib -lusb-1.0", ["/a/lib"])
            twice = _append_macos_rpath(once, ["/a/lib"])
        self.assertEqual(once.count("-Wl,-rpath,/a/lib"), 1)
        self.assertEqual(twice.count("-Wl,-rpath,/a/lib"), 1)

    def test_usbscsi_and_wind3x_call_libusb_cgo_env(self):
        src = (ROOT / "host" / "usb.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(src.count("libusb_cgo_env("), 2)
        self.assertIn("def build_usbscsi", src)
        usbscsi = src[src.index("def build_usbscsi") :]
        self.assertIn("libusb_cgo_env", usbscsi)
        self.assertIn("src-sha256", usbscsi)
        self.assertIn("usbscsi_src_hash()", usbscsi)
        self.assertNotIn("brew --prefix libusb", usbscsi)
        sg = src[src.index("def build_ipod_sg_raw") : src.index("def build_usbscsi")]
        self.assertIn("DiskArbitration", sg)
        self.assertIn("IOKit", sg)
        self.assertIn("CoreFoundation", sg)
        wind = src[src.index("def build_wind3x") : src.index("def run_wind3x")]
        self.assertIn("libusb_cgo_env", wind)

    def test_versioned_helper_paths_embed_source_hash(self):
        usb = str(usbscsi_bin())
        sg = str(ipod_sg_raw_bin())
        self.assertIn(usbscsi_src_hash()[:12], usb)
        self.assertIn(ipod_sg_raw_src_hash()[:12], sg)
        self.assertRegex(usbscsi_src_hash(), r"^[0-9a-f]{64}$")
        self.assertRegex(ipod_sg_raw_src_hash(), r"^[0-9a-f]{64}$")
        names = {p.name for p in usbscsi_sources()}
        self.assertIn("main.go", names)
        self.assertIn("logic.go", names)
        self.assertIn("seize_darwin.go", names)
        self.assertIn("seize_other.go", names)
        self.assertNotIn("logic_test.go", names)
        seize = (ROOT / "host" / "cmd" / "usbscsi" / "seize_darwin.go").read_text(
            encoding="utf-8"
        )
        self.assertIn("USBInterfaceOpenSeize", seize)
        self.assertIn("USBDeviceOpenSeize", seize)
        self.assertIn("//go:build darwin", seize)
        other = (ROOT / "host" / "cmd" / "usbscsi" / "seize_other.go").read_text(
            encoding="utf-8"
        )
        self.assertIn("//go:build !darwin", other)

    def test_deps_libusb_uses_pkgconfig_exists(self):
        src = (ROOT / "host" / "deps.py").read_text(encoding="utf-8")
        self.assertIn("libusb_pkgconfig_ok", src)
        self.assertNotIn("or have(\"pkg-config\")", src)
        self.assertNotIn("or have('pkg-config')", src)


class RustupAndPipTests(unittest.TestCase):
    def test_existing_rustup_still_pins_nightly_and_thumbv6m(self):
        calls = []

        def fake_run(cmd, **_kwargs):
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with mock.patch("host.deps.have", return_value=True):
            with mock.patch("host.deps.run", fake_run):
                rustup_nightly()
        flat = [" ".join(c) for c in calls]
        self.assertTrue(any("toolchain install" in f and "nightly-2023-12-20" in f for f in flat))
        self.assertTrue(any("thumbv6m-none-eabi" in f for f in flat))
        self.assertFalse(any("sh.rustup.rs" in f for f in flat))

    def test_rustup_init_uses_no_modify_path(self):
        calls = []

        def fake_run(cmd, **_kwargs):
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with mock.patch("host.deps.have", return_value=False):
            with mock.patch("host.deps.which", return_value=None):
                with mock.patch("host.deps.run", fake_run):
                    rustup_nightly()
        self.assertTrue(any("--no-modify-path" in " ".join(c) for c in calls))
        self.assertTrue(any("sh.rustup.rs" in " ".join(c) for c in calls))

    def test_rustup_init_binary_no_modify_path(self):
        calls = []

        def fake_run(cmd, **_kwargs):
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with mock.patch("host.deps.have", return_value=False):
            with mock.patch("host.deps.which", return_value="/prefix/bin/rustup-init"):
                with mock.patch("host.deps.run", fake_run):
                    rustup_nightly()
        self.assertEqual(calls[0][0], "/prefix/bin/rustup-init")
        self.assertIn("--no-modify-path", calls[0])
        self.assertIn("-y", calls[0])

    def test_ensure_deps_always_calls_rustup_nightly(self):
        src = (ROOT / "host" / "deps.py").read_text(encoding="utf-8")
        self.assertIn("rustup_nightly()", src)
        self.assertNotIn('extra=rustup_nightly', src)

    def test_pip_uses_venv_not_user(self):
        calls = []

        def fake_run(cmd, **_kwargs):
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "ok\n", "")

        with tempfile.TemporaryDirectory() as td:
            venv = Path(td) / "venv"
            (venv / "bin").mkdir(parents=True)
            (venv / "bin" / "pip").write_text("#!/bin/sh\n")
            with mock.patch("host.deps._ensure_venv", return_value=venv):
                with mock.patch("host.deps.run", fake_run):
                    self.assertTrue(_pip("pillow"))
        self.assertTrue(calls)
        self.assertNotIn("--user", calls[0])
        self.assertEqual(calls[0][0], str(venv / "bin" / "pip"))
        self.assertIn("install", calls[0])
        self.assertIn("pillow", calls[0])


class IntelBootstrapTests(unittest.TestCase):
    def test_script_pins_hashes_and_writes_env(self):
        path = ROOT / "scripts" / "macos-intel-bootstrap.sh"
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        self.assertTrue(os.access(path, os.X_OK), "bootstrap script must be executable")
        for needle in (
            "1aac2a0c345c4ee441040a07784c5b6ed432738fa4500b15cb1e758870205320",
            "6700067389a53a1607d30aa8d6e01d198230397029faa0b109e89bc871ab5a0e",
            "2d9e717dd4f7751d18936ae1365d25916534105ebcb7583039eff1092b824505",
            "eea170403f48f0cd19b3d940e4bd12630a82601e25f944f47654b13d9d7eb5d4",
            "f547d77c32d50d82b8228899b936bf2b3c72ce0a70fb3b364e7fba8891eba781",
            "micromamba",
            "14.2.rel1",
            "darwin-x86_64",
            "--no-modify-path",
            "TZ=UTC",
            "env.sh",
            "-Wl,-rpath",
            "mtools",
            "nightly-2023-12-20",
            "thumbv6m-none-eabi",
            "xcode-select --install",
        ):
            self.assertIn(needle, text)
        self.assertIn("no sudo", text.lower())
        self.assertIsNone(re.search(r"(?m)^\s*sudo\b", text))
        self.assertNotIn("sudo -E", text)
        self.assertNotIn("sudo -n", text)
        self.assertNotIn(".zshrc", text)
        self.assertNotIn(".bashrc", text)
        self.assertNotIn("\u2014", text)

    def test_deps_explains_intel_no_brew(self):
        src = (ROOT / "host" / "deps.py").read_text(encoding="utf-8")
        self.assertIn("Homebrew dropped Intel", src)
        self.assertIn("macos-intel-bootstrap.sh", src)
        self.assertIn("intel_macos_without_brew", src)



class NoHomebrewToolchainTests(unittest.TestCase):
    """A Mac without Homebrew: libzstd for the Arm compiler, matching Go, loud deps."""

    def test_bootstrap_builds_pinned_libzstd_and_fixes_the_toolchain(self):
        text = (ROOT / "scripts" / "macos-intel-bootstrap.sh").read_text(encoding="utf-8")
        self.assertIn("ZSTD_SHA=8c29e06cf42aacc1eafc4077ae2ec6c6fcb96a626157e0593d5e82a34fd403c1", text)
        self.assertIn('fetch "$ZSTD_URL" "$CACHE/zstd-${ZSTD_VER}.tar.gz" "$ZSTD_SHA"', text)
        self.assertIn("darwin-arm-zstd.sh\" --check-or-fix --zstd \"$PREFIX/lib/libzstd.1.dylib\"", text)
        self.assertIn("unset GOROOT", text)
        self.assertIn("export GOTOOLCHAIN=local", text)
        from host.installer import BOOTSTRAP_STAMP

        self.assertIn("export " + BOOTSTRAP_STAMP, text)

    def test_zstd_fix_looks_in_the_bootstrap_prefix(self):
        text = (ROOT / "scripts" / "darwin-arm-zstd.sh").read_text(encoding="utf-8")
        self.assertIn("${NANO7KIT_INTEL_PREFIX:-/nonexistent}/lib/libzstd.1.dylib", text)
        self.assertIn("build/macos-intel/lib/libzstd.1.dylib", text)

    def test_go_env_drops_a_foreign_goroot(self):
        from host.usb import go_env

        with tempfile.TemporaryDirectory() as td:
            mine = Path(td) / "mine" / "bin"
            other = Path(td) / "other" / "bin"
            for d in (mine, other):
                d.mkdir(parents=True)
                (d / "go").write_text("#!/bin/sh\n")
                (d / "go").chmod(0o755)
            env = {"PATH": str(mine), "GOROOT": str(other.parent)}
            self.assertNotIn("GOROOT", go_env(env))
            env = {"PATH": str(mine), "GOROOT": str(mine.parent)}
            self.assertEqual(go_env(env)["GOROOT"], str(mine.parent))
        src = (ROOT / "host" / "usb.py").read_text(encoding="utf-8")
        self.assertEqual(src.count("env = go_env(libusb_cgo_env(env))"), 2)

    def test_failed_go_builds_fail_deps(self):
        from host import deps

        def boom():
            raise RuntimeError("go build failed")

        out = io.StringIO()
        with mock.patch("host.usb.build_wind3x", boom), mock.patch("host.usb.build_usbscsi", boom), \
                mock.patch("host.usb.build_ipod_sg_raw", lambda: None), \
                mock.patch.object(deps, "have", lambda tool: True), \
                mock.patch.object(deps, "is_macos", lambda: False), \
                mock.patch.object(deps, "is_linux", lambda: False), \
                mock.patch.object(deps, "rustup_nightly", lambda: None), \
                mock.patch.object(deps, "ensure_lvgl", lambda: True), \
                mock.patch.object(deps, "ensure_fontawesome", lambda: True), \
                mock.patch.object(deps, "_pip", lambda pkg: True), \
                mock.patch("sys.stdout", out):
            missing = deps.ensure_deps()
        self.assertGreaterEqual(missing, 2)
        self.assertIn("wInd3x host build", out.getvalue())
        self.assertIn("FAILED", out.getvalue())
        self.assertNotIn("deps: build tools present", out.getvalue())


if __name__ == "__main__":
    unittest.main()
