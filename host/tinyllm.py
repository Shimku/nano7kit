"""Tiny LLM: a 260K-parameter story model as one NanoApps app.

The weights are not in git. They are downloaded at a pinned commit of
karpathy/tinyllamas on Hugging Face (MIT), checked against SHA-256 sums,
and turned into the app's int8 model file by
nanoapps/apps/tinyllm/mkmodel.py. The model file is staged under
untether/artifacts/extra/Data/TinyLLM/ and copied to /Apps/Data/TinyLLM/
by `./start nabase add 'Tiny LLM'`.
"""

from __future__ import annotations

import importlib.util
import shutil
import urllib.request
from pathlib import Path

from . import const
from .compat import Refused, have, root, run, sha256_file

NAME = "Tiny LLM"
FOLDER = "tinyllm"
CMD = "./start nabase add 'Tiny LLM'"
FAT_DATA = "/Apps/Data/TinyLLM"
HOST_DATA = "artifacts/extra/Data/TinyLLM"
LICENCE = "MIT"
HF_REPO = "karpathy/tinyllamas"
HF_COMMIT = "0bd21da7698eaf29a0d7de3992de8a46ef624add"
FILES = {
    "stories260K.bin": "b0a507e7ad0f626624f17112325e66691f9076d622e1d3274d103d00299f2696",
    "tok512.bin": "037cb335abb25d1fa9e8ecae30ed2a3a8ace9302862ebcdc05d51a6bbb10c312",
}
STORY_TOKENS = 256  # RoPE table rows in the model file; matches STORY_TOKENS in tinyllm.c


def url(name: str) -> str:
    return f"https://huggingface.co/{HF_REPO}/resolve/{HF_COMMIT}/stories260K/{name}"


def app_dir() -> Path:
    return root() / "nanoapps" / "apps" / FOLDER


def cache_dir() -> Path:
    return root() / "build" / "tinyllm"


def model_path() -> Path:
    return const.artifact(HOST_DATA) / "model.bin"


def _download(src: str, dest: Path) -> None:
    part = dest.with_suffix(dest.suffix + ".part")
    if have("curl"):
        r = run(["curl", "-fsSL", "--retry", "3", "-o", str(part), src], check=False)
        if r.returncode != 0:
            part.unlink(missing_ok=True)
            raise Refused(f"download failed: {src}")
    else:
        with urllib.request.urlopen(src, timeout=120) as r, part.open("wb") as fh:
            shutil.copyfileobj(r, fh)
    part.rename(dest)


def fetch(force: bool = False) -> dict:
    """Download the checkpoint and tokenizer into build/tinyllm (cached)."""
    out = {}
    cache_dir().mkdir(parents=True, exist_ok=True)
    for name, want in FILES.items():
        dest = cache_dir() / name
        if force or not dest.is_file() or sha256_file(dest) != want:
            dest.unlink(missing_ok=True)
            print(f"==> downloading {name} from {HF_REPO} @ {HF_COMMIT[:12]} ({LICENCE})")
            _download(url(name), dest)
            got = sha256_file(dest)
            if got != want:
                dest.unlink(missing_ok=True)
                raise Refused(f"{name}: SHA-256 mismatch (got {got})")
        out[name] = dest
    return out


def _mkmodel():
    spec = importlib.util.spec_from_file_location("tinyllm_mkmodel", app_dir() / "mkmodel.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ensure_model(force: bool = False) -> Path:
    """Make the int8 model file from the pinned checkpoint, if it is missing."""
    dest = model_path()
    if dest.is_file() and not force:
        return dest
    files = fetch()
    data = _mkmodel().convert(files["stories260K.bin"], files["tok512.bin"], STORY_TOKENS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.rename(dest)
    print(f"  model  {dest}  {len(data)} bytes (int8, from stories260K)")
    return dest


def prepare() -> dict:
    """What `nabase add 'Tiny LLM'` needs: the model file and the .hbapp."""
    model = ensure_model()
    hbapp = app_dir() / "build" / f"{FOLDER}.hbapp"
    staged = const.artifact("artifacts/extra/Executables") / f"{NAME}.hbapp"
    built = False
    if not staged.is_file():
        from .sdkapp import build_app

        build_app(FOLDER, quiet=True)
        built = True
    return {"model": str(model), "hbapp": str(hbapp), "built": built}


def missing_help() -> str:
    return (
        "Tiny LLM's model file is made on this computer from karpathy/tinyllamas "
        f"stories260K (MIT, pinned commit {HF_COMMIT[:12]}): ./start sdk build tinyllm, "
        f"then {CMD}."
    )
