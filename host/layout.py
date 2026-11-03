"""Parse iPod partition tables. Read-only except for building a file image.

Windows iTunes ("Winpod") writes a DOS MBR: partition 0 type 0x00 (firmware,
hidden), partition 1 type 0x0b or 0x0c (FAT32 data). Sources: Rockbox
ipodpatcher (write_dos_partition_table), Rockbox wiki IpodPatcher /
IpodConversionToFAT32, and GParted notes on type-0 firmware partitions.

Mac Finder / iTunes Restore ("Macpod") on older nanos writes an Apple
Partition Map with Apple_MDFW (firmware) and Apple_HFS / Apple_HFSX (data).
A Finder-restored nano 7G is 4K APM + Apple_HFS only: firmware/MSE lives
in NAND, not on the USB map. Apple's 2005 nano test procedure: factory
units are FAT32; the first Mac iTunes connection reformats HFS+.

This module never invents nano 7G LBA numbers. It only reports what is on
the image, or copies firmware start/size already present in an APM (the
ipodpatcher conversion). Writing a constructed MBR to a live nano 7G is
refused elsewhere: Rockbox's conversion tables do not list nano 6G/7G, and
this repo has no Windows iTunes n7g dump.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from .compat import Refused, is_macos

# Darwin <sys/disk.h>: DKIOCGETBLOCKSIZE = _IOR('d', 24, uint32_t)
# DKIOCGETBLOCKCOUNT = _IOR('d', 25, uint64_t). Capacity = count * block size.
# /dev/rdiskN is a character device: st_size is 0 and SEEK_END also returns 0.
DKIOCGETBLOCKSIZE = 0x40046418
DKIOCGETBLOCKCOUNT = 0x40086419

SECTOR = 512
FAT_TYPES = {0x0B, 0x0C, 0x0E}
SECTOR_CANDIDATES = (512, 1024, 2048, 4096)
# 4K APM maps can occupy many blocks (MKN52 Finder Restore: 62-block map).
MAX_PREFIX = 512 * 1024

# Finder-restored MKN52ZP (2026-10-28 hardware inspect of a hashed backup).
# USB disk only: Apple Partition Map + one Apple_HFS volume. No Apple_MDFW.
# Firmware/MSE for nano 7G lives in NAND (DFU/WTF/SCSI writefirmware).
MKN52_DISK_BYTES = 15_798_411_264
MKN52_APM_SECTOR = 4096
MKN52_APM_MAP_START = 1
MKN52_APM_MAP_SIZE = 62
MKN52_APM_HFS_START = 64
MKN52_APM_HFS_SIZE = 3_856_960
MKN52_HFS_BYTES = MKN52_APM_HFS_SIZE * MKN52_APM_SECTOR  # 15_798_108_160
MKN52_APM_DISK_LBAS = MKN52_DISK_BYTES // MKN52_APM_SECTOR  # 3_857_034
MKN52_APM_TRAILING_SECTORS = MKN52_APM_DISK_LBAS - (
    MKN52_APM_HFS_START + MKN52_APM_HFS_SIZE
)  # 10

# MKN52ZP 2026-10-28 after wInd3x restore --full / IPodRepartition (recovery
# diskmode 05ac:1267). MBR p0 type 0x0c start_lba=64 size_lba=3858975.
# Those LBA numbers fill 15,806,623,744 bytes only as 4096-byte sectors
# (64*4096 + 3858975*4096 == capacity). 512-byte parsing yields a 1.97 GB
# slice that overlaps the MBR gap. Firmware stays in NAND (no type-0x00 slot).
# wInd3x pkg/usbms/ipod.go IPodRepartition: targetSize % 4096 == 0, sectors
# := targetSize >> 12. wInd3x pkg/usbms/mbr.go: MBR LBAs are in the device's
# reported block unit.
MKN52_RECOVERY_BYTES = 15_806_623_744
MKN52_RECOVERY_SECTOR = 4096
MKN52_RECOVERY_FAT_START = 64
MKN52_RECOVERY_FAT_SIZE = 3_858_975
MKN52_RECOVERY_FAT_BYTES = MKN52_RECOVERY_FAT_SIZE * MKN52_RECOVERY_SECTOR
MKN52_RECOVERY_FAT_START_BYTES = MKN52_RECOVERY_FAT_START * MKN52_RECOVERY_SECTOR
MKN52_RECOVERY_DISK_LBAS = MKN52_RECOVERY_BYTES // MKN52_RECOVERY_SECTOR  # 3_859_039
# IPodRepartition grew the USB map (Finder 15_798_411_264 -> recovery 15_806_623_744).
MKN52_IPODREPARTITION_DELTA = MKN52_RECOVERY_BYTES - MKN52_DISK_BYTES  # 8_212_480
# Fail-closed cap for --format-only accepting a pre-repartition backup.
MAX_IPODREPARTITION_DELTA = 16 * 1024 * 1024


@dataclass
class Partition:
    index: int
    type_id: int
    type_name: str
    start_lba: int
    size_lba: int
    scheme: str
    sector_size: int = SECTOR

    @property
    def start_bytes(self) -> int:
        return self.start_lba * self.sector_size

    @property
    def size_bytes(self) -> int:
        return self.size_lba * self.sector_size

    @property
    def end_lba(self) -> int:
        return self.start_lba + self.size_lba


@dataclass
class DiskLayout:
    scheme: str  # mbr, apm, gpt, unknown
    sector_size: int
    size_bytes: int
    partitions: List[Partition] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    mbr_signature: bool = False
    sector_resolved: bool = True
    scsi_block_size: Optional[int] = None

    @property
    def firmware(self) -> Optional[Partition]:
        for p in self.partitions:
            if p.scheme == "mbr" and p.type_id == 0x00 and p.size_lba > 0:
                return p
            if p.scheme == "apm" and p.type_name in {"Apple_MDFW", "Apple_Firmware"}:
                return p
        return None

    @property
    def data(self) -> Optional[Partition]:
        for p in self.partitions:
            if p.scheme == "mbr" and p.type_id in FAT_TYPES and p.size_lba > 0:
                return p
            if p.scheme == "apm" and p.type_name in {"Apple_HFS", "Apple_HFSX", "Apple_HFS+"}:
                return p
        return None

    def is_winpod(self) -> bool:
        fw, data = self.firmware, self.data
        return (
            self.scheme == "mbr"
            and self.sector_resolved
            and fw is not None
            and data is not None
            and fw.index == 0
            and data.index == 1
            and fw.size_lba > 0
            and data.size_lba > 0
        )

    def is_ipodrepartition_data(self) -> bool:
        """MBR FAT32 data volume, firmware in NAND (nano 7G after IPodRepartition)."""
        if self.scheme != "mbr" or not self.sector_resolved:
            return False
        if self.firmware is not None:
            return False
        data = self.data
        return data is not None and data.type_id in FAT_TYPES and data.size_lba > 0

    def is_ipodrepartition_4k_fat32(self) -> bool:
        """MKN52-class IPodRepartition: MBR 0x0c, hidden 64, 4K fill, firmware in NAND."""
        if not self.is_ipodrepartition_data():
            return False
        if self.sector_size != 4096:
            return False
        data = self.data
        return (
            data is not None
            and data.type_id == 0x0C
            and data.start_lba == 64
            and data.size_lba > 0
        )

    def is_macpod(self) -> bool:
        """ipodpatcher-style Macpod: firmware AND data on the USB map."""
        fw, data = self.firmware, self.data
        return self.scheme == "apm" and fw is not None and data is not None

    def is_macpod_usb_data(self) -> bool:
        """APM + HFS data volume. Firmware may be absent (nano 7G NAND)."""
        return self.scheme == "apm" and self.data is not None

    def summary(self) -> str:
        lines = [
            f"scheme {self.scheme}  sector {self.sector_size}  size {self.size_bytes} bytes"
        ]
        if self.is_winpod():
            lines.append("identity: Winpod (MBR type 0x00 firmware + FAT32 data)")
        elif self.is_ipodrepartition_data():
            lines.append(
                "identity: IPodRepartition data volume (MBR FAT32; firmware in NAND)"
            )
        elif self.is_macpod():
            lines.append("identity: Macpod (APM Apple_MDFW + Apple_HFS)")
        elif self.is_macpod_usb_data() and self.firmware is None:
            lines.append(
                "identity: Macpod data volume (APM Apple_HFS; no Apple_MDFW on the USB disk)"
            )
        elif self.scheme == "gpt":
            lines.append("identity: GPT (not an iPod Winpod/Macpod table)")
        else:
            lines.append("identity: unknown (fail closed for host-built tables)")
        if self.scsi_block_size:
            lines.append(f"  scsi_block_size={self.scsi_block_size}")
        for p in self.partitions:
            lines.append(
                f"  p{p.index} type={p.type_name} start_lba={p.start_lba} "
                f"size_lba={p.size_lba} ({p.size_bytes} bytes) "
                f"start_byte={p.start_bytes} end_byte={p.start_bytes + p.size_bytes}"
            )
        for n in self.notes:
            lines.append(f"  note: {n}")
        return "\n".join(lines)


def _positive_size(value) -> int:
    """Capacity in bytes, or 0 when missing/unusable. 0 is never a nano."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def _bsd_whole_disk_ident(path: Path) -> str:
    """Map /dev/rdisk4 or /dev/disk4 to diskutil ident disk4. Not a slice (s1)."""
    name = Path(path).name
    if name.startswith("rdisk"):
        rest = name[5:]
    elif name.startswith("disk"):
        rest = name[4:]
    else:
        return ""
    if rest.isdigit():
        return "disk" + rest
    return ""


def _darwin_ioctl_disk_bytes(f) -> int:
    """DKIOCGETBLOCKCOUNT * DKIOCGETBLOCKSIZE on a Darwin raw disk fd."""
    try:
        import array
        import fcntl

        fd = f.fileno()
    except (OSError, AttributeError, TypeError):
        return 0
    try:
        block_size = array.array("I", [0])
        fcntl.ioctl(fd, DKIOCGETBLOCKSIZE, block_size)
        block_count = array.array("Q", [0])
        fcntl.ioctl(fd, DKIOCGETBLOCKCOUNT, block_count)
    except (OSError, OverflowError, TypeError, ValueError, AttributeError):
        return 0
    bs = int(block_size[0])
    bc = int(block_count[0])
    if bs <= 0 or bc <= 0:
        return 0
    return bs * bc


def _darwin_diskutil_total_size(path: Path) -> int:
    """diskutil info -plist Size/TotalSize. Leftover nodes report 0 and stay 0."""
    if not is_macos():
        return 0
    ident = _bsd_whole_disk_ident(path)
    if not ident:
        return 0
    import plistlib
    import subprocess

    try:
        blob = subprocess.check_output(
            ["diskutil", "info", "-plist", ident], timeout=15
        )
        info = plistlib.loads(blob)
    except (
        FileNotFoundError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        plistlib.InvalidFileException,
        OSError,
        TypeError,
        ValueError,
    ):
        return 0
    return _positive_size(info.get("TotalSize") or info.get("Size") or 0)


def read_prefix(
    path: Path, n: int = MAX_PREFIX, size_bytes: Optional[int] = None
) -> Tuple[bytes, int]:
    path = Path(path)
    if not path.exists():
        raise Refused(f"cannot read {path}: not found")
    # Prefer the USB capacity from device discovery. Darwin /dev/rdiskN
    # reports st_size 0 and SEEK_END 0; that is not a leftover node.
    size = _positive_size(size_bytes)
    if size == 0:
        size = _positive_size(path.stat().st_size)
    with open(path, "rb") as f:
        data = f.read(n)
        if size == 0:
            try:
                f.seek(0, 2)
                size = _positive_size(f.tell())
            except OSError:
                size = 0
        if size == 0 and is_macos():
            size = _positive_size(_darwin_ioctl_disk_bytes(f))
    if size == 0 and is_macos():
        size = _positive_size(_darwin_diskutil_total_size(path))
    if size < 512:
        raise Refused(
            f"{path} size {size} is not a usable disk (a real nano is never 0 bytes)"
        )
    if len(data) < 512:
        raise Refused(f"{path} is shorter than one sector ({len(data)} bytes)")
    return data, size


def inspect_bytes(
    raw: bytes, size: int, *, scsi_block_size: Optional[int] = None
) -> DiskLayout:
    """Parse a partition-table prefix already in memory. size is the whole disk."""
    if len(raw) < 512:
        raise Refused(f"prefix is shorter than one sector ({len(raw)} bytes)")
    if size < 512:
        raise Refused(
            f"disk size {size} is not a usable disk (a real nano is never 0 bytes; "
            "leftover /dev nodes after unplug are not a capacity)"
        )
    # APM DDM is "ER" at byte 0. Check that before GPT at offset 512: a 4K
    # DDM is 4096 bytes, so offset 512 is inside the DDM, not a GPT header.
    if raw[0:2] == b"ER":
        layout = _parse_apm(raw, size)
        layout.scsi_block_size = scsi_block_size
        return layout
    if len(raw) >= 1024 and raw[512:520] == b"EFI PART":
        layout = DiskLayout(scheme="gpt", sector_size=SECTOR, size_bytes=size)
        layout.notes.append("protective or primary GPT header at LBA 1")
        layout.scsi_block_size = scsi_block_size
        if raw[510] == 0x55 and raw[511] == 0xAA:
            layout.mbr_signature = True
            layout.notes.append("protective MBR signature present")
        return layout
    if raw[510] == 0x55 and raw[511] == 0xAA:
        return _parse_mbr(raw, size, scsi_block_size=scsi_block_size)
    layout = DiskLayout(
        scheme="unknown",
        sector_size=SECTOR,
        size_bytes=size,
        sector_resolved=False,
        scsi_block_size=scsi_block_size,
    )
    layout.notes.append("no MBR 55AA, no APM ER signature, no GPT header")
    return layout


def inspect_image(path: Path) -> DiskLayout:
    raw, size = read_prefix(Path(path))
    return inspect_bytes(raw, size)


def inspect_device(path: Path, size_bytes: Optional[int] = None) -> DiskLayout:
    """Parse a live disk or image. Prefer the known USB capacity over st_size 0."""
    raw, size = read_prefix(Path(path), size_bytes=size_bytes)
    known = _positive_size(size_bytes)
    if known:
        size = known
    return inspect_bytes(raw, size)


def _parse_mbr(
    raw: bytes, size: int, *, scsi_block_size: Optional[int] = None
) -> DiskLayout:
    layout = DiskLayout(
        scheme="mbr",
        sector_size=SECTOR,
        size_bytes=size,
        mbr_signature=True,
        scsi_block_size=scsi_block_size,
    )
    for i in range(4):
        e = raw[0x1BE + i * 16 : 0x1BE + (i + 1) * 16]
        type_id = e[4]
        start = struct.unpack_from("<I", e, 8)[0]
        sz = struct.unpack_from("<I", e, 12)[0]
        if type_id == 0 and sz == 0 and start == 0:
            continue
        name = _mbr_type_name(type_id)
        layout.partitions.append(
            Partition(
                index=i,
                type_id=type_id,
                type_name=name,
                start_lba=start,
                size_lba=sz,
                scheme="mbr",
                sector_size=SECTOR,
            )
        )
    if not layout.partitions:
        layout.notes.append("MBR signature present but all four slots empty")
        return layout
    apply_mbr_sector_size(layout, scsi_block_size=scsi_block_size)
    return layout


def mbr_extent_report(
    parts: List[Partition], disk_bytes: int, sector: int
) -> dict:
    """How MBR LBA numbers look at a candidate sector size."""
    overflow = False
    last_end = 0
    rows = []
    for p in parts:
        if p.size_lba == 0 and p.type_id == 0:
            continue
        start_b = p.start_lba * sector
        size_b = p.size_lba * sector
        end_b = start_b + size_b
        if start_b >= disk_bytes or end_b > disk_bytes:
            overflow = True
        last_end = max(last_end, end_b)
        rows.append(
            {
                "index": p.index,
                "start_lba": p.start_lba,
                "size_lba": p.size_lba,
                "start_byte": start_b,
                "size_byte": size_b,
                "end_byte": end_b,
            }
        )
    slack = None if overflow else disk_bytes - last_end
    fill = (
        not overflow
        and slack is not None
        and slack >= 0
        and slack <= max(disk_bytes // 100, 64 * sector)
    )
    return {
        "sector": sector,
        "overflow": overflow,
        "slack": slack,
        "fill": fill,
        "last_end": last_end,
        "rows": rows,
    }


def resolve_mbr_sector_size(
    parts: List[Partition],
    disk_bytes: int,
    *,
    scsi_block_size: Optional[int] = None,
) -> Tuple[int, List[str]]:
    """Pick the unique sector size whose MBR extents fill capacity.

    MKN52ZP 2026-10-28: start_lba=64 size_lba=3858975 on 15806623744 bytes
    fills only at 4096 (slack 0). 512 is a 1.97 GB slice inside 15.8 GB.
    """
    if not parts:
        raise Refused("MBR has no partitions to resolve against capacity")
    reports = [mbr_extent_report(parts, disk_bytes, ss) for ss in SECTOR_CANDIDATES]
    notes = []
    for r in reports:
        if r["overflow"]:
            notes.append(f"{r['sector']}: overflow (end past {disk_bytes})")
        else:
            notes.append(
                f"{r['sector']}: last_end={r['last_end']} slack={r['slack']} "
                f"fill={r['fill']}"
            )
    filling = [r for r in reports if r["fill"]]
    if len(filling) == 1:
        return filling[0]["sector"], notes
    if scsi_block_size in {r["sector"] for r in filling}:
        return int(scsi_block_size), notes
    if len(filling) == 0:
        raise Refused(
            "MBR partition extents do not fill the disk at 512/1024/2048/4096. "
            "A 512-byte reading of a 4K IPodRepartition table is a 1.97 GB "
            "slice inside 15.8 GB and would WRITE the FAT32 prefix on top of "
            "the MBR. Refusing.\n"
            + "\n".join(notes)
            + f"\ncapacity_bytes={disk_bytes} scsi_block_size={scsi_block_size}"
        )
    raise Refused(
        "MBR sector size is ambiguous (more than one candidate fills capacity). "
        "Refusing to guess 512 vs 4096.\n"
        + "\n".join(notes)
        + f"\ncapacity_bytes={disk_bytes} scsi_block_size={scsi_block_size}"
    )


def apply_mbr_sector_size(
    layout: DiskLayout, *, scsi_block_size: Optional[int] = None
) -> DiskLayout:
    """Set layout.sector_size from unique fill, or leave unresolved."""
    try:
        sector, notes = resolve_mbr_sector_size(
            layout.partitions,
            layout.size_bytes,
            scsi_block_size=scsi_block_size,
        )
    except Refused as e:
        layout.sector_resolved = False
        layout.notes.append(str(e).replace("REFUSED: ", "", 1))
        return layout
    layout.sector_size = sector
    layout.sector_resolved = True
    for p in layout.partitions:
        p.sector_size = sector
    for n in notes:
        layout.notes.append("MBR LBA unit " + n)
    if scsi_block_size and scsi_block_size != sector:
        layout.notes.append(
            f"SCSI logical block {scsi_block_size} differs from MBR LBA unit "
            f"{sector}; WRITE(10) uses SCSI blocks, FAT BPB uses {sector}"
        )
    return layout


def _mbr_type_name(type_id: int) -> str:
    names = {
        0x00: "empty/firmware(0x00)",
        0x0B: "FAT32-CHS(0x0b)",
        0x0C: "FAT32-LBA(0x0c)",
        0x0E: "FAT16-LBA(0x0e)",
        0x07: "NTFS/HPFS(0x07)",
        0x83: "Linux(0x83)",
        0xAF: "HFS(0xaf)",
        0xEE: "GPT-protective(0xee)",
    }
    return names.get(type_id, f"unknown(0x{type_id:02x})")


def _parse_apm(raw: bytes, size: int) -> DiskLayout:
    block = struct.unpack(">H", raw[2:4])[0]
    if block not in (512, 1024, 2048, 4096):
        layout = DiskLayout(scheme="unknown", sector_size=block or SECTOR, size_bytes=size)
        layout.notes.append(f"APM DDM claimed implausible block size {block}")
        return layout
    layout = DiskLayout(scheme="apm", sector_size=block, size_bytes=size)
    # Entry 0 is the DDM; APM entries start at block 1.
    if len(raw) < block * 2:
        layout.notes.append("prefix too short to read APM entries")
        return layout
    first = raw[block : block * 2]
    if first[0:2] != b"PM":
        layout.notes.append("DDM present but block 1 is not an APM PM entry")
        layout.scheme = "unknown"
        return layout
    map_count = struct.unpack(">I", first[4:8])[0]
    if map_count < 1 or map_count > 64:
        layout.notes.append(f"implausible APM map count {map_count}")
        layout.scheme = "unknown"
        return layout
    need = block * (1 + map_count)
    if need > len(raw):
        layout.notes.append(
            f"APM map has {map_count} entries; prefix only covers {len(raw)} bytes"
        )
    readable = min(map_count, max(0, len(raw) // block - 1))
    for i in range(readable):
        e = raw[block * (i + 1) : block * (i + 2)]
        if e[0:2] != b"PM":
            layout.notes.append(f"APM entry {i} missing PM signature")
            continue
        start = struct.unpack(">I", e[8:12])[0]
        sz = struct.unpack(">I", e[12:16])[0]
        name = e[16:48].split(b"\x00", 1)[0].decode("ascii", "replace")
        ptype = e[48:80].split(b"\x00", 1)[0].decode("ascii", "replace")
        layout.partitions.append(
            Partition(
                index=i,
                type_id=0,
                type_name=ptype or name or "unnamed",
                start_lba=start,
                size_lba=sz,
                scheme="apm",
                sector_size=block,
            )
        )
    _annotate_apm(layout)
    return layout


def _annotate_apm(layout: DiskLayout) -> None:
    if layout.scheme != "apm" or not layout.sector_size:
        return
    if layout.data is not None and layout.firmware is None:
        layout.notes.append(
            "no Apple_MDFW on this USB map; nano 7G firmware/MSE is in NAND "
            "(DFU/WTF/SCSI writefirmware), not a disk partition"
        )
    if layout.partitions:
        last_end = max(p.end_lba for p in layout.partitions)
        disk_lbas = layout.size_bytes // layout.sector_size
        if disk_lbas and last_end < disk_lbas:
            leftover = disk_lbas - last_end
            layout.notes.append(
                f"{leftover} sectors ({leftover * layout.sector_size} bytes) "
                "after the last partition; not a firmware slice"
            )


def pick_format_partition(layout: DiskLayout) -> Partition:
    """Choose the data slice to format FAT32. Fail closed on doubt.

    After Windows iTunes or wInd3x IPodRepartition, this is the FAT32 slot
    (MBR index 1, type 0x0b/0x0c). A type-0x00 firmware slot is never
    formatted.
    """
    if layout.scheme == "gpt":
        raise Refused(
            "disk has a GPT header. That is not the Winpod table Windows iTunes "
            "writes. Refusing to guess a FAT32 slice.\n" + layout.summary()
        )
    if layout.scheme == "unknown":
        raise Refused(
            "partition table is not a DOS MBR or Apple Partition Map. "
            "Refusing to guess a FAT32 slice.\n" + layout.summary()
        )
    data = layout.data
    if data is not None and layout.scheme == "mbr" and not layout.sector_resolved:
        raise Refused(
            "MBR sector size could not be resolved against capacity. "
            "Will not format using a 512-byte reading of a 4K table.\n"
            + layout.summary()
        )
    if data is not None and data.size_bytes >= 1024 * 1024:
        return data
    if layout.scheme == "mbr" and len(layout.partitions) >= 2:
        fw = layout.partitions[0]
        second = layout.partitions[1]
        if fw.type_id == 0x00 and fw.size_lba > 0 and second.size_lba > 0:
            if second.type_id == 0x00:
                raise Refused(
                    "MBR looks like firmware + a second type-0x00 slot. "
                    "Will not format a type-0x00 partition (that is how the "
                    "firmware partition is stored on a Winpod).\n" + layout.summary()
                )
            return second
    raise Refused(
        "could not find a FAT32 / HFS data partition to format. "
        "The firmware (type 0x00 / Apple_MDFW) slice is never formatted.\n"
        + layout.summary()
    )


def partition_node(raw_device: str, index_zero_based: int) -> str:
    """Map MBR/APM index 0 to the host's first slice (s1 / part1 / n1)."""
    n = index_zero_based + 1
    path = Path(raw_device)
    name = path.name
    parent = str(path.parent)
    if name.startswith("rdisk") and name[5:].isdigit():
        return f"{parent}/{name}s{n}"
    if name.startswith("disk") and name[4:].isdigit():
        return f"{parent}/{name}s{n}"
    if name.startswith("mmcblk") or name.startswith("nvme") or "loop" in name:
        return f"{parent}/{name}p{n}"
    return f"{parent}/{name}{n}"


def encode_mkn52_repartition_mbr() -> bytes:
    """MBR from the MKN52ZP 2026-10-28 IPodRepartition dry-run (4K LBA units)."""
    return encode_mbr(
        [(0x0C, MKN52_RECOVERY_FAT_START, MKN52_RECOVERY_FAT_SIZE)],
        MKN52_RECOVERY_DISK_LBAS,
    )


def encode_mbr(partitions: List[Tuple[int, int, int]], disk_lbas: int) -> bytes:
    """Build a 512-byte MBR. partitions: (type, start_lba, size_lba) for slots 0..3.

    Matches ipodpatcher write_dos_partition_table: CHS fields left zero, slot 0
    type 0x00, slot 1 type 0x0b, 55AA signature. Type is a single byte at +4.
    """
    if disk_lbas < 1:
        raise Refused("disk LBA count is zero")
    sector = bytearray(SECTOR)
    for i, (typ, start, size) in enumerate(partitions[:4]):
        off = 0x1BE + i * 16
        sector[off + 4] = typ & 0xFF
        sector[off + 8 : off + 12] = struct.pack("<I", start)
        sector[off + 12 : off + 16] = struct.pack("<I", size)
        if start + size > disk_lbas:
            raise Refused(
                f"MBR slot {i} ends at LBA {start + size}, past disk {disk_lbas}"
            )
    sector[0x1FE] = 0x55
    sector[0x1FF] = 0xAA
    return bytes(sector)


def encode_apm_ddm(sector_size: int, disk_lbas: int) -> bytes:
    if sector_size not in (512, 1024, 2048, 4096):
        raise Refused(f"APM block size {sector_size} is not 512/1024/2048/4096")
    ddm = bytearray(sector_size)
    ddm[0:2] = b"ER"
    ddm[2:4] = struct.pack(">H", sector_size)
    ddm[4:8] = struct.pack(">I", disk_lbas)
    return bytes(ddm)


def encode_apm_entry(
    sector_size: int,
    map_count: int,
    start: int,
    size: int,
    name: str,
    ptype: str,
) -> bytes:
    entry = bytearray(sector_size)
    entry[0:2] = b"PM"
    entry[4:8] = struct.pack(">I", map_count)
    entry[8:12] = struct.pack(">I", start)
    entry[12:16] = struct.pack(">I", size)
    entry[16:48] = name.encode("ascii").ljust(32, b"\x00")[:32]
    entry[48:80] = ptype.encode("ascii").ljust(32, b"\x00")[:32]
    return bytes(entry)


def build_apm_prefix(
    sector_size: int,
    disk_lbas: int,
    entries: List[Tuple[int, int, str, str]],
) -> bytes:
    """DDM + PM entries. Does not allocate the data volume."""
    if not entries:
        raise Refused("APM needs at least one partition map entry")
    map_count = len(entries)
    blob = bytearray(sector_size * (1 + map_count))
    blob[0:sector_size] = encode_apm_ddm(sector_size, disk_lbas)
    for i, (start, size, name, ptype) in enumerate(entries):
        off = sector_size * (i + 1)
        blob[off : off + sector_size] = encode_apm_entry(
            sector_size, map_count, start, size, name, ptype
        )
    return bytes(blob)


def mkn52_finder_apm_prefix() -> bytes:
    """Exact APM prefix from the MKN52ZP Finder Restore backup (no MDFW)."""
    return build_apm_prefix(
        MKN52_APM_SECTOR,
        MKN52_APM_DISK_LBAS,
        [
            (
                MKN52_APM_MAP_START,
                MKN52_APM_MAP_SIZE,
                "Apple",
                "Apple_partition_map",
            ),
            (MKN52_APM_HFS_START, MKN52_APM_HFS_SIZE, "iPod", "Apple_HFS"),
        ],
    )


def write_mkn52_finder_image(path: Path, *, sparse_full_size: bool = True) -> Path:
    """Sparse file whose APM matches the live MKN52 backup. Not 15 GB of data."""
    path = Path(path)
    path.write_bytes(mkn52_finder_apm_prefix())
    if sparse_full_size:
        with open(path, "r+b") as f:
            f.truncate(MKN52_DISK_BYTES)
    return path


def build_apm_image(
    sector_size: int,
    disk_lbas: int,
    entries: List[Tuple[int, int, str, str]],
    *,
    firmware_marker: bytes = b"",
) -> bytes:
    """In-memory APM image. Refuses huge allocations (use write_mkn52_finder_image)."""
    nbytes = disk_lbas * sector_size
    if nbytes > 64 * 1024 * 1024:
        raise Refused(
            f"refusing to materialize a {nbytes} byte APM image in RAM; "
            "write a sparse prefix instead"
        )
    img = bytearray(nbytes)
    prefix = build_apm_prefix(sector_size, disk_lbas, entries)
    img[0 : len(prefix)] = prefix
    marker = firmware_marker or b"FWREGION"
    for start, _size, _name, ptype in entries:
        if ptype in {"Apple_MDFW", "Apple_Firmware"}:
            off = start * sector_size
            img[off : off + len(marker)] = marker
            break
    return bytes(img)


def build_apm_macpod(
    disk_lbas: int,
    fw_start: int,
    fw_size: int,
    data_start: int,
    data_size: int,
    *,
    firmware_marker: bytes = b"",
    sector_size: int = SECTOR,
) -> bytes:
    """Synthetic Macpod image for tests (not a factory dump). 512-byte APM by default."""
    if fw_start < 4 or data_start < fw_start + fw_size:
        raise Refused("APM firmware/data LBAs overlap or sit inside the map")
    entries = [
        (1, 3, "Apple", "Apple_partition_map"),
        (fw_start, fw_size, "firmware", "Apple_MDFW"),
        (data_start, data_size, "iPod", "Apple_HFS"),
    ]
    return build_apm_image(
        sector_size,
        disk_lbas,
        entries,
        firmware_marker=firmware_marker or b"FWREGION",
    )


def build_mbr_winpod(
    disk_lbas: int,
    fw_start: int,
    fw_size: int,
    *,
    fat_type: int = 0x0B,
    firmware_marker: bytes = b"",
) -> bytes:
    """Synthetic Winpod image for tests (not a Windows iTunes dump)."""
    if fw_start < 1 or fw_start + fw_size >= disk_lbas:
        raise Refused("Winpod firmware LBAs do not fit on the disk")
    data_start = fw_start + fw_size
    data_size = disk_lbas - data_start
    img = bytearray(disk_lbas * SECTOR)
    img[0:SECTOR] = encode_mbr(
        [(0x00, fw_start, fw_size), (fat_type, data_start, data_size)],
        disk_lbas,
    )
    marker = firmware_marker or b"FWREGION"
    fw_off = fw_start * SECTOR
    img[fw_off : fw_off + len(marker)] = marker
    return bytes(img)
