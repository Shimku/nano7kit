"""DFU detection, host-built wInd3x, and vendor SCSI over USB BOT.

DFU (05ac:1234) is a libusb device, the same class of path irecovery/ipwndfu
use on macOS. wInd3x's nano 7G path is s5late, which the upstream README does
not mark Linux-only (that restriction is nano 4G/5G).

Retail-OS vendor SCSI (C6) needs the USB mass-storage interface. wInd3x already
seizes that interface with libusb SetAutoDetach for recovery diskmode. We use
the same approach for C6. If the kernel will not release the device on macOS,
the caller falls back to a Raspberry Pi over SSH.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

from . import const
from .compat import (
    Refused,
    die_if_symlink,
    have,
    host_arch,
    host_os,
    is_linux,
    is_macos,
    is_root,
    run,
    run_out,
    sha256_file,
    which,
)
from .state import state_dir, utc_stamp


DFU_PIDS = {
    const.NANO7_DFU_PID,
    const.APPLE_DFU_GENERIC_PID,
    0x1223,
    0x1225,
    0x1231,
    0x1232,
    0x1234,
}


def _ioreg_int(raw: str) -> int:
    text = raw.strip()
    if text.lower().startswith("0x"):
        return int(text, 16)
    return int(text, 10)


def parse_ioreg_usb(text: str) -> list:
    """Parse `ioreg -p IOUSB -l -w 0` into dicts with vid, pid, serial, product.

    USB Serial Number is a property of the IOUSBHostDevice. Product strings
    such as 'Apple iPod Media' are not serials.
    """
    devices = []
    blocks = re.split(r"\+\-o ", text or "")
    for block in blocks:
        vm = re.search(r'"idVendor"\s*=\s*(0x[0-9A-Fa-f]+|\d+)', block)
        pm = re.search(r'"idProduct"\s*=\s*(0x[0-9A-Fa-f]+|\d+)', block)
        if not vm or not pm:
            continue
        try:
            vid, pid = _ioreg_int(vm.group(1)), _ioreg_int(pm.group(1))
        except ValueError:
            continue
        sm = re.search(r'"USB Serial Number"\s*=\s*"([^"]*)"', block)
        if not sm:
            sm = re.search(r'"kUSBSerialNumberString"\s*=\s*"([^"]*)"', block)
        prod = re.search(r'"USB Product Name"\s*=\s*"([^"]*)"', block)
        bsd = re.search(r'"BSD Name"\s*=\s*"([^"]*)"', block)
        header = block.splitlines()[0] if block else ""
        name = header.split("@", 1)[0].strip()
        devices.append(
            {
                "vid": vid,
                "pid": pid,
                "serial": (sm.group(1).strip() if sm else ""),
                "product": (prod.group(1).strip() if prod else ""),
                "bsd_name": (bsd.group(1).strip() if bsd else ""),
                "name": name,
            }
        )
    return devices


def apple_usb_serials_for_pid(text: str, pid: int) -> list:
    """USB Serial Number values for Apple VID + pid from an ioreg dump."""
    out = []
    seen = set()
    for d in parse_ioreg_usb(text):
        if d["vid"] != const.APPLE_VID or d["pid"] != pid:
            continue
        serial = (d.get("serial") or "").strip()
        if not serial or serial in seen:
            continue
        seen.add(serial)
        out.append(serial)
    return out


def apple_usb_serial_for_bsd(text: str, bsd_name: str) -> str:
    ident = (bsd_name or "").strip()
    if not ident:
        return ""
    for d in parse_ioreg_usb(text):
        if d["vid"] != const.APPLE_VID:
            continue
        if d.get("bsd_name") == ident or d.get("bsd_name") == ident.lstrip("r"):
            return (d.get("serial") or "").strip()
    return ""


def list_apple_usb() -> list:
    """Return [(vid, pid, extra), ...] for attached Apple USB devices."""
    found = []
    if is_linux():
        usb = Path("/sys/bus/usb/devices")
        if not usb.is_dir():
            return found
        for node in usb.iterdir():
            try:
                vid = int((node / "idVendor").read_text().strip(), 16)
                pid = int((node / "idProduct").read_text().strip(), 16)
            except (FileNotFoundError, ValueError):
                continue
            if vid == const.APPLE_VID:
                serial = ""
                try:
                    serial = (node / "serial").read_text().strip()
                except OSError:
                    serial = ""
                found.append((vid, pid, serial or node.name))
        return found
    if is_macos():
        try:
            out = run_out(["ioreg", "-p", "IOUSB", "-l", "-w", "0"], check=False)
        except Exception:
            out = ""
        for d in parse_ioreg_usb(out):
            if d["vid"] == const.APPLE_VID:
                extra = d.get("serial") or d.get("product") or d.get("name") or ""
                found.append((d["vid"], d["pid"], extra))
        return found
    return found


def dfu_present() -> bool:
    return any(pid in DFU_PIDS for _, pid, _ in list_apple_usb())


def diskmode_usb_present() -> bool:
    """True when USB shows nano 7G MSC (0x1267), even if macOS has no IOMedia."""
    return const.NANO7_DISK_PID in apple_pids()


def wtf_present() -> bool:
    pids = set(apple_pids())
    return const.NANO7_WTF_PID in pids or const.NANO7_LATE_WTF_PID in pids


def recovery_usb_state() -> str:
    """disk | wtf | dfu | none. Disk wins if 0x1267 is enumerated."""
    pids = set(apple_pids())
    if const.NANO7_DISK_PID in pids:
        return "disk"
    if const.NANO7_LATE_WTF_PID in pids or const.NANO7_WTF_PID in pids:
        return "wtf"
    if any(pid in DFU_PIDS for pid in pids):
        return "dfu"
    return "none"


def wait_recovery_entry(timeout: int = 180) -> str:
    """Wait until DFU, WTF, or recovery diskmode is visible.

    After IPodRepartition, RetailOS may not boot (no FAT32), so Disk Mode
    from the Sleep/Wake+Home volume combo is unreachable. DFU still works.
    If the nano is already WTF 0x124a/0x1249 or 0x1267, do not wait for DFU.
    """
    state = recovery_usb_state()
    if state == "disk":
        print("  USB diskmode already present (05ac:1267).")
        return state
    if state == "wtf":
        print("  USB WTF already present (05ac:1249 or 05ac:124a).")
        return state
    if state == "dfu":
        print("  DFU device already seen.")
        return state
    wait_dfu(timeout=timeout)
    return "dfu"


def apple_pids() -> List[int]:
    return [pid for _, pid, _ in list_apple_usb()]


def late_wtf_present() -> bool:
    """True when USB shows Mid-2015 WTF (0x124a)."""
    return const.NANO7_LATE_WTF_PID in apple_pids()


def usb_revision_notes() -> List[str]:
    """Human-readable USB identity notes for doctor / find. Never writes."""
    pids = set(apple_pids())
    notes: List[str] = []
    if const.NANO7_LATE_WTF_PID in pids:
        notes.append(
            "WTF 0x124a (Mid-2015). Untested. Flash/fat32 need --i-accept-2015-refresh. "
            "Patched wInd3x then uses 0x124a / 39A10023 recovery. See docs/HARDWARE-2015.md."
        )
    if const.NANO7_WTF_PID in pids:
        notes.append("WTF 0x1249 (2012 recovery identity in wInd3x)")
    if const.NANO7_DFU_PID in pids:
        notes.append(
            "DFU 0x1234 (same PID on 2012 and 2015; initial Kind is Nano 7G; "
            "WTF 0x124a promotes Kind to Nano 7G (Mid-2015))"
        )
    if const.NANO7_DISK_PID in pids:
        notes.append("disk 0x1267 (same PID on 2012 and 2015, RetailOS or Disk Mode)")
    return notes


def log_indicates_2015(log: str) -> bool:
    text = log or ""
    if const.PREPARE_DISKMODE_2015 in text:
        return True
    if re.search(r"0x0*124a\b", text, re.I):
        return True
    if "Nano 7G (Mid-2015)" in text:
        return True
    return False


def _2015_refused_message() -> str:
    return (
        "this looks like a Mid-2015 nano 7G (USB WTF 0x124a, or wInd3x "
        '"Nano 7G (Mid-2015)"). The hardware-tested flash path is a 2012 unit '
        "(WTF 0x1249). DFU PID 0x1234 is the same, so the first match is still "
        "Kind Nano 7G. The nano7 patch then switches Kind to Nano 7G (Mid-2015) "
        "when WTF 0x124a opens, so recovery is 0x124a0000 / 39A10023 instead of "
        "the 2012 0x12490000 / 37A40005 image. That hop has not been run on "
        "hardware. Read docs/HARDWARE-2015.md. Pass --i-accept-2015-refresh "
        "to continue. Nothing was written to NAND."
    )


def wait_dfu(timeout: int = 180) -> None:
    from .buttons import show

    show("dfu")
    print("  On some Intel Macs, DFU only enumerates through a USB-A hub;")
    print("  direct USB-C to Lightning may not show 05ac:1234.")
    print("  If Finder offers to download recovery software, Cancel.")
    print("  Waiting for USB DFU (Apple VID 05ac, PID 1234)...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if dfu_present():
            print("  DFU device seen.")
            return
        time.sleep(1)
    raise Refused(
        "no DFU device appeared. The screen must stay black (not the Apple logo). "
        "Try again, use a different cable, or plug the nano into the Raspberry Pi "
        "and re-run ./start setup if this Mac cannot see DFU."
    )


def tested_wind3x() -> Path:
    return Path(__file__).resolve().parent.parent / "untether" / "third_party" / "wind3x" / "wInd3x-linux-amd64"


def host_wind3x_path() -> Path:
    build = Path(__file__).resolve().parent.parent / "build"
    return build / f"wInd3x-{host_os()}-{host_arch()}"


def select_wind3x(*, need_2015_kind_switch: bool = False) -> Tuple[Path, bool, str]:
    """Return (path, hardware_tested_binary, note).

    The bundled Linux amd64 binary predates the Mid-2015 Kind switch. A 2015
    unit must use a host build of the current nano7 patch so recovery is
    0x124a0000 / 39A10023.
    """
    tested = tested_wind3x()
    if (
        is_linux()
        and host_arch() == "amd64"
        and tested.is_file()
        and not need_2015_kind_switch
    ):
        die_if_symlink(tested, "wInd3x")
        digest = sha256_file(tested)
        if digest == const.TESTED_WIND3X_SHA256:
            return tested, True, "bundled Linux amd64 binary (hardware-tested)"
        raise Refused(
            f"bundled wInd3x hash is {digest}, expected {const.TESTED_WIND3X_SHA256}"
        )
    hostp = host_wind3x_path()
    if hostp.is_file() and os.access(hostp, os.X_OK):
        die_if_symlink(hostp, "wInd3x")
        if need_2015_kind_switch:
            _require_current_patch_stamp(hostp)
        return (
            hostp,
            False,
            f"host-built {host_os()}/{host_arch()} (current nano7 patch"
            f"{'; Mid-2015 Kind switch' if need_2015_kind_switch else ''}; "
            f"this binary has not been used on hardware)",
        )
    if need_2015_kind_switch:
        raise Refused(
            "Mid-2015 recovery needs a host-built wInd3x from the current nano7 "
            "patch (Kind switch to 0x124a / 39A10023). The bundled Linux amd64 "
            "binary does not include that switch. Run ./start deps, then pass "
            "--i-accept-untested-flasher --i-accept-2015-refresh."
        )
    raise Refused(
        "no wInd3x binary for this computer. Run ./start deps to build it from the "
        "pinned freemyipod/wInd3x commit plus the nano7 patch. The only hardware-tested "
        "flasher is the bundled Linux x86-64 binary."
    )


def nano7_patch_path() -> Path:
    return Path(__file__).resolve().parent.parent / "untether" / "patches" / "wind3x-nano7.patch"


def _wind3x_stamp(path: Path) -> Path:
    return Path(str(path) + ".patch-sha256")


def _require_current_patch_stamp(wind3x: Path) -> None:
    stamp = _wind3x_stamp(wind3x)
    expect = sha256_file(nano7_patch_path())
    if not stamp.is_file():
        raise Refused(
            f"{wind3x} has no patch stamp; it may predate the Mid-2015 Kind switch. "
            "Re-run ./start deps so wInd3x is rebuilt from the current nano7 patch."
        )
    actual = stamp.read_text().strip().split()[0]
    if actual != expect:
        raise Refused(
            f"{wind3x} was built with patch {actual[:16]}...; current nano7 patch is "
            f"{expect[:16]}.... Rebuild with ./start deps."
        )


def libusb_pkgconfig_ok() -> bool:
    """True when pkg-config can see libusb-1.0 (not merely that pkg-config exists)."""
    if not have("pkg-config"):
        return False
    r = run(["pkg-config", "--exists", "libusb-1.0"], check=False, capture=True)
    return r.returncode == 0


def _ldflag_libdirs(ldflags: str) -> list:
    dirs = []
    parts = (ldflags or "").split()
    i = 0
    while i < len(parts):
        part = parts[i]
        if part.startswith("-L") and len(part) > 2:
            dirs.append(part[2:])
        elif part == "-L" and i + 1 < len(parts):
            i += 1
            dirs.append(parts[i])
        i += 1
    return dirs


def _append_macos_rpath(ldflags: str, libdirs: list) -> str:
    if not is_macos():
        return ldflags
    out = ldflags or ""
    seen = set()
    for raw in libdirs:
        libdir = str(raw).rstrip("/")
        if not libdir or libdir in seen:
            continue
        seen.add(libdir)
        flag = f"-Wl,-rpath,{libdir}"
        if flag not in out:
            out = (out + " " + flag).strip()
    return out


def libusb_cgo_env(env: Optional[dict] = None) -> dict:
    """CGO flags for libusb-1.0.

    Keeps pre-set CGO_CPPFLAGS / CGO_LDFLAGS (micromamba env.sh). Otherwise
    uses `pkg-config --cflags --libs libusb-1.0`, then Homebrew. On macOS
    always adds -Wl,-rpath,<libdir> so a non-system dylib still loads under
    sudo -E.
    """
    out = dict(os.environ if env is None else env)
    out.setdefault("CGO_ENABLED", "1")
    cppflags = (out.get("CGO_CPPFLAGS") or "").strip()
    ldflags = (out.get("CGO_LDFLAGS") or "").strip()
    libdirs: list = _ldflag_libdirs(ldflags)

    if (not cppflags or not ldflags) and libusb_pkgconfig_ok():
        cflags = run_out(["pkg-config", "--cflags", "libusb-1.0"], check=False).strip()
        libs = run_out(["pkg-config", "--libs", "libusb-1.0"], check=False).strip()
        libdir = run_out(
            ["pkg-config", "--variable=libdir", "libusb-1.0"], check=False
        ).strip()
        if cflags and not cppflags:
            cppflags = cflags
        if libs and not ldflags:
            ldflags = libs
        if libdir:
            libdirs.append(libdir)

    if is_macos() and (not cppflags or not ldflags):
        prefixes = []
        brew = which("brew")
        if brew:
            try:
                prefix = run_out([brew, "--prefix", "libusb"], check=False).strip()
                if prefix:
                    prefixes.append(prefix)
                    pc = str(Path(prefix) / "lib" / "pkgconfig")
                    out["PKG_CONFIG_PATH"] = pc + ":" + out.get("PKG_CONFIG_PATH", "")
            except Exception:
                pass
        for extra in ("/opt/homebrew", "/usr/local"):
            if extra not in prefixes:
                prefixes.append(extra)
        for pfx in prefixes:
            inc = Path(pfx) / "include"
            lib = Path(pfx) / "lib"
            if inc.is_dir() and (
                (lib / "libusb-1.0.dylib").exists() or (lib / "libusb-1.0.a").exists()
            ):
                if not cppflags:
                    cppflags = f"-I{inc}"
                if not ldflags:
                    ldflags = f"-L{lib} -lusb-1.0"
                libdirs.append(str(lib))
                break

    ldflags = _append_macos_rpath(ldflags, libdirs + _ldflag_libdirs(ldflags))
    if cppflags:
        out["CGO_CPPFLAGS"] = cppflags
    if ldflags:
        out["CGO_LDFLAGS"] = ldflags
    return out


def go_env(env: dict) -> dict:
    """Drop a GOROOT that belongs to a different Go than the one on PATH.

    A GOROOT left over from another toolchain makes every package fail with
    'compile: version "goX" does not match go tool version "goY"'."""
    root = env.get("GOROOT")
    go = shutil.which("go", path=env.get("PATH"))
    if root and go:
        try:
            same = (Path(root) / "bin" / "go").resolve() == Path(go).resolve()
        except OSError:
            same = False
        if not same:
            env = dict(env)
            env.pop("GOROOT", None)
    return env


def build_wind3x(force: bool = False) -> Path:
    """Build patched wInd3x for THIS os/arch into build/."""
    dest = host_wind3x_path()
    patch_hash = sha256_file(nano7_patch_path())
    stamp = _wind3x_stamp(dest)
    if dest.is_file() and not force:
        if stamp.is_file() and stamp.read_text().strip().split()[0] == patch_hash:
            return dest
        print("    wInd3x patch changed; rebuilding host flasher")
        force = True
    if not have("go"):
        raise Refused(
            "Go is required to build wInd3x (brew install go / apt install golang-go / "
            "Intel Mac: scripts/macos-intel-bootstrap.sh)"
        )
    if not have("pkg-config") and not is_macos():
        print("warning: pkg-config not found; libusb flags may be wrong")
    repo = Path(__file__).resolve().parent.parent
    src = repo / "build" / "wInd3x-src"
    patch = nano7_patch_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.exists() and force:
        shutil.rmtree(src)
    if not src.exists():
        print(f"==> cloning wInd3x {const.WIND3X_COMMIT[:12]}")
        run(["git", "clone", const.WIND3X_UPSTREAM, str(src)], check=True)
        run(["git", "-C", str(src), "checkout", "--detach", const.WIND3X_COMMIT], check=True)
        run(["git", "-C", str(src), "apply", str(patch)], check=True)
    env = os.environ.copy()
    env.setdefault("GOPROXY", "https://proxy.golang.org,direct")
    env.setdefault("CGO_ENABLED", "1")
    env = go_env(libusb_cgo_env(env))
    print(f"==> go build wInd3x -> {dest}")
    run(
        ["go", "build", "-trimpath", "-o", str(dest), "./cmd/wInd3x"],
        check=True,
        cwd=src,
        env=env,
    )
    dest.chmod(dest.stat().st_mode | stat.S_IXUSR)
    stamp.write_text(patch_hash + "\n")
    print(f"    built {dest} ({sha256_file(dest)})")
    print("    HARDWARE: this flasher binary is untested. The firmware IMAGE hash is still enforced.")
    return dest


def run_wind3x(args: list, wind3x: Path) -> subprocess.CompletedProcess:
    if not is_root() and is_macos():
        # libusb device capture on Darwin is documented to need root for CLI apps.
        print("note: wInd3x on macOS usually needs root to seize the USB device")
    cmd = [str(wind3x), *args]
    print("    $ " + " ".join(cmd))
    return subprocess.run(cmd, check=False, text=True, capture_output=True)


def check_prepare_log(
    log: str,
    serial: Optional[str],
    *,
    accept_2015: bool = False,
    late_usb: bool = False,
) -> None:
    """Same identity greps as untether/scripts/flash-once.sh. Nothing is written here.

    DFU-started restore matches Kind Nano 7G first (Mid-2015 has no DFU PID).
    The nano7 patch promotes Kind to Nano 7G (Mid-2015) when WTF 0x124a
    appears, so a 2015 unit should log the Mid-2015 diskmode line. Either
    diskmode line is acceptable; a 2015 hint still needs --i-accept-2015-refresh.
    """
    has_2012 = const.PREPARE_DISKMODE_2012 in log
    has_2015 = const.PREPARE_DISKMODE_2015 in log
    if not has_2012 and not has_2015:
        raise Refused(
            "wInd3x did not report Nano 7G recovery diskmode; no firmware write attempted.\n"
            + log[-4000:]
        )
    if (has_2015 or late_usb or log_indicates_2015(log)) and not accept_2015:
        raise Refused(_2015_refused_message())
    serial = (serial or "").strip()
    if not serial:
        raise Refused("device serial is required for DFU prepare; nothing written")
    # grep -Fxq "SerialNumber: $SERIAL"  (whole line, so a prefix cannot match)
    if f"SerialNumber: {serial}" not in log.splitlines():
        m = re.search(r"SerialNumber:\s*(\S+)", log)
        seen = m.group(1) if m else "unknown"
        raise Refused(
            f"nano serial mismatch during prepare (expected {serial}, wInd3x saw {seen}). "
            f"Nothing written."
        )
    if "Recovery mode ready; no device partitions were written" not in log:
        raise Refused(
            "wInd3x prepare did not reach recovery-ready; no firmware write attempted.\n"
            + log[-4000:]
        )


def run_wind3x_watched(args: list, wind3x: Path) -> tuple:
    """Run wInd3x, sampling Apple USB PIDs while it is alive.

    Returns (CompletedProcess, late_wtf_seen). Used for prepare so a 2015 unit
    that enumerates as 0x124a between DFU and diskmode cannot hide behind the
    Nano 7G slog line.
    """
    if not is_root() and is_macos():
        print("note: wInd3x on macOS usually needs root to seize the USB device")
    cmd = [str(wind3x), *args]
    print("    $ " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    chunks: List[str] = []

    def _read() -> None:
        if proc.stdout is not None:
            chunks.append(proc.stdout.read() or "")

    reader = threading.Thread(target=_read, daemon=True)
    reader.start()
    late = False
    while proc.poll() is None:
        if late_wtf_present():
            late = True
        time.sleep(0.2)
    reader.join(timeout=10)
    if late_wtf_present():
        late = True
    log = "".join(chunks)
    result = subprocess.CompletedProcess(cmd, proc.returncode or 0, log, "")
    return result, late


def prepare_restore(
    wind3x: Path,
    serial: Optional[str],
    *,
    accept_2015: bool = False,
) -> str:
    """Run restore --prepare-only. Returns the log text. Writes nothing."""
    r, late_usb = run_wind3x_watched(["restore", "--prepare-only"], wind3x)
    log = r.stdout or ""
    stamp = utc_stamp()
    extra = f"\n# nano7kit: late_wtf_usb={late_usb} accept_2015={accept_2015}\n"
    (state_dir() / f"prepare-{stamp}.log").write_text(log + extra)
    if r.returncode != 0:
        raise Refused(
            "wInd3x restore --prepare-only failed; no firmware write attempted.\n" + log[-4000:]
        )
    check_prepare_log(log, serial, accept_2015=accept_2015, late_usb=late_usb)
    if accept_2015 and (late_usb or log_indicates_2015(log)):
        print("HARDWARE: Mid-2015 path accepted (--i-accept-2015-refresh).")
        print("          Patched wInd3x uses 0x124a / 39A10023 recovery")
        print("          (not 2012 0x12490000 / 37A40005). Still untested on")
        print("          a real MKN52. Watch BuildID and WTF 0x124a.")
        print("          See docs/HARDWARE-2015.md.")
    return log


def parse_serial(log: str) -> str:
    m = re.search(r"SerialNumber:\s*(\S+)", log)
    return m.group(1) if m else ""


def firmware_only_argv(mse: Path) -> list:
    """wInd3x IPodUpdate firmware kind only. Never --full / --bootloader / nand."""
    argv = ["restore", "--firmware", str(mse)]
    joined = " ".join(argv).lower()
    if "--full" in joined or "--bootloader" in joined or "nand" in joined or "--replace" in joined:
        raise Refused(
            "internal: firmware argv must be restore --firmware PATH only "
            "(no --full, no --bootloader, no nand, no --replace)"
        )
    return argv


def assert_firmware_only_log(log: str) -> None:
    """Prove IPodUpdate wrote firmware once and did not touch bootloader/FAT."""
    if log.count("Writing firmware...") != 1:
        raise Refused(
            "log does not prove exactly one firmware write; treating as failure. "
            "Do not retry blindly."
        )
    if "Resetting..." not in log:
        raise Refused("log does not show reset after write")
    if re.search(r"^Error:", log, re.M):
        raise Refused("wInd3x reported an error after write")
    if "Writing bootloader" in log:
        raise Refused(
            "firmware-only log shows a bootloader write. Treating as failure. "
            "Do not retry blindly. The 39A10023 bootloader on this nano must stay."
        )
    if "Reformatting system partition" in log or "IPodRepartition" in log:
        raise Refused(
            "firmware-only log shows IPodRepartition. Treating as failure. "
            "Do not retry blindly. FAT32 and music must stay."
        )


def flash_restore(wind3x: Path, mse: Path) -> str:
    """Write firmware exactly once. Caller must have already dry-run.

    IPodUpdate kind is firmware only. No bootloader, no --full, no FAT format.
    """
    argv = firmware_only_argv(mse)
    r = run_wind3x(argv, wind3x)
    log = (r.stdout or "") + (r.stderr or "")
    stamp = utc_stamp()
    (state_dir() / f"flash-{stamp}.log").write_text(log)
    if r.returncode != 0:
        raise Refused(f"firmware write failed; no retry.\n" + log[-4000:])
    assert_firmware_only_log(log)
    return log


def _repo() -> Path:
    return Path(__file__).resolve().parent.parent


def hash_source_files(paths: List[Path]) -> str:
    """Stable tree hash. Filename + content, so a rename rebuilds."""
    h = hashlib.sha256()
    for path in paths:
        path = Path(path)
        h.update(path.name.encode("utf-8"))
        h.update(b":")
        h.update(sha256_file(path).encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def ipod_sg_raw_sources() -> List[Path]:
    src = _repo() / "host" / "cmd" / "ipod_sg_raw" / "ipod_sg_raw.c"
    return [src] if src.is_file() else []


def usbscsi_sources() -> List[Path]:
    folder = _repo() / "host" / "cmd" / "usbscsi"
    return sorted(
        p
        for p in folder.glob("*.go")
        if p.is_file() and not p.name.endswith("_test.go")
    )


def ipod_sg_raw_src_hash() -> str:
    files = ipod_sg_raw_sources()
    if not files:
        return "missing"
    return hash_source_files(files)


def usbscsi_src_hash() -> str:
    files = usbscsi_sources()
    if not files:
        return "missing"
    return hash_source_files(files)


def usbscsi_bin() -> Path:
    return _repo() / "build" / f"usbscsi-{host_os()}-{host_arch()}-{usbscsi_src_hash()[:12]}"


def ipod_sg_raw_bin() -> Path:
    return (
        _repo()
        / "build"
        / f"ipod_sg_raw-{host_os()}-{host_arch()}-{ipod_sg_raw_src_hash()[:12]}"
    )


def build_ipod_sg_raw(force: bool = False) -> Path:
    """macOS IOKit SCSITask helper. No-op to build on Linux (source is still there).

    Destination embeds a 12-hex source hash so a stale
    build/ipod_sg_raw-darwin-amd64 from an older commit cannot be reused.
    """
    dest = ipod_sg_raw_bin()
    srcs = ipod_sg_raw_sources()
    if not srcs:
        raise Refused(f"missing {_repo() / 'host' / 'cmd' / 'ipod_sg_raw' / 'ipod_sg_raw.c'}")
    expect = ipod_sg_raw_src_hash()
    stamp = Path(str(dest) + ".src-sha256")
    if not is_macos():
        return dest
    if dest.is_file() and not force:
        if stamp.is_file() and stamp.read_text().strip().split()[0] == expect:
            return dest
        print("    ipod_sg_raw source changed; rebuilding")
    cc = which("cc") or which("clang") or which("gcc")
    if not cc:
        raise Refused("cc/clang is required to build ipod_sg_raw on macOS")
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"==> cc ipod_sg_raw -> {dest}")
    run(
        [
            cc,
            "-O2",
            "-Wall",
            "-o",
            str(dest),
            str(srcs[0]),
            "-framework",
            "IOKit",
            "-framework",
            "CoreFoundation",
            "-framework",
            "DiskArbitration",
        ],
        check=True,
    )
    dest.chmod(dest.stat().st_mode | stat.S_IXUSR)
    stamp.write_text(expect + "\n")
    return dest


def build_usbscsi(force: bool = False) -> Path:
    """Build usbscsi into a source-hashed path. Always checks .go files, not only main.go."""
    dest = usbscsi_bin()
    src = _repo() / "host" / "cmd" / "usbscsi"
    expect = usbscsi_src_hash()
    stamp = Path(str(dest) + ".src-sha256")
    if dest.is_file() and not force:
        if stamp.is_file() and stamp.read_text().strip().split()[0] == expect:
            return dest
        print("    usbscsi source changed; rebuilding")
    if not have("go"):
        raise Refused("Go is required to build the USB SCSI helper")
    dest.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.setdefault("CGO_ENABLED", "1")
    env.setdefault("GOPROXY", "https://proxy.golang.org,direct")
    env = go_env(libusb_cgo_env(env))
    print(f"==> go build usbscsi -> {dest}")
    run(["go", "build", "-trimpath", "-o", str(dest), "."], check=True, cwd=src, env=env)
    dest.chmod(dest.stat().st_mode | stat.S_IXUSR)
    stamp.write_text(expect + "\n")
    return dest
