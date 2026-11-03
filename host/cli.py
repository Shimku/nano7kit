""" ./start – one entry point on macOS and Linux.

Device work is either Mac-native (diskutil + mtools + libusb wInd3x) or
driven on a Raspberry Pi over SSH. The operator only runs commands here.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .compat import Refused, is_macos, is_root, reexec_as_root, which
from .const import FIRMWARE_MSE_SHA256, HARDWARE_TESTED_SUMMARY, TESTED_DISK_SIZE, TRACE_RING
from .device import format_nano, find_nanos, require_one_nano
from .safety import backup_verify_size, check_backup, check_mse
from .state import (
    config_file,
    last_backup,
    load_config,
    nanoapps_config_sync,
    save_config,
)


def _json_mode(args) -> bool:
    return bool(getattr(args, "json", False))


def configured_mode() -> str:
    return load_config().get("MODE") or "local"


def configured_remote() -> Optional[str]:
    cfg = load_config()
    if cfg.get("MODE") == "ssh":
        return cfg.get("REMOTE")
    return None


def use_ssh(args) -> bool:
    """SSH device path unless --local forces USB on this computer."""
    if getattr(args, "local", False):
        return False
    if getattr(args, "cmd", None) == "setup":
        return False
    if getattr(args, "remote", None) and getattr(args, "cmd", None) != "setup":
        return True
    return configured_mode() == "ssh"


def ssh_host(args) -> str:
    if getattr(args, "remote", None) and getattr(args, "cmd", None) != "setup":
        return args.remote
    from .remote import configured_host

    return configured_host()


def needs_root_for(cmd: str) -> bool:
    return cmd in {
        "backup",
        "backup-stream",
        "unmount",
        "fsck",
        "install-untether",
        "install",
        "roms",
        "library",
        "flash",
        "untether",
        "install-stage",
        "scsi-probe",
        "bootloader",
    }


def maybe_sudo(cmd: str) -> None:
    if needs_root_for(cmd) and not is_root():
        print("re-running with sudo so the raw disk / USB device can be opened...")
        reexec_as_root()


def disk_flags(args) -> dict:
    return {
        "allow_size": getattr(args, "allow_disk_size", None),
        "confirm_untested": getattr(args, "confirm_untested_capacity", False),
    }


def _selected_apps(args) -> Optional[list]:
    raw = getattr(args, "apps", None)
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        parts: list[str] = []
        for item in raw:
            parts.extend(x.strip() for x in str(item).split(",") if x.strip())
        return parts
    return [x.strip() for x in str(raw).split(",") if x.strip()]


def _typed(args) -> Optional[str]:
    return getattr(args, "typed_confirm", None) or None


def cmd_doctor(args) -> int:
    from .scsi import scsi_available_local
    from .usb import (
        apple_pids,
        dfu_present,
        host_wind3x_path,
        select_wind3x,
        tested_wind3x,
        usb_revision_notes,
    )

    print("nano7kit doctor")
    print(f"  os                 {sys.platform} ({os.uname().machine if hasattr(os, 'uname') else ''})")
    print(f"  config             {config_file()} (mode={configured_mode()} remote={configured_remote() or '-'})")
    print(f"  hardware-tested    {HARDWARE_TESTED_SUMMARY}")
    print()
    print("  What this computer can do")
    print("  -------------------------")
    print("  build apps          yes (ARM gcc + LVGL + Rust; Homebrew, apt, or Intel micromamba bootstrap)")
    print("  build firmware      yes (downloads Apple IPSW; image hash enforced)")
    print("  find nano           yes (macOS diskutil / Linux by-id)")
    print("  backup + FAT copy   yes (mounted /Volumes/IPOD on macOS; mtools otherwise)")
    print("  fsck                yes (fsck_msdos / fsck.vfat)")
    try:
        path, tested, note = select_wind3x()
        print(f"  DFU flash           {path.name}: {note}")
    except Refused as e:
        print(f"  DFU flash           not ready ({e})")
        print(f"                      host build would be {host_wind3x_path()}")
    ok, method, detail = scsi_available_local()
    print(f"  vendor SCSI (C6)    {'yes' if ok else 'no'} ({method}: {detail})")
    print(f"  DFU currently seen  {dfu_present()}")
    pids = apple_pids()
    shown = ", ".join(f"05ac:{pid:04x}" for pid in pids) if pids else "none"
    print(f"  Apple USB now       {shown}")
    for note in usb_revision_notes():
        print(f"                      {note}")
    print()
    print("  Tested")
    print("  ------")
    print("  firmware image      " + FIRMWARE_MSE_SHA256[:16] + "... (upstream: one 2012 16 GB nano, Linux)")
    print("  this toolkit        one Mid-2015 16 GB nano (MKN52), Intel Mac, macOS 13")
    print("  on that nano        Blocks, Game Boy, Probe, extras one per reboot, Mario 64,")
    print("                      Temple Run, Fruit Ninja, PvZ Portable, PocketJS")
    print("  not yet tested      2012 nanos on a Mac, Apple silicon Macs, other colours")
    print()
    print("  Commands")
    print("  --------")
    print("  Easy way:           ./install  (menu: pick apps and games, it does the rest)")
    print("  One command:        ./start install  (check, backup, flash, /Apps, verify;")
    print("                      skips NAND when FIRMWARE_ATTEMPTED exists;")
    print("                      bootloader only with --with-bootloader)")
    print("  Apps:               ./start apps list | add Paint | remove | lasttrace |")
    print("                      restore-tested (Disk Mode; one extra per reboot)")
    print("                      Blocks A/B: ./start apps remove Blocks --keep-files")
    print("                      Mario 64: ./start apps add Mario --rom /path/to/sm64-usa.z64")
    print("                      Home holds 16 apps with the Launcher (8 on the old one)")
    print("  PocketJS:           ./start pocketjs packages  (after apps add 'JS QuickJS')")
    print("  Music sync:         ./start sync  (Disk Mode, then Music or Finder)")
    print("  Torn library DBs:   ./start library restore-from-backup")
    print("  Game Boy ROMs:      ./start roms add FILE.gb")
    print("  Memory:             ./start perf")
    print("  Disk Mode screen:   Do not disconnect")
    print("  Recovery:           ./start apps lasttrace && ./start apps restore-tested")
    print("  Disk size allow-list:", TESTED_DISK_SIZE, "bytes, or 15806623744 after IPodRepartition")
    print("  Docs:               docs/BUTTONS.md  docs/RECOVERY.md  docs/GAMES.md")
    if configured_mode() == "ssh":
        print()
        print(f"  Remote Pi           {configured_remote()} (all device ops go there; backups stream home)")
    try:
        from .library import inspect_attached_or_none, print_library_report

        report = inspect_attached_or_none()
        if report is not None:
            print()
            print("  Attached library DBs")
            print("  --------------------")
            print_library_report(report, title="library")
    except Refused:
        pass
    return 0


def cmd_deps(args) -> int:
    from .deps import ensure_deps

    n = ensure_deps(check_only=getattr(args, "check", False), device=True)
    return 1 if n and getattr(args, "check", False) else 0


def cmd_setup(args) -> int:
    print("Where is the iPod nano plugged in?")
    print("  1. This computer (Mac-native USB: diskutil/mtools/libusb DFU)")
    print("  2. A Raspberry Pi / Linux box (this computer drives it over SSH)")
    if getattr(args, "remote", None):
        choice = "2"
        host = args.remote
    elif getattr(args, "local", False):
        choice = "1"
        host = None
    elif sys.stdin.isatty():
        choice = input("choose [1/2]: ").strip() or "1"
        host = None
    else:
        raise Refused("non-interactive setup needs --local or --remote user@host")
    cfg = load_config()
    if choice == "1":
        if is_macos():
            print("Mac-native device path selected.")
            print("  find/backup/FAT/fsck: diskutil + mtools (untested on hardware, same checks as Linux)")
            print("  DFU flash: host-built wInd3x via libusb (nano 7G uses s5late, not the Linux-only n4g/n5g exploit)")
            print("  vendor SCSI: libusb BOT helper; if the kernel will not release the disk, use option 2")
        cfg["MODE"] = "local"
        cfg.pop("REMOTE", None)
        cfg.pop("REMOTE_ROOT", None)
        save_config(cfg)
        nanoapps_config_sync("local")
        print(f"saved {config_file()} (local)")
        return 0
    if not host:
        host = input("  SSH host (user@pi.local): ").strip()
    if not host:
        raise Refused("no SSH host")
    from .remote import setup_remote

    remote_cfg = setup_remote(host)
    cfg.update(remote_cfg)
    save_config(cfg)
    nanoapps_config_sync("ssh", host)
    print(f"saved {config_file()} (ssh {host})")
    print("From now on, run ./start on this computer only. The Pi is driven automatically.")
    return 0


def cmd_find(args) -> int:
    if use_ssh(args):
        from .remote import find_remote

        nanos = find_remote(ssh_host(args))
    else:
        nanos = find_nanos()
    if _json_mode(args):
        print(json.dumps({"devices": [n.as_dict() for n in nanos]}, indent=2))
        return 0 if nanos else 1
    if not nanos:
        print("No iPod found. Connect it and wait for it to show Connected.")
        if not use_ssh(args):
            from .usb import usb_revision_notes

            for note in usb_revision_notes():
                print(f"  {note}")
        return 1
    for n in nanos:
        print(format_nano(n))
        print()
    if not use_ssh(args):
        from .usb import usb_revision_notes

        for note in usb_revision_notes():
            print(f"  {note}")
    return 0


def _pick_nano(args):
    if use_ssh(args):
        from .remote import find_remote

        nanos = find_remote(ssh_host(args))
        if not nanos:
            raise Refused("no iPod found on the Raspberry Pi")
        if len(nanos) > 1:
            raise Refused("more than one iPod on the Pi; unplug extras")
        if getattr(args, "device", None):
            for n in nanos:
                if n.device == args.device or n.raw_device == args.device:
                    return n
            raise Refused(f"device {args.device} not found on the Pi")
        return nanos[0]
    if getattr(args, "device", None):
        for n in find_nanos():
            if n.device == args.device or n.raw_device == args.device:
                return n
        raise Refused(f"device {args.device} not found")
    return require_one_nano()


def _nabase_root(args):
    from .fat import mounted_root

    vol = getattr(args, "volume", None)
    if vol:
        root = Path(vol).expanduser()
        if not root.is_dir():
            raise Refused(f"{root} is not a mounted volume")
        return root
    nano = require_one_nano()
    root = mounted_root(nano)
    if root is None:
        raise Refused("the iPod is not mounted: put it in Disk Mode and let Finder show it")
    return root


def cmd_nabase(args) -> int:
    """nano7kit on the NanoApps 1.0 base: file copies only, no firmware, no root."""
    from . import nabase

    sub = getattr(args, "nabase_cmd", None) or "status"
    if sub == "flash":
        return _cmd_nabase_flash(args)
    if sub == "ipsw":
        name, digest = nabase.NA_IPSW[args.model]
        path = nabase.fetch_release_file(name, digest)
        print(f"NANOAPPS_IPSW_OK {path}")
        print("In Finder, select the iPod, hold Option and click Check for Update, then pick this file.")
        print("Do not click Restore.")
        return 0
    root = _nabase_root(args)
    if sub == "status":
        print("\n".join(nabase.status_lines(root)))
        return 0
    if sub == "mark":
        nabase.mark_base(nabase.Volume(root), args.base)
        print(f"NABASE_MARK_OK {args.base}")
        return 0
    if sub == "backup":
        nabase.backup(root)
        print("NABASE_BACKUP_OK")
        return 0
    if sub == "disk-files":
        nabase.install_disk_files(root)
        print("NABASE_DISK_FILES_OK")
        return 0
    if sub == "launcher":
        nabase.install_launcher(root)
        print("NABASE_LAUNCHER_OK")
        return 0
    if sub == "restore":
        nabase.restore_launcher(root)
        print("NABASE_RESTORE_OK")
        return 0
    if sub == "add":
        from .ports import prepare_for_add

        rom = getattr(args, "rom", None)
        payload = getattr(args, "payload", None)
        for name in args.names:
            prepare_for_add(name, rom=Path(rom).expanduser() if rom else None,
                            payload=Path(payload).expanduser() if payload else None)
        nabase.add(root, args.names)
        print("APPS_OK")
        return 0
    if sub == "remove":
        nabase.remove(root, args.names)
        print("APPS_OK")
        return 0
    if sub == "packages":
        nabase.packages(root)
        print("POCKETJS_PACKAGES_OK")
        return 0
    raise Refused(f"unknown nabase command {sub}")


def _cmd_nabase_flash(args) -> int:
    """NanoApps 1.0 firmware through DFU when Finder cannot update the iPod."""
    from . import nabase_fw
    from .usb import build_wind3x, host_wind3x_path

    if getattr(args, "allow_disk_size", None) is not None or getattr(args, "confirm_untested_capacity", False):
        raise Refused("nabase flash takes no disk size overrides. Nothing written.")
    run = bool(getattr(args, "run", False))
    if run and getattr(args, "dry_run", False):
        raise Refused("pass --dry-run or --run, not both")
    if not getattr(args, "backup", None) or not getattr(args, "backup_sha256", None):
        raise Refused("pass --backup IMAGE and --backup-sha256 HASH from ./start backup")
    backup = Path(args.backup).expanduser().resolve()
    if not backup.is_file():
        raise Refused(f"backup image {backup} is not on this Mac. Run ./start backup first.")
    serial = (getattr(args, "serial", None) or load_config().get("DEVICE_SERIAL") or "").strip()
    if not serial:
        raise Refused("device serial is required (Settings > General > About, or the back). Pass --serial.")
    if not is_root():
        print("==> NanoApps 1.0 firmware image (no device write)")
        mse = nabase_fw.extract_mse()
        if not host_wind3x_path().is_file():
            try:
                build_wind3x()
            except subprocess.CalledProcessError as exc:
                raise Refused(
                    "could not build wInd3x (needs Go and libusb). On an Intel Mac run "
                    "./scripts/macos-intel-bootstrap.sh, then source build/macos-intel/env.sh "
                    "and try again. Nothing written.") from exc
        print("re-running the DFU step with sudo so the USB device can be opened...")
        argv = _parent_device_flags(args) + [
            "nabase", "flash", "--mse", str(mse), "--backup", str(backup),
            "--backup-sha256", args.backup_sha256, "--serial", serial,
            "--run" if run else "--dry-run"]
        reexec_as_root(argv=argv)
    mse = Path(args.mse) if getattr(args, "mse", None) else nabase_fw.extract_mse()
    nabase_fw.flash(
        mse=mse,
        backup=backup,
        backup_sha256=args.backup_sha256,
        serial=serial,
        dry_run=not run,
        accept_2015_refresh=getattr(args, "i_accept_2015_refresh", False),
        accept_untested_flasher=getattr(args, "i_accept_untested_flasher", False),
        typed_confirm=_typed(args),
    )
    return 0


def cmd_backup(args) -> int:
    from .backup import backup_local, backup_path_for
    from .remote import stream_backup

    nano = _pick_nano(args)
    dest = Path(args.out).expanduser() if getattr(args, "out", None) else backup_path_for(nano)
    flags = disk_flags(args)
    if use_ssh(args):
        stream_backup(ssh_host(args), nano, dest, **flags)
    else:
        backup_local(nano, dest, **flags)
    return 0


def cmd_backup_stream(args) -> int:
    from .backup import backup_stream_stdout

    nano = _pick_nano(args)
    backup_stream_stdout(nano)
    return 0


def cmd_unmount(args) -> int:
    from .fat import unmount
    from .remote import remote_unmount

    nano = _pick_nano(args)
    if use_ssh(args):
        remote_unmount(ssh_host(args), nano)
    else:
        unmount(nano)
    print("unmounted (not ejected)")
    return 0


def cmd_fsck(args) -> int:
    from .fat import fsck
    from .remote import remote_fsck

    nano = _pick_nano(args)
    if use_ssh(args):
        remote_fsck(ssh_host(args), nano)
    else:
        print(fsck(nano))
    return 0


def cmd_build_firmware(args) -> int:
    from .firmware import build_firmware

    build_firmware(Path(args.ipsw) if getattr(args, "ipsw", None) else None)
    return 0


def cmd_verify(args) -> int:
    from .safety import verify_release_artifacts

    verify_release_artifacts()
    print("release verification passed")
    return 0


def cmd_install_untether(args) -> int:
    from .backup import select_untether_backup
    from .untether import install_loader_files
    from .remote import remote_python

    nano = _pick_nano(args)
    flags = disk_flags(args)
    info = select_untether_backup(
        nano,
        Path(args.backup).expanduser() if getattr(args, "backup", None) else None,
        getattr(args, "backup_sha256", None),
        **flags,
    )
    backup, bhash = Path(info["path"]), info["sha256"]
    if use_ssh(args):
        # Parent-parser flags must precede the subcommand (argparse).
        argv = ["--allow-disk-size", str(nano.size)]
        if flags["confirm_untested"]:
            argv.append("--confirm-untested-capacity")
        argv += [
            "install-untether",
            "--device",
            nano.device,
            "--backup",
            str(backup),
            "--backup-sha256",
            bhash,
            "--skip-backup-file",
        ]
        if getattr(args, "replace", False):
            argv.append("--replace")
        selected = _selected_apps(args)
        if selected is not None:
            argv += ["--apps", ",".join(selected)]
        r = remote_python(
            ssh_host(args),
            argv,
            sudo=True,
        )
        sys.stdout.write(r.stdout or "")
        sys.stderr.write(r.stderr or "")
        return r.returncode
    print("==> install-untether: copy /Apps only (no firmware write, no DFU)")
    install_loader_files(
        nano,
        backup=backup,
        backup_sha256=bhash,
        replace=getattr(args, "replace", False),
        skip_backup_file=getattr(args, "skip_backup_file", False),
        verify_bytes=not info.get("reused"),
        apps=_selected_apps(args),
        leave_mounted=True,
        **flags,
    )
    print("    No firmware write. Do not DFU. Do not re-flash.")
    print("    Reboot Sleep/Wake+Home. Look for Blocks, Game Boy, Probe on Home.")
    print("    That tests Helvetica CFF (firmware rsrc) loading resident from FAT.")
    return 0


def _install_forward_argv(args) -> list:
    forwarded = _parent_device_flags(args)
    forwarded += ["install"]
    if getattr(args, "run", False):
        forwarded.append("--run")
    if getattr(args, "serial", None):
        forwarded += ["--serial", args.serial]
    if getattr(args, "device", None):
        forwarded += ["--device", args.device]
    if getattr(args, "mse", None):
        forwarded += ["--mse", str(args.mse)]
    if getattr(args, "backup", None):
        forwarded += ["--backup", str(args.backup)]
    if getattr(args, "backup_sha256", None):
        forwarded += ["--backup-sha256", args.backup_sha256]
    selected = _selected_apps(args)
    if selected is not None:
        forwarded += ["--apps", ",".join(selected)]
    return forwarded


def cmd_install(args) -> int:
    """One command: check, backup, bootloader-if-needed, flash, /Apps, verify."""
    from .backup import select_untether_backup
    from .deps import ensure_deps
    from .firmware import build_firmware, default_mse
    from .install import run_install
    from .remote import remote_python
    from .safety import check_mse

    run = bool(getattr(args, "run", False))
    phase = os.environ.get("NANO7KIT_DEVICE_PHASE") == "1"
    if not phase:
        print("==> dependencies / firmware image (no device write)")
        ensure_deps(check_only=False)
        mse = Path(args.mse).expanduser() if getattr(args, "mse", None) else default_mse()
        if not mse.is_file():
            build_firmware()
        check_mse(mse, allow_untested=getattr(args, "i_accept_untested_firmware", False))
        if not use_ssh(args) and not is_root():
            print("re-running device steps with sudo so the raw disk / USB device can be opened...")
            os.environ["NANO7KIT_DEVICE_PHASE"] = "1"
            reexec_as_root(argv=_install_forward_argv(args))
    mse = Path(args.mse).expanduser() if getattr(args, "mse", None) else default_mse()
    check_mse(mse, allow_untested=getattr(args, "i_accept_untested_firmware", False))

    if use_ssh(args):
        r = remote_python(ssh_host(args), _install_forward_argv(args), sudo=True)
        sys.stdout.write(r.stdout or "")
        sys.stderr.write(r.stderr or "")
        return r.returncode

    print("==> find nano (must be booted normally, not DFU)")
    nano = _pick_nano(args)
    print(format_nano(nano))
    serial = _device_serial(args, nano)
    flags = disk_flags(args)
    info = select_untether_backup(
        nano,
        Path(args.backup).expanduser() if getattr(args, "backup", None) else None,
        getattr(args, "backup_sha256", None),
        **flags,
    )
    backup, bhash = Path(info["path"]), info["sha256"]
    run_install(
        nano,
        mse=mse,
        serial=serial,
        backup=backup,
        backup_sha256=bhash,
        run=run,
        accept_2015_refresh=getattr(args, "i_accept_2015_refresh", False),
        accept_untested_flasher=getattr(args, "i_accept_untested_flasher", False),
        accept_untested_firmware=getattr(args, "i_accept_untested_firmware", False),
        typed_confirm=_typed(args),
        with_bootloader=getattr(args, "with_bootloader", False),
        accept_brick=getattr(args, "i_accept_brick_risk", False) and getattr(args, "yes", False),
        apps=_selected_apps(args),
        skip_backup_file=getattr(args, "skip_backup_file", False),
        verify_bytes=not info.get("reused"),
        **flags,
    )
    return 0


def cmd_roms(args) -> int:
    """Disk Mode copy of Game Boy ROMs into /Apps/Data/GameBoy."""
    from .roms import add_roms
    from .remote import remote_python

    sub = getattr(args, "roms_cmd", None) or "add"
    if sub != "add":
        raise Refused(f"unknown roms command {sub}")
    files = [Path(p) for p in (getattr(args, "files", None) or [])]
    if not files:
        raise Refused("pass one or more .gb / .gbc files: ./start roms add FILE.gb")
    if use_ssh(args):
        raise Refused(
            "roms add copies local files onto the nano. Run it on the Mac that "
            "holds the .gb files (Disk Mode), not over SSH."
        )
    nano = None
    try:
        nano = _pick_nano(args)
    except Refused:
        nano = None
    add_roms(
        nano,
        files,
        skip_wait=getattr(args, "skip_wait", False),
        watch=getattr(args, "watch", False),
    )
    return 0


def _device_serial(args, nano) -> str:
    s = getattr(args, "serial", None) or load_config().get("DEVICE_SERIAL")
    if s:
        return s
    if not sys.stdin.isatty():
        raise Refused("device serial is required (Settings > General > About, or the back). Pass --serial.")
    s = input("Nano serial (Settings > General > About, or engraved): ").strip()
    if not s:
        raise Refused("no serial")
    cfg = load_config()
    cfg["DEVICE_SERIAL"] = s
    save_config(cfg)
    return s


def _resolve_flash_backup(args, nano) -> tuple:
    rec = last_backup(nano.usb_serial if nano else None)
    if getattr(args, "backup", None):
        backup = Path(args.backup)
        bhash = getattr(args, "backup_sha256", None) or (rec or {}).get("sha256") or ""
        if not bhash:
            raise Refused("pass --backup-sha256 with --backup")
        return backup, bhash
    if rec and rec.get("path") and rec.get("sha256"):
        return Path(rec["path"]), rec["sha256"]
    if nano:
        from .untether import resolve_backup

        return resolve_backup(nano, None, None)
    raise Refused("no backup on file. Run ./start backup first, or ./start untether.")


def cmd_flash(args) -> int:
    from .firmware import default_mse
    from .untether import flash_firmware
    from .remote import configured_remote_root, remote_python, scp_mse_if_needed

    mse = Path(args.mse) if getattr(args, "mse", None) else default_mse()
    dry = not getattr(args, "run", False)
    if getattr(args, "dry_run", False):
        dry = True
    serial = getattr(args, "serial", None) or load_config().get("DEVICE_SERIAL") or ""
    if not str(serial).strip():
        raise Refused(
            "device serial is required (Settings > General > About, or the back). Pass --serial."
        )
    nano = None
    try:
        nano = _pick_nano(args)
    except Refused:
        nano = None
    backup, bhash = _resolve_flash_backup(args, nano)
    rec = last_backup(nano.usb_serial if nano else None)
    if nano:
        size = nano.size
    elif rec and rec.get("size"):
        size = int(rec["size"])
    else:
        raise Refused(
            "cannot determine nano size without the device or a backup receipt. "
            "Run ./start backup first, or connect the nano."
        )
    check_backup(backup, bhash, size, skip_file=getattr(args, "skip_backup_file", False))
    allow_untested_fw = getattr(args, "i_accept_untested_firmware", False)
    check_mse(mse, allow_untested=allow_untested_fw)
    flags = disk_flags(args)
    untested = getattr(args, "i_accept_untested_flasher", False)
    if use_ssh(args):
        host = ssh_host(args)
        remote_mse = f"{configured_remote_root()}/build/firmware/Firmware-untethered.MSE"
        scp_mse_if_needed(host, mse, remote_mse)
        # Parent-parser flags must precede the subcommand (argparse).
        argv = ["--allow-disk-size", str(size)]
        if flags["confirm_untested"]:
            argv.append("--confirm-untested-capacity")
        if getattr(args, "i_accept_brick_risk", False):
            argv += ["--i-accept-brick-risk", "--yes"]
        if untested:
            argv.append("--i-accept-untested-flasher")
        if getattr(args, "i_accept_2015_refresh", False):
            argv.append("--i-accept-2015-refresh")
        if allow_untested_fw:
            argv.append("--i-accept-untested-firmware")
        if _typed(args):
            argv += ["--typed-confirm", _typed(args)]
        argv += [
            "flash",
            "--mse",
            remote_mse,
            "--backup",
            str(backup),
            "--backup-sha256",
            bhash,
            "--skip-backup-file",
            "--serial",
            serial,
        ]
        argv.append("--dry-run" if dry else "--run")
        r = remote_python(host, argv, sudo=True)
        sys.stdout.write(r.stdout or "")
        sys.stderr.write(r.stderr or "")
        return r.returncode
    flash_firmware(
        nano,
        mse=mse,
        backup=backup,
        backup_sha256=bhash,
        serial=serial,
        dry_run=dry,
        accept_brick=getattr(args, "i_accept_brick_risk", False) and getattr(args, "yes", False),
        skip_backup_file=getattr(args, "skip_backup_file", False),
        accept_untested_flasher=untested,
        accept_2015_refresh=getattr(args, "i_accept_2015_refresh", False),
        accept_untested_firmware=allow_untested_fw,
        typed_confirm=_typed(args),
        **flags,
    )
    return 0


def _untether_forward_argv(args) -> list:
    forwarded = _parent_device_flags(args)
    forwarded += ["untether"]
    if getattr(args, "check_only", False) and not getattr(args, "run", False):
        forwarded.append("--check-only")
    if getattr(args, "run", False):
        forwarded.append("--run")
    if getattr(args, "verify", False):
        forwarded.append("--verify")
    if getattr(args, "serial", None):
        forwarded += ["--serial", args.serial]
    if getattr(args, "device", None):
        forwarded += ["--device", args.device]
    if getattr(args, "mse", None):
        forwarded += ["--mse", str(args.mse)]
    if getattr(args, "backup", None):
        forwarded += ["--backup", str(args.backup)]
    if getattr(args, "backup_sha256", None):
        forwarded += ["--backup-sha256", args.backup_sha256]
    selected = _selected_apps(args)
    if selected is not None:
        forwarded += ["--apps", ",".join(selected)]
    return forwarded


def cmd_untether(args) -> int:
    """Guarded Helvetica flash: --check-only (booted), --run (DFU firmware-only), --verify."""
    from .deps import ensure_deps
    from .firmware import build_firmware, default_mse
    from .remote import remote_python
    from .untether import run_untether_command

    if getattr(args, "replace", False):
        raise Refused(
            "./start untether will not pass --replace. Loader-file mismatches are "
            "refused. FAT and music stay. Nothing written."
        )
    run = bool(getattr(args, "run", False))
    verify = bool(getattr(args, "verify", False))
    check_only = bool(getattr(args, "check_only", False))
    if not run and not verify:
        check_only = True
    if run and getattr(args, "check_only", False):
        raise Refused("--check-only cannot be combined with --run")
    if verify and run:
        raise Refused("--verify is post-boot; do not combine with --run")
    if verify and getattr(args, "check_only", False):
        raise Refused("--verify cannot be combined with --check-only")

    phase = os.environ.get("NANO7KIT_DEVICE_PHASE") == "1"
    if not phase:
        if not verify:
            print("==> dependencies / firmware image (no device write)")
            ensure_deps(check_only=False)
            mse = Path(args.mse).expanduser() if getattr(args, "mse", None) else default_mse()
            if not mse.is_file():
                build_firmware()
            check_mse(mse, allow_untested=getattr(args, "i_accept_untested_firmware", False))
        if not use_ssh(args) and not is_root():
            print("re-running device steps with sudo so the raw disk / USB device can be opened...")
            os.environ["NANO7KIT_DEVICE_PHASE"] = "1"
            reexec_as_root(argv=_untether_forward_argv(args))
    mse = Path(args.mse).expanduser() if getattr(args, "mse", None) else default_mse()
    if not verify:
        check_mse(mse, allow_untested=getattr(args, "i_accept_untested_firmware", False))

    if use_ssh(args):
        r = remote_python(ssh_host(args), _untether_forward_argv(args), sudo=True)
        sys.stdout.write(r.stdout or "")
        sys.stderr.write(r.stderr or "")
        return r.returncode

    print("==> find nano (must be booted normally, not DFU)")
    nano = _pick_nano(args)
    print(format_nano(nano))
    serial = _device_serial(args, nano)
    flags = disk_flags(args)
    backup = None
    bhash = None
    verify_bytes = True
    if run:
        from .backup import select_untether_backup

        info = select_untether_backup(
            nano,
            Path(args.backup).expanduser() if getattr(args, "backup", None) else None,
            getattr(args, "backup_sha256", None),
            **flags,
        )
        backup, bhash = Path(info["path"]), info["sha256"]
        verify_bytes = not info.get("reused")
    run_untether_command(
        nano,
        check_only=check_only,
        run=run,
        verify=verify,
        mse=mse,
        serial=serial,
        backup=backup,
        backup_sha256=bhash,
        accept_2015_refresh=getattr(args, "i_accept_2015_refresh", False),
        accept_untested_flasher=getattr(args, "i_accept_untested_flasher", False),
        accept_untested_firmware=getattr(args, "i_accept_untested_firmware", False),
        typed_confirm=_typed(args),
        accept_brick=getattr(args, "i_accept_brick_risk", False) and getattr(args, "yes", False),
        apps=_selected_apps(args),
        replace=False,
        verify_bytes=verify_bytes,
        **flags,
    )
    return 0


def cmd_build(args) -> int:
    """Build apps (no device). Delegates to nanoapps/start."""
    repo = Path(__file__).resolve().parent.parent
    nanoapps = repo / "nanoapps" / "start"
    cmd = [sys.executable, str(nanoapps), "build", *getattr(args, "targets", [])]
    return os.spawnv(os.P_WAIT, sys.executable, cmd)


def cmd_tethered(args) -> int:
    """Original NanoApps SCSI install (Linux local or SSH)."""
    repo = Path(__file__).resolve().parent.parent
    nanoapps = repo / "nanoapps" / "start"
    extra = list(getattr(args, "rest", []) or [])
    cmd = [sys.executable, str(nanoapps), *extra]
    if use_ssh(args) and configured_remote():
        os.environ["REMOTE"] = configured_remote()
    return os.spawnv(os.P_WAIT, sys.executable, cmd)


def cmd_games_build(args) -> int:
    from .ports import build_n64_game

    mk64 = getattr(args, "mk64", None)
    sm64 = getattr(args, "sm64", None)
    build_n64_game(
        mk64=Path(mk64).expanduser() if mk64 else None,
        sm64=Path(sm64).expanduser() if sm64 else None,
    )
    return 0


def cmd_ports(args) -> int:
    from .ports import fetch_ios_port, PORT_REPOS, print_status

    sub = getattr(args, "ports_cmd", None) or "status"
    if sub == "status":
        print_status()
        return 0
    if sub == "fetch":
        for slug in PORT_REPOS:
            path = fetch_ios_port(slug)
            print(f"    {slug}  {path}")
        return 0
    if sub == "stage":
        from .ports import stage_games_from_build

        stage_games_from_build(
            Path(args.dir).expanduser() if getattr(args, "dir", None) else None
        )
        return 0
    raise Refused(f"unknown ports command {sub}")


def cmd_port(args) -> int:
    from .porting import (
        print_candidates,
        print_families,
        print_oracle,
        print_rules,
        print_status,
        print_budget,
        refuse_new_game_port,
    )

    sub = getattr(args, "port_cmd", None) or "status"
    if sub == "status":
        return print_status()
    if sub == "families":
        return print_families()
    if sub == "rules":
        return print_rules()
    if sub == "oracle":
        return print_oracle()
    if sub == "candidates":
        return print_candidates()
    if sub in {"fruit", "fruitninja"}:
        from .fruitninja import print_inventory

        payload = getattr(args, "payload", None)
        return print_inventory(Path(payload).expanduser() if payload else None)
    if sub in {"pvz", "plants", "plantvszombies"}:
        from .pvz import print_inventory as print_pvz

        payload = getattr(args, "payload", None)
        return print_pvz(Path(payload).expanduser() if payload else None)
    if sub == "budget":
        return print_budget(
            path=getattr(args, "file", None),
            name=getattr(args, "name", None),
        )
    if sub == "new":
        family = getattr(args, "family", None) or "first-party"
        if family in {"n64", "decomp", "recomp"}:
            refuse_new_game_port(family)
        from .sdkapp import new_app

        new_app(
            args.name,
            title=getattr(args, "title", None),
            kind=getattr(args, "kind", None) or "raw",
        )
        return 0
    raise Refused(f"unknown port command {sub}")


def cmd_perf(args) -> int:
    from .perf import print_status

    return print_status()


def cmd_sdk(args) -> int:
    from .sdkapp import build_app, new_app, print_status

    sub = getattr(args, "sdk_cmd", None) or "status"
    if sub == "status":
        return print_status()
    if sub == "new":
        new_app(
            args.name,
            title=getattr(args, "title", None),
            kind=getattr(args, "kind", None) or "raw",
            color=getattr(args, "color", None) or "#e8c547",
            glyph=getattr(args, "glyph", None) or "stopwatch",
        )
        return 0
    if sub == "build":
        build_app(args.name)
        return 0
    raise Refused(f"unknown sdk command {sub}")


def cmd_pocketjs(args) -> int:
    from .pocketjs import build_hello, clone, print_status

    sub = getattr(args, "pocketjs_cmd", None) or "status"
    if sub == "status":
        return print_status()
    if sub == "clone":
        clone(force=getattr(args, "force", False))
        return 0
    if sub == "hello":
        build_hello()
        return 0
    if sub == "apps":
        from .pocketjs import print_nano7g_apps

        return print_nano7g_apps()
    if sub == "build":
        from .pocketjs import build_nano7g_app, manifest

        names = list(getattr(args, "names", []) or [])
        if getattr(args, "all", False) or not names:
            names = [a["key"] for a in manifest()["apps"]]
        for name in names:
            build_nano7g_app(name)
        return 0
    if sub == "emu":
        from .pocketjs import emulate

        return emulate(args.name, getattr(args, "out", None), getattr(args, "script", None),
                       getattr(args, "os_blocks", None), getattr(args, "no_bytecode", False))
    if sub == "packages":
        from .pocketjs import add_packages

        if use_ssh(args):
            raise Refused("pocketjs packages copies local files onto the nano. Run it on the Mac (Disk Mode).")
        add_packages(skip_wait=getattr(args, "skip_wait", False), watch=getattr(args, "watch", False))
        return 0
    if sub == "log":
        from .pocketjs import pocketjs_log

        if use_ssh(args) and not getattr(args, "file", None):
            raise Refused("pocketjs log reads the iPod volume. Run it on the Mac (Disk Mode).")
        return pocketjs_log(getattr(args, "file", None))
    raise Refused(f"unknown pocketjs command {sub}")



def cmd_resident(args) -> int:
    """Disk Mode install/restore of the NanoApps-1.0-based heavy resident."""
    from .resident_na import install_na, restore, status

    sub = getattr(args, "resident_cmd", None) or "status"
    if sub == "status":
        return status()
    if sub not in {"install-na", "restore"}:
        raise Refused(f"unknown resident command {sub}")
    nano = None
    try:
        nano = _pick_nano(args)
    except Refused:
        nano = None
    # install_na / restore wait for Disk Mode when skip_wait is false
    if nano is None and (getattr(args, "skip_wait", False) or getattr(args, "watch", False)):
        # watch path: open after wait inside install_na
        pass
    backup = Path(getattr(args, "backup") or "") if getattr(args, "backup", None) else None
    bhash = getattr(args, "backup_sha256", None) or ""
    if nano is not None:
        backup, bhash = _resolve_flash_backup(args, nano)
    elif backup is None:
        # Host will refuse inside check_backup unless --skip-backup-file
        backup = Path(".")
    flags = disk_flags(args)
    wait_flags = {
        "skip_wait": getattr(args, "skip_wait", False),
        "watch": getattr(args, "watch", False),
    }
    if use_ssh(args):
        raise Refused("resident install-na / restore: Mac Disk Mode only, not over SSH.")
    if not is_root():
        print("re-running with sudo so the volume can be flushed...")
        forwarded = _parent_device_flags(args) + ["resident", sub]
        if getattr(args, "device", None):
            forwarded += ["--device", args.device]
        if backup and str(backup) != ".":
            forwarded += ["--backup", str(backup), "--backup-sha256", bhash]
        if getattr(args, "skip_backup_file", False):
            forwarded.append("--skip-backup-file")
        if wait_flags["skip_wait"]:
            forwarded.append("--skip-wait")
        if wait_flags["watch"]:
            forwarded.append("--watch")
        reexec_as_root(argv=forwarded)
    fn = install_na if sub == "install-na" else restore
    fn(
        nano,
        backup=backup if backup is not None else Path("."),
        backup_sha256=bhash or "",
        skip_backup_file=getattr(args, "skip_backup_file", False) or (str(backup) == "."),
        **wait_flags,
        **flags,
    )
    return 0



def cmd_nanoapp(args) -> int:
    """Disk Mode copy of staged .nanoapp folders into /Apps."""
    from .nanoapp_copy import copy_nanoapp

    sub = getattr(args, "nanoapp_cmd", None) or "copy"
    if sub != "copy":
        raise Refused(f"unknown nanoapp command {sub}")
    names = list(getattr(args, "names", []) or [])
    if not names:
        raise Refused("name a staged .nanoapp folder, e.g. ./start nanoapp copy Paint.nanoapp")
    nano = None
    try:
        nano = _pick_nano(args)
    except Refused:
        nano = None
    backup = Path(getattr(args, "backup") or ".") if getattr(args, "backup", None) else Path(".")
    bhash = getattr(args, "backup_sha256", None) or ""
    if nano is not None:
        backup, bhash = _resolve_flash_backup(args, nano)
    flags = disk_flags(args)
    wait_flags = {
        "skip_wait": getattr(args, "skip_wait", False),
        "watch": getattr(args, "watch", False),
    }
    if use_ssh(args):
        raise Refused("nanoapp copy: Mac Disk Mode only, not over SSH.")
    if not is_root():
        print("re-running with sudo so the volume can be flushed...")
        forwarded = _parent_device_flags(args) + ["nanoapp", "copy", *names]
        if getattr(args, "device", None):
            forwarded += ["--device", args.device]
        if backup and str(backup) != ".":
            forwarded += ["--backup", str(backup), "--backup-sha256", bhash]
        if getattr(args, "skip_backup_file", False) or str(backup) == ".":
            forwarded.append("--skip-backup-file")
        if wait_flags["skip_wait"]:
            forwarded.append("--skip-wait")
        if wait_flags["watch"]:
            forwarded.append("--watch")
        reexec_as_root(argv=forwarded)
    for name in names:
        copy_nanoapp(
            name,
            nano,
            backup=backup if backup is not None else Path("."),
            backup_sha256=bhash or "",
            skip_backup_file=getattr(args, "skip_backup_file", False) or str(backup) == ".",
            **wait_flags,
            **flags,
        )
    return 0


def cmd_package(args) -> int:
    """Build a distributable .nanoapp zip (Paint, Free RAM, ...)."""
    import sys
    from pathlib import Path as P
    tools = P(__file__).resolve().parent.parent / "nanoapps" / "tools"
    sys.path.insert(0, str(tools))
    from pack_nanoapp_simple import package as package_app

    names = list(getattr(args, "names", []) or [])
    if not names:
        raise Refused("name an app to package, e.g. ./start package paint freeram")
    for name in names:
        package_app(name)
    return 0


def cmd_install_stage(args) -> int:
    """Copy a staged Apps tree onto the nano over FAT (games, extra apps)."""
    from .fat import assert_not_hfs, copy_tree_verified, need_mtools, unmount
    from .remote import configured_remote_root, deploy_dir, remote_python
    from .state import repo_root

    src = Path(args.dir).expanduser() if getattr(args, "dir", None) else repo_root() / "build" / "games-stage"
    if not src.is_dir():
        raise Refused(f"stage directory missing: {src} (build games first, or pass a folder)")
    apps = src / "Apps" if (src / "Apps").is_dir() else src
    nano = _pick_nano(args)
    assert_not_hfs(nano)
    if use_ssh(args):
        host = ssh_host(args)
        remote_stage = f"{configured_remote_root()}/build/install-stage"
        print(f"==> copying {apps} to {host}:{remote_stage}")
        deploy_dir(host, apps, remote_stage)
        r = remote_python(
            host,
            ["install-stage", remote_stage, "--device", nano.device],
            sudo=True,
        )
        sys.stdout.write(r.stdout or "")
        sys.stderr.write(r.stderr or "")
        return r.returncode
    need_mtools()
    unmount(nano)
    n = copy_tree_verified(nano.partition, apps, "/Apps")
    print(f"copied {n} files from {apps} onto the nano")
    return 0


def _fat32_forward_argv(
    args,
    *,
    backup: Path,
    bhash: str,
    serial: str,
    dry: bool,
    mse: Optional[Path],
    skip_backup_file: bool = False,
) -> list:
    """sudo / SSH argv for a live fat32 convert. Always includes the hashed backup."""
    forwarded = _parent_device_flags(args)
    forwarded += ["fat32"]
    forwarded.append("--dry-run" if dry else "--run")
    if mse is not None:
        forwarded += ["--mse", str(mse)]
    forwarded += ["--backup", str(Path(backup)), "--backup-sha256", bhash]
    if skip_backup_file:
        forwarded.append("--skip-backup-file")
    forwarded += ["--serial", serial]
    if getattr(args, "replace", False):
        forwarded.append("--replace")
    if getattr(args, "format_only", False):
        forwarded.append("--format-only")
    if getattr(args, "scsi_dump", None):
        forwarded += ["--scsi-dump", str(args.scsi_dump)]
    if getattr(args, "device", None):
        forwarded += ["--device", args.device]
    return forwarded


def _parent_device_flags(args) -> list:
    argv = []
    if getattr(args, "allow_disk_size", None) is not None:
        argv += ["--allow-disk-size", str(args.allow_disk_size)]
    if getattr(args, "confirm_untested_capacity", False):
        argv.append("--confirm-untested-capacity")
    if getattr(args, "yes", False):
        argv.append("--yes")
    if getattr(args, "i_accept_brick_risk", False):
        argv.append("--i-accept-brick-risk")
    if getattr(args, "i_accept_untested_flasher", False):
        argv.append("--i-accept-untested-flasher")
    if getattr(args, "i_accept_2015_refresh", False):
        argv.append("--i-accept-2015-refresh")
    if getattr(args, "i_accept_untested_firmware", False):
        argv.append("--i-accept-untested-firmware")
    if getattr(args, "i_accept_themed_untether", False):
        argv.append("--i-accept-themed-untether")
    if getattr(args, "typed_confirm", None):
        argv += ["--typed-confirm", args.typed_confirm]
    if getattr(args, "local", False):
        argv.append("--local")
    if getattr(args, "json", False):
        argv.append("--json")
    return argv


def cmd_fat32(args) -> int:
    """Windows-free HFS+ -> FAT32. Wipes music. Keeps the hashed raw backup."""
    from .winpod import (
        build_winpod_file,
        convert_nano,
        extract_stock_mse,
        inspect_path,
        print_layout,
    )
    from .remote import configured_remote_root, remote_python, scp_mse_if_needed

    inspect = getattr(args, "inspect", None)
    build_out = getattr(args, "build_image", None)
    scsi_dump = getattr(args, "scsi_dump", None)

    if scsi_dump and (inspect is not None or build_out):
        raise Refused("--scsi-dump cannot be combined with --inspect or --build-image")
    if scsi_dump and getattr(args, "run", False):
        raise Refused("--scsi-dump is read-only; do not pass --run")
    if scsi_dump and getattr(args, "replace", False):
        raise Refused("--scsi-dump is read-only; do not pass --replace")
    if scsi_dump and getattr(args, "format_only", False):
        raise Refused("--scsi-dump cannot be combined with --format-only")

    if build_out:
        src = Path(args.backup).expanduser() if getattr(args, "backup", None) else None
        if src is None:
            raise Refused("--build-image needs --backup pointing at a Macpod disk image")
        dest = Path(build_out).expanduser()
        layout = build_winpod_file(src, dest)
        print_layout(layout, dest)
        print(
            "FILE ONLY. This constructed Winpod is not written to a live nano "
            "(no n7g Windows iTunes dump; Rockbox conversion tables omit nano 7G). "
            "Live path: ./start fat32"
        )
        return 0

    if inspect is not None:
        if inspect != "attached":
            path = Path(inspect).expanduser()
            print_layout(inspect_path(path), path)
            return 0
        if use_ssh(args):
            r = remote_python(ssh_host(args), _parent_device_flags(args) + ["fat32", "--inspect"], sudo=True)
            sys.stdout.write(r.stdout or "")
            sys.stderr.write(r.stderr or "")
            return r.returncode
        if not is_root():
            print("re-running with sudo so the raw disk can be read...")
            reexec_as_root(argv=_parent_device_flags(args) + ["fat32", "--inspect"])
        nano = _pick_nano(args)
        print(format_nano(nano))
        print_layout(inspect_path(Path(nano.raw_device)), Path(nano.raw_device))
        return 0

    dry = not getattr(args, "run", False)
    if getattr(args, "dry_run", False):
        dry = True
    serial = getattr(args, "serial", None) or load_config().get("DEVICE_SERIAL") or ""
    if not str(serial).strip():
        raise Refused(
            "device serial is required (Settings > General > About, or the back). Pass --serial."
        )
    nano = None
    try:
        nano = _pick_nano(args)
    except Refused:
        nano = None
    backup, bhash = _resolve_flash_backup(args, nano)
    rec = last_backup(nano.usb_serial if nano else None)
    size = backup_verify_size(
        Path(backup),
        nano_size=nano.size if nano else None,
        receipt_size=rec.get("size") if rec else None,
        format_only=getattr(args, "format_only", False),
        scsi_dump=bool(scsi_dump),
    )
    check_backup(backup, bhash, size, skip_file=getattr(args, "skip_backup_file", False))
    flags = disk_flags(args)
    untested = getattr(args, "i_accept_untested_flasher", False)
    if scsi_dump:
        mse = None
    elif getattr(args, "format_only", False) and not getattr(args, "mse", None):
        mse = None
    else:
        mse = Path(args.mse) if getattr(args, "mse", None) else extract_stock_mse()

    if use_ssh(args):
        host = ssh_host(args)
        remote_mse = None
        if mse is not None:
            remote_mse = Path(f"{configured_remote_root()}/build/firmware/{mse.name}")
            scp_mse_if_needed(host, mse, str(remote_mse))
        argv = _fat32_forward_argv(
            args,
            backup=backup,
            bhash=bhash,
            serial=serial,
            dry=dry,
            mse=remote_mse,
            skip_backup_file=True,
        )
        r = remote_python(host, argv, sudo=True)
        sys.stdout.write(r.stdout or "")
        sys.stderr.write(r.stderr or "")
        return r.returncode

    if not is_root():
        print("re-running with sudo so the raw disk / USB device can be opened...")
        # Always forward the resolved backup. As root, last_backup() looks in
        # /var/db/nano7kit and ~root, not the user's receipt.
        reexec_as_root(
            argv=_fat32_forward_argv(
                args,
                backup=backup,
                bhash=bhash,
                serial=serial,
                dry=dry,
                mse=mse,
                skip_backup_file=False,
            )
        )

    convert_nano(
        nano,
        mse=mse,
        backup=backup,
        backup_sha256=bhash,
        serial=serial,
        dry_run=dry,
        accept_brick=getattr(args, "i_accept_brick_risk", False) and getattr(args, "yes", False),
        skip_backup_file=getattr(args, "skip_backup_file", False),
        accept_untested_flasher=untested,
        accept_2015_refresh=getattr(args, "i_accept_2015_refresh", False),
        replace=getattr(args, "replace", False),
        format_only=getattr(args, "format_only", False),
        typed_confirm=_typed(args),
        scsi_dump=Path(scsi_dump).expanduser() if scsi_dump else None,
        **flags,
    )
    return 0


def cmd_sync(args) -> int:
    """Disk Mode walkthrough, then hand off to Music/Finder. Never writes the DB."""
    from .sync import run_sync
    from .remote import remote_python

    if use_ssh(args):
        argv = ["sync"]
        if getattr(args, "watch", False):
            argv.append("--watch")
        if getattr(args, "skip_wait", False):
            argv.append("--skip-wait")
        r = remote_python(ssh_host(args), argv, sudo=False)
        sys.stdout.write(r.stdout or "")
        sys.stderr.write(r.stderr or "")
        return r.returncode
    return run_sync(
        watch=getattr(args, "watch", False),
        skip_wait=getattr(args, "skip_wait", False),
    )


def cmd_scsi_dump(args) -> int:
    """Read-only BOT dump. Same gates as fat32 --scsi-dump DIR. Never WRITE."""
    args.scsi_dump = getattr(args, "dir", None) or getattr(args, "scsi_dump", None)
    args.inspect = None
    args.build_image = None
    args.run = False
    args.format_only = False
    args.replace = False
    args.mse = None
    if not getattr(args, "scsi_dump", None):
        raise Refused("scsi-dump needs a directory")
    return cmd_fat32(args)


def cmd_bootloader(args) -> int:
    """2015 39A10023 bootloader only. No MSE, no FAT, no --full, no NAND RCE."""
    from .bootloader import run_bootloader_command
    from .remote import remote_python

    serial = getattr(args, "serial", None) or load_config().get("DEVICE_SERIAL") or ""
    run = bool(getattr(args, "run", False))
    check_only = bool(getattr(args, "check_only", False))
    if not run:
        check_only = True
    if use_ssh(args):
        argv = _parent_device_flags(args) + ["bootloader"]
        if check_only and not run:
            argv.append("--check-only")
        if run:
            argv.append("--run")
        argv += ["--serial", str(serial)]
        if getattr(args, "ipsw", None):
            argv += ["--ipsw", str(args.ipsw)]
        r = remote_python(ssh_host(args), argv, sudo=True)
        sys.stdout.write(r.stdout or "")
        sys.stderr.write(r.stderr or "")
        return r.returncode
    run_bootloader_command(
        serial=str(serial),
        check_only=check_only,
        run=run,
        accept_untested_flasher=getattr(args, "i_accept_untested_flasher", False),
        accept_2015_refresh=getattr(args, "i_accept_2015_refresh", False),
        typed_confirm=_typed(args),
        accept_brick=getattr(args, "i_accept_brick_risk", False) and getattr(args, "yes", False),
        ipsw=Path(args.ipsw).expanduser() if getattr(args, "ipsw", None) else None,
    )
    return 0


def cmd_scsi_probe(args) -> int:
    """Try vendor C6 peek. Tells you whether Mac-native SCSI works, or to use the Pi."""
    from .fat import unmount
    from .remote import remote_python
    from .scsi import peek

    if use_ssh(args):
        r = remote_python(ssh_host(args), ["scsi-probe"], sudo=True)
        sys.stdout.write(r.stdout or "")
        sys.stderr.write(r.stderr or "")
        return r.returncode
    nano = _pick_nano(args)
    unmount(nano)
    data = peek(nano, TRACE_RING, 0x200)
    nonzero = any(data)
    print(f"SCSI peek {hex(TRACE_RING)}: {len(data)} bytes, nonzero={nonzero}")
    if not nonzero:
        print("peek came back zeros (device may still be settling; retry, or use the Pi)")
        return 1
    print("vendor SCSI is responding on this computer")
    print(
        "For a read-only BOT dump of the MBR + data-slice prefix in recovery "
        "diskmode, use ./start fat32 --scsi-dump DIR (not this C6 peek)."
    )
    return 0


def _rescue_forward_argv(
    args,
    *,
    backup: Path,
    bhash: str,
    serial: str,
    dry: bool,
    mse: Optional[Path],
    skip_backup_file: bool = False,
) -> list:
    forwarded = _parent_device_flags(args)
    forwarded += ["rescue"]
    forwarded.append("--dry-run" if dry else "--run")
    if mse is not None:
        forwarded += ["--mse", str(mse)]
    forwarded += ["--backup", str(Path(backup)), "--backup-sha256", bhash]
    if skip_backup_file:
        forwarded.append("--skip-backup-file")
    forwarded += ["--serial", serial]
    if getattr(args, "replace", False):
        forwarded.append("--replace")
    if getattr(args, "format_only", False):
        forwarded.append("--format-only")
    if getattr(args, "restore_data", False):
        forwarded.append("--restore-data")
    if getattr(args, "device", None):
        forwarded += ["--device", args.device]
    return forwarded


def cmd_rescue(args) -> int:
    """Stock 39A10023 restore. LeUnBrIck idea, this toolkit's checks. Untested here."""
    from .rescue import rescue_nano
    from .winpod import extract_stock_mse
    from .remote import configured_remote_root, remote_python, scp_mse_if_needed

    dry = not getattr(args, "run", False)
    if getattr(args, "dry_run", False):
        dry = True
    serial = getattr(args, "serial", None) or load_config().get("DEVICE_SERIAL") or ""
    if not str(serial).strip():
        raise Refused(
            "device serial is required (Settings > General > About, or the back). Pass --serial."
        )
    nano = None
    try:
        nano = _pick_nano(args)
    except Refused:
        nano = None
    backup, bhash = _resolve_flash_backup(args, nano)
    rec = last_backup(nano.usb_serial if nano else None)
    size = backup_verify_size(
        Path(backup),
        nano_size=nano.size if nano else None,
        receipt_size=rec.get("size") if rec else None,
        format_only=getattr(args, "format_only", False),
        scsi_dump=False,
    )
    check_backup(backup, bhash, size, skip_file=getattr(args, "skip_backup_file", False))
    flags = disk_flags(args)
    untested = getattr(args, "i_accept_untested_flasher", False)
    mse = Path(args.mse) if getattr(args, "mse", None) else extract_stock_mse()

    if use_ssh(args):
        host = ssh_host(args)
        remote_mse = Path(f"{configured_remote_root()}/build/firmware/{mse.name}")
        scp_mse_if_needed(host, mse, str(remote_mse))
        r = remote_python(
            host,
            _rescue_forward_argv(
                args,
                backup=backup,
                bhash=bhash,
                serial=serial,
                dry=dry,
                mse=remote_mse,
                skip_backup_file=True,
            ),
            sudo=True,
        )
        sys.stdout.write(r.stdout or "")
        sys.stderr.write(r.stderr or "")
        return r.returncode

    if not is_root():
        print("re-running with sudo so the raw disk / USB device can be opened...")
        reexec_as_root(
            argv=_rescue_forward_argv(
                args,
                backup=backup,
                bhash=bhash,
                serial=serial,
                dry=dry,
                mse=mse,
                skip_backup_file=False,
            )
        )

    rescue_nano(
        nano,
        mse=mse,
        backup=backup,
        backup_sha256=bhash,
        serial=serial,
        dry_run=dry,
        accept_brick=getattr(args, "i_accept_brick_risk", False) and getattr(args, "yes", False),
        skip_backup_file=getattr(args, "skip_backup_file", False),
        accept_untested_flasher=untested,
        accept_2015_refresh=getattr(args, "i_accept_2015_refresh", False),
        restore_data=getattr(args, "restore_data", False),
        format_only=getattr(args, "format_only", False),
        replace=getattr(args, "replace", False),
        typed_confirm=_typed(args),
        **flags,
    )
    return 0


def cmd_library(args) -> int:
    from .library import (
        inspect_attached_or_none,
        inspect_library_image,
        print_library_report,
        restore_library_from_backup,
    )

    sub = getattr(args, "library_cmd", None) or "inspect"
    if sub == "inspect":
        image = getattr(args, "image", None)
        if image:
            report = inspect_library_image(Path(image).expanduser())
            print_library_report(report, title="backup")
            return 0 if report.get("ok") else 1
        report = inspect_attached_or_none()
        if report is None:
            print("no FAT iPod mounted. Disk Mode (Do not disconnect while plugged in),")
            print("or pass --image on a FAT32 backup.")
            return 1
        print_library_report(report, title="library")
        return 0 if report.get("ok") else 1
    if sub != "restore-from-backup":
        raise Refused(f"unknown library command {sub}")
    if use_ssh(args):
        raise Refused(
            "library restore-from-backup copies from a local backup image. "
            "Run it on the Mac that holds the 10:37 FAT32 backup, not over SSH."
        )
    nano = None
    try:
        nano = _pick_nano(args)
    except Refused:
        nano = None
    backup, bhash = _resolve_flash_backup(args, nano)
    restore_library_from_backup(
        nano,
        backup=backup,
        backup_sha256=bhash,
        skip_wait=getattr(args, "skip_wait", False),
        watch=getattr(args, "watch", False),
        skip_backup_file=getattr(args, "skip_backup_file", False),
    )
    return 0


def _pvz_names():
    from .pvz_fit import PVZ_NAMES

    return PVZ_NAMES


def _apps_add_pvz(args, names, payload) -> int:
    """apps add PvZ [--plan]: memory verdict from the user's ipa; never builds or writes."""
    from .pvz_fit import plan_lines, refuse_add
    from .templerun import parse_size

    if len(names) != 1:
        raise Refused("apps add PvZ takes no other app names: ./start apps add PvZ --ipa FILE --plan")
    heap_free = parse_size(getattr(args, "heap_free", None))
    largest = parse_size(getattr(args, "largest", None))
    ipa = Path(payload).expanduser() if payload else None
    if getattr(args, "plan", False) or getattr(args, "dry_run", False):
        lines = plan_lines(ipa, heap_free, largest)
        print("\n".join(lines))
        return 0 if lines[-1] == "PLAN_OK" else 1
    refuse_add(ipa, heap_free, largest)
    return 1


def _apps_add_plan(args, names, payload) -> int:
    """apps add NAME --plan: read-only. Temple Run only for now."""
    from .apps import normalize_selection

    selected = normalize_selection(names, default_all=False, allow_extra=True)
    if selected not in (["Temple Run"], ["Fruit Ninja"], ["PvZ Portable"]):
        raise Refused(
            "apps add --plan is for one port at a time and covers Temple Run, Fruit Ninja, PvZ and PvZ Portable: "
            "./start apps add TempleRun --ipa /path/to/TempleRun.ipa --plan"
        )
    from .templerun import parse_size, plan_lines

    if selected == ["Fruit Ninja"]:
        from .fruitninja_rc import plan_lines as fruit_plan_lines

        def plan_lines(payload, **kw):  # noqa: F811 - same call shape as Temple Run's
            return fruit_plan_lines(payload, kw.pop("heap_free"), kw.pop("largest"), **kw)

    if selected == ["PvZ Portable"]:
        from .pvz_portable import plan_lines as pvzp_plan_lines

        def plan_lines(payload, **kw):  # noqa: F811 - same call shape as Temple Run's
            return pvzp_plan_lines(payload, kw.pop("heap_free"), kw.pop("largest"), **kw)

    heap_free = parse_size(getattr(args, "heap_free", None))
    largest = parse_size(getattr(args, "largest", None))
    on_disk = slot_b = nogrow = None
    try:
        from .apps import home_pack_fat, pack_app_names, slot_b_active
        from .cap import cap_lifted
        from .fat import MountFat, mounted_root

        nano = _pick_nano(args)
        mroot = mounted_root(nano)
        if mroot is not None:
            ops = MountFat(mroot)
            slot_b = slot_b_active(ops)
            nogrow = bool(cap_lifted(ops))
            data = ops.read_bytes(home_pack_fat(ops))
            on_disk = pack_app_names(data) if data else []
    except (Refused, Exception):  # the plan never needs the nano; skip the pack check
        on_disk = slot_b = nogrow = None
    lines = plan_lines(Path(payload).expanduser() if payload else None,
                       on_disk=on_disk, slot_b=slot_b, nogrow=nogrow,
                       heap_free=heap_free, largest=largest)
    if on_disk is None:
        lines.insert(1, "    nano      not mounted (Disk Mode not attached): pack check skipped")
    print("\n".join(lines))
    return 0 if lines[-1] == "PLAN_OK" else 1


def _refuse_apps_on_nanoapps_base(args, sub: str) -> None:
    """Before any build, plan or sudo: ./start apps writes the untethered layout only."""
    from .apps import refuse_nanoapps_base
    from .fat import MountFat, mounted_root

    if getattr(args, "volume", None):
        root = Path(args.volume).expanduser()
    else:
        try:
            root = mounted_root(_pick_nano(args))
        except (Exception, SystemExit):  # no iPod attached yet: add_apps checks again once it is
            return
    if root is not None and Path(root).is_dir():
        refuse_nanoapps_base(MountFat(Path(root)), f"apps {sub}")


def cmd_apps(args) -> int:
    from .app_catalog import LOWEST_RISK_EXTRA, addable_names
    from .apps import (
        RECOVERY_HINT,
        apply_app_set,
        dump_lasttrace,
        format_list,
        format_set_plan,
        install_apps,
        list_on_disk,
        pick_interactive,
        plan_app_set,
        format_yield_plan,
        plan_yield,
        remove_apps,
        restore_tested_pack,
        tested_app_names,
        yield_apps,
        assert_yield_flag,
    )

    sub = getattr(args, "apps_cmd", None) or "list"
    names = list(getattr(args, "names", []) or [])
    if getattr(args, "all", False):
        names = tested_app_names()
    if getattr(args, "extras", False) and sub in {"add", "install", "pick", "set"}:
        raise Refused(
            "the first extras slice reboot-looped on MKN52 "
            "(11 icons, then ~1-2 s of UI, then reboot). "
            "apps add takes one extra per reboot. "
            f"Start with {LOWEST_RISK_EXTRA}: "
            f"./start apps add {LOWEST_RISK_EXTRA}. "
            + RECOVERY_HINT
        )
    rom = getattr(args, "rom", None)
    payload = getattr(args, "payload", None)
    if sub in {"add", "install", "remove", "set", "yield", "refresh", "pick"} or (
        sub == "cap" and getattr(args, "cap_cmd", None) in {"enable", "disable", "lean"}
    ):
        _refuse_apps_on_nanoapps_base(args, sub)
    if sub == "cap":
        from .cap import build_nogrow_artifacts, enable_cap, print_status

        cap_cmd = getattr(args, "cap_cmd", None) or "status"
        if cap_cmd in {None, "status"}:
            return print_status()
        if cap_cmd == "build":
            from .cap import build_lean_artifacts

            build_nogrow_artifacts()
            build_lean_artifacts()
            print("CAP_OK (host artifacts)")
            return 0
        if cap_cmd not in {"enable", "disable", "lean"}:
            raise Refused(f"unknown apps cap command {cap_cmd}")
        # fall through to device enable below
    if sub in {"add", "install"} and names and any(
        str(n).strip().lower().replace(" ", "") in _pvz_names() for n in names
    ):
        return _apps_add_pvz(args, names, payload)
    if sub in {"add", "install"} and names and (
        getattr(args, "plan", False) or getattr(args, "dry_run", False)
    ):
        return _apps_add_plan(args, names, payload)
    if sub in {"add", "install"} and names and not getattr(args, "all", False):
        from .apps import assert_one_extra_add, normalize_selection
        from .ports import prepare_for_add

        selected = normalize_selection(names, default_all=False, allow_extra=True)
        extras = assert_one_extra_add(selected)
        if extras:
            prepare_for_add(
                extras[0],
                rom=Path(rom).expanduser() if rom else None,
                payload=Path(payload).expanduser() if payload else None,
            )
            names = selected
    if sub == "set" and (
        getattr(args, "plan", False) or getattr(args, "dry_run", False)
    ):
        on_disk = None
        try:
            nano = _pick_nano(args)
            on_disk = list_on_disk(nano)
        except Refused:
            on_disk = None
        plan = plan_app_set(names or None, on_disk=on_disk, nogrow=False)
        if on_disk is None:
            # Host-only: assume tested three, stock cap.
            pass
        print(format_set_plan(plan))
        return 0
    if sub == "yield" and (
        getattr(args, "plan", False) or getattr(args, "dry_run", False)
    ):
        if not names:
            names = ["Blocks"]
        print(format_yield_plan(plan_yield(names)))
        return 0
    if sub == "yield":
        if not names:
            names = ["Blocks"]
        assert_yield_flag(
            names, getattr(args, "i_accept_untested_pack", False)
        )
    if sub == "list":
        on_disk = None
        try:
            nano = _pick_nano(args)
            on_disk = list_on_disk(nano)
        except Refused:
            nano = None
        text = format_list(on_disk=on_disk, selected=names or None)
        if _json_mode(args):
            print(
                json.dumps(
                    {
                        "on_disk": on_disk,
                        "tested": tested_app_names(),
                        "hardware_safe": tested_app_names(),
                        "addable": addable_names(),
                        "lowest_risk_extra": LOWEST_RISK_EXTRA,
                        "recovery": RECOVERY_HINT,
                    },
                    indent=2,
                )
            )
        else:
            print(text)
        return 0
    nano = None
    try:
        nano = _pick_nano(args)
    except Refused:
        nano = None
    if sub == "lasttrace":
        if nano is None:
            nano = _pick_nano(args)
        dump_lasttrace(nano)
        return 0
    if sub == "refresh" and (
        getattr(args, "plan", False) or getattr(args, "dry_run", False)
    ):
        from .app_refresh import format_plan, plan_refresh
        from .fat import MountFat, mounted_root

        if nano is None:
            nano = _pick_nano(args)
        mroot = mounted_root(nano)
        if mroot is None:
            raise Refused(
                "apps refresh --plan reads the mounted IPOD volume. Enter Disk Mode "
                "(Do not disconnect) and let macOS mount it, then re-run."
            )
        print(format_plan(plan_refresh(MountFat(mroot).read_hash)))
        return 0
    if sub == "pick":
        installed = None
        if nano is not None:
            try:
                installed = list_on_disk(nano)
            except Refused:
                installed = None
        names = pick_interactive(installed=installed)
        sub = "add"
    if sub not in {"add", "install", "remove", "restore-tested", "set", "cap", "yield", "refresh"}:
        raise Refused(f"unknown apps command {sub}")
    if nano is None:
        nano = _pick_nano(args)
    backup, bhash = _resolve_flash_backup(args, nano)
    flags = disk_flags(args)
    wait_flags = {
        "skip_wait": getattr(args, "skip_wait", False),
        "watch": getattr(args, "watch", False),
    }
    if use_ssh(args):
        from .app_catalog import by_name
        from .remote import remote_python

        if sub in {"set", "cap"}:
            raise Refused(
                "apps set / apps cap enable / disable: Mac Disk Mode only, not over SSH."
            )
        extras_now = [n for n in names if n not in tested_app_names()]
        if extras_now:
            row = by_name().get(extras_now[0]) or {}
            if row.get("large_gl"):
                raise Refused(
                    "Mario / large GL ports: add on the Mac in Disk Mode, "
                    "not over SSH (slot B files live in this checkout)."
                )
        argv = _parent_device_flags(args) + ["apps", sub, *names, "--device", nano.device]
        argv += ["--backup", str(backup), "--backup-sha256", bhash, "--skip-backup-file"]
        if getattr(args, "replace", False):
            argv.append("--replace")
        if wait_flags["skip_wait"]:
            argv.append("--skip-wait")
        if getattr(args, "keep_files", False):
            argv.append("--keep-files")
        if getattr(args, "i_accept_untested_pack", False):
            argv.append("--i-accept-untested-pack")
        if sub == "refresh" and getattr(args, "undo", False):
            argv.append("--undo")
        if rom:
            argv += ["--rom", str(Path(rom).expanduser())]
        if payload:
            argv += ["--payload", str(Path(payload).expanduser())]
        r = remote_python(ssh_host(args), argv, sudo=True)
        sys.stdout.write(r.stdout or "")
        sys.stderr.write(r.stderr or "")
        return r.returncode
    if not is_root():
        print("re-running with sudo so the volume can be flushed...")
        forwarded = _parent_device_flags(args) + ["apps", sub]
        if sub == "cap":
            forwarded.append(getattr(args, "cap_cmd", None) or "enable")
        forwarded += list(names)
        if getattr(args, "device", None):
            forwarded += ["--device", args.device]
        forwarded += ["--backup", str(backup), "--backup-sha256", bhash]
        if getattr(args, "replace", False):
            forwarded.append("--replace")
        if wait_flags["skip_wait"]:
            forwarded.append("--skip-wait")
        if getattr(args, "keep_files", False):
            forwarded.append("--keep-files")
        if getattr(args, "i_accept_untested_pack", False):
            forwarded.append("--i-accept-untested-pack")
        if sub == "refresh" and getattr(args, "undo", False):
            forwarded.append("--undo")
        if rom:
            forwarded += ["--rom", str(Path(rom).expanduser())]
        if payload:
            forwarded += ["--payload", str(Path(payload).expanduser())]
        reexec_as_root(argv=forwarded)
    if sub == "restore-tested":
        restore_tested_pack(
            nano,
            backup=backup,
            backup_sha256=bhash,
            skip_backup_file=getattr(args, "skip_backup_file", False),
            **wait_flags,
            **flags,
        )
        return 0
    if sub == "refresh":
        from .app_refresh import refresh_apps, undo_refresh

        fn = undo_refresh if getattr(args, "undo", False) else refresh_apps
        fn(
            nano,
            backup=backup,
            backup_sha256=bhash,
            skip_backup_file=getattr(args, "skip_backup_file", False),
            **wait_flags,
            **flags,
        )
        return 0
    if sub == "cap":
        from .cap import disable_cap, enable_cap

        cap_cmd = getattr(args, "cap_cmd", None)
        extra = {"lean": True} if cap_cmd == "lean" else {}
        fn = disable_cap if cap_cmd == "disable" else enable_cap
        fn(
            nano,
            **extra,
            backup=backup,
            backup_sha256=bhash,
            skip_backup_file=getattr(args, "skip_backup_file", False),
            **wait_flags,
            **flags,
        )
        return 0
    if sub == "set":
        apply_app_set(
            nano,
            names or None,
            backup=backup,
            backup_sha256=bhash,
            skip_backup_file=getattr(args, "skip_backup_file", False),
            rom=Path(rom).expanduser() if rom else None,
            payload=Path(payload).expanduser() if payload else None,
            **wait_flags,
            **flags,
        )
        return 0
    if sub in {"add", "install"}:
        if getattr(args, "all", False):
            chosen = tested_app_names()
        elif names:
            chosen = names
        else:
            raise Refused(
                f"name one extra to add ({LOWEST_RISK_EXTRA} is lowest risk), "
                "or pass --all (tested three: Blocks, Game Boy, Probe). "
                "The eight-extra slice is refused."
            )
        install_apps(
            nano,
            chosen,
            backup=backup,
            backup_sha256=bhash,
            replace=True,
            skip_backup_file=getattr(args, "skip_backup_file", False),
            **wait_flags,
            **flags,
        )
        return 0
    if sub == "remove":
        if getattr(args, "all", False):
            names = tested_app_names()
        if not names:
            raise Refused("name the apps to remove, or pass --all (tested three only)")
        remove_apps(
            nano,
            names,
            backup=backup,
            backup_sha256=bhash,
            skip_backup_file=getattr(args, "skip_backup_file", False),
            keep_files=getattr(args, "keep_files", False),
            **wait_flags,
            **flags,
        )
        return 0
    if sub == "yield":
        if not names:
            names = ["Blocks"]
        yield_apps(
            nano,
            names,
            backup=backup,
            backup_sha256=bhash,
            skip_backup_file=getattr(args, "skip_backup_file", False),
            accept_untested_pack=getattr(args, "i_accept_untested_pack", False),
            **wait_flags,
            **flags,
        )
        return 0
    raise Refused(f"unknown apps command {sub}")


def cmd_theme(args) -> int:
    from .theme import (
        clone_ipod_theme,
        combine_theme,
        import_theme_art,
        pack_theme,
        print_preview,
        print_status,
        revert_instructions,
        unpack_theme_firmware,
    )

    sub = getattr(args, "theme_cmd", None) or "status"
    if sub == "status":
        return print_status()
    if sub == "preview":
        return print_preview()
    if sub == "clone":
        clone_ipod_theme(force=getattr(args, "force", False))
        return 0
    if sub == "unpack":
        unpack_theme_firmware(ipsw=Path(args.ipsw) if getattr(args, "ipsw", None) else None)
        return 0
    if sub == "pack":
        pack_theme()
        return 0
    if sub in ("combine", "inject"):
        extra = []
        if getattr(args, "extra", None):
            for item in args.extra:
                if "=" not in item:
                    raise Refused("--extra needs FATPATH=FILE")
                fat, src = item.split("=", 1)
                extra.append((fat, Path(src)))
        fonts = [Path(p) for p in (getattr(args, "font", None) or [])]
        combine_theme(
            silverdb=Path(args.silverdb) if getattr(args, "silverdb", None) else None,
            rsrc=Path(args.rsrc) if getattr(args, "rsrc", None) else None,
            fonts=fonts,
            extra=extra,
            mse=Path(args.mse) if getattr(args, "mse", None) else None,
            accept_untested=getattr(args, "i_accept_untested_firmware", False),
            accept_combine=getattr(args, "i_accept_themed_untether", False),
        )
        return 0
    if sub == "revert":
        return revert_instructions()
    if sub == "import":
        import_theme_art(
            Path(args.source),
            out_dir=Path(args.out) if getattr(args, "out", None) else None,
            combine=getattr(args, "combine", False),
            mse=Path(args.mse) if getattr(args, "mse", None) else None,
            accept_untested=getattr(args, "i_accept_untested_firmware", False),
            accept_combine=getattr(args, "i_accept_themed_untether", False),
        )
        return 0
    raise Refused(f"unknown theme command {sub}")


def _wizard_run(sub: str, extra: Optional[list] = None) -> int:
    extra = extra or []
    argv = [sub, *extra]
    if needs_root_for(sub) and not is_root() and configured_mode() == "local":
        print(f"re-running with sudo: ./start {sub}")
        reexec_as_root(argv=argv)
    return main(argv)


def wizard(args) -> int:
    from .deps import ensure_deps

    ensure_deps(check_only=False)
    actions = [
        ("Doctor (what works on this computer)", lambda: _wizard_run("doctor")),
        ("One-command install (check, backup, skip-or-write NAND, /Apps, verify)", lambda: _wizard_run("install")),
        ("Install / update dependencies", lambda: _wizard_run("deps")),
        ("Build firmware (no device write)", lambda: _wizard_run("build-firmware")),
        ("Find the nano", lambda: _wizard_run("find")),
        ("Untether check-only (booted FAT32, no write)", lambda: _wizard_run("untether", ["--check-only"])),
        ("Backup only", lambda: _wizard_run("backup")),
        ("Install loader files only", lambda: _wizard_run("install-untether")),
        ("Flash firmware (DFU dry-run)", lambda: _wizard_run("flash", ["--dry-run"])),
        ("Repair FAT", lambda: _wizard_run("fsck")),
        ("Probe vendor SCSI (C6)", lambda: _wizard_run("scsi-probe")),
        ("HFS+ to FAT32 (no Windows; wipes music)", lambda: _wizard_run("fat32", ["--dry-run"])),
        ("2015 bootloader check (no write)", lambda: _wizard_run("bootloader", ["--check-only"])),
        ("Music sync (Disk Mode, then Music/Finder)", lambda: _wizard_run("sync")),
        ("Rescue to stock 1.1.2 (dry-run)", lambda: _wizard_run("rescue", ["--dry-run"])),
        ("List / choose apps", lambda: _wizard_run("apps", ["list"])),
        ("Resident heap / Blocks A/B (no NAND)", lambda: _wizard_run("perf")),
        ("Ports status (Mario / Temple / Flappy / Doodle)", lambda: _wizard_run("ports")),
        ("Porting pipeline", lambda: _wizard_run("port")),
        ("First-party SDK", lambda: _wizard_run("sdk")),
        ("PocketJS status", lambda: _wizard_run("pocketjs")),
        ("Theming status (ipod_theme)", lambda: _wizard_run("theme", ["status"])),
        ("Build apps (no device)", lambda: _wizard_run("build")),
        ("Tethered NanoApps (SCSI; original start)", lambda: _wizard_run("tethered")),
        ("Setup: where the nano is plugged in", lambda: _wizard_run("setup")),
    ]
    print("nano7kit – iPod nano 7G homebrew\n")
    print(f"  mode: {configured_mode()} {configured_remote() or ''}")
    print("  Modifying firmware can brick an iPod. Read README.md and docs/RECOVERY.md.\n")
    for i, (label, _) in enumerate(actions, 1):
        print(f"  {i}. {label}")
    print("  q. quit")
    if not sys.stdin.isatty():
        print("non-interactive: pass a subcommand (try ./start doctor)")
        return 0
    choice = input("\nchoose: ").strip().lower()
    if choice in ("", "q"):
        return 0
    try:
        return actions[int(choice) - 1][1]() or 0
    except (ValueError, IndexError):
        print("invalid choice")
        return 1


def _repeat_parent_device_flags(parser: argparse.ArgumentParser) -> None:
    """Allow --i-accept-* / disk-size flags after the subcommand as well as before.

    default=SUPPRESS so a flag set on the parent is not overwritten with False
    when the operator puts it before the subcommand.
    """
    parser.add_argument(
        "--i-accept-untested-flasher",
        action="store_true",
        default=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--i-accept-2015-refresh",
        action="store_true",
        default=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--i-accept-brick-risk",
        action="store_true",
        default=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--allow-disk-size",
        type=int,
        default=argparse.SUPPRESS,
        metavar="BYTES",
    )
    parser.add_argument(
        "--confirm-untested-capacity",
        action="store_true",
        default=argparse.SUPPRESS,
    )
    parser.add_argument("--yes", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--local", action="store_true", default=argparse.SUPPRESS)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="start",
        description="iPod nano 7G homebrew toolkit (macOS + Linux). One command from this computer.",
    )
    p.add_argument("--json", action="store_true", help="machine-readable output (agent/SSH)")
    p.add_argument("--verbose", action="store_true", help="show the long explanations (music rebuild text and more)")
    p.add_argument("--local", action="store_true", help="force local USB even if SSH is configured")
    p.add_argument("--remote", default=None, help="SSH host user@pi (setup / override)")
    p.add_argument("--allow-disk-size", type=int, default=None, metavar="BYTES")
    p.add_argument("--confirm-untested-capacity", action="store_true")
    p.add_argument("--yes", action="store_true", help="non-interactive yes (still needs --i-accept-brick-risk to flash)")
    p.add_argument("--i-accept-brick-risk", action="store_true")
    p.add_argument(
        "--i-accept-untested-flasher",
        action="store_true",
        help="allow host-built wInd3x (darwin/arm64, linux/arm64); firmware image hash still enforced",
    )
    p.add_argument(
        "--i-accept-2015-refresh",
        action="store_true",
        help="allow untested Mid-2015 / MKN52 flash path (WTF 0x124a). docs/HARDWARE-2015.md",
    )
    p.add_argument(
        "--i-accept-untested-firmware",
        action="store_true",
        help="allow an MSE that is not the hardware-tested hash (themed combine). Default flash stays tested.",
    )
    p.add_argument(
        "--i-accept-themed-untether",
        action="store_true",
        help="allow combining ipod_theme SilverImagesDB with the Helvetica carrier (new untested MSE)",
    )
    p.add_argument(
        "--typed-confirm",
        default=None,
        metavar="WORD",
        help="non-TTY typed confirmation (FAT32, FLASH, RESCUE, BOOTLOADER, or UNTETHER). Still checks the exact word.",
    )
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("doctor")
    fe = sub.add_parser("fetch", help="download third-party files (nano7-untethered) at the pinned commit and check them")
    fe.add_argument("--check", action="store_true", help="only check the files already in place")
    d = sub.add_parser("deps")
    d.add_argument("--check", action="store_true")
    s = sub.add_parser("setup")
    s.add_argument("--local", action="store_true")
    s.add_argument("--remote", default=None)
    sub.add_parser("find")
    b = sub.add_parser("backup")
    b.add_argument("--out", default=None)
    b.add_argument("--device", default=None)
    sub.add_parser("backup-stream").add_argument("--device", default=None)
    sub.add_parser("unmount").add_argument("--device", default=None)
    sub.add_parser("fsck").add_argument("--device", default=None)
    bf = sub.add_parser("build-firmware")
    bf.add_argument("ipsw", nargs="?", default=None)
    sub.add_parser("verify-release")
    nb = sub.add_parser("nabase", help="NanoApps 1.0 base: copy files onto the mounted iPod (no firmware)")
    nb_sub = nb.add_subparsers(dest="nabase_cmd")
    for name in ("status", "backup", "disk-files", "launcher", "restore", "packages"):
        nb_sub.add_parser(name).add_argument("--volume", default=None)
    nb_mk = nb_sub.add_parser("mark", help="record which firmware is on the iPod now")
    nb_mk.add_argument("base", choices=("nanoapps", "untethered"))
    nb_mk.add_argument("--volume", default=None)
    nb_ip = nb_sub.add_parser("ipsw")
    nb_ip.add_argument("--model", choices=("2015", "2012"), default="2015")
    nb_fl = nb_sub.add_parser(
        "flash",
        help="NanoApps 1.0 firmware via DFU, firmware only (when Finder cannot update)",
    )
    _repeat_parent_device_flags(nb_fl)
    nb_fl.add_argument("--dry-run", action="store_true")
    nb_fl.add_argument("--run", action="store_true")
    nb_fl.add_argument("--mse", default=None, help=argparse.SUPPRESS)
    nb_fl.add_argument("--backup", default=None)
    nb_fl.add_argument("--backup-sha256", default=None)
    nb_fl.add_argument("--serial", default=None)
    for name in ("add", "remove"):
        x = nb_sub.add_parser(name)
        x.add_argument("names", nargs="+")
        x.add_argument("--volume", default=None)
        x.add_argument("--rom", default=None)
        x.add_argument("--payload", "--ipa", dest="payload", default=None)
    iu = sub.add_parser(
        "install-untether",
        help="copy /Apps only (fsync, unmount, remount rehash; no firmware, no DFU)",
    )
    iu.add_argument("--device", default=None)
    iu.add_argument("--backup", default=None)
    iu.add_argument("--backup-sha256", default=None)
    iu.add_argument("--replace", action="store_true")
    iu.add_argument("--skip-backup-file", action="store_true")
    iu.add_argument("--apps", default=None, help="comma-separated catalog names (default: all tested)")
    ins = sub.add_parser(
        "install",
        help="one command: check, backup, bootloader-if-needed, flash, /Apps, verify",
        description="one command: check, backup, bootloader-if-needed, flash, /Apps, verify",
    )
    _repeat_parent_device_flags(ins)
    ins.add_argument(
        "--run",
        action="store_true",
        help="allow a NAND write if bootloader or firmware is still needed (typed BOOTLOADER or UNTETHER)",
    )
    ins.add_argument("--serial", default=None)
    ins.add_argument("--device", default=None)
    ins.add_argument("--mse", default=None)
    ins.add_argument("--backup", default=None)
    ins.add_argument("--backup-sha256", default=None)
    ins.add_argument("--skip-backup-file", action="store_true")
    ins.add_argument("--apps", default=None, help="comma-separated catalog names (default: all tested)")
    ins.add_argument(
        "--with-bootloader",
        action="store_true",
        help="also write the 39A10023 bootloader if no receipt says it was written (off by default)",
    )
    rm = sub.add_parser(
        "roms",
        help="copy Game Boy ROMs to /Apps/Data/GameBoy (Disk Mode, durable rehash)",
    )
    rm_sub = rm.add_subparsers(dest="roms_cmd")
    rm_add = rm_sub.add_parser(
        "add",
        help="copy .gb/.gbc files into /Apps/Data/GameBoy",
        description="Disk Mode copy of .gb/.gbc into /Apps/Data/GameBoy (durable rehash)",
    )
    rm_add.add_argument("files", nargs="+", help=".gb or .gbc files (you supply; none in git)")
    rm_add.add_argument("--device", default=None)
    rm_add.add_argument(
        "--watch",
        action="store_true",
        help="wait for a FAT32 volume instead of asking you to confirm Disk Mode",
    )
    rm_add.add_argument(
        "--skip-wait",
        action="store_true",
        help="copy onto whatever is already attached (still prefers Disk Mode)",
    )
    fl = sub.add_parser("flash")
    _repeat_parent_device_flags(fl)
    fl.add_argument("--dry-run", action="store_true")
    fl.add_argument("--run", action="store_true")
    fl.add_argument("--mse", default=None)
    fl.add_argument("--backup", default=None)
    fl.add_argument("--backup-sha256", default=None)
    fl.add_argument("--serial", default=None)
    fl.add_argument("--device", default=None)
    fl.add_argument("--skip-backup-file", action="store_true")
    ut = sub.add_parser(
        "untether",
        help="firmware-only Helvetica flash (no bootloader, no FAT format, no --replace)",
    )
    _repeat_parent_device_flags(ut)
    ut.add_argument(
        "--check-only",
        action="store_true",
        help="booted FAT32 checks only (layout, size, 39A10023 MSE, family 39). No DFU.",
    )
    ut.add_argument(
        "--run",
        action="store_true",
        help="copy /Apps, DFU, restore --firmware only (needs --typed-confirm UNTETHER)",
    )
    ut.add_argument(
        "--verify",
        action="store_true",
        help="post-boot /Apps sha match (C6 96 04 CHECK CONDITION expected under Helvetica)",
    )
    ut.add_argument("--serial", default=None)
    ut.add_argument("--device", default=None)
    ut.add_argument("--mse", default=None)
    ut.add_argument(
        "--backup",
        default=None,
        help="reuse this hashed FAT32 image; size must match the live nano",
    )
    ut.add_argument(
        "--backup-sha256",
        default=None,
        help="recorded sha of --backup; skips a new 15 GB read when size matches",
    )
    ut.add_argument("--replace", action="store_true", help=argparse.SUPPRESS)
    ut.add_argument("--apps", default=None, help="comma-separated catalog names (default: all tested)")
    bd = sub.add_parser("build")
    bd.add_argument("targets", nargs="*")
    td = sub.add_parser("tethered")
    td.add_argument("rest", nargs=argparse.REMAINDER)
    gb = sub.add_parser("build-games")
    gb.add_argument("--mk64", default=None)
    gb.add_argument("--sm64", default=None)
    po = sub.add_parser(
        "ports",
        help="olievans123 third-party ports (Mario / Temple Run / Flappy / Doodle)",
    )
    po_sub = po.add_subparsers(dest="ports_cmd")
    po_sub.add_parser("status")
    po_sub.add_parser("fetch")
    po_st = po_sub.add_parser("stage")
    po_st.add_argument("dir", nargs="?", default=None)
    pt = sub.add_parser(
        "port",
        help="repeatable porting pipeline (olievans123 recomp/decomp + oracle)",
    )
    pt_sub = pt.add_subparsers(dest="port_cmd")
    pt_sub.add_parser("status")
    pt_sub.add_parser("families")
    pt_sub.add_parser("rules")
    pt_sub.add_parser("oracle")
    pt_sub.add_parser("candidates")
    pt_fn = pt_sub.add_parser(
        "fruit",
        aliases=["fruitninja"],
        help="Fruit Ninja 1.0 inventory / stubs / archive plan (host only)",
    )
    pt_fn.add_argument("--payload", default=None, help="Fruit Ninja 1.0 .ipa (never committed)")
    pt_pvz = pt_sub.add_parser(
        "pvz",
        aliases=["plants", "plantvszombies"],
        help="PvZ iOS 1.0 inventory / stubs / slot budget (research only)",
    )
    pt_pvz.add_argument("--payload", default=None, help="PvZ 1.0 .ipa (never committed)")
    pt_b = pt_sub.add_parser("budget")
    pt_b.add_argument("file", nargs="?", default=None)
    pt_b.add_argument("--name", default=None)
    pt_n = pt_sub.add_parser("new")
    pt_n.add_argument("name")
    pt_n.add_argument("--title", default=None)
    pt_n.add_argument("--family", default="first-party")
    pt_n.add_argument("--kind", default="raw")
    sk = sub.add_parser(
        "sdk",
        help="first-party RAW_SURFACE app template (Lap) + sdk new/build",
    )
    sk_sub = sk.add_subparsers(dest="sdk_cmd")
    sk_sub.add_parser("status")
    sk_n = sk_sub.add_parser("new")
    sk_n.add_argument("name")
    sk_n.add_argument("--title", default=None)
    sk_n.add_argument("--kind", default="raw")
    sk_n.add_argument("--color", default="#e8c547")
    sk_n.add_argument("--glyph", default="stopwatch")
    sk_b = sk_sub.add_parser("build")
    sk_b.add_argument("name")
    pj = sub.add_parser(
        "pocketjs",
        help="PocketJS (MIT): nano7g MicroTS apps (JS Hero), pinned build, dry run; JS Hello HAL",
    )
    pj_sub = pj.add_subparsers(dest="pocketjs_cmd")
    pj_sub.add_parser("status")
    pj_cl = pj_sub.add_parser("clone")
    pj_cl.add_argument("--force", action="store_true")
    pj_sub.add_parser("hello", help="build first-party JS Hello HAL .hbapp (not the runtime)")
    pj_sub.add_parser("apps", help="list the PocketJS MicroTS apps built for the nano 7G (nano7g target)")
    pj_b = pj_sub.add_parser(
        "build",
        help="rebuild PocketJS nano7g apps from the pinned commit + pocketjs/nano7g.patch (needs bun, rustup)",
    )
    pj_b.add_argument("names", nargs="*")
    pj_b.add_argument("--all", action="store_true")
    pj_e = pj_sub.add_parser("emu", help="run a staged PocketJS app in the Unicorn dry run (host only)")
    pj_e.add_argument("name")
    pj_e.add_argument("--out", default=None)
    pj_e.add_argument("--script", default=None)
    pj_e.add_argument("--os-blocks", default=None,
                      help="model the OS heap as free blocks in MiB, e.g. 4.0,1.5,1.0,0.5 (Probe: 7.0 free, 4.0 largest)")
    pj_e.add_argument("--no-bytecode", action="store_true",
                      help="serve only the .pocket files (the app parses the JavaScript itself)")
    pj_p = pj_sub.add_parser("packages", help="copy the QuickJS packages to /Apps/Data/PocketJS (Disk Mode, no NAND)")
    pj_p.add_argument("--skip-wait", action="store_true")
    pj_p.add_argument("--watch", action="store_true")
    pj_l = pj_sub.add_parser("log", help="paste-able PocketJS log: /Apps/Data/PocketJS/logs/*.txt and /Apps/Startup/lasttrace.bin")
    pj_l.add_argument("file", nargs="?", default=None, help="a copied log .txt or lasttrace.bin (default: read the iPod)")

    res = sub.add_parser(
        "resident",
        help="Disk Mode install/restore of the NanoApps-1.0 heavy resident (slot B)",
    )
    res_sub = res.add_subparsers(dest="resident_cmd")
    res_sub.add_parser("status")
    res_in = res_sub.add_parser(
        "install-na",
        help="copy silver_resident_na to /Apps/Startup/resident-B.hbapp (saves rollback)",
    )
    res_rs = res_sub.add_parser(
        "restore",
        help="put back the resident-B saved by install-na",
    )
    for ap_r in (res_in, res_rs):
        ap_r.add_argument("--device", default=None)
        ap_r.add_argument("--backup", default=None)
        ap_r.add_argument("--backup-sha256", default=None)
        ap_r.add_argument("--skip-backup-file", action="store_true")
        ap_r.add_argument("--watch", action="store_true")
        ap_r.add_argument("--skip-wait", action="store_true")
    pkg = sub.add_parser(
        "package",
        help="build a distributable .nanoapp zip (Paint, Free RAM, ...)",
    )
    pkg.add_argument("names", nargs="+", help="nanoapps/apps/<name> folder")

    na = sub.add_parser(
        "nanoapp",
        help="Disk Mode copy of staged .nanoapp folders into /Apps",
    )
    na_sub = na.add_subparsers(dest="nanoapp_cmd")
    na_cp = na_sub.add_parser("copy", help="copy Paint.nanoapp / Free RAM.nanoapp into /Apps")
    na_cp.add_argument("names", nargs="+", help="folder name under untether/artifacts/extra/Apps")
    na_cp.add_argument("--device", default=None)
    na_cp.add_argument("--backup", default=None)
    na_cp.add_argument("--backup-sha256", default=None)
    na_cp.add_argument("--skip-backup-file", action="store_true")
    na_cp.add_argument("--watch", action="store_true")
    na_cp.add_argument("--skip-wait", action="store_true")

    st = sub.add_parser("install-stage")
    st.add_argument("dir", nargs="?", default=None)
    st.add_argument("--device", default=None)
    sub.add_parser("scsi-probe").add_argument("--device", default=None)
    sy = sub.add_parser("sync", aliases=["disk-mode"])
    sy.add_argument(
        "--watch",
        action="store_true",
        help="wait for a FAT32 volume instead of asking you to confirm Disk Mode",
    )
    sy.add_argument(
        "--skip-wait",
        action="store_true",
        help="print the Music/Finder steps for whatever is already attached",
    )
    fz = sub.add_parser(
        "fat32",
        aliases=["winpod"],
        help="HFS+ to FAT32 without Windows (wipes music; keeps hashed backup)",
    )
    _repeat_parent_device_flags(fz)
    fz.add_argument(
        "--inspect",
        nargs="?",
        const="attached",
        default=None,
        metavar="IMAGE",
        help="read-only: attached nano, or a disk image / backup file",
    )
    fz.add_argument(
        "--build-image",
        default=None,
        metavar="OUT",
        help="write a Winpod file from a Macpod --backup (never a live device)",
    )
    fz.add_argument("--dry-run", action="store_true")
    fz.add_argument("--run", action="store_true")
    fz.add_argument("--mse", default=None, help="stock Firmware.MSE (default: extract from IPSW)")
    fz.add_argument("--backup", default=None)
    fz.add_argument("--backup-sha256", default=None)
    fz.add_argument("--serial", default=None)
    fz.add_argument("--device", default=None)
    fz.add_argument("--skip-backup-file", action="store_true")
    fz.add_argument("--replace", action="store_true", help="allow another conversion")
    fz.add_argument(
        "--format-only",
        action="store_true",
        help="format the data partition FAT32; from DFU via prepare-only if needed; never restore --full",
    )
    fz.add_argument(
        "--scsi-dump",
        default=None,
        metavar="DIR",
        help="read-only BOT dump of MBR + data-slice prefix into DIR (recovery diskmode; never WRITE)",
    )
    sd = sub.add_parser(
        "scsi-dump",
        help="read-only BOT dump of MBR + data-slice prefix (same as fat32 --scsi-dump DIR)",
    )
    _repeat_parent_device_flags(sd)
    sd.add_argument("dir", help="directory to write dump-mbr.bin / dump-data-prefix.bin / dump.txt")
    sd.add_argument("--backup", default=None)
    sd.add_argument("--backup-sha256", default=None)
    sd.add_argument("--serial", default=None)
    sd.add_argument("--device", default=None)
    sd.add_argument("--skip-backup-file", action="store_true")
    bl = sub.add_parser(
        "bootloader",
        help="write 2015 39A10023 bootloader only (no MSE, no FAT, no --full)",
    )
    _repeat_parent_device_flags(bl)
    bl.add_argument(
        "--check-only",
        action="store_true",
        help="extract+hash+identity only; no IPodUpdate write. On-device NAND read is not available.",
    )
    bl.add_argument("--run", action="store_true", help="write the 39A10023 bootloader (needs --typed-confirm BOOTLOADER)")
    bl.add_argument("--serial", default=None)
    bl.add_argument("--ipsw", default=None, help="local iPod_1.1.2_39A10023.ipsw (default: build/)")
    rs = sub.add_parser(
        "rescue",
        help="stock 1.1.2 restore from DFU (hashed IPSW). Untested on a real nano here.",
    )
    _repeat_parent_device_flags(rs)
    rs.add_argument("--dry-run", action="store_true")
    rs.add_argument("--run", action="store_true")
    rs.add_argument("--mse", default=None, help="stock Firmware.MSE (default: extract from IPSW)")
    rs.add_argument("--backup", default=None)
    rs.add_argument("--backup-sha256", default=None)
    rs.add_argument("--serial", default=None)
    rs.add_argument("--device", default=None)
    rs.add_argument("--skip-backup-file", action="store_true")
    rs.add_argument("--replace", action="store_true")
    rs.add_argument(
        "--format-only",
        action="store_true",
        help="format or restore the data slice; from DFU via prepare-only if needed; never restore --full",
    )
    rs.add_argument(
        "--restore-data",
        action="store_true",
        help="copy the backup DATA slice after stock restore (never firmware). Layouts must match.",
    )
    lib = sub.add_parser(
        "library",
        help="inspect / restore torn iTunes Library.itdb Dynamic.itdb iTunesCDB (Disk Mode)",
    )
    lib_sub = lib.add_subparsers(dest="library_cmd")
    lib_ins = lib_sub.add_parser("inspect")
    lib_ins.add_argument("--image", default=None, help="FAT32 backup image (4K MBR, parsed in Python)")
    lib_ins.add_argument("--device", default=None)
    lib_rs = lib_sub.add_parser(
        "restore-from-backup",
        help="Mac-only Disk Mode copy of the three library files from a hashed backup",
    )
    lib_rs.add_argument("--backup", default=None)
    lib_rs.add_argument("--backup-sha256", default=None)
    lib_rs.add_argument("--device", default=None)
    lib_rs.add_argument("--skip-backup-file", action="store_true")
    lib_rs.add_argument(
        "--watch",
        action="store_true",
        help="wait for a FAT32 volume instead of asking you to confirm Disk Mode",
    )
    lib_rs.add_argument(
        "--skip-wait",
        action="store_true",
        help="copy onto whatever is already attached (still prefers Disk Mode)",
    )
    ap = sub.add_parser(
        "apps",
        help="list / add / refresh / set / cap / remove / yield / lasttrace / restore-tested Home apps (never deletes loader)",
    )
    ap_sub = ap.add_subparsers(dest="apps_cmd")
    ap_list = ap_sub.add_parser("list")
    ap_list.add_argument("--device", default=None)
    ap_add = ap_sub.add_parser(
        "add",
        help="merge one extra onto Home (Disk Mode, no NAND; Paint first)",
    )
    ap_add.add_argument("names", nargs="*")
    ap_add.add_argument("--all", action="store_true", help="hardware-tested three, not every extra")
    ap_add.add_argument(
        "--extras",
        action="store_true",
        help="refused: that 8-app set reboot-looped on MKN52",
    )
    ap_add.add_argument("--device", default=None)
    ap_add.add_argument("--backup", default=None)
    ap_add.add_argument("--backup-sha256", default=None)
    ap_add.add_argument("--replace", action="store_true")
    ap_add.add_argument("--skip-backup-file", action="store_true")
    ap_add.add_argument("--watch", action="store_true")
    ap_add.add_argument("--skip-wait", action="store_true")
    ap_add.add_argument(
        "--rom",
        default=None,
        help="USA .z64 for Mario 64 / Mario Kart (not stored in git)",
    )
    ap_add.add_argument(
        "--payload",
        "--ipa",
        dest="payload",
        default=None,
        help="original iOS .ipa or unpacked .app for Temple Run / Flappy Bird / Doodle Jump / Fruit Ninja; "
        "for PvZ Portable the folder with your own PvZ GOTY main.pak and properties/",
    )
    ap_add.add_argument(
        "--plan",
        action="store_true",
        help="Temple Run / Fruit Ninja / PvZ / PvZ Portable: check the game files, host tools, Home pack and memory; "
        "build and write nothing",
    )
    ap_add.add_argument("--dry-run", action="store_true", help="same as --plan")
    ap_add.add_argument(
        "--heap-free",
        default=None,
        help="--plan: Probe's 'heap free' (6.6M, 6758K or bytes)",
    )
    ap_add.add_argument(
        "--largest",
        default=None,
        help="--plan: Probe's 'largest block' (4.0M, 4096K or bytes)",
    )
    ap_in = ap_sub.add_parser("install", help="same as apps add")
    ap_in.add_argument("names", nargs="*")
    ap_in.add_argument("--all", action="store_true")
    ap_in.add_argument("--extras", action="store_true")
    ap_in.add_argument("--device", default=None)
    ap_in.add_argument("--backup", default=None)
    ap_in.add_argument("--backup-sha256", default=None)
    ap_in.add_argument("--replace", action="store_true")
    ap_in.add_argument("--skip-backup-file", action="store_true")
    ap_in.add_argument("--watch", action="store_true")
    ap_in.add_argument("--skip-wait", action="store_true")
    ap_in.add_argument("--rom", default=None)
    ap_in.add_argument("--payload", "--ipa", dest="payload", default=None)
    ap_rm = ap_sub.add_parser("remove")
    ap_rm.add_argument("names", nargs="*")
    ap_rm.add_argument("--all", action="store_true", help="tested three only, never the loader")
    ap_rm.add_argument("--device", default=None)
    ap_rm.add_argument("--backup", default=None)
    ap_rm.add_argument("--backup-sha256", default=None)
    ap_rm.add_argument("--skip-backup-file", action="store_true")
    ap_rm.add_argument("--watch", action="store_true")
    ap_rm.add_argument("--skip-wait", action="store_true")
    ap_rm.add_argument(
        "--keep-files",
        action="store_true",
        help="rewrite the pack only; leave Executables/Icons on disk (Blocks A/B)",
    )
    ap_pick = ap_sub.add_parser("pick", help="interactive picker, then apps add (one extra)")
    ap_pick.add_argument("--device", default=None)
    ap_pick.add_argument("--backup", default=None)
    ap_pick.add_argument("--backup-sha256", default=None)
    ap_pick.add_argument("--skip-backup-file", action="store_true")
    ap_pick.add_argument("--skip-wait", action="store_true")
    ap_pick.add_argument("--watch", action="store_true")
    ap_pick.add_argument("--all", action="store_true")
    ap_pick.add_argument("--extras", action="store_true")
    ap_pick.add_argument("names", nargs="*")
    ap_lt = ap_sub.add_parser(
        "lasttrace",
        help="read-only decode of /Apps/Startup/lasttrace.bin (previous-boot hb_trace)",
    )
    ap_lt.add_argument("--device", default=None)
    ap_set = ap_sub.add_parser(
        "set",
        help="plan or apply the suggested Home set (one extra per reboot; cap enable is its own step)",
    )
    ap_set.add_argument("names", nargs="*")
    ap_set.add_argument(
        "--plan",
        action="store_true",
        help="print the Disk Mode steps and exit (host only)",
    )
    ap_set.add_argument("--dry-run", action="store_true", help="same as --plan")
    ap_set.add_argument("--device", default=None)
    ap_set.add_argument("--backup", default=None)
    ap_set.add_argument("--backup-sha256", default=None)
    ap_set.add_argument("--skip-backup-file", action="store_true")
    ap_set.add_argument("--watch", action="store_true")
    ap_set.add_argument("--skip-wait", action="store_true")
    ap_set.add_argument("--rom", default=None)
    ap_set.add_argument("--payload", "--ipa", dest="payload", default=None)
    ap_cap = ap_sub.add_parser(
        "cap",
        help="nogrow overlay: skip grow_cache (host build / Disk Mode enable)",
    )
    ap_cap_sub = ap_cap.add_subparsers(dest="cap_cmd")
    ap_cap_sub.add_parser("status")
    ap_cap_sub.add_parser("build")
    ap_cap_en = ap_cap_sub.add_parser(
        "enable",
        help="Disk Mode copy of nogrow residents (no NAND, no pack change)",
    )
    ap_cap_lean = ap_cap_sub.add_parser(
        "lean",
        help="Disk Mode: nogrow + Home icon LRU 16 x 50 KB (frees 320 KB of boot heap); undo with cap enable",
    )
    ap_cap_dis = ap_cap_sub.add_parser(
        "disable",
        help="Disk Mode: tested residents back, keep packs + slot B (refuses if a pack has > 8 apps)",
    )
    for ap_c in (ap_cap_en, ap_cap_lean, ap_cap_dis):
        ap_c.add_argument("--device", default=None)
        ap_c.add_argument("--backup", default=None)
        ap_c.add_argument("--backup-sha256", default=None)
        ap_c.add_argument("--skip-backup-file", action="store_true")
        ap_c.add_argument("--watch", action="store_true")
        ap_c.add_argument("--skip-wait", action="store_true")
    ap_rf = ap_sub.add_parser(
        "refresh",
        help="re-copy this tree's build of every installed extra (one Disk Mode step; pack, icons, /Apps/Data kept)",
    )
    ap_rf.add_argument("--plan", action="store_true", help="read the mounted nano and list what would change")
    ap_rf.add_argument("--dry-run", action="store_true", help="same as --plan")
    ap_rf.add_argument("--undo", action="store_true", help="copy the .hbapp files saved by the last refresh back")
    ap_rf.add_argument("--device", default=None)
    ap_rf.add_argument("--backup", default=None)
    ap_rf.add_argument("--backup-sha256", default=None)
    ap_rf.add_argument("--skip-backup-file", action="store_true")
    ap_rf.add_argument("--watch", action="store_true")
    ap_rf.add_argument("--skip-wait", action="store_true")
    ap_rs = ap_sub.add_parser(
        "restore-tested",
        help="rewrite AllApps.pack to Blocks + Game Boy + Probe (Disk Mode recovery)",
    )
    ap_rs.add_argument("--device", default=None)
    ap_rs.add_argument("--backup", default=None)
    ap_rs.add_argument("--backup-sha256", default=None)
    ap_rs.add_argument("--skip-backup-file", action="store_true")
    ap_rs.add_argument("--watch", action="store_true")
    ap_rs.add_argument("--skip-wait", action="store_true")
    ap_yd = ap_sub.add_parser(
        "yield",
        help="hide named apps from Home, keep files (opt-in subset pack; not a runtime suspend)",
    )
    ap_yd.add_argument("names", nargs="*")
    ap_yd.add_argument(
        "--plan",
        action="store_true",
        help="print the pack hide and exit (host only, no NAND)",
    )
    ap_yd.add_argument("--dry-run", action="store_true", help="same as --plan")
    ap_yd.add_argument(
        "--i-accept-untested-pack",
        action="store_true",
        help="required to drop a hardware-tested app from Home (subset pack is untested)",
    )
    ap_yd.add_argument("--device", default=None)
    ap_yd.add_argument("--backup", default=None)
    ap_yd.add_argument("--backup-sha256", default=None)
    ap_yd.add_argument("--skip-backup-file", action="store_true")
    ap_yd.add_argument("--watch", action="store_true")
    ap_yd.add_argument("--skip-wait", action="store_true")
    th = sub.add_parser("theme", help="ipod_theme (GPL-3) separate mode, or gated Helvetica combine")
    th_sub = th.add_subparsers(dest="theme_cmd")
    th_sub.add_parser("status")
    th_sub.add_parser("preview")
    th_cl = th_sub.add_parser("clone")
    th_cl.add_argument("--force", action="store_true")
    th_un = th_sub.add_parser("unpack")
    th_un.add_argument("ipsw", nargs="?", default=None)
    th_sub.add_parser("pack")
    th_cb = th_sub.add_parser("combine", aliases=["inject"])
    th_cb.add_argument("--silverdb", default=None)
    th_cb.add_argument("--rsrc", default=None)
    th_cb.add_argument("--mse", default=None)
    th_cb.add_argument("--font", action="append", default=None)
    th_cb.add_argument("--extra", action="append", default=None, help="FATPATH=FILE")
    th_cb.add_argument("--i-accept-untested-firmware", action="store_true")
    th_cb.add_argument("--i-accept-themed-untether", action="store_true")
    th_im = th_sub.add_parser(
        "import",
        help="extract SilverImagesDB from a community IPSW/MSE/bin (art only, never flashes)",
    )
    th_im.add_argument("source", help="IPSW, Firmware.MSE, SilverImagesDB.LE.bin, or a folder")
    th_im.add_argument("--out", default=None, help="directory for the extracted art (default: build/theme-import)")
    th_im.add_argument(
        "--combine",
        action="store_true",
        help="also splice into the tested Helvetica MSE (needs the two accept flags)",
    )
    th_im.add_argument("--mse", default=None, help="Helvetica MSE to splice into (default: build/firmware/Firmware-untethered.MSE)")
    th_im.add_argument("--i-accept-untested-firmware", action="store_true")
    th_im.add_argument("--i-accept-themed-untether", action="store_true")
    th_sub.add_parser("revert")
    sub.add_parser("wizard")
    sub.add_parser(
        "perf",
        help="heap/staging readout + Blocks A/B (host; Probe file in Disk Mode)",
    )
    return p


def cmd_fetch(args) -> int:
    from . import upstream

    if getattr(args, "check", False):
        bad = upstream.missing_or_changed()
        if bad:
            print(f"FETCH_CHECK_FAILED: {len(bad)} file(s) missing or changed, e.g. {bad[0]}. Run ./start fetch")
            return 2
        print("FETCH_CHECK_OK: every third-party file matches upstream/lock.json")
        return 0
    upstream.fetch()
    print("FETCH_OK")
    return 0


def main(argv=None) -> int:
    p = build_parser()
    args = p.parse_args(argv)
    cmd = args.cmd
    if getattr(args, "verbose", False):
        os.environ["NANO7KIT_VERBOSE"] = "1"
    try:
        if cmd and cmd not in (
            "doctor",
            "fetch",
            "deps",
            "setup",
            "build",
            "build-firmware",
            "verify-release",
            "build-games",
            "tethered",
            "wizard",
            "find",
            "untether",
            "install",
            "sync",
            "disk-mode",
            "theme",
            "apps",
            "ports",
            "build-games",
            "port",
            "sdk",
            "pocketjs",
            "resident",
            "package",
            "nanoapp",
            "perf",
            "nabase",
        ):
            if not use_ssh(args):
                maybe_sudo(cmd)
        dispatch = {
            "fetch": cmd_fetch,
            "doctor": cmd_doctor,
            "deps": cmd_deps,
            "setup": cmd_setup,
            "find": cmd_find,
            "backup": cmd_backup,
            "backup-stream": cmd_backup_stream,
            "unmount": cmd_unmount,
            "fsck": cmd_fsck,
            "build-firmware": cmd_build_firmware,
            "verify-release": cmd_verify,
            "nabase": cmd_nabase,
            "install-untether": cmd_install_untether,
            "install": cmd_install,
            "roms": cmd_roms,
            "flash": cmd_flash,
            "untether": cmd_untether,
            "build": cmd_build,
            "tethered": cmd_tethered,
            "build-games": cmd_games_build,
            "install-stage": cmd_install_stage,
            "scsi-probe": cmd_scsi_probe,
            "scsi-dump": cmd_scsi_dump,
            "bootloader": cmd_bootloader,
            "sync": cmd_sync,
            "disk-mode": cmd_sync,
            "fat32": cmd_fat32,
            "winpod": cmd_fat32,
            "rescue": cmd_rescue,
            "apps": cmd_apps,
            "library": cmd_library,
            "theme": cmd_theme,
            "ports": cmd_ports,
            "port": cmd_port,
            "sdk": cmd_sdk,
            "pocketjs": cmd_pocketjs,
            "resident": cmd_resident,
            "package": cmd_package,
            "nanoapp": cmd_nanoapp,
            "perf": cmd_perf,
        }
        if cmd in dispatch:
            return dispatch[cmd](args)
        return wizard(args)
    except Refused as e:
        print(str(e), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\naborted")
        return 130
