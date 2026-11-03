"""Temple Run 1.0 (olievans123/nano-templerun) built from the user's own .ipa.

Nothing from Imangi is in git. The pinned nano-templerun tree (no OSS
licence: fetched at build time, credited, never vendored) translates the
armv7 half of the user's TempleRun executable into C and converts the
user's textures and models. Everything derived lands in gitignored
build/ports/templerun, build/templerun and artifacts/extra.

Host only. Nothing here touches the nano; apps add copies the staged
files in Disk Mode afterwards (slot B, like Mario).
"""

from __future__ import annotations

import hashlib
import os
import plistlib
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

from . import templerun_patch
from .compat import Refused, root

# nano-templerun README: "the armv7 half of the original executable
# (sha256 91d85b35...afe01392, 1,439,184 bytes)". recomp.py hard-codes
# addresses from exactly this slice, so any other build is refused.
ARMV7_SHA256 = "91d85b352333c95531dbe9b2a2edd3e772f82ee038e386953a4b0929afe01392"
ARMV7_SIZE = 1439184
BUNDLE_ID = "com.imangi.templerun"
WANT_VERSION = "1.0"

CPU_TYPE_ARM = 12
CPU_SUBTYPE_ARM_V7 = 9
LC_ENCRYPTION_INFO = 0x21
FAT_MAGIC = 0xCAFEBABE
MH_MAGIC = 0xFEEDFACE

# Every wheel below exists for macOS x86_64 (and arm64) on CPython 3.12,
# so the venv never needs a compiler. capstone 5.0.6 gives byte-identical
# recomp output to newer 5.0.x.
BUILD_PINS = (
    "capstone==5.0.6",
    "texture2ddecoder==1.0.5",
    "numpy==2.2.6",
    "Pillow==12.1.1",
    "pyelftools==0.32",
)
BUILD_IMPORTS = "import capstone, texture2ddecoder, numpy, PIL, elftools"

# Bump when the build recipe changes so a staged copy is rebuilt.
RECIPE = 6  # Temple Run pre-flight before its own arena (it measured itself), step logging; delete textures earlier ports left in the shared GL context, record its own (gl_left.c); memory pre-flight + NOT ENOUGH MEMORY screen; budget.txt bytes-per-triangle knob (arena/budget patch + 256 scenery textures)

# Runtime budget, from nano-templerun port/nano (pinned commit):
#  - tr_entry.c reads "/Apps/Executables/Temple Run.hbapp" back on relaunch
#    and gives up above 3 MB, so the FAT name and size limit are fixed.
#  - nano_alloc.c takes one arena of PORT_ARENA_SIZE (0x330000) from the OS
#    heap and hands it to the resident as the aux block (freed on unload).
#    Upstream clamps it to heap free minus a 0x380000 GL reserve;
#    templerun_patch.py asks for the whole arena first.
FAT_EXE = "/Apps/Executables/Temple Run.hbapp"
FAT_DATA = "/Apps/Data/TempleRun"
RELAUNCH_IMAGE_LIMIT = 3 * 1024 * 1024
ARENA_BYTES = 0x330000
GL_RESERVE = 0x380000
ARENA_MIN = 0x80000

CMD = "./start apps add TempleRun --ipa /path/to/TempleRun.ipa"


def build_dir() -> Path:
    return root() / "build" / "templerun"


def venv_dir() -> Path:
    return build_dir() / "venv"


def stamp_path() -> Path:
    return build_dir() / "stamp.txt"


# ---------------------------------------------------------------- Mach-O


def macho_slices(data: bytes) -> List[dict]:
    """Slices of a fat (or thin 32-bit) Mach-O: cputype, subtype, offset, size."""
    if len(data) < 28:
        raise Refused("TempleRun executable is too short to be Mach-O")
    magic = struct.unpack_from(">I", data, 0)[0]
    if magic == FAT_MAGIC:
        count = struct.unpack_from(">I", data, 4)[0]
        if count > 16:
            raise Refused(f"fat Mach-O claims {count} slices")
        out = []
        for i in range(count):
            ct, cs, off, size, _align = struct.unpack_from(">5I", data, 8 + 20 * i)
            if off + size > len(data):
                raise Refused(f"fat Mach-O slice {i} runs past the file")
            out.append({"cputype": ct, "subtype": cs & 0xFFFFFF, "offset": off, "size": size})
        return out
    if struct.unpack_from("<I", data, 0)[0] == MH_MAGIC:
        ct, cs = struct.unpack_from("<II", data, 4)
        return [{"cputype": ct, "subtype": cs & 0xFFFFFF, "offset": 0, "size": len(data)}]
    raise Refused("TempleRun executable is not a 32-bit iOS Mach-O")


def thin_armv7(data: bytes) -> bytes:
    """Same bytes as `lipo -thin armv7`, without needing Xcode."""
    for s in macho_slices(data):
        if s["cputype"] == CPU_TYPE_ARM and s["subtype"] == CPU_SUBTYPE_ARM_V7:
            return data[s["offset"] : s["offset"] + s["size"]]
    raise Refused("TempleRun executable has no armv7 slice (Temple Run 1.0 has armv6 + armv7)")


def cryptid(thin: bytes) -> Optional[int]:
    """LC_ENCRYPTION_INFO cryptid of a thin 32-bit Mach-O, or None if absent."""
    if len(thin) < 28 or struct.unpack_from("<I", thin, 0)[0] != MH_MAGIC:
        raise Refused("armv7 slice is not a 32-bit Mach-O")
    ncmds = struct.unpack_from("<I", thin, 16)[0]
    pos = 28
    for _ in range(ncmds):
        if pos + 8 > len(thin):
            break
        cmd, size = struct.unpack_from("<II", thin, pos)
        if cmd == LC_ENCRYPTION_INFO and pos + 20 <= len(thin):
            return struct.unpack_from("<I", thin, pos + 16)[0]
        if size < 8:
            break
        pos += size
    return None


# ---------------------------------------------------------------- payload


def _app_dir(payload: Path) -> Path:
    from .ports import _payload_app_dir

    return _payload_app_dir(
        Path(payload), ("TempleRun.app", "Temple Run.app"), expected_bundle=BUNDLE_ID
    )


def inspect_payload(payload: Path) -> dict:
    """Read-only facts about the user's Temple Run. Unzips an .ipa under build/payloads."""
    app = _app_dir(payload)
    info = {}
    try:
        info = plistlib.loads((app / "Info.plist").read_bytes())
    except Exception:
        pass
    exe = app / str(info.get("CFBundleExecutable") or "TempleRun")
    if not exe.is_file():
        raise Refused(f"TempleRun executable missing in {app}")
    thin = thin_armv7(exe.read_bytes())
    digest = hashlib.sha256(thin).hexdigest()
    cid = cryptid(thin)
    version = str(info.get("CFBundleShortVersionString") or info.get("CFBundleVersion") or "?")
    problems = []
    if cid not in (0, None):
        problems.append(
            "the executable is still App Store encrypted (cryptid "
            f"{cid}). nano-templerun needs a decrypted Temple Run 1.0."
        )
    elif digest != ARMV7_SHA256:
        problems.append(
            f"this is Temple Run {version}, armv7 sha256 {digest[:16]}... "
            f"nano-templerun only translates Temple Run {WANT_VERSION} "
            f"(armv7 sha256 {ARMV7_SHA256[:8]}...{ARMV7_SHA256[-8:]}, "
            f"{ARMV7_SIZE:,} bytes). Other versions move every address."
        )
    return {
        "app": app,
        "exe": exe,
        "version": version,
        "bundle": info.get("CFBundleIdentifier"),
        "armv7_sha256": digest,
        "armv7_size": len(thin),
        "cryptid": cid,
        "ok": not problems,
        "problems": problems,
    }


def assert_payload(payload: Path) -> dict:
    facts = inspect_payload(payload)
    if not facts["ok"]:
        raise Refused(" ".join(facts["problems"]) + " Your files stay local; nothing was built.")
    return facts


# ---------------------------------------------------------------- build


def ensure_venv() -> Path:
    """Private build/templerun/venv with pinned wheels. Returns its python."""
    py = venv_dir() / "bin" / "python"
    if py.is_file():
        r = subprocess.run([str(py), "-c", BUILD_IMPORTS], capture_output=True)
        if r.returncode == 0:
            return py
    if not py.is_file():
        venv_dir().parent.mkdir(parents=True, exist_ok=True)
        print(f"==> python venv {venv_dir()}")
        subprocess.check_call([sys.executable, "-m", "venv", str(venv_dir())])
    print("==> pip install " + " ".join(BUILD_PINS))
    rc = subprocess.call(
        [str(py), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", *BUILD_PINS]
    )
    if rc != 0:
        raise Refused(
            "pip could not install the Temple Run build wheels "
            f"({', '.join(BUILD_PINS)}). Check the network, then re-run."
        )
    return py


def _sdk_digest() -> str:
    h = hashlib.sha256()
    sdk = root() / "nanoapps" / "sdk"
    for p in sorted(sdk.rglob("*")):
        if p.is_file() and p.suffix in {".c", ".h", ".mk", ".ld", ".S"}:
            h.update(p.relative_to(sdk).as_posix().encode())
            h.update(p.read_bytes())
    tool = root() / "nanoapps" / "tools" / "mkrelocapp.py"
    if tool.is_file():
        h.update(tool.read_bytes())
    # nano7kit's own patch and the files it copies in (notice, gl_left) count too
    from .templerun_patch import NOTICE_FILES

    extra = [root() / "host" / "templerun_patch.py"]
    extra += [root() / "games" / "fruitninja" / "nano" / n for n in NOTICE_FILES]
    for p in extra:
        if p.is_file():
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def stamp_value(armv7_sha256: str) -> str:
    from .ports import PORT_REPOS

    return (
        f"recipe={RECIPE} upstream={PORT_REPOS['templerun']['commit']} "
        f"armv7={armv7_sha256} sdk={_sdk_digest()}\n"
    )


def _staged() -> bool:
    from . import const
    from .ports import data_ready

    exe = const.artifact("artifacts/extra/Executables/Temple Run.hbapp")
    return exe.is_file() and data_ready("Temple Run")


def _link_original(repo: Path, app: Path) -> None:
    orig = repo / "original" / "v1.0" / "Payload" / "TempleRun.app"
    orig.parent.mkdir(parents=True, exist_ok=True)
    if orig.is_symlink() or orig.is_file():
        orig.unlink()
    elif orig.is_dir():
        shutil.rmtree(orig)
    try:
        orig.symlink_to(app.resolve(), target_is_directory=True)
    except OSError:
        shutil.copytree(app, orig)


def _jobs() -> str:
    return f"-j{max(1, min(8, os.cpu_count() or 2))}"


def build(payload: Path, *, force: bool = False) -> dict:
    """Host-only build of Temple Run.hbapp + Data/TempleRun from the user's ipa."""
    from .ports import _copy_app_shell, _run_make, _stage_hbapp, extra_data_dir, fetch_ios_port

    t0 = time.time()
    print("nano7kit Temple Run build (host only, no NAND, nothing written to the nano)")
    print("Port: olievans123/nano-templerun (fetched at build time, no OSS licence, not")
    print("redistributed). Game: Imangi Studios, from your own .ipa (stays local).")
    facts = assert_payload(payload)
    print(f"    payload   {facts['app']}")
    print(f"    armv7     sha256 {facts['armv7_sha256'][:16]}... (Temple Run {WANT_VERSION}, decrypted)")
    stamp = stamp_value(facts["armv7_sha256"])
    if (
        not force
        and _staged()
        and stamp_path().is_file()
        and stamp_path().read_text(encoding="utf-8") == stamp
    ):
        print("    build     already staged for this ipa (stamp match), skipping rebuild")
        return {"name": "Temple Run", "built": False, "data": str(extra_data_dir("Temple Run"))}

    repo = fetch_ios_port("templerun")
    _link_original(repo, facts["app"])
    armv7 = repo / "build" / "TempleRun-armv7"
    armv7.parent.mkdir(parents=True, exist_ok=True)
    armv7.write_bytes(thin_armv7(facts["exe"].read_bytes()))
    py = ensure_venv()
    logs = build_dir()
    logs.mkdir(parents=True, exist_ok=True)
    recomp = repo / "build" / "recomp"
    assets = repo / "build" / "assets"
    for d in (recomp, assets, repo / "port" / "build"):
        if d.exists():
            shutil.rmtree(d)
    print("==> recomp.py (ARM -> C, 741 engine functions)")
    _run_make([str(py), str(repo / "tools" / "recomp.py"), str(recomp)], repo, logs / "recomp.log")
    print("==> convert_assets.py (textures, models, engine image)")
    _run_make([str(py), str(repo / "tools" / "convert_assets.py"), str(assets)], repo, logs / "assets.log")
    shrunk = templerun_patch.shrink_textures(assets / "nano")
    print("==> nano7kit memory edits: " + ", ".join(templerun_patch.apply(repo))
          + "; scenery at 256: " + (", ".join(shrunk) or "none"))
    app_dir = _copy_app_shell("templerun")
    if (app_dir / "build").exists():
        shutil.rmtree(app_dir / "build")
    print("==> arm-none-eabi-gcc libtr_nano.a")
    _run_make(
        [
            "make", _jobs(), "-C", str(repo / "port" / "nano"),
            f"NANOAPPS={root() / 'nanoapps'}", f"RECOMP={recomp}",
        ],
        repo,
        logs / "nano.log",
    )
    print("==> templerun.hbapp (NanoApps SDK, reloc)")
    _run_make(
        ["make", "-C", str(app_dir), f"TR_DIR={repo / 'port'}", f"PYTHON={py}"],
        app_dir,
        logs / "app.log",
    )
    hbapp = app_dir / "build" / "templerun.hbapp"
    if not hbapp.is_file():
        raise Refused(f"build finished without {hbapp}; see {logs}/app.log")
    size = hbapp.stat().st_size
    if size > RELAUNCH_IMAGE_LIMIT:
        raise Refused(
            f"templerun.hbapp is {size:,} bytes; tr_entry.c cannot reload images above "
            f"{RELAUNCH_IMAGE_LIMIT:,}. Not staging it."
        )
    nano_data = assets / "nano"
    if not (nano_data / "files.lst").is_file() or not (nano_data / "engine.img").is_file():
        raise Refused(f"convert_assets.py did not write files.lst / engine.img; see {logs}/assets.log")
    _stage_hbapp("Temple Run", hbapp, nano_data)
    stamp_path().write_text(stamp, encoding="utf-8")
    data_bytes = sum(p.stat().st_size for p in nano_data.rglob("*") if p.is_file())
    n_files = sum(1 for p in nano_data.rglob("*") if p.is_file())
    print(
        f"    staged    Temple Run.hbapp {size:,} bytes, Data/TempleRun {n_files} files "
        f"{data_bytes / 1e6:.1f} MB ({time.time() - t0:.0f} s)"
    )
    return {
        "name": "Temple Run",
        "built": True,
        "hbapp_size": size,
        "data_files": n_files,
        "data_bytes": data_bytes,
        "data": str(extra_data_dir("Temple Run")),
    }


# ---------------------------------------------------------------- plan


# Memory model (see templerun_patch.py; measured with the upstream engine
# on a host harness: arena peak 3,238,959 bytes = engine 1,400,832 + models
# 1,703,802 + small; GL textures 942,080 bytes once the 1024 sheets are 256).
IMAGE_BYTES = 1_401_904 + 4096          # span of templerun.reloc.elf (+ pre-flight screen), plus align
ARENA_FLOOR = 0x320000                  # templerun_patch.ARENA_FLOOR
TEXTURE_BYTES = 942_080
BYTES_PER_TRIANGLE = 3000
BUDGET_MIN, BUDGET_MAX = 400, 1300


def parse_size(text) -> Optional[int]:
    """'6.6M', '6.6 MB', '6758K', '6920601' -> bytes. Bare numbers under 64 are MB."""
    if text is None or str(text).strip() == "":
        return None
    t = str(text).strip().upper().replace(" ", "").rstrip("B")
    mult = 1
    if t.endswith("M"):
        mult, t = 1 << 20, t[:-1]
    elif t.endswith("K"):
        mult, t = 1 << 10, t[:-1]
    try:
        v = float(t)
    except ValueError:
        raise Refused(f"cannot read the size {text!r}; use Probe's numbers like 6.6M or 4.0M")
    if mult == 1 and v < 64:
        mult = 1 << 20
    return int(v * mult)


def arena_for(heap_free: int, largest: Optional[int] = None) -> int:
    """arena_init() as patched: the whole arena if a block takes it, else down to the floor, else 0."""
    block = largest if largest is not None else heap_free
    if block >= ARENA_BYTES:
        return ARENA_BYTES
    if block >= ARENA_FLOOR:
        return block & ~0xFFFF
    return 0


def heap_fit(heap_free: int, largest: Optional[int] = None) -> dict:
    """Probe's heap free / largest (Probe loaded) -> Temple Run's budget.

    Probe's own image is tiny, and the sled and framebuffer it allocates stay
    for the boot, so its numbers are what Temple Run's loader starts from.
    """
    after_image = heap_free - IMAGE_BYTES
    # the image goes in first; if it lands in the largest block, that block
    # shrinks by the image. Report both cases.
    best = arena_for(after_image, largest)
    worst = arena_for(after_image, None if largest is None else largest - IMAGE_BYTES)
    arena = best
    left = after_image - arena - TEXTURE_BYTES
    budget = max(BUDGET_MIN, min(BUDGET_MAX, left // BYTES_PER_TRIANGLE)) if arena else 0
    from .templerun_patch import NEED_FREE, NEED_LARGEST

    short = after_image < NEED_FREE or (largest is not None and largest < NEED_LARGEST)
    if not arena or short:
        verdict = "NO_FIT"
    elif left >= BUDGET_MAX * BYTES_PER_TRIANGLE:
        verdict = "FITS"
    elif left >= BUDGET_MIN * BYTES_PER_TRIANGLE:
        verdict = "FITS_REDUCED"
    else:
        verdict = "TIGHT"
    return {
        "after_image": after_image,
        "arena": arena,
        "arena_if_image_in_largest": worst,
        "left_for_gl": left,
        "budget": budget,
        "verdict": verdict,
    }


def heap_lines(heap_free: Optional[int], largest: Optional[int]) -> List[str]:
    mb = lambda n: f"{n / 1048576:.2f} MB"
    out = [
        f"    memory    image {mb(IMAGE_BYTES)} first, then one arena {mb(ARENA_BYTES)} (floor "
        f"{mb(ARENA_FLOOR)}; engine 1.34 + models 1.62),",
        f"              GL textures {mb(TEXTURE_BYTES)}, and the triangle budget takes about "
        f"{BYTES_PER_TRIANGLE} bytes",
        f"              of what is left per triangle ({BUDGET_MIN} to {BUDGET_MAX}); needs about "
        f"{mb(IMAGE_BYTES + ARENA_BYTES + TEXTURE_BYTES + BUDGET_MIN * BYTES_PER_TRIANGLE)} free "
        f"to play, {mb(IMAGE_BYTES + ARENA_BYTES + TEXTURE_BYTES + BUDGET_MAX * BYTES_PER_TRIANGLE)} for full scenery",
    ]
    from .templerun_patch import NEED_FREE, NEED_LARGEST

    out.append(
        f"              pre-flight: under {mb(NEED_LARGEST)} largest or {mb(NEED_FREE)} free after the "
        "image it shows NOT ENOUGH MEMORY and never starts"
    )
    if heap_free is None:
        out.append("    heap      pass Probe's numbers to check: --heap-free 6.6M --largest 4.0M")
        return out
    fit = heap_fit(heap_free, largest)
    out.append(
        f"    heap      Probe {mb(heap_free)} free"
        + (f", largest {mb(largest)}" if largest is not None else "")
        + f"; after the image {mb(fit['after_image'])}"
    )
    if fit["arena"]:
        out.append(
            f"              arena {mb(fit['arena'])}, then {mb(max(0, fit['left_for_gl']))} for the GL driver "
            f"after textures -> budget about {fit['budget']} triangles"
        )
    if largest is not None and fit["arena"] and not fit["arena_if_image_in_largest"]:
        out.append(
            "              if the OS puts the image inside the largest block, no 3.1 MB block is left;"
        )
        out.append(
            "              log.txt then says 'nano7kit arena 0 bytes' or a small arena before 'initialization failed'"
        )
    verdict = {
        "FITS": "FITS (full scenery budget)",
        "FITS_REDUCED": "FITS with a reduced triangle budget (less distant scenery, same game)",
        "TIGHT": "TIGHT: loads, but the driver has under 1.2 MB; risk of a reboot in play",
        "NO_FIT": "DOES NOT FIT: the app will show NOT ENOUGH MEMORY (pause music, reopen)",
    }[fit["verdict"]]
    out.append(f"    verdict   {verdict}")
    return out


def plan_lines(
    payload: Optional[Path],
    *,
    on_disk: Optional[List[str]] = None,
    slot_b: Optional[bool] = None,
    nogrow: Optional[bool] = None,
    heap_free: Optional[int] = None,
    largest: Optional[int] = None,
) -> List[str]:
    from .compat import which

    lines = ["nano7kit apps add TempleRun --plan (read-only, nothing built or written)"]
    ok = True
    if payload is None:
        lines.append("    payload   missing: pass --ipa /path/to/TempleRun.ipa (Temple Run 1.0)")
        ok = False
    else:
        try:
            facts = inspect_payload(Path(payload))
        except Refused as exc:
            facts = None
            ok = False
            lines.append(f"    payload   REFUSED: {exc}")
        if facts:
            lines.append(
                f"    payload   {facts['bundle']} {facts['version']}, armv7 "
                f"{facts['armv7_size']:,} bytes sha256 {facts['armv7_sha256'][:16]}..."
            )
            if facts["ok"]:
                lines.append("              matches nano-templerun (decrypted Temple Run 1.0)")
            else:
                ok = False
                for p in facts["problems"]:
                    lines.append("    REFUSED   " + p)
    tools = {t: bool(which(t)) for t in ("git", "make", "arm-none-eabi-gcc")}
    missing = [t for t, have in tools.items() if not have]
    lines.append(
        "    host      " + ("git, make, arm-none-eabi-gcc found" if not missing
                            else "missing " + ", ".join(missing) + " (source env.sh / ./start deps)")
    )
    if missing:
        ok = False
    lines.append(
        "    build     fetch olievans123/nano-templerun, pip " + ", ".join(BUILD_PINS)
        + f" into {venv_dir()}, recomp + assets + arm-none-eabi-gcc (about 1 to 3 min)"
    )
    lines.append(f"    writes    {FAT_EXE} (about 690 KB), /Apps/Icons/Temple Run.bin,")
    lines.append(f"              {FAT_DATA}/ (146 files, about 4.2 MB), active Home pack")
    if slot_b is not None:
        lines.append("    slot      " + ("B active (AllApps-B.pack), as for Mario" if slot_b
                                         else "A: apps add switches to slot B (large GL)"))
    if on_disk is not None:
        names = list(on_disk)
        after = names if "Temple Run" in names else names + ["Temple Run"]
        cap = 16 if nogrow else 8
        lines.append(f"    pack      {len(names)} now -> {len(after)} after; cap {cap}"
                     + (" (nogrow on)" if nogrow else ""))
        if len(after) > cap:
            ok = False
            lines.append("    REFUSED   over the pack cap")
    lines.extend(heap_lines(heap_free, largest))
    if heap_free is not None and heap_fit(heap_free, largest)["verdict"] == "NO_FIT":
        ok = False
    lines.append("    undo      ./start apps remove TempleRun --keep-files (pack only)")
    lines.append("              ./start apps remove TempleRun (also deletes exe + icon)")
    lines.append("PLAN_OK" if ok else "PLAN_REFUSED")
    return lines
