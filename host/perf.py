"""Host-side resident / heap / staging readout and Blocks A/B commands.

Live OS heap only exists on the nano (Probe). This command prints the
pinned Probe numbers, slot A staging, hbapp sizes, and Disk Mode files
when a volume is visible. It does not flash and it does not rewrite NAND.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from . import const
from .compat import Refused
from .ports import SLOT_A_GL_STAGING

PERF_CMD = "./start perf"
REMOVE_BLOCKS = "./start apps remove Blocks --keep-files"
ADD_BLOCKS = "./start apps add Blocks"
YIELD_BLOCKS = "./start apps yield Blocks --i-accept-untested-pack"
PAINT_ADD = "./start apps add Paint"
RESTORE = "./start apps lasttrace && ./start apps restore-tested"
BLOCKS_SAVE = "/Apps/Data/Blocks/world.bin"
PROBE_TXT = "/Apps/Data/Probe/probe.txt"
PROBE_FREE_MB = 8.7
PROBE_LARGEST_MB = 8.0
FB_BYTES = 240 * 432 * 4  # resident s_fb, XRGB8888


def _size_of(name: str) -> int:
    from .app_catalog import catalog

    row = next((r for r in catalog() if r["name"] == name), None)
    if not row:
        return 0
    n = int(row.get("size") or 0)
    if n:
        return n
    rel = (const.TESTED_APPS.get(name) or {}).get("executable")
    if rel:
        p = const.artifact(rel)
        if p.is_file():
            return p.stat().st_size
    return 0


def blob_sizes() -> Dict[str, int]:
    return {
        "Blocks": _size_of("Blocks"),
        "Game Boy": _size_of("Game Boy"),
        "Probe": _size_of("Probe"),
        "Paint": _size_of("Paint"),
        "Tetris": _size_of("Tetris"),
        "Snake": _size_of("Snake"),
    }


def parse_probe_txt(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.upper().startswith("HEAP FREE"):
            out["free"] = line.split(None, 2)[-1] if line.count(" ") >= 2 else line
        elif line.upper().startswith("LARGEST"):
            out["largest"] = line.split(None, 1)[-1]
        elif "ALLOC" in line.upper():
            out["alloc"] = line
        elif "MHZ" in line.upper():
            out["cpu"] = line
        elif "MEMCPY" in line.upper() or "MB/S" in line.upper():
            out["copy"] = line
    return out


def ab_steps() -> List[str]:
    return [
        "Blocks A/B (Paint lag). Disk Mode, no NAND.",
        "  " + PERF_CMD,
        "  " + PAINT_ADD + "   # if Paint is not on Home; then Sleep/Wake+Home; time first-touch lag",
        "  " + REMOVE_BLOCKS,
        "  Sleep/Wake+Home. Open Paint. Time lag again.",
        "  " + ADD_BLOCKS + "   # re-add; " + BLOCKS_SAVE + " stays",
        "  opt-in hide (files stay): " + YIELD_BLOCKS,
        "  If it loops: " + RESTORE,
    ]


def _try_live() -> Dict[str, object]:
    info: Dict[str, object] = {
        "present": False,
        "pack_names": [],
        "probe_txt": "",
        "probe_parsed": {},
        "blocks_save_bytes": None,
        "lasttrace_tags": [],
        "note": "no iPod mass-storage volume",
    }
    try:
        from .device import find_nanos

        found = find_nanos()
    except Exception as exc:
        info["note"] = f"find_nanos failed ({exc.__class__.__name__})"
        return info
    if not found:
        return info
    if len(found) > 1:
        info["note"] = "more than one iPod; unplug extras"
        return info
    nano = found[0]
    try:
        from .apps import (
            decode_lasttrace,
            home_pack_fat,
            pack_app_names,
            _ops_for_nano,
        )

        ops, _mounted = _ops_for_nano(nano)
        info["present"] = True
        info["note"] = "Disk Mode volume visible"
        pack_fat = home_pack_fat(ops)
        blob = ops.read_bytes(pack_fat)
        if blob:
            info["pack_names"] = pack_app_names(blob)
            info["pack_fat"] = pack_fat
            info["pack_bytes"] = len(blob)
        probe = ops.read_bytes(PROBE_TXT)
        if probe:
            text = probe.decode("utf-8", "replace")
            info["probe_txt"] = text
            info["probe_parsed"] = parse_probe_txt(text)
        save = ops.read_bytes(BLOCKS_SAVE)
        if save:
            info["blocks_save_bytes"] = len(save)
        lt = ops.read_bytes("/Apps/Startup/lasttrace.bin")
        if lt:
            try:
                decoded = decode_lasttrace(lt)
                info["lasttrace_tags"] = list(decoded.get("tags") or [])
            except Refused as exc:
                info["lasttrace_error"] = str(exc)
    except Exception as exc:
        info["note"] = f"volume present but readout failed ({exc.__class__.__name__}: {exc})"
        info["present"] = True
    return info


def as_text(*, live: Optional[dict] = None) -> str:
    sizes = blob_sizes()
    live = live if live is not None else _try_live()
    lines = [
        "nano7kit perf (host readout, no NAND)",
        "Live heap is Probe on the nano. Numbers below are pinned MKN52",
        "Probe plus this tree's blobs, unless a Disk Mode volume is visible.",
        "",
        "model (slot A silver_resident)",
        "  one Home app at a time. Home/exit stops the 8 ms heartbeat and",
        "  clears the frame fn. Blocks does not tick behind Paint.",
        f"  staging     {SLOT_A_GL_STAGING} bytes ({SLOT_A_GL_STAGING // 1024} KB)",
        f"  resident fb {FB_BYTES} bytes (240x432 XRGB8888, kept for the deploy)",
        f"  Probe pin   {PROBE_FREE_MB} MB free / {PROBE_LARGEST_MB} MB largest",
        "",
        "blobs in this tree",
    ]
    for name, n in sizes.items():
        kind = "GL" if name == "Blocks" else "RAW"
        extra = ""
        if name in {"Tetris", "Snake"}:
            kind = "LVGL-sized"
        lines.append(f"  {name:<10} {n:>8}  {kind}{extra}")
    lines.append("")
    lines.append("Disk Mode files")
    if live.get("present") and live.get("pack_names"):
        lines.append(
            "  OK       pack  "
            + str(live.get("pack_fat") or "/Apps/AllApps.pack")
            + "  "
            + ", ".join(str(n) for n in (live.get("pack_names") or []))
        )
    else:
        lines.append(
            "  MISSING  pack  (no volume; "
            + str(live.get("note") or "unplugged")
            + ")"
        )
    parsed = live.get("probe_parsed") or {}
    if parsed:
        lines.append(
            "  OK       probe.txt  free "
            + str(parsed.get("free") or "?")
            + "  largest "
            + str(parsed.get("largest") or "?")
        )
    else:
        lines.append(
            f"  MISSING  {PROBE_TXT}  (open Probe on the nano, then Disk Mode)"
        )
    save_n = live.get("blocks_save_bytes")
    if save_n:
        lines.append(f"  OK       {BLOCKS_SAVE}  {save_n} bytes (survives reboot)")
    else:
        lines.append(
            f"  MISSING  {BLOCKS_SAVE}  (created after you edit in Blocks)"
        )
    tags = live.get("lasttrace_tags") or []
    if tags:
        interesting = [
            t
            for t in tags
            if any(
                s in t
                for s in ("XEXIT", "TIMR", "BLKSAVE", "PROBE", "SURF", "GLAPP", "HBTAP")
            )
        ]
        lines.append(
            "  OK       lasttrace  "
            + (" ".join(interesting[-12:]) if interesting else " ".join(tags[-8:]))
        )
    else:
        lines.append("  MISSING  lasttrace.bin  (previous-boot hb_trace)")
    lines.append("")
    lines.extend(ab_steps())
    lines.append("  world.bin is not deleted by remove/yield. No flash.")
    return "\n".join(lines)


def print_status() -> int:
    print(as_text())
    return 0
