"""FAT access: mounted volume on macOS (cp/sync), mtools on a raw node otherwise."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .compat import Refused, have, is_macos, is_root, run, sha256_file
from .device import Nano

# Darwin fcntl.F_FULLFSYNC. Regular fsync can return before USB FAT is on media.
# Live test hashed /Apps from the page cache; DEVICE_FILES_OK then DFU lost
# the write. F_FULLFSYNC + unmount + remount rehash is the durable proof.
F_FULLFSYNC = 51

# Darwin /dev/rdiskNs1 is a 4K character device: unaligned seek/read returns EINVAL.
DARWIN_DISK_ALIGN = 4096


def mtools_env() -> dict:
    env = os.environ.copy()
    env["MTOOLS_SKIP_CHECK"] = "1"
    env["MTOOLS_NO_VFAT"] = "0"
    return env


def need_mtools() -> None:
    for tool in ("mcopy", "mdir", "mmd", "mdel"):
        if not have(tool):
            raise Refused(
                f"missing {tool} (mtools). On a Mac: brew install mtools. "
                f"On Debian/Ubuntu: sudo apt install mtools. Then re-run ./start deps."
            )


def exists(part: str, fat_path: str) -> bool:
    r = run(
        ["mdir", "-i", part, "::" + fat_path],
        check=False,
        capture=True,
        env=mtools_env(),
    )
    return r.returncode == 0


def mkdir_p(part: str, fat_path: str) -> None:
    """Create FAT directories, ignoring already-exists."""
    parts = [p for p in fat_path.strip("/").split("/") if p]
    cur = ""
    for p in parts:
        cur += "/" + p
        if exists(part, cur):
            continue
        r = run(["mmd", "-i", part, "::" + cur], check=False, capture=True, env=mtools_env())
        if r.returncode != 0 and not exists(part, cur):
            err = (r.stderr or r.stdout or "").strip()
            raise Refused(f"could not create {cur} on the nano: {err}")


_ITUNES_MARKERS = ("itunes_control", "ipod_control")

# Relative to the FAT volume root. fsync these before DFU so AMP/Music dirty
# pages cannot tear Library.itdb (MKN52: 1-byte 'S' after the remount+DFU).
_LIBRARY_FLUSH_REL = (
    Path("iPod_Control") / "iTunes" / "iTunes Library.itlp" / "Library.itdb",
    Path("iPod_Control") / "iTunes" / "iTunes Library.itlp" / "Dynamic.itdb",
    Path("iPod_Control") / "iTunes" / "iTunesCDB",
    Path("iTunes_Control") / "iTunes" / "iTunesCDB",
)


def assert_not_hfs(nano: Nano) -> None:
    """Refuse HFS+ / Apple_HFS volumes. Convert with ./start fat32 (no Windows)."""
    raw = (nano.fstype or "").lower()
    if raw == "hfs" or "hfs" in raw:
        raise Refused(
            "this nano looks HFS+. Finder Restore on a Mac creates HFS+ again. "
            "There is no Windows machine in this workflow. Export music only if you "
            "still want those files; wiping and resyncing later is fine. Then run "
            "./start backup (hashed raw image, kept for recovery) and ./start fat32. "
            "That uses the same SCSI repartition command as Windows iTunes, then "
            "formats the data partition FAT32. See docs/FAT32.md."
        )


def assert_safe_fat_path(fat_path: str, *, allow_library: bool = False) -> None:
    """Never write the iTunes/iPod music database (upstream warning).

    The only exception is ./start library restore-from-backup, which copies
    the three torn DB files (Library.itdb, Dynamic.itdb, iTunesCDB) from a
    hashed FAT32 backup. Song files stay refused.
    """
    if allow_library:
        from .library import is_library_restore_path

        if is_library_restore_path(fat_path):
            return
    lowered = fat_path.replace("\\", "/").lower()
    for marker in _ITUNES_MARKERS:
        if marker in lowered:
            raise Refused(
                f"refusing to write {fat_path}: that path is part of the iTunes/iPod "
                f"database. Restore with iTunes; do not patch it from this toolkit. "
                f"Torn Library.itdb / iTunesCDB: ./start library restore-from-backup."
            )


def copy_to(part: str, src: Path, fat_path: str, *, allow_library: bool = False) -> None:
    assert_safe_fat_path(fat_path, allow_library=allow_library)
    r = run(
        ["mcopy", "-o", "-i", part, str(src), "::" + fat_path],
        check=False,
        capture=True,
        env=mtools_env(),
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        raise Refused(f"mcopy to {fat_path} failed: {err}")


def copy_from(part: str, fat_path: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = run(
        ["mcopy", "-n", "-i", part, "::" + fat_path, str(dest)],
        check=False,
        capture=True,
        env=mtools_env(),
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        raise Refused(f"mcopy from {fat_path} failed: {err}")


def delete(part: str, fat_path: str) -> None:
    run(["mdel", "-i", part, "::" + fat_path], check=False, capture=True, env=mtools_env())


def read_hash(part: str, fat_path: str) -> Optional[str]:
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / "read"
        r = run(
            ["mcopy", "-n", "-i", part, "::" + fat_path, str(dest)],
            check=False,
            capture=True,
            env=mtools_env(),
        )
        if r.returncode != 0 or not dest.is_file():
            return None
        return sha256_file(dest)


def copy_verified(part: str, src: Path, fat_path: str, *, replace: bool = False) -> str:
    """Write one file and read it back. Idempotent if the on-disk hash already matches."""
    return MtoolsFat(part).copy_verified(src, fat_path, replace=replace)


def read_aligned(
    path: Path,
    offset: int,
    length: int,
    *,
    align: int = DARWIN_DISK_ALIGN,
) -> bytes:
    """Read from a raw disk using align-sized offsets and lengths.

    Darwin rdisk character devices refuse unaligned 512-byte reads. Known
    size/offset come from discovery, not st_size (which is 0 on rdisk).
    """
    path = Path(path)
    if offset < 0 or length < 0:
        raise OSError("negative disk read")
    if align < 1:
        align = 1
    start = offset - (offset % align)
    end = offset + length
    if end % align:
        end += align - (end % align)
    need = end - start
    with open(path, "rb") as f:
        f.seek(start)
        buf = f.read(need)
    rel = offset - start
    if len(buf) < rel + length:
        raise OSError(
            f"short aligned read {path} off={offset} len={length} got {len(buf)}"
        )
    return buf[rel : rel + length]


def fat32_bpb_present(nano: Nano) -> bool:
    """True when a FAT32 boot sector is readable with 4K-aligned I/O."""
    from .fat32fs import is_fat32_boot

    candidates = []
    if nano.partition:
        candidates.append((nano.partition, 0))
    raw = nano.raw_device
    if raw:
        off = 64 * 4096
        try:
            from .layout import inspect_device

            layout = inspect_device(Path(raw), nano.size if nano.size else None)
            if layout.data is not None:
                off = layout.data.start_bytes
        except (Refused, OSError):
            pass
        candidates.append((raw, off))
    seen = set()
    for node, off in candidates:
        key = (str(node), int(off))
        if key in seen:
            continue
        seen.add(key)
        try:
            blob = read_aligned(Path(node), int(off), 512)
        except (OSError, ValueError, TypeError):
            continue
        if is_fat32_boot(blob):
            return True
    return False


def refuse_unreadable_fat(nano: Nano, *, after_unmount: bool = True) -> None:
    """Refuse without suggesting fat32 when a valid FAT BPB is already there."""
    where = " after unmount" if after_unmount else ""
    if fat32_bpb_present(nano):
        raise Refused(
            f"data partition has a FAT32 boot record{where} but the raw node "
            f"({nano.partition}) is not usable for mtools on this host "
            "(Darwin rdisk is a 4K character device). Copy /Apps on the "
            "mounted volume (/Volumes/IPOD) instead. Do not run ./start fat32: "
            "that formats the data volume and would wipe music."
        )
    raise Refused(
        f"data partition is not a readable FAT volume{where}. "
        "If Finder already mounts a FAT32 IPOD volume, remount it and retry; "
        "do not format. ./start fat32 is only for HFS+ (it wipes music)."
    )


def mounted_root(nano: Nano) -> Optional[Path]:
    """First live mountpoint. Leftover /Volumes/IPOD after unmount is skipped."""
    for mp in nano.mountpoints or []:
        p = Path(mp)
        if not p.is_dir():
            continue
        if str(p).startswith("/Volumes/") and not os.path.ismount(p):
            continue
        return p
    return None


def _darwin_disk_idents(nano: Nano) -> tuple:
    ident = (nano.bsd_name or Path(nano.device).name).replace("/dev/", "")
    if ident.startswith("rdisk"):
        ident = ident[1:]
    part = Path(nano.partition).name if nano.partition else ""
    if part.startswith("r"):
        part = part[1:]
    if not part:
        part = ident + "s1" if ident.startswith("disk") and ident[4:].isdigit() else ident
    return ident, part


def _refresh_darwin_mounts(nano: Nano) -> None:
    ident, part = _darwin_disk_idents(nano)
    from .device import _macos_mounts

    mps = _macos_mounts(ident, part)
    ipod = Path("/Volumes/IPOD")
    if ipod.is_dir() and os.path.ismount(ipod) and str(ipod) not in mps:
        mps = list(mps) + [str(ipod)]
    if mps:
        nano.mountpoints = mps


def ensure_fat_mount(
    nano: Nano,
    *,
    retries: int = 8,
    delay: float = 0.4,
) -> Optional[Path]:
    """Keep or restore the Finder mount so /Apps can be copied with cp.

    After usbscsi seizes mass storage, diskutil mountDisk can return 0
    before IOMedia reappears. Retry `diskutil mount /dev/diskNs1` and wait.
    """
    root = mounted_root(nano)
    if root is not None:
        return root
    if not is_macos():
        return None
    ident, part = _darwin_disk_idents(nano)
    attempts = max(1, int(retries))
    for i in range(attempts):
        # Slice first: mountDisk after libusb release is often a no-op.
        for cmd in (
            ["diskutil", "mount", f"/dev/{part}"],
            ["diskutil", "mount", part],
            ["diskutil", "mountDisk", ident],
            ["diskutil", "mountDisk", f"/dev/{ident}"],
        ):
            run(cmd, check=False, capture=True)
        _refresh_darwin_mounts(nano)
        root = mounted_root(nano)
        if root is not None:
            return root
        extra = Path("/Volumes/IPOD")
        if extra.is_dir() and os.path.ismount(extra):
            nano.mountpoints = [str(extra)]
            return extra
        if i + 1 < attempts and delay > 0:
            print(f"    remount    retry {i + 2}/{attempts}: diskutil mount /dev/{part}")
            time.sleep(delay)
    return None


def fsync_path(path: Path) -> None:
    """fsync one file or directory. On Darwin also F_FULLFSYNC (USB FAT)."""
    path = Path(path)
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
        if is_macos():
            try:
                import fcntl

                cmd = getattr(fcntl, "F_FULLFSYNC", F_FULLFSYNC)
                fcntl.fcntl(fd, cmd)
            except (OSError, AttributeError, ValueError):
                pass
    finally:
        os.close(fd)


def _fsync_tree(path: Path) -> None:
    path = Path(path)
    if path.is_file():
        try:
            fsync_path(path)
        except OSError:
            pass
        return
    if not path.is_dir():
        return
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            p = Path(dirpath) / name
            try:
                fsync_path(p)
            except OSError:
                pass
        try:
            fsync_path(Path(dirpath))
        except OSError:
            pass
    try:
        fsync_path(path)
    except OSError:
        pass


def flush_library_dbs(root: Path) -> list:
    """fsync the three iTunes library DBs if present. Never writes them."""
    flushed = []
    root = Path(root)
    for rel in _LIBRARY_FLUSH_REL:
        p = root / rel
        if p.is_file():
            try:
                fsync_path(p)
                fsync_path(p.parent)
                flushed.append("/" + rel.as_posix())
            except OSError:
                pass
    itunes = root / "iPod_Control" / "iTunes"
    if itunes.is_dir():
        try:
            fsync_path(itunes)
        except OSError:
            pass
    return flushed


def sync_mount(root: Path, extra_files: Optional[Iterable] = None) -> None:
    """fsync each /Apps file and dir, library DBs, F_FULLFSYNC, then os.sync.

    Does not walk the music tree. O_RDONLY fsync still flushes dirty pages;
    copy_verified also fsyncs the dest immediately after write. Library DBs
    are flushed because remount lets AMP/Music dirty them; DFU then tore
    Library.itdb to 1 byte ('S') on MKN52.
    """
    _fsync_tree(Path(root) / "Apps")
    flush_library_dbs(root)
    for extra in extra_files or []:
        p = Path(extra)
        try:
            if p.exists():
                fsync_path(p)
                if p.parent.exists():
                    fsync_path(p.parent)
        except OSError:
            pass
    try:
        fsync_path(Path(root))
    except OSError:
        pass
    try:
        os.sync()
    except (AttributeError, OSError):
        pass


class MountFat:
    """POSIX copy onto a mounted FAT volume (Darwin /Volumes/IPOD)."""

    def __init__(self, root: Path, *, allow_library: bool = False):
        self.root = Path(root)
        self.allow_library = allow_library

    def host_path(self, fat_path: str) -> Path:
        return self._host(fat_path)

    def _host(self, fat_path: str) -> Path:
        # Reads of iPod_Control (library inspect / remount rehash) are allowed.
        # Writes still go through assert_safe_fat_path in copy/mkdir/delete.
        rel = fat_path.replace("\\", "/").lstrip("/")
        root = self.root.resolve()
        if not rel:
            return root
        host = (root / rel).resolve()
        if host != root and root not in host.parents:
            raise Refused(f"refusing path {fat_path} outside the mounted volume")
        return host

    def exists(self, fat_path: str) -> bool:
        return self._host(fat_path).exists()

    def mkdir_p(self, fat_path: str) -> None:
        assert_safe_fat_path(fat_path, allow_library=self.allow_library)
        self._host(fat_path).mkdir(parents=True, exist_ok=True)

    def delete(self, fat_path: str) -> None:
        assert_safe_fat_path(fat_path, allow_library=self.allow_library)
        p = self._host(fat_path)
        if p.is_file():
            p.unlink()

    def read_hash(self, fat_path: str) -> Optional[str]:
        p = self._host(fat_path)
        if not p.is_file():
            return None
        return sha256_file(p)

    def read_bytes(self, fat_path: str) -> Optional[bytes]:
        p = self._host(fat_path)
        if not p.is_file():
            return None
        return p.read_bytes()

    def copy_to(self, src: Path, fat_path: str) -> None:
        assert_safe_fat_path(fat_path, allow_library=self.allow_library)
        dest = self._host(fat_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        try:
            fsync_path(dest)
            fsync_path(dest.parent)
        except OSError:
            pass

    def copy_verified(self, src: Path, fat_path: str, *, replace: bool = False) -> str:
        assert_safe_fat_path(fat_path, allow_library=self.allow_library)
        src_hash = sha256_file(src)
        present = self.exists(fat_path)
        if present:
            on_disk = self.read_hash(fat_path)
            if on_disk == src_hash:
                return "already"
            if not replace:
                raise Refused(
                    f"{fat_path} already exists with a different hash "
                    f"(on nano {on_disk}, local {src_hash}). "
                    f"This installer will not overwrite a mismatch. "
                    f"Pass --replace only if you intend to replace it."
                )
        parent = str(Path(fat_path).parent).replace("\\", "/")
        if parent not in (".", "", "/"):
            self.mkdir_p(parent)
        self.copy_to(src, fat_path)
        back = self.read_hash(fat_path)
        if back != src_hash:
            raise Refused(f"readback mismatch: {fat_path} expected {src_hash} got {back}")
        return "wrote"


class MtoolsFat:
    """mtools on a raw partition node or FAT image file."""

    def __init__(self, part: str, *, allow_library: bool = False):
        self.part = part
        self.allow_library = allow_library

    def exists(self, fat_path: str) -> bool:
        return exists(self.part, fat_path)

    def mkdir_p(self, fat_path: str) -> None:
        mkdir_p(self.part, fat_path)

    def delete(self, fat_path: str) -> None:
        delete(self.part, fat_path)

    def read_hash(self, fat_path: str) -> Optional[str]:
        return read_hash(self.part, fat_path)

    def read_bytes(self, fat_path: str) -> Optional[bytes]:
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "read"
            try:
                copy_from(self.part, fat_path, dest)
            except Refused:
                return None
            if not dest.is_file():
                return None
            return dest.read_bytes()

    def copy_verified(self, src: Path, fat_path: str, *, replace: bool = False) -> str:
        assert_safe_fat_path(fat_path, allow_library=self.allow_library)
        src_hash = sha256_file(src)
        present = self.exists(fat_path)
        if present:
            on_disk = self.read_hash(fat_path)
            if on_disk == src_hash:
                return "already"
            if not replace:
                raise Refused(
                    f"{fat_path} already exists with a different hash "
                    f"(on nano {on_disk}, local {src_hash}). "
                    f"This installer will not overwrite a mismatch. "
                    f"Pass --replace only if you intend to replace it."
                )
        parent = str(Path(fat_path).parent).replace("\\", "/")
        if parent not in (".", "", "/"):
            self.mkdir_p(parent)
        copy_to(self.part, src, fat_path, allow_library=self.allow_library)
        back = self.read_hash(fat_path)
        if back != src_hash:
            raise Refused(f"readback mismatch: {fat_path} expected {src_hash} got {back}")
        return "wrote"


def _run_as_console_user(cmd: list):
    """Run cmd as SUDO_USER when we are root. Finder is the login session."""
    user = (os.environ.get("SUDO_USER") or "").strip()
    argv = list(cmd)
    if is_root() and user and user != "root":
        argv = ["sudo", "-u", user, "-H", "--"] + argv
    return run(argv, check=False, capture=True)


def close_finder_ipod_windows(*, retries: int = 3, delay: float = 0.25) -> int:
    """Close Finder windows named IPOD. Does not persist Finder prefs.

    After remount, Finder auto-opens the IPOD volume. Finder/AMP talking
    RetailOS SCSI after Helvetica can reboot-loop. osascript close is the
    suppress that does not write OpenWindowForNewRemovableDisk.
    """
    if not is_macos():
        return 0
    script = 'tell application "Finder" to close (every window whose name is "IPOD")'
    ok = 0
    attempts = max(1, int(retries))
    for i in range(attempts):
        r = _run_as_console_user(["osascript", "-e", script])
        if r.returncode == 0:
            ok += 1
        if i + 1 < attempts and delay > 0:
            time.sleep(delay)
    return ok


def unmount(nano: Nano, *, allow: bool = True, force: bool = False) -> None:
    """Unmount volumes so mtools/dd can use the raw device. Never ejects."""
    if not nano.mountpoints and _still_mounted(nano):
        pass
    if is_macos():
        ident = nano.bsd_name or Path(nano.device).name
        cmd = ["diskutil", "unmountDisk"]
        if force:
            cmd.append("force")
        cmd.append(ident)
        r = run(cmd, check=False, capture=True)
        if r.returncode != 0 and nano.mountpoints:
            raise Refused(
                f"could not unmount {ident}: {(r.stderr or r.stdout or '').strip()}. "
                f"Close Finder windows for the nano and try again. Do not eject it."
            )
        return
    # Linux: unmount whole disk + partition. Do not rstrip digits from sda / mmcblk0.
    nodes = [nano.raw_device, nano.partition, nano.device]
    for node in nodes:
        if not node:
            continue
        run(["umount", node], check=False, capture=True)
        if have("udisksctl"):
            run(["udisksctl", "unmount", "-b", node], check=False, capture=True)


def _still_mounted(nano: Nano) -> bool:
    return bool(nano.mountpoints)


def fsck(nano: Nano) -> str:
    """Repair FAT. Linux: fsck.vfat. macOS: fsck_msdos / diskutil repairVolume."""
    unmount(nano)
    logs: List[str] = []
    if is_macos():
        ident = nano.bsd_name or Path(nano.device).name
        part = ident + "s1" if not ident.endswith("s1") else ident
        if have("diskutil"):
            r = run(["diskutil", "repairVolume", part], check=False, capture=True)
            logs.append((r.stdout or "") + (r.stderr or ""))
            if r.returncode == 0:
                return "ok"
        fsck_bin = "fsck_msdos"
        if have(fsck_bin):
            r = run(["fsck_msdos", "-y", nano.partition], check=False, capture=True)
            logs.append((r.stdout or "") + (r.stderr or ""))
            if r.returncode == 0:
                return "ok"
        raise Refused("FAT repair failed on macOS:\n" + "\n".join(logs[-2:]))
    cmd = None
    for cand in ("fsck.vfat", "fsck.msdos"):
        if have(cand):
            cmd = cand
            break
    if not cmd:
        raise Refused("missing fsck.vfat (install dosfstools)")
    r = run([cmd, "-a", "-w", nano.partition], check=False, capture=True)
    logs.append((r.stdout or "") + (r.stderr or ""))
    # Verify we can at least see the volume via mdir.
    if exists(nano.partition, "/"):
        return "ok"
    raise Refused(
        "FAT repair did not leave a readable volume. "
        "Run ./start fat32 or see docs/RECOVERY.md.\n" + "\n".join(logs[-2:])
    )


def verify_expected_hashes(ops, expected: Dict[str, str]) -> None:
    """Re-read hashes after remount. Refuse if Darwin page cache lied."""
    mismatches = []
    for fat, digest in expected.items():
        got = ops.read_hash(fat)
        if got != digest:
            mismatches.append(f"{fat} expected {digest} got {got}")
    if mismatches:
        raise Refused(
            "remount rehash mismatch (copy was not on media): "
            + "; ".join(mismatches[:8])
            + ". Do not DFU. Do not flash. Replug and ./start install-untether."
        )


def copy_tree_verified(part: str, src_root: Path, dest_prefix: str = "") -> int:
    """Copy every file under src_root into the FAT at dest_prefix, read back each."""
    count = 0
    files = sorted(p for p in src_root.rglob("*") if p.is_file())
    for path in files:
        rel = path.relative_to(src_root).as_posix()
        fat_path = (dest_prefix.rstrip("/") + "/" + rel) if dest_prefix else "/" + rel
        if not fat_path.startswith("/"):
            fat_path = "/" + fat_path
        copy_verified(part, path, fat_path, replace=True)
        count += 1
    return count
