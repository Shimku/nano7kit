"""MSE / IMG1 helpers for the nano 7G resource section.

The hardware-tested firmware is a 193,114,112-byte MSE. Section bodies live at
``dev_offset + 0x1000`` for ``length`` bytes. The resource payload is named
``crsr`` (little-endian ``rsrc``). Its IMG1 uses 0x400 bytes of header+padding,
then a FAT16 volume of ``body_length`` bytes, then a certificate.

These helpers never write a device. They exist so theme combine can splice a
same-size FAT volume back into the tested MSE without moving OSOS or the
section table.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any, Optional

from .compat import Refused, sha256_bytes, sha256_file


MSE_HEADER = 0x5000
SLOT_SIZE = 40
SLOT_COUNT = 16
IMG1_PREFIX = 0x400
NANO7_IMG1_MAGIC = b"87402.0"
PATCHED_FORMAT = 0x03
STOCK_FORMAT = 0x04
RSRC_NAME = b"crsr"


def parse_sections(mse: bytes) -> list[dict[str, Any]]:
    if len(mse) < MSE_HEADER + SLOT_COUNT * SLOT_SIZE:
        raise Refused("Firmware.MSE is too short for its section table")
    sections: list[dict[str, Any]] = []
    for index in range(SLOT_COUNT):
        entry = mse[MSE_HEADER + index * SLOT_SIZE : MSE_HEADER + (index + 1) * SLOT_SIZE]
        tag, name = entry[:4], entry[4:8]
        if tag == b"\0\0\0\0":
            continue
        unk, dev_offset, length, address, entry_offset, unk2, version, load_addr = (
            struct.unpack_from("<8I", entry, 8)
        )
        body_start = dev_offset + 0x1000
        body_end = body_start + length
        if body_end > len(mse):
            raise Refused(f"MSE section {name!r} exceeds Firmware.MSE")
        sections.append(
            {
                "index": index,
                "tag": tag,
                "name": name,
                "name_ascii": name[::-1].decode("ascii", errors="replace"),
                "unk": unk,
                "dev_offset": dev_offset,
                "length": length,
                "address": address,
                "entry_offset": entry_offset,
                "unk2": unk2,
                "version": version,
                "load_addr": load_addr,
                "head_start": dev_offset,
                "body_start": body_start,
                "body_end": body_end,
                "slot": bytes(entry),
            }
        )
    return sections


def find_section(mse: bytes, name: bytes) -> dict[str, Any]:
    hits = [section for section in parse_sections(mse) if section["name"] == name]
    if len(hits) != 1:
        raise Refused(f"Firmware.MSE has {len(hits)} sections named {name!r}")
    return hits[0]


def parse_img1(img1: bytes, *, require_patched: bool = True) -> dict[str, Any]:
    if len(img1) < IMG1_PREFIX:
        raise Refused("IMG1 is shorter than the nano 7G 0x400 header/padding")
    if img1[:7] != NANO7_IMG1_MAGIC:
        raise Refused(f"IMG1 magic is {img1[:8]!r}, not 87402.0")
    format_byte = img1[7]
    if require_patched and format_byte != PATCHED_FORMAT:
        raise Refused(
            f"IMG1 format byte is 0x{format_byte:02x}, expected patched 0x03 "
            "(stock 0x04 is signature-checked)"
        )
    body_length, data_length, cert_offset, cert_length = struct.unpack_from("<4I", img1, 12)
    if IMG1_PREFIX + body_length > len(img1):
        raise Refused("IMG1 body exceeds the section")
    cert_start = IMG1_PREFIX + body_length
    # ipodhax treats the 0x80 bytes after the body as sign.bin, then cert.
    sign = img1[cert_start : cert_start + 0x80]
    cert = img1[cert_start + 0x80 : cert_start + 0x80 + cert_length] if cert_length else img1[cert_start:]
    return {
        "magic": img1[:7],
        "format": format_byte,
        "entrypoint": struct.unpack_from("<I", img1, 8)[0],
        "body_length": body_length,
        "data_length": data_length,
        "cert_offset": cert_offset,
        "cert_length": cert_length,
        "salt": img1[28:60],
        "unk0": struct.unpack_from("<H", img1, 60)[0],
        "unk1": struct.unpack_from("<H", img1, 62)[0],
        "header_signature": img1[64:80],
        "header_leftover": img1[80:84],
        "head": img1[:0x54],
        "padding": img1[0x54:IMG1_PREFIX],
        "body": img1[IMG1_PREFIX : IMG1_PREFIX + body_length],
        "sign": sign,
        "cert": cert,
        "img1_len": len(img1),
    }


def extract_rsrc_fat(
    mse: bytes, *, require_patched: bool = True
) -> tuple[bytes, dict[str, Any]]:
    """Return the rsrc FAT volume.

    ``require_patched=True`` (default) is for the Helvetica carrier (IMG1
    format 0x03). Community themed IPSWs are usually stock-signed 0x04;
    pass False to read art from those images. Never splice a 0x04 rsrc
    into the Helvetica MSE: copy files out of the FAT instead.
    """
    section = find_section(mse, RSRC_NAME)
    img1 = mse[section["body_start"] : section["body_end"]]
    parsed = parse_img1(img1, require_patched=require_patched)
    details = {
        "section": section,
        "img1": {
            key: parsed[key]
            for key in (
                "format",
                "entrypoint",
                "body_length",
                "data_length",
                "cert_offset",
                "cert_length",
                "header_signature",
                "header_leftover",
                "img1_len",
            )
        },
        "fat_sha256": sha256_bytes(parsed["body"]),
        "img1_head_sha256": sha256_bytes(parsed["head"]),
        "img1_padding_sha256": sha256_bytes(parsed["padding"]),
        "img1_sign_sha256": sha256_bytes(parsed["sign"]),
        "img1_cert_sha256": sha256_bytes(parsed["cert"]),
    }
    details["img1"]["header_signature_hex"] = parsed["header_signature"].hex()
    return parsed["body"], details


def splice_rsrc_fat(mse: bytes, new_fat: bytes) -> bytes:
    """Replace the rsrc FAT volume. Volume size must match the IMG1 body_length.

    Keeping body_length identical leaves the IMG1 header, certificate, section
    table, OSOS, and every byte outside the FAT volume untouched. ipod_sun's
    Helvetica patch works the same way: pyfatfs edits the existing volume.
    """
    fat, details = extract_rsrc_fat(mse)
    body_length = details["img1"]["body_length"]
    if len(new_fat) != body_length:
        raise Refused(
            f"themed rsrc FAT is {len(new_fat)} bytes; IMG1 body_length is "
            f"{body_length}. Refusing to rewrite IMG1 headers or shift MSE "
            "sections. Inject into the existing volume (same size) instead."
        )
    if len(new_fat) != len(fat):
        raise Refused("extracted FAT length does not match IMG1 body_length")
    start = details["section"]["body_start"] + IMG1_PREFIX
    out = bytearray(mse)
    out[start : start + body_length] = new_fat
    return bytes(out)


def region_map(mse: bytes) -> dict[str, tuple[int, int]]:
    """Named byte ranges used by the offline combine diff."""
    sections = parse_sections(mse)
    regions = {
        "mse_header": (0, MSE_HEADER),
        "section_table": (MSE_HEADER, MSE_HEADER + SLOT_COUNT * SLOT_SIZE),
    }
    for section in sections:
        key = section["name_ascii"]
        regions[f"{key}_head"] = (section["head_start"], section["body_start"])
        regions[f"{key}_body"] = (section["body_start"], section["body_end"])
        if section["name"] == RSRC_NAME:
            img1_start = section["body_start"]
            fat, details = extract_rsrc_fat(mse)
            body_length = details["img1"]["body_length"]
            regions["rsrc_img1_head"] = (img1_start, img1_start + 0x54)
            regions["rsrc_img1_padding"] = (img1_start + 0x54, img1_start + IMG1_PREFIX)
            regions["rsrc_fat"] = (img1_start + IMG1_PREFIX, img1_start + IMG1_PREFIX + body_length)
            regions["rsrc_img1_footer"] = (
                img1_start + IMG1_PREFIX + body_length,
                section["body_end"],
            )
            _ = fat
    return regions


def first_changed_offset(a: bytes, b: bytes) -> Optional[int]:
    limit = min(len(a), len(b))
    for index, (left, right) in enumerate(zip(a[:limit], b[:limit])):
        if left != right:
            return index
    if len(a) != len(b):
        return limit
    return None


def parse_section_table(prefix: bytes) -> list[dict[str, Any]]:
    """Parse MSE slots from a header prefix. Does not load section bodies."""
    need = MSE_HEADER + SLOT_COUNT * SLOT_SIZE
    if len(prefix) < need:
        raise Refused("Firmware.MSE prefix is too short for its section table")
    sections: list[dict[str, Any]] = []
    for index in range(SLOT_COUNT):
        entry = prefix[MSE_HEADER + index * SLOT_SIZE : MSE_HEADER + (index + 1) * SLOT_SIZE]
        tag, name = entry[:4], entry[4:8]
        if tag == b"\0\0\0\0":
            continue
        _unk, dev_offset, length, address, entry_offset, _unk2, version, load_addr = (
            struct.unpack_from("<8I", entry, 8)
        )
        body_start = dev_offset + 0x1000
        sections.append(
            {
                "index": index,
                "tag": tag,
                "name": name,
                "name_ascii": name[::-1].decode("ascii", errors="replace"),
                "dev_offset": dev_offset,
                "length": length,
                "address": address,
                "entry_offset": entry_offset,
                "version": version,
                "load_addr": load_addr,
                "body_start": body_start,
                "body_end": body_start + length,
            }
        )
    return sections


def inspect_mse_flash_identity(
    path: Path,
    *,
    expected_sha256: Optional[str] = None,
    expected_size: Optional[int] = None,
) -> dict[str, Any]:
    """Confirm an MSE is the 39A10023 Helvetica image, not 2012 / iPEMS.

    Reads the section table and the rsrc IMG1 format byte. Does not load the
    whole 193 MiB file beyond those seeks (the caller still hashes it).
    """
    from . import const

    path = Path(path)
    if not path.is_file():
        raise Refused(f"firmware image missing: {path}")
    size = path.stat().st_size
    want_size = const.FIRMWARE_MSE_SIZE if expected_size is None else expected_size
    if size != want_size:
        raise Refused(f"firmware image size {size} != {want_size}")
    with path.open("rb") as f:
        prefix = f.read(MSE_HEADER + SLOT_COUNT * SLOT_SIZE)
        sections = parse_section_table(prefix)
        by_name = {s["name_ascii"]: s for s in sections}
        osos = by_name.get("osos")
        disk = by_name.get("disk")
        rsrc = by_name.get("rsrc")
        if osos is None:
            raise Refused("Firmware.MSE has no osos section")
        if osos["length"] < 1_000_000:
            raise Refused(
                "osos section is under 1 MiB. That is the ipod_theme unsigned-boot "
                "name swap, not RetailOS. Refusing to flash."
            )
        if disk is not None and disk["length"] > osos["length"]:
            raise Refused(
                "disk section is larger than osos. That is a 2012-style unsigned "
                "boot wrapper, not the 39A10023 Helvetica MSE. Refusing to flash."
            )
        if rsrc is None:
            raise Refused("Firmware.MSE has no rsrc section")
        f.seek(rsrc["body_start"])
        img1_head = f.read(8)
    if img1_head[:7] != NANO7_IMG1_MAGIC:
        raise Refused(f"rsrc IMG1 magic is {img1_head[:8]!r}, not 87402.0")
    fmt = img1_head[7]
    if fmt == STOCK_FORMAT:
        raise Refused(
            "rsrc IMG1 format byte is 0x04 (stock-signed). iPEMS ByteFau1t and "
            "Finder IPSW restores use that path. This toolkit flashes the hashed "
            "Helvetica CFF image (format 0x03) with wInd3x restore --firmware only."
        )
    if fmt != PATCHED_FORMAT:
        raise Refused(f"rsrc IMG1 format byte is 0x{fmt:02x}, expected patched 0x03")
    digest = sha256_file(path)
    want_hash = const.FIRMWARE_MSE_SHA256 if expected_sha256 is None else expected_sha256
    if digest != want_hash:
        raise Refused(
            "firmware image is not the hardware-tested 39A10023 Helvetica hash "
            f"{want_hash}. Actual {digest}."
        )
    return {
        "sha256": digest,
        "identity": "39A10023",
        "osos_length": osos["length"],
        "rsrc_format": fmt,
        "label": "39A10023 Helvetica CFF (ipod_sun --device=nano7-refresh)",
    }
