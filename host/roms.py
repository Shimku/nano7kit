"""Copy Game Boy ROMs onto FAT /Apps/Data/GameBoy.

The Game Boy app scans that directory for .gb / .gbc. Empty-state copy on
MKN52: "No roms found. Copy .gb files to /Apps/Data/GameBoy". This command
is Disk Mode (same combo as ./start sync) so Music/Finder are not talking
RetailOS SCSI after Helvetica. Durable flush matches install-untether:
fsync, F_FULLFSYNC, unmount, remount rehash. Never writes NAND. Never
writes iPod_Control. Never deletes /Apps/Startup.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from .compat import Refused, sha256_file
from .device import Nano, format_nano, find_nanos
from .fat import (
    MountFat,
    assert_not_hfs,
    assert_safe_fat_path,
    ensure_fat_mount,
)
from .untether import (
    check_loader_hashes,
    durable_flush_and_rehash,
)


GAMEBOY_FAT_DIR = "/Apps/Data/GameBoy"
ROM_EXTS = {".gb", ".gbc"}
ROMS_OK = "ROMS_OK"


def rom_basename(src: Path) -> str:
    name = Path(src).name
    if not name or name in {".", ".."} or name.startswith("."):
        raise Refused(f"refusing ROM name {name!r}")
    if "/" in name or "\\" in name or ":" in name:
        raise Refused(f"refusing ROM path elements in {name!r}")
    ext = Path(name).suffix.lower()
    if ext not in ROM_EXTS:
        raise Refused(
            f"{name} is not a .gb or .gbc file. Game Boy only loads those "
            f"from {GAMEBOY_FAT_DIR}. This toolkit does not ship Nintendo ROMs."
        )
    dest = f"{GAMEBOY_FAT_DIR}/{name}"
    assert_safe_fat_path(dest)
    return name


def add_roms(
    nano: Optional[Nano],
    files: Iterable[Path],
    *,
    skip_wait: bool = False,
    watch: bool = False,
    timeout: int = 180,
) -> dict:
    """Disk Mode FAT copy of .gb/.gbc into /Apps/Data/GameBoy. No NAND write."""
    from .sync import wait_for_enter_or_watch

    srcs = [Path(p).expanduser() for p in files]
    if not srcs:
        raise Refused("pass one or more .gb / .gbc files")
    missing = [str(p) for p in srcs if not p.is_file()]
    if missing:
        raise Refused("ROM file missing: " + ", ".join(missing[:8]))
    names = [rom_basename(p) for p in srcs]

    print("nano7kit roms add")
    print(f"Copy .gb/.gbc into {GAMEBOY_FAT_DIR}. Does not write NAND.")
    print("Does not write iPod_Control. Does not wipe /Apps/Startup.")
    print("Prefer Disk Mode (Do not disconnect while plugged in) after Helvetica.")
    print()
    attached = find_nanos()
    if attached:
        print("Currently attached:")
        for n in attached:
            print(format_nano(n))
            print()
        n0 = attached[0]
        if n0.fstype == "vfat":
            print(
                "If Home shows Connected, that is RetailOS. Music/Finder SCSI "
                "can reboot-loop after Helvetica. Prefer Disk Mode before copy."
            )
            print()
        else:
            assert_not_hfs(n0)
    if not skip_wait:
        wait_for_enter_or_watch(watch=watch, timeout=timeout)
        print()

    if nano is None:
        found = find_nanos()
        if not found:
            raise Refused(
                "no iPod mass-storage volume. Confirm Disk Mode "
                "(Do not disconnect while plugged in), then re-run ./start roms add."
            )
        if len(found) > 1:
            raise Refused("more than one iPod; unplug extras")
        nano = found[0]
    assert_not_hfs(nano)
    root = ensure_fat_mount(nano)
    if root is None:
        raise Refused(
            "could not mount the FAT volume. Disk Mode, then ./start roms add. "
            "Nothing written."
        )
    print(f"    volume     {root}")
    before = check_loader_hashes(nano)
    if before.get("ok"):
        print("    /Apps      loader hashes match before ROM copy (will recheck)")
    else:
        print(
            "    /Apps      loader hashes not complete before ROM copy "
            "(icons need ./start install-untether; ROMs still copy)"
        )

    ops = MountFat(root)
    ops.mkdir_p(GAMEBOY_FAT_DIR)
    expected = {}
    wrote = []
    already = []
    for src, name in zip(srcs, names):
        fat = f"{GAMEBOY_FAT_DIR}/{name}"
        digest = sha256_file(src)
        status = ops.copy_verified(src, fat, replace=True)
        expected[fat] = digest
        (already if status == "already" else wrote).append(fat)
        print(f"    {status:8}  {fat}  sha {digest[:16]}...")

    rec = {
        "file_hashes": expected,
        "wrote": str(len(wrote)),
        "already": str(len(already)),
        "verified": "yes",
    }
    durable_flush_and_rehash(nano, rec)
    after = check_loader_hashes(nano)
    if before.get("ok") and not after.get("ok"):
        raise Refused(
            "ROM copy finished but tested /Apps loader hashes no longer match. "
            "Music Restore reformats HFS+ and wipes /Apps. This command does "
            "not Restore. Re-run ./start install-untether (no firmware). "
            "ROMs may still be in /Apps/Data/GameBoy."
        )
    print(ROMS_OK)
    print(f"    {len(wrote)} written, {len(already)} already present in {GAMEBOY_FAT_DIR}")
    print("    Eject IPOD. Unplug. Sleep/Wake+Home to the Home screen. Open Game Boy.")
    print("    Do not Finder Restore (/Apps stays). Nothing written to NAND.")
    return {
        "ok": True,
        "wrote": wrote,
        "already": already,
        "dir": GAMEBOY_FAT_DIR,
        "hashes": expected,
        "loader_ok": bool(after.get("ok")),
    }
