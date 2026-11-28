"""PebbleOS (Apache-2.0): hosted as one NanoApps app on the nano 7G.

Fetches coredevices/PebbleOS at a pinned commit. Does not vendor the tree.
The NanoApps shim is nanoapps/apps/pebbleos. The real kernel is compiled
for Cortex-A5 VFPv4-D16 and linked into the .hbapp.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .compat import Refused, root


PEBBLEOS_REPO = "https://github.com/coredevices/PebbleOS"
PEBBLEOS_COMMIT = "94e3d98daa0dfd6bd204ca1973f1b502eed07d5f"
PEBBLEOS_LICENSE = "Apache-2.0"
PEBBLEOS_TITLE = "PebbleOS"
BOARD = "nano7_emery"
OVERLAY_MARK = ".nano7-overlay"

# Known SDK / venv locations on this box; PATH is also searched.
SDK_ENV = Path.home() / "pebbleos-sdk-0.1.17" / "env.sh"
VENV_PBL = Path("/workspace/pebbleos/.venv/bin")


def checkout() -> Path:
    return root() / "build" / "pebbleos"


def kernel_build_dir() -> Path:
    return checkout() / "build-nano7"


def data_dir() -> Path:
    return root() / "build" / "pebbleos-data"



def _ensure_submodules(dest: Path) -> None:
    """Populate submodule trees from a local PebbleOS checkout when possible."""
    import shutil

    local = Path("/workspace/pebbleos")
    cfg = dest / ".gitmodules"
    if not cfg.is_file():
        return
    paths = []
    for line in cfg.read_text().splitlines():
        line = line.strip()
        if line.startswith("path = "):
            paths.append(line.split("=", 1)[1].strip())
    missing = []
    for rel in paths:
        src = local / rel
        dst = dest / rel
        populated = dst.is_dir() and any(p.name != ".git" for p in dst.iterdir()) if dst.exists() else False
        if populated:
            # Drop copied .git links; they point at another clone.
            git = dst / ".git"
            if git.is_dir():
                shutil.rmtree(git)
            elif git.is_file():
                git.unlink()
            continue
        if src.is_dir() and any(src.iterdir()):
            if dst.exists():
                shutil.rmtree(dst)
            print(f"+ copy submodule {rel}")
            shutil.copytree(src, dst, symlinks=True, ignore=shutil.ignore_patterns(".git"))
        else:
            missing.append(rel)
    if missing:
        cmd = ["git", "-C", str(dest), "submodule", "update", "--init", "--"] + missing
        print("+", " ".join(cmd))
        subprocess.check_call(cmd)


def print_status() -> int:
    dest = checkout()
    present = (dest / ".git").is_dir() or (dest / "CMakeLists.txt").is_file()
    lib = kernel_build_dir() / "libpebbleos.a"
    print("PebbleOS hosted on this MKN52 (Helvetica NanoApps)")
    print("------------------------------------------------")
    print("Real PebbleOS, fetched at a pinned commit, hosted as one app.")
    print("Not a recreation.")
    print()
    print("  repo       " + PEBBLEOS_REPO)
    print("  licence    " + PEBBLEOS_LICENSE + " (credited, not vendored)")
    print("  pinned     " + PEBBLEOS_COMMIT)
    print("  checkout   " + str(dest) + (" (present)" if present else " (fetched by ./start pebbleos fetch)"))
    print("  kernel     " + str(lib) + (" (present)" if lib.is_file() else " (built by ./start pebbleos build)"))
    print("  app        nanoapps/apps/pebbleos")
    print("  screen     240x432, 200x228 Pebble panel centred")
    print("  buttons    Home=Back, Play/Pause=Select, Vol Up/Down=Up/Down")
    print("  memory     must run at 3.9 MB/2 MB and at 6 MB/4 MB;")
    print("             otherwise a NOT ENOUGH MEMORY screen")
    print("  flash      file-backed under /Apps/Data/PebbleOS")
    print()
    print("exact: ./start pebbleos fetch")
    print("       ./start pebbleos build")
    print("       ./start nabase add 'PebbleOS'")
    return 0


def _apply_overlay(dest: Path) -> None:
    script = root() / "nanoapps" / "apps" / "pebbleos" / "apply_overlay.py"
    subprocess.check_call([sys.executable, str(script), str(dest)])
    (dest / OVERLAY_MARK).write_text(PEBBLEOS_COMMIT + "\n")


def fetch(*, force: bool = False) -> Path:
    dest = checkout()
    dest.parent.mkdir(parents=True, exist_ok=True)
    have_pin = False
    if dest.is_dir() and not force:
        head = subprocess.run(
            ["git", "-C", str(dest), "rev-parse", "HEAD"],
            capture_output=True, text=True,
        )
        have_pin = head.returncode == 0 and head.stdout.strip() == PEBBLEOS_COMMIT
    if dest.is_dir() and not have_pin:
        subprocess.check_call(["rm", "-rf", str(dest)])
        have_pin = False
    if not dest.is_dir():
        dest.parent.mkdir(parents=True, exist_ok=True)
        local = Path("/workspace/pebbleos")
        have_local = False
        if (local / ".git").is_dir():
            probe = subprocess.run(
                ["git", "-C", str(local), "cat-file", "-e", PEBBLEOS_COMMIT + "^{commit}"],
                capture_output=True,
            )
            have_local = probe.returncode == 0
        if have_local:
            if dest.exists():
                subprocess.check_call(["rm", "-rf", str(dest)])
            subprocess.check_call(["git", "clone", "-q", str(local), str(dest)])
            subprocess.check_call(
                ["git", "-c", "advice.detachedHead=false", "-C", str(dest),
                 "checkout", "-q", "--detach", PEBBLEOS_COMMIT],
            )
        else:
            dest.mkdir(parents=True, exist_ok=True)
            subprocess.check_call(["git", "init", "-q", str(dest)])
            subprocess.check_call(
                ["git", "fetch", "-q", "--depth", "1", PEBBLEOS_REPO, PEBBLEOS_COMMIT],
                cwd=dest,
            )
            subprocess.check_call(
                ["git", "-c", "advice.detachedHead=false", "checkout", "-q", "--detach", "FETCH_HEAD"],
                cwd=dest,
            )
        have_pin = True
    head = subprocess.check_output(["git", "-C", str(dest), "rev-parse", "HEAD"], text=True).strip()
    if head != PEBBLEOS_COMMIT:
        raise Refused(f"PebbleOS: got commit {head}, expected {PEBBLEOS_COMMIT}")
    mark = dest / OVERLAY_MARK
    if force or not mark.is_file() or mark.read_text().strip() != PEBBLEOS_COMMIT:
        # Return the tree to the pin, then apply the overlay once.
        subprocess.check_call(["git", "-C", str(dest), "reset", "-q", "--hard", "HEAD"])
        subprocess.check_call(["git", "-C", str(dest), "clean", "-qfd", "-e", "build-nano7"])
        _apply_overlay(dest)
        print(f"Fetched PebbleOS {PEBBLEOS_COMMIT} and applied the nano7 overlay")
    else:
        print(f"PebbleOS already at {PEBBLEOS_COMMIT} with the nano7 overlay")
    _ensure_submodules(dest)
    return dest


def _pbl_env() -> dict[str, str]:
    env = os.environ.copy()
    paths = []
    if VENV_PBL.is_dir():
        paths.append(str(VENV_PBL))
    arm = Path.home() / "pebbleos-sdk-0.1.17" / "arm-none-eabi" / "bin"
    if arm.is_dir():
        paths.append(str(arm))
    env["PATH"] = os.pathsep.join(paths + [env.get("PATH", "")])
    if SDK_ENV.is_file():
        # env.sh prepends the SDK; we already added its bin. Keep PEBBLEOS_SDK.
        env.setdefault("PEBBLEOS_SDK", str(SDK_ENV.parent))
    return env


def _run(argv: list[str], cwd: Path) -> None:
    print("+", " ".join(argv))
    subprocess.check_call(argv, cwd=cwd, env=_pbl_env())


def stage_resources(pbpack: Path, dest: Path) -> None:
    """Write 64 KiB flash banks for the resource partition at offset 0."""
    dest.mkdir(parents=True, exist_ok=True)
    data = pbpack.read_bytes()
    bank = 0x10000
    # Only the used prefix, rounded up to a bank.
    n = (len(data) + bank - 1) // bank
    for i in range(n):
        chunk = data[i * bank:(i + 1) * bank]
        if len(chunk) < bank:
            chunk = chunk + b"\xff" * (bank - len(chunk))
        (dest / f"{i:02x}.bin").write_bytes(chunk)
    # Keep a copy of the pbpack next to the banks.
    shutil.copy2(pbpack, dest.parent / "system_resources.pbpack")
    print(f"Staged {n} resource flash banks in {dest}")


def configure_kernel() -> Path:
    dest = fetch()
    bdir = kernel_build_dir()
    _run(["pbl", "configure", "-b", str(bdir), "--board", BOARD, "--variant", "normal"], cwd=dest)
    return bdir


def build_kernel() -> Path:
    bdir = configure_kernel()
    _run(["ninja", "-C", str(bdir)], cwd=checkout())
    lib = bdir / "libpebbleos.a"
    if not lib.is_file():
        raise Refused(f"kernel link did not produce {lib}")
    # pbpack path used by the PebbleOS build; search common names.
    pbpack = None
    for cand in bdir.rglob("system_resources.pbpack"):
        pbpack = cand
        break
    if pbpack is not None:
        stage_resources(pbpack, data_dir() / "flash")
    return lib


def build() -> dict:
    from .sdkapp import build_app

    build_kernel()
    result = build_app("pebbleos")
    print("On a NanoApps-base iPod (Disk Mode):")
    print("  ./start nabase add 'PebbleOS'")
    print("  also copy build/pebbleos-data/ into /Apps/Data/PebbleOS/")
    print("Do not use ./start apps add on that base.")
    return result
