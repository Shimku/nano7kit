"""Refuse-on-doubt checks. Strengthened, never weakened."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from . import const
from .compat import Refused, die_if_symlink, file_size, sha256_file
from .layout import MAX_IPODREPARTITION_DELTA

# Optional DiskLayout; imported lazily in check_disk_size to avoid cycles.

# Leftover diskutil nodes after unplug report TotalSize 0. That is never a nano.
# Partition-table parsers still need a full sector (512); that is separate.


def usable_disk_size(size: object) -> bool:
    """True when size is a positive capacity. 0 is never a nano."""
    try:
        n = int(size)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return n > 0


def require_usable_disk_size(size: object, *, what: str = "disk") -> int:
    """Return a positive int capacity or refuse. Never treat 0 as a real nano."""
    if not usable_disk_size(size):
        raise Refused(
            f"{what} size {size} is not a usable disk (a real nano is never 0 bytes)"
        )
    return int(size)  # type: ignore[arg-type]


def describe_size(size: int) -> str:
    info = const.KNOWN_DISK_SIZES.get(size)
    if info:
        tag = "hardware-tested" if info.get("hardware_tested") else "listed, not hardware-tested"
        return f"{size} bytes ({info['label']}; {tag})"
    return f"{size} bytes (unknown; not on the allow-list)"


def _layout_is_ipodrepartition_4k(layout) -> bool:
    fn = getattr(layout, "is_ipodrepartition_4k_fat32", None)
    if callable(fn):
        return bool(fn())
    return False


def layout_for_disk_size(path, size):
    """Inspect a disk only when the live size is the IPodRepartition USB map.

    Always pass the discovered capacity: Darwin /dev/rdiskN reports st_size 0.
    Size 0 leftover nodes never reach inspect_device.
    """
    if not usable_disk_size(size):
        return None
    if int(size) != const.IPODREPARTITION_DISK_SIZE:
        return None
    p = Path(path)
    if not p.exists():
        return None
    from .layout import inspect_device

    return inspect_device(p, int(size))


def check_disk_size(
    size: int,
    *,
    allow_size: Optional[int] = None,
    confirm_untested: bool = False,
    what: str = "nano",
    layout=None,
    ipodrepartition_4k: Optional[bool] = None,
) -> dict:
    """Return size info or raise Refused.

    The tested 16 GB Finder size always passes. The IPodRepartition USB map
    (15806623744) passes only when the MBR is type 0x0c, hidden 64, 4K FAT32,
    firmware in NAND. Any other size is refused unless --allow-disk-size
    matches the actual byte count AND --confirm-untested-capacity is set.
    That override never skips hash checks, backups, or dry-run. Size 0 is
    never a disk.
    """
    if not usable_disk_size(size):
        raise Refused(
            f"{what} size {size} is not a usable disk (a real nano is never 0 bytes)"
        )
    if ipodrepartition_4k is None and layout is not None:
        ipodrepartition_4k = _layout_is_ipodrepartition_4k(layout)
    info = const.KNOWN_DISK_SIZES.get(size)
    if info and info.get("hardware_tested"):
        return {"size": size, "known": True, "hardware_tested": True, "label": info["label"]}
    if size == const.IPODREPARTITION_DISK_SIZE:
        if ipodrepartition_4k:
            label = (info or {}).get(
                "label",
                "16 GB nano 7G IPodRepartition 4K FAT32",
            )
            return {
                "size": size,
                "known": True,
                "hardware_tested": False,
                "ipodrepartition_4k_fat32": True,
                "label": label,
            }
        if size == allow_size and confirm_untested:
            return {
                "size": size,
                "known": True,
                "hardware_tested": False,
                "override": True,
                "label": (info or {}).get(
                    "label",
                    "16 GB nano 7G IPodRepartition USB map (operator override)",
                ),
            }
        raise Refused(
            f"{what} size is {size} bytes (IPodRepartition USB map) but the "
            "MBR/FAT layout is not type 0x0c, hidden 64, 4K FAT32 with firmware "
            "in NAND. Inspect the live disk or image. Or pass BOTH "
            f"--allow-disk-size {size} and --confirm-untested-capacity."
        )
    if info and not confirm_untested:
        raise Refused(
            f"{what} size {size} is listed ({info['label']}) but has not been "
            f"hardware-tested. Pass --confirm-untested-capacity if you still want to continue."
        )
    if size == allow_size and confirm_untested:
        return {
            "size": size,
            "known": bool(info),
            "hardware_tested": False,
            "label": info["label"] if info else "operator override",
            "override": True,
        }
    tested = const.TESTED_DISK_SIZE
    raise Refused(
        f"{what} size is {size} bytes; the hardware-tested size is {tested} "
        f"(16 GB silver 2012 unit). The IPodRepartition map "
        f"{const.IPODREPARTITION_DISK_SIZE} is accepted only with a matching "
        f"MBR 0x0c / hidden 64 / 4K FAT32 layout.\n"
        f"  Other nano 7G capacities are not written unless you pass BOTH:\n"
        f"    --allow-disk-size {size}\n"
        f"    --confirm-untested-capacity\n"
        f"  Do not add a size to the allow-list until it has been tested on hardware. "
        f"Keep the full backup. If you are unsure, stop."
    )


def backup_verify_size(
    backup: Path,
    *,
    nano_size: Optional[int] = None,
    receipt_size: Optional[int] = None,
    format_only: bool = False,
    scsi_dump: bool = False,
) -> int:
    """Byte count check_backup hashes against.

    --format-only and --scsi-dump hash the backup file (or its receipt). After
    IPodRepartition the live SCSI size can be a few MiB larger than the Finder
    image; that image is still the recovery copy. A new backup of the empty
    FAT volume is not useful. Full convert still hashes against the live nano.
    """
    backup = Path(backup)
    rec = None if receipt_size in (None, "") else int(receipt_size)
    live = None if nano_size in (None, "") else int(nano_size)
    if rec is not None and not usable_disk_size(rec):
        rec = None
    if live is not None and not usable_disk_size(live):
        live = None
    if format_only or scsi_dump:
        if backup.is_file():
            return file_size(backup)
        if rec is not None:
            return rec
        if live is not None:
            return live
        raise Refused(
            "cannot determine backup size without the file or a backup receipt. "
            "Pass --backup pointing at the hashed Finder image."
        )
    if live is not None:
        return live
    if rec is not None:
        return rec
    raise Refused(
        "cannot determine nano size without the device or a backup receipt. "
        "Run ./start backup first, or connect the nano."
    )


def ipodrepartition_delta_ok(live_size: int, backup_size: int) -> bool:
    """True when live USB map is a 4K-aligned IPodRepartition growth of backup."""
    delta = live_size - backup_size
    return live_size > backup_size and delta % 4096 == 0 and 0 < delta <= MAX_IPODREPARTITION_DELTA


def check_backup(
    backup: Path,
    expected_sha256: str,
    expected_size: Optional[int],
    *,
    skip_file: bool = False,
    verify_bytes: bool = True,
    allow_size_mismatch: bool = False,
) -> str:
    """Verify backup bytes when the file is present.

    skip_file is only for the SSH agent, where the image lives on the Mac.
    If the file exists here, it is hashed unless verify_bytes is False
    (recorded size+sha from a completed backup; do not re-read 15 GB).
    """
    backup = Path(backup)
    if not expected_sha256 or len(expected_sha256) != 64 or any(
        c not in "0123456789abcdef" for c in expected_sha256.lower()
    ):
        raise Refused("backup SHA-256 is missing or not 64 hex characters")
    if backup.is_file():
        die_if_symlink(backup, "backup")
        actual_size = file_size(backup)
        if expected_size not in (None, 0) and actual_size != expected_size:
            if allow_size_mismatch:
                print(
                    f"    backup    size {actual_size} != live/expected {expected_size} "
                    "(Finder vs IPodRepartition map). Hash still checked. "
                    "library restore copies only the three DB files."
                )
            else:
                raise Refused(
                    f"backup size {actual_size} does not match the nano size {expected_size}. "
                    f"Take a new backup of THIS nano and use that."
                )
        if not verify_bytes:
            return expected_sha256.lower()
        actual = sha256_file(backup)
        if actual != expected_sha256.lower():
            raise Refused(
                f"backup hash mismatch for {backup}\n"
                f"  expected {expected_sha256}\n"
                f"  actual   {actual}\n"
                f"  The file changed since you recorded the hash. Take a new backup."
            )
        return actual
    if skip_file:
        return expected_sha256.lower()
    raise Refused(f"backup missing: {backup}")


def check_ab_receipt(rec: dict, usb_serial: str | None = None) -> None:
    """A/B loader receipt must match the hardware-tested hashes (flash-once.sh)."""
    if rec.get("verified") != "yes":
        raise Refused(
            "verified A/B loader-file receipt is missing. Install the loader files first "
            "(./start install-untether, or ./start untether)."
        )
    if rec.get("active_slot") != "A":
        raise Refused("A/B slot A was not prepared")
    if rec.get("ab_sha256") != const.AB_SHA256:
        raise Refused("A/B selector hash receipt missing or wrong")
    if rec.get("slot_a_sha256") != const.SLOT_A_SHA256:
        raise Refused("slot A hash receipt missing or wrong")
    if rec.get("resident_sha256") != const.RESIDENT_SHA256:
        raise Refused("working resident hash receipt missing or wrong")
    rec_serial = (rec.get("usb_serial") or "").strip()
    if not rec_serial:
        raise Refused("A/B receipt is missing usb_serial")
    if usb_serial and rec_serial != usb_serial:
        raise Refused("A/B receipt belongs to a different USB device")


def plausible_usb_serial(value: object) -> str:
    """Return a USB serial or empty if the value is a product string / disk name."""
    s = str(value or "").strip()
    if not s:
        return ""
    if s.lower() in {"unknown", "none", "null"}:
        return ""
    if re.fullmatch(r"r?disk\d+", s, re.I):
        return ""
    if re.search(r"\s", s):
        return ""
    if re.search(r"ipod", s, re.I) and re.search(r"media|apple", s, re.I):
        return ""
    return s


def backup_filename_token(serial: str) -> str:
    """Keep backup names free of spaces so later argv quoting is less error-prone."""
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", (serial or "").strip())
    token = re.sub(r"-{2,}", "-", token).strip("-.")
    return (token[:80] or "unknown")


def check_usb_serial(usb_serial: str, *, required: bool = True) -> str:
    """Refuse missing, disk-name, or product-string USB serials."""
    s = (usb_serial or "").strip()
    if not s:
        if required:
            raise Refused("USB serial is missing; refusing to continue without a device identity")
        return ""
    if s.lower() in {"unknown", "none", "null"}:
        raise Refused("USB serial is unknown; refusing to continue without a device identity")
    if re.fullmatch(r"r?disk\d+", s, re.I):
        raise Refused(
            f"USB serial {s!r} looks like a disk name, not the iPod USB serial. "
            "On macOS this toolkit reads ioreg 'USB Serial Number' for 05ac:1267."
        )
    if re.search(r"\s", s) or (
        re.search(r"ipod", s, re.I) and re.search(r"media|apple", s, re.I)
    ):
        raise Refused(
            f"USB serial {s!r} looks like a USB product string, not a serial. "
            "On macOS, run ioreg on 05ac:1267 and use 'USB Serial Number' "
            "(diskutil MediaName / IORegistryEntryName is 'Apple iPod Media')."
        )
    return s


def check_mse(mse: Path, *, rescue: bool = False, allow_untested: bool = False) -> str:
    mse = Path(mse)
    if not mse.is_file():
        raise Refused(f"firmware image missing: {mse} (run: ./start build-firmware)")
    die_if_symlink(mse, "firmware image")
    size = file_size(mse)
    if size != const.FIRMWARE_MSE_SIZE:
        raise Refused(f"firmware image size {size} != {const.FIRMWARE_MSE_SIZE}")
    expected = const.RESCUE_MSE_SHA256 if rescue else const.FIRMWARE_MSE_SHA256
    actual = sha256_file(mse)
    if actual == expected:
        return actual
    if allow_untested:
        print("HARDWARE: firmware image is not the hardware-tested hash.")
        print(f"  expected {expected}")
        print(f"  actual   {actual}")
        print("  --i-accept-untested-firmware is set. This MSE has not been")
        print("  written on the upstream test unit. Keep the hashed backup.")
        print("  Revert with ./start rescue (stock 39A10023). docs/RECOVERY.md")
        return actual
    raise Refused(
        f"firmware image is not the hardware-tested hash\n"
        f"  expected {expected}\n"
        f"  actual   {actual}\n"
        f"  Refusing to flash. Rebuild with ./start build-firmware and do not "
        f"override this check. A themed or combined MSE is a new image: pass "
        f"--i-accept-untested-firmware only if you intend to write it. "
        f"The default path stays the tested hash."
    )


def check_stock_mse(mse: Path) -> str:
    """Stock 39A10023 Firmware.MSE extracted from the hashed IPSW.

    Must be the IPSW payload, not the Helvetica-patched untether image.
    Integrity of the bytes is the IPSW SHA-256 plus this size check.
    """
    mse = Path(mse)
    if not mse.is_file():
        raise Refused(
            f"stock Firmware.MSE missing: {mse}. "
            "Run ./start fat32 (it extracts from the official IPSW) or "
            "./start build-firmware."
        )
    die_if_symlink(mse, "stock firmware image")
    size = file_size(mse)
    if size != const.FIRMWARE_MSE_SIZE:
        raise Refused(f"stock Firmware.MSE size {size} != {const.FIRMWARE_MSE_SIZE}")
    actual = sha256_file(mse)
    if actual == const.FIRMWARE_MSE_SHA256:
        raise Refused(
            "this file is the patched untether Firmware.MSE. Conversion needs "
            "the unpatched stock image from iPod_1.1.2_39A10023.ipsw."
        )
    if actual == const.RESCUE_MSE_SHA256:
        raise Refused(
            "this file is the rescue patched Firmware.MSE. Conversion needs "
            "the unpatched stock image from iPod_1.1.2_39A10023.ipsw."
        )
    return actual


def check_bootloader(path: Path, *, expected_sha256: Optional[str] = None) -> str:
    """Pinned 2015 N31.bootloader.rb3. Refuses the 2012 37A40005 prefix."""
    path = Path(path)
    if not path.is_file():
        raise Refused(
            f"bootloader missing: {path}. Extract it from the hashed "
            f"{const.STOCK_IPSW_NAME} with ./start bootloader --check-only."
        )
    die_if_symlink(path, "bootloader")
    actual = sha256_file(path)
    expected = expected_sha256 or const.BOOTLOADER_39A10023_SHA256
    if actual.startswith(const.BOOTLOADER_37A40005_SHA256_PREFIX):
        raise Refused(
            f"bootloader is the 2012 37A40005 image (sha256 {actual}). "
            "Mid-2015 / Nano7Late needs 39A10023 from iPod_1.1.2_39A10023.ipsw. "
            "Nothing written."
        )
    if actual != expected:
        raise Refused(
            f"bootloader hash mismatch\n  expected {expected}\n  actual   {actual}\n"
            "Refusing to write. Re-extract from the hashed 39A10023 IPSW."
        )
    return actual


def bootloader_identity(digest: str) -> str:
    d = (digest or "").strip().lower()
    if d == const.BOOTLOADER_39A10023_SHA256:
        return "39A10023"
    if d.startswith(const.BOOTLOADER_37A40005_SHA256_PREFIX):
        return "37A40005"
    return "unknown"


def verify_release_artifacts() -> None:
    from .const import artifact, sha256sums

    sums = sha256sums()
    missing = []
    bad = []
    for rel, expected in sums.items():
        path = artifact(rel)
        if not path.is_file():
            missing.append(rel)
            continue
        actual = sha256_file(path)
        if actual != expected:
            bad.append(f"{rel}: expected {expected} actual {actual}")
    if missing or bad:
        detail = []
        if missing:
            detail.append("missing: " + ", ".join(missing[:8]))
        if bad:
            detail.extend(bad[:8])
        raise Refused("release artifact verification failed: " + "; ".join(detail))
