"""Repeatable nano 7G porting pipeline, modelled on olievans123.

Three families we actually have working references for:

1. decomp + oracle (Flappy Bird, Doodle Jump): translate the original
   engine to C; Unicorn runs the original ARM beside it; assets from
   the original atlas via convert_assets.py (macOS sips).
2. recomp + oracle (Temple Run): ARM machine code -> C (recomp.py);
   original_oracle.py + compare_engine.py; CPU-side GL (glfe.c) because
   the nano driver reboots on ordinary triangles.
3. N64 (SM64 / MK64): existing games/ tree, user ROM, slot B.

First-party apps are not ports: ./start sdk new (RAW_SURFACE template).

Original game files, recomp output, and converted atlases stay on the
operator's machine. The iOS-port repos state no OSS licence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import const
from .app_catalog import GROW_CACHE_LIMIT, LAUNCH_SOFT_CAP, catalog, reset_catalog_cache
from .compat import Refused, root
from .ports import LARGE_GL, MARIO_CMD, SLOT_A_GL_STAGING, is_large_gl


FAMILIES = (
    {
        "id": "n64",
        "name": "N64 (SM64 / MK64)",
        "reference": "olievans123/nano7-untethered games/sm64 and games/mk64",
        "oracle": "ROM hash (USA SM64 sha1, USA MK64 md5). No Unicorn.",
        "slot": "B",
        "you_supply": "USA .z64 (not in git)",
        "command": MARIO_CMD,
    },
    {
        "id": "decomp",
        "name": "decomp + oracle (2D iOS)",
        "reference": "olievans123/nano-flappybird and nano-doodlejump",
        "oracle": "tools/original_oracle.py (Unicorn) + tests/compare_*.py",
        "slot": "A or B (small GL + /Apps/Data)",
        "you_supply": "original iOS .ipa or unpacked .app (not in git); macOS sips",
        "command": "./start apps add 'Flappy Bird' --payload /path/to/Flap.ipa",
    },
    {
        "id": "recomp",
        "name": "recomp + oracle (3D iOS engine)",
        "reference": "olievans123/nano-templerun tools/recomp.py",
        "oracle": "original_oracle.py + tests/compare_engine.py (word-for-word heap)",
        "slot": "B (large GL)",
        "you_supply": "original iOS 1.0 .ipa or unpacked .app (not in git)",
        "command": "./start apps add 'Temple Run' --ipa /path/to/TempleRun.ipa",
    },
    {
        "id": "first-party",
        "name": "first-party RAW_SURFACE",
        "reference": "nanoapps/apps/lap and nanoapps/sdk/template",
        "oracle": "Probe heap on device; ./start port budget after build",
        "slot": "A",
        "you_supply": "nothing (MIT host + MIT SDK)",
        "command": "./start sdk new NAME --title Title",
    },
)

# Driver reboot rules from olievans123/nano-templerun analysis/nano-gl-notes.md
# (hardware, 2026-10). Copied as a checklist, not as new exploit material.
GL_RULES = (
    ("Clip on the CPU, slightly inside the view", "reboot when a large triangle crosses the screen edge"),
    ("No triangle under 2 square pixels, none thinner than half a pixel", "reboot (slivers too)"),
    ("About 1,300 triangles a frame (never more than ~1,500 here)", "reboot; limit follows free memory"),
    ("No texture level above 512x512 in a sheet", "reboot on the first draw with a third 1024 sheet"),
    ("First-draw each large texture alone, in a light frame", "reboot when two large sheets first-draw together"),
    ("No large uncompressed textures; PVRTC is fine", "reboot"),
    ("No large vertex buffer objects; client arrays are fine", "reboot at the first draw"),
    ("A single-level texture must not have a mipmap filter", "draws white"),
    ("Keep ~3 MB OS heap for the driver (Probe: 8.7 / 8.0 MB on 3-app pack)", "reboot when the driver cannot alloc"),
    ("Write a second small file after a log you want to keep", "newest file is lost if a reboot follows quickly"),
)


STEPS = (
    "Pick a family (./start port families). Do not mix them.",
    "Keep original files local and gitignored. Nothing from Nintendo or the original iOS app enters git.",
    "Host-playable build first (Mac). Do not copy an .hbapp to the nano yet.",
    "Oracle: Unicorn (or ROM hash) must match the translation before a device copy.",
    "Assets via convert_assets.py onto /Apps/Data/<Name>. Skip wav/caf. Never commit the output.",
    "Nano shell: NanoApps kind 2 for GL, kind 1 RAW_SURFACE for first-party. No firmware patch.",
    "./start port budget on the .hbapp. Over 576 KB GL -> slot B. Over ~0.5 MB may fail to launch on slot A.",
    "GL: obey ./start port rules. CPU clip, client arrays, triangle budget, PVRTC, first-draw isolation.",
    "Stage locally. Copy in Disk Mode with ./start apps add NAME (one extra, pack cap 8; nogrow 16 after apps cap enable).",
    "Credit the author in CREDITS.md. iOS-port trees state no OSS licence: do not redistribute derived files.",
    "If it loops: ./start apps lasttrace && ./start apps restore-tested. Do not retry a flash.",
)


def print_status() -> int:
    print("nano7kit porting pipeline (olievans123 recomp/decomp + oracle)")
    print("Host-only until apps add. No NAND. No original game files in git.")
    print()
    print("steps")
    for i, step in enumerate(STEPS, 1):
        print(f"  {i:2}. {step}")
    print()
    print("next: ./start port families")
    print("      ./start port rules")
    print("      ./start sdk new NAME --title Title   (first-party)")
    print("      " + MARIO_CMD)
    print("      ./start port candidates")
    print("docs: docs/GAMES.md")
    return 0


def print_families() -> int:
    print("port families")
    print()
    for fam in FAMILIES:
        print(f"  {fam['id']:<12} {fam['name']}")
        print(f"               ref     {fam['reference']}")
        print(f"               oracle  {fam['oracle']}")
        print(f"               slot    {fam['slot']}")
        print(f"               supply  {fam['you_supply']}")
        print(f"               {fam['command']}")
        print()
    print("A new game port is not scaffolded here (that would invite vendoring")
    print("original files). Use the matching olievans123 repo as the reference,")
    print("keep payloads local, then ./start apps add when the .hbapp is ready.")
    return 0


def print_rules() -> int:
    print("nano 7G GL driver rules (from nano-templerun hardware notes)")
    print("Helvetica / 39A10023. Breaking a rule reboots the iPod.")
    print()
    for rule, otherwise in GL_RULES:
        print(f"  - {rule}")
        print(f"      else: {otherwise}")
    print()
    print(f"Slot A GL staging: {SLOT_A_GL_STAGING} bytes (576 KB).")
    print(f"Launch soft cap:   {LAUNCH_SOFT_CAP} bytes (~0.5 MB).")
    print(f"Pack cap:          {GROW_CACHE_LIMIT} (grow_cache); nogrow 16 after ./start apps cap enable.")
    print("Large GL (Mario, Kart, Temple Run) uses slot B.")
    return 0


def print_oracle() -> int:
    print("oracle (how olievans123 proves a translation)")
    print()
    print("  1. Original ARM executable stays local (gitignored).")
    print("  2. tools/original_oracle.py runs that code in Unicorn.")
    print("     C library / libm / GL imports are stubbed; Objective-C is not run.")
    print("  3. tests/compare_*.py give both sides the same seed and the same")
    print("     scripted taps/swipes, then compare engine state (and often the")
    print("     drawn quads or the whole heap). Expect 0 differing words.")
    print("  4. Temple Run also cross-checks the C translation with")
    print("     -ffp-contract=off -fno-strict-aliasing -fwrapv.")
    print("  5. Flappy/Doodle also hash Mac vs qemu-arm of the translated C.")
    print()
    print("This toolkit does not vendor Unicorn tests that need the original")
    print("binary. After ./start ports fetch the tests live in build/ports/.")
    print("Run them only on your machine, with your copy of the game.")
    return 0


def budget_for(
    *,
    path: Optional[Path] = None,
    name: Optional[str] = None,
) -> dict:
    reset_catalog_cache()
    if path is None and not name:
        raise Refused("pass a .hbapp path or --name")
    size = 0
    kind = 1
    label = name or (str(path) if path else "")
    large = False
    if name:
        row = next((r for r in catalog() if r["name"] == name), None)
        if row is None:
            raise Refused(f"unknown app {name}")
        size = int(row.get("size") or 0)
        kind = int(row.get("kind") or 1)
        large = bool(row.get("large_gl") or is_large_gl(name))
        label = name
        if not size and path is None and row.get("present"):
            p = const.artifact(row["executable"])
            if p.is_file():
                size = p.stat().st_size
        elif not size and row.get("present") is False:
            pass
    if path is not None:
        src = Path(path).expanduser().resolve()
        if not src.is_file():
            raise Refused(f"hbapp missing: {src}")
        size = src.stat().st_size
        label = label or src.name
    slot = "B" if (large or (kind == 2 and size > SLOT_A_GL_STAGING)) else "A"
    launch_ok = size <= LAUNCH_SOFT_CAP or slot == "B"
    gl_ok = size <= SLOT_A_GL_STAGING or slot == "B"
    return {
        "name": label,
        "bytes": size,
        "kind": kind,
        "slot": slot,
        "launch_soft_cap": LAUNCH_SOFT_CAP,
        "slot_a_gl_staging": SLOT_A_GL_STAGING,
        "pack_cap": GROW_CACHE_LIMIT,
        "fits_slot_a_gl": gl_ok,
        "fits_launch_soft_cap": launch_ok,
        "advice": (
            "slot B (AllApps-B.pack, unload resident)"
            if slot == "B"
            else (
                "slot A OK"
                if launch_ok
                else "over ~0.5 MB: may fail to launch on slot A"
            )
        ),
    }


def print_budget(*, path: Optional[str] = None, name: Optional[str] = None) -> int:
    info = budget_for(
        path=Path(path) if path else None,
        name=name,
    )
    print(f"budget  {info['name']}")
    print(f"  size           {info['bytes']} bytes")
    print(f"  kind           {info['kind']}")
    print(f"  slot           {info['slot']}  ({info['advice']})")
    print(f"  launch cap     {info['launch_soft_cap']} (soft)")
    print(f"  slot A GL      {info['slot_a_gl_staging']}")
    print(f"  pack cap       {info['pack_cap']}")
    return 0


# Host-only ranking. Original game files stay on the operator's machine.
CANDIDATES = (
    {
        "rank": 1,
        "name": "Super Mario 64 (native)",
        "class": "Mario-like",
        "feasibility": "ready",
        "why": "Already in this tree. Slot B. USA .z64 sha1 9bef1128.",
        "you_supply": "USA Super Mario 64 .z64",
        "command": MARIO_CMD,
    },
    {
        "rank": 2,
        "name": "Mario Kart 64 (native)",
        "class": "Mario-like",
        "feasibility": "ready",
        "why": "Already in this tree. Slot B. USA .z64 md5 3a67d998.",
        "you_supply": "USA Mario Kart 64 .z64",
        "command": "./start apps add 'Mario Kart' --rom /path/to/mk64-usa.z64",
    },
    {
        "rank": 3,
        "name": "Temple Run 1.0",
        "class": "endless-run",
        "feasibility": "ready pipeline",
        "why": "olievans123/nano-templerun. cryptid 0 fat armv6+armv7, 2.94 MB.",
        "you_supply": "Temple Run 1.0 .ipa (com.imangi.templerun)",
        "command": "./start apps add 'Temple Run' --ipa /path/to/TempleRun.ipa",
    },
    {
        "rank": 4,
        "name": "Flappy Bird / Doodle Jump",
        "class": "2D iOS",
        "feasibility": "ready pipeline",
        "why": "decomp + oracle. Small GL. macOS sips.",
        "you_supply": "original iOS .ipa",
        "command": "./start apps add 'Flappy Bird' --payload /path/to/Flap.ipa",
    },
    {
        "rank": 5,
        "name": "Fruit Ninja 1.0",
        "class": "swipe / slice",
        "feasibility": "slice shell + streamed catalog (Mortar recomp not verified)",
        "why": (
            "com.Halfbrick.Fruit, MinOS 2.2.1, thin ARMv6, cryptid 0, "
            "Fruit 2,261,728 bytes, 388 files / 13.8 MB unpacked. Mortar "
            "engine, EAGLView touchesBegan/Moved/Ended, SlashEntity swipe. "
            "No Cocos, no FMOD, no UIKit dylib. Lighter than PvZ. Slot B "
            "(TEXT 1.28 MB). Stub OpenFeint/Facebook. Stream fileArcive.bin "
            "(8.6 MB) rather than loading it whole (Probe 8.7/8.0 MB)."
        ),
        "you_supply": "Fruit Ninja 1.0 .ipa (never commit)",
        "command": "./start apps add 'Fruit Ninja' --payload /path/to/FruitNinja.ipa",
    },
    {
        "rank": 6,
        "name": "Plants vs. Zombies iOS 1.0",
        "class": "tower defense",
        "feasibility": "medium-hard recomp",
        "why": (
            "com.popcap.PvZ CFBundleVersion 524, MinOS 2.2.1, thin ARMv6 "
            "cpusubtype 6, cryptid 0, PvZ 2,509,984 bytes, TEXT 2,183,168, "
            "507 files / 38 MB. SexyAppFramework + TodLib + FMOD via "
            "AudioQueue. UIKit + MediaPlayer. Slot B (TEXT > 576 KB). "
            "All-PNG RGBA 33 MB vs Probe 8.7/8.0 MB: stream 512-cap tiles. "
            "Skip credits.mp4 and 173 CAF. Oracle possible. No apps add. "
            "sha256 ab6a66cd (never commit)."
        ),
        "you_supply": "PvZ iOS 1.0 .ipa (never commit)",
        "command": "./start port pvz --payload /path/to/pvz.ipa",
    },
    {
        "rank": 7,
        "name": "Blocks (already on Home) / first-party voxel",
        "class": "Minecraft-like",
        "feasibility": "hardware-safe / sdk",
        "why": "Blocks is the realistic Minecraft-like on this nano. MCPE is not.",
        "you_supply": "nothing for Blocks; ./start sdk new for a new voxel toy",
        "command": "./start sdk new voxel --title Voxel",
    },
)


def print_candidates() -> int:
    print("nano7kit port candidates (host-only ranking)")
    print("Original iOS apps and Nintendo ROMs stay local. Never commit them.")
    print("Fruit Ninja has a RAW slice-shell apps add path; PvZ is research only (./start port pvz).")
    print()
    for row in CANDIDATES:
        print(f"  {row['rank']}. {row['name']}  [{row['class']}]  {row['feasibility']}")
        print(f"     {row['why']}")
        print(f"     you supply  {row['you_supply']}")
        print(f"     {row['command']}")
        print()
    return 0


def refuse_new_game_port(family: str) -> None:
    raise Refused(
        "game-port scaffolding is not offered (original files must stay "
        f"off git). Family {family}: clone the matching olievans123 repo "
        "with ./start ports fetch, keep the payload local, follow "
        "./start port families, then apps add. First-party: ./start sdk new NAME."
    )
