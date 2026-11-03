"""Standard SCSI BOT (READ/WRITE CAPACITY) for recovery diskmode.

This is not C6 vendor peek/write/exec (host/scsi.py). After wInd3x
`restore --full` / `--prepare-only`, the nano stays at 05ac:1267. On macOS
the kernel mass-storage driver does not reattach (MKN52ZP 2026-10-28:
AppleUSBHostUserClient exclusive access). We format the data slice with
WRITE(10) of a FAT32 prefix. Firmware / type-0x00 LBAs are never written.

WRITE(10) is the same BOT family as wInd3x ReadBlocks used for --backup.
LBA arguments are SCSI device blocks from READ CAPACITY, not 512-byte
guesses. The MBR LBA unit is resolved against capacity (4096 on MKN52ZP).

READ CAPACITY(16) is sent only when RC10 last_lba is 0xFFFFFFFF. On MKN52
(last_lba=3859038) RC16 stalls bulk-IN; the next CBW is then cancelled
unless CLEAR_FEATURE(ENDPOINT_HALT) / Bulk-Only Reset 0xFF ran. Format-only
keeps one usbscsi session for inspect + snapshot + WRITE.
"""

from __future__ import annotations

import os
import re
import struct
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional, Protocol

from .compat import Refused, run, sha256_bytes
from .fat32fs import (
    FAT32_EOC,
    FAT32_FAT1_WINDOWS,
    FAT32_MEDIA,
    NANO7_FAT_SECTOR,
    analyze_fat32_prefix,
    build_fat32_prefix,
    fat32_bytes_per_sector,
    fat32_prefix_layout,
    format_analyze_report,
    is_fat32_boot,
)
from .layout import DiskLayout, MAX_PREFIX, inspect_bytes, pick_format_partition
from .safety import require_usable_disk_size
from .usb import build_usbscsi

# First few MiB of the data slice plus targeted FAT/root reads. 8 MiB covers
# the live test 4,030,464-byte prefix entirely. Extra READ(10)s pull FAT2
# and the root cluster when the BPB says they sit past this window.
DUMP_DATA_BYTES = 8 * 1024 * 1024


@dataclass
class Capacity:
    last_lba: int
    block_size: int
    last_lba_16: Optional[int] = None
    block_size_16: Optional[int] = None
    rc16_error: str = ""
    rc16_skipped: bool = False
    raw_text: str = ""

    @property
    def size_bytes(self) -> int:
        return (self.last_lba + 1) * self.block_size


class BotClient(Protocol):
    def capacity(self) -> Capacity: ...

    def read10(self, lba: int, count: int) -> bytes: ...

    def write10(self, lba: int, data: bytes) -> None: ...

    def reset(self) -> None: ...

    def synccache(self) -> None: ...


def _usbscsi() -> Path:
    binp = build_usbscsi()
    if not binp.is_file():
        raise Refused("usbscsi binary missing; run ./start deps")
    return binp


def parse_capacity_text(text: str) -> Capacity:
    """Parse usbscsi capacity stdout (rc10 required, rc16 optional)."""
    m10 = re.search(
        r"read_capacity_10 last_lba=(\d+)\s+block_size=(\d+)\s+bytes=(\d+)", text
    )
    if not m10:
        m10 = re.search(r"last_lba=(\d+)\s+block_size=(\d+)", text)
        if not m10:
            raise Refused("usbscsi capacity output not parseable:\n" + text)
        last, bs = int(m10.group(1)), int(m10.group(2))
        cap = Capacity(last_lba=last, block_size=bs, raw_text=text)
    else:
        last, bs = int(m10.group(1)), int(m10.group(2))
        cap = Capacity(last_lba=last, block_size=bs, raw_text=text)
        reported = int(m10.group(3))
        if reported != cap.size_bytes:
            raise Refused(
                f"READ CAPACITY(10) bytes {reported} != (last_lba+1)*block_size "
                f"{cap.size_bytes}"
            )
    m16 = re.search(
        r"read_capacity_16 last_lba=(\d+)\s+block_size=(\d+)\s+bytes=(\d+)", text
    )
    err16 = re.search(r"read_capacity_16 error=(.*)", text)
    skip16 = re.search(r"read_capacity_16 skipped", text)
    if m16:
        cap.last_lba_16 = int(m16.group(1))
        cap.block_size_16 = int(m16.group(2))
    elif skip16:
        cap.rc16_skipped = True
    elif err16:
        cap.rc16_error = err16.group(1).strip()
    if cap.block_size < 512 or cap.block_size > 4096 or cap.last_lba < 1:
        raise Refused(
            f"implausible SCSI capacity last_lba={cap.last_lba} "
            f"block_size={cap.block_size}"
        )
    require_usable_disk_size(cap.size_bytes, what="READ CAPACITY")
    return cap


def describe_capacity(cap: Capacity) -> str:
    lines = [
        f"READ CAPACITY(10) last_lba={cap.last_lba} block_size={cap.block_size} "
        f"bytes={cap.size_bytes}"
    ]
    if cap.last_lba_16 is not None and cap.block_size_16:
        n = cap.last_lba_16 + 1
        lines.append(
            f"READ CAPACITY(16) last_lba={cap.last_lba_16} "
            f"block_size={cap.block_size_16} bytes={n * cap.block_size_16}"
        )
    elif cap.rc16_skipped:
        lines.append(
            "READ CAPACITY(16) skipped (RC10 last_lba fits in 32 bits; "
            "RC16 stalls bulk-IN on this nano)"
        )
    elif cap.rc16_error:
        lines.append(f"READ CAPACITY(16) error: {cap.rc16_error}")
    else:
        lines.append("READ CAPACITY(16) not reported")
    return "\n".join(lines)


def close_bot(client: object) -> None:
    closer = getattr(client, "close", None)
    if closer is not None:
        closer()


@contextmanager
def bot_session(bot: Optional[BotClient] = None) -> Iterator[BotClient]:
    """One BOT claim for inspect + snapshot + WRITE. Lazy: no USB until a command."""
    own = bot is None
    client: BotClient = bot if bot is not None else UsbScsiBot()
    try:
        yield client
    finally:
        if own:
            close_bot(client)


class UsbScsiBot:
    """Live 05ac:1267 via host/cmd/usbscsi session (one USB claim)."""

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None

    def _ensure(self) -> subprocess.Popen:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        binp = _usbscsi()
        self._proc = subprocess.Popen(
            [str(binp), "session"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            bufsize=0,
        )
        return self._proc

    def _transact(self, cmd: str) -> list:
        proc = self._ensure()
        if proc.stdin is None or proc.stdout is None:
            raise Refused("usbscsi session pipes missing")
        try:
            proc.stdin.write((cmd + "\n").encode("utf-8"))
            proc.stdin.flush()
        except BrokenPipeError as e:
            raise Refused(f"usbscsi session stdin closed: {e}") from e
        lines = []
        while True:
            raw = proc.stdout.readline()
            if not raw:
                code = proc.poll()
                raise Refused(
                    f"usbscsi session closed while waiting for OK (exit {code})"
                )
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line == "OK":
                return lines
            if line.startswith("ERR "):
                raise Refused(line[4:])
            lines.append(line)

    def capacity(self) -> Capacity:
        lines = self._transact("capacity")
        return parse_capacity_text("\n".join(lines) + "\n")

    def read10(self, lba: int, count: int) -> bytes:
        fd, name = tempfile.mkstemp(prefix="nano7-bot-read-")
        os.close(fd)
        path = Path(name)
        try:
            self._transact(f"read10 {int(lba)} {int(count)} {path}")
            return path.read_bytes()
        except Refused:
            raise
        finally:
            path.unlink(missing_ok=True)

    def write10(self, lba: int, data: bytes) -> None:
        fd, name = tempfile.mkstemp(prefix="nano7-bot-write-")
        try:
            os.write(fd, data)
            os.close(fd)
            fd = -1
            self._transact(f"write10 {int(lba)} {name}")
        finally:
            if fd >= 0:
                os.close(fd)
            Path(name).unlink(missing_ok=True)

    def synccache(self) -> None:
        self._transact("synccache")

    def clear_halts(self) -> None:
        self._transact("clearhalt")

    def bot_reset(self) -> None:
        """Bulk-Only Mass Storage Reset (0xFF) + clear halts. Not libusb_reset_device."""
        self._transact("botreset")

    def reset(self) -> None:
        """libusb_reset_device. Last resort; may drop recovery diskmode."""
        r = run([str(_usbscsi()), "reset"], check=False, capture=True)
        if r.returncode != 0:
            raise Refused("usbscsi reset failed: " + str(r.stderr or r.stdout or ""))

    def close(self) -> None:
        proc = self._proc
        if proc is None:
            return
        self._proc = None
        try:
            if proc.poll() is None and proc.stdin is not None:
                proc.stdin.write(b"quit\n")
                proc.stdin.flush()
                proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except OSError:
                pass
        finally:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except OSError:
                pass
            try:
                if proc.stdout:
                    proc.stdout.close()
            except OSError:
                pass

    def __enter__(self) -> "UsbScsiBot":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def inspect_via_bot(bot: Optional[BotClient] = None) -> DiskLayout:
    own = bot is None
    client = bot or UsbScsiBot()
    try:
        cap = client.capacity()
        require_usable_disk_size(cap.size_bytes, what="READ CAPACITY")
        print(describe_capacity(cap))
        n = min(max(1, MAX_PREFIX // cap.block_size), cap.last_lba + 1)
        raw = client.read10(0, n)
        if len(raw) < 512:
            raise Refused(f"READ(10) of LBA 0 returned {len(raw)} bytes")
        return inspect_bytes(raw, cap.size_bytes, scsi_block_size=cap.block_size)
    finally:
        if own:
            close_bot(client)


def probe_read_lba0(client: BotClient) -> bytes:
    """Test READ(10) of LBA 0. Used after a failed snapshot before any WRITE."""
    data = client.read10(0, 1)
    if len(data) < 512:
        raise Refused(f"READ(10) LBA 0 returned {len(data)} bytes")
    return data


def print_format_plan(plan: dict) -> None:
    print(
        f"    would WRITE(10) FAT32 prefix:\n"
        f"      byte_offset={plan['byte_offset']} byte_length={plan['byte_length']}\n"
        f"      scsi_lba={plan['scsi_lba']} scsi_blocks={plan['scsi_blocks']} "
        f"scsi_block_size={plan['scsi_block_size']}\n"
        f"      fat_bytes_per_sector={plan['fat_bytes_per_sector']} "
        f"sectors_per_cluster={plan['sectors_per_cluster']} "
        f"cluster_bytes={plan['cluster_bytes']}\n"
        f"      hidden_sectors={plan['hidden_sectors']} "
        f"partition start_lba={plan['start_lba']} size_lba={plan['size_lba']} "
        f"({plan['partition_bytes']} bytes)"
    )


def format_data_slice_via_bot(
    *,
    bot: Optional[BotClient] = None,
    write: bool = True,
    label: str = "IPOD",
    plan: Optional[dict] = None,
) -> dict:
    """FAT32-format the data slice over WRITE(10). Never touches firmware LBAs.

    Pass the same bot for inspect, snapshot, and WRITE so there is one USB
    claim. Reuse plan= to skip a second READ CAPACITY after the gate.
    """
    own = bot is None
    client = bot or UsbScsiBot()
    try:
        if plan is None:
            plan = _inspect_format_plan(client, label=label)
        if not write:
            return plan
        return _execute_format_write(client, plan, label=label)
    finally:
        if own:
            close_bot(client)


def _inspect_format_plan(client: BotClient, *, label: str) -> dict:
    cap = client.capacity()
    require_usable_disk_size(cap.size_bytes, what="READ CAPACITY")
    print(describe_capacity(cap))
    n = min(max(1, MAX_PREFIX // cap.block_size), cap.last_lba + 1)
    raw = client.read10(0, n)
    layout = inspect_bytes(raw, cap.size_bytes, scsi_block_size=cap.block_size)
    print(layout.summary())
    if layout.scheme == "apm":
        raise Refused(
            "SCSI LBA 0 is still an Apple Partition Map. After IPodRepartition "
            "the table should be a DOS MBR. Refusing to FAT-format an HFS "
            "volume in place. Keep the backup. docs/RECOVERY.md\n"
            + layout.summary()
        )
    if layout.scheme != "mbr":
        raise Refused(
            "SCSI LBA 0 is not a DOS MBR. Refusing to guess a FAT32 slice.\n"
            + layout.summary()
        )
    if not layout.sector_resolved:
        raise Refused(
            "MBR sector size did not uniquely fill SCSI capacity. "
            "The 2026-10-28 MKN52ZP table is 4K (64 / 3858975 on "
            "15806623744 bytes). A 512-byte reading would WRITE at 32 KiB, "
            "inside the MBR gap. This is a refuse, not a successful dry-run.\n"
            + layout.summary()
        )
    if "unknown (fail closed" in layout.summary():
        raise Refused(
            "partition identity is unknown (fail closed). Will not WRITE(10).\n"
            + layout.summary()
        )
    target = pick_format_partition(layout)
    if target.scheme == "mbr" and target.type_id == 0x00:
        raise Refused(
            "refusing to WRITE(10) a type-0x00 slice (firmware on a Winpod).\n"
            + layout.summary()
        )
    fw = layout.firmware
    t0, t1 = target.start_bytes, target.start_bytes + target.size_bytes
    if fw is not None:
        f0, f1 = fw.start_bytes, fw.start_bytes + fw.size_bytes
        if t0 < f1 and f0 < t1:
            raise Refused(
                "data slice overlaps the firmware slice; refusing WRITE(10).\n"
                + layout.summary()
            )
    if target.size_bytes < 1024 * 1024:
        raise Refused(f"data slice {target.size_bytes} bytes is too small for FAT32")
    if t1 > cap.size_bytes:
        raise Refused(
            f"data slice end {t1} is past SCSI capacity {cap.size_bytes}"
        )
    fat_sector = layout.sector_size
    if cap.size_bytes >= 8 * 1024 * 1024 * 1024 and fat_sector != NANO7_FAT_SECTOR:
        raise Refused(
            f"nano 7G-class disk ({cap.size_bytes} bytes) must use 4096-byte "
            f"FAT sectors (wInd3x IPodRepartition 4K units; Windows iTunes 4Kn "
            f"Winpod). Resolved MBR sector is {fat_sector}. Refusing."
        )
    if t0 % cap.block_size:
        raise Refused(
            f"data slice starts at byte {t0}, not aligned to SCSI block "
            f"{cap.block_size}"
        )
    if t0 % fat_sector:
        raise Refused(
            f"data slice starts at byte {t0}, not aligned to FAT sector {fat_sector}"
        )
    scsi_lba = t0 // cap.block_size
    hidden = t0 // fat_sector
    prefix = build_fat32_prefix(
        target.size_bytes,
        label=label,
        hidden_sectors=hidden,
        bytes_per_sector=fat_sector,
    )
    if len(prefix) % cap.block_size:
        pad = cap.block_size - (len(prefix) % cap.block_size)
        prefix = prefix + b"\x00" * pad
    prefix_end = t0 + len(prefix)
    if fw is not None:
        f0, f1 = fw.start_bytes, fw.start_bytes + fw.size_bytes
        if t0 < f1 and f0 < prefix_end:
            raise Refused("FAT32 prefix would overlap the firmware slice")
    if prefix_end > t1:
        raise Refused("FAT32 prefix is larger than the data slice")
    if scsi_lba < 1:
        raise Refused(
            f"WRITE(10) LBA {scsi_lba} would hit LBA 0 (the MBR). Refusing."
        )

    geo = fat32_prefix_layout(target.size_bytes, bytes_per_sector=fat_sector)
    scsi_blocks = len(prefix) // cap.block_size
    plan = {
        "method": "scsi",
        "wrote": False,
        "scsi_lba": scsi_lba,
        "scsi_blocks": scsi_blocks,
        "scsi_block_size": cap.block_size,
        "byte_offset": t0,
        "byte_length": len(prefix),
        "prefix_bytes": len(prefix),
        "fat_bytes_per_sector": fat_sector,
        "sectors_per_cluster": geo["spc"],
        "cluster_bytes": geo["cluster_bytes"],
        "hidden_sectors": hidden,
        "start_lba": target.start_lba,
        "size_lba": target.size_lba,
        "partition_bytes": target.size_bytes,
        "layout": layout,
        "total_fat_sectors": geo["total_sectors"],
        "reserved": geo["reserved"],
        "fat_sectors": geo["fat_sectors"],
        "prefix_covers_root": True,
        "synccache": False,
        "prefix": prefix,
        "geo": geo,
        "capacity": cap,
    }
    print_format_plan(plan)
    print(
        f"      reserved={geo['reserved']} fat_sectors={geo['fat_sectors']} "
        f"prefix_sectors={geo['prefix_sectors']} "
        f"(reserved + 2*FAT + root cluster)"
    )
    return plan


def _execute_format_write(client: BotClient, plan: dict, *, label: str) -> dict:
    _ = label
    layout = plan["layout"]
    cap = plan.get("capacity")
    prefix = plan.get("prefix")
    geo = plan.get("geo")
    if prefix is None or cap is None or geo is None:
        raise Refused("format plan is missing prefix/capacity; inspect first")
    scsi_lba = plan["scsi_lba"]
    fat_sector = plan["fat_bytes_per_sector"]
    fw = layout.firmware if layout is not None else None

    fw_sample = b""
    fw_lba = 0
    if fw is not None and fw.size_bytes >= cap.block_size:
        fw_lba = fw.start_bytes // cap.block_size
        fw_sample = client.read10(fw_lba, 1)

    mbr_before = client.read10(0, 1)

    client.write10(scsi_lba, prefix)
    synced, sync_err = _synccache(client)
    plan["synccache"] = synced
    plan["synccache_error"] = sync_err
    if synced:
        print("    SYNCHRONIZE CACHE(10) accepted")
    else:
        print(
            "    SYNCHRONIZE CACHE(10) not accepted: "
            f"{sync_err or 'client has no synccache'}. "
            "READ-back may be from device cache. Do not unplug. "
            "docs/RECOVERY.md"
        )

    _verify_fat32_write(
        client,
        cap=cap,
        scsi_lba=scsi_lba,
        prefix=prefix,
        fat_sector=fat_sector,
        geo=geo,
    )
    mbr_after = client.read10(0, 1)
    if mbr_after != mbr_before:
        raise Refused(
            "LBA 0 (MBR) changed during BOT FAT32 write. Stop. Keep the backup."
        )
    if fw_sample:
        after = client.read10(fw_lba, 1)
        if after != fw_sample:
            raise Refused(
                "firmware slice changed during BOT FAT32 write. Stop. "
                "Keep the backup. docs/RECOVERY.md"
            )
    print(
        "    BOT FAT32 prefix verified (55AA + FAT32 boot, "
        f"bytes/sector={fat_sector}, spc={geo['spc']}, "
        f"FAT[0]/FAT[1]/root EOC, firmware/MBR unchanged)"
    )
    plan["wrote"] = True
    plan["partition"] = f"scsi:lba{scsi_lba}"
    return plan


def _synccache(client: BotClient) -> tuple:
    fn = getattr(client, "synccache", None)
    if fn is None:
        return False, "synccache not implemented on this client"
    try:
        fn()
        return True, ""
    except Refused as e:
        return False, str(e)


def _verify_fat32_write(
    client: BotClient,
    *,
    cap: Capacity,
    scsi_lba: int,
    prefix: bytes,
    fat_sector: int,
    geo: dict,
) -> None:
    need = max(1, fat_sector // cap.block_size)
    check = client.read10(scsi_lba, need)
    boot = check[:512]
    if not is_fat32_boot(boot, bytes_per_sector=fat_sector):
        raise Refused(
            "WRITE(10) of FAT32 prefix did not verify (no 55AA/FAT32 boot or "
            f"bytes/sector != {fat_sector}). Keep the backup. Device should "
            "still be in recovery diskmode. Retry ./start fat32 --run "
            "--format-only. Do not unplug. docs/RECOVERY.md"
        )
    if fat32_bytes_per_sector(boot) != fat_sector:
        raise Refused(
            f"FAT BPB bytes/sector {fat32_bytes_per_sector(boot)} != {fat_sector}"
        )
    if fat_sector > 512 and len(check) >= fat_sector:
        if check[fat_sector - 2 : fat_sector] != b"\x55\xaa":
            raise Refused(
                f"WRITE(10) verify: 55AA missing at end of {fat_sector}-byte "
                "boot sector. Do not unplug. docs/RECOVERY.md"
            )
    fat1_byte = geo["fat1_byte"]
    fat1_lba = scsi_lba + fat1_byte // cap.block_size
    fat_head = client.read10(fat1_lba, 1)
    off = fat1_byte % cap.block_size
    if off + 12 > len(fat_head):
        raise Refused("WRITE(10) verify: could not read FAT[0..2]")
    a, b, c = struct.unpack_from("<III", fat_head, off)
    if a != FAT32_MEDIA or b != FAT32_FAT1_WINDOWS or c != FAT32_EOC:
        raise Refused(
            f"WRITE(10) verify: FAT[0]={a:#x} FAT[1]={b:#x} clus2={c:#x} "
            f"(want {FAT32_MEDIA:#x} / {FAT32_FAT1_WINDOWS:#x} / {FAT32_EOC:#x}). "
            "Do not unplug. docs/RECOVERY.md"
        )


def dump_data_slice_via_bot(
    dest: Path,
    *,
    bot: Optional[BotClient] = None,
    data_bytes: int = DUMP_DATA_BYTES,
) -> dict:
    """Read-only BOT dump of MBR + data-slice prefix. Never WRITE(10)."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for name in (
        "dump-mbr.bin",
        "dump-data-prefix.bin",
        "dump-fat1-head.bin",
        "dump-fat2-head.bin",
        "dump-root.bin",
        "dump.txt",
    ):
        p = dest / name
        if p.exists():
            raise Refused(
                f"{p} already exists. Pick an empty directory; refusing to "
                "overwrite a dump."
            )

    own = bot is None
    client = bot or UsbScsiBot()
    try:
        return _dump_data_slice_via_bot(dest, client=client, data_bytes=data_bytes)
    finally:
        if own:
            close_bot(client)


def _dump_data_slice_via_bot(dest: Path, *, client: BotClient, data_bytes: int) -> dict:
    cap = client.capacity()
    require_usable_disk_size(cap.size_bytes, what="READ CAPACITY")
    print(describe_capacity(cap))
    mbr_blocks = max(1, 4096 // cap.block_size)
    mbr = client.read10(0, mbr_blocks)
    (dest / "dump-mbr.bin").write_bytes(mbr)
    print(f"    wrote {dest / 'dump-mbr.bin'} ({len(mbr)} bytes)")

    layout = inspect_bytes(mbr, cap.size_bytes, scsi_block_size=cap.block_size)
    print(layout.summary())
    target = None
    data_lba = 0
    data_note = "could not resolve data slice; dumping from SCSI LBA 0"
    try:
        if layout.scheme == "mbr" and layout.sector_resolved:
            target = pick_format_partition(layout)
            if target.start_bytes % cap.block_size:
                raise Refused(
                    f"data slice byte {target.start_bytes} is not SCSI-aligned"
                )
            data_lba = target.start_bytes // cap.block_size
            data_note = (
                f"data slice start_lba={target.start_lba} "
                f"start_byte={target.start_bytes} scsi_lba={data_lba}"
            )
    except Refused as e:
        data_note = f"data slice not resolved ({e}); dumping from SCSI LBA 0"
        target = None
        data_lba = 0

    want = min(max(cap.block_size, int(data_bytes)), DUMP_DATA_BYTES * 2)
    nblocks = max(1, want // cap.block_size)
    nblocks = min(nblocks, cap.last_lba + 1 - data_lba)
    if nblocks < 1:
        raise Refused("no SCSI blocks left to dump")
    prefix = client.read10(data_lba, nblocks)
    (dest / "dump-data-prefix.bin").write_bytes(prefix)
    print(
        f"    wrote {dest / 'dump-data-prefix.bin'} ({len(prefix)} bytes) "
        f"scsi_lba={data_lba} ({data_note})"
    )

    extra = _dump_fat_heads_and_root(
        client, cap, data_lba, prefix, dest, volume_bytes=(target.size_bytes if target else 0)
    )
    hidden = 0
    if target is not None and layout.sector_size:
        hidden = target.start_bytes // layout.sector_size
    analysis = analyze_fat32_prefix(
        extra.get("combined") or prefix,
        volume_bytes=target.size_bytes if target else 0,
        hidden_expect=hidden,
        bytes_per_sector=layout.sector_size if layout.sector_resolved else 0,
    )
    report = []
    report.append("nano7kit SCSI BOT dump (read-only; no WRITE)")
    report.append(describe_capacity(cap))
    report.append("")
    report.append(layout.summary())
    report.append("")
    report.append(data_note)
    report.append(f"dump-mbr.bin          {len(mbr)} bytes sha256 {sha256_bytes(mbr)}")
    report.append(
        f"dump-data-prefix.bin  {len(prefix)} bytes sha256 {sha256_bytes(prefix)}"
    )
    for key, label in (
        ("fat1", "dump-fat1-head.bin"),
        ("fat2", "dump-fat2-head.bin"),
        ("root", "dump-root.bin"),
    ):
        blob = extra.get(key)
        if blob:
            report.append(f"{label:22} {len(blob)} bytes sha256 {sha256_bytes(blob)}")
    report.append("")
    report.append(format_analyze_report(analysis))
    report.append("")
    report.append("RetailOS boot notes (this dump, not a firmware rewrite):")
    report.append(
        "- Do not run restore --full again. Apple logo after unplug means OSOS "
        "loaded from NAND; bootloader + firmware + Finalizing already ran."
    )
    report.append(
        "- Do not pass --replace. Do not rewrite the MBR. Type 0x0c + hidden "
        "sectors matching the partition start (64 on MKN52ZP) are the "
        "IPodRepartition table."
    )
    report.append(
        "- SysCfg is NAND, not this FAT. iPod_Control is not required to leave "
        "the Apple logo."
    )
    report.append("SCSI_DUMP_OK")
    text = "\n".join(report) + "\n"
    (dest / "dump.txt").write_text(text)
    print(f"    wrote {dest / 'dump.txt'}")
    print(format_analyze_report(analysis).rstrip())
    print("SCSI_DUMP_OK")
    return {
        "method": "scsi-dump",
        "wrote": False,
        "dest": str(dest),
        "scsi_lba": data_lba,
        "byte_length": len(prefix),
        "analysis": analysis,
        "layout": layout,
    }


def _dump_fat_heads_and_root(
    client: BotClient,
    cap: Capacity,
    data_lba: int,
    prefix: bytes,
    dest: Path,
    *,
    volume_bytes: int,
) -> dict:
    """If a FAT32 BPB is present, READ FAT heads and the root cluster."""
    out: dict = {}
    if not is_fat32_boot(prefix[:512] if len(prefix) >= 512 else prefix):
        return out
    bps = fat32_bytes_per_sector(prefix)
    if bps not in (512, 1024, 2048, 4096):
        return out
    reserved = struct.unpack_from("<H", prefix, 0x0E)[0]
    nfat = prefix[0x10]
    fat_sectors = struct.unpack_from("<I", prefix, 0x24)[0]
    spc = prefix[0x0D]
    if reserved < 1 or nfat < 1 or fat_sectors < 1 or spc < 1:
        return out
    fat1_byte = reserved * bps
    fat2_byte = fat1_byte + fat_sectors * bps
    root_byte = fat2_byte + fat_sectors * bps
    combined = bytearray(max(len(prefix), root_byte + spc * bps))
    combined[: len(prefix)] = prefix

    def take(byte_off: int, length: int, name: str) -> bytes:
        end = byte_off + length
        if end <= len(prefix) and byte_off >= 0:
            blob = prefix[byte_off:end]
        else:
            scsi_off = data_lba * cap.block_size + byte_off
            if scsi_off % cap.block_size:
                # Read the containing block(s).
                lba = scsi_off // cap.block_size
                skip = scsi_off % cap.block_size
                n = (skip + length + cap.block_size - 1) // cap.block_size
                raw = client.read10(lba, n)
                blob = raw[skip : skip + length]
            else:
                n = (length + cap.block_size - 1) // cap.block_size
                raw = client.read10(scsi_off // cap.block_size, n)
                blob = raw[:length]
        (dest / name).write_bytes(blob)
        if byte_off + len(blob) <= len(combined):
            combined[byte_off : byte_off + len(blob)] = blob
        return blob

    try:
        out["fat1"] = take(fat1_byte, bps, "dump-fat1-head.bin")
        if nfat >= 2:
            out["fat2"] = take(fat2_byte, bps, "dump-fat2-head.bin")
        out["root"] = take(root_byte, spc * bps, "dump-root.bin")
    except Refused as e:
        print(f"    extra FAT/root READ(10) skipped: {e}")
    out["combined"] = bytes(combined)
    _ = volume_bytes
    return out


def bot_reset(bot: Optional[BotClient] = None) -> None:
    """libusb_reset_device. May drop recovery diskmode (same class of risk as unplug)."""
    if bot is not None:
        fn = getattr(bot, "reset", None)
        if fn is not None:
            fn()
            return
    r = run([str(_usbscsi()), "reset"], check=False, capture=True)
    if r.returncode != 0:
        raise Refused("usbscsi reset failed: " + str(r.stderr or r.stdout or ""))
