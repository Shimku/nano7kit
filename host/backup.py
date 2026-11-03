"""Full-disk backup. Always lands on the Mac (or the machine running ./start)."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Optional

from .compat import Refused, die_if_symlink, disk_free, is_root, quoted_path
from .device import Nano
from .fat import unmount
from .safety import backup_filename_token, check_disk_size, layout_for_disk_size
from .state import default_backup_dir, remember_backup, utc_stamp


def backup_path_for(nano: Nano, out: Optional[Path] = None) -> Path:
    if out:
        return Path(out).expanduser()
    directory = default_backup_dir()
    directory.mkdir(parents=True, exist_ok=True)
    name = f"nano-{backup_filename_token(nano.usb_serial)}-{utc_stamp()}.img"
    return directory / name


def backup_local(
    nano: Nano,
    dest: Path,
    *,
    allow_size: Optional[int] = None,
    confirm_untested: bool = False,
) -> dict:
    if not is_root():
        raise Refused("backup needs root so it can read the raw disk (re-run with sudo, or use ./start)")
    die_if_symlink(dest, "backup destination") if dest.exists() else None
    if dest.exists():
        raise Refused(f"{dest} already exists; will not overwrite a backup")
    layout = layout_for_disk_size(nano.raw_device, nano.size)
    check_disk_size(
        nano.size,
        allow_size=allow_size,
        confirm_untested=confirm_untested,
        what="nano",
        layout=layout,
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    free = disk_free(dest.parent)
    if free <= nano.size:
        raise Refused(
            f"not enough free space for a {nano.size} byte image in {dest.parent} "
            f"({free} bytes free). Put the backup on another disk."
        )
    unmount(nano)
    src = nano.raw_device
    print(f"==> reading {src} ({nano.size} bytes) into {quoted_path(dest)}")
    h = hashlib.sha256()
    copied = 0
    buf = 4 * 1024 * 1024
    last_pct = -1
    with open(src, "rb") as inf, open(dest, "wb") as outf:
        while copied < nano.size:
            chunk = inf.read(min(buf, nano.size - copied))
            if not chunk:
                break
            outf.write(chunk)
            h.update(chunk)
            copied += len(chunk)
            pct = copied * 100 // nano.size
            if pct != last_pct and pct % 2 == 0:
                print(f"    {pct}%  {copied // (1024 * 1024)} MiB", flush=True)
                last_pct = pct
        outf.flush()
        os_fsync(outf)
    if copied != nano.size:
        dest.unlink(missing_ok=True)
        raise Refused(f"short backup: got {copied} bytes, expected {nano.size}")
    digest = h.hexdigest()
    (dest.with_suffix(dest.suffix + ".sha256")).write_text(f"{digest}  {dest.name}\n")
    remember_backup(nano.usb_serial, dest, digest, nano.size)
    print("Backup complete. Pass these flags next time to skip the 15 GB re-read:")
    print_backup_flags(dest, digest)
    return {"path": str(dest), "sha256": digest, "size": nano.size, "usb_serial": nano.usb_serial}


def print_backup_flags(backup: Path, digest: str) -> None:
    print(f"  --backup {quoted_path(backup)}")
    print(f"  --backup-sha256 {digest}")


def select_untether_backup(
    nano: Nano,
    backup: Optional[Path] = None,
    backup_sha256: Optional[str] = None,
    *,
    allow_size: Optional[int] = None,
    confirm_untested: bool = False,
) -> dict:
    """Reuse a recorded hashed backup when size matches; otherwise take one.

    Does not re-read 15 GB when the file size matches and the sha is recorded.
    """
    from .safety import check_backup
    from .state import last_backup

    if bool(backup) ^ bool(backup_sha256):
        raise Refused(
            "pass --backup PATH and --backup-sha256 SHA together to skip a new "
            "15 GB read. One without the other is refused."
        )
    chosen = None
    digest = None
    if backup and backup_sha256:
        chosen = Path(backup).expanduser()
        digest = str(backup_sha256).lower()
        print(f"    reusing hashed FAT32 backup {quoted_path(chosen)}")
        print_backup_flags(chosen, digest)
    else:
        rec = last_backup(nano.usb_serial)
        if (
            rec
            and rec.get("path")
            and rec.get("sha256")
            and Path(rec["path"]).is_file()
            and int(rec.get("size") or 0) == nano.size
        ):
            chosen = Path(rec["path"])
            digest = str(rec["sha256"]).lower()
            print(f"    reusing hashed FAT32 backup {quoted_path(chosen)}")
            print_backup_flags(chosen, digest)
    if chosen is not None and digest is not None:
        check_backup(chosen, digest, nano.size, verify_bytes=False)
        return {
            "path": str(chosen),
            "sha256": digest,
            "size": nano.size,
            "usb_serial": nano.usb_serial,
            "reused": True,
        }
    rec = last_backup(nano.usb_serial)
    if rec and rec.get("size") and int(rec.get("size") or 0) != nano.size:
        print(
            f"    previous backup is {rec.get('size')} bytes; live nano is "
            f"{nano.size}. Taking a new FAT32 backup (music stays on the live volume)."
        )
    dest = backup_path_for(nano)
    info = backup_local(nano, dest, allow_size=allow_size, confirm_untested=confirm_untested)
    info["reused"] = False
    return info


def os_fsync(f) -> None:
    try:
        import os

        os.fsync(f.fileno())
    except OSError:
        pass


def backup_stream_stdout(nano: Nano) -> None:
    """Agent mode: write the raw disk to stdout (SSH streaming to the Mac)."""
    if not is_root():
        raise Refused("backup-stream needs root")
    unmount(nano)
    src = nano.raw_device
    copied = 0
    buf = 4 * 1024 * 1024
    with open(src, "rb") as inf:
        while copied < nano.size:
            chunk = inf.read(min(buf, nano.size - copied))
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
            copied += len(chunk)
    if copied != nano.size:
        raise Refused(f"short backup-stream: got {copied} expected {nano.size}")


def receive_stream(src_iter, dest: Path, expected_size: int) -> str:
    """Write a streamed backup (from SSH) to dest and hash it."""
    if dest.exists():
        raise Refused(f"{dest} already exists; will not overwrite a backup")
    dest.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    copied = 0
    last_pct = -1
    with open(dest, "wb") as outf:
        for chunk in src_iter:
            if not chunk:
                break
            outf.write(chunk)
            h.update(chunk)
            copied += len(chunk)
            if expected_size:
                pct = min(100, copied * 100 // expected_size)
                if pct != last_pct and pct % 2 == 0:
                    print(f"    {pct}%  {copied // (1024 * 1024)} MiB", flush=True)
                    last_pct = pct
        outf.flush()
        os_fsync(outf)
    if expected_size and copied != expected_size:
        dest.unlink(missing_ok=True)
        raise Refused(f"short streamed backup: got {copied} expected {expected_size}")
    return h.hexdigest()
