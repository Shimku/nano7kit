"""Find an iPod nano 7G on macOS (diskutil) or Linux (by-id / sysfs)."""

from __future__ import annotations

import plistlib
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from . import const
from .compat import Refused, have, is_linux, is_macos, run_out
from .safety import describe_size, plausible_usb_serial, usable_disk_size


@dataclass
class Nano:
    os_name: str
    device: str
    raw_device: str
    partition: str
    usb_serial: str
    size: int
    fstype: str
    mountpoints: List[str] = field(default_factory=list)
    model: str = "iPod"
    bsd_name: str = ""
    hardware_tested_size: bool = False
    size_note: str = ""

    def as_dict(self) -> dict:
        return {
            "os": self.os_name,
            "device": self.device,
            "raw_device": self.raw_device,
            "partition": self.partition,
            "usb_serial": self.usb_serial,
            "size": self.size,
            "fstype": self.fstype,
            "mountpoints": self.mountpoints,
            "model": self.model,
            "hardware_tested_size": self.hardware_tested_size,
            "size_note": self.size_note,
        }


def find_nanos() -> List[Nano]:
    if is_macos():
        return _find_macos()
    if is_linux():
        return _find_linux()
    raise Refused(f"device discovery is not implemented on {__import__('platform').system()}")


def require_one_nano() -> Nano:
    found = find_nanos()
    if not found:
        raise Refused(
            "no iPod found. Plug it in, wait until it shows Connected, and do not "
            "eject it. On a Mac, leave it mounted or unmounted; this tool will unmount "
            "it. If the nano is on a Raspberry Pi, run ./start setup and choose the Pi."
        )
    if len(found) > 1:
        listing = "\n".join(f"  {n.device} serial={n.usb_serial} {n.size} bytes" for n in found)
        raise Refused("more than one iPod is attached; unplug the extras:\n" + listing)
    return found[0]


def annotate_size(nano: Nano) -> Nano:
    info = const.KNOWN_DISK_SIZES.get(nano.size)
    nano.hardware_tested_size = bool(info and info.get("hardware_tested"))
    nano.size_note = describe_size(nano.size)
    return nano


def usable_nano(nano: Optional[Nano]) -> Optional[Nano]:
    """Drop leftover kernel nodes that report size 0. Those are not a live nano."""
    if nano is None:
        return None
    if not usable_disk_size(nano.size):
        return None
    return nano


# ----- Linux -----

def _find_linux() -> List[Nano]:
    found: List[Nano] = []
    by_id = Path("/dev/disk/by-id")
    links = []
    if by_id.is_dir():
        links = sorted(
            p for p in by_id.glob("usb-Apple_iPod_*") if p.exists() and "-part" not in p.name
        )
    if links:
        for link in links:
            nano = _linux_from_node(str(link), Path(link).resolve())
            if nano:
                found.append(annotate_size(nano))
        return found
    # Fallback: scan /sys/block for Apple iPod.
    sysblock = Path("/sys/block")
    if not sysblock.is_dir():
        return []
    for node in sorted(sysblock.iterdir()):
        vendor = _read_sys(node / "device" / "vendor")
        model = _read_sys(node / "device" / "model")
        if vendor == "Apple" and model and "ipod" in model.lower():
            dev = Path("/dev") / node.name
            nano = _linux_from_node(str(dev), dev)
            if nano:
                found.append(annotate_size(nano))
    return found


def _read_sys(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def _linux_from_node(stable: str, dev: Path) -> Optional[Nano]:
    if not dev.exists():
        return None
    name = dev.name
    sysdev = Path("/sys/block") / name
    size_sectors = _read_sys(sysdev / "size")
    try:
        size = int(size_sectors) * 512
    except ValueError:
        size = 0
    serial = _linux_usb_serial(name) or _udev_prop(str(dev), "ID_SERIAL_SHORT")
    mounts = _linux_mounts(name)
    part = _linux_partition(stable, name)
    fstype = _fstype_of_node(part)
    return Nano(
        os_name="linux",
        device=stable,
        raw_device=str(dev),
        partition=part,
        usb_serial=serial or "unknown",
        size=size,
        fstype=fstype,
        mountpoints=mounts,
        model=_read_sys(sysdev / "device" / "model") or "iPod",
    )


def _linux_partition(stable: str, name: str) -> str:
    candidates: List[str] = []
    if stable.startswith("/dev/disk/by-id/"):
        for i in (1, 2, 3, 4):
            part = f"{stable}-part{i}"
            if Path(part).exists():
                candidates.append(part)
    else:
        for i in (1, 2, 3, 4):
            for cand in (Path("/dev") / f"{name}{i}", Path("/dev") / f"{name}p{i}"):
                if cand.exists() and str(cand) not in candidates:
                    candidates.append(str(cand))
    for part in candidates:
        if _fstype_of_node(part) == "vfat":
            return part
    if candidates:
        return candidates[0]
    return str(Path("/dev") / f"{name}1")


def _fstype_of_node(node: str) -> str:
    if have("lsblk"):
        try:
            out = run_out(["lsblk", "-no", "FSTYPE", node], check=False).strip()
            if out:
                return _normalize_fstype(out.splitlines()[0])
        except Exception:
            pass
    if have("blkid"):
        try:
            out = run_out(["blkid", "-s", "TYPE", "-o", "value", node], check=False).strip()
            if out:
                return _normalize_fstype(out.splitlines()[0])
        except Exception:
            pass
    return "unknown"


def _linux_usb_serial(name: str) -> str:
    # Walk from the block device up to a USB serial.
    p = Path("/sys/block") / name
    try:
        cur = p.resolve()
    except OSError:
        return ""
    for parent in [cur, *cur.parents]:
        serial_path = parent / "serial"
        if serial_path.is_file():
            val = _read_sys(serial_path)
            if val:
                return val
        idv = parent / "idVendor"
        if idv.is_file() and _read_sys(idv) == "05ac":
            return _read_sys(parent / "serial")
    return ""


def _udev_prop(dev: str, key: str) -> str:
    if not have("udevadm"):
        return ""
    try:
        out = run_out(["udevadm", "info", "--query=property", "--name", dev], check=False)
    except Exception:
        return ""
    prefix = key + "="
    for line in out.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):]
    return ""


def _linux_mounts(name: str) -> List[str]:
    mounts = []
    try:
        text = Path("/proc/mounts").read_text()
    except OSError:
        return mounts
    nodes = {f"/dev/{name}", f"/dev/{name}1"}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in nodes:
            mounts.append(parts[1])
    return mounts


# ----- macOS -----

def _find_macos() -> List[Nano]:
    found: List[Nano] = []
    try:
        blob = subprocess.check_output(["diskutil", "list", "-plist", "external"])
    except (subprocess.CalledProcessError, FileNotFoundError):
        try:
            blob = subprocess.check_output(["diskutil", "list", "-plist"])
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            raise Refused(f"diskutil failed: {exc}") from exc
    data = plistlib.loads(blob)
    for disk in data.get("AllDisksAndPartitions") or []:
        ident = disk.get("DeviceIdentifier") or ""
        if not ident or "s" in ident[4:]:
            # Skip partitions; we want whole disks (diskN).
            continue
        info = _diskutil_info(ident)
        if not _looks_like_ipod(info, disk):
            continue
        size = int(info.get("TotalSize") or disk.get("Size") or 0)
        parts = disk.get("Partitions") or []
        part_id = ""
        fstype = "unknown"
        mounts: List[str] = []
        chosen = _macos_choose_partition(parts)
        if chosen:
            part_id = chosen.get("DeviceIdentifier") or (ident + "s1")
            fstype = (chosen.get("Content") or chosen.get("FilesystemName") or "unknown")
            mp = chosen.get("MountPoint") or info.get("MountPoint") or ""
            if mp:
                mounts.append(mp)
        else:
            part_id = ident + "s1"
            mp = info.get("MountPoint") or ""
            if mp:
                mounts.append(mp)
            fstype = info.get("FilesystemName") or info.get("Content") or "unknown"
        if not mounts:
            mounts = _macos_mounts(ident, part_id)
        serial = (
            plausible_usb_serial(info.get("USB Serial Number"))
            or _macos_usb_serial(ident, info)
            or "unknown"
        )
        fstype_n = _normalize_fstype(str(fstype))
        nano = Nano(
            os_name="darwin",
            device=f"/dev/{ident}",
            raw_device=f"/dev/r{ident}",
            partition=f"/dev/r{part_id}",
            usb_serial=str(serial),
            size=size,
            fstype=fstype_n,
            mountpoints=mounts,
            model=str(info.get("MediaName") or info.get("IOKitName") or "iPod"),
            bsd_name=ident,
        )
        found.append(annotate_size(nano))
    return found


def _diskutil_info(ident: str) -> dict:
    try:
        blob = subprocess.check_output(["diskutil", "info", "-plist", ident])
        return plistlib.loads(blob)
    except (subprocess.CalledProcessError, FileNotFoundError, plistlib.InvalidFileException):
        return {}


def _looks_like_ipod(info: dict, disk: dict) -> bool:
    """Require the word iPod. Generic Apple USB (iPhone, SuperDrive) is not enough."""
    blob = " ".join(
        str(x)
        for x in (
            info.get("MediaName"),
            info.get("DevicePath"),
            info.get("IOKitName"),
            info.get("MediaType"),
            info.get("VolumeName"),
            info.get("USB Product Name"),
            info.get("Product Name"),
            info.get("IORegistryEntryName"),
            disk.get("DeviceIdentifier"),
            disk.get("VolumeName"),
        )
        if x
    ).lower()
    return "ipod" in blob


def _macos_choose_partition(parts: list) -> Optional[dict]:
    """Prefer a FAT data slice over a type-0 firmware slice (Winpod p0)."""
    if not parts:
        return None
    fat = None
    hfs = None
    for p in parts:
        content = str(p.get("Content") or p.get("FilesystemName") or "").lower()
        if "fat" in content or "ms-dos" in content or "dos_fat" in content:
            fat = p
            break
        if "hfs" in content:
            hfs = hfs or p
    return fat or hfs or parts[0]


def _normalize_fstype(raw: str) -> str:
    s = raw.lower()
    if "fat" in s or "ms-dos" in s or "dos_fat" in s or s in ("vfat", "fat32", "msdos"):
        return "vfat"
    if "hfs" in s:
        return "hfs"
    return raw or "unknown"


def _macos_mounts(disk: str, part: str) -> List[str]:
    mounts = []
    try:
        out = run_out(["mount"], check=False)
    except Exception:
        return mounts
    for line in out.splitlines():
        if line.startswith(f"/dev/{disk}") or line.startswith(f"/dev/{part}") or line.startswith(f"/dev/r{part}"):
            m = re.search(r" on (/.*) \(", line)
            if m:
                mounts.append(m.group(1))
    return mounts


def _macos_usb_serial(ident: str, info: dict) -> str:
    """USB serial, never the product string (MediaName / IORegistryEntryName).

    diskutil often omits USB Serial Number on a Finder-mounted nano. ioreg
    IOUSB `USB Serial Number` for 05ac:1267 is the reliable source. A
    case-sensitive grep of system_profiler for 'ipod' misses 'iPod'.
    """
    for key in ("USB Serial Number", "Serial Number"):
        s = plausible_usb_serial(info.get(key))
        if s:
            return s
    s = _macos_ioreg_disk_serial(ident)
    if s:
        return s
    return _macos_profiler_usb_serial(ident)


def _macos_ioreg_disk_serial(ident: str) -> str:
    try:
        out = run_out(["ioreg", "-p", "IOUSB", "-l", "-w", "0"], check=False)
    except Exception:
        return ""
    from .usb import apple_usb_serial_for_bsd, apple_usb_serials_for_pid

    named = plausible_usb_serial(apple_usb_serial_for_bsd(out, ident))
    if named:
        return named
    serials = [
        s
        for s in apple_usb_serials_for_pid(out, const.NANO7_DISK_PID)
        if plausible_usb_serial(s)
    ]
    if len(serials) == 1:
        return serials[0]
    return ""


def _macos_profiler_usb_serial(ident: str) -> str:
    try:
        blob = subprocess.check_output(["system_profiler", "SPUSBDataType", "-xml"], timeout=30)
        data = plistlib.loads(blob)
    except Exception:
        return ""
    hits = []

    def walk(node):
        if isinstance(node, list):
            for x in node:
                walk(x)
            return
        if not isinstance(node, dict):
            return
        name = str(node.get("_name") or "")
        product = str(node.get("product_id") or node.get("Product ID") or "")
        bsd = str(
            node.get("bsd_name")
            or node.get("BSD Name")
            or node.get("bsd_device_name")
            or ""
        )
        serial = plausible_usb_serial(
            node.get("serial_num") or node.get("Serial Number") or ""
        )
        blob = f"{name} {product} {bsd}".lower()
        match = (
            bsd == ident
            or bsd.startswith(ident)
            or "ipod" in blob
            or "05ac:1267" in blob.replace(" ", "")
        )
        if match and serial:
            hits.append(serial)
        for v in node.values():
            if isinstance(v, (list, dict)):
                walk(v)

    walk(data)
    return hits[0] if hits else ""


def format_nano(n: Nano) -> str:
    mounted = ", ".join(n.mountpoints) if n.mountpoints else "nowhere"
    warn_size = "" if n.hardware_tested_size else "  (not the hardware-tested 16 GB size)"
    warn_fs = (
        ""
        if n.fstype == "vfat"
        else "  (need FAT32: ./start fat32 – not Finder Restore, which is HFS+)"
    )
    warn_mnt = "" if not n.mountpoints else "  (will unmount before writing)"
    serial = n.usb_serial
    if not serial or serial.lower() == "unknown":
        serial = (
            "unknown  (not MediaName; ioreg USB Serial Number for 05ac:1267 was empty)"
        )
    return (
        f"iPod found\n"
        f"  device         {n.device}\n"
        f"  raw            {n.raw_device}\n"
        f"  partition      {n.partition}\n"
        f"  usb-serial     {serial}\n"
        f"  size           {n.size_note}{warn_size}\n"
        f"  filesystem     {n.fstype}{warn_fs}\n"
        f"  mounted at     {mounted}{warn_mnt}"
    )
