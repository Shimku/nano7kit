"""Plants vs. Zombies iOS 1.0 analysis stub (research only, no apps add).

Your PvZ 1.0 .ipa (com.popcap.PvZ build 524) stays on the operator's
machine. Never commit it, never publish derived atlases / the original PvZ
executable / credits.mp4. Same rule as Temple Run and Fruit Ninja.

Tonight's command inventories Mach-O, frameworks, and the asset footprint
against slot A (576 KB) and slot B, and records the recomp + Unicorn-oracle
plan. It does not build an .hbapp and it does not copy anything to the nano.
"""

from __future__ import annotations

import json
import plistlib
import struct
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .compat import Refused, root, sha256_file
from .fruitninja import parse_macho
from .ports import SLOT_A_GL_STAGING, _payload_app_dir

PVZ_BUNDLE_ID = "com.popcap.PvZ"
PVZ_IPA_SHA256 = (
    "ab6a66cd9b6cf47d81f1e010c614a50bded3ebbc0e04edd744ed55c69d6f5399"
)
PVZ_EXE_NAME = "PvZ"
PVZ_EXE_SIZE = 2_509_984
PVZ_TEXT_SIZE = 2_183_168
PVZ_CRYPTSIZE = 2_179_072
PVZ_MEMBERS = 507
PVZ_UNPACKED = 38_193_013
PVZ_CMD = "./start port pvz --payload /path/to/pvz.ipa"
PVZ_VERSION = "524"
PVZ_MIN_OS = "2.2.1"
SLOT_A = SLOT_A_GL_STAGING
PROBE_FREE = 8.7
PROBE_LARGEST = 8.0

# PopCap compiled resource: magic DEADFED4, then little-endian size, then zlib.
COMPILED_MAGIC = 0xDEADFED4

STUBS: Tuple[Dict[str, str], ...] = (
    {
        "name": "FMOD / AudioQueue / AudioUnit",
        "policy": "silent",
        "symbols": (
            "Sexy::FMODSoundInstance Sexy::FMODMusicInterface "
            "AudioQueueNewOutput AudioUnitRender AudioSessionInitialize"
        ),
        "note": (
            "No OpenAL. No FMOD.framework dylib; FMOD is a C++ wrapper over "
            "AudioQueue. 173 CAF files (12.6 MB) stay on the Mac."
        ),
    },
    {
        "name": "UIKit / Foundation / QuartzCore",
        "policy": "stub",
        "symbols": (
            "UIApplicationMain UIWindow UIView UIImage "
            "UIImagePickerController NSSearchPathForDirectoriesInDomains"
        ),
        "note": (
            "Unlike Fruit Ninja, this binary loads UIKit. Unicorn hooks return "
            "empty objects. No photo picker on the nano."
        ),
    },
    {
        "name": "MediaPlayer",
        "policy": "skip",
        "symbols": (
            "MPMoviePlayerController "
            "MPMoviePlayerPlaybackDidFinishNotification"
        ),
        "note": "credits.mp4 is 12,074,127 bytes. Do not copy it. Do not decode it.",
    },
    {
        "name": "EAGL / OpenGLES / CoreGraphics",
        "policy": "recomp target",
        "symbols": "EAGLContext CAEAGLLayer glDrawArrays glClear CGImageCreateWithImageInRect",
        "note": (
            "Landscape 480x320 on iPhone. Nano is 240x432 portrait. CPU-clip "
            "and 512-capped tiles (./start port rules)."
        ),
    },
    {
        "name": "CFNetwork / GameKit / OpenFeint / Facebook",
        "policy": "absent",
        "symbols": "",
        "note": "No OpenFeint, no Facebook, no Game Center in this 1.0 dump.",
    },
)

SEXY_SURFACE = (
    "Sexy::Graphics / Sexy::MemoryImage / Sexy::Widget (UIKit shell around this)",
    "Sexy::ButtonWidget / Dialog / EditWidget / ScrollWidget",
    "Sexy::FMODSoundInstance / Sexy::FMODMusicInterface (silent on nano)",
    "TodLib::DataArray<Plant, Zombie, Projectile, LawnMower, GridItem, Coin, "
    "Reanimation, TodParticle, Attachment, Trail>",
    "TodLib reanim + particle XML in 218 .compiled blobs (DEADFED4 + zlib)",
    "EAGLContext + glDrawArrays; UIInterfaceOrientationLandscapeRight",
)

PINNED = {
    "bundle_id": PVZ_BUNDLE_ID,
    "version": PVZ_VERSION,
    "short_version": "1.0",
    "min_os": PVZ_MIN_OS,
    "sdk": "iphoneos3.0",
    "orientation": "UIInterfaceOrientationLandscapeRight",
    "members": PVZ_MEMBERS,
    "unpacked": PVZ_UNPACKED,
    "exe_size": PVZ_EXE_SIZE,
    "text_size": PVZ_TEXT_SIZE,
    "cryptid": 0,
    "cryptoff": 4096,
    "cryptsize": PVZ_CRYPTSIZE,
    "cputype": 12,
    "cpusubtype": 6,
    "nsymbols": 1349,
    "n_sect": 889,
    "sexy_symbols": 448,
    "tod_symbols": 155,
    "png_count": 70,
    "png_bytes": 8_956_002,
    "png_rgba": 33_091_628,
    "caf_count": 173,
    "caf_bytes": 12_571_183,
    "compiled_count": 218,
    "compiled_bytes": 1_149_764,
    "mp4_bytes": 12_074_127,
    "keep_after_skip_audio_video": 13_485_052,
    "atlas": "images/PlantsZombies.png 1024x1003 RGBA 4,108,288",
}


def work_dir() -> Path:
    d = root() / "build" / "pvz"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ipa_hash_status(path: Path) -> Dict[str, object]:
    src = Path(path).expanduser().resolve()
    digest = sha256_file(src) if src.is_file() else ""
    return {
        "path": str(src) if src.is_file() else str(path),
        "present": src.is_file(),
        "sha256": digest,
        "matches_reference": digest == PVZ_IPA_SHA256,
        "expected": PVZ_IPA_SHA256,
    }


def assert_pvz_payload(payload: Path) -> Path:
    app = _payload_app_dir(
        payload,
        ("PvZ.app", "Plants vs. Zombies.app", "PlantsVsZombies.app"),
        expected_bundle=PVZ_BUNDLE_ID,
    )
    exe = app / PVZ_EXE_NAME
    if not exe.is_file():
        raise Refused(f"{app} has no {PVZ_EXE_NAME} executable")
    return app


def _png_ihdr(blob: bytes) -> Optional[Tuple[int, int, int, int]]:
    # 8-byte sig + 4 length + 4 "IHDR" + 13-byte IHDR payload
    if len(blob) < 26 or blob[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    if blob[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", blob[16:24])
    return w, h, blob[24], blob[25]


def _zip_members(src: Path) -> List[zipfile.ZipInfo]:
    with zipfile.ZipFile(src) as zf:
        return list(zf.infolist())


def catalog_ipa(path: Path) -> dict:
    """Seek the zip directory and PNG IHDR bytes. Never decode RGBA. Never read mp4."""
    src = Path(path).expanduser().resolve()
    if not src.is_file():
        raise Refused(f"ipa missing: {src}")
    try:
        infos = _zip_members(src)
    except zipfile.BadZipFile as exc:
        raise Refused(f"{src} is not a zip/ipa ({exc})") from exc
    files = [i for i in infos if not i.is_dir()]
    members = files
    pngs: List[dict] = []
    cafs = 0
    caf_bytes = 0
    compiled = 0
    compiled_bytes = 0
    mp4_bytes = 0
    mp4_name = ""
    unpacked = 0
    with zipfile.ZipFile(src) as zf:
        for info in members:
            unpacked += info.file_size
            name = info.filename.replace("\\", "/")
            lower = name.lower()
            suffix = Path(name).suffix.lower()
            if suffix == ".caf":
                cafs += 1
                caf_bytes += info.file_size
            elif suffix == ".compiled":
                compiled += 1
                compiled_bytes += info.file_size
            elif suffix == ".mp4":
                mp4_bytes += info.file_size
                mp4_name = name
            elif suffix == ".png":
                with zf.open(info) as fh:
                    hdr = fh.read(29)
                ihdr = _png_ihdr(hdr)
                rec = {
                    "name": name.split("/")[-1],
                    "path": name,
                    "bytes": info.file_size,
                }
                if ihdr:
                    w, h, bit, color = ihdr
                    rec.update(
                        {
                            "width": w,
                            "height": h,
                            "bit": bit,
                            "color": color,
                            "rgba": w * h * 4,
                            "over_512": w > 512 or h > 512,
                        }
                    )
                pngs.append(rec)
    pngs.sort(key=lambda r: int(r.get("rgba") or 0), reverse=True)
    skip = caf_bytes + mp4_bytes
    return {
        "path": str(src),
        "sha256": sha256_file(src),
        "zip_bytes": src.stat().st_size,
        "members": len(infos),
        "files": len(files),
        "unpacked": unpacked,
        "pngs": pngs,
        "png_count": len(pngs),
        "png_bytes": sum(int(p["bytes"]) for p in pngs),
        "png_rgba": sum(int(p.get("rgba") or 0) for p in pngs),
        "png_over_512": sum(1 for p in pngs if p.get("over_512")),
        "caf_count": cafs,
        "caf_bytes": caf_bytes,
        "compiled_count": compiled,
        "compiled_bytes": compiled_bytes,
        "mp4_bytes": mp4_bytes,
        "mp4_name": mp4_name,
        "keep_after_skip_audio_video": unpacked - skip,
        "plan": (
            "Skip credits.mp4 and every CAF. Stream one PNG IHDR at a time. "
            "Decode at most one 512-capped RGB565 tile into RAM. "
            ".compiled is DEADFED4 + zlib; inflate one blob, not all 218. "
            f"Never hold {unpacked} unpacked bytes or the 33 MB RGBA set."
        ),
    }


def _count_syms(names: Sequence[str], needle: str) -> int:
    return sum(1 for n in names if needle in n)


def classify_macho(path: Path) -> dict:
    macho = parse_macho(path)
    data = Path(path).read_bytes()
    magic = struct.unpack_from("<I", data, 0)[0]
    swapped = magic == 0xCEFAEDFE

    def u32(off: int) -> int:
        if swapped:
            return struct.unpack_from(">I", data, off)[0]
        return struct.unpack_from("<I", data, off)[0]

    ncmds = u32(16)
    off = 28
    symoff = nsyms = stroff = strsize = 0
    for _ in range(ncmds):
        cmd = u32(off)
        clen = u32(off + 4)
        if clen < 8 or off + clen > len(data):
            break
        if cmd == 0x02:
            symoff = u32(off + 8)
            nsyms = u32(off + 12)
            stroff = u32(off + 16)
            strsize = u32(off + 20)
        off += clen
    names: List[str] = []
    if nsyms and stroff and stroff + strsize <= len(data):
        strtab = data[stroff : stroff + strsize]
        for i in range(nsyms):
            eoff = symoff + i * 12
            if eoff + 12 > len(data):
                break
            n_strx = u32(eoff)
            if n_strx >= len(strtab):
                continue
            raw = strtab[n_strx:].split(b"\x00", 1)[0]
            try:
                name = raw.decode("ascii")
            except UnicodeDecodeError:
                continue
            if name:
                names.append(name)
    dylibs = [str(d) for d in (macho.get("dylibs") or [])]
    macho.update(
        {
            "names_sample_sexy": [n for n in names if "Sexy" in n][:12],
            "sexy_symbols": _count_syms(names, "Sexy"),
            "tod_symbols": _count_syms(names, "Tod"),
            "dataarray_symbols": _count_syms(names, "DataArray"),
            "fmod_symbols": _count_syms(names, "FMOD") + _count_syms(names, "fmod"),
            "uikit_symbols": sum(
                1
                for n in names
                if "UIApplication" in n or "UIView" in n or "UIWindow" in n or "UIImage" in n
            ),
            "eagl_symbols": _count_syms(names, "EAGL"),
            "audioqueue_symbols": _count_syms(names, "AudioQueue"),
            "mediaplayer": any("MPMoviePlayer" in n for n in names),
            "has_uikit_dylib": any("UIKit" in d for d in dylibs),
            "has_openal_dylib": any("OpenAL" in d for d in dylibs),
            "has_fmod_dylib": any("FMOD" in d.upper() or "fmod" in d for d in dylibs),
            "has_mediaplayer_dylib": any("MediaPlayer" in d for d in dylibs),
            "dylib_short": [d.rsplit("/", 1)[-1] for d in dylibs],
        }
    )
    return macho


def slot_budget(exe_size: int, text_size: int, png_rgba: int) -> dict:
    fits_a = exe_size <= SLOT_A and text_size <= SLOT_A
    return {
        "slot_a_gl_staging": SLOT_A,
        "slot_a_kb": SLOT_A // 1024,
        "exe_fits_slot_a": fits_a,
        "text_fits_slot_a": text_size <= SLOT_A,
        "slot": "B",
        "why_slot_b": (
            f"__TEXT {text_size} and the {exe_size}-byte image both exceed "
            f"slot A GL staging ({SLOT_A} bytes / 576 KB). Same as Mario / Temple Run."
        ),
        "heap_free_mb": PROBE_FREE,
        "heap_largest_mb": PROBE_LARGEST,
        "png_rgba": png_rgba,
        "rgba_vs_heap": (
            f"all-PNG RGBA {png_rgba} bytes is about "
            f"{png_rgba / (PROBE_FREE * 1024 * 1024):.1f}x Probe free "
            f"({PROBE_FREE} MB). Never decode the set. One 1024x1003 atlas "
            "is 4,108,288 RGBA (about half the 8.0 MB largest block) and "
            "breaks the 512 texture rule unless tiled."
        ),
    }


def render_host_png(dest: Path) -> Path:
    """Headless 240x432 portrait lawn. Procedural; no game pixels."""
    from PIL import Image, ImageDraw

    dest.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (240, 432), (32, 72, 28))
    draw = ImageDraw.Draw(im)
    draw.rectangle((0, 0, 240, 40), fill=(28, 20, 24))
    draw.text((8, 12), "PVZ HOST", fill=(232, 197, 71))
    # 5-row lawn, portrait. Seed bank along the top of the grass.
    draw.rectangle((0, 40, 240, 88), fill=(62, 44, 28))
    for i in range(8):
        x = 6 + i * 29
        draw.rectangle((x, 48, x + 26, 80), fill=(90, 140, 48))
        draw.ellipse((x + 6, 54, x + 20, 74), fill=(40, 110, 36))
    colors = ((52, 120, 44), (44, 104, 36), (58, 132, 48), (40, 96, 32), (50, 116, 42))
    for row, col in enumerate(colors):
        y0 = 92 + row * 52
        draw.rectangle((8, y0, 232, y0 + 48), fill=col)
        for col_i in range(5):
            x = 16 + col_i * 44
            draw.ellipse((x, y0 + 10, x + 28, y0 + 38), fill=(36, 90, 30))
    draw.rectangle((8, 360, 232, 420), fill=(20, 16, 24))
    draw.text((12, 368), "research only", fill=(138, 147, 168))
    draw.text((12, 384), "no apps add", fill=(138, 147, 168))
    draw.text((12, 400), "FMOD silent  skip mp4", fill=(90, 98, 112))
    im.save(dest)
    return dest


def write_inventory(app: Path, dest: Path, *, ipa: Optional[Path] = None) -> dict:
    exe = app / PVZ_EXE_NAME
    macho = classify_macho(exe)
    info: dict = {}
    pl = app / "Info.plist"
    if pl.is_file():
        try:
            info = plistlib.loads(pl.read_bytes())
        except Exception:
            info = {}
    assets = None
    if ipa is not None and Path(ipa).is_file():
        assets = catalog_ipa(Path(ipa))
    budget = slot_budget(
        int(macho.get("size") or 0),
        int(macho.get("text_size") or 0),
        int((assets or {}).get("png_rgba") or PINNED["png_rgba"]),
    )
    report = {
        "bundle_id": info.get("CFBundleIdentifier"),
        "version": info.get("CFBundleVersion"),
        "short_version": info.get("CFBundleShortVersionString"),
        "min_os": info.get("MinimumOSVersion"),
        "sdk": info.get("DTSDKName"),
        "orientation": info.get("UIInterfaceOrientation"),
        "exe": macho,
        "assets": assets,
        "stubs": list(STUBS),
        "sexy_surface": list(SEXY_SURFACE),
        "budget": budget,
        "verified": {
            "cryptid0": macho.get("cryptid") == 0,
            "armv6": bool(macho.get("armv6")),
            "exe_size": macho.get("size") == PVZ_EXE_SIZE,
            "has_uikit": bool(macho.get("has_uikit_dylib")),
            "no_openal": not macho.get("has_openal_dylib"),
            "no_fmod_dylib": not macho.get("has_fmod_dylib"),
            "has_mediaplayer": bool(macho.get("has_mediaplayer_dylib")),
        },
        "honest": (
            "Mach-O inventory, zip catalog (IHDR only), and the slot/heap "
            "budget are verified from the reference dump on the host. Unicorn "
            "heap compare, SexyApp recomp, .compiled inflate, PVRTC/RGB565 "
            "tiles, a nano .hbapp, and hardware are NOT verified. No apps add."
        ),
        "family": "recomp + Unicorn oracle (Temple Run), not the PC PvZ decomp",
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def oracle_status() -> dict:
    try:
        import unicorn  # noqa: F401

        have = True
        reason = (
            "unicorn import ok; PvZ original_oracle.py is not written. "
            "ObjC UIKit + C++ SexyApp is a larger stub surface than Temple Run."
        )
    except Exception as exc:
        have = False
        reason = f"unicorn not installed ({exc.__class__.__name__}); inventory-only"
    return {
        "have_unicorn": have,
        "reason": reason,
        "hooks": [s["name"] + ":" + s["policy"] for s in STUBS],
        "compare": "not run (no original_oracle.py for SexyApp yet)",
    }


def _print_pinned() -> None:
    print("pinned from the reference dump, sha256 " + PVZ_IPA_SHA256)
    print(f"  bundle     {PINNED['bundle_id']}  v{PINNED['version']} ({PINNED['short_version']})")
    print(f"  min OS     {PINNED['min_os']}  SDK {PINNED['sdk']}")
    print(f"  exe        {PINNED['exe_size']}  TEXT {PINNED['text_size']}  cryptid {PINNED['cryptid']}")
    print(f"  arch       thin ARMv6 (cputype {PINNED['cputype']}, subtype {PINNED['cpusubtype']})")
    print(
        f"  symbols    {PINNED['nsymbols']} ({PINNED['n_sect']} N_SECT); "
        f"Sexy {PINNED['sexy_symbols']}  Tod {PINNED['tod_symbols']}"
    )
    print(
        f"  zip        {PINNED['members']} members / {PINNED['unpacked']} unpacked"
    )
    print(
        f"  png        {PINNED['png_count']} files / {PINNED['png_bytes']}  "
        f"RGBA if decoded {PINNED['png_rgba']}"
    )
    print(
        f"  caf        {PINNED['caf_count']} files / {PINNED['caf_bytes']} (skip)"
    )
    print(
        f"  compiled   {PINNED['compiled_count']} / {PINNED['compiled_bytes']} (DEADFED4+zlib, stream)"
    )
    print(f"  mp4        credits.mp4 {PINNED['mp4_bytes']} (skip)")
    print(f"  keep       {PINNED['keep_after_skip_audio_video']} after skip caf/mp4")
    print(f"  atlas      {PINNED['atlas']}")
    print(f"  landscape  {PINNED['orientation']}")


def print_inventory(payload: Optional[Path] = None) -> int:
    print("Plants vs. Zombies iOS 1.0 (recomp + oracle analysis, research only)")
    print("Original .ipa stays local. Never commit it. No apps add path.")
    print()
    print("stubs")
    for row in STUBS:
        print(f"  - {row['name']}: {row['policy']}")
        print(f"      {row['note']}")
    print()
    print("SexyApp / TodLib surface")
    for line in SEXY_SURFACE:
        print(f"  - {line}")
    print()
    budget = slot_budget(PVZ_EXE_SIZE, PVZ_TEXT_SIZE, PINNED["png_rgba"])
    print("slot / heap")
    print(f"  slot A GL staging  {budget['slot_a_gl_staging']} (576 KB)")
    print(f"  chosen slot        slot {budget['slot']}  ({budget['why_slot_b']})")
    print(
        f"  Probe heap         {PROBE_FREE} MB free / {PROBE_LARGEST} MB largest"
    )
    print(f"  {budget['rgba_vs_heap']}")
    print()
    print("strategy (olievans123 recomp + Unicorn oracle)")
    print("  1. cryptid 0 thin ARMv6: Unicorn can map __TEXT the same way as Temple Run.")
    print("  2. Family is recomp, not the PC PvZ decomp (iOS SexyApp + EAGL + FMOD wrappers).")
    print("  3. Recomp Sexy:: + TodLib; stub UIKit/ObjC; FMOD silent; drop MediaPlayer.")
    print("  4. Oracle: same seed + scripted lawn taps; compare DataArray Plant/Zombie words.")
    print("  5. Assets: skip caf/mp4; 512-cap RGB565 tiles; inflate one .compiled at a time.")
    print("  6. Nano image will need slot B (TEXT 2.18 MB). No firmware patch.")
    print("  7. Stop before apps add until the host oracle matches.")
    print()
    print("command: " + PVZ_CMD)
    print("docs:    docs/GAMES.md")
    print()
    if payload is None:
        _print_pinned()
        print()
        print("pass --payload FILE.ipa to inventory a local dump (never committed)")
        frame = render_host_png(work_dir() / "host-frames" / "frame-000.png")
        print(f"host png (procedural, no game pixels): {frame}")
        print("honest: inventory + budget on the host. Recomp, oracle, hardware: not verified.")
        return 0
    src = Path(payload).expanduser().resolve()
    status = ipa_hash_status(src)
    if not status["present"]:
        raise Refused(f"ipa missing: {src}")
    if not status["matches_reference"]:
        print(
            "note: ipa sha256 is not the pinned 1.0 reference dump "
            f"({PVZ_IPA_SHA256[:16]}...); still accepting bundle {PVZ_BUNDLE_ID}"
        )
    app = assert_pvz_payload(src)
    report = write_inventory(app, work_dir() / "inventory.json", ipa=src)
    assets = report.get("assets") or {}
    exe = report["exe"]
    print()
    print(f"  bundle   {report.get('bundle_id')}  {report.get('version')}")
    print(f"  exe      {exe.get('size')} cryptid {exe.get('cryptid')} TEXT {exe.get('text_size')}")
    print(
        f"  symbols  {exe.get('nsymbols')}  Sexy {exe.get('sexy_symbols')}  "
        f"Tod {exe.get('tod_symbols')}"
    )
    print(
        f"  dylibs   {', '.join(exe.get('dylib_short') or [])}"
    )
    print(
        f"  assets   png {assets.get('png_count')} rgba {assets.get('png_rgba')}  "
        f"caf {assets.get('caf_count')}  compiled {assets.get('compiled_count')}  "
        f"mp4 {assets.get('mp4_bytes')}"
    )
    print(f"  plan     {assets.get('plan')}")
    oracle = oracle_status()
    (work_dir() / "oracle.json").write_text(
        json.dumps(oracle, indent=2) + "\n", encoding="utf-8"
    )
    frame = render_host_png(work_dir() / "host-frames" / "frame-000.png")
    print(f"  oracle   {oracle['reason']}")
    print(f"  host png {frame} (procedural; game atlases not copied)")
    print(f"  honest   {report['honest']}")
    return 0
