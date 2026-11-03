"""One-command install: check, backup, bootloader-if-needed, flash, /Apps, verify.

`./start install` never writes NAND unless `--run` is passed AND a typed
BOOTLOADER or UNTETHER confirm is required for that write. Receipts skip
work that already ran: BOOTLOADER_OK skips the bootloader, FIRMWARE_ATTEMPTED
/ UNTETHER_OK skip the Helvetica MSE. FAT `/Apps` copy is not a NAND write.

For a unit that already has Helvetica and Home icons (MKN52 after install-untether),
`./start install` is check + reused backup + skip NAND + durable /Apps +
verify. C6 96 04 CHECK CONDITION is expected; VERIFY_OK is /Apps sha match.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .compat import Refused
from .device import Nano
from .state import read_receipt, receipt_exists, state_dir, utc_stamp, write_receipt


INSTALL_OK = "INSTALL_OK"
INSTALL_SKIP_BOOTLOADER = "BOOTLOADER_SKIP"
INSTALL_SKIP_FIRMWARE = "FIRMWARE_SKIP"


def firmware_attempt_path(mse_hash: str) -> Path:
    return state_dir() / f"FIRMWARE_ATTEMPTED-{mse_hash[:16]}"


def bootloader_skip_reason(serial: str) -> Optional[str]:
    from .bootloader import BOOTLOADER_OK

    if not receipt_exists(BOOTLOADER_OK):
        return None
    prev = read_receipt(BOOTLOADER_OK)
    ident = prev.get("identity") or ""
    prev_serial = prev.get("serial") or ""
    if ident == "39A10023" and (not prev_serial or prev_serial == serial):
        return (
            "BOOTLOADER_OK already records 39A10023 for this serial. "
            "On-device NAND read-back is not available, so a second write is skipped. "
            "Do not pass --replace. Do not rewrite the bootloader."
        )
    return (
        "BOOTLOADER_OK exists. A second bootloader write is skipped. "
        "Do not pass --replace."
    )


def firmware_skip_reason(mse_hash: str) -> Optional[str]:
    from .untether import UNTETHER_OK, UNTETHER_STARTED

    attempt = firmware_attempt_path(mse_hash)
    if attempt.exists() or receipt_exists(UNTETHER_STARTED) or receipt_exists(UNTETHER_OK):
        return (
            "this exact firmware was already attempted (FIRMWARE_ATTEMPTED / "
            "UNTETHER_OK). One write, no retry. Do not re-flash. Do not pass "
            "--replace. Do not run restore --full."
        )
    return None


def run_install(
    nano: Nano,
    *,
    mse: Path,
    serial: str,
    backup: Path,
    backup_sha256: str,
    run: bool = False,
    allow_size: Optional[int] = None,
    confirm_untested: bool = False,
    accept_2015_refresh: bool = False,
    accept_untested_flasher: bool = False,
    accept_untested_firmware: bool = False,
    typed_confirm: Optional[str] = None,
    accept_brick: bool = False,
    skip_backup_file: bool = False,
    with_bootloader: bool = False,
    verify_bytes: bool = True,
    apps: Optional[list] = None,
) -> dict:
    """Check, backup (already resolved), skip-or-write NAND, durable /Apps, verify."""
    from .bootloader import write_bootloader
    from .safety import check_mse
    from .untether import (
        check_untether_device,
        install_loader_files,
        verify_untether,
        write_untether_firmware,
    )

    serial = (serial or "").strip()
    print("==> ./start install (check, backup, bootloader-if-needed, flash, /Apps, verify)")
    print("    NAND writes happen only with --run and the matching typed word.")
    print("    FAT /Apps copy is not a NAND write. Music stays. No --replace.")

    mse_hash = check_mse(mse, allow_untested=accept_untested_firmware)
    check_untether_device(
        nano,
        mse=mse,
        serial=serial,
        allow_size=allow_size,
        confirm_untested=confirm_untested,
        accept_2015_refresh=accept_2015_refresh,
        accept_untested_flasher=accept_untested_flasher,
        accept_untested_firmware=accept_untested_firmware,
    )

    bl_skip = bootloader_skip_reason(serial)
    fw_skip = firmware_skip_reason(mse_hash)
    wrote_bootloader = False
    wrote_firmware = False

    if bl_skip:
        print(f"{INSTALL_SKIP_BOOTLOADER}: {bl_skip}")
    elif not with_bootloader:
        print(
            f"{INSTALL_SKIP_BOOTLOADER}: the bootloader is left alone (default). "
            "Only a nano whose bootloader was replaced by a full restore needs "
            "--with-bootloader."
        )
    elif not run:
        print(
            f"{INSTALL_SKIP_BOOTLOADER}: no --run. Would write 39A10023 "
            "N31.bootloader.rb3 only if BOOTLOADER_OK is missing, with "
            "--typed-confirm BOOTLOADER. This unit's NAND is not written."
        )
    else:
        word = (typed_confirm or "").strip().upper()
        if word and word != "BOOTLOADER":
            raise Refused(
                "bootloader write is still needed. Pass --typed-confirm BOOTLOADER "
                "(global flag, before install). Do not combine a firmware write in "
                "the same command. After BOOTLOADER_OK, reboot, then ./start install --run "
                "with --typed-confirm UNTETHER. Nothing written."
            )
        print("==> bootloader-if-needed: writing 39A10023 N31.bootloader.rb3 only")
        write_bootloader(
            serial=serial,
            accept_untested_flasher=accept_untested_flasher,
            accept_2015_refresh=accept_2015_refresh,
            typed_confirm=typed_confirm,
            accept_brick=accept_brick,
        )
        wrote_bootloader = True
        print(
            "BOOTLOADER_OK. Keep the cable in, then unplug. Reboot to Home. "
            "Re-run ./start install --run with --typed-confirm UNTETHER for firmware. "
            "One NAND write per command."
        )
        rec = {
            "ok": True,
            "serial": serial,
            "bootloader": "wrote",
            "firmware": "deferred",
            "apps": "skipped",
            "verify": "skipped",
        }
        write_receipt(
            INSTALL_OK,
            {
                "utc": utc_stamp(),
                "serial": serial,
                "bootloader": "wrote",
                "firmware": "deferred",
            },
        )
        return rec

    if fw_skip:
        print(f"{INSTALL_SKIP_FIRMWARE}: {fw_skip}")
    elif not run:
        print(
            f"{INSTALL_SKIP_FIRMWARE}: no --run. Would restore --firmware the "
            "Helvetica MSE only if FIRMWARE_ATTEMPTED is missing, with "
            "--typed-confirm UNTETHER. This unit's NAND is not written."
        )
    elif wrote_bootloader:
        print(f"{INSTALL_SKIP_FIRMWARE}: bootloader just wrote; firmware is the next command.")
    else:
        word = (typed_confirm or "").strip().upper()
        if word and word != "UNTETHER":
            raise Refused(
                "firmware write is still needed. Pass --typed-confirm UNTETHER "
                "(global flag, before install). Nothing written."
            )
        print("==> flash-if-needed: restore --firmware only (durable /Apps first)")
        write_untether_firmware(
            nano,
            mse=mse,
            serial=serial,
            backup=backup,
            backup_sha256=backup_sha256,
            allow_size=allow_size,
            confirm_untested=confirm_untested,
            accept_2015_refresh=accept_2015_refresh,
            accept_untested_flasher=accept_untested_flasher,
            accept_untested_firmware=accept_untested_firmware,
            typed_confirm=typed_confirm,
            accept_brick=accept_brick,
            skip_backup_file=skip_backup_file,
            verify_bytes=verify_bytes,
            apps=apps,
        )
        wrote_firmware = True

    if not wrote_firmware:
        print("==> /Apps durable copy (fsync, F_FULLFSYNC, unmount, remount rehash)")
        print("    Same code path as install-untether and untether --run.")
        install_loader_files(
            nano,
            backup=backup,
            backup_sha256=backup_sha256,
            allow_size=allow_size,
            confirm_untested=confirm_untested,
            replace=False,
            skip_backup_file=skip_backup_file,
            verify_bytes=verify_bytes,
            apps=apps,
            leave_mounted=True,
        )
        print("    No firmware write. Do not DFU. Do not re-flash.")

    print("==> verify (/Apps sha match; C6 CHECK CONDITION expected under Helvetica)")
    verify = verify_untether(
        nano,
        serial=serial,
        allow_size=allow_size,
        confirm_untested=confirm_untested,
    )
    write_receipt(
        INSTALL_OK,
        {
            "utc": utc_stamp(),
            "serial": serial,
            "bootloader": "wrote" if wrote_bootloader else "skip",
            "firmware": "wrote" if wrote_firmware else "skip",
            "apps": "yes",
            "verify": "yes",
            "mse": mse_hash,
        },
    )
    print(INSTALL_OK)
    print("    On-device sign: Blocks, Game Boy, Probe on Home after Sleep/Wake+Home.")
    print("    Music library: ./start sync (Disk Mode). Game Boy ROMs: ./start roms add.")
    return {
        "ok": True,
        "serial": serial,
        "bootloader": "wrote" if wrote_bootloader else "skip",
        "firmware": "wrote" if wrote_firmware else "skip",
        "apps": "yes",
        "verify": verify,
        "sha256": mse_hash,
        "identity": "39A10023",
    }
