"""Vendor SCSI (C6) for tethered NanoApps and post-untether verify.

Linux: sg_raw on the block device (hardware-tested path).
macOS: prefer IOKit SCSITask (`ipod_sg_raw`, same idea as
Raphmatt/nano7-a1717-dock tools/macos/ipod_sg_raw.c) on
com_apple_driver_iPodSBCNub after unmount + ObtainExclusiveAccess.
Fall back to libusb BOT usbscsi if the CDB could not be sent.
usbscsi seizes mass storage and fights the kernel.
C6 96 is the existing ipod_sun harness, not a new exploit.

Outcomes:
  ok               CDB sent, status GOOD
  device_rejected  CDB sent, SCSI CHECK CONDITION (handler not active)
  could_not_send   CDB never reached the device (nub/plugin/exclusive/claim)
"""

from __future__ import annotations

import subprocess

import base64
import re
import tempfile
from pathlib import Path

from . import const
from .compat import Refused, have, is_linux, is_macos, run
from .device import Nano
from .usb import build_ipod_sg_raw, build_usbscsi, ipod_sg_raw_bin, usbscsi_bin

OUTCOME_OK = "ok"
OUTCOME_COULD_NOT_SEND = "could_not_send"
OUTCOME_DEVICE_REJECTED = "device_rejected"

# CSW status 1 = CHECK CONDITION. "scsi status 12" must not match.
_CHECK_CONDITION_RE = re.compile(
    r"check condition|sense key|csw status 1\b|scsi status (?:0x0*1\b|1\b)",
    re.IGNORECASE,
)


def join_helper_detail(*parts: str) -> str:
    """Concatenate helper stdout/stderr. Do not add a second usbscsi: prefix."""
    chunks = []
    for part in parts:
        text = str(part or "").strip()
        if text:
            chunks.append(text)
    return "\n".join(chunks)


def print_probe_detail(probe: dict) -> None:
    """Print every helper line. Live test truncated this to 240 chars."""
    print(
        f"    SCSI       method={probe.get('method')} ok={probe.get('ok')} "
        f"outcome={probe.get('outcome')}"
    )
    tried = probe.get("tried") or []
    if tried:
        print(f"               tried={' '.join(str(t) for t in tried)}")
    detail = str(probe.get("detail") or "").strip()
    if not detail:
        print("               (no helper stderr)")
        return
    for line in detail.splitlines():
        print(f"               {line}")


def classify_scsi_result(returncode: int, text: str) -> str:
    """Map helper output to ok / could_not_send / device_rejected.

    DEVICE_REJECTED only for a real SCSI CHECK CONDITION / CSW status 1.
    Argv errors (unknown command probe), missing user client, exclusive
    busy, and claim failures are COULD_NOT_SEND. Never default to rejected.
    """
    blob = text or ""
    low = blob.lower()
    if returncode == 0:
        return OUTCOME_OK
    if (
        returncode == 2
        or "unknown command" in low
        or "usage:" in low
        or "flag provided but not defined" in low
    ):
        return OUTCOME_COULD_NOT_SEND
    if _CHECK_CONDITION_RE.search(low):
        return OUTCOME_DEVICE_REJECTED
    return OUTCOME_COULD_NOT_SEND


def ensure_scsi_helpers() -> None:
    """Rebuild ipod_sg_raw / usbscsi when sources changed. No NAND write.

    --verify must not reuse a stale build/usbscsi-darwin-amd64 that lacks
    `probe`. Destination paths include a 12-hex source hash.
    """
    if not is_macos():
        return
    sg = build_ipod_sg_raw()
    print(f"    helper     ipod_sg_raw {sg}")
    bot = build_usbscsi()
    print(f"    helper     usbscsi {bot}")


def scsi_available_local() -> tuple:
    if is_linux() and have("sg_raw"):
        return True, "sg_raw", "Linux SG_IO (hardware-tested for tethered NanoApps)"
    if is_macos():
        iokit = ipod_sg_raw_bin()
        if not iokit.is_file():
            try:
                iokit = build_ipod_sg_raw()
            except Refused:
                iokit = ipod_sg_raw_bin()
        if iokit.is_file():
            return True, "ipod_sg_raw", "IOKit SCSITask (macOS; does not format FAT)"
        try:
            binp = build_usbscsi()
        except (Refused, OSError, subprocess.CalledProcessError):
            binp = usbscsi_bin()
        if binp.is_file():
            return True, "usbscsi", "libusb BOT (untested on hardware; seizes USB mass storage)"
        return False, "scsi-missing", "run ./start deps to build ipod_sg_raw / usbscsi"
    binp = usbscsi_bin()
    if not binp.is_file():
        try:
            binp = build_usbscsi()
        except (Refused, OSError, subprocess.CalledProcessError):
            pass
    if binp.is_file():
        note = "libusb BOT (untested on hardware; seizes USB mass storage)"
        if is_linux():
            note = "libusb BOT fallback (sg_raw not found; untested)"
        return True, "usbscsi", note
    return False, "none", "install sg3-utils (sg_raw) or run ./start deps to build usbscsi"


def _need_method() -> str:
    ok, method, detail = scsi_available_local()
    if not ok:
        raise Refused(f"no local SCSI: {detail}")
    return method


def peek(nano: Nano, addr: int, n: int = 0x200) -> bytes:
    ensure_scsi_helpers()
    method = _need_method()
    if method == "sg_raw":
        return _peek_sg(nano, addr, n)
    if method == "ipod_sg_raw":
        try:
            return _peek_iokit(nano, addr, n)
        except Refused as exc:
            blob = str(exc)
            rc = 3 if "COULD_NOT_SEND" in blob else 1
            if classify_scsi_result(rc, blob) == OUTCOME_COULD_NOT_SEND:
                return _peek_usbscsi(addr, n)
            raise
    return _peek_usbscsi(addr, n)


def harness_probe(nano: Nano) -> dict:
    """C6 96 04: ipod_sun VROM/harness. Stock 1.1.2 rejects it.

    Success means the untether SCSI handler is in RetailOS. Does not write
    FAT, firmware, or bootloader. Distinguishes could-not-send from a
    device CHECK CONDITION so verify never claims the handler is inactive
    when the CDB was never delivered.
    """
    ensure_scsi_helpers()
    method = _need_method()
    tried = []
    if method == "sg_raw":
        ok, outcome, detail = _probe_sg(nano)
        tried.append("sg_raw")
        return {"ok": ok, "outcome": outcome, "method": method, "detail": detail, "tried": tried}
    if method == "ipod_sg_raw":
        ok, outcome, detail = _probe_iokit(nano)
        tried.append("ipod_sg_raw")
        if outcome == OUTCOME_COULD_NOT_SEND:
            ok2, outcome2, detail2 = _probe_usbscsi()
            tried.append("usbscsi")
            return {
                "ok": ok2,
                "outcome": outcome2,
                "method": "usbscsi" if ok2 or outcome2 != OUTCOME_COULD_NOT_SEND else "ipod_sg_raw",
                "detail": join_helper_detail(detail, detail2) if not ok2 else detail2,
                "tried": tried,
            }
        return {
            "ok": ok,
            "outcome": outcome,
            "method": "ipod_sg_raw",
            "detail": detail,
            "tried": tried,
        }
    ok, outcome, detail = _probe_usbscsi()
    tried.append("usbscsi")
    return {"ok": ok, "outcome": outcome, "method": "usbscsi", "detail": detail, "tried": tried}


def write_mem(nano: Nano, addr: int, data: bytes) -> None:
    method = _need_method()
    if method == "sg_raw":
        _write_sg(nano, addr, data)
        return
    if method == "ipod_sg_raw":
        raise Refused(
            "ipod_sg_raw is probe/peek only. Mac-native NanoApps write/exec is "
            "the next step (a1717-dock nanoapps-macos-transport.patch). "
            "Nothing written."
        )
    _write_usbscsi(addr, data)


def exec_addr(nano: Nano, addr: int) -> None:
    method = _need_method()
    if method == "sg_raw":
        _exec_sg(nano, addr)
        return
    if method == "ipod_sg_raw":
        raise Refused(
            "ipod_sg_raw is probe/peek only. Mac-native NanoApps exec is the "
            "next step. Nothing written."
        )
    _exec_usbscsi(addr)


def _addr_bytes(addr: int) -> list:
    return [(addr >> s) & 0xFF for s in (24, 16, 8, 0)]


def _usbscsi() -> Path:
    return build_usbscsi()


def _peek_sg(nano: Nano, addr: int, n: int) -> bytes:
    dev = nano.raw_device
    out = bytearray()
    for off in range(0, n, 0x200):
        a = addr + off
        hexb = [f"{b:02x}" for b in [0xC6, 0x96, 0x02, *_addr_bytes(a)]]
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name
        try:
            run(["sg_raw", "-o", tmp_path, "-r", "512", dev, *hexb], check=False, capture=True)
            data = Path(tmp_path).read_bytes() if Path(tmp_path).is_file() else b""
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        data = (data + b"\0" * 0x200)[: min(0x200, n - off)]
        out += data
    return bytes(out)


def _write_sg(nano: Nano, addr: int, data: bytes) -> None:
    dev = nano.raw_device
    chunk = 512
    pad = (len(data) + chunk - 1) // chunk * chunk
    blob = data + b"\0" * (pad - len(data))
    for i in range(pad // chunk):
        cur = addr + i * chunk
        hexb = [f"{b:02x}" for b in [0xC6, 0x96, 0x01, *_addr_bytes(cur)]]
        with tempfile.NamedTemporaryFile(delete=False) as piece:
            piece.write(blob[i * chunk : (i + 1) * chunk])
            piece_path = piece.name
        try:
            run(
                ["sg_raw", "-s", str(chunk), "-i", piece_path, dev, *hexb],
                check=False,
                capture=True,
            )
        finally:
            Path(piece_path).unlink(missing_ok=True)


def _exec_sg(nano: Nano, addr: int) -> None:
    hexb = [f"{b:02x}" for b in [0xC6, 0x96, 0x03, *_addr_bytes(addr)]]
    run(["sg_raw", "-o", "/dev/null", "-r", "512", nano.raw_device, *hexb], check=False, capture=True)


def _peek_usbscsi(addr: int, n: int) -> bytes:
    r = run([str(_usbscsi()), "peek", hex(addr), hex(n)], check=False, capture=True)
    text = str(r.stdout or "") + str(r.stderr or "")
    if r.returncode != 0:
        outcome = classify_scsi_result(r.returncode, text)
        tag = "COULD_NOT_SEND" if outcome == OUTCOME_COULD_NOT_SEND else "DEVICE_REJECTED"
        raise Refused(
            f"{tag} macOS USB SCSI peek failed. FAT was not written. "
            "Unmount the nano, or plug it into the Raspberry Pi and run ./start setup.\n"
            + text
        )
    raw = r.stdout or ""
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", "ignore")
    return base64.b64decode(raw.strip())


def _write_usbscsi(addr: int, data: bytes) -> None:
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(data)
        path = tmp.name
    try:
        r = run([str(_usbscsi()), "write", hex(addr), path], check=False, capture=True)
        if r.returncode != 0:
            raise Refused("macOS USB SCSI write failed: " + str(r.stderr or r.stdout))
    finally:
        Path(path).unlink(missing_ok=True)


def _exec_usbscsi(addr: int) -> None:
    r = run([str(_usbscsi()), "exec", hex(addr)], check=False, capture=True)
    if r.returncode != 0:
        raise Refused("macOS USB SCSI exec failed: " + str(r.stderr or r.stdout))


def _ipod_sg() -> Path:
    binp = build_ipod_sg_raw()
    if not binp.is_file():
        raise Refused("ipod_sg_raw missing; run ./start deps on macOS")
    return binp


def _bsd_args(nano: Nano) -> list:
    ident = (nano.bsd_name or Path(nano.device).name or "").lstrip("r")
    if ident.startswith("disk"):
        return ["--bsd", ident]
    return []


def _peek_iokit(nano: Nano, addr: int, n: int) -> bytes:
    r = run(
        [str(_ipod_sg()), *_bsd_args(nano), "peek", hex(addr), hex(n)],
        check=False,
        capture=True,
    )
    text = str(r.stdout or "") + str(r.stderr or "")
    if r.returncode != 0:
        outcome = classify_scsi_result(r.returncode, text)
        tag = "COULD_NOT_SEND" if outcome == OUTCOME_COULD_NOT_SEND else "DEVICE_REJECTED"
        raise Refused(
            f"{tag} IOKit SCSITask peek failed. FAT was not written. "
            "Try unmounting, or use usbscsi after ./start unmount.\n"
            + text
        )
    lines = (r.stdout or "").strip().splitlines()
    payload = lines[-1] if lines else ""
    try:
        return base64.b64decode(payload)
    except Exception as exc:
        raise Refused(f"IOKit peek output was not base64: {payload!r}") from exc


def _probe_iokit(nano: Nano) -> tuple:
    r = run(
        [str(_ipod_sg()), *_bsd_args(nano), "probe"],
        check=False,
        capture=True,
    )
    text = str(r.stdout or "") + str(r.stderr or "")
    outcome = classify_scsi_result(r.returncode, text)
    if r.returncode == 0 and "C6_96_04_OK" in text:
        return True, OUTCOME_OK, text.strip()
    if r.returncode == 0:
        return True, OUTCOME_OK, text.strip() or "C6 96 04 accepted"
    return False, outcome, text.strip() or "C6 96 04 not accepted"


def _probe_sg(nano: Nano) -> tuple:
    hexb = [
        f"{b:02x}"
        for b in [0xC6, 0x96, const.C6_HARNESS_OP, 0, 0, 0, 0]
    ]
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name
    try:
        r = run(
            ["sg_raw", "-o", tmp_path, "-r", "512", nano.raw_device, *hexb],
            check=False,
            capture=True,
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    text = str(r.stdout or "") + str(r.stderr or "")
    outcome = classify_scsi_result(r.returncode, text)
    if r.returncode == 0:
        return True, OUTCOME_OK, text.strip() or "sg_raw C6 96 04 accepted"
    return False, outcome, text.strip() or "sg_raw C6 96 04 not accepted"


def _probe_usbscsi() -> tuple:
    r = run([str(_usbscsi()), "probe"], check=False, capture=True)
    text = str(r.stdout or "") + str(r.stderr or "")
    outcome = classify_scsi_result(r.returncode, text)
    if r.returncode == 0 and "C6_96_04_OK" in text:
        return True, OUTCOME_OK, text.strip()
    if r.returncode == 0:
        return True, OUTCOME_OK, text.strip() or "usbscsi C6 96 04 accepted"
    return False, outcome, text.strip() or "usbscsi C6 96 04 not accepted"
