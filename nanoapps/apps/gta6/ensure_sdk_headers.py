#!/usr/bin/env python3
"""Put NanoApps SDK headers on disk for an ARM compile of gta6.c.

Prefers a fetched nanoapps/sdk tree. If hb_sdk.h is missing, downloads the
pinned lock.json files from GitHub and applies upstream/nano7-untethered.patch
so the headers match a real device build.
Prints the include directory on stdout.
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

APP = Path(__file__).resolve().parent
ROOT = APP.parents[2]
LOCK = ROOT / "upstream" / "lock.json"
PATCH = ROOT / "upstream" / "nano7-untethered.patch"
NEED = (
    "nanoapps/sdk/hb_sdk.h",
    "nanoapps/sdk/hb_raw_surface.h",
    "nanoapps/sdk/hb_heap.h",
    "nanoapps/sdk/hb_shared.h",
    "nanoapps/sdk/hb_surface_input.h",
    "nanoapps/sdk/freestanding/string.h",
    "nanoapps/sdk/freestanding/stdlib.h",
)


def real_sdk() -> Path | None:
    sdk = ROOT / "nanoapps" / "sdk"
    if (sdk / "hb_sdk.h").is_file() and (sdk / "hb_raw_surface.h").is_file():
        return sdk
    return None


def fetch_into(dest: Path) -> Path:
    lock = json.loads(LOCK.read_text())
    spec = lock["nano7-untethered"]
    commit = spec["commit"]
    url = spec["url"]
    if url.endswith(".git"):
        url = url[:-4]
    if "github.com" not in url:
        raise SystemExit(f"unexpected upstream url {spec['url']}")
    parts = url.split("github.com/")[-1]
    raw = f"https://raw.githubusercontent.com/{parts}/{commit}/"
    dest.mkdir(parents=True, exist_ok=True)
    for rel in NEED:
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(raw + rel, timeout=60) as r:
            out.write_bytes(r.read())
    if PATCH.is_file():
        r = subprocess.run(
            [
                "git",
                "apply",
                "--whitespace=nowarn",
                f"--directory={dest}",
                "--include=nanoapps/sdk/hb_sdk.h",
                "--include=nanoapps/sdk/hb_shared.h",
                "--include=nanoapps/sdk/hb_heap.h",
                "--include=nanoapps/sdk/hb_raw_surface.h",
                str(PATCH),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            sys.stderr.write(r.stderr)
            # Headers from the pinned commit are enough to catch type conflicts.
    sdk = dest / "nanoapps" / "sdk"
    if not (sdk / "hb_sdk.h").is_file():
        raise SystemExit("failed to materialize hb_sdk.h")
    blob = (sdk / "hb_sdk.h").read_text(errors="replace")
    if "bool hb_audio_play_wav" not in blob:
        raise SystemExit("hb_sdk.h is missing bool hb_audio_play_wav")
    shared = (sdk / "hb_shared.h").read_text(errors="replace")
    if "prepare_unload" not in shared:
        shared = shared.replace(
            "    void (*trace)(const char *tag, uint32_t v1, uint32_t v2);\n} hb_shared_t;",
            "    void (*trace)(const char *tag, uint32_t v1, uint32_t v2);\n"
            "    int (*prepare_unload)(void);\n} hb_shared_t;",
        )
        (sdk / "hb_shared.h").write_text(shared)
    return sdk


def main() -> int:
    sdk = real_sdk()
    if sdk is None:
        sdk = fetch_into(APP / "build" / "sdk-src")
    print(sdk)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
