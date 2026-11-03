"""Music sync after install: Disk Mode, then Apple's tools.

This toolkit never writes iTunes_Control / iPod_Control. Linux music-database
tools have boot-looped a nano 7G. Finder/Music on a FAT32 volume is the
supported writer. After ipod_sun / untether firmware, RetailOS can reboot-loop
when those apps talk SCSI; Disk Mode is the documented-safe workaround.

Torn Library.itdb / empty iTunesCDB after Helvetica is the library DB, not
a wipe of the song files. Rebuild that DB here, or restore the three files
from the hashed FAT32 backup. Do not Finder Restore: Restore creates HFS+
and deletes /Apps (Blocks / Game Boy / Probe).
"""

from __future__ import annotations

import os
import sys
import time
from typing import Optional

from .compat import Refused, is_macos
from .device import format_nano, find_nanos
from .fat import assert_not_hfs
from .usb import apple_pids, usb_revision_notes


DISK_MODE_STEPS = """Disk Mode music rebuild (torn Library.itdb / empty iTunesCDB):

  0. Quit Music.app. Close Finder windows named IPOD. Unplug the nano.
     Do not leave Music attached while Home shows Connected (RetailOS SCSI
     reboot-loop after Helvetica).
  1. Enter Disk Mode (this is not DFU and not Home Connected):
     - Hold Sleep/Wake (top) and Home together.
     - As soon as the Apple logo appears, quickly switch to holding
       Volume Up and Volume Down.
     - Keep holding them until the screen says "Do not disconnect".
     If you see the Home screen, it is not Disk Mode.
     A screen that stays black is DFU (wrong mode for music).
  2. Plug into the USB-A hub on this Mac.
  3. Run:  ./start sync
     This command snapshots /Apps hashes so a Restore cannot silently
     wipe Blocks / Game Boy / Probe, then hands off to Music.
     If Library.itdb / Dynamic.itdb / iTunesCDB are torn, run
     ./start library restore-from-backup instead of rebuilding in Music.
  4. Open Music (macOS 13 Music.app; older macOS: iTunes). Finder also
     works as a USB disk. Select the iPod in the sidebar.
     - Prefer Manually manage music / videos.
     - Do not click Restore. Restore creates HFS+ and deletes /Apps.
     - Music rewrites iTunesCDB. Song files under iPod_Control can still
       be there even when About showed 0 songs.
  5. Eject IPOD. Unplug.
  6. Restart: hold Sleep/Wake and Home until the Apple logo, then let go.
     Blocks, Game Boy, Probe should still be there (/Apps is not music).

One command: ./start sync
Torn DBs: ./start library restore-from-backup --backup IMAGE --backup-sha256 HASH
Game Boy ROMs (not music): ./start roms add FILE.gb   also Disk Mode.
Home extras: ./start apps add Paint   (one extra per reboot; eight-extra set looped)"""


def verbose() -> bool:
    return os.environ.get("NANO7KIT_VERBOSE", "") not in ("", "0")


def print_disk_mode_steps(*, full: bool = False) -> None:
    """Short Disk Mode combo. The long music text only with --verbose or ./start sync."""
    if full or verbose():
        print(DISK_MODE_STEPS)
        return
    from .buttons import show

    show("disk")
    print("  Music: if the nano shows 0 songs later, run ./start sync")


def print_music_handoff(*, fat32: bool) -> None:
    print()
    print("Apple's tools write the music database. This toolkit will not.")
    print("Do not use gtkpod, libgpod, Rhythmbox, or any Linux iPod DB writer.")
    if not fat32:
        return
    print()
    if is_macos():
        print("On this Mac:")
        print("  - Finder should show the nano as a USB disk named IPOD.")
        print("  - Open Music (or iTunes on older macOS). Select the iPod in the sidebar.")
        print("  - Prefer Manually manage music / videos.")
        print("  - Do not click Restore. Restore reformats HFS+ and wipes /Apps.")
        print("  - Torn Library.itdb / empty iTunesCDB is the library DB. Music rewrites it,")
        print("    or ./start library restore-from-backup copies the three files.")
        print("  - If Music never sees the iPod, the volume is still usable as a disk;")
        print("    copy files only if you will let Music rewrite the library later.")
        print("  - Sequoia sometimes needs the iPod's Enable disk use setting on stock")
        print("    firmware. Do not kill system agents as a workaround.")
    else:
        print("On Linux, do not write the iPod database. Use a Mac Music/Finder")
        print("session in Disk Mode (./start sync).")
    print()
    print("When Music/Finder/iTunes finishes, eject, then restart: hold Sleep/Wake and Home until the Apple logo.")
    print("Confirm Blocks, Game Boy, Probe are still on Home. Full write-up: docs/SYNC.md")


def snapshot_apps_protect(nano) -> dict:
    """Hashes of tested /Apps loader files. Restore would drop these."""
    from .untether import apps_markers, check_loader_hashes

    apps = apps_markers(nano)
    hashes = check_loader_hashes(nano)
    print(
        f"    /Apps      protect snapshot loader={'yes' if hashes.get('ok') else 'no'} "
        f"{hashes.get('matched', 0)}/{hashes.get('expected', 0)} "
        f"icons={','.join(apps.get('icons') or []) or 'none'}"
    )
    if hashes.get("ok"):
        print("               Do not Restore. Restore wipes /Apps and creates HFS+.")
    elif not apps.get("ok"):
        print("               /Apps not visible. Icons need ./start install-untether.")
    else:
        print("               /Apps present but hashes incomplete. Do not Restore.")
    return {"apps": apps, "hashes": hashes}


def recheck_apps_protect(before: Optional[dict]) -> None:
    from .untether import check_loader_hashes

    nanos = find_nanos()
    if not nanos:
        print("    /Apps      not attached after handoff; recheck after Sleep/Wake+Home.")
        return
    nano = nanos[0]
    try:
        assert_not_hfs(nano)
    except Refused as e:
        print(str(e), file=sys.stderr)
        print(
            "    /Apps      volume is HFS+. Finder Restore likely ran. "
            "That wipes /Apps. ./start fat32 is recovery, then install-untether."
        )
        return
    after = check_loader_hashes(nano)
    print(
        f"    /Apps      after snapshot {after.get('matched', 0)}/{after.get('expected', 0)} "
        f"ok={'yes' if after.get('ok') else 'no'}"
    )
    if before and before.get("hashes", {}).get("ok") and not after.get("ok"):
        print(
            "    /Apps      WARNING: loader hashes no longer match. "
            "If you clicked Restore, /Apps is gone. Re-run ./start install-untether "
            "(no firmware write). Do not re-flash."
        )


def describe_attached() -> int:
    notes = usb_revision_notes()
    pids = apple_pids()
    if pids:
        shown = ", ".join(f"05ac:{pid:04x}" for pid in pids)
        print(f"Apple USB now: {shown}")
    else:
        print("Apple USB now: none")
    for n in notes:
        print(f"  {n}")
    nanos = find_nanos()
    if not nanos:
        print("No iPod mass-storage volume yet.")
        return 1
    for n in nanos:
        print(format_nano(n))
        try:
            assert_not_hfs(n)
        except Refused as e:
            print(str(e), file=sys.stderr)
            return 2
    return 0


def wait_for_enter_or_watch(*, watch: bool, timeout: int, full: bool = False) -> None:
    print_disk_mode_steps(full=full)
    if watch:
        print(f"Watching for a FAT iPod for {timeout} seconds...")
        deadline = time.time() + timeout
        while time.time() < deadline:
            nanos = [n for n in find_nanos() if n.fstype == "vfat"]
            if nanos:
                print("FAT iPod seen.")
                return
            time.sleep(1)
        raise Refused(
            f"no FAT32 iPod appeared in {timeout}s. Confirm Disk Mode "
            "(Do not disconnect while plugged in), then re-run ./start sync."
        )
    if sys.stdin.isatty():
        print("Anything typed or pasted here is ignored; only Enter counts. Run the next")
        print("command after this one prints its result.")
        input("Press Enter when Disk Mode says Do not disconnect (nano is plugged in)... ")
        return
    raise Refused(
        "non-interactive sync needs --watch (waits for a FAT32 volume) "
        "or a TTY so you can confirm Disk Mode."
    )


def run_sync(*, watch: bool = False, timeout: int = 180, skip_wait: bool = False) -> int:
    """Guide Disk Mode, then hand off to Music/Finder. Never writes the DB."""
    print("nano7kit music sync")
    print("This command does not copy songs and does not write iTunes_Control.")
    print("It snapshots /Apps so Restore cannot silently wipe homebrew.")
    print()
    print("Safest order:")
    print("  1. ./start backup (hashed raw image; music need not be kept).")
    print("  2. ./start fat32 if the nano is HFS+ / Mac-formatted (no Windows).")
    print("  3. Sync with Music/Finder while firmware is still stock.")
    print("  4. ./start install (or untether) so /Apps + Helvetica are on the nano.")
    print("  5. Later library edits: Disk Mode, then Music/Finder, then reboot.")
    print()
    if skip_wait:
        print_disk_mode_steps(full=True)
        print()
    protect = None
    nanos = find_nanos()
    if nanos:
        print("Currently attached:")
        for n in nanos:
            print(format_nano(n))
            print()
        n0 = nanos[0]
        if n0.fstype != "vfat":
            assert_not_hfs(n0)
        else:
            print(
                "RetailOS (Home screen showing Connected) can reboot-loop when "
                "Music or Finder talks SCSI. Prefer Disk Mode before those apps."
            )
            print()
            try:
                protect = snapshot_apps_protect(n0)
            except Refused:
                protect = None
            try:
                from .library import inspect_library_mount, print_library_report
                from .fat import ensure_fat_mount

                root = ensure_fat_mount(n0)
                if root is not None:
                    print_library_report(inspect_library_mount(root), title="library")
            except Refused:
                pass
    if not skip_wait:
        wait_for_enter_or_watch(watch=watch, timeout=timeout, full=True)
        print()
    rc = describe_attached()
    fat32 = False
    try:
        nanos = find_nanos()
        fat32 = bool(nanos) and all(n.fstype == "vfat" for n in nanos)
        if nanos and protect is None:
            try:
                protect = snapshot_apps_protect(nanos[0])
            except Refused:
                protect = None
        elif nanos:
            recheck_apps_protect(protect)
    except Refused:
        fat32 = False
    print_music_handoff(fat32=fat32)
    return rc
