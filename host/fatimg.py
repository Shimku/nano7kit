"""Read (and test-write) FAT32 on a nano 7G backup image.

The tested nano's live FAT32 backup is an MBR disk image with 4096-byte sectors and the
data volume at byte 262144. Darwin hdiutil / mount_msdos will not mount that
map. This module walks the FAT in Python (no kernel mount, no mtools).

Used by ./start library restore-from-backup to copy only the three iTunes
library files. Never writes NAND. Never formats.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .compat import Refused, sha256_bytes
from .fat32fs import NANO7_FAT_SECTOR, is_fat32_boot

ATTR_RO = 0x01
ATTR_HIDDEN = 0x02
ATTR_SYS = 0x04
ATTR_VOL = 0x08
ATTR_DIR = 0x10
ATTR_ARCH = 0x20
ATTR_LFN = 0x0F
FAT32_EOC_MIN = 0x0FFFFFF8
FAT32_BAD = 0x0FFFFFF7
FAT32_MASK = 0x0FFFFFFF
ENTRY = 32
# wInd3x IPodRepartition: FAT starts at LBA 64 on 4096-byte media.
NANO7_FAT_BYTE = 64 * NANO7_FAT_SECTOR  # 262144


def _u16(b: bytes, off: int) -> int:
    return struct.unpack_from("<H", b, off)[0]


def _u32(b: bytes, off: int) -> int:
    return struct.unpack_from("<I", b, off)[0]


def _decode_lfn_chunk(ent: bytes) -> str:
    parts = ent[1:11] + ent[14:26] + ent[28:32]
    chars = []
    for i in range(0, len(parts), 2):
        cp = parts[i] | (parts[i + 1] << 8)
        if cp == 0 or cp == 0xFFFF:
            break
        chars.append(chr(cp))
    return "".join(chars)


def _short_name(ent: bytes) -> str:
    raw = ent[0:11]
    if raw[0] in (0x00, 0xE5):
        return ""
    name = raw[0:8].decode("ascii", "replace").rstrip(" ")
    ext = raw[8:11].decode("ascii", "replace").rstrip(" ")
    if name.startswith("\x05"):
        name = "\xe5" + name[1:]
    return f"{name}.{ext}" if ext else name


def _lfn_checksum(name11: bytes) -> int:
    s = 0
    for b in name11:
        s = ((s & 1) << 7 | (s >> 1)) + b
        s &= 0xFF
    return s


def _encode_lfn_chunk(text: str, seq: int, checksum: int, last: bool) -> bytes:
    padded = (text + "\x00").ljust(13, "\uffff")
    ucs = padded.encode("utf-16le")
    ent = bytearray(32)
    ent[0] = seq | (0x40 if last else 0)
    ent[1:11] = ucs[0:10]
    ent[11] = ATTR_LFN
    ent[13] = checksum
    ent[14:26] = ucs[10:22]
    ent[26:28] = b"\x00\x00"
    ent[28:32] = ucs[22:26]
    return bytes(ent)


def _dos83(name: str, used: Iterable[str]) -> bytes:
    """8.3 directory name. used is existing 11-byte names in the folder."""
    taken = {u.upper() for u in used}
    stem, _, ext = name.replace("\\", "/").split("/")[-1].partition(".")
    def clean(s: str, n: int) -> str:
        out = []
        for ch in s.upper():
            if ch.isalnum() or ch in ("_", "-", "~"):
                out.append(ch)
            elif ch in " .":
                continue
            else:
                out.append("_")
        return "".join(out)[:n] or "FILE"

    base = clean(stem, 8)
    ex = clean(ext, 3) if ext else ""
    cand = (base.ljust(8) + ex.ljust(3)).encode("ascii")
    if cand.upper() not in taken and len(stem) <= 8 and len(ext) <= 3:
        return cand
    for i in range(1, 999):
        suffix = f"~{i}"
        head = clean(stem, 8 - len(suffix)) + suffix
        cand = (head.ljust(8) + ex.ljust(3)).encode("ascii")
        if cand.upper() not in taken:
            return cand
    raise Refused(f"could not allocate an 8.3 name for {name!r}")


class Fat32Image:
    """FAT32 volume inside a disk image or a partition file."""

    def __init__(self, path: Path, *, offset: int = 0, writable: bool = False):
        self.path = Path(path)
        if not self.path.is_file():
            raise Refused(f"FAT image not found: {self.path}")
        self.offset = int(offset)
        self.writable = writable
        mode = "r+b" if writable else "rb"
        self._fh = open(self.path, mode)
        try:
            self._fh.seek(self.offset)
            boot = self._fh.read(512)
        except OSError as exc:
            self._fh.close()
            raise Refused(f"cannot read FAT boot at offset {self.offset}: {exc}") from exc
        if not is_fat32_boot(boot):
            self._fh.close()
            raise Refused(
                f"{self.path} offset {self.offset} is not a FAT32 boot sector. "
                "Expected 4096-byte-sector MBR map with FAT at byte 262144."
            )
        self.bps = _u16(boot, 0x0B)
        self.spc = boot[0x0D]
        self.reserved = _u16(boot, 0x0E)
        self.nfat = boot[0x10]
        self.fat_sectors = _u32(boot, 0x24)
        self.root_cluster = _u32(boot, 0x2C)
        if self.bps not in (512, 1024, 2048, 4096) or self.spc < 1:
            self._fh.close()
            raise Refused(
                f"unsupported FAT geometry bps={self.bps} spc={self.spc}"
            )
        self.cluster_bytes = self.bps * self.spc
        self.fat_off = self.offset + self.reserved * self.bps
        self.data_off = (
            self.offset
            + (self.reserved + self.nfat * self.fat_sectors) * self.bps
        )

    def close(self) -> None:
        try:
            self._fh.close()
        except OSError:
            pass

    def __enter__(self) -> "Fat32Image":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _read(self, abs_off: int, n: int) -> bytes:
        self._fh.seek(abs_off)
        blob = self._fh.read(n)
        if len(blob) < n:
            raise Refused(f"short FAT read at {abs_off} wanted {n} got {len(blob)}")
        return blob

    def _write(self, abs_off: int, blob: bytes) -> None:
        if not self.writable:
            raise Refused("FAT image opened read-only")
        self._fh.seek(abs_off)
        self._fh.write(blob)

    def cluster_offset(self, cluster: int) -> int:
        if cluster < 2:
            raise Refused(f"FAT cluster {cluster} is reserved")
        return self.data_off + (cluster - 2) * self.cluster_bytes

    def fat_get(self, cluster: int) -> int:
        return _u32(self._read(self.fat_off + cluster * 4, 4), 0) & FAT32_MASK

    def fat_set(self, cluster: int, value: int) -> None:
        raw = struct.pack("<I", value & FAT32_MASK)
        for i in range(self.nfat):
            off = self.fat_off + i * self.fat_sectors * self.bps + cluster * 4
            self._write(off, raw)

    def cluster_chain(self, start: int) -> List[int]:
        out = []
        seen = set()
        c = start
        while 2 <= c < FAT32_BAD:
            if c in seen:
                raise Refused("FAT cluster loop")
            seen.add(c)
            out.append(c)
            if len(out) > 1_000_000:
                raise Refused("FAT chain is implausibly long")
            nxt = self.fat_get(c)
            if nxt >= FAT32_EOC_MIN:
                break
            c = nxt
        return out

    def read_cluster(self, cluster: int) -> bytes:
        return self._read(self.cluster_offset(cluster), self.cluster_bytes)

    def write_cluster(self, cluster: int, data: bytes) -> None:
        blob = data
        if len(blob) < self.cluster_bytes:
            blob = blob + b"\x00" * (self.cluster_bytes - len(blob))
        self._write(self.cluster_offset(cluster), blob[: self.cluster_bytes])

    def alloc_clusters(self, count: int) -> List[int]:
        if count < 1:
            raise Refused("need at least one cluster")
        found: List[int] = []
        max_cl = (self.fat_sectors * self.bps) // 4
        for c in range(3, max_cl):
            if self.fat_get(c) == 0:
                found.append(c)
                if len(found) == count:
                    break
        if len(found) != count:
            raise Refused("FAT image is full")
        for i, c in enumerate(found):
            nxt = found[i + 1] if i + 1 < len(found) else FAT32_EOC_MIN
            self.fat_set(c, nxt)
        return found

    def read_chain_bytes(self, start: int, size: int) -> bytes:
        if size == 0:
            return b""
        chunks = [self.read_cluster(c) for c in self.cluster_chain(start)]
        return b"".join(chunks)[:size]

    def _dir_entries(self, cluster: int) -> List[dict]:
        chain = self.cluster_chain(cluster)
        blob = b"".join(self.read_cluster(c) for c in chain)
        pending_lfn: List[str] = []
        rows: List[dict] = []
        for i in range(0, len(blob), ENTRY):
            ent = blob[i : i + ENTRY]
            if len(ent) < ENTRY or ent[0] == 0x00:
                break
            if ent[0] == 0xE5:
                pending_lfn = []
                continue
            attr = ent[11]
            if attr == ATTR_LFN:
                seq = ent[0]
                if seq == 0xE5:
                    pending_lfn = []
                    continue
                chunk = _decode_lfn_chunk(ent)
                if seq & 0x40:
                    pending_lfn = [chunk]
                else:
                    pending_lfn.append(chunk)
                continue
            if attr & ATTR_VOL:
                pending_lfn = []
                continue
            name = "".join(reversed(pending_lfn)) if pending_lfn else _short_name(ent)
            pending_lfn = []
            cl = (_u16(ent, 20) << 16) | _u16(ent, 26)
            rows.append(
                {
                    "name": name,
                    "short": _short_name(ent),
                    "attr": attr,
                    "dir": bool(attr & ATTR_DIR),
                    "cluster": cl,
                    "size": _u32(ent, 28),
                    "index": i,
                    "dir_cluster": cluster,
                }
            )
        return rows

    def list_dir(self, path: str = "/") -> List[dict]:
        cluster = self._walk(path, want_dir=True)
        return self._dir_entries(cluster)

    def _walk(self, path: str, *, want_dir: bool) -> int:
        parts = [p for p in path.replace("\\", "/").split("/") if p and p != "."]
        cluster = self.root_cluster
        if not parts:
            if not want_dir:
                raise Refused("path is a directory")
            return cluster
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            rows = self._dir_entries(cluster)
            hit = None
            for row in rows:
                if row["name"].lower() == part.lower() or row["short"].lower() == part.lower():
                    hit = row
                    break
            if hit is None:
                raise Refused(f"FAT path not found: {path}")
            if last:
                if want_dir:
                    if not hit["dir"]:
                        raise Refused(f"{path} is not a directory")
                    return hit["cluster"] or 2
                if hit["dir"]:
                    raise Refused(f"{path} is a directory")
                return hit["cluster"]
            if not hit["dir"]:
                raise Refused(f"{path} is not a directory")
            cluster = hit["cluster"] or 2
        return cluster

    def stat(self, path: str) -> dict:
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        if not parts:
            raise Refused("cannot stat volume root as a file")
        parent = "/" + "/".join(parts[:-1])
        cluster = self._walk(parent, want_dir=True) if parts[:-1] else self.root_cluster
        want = parts[-1].lower()
        for row in self._dir_entries(cluster):
            if row["name"].lower() == want or row["short"].lower() == want:
                return row
        raise Refused(f"FAT path not found: {path}")

    def read_file(self, path: str) -> bytes:
        row = self.stat(path)
        if row["dir"]:
            raise Refused(f"{path} is a directory")
        if row["size"] == 0:
            return b""
        if row["cluster"] < 2:
            return b""
        return self.read_chain_bytes(row["cluster"], row["size"])

    def exists(self, path: str) -> bool:
        try:
            self.stat(path)
            return True
        except Refused:
            return False

    def mkdir_p(self, path: str) -> None:
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        cluster = self.root_cluster
        sofar = []
        for part in parts:
            sofar.append(part)
            rows = self._dir_entries(cluster)
            hit = next(
                (
                    r
                    for r in rows
                    if r["name"].lower() == part.lower() or r["short"].lower() == part.lower()
                ),
                None,
            )
            if hit:
                if not hit["dir"]:
                    raise Refused("/".join(sofar) + " exists and is not a directory")
                cluster = hit["cluster"] or 2
                continue
            new_cl = self.alloc_clusters(1)[0]
            # "." and ".."
            dot = bytearray(self.cluster_bytes)
            self._put_dir_ent(dot, 0, b".          ", ATTR_DIR, new_cl, 0)
            self._put_dir_ent(dot, 32, b"..         ", ATTR_DIR, cluster if cluster != self.root_cluster else 0, 0)
            self.write_cluster(new_cl, bytes(dot))
            self._append_dir_entry(cluster, part, ATTR_DIR, new_cl, 0)
            cluster = new_cl

    def write_file(self, path: str, data: bytes) -> None:
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        if not parts:
            raise Refused("refusing to write volume root")
        parent = "/" + "/".join(parts[:-1])
        if parts[:-1]:
            self.mkdir_p(parent)
        parent_cl = self._walk(parent, want_dir=True) if parts[:-1] else self.root_cluster
        name = parts[-1]
        try:
            existing = self.stat(path)
            if existing["dir"]:
                raise Refused(f"{path} is a directory")
            self._replace_file(existing, data)
            return
        except Refused:
            pass
        ncl = max(1, (len(data) + self.cluster_bytes - 1) // self.cluster_bytes) if data else 1
        chain = self.alloc_clusters(ncl)
        if not data:
            self.write_cluster(chain[0], b"")
        else:
            for i, c in enumerate(chain):
                sl = data[i * self.cluster_bytes : (i + 1) * self.cluster_bytes]
                self.write_cluster(c, sl)
        self._append_dir_entry(parent_cl, name, ATTR_ARCH, chain[0], len(data))

    def _replace_file(self, row: dict, data: bytes) -> None:
        ncl = max(1, (len(data) + self.cluster_bytes - 1) // self.cluster_bytes) if data else 1
        old = self.cluster_chain(row["cluster"]) if row["cluster"] >= 2 else []
        if len(old) < ncl:
            extra = self.alloc_clusters(ncl - len(old)) if old else self.alloc_clusters(ncl)
            if old:
                self.fat_set(old[-1], extra[0])
                chain = old + extra
            else:
                chain = extra
        else:
            chain = old[:ncl]
            if old and ncl:
                self.fat_set(chain[-1], FAT32_EOC_MIN)
            for leftover in old[ncl:]:
                self.fat_set(leftover, 0)
        if not data:
            self.write_cluster(chain[0], b"")
        else:
            for i, c in enumerate(chain):
                self.write_cluster(c, data[i * self.cluster_bytes : (i + 1) * self.cluster_bytes])
        self._patch_dir_size(row, chain[0], len(data))

    def _patch_dir_size(self, row: dict, cluster: int, size: int) -> None:
        chain = self.cluster_chain(row["dir_cluster"])
        idx = row["index"]
        cl_i = idx // self.cluster_bytes
        off = idx % self.cluster_bytes
        blob = bytearray(self.read_cluster(chain[cl_i]))
        struct.pack_into("<H", blob, off + 20, (cluster >> 16) & 0xFFFF)
        struct.pack_into("<H", blob, off + 26, cluster & 0xFFFF)
        struct.pack_into("<I", blob, off + 28, size)
        self.write_cluster(chain[cl_i], bytes(blob))

    def _put_dir_ent(
        self, blob: bytearray, off: int, name11: bytes, attr: int, cluster: int, size: int
    ) -> None:
        blob[off : off + 11] = name11
        blob[off + 11] = attr
        struct.pack_into("<H", blob, off + 20, (cluster >> 16) & 0xFFFF)
        struct.pack_into("<H", blob, off + 26, cluster & 0xFFFF)
        struct.pack_into("<I", blob, off + 28, size)

    def _append_dir_entry(
        self, dir_cluster: int, name: str, attr: int, cluster: int, size: int
    ) -> None:
        chain = self.cluster_chain(dir_cluster)
        blob = bytearray(b"".join(self.read_cluster(c) for c in chain))
        used = []
        for i in range(0, len(blob), ENTRY):
            ent = blob[i : i + ENTRY]
            if ent and ent[0] not in (0x00, 0xE5) and ent[11] != ATTR_LFN:
                used.append(bytes(ent[0:11]))
        name11 = _dos83(name, used)
        checksum = _lfn_checksum(name11)
        # 13 chars per LFN entry
        n_lfn = (len(name) + 12) // 13
        chunks = []
        for seq in range(n_lfn, 0, -1):
            start = (seq - 1) * 13
            piece = name[start : start + 13]
            chunks.append(_encode_lfn_chunk(piece, seq, checksum, last=(seq == n_lfn)))
        need = (n_lfn + 1) * ENTRY
        slot = None
        run = 0
        start = 0
        for i in range(0, len(blob), ENTRY):
            ent = blob[i : i + ENTRY]
            free = (not ent or ent[0] in (0x00, 0xE5))
            if free:
                if run == 0:
                    start = i
                run += ENTRY
                if run >= need:
                    slot = start
                    break
            else:
                run = 0
        if slot is None:
            extra = self.alloc_clusters(1)[0]
            self.fat_set(chain[-1], extra)
            chain.append(extra)
            blob.extend(b"\x00" * self.cluster_bytes)
            slot = len(blob) - self.cluster_bytes
            run = self.cluster_bytes
        cur = slot
        for ch in chunks:
            blob[cur : cur + ENTRY] = ch
            cur += ENTRY
        self._put_dir_ent(blob, cur, name11, attr, cluster, size)
        # write back clusters
        for i, c in enumerate(chain):
            sl = bytes(blob[i * self.cluster_bytes : (i + 1) * self.cluster_bytes])
            if len(sl) < self.cluster_bytes:
                sl = sl + b"\x00" * (self.cluster_bytes - len(sl))
            self.write_cluster(c, sl)

    def digest_file(self, path: str) -> str:
        return sha256_bytes(self.read_file(path))


def open_nano_fat_image(path: Path, *, writable: bool = False) -> Fat32Image:
    """Open a full-disk nano 7G backup (MBR + 4K FAT at 262144) or a partition."""
    path = Path(path)
    size = path.stat().st_size
    with open(path, "rb") as fh:
        head = fh.read(max(NANO7_FAT_BYTE + 512, 512))
    if len(head) >= NANO7_FAT_BYTE + 512 and is_fat32_boot(head[NANO7_FAT_BYTE : NANO7_FAT_BYTE + 512]):
        return Fat32Image(path, offset=NANO7_FAT_BYTE, writable=writable)
    if is_fat32_boot(head[:512]):
        return Fat32Image(path, offset=0, writable=writable)
    # MBR with 512-byte LBA numbers that still start the FAT at 262144.
    if len(head) >= 512 and head[510:512] == b"\x55\xaa":
        for slot in range(4):
            ent = head[446 + slot * 16 : 446 + (slot + 1) * 16]
            ptype = ent[4]
            start = struct.unpack_from("<I", ent, 8)[0]
            if ptype in (0x0B, 0x0C, 0x0E) and start:
                for bps in (4096, 512):
                    off = start * bps
                    if off + 512 <= size:
                        with open(path, "rb") as fh:
                            fh.seek(off)
                            boot = fh.read(512)
                        if is_fat32_boot(boot):
                            return Fat32Image(path, offset=off, writable=writable)
    raise Refused(
        f"{path} has no FAT32 boot at byte 0 or 262144. "
        "Need the 10:37 FAT32 backup (MBR, 4096-byte sectors, FAT at 262144). "
        "hdiutil cannot mount that image; this parser is the Mac path."
    )


def make_nano7_fat_test_image(path: Path, *, volume_bytes: int = 8 * 1024 * 1024) -> Path:
    """Build a tiny MBR+4K FAT32 image for tests (not a real nano backup)."""
    from .fat32fs import build_fat32_prefix

    path = Path(path)
    prefix = build_fat32_prefix(
        volume_bytes, label="IPOD", hidden_sectors=64, bytes_per_sector=NANO7_FAT_SECTOR
    )
    mbr = bytearray(NANO7_FAT_BYTE)
    size_lba = volume_bytes // NANO7_FAT_SECTOR
    # One FAT32 LBA-mapped partition at LBA 64 in 4096-byte units.
    mbr[446] = 0x80
    mbr[450] = 0x0C
    struct.pack_into("<I", mbr, 454, 64)
    struct.pack_into("<I", mbr, 458, size_lba)
    mbr[510] = 0x55
    mbr[511] = 0xAA
    path.write_bytes(bytes(mbr) + prefix + b"\x00" * (volume_bytes - len(prefix)))
    return path
