"""NanoApps Home catalog for Mid-2015 / Helvetica silver_resident.

Survey (2026-10-29): most Home apps live in nanoapps/apps/*/Info.plist.
Third-party olievans123 ports (Mario 64 / Mario Kart from nano7-untethered;
Temple Run, Flappy Bird, Doodle Jump from their own repos) are catalogued
here too. Original game files stay out of git.

Helvetica resident is silver_resident, so LVGL / raw / GL apps built for
39A10023 are compatible in principle. Hardware-run on MKN52: Blocks,
Game Boy, Probe. Extra apps are built here and untested on this unit
except the 11-icon result: adding FIRST_SLICE as a set (11 Home icons)
printed APPS_OK, showed all 11 icons, then reboot-looped after ~1-2 s.
Disk Mode remove of those 8 restored the 3-app pack. Apps over ~0.5 MB
can fail to launch on slot A. Native Mario is ~3 MB GL and uses slot B.
BigLoad and FPUTest did not build in this tree.
"""

from __future__ import annotations

import plistlib
from pathlib import Path
from typing import Dict, List, Optional

from . import const
from .compat import root, sha256_file


# The eight extras that, merged onto the tested three, reboot-looped on
# MKN52 (11 icons on Home, then ~1-2 s of UI, then reboot).
# G2048 is the CFBundleName (alias 2048). Do not add this set together.
FIRST_SLICE = (
    "Calculator",
    "Notes",
    "Files",
    "Snake",
    "Tetris",
    "G2048",
    "Paint",
    "Calendar",
)

# silver_resident hb_app_register_pack: if (n > 8u) grow the OS bitmap
# cache by 1 MiB. The 3-app pack never takes that path; the 11-app pack did.
# Stock host cap stays 8. `./start apps cap enable` overlays a one-branch
# nogrow patch (bls.n -> b.n) and raises the host pack limit to 16. The
# The 11-app FIRST_SLICE set stays refused even with nogrow.
GROW_CACHE_LIMIT = 8

# Suggested Home extras. Graphics / Sensors / developer stay out.
# Default `./start apps set` target is the tested three plus these six (9).
# Mario / Temple Run are later, one extra per reboot, and need --rom / --payload.
SUGGESTED_HOME = (
    "Blocks",
    "Game Boy",
    "Probe",
    "Chess",
    "Books",
    "Calculator",
    "Notes",
    "Wallet",
    "Screenshot",
)
SUGGESTED_EXTRAS = (
    "Chess",
    "Books",
    "Calculator",
    "Notes",
    "Wallet",
    "Screenshot",
)

# `apps set` will not add these. Skip graphics, developer, Sensors.
SKIP_FROM_SET = frozenset(
    {
        "Benchmark",
        "OpenGL",
        "Stress",
        "Widgets",
        "Sensors",
        "Level",
        "GLBench",
        "BigLoad",
        "FPUTest",
    }
)

# One extra per Disk Mode reboot. Paint is raw, 8772 bytes, not LVGL.
LOWEST_RISK_EXTRA = "Paint"

# Hardware plan: add this order, reboot Sleep/Wake+Home after each, restore-tested
# if it loops. Calendar is last (RTC/events; still not a boot widget).
TEST_ORDER_EXTRAS = (
    "Paint",
    "Tetris",
    "Snake",
    "G2048",
    "Files",
    "Notes",
    "Calculator",
    "Calendar",
)

# Lower is safer to try first. 0 is hardware-safe on this MKN52.
RISK_LABEL = {
    0: "hardware-safe",
    1: "lowest (raw/tiny)",
    2: "low (lvgl game)",
    3: "medium (lvgl productivity)",
    4: "higher (clock/calendar/widgets)",
    5: "higher (gl/graphics/sensors)",
    6: "needs-rom",
    7: "not-built",
}

CATEGORIES = {
    "productivity": (
        "Books",
        "Calculator",
        "Calendar",
        "Contacts",
        "Countdown",
        "Dictionary",
        "Elements",
        "Files",
        "Notes",
        "Passwords",
        "Reminders",
        "Wallet",
    ),
    "tools": ("Clock", "Paint", "Remote", "Screenshot", "Sensors", "Level"),
    "games": (
        "G2048",
        "Breakout",
        "Connect4",
        "Crosswords",
        "Golf",
        "Match3",
        "Minesweeper",
        "Pong",
        "Quiz",
        "Rhythm",
        "Simon Says",
        "Snake",
        "Sudoku",
        "Tetris",
        "Tictactoe",
        "Tilt Ball",
        "Tower",
        "Wordle",
    ),
    "cards": ("Chess", "Freecell", "Heads Up", "Mahjong", "Solitaire", "Holdem"),
    "graphics": ("Benchmark", "OpenGL", "Stress", "Widgets"),
    "diagnostic": ("BigLoad", "FPUTest", "GLBench", "Probe"),
    "first-party": ("Lap", "JS Hello", "JS Hero", "JS Samples", "JS QuickJS", "Free RAM"),
    "hardware-tested": ("Blocks", "Game Boy", "Probe"),
    "ports": (
        "Mario Kart",
        "Mario 64",
        "Temple Run",
        "Flappy Bird",
        "Doodle Jump",
        "Fruit Ninja",
        "PvZ Portable",
        "Gravity Defied",
    ),
    "needs-rom": ("Mario Kart", "Mario 64"),
}

# nfzerox wrote the NanoApps apps (MIT). Extra credits per app:
AUTHORS = {
    "Blocks": "nfzerox (NanoApps, MIT)",
    "Game Boy": "nfzerox (NanoApps, MIT); Peanut-GB core by Mahyar Koshkouei (MIT)",
    "Probe": "nfzerox (NanoApps, MIT)",
    "Widgets": "nfzerox (NanoApps, MIT); LVGL widgets by LVGL contributors (MIT)",
    "Mario Kart": (
        "olievans123 nano7-untethered; jnmartin84 mk64-dc; n64decomp/mk64 "
        "(user ROM, not in git)"
    ),
    "Mario 64": (
        "olievans123 nano7-untethered; jnmartin84 sm64-dc; n64decomp/sm64 "
        "(user ROM, not in git)"
    ),
    "Temple Run": "olievans123 nano-templerun (original iOS app not in git)",
    "Flappy Bird": "olievans123 nano-flappybird (original iOS app not in git)",
    "Doodle Jump": "olievans123 nano-doodlejump (original iOS app not in git)",
    "Fruit Ninja": (
        "nano7kit first-party static recompile (MIT; approach after "
        "olievans123 nano-templerun); original Halfbrick Fruit Ninja 1.0 not in git"
    ),
    "PvZ Portable": (
        "PvZ-Portable engine by wszqkzqk (LGPL-3.0-or-later, fetched at build time) with nano7kit's device "
        "layer; dlmalloc by Doug Lea (CC0); your own PopCap PvZ GOTY files, not in git"
    ),
    "Gravity Defied": (
        "gravity_defied_cpp by rgimad, AntonEvmenenko and Max Logaev (GPL-2.0, fetched at a pinned commit) "
        "with nano7kit's framebuffer layer; original J2ME game by Codebrew (Tors, Set, Per), 2004; "
        "dlmalloc by Doug Lea (CC0); your own J2ME .jar, not in git"
    ),
    "Lap": "nano7kit first-party (MIT); NanoApps SDK (MIT)",
    "Free RAM": "nano7kit first-party LVGL memory probe (MIT); NanoApps SDK (MIT)",
    "JS Hello": (
        "nano7kit first-party PocketJS HAL prototype (MIT); "
        "NanoApps SDK (MIT). Not the PocketJS runtime."
    ),
    "JS Hero": (
        "PocketJS by pocket-nexus (MIT, fetched at a pinned commit at build time) "
        "with nano7kit's nano7g target (pocketjs/nano7g.patch); NanoApps SDK (MIT)"
    ),
    "JS Samples": (
        "PocketJS by pocket-nexus (MIT, fetched at a pinned commit at build time): its "
        "gba-hero, ipodnano-hero, hero, solid-aot-lab and vue-sfc-lab samples, with nano7kit's "
        "nano7g target, JS Hero and Tilt (pocketjs/nano7g.patch); NanoApps SDK (MIT)"
    ),
    "JS QuickJS": (
        "PocketJS by pocket-nexus (MIT) with its canonical QuickJS guest; QuickJS by Fabrice "
        "Bellard and Charlie Gordon (MIT, the revision PocketJS pins, fetched at build time); "
        "dlmalloc by Doug Lea (public domain); newlib (BSD-style); NanoApps SDK (MIT)"
    ),
}

ALIASES = {
    "2048": "G2048",
    "g2048": "G2048",
    "gameboy": "Game Boy",
    "game-boy": "Game Boy",
    "simon": "Simon Says",
    "simonsays": "Simon Says",
    "tiltball": "Tilt Ball",
    "tilt-ball": "Tilt Ball",
    "headsup": "Heads Up",
    "heads-up": "Heads Up",
    "mk64": "Mario Kart",
    "mariokart": "Mario Kart",
    "mario-kart": "Mario Kart",
    "sm64": "Mario 64",
    "mario64": "Mario 64",
    "mario": "Mario 64",
    "supermario": "Mario 64",
    "super-mario": "Mario 64",
    "super mario": "Mario 64",
    "templerun": "Temple Run",
    "temple-run": "Temple Run",
    "temple": "Temple Run",
    "flappy": "Flappy Bird",
    "flappybird": "Flappy Bird",
    "flappy-bird": "Flappy Bird",
    "doodle": "Doodle Jump",
    "doodlejump": "Doodle Jump",
    "doodle-jump": "Doodle Jump",
    "fruit": "Fruit Ninja",
    "fruitninja": "Fruit Ninja",
    "fruit-ninja": "Fruit Ninja",
    "ninja": "Fruit Ninja",
    "pvzportable": "PvZ Portable",
    "pvz-portable": "PvZ Portable",
    "pvz_portable": "PvZ Portable",
    "gd": "Gravity Defied",
    "gdtr": "Gravity Defied",
    "gravity": "Gravity Defied",
    "gravitydefied": "Gravity Defied",
    "gravity-defied": "Gravity Defied",
    "gravity_defied": "Gravity Defied",
    "tictactoe": "Tictactoe",
    "tic-tac-toe": "Tictactoe",
    "lap": "Lap",
    "stopwatch": "Lap",
    "jshello": "JS Hello",
    "js-hello": "JS Hello",
    "pockethello": "JS Hello",
    "pocket-hello": "JS Hello",
    "jshero": "JS Hero",
    "js-hero": "JS Hero",
    "pjhero": "JS Hero",
    "pocketjs-hero": "JS Hero",
    "jssamples": "JS Samples",
    "js-samples": "JS Samples",
    "pjsamples": "JS Samples",
    "pocketjs-samples": "JS Samples",
    "jsquickjs": "JS QuickJS",
    "js-quickjs": "JS QuickJS",
    "pjquickjs": "JS QuickJS",
    "quickjs": "JS QuickJS",
    "freeram": "Free RAM",
    "free-ram": "Free RAM",
}

HARNESSES = (
    "api_tests",
    "brightness_test",
    "button_test",
    "fs_test",
    "multitouch_test",
    "wav_player",
)

SYSTEM_NOT_HOME = (
    "silver_resident",
    "resident_selector",
    "resident_stub",
    "untether_bootstrap",
    "heap_diagnostic",
    "deferred_untether",
)

LAUNCH_SOFT_CAP = 512 * 1024

# Explicit ranks for the looping slice and other stand-outs. Category fallback
# covers the rest. Paint is in tools but is the lowest-risk extra.
_RISK_BY_NAME = {
    "Blocks": 0,
    "Game Boy": 0,
    "Probe": 0,
    "Paint": 1,
    "Tetris": 2,
    "Snake": 2,
    "G2048": 2,
    "Tictactoe": 2,
    "Pong": 2,
    "Connect4": 2,
    "Breakout": 2,
    "Simon Says": 2,
    "Rhythm": 2,
    "Match3": 2,
    "Minesweeper": 2,
    "Sudoku": 2,
    "Wordle": 2,
    "Tilt Ball": 2,
    "Tower": 2,
    "Golf": 2,
    "Quiz": 2,
    "Crosswords": 2,
    "Chess": 2,
    "Freecell": 2,
    "Solitaire": 2,
    "Mahjong": 2,
    "Holdem": 2,
    "Heads Up": 2,
    "Files": 3,
    "Notes": 3,
    "Calculator": 3,
    "Books": 3,
    "Contacts": 3,
    "Dictionary": 3,
    "Elements": 3,
    "Passwords": 3,
    "Reminders": 3,
    "Wallet": 3,
    "Remote": 3,
    "Calendar": 4,
    "Clock": 4,
    "Countdown": 4,
    "Widgets": 4,
    "OpenGL": 5,
    "GLBench": 5,
    "Benchmark": 5,
    "Stress": 5,
    "Screenshot": 5,
    "Sensors": 5,
    "Level": 5,
    "Mario Kart": 6,
    "Mario 64": 6,
    "Temple Run": 6,
    "Flappy Bird": 6,
    "Doodle Jump": 6,
    "Fruit Ninja": 6,
    "PvZ Portable": 6,
    "Gravity Defied": 6,
    "Lap": 1,
    "JS Hello": 1,
    "JS Hero": 5,
    "JS Samples": 5,
    "JS QuickJS": 5,
    "Free RAM": 3,
    "BigLoad": 7,
    "FPUTest": 7,
}


def looping_pack_names() -> frozenset:
    """The 11-name AllApps.pack that reboot-looped on MKN52."""
    return frozenset(list(const.TESTED_APPS.keys()) + list(FIRST_SLICE))


def hardware_safe_names() -> List[str]:
    return list(const.TESTED_APPS.keys())


def risk_rank(name: str) -> int:
    if name in const.TESTED_APPS:
        return 0
    if name in _RISK_BY_NAME:
        return _RISK_BY_NAME[name]
    if name in CATEGORIES["needs-rom"]:
        return 6
    if name in CATEGORIES["graphics"] or name in (
        "GLBench",
        "Screenshot",
        "Sensors",
        "Level",
    ):
        return 5
    if name in ("Clock", "Calendar", "Countdown", "Widgets"):
        return 4
    if name in CATEGORIES["games"] or name in CATEGORIES["cards"]:
        return 2
    if name in CATEGORIES["productivity"] or name in CATEGORIES["tools"]:
        return 3
    return 3


def risk_label(name: str) -> str:
    return RISK_LABEL.get(risk_rank(name), "unranked")


def extras_in(names) -> List[str]:
    tested = set(const.TESTED_APPS.keys())
    out = []
    for n in names:
        if n not in tested and n not in out:
            out.append(n)
    return out


def skipped_from_set(name: str) -> bool:
    if name in SKIP_FROM_SET:
        return True
    if name in HARNESSES or name in SYSTEM_NOT_HOME:
        return True
    if name in CATEGORIES.get("graphics", ()):
        return True
    return False


def _plist_apps() -> List[dict]:
    apps_dir = root() / "nanoapps" / "apps"
    rows = []
    if not apps_dir.is_dir():
        return rows
    for d in sorted(apps_dir.iterdir()):
        pl = d / "Info.plist"
        if not pl.is_file():
            continue
        try:
            info = plistlib.loads(pl.read_bytes())
        except Exception:
            continue
        name = str(info.get("CFBundleName") or d.name)
        if d.name.startswith("_"):
            continue
        rows.append(
            {
                "folder": d.name,
                "name": name,
                "bundle_id": str(info.get("CFBundleIdentifier") or f"org.nanoapps.{d.name}"),
                "kind": int(info.get("HBAppKind") or 1),
                "glyph": str(info.get("HBIconGlyph") or "shapes"),
                "color": str(info.get("HBIconColor") or "#69809d"),
                "executable_key": str(info.get("CFBundleExecutable") or d.name),
            }
        )
    return rows


# Our own apps ship prebuilt. NanoApps-derived ones are compiled on demand.
SHIPPED_EXTRAS = ("Free RAM", "JS Hello", "JS Hero", "JS QuickJS", "JS Samples", "Lap")


def _category_for(name: str, bundle_id: str = "") -> str:
    for cat, names in CATEGORIES.items():
        if cat == "hardware-tested":
            continue
        if name in names:
            return cat
    if str(bundle_id).startswith("org.nano7kit."):
        return "first-party"
    return "other"


def _author_for(name: str, bundle_id: str = "") -> str:
    if name in AUTHORS:
        return AUTHORS[name]
    if str(bundle_id).startswith("org.nano7kit."):
        return "nano7kit first-party (MIT); NanoApps SDK (MIT)"
    return "nfzerox (NanoApps, MIT)"


def tested_paths(name: str) -> Optional[dict]:
    meta = const.TESTED_APPS.get(name)
    if not meta:
        return None
    return {
        "executable": meta["executable"],
        "icon": meta["icon"],
        "fat_exe": meta["fat_exe"],
        "fat_icon": meta["fat_icon"],
    }


def extra_paths(name: str) -> dict:
    from .ports import PORT_DATA

    data = PORT_DATA.get(name) or {}
    return {
        "executable": f"artifacts/extra/Executables/{name}.hbapp",
        "icon": f"artifacts/extra/Icons/{name}.bin",
        "bundle": f"artifacts/extra/Bundles/{name}.hbap",
        "fat_exe": f"/Apps/Executables/{name}.hbapp",
        "fat_icon": f"/Apps/Icons/{name}.bin",
        "data": data.get("host") or "",
        "fat_data": data.get("fat") or "",
    }


def catalog_entry(plist_row: dict) -> dict:
    name = plist_row["name"]
    tested = tested_paths(name)
    extra = extra_paths(name)
    tested_exe = const.artifact(tested["executable"]) if tested else None
    extra_exe = const.artifact(extra["executable"])
    extra_icon = const.artifact(extra["icon"])
    src = None
    icon = None
    origin = "missing"
    if tested and tested_exe is not None and tested_exe.is_file():
        src = tested_exe
        icon = const.artifact(tested["icon"])
        origin = "hardware-tested"
    elif extra_exe.is_file():
        src = extra_exe
        icon = extra_icon if extra_icon.is_file() else None
        origin = "built"
    size = src.stat().st_size if src is not None else 0
    from .ports import (
        LARGE_GL,
        POCKETJS_APPS,
        NEEDS_PAYLOAD,
        MARIO_CMD,
        data_ready,
        missing_help,
    )

    needs_rom = name in CATEGORIES["needs-rom"]
    payload = name in NEEDS_PAYLOAD
    large_gl = name in LARGE_GL
    ready_data = data_ready(name)
    compatible = origin in {"hardware-tested", "built"} and (
        not needs_rom or (origin == "built" and ready_data)
    )
    if payload:
        compatible = origin == "built" and ready_data
    buildable = (
        origin == "missing"
        and not (needs_rom or payload or large_gl)
        and bool(plist_row.get("folder"))
        and (root() / "nanoapps" / "apps" / str(plist_row.get("folder")) / "Makefile").is_file()
    )
    if buildable:
        compatible = True
    rank = risk_rank(name)
    if origin == "hardware-tested":
        note = "hardware-safe on MKN52 (Helvetica / 39A10023)"
    elif name == LOWEST_RISK_EXTRA:
        note = "lowest-risk extra; add this one first (one app per reboot)"
    elif needs_rom and origin != "built":
        note = missing_help(name)
    elif payload and origin != "built":
        note = missing_help(name)
    elif large_gl and name == "JS QuickJS":
        note = (
            f"PocketJS on QuickJS (nano7g); ~{size} bytes, RAW_SURFACE, slot B. Reads its "
            "JavaScript packages from /Apps/Data/PocketJS (./start pocketjs packages). Takes "
            "one OS heap block of 2.3 to 2.6 MB while an app is open; freed on quit."
        )
    elif large_gl and name in POCKETJS_APPS:
        note = (
            f"PocketJS MicroTS app (nano7g); ~{size} bytes, RAW_SURFACE. Over the 576 KB "
            "slot A staging, so apps add uses slot B (AllApps-B.pack). Takes one OS heap "
            "block of 0.3 to 1.4 MB while open; freed on quit. ./start pocketjs apps"
        )
    elif large_gl:
        note = (
            f"native GL port; ~{size} bytes. Slot A staging is 576 KB so "
            "apps add uses hardware-tested slot B (AllApps-B.pack). "
            f"Pack cap 8. Probe heap 7.0 MB free / 4.0 MB largest (fresh boot, current pack). {MARIO_CMD if name == 'Mario 64' else ''}"
        ).strip()
    elif size > LAUNCH_SOFT_CAP:
        note = f"{size} bytes is over the ~0.5 MB launch cap; may fail to open"
    elif origin == "missing" and (
        name == "Lap" or str(plist_row.get("bundle_id") or "").startswith("org.nano7kit.")
    ):
        note = (
            "first-party RAW_SURFACE (slot A). "
            f"./start sdk build {plist_row.get('folder') or 'lap'}"
        )
    elif buildable:
        note = "built from source on this computer on first add (a few seconds)"
    elif origin == "missing":
        note = "did not build in this tree (or needs a ROM / original app)"
    else:
        note = (
            "built for 39A10023 / Helvetica silver_resident; untested on this "
            f"MKN52; {RISK_LABEL.get(rank, 'unranked')}"
        )
    bundle = plist_row.get("bundle_id") or ""
    return {
        "name": name,
        "folder": plist_row["folder"],
        "bundle_id": bundle,
        "kind": plist_row["kind"],
        "glyph": plist_row["glyph"],
        "color": plist_row["color"],
        "category": _category_for(name, bundle),
        "author": _author_for(name, bundle),
        "tested": origin == "hardware-tested",
        "hardware_safe": origin == "hardware-tested",
        "origin": origin,
        "present": src is not None and src.is_file(),
        "compatible": compatible,
        "buildable": buildable,
        "needs_rom": needs_rom,
        "needs_payload": payload,
        "large_gl": large_gl,
        "data": extra.get("data") or "",
        "fat_data": extra.get("fat_data") or "",
        "data_ready": ready_data,
        "size": size,
        "sha256": sha256_file(src) if src is not None and src.is_file() else "",
        "executable": (tested or extra)["executable"] if (tested or extra) else extra["executable"],
        "icon": (tested["icon"] if tested and origin == "hardware-tested" else extra["icon"]),
        "fat_exe": extra["fat_exe"] if not tested else tested["fat_exe"],
        "fat_icon": extra["fat_icon"] if not tested else tested["fat_icon"],
        "note": note,
        "risk": rank,
        "risk_label": RISK_LABEL.get(rank, "unranked"),
        "aliases": [k for k, v in ALIASES.items() if v == name],
    }


_CACHE: Optional[List[dict]] = None
_BY_NAME: Optional[Dict[str, dict]] = None


def catalog() -> List[dict]:
    global _CACHE, _BY_NAME
    if _CACHE is None:
        from .ports import OVERLAY_PLISTS

        rows = [catalog_entry(row) for row in _plist_apps()]
        seen = {row["name"] for row in rows}
        for overlay in OVERLAY_PLISTS:
            if overlay["name"] not in seen:
                rows.append(catalog_entry(overlay))
        _CACHE = rows
        _BY_NAME = {row["name"]: row for row in _CACHE}
    return list(_CACHE)


def reset_catalog_cache() -> None:
    global _CACHE, _BY_NAME
    _CACHE = None
    _BY_NAME = None


def by_name() -> Dict[str, dict]:
    catalog()
    return dict(_BY_NAME or {})


def resolve_name(token: str) -> Optional[str]:
    raw = str(token).strip()
    if not raw:
        return None
    key = raw.lower()
    if key in ALIASES:
        return ALIASES[key]
    names = by_name()
    if raw in names:
        return raw
    for name in names:
        if name.lower() == key:
            return name
        folder = names[name].get("folder") or ""
        if folder.lower() == key:
            return name
    return None


def addable_names() -> List[str]:
    return [
        row["name"]
        for row in catalog()
        if (row["present"] or row.get("buildable"))
        and (not row.get("needs_rom") or row.get("data_ready"))
    ]


def known_names() -> List[str]:
    return [row["name"] for row in catalog()]
