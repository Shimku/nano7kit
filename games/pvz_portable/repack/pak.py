"""PopCap .pak reader/writer (XOR 0xF7). Used by the nano7 repacker on the user's own main.pak."""
import struct

MAGIC, VERSION = 0xBAC04AC0, 0

def read_pak(path):
    raw = open(path, 'rb').read()
    data = bytes(b ^ 0xF7 for b in raw) if len(raw) < 0 else _xor(raw)
    magic, ver = struct.unpack_from('<II', data, 0)
    if magic != MAGIC or ver != VERSION:
        raise SystemExit('not a PopCap pak: ' + path)
    pos = 8; entries = []
    while True:
        flags = data[pos]; pos += 1
        if flags & 0x80: break
        nl = data[pos]; pos += 1
        name = data[pos:pos + nl].decode('latin-1'); pos += nl
        size, ftime = struct.unpack_from('<iq', data, pos); pos += 12
        entries.append([name, size, ftime])
    out = []
    for name, size, ftime in entries:
        out.append((name, ftime, data[pos:pos + size])); pos += size
    return out

def _xor(raw):
    # fast xor via int conversion in chunks
    out = bytearray(raw)
    mask = (b'\xF7' * 65536)
    for i in range(0, len(out), 65536):
        chunk = out[i:i + 65536]
        n = len(chunk)
        out[i:i + n] = (int.from_bytes(chunk, 'little') ^ int.from_bytes(mask[:n], 'little')).to_bytes(n, 'little')
    return bytes(out)

def write_pak(path, files):
    """files: list of (name, filetime, bytes); names use backslashes like the original."""
    hdr = bytearray(struct.pack('<II', MAGIC, VERSION))
    for name, ftime, blob in files:
        nb = name.encode('latin-1')
        if len(nb) > 255: raise SystemExit('name too long: ' + name)
        hdr += bytes([0, len(nb)]) + nb + struct.pack('<iq', len(blob), ftime)
    hdr += bytes([0x80])
    with open(path, 'wb') as f:
        f.write(_xor(bytes(hdr)))
        for _, _, blob in files:
            f.write(_xor(blob))
