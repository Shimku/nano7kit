"""Angry Birds 1.0.0 Android .so on the nano: first-party, from the user's own APK.

games/angrybirds loads Rovio's armeabi-v7a libangrybirds.so (JNI Ka3D) instead of
recompiling the iOS binary. Nothing from Rovio is in git: the converted .o, .hbapp
and downscaled data/ files are written to gitignored build/angrybirds_rc and
artifacts/extra only.

Host only. nabase add copies the staged files onto the mounted iPod volume afterwards (slot B).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import zipfile
from pathlib import Path
from typing import List, Optional

from .compat import Refused, root

NAME = "Angry Birds"
BUNDLE_ID = "com.rovio.angrybirds"
WANT_VERSION = "1.0.0"
SO_NAME = "libangrybirds.so"
SO_SHA256 = "6d906957c050084fd6b9c1386e6b2a10c819a18461422f9de2df8ea6c7704c95"
SO_SIZE = 1_670_325
APK_SHA256 = "c80ed55b5a7e594653402217b61569049300358d2c2f6c4515c867608104c477"
CMD = "./start nabase add 'Angry Birds' --payload /path/to/angrybirds-android-1.0.0.apk"
FAT_EXE = "/Apps/Executables/Angry Birds.hbapp"
FAT_DATA = "/Apps/Data/AngryBirds"
RECIPE = 17  # pool base 16-up; 16-round blocks; host AB_POOL_OFF=4 device-like

# Memory model. IMAGE_BYTES is the loaded .hbapp span (runtime + linked .so).
# The v7a .so image is about 1.32 MB; runtime/GL/JNI stay ARM+VFP softfp.
IMAGE_BYTES = 1_800_000 + 4096
BLOCK_MAX = 0x300000
BLOCK_MIN = 0x230000
GL_KEEP = 0x100000
AFTER_MIN = 0x100000
SLOP = 0x10000
HEAP_PEAK = 2_800_000
SMALL_BYTES = 40_000
TEXTURE_BYTES = 2_105_344
BYTES_PER_TRIANGLE = 3000
BUDGET_MIN, BUDGET_MAX = 300, 1200
TRIS_DRAWN_MAX = 800
ZLIB_SHA256 = "9a93b2b7dfdac77ceba5a558a580e74667dd6fede4585b91eefb60f03b72df23"


def build_dir() -> Path:
    return root() / "build" / "angrybirds_rc"


def _recipe_python() -> Path:
    """Interpreter for so_to_obj, convert_assets, and mkrelocapp (pyelftools)."""
    import subprocess

    py = Path(sys.executable)
    probe = subprocess.run([str(py), "-c", "import elftools"], capture_output=True)
    if probe.returncode == 0:
        return py
    subprocess.check_call(
        [str(py), "-m", "pip", "install", "--user", "--quiet", "--disable-pip-version-check",
         "pyelftools==0.32"]
    )
    return py


def stamp_path() -> Path:
    return build_dir() / "stamp.txt"


def game_dir() -> Path:
    return root() / "games" / "angrybirds"


def _unpack_payload(payload: Path) -> Path:
    payload = Path(payload).expanduser().resolve()
    if payload.is_dir():
        return payload
    if not payload.is_file():
        raise Refused(f"payload missing: {payload}")
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    dest = build_dir() / "payload"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    if zipfile.is_zipfile(payload):
        with zipfile.ZipFile(payload) as z:
            z.extractall(dest)
        # zip of an apk, or the apk itself
        inner = list(dest.rglob("libangrybirds.so"))
        if inner:
            # unpacked apk tree
            rootish = inner[0]
            for _ in range(3):
                if (rootish / "lib").is_dir() or (rootish / "assets").is_dir():
                    return rootish
                rootish = rootish.parent
            return inner[0].parents[2] if len(inner[0].parents) >= 3 else dest
        apks = list(dest.rglob("*.apk"))
        if len(apks) == 1:
            apk_dir = dest / "apk"
            apk_dir.mkdir()
            with zipfile.ZipFile(apks[0]) as z:
                z.extractall(apk_dir)
            return apk_dir
    raise Refused(
        f"{payload} is not Angry Birds Android 1.0.0 "
        f"(expected zip/apk sha256 {APK_SHA256[:8]}..., got {digest[:8]}...)."
    )


def inspect_payload(payload: Path) -> dict:
    app = _unpack_payload(payload)
    so = app / "lib" / "armeabi-v7a" / SO_NAME
    if not so.is_file():
        so = app / "lib" / "armeabi" / SO_NAME
    problems = []
    digest = ""
    if not so.is_file():
        problems.append(f"{app} has no {SO_NAME} (expected lib/armeabi-v7a/).")
    else:
        digest = hashlib.sha256(so.read_bytes()).hexdigest()
        if digest != SO_SHA256 and so.parent.name != "armeabi-v7a":
            problems.append(
                f"{SO_NAME} sha256 {digest[:16]}... is not the armeabi-v7a Angry Birds "
                f"{WANT_VERSION} build ({SO_SHA256[:8]}...{SO_SHA256[-8:]}, {SO_SIZE:,} bytes)."
            )
        elif digest != SO_SHA256:
            problems.append(
                f"{SO_NAME} sha256 {digest[:16]}... is not Angry Birds Android {WANT_VERSION} "
                f"({SO_SHA256[:8]}...{SO_SHA256[-8:]}, {SO_SIZE:,} bytes)."
            )
    data = None
    for cand in (app / "assets" / "data", app / "data"):
        if cand.is_dir() and any(cand.glob("Level*.lua")):
            data = cand
            break
    if data is None:
        problems.append("assets/data/ is missing (Lua, textures).")
    src = Path(payload).expanduser().resolve()
    apk_digest = hashlib.sha256(src.read_bytes()).hexdigest() if src.is_file() else ""
    if src.is_file() and apk_digest != APK_SHA256:
        problems.append(
            f"payload sha256 {apk_digest[:16]}... is not Angry Birds Android {WANT_VERSION} "
            f"({APK_SHA256[:8]}...{APK_SHA256[-8:]})."
        )
    return {"app": app, "so": so, "sha256": digest, "apk_sha256": apk_digest,
            "data": data, "ok": not problems, "problems": problems}


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

    return f"recipe={RECIPE} so={SO_SHA256[:16]} src={_sources_digest()} sdk={_sdk_digest()}\n"


def _staged() -> bool:
    from . import const
    from .ports import data_ready

    return const.artifact("artifacts/extra/Executables/Angry Birds.hbapp").is_file() and data_ready(NAME)


def _ensure_zlib() -> Path:
    from .ports import _run_make

    zdir = root() / "build" / "zlib-1.3.1"
    if (zdir / "inflate.c").is_file():
        return zdir
    zdir.parent.mkdir(parents=True, exist_ok=True)
    tgz = Path("/tmp/zlib-1.3.1.tar.gz")
    if not tgz.is_file():
        import urllib.request
        urllib.request.urlretrieve(
            "https://github.com/madler/zlib/releases/download/v1.3.1/zlib-1.3.1.tar.gz", tgz)
    digest = hashlib.sha256(tgz.read_bytes()).hexdigest()
    if digest != ZLIB_SHA256:
        raise Refused(f"zlib tarball hash {digest} is not {ZLIB_SHA256}")
    _run_make(["tar", "-xzf", str(tgz), "-C", str(zdir.parent)], zdir.parent, build_dir() / "zlib.log")
    return zdir


def build(payload: Path, *, force: bool = False) -> dict:
    """Host-only build of Angry Birds.hbapp + Data/AngryBirds from the user's APK."""
    import time as time_mod

    from .ports import _run_make, _stage_hbapp, extra_data_dir

    t0 = time_mod.time()
    print("nano7kit Angry Birds build (host only, no NAND, nothing written to the nano)")
    print("Runtime: nano7kit games/angrybirds loads Rovio's Android 1.0.0 libangrybirds.so")
    print("(JNI Ka3D). Game files stay local, never committed.")
    facts = assert_payload(payload)
    print(f"    payload   {facts['app']}")
    print(f"    so        sha256 {facts['sha256'][:16]}... (Angry Birds Android {WANT_VERSION} v7a)")
    stamp = stamp_value()
    if not force and _staged() and stamp_path().is_file() and stamp_path().read_text() == stamp:
        print("    build     already staged for this apk (stamp match), skipping rebuild")
        return {"name": NAME, "built": False, "data": str(extra_data_dir(NAME))}
    py = _recipe_python()
    out = build_dir()
    data = out / "data"
    soobj = out / "angrybirds_so.o"
    for d in (data, out / "nano"):
        if d.exists():
            shutil.rmtree(d)
    out.mkdir(parents=True, exist_ok=True)
    zdir = _ensure_zlib()
    print("==> so_to_obj.py (ET_DYN -> ET_REL, gl* remapped to ab_gl*)")
    _run_make([str(py), str(game_dir() / "tools" / "so_to_obj.py"), str(facts["so"]), str(soobj)],
              out, out / "so_to_obj.log")
    print("==> convert_assets.py (skip wav/mp3, INGAME and UI keep 512)")
    _run_make([str(py), str(game_dir() / "tools" / "convert_assets.py"), str(facts["app"]), str(data)],
              out, out / "convert.log")
    print("==> arm-none-eabi-gcc libab_nano.a")
    _run_make(["make", _jobs(), "-C", str(game_dir() / "nano"), f"NANOAPPS={root() / 'nanoapps'}",
               f"SOOBJ={soobj}", f"ZLIB={zdir}", f"BUILD={out / 'nano'}"], out, out / "nano.log")
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
    _stage_hbapp(NAME, hbapp, data)
    stamp_path().write_text(stamp)
    size = hbapp.stat().st_size
    files = [p for p in data.rglob("*") if p.is_file()]
    nbytes = sum(p.stat().st_size for p in files)
    print(f"    staged    Angry Birds.hbapp {size:,} bytes, Data/AngryBirds {len(files)} files "
          f"{nbytes / 1e6:.1f} MB ({time_mod.time() - t0:.0f} s)")
    return {"name": NAME, "built": True, "hbapp_size": size, "data_files": len(files),
            "data_bytes": nbytes, "data": str(extra_data_dir(NAME))}


def heap_fit(heap_free: int, largest: Optional[int] = None) -> dict:
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
        f"    memory    image {mb(IMAGE_BYTES)}, one malloc pool {mb(BLOCK_MIN)} to {mb(BLOCK_MAX)} "
        f"(engine heap peak {mb(HEAP_PEAK)}),",
        f"              GL textures about {mb(TEXTURE_BYTES)} (pre-scaled at convert; INGAME keep 512), "
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
        out.append(f"              block {mb(fit['block'])}, textures {mb(fit['textures'])}, "
                   f"{mb(fit['left_for_gl'])} for the driver -> budget {fit['budget']}")
    verdict = {
        "FITS": "FITS (every triangle of the busiest frame)",
        "FITS_REDUCED": f"FITS with a reduced triangle budget ({fit['budget']} of {TRIS_DRAWN_MAX})",
        "NO_FIT": "DOES NOT FIT: the app will show NOT ENOUGH MEMORY",
    }[fit["verdict"]]
    out.append(f"    verdict   {verdict}")
    return out


def plan_lines(payload: Optional[Path], heap_free: Optional[int] = None, largest: Optional[int] = None,
               *, on_disk: Optional[List[str]] = None, slot_b: Optional[bool] = None,
               nogrow: Optional[bool] = None) -> List[str]:
    from .compat import which

    lines = ["nano7kit nabase add AngryBirds (read-only plan, nothing built or written)"]
    ok = True
    if payload is None:
        lines.append("    payload   missing: pass --payload /path/to/angrybirds-android-1.0.0.apk")
        ok = False
    else:
        try:
            facts = inspect_payload(Path(payload))
            lines.append(f"    payload   {BUNDLE_ID}, {SO_NAME} sha256 {facts['sha256'][:16]}...")
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
    lines.append("    build     so_to_obj.py + convert_assets.py + arm-none-eabi-gcc; zlib fetched at build")
    lines.append(f"    writes    {FAT_EXE}, /Apps/Icons/Angry Birds.bin,")
    lines.append(f"              {FAT_DATA}/ (converted data/; wav/mp3 skipped). The .so is not copied.")
    if slot_b is not None:
        lines.append("    slot      " + ("B active, as for Mario and Temple Run" if slot_b
                                         else "slot B is not active: nabase add switches to slot B (large GL)"))
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
