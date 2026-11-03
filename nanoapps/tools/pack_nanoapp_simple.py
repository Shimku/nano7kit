#!/usr/bin/env python3
"""Stage one nanoapps/apps/<name> into a distributable .nanoapp zip.

Uses the local build_apps icon helpers + mkapp.py bundle, then package_app.write_package.
Does not rewrite AllApps.pack.
"""
from __future__ import annotations

import hashlib
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "dependencies" / "ipodhax"))

from package_app import write_package, validate_folder  # noqa: E402
from build_apps import (  # noqa: E402
    SIZE, FMT, ICON_BASE_ID, HBSCREEN, HBLAYOUT, HBAP, HBSD, SCREEN,
    load_meta, make_png, sbid_for,
)
from ipodhax.silverdb import pack_silverdb  # noqa: E402


def app_path(name: str) -> Path:
    folder = ROOT / "apps" / name
    if not (folder / "Info.plist").is_file():
        raise SystemExit(f"no Info.plist for {name} under nanoapps/apps/")
    return folder


def stage_one(name: str, out: Path) -> Path:
    meta = load_meta(name, 0)
    # Prefer Info.plist HBMinimumResidentVersion if present
    pl = plistlib.loads((app_path(name) / "Info.plist").read_bytes())
    minimum = int(pl.get("HBMinimumResidentVersion", meta.get("minimum_resident", 0) or 0))
    label = meta["label"]
    folder_name = validate_folder(label + ".nanoapp")

    appsd = out / "Apps"
    folder = appsd / folder_name
    folder.mkdir(parents=True)

    with tempfile.TemporaryDirectory(prefix="nanoapp-icon-") as td:
        td = Path(td)
        icons_src = td / "icons_src"
        icons_src.mkdir()
        dst = icons_src / f"{ICON_BASE_ID}_{FMT:04x}.png"
        custom = app_path(name) / "icon.png"
        if custom.exists():
            from PIL import Image
            Image.open(custom).convert("RGBA").resize((SIZE, SIZE), Image.LANCZOS).save(dst)
        else:
            make_png(meta["glyph"], meta["color"], dst)
        icondb = td / "icons.bin"
        with open(icondb, "wb") as f:
            pack_silverdb(f, icons_src)
        data = icondb.read_bytes()
        ds = struct.unpack_from("<I", data, 4)[0]
        cnt = struct.unpack_from("<I", data, 16)[0]
        assert cnt >= 1
        off = struct.unpack_from("<I", data, 28 + 4)[0]
        ln = struct.unpack_from("<I", data, 28 + 8)[0]
        icon = data[ds + off : ds + off + ln]
        (folder / "icon.bin").write_bytes(icon)

        app_id = 0x10000000 | (
            int.from_bytes(hashlib.sha256(meta["bundle_id"].encode()).digest()[:4], "little")
            & 0x0FFFFFFF
        )
        sbid = sbid_for(name)
        if len(sbid) > 23:
            raise SystemExit(f"sbid {sbid} too long")
        appf = td / f"{name}.app"
        subprocess.run(
            [
                "python3",
                str(ROOT / "tools" / "mkapp.py"),
                "bundle",
                str(appf),
                "--id",
                hex(app_id),
                "--kind",
                str(meta["kind"]),
                "--sbid",
                sbid,
                "--name",
                sbid,
                "--label-id",
                hex(app_id),
                "--label-tag",
                hex(HBAP),
                "--label-text",
                label,
                "--screen-id",
                str(HBSCREEN),
                "--layout-id",
                str(HBLAYOUT),
                "--screen-tag",
                hex(HBSD),
                "--screen",
                str(SCREEN),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        binsrc = app_path(name) / "build" / f"{name}.hbapp"
        if not binsrc.is_file():
            raise SystemExit(f"missing {binsrc}; build the app first")
        exe = binsrc.read_bytes()
        (folder / "app.hbapp").write_bytes(exe)
        from package_app import manifest_for

        (folder / "manifest.napp").write_bytes(
            manifest_for(appf.read_bytes(), exe, icon, minimum)
        )
    return appsd


def package(name: str, output: Path | None = None) -> Path:
    output = output or (ROOT / "build" / "packages" / f"{name}.zip")
    with tempfile.TemporaryDirectory(prefix="nanoapp-stage-") as td:
        appsd = stage_one(name, Path(td) / "stage")
        result = write_package(appsd, output)
    print(f"Package: {result}")
    print("Unzip, copy the .nanoapp folder into Apps on the iPod, then eject.")
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: pack_nanoapp_simple.py <app> [app...]")
    for n in sys.argv[1:]:
        package(n)
