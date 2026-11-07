#!/usr/bin/env python3
"""Downscale Angry Birds 1.0 textures for the nano heap. Never commits game files.

Copies data/ from the user's .app: Lua as-is, named sprite .dat left at original
pixel sizes (UV wrap is undone from texscale). wav/mp3 skipped.

INGAME sheets fill a zoomed camera on a 240-wide panel, so keep 256. Menu, splash,
fonts, cutscenes, tutorials and level-select fill the 480x320 frame: keep 512
(one fewer halve than INGAME). Original 1024 sheets are ~1 MB POT 16-bit each
and do not fit the 6.4/4.0 probe together with the guest block; 512 does.
The nano GLES upload cap is 512 on a side (be_tex_image).
"""
from __future__ import annotations

import os
import struct
import sys
import zlib
from pathlib import Path

SKIP_AUDIO = {".wav", ".mp3", ".caf", ".aif", ".aiff", ".m4a"}
# named sprite-sheet .dat: u16 BE name, u16 BE count, then name + 6 x u16 BE per sprite
SHEET_DAT = True
INGAME_KEEP = 256
UI_KEEP = 512
MAX_KEEP = UI_KEEP  # default / tests: UI sheets; INGAME uses INGAME_KEEP
# Host PHYS_TEST with every 1024-class UI sheet at 512 was 3,211,264 live
# (all sheets preload). After the 2 MB guest block the 6.4/4.0 probe has
# about 2.30 MB. All-256 live is ~1.52 MB (PVRTC INGAME_6/7/8).
# MENU+SPLASHES+TUTORIALS at 512 measured 2,105,344 (196 KB spare). Adding
# fonts (2,228,224) left 73 KB, too tight for the GL driver. MENU_1 is hills
# as 512x512 (+384 KB) and does not fit with the three 512x256 sheets.
# MENU at 512 is 505x255 RGBA (515100) plus a 512x256 POT pad. Those decode
# buffers live on the OS heap (g_malloc 256 KB+), not in the 1.82 MB guest
# heap; keeping MENU at 256 is only needed if OS largest cannot hold 515 KB.
HQ_KEEP = {
    "MENU", "SPLASHES", "TUTORIALS",
}


def keep_for(stem: str) -> int:
    u = stem.upper()
    if u.startswith("INGAME"):
        return INGAME_KEEP
    if u in HQ_KEEP:
        return UI_KEEP
    return INGAME_KEEP


def pvr_header(data: bytes):
    hl, h, w, mips, flags, datalen, bpp, r, g, b, a, tag, ns = struct.unpack_from("<13I", data)
    return dict(hl=hl, h=h, w=w, mips=mips, flags=flags, datalen=datalen, bpp=bpp, tag=tag)


def unpack_4444(px: bytes, w: int, h: int) -> list:
    out = []
    n = w * h
    for i in range(n):
        v = px[2 * i] | (px[2 * i + 1] << 8)
        out.append(((v >> 12) * 17, ((v >> 8) & 15) * 17, ((v >> 4) & 15) * 17, (v & 15) * 17))
    return out


def pack_4444(rgba, w: int, h: int) -> bytes:
    out = bytearray(w * h * 2)
    for i, (r, g, b, a) in enumerate(rgba):
        v = (r >> 4) << 12 | (g >> 4) << 8 | (b >> 4) << 4 | (a >> 4)
        out[2 * i] = v & 255
        out[2 * i + 1] = v >> 8
    return bytes(out)


def box_half(rgba, w, h):
    nw, nh = max(1, w // 2), max(1, h // 2)
    out = []
    for y in range(nh):
        for x in range(nw):
            acc = [0, 0, 0, 0]
            for j in range(2):
                for i in range(2):
                    p = rgba[min(h - 1, y * 2 + j) * w + min(w - 1, x * 2 + i)]
                    for k in range(4):
                        acc[k] += p[k]
            out.append(tuple(v // 4 for v in acc))
    return out, nw, nh


def unpack_col(word, b):
    c = (word >> 16) if b else (word & 0xFFFF)
    if c & 0x8000:
        if b:
            r, g, bl = (c >> 10) & 31, (c >> 5) & 31, c & 31
        else:
            r, g, bl = (c >> 10) & 31, (c >> 5) & 31, (c >> 1) & 15
            bl = (bl << 1) | (bl >> 3)
        a = 255
        r = (r << 3) | (r >> 2)
        g = (g << 3) | (g >> 2)
        bl = (bl << 3) | (bl >> 2)
    else:
        a = (c >> 12) & 7
        a = (a << 5) | (a << 2) | (a >> 1)
        if b:
            r, g, bl = (c >> 8) & 15, (c >> 4) & 15, c & 15
        else:
            r, g, bl = (c >> 8) & 15, (c >> 4) & 15, (c >> 1) & 7
            bl = (bl << 1) | (bl >> 2)
        r, g, bl = (r << 4) | r, (g << 4) | g, (bl << 4) | bl
    return r, g, bl, a


def morton(x, y, bx, by):
    m = bit = 0
    minb = bx if bx < by else by
    i = 0
    while (1 << i) < minb:
        m |= ((y >> i) & 1) << bit
        bit += 1
        m |= ((x >> i) & 1) << bit
        bit += 1
        i += 1
    while (1 << i) < bx or (1 << i) < by:
        if (1 << i) < bx:
            m |= ((x >> i) & 1) << bit
            bit += 1
        else:
            m |= ((y >> i) & 1) << bit
            bit += 1
        i += 1
    return m


def pvrtc_decode(data: bytes, w: int, h: int, bpp2: bool) -> list:
    bw, bh = (8, 4) if bpp2 else (4, 4)
    nbx, nby = max(2, w // bw), max(2, h // bh)
    cols_a = [None] * (nbx * nby)
    cols_b = [None] * (nbx * nby)
    mods = [0] * (nbx * nby)
    for by in range(nby):
        for bx in range(nbx):
            blk = data[8 * morton(bx, by, nbx, nby):]
            cw = blk[4] | blk[5] << 8 | blk[6] << 16 | blk[7] << 24
            i = by * nbx + bx
            cols_a[i] = unpack_col(cw, 0)
            cols_b[i] = unpack_col(cw, 1)
            mods[i] = blk[0] | blk[1] << 8 | blk[2] << 16 | blk[3] << 24
    out = [(0, 0, 0, 0)] * (w * h)
    for y in range(h):
        for x in range(w):
            fx, fy = x - bw // 2, y - bh // 2
            bx0 = (fx - bw + 1 if fx < 0 else fx) // bw
            by0 = (fy - bh + 1 if fy < 0 else fy) // bh
            tx = (fx - bx0 * bw) / bw
            ty = (fy - by0 * bh) / bh
            A = [0.0] * 4
            B = [0.0] * 4
            for j in range(2):
                for i in range(2):
                    bx = ((bx0 + i) % nbx + nbx) % nbx
                    by = ((by0 + j) % nby + nby) % nby
                    ci = by * nbx + bx
                    ca, cb = cols_a[ci], cols_b[ci]
                    wgt = (tx if i else 1 - tx) * (ty if j else 1 - ty)
                    for k in range(4):
                        A[k] += wgt * ca[k]
                        B[k] += wgt * cb[k]
            bx, by = (x // bw) % nbx, (y // bh) % nby
            mw = mods[by * nbx + bx]
            if not bpp2:
                mv = (mw >> (2 * ((y % 4) * 4 + (x % 4)))) & 3
                wv = (0, 3 / 8, 5 / 8, 1)[mv]
            else:
                wv = 1.0 if (mw >> ((y % 4) * 8 + (x % 8))) & 1 else 0.0
            pix = []
            for k in range(4):
                v = A[k] + (B[k] - A[k]) * wv
                pix.append(0 if v < 0 else 255 if v > 255 else int(v))
            out[y * w + x] = tuple(pix)
    return out


def write_pvr_4444(path: Path, w: int, h: int, rgba) -> None:
    payload = pack_4444(rgba, w, h)
    hdr = struct.pack(
        "<13I",
        52, h, w, 0, 0x8010, len(payload), 16,
        0xF000, 0x0F00, 0x00F0, 0x000F, 0x21525650, 1,
    )
    path.write_bytes(hdr + payload)


def paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def load_png(data: bytes):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not png")
    i = 8
    w = h = 0
    depth = 8
    color = 6
    idat = []
    while i + 12 <= len(data):
        ln, typ = struct.unpack_from(">I4s", data, i)
        chunk = data[i + 8:i + 8 + ln]
        i += 12 + ln
        if typ == b"IHDR":
            w, h, depth, color, *_ = struct.unpack(">IIBBBBB", chunk)
        elif typ == b"IDAT":
            idat.append(chunk)
        elif typ == b"IEND":
            break
    raw = zlib.decompress(b"".join(idat))
    bpp = {0: 1, 2: 3, 4: 2, 6: 4}[color] * (depth // 8)
    stride = w * bpp
    rows = []
    pos = 0
    prev = bytes(stride)
    for y in range(h):
        filt = raw[pos]
        pos += 1
        row = bytearray(raw[pos:pos + stride])
        pos += stride
        for x in range(stride):
            a = row[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if filt == 1:
                row[x] = (row[x] + a) & 255
            elif filt == 2:
                row[x] = (row[x] + b) & 255
            elif filt == 3:
                row[x] = (row[x] + ((a + b) // 2)) & 255
            elif filt == 4:
                row[x] = (row[x] + paeth(a, b, c)) & 255
        prev = bytes(row)
        rows.append(row)
    rgba = []
    for y in range(h):
        row = rows[y]
        for x in range(w):
            if color == 6:
                o = x * 4
                rgba.append((row[o], row[o + 1], row[o + 2], row[o + 3]))
            elif color == 2:
                o = x * 3
                rgba.append((row[o], row[o + 1], row[o + 2], 255))
            elif color == 0:
                v = row[x]
                rgba.append((v, v, v, 255))
            elif color == 4:
                o = x * 2
                v = row[o]
                rgba.append((v, v, v, row[o + 1]))
            else:
                rgba.append((0, 0, 0, 255))
    return rgba, w, h


def write_png(path: Path, rgba, w: int, h: int) -> None:
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            raw.extend(rgba[y * w + x])
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)

    def chunk(typ, payload):
        crc = zlib.crc32(typ + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + typ + payload + struct.pack(">I", crc)

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def scale_sheet_dat(data: bytes, shift: int) -> bytes:
    if len(data) < 4 or shift <= 0:
        return data
    i = 0
    n = struct.unpack_from(">H", data, i)[0]
    i += 2 + n
    if i + 2 > len(data):
        return data
    count = struct.unpack_from(">H", data, i)[0]
    i += 2
    out = bytearray(data[:i])
    for _ in range(count):
        if i + 2 > len(data):
            return data
        ln = struct.unpack_from(">H", data, i)[0]
        if ln > 80 or i + 2 + ln + 12 > len(data):
            return data
        out.extend(data[i:i + 2 + ln])
        i += 2 + ln
        fields = list(struct.unpack_from(">6H", data, i))
        i += 12
        # x, y, w, h are pixel coords; ox, oy are pivot (signed)
        for k in range(4):
            fields[k] = fields[k] >> shift
        for k in range(4, 6):
            v = fields[k]
            if v >= 0x8000:
                v = v - 0x10000
            v = v >> shift
            fields[k] = v & 0xFFFF
        out.extend(struct.pack(">6H", *fields))
    if i != len(data):
        return data
    return bytes(out)


def needed_shift(w: int, h: int, keep: int = MAX_KEEP) -> int:
    s = 0
    while max(w, h) > keep and s < 8:
        w, h = max(1, w // 2), max(1, h // 2)
        s += 1
    return s


def convert_pvr(src: Path, dst: Path, shift: int, keep: int = MAX_KEEP) -> tuple:
    raw = src.read_bytes()
    h = pvr_header(raw)
    body = raw[h["hl"]:h["hl"] + h["datalen"]]
    fmt = h["flags"] & 0xFF
    w, ht = h["w"], h["h"]
    if fmt == 0x10 and h["bpp"] == 16:
        rgba = unpack_4444(body, w, ht)
    elif fmt in (0x18, 0x19) and h["bpp"] == 4:
        if shift <= 0:
            dst.write_bytes(raw)
            return w, ht, False
        rgba = pvrtc_decode(body, w, ht, False)
    else:
        dst.write_bytes(raw)
        return w, ht, False
    did = False
    for _ in range(shift):
        if max(w, ht) <= keep:
            break
        rgba, w, ht = box_half(rgba, w, ht)
        did = True
    write_pvr_4444(dst, w, ht, rgba)
    return w, ht, did


def convert_png(src: Path, dst: Path, shift: int, keep: int = MAX_KEEP) -> tuple:
    rgba, w, h = load_png(src.read_bytes())
    did = False
    for _ in range(shift):
        if max(w, h) <= keep:
            break
        rgba, w, h = box_half(rgba, w, h)
        did = True
    if did:
        write_png(dst, rgba, w, h)
    else:
        dst.write_bytes(src.read_bytes())
    return w, h, did


def convert_tree(app: Path, dest: Path) -> dict:
    data = app / "data"
    if not data.is_dir():
        raise FileNotFoundError("no data/ in %s" % app)
    dest.mkdir(parents=True, exist_ok=True)
    scaled = {}
    n_copy = n_skip = n_scale = 0
    for src in sorted(data.rglob("*")):
        if not src.is_file():
            continue
        if src.name.startswith("._") or src.name in {".DS_Store", "Thumbs.db"}:
            n_skip += 1
            continue
        rel = src.relative_to(data)
        suf = src.suffix.lower()
        if suf in SKIP_AUDIO:
            n_skip += 1
            continue
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if suf == ".pvr":
            hdr = pvr_header(src.read_bytes())
            keep = keep_for(src.stem)
            need = needed_shift(hdr["w"], hdr["h"], keep)
            w, h, did = convert_pvr(src, out, need, keep)
            if did:
                scaled[src.stem] = need
                n_scale += 1
            else:
                n_copy += 1
        elif suf == ".png":
            _, pw, ph = load_png(src.read_bytes())
            keep = keep_for(src.stem)
            need = needed_shift(pw, ph, keep)
            w, h, did = convert_png(src, out, need, keep)
            if did:
                scaled[src.stem] = need
                n_scale += 1
            else:
                n_copy += 1
        elif suf == ".dat":
            # Keep original sprite pixel sizes so Image::draw dest quads stay
            # 480x320. UV wrap from the smaller PNG is undone in fngl using texscale.
            out.write_bytes(src.read_bytes())
            n_copy += 1
        else:
            out.write_bytes(src.read_bytes())
            n_copy += 1
    lines = ["%s %d\n" % (stem, sh) for stem, sh in sorted(scaled.items()) if sh]
    (dest / "texscale").write_text("".join(lines) if lines else "")
    return {"copy": n_copy, "skip": n_skip, "scale": n_scale, "scaled": sorted(scaled)}


def main():
    app, dest = Path(sys.argv[1]), Path(sys.argv[2])
    info = convert_tree(app, dest)
    print("convert_assets: copied %d, scaled %d, skipped audio %d, textures %s"
          % (info["copy"], info["scale"], info["skip"], ",".join(info["scaled"]) or "-"))


if __name__ == "__main__":
    main()
