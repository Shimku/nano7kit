"""Fruit Ninja 1.0 recompiled for the nano: first-party, built from the user's own .ipa.

games/fruitninja is nano7kit's own static recompiler (Thumb to C), runtime, GL front
end and nano backend, written for this port in the olievans123 style (credit: the
approach of olievans123/nano-templerun; none of its code is used). Nothing from
Halfbrick is in git: the translated C, guest.img and the split fileArcive.bin are
written to gitignored build/fruitninja_rc and artifacts/extra only.

Host only. apps add copies the staged files in Disk Mode afterwards (slot B).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import time
from pathlib import Path
from typing import List, Optional

from .compat import Refused, root

NAME = "Fruit Ninja"
BUNDLE_ID = "com.Halfbrick.Fruit"
WANT_VERSION = "1.0"
EXE_NAME = "Fruit"
# recomp.py reads addresses from exactly this executable (thin armv6, cryptid 0)
EXE_SHA256 = "67754af47b5118089d80c5fb74bba21f3290c02e56541335fe1e82fb0abd7d61"
EXE_SIZE = 2_261_728
ARCHIVE = "fileArcive.bin"
ARCHIVE_FAT = "fileArciveFat.bin"
CHUNK = 128 * 1024                      # nano/plat_nano.c FN_CHUNK
CMD = "./start apps add 'Fruit Ninja' --ipa /path/to/FruitNinja.ipa"
FAT_EXE = "/Apps/Executables/Fruit Ninja.hbapp"
FAT_DATA = "/Apps/Data/FruitNinja"
RECIPE = 1

# ---------------------------------------------------------------- memory model
# Measured on the host harness (games/fruitninja/host) through menu, play and
# game over: guest heap peak 1,075,216 bytes; GL textures by policy below; at
# most 691 triangles a frame that the nano driver would draw (2 px^2 and up).
IMAGE_BYTES = 1_285_852 + 4096          # span of fruitninja.hbapp (log + archive chunk in .bss), plus align
BLOCK_MAX = 0x300000                    # nano/fn_nano.c (front end allocations live inside it too)
BLOCK_MIN = 0x260000
GL_KEEP = 0x140000
AFTER_MIN = 0x180000
SLOP = 0x10000
HEAP_PEAK = 1_075_216
SMALL_BYTES = 40_000                    # C library, file and texture scratch (transient)
TEXTURES = {(1, 1): 1_867_776, (1, 2): 806_912, (2, 2): 643_072}
BYTES_PER_TRIANGLE = 3000               # as Temple Run (hardware measured)
BUDGET_MIN, BUDGET_MAX = 300, 1200
TRIS_DRAWN_MAX = 691


def build_dir() -> Path:
    return root() / "build" / "fruitninja_rc"


def stamp_path() -> Path:
    return build_dir() / "stamp.txt"


def game_dir() -> Path:
    return root() / "games" / "fruitninja"


# ---------------------------------------------------------------- payload


def _app_dir(payload: Path) -> Path:
    from .ports import _payload_app_dir

    return _payload_app_dir(Path(payload), ("Fruit.app", "Fruit Ninja.app"), expected_bundle=BUNDLE_ID)


def inspect_payload(payload: Path) -> dict:
    from .fruitninja import parse_macho

    app = _app_dir(payload)
    exe = app / EXE_NAME
    if not exe.is_file():
        raise Refused(f"{app} has no {EXE_NAME} executable")
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    facts = parse_macho(exe)
    problems = []
    if facts["cryptid"] not in (0, None):
        problems.append(f"the executable is still App Store encrypted (cryptid {facts['cryptid']}).")
    elif digest != EXE_SHA256:
        problems.append(
            f"Fruit executable sha256 {digest[:16]}... is not Fruit Ninja {WANT_VERSION} "
            f"({EXE_SHA256[:8]}...{EXE_SHA256[-8:]}, {EXE_SIZE:,} bytes); the recompiler "
            "reads addresses from exactly that build."
        )
    for name in (ARCHIVE, ARCHIVE_FAT):
        if not (app / name).is_file():
            problems.append(f"{name} is missing in {app.name}.")
    return {"app": app, "exe": exe, "sha256": digest, "cryptid": facts["cryptid"],
            "ok": not problems, "problems": problems}


def assert_payload(payload: Path) -> dict:
    facts = inspect_payload(payload)
    if not facts["ok"]:
        raise Refused(" ".join(facts["problems"]) + " Your files stay local; nothing was built.")
    return facts


# ---------------------------------------------------------------- data


def split_archive(src: Path, out: Path, chunk: int = CHUNK) -> int:
    """fileArcive.bin -> out/fa/000.bin.. (hb_fs_read reads whole files) + fa/size.txt."""
    fa = out / "fa"
    if fa.exists():
        shutil.rmtree(fa)
    fa.mkdir(parents=True)
    size = src.stat().st_size
    n = 0
    with src.open("rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            (fa / f"{n:03d}.bin").write_bytes(b)
            n += 1
    (fa / "size.txt").write_text(f"{size}\n", encoding="ascii")
    return n


# ---------------------------------------------------------------- build


def _jobs() -> str:
    return f"-j{max(1, min(8, os.cpu_count() or 2))}"


def _sources_digest() -> str:
    h = hashlib.sha256()
    for p in sorted(game_dir().rglob("*")):
        if p.is_file():
            h.update(p.relative_to(game_dir()).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def stamp_value() -> str:
    from .templerun import _sdk_digest

    return f"recipe={RECIPE} exe={EXE_SHA256[:16]} src={_sources_digest()} sdk={_sdk_digest()}\n"


def _staged() -> bool:
    from . import const
    from .ports import data_ready

    return const.artifact("artifacts/extra/Executables/Fruit Ninja.hbapp").is_file() and data_ready(NAME)


def build(payload: Path, *, force: bool = False) -> dict:
    """Host-only build of Fruit Ninja.hbapp + Data/FruitNinja from the user's ipa."""
    from .ports import _run_make, _stage_hbapp, extra_data_dir
    from .templerun import ensure_venv

    t0 = time.time()
    print("nano7kit Fruit Ninja build (host only, no NAND, nothing written to the nano)")
    print("Recompiler: nano7kit games/fruitninja (first-party, olievans123-style static")
    print("recompile). Game: Halfbrick, from your own .ipa (stays local, never committed).")
    facts = assert_payload(payload)
    print(f"    payload   {facts['app']}")
    print(f"    Fruit     sha256 {facts['sha256'][:16]}... (Fruit Ninja {WANT_VERSION}, decrypted)")
    stamp = stamp_value()
    if not force and _staged() and stamp_path().is_file() and stamp_path().read_text() == stamp:
        print("    build     already staged for this ipa (stamp match), skipping rebuild")
        return {"name": NAME, "built": False, "data": str(extra_data_dir(NAME))}
    py = ensure_venv()                  # pyelftools for mkrelocapp
    out = build_dir()
    recomp = out / "recomp"
    data = out / "data"
    for d in (recomp, data, out / "nano"):
        if d.exists():
            shutil.rmtree(d)
    out.mkdir(parents=True, exist_ok=True)
    print("==> recomp.py (Thumb -> C)")
    _run_make([str(py), str(game_dir() / "tools" / "recomp.py"), str(facts["exe"]), str(recomp)],
              out, out / "recomp.log")
    print("==> arm-none-eabi-gcc libfn_nano.a")
    _run_make(["make", _jobs(), "-C", str(game_dir() / "nano"), f"NANOAPPS={root() / 'nanoapps'}",
               f"RECOMP={recomp}", f"BUILD={out / 'nano'}"], out, out / "nano.log")
    app_dir = root() / "nanoapps" / "apps" / "fruitninja_rc"
    if app_dir.exists():
        shutil.rmtree(app_dir)
    shutil.copytree(game_dir() / "nano" / "app", app_dir)
    print("==> fruitninja.hbapp (NanoApps SDK, reloc)")
    _run_make(["make", "-C", str(app_dir), f"FN_LIB={out / 'nano' / 'libfn_nano.a'}", f"PYTHON={py}"],
              app_dir, out / "app.log")
    hbapp = app_dir / "build" / "fruitninja.hbapp"
    if not hbapp.is_file():
        raise Refused(f"build finished without {hbapp}; see {out}/app.log")
    data.mkdir(parents=True)
    shutil.copyfile(recomp / "guest.img", data / "guest.img")
    shutil.copyfile(facts["app"] / ARCHIVE_FAT, data / ARCHIVE_FAT)
    pieces = split_archive(facts["app"] / ARCHIVE, data)
    # MusicArcive.bin stays behind: the port is silent and the game runs without it
    _stage_hbapp(NAME, hbapp, data)
    stamp_path().write_text(stamp)
    size = hbapp.stat().st_size
    files = [p for p in data.rglob("*") if p.is_file()]
    nbytes = sum(p.stat().st_size for p in files)
    print(f"    staged    Fruit Ninja.hbapp {size:,} bytes, Data/FruitNinja {len(files)} files "
          f"{nbytes / 1e6:.1f} MB ({pieces} archive pieces, {time.time() - t0:.0f} s)")
    return {"name": NAME, "built": True, "hbapp_size": size, "data_files": len(files),
            "data_bytes": nbytes, "data": str(extra_data_dir(NAME))}


# ---------------------------------------------------------------- plan


def _policy(after: int):
    big = 2 if after < GL_KEEP + 0x200000 else 1
    small = 2 if after < GL_KEEP + 0xC0000 else 1
    return small, big


def heap_fit(heap_free: int, largest: Optional[int] = None) -> dict:
    """Probe's heap free / largest -> what nano/fn_nano.c will do (same arithmetic)."""
    after_image = heap_free - IMAGE_BYTES
    big = largest if largest is not None else after_image
    out = {"after_image": after_image, "block": 0, "policy": None, "textures": 0,
           "left_for_gl": 0, "budget": 0}
    block = BLOCK_MAX if big > BLOCK_MAX + SLOP else max(0, (big - SLOP) & ~0xFFFF)
    if big < BLOCK_MIN + SLOP or block < BLOCK_MIN or after_image < block + AFTER_MIN:
        out["verdict"] = "NO_FIT"
        return out
    after = after_image - block
    pol = _policy(after)
    tex = TEXTURES[pol]
    left = after - SMALL_BYTES - tex
    budget = max(BUDGET_MIN, min(BUDGET_MAX, left // BYTES_PER_TRIANGLE))
    out.update(block=block, policy=pol, textures=tex, left_for_gl=left, budget=budget,
               image_in_largest_ok=largest is None or largest - IMAGE_BYTES >= BLOCK_MIN + SLOP)
    out["verdict"] = "FITS" if budget >= TRIS_DRAWN_MAX else "FITS_REDUCED"
    return out


def heap_lines(heap_free: Optional[int], largest: Optional[int]) -> List[str]:
    mb = lambda n: f"{n / 1048576:.2f} MB"
    out = [
        f"    memory    image {mb(IMAGE_BYTES)}, one guest block {mb(BLOCK_MIN)} to {mb(BLOCK_MAX)} "
        f"(guest heap peak {mb(HEAP_PEAK)}),",
        f"              GL textures {mb(TEXTURES[(2, 2)])} to {mb(TEXTURES[(1, 1)])} by free memory, "
        f"{BYTES_PER_TRIANGLE} bytes a triangle ({TRIS_DRAWN_MAX} at the busiest)",
        f"              pre-flight: under {mb(BLOCK_MIN + SLOP)} largest or {mb(BLOCK_MAX + AFTER_MIN)} free "
        "after the image, it shows NOT ENOUGH MEMORY and never starts",
    ]
    if heap_free is None:
        out.append("    heap      pass Probe's numbers to check: --heap-free 6.4M --largest 4.0M")
        return out
    fit = heap_fit(heap_free, largest)
    out.append(f"    heap      Probe {mb(heap_free)} free" + (f", largest {mb(largest)}" if largest else "")
               + f"; after the image {mb(fit['after_image'])}")
    if fit["block"]:
        s, b = fit["policy"]
        out.append(f"              block {mb(fit['block'])}, textures {mb(fit['textures'])} "
                   f"(shift {s}/{b}), {mb(fit['left_for_gl'])} for the driver -> budget {fit['budget']}")
    verdict = {
        "FITS": "FITS (every triangle of the busiest frame)",
        "FITS_REDUCED": f"FITS with a reduced triangle budget ({fit['budget']} of {TRIS_DRAWN_MAX}: "
                        "busy juice splashes thin out)",
        "NO_FIT": "DOES NOT FIT: the app will show NOT ENOUGH MEMORY (pause music, reopen)",
    }[fit["verdict"]]
    out.append(f"    verdict   {verdict}")
    return out


def plan_lines(payload: Optional[Path], heap_free: Optional[int] = None, largest: Optional[int] = None,
               *, on_disk: Optional[List[str]] = None, slot_b: Optional[bool] = None,
               nogrow: Optional[bool] = None) -> List[str]:
    from .compat import which

    lines = ["nano7kit apps add FruitNinja --plan (read-only, nothing built or written)"]
    ok = True
    if payload is None:
        lines.append("    payload   missing: pass --ipa /path/to/FruitNinja.ipa (Fruit Ninja 1.0)")
        ok = False
    else:
        try:
            facts = inspect_payload(Path(payload))
            lines.append(f"    payload   {BUNDLE_ID}, Fruit sha256 {facts['sha256'][:16]}...")
            if not facts["ok"]:
                ok = False
                lines.extend("    REFUSED   " + p for p in facts["problems"])
        except Refused as exc:
            ok = False
            lines.append(f"    payload   REFUSED: {exc}")
    missing = [t for t in ("make", "arm-none-eabi-gcc") if not which(t)]
    lines.append("    host      " + ("make, arm-none-eabi-gcc found" if not missing
                                     else "missing " + ", ".join(missing) + " (source env.sh / ./start deps)"))
    ok = ok and not missing
    lines.append("    build     recomp.py + arm-none-eabi-gcc (about 1 min), nothing fetched")
    lines.append(f"    writes    {FAT_EXE} (about 920 KB), /Apps/Icons/Fruit Ninja.bin,")
    lines.append(f"              {FAT_DATA}/ (guest.img, fileArciveFat.bin, fa/ 66 pieces; about 9.2 MB)")
    if slot_b is not None:
        lines.append("    slot      " + ("B active, as for Mario and Temple Run" if slot_b
                                         else "slot B is not active: apps add switches to slot B (large GL)"))
    if on_disk is not None:
        after = list(on_disk) if NAME in on_disk else list(on_disk) + [NAME]
        cap = 16 if nogrow is not False else 8
        lines.append(f"    pack      {len(on_disk)} now, with Fruit Ninja {len(after)} of {cap}")
        if len(after) > cap:
            ok = False
            lines.append(f"    REFUSED   over the {cap}-app cap")
    lines.extend(heap_lines(heap_free, largest))
    if heap_free is not None and heap_fit(heap_free, largest)["verdict"] == "NO_FIT":
        ok = False
    lines.append("    undo      ./start apps remove 'Fruit Ninja' --keep-files (pack only)")
    lines.append("PLAN_OK" if ok else "PLAN_REFUSED")
    return lines
