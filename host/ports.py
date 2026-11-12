"""Third-party NanoApps ports (olievans123): Mario first, then iOS ports.

Mario 64 / Mario Kart 64 are native GL ports in this tree (games/ +
nanoapps/apps/sm64|mk64). They need a user ROM to build; nothing from
Nintendo is in git.

Temple Run, Flappy Bird, Doodle Jump and Angry Birds live in separate GitHub repos.
Those trees contain none of the original game files. This toolkit clones
them on demand into build/ports/ and builds only when you pass --payload.

Fruit Ninja 1.0 is a first-party pipeline in this tree (no olievans123
repo). Original .ipa stays local; derived Data/FruitNinja is gitignored.

Helvetica silver_resident (slot A) reads GL apps into a 576 KB staging
slot (LV_SURFACE_MAX). Super Mario 64 is about 3 MB, so apps add for
large GL games uses the hardware-tested slot-B unload resident and
/Apps/AllApps-B.pack. Pack count cannot exceed 8 unless
./start apps cap enable overlays the nogrow residents (host limit 16;
the 11-app set stays refused). --payload accepts an unpacked
.app or a .ipa/.zip (extracted under gitignored build/payloads/).
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from . import const
from .compat import Refused, is_macos, md5_file, root, sha1_file, which

# Pinned clones of olievans123 ports (no original game data in those trees).
PORT_REPOS = {
    "templerun": {
        "url": "https://github.com/olievans123/nano-templerun.git",
        "commit": "b73a3d43f6d26147198060cdb28b6b55d59418a1",
        "name": "Temple Run",
        "folder": "templerun",
    },
    "flappybird": {
        "url": "https://github.com/olievans123/nano-flappybird.git",
        "commit": "7cc3184eb34747a6903dab20ba944cb8a7592231",
        "name": "Flappy Bird",
        "folder": "flappybird",
    },
    "doodlejump": {
        "url": "https://github.com/olievans123/nano-doodlejump.git",
        "commit": "57fae1c5943633d207a14688811f3d4588c8de55",
        "name": "Doodle Jump",
        "folder": "doodlejump",
    },
    "angrybirds": {
        "url": "https://github.com/olievans123/nano-angrybirds.git",
        "commit": "b547023d4be99a54052ce821540548d13596b3a0",
        "name": "Angry Birds",
        "folder": "angrybirds",
    },
}

# Host / FAT data trees staged next to extra .hbapp files.
PORT_DATA = {
    "Mario 64": {
        "host": "artifacts/extra/Data/SM64",
        "fat": "/Apps/Data/SM64",
        "required": True,
    },
    "Mario Kart": {
        "host": "artifacts/extra/Data/MK64",
        "fat": "/Apps/Data/MK64",
        "required": True,
    },
    "Temple Run": {
        "host": "artifacts/extra/Data/TempleRun",
        "fat": "/Apps/Data/TempleRun",
        "required": True,
    },
    "Flappy Bird": {
        "host": "artifacts/extra/Data/FlappyBird",
        "fat": "/Apps/Data/FlappyBird",
        "required": True,
    },
    "Doodle Jump": {
        "host": "artifacts/extra/Data/DoodleJump",
        "fat": "/Apps/Data/DoodleJump",
        "required": True,
    },
    "Fruit Ninja": {
        "host": "artifacts/extra/Data/FruitNinja",
        "fat": "/Apps/Data/FruitNinja",
        "required": True,
    },
    "PvZ Portable": {
        "host": "artifacts/extra/Data/PvZ",
        "fat": "/Apps/Data/PvZ",
        "required": True,
    },
    "Angry Birds": {
        "host": "artifacts/extra/Data/AngryBirds",
        "fat": "/Apps/Data/AngryBirds",
        "required": True,
    },
}

# PocketJS MicroTS apps (nano7g): RAW_SURFACE, but 1 to 2.2 MB with baked fonts, so slot B too.
POCKETJS_APPS = frozenset({"JS Hero", "JS Samples", "JS QuickJS"})
LARGE_GL = frozenset({"Mario 64", "Mario Kart", "Temple Run", "Fruit Ninja", "PvZ Portable",
                      "Angry Birds"}) | POCKETJS_APPS
NEEDS_PAYLOAD = frozenset({"Temple Run", "Flappy Bird", "Doodle Jump", "Fruit Ninja", "PvZ Portable",
                           "Angry Birds"})
NEEDS_ROM = frozenset({"Mario 64", "Mario Kart"})

SLOT_B_FAT = "/Apps/Startup/resident-B.hbapp"
SLOT_B_MARKER = "/Apps/Startup/resident-B.active"
PACK_A_FAT = "/Apps/AllApps.pack"
PACK_B_FAT = "/Apps/AllApps-B.pack"

SKIP_DATA_NAMES = {
    ".ds_store",
    "boot.txt",
    "prevboot.txt",
    "lastboot.txt",
    "thumbs.db",
}
SKIP_DATA_SUFFIX = {".wav", ".aif", ".caf"}
# ports whose sound effects are WAV files played by the OS player: their audio is copied
KEEP_AUDIO = frozenset({"PvZ Portable"})

# Slot A silver_resident reads the .hbapp into this many bytes before reloc.
SLOT_A_GL_STAGING = 0x90000  # 576 KB

MARIO_CMD = "./start apps add Mario --rom /path/to/sm64-usa.z64"
MK64_CMD = "./start apps add 'Mario Kart' --rom /path/to/mk64-usa.z64"
FLAPPY_CMD = "./start apps add 'Flappy Bird' --payload /path/to/Flap.ipa"
DOODLE_CMD = "./start apps add 'Doodle Jump' --payload /path/to/DoodleJump.ipa"
TEMPLE_CMD = "./start apps add TempleRun --ipa /path/to/TempleRun.ipa"
FRUIT_CMD = "./start apps add 'Fruit Ninja' --ipa /path/to/FruitNinja.ipa"
PVZP_CMD = "./start apps add 'PvZ Portable' --payload /path/to/pvz-goty"
ANGRY_CMD = "./start nabase add 'Angry Birds' --payload /path/to/AngryBirds.ipa"

# Original iOS bundle ids we will accept. Wrong game files are refused.
# PvZ (com.popcap.PvZ) is research-only and has no apps add path. PvZ Portable (the PvZ-Portable engine with
# the user's own PC GOTY files) is a different port with its own name.
TEMPLE_BUNDLE_ID = "com.imangi.templerun"
FRUIT_BUNDLE_ID = "com.Halfbrick.Fruit"
ANGRY_BUNDLE_ID = "com.clickgamer.AngryBirds"
PAYLOAD_BUNDLES = {
    "Temple Run": TEMPLE_BUNDLE_ID,
    "Fruit Ninja": FRUIT_BUNDLE_ID,
    "Angry Birds": ANGRY_BUNDLE_ID,
}

OVERLAY_PLISTS = (
    {
        "folder": "flappybird",
        "name": "Flappy Bird",
        "bundle_id": "org.nanoapps.flappybird",
        "kind": 2,
        "glyph": "gamepad",
        "color": "#4ec0ca",
        "executable_key": "flappybird",
    },
    {
        "folder": "doodlejump",
        "name": "Doodle Jump",
        "bundle_id": "org.nanoapps.doodlejump",
        "kind": 2,
        "glyph": "gamepad",
        "color": "#e9dba5",
        "executable_key": "doodlejump",
    },
    {
        "folder": "templerun",
        "name": "Temple Run",
        "bundle_id": "org.nanoapps.templerun",
        "kind": 2,
        "glyph": "gamepad",
        "color": "#3a5a2a",
        "executable_key": "templerun",
    },
    {
        # the app folder (nanoapps/apps/pvz_portable) is staged at build time and gitignored,
        # so a fresh checkout needs this row to know the name before the first build
        "folder": "pvz_portable",
        "name": "PvZ Portable",
        "bundle_id": "org.nano7kit.pvzportable",
        "kind": 2,
        "glyph": "seedling",
        "color": "#4f9a2f",
        "executable_key": "pvz",
    },
    {
        # same as nano-angrybirds' port/nano/app/Info.plist, which the build copies in
        "folder": "angrybirds",
        "name": "Angry Birds",
        "bundle_id": "org.nanoapps.angrybirds",
        "kind": 2,
        "glyph": "dove",
        "color": "#2980b9",
        "executable_key": "angrybirds",
    },
)


def ports_root() -> Path:
    return root() / "build" / "ports"


def extra_data_dir(name: str) -> Optional[Path]:
    meta = PORT_DATA.get(name)
    if not meta:
        return None
    return const.artifact(meta["host"])


def fat_data_dir(name: str) -> Optional[str]:
    meta = PORT_DATA.get(name)
    if not meta:
        return None
    return str(meta["fat"])


def data_ready(name: str) -> bool:
    path = extra_data_dir(name)
    if path is None:
        return True
    if not path.is_dir():
        return False
    return any(p.is_file() for p in path.rglob("*"))


def is_large_gl(name: str) -> bool:
    return name in LARGE_GL


def needs_payload(name: str) -> bool:
    return name in NEEDS_PAYLOAD


def needs_rom(name: str) -> bool:
    return name in NEEDS_ROM


def skip_data_file(path: Path, name: Optional[str] = None) -> bool:
    if path.name.lower() in SKIP_DATA_NAMES:
        return True
    if path.suffix.lower() in SKIP_DATA_SUFFIX and name not in KEEP_AUDIO:
        return True
    return False


def data_files(name: str) -> List[Path]:
    path = extra_data_dir(name)
    if path is None or not path.is_dir():
        return []
    return sorted(p for p in path.rglob("*") if p.is_file() and not skip_data_file(p, name))


def missing_help(name: str) -> str:
    if name == "Mario 64":
        return (
            "Mario 64 is the native Super Mario 64 port from olievans123/"
            "nano7-untethered (not a Game Boy ROM). USA .z64 sha1 "
            f"{const.SM64_SHA1}. Nothing from Nintendo is in git. "
            "Slot A silver_resident cannot load the ~3 MB GL image "
            f"(576 KB staging slot). {MARIO_CMD} builds from your ROM "
            "and copies it in Disk Mode onto slot B (AllApps-B.pack, "
            "hardware-tested unload resident, pack cap 8 unless "
            "./start apps cap enable). Probe heap "
            "on this MKN52: 7.0MB free / 4.0MB largest after a fresh boot."
        )
    if name == "Mario Kart":
        return (
            "Mario Kart is the native Mario Kart 64 port from olievans123/"
            "nano7-untethered. USA .z64 md5 "
            f"{const.MK64_MD5}. {MK64_CMD}"
        )
    if name == "Flappy Bird":
        return (
            "Flappy Bird is olievans123/nano-flappybird (original iOS 1.2 "
            "logic + atlas; no game files in git). Asset conversion uses "
            f"macOS sips. {FLAPPY_CMD}"
        )
    if name == "Doodle Jump":
        return (
            "Doodle Jump is olievans123/nano-doodlejump (original iOS 1.0; "
            "no game files in git). Asset conversion uses macOS sips. "
            f"{DOODLE_CMD}"
        )
    if name == "Temple Run":
        return (
            "Temple Run is olievans123/nano-templerun (the original 1.0 engine "
            "translated to C; no game files in git). Pass your own decrypted "
            f"Temple Run 1.0 .ipa or unpacked .app (bundle {TEMPLE_BUNDLE_ID}, "
            "armv7 sha256 91d85b35...afe01392). The build runs on this Mac; "
            "large GL, so it goes on slot B like Mario. "
            f"Check first: {TEMPLE_CMD} --plan. Then: {TEMPLE_CMD}"
        )
    if name == "Fruit Ninja":
        return (
            "Fruit Ninja is nano7kit's own static recompile of the original 1.0 "
            "engine (games/fruitninja, olievans123-style; no game files in git). "
            f"Pass your own decrypted Fruit Ninja 1.0 .ipa or unpacked .app as the payload (bundle "
            f"{FRUIT_BUNDLE_ID}, ipa sha256 427538e7...). Silent (no music). Large GL, "
            f"so it goes on slot B like Temple Run. Check first: {FRUIT_CMD} --plan. "
            f"Then: {FRUIT_CMD}"
        )
    if name == "Angry Birds":
        return (
            "Angry Birds is olievans123/nano-angrybirds (an engine re-implementation that runs "
            "the game's own Lua scripts; fetched at build time, no game files in git). Pass your "
            f"own Angry Birds 1.0 .ipa or unpacked .app (bundle {ANGRY_BUNDLE_ID}). The build runs "
            "on this Mac. Silent (no sound yet). At launch it needs about 5.0 MB free with a "
            f"2.0 MB largest block, or it shows NOT ENOUGH MEMORY. {ANGRY_CMD}"
        )
    if name == "PvZ Portable":
        return (
            "PvZ Portable is the PvZ-Portable engine (wszqkzqk, LGPL-3.0-or-later, fetched at build time) "
            "with nano7kit's device layer (games/pvz_portable). Pass the folder holding your own PvZ GOTY "
            "main.pak and properties/ as the payload; nothing from the game is in git. Sound effects, no "
            f"music. Large GL, so it goes on slot B. Check first: {PVZP_CMD} --plan. Then: {PVZP_CMD}"
        )
    return f"{name} is not built in this tree."


def assert_sm64_rom(path: Path) -> Path:
    src = Path(path).expanduser().resolve()
    if not src.is_file():
        raise Refused(f"Super Mario 64 ROM missing: {src}")
    digest = sha1_file(src)
    if digest != const.SM64_SHA1:
        raise Refused(
            f"{src} is not the supported USA Super Mario 64 .z64 "
            f"(sha1 {const.SM64_SHA1}, got {digest}). "
            "This toolkit does not ship Nintendo ROMs."
        )
    return src


def assert_mk64_rom(path: Path) -> Path:
    src = Path(path).expanduser().resolve()
    if not src.is_file():
        raise Refused(f"Mario Kart 64 ROM missing: {src}")
    digest = md5_file(src)
    if digest != const.MK64_MD5:
        raise Refused(
            f"{src} is not the supported USA Mario Kart 64 .z64 "
            f"(md5 {const.MK64_MD5}, got {digest}). "
            "This toolkit does not ship Nintendo ROMs."
        )
    return src


def stage_games_from_build(stage: Optional[Path] = None) -> dict:
    """Copy build/games-stage/Apps into artifacts/extra for apps add."""
    src = Path(stage) if stage is not None else root() / "build" / "games-stage" / "Apps"
    if src.name != "Apps" and (src / "Apps").is_dir():
        src = src / "Apps"
    if not src.is_dir():
        raise Refused(f"games stage missing: {src} (run ./start build-games)")
    extra = const.artifact("artifacts/extra")
    copied = []
    for sub, dest_name in (
        ("Executables", extra / "Executables"),
        ("Icons", extra / "Icons"),
        ("Data", extra / "Data"),
    ):
        from_dir = src / sub
        if not from_dir.is_dir():
            continue
        dest_name.mkdir(parents=True, exist_ok=True)
        for path in from_dir.rglob("*"):
            if not path.is_file() or skip_data_file(path):
                continue
            rel = path.relative_to(from_dir)
            dest = dest_name / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
            copied.append(str(dest.relative_to(extra)))
    from .app_catalog import reset_catalog_cache

    reset_catalog_cache()
    print(f"staged {len(copied)} game file(s) under artifacts/extra")
    return {"copied": copied, "extra": str(extra)}


def build_n64_game(*, sm64: Optional[Path] = None, mk64: Optional[Path] = None) -> dict:
    """Host-only build. Nothing is written to the nano."""
    argv = ["bash", str(root() / "scripts" / "build-games.sh")]
    if mk64 is not None:
        argv += ["--mk64", str(assert_mk64_rom(Path(mk64)))]
    if sm64 is not None:
        argv += ["--sm64", str(assert_sm64_rom(Path(sm64)))]
    if len(argv) == 2:
        raise Refused("pass --sm64 and/or --mk64 with your USA .z64")
    bash = which("bash") or "/bin/bash"
    print("nano7kit build-games (host only, no NAND, no Nintendo files in git)")
    rc = subprocess.call(argv, cwd=str(root()))
    if rc != 0:
        raise Refused(f"build-games.sh exited {rc}; see build/sm64-build.log / mk64-build.log")
    return stage_games_from_build()


def fetch_ios_port(slug: str) -> Path:
    meta = PORT_REPOS.get(slug)
    if not meta:
        raise Refused(f"unknown port {slug}")
    dest = ports_root() / slug
    dest.parent.mkdir(parents=True, exist_ok=True)
    if (dest / ".git").is_dir():
        got = subprocess.check_output(
            ["git", "-C", str(dest), "rev-parse", "HEAD"], text=True
        ).strip()
        if got == meta["commit"]:
            return dest
    if dest.exists():
        shutil.rmtree(dest)
    print(f"==> fetch {meta['url']} at {meta['commit'][:12]}")
    subprocess.check_call(["git", "clone", "--quiet", meta["url"], str(dest)])
    subprocess.check_call(["git", "-C", str(dest), "checkout", "--quiet", meta["commit"]])
    return dest


def _copy_app_shell(slug: str) -> Path:
    meta = PORT_REPOS[slug]
    repo = fetch_ios_port(slug)
    app_src = repo / "port" / "nano" / "app"
    if not app_src.is_dir():
        raise Refused(f"{slug} is missing port/nano/app")
    dest = root() / "nanoapps" / "apps" / meta["folder"]
    dest.mkdir(parents=True, exist_ok=True)
    for path in app_src.iterdir():
        if path.is_file():
            shutil.copyfile(path, dest / path.name)
    return dest


def _run_make(args: Sequence[str], cwd: Path, log: Path) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    uses_cross = any(str(a) == "make" or str(a).endswith("/make") for a in args)
    if uses_cross and not which("arm-none-eabi-gcc"):
        raise Refused("arm-none-eabi-gcc is missing (run ./start deps)")
    with log.open("w", encoding="utf-8") as fh:
        rc = subprocess.call(list(args), cwd=str(cwd), env=env, stdout=fh, stderr=subprocess.STDOUT)
    if rc != 0:
        tail = log.read_text(encoding="utf-8", errors="replace")[-2000:]
        raise Refused(f"{' '.join(args)} failed; see {log}\n{tail}")


def _stage_hbapp(name: str, hbapp: Path, data_src: Path) -> None:
    if not hbapp.is_file():
        raise Refused(f"built {name} hbapp missing: {hbapp}")
    extra = const.artifact("artifacts/extra")
    exe_d = extra / "Executables"
    exe_d.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(hbapp, exe_d / f"{name}.hbapp")
    dest_data = extra_data_dir(name)
    if dest_data is None:
        raise Refused(f"{name} has no data directory mapping")
    if dest_data.exists():
        shutil.rmtree(dest_data)
    dest_data.mkdir(parents=True, exist_ok=True)
    if data_src.is_dir():
        for path in data_src.rglob("*"):
            if not path.is_file() or skip_data_file(path, name):
                continue
            rel = path.relative_to(data_src)
            target = dest_data / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    from .app_catalog import reset_catalog_cache
    from .apps import ensure_extra_icon
    from .app_catalog import by_name

    reset_catalog_cache()
    row = by_name().get(name)
    if row:
        ensure_extra_icon(row)
    reset_catalog_cache()


def _safe_extract_zip(src: Path, dest: Path) -> None:
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src) as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in Path(name).parts:
                raise Refused(f"{src} has an unsafe path {info.filename!r}")
            target = (dest / name).resolve()
            if dest != target and dest not in target.parents:
                raise Refused(f"{src} has an unsafe path {info.filename!r}")
        zf.extractall(dest)


def extract_ipa(payload: Path) -> Path:
    """Unzip an .ipa/.zip into gitignored build/payloads/<sha16>/."""
    from .compat import sha256_file

    src = Path(payload).expanduser().resolve()
    if not src.is_file():
        raise Refused(f"ipa missing: {src}")
    digest = sha256_file(src)[:16]
    dest = root() / "build" / "payloads" / digest
    marker = dest / ".extracted"
    payload_dir = dest / "Payload"
    if marker.is_file() and payload_dir.is_dir() and any(payload_dir.glob("*.app")):
        return dest
    if dest.exists():
        shutil.rmtree(dest)
    try:
        _safe_extract_zip(src, dest)
    except zipfile.BadZipFile as exc:
        raise Refused(f"{src} is not a zip/ipa ({exc})") from exc
    if not (dest / "Payload").is_dir():
        raise Refused(f"{src} has no Payload/ folder (not an iOS ipa)")
    marker.write_text(digest + "\n", encoding="utf-8")
    return dest


def _read_bundle_id(app: Path) -> Optional[str]:
    pl = app / "Info.plist"
    if not pl.is_file():
        return None
    try:
        info = plistlib.loads(pl.read_bytes())
    except Exception:
        return None
    ident = str(info.get("CFBundleIdentifier") or "").strip()
    return ident or None


def _payload_app_dir(
    payload: Path,
    expected_names: Iterable[str],
    *,
    expected_bundle: Optional[str] = None,
) -> Path:
    src = Path(payload).expanduser().resolve()
    if not src.exists():
        raise Refused(f"payload missing: {src}")
    suffix = src.suffix.lower()
    if src.is_file() and suffix == ".app":
        raise Refused(f"{src} looks like a file; pass the unpacked .app directory or a .ipa")
    if src.is_file() and suffix in {".ipa", ".zip"}:
        src = extract_ipa(src)
    if src.is_dir() and src.suffix.lower() == ".app":
        app = src
    else:
        app = None
        for name in expected_names:
            cand = src / name
            if cand.is_dir():
                app = cand
                break
            nested = src / "Payload" / name
            if nested.is_dir():
                app = nested
                break
        if app is None:
            payload_dir = src / "Payload"
            found = sorted(payload_dir.glob("*.app")) if payload_dir.is_dir() else []
            if len(found) == 1:
                app = found[0]
    if app is None:
        raise Refused(
            f"{src} is not an unpacked iOS .app or .ipa. "
            "Pass the original .ipa or the Payload/*.app folder. "
            "This toolkit does not ship original game files."
        )
    if expected_bundle:
        got = _read_bundle_id(app)
        if not got:
            raise Refused(f"{app} has no CFBundleIdentifier in Info.plist")
        if got != expected_bundle:
            raise Refused(
                f"{app.name} is bundle {got}, not {expected_bundle}. "
                "Original game files stay local and are never committed."
            )
    return app


def build_flappy(payload: Path) -> dict:
    if not is_macos():
        raise Refused(
            "Flappy Bird asset conversion needs macOS sips (Apple PNG). "
            + FLAPPY_CMD
        )
    repo = fetch_ios_port("flappybird")
    app = _payload_app_dir(payload, ("Flap.app", "flap.app"))
    data = repo / "build" / "data"
    data.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "python3",
        str(repo / "tools" / "convert_assets.py"),
        str(app),
        str(data),
    ]
    log = root() / "build" / "flappy-assets.log"
    _run_make(cmd, repo, log)
    _copy_app_shell("flappybird")
    _run_make(
        ["make", "-C", str(repo / "port" / "nano"), f"NANOAPPS={root() / 'nanoapps'}"],
        repo,
        root() / "build" / "flappy-nano.log",
    )
    app_dir = root() / "nanoapps" / "apps" / "flappybird"
    _run_make(
        ["make", "-C", str(app_dir), f"FB_DIR={repo / 'port'}"],
        app_dir,
        root() / "build" / "flappy-app.log",
    )
    _stage_hbapp("Flappy Bird", app_dir / "build" / "flappybird.hbapp", data)
    return {"name": "Flappy Bird", "data": str(extra_data_dir("Flappy Bird"))}


def build_doodle(payload: Path) -> dict:
    if not is_macos():
        raise Refused(
            "Doodle Jump asset conversion needs macOS sips (Apple PNG). "
            + DOODLE_CMD
        )
    repo = fetch_ios_port("doodlejump")
    app = _payload_app_dir(payload, ("DoodleJump.app", "Doodle Jump.app"))
    data = repo / "build" / "data"
    data.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "python3",
        str(repo / "tools" / "convert_assets.py"),
        str(app),
        str(data),
    ]
    _run_make(cmd, repo, root() / "build" / "doodle-assets.log")
    _copy_app_shell("doodlejump")
    _run_make(
        ["make", "-C", str(repo / "port" / "nano"), f"NANOAPPS={root() / 'nanoapps'}"],
        repo,
        root() / "build" / "doodle-nano.log",
    )
    app_dir = root() / "nanoapps" / "apps" / "doodlejump"
    _run_make(
        ["make", "-C", str(app_dir), f"DJ_DIR={repo / 'port'}"],
        app_dir,
        root() / "build" / "doodle-app.log",
    )
    _stage_hbapp("Doodle Jump", app_dir / "build" / "doodlejump.hbapp", data)
    return {"name": "Doodle Jump", "data": str(extra_data_dir("Doodle Jump"))}


def build_angry(payload: Path) -> dict:
    """Host-only Angry Birds build from the user's 1.0 ipa (see host/angrybirds.py)."""
    from .angrybirds import build

    return build(Path(payload))


def build_temple(payload: Path) -> dict:
    """Host-only Temple Run build from the user's 1.0 ipa (see host/templerun.py)."""
    from .templerun import build

    return build(Path(payload))


def prepare_for_add(
    name: str,
    *,
    rom: Optional[Path] = None,
    payload: Optional[Path] = None,
) -> dict:
    """Build/stage a third-party port so apps add can copy it. Host only."""
    from .app_catalog import by_name, reset_catalog_cache

    reset_catalog_cache()
    row = by_name().get(name)
    if row is None:
        raise Refused(f"unknown app {name}")
    present = bool(row.get("present"))
    ready = present and (not PORT_DATA.get(name) or data_ready(name))
    if ready and rom is None and payload is None:
        return {"name": name, "present": True, "built": False}
    if name == "Mario 64":
        if rom is None and not ready:
            raise Refused(missing_help(name))
        if rom is not None:
            build_n64_game(sm64=Path(rom))
        elif not data_ready(name):
            raise Refused(missing_help(name))
        return {"name": name, "present": True, "built": rom is not None}
    if name == "Mario Kart":
        if rom is None and not ready:
            raise Refused(missing_help(name))
        if rom is not None:
            build_n64_game(mk64=Path(rom))
        elif not data_ready(name):
            raise Refused(missing_help(name))
        return {"name": name, "present": True, "built": rom is not None}
    if name == "Flappy Bird":
        if payload is None and not ready:
            raise Refused(missing_help(name))
        if payload is not None:
            build_flappy(Path(payload))
        return {"name": name, "present": True, "built": payload is not None}
    if name == "Doodle Jump":
        if payload is None and not ready:
            raise Refused(missing_help(name))
        if payload is not None:
            build_doodle(Path(payload))
        return {"name": name, "present": True, "built": payload is not None}
    if name == "Temple Run":
        if payload is None and not ready:
            raise Refused(missing_help(name))
        if payload is not None:
            build_temple(Path(payload))
        return {"name": name, "present": True, "built": payload is not None}
    if name == "Angry Birds":
        if payload is None and not ready:
            raise Refused(missing_help(name))
        if payload is not None:
            build_angry(Path(payload))
        return {"name": name, "present": True, "built": payload is not None}
    if name == "Fruit Ninja":
        if payload is None and not ready:
            raise Refused(missing_help(name))
        if payload is not None:
            from .fruitninja_rc import build as build_fruit_rc

            build_fruit_rc(Path(payload))
        return {"name": name, "present": True, "built": payload is not None}
    if name == "PvZ Portable":
        if payload is None and not ready:
            raise Refused(missing_help(name))
        if payload is not None:
            from .pvz_portable import build as build_pvzp

            build_pvzp(Path(payload))
        return {"name": name, "present": True, "built": payload is not None}
    if not present:
        folder = row.get("folder") or ""
        if folder and (root() / "nanoapps" / "apps" / folder / "Makefile").is_file():
            from .sdkapp import build_from_source

            build_from_source(name, folder)
            reset_catalog_cache()
            return {"name": name, "present": True, "built": True}
        raise Refused(f"{name} is not built in this tree")
    return {"name": name, "present": True, "built": False}


def print_status() -> None:
    from .app_catalog import catalog, reset_catalog_cache

    reset_catalog_cache()
    print("nano7kit third-party ports (olievans123)")
    print("Helvetica silver_resident / 39A10023. Pack cap 8 (grow_cache);")
    print("nogrow overlay raises host cap to 16 (./start apps cap enable).")
    print("Large GL (Mario, Temple Run, Fruit Ninja, PvZ Portable, Angry Birds) uses slot B, not the 576 KB slot A.")
    print("iOS-port repos state no OSS licence: credit, do not redistribute")
    print("derived game files. Original iOS apps and Nintendo ROMs stay local.")
    print()
    wanted = (
        "Mario 64",
        "Mario Kart",
        "Temple Run",
        "Flappy Bird",
        "Doodle Jump",
        "Fruit Ninja",
        "PvZ Portable",
        "Angry Birds",
    )
    by = {row["name"]: row for row in catalog()}
    for name in wanted:
        row = by.get(name) or {}
        staged = "staged" if row.get("present") else "not-built"
        data = "data-ok" if data_ready(name) else "data-missing"
        slot = "slot-B" if is_large_gl(name) else "slot-A-or-B"
        print(f"  {name:<16} {staged:<10} {data:<13} {slot}")
    print()
    print("Mario (exact): " + MARIO_CMD)
    print("Fruit Ninja:   " + FRUIT_CMD)
    print("PvZ Portable:  " + PVZP_CMD)
    print("Angry Birds:   " + ANGRY_CMD)
    print("Disk Mode (Do not disconnect). One extra per reboot.")
