"""Safe Home pack-cap raise: skip grow_cache in a new resident.

MKN52 reboot-looped when silver_resident called
hb_silver_icon_grow_cache(1 MiB) for pack count > 8. The hardware-tested
HRL1 images stay pinned. This module copies those files and patches one
Thumb branch so the 1 MiB grow is never taken. Selector/ab are untouched.

Rollback: ./start apps restore-tested copies the pinned residents back
and deletes resident-nogrow.active (and resident-B.active).

Nothing here writes NAND.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Dict, List, Optional

from . import const
from .compat import Refused, sha256_bytes, sha256_file


HRL1_MAGIC = 0x314C5248
NOGROW_PACK_LIMIT = 16
STOCK_PACK_LIMIT = 8

NOGROW_MARKER = "/Apps/Startup/resident-nogrow.active"
NOGROW_WORKING_FAT = "/Apps/Startup/resident.hbapp"
NOGROW_A_FAT = "/Apps/Startup/resident-A.hbapp"
NOGROW_B_FAT = "/Apps/Startup/resident-B.hbapp"
TESTED_WORKING_FAT = "/Apps/Startup/resident.tested.hbapp"
TESTED_A_FAT = "/Apps/Startup/resident-A.tested.hbapp"
TESTED_B_FAT = "/Apps/Startup/resident-B.tested.hbapp"

NOGROW_WORKING_REL = "artifacts/extra/Startup/resident-nogrow.hbapp"
NOGROW_A_REL = "artifacts/extra/Startup/resident-A-nogrow.hbapp"
NOGROW_B_REL = "artifacts/extra/Startup/resident-B-nogrow.hbapp"

# Pinned one-branch patches of the hardware-tested HRL1 files (bls.n -> b.n).
NOGROW_WORKING_SHA256 = "1f9a8ca7e88d93976e015d4b90bd06b09899469648ea45955e05b0b5afe2baef"
NOGROW_A_SHA256 = "cd55a5818696d7ca91af373aaf6be61923e2f0ac81e43580661d8e66914bdef0"
NOGROW_B_SHA256 = "6b0581bd18a7121f2f7905c9c06c2c56fcdd39d5ae164baa91a6f44dc208cff4"
NOGROW_HASHES = frozenset(
    {NOGROW_WORKING_SHA256, NOGROW_A_SHA256, NOGROW_B_SHA256}
)
PINNED_NOGROW = {
    NOGROW_WORKING_REL: NOGROW_WORKING_SHA256,
    NOGROW_A_REL: NOGROW_A_SHA256,
    NOGROW_B_REL: NOGROW_B_SHA256,
}

# Unique site of `if (n > 8u) grow_cache(1 MiB)` in the tested HRL1 images.
# cmp rN, #8 ; bls.n skip ; ... ; mov.w r0, #1048576
# Patch: bls.n -> b.n (always skip the grow). Same 4-byte displacement.
# Memory order (Thumb halfwords little-endian), not objdump display order.
PATCH_A = {
    "rel": "artifacts/device/Startup/resident.hbapp",
    "trailer": False,
    "site": bytes.fromhex("082d04d959464ff48010"),
    "bls_off": 2,
}
PATCH_A_SLOT = {
    "rel": "artifacts/device/Startup/resident-A.hbapp",
    "trailer": True,
    "site": bytes.fromhex("082d04d959464ff48010"),
    "bls_off": 2,
}
PATCH_B = {
    "rel": "artifacts/device/Startup/resident-B.hbapp",
    "trailer": True,
    "site": bytes.fromhex("082c04d900994ff48010"),
    "bls_off": 2,
}
BLS_N = bytes.fromhex("04d9")
B_N = bytes.fromhex("04e0")


def fnv1a32(data: bytes) -> int:
    value = 0x811C9DC5
    for byte in data:
        value = ((value ^ byte) * 0x01000193) & 0xFFFFFFFF
    return value


def _split_hrl1(data: bytes, *, trailer: bool) -> tuple[bytes, bytes, bytes]:
    if len(data) < 24 or struct.unpack_from("<I", data, 0)[0] != HRL1_MAGIC:
        raise Refused("resident is not HRL1")
    magic, entry, image, span, count, align = struct.unpack_from("<6I", data)
    packed_end = 24 + image + 4 * count
    expect = packed_end + (4 if trailer else 0)
    if len(data) != expect:
        raise Refused(
            f"resident length {len(data)} != {expect} "
            f"(image={image} relocs={count} trailer={trailer})"
        )
    packed = data[:packed_end]
    trail = data[packed_end:]
    img = data[24 : 24 + image]
    if trailer:
        got = fnv1a32(packed)
        want = struct.unpack_from("<I", trail, 0)[0]
        if got != want:
            raise Refused(f"resident FNV-1a {got:#010x} != trailer {want:#010x}")
    return packed, img, trail


def _patch_image(img: bytes, site: bytes, bls_off: int) -> bytes:
    hits = []
    start = 0
    while True:
        i = img.find(site, start)
        if i < 0:
            break
        hits.append(i)
        start = i + 1
    if len(hits) != 1:
        raise Refused(
            f"grow_cache site: expected 1 match, found {len(hits)}. "
            "Will not patch an unknown resident."
        )
    off = hits[0] + bls_off
    if img[off : off + 2] != BLS_N:
        raise Refused("grow_cache branch is not bls.n at the known site")
    out = bytearray(img)
    out[off : off + 2] = B_N
    return bytes(out)


def patch_resident(src: Path, *, trailer: bool, site: bytes, bls_off: int) -> bytes:
    data = src.read_bytes()
    packed, img, trail = _split_hrl1(data, trailer=trailer)
    new_img = _patch_image(img, site, bls_off)
    new_packed = packed[:24] + new_img + packed[24 + len(img) :]
    if not trailer:
        return new_packed
    return new_packed + struct.pack("<I", fnv1a32(new_packed))


# ---------------------------------------------------------------------------
# Lean residents: nogrow + a smaller Home icon LRU.
#
# Measured in the pinned HRL1 images (not the legacy in-tree source): the
# resident already reads AllApps*.pack into an exact-size buffer
# (hb_fs_size, then alloc(size)), so there is no 1 MiB pack buffer to free on
# the device. The biggest fixed resident allocation left is the icon LRU:
# 20 slots x 0xE000 (57,344) = 1,146,880 bytes, allocated once at boot.
#
# Four immediates change, nothing else:
#   hit loop   cmp r1, #20 -> #16  (r1 is also the divisor of rr % N, so the
#                                   round-robin follows the same constant)
#   alloc loop cmp rN, #20 -> #16
#   read cap   mov.w r2, #0xE000 -> #0xC800
#   alloc size mov.w r0, #0xE000 -> #0xC800
# Every Home icon .bin is 50,208 bytes (enforced by apps.py / app_icons.py);
# 0xC800 = 51,200 leaves 992 bytes of margin. With the 16-app nogrow cap every
# lazy icon keeps its own slot, so nothing is evicted that was not before.
# 16 x 51,200 = 819,200: saves 327,680 bytes of boot heap.
# Slots 16..19 of the reboot-stable state struct are never read again (both
# loops and the modulo stop at 16).
LEAN_MARKER = "/Apps/Startup/resident-lean.active"
STOCK_ICON_LRU_N = 20
STOCK_ICON_BUF = 0xE000
LEAN_ICON_LRU_N = 16
LEAN_ICON_BUF = 0xC800
LEAN_SAVED_BYTES = STOCK_ICON_LRU_N * STOCK_ICON_BUF - LEAN_ICON_LRU_N * LEAN_ICON_BUF

LEAN_WORKING_REL = "artifacts/extra/Startup/resident-lean.hbapp"
LEAN_A_REL = "artifacts/extra/Startup/resident-A-lean.hbapp"
LEAN_B_REL = "artifacts/extra/Startup/resident-B-lean.hbapp"

LEAN_WORKING_SHA256 = "13be567d565a3c4db0cbbe6229d167312a365eacd1a8ea5326bfc7138f73fc91"
LEAN_A_SHA256 = "6a655519b86be430284356f2155ceb298fb66b5685afbad2757e3720d22d96dd"
LEAN_B_SHA256 = "90ca80f2097b821f793baaf6207ebb0b53d66515953b7175b7ef27d323020d98"
LEAN_HASHES = frozenset({LEAN_WORKING_SHA256, LEAN_A_SHA256, LEAN_B_SHA256})
PINNED_LEAN = {
    LEAN_WORKING_REL: LEAN_WORKING_SHA256,
    LEAN_A_REL: LEAN_A_SHA256,
    LEAN_B_REL: LEAN_B_SHA256,
}

# (old, new) memory-order byte strings; each old must match exactly once.
_LEAN_SLOT_A = (
    ("01311429ecd1", "01311029ecd1"),          # hit loop + rr divisor
    ("04ebc302142d5060", "04ebc302102d5060"),  # alloc loop
    ("2c4ff46042fff7", "2c4ff44842fff7"),      # hb_fs_read cap
    ("374ff46040b047", "374ff44840b047"),      # alloc(ICON_BUF_SZ)
)
_LEAN_SLOT_B = (
    ("01311429ecd1", "01311029ecd1"),
    ("05ebc302142c5060", "05ebc302102c5060"),
    ("2c4ff46042fff7", "2c4ff44842fff7"),
    ("364ff46040b847", "364ff44840b847"),
)
LEAN_SPECS = (
    (NOGROW_WORKING_REL, False, _LEAN_SLOT_A, LEAN_WORKING_REL),
    (NOGROW_A_REL, True, _LEAN_SLOT_A, LEAN_A_REL),
    (NOGROW_B_REL, True, _LEAN_SLOT_B, LEAN_B_REL),
)


def _apply_lean(img: bytes, edits) -> bytes:
    out = bytearray(img)
    for old_hex, new_hex in edits:
        old = bytes.fromhex(old_hex)
        new = bytes.fromhex(new_hex)
        if len(old) != len(new):
            raise Refused("lean edit changes length")
        hits = []
        i = img.find(old)
        while i >= 0:
            hits.append(i)
            i = img.find(old, i + 1)
        if len(hits) != 1:
            raise Refused(
                f"icon LRU site {old_hex}: expected 1 match, found {len(hits)}. "
                "Will not patch an unknown resident."
            )
        out[hits[0] : hits[0] + len(old)] = new
    return bytes(out)


def lean_resident_bytes(src_bytes: bytes, *, trailer: bool, edits) -> bytes:
    packed, img, _trail = _split_hrl1(src_bytes, trailer=trailer)
    new_img = _apply_lean(img, edits)
    new_packed = packed[:24] + new_img + packed[24 + len(img) :]
    if not trailer:
        return new_packed
    return new_packed + struct.pack("<I", fnv1a32(new_packed))


def build_lean_artifacts() -> Dict[str, str]:
    """nogrow + icon LRU 16 x 0xC800. Host only. Does not write NAND."""
    nog = require_nogrow_files()
    by_rel = {NOGROW_WORKING_REL: nog["working"], NOGROW_A_REL: nog["a"], NOGROW_B_REL: nog["b"]}
    hashes = {}
    for nog_rel, trailer, edits, rel in LEAN_SPECS:
        src = by_rel[nog_rel]
        if sha256_file(src) != PINNED_NOGROW[nog_rel]:
            raise Refused(f"{nog_rel} does not match the pinned nogrow hash")
        out = lean_resident_bytes(src.read_bytes(), trailer=trailer, edits=edits)
        digest = sha256_bytes(out)
        want = PINNED_LEAN[rel]
        if digest != want:
            raise Refused(
                f"{rel} sha {digest} != pinned {want}. "
                "Will not ship an unknown lean resident."
            )
        dest = const.artifact(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(out)
        hashes[rel] = digest
        print(f"  patched  {dest.name}  {len(out)} bytes  sha {digest[:16]}...")
    return hashes


def require_lean_files() -> Dict[str, Path]:
    files = {
        "working": const.artifact(LEAN_WORKING_REL),
        "a": const.artifact(LEAN_A_REL),
        "b": const.artifact(LEAN_B_REL),
    }
    want = {"working": LEAN_WORKING_SHA256, "a": LEAN_A_SHA256, "b": LEAN_B_SHA256}
    if any(not p.is_file() or sha256_file(p) != want[k] for k, p in files.items()):
        build_lean_artifacts()
    for key, path in files.items():
        if not path.is_file() or sha256_file(path) != want[key]:
            raise Refused(f"lean resident {path.name} missing or not the pinned hash")
    return files


def lean_present(ops) -> bool:
    return bool(ops.exists(LEAN_MARKER))


def active_slot_b(ops) -> tuple:
    """(source path, sha256, label) of the slot-B resident to keep in place."""
    if lean_present(ops) and marker_present(ops):
        return require_lean_files()["b"], LEAN_B_SHA256, "lean unload resident"
    if marker_present(ops):
        return require_nogrow_files()["b"], NOGROW_B_SHA256, "nogrow unload resident"
    return (
        const.artifact("artifacts/device/Startup/resident-B.hbapp"),
        const.SLOT_B_SHA256,
        "tested unload resident",
    )


def nogrow_dir() -> Path:
    dest = const.artifact("artifacts/extra/Startup")
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def build_nogrow_artifacts() -> Dict[str, str]:
    """Patch pinned residents. Host only. Does not write NAND."""
    specs = (
        (PATCH_A, NOGROW_WORKING_REL),
        (PATCH_A_SLOT, NOGROW_A_REL),
        (PATCH_B, NOGROW_B_REL),
    )
    hashes = {}
    for spec, rel in specs:
        src = const.artifact(spec["rel"])
        if not src.is_file():
            raise Refused(f"pinned resident missing: {src}")
        out = patch_resident(
            src, trailer=spec["trailer"], site=spec["site"], bls_off=spec["bls_off"]
        )
        dest = const.artifact(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(out)
        digest = sha256_bytes(out)
        want = PINNED_NOGROW[rel]
        if digest != want:
            raise Refused(
                f"{rel} sha {digest} != pinned {want}. "
                "Will not ship an unknown nogrow resident."
            )
        hashes[rel] = digest
        print(f"  patched  {dest.name}  {len(out)} bytes  sha {digest[:16]}...")
    return hashes


def require_nogrow_files() -> Dict[str, Path]:
    files = {
        "working": const.artifact(NOGROW_WORKING_REL),
        "a": const.artifact(NOGROW_A_REL),
        "b": const.artifact(NOGROW_B_REL),
    }
    missing = [str(p) for p in files.values() if not p.is_file()]
    if missing:
        build_nogrow_artifacts()
        missing = [str(p) for p in files.values() if not p.is_file()]
    if missing:
        raise Refused("nogrow residents missing: " + ", ".join(missing))
    return files


def nogrow_hashes() -> Dict[str, str]:
    files = require_nogrow_files()
    got = {
        NOGROW_WORKING_FAT: sha256_file(files["working"]),
        NOGROW_A_FAT: sha256_file(files["a"]),
        NOGROW_B_FAT: sha256_file(files["b"]),
    }
    if got[NOGROW_WORKING_FAT] != NOGROW_WORKING_SHA256:
        raise Refused("resident-nogrow.hbapp does not match the pinned hash")
    if got[NOGROW_A_FAT] != NOGROW_A_SHA256:
        raise Refused("resident-A-nogrow.hbapp does not match the pinned hash")
    if got[NOGROW_B_FAT] != NOGROW_B_SHA256:
        raise Refused("resident-B-nogrow.hbapp does not match the pinned hash")
    return got


def overlay_core_hashes(
    expected: Dict[str, str], *, nogrow: bool, lean: bool = False
) -> Dict[str, str]:
    """Swap resident / resident-A hashes when the nogrow (or lean) marker is on FAT."""
    out = dict(expected)
    if nogrow and lean:
        out[NOGROW_WORKING_FAT] = LEAN_WORKING_SHA256
        out[NOGROW_A_FAT] = LEAN_A_SHA256
    elif nogrow:
        out[NOGROW_WORKING_FAT] = NOGROW_WORKING_SHA256
        out[NOGROW_A_FAT] = NOGROW_A_SHA256
    return out


def pack_limit(*, nogrow: bool) -> int:
    return NOGROW_PACK_LIMIT if nogrow else STOCK_PACK_LIMIT


def marker_present(ops) -> bool:
    return bool(ops.exists(NOGROW_MARKER))


def launcher_on(ops) -> bool:
    """True if slot B holds the nano7kit Launcher (silver_resident_na) and is active."""
    from .ports import SLOT_B_FAT, SLOT_B_MARKER

    return bool(ops.exists(SLOT_B_MARKER) and ops.exists(SLOT_B_FAT)
                and ops.read_hash(SLOT_B_FAT) in const.NA_B_ALL_SHA256S)


def cap_lifted(ops) -> bool:
    """Home may hold NOGROW_PACK_LIMIT apps: the nogrow/lean overlay is on, or the
    Launcher is, which refuses the 1 MiB icon-cache grow by itself."""
    return marker_present(ops) or launcher_on(ops)


def print_status() -> int:
    print("nano7kit pack cap")
    print("Stock silver_resident grows the OS bitmap cache by 1 MiB when")
    print("count > 8. That path reboot-looped this MKN52 at 11 icons.")
    print("nogrow residents are a one-branch patch of the hardware-tested")
    print("HRL1 files (bls.n -> b.n over grow_cache). ab.hbapp is unchanged.")
    print()
    print(f"  stock cap   {STOCK_PACK_LIMIT}")
    print(f"  nogrow cap  {NOGROW_PACK_LIMIT} (registrar still 64)")
    print(f"  marker      {NOGROW_MARKER}")
    print()
    try:
        files = require_nogrow_files()
    except Refused as exc:
        print(f"  artifacts   missing ({exc})")
        print("  next        ./start apps cap build")
        return 0
    for key, path in files.items():
        print(f"  {key:<10} {path.name}  {path.stat().st_size} bytes  sha {sha256_file(path)[:16]}...")
    print()
    try:
        for key, path in require_lean_files().items():
            print(f"  lean-{key:<5} {path.name}  {path.stat().st_size} bytes  sha {sha256_file(path)[:16]}...")
    except Refused as exc:
        print(f"  lean        missing ({exc})")
    print(f"  lean        icon LRU {LEAN_ICON_LRU_N} x {LEAN_ICON_BUF:#x} "
          f"(stock {STOCK_ICON_LRU_N} x {STOCK_ICON_BUF:#x}); frees {LEAN_SAVED_BYTES:,} bytes at boot")
    print(f"  lean marker {LEAN_MARKER}")
    print()
    print("exact:")
    print("  ./start apps cap build")
    print("  ./start apps cap enable     # Disk Mode, Do not disconnect")
    print("  ./start apps cap lean       # nogrow + smaller icon LRU (needs nogrow first)")
    print("  ./start apps cap enable     # from lean: back to plain nogrow, cap stays 16")
    print("  ./start apps cap disable    # tested residents back; keeps packs, slot B, Mario")
    print("  ./start apps restore-tested # full rollback: residents + 3-app pack + slot A")
    print("Untested on this MKN52. First Sleep/Wake+Home on the current pack")
    print("(8 or fewer apps, same behavior as stock), then add extras one per reboot.")
    print("Slot B (Mario) is covered: resident-B.hbapp gets the same one-branch patch.")
    return 0


def refuse_over_launcher(ops) -> None:
    """The nano7kit Launcher (silver_resident_na) already holds 16 Home apps and
    refuses the icon-cache grow. Overlaying the old residents on top of it would
    undo it, so apps cap enable / apps cap lean stop here."""
    from .ports import SLOT_B_FAT

    if ops.exists(SLOT_B_FAT) and ops.read_hash(SLOT_B_FAT) in const.NA_B_ALL_SHA256S:
        raise Refused(
            f"{SLOT_B_FAT} is the nano7kit Launcher, which already holds "
            "16 Home apps. apps cap is not needed and would undo it. "
            "To go back to the old launcher: ./start resident restore."
        )


def enable_cap(
    nano,
    *,
    backup: Path,
    backup_sha256: str,
    allow_size: Optional[int] = None,
    confirm_untested: bool = False,
    skip_backup_file: bool = False,
    skip_wait: bool = False,
    watch: bool = False,
    lean: bool = False,
) -> dict:
    """Disk Mode: overlay nogrow (or lean) residents. Does not change AllApps.pack.

    lean=True copies the lean residents (nogrow + icon LRU 16 x 0xC800) and
    writes resident-lean.active. lean=False puts plain nogrow back and deletes
    resident-lean.active if present (the one-step undo for lean).
    """
    from .apps import RECOVERY_HINT, _ops_for_nano
    from .compat import is_macos, sha256_bytes
    from .device import find_nanos
    from .fat import assert_not_hfs, ensure_fat_mount
    from .safety import check_backup, check_disk_size, check_usb_serial, layout_for_disk_size
    from .sync import wait_for_enter_or_watch
    from .untether import durable_flush_and_rehash
    from .ports import SLOT_B_MARKER

    files = require_lean_files() if lean else require_nogrow_files()
    print("nano7kit apps cap " + ("lean" if lean else "enable"))
    if lean:
        print(f"Lean residents: nogrow + Home icon LRU {LEAN_ICON_LRU_N} x {LEAN_ICON_BUF:#x}")
        print(f"(stock {STOCK_ICON_LRU_N} x {STOCK_ICON_BUF:#x}). Frees {LEAN_SAVED_BYTES:,} bytes of boot heap.")
    print("Copies nogrow residents over resident / resident-A (and B if")
    print("slot B is already active). Pins stay in *.tested.hbapp on FAT.")
    print("Does not write NAND. Does not change AllApps.pack.")
    print("ab.hbapp is not touched. " + RECOVERY_HINT)
    print()
    if not skip_wait:
        wait_for_enter_or_watch(watch=watch, timeout=180)
        print()
    if nano is None:
        found = find_nanos()
        if not found:
            raise Refused(
                "no iPod mass-storage volume. Confirm Disk Mode "
                "(Do not disconnect while plugged in), then re-run "
                "./start apps cap enable."
            )
        if len(found) > 1:
            raise Refused("more than one iPod; unplug extras")
        nano = found[0]
    check_usb_serial(nano.usb_serial)
    check_disk_size(
        nano.size,
        allow_size=allow_size,
        confirm_untested=confirm_untested,
        layout=layout_for_disk_size(nano.raw_device, nano.size),
    )
    check_backup(
        backup,
        backup_sha256,
        nano.size,
        skip_file=skip_backup_file,
        allow_size_mismatch=True,
    )
    assert_not_hfs(nano)
    if not is_macos():
        print("    note      live copy is documented Mac-only; this host is not macOS")
    ops, mounted = _ops_for_nano(nano)
    from .ports import SLOT_B_FAT

    refuse_over_launcher(ops)

    if lean:
        # Pre-flight: lean only goes on top of a nogrow (or lean) set that is
        # already on FAT, so the step from the known-good state is 4 bytes.
        if not marker_present(ops):
            raise Refused(
                "apps cap lean needs the nogrow overlay first "
                f"({NOGROW_MARKER} is absent). Run ./start apps cap enable, "
                "Sleep/Wake+Home once, then ./start apps cap lean."
            )
        ok_hashes = NOGROW_HASHES | LEAN_HASHES
        for fat in (NOGROW_WORKING_FAT, NOGROW_A_FAT, SLOT_B_FAT):
            if ops.exists(fat):
                got = ops.read_hash(fat)
                if got not in ok_hashes:
                    raise Refused(
                        f"{fat} sha {str(got)[:16]}... is neither nogrow nor lean. "
                        "Will not overlay an unknown resident. " + RECOVERY_HINT
                    )
        print("    preflight nogrow marker present; residents are nogrow/lean")

    wrote: List[str] = []
    already: List[str] = []
    expected: Dict[str, str] = {}

    def backup_if_tested(fat: str, digest: str, dest_fat: str) -> None:
        if not ops.exists(fat):
            return
        got = ops.read_hash(fat)
        if got in NOGROW_HASHES or got in LEAN_HASHES:
            print(f"    already   {fat} is nogrow/lean (tested copy stays in {dest_fat})")
            return
        if got != digest:
            print(
                f"    keep      {fat} (sha {got[:16]}... is not the tested file; "
                "will not clobber it into *.tested.hbapp)"
            )
            return
        if ops.exists(dest_fat) and ops.read_hash(dest_fat) == digest:
            print(f"    already   {dest_fat}")
            return
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            src = const.artifact(
                {
                    NOGROW_WORKING_FAT: "artifacts/device/Startup/resident.hbapp",
                    NOGROW_A_FAT: "artifacts/device/Startup/resident-A.hbapp",
                    SLOT_B_FAT: "artifacts/device/Startup/resident-B.hbapp",
                }[fat]
            )
            status = ops.copy_verified(src, dest_fat, replace=True)
            expected[dest_fat] = digest
            (already if status == "already" else wrote).append(dest_fat)
            print(f"    {status:8}  {dest_fat}  (tested backup)")

    backup_if_tested(NOGROW_WORKING_FAT, const.RESIDENT_SHA256, TESTED_WORKING_FAT)
    backup_if_tested(NOGROW_A_FAT, const.SLOT_A_SHA256, TESTED_A_FAT)
    slot_b = bool(ops.exists(SLOT_B_MARKER))
    if slot_b or ops.exists(SLOT_B_FAT):
        backup_if_tested(SLOT_B_FAT, const.SLOT_B_SHA256, TESTED_B_FAT)

    pairs = [
        (files["working"], NOGROW_WORKING_FAT),
        (files["a"], NOGROW_A_FAT),
    ]
    if slot_b or ops.exists(SLOT_B_FAT):
        pairs.append((files["b"], SLOT_B_FAT))
    for src, fat in pairs:
        status = ops.copy_verified(src, fat, replace=True)
        digest = sha256_file(src)
        expected[fat] = digest
        (already if status == "already" else wrote).append(fat)
        print(f"    {status:8}  {fat}  sha {digest[:16]}...")

    import tempfile

    with tempfile.TemporaryDirectory() as td:
        marker = Path(td) / "resident-nogrow.active"
        marker.write_bytes(b"")
        status = ops.copy_verified(marker, NOGROW_MARKER, replace=True)
        expected[NOGROW_MARKER] = sha256_bytes(b"")
        (already if status == "already" else wrote).append(NOGROW_MARKER)
        print(f"    {status:8}  {NOGROW_MARKER}  (pack cap {NOGROW_PACK_LIMIT} next boot)")
        if lean:
            lm = Path(td) / "resident-lean.active"
            lm.write_bytes(b"")
            status = ops.copy_verified(lm, LEAN_MARKER, replace=True)
            expected[LEAN_MARKER] = sha256_bytes(b"")
            (already if status == "already" else wrote).append(LEAN_MARKER)
            print(f"    {status:8}  {LEAN_MARKER}  (icon LRU {LEAN_ICON_LRU_N} next boot)")
        elif ops.exists(LEAN_MARKER):
            ops.delete(LEAN_MARKER)
            wrote.append(LEAN_MARKER)
            print(f"    deleted   {LEAN_MARKER} (plain nogrow next boot)")

    rec = {
        "file_hashes": expected,
        "wrote": str(len(wrote)),
        "already": str(len(already)),
        "verified": "yes",
        "apps": "cap-enable",
    }
    if mounted:
        from .fat import mounted_root
        from .untether import check_core_loader_hashes

        if mounted_root(nano) is None:
            nano.mountpoints = [str(ensure_fat_mount(nano) or "")]
        durable_flush_and_rehash(nano, rec)
        after = check_core_loader_hashes(nano)
        # cap enable/disable never touch a pack; judge the residents only.
        if not after.get("core_ok"):
            raise Refused(
                "apps cap enable finished but core loader hashes are not the "
                "nogrow set (resident / resident-A). "
                + RECOVERY_HINT
            )
    print("APPS_OK")
    print(f"    cap       {NOGROW_PACK_LIMIT} ({'lean' if lean else 'nogrow'})")
    if lean:
        print("    undo      ./start apps cap enable   (plain nogrow back, cap stays 16)")
        print("    full      ./start apps restore-tested")
    print("    Eject IPOD. Unplug. Sleep/Wake+Home on the current pack first.")
    print("    If Home loops, " + RECOVERY_HINT)
    return {"ok": True, "wrote": wrote, "already": already, "slot_b": slot_b, "lean": lean}


def _open_device(nano, *, backup, backup_sha256, allow_size, confirm_untested,
                 skip_backup_file, skip_wait, watch, verb):
    from .apps import _ops_for_nano
    from .compat import is_macos
    from .device import find_nanos
    from .fat import assert_not_hfs
    from .safety import check_backup, check_disk_size, check_usb_serial, layout_for_disk_size
    from .sync import wait_for_enter_or_watch

    if not skip_wait:
        wait_for_enter_or_watch(watch=watch, timeout=180)
        print()
    if nano is None:
        found = find_nanos()
        if not found:
            raise Refused(
                "no iPod mass-storage volume. Confirm Disk Mode "
                "(Do not disconnect while plugged in), then re-run "
                f"./start apps cap {verb}."
            )
        if len(found) > 1:
            raise Refused("more than one iPod; unplug extras")
        nano = found[0]
    check_usb_serial(nano.usb_serial)
    check_disk_size(
        nano.size,
        allow_size=allow_size,
        confirm_untested=confirm_untested,
        layout=layout_for_disk_size(nano.raw_device, nano.size),
    )
    check_backup(
        backup,
        backup_sha256,
        nano.size,
        skip_file=skip_backup_file,
        allow_size_mismatch=True,
    )
    assert_not_hfs(nano)
    if not is_macos():
        print("    note      live copy is documented Mac-only; this host is not macOS")
    ops, mounted = _ops_for_nano(nano)
    return nano, ops, mounted


def packs_over_stock(ops) -> Dict[str, int]:
    """Home packs on FAT with more apps than the stock (grow_cache) cap."""
    from .apps import pack_app_names
    from .ports import PACK_A_FAT, PACK_B_FAT

    over: Dict[str, int] = {}
    for fat in (PACK_A_FAT, PACK_B_FAT):
        if not ops.exists(fat):
            continue
        data = ops.read_bytes(fat)
        if not data:
            continue
        n = len(dict.fromkeys(pack_app_names(data)))
        if n > STOCK_PACK_LIMIT:
            over[fat] = n
    return over


def disable_cap(
    nano,
    *,
    backup: Path,
    backup_sha256: str,
    allow_size: Optional[int] = None,
    confirm_untested: bool = False,
    skip_backup_file: bool = False,
    skip_wait: bool = False,
    watch: bool = False,
) -> dict:
    """Disk Mode: put the pinned tested residents back, keep packs and slot B.

    Unlike restore-tested this keeps resident-B.active, AllApps.pack and
    AllApps-B.pack, so Mario stays on Home. Refuses while any Home pack has
    more than 8 apps: the stock residents would take grow_cache on boot.
    Does not write NAND. ab.hbapp is not touched.
    """
    from .apps import RECOVERY_HINT
    from .compat import sha256_bytes
    from .fat import ensure_fat_mount
    from .ports import SLOT_B_FAT, SLOT_B_MARKER
    from .untether import check_core_loader_hashes, durable_flush_and_rehash

    print("nano7kit apps cap disable")
    print("Copies the pinned tested residents back over resident / resident-A")
    print("(and resident-B if it holds the nogrow patch). Deletes")
    print("resident-nogrow.active. Keeps AllApps.pack, AllApps-B.pack and")
    print("resident-B.active, so slot B and Mario stay. Does not write NAND.")
    print("ab.hbapp is not touched. " + RECOVERY_HINT)
    print()
    nano, ops, mounted = _open_device(
        nano,
        backup=backup,
        backup_sha256=backup_sha256,
        allow_size=allow_size,
        confirm_untested=confirm_untested,
        skip_backup_file=skip_backup_file,
        skip_wait=skip_wait,
        watch=watch,
        verb="disable",
    )
    over = packs_over_stock(ops)
    if over:
        detail = ", ".join(f"{fat} has {n}" for fat, n in over.items())
        raise Refused(
            f"cap disable would leave a Home pack above {STOCK_PACK_LIMIT} apps "
            f"on the stock residents ({detail}). Stock silver_resident calls "
            "grow_cache(1 MiB) above 8, the path the set looped on. "
            "First take extras off with ./start apps remove NAME --keep-files "
            "(one at a time, still on nogrow), then re-run ./start apps cap disable. "
            "Or full rollback: ./start apps restore-tested."
        )
    pins = (
        ("artifacts/device/Startup/resident.hbapp", NOGROW_WORKING_FAT,
         const.RESIDENT_SHA256, NOGROW_WORKING_SHA256),
        ("artifacts/device/Startup/resident-A.hbapp", NOGROW_A_FAT,
         const.SLOT_A_SHA256, NOGROW_A_SHA256),
        ("artifacts/device/Startup/resident-B.hbapp", SLOT_B_FAT,
         const.SLOT_B_SHA256, NOGROW_B_SHA256),
    )
    expected: Dict[str, str] = {}
    wrote: List[str] = []
    already: List[str] = []
    lean_by_fat = {
        NOGROW_WORKING_FAT: LEAN_WORKING_SHA256,
        NOGROW_A_FAT: LEAN_A_SHA256,
        SLOT_B_FAT: LEAN_B_SHA256,
    }
    for rel, fat, tested, nogrow in pins:
        if not ops.exists(fat):
            continue
        got = ops.read_hash(fat)
        if got == tested:
            expected[fat] = tested
            already.append(fat)
            print(f"    already   {fat}  (tested)")
            continue
        if got not in (nogrow, lean_by_fat.get(fat)):
            print(
                f"    keep      {fat} (sha {str(got)[:16]}... is neither tested nor "
                "nogrow; not replacing an unknown resident)"
            )
            continue
        src = const.artifact(rel)
        if not src.is_file() or sha256_file(src) != tested:
            raise Refused(f"pinned tested resident {rel} is missing or changed")
        status = ops.copy_verified(src, fat, replace=True)
        expected[fat] = tested
        (already if status == "already" else wrote).append(fat)
        print(f"    {status:8}  {fat}  sha {tested[:16]}... (tested resident)")
    if ops.exists(NOGROW_MARKER):
        ops.delete(NOGROW_MARKER)
        wrote.append(NOGROW_MARKER)
        print(f"    deleted   {NOGROW_MARKER} (stock pack cap {STOCK_PACK_LIMIT} next boot)")
    else:
        print(f"    already   {NOGROW_MARKER} absent")
    if ops.exists(LEAN_MARKER):
        ops.delete(LEAN_MARKER)
        wrote.append(LEAN_MARKER)
        print(f"    deleted   {LEAN_MARKER}")
    slot_b = bool(ops.exists(SLOT_B_MARKER))
    if slot_b:
        print(f"    kept      {SLOT_B_MARKER} (slot B, Mario stays on Home)")
    rec = {
        "file_hashes": expected,
        "wrote": str(len(wrote)),
        "already": str(len(already)),
        "verified": "yes",
        "apps": "cap-disable",
    }
    if mounted:
        from .fat import mounted_root

        if mounted_root(nano) is None:
            nano.mountpoints = [str(ensure_fat_mount(nano) or "")]
        durable_flush_and_rehash(nano, rec)
        after = check_core_loader_hashes(nano)
        # cap enable/disable never touch a pack; judge the residents only.
        if not after.get("core_ok"):
            raise Refused(
                "apps cap disable finished but core loader hashes are not the "
                "tested set. " + RECOVERY_HINT
            )
    print("APPS_OK")
    print(f"    cap       stock {STOCK_PACK_LIMIT} (nogrow overlay removed)")
    print("    Eject IPOD. Unplug. Sleep/Wake+Home.")
    return {"ok": True, "wrote": wrote, "already": already, "slot_b": slot_b}
