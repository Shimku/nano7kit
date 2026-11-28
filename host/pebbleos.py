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

# Optional PebbleOS SDK toolchain in the home folder; PATH is also searched.
SDK_ENV = Path.home() / "pebbleos-sdk-0.1.17" / "env.sh"

# Python packages the kernel build needs (pbl itself comes from the
# fetched tree). Installed into build/pbl-venv by `./start pebbleos deps`.
PBL_PACKAGES = (
    "pillow", "freetype-py", "pypng", "svg.path", "bitarray", "polib",
    "sh<2", "packaging", "PyYAML", "kconfiglib", "intelhex>=2.1,<3",
    "pyelftools", "nanopb", "ninja", "cmake", "unicorn",
)


def venv_dir() -> Path:
    return root() / "build" / "pbl-venv"


def checkout() -> Path:
    return root() / "build" / "pebbleos"


def kernel_build_dir() -> Path:
    return checkout() / "build-nano7"


def data_dir() -> Path:
    """Staged /Apps/Data/PebbleOS tree. `./start nabase add PebbleOS` copies it."""
    from .const import artifact

    return artifact("artifacts/extra/Data/PebbleOS")


def kernel_lib() -> Path:
    return kernel_build_dir() / "libpebbleos.a"


def overlay_fingerprint() -> str:
    """Pinned commit plus a hash of the overlay, so overlay edits re-apply."""
    import hashlib

    app = root() / "nanoapps" / "apps" / "pebbleos"
    h = hashlib.sha256()
    files = [app / "apply_overlay.py"] + sorted(p for p in (app / "overlay").rglob("*") if p.is_file())
    for f in files:
        h.update(f.relative_to(app).as_posix().encode() + b"\0")
        h.update(f.read_bytes())
    return f"{PEBBLEOS_COMMIT} {h.hexdigest()[:16]}"



def _local_mirror() -> Path | None:
    """An existing PebbleOS clone to copy from instead of the network.

    Set PEBBLEOS_MIRROR to its folder. It is only used when it holds the
    pinned commit."""
    env = os.environ.get("PEBBLEOS_MIRROR")
    if not env:
        return None
    local = Path(env).expanduser()
    if not (local / ".git").exists():
        return None
    probe = subprocess.run(
        ["git", "-C", str(local), "cat-file", "-e", PEBBLEOS_COMMIT + "^{commit}"],
        capture_output=True,
    )
    return local if probe.returncode == 0 else None


def _ensure_submodules(dest: Path) -> None:
    """Populate the submodules: from PEBBLEOS_MIRROR if set, else with git."""
    cfg = dest / ".gitmodules"
    if not cfg.is_file():
        return
    local = _local_mirror()
    paths = []
    for line in cfg.read_text().splitlines():
        line = line.strip()
        if line.startswith("path = "):
            paths.append(line.split("=", 1)[1].strip())
    missing = []
    for rel in paths:
        dst = dest / rel
        populated = dst.is_dir() and any(p.name != ".git" for p in dst.iterdir())
        if populated:
            continue
        src = local / rel if local is not None else None
        if src is not None and src.is_dir() and any(src.iterdir()):
            if dst.exists():
                shutil.rmtree(dst)
            print(f"+ copy submodule {rel}")
            shutil.copytree(src, dst, symlinks=True, ignore=shutil.ignore_patterns(".git"))
        else:
            missing.append(rel)
    if missing:
        cmd = ["git", "-C", str(dest), "submodule", "update", "--init", "--depth", "1", "--"] + missing
        print("+", " ".join(cmd))
        subprocess.check_call(cmd)


def print_status() -> int:
    dest = checkout()
    present = (dest / ".git").is_dir() or (dest / "CMakeLists.txt").is_file()
    lib = kernel_lib()
    res = data_dir() / "flash" / "00.bin"
    print("PebbleOS hosted on this MKN52 (Helvetica NanoApps)")
    print("------------------------------------------------")
    print("Real PebbleOS, fetched at a pinned commit, hosted as one app.")
    print("Not a recreation.")
    print()
    print("  repo       " + PEBBLEOS_REPO)
    print("  licence    " + PEBBLEOS_LICENSE + " (credited, not vendored)")
    print("  pinned     " + PEBBLEOS_COMMIT)
    print("  checkout   " + str(dest) + (" (present)" if present else " (fetched by ./start pebbleos fetch)"))
    print("  kernel     " + str(lib) + (" (present)" if lib.is_file() else " (built by ./start sdk build pebbleos)"))
    print("  resources  " + str(res.parent) + (" (staged)" if res.is_file() else " (staged by the build)"))
    print("  app        nanoapps/apps/pebbleos")
    print("  screen     240x432, 200x228 Pebble panel centred")
    print("  buttons    Home=Back, Play/Pause=Select, Vol Up/Down=Up/Down")
    print("  memory     must run at 3.9 MB/2 MB and at 6 MB/4 MB;")
    print("             otherwise a NOT ENOUGH MEMORY screen")
    print("  flash      file-backed under /Apps/Data/PebbleOS")
    print()
    print("exact: ./start pebbleos fetch")
    print("       ./start sdk build pebbleos")
    print("       ./start nabase add 'PebbleOS'")
    return 0


def _apply_overlay(dest: Path) -> None:
    script = root() / "nanoapps" / "apps" / "pebbleos" / "apply_overlay.py"
    subprocess.check_call([sys.executable, str(script), str(dest)])
    (dest / OVERLAY_MARK).write_text(overlay_fingerprint() + "\n")


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
        local = _local_mirror()
        have_local = local is not None
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
    if force or not mark.is_file() or mark.read_text().strip() != overlay_fingerprint():
        # Return the tree to the pin, then apply the overlay once.
        subprocess.check_call(["git", "-C", str(dest), "reset", "-q", "--hard", "HEAD"])
        subprocess.check_call(["git", "-C", str(dest), "clean", "-qfd", "-e", "build-nano7"])
        _apply_overlay(dest)
        print(f"Fetched PebbleOS {PEBBLEOS_COMMIT} and applied the nano7 overlay")
    else:
        print(f"PebbleOS already at {PEBBLEOS_COMMIT} with the nano7 overlay")
    _ensure_submodules(dest)
    return dest


def deps() -> Path:
    """Create build/pbl-venv with pbl, ninja and cmake for the kernel build."""
    dest = fetch()
    venv = venv_dir()
    py = venv / "bin" / "python3"
    if not py.is_file():
        print(f"+ python3 -m venv {venv}")
        subprocess.check_call([sys.executable, "-m", "venv", str(venv)])
    pip = [str(py), "-m", "pip", "install", "-q"]
    print("+ pip install " + " ".join(PBL_PACKAGES))
    subprocess.check_call(pip + list(PBL_PACKAGES))
    print("+ pip install tools/libs/pbl-cli")
    subprocess.check_call(pip + [str(dest / "tools" / "libs" / "pbl-cli")])
    return venv


def _pbl_env() -> dict[str, str]:
    env = os.environ.copy()
    paths = []
    vbin = venv_dir() / "bin"
    if vbin.is_dir():
        paths.append(str(vbin))
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
    """Write 64 KiB flash banks for the resource partition at offset 0.

    Only the resource banks are staged. The file system banks (2 MiB and
    up) are the user's watch state and are never written from here."""
    if dest.is_dir():
        shutil.rmtree(dest)
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
    # Keep a copy of the pbpack on this computer, not on the iPod.
    shutil.copy2(pbpack, kernel_build_dir() / "nano7-system_resources.pbpack")
    stale = dest.parent / "system_resources.pbpack"
    if stale.is_file():
        stale.unlink()
    print(f"Staged {n} resource flash banks in {dest}")


def configure_kernel() -> Path:
    dest = fetch()
    if not (venv_dir() / "bin" / "pbl").is_file() and not shutil.which("pbl"):
        deps()
    env = _pbl_env()
    for tool in ("pbl", "ninja", "cmake", "arm-none-eabi-gcc"):
        if not shutil.which(tool, path=env["PATH"]):
            raise Refused(f"PebbleOS kernel build needs {tool} (./start pebbleos deps, ./start deps)")
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


def ensure_kernel() -> Path:
    """Fetch and build the kernel when it is missing or the overlay changed.

    Called by `./start sdk build pebbleos`, so that build never ships the
    shim alone."""
    lib = kernel_lib()
    mark = checkout() / OVERLAY_MARK
    fresh = lib.is_file() and mark.is_file() and mark.read_text().strip() == overlay_fingerprint()
    if not fresh or not (data_dir() / "flash" / "00.bin").is_file():
        return build_kernel()
    return lib


KERNEL_MARKERS = (b"pbl_nano_kernel_start", b"pbl_posix_start")


def check_hbapp(hbapp: Path) -> None:
    """Refuse an .hbapp that does not carry the real kernel."""
    elf = hbapp.with_name(hbapp.stem + ".reloc.elf")
    blob = elf.read_bytes() if elf.is_file() else b""
    missing = [m.decode() for m in KERNEL_MARKERS if m not in blob]
    if missing:
        raise Refused(f"{hbapp} does not contain the PebbleOS kernel ({', '.join(missing)} missing)")
    if hbapp.stat().st_size < 400 * 1024:
        raise Refused(f"{hbapp} is too small to hold the PebbleOS kernel")


def build() -> dict:
    from .sdkapp import build_app

    result = build_app("pebbleos")
    return result
