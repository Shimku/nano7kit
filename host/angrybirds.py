"""Angry Birds 1.0 recompiled for the nano: first-party, built from the user's own .ipa.

games/angrybirds is nano7kit's own static recompiler (ARM+VFP to C), runtime, GL
front end and nano backend, written for this port in the olievans123 style (credit:
the approach of olievans123/nano-templerun; none of its code is used). Nothing from
Rovio is in git: the translated C, guest.img and converted data/ files are written
to gitignored build/angrybirds_rc and artifacts/extra only.

Host only. nabase add copies the staged files onto the mounted iPod volume afterwards (slot B).
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

NAME = "Angry Birds"
BUNDLE_ID = "com.clickgamer.AngryBirds"
WANT_VERSION = "1.0"
EXE_NAME = "AngryBirds"
EXE_SHA256 = "e1dadaf6e07c02307d8c1ee9345b9c0e80b954b903dd3150f19d43eae66ec6e8"
EXE_SIZE = 1_822_112
IPA_SHA256_PREFIX = "cdea1290"
CMD = "./start nabase add 'Angry Birds' --payload /path/to/AngryBirds.ipa"
FAT_EXE = "/Apps/Executables/Angry Birds.hbapp"
FAT_DATA = "/Apps/Data/AngryBirds"
RECIPE = 14

# Memory model. IMAGE_BYTES is the loaded .hbapp span (code + log in .bss).
# The translated engine is Thumb + software float so this fits; runtime/GL stay ARM+VFP.
# Guest.img is __cstring through DATA (~80 KB); original ARM TEXT is not mapped
# (PC-relative pools are folded into C). Lua/Box2D live in the 2 MB guest block;
# 256 KB+ image decode is OS heap.
# Textures: MENU/SPLASHES/TUTORIALS keep 512, the rest 256 (see convert_assets).
IMAGE_BYTES = 2_393_472 + 4096 + 32768  # hbapp span plus 64 KB log ring in .bss
BLOCK_MAX = 0x200000
BLOCK_MIN = 0x190000
GL_KEEP = 0x180000
AFTER_MIN = 0x1A0000
SLOP = 0x10000
HEAP_PEAK = 1_100_000                     # guest only; 256 KB+ decode is OS heap (peak ~1.08 MB through level 1)
SMALL_BYTES = 40_000
TEXTURE_BYTES = 2_105_344                 # host peak, MENU+SPLASHES+TUTORIALS keep 512, rest 256
BYTES_PER_TRIANGLE = 3000
BUDGET_MIN, BUDGET_MAX = 300, 1200
TRIS_DRAWN_MAX = 800


def build_dir() -> Path:
    return root() / "build" / "angrybirds_rc"


def stamp_path() -> Path:
    return build_dir() / "stamp.txt"


def game_dir() -> Path:
    return root() / "games" / "angrybirds"


def _app_dir(payload: Path) -> Path:
    from .ports import _payload_app_dir

    return _payload_app_dir(Path(payload), ("AngryBirds.app", "Angry Birds.app"),
                            expected_bundle=BUNDLE_ID)


def inspect_payload(payload: Path) -> dict:
    sys.path.insert(0, str(game_dir() / "tools"))
    from macho import MachO  # noqa: E402

    app = _app_dir(payload)
    exe = app / EXE_NAME
    if not exe.is_file():
        raise Refused(f"{app} has no {EXE_NAME} executable")
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    facts = MachO(exe.read_bytes())
    problems = []
    if facts.cryptid not in (0, None):
        problems.append(f"the executable is still App Store encrypted (cryptid {facts.cryptid}).")
    elif digest != EXE_SHA256:
        problems.append(
            f"AngryBirds executable sha256 {digest[:16]}... is not Angry Birds {WANT_VERSION} "
            f"({EXE_SHA256[:8]}...{EXE_SHA256[-8:]}, {EXE_SIZE:,} bytes); the recompiler "
            "reads addresses from exactly that build."
        )
    data = app / "data"
    if not data.is_dir():
        problems.append("data/ is missing in the .app (Lua, textures).")
    elif not any(data.glob("Level*.lua")):
        problems.append("data/ has no Level*.lua scripts.")
    return {"app": app, "exe": exe, "sha256": digest, "cryptid": facts.cryptid,
            "ok": not problems, "problems": problems}


def assert_payload(payload: Path) -> dict:
    facts = inspect_payload(payload)
    if not facts["ok"]:
        raise Refused(" ".join(facts["problems"]) + " Your files stay local; nothing was built.")
    return facts


def _jobs() -> str:
    return f"-j{max(1, min(8, os.cpu_count() or 2))}"


def _sources_digest() -> str:
    h = hashlib.sha256()
    for p in sorted(game_dir().rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            h.update(p.relative_to(game_dir()).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def stamp_value() -> str:
    from .templerun import _sdk_digest

    return f"recipe={RECIPE} exe={EXE_SHA256[:16]} src={_sources_digest()} sdk={_sdk_digest()}\n"


def _staged() -> bool:
    from . import const
    from .ports import data_ready

    return const.artifact("artifacts/extra/Executables/Angry Birds.hbapp").is_file() and data_ready(NAME)


def build(payload: Path, *, force: bool = False) -> dict:
    """Host-only build of Angry Birds.hbapp + Data/AngryBirds from the user's ipa."""
    from .ports import _run_make, _stage_hbapp, extra_data_dir
    from .templerun import ensure_venv

    t0 = time.time()
    print("nano7kit Angry Birds build (host only, no NAND, nothing written to the nano)")
    print("Recompiler: nano7kit games/angrybirds (first-party, olievans123-style static")
    print("recompile, ARM+VFP to C; nano image is Thumb+soft-float). Game: Rovio / Clickgamer,")
    print("from your own .ipa (stays local, never committed).")
    facts = assert_payload(payload)
    print(f"    payload   {facts['app']}")
    print(f"    AngryBirds sha256 {facts['sha256'][:16]}... (Angry Birds {WANT_VERSION}, decrypted)")
    stamp = stamp_value()
    if not force and _staged() and stamp_path().is_file() and stamp_path().read_text() == stamp:
        print("    build     already staged for this ipa (stamp match), skipping rebuild")
        return {"name": NAME, "built": False, "data": str(extra_data_dir(NAME))}
    py = ensure_venv()
    out = build_dir()
    recomp = out / "recomp"
    data = out / "data"
    for d in (recomp, data, out / "nano"):
        if d.exists():
            shutil.rmtree(d)
    out.mkdir(parents=True, exist_ok=True)
    print("==> recomp.py (ARM+VFP -> C)")
    _run_make([str(py), str(game_dir() / "tools" / "recomp.py"), str(facts["exe"]), str(recomp)],
              out, out / "recomp.log")
    print("==> convert_assets.py (skip wav/mp3, PNG->PVR 4444 + .dat patch, MENU/SPLASHES/TUTORIALS keep 512, rest 256)")
    _run_make([str(py), str(game_dir() / "tools" / "convert_assets.py"), str(facts["app"]), str(data)],
              out, out / "convert.log")
    print("==> arm-none-eabi-gcc libab_nano.a")
    _run_make(["make", _jobs(), "-C", str(game_dir() / "nano"), f"NANOAPPS={root() / 'nanoapps'}",
               f"RECOMP={recomp}", f"BUILD={out / 'nano'}"], out, out / "nano.log")
    app_dir = root() / "nanoapps" / "apps" / "angrybirds_rc"
    if app_dir.exists():
        shutil.rmtree(app_dir)
    shutil.copytree(game_dir() / "nano" / "app", app_dir)
    print("==> angrybirds.hbapp (NanoApps SDK, reloc)")
    _run_make(["make", "-C", str(app_dir), f"AB_LIB={out / 'nano' / 'libab_nano.a'}", f"PYTHON={py}"],
              app_dir, out / "app.log")
    hbapp = app_dir / "build" / "angrybirds.hbapp"
    if not hbapp.is_file():
        raise Refused(f"build finished without {hbapp}; see {out}/app.log")
    shutil.copyfile(recomp / "guest.img", data / "guest.img")
    _stage_hbapp(NAME, hbapp, data)
    stamp_path().write_text(stamp)
    size = hbapp.stat().st_size
    files = [p for p in data.rglob("*") if p.is_file()]
    nbytes = sum(p.stat().st_size for p in files)
    print(f"    staged    Angry Birds.hbapp {size:,} bytes, Data/AngryBirds {len(files)} files "
          f"{nbytes / 1e6:.1f} MB ({time.time() - t0:.0f} s)")
    return {"name": NAME, "built": True, "hbapp_size": size, "data_files": len(files),
            "data_bytes": nbytes, "data": str(extra_data_dir(NAME))}


def heap_fit(heap_free: int, largest: Optional[int] = None) -> dict:
    """Probe's heap free / largest -> what nano/fn_nano.c will do (same arithmetic)."""
    after_image = heap_free - IMAGE_BYTES
    big = largest if largest is not None else after_image
    out = {"after_image": after_image, "block": 0, "textures": TEXTURE_BYTES,
           "left_for_gl": 0, "budget": 0}
    block = BLOCK_MAX if big > BLOCK_MAX + SLOP else max(0, (big - SLOP) & ~0xFFFF)
    image_in_largest_ok = largest is None or largest - IMAGE_BYTES >= BLOCK_MIN + SLOP
    if big < BLOCK_MIN + SLOP or block < BLOCK_MIN or after_image < block + AFTER_MIN \
            or not image_in_largest_ok:
        out["verdict"] = "NO_FIT"
        out["image_in_largest_ok"] = image_in_largest_ok
        return out
    after = after_image - block
    left = after - SMALL_BYTES - TEXTURE_BYTES
    budget = max(BUDGET_MIN, min(BUDGET_MAX, left // BYTES_PER_TRIANGLE)) if left > 0 else BUDGET_MIN
    out.update(block=block, left_for_gl=left, budget=budget,
               image_in_largest_ok=image_in_largest_ok)
    out["verdict"] = "FITS" if budget >= TRIS_DRAWN_MAX else "FITS_REDUCED"
    return out


def heap_lines(heap_free: Optional[int], largest: Optional[int]) -> List[str]:
    mb = lambda n: f"{n / 1048576:.2f} MB"
    out = [
        f"    memory    image {mb(IMAGE_BYTES)}, one guest block {mb(BLOCK_MIN)} to {mb(BLOCK_MAX)} "
        f"(guest heap peak {mb(HEAP_PEAK)}),",
        f"              GL textures about {mb(TEXTURE_BYTES)} (pre-scaled at convert; no runtime shift), "
        f"{BYTES_PER_TRIANGLE} bytes a triangle ({TRIS_DRAWN_MAX} at the busiest)",
        f"              pre-flight: under {mb(BLOCK_MIN + SLOP)} largest or {mb(BLOCK_MAX + AFTER_MIN)} free "
        "after the image, it shows the red NOT ENOUGH MEMORY / CHECK LOG.TXT screen and never starts",
    ]
    if heap_free is None:
        out.append("    heap      pass Probe's numbers to check: --heap-free 6.4M --largest 4.0M")
        return out
    fit = heap_fit(heap_free, largest)
    out.append(f"    heap      Probe {mb(heap_free)} free" + (f", largest {mb(largest)}" if largest else "")
               + f"; after the image {mb(fit['after_image'])}")
    if fit["block"]:
        out.append(f"              block {mb(fit['block'])}, textures {mb(fit['textures'])}, "
                   f"{mb(fit['left_for_gl'])} for the driver -> budget {fit['budget']}")
    verdict = {
        "FITS": "FITS (every triangle of the busiest frame)",
        "FITS_REDUCED": f"FITS with a reduced triangle budget ({fit['budget']} of {TRIS_DRAWN_MAX})",
        "NO_FIT": "DOES NOT FIT: the app will show NOT ENOUGH MEMORY (pause music, reopen)",
    }[fit["verdict"]]
    out.append(f"    verdict   {verdict}")
    return out


def plan_lines(payload: Optional[Path], heap_free: Optional[int] = None, largest: Optional[int] = None,
               *, on_disk: Optional[List[str]] = None, slot_b: Optional[bool] = None,
               nogrow: Optional[bool] = None) -> List[str]:
    from .compat import which

    lines = ["nano7kit apps add AngryBirds --plan (read-only, nothing built or written)"]
    ok = True
    if payload is None:
        lines.append("    payload   missing: pass --payload /path/to/AngryBirds.ipa (Angry Birds 1.0)")
        ok = False
    else:
        try:
            facts = inspect_payload(Path(payload))
            lines.append(f"    payload   {BUNDLE_ID}, AngryBirds sha256 {facts['sha256'][:16]}...")
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
    lines.append("    build     recomp.py + convert_assets.py + arm-none-eabi-gcc, nothing fetched")
    lines.append(f"    writes    {FAT_EXE}, /Apps/Icons/Angry Birds.bin,")
    lines.append(f"              {FAT_DATA}/ (guest.img + converted data/; wav/mp3 skipped)")
    if slot_b is not None:
        lines.append("    slot      " + ("B active, as for Mario and Temple Run" if slot_b
                                         else "slot B is not active: apps add switches to slot B (large GL)"))
    if on_disk is not None:
        after = list(on_disk) if NAME in on_disk else list(on_disk) + [NAME]
        cap = 16 if nogrow is not False else 8
        lines.append(f"    pack      {len(on_disk)} now, with Angry Birds {len(after)} of {cap}")
        if len(after) > cap:
            ok = False
            lines.append(f"    REFUSED   over the {cap}-app cap")
    lines.extend(heap_lines(heap_free, largest))
    if heap_free is not None and heap_fit(heap_free, largest)["verdict"] == "NO_FIT":
        ok = False
    lines.append("    undo      ./start nabase remove 'Angry Birds'")
    lines.append("PLAN_OK" if ok else "PLAN_REFUSED")
    return lines
