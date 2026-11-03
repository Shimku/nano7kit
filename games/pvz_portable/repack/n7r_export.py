#!/usr/bin/env python3
"""Write .n7r compact animations straight from the user's own .reanim files (no engine needed).

Same output as the engine-side exporter (engine/nano7/n7r.cpp Nano7ExportN7R), so the repacker can run on a Mac.
Format (little endian): "N7R1", f32 fps, u16 ntracks, nimg, nfont, ntext, u32 text-blob bytes,
then (u16 len + bytes) for every image name, font name and text, then per track:
u16 name len + name, u16 nframes, u16 nunique, u16 index[nframes], 22-byte records[nunique].
Record: i16 x*8, i16 y*8, half kx, ky, sx, sy, f, a, u16 image id (0xFFFF none), u16 text id, u8 font id (0xFF none), u8 0.
"""
import math, re, struct

PLACEHOLDER = struct.unpack('<f', struct.pack('<f', -10000.0))[0]

def f32(v):
    return struct.unpack('<f', struct.pack('<f', v))[0]

def f2h(f):
    x = struct.unpack('<I', struct.pack('<f', f))[0]
    sign = (x >> 16) & 0x8000
    e = ((x >> 23) & 0xFF) - 127 + 15
    m = x & 0x7FFFFF
    if ((x >> 23) & 0xFF) == 0xFF:
        return sign | 0x7C00 | (0x200 if m else 0)
    if e >= 31:
        return sign | 0x7BFF
    if e <= 0:
        if e < -10:
            return sign
        m |= 0x800000
        sh = 14 - e
        h = m >> sh
        if (m >> (sh - 1)) & 1:
            h += 1
        return (sign | h) & 0xFFFF
    h = sign | (e << 10) | (m >> 13)
    if m & 0x1000:
        h += 1
    return h & 0xFFFF

def q8(v):
    s = v * 8.0
    r = math.floor(abs(s) + 0.5) * (1 if s >= 0 else -1)
    return max(-32768, min(32767, int(r)))

def scanf_float(s):
    """sscanf("%f"): leading float, rest ignored; None when nothing parses."""
    m = re.match(r'\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)', s)
    return f32(float(m.group(1))) if m else None

_TOK = re.compile(r'<(/?)([A-Za-z_][\w]*)\s*(/?)>|([^<]+)', re.S)

def _collapse(s):
    """PopCap XMLParser text rules: whitespace runs outside quotes become one space, none at the ends."""
    out, space, q = [], False, False
    for c in s:
        if c == '"':
            q = not q
        if not q and c.isspace():
            if out:
                space = True
            continue
        if space:
            out.append(' ')
            space = False
        out.append(c)
    return ''.join(out)

def _unescape(s):
    return s.replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"').replace('&apos;', "'").replace('&amp;', '&')

def parse_reanim(text):
    """-> (fps, [(track name, [dict per frame])])"""
    fps = 12.0
    tracks = []
    stack = []
    cur_track = None
    cur_t = None
    field = None
    val = ''
    for m in _TOK.finditer(text):
        close, tag, selfclose, txt = m.groups()
        if txt is not None:
            if field is not None:
                val += txt
            continue
        if not close:
            if selfclose:
                continue
            if tag == 'track':
                cur_track = [None, []]
            elif tag == 't':
                cur_t = {}
            else:
                field = tag
                val = ''
            stack.append(tag)
        else:
            if tag == 'track':
                tracks.append((cur_track[0] or '', cur_track[1]))
                cur_track = None
            elif tag == 't':
                cur_track[1].append(cur_t)
                cur_t = None
            elif tag == field:
                v = _unescape(_collapse(val))
                if cur_t is not None:
                    cur_t[field] = v
                elif cur_track is not None and field == 'name':
                    cur_track[0] = v
                elif field == 'fps':
                    p = scanf_float(v)
                    if p is not None:
                        fps = p
                field = None
            if stack:
                stack.pop()
    return fps, tracks

def export(text):
    fps, tracks = parse_reanim(text)
    imgs, fonts, texts = [], [], ['']
    img_id, font_id, text_id = {}, {}, {'': 0}
    def id_of(m, v, s):
        if s in m:
            return m[s]
        m[s] = len(v)
        v.append(s)
        return m[s]
    out_tracks = []
    for name, frames in tracks:
        prev = {'x': 0.0, 'y': 0.0, 'kx': 0.0, 'ky': 0.0, 'sx': 1.0, 'sy': 1.0, 'f': 0.0, 'a': 1.0}
        prev_img = prev_font = None
        prev_text = ''
        seen = {}
        idx, recs = [], []
        for fr in frames:
            vals = {}
            for k in ('x', 'y', 'kx', 'ky', 'sx', 'sy', 'f', 'a'):
                v = scanf_float(fr[k]) if k in fr else None
                if v is None or v == PLACEHOLDER:
                    v = prev[k]
                else:
                    prev[k] = v
                vals[k] = v
            img = fr.get('i') or None
            if img is None:
                img = prev_img
            else:
                prev_img = img
            font = fr.get('font') or None
            if font is None:
                font = prev_font
            else:
                prev_font = font
            text_v = fr.get('text', '')
            if text_v == '':
                text_v = prev_text
            else:
                prev_text = text_v
            rec = struct.pack('<hhHHHHHHHHBB', q8(vals['x']), q8(vals['y']),
                              f2h(vals['kx']), f2h(vals['ky']), f2h(vals['sx']), f2h(vals['sy']),
                              f2h(vals['f']), f2h(vals['a']),
                              id_of(img_id, imgs, img) if img else 0xFFFF,
                              id_of(text_id, texts, text_v),
                              id_of(font_id, fonts, font) if font else 0xFF, 0)
            if rec not in seen:
                seen[rec] = len(recs)
                recs.append(rec)
            idx.append(seen[rec])
        out_tracks.append((name, idx, recs))
    b = bytearray(b'N7R1')
    b += struct.pack('<f', fps)
    blob = sum(len(s.encode('latin-1')) + 1 for s in texts)
    b += struct.pack('<HHHHI', len(out_tracks), len(imgs), len(fonts), len(texts), blob)
    for s in imgs + fonts + texts:
        e = s.encode('latin-1')
        b += struct.pack('<H', len(e)) + e
    for name, idx, recs in out_tracks:
        e = name.encode('latin-1')
        b += struct.pack('<H', len(e)) + e + struct.pack('<HH', len(idx), len(recs))
        b += struct.pack('<%dH' % len(idx), *idx)
        b += b''.join(recs)
    return bytes(b)
