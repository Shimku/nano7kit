"""Select which NanoApps sit on the Home screen.

The silver_resident loader does not scan /Apps/Executables. It reads
/Apps/AllApps.pack (HBPK), registers up to 64 apps, and launches
/Apps/Executables/<CFBundleName>.hbapp with /Apps/Icons/<CFBundleName>.bin.

Deleting an .hbapp without rewriting the pack leaves a broken Home icon.
Removing is safe only when this module filters the pack and deletes the
matching executable and icon together.

Startup files (resident, ab, resident-A, resident-B) are not user-removable.
The hardware-tested pack is Blocks + Game Boy + Probe. A subset pack is a
new hash and is untested as a unit; the individual app blobs stay tested.

MKN52: adding the eight FIRST_SLICE extras as a set wrote an
11-app pack (APPS_OK, all 11 icons on Home) then reboot-looped after
about 1-2 s of UI. Disk Mode remove of those 8 restored the 3-app pack.
Cause: silver_resident hb_app_register_pack grows the OS bitmap cache
by 1 MiB when pack count > 8 (the 3-app pack never does) and then
ensure_capacity / add_item / trim_pages / home_reload. Not pack size
(labels+screens << 1 MiB), not Probe heap (8.7 MB free / 8 MB largest
with 3 apps), not a Calendar boot widget (apps launch on tap). apps add
takes one extra per reboot. Packs known to loop are not written.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from . import const
from .compat import Refused, root, sha256_bytes, sha256_file
from .fat import (
    assert_not_hfs,
    copy_verified,
    delete,
    exists,
    mkdir_p,
    need_mtools,
    unmount,
)
from .safety import check_backup, check_disk_size, check_usb_serial, layout_for_disk_size


PACK_MAGIC = 0x4B504248  # HBPK
BUNDLE_MAGIC = 0x50414248  # HBAP
PACK_HDR = "<III"
BUNDLE_HDR = "<IHHIIIIIIIIIII"
BUNDLE_HDR_SIZE = struct.calcsize(BUNDLE_HDR)  # 52

HB_TRACE_MAGIC = 0x48425452  # 'HBTR'
HB_TRACE_MAX = 256
HB_TRACE_HEADER = 16
HB_TRACE_ENTRY = 16
LASTTRACE_FAT = "/Apps/Startup/lasttrace.bin"

RECOVERY_HINT = (
    "RECOVERY (no extra prompt): Disk Mode (hold Sleep/Wake and Home; at the Apple "
    "logo switch to Volume Up and Volume Down until it says Do not disconnect). Then: "
    "./start apps lasttrace && ./start apps restore-tested"
)


def _align4(n: int) -> int:
    return (n + 3) & ~3


def parse_pack(data: bytes) -> List[bytes]:
    if len(data) < 12:
        raise Refused("AllApps.pack is shorter than an HBPK header")
    magic, version, count = struct.unpack_from(PACK_HDR, data, 0)
    if magic != PACK_MAGIC:
        raise Refused(f"AllApps.pack magic {magic:#x} is not HBPK")
    if version != 1:
        raise Refused(f"AllApps.pack version {version} is not 1")
    if count > 64:
        raise Refused(f"AllApps.pack claims {count} apps; resident cap is 64")
    bundles: List[bytes] = []
    for i in range(count):
        off = struct.unpack_from("<I", data, 12 + 4 * i)[0]
        if off + 48 > len(data):
            raise Refused(f"AllApps.pack bundle {i} offset {off} is past the file")
        blen = struct.unpack_from("<I", data, off + 44)[0]
        if blen < BUNDLE_HDR_SIZE or off + blen > len(data):
            raise Refused(f"AllApps.pack bundle {i} length {blen} is not readable")
        bundles.append(data[off : off + blen])
    return bundles


def bundle_name(bundle: bytes) -> str:
    if len(bundle) < BUNDLE_HDR_SIZE:
        raise Refused("HBAP bundle is truncated")
    fields = struct.unpack_from(BUNDLE_HDR, bundle, 0)
    magic = fields[0]
    if magic != BUNDLE_MAGIC:
        raise Refused(f"bundle magic {magic:#x} is not HBAP")
    name_off = fields[11]
    if name_off >= len(bundle):
        raise Refused("HBAP name_off is past the bundle")
    end = bundle.find(b"\x00", name_off)
    if end < 0:
        raise Refused("HBAP name is not NUL-terminated")
    return bundle[name_off:end].decode("ascii", "replace")


def pack_app_names(data: bytes) -> List[str]:
    return [bundle_name(b) for b in parse_pack(data)]


def print_recovery_hint() -> None:
    print(RECOVERY_HINT)


def extras_named(names: Sequence[str]) -> List[str]:
    from .app_catalog import extras_in

    return extras_in(names)


def pack_is_looping(names: Sequence[str], *, nogrow: bool = False) -> Optional[str]:
    """Why this AllApps.pack must not be written, or None if allowed."""
    from .app_catalog import looping_pack_names
    from .cap import STOCK_PACK_LIMIT, pack_limit

    ordered = [n for n in names if n]
    unique = list(dict.fromkeys(ordered))
    looping = looping_pack_names()
    if set(unique) == looping:
        return (
            f"AllApps.pack with {len(unique)} apps "
            f"({', '.join(sorted(unique))}) is the MKN52 reboot-loop "
            "set (tested three plus the eight FIRST_SLICE extras). "
            "11 icons appeared, then ~1-2 s of UI, then reboot. "
            "nogrow does not make this set writable. "
            + RECOVERY_HINT
        )
    if looping.issubset(set(unique)) and len(unique) >= len(looping):
        return (
            "AllApps.pack contains the looping extras plus more. "
            "That set reboot-looped on MKN52. "
            + RECOVERY_HINT
        )
    limit = pack_limit(nogrow=nogrow)
    if len(unique) > limit:
        if nogrow:
            return (
                f"AllApps.pack would have {len(unique)} apps; nogrow cap is "
                f"{limit} (registrar still 64). "
                + RECOVERY_HINT
            )
        return (
            f"AllApps.pack would have {len(unique)} apps; silver_resident "
            f"calls hb_silver_icon_grow_cache(1 MiB) when count > "
            f"{STOCK_PACK_LIMIT} (the path the 11-app pack took). "
            "Enable the nogrow overlay first: ./start apps cap enable "
            "(untested on this MKN52; Sleep/Wake+Home on the current pack first). "
            "Hardware-safe pack is 3 apps. Add one extra per reboot. "
            + RECOVERY_HINT
        )
    return None


def assert_pack_writable(names: Sequence[str], *, nogrow: bool = False) -> List[str]:
    unique = list(dict.fromkeys(n for n in names if n))
    reason = pack_is_looping(unique, nogrow=nogrow)
    if reason:
        raise Refused(reason)
    return unique


def assert_one_extra_add(names: Sequence[str]) -> List[str]:
    """apps add writes one extra per invocation. Tested three may be named too."""
    from .app_catalog import LOWEST_RISK_EXTRA

    extras = extras_named(names)
    if len(extras) > 1:
        raise Refused(
            "apps add takes one extra at a time. MKN52: adding "
            "Calculator Notes Files Snake Tetris G2048 Paint Calendar "
            "together printed APPS_OK, showed 11 icons, then reboot-looped. "
            f"Named extras: {', '.join(extras)}. "
            f"Lowest risk: {LOWEST_RISK_EXTRA} "
            f"(./start apps add {LOWEST_RISK_EXTRA}). "
            + RECOVERY_HINT
        )
    return extras


def merge_if_writable(base: bytes, extras: Sequence[bytes]) -> tuple[bytes, bool]:
    """Merge extra bundles unless the result is a known looping pack.

    Returns (pack, kept_extras). Used by install-untether so a looping
    on-disk pack is not rewritten.
    """
    if not extras:
        return base, False
    candidate = merge_bundles(base, extras)
    reason = pack_is_looping(pack_app_names(candidate), nogrow=False)
    if reason:
        return base, False
    return candidate, True


def decode_lasttrace(data: bytes) -> dict:
    """Decode silver_resident /Apps/Startup/lasttrace.bin (previous-boot HBTR)."""
    if len(data) < HB_TRACE_HEADER:
        raise Refused(
            f"lasttrace.bin is {len(data)} bytes; need at least {HB_TRACE_HEADER}"
        )
    magic, widx, count, seq = struct.unpack_from("<4I", data, 0)
    if magic != HB_TRACE_MAGIC:
        raise Refused(f"lasttrace.bin magic {magic:#x} is not HBTR (0x48425452)")
    n = min(int(count), HB_TRACE_MAX)
    first = (int(widx) - n) % HB_TRACE_MAX if n else 0
    entries = []
    for i in range(n):
        idx = (first + i) % HB_TRACE_MAX
        off = HB_TRACE_HEADER + HB_TRACE_ENTRY * idx
        if off + HB_TRACE_ENTRY > len(data):
            break
        tag, v1, v2 = struct.unpack_from("<8sII", data, off)
        entries.append(
            {
                "i": int(count) - n + i,
                "tag": tag.decode("latin-1").rstrip("\x00"),
                "v1": int(v1),
                "v2": int(v2),
            }
        )
    tags = [e["tag"].rstrip() for e in entries]
    return {
        "magic": int(magic),
        "write_idx": int(widx),
        "count": int(count),
        "seq": int(seq),
        "entries": entries,
        "tags": tags,
        "size": len(data),
    }


def format_lasttrace(decoded: dict, *, limit: int = 0) -> str:
    entries = decoded.get("entries") or []
    if limit and len(entries) > limit:
        entries = entries[-limit:]
    lines = [
        f"boot seq {decoded.get('seq')}: {decoded.get('count')} entries logged, "
        f"showing last {len(entries)}"
    ]
    for e in entries:
        lines.append(
            f"{e['i']:6d}  {e['tag']:<8}  {e['v1']:#010x}  {e['v2']:#010x}"
        )
    tags = decoded.get("tags") or []
    if tags:
        lines.append("tags: " + " ".join(tags[-16:]))
    interesting = [
        t
        for t in tags
        if t.strip() in {"RESIDENT", "ARMED", "APPSRC", "PKICON", "PKAPP", "PKSCRN", "APPSREG"}
    ]
    if interesting:
        lines.append("resident: " + " ".join(interesting[-12:]))
    return "\n".join(lines)


def build_pack(bundles: Sequence[bytes]) -> bytes:
    n = len(bundles)
    if n > 64:
        raise Refused("cannot pack more than 64 apps")
    head = _align4(12 + 4 * n)
    offs: List[int] = []
    blob = bytearray()
    cur = head
    for b in bundles:
        offs.append(cur)
        blob += b
        pad = _align4(len(b)) - len(b)
        blob += b"\x00" * pad
        cur += len(b) + pad
    out = bytearray(head) + blob
    struct.pack_into(PACK_HDR, out, 0, PACK_MAGIC, 1, n)
    for i, o in enumerate(offs):
        struct.pack_into("<I", out, 12 + 4 * i, o)
    return bytes(out)


YIELD_FLAG = "--i-accept-untested-pack"
YIELD_REFUSE = (
    "apps yield drops a hardware-tested app from Home (untested "
    f"subset pack). Pass {YIELD_FLAG}. "
    "A/B without that flag: ./start apps remove Blocks --keep-files "
    "then ./start apps add Blocks. "
    "Rollback: ./start apps restore-tested."
)


def assert_yield_flag(names: Sequence[str], accept_untested_pack: bool) -> List[str]:
    selected = normalize_selection(names, default_all=False, allow_extra=True)
    if not selected:
        raise Refused("name the apps to hide from Home (default Blocks)")
    if any(n in const.TESTED_APPS for n in selected) and not accept_untested_pack:
        raise Refused(YIELD_REFUSE)
    return selected


def pack_drop_plan(
    data: bytes,
    names: Sequence[str],
    *,
    keep_files: bool = False,
) -> dict:
    """Rewrite a pack without named apps. Host-only; does not touch FAT."""
    selected = normalize_selection(names, default_all=False, allow_extra=True)
    if not selected:
        raise Refused("name the apps to drop from Home")
    for name in selected:
        if is_protected_name(name):
            raise Refused(
                f"{name} is a startup/resident/loader file and cannot be removed. "
                "Copy a new resident only via ./start install-untether."
            )
    current = pack_app_names(data)
    keep = [n for n in current if n not in selected]
    missing = [n for n in selected if n not in current]
    drop = [n for n in selected if n in current]
    rebuilt = _filter_existing(data, keep)
    return {
        "drop": drop,
        "keep": keep,
        "missing": missing,
        "pack": rebuilt,
        "keep_files": bool(keep_files),
        "tested_pack": keep == tested_app_names(),
        "drops_tested": any(n in const.TESTED_APPS for n in drop),
    }


def plan_yield(
    names: Sequence[str],
    *,
    pack: Optional[bytes] = None,
) -> dict:
    data = pack if pack is not None else tested_pack_bytes()
    plan = pack_drop_plan(data, names, keep_files=True)
    plan["assumed_tested_pack"] = pack is None
    return plan


def format_yield_plan(plan: dict) -> str:
    from .perf import ADD_BLOCKS, RESTORE, YIELD_BLOCKS

    lines = [
        "nano7kit apps yield (plan, host only, no NAND)",
        "Rewrites AllApps.pack so named apps leave Home. Executables stay.",
        "This is a pack hide, not a runtime suspend. Blocks does not tick",
        "behind Paint.",
        "",
        "  drop      " + (", ".join(plan.get("drop") or []) or "(none)"),
        "  keep      " + (", ".join(plan.get("keep") or []) or "(empty pack)"),
        "  files     stay on disk (not deleted)",
        "  live      " + YIELD_BLOCKS,
        "  re-add    " + ADD_BLOCKS,
        "  rollback  " + RESTORE,
    ]
    if plan.get("missing"):
        lines.append("  missing   " + ", ".join(plan["missing"]))
    if plan.get("assumed_tested_pack"):
        lines.append("  note      plan used the tested 3-app pack; live Home may differ")
    if plan.get("drops_tested"):
        lines.append(
            "  flag      live write needs "
            + YIELD_FLAG
            + " (subset pack is untested as a unit)"
        )
    lines.append("")
    lines.append("Save data: /Apps/Data/Blocks/world.bin is not in the pack.")
    return "\n".join(lines)


def filter_pack(data: bytes, keep: Iterable[str]) -> bytes:
    keep_set = list(dict.fromkeys(keep))
    wanted = set(keep_set)
    selected = []
    seen = set()
    for bundle in parse_pack(data):
        name = bundle_name(bundle)
        if name in wanted:
            selected.append(bundle)
            seen.add(name)
    missing = [n for n in keep_set if n not in seen]
    if missing:
        raise Refused(
            "AllApps.pack does not contain: "
            + ", ".join(missing)
            + ". Known names: "
            + ", ".join(pack_app_names(data) or ["(none)"])
        )
    return build_pack(selected)


def catalog() -> List[dict]:
    from .app_catalog import catalog as full_catalog

    return full_catalog()


def tested_app_names() -> List[str]:
    return list(const.TESTED_APPS.keys())


def normalize_selection(
    names: Optional[Sequence[str]],
    *,
    default_all: bool = True,
    allow_extra: bool = False,
) -> List[str]:
    """Resolve app names.

    `all` / `*` is the hardware-tested three (Blocks, Game Boy, Probe), never
    the fifty extras. Unknown names are refused. Extras are allowed when
    allow_extra is set (apps add / remove / pick).
    """
    from .app_catalog import addable_names, resolve_name, known_names

    if names is None:
        return tested_app_names() if default_all else []
    out: List[str] = []
    for raw in names:
        token = str(raw).strip()
        if not token:
            continue
        if token.lower() in {"all", "*"}:
            return tested_app_names()
        if token.lower() in {"extras", "first", "slice"}:
            from .app_catalog import LOWEST_RISK_EXTRA

            raise Refused(
                "the first extras slice (Calculator Notes Files Snake Tetris "
                "G2048 Paint Calendar) reboot-looped on MKN52 "
                "(11 icons, then ~1-2 s of UI, then reboot). "
                "apps add takes one extra per reboot. "
                f"Start with {LOWEST_RISK_EXTRA}: "
                f"./start apps add {LOWEST_RISK_EXTRA}. "
                + RECOVERY_HINT
            )
        key = resolve_name(token)
        if key is None:
            raise Refused(
                f"unknown app {token!r}. Hardware-tested: "
                + ", ".join(tested_app_names())
                + ". Built extras: "
                + ", ".join(addable_names()[:12])
                + ("..." if len(addable_names()) > 12 else "")
                + ". See ./start apps list."
            )
        if not allow_extra and key not in const.TESTED_APPS:
            if key not in known_names():
                raise Refused(f"unknown app {token!r}")
            raise Refused(
                f"{key} is not in the hardware-tested three. "
                f"Use ./start apps add {key} (Disk Mode). "
                f"`--all` stays Blocks, Game Boy, Probe."
            )
        if key not in out:
            out.append(key)
    return out


def is_protected_name(name: str) -> bool:
    base = Path(name).stem
    lowered = str(name).strip().lower()
    if base in const.STARTUP_PROTECTED or name in const.STARTUP_PROTECTED:
        return True
    if lowered in {
        "resident.hbapp",
        "ab.hbapp",
        "resident-a.hbapp",
        "resident-b.hbapp",
        "allapps.pack",
        "lasttrace.bin",
        "heapdiag.bin",
        "disabled",
    }:
        return True
    if lowered.startswith("resident"):
        return True
    return False


def tested_pack_bytes() -> bytes:
    path = const.artifact("artifacts/device/AllApps.pack")
    if not path.is_file():
        raise Refused("hardware-tested AllApps.pack is missing")
    data = path.read_bytes()
    digest = sha256_bytes(data)
    if digest != const.ALLAPPS_PACK_SHA256:
        raise Refused(
            f"AllApps.pack hash {digest} != tested {const.ALLAPPS_PACK_SHA256}"
        )
    return data


def pack_for_selection(names: Sequence[str]) -> tuple[bytes, str, bool]:
    """Return (pack_bytes, sha256, is_hardware_tested_pack)."""
    selected = normalize_selection(names, default_all=False)
    data = tested_pack_bytes()
    if set(selected) == set(tested_app_names()):
        return data, const.ALLAPPS_PACK_SHA256, True
    rebuilt = filter_pack(data, selected)
    return rebuilt, sha256_bytes(rebuilt), False


def loader_files_for(names: Sequence[str]) -> List[tuple[str, str]]:
    """(repo-relative under untether/, FAT path) including rebuilt pack later."""
    selected = normalize_selection(names, default_all=False, allow_extra=True)
    files = [
        ("artifacts/device/Startup/resident.hbapp", "/Apps/Startup/resident.hbapp"),
    ]
    by = {row["name"]: row for row in catalog()}
    for name in selected:
        row = by.get(name) or const.TESTED_APPS.get(name)
        if not row:
            continue
        files.append((row["executable"], row["fat_exe"]))
        files.append((row["icon"], row["fat_icon"]))
    return files


def _flag_row(row: dict, chosen: set, disk: Optional[set]) -> str:
    flags = []
    if row.get("hardware_safe") or row.get("tested"):
        flags.append("hardware-safe")
        flags.append("hardware-tested")
    elif row.get("origin") == "built":
        flags.append("built")
        flags.append(row.get("risk_label") or "untested")
    elif row.get("needs_rom") or row.get("needs_payload"):
        flags.append("needs-rom" if row.get("needs_rom") else "needs-payload")
        if row.get("large_gl"):
            flags.append("slot-B")
    elif row.get("buildable"):
        flags.append("builds-on-add")
    else:
        flags.append("not-built")
    if row.get("compatible"):
        flags.append("compatible")
    else:
        flags.append("not-compatible")
    if disk is not None:
        flags.append("installed" if row["name"] in disk else "not-installed")
    if row["name"] in chosen:
        flags.append("selected")
    return " ".join(flags)


def format_list(*, on_disk: Optional[List[str]] = None, selected: Optional[Sequence[str]] = None) -> str:
    from .app_catalog import (
        FIRST_SLICE,
        GROW_CACHE_LIMIT,
        HARNESSES,
        LOWEST_RISK_EXTRA,
        SUGGESTED_HOME,
        SYSTEM_NOT_HOME,
        TEST_ORDER_EXTRAS,
    )

    lines = [
        "App discovery: silver_resident reads /Apps/AllApps.pack (HBPK),",
        "then launches /Apps/Executables/<CFBundleName>.hbapp.",
        "Removing an app is safe only if the pack is rewritten.",
        "Startup/resident/ab files are not removable (no apps remove --loader).",
        "Helvetica silver_resident / firmware 39A10023.",
        "Hardware-safe on this MKN52: Blocks, Game Boy, Probe.",
        "MKN52: adding the eight FIRST_SLICE extras as a set wrote 11 Home",
        "icons (APPS_OK), then reboot-looped after ~1-2 s of UI. Cause: pack",
        f"count > {GROW_CACHE_LIMIT} takes hb_silver_icon_grow_cache(1 MiB) plus",
        "Home ensure_capacity/reload; not pack size, not Probe heap, not a",
        "Calendar boot widget. apps add takes ONE extra per reboot.",
        "Suggested Home set (Chess Books Calculator Notes Wallet Screenshot):",
        "./start apps set --plan   then   ./start apps set (one step / reboot).",
        "Raising the cap: ./start apps cap enable (nogrow overlay, untested).",
        RECOVERY_HINT,
        "",
        "Hardware-safe on MKN52:",
    ]
    chosen = set(selected or [])
    disk = set(on_disk) if on_disk is not None else None
    rows = catalog()
    by_cat: dict = {}
    for row in rows:
        by_cat.setdefault(row.get("category") or "other", []).append(row)
    tested = [r for r in rows if r.get("tested")]
    for row in tested:
        tag = _flag_row(row, chosen, disk)
        lines.append(f"  {row['name']:<16} {tag}")
    lines.append("")
    lines.append(
        f"Built extras (Disk Mode ./start apps add {LOWEST_RISK_EXTRA}; "
        "one extra per reboot; untested on this unit):"
    )
    for cat in (
        "productivity",
        "tools",
        "games",
        "cards",
        "graphics",
        "diagnostic",
        "first-party",
        "ports",
        "needs-rom",
        "other",
    ):
        group = [r for r in by_cat.get(cat, []) if not r.get("tested")]
        if not group:
            continue
        lines.append(f"  [{cat}]")
        for row in group:
            tag = _flag_row(row, chosen, disk)
            extra = f"  {row['size']}B" if row.get("size") else ""
            lines.append(f"    {row['name']:<16} {tag}{extra}")
    if disk is not None:
        extra = [n for n in on_disk or [] if n not in {r["name"] for r in rows}]
        if extra:
            lines.append("On nano, not in this catalog:")
            for n in extra:
                lines.append(f"  {n}")
    lines.append("")
    lines.append("Not Home apps (not addable): harnesses " + ", ".join(HARNESSES))
    lines.append("Protected loader: " + ", ".join(SYSTEM_NOT_HOME + const.STARTUP_PROTECTED))
    lines.append(
        "Looping extras set (do not add together): " + ", ".join(FIRST_SLICE)
    )
    lines.append(
        "Hardware test order (one per reboot): " + ", ".join(TEST_ORDER_EXTRAS)
    )
    lines.append("Suggested Home set: " + ", ".join(SUGGESTED_HOME))
    lines.append("./start apps set --plan   ./start apps cap enable")
    lines.append(
        f"`all` is the tested three, not every extra. Start with {LOWEST_RISK_EXTRA}."
    )
    lines.append("A merged pack is a new AllApps.pack hash (VERIFY_OK uses core loader sha).")
    lines.append(
        "Native Mario (olievans123/nano7-untethered, not a Game Boy ROM): "
        "./start apps add Mario --rom /path/to/sm64-usa.z64"
    )
    lines.append(
        "Temple Run / Flappy Bird / Doodle Jump: olievans123 ports; "
        "pass --payload with your original iOS .ipa or unpacked .app (not in git)."
    )
    lines.append(
        "Packs with count > 8 cannot be written until ./start apps cap enable "
        "(nogrow host cap 16). The 11-app looping set stays refused."
    )
    lines.append(
        "Paint lag A/B (Blocks does not tick behind Paint): ./start perf then "
        "./start apps remove Blocks --keep-files then ./start apps add Blocks. "
        "Opt-in hide: ./start apps yield Blocks --i-accept-untested-pack."
    )
    return "\n".join(lines)


def _loader_check_reason(check: dict) -> str:
    """Say which part of check_core_loader_hashes failed (hash vs pack)."""
    parts = []
    for fat in check.get("mismatched") or []:
        parts.append(f"hash mismatch {fat}")
    for fat in check.get("missing") or []:
        parts.append(f"missing {fat}")
    pack = check.get("pack") or {}
    if not pack.get("ok"):
        parts.append(f"{pack.get('pack_fat') or 'Home pack'}: {pack.get('reason') or 'not ok'}")
    return "; ".join(parts) or "unknown"


def slot_b_active(ops) -> bool:
    from .ports import SLOT_B_MARKER

    return bool(ops.exists(SLOT_B_MARKER))


def home_pack_fat(ops) -> str:
    from .ports import PACK_A_FAT, PACK_B_FAT

    if slot_b_active(ops) and ops.exists(PACK_B_FAT):
        return PACK_B_FAT
    return PACK_A_FAT


def list_on_disk(nano) -> List[str]:
    from .fat import MountFat, ensure_fat_mount, copy_from
    from .ports import PACK_A_FAT, PACK_B_FAT, SLOT_B_MARKER

    root = ensure_fat_mount(nano)
    if root is not None:
        ops = MountFat(root)
        fat = home_pack_fat(ops)
        data = ops.read_bytes(fat)
        if not data:
            return []
        return pack_app_names(data)
    need_mtools()
    unmount(nano)
    part = nano.partition
    names: List[str] = []
    marker = exists(part, SLOT_B_MARKER)
    pack = PACK_B_FAT if marker and exists(part, PACK_B_FAT) else PACK_A_FAT
    if exists(part, pack):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "AllApps.pack"
            copy_from(part, pack, dest)
            names = pack_app_names(dest.read_bytes())
    return names


def merge_bundles(base: bytes, extras: Sequence[bytes]) -> bytes:
    """Keep existing order, append new names, replace same-name bundles."""
    by_name = {bundle_name(b): b for b in parse_pack(base)} if base else {}
    order = pack_app_names(base) if base else []
    for b in extras:
        n = bundle_name(b)
        by_name[n] = b
        if n not in order:
            order.append(n)
    if len(order) > 64:
        raise Refused(f"AllApps.pack would have {len(order)} apps; resident cap is 64")
    return build_pack([by_name[n] for n in order])


def make_label_db(label_id: int, text: str) -> bytes:
    s = text.encode("ascii") + b"\0"
    res_meta_off = 0xC + 16
    header_len = res_meta_off + 12
    out = bytearray()
    out += struct.pack("<III", 3, header_len, 1)
    out += struct.pack("<IIII", 0x53747220, 1, 1, res_meta_off)
    out += struct.pack("<III", label_id, 0, len(s))
    out += s
    return bytes(out)


def make_hbap_bundle(row: dict, screen: bytes) -> bytes:
    from .app_icons import HBAP, HBSD, HBSCREEN, HBLAYOUT, assign_app_id, sbid_for

    label = row["name"]
    app_id = assign_app_id(row["bundle_id"])
    sbid = sbid_for(row["folder"])
    label_db = make_label_db(app_id, label)
    sections = [(2, label_db), (3, screen)]
    sbid_b = sbid.encode("ascii") + b"\0"
    name_b = label.encode("ascii") + b"\0"
    sec_table_off = BUNDLE_HDR_SIZE
    sbid_off = sec_table_off + 12 * len(sections)
    name_off = sbid_off + len(sbid_b)
    cur = _align4(name_off + len(name_b))
    descs = []
    data = bytearray()
    for kind, payload in sections:
        descs.append((kind, cur, len(payload)))
        data += payload
        pad = _align4(len(payload)) - len(payload)
        data += b"\x00" * pad
        cur += len(payload) + pad
    out = bytearray(cur)
    struct.pack_into(
        BUNDLE_HDR,
        out,
        0,
        BUNDLE_MAGIC,
        1,
        int(row.get("kind") or 1),
        app_id,
        app_id,
        HBSCREEN,
        HBLAYOUT,
        0,
        HBAP,
        HBSD,
        sbid_off,
        name_off,
        cur,
        len(sections),
    )
    for i, (kind, off, ln) in enumerate(descs):
        struct.pack_into("<III", out, sec_table_off + i * 12, kind, off, ln)
    out[sbid_off : sbid_off + len(sbid_b)] = sbid_b
    out[name_off : name_off + len(name_b)] = name_b
    out[_align4(name_off + len(name_b)) :] = data
    return bytes(out)


def ensure_extra_icon(row: dict) -> Path:
    from .app_icons import generate_icon

    dest = const.artifact(row["icon"])
    if dest.is_file() and dest.stat().st_size == 50208:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    generate_icon(
        label=row["name"],
        color=row.get("color") or "#69809d",
        glyph=row.get("glyph") or "shapes",
        bundle_id=row["bundle_id"],
        dest=dest,
    )
    return dest


def _sdk_built_extra(row: dict) -> bool:
    """An extra this tree builds from nanoapps/apps (not a ROM / iOS port)."""
    from .app_stage import SHELL_EXTRAS

    if row.get("tested") or row.get("needs_rom") or row.get("large_gl"):
        return False
    if row.get("needs_payload") and row.get("name") not in SHELL_EXTRAS:
        return False
    return row.get("origin") == "built"


def local_app_files(row: dict) -> tuple[Path, Path]:
    exe = const.artifact(row["executable"])
    if not exe.is_file():
        from .ports import missing_help

        if row.get("needs_rom") or row.get("needs_payload") or row.get("large_gl"):
            raise Refused(missing_help(row["name"]))
        raise Refused(
            f"{row['name']} is not built in this tree ({exe}). "
            "mk64/sm64 need your ROM. BigLoad/FPUTest did not build."
        )
    if _sdk_built_extra(row):
        from .app_stage import assert_no_legacy_addresses

        assert_no_legacy_addresses(exe, row["name"])
    icon = const.artifact(row["icon"])
    if not icon.is_file() or icon.stat().st_size != 50208:
        if row.get("tested"):
            raise Refused(f"hardware-tested icon missing for {row['name']}")
        icon = ensure_extra_icon(row)
    return exe, icon


def _copy_port_data(ops, row: dict, expected: dict, wrote: list, already: list) -> None:
    from .ports import data_files, fat_data_dir, extra_data_dir

    fat_dir = row.get("fat_data") or fat_data_dir(row["name"])
    host_dir = extra_data_dir(row["name"])
    if not fat_dir:
        return
    files = data_files(row["name"])
    if not files:
        if row.get("needs_rom") or row.get("needs_payload") or row.get("large_gl"):
            raise Refused(
                f"{row['name']} has no staged data under {host_dir}. "
                "Build from your ROM / original app first."
            )
        return
    ops.mkdir_p(fat_dir)
    for src in files:
        rel = src.relative_to(host_dir).as_posix()
        fat = f"{fat_dir.rstrip('/')}/{rel}"
        status = ops.copy_verified(src, fat, replace=True)
        digest = sha256_file(src)
        expected[fat] = digest
        (already if status == "already" else wrote).append(fat)
        print(f"    {status:8}  {fat}  sha {digest[:16]}...")


def _ensure_slot_b(ops, td_path: Path, expected: dict, wrote: list, already: list) -> str:
    """Copy slot-B resident (tested, or B-nogrow if the cap overlay is active)."""
    from .cap import LEAN_B_SHA256, NOGROW_B_SHA256, active_slot_b
    from .ports import PACK_B_FAT, SLOT_B_FAT

    src, digest, label = active_slot_b(ops)
    if not src.is_file():
        raise Refused("slot-B resident is missing")
    if sha256_file(src) != digest:
        raise Refused(f"resident-B source does not match {label} hash")
    known = {const.SLOT_B_SHA256, NOGROW_B_SHA256, LEAN_B_SHA256, const.NA_B_SHA256}
    if ops.exists(SLOT_B_FAT):
        got = ops.read_hash(SLOT_B_FAT)
        if got == digest:
            expected[SLOT_B_FAT] = digest
            already.append(SLOT_B_FAT)
            print(f"    already   {SLOT_B_FAT}  sha {digest[:16]}...")
        elif got in const.NA_B_ALL_SHA256S:
            # Keep NanoApps 1.0 heavy resident from ./start resident install-na.
            expected[SLOT_B_FAT] = got
            already.append(SLOT_B_FAT)
            print(f"    keep      {SLOT_B_FAT}  sha {got[:16]}... (silver_resident_na)")
            if got != const.NA_B_SHA256:
                print("    note      older silver_resident_na; run ./start resident install-na to update it")
        elif got in known:
            status = ops.copy_verified(src, SLOT_B_FAT, replace=True)
            expected[SLOT_B_FAT] = digest
            (already if status == "already" else wrote).append(SLOT_B_FAT)
            print(f"    {status:8}  {SLOT_B_FAT}  sha {digest[:16]}... ({label})")
        else:
            raise Refused(
                f"{SLOT_B_FAT} holds {got}, not a known unload resident "
                f"(tested {const.SLOT_B_SHA256[:16]}..., nogrow "
                f"{NOGROW_B_SHA256[:16]}..., or NA heavy {const.NA_B_SHA256[:16]}...). "
                f"Will not replace an unknown slot B. "
                + RECOVERY_HINT
            )
    else:
        status = ops.copy_verified(src, SLOT_B_FAT, replace=False)
        expected[SLOT_B_FAT] = digest
        (already if status == "already" else wrote).append(SLOT_B_FAT)
        print(f"    {status:8}  {SLOT_B_FAT}  sha {digest[:16]}... ({label})")
    print(
        "    slot B    large GL games (Mario) cannot load in the 576 KB "
        "slot A staging slot. Home will use /Apps/AllApps-B.pack after "
        "resident-B.active is created. Selector/ab and slot A stay put. "
        "Rollback: ./start apps restore-tested"
    )
    return PACK_B_FAT


def _activate_slot_b(ops, td_path: Path, expected: dict, wrote: list, already: list) -> None:
    """Create resident-B.active last, after game files and the loader read back."""
    from .ports import SLOT_B_MARKER
    from .compat import sha256_bytes

    if ops.exists(SLOT_B_MARKER):
        print(f"    already   {SLOT_B_MARKER}")
        return
    marker = td_path / "resident-B.active"
    marker.write_bytes(b"")
    status = ops.copy_verified(marker, SLOT_B_MARKER, replace=False)
    expected[SLOT_B_MARKER] = sha256_bytes(b"")
    (already if status == "already" else wrote).append(SLOT_B_MARKER)
    print(f"    {status:8}  {SLOT_B_MARKER}  (selector boots slot B next)")


def ensure_built(name: str) -> Optional[dict]:
    """Compile a NanoApps app from fetched source if its .hbapp is not here yet."""
    from .app_catalog import by_name, reset_catalog_cache

    row = by_name().get(name)
    if not row or row.get("present"):
        return row
    folder = row.get("folder") or ""
    if not folder or not (root() / "nanoapps" / "apps" / folder / "Makefile").is_file():
        return row
    from .sdkapp import build_from_source

    build_from_source(name, folder)
    reset_catalog_cache()
    return by_name().get(name)


def install_apps(
    nano,
    names: Sequence[str],
    *,
    backup: Path,
    backup_sha256: str,
    allow_size: Optional[int] = None,
    confirm_untested: bool = False,
    replace: bool = False,
    skip_backup_file: bool = False,
    skip_wait: bool = False,
    watch: bool = False,
) -> dict:
    """Merge named apps onto Home. Does not delete extras or the loader."""
    return add_apps(
        nano,
        names,
        backup=backup,
        backup_sha256=backup_sha256,
        allow_size=allow_size,
        confirm_untested=confirm_untested,
        replace=replace,
        skip_backup_file=skip_backup_file,
        skip_wait=skip_wait,
        watch=watch,
    )


def _ops_for_nano(nano):
    from .fat import MountFat, MtoolsFat, ensure_fat_mount, refuse_unreadable_fat

    root = ensure_fat_mount(nano)
    if root is not None:
        print(f"    volume    {root} (Disk Mode copy, no NAND, no DFU)")
        return MountFat(root), True
    need_mtools()
    unmount(nano)
    part = nano.partition
    if not exists(part, "/"):
        refuse_unreadable_fat(nano, after_unmount=True)
    return MtoolsFat(part), False


def refuse_nanoapps_base(ops, command: str) -> None:
    """Stop an untethered-layout command on a NanoApps-base iPod before it writes."""
    from .nabase import old_layout_refusal

    why = old_layout_refusal(ops, command)
    if why:
        raise Refused(why)


def add_apps(
    nano,
    names: Sequence[str],
    *,
    backup: Path,
    backup_sha256: str,
    allow_size: Optional[int] = None,
    confirm_untested: bool = False,
    replace: bool = True,
    skip_backup_file: bool = False,
    skip_wait: bool = False,
    watch: bool = False,
) -> dict:
    """Disk Mode merge of named Home apps. Never writes NAND. Never deletes loader."""
    from .app_catalog import by_name
    from .app_icons import shared_screen_blob
    from .compat import is_macos, sha256_bytes
    from .fat import ensure_fat_mount
    from .sync import wait_for_enter_or_watch
    from .untether import check_core_loader_hashes, durable_flush_and_rehash

    selected = normalize_selection(names, default_all=False, allow_extra=True)
    if not selected:
        raise Refused(
            "name one extra to add (Paint is lowest risk), or pass --all "
            "(tested three). The eight-extra slice is refused."
        )
    for name in selected:
        if is_protected_name(name):
            raise Refused(
                f"{name} is a startup/resident/loader file. "
                "apps add will not touch it."
            )
    extras = assert_one_extra_add(selected)
    print("nano7kit apps add")
    print("Merges Home apps into /Apps/AllApps.pack. Does not write NAND.")
    print("Does not delete resident / ab / resident-A. Prefer Disk Mode")
    print("(Do not disconnect while plugged in).")
    print("One extra per reboot. Hardware-safe: Blocks, Game Boy, Probe.")
    if extras:
        from .app_catalog import LOWEST_RISK_EXTRA, risk_label

        print(
            f"    extra     {extras[0]} ({risk_label(extras[0])})"
            + (
                "  [lowest-risk extra]"
                if extras[0] == LOWEST_RISK_EXTRA
                else f"  [lowest-risk extra is {LOWEST_RISK_EXTRA}]"
            )
        )
    print()
    if not skip_wait:
        wait_for_enter_or_watch(watch=watch, timeout=180)
        print()
    if nano is None:
        from .device import find_nanos

        found = find_nanos()
        if not found:
            raise Refused(
                "no iPod mass-storage volume. Confirm Disk Mode "
                "(Do not disconnect while plugged in), then re-run ./start apps add."
            )
        if len(found) > 1:
            raise Refused("more than one iPod; unplug extras")
        nano = found[0]
    check_usb_serial(nano.usb_serial)
    check_disk_size(
        nano.size,
        allow_size=allow_size,
        confirm_untested=confirm_untested,
        layout=layout_for_disk_size(nano.raw_device, nano.size),
    )
    check_backup(
        backup,
        backup_sha256,
        nano.size,
        skip_file=skip_backup_file,
        allow_size_mismatch=True,
    )
    assert_not_hfs(nano)
    if not is_macos():
        print("    note      live copy is documented Mac-only; this host is not macOS")
    catalog_by = by_name()
    rows = []
    for name in selected:
        row = catalog_by.get(name)
        if not row:
            raise Refused(f"unknown app {name}")
        if not row.get("present") and not row.get("tested"):
            from .ports import missing_help

            if row.get("needs_rom") or row.get("needs_payload") or row.get("large_gl"):
                raise Refused(missing_help(name))
            row = ensure_built(name) or row
            if not row.get("present"):
                raise Refused(f"{name} is not built in this tree")
        rows.append(row)
    ops, mounted = _ops_for_nano(nano)
    refuse_nanoapps_base(ops, "apps add")
    before = check_core_loader_hashes(nano)
    if before.get("ok"):
        print("    /Apps     core loader hashes match before add (will recheck)")
    screen = shared_screen_blob()
    import tempfile

    wrote = []
    already = []
    expected = {}
    extra_bundles = []
    replaced = []
    use_slot_b = False
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        for row in rows:
            exe, icon = local_app_files(row)
            for src, fat in ((exe, row["fat_exe"]), (icon, row["fat_icon"])):
                was_there = ops.exists(fat)
                status = ops.copy_verified(src, fat, replace=True)
                digest = sha256_file(src)
                expected[fat] = digest
                (already if status == "already" else wrote).append(fat)
                if status == "wrote" and was_there:
                    status = "replaced"
                    replaced.append(fat)
                print(f"    {status:8}  {fat}  sha {digest[:16]}...")
            _copy_port_data(ops, row, expected, wrote, already)
            extra_bundles.append(make_hbap_bundle(row, screen))
            if row.get("large_gl"):
                use_slot_b = True
        b_was_active = slot_b_active(ops)
        if use_slot_b:
            pack_fat = _ensure_slot_b(ops, td_path, expected, wrote, already)
        elif b_was_active:
            from .ports import PACK_B_FAT

            pack_fat = PACK_B_FAT
        else:
            pack_fat = "/Apps/AllApps.pack"
        on_disk = ops.read_bytes(pack_fat)
        if use_slot_b and not b_was_active and on_disk:
            # restore-tested keeps AllApps-B.pack on disk but drops slot B.
            # Re-activating slot B starts from the current slot A Home, not
            # the stale B pack (which may hold > 8 apps from a nogrow run).
            stale = pack_app_names(on_disk)
            print(
                f"    reseed    {pack_fat} from /Apps/AllApps.pack "
                f"(slot B was off; old B pack had {len(stale)} app(s))"
            )
            on_disk = b""
        if on_disk:
            pack_bytes = merge_bundles(on_disk, extra_bundles)
        elif pack_fat != "/Apps/AllApps.pack":
            seed = ops.read_bytes("/Apps/AllApps.pack") or tested_pack_bytes()
            pack_bytes = merge_bundles(seed, extra_bundles)
        else:
            pack_bytes = merge_bundles(tested_pack_bytes(), extra_bundles)
        from .cap import cap_lifted

        assert_pack_writable(pack_app_names(pack_bytes), nogrow=cap_lifted(ops))
        pack_src = td_path / "AllApps.pack"
        pack_src.write_bytes(pack_bytes)
        status = ops.copy_verified(pack_src, pack_fat, replace=True)
        expected[pack_fat] = sha256_bytes(pack_bytes)
        (already if status == "already" else wrote).append(pack_fat)
        print(
            f"    pack      {pack_fat}  {len(pack_app_names(pack_bytes))} app(s): "
            + ", ".join(pack_app_names(pack_bytes))
        )
        if use_slot_b:
            _activate_slot_b(ops, td_path, expected, wrote, already)
    rec = {
        "file_hashes": expected,
        "wrote": str(len(wrote)),
        "already": str(len(already)),
        "verified": "yes",
        "apps": ",".join(selected),
    }
    if mounted:
        from .fat import mounted_root

        if mounted_root(nano) is None:
            nano.mountpoints = [str(ensure_fat_mount(nano) or "")]
        durable_flush_and_rehash(nano, rec)
        after = check_core_loader_hashes(nano)
        if before.get("ok") and not after.get("ok"):
            raise Refused(
                "apps add finished but core loader hashes no longer match. "
                "Resident/ab were not supposed to change. Re-run "
                "./start install-untether (no firmware)."
            )
    print("APPS_OK")
    print(f"    added {', '.join(selected)}")
    if replaced:
        print(
            "    replaced  "
            + ", ".join(replaced)
            + " (older copy on the nano; /Apps/Data kept)"
        )
    print(
        "    pack      "
        + str(len(pack_app_names(pack_bytes)))
        + " app(s): "
        + ", ".join(pack_app_names(pack_bytes))
    )
    print("    HARDWARE: only Blocks, Game Boy, Probe are proven on this MKN52.")
    if use_slot_b:
        print(
            "    HARDWARE: Mario / large GL uses slot B (unload resident). "
            "Not yet run on this MKN52. Music uses ~0.9 MB; Probe heap "
            "7.0 MB free / 4.0 MB largest (fresh boot). If Home loops, restore-tested deletes "
            "resident-B.active and returns slot A."
        )
    print("    Eject IPOD. Unplug. Sleep/Wake+Home to Home. If it boots then reboots")
    print("    after about 1 s of UI, do not re-add. " + RECOVERY_HINT)
    return {
        "ok": True,
        "added": selected,
        "wrote": wrote,
        "already": already,
        "pack_names": pack_app_names(pack_bytes),
        "slot_b": use_slot_b or slot_b_active(ops),
        "replaced": replaced,
    }


def remove_apps(
    nano,
    names: Sequence[str],
    *,
    backup: Path,
    backup_sha256: str,
    allow_size: Optional[int] = None,
    confirm_untested: bool = False,
    skip_backup_file: bool = False,
    skip_wait: bool = False,
    watch: bool = False,
    remove_loader: bool = False,
    keep_files: bool = False,
    accept_untested_pack: bool = False,
    yield_mode: bool = False,
) -> dict:
    from .compat import sha256_bytes
    from .fat import ensure_fat_mount
    from .sync import wait_for_enter_or_watch
    from .untether import check_core_loader_hashes, durable_flush_and_rehash

    selected = normalize_selection(names, default_all=False, allow_extra=True)
    if not selected:
        raise Refused("name the apps to remove, or pass --all (tested three only)")
    for name in selected:
        if is_protected_name(name):
            raise Refused(
                f"{name} is a startup/resident/loader file and cannot be removed. "
                "There is no apps remove --loader. Copy a new resident only via "
                "./start install-untether."
            )
        if remove_loader:
            raise Refused("apps remove will not remove the resident or loader files")
    if yield_mode:
        print("nano7kit apps yield")
        print("Filters the active Home pack. Executables and icons stay on disk.")
        print("Pack hide, not a runtime suspend.")
        print("Does not write NAND. Does not delete resident / ab / resident-A.")
        keep_files = True
    else:
        print("nano7kit apps remove")
        if keep_files:
            print("Filters the active Home pack. Executables stay (--keep-files).")
        else:
            print("Filters the active Home pack then deletes the matching exe + icon.")
        print("Does not write NAND. Does not delete resident / ab / resident-A.")
    print()
    if not skip_wait:
        wait_for_enter_or_watch(watch=watch, timeout=180)
        print()
    check_usb_serial(nano.usb_serial)
    check_disk_size(
        nano.size,
        allow_size=allow_size,
        confirm_untested=confirm_untested,
        layout=layout_for_disk_size(nano.raw_device, nano.size),
    )
    check_backup(
        backup,
        backup_sha256,
        nano.size,
        skip_file=skip_backup_file,
        allow_size_mismatch=True,
    )
    assert_not_hfs(nano)
    ops, mounted = _ops_for_nano(nano)
    refuse_nanoapps_base(ops, "apps remove")
    before = check_core_loader_hashes(nano)
    pack_fat = home_pack_fat(ops)
    data = ops.read_bytes(pack_fat)
    if not data:
        raise Refused(f"no {pack_fat} on the nano; nothing to remove")
    plan = pack_drop_plan(data, selected, keep_files=keep_files)
    if plan["missing"]:
        print("    missing   not on nano (pack): " + ", ".join(plan["missing"]))
    keep = plan["keep"]
    rebuilt = plan["pack"]
    import tempfile

    wrote = []
    expected = {}
    with tempfile.TemporaryDirectory() as td:
        new_pack = Path(td) / "AllApps.new"
        new_pack.write_bytes(rebuilt)
        status = ops.copy_verified(new_pack, pack_fat, replace=True)
        expected[pack_fat] = sha256_bytes(rebuilt)
        wrote.append(status)
        print(f"    pack      {pack_fat}")
    catalog_by = {row["name"]: row for row in catalog()}
    removed = []
    for name in plan["drop"]:
        row = catalog_by.get(name)
        fat_exe = row["fat_exe"] if row else f"/Apps/Executables/{name}.hbapp"
        fat_icon = row["fat_icon"] if row else f"/Apps/Icons/{name}.bin"
        if is_protected_name(Path(fat_exe).stem) or fat_exe.startswith("/Apps/Startup/"):
            raise Refused(f"refusing to delete protected {fat_exe}")
        if keep_files:
            print(f"    kept      {fat_exe}")
        else:
            ops.delete(fat_exe)
            ops.delete(fat_icon)
            print(f"    deleted   {fat_exe}")
        removed.append(name)
    rec = {
        "file_hashes": expected,
        "wrote": "1",
        "already": "0",
        "verified": "yes",
        "apps_removed": ",".join(removed),
    }
    if mounted:
        from .fat import mounted_root

        if mounted_root(nano) is None:
            nano.mountpoints = [str(ensure_fat_mount(nano) or "")]
        durable_flush_and_rehash(nano, rec)
        after = check_core_loader_hashes(nano)
        if before.get("ok") and not after.get("ok"):
            raise Refused(
                "apps remove finished but the loader check no longer passes: "
                + _loader_check_reason(after)
            )
    label = "YIELDED" if yield_mode else "REMOVED"
    print(
        label
        + ": "
        + (", ".join(removed) or "(none)")
        + f"; pack now has {len(keep)} app(s)"
        + (" (files kept)" if keep_files else "")
    )
    if keep == tested_app_names():
        print("HARDWARE: pack is the tested three (Blocks, Game Boy, Probe).")
    else:
        print("HARDWARE: this AllApps.pack subset is untested as a unit.")
        if keep_files:
            print("Re-add: ./start apps add " + " ".join(f"'{n}'" if " " in n else n for n in removed))
        print_recovery_hint()
    return {
        "removed": removed,
        "kept": keep,
        "tested_pack": keep == tested_app_names(),
        "keep_files": keep_files,
        "yield_mode": yield_mode,
    }


def yield_apps(
    nano,
    names: Sequence[str],
    *,
    backup: Path,
    backup_sha256: str,
    allow_size: Optional[int] = None,
    confirm_untested: bool = False,
    skip_backup_file: bool = False,
    skip_wait: bool = False,
    watch: bool = False,
    accept_untested_pack: bool = False,
) -> dict:
    """Hide named apps from Home. Keep executables. Opt-in subset pack."""
    assert_yield_flag(names or ["Blocks"], accept_untested_pack)
    return remove_apps(
        nano,
        names,
        backup=backup,
        backup_sha256=backup_sha256,
        allow_size=allow_size,
        confirm_untested=confirm_untested,
        skip_backup_file=skip_backup_file,
        skip_wait=skip_wait,
        watch=watch,
        keep_files=True,
        accept_untested_pack=accept_untested_pack,
        yield_mode=True,
    )


def pick_interactive(*, installed: Optional[List[str]] = None) -> List[str]:
    import sys

    rows = [
        r
        for r in catalog()
        if r.get("present") and not (r.get("needs_rom") and not r.get("data_ready"))
    ]
    if not sys.stdin.isatty():
        raise Refused(
            "apps pick needs a TTY. Pass names to ./start apps add, or use ./install."
        )
    disk = set(installed or [])
    print("Pick Home apps. Hardware-tested three stay unless you remove them.")
    print("`all` is Blocks, Game Boy, Probe. Type one extra name or number.")
    print("The eight-extra slice is refused (MKN52 reboot loop).")
    print()
    for i, row in enumerate(rows, 1):
        mark = "*" if row["name"] in disk else " "
        tag = "tested" if row.get("tested") else row.get("origin")
        print(f"  {i:2} {mark} {row['name']:<16} {tag}")
    print()
    raw = input("add which apps? ").strip()
    if not raw:
        return []
    tokens: List[str] = []
    for part in raw.replace(",", " ").split():
        if part.isdigit():
            idx = int(part)
            if 1 <= idx <= len(rows):
                tokens.append(rows[idx - 1]["name"])
            else:
                raise Refused(f"no app number {idx}")
        else:
            tokens.append(part)
    return normalize_selection(tokens, default_all=False, allow_extra=True)


def _filter_existing(data: bytes, keep: Sequence[str]) -> bytes:
    """Keep named bundles from this pack. Missing keep names are dropped, not refused."""
    keep_set = set(keep)
    selected = [b for b in parse_pack(data) if bundle_name(b) in keep_set]
    return build_pack(selected)


def write_pack_file(dest: Path, names: Sequence[str]) -> dict:
    selected = list(normalize_selection(names, default_all=False))
    assert_pack_writable(selected)
    data, digest, tested = pack_for_selection(names)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return {
        "path": str(dest),
        "sha256": digest,
        "apps": selected,
        "hardware_tested_pack": tested,
    }


def restore_tested_pack(
    nano,
    *,
    backup: Path,
    backup_sha256: str,
    allow_size: Optional[int] = None,
    confirm_untested: bool = False,
    skip_backup_file: bool = False,
    skip_wait: bool = False,
    watch: bool = False,
) -> dict:
    """Rewrite /Apps/AllApps.pack to the hardware-tested three. No NAND.

    Extra executables/icons stay on disk but drop off Home. Loader stays.
    This is the prompt-free recovery from the 11-icon reboot loop.
    """
    from .compat import is_macos, sha256_bytes
    from .fat import ensure_fat_mount
    from .sync import wait_for_enter_or_watch
    from .untether import check_core_loader_hashes, durable_flush_and_rehash

    print("nano7kit apps restore-tested")
    print("Writes the hardware-tested AllApps.pack (Blocks, Game Boy, Probe).")
    print("Deletes resident-B.active and resident-nogrow.active if present.")
    print("Copies pinned tested residents back if a nogrow overlay is on FAT.")
    print("Does not write NAND. Does not delete extra .hbapp files.")
    print("Extra .hbapp files stay on disk; they will not appear on Home.")
    print()
    if not skip_wait:
        wait_for_enter_or_watch(watch=watch, timeout=180)
        print()
    if nano is None:
        from .device import find_nanos

        found = find_nanos()
        if not found:
            raise Refused(
                "no iPod mass-storage volume. Confirm Disk Mode "
                "(Do not disconnect while plugged in), then re-run "
                "./start apps restore-tested."
            )
        if len(found) > 1:
            raise Refused("more than one iPod; unplug extras")
        nano = found[0]
    check_usb_serial(nano.usb_serial)
    check_disk_size(
        nano.size,
        allow_size=allow_size,
        confirm_untested=confirm_untested,
        layout=layout_for_disk_size(nano.raw_device, nano.size),
    )
    check_backup(
        backup,
        backup_sha256,
        nano.size,
        skip_file=skip_backup_file,
        allow_size_mismatch=True,
    )
    assert_not_hfs(nano)
    if not is_macos():
        print("    note      live copy is documented Mac-only; this host is not macOS")
    ops, mounted = _ops_for_nano(nano)
    before = check_core_loader_hashes(nano)
    from .cap import LEAN_B_SHA256, LEAN_MARKER, NOGROW_MARKER, NOGROW_B_SHA256
    from .ports import PACK_B_FAT, SLOT_B_FAT, SLOT_B_MARKER

    on_disk = ops.read_bytes(home_pack_fat(ops))
    previous = pack_app_names(on_disk) if on_disk else []
    pack_bytes = tested_pack_bytes()
    assert_pack_writable(pack_app_names(pack_bytes))
    import tempfile

    expected = {}
    deactivated_b = False
    rolled_nogrow = False
    with tempfile.TemporaryDirectory() as td:
        pack_src = Path(td) / "AllApps.pack"
        pack_src.write_bytes(pack_bytes)
        status = ops.copy_verified(pack_src, "/Apps/AllApps.pack", replace=True)
        expected["/Apps/AllApps.pack"] = sha256_bytes(pack_bytes)
        print(f"    {status:8}  /Apps/AllApps.pack  sha {const.ALLAPPS_PACK_SHA256[:16]}...")
        pins = (
            (
                const.artifact("artifacts/device/Startup/resident.hbapp"),
                "/Apps/Startup/resident.hbapp",
                const.RESIDENT_SHA256,
            ),
            (
                const.artifact("artifacts/device/Startup/resident-A.hbapp"),
                "/Apps/Startup/resident-A.hbapp",
                const.SLOT_A_SHA256,
            ),
        )
        for src, fat, digest in pins:
            if not src.is_file():
                continue
            if ops.exists(fat) and ops.read_hash(fat) == digest:
                continue
            status = ops.copy_verified(src, fat, replace=True)
            expected[fat] = digest
            rolled_nogrow = True
            print(f"    {status:8}  {fat}  (tested resident)")
        if ops.exists(SLOT_B_FAT):
            got_b = ops.read_hash(SLOT_B_FAT)
            if got_b in (NOGROW_B_SHA256, LEAN_B_SHA256):
                src_b = const.artifact("artifacts/device/Startup/resident-B.hbapp")
                status = ops.copy_verified(src_b, SLOT_B_FAT, replace=True)
                expected[SLOT_B_FAT] = const.SLOT_B_SHA256
                rolled_nogrow = True
                print(f"    {status:8}  {SLOT_B_FAT}  (tested slot B)")
        if ops.exists(SLOT_B_MARKER):
            ops.delete(SLOT_B_MARKER)
            deactivated_b = True
            print(f"    deleted   {SLOT_B_MARKER} (slot A next boot)")
        if ops.exists(NOGROW_MARKER):
            ops.delete(NOGROW_MARKER)
            rolled_nogrow = True
            print(f"    deleted   {NOGROW_MARKER} (stock pack cap 8 next boot)")
        if ops.exists(LEAN_MARKER):
            ops.delete(LEAN_MARKER)
            rolled_nogrow = True
            print(f"    deleted   {LEAN_MARKER} (stock icon LRU next boot)")
        if ops.exists(PACK_B_FAT):
            print(
                f"    kept      {PACK_B_FAT} and game files on disk; "
                "they are off Home until you add them again"
            )
    rec = {
        "file_hashes": expected,
        "wrote": "1",
        "already": "0",
        "verified": "yes",
        "apps": ",".join(tested_app_names()),
    }
    if mounted:
        from .fat import mounted_root

        if mounted_root(nano) is None:
            nano.mountpoints = [str(ensure_fat_mount(nano) or "")]
        durable_flush_and_rehash(nano, rec)
        after = check_core_loader_hashes(nano)
        if before.get("ok") and not after.get("ok"):
            raise Refused(
                "apps restore-tested finished but core loader hashes no longer match."
            )
    dropped = [n for n in previous if n not in tested_app_names()]
    print("APPS_OK")
    print("    pack      3 app(s): " + ", ".join(tested_app_names()))
    if dropped:
        print("    dropped   from Home: " + ", ".join(dropped))
    if deactivated_b:
        print("    slot      A (resident-B.active removed)")
    if rolled_nogrow:
        print("    cap       stock 8 (nogrow overlay removed)")
    print("    Eject IPOD. Unplug. Sleep/Wake+Home. Hardware-safe icons should stay.")
    return {
        "ok": True,
        "kept": tested_app_names(),
        "dropped": dropped,
        "previous": previous,
        "tested_pack": True,
        "slot_b_deactivated": deactivated_b,
        "nogrow_rolled_back": rolled_nogrow,
    }


def dump_lasttrace(nano, *, dest: Optional[Path] = None, ops=None, absent_hint: bool = True) -> dict:
    """Read-only copy + decode of /Apps/Startup/lasttrace.bin. No NAND."""
    from .state import state_dir

    print("nano7kit apps lasttrace")
    print("Reads /Apps/Startup/lasttrace.bin (previous-boot hb_trace).")
    print("Does not write NAND. Does not change AllApps.pack.")
    if nano is None:
        from .device import find_nanos

        found = find_nanos()
        if not found:
            raise Refused(
                "no iPod mass-storage volume. Confirm Disk Mode "
                "(Do not disconnect while plugged in), then re-run "
                "./start apps lasttrace."
            )
        if len(found) > 1:
            raise Refused("more than one iPod; unplug extras")
        nano = found[0]
    if ops is None:
        assert_not_hfs(nano)
        ops, _mounted = _ops_for_nano(nano)
    data = ops.read_bytes(LASTTRACE_FAT)
    if not data:
        print("LASTTRACE_ABSENT")
        print(f"    {LASTTRACE_FAT} is not on this volume.")
        print(
            "    The resident writes it only when RetailOS boots normally: "
            "after the test, reboot with Sleep/Wake+Home, let it reach the Home "
            "screen, then enter Disk Mode. Going straight to Disk Mode "
            "leaves no file."
        )
        if absent_hint:
            print_recovery_hint()
        return {"ok": False, "present": False, "path": LASTTRACE_FAT}
    out = dest if dest is not None else state_dir() / "lasttrace.bin"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"    copied    {LASTTRACE_FAT} -> {out} ({len(data)} bytes)")
    try:
        decoded = decode_lasttrace(data)
    except Refused as err:
        print("LASTTRACE_BAD")
        print(f"    {err}")
        print_recovery_hint()
        return {
            "ok": False,
            "present": True,
            "path": str(out),
            "size": len(data),
            "error": str(err),
        }
    print(format_lasttrace(decoded))
    print("LASTTRACE_OK")
    return {
        "ok": True,
        "present": True,
        "path": str(out),
        "size": len(data),
        "count": decoded.get("count"),
        "seq": decoded.get("seq"),
        "tags": decoded.get("tags"),
    }


def _shell_app_name(name: str) -> str:
    if any(ch in name for ch in (" ", "'")):
        return "'" + name.replace("'", "") + "'"
    return name


def plan_app_set(
    names: Optional[Sequence[str]] = None,
    *,
    on_disk: Optional[Sequence[str]] = None,
    nogrow: bool = False,
) -> dict:
    """Host-only plan: add missing names one per reboot, cap enable if needed."""
    from .app_catalog import SUGGESTED_HOME, skipped_from_set
    from .cap import STOCK_PACK_LIMIT, pack_limit
    from .ports import needs_payload, needs_rom

    if names:
        target = normalize_selection(names, default_all=False, allow_extra=True)
    else:
        target = list(SUGGESTED_HOME)
    skipped = [n for n in target if skipped_from_set(n)]
    if skipped:
        raise Refused(
            "apps set skips graphics, developer, and Sensors apps: "
            + ", ".join(skipped)
            + ". Suggested Home set: "
            + ", ".join(SUGGESTED_HOME)
        )
    current = (
        list(dict.fromkeys(n for n in on_disk if n))
        if on_disk is not None
        else list(tested_app_names())
    )
    missing = [n for n in target if n not in current]
    future = list(dict.fromkeys(list(current) + missing))
    reason = pack_is_looping(future, nogrow=True)
    if reason:
        raise Refused(reason)
    limit = pack_limit(nogrow=True)
    if len(future) > limit:
        raise Refused(
            f"apps set would have {len(future)} apps; nogrow cap is {limit}."
        )
    steps = []
    running = list(current)
    cap_on = bool(nogrow)
    for extra in missing:
        if (not cap_on) and (len(running) + 1 > STOCK_PACK_LIMIT):
            steps.append(
                {
                    "action": "cap",
                    "name": None,
                    "command": "./start apps cap enable",
                }
            )
            cap_on = True
        cmd = "./start apps add " + _shell_app_name(extra)
        if needs_rom(extra):
            cmd += " --rom /path/to/usa.z64"
        if needs_payload(extra):
            cmd += " --payload /path/to/Game.ipa"
        steps.append({"action": "add", "name": extra, "command": cmd})
        running.append(extra)
    need_cap = any(s["action"] == "cap" for s in steps) or (
        len(future) > STOCK_PACK_LIMIT and not nogrow
    )
    return {
        "target": target,
        "current": current,
        "missing": missing,
        "future": future,
        "steps": steps,
        "need_cap": need_cap,
        "nogrow": cap_on,
        "done": not steps,
        "limit": pack_limit(nogrow=cap_on),
    }


def format_set_plan(plan: dict) -> str:
    from .cap import NOGROW_PACK_LIMIT, STOCK_PACK_LIMIT

    lines = [
        "nano7kit apps set (plan, host only, no NAND)",
        "Adds missing names one per Disk Mode reboot. Does not remove extras",
        "already on Home (Mario stays if you added it first).",
        "Screen while plugged in: Do not disconnect.",
        "",
        "  target    " + ", ".join(plan["target"]),
        "  on disk   " + (", ".join(plan["current"]) or "(none)"),
        "  missing   " + (", ".join(plan["missing"]) or "(none)"),
        "  future    "
        + str(len(plan["future"]))
        + " app(s), stock cap "
        + str(STOCK_PACK_LIMIT)
        + ", nogrow cap "
        + str(NOGROW_PACK_LIMIT),
        "  cap       "
        + (
            "enable needed before a 9th Home icon"
            if plan.get("need_cap")
            else "stock 8 is enough"
        ),
        "",
    ]
    if plan.get("done"):
        lines.append("Already at the target set.")
        return "\n".join(lines)
    lines.append("steps (one per reboot; cap enable is its own reboot):")
    for i, step in enumerate(plan["steps"], 1):
        lines.append(f"  {i}. {step['command']}")
    lines.append("")
    lines.append("Mario first on this MKN52, then this set:")
    lines.append("  ./start apps add Mario --rom /path/to/sm64-usa.z64")
    lines.append("  then ./start apps set   (applies the next step only)")
    lines.append("Rollback: ./start apps lasttrace && ./start apps restore-tested")
    return "\n".join(lines)


def apply_app_set(
    nano,
    names: Optional[Sequence[str]] = None,
    *,
    backup: Path,
    backup_sha256: str,
    allow_size: Optional[int] = None,
    confirm_untested: bool = False,
    skip_backup_file: bool = False,
    skip_wait: bool = False,
    watch: bool = False,
    rom: Optional[Path] = None,
    payload: Optional[Path] = None,
) -> dict:
    """Disk Mode: apply the next apps-set step (one extra, or cap enable)."""
    from .cap import cap_lifted, enable_cap
    from .ports import prepare_for_add

    ops, _mounted = _ops_for_nano(nano)
    refuse_nanoapps_base(ops, "apps set")
    current = []
    pack_fat = home_pack_fat(ops)
    data = ops.read_bytes(pack_fat)
    if data:
        current = pack_app_names(data)
    elif ops.exists("/Apps/AllApps.pack"):
        seed = ops.read_bytes("/Apps/AllApps.pack")
        current = pack_app_names(seed) if seed else tested_app_names()
    else:
        current = tested_app_names()
    plan = plan_app_set(names, on_disk=current, nogrow=cap_lifted(ops))
    print(format_set_plan(plan))
    print()
    if plan.get("done"):
        print("SET_OK (already at target)")
        return {"ok": True, "done": True, "plan": plan, "applied": None}
    step = plan["steps"][0]
    if step["action"] == "cap":
        from .cap import lean_present

        rec = enable_cap(
            nano,
            lean=bool(lean_present(ops)),
            backup=backup,
            backup_sha256=backup_sha256,
            allow_size=allow_size,
            confirm_untested=confirm_untested,
            skip_backup_file=skip_backup_file,
            skip_wait=skip_wait,
            watch=watch,
        )
        print("SET_OK (cap enable). Sleep/Wake+Home on the current pack, then apps set again.")
        return {"ok": True, "done": False, "plan": plan, "applied": step, "cap": rec}
    if step["action"] == "add":
        prepare_for_add(
            step["name"],
            rom=Path(rom).expanduser() if rom else None,
            payload=Path(payload).expanduser() if payload else None,
        )
        rec = add_apps(
            nano,
            [step["name"]],
            backup=backup,
            backup_sha256=backup_sha256,
            allow_size=allow_size,
            confirm_untested=confirm_untested,
            skip_backup_file=skip_backup_file,
            skip_wait=skip_wait,
            watch=watch,
        )
        print("SET_OK (added " + step["name"] + "). Eject, Sleep/Wake+Home, then apps set again.")
        return {"ok": True, "done": False, "plan": plan, "applied": step, "add": rec}
    raise Refused(f"unknown apps set step {step}")
