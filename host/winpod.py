"""Windows-free FAT32 conversion for a Mac-formatted nano 7G.

Live path: hashed backup, then wInd3x `restore --full --firmware <stock MSE>`.
That issues SCSI C6/0x94 IPodRepartition (the same vendor command iTunes uses)
so the *device* writes the partition table. This host then formats the data
slice FAT32. Music is erased; the raw backup is kept for recovery.

Rejected:
  (b) Finder / Music Restore cannot be forced to FAT32 on modern macOS.
  (c) A Windows VM is not acceptable.

Host-built Winpod images (ipodpatcher-style APM -> MBR, firmware start/size
copied from the backup) can be written to a *file* for inspection. Writing
that constructed table to a live nano 7G is refused: there is no Windows
iTunes n7g dump in this repo, and Rockbox's conversion tables omit nano 6/7G.
"""

from __future__ import annotations

import os
import stat
import tempfile
import time
from pathlib import Path
from typing import Optional

from .compat import Refused, have, is_macos, quoted_path, require_typed, run, sha256_file
from .device import Nano, find_nanos, usable_nano
from .fat import exists, mtools_env, need_mtools, unmount
from .layout import (
    DiskLayout,
    MKN52_IPODREPARTITION_DELTA,
    MAX_IPODREPARTITION_DELTA,
    inspect_image,
    inspect_bytes,
    partition_node,
    pick_format_partition,
)
from .safety import (
    backup_verify_size,
    check_backup,
    check_disk_size,
    check_usb_serial,
    ipodrepartition_delta_ok,
    require_usable_disk_size,
    usable_disk_size,
)
from .state import last_backup, read_receipt, receipt_exists, utc_stamp, write_receipt
from .usb import (
    diskmode_usb_present,
    late_wtf_present,
    prepare_restore,
    recovery_usb_state,
    select_wind3x,
    wait_dfu,
    wait_recovery_entry,
)


FAT32_STARTED = "FAT32_STARTED"
FAT32_OK = "FAT32_OK"
MIN_FAT_BYTES = 1024 * 1024


def inspect_path(path: Path) -> DiskLayout:
    return inspect_image(Path(path))


def print_layout(layout: DiskLayout, path: Path) -> None:
    print(f"disk image/device: {quoted_path(path)}")
    print(layout.summary())
    print()
    print("Windows iTunes (Winpod) writes MBR slot0 type 0x00 firmware + slot1")
    print("FAT32 (0x0b/0x0c). Finder Restore on nano 7G writes 4K APM + HFS")
    print("(firmware is in NAND, not Apple_MDFW). See docs/FAT32.md.")


def extract_stock_mse(ipsw: Optional[Path] = None) -> Path:
    """Firmware.MSE from the hashed official IPSW. Not the patched untether image."""
    from .firmware import build_dir, ensure_stock_ipsw
    from .safety import check_stock_mse

    stock_ipsw = ensure_stock_ipsw(Path(ipsw) if ipsw else None)
    dest = build_dir() / "firmware" / "Firmware-stock-39A10023.MSE"
    dest.parent.mkdir(parents=True, exist_ok=True)
    import zipfile

    with zipfile.ZipFile(stock_ipsw) as z:
        names = z.namelist()
        candidates = [
            n for n in names if n.rsplit("/", 1)[-1].lower() == "firmware.mse"
        ]
        if not candidates:
            raise Refused(
                f"{stock_ipsw.name} has no Firmware.MSE member. "
                f"First entries: {names[:12]}"
            )
        data = z.read(candidates[0])
    if dest.is_file() and dest.stat().st_size == len(data):
        from .compat import sha256_bytes

        if sha256_file(dest) == sha256_bytes(data):
            digest = check_stock_mse(dest)
            print(f"    stock Firmware.MSE already extracted ({digest[:16]}...)")
            return dest
    dest.write_bytes(data)
    digest = check_stock_mse(dest)
    (dest.with_suffix(dest.suffix + ".sha256")).write_text(f"{digest}  {dest.name}\n")
    print(f"    extracted stock Firmware.MSE ({len(data)} bytes, sha256 {digest})")
    return dest


def refuse_live_device_path(path: Path, what: str) -> None:
    path = Path(path)
    raw = str(path)
    if raw.startswith("/dev/") or path.is_block_device() or path.is_char_device():
        raise Refused(
            f"refusing to {what} {path}: host-built Winpod tables are not written "
            "to a live device. Live conversion is ./start fat32 (device-side "
            "IPodRepartition). See docs/FAT32.md."
        )
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    if stat.S_ISBLK(mode) or stat.S_ISCHR(mode):
        raise Refused(f"refusing to {what} special file {path}")


def build_winpod_file(src: Path, dest: Path) -> DiskLayout:
    """APM Macpod backup -> Winpod file. Never a live device.

    Firmware start/size come from the backup's Apple_MDFW entry (ipodpatcher
    style). The HFS data region is formatted empty FAT32. Music is not copied.
    """
    from .layout import encode_mbr

    src = Path(src)
    dest = Path(dest)
    refuse_live_device_path(src, "read a block device as if it were a backup file from")
    refuse_live_device_path(dest, "write a constructed Winpod table onto")
    if dest.exists():
        raise Refused(f"{dest} already exists; will not overwrite")
    layout = inspect_image(src)
    if layout.is_macpod_usb_data() and not layout.is_macpod():
        raise Refused(
            "source is a Finder-style nano 7G USB map (APM Apple_HFS, no "
            "Apple_MDFW). Firmware is not on this disk; refusing to invent "
            "nano 7G geometry. Live conversion is ./start fat32 (device-side "
            "IPodRepartition).\n" + layout.summary()
        )
    if not layout.is_macpod():
        raise Refused(
            "source is not a Macpod (APM Apple_MDFW + Apple_HFS). "
            "Refusing to invent nano 7G geometry.\n" + layout.summary()
        )
    fw = layout.firmware
    data = layout.data
    assert fw is not None and data is not None
    if fw.start_lba + fw.size_lba > data.start_lba:
        raise Refused("firmware and data slices overlap in the APM; refusing")
    src_size = src.stat().st_size
    ss = layout.sector_size
    if src_size % ss:
        raise Refused(
            f"source image size {src_size} is not a multiple of APM sector {ss}"
        )
    if src_size % 512:
        raise Refused(f"source image size {src_size} is not a multiple of 512")
    if ss % 512:
        raise Refused(
            f"APM sector {ss} is not a multiple of 512; refusing MBR conversion"
        )
    scale = ss // 512
    disk_lbas = src_size // 512
    import shutil

    shutil.copyfile(src, dest)
    with open(dest, "r+b") as f:
        f.write(
            encode_mbr(
                [
                    (0x00, fw.start_lba * scale, fw.size_lba * scale),
                    (0x0B, data.start_lba * scale, data.size_lba * scale),
                ],
                disk_lbas,
            )
        )
        f.flush()
        os.fsync(f.fileno())
    if data.size_bytes <= 64 * 1024 * 1024:
        format_fat32_region(dest, data.start_bytes, data.size_bytes)
    else:
        print(
            "    MBR written; data slice is larger than 64 MiB so FAT32 format "
            "is skipped here. Live conversion formats a partition node "
            "(./start fat32). This file is not written to a device."
        )
    result = inspect_image(dest)
    if not result.is_winpod():
        dest.unlink(missing_ok=True)
        raise Refused("built image is not a Winpod after MBR write:\n" + result.summary())
    orig = _read_slice(src, fw.start_bytes, fw.size_bytes)
    new = _read_slice(dest, fw.start_bytes, fw.size_bytes)
    if orig != new:
        dest.unlink(missing_ok=True)
        raise Refused("firmware region changed while building the Winpod file")
    if data.size_bytes <= 64 * 1024 * 1024 and not _fat_readable(dest, data.start_bytes):
        dest.unlink(missing_ok=True)
        raise Refused("built Winpod FAT32 data slice is not readable with mtools")
    return result


def _read_slice(path: Path, offset: int, size: int) -> bytes:
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read(size)
    if len(data) != size:
        raise Refused(f"short read at {path} offset {offset}: {len(data)} != {size}")
    return data


def _fat_readable(image: Path, offset: int = 0) -> bool:
    need_mtools()
    target = str(image) if offset == 0 else f"{image}@@{offset}"
    r = run(
        ["mdir", "-i", target, "::/"],
        check=False,
        capture=True,
        env=mtools_env(),
    )
    return r.returncode == 0


def format_fat32_region(image: Path, offset: int, size: int, *, label: str = "IPOD") -> None:
    """Format a slice of a regular file. Refuses huge in-RAM copies."""
    if size < MIN_FAT_BYTES:
        raise Refused(f"data slice {size} bytes is too small for FAT32")
    if size > 64 * 1024 * 1024:
        raise Refused(
            "refusing to materialize a FAT filesystem larger than 64 MiB in a "
            "temp file. Live conversion formats a partition node instead."
        )
    refuse_live_device_path(image, "format")
    tmp = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix="nano7-fat32-")
        os.close(fd)
        tmp = Path(tmp_name)
        tmp.write_bytes(b"\x00" * size)
        _mkfs_fat(str(tmp), label)
        blob = tmp.read_bytes()
        if len(blob) != size:
            raise Refused(f"mkfs wrote {len(blob)} bytes, expected {size}")
        with open(image, "r+b") as f:
            f.seek(offset)
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def format_fat32_node(
    node: str,
    *,
    label: str = "IPOD",
    bytes_per_sector: int = 0,
    sectors_per_cluster: int = 0,
) -> None:
    """Format a partition device. Does not rewrite the partition table."""
    if node.startswith("/dev/"):
        _mkfs_fat(
            node,
            label,
            bytes_per_sector=bytes_per_sector,
            sectors_per_cluster=sectors_per_cluster,
        )
        return
    path = Path(node)
    if not path.is_file():
        raise Refused(f"cannot format {node}: not a file or device")
    _mkfs_fat(
        str(path),
        label,
        bytes_per_sector=bytes_per_sector,
        sectors_per_cluster=sectors_per_cluster,
    )


def _mkfs_fat(
    node: str,
    label: str,
    *,
    bytes_per_sector: int = 0,
    sectors_per_cluster: int = 0,
) -> None:
    label = (label or "IPOD")[:11]
    extra_newfs = []
    extra_mkfs = []
    if bytes_per_sector:
        extra_newfs.extend(["-S", str(int(bytes_per_sector))])
        extra_mkfs.extend(["-S", str(int(bytes_per_sector))])
    if sectors_per_cluster:
        extra_newfs.extend(["-c", str(int(sectors_per_cluster))])
        extra_mkfs.extend(["-s", str(int(sectors_per_cluster))])
    if is_macos() and have("newfs_msdos"):
        r = run(
            ["newfs_msdos", "-F", "32", "-v", label, *extra_newfs, node],
            check=False,
            capture=True,
        )
        if r.returncode == 0:
            return
        err = (r.stderr or r.stdout or "").strip()
        raise Refused(f"newfs_msdos failed on {node}: {err}")
    mkfs = None
    for cand in ("mkfs.vfat", "mkfs.msdos"):
        if have(cand):
            mkfs = cand
            break
    if mkfs:
        r = run(
            [mkfs, "-F", "32", "-n", label, *extra_mkfs, node],
            check=False,
            capture=True,
        )
        if r.returncode == 0:
            return
        err = (r.stderr or r.stdout or "").strip()
        raise Refused(f"{mkfs} failed on {node}: {err}")
    if have("mformat"):
        r = run(
            ["mformat", "-F", "-v", label, "-i", node, "::"],
            check=False,
            capture=True,
            env=mtools_env(),
        )
        if r.returncode == 0:
            return
        err = (r.stderr or r.stdout or "").strip()
        raise Refused(f"mformat failed on {node}: {err}")
    raise Refused(
        "no FAT32 formatter (macOS: newfs_msdos; Linux: mkfs.vfat from dosfstools; "
        "or mformat from mtools)"
    )


KERNEL_WAIT_S = 15


def wait_for_nano_optional(timeout: int = KERNEL_WAIT_S) -> Optional[Nano]:
    """Poll for a kernel disk node. Returns None if none appeared (do not raise)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            found = find_nanos()
        except Refused:
            found = []
        usable = [n for n in found if usable_nano(n) is not None]
        if len(usable) == 1:
            return usable[0]
        if len(usable) > 1:
            raise Refused("more than one iPod appeared after restore; unplug extras")
        time.sleep(1)
    return None


def wait_for_nano(timeout: int = 90) -> Nano:
    nano = wait_for_nano_optional(timeout)
    if nano is not None:
        return nano
    extra = ""
    if diskmode_usb_present():
        extra = (
            " USB 05ac:1267 is present but the kernel has no IOMedia "
            "(macOS post-restore handoff). Use SCSI BOT format; do not unplug. "
        )
    raise Refused(
        f"nano did not reappear as mass storage within {timeout}s after "
        f"restore --full (none).{extra}Keep the backup. "
        "If the screen is a stuck Apple logo, put the nano in DFU "
        "(Sleep/Wake+Home, USB-A hub) and run ./start fat32 --run --format-only. "
        "Do not run restore --full again. See docs/RECOVERY.md."
    )


def wait_for_format_target(timeout: int = KERNEL_WAIT_S) -> tuple:
    """Return ('kernel', Nano) or ('bot', None). Raise if neither."""
    deadline = time.time() + timeout
    saw_1267 = False
    last_err = "none"
    while time.time() < deadline:
        try:
            found = find_nanos()
        except Refused as e:
            last_err = str(e)
            found = []
        usable = [n for n in found if usable_nano(n) is not None]
        if len(usable) == 1:
            return "kernel", usable[0]
        if len(usable) > 1:
            raise Refused("more than one iPod appeared after restore; unplug extras")
        if diskmode_usb_present():
            saw_1267 = True
            time.sleep(2)
            try:
                found = find_nanos()
            except Refused as e:
                last_err = str(e)
                found = []
            usable = [n for n in found if usable_nano(n) is not None]
            if len(usable) == 1:
                return "kernel", usable[0]
            if len(usable) > 1:
                raise Refused("more than one iPod appeared after restore; unplug extras")
            return "bot", None
        time.sleep(1)
    if saw_1267 or diskmode_usb_present():
        return "bot", None
    raise Refused(
        f"nano did not reappear as mass storage or 05ac:1267 within {timeout}s "
        f"({last_err}). White Do Not Disconnect means recovery diskmode: keep "
        "the cable in and retry ./start fat32 --run --format-only. A stuck "
        "Apple logo means RetailOS has no FAT32: DFU (docs/BUTTONS.md), "
        "then the same --format-only. Do not unplug during a write. Do not "
        "run restore --full again unless prepare-only cannot reach diskmode. "
        "docs/RECOVERY.md"
    )


def _format_kernel_node(nano: Nano, *, verify: bool = True) -> dict:
    unmount(nano)
    live = inspect_image(Path(nano.raw_device))
    print("==> layout (kernel disk)")
    print_layout(live, Path(nano.raw_device))
    target = pick_format_partition(live)
    node = partition_node(nano.raw_device, target.index)
    alt = node.replace("/rdisk", "/disk")
    if not Path(node).exists() and Path(alt).exists():
        node = alt
    if not Path(node).exists():
        raise Refused(
            f"data partition node {node} does not exist. Layout:\n{live.summary()}\n"
            "Firmware was written; FAT32 format did not run. Retry "
            "./start fat32 --run --format-only. Do not guess a device node."
        )
    print(f"    formatting {node} FAT32 (partition table not rewritten)")
    print(
        f"    byte_offset={target.start_bytes} byte_length={target.size_bytes} "
        f"sector={target.sector_size}"
    )
    spc = 0
    if target.sector_size:
        from .fat32fs import choose_spc

        spc = choose_spc(target.size_bytes, bytes_per_sector=target.sector_size)
    format_fat32_node(
        node,
        bytes_per_sector=target.sector_size,
        sectors_per_cluster=spc,
    )
    if verify and not exists(node, "/") and not _fat_readable(Path(node)):
        raise Refused(
            f"FAT32 format of {node} left a volume mtools cannot read. "
            "Keep the backup. docs/RECOVERY.md"
        )
    return {"method": "kernel", "partition": node, "layout": live, "wrote": True}


def format_after_restore(*, try_reset: bool = False) -> dict:
    """After wInd3x left recovery diskmode (IPodFinalize(false)).

    Linux often rebinds mass-storage. macOS does not (MKN52ZP 2026-10-28:
    exclusive-access leftover, 05ac:1267 class 8/6, no IOMedia). Prefer
    SCSI WRITE(10) of a FAT32 prefix onto the data slice. USB reset is
    last resort: on this unit, unplug dropped diskmode to a stuck Apple logo.
    """
    kind, nano = wait_for_format_target()
    if kind == "kernel" and nano is not None:
        return _format_kernel_node(nano)
    print("    05ac:1267 present without a kernel disk node")
    print("    (macOS mass-storage did not reattach after wInd3x/libusb).")
    print("    Formatting the data slice via SCSI WRITE(10); firmware slice untouched.")
    from .usbms import format_data_slice_via_bot

    try:
        return format_data_slice_via_bot(write=True)
    except Refused as e:
        msg = str(e)
        claim = "claim interface" in msg or "capacity failed" in msg
        if not claim or not try_reset:
            raise Refused(
                msg
                + " USB reset is not the default: on MKN52ZP an unplug from "
                "recovery diskmode rebooted to a stuck Apple logo. Keep the "
                "cable in. Retry ./start fat32 --run --format-only. "
                "docs/RECOVERY.md"
            )
        print("    BOT could not claim the interface.")
        print("    USB reset is last resort and may drop recovery diskmode")
        print("    (unplug on MKN52ZP rebooted to a stuck Apple logo).")
        from .usbms import bot_reset

        bot_reset()
        time.sleep(3)
        kind, nano = wait_for_format_target(timeout=8)
        if kind == "kernel" and nano is not None:
            return _format_kernel_node(nano)
        if diskmode_usb_present():
            return format_data_slice_via_bot(write=True)
        raise Refused(
            "USB reset did not restore a kernel disk or 05ac:1267. "
            "If the screen is a stuck Apple logo, DFU then "
            "./start fat32 --run --format-only. Keep the backup. docs/RECOVERY.md"
        )


def restore_full(wind3x: Path, mse: Path, *, bootloader: Optional[Path] = None) -> str:
    from .usb import run_wind3x

    if bootloader is None:
        from .bootloader import extract_stock_bootloader

        bootloader = extract_stock_bootloader()
    cmd = [
        "restore",
        "--full",
        "--firmware",
        str(mse),
        "--bootloader",
        str(bootloader),
    ]
    r = run_wind3x(cmd, wind3x)
    log = (r.stdout or "") + (r.stderr or "")
    from .state import state_dir

    (state_dir() / f"fat32-full-{utc_stamp()}.log").write_text(log)
    if r.returncode != 0:
        raise Refused(
            "wInd3x restore --full failed. The nano may be in recovery. "
            "Do not invent a retry. Keep the backup. docs/RECOVERY.md\n" + log[-4000:]
        )
    if "37A40005" in log and "Downloading IPSW" in log:
        raise Refused(
            "restore --full downloaded 2012 bootloader IPSW 37A40005. "
            "Nano7Late must use local 39A10023 N31.bootloader.rb3. "
            "Rebuild wInd3x with ./start deps. Keep the backup. docs/RECOVERY.md\n"
            + log[-4000:]
        )
    if "Writing firmware..." not in log:
        raise Refused("restore --full log does not show a firmware write:\n" + log[-4000:])
    if "Reformatting system partition" not in log and "repartition" not in log.lower():
        raise Refused(
            "restore --full log does not show IPodRepartition / system partition "
            "reformat. Refusing to format a data slice we cannot prove exists.\n"
            + log[-4000:]
        )
    return log


def check_format_only_backup_vs_live(
    *,
    serial: str,
    backup: Path,
    backup_layout: Optional[DiskLayout],
    backup_size: int,
    live_layout: DiskLayout,
    live_size: int,
) -> dict:
    """Fail-closed --format-only exception for a pre-repartition Finder backup.

    After IPodRepartition the USB map is a few MiB larger than the hashed
    Finder APM/HFS image. A new backup of the empty FAT volume is not useful.
    Accept that backup only when every identity check holds. Sizes that already
    match take the existing path.
    """
    live_size = require_usable_disk_size(live_size, what="live nano")
    backup_size = require_usable_disk_size(backup_size, what="backup")
    if live_size == backup_size:
        return {"match": True, "delta": 0, "accepted": False}

    started = read_receipt(FAT32_STARTED)
    started_serial = (started.get("serial") or "").strip()
    ok_receipt = bool(started) and started_serial == serial
    if backup_layout is None and Path(backup).is_file() and Path(backup).stat().st_size >= 512:
        backup_layout = inspect_image(Path(backup))
    ok_backup = (
        backup_layout is not None
        and backup_layout.is_macpod_usb_data()
        and backup_layout.firmware is None
    )
    ok_live = live_layout is not None and live_layout.is_ipodrepartition_4k_fat32()
    delta = live_size - backup_size
    ok_delta = ipodrepartition_delta_ok(live_size, backup_size)

    if ok_receipt and ok_backup and ok_live and ok_delta:
        known = ""
        if delta == MKN52_IPODREPARTITION_DELTA:
            known = " (MKN52ZP IPodRepartition growth)"
        print("FORMAT_ONLY: IPodRepartition grew the USB map.")
        print(f"  hashed backup  {backup_size} bytes (Finder 4K APM/HFS, SHA-256 verified)")
        print(
            f"  live nano      {live_size} bytes "
            "(MBR type 0x0c, hidden 64, 4K fill, firmware in NAND)"
        )
        print(
            f"  delta          {delta} bytes "
            f"({delta // 4096} * 4096), within {MAX_IPODREPARTITION_DELTA} bytes{known}"
        )
        print(f"  FAT32_STARTED  serial {serial} matches")
        print("  A new backup of this empty FAT volume would not restore the Finder image.")
        print("  Using the pre-repartition backup. Firmware is not rewritten. Do not pass --replace.")
        return {
            "match": False,
            "accepted": True,
            "delta": delta,
            "reason": "pre-repartition APM/HFS backup vs IPodRepartition 4K FAT32 data volume",
        }

    why = []
    if not ok_receipt:
        why.append("FAT32_STARTED receipt missing or serial mismatch")
    if not ok_backup:
        why.append("backup is not APM/HFS (pre-repartition Macpod data, no Apple_MDFW)")
    if not ok_live:
        why.append(
            "live disk is not an IPodRepartition 4K FAT32 data volume "
            "(MBR 0x0c, hidden 64, 4K fill)"
        )
    if not ok_delta:
        why.append(
            f"size delta {delta} is not a 4096-aligned IPodRepartition growth "
            f"(need 0 < delta <= {MAX_IPODREPARTITION_DELTA})"
        )
    raise Refused(
        f"backup size {backup_size} does not match the nano size {live_size}. "
        f"Take a new backup of THIS nano and use that. "
        f"--format-only can reuse a hashed pre-repartition APM/HFS backup only when "
        f"the live device is an IPodRepartition data volume (MBR 0x0c, hidden 64, "
        f"4K fill), FAT32_STARTED matches this serial, and the size delta is the "
        f"repartition growth. "
        + "; ".join(why)
        + ". Do not pass --replace. Do not rewrite firmware. docs/RECOVERY.md"
    )


def _save_preformat_snapshot(
    *, serial: str, nano: Optional[Nano] = None, bot=None
) -> Optional[Path]:
    """Best-effort MBR + data-prefix dump before format WRITE. Never WRITE itself.

    Snapshot failures do not silently continue if BOT reads are broken: a test
    READ(10) of LBA 0 must succeed, otherwise format is refused.
    """
    from .safety import backup_filename_token
    from .state import state_dir
    from .usbms import dump_data_slice_via_bot, probe_read_lba0

    dest = state_dir() / f"pre-format-{utc_stamp()}-{backup_filename_token(serial)}"
    try:
        dest.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        dest = state_dir() / (
            f"pre-format-{utc_stamp()}-{backup_filename_token(serial)}-{os.getpid()}"
        )
        dest.mkdir(parents=True, exist_ok=True)
    try:
        if bot is not None:
            dump_data_slice_via_bot(dest, bot=bot)
            print(f"    pre-format snapshot: {dest}")
            return dest
        if diskmode_usb_present():
            dump_data_slice_via_bot(dest)
            print(f"    pre-format snapshot: {dest}")
            return dest
        if nano is not None:
            _dump_kernel_preformat(nano, dest)
            print(f"    pre-format snapshot: {dest}")
            return dest
        print("    pre-format snapshot skipped (no 05ac:1267 and no kernel node)")
        return None
    except (Refused, OSError) as e:
        if bot is not None:
            try:
                probe_read_lba0(bot)
            except Refused as e2:
                raise Refused(
                    f"pre-format snapshot failed ({e}); "
                    f"test READ(10) of LBA 0 also failed ({e2}). "
                    "BOT reads are broken; not writing. Do not pass --replace. "
                    "docs/RECOVERY.md"
                ) from e2
            print(
                f"    pre-format snapshot failed ({e}); "
                "READ(10) LBA 0 succeeded, continuing with format. "
                "Keep the hashed backup."
            )
            return None
        print(
            f"    pre-format snapshot failed ({e}); continuing with format. "
            "Keep the hashed backup."
        )
        return None


def _dump_kernel_preformat(nano: Nano, dest: Path) -> None:
    from .usbms import DUMP_DATA_BYTES

    dest.mkdir(parents=True, exist_ok=True)
    raw = Path(nano.raw_device)
    size = require_usable_disk_size(nano.size, what="kernel snapshot")
    with open(raw, "rb") as f:
        mbr = f.read(4096)
        (dest / "dump-mbr.bin").write_bytes(mbr)
        layout = inspect_bytes(mbr, size)
        off = 0
        if layout.sector_resolved and layout.data is not None:
            off = layout.data.start_bytes
        f.seek(off)
        want = DUMP_DATA_BYTES
        if size and size > off:
            want = min(want, size - off)
        prefix = f.read(max(0, want))
        (dest / "dump-data-prefix.bin").write_bytes(prefix)
    (dest / "dump.txt").write_text(
        "nano7kit pre-format kernel snapshot (read-only; no WRITE)\n"
        f"raw={raw}\nsize={size}\n\n{layout.summary()}\n"
    )


def convert_nano(
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
    replace: bool = False,
    mse: Optional[Path] = None,
    format_only: bool = False,
    typed_confirm: Optional[str] = None,
    scsi_dump: Optional[Path] = None,
) -> dict:
    """HFS+ (or other) -> FAT32 via device-side repartition + host FAT format."""
    serial = (serial or "").strip()
    if not serial:
        raise Refused(
            "device serial is required (Settings > General > About, or the back). "
            "Pass --serial."
        )
    rec = last_backup(nano.usb_serial if nano else None)
    if format_only or scsi_dump is not None:
        dropped = nano is not None and usable_nano(nano) is None
        if dropped:
            print(
                f"    ignoring kernel node {nano.raw_device} size {nano.size} "
                "(0 is not a real nano; not in recovery diskmode yet)"
            )
        nano = usable_nano(nano)
    if nano:
        check_usb_serial(nano.usb_serial)
    elif rec and rec.get("usb_serial"):
        check_usb_serial(rec.get("usb_serial"))
    receipt_size = rec.get("size") if rec else None
    size = backup_verify_size(
        Path(backup),
        nano_size=nano.size if nano else None,
        receipt_size=receipt_size,
        format_only=format_only,
        scsi_dump=scsi_dump is not None,
    )
    size_what = "backup" if (format_only or scsi_dump is not None) else "backup/nano"
    check_disk_size(
        size, allow_size=allow_size, confirm_untested=confirm_untested, what=size_what
    )
    check_backup(backup, backup_sha256, size, skip_file=skip_backup_file)

    backup_layout = None
    if backup.is_file() and backup.stat().st_size >= 512:
        backup_layout = inspect_image(backup)
        print("==> backup layout")
        print_layout(backup_layout, backup)
        if backup_layout.is_macpod_usb_data() and backup_layout.firmware is None:
            print("    USB map has no firmware partition. Expected for a")
            print("    Finder-restored nano 7G: the MSE is in NAND, not this disk.")
            print("    Dry-run still uses DFU prepare; host-built --build-image")
            print("    is refused (nothing to copy).")
        if backup_layout.is_winpod() and not replace and not format_only and not scsi_dump:
            raise Refused(
                "backup is already a Winpod (MBR firmware + FAT32). "
                "Nothing to convert. If Finder later rewrote HFS+, take a new "
                "backup and pass --replace."
            )

    if scsi_dump is not None:
        if format_only:
            raise Refused(
                "--scsi-dump is read-only and cannot be combined with --format-only"
            )
        return _scsi_dump(
            nano,
            dest=Path(scsi_dump),
            serial=serial,
            accept_untested_flasher=accept_untested_flasher,
            accept_2015_refresh=accept_2015_refresh,
        )

    if format_only:
        backup_size = size
        if Path(backup).is_file():
            from .compat import file_size as _file_size

            backup_size = _file_size(Path(backup))
        return _format_only(
            nano,
            serial=serial,
            accept_brick=accept_brick,
            typed_confirm=typed_confirm,
            dry_run=dry_run,
            accept_untested_flasher=accept_untested_flasher,
            accept_2015_refresh=accept_2015_refresh,
            backup_sha256=backup_sha256,
            backup=Path(backup),
            backup_layout=backup_layout,
            backup_size=backup_size,
        )

    stock = Path(mse) if mse else extract_stock_mse()
    from .safety import check_stock_mse

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

    from .usb import late_wtf_present as _late_wtf_present

    if accept_2015_refresh:
        print(
            "HARDWARE: Mid-2015 nano 7G (MKN52 / A1446 Rev A). "
            "Patched wInd3x uses 0x124a / 39A10023 recovery. "
            "Kind switch + restore --full observed on MKN52ZP 2026-10-28; "
            "FAT/kernel handoff failed that day. Later bootloader then "
            "Helvetica HOME SCREEN (docs/HARDWARE-2015.md)."
        )
    elif _late_wtf_present():
        raise Refused(
            "USB already shows WTF 0x124a (Mid-2015). Pass --i-accept-2015-refresh. "
            "See docs/HARDWARE-2015.md."
        )

    if receipt_exists(FAT32_OK) and not replace:
        raise Refused(
            "a FAT32 conversion already completed on this computer "
            f"({FAT32_OK}). Pass --replace only if the nano is HFS+ again "
            "and you have a current hashed backup."
        )
    if receipt_exists(FAT32_STARTED) and not replace:
        raise Refused(
            "a FAT32 conversion was started and may have written the device. "
            "If the nano still boots, take a new backup and pass --replace. "
            "If it does not, stop and read docs/RECOVERY.md. Do not retry blindly."
        )

    print()
    print("This step wipes the data partition (music, photos, everything).")
    print("The hashed raw backup is the recovery image, not a music archive.")
    print("Finder Restore is HFS+ and is not used. No Windows VM is used.")
    print()

    from .state import MaintenanceLock

    with MaintenanceLock():
        return _convert_locked(
            nano,
            backup=backup,
            backup_sha256=backup_sha256,
            serial=serial,
            dry_run=dry_run,
            accept_brick=accept_brick,
            accept_2015_refresh=accept_2015_refresh,
            stock=stock,
            stock_hash=stock_hash,
            wind3x=wind3x,
            replace=replace,
            typed_confirm=typed_confirm,
        )


def _convert_locked(
    nano: Optional[Nano],
    *,
    backup: Path,
    backup_sha256: str,
    serial: str,
    dry_run: bool,
    accept_brick: bool,
    accept_2015_refresh: bool,
    stock: Path,
    stock_hash: str,
    wind3x: Path,
    replace: bool,
    typed_confirm: Optional[str] = None,
) -> dict:
    if dry_run:
        print("==> DFU dry-run (identity only, no repartition)")
        wait_dfu()
        log = prepare_restore(wind3x, serial, accept_2015=accept_2015_refresh)
        from .usb import parse_serial
        from .state import load_config, save_config

        seen = parse_serial(log)
        if seen:
            cfg = load_config()
            cfg["DEVICE_SERIAL"] = seen
            save_config(cfg)
            print(f"    device serial {seen} (saved)")
        print("DRY_RUN_OK; would run restore --full with stock 39A10023 MSE,")
        print("            then format the data partition FAT32.")
        write_receipt(
            "FAT32_DRY_RUN_OK",
            {"utc": utc_stamp(), "serial": seen or serial, "stock_mse": stock_hash},
        )
        return {"dry_run": True, "serial": seen or serial, "stock_mse": stock_hash}

    if not accept_brick:
        require_typed(
            "FAT32",
            f"About to IPodRepartition nano serial {serial} and format FAT32. "
            "All music on the device will be erased. The backup file is kept.",
            typed=typed_confirm,
        )

    print("==> FAT32_START (restore --full, then format data partition)")
    wait_dfu()
    prepare_restore(wind3x, serial, accept_2015=accept_2015_refresh)
    write_receipt(
        FAT32_STARTED,
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
    result = format_after_restore()
    node = result.get("partition") or result.get("method")
    write_receipt(
        FAT32_OK,
        {
            "utc": utc_stamp(),
            "serial": serial,
            "stock_mse": stock_hash,
            "backup_sha256": backup_sha256,
            "partition": str(node),
            "method": str(result.get("method") or ""),
        },
    )
    print("FAT32_OK")
    print("Nano should boot stock 1.1.2 with a FAT32 data partition.")
    print("Rebuild music with ./start sync (Disk Mode, then Music/Finder).")
    print("Then ./start backup (new FAT32 image) and ./start untether.")
    return {
        "dry_run": False,
        "ok": True,
        "partition": node,
        "stock_mse": stock_hash,
        "method": result.get("method"),
    }


def _enter_recovery_diskmode(
    *,
    serial: str,
    accept_untested_flasher: bool,
    accept_2015_refresh: bool,
    what: str = "--format-only",
) -> None:
    """DFU/WTF -> restore --prepare-only. No firmware write. No size or MBR read."""
    print("    no usable kernel disk and no 05ac:1267; entering recovery diskmode")
    print("    via wInd3x restore --prepare-only (no firmware write).")
    state = recovery_usb_state()
    print(f"    USB state now: {state}")
    wind3x, tested_bin, note = select_wind3x(need_2015_kind_switch=accept_2015_refresh)
    print(f"flasher: {wind3x}")
    print(f"         {note}")
    if not tested_bin:
        if not accept_untested_flasher:
            raise Refused(
                "this computer has no hardware-tested wInd3x binary (that file is "
                "Linux x86-64). Pass --i-accept-untested-flasher to continue. "
                f"{what} still does not write firmware."
            )
        print("HARDWARE: this flasher binary is untested.")
    if accept_2015_refresh:
        print(
            "HARDWARE: Mid-2015 nano 7G (MKN52 / A1446 Rev A). "
            "Patched wInd3x uses 0x124a / 39A10023 recovery. docs/HARDWARE-2015.md"
        )
    elif late_wtf_present():
        raise Refused(
            "USB already shows WTF 0x124a (Mid-2015). Pass --i-accept-2015-refresh. "
            "See docs/HARDWARE-2015.md."
        )
    wait_recovery_entry()
    prepare_restore(wind3x, serial, accept_2015=accept_2015_refresh)
    print("    prepare-only finished; recovery diskmode should be 05ac:1267")
    print("    Do not unplug. White Do Not Disconnect is the window.")
    if not diskmode_usb_present():
        raise Refused(
            "device not in recovery diskmode. prepare-only finished but "
            "05ac:1267 is not present. Cannot READ CAPACITY or inspect the MBR. "
            "Keep the cable in, Cancel Finder, retry DFU (docs/BUTTONS.md). "
            "Do not pass --replace. Do not rewrite firmware. docs/RECOVERY.md"
        )


def _format_only(
    nano: Optional[Nano],
    *,
    serial: str,
    accept_brick: bool,
    typed_confirm: Optional[str] = None,
    dry_run: bool = True,
    accept_untested_flasher: bool = False,
    accept_2015_refresh: bool = False,
    backup_sha256: str = "",
    backup: Optional[Path] = None,
    backup_layout: Optional[DiskLayout] = None,
    backup_size: int = 0,
) -> dict:
    """Format the data slice. Never restore --full.

    Recovery diskmode 05ac:1267 is the live target: READ CAPACITY over BOT,
    then the backup-delta gate, then WRITE. A leftover kernel node with size 0
    (unplug after scsi-dump) is ignored. If USB is not 1267, this enters
    recovery via prepare-only (no firmware write) *before* any live size or
    MBR read.
    """
    from .state import MaintenanceLock
    from .usbms import bot_session, format_data_slice_via_bot

    backup = Path(backup) if backup is not None else Path(".")

    def _gate(live_layout: DiskLayout, live_size: int) -> None:
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
            live_size = 0
            if live is not None:
                live_size = live.size_bytes
            require_usable_disk_size(live_size, what="READ CAPACITY")
            if live is not None:
                _gate(live, live_size)
            if dry_run:
                print("DRY_RUN_OK; would WRITE(10) a FAT32 prefix onto the data slice.")
                print("            Firmware/type-0x00 LBAs would not be written.")
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
                    "FAT32",
                    f"About to SCSI-format the data partition of serial {serial} as FAT32. "
                    "The firmware slice is not rewritten. Music on that slice is erased.",
                    typed=typed_confirm,
                )
            with MaintenanceLock():
                _save_preformat_snapshot(serial=serial, nano=None, bot=client)
                result = format_data_slice_via_bot(bot=client, write=True, plan=plan)
            return _finish_format_only(result, serial=serial, backup_sha256=backup_sha256)

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
        if len(usable) == 1:
            nano = usable[0]
        else:
            nano = None
    else:
        if usable_nano(nano) is None:
            print(
                f"    ignoring kernel node {nano.raw_device} size {nano.size} "
                "(0 is not a real nano; not in recovery diskmode yet)"
            )
            nano = None

    # 05ac:1267 is recovery diskmode. Inspect it over BOT, never a size-0 leftover.
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
                "FAT32",
                f"About to format the data partition of serial {serial} as FAT32. "
                "The partition table is not rewritten. Music on that slice is erased.",
                typed=typed_confirm,
            )
        with MaintenanceLock():
            _save_preformat_snapshot(serial=serial, nano=nano)
            result = _format_kernel_node(nano)
        return _finish_format_only(result, serial=serial, backup_sha256=backup_sha256)

    _enter_recovery_diskmode(
        serial=serial,
        accept_untested_flasher=accept_untested_flasher,
        accept_2015_refresh=accept_2015_refresh,
        what="--format-only",
    )
    return _via_bot(method="prepare-only")


def _scsi_dump(
    nano: Optional[Nano],
    *,
    dest: Path,
    serial: str,
    accept_untested_flasher: bool = False,
    accept_2015_refresh: bool = False,
) -> dict:
    """Read-only MBR + data-slice dump over BOT. Never WRITE(10), never --full."""
    from .usbms import dump_data_slice_via_bot

    dest = Path(dest).expanduser()
    if dest.exists() and dest.is_file():
        raise Refused(f"--scsi-dump {dest} is a file; pass a directory")

    if nano is None:
        try:
            found = find_nanos()
        except Refused:
            found = []
        usable = [n for n in found if usable_nano(n) is not None]
        for n in found:
            if usable_nano(n) is None:
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
        print("    USB 05ac:1267: read-only BOT dump of MBR + data-slice prefix")
        print("    No WRITE(10). No restore --full. Firmware LBAs are not touched.")
        return dump_data_slice_via_bot(dest)

    if nano is not None:
        unmount(nano)
        print("    kernel disk present; BOT 05ac:1267 is preferred for this dump.")
        print("    05ac:1267 is not listed. Refusing to dump a kernel node as SCSI.")
        print("    Put the nano in recovery diskmode (DFU then prepare-only) or")
        print("    keep it on the white Do Not Disconnect screen.")
        raise Refused(
            "scsi-dump needs recovery diskmode 05ac:1267 (BOT). A kernel /dev "
            "node is not this diagnostic. DFU (docs/BUTTONS.md), then retry. "
            "docs/RECOVERY.md"
        )

    _enter_recovery_diskmode(
        serial=serial,
        accept_untested_flasher=accept_untested_flasher,
        accept_2015_refresh=accept_2015_refresh,
        what="--scsi-dump",
    )
    return dump_data_slice_via_bot(dest)


def _finish_format_only(result: dict, *, serial: str, backup_sha256: str) -> dict:
    node = result.get("partition") or result.get("method")
    write_receipt(
        FAT32_OK,
        {
            "utc": utc_stamp(),
            "serial": serial,
            "backup_sha256": backup_sha256,
            "partition": str(node),
            "method": str(result.get("method") or ""),
            "format_only": "yes",
        },
    )
    print(f"FAT32_OK (format-only) {node}")
    out = dict(result)
    out.update({"dry_run": False, "ok": True, "format_only": True})
    return out
