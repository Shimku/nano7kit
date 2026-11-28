"""PebbleOS (Apache-2.0): hosted as one NanoApps app on the nano 7G.

Fetches coredevices/PebbleOS at a pinned commit. Does not vendor the tree.
The NanoApps shim is nanoapps/apps/pebbleos. The hosted kernel is not
linked yet; ./start pebbleos build still compiles the shim, memory screen
and button map so they can be tested.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .compat import Refused, root


PEBBLEOS_REPO = "https://github.com/coredevices/PebbleOS"
PEBBLEOS_COMMIT = "94e3d98daa0dfd6bd204ca1973f1b502eed07d5f"
PEBBLEOS_LICENSE = "Apache-2.0"
PEBBLEOS_TITLE = "PebbleOS"


def checkout() -> Path:
    return root() / "build" / "pebbleos"


def print_status() -> int:
    dest = checkout()
    present = (dest / ".git").is_dir() or (dest / "CMakeLists.txt").is_file()
    print("PebbleOS hosted on this MKN52 (Helvetica NanoApps)")
    print("------------------------------------------------")
    print("Real PebbleOS, fetched at a pinned commit, hosted as one app.")
    print("Not a recreation. The kernel is not linked into the app yet.")
    print()
    print("  repo       " + PEBBLEOS_REPO)
    print("  licence    " + PEBBLEOS_LICENSE + " (credited, not vendored)")
    print("  pinned     " + PEBBLEOS_COMMIT)
    print("  checkout   " + str(dest) + (" (present)" if present else " (fetched by ./start pebbleos fetch)"))
    print("  app        nanoapps/apps/pebbleos")
    print("  screen     240x432, 200x228 Pebble panel centred")
    print("  buttons    Home=Back, Play/Pause=Select, Vol Up/Down=Up/Down")
    print("  memory     must run at 3.9 MB/2 MB and at 6 MB/4 MB;")
    print("             otherwise a NOT ENOUGH MEMORY screen")
    print()
    print("Not ready for the iPod until a device-like host run passes")
    print("(no host libc unwinder, device .hbapp layout, Cortex-A5 VFPv4-D16).")
    print()
    print("exact: ./start pebbleos fetch")
    print("       ./start sdk build pebbleos")
    print("       ./start nabase add 'PebbleOS'")
    return 0


def fetch(*, force: bool = False) -> Path:
    dest = checkout()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_dir() and not force:
        head = subprocess.run(
            ["git", "-C", str(dest), "rev-parse", "HEAD"],
            capture_output=True, text=True,
        )
        if head.returncode == 0 and head.stdout.strip() == PEBBLEOS_COMMIT:
            print(f"PebbleOS already at {PEBBLEOS_COMMIT}")
            return dest
        # Wrong or dirty checkout: start again.
        subprocess.check_call(["rm", "-rf", str(dest)])
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(["git", "init", "-q", str(dest)])
    subprocess.check_call(
        ["git", "fetch", "-q", "--depth", "1", PEBBLEOS_REPO, PEBBLEOS_COMMIT],
        cwd=dest,
    )
    subprocess.check_call(
        ["git", "-c", "advice.detachedHead=false", "checkout", "-q", "--detach", "FETCH_HEAD"],
        cwd=dest,
    )
    head = subprocess.check_output(["git", "-C", str(dest), "rev-parse", "HEAD"], text=True).strip()
    if head != PEBBLEOS_COMMIT:
        raise Refused(f"PebbleOS: got commit {head}, expected {PEBBLEOS_COMMIT}")
    print(f"Fetched PebbleOS {PEBBLEOS_COMMIT}")
    return dest


def build() -> dict:
    from .sdkapp import build_app

    result = build_app("pebbleos")
    print("On a NanoApps-base iPod (Disk Mode):")
    print("  ./start nabase add 'PebbleOS'")
    print("Do not use ./start apps add on that base.")
    return result
