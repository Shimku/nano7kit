"""Re-copy rebuilt extras over the copies already on the nano.

`./start apps refresh` is one Disk Mode step. For every staged extra in
untether/artifacts/extra/MANIFEST.json whose .hbapp is already on the nano,
it copies the current build over it when the hash differs. It does not
change AllApps.pack (or AllApps-B.pack), icons, /Apps/Data, residents, the
selector, or the loader, so the Home set and its pack-size risk stay the
same. No NAND, no firmware.

Each overwritten .hbapp is saved first under the state dir
(refresh-backup/STAMP/). `./start apps refresh --undo` copies the latest
saved set back. Home rollback stays `./start apps restore-tested`.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import const
from .compat import Refused, sha256_bytes, sha256_file

BACKUP_DIRNAME = "refresh-backup"


def staged_extra_names() -> List[str]:
    """Extras this tree builds and stages (MANIFEST.json order)."""
    man = const.artifact("artifacts/extra/MANIFEST.json")
    if not man.is_file():
        raise Refused(f"{man} is missing; run python3 -m host.app_stage")
    data = json.loads(man.read_text(encoding="utf-8"))
    return [a["name"] for a in data.get("apps", [])]


def plan_refresh(read_hash: Callable[[str], Optional[str]]) -> Dict[str, list]:
    """Pure plan: which installed extras differ from this tree's build.

    read_hash(fat_path) returns the on-nano sha256 or None when absent.
    """
    from .app_catalog import by_name, reset_catalog_cache

    reset_catalog_cache()
    rows = by_name()
    plan: Dict[str, list] = {"update": [], "current": [], "absent": [], "missing_local": []}
    for name in staged_extra_names():
        row = rows.get(name)
        if not row:
            continue
        local = const.artifact(row["executable"])
        if not local.is_file():
            plan["missing_local"].append(name)
            continue
        fat = row["fat_exe"]
        got = read_hash(fat)
        if got is None:
            plan["absent"].append(name)
            continue
        want = sha256_file(local)
        entry = {"name": name, "fat": fat, "local": str(local), "old": got, "new": want}
        (plan["current"] if got == want else plan["update"]).append(entry)
    return plan


def format_plan(plan: Dict[str, list]) -> str:
    lines = ["nano7kit apps refresh plan (read-only)"]
    for e in plan["update"]:
        lines.append(f"    update    {e['fat']}  {e['old'][:12]}... -> {e['new'][:12]}...")
    for e in plan["current"]:
        lines.append(f"    current   {e['fat']}")
    if plan["absent"]:
        lines.append("    not on nano (skipped): " + ", ".join(plan["absent"]))
    if plan["missing_local"]:
        lines.append("    not built here (skipped): " + ", ".join(plan["missing_local"]))
    lines.append(
        f"    {len(plan['update'])} to update, {len(plan['current'])} current. "
        "AllApps.pack, icons, /Apps/Data, residents and loader are not touched."
    )
    return "\n".join(lines)


def backup_root() -> Path:
    from .state import state_dir

    return state_dir() / BACKUP_DIRNAME


def latest_backup() -> Optional[Path]:
    root = backup_root()
    if not root.is_dir():
        return None
    sets = sorted(p for p in root.iterdir() if (p / "manifest.json").is_file())
    return sets[-1] if sets else None


def _preflight(nano, *, backup, backup_sha256, allow_size, confirm_untested,
               skip_backup_file, skip_wait, watch, label):
    from .apps import (
        _ops_for_nano,
        assert_not_hfs,
        check_backup,
        check_disk_size,
        check_usb_serial,
        layout_for_disk_size,
    )
    from .compat import is_macos
    from .sync import wait_for_enter_or_watch

    if not skip_wait:
        wait_for_enter_or_watch(watch=watch, timeout=180)
        print()
    if nano is None:
        from .device import find_nanos

        found = find_nanos()
        if not found:
            raise Refused(
                "no iPod mass-storage volume. Confirm Disk Mode "
                f"(Do not disconnect while plugged in), then re-run ./start apps {label}."
            )
        if len(found) > 1:
            raise Refused("more than one iPod; unplug extras")
        nano = found[0]
    check_usb_serial(nano.usb_serial)
    check_disk_size(
        nano.size,
        allow_size=allow_size,
        confirm_untested=confirm_untested,
        layout=layout_for_disk_size(nano.raw_device, nano.size),
    )
    check_backup(backup, backup_sha256, nano.size, skip_file=skip_backup_file,
                 allow_size_mismatch=True)
    assert_not_hfs(nano)
    if not is_macos():
        print("    note      live copy is documented Mac-only; this host is not macOS")
    ops, mounted = _ops_for_nano(nano)
    return nano, ops, mounted


def _finish(nano, ops, mounted, before, expected, label, packs_before):
    from .fat import ensure_fat_mount, mounted_root
    from .untether import check_core_loader_hashes, durable_flush_and_rehash

    if not expected:
        return  # nothing written; no flush or remount needed
    for fat, digest in packs_before.items():
        if digest is not None:
            expected[fat] = digest
    rec = {
        "file_hashes": expected,
        "wrote": str(len(expected)),
        "already": "0",
        "verified": "yes",
    }
    if mounted:
        if mounted_root(nano) is None:
            nano.mountpoints = [str(ensure_fat_mount(nano) or "")]
        durable_flush_and_rehash(nano, rec)
        after = check_core_loader_hashes(nano)
        if before.get("ok") and not after.get("ok"):
            raise Refused(
                f"apps {label} finished but core loader hashes no longer match. "
                "Resident/ab were not supposed to change."
            )


def _pack_hashes(ops) -> Dict[str, Optional[str]]:
    from .ports import PACK_B_FAT

    return {fat: ops.read_hash(fat) for fat in ("/Apps/AllApps.pack", PACK_B_FAT)}


def refresh_apps(
    nano,
    *,
    backup: Path,
    backup_sha256: str,
    allow_size: Optional[int] = None,
    confirm_untested: bool = False,
    skip_backup_file: bool = False,
    skip_wait: bool = False,
    watch: bool = False,
) -> dict:
    """Disk Mode: replace installed extras' .hbapp with this tree's build."""
    from .apps import RECOVERY_HINT
    from .untether import check_core_loader_hashes

    print("nano7kit apps refresh")
    print("Re-copies this tree's build of every extra already on the nano.")
    print("Does not change AllApps.pack, icons, /Apps/Data, residents or the")
    print("loader. Does not write NAND. Older copies are saved on this Mac.")
    print()
    nano, ops, mounted = _preflight(
        nano, backup=backup, backup_sha256=backup_sha256, allow_size=allow_size,
        confirm_untested=confirm_untested, skip_backup_file=skip_backup_file,
        skip_wait=skip_wait, watch=watch, label="refresh",
    )
    before = check_core_loader_hashes(nano)
    packs_before = _pack_hashes(ops)
    plan = plan_refresh(ops.read_hash)
    from .app_stage import assert_no_legacy_addresses

    for e in plan["update"]:
        assert_no_legacy_addresses(Path(e["local"]), e["name"])
    print(format_plan(plan))
    saved: Optional[Path] = None
    expected: Dict[str, str] = {}
    done: List[dict] = []
    if plan["update"]:
        saved = backup_root() / time.strftime("%Y%m%d-%H%M%S")
        saved.mkdir(parents=True, exist_ok=True)
        entries = []
        for e in plan["update"]:
            old = ops.read_bytes(e["fat"])
            if old is None or sha256_bytes(old) != e["old"]:
                raise Refused(f"could not read back {e['fat']} before replacing it")
            keep = saved / Path(e["fat"]).name
            keep.write_bytes(old)
            entries.append({**e, "saved": str(keep)})
        (saved / "manifest.json").write_text(json.dumps(entries, indent=2) + "\n")
        print(f"    saved     older copies in {saved}")
        for e in entries:
            status = ops.copy_verified(Path(e["local"]), e["fat"], replace=True)
            expected[e["fat"]] = e["new"]
            done.append(e)
            print(f"    {status:8}  {e['fat']}  sha {e['new'][:16]}...")
    if _pack_hashes(ops) != packs_before:
        raise Refused("a Home pack changed during apps refresh; it should not. " + RECOVERY_HINT)
    _finish(nano, ops, mounted, before, expected, "refresh", packs_before)
    print("APPS_OK")
    print(f"    refreshed {len(done)} app(s)" + (": " + ", ".join(e["name"] for e in done) if done else ""))
    if done:
        print("    Eject IPOD. Unplug. Sleep/Wake+Home. Open each refreshed app once.")
        print("    Undo these copies: ./start apps refresh --undo")
        print("    " + RECOVERY_HINT)
    return {
        "ok": True,
        "refreshed": [e["name"] for e in done],
        "current": [e["name"] for e in plan["current"]],
        "absent": plan["absent"],
        "backup_dir": str(saved) if saved else None,
    }


def undo_refresh(
    nano,
    *,
    backup: Path,
    backup_sha256: str,
    allow_size: Optional[int] = None,
    confirm_untested: bool = False,
    skip_backup_file: bool = False,
    skip_wait: bool = False,
    watch: bool = False,
    backup_dir: Optional[Path] = None,
) -> dict:
    """Disk Mode: copy the latest saved pre-refresh .hbapp set back."""
    from .untether import check_core_loader_hashes

    src_dir = Path(backup_dir) if backup_dir else latest_backup()
    if src_dir is None or not (src_dir / "manifest.json").is_file():
        raise Refused(f"no saved apps refresh under {backup_root()}; nothing to undo")
    entries = json.loads((src_dir / "manifest.json").read_text())
    print("nano7kit apps refresh --undo")
    print(f"Copies the {len(entries)} .hbapp file(s) saved in {src_dir} back.")
    print("Does not change AllApps.pack, icons, /Apps/Data, residents or the loader.")
    print()
    nano, ops, mounted = _preflight(
        nano, backup=backup, backup_sha256=backup_sha256, allow_size=allow_size,
        confirm_untested=confirm_untested, skip_backup_file=skip_backup_file,
        skip_wait=skip_wait, watch=watch, label="refresh --undo",
    )
    before = check_core_loader_hashes(nano)
    packs_before = _pack_hashes(ops)
    expected: Dict[str, str] = {}
    restored, skipped = [], []
    for e in entries:
        saved = Path(e["saved"])
        if not saved.is_file() or sha256_file(saved) != e["old"]:
            raise Refused(f"saved copy {saved} is missing or changed")
        got = ops.read_hash(e["fat"])
        if got == e["old"]:
            skipped.append(e["name"])
            print(f"    already   {e['fat']}")
            continue
        if got != e["new"]:
            skipped.append(e["name"])
            print(f"    skipped   {e['fat']} (changed since the refresh)")
            continue
        ops.copy_verified(saved, e["fat"], replace=True)
        expected[e["fat"]] = e["old"]
        restored.append(e["name"])
        print(f"    restored  {e['fat']}  sha {e['old'][:16]}...")
    _finish(nano, ops, mounted, before, expected, "refresh --undo", packs_before)
    print("APPS_OK")
    print(f"    restored {len(restored)} app(s)")
    return {"ok": True, "restored": restored, "skipped": skipped, "backup_dir": str(src_dir)}
