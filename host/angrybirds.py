"""Angry Birds 1.0 (olievans123/nano-angrybirds) built from the user's own .ipa.

Nothing from Rovio is in git. The pinned nano-angrybirds tree (no OSS licence:
fetched at build time, credited, never vendored) is an engine
re-implementation: its own C/C++ engine, Lua 5.1 and Box2D run the game's
original Lua bytecode, and its converter repacks the user's sprite sheets into
the 256x128 16-bit pages the nano's GL driver takes. The iOS executable itself
is not used. nano7kit adds its own memory pre-flight, notice screen and
shared-GL-context clean-up (host/angrybirds_patch.py). Everything derived
lands in gitignored build/ports/angrybirds, build/angrybirds and
artifacts/extra.

Host only. Nothing here touches the nano; nabase add / apps add copies the
staged files in Disk Mode afterwards.
"""

from __future__ import annotations

import hashlib
import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import angrybirds_patch
from .compat import Refused, root

BUNDLE_ID = "com.clickgamer.AngryBirds"
WANT_VERSION = "1.0"
# nano-angrybirds re-implements the natives that this exact game script calls
# (its analysis/ specs cite 1.0's addresses), so the script is what is checked.
# The executable hash is only reported (thin armv6, 1,822,112 bytes).
GAMELOGIC_SHA256 = "16027cdae0aadf1ac6af74da4fcfe8910e63536ee2f1e84000219d811b934b47"
GAMELOGIC_SIZE = 129287
EXE_SHA256 = "e1dadaf6e07c02307d8c1ee9345b9c0e80b954b903dd3150f19d43eae66ec6e8"

# Every wheel exists for macOS x86_64 and arm64 on CPython 3.12 (same pins as Temple Run).
BUILD_PINS = (
    "texture2ddecoder==1.0.5",
    "numpy==2.2.6",
    "Pillow==12.1.1",
)
BUILD_IMPORTS = "import texture2ddecoder, numpy, PIL"

# The installed test profile in nano-angrybirds' README: pages at most 256x128,
# matching the Mario Kart renderer's page bounds (a 1-2 reboot went away with it).
CONVERT_ARGS = ("--max-page", "256", "--max-page-height", "128")

# Bump when the build recipe changes so a staged copy is rebuilt.
RECIPE = 1  # nano-angrybirds b547023 + nano7kit pre-flight, live texture budget, notice, gl_left

FAT_EXE = "/Apps/Executables/Angry Birds.hbapp"   # ab_entry.c APP_FILE: read back on relaunch
FAT_DATA = "/Apps/Data/AngryBirds"

CMD = "./start nabase add 'Angry Birds' --payload '/path/to/Angry Birds 1.0.ipa'"


def build_dir() -> Path:
    return root() / "build" / "angrybirds"


def venv_dir() -> Path:
    return build_dir() / "venv"


def stamp_path() -> Path:
    return build_dir() / "stamp.txt"


# ---------------------------------------------------------------- payload


def _app_dir(payload: Path) -> Path:
    from .ports import _payload_app_dir

    return _payload_app_dir(
        Path(payload), ("AngryBirds.app", "Angry Birds.app"), expected_bundle=BUNDLE_ID
    )


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def inspect_payload(payload: Path) -> dict:
    """Read-only facts about the user's Angry Birds. Unzips an .ipa under build/payloads."""
    app = _app_dir(payload)
    info = {}
    try:
        info = plistlib.loads((app / "Info.plist").read_bytes())
    except Exception:
        pass
    version = str(info.get("CFBundleShortVersionString") or info.get("CFBundleVersion") or "?")
    data = app / "data"
    logic = data / "gamelogic.lua"
    exe = app / str(info.get("CFBundleExecutable") or "AngryBirds")
    problems = []
    digest = ""
    if not data.is_dir() or not logic.is_file():
        problems.append(f"{app.name} has no data/gamelogic.lua; this is not the Angry Birds 1.0 app.")
    else:
        digest = _sha256(logic)
        if digest != GAMELOGIC_SHA256:
            problems.append(
                f"this is Angry Birds {version} with gamelogic.lua sha256 {digest[:16]}... "
                f"nano-angrybirds runs the Angry Birds {WANT_VERSION} script "
                f"(sha256 {GAMELOGIC_SHA256[:8]}...{GAMELOGIC_SHA256[-8:]}, {GAMELOGIC_SIZE:,} bytes); "
                "other versions call natives it does not have."
            )
    return {
        "app": app,
        "data": data,
        "version": version,
        "bundle": info.get("CFBundleIdentifier"),
        "gamelogic_sha256": digest,
        "exe_sha256": _sha256(exe) if exe.is_file() else "",
        "ok": not problems,
        "problems": problems,
    }


def assert_payload(payload: Path) -> dict:
    facts = inspect_payload(payload)
    if not facts["ok"]:
        raise Refused(" ".join(facts["problems"]) + " Your files stay local; nothing was built.")
    return facts


# ---------------------------------------------------------------- build


def ensure_venv() -> Path:
    """Private build/angrybirds/venv with pinned wheels. Returns its python."""
    py = venv_dir() / "bin" / "python"
    if py.is_file():
        r = subprocess.run([str(py), "-c", BUILD_IMPORTS], capture_output=True)
        if r.returncode == 0:
            return py
    if not py.is_file():
        venv_dir().parent.mkdir(parents=True, exist_ok=True)
        print(f"==> python venv {venv_dir()}")
        subprocess.check_call([sys.executable, "-m", "venv", str(venv_dir())])
    print("==> pip install " + " ".join(BUILD_PINS))
    rc = subprocess.call(
        [str(py), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", *BUILD_PINS]
    )
    if rc != 0:
        raise Refused(
            "pip could not install the Angry Birds build wheels "
            f"({', '.join(BUILD_PINS)}). Check the network, then re-run."
        )
    return py


def _kit_digest() -> str:
    """The SDK, nano7kit's patch and the files it copies in: a change rebuilds."""
    h = hashlib.sha256()
    sdk = root() / "nanoapps" / "sdk"
    for p in sorted(sdk.rglob("*")):
        if p.is_file() and p.suffix in {".c", ".h", ".mk", ".ld", ".S"}:
            h.update(p.relative_to(sdk).as_posix().encode())
            h.update(p.read_bytes())
    extra = [root() / "nanoapps" / "tools" / "mkrelocapp.py", root() / "host" / "angrybirds_patch.py"]
    extra += [root() / folder / name for folder, name in angrybirds_patch.OWN_FILES]
    for p in extra:
        if p.is_file():
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def stamp_value(gamelogic_sha256: str) -> str:
    from .ports import PORT_REPOS

    return (
        f"recipe={RECIPE} upstream={PORT_REPOS['angrybirds']['commit']} "
        f"gamelogic={gamelogic_sha256} kit={_kit_digest()} convert={' '.join(CONVERT_ARGS)}\n"
    )


def _staged() -> bool:
    from . import const
    from .ports import data_ready

    exe = const.artifact("artifacts/extra/Executables/Angry Birds.hbapp")
    return exe.is_file() and data_ready("Angry Birds")


def _jobs() -> str:
    return f"-j{max(1, min(8, os.cpu_count() or 2))}"


def build(payload: Path, *, force: bool = False) -> dict:
    """Host-only build of Angry Birds.hbapp + Data/AngryBirds from the user's ipa."""
    from .ports import _copy_app_shell, _run_make, _stage_hbapp, extra_data_dir, fetch_ios_port

    t0 = time.time()
    print("nano7kit Angry Birds build (host only, nothing written to the nano)")
    print("Port: olievans123/nano-angrybirds (fetched at build time, no OSS licence, not")
    print("redistributed). Game: Rovio, from your own .ipa (stays local).")
    facts = assert_payload(payload)
    print(f"    payload   {facts['app']}")
    print(f"    script    gamelogic.lua sha256 {facts['gamelogic_sha256'][:16]}... (Angry Birds {WANT_VERSION})")
    if facts["exe_sha256"] and facts["exe_sha256"] != EXE_SHA256:
        print(f"    note      executable sha256 {facts['exe_sha256'][:16]}... differs from the tested "
              "dump; the port does not use it")
    stamp = stamp_value(facts["gamelogic_sha256"])
    if (
        not force
        and _staged()
        and stamp_path().is_file()
        and stamp_path().read_text(encoding="utf-8") == stamp
    ):
        print("    build     already staged for this ipa (stamp match), skipping rebuild")
        return {"name": "Angry Birds", "built": False, "data": str(extra_data_dir("Angry Birds"))}

    repo = fetch_ios_port("angrybirds")
    py = ensure_venv()
    logs = build_dir()
    logs.mkdir(parents=True, exist_ok=True)
    data = repo / "build" / "data-nano"
    for d in (data, repo / "port" / "build"):
        if d.exists():
            shutil.rmtree(d)
    data.parent.mkdir(parents=True, exist_ok=True)
    print("==> convert_assets.py (sprite sheets to 256x128 16-bit pages; Lua copied as is)")
    _run_make(
        [str(py), str(repo / "tools" / "convert_assets.py"), str(facts["data"]), str(data), *CONVERT_ARGS],
        repo,
        logs / "assets.log",
    )
    if not (data / "files.lst").is_file() or not (data / "gamelogic.lua").is_file():
        raise Refused(f"convert_assets.py did not write files.lst / gamelogic.lua; see {logs}/assets.log")
    print("==> nano7kit edits: " + ", ".join(angrybirds_patch.apply(repo)))
    app_dir = _copy_app_shell("angrybirds")
    if (app_dir / "build").exists():
        shutil.rmtree(app_dir / "build")
    print("==> arm-none-eabi-gcc libab_nano.a (engine, Lua 5.1, Box2D; ARM + VFP)")
    _run_make(
        ["make", _jobs(), "-C", str(repo / "port" / "nano"), f"NANOAPPS={root() / 'nanoapps'}"],
        repo,
        logs / "nano.log",
    )
    print("==> angrybirds.hbapp (NanoApps SDK, reloc)")
    _run_make(
        ["make", "-C", str(app_dir), f"AB_DIR={repo / 'port'}", f"PYTHON={py}"],
        app_dir,
        logs / "app.log",
    )
    hbapp = app_dir / "build" / "angrybirds.hbapp"
    if not hbapp.is_file():
        raise Refused(f"build finished without {hbapp}; see {logs}/app.log")
    size = hbapp.stat().st_size
    _stage_hbapp("Angry Birds", hbapp, data)
    stamp_path().write_text(stamp, encoding="utf-8")
    data_bytes = sum(p.stat().st_size for p in data.rglob("*") if p.is_file())
    n_files = sum(1 for p in data.rglob("*") if p.is_file())
    print(
        f"    staged    Angry Birds.hbapp {size:,} bytes, Data/AngryBirds {n_files} files "
        f"{data_bytes / 1e6:.1f} MB ({time.time() - t0:.0f} s)"
    )
    return {
        "name": "Angry Birds",
        "built": True,
        "hbapp_size": size,
        "data_files": n_files,
        "data_bytes": data_bytes,
        "data": str(extra_data_dir("Angry Birds")),
    }


def needs_free() -> dict:
    """What the nano needs at launch (n7_ab.c), for help text and tests."""
    n = angrybirds_patch.glue_numbers()
    return {
        "largest": n["N7_ARENA_NEED"],
        "free": n["N7_ARENA_NEED"] + n["N7_TEX_MIN"] + n["N7_DRIVER"],
        "texture_max": n["N7_TEX_MAX"],
    }
