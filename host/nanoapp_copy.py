"""Disk Mode copy of staged .nanoapp folders into /Apps."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import const
from .compat import Refused, sha256_file
from .safety import check_backup, check_disk_size, check_usb_serial, layout_for_disk_size


STAGED = "artifacts/extra/Apps"


def staged_folder(name: str) -> Path:
    if not name.endswith(".nanoapp"):
        raise Refused(f"expected a .nanoapp folder name, got {name!r}")
    path = const.artifact(f"{STAGED}/{name}")
    if not path.is_dir():
        raise Refused(f"missing staged package {path}")
    for part in ("manifest.napp", "app.hbapp", "icon.bin"):
        if not (path / part).is_file():
            raise Refused(f"incomplete package {path}: missing {part}")
    return path


def copy_nanoapp(
    name: str,
    nano=None,
    *,
    backup: Path,
    backup_sha256: str,
    allow_size: Optional[int] = None,
    confirm_untested: bool = False,
    skip_backup_file: bool = False,
    skip_wait: bool = False,
    watch: bool = False,
) -> dict:
    from .apps import _ops_for_nano
    from .fat import assert_not_hfs
    from .device import find_nanos
    from .sync import wait_for_enter_or_watch
    from .untether import durable_flush_and_rehash

    src = staged_folder(name)
    print(f"nano7kit nanoapp copy {name}")
    print("Disk Mode only. Does not write NAND.")
    print()
    if not skip_wait:
        wait_for_enter_or_watch(watch=watch, timeout=180)
        print()
    if nano is None:
        found = find_nanos()
        if not found:
            raise Refused("no iPod mass-storage volume. Confirm Disk Mode, then re-run.")
        if len(found) > 1:
            raise Refused("more than one iPod; unplug extras")
        nano = found[0]
    assert_not_hfs(nano)
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
    ops, _ = _ops_for_nano(nano)
    expected = {}
    fat_dir = f"/Apps/{name}"
    for part in ("app.hbapp", "icon.bin", "manifest.napp"):
        local = src / part
        fat = f"{fat_dir}/{part}"
        status = ops.copy_verified(local, fat, replace=True)
        digest = sha256_file(local)
        expected[fat] = digest
        print(f"    {status:8}  {fat}  sha {digest[:16]}...")
    durable_flush_and_rehash(nano, {"file_hashes": expected})
    print("NANOAPP_COPY_OK")
    print("    eject, Home+Sleep; Home refreshes without reflashing")
    return {"folder": fat_dir, "files": list(expected)}
