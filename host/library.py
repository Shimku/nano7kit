"""Detect truncated iTunes library DBs and restore them from a FAT32 backup.

Root cause on MKN52 (2026-10-29): after Helvetica `untether --run` (~11:10)
and the lost `/Apps` copy (page cache, then DFU), Finder reported that the
contents of the iPod could not be read and About showed 0 songs.

  iPod_Control/iTunes/iTunes Library.itlp/Library.itdb  -> 1 byte ('S')
      mtime 11:28
  iPod_Control/iTunes/iTunes Library.itlp/Dynamic.itdb  -> 0 bytes
  iPod_Control/iTunes/iTunesCDB                         -> 0 bytes

'S' is the first byte of the SQLite header "SQLite format 3". A 1-byte file
is a torn write. AMPDevicesAgent / Music had the volume remounted during the
`/Apps` copy, then DFU yanked USB before those dirty pages flushed.

Fix that ran on hardware: copy those three files from the 10:37 FAT32 backup
(sqlite integrity ok, 66 items). This command does the same, durably.

Mac-only for the live copy. Parses the backup in Python because hdiutil /
mount_msdos cannot mount the 4K MBR image. Never writes NAND. Never writes
song files. Never deletes /Apps.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from typing import Iterable, List, Optional

from .compat import Refused, is_macos, sha256_bytes, sha256_file
from .device import Nano, format_nano, find_nanos
from .fat import (
    MountFat,
    assert_not_hfs,
    ensure_fat_mount,
    fsync_path,
    sync_mount,
)
from .fatimg import Fat32Image, open_nano_fat_image

SQLITE_MAGIC = b"SQLite format 3\x00"
MHBD_MAGIC = b"mhbd"
MIN_SQLITE = 1024
LIBRARY_OK = "LIBRARY_OK"
LIBRARY_TRUNCATED = "LIBRARY_TRUNCATED"

# The three files that were torn on MKN52. Relative to the FAT volume root.
LIBRARY_FAT_PATHS = (
    "/iPod_Control/iTunes/iTunes Library.itlp/Library.itdb",
    "/iPod_Control/iTunes/iTunes Library.itlp/Dynamic.itdb",
    "/iPod_Control/iTunes/iTunesCDB",
)

# Also report these if present (not restored unless they match a restore path).
LIBRARY_SCAN_NAMES = (
    "Library.itdb",
    "Dynamic.itdb",
    "iTunesCDB",
    "iTunesDB",
    "Locations.itdb",
)


def is_library_restore_path(fat_path: str) -> bool:
    want = fat_path.replace("\\", "/").lower()
    if not want.startswith("/"):
        want = "/" + want
    want = want.rstrip("/") or "/"
    for p in LIBRARY_FAT_PATHS:
        target = p.lower()
        if want == target:
            return True
        if target.startswith(want + "/"):
            return True
    return False


def classify_library_bytes(name: str, data: bytes) -> dict:
    """Return ok/truncated/invalid plus a short reason. Never writes."""
    size = len(data)
    low = name.lower()
    row = {
        "name": name,
        "size": size,
        "ok": False,
        "truncated": False,
        "kind": "unknown",
        "reason": "",
    }
    if size == 0:
        row.update(truncated=True, reason="0 bytes")
        return row
    if size == 1:
        row.update(
            truncated=True,
            reason="1 byte torn write (MKN52 Library.itdb was 'S')",
        )
        return row
    if data.startswith(SQLITE_MAGIC):
        row["kind"] = "sqlite"
        if size < MIN_SQLITE:
            row.update(truncated=True, reason=f"sqlite header but only {size} bytes")
            return row
        try:
            _sqlite_integrity(data)
        except Refused as exc:
            row.update(reason=str(exc).replace("REFUSED: ", "", 1))
            return row
        row.update(ok=True, reason=f"sqlite ok, {size} bytes")
        return row
    if data[:1] == b"S" and not data.startswith(SQLITE_MAGIC):
        row.update(
            truncated=True,
            kind="sqlite-torn",
            reason="starts with 'S' but SQLite header is incomplete",
        )
        return row
    if data.startswith(MHBD_MAGIC):
        row["kind"] = "mhbd"
        if size < 64:
            row.update(truncated=True, reason="mhbd header but file is tiny")
            return row
        row.update(ok=True, reason=f"iTunesDB mhbd, {size} bytes")
        return row
    if low.endswith("itunescdb") and size < 64:
        row.update(truncated=True, reason=f"iTunesCDB is {size} bytes")
        return row
    if size < 64:
        row.update(truncated=True, reason=f"{size} bytes is not a library DB")
        return row
    row.update(ok=True, reason=f"present, {size} bytes (format not sqlite/mhbd)")
    return row


def _sqlite_integrity(data: bytes) -> None:
    with tempfile.NamedTemporaryFile(suffix=".itdb", delete=True) as tmp:
        tmp.write(data)
        tmp.flush()
        try:
            con = sqlite3.connect(f"file:{tmp.name}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise Refused(f"sqlite open failed: {exc}") from exc
        try:
            cur = con.execute("PRAGMA integrity_check")
            msg = cur.fetchone()
            if not msg or str(msg[0]).lower() != "ok":
                raise Refused(f"sqlite integrity_check: {msg}")
            try:
                n = con.execute("SELECT COUNT(*) FROM item").fetchone()
                if n:
                    # Info only; some itdb schemas use different table names.
                    pass
            except sqlite3.Error:
                pass
        finally:
            con.close()


def inspect_library_mount(root: Path) -> dict:
    """Read-only scan of a mounted FAT volume."""
    root = Path(root)
    files = []
    itunes_dirs = []
    for ctrl in ("iPod_Control", "iTunes_Control"):
        itunes = root / ctrl / "iTunes"
        if itunes.is_dir():
            itunes_dirs.append(str(itunes))
            for p in itunes.rglob("*"):
                if p.is_file() and p.name in LIBRARY_SCAN_NAMES:
                    try:
                        data = p.read_bytes()
                    except OSError:
                        data = b""
                    info = classify_library_bytes(p.name, data)
                    rel = "/" + str(p.relative_to(root)).replace("\\", "/")
                    info["path"] = rel
                    info["host"] = str(p)
                    files.append(info)
    truncated = [f for f in files if f.get("truncated") or not f.get("ok")]
    missing = []
    have = {f["path"].lower() for f in files}
    for fat in LIBRARY_FAT_PATHS:
        if fat.lower() not in have:
            # iTunesCDB may live next to the itlp folder; already scanned by name.
            if not any(f["name"].lower() == Path(fat).name.lower() for f in files):
                missing.append(fat)
    ok = bool(files) and not truncated and not missing
    return {
        "ok": ok,
        "truncated": bool(truncated),
        "files": files,
        "missing": missing,
        "itunes_dirs": itunes_dirs,
        "root": str(root),
    }


def inspect_library_image(image: Path) -> dict:
    """Read-only scan of a 4K MBR FAT32 backup image."""
    fat = open_nano_fat_image(image, writable=False)
    try:
        files = []
        for path in LIBRARY_FAT_PATHS:
            try:
                data = fat.read_file(path)
            except Refused:
                files.append(
                    {
                        "name": Path(path).name,
                        "path": path,
                        "size": 0,
                        "ok": False,
                        "truncated": True,
                        "kind": "missing",
                        "reason": "not in backup image",
                    }
                )
                continue
            info = classify_library_bytes(Path(path).name, data)
            info["path"] = path
            info["sha256"] = sha256_bytes(data)
            files.append(info)
        truncated = [f for f in files if f.get("truncated") or not f.get("ok")]
        return {
            "ok": not truncated,
            "truncated": bool(truncated),
            "files": files,
            "missing": [f["path"] for f in files if f.get("kind") == "missing"],
            "image": str(image),
            "offset": fat.offset,
        }
    finally:
        fat.close()


def print_library_report(report: dict, *, title: str = "library") -> None:
    print(f"    {title:10} root={report.get('root') or report.get('image') or '-'}")
    files = report.get("files") or []
    if not files:
        print("               no iTunesCDB / Library.itdb / Dynamic.itdb visible")
        print("               About 0 songs can mean a torn DB. See docs/SYNC.md.")
        return
    bad = False
    for item in files:
        flag = "ok" if item.get("ok") else "TRUNCATED"
        if not item.get("ok"):
            bad = True
        loc = item.get("path") or item.get("name")
        print(f"               {flag:9} {loc}  {item.get('size')} bytes  {item.get('reason')}")
    if report.get("missing"):
        bad = True
        for m in report["missing"]:
            print(f"               missing   {m}")
    if bad:
        print(f"               {LIBRARY_TRUNCATED}")
        print("               Restore the three files from the hashed FAT32 backup:")
        print("               ./start library restore-from-backup --backup IMAGE --backup-sha256 HASH")
        print("               Disk Mode (Do not disconnect while plugged in). No NAND.")
    else:
        print("               library DBs look intact (sqlite/mhbd).")


def extract_library_from_backup(image: Path) -> dict:
    """Pull the three restore files from a backup image. Refuse if source is torn."""
    fat = open_nano_fat_image(image, writable=False)
    try:
        blobs = {}
        for path in LIBRARY_FAT_PATHS:
            try:
                data = fat.read_file(path)
            except Refused as exc:
                raise Refused(
                    f"backup image is missing {path}. Need the 10:37 FAT32 backup, "
                    f"not the HFS Finder image. {exc}"
                ) from exc
            info = classify_library_bytes(Path(path).name, data)
            if not info.get("ok"):
                raise Refused(
                    f"backup {path} is not a usable library DB ({info.get('reason')}). "
                    "Will not copy a torn file onto the nano."
                )
            blobs[path] = data
        return {"files": blobs, "offset": fat.offset, "image": str(image)}
    finally:
        fat.close()


def restore_library_from_backup(
    nano: Optional[Nano],
    *,
    backup: Path,
    backup_sha256: str,
    skip_wait: bool = False,
    watch: bool = False,
    timeout: int = 180,
    skip_backup_file: bool = False,
) -> dict:
    """Disk Mode copy of the three library files. Mac-only. No NAND."""
    from .safety import check_backup
    from .sync import wait_for_enter_or_watch
    from .untether import check_core_loader_hashes, durable_flush_and_rehash

    if not is_macos():
        raise Refused(
            "library restore-from-backup is Mac-only (Disk Mode + mounted "
            "/Volumes/IPOD). The backup parser itself is in host/fatimg.py "
            "and is tested on Linux. Copy the files on a Mac."
        )
    backup = Path(backup).expanduser()
    print("nano7kit library restore-from-backup")
    print("Copies only Library.itdb, Dynamic.itdb, iTunesCDB from a FAT32 backup.")
    print("Does not write NAND. Does not write song files. Does not wipe /Apps.")
    print("Backup image is parsed in Python (hdiutil cannot mount 4K MBR FAT).")
    print()
    check_backup(
        backup,
        backup_sha256,
        None,
        skip_file=skip_backup_file,
        verify_bytes=not skip_backup_file,
        allow_size_mismatch=True,
    )
    extracted = extract_library_from_backup(backup)
    print(f"    backup    {backup}")
    print(f"    fat off   {extracted['offset']}")
    for path, data in extracted["files"].items():
        print(f"    source    {path}  {len(data)} bytes  sha {sha256_bytes(data)[:16]}...")

    attached = find_nanos()
    if attached:
        print("Currently attached:")
        for n in attached:
            print(format_nano(n))
            print()
        n0 = attached[0]
        if n0.fstype == "vfat":
            print(
                "If Home shows Connected, that is RetailOS. Prefer Disk Mode "
                "(screen: Do not disconnect while plugged in) before this copy."
            )
            print()
        else:
            assert_not_hfs(n0)
    if not skip_wait:
        wait_for_enter_or_watch(watch=watch, timeout=timeout)
        print()

    if nano is None:
        found = find_nanos()
        if not found:
            raise Refused(
                "no iPod mass-storage volume. Confirm Disk Mode (Do not disconnect "
                "while plugged in), then re-run ./start library restore-from-backup."
            )
        if len(found) > 1:
            raise Refused("more than one iPod; unplug extras")
        nano = found[0]
    assert_not_hfs(nano)
    if nano.size:
        # Size may differ: 10:37 image is the live FAT32 map. Warn, do not refuse
        # a known IPodRepartition vs Finder delta; check_backup already hashed.
        print(f"    live size {nano.size}")
    root = ensure_fat_mount(nano)
    if root is None:
        raise Refused(
            "could not mount the FAT volume. Disk Mode, then retry. Nothing written."
        )
    print(f"    volume    {root}")
    before_apps = check_core_loader_hashes(nano)
    if before_apps.get("ok"):
        print("    /Apps     core loader hashes match before library copy (will recheck)")
    else:
        print("    /Apps     core loader hashes incomplete; copy still runs. Do not Restore.")

    live = inspect_library_mount(root)
    print_library_report(live, title="before")

    ops = MountFat(root, allow_library=True)
    wrote = []
    already = []
    expected = {}
    tmpdir = Path(tempfile.mkdtemp(prefix="nano7-library-"))
    try:
        for fat_path, data in extracted["files"].items():
            src = tmpdir / Path(fat_path).name
            src.write_bytes(data)
            digest = sha256_file(src)
            status = ops.copy_verified(src, fat_path, replace=True)
            expected[fat_path] = digest
            (already if status == "already" else wrote).append(fat_path)
            print(f"    {status:8}  {fat_path}  sha {digest[:16]}...")
            try:
                fsync_path(ops.host_path(fat_path))
            except OSError:
                pass
    finally:
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)

    rec = {
        "file_hashes": expected,
        "wrote": str(len(wrote)),
        "already": str(len(already)),
        "verified": "yes",
        "library": "yes",
    }
    # Flush library files plus /Apps so DFU/unplug cannot tear either tree.
    print("    flush     library DBs + /Apps, F_FULLFSYNC, unmount, remount rehash")
    sync_mount(root, extra_files=[ops.host_path(p) for p in expected])
    durable_flush_and_rehash(nano, rec)
    after_apps = check_core_loader_hashes(nano)
    if before_apps.get("ok") and not after_apps.get("ok"):
        raise Refused(
            "library copy finished but core /Apps loader hashes no longer match. "
            "Finder Restore reformats HFS+ and wipes /Apps. This command does not "
            "Restore. Re-run ./start install-untether (no firmware)."
        )
    after = inspect_library_mount(Path(nano.mountpoints[0]) if nano.mountpoints else root)
    print_library_report(after, title="after")
    if after.get("truncated") or not after.get("ok"):
        raise Refused(
            "library files were copied but still look truncated after remount. "
            "Nothing else written. Do not Finder Restore."
        )
    print(LIBRARY_OK)
    print(f"    {len(wrote)} written, {len(already)} already present")
    print("    Eject IPOD. Unplug. Sleep/Wake+Home to Home. Music + album art should load.")
    print("    Blocks / Game Boy / Probe stay (/Apps was not music).")
    return {
        "ok": True,
        "wrote": wrote,
        "already": already,
        "hashes": expected,
        "loader_ok": bool(after_apps.get("ok")),
    }


def inspect_attached_or_none() -> Optional[dict]:
    nanos = find_nanos()
    if not nanos:
        return None
    nano = nanos[0]
    try:
        assert_not_hfs(nano)
    except Refused:
        return None
    root = ensure_fat_mount(nano)
    if root is None:
        return None
    return inspect_library_mount(root)
