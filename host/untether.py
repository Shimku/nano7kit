"""Untethered install: loader files on FAT, then DFU flash.

One-command path: backup (if needed) -> copy hardware-tested /Apps files ->
wait for DFU -> dry-run wInd3x -> flash once.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

from . import const
from .compat import Refused, is_macos, is_root, sha256_bytes, sha256_file
from .device import Nano
from .fat import (
    MountFat,
    MtoolsFat,
    _darwin_disk_idents,
    assert_not_hfs,
    close_finder_ipod_windows,
    ensure_fat_mount,
    exists,
    mounted_root,
    need_mtools,
    refuse_unreadable_fat,
    sync_mount,
    unmount,
    verify_expected_hashes,
)
from .safety import (
    check_ab_receipt,
    check_backup,
    check_disk_size,
    check_mse,
    check_usb_serial,
    layout_for_disk_size,
    verify_release_artifacts,
)
from .layout import inspect_device
from .state import (
    last_backup,
    load_config,
    read_receipt,
    receipt_exists,
    save_config,
    state_dir,
    utc_stamp,
    write_receipt,
)
from .usb import (
    flash_restore,
    late_wtf_present,
    parse_serial,
    prepare_restore,
    select_wind3x,
    wait_dfu,
)


def _dirs(ops) -> None:
    for d in ("/Apps", "/Apps/Executables", "/Apps/Icons", "/Apps/Startup"):
        ops.mkdir_p(d)


def _install_loader_files_to(
    ops,
    nano: Nano,
    *,
    replace: bool,
    apps: Optional[list],
) -> dict:
    wrote = []
    already = []
    expected = {}
    repo = Path(__file__).resolve().parent.parent
    from .apps import (
        assert_pack_writable,
        bundle_name,
        loader_files_for,
        merge_bundles,
        pack_app_names,
        pack_for_selection,
        parse_pack,
        tested_app_names,
    )
    from .compat import sha256_bytes

    selected = list(apps) if apps is not None else tested_app_names()
    files = loader_files_for(selected)
    pack_bytes, pack_hash, tested_pack = pack_for_selection(selected)
    # Keep extra Home apps already on the nano. Never delete the resident.
    if ops.exists("/Apps/AllApps.pack"):
        on_disk = ops.read_bytes("/Apps/AllApps.pack")
        if on_disk:
            try:
                extra = [
                    b
                    for b in parse_pack(on_disk)
                    if bundle_name(b) not in const.TESTED_APPS
                ]
            except Refused:
                extra = []
            if extra:
                from .apps import merge_if_writable, pack_is_looping

                candidate = merge_bundles(pack_bytes, extra)
                reason = pack_is_looping(pack_app_names(candidate))
                kept_pack, kept = merge_if_writable(pack_bytes, extra)
                if kept:
                    pack_bytes = kept_pack
                    pack_hash = sha256_bytes(pack_bytes)
                    tested_pack = False
                    print(
                        "    pack      keeping extra Home apps already on disk: "
                        + ", ".join(pack_app_names(pack_bytes))
                    )
                else:
                    print(
                        "    pack      not keeping extra Home apps already on disk "
                        "(that pack reboot-looped on MKN52 / count > 8)."
                    )
                    if reason:
                        print("    pack      " + reason)
                    print(
                        "    pack      writing hardware-tested three: "
                        + ", ".join(pack_app_names(pack_bytes))
                    )
    import tempfile

    assert_pack_writable(pack_app_names(pack_bytes))
    _dirs(ops)
    with tempfile.TemporaryDirectory() as td:
        pack_src = Path(td) / "AllApps.pack"
        pack_src.write_bytes(pack_bytes)
        status = ops.copy_verified(
            pack_src, "/Apps/AllApps.pack", replace=replace or not tested_pack
        )
        expected["/Apps/AllApps.pack"] = pack_hash
        (already if status == "already" else wrote).append("/Apps/AllApps.pack")
    if not tested_pack:
        print(
            "HARDWARE: AllApps.pack is not the three-app tested hash "
            f"{const.ALLAPPS_PACK_SHA256[:16]}... (subset or extras). "
            "VERIFY_OK uses core loader sha (resident/ab/A) plus a valid HBPK."
        )
    for rel, fat in files:
        src = repo / "untether" / rel
        if not src.is_file():
            continue
        status = ops.copy_verified(src, fat, replace=replace)
        expected[fat] = sha256_file(src)
        (already if status == "already" else wrote).append(fat)
    # Do not delete extra catalog apps or unselected tested apps. Removal is
    # ./start apps remove only. Never delete /Apps/Startup.

    if ops.exists("/Apps/Startup/resident-B.active"):
        raise Refused(
            "/Apps/Startup/resident-B.active already exists; will not change the active slot. "
            "Delete that marker only if you intend to go back to slot A."
        )
    for rel, fat in const.AB_FILES:
        src = repo / "untether" / rel
        src_hash = sha256_file(src)
        if ops.exists(fat) and not replace:
            on = ops.read_hash(fat)
            if on != src_hash:
                raise Refused(f"{fat} already exists with a different hash; refusing to overwrite")
            already.append(fat)
            expected[fat] = src_hash
            continue
        status = ops.copy_verified(src, fat, replace=replace)
        expected[fat] = src_hash
        (already if status == "already" else wrote).append(fat)

    rh = ops.read_hash("/Apps/Startup/resident.hbapp")
    if rh != const.RESIDENT_SHA256:
        raise Refused(f"installed resident hash {rh} != tested {const.RESIDENT_SHA256}")
    expected["/Apps/Startup/resident.hbapp"] = const.RESIDENT_SHA256

    rec = {
        "verified": "yes",
        "usb_serial": nano.usb_serial,
        "active_slot": "A",
        "ab_sha256": const.AB_SHA256,
        "slot_a_sha256": const.SLOT_A_SHA256,
        "resident_sha256": const.RESIDENT_SHA256,
        "utc": utc_stamp(),
        "wrote": str(len(wrote)),
        "already": str(len(already)),
        "apps": ",".join(selected),
        "pack_sha256": pack_hash,
        "tested_pack": "yes" if tested_pack else "no",
        "file_hashes": expected,
    }
    print(
        f"DEVICE_FILES_COPIED: {len(wrote)} written, {len(already)} already present "
        "(hashes from this mount; remount rehash is the durable check)"
    )
    return rec


def _write_files_receipts(rec: dict) -> None:
    rec_disk = {k: str(v) for k, v in rec.items() if k != "file_hashes"}
    write_receipt("DEVICE_FILES_VERIFIED", rec_disk)
    write_receipt("AB_LOADER_FILES_VERIFIED", rec_disk)


def install_loader_files(
    nano: Nano,
    *,
    backup: Path,
    backup_sha256: str,
    allow_size: Optional[int] = None,
    confirm_untested: bool = False,
    replace: bool = False,
    skip_backup_file: bool = False,
    verify_bytes: bool = True,
    apps: Optional[list] = None,
    leave_mounted: bool = True,
) -> dict:
    verify_release_artifacts()
    check_usb_serial(nano.usb_serial)
    layout = layout_for_disk_size(nano.raw_device, nano.size)
    check_disk_size(
        nano.size,
        allow_size=allow_size,
        confirm_untested=confirm_untested,
        layout=layout,
    )
    check_backup(
        backup,
        backup_sha256,
        nano.size,
        skip_file=skip_backup_file,
        verify_bytes=verify_bytes,
    )
    assert_not_hfs(nano)
    root = ensure_fat_mount(nano)
    if root is not None:
        print(f"==> copying /Apps via mounted volume {root} (no mtools, no format)")
        rec = _install_loader_files_to(
            MountFat(root), nano, replace=replace, apps=apps
        )
        durable_flush_and_rehash(nano, rec)
        _write_files_receipts(rec)
        if not leave_mounted:
            print("    unmount    after remount rehash, before DFU")
            unmount(nano, force=True)
            nano.mountpoints = []
        return rec
    need_mtools()
    unmount(nano)
    part = nano.partition
    if not exists(part, "/"):
        refuse_unreadable_fat(nano, after_unmount=True)
    rec = _install_loader_files_to(
        MtoolsFat(part), nano, replace=replace, apps=apps
    )
    _write_files_receipts(rec)
    print(
        f"DEVICE_FILES_OK: {rec.get('wrote', len(rec.get('file_hashes') or {}))} written, {rec.get('already', 0)} already present "
        "(mtools readback from the raw node)"
    )
    return rec


def durable_flush_and_rehash(nano: Nano, rec: dict) -> Path:
    """fsync each file, os.sync, force unmount, remount, re-read sha.

    Live test printed DEVICE_FILES_OK from Darwin page cache, then DFU
    dropped the device; /Volumes/IPOD had no /Apps. Hashes after remount
    are the durable proof. Refuse if remount or rehash fails. No NAND write.
    """
    expected = rec.get("file_hashes") or {}
    root = mounted_root(nano)
    if root is None:
        raise Refused(
            "mounted volume disappeared before flush. /Apps write is not proven. "
            "Do not DFU. Do not flash."
        )
    print("    flush      fsync each /Apps file, F_FULLFSYNC on Darwin, os.sync")
    sync_mount(root)
    print("    unmount    diskutil unmountDisk force (flush before DFU / drop)")
    unmount(nano, force=True)
    nano.mountpoints = []
    print("    remount    re-read sha from media, not the page cache")
    remounted = ensure_fat_mount(nano, retries=12, delay=0.5)
    if remounted is None:
        raise Refused(
            "could not remount after /Apps copy; FAT write is not proven durable. "
            "Do not DFU. Do not flash. Replug, then ./start install-untether."
        )
    print(f"    remount    {remounted}")
    listing = volume_root_listing(remounted)
    print_volume_root_listing(listing)
    if not listing.get("apps_name"):
        raise Refused(
            "remount succeeded but /Apps is missing on the volume root "
            f"({remounted}). Copy was not on media. Do not DFU. Do not flash. "
            "See the volume listing above."
        )
    allow_lib = any(
        "ipod_control" in str(fat).lower() or "itunes_control" in str(fat).lower()
        for fat in expected
    )
    verify_expected_hashes(MountFat(remounted, allow_library=allow_lib), expected)
    from .fat import flush_library_dbs, sync_mount as _sync_mount

    flushed = flush_library_dbs(remounted)
    if flushed:
        print(
            "    library    fsynced "
            + ", ".join(flushed)
            + " (AMP/Music dirty pages on remount; DFU tore Library.itdb to 1 byte 'S' on MKN52)"
        )
    _sync_mount(remounted)
    closed = close_finder_ipod_windows()
    print(
        f"    finder     closed {closed} IPOD window pass(es) after remount rehash"
    )
    print(
        "    dfu        volume will unmount before DFU so library DBs cannot tear"
    )
    print(
        f"DEVICE_FILES_OK: {rec.get('wrote', len(expected))} written, {rec.get('already', 0)} already present "
        "(remount rehash matched)"
    )
    rec["durable"] = "yes"
    rec["remount"] = str(remounted)
    return remounted


def flash_firmware(
    nano: Optional[Nano],
    *,
    mse: Path,
    backup: Path,
    backup_sha256: str,
    serial: str,
    dry_run: bool = True,
    allow_size: Optional[int] = None,
    confirm_untested: bool = False,
    accept_brick: bool = False,
    skip_backup_file: bool = False,
    accept_untested_flasher: bool = False,
    accept_2015_refresh: bool = False,
    accept_untested_firmware: bool = False,
    typed_confirm: Optional[str] = None,
) -> dict:
    from .state import MaintenanceLock

    verify_release_artifacts()
    mse_hash = check_mse(mse, allow_untested=accept_untested_firmware)
    serial = (serial or "").strip()
    if not serial:
        raise Refused(
            "device serial is required (Settings > General > About, or the back). Pass --serial."
        )
    rec = last_backup(nano.usb_serial if nano else None)
    if nano:
        check_usb_serial(nano.usb_serial)
        size = nano.size
        usb_serial = nano.usb_serial
    elif rec and rec.get("size"):
        size = int(rec["size"])
        usb_serial = rec.get("usb_serial") or None
        if usb_serial:
            check_usb_serial(usb_serial)
    else:
        raise Refused(
            "cannot determine nano size without the device or a backup receipt. "
            "Run ./start backup first, or connect the nano."
        )
    layout = layout_for_disk_size(nano.raw_device, size) if nano else None
    check_disk_size(
        size,
        allow_size=allow_size,
        confirm_untested=confirm_untested,
        what="backup/nano",
        layout=layout,
    )
    check_backup(backup, backup_sha256, size, skip_file=skip_backup_file)

    ab = read_receipt("AB_LOADER_FILES_VERIFIED")
    check_ab_receipt(ab, usb_serial)

    wind3x, tested_bin, note = select_wind3x(need_2015_kind_switch=accept_2015_refresh)
    print(f"flasher: {wind3x}")
    print(f"         {note}")
    if not tested_bin:
        if not accept_untested_flasher:
            raise Refused(
                "this computer has no hardware-tested wInd3x binary (that file is Linux x86-64). "
                "A host-built flasher uses the same source commit and nano7 patch, but it has "
                "not been used on hardware. Pass --i-accept-untested-flasher to continue. "
                "The firmware IMAGE hash is still enforced."
            )
        print(
            "HARDWARE: this flasher binary is untested. The firmware image hash matches "
            "the unit that was flashed on Linux. A host-built wInd3x is the macOS-native "
            "path (libusb DFU, same family as irecovery)."
        )

    if accept_2015_refresh:
        print(
            "HARDWARE: Mid-2015 nano 7G (MKN52 / A1446 Rev A) is untested. "
            "Same 39A10023 MSE, different WTF PID 0x124a. See docs/HARDWARE-2015.md."
        )
    elif late_wtf_present():
        raise Refused(
            "USB already shows WTF 0x124a (Mid-2015). Pass --i-accept-2015-refresh. "
            "See docs/HARDWARE-2015.md."
        )

    attempt = state_dir() / f"FIRMWARE_ATTEMPTED-{mse_hash[:16]}"
    if attempt.exists():
        raise Refused(
            "this exact firmware was already attempted (receipt "
            f"{attempt}). The original rule is one write, no retry, including dry-run. "
            "See docs/RECOVERY.md."
        )

    with MaintenanceLock():
        if dry_run:
            print("==> DFU dry-run (every check, no write)")
            wait_dfu()
            log = prepare_restore(wind3x, serial, accept_2015=accept_2015_refresh)
            seen = parse_serial(log)
            if seen:
                cfg = load_config()
                cfg["DEVICE_SERIAL"] = seen
                save_config(cfg)
                print(f"    device serial {seen} (saved)")
            print("DRY_RUN_OK; all checks passed; no firmware write attempted")
            write_receipt("DRY_RUN_OK", {"utc": utc_stamp(), "serial": seen or serial, "flasher": note})
            return {"dry_run": True, "serial": seen or serial}

        if not accept_brick:
            from .compat import require_typed

            require_typed(
                "FLASH",
                f"About to write firmware {mse_hash[:16]}... to nano serial {serial}. "
                f"This can brick the iPod.",
                typed=typed_confirm,
            )
        print("==> FLASH_START (one write, no retry)")
        wait_dfu()
        # Prepare again immediately before write so identity still matches.
        prepare_restore(wind3x, serial, accept_2015=accept_2015_refresh)
        if not attempt.exists():
            write_receipt(
                attempt.name,
                {"utc": utc_stamp(), "mse_sha256": mse_hash, "serial": serial},
            )
        log = flash_restore(wind3x, mse)
        print("FLASH_OK")
        write_receipt("FLASH_OK", {"utc": utc_stamp(), "serial": serial, "mse": mse_hash})
        return {"dry_run": False, "ok": True, "log_tail": log[-500:]}


def resolve_backup(nano: Nano, backup: Optional[Path], backup_sha256: Optional[str]) -> tuple:
    if backup and backup_sha256:
        return Path(backup), backup_sha256
    rec = last_backup(nano.usb_serial)
    if rec and rec.get("path") and rec.get("sha256"):
        return Path(rec["path"]), rec["sha256"]
    raise Refused(
        "no backup on file for this nano. ./start untether will take one automatically, "
        "or run ./start backup first."
    )


UNTETHER_CHECK_OK = "UNTETHER_CHECK_OK"
UNTETHER_STARTED = "UNTETHER_STARTED"
UNTETHER_OK = "UNTETHER_OK"
UNTETHER_VERIFY_OK = "UNTETHER_VERIFY_OK"
UNTETHER_VERIFY_INCONCLUSIVE = "UNTETHER_VERIFY_INCONCLUSIVE"


def _layout_for_nano(nano: Nano):
    # Pass discovery size: Darwin /dev/rdiskN has st_size 0 and SEEK_END 0.
    return inspect_device(Path(nano.raw_device), nano.size)


def print_mse_plan(mse: Path, ident: dict) -> None:
    from .bootloader import require_family_39

    family = require_family_39()
    print(f"    file       {mse}")
    print(f"    sha256     {ident['sha256']}")
    print(f"    identity   {ident['identity']} ({ident['label']})")
    print(f"    rsrc IMG1  format 0x{ident['rsrc_format']:02x} (patched Helvetica CFF)")
    print(f"    osos bytes {ident['osos_length']}")
    print(f"    Nano7Late  UpdaterFamilyID {family} (want {const.NANO7LATE_UPDATER_FAMILY_ID})")
    print("    write      wInd3x restore --firmware (IPodUpdate firmware kind)")
    print("    rsrc file  /Resources/Fonts/Helvetica.ttf (CFF / CVE-2010-1797)")
    print("               lives in firmware rsrc FAT (IMG1 0x03), not data /Apps")
    print("    data FAT   /Apps/Startup/ab.hbapp + resident load after CFF fires")
    print("    not        --full, --bootloader, IPodRepartition, FAT format, --replace")
    print("    not        iPEMS ByteFau1t, not Finder Option+Restore")
    if ident["identity"] != "39A10023":
        raise Refused("MSE identity is not 39A10023. Nothing written.")
    if family != const.NANO7LATE_UPDATER_FAMILY_ID:
        raise Refused("Nano7Late family is not 39. Nothing written.")


def _require_no_replace(replace: bool) -> None:
    if replace:
        raise Refused(
            "./start untether will not pass --replace. Loader files with a "
            "different hash are refused. FAT and music stay. Nothing written."
        )


def music_markers(nano: Nano) -> dict:
    """Prove the FAT music tree is still there. Never writes."""
    found = []
    songs = 0
    for mp in nano.mountpoints or []:
        root = Path(mp)
        for name in ("iPod_Control", "iTunes_Control"):
            ctrl = root / name
            if ctrl.is_dir():
                found.append(str(ctrl))
                music = ctrl / "Music"
                if music.is_dir():
                    for _dirpath, _dirs, files in os.walk(music):
                        songs += sum(1 for n in files if not n.startswith("."))
    return {"paths": found, "songs": songs, "ok": bool(found)}


def library_db_markers(nano: Nano) -> dict:
    """Read-only look for iTunes library DBs, including torn itdb files."""
    from .library import inspect_library_mount

    for mp in nano.mountpoints or []:
        root = Path(mp)
        if root.is_dir():
            report = inspect_library_mount(root)
            files = []
            for item in report.get("files") or []:
                files.append(
                    {
                        "path": item.get("host") or item.get("path"),
                        "name": item.get("name"),
                        "size": item.get("size"),
                        "ok": item.get("ok"),
                        "truncated": item.get("truncated"),
                        "reason": item.get("reason"),
                    }
                )
            return {
                "files": files,
                "ok": bool(report.get("ok")),
                "truncated": bool(report.get("truncated")),
                "missing": report.get("missing") or [],
                "report": report,
            }
    return {"files": [], "ok": False, "truncated": False, "missing": []}


def _child_ci(parent: Path, name: str) -> Optional[Path]:
    """Case-insensitive directory entry (FAT). Live listing had no Apps/apps."""
    want = name.lower()
    try:
        for child in parent.iterdir():
            if child.name.lower() == want:
                return child
    except OSError:
        return None
    return None


def volume_root_listing(root: Path) -> dict:
    """ls -la of the real mount root, including hidden and iPod_Control/Device."""
    root = Path(root)
    entries = []
    try:
        children = list(root.iterdir())
    except OSError as exc:
        return {
            "root": str(root),
            "error": str(exc),
            "entries": [],
            "apps_name": None,
            "ipod_control": None,
            "device_dir": None,
            "device_names": [],
            "sysinfo": None,
        }
    for p in sorted(children, key=lambda x: x.name.lower()):
        kind = "dir" if p.is_dir() else ("file" if p.is_file() else "other")
        size = None
        mtime = None
        try:
            st = p.stat()
            if p.is_file():
                size = st.st_size
            mtime = int(st.st_mtime)
        except OSError:
            pass
        entries.append(
            {
                "name": p.name,
                "kind": kind,
                "hidden": p.name.startswith("."),
                "size": size,
                "mtime": mtime,
            }
        )
    apps = _child_ci(root, "Apps")
    ctrl = _child_ci(root, "iPod_Control") or _child_ci(root, "iTunes_Control")
    device = _child_ci(ctrl, "Device") if ctrl else None
    device_names = []
    sysinfo = None
    if device and device.is_dir():
        try:
            device_names = sorted(c.name for c in device.iterdir())
        except OSError:
            device_names = []
        hit = _child_ci(device, "SysInfo") or _child_ci(device, "SysInfoExtended")
        if hit is not None:
            sysinfo = str(hit)
    return {
        "root": str(root),
        "error": None,
        "entries": entries,
        "apps_name": apps.name if apps else None,
        "ipod_control": str(ctrl) if ctrl else None,
        "device_dir": str(device) if device else None,
        "device_names": device_names,
        "sysinfo": sysinfo,
    }


def print_volume_root_listing(listing: Optional[dict]) -> None:
    if not listing:
        print("    volume     (no mount root to list)")
        return
    root = listing.get("root") or "?"
    if listing.get("error"):
        print(f"    volume     {root} list failed: {listing['error']}")
        return
    entries = listing.get("entries") or []
    print(f"    volume     {root} ({len(entries)} root entries, hidden included)")
    if not entries:
        print("               (empty root)")
    for item in entries:
        extra = ""
        if item.get("kind") == "file" and item.get("size") is not None:
            extra = f" {item['size']} bytes"
        print(f"               {item['name']}  {item['kind']}{extra}")
    apps_name = listing.get("apps_name")
    if apps_name:
        print(f"               Apps: {apps_name} (case-insensitive match)")
    else:
        print("               Apps: missing (no Apps/apps/APPS on the volume root)")
    if listing.get("ipod_control"):
        print(f"               control {listing['ipod_control']}")
    else:
        print("               iPod_Control: missing")
    if listing.get("device_dir"):
        names = ", ".join(listing.get("device_names") or []) or "(empty)"
        print(f"               Device {listing['device_dir']}: {names}")
    else:
        print("               iPod_Control/Device: missing")
    if listing.get("sysinfo"):
        print(f"               SysInfo {listing['sysinfo']}")
    else:
        print("               SysInfo: not visible (FAT hint only; not a NAND read)")


def apps_markers(nano: Nano) -> dict:
    """Read-only FAT signs of the /Apps copy and whether resident ran.

    lasttrace.bin is a previous-boot hb_trace dump, written only if DRAM
    still held a valid ring (kernel panic). A watchdog or hard reset wipes
    DRAM, so absence after Home does not prove the resident skipped.
    Home icons are the on-device visual. Settings looks stock after
    Helvetica. Never writes. Lists the real volume root (hidden, Apps/apps,
    iPod_Control/Device) so a missing /Apps is visible in the log.
    """
    present = []
    missing = []
    icon_names = []
    lasttrace = False
    lasttrace_text = ""
    lasttrace_error = ""
    heapdiag = False
    disabled = False
    roots = []
    listing = None
    for mp in nano.mountpoints or []:
        root = Path(mp)
        if not root.is_dir():
            continue
        listing = volume_root_listing(root)
        apps = _child_ci(root, "Apps")
        if apps is None or not apps.is_dir():
            continue
        roots.append(str(apps))

        def _file(fat: str) -> Path:
            rel = fat.lstrip("/").replace("\\", "/")
            parts = [p for p in rel.split("/") if p]
            cur = root
            for part in parts:
                nxt = _child_ci(cur, part)
                if nxt is None:
                    return root / rel
                cur = nxt
            return cur

        checks = [
            "/Apps/AllApps.pack",
            "/Apps/Startup/resident.hbapp",
            "/Apps/Startup/ab.hbapp",
            "/Apps/Startup/resident-A.hbapp",
        ]
        for _name, meta in const.TESTED_APPS.items():
            checks.append(meta["fat_exe"])
            checks.append(meta["fat_icon"])
        seen = set()
        for fat in checks:
            if fat in seen:
                continue
            seen.add(fat)
            if _file(fat).is_file():
                present.append(fat)
            else:
                missing.append(fat)
        for name, meta in const.TESTED_APPS.items():
            if _file(meta["fat_exe"]).is_file() and _file(meta["fat_icon"]).is_file():
                icon_names.append(name)
        lt_path = _file("/Apps/Startup/lasttrace.bin")
        if lt_path.is_file():
            lasttrace = True
            present.append("/Apps/Startup/lasttrace.bin")
            try:
                from .apps import decode_lasttrace, format_lasttrace

                lasttrace_text = format_lasttrace(
                    decode_lasttrace(lt_path.read_bytes()), limit=24
                )
            except (Refused, OSError, ValueError) as err:
                lasttrace_error = str(err)
        if _file("/Apps/Startup/heapdiag.bin").is_file():
            heapdiag = True
            present.append("/Apps/Startup/heapdiag.bin")
        if _file("/Apps/Startup/disabled").exists():
            disabled = True
            present.append("/Apps/Startup/disabled")
        break
    loader = any(
        p.endswith("AllApps.pack") or p.endswith("resident.hbapp") for p in present
    )
    return {
        "paths": roots,
        "present": present,
        "missing": missing,
        "icons": icon_names,
        "lasttrace": lasttrace,
        "lasttrace_text": lasttrace_text,
        "lasttrace_error": lasttrace_error,
        "heapdiag": heapdiag,
        "disabled": disabled,
        "loader": loader,
        "ok": loader,
        "listing": listing,
    }


def print_apps_markers(apps: dict) -> None:
    print_volume_root_listing(apps.get("listing"))
    present = apps.get("present") or []
    print(
        f"    FAT /Apps  loader={'yes' if apps.get('ok') else 'no'} "
        f"lasttrace={'yes' if apps.get('lasttrace') else 'no'} "
        f"icons={','.join(apps.get('icons') or []) or 'none'}"
    )
    if present:
        for fat in present:
            print(f"               present {fat}")
    else:
        print("               /Apps not visible on the mount (unmounted or not copied)")
    if apps.get("lasttrace"):
        print("               lasttrace.bin: resident ran and dumped hb_trace to FAT")
        text = apps.get("lasttrace_text") or ""
        err = apps.get("lasttrace_error") or ""
        if err:
            print(f"               lasttrace decode: {err}")
        elif text:
            for line in text.splitlines()[:16]:
                print(f"               {line}")
        print("               full dump: ./start apps lasttrace")
    else:
        print(
            "               lasttrace.bin absent: watchdog/hard reset wipes DRAM, "
            "so the resident has nothing to dump even after Home. Kernel panic "
            "leaves DRAM and writes the file. Per-app log: /Apps/Data/<App>/log.txt"
        )
    if apps.get("disabled"):
        print("               /Apps/Startup/disabled is present")


def print_screen_verify_note() -> None:
    print("    screen     Home and Settings look stock after Helvetica.")
    print("               There is no Settings visual marker of the CFF untether.")
    print("               On-device sign: Blocks, Game Boy, Probe icons on Home")
    print("               (or Settings > About, then Home to refresh).")
    print("               MKN52 hardware SUCCESS after install-untether")
    print("               (DEVICE_FILES_OK, remount rehash matched) + Sleep/Wake+Home:")
    print("               those three icons appeared. Credits: wInd3x, ipod_sun,")
    print("               NanoApps, nano7-untethered.")
    print("               If those icons are missing, FAT /Apps may still be")
    print("               present and resident has not hooked this boot. If")
    print("               /Apps is missing on the volume root, copy it with")
    print("               ./start install-untether (no firmware write), reboot")
    print("               Sleep/Wake+Home, then look for those three Home icons.")
    print("               CFF is in firmware rsrc Helvetica.ttf; resident loads")
    print("               from data FAT. Do not DFU / nand-read the firmware.")
    print("               Game Boy ROMs: ./start roms add FILE.gb (Disk Mode).")


def print_finder_reboot_loop_note(*, closed: int = 0) -> None:
    print(
        f"    finder     Closed {closed} IPOD window pass(es) via osascript. "
        "Finder prefs were not changed."
    )
    print("               Finder auto-opens IPOD after remount. Finder/AMP will")
    print("               touch the device. After Helvetica, RetailOS SCSI can")
    print("               reboot-loop. Unplug if Home starts looping. Later")
    print("               music: ./start sync in Disk Mode.")


def print_verify_host_steps() -> None:
    print("    host       Quit Music.app. Close Finder windows for the IPOD volume.")
    print("               Optional: killall AMPDevicesAgent  (Ventura).")
    print("               Also AMPDeviceDiscoveryAgent on later macOS. Do not")
    print("               permanently unload that LaunchAgent.")
    print("               This tool force-unmounts, sends C6, remounts, then")
    print("               closes Finder IPOD windows. No NAND write. Prefs stay.")
    print("               Finder/Music talking RetailOS SCSI after Helvetica can")
    print("               reboot-loop. Later music: ./start sync in Disk Mode.")


def print_library_db_note(markers: dict, db: dict) -> None:
    from .library import LIBRARY_TRUNCATED, print_library_report

    print("    library    Settings > About 0 songs after Helvetica can be a torn")
    print("               iTunes DB, not a wipe of song files. MKN52: Library.itdb")
    print("               was 1 byte ('S') after remount + DFU; Dynamic.itdb and")
    print("               iTunesCDB were 0 bytes. Firmware '1.1.2 PC' is the")
    print("               FAT32/Winpod About text (expected after IPodRepartition).")
    print("               /Apps copy does not write iPod_Control.")
    if markers.get("ok"):
        print(f"               song files still on FAT: >={markers.get('songs', 0)}")
    report = db.get("report")
    if report:
        print_library_report(report, title="library")
    elif db.get("files"):
        for item in db["files"]:
            flag = "ok" if item.get("ok") else "TRUNCATED"
            print(
                f"               {flag:9} {item.get('path')} {item.get('size')} bytes "
                f"{item.get('reason') or ''}"
            )
        if db.get("truncated"):
            print(f"               {LIBRARY_TRUNCATED}")
    else:
        print("               iTunesCDB / Library.itdb / Dynamic.itdb not visible.")
    torn = bool(db.get("truncated")) or bool(report and (report.get("truncated") or not report.get("ok")))
    if torn or not (report and report.get("ok")):
        print("               Restore the three files from the hashed FAT32 backup:")
        print("               ./start library restore-from-backup --backup IMAGE --backup-sha256 HASH")
        print("               Disk Mode (Do not disconnect while plugged in). No NAND.")
        print("               Or rebuild with Music/Finder in Disk Mode (./start sync).")
        print("               Do not use gtkpod/libgpod. Do not Finder Restore (/Apps).")


def core_loader_hashes() -> dict:
    """Resident + A/B selector. Extra Home apps must not change these."""
    repo = Path(__file__).resolve().parent.parent
    expected = {"/Apps/Startup/resident.hbapp": const.RESIDENT_SHA256}
    for rel, fat in const.AB_FILES:
        src = repo / "untether" / rel
        expected[fat] = sha256_file(src)
    return expected


def expected_loader_hashes(*, apps: Optional[list] = None) -> dict:
    """SHA-256 of the tested loader + selected Home files.

    VERIFY_OK / sync protect use check_loader_hashes, which defaults to
    core_loader_hashes plus a valid HBPK (extras change the pack hash).
    """
    repo = Path(__file__).resolve().parent.parent
    from .apps import loader_files_for, pack_for_selection, tested_app_names

    selected = list(apps) if apps is not None else tested_app_names()
    _pack_bytes, pack_hash, _tested = pack_for_selection(selected)
    expected = {"/Apps/AllApps.pack": pack_hash}
    for rel, fat in loader_files_for(selected):
        src = repo / "untether" / rel
        if src.is_file():
            expected[fat] = sha256_file(src)
    for rel, fat in const.AB_FILES:
        src = repo / "untether" / rel
        expected[fat] = sha256_file(src)
    expected["/Apps/Startup/resident.hbapp"] = const.RESIDENT_SHA256
    return expected


def inspect_pack_on_mount(root: Path) -> dict:
    """Check the Home pack the resident will actually read next boot.

    With resident-B.active on FAT, slot B reads /Apps/AllApps-B.pack and
    /Apps/AllApps.pack is the dormant slot-A recovery pack. apps remove only
    filters the active pack, so a dormant pack may still name an app whose
    exe was just deleted; that is reported (dormant_missing) but does not
    fail the check. restore-tested rewrites AllApps.pack before slot A boots.
    """
    from .apps import home_pack_fat, pack_app_names, parse_pack
    from .fat import MountFat
    from .ports import PACK_A_FAT

    ops = MountFat(root)
    pack_fat = home_pack_fat(ops)
    data = ops.read_bytes(pack_fat)
    if not data:
        return {
            "ok": False,
            "reason": f"{Path(pack_fat).name} missing",
            "names": [],
            "pack_fat": pack_fat,
        }
    try:
        names = pack_app_names(data)
        parse_pack(data)
    except Refused as exc:
        return {"ok": False, "reason": str(exc), "names": []}
    missing = []
    for name in names:
        if not ops.exists(f"/Apps/Executables/{name}.hbapp"):
            missing.append(name)
    dormant_missing: list = []
    if pack_fat != PACK_A_FAT:
        dormant = ops.read_bytes(PACK_A_FAT)
        if dormant:
            try:
                dormant_missing = [
                    n for n in pack_app_names(dormant)
                    if not ops.exists(f"/Apps/Executables/{n}.hbapp")
                ]
            except Refused:
                dormant_missing = []
    return {
        "ok": not missing,
        "pack_fat": pack_fat,
        "names": names,
        "missing_executables": missing,
        "dormant_missing": dormant_missing,
        "sha256": sha256_bytes(data),
        "tested_pack": sha256_bytes(data) == const.ALLAPPS_PACK_SHA256,
        "reason": "missing exe " + ",".join(missing) if missing else "HBPK ok",
    }


def check_core_loader_hashes(nano: Nano) -> dict:
    expected = core_loader_hashes()
    root = None
    for mp in nano.mountpoints or []:
        p = Path(mp)
        if p.is_dir():
            root = p
            break
    if root is not None:
        from .cap import LEAN_MARKER, NOGROW_MARKER, overlay_core_hashes

        marker = root / NOGROW_MARKER.lstrip("/")
        lean = root / LEAN_MARKER.lstrip("/")
        expected = overlay_core_hashes(
            expected, nogrow=marker.is_file(), lean=lean.is_file()
        )
    return check_loader_hashes(nano, expected=expected)


def check_loader_hashes(nano: Nano, expected: Optional[dict] = None) -> dict:
    """Read /Apps hashes from a mounted volume. Never writes.

    Default expected set is the core loader (resident/ab/A), not the
    three-app AllApps.pack hash, so extras do not fail VERIFY_OK / sync.
    """
    expected = dict(expected or core_loader_hashes())
    results = []
    missing = []
    mismatched = []
    matched = 0
    root = None
    for mp in nano.mountpoints or []:
        p = Path(mp)
        if p.is_dir():
            root = p
            break
    if root is None:
        return {
            "ok": False,
            "core_ok": False,
            "matched": 0,
            "expected": len(expected),
            "missing": list(expected),
            "mismatched": [],
            "results": [],
            "root": None,
            "pack": {"ok": False, "names": [], "reason": "unmounted"},
        }
    ops = MountFat(root)
    for fat, digest in expected.items():
        got = ops.read_hash(fat)
        row = {"fat": fat, "expected": digest, "got": got, "ok": got == digest}
        results.append(row)
        if got is None:
            missing.append(fat)
        elif got != digest:
            mismatched.append(fat)
        else:
            matched += 1
    pack = inspect_pack_on_mount(root)
    core_ok = matched == len(expected) and not missing and not mismatched
    return {
        "ok": core_ok and bool(pack.get("ok")),
        "core_ok": core_ok,
        "matched": matched,
        "expected": len(expected),
        "missing": missing,
        "mismatched": mismatched,
        "results": results,
        "root": str(root),
        "pack": pack,
    }


def print_loader_hashes(check: dict) -> None:
    print(
        f"    hashes     {check.get('matched', 0)}/{check.get('expected', 0)} core matched "
        f"root={check.get('root') or 'unmounted'}"
    )
    for fat in check.get("missing") or []:
        print(f"               missing {fat}")
    for fat in check.get("mismatched") or []:
        print(f"               mismatch {fat}")
    pack = check.get("pack") or {}
    names = ",".join(pack.get("names") or []) or "none"
    print(
        f"               pack {pack.get('reason') or 'n/a'} "
        f"tested_pack={'yes' if pack.get('tested_pack') else 'no'} "
        f"apps={names}"
    )


def print_c6_helvetica_note(probe: dict) -> None:
    """C6 96 04 CHECK CONDITION is expected on the Helvetica image."""
    from .scsi import OUTCOME_COULD_NOT_SEND, OUTCOME_DEVICE_REJECTED

    outcome = probe.get("outcome") or ""
    if probe.get("ok"):
        print("    C6 96 04   accepted (star harness). Not required for Helvetica.")
        print("               VERIFY_OK is /Apps present + sha match.")
        print("               On-device sign: Blocks, Game Boy, Probe on Home.")
        return
    if outcome == OUTCOME_DEVICE_REJECTED:
        print("    C6 96 04   CHECK CONDITION (expected under Helvetica).")
        print("               Helvetica CFF publisher is an 88-byte read-only")
        print("               reporter; the stock C6 callsite is left as-is.")
        print("               96 04 is the ipod_sun star harness and is not in")
        print("               this image. Not REFUSED.")
        print("               VERIFY_OK is /Apps present + sha match.")
        print("               On-device sign: Blocks, Game Boy, Probe on Home.")
        return
    if outcome == OUTCOME_COULD_NOT_SEND:
        print("    C6 96 04   not delivered (host/IOKit). Does not prove inactive.")
        print("               VERIFY_OK is still /Apps present + sha match.")
        print("               On-device sign: Blocks, Game Boy, Probe on Home.")
        return
    print("    C6 96 04   not ok. VERIFY_OK is /Apps present + sha match.")


def refuse_verify_probe(probe: dict, apps: Optional[dict] = None) -> None:
    """Called only when /Apps hashes did not match. C6 is not the success gate.

    Could-not-send is not proof the handler is inactive. CHECK CONDITION under
    Helvetica is expected (88-byte reporter, not the star harness). Missing
    /Apps or a sha mismatch is why we refuse: copy with install-untether, do
    not re-flash.
    """
    from .scsi import OUTCOME_COULD_NOT_SEND, OUTCOME_DEVICE_REJECTED

    outcome = probe.get("outcome") or (
        OUTCOME_DEVICE_REJECTED if not probe.get("ok") else "ok"
    )
    apps = apps or {}
    if outcome == OUTCOME_COULD_NOT_SEND and not apps.get("ok"):
        raise Refused(
            "could not send C6 96 04. The SCSI CDB never reached the nano, so this "
            "does not prove the untether handler is inactive. FAT /Apps is also "
            "missing, so automatic-Home cannot load /Apps/Startup/ab.hbapp. On "
            "macOS 13 Intel: quit Music, close Finder IPOD windows, optional "
            "killall AMPDevicesAgent, then retry. The tool rebuilds "
            "ipod_sg_raw/usbscsi into a source-hashed path, force-unmounts "
            "(diskutil unmountDisk force), DADiskClaim, and opens "
            "com_apple_driver_iPodSBCNub keeping the IOCFPlugIn until "
            "CreateSCSITask; if that still fails it tries usbscsi BOT after "
            "USBInterfaceOpenSeize + SetAutoDetach (LIBUSB_ERROR_ACCESS while "
            "IOUSBMassStorageDriver holds iface 0). Copy /Apps with "
            "./start install-untether (no firmware write), reboot Sleep/Wake+Home, "
            "look for Blocks / Game Boy / Probe. FAT and music were not "
            "written. Nothing written. Do not DFU to read NAND."
        )
    if not apps.get("ok"):
        raise Refused(
            "C6 96 04 CHECK CONDITION is expected under Helvetica (88-byte "
            "read-only reporter, not the ipod_sun star harness). CFF is in "
            "firmware rsrc /Resources/Fonts/Helvetica.ttf (IMG1 0x03). That is "
            "not REFUSED by itself. FAT /Apps is missing on the mount, so "
            "automatic-Home cannot load /Apps/Startup/ab.hbapp and icons "
            "cannot appear. That does not prove the Helvetica MSE is missing "
            "(HOME SCREEN after restore --firmware; MKN52 showed Blocks / "
            "Game Boy / Probe after install-untether + Sleep/Wake+Home). "
            "Copy /Apps only with ./start install-untether (no firmware write), "
            "reboot Sleep/Wake+Home, look for those three Home icons. Do not "
            "re-flash. FAT and music were not written. Nothing written."
        )
    raise Refused(
        "FAT /Apps is present but hashes do not match the tested loader files. "
        "C6 96 04 CHECK CONDITION is expected under Helvetica and is not the "
        "failure. Do not re-flash. Copy with ./start install-untether (no "
        "firmware write), reboot Sleep/Wake+Home, look for Blocks / Game Boy / "
        "Probe. FAT and music were not written. Nothing written."
    )


def refuse_verify_inconclusive(probe: dict, apps: dict) -> None:
    """FAT loader present but C6 never sent. Not OK, not handler-inactive."""
    present = ", ".join(apps.get("present") or []) or "loader files"
    lasttrace = "yes" if apps.get("lasttrace") else "no"
    raise Refused(
        f"{UNTETHER_VERIFY_INCONCLUSIVE}: could not send C6 96 04. The SCSI CDB "
        "never reached the nano, so this does not prove the untether handler is "
        f"inactive. FAT loader files are present ({present}). That proves the "
        "/Apps copy, not that Helvetica hooked this boot. lasttrace.bin="
        f"{lasttrace}. Home and Settings look stock after Helvetica; the "
        "on-device sign is Blocks, Game Boy, Probe icons (Settings > About > "
        "Home). If those icons are missing, reboot with /Apps present or run "
        "./start apps install, then look for the three Home icons. Finder/AMP "
        "talking RetailOS SCSI after Helvetica can reboot-loop; IPOD Finder "
        "windows were closed (prefs not changed). FAT and music were not "
        "written. Nothing written. Do not DFU to read the firmware version."
    )


def check_untether_device(
    nano: Nano,
    *,
    mse: Path,
    serial: str,
    allow_size: Optional[int] = None,
    confirm_untested: bool = False,
    accept_2015_refresh: bool = False,
    accept_untested_flasher: bool = False,
    accept_untested_firmware: bool = False,
) -> dict:
    """Booted RetailOS checks. No DFU, no NAND write, no FAT format."""
    from .bootloader import require_family_39
    from .fat import assert_not_hfs
    from .mse import inspect_mse_flash_identity
    from .usb import firmware_only_argv, select_wind3x

    serial = (serial or "").strip()
    if not serial:
        raise Refused(
            "device serial is required (Settings > General > About, or the back). "
            "Pass --serial."
        )
    print("==> untether --check-only (booted FAT32; no DFU; no NAND write)")
    verify_release_artifacts()
    assert_not_hfs(nano)
    check_usb_serial(nano.usb_serial)
    ensure_fat_mount(nano)
    layout = _layout_for_nano(nano)
    print(layout.summary())
    size_info = check_disk_size(
        nano.size,
        allow_size=allow_size,
        confirm_untested=confirm_untested,
        layout=layout,
    )
    if nano.size == const.IPODREPARTITION_DISK_SIZE and not layout.is_ipodrepartition_4k_fat32():
        raise Refused(
            "live size is the IPodRepartition map but MBR/FAT is not 0x0c hidden 64 4K. "
            "Nothing written."
        )
    if nano.size == const.IPODREPARTITION_DISK_SIZE and not accept_2015_refresh:
        raise Refused(
            "this is the Mid-2015 IPodRepartition FAT32 map. "
            "Pass --i-accept-2015-refresh. docs/HARDWARE-2015.md"
        )
    family = require_family_39()
    mse_hash = check_mse(mse, allow_untested=accept_untested_firmware)
    ident = inspect_mse_flash_identity(mse)
    print_mse_plan(mse, ident)
    argv = firmware_only_argv(mse)
    print(f"    argv       {' '.join(argv)}")
    markers = music_markers(nano)
    if markers["ok"]:
        print(f"    music      {markers['paths'][0]} (songs>={markers['songs']})")
    else:
        print("    music      iPod_Control not visible (volume may be unmounted)")
    need_2015 = accept_2015_refresh or nano.size == const.IPODREPARTITION_DISK_SIZE
    wind3x, tested_bin, note = select_wind3x(need_2015_kind_switch=need_2015)
    print(f"flasher: {wind3x}")
    print(f"         {note}")
    if not tested_bin and not accept_untested_flasher:
        raise Refused(
            "this computer has no hardware-tested wInd3x binary. "
            "Pass --i-accept-untested-flasher. The firmware IMAGE hash is still enforced."
        )
    from .state import write_receipt, utc_stamp

    write_receipt(
        UNTETHER_CHECK_OK,
        {
            "utc": utc_stamp(),
            "serial": serial,
            "mse_sha256": mse_hash,
            "identity": ident["identity"],
            "family": str(family),
            "size": str(nano.size),
            "layout": "ipodrepartition_4k_fat32" if layout.is_ipodrepartition_4k_fat32() else layout.scheme,
        },
    )
    print("UNTETHER_CHECK_OK")
    print("    Would copy /Apps loader files (no format, no --replace), then DFU,")
    print("    then restore --firmware only. Bootloader and FAT stay.")
    print("    Finder Option+Restore of an IPSW would reformat data to HFS+ and")
    print("    rewrite bootloader+firmware. Do not use Restore to flash this MSE.")
    return {
        "check_only": True,
        "serial": serial,
        "sha256": mse_hash,
        "identity": ident["identity"],
        "family": family,
        "size": nano.size,
        "size_info": size_info,
        "music": markers,
        "argv": argv,
    }


def write_untether_firmware(
    nano: Nano,
    *,
    mse: Path,
    serial: str,
    backup: Path,
    backup_sha256: str,
    allow_size: Optional[int] = None,
    confirm_untested: bool = False,
    accept_2015_refresh: bool = False,
    accept_untested_flasher: bool = False,
    accept_untested_firmware: bool = False,
    typed_confirm: Optional[str] = None,
    accept_brick: bool = False,
    skip_backup_file: bool = False,
    verify_bytes: bool = True,
    apps: Optional[list] = None,
) -> dict:
    """Firmware-only Helvetica write. No bootloader, no --full, no --replace."""
    from .compat import require_typed
    from .mse import inspect_mse_flash_identity
    from .state import MaintenanceLock, receipt_exists, utc_stamp, write_receipt
    from .usb import (
        flash_restore,
        firmware_only_argv,
        late_wtf_present,
        parse_serial,
        prepare_restore,
        select_wind3x,
        wait_dfu,
    )

    _require_no_replace(False)
    check_untether_device(
        nano,
        mse=mse,
        serial=serial,
        allow_size=allow_size,
        confirm_untested=confirm_untested,
        accept_2015_refresh=accept_2015_refresh,
        accept_untested_flasher=accept_untested_flasher,
        accept_untested_firmware=accept_untested_firmware,
    )
    mse_hash = check_mse(mse, allow_untested=accept_untested_firmware)
    inspect_mse_flash_identity(mse)
    check_backup(
        backup,
        backup_sha256,
        nano.size,
        skip_file=skip_backup_file,
        verify_bytes=verify_bytes,
    )
    print(
        "    /Apps      durable copy (same as ./start install-untether: "
        "fsync, F_FULLFSYNC, unmount, remount rehash, then unmount before DFU)"
    )
    install_loader_files(
        nano,
        backup=backup,
        backup_sha256=backup_sha256,
        allow_size=allow_size,
        confirm_untested=confirm_untested,
        replace=False,
        skip_backup_file=skip_backup_file,
        verify_bytes=verify_bytes,
        apps=apps,
        leave_mounted=False,
    )
    if nano.size == const.IPODREPARTITION_DISK_SIZE and not accept_2015_refresh:
        raise Refused("Pass --i-accept-2015-refresh. docs/HARDWARE-2015.md")
    if late_wtf_present() and not accept_2015_refresh:
        raise Refused(
            "USB already shows WTF 0x124a (Mid-2015). Pass --i-accept-2015-refresh."
        )
    wind3x, tested_bin, note = select_wind3x(
        need_2015_kind_switch=accept_2015_refresh or nano.size == const.IPODREPARTITION_DISK_SIZE
    )
    print(f"flasher: {wind3x}")
    print(f"         {note}")
    if not tested_bin and not accept_untested_flasher:
        raise Refused(
            "this computer has no hardware-tested wInd3x binary. "
            "Pass --i-accept-untested-flasher. The firmware IMAGE hash is still enforced."
        )
    from .state import state_dir

    attempt = state_dir() / f"FIRMWARE_ATTEMPTED-{mse_hash[:16]}"
    if attempt.exists() or receipt_exists(UNTETHER_STARTED):
        raise Refused(
            "this exact firmware was already attempted. One write, no retry, "
            "including a second --run. See docs/RECOVERY.md. Do not pass --replace. "
            "Do not rewrite the bootloader. Finder Option+Restore is last-resort "
            "boot recovery only (that path creates HFS+)."
        )
    argv = firmware_only_argv(mse)
    if not accept_brick:
        require_typed(
            "UNTETHER",
            f"About to write firmware {mse_hash[:16]}... (39A10023 Helvetica) to "
            f"serial {serial}. Bootloader, partition table, and FAT are not written.",
            typed=typed_confirm,
        )
    print("Loader files are in place. Next: recovery (DFU) mode for the firmware write.")
    with MaintenanceLock():
        wait_dfu()
        log = prepare_restore(wind3x, serial, accept_2015=accept_2015_refresh)
        seen = parse_serial(log)
        if seen and seen != serial:
            raise Refused(f"prepare serial {seen} does not match {serial}. Nothing written.")
        print("==> UNTETHER_START (IPodUpdate firmware kind only)")
        write_receipt(
            UNTETHER_STARTED,
            {
                "utc": utc_stamp(),
                "serial": serial,
                "mse_sha256": mse_hash,
                "identity": "39A10023",
            },
        )
        if not attempt.exists():
            write_receipt(
                attempt.name,
                {"utc": utc_stamp(), "mse_sha256": mse_hash, "serial": serial},
            )
        log = flash_restore(wind3x, mse)
    write_receipt(
        UNTETHER_OK,
        {"utc": utc_stamp(), "serial": serial, "mse": mse_hash, "argv": " ".join(argv)},
    )
    print("UNTETHER_OK")
    print("    Wrote Helvetica MSE with restore --firmware only. No bootloader. No FAT format.")
    print("    Keep the cable in until the screen leaves white Do Not Disconnect.")
    print("    After HOME SCREEN: quit Music, close Finder IPOD windows, then:")
    print("      ./start --i-accept-untested-flasher --i-accept-2015-refresh untether --verify --serial " + serial)
    print("    About 0 songs / 1.1.2 PC is the library DB, not a wipe. Disk Mode: ./start sync")
    return {"ok": True, "serial": serial, "sha256": mse_hash, "log_tail": log[-500:], "argv": argv}


def verify_untether(
    nano: Nano,
    *,
    serial: str,
    allow_size: Optional[int] = None,
    confirm_untested: bool = False,
) -> dict:
    """Post-boot: /Apps sha match. C6 96 04 is diagnostic, not the gate. No writes."""
    from .fat import assert_not_hfs
    from .scsi import (
        OUTCOME_COULD_NOT_SEND,
        OUTCOME_DEVICE_REJECTED,
        ensure_scsi_helpers,
        harness_probe,
        peek,
        print_probe_detail,
    )
    from .state import utc_stamp, write_receipt

    print("==> untether --verify (booted; no NAND write; no FAT format)")
    print("    success    /Apps present + sha match. Icons are the on-device sign.")
    print("    C6 96 04   CHECK CONDITION is expected under Helvetica (not REFUSED).")
    assert_not_hfs(nano)
    check_usb_serial(nano.usb_serial)
    layout = _layout_for_nano(nano)
    print(layout.summary())
    check_disk_size(
        nano.size,
        allow_size=allow_size,
        confirm_untested=confirm_untested,
        layout=layout,
    )
    if not layout.is_ipodrepartition_4k_fat32() and nano.size != const.TESTED_DISK_SIZE:
        raise Refused("FAT layout is not the expected IPodRepartition 4K map. FAT was not rewritten.")
    ensure_scsi_helpers()
    ensure_fat_mount(nano)
    markers = music_markers(nano)
    db = library_db_markers(nano)
    apps = apps_markers(nano)
    hashes = check_loader_hashes(nano)
    print_volume_root_listing(apps.get("listing"))
    if markers["ok"]:
        print(f"    FAT music  {markers['paths'][0]} songs>={markers['songs']}")
    else:
        print("    FAT music  iPod_Control not mounted; MBR still matches, volume not formatted")
    print_library_db_note(markers, db)
    print_loader_hashes(hashes)
    if is_macos():
        print_verify_host_steps()
        unmount(nano, force=True)
        time.sleep(0.5)
    probe = {"ok": False, "outcome": "could_not_send", "method": "none", "detail": ""}
    data = b""
    nonzero = False
    closed = 0
    try:
        probe = harness_probe(nano)
        print_probe_detail(probe)
        if probe["ok"]:
            data = peek(nano, const.TRACE_RING, 0x200)
            nonzero = any(data)
            print(f"    peek       {hex(const.TRACE_RING)} nonzero={nonzero} bytes={len(data)}")
    finally:
        if is_macos():
            remounted = ensure_fat_mount(nano, retries=12, delay=0.5)
            if remounted is not None:
                print(f"    remount    {remounted}")
                apps = apps_markers(nano)
                hashes = check_loader_hashes(nano)
            else:
                _, part = _darwin_disk_idents(nano)
                print(
                    f"    remount    diskutil mount /dev/{part} did not restore "
                    f"/Volumes/IPOD; wait for re-enumeration, then: "
                    f"diskutil mount /dev/{part}"
                )
            closed = close_finder_ipod_windows()
            print_finder_reboot_loop_note(closed=closed)
    print_apps_markers(apps)
    print_loader_hashes(hashes)
    print_c6_helvetica_note(probe)
    print_screen_verify_note()
    if not hashes.get("ok"):
        refuse_verify_probe(probe, apps)
    outcome = probe.get("outcome") or OUTCOME_COULD_NOT_SEND
    if probe.get("ok"):
        scsi_field = "yes"
    elif outcome == OUTCOME_DEVICE_REJECTED:
        scsi_field = "expected_reject"
    else:
        scsi_field = "not_sent"
        write_receipt(
            UNTETHER_VERIFY_INCONCLUSIVE,
            {
                "utc": utc_stamp(),
                "serial": serial,
                "scsi_ok": "no",
                "method": str(probe.get("method") or ""),
                "outcome": str(outcome),
                "loader": "yes",
                "hashes": "yes",
                "lasttrace": "yes" if apps.get("lasttrace") else "no",
                "icons": ",".join(apps.get("icons") or []),
                "music": "yes" if markers["ok"] else "unmounted",
                "library_db": "yes" if db["ok"] else "missing",
                "finder_closed": str(closed),
                "note": "C6 not delivered; /Apps sha matched; VERIFY_OK",
            },
        )
    write_receipt(
        UNTETHER_VERIFY_OK,
        {
            "utc": utc_stamp(),
            "serial": serial,
            "scsi_ok": scsi_field,
            "method": str(probe.get("method") or ""),
            "outcome": str(outcome),
            "trace_nonzero": "yes" if nonzero else "no",
            "music": "yes" if markers["ok"] else "unmounted",
            "library_db": "yes" if db["ok"] else "missing",
            "loader": "yes" if apps.get("ok") else "no",
            "hashes": "yes",
            "lasttrace": "yes" if apps.get("lasttrace") else "no",
            "icons": ",".join(apps.get("icons") or []),
        },
    )
    print("UNTETHER_VERIFY_OK")
    print("    /Apps present, sha match. FAT volume and music were not formatted.")
    if outcome == OUTCOME_DEVICE_REJECTED and not probe.get("ok"):
        print("    C6 96 04 CHECK CONDITION is expected under Helvetica (not REFUSED).")
    elif not probe.get("ok"):
        print("    C6 was not delivered. Hashes matched; icons are the on-device sign.")
    else:
        print("    Untether SCSI handler also accepted C6 96 04 (bonus, not required).")
    print("    Home/Settings still look stock; Blocks / Game Boy / Probe are the sign.")
    return {
        "ok": True,
        "probe": probe,
        "music": markers,
        "library_db": db,
        "apps": apps,
        "hashes": hashes,
        "trace_nonzero": nonzero,
    }


def run_untether_command(
    nano: Optional[Nano],
    *,
    check_only: bool,
    run: bool,
    verify: bool,
    mse: Path,
    serial: str,
    backup: Optional[Path] = None,
    backup_sha256: Optional[str] = None,
    allow_size: Optional[int] = None,
    confirm_untested: bool = False,
    accept_2015_refresh: bool = False,
    accept_untested_flasher: bool = False,
    accept_untested_firmware: bool = False,
    typed_confirm: Optional[str] = None,
    accept_brick: bool = False,
    skip_backup_file: bool = False,
    verify_bytes: bool = True,
    apps: Optional[list] = None,
    replace: bool = False,
) -> dict:
    _require_no_replace(replace)
    if run and check_only:
        raise Refused("--check-only cannot be combined with --run")
    if verify and run:
        raise Refused("--verify is post-boot; do not combine with --run")
    if verify and check_only:
        raise Refused("--verify cannot be combined with --check-only")
    if nano is None:
        raise Refused(
            "no iPod found. For --check-only and --verify the nano must be booted "
            "normally (Connected / FAT32), not DFU. For --run start booted so "
            "backup and /Apps copy can run, then you will DFU."
        )
    if verify:
        return verify_untether(
            nano,
            serial=serial,
            allow_size=allow_size,
            confirm_untested=confirm_untested,
        )
    if not run:
        return check_untether_device(
            nano,
            mse=mse,
            serial=serial,
            allow_size=allow_size,
            confirm_untested=confirm_untested,
            accept_2015_refresh=accept_2015_refresh,
            accept_untested_flasher=accept_untested_flasher,
            accept_untested_firmware=accept_untested_firmware,
        )
    if backup is None or not backup_sha256:
        raise Refused("untether --run needs a hashed backup of THIS live FAT32 nano")
    return write_untether_firmware(
        nano,
        mse=mse,
        serial=serial,
        backup=backup,
        backup_sha256=backup_sha256,
        allow_size=allow_size,
        confirm_untested=confirm_untested,
        accept_2015_refresh=accept_2015_refresh,
        accept_untested_flasher=accept_untested_flasher,
        accept_untested_firmware=accept_untested_firmware,
        typed_confirm=typed_confirm,
        accept_brick=accept_brick,
        skip_backup_file=skip_backup_file,
        verify_bytes=verify_bytes,
        apps=apps,
    )
