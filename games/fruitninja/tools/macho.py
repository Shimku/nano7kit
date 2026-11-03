"""Minimal 32-bit ARM Mach-O reader for the Fruit Ninja 1.0 recompiler (nano7kit)."""
import struct

LC_SEGMENT, LC_SYMTAB, LC_DYSYMTAB, LC_UNIXTHREAD = 0x1, 0x2, 0xB, 0x5
INDIRECT_LOCAL, INDIRECT_ABS = 0x80000000, 0x40000000


class MachO:
    def __init__(self, data: bytes):
        self.data = data
        magic, cpu, sub, ftype, ncmds, sizeofcmds, flags = struct.unpack_from("<7I", data, 0)
        if magic != 0xFEEDFACE or cpu != 12:
            raise ValueError("not a thin 32-bit ARM Mach-O")
        self.cpusubtype = sub
        self.segments = []      # (name, vmaddr, vmsize, fileoff, filesize)
        self.sections = {}      # name -> dict
        self.cryptid = 0
        off = 28
        symtab = dysym = None
        for _ in range(ncmds):
            cmd, size = struct.unpack_from("<II", data, off)
            if cmd == LC_SEGMENT:
                seg = data[off + 8:off + 24].split(b"\0")[0].decode()
                va, vs, fo, fs = struct.unpack_from("<4I", data, off + 24)
                nsect = struct.unpack_from("<I", data, off + 48)[0]
                self.segments.append((seg, va, vs, fo, fs))
                for j in range(nsect):
                    q = off + 56 + 68 * j
                    sn = data[q:q + 16].split(b"\0")[0].decode()
                    sa, ss, so, al, ro, nr, fl, r1, r2 = struct.unpack_from("<9I", data, q + 32)
                    self.sections[(seg, sn)] = dict(seg=seg, name=sn, addr=sa, size=ss, offset=so,
                                                    flags=fl, reserved1=r1, reserved2=r2,
                                                    index=len(self.sections) + 1)
            elif cmd == LC_SYMTAB:
                symtab = struct.unpack_from("<4I", data, off + 8)
            elif cmd == LC_DYSYMTAB:
                dysym = struct.unpack_from("<18I", data, off + 8)
            elif cmd == 0x21:
                self.cryptid = struct.unpack_from("<I", data, off + 16)[0]
            off += size
        self.sect_by_index = {s["index"]: s for s in self.sections.values()}
        self.symbols = []       # (addr, name, sect_index, desc, type)
        so, nsy, stro, strs = symtab
        self._symraw = []
        for i in range(nsy):
            sx, ty, se, de, va = struct.unpack_from("<IBBhI", data, so + 12 * i)
            end = data.index(b"\0", stro + sx)
            name = data[stro + sx:end].decode("latin1")
            self._symraw.append((name, ty, se, de, va))
            if (ty & 0x0E) == 0x0E and not (ty & 0xE0):
                self.symbols.append((va, name, se, de & 0xFFFF, ty))
        self.symbols.sort()
        # imports: stub / pointer address -> name
        ilo, nind = dysym[12], dysym[13]
        ind = struct.unpack_from("<%dI" % nind, data, ilo)
        self.imports = {}
        for key in (("__TEXT", "__symbol_stub4"), ("__DATA", "__la_symbol_ptr"), ("__DATA", "__nl_symbol_ptr")):
            s = self.sections.get(key)
            if not s:
                continue
            stride = s["reserved2"] if key[1] == "__symbol_stub4" else 4
            for k in range(s["size"] // stride):
                ii = ind[s["reserved1"] + k]
                if ii & INDIRECT_LOCAL:
                    name = None
                elif ii & INDIRECT_ABS:
                    name = None
                else:
                    name = self._symraw[ii][0]
                self.imports[s["addr"] + k * stride] = (key[1], name)

    def sect(self, seg, name):
        return self.sections.get((seg, name))

    def read(self, va, n):
        for seg, sva, vs, fo, fs in self.segments:
            if sva <= va and va + n <= sva + vs:
                o = va - sva
                if o + n <= fs:
                    return self.data[fo + o:fo + o + n]
                head = self.data[fo + o:fo + fs] if o < fs else b""
                return head + b"\0" * (n - len(head))
        raise KeyError(hex(va))

    def u16(self, va):
        return struct.unpack("<H", self.read(va, 2))[0]

    def u32(self, va):
        return struct.unpack("<I", self.read(va, 4))[0]

    def cstr(self, va, limit=256):
        out = bytearray()
        while len(out) < limit:
            ch = self.read(va + len(out), 1)[0]
            if ch == 0:
                break
            out.append(ch)
        return bytes(out)


def read_span(m, va, n):
    """Bytes for [va, va+n) across segments; unmapped or zero-fill parts read as zero."""
    out = bytearray(n)
    for seg, sva, vs, fo, fs in m.segments:
        lo, hi = max(va, sva), min(va + n, sva + fs)
        if lo < hi:
            out[lo - va:hi - va] = m.data[fo + lo - sva:fo + hi - sva]
    return bytes(out)
