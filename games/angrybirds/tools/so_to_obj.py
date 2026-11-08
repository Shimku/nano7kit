#!/usr/bin/env python3
"""Convert Android ET_DYN libangrybirds.so to an ET_REL object for the hbapp.

R_ARM_RELATIVE becomes R_ARM_ABS32 against __ab_so_base (addend is the stored
vaddr). JUMP_SLOT / GLOB_DAT become ABS32 against remapped shims (glFoo ->
ab_glFoo). Original vaddr layout is kept so GOT/PLT still line up.

Never commits the .so or the converted object.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ET_REL, ET_DYN, EM_ARM = 1, 3, 40
PT_LOAD, PT_DYNAMIC, PT_ARM_EXIDX = 1, 2, 0x70000001
DT_STRTAB, DT_SYMTAB, DT_REL, DT_RELSZ = 5, 6, 17, 18
DT_JMPREL, DT_PLTRELSZ, DT_SYMENT = 23, 2, 11
DT_INIT_ARRAY, DT_INIT_ARRAYSZ = 25, 27
R_ABS32, R_GLOB_DAT, R_JUMP_SLOT, R_RELATIVE = 2, 21, 22, 23
STB_GLOBAL, STT_FUNC, STT_OBJECT, SHN_UNDEF = 1, 2, 1, 0


def u16(b, o): return struct.unpack_from("<H", b, o)[0]
def u32(b, o): return struct.unpack_from("<I", b, o)[0]


def load_so(data: bytes) -> dict:
    if data[:4] != b"\x7fELF" or data[4] != 1:
        raise SystemExit("not ELF32")
    e_type, e_machine = u16(data, 16), u16(data, 18)
    if e_type != ET_DYN or e_machine != EM_ARM:
        raise SystemExit("need ARM ET_DYN")
    e_phoff, e_phentsize, e_phnum = u32(data, 28), u16(data, 42), u16(data, 44)
    lo, hi, dyn_off, dyn_sz = 0xFFFFFFFF, 0, 0, 0
    exidx_off = exidx_sz = 0
    ph = []
    for i in range(e_phnum):
        o = e_phoff + i * e_phentsize
        p_type, p_off, p_v, p_fs, p_ms = u32(data, o), u32(data, o + 4), u32(data, o + 8), u32(data, o + 16), u32(data, o + 20)
        ph.append((p_type, p_off, p_v, p_fs, p_ms))
        if p_type == PT_LOAD:
            lo = min(lo, p_v)
            hi = max(hi, p_v + p_ms)
        elif p_type == PT_DYNAMIC:
            dyn_off, dyn_sz = p_off, p_fs
        elif p_type == PT_ARM_EXIDX:
            exidx_off, exidx_sz = p_v, p_ms
    size = (hi + 0xFFF) & ~0xFFF
    img = bytearray(size)
    for p_type, p_off, p_v, p_fs, p_ms in ph:
        if p_type == PT_LOAD:
            img[p_v:p_v + p_fs] = data[p_off:p_off + p_fs]
    dyn = []
    for o in range(dyn_off, dyn_off + dyn_sz, 8):
        tag, val = u32(data, o), u32(data, o + 4)
        dyn.append((tag, val))
        if tag == 0:
            break
    d = {t: v for t, v in dyn}

    def dynv(tag, default=0):
        return d.get(tag, default)

    strtab = dynv(DT_STRTAB)
    syment = dynv(DT_SYMENT) or 16
    symoff = dynv(DT_SYMTAB)
    # nchain from hash
    nsym = 0
    for t, v in dyn:
        if t == 4:
            nsym = u32(img, v + 4)
            break
    if nsym <= 0:
        nsym = 8000
    strings = bytes(img[strtab:])
    def zstr(off):
        e = strings.find(b"\0", off)
        return strings[off:e].decode("ascii", "replace") if e >= 0 else ""

    syms = []
    for i in range(nsym):
        so = symoff + i * syment
        st_name, st_value, st_size, st_info, st_shndx = u32(img, so), u32(img, so + 4), u32(img, so + 8), img[so + 12], u16(img, so + 14)
        name = zstr(st_name) if st_name else ""
        bind = st_info >> 4
        typ = st_info & 0xF
        syms.append((name, st_value, st_size, bind, typ, st_shndx))

    relocs = []
    def take_rel(off, sz):
        if not off or not sz:
            return
        for o in range(off, off + sz, 8):
            relocs.append((u32(img, o), u32(img, o + 4)))

    take_rel(dynv(DT_REL), dynv(DT_RELSZ))
    take_rel(dynv(DT_JMPREL), dynv(DT_PLTRELSZ))
    return {
        "img": img, "syms": syms, "relocs": relocs, "size": size,
        "exidx_off": exidx_off, "exidx_count": exidx_sz // 8,
        "init_off": dynv(DT_INIT_ARRAY), "init_count": dynv(DT_INIT_ARRAYSZ) // 4,
        "strings": strings, "strtab": strtab,
    }


REMAP = {
    "malloc": "ab_malloc", "free": "ab_free", "realloc": "ab_realloc",
    "fopen": "ab_fopen", "fclose": "ab_fclose", "fread": "ab_fread",
    "fwrite": "ab_fwrite", "fseek": "ab_fseek", "ftell": "ab_ftell",
    "fflush": "ab_fflush", "fgets": "ab_fgets", "fputc": "ab_fputc",
    "fputs": "ab_fputs", "ungetc": "ab_ungetc", "setvbuf": "ab_setvbuf",
    "freopen": "ab_freopen", "fprintf": "ab_fprintf", "fscanf": "ab_fscanf",
    "printf": "ab_printf", "tmpfile": "ab_tmpfile", "tmpnam": "ab_tmpnam",
    "gettimeofday": "ab_gettimeofday", "time": "ab_time", "clock": "ab_clock",
    "usleep": "ab_usleep", "select": "ab_select", "system": "ab_system",
    "getenv": "ab_getenv", "setlocale": "ab_setlocale",
    "open": "ab_open", "close": "ab_close", "read": "ab_read", "write": "ab_write",
    "lseek": "ab_lseek", "fcntl": "ab_fcntl", "remove": "ab_remove", "rename": "ab_rename",
    "pthread_create": "ab_pthread_create", "pthread_exit": "ab_pthread_exit",
    "pthread_join": "ab_pthread_join", "pthread_mutex_init": "ab_pthread_mutex_init",
    "pthread_mutex_destroy": "ab_pthread_mutex_destroy", "pthread_mutex_lock": "ab_pthread_mutex_lock",
    "pthread_mutex_trylock": "ab_pthread_mutex_trylock", "pthread_mutex_unlock": "ab_pthread_mutex_unlock",
    "pthread_mutexattr_init": "ab_pthread_mutexattr_init",
    "pthread_mutexattr_destroy": "ab_pthread_mutexattr_destroy",
    "pthread_mutexattr_settype": "ab_pthread_mutexattr_settype",
    "pthread_key_create": "ab_pthread_key_create", "pthread_key_delete": "ab_pthread_key_delete",
    "pthread_setspecific": "ab_pthread_setspecific", "pthread_getspecific": "ab_pthread_getspecific",
    "pthread_getschedparam": "ab_pthread_getschedparam",
    "pthread_setschedparam": "ab_pthread_setschedparam", "sched_yield": "ab_sched_yield",
    "srand48": "ab_srand48", "lrand48": "ab_lrand48",
    "__errno": "ab_errno", "__stack_chk_fail": "ab_stack_chk_fail",
    "__cxa_atexit": "ab_cxa_atexit", "__stack_chk_guard": "ab_stack_chk_guard",
    "__dso_handle": "ab_dso_handle",
    "__gnu_Unwind_Find_exidx": "ab_gnu_Unwind_Find_exidx",
}


def remap(name: str) -> str:
    if name.startswith("gl"):
        return "ab_" + name
    return REMAP.get(name, name)


def emit_rel(so: dict) -> bytes:
    img = bytearray(so["img"])
    shstr = b"\0.abso\0.rel.abso\0.symtab\0.strtab\0.shstrtab\0"
    strtab = [b"\0"]
    str_off = {"": 0}

    def add_str(s: str) -> int:
        if s in str_off:
            return str_off[s]
        off = sum(len(x) for x in strtab)
        strtab.append(s.encode("ascii") + b"\0")
        str_off[s] = off
        return off

    # symbol 0 null, 1 __ab_so_base, then imports, then defined exports
    syms = [struct.pack("<IIIBBH", 0, 0, 0, 0, 0, 0)]
    add_str("")
    base_idx = 1
    syms.append(struct.pack("<IIIBBH", add_str("__ab_so_base"), 0, so["size"], (STB_GLOBAL << 4) | STT_OBJECT, 0, 1))
    import_idx = {}
    undef = []
    for r_off, r_info in so["relocs"]:
        typ, si = r_info & 0xFF, r_info >> 8
        if typ in (R_JUMP_SLOT, R_GLOB_DAT, R_ABS32):
            name, val, sz, bind, st, sh = so["syms"][si] if si < len(so["syms"]) else ("", 0, 0, 0, 0, 0)
            if sh == 0 and name:
                undef.append(name)
    for name in sorted(set(undef)):
        mapped = remap(name)
        import_idx[name] = len(syms)
        syms.append(struct.pack("<IIIBBH", add_str(mapped), 0, 0, (STB_GLOBAL << 4) | STT_FUNC, 0, SHN_UNDEF))
    for name, val, sz, bind, st, sh in so["syms"]:
        if not name or sh == 0 or bind != STB_GLOBAL:
            continue
        if name in ("__bss_start__", "_end", "_edata"):
            continue
        syms.append(struct.pack("<IIIBBH", add_str(name), val, sz, (STB_GLOBAL << 4) | st, 0, 1))
    # extras
    for n, v, t in (
        ("ab_so_exidx_off", 0, STT_OBJECT),
        ("ab_so_exidx_count", 4, STT_OBJECT),
        ("ab_so_init_off", 8, STT_OBJECT),
        ("ab_so_init_count", 12, STT_OBJECT),
        ("__ab_so_end", so["size"], STT_OBJECT),
    ):
        # placed after image in a tiny info blob we append? keep as absolute values in a 16-byte trailer
        pass
    info = struct.pack("<IIII", so["exidx_off"], so["exidx_count"], so["init_off"], so["init_count"])
    # append 16-byte info at end of image, grow
    info_off = len(img)
    img += info
    size = len(img)
    # patch those symbols
    extra_syms = [
        ("ab_so_exidx_off", info_off + 0),
        ("ab_so_exidx_count", info_off + 4),
        ("ab_so_init_off", info_off + 8),
        ("ab_so_init_count", info_off + 12),
        ("__ab_so_end", size),
    ]
    for n, v in extra_syms:
        syms.append(struct.pack("<IIIBBH", add_str(n), v, 4, (STB_GLOBAL << 4) | STT_OBJECT, 0, 1))

    rels = []
    for r_off, r_info in so["relocs"]:
        typ, si = r_info & 0xFF, r_info >> 8
        if typ == R_RELATIVE:
            # *loc is vaddr; ABS32 against __ab_so_base uses that as addend
            rels.append(struct.pack("<II", r_off, (base_idx << 8) | R_ABS32))
        elif typ in (R_JUMP_SLOT, R_GLOB_DAT, R_ABS32):
            name, val, sz, bind, st, sh = so["syms"][si] if si < len(so["syms"]) else ("", 0, 0, 0, 0, 0)
            if sh == 0 and name in import_idx:
                if typ != R_ABS32:
                    struct.pack_into("<I", img, r_off, 0)
                rels.append(struct.pack("<II", r_off, (import_idx[name] << 8) | R_ABS32))
            else:
                # internal ABS32: treat like relative to base
                rels.append(struct.pack("<II", r_off, (base_idx << 8) | R_ABS32))

    strtab_b = b"".join(strtab)
    symtab_b = b"".join(syms)
    rel_b = b"".join(rels)

    # layout: ehdr, shdrs x6, .abso, .rel.abso, .symtab, .strtab, .shstrtab
    ehdr_sz, shent, shnum = 52, 40, 6
    shoff = ehdr_sz
    off = ehdr_sz + shent * shnum
    abso_off = off
    off += size
    rel_off = off
    off += len(rel_b)
    sym_off = off
    off += len(symtab_b)
    str_off_b = off
    off += len(strtab_b)
    shstr_off = off
    off += len(shstr)

    def shdr(name_off, typ, flags, addr, soff, sz, link, info, align, entsize):
        return struct.pack("<IIIIIIIIII", name_off, typ, flags, addr, soff, sz, link, info, align, entsize)

    # shstr names
    # 0 null, 1 .abso, 7 .rel.abso, 16 .symtab, 24 .strtab, 32 .shstrtab
    shdrs = b"".join([
        shdr(0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        shdr(1, 1, 0x7, 0, abso_off, size, 0, 0, 16, 0),           # PROGBITS ALLOC WRITE EXEC
        shdr(7, 9, 0x40, 0, rel_off, len(rel_b), 3, 1, 4, 8),     # REL, link symtab, info .abso
        shdr(16, 2, 0, 0, sym_off, len(symtab_b), 4, 1, 4, 16),    # SYMTAB, link strtab
        shdr(24, 3, 0, 0, str_off_b, len(strtab_b), 0, 0, 1, 0),
        shdr(32, 3, 0, 0, shstr_off, len(shstr), 0, 0, 1, 0),
    ])
    ehdr = struct.pack(
        "<16sHHIIIIIHHHHHH",
        b"\x7fELF\x01\x01\x01" + b"\0" * 9,
        ET_REL, EM_ARM, 1, 0, 0, shoff, 0x5000002,
        ehdr_sz, 0, 0, shent, shnum, 5,
    )
    return ehdr + shdrs + bytes(img) + rel_b + symtab_b + strtab_b + shstr


def main():
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    so = load_so(src.read_bytes())
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(emit_rel(so))
    print("so_to_obj: %s -> %s (%d bytes image, %d relocs, exidx %d)"
          % (src.name, dst, so["size"], len(so["relocs"]), so["exidx_count"]))


if __name__ == "__main__":
    main()
