"""Config, receipts, and a maintenance lock.

Receipts are what stop a second firmware write of the same image, and what
lets later commands reuse the backup hash without the user copy-pasting it.
"""

from __future__ import annotations

import fcntl
import os
import time
from pathlib import Path
from typing import Dict, Optional

from .compat import Refused, is_linux, is_macos, is_root, root


def config_dir() -> Path:
    """Read XDG/NANO7KIT_CONFIG_DIR on every call so tests cannot freeze a path at import."""
    override = os.environ.get("NANO7KIT_CONFIG_DIR")
    if override:
        return Path(override)
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "nano7kit"


def config_file() -> Path:
    return config_dir() / "config"


def state_dir() -> Path:
    env = os.environ.get("NANO7_STATE_DIR") or os.environ.get("NANO7KIT_STATE")
    if env:
        p = Path(env)
        p.mkdir(parents=True, mode=0o700, exist_ok=True)
        return p
    legacy = Path("/var/lib/nano7-nanoapps-untether")
    if is_root() and legacy.is_dir():
        return legacy
    if is_root() and is_macos():
        p = Path("/var/db/nano7kit")
    elif is_root() and is_linux():
        p = Path("/var/lib/nano7kit")
    else:
        p = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "nano7kit"
    p.mkdir(parents=True, mode=0o700, exist_ok=True)
    return p


def load_config() -> Dict[str, str]:
    cfg: Dict[str, str] = {}
    path = config_file()
    if path.is_file():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    return cfg


def save_config(cfg: Dict[str, str]) -> None:
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    config_file().write_text("".join(f"{k}={v}\n" for k, v in cfg.items()))


def write_receipt(name: str, fields: Dict[str, str]) -> Path:
    path = state_dir() / name
    body = "".join(f"{k}={v}\n" for k, v in fields.items())
    path.write_text(body)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def read_receipt(name: str) -> Dict[str, str]:
    path = state_dir() / name
    if not path.is_file():
        return {}
    out: Dict[str, str] = {}
    for line in path.read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def receipt_exists(name: str) -> bool:
    return (state_dir() / name).exists()


class MaintenanceLock:
    """Exclusive lock so two writes cannot overlap."""

    def __init__(self):
        self._fh = None

    def __enter__(self):
        path = state_dir() / "maintenance.lock"
        self._fh = open(path, "a+")
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._fh.close()
            self._fh = None
            raise Refused("another nano7kit device operation holds the lock") from exc
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._fh:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._fh.close()
                self._fh = None
        return False


def utc_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def remember_backup(usb_serial: str, path: Path, sha256: str, size: int) -> None:
    cfg = load_config()
    cfg["LAST_USB_SERIAL"] = usb_serial
    cfg["LAST_BACKUP"] = str(path)
    cfg["LAST_BACKUP_SHA256"] = sha256
    cfg["LAST_BACKUP_SIZE"] = str(size)
    save_config(cfg)
    write_receipt(
        f"BACKUP-{usb_serial}",
        {
            "verified": "yes",
            "usb_serial": usb_serial,
            "path": str(path),
            "sha256": sha256,
            "size": str(size),
            "utc": utc_stamp(),
        },
    )


def last_backup(usb_serial: Optional[str] = None) -> Optional[dict]:
    cfg = load_config()
    if usb_serial:
        rec = read_receipt(f"BACKUP-{usb_serial}")
        if rec:
            return rec
    if cfg.get("LAST_BACKUP"):
        return {
            "path": cfg["LAST_BACKUP"],
            "sha256": cfg.get("LAST_BACKUP_SHA256", ""),
            "size": cfg.get("LAST_BACKUP_SIZE", ""),
            "usb_serial": cfg.get("LAST_USB_SERIAL", ""),
        }
    return None


def nanoapps_config_sync(mode: str, remote: Optional[str] = None) -> None:
    """Keep NanoApps ./start transport in sync so tethered SCSI uses the same host."""
    ncfg_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "nanoapps"
    ncfg_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"MODE={mode}\n"]
    if remote:
        lines.append(f"REMOTE={remote}\n")
    (ncfg_dir / "config").write_text("".join(lines))


def default_backup_dir() -> Path:
    cfg = load_config()
    if cfg.get("BACKUP_DIR"):
        return Path(cfg["BACKUP_DIR"]).expanduser()
    return Path.home() / "nano7-backups"


def repo_root() -> Path:
    return root()
