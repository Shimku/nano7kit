"""Gravity Defied for the nano: rgimad/gravity_defied_cpp (GPL-2.0) with the user's own J2ME files.

games/gravity_defied holds nano7kit's device layer (GPL-2.0 like the engine). The engine is
fetched at a pinned commit. Game data (levels.mrg, sprites) comes from the user's payload
at install time and is never committed.
Host only. nabase add copies the staged files in Disk Mode afterwards.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import struct
import subprocess
import sys
import time
import zipfile
import zlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .compat import Refused, root, which

NAME = "Gravity Defied"
CMD = "./start nabase add 'Gravity Defied' --payload /path/to/GDTR.jar"
FAT_EXE = f"/Apps/Executables/{NAME}.hbapp"
FAT_DATA = "/Apps/Data/GravityDefied"
ENGINE_URL = "https://github.com/rgimad/gravity_defied_cpp.git"
ENGINE_COMMIT = "91bd283959b96a7ea07e1c4c0040460334c85458"
DLMALLOC = (
    "https://gee.cs.oswego.edu/pub/misc/malloc-2.8.6.c",
    "103602c3fcbe200d5e257cdd7353d84bcc033d887bea3b245321319bf5401f47",
)
SPRITE_STEMS = (
    "splash", "logo", "helmet", "sprites", "fender", "engine",
    "blueleg", "bluearm", "bluebody", "raster",
)
SPRITE_SIZES = {
    "splash": (80, 50),
    "logo": (61, 46),
    "helmet": (48, 48),
    "sprites": (49, 40),
    "fender": (108, 108),
    "engine": (120, 120),
    "blueleg": (72, 36),
    "bluearm": (48, 24),
    "bluebody": (60, 30),
    "raster": (64, 64),
}

# Same numbers as games/gravity_defied/nano/gd_nano.c
IMAGE_BYTES = 172_032
BLOCK_MAX = 0x80000
BLOCK_MIN = 0x60000
AFTER_MIN = 0x8000
SLOP = 0x4000
ARENA_NEED = BLOCK_MIN

LEVEL_NAMES = ("Easy", "Medium", "Pro")
TRACK_NAMES = (
    ("Intro", "Shorty", "Slope", "Crackle", "Knolls", "Deep", "Cliff", "Hole", "Original", "Savvy"),
    ("Indoor", "Spikeholes", "Downhill", "Hillclimb", "Modesty", "Blocks", "Bumps", "Spikehops", "Pillar", "Trenches"),
    ("Floorboards", "Tip top", "Dantes Peak", "Abrupt", "Undertaker", "Liberty", "Intense", "100%", "Training day", "Trial again"),
)


def build_dir() -> Path:
    return root() / "build" / "gravity_defied"


def stamp_path() -> Path:
    return build_dir() / "stamp.txt"


def game_dir() -> Path:
    return root() / "games" / "gravity_defied"


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def write_png_rgba(path: Path, w: int, h: int, rgba: bytes) -> None:
    raw = b""
    stride = w * 4
    for y in range(h):
        raw += b"\x00" + rgba[y * stride : (y + 1) * stride]
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw, 9))
        + _png_chunk(b"IEND", b"")
    )


def decode_png(data: bytes) -> Tuple[int, int, bytes]:
    """Return (w, h, RGBA bytes). 8-bit grey/rgb/palette/rgba, no Adam7."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos = 8
    w = h = bit = ctype = 0
    idat = b""
    plte = b""
    trns = b""
    while pos + 12 <= len(data):
        ln = struct.unpack_from(">I", data, pos)[0]
        tag = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + ln]
        pos += 12 + ln
        if tag == b"IHDR":
            w, h, bit, ctype, comp, filt, inter = struct.unpack(">IIBBBBB", chunk)
            if bit != 8 or inter != 0 or comp != 0 or filt != 0:
                if bit not in (1, 2, 4, 8) or inter != 0:
                    raise ValueError("unsupported PNG")
        elif tag == b"PLTE":
            plte = chunk
        elif tag == b"tRNS":
            trns = chunk
        elif tag == b"IDAT":
            idat += chunk
        elif tag == b"IEND":
            break
    raw = zlib.decompress(idat)
    if ctype == 6:
        bpp = 4
    elif ctype == 2:
        bpp = 3
    elif ctype == 4:
        bpp = 2
    elif ctype in (0, 3):
        bpp = 1 if bit == 8 else 1
    else:
        raise ValueError("unsupported PNG colour type")
    if bit < 8 and ctype in (0, 3):
        samples = w
        stride = (w * bit + 7) // 8
    else:
        samples = w * bpp
        stride = samples
    rows = []
    off = 0
    prev = bytearray(stride)
    for _y in range(h):
        if off >= len(raw):
            raise ValueError("short PNG")
        ft = raw[off]
        off += 1
        row = bytearray(raw[off : off + stride])
        off += stride
        if len(row) < stride:
            row.extend(b"\x00" * (stride - len(row)))
        if ft == 1:
            for i in range(len(row)):
                left = row[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + left) & 255
        elif ft == 2:
            for i in range(len(row)):
                row[i] = (row[i] + prev[i]) & 255
        elif ft == 3:
            for i in range(len(row)):
                left = row[i - bpp] if i >= bpp else 0
                up = prev[i]
                row[i] = (row[i] + ((left + up) // 2)) & 255
        elif ft == 4:
            def paeth(a, b, c):
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                if pa <= pb and pa <= pc:
                    return a
                if pb <= pc:
                    return b
                return c
            for i in range(len(row)):
                a = row[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + paeth(a, b, c)) & 255
        rows.append(bytes(row))
        prev = row
    out = bytearray(w * h * 4)

    def put(i, r, g, b, a=255):
        out[i * 4 : i * 4 + 4] = bytes((r, g, b, a))

    pix = 0
    for y, row in enumerate(rows):
        if bit < 8 and ctype in (0, 3):
            bits = []
            for byte in row:
                for s in range(8 - bit, -1, -bit):
                    bits.append((byte >> s) & ((1 << bit) - 1))
            bits = bits[:w]
            for v in bits:
                if ctype == 0:
                    g = v * (255 // ((1 << bit) - 1))
                    put(pix, g, g, g)
                else:
                    offp = v * 3
                    r = plte[offp] if offp < len(plte) else 0
                    g = plte[offp + 1] if offp + 1 < len(plte) else 0
                    b = plte[offp + 2] if offp + 2 < len(plte) else 0
                    a = 255
                    if trns and v < len(trns):
                        a = trns[v]
                    put(pix, r, g, b, a)
                pix += 1
            continue
        x = 0
        while x + bpp <= len(row) and pix < w * h:
            if ctype == 6:
                put(pix, row[x], row[x + 1], row[x + 2], row[x + 3])
            elif ctype == 2:
                put(pix, row[x], row[x + 1], row[x + 2])
            elif ctype == 4:
                put(pix, row[x], row[x], row[x], row[x + 1])
            elif ctype == 0:
                put(pix, row[x], row[x], row[x])
            elif ctype == 3:
                v = row[x]
                offp = v * 3
                r = plte[offp] if offp < len(plte) else 0
                g = plte[offp + 1] if offp + 1 < len(plte) else 0
                b = plte[offp + 2] if offp + 2 < len(plte) else 0
                a = 255
                if trns and v < len(trns):
                    a = trns[v]
                put(pix, r, g, b, a)
            pix += 1
            x += bpp
    return w, h, bytes(out)


def rgba_to_raw(w: int, h: int, rgba: bytes) -> bytes:
    buf = bytearray(4 + w * h * 4)
    buf[0] = w & 255
    buf[1] = (w >> 8) & 255
    buf[2] = h & 255
    buf[3] = (h >> 8) & 255
    buf[4:] = rgba
    return bytes(buf)


def parse_mrg_header(data: bytes) -> Dict:
    if len(data) < 16:
        return {"ok": False, "reason": "levels.mrg is too small"}
    off = 0
    leagues = []
    try:
        for _lg in range(3):
            if off + 4 > len(data):
                return {"ok": False, "reason": "levels.mrg header is truncated"}
            n = struct.unpack_from(">i", data, off)[0]
            off += 4
            if n < 1 or n > 20:
                return {"ok": False, "reason": f"levels.mrg league count {n} is not a classic set"}
            tracks = []
            for _i in range(n):
                if off + 5 > len(data):
                    return {"ok": False, "reason": "levels.mrg track list is truncated"}
                pos = struct.unpack_from(">i", data, off)[0]
                off += 4
                end = data.find(b"\x00", off)
                if end < 0 or end - off > 40:
                    return {"ok": False, "reason": "levels.mrg track name is not null-terminated"}
                name = data[off:end].decode("latin1", "replace").replace("_", " ")
                off = end + 1
                tracks.append({"offset": pos, "name": name})
            leagues.append(tracks)
    except struct.error:
        return {"ok": False, "reason": "levels.mrg header could not be read"}
    return {"ok": True, "leagues": leagues, "header_end": off, "size": len(data)}


def build_test_mrg() -> bytes:
    """A classic-shaped 3 x 10 levels.mrg with short flat tracks (not the original courses)."""
    tracks = []
    names = []
    for lg in range(3):
        ln = []
        lt = []
        for t in range(10):
            ln.append(f"L{lg}T{t}")
            # byte 51 (not the 20-char name record), start/finish in <<13, points in pixels
            body = bytearray()
            body.append(51)
            sx, sy = (8 + t) << 13, 78 << 13
            fx, fy = (520 + t * 8) << 13, 78 << 13
            body += struct.pack(">iiii", sx, sy, fx, fy)
            pts = [(-16, 80)]
            x = -16
            for i in range(24):
                x += 28
                pts.append((x, 80 + ((i + lg + t) % 3) - 1))
            body += struct.pack(">hii", len(pts), pts[0][0], pts[0][1])
            ox, oy = pts[0]
            for px, py in pts[1:]:
                dx, dy = px - ox, py - oy
                if abs(dx) > 127 or abs(dy) > 127:
                    body.append(0xFF)
                    body += struct.pack(">ii", px, py)
                else:
                    body.append(dx & 255)
                    body.append(dy & 255)
                ox, oy = px, py
            lt.append(bytes(body))
        names.append(ln)
        tracks.append(lt)
    header = bytearray()
    blobs = bytearray()
    # placeholder; filled after we know header size
    hdr = bytearray()
    # compute header size
    hsz = 0
    for lg in range(3):
        hsz += 4
        for name in names[lg]:
            hsz += 4 + len(name) + 1
    off = hsz
    for lg in range(3):
        hdr += struct.pack(">i", 10)
        for i, name in enumerate(names[lg]):
            hdr += struct.pack(">i", off)
            hdr += name.encode("ascii") + b"\x00"
            off += len(tracks[lg][i])
    out = bytes(hdr)
    for lg in range(3):
        for blob in tracks[lg]:
            out += blob
    return out


def _find_in_zip(zf: zipfile.ZipFile, want: str) -> Optional[zipfile.ZipInfo]:
    want_l = want.lower().replace("\\", "/")
    for info in zf.infolist():
        name = info.filename.replace("\\", "/").lower()
        if name.rsplit("/", 1)[-1] == want_l:
            return info
    return None


def inspect_payload(payload: Path) -> dict:
    src = Path(payload).expanduser().resolve()
    problems = []
    mrg = b""
    sprites: Dict[str, bytes] = {}
    kind = ""
    if not src.exists():
        return {"ok": False, "problems": [f"not found: {src}"], "path": src}

    def take_mrg(data: bytes, where: str) -> None:
        nonlocal mrg
        facts = parse_mrg_header(data)
        if not facts["ok"]:
            problems.append(facts["reason"] + f" ({where})")
            return
        mrg = data

    def take_png(stem: str, data: bytes) -> None:
        try:
            decode_png(data)
        except Exception as exc:
            problems.append(f"{stem}.png is not a readable PNG ({exc})")
            return
        sprites[stem] = data

    if src.is_dir():
        kind = "folder"
        found = list(src.rglob("levels.mrg")) + list(src.rglob("*.mrg"))
        if not found:
            problems.append(f"{src} has no levels.mrg (pass a J2ME .jar, a folder, or the bare file).")
        else:
            take_mrg(found[0].read_bytes(), str(found[0].name))
        for stem in SPRITE_STEMS:
            for cand in src.rglob(stem + ".png"):
                take_png(stem, cand.read_bytes())
                break
    elif src.is_file() and src.suffix.lower() in {".jar", ".zip"}:
        kind = "jar"
        try:
            with zipfile.ZipFile(src) as zf:
                info = _find_in_zip(zf, "levels.mrg")
                if info is None:
                    problems.append(
                        f"{src.name} is a zip but has no levels.mrg. "
                        "Use the 2004 Codebrew J2ME Gravity Defied .jar (often GDTR.jar)."
                    )
                else:
                    take_mrg(zf.read(info), info.filename)
                for stem in SPRITE_STEMS:
                    info = _find_in_zip(zf, stem + ".png")
                    if info is not None:
                        take_png(stem, zf.read(info))
        except zipfile.BadZipFile:
            problems.append(f"{src.name} is not a zip/jar.")
    elif src.is_file() and (src.suffix.lower() == ".mrg" or src.name.lower() == "levels.mrg"):
        kind = "mrg"
        take_mrg(src.read_bytes(), src.name)
    else:
        problems.append(
            f"{src} is not a J2ME .jar, a folder, or levels.mrg. "
            "Pass the 2004 Codebrew Gravity Defied jar (often GDTR.jar)."
        )
    ok = not problems and bool(mrg)
    return {
        "ok": ok,
        "problems": problems,
        "path": src,
        "kind": kind,
        "mrg": mrg,
        "sprites": sprites,
        "header": parse_mrg_header(mrg) if mrg else {},
    }


def assert_payload(payload: Path) -> dict:
    facts = inspect_payload(payload)
    if not facts["ok"]:
        raise Refused(" ".join(facts["problems"]) + " Your files stay local; nothing was built.")
    return facts


def fetch_dlmalloc(dest_dir: Path) -> Path:
    url, want = DLMALLOC
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / "malloc-2.8.6.c"
    if not (out.is_file() and _sha256(out) == want):
        print(f"==> fetch {url}")
        import urllib.request
        tmp = out.with_suffix(".c.part")
        with urllib.request.urlopen(url, timeout=120) as r, tmp.open("wb") as fh:
            shutil.copyfileobj(r, fh)
        got = _sha256(tmp)
        if got != want:
            tmp.unlink()
            raise Refused(f"{url} sha256 {got[:16]}... is not the pinned {want[:16]}...; nothing was built.")
        tmp.replace(out)
    return out


def fetch_engine(dest: Path) -> Path:
    from .ports import _run_make

    if not which("git"):
        raise Refused("git is missing (xcode-select --install)")
    log = build_dir() / "engine-fetch.log"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not (dest / ".git").is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        _run_make(["git", "init", "-q"], dest, log)
        _run_make(["git", "remote", "add", "origin", ENGINE_URL], dest, log)
    have = subprocess.run(
        ["git", "cat-file", "-e", ENGINE_COMMIT + "^{commit}"],
        cwd=str(dest), capture_output=True,
    ).returncode == 0
    if not have:
        print(f"==> fetch gravity_defied_cpp {ENGINE_COMMIT[:7]} ({ENGINE_URL})")
        _run_make(["git", "fetch", "-q", "--depth", "1", "origin", ENGINE_COMMIT], dest, log)
    _run_make(["git", "checkout", "-q", "-f", ENGINE_COMMIT], dest, log)
    _run_make(["git", "clean", "-qfdx"], dest, log)
    overlay = game_dir() / "engine" / "overlay"
    for p in overlay.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(overlay)
        target = dest / "src" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, target)
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "gd_patch_engine", game_dir() / "engine" / "patch_engine.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    mod.apply(dest)
    shutil.copyfile(fetch_dlmalloc(build_dir() / "dl"), dest / "src" / "nano" / "malloc-2.8.6.c")
    shutil.copyfile(game_dir() / "engine" / "LICENSE-gravity_defied_cpp", dest / "LICENSE.md")
    shutil.copyfile(game_dir() / "engine" / "build_arm.py", dest / "build_arm.py")
    return dest


def _sources_digest() -> str:
    h = hashlib.sha256()
    for p in sorted(game_dir().rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            h.update(p.relative_to(game_dir()).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def stamp_value() -> str:
    from .templerun import _sdk_digest

    return f"engine={ENGINE_COMMIT[:12]} src={_sources_digest()} sdk={_sdk_digest()}\n"


def _staged() -> bool:
    from . import const
    from .ports import data_ready

    return const.artifact(f"artifacts/extra/Executables/{NAME}.hbapp").is_file() and data_ready(NAME)


def stage_payload_data(facts: dict, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    assets = dest / "assets"
    assets.mkdir(exist_ok=True)
    (dest / "levels.mrg").write_bytes(facts["mrg"])
    for stem in SPRITE_STEMS:
        png = facts["sprites"].get(stem)
        if png:
            w, h, rgba = decode_png(png)
        else:
            w, h = SPRITE_SIZES[stem]
            rgba = bytes((40, 40, 48, 255)) * (w * h)
            if stem == "raster":
                rgba = bytearray(w * h * 4)
                for y in range(h):
                    for x in range(w):
                        on = ((x // 8) + (y // 8)) & 1
                        c = 20 if on else 48
                        i = (y * w + x) * 4
                        rgba[i:i + 4] = bytes((c, c, c + 8, 255))
                rgba = bytes(rgba)
        (assets / f"{stem}.raw").write_bytes(rgba_to_raw(w, h, rgba))
    return dest


def _compile_host(engine: Path, out: Path) -> Path:
    srcs = []
    skip = {
        "src/main.cpp",
        "src/utils/EmbedFileStream.cpp",
        "src/utils/FileStream.cpp",
        "src/nano/malloc-2.8.6.c",
    }
    for pat in ("src/*.cpp", "src/lcdui/*.cpp", "src/rms/*.cpp", "src/utils/*.cpp",
                "src/nano/*.cpp", "src/nano/*.c"):
        for s in sorted(engine.glob(pat)):
            rel = s.relative_to(engine).as_posix()
            if rel in skip or "/tests/" in rel:
                continue
            srcs.append(s)
    notice = game_dir() / "nano" / "notice.c"
    host_main = game_dir() / "engine" / "host" / "main.c"
    cc = which("g++") or which("c++") or which("clang++")
    gcc = which("gcc") or which("cc") or which("clang")
    if not cc or not gcc:
        raise Refused("a host C++ compiler is missing (c++ / g++)")
    obj = out / "host-obj"
    obj.mkdir(parents=True, exist_ok=True)
    inc = ["-I" + str(engine / "src"), "-I" + str(engine / "src" / "nano"),
           "-I" + str(game_dir() / "nano")]
    cflags = ["-O2", "-g0", "-fno-exceptions", "-DGD_HOST", "-ffp-contract=off"]
    cxxflags = cflags + ["-fno-rtti", "-std=gnu++17"]
    objs = []
    log = out / "host-compile.log"
    log.write_text("")
    for s in srcs + [host_main, notice]:
        o = obj / (s.name + ".o")
        stale = (not o.is_file()) or o.stat().st_mtime < s.stat().st_mtime
        if stale:
            if s.suffix == ".c":
                cmd = [gcc, "-c", str(s), "-o", str(o), "-std=gnu11"] + inc + cflags
            else:
                cmd = [cc, "-c", str(s), "-o", str(o)] + inc + cxxflags
            with log.open("a", encoding="utf-8") as fh:
                rc = subprocess.call(cmd, cwd=str(engine), stdout=fh, stderr=subprocess.STDOUT)
            if rc != 0:
                raise Refused(f"{' '.join(cmd)} failed; see {log}\n{log.read_text()[-2000:]}")
        objs.append(o)
    bin_path = out / "gd_host"
    link = [cc, "-o", str(bin_path)] + [str(o) for o in objs] + ["-lm"]
    with log.open("a", encoding="utf-8") as fh:
        rc = subprocess.call(link, cwd=str(engine), stdout=fh, stderr=subprocess.STDOUT)
    if rc != 0:
        raise Refused(f"{' '.join(link)} failed; see {log}\n{log.read_text()[-2000:]}")
    return bin_path


def build(payload: Path, *, force: bool = False) -> dict:
    from .ports import _run_make, _stage_hbapp, extra_data_dir

    t0 = time.time()
    print("nano7kit Gravity Defied build (host only, nothing written to the nano)")
    print("Engine: rgimad/gravity_defied_cpp (GPL-2.0), fetched at a pinned commit.")
    print("Game: your own J2ME .jar (levels.mrg + sprites stay local, never committed).")
    facts = assert_payload(payload)
    hdr = facts["header"]
    ntracks = sum(len(lg) for lg in hdr.get("leagues", []))
    print(f"    payload   {facts['path']} ({facts['kind']}, {ntracks} tracks, "
          f"{len(facts['sprites'])} sprite PNGs)")
    if facts["path"].is_file():
        stamp = stamp_value() + f"payload={_sha256(facts['path'])[:16]}\n"
    else:
        stamp = stamp_value() + f"payload={facts['path']}\n"
    if not force and _staged() and stamp_path().is_file() and stamp_path().read_text() == stamp:
        print("    build     already staged for these files (stamp match), skipping rebuild")
        return {"name": NAME, "built": False, "data": str(extra_data_dir(NAME))}
    missing = [t for t in ("make", "git") if not which(t)]
    if missing:
        raise Refused(", ".join(missing) + " is missing")
    if not which("arm-none-eabi-gcc"):
        raise Refused("arm-none-eabi-gcc is missing (./start deps)")
    from .sdkapp import _python_with_elftools
    py = Path(_python_with_elftools())
    if subprocess.call([str(py), "-c", "import elftools"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL) != 0:
        py = Path(sys.executable)
        if subprocess.call([str(py), "-c", "import elftools"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL) != 0:
            raise Refused("pyelftools is missing (python3 -m pip install --user pyelftools, or ./start deps)")
    out = build_dir()
    out.mkdir(parents=True, exist_ok=True)
    engine = fetch_engine(out / "src" / "gd")
    data = out / "data"
    if data.exists():
        shutil.rmtree(data)
    stage_payload_data(facts, data)
    print("==> arm-none-eabi-g++ libgd_nano.a (engine in ARM state)")
    _run_make(
        ["env", "NANO7_TC=", f"NANO7KIT={root()}", str(py), str(engine / "build_arm.py")],
        engine, out / "engine.log",
    )
    lib = engine / "obj_arm" / "libgd_nano.a"
    if not lib.is_file():
        raise Refused(f"engine build finished without {lib}; see {out}/engine.log")
    app_dir = root() / "nanoapps" / "apps" / "gravity_defied"
    if app_dir.exists():
        shutil.rmtree(app_dir)
    app_dir.mkdir(parents=True)
    shutil.copyfile(game_dir() / "nano" / "app" / "Makefile", app_dir / "Makefile")
    shutil.copyfile(game_dir() / "nano" / "app" / "Info.plist", app_dir / "Info.plist")
    shutil.copyfile(game_dir() / "nano" / "gd_nano.c", app_dir / "gd_nano.c")
    shutil.copyfile(game_dir() / "nano" / "gd_sys.c", app_dir / "gd_sys.c")
    shutil.copyfile(game_dir() / "nano" / "notice.c", app_dir / "notice.c")
    shutil.copyfile(game_dir() / "nano" / "notice.h", app_dir / "notice.h")
    shutil.copyfile(engine / "src" / "nano" / "gd_plat.c", app_dir / "gd_plat.c")
    shutil.copyfile(engine / "src" / "nano" / "gd_font8.h", app_dir / "gd_font8.h")
    shutil.copyfile(engine / "src" / "gd_config.h", app_dir / "gd_config.h")
    shutil.copyfile(engine / "src" / "nano" / "gdalloc.h", app_dir / "gdalloc.h")
    shutil.copyfile(engine / "src" / "nano" / "gd_engine.h", app_dir / "gd_engine.h")
    print("==> Gravity Defied.hbapp (NanoApps SDK, reloc, RAW_SURFACE)")
    _run_make(
        ["make", "-C", str(app_dir), f"GD_LIB={lib}", f"PYTHON={py}",
         f"ENGINE_SRC={engine / 'src'}"],
        app_dir, out / "app.log",
    )
    hbapp = app_dir / "build" / "gd.hbapp"
    if not hbapp.is_file():
        raise Refused(f"build finished without {hbapp}; see {out}/app.log")
    global IMAGE_BYTES
    IMAGE_BYTES = hbapp.stat().st_size
    _stage_hbapp(NAME, hbapp, data)
    stamp_path().write_text(stamp)
    size = hbapp.stat().st_size
    files = [p for p in data.rglob("*") if p.is_file()]
    nbytes = sum(p.stat().st_size for p in files)
    print(f"    staged    {NAME}.hbapp {size:,} bytes, Data/GravityDefied {len(files)} files "
          f"{nbytes / 1024:.1f} KB ({time.time() - t0:.0f} s)")
    return {"name": NAME, "built": True, "hbapp_size": size, "data_files": len(files),
            "data_bytes": nbytes, "data": str(extra_data_dir(NAME))}


def heap_fit(heap_free: int, largest: Optional[int] = None) -> dict:
    after_image = heap_free - IMAGE_BYTES
    big = largest if largest is not None else after_image

    def case(lg: int) -> dict:
        size = BLOCK_MAX if lg > BLOCK_MAX + SLOP else max(0, (lg - SLOP) & ~0xFFFF)
        if after_image > AFTER_MIN:
            size = min(size, (after_image - AFTER_MIN) & ~0xFFFF)
        else:
            size = 0
        if lg < BLOCK_MIN + SLOP or size < BLOCK_MIN:
            return {"verdict": "NO_FIT", "block": 0, "left": 0}
        return {"verdict": "FITS", "block": size, "left": after_image - size}

    best = case(big)
    worst = case(big - IMAGE_BYTES) if largest is not None else best
    return {"after_image": after_image, "best": best, "worst": worst}


def heap_lines(heap_free: Optional[int], largest: Optional[int]) -> List[str]:
    mb = lambda n: f"{n / 1048576:.2f} MB"
    out = [
        f"    memory    image about {mb(IMAGE_BYTES)}, arena {mb(BLOCK_MIN)} to {mb(BLOCK_MAX)} "
        f"(game peak plus margin, no GL driver). Pre-flight: under {mb(BLOCK_MIN + SLOP)} largest or "
        f"{mb(BLOCK_MIN + AFTER_MIN)} free after the image, it shows NOT ENOUGH MEMORY.",
    ]
    if heap_free is None:
        out.append("    heap      pass Probe's numbers to check: --heap-free 6.0M --largest 4.0M")
        return out
    fit = heap_fit(heap_free, largest)
    out.append(
        f"    heap      Probe {mb(heap_free)} free"
        + (f", largest {mb(largest)}" if largest else "")
        + f"; after the image {mb(fit['after_image'])}"
    )
    for label, c in (("image elsewhere", fit["best"]), ("image in largest", fit["worst"])):
        if c["verdict"] == "NO_FIT":
            out.append(f"    {label:<17} DOES NOT FIT: NOT ENOUGH MEMORY on screen (pause music, reopen)")
        else:
            out.append(f"    {label:<17} arena {mb(c['block'])}, {mb(c['left'])} left")
    return out


def plan_lines(payload: Optional[Path], heap_free: Optional[int] = None, largest: Optional[int] = None,
               *, on_disk: Optional[List[str]] = None, slot_b: Optional[bool] = None,
               nogrow: Optional[bool] = None) -> List[str]:
    lines = [f"nano7kit nabase add '{NAME}' --plan (read-only, nothing built or written)"]
    ok = True
    if payload is None:
        lines.append("    payload   missing: pass --payload /path/to/GDTR.jar (or a folder / levels.mrg)")
        ok = False
    else:
        facts = inspect_payload(Path(payload))
        if facts["ok"]:
            n = sum(len(lg) for lg in facts["header"].get("leagues", []))
            lines.append(
                f"    payload   {facts['kind']}, {n} tracks, {len(facts['sprites'])} sprite PNGs "
                f"(Codebrew J2ME data, stays local)"
            )
        else:
            ok = False
            lines.extend("    REFUSED   " + p for p in facts["problems"])
    missing = [t for t in ("make", "arm-none-eabi-gcc", "git") if not which(t)]
    lines.append("    host      " + ("make, arm-none-eabi-gcc, git found" if not missing
                                     else "missing " + ", ".join(missing) + " (./start deps)"))
    ok = ok and not missing
    lines.append(f"    build     fetches gravity_defied_cpp {ENGINE_COMMIT[:7]} and dlmalloc 2.8.6 (sha256 pinned)")
    lines.append(f"    writes    {FAT_EXE} (Slot A if under 576 KB), {FAT_DATA}/ (levels.mrg + converted sprites)")
    lines.extend(heap_lines(heap_free, largest))
    if heap_free is not None and heap_fit(heap_free, largest)["best"]["verdict"] == "NO_FIT":
        ok = False
    lines.append(f"    undo      ./start nabase remove '{NAME}'")
    lines.append("PLAN_OK" if ok else "PLAN_REFUSED")
    return lines


def make_test_jar(dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    mrg = build_test_mrg()
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr("levels.mrg", mrg)
        for stem, (w, h) in SPRITE_SIZES.items():
            rgba = bytearray(w * h * 4)
            for y in range(h):
                for x in range(w):
                    i = (y * w + x) * 4
                    rgba[i:i + 4] = bytes((40 + (x * 3) % 80, 80, 40 + (y * 2) % 80, 255))
            tmp = dest.with_suffix("." + stem + ".png")
            write_png_rgba(tmp, w, h, bytes(rgba))
            zf.writestr(stem + ".png", tmp.read_bytes())
            tmp.unlink()
    return dest


def apply_fetched_sprites(facts: dict, engine: Path) -> int:
    """At test time only: run upstream sprite PNGs through the same converter as a jar."""
    assets = engine / "assets"
    n = 0
    for stem in SPRITE_STEMS:
        p = assets / f"{stem}.png"
        if p.is_file():
            facts["sprites"][stem] = p.read_bytes()
            n += 1
    return n


def _menu_script() -> str:
    """Splash, main, play, pause, labelled bar, Exit Game from pause then main."""
    return "\n".join([
        "wait 3",
        "dump splash",
        "wait 67",
        "dump menu_main",
        "fire",
        "wait 8",
        "dump menu_play",
        "track 0 0 0",
        "wait 8",
        "dump gameplay_easy_intro",
        "hold 1 0 0 0",
        "wait 50",
        "dump gameplay_easy_intro_ride",
        "hold 0 0 0 0",
        "pause",
        "wait 8",
        "dump menu_ingame",
        "key 6", "key 6", "key 6", "key 6", "key 6",
        "wait 4",
        "dump menu_ingame_exit",
        "fire",
        "wait 4",
        "",
    ])


def _tracks_script() -> str:
    """Start every classic track and ride until finish, crash, or timeout."""
    lines = ["wait 70"]
    dumps = {(0, 0), (1, 0), (2, 0), (2, 1)}
    for level in range(3):
        for track in range(10):
            lines += [f"track {level} {track} 0", "wait 6"]
            if (level, track) in dumps:
                slug = LEVEL_NAMES[level].lower() + "_" + TRACK_NAMES[level][track].lower().replace(" ", "_").replace("%", "pct")
                lines.append(f"dump gameplay_{slug}")
            lines += ["hold 1 0 0 0", "until 600", "hold 0 0 0 0", "wait 4"]
    lines += ["quit", ""]
    return "\n".join(lines)


def _save_script() -> str:
    """Toggle Shadows Off from Options, stamp distinctive unlocks, Exit Game from Main."""
    return "\n".join([
        "wait 70",
        "dump menu_main_save",
        "pick 1",
        "wait 6",
        "pick 1",
        "wait 4",
        "dump menu_options_shadows",
        "pick 7",
        "wait 6",
        "unlocks 2 4 1",
        "row 4",
        "wait 2",
        "dump menu_exit_main",
        "fire",
        "wait 4",
        "",
    ])


def _pause_quit_script() -> str:
    """Quit from the in-game pause menu (Exit Game row)."""
    return "\n".join([
        "wait 70",
        "track 0 0 0",
        "wait 8",
        "hold 1 0 0 0",
        "wait 20",
        "hold 0 0 0 0",
        "pause",
        "wait 8",
        "dump menu_pause_quit",
        "row 5",
        "wait 2",
        "dump menu_pause_exit",
        "fire",
        "wait 4",
        "",
    ])


def _loaded_line(blob: str) -> str:
    hits = [ln for ln in blob.splitlines() if "save: loaded" in ln]
    return hits[-1] if hits else ""


def _peak_fails(blob: str) -> Tuple[int, int, int]:
    peak = fails = arena = 0
    for line in blob.splitlines():
        if "arena " in line and "heap:" in line:
            try:
                arena = int(line.split("arena ")[1].split()[0])
            except (IndexError, ValueError):
                pass
        if "peak=" in line:
            try:
                peak = max(peak, int(line.split("peak=")[1].split()[0]))
            except (IndexError, ValueError):
                pass
        if "fails=" in line:
            try:
                fails = max(fails, int(line.split("fails=")[1].split()[0]))
            except (IndexError, ValueError):
                pass
    return arena, peak, fails


def _parse_tracks(blob: str) -> List[dict]:
    rows = []
    pending = None
    for line in blob.splitlines():
        if "track: start " in line:
            pending = {"name": "", "level": -1, "track": -1, "result": "missing"}
            rest = line.split("track: start ", 1)[1]
            if "name=" in rest:
                left, name = rest.split("name=", 1)
                pending["name"] = name.strip()
                rest = left
            try:
                parts = dict(kv.split("=", 1) for kv in rest.split() if "=" in kv)
                pending["level"] = int(parts.get("level", -1))
                pending["track"] = int(parts.get("track", -1))
            except (ValueError, IndexError):
                pass
            rows.append(pending)
        elif "track: result=" in line and rows:
            try:
                result = line.split("track: result=", 1)[1].split()[0]
            except IndexError:
                result = "unknown"
            rows[-1]["result"] = result
            if "name=" in line:
                rows[-1]["name"] = line.split("name=", 1)[1].strip()
    return rows


def _ppms_to_pngs(art: Path) -> List[Path]:
    pngs = []
    for ppm in sorted(art.glob("*.ppm")):
        try:
            raw = ppm.read_bytes()
            parts = raw.split(b"\n", 3)
            if len(parts) < 4 or not parts[0].startswith(b"P6"):
                continue
            wh = parts[1].split()
            w, h = int(wh[0]), int(wh[1])
            rgb = parts[3]
            rgba = bytearray(w * h * 4)
            for i in range(w * h):
                rgba[i * 4:i * 4 + 3] = rgb[i * 3:i * 3 + 3]
                rgba[i * 4 + 3] = 255
            dest = ppm.with_suffix(".png")
            write_png_rgba(dest, w, h, bytes(rgba))
            pngs.append(dest)
        except Exception:
            continue
    extra_art = Path("/opt/cursor/artifacts/gravity-defied")
    try:
        extra_art.mkdir(parents=True, exist_ok=True)
        same = extra_art.resolve() == art.resolve()
        if not same:
            for old in extra_art.glob("*"):
                if old.is_file():
                    old.unlink()
            for p in pngs:
                shutil.copyfile(p, extra_art / p.name)
    except OSError:
        extra_art = None
    return pngs


def _wipe_saves(data: Path) -> None:
    for p in list(data.iterdir()):
        if p.is_file() and p.name not in {"levels.mrg"}:
            p.unlink()
        elif p.is_dir() and p.name not in {"assets"}:
            shutil.rmtree(p)


def host_test(*, artifacts: Optional[Path] = None) -> dict:
    """Compile the engine for the host and run iPod-like scripts."""
    out = build_dir()
    out.mkdir(parents=True, exist_ok=True)
    engine = fetch_engine(out / "src" / "gd")
    binary = _compile_host(engine, out)
    jar = out / "test.jar"
    make_test_jar(jar)
    facts = assert_payload(jar)
    n_art = apply_fetched_sprites(facts, engine)
    print(f"    sprites   {n_art} upstream PNGs through the jar converter (not committed)")
    data = out / "host-data"
    if data.exists():
        shutil.rmtree(data)
    stage_payload_data(facts, data)
    classic = engine / "assets" / "levels.mrg"
    if classic.is_file() and classic.stat().st_size > 1000:
        shutil.copyfile(classic, data / "levels.mrg")
        print(f"==> host test tracks: classic levels.mrg {classic.stat().st_size} bytes from the pin")
    art = Path(artifacts) if artifacts else out / "host-out"
    if art.exists():
        for old in art.glob("*"):
            if old.is_file():
                old.unlink()
    art.mkdir(parents=True, exist_ok=True)

    def run(args, script_text=None, name="run", allow_rc=(0,), data_dir=None):
        sdir = out / "scripts"
        sdir.mkdir(exist_ok=True)
        d = data_dir or data
        cmd = [str(binary), "--data", str(d), "--ppm", str(art)] + list(args)
        if script_text is not None:
            sp = sdir / (name + ".txt")
            sp.write_text(script_text)
            cmd += ["--script", str(sp)]
        log = out / f"host-{name}.log"
        with log.open("w", encoding="utf-8") as fh:
            rc = subprocess.call(cmd, cwd=str(out), stdout=fh, stderr=subprocess.STDOUT)
        text = log.read_text(encoding="utf-8", errors="replace")
        if rc not in allow_rc:
            raise Refused(f"{' '.join(cmd)} rc={rc}; see {log}\n{text[-2000:]}")
        return text, rc

    tiny_log, tiny_rc = run(
        ["--free", str(6 * 1024 * 1024), "--largest", str(256 * 1024)],
        name="notice_tiny",
        allow_rc=(3,),
    )

    _wipe_saves(data)
    bt_free = str(int(3.9 * 1024 * 1024))
    bt_lg = str(2 * 1024 * 1024)
    menu_log, menu_rc = run(
        ["--free", bt_free, "--largest", bt_lg, "--frames", "800"],
        _menu_script(),
        "menus",
        allow_rc=(0,),
    )

    _wipe_saves(data)
    low_log, low_rc = run(
        ["--free", str(6 * 1024 * 1024), "--largest", str(1024 * 1024), "--frames", "400"],
        "wait 70\ntrack 0 0 0\nwait 6\ndump gameplay_1mb\nhold 1 0 0 0\nwait 40\ndump gameplay_1mb_ride\nhold 0 0 0 0\nquit\n",
        "lowmem",
        allow_rc=(0,),
    )

    _wipe_saves(data)
    tracks_log, tracks_rc = run(
        ["--free", bt_free, "--largest", bt_lg, "--frames", "20000"],
        _tracks_script(),
        "tracks",
        allow_rc=(0, 1),
    )

    _wipe_saves(data)
    save1_log, save1_rc = run(
        ["--free", bt_free, "--largest", bt_lg, "--frames", "400"],
        _save_script(),
        "save_main",
        allow_rc=(0,),
    )
    save_file = data / "GDTRStates"
    save_bytes = save_file.stat().st_size if save_file.is_file() else 0
    if save_bytes < 20:
        raise Refused(f"save_main did not write GDTRStates ({save_bytes} bytes); see {out / 'host-save_main.log'}")
    if "input: Exit Game" not in save1_log:
        raise Refused("save_main did not fire Exit Game from Main")
    save2_log, save2_rc = run(
        ["--free", bt_free, "--largest", bt_lg, "--frames", "400"],
        _pause_quit_script(),
        "save_pause",
        allow_rc=(0,),
    )
    save3_log, save3_rc = run(
        ["--free", bt_free, "--largest", bt_lg, "--frames", "200"],
        "wait 70\ndump menu_relaunch\npick 1\nwait 6\nrow 1\nwait 2\ndump menu_relaunch_options\nquit\n",
        "save_relaunch",
        allow_rc=(0,),
    )

    corrupt = data / "GDTRStates"
    corrupt.write_bytes(b"\xff" * 64)
    corrupt_log, corrupt_rc = run(
        ["--free", bt_free, "--largest", bt_lg, "--frames", "80"],
        "wait 50\ndump after_corrupt\nquit\n",
        "corrupt",
        allow_rc=(0,),
    )

    if "input: Exit Game" not in save2_log:
        raise Refused("save_pause did not fire Exit Game from the pause menu")
    loaded_pause = _loaded_line(save2_log)
    loaded_relaunch = _loaded_line(save3_log)
    if "shadows=1" not in loaded_pause or "tracks=2,4,1" not in loaded_pause:
        raise Refused(f"pause relaunch lost save/unlocks: {loaded_pause}")
    if "shadows=1" not in loaded_relaunch or "tracks=2,4,1" not in loaded_relaunch:
        raise Refused(f"main-menu save/unlocks did not survive relaunch: {loaded_relaunch}")

    pngs = _ppms_to_pngs(art)
    tracks = _parse_tracks(tracks_log)
    missing_tracks = [r for r in tracks if r.get("result") in (None, "missing")]
    if len(tracks) != 30 or missing_tracks:
        raise Refused(f"expected a result for all 30 tracks, got {len(tracks)} ({missing_tracks[:3]})")
    mem = {}
    for label, blob in (
        ("bluetooth_music", menu_log),
        ("one_mb", low_log),
        ("tracks", tracks_log),
        ("tiny", tiny_log),
    ):
        arena, peak, fails = _peak_fails(blob)
        mem[label] = {"arena": arena, "peak": peak, "fails": fails}
    shadows_loaded = [
        ln for ln in (save1_log + save2_log + save3_log).splitlines()
        if "save: loaded" in ln
    ]
    return {
        "binary": str(binary),
        "artifacts": str(art),
        "pngs": [str(p) for p in pngs],
        "notice_tiny": tiny_log[-800:],
        "notice_tiny_rc": tiny_rc,
        "menus": menu_log[-1500:],
        "menu_rc": menu_rc,
        "lowmem": low_log[-1500:],
        "low_rc": low_rc,
        "tracks_log": tracks_log[-2500:],
        "tracks_rc": tracks_rc,
        "tracks": tracks,
        "save_main_rc": save1_rc,
        "save_pause_rc": save2_rc,
        "save_relaunch_rc": save3_rc,
        "save_bytes": save_bytes,
        "save_loaded": shadows_loaded,
        "save_loaded_pause": loaded_pause,
        "save_loaded_relaunch": loaded_relaunch,
        "corrupt": corrupt_log[-800:],
        "corrupt_rc": corrupt_rc,
        "mem": mem,
        "ppm_count": len(list(art.glob("*.ppm"))),
        "sprite_pngs": n_art,
    }
