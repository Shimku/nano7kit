"""Stock recovery: DFU -> hashed IPSW MSE -> optional data-slice restore.

This is the LeUnBrIck idea implemented with this toolkit's own checks. No
LeUnBrIck code, no Apple DFU blobs in git, no typed disk names.

Typical official nano 7G IPSW members are Firmware.MSE and N31.bootloader.rb3.
WTF.x1234 / FIRMWARE.x1249 / FIRMWARE.x124a are Apple DFU CDN files that
wInd3x downloads at restore time, keyed by WTF 0x1249 (2012) vs 0x124a
(Mid-2015). If the IPSW happens to contain .dfu members they are extracted
and listed; they are still not flashed by this module.

Observed on MKN52ZP 2026-10-28 (darwin/amd64): restore --full + IPodRepartition
worked. FAT format via kernel IOMedia failed (macOS handoff). Data-slice copy
is still untested.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Optional

from . import const
from .compat import Refused, require_typed
from .device import Nano, usable_nano
from .fat import unmount
from .layout import DiskLayout, inspect_image, pick_format_partition
from .safety import (
    backup_verify_size,
    check_backup,
    check_disk_size,
    check_stock_mse,
    check_usb_serial,
    require_usable_disk_size,
    usable_disk_size,
)
from .state import MaintenanceLock, last_backup, receipt_exists, utc_stamp, write_receipt
from .usb import (
    diskmode_usb_present,
    late_wtf_present,
    parse_serial,
    prepare_restore,
    recovery_usb_state,
    select_wind3x,
    wait_dfu,
    wait_recovery_entry,
)
from .winpod import (
    check_format_only_backup_vs_live,
    extract_stock_mse,
    format_after_restore,
    print_layout,
    restore_full,
    wait_for_nano,
    _enter_recovery_diskmode,
    _format_kernel_node,
    _save_preformat_snapshot,
)


RESCUE_STARTED = "RESCUE_STARTED"
RESCUE_OK = "RESCUE_OK"
RESCUE_DRY_RUN = "RESCUE_DRY_RUN_OK"


def inspect_ipsw_members(ipsw: Path) -> dict:
    ipsw = Path(ipsw)
    if not ipsw.is_file():
        raise Refused(f"IPSW missing: {ipsw}")
    with zipfile.ZipFile(ipsw) as z:
        names = z.namelist()
    dfu = [n for n in names if n.lower().endswith(".dfu")]
    mse = [n for n in names if n.rsplit("/", 1)[-1].lower() == "firmware.mse"]
    boot = [n for n in names if "bootloader" in n.lower()]
    return {"names": names, "dfu": dfu, "mse": mse, "bootloader": boot}


def report_ipsw_payloads(ipsw: Path) -> dict:
    info = inspect_ipsw_members(ipsw)
    print(f"IPSW {ipsw.name}: {len(info['names'])} members")
    print("  Firmware.MSE: " + (", ".join(info["mse"]) or "(none)"))
    print("  bootloader:   " + (", ".join(info["bootloader"]) or "(none)"))
    if info["dfu"]:
        print("  .dfu members: " + ", ".join(info["dfu"]))
        print("  Those DFU files stay on this computer. This toolkit still")
        print("  lets wInd3x fetch recovery by WTF PID (0x1249 / 0x124a).")
    else:
        print("  .dfu members: none (expected for iPod_1.1.2_39A10023.ipsw)")
        print("  wInd3x downloads Apple WTF/recovery DFU at restore time.")
        print("  2012 WTF 0x1249 -> 0x12490000 / 37A40005 family")
        print("  2015 WTF 0x124a -> 0x124a0000 / 39A10023 family")
    return info


def layouts_compatible_for_data(backup: DiskLayout, live: DiskLayout) -> Optional[str]:
    """Return None if a data-slice copy is allowed, else a refusal reason."""
    if backup.is_macpod() and live.is_winpod():
        return "backup is Macpod (HFS+). Live nano is Winpod (FAT32). Will not copy HFS onto FAT."
    if backup.is_winpod() and live.is_macpod():
        return "backup is Winpod (FAT32). Live nano is Macpod (HFS+). Will not copy FAT onto HFS."
    if backup.scheme != live.scheme:
        return (
            f"backup scheme is {backup.scheme}, live scheme is {live.scheme}. "
            "Refusing to copy a data slice across partition maps."
        )
    bdata, ldata = backup.data, live.data
    if bdata is None or ldata is None:
        return "backup or live image has no data partition"
    if bdata.size_bytes != ldata.size_bytes:
        return (
            f"data slice size mismatch: backup {bdata.size_bytes} vs live {ldata.size_bytes}"
        )
    bfw, lfw = backup.firmware, live.firmware
    if bfw is None or lfw is None:
        return (
            "backup or live image has no firmware partition on the USB map. "
            "nano 7G Finder Restore is APM+HFS only (MSE is in NAND). "
            "Data-slice restore needs matching Winpod or Macpod-with-MDFW "
            "layouts. To put this backup back, restore the whole image, not "
            "--restore-data."
        )
    return None


def copy_data_slice(src: Path, dest: Path, src_layout: DiskLayout, dest_layout: DiskLayout) -> str:
    """Copy the data partition only. Never the firmware (type 0x00 / Apple_MDFW) slice."""
    reason = layouts_compatible_for_data(src_layout, dest_layout)
    if reason:
        raise Refused(reason)
    src_data = src_layout.data
    dest_data = dest_layout.data
    assert src_data is not None and dest_data is not None
    fw_before = _read_prefix(dest, dest_layout.firmware.start_bytes if dest_layout.firmware else 0, 16)
    digest = _copy_region(
        src,
        dest,
        src_data.start_bytes,
        dest_data.start_bytes,
        src_data.size_bytes,
    )
    fw_after = _read_prefix(dest, dest_layout.firmware.start_bytes if dest_layout.firmware else 0, 16)
    if fw_before != fw_after:
        raise Refused(
            "firmware slice changed while restoring the data partition. Stop. "
            "Keep the backup. docs/RECOVERY.md"
        )
    return digest


def _read_prefix(path: Path, offset: int, size: int) -> bytes:
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read(size)
    return data


def _copy_region(src: Path, dest: Path, src_off: int, dest_off: int, size: int) -> str:
    import hashlib

    if size < 1:
        raise Refused("data slice size is zero")
    hasher = hashlib.sha256()
    buf = 1024 * 1024
    remaining = size
    with open(src, "rb") as inf, open(dest, "r+b") as outf:
        inf.seek(src_off)
        outf.seek(dest_off)
        while remaining:
            chunk = inf.read(min(buf, remaining))
            if not chunk:
                raise Refused(
                    f"short read from {src} at offset {src_off + (size - remaining)}"
                )
            outf.write(chunk)
            hasher.update(chunk)
            remaining -= len(chunk)
        outf.flush()
        os.fsync(outf.fileno())
    return hasher.hexdigest()


def rescue_nano(
    nano: Optional[Nano],
    *,
    backup: Path,
    backup_sha256: str,
    serial: str,
    dry_run: bool = True,
    allow_size: Optional[int] = None,
    confirm_untested: bool = False,
    accept_brick: bool = False,
    skip_backup_file: bool = False,
    accept_untested_flasher: bool = False,
    accept_2015_refresh: bool = False,
    restore_data: bool = False,
    format_only: bool = False,
    replace: bool = False,
    mse: Optional[Path] = None,
    typed_confirm: Optional[str] = None,
    ipsw: Optional[Path] = None,
) -> dict:
    """Stock 39A10023 restore --full, then FAT32 format and/or data-slice restore."""
    serial = (serial or "").strip()
    if not serial:
        raise Refused(
            "device serial is required (Settings > General > About, or the back). "
            "Pass --serial."
        )
    rec = last_backup(nano.usb_serial if nano else None)
    if format_only and not restore_data:
        if nano is not None and usable_nano(nano) is None:
            print(
                f"    ignoring kernel node {nano.raw_device} size {nano.size} "
                "(0 is not a real nano; not in recovery diskmode yet)"
            )
        nano = usable_nano(nano)
    if nano:
        check_usb_serial(nano.usb_serial)
    elif rec and rec.get("usb_serial"):
        check_usb_serial(rec.get("usb_serial"))
    size = backup_verify_size(
        Path(backup),
        nano_size=nano.size if nano else None,
        receipt_size=rec.get("size") if rec else None,
        format_only=format_only,
        scsi_dump=False,
    )
    size_what = "backup" if format_only else "backup/nano"
    check_disk_size(
        size, allow_size=allow_size, confirm_untested=confirm_untested, what=size_what
    )
    check_backup(backup, backup_sha256, size, skip_file=skip_backup_file)

    backup_layout = None
    if backup.is_file() and backup.stat().st_size >= 512:
        backup_layout = inspect_image(backup)
        print("==> backup layout")
        print_layout(backup_layout, backup)

    if format_only:
        backup_size = size
        if Path(backup).is_file():
            from .compat import file_size as _file_size

            backup_size = _file_size(Path(backup))
        return _rescue_format_only(
            nano,
            serial=serial,
            accept_brick=accept_brick,
            typed_confirm=typed_confirm,
            restore_data=restore_data,
            backup=backup,
            backup_layout=backup_layout,
            backup_size=backup_size,
            dry_run=dry_run,
            accept_untested_flasher=accept_untested_flasher,
            accept_2015_refresh=accept_2015_refresh,
            backup_sha256=backup_sha256,
        )

    from .firmware import ensure_stock_ipsw

    stock_ipsw = ensure_stock_ipsw(Path(ipsw) if ipsw else None)
    report_ipsw_payloads(stock_ipsw)
    stock = Path(mse) if mse else extract_stock_mse(stock_ipsw)
    stock_hash = check_stock_mse(stock)
    print(f"stock MSE: {stock} ({stock_hash})")

    wind3x, tested_bin, note = select_wind3x(need_2015_kind_switch=accept_2015_refresh)
    print(f"flasher: {wind3x}")
    print(f"         {note}")
    if not tested_bin:
        if not accept_untested_flasher:
            raise Refused(
                "this computer has no hardware-tested wInd3x binary (that file is "
                "Linux x86-64). Pass --i-accept-untested-flasher to continue. "
                "The stock IPSW hash is still enforced."
            )
        print("HARDWARE: this flasher binary is untested.")

    if accept_2015_refresh:
        print(
            "HARDWARE: Mid-2015 nano 7G (MKN52 / A1446 Rev A) is untested. "
            "Patched wInd3x uses 0x124a / 39A10023 recovery. docs/HARDWARE-2015.md"
        )
    elif late_wtf_present():
        raise Refused(
            "USB already shows WTF 0x124a (Mid-2015). Pass --i-accept-2015-refresh. "
            "See docs/HARDWARE-2015.md."
        )

    if receipt_exists(RESCUE_STARTED) and not replace and not dry_run:
        raise Refused(
            "a rescue was started and may have written the device "
            f"({RESCUE_STARTED}). If the nano still boots, take a new backup "
            "and pass --replace. If it does not, stop. Do not retry blindly. "
            "docs/RECOVERY.md"
        )

    print()
    print("Rescue writes STOCK 1.1.2 / 39A10023 firmware (not the Helvetica untether).")
    print("That is the tested-hash revert path. Music on the data partition is wiped")
    print("unless --restore-data and the backup layout matches the live layout.")
    print("UNTESTED on a real nano from this repo.")
    print()

    with MaintenanceLock():
        return _rescue_locked(
            nano,
            backup=backup,
            backup_sha256=backup_sha256,
            backup_layout=backup_layout,
            serial=serial,
            dry_run=dry_run,
            accept_brick=accept_brick,
            accept_2015_refresh=accept_2015_refresh,
            restore_data=restore_data,
            stock=stock,
            stock_hash=stock_hash,
            wind3x=wind3x,
            typed_confirm=typed_confirm,
        )


def _rescue_locked(
    nano: Optional[Nano],
    *,
    backup: Path,
    backup_sha256: str,
    backup_layout: Optional[DiskLayout],
    serial: str,
    dry_run: bool,
    accept_brick: bool,
    accept_2015_refresh: bool,
    restore_data: bool,
    stock: Path,
    stock_hash: str,
    wind3x: Path,
    typed_confirm: Optional[str],
) -> dict:
    from .state import load_config, save_config

    if dry_run:
        print("==> DFU dry-run (identity only, no restore)")
        wait_dfu()
        log = prepare_restore(wind3x, serial, accept_2015=accept_2015_refresh)
        seen = parse_serial(log)
        if seen:
            cfg = load_config()
            cfg["DEVICE_SERIAL"] = seen
            save_config(cfg)
            print(f"    device serial {seen} (saved)")
        print("DRY_RUN_OK; would run restore --full with stock 39A10023 MSE,")
        print("            then format the data partition FAT32")
        if restore_data:
            print("            then copy the backup DATA slice (never firmware).")
        write_receipt(
            RESCUE_DRY_RUN,
            {"utc": utc_stamp(), "serial": seen or serial, "stock_mse": stock_hash},
        )
        return {"dry_run": True, "serial": seen or serial, "stock_mse": stock_hash}

    if not accept_brick:
        require_typed(
            "RESCUE",
            f"About to restore STOCK firmware on nano serial {serial}. "
            "This erases the current OSOS. The hashed backup is kept.",
            typed=typed_confirm,
        )

    print("==> RESCUE_START (restore --full stock MSE)")
    wait_dfu()
    prepare_restore(wind3x, serial, accept_2015=accept_2015_refresh)
    write_receipt(
        RESCUE_STARTED,
        {
            "utc": utc_stamp(),
            "serial": serial,
            "stock_mse": stock_hash,
            "backup_sha256": backup_sha256,
        },
    )
    restore_full(wind3x, stock)
    print("    restore --full finished; formatting data partition")
    print("    Do not unplug. White Do Not Disconnect is recovery diskmode.")
    print("    macOS will often show 05ac:1267 with no /dev/disk; that is expected.")

    data_restored = False
    formatted = False
    if restore_data:
        attached = wait_for_nano()
        unmount(attached)
        live = inspect_image(Path(attached.raw_device))
        print("==> layout after stock restore")
        print_layout(live, Path(attached.raw_device))
        if backup_layout is None:
            raise Refused("--restore-data needs a readable backup image to inspect")
        reason = layouts_compatible_for_data(backup_layout, live)
        if reason:
            raise Refused(
                reason + " Firmware was written. FAT32 format did not run. "
                "Retry ./start rescue --run --format-only or "
                "./start fat32 --run --format-only. Do not copy HFS onto FAT."
            )
        print("    restoring DATA slice from hashed backup (firmware slice untouched)")
        copy_data_slice(backup, Path(attached.raw_device), backup_layout, live)
        data_restored = True
    else:
        result = format_after_restore()
        formatted = True
        live = result.get("layout")

    write_receipt(
        RESCUE_OK,
        {
            "utc": utc_stamp(),
            "serial": serial,
            "stock_mse": stock_hash,
            "backup_sha256": backup_sha256,
            "data_restored": "yes" if data_restored else "no",
            "formatted": "yes" if formatted else "no",
        },
    )
    print("RESCUE_OK")
    print("Nano should boot stock 1.1.2. This path is UNTESTED on a real nano")
    print("from this repo. Rebuild music with ./start sync if the data slice")
    print("was formatted empty. Then ./start backup before any later flash.")
    return {
        "dry_run": False,
        "ok": True,
        "stock_mse": stock_hash,
        "data_restored": data_restored,
        "formatted": formatted,
    }


def _rescue_format_only(
    nano: Optional[Nano],
    *,
    serial: str,
    accept_brick: bool,
    typed_confirm: Optional[str],
    restore_data: bool,
    backup: Path,
    backup_layout: Optional[DiskLayout],
    backup_size: int = 0,
    dry_run: bool = True,
    accept_untested_flasher: bool = False,
    accept_2015_refresh: bool = False,
    backup_sha256: str = "",
) -> dict:
    from .device import find_nanos
    from .usbms import bot_session, format_data_slice_via_bot

    def _gate(live_layout: DiskLayout, live_size: int) -> None:
        if restore_data:
            return
        live_size = require_usable_disk_size(live_size, what="live nano")
        if not usable_disk_size(backup_size):
            return
        check_format_only_backup_vs_live(
            serial=serial,
            backup=backup,
            backup_layout=backup_layout,
            backup_size=backup_size,
            live_layout=live_layout,
            live_size=live_size,
        )

    def _via_bot(*, method: str) -> dict:
        with bot_session() as client:
            plan = format_data_slice_via_bot(bot=client, write=False)
            live = plan.get("layout")
            live_size = live.size_bytes if live is not None else 0
            require_usable_disk_size(live_size, what="READ CAPACITY")
            if live is not None:
                _gate(live, live_size)
            if dry_run:
                print("DRY_RUN_OK; would WRITE(10) a FAT32 prefix onto the data slice.")
                print("            restore --full would not run.")
                return {
                    "dry_run": True,
                    "format_only": True,
                    "method": method,
                    "byte_offset": plan.get("byte_offset"),
                    "byte_length": plan.get("byte_length"),
                }
            if not accept_brick:
                require_typed(
                    "RESCUE",
                    f"About to SCSI-format the data partition of serial {serial}. "
                    "The firmware slice is not rewritten.",
                    typed=typed_confirm,
                )
            _save_preformat_snapshot(serial=serial, nano=None, bot=client)
            result = format_data_slice_via_bot(bot=client, write=True, plan=plan)
            print(f"RESCUE_OK (format-only) {result.get('partition')}")
            return {"dry_run": False, "ok": True, "partition": result.get("partition"), "format_only": True}

    if restore_data:
        if nano is None:
            try:
                found = find_nanos()
            except Refused:
                found = []
            usable = [n for n in found if usable_nano(n) is not None]
            nano = usable[0] if usable else None
        if nano is None:
            raise Refused(
                "--restore-data --format-only needs a kernel disk node so the "
                "data slice can be copied. USB 05ac:1267 without IOMedia is "
                "the macOS handoff bug: run ./start fat32 --run --format-only "
                "first (SCSI FAT32), then restore files another way. "
                "Macpod HFS backup onto Winpod FAT is refused anyway."
            )
        unmount(nano)
        live = inspect_image(Path(nano.raw_device))
        print_layout(live, Path(nano.raw_device))
        if backup_layout is None:
            raise Refused("--restore-data --format-only needs a readable backup")
        if dry_run:
            reason = layouts_compatible_for_data(backup_layout, live)
            if reason:
                raise Refused(reason)
            print("DRY_RUN_OK; would copy the backup DATA slice (never firmware).")
            return {"dry_run": True, "format_only": True, "data_restored": True}
        if not accept_brick:
            require_typed(
                "RESCUE",
                f"About to restore the data partition of serial {serial}. "
                "The firmware slice is not rewritten.",
                typed=typed_confirm,
            )
        copy_data_slice(backup, Path(nano.raw_device), backup_layout, live)
        print("RESCUE_OK (format-only data restore)")
        return {"dry_run": False, "ok": True, "data_restored": True, "format_only": True}

    if nano is None:
        try:
            found = find_nanos()
        except Refused:
            found = []
        usable = [n for n in found if usable_nano(n) is not None]
        dropped = [n for n in found if usable_nano(n) is None]
        for n in dropped:
            print(
                f"    ignoring kernel node {n.raw_device} size {n.size} "
                "(0 is not a real nano; not in recovery diskmode yet)"
            )
        if len(usable) > 1:
            raise Refused("more than one iPod is attached; unplug extras")
        nano = usable[0] if usable else None
    elif usable_nano(nano) is None:
        print(
            f"    ignoring kernel node {nano.raw_device} size {nano.size} "
            "(0 is not a real nano; not in recovery diskmode yet)"
        )
        nano = None

    if diskmode_usb_present():
        print("    USB 05ac:1267; READ CAPACITY then FAT32 over BOT.")
        print("    Firmware/type-0x00 LBAs are not written. restore --full will not run.")
        return _via_bot(method="scsi")

    if nano is not None:
        unmount(nano)
        live = inspect_image(Path(nano.raw_device))
        print_layout(live, Path(nano.raw_device))
        target = pick_format_partition(live)
        live_size = nano.size if usable_disk_size(nano.size) else live.size_bytes
        _gate(live, live_size)
        if dry_run:
            print(
                f"    would format kernel node bytes {target.start_bytes}.."
                f"{target.start_bytes + target.size_bytes} "
                f"(sector {target.sector_size})"
            )
            print("DRY_RUN_OK; would format the data partition FAT32 (kernel node).")
            print("            restore --full would not run.")
            return {"dry_run": True, "format_only": True, "method": "kernel"}
        if not accept_brick:
            require_typed(
                "RESCUE",
                f"About to format the data partition of serial {serial}. "
                "The firmware slice is not rewritten.",
                typed=typed_confirm,
            )
        _save_preformat_snapshot(serial=serial, nano=nano)
        result = _format_kernel_node(nano)
        print(f"RESCUE_OK (format-only) {result.get('partition')}")
        return {"dry_run": False, "ok": True, "partition": result.get("partition"), "format_only": True}

    _enter_recovery_diskmode(
        serial=serial,
        accept_untested_flasher=accept_untested_flasher,
        accept_2015_refresh=accept_2015_refresh,
        what="--format-only",
    )
    return _via_bot(method="prepare-only")
