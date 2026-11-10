"""Build olievans123's pinned Angry Birds engine with the user's 1.0 assets.

No executable translation: only the original Lua bytecode and converted artwork
are used. Source is fetched at build time; game files stay in ignored directories.
"""
from __future__ import annotations

import hashlib
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from .compat import Refused, root

NAME = 'Angry Birds'
BUNDLE_ID = 'com.clickgamer.AngryBirds'
WANT_VERSION = '1.0'
# SHA-256 over sorted relative paths + NUL + each file's SHA-256 digest.
# AppleDouble/Finder metadata are excluded; the original executable is not used.
ASSET_SHA256 = '05dacfc26e35fcbf689916d88225a96de36aa7737c7a98f88436d1335ec16cbd'
BUILD_PINS = ('texture2ddecoder==1.0.5', 'numpy==2.2.6', 'Pillow==12.1.1', 'pyelftools==0.32')
RECIPE = 1
CMD = "./start nabase add 'Angry Birds' --payload /path/to/AngryBirds.ipa"


def asset_files(data: Path) -> list[Path]:
    return sorted(p for p in data.rglob('*') if p.is_file() and
                  not any(part.startswith('.') or part == '__MACOSX' for part in p.relative_to(data).parts))


def asset_digest(data: Path) -> str:
    digest = hashlib.sha256()
    for p in asset_files(data):
        digest.update(p.relative_to(data).as_posix().encode() + b'\0')
        digest.update(hashlib.sha256(p.read_bytes()).digest())
    return digest.hexdigest()


def inspect_payload(payload: Path) -> dict:
    from .ports import _payload_app_dir
    app = _payload_app_dir(Path(payload), ('AngryBirds.app',), expected_bundle=BUNDLE_ID)
    try:
        info = plistlib.loads((app / 'Info.plist').read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException) as exc:
        raise Refused(f'Angry Birds Info.plist is missing or invalid: {exc}')
    version = str(info.get('CFBundleShortVersionString') or info.get('CFBundleVersion') or '')
    data = app / 'data'
    problems = []
    if info.get('CFBundleIdentifier') != BUNDLE_ID or version != WANT_VERSION:
        problems.append(f'Expected {BUNDLE_ID} version {WANT_VERSION}; got {info.get("CFBundleIdentifier")} {version}.')
    for name in ('gamelogic.lua', 'blocks.lua', 'starLimits.lua', 'INGAME.dat'):
        if not (data / name).is_file():
            problems.append(f'Missing Angry Birds asset: {name}.')
    digest = asset_digest(data) if data.is_dir() else ''
    if digest != ASSET_SHA256:
        problems.append(f'Assets do not match the supported Angry Birds 1.0 build (sha256 {digest or "missing"}).')
    return dict(app=app, data=data, version=version, asset_sha256=digest, ok=not problems, problems=problems)


def assert_payload(payload: Path) -> dict:
    facts = inspect_payload(payload)
    if not facts['ok']:
        raise Refused(' '.join(facts['problems']) + ' Nothing was built.')
    return facts


def ensure_venv() -> Path:
    directory = root() / 'build/angrybirds/venv'
    py = directory / 'bin/python'
    if not py.is_file():
        directory.parent.mkdir(parents=True, exist_ok=True)
        subprocess.check_call([sys.executable, '-m', 'venv', str(directory)])
    if subprocess.run([str(py), '-c', 'import texture2ddecoder, numpy, PIL, elftools'], capture_output=True).returncode:
        subprocess.check_call([str(py), '-m', 'pip', 'install', '--disable-pip-version-check', *BUILD_PINS])
    return py


def validate_assets(data: Path) -> None:
    """Only manifest-listed converter output may be staged; exclude saves/logs."""
    manifest = data / 'files.lst'
    try:
        lines = manifest.read_text(encoding='ascii').splitlines()
        if not 0 < len(lines) <= 200 or manifest.stat().st_size >= 16384:
            raise ValueError('manifest exceeds nano limits')
        seen = set()
        for line in lines:
            name, size = line.split()
            if name in seen or Path(name).name != name or '\\' in name or name.startswith('.') or len(name) >= 48:
                raise ValueError(f'unsafe or duplicate asset name: {name}')
            seen.add(name)
            path = data / name
            if not path.is_file() or path.stat().st_size != int(size):
                raise ValueError(f'asset size mismatch: {name}')
        if {p.name for p in data.iterdir() if p.is_file()} != seen | {'files.lst'}:
            raise ValueError('unlisted converter output')
    except (OSError, UnicodeError, ValueError) as exc:
        raise Refused(f'Invalid Angry Birds converted data: {exc}')


def build(payload: Path, *, force: bool = False) -> dict:
    from . import const
    from .ports import PORT_REPOS, _copy_app_shell, _run_make, _stage_hbapp, data_ready, fetch_ios_port
    from .toolchain import select
    from .templerun import _sdk_digest
    facts = assert_payload(payload)  # Refuse wrong game/version/assets before fetching or compiling.
    if not select() or not shutil.which('arm-none-eabi-g++'):
        raise Refused('Angry Birds needs the complete Arm GNU C/C++ toolchain with newlib. Run ./start deps.')
    logs = root() / 'build/angrybirds'
    logs.mkdir(parents=True, exist_ok=True)
    stamp = f"recipe={RECIPE} upstream={PORT_REPOS['angrybirds']['commit']} assets={facts['asset_sha256']} sdk={_sdk_digest()}\n"
    stamp_path = logs / 'stamp.txt'
    staged = const.artifact('artifacts/extra/Executables/Angry Birds.hbapp')
    if not force and staged.is_file() and data_ready(NAME) and stamp_path.is_file() and stamp_path.read_text() == stamp:
        return dict(name=NAME, built=False)
    print('nano7kit Angry Birds build (host only; your game files stay local)')
    repo = fetch_ios_port('angrybirds')
    py = ensure_venv()
    clean = logs / 'original-data'
    converted = repo / 'port/build/data-small-pages'
    for directory in (clean, converted, repo / 'port/build/nano'):
        if directory.exists():
            shutil.rmtree(directory)
    clean.mkdir()
    for source in asset_files(facts['data']):
        target = clean / source.relative_to(facts['data'])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    _run_make([str(py), str(repo / 'tools/convert_assets.py'), str(clean), str(converted),
               '--max-page', '256', '--max-page-height', '128'], repo, logs / 'assets.log')
    validate_assets(converted)
    _run_make(['make', '-j' + str(min(os.cpu_count() or 4, 8)), '-C', str(repo / 'port/nano'),
               f'NANOAPPS={root() / "nanoapps"}'], repo, logs / 'nano.log')
    app = _copy_app_shell('angrybirds')
    if (app / 'build').exists():
        shutil.rmtree(app / 'build')
    _run_make(['make', '-C', str(app), f'AB_DIR={repo / "port"}', f'PYTHON={py}'], app, logs / 'app.log')
    hbapp = app / 'build/angrybirds.hbapp'
    if not hbapp.is_file() or hbapp.stat().st_size > 3 * 1024 * 1024:
        raise Refused('Angry Birds app is missing or exceeds the relaunch size limit.')
    icon_bin = logs / 'icon.bin'
    _run_make([str(py), '-m', 'host.angrybirds_icon', str(repo), str(clean), str(icon_bin)],
              root(), logs / 'icon.log')
    _stage_hbapp(NAME, hbapp, converted)
    target = const.artifact('artifacts/extra/Icons/Angry Birds.bin')
    shutil.copyfile(icon_bin, target)
    stamp_path.write_text(stamp)
    print(f'    staged    Angry Birds.hbapp {hbapp.stat().st_size:,} bytes; small-page assets and red-bird icon')
    return dict(name=NAME, built=True)


def plan_lines(payload: Path | None, *, heap_free=None, largest=None, **kw) -> list[str]:
    lines = ['Angry Birds 1.0 plan (no device writes)', '    status    untested in nano7kit; silent; landscape (Home on right)']
    if payload is None:
        return lines + [f'    payload   required: {CMD}', 'PLAN_REFUSED']
    try:
        facts = assert_payload(payload)
    except Refused as exc:
        return lines + [str(exc), 'PLAN_REFUSED']
    lines += [f"    assets    supported 1.0 build, sha256 {facts['asset_sha256']}",
              '    memory    upstream uses a 2–4 MiB arena plus textures/driver; device fit unverified',
              '    layout    NanoApps: nabase add; untethered: apps add (slot B)', 'PLAN_OK']
    return lines
