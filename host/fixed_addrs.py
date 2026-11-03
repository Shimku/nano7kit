"""Find the old NanoApps SDK's fixed DRAM addresses in a built .hbapp.

On the untethered nano 7G, 0x0910xxxx-0x094xxxxx is live OS heap. The old
SDK and a few apps wrote to fixed addresses there (trace ring, hb_fs
buffers, touch mailbox, screenshot flags, canvas and book buffers). The
current SDK keeps that state in the resident's shared block (hb_shared.h)
or in the app's own heap arena instead. Host only; reads files, no NAND.

A relocatable .hbapp is linked at base 0, so its own pointers are small
and listed in the reloc table. Any other aligned word on one of the legacy
pages below, or a Thumb-2 MOVT loading one of their high halves, is a
fixed-address reference. Words are only flagged when 4-byte aligned, since
every legacy buffer and flag address was.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Dict, List, Tuple, Union

HRL1_MAGIC = 0x314C5248  # 'HRL1'

# 4 KiB page -> what the old SDK or app kept there.
LEGACY_PAGES: Dict[int, str] = {
    0x09118000: "hb_fs one-shot cache (old SDK)",
    0x0911A000: "hb_fs stream object and cache (old SDK)",
    0x09120000: "hb_trace ring (old SDK)",
    0x09135000: "touch mailbox 0x09135f40 / screenshot flags 0x09135fec-ff0 / icon state 0x09135b00",
    0x092E0000: "fixed canvas buffer (old LVGL games)",
    0x09400000: "fixed Books text buffer",
}
LEGACY_MOVT = {page >> 16 for page in LEGACY_PAGES}


class NotRelocatable(ValueError):
    pass


def parse_hbapp(data: bytes) -> Tuple[bytes, set]:
    if len(data) < 24:
        raise NotRelocatable("too short for an HRL1 header")
    magic, _entry, image_size, _span, reloc_count, _align = struct.unpack_from("<6I", data, 0)
    if magic != HRL1_MAGIC:
        raise NotRelocatable("not an HRL1 relocatable .hbapp")
    end = 24 + image_size + 4 * reloc_count
    if end > len(data):
        raise NotRelocatable("truncated HRL1 image or reloc table")
    image = data[24 : 24 + image_size]
    relocs = set(struct.unpack_from(f"<{reloc_count}I", data, 24 + image_size))
    return image, relocs


def legacy_hits(data: Union[bytes, Path, str]) -> List[dict]:
    """Return fixed-address references to the legacy pages (empty = clean)."""
    if not isinstance(data, (bytes, bytearray)):
        data = Path(data).read_bytes()
    image, relocs = parse_hbapp(bytes(data))
    hits: List[dict] = []
    for off in range(0, len(image) - 3, 4):
        if off in relocs:
            continue
        (value,) = struct.unpack_from("<I", image, off)
        page = value & ~0xFFF
        # Buffer and flag addresses are 4-byte aligned; Thumb-2 code that
        # happens to look like one of these pages usually is not.
        if page in LEGACY_PAGES and not value & 3:
            hits.append({"offset": off, "value": value, "kind": "word", "what": LEGACY_PAGES[page]})
    for off in range(0, len(image) - 3, 2):
        h1, h2 = struct.unpack_from("<HH", image, off)
        if (h1 & 0xFBF0) != 0xF2C0 or (h2 & 0x8000):
            continue
        imm = ((h1 & 0xF) << 12) | (((h1 >> 10) & 1) << 11) | (((h2 >> 12) & 7) << 8) | (h2 & 0xFF)
        if imm in LEGACY_MOVT:
            page = next(p for p in LEGACY_PAGES if p >> 16 == imm)
            hits.append({"offset": off, "value": imm << 16, "kind": "movt", "what": LEGACY_PAGES[page]})
    return hits


def describe(hits: List[dict], limit: int = 4) -> str:
    seen = []
    for h in hits:
        tag = f"{h['value']:#010x} ({h['what']})"
        if tag not in seen:
            seen.append(tag)
    more = "" if len(seen) <= limit else f", +{len(seen) - limit} more"
    return ", ".join(seen[:limit]) + more
