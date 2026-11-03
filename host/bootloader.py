"""Guarded 2015 bootloader rewrite. No MSE, no partition, no FAT, no NAND RCE.

MKN52ZP after restore --full had 2012 37A40005 N31.bootloader.rb3 (family 37
while Kind was still Nano7) plus 2015 39A10023 RetailOS. That pair is the
leading Apple-logo hang cause after FAT actually mounted.

IPodUpdate (SCSI C6 0x90/0x91/0x92) is write-only. NAND read in wInd3x is DFU
RCE (cmd_nand_read). This path never uses it.
"""

from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path
from typing import Optional

from . import const
from .compat import Refused, require_typed, sha256_bytes, sha256_file
from .safety import backup_filename_token, bootloader_identity, check_bootloader
from .state import (
    MaintenanceLock,
    read_receipt,
    receipt_exists,
    utc_stamp,
    write_receipt,
)
from .usb import (
    apple_pids,
    late_wtf_present,
    nano7_patch_path,
    prepare_restore,
    recovery_usb_state,
    select_wind3x,
    wait_dfu,
    wait_recovery_entry,
)


BOOTLOADER_STARTED = "BOOTLOADER_STARTED"
BOOTLOADER_OK = "BOOTLOADER_OK"
BOOTLOADER_CHECK_OK = "BOOTLOADER_CHECK_OK"
BOOTLOADER_READBACK_NOTE = (
    "On-device NAND bootloader read-back is not available. IPodUpdate is "
    "write-only (C6 0x90/0x91/0x92). wInd3x nand-read is DFU RCE and is not used."
)


def nano7late_updater_family_id(patch: Optional[Path] = None) -> int:
    """UpdaterFamilyID for Nano7Late from the nano7 wInd3x patch (want 39)."""
    path = Path(patch) if patch is not None else nano7_patch_path()
    text = path.read_text()
    late = text.rfind("Kind:            Nano7Late")
    if late < 0:
        raise Refused("nano7 wInd3x patch is missing Nano7Late")
    hunk = text[max(0, late - 900) : late + 40]
    added = re.search(r"^\+\s*UpdaterFamilyID:\s*(\d+)", hunk, re.M)
    if added:
        return int(added.group(1))
    any_id = re.search(r"UpdaterFamilyID:\s*(\d+)", hunk)
    if not any_id:
        raise Refused("nano7 wInd3x patch Nano7Late hunk has no UpdaterFamilyID")
    return int(any_id.group(1))


def require_family_39(patch: Optional[Path] = None) -> int:
    family = nano7late_updater_family_id(patch)
    if family != const.NANO7LATE_UPDATER_FAMILY_ID:
        raise Refused(
            f"Nano7Late UpdaterFamilyID in the nano7 patch is {family}, want "
            f"{const.NANO7LATE_UPDATER_FAMILY_ID}. 37 fetches 37A40005. "
            "Rebuild wInd3x with ./start deps. Nothing written."
        )
    return family


def extract_stock_bootloader(
    ipsw: Optional[Path] = None,
    *,
    expected_sha256: Optional[str] = None,
) -> Path:
    """N31.bootloader.rb3 from the hashed official 39A10023 IPSW."""
    from .firmware import build_dir, ensure_stock_ipsw

    if ipsw is not None:
        stock_ipsw = Path(ipsw)
        if not stock_ipsw.is_file():
            raise Refused(f"IPSW missing: {stock_ipsw}")
        ipsw_hash = sha256_file(stock_ipsw)
        if expected_sha256 is None and ipsw_hash != const.STOCK_IPSW_SHA256:
            raise Refused(
                f"stock IPSW hash mismatch\n  expected {const.STOCK_IPSW_SHA256}\n"
                f"  actual   {ipsw_hash}"
            )
    else:
        stock_ipsw = ensure_stock_ipsw()
        ipsw_hash = sha256_file(stock_ipsw)
    dest = build_dir() / "firmware" / "N31.bootloader-39A10023.rb3"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(stock_ipsw) as z:
        names = z.namelist()
        members = [
            n
            for n in names
            if n.rsplit("/", 1)[-1].lower() == const.BOOTLOADER_NAME.lower()
        ]
        if not members:
            raise Refused(
                f"{stock_ipsw.name} has no {const.BOOTLOADER_NAME} member. "
                f"First entries: {names[:12]}"
            )
        data = z.read(members[0])
    digest = sha256_bytes(data)
    expected = expected_sha256 or const.BOOTLOADER_39A10023_SHA256
    if digest.startswith(const.BOOTLOADER_37A40005_SHA256_PREFIX):
        raise Refused(
            f"extracted {const.BOOTLOADER_NAME} is 2012 37A40005 (sha256 {digest}). "
            "Refusing. Mid-2015 needs 39A10023. Nothing written."
        )
    if digest != expected:
        raise Refused(
            f"extracted {const.BOOTLOADER_NAME} hash mismatch\n"
            f"  expected {expected}\n  actual   {digest}"
        )
    if dest.is_file() and dest.stat().st_size == len(data) and sha256_file(dest) == digest:
        print(f"    stock {const.BOOTLOADER_NAME} already extracted ({digest[:16]}...)")
        return dest
    dest.write_bytes(data)
    (dest.with_suffix(dest.suffix + ".sha256")).write_text(f"{digest}  {dest.name}\n")
    print(f"    extracted {const.BOOTLOADER_NAME} ({len(data)} bytes, sha256 {digest})")
    check_bootloader(dest, expected_sha256=expected)
    return dest


def print_bootloader_plan(path: Path, digest: str) -> None:
    ident = bootloader_identity(digest)
    family = require_family_39()
    print(f"    file       {path}")
    print(f"    sha256     {digest}")
    print(f"    identity   {ident}")
    print(f"    Nano7Late  UpdaterFamilyID {family} (want {const.NANO7LATE_UPDATER_FAMILY_ID})")
    print(f"    IPSW       {const.STOCK_IPSW_NAME} ({const.STOCK_IPSW_SHA256})")
    if ident == "37A40005":
        raise Refused(
            "this bootloader is 2012 37A40005. Mid-2015 needs 39A10023. Nothing written."
        )
    print(f"    {BOOTLOADER_READBACK_NOTE}")


def _pre_bootloader_dir(serial: str) -> Path:
    from .state import state_dir

    token = backup_filename_token(serial)
    dest = state_dir() / f"pre-bootloader-{utc_stamp()}-{token}"
    try:
        dest.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        dest = state_dir() / f"pre-bootloader-{utc_stamp()}-{token}-{os.getpid()}"
        dest.mkdir(parents=True, exist_ok=True)
    return dest


def readback_on_device(*, serial: str) -> Optional[Path]:
    """IPodUpdate cannot read NAND. Do not call nand-read (RCE)."""
    dest = _pre_bootloader_dir(serial)
    note = (
        "nano7kit pre-bootloader directory\n"
        f"serial={serial}\n"
        f"{BOOTLOADER_READBACK_NOTE}\n"
        "No file was saved because no read path exists.\n"
    )
    (dest / "READBACK.txt").write_text(note)
    print(f"    pre-bootloader dir: {dest}")
    print(f"    {BOOTLOADER_READBACK_NOTE}")
    return None


def _usb_gate_2015(*, accept_2015: bool, what: str) -> str:
    pids = set(apple_pids())
    if const.NANO7_WTF_PID in pids and const.NANO7_LATE_WTF_PID not in pids:
        raise Refused(
            "USB WTF 0x1249 is 2012 Initial. This bootloader command is "
            "Nano7Late / WTF 0x124a / 39A10023 only. Nothing written."
        )
    if not accept_2015:
        if const.NANO7_LATE_WTF_PID in pids:
            raise Refused(
                "USB already shows WTF 0x124a (Mid-2015). Pass --i-accept-2015-refresh. "
                "See docs/HARDWARE-2015.md."
            )
        raise Refused(
            f"{what} is the Mid-2015 39A10023 bootloader path. "
            "Pass --i-accept-2015-refresh. docs/HARDWARE-2015.md"
        )
    state = recovery_usb_state()
    if state == "disk" and const.NANO7_LATE_WTF_PID not in pids:
        raise Refused(
            "USB is 05ac:1267 without WTF 0x124a. Disk PID 0x1267 matches Kind "
            "Nano7 (2012, family 37) first. Unplug, DFU (docs/BUTTONS.md) "
            "so WTF 0x124a can promote Kind to Nano7Late. Nothing written."
        )
    return state


def _select_flasher(*, accept_untested: bool, accept_2015: bool):
    wind3x, tested_bin, note = select_wind3x(need_2015_kind_switch=True)
    print(f"flasher: {wind3x}")
    print(f"         {note}")
    if not tested_bin:
        if not accept_untested:
            raise Refused(
                "this computer has no hardware-tested wInd3x binary (that file is "
                "Linux x86-64). Pass --i-accept-untested-flasher to continue. "
                "The 39A10023 bootloader hash is still enforced."
            )
        print("HARDWARE: this flasher binary is untested.")
    if accept_2015:
        print(
            "HARDWARE: Mid-2015 nano 7G (MKN52 / A1446 Rev A). "
            "Patched wInd3x uses family 39 / 39A10023 bootloader, never 37A40005. "
            "docs/HARDWARE-2015.md"
        )
    return wind3x


def check_bootloader_device(
    *,
    serial: str,
    accept_untested_flasher: bool = False,
    accept_2015_refresh: bool = False,
    ipsw: Optional[Path] = None,
    expected_sha256: Optional[str] = None,
) -> dict:
    """Read-only: extract+hash, family mapping, identity. No IPodUpdate write."""
    serial = (serial or "").strip()
    if not serial:
        raise Refused(
            "device serial is required (Settings > General > About, or the back). "
            "Pass --serial."
        )
    print("==> bootloader --check-only (no NAND write)")
    family = require_family_39()
    bl = extract_stock_bootloader(ipsw, expected_sha256=expected_sha256)
    digest = check_bootloader(bl, expected_sha256=expected_sha256)
    print_bootloader_plan(bl, digest)
    readback_on_device(serial=serial)
    state = _usb_gate_2015(accept_2015=accept_2015_refresh, what="--check-only")
    print(f"    USB state now: {state}")
    wind3x = _select_flasher(
        accept_untested=accept_untested_flasher, accept_2015=accept_2015_refresh
    )
    if state == "none":
        wait_dfu()
    else:
        wait_recovery_entry()
    log = prepare_restore(wind3x, serial, accept_2015=accept_2015_refresh)
    if "37A40005" in log and "Downloading IPSW" in log:
        raise Refused(
            "prepare-only log downloaded 37A40005. Family mapping is wrong. "
            "Rebuild wInd3x with ./start deps. Nothing written."
        )
    write_receipt(
        BOOTLOADER_CHECK_OK,
        {
            "utc": utc_stamp(),
            "serial": serial,
            "bootloader_sha256": digest,
            "identity": bootloader_identity(digest),
            "family": str(family),
            "readback": "unavailable",
        },
    )
    print("BOOTLOADER_CHECK_OK")
    print("    Would write only N31.bootloader.rb3 39A10023 (no MSE, no FAT).")
    print("    On-device contents cannot be compared; IPodUpdate cannot read NAND.")
    return {
        "check_only": True,
        "serial": serial,
        "sha256": digest,
        "identity": bootloader_identity(digest),
        "family": family,
        "path": str(bl),
        "readback": None,
        "log": log,
    }


def write_bootloader(
    *,
    serial: str,
    accept_untested_flasher: bool = False,
    accept_2015_refresh: bool = False,
    typed_confirm: Optional[str] = None,
    accept_brick: bool = False,
    ipsw: Optional[Path] = None,
    expected_sha256: Optional[str] = None,
) -> dict:
    """One wInd3x process: DFU/WTF 0x124a -> Disk -> IPodUpdate bootloader only."""
    serial = (serial or "").strip()
    if not serial:
        raise Refused(
            "device serial is required (Settings > General > About, or the back). "
            "Pass --serial."
        )
    family = require_family_39()
    bl = extract_stock_bootloader(ipsw, expected_sha256=expected_sha256)
    digest = check_bootloader(bl, expected_sha256=expected_sha256)
    print_bootloader_plan(bl, digest)
    if bootloader_identity(digest) == "39A10023" and receipt_exists(BOOTLOADER_OK):
        prev = read_receipt(BOOTLOADER_OK)
        if prev.get("bootloader_sha256") == digest and prev.get("serial") == serial:
            raise Refused(
                "BOOTLOADER_OK already records 39A10023 for this serial. "
                "On-device read-back is not available, so a second write is refused. "
                "If the Apple logo is still stuck, stop and read docs/RECOVERY.md. "
                "Do not pass --replace. Nothing written."
            )
    if receipt_exists(BOOTLOADER_STARTED) and not receipt_exists(BOOTLOADER_OK):
        raise Refused(
            "a bootloader write was started and may have reached the device. "
            "Stop. Read docs/RECOVERY.md. Do not retry blindly. Nothing written."
        )
    state = _usb_gate_2015(accept_2015=accept_2015_refresh, what="bootloader --run")
    print(f"    USB state now: {state}")
    wind3x = _select_flasher(
        accept_untested=accept_untested_flasher, accept_2015=accept_2015_refresh
    )
    if not accept_brick:
        require_typed(
            "BOOTLOADER",
            f"About to write ONLY the 2015 39A10023 bootloader to serial {serial}. "
            "Firmware MSE, partition table, and FAT are not written.",
            typed=typed_confirm,
        )
    if state == "none":
        wait_dfu()
    else:
        wait_recovery_entry()
    readback_on_device(serial=serial)
    argv = ["restore", "--bootloader-only", "--bootloader", str(bl)]
    joined = " ".join(argv).lower()
    if "nand" in joined or "--firmware" in joined or "--full" in joined:
        raise Refused("internal: bootloader argv must not include nand, --firmware, or --full")
    from .usb import run_wind3x

    print("==> BOOTLOADER_START (IPodUpdate kind=1 only)")
    write_receipt(
        BOOTLOADER_STARTED,
        {
            "utc": utc_stamp(),
            "serial": serial,
            "bootloader_sha256": digest,
            "identity": "39A10023",
            "family": str(family),
        },
    )
    with MaintenanceLock():
        r = run_wind3x(argv, wind3x)
    log = (r.stdout or "") + (r.stderr or "")
    from .state import state_dir

    (state_dir() / f"bootloader-{utc_stamp()}.log").write_text(log)
    if r.returncode != 0:
        raise Refused(
            "wInd3x restore --bootloader-only failed. Do not retry blindly. "
            "Keep the backup. docs/RECOVERY.md\n" + log[-4000:]
        )
    if "37A40005" in log:
        raise Refused(
            "bootloader write log mentions 37A40005. Treating as failure. "
            "Do not retry blindly.\n" + log[-4000:]
        )
    if "Writing firmware..." in log:
        raise Refused(
            "bootloader-only log shows a firmware write. Treating as failure. "
            "Do not retry blindly.\n" + log[-4000:]
        )
    if "Reformatting system partition" in log:
        raise Refused(
            "bootloader-only log shows IPodRepartition. Treating as failure. "
            "Do not retry blindly.\n" + log[-4000:]
        )
    if "Writing bootloader" not in log:
        raise Refused(
            "bootloader-only log does not show a bootloader write:\n" + log[-4000:]
        )
    if "no firmware and no partition were written" not in log:
        raise Refused(
            "bootloader-only log does not prove MSE/partition were skipped:\n"
            + log[-4000:]
        )
    write_receipt(
        BOOTLOADER_OK,
        {
            "utc": utc_stamp(),
            "serial": serial,
            "bootloader_sha256": digest,
            "identity": "39A10023",
            "family": str(family),
        },
    )
    print("BOOTLOADER_OK")
    print("    Wrote 39A10023 N31.bootloader.rb3 only. No MSE. No FAT. No --full.")
    print("    Keep the cable in until the screen leaves white Do Not Disconnect.")
    print("    Then unplug. If Home shows the Home screen, the hang hypothesis held.")
    print("    Finder Option+Restore of iPod_1.1.2_39A10023.ipsw remains last-resort")
    print("    boot recovery (that path creates HFS+).")
    return {
        "ok": True,
        "serial": serial,
        "sha256": digest,
        "identity": "39A10023",
        "family": family,
        "path": str(bl),
        "log": log,
    }


def run_bootloader_command(
    *,
    serial: str,
    check_only: bool,
    run: bool,
    accept_untested_flasher: bool = False,
    accept_2015_refresh: bool = False,
    typed_confirm: Optional[str] = None,
    accept_brick: bool = False,
    ipsw: Optional[Path] = None,
    expected_sha256: Optional[str] = None,
) -> dict:
    if run and check_only:
        raise Refused("--check-only cannot be combined with --run")
    if not run:
        return check_bootloader_device(
            serial=serial,
            accept_untested_flasher=accept_untested_flasher,
            accept_2015_refresh=accept_2015_refresh,
            ipsw=ipsw,
            expected_sha256=expected_sha256,
        )
    return write_bootloader(
        serial=serial,
        accept_untested_flasher=accept_untested_flasher,
        accept_2015_refresh=accept_2015_refresh,
        typed_confirm=typed_confirm,
        accept_brick=accept_brick,
        ipsw=ipsw,
        expected_sha256=expected_sha256,
    )
