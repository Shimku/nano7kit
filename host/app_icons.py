"""Generate 112x112 Home icons without the gitignored FontAwesome path.

Uses the vendored Font Awesome OTF in untether/third_party/fontawesome
(same files SHA256SUMS already pins). Falls back to two-letter initials
if Pillow or the OTF is missing. Output is the 32-byte SilverDB bitmap
header plus 112x112 BGRA (50208 bytes), matching Blocks.bin.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Optional, Tuple

from .compat import Refused, root
from .const import artifact


SIZE = 112
FMT = 0x1888
CIRC_X0, CIRC_Y0, CIRC_W, CIRC_H = 12, 6, 88, 89
HBSCREEN = 0x0DAD8002
HBLAYOUT = 0x0DAD8001
HBAP = 0x48426170
HBSD = 0x48425344
OUR_ID_LO, OUR_ID_HI = 0x0DAD8100, 0x0DADFF00


def _ipodhax_dir() -> Path:
    return root() / "nanoapps" / "tools" / "dependencies" / "ipodhax"


def _fa_otf() -> Path:
    return artifact("third_party/fontawesome/otfs/Font Awesome 7 Free-Solid-900.otf")


def _fa_meta() -> Path:
    return artifact("third_party/fontawesome/metadata/icons.json")


def hex_rgb(s: str, fallback: Tuple[int, int, int] = (0x69, 0x80, 0x9D)) -> Tuple[int, int, int]:
    try:
        t = s.lstrip("#")
        return (int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16))
    except Exception:
        return fallback


def assign_app_id(bundle_id: str) -> int:
    span = OUR_ID_HI - OUR_ID_LO
    h = 0x811C9DC5
    for ch in bundle_id.encode("ascii", "replace"):
        h = ((h ^ ch) * 0x01000193) & 0xFFFFFFFF
    x = OUR_ID_LO + (h % span)
    if x in (HBSCREEN, HBLAYOUT):
        x = OUR_ID_LO + ((x - OUR_ID_LO + 1) % span)
    return x


def sbid_for(folder: str) -> str:
    sbid = "hb." + folder
    if len(sbid) > 23:
        raise Refused(f"sbid {sbid!r} is longer than 23 characters")
    return sbid


def _scale(rgb: Tuple[int, int, int], f: float) -> Tuple[int, int, int]:
    return tuple(max(0, min(255, int(c * f))) for c in rgb)  # type: ignore[return-value]


def _make_png(fa_name: str, rgb: Tuple[int, int, int], dest: Path, initials: str) -> None:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

    SS = 4
    S = SIZE * SS
    x0, y0 = int(round(CIRC_X0 * SS)), int(round(CIRC_Y0 * SS))
    dw, dh = int(round(CIRC_W * SS)), int(round(CIRC_H * SS))
    x1, y1 = x0 + dw, y0 + dh
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sm = Image.new("L", (S, S), 0)
    ImageDraw.Draw(sm).ellipse([x0 + 4 * SS, y0 + 7 * SS, x1 - 4 * SS, y1 + 3 * SS], fill=70)
    sm = sm.filter(ImageFilter.GaussianBlur(3 * SS))
    sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sh.putalpha(sm)
    img = Image.alpha_composite(img, sh)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).ellipse([x0, y0, x1, y1], fill=255)
    top, bot = _scale(rgb, 1.18), _scale(rgb, 0.86)
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = min(1.0, max(0.0, (y - y0) / dh))
        c = tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3))
        gd.line([(0, y), (S, y)], fill=c + (255,))
    grad.putalpha(mask)
    img = Image.alpha_composite(img, grad)
    axc = CIRC_X0 + CIRC_W / 2.0
    acu = (46 - 52) / (CIRC_X0 - axc) ** 2
    pts = [(0, 0), (S, 0)]
    nseg = 96
    for k in range(nseg + 1):
        x = SIZE * (nseg - k) / nseg
        y = acu * (x - axc) ** 2 + 52
        pts.append((x * SS, y * SS))
    ga = Image.new("L", (S, S), 0)
    ImageDraw.Draw(ga).polygon(pts, fill=105)
    ga = ImageChops.multiply(ga, mask)
    gloss = Image.new("RGBA", (S, S), (255, 255, 255, 0))
    gloss.putalpha(ga)
    img = Image.alpha_composite(img, gloss)
    bw = SS
    inner = Image.new("L", (S, S), 0)
    ImageDraw.Draw(inner).ellipse([x0 + bw, y0 + bw, x1 - bw, y1 - bw], fill=255)
    ring = ImageChops.subtract(mask, inner)
    bdr = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    bdr.putalpha(ring.point(lambda v: int(v * 0.25)))
    img = Image.alpha_composite(img, bdr)

    glyph_ok = False
    otf = _fa_otf()
    meta_path = _fa_meta()
    if otf.is_file() and meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text())
            name = fa_name if fa_name in meta else "shapes"
            if name not in meta:
                name = next(iter(meta))
            cp = chr(int(meta[name]["unicode"], 16))
            font = ImageFont.truetype(str(otf), int(dw * 0.50))
            big = Image.new("L", (int(dw * 3), int(dh * 3)), 0)
            d = ImageDraw.Draw(big)
            bb = d.textbbox((0, 0), cp, font=font)
            d.text((-bb[0], -bb[1]), cp, font=font, fill=255)
            gm = big.crop((0, 0, max(1, bb[2] - bb[0]), max(1, bb[3] - bb[1])))
            gw, gh = gm.size
            gx, gy = x0 + (dw - gw) // 2, y0 + (dh - gh) // 2
            ga2 = Image.new("L", (S, S), 0)
            ga2.paste(gm, (gx, gy))
            outline = ga2.filter(ImageFilter.MaxFilter(SS + 1))
            oa = outline.point(lambda v: int(v * 0.20))
            ol = Image.new("RGBA", (S, S), (0, 0, 0, 0))
            ol.putalpha(oa)
            img = Image.alpha_composite(img, ol)
            gl = Image.new("RGBA", (S, S), (255, 255, 255, 0))
            gl.putalpha(ga2)
            img = Image.alpha_composite(img, gl)
            glyph_ok = True
        except Exception:
            glyph_ok = False
    if not glyph_ok:
        d = ImageDraw.Draw(img)
        text = (initials or "?")[:2]
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", int(dw * 0.42))
        except Exception:
            font = ImageFont.load_default()
        bb = d.textbbox((0, 0), text, font=font)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        d.text(
            (x0 + (dw - tw) // 2 - bb[0], y0 + (dh - th) // 2 - bb[1]),
            text,
            font=font,
            fill=(255, 255, 255, 255),
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.resize((SIZE, SIZE), Image.LANCZOS).save(dest)


def encode_icon_bin(png: Path, icon_id: int) -> bytes:
    sys.path.insert(0, str(_ipodhax_dir()))
    from ipodhax.silverdb.pack import encode_image  # type: ignore

    buf = io.BytesIO()
    encode_image(icon_id, FMT, png, buf)
    blob = buf.getvalue()
    if len(blob) < 32 + SIZE * SIZE * 4:
        raise Refused(f"icon encoder produced {len(blob)} bytes; expected 50208")
    return blob[: 32 + SIZE * SIZE * 4]


def generate_icon(
    *,
    label: str,
    color: str,
    glyph: str,
    bundle_id: str,
    dest: Path,
) -> Path:
    """Write /Apps/Icons/<CFBundleName>.bin (50208 bytes)."""
    dest = Path(dest)
    png = dest.with_suffix(".png")
    initials = "".join(ch for ch in label if ch.isalnum())[:2] or "NA"
    _make_png(glyph, hex_rgb(color), png, initials)
    dest.write_bytes(encode_icon_bin(png, assign_app_id(bundle_id)))
    try:
        png.unlink()
    except OSError:
        pass
    return dest


def shared_screen_blob() -> bytes:
    """Kind-3 screen section from the hardware-tested AllApps.pack (724 bytes)."""
    cached = artifact("artifacts/extra/screen.bin")
    if cached.is_file() and cached.stat().st_size == 724:
        return cached.read_bytes()
    from .apps import parse_pack

    pack = artifact("artifacts/device/AllApps.pack").read_bytes()
    for bundle in parse_pack(pack):
        nsec = int.from_bytes(bundle[48:52], "little")
        for i in range(nsec):
            kind, off, ln = (
                int.from_bytes(bundle[52 + i * 12 : 56 + i * 12], "little"),
                int.from_bytes(bundle[56 + i * 12 : 60 + i * 12], "little"),
                int.from_bytes(bundle[60 + i * 12 : 64 + i * 12], "little"),
            )
            if kind == 3 and ln == 724:
                blob = bundle[off : off + ln]
                cached.parent.mkdir(parents=True, exist_ok=True)
                cached.write_bytes(blob)
                return blob
    raise Refused("hardware-tested AllApps.pack has no 724-byte screen section")
