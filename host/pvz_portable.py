"""PvZ Portable for the nano: the PvZ-Portable engine (wszqkzqk, LGPL-3.0-or-later) with your own PvZ GOTY files.

games/pvz_portable holds nano7kit's device layer for the engine (engine/pvzp-nano7.patch, engine/nano7,
engine/nano7dev, LGPL-3.0-or-later like the engine), the repacker (repack/) and the nano app (nano/,
LGPL-3.0-or-later). The engine itself is fetched at build time at a pinned commit, with zlib 1.3.1, the SDL 2.30.9
headers and dlmalloc 2.8.6 (all checked by sha256). Nothing from the game is in git: the repacker reads your own
main.pak and properties/ folder and writes to gitignored build/pvz_portable and artifacts/extra only.

Not the iOS 1.0 port (`apps add PvZ`, research only): this is a different engine and its own app name.
Host only. apps add copies the staged files in Disk Mode afterwards (slot B).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tarfile
import time
import urllib.request
from pathlib import Path
from typing import List, Optional

from .compat import Refused, root, which

NAME = "PvZ Portable"
CMD = "./start apps add 'PvZ Portable' --payload /path/to/pvz-goty"
FAT_EXE = f"/Apps/Executables/{NAME}.hbapp"
FAT_DATA = "/Apps/Data/PvZ"
RECIPE = 4  # d/XX/HHHHHHHH data layout (one OS call a file), no sounds, fullscreen, crash-safe crumbs
# your game folder: main.pak of Plants vs. Zombies Game of the Year (the PC release PvZ-Portable targets)
PAK_SHA256 = "0cbdba7ecd6106cca3190976061922ef61011c1896205b10cbc204aaa9d88cba"

ENGINE_URL = "https://github.com/wszqkzqk/PvZ-Portable.git"
ENGINE_COMMIT = "98b659380014c89c3553fbb2839f5e5a84d6df3d"
FETCH = {
    "zlib": ("https://zlib.net/fossils/zlib-1.3.1.tar.gz",
             "9a93b2b7dfdac77ceba5a558a580e74667dd6fede4585b91eefb60f03b72df23"),
    "sdl": ("https://github.com/libsdl-org/SDL/releases/download/release-2.30.9/SDL2-2.30.9.tar.gz",
            "24b574f71c87a763f50704bbb630cbe38298d544a1f890f099a4696b1d6beba4"),
    "dlmalloc": ("https://gee.cs.oswego.edu/pub/misc/malloc-2.8.6.c",
                 "103602c3fcbe200d5e257cdd7353d84bcc033d887bea3b245321319bf5401f47"),
}

# ---------------------------------------------------------------- memory model
# MEASURED on the box: the ARM build's image and the x86 dry run of the device code (same allocator, same
# textures, same triangle counting) through 1-1..1-10. ESTIMATED: what the OS heap and the GL driver do.
IMAGE_BYTES = 1_909_076 + 16            # span of pvz.hbapp (the loader takes max(span, file) in one block)
BLOCK_MAX = 0x340000                    # nano/pvz_nano.c
BLOCK_MIN = 0x300000
AFTER_MIN = 0x150000
SLOP = 0x10000
ARENA_NEED = {"1-1": 3072 * 1024, "1-1..1-10": 3200 * 1024}      # smallest arena that completes (dry run)
TEXTURES = {"1-1": 1_014_272, "1-4": 1_701_040}                   # GL texture peak, POT (dry run, still bakes)
TRIS_NEEDED = {"1-1": 262, "1-10": 910}                            # triangles of the busiest frame (dry run, baked)
BYTES_PER_TRIANGLE = 3000               # as Temple Run (hardware measured there, not for PvZ)
TRI_MARGIN = 0x40000
BUDGET_MIN, BUDGET_MAX = 150, 600     # n7main.cpp N7_TRI_MIN, N7_TRI_MAX (514 drawn on the device)
DATA_FILES, DATA_MB = 3221, 31.9


def build_dir() -> Path:
    return root() / "build" / "pvz_portable"


def stamp_path() -> Path:
    return build_dir() / "stamp.txt"


def game_dir() -> Path:
    return root() / "games" / "pvz_portable"


# ---------------------------------------------------------------- your game files


def inspect_payload(payload: Path) -> dict:
    src = Path(payload).expanduser().resolve()
    problems = []
    pak = src / "main.pak"
    props = src / "properties"
    digest = ""
    if not src.is_dir():
        problems.append(f"{src} is not a folder; pass the folder that holds main.pak and properties/.")
    else:
        if not pak.is_file():
            problems.append(f"{src} has no main.pak.")
        if not props.is_dir():
            problems.append(f"{src} has no properties/ folder.")
    if pak.is_file():
        h = hashlib.sha256()
        with pak.open("rb") as fh:
            for b in iter(lambda: fh.read(1 << 20), b""):
                h.update(b)
        digest = h.hexdigest()
        if digest != PAK_SHA256:
            problems.append(f"main.pak sha256 {digest[:16]}... is not the PvZ GOTY main.pak this port was "
                            f"measured with ({PAK_SHA256[:8]}...{PAK_SHA256[-8:]}).")
    return {"dir": src, "pak": pak, "properties": props, "sha256": digest, "ok": not problems,
            "problems": problems}


def assert_payload(payload: Path) -> dict:
    facts = inspect_payload(payload)
    if not facts["ok"]:
        raise Refused(" ".join(facts["problems"]) + " Your files stay local; nothing was built.")
    return facts


# ---------------------------------------------------------------- fetch


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def fetch(key: str, dest_dir: Path) -> Path:
    url, want = FETCH[key]
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / url.rsplit("/", 1)[1]
    if not (out.is_file() and _sha256(out) == want):
        print(f"==> fetch {url}")
        tmp = out.with_suffix(out.suffix + ".part")
        with urllib.request.urlopen(url, timeout=120) as r, tmp.open("wb") as fh:
            shutil.copyfileobj(r, fh)
        got = _sha256(tmp)
        if got != want:
            tmp.unlink()
            raise Refused(f"{url} sha256 {got[:16]}... is not the pinned {want[:16]}...; nothing was built.")
        tmp.replace(out)
    return out


def fetch_engine(dest: Path) -> Path:
    """PvZ-Portable at the pinned commit, clean, with nano7kit's patch and device layer on top."""
    from .ports import _run_make

    if not which("git"):
        raise Refused("git is missing (xcode-select --install)")
    log = build_dir() / "engine-fetch.log"
    if not (dest / ".git").is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        _run_make(["git", "init", "-q"], dest, log)
        _run_make(["git", "remote", "add", "origin", ENGINE_URL], dest, log)
    have = subprocess.run(["git", "cat-file", "-e", ENGINE_COMMIT + "^{commit}"], cwd=str(dest),
                          capture_output=True).returncode == 0
    if not have:
        print(f"==> fetch PvZ-Portable {ENGINE_COMMIT[:7]} ({ENGINE_URL})")
        _run_make(["git", "fetch", "-q", "--depth", "1", "origin", ENGINE_COMMIT], dest, log)
    _run_make(["git", "checkout", "-q", "-f", ENGINE_COMMIT], dest, log)
    _run_make(["git", "clean", "-qfdx"], dest, log)
    _run_make(["patch", "-s", "-p1", "-i", str(game_dir() / "engine" / "pvzp-nano7.patch")], dest, log)
    for sub in ("nano7", "nano7dev"):
        shutil.copytree(game_dir() / "engine" / sub, dest / sub)
    shutil.copyfile(game_dir() / "engine" / "build_arm.py", dest / "build_arm.py")
    third = dest / "nano7dev" / "third_party"
    third.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fetch("dlmalloc", build_dir() / "dl"), third / "malloc-2.8.6.c")
    return dest


def _untar(tgz: Path, dest: Path, top: str) -> Path:
    out = dest / top
    if not out.is_dir():
        with tarfile.open(tgz) as tf:
            tf.extractall(dest, filter="data") if hasattr(tarfile, "data_filter") else tf.extractall(dest)
    return out


def sdl_headers(src: Path, dest: Path) -> Path:
    """SDL's include/ with the minimal config (headers only; the nano build links no SDL code)."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src / "include", dest)
    shutil.copyfile(dest / "SDL_config_minimal.h", dest / "SDL_config.h")
    return dest


# ---------------------------------------------------------------- build


def _sources_digest() -> str:
    h = hashlib.sha256()
    for p in sorted(game_dir().rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            h.update(p.relative_to(game_dir()).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def stamp_value() -> str:
    from .templerun import _sdk_digest

    return f"recipe={RECIPE} pak={PAK_SHA256[:16]} engine={ENGINE_COMMIT[:12]} src={_sources_digest()} sdk={_sdk_digest()}\n"


def _staged() -> bool:
    from . import const
    from .ports import data_ready

    return const.artifact(f"artifacts/extra/Executables/{NAME}.hbapp").is_file() and data_ready(NAME)


def build(payload: Path, *, force: bool = False) -> dict:
    """Host-only build of PvZ Portable.hbapp + Data/PvZ from your own game folder."""
    from .ports import _run_make, _stage_hbapp, extra_data_dir
    from .templerun import ensure_venv

    t0 = time.time()
    print("nano7kit PvZ Portable build (host only, nothing written to the nano)")
    print("Engine: PvZ-Portable by wszqkzqk (LGPL-3.0-or-later), fetched at a pinned commit.")
    print("Game: your own PvZ GOTY main.pak + properties/ (stay local, never committed).")
    facts = assert_payload(payload)
    print(f"    game      {facts['dir']}")
    print(f"    main.pak  sha256 {facts['sha256'][:16]}... (PvZ GOTY)")
    stamp = stamp_value()
    if not force and _staged() and stamp_path().is_file() and stamp_path().read_text() == stamp:
        print("    build     already staged for these files (stamp match), skipping rebuild")
        return {"name": NAME, "built": False, "data": str(extra_data_dir(NAME))}
    for tool in ("make", "arm-none-eabi-gcc", "cc", "patch"):
        if not which(tool):
            raise Refused(f"{tool} is missing (./start deps; xcode-select --install for cc and patch)")
    py = ensure_venv()                  # pyelftools for mkrelocapp
    out = build_dir()
    out.mkdir(parents=True, exist_ok=True)
    src = out / "src"
    engine = fetch_engine(src / "pvzp")
    zlib = _untar(fetch("zlib", out / "dl"), src, "zlib-1.3.1")
    sdl = sdl_headers(_untar(fetch("sdl", out / "dl"), src, "SDL2-2.30.9"), src / "sdl-include")
    print("==> arm-none-eabi-gcc libpvz_nano.a (engine in ARM and Thumb state)")
    env_args = ["env", "NANO7_TC=", f"NANO7KIT={root()}", f"NANO7_ZLIB={zlib}", f"NANO7_SDLINC={sdl}",
                f"NANO7_APP={game_dir() / 'nano'}", "FORCE=1"]
    _run_make(env_args + [str(py), str(engine / "build_arm.py")], engine, out / "engine.log")
    lib = engine / "obj_arm" / "libpvz_nano.a"
    app_dir = root() / "nanoapps" / "apps" / "pvz_portable"
    if app_dir.exists():
        shutil.rmtree(app_dir)
    shutil.copytree(game_dir() / "nano" / "app", app_dir)
    print("==> PvZ Portable.hbapp (NanoApps SDK, reloc)")
    _run_make(["make", "-C", str(app_dir), f"PVZ_LIB={lib}", f"PYTHON={py}"], app_dir, out / "app.log")
    hbapp = app_dir / "build" / "pvz.hbapp"
    if not hbapp.is_file():
        raise Refused(f"build finished without {hbapp}; see {out}/app.log")
    print("==> repack your game files for the nano (about a minute)")
    tools = out / "tools"
    tools.mkdir(exist_ok=True)
    rp = game_dir() / "repack"
    # -ffp-contract=off: the same textures from any compiler (clang fuses multiply-adds by default)
    for t in ("n7tconv", "n7aconv"):
        _run_make(["cc", "-O2", "-ffp-contract=off", "-o", str(tools / t), str(rp / f"{t}.c"), "-lm"], rp,
                  out / f"{t}.log")
    rout = out / "repack"
    if rout.exists():
        shutil.rmtree(rout)
    _run_make([str(py), str(rp / "repack.py"), "--pak", str(facts["pak"]), "--properties", str(facts["properties"]),
               "--out", str(rout), "--conv", str(tools / "n7tconv"), "--aconv", str(tools / "n7aconv"),
               "--nano-only"], rp, out / "repack.log")
    data = rout / "nano" / "PvZ"
    if not (data / "files.lst").is_file():
        raise Refused(f"repack finished without the nano folder {data}; see {out}/repack.log")
    _stage_hbapp(NAME, hbapp, data)
    stamp_path().write_text(stamp)
    size = hbapp.stat().st_size
    files = [p for p in data.rglob("*") if p.is_file()]
    nbytes = sum(p.stat().st_size for p in files)
    print(f"    staged    {NAME}.hbapp {size:,} bytes, Data/PvZ {len(files)} files {nbytes / 1e6:.1f} MB "
          f"({time.time() - t0:.0f} s)")
    return {"name": NAME, "built": True, "hbapp_size": size, "data_files": len(files), "data_bytes": nbytes,
            "data": str(extra_data_dir(NAME))}


# ---------------------------------------------------------------- plan


def heap_fit(heap_free: int, largest: Optional[int] = None) -> dict:
    """Probe's heap free / largest (before the app loads) -> what nano/pvz_nano.c will do (same arithmetic).

    The loader takes the image (IMAGE_BYTES) in one block first. Where it puts it is not known: if it takes it out
    of the largest block, the arena may no longer fit there. Both cases are given."""
    after_image = heap_free - IMAGE_BYTES
    big = largest if largest is not None else after_image

    def case(lg: int) -> dict:
        size = BLOCK_MAX if lg > BLOCK_MAX + SLOP else max(0, (lg - SLOP) & ~0xFFFF)
        if after_image > AFTER_MIN:
            size = min(size, (after_image - AFTER_MIN) & ~0xFFFF)
        else:
            size = 0
        if lg < BLOCK_MIN + SLOP or size < BLOCK_MIN:
            return {"verdict": "NO_FIT", "block": 0, "left": 0, "budget": {}}
        left = after_image - size
        budget = {}
        for lvl in TEXTURES:   # the budget is the measured OS heap only (the device logs: textures are elsewhere)
            k = max(0, left - TRI_MARGIN) // BYTES_PER_TRIANGLE
            budget[lvl] = max(BUDGET_MIN, min(BUDGET_MAX, k))
        need = ARENA_NEED["1-1..1-10"]
        return {"verdict": "FITS" if size >= need else "FITS_1_1", "block": size, "left": left, "budget": budget}

    best = case(big)
    worst = case(big - IMAGE_BYTES) if largest is not None else best
    return {"after_image": after_image, "best": best, "worst": worst}


def heap_lines(heap_free: Optional[int], largest: Optional[int]) -> List[str]:
    mb = lambda n: f"{n / 1048576:.2f} MB"
    out = [
        f"    memory    image {mb(IMAGE_BYTES)}, arena {mb(BLOCK_MIN)} to {mb(BLOCK_MAX)} (dry run needs "
        f"{mb(ARENA_NEED['1-1'])} for 1-1, {mb(ARENA_NEED['1-1..1-10'])} for 1-1..1-10),",
        f"              GL textures {mb(TEXTURES['1-1'])} (1-1) to {mb(TEXTURES['1-4'])} (1-4), "
        f"{BYTES_PER_TRIANGLE} bytes a triangle (estimate): 1-1 needs {TRIS_NEEDED['1-1']} a frame, 1-10 "
        f"{TRIS_NEEDED['1-10']}",
        f"              pre-flight: under {mb(BLOCK_MIN + SLOP)} largest or {mb(BLOCK_MIN + AFTER_MIN)} free "
        "after the image, it shows NOT ENOUGH MEMORY and never starts",
    ]
    if heap_free is None:
        out.append("    heap      pass Probe's numbers to check: --heap-free 6.4M --largest 4.0M")
        return out
    fit = heap_fit(heap_free, largest)
    out.append(f"    heap      Probe {mb(heap_free)} free" + (f", largest {mb(largest)}" if largest else "")
               + f"; after the image {mb(fit['after_image'])}")
    for label, c in (("image elsewhere", fit["best"]), ("image in largest", fit["worst"])):
        if c["verdict"] == "NO_FIT":
            out.append(f"    {label:<17} DOES NOT FIT: NOT ENOUGH MEMORY on screen (pause music, reopen)")
        else:
            b = c["budget"]
            out.append(f"    {label:<17} arena {mb(c['block'])}, {mb(c['left'])} left -> triangle budget "
                       f"{b['1-1']} (1-1), {b['1-4']} (1-4); "
                       + ("1-1..1-10" if c["verdict"] == "FITS" else "1-1 only"))
    return out


def plan_lines(payload: Optional[Path], heap_free: Optional[int] = None, largest: Optional[int] = None,
               *, on_disk: Optional[List[str]] = None, slot_b: Optional[bool] = None,
               nogrow: Optional[bool] = None) -> List[str]:
    lines = [f"nano7kit apps add '{NAME}' --plan (read-only, nothing built or written)"]
    ok = True
    if payload is None:
        lines.append("    payload   missing: pass --payload /path/to/pvz-goty (main.pak + properties/)")
        ok = False
    else:
        facts = inspect_payload(Path(payload))
        if facts["ok"]:
            lines.append(f"    payload   main.pak sha256 {facts['sha256'][:16]}... (PvZ GOTY), properties/ found")
        else:
            ok = False
            lines.extend("    REFUSED   " + p for p in facts["problems"])
    missing = [t for t in ("make", "arm-none-eabi-gcc", "cc", "patch", "git") if not which(t)]
    lines.append("    host      " + ("make, arm-none-eabi-gcc, cc, patch, git found" if not missing
                                     else "missing " + ", ".join(missing) + " (./start deps; xcode-select --install)"))
    ok = ok and not missing
    lines.append(f"    build     fetches PvZ-Portable {ENGINE_COMMIT[:7]}, zlib 1.3.1, SDL 2.30.9 headers, dlmalloc "
                 "(sha256 pinned); about 5 min")
    lines.append(f"    writes    {FAT_EXE} (about 1.6 MB), /Apps/Icons/{NAME}.bin,")
    lines.append(f"              {FAT_DATA}/ (about {DATA_FILES} files, {DATA_MB} MB: textures, animations, "
                 "sounds as WAV; no music)")
    if slot_b is not None:
        lines.append("    slot      " + ("B active, as for Fruit Ninja and Temple Run" if slot_b
                                         else "slot B is not active: apps add switches to slot B (large GL)"))
    if on_disk is not None:
        after = list(on_disk) if NAME in on_disk else list(on_disk) + [NAME]
        cap = 16 if nogrow is not False else 8
        lines.append(f"    pack      {len(on_disk)} now, with {NAME} {len(after)} of {cap}")
        if len(after) > cap:
            ok = False
            lines.append(f"    REFUSED   over the {cap}-app cap")
    lines.extend(heap_lines(heap_free, largest))
    if heap_free is not None and heap_fit(heap_free, largest)["best"]["verdict"] == "NO_FIT":
        ok = False
    lines.append(f"    undo      ./start apps remove '{NAME}' --keep-files (pack only)")
    lines.append("PLAN_OK" if ok else "PLAN_REFUSED")
    return lines
