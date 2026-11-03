"""Put NanoApps 1.0's firmware on a nano that Finder cannot update.

Finder's Check for Update needs Apple's C6/96 SCSI handler. nano7-untethered
overwrites it (NanoApps keeps it and uses C6/98: NanoApps
firmware/src/payload/exploit_config.rs), so on an untethered nano Finder fails
with 1461, and in DFU it cannot read the ECID and offers no software.

This writes the Firmware.MSE from the pinned NanoApps-2015-Model.ipsw with the
same wInd3x `restore --firmware` step that put nano7-untethered on the iPod:
firmware partition only, no bootloader, no partition table, no FAT format, so
music and files stay. The MSE is Apple's 39A10023 image with only the resource
volume changed (Helvetica.ttf and /NanoApps/loader.hbapp); tests/test_nabase_fw.py
records the check. Same rules as the untethered flash: hashed full backup,
dry run first, typed FLASH, one write and no retry per image.
"""

from __future__ import annotations

import plistlib
import zipfile
from pathlib import Path
from typing import Optional

from . import const
from .compat import Refused, file_size, require_typed, sha256_file
from .nabase import NA_IPSW, fetch_release_file
from .state import read_receipt, repo_root, state_dir, utc_stamp, write_receipt

MODEL = "2015"
# Firmware.MSE inside NanoApps-2015-Model.ipsw (build-00d9fb3c...), 193,114,112 bytes.
MSE_SHA256 = "ff8dbefbe812fd392c0f6c01908395715752e102aacf94da722bb7d025a61baa"
MSE_NAME = "Firmware-NanoApps-2015.MSE"
IPSW_MEMBERS = {"Firmware.MSE", "N31.bootloader.rb3", "manifest.plist"}
DRY_RUN = "NABASE_FLASH_DRY_RUN_OK"
STARTED = "NABASE_FLASH_STARTED"
DONE = "NABASE_FLASH_OK"


def attempt_receipt() -> Path:
    return state_dir() / f"FIRMWARE_ATTEMPTED-{MSE_SHA256[:16]}"


def check_ipsw(ipsw: Path) -> dict:
    """Pinned hash, exact members, 1.1.2 / 39A10023 / family 39, stock bootloader."""
    name, digest = NA_IPSW[MODEL]
    if sha256_file(ipsw) != digest:
        raise Refused(f"{ipsw} is not the pinned {name}")
    with zipfile.ZipFile(ipsw) as z:
        names = {i.filename for i in z.infolist() if not i.is_dir()}
        if names != IPSW_MEMBERS:
            raise Refused(f"unexpected files in {name}: {sorted(names)}")
        payload = plistlib.loads(z.read("manifest.plist")).get("FirmwarePayload", {})
        boot = sha256_file_bytes(z.read("N31.bootloader.rb3"))
    want = {"BuildVersion": "39A10023", "ProductVersion": "1.1.2",
            "UpdaterFamilyID": const.NANO7LATE_UPDATER_FAMILY_ID, "FirmwareName": "Firmware.MSE"}
    for key, value in want.items():
        if payload.get(key) != value:
            raise Refused(f"{name} manifest {key} is {payload.get(key)!r}, expected {value!r}")
    # Not written (firmware-only), but it shows the IPSW expects the bootloader
    # Apple's 1.1.2 put on this nano.
    if boot != const.BOOTLOADER_39A10023_SHA256:
        raise Refused(f"{name} bootloader is not Apple's 39A10023 one")
    return payload


def sha256_file_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def extract_mse(ipsw: Optional[Path] = None, dest_dir: Optional[Path] = None, *, out=print) -> Path:
    """Fetch (or reuse) the pinned IPSW and write its checked Firmware.MSE to build/."""
    from .mse import inspect_mse_flash_identity

    ipsw = Path(ipsw) if ipsw else fetch_release_file(*NA_IPSW[MODEL], out=out)
    check_ipsw(ipsw)
    dest_dir = Path(dest_dir) if dest_dir else repo_root() / "build" / "nanoapps-release"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / MSE_NAME
    if not (dest.is_file() and not dest.is_symlink() and sha256_file(dest) == MSE_SHA256):
        tmp = dest.with_suffix(".part")
        with zipfile.ZipFile(ipsw) as z, z.open("Firmware.MSE") as s, tmp.open("wb") as d:
            while chunk := s.read(1 << 20):
                d.write(chunk)
        if sha256_file(tmp) != MSE_SHA256:
            tmp.unlink()
            raise Refused("Firmware.MSE in the NanoApps IPSW does not match the pinned sha256")
        tmp.replace(dest)
    inspect_mse_flash_identity(dest, expected_sha256=MSE_SHA256)
    out(f"    firmware  {dest} (NanoApps 1.0, 39A10023, sha256 {MSE_SHA256[:16]}...)")
    return dest


def check_mse(mse: Path) -> str:
    from .compat import die_if_symlink
    from .mse import inspect_mse_flash_identity

    mse = Path(mse)
    if not mse.is_file():
        raise Refused(f"firmware image missing: {mse}")
    die_if_symlink(mse, "firmware image")
    ident = inspect_mse_flash_identity(mse, expected_sha256=MSE_SHA256)
    return ident["sha256"]


def check_backup_image(backup: Path, backup_sha256: str) -> int:
    """Full raw backup of this nano, hashed. Its own layout must pass the size rule."""
    from .layout import inspect_image
    from .safety import check_backup, check_disk_size

    backup = Path(backup)
    if not backup.is_file():
        raise Refused(f"backup image {backup} is not on this Mac. Run ./start backup first.")
    size = file_size(backup)
    layout = inspect_image(backup)
    check_disk_size(size, what="backup", layout=layout)
    check_backup(backup, backup_sha256, size)
    return size


def flash(
    *,
    mse: Path,
    backup: Path,
    backup_sha256: str,
    serial: str,
    dry_run: bool = True,
    accept_2015_refresh: bool = False,
    accept_untested_flasher: bool = False,
    typed_confirm: Optional[str] = None,
    out=print,
) -> dict:
    """DFU, then wInd3x restore --firmware with the NanoApps MSE. Nothing else."""
    from .state import MaintenanceLock
    from .usb import flash_restore, parse_serial, prepare_restore, select_wind3x, wait_dfu

    mse_hash = check_mse(mse)
    serial = (serial or "").strip()
    if not serial:
        raise Refused("device serial is required (Settings > General > About, or the back). Pass --serial.")
    if not accept_2015_refresh:
        raise Refused("this is the Mid-2015 path. Pass --i-accept-2015-refresh. docs/HARDWARE-2015.md")
    check_backup_image(backup, backup_sha256)
    wind3x, tested_bin, note = select_wind3x(need_2015_kind_switch=True)
    out(f"flasher: {wind3x}")
    out(f"         {note}")
    if not tested_bin and not accept_untested_flasher:
        raise Refused("this computer has no hardware-tested wInd3x binary. "
                      "Pass --i-accept-untested-flasher. The firmware image hash is still enforced.")
    if attempt_receipt().exists():
        raise Refused(f"the NanoApps firmware was already attempted (receipt {attempt_receipt()}). "
                      "One write, no retry, including dry runs. See docs/RECOVERY.md.")

    with MaintenanceLock():
        if dry_run:
            out("==> DFU dry run (every check, no write)")
            wait_dfu()
            log = prepare_restore(wind3x, serial, accept_2015=True)
            seen = parse_serial(log) or serial
            write_receipt(DRY_RUN, {"utc": utc_stamp(), "serial": seen, "mse_sha256": mse_hash,
                                    "backup_sha256": backup_sha256})
            out("NABASE_FLASH_DRY_RUN_OK; nothing written. The iPod is in recovery mode now:")
            out("    hold Home and Sleep/Wake until it restarts, then put it back in DFU for --run.")
            return {"dry_run": True, "serial": seen}

        dry = read_receipt(DRY_RUN)
        if (dry.get("mse_sha256") != mse_hash or dry.get("serial") != serial
                or dry.get("backup_sha256") != backup_sha256):
            raise Refused("run the dry run first with the same serial and backup "
                          "(./start ... nabase flash --dry-run). Nothing written.")
        require_typed(
            "FLASH",
            f"About to write NanoApps 1.0 firmware {mse_hash[:16]}... to nano serial {serial}. "
            "Firmware only: bootloader, partitions, music and files are not written. "
            "This can still brick the iPod.",
            typed=typed_confirm,
        )
        out("==> FLASH_START (one write, no retry)")
        wait_dfu()
        log = prepare_restore(wind3x, serial, accept_2015=True)
        seen = parse_serial(log)
        if seen and seen != serial:
            raise Refused(f"prepare serial {seen} does not match {serial}. Nothing written.")
        stamp = {"utc": utc_stamp(), "mse_sha256": mse_hash, "serial": serial}
        write_receipt(STARTED, stamp)
        write_receipt(attempt_receipt().name, stamp)
        log = flash_restore(wind3x, mse)
    write_receipt(DONE, {"utc": utc_stamp(), "serial": serial, "mse_sha256": mse_hash})
    out("NABASE_FLASH_OK")
    out("    Wrote the NanoApps 1.0 firmware with restore --firmware only.")
    out("    Keep the cable in until the iPod shows its Home screen.")
    out("    Then Disk Mode and ./install --nanoapps puts the nano7kit Launcher and apps back.")
    return {"dry_run": False, "ok": True, "serial": serial, "log_tail": log[-500:]}
