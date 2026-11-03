# SPDX-License-Identifier: MIT
"""./install: pick apps and games from a menu, then run the right ./start steps in order.

Everything that touches the iPod is done by ./start with its safety checks.
This file only decides what to run, asks for your files, and keeps the output short.
"""

from __future__ import annotations

import hashlib
import os
import platform
import plistlib
import re
import shlex
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import buttons

ROOT = Path(__file__).resolve().parent.parent
START = ROOT / "start"
DROP_DIR = ROOT / "mygames"
BASE_APPS = ("Blocks", "Game Boy", "Probe")
PACK_LIMIT = 8          # stock loader
PACK_LIMIT_LEAN = 16    # with apps cap lean or the Launcher (hardware: 12 lean, 14 with the Launcher)

SM64_SHA1 = "9bef1128717f958171a4afac3ed78ee2bb4e86ce"
MK64_MD5 = "3a67d9986f54eb282924fca4cd5f6dff"
PVZ_PAK_SHA256 = "0cbdba7ecd6106cca3190976061922ef61011c1896205b10cbc204aaa9d88cba"


# ---------------------------------------------------------------- catalog


@dataclass(frozen=True)
class Item:
    key: str              # what the user sees
    name: str             # ./start apps add NAME
    group: str            # apps | games | pocketjs
    needs: str = ""       # one line: what you supply ("" = nothing)
    kind: str = "none"    # none | rom | ipa | folder
    tested: bool = True   # run on a real nano
    blurb: str = ""
    bundle: str = ""      # iOS bundle id for kind == ipa
    version: str = ""     # iOS version we accept


ITEMS: Tuple[Item, ...] = (
    # Loader extras
    Item("Launcher", "resident-na", "tools",
         blurb="NanoApps 1.0-based launcher with room for large games (keeps a copy of the old one)"),
    Item("Paint folder", "Paint.nanoapp", "tools",
         blurb="same app as Paint below, as a .nanoapp folder (not one of the 16). Pick one"),
    Item("Free RAM folder", "Free RAM.nanoapp", "tools",
         blurb="same app as Free RAM below, as a .nanoapp folder (not one of the 16). Pick one"),
    # Apps: nothing to supply
    Item("Calculator", "Calculator", "apps"),
    Item("Notes", "Notes", "apps"),
    Item("Chess", "Chess", "apps"),
    Item("Books", "Books", "apps"),
    Item("Wallet", "Wallet", "apps"),
    Item("Screenshot", "Screenshot", "apps"),
    Item("Paint", "Paint", "apps"),
    Item("Tetris", "Tetris", "apps"),
    Item("Snake", "Snake", "apps"),
    Item("2048", "G2048", "apps"),
    Item("Solitaire", "Solitaire", "apps"),
    Item("Sudoku", "Sudoku", "apps"),
    Item("Minesweeper", "Minesweeper", "apps"),
    Item("Wordle", "Wordle", "apps"),
    Item("Clock", "Clock", "apps"),
    Item("Calendar", "Calendar", "apps"),
    Item("Lap", "Lap", "apps", blurb="stopwatch"),
    # Games: bring your own file
    Item("Mario 64", "Mario 64", "games", "your Super Mario 64 (USA) .z64 ROM", "rom"),
    Item("Mario Kart 64", "Mario Kart", "games", "your Mario Kart 64 (USA) .z64 ROM", "rom", tested=False),
    Item("Temple Run", "Temple Run", "games", "your decrypted Temple Run 1.0 .ipa", "ipa",
         bundle="com.imangi.templerun", version="1.0"),
    Item("Fruit Ninja", "Fruit Ninja", "games", "your decrypted Fruit Ninja 1.0 .ipa", "ipa",
         bundle="com.Halfbrick.Fruit", version="1.0"),
    Item("PvZ Portable", "PvZ Portable", "games",
         "your PC Plants vs. Zombies GOTY folder (main.pak + properties/)", "folder"),
    Item("Flappy Bird", "Flappy Bird", "games", "your Flappy Bird 1.2 .ipa", "ipa", tested=False,
         version="1.2"),
    Item("Doodle Jump", "Doodle Jump", "games", "your Doodle Jump 1.0 .ipa", "ipa", tested=False,
         version="1.0"),
    # PocketJS: optional
    Item("JS QuickJS", "JS QuickJS", "pocketjs",
         blurb="home screen with Notes, Talk, Motion Lab, Cafe, Physics, Tilt and more"),
    Item("JS Hero", "JS Hero", "pocketjs", blurb="the PocketJS hero demo"),
    Item("JS Samples", "JS Samples", "pocketjs", blurb="GBA Hero, Nano 2G Hero, Solid and Vue demos"),
    Item("Free RAM", "Free RAM", "pocketjs", blurb="free memory, and whether JS QuickJS fits right now"),
    # Everything else in NanoApps
    Item("Benchmark", "Benchmark", "more"),
    Item("Breakout", "Breakout", "more"),
    Item("Connect4", "Connect4", "more"),
    Item("Contacts", "Contacts", "more"),
    Item("Countdown", "Countdown", "more"),
    Item("Crosswords", "Crosswords", "more"),
    Item("Dictionary", "Dictionary", "more"),
    Item("Elements", "Elements", "more"),
    Item("Files", "Files", "more"),
    Item("Freecell", "Freecell", "more"),
    Item("GLBench", "GLBench", "more"),
    Item("Golf", "Golf", "more"),
    Item("Heads Up", "Heads Up", "more"),
    Item("Holdem", "Holdem", "more"),
    Item("Level", "Level", "more"),
    Item("Mahjong", "Mahjong", "more"),
    Item("Match3", "Match3", "more"),
    Item("OpenGL", "OpenGL", "more"),
    Item("Passwords", "Passwords", "more"),
    Item("Pong", "Pong", "more"),
    Item("Quiz", "Quiz", "more"),
    Item("Reminders", "Reminders", "more"),
    Item("Remote", "Remote", "more"),
    Item("Rhythm", "Rhythm", "more"),
    Item("Sensors", "Sensors", "more"),
    Item("Simon Says", "Simon Says", "more"),
    Item("Stress", "Stress", "more"),
    Item("Tictactoe", "Tictactoe", "more"),
    Item("Tilt Ball", "Tilt Ball", "more"),
    Item("Tower", "Tower", "more"),
    Item("Widgets", "Widgets", "more"),
)

GROUP_TITLES = {
    "tools": "Launcher and .nanoapp folders (ticked by default: Launcher)",
    "apps": "Apps (nothing to supply; NanoApps ones compile on your Mac in seconds)",
    "games": "Games (bring your own file: put it in mygames/ or give the path)",
    "pocketjs": "PocketJS (optional: JavaScript apps)",
    "more": "More NanoApps",
}
COMPACT_GROUPS = {"more"}


def prebuilt(name: str) -> bool:
    """True when the app's .hbapp is already here (ours ship; NanoApps ones build on first add)."""
    return (ROOT / "untether" / "artifacts" / "extra" / "Executables" / f"{name}.hbapp").is_file()


def numbered() -> Dict[int, Item]:
    return {i: item for i, item in enumerate(ITEMS, 1)}


def by_name(name: str) -> Optional[Item]:
    for item in ITEMS:
        if item.name == name or item.key == name:
            return item
    return None


# ---------------------------------------------------------------- state


@dataclass
class MacInfo:
    system: str = ""
    arch: str = ""          # arm64 | x86_64
    rosetta: bool = False
    version: str = ""

    def label(self) -> str:
        if self.system == "Darwin":
            chip = "Apple silicon" if self.arch == "arm64" or self.rosetta else "Intel"
            out = f"{chip} Mac, macOS {self.version}"
            if self.rosetta:
                out += " (Terminal is running under Rosetta: open it natively)"
            return out
        if self.system == "Linux":
            return f"Linux ({self.arch}), command line only: ./start"
        return f"{self.system} ({self.arch}), not supported"


@dataclass
class NanoState:
    present: bool = False
    mount: Optional[Path] = None
    fstype: str = ""
    size: int = 0
    dfu: bool = False
    loader: bool = False
    home: List[str] = field(default_factory=list)
    lean: bool = False
    na: bool = False      # the nano7kit Launcher is installed (slot B, or /Library on NanoApps)
    base: str = ""        # untethered | nanoapps | mixed | none (host/nabase.detect_base)
    folders: List[str] = field(default_factory=list)   # /Apps/*.nanoapp on the NanoApps base

    @property
    def nanoapps(self) -> bool:
        return self.base == "nanoapps"

    def label(self) -> str:
        if self.dfu:
            return "iPod nano in recovery (DFU) mode"
        if not self.present:
            return "not found (plug it in, then put it in Disk Mode)"
        gb = f"{self.size / 1e9:.0f} GB" if self.size else "?"
        if self.nanoapps:
            return f"iPod nano, {gb}, NanoApps 1.0 base ({len(self.home)} apps on Home)"
        if self.fstype and self.fstype not in ("vfat", "msdos", "fat32"):
            return f"iPod nano, {gb}, formatted for Mac ({self.fstype}): see docs/FAT32.md"
        if not self.loader:
            return f"iPod nano, {gb}, no homebrew yet"
        return f"iPod nano, {gb}, homebrew installed ({len(self.home)} apps on Home)"


def detect_mac() -> MacInfo:
    info = MacInfo(system=platform.system(), arch=platform.machine())
    if info.system == "Darwin":
        info.version = platform.mac_ver()[0]
        try:
            out = subprocess.run(["sysctl", "-n", "sysctl.proc_translated"], capture_output=True, text=True)
            info.rosetta = out.stdout.strip() == "1"
        except OSError:
            pass
    return info


def read_home(mount: Path) -> Tuple[bool, List[str], bool]:
    """(loader installed, Home app names, cap lean on) from a mounted nano."""
    from .apps import pack_app_names

    apps = mount / "Apps"
    startup = apps / "Startup"
    loader = (startup / "resident-A.hbapp").is_file() or (startup / "resident.hbapp").is_file()
    pack = apps / ("AllApps-B.pack" if (startup / "resident-B.active").is_file() else "AllApps.pack")
    names: List[str] = []
    if pack.is_file():
        try:
            names = pack_app_names(pack.read_bytes())
        except Exception:
            names = []
    lean = (startup / "resident-lean.active").is_file() or (startup / "resident-nogrow.active").is_file()
    return loader, names, lean


def launcher_installed(mount: Path) -> bool:
    """True if /Apps/Startup/resident-B.hbapp is a nano7kit Launcher image."""
    import hashlib

    from . import const

    b = mount / "Apps" / "Startup" / "resident-B.hbapp"
    try:
        return hashlib.sha256(b.read_bytes()).hexdigest() in const.NA_B_ALL_SHA256S
    except OSError:
        return False


LAUNCHER = "Launcher"
REMOVE = "remove:"      # selection entries that remove an app already on the nano


def removals(selected: Iterable[str]) -> List[str]:
    """Keys marked [remove], in the order they were typed."""
    return [s[len(REMOVE):] for s in selected if s.startswith(REMOVE)]


def additions(selected: Iterable[str]) -> List[str]:
    return [s for s in selected if not s.startswith(REMOVE)]


def default_selection(state: "NanoState") -> List[str]:
    """What the menu starts with: the Launcher, unless it is already on the nano."""
    return [] if state.na else [LAUNCHER]


def is_on(item: "Item", state: "NanoState") -> bool:
    if item.name.endswith(".nanoapp"):
        return item.name[: -len(".nanoapp")] in state.folders
    return item.name in state.home or (item.name == "resident-na" and state.na)


def home_limit(state: "NanoState") -> int:
    if state.nanoapps:
        from .nabase import HOME_LIMIT

        return HOME_LIMIT
    return PACK_LIMIT_LEAN


FAT_TYPES = ("vfat", "msdos", "fat32")


def detect_nano() -> NanoState:
    state = NanoState()
    try:
        from .usb import dfu_present

        state.dfu = dfu_present()
    except Exception:
        state.dfu = False
    try:
        from .device import find_nanos

        nanos = find_nanos()
    except Exception:
        nanos = []
    if not nanos:
        return state
    n = nanos[0]
    state.present = True
    state.size = n.size
    state.fstype = n.fstype
    if n.mountpoints:
        state.mount = Path(n.mountpoints[0])
        state.loader, state.home, state.lean = read_home(state.mount)
        state.na = launcher_installed(state.mount)
        read_base(state)
    return state


def read_base(state: NanoState, force: str = "") -> None:
    """Fill base, and on the NanoApps base the Home list and Launcher flag."""
    from . import nabase

    try:
        state.base = force or nabase.detect_base(state.mount)
    except Exception:
        state.base = ""
    if state.nanoapps:
        state.loader = True
        state.folders = nabase.folder_apps(state.mount)
        state.home = nabase.home_names(state.mount)
        state.na = nabase.launcher_state(state.mount)["kind"] == "ours"


# ---------------------------------------------------------------- menu logic


def home_count(state: NanoState, selected: Iterable[str]) -> int:
    selected = list(selected)
    gone = {by_name(k).name for k in removals(selected) if by_name(k)}
    on = (set(state.home) or set(BASE_APPS)) - gone
    if state.nanoapps:
        on |= set(BASE_APPS) - gone
    extras = set()
    for s in additions(selected):
        item = by_name(s)
        if item is None:
            extras.add(s)
        elif item.group != "tools":
            extras.add(item.name)
        elif item.name.endswith(".nanoapp") and state.nanoapps:
            extras.add(item.name[: -len(".nanoapp")])
    return len(on | extras)


def toggle(text: str, selected: List[str], state: NanoState) -> Tuple[List[str], str]:
    """Apply a line typed at the menu. Returns (new selection, message).

    A number ticks a new app, or marks an app already on the nano [remove].
    Typing it again undoes either. The Launcher can't be removed here."""
    table = numbered()
    out = list(selected)
    msgs = []
    tokens = re.split(r"[\s,]+", text.strip())
    for tok in tokens:
        if not tok:
            continue
        if not tok.isdigit() or int(tok) not in table:
            msgs.append(f"{tok}: not on the list")
            continue
        item = table[int(tok)]
        if is_on(item, state):
            if item.name == "resident-na":
                msgs.append(launcher_message(state))
            elif REMOVE + item.key in out:
                out.remove(REMOVE + item.key)
            else:
                out.append(REMOVE + item.key)
            continue
        if item.key in out:
            out.remove(item.key)
            continue
        if home_count(state, out + [item.key]) > home_limit(state):
            msgs.append(f"{item.key}: the Home screen holds {home_limit(state)} apps at most")
            continue
        out.append(item.key)
    return out, "; ".join(msgs)


def launcher_message(state: Optional["NanoState"] = None) -> str:
    if state is not None and state.nanoapps:
        return ("Launcher: can't be removed here. To go back to NanoApps' own launcher, "
                "put the nano in Disk Mode and run: ./start nabase restore")
    return ("Launcher: can't be removed here. To go back to the old launcher, "
            "put the nano in Disk Mode and run: ./start resident restore")


def render_menu(mac: MacInfo, state: NanoState, selected: Sequence[str]) -> str:
    lines = ["", "nano7kit installer", ""]
    lines.append(f"  Mac    {mac.label()}")
    lines.append(f"  iPod   {state.label()}")
    lines.append("")
    lines.append("Type the numbers you want, separated by spaces (for example: 1 2 10), then press Enter.")
    lines.append("Type a number again to undo it.")
    lines.append("[on] = already on your iPod. Type its number to remove it ([remove]).")
    lines.append("Press Enter on an empty line when you are done.")
    lines.append("Always included: Blocks, Game Boy, Probe. These and an installed Launcher")
    if state.nanoapps:
        lines.append("can't be removed here (NanoApps' launcher back: ./start nabase restore).")
    else:
        lines.append("can't be removed here (old launcher back: ./start resident restore).")
    group = None
    row: List[str] = []
    for i, item in numbered().items():
        if item.group != group:
            if row:
                lines.append("".join(row).rstrip())
                row = []
            group = item.group
            lines.append("")
            lines.append(GROUP_TITLES[group])
        if is_on(item, state):
            box = "[remove]" if REMOVE + item.key in selected else "[on]"
        else:
            box = "[x]" if item.key in selected else "[ ]"
        box = f"{box:<8}"
        if group in COMPACT_GROUPS:
            row.append(f"  {i:>2} {box} {item.key:<12}")
            if len(row) == 3:
                lines.append("".join(row).rstrip())
                row = []
            continue
        extra = item.needs or item.blurb
        tag = "" if item.tested else "  (untested)"
        lines.append(f"  {i:>2} {box} {item.key:<15}{('  ' + extra) if extra else ''}{tag}".rstrip())
    if row:
        lines.append("".join(row).rstrip())
    lines.append("")
    lines.append(f"Home apps: {home_count(state, selected)} of {home_limit(state)}.  q = quit")
    return "\n".join(lines)


# ---------------------------------------------------------------- your files


def clean_path(text: str) -> str:
    """A path typed or dragged into Terminal: strip quotes and backslash escapes."""
    t = text.strip()
    if not t:
        return ""
    try:
        parts = shlex.split(t)
        if len(parts) == 1:
            t = parts[0]
    except ValueError:
        t = t.strip("'\"")
    return os.path.expanduser(t)


def _digest(path: Path, algo: str) -> str:
    h = hashlib.new(algo)
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_bundle(path: Path) -> Dict[str, str]:
    """CFBundleIdentifier and version of an .ipa or an unpacked .app, without unpacking the game."""
    info: dict = {}
    if path.is_dir():
        apps = [path] if path.suffix.lower() == ".app" else sorted((path / "Payload").glob("*.app")) or sorted(path.glob("*.app"))
        if apps and (apps[0] / "Info.plist").is_file():
            info = plistlib.loads((apps[0] / "Info.plist").read_bytes())
    elif zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", name):
                    info = plistlib.loads(z.read(name))
                    break
    return {
        "bundle": str(info.get("CFBundleIdentifier") or ""),
        "version": str(info.get("CFBundleShortVersionString") or info.get("CFBundleVersion") or ""),
    }


def check_file(item: Item, path: Path) -> Tuple[bool, str]:
    """Is this the file the game needs? Plain one-line answer."""
    p = Path(path)
    if not p.exists():
        return False, f"not found: {p}"
    if item.kind == "rom":
        if not p.is_file():
            return False, "that is a folder; pick the .z64 file"
        if item.name == "Mario 64":
            ok = _digest(p, "sha1") == SM64_SHA1
            return (True, "Super Mario 64 (USA) ROM, checksum OK") if ok else (
                False, "not the Super Mario 64 (USA) .z64 this port needs (checksum differs)")
        ok = _digest(p, "md5") == MK64_MD5
        return (True, "Mario Kart 64 (USA) ROM, checksum OK") if ok else (
            False, "not the Mario Kart 64 (USA) .z64 this port needs (checksum differs)")
    if item.kind == "folder":
        if not p.is_dir():
            return False, "pick the game folder that holds main.pak and properties/"
        pak = p / "main.pak"
        if not pak.is_file() or not (p / "properties").is_dir():
            return False, "that folder needs main.pak and a properties/ folder"
        if _digest(pak, "sha256") != PVZ_PAK_SHA256:
            return False, "main.pak is not the PC GOTY version this port needs (checksum differs)"
        return True, "PvZ GOTY files, checksum OK"
    if item.kind == "ipa":
        try:
            facts = read_bundle(p)
        except (OSError, zipfile.BadZipFile, plistlib.InvalidFileException) as exc:
            return False, f"could not read it as an .ipa ({exc})"
        if not facts["bundle"]:
            return False, "that is not an iOS app (.ipa or .app)"
        if item.bundle and facts["bundle"].lower() != item.bundle.lower():
            return False, f"this is {facts['bundle']}, not {item.key}"
        if item.version and facts["version"] and facts["version"] != item.version and not facts["version"].startswith(item.version + "."):
            return False, f"this is {item.key} {facts['version']}; the port needs {item.version}"
        return True, f"{item.key} {facts['version'] or '?'} .ipa (decryption is checked when it builds)"
    return True, ""


def scan_drop_dir(item: Item, folder: Path = DROP_DIR) -> Optional[Path]:
    """First file in mygames/ that passes check_file for this game."""
    if not folder.is_dir():
        return None
    cands: List[Path] = []
    for p in sorted(folder.iterdir()):
        if item.kind == "rom" and p.is_file() and p.suffix.lower() == ".z64":
            cands.append(p)
        elif item.kind == "ipa" and (p.suffix.lower() in (".ipa", ".app")):
            cands.append(p)
        elif item.kind == "folder" and p.is_dir() and (p / "main.pak").exists():
            cands.append(p)
    for p in cands:
        ok, _ = check_file(item, p)
        if ok:
            return p
    return None


# ---------------------------------------------------------------- plan


@dataclass
class Step:
    title: str
    argv: List[str]                   # ./start arguments
    kind: str = "disk"                # tools | jailbreak | disk | remove | na (NanoApps base, one Disk Mode session)
    then: List[List[str]] = field(default_factory=list)   # more ./start runs in the same Disk Mode session
    check: str = ""                   # app to look for on Home afterwards
    root: bool = True
    gone: str = ""                    # app that should be gone from Home afterwards


def build_na_plan(state: NanoState, selected: Sequence[str], files: Dict[str, Path], *,
                  disk_files: bool = False) -> List[Step]:
    """NanoApps 1.0 base: plain file copies through the mounted volume, no firmware, no root.

    Backup first, then (on a fresh base) NanoApps' own disk files, the Launcher,
    removals, then adds. All in one Disk Mode session with one restart at the end."""
    selected = list(selected)
    items = [by_name(k) for k in additions(selected)]
    items = [i for i in items if i is not None and not is_on(i, state)]
    dropping = [by_name(k) for k in removals(selected)]
    dropping = [i for i in dropping if i is not None and is_on(i, state) and i.name != "resident-na"]
    steps: List[Step] = []
    if any((i.group != "tools") and (i.kind != "none" or not prebuilt(i.name)) for i in items):
        steps.append(Step("Check build tools", ["deps"], kind="tools", root=False))
    steps.append(Step("Back up /Library and /Apps from the iPod to this Mac", ["nabase", "backup"],
                      kind="na", root=False))
    if disk_files:
        steps.append(Step("Copy the NanoApps 1.0 disk files (Apps, Data, Library)", ["nabase", "disk-files"],
                          kind="na", root=False))
    if not state.na or any(i.name == "resident-na" for i in items):
        steps.append(Step("Install the Launcher as /Library/resident.hbapp (NanoApps' copy is in the backup)",
                          ["nabase", "launcher"], kind="na", root=False))
    seen = set()
    for item in dropping:
        # "Paint folder" (Paint.nanoapp) and "Paint" are the same folder on this base: remove it once.
        folder = item.name[: -len(".nanoapp")] if item.name.endswith(".nanoapp") else item.name
        if folder in seen:
            continue
        seen.add(folder)
        steps.append(Step(f"Remove {item.key}", ["nabase", "remove", folder], kind="na", root=False,
                          gone=item.key))
    order = {"tools": 0, "apps": 1, "more": 2, "games": 3, "pocketjs": 4}
    adds = [i for i in items if i.name != "resident-na"]
    for item in sorted(adds, key=lambda i: (order.get(i.group, 9), ITEMS.index(i))):
        argv = ["nabase", "add", item.name]
        if item.kind == "rom":
            argv += ["--rom", str(files[item.key])]
        elif item.kind in ("ipa", "folder"):
            argv += ["--payload", str(files[item.key])]
        title = f"Copy {item.name} into /Apps" if item.name.endswith(".nanoapp") else f"Add {item.key}"
        step = Step(title, argv, kind="na", root=False, check=item.key)
        if item.name == "JS QuickJS":
            step.title = "Add JS QuickJS and the PocketJS demo files"
            step.then.append(["nabase", "packages"])
        steps.append(step)
    if len(steps) == 1 + int(steps[0].kind == "tools") and not disk_files:
        return []          # only the backup: nothing to change
    return steps


def build_plan(state: NanoState, selected: Sequence[str], files: Dict[str, Path], *, serial: str = "",
               disk_files: bool = False) -> List[Step]:
    if state.nanoapps:
        return build_na_plan(state, selected, files, disk_files=disk_files)
    selected = list(selected)
    items = [by_name(k) for k in additions(selected)]
    items = [i for i in items if i is not None and not is_on(i, state)]
    dropping = [by_name(k) for k in removals(selected)]
    dropping = [i for i in dropping if i is not None and is_on(i, state) and i.name != "resident-na"]
    steps: List[Step] = []
    needs_tools = (not state.loader) or any(
        (i.group != "tools") and (i.kind != "none" or not prebuilt(i.name)) for i in items
    )
    if needs_tools:
        steps.append(Step("Check build tools", ["deps"], kind="tools", root=False))
    if not state.loader:
        argv = ["--typed-confirm", "UNTETHER", "install", "--run"]
        if serial:
            argv += ["--serial", serial]
        steps.append(Step("Install the homebrew loader and firmware (Blocks, Game Boy, Probe)", argv,
                          kind="jailbreak", check="Blocks"))
    total = home_count(state, selected)
    launcher = state.na or any(i.name == "resident-na" for i in items)
    # The Launcher holds 16 Home apps by itself. apps cap lean overlays the old
    # residents and must never run once the Launcher is (or is about to be) on.
    if total > PACK_LIMIT and not state.lean and not launcher:
        steps.append(Step("Make room for more than 8 Home apps", ["apps", "cap", "lean", "--skip-wait"]))
    # Removals first (they free Home slots), one Disk Mode step each.
    for item in dropping:
        steps.append(Step(f"Remove {item.key}", ["apps", "remove", item.name, "--skip-wait"],
                          kind="remove", gone=item.key))
    order = {"tools": 0, "apps": 1, "more": 2, "games": 3, "pocketjs": 4}
    for item in sorted(items, key=lambda i: (order.get(i.group, 9), ITEMS.index(i))):
        if item.name == "resident-na":
            steps.append(Step(
                "Install the Launcher (keeps a copy of the old one)",
                ["resident", "install-na", "--skip-wait"],
            ))
            continue
        if item.name.endswith(".nanoapp"):
            steps.append(Step(
                f"Copy {item.name} into /Apps",
                ["nanoapp", "copy", item.name, "--skip-wait"],
            ))
            continue
        argv = ["apps", "add", item.name, "--skip-wait"]
        if item.kind == "rom":
            argv += ["--rom", str(files[item.key])]
        elif item.kind in ("ipa", "folder"):
            argv += ["--payload", str(files[item.key])]
        step = Step(f"Add {item.key}", argv, check=item.key)
        if item.name == "JS QuickJS":
            step.title = "Add JS QuickJS and the PocketJS demo files"
            step.then.append(["pocketjs", "packages", "--skip-wait"])
        steps.append(step)
    return steps


def render_plan(steps: Sequence[Step]) -> str:
    if any(s.kind == "na" for s in steps):
        head = "Plan (plain file copies in one Disk Mode session, one restart at the end):"
    else:
        head = "Plan (one step at a time, with a restart after each):"
    lines = ["", head]
    for i, s in enumerate(steps, 1):
        tail = "  (writes firmware, after a full backup)" if s.kind == "jailbreak" else ""
        if s.kind == "remove" or (s.kind == "na" and s.gone):
            tail = "  (deletes it from the iPod)"
        lines.append(f"  {i}. {s.title}{tail}")
    return "\n".join(lines)


def start_prompt(steps: Sequence[Step]) -> Tuple[str, bool]:
    """The Start? question. With removals it names them and defaults to No."""
    gone = [s.gone for s in steps if s.gone]
    if not gone:
        return "Start?", True
    return f"This removes from your iPod: {', '.join(gone)}. Start?", False


# ---------------------------------------------------------------- running ./start quietly

QUIET_MARKERS = (
    ("Waiting for USB DFU", None),             # handled: show the DFU steps
    ("DFU device seen", "Recovery mode found."),
    ("UNTETHER_START", "Writing firmware. Do not unplug or press any buttons."),
    ("reusing hashed FAT32 backup", "Using your earlier backup."),
    ("Taking a new FAT32 backup", "Backing up the whole nano to this Mac (about 16 GB, this takes a while)."),
    ("==> backup", "Backing up the whole nano to this Mac (about 16 GB, this takes a while)."),
    ("==> build-firmware", "Building the firmware from Apple's download."),
    ("DEVICE_FILES_OK", "Files copied and checked."),
    ("APPS_OK", "App copied and checked."),
)
FAIL_RE = re.compile(r"(REFUSED|Refused|ERROR|error:|Traceback|failed)", re.I)


def classify(line: str) -> Optional[str]:
    """Friendly one-liner for an engine line, '__dfu__' for the DFU prompt, or None to keep it in the log."""
    if "Waiting for USB DFU" in line:
        return "__dfu__"
    if line.startswith(("Building ", "Fetching LVGL", "Installing pyelftools")) and "..." in line:
        return line.strip()
    for needle, msg in QUIET_MARKERS:
        if msg and needle in line:
            return msg
    return None


def failure_lines(lines: Sequence[str], keep: int = 3) -> List[str]:
    hits = [ln.strip() for ln in lines if FAIL_RE.search(ln)]
    if not hits:
        hits = [ln.strip() for ln in lines if ln.strip()][-keep:]
    return hits[-keep:]


def log_dir() -> Path:
    d = ROOT / "build" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_env_sh(path: Path) -> Dict[str, str]:
    """Environment after sourcing a bash env.sh (the Intel toolchain prefix)."""
    r = subprocess.run(["bash", "-c", f"source {shlex.quote(str(path))} >/dev/null 2>&1 && env -0"],
                       capture_output=True)
    env: Dict[str, str] = {}
    for chunk in r.stdout.split(b"\0"):
        if b"=" in chunk:
            k, _, v = chunk.partition(b"=")
            env[k.decode(errors="replace")] = v.decode(errors="replace")
    return env


# Written by scripts/macos-intel-bootstrap.sh; an older build/macos-intel is set up again.
BOOTSTRAP_STAMP = "NANO7KIT_BOOTSTRAP=2"


def intel_without_brew(mac: MacInfo) -> bool:
    from shutil import which

    return mac.system == "Darwin" and mac.arch == "x86_64" and not mac.rosetta and which("brew") is None


class Runner:
    def __init__(self, *, verbose: bool, flags: Sequence[str], out=print):
        self.verbose = verbose
        self.flags = list(flags)
        self.out = out
        self.log = log_dir() / time.strftime("install-%Y%m%d-%H%M%S.log")

    def argv(self, args: Sequence[str], root: bool) -> List[str]:
        base = [sys.executable, str(START), *self.flags]
        if self.verbose:
            base.append("--verbose")
        cmd = base + list(args)
        if root and os.geteuid() != 0:
            cmd = ["sudo", "-E"] + cmd
        return cmd

    def run(self, args: Sequence[str], *, root: bool = True, raw: bool = False) -> bool:
        cmd = list(args) if raw else self.argv(args, root)
        seen: List[str] = []
        shown: Set[str] = set()
        with self.log.open("a") as log:
            log.write("$ " + " ".join(shlex.quote(c) for c in cmd) + "\n")
            proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, bufsize=1)
            assert proc.stdout is not None
            for line in proc.stdout:
                log.write(line)
                seen.append(line)
                if self.verbose:
                    self.out(line.rstrip("\n"))
                    continue
                msg = classify(line)
                if msg == "__dfu__" and msg not in shown:
                    shown.add(msg)
                    self.out("")
                    self.out(buttons.text("dfu"))
                    self.out("  If your Mac offers to download recovery software, click Cancel.")
                    self.out("  Waiting for the nano...")
                elif msg and msg not in shown:
                    shown.add(msg)
                    self.out("  " + msg)
            rc = proc.wait()
            log.write(f"[exit {rc}]\n")
        if rc != 0 and not self.verbose:
            for ln in failure_lines(seen):
                self.out("  " + ln)
            self.out(f"  Full log: {self.log}")
        return rc == 0


# ---------------------------------------------------------------- interactive flow


def ask(prompt: str, inp: Callable[[str], str] = input) -> str:
    try:
        return inp(prompt)
    except EOFError:
        return "q"


def yes(prompt: str, default: bool = False, inp: Callable[[str], str] = input) -> bool:
    ans = ask(f"{prompt} [{'Y/n' if default else 'y/N'}] ", inp).strip().lower()
    if not ans:
        return default
    return ans in ("y", "yes")


def get_file(item: Item, inp=input, out=print) -> Optional[Path]:
    found = scan_drop_dir(item)
    if found:
        ok, msg = check_file(item, found)
        out(f"  {item.key}: using mygames/{found.name} ({msg})")
        return found
    out(f"  {item.key} needs {item.needs}.")
    while True:
        raw = ask("  Drag the file here (or type its path), Enter to skip this game: ", inp)
        path = clean_path(raw)
        if not path or path.lower() == "q":
            out(f"  Skipping {item.key}.")
            return None
        ok, msg = check_file(item, Path(path))
        out(f"  {'OK' if ok else 'No'}: {msg}")
        if ok:
            return Path(path)


def wait_disk_mode(inp=input, out=print, *, detect=detect_nano) -> str:
    """Show the Disk Mode steps and wait. Returns '' to go on, 's' to stop, 'r' for a restart loop."""
    out("")
    out(buttons.text("disk"))
    while True:
        ans = ask('Press Enter when it says "Do not disconnect" (s = stop here, r = it keeps restarting): ', inp)
        ans = ans.strip().lower()
        if ans in ("s", "q", "r"):
            return "s" if ans == "q" else ans
        st = detect()
        if st.present and st.mount is not None:
            return ""
        out("  I can't see the nano yet. Check the cable and that the screen says Do not disconnect.")


def recover(runner: Runner, last: Optional[Step], inp=input, out=print, *, nanoapps: bool = False) -> None:
    out("")
    out("Let's undo the last step. Nothing here touches the firmware.")
    if wait_disk_mode(inp, out) != "":
        return
    if nanoapps:
        out("Putting NanoApps' own launcher back from your backup.")
        if runner.run(["nabase", "restore"], root=False):
            out("Done. Eject and restart the nano. Your apps, music and saves stay on it.")
        else:
            out("That did not work. See docs/NANOAPPS-BASE.md (Undo).")
        return
    runner.run(["apps", "lasttrace"], root=True)
    if last is not None and last.check and last.argv[:2] == ["apps", "add"]:
        name = last.argv[2]
        out(f"Removing {last.check} from the Home screen.")
        if runner.run(["apps", "remove", name, "--skip-wait"]):
            out("Done. Restart the nano. If it still restarts by itself, run ./install again and pick r.")
            return
    out("Going back to the tested set (Blocks, Game Boy, Probe).")
    if runner.run(["apps", "restore-tested", "--skip-wait"]):
        out("Done. Restart the nano. Your app files and saves stay on it.")
    else:
        out("That did not work. See docs/RECOVERY.md.")


def choose_base(state: NanoState, inp=input, out=print) -> Optional[bool]:
    """Ask which path to take on a nano with no homebrew (or both kinds of files).

    Returns None to stop, else whether NanoApps' own disk files still need copying."""
    from . import nabase

    out("")
    if state.base == "mixed":
        out("This iPod has files from both nano7-untethered and NanoApps 1.0.")
        out("Which firmware is on it now?")
        out("  1 = NanoApps 1.0 (I did its Finder Check for Update)")
        out("  2 = nano7-untethered")
        ans = ask("> ", inp).strip()
        if ans == "1":
            read_base(state, "nanoapps")
            return False
        if ans == "2":
            state.base = "untethered"
            return False
        out("Nothing changed.")
        return None
    out("No homebrew on this iPod yet. Two ways to add it:")
    out("  1 = NanoApps 1.0 is installed (Finder Check for Update): copy nano7kit on top.")
    out("      No firmware written, music stays, Mac or FAT32 format both fine.")
    out("  2 = Not yet: download NanoApps 1.0 and show me the Finder steps.")
    out("  3 = nano7-untethered firmware instead (FAT32 only, writes firmware).")
    ans = ask("> ", inp).strip()
    if ans == "1":
        read_base(state, "nanoapps")
        return True
    if ans == "2":
        name, digest = nabase.NA_IPSW["2015"]
        out(f"Downloading {name} (checked against its SHA-256)...")
        path = nabase.fetch_release_file(name, digest)
        if platform.system() == "Darwin":
            subprocess.run(["open", "-R", str(path)], capture_output=True)
        out("")
        out(f"  File: {path}")
        out("  1. Open Finder and click the iPod in the sidebar.")
        out("  2. Hold Option and click Check for Update (not Restore), then pick that file.")
        out("  3. Wait for the iPod to restart, then run ./install again and pick 1.")
        out("  A 2012 nano needs NanoApps-2012-Model.ipsw: ./start nabase ipsw --model 2012 (untested here).")
        return None
    if ans == "3":
        return False
    out("Nothing changed.")
    return None


def run_na_steps(runner: Runner, state: NanoState, steps: Sequence[Step], inp=input, out=print) -> int:
    """Run a NanoApps-base plan: tools first, then every copy in one Disk Mode session."""
    disk_ready = False
    last: Optional[Step] = None
    for n, step in enumerate(steps, 1):
        out("")
        out(f"Step {n} of {len(steps)}: {step.title}")
        if step.kind == "na" and not disk_ready:
            ans = wait_disk_mode(inp, out)
            if ans == "s":
                out("Stopped. Run ./install again to carry on.")
                return 0
            if ans == "r":
                recover(runner, last, inp, out, nanoapps=True)
                return 1
            disk_ready = True
        ok = runner.run(step.argv, root=False)
        for extra in step.then:
            ok = ok and runner.run(extra, root=False)
        if not ok:
            out(f"FAILED: {step.title}. The steps before it stay done; your backup is in build/nanoapps-base-backups.")
            out("To put NanoApps' own launcher back: ./start nabase restore")
            return 1
        out(f"OK: {step.title}")
        last = step
    if disk_ready:
        if platform.system() == "Darwin" and state.mount is not None:
            subprocess.run(["diskutil", "eject", str(state.mount)], capture_output=True)
        out("")
        out(buttons.text("restart"))
        for s in steps:
            if s.check:
                out(f"  Check that {s.check} is on the Home screen.")
            if s.gone:
                out(f"  Check that {s.gone} is gone from the Home screen.")
        out("")
        from .nabase import WARN_REPLACE

        out(WARN_REPLACE)
    out("")
    out("All done. Undo: ./start nabase restore   Problems: docs/NANOAPPS-BASE.md")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    verbose = "--verbose" in args or "-v" in args
    mac = detect_mac()
    if mac.system not in ("Darwin", "Linux"):
        print("nano7kit runs on a Mac (or Linux with ./start). This system is not supported.")
        return 2
    if mac.rosetta:
        print("Terminal is running under Rosetta. Quit it, open Terminal normally, and run ./install again.")
        return 2
    print("Looking for your iPod...")
    try:
        from . import upstream

        upstream.ensure()
    except Exception as exc:  # noqa: BLE001
        print(f"Could not download the third-party files: {exc}")
        print("Check your internet connection and run ./install again.")
        return 2
    state = detect_nano()
    disk_files = False
    if "--nanoapps" in args and state.present and state.mount is not None:
        # After NanoApps' Finder update on a nano that still has nano7-untethered files:
        # the disk alone can't show the firmware changed, so the user says so.
        read_base(state, "nanoapps")
    elif state.present and state.mount is not None and state.base in ("mixed", "none"):
        choice = choose_base(state)
        if choice is None:
            return 0
        disk_files = choice
    if state.present and state.fstype and state.fstype not in FAT_TYPES and not state.nanoapps:
        print(render_menu(mac, state, []))
        print("\nThis nano is formatted for Mac. The nano7-untethered path needs FAT32 (docs/FAT32.md).")
        print("The NanoApps 1.0 base works on a Mac-formatted nano: see docs/NANOAPPS-BASE.md.")
        return 2

    selected: List[str] = default_selection(state)
    while True:
        print(render_menu(mac, state, selected))
        line = ask("> ")
        if line.strip().lower() in ("q", "quit"):
            print("Nothing changed.")
            return 0
        if not line.strip():
            break
        selected, msg = toggle(line, selected, state)
        if msg:
            print(msg)

    files: Dict[str, Path] = {}
    for key in list(selected):
        item = by_name(key)
        if item and item.kind != "none":
            path = get_file(item)
            if path is None:
                selected.remove(key)
            else:
                files[key] = path

    serial = ""
    flags: List[str] = []
    if state.nanoapps:
        pass
    elif not state.loader:
        print("")
        print("Your nano needs the homebrew loader first. This writes new firmware to it.")
        print("Tested on one Mid-2015 nano so far. A full backup is saved on this Mac first.")
        if not yes("Install it?"):
            print("Nothing changed.")
            return 0
        serial = ask("Your nano's serial number (Settings > General > About): ").strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{8,14}", serial):
            print("That does not look like a serial number. Nothing changed.")
            return 2
    if state.nanoapps:
        flags = []
    elif not state.loader or any(by_name(k).kind != "none" for k in selected if by_name(k)):
        flags = ["--i-accept-untested-flasher", "--i-accept-2015-refresh"]
    else:
        flags = ["--i-accept-2015-refresh"]

    steps = build_plan(state, selected, files, serial=serial, disk_files=disk_files)
    if not steps:
        print("Nothing to do: everything you picked is already on the nano.")
        return 0
    print(render_plan(steps))
    prompt, default = start_prompt(steps)
    if not yes(prompt, default=default):
        print("Nothing changed.")
        return 0
    if any(s.root for s in steps) and os.geteuid() != 0:
        print("Your Mac password is needed to write to the iPod. It is not stored.")
        if subprocess.run(["sudo", "-v"]).returncode != 0:
            print("No password, nothing changed.")
            return 2

    runner = Runner(verbose=verbose, flags=flags)
    if state.nanoapps:
        return run_na_steps(runner, state, steps)
    for n, step in enumerate(steps, 1):
        print("")
        print(f"Step {n} of {len(steps)}: {step.title}")
        if step.kind in ("disk", "remove"):
            ans = wait_disk_mode()
            if ans == "s":
                print("Stopped. Run ./install again to carry on.")
                return 0
            if ans == "r":
                recover(runner, steps[n - 2] if n > 1 else None)
                return 1
        if step.kind == "jailbreak":
            print("Plug the nano in and put it in Disk Mode first:")
            if wait_disk_mode() != "":
                print("Stopped. Run ./install again to carry on.")
                return 0
        if step.kind == "tools" and intel_without_brew(mac):
            env_sh = ROOT / "build" / "macos-intel" / "env.sh"
            if not env_sh.is_file() or BOOTSTRAP_STAMP not in env_sh.read_text(encoding="utf-8", errors="replace"):
                print("  Setting up the build tools in build/macos-intel (no password, a few minutes)...")
                if not runner.run([str(ROOT / "scripts" / "macos-intel-bootstrap.sh")], raw=True):
                    print(f"FAILED: {step.title}. See docs/MACOS.md.")
                    return 1
            os.environ.update(load_env_sh(env_sh))
        ok = runner.run(step.argv, root=step.root)
        for extra in step.then:
            ok = ok and runner.run(extra, root=step.root)
        if not ok:
            print(f"FAILED: {step.title}. Nothing else was changed.")
            if step.kind in ("disk", "remove"):
                print("If the nano now restarts by itself: run ./install again and pick r.")
            return 1
        print(f"OK: {step.title}")
        if step.kind in ("disk", "remove", "jailbreak"):
            if mac.system == "Darwin":
                subprocess.run(["diskutil", "eject", "/Volumes/IPOD"], capture_output=True)
            print("")
            print(buttons.text("restart"))
            if step.check:
                print(f"  Check that {step.check} is on the Home screen.")
            if step.gone:
                print(f"  Check that {step.gone} is gone from the Home screen.")
    print("")
    print("All done. Game Boy games: put .gb files in with ./start roms add FILE.gb")
    print("Music: ./start sync   Problems: docs/RECOVERY.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
