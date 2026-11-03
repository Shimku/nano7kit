"""Fruit Ninja 1.0 recomp + oracle pipeline (first-party, no upstream port).

Your Fruit Ninja 1.0 .ipa stays on the operator's machine. Never commit
it, never publish derived textures / fileArcive.bin / the original Fruit
executable. Same rule as Temple Run.

Tonight's hardware-testable slice is a RAW_SURFACE swipe shell plus streamed
textures. It is not a complete Mortar recomp. OpenAL is silent. OpenFeint
and Facebook are stubbed. fileArcive.bin is catalogued by seeking 12-byte
headers; the 8.6 MB file is never held whole in RAM.
"""

from __future__ import annotations

import json
import os
import plistlib
import struct
from pathlib import Path
from typing import BinaryIO, Dict, List, Optional, Sequence, Tuple

from .compat import Refused, root, sha256_file, which
from .ports import _payload_app_dir, _run_make, _stage_hbapp, extra_data_dir

FRUIT_BUNDLE_ID = "com.Halfbrick.Fruit"
FRUIT_IPA_SHA256 = (
    "427538e79b691c35cc63a9b7f5b0deec9b154e9d9c4d2b7d9bd10c71afcd01a5"
)
FRUIT_EXE_NAME = "Fruit"
FRUIT_EXE_SIZE = 2_261_728
FRUIT_CMD = "./start apps add 'Fruit Ninja' --ipa /path/to/FruitNinja.ipa"
FRUIT_NAME = "Fruit Ninja"
ARCHIVE_NAME = "fileArcive.bin"
MUSIC_NAME = "MusicArcive.bin"

# Header (little-endian):
#   u8 log2_w, u8 log2_h, u8 format, u8 zero
#   u16 width, u16 height
#   u32 colorkey  (0xffffffff)
# format 0x10 / 0x11: 16-bit pixels (RGBA4444 or RGB565). format 0x00 is
# likely PVRTC; for now we record it and skip decode rather than invent bits.
POT = frozenset({8, 16, 32, 64, 128, 256, 512, 1024})
MAX_NANO_SPRITE = 128 * 128 * 2  # 32 KiB; never the 512x512 sheets
BANNER_W, BANNER_H = 240, 48

MH_MAGIC = 0xFEEDFACE
MH_CIGAM = 0xCEFAEDFE
CPU_TYPE_ARM = 12
LC_SEGMENT = 0x01
LC_SYMTAB = 0x02
LC_LOAD_DYLIB = 0x0C
LC_ENCRYPTION_INFO = 0x21
N_SECT = 0x0E
N_TYPE = 0x0E

STUBS: Tuple[Dict[str, str], ...] = (
    {
        "name": "OpenAL",
        "policy": "silent",
        "symbols": "alcOpenDevice alcCreateContext alGenBuffers alBufferData alSourcePlay alListenerfv",
        "note": "MusicArcive.bin is a CAF/AAC blob (1.7 MB). Do not copy it. alSourcePlay is a no-op.",
    },
    {
        "name": "OpenFeint",
        "policy": "stub",
        "symbols": "OpenFeint* OF* dashboard login achievements leaderboards",
        "note": "718 string hits in the 1.0 binary. Return success/empty. Drop OF chrome PNGs.",
    },
    {
        "name": "Facebook",
        "policy": "stub",
        "symbols": "FacebookCallback FacebookExtendedCredential OFFacebookIcon",
        "note": "Game-over share is a no-op. No CFNetwork.",
    },
    {
        "name": "MPOAuth / CoreLocation / sqlite / Security",
        "policy": "stub",
        "symbols": "MPOAuth CoreLocation CFNetwork sqlite3 Security SystemConfiguration",
        "note": "Slice game does not need them. Unicorn hooks return 0.",
    },
)

MORTAR_SURFACE = (
    "Mortar::FileArchive / ROFA_FindEntry (fileArcive.bin, not loaded whole)",
    "Mortar::Iphone::Texture2DFromFile_Iphone (textures/%s.tex)",
    "Mortar::DisplayManagerIphone + EAGLView touchesBegan/Moved/Ended",
    "Mortar::InputDeviceIphoneTouch / IPhoneInterface::UpdateTouchScreenInput",
    "SlashEntity::PlaySwipe / Fruit::Slice / Bomb (9BombBlast)",
    "Mortar::Mesh / IVertexStream (HBR0 chunks inside the archive)",
)


def work_dir() -> Path:
    d = root() / "build" / "fruitninja"
    d.mkdir(parents=True, exist_ok=True)
    return d


def assert_fruit_payload(payload: Path) -> Path:
    app = _payload_app_dir(
        payload,
        ("Fruit.app", "Fruit Ninja.app"),
        expected_bundle=FRUIT_BUNDLE_ID,
    )
    exe = app / FRUIT_EXE_NAME
    if not exe.is_file():
        raise Refused(f"{app} has no {FRUIT_EXE_NAME} executable")
    return app


def ipa_hash_status(path: Path) -> Dict[str, object]:
    src = Path(path).expanduser().resolve()
    digest = sha256_file(src) if src.is_file() else ""
    return {
        "path": str(src) if src.is_file() else str(path),
        "present": src.is_file(),
        "sha256": digest,
        "matches_reference": digest == FRUIT_IPA_SHA256,
        "expected": FRUIT_IPA_SHA256,
    }


def _u32(buf: bytes, off: int, swapped: bool) -> int:
    if swapped:
        return struct.unpack_from(">I", buf, off)[0]
    return struct.unpack_from("<I", buf, off)[0]


def parse_macho(path: Path) -> Dict[str, object]:
    """Inventory a thin 32-bit Mach-O. Reads the file once (Fruit is 2.26 MB)."""
    data = Path(path).read_bytes()
    if len(data) < 28:
        raise Refused(f"{path} is too small to be Mach-O")
    magic = struct.unpack_from("<I", data, 0)[0]
    swapped = magic == MH_CIGAM
    if magic not in (MH_MAGIC, MH_CIGAM):
        raise Refused(f"{path} is not a 32-bit Mach-O (magic {magic:#x})")
    cputype = _u32(data, 4, swapped)
    subtype = _u32(data, 8, swapped)
    ncmds = _u32(data, 16, swapped)
    sizeofcmds = _u32(data, 20, swapped)
    cryptid = 0
    cryptoff = 0
    cryptsize = 0
    text_size = 0
    dylibs: List[str] = []
    symoff = 0
    nsyms = 0
    stroff = 0
    strsize = 0
    off = 28
    for _ in range(ncmds):
        if off + 8 > len(data):
            break
        cmd = _u32(data, off, swapped)
        clen = _u32(data, off + 4, swapped)
        if clen < 8 or off + clen > len(data):
            break
        if cmd == LC_SEGMENT:
            name = data[off + 8 : off + 24].split(b"\x00", 1)[0].decode("ascii", "replace")
            vmsize = _u32(data, off + 28, swapped)
            if name == "__TEXT":
                text_size = vmsize
        elif cmd == LC_SYMTAB:
            symoff = _u32(data, off + 8, swapped)
            nsyms = _u32(data, off + 12, swapped)
            stroff = _u32(data, off + 16, swapped)
            strsize = _u32(data, off + 20, swapped)
        elif cmd == LC_LOAD_DYLIB:
            noff = _u32(data, off + 8, swapped)
            raw = data[off + noff : off + clen]
            dylibs.append(raw.split(b"\x00", 1)[0].decode("ascii", "replace"))
        elif cmd == LC_ENCRYPTION_INFO:
            cryptoff = _u32(data, off + 8, swapped)
            cryptsize = _u32(data, off + 12, swapped)
            cryptid = _u32(data, off + 16, swapped)
        off += clen
    names: List[str] = []
    n_sect = 0
    if nsyms and stroff and strsize and stroff + strsize <= len(data):
        strtab = data[stroff : stroff + strsize]
        for i in range(nsyms):
            eoff = symoff + i * 12
            if eoff + 12 > len(data):
                break
            n_strx = _u32(data, eoff, swapped)
            n_type = data[eoff + 4]
            if n_strx >= len(strtab):
                continue
            raw = strtab[n_strx:].split(b"\x00", 1)[0]
            if not raw:
                continue
            try:
                name = raw.decode("ascii")
            except UnicodeDecodeError:
                continue
            names.append(name)
            if (n_type & N_TYPE) == N_SECT:
                n_sect += 1
    mortar = [n for n in names if "Mortar" in n or "mortar" in n]
    openfeint = [n for n in names if "OpenFeint" in n or "OF" == n[:2]]
    return {
        "path": str(path),
        "size": len(data),
        "cputype": cputype,
        "cpusubtype": subtype,
        "armv6": cputype == CPU_TYPE_ARM and subtype == 6,
        "ncmds": ncmds,
        "sizeofcmds": sizeofcmds,
        "cryptid": cryptid,
        "cryptoff": cryptoff,
        "cryptsize": cryptsize,
        "text_size": text_size,
        "dylibs": dylibs,
        "nsymbols": len(names),
        "n_sect": n_sect,
        "mortar_symbols": len(mortar),
        "openfeint_symbols": len(openfeint),
        "sample_mortar": mortar[:24],
        "has_uikit": any("UIKit" in d for d in dylibs),
        "has_openal": any("OpenAL" in d for d in dylibs),
        "has_fmod": any("FMOD" in d.upper() or "fmod" in d for d in dylibs),
    }


def _header_at(fh: BinaryIO, pos: int) -> Optional[dict]:
    fh.seek(pos)
    hdr = fh.read(12)
    if len(hdr) < 12:
        return None
    log2w, log2h, fmt, z = hdr[0], hdr[1], hdr[2], hdr[3]
    w, h = struct.unpack_from("<HH", hdr, 4)
    key = struct.unpack_from("<I", hdr, 8)[0]
    if key != 0xFFFFFFFF:
        return None
    if w not in POT or h not in POT:
        return None
    if z != 0:
        return None
    if (1 << log2w) != w or (1 << log2h) != h:
        return None
    return {
        "offset": pos,
        "log2_w": log2w,
        "log2_h": log2h,
        "format": fmt,
        "width": w,
        "height": h,
        "bytes": w * h * 2,
        "pvrtc": fmt == 0,
    }


def catalog_archive(path: Path) -> List[dict]:
    """Seek-scan fileArcive.bin. Never reads the payload until extract time."""
    src = Path(path)
    entries: List[dict] = []
    with src.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        pos = 0
        idx = 0
        while pos + 12 <= size:
            rec = _header_at(fh, pos)
            if rec is None:
                pos += 4
                continue
            rec["index"] = idx
            entries.append(rec)
            idx += 1
            pos += 12 + int(rec["bytes"])
    return entries


def read_tex_pixels(path: Path, rec: dict) -> bytes:
    with Path(path).open("rb") as fh:
        fh.seek(int(rec["offset"]) + 12)
        blob = fh.read(int(rec["bytes"]))
    if len(blob) != int(rec["bytes"]):
        raise Refused(f"short texture read at {rec['offset']}")
    return blob


def _rgb565(v: int) -> Tuple[int, int, int, int]:
    r = ((v >> 11) & 31) * 255 // 31
    g = ((v >> 5) & 63) * 255 // 63
    b = (v & 31) * 255 // 31
    return r, g, b, 255


def _rgba4444(v: int) -> Tuple[int, int, int, int]:
    r = ((v >> 12) & 15) * 17
    g = ((v >> 8) & 15) * 17
    b = ((v >> 4) & 15) * 17
    a = (v & 15) * 17
    return r, g, b, a


def decode_rgba(pixels: bytes, w: int, h: int, fmt: int) -> List[Tuple[int, int, int, int]]:
    n = w * h
    if fmt == 0:
        # PVRTC not decoded yet. Return empty so callers skip.
        return []
    use_4444 = fmt >= 0x10
    out: List[Tuple[int, int, int, int]] = []
    for i in range(n):
        v = struct.unpack_from("<H", pixels, i * 2)[0]
        out.append(_rgba4444(v) if use_4444 else _rgb565(v))
    return out


def _save_png(rgba: Sequence[Tuple[int, int, int, int]], w: int, h: int, dest: Path) -> None:
    from PIL import Image

    im = Image.new("RGBA", (w, h))
    im.putdata(list(rgba))
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest)


def extract_textures(archive: Path, dest: Path, *, limit: Optional[int] = None) -> List[dict]:
    """Stream each catalogued texture one at a time into dest/*.png."""
    dest.mkdir(parents=True, exist_ok=True)
    catalog = catalog_archive(archive)
    written: List[dict] = []
    for rec in catalog:
        if rec.get("pvrtc"):
            rec = dict(rec)
            rec["png"] = ""
            rec["skipped"] = "pvrtc"
            written.append(rec)
            continue
        pixels = read_tex_pixels(archive, rec)
        rgba = decode_rgba(pixels, rec["width"], rec["height"], rec["format"])
        if not rgba:
            rec = dict(rec)
            rec["png"] = ""
            rec["skipped"] = "decode"
            written.append(rec)
            continue
        name = f"{rec['index']:02d}_{rec['width']}x{rec['height']}_f{rec['format']:02x}.png"
        png = dest / name
        _save_png(rgba, rec["width"], rec["height"], png)
        rec = dict(rec)
        rec["png"] = str(png)
        rec["skipped"] = ""
        written.append(rec)
        if limit is not None and len([w for w in written if w.get("png")]) >= limit:
            break
    return written


def render_host_png(textures: Sequence[dict], dest: Path) -> Path:
    """Headless 240x432 portrait frame from streamed textures + procedural fruit."""
    from PIL import Image, ImageDraw

    dest.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGBA", (240, 432), (20, 16, 24, 255))
    draw = ImageDraw.Draw(im)
    draw.rectangle((0, 0, 240, 36), fill=(28, 20, 24, 255))
    draw.text((8, 10), "SLICE HOST", fill=(232, 197, 71, 255))
    y = 48
    used = 0
    ordered = sorted(
        (rec for rec in textures if rec.get("png") and Path(rec["png"]).is_file()),
        key=lambda rec: -(int(rec["width"]) * int(rec["height"])),
    )
    for rec in ordered:
        if rec["width"] * rec["height"] > 256 * 256:
            continue
        sheet = Image.open(rec["png"]).convert("RGBA")
        sheet.thumbnail((220, 140))
        im.paste(sheet, (10, y), sheet)
        y += sheet.size[1] + 8
        used += 1
        if used >= 2 or y > 260:
            break
    # procedural fruit so a missing atlas still produces a frame
    fruits = ((60, 340, 18, (230, 57, 70)), (120, 310, 16, (255, 214, 10)),
              (180, 350, 20, (42, 157, 143)))
    for x, fy, r, col in fruits:
        draw.ellipse((x - r, fy - r, x + r, fy + r), fill=col)
        draw.ellipse((x - r // 2, fy - r // 2, x - r // 6, fy - r // 6), fill=(255, 255, 255, 180))
    draw.line((40, 300, 200, 250), fill=(244, 241, 222, 255), width=3)
    draw.text((8, 412), "not Mortar  silent AL", fill=(90, 98, 112, 255))
    im.convert("RGB").save(dest)
    return dest


def write_banner_rgb565(textures: Sequence[dict], dest: Path) -> Optional[Path]:
    """Pick a wide UI texture (GAME OVER etc.) and letterbox it to 240x48 RGB565."""
    from PIL import Image

    src_png = None
    for rec in textures:
        png = rec.get("png") or ""
        if not png or not Path(png).is_file():
            continue
        if rec["width"] >= 128 and rec["height"] >= 64 and rec["width"] <= 256:
            src_png = Path(png)
            break
    dest.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (BANNER_W, BANNER_H), (20, 16, 24))
    if src_png is not None:
        sheet = Image.open(src_png).convert("RGBA")
        sheet.thumbnail((BANNER_W, BANNER_H))
        x = (BANNER_W - sheet.size[0]) // 2
        y = (BANNER_H - sheet.size[1]) // 2
        bg = Image.new("RGBA", (BANNER_W, BANNER_H), (20, 16, 24, 255))
        bg.paste(sheet, (x, y), sheet)
        im = bg.convert("RGB")
    raw = bytearray()
    for py in range(BANNER_H):
        for px in range(BANNER_W):
            r, g, b = im.getpixel((px, py))[:3]
            v = ((r * 31 // 255) << 11) | ((g * 63 // 255) << 5) | (b * 31 // 255)
            raw += struct.pack("<H", v)
    dest.write_bytes(bytes(raw))
    return dest


def write_nano_data(textures: Sequence[dict], dest: Path) -> dict:
    """Stage streamed, size-capped sprites. Never copy fileArcive.bin or CAF."""
    if dest.exists():
        import shutil

        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    banner = write_banner_rgb565(textures, dest / "banner.rgb565")
    catalog = []
    copied = 0
    bytes_out = banner.stat().st_size if banner and banner.is_file() else 0
    for rec in textures:
        png = rec.get("png") or ""
        if not png or rec.get("pvrtc"):
            continue
        nbytes = int(rec["bytes"])
        if nbytes > MAX_NANO_SPRITE:
            continue
        src = Path(png)
        target = dest / src.name
        target.write_bytes(src.read_bytes())
        catalog.append(
            {
                "file": src.name,
                "width": rec["width"],
                "height": rec["height"],
                "format": rec["format"],
                "bytes": nbytes,
            }
        )
        copied += 1
        bytes_out += target.stat().st_size
    (dest / "catalog.json").write_text(
        json.dumps(
            {
                "note": "streamed from fileArcive.bin; original archive not copied",
                "banner": "banner.rgb565",
                "textures": catalog,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (dest / "README.txt").write_text(
        "Derived Fruit Ninja 1.0 textures. Not in git. Do not publish.\n"
        "fileArcive.bin and MusicArcive.bin were not copied here.\n",
        encoding="utf-8",
    )
    return {"files": copied, "bytes": bytes_out, "dir": str(dest)}


def write_inventory(app: Path, dest: Path) -> dict:
    exe = app / FRUIT_EXE_NAME
    macho = parse_macho(exe)
    archive = app / ARCHIVE_NAME
    music = app / MUSIC_NAME
    textures = catalog_archive(archive) if archive.is_file() else []
    info: dict = {}
    pl = app / "Info.plist"
    if pl.is_file():
        try:
            info = plistlib.loads(pl.read_bytes())
        except Exception:
            info = {}
    report = {
        "bundle_id": info.get("CFBundleIdentifier"),
        "version": info.get("CFBundleVersion"),
        "min_os": info.get("MinimumOSVersion"),
        "exe": macho,
        "archive": {
            "path": ARCHIVE_NAME,
            "present": archive.is_file(),
            "size": archive.stat().st_size if archive.is_file() else 0,
            "textures": len(textures),
            "pvrtc": sum(1 for t in textures if t.get("pvrtc")),
            "sixteen_bit": sum(1 for t in textures if not t.get("pvrtc")),
            "plan": (
                "seek 12-byte headers (log2 w/h + format + 0xffffffff). "
                "Read one payload at a time. Never mmap the 8.6 MB whole. "
                "Skip MusicArcive.bin (CAF). Skip *Fat.bin. Drop OpenFeint PNGs."
            ),
        },
        "music": {
            "path": MUSIC_NAME,
            "present": music.is_file(),
            "size": music.stat().st_size if music.is_file() else 0,
            "copied": False,
        },
        "stubs": list(STUBS),
        "mortar_surface": list(MORTAR_SURFACE),
        "verified": {
            "cryptid0": macho.get("cryptid") == 0,
            "armv6": bool(macho.get("armv6")),
            "exe_size": macho.get("size") == FRUIT_EXE_SIZE,
            "no_uikit": not macho.get("has_uikit"),
            "openal": bool(macho.get("has_openal")),
            "no_fmod": not macho.get("has_fmod"),
        },
        "honest": (
            "Host PNG + catalog + RAW slice shell are verified in this tree. "
            "Mortar recomp, PVRTC decode, Unicorn heap compare, and a full "
            "game loop matching 1.0 are NOT verified."
        ),
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def oracle_status(exe: Path) -> dict:
    """Unicorn oracle skeleton. Does not need the original to be committed."""
    try:
        import unicorn  # noqa: F401
        have = True
        reason = "unicorn import ok; run is still a skeleton (stubs listed, no heap compare)"
    except Exception as exc:
        have = False
        reason = f"unicorn not installed ({exc.__class__.__name__}); inventory-only"
    return {
        "have_unicorn": have,
        "exe": str(exe),
        "reason": reason,
        "hooks": [s["name"] + ":" + s["policy"] for s in STUBS],
        "compare": "not run (no original_oracle.py for Mortar yet)",
    }


def build_shell() -> Path:
    app_dir = root() / "nanoapps" / "apps" / "fruitninja"
    if not (app_dir / "Makefile").is_file():
        raise Refused("nanoapps/apps/fruitninja is missing")
    if not which("arm-none-eabi-gcc"):
        hbapp = app_dir / "build" / "fruitninja.hbapp"
        staged = extra_data_dir(FRUIT_NAME)
        extra = root() / "untether" / "artifacts" / "extra" / "Executables" / f"{FRUIT_NAME}.hbapp"
        if extra.is_file():
            return extra
        if hbapp.is_file():
            return hbapp
        raise Refused("arm-none-eabi-gcc is missing (run ./start deps)")
    _run_make(
        ["make", "-C", str(app_dir)],
        app_dir,
        root() / "build" / "fruitninja-app.log",
    )
    hbapp = app_dir / "build" / "fruitninja.hbapp"
    if not hbapp.is_file():
        raise Refused(f"fruitninja hbapp missing after make: {hbapp}")
    return hbapp


def build_fruitninja(payload: Path) -> dict:
    """Host-only: inventory, stream textures, host PNG, nano shell, stage."""
    src = Path(payload).expanduser().resolve()
    if src.is_file():
        status = ipa_hash_status(src)
        if not status["matches_reference"]:
            print(
                "note: ipa sha256 is not the pinned 1.0 reference dump "
                f"({FRUIT_IPA_SHA256[:16]}...); still accepting bundle "
                f"{FRUIT_BUNDLE_ID}"
            )
    app = assert_fruit_payload(src)
    wd = work_dir()
    report = write_inventory(app, wd / "inventory.json")
    archive = app / ARCHIVE_NAME
    if not archive.is_file():
        raise Refused(f"{ARCHIVE_NAME} missing in {app}")
    tex_dir = wd / "textures"
    textures = extract_textures(archive, tex_dir)
    frame = render_host_png(textures, wd / "host-frames" / "frame-000.png")
    data_src = wd / "nano-data"
    staged_data = write_nano_data(textures, data_src)
    oracle = oracle_status(app / FRUIT_EXE_NAME)
    (wd / "oracle.json").write_text(json.dumps(oracle, indent=2) + "\n", encoding="utf-8")
    hbapp = build_shell()
    _stage_hbapp(FRUIT_NAME, hbapp, data_src)
    data_dest = extra_data_dir(FRUIT_NAME)
    print("fruitninja host pipeline")
    print(f"  bundle    {report.get('bundle_id')} v{report.get('version')}")
    print(f"  cryptid   {report['exe'].get('cryptid')}  TEXT {report['exe'].get('text_size')}")
    print(f"  textures  {len(textures)} streamed from {ARCHIVE_NAME} (not loaded whole)")
    print(f"  host png  {frame}")
    print(f"  data      {staged_data['dir']}  {staged_data['bytes']} bytes")
    print(f"  oracle    {oracle['reason']}")
    print(f"  hbapp     {hbapp}  {hbapp.stat().st_size} bytes")
    print("  honest    slice shell + catalog; Mortar recomp not verified")
    print("Disk Mode, one extra per reboot (Do not disconnect):")
    print("  " + FRUIT_CMD)
    return {
        "name": FRUIT_NAME,
        "data": str(data_dest),
        "hbapp": str(hbapp),
        "frame": str(frame),
        "inventory": str(wd / "inventory.json"),
        "textures": len(textures),
        "oracle": oracle,
    }


def print_inventory(payload: Optional[Path] = None) -> int:
    print("Fruit Ninja 1.0 pipeline (recomp + oracle, first-party)")
    print("Original .ipa stays local. Never commit it. Never load fileArcive.bin whole.")
    print()
    print("stubs")
    for row in STUBS:
        print(f"  - {row['name']}: {row['policy']}  ({row['note']})")
    print()
    print("mortar surface")
    for line in MORTAR_SURFACE:
        print(f"  - {line}")
    print()
    print("slot: the RAW shell fits A; full Mortar recomp needs B (TEXT 1.28 MB)")
    print("command: " + FRUIT_CMD)
    if payload is not None:
        app = assert_fruit_payload(payload)
        report = write_inventory(app, work_dir() / "inventory.json")
        print()
        print(f"  bundle   {report.get('bundle_id')}  {report.get('version')}")
        print(f"  exe      {report['exe'].get('size')} cryptid {report['exe'].get('cryptid')}")
        print(f"  symbols  {report['exe'].get('nsymbols')}  mortar {report['exe'].get('mortar_symbols')}")
        print(f"  archive  {report['archive'].get('size')}  textures {report['archive'].get('textures')}")
        print(f"  honest   {report['honest']}")
    else:
        print("pass --payload FILE.ipa to inventory a local dump")
    return 0


def write_synthetic_archive(dest: Path, *, n: int = 2) -> Path:
    """Tiny fileArcive.bin for tests. 16x16 then 32x32 RGB565."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    blob = bytearray()
    specs = [(4, 0x10, bytes([0x00, 0xF8] * (16 * 16))), (5, 0x10, bytes([0xE0, 0x07] * (32 * 32)))]
    for log2, fmt, pixels in specs[:n]:
        w = 1 << log2
        blob += bytes([log2, log2, fmt, 0])
        blob += struct.pack("<HH I", w, w, 0xFFFFFFFF)
        blob += pixels
    dest.write_bytes(bytes(blob))
    return dest
