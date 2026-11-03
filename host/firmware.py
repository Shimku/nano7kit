"""Build the hardware-tested untethered firmware. Does not write any device."""

from __future__ import annotations

import os
import shutil
import stat
import zipfile
from pathlib import Path

from . import const
from .compat import Refused, force_utc_timezone, have, path_prepend, run, sha256_file
from .safety import check_mse
from .state import repo_root

# Hash mismatch is usually TZ, not missing rustup/gcc/afdko. Say that first.
FIRMWARE_BUILD_FAILED = (
    "firmware build failed. If the log shows a hash mismatch (for example "
    "rescue 041d4f61... instead of 39579dba...), the image is timezone-"
    "dependent: pyfatfs round-trips FAT DOS times through local time. "
    "This toolkit forces TZ=UTC; export TZ=UTC if you run the bash script "
    "yourself. Missing tools (rustup nightly-2023-12-20, arm-none-eabi-gcc, "
    "the Python venv / afdko) are a separate cause. See the log above."
)


def build_dir() -> Path:
    d = repo_root() / "build"
    d.mkdir(exist_ok=True)
    return d


def install_sha_shims() -> Path:
    """Put GNU-named shims on PATH so upstream bash scripts run on macOS."""
    shim = build_dir() / "shims"
    shim.mkdir(exist_ok=True)
    sha = shim / "sha256sum"
    if not sha.exists():
        sha.write_text(
            "#!/usr/bin/env python3\n"
            "import hashlib, sys\n"
            "paths = [a for a in sys.argv[1:] if not a.startswith('-')]\n"
            "check = '-c' in sys.argv or '--check' in sys.argv\n"
            "if check:\n"
            "    src = sys.stdin if not paths else open(paths[0])\n"
            "    ok = True\n"
            "    for line in src:\n"
            "        line=line.strip()\n"
            "        if not line or line.startswith('#'): continue\n"
            "        digest, name = line.split(None, 1)\n"
            "        name = name[1:].lstrip() if name.startswith('*') else name\n"
            "        h=hashlib.sha256(open(name,'rb').read()).hexdigest()\n"
            "        if h != digest:\n"
            "            sys.stderr.write(name + ': FAILED\\n'); ok=False\n"
            "    sys.exit(0 if ok else 1)\n"
            "for p in paths or ['-']:\n"
            "    data = sys.stdin.buffer.read() if p=='-' else open(p,'rb').read()\n"
            "    print(hashlib.sha256(data).hexdigest() + '  ' + (p if p!='-' else '-'))\n"
        )
        sha.chmod(sha.stat().st_mode | stat.S_IXUSR)
    md5 = shim / "md5sum"
    if not md5.exists():
        md5.write_text(
            "#!/usr/bin/env python3\n"
            "import hashlib, sys\n"
            "for p in sys.argv[1:]:\n"
            "    if p.startswith('-'): continue\n"
            "    print(hashlib.md5(open(p,'rb').read()).hexdigest() + '  ' + p)\n"
        )
        md5.chmod(md5.stat().st_mode | stat.S_IXUSR)
    sha1 = shim / "sha1sum"
    if not sha1.exists():
        sha1.write_text(
            "#!/usr/bin/env python3\n"
            "import hashlib, sys\n"
            "for p in sys.argv[1:]:\n"
            "    if p.startswith('-'): continue\n"
            "    print(hashlib.sha1(open(p,'rb').read()).hexdigest() + '  ' + p)\n"
        )
        sha1.chmod(sha1.stat().st_mode | stat.S_IXUSR)
    n = shim / "nproc"
    if not n.exists():
        n.write_text("#!/bin/sh\nexec python3 -c 'import os; print(os.cpu_count() or 4)'\n")
        n.chmod(n.stat().st_mode | stat.S_IXUSR)
    path_prepend(shim)
    return shim


def ensure_stock_ipsw(path: Path | None = None) -> Path:
    dest = path or (build_dir() / const.STOCK_IPSW_NAME)
    if dest.is_file():
        actual = sha256_file(dest)
        if actual != const.STOCK_IPSW_SHA256:
            raise Refused(
                f"stock IPSW hash mismatch\n  expected {const.STOCK_IPSW_SHA256}\n  actual   {actual}"
            )
        return dest
    if not have("curl"):
        raise Refused("curl is required to download Apple's stock firmware")
    print("==> downloading Apple's stock 1.1.2 firmware (121 MB) from Apple")
    print("    (this IPSW is the 2015 Rev A / 39A10023 image; see docs/HARDWARE-2015.md)")
    part = dest.with_suffix(".ipsw.part")
    r = run(["curl", "-fL", "--retry", "3", "-o", str(part), const.STOCK_IPSW_URL], check=False)
    if r.returncode != 0:
        raise Refused("download failed. Pass an existing IPSW: ./start build-firmware /path/to/iPod_1.1.2_39A10023.ipsw")
    part.rename(dest)
    actual = sha256_file(dest)
    if actual != const.STOCK_IPSW_SHA256:
        dest.unlink(missing_ok=True)
        raise Refused(f"downloaded IPSW hash mismatch (got {actual})")
    return dest


def setup_venv() -> Path:
    venv = build_dir() / "venv"
    py = venv / "bin" / "python"
    if not py.is_file():
        run([sys_executable(), "-m", "venv", str(venv)], check=True)
    # Import check
    r = run(
        [str(py), "-c", "import pyfatfs.PyFatFS, fontTools, PIL"],
        check=False,
        capture=True,
    )
    if r.returncode != 0:
        print("==> installing Python packages into build/venv")
        pip = str(venv / "bin" / "pip")
        run([pip, "install", "--upgrade", "pip"], check=False)
        r = run(
            [
                pip, "install",
                "setuptools<81",
                "Pillow==12.1.1",
                "fonttools==4.66.0",
                "pyfatfs==1.1.0",
                "afdko==4.0.2",
                "pyelftools",
            ],
            check=False,
        )
        if r.returncode != 0:
            raise Refused("venv package install failed (afdko in particular needs a working compiler)")
    return venv


def sys_executable() -> str:
    import sys
    return sys.executable


def prepare_ipod_sun_toolchain() -> None:
    """No git-tree edits. rust-toolchain.toml lists thumbv6m-none-eabi plus
    aarch64-apple-darwin (for Apple silicon hosts). rustup on Linux can still
    fetch the apple std target; the firmware build itself is thumbv6m only.
    """
    return


def firmware_build_env(base: dict | None = None, *, python: Path | None = None) -> dict:
    """Environment for the Helvetica CFF bash build. Always TZ=UTC.

    The rescue and untethered cargo runs both call helpers/fat_patch.py.
    Theme combine inplace is not timestamp-sensitive; realloc still stamps
    'now' (UTC after force_utc_timezone, but wall-clock).
    """
    force_utc_timezone()
    env = dict(os.environ if base is None else base)
    env["TZ"] = "UTC"
    cargo_home = os.environ.get("CARGO_HOME")
    cargo_bin = Path(cargo_home) / "bin" if cargo_home else Path.home() / ".cargo" / "bin"
    if cargo_bin.is_dir():
        env["PATH"] = str(cargo_bin) + os.pathsep + env.get("PATH", "")
    if python is not None:
        env["PYTHON"] = str(python)
    return env


def build_firmware(ipsw: Path | None = None) -> dict:
    install_sha_shims()
    repo = repo_root()
    stock = ensure_stock_ipsw(Path(ipsw) if ipsw else None)
    print("==> verifying bundled loader files")
    from .safety import verify_release_artifacts

    verify_release_artifacts()
    venv = setup_venv()
    python = venv / "bin" / "python"

    # Extract Apple bootloader next to ipod_sun (from the stock IPSW; not in git).
    boot_dir = repo / "untether" / "ipod_sun_untethered" / "iPod_1.1.2_39A10023_2012"
    boot_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(stock) as z:
        boot_bytes = z.read("N31.bootloader.rb3")
    from .safety import check_bootloader

    boot_path = boot_dir / "N31.bootloader.rb3"
    boot_path.write_bytes(boot_bytes)
    check_bootloader(boot_path)

    prepare_ipod_sun_toolchain()
    env = firmware_build_env(os.environ.copy(), python=python)

    print("==> building firmware (first run compiles the Rust builder)")
    script = repo / "untether" / "scripts" / "build-helvetica-cff-candidate.sh"
    r = run(["bash", str(script), str(stock)], check=False, env=env, cwd=repo / "untether")
    if r.returncode != 0:
        raise Refused(FIRMWARE_BUILD_FAILED)

    src_mse = repo / "untether" / "build" / "helvetica-cff" / "Firmware-helvetica-cff.MSE"
    src_rescue = repo / "untether" / "build" / "helvetica-cff" / "Firmware-known-good-39579dba.MSE"
    out = build_dir() / "firmware"
    out.mkdir(exist_ok=True)
    dest_mse = out / "Firmware-untethered.MSE"
    dest_rescue = out / "Firmware-rescue.MSE"
    shutil.copy2(src_mse, dest_mse)
    shutil.copy2(src_rescue, dest_rescue)
    check_mse(dest_mse)
    check_mse(dest_rescue, rescue=True)
    print()
    print("Firmware built and verified against the hardware-tested image:")
    print(f"  {dest_mse}")
    print(f"  {dest_rescue}   (earlier tested build, kept as a fallback)")
    return {"mse": str(dest_mse), "rescue": str(dest_rescue), "sha256": const.FIRMWARE_MSE_SHA256}


def default_mse() -> Path:
    return build_dir() / "firmware" / "Firmware-untethered.MSE"
