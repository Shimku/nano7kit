"""ipod_theme (GPL-3) as a macOS option, plus a gated Helvetica combine.

Investigation (rsrc FAT, not NanoVault files):

  ipod_theme writes SilverImagesDB / strings / fonts / sounds in the same
  rsrc FAT that ipod_sun patches. The untether carrier is
  /Resources/Fonts/Helvetica.ttf (CFF). Combining is possible if themed
  SilverImagesDB (and other non-Helvetica files) are written into rsrc.bin
  BEFORE or AFTER the Helvetica CFF patch, and Helvetica.ttf is never
  replaced. Replacing Helvetica via ipod_theme's Fonts/ walk DESTROYS the
  exploit carrier.

  A combined MSE is a new untested hash. ./start flash keeps the tested
  hash as the default and requires --i-accept-untested-firmware to write
  anything else.

Default: separate mode. Build a themed IPSW with ipod_theme (cloned on
demand into build/ipod_theme, not vendored), install with Finder
Option+Update, no NanoApps untether. Revert with ./start rescue (stock).

Never use NanoVault files.
Preview: https://nano.zeehondie.net/n7g_theme_previewer/
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from . import const
from .compat import (
    Refused,
    die_if_symlink,
    force_utc_timezone,
    have,
    run,
    sha256_bytes,
    sha256_file,
)
from .firmware import build_dir, ensure_stock_ipsw
from .mse import (
    RSRC_NAME,
    extract_rsrc_fat,
    find_section,
    first_changed_offset,
    parse_sections,
    splice_rsrc_fat,
)
from .safety import check_mse


NANOVAULT_MARKERS = (
    "nanovault",
    "nogills",
    "pseudostock",
    "nanolib",
    "burgerbat",
)
HELVETICA_NAMES = {"helvetica.ttf", "helvetica.otf", "helvetica.bin"}
SILVERDB_NAMES = {
    "silverimagesdb.le.bin",
    "silverimagesdb.le.bin2",
}


def preview_url() -> str:
    return const.N7G_THEME_PREVIEWER


def theme_checkout() -> Path:
    return build_dir() / "ipod_theme"


def refuse_nanovault(
    path: Path,
    *,
    what: str = "theme file",
    allow_community_ipsw: bool = False,
) -> None:
    raw = str(Path(path).resolve()) if Path(path).exists() else str(path)
    lowered = raw.replace("\\", "/").lower()
    for marker in NANOVAULT_MARKERS:
        if marker in lowered:
            raise Refused(
                f"refusing {what} {path}: NanoVault / community IPSW dumps are "
                "not used here. Build from Apple's hashed IPSW plus your own "
                "art, or from an ipod_theme checkout this toolkit cloned."
            )
    if allow_community_ipsw:
        # ./start theme import reads art from a community IPSW. The path
        # still cannot look like NanoVault. The filename may say "theme".
        return
    name = Path(path).name.lower()
    if name.endswith(".ipsw") and any(
        token in name for token in ("theme", "pseudostock", "nogills", "nanovault")
    ):
        raise Refused(
            f"refusing {what} {path}: that filename looks like a community "
            "themed IPSW, not Apple's hashed iPod_1.1.2_39A10023.ipsw. "
            "To take art from a community IPSW without flashing it, use "
            "./start theme import FILE"
        )


def would_replace_helvetica(paths: Iterable[Path]) -> Optional[Path]:
    for path in paths:
        if Path(path).name.lower() in HELVETICA_NAMES:
            return Path(path)
    return None


def print_preview() -> int:
    print("n7g theme previewer (browser, does not write a device):")
    print(f"  {preview_url()}")
    print("Open your themed IPSW there before flashing anything.")
    print("2015 colours are partial on that page. Never use NanoVault files.")
    return 0


def print_status() -> int:
    dest = theme_checkout()
    print("Theming vs untether")
    print("-------------------")
    print("Default: SEPARATE mode. ipod_theme themed IPSW via Finder")
    print("Option+Update. No NanoApps. Revert with ./start rescue (stock).")
    print()
    print("Combine: themed SilverImagesDB into the Helvetica rsrc FAT is")
    print("possible IF Helvetica.ttf is left to the CFF carrier. Same-size")
    print("inject splices a new Firmware-themed-untether.MSE. That hash is")
    print("untested. ./start flash still defaults to")
    print(f"  {const.FIRMWARE_MSE_SHA256}")
    print("and needs --i-accept-untested-firmware to write a combined image.")
    print()
    print("ipod_theme  " + const.IPOD_THEME_LICENSE + "  " + const.IPOD_THEME_REPO)
    print("checkout    " + str(dest) + (" (present)" if dest.is_dir() else " (not cloned)"))
    print("preview     " + preview_url())
    print("import      ./start theme import FILE  (art only; never flashes)")
    print("NanoVault   refused")
    return 0


def clone_ipod_theme(*, force: bool = False) -> Path:
    dest = theme_checkout()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_dir() and not force:
        print(f"ipod_theme already at {dest}")
        return dest
    if dest.is_dir() and force:
        shutil.rmtree(dest)
    if not have("git"):
        raise Refused("git is required to clone ipod_theme")
    print(f"==> cloning ipod_theme (GPL-3) -> {dest}")
    print("    credit: nfzerox / ipod_theme. Not vendored in this git tree.")
    r = run(
        ["git", "clone", "--depth", "1", const.IPOD_THEME_REPO, str(dest)],
        check=False,
    )
    if r.returncode != 0 or not dest.is_dir():
        raise Refused("git clone of ipod_theme failed")
    license_file = dest / "LICENSE"
    if license_file.is_file():
        text = license_file.read_text(errors="replace")[:80]
        if "GNU GENERAL PUBLIC LICENSE" not in text and "GPL" not in text:
            print("warning: ipod_theme LICENSE did not look like GPL at a glance")
    print(f"cloned {dest}")
    print("Next: ./start theme unpack   then edit art in that checkout,")
    print("      ./start theme pack     then Finder Option+Update (not Restore).")
    print(f"Preview: {preview_url()}")
    return dest


def unpack_theme_firmware(*, ipsw: Optional[Path] = None) -> Path:
    """Run ipod_theme 01_firmware_unpack_7g against the hashed official IPSW."""
    dest = clone_ipod_theme()
    stock = ensure_stock_ipsw(Path(ipsw) if ipsw else None)
    refuse_nanovault(stock, what="IPSW")
    script = dest / "01_firmware_unpack_7g"
    if not script.is_file():
        raise Refused(f"{script} missing after clone; ipod_theme layout changed")
    env = os.environ.copy()
    print("==> ipod_theme unpack (separate mode, no Helvetica payload)")
    print(f"    IPSW {stock} (hash-verified)")
    print("    This downloads/builds inside build/ipod_theme. Not a device write.")
    # ipod_theme's unpack script fetches its own IPSW. Point it at ours when possible.
    copied = dest / const.STOCK_IPSW_NAME
    if not copied.is_file():
        shutil.copy2(stock, copied)
    r = run(["bash", str(script)], check=False, cwd=dest, env=env)
    if r.returncode != 0:
        raise Refused(
            "ipod_theme unpack failed. Need Rust + arm-none-eabi-gcc + Python "
            "deps (pyfatfs, fonttools, pillow). See that project's README. "
            "Nothing was written to a nano."
        )
    print("UNTESTED: ipod_theme unpack on this computer. Separate mode: no untether.")
    print(f"Preview your art at {preview_url()} before packing.")
    return dest


def pack_theme() -> Path:
    dest = theme_checkout()
    if not dest.is_dir():
        raise Refused("ipod_theme is not cloned. Run ./start theme clone first.")
    helvetica_hit = would_replace_helvetica(dest.rglob("*"))
    if helvetica_hit and "Fonts" in helvetica_hit.parts:
        print(
            "NOTE: ipod_theme will replace Helvetica.ttf. That is fine in "
            "SEPARATE mode (no untether). It is NOT combinable with the "
            "Helvetica CFF payload. ./start theme combine will refuse this."
        )
    script = dest / "03_art_pack.py"
    if not script.is_file():
        raise Refused(f"{script} missing")
    print("==> ipod_theme pack (themed IPSW, stock OSOS, no NanoApps)")
    r = run(["python3", str(script)], check=False, cwd=dest)
    if r.returncode != 0:
        raise Refused("ipod_theme 03_art_pack.py failed. Nothing written to a nano.")
    print()
    print("SEPARATE MODE. Install with Finder: hold Option, click Update")
    print("(not Restore) and pick the *_repack.ipsw in the ipod_theme folder.")
    print("Do not point ./start flash at that IPSW. Revert with ./start rescue.")
    print(f"Preview: {preview_url()}")
    print("UNTESTED on a real nano from this repo.")
    return dest


def combine_theme(
    *,
    silverdb: Optional[Path] = None,
    rsrc: Optional[Path] = None,
    fonts: Optional[Sequence[Path]] = None,
    extra: Optional[Sequence[tuple[str, Path]]] = None,
    mse: Optional[Path] = None,
    accept_untested: bool = False,
    accept_combine: bool = False,
) -> dict:
    """Inject themed SilverImagesDB into rsrc without touching Helvetica.

    Produces build/firmware/rsrc-themed.bin. When --mse is given (the tested
    Helvetica image, or that image already extracted to --rsrc), splices the
    same-size FAT volume back into a new Firmware-themed-untether.MSE. Flash
    still refuses that hash unless --i-accept-untested-firmware is set.
    """
    if not accept_untested or not accept_combine:
        raise Refused(
            "combining a theme with the Helvetica untether produces a new "
            "untested MSE. Pass both --i-accept-untested-firmware and "
            "--i-accept-themed-untether. The default flash path stays the "
            "tested hash. Prefer SEPARATE mode (./start theme pack) plus "
            "./start rescue to revert."
        )
    paths = [p for p in (silverdb, rsrc, mse) if p]
    paths.extend(fonts or [])
    paths.extend(p for _, p in (extra or []))
    for path in paths:
        refuse_nanovault(path)
    hit = would_replace_helvetica(fonts or [])
    if hit:
        raise Refused(
            f"{hit} would replace {const.THEME_HELVETICA_FAT}, which is the "
            "untether carrier. Combining DESTROYS the exploit. Use separate "
            "mode (themed stock IPSW, no untether) or drop Helvetica from the theme."
        )
    if silverdb is None:
        raise Refused(
            "pass --silverdb path/to/SilverImagesDB.LE.bin (from ipod_theme "
            "after 03_art_pack, never from NanoVault)"
        )
    silverdb = Path(silverdb)
    if not silverdb.is_file():
        raise Refused(f"SilverImagesDB missing: {silverdb}")
    die_if_symlink(silverdb, "SilverImagesDB")

    out_dir = build_dir() / "firmware"
    out_dir.mkdir(parents=True, exist_ok=True)
    mse_path = Path(mse) if mse is not None else None
    if mse_path is not None:
        if not mse_path.is_file():
            raise Refused(f"MSE missing: {mse_path}")
        die_if_symlink(mse_path, "MSE")
        # Source must be the tested Helvetica image (or already allowed untested).
        check_mse(mse_path, allow_untested=accept_untested)

    if rsrc is None:
        if mse_path is None:
            print("No --rsrc given. Combine needs the firmware rsrc FAT (rsrc.bin)")
            print("from a Helvetica build, or --mse pointing at the tested image.")
            copied = out_dir / "SilverImagesDB.LE.bin"
            shutil.copy2(silverdb, copied)
            print(f"stored {copied}")
            print("Next: ./start theme combine --silverdb ... --mse "
                  "build/firmware/Firmware-untethered.MSE \\")
            print("    --i-accept-untested-firmware --i-accept-themed-untether")
            print("Default ./start flash still requires the tested MSE hash.")
            return {"silverdb": str(copied), "combined_mse": None, "untested": True}
        print(f"extracting rsrc FAT from {mse_path}")
        fat, _details = extract_rsrc_fat(mse_path.read_bytes())
        rsrc = out_dir / "rsrc-from-mse.bin"
        rsrc.write_bytes(fat)
        print(f"extracted {rsrc} ({len(fat)} bytes)")

    rsrc = Path(rsrc)
    if not rsrc.is_file():
        raise Refused(f"rsrc.bin missing: {rsrc}")
    die_if_symlink(rsrc, "rsrc.bin")
    dest_rsrc = out_dir / "rsrc-themed.bin"
    shutil.copy2(rsrc, dest_rsrc)
    original_rsrc_size = dest_rsrc.stat().st_size
    helvetica_before = _fat_hash(dest_rsrc, const.THEME_HELVETICA_FAT)
    if not helvetica_before:
        raise Refused(
            f"{const.THEME_HELVETICA_FAT} is missing from rsrc.bin. "
            "Combining needs the Helvetica CFF carrier already in the FAT."
        )
    # Same-size inplace overwrite does not restamp. delete+create stamps
    # 'now'; TZ=UTC removes timezone skew (not wall-clock).
    force_utc_timezone()
    silver_mode = _fat_replace(dest_rsrc, const.THEME_SILVERDB_FAT, silverdb)
    for fat_path, src in extra or []:
        if Path(fat_path).name.lower() in HELVETICA_NAMES or fat_path.rstrip("/") == const.THEME_HELVETICA_FAT:
            raise Refused(f"refusing to write {fat_path}: Helvetica is the untether carrier")
        _fat_replace(dest_rsrc, fat_path, src)
    if dest_rsrc.stat().st_size != original_rsrc_size:
        dest_rsrc.unlink(missing_ok=True)
        raise Refused(
            "pyfatfs changed the FAT volume size. Combination aborted so IMG1 "
            "body_length and MSE section offsets stay put."
        )
    helvetica_after = _fat_hash(dest_rsrc, const.THEME_HELVETICA_FAT)
    if helvetica_after != helvetica_before:
        dest_rsrc.unlink(missing_ok=True)
        raise Refused(
            "Helvetica.ttf changed during theme inject. Combination aborted. "
            "rsrc-themed.bin was deleted."
        )
    print(f"rsrc themed: {dest_rsrc}")
    print(f"Helvetica.ttf unchanged ({helvetica_before[:16]}...)")
    print("HARDWARE: this rsrc is untested. It is not the flash default.")
    result = {
        "rsrc": str(dest_rsrc),
        "helvetica_sha256": helvetica_before,
        "silverdb_sha256": sha256_file(silverdb),
        "combined_mse": None,
        "untested": True,
        "silverdb_fat_replace": silver_mode,
        "silverdb_size": silverdb.stat().st_size,
    }
    print(f"SilverImagesDB FAT replace: {silver_mode}")
    if mse_path is not None:
        tested_bytes = mse_path.read_bytes()
        combined = splice_rsrc_fat(tested_bytes, dest_rsrc.read_bytes())
        out_mse = out_dir / "Firmware-themed-untether.MSE"
        out_mse.write_bytes(combined)
        digest = sha256_bytes(combined)
        result["combined_mse"] = str(out_mse)
        result["combined_mse_sha256"] = digest
        result["source_mse"] = str(mse_path)
        print(f"combined MSE: {out_mse}")
        print(f"combined sha256: {digest}")
        print("This is a new untested hash. ./start flash will refuse it")
        print("without --i-accept-untested-firmware. Default flash stays")
        print(f"{const.FIRMWARE_MSE_SHA256}")
        if digest == const.FIRMWARE_MSE_SHA256:
            raise Refused(
                "combined MSE hash unexpectedly matches the tested image; "
                "SilverImagesDB was not actually changed"
            )
        identity = _combine_identity(tested_bytes, combined, dest_rsrc)
        result["identity"] = identity
        _print_combine_identity(identity)
    return result


def import_theme_art(
    source: Path,
    *,
    out_dir: Optional[Path] = None,
    combine: bool = False,
    mse: Optional[Path] = None,
    accept_untested: bool = False,
    accept_combine: bool = False,
) -> dict:
    """Extract SilverImagesDB from a community IPSW/MSE/bin. Never flash it.

    Helvetica.ttf, OSOS, the section table, and IMG1 are left in the
    source file. Combining (optional) injects only the extracted art into
    the tested Helvetica MSE.
    """
    source = Path(source)
    if not source.exists():
        raise Refused(f"theme import source missing: {source}")
    die_if_symlink(source, "theme import source")
    refuse_nanovault(source, what="theme import", allow_community_ipsw=True)
    resolved = _resolve_import_source(source)
    refuse_nanovault(resolved, what="theme import", allow_community_ipsw=True)
    die_if_symlink(resolved, "theme import file")

    dest = Path(out_dir) if out_dir is not None else (build_dir() / "theme-import")
    dest.mkdir(parents=True, exist_ok=True)
    silver_out = dest / "SilverImagesDB.LE.bin"

    report: dict[str, Any] = {
        "source": str(resolved),
        "source_kind": None,
        "silverdb": str(silver_out),
        "combined_mse": None,
        "warnings": [],
    }

    kind, silver, extra = _load_import_art(resolved)
    report["source_kind"] = kind
    report.update(extra)
    for warning in extra.get("warnings") or []:
        print(f"NOTE: {warning}")
    silver_out.write_bytes(silver)
    report["silverdb_sha256"] = sha256_bytes(silver)
    report["silverdb_size"] = len(silver)
    print(f"imported art: {silver_out}")
    print(f"  {len(silver)} bytes  sha256 {report['silverdb_sha256']}")
    print("This is SilverImagesDB only. Helvetica.ttf was not copied.")
    print("Nothing was written to a nano. This file is not a flash image.")
    if extra.get("osos_disk_name_swap"):
        print("NOTE: source MSE swaps osos/disk names (ipod_theme unsigned boot).")
        print("      Combine will not copy that table. The Helvetica MSE keeps")
        print("      its own section names and OSOS.")
    if extra.get("osos_bytes_at_tested_offsets_identical") is True:
        print("NOTE: 39A10023 OSOS bytes at the tested file offsets are identical.")
    elif extra.get("osos_bytes_at_tested_offsets_identical") is False:
        print(
            "NOTE: OSOS bytes at the tested file offsets DIFFER from the "
            "Helvetica image. Combine still does not copy OSOS; it only takes art."
        )
    if extra.get("img1_format") not in (None, 0x03):
        print(
            f"NOTE: source rsrc IMG1 format is 0x{extra['img1_format']:02x} "
            "(stock-signed). Art was read with signature skip off. Combine "
            "still splices into the tested 0x03 Helvetica image."
        )
    if extra.get("helvetica"):
        helv = extra["helvetica"]
        if helv.get("sha256") != const.TESTED_HELVETICA_SHA256:
            print(
                "NOTE: source Helvetica.ttf is NOT the CFF untether carrier "
                f"({helv.get('size')} bytes, sha256 {helv.get('sha256', '')[:16]}...). "
                "It was not imported."
            )
        if not helv.get("has_cff"):
            print(
                "NOTE: source Helvetica is a stock-style TrueType (no CFF). "
                "Replacing the tested font with it would destroy the untether."
            )
    if len(silver) != const.TESTED_SILVERDB_SIZE:
        delta = len(silver) - const.TESTED_SILVERDB_SIZE
        print(
            f"NOTE: SilverImagesDB is {delta:+d} bytes vs the tested image "
            f"({const.TESTED_SILVERDB_SIZE}). Combine will delete+create inside "
            "the existing FAT volume (Helvetica clusters stay if free space fits)."
        )

    report_path = dest / "import-report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"report: {report_path}")
    print(f"Preview: {preview_url()}")
    print("Next: ./start theme combine --silverdb "
          f"{silver_out} --mse build/firmware/Firmware-untethered.MSE \\")
    print("         --i-accept-untested-firmware --i-accept-themed-untether")

    if combine:
        mse_path = Path(mse) if mse is not None else (
            build_dir() / "firmware" / "Firmware-untethered.MSE"
        )
        combined = combine_theme(
            silverdb=silver_out,
            mse=mse_path,
            accept_untested=accept_untested,
            accept_combine=accept_combine,
        )
        report["combined_mse"] = combined.get("combined_mse")
        report["combined_mse_sha256"] = combined.get("combined_mse_sha256")
        report["identity"] = combined.get("identity")
        report["silverdb_fat_replace"] = combined.get("silverdb_fat_replace")
        report_path.write_text(json.dumps(report, indent=2) + "\n")
    return report


def _resolve_import_source(source: Path) -> Path:
    if source.is_file():
        return source
    if not source.is_dir():
        raise Refused(f"theme import source is not a file or directory: {source}")
    silvers = [
        p
        for p in source.iterdir()
        if p.is_file() and p.name.lower() in SILVERDB_NAMES
    ]
    if silvers:
        return silvers[0]
    ipsws = sorted(p for p in source.iterdir() if p.is_file() and p.suffix.lower() == ".ipsw")
    if ipsws:
        preferred = [p for p in ipsws if "2015" in p.name.lower()]
        chosen = preferred[0] if preferred else ipsws[0]
        if len(ipsws) > 1:
            print(
                f"NOTE: {len(ipsws)} IPSWs in {source}; using {chosen.name} "
                "(prefer a *_2015.ipsw for MKN52 / 39A10023 wrapper)."
            )
        return chosen
    mses = [
        p
        for p in source.iterdir()
        if p.is_file()
        and (p.name.lower() == "firmware.mse" or p.suffix.lower() == ".mse")
    ]
    if mses:
        return mses[0]
    raise Refused(
        f"{source} has no IPSW, Firmware.MSE, or SilverImagesDB.LE.bin to import"
    )


def _load_import_art(path: Path) -> tuple[str, bytes, dict[str, Any]]:
    extra: dict[str, Any] = {"warnings": []}
    data = path.read_bytes()
    name = path.name.lower()
    if name in SILVERDB_NAMES or _looks_like_silverdb(data):
        if not _looks_like_silverdb(data):
            raise Refused(f"{path} is named like SilverImagesDB but magic is not paMB")
        extra["warnings"].append("source is a raw SilverImagesDB; no IPSW/MSE inspect")
        return "silverdb", data, extra
    if name.endswith(".ipsw") or zipfile.is_zipfile(path):
        mse = _mse_from_ipsw(path)
        extra["ipsw"] = str(path)
        extra["ipsw_sha256"] = sha256_bytes(data)
        extra["ipsw_size"] = len(data)
        extra["ipsw_members"] = _ipsw_member_names(path)
        kind = "ipsw"
        return kind, _silver_from_mse(mse, extra), extra
    if name.endswith(".mse") or name == "firmware.mse":
        extra["mse_sha256"] = sha256_bytes(data)
        extra["mse_size"] = len(data)
        return "mse", _silver_from_mse(data, extra), extra
    raise Refused(
        f"{path} is not an IPSW, Firmware.MSE, or SilverImagesDB.LE.bin"
    )


def _ipsw_member_names(ipsw: Path) -> list[str]:
    with zipfile.ZipFile(ipsw) as zf:
        return zf.namelist()


def _mse_from_ipsw(ipsw: Path) -> bytes:
    with zipfile.ZipFile(ipsw) as zf:
        names = [
            n for n in zf.namelist() if n.rsplit("/", 1)[-1].lower() == "firmware.mse"
        ]
        if len(names) != 1:
            raise Refused(f"{ipsw.name} has {len(names)} Firmware.MSE members")
        return zf.read(names[0])


def _looks_like_silverdb(data: bytes) -> bool:
    return len(data) >= 16 and data[:4] == b"\x03\x00\x00\x00" and data[12:16] == b"paMB"


def _silver_from_mse(mse: bytes, extra: dict[str, Any]) -> bytes:
    extra["mse_sha256"] = extra.get("mse_sha256") or sha256_bytes(mse)
    extra["mse_size"] = extra.get("mse_size") or len(mse)
    extra["osos_disk_name_swap"] = _osos_disk_name_swap(mse)
    fat, details = extract_rsrc_fat(mse, require_patched=False)
    extra["img1_format"] = details["img1"]["format"]
    extra["img1_body_length"] = details["img1"]["body_length"]
    extra["rsrc_dev_offset"] = details["section"]["dev_offset"]
    with tempfile.NamedTemporaryFile(prefix="nano7kit-import-fat-", suffix=".bin") as tmp:
        tmp.write(fat)
        tmp.flush()
        fat_path = Path(tmp.name)
        silver = _fat_read(fat_path, const.THEME_SILVERDB_FAT)
        extra["helvetica"] = _helvetica_info(fat_path)
        extra["silver_clusters"] = _fat_meta(fat_path, const.THEME_SILVERDB_FAT)
        tested = build_dir() / "firmware" / "Firmware-untethered.MSE"
        if tested.is_file() and tested.stat().st_size == len(mse):
            extra["osos_bytes_at_tested_offsets_identical"] = _osos_at_tested_offsets(
                mse, tested.read_bytes()
            )
    if not _looks_like_silverdb(silver):
        raise Refused("extracted SilverImagesDB magic is not paMB")
    extra["file_count_hint"] = "SilverImagesDB extracted; Helvetica was not copied"
    return silver


def _osos_disk_name_swap(mse: bytes) -> bool:
    """ipod_theme unsigned boot: the 690k disk image is named osos."""
    try:
        sections = parse_sections(mse)
    except Refused:
        return False
    by_name = {s["name_ascii"]: s for s in sections}
    osos = by_name.get("osos")
    disk = by_name.get("disk")
    if osos is None or disk is None:
        return False
    # Tested image: disk is the 690,195-byte stub, osos is ~9.9 MB RetailOS.
    # ipod_theme unsigned boot swaps those names so the stub is what boots.
    return osos["length"] < 1_000_000 and disk["length"] > 1_000_000


def _osos_at_tested_offsets(candidate: bytes, tested: bytes) -> bool:
    osos = find_section(tested, b"soso")
    end = osos["body_end"]
    if end > len(candidate) or end > len(tested):
        return False
    return candidate[osos["body_start"] : end] == tested[osos["body_start"] : end]


def _helvetica_info(rsrc: Path) -> Optional[dict[str, Any]]:
    try:
        data = _fat_read(rsrc, const.THEME_HELVETICA_FAT)
    except Refused:
        return None
    tables = _sfnt_tables(data)
    return {
        "size": len(data),
        "sha256": sha256_bytes(data),
        "has_cff": "CFF " in tables,
        "tables": tables,
        "matches_tested_cff": sha256_bytes(data) == const.TESTED_HELVETICA_SHA256,
    }


def _sfnt_tables(data: bytes) -> list[str]:
    if len(data) < 12:
        return []
    count = struct.unpack(">H", data[4:6])[0]
    tags = []
    for index in range(count):
        offset = 12 + index * 16
        if offset + 4 > len(data):
            break
        tags.append(data[offset : offset + 4].decode("ascii", errors="replace"))
    return tags


def _combine_identity(tested: bytes, combined: bytes, dest_rsrc: Path) -> dict[str, Any]:
    osos = find_section(tested, b"soso")
    rsrc = find_section(tested, RSRC_NAME)
    helv = _fat_meta(dest_rsrc, const.THEME_HELVETICA_FAT)
    silver = _fat_meta(dest_rsrc, const.THEME_SILVERDB_FAT)
    fat, details = extract_rsrc_fat(combined, require_patched=True)
    return {
        "size_identical": len(combined) == len(tested) == const.FIRMWARE_MSE_SIZE,
        "header_identical": combined[:0x5000] == tested[:0x5000],
        "section_table_identical": combined[0x5000:0x5000 + 16 * 40]
        == tested[0x5000:0x5000 + 16 * 40],
        "osos_identical": combined[osos["body_start"] : osos["body_end"]]
        == tested[osos["body_start"] : osos["body_end"]],
        "osos_sha256": sha256_bytes(combined[osos["body_start"] : osos["body_end"]]),
        "rsrc_img1_head_identical": combined[rsrc["body_start"] : rsrc["body_start"] + 0x54]
        == tested[rsrc["body_start"] : rsrc["body_start"] + 0x54],
        "img1_format": details["img1"]["format"],
        "first_changed_offset": first_changed_offset(tested, combined),
        "helvetica_sha256": _fat_hash(dest_rsrc, const.THEME_HELVETICA_FAT),
        "helvetica_matches_tested": _fat_hash(dest_rsrc, const.THEME_HELVETICA_FAT)
        == const.TESTED_HELVETICA_SHA256,
        "helvetica_clusters": helv,
        "silver_clusters": silver,
        "bytes_outside_rsrc_identical": (
            combined[: rsrc["head_start"]] == tested[: rsrc["head_start"]]
            and combined[rsrc["body_end"] :] == tested[rsrc["body_end"] :]
        ),
        "fat_sha256": sha256_bytes(fat),
    }


def _print_combine_identity(identity: dict[str, Any]) -> None:
    print("identity vs tested Helvetica MSE:")
    flags = [
        ("size", identity["size_identical"]),
        ("MSE header", identity["header_identical"]),
        ("section table", identity["section_table_identical"]),
        ("OSOS", identity["osos_identical"]),
        ("rsrc IMG1 head", identity["rsrc_img1_head_identical"]),
        ("bytes outside rsrc", identity["bytes_outside_rsrc_identical"]),
        ("Helvetica.ttf", identity["helvetica_matches_tested"]),
    ]
    for label, ok in flags:
        print(f"  {label}: {'identical' if ok else 'CHANGED'}")
    print(f"  IMG1 format: 0x{identity['img1_format']:02x} (want 0x03)")
    print(f"  first changed offset: {identity['first_changed_offset']}")
    helv = identity.get("helvetica_clusters") or {}
    silver = identity.get("silver_clusters") or {}
    print(
        f"  Helvetica clusters: first={helv.get('first_cluster')} "
        f"count={helv.get('cluster_count')} size={helv.get('size')}"
    )
    print(
        f"  SilverImagesDB clusters: first={silver.get('first_cluster')} "
        f"count={silver.get('cluster_count')} size={silver.get('size')}"
    )


def _pyfat():
    try:
        from pyfatfs import PyFatFS  # type: ignore
    except ImportError:
        venv = build_dir() / "venv" / "bin" / "python"
        raise Refused(
            "pyfatfs is required to inject a theme into rsrc.bin. "
            f"Use the firmware venv ({venv}) or pip install pyfatfs. "
            "Run ./start deps / ./start build-firmware first."
        ) from None
    return PyFatFS


def _fat_read(rsrc: Path, fat_path: str) -> bytes:
    PyFatFS = _pyfat()
    fs = PyFatFS.PyFatFS(str(rsrc), read_only=True)
    try:
        with fs.openbin(fat_path, mode="rb") as handle:
            return handle.read()
    except Exception as exc:
        raise Refused(f"could not read {fat_path} from {rsrc}: {exc}") from exc
    finally:
        fs.close()


def _fat_hash(rsrc: Path, fat_path: str) -> str:
    return sha256_bytes(_fat_read(rsrc, fat_path))


def _fat_meta(rsrc: Path, fat_path: str) -> dict[str, Any]:
    PyFatFS = _pyfat()
    fs = PyFatFS.PyFatFS(str(rsrc), read_only=True)
    try:
        entry = fs.fs.root_dir.get_entry(fat_path)
        chain = list(fs.fs.get_cluster_chain(entry.get_cluster()))
        return {
            "size": entry.get_size(),
            "first_cluster": entry.get_cluster(),
            "cluster_count": len(chain),
            "chain_sha256": sha256_bytes(struct.pack(f"<{len(chain)}I", *chain)),
        }
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        fs.close()


def _fat_replace(rsrc: Path, fat_path: str, src: Path) -> str:
    """Write ``src`` into ``fat_path``. Returns ``inplace`` or ``realloc``.

    Same-size payloads are overwritten in place so the cluster chain and
    directory slot stay put (Helvetica's clusters are not reshuffled). A
    size change falls back to delete+create, which uses free clusters and
    is the same method ipod_sun uses for the CFF font.
    """
    force_utc_timezone()
    PyFatFS = _pyfat()
    payload = Path(src).read_bytes()
    fs = PyFatFS.PyFatFS(str(rsrc), read_only=False)
    try:
        existing = None
        try:
            existing = fs.getsize(fat_path)
        except Exception:
            existing = None
        if existing == len(payload):
            with fs.openbin(fat_path, mode="r+") as handle:
                handle.seek(0)
                written = handle.write(payload)
            if written != len(payload):
                raise Refused(
                    f"short write replacing {fat_path}: {written} != {len(payload)}"
                )
            return "inplace"
        try:
            fs.remove(fat_path)
        except Exception:
            pass
        with fs.openbin(fat_path, mode="wb") as handle:
            handle.write(payload)
        return "realloc"
    finally:
        fs.close()


def revert_instructions() -> int:
    print("Easy revert from a theme or a combined untested MSE:")
    print("  1. ./start backup          (if the nano still enumerates as a disk)")
    print("  2. ./start rescue --dry-run --serial YOUR_SERIAL")
    print("  3. ./start rescue --run --typed-confirm RESCUE --serial YOUR_SERIAL")
    print("That writes STOCK 1.1.2 / 39A10023, not the Helvetica image.")
    print("Finder Option+Restore of the hashed IPSW in build/ is last-resort")
    print("boot recovery and creates HFS+; follow with ./start fat32.")
    print("Do not flash NanoVault IPSWs.")
    return 0
