"""nano7kit on the NanoApps 1.0 base: plain file copies onto the mounted iPod.

NanoApps 1.0 installs as a Finder/iTunes update. Its fixed firmware stage loads
/NanoApps/loader.hbapp from the IPSW, and that loader loads /Library/resident.hbapp
from the normal disk if the file's checksum, ABI (2), firmware (1.1.2) and size
(span under 1 MB) check out (NanoApps system/disk_bootstrap/disk_bootstrap.c,
sdk/hb_reloc_file.c). It is not pinned to their launcher.

So on that base nano7kit only copies files through the mounted volume, on HFS+
or FAT32: our launcher (the same HRL1 image as resident-B-na.hbapp, rewrapped
with NanoApps' version trailer), the app pack, app files, game data built from
the user's own files, and the PocketJS packages. No firmware write, no DFU, no
FAT32 conversion, no raw disk tools, no root. Music (iPod_Control) is never
written: every write goes through Volume, which allows /Apps/ and the one
launcher file only.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import shutil
import struct
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from . import const
from .compat import Refused, root as repo_root, sha256_file
from .fat import MountFat, assert_safe_fat_path

# --- NanoApps 1.0 release we build on (github.com/nfzerox/NanoApps) ---------
NA_TAG = "build-00d9fb3c988ea424452edc4cd78c4a6438be7a64"
NA_URL = f"https://github.com/nfzerox/NanoApps/releases/download/{NA_TAG}"
NA_IPSW = {
    "2015": ("NanoApps-2015-Model.ipsw", "34583183923ca3671272bc0d2b57f93b2a230a007b470ce2ba3b34f5034d9056"),
    "2012": ("NanoApps-2012-Model.ipsw", "fc283c6efb77e52a8c8e7d76ed7aa4425752bb60d9763c928c1ff1bb074c60c0"),
}
NA_DISK_ZIP = ("NanoApps-disk.zip", "9bf360c406ba8657f6cb3370d26bc224493558c2869965fd7f5740fa11a2e3dc")
NA_RESIDENT_VERSION = 1793597007          # their build.json resident_version
NA_RESIDENT_SHA256 = "616b38615b09df91d6b14e55a02b61073f5b5da7615fee63f331f412f484babe"  # Library/resident.hbapp in the zip
OUR_RESIDENT_VERSION = NA_RESIDENT_VERSION + 1   # never backdate (their wrap_resident_slot.py)

VERSION_MAGIC = 0x52564248                # HBVR
RUNTIME_ABI = 2
RUNTIME_FIRMWARE = 0x00010102
MAX_RESIDENT_SPAN = 1024 * 1024           # hb_reloc_file.c checked loads

RESIDENT = "/Library/resident.hbapp"
DISABLED = "/Library/disabled"            # their loader skips the launcher while this exists
PACKS = ("/Apps/AllApps-B.pack", "/Apps/AllApps.pack", "/Library/AllApps.pack")  # our launcher's order
NEW_PACK = "/Apps/AllApps.pack"
MARKER = "/Apps/Startup/nano7kit-base.txt"
BACKUP_ROOTS = ("Library", "Apps", "NanoApps")
UNTETHERED_FILES = ("/Apps/Startup/ab.hbapp", "/Apps/Startup/resident-A.hbapp")
HOME_LIMIT = 60                           # launcher pages: 6 per page x 10 (HB_MAX_APPS 64)

WARN_REPLACE = (
    "Copying NanoApps-disk.zip onto the iPod again, or a later NanoApps release, "
    "puts their own launcher back in /Library/resident.hbapp. Your nano7kit apps stay "
    "on the disk; run ./install again to put the nano7kit Launcher back."
)


# --- launcher slot format (mirror of NanoApps tools/wrap_resident_slot.py) ---

def fnv1a32(data: bytes) -> int:
    h = 0x811C9DC5
    for b in data:
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def _hrl_len(data: bytes) -> int:
    if len(data) < 24 or struct.unpack_from("<I", data)[0] != 0x314C5248:
        raise Refused("not an HRL1 launcher image")
    _, entry, image, span, relocs, align = struct.unpack_from("<6I", data)
    if not (entry < image <= span and align and not align & (align - 1) and align <= 32):
        raise Refused("launcher HRL1 header is invalid")
    return 24 + image + 4 * relocs


def read_slot(data: bytes) -> tuple:
    """(HRL1 bytes, version) of a /Library/resident.hbapp, checked like their loader."""
    if len(data) < 28 or fnv1a32(data[:-4]) != int.from_bytes(data[-4:], "little"):
        raise Refused("launcher file checksum mismatch")
    n = _hrl_len(data)
    span = struct.unpack_from("<I", data, 12)[0]
    if span > MAX_RESIDENT_SPAN:
        raise Refused("launcher is over the 1 MB limit of the NanoApps loader")
    if n + 20 == len(data):
        magic, abi, fw, version = struct.unpack_from("<4I", data, n)
        if (magic, abi, fw) != (VERSION_MAGIC, RUNTIME_ABI, RUNTIME_FIRMWARE) or not version:
            raise Refused("launcher version info does not match ABI 2 / firmware 1.1.2")
    elif n + 4 == len(data):
        version = 0
    else:
        raise Refused("launcher file has the wrong size")
    return data[:n], version


def wrap_slot(hrl: bytes, version: int) -> bytes:
    if _hrl_len(hrl) != len(hrl):
        raise Refused("launcher HRL1 has trailing bytes")
    if not 0 < version <= 0xFFFFFFFF:
        raise Refused("invalid launcher version")
    body = hrl + struct.pack("<4I", VERSION_MAGIC, RUNTIME_ABI, RUNTIME_FIRMWARE, version)
    return body + struct.pack("<I", fnv1a32(body))


def our_hrl(path: Optional[Path] = None) -> bytes:
    """The exact launcher code the untethered path installs (pinned sha256)."""
    if path is None:
        from .resident_na import na_resident_path

        path = na_resident_path()
    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() not in const.NA_B_ALL_SHA256S:
        raise Refused(f"{path} is not a pinned nano7kit Launcher build")
    hrl, _ = read_slot(data)
    return hrl


def our_slot(path: Optional[Path] = None) -> bytes:
    return wrap_slot(our_hrl(path), OUR_RESIDENT_VERSION)


# --- the mounted volume -----------------------------------------------------

class Volume(MountFat):
    """MountFat on any mounted filesystem, with a write allow-list."""

    def _check(self, path: str) -> None:
        p = "/" + path.replace("\\", "/").lstrip("/")
        assert_safe_fat_path(p)
        if not (p.startswith("/Apps/") or p in (RESIDENT, DISABLED)):
            raise Refused(f"refusing to write {p}: nano7kit writes only /Apps and {RESIDENT}")

    def mkdir_p(self, fat_path: str) -> None:
        if fat_path.rstrip("/") not in ("/Apps", "/Library"):
            self._check(fat_path.rstrip("/") + "/")
        self._host(fat_path).mkdir(parents=True, exist_ok=True)

    def copy_to(self, src: Path, fat_path: str) -> None:
        self._check(fat_path)
        super().copy_to(src, fat_path)

    def copy_verified(self, src: Path, fat_path: str, *, replace: bool = False) -> str:
        self._check(fat_path)
        return super().copy_verified(src, fat_path, replace=replace)

    def delete(self, fat_path: str) -> None:
        self._check(fat_path)
        p = self._host(fat_path)
        if p.is_file():
            p.unlink()

    def delete_tree(self, fat_path: str) -> None:
        self._check(fat_path.rstrip("/") + "/")
        p = self._host(fat_path)
        if p.is_dir():
            _rmtree(p)
        if p.exists():
            raise Refused(f"{fat_path} is still on the iPod after delete")

    def write_bytes(self, fat_path: str, data: bytes) -> str:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "f"
            src.write_bytes(data)
            return self.copy_verified(src, fat_path, replace=True)


def _rmtree(p: Path) -> None:
    """rmtree that does not stop on an entry that is already gone.

    macOS deletes the ._name AppleDouble file with name on FAT and HFS volumes,
    so the ._ entry rmtree listed first is gone when it gets to it."""
    import sys

    def gone(func, path, exc):
        err = exc if isinstance(exc, BaseException) else exc[1]
        if not isinstance(err, FileNotFoundError):
            raise err

    if sys.version_info >= (3, 12):
        shutil.rmtree(p, onexc=gone)
    else:
        shutil.rmtree(p, onerror=gone)


# --- detection --------------------------------------------------------------

def folder_apps(root: Path) -> List[str]:
    apps = Path(root) / "Apps"
    if not apps.is_dir():
        return []
    return sorted(p.name[: -len(".nanoapp")] for p in apps.iterdir()
                  if p.is_dir() and p.name.endswith(".nanoapp") and (p / "manifest.napp").is_file())


def active_pack(root: Path) -> Optional[str]:
    """The pack our launcher reads: the first non-empty one (hb_legacy_pack_path)."""
    for fat in PACKS:
        p = Path(root) / fat.lstrip("/")
        if p.is_file() and p.stat().st_size:
            return fat
    return None


def pack_names(root: Path) -> List[str]:
    from .apps import pack_app_names

    fat = active_pack(root)
    if not fat:
        return []
    try:
        return pack_app_names((Path(root) / fat.lstrip("/")).read_bytes())
    except Refused:
        return []


def launcher_state(root: Path) -> dict:
    """What is in /Library/resident.hbapp: none, ours, ours-old, theirs or invalid.

    ours-old is an earlier nano7kit Launcher (const.NA_B_OLD_HRL_SHA256S): the
    installer offers the current one in its place."""
    p = Path(root) / RESIDENT.lstrip("/")
    if not p.is_file():
        return {"kind": "none"}
    data = p.read_bytes()
    try:
        hrl, version = read_slot(data)
    except Refused as exc:
        return {"kind": "invalid", "why": str(exc)}
    try:
        ours = hrl == our_hrl()
    except Refused:
        ours = False
    old = not ours and hashlib.sha256(hrl).hexdigest() in const.NA_B_OLD_HRL_SHA256S
    return {"kind": "ours" if ours else "ours-old" if old else "theirs", "version": version,
            "sha256": hashlib.sha256(data).hexdigest()}


def detect_base(root: Optional[Path]) -> str:
    """nanoapps | untethered | mixed | none (stock, or NanoApps firmware without its files).

    The disk alone cannot show which firmware is installed, so a nano that has
    both nano7-untethered startup files and NanoApps files is 'mixed' unless
    our marker says which one we set up last."""
    if root is None:
        return "none"
    r = Path(root)
    marker = r / MARKER.lstrip("/")
    if marker.is_file():
        text = marker.read_text(errors="replace").strip().split()
        if text and text[0] in ("nanoapps", "untethered"):
            return text[0]
    untethered = any((r / f.lstrip("/")).is_file() for f in UNTETHERED_FILES)
    nanoapps = (r / RESIDENT.lstrip("/")).is_file() or bool(folder_apps(r))
    if untethered and nanoapps:
        return "mixed"
    if untethered:
        return "untethered"
    return "nanoapps" if nanoapps else "none"


def old_layout_refusal(ops, command: str = "apps add") -> Optional[str]:
    """Why an untethered-layout command (./start apps ...) must not run on this iPod, or None.

    ops is a MountFat (mounted volume) or MtoolsFat. On the NanoApps 1.0 base the
    firmware loads /Library/resident.hbapp, so /Apps/Startup slot files, the
    resident-B.active marker and the nogrow/lean overlays do nothing there, and
    Home is written by ./start nabase add / remove (or ./install)."""
    root = getattr(ops, "root", None)
    text = (ops.read_bytes(MARKER) or b"").decode(errors="replace").split()
    marker = text[0] if text else ""
    has = bool(ops.exists(RESIDENT))
    # .nanoapp folders alone do not count: ./start nanoapp copy puts them on untethered iPods too.
    if marker == "untethered" or not (marker == "nanoapps" or has):
        return None
    kind = launcher_state(Path(root))["kind"] if root is not None and has else ("a launcher" if has else "none")
    who = {"ours": "the nano7kit Launcher", "ours-old": "an older nano7kit Launcher",
           "theirs": "NanoApps' launcher",
           "none": "no launcher yet"}.get(kind, kind)
    return (
        f"this iPod is on the NanoApps 1.0 base ({RESIDENT}: {who}). ./start {command} writes the "
        "old untethered layout (/Apps/Startup slots, resident-B.active), which this firmware never reads. "
        "Use ./install, or ./start nabase add NAME [--payload FILE | --rom FILE] and "
        "./start nabase remove NAME. Nothing written."
        + ("" if marker == "nanoapps" else
           " If this iPod really runs the untethered firmware: ./start nabase mark untethered.")
    )


def mark_base(vol: Volume, base: str) -> None:
    vol.write_bytes(MARKER, f"{base}\nset by nano7kit {_dt.date.today().isoformat()}\n".encode())


def home_names(root: Path) -> List[str]:
    """Everything on Home under our launcher: pack apps, then .nanoapp folders."""
    names = pack_names(root)
    return names + [n for n in folder_apps(root) if n not in names]


# --- backup and restore -----------------------------------------------------

def backup_base_dir() -> Path:
    d = repo_root() / "build" / "nanoapps-base-backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _walk(root: Path) -> Iterable[Path]:
    for top in BACKUP_ROOTS:
        base = root / top
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".Trashes"))
            for name in sorted(filenames):
                if name.startswith("._") or name == ".DS_Store":
                    continue
                yield Path(dirpath) / name


def backup(root: Path, dest: Optional[Path] = None, *, out=print) -> Path:
    """Copy /Library, /Apps and /NanoApps to this Mac, hash-checked. Music is not copied."""
    root = Path(root)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = Path(dest) if dest else backup_base_dir() / stamp
    files_dir = dest / "files"
    files_dir.mkdir(parents=True, exist_ok=False)
    manifest: Dict[str, dict] = {}
    total = 0
    for src in _walk(root):
        rel = src.relative_to(root).as_posix()
        target = files_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
        digest = sha256_file(src)
        if sha256_file(target) != digest:
            raise Refused(f"backup copy of /{rel} does not match the iPod")
        size = target.stat().st_size
        manifest["/" + rel] = {"sha256": digest, "size": size}
        total += size
    meta = {"made": stamp, "volume": str(root), "base": detect_base(root),
            "launcher": launcher_state(root), "files": manifest}
    (dest / "backup.json").write_text(json.dumps(meta, indent=2) + "\n")
    out(f"    backup    {len(manifest)} files, {total / 1e6:.1f} MB -> {dest}")
    return dest


def backups() -> List[Path]:
    base = backup_base_dir()
    return sorted(p for p in base.iterdir() if (p / "backup.json").is_file())


def load_backup(path: Path) -> dict:
    meta = json.loads((Path(path) / "backup.json").read_text())
    for fat, rec in meta["files"].items():
        f = Path(path) / "files" / fat.lstrip("/")
        if not f.is_file() or f.stat().st_size != rec["size"]:
            raise Refused(f"backup {path} is missing {fat}")
    return meta


def _their_launcher_from_backups() -> Optional[bytes]:
    for b in reversed(backups()):
        try:
            meta = load_backup(b)
        except (Refused, ValueError, KeyError):
            continue
        if meta.get("launcher", {}).get("kind") == "theirs":
            data = (b / "files" / RESIDENT.lstrip("/")).read_bytes()
            if hashlib.sha256(data).hexdigest() == meta["files"][RESIDENT]["sha256"]:
                return data
    return None


def fetch_release_file(name: str, sha256: str, dest_dir: Optional[Path] = None, *, out=print) -> Path:
    """Download one pinned NanoApps 1.0 release file and check its sha256."""
    dest_dir = Path(dest_dir) if dest_dir else repo_root() / "build" / "nanoapps-release"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    if dest.is_file() and sha256_file(dest) == sha256:
        return dest
    out(f"    download  {NA_URL}/{name}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(f"{NA_URL}/{name}") as r, tmp.open("wb") as fh:
        shutil.copyfileobj(r, fh, 1 << 20)
    if sha256_file(tmp) != sha256:
        tmp.unlink()
        raise Refused(f"{name} does not match the pinned NanoApps 1.0 sha256")
    tmp.replace(dest)
    return dest


def their_launcher_from_zip(zip_path: Optional[Path] = None) -> bytes:
    zip_path = zip_path or fetch_release_file(*NA_DISK_ZIP)
    if sha256_file(zip_path) != NA_DISK_ZIP[1]:
        raise Refused("NanoApps-disk.zip does not match the pinned sha256")
    with zipfile.ZipFile(zip_path) as z:
        data = z.read("Library/resident.hbapp")
    read_slot(data)
    return data


def restore_launcher(root: Path, *, zip_path: Optional[Path] = None, out=print) -> str:
    """Put NanoApps' own launcher back in /Library/resident.hbapp."""
    data = _their_launcher_from_backups()
    source = "your backup"
    if data is None:
        data = their_launcher_from_zip(zip_path)
        source = "NanoApps-disk.zip (pinned 1.0)"
    vol = Volume(Path(root))
    status = vol.write_bytes(RESIDENT, data)
    mark_base(vol, "nanoapps")
    out(f"    restored  {RESIDENT} from {source} ({status})")
    out("    Eject, restart the iPod: Home shows NanoApps' own launcher. nano7kit's pack apps stay on")
    out("    the disk but that launcher does not list them (it reads /Library/AllApps.pack).")
    return status


# --- installing -------------------------------------------------------------

def require_backup(root: Path, backup_dir: Optional[Path]) -> dict:
    if backup_dir is None:
        found = backups()
        if not found:
            raise Refused("no file backup of this iPod yet: run ./start nabase backup first")
        backup_dir = found[-1]
    meta = load_backup(backup_dir)
    if Path(meta["volume"]).name != Path(root).name:
        raise Refused(f"latest backup {backup_dir} is of another volume ({meta['volume']})")
    return meta


def install_disk_files(root: Path, *, zip_path: Optional[Path] = None, out=print) -> int:
    """NanoApps' own step 3 (copy Apps, Data, Library), for a nano without them.

    Never replaces a file that is already there."""
    zip_path = zip_path or fetch_release_file(*NA_DISK_ZIP)
    if sha256_file(zip_path) != NA_DISK_ZIP[1]:
        raise Refused("NanoApps-disk.zip does not match the pinned sha256")
    r = Path(root)
    n = 0
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            name = info.filename
            if info.is_dir() or name.startswith("__MACOSX/"):
                continue
            top = name.split("/", 1)[0]
            if top not in ("Apps", "Data", "Library") or ".." in name.split("/"):
                raise Refused(f"unexpected file in NanoApps-disk.zip: {name}")
            assert_safe_fat_path("/" + name)
            target = r / name
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as s, target.open("wb") as d:
                shutil.copyfileobj(s, d)
            n += 1
    out(f"    copied    {n} NanoApps 1.0 files (Apps, Data, Library)")
    return n


def install_launcher(root: Path, *, backup_dir: Optional[Path] = None,
                     launcher: Optional[Path] = None, out=print) -> str:
    """Copy the nano7kit Launcher to /Library/resident.hbapp (after a backup)."""
    root = Path(root)
    meta = require_backup(root, backup_dir)
    state = launcher_state(root)
    if state["kind"] == "theirs" and state["version"] > OUR_RESIDENT_VERSION:
        raise Refused(
            f"this iPod has a newer NanoApps launcher (version {state['version']}) than the "
            f"NanoApps 1.0 nano7kit was tested with. Nothing written.")
    if (state["kind"] == "theirs" and state["sha256"] != NA_RESIDENT_SHA256
            and meta.get("launcher", {}).get("sha256") != state["sha256"]):
        raise Refused("the backup does not hold the launcher that is on the iPod now: "
                      "run ./start nabase backup again")
    slot = our_slot(launcher)
    vol = Volume(root)
    vol.mkdir_p("/Library")
    vol.mkdir_p("/Apps/Startup")              # lasttrace.bin lives here
    status = vol.write_bytes(RESIDENT, slot)
    hrl, version = read_slot((root / RESIDENT.lstrip("/")).read_bytes())
    if hrl != our_hrl(launcher) or version != OUR_RESIDENT_VERSION:
        raise Refused("launcher readback does not match")
    if (root / DISABLED.lstrip("/")).exists():
        out(f"    note      {DISABLED} exists, so no launcher loads until you delete it")
    mark_base(vol, "nanoapps")
    out(f"    launcher  {RESIDENT} ({status}, {len(slot)} bytes, version {OUR_RESIDENT_VERSION})")
    out("    " + WARN_REPLACE)
    return status


def _base_rows(root: Path) -> list:
    """Game Boy and Probe from nano7-untethered; Blocks only if NanoApps' folder is absent."""
    from .app_catalog import by_name

    cat = by_name()
    names = ["Game Boy", "Probe"]
    if "Blocks" not in folder_apps(root):
        names.insert(0, "Blocks")
    return [cat[n] for n in names]


def add_folder(root: Path, name: str, *, out=print) -> str:
    """Copy one staged nano7kit .nanoapp folder into /Apps. Never replaces a folder."""
    from .nanoapp_copy import staged_folder

    src = staged_folder(name)
    if (Path(root) / "Apps" / name).exists():
        raise Refused(f"/Apps/{name} is already on the iPod; nothing replaced")
    if len(folder_apps(root)) + len(pack_names(root)) + 1 > HOME_LIMIT:
        raise Refused(f"the launcher shows {HOME_LIMIT} apps at most")
    vol = Volume(Path(root))
    for f in sorted(src.rglob("*")):
        if f.is_file():
            vol.copy_verified(f, f"/Apps/{name}/{f.relative_to(src).as_posix()}")
    out(f"    folder    /Apps/{name}")
    return name


def add(root: Path, names: Sequence[str], *, out=print) -> List[str]:
    """Copy built apps onto the iPod and merge them into the pack our launcher reads.

    Names ending .nanoapp are staged folders and are copied as they are."""
    folders = [n for n in names if n.endswith(".nanoapp")]
    for n in folders:
        add_folder(root, n, out=out)
    names = [n for n in names if not n.endswith(".nanoapp")]
    if folders and not names:
        return pack_names(root)
    from .app_catalog import by_name
    from .app_icons import shared_screen_blob
    from .apps import (_copy_port_data, local_app_files, make_hbap_bundle, merge_bundles,
                       pack_app_names, parse_pack, bundle_name, build_pack)

    root = Path(root)
    vol = Volume(root)
    cat = by_name()
    rows = []
    for n in names:
        row = cat.get(n)
        if row is None:
            raise Refused(f"unknown app {n}")
        rows.append(row)
    fat = active_pack(root) or NEW_PACK
    current = (root / fat.lstrip("/")).read_bytes() if active_pack(root) else b""
    have = pack_app_names(current) if current else []
    todo = [r for r in _base_rows(root) if r["name"] not in have] + rows
    total = len(set(have) | {r["name"] for r in todo} | set(folder_apps(root)))
    if total > HOME_LIMIT:
        raise Refused(f"Home would hold {total} apps; the launcher shows {HOME_LIMIT} at most")
    screen = shared_screen_blob()
    expected: Dict[str, str] = {}
    wrote: list = []
    already: list = []
    bundles = []
    for row in todo:
        exe, icon = local_app_files(row)
        for src, dst in ((exe, row["fat_exe"]), (icon, row["fat_icon"])):
            status = vol.copy_verified(src, dst, replace=True)
            out(f"    {status:8}  {dst}")
        _copy_port_data(vol, row, expected, wrote, already)
        bundles.append(make_hbap_bundle(row, screen))
    pack = merge_bundles(current, bundles)
    vol.write_bytes(fat, pack)
    out(f"    pack      {fat}: {', '.join(pack_app_names(pack))}")
    return pack_app_names(pack)


def remove(root: Path, names: Sequence[str], *, out=print) -> List[str]:
    """Take apps off Home: drop them from the pack (and their files) or delete a .nanoapp folder.

    A folder is only deleted when the latest backup holds it."""
    from .app_catalog import by_name
    from .apps import pack_app_names, parse_pack, bundle_name, build_pack

    root = Path(root)
    vol = Volume(root)
    fat = active_pack(root)
    pack = (root / fat.lstrip("/")).read_bytes() if fat else b""
    in_pack = pack_app_names(pack) if pack else []
    folders = folder_apps(root)
    cat = by_name()
    done = []
    # "Paint.nanoapp" (the installer's folder entry) and "Paint" name the same folder.
    names = list(dict.fromkeys(n[: -len(".nanoapp")] if n.endswith(".nanoapp") else n for n in names))
    for n in names:
        if n not in folders and (root / "Apps" / f"{n}.nanoapp").is_dir():
            folders.append(n)      # left half-deleted (no manifest): finish the delete
        if n in in_pack:
            pack = build_pack([b for b in parse_pack(pack) if bundle_name(b) != n])
            row = cat.get(n) or {}
            for f in (row.get("fat_exe"), row.get("fat_icon")):
                if f:
                    vol.delete(f)
            done.append(n)
        if n in folders:
            meta = require_backup(root, None)
            key = f"/Apps/{n}.nanoapp/manifest.napp"
            partial = not (root / key.lstrip("/")).is_file()
            if key not in meta["files"] and not (
                    partial and any(key in load_backup(b)["files"] for b in backups())):
                raise Refused(f"{n}.nanoapp is not in the latest backup: run ./start nabase backup first")
            vol.delete_tree(f"/Apps/{n}.nanoapp")
            done.append(n)
        if n not in in_pack and n not in folders:
            out(f"    missing   {n} is not on this iPod")
    if fat and pack:
        vol.write_bytes(fat, pack)
    for n in dict.fromkeys(done):
        out(f"    removed   {n}")
    return list(dict.fromkeys(done))


def packages(root: Path, *, out=print) -> int:
    """The JS QuickJS packages, into /Apps/Data/PocketJS."""
    from .pocketjs import device_name, manifest, package_files

    data = manifest()
    fat_dir = data["packages_dir"]
    srcs = [p for k in data["packages"] for p in package_files(k)]
    missing = [str(p) for p in srcs if not p.is_file()]
    if missing:
        raise Refused("package missing: " + ", ".join(missing) + " (./start pocketjs build quickjs)")
    vol = Volume(Path(root))
    for src in srcs:
        dst = f"{fat_dir}/{device_name(src)}"
        vol.copy_verified(src, dst, replace=True)
    out(f"    pocketjs  {len(srcs)} package files in {fat_dir}")
    return len(srcs)


def status_lines(root: Optional[Path]) -> List[str]:
    if root is None:
        return ["no mounted iPod: plug it in so Finder shows /Volumes/IPOD (Disk Mode only if it is not mounted)"]
    st = launcher_state(root)
    lines = [f"volume    {root}", f"base      {detect_base(root)}",
             f"launcher  {st['kind']}" + (f" (version {st['version']})" if "version" in st else ""),
             f"pack      {active_pack(root) or 'none'}: {', '.join(pack_names(root))}",
             f"folders   {len(folder_apps(root))} .nanoapp apps",
             f"backups   {len(backups())} in {backup_base_dir()}"]
    return lines
