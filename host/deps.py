"""Install build + device tools on macOS (Homebrew) and Linux (apt/dnf/...)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .compat import have, host_arch, is_linux, is_macos, is_root, run, which
from .const import FA_VERSION, LVGL_PIN, LVGL_REPO
from .state import repo_root

RUST_NIGHTLY = "nightly-2023-12-20"
RUST_THUMBV6M = "thumbv6m-none-eabi"


def intel_macos_without_brew() -> bool:
    return is_macos() and host_arch() == "amd64" and not have("brew")


def print_intel_no_brew_help() -> None:
    print("    Homebrew dropped Intel Macs. It is not a supported install path here.")
    print("    Use the user-space bootstrap (no sudo, no shell rc edits):")
    print("      ./scripts/macos-intel-bootstrap.sh")
    print("      source build/macos-intel/env.sh")
    print("    Then re-run ./start deps. See docs/MACOS.md.")


def _brew_install(pkg: str) -> bool:
    brew = which("brew")
    if not brew:
        if intel_macos_without_brew():
            print_intel_no_brew_help()
        else:
            print(
                "    Homebrew is not installed. Install it from https://brew.sh "
                "then re-run ./start deps."
            )
        return False
    return run([brew, "install", *pkg.split()], check=False).returncode == 0


def _linux_install(pkg: str) -> bool:
    sudo = [] if is_root() else (["sudo"] if have("sudo") else None)
    if sudo is None:
        print("    no sudo; install packages manually")
        return False
    if have("apt-get"):
        return run(sudo + ["apt-get", "install", "-y", *pkg.split()], check=False).returncode == 0
    if have("dnf"):
        return run(sudo + ["dnf", "install", "-y", *pkg.split()], check=False).returncode == 0
    if have("pacman"):
        return run(sudo + ["pacman", "-S", "--noconfirm", *pkg.split()], check=False).returncode == 0
    print("    no known package manager")
    return False


def venv_dir() -> Path:
    return repo_root() / "build" / "venv"


def _ensure_venv() -> Path:
    """build/venv, never ~/.local via pip --user."""
    venv = venv_dir()
    py = venv / "bin" / "python"
    if not py.is_file():
        venv.parent.mkdir(parents=True, exist_ok=True)
        run([sys.executable, "-m", "venv", str(venv)], check=True)
    return venv


def _venv_importable(mod: str) -> bool:
    venv_py = repo_root() / "build" / "venv" / "bin" / "python"
    if venv_py.is_file():
        r = run([str(venv_py), "-c", f"import {mod}"], check=False, capture=True)
        if r.returncode == 0:
            return True
    return False


def _pip(pkg: str) -> bool:
    venv = _ensure_venv()
    pip = venv / "bin" / "pip"
    cmd = [str(pip), "install", pkg]
    r = run(cmd, check=False, capture=True)
    sys.stdout.write(r.stdout or "")
    sys.stderr.write(r.stderr or "")
    return r.returncode == 0


def ensure_lvgl() -> bool:
    lvgl = repo_root() / "nanoapps" / "lvgl"
    if (lvgl / ".git").exists():
        cur = run(["git", "-C", str(lvgl), "rev-parse", "HEAD"], check=False, capture=True)
        head = (cur.stdout or "").strip()
        if head.startswith(LVGL_PIN[:12]):
            print(f"  lvgl/                  ok ({LVGL_PIN[:12]})")
            return True
        run(["git", "-C", str(lvgl), "fetch", "--depth", "1", "origin", LVGL_PIN], check=False)
        run(["git", "-C", str(lvgl), "checkout", "-f", LVGL_PIN], check=False)
        return True
    print("  lvgl/                  fetching pinned upstream")
    lvgl.mkdir(parents=True, exist_ok=True)
    run(["git", "init", "-q", str(lvgl)], check=False)
    run(["git", "-C", str(lvgl), "remote", "add", "origin", LVGL_REPO], check=False)
    r = run(["git", "-C", str(lvgl), "fetch", "--depth", "1", "origin", LVGL_PIN], check=False)
    run(["git", "-C", str(lvgl), "checkout", "-f", LVGL_PIN], check=False)
    return (lvgl / ".git").exists() and r.returncode == 0


def ensure_fontawesome() -> bool:
    fa = repo_root() / "nanoapps" / "tools" / "dependencies" / "fontawesome"
    otf = fa / "otfs" / f"Font Awesome {FA_VERSION[0]} Free-Solid-900.otf"
    meta = fa / "metadata" / "icons.json"
    bundled = repo_root() / "untether" / "third_party" / "fontawesome"
    if otf.is_file() and meta.is_file():
        print(f"  fontawesome/           ok ({FA_VERSION})")
        return True
    if (bundled / "metadata" / "icons.json").is_file():
        print("  fontawesome/           copying bundled untether copy")
        fa.mkdir(parents=True, exist_ok=True)
        for sub in ("otfs", "metadata"):
            src = bundled / sub
            dst = fa / sub
            if src.is_dir():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
        lic = bundled / "LICENSE.txt"
        if lic.is_file():
            shutil.copy2(lic, fa / "LICENSE.txt")
        return otf.is_file() or (fa / "otfs").exists()
    print("  fontawesome/           MISSING (icons will be skipped until downloaded)")
    return False


def _cargo_bin() -> Path:
    home = os.environ.get("CARGO_HOME")
    if home:
        return Path(home) / "bin"
    return Path.home() / ".cargo" / "bin"


def rustup_nightly() -> None:
    """Always ensure nightly-2023-12-20 + thumbv6m, even if rustup already exists.

    Install with --no-modify-path so shell rc files are left alone (Intel
    bootstrap env.sh puts cargo on PATH).
    """
    cargo_bin = _cargo_bin()
    if cargo_bin.is_dir():
        os.environ["PATH"] = str(cargo_bin) + os.pathsep + os.environ.get("PATH", "")
    if not have("rustup"):
        print("  rustup                 installing (https://rustup.rs, --no-modify-path)")
        init = which("rustup-init")
        if init:
            run(
                [init, "-y", "--no-modify-path", "--default-toolchain", "none"],
                check=False,
            )
        else:
            script = (
                "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | "
                "sh -s -- -y --no-modify-path --default-toolchain none"
            )
            run(["bash", "-lc", script], check=False)
        if cargo_bin.is_dir():
            os.environ["PATH"] = str(cargo_bin) + os.pathsep + os.environ.get("PATH", "")
    if have("rustup"):
        run(
            ["rustup", "toolchain", "install", RUST_NIGHTLY, "--profile", "minimal"],
            check=False,
        )
        run(
            ["rustup", "target", "add", "--toolchain", RUST_NIGHTLY, RUST_THUMBV6M],
            check=False,
        )


def ensure_deps(*, check_only: bool = False, device: bool = True) -> int:
    """Return the number of items still missing."""
    import importlib.util

    missing = 0
    print(f"[this computer] {sys.platform}")

    plan = []

    def need(label, present, brew=None, linux=None, pip=None, extra=None):
        nonlocal missing
        print(f"  {label:<24} {'ok' if present else 'MISSING'}")
        if present:
            return
        missing += 1
        if check_only:
            return
        if extra:
            extra()
            return
        if pip:
            _pip(pip)
            return
        if is_macos() and brew:
            _brew_install(brew)
        elif is_linux() and linux:
            _linux_install(linux)
        else:
            print(f"    install '{label}' manually")

    need("arm-none-eabi-gcc", have("arm-none-eabi-gcc"), brew="arm-none-eabi-gcc",
         linux="gcc-arm-none-eabi")
    if is_macos() and have("arm-none-eabi-gcc"):
        # The darwin-x86_64 Arm toolchain links Homebrew's libzstd; without it every
        # compile dies in dyld. The fix copies a libzstd into the toolchain folder.
        zstd_script = repo_root() / "scripts" / "darwin-arm-zstd.sh"
        mode = "--check" if check_only else "--check-or-fix"
        zstd_ok = subprocess.run(["bash", str(zstd_script), mode]).returncode == 0
        need("arm-none-eabi-gcc runs", zstd_ok)
    need("make", have("make"), brew="make", linux="make")
    need("git", have("git"), brew="git", linux="git")
    need("cc", have("cc") or have("gcc") or have("clang"),
         linux="gcc")
    need("python3", True)
    need(
        "python3 Pillow",
        importlib.util.find_spec("PIL") is not None or _venv_importable("PIL"),
        pip="pillow",
    )
    need(
        "python3 pyelftools",
        importlib.util.find_spec("elftools") is not None or _venv_importable("elftools"),
        pip="pyelftools",
    )
    need("curl", have("curl"), brew="curl", linux="curl")
    cargo_bin = _cargo_bin()
    if cargo_bin.is_dir():
        os.environ["PATH"] = str(cargo_bin) + os.pathsep + os.environ.get("PATH", "")
    rustup_ok = have("cargo") or have("rustup")
    print(f"  {'rustup/cargo':<24} {'ok' if rustup_ok else 'MISSING'}")
    if check_only:
        if not rustup_ok:
            missing += 1
    else:
        # Always pin nightly + thumbv6m even when cargo is already on PATH.
        rustup_nightly()
        if not (have("cargo") or have("rustup")):
            missing += 1
    need("go", have("go"), brew="go", linux="golang-go")
    need("ssh", have("ssh"), brew="openssh", linux="openssh-client")
    from .usb import libusb_pkgconfig_ok

    if is_macos():
        need(
            "libusb",
            libusb_pkgconfig_ok()
            or Path("/opt/homebrew/lib/libusb-1.0.dylib").exists()
            or Path("/usr/local/lib/libusb-1.0.dylib").exists(),
            brew="libusb",
        )
        need("mtools", have("mcopy"), brew="mtools")
        need("pkg-config", have("pkg-config"), brew="pkg-config")
    if is_linux() and device:
        need("mtools", have("mcopy"), linux="mtools")
        need("sg_raw", have("sg_raw"), linux="sg3-utils")
        need("fsck.vfat", have("fsck.vfat") or have("fsck.msdos"), linux="dosfstools")
        need(
            "libusb",
            Path("/usr/include/libusb-1.0/libusb.h").exists() or libusb_pkgconfig_ok(),
            linux="libusb-1.0-0-dev pkg-config",
        )

    print("\n[lvgl]")
    if check_only:
        lvgl_ok = (repo_root() / "nanoapps" / "lvgl" / ".git").exists()
        print(f"  lvgl/                  {'ok' if lvgl_ok else 'MISSING'}")
        if not lvgl_ok:
            missing += 1
    elif not ensure_lvgl():
        missing += 1
    print("[fontawesome]")
    if check_only:
        fa_ok = (
            (repo_root() / "nanoapps" / "tools" / "dependencies" / "fontawesome" / "metadata" / "icons.json").is_file()
            or (repo_root() / "untether" / "third_party" / "fontawesome" / "metadata" / "icons.json").is_file()
        )
        print(f"  fontawesome/           {'ok' if fa_ok else 'MISSING'}")
        if not fa_ok:
            missing += 1
    elif not ensure_fontawesome():
        missing += 1

    if not check_only:
        try:
            from .usb import build_ipod_sg_raw, build_usbscsi, build_wind3x
            if have("go"):
                # The firmware and Disk Mode steps need both, so a failed build fails deps.
                for label, build in (("wInd3x", build_wind3x), ("usbscsi", build_usbscsi)):
                    try:
                        build()
                    except Exception as exc:
                        missing += 1
                        print(f"  {label + ' host build':<24} FAILED ({exc})")
            try:
                build_ipod_sg_raw()
            except Exception as extra:
                print(f"  ipod_sg_raw host build skipped ({extra})")
        except Exception as exc:
            print(f"  usb helpers            skipped ({exc})")

    print()
    if missing:
        print(f"deps: {missing} item(s) still missing. Re-run ./start deps after installing them.")
        if intel_macos_without_brew():
            print_intel_no_brew_help()
    else:
        print("deps: build tools present.")
    return missing
