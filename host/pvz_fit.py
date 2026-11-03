"""Plants vs. Zombies 1.0: does a Temple-Run-style recomp fit the nano's heap?

`./start apps add PvZ --ipa FILE --plan` answers that from the user's own
.ipa (read-only, nothing is built, nothing touches the nano). Without
--plan, apps add PvZ prints the same numbers and refuses: on the MKN52's
slot B heap (Probe about 6.4 MB free, 4.0 MB largest block) the port cannot
fit, so there is nothing to build yet.

Why it does not fit (measured on the reference dump, com.popcap.PvZ build 524):

* code: the stripped ARM __text is 2,013,860 bytes. Recursive descent from
  the entry point and every code pointer in __DATA (vtables included, as a
  recompiler must keep every virtual method) reaches at least 225,113 ARM
  instructions. Temple Run's recompiled engine measured 593,632 bytes of
  nano code for 68,700 instructions (8.64 bytes each), so PvZ's code image
  is at least about 1.9 MB.
* guest memory: the original __DATA segment (data, bss, common) is
  1,519,616 bytes and must sit in the one guest block.
* animation data: PopCap's .compiled reanim files are memory images that
  stay loaded once inflated. The main menu (selectorscreen) is 1.9 MB, the
  basic zombie 1.19 MB, Crazy Dave 0.84 MB. A first level with the menu
  behind it needs about 3.3 MB of them.
* the GL driver needs its own heap headroom (Temple Run runs with about
  1.25 MB left; the Mario Kart notes reboot under about 3 MB).

The guest block alone (data + reanims) is over the 4.0 MB largest block,
and the total is over the 6.4 MB free, before the game allocates a lawn.
"""

from __future__ import annotations

import struct
import zipfile
import zlib
from pathlib import Path
from typing import Dict, List, Optional

from .compat import Refused, sha256_file

PVZ_NAMES = frozenset({"pvz", "plantsvszombies", "plants-vs-zombies", "plants", "plantsvs.zombies"})

TR_BYTES_PER_INSN = 593_632 / 68_700      # Temple Run recomp, arm-none-eabi-gcc -O2
REACHABLE_INSNS = 225_113                  # lower bound, see module doc
DATA_SEGMENT = 1_519_616                   # __DATA vmsize, build 524
GL_HEADROOM = 1_250_000                    # what Temple Run runs on (418 triangles)
TEXTURES_MIN = 600_000                     # PlantsZombies + Dialog + lawn at PVRTC 512, rough
PROBE_FREE = int(6.4 * (1 << 20))
PROBE_LARGEST = 4 << 20
# Lean residents free 327,680 bytes of boot heap (icon LRU 20 x 56 KB ->
# 16 x 50 KB). The largest block only grows if those buffers border it.
LEAN_SAVED = 20 * 0xE000 - 16 * 0xC800
LEAN_FREE = PROBE_FREE + LEAN_SAVED
LEAN_LARGEST_BEST = PROBE_LARGEST + LEAN_SAVED

# reanims a first level needs, with the selector screen still loaded behind it
FIRST_LEVEL_REANIMS = (
    "selectorscreen", "zombie", "peashootersingle", "sunflower",
    "lawnmower", "sun", "zombie_flagpole",
)

PINNED_REANIM = {
    "selectorscreen": 1_899_618,
    "zombie": 1_187_066,
    "crazydave": 836_273,
    "peashootersingle": 93_782,
    "lawnmower": 37_030,
    "sunflower": 14_959,
    "sun": 2_292,
    "zombie_flagpole": 1_554,
}

BUNDLE_ID = "com.popcap.PvZ"


def _reanim_sizes(ipa: Path) -> Dict[str, int]:
    """Inflate each reanim .compiled (DEADFED4, size, zlib) one at a time; keep only sizes."""
    out: Dict[str, int] = {}
    with zipfile.ZipFile(ipa) as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if "/reanim/" not in name.lower() or not name.lower().endswith(".reanim.compiled"):
                continue
            blob = zf.read(info)
            if len(blob) < 8 or struct.unpack_from("<I", blob, 0)[0] != 0xDEADFED4:
                continue
            try:
                size = len(zlib.decompress(blob[8:]))
            except zlib.error:
                continue
            out[name.rsplit("/", 1)[-1][: -len(".reanim.compiled")].lower()] = size
    return out


def _bundle_id(ipa: Path) -> Optional[str]:
    import plistlib

    with zipfile.ZipFile(ipa) as zf:
        for info in zf.infolist():
            parts = info.filename.split("/")
            if len(parts) == 3 and parts[0] == "Payload" and parts[2] == "Info.plist":
                try:
                    return plistlib.loads(zf.read(info)).get("CFBundleIdentifier")
                except Exception:
                    return None
    return None


def model(ipa: Optional[Path] = None) -> dict:
    reanim = dict(PINNED_REANIM)
    source = "pinned (build 524)"
    if ipa is not None:
        ipa = Path(ipa).expanduser()
        if not ipa.is_file():
            raise Refused(f"ipa missing: {ipa}")
        try:
            bid = _bundle_id(ipa)
        except zipfile.BadZipFile as exc:
            raise Refused(f"{ipa} is not an ipa ({exc})") from exc
        if bid != BUNDLE_ID:
            raise Refused(f"{ipa} is {bid or 'not an app bundle'}, not Plants vs. Zombies ({BUNDLE_ID})")
        measured = _reanim_sizes(ipa)
        if measured:
            reanim = measured
            source = "measured from your ipa"
    code = int(REACHABLE_INSNS * TR_BYTES_PER_INSN)
    level = sum(reanim.get(k, 0) for k in FIRST_LEVEL_REANIMS)
    guest = DATA_SEGMENT + level
    total = code + guest + TEXTURES_MIN + GL_HEADROOM
    # Best case a port could reach: guest malloc redirected to the nano heap
    # (so only __DATA must be one block) and the menu reanim freed before the
    # lawn loads. Still a lower bound: later levels need more zombie reanims.
    menu = reanim.get("selectorscreen", 0)
    best_total = total - menu
    best_block = max(DATA_SEGMENT, max(reanim.get(k, 0) for k in FIRST_LEVEL_REANIMS if k != "selectorscreen"))
    return {
        "best_total": best_total,
        "best_block": best_block,
        "menu_reanim": menu,
        "source": source,
        "code": code,
        "data_segment": DATA_SEGMENT,
        "reanim_first_level": level,
        "reanim_all": sum(reanim.values()),
        "reanim_top": sorted(reanim.items(), key=lambda kv: -kv[1])[:4],
        "guest_block": guest,
        "textures": TEXTURES_MIN,
        "gl_headroom": GL_HEADROOM,
        "total": total,
    }


def verdict(m: dict, heap_free: int, largest: int) -> str:
    if m["guest_block"] > largest or m["total"] > heap_free:
        return "NO_FIT"
    return "UNKNOWN"   # never seen on any nano heap; would still need the port written


def _mb(n: int) -> str:
    return f"{n / (1 << 20):.2f} MB"


def plan_lines(ipa: Optional[Path], heap_free: Optional[int] = None, largest: Optional[int] = None) -> List[str]:
    hf = heap_free if heap_free is not None else PROBE_FREE
    lg = largest if largest is not None else PROBE_LARGEST
    lines = ["nano7kit apps add PvZ --plan (read-only: nothing is built, nothing is written)"]
    if ipa is not None:
        lines.append(f"    payload   {Path(ipa).expanduser()} sha256 {sha256_file(Path(ipa).expanduser())[:16]}...")
    m = model(ipa)
    lines += [
        f"    code      at least {_mb(m['code'])}: 225,113 reachable ARM instructions at Temple Run's",
        "              8.64 bytes each (stripped binary, 2.0 MB __text)",
        f"    data      {_mb(m['data_segment'])} original __DATA (data, bss, common), in the guest block",
        f"    reanims   {_mb(m['reanim_first_level'])} for menu + first level ({m['source']});",
        "              largest: " + ", ".join(f"{k} {_mb(v)}" for k, v in m["reanim_top"]),
        f"    guest     {_mb(m['guest_block'])} in one block (largest block {_mb(lg)})",
        f"    GL        {_mb(m['textures'])} textures + {_mb(m['gl_headroom'])} driver headroom",
        f"    total     {_mb(m['total'])} needed, heap free {_mb(hf)}",
    ]
    best_margin = hf - m["best_total"]
    lines += [
        f"    best case split guest allocations + menu reanim ({_mb(m['menu_reanim'])}) freed in-level:",
        f"              {_mb(m['best_total'])} total, biggest single block {_mb(m['best_block'])}, "
        f"margin {_mb(best_margin)}" + ("" if best_margin >= 0 else " (short)"),
        "              lower bound: no lawn, sound, or second zombie type counted yet",
    ]
    v = verdict(m, hf, lg)
    if v == "NO_FIT":
        lines.append("    verdict   NO_FIT: PvZ's engine keeps its animation data in RAM and it does not")
        lines.append("              fit beside its own code on this iPod. Nothing to build.")
        lines.append("PLAN_REFUSED")
    else:
        lines.append("    verdict   numbers fit on paper, but the PvZ port is not written. Nothing to build.")
        lines.append("PLAN_REFUSED")
    return lines


def refuse_add(ipa: Optional[Path], heap_free: Optional[int] = None, largest: Optional[int] = None) -> None:
    lines = plan_lines(ipa, heap_free, largest)
    print("\n".join(lines[:-1]))
    raise Refused(
        "apps add PvZ: Plants vs. Zombies 1.0 does not fit the nano's heap (see the numbers above). "
        "Nothing was built or copied."
    )
