"""OS-portable helpers. Never assume GNU coreutils (macOS is BSD)."""

from __future__ import annotations

import hashlib
import os
import platform
import shlex
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, Optional, Sequence, Union


class Refused(SystemExit):
    """A safety check failed. Nothing past this point should write the device."""

    def __init__(self, message: str):
        super().__init__("REFUSED: " + message)


def root() -> Path:
    return Path(__file__).resolve().parent.parent


def is_macos() -> bool:
    return platform.system() == "Darwin"


def is_linux() -> bool:
    return platform.system() == "Linux"


def host_os() -> str:
    return platform.system().lower()  # darwin / linux


def host_arch() -> str:
    machine = platform.machine().lower()
    return {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(machine, machine)


def is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def which(name: str) -> Optional[str]:
    return shutil.which(name)


def have(name: str) -> bool:
    return which(name) is not None


def nproc() -> int:
    return os.cpu_count() or 4


def file_size(path: Path) -> int:
    path = Path(path)
    st = path.stat()
    if stat.S_ISBLK(st.st_mode) or stat.S_ISCHR(st.st_mode):
        # Block devices report st_size 0 on Linux. Callers should use device.size.
        if st.st_size:
            return st.st_size
        raise OSError(f"no size for special file {path}")
    return st.st_size


def hash_file(path: Path, algo: str, bufsize: int = 1024 * 1024) -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        while True:
            chunk = f.read(bufsize)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def sha256_file(path: Path, bufsize: int = 1024 * 1024) -> str:
    return hash_file(path, "sha256", bufsize=bufsize)


def sha1_file(path: Path, bufsize: int = 1024 * 1024) -> str:
    return hash_file(path, "sha1", bufsize=bufsize)


def md5_file(path: Path, bufsize: int = 1024 * 1024) -> str:
    return hash_file(path, "md5", bufsize=bufsize)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def realpath(path: Path) -> Path:
    return Path(path).resolve()


def is_symlink(path: Path) -> bool:
    return Path(path).is_symlink()


def die_if_symlink(path: Path, what: str) -> None:
    if Path(path).is_symlink():
        raise Refused(f"{what} is a symlink: {path}")


def confirm(prompt: str, default: bool = False) -> bool:
    if not sys.stdin.isatty():
        return default
    suffix = "[Y/n]" if default else "[y/N]"
    ans = input(f"{prompt} {suffix} ").strip().lower()
    if not ans:
        return default
    return ans in ("y", "yes")


def require_typed(word: str, prompt: str, typed: Optional[str] = None) -> None:
    """Demand the exact confirmation word before a destructive write.

    --typed-confirm WORD still checks the word (GUI / non-TTY).
    --yes --i-accept-brick-risk skips this only when no typed word was supplied.
    The GUI must pass --typed-confirm and must not use that skip pair.
    """
    supplied = typed if typed is not None else os.environ.get("NANO7KIT_TYPED_CONFIRM")
    if supplied is not None and str(supplied) != "":
        if str(supplied) != word:
            raise Refused(f"did not type {word}; nothing written")
        return
    if not sys.stdin.isatty():
        raise Refused(
            f"refusing a destructive step without a TTY; pass --typed-confirm {word}, "
            "or --i-accept-brick-risk with --yes"
        )
    ans = input(prompt + f" Type {word} to continue: ").strip()
    if ans != word:
        raise Refused(f"did not type {word}; nothing written")


def run(
    cmd: Sequence[str],
    *,
    check: bool = True,
    capture: bool = False,
    text: bool = True,
    env: Optional[dict] = None,
    cwd: Optional[Path] = None,
    input: Optional[str] = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(cmd),
        check=check,
        capture_output=capture,
        text=text,
        env=env,
        cwd=str(cwd) if cwd else None,
        input=input,
    )


def run_out(cmd: Sequence[str], check: bool = True) -> str:
    return run(cmd, check=check, capture=True).stdout


def disk_free(path: Path) -> int:
    return shutil.disk_usage(path).free


def reexec_as_root(*, argv: Optional[Sequence[str]] = None) -> None:
    """Replace this process with sudo -E if we are not root.

    argv, when given, replaces sys.argv[1:] so a wizard can re-enter a
    subcommand instead of the menu.

    `python -m host` sets argv[0] to host/__main__.py. Re-execing that path
    breaks relative imports, so we switch to `python -m host`.
    """
    if is_root():
        return
    if os.environ.get("NANO7KIT_SKIP_SUDO") == "1":
        return
    if os.environ.get("NANO7KIT_REEXEC") == "1":
        raise Refused("sudo re-exec did not become root")
    sudo = which("sudo")
    if not sudo:
        raise Refused("this step needs root and sudo is not installed")
    rest = list(argv) if argv is not None else list(sys.argv[1:])
    os.environ["NANO7KIT_REEXEC"] = "1"
    entry = Path(sys.argv[0]).resolve()
    if entry.name == "__main__.py" and entry.parent.name == "host":
        os.execvp(sudo, [sudo, "-E", sys.executable, "-m", "host", *rest])
    os.execvp(sudo, [sudo, "-E", sys.executable, sys.argv[0], *rest])


def path_prepend(dir_path: Path) -> None:
    os.environ["PATH"] = str(dir_path) + os.pathsep + os.environ.get("PATH", "")


def force_utc_timezone() -> None:
    """Make FAT DOS timestamps stable across host timezones.

    FAT times are naive local values. pyfatfs getinfo/setinfo is not an
    inverse under a non-UTC zone (mktime vs DOS), so
    helpers/fat_patch.py restamping Helvetica.ttf changes MSE bytes.
    Europe/London produced rescue 041d4f61...; TZ=UTC reproduces 39579dba...
    """
    os.environ["TZ"] = "UTC"
    if hasattr(time, "tzset"):
        time.tzset()


def quoted_path(path: Union[Path, str]) -> str:
    """Shell-quote a path so copy-paste survives spaces in backup filenames."""
    return shlex.quote(str(path))


def print_cmd(cmd: Iterable[str]) -> None:
    print("    $ " + " ".join(shlex.quote(str(c)) for c in cmd))
