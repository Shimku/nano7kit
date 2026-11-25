"""GTA 6 (Mangrove Key): first-party RAW_SURFACE joke app. No payload."""

from __future__ import annotations

from pathlib import Path

from .compat import root

NAME = "GTA 6"
CMD = "./start nabase add 'GTA 6'"
FAT_DATA = "/Apps/Data/GTA6"
FOLDER = "gta6"
BLOCK_MAX = 0x40000
BLOCK_MIN = 0x18000
AFTER_MIN = 0x8000
SLOP = 0x4000


def game_dir() -> Path:
    return root() / "nanoapps" / "apps" / FOLDER


def pick_block(free_b: int, largest: int) -> int:
    if largest > BLOCK_MAX + SLOP:
        size = BLOCK_MAX
    else:
        size = ((largest - SLOP) & ~0xFFFF) if largest > SLOP else 0
    if free_b > AFTER_MIN:
        cap = (free_b - AFTER_MIN) & ~0xFFFF
        if size > cap:
            size = cap
    else:
        size = 0
    if largest < BLOCK_MIN + SLOP or size < BLOCK_MIN:
        return 0
    return size


def heap_fit(free_b: int, largest: int) -> dict:
    size = pick_block(free_b, largest)
    return {
        "free": free_b,
        "largest": largest,
        "block": size,
        "verdict": "FITS" if size else "NO_FIT",
    }
