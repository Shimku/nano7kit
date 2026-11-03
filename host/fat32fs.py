"""Build a FAT32 prefix (boot + FATs + root cluster) without materializing the volume.

Used to format the nano 7G data slice over SCSI WRITE(10) when macOS has no
/dev/disk node. Only the metadata is generated (a few MiB for a 16 GB volume).
The rest of the slice stays unallocated clusters (FAT entries 0).

nano 7G FAT32 (Windows iTunes / IPodRepartition 4K media):
- BPB bytes/sector = 4096. wInd3x IPodRepartition (pkg/usbms/ipod.go) requires
  targetSize % 4096 == 0 and counts sectors as targetSize >> 12. The live
  MKN52ZP MBR (2026-10-28) fills 15,806,623,744 bytes only as 4096-byte LBAs.
  Microsoft fatgen103 allows 512, 1024, 2048, or 4096. 4Kn Windows format uses
  4096. A 512-byte BPB on this device would not match RetailOS or the MBR.
- Cluster size follows Microsoft FAT32 defaults (KB 140365 / fatgen103), not
  the 32 KiB Rockbox-era Winpod size. The MKN52ZP data slice is 14.72 GiB
  (15,806,361,600 bytes), in the 8 GiB-16 GiB bucket: 8 KiB clusters. With
  4096-byte sectors that is sectors_per_cluster=2. An earlier build wrote
  32 KiB (spc=8); RetailOS 39A10023 still did not boot.
- FAT[0] = 0x0FFFFFF8 (media F8). FAT[1] = 0xFFFFFFFF (Windows format.exe and
  freemyipod libs/mkfat32/main.c). Root cluster 2 = 0x0FFFFFFF (EOC).
- 55AA at offset 510 (fatgen103) and, on 4K sectors, also at sector_size-2.
"""

from __future__ import annotations

import struct
from typing import Dict, List, Optional, Tuple

from .compat import Refused

SECTOR = 512
MIN_VOLUME = 1024 * 1024
MAX_PREFIX = 32 * 1024 * 1024
NANO7_FAT_SECTOR = 4096
# Microsoft default for 8 GiB-16 GiB FAT32 (KB 140365 / fatgen103).
NANO7_FAT_CLUSTER = 8 * 1024
# Live test WRITE on MKN52ZP (32 KiB clusters). Documented, no longer emitted.
LIVE_32K_SPC = 8
LIVE_32K_FAT_SECTORS = 472
LIVE_32K_PREFIX_SECTORS = 32 + 2 * LIVE_32K_FAT_SECTORS + LIVE_32K_SPC  # 984
LIVE_32K_PREFIX_BYTES = LIVE_32K_PREFIX_SECTORS * NANO7_FAT_SECTOR  # 4030464

FAT32_MEDIA = 0x0FFFFFF8
FAT32_FAT1_WINDOWS = 0xFFFFFFFF
FAT32_EOC = 0x0FFFFFFF
FSINFO_LEAD = 0x41615252
FSINFO_STRUC = 0x61417272


def microsoft_fat32_cluster_bytes(volume_bytes: int) -> int:
    """Default FAT32 cluster size from Microsoft KB 140365 / fatgen103.

    Thresholds are binary GiB (1024^3), the table Microsoft published:

      260 MiB - 8 GiB   ->  4 KiB
      8 GiB - 16 GiB    ->  8 KiB
      16 GiB - 32 GiB   -> 16 KiB
      32 GiB - 2 TiB    -> 32 KiB

    The MKN52ZP IPodRepartition data slice is 15,806,361,600 bytes = 14.72 GiB,
    so 8 KiB. Rockbox ipodpatcher used 32 KiB (64 * 512) on classic 512-byte
    Winpods; that is a different generation and is not used here. A public
    iTunes-on-Classic report of 16 KiB clusters is also 512-byte-sector
    Classic (spc=32), not nano 7G 4K media.
    """
    gib = 1024 * 1024 * 1024
    if volume_bytes >= 32 * gib:
        return 32 * 1024
    if volume_bytes >= 16 * gib:
        return 16 * 1024
    if volume_bytes >= 8 * gib:
        return 8 * 1024
    if volume_bytes >= 260 * 1024 * 1024:
        return 4 * 1024
    return 4096


def choose_cluster_bytes(volume_bytes: int) -> int:
    """Cluster size in bytes. Microsoft FAT32 defaults, including nano 7G 16 GB."""
    return microsoft_fat32_cluster_bytes(volume_bytes)


def choose_spc(volume_bytes: int, *, bytes_per_sector: int = SECTOR) -> int:
    cluster = choose_cluster_bytes(volume_bytes)
    if cluster % bytes_per_sector:
        raise Refused(
            f"cluster {cluster} is not a multiple of sector {bytes_per_sector}"
        )
    spc = cluster // bytes_per_sector
    if spc < 1 or spc > 128:
        raise Refused(f"sectors per cluster {spc} is out of range")
    return spc


def fat32_geometry(
    volume_bytes: int, *, bytes_per_sector: int = SECTOR
) -> Tuple[int, int, int, int, int]:
    """Return (total_sectors, spc, reserved, fat_sectors, clusters)."""
    if bytes_per_sector not in (512, 1024, 2048, 4096):
        raise Refused(f"FAT32 bytes/sector {bytes_per_sector} is not 512/1024/2048/4096")
    if volume_bytes < MIN_VOLUME:
        raise Refused(f"volume {volume_bytes} bytes is too small for FAT32")
    if volume_bytes % bytes_per_sector:
        raise Refused(f"volume {volume_bytes} is not a multiple of {bytes_per_sector}")
    total = volume_bytes // bytes_per_sector
    reserved = 32
    nfat = 2
    spc = choose_spc(volume_bytes, bytes_per_sector=bytes_per_sector)
    fat = 1
    for _ in range(64):
        data = total - reserved - nfat * fat
        if data <= 0:
            raise Refused("volume has no room for a FAT32 data region")
        clusters = data // spc
        if clusters < 2:
            raise Refused("volume has fewer than two clusters")
        need = (clusters + 2) * 4
        fat2 = (need + bytes_per_sector - 1) // bytes_per_sector
        if fat2 <= fat:
            return total, spc, reserved, fat, clusters
        fat = fat2
    raise Refused("could not settle FAT32 FAT size")


def fat32_prefix_layout(
    volume_bytes: int, *, bytes_per_sector: int = SECTOR
) -> Dict[str, int]:
    """Reserved + both FATs + root cluster. This is what WRITE(10) materializes."""
    total, spc, reserved, fat, clusters = fat32_geometry(
        volume_bytes, bytes_per_sector=bytes_per_sector
    )
    nfat = 2
    prefix_sectors = reserved + nfat * fat + spc
    return {
        "total_sectors": total,
        "spc": spc,
        "reserved": reserved,
        "nfat": nfat,
        "fat_sectors": fat,
        "clusters": clusters,
        "root_cluster": 2,
        "prefix_sectors": prefix_sectors,
        "prefix_bytes": prefix_sectors * bytes_per_sector,
        "cluster_bytes": spc * bytes_per_sector,
        "bytes_per_sector": bytes_per_sector,
        "fat1_byte": reserved * bytes_per_sector,
        "fat2_byte": (reserved + fat) * bytes_per_sector,
        "root_byte": (reserved + nfat * fat) * bytes_per_sector,
    }


def prefix_span_sectors(*, reserved: int, nfat: int, fat_sectors: int, spc: int) -> int:
    """How many sectors cover reserved + every FAT + the first cluster (root)."""
    return int(reserved) + int(nfat) * int(fat_sectors) + int(spc)


def build_fat32_prefix(
    volume_bytes: int,
    *,
    label: str = "IPOD",
    hidden_sectors: int = 0,
    bytes_per_sector: int = SECTOR,
) -> bytes:
    """Boot + reserved + two FATs + first cluster (empty root). Not the whole volume."""
    layout = fat32_prefix_layout(volume_bytes, bytes_per_sector=bytes_per_sector)
    prefix_bytes = layout["prefix_bytes"]
    if prefix_bytes > MAX_PREFIX:
        raise Refused(
            f"FAT32 prefix {prefix_bytes} bytes exceeds {MAX_PREFIX}; refusing"
        )
    vol = _label11(label)
    hidden = hidden_sectors & 0xFFFFFFFF
    boot = _boot_sector(
        total=layout["total_sectors"],
        spc=layout["spc"],
        reserved=layout["reserved"],
        fat_sectors=layout["fat_sectors"],
        hidden=hidden,
        vol=vol,
        bytes_per_sector=bytes_per_sector,
    )
    fsinfo = _fsinfo_sector(
        free_clusters=layout["clusters"] - 1,
        next_free=3,
        bytes_per_sector=bytes_per_sector,
    )
    blob = bytearray(prefix_bytes)
    blob[0:bytes_per_sector] = boot
    blob[bytes_per_sector : 2 * bytes_per_sector] = fsinfo
    # Backup boot + FSInfo at sectors 6 and 7 (FAT32 spec).
    blob[6 * bytes_per_sector : 7 * bytes_per_sector] = boot
    blob[7 * bytes_per_sector : 8 * bytes_per_sector] = fsinfo

    fat = bytearray(layout["fat_sectors"] * bytes_per_sector)
    struct.pack_into("<I", fat, 0, FAT32_MEDIA)
    struct.pack_into("<I", fat, 4, FAT32_FAT1_WINDOWS)
    struct.pack_into("<I", fat, 8, FAT32_EOC)  # cluster 2 = root EOC
    fat1 = layout["fat1_byte"]
    fat2 = layout["fat2_byte"]
    blob[fat1 : fat1 + len(fat)] = fat
    blob[fat2 : fat2 + len(fat)] = fat

    root = bytearray(layout["spc"] * bytes_per_sector)
    root[0:11] = vol
    root[11] = 0x08  # volume label
    root_off = layout["root_byte"]
    blob[root_off : root_off + len(root)] = root
    return bytes(blob)


def is_fat32_boot(sector: bytes, *, bytes_per_sector: int = 0) -> bool:
    if len(sector) < 512:
        return False
    if sector[510] != 0x55 or sector[511] != 0xAA:
        return False
    if sector[0x52:0x5A] != b"FAT32   ":
        return False
    if bytes_per_sector:
        bps = struct.unpack_from("<H", sector, 0x0B)[0]
        if bps != bytes_per_sector:
            return False
    return True


def fat32_bytes_per_sector(sector: bytes) -> int:
    if len(sector) < 0x0D:
        return 0
    return struct.unpack_from("<H", sector, 0x0B)[0]


def analyze_fat32_prefix(
    blob: bytes,
    *,
    volume_bytes: int = 0,
    hidden_expect: int = 0,
    bytes_per_sector: int = 0,
) -> Dict:
    """Parse a data-slice prefix (or a BOT dump of one). Never writes.

    Reports whether reserved + both FATs + root cluster are present, FAT[0]/
    FAT[1]/root EOC, FSInfo, volume label, cluster size vs Microsoft default,
    and 55AA placement.
    """
    issues: List[str] = []
    notes: List[str] = []
    info: Dict = {
        "fat32": False,
        "issues": issues,
        "notes": notes,
        "bytes_in": len(blob),
    }
    if len(blob) < 512:
        issues.append(f"blob is {len(blob)} bytes; need at least 512")
        return info
    if blob[:512] == b"\x00" * 512:
        issues.append("first 512 bytes are zeros (no FAT32 boot, no MBR)")
        info["all_zero_boot"] = True
        return info
    if not is_fat32_boot(blob[:512]):
        issues.append("no FAT32 boot (missing 55AA at 510 or FAT32   id at 0x52)")
        info["boot_hex"] = blob[:16].hex()
        return info

    bps = fat32_bytes_per_sector(blob)
    info["bytes_per_sector"] = bps
    if bps not in (512, 1024, 2048, 4096):
        issues.append(f"BPB bytes/sector {bps} is not 512/1024/2048/4096")
        return info
    if bytes_per_sector and bps != bytes_per_sector:
        issues.append(f"BPB bytes/sector {bps} != expected {bytes_per_sector}")
    if len(blob) < bps:
        issues.append(f"blob {len(blob)} is shorter than one {bps}-byte sector")
        return info

    spc = blob[0x0D]
    reserved = struct.unpack_from("<H", blob, 0x0E)[0]
    nfat = blob[0x10]
    hidden = struct.unpack_from("<I", blob, 0x1C)[0]
    total = struct.unpack_from("<I", blob, 0x20)[0]
    fat_sectors = struct.unpack_from("<I", blob, 0x24)[0]
    rootclus = struct.unpack_from("<I", blob, 0x2C)[0]
    fsinfo_sec = struct.unpack_from("<H", blob, 0x30)[0]
    backup_sec = struct.unpack_from("<H", blob, 0x32)[0]
    label = blob[0x47:0x52]
    info.update(
        {
            "fat32": True,
            "spc": spc,
            "cluster_bytes": spc * bps,
            "reserved": reserved,
            "nfat": nfat,
            "hidden_sectors": hidden,
            "total_sectors": total,
            "fat_sectors": fat_sectors,
            "root_cluster": rootclus,
            "fsinfo_sector": fsinfo_sec,
            "backup_boot_sector": backup_sec,
            "oem": blob[3:11].decode("ascii", "replace"),
            "volume_label_bpb": label.decode("ascii", "replace").rstrip(),
            "jump": blob[0:3].hex(),
            "drvnum": blob[0x40] if len(blob) > 0x40 else None,
            "bootsig": blob[0x42] if len(blob) > 0x42 else None,
            "sig_510": blob[510:512].hex(),
        }
    )
    if bps > 512 and len(blob) >= bps:
        info["sig_end_of_sector"] = blob[bps - 2 : bps].hex()
        if blob[bps - 2 : bps] != b"\x55\xaa":
            issues.append(
                f"55AA missing at end of {bps}-byte sector (offset {bps - 2}); "
                "fatgen103 only requires it at 510, but 4Kn hosts often look at both"
            )
        else:
            notes.append(f"55AA present at 510 and at {bps - 2}")
    else:
        notes.append("55AA present at 510 (fatgen103 location)")

    if hidden_expect and hidden != hidden_expect:
        issues.append(f"hidden_sectors {hidden} != partition start {hidden_expect}")
    if rootclus != 2:
        issues.append(f"root cluster {rootclus} != 2")
    if nfat != 2:
        issues.append(f"NumFATs {nfat} != 2 (Windows FAT32 uses 2; freemyipod nano2G used 1)")
    if reserved < 32:
        notes.append(f"reserved sectors {reserved} (common FAT32 is 32)")

    span = prefix_span_sectors(
        reserved=reserved, nfat=nfat, fat_sectors=fat_sectors, spc=spc
    )
    need_bytes = span * bps
    info["prefix_sectors_needed"] = span
    info["prefix_bytes_needed"] = need_bytes
    info["covers_reserved_fats_root"] = len(blob) >= need_bytes
    if len(blob) < need_bytes:
        issues.append(
            f"dump {len(blob)} bytes does not cover reserved+both FATs+root "
            f"({span} sectors = {need_bytes} bytes)"
        )

    if volume_bytes:
        ms = microsoft_fat32_cluster_bytes(volume_bytes)
        info["microsoft_cluster_bytes"] = ms
        info["microsoft_spc"] = ms // bps if bps and ms % bps == 0 else None
        if spc * bps != ms:
            issues.append(
                f"sectors_per_cluster={spc} ({spc * bps} bytes/cluster). "
                f"Microsoft FAT32 default for {volume_bytes} bytes is {ms} "
                f"(KB 140365 / fatgen103). An earlier build used 32768 "
                f"(spc=8 at 4096)."
            )
        try:
            want = fat32_prefix_layout(volume_bytes, bytes_per_sector=bps)
            info["want_spc"] = want["spc"]
            info["want_fat_sectors"] = want["fat_sectors"]
            info["want_prefix_bytes"] = want["prefix_bytes"]
        except Refused as e:
            issues.append(f"could not compute desired geometry: {e}")

    # FSInfo
    if fsinfo_sec and (fsinfo_sec + 1) * bps <= len(blob):
        fs = blob[fsinfo_sec * bps : (fsinfo_sec + 1) * bps]
        lead = struct.unpack_from("<I", fs, 0)[0]
        struc = struct.unpack_from("<I", fs, 0x1E4)[0]
        free = struct.unpack_from("<I", fs, 0x1E8)[0]
        nxt = struct.unpack_from("<I", fs, 0x1EC)[0]
        info["fsinfo"] = {
            "lead": f"{lead:#010x}",
            "struc": f"{struc:#010x}",
            "free_clusters": free,
            "next_free": nxt,
            "sig_510": fs[510:512].hex(),
        }
        if lead != FSINFO_LEAD or struc != FSINFO_STRUC:
            issues.append(
                f"FSInfo signatures {lead:#x}/{struc:#x} != "
                f"{FSINFO_LEAD:#x}/{FSINFO_STRUC:#x}"
            )
        if fs[510:512] != b"\x55\xaa":
            issues.append("FSInfo missing 55AA at 510")
    else:
        issues.append("FSInfo sector not in dump")

    fat1_off = reserved * bps
    fat2_off = fat1_off + fat_sectors * bps
    root_off = fat2_off + fat_sectors * bps
    info["fat1_byte"] = fat1_off
    info["fat2_byte"] = fat2_off
    info["root_byte"] = root_off

    def _fat_head(off: int, name: str) -> Optional[Tuple[int, int, int]]:
        if off + 12 > len(blob):
            issues.append(f"{name} head not in dump (offset {off})")
            return None
        a, b, c = struct.unpack_from("<III", blob, off)
        info[name] = {"fat0": f"{a:#010x}", "fat1": f"{b:#010x}", "clus2": f"{c:#010x}"}
        if a != FAT32_MEDIA:
            issues.append(f"{name}[0]={a:#x} != media {FAT32_MEDIA:#x}")
        if b not in (FAT32_FAT1_WINDOWS, FAT32_EOC):
            issues.append(
                f"{name}[1]={b:#x} (Windows/freemyipod use {FAT32_FAT1_WINDOWS:#x}; "
                f"28-bit EOC is {FAT32_EOC:#x})"
            )
        elif b == FAT32_EOC:
            notes.append(
                f"{name}[1]={b:#x} is 28-bit EOC; Windows format.exe writes "
                f"{FAT32_FAT1_WINDOWS:#x}"
            )
        if c < 0x0FFFFFF8:
            issues.append(f"{name} cluster 2 (root) {c:#x} is not EOC (>= 0x0ffffff8)")
        return a, b, c

    h1 = _fat_head(fat1_off, "fat1")
    h2 = _fat_head(fat2_off, "fat2")
    if h1 and h2 and h1 != h2:
        issues.append("FAT1 and FAT2 heads differ")

    if root_off + 32 <= len(blob):
        root_ent = blob[root_off : root_off + 32]
        rlabel = root_ent[0:11].decode("ascii", "replace")
        rattr = root_ent[11]
        info["root_label"] = rlabel.rstrip()
        info["root_attr"] = rattr
        if rattr == 0x08:
            notes.append(f"root directory volume label {rlabel!r} attr=0x08")
        else:
            issues.append(f"root[0] attr {rattr:#x} is not volume label 0x08")
        if rlabel.rstrip() != "IPOD":
            notes.append(
                f"volume label {rlabel!r} (a working n7g Winpod used IPOD; "
                "RetailOS does not require iPod_Control to boot)"
            )
        rest_off = root_off + 32
        rest_end = min(len(blob), root_off + spc * bps)
        rest = blob[rest_off:rest_end]
        info["root_rest_zero"] = rest == b"\x00" * len(rest)
        info["root_bytes_seen"] = rest_end - root_off
        if rest and not info["root_rest_zero"]:
            issues.append("root cluster is not zero after the volume-label entry")
        elif rest_end - root_off < spc * bps:
            notes.append(
                f"root cluster dump truncated ({rest_end - root_off} of {spc * bps} bytes)"
            )
    elif info["covers_reserved_fats_root"] is False:
        notes.append("root cluster not in this dump")

    notes.append(
        "iPod_Control / SysInfo is written by iTunes after a restore, not "
        "required to leave the Apple logo. SysCfg lives in NAND, not FAT."
    )
    return info


def format_analyze_report(info: Dict) -> str:
    """Human-readable dump.txt body."""
    lines: List[str] = ["FAT32 prefix analysis (read-only)", ""]
    if not info.get("fat32"):
        lines.append("Not a FAT32 boot sector.")
        for i in info.get("issues") or []:
            lines.append(f"  ISSUE: {i}")
        if info.get("boot_hex"):
            lines.append(f"  first 16 bytes: {info['boot_hex']}")
        return "\n".join(lines) + "\n"
    lines.append(f"bytes_per_sector     {info.get('bytes_per_sector')}")
    lines.append(f"sectors_per_cluster  {info.get('spc')} ({info.get('cluster_bytes')} bytes)")
    lines.append(f"reserved             {info.get('reserved')}")
    lines.append(f"NumFATs              {info.get('nfat')}")
    lines.append(f"FATSz32              {info.get('fat_sectors')}")
    lines.append(f"TotSec32             {info.get('total_sectors')}")
    lines.append(f"hidden_sectors       {info.get('hidden_sectors')}")
    lines.append(f"root_cluster         {info.get('root_cluster')}")
    lines.append(f"FSInfo sector        {info.get('fsinfo_sector')}")
    lines.append(f"backup boot          {info.get('backup_boot_sector')}")
    lines.append(f"OEM                  {info.get('oem')!r}")
    lines.append(f"BPB label            {info.get('volume_label_bpb')!r}")
    lines.append(f"jump                 {info.get('jump')}")
    lines.append(f"55AA at 510          {info.get('sig_510')}")
    if "sig_end_of_sector" in info:
        lines.append(f"55AA at end          {info.get('sig_end_of_sector')}")
    lines.append(
        f"prefix needed        {info.get('prefix_sectors_needed')} sectors "
        f"({info.get('prefix_bytes_needed')} bytes); "
        f"covers_reserved_fats_root={info.get('covers_reserved_fats_root')}"
    )
    if info.get("microsoft_cluster_bytes"):
        lines.append(
            f"Microsoft default    {info['microsoft_cluster_bytes']} bytes/cluster "
            f"for this volume (KB 140365 / fatgen103)"
        )
    fs = info.get("fsinfo") or {}
    if fs:
        lines.append(
            f"FSInfo               lead={fs.get('lead')} struc={fs.get('struc')} "
            f"free={fs.get('free_clusters')} next={fs.get('next_free')} "
            f"sig={fs.get('sig_510')}"
        )
    for name in ("fat1", "fat2"):
        h = info.get(name)
        if h:
            lines.append(
                f"{name:20} [0]={h['fat0']} [1]={h['fat1']} clus2={h['clus2']}"
            )
    if "root_label" in info:
        lines.append(
            f"root dir             label={info.get('root_label')!r} "
            f"attr={info.get('root_attr'):#x} rest_zero={info.get('root_rest_zero')}"
        )
    lines.append("")
    if info.get("issues"):
        lines.append("Issues:")
        for i in info["issues"]:
            lines.append(f"  - {i}")
    else:
        lines.append("Issues: none")
    if info.get("notes"):
        lines.append("Notes:")
        for n in info["notes"]:
            lines.append(f"  - {n}")
    return "\n".join(lines) + "\n"


def _label11(label: str) -> bytes:
    raw = (label or "IPOD").encode("ascii", "replace").upper()
    raw = raw.replace(b"\x00", b" ")[:11]
    return raw.ljust(11, b" ")


def _pad_sector(head: bytes, bytes_per_sector: int) -> bytes:
    if len(head) > bytes_per_sector:
        raise Refused(
            f"FAT sector head {len(head)} is larger than sector {bytes_per_sector}"
        )
    if len(head) == bytes_per_sector:
        return head
    return head + b"\x00" * (bytes_per_sector - len(head))


def _with_end_signature(sector: bytes, bytes_per_sector: int) -> bytes:
    """55AA at 510 (spec) and at the last two bytes of a 4K sector (4Kn hosts)."""
    out = bytearray(_pad_sector(sector, bytes_per_sector))
    if len(out) < 512:
        raise Refused("FAT sector shorter than 512")
    out[510] = 0x55
    out[511] = 0xAA
    if bytes_per_sector > 512:
        out[-2] = 0x55
        out[-1] = 0xAA
    return bytes(out)


def _boot_sector(
    *,
    total: int,
    spc: int,
    reserved: int,
    fat_sectors: int,
    hidden: int,
    vol: bytes,
    bytes_per_sector: int = SECTOR,
) -> bytes:
    s = bytearray(512)
    s[0:3] = b"\xeb\x58\x90"
    s[3:11] = b"MSDOS5.0"
    struct.pack_into("<H", s, 0x0B, bytes_per_sector)
    s[0x0D] = spc
    struct.pack_into("<H", s, 0x0E, reserved)
    s[0x10] = 2
    struct.pack_into("<H", s, 0x11, 0)  # root entries (FAT32)
    struct.pack_into("<H", s, 0x13, 0)  # total 16
    s[0x15] = 0xF8
    struct.pack_into("<H", s, 0x16, 0)  # FAT size 16
    struct.pack_into("<H", s, 0x18, 63)
    struct.pack_into("<H", s, 0x1A, 255)
    struct.pack_into("<I", s, 0x1C, hidden)
    struct.pack_into("<I", s, 0x20, total)
    struct.pack_into("<I", s, 0x24, fat_sectors)
    struct.pack_into("<H", s, 0x28, 0)  # ext flags
    struct.pack_into("<H", s, 0x2A, 0)  # FS version
    struct.pack_into("<I", s, 0x2C, 2)  # root cluster
    struct.pack_into("<H", s, 0x30, 1)  # FSInfo
    struct.pack_into("<H", s, 0x32, 6)  # backup boot
    s[0x40] = 0x80
    s[0x42] = 0x29
    struct.pack_into("<I", s, 0x43, 0x4E414E37)  # volume id 'NAN7'
    s[0x47:0x52] = vol
    s[0x52:0x5A] = b"FAT32   "
    s[0x1FE] = 0x55
    s[0x1FF] = 0xAA
    return _with_end_signature(bytes(s), bytes_per_sector)


def _fsinfo_sector(
    free_clusters: int, next_free: int, *, bytes_per_sector: int = SECTOR
) -> bytes:
    s = bytearray(512)
    struct.pack_into("<I", s, 0, FSINFO_LEAD)
    struct.pack_into("<I", s, 0x1E4, FSINFO_STRUC)
    struct.pack_into("<I", s, 0x1E8, free_clusters & 0xFFFFFFFF)
    struct.pack_into("<I", s, 0x1EC, next_free & 0xFFFFFFFF)
    s[0x1FE] = 0x55
    s[0x1FF] = 0xAA
    return _with_end_signature(bytes(s), bytes_per_sector)
