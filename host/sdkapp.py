"""First-party NanoApps that run well on Helvetica / slot A.

Default kind is RAW_SURFACE (Paint/Probe/Lap), not LVGL. LVGL adds about
150 KB and is optional. GL (kind 2) must follow ./start port rules and
the 576 KB slot A staging limit.

./start sdk new copies nanoapps/sdk/template (tokens KIT_*).
./start sdk build compiles one app and stages it under artifacts/extra
so Disk Mode ./start apps add TITLE works.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

from .app_catalog import catalog, reset_catalog_cache
from .app_stage import stage_named
from .compat import Refused, root, which
from .porting import budget_for, print_budget


RESERVED = {
    "silver_resident",
    "resident_selector",
    "resident_stub",
    "untether_bootstrap",
    "heap_diagnostic",
    "deferred_untether",
    "kit_folder",
}

TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{0,20}$")


def template_dir() -> Path:
    return root() / "nanoapps" / "sdk" / "template"


def apps_dir() -> Path:
    return root() / "nanoapps" / "apps"


def _ascii_banner(title: str) -> str:
    raw = "".join(ch if ch.isascii() and (ch.isalnum() or ch == " ") else " " for ch in title)
    raw = " ".join(raw.split()).upper()
    if not raw:
        raw = "APP"
    return raw[:10]


def _tag(folder: str) -> str:
    t = "".join(ch for ch in folder.upper() if ch.isalnum())
    return (t[:8] or "APPINIT").ljust(8)[:8]


def new_app(
    folder: str,
    *,
    title: Optional[str] = None,
    kind: str = "raw",
    color: str = "#e8c547",
    glyph: str = "stopwatch",
) -> dict:
    name = (folder or "").strip().lower()
    if not TOKEN_RE.match(name) or name in RESERVED:
        raise Refused(
            "app folder must be lowercase letters/digits/underscore, "
            "start with a letter, max 21 chars, and not a loader name"
        )
    if kind != "raw":
        raise Refused(
            "first-party scaffold is RAW_SURFACE only (the well-performing "
            "path). For LVGL clone clock with nanoapps/start new. For GL "
            "read ./start port rules first. "
            f"./start sdk new {name} --title Title"
        )
    dest = apps_dir() / name
    if dest.exists():
        raise Refused(f"nanoapps/apps/{name} already exists")
    src = template_dir()
    if not (src / "KIT_FOLDER.c").is_file():
        raise Refused(f"template missing: {src}")
    display = (title or name.replace("_", " ").title()).strip()
    if not display or len(display) > 24:
        raise Refused("title must be 1..24 characters")
    data = "/Apps/Data/" + "".join(ch for ch in display if ch.isalnum() or ch in " -_")
    data = data.replace(" ", "")
    if data == "/Apps/Data/":
        data = f"/Apps/Data/{name.title()}"
    bundle = f"org.nano7kit.{name}"
    banner = _ascii_banner(display)
    tag = _tag(name)
    dest.mkdir(parents=True)
    mapping = {
        "KIT_FOLDER": name,
        "KIT_TITLE": display,
        "KIT_BUNDLE": bundle,
        "KIT_DATA": data,
        "KIT_COLOR": color,
        "KIT_GLYPH": glyph,
        "KIT_BANNER": banner,
        "KIT_TAG": tag,
    }

    def subst(text: str) -> str:
        for key, val in mapping.items():
            text = text.replace(key, val)
        return text

    (dest / f"{name}.c").write_text(subst((src / "KIT_FOLDER.c").read_text(encoding="utf-8")))
    (dest / "Makefile").write_text(subst((src / "Makefile").read_text(encoding="utf-8")))
    (dest / "Info.plist").write_text(subst((src / "Info.plist").read_text(encoding="utf-8")))
    reset_catalog_cache()
    print(f"created nanoapps/apps/{name}  (RAW_SURFACE, {display}, {bundle})")
    print(f"  data   {data}")
    print("next (host only):")
    print(f"  ./start sdk build {name}")
    print("then Disk Mode (Do not disconnect), one extra per reboot:")
    print(f"  ./start apps add {display}")
    return {"folder": name, "title": display, "bundle": bundle, "data": data}


def _python_with_elftools() -> str:
    """The packer needs pyelftools: prefer build/venv (where ./start deps puts it)."""
    import importlib.util
    import sys

    venv_py = root() / "build" / "venv" / "bin" / "python"
    if venv_py.is_file():
        r = subprocess.run([str(venv_py), "-c", "import elftools"], capture_output=True)
        if r.returncode == 0:
            return str(venv_py)
    if importlib.util.find_spec("elftools") is not None:
        return sys.executable
    return str(venv_py) if venv_py.is_file() else "python3"


def build_app(folder: str, *, quiet: bool = False) -> dict:
    name = (folder or "").strip().lower()
    dest = apps_dir() / name
    if not (dest / "Makefile").is_file():
        raise Refused(f"nanoapps/apps/{name} is missing (./start sdk new {name})")
    if not which("arm-none-eabi-gcc"):
        raise Refused("arm-none-eabi-gcc is missing (run ./start deps)")
    log = root() / "build" / f"sdk-{name}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    if not quiet:
        print(f"==> sdk build {name} (host only, no NAND)")
    with log.open("w", encoding="utf-8") as fh:
        rc = subprocess.call(
            ["make", "-C", str(dest), f"PYTHON={_python_with_elftools()}"],
            stdout=fh,
            stderr=subprocess.STDOUT,
        )
    hbapp = dest / "build" / f"{name}.hbapp"
    if rc != 0 or not hbapp.is_file():
        tail = log.read_text(encoding="utf-8", errors="replace")[-2000:]
        raise Refused(f"sdk build {name} failed; see {log}\n{tail}")
    reset_catalog_cache()
    if quiet:
        import contextlib
        import io

        with contextlib.redirect_stdout(io.StringIO()):
            staged = stage_named(name)
        return {"folder": name, "hbapp": str(hbapp), "staged": staged}
    staged = stage_named(name)
    info = budget_for(path=hbapp, name=staged.get("title"))
    print(f"  hbapp  {hbapp}  {hbapp.stat().st_size} bytes")
    print(f"  slot   {info['slot']}  {info['advice']}")
    print("Disk Mode, one extra per reboot:")
    title = staged.get("title") or name
    quoted = f"'{title}'" if " " in str(title) else title
    print(f"  ./start apps add {quoted}")
    return {"folder": name, "hbapp": str(hbapp), "staged": staged, "budget": info}


def build_from_source(title: str, folder: str) -> dict:
    """Build one NanoApps app from the fetched, pinned source on this computer.

    Binaries derived from the upstream SDK are not shipped in this repo; the
    first `apps add` of such an app compiles it here (a few seconds).
    """
    missing = [t for t in ("arm-none-eabi-gcc", "make", "git") if not which(t)]
    if missing:
        raise Refused(
            f"{title} is built from source on this computer and needs {', '.join(missing)}: "
            "run ./start deps once (./install does this for you)"
        )
    import importlib.util

    venv_py = root() / "build" / "venv" / "bin" / "python"
    if importlib.util.find_spec("elftools") is None and not (
        venv_py.is_file()
        and subprocess.run([str(venv_py), "-c", "import elftools"], capture_output=True).returncode == 0
    ):
        import contextlib
        import io

        from .deps import _pip

        print("Installing pyelftools into build/venv (one time)...", flush=True)
        with contextlib.redirect_stdout(io.StringIO()):
            if not _pip("pyelftools"):
                raise Refused("could not install pyelftools (run ./start deps)")
    lvgl = root() / "nanoapps" / "lvgl"
    if not (lvgl / "src").is_dir():
        from .const import LVGL_PIN, LVGL_REPO

        print("Fetching LVGL (one time)...", flush=True)
        lvgl.mkdir(parents=True, exist_ok=True)
        ok = True
        for cmd in (["git", "init", "-q"], ["git", "remote", "add", "origin", LVGL_REPO],
                    ["git", "fetch", "-q", "--depth", "1", "origin", LVGL_PIN],
                    ["git", "-c", "advice.detachedHead=false", "checkout", "-q", "-f", LVGL_PIN]):
            r = subprocess.run(cmd, cwd=lvgl, capture_output=True, text=True)
            if r.returncode != 0 and cmd[1] != "remote":
                ok = False
                break
        if not ok or not (lvgl / "src").is_dir():
            raise Refused("could not fetch LVGL (check the network, then run ./start deps)")
    print(f"Building {title} from source on this computer...", flush=True)
    return build_app(folder, quiet=True)


def print_status() -> int:
    reset_catalog_cache()
    print("nano7kit first-party SDK (RAW_SURFACE template)")
    print("Helvetica silver_resident / 39A10023. Slot A. Pack cap 8.")
    print()
    print("  template  nanoapps/sdk/template")
    print("  example   nanoapps/apps/lap  (stopwatch; heap on screen)")
    print("  example   nanoapps/apps/pockethello  (JS Hello HAL; ./start pocketjs hello)")
    print()
    print("exact:")
    print("  ./start sdk new NAME --title Title")
    print("  ./start sdk build NAME")
    print("  ./start apps add Title          # Disk Mode, Do not disconnect")
    print()
    gcc = which("arm-none-eabi-gcc") or "missing (./start deps)"
    print(f"  arm-none-eabi-gcc  {gcc}")
    first = [
        r
        for r in catalog()
        if str(r.get("bundle_id") or "").startswith("org.nano7kit.")
    ]
    if not first:
        print("  first-party apps  none staged yet")
        return 0
    print("  first-party apps")
    for row in first:
        flag = "staged" if row.get("present") else "source only"
        print(f"    {row['name']:<16} {flag:<12} {row.get('size') or 0} bytes")
    return 0
