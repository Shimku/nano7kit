#!/usr/bin/env python3
"""Run the hosted PebbleOS .hbapp in Unicorn under device-like host conditions.

Loads the image the way the resident does (HRL1 plus the ABS32 table),
calls payload_entry, then the raw-surface frame function. The OS heap,
file system, clock and buttons are the same hooks the NanoApps SDK uses
on the nano 7G. Writes stay under /Apps/Data/PebbleOS/.

Usage:
  emu.py APP.hbapp OUTDIR [script] [--os-blocks 2.0,1.0,0.9] [--fs-dir DIR]
  script lines: `frames N`, `shot NAME`
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(KIT))

from pocketjs.emu import (  # noqa: E402
    BASE,
    FB,
    H,
    SHARED,
    STACK_TOP,
    W,
    Emu,
    relocated,
)

DATA_PREFIX = "/Apps/Data/PebbleOS/"


class PebbleEmu(Emu):
    """PocketJS dry run with PebbleOS data paths and an A-profile CPU close to the A5."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            from unicorn.arm_const import UC_CPU_ARM_CORTEX_A7

            # Unicorn has no Cortex-A5 model. A7 is the same ARMv7-A
            # family the image was built for (Thumb-2, VFPv4-D16 / soft).
            self.uc.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_A7)
        except Exception:
            pass
        self.add_parents(DATA_PREFIX + "flash/x")
        self.dirs.add("/Apps")
        self.dirs.add("/Apps/Data")
        self.dirs.add("/Apps/Data/PebbleOS")
        self.dirs.add("/Apps/Data/PebbleOS/flash")

    def writable(self, path):
        if not str(path).startswith(DATA_PREFIX):
            raise RuntimeError(f"the app wrote outside {DATA_PREFIX}: {path}")


def load_fs_dir(host: Path) -> tuple[dict[str, bytearray], set[str]]:
    data: dict[str, bytearray] = {}
    dirs: set[str] = set()
    if not host.is_dir():
        return data, dirs
    for f in sorted(host.rglob("*")):
        rel = f.relative_to(host).as_posix()
        if f.is_dir():
            dirs.add(DATA_PREFIX + rel)
        elif f.is_file():
            data[DATA_PREFIX + rel] = bytearray(f.read_bytes())
    return data, dirs


def classify_frame(raw: bytes) -> str:
    """Name the 240x432 BGRA frame: low-memory, stub, or a painted panel."""
    if len(raw) < W * H * 4:
        return "short"
    bg = raw[0:4]
    # Low-memory fill is 0x101418 with alpha 0xff, stored BGRA.
    if bg == bytes((0x18, 0x14, 0x10, 0xFF)):
        # Look for the red "NOT ENOUGH MEMORY" ink (0xff5555).
        if bytes((0x55, 0x55, 0xFF, 0xFF)) in raw:
            return "low-memory"
        return "low-memory-plain"
    # Stub status fill is 0x0a0a0a; orange title 0xff4700.
    if bg == bytes((0x0A, 0x0A, 0x0A, 0xFF)) and bytes((0x00, 0x47, 0xFF, 0xFF)) in raw:
        return "stub"
    # The 200x228 panel is centred at (20, 102).
    x0, y0, pw, ph = 20, 102, 200, 228
    colours = set()
    for y in range(y0, y0 + ph):
        row = raw[(y * W + x0) * 4 : (y * W + x0 + pw) * 4]
        for i in range(0, len(row), 4):
            colours.add(row[i : i + 4])
            if len(colours) > 8:
                return "watchface"
    if len(colours) >= 3:
        return "watchface"
    return f"panel-{len(colours)}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("app")
    ap.add_argument("outdir")
    ap.add_argument("script", nargs="?")
    ap.add_argument("--largest", type=int, default=3 * 1024 * 1024)
    ap.add_argument(
        "--os-blocks",
        default=None,
        help="OS free blocks in MiB, for example 2.0,1.0,0.9 (3.9 free / 2.0 largest)",
    )
    ap.add_argument("--fs-dir", default=None, help="host tree copied to /Apps/Data/PebbleOS/")
    ap.add_argument("--heartbeat-us", type=int, default=20000)
    a = ap.parse_args(argv)

    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    script = Path(a.script).read_text() if a.script else "90\nshot watchface\n"
    regions = [int(float(v) * 1024 * 1024) for v in a.os_blocks.split(",")] if a.os_blocks else None
    data, extra_dirs = load_fs_dir(Path(a.fs_dir)) if a.fs_dir else ({}, set())
    emu = PebbleEmu(a.app, a.largest, (), a.heartbeat_us, regions, None, data)
    emu.dirs.update(extra_dirs)
    if regions:
        print(
            f"OS heap model: blocks {a.os_blocks} MiB; image arena {emu.image_span:,} bytes; "
            f"then free {emu.heap.free_total():,}, largest {emu.heap.largest():,}"
        )

    frame_fn = emu.call(BASE + emu.entry, [0, FB, W, H, SHARED])
    print(f"stack: payload_entry {STACK_TOP - 16 - emu.min_sp_call:,} bytes")
    if not frame_fn:
        print("payload_entry refused the launch")
        raw = bytes(emu.uc.mem_read(FB, W * H * 4))
        kind = classify_frame(raw)
        emu.png(out / "refused.png")
        print(f"refused frame is {kind}")
        return 1

    frames = 0
    for line in script.splitlines():
        words = line.split("#")[0].split()
        if not words:
            continue
        if words[0] == "shot":
            path = out / f"{words[1]}.png"
            emu.png(path)
            raw = bytes(emu.uc.mem_read(FB, W * H * 4))
            kind = classify_frame(raw)
            print(f"{words[1]}: frame {frames} ({kind})")
            (out / f"{words[1]}.kind").write_text(kind + "\n")
            continue
        n = int(words[0])
        for _ in range(n):
            emu.call(frame_fn, [])
            frames += 1

    if emu.writes or emu.data:
        print(
            f"data.fs: {emu.writes} files written ({emu.bytes_written:,} bytes); "
            f"{len(emu.data)} paths under {DATA_PREFIX}"
        )
    print(f"OS heap: blocks held {len(emu.heap.blocks)} ({sum(emu.heap.blocks.values()):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
