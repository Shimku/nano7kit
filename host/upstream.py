# SPDX-License-Identifier: MIT
"""Third-party files we build on, fetched instead of committed.

nano7-untethered (olievans123) carries the loader files, the firmware builder
(ipod_sun_untethered), the NanoApps framework and the Mario ports. None of it
is in this repository. `fetch()` downloads it from GitHub at the pinned commit,
applies our changes (upstream/nano7-untethered.patch), checks every file
against upstream/lock.json and only then copies the files into place.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from .compat import Refused

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "upstream" / "lock.json"
EXCLUDE_BEGIN = "# nano7kit fetched files (begin)"
EXCLUDE_END = "# nano7kit fetched files (end)"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_lock(lock: Path = LOCK) -> Dict[str, dict]:
    return json.loads(lock.read_text())


def cache_root() -> Path:
    env = os.environ.get("NANO7KIT_CACHE")
    if env:
        return Path(env)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "nano7kit"


def _git(*args: str, cwd: Optional[Path] = None) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise Refused(f"git {' '.join(args)} failed: {r.stderr.strip()[-400:]}")
    return r.stdout.strip()


def _checkout(name: str, spec: dict) -> Path:
    """Shallow checkout of exactly spec['commit']. Git checks the content against the commit id."""
    dest = cache_root() / "upstream" / f"{name}-{spec['commit'][:12]}"
    if (dest / ".git").is_dir():
        try:
            if _git("rev-parse", "HEAD", cwd=dest) == spec["commit"]:
                return dest
        except Refused:
            pass
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", cwd=dest)
    _git("fetch", "-q", "--depth", "1", spec["url"], spec["commit"], cwd=dest)
    _git("-c", "advice.detachedHead=false", "checkout", "-q", "--detach", "FETCH_HEAD", cwd=dest)
    head = _git("rev-parse", "HEAD", cwd=dest)
    if head != spec["commit"]:
        raise Refused(f"{name}: got commit {head}, expected {spec['commit']}")
    return dest


def missing_or_changed(lock: Optional[Dict[str, dict]] = None, root: Path = ROOT) -> List[str]:
    lock = lock or load_lock()
    bad = []
    for spec in lock.values():
        for rel, digest in spec["files"].items():
            p = root / rel
            if not p.is_file() or _sha256(p) != digest:
                bad.append(rel)
    return bad


def is_ready(root: Path = ROOT) -> bool:
    """Cheap check: every fetched file exists (hashes are checked by fetch and `./start fetch --check`)."""
    try:
        lock = load_lock()
    except FileNotFoundError:
        return True
    return all((root / rel).is_file() for spec in lock.values() for rel in spec["files"])


def _stage(name: str, spec: dict, src: Path, stage: Path, root: Path) -> None:
    for rel in spec["files"]:
        s = src / rel
        if s.is_file():
            d = stage / rel
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)
    patch = spec.get("patch")
    if patch:
        r = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", str(root / patch)],
            cwd=stage, capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise Refused(f"{name}: our patch did not apply: {r.stderr.strip()[-400:]}")
    bad = []
    for rel, digest in spec["files"].items():
        p = stage / rel
        if not p.is_file() or _sha256(p) != digest:
            bad.append(rel)
    if bad:
        raise Refused(f"{name}: {len(bad)} file(s) do not match upstream/lock.json, e.g. {bad[0]}. Nothing was copied.")


def _write_exclude(paths: List[str], root: Path) -> None:
    if not (root / ".git").exists():
        return
    r = subprocess.run(["git", "rev-parse", "--git-path", "info/exclude"], cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        return
    ex = Path(r.stdout.strip())
    if not ex.is_absolute():
        ex = root / ex
    ex.parent.mkdir(parents=True, exist_ok=True)
    old = ex.read_text() if ex.exists() else ""
    if EXCLUDE_BEGIN in old:
        head, _, rest = old.partition(EXCLUDE_BEGIN)
        _, _, tail = rest.partition(EXCLUDE_END)
        old = head.rstrip("\n") + ("\n" if head.strip() else "") + tail.lstrip("\n")
    block = [EXCLUDE_BEGIN] + ["/" + p for p in sorted(paths)] + [EXCLUDE_END]
    ex.write_text(old.rstrip("\n") + ("\n" if old.strip() else "") + "\n".join(block) + "\n")


def fetch(*, quiet: bool = False, root: Path = ROOT, lock_path: Path = LOCK) -> int:
    lock = load_lock(lock_path)
    say = (lambda *_: None) if quiet else print
    copied: List[str] = []
    for name, spec in lock.items():
        say(f"Fetching {name} ({spec['url']} @ {spec['commit'][:12]})")
        src = _checkout(name, spec)
        with tempfile.TemporaryDirectory(prefix="nano7kit-stage-") as tmp:
            stage = Path(tmp)
            _stage(name, spec, src, stage, root)
            for rel in spec["files"]:
                d = root / rel
                d.parent.mkdir(parents=True, exist_ok=True)
                if d.exists() or d.is_symlink():
                    d.unlink()
                shutil.copy2(stage / rel, d)
                copied.append(rel)
        say(f"  {len(spec['files'])} files checked against upstream/lock.json")
    _write_exclude(copied, root)
    return 0


def ensure(*, quiet: bool = True) -> None:
    """Fetch once if anything is missing. Offline and not fetched yet: say so plainly."""
    if is_ready():
        return
    print("First run: downloading third-party files (nano7-untethered) from GitHub...")
    fetch(quiet=quiet)
