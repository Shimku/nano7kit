"""Pinned hashes, USB IDs, and disk-size allow-list.

The firmware image hash is the hardware-tested default and must not change
unless upstream re-tests. The Linux amd64 wInd3x binary is the only flasher
that has been used on hardware. Host-built flashers (macOS, Linux arm64) use
the same source and patch, but are marked untested.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from .compat import root

# USB IDs from wInd3x pkg/devices (Apple VID 0x05ac).
# 2012 Initial 7G and 2015 Rev A (Mid-2015, MKN52) share DFU 0x1234 and disk 0x1267.
# The only USB discriminator in wInd3x is WTF: 0x1249 (2012) vs 0x124A (2015).
APPLE_VID = 0x05AC
NANO7_DFU_PID = 0x1234
NANO7_WTF_PID = 0x1249
NANO7_DISK_PID = 0x1267
NANO7_LATE_WTF_PID = 0x124A
# Exact wInd3x slog lines. The 2012 string is not a substring of the 2015 string.
PREPARE_DISKMODE_2012 = 'Found device device="Nano 7G" interface=diskmode'
PREPARE_DISKMODE_2015 = 'Found device device="Nano 7G (Mid-2015)" interface=diskmode'
# Classic Apple DFU (some tools report this). Also accepted when waiting.
APPLE_DFU_GENERIC_PID = 0x1227
# NanoApps resident trace ring (vendor SCSI peek target).
TRACE_RING = 0x09120000
# ipod_sun harness: sg_raw c6 96 04 00 00 00 00 (VROM gates / SCSI handler).
# Not a new exploit. Stock 1.1.2 rejects this CDB.
C6_HARNESS_OP = 0x04
C6_PEEK_OP = 0x02

STOCK_IPSW_URL = (
    "http://appldnld.apple.com/ipod/sbml/osx/bundles/"
    "031-59796-20160525-8E6A5D46-21FF-11E6-89D1-C5D3662719FC/"
    "iPod_1.1.2_39A10023.ipsw"
)
STOCK_IPSW_SHA256 = "960d570aa073f278f21b2c99d1aa3601ef136539353492b71cc71f462870a252"
STOCK_IPSW_NAME = "iPod_1.1.2_39A10023.ipsw"

# N31.bootloader.rb3 inside that IPSW (2015 Rev A / Mid-2015). Same bytes
# ipod_sun and BrutalistOS ship. restore --full used to fetch 37A40005 instead.
BOOTLOADER_NAME = "N31.bootloader.rb3"
BOOTLOADER_39A10023_SHA256 = (
    "7d921c6d97305e3ef944c1f6212cab2adcbb4a5d4c2ff45faeafc438aa3b1548"
)
# 2012 iPod_1.0.4_37A40005.ipsw bootloader. Prefix only; Apple CDN did not
# give us a full hash from this VM. Identify 2012 by this prefix and/or URL.
BOOTLOADER_37A40005_SHA256_PREFIX = "c5abb67f"
NANO7LATE_UPDATER_FAMILY_ID = 39
NANO7_UPDATER_FAMILY_ID = 37

FIRMWARE_MSE_SHA256 = "b37648c194f00207deff6b1b6696d938aaed70b19e52c78dba63bdd1295929ad"
RESCUE_MSE_SHA256 = "39579dbaabeb2d6611426fb183ea04307da7040a28155e165ed21c5c96b33ed3"
FIRMWARE_MSE_SIZE = 193114112

TESTED_WIND3X_SHA256 = "68d8018ecaa80b8766d0961527ba3c06fd2f5d5e7511378c50ce974b18e7b7e3"
WIND3X_UPSTREAM = "https://github.com/freemyipod/wInd3x.git"
WIND3X_COMMIT = "7c7c4d83c8b923f29e7559585b2cdbbf4e028045"

AB_SHA256 = "9b4c82fd9cc2b94723d19bf89404b0d5dbc1ea9d73c880a097ef3c026e95f6d2"
SLOT_A_SHA256 = "0f81cebdda2c12f33ff83c363cf6ccdbdeaa2b38fae4ae4efaa7c47bc5d86803"
SLOT_B_SHA256 = "b2e284ca0cff87bb92e5e22bac3bd75a2c7c7fdb7521b0617c0ea407ac3cd3ac"
NA_B_SHA256 = "aad1664b06ab055acd67eef854a6107fe6da6874c059844c783f0d0706577866"  # silver_resident_na (485bef2 latch + every queue's newest sample per touch event)
# Earlier silver_resident_na builds. install-na upgrades these in place and keeps
# the original resident-B backup; apps add keeps them as known unload residents.
NA_B_OLD_SHA256S = frozenset({
    "3426a7a80a729cce28c49fd175ef5efd9e2ef9f13f7a8c3717704e5e859458cf",  # read /Library/AllApps.pack only
    "641a0ac7d43c389cefa42235430b1141a275883b0c12a9f73a22b4ba9eb808ef",  # fragmented the heap on Home
    "b9101b96a2b0785a856418c6cb564dfa876cbc2d9ca913071b91f8f98acc8b2c",  # rebooted on Probe after a GL app
    "4ee7737084fd178e71148026ae30272394d396c21014532ca8e543e426463282",  # latched the oldest queued touch sample (rc5, 1.1 on 3 Nov)
    "9f1c8e8a0f9a8f800509935405cf42299d2333131d633bac64cc3d79565a5a4b",  # no touch snapshot for apps (485bef2, 3 Nov)
})
NA_B_ALL_SHA256S = frozenset({NA_B_SHA256}) | NA_B_OLD_SHA256S
# Launcher code (HRL1, without the slot trailer) of earlier nano7kit Launchers that
# ./install --nanoapps may find in /Library/resident.hbapp; it replaces them.
NA_B_OLD_HRL_SHA256S = frozenset({
    "db3f0612aef947028bae4bb1563dca2d59dd210f8417bf982a3e7f9a6733b7e9",  # 4ee77370..., slot e0e6f695...
    "5a48d8da7fb9739b8d39f02d319324889d3ed4487e83929d416c8e1ac3e47cbe",  # 9f1c8e8a... (485bef2), slot 73768557...
})
RESIDENT_SHA256 = "3ad91228e6388e5e3972c521f750ef232d83fdef6843fcd1a0cb95ea314590ad"

# The only disk size that has been written on hardware (Finder / 4K APM map).
TESTED_DISK_SIZE = 15_798_411_264
# Live USB map after wInd3x IPodRepartition on MKN52ZP (MBR 0x0c, hidden 64, 4K).
# Accepted only when the MBR/FAT layout matches. Helvetica restore --firmware
# on this map reached the HOME SCREEN.
IPODREPARTITION_DISK_SIZE = 15_806_623_744

# Allow-list: add a size here only after a successful hardware test.
# Unknown sizes still require --allow-disk-size and --confirm-untested-capacity.
# MKN52ZP Finder Restore backup (2026-10-28) is TESTED_DISK_SIZE.
# MKN52ZP after IPodRepartition + FAT32 + 39A10023 bootloader (2026-10-29)
# enumerates as IPODREPARTITION_DISK_SIZE.
KNOWN_DISK_SIZES: Dict[int, dict] = {
    TESTED_DISK_SIZE: {
        "label": "16 GB nano 7G (hardware-tested silver 2012 unit; MKN52ZP Finder backup matches)",
        "hardware_tested": True,
        "firmware": "1.1.2 build 39A10023",
    },
    IPODREPARTITION_DISK_SIZE: {
        "label": "16 GB nano 7G IPodRepartition 4K FAT32 (MKN52ZP live USB map)",
        "hardware_tested": False,
        "ipodrepartition_4k_fat32": True,
        "firmware": "1.1.2 build 39A10023",
    },
}

ALLAPPS_PACK_SHA256 = "a9e4583073c4a652727d1cca3b989d9961d1ba890c0119d6d5cd31d6593f7d7f"

# Hardware-tested Home pack: Blocks, Game Boy, Probe. Subsets rebuild the pack
# and are untested as a unit; the individual .hbapp / icon hashes stay tested.
TESTED_APPS = {
    "Blocks": {
        "executable": "artifacts/device/Executables/Blocks.hbapp",
        "icon": "artifacts/device/Icons/Blocks.bin",
        "fat_exe": "/Apps/Executables/Blocks.hbapp",
        "fat_icon": "/Apps/Icons/Blocks.bin",
    },
    "Game Boy": {
        "executable": "artifacts/device/Executables/Game Boy.hbapp",
        "icon": "artifacts/device/Icons/Game Boy.bin",
        "fat_exe": "/Apps/Executables/Game Boy.hbapp",
        "fat_icon": "/Apps/Icons/Game Boy.bin",
    },
    "Probe": {
        "executable": "artifacts/device/Executables/Probe.hbapp",
        "icon": "artifacts/device/Icons/Probe.bin",
        "fat_exe": "/Apps/Executables/Probe.hbapp",
        "fat_icon": "/Apps/Icons/Probe.bin",
    },
}

# Loader / A/B files the resident needs. Never removable via ./start apps remove.
STARTUP_PROTECTED = ("resident", "ab", "resident-A", "resident-B")

LOADER_FILES = [
    ("artifacts/device/AllApps.pack", "/Apps/AllApps.pack"),
    ("artifacts/device/Executables/Blocks.hbapp", "/Apps/Executables/Blocks.hbapp"),
    ("artifacts/device/Executables/Game Boy.hbapp", "/Apps/Executables/Game Boy.hbapp"),
    ("artifacts/device/Executables/Probe.hbapp", "/Apps/Executables/Probe.hbapp"),
    ("artifacts/device/Icons/Blocks.bin", "/Apps/Icons/Blocks.bin"),
    ("artifacts/device/Icons/Game Boy.bin", "/Apps/Icons/Game Boy.bin"),
    ("artifacts/device/Icons/Probe.bin", "/Apps/Icons/Probe.bin"),
    ("artifacts/device/Startup/resident.hbapp", "/Apps/Startup/resident.hbapp"),
]

AB_FILES = [
    ("artifacts/device/Startup/ab.hbapp", "/Apps/Startup/ab.hbapp"),
    ("artifacts/device/Startup/resident-A.hbapp", "/Apps/Startup/resident-A.hbapp"),
]

IPOD_THEME_REPO = "https://github.com/nfzerox/ipod_theme.git"
IPOD_THEME_LICENSE = "GPL-3.0"
N7G_THEME_PREVIEWER = "https://nano.zeehondie.net/n7g_theme_previewer/"
POCKETJS_REPO = "https://github.com/pocket-nexus/pocketjs.git"
POCKETJS_LICENSE = "MIT"
POCKETJS_SITE = "https://pocketjs.pocket.nexus"
# PocketJS is fetched at this exact commit at build time and patched with
# pocketjs/nano7g.patch (the upstreamable nano7g target). Not vendored.
POCKETJS_COMMIT = "6ca9f914f6aa0f0c66bcc1a3f0e15c3d22127746"
# Slot A silver_resident GL staging (LV_SURFACE_MAX). Large ports use slot B.
SLOT_A_GL_STAGING = 0x90000
THEME_HELVETICA_FAT = "/Resources/Fonts/Helvetica.ttf"
THEME_SILVERDB_FAT = "/Resources/UI/SilverImagesDB.LE.bin"
# Bytes on the hardware-tested Helvetica image (rsrc FAT). Import/combine
# compare against these so a community IPSW cannot silently replace the CFF.
TESTED_HELVETICA_SIZE = 728932
TESTED_HELVETICA_SHA256 = (
    "0afb62703b4c968fbc94c0babd6bd6da01e2c684e979d588fe8816b68288324b"
)
TESTED_SILVERDB_SIZE = 33317720
TESTED_SILVERDB_SHA256 = (
    "3730ab4ca65199dfb2062ef485b7b87e0ab55ac50ad7daa59fa1a77ceb1047aa"
)

LVGL_REPO = "https://github.com/lvgl/lvgl.git"
LVGL_PIN = "829c7a22191e5dc0aceba71c54c09f42c08e3ac5"
FA_VERSION = "7.2.0"

MK64_MD5 = "3a67d9986f54eb282924fca4cd5f6dff"
SM64_SHA1 = "9bef1128717f958171a4afac3ed78ee2bb4e86ce"

HARDWARE_TESTED_SUMMARY = (
    "one silver 2012 iPod nano 7G, 16 GB, firmware 1.1.2 build 39A10023; "
    "Linux x86-64 install/flash with the bundled wInd3x-linux-amd64 binary"
)

# Stock iPod_1.1.2_39A10023.ipsw is Apple's Rev A / Mid-2015 retail image
# (build 39A10023). Official 2012 Initial firmware stops at 1.0.4 / 37A40005.
# The hardware-tested MSE is that 39A10023 OSOS plus the Helvetica CFF patch.
# ipod_sun still wraps it as a 2012 IPSW (manifest ProductVersion 1.0.4,
# UpdaterFamilyID 37, BuildVersion 37A40005). wInd3x Nano7Late is family 39
# so bootloader fetch is 39A10023, never 37A40005. See docs/HARDWARE-2015.md.


def release_json() -> dict:
    path = root() / "untether" / "RELEASE.json"
    return json.loads(path.read_text())


def sha256sums() -> Dict[str, str]:
    path = root() / "untether" / "SHA256SUMS"
    out: Dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        digest, rel = line.split(None, 1)
        out[rel] = digest
    return out


def artifact(rel: str) -> Path:
    return root() / "untether" / rel
