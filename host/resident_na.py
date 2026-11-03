"""Disk Mode install of the NanoApps-1.0-based heavy resident (silver_resident_na).

Copies the wrapped slot-B image to /Apps/Startup/resident-B.hbapp after saving
the current resident-B (and marker state) for one-command rollback:

  ./start resident install-na
  ./start resident restore

No NAND. No DFU. Mac Disk Mode only (Do not disconnect).
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Optional

from . import const
from .compat import Refused, sha256_file
from .ports import SLOT_B_FAT, SLOT_B_MARKER
from .safety import check_backup, check_disk_size, check_usb_serial, layout_for_disk_size

NA_REL = "artifacts/extra/Startup/resident-B-na.hbapp"
BACKUP_DIR_REL = "artifacts/extra/Startup/na-backup"
BACKUP_META = "resident-na-backup.json"
BACKUP_B = "resident-B.before-na.hbapp"
BACKUP_MARKER = "resident-B.active.before-na"


def na_resident_path() -> Path:
    path = const.artifact(NA_REL)
    if not path.is_file():
        raise Refused(
            f"missing {NA_REL}. Build it with: "
            "make -C nanoapps/system/silver_resident_na && "
            "python3 nanoapps/tools/wrap_resident_slot.py "
            "nanoapps/system/silver_resident_na/build/silver_resident.hbapp "
            f"{NA_REL}"
        )
    return path


def na_sha256() -> str:
    return sha256_file(na_resident_path())


def backup_dir() -> Path:
    d = const.artifact(BACKUP_DIR_REL)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ops(nano):
    from .apps import _ops_for_nano
    from .fat import assert_not_hfs

    assert_not_hfs(nano)
    return _ops_for_nano(nano)


def _open_nano(nano, *, backup, backup_sha256, allow_size, confirm_untested,
               skip_backup_file, skip_wait, watch, verb):
    from .device import find_nanos
    from .sync import wait_for_enter_or_watch

    print(f"nano7kit resident {verb}")
    print("Disk Mode only. Does not write NAND. Does not run DFU.")
    print()
    if not skip_wait:
        wait_for_enter_or_watch(watch=watch, timeout=180)
        print()
    if nano is None:
        found = find_nanos()
        if not found:
            raise Refused(
                "no iPod mass-storage volume. Confirm Disk Mode "
                "(Do not disconnect while plugged in), then re-run."
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
    return nano


def install_na(
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
    """Replace resident-B with silver_resident_na after saving the previous one."""
    src = na_resident_path()
    digest = na_sha256()
    nano = _open_nano(
        nano,
        backup=backup,
        backup_sha256=backup_sha256,
        allow_size=allow_size,
        confirm_untested=confirm_untested,
        skip_backup_file=skip_backup_file,
        skip_wait=skip_wait,
        watch=watch,
        verb="install-na",
    )
    ops, mounted = _ops(nano)
    bdir = backup_dir()
    meta = {
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "installed_sha256": digest,
        "had_resident_b": False,
        "had_marker": False,
        "previous_sha256": None,
    }
    # Save current slot B for restore. If slot B already holds a nano7kit NA
    # image (an earlier install-na), keep the original backup: overwriting it
    # with the NA image would make restore put the NA image back.
    prev = ops.read_bytes(SLOT_B_FAT) if ops.exists(SLOT_B_FAT) else None
    if ops.exists(SLOT_B_FAT) and prev is None:
        raise Refused(f"could not read {SLOT_B_FAT}")
    prev_sha = hashlib.sha256(prev).hexdigest() if prev is not None else None
    if prev_sha in const.NA_B_ALL_SHA256S:
        old_meta = _valid_backup_meta(bdir)
        if old_meta is None:
            raise Refused(
                f"{SLOT_B_FAT} already holds silver_resident_na ({prev_sha[:16]}...) "
                "but the saved original resident-B backup is missing or does not "
                f"match {bdir / BACKUP_META}. Not overwriting it with the NA image. "
                "Copy the na-backup folder back from the Mac you first installed on."
            )
        old_meta["installed_sha256"] = digest
        old_meta["upgraded_from"] = prev_sha
        old_meta["upgraded_at"] = meta["saved_at"]
        meta = old_meta
        print(f"    upgrade   {SLOT_B_FAT}  sha {prev_sha[:16]}... (older silver_resident_na)")
        print(f"    keep      backup {bdir / BACKUP_B}")
        print(f"              sha {str(meta.get('previous_sha256') or '-')[:16]}... (original resident-B)")
        (bdir / BACKUP_META).write_text(json.dumps(meta, indent=2) + "\n")
        return _write_na(nano, ops, src, digest, meta)
    if prev is not None:
        (bdir / BACKUP_B).write_bytes(prev)
        meta["had_resident_b"] = True
        meta["previous_sha256"] = sha256_file(bdir / BACKUP_B)
        print(f"    backup    {SLOT_B_FAT} -> {bdir / BACKUP_B}")
        print(f"              sha {meta['previous_sha256'][:16]}...")
    else:
        print(f"    note      {SLOT_B_FAT} absent; restore will delete the NA image only")
    if ops.exists(SLOT_B_MARKER):
        (bdir / BACKUP_MARKER).write_bytes(b"")
        meta["had_marker"] = True
        print(f"    backup    {SLOT_B_MARKER} present (will keep active)")
    (bdir / BACKUP_META).write_text(json.dumps(meta, indent=2) + "\n")
    return _write_na(nano, ops, src, digest, meta)


def _valid_backup_meta(bdir: Path):
    """The saved install-na metadata, if its resident-B copy still matches."""
    meta_path = bdir / BACKUP_META
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text())
    except ValueError:
        return None
    if meta.get("had_resident_b"):
        src = bdir / BACKUP_B
        if not src.is_file() or sha256_file(src) != meta.get("previous_sha256"):
            return None
        if meta.get("previous_sha256") in const.NA_B_ALL_SHA256S:
            return None
    return meta


def _write_na(nano, ops, src: Path, digest: str, meta: dict) -> dict:
    from .untether import durable_flush_and_rehash

    expected = {}
    status = ops.copy_verified(src, SLOT_B_FAT, replace=True)
    expected[SLOT_B_FAT] = digest
    print(f"    {status:8}  {SLOT_B_FAT}  sha {digest[:16]}... (silver_resident_na)")

    # Ensure slot B is selected next boot.
    if not ops.exists(SLOT_B_MARKER):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "resident-B.active"
            marker.write_bytes(b"")
            status = ops.copy_verified(marker, SLOT_B_MARKER, replace=False)
            expected[SLOT_B_MARKER] = sha256_file(marker) if marker.stat().st_size else (
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
            )
            print(f"    {status:8}  {SLOT_B_MARKER}  (selector boots slot B next)")
    else:
        print(f"    already   {SLOT_B_MARKER}")

    from .compat import sha256_bytes
    from .untether import check_core_loader_hashes

    durable_flush_and_rehash(nano, {"file_hashes": expected})
    print("RESIDENT_NA_OK")
    print("    rollback  ./start resident restore")
    print("    then      eject, Home+Sleep, open Paint / Free RAM / JS QuickJS")
    return {"installed_sha256": digest, "backup": meta}


def restore(
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
    """Put back the resident-B saved by install-na."""
    from .untether import durable_flush_and_rehash

    bdir = backup_dir()
    meta_path = bdir / BACKUP_META
    if not meta_path.is_file():
        raise Refused(
            "no resident-na backup found. "
            f"Expected {meta_path} from a prior ./start resident install-na."
        )
    meta = json.loads(meta_path.read_text())
    nano = _open_nano(
        nano,
        backup=backup,
        backup_sha256=backup_sha256,
        allow_size=allow_size,
        confirm_untested=confirm_untested,
        skip_backup_file=skip_backup_file,
        skip_wait=skip_wait,
        watch=watch,
        verb="restore",
    )
    ops, mounted = _ops(nano)
    expected = {}
    if meta.get("had_resident_b"):
        src = bdir / BACKUP_B
        if not src.is_file():
            raise Refused(f"backup file missing: {src}")
        digest = sha256_file(src)
        if meta.get("previous_sha256") and digest != meta["previous_sha256"]:
            raise Refused("backup file hash does not match the saved meta")
        status = ops.copy_verified(src, SLOT_B_FAT, replace=True)
        expected[SLOT_B_FAT] = digest
        print(f"    {status:8}  {SLOT_B_FAT}  sha {digest[:16]}... (restored)")
    elif ops.exists(SLOT_B_FAT):
        ops.delete(SLOT_B_FAT)
        print(f"    deleted   {SLOT_B_FAT}  (no prior slot B to restore)")
    if meta.get("had_marker"):
        if not ops.exists(SLOT_B_MARKER):
            import tempfile

            with tempfile.TemporaryDirectory() as td:
                marker = Path(td) / "resident-B.active"
                marker.write_bytes(b"")
                ops.copy_verified(marker, SLOT_B_MARKER, replace=False)
                print(f"    wrote     {SLOT_B_MARKER}")
        else:
            print(f"    already   {SLOT_B_MARKER}")
    elif ops.exists(SLOT_B_MARKER) and not meta.get("had_marker"):
        ops.delete(SLOT_B_MARKER)
        print(f"    deleted   {SLOT_B_MARKER}  (was created by install-na)")

    durable_flush_and_rehash(nano, {"file_hashes": expected} if expected else {})
    print("RESIDENT_RESTORE_OK")
    print("    eject, Home+Sleep")
    return meta


def status() -> int:
    src = None
    try:
        src = na_resident_path()
        digest = na_sha256()
    except Refused as e:
        print(f"nano7kit resident status")
        print(f"  na image  missing ({e})")
        return 1
    print("nano7kit resident status")
    print(f"  na image  {src}  sha {digest[:16]}...")
    meta_path = backup_dir() / BACKUP_META
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text())
        print(f"  backup    {meta_path}")
        print(f"  saved_at  {meta.get('saved_at')}")
        print(f"  previous  {str(meta.get('previous_sha256') or '-')[:16]}...")
        print("  rollback  ./start resident restore")
    else:
        print("  backup    none yet (run ./start resident install-na in Disk Mode)")
    return 0
