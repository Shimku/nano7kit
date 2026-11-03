"""Stage built NanoApps extras under untether/artifacts/extra/.

Run from the repo: python3 -m host.app_stage
Does not write NAND. Does not touch the hardware-tested three.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .app_catalog import catalog, reset_catalog_cache
from .app_icons import generate_icon, shared_screen_blob
from .compat import root, sha256_file
from .const import artifact
from .fixed_addrs import describe, legacy_hits

# Extras staged from this tree. Fruit Ninja here is the first-party slice
# shell only (its optional banner data comes from the user's own app).
SHELL_EXTRAS = ("Fruit Ninja",)


LOOKALIKES = "pocketjs/asset_lookalikes.json"


def proven_lookalikes(path: Path) -> set:
    """Offsets a build proved are baked asset bytes, for exactly this file.

    The PocketJS build records, per hbapp SHA-256, the words that look like
    an old fixed address but sit inside a baked font or image (see
    host.pocketjs._asset_lookalikes). Any other byte change drops the proof."""
    rec = root() / LOOKALIKES
    if not rec.is_file():
        return set()
    entry = json.loads(rec.read_text(encoding="utf-8")).get(sha256_file(path))
    return set(entry["offsets"]) if entry else set()


def assert_no_legacy_addresses(path: Path, name: str, allow_offsets=()) -> None:
    """Refuse a build that still writes the old SDK's fixed live-OS addresses.

    `allow_offsets` are image offsets the caller proved are baked asset bytes
    (font or image data that happen to look like such an address)."""
    allowed = set(allow_offsets) | proven_lookalikes(path)
    hits = [h for h in legacy_hits(path) if h["offset"] not in allowed]
    if hits:
        from .compat import Refused

        raise Refused(
            f"{name}: {path} still references fixed live-OS heap addresses: "
            f"{describe(hits)}. Rebuild it against the current SDK "
            "(make -C nanoapps/apps/FOLDER clean all)."
        )


def stage_named(folder: str, allow_legacy_offsets=()) -> dict:
    """Stage one built app by folder name into artifacts/extra."""
    reset_catalog_cache()
    extra = artifact("artifacts/extra")
    exe_d = extra / "Executables"
    icon_d = extra / "Icons"
    exe_d.mkdir(parents=True, exist_ok=True)
    icon_d.mkdir(parents=True, exist_ok=True)
    row = next((r for r in catalog() if r.get("folder") == folder), None)
    if row is None:
        from .compat import Refused

        raise Refused(f"{folder} is not in the catalog (missing Info.plist?)")
    src = root() / "nanoapps" / "apps" / folder / "build" / f"{folder}.hbapp"
    if not src.is_file():
        from .compat import Refused

        raise Refused(f"built hbapp missing: {src}")
    assert_no_legacy_addresses(src, row["name"], allow_legacy_offsets)
    dest_exe = exe_d / f"{row['name']}.hbapp"
    shutil.copyfile(src, dest_exe)
    dest_icon = icon_d / f"{row['name']}.bin"
    if not dest_icon.is_file() or dest_icon.stat().st_size != 50208:
        generate_icon(
            label=row["name"],
            color=row.get("color") or "#69809d",
            glyph=row.get("glyph") or "shapes",
            bundle_id=row["bundle_id"],
            dest=dest_icon,
        )
    reset_catalog_cache()
    print(f"staged {row['name']} {dest_exe.stat().st_size} bytes")
    return {
        "folder": folder,
        "title": row["name"],
        "size": dest_exe.stat().st_size,
        "exe": str(dest_exe),
        "icon": str(dest_icon),
    }


def stage_extras() -> dict:
    reset_catalog_cache()
    extra = artifact("artifacts/extra")
    exe_d = extra / "Executables"
    icon_d = extra / "Icons"
    exe_d.mkdir(parents=True, exist_ok=True)
    icon_d.mkdir(parents=True, exist_ok=True)
    screen = shared_screen_blob()
    (extra / "screen.bin").write_bytes(screen)
    apps_dir = root() / "nanoapps" / "apps"
    staged = []
    skipped = []
    for row in catalog():
        if row.get("tested"):
            continue
        if row.get("needs_rom"):
            skipped.append({"name": row["name"], "reason": "user ROM build"})
            continue
        if row.get("large_gl"):
            skipped.append({"name": row["name"], "reason": "large GL port (user files)"})
            continue
        if row.get("needs_payload") and row["name"] not in SHELL_EXTRAS:
            skipped.append({"name": row["name"], "reason": "user payload build"})
            continue
        src = apps_dir / row["folder"] / "build" / f"{row['folder']}.hbapp"
        if not src.is_file():
            skipped.append({"name": row["name"], "reason": "no hbapp"})
            continue
        assert_no_legacy_addresses(src, row["name"])
        dest_exe = exe_d / f"{row['name']}.hbapp"
        shutil.copyfile(src, dest_exe)
        dest_icon = icon_d / f"{row['name']}.bin"
        if not dest_icon.is_file() or dest_icon.stat().st_size != 50208:
            generate_icon(
                label=row["name"],
                color=row.get("color") or "#69809d",
                glyph=row.get("glyph") or "shapes",
                bundle_id=row["bundle_id"],
                dest=dest_icon,
            )
        staged.append(
            {
                "name": row["name"],
                "folder": row["folder"],
                "size": dest_exe.stat().st_size,
                "exe_sha256": sha256_file(dest_exe),
                "icon_sha256": sha256_file(dest_icon),
                "author": row["author"],
                "category": row["category"],
            }
        )
        print(f"staged {row['name']} {dest_exe.stat().st_size} bytes")
    man = {
        "note": (
            "Built extras for Helvetica silver_resident / 39A10023. "
            "Untested on MKN52 except Blocks, Game Boy, Probe (those stay in artifacts/device)."
        ),
        "sdk": (
            "hb_shared ABI v1: touch, trace and file buffers come from the resident's "
            "shared block or the app's own arena; no fixed live-OS addresses "
            "(host/fixed_addrs.py). Refresh installed copies: ./start apps refresh."
        ),
        "apps": staged,
        "skipped": skipped,
    }
    (extra / "MANIFEST.json").write_text(json.dumps(man, indent=2) + "\n")
    (extra / "README.txt").write_text(
        "NanoApps extras staged for ./start apps add.\n"
        "Hardware-tested three (Blocks, Game Boy, Probe) stay in artifacts/device.\n"
        "Extras beyond Blocks, Game Boy and Probe are tested one per reboot. Authors: see CREDITS.md.\n"
        "No Nintendo ROMs. Native Mario stages via ./start apps add Mario --rom ROM.z64.\n"
        "First-party Lap (RAW_SURFACE stopwatch) is staged: ./start apps add Lap.\n"
        "First-party JS Hello (PocketJS 240x432 HAL, not the runtime): ./start pocketjs hello then ./start apps add 'JS Hello'.\n"
        "olievans123 iOS ports need --payload and stay out of git (no OSS licence).\n"
        "Built against the hb_shared SDK (no fixed live-OS addresses).\n"
        "Already installed? ./start apps refresh re-copies them in one Disk Mode step.\n"
    )
    reset_catalog_cache()
    print(f"staged {len(staged)} extras, skipped {len(skipped)}")
    return man


if __name__ == "__main__":
    stage_extras()
