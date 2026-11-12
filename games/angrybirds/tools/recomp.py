#!/usr/bin/env python3
"""Angry Birds 1.0 (armv6 ARM+VFP) to C: nano7kit's own static recompiler.

Reads the user's decrypted `AngryBirds` executable and writes one C function
per original ARM function that the Ka3D/hgr engine can reach (the Objective-C
shell is left out). Integer registers become C locals, VFP s0-s31 live in a
shared register file, and iOS library calls go to the runtime. Nothing from
the game is stored in nano7kit: this writes into the caller's build directory.

usage: recomp.py AngryBirds OUTDIR
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from macho import MachO, read_span  # noqa: E402

ROOT_NAMES = (
    "__ZN9framework4initEPNS_11OSInterfaceEPN2gr7ContextE",
    "__ZN7GameApp6updateEfPN2gr7ContextEi",
    "__ZN7GameAppC2EPN2gr7ContextEPN9framework11OSInterfaceE",
    "__ZN9framework3App10touchBeganERKNS_10TouchEventE",
    "__ZN9framework3App10touchMovedERKNS_10TouchEventE",
    "__ZN9framework3App10touchEndedERKNS_10TouchEventE",
    "__ZN9framework3App3getEv",
    "__ZN9framework3App11setDataPathERKN4lang6StringE",
    "__ZN2io18AppDataInputStream11setRootPathERKN4lang6StringE",
    "__ZN2io19AppDataOutputStream11setRootPathERKN4lang6StringE",
    "__ZN2gr11EGL_ContextC2EiiNS_7Context15OrientationTypeEPFvvE",
    "__ZN2gr11EGL_ContextC1EiiNS_7Context15OrientationTypeEPFvvE",
    # GLES 1.x: EGL_createContext news the context, constructs it, then
    # createShaders registers EGL_Shader_Default templates (sprite-straightalpha
    # and friends). The IPA has no shaders/ files; skipping this throws.
    "__ZN2gr17EGL_createContextEiiNS_7Context15OrientationTypeEPFvvE",
    "__ZN2gr13createShadersEPNS_11EGL_ContextE",
    "__ZN4lang6StringC1EPKc",
    "__ZN4lang6StringC1Ev",
    "__ZN4lang6StringD1Ev",
    "__ZNK4lang6String5c_strEv",
    "__ZN17IphoneOSInterface18getDefaultDataPathEv",
    "__ZN17IphoneOSInterface14setOrientationEN2gr7Context15OrientationTypeE",
    "__ZN17IphoneOSInterface11loadShadersERKN4lang6StringEb",
    "__ZN17IphoneOSInterface13setResolutionEii",
    "__ZN7GameApp7keyDownEN9framework3App7KeyTypeE",
    "__ZN7GameApp5keyUpEN9framework3App7KeyTypeE",
    # Shell calls these on every level change. Const getTable was missing from
    # the reachable set, so AB_LuaGetTable was 0 and setTheme never saw objects.theme.
    "__ZNK3lua8LuaTable8getTableEPKc",
    "__ZNK3lua8LuaTable9getNumberEPKc",
    "__ZNK3lua8LuaTable9getStringEPKc",
    "__ZN7GameLua8setThemeEN4lang6StringE",
    "__ZN7GameLua9loadLevelEN4lang6StringE",
    "__ZN3lua9LuaObject4callEPKc",
)
# silent: OpenAL is stubbed in the runtime; skip ObjC UIApplicationMain path
STUBS = {
    "__ZN7GameApp13activateAudioEb",
    "__ZN9framework3App13activateAudioEb",
    # libmad III walks a NULL-80 pointer. Skip the mp3 reader so createAudio
    # still registers each clip; SFX wavs load as silent stubs.
    "__ZN5audio11AudioReader8init_mp3Ev",
    "__ZN5audio11AudioReader14readHeader_mp3Ev",
    "__ZN5audio11AudioReader12readData_mp3EPvii",
    "__ZN5audio11AudioReader10deinit_mp3Ev",
    "_init_layer3",
    # IphoneOSInterface::setOrientation walks an ObjC UIDevice at this+4. The shell
    # never constructs that object, so the method is a no-op (orientation is already landscape).
    "__ZN17IphoneOSInterface14setOrientationEN2gr7Context15OrientationTypeE",
}
RET_MAGIC = 0xFFFF0001
IMPORT_BASE = 0xFE000000

INLINE = {
    "___divsi3": "r0=SDIV(r0,r1);",
    "___modsi3": "r0=SMOD(r0,r1);",
    "___udivsi3": "r0=r1?r0/r1:0;",
    "___umodsi3": "r0=r1?r0%r1:r0;",
    "___divdi3": "{int64_t a_=(int64_t)(((uint64_t)r1<<32)|r0),b_=(int64_t)(((uint64_t)r3<<32)|r2);"
                 "int64_t q_=b_?a_/b_:0;r0=(uint32_t)q_;r1=(uint32_t)(q_>>32);}",
    "___moddi3": "{int64_t a_=(int64_t)(((uint64_t)r1<<32)|r0),b_=(int64_t)(((uint64_t)r3<<32)|r2);"
                 "int64_t q_=b_?a_%b_:a_;r0=(uint32_t)q_;r1=(uint32_t)(q_>>32);}",
    "___muldi3": "{uint64_t q_=(((uint64_t)r1<<32)|r0)*(((uint64_t)r3<<32)|r2);r0=(uint32_t)q_;r1=(uint32_t)(q_>>32);}",
    "___floatdidf": "{double d_=(double)(int64_t)(((uint64_t)r1<<32)|r0);D2R(d_);}",
    "___floatdisf": "r0=F2U((float)(int64_t)(((uint64_t)r1<<32)|r0));",
    "___fixdfdi": "{double a_=R2D(r0,r1);int64_t q_=a_!=a_?0:a_>=9223372036854775807.0?0x7fffffffffffffffLL:"
                  "a_<=-9223372036854775808.0?(int64_t)0x8000000000000000ULL:(int64_t)a_;"
                  "r0=(uint32_t)q_;r1=(uint32_t)(q_>>32);}",
}

COND = ["z_", "!z_", "c_", "!c_", "n_", "!n_", "v_", "!v_", "(c_&&!z_)", "(!c_||z_)",
        "(n_==v_)", "(n_!=v_)", "(!z_&&n_==v_)", "(z_||n_!=v_)", "1", "0"]
REG = ["r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8", "r9", "r10", "r11", "r12", "sp", "lr", "pc"]
SETJMP = {"_setjmp", "__setjmp", "_sigsetjmp"}
LONGJMP = {"_longjmp", "__longjmp", "_siglongjmp"}
SJLJ_REGISTER = "__Unwind_SjLj_Register"


def cname(name):
    out = []
    for ch in name:
        out.append(ch if ch.isalnum() or ch == "_" else "_")
    return "".join(out)


class Image:
    def __init__(self, path):
        self.m = MachO(open(path, "rb").read())
        m = self.m
        self.text = m.sect("__TEXT", "__text")
        self.stubs = m.sect("__TEXT", "__symbol_stub4")
        self.t0, self.t1 = self.text["addr"], self.text["addr"] + self.text["size"]
        funcs = [(a, n, d) for a, n, se, d, ty in m.symbols if se == self.text["index"]]
        funcs.sort()
        self.fname = {}
        self.fthumb = {}
        for a, n, d in funcs:
            if a not in self.fname:
                self.fname[a] = n
                self.fthumb[a] = bool(d & 8)
        self.fstart = sorted(self.fname)
        self.fend = {}
        for i, a in enumerate(self.fstart):
            self.fend[a] = self.fstart[i + 1] if i + 1 < len(self.fstart) else self.t1
        self.byname = {n: a for a, n in self.fname.items()}
        dsec = {s["index"] for s in m.sections.values() if s["seg"] == "__DATA" or s["name"] in ("__const", "__cstring")}
        self.dsyms = sorted((a, n) for a, n, se, d, ty in m.symbols if se in dsec)
        self.stub_imports = {a: n for a, (k, n) in m.imports.items() if k == "__symbol_stub4"}

    def is_stub(self, a):
        return self.stubs and self.stubs["addr"] <= a < self.stubs["addr"] + self.stubs["size"]

    def skip(self, name):
        if name.startswith(("-[", "+[")):
            return True
        if "objc" in name.lower() or "OBJC" in name:
            return True
        return False


class Ins:
    __slots__ = ("a", "w", "kind", "targets", "term", "call", "rd_pc", "sw")

    def __init__(self, a, w):
        self.a, self.w = a, w
        self.kind, self.targets, self.term, self.call, self.rd_pc = "op", [], False, None, False
        self.sw = None  # (rm, n, ents) for ARM LDRLS pc, [pc, rm, lsl #2] switches


def parse_pc_switch(img, a, w, start, end):
    """ARM `LDRLS pc, [pc, rm, lsl #2]` jump table. Always preceded here by CMP rm, #n."""
    if (w & 0x0E5FF000) != 0x061FF000:
        return None
    if ((w >> 16) & 15) != 15 or ((w >> 12) & 15) != 15:
        return None
    if ((w >> 25) & 1) != 1:
        return None
    if (w & 0xFF0) != 0x100:  # LSL #2, immediate shift
        return None
    rm = w & 15
    prev = img.m.u32(a - 4)
    # CMP immediate: bits 27-20 are I=1 opcode=1010 S=1 (0x35), Rn = rm
    if (prev & 0x0FF00000) != 0x03500000:
        return None
    if ((prev >> 16) & 15) != rm:
        return None
    n = rot_imm(prev & 0xFF, ((prev >> 8) & 0xF) * 2)
    if n > 256:
        return None
    tab = a + 8
    ents = []
    for i in range(n + 1):
        t = img.m.u32(tab + 4 * i)
        if not (img.t0 <= t < img.t1):
            return None
        ents.append(t)
    return rm, n, ents


def rot_imm(imm, rot):
    rot &= 31
    if rot == 0:
        return imm
    return ((imm >> rot) | ((imm << (32 - rot)) & 0xFFFFFFFF)) & 0xFFFFFFFF


def decode(img, a):
    w = img.m.u32(a)
    ins = Ins(a, w)
    cond = w >> 28
    if cond == 15:
        if (w >> 25) == 0b101:
            off = w & 0xFFFFFF
            if off & 0x800000:
                off -= 0x1000000
            h = (w >> 24) & 1
            ins.kind, ins.call, ins.term = "blx", (a + 8 + off * 4 + h * 2) & ~1, False
            return ins
        ins.kind, ins.term = "undef", True
        return ins
    op = (w >> 25) & 7
    if op == 0b101:
        off = w & 0xFFFFFF
        if off & 0x800000:
            off -= 0x1000000
        tgt = a + 8 + off * 4
        if w & (1 << 24):
            ins.kind, ins.call = "bl", tgt
        else:
            ins.kind, ins.targets, ins.term = "b", [tgt], True
        return ins
    # BX rm (0x012FFF10) / BLX rm (0x012FFF30). Bit 5 selects BLX; the old
    # mask required it to be 0, so lua_newstate's BLX r6 became a no-op.
    if (w & 0x0FFFFFD0) == 0x012FFF10:
        ins.kind = "blxr" if (w & 0x20) else "bx"
        # Conditional BX (BXNE lr as an early return) must not end the scan:
        # luaO_rawequalObj falls through to a type switch after BXNE lr.
        ins.term = ins.kind == "bx" and cond == 14
        return ins
    # LDM/STM
    if op == 0b100:
        ins.kind = "ldm" if (w & (1 << 20)) else "stm"
        if (w & (1 << 20)) and (w & (1 << 15)):
            ins.rd_pc = True
            ins.term = cond == 14
        return ins
    # LDR to PC. LDRLS pc, [pc, rm, lsl #2] is an ARM switch table (see parse_pc_switch).
    # `MOV lr, pc; LDR pc, [vtbl, #slot]` is a virtual BL, not a tail call: Ka3D
    # does this 515 times (LuaState::loadFile's stream->read is one).
    if op in (0b010, 0b011) and (w & (1 << 20)) and ((w >> 12) & 15) == 15 and not (op == 0b011 and (w & 0x10)):
        prev = img.m.u32(a - 4) if a >= 4 else 0
        if (prev & 0x0FFFFFFF) == 0x01A0E00F:
            ins.kind, ins.term, ins.rd_pc = "vcall", False, True
            return ins
        ins.kind, ins.term, ins.rd_pc = "ldrpc", True, True
        return ins
    # data processing to PC
    if op in (0, 1) and not ((w & 0x90) == 0x90 and op == 0):
        if ((w >> 16) & 0x1F) not in (0x10, 0x12, 0x14, 0x16) or op == 1:
            opcode = (w >> 21) & 15
            rd = (w >> 12) & 15
            if opcode not in (8, 9, 10, 11) and rd == 15:
                ins.kind, ins.term, ins.rd_pc = "alupc", True, True
                return ins
    ins.kind = "op"
    return ins


def func_body(img, start, end):
    body, labels, todo, problems = {}, set(), [start], []
    switch_tables = {}
    stub = next((va for va, n in img.stub_imports.items() if n == SJLJ_REGISTER), 0)
    while True:
        while todo:
            a = todo.pop()
            while a not in body:
                if not (start <= a < end):
                    problems.append("falls outside at %x" % a)
                    break
                if a + 4 > end:
                    problems.append("truncated at %x" % a)
                    break
                ins = decode(img, a)
                if ins.kind == "ldrpc":
                    sw = parse_pc_switch(img, a, ins.w, start, end)
                    if sw:
                        rm, n, ents = sw
                        ins.kind, ins.sw = "switch", sw
                        switch_tables[a] = sw
                        for t in ents:
                            if start <= t < end:
                                labels.add(t)
                                todo.append(t)
                            elif t in img.fname:
                                todo.append(t)
                        # default is the next instruction (typically B); do not decode the table
                        default = a + 4
                        if start <= default < end:
                            labels.add(default)
                            todo.append(default)
                        body[a] = ins
                        break
                body[a] = ins
                if ins.kind == "bl" and start < ins.call < end and not img.is_stub(ins.call):
                    # far jump inside the function (compiler uses BL as local jump)
                    # only if the target is not a known function start
                    if ins.call not in img.fname:
                        ins.kind, ins.targets, ins.term = "farjump", [ins.call], True
                        labels.add(ins.call)
                        todo.append(ins.call)
                        break
                if ins.kind in ("b", "bcond"):
                    t = ins.targets[0]
                    if start <= t < end:
                        labels.add(t)
                        todo.append(t)
                if ins.kind == "b" and (ins.w >> 28) != 14:
                    # conditional B was classified as term; also continue
                    ins.term = False
                    ins.kind = "bcond"
                    labels.add(ins.targets[0])
                    todo.append(ins.targets[0])
                    a += 4
                    continue
                if ins.term:
                    break
                a += 4
        # SjLj landing pads live in the function but are only reached by longjmp.
        # The compiler stores the pad address in a literal pool word loaded just
        # before BL __Unwind_SjLj_Register; those pads are not branch targets.
        extra = []
        if stub:
            for pa, ins in body.items():
                if ins.kind != "bl" or ins.call != stub:
                    continue
                b, steps = pa - 4, 0
                while b >= start and steps < 24:
                    w = img.m.u32(b)
                    if (w & 0x0F7F0000) == 0x051F0000:
                        u_bit, imm = (w >> 23) & 1, w & 0xFFF
                        loc = (b + 8) + (imm if u_bit else -imm)
                        if img.t0 <= loc < img.t1 and (loc & 3) == 0:
                            pad = img.m.u32(loc)
                            if start <= pad < end and pad not in body and (pad & 3) == 0:
                                extra.append(pad)
                    b -= 4
                    steps += 1
        if not extra:
            break
        for pad in extra:
            labels.add(pad)
            todo.append(pad)
    return body, labels, switch_tables, problems


def reachable(img):
    m = img.m
    roots = [img.byname[n] for n in ROOT_NAMES if n in img.byname]
    mi = m.sect("__DATA", "__mod_init_func")
    if mi:
        for k in range(mi["size"] // 4):
            roots.append(m.u32(mi["addr"] + 4 * k) & ~1)
    import bisect
    dstarts = [x[0] for x in img.dsyms]
    for seg, va, vs, fo, fs in m.segments:
        if seg != "__DATA":
            continue
        for p in range(va, va + fs, 4):
            w = m.u32(p)
            if img.t0 <= w < img.t1 and (w & ~1) in img.fname:
                j = bisect.bisect_right(dstarts, p) - 1
                owner = img.dsyms[j][1] if j >= 0 else ""
                if "OBJC" in owner or owner.startswith(("l_OBJC", "__OBJC")):
                    continue
                if img.skip(owner):
                    continue
                if any(s for s in ("objc_", "__objc") if s in owner):
                    continue
                roots.append(w & ~1)
    seen = set()
    stack = list(roots)
    while stack:
        f = stack.pop()
        if f in seen or f not in img.fname:
            continue
        name = img.fname[f]
        if img.skip(name):
            continue
        seen.add(f)
        body, labels, sw, probs = func_body(img, f, img.fend[f])
        for ins in body.values():
            if ins.kind in ("bl", "blx") and ins.call is not None and not img.is_stub(ins.call):
                stack.append(ins.call & ~1)
            if ins.kind in ("b", "bcond") and ins.targets:
                t = ins.targets[0]
                if not (f <= t < img.fend[f]):
                    stack.append(t & ~1)
        # ARM literal pools sit after the last instruction. luaL_newstate passes
        # &l_alloc in r0 from a pool word; without this those statics are never
        # translated and lua_newstate's allocator call returns 0.
        for p in range(f, img.fend[f], 4):
            w = m.u32(p) & ~1
            if w in img.fname and not img.skip(img.fname[w]):
                stack.append(w)
    return seen


class Emitter:
    def __init__(self, img, funcs):
        self.img = img
        self.funcs = funcs
        self.used_imports = set()
        self.missing_calls = defaultdict(int)
        self.stats = defaultdict(int)

    def fn(self, a):
        return "F_%x" % a

    def call_target(self, tgt):
        img = self.img
        if img.is_stub(tgt):
            name = img.stub_imports.get(tgt)
            if name in INLINE:
                self.stats["inline_" + name] += 1
                return INLINE[name]
            if name in SETJMP:
                self.used_imports.add(name)
                return "SP=sp;r0=(uint32_t)setjmp(*rt_jmp(r0));"
            if name in LONGJMP:
                self.used_imports.add(name)
                return "SP=sp;longjmp(*rt_jmp(r0),(int)r1);"
            self.used_imports.add(name)
            return "SP=sp;CALLQ(I_%s(r0,r1,r2,r3));" % cname(name)
        t = tgt & ~1
        if t in self.funcs:
            # Static terrain draw: the runtime patches the 2D xf so a swipe
            # is not the iPad castle close-up. Direct CALLQ skips rt_icall.
            if t in (0x130268, 0x130a48, 0x6c1b4):
                return "SP=sp;CALLQ(rt_icall(0x%xu,r0,r1,r2,r3));" % t
            return "SP=sp;CALLQ(%s(r0,r1,r2,r3));" % self.fn(t)
        self.missing_calls[t] += 1
        return "SP=sp;CALLQ(rt_icall(0x%xu,r0,r1,r2,r3));" % t

    def tail(self, t):
        t = (t & 0xFFFFFFFF) & ~1
        if t in self.funcs:
            return "SP=sp;return %s(r0,r1,r2,r3);" % self.fn(t)
        if not (self.img.t0 <= t < self.img.t1):
            return "rt_undef(0x%xu);" % t
        self.missing_calls[t] += 1
        return "SP=sp;return rt_icall(0x%xu,r0,r1,r2,r3);" % t

    def wrap(self, w, stmt):
        cond = w >> 28
        if cond == 14:
            return stmt
        # BLX immediate is encoded with cond=15; that is the opcode, not "never".
        if cond == 15:
            if (w >> 25) == 0b101:
                return stmt
            return ";"
        return "if(%s){%s}" % (COND[cond], stmt)

    def emit_function(self, start, out):
        img = self.img
        end = img.fend[start]
        body, labels, switches, problems = func_body(img, start, end)
        for p in problems:
            self.stats["problem"] += 1
        name = img.fname[start]
        lines = []
        lines.append("/* %s */" % name.replace("*/", "* /"))
        lines.append("uint64_t %s(uint32_t r0,uint32_t r1,uint32_t r2,uint32_t r3){" % self.fn(start))
        lines.append(" uint32_t r4=0,r5=0,r6=0,r7=0,r8=0,r9=0,r10=0,r11=0,r12=0,lr=RET_MAGIC,sp=SP;"
                     "uint32_t n_=0,z_=0,c_=0,v_=0;uint8_t*const M=g_mem;(void)n_;(void)z_;(void)c_;(void)v_;"
                     "(void)r4;(void)r5;(void)r6;(void)r7;(void)r8;(void)r9;(void)r10;(void)r11;(void)r12;(void)lr;")
        if name in STUBS:
            lines.append(" (void)r0;(void)r1;(void)r2;(void)r3;return 0;}")
            out.append("\n".join(l for l in lines if not l.startswith(" uint32_t r4")))
            return
        lines.append(" ENTER(0x%x);" % start)
        if name in ("__ZN3lua8LuaTableD1Ev", "__ZN3lua8LuaTableD0Ev", "__ZN3lua8LuaTableD2Ev"):
            lines.append(" if(!r0) RETURN();")
        order = sorted(body)
        for a in order:
            ins = body[a]
            if a in labels:
                lines.append("L_%x:;" % a)
            if a == 0xa112c:
                # luaH_next: invalid key used to luaG_runerror and kill the frame
                lines.append(" r0=0;goto L_a15e4;")
                continue
            stmt = self.translate(ins, start, end, labels, body)
            lines.append(" " + stmt)
        lines.append(" RETURN();")
        lines.append("}")
        out.append("\n".join(lines))
        self.stats["functions"] += 1
        self.stats["instructions"] += len(body)

    def shifter(self, w, a):
        """C expressions (val, carry_rhs or None) for the ARM shifter operand."""
        if w & (1 << 25):
            imm = w & 0xFF
            rot = ((w >> 8) & 0xF) * 2
            val = rot_imm(imm, rot)
            if rot:
                return "0x%xu" % val, str(val >> 31)
            return "0x%xu" % val, None
        rm = w & 15
        src = "0x%xu" % (a + 8) if rm == 15 else REG[rm]
        styp = (w >> 5) & 3
        if w & 0x10:
            rs = (w >> 8) & 15
            n = "(%s&255u)" % REG[rs]
            if styp == 0:
                return "lsl_reg(%s,%s)" % (src, n), "lsl_reg_c(%s,%s,c_)" % (src, n)
            if styp == 1:
                return "lsr_reg(%s,%s)" % (src, n), "lsr_reg_c(%s,%s,c_)" % (src, n)
            if styp == 2:
                return "asr_reg(%s,%s)" % (src, n), "asr_reg_c(%s,%s,c_)" % (src, n)
            return "ror_reg(%s,%s)" % (src, n), "ror_reg_c(%s,%s,c_)" % (src, n)
        amt = (w >> 7) & 31
        if styp == 0:
            if amt == 0:
                return src, None
            return "(%s<<%du)" % (src, amt), "((%s>>%du)&1u)" % (src, 32 - amt)
        if styp == 1:
            if amt == 0:
                return "0u", "(%s>>31)" % src
            return "(%s>>%du)" % (src, amt), "((%s>>%du)&1u)" % (src, amt - 1)
        if styp == 2:
            if amt == 0:
                return "(uint32_t)((int32_t)%s>>31)" % src, "(%s>>31)" % src
            return "(uint32_t)((int32_t)%s>>%du)" % (src, amt), "((%s>>%du)&1u)" % (src, amt - 1)
        if amt == 0:
            return "((c_<<31)|(%s>>1))" % src, "(%s&1u)" % src
        return "((%s>>%du)|(%s<<%du))" % (src, amt, src, 32 - amt), "((%s>>%du)&1u)" % (src, amt - 1)

    def translate(self, ins, start, end, labels, body):
        a, w = ins.a, ins.w
        k = ins.kind
        if k == "bl" or k == "blx":
            tgt = ins.call
            if self.img.is_stub(tgt) and self.img.stub_imports.get(tgt) == SJLJ_REGISTER:
                cases = "".join(
                    "case 0x%xu:goto L_%x;" % (lab, lab)
                    for lab in sorted(labels)
                    if isinstance(lab, int) and start <= lab < end and lab in body
                )
                body = (
                    "lr=0x%xu;SP=sp;{uint32_t ctx_=r0;"
                    "if(setjmp(*rt_sjlj_buf(ctx_))){uint32_t c_=rt_sjlj_landed();"
                    "sp=L32(c_+40u);r7=L32(c_+32u);r0=L32(c_+8u);r1=L32(c_+12u);SP=sp;"
                    "switch(L32(c_+36u)){%s default:SP=sp;return rt_icall(L32(c_+36u),r0,r1,r2,r3);}"
                    "}else{CALLQ(I_%s(ctx_,r1,r2,r3));}}"
                    % (a + 4, cases, cname(SJLJ_REGISTER))
                )
                self.used_imports.add(SJLJ_REGISTER)
                self.stats["sjlj_register"] += 1
                return self.wrap(w, body)
            body = "lr=0x%xu;" % (a + 4) + self.call_target(tgt)
            return self.wrap(w, body)
        if k == "farjump":
            return self.wrap(w, "lr=0x%xu;goto L_%x;" % (a + 4, ins.targets[0]))
        if k == "switch":
            rm, n, ents = ins.sw
            cases = "".join(
                "case %du:goto L_%x;" % (i, t) if start <= t < end else
                "case %du:SP=sp;return rt_icall(0x%xu,r0,r1,r2,r3);" % (i, t)
                for i, t in enumerate(ents)
            )
            return self.wrap(w, "switch(%s){%s}" % (REG[rm], cases))
        if k == "bcond" or k == "b":
            t = ins.targets[0] & 0xFFFFFFFF
            if start <= t < end:
                return self.wrap(w, "goto L_%x;" % t)
            return self.wrap(w, self.tail(t))
        if k == "bx" or k == "blxr":
            rm = w & 15
            src = "0x%xu" % (a + 8) if rm == 15 else REG[rm]
            if k == "bx":
                if rm == 14:
                    return self.wrap(w, "RETURN();")
                self.stats["bx_indirect"] += 1
                return self.wrap(w, "SP=sp;return rt_icall(%s,r0,r1,r2,r3);" % src)
            return self.wrap(w, "lr=0x%xu;SP=sp;CALLQ(rt_icall(%s,r0,r1,r2,r3));" % (a + 4, src))
        if k == "vcall":
            addr = self.ldr_addr(a, w)
            self.stats["vcall"] += 1
            return self.wrap(w, "lr=0x%xu;SP=sp;CALLQ(rt_icall(L32(%s),r0,r1,r2,r3));" % (a + 4, addr))
        return self.wrap(w, self.op(a, w))

    def set_reg(self, rd, expr, a):
        if rd == 15:
            self.stats["pc_write"] += 1
            return "SP=sp;return rt_icall(%s,r0,r1,r2,r3);" % expr
        if rd == 13:
            return "sp=%s;" % expr
        return "%s=%s;" % (REG[rd], expr)

    def op(self, a, w):
        top = (w >> 25) & 7
        # multiply / extra / DP
        if top == 0 and (w & 0x90) == 0x90:
            # Bits 7 and 4 set: multiply (S=H=0, including UMULL/SMULL) or
            # extra load/store (LDRH/STRH/LDRSB/LDRD, S or H set). The old
            # mul mask required bits 27-22 = 0, so UMULL (bits 23-21 = 100)
            # became STRB to the 0x51eb851f "divide by 100" magic.
            s_bit, h = (w >> 6) & 1, (w >> 5) & 1
            if (w & 0x0F0000F0) == 0x00000090 and not s_bit and not h:
                return self.mul(w)
            if (w & 0x0E000090) == 0x00000090:
                return self.extra_ls(a, w)
            self.stats["unknown_op"] += 1
            self.stats["unk_%08x" % w] += 1
            return "rt_undef(0x%xu);/*%08x*/" % (a, w)
        if top in (0, 1):
            if top == 0 and ((w >> 23) & 3) == 2 and not (w & (1 << 20)):
                return self.misc(a, w)
            return self.dp(a, w)
        if top in (2, 3):
            if top == 3 and (w & 0x10):
                return self.media(a, w)
            return self.ls(a, w)
        if top == 4:
            return self.block(a, w)
        if top == 6:
            return self.vfp_ls(a, w)
        if top == 7:
            if w & (1 << 24):
                return "rt_swi(0x%x);" % (w & 0xFFFFFF)
            return self.vfp_cdp(a, w)
        self.stats["unknown_op"] += 1
        self.stats["unk_%08x" % w] += 1
        return "rt_undef(0x%xu);/*%08x*/" % (a, w)

    def mul(self, w):
        a_bit = (w >> 21) & 15
        s = (w >> 20) & 1
        rd = (w >> 16) & 15
        rn = (w >> 12) & 15
        rs = (w >> 8) & 15
        rm = w & 15
        flags = "NZ(%s);" % REG[rd] if s else ""
        if a_bit == 0:
            return "%s=%s*%s;%s" % (REG[rd], REG[rm], REG[rs], flags)
        if a_bit == 1:
            return "%s=%s*%s+%s;%s" % (REG[rd], REG[rm], REG[rs], REG[rn], flags)
        if a_bit in (4, 6):  # umull / smull
            signed = "int64_t" if a_bit == 6 else "uint64_t"
            cast = "(int32_t)" if a_bit == 6 else ""
            return "{%s q_=(%s)(%s%s)*(%s%s);%s=(uint32_t)q_;%s=(uint32_t)(q_>>32);%s}" % (
                signed, signed, cast, REG[rm], cast, REG[rs], REG[rn], REG[rd], flags)
        if a_bit in (5, 7):  # umlal / smlal
            signed = "int64_t" if a_bit == 7 else "uint64_t"
            cast = "(int32_t)" if a_bit == 7 else ""
            return "{%s q_=(%s)(((%s)%s<<32)|%s)+(%s)(%s%s)*(%s%s);%s=(uint32_t)q_;%s=(uint32_t)(q_>>32);%s}" % (
                signed, signed, signed, REG[rd], REG[rn], signed, cast, REG[rm], cast, REG[rs], REG[rn], REG[rd], flags)
        return "rt_undef(0);/*mul %x*/" % a_bit

    def extra_ls(self, a, w):
        p, u, i, ww, l = [(w >> b) & 1 for b in (24, 23, 22, 21, 20)]
        rn, rd = (w >> 16) & 15, (w >> 12) & 15
        s_bit, h = (w >> 6) & 1, (w >> 5) & 1
        if i:
            off = ((w >> 4) & 0xF0) | (w & 0xF)
            off_e = "%du" % off
        else:
            off_e = REG[w & 15]
        if not u:
            off_e = "-(int)%s" % off_e if not i else "-%du" % (((w >> 4) & 0xF0) | (w & 0xF))
        base = "0x%xu" % (a + 8) if rn == 15 else REG[rn]
        if p:
            addr = "%s+%s" % (base, off_e)
        else:
            addr = base
        wb = ""
        if (ww or not p) and rn != 15:
            newb = "%s+%s" % (base, off_e) if not p else addr
            wb = self.set_reg(rn, newb, a)
        if l and rn == 15 and p and i:
            imm8 = ((w >> 4) & 0xF0) | (w & 0xF)
            loc = (a + 8) + (imm8 if u else -imm8)
            n = 1 if (s_bit and not h) else 2 if h else 4
            bits = self.fold_pc(loc, n)
            if bits is not None:
                if s_bit and h:
                    self.stats["pc_fold"] += 1
                    return self.set_reg(rd, "(uint32_t)(int32_t)(int16_t)0x%xu" % (bits & 0xFFFF), a)
                if s_bit:
                    self.stats["pc_fold"] += 1
                    return self.set_reg(rd, "(uint32_t)(int32_t)(int8_t)0x%xu" % (bits & 0xFF), a)
                if h:
                    self.stats["pc_fold"] += 1
                    return self.set_reg(rd, "0x%xu" % bits, a)
        # ARMv5TE: LDRD is L=0 S=1 H=0; STRD is L=0 S=1 H=1. L=1 S=1 H=0 is LDRSB.
        if s_bit and not l:
            if h:
                src_lo = "0x%xu" % (a + 8) if rd == 15 else REG[rd]
                src_hi = "0x%xu" % (a + 8) if (rd + 1) == 15 else REG[rd + 1]
                return "rt_check_align8(%s);S32(%s,%s);S32((%s)+4,%s);%s" % (addr, addr, src_lo, addr, src_hi, wb)
            lo = self.set_reg(rd, "L32(%s)" % addr, a)
            hi = self.set_reg(rd + 1, "L32((%s)+4)" % addr, a)
            return "rt_check_align8(%s);" % addr + lo + hi + wb
        if l:
            if s_bit and h:
                load = "(uint32_t)(int32_t)(int16_t)L16(%s)" % addr
            elif s_bit:
                load = "(uint32_t)(int32_t)(int8_t)L8(%s)" % addr
            elif h:
                load = "L16(%s)" % addr
            else:
                self.stats["unknown_op"] += 1
                return "rt_undef(0x%xu);/*extra_ls %08x*/" % (a, w)
            return self.set_reg(rd, load, a) + wb
        if h:
            return "S16(%s,%s);%s" % (addr, REG[rd], wb)
        self.stats["unknown_op"] += 1
        return "rt_undef(0x%xu);/*extra_ls %08x*/" % (a, w)

    def dp(self, a, w):
        opcode = (w >> 21) & 15
        s = (w >> 20) & 1
        rn, rd = (w >> 16) & 15, (w >> 12) & 15
        val, carry = self.shifter(w, a)
        n = "0x%xu" % (a + 8) if rn == 15 else REG[rn]
        setc = ("c_=%s;" % carry) if carry is not None and s and opcode in (0, 1, 12, 13, 14, 15, 8, 9) else ""
        ops = {
            0: "%s&%s" % (n, val),
            1: "%s^%s" % (n, val),
            8: "%s&%s" % (n, val),
            9: "%s^%s" % (n, val),
            12: "%s|%s" % (n, val),
            13: val,
            14: "%s&~%s" % (n, val),
            15: "~%s" % val,
        }
        if opcode in (2, 10):  # SUB / CMP
            if opcode == 10:
                return setc + "SUBS(%s,%s);" % (n, val)
            if s:
                return setc + "SUBSD(%s,%s,%s);" % (REG[rd] if rd != 15 else "r0", n, val) + (
                    self.set_reg(rd, "r0", a) if rd == 15 else "")
            return setc + self.set_reg(rd, "(%s-%s)" % (n, val), a)
        if opcode == 3:  # RSB
            if s:
                return setc + "SUBSD(%s,%s,%s);" % (REG[rd] if rd != 15 else "r0", val, n) + (
                    self.set_reg(rd, "r0", a) if rd == 15 else "")
            return setc + self.set_reg(rd, "(%s-%s)" % (val, n), a)
        if opcode in (4, 11):  # ADD / CMN
            if opcode == 11:
                return setc + "ADDS_(%s,%s);" % (n, val)
            if s:
                dst = REG[rd] if rd != 15 else "r0"
                extra = self.set_reg(rd, "r0", a) if rd == 15 else ""
                return setc + "ADDSD(%s,%s,%s);" % (dst, n, val) + extra
            return setc + self.set_reg(rd, "(%s+%s)" % (n, val), a)
        if opcode == 5:
            return setc + "ADCS(%s,%s,%s);" % (REG[rd], n, val)
        if opcode == 6:
            return setc + "SBCS(%s,%s,%s);" % (REG[rd], n, val)
        if opcode == 7:
            return setc + "SBCS(%s,%s,%s);" % (REG[rd], val, n)
        expr = ops.get(opcode)
        if expr is None:
            return "rt_undef(0x%xu);" % a
        if opcode in (8, 9):
            return setc + "{uint32_t t_=%s;NZ(t_);}" % expr
        stmt = self.set_reg(rd, expr, a)
        if s and rd != 15:
            stmt += "NZ(%s);" % REG[rd]
        return setc + stmt

    def misc(self, a, w):
        if (w & 0x0FF000F0) == 0x01600080:  # SMUL<x><y>
            xbit, ybit = (w >> 5) & 1, (w >> 6) & 1
            rd, rs, rm = (w >> 16) & 15, (w >> 8) & 15, w & 15
            aexpr = "(int16_t)(%s>>16)" % REG[rm] if xbit else "(int16_t)%s" % REG[rm]
            bexpr = "(int16_t)(%s>>16)" % REG[rs] if ybit else "(int16_t)%s" % REG[rs]
            return self.set_reg(rd, "(uint32_t)((int32_t)(%s)*(int32_t)(%s))" % (aexpr, bexpr), a)
        if (w & 0x0FF00090) == 0x01000080:  # SMLA<x><y>
            xbit, ybit = (w >> 5) & 1, (w >> 6) & 1
            rd, rn, rs, rm = (w >> 16) & 15, (w >> 12) & 15, (w >> 8) & 15, w & 15
            aexpr = "(int16_t)(%s>>16)" % REG[rm] if xbit else "(int16_t)%s" % REG[rm]
            bexpr = "(int16_t)(%s>>16)" % REG[rs] if ybit else "(int16_t)%s" % REG[rs]
            return self.set_reg(rd, "(uint32_t)((int32_t)(%s)*(int32_t)(%s)+(int32_t)%s)" % (aexpr, bexpr, REG[rn]), a)
        if (w & 0x0FFF0FF0) == 0x016F0F10:  # CLZ
            rd, rm = (w >> 12) & 15, w & 15
            src = "0x%xu" % (a + 8) if rm == 15 else REG[rm]
            return self.set_reg(rd, "%s?__builtin_clz(%s):32u" % (src, src), a)
        if (w & 0x0FBF0FFF) == 0x010F0000:  # MRS
            rd = (w >> 12) & 15
            return self.set_reg(rd, "(n_<<31)|(z_<<30)|(c_<<29)|(v_<<28)", a)
        if (w & 0x0DB0F000) == 0x0120F000:  # MSR
            return ";"  # flags only; ignore
        self.stats["unknown_op"] += 1
        self.stats["unk_%08x" % w] += 1
        return "rt_undef(0x%xu);/*misc %08x*/" % (a, w)

    def media(self, a, w):
        """ARMv6 packing / extend / byte-reverse (bit 4 set in the 0b011 space)."""
        rd, rm = (w >> 12) & 15, w & 15
        src = "0x%xu" % (a + 8) if rm == 15 else REG[rm]
        rot = ((w >> 10) & 3) * 8
        if rot:
            src = "ror_reg(%s,%du)" % (src, rot)
        # UXTB / UXTH / SXTB / SXTH and *AB / *AH (Rn==15 means no add)
        if (w & 0x0F8000F0) == 0x06800070:
            op = (w >> 20) & 7
            rn = (w >> 16) & 15
            if op == 2:  # SXTB / SXTAB
                expr = "(uint32_t)(int32_t)(int8_t)%s" % src
            elif op == 3:  # SXTH / SXTAH
                expr = "(uint32_t)(int32_t)(int16_t)%s" % src
            elif op == 6:  # UXTB / UXTAB
                expr = "(%s&255u)" % src
            elif op == 7:  # UXTH / UXTAH
                expr = "(%s&65535u)" % src
            else:
                self.stats["unknown_op"] += 1
                self.stats["unk_%08x" % w] += 1
                return "rt_undef(0x%xu);/*%08x*/" % (a, w)
            if rn != 15:
                expr = "(%s+%s)" % (REG[rn], expr)
            return self.set_reg(rd, expr, a)
        # REV / REV16 / REVSH
        if (w & 0x0FFF0FF0) == 0x06BF0F30:  # REV
            return self.set_reg(rd, "((%s>>24)|((%s>>8)&0xff00u)|((%s&0xff00u)<<8)|(%s<<24))" % (src, src, src, src), a)
        if (w & 0x0FFF0FF0) == 0x06BF0FB0:  # REV16
            return self.set_reg(rd, "(((%s&0xff00ff00u)>>8)|((%s&0x00ff00ffu)<<8))" % (src, src), a)
        if (w & 0x0FFF0FF0) == 0x06FF0FB0:  # REVSH
            return self.set_reg(rd, "(uint32_t)(int32_t)(int16_t)(((%s&0xff00u)>>8)|((%s&0xffu)<<8))" % (src, src), a)
        self.stats["unknown_op"] += 1
        self.stats["unk_%08x" % w] += 1
        return "rt_undef(0x%xu);/*%08x*/" % (a, w)

    def fold_pc(self, va, n=4):
        """Integer bits at a PC-relative pool address, or None if unmapped."""
        try:
            if n == 4:
                return self.img.m.u32(va)
            if n == 2:
                return self.img.m.u16(va)
            if n == 1:
                return self.img.m.read(va, 1)[0]
        except Exception:
            return None
        return None

    def ldr_addr(self, a, w):
        p, u, i = (w >> 24) & 1, (w >> 23) & 1, (w >> 25) & 1
        rn = (w >> 16) & 15
        base = "0x%xu" % (a + 8) if rn == 15 else REG[rn]
        if i:
            rm = w & 15
            return "%s+%s" % (base, REG[rm]) if u else "%s-(int)%s" % (base, REG[rm])
        off = w & 0xFFF
        if not p:
            return base
        return "%s+%s%du" % (base, "" if u else "-", off)

    def ls(self, a, w):
        p, u, b, ww, l = [(w >> k) & 1 for k in (24, 23, 22, 21, 20)]
        rn, rd = (w >> 16) & 15, (w >> 12) & 15
        if w & (1 << 25):
            val, _ = self.shifter(w & ~(1 << 25) | (0 << 25), a)  # force reg form: clear I, but I is bit 25 which is 1 for reg
            # For LDR/STR, bit 25=1 means register offset: bits 11-0 are shifter of Rm
            rm = w & 15
            src = "0x%xu" % (a + 8) if rm == 15 else REG[rm]
            amt = (w >> 7) & 31
            styp = (w >> 5) & 3
            if styp == 0:
                off_e = "(%s<<%du)" % (src, amt) if amt else src
            elif styp == 1:
                off_e = "(%s>>%du)" % (src, amt) if amt else "0u"
            elif styp == 2:
                off_e = "(uint32_t)((int32_t)%s>>%du)" % (src, amt if amt else 31)
            else:
                off_e = "((%s>>%du)|(%s<<%du))" % (src, amt, src, 32 - amt) if amt else src
        else:
            off_e = "%du" % (w & 0xFFF)
        if not u:
            if w & (1 << 25):
                off_e = "-(int)%s" % off_e
            else:
                off_e = "-%du" % (w & 0xFFF)
        base = "0x%xu" % (a + 8) if rn == 15 else REG[rn]
        addr = "%s+%s" % (base, off_e) if p else base
        wb = ""
        if (ww or not p) and rn != 15:
            wb = self.set_reg(rn, "%s+%s" % (base, off_e), a)
        # ARM literal pools sit in __text. Fold PC-relative loads so guest.img
        # need not map the original ARM instructions (only __cstring and DATA).
        if l and rn == 15 and p and not (w & (1 << 25)):
            loc = (a + 8) + ((w & 0xFFF) if u else -(w & 0xFFF))
            bits = self.fold_pc(loc, 1 if b else 4)
            if bits is not None:
                self.stats["pc_fold"] += 1
                return self.set_reg(rd, "0x%xu" % bits, a)
        if l:
            if b:
                load = "L8(%s)" % addr
            else:
                load = "L32(%s)" % addr
            return self.set_reg(rd, load, a) + wb
        if b:
            return "S8(%s,%s);%s" % (addr, REG[rd], wb)
        src = "0x%xu" % (a + 8) if rd == 15 else REG[rd]
        return "S32(%s,%s);%s" % (addr, src, wb)

    def block(self, a, w):
        p, u, s_bit, ww, l = [(w >> k) & 1 for k in (24, 23, 22, 21, 20)]
        rn = (w >> 16) & 15
        regs = [i for i in range(16) if w & (1 << i)]
        n = len(regs)
        if not n:
            return ";"
        base = REG[rn]
        # IA: addr = base, wrap +4 each; IB: addr = base+4; DA: addr = base - 4*n; DB: addr = base - 4*n then +4 first
        if u and not p:
            start_e, step = base, 4  # IA
        elif u and p:
            start_e, step = "(%s+4u)" % base, 4  # IB
        elif not u and not p:
            start_e, step = "(%s-%du)" % (base, 4 * n), 4  # DA treated as start then increment
            start_e = "(%s-%du+4u)" % (base, 4 * n)
        else:
            start_e, step = "(%s-%du)" % (base, 4 * n), 4  # DB
        lines = ["{uint32_t a_=%s;" % start_e]
        for i, r in enumerate(regs):
            addr = "a_+%du" % (step * i)
            if l:
                if r == 15:
                    lines.append("uint32_t npc_=L32(%s);" % addr)
                elif r == 13:
                    lines.append("sp=L32(%s);" % addr)
                else:
                    lines.append("%s=L32(%s);" % (REG[r], addr))
            else:
                src = "0x%xu" % (a + 8) if r == 15 else ("sp" if r == 13 else REG[r])
                lines.append("S32(%s,%s);" % (addr, src))
        if ww and rn != 15:
            delta = 4 * n if u else -4 * n
            if delta >= 0:
                lines.append("%s+=%du;" % (REG[rn], delta))
            else:
                lines.append("%s-=%du;" % (REG[rn], -delta))
        if l and 15 in regs:
            lines.append("SP=sp;if(npc_==RET_MAGIC||npc_==lr)RETURN();return rt_icall(npc_,r0,r1,r2,r3);")
        lines.append("}")
        return "".join(lines)

    def vfp_ls(self, a, w):
        p, u, d, ww, l = [(w >> k) & 1 for k in (24, 23, 22, 21, 20)]
        rn = (w >> 16) & 15
        vd = (w >> 12) & 15
        cp = (w >> 8) & 15
        off = (w & 0xFF) * 4
        if cp not in (10, 11):
            self.stats["unknown_op"] += 1
            return ";"  # ignore unknown coprocessor
        # VMOV Rt,Rt2,Dm / VMOV Dm,Rt,Rt2 (softfp double <-> rN,rN+1).
        # cond 1100010 L Rt2 Rt 1011 ... (bits 27-21 = 1100010). GNU: 0xec532b16
        # is vmov r2, r3, d6. P=U=W=0, bit22=1 is the opcode, not VLDM.
        if ((w >> 21) & 0x7F) == 0x62 and cp == 11 and (w & 0x10):
            rt, rt2 = vd, rn
            dm = (w & 15) | (((w >> 5) & 1) << 4)
            self.stats["vmov_rr_d"] += 1
            if l:
                return "%s=VS[%d];%s=VS[%d];" % (REG[rt], dm * 2, REG[rt2], dm * 2 + 1)
            return "VS[%d]=%s;VS[%d]=%s;" % (dm * 2, REG[rt], dm * 2 + 1, REG[rt2])
        base = "0x%xu" % (a + 8) if rn == 15 else REG[rn]
        dbl = cp == 11
        if p and not ww:
            # VLDR / VSTR
            addr = "%s+%s%du" % (base, "" if u else "-", off)
            if l and rn == 15:
                loc = (a + 8) + (off if u else -off)
                if dbl:
                    lo, hi = self.fold_pc(loc, 4), self.fold_pc(loc + 4, 4)
                    if lo is not None and hi is not None:
                        idx = vd | (d << 4)
                        self.stats["pc_fold"] += 1
                        return "VS[%d]=0x%xu;VS[%d]=0x%xu;" % (idx * 2, lo, idx * 2 + 1, hi)
                else:
                    bits = self.fold_pc(loc, 4)
                    if bits is not None:
                        idx = (vd << 1) | d
                        self.stats["pc_fold"] += 1
                        return "VS[%d]=0x%xu;" % (idx, bits)
            if dbl:
                idx = vd | (d << 4)
                if idx * 2 + 1 >= 32:
                    self.stats["vfp_d16"] = self.stats.get("vfp_d16", 0) + 1
                    return "rt_log(\"VFP d16-d31 VLDR/VSTR at 0x%x\");" % a
                if l:
                    return "rt_check_align8(%s);VS[%d]=L32(%s);VS[%d]=L32((%s)+4);" % (addr, idx * 2, addr, idx * 2 + 1, addr)
                return "rt_check_align8(%s);S32(%s,VS[%d]);S32((%s)+4,VS[%d]);" % (addr, addr, idx * 2, addr, idx * 2 + 1)
            idx = (vd << 1) | d
            if l:
                return "VS[%d]=L32(%s);" % (idx, addr)
            return "S32(%s,VS[%d]);" % (addr, idx)
        # VLDM / VSTM
        n = (w & 0xFF)
        if not u:
            start_e = "(%s-%du)" % (base, off)
        else:
            start_e = base if not p else "(%s+0)" % base
        if p and not u:
            start_e = "(%s-%du)" % (base, off)  # DB
        # Unrolled VSTM/VLDM of d8-d15 was ~16 stores per function and blew the
        # Thumb image past the 4.0 MB largest-block budget. A short loop is the
        # same guest stores, much less C.
        vs0 = ((vd | (d << 4)) * 2) if dbl else ((vd << 1) | d)
        if vs0 + max(n, 1) > 32:
            self.stats["vfp_d16"] = self.stats.get("vfp_d16", 0) + 1
            return "rt_log(\"VFP d16-d31 VLDM/VSTM at 0x%x\");" % a
        lines = ["{uint32_t a_=%s;" % start_e]
        if dbl:
            lines.append("rt_check_align8(a_);")
        if n > 0:
            if l:
                lines.append("for(int i_=0;i_<%du;i_++)VS[%d+i_]=L32(a_+4u*(uint32_t)i_);" % (n, vs0))
            else:
                lines.append("for(int i_=0;i_<%du;i_++)S32(a_+4u*(uint32_t)i_,VS[%d+i_]);" % (n, vs0))
        if ww and rn != 15:
            if u:
                lines.append("%s+=%du;" % (REG[rn], off))
            else:
                lines.append("%s-=%du;" % (REG[rn], off))
        lines.append("}")
        return "".join(lines)

    def vfp_cdp(self, a, w):
        # MRC/MCR bit 4 = 1
        if w & 0x10:
            return self.vfp_xfr(a, w)
        cp = (w >> 8) & 15
        if cp not in (10, 11):
            self.stats["unknown_op"] += 1
            return ";"
        dbl = cp == 11
        # bit 22 is D (register msb), not opcode: opcode is bit23 + bits21-20 + bit6
        op_hi, op_mid = (w >> 23) & 1, (w >> 20) & 3
        vn, vd, vm = (w >> 16) & 15, (w >> 12) & 15, w & 15
        nbit, dbit, mbit = (w >> 7) & 1, (w >> 22) & 1, (w >> 5) & 1
        op2 = (w >> 6) & 1
        if dbl:
            dd, dn, dm = vd | (dbit << 4), vn | (nbit << 4), vm | (mbit << 4)
            def D(i):
                return "DGET(%d)" % i
            def Dset(i, e):
                return "DSET(%d,%s);" % (i, e)
        else:
            dd, dn, dm = (vd << 1) | dbit, (vn << 1) | nbit, (vm << 1) | mbit
            def S(i):
                return "S(%d)" % i
            def Sset(i, e):
                return "S(%d)=%s;" % (i, e)

        # two-operand extension: bit23=1, bits21-20=11 (D is bit 22)
        if op_hi == 1 and op_mid == 3:
            opc = (vn << 1) | nbit  # Vn+N as opcode
            if dbl:
                # opc is Vn:N. FCPY=0 FABS=1 FNEG=2 FSQRT=3 (bit6 must be 1)
                if opc == 0 and (w & 0x40):
                    return Dset(dd, D(dm))  # FCPYD
                if opc == 1 and (w & 0x40):
                    return Dset(dd, "fabs(%s)" % D(dm))
                if opc == 2 and (w & 0x40):
                    return Dset(dd, "-(%s)" % D(dm))
                if opc == 3 and (w & 0x40):
                    return Dset(dd, "sqrt(%s)" % D(dm))
                if opc in (8, 9, 10, 11):  # FCMP: FPSCR only. APSR stays until FMSTAT.
                    # ARM VCMP does not write APSR; Box2D ComputeAABB is
                    # VCMP / VMRS / VCMP / VMOVPL, so assigning n_ here made
                    # the second compare clobber the first and collapsed AABBs
                    # to the last vertex (ground became a point, pigs fell through).
                    return ("{double a_=%s,b_=%s;uint32_t nn_=a_<b_,zz_=a_==b_,cc_=!(a_<b_),vv_=a_!=a_||b_!=b_;"
                            "FPSCR=(FPSCR&0x0fffffffu)|(nn_<<31)|(zz_<<30)|(cc_<<29)|(vv_<<28);}" % (
                        D(dd), "0.0" if opc in (10, 11) else D(dm)))
                if opc == 15 and (w & 0x40):  # FCVTSD (double encoding, dest is S)
                    sd = (vd << 1) | dbit
                    return "S(%d)=(float)%s;" % (sd, D(dm))
                if opc in (16, 17):  # FUITOD / FSITOD
                    sm = (vm << 1) | mbit
                    cast = "(double)VS[%d]" % sm if opc == 16 else "(double)(int32_t)VS[%d]" % sm
                    return Dset(dd, cast)
                if opc in (24, 25, 26, 27):  # FTOUID / FTOSID / FTOUIZD / FTOSIZD
                    sd = (vd << 1) | dbit
                    fn = "F2UI" if opc in (24, 26) else "(uint32_t)F2I"
                    return "VS[%d]=%s(%s);" % (sd, fn, D(dm))
            else:
                if opc == 0 and (w & 0x40):
                    return Sset(dd, S(dm))
                if opc == 1 and (w & 0x40):
                    return Sset(dd, "fabsf(%s)" % S(dm))
                if opc == 2 and (w & 0x40):
                    return Sset(dd, "-(%s)" % S(dm))
                if opc == 3 and (w & 0x40):
                    return Sset(dd, "sqrtf(%s)" % S(dm))
                if opc in (8, 9, 10, 11):
                    return ("{float a_=%s,b_=%s;uint32_t nn_=a_<b_,zz_=a_==b_,cc_=!(a_<b_),vv_=a_!=a_||b_!=b_;"
                            "FPSCR=(FPSCR&0x0fffffffu)|(nn_<<31)|(zz_<<30)|(cc_<<29)|(vv_<<28);}" % (
                        S(dd), "0.f" if opc in (10, 11) else S(dm)))
                if opc == 15 and (w & 0x40):  # FCVTDS (single encoding, dest is D)
                    ddbl = vd | (dbit << 4)
                    sm = (vm << 1) | mbit
                    return "DSET(%d,(double)S(%d));" % (ddbl, sm)
                if opc in (16, 17):
                    cast = "(float)VS[%d]" % dm if opc == 16 else "(float)(int32_t)VS[%d]" % dm
                    return Sset(dd, cast)
                if opc in (24, 25, 26, 27):
                    fn = "F2UI" if opc in (24, 26) else "(uint32_t)F2I"
                    return "VS[%d]=%s(%s);" % (dd, fn, S(dm))
            self.stats["vfp_ext"] += 1
            self.stats["vfp_ext_opc_%d" % opc] += 1
            self.stats["vfp_ext_%08x" % (w & 0x0fffffff)] += 1
            return ";"
        # three-operand: bit23=0 (or FDIV bit23=1, bits21-20=00)
        # VFPv2 three-operand, ARM ARM DDI 0406 / GNU UAL:
        # VMLA  (op=00 bit6=0) Fd=Fd+Fn*Fm
        # VMLS  (op=00 bit6=1) Fd=Fd-Fn*Fm
        # VNMLS (op=01 bit6=0) Fd=Fn*Fm-Fd
        # VNMLA (op=01 bit6=1) Fd=-(Fd+Fn*Fm)
        # An earlier decode swapped VMLS and VNMLS. Image::draw then did
        # s6 = m00*tx - x (= -x with identity RS2D), so clip corners sheared.
        if dbl:
            if dd >= 16 or dn >= 16 or dm >= 16:
                self.stats["vfp_d16"] = self.stats.get("vfp_d16", 0) + 1
                return "rt_log(\"VFP d16-d31 CDP at 0x%x\");" % a
            if op_hi == 0 and op_mid == 0 and not op2:
                return Dset(dd, "%s*%s+%s" % (D(dn), D(dm), D(dd)))
            if op_hi == 0 and op_mid == 0 and op2:
                return Dset(dd, "%s-(%s*%s)" % (D(dd), D(dn), D(dm)))
            if op_hi == 0 and op_mid == 1 and not op2:
                return Dset(dd, "%s*%s-(%s)" % (D(dn), D(dm), D(dd)))
            if op_hi == 0 and op_mid == 1 and op2:
                return Dset(dd, "-(%s*%s+%s)" % (D(dn), D(dm), D(dd)))
            if op_hi == 0 and op_mid == 2 and not op2:
                return Dset(dd, "%s*%s" % (D(dn), D(dm)))
            if op_hi == 0 and op_mid == 2 and op2:
                return Dset(dd, "-(%s*%s)" % (D(dn), D(dm)))
            if op_hi == 0 and op_mid == 3 and not op2:
                return Dset(dd, "%s+%s" % (D(dn), D(dm)))
            if op_hi == 0 and op_mid == 3 and op2:
                return Dset(dd, "%s-%s" % (D(dn), D(dm)))
            if op_hi == 1 and op_mid == 0 and not op2:
                return Dset(dd, "%s/%s" % (D(dn), D(dm)))
            self.stats["vfp_ext"] += 1
            self.stats["vfp_ext_d_%08x" % (w & 0x0fffffff)] += 1
            return ";"
        if op_hi == 0 and op_mid == 0 and not op2:
            return Sset(dd, "%s*%s+%s" % (S(dn), S(dm), S(dd)))
        if op_hi == 0 and op_mid == 0 and op2:
            return Sset(dd, "%s-(%s*%s)" % (S(dd), S(dn), S(dm)))
        if op_hi == 0 and op_mid == 1 and not op2:
            return Sset(dd, "%s*%s-(%s)" % (S(dn), S(dm), S(dd)))
        if op_hi == 0 and op_mid == 1 and op2:
            return Sset(dd, "-(%s*%s+%s)" % (S(dn), S(dm), S(dd)))
        if op_hi == 0 and op_mid == 2 and not op2:
            return Sset(dd, "%s*%s" % (S(dn), S(dm)))
        if op_hi == 0 and op_mid == 2 and op2:
            return Sset(dd, "-(%s*%s)" % (S(dn), S(dm)))
        if op_hi == 0 and op_mid == 3 and not op2:
            return Sset(dd, "%s+%s" % (S(dn), S(dm)))
        if op_hi == 0 and op_mid == 3 and op2:
            return Sset(dd, "%s-%s" % (S(dn), S(dm)))
        if op_hi == 1 and op_mid == 0 and not op2:
            return Sset(dd, "%s/%s" % (S(dn), S(dm)))
        self.stats["vfp_ext"] += 1
        self.stats["vfp_ext_s_%08x" % (w & 0x0fffffff)] += 1
        return ";"

    def vfp_xfr(self, a, w):
        cp = (w >> 8) & 15
        l = (w >> 20) & 1  # MRC = 1 (to ARM)
        rd = (w >> 12) & 15
        vn, dbit = (w >> 16) & 15, (w >> 7) & 1
        op = (w >> 21) & 7
        if cp == 10 and op == 0:
            # FMSR / FMRS  S<->Rd
            idx = (vn << 1) | dbit
            if l:
                return self.set_reg(rd, "VS[%d]" % idx, a) + ("NZ(%s);" % REG[rd] if rd != 15 else "")
            src = "0x%xu" % (a + 8) if rd == 15 else REG[rd]
            return "VS[%d]=%s;" % (idx, src)
        if cp == 11 and op == 0:
            # FMDRR / FMRRD: Dm in CRm+M (bits 3-0 and bit 5), ARM pair Rd,Rn
            idx = (w & 15) | (((w >> 5) & 1) << 4)
            rn = (w >> 16) & 15
            if idx >= 16:
                self.stats["vfp_d16"] = self.stats.get("vfp_d16", 0) + 1
                return "rt_log(\"VFP d16-d31 FMDRR/FMRRD at 0x%x\");" % a
            if l:
                return "%s=VS[%d];%s=VS[%d];" % (REG[rd], idx * 2, REG[rn], idx * 2 + 1)
            return "VS[%d]=%s;VS[%d]=%s;" % (idx * 2, REG[rd], idx * 2 + 1, REG[rn])
        if (w & 0x0FFFFFFF) == 0x0EF1FA10:  # FMSTAT (Rd = PC)
            return "n_=(FPSCR>>31)&1;z_=(FPSCR>>30)&1;c_=(FPSCR>>29)&1;v_=(FPSCR>>28)&1;"
        # FMRX / FMXR FPSCR
        if (w & 0x0FBF0FFF) == 0x0EF10A10:
            return self.set_reg(rd, "FPSCR", a)
        if (w & 0x0FBF0FFF) == 0x0EE10A10:
            src = REG[rd]
            return "FPSCR=%s;n_=(FPSCR>>31)&1;z_=(FPSCR>>30)&1;c_=(FPSCR>>29)&1;v_=(FPSCR>>28)&1;" % src
        self.stats["vfp_xfr"] += 1
        self.stats["vfp_xfr_%08x" % (w & 0x0fffffff)] += 1
        return ";"


HEADER = r'''/* generated by nano7kit games/angrybirds/recomp.py from the user's own AngryBirds binary: do not commit */
#include "rt.h"
#include <setjmp.h>
#include <math.h>
'''


def main():
    src, outdir = sys.argv[1], sys.argv[2]
    img = Image(src)
    if img.m.cryptid:
        sys.exit("encrypted binary (cryptid %d): decrypt it first" % img.m.cryptid)
    funcs = reachable(img)
    em = Emitter(img, funcs)
    os.makedirs(outdir, exist_ok=True)
    order = sorted(funcs)
    seen_set = set(funcs)
    chunks = 16
    per = (len(order) + chunks - 1) // chunks if order else 1
    protos = ["uint64_t %s(uint32_t,uint32_t,uint32_t,uint32_t);" % em.fn(a) for a in order]
    with open(os.path.join(outdir, "recomp_funcs.h"), "w") as fh:
        fh.write("/* generated: do not commit */\n#include <stdint.h>\n" + "\n".join(protos) + "\n")
    for c in range(chunks):
        part = order[c * per:(c + 1) * per]
        if not part:
            continue
        out = []
        for a in part:
            em.emit_function(a, out)
        text = HEADER + '#include "recomp_funcs.h"\n#include "recomp_imports.h"\n\n' + "\n\n".join(out) + "\n"
        # Image::draw clip AABB rejects L2 ice/pigs when the 2x2 is not identity.
        # Keep the draw path (L_b1870); RECIPE rebuild must not restore the clip.
        if 0xb15c4 in part:
            text = text.replace("if(n_){goto L_b1860;}", "if(0&&n_){goto L_b1860;}")
            text = text.replace("if(n_==v_){goto L_b1860;}", "if(0&&n_==v_){goto L_b1860;}")
            text = text.replace("if(n_!=v_){goto L_b1870;}", "if(1||n_!=v_){goto L_b1870;}")
        with open(os.path.join(outdir, "recomp_%02d.c" % c), "w") as fh:
            fh.write(text)
    imps = sorted({n for n in em.used_imports if n} | {n for (k, n) in img.m.imports.values() if n})
    with open(os.path.join(outdir, "recomp_imports.h"), "w") as fh:
        fh.write("/* generated */\n#include <stdint.h>\n")
        for n in imps:
            fh.write("uint64_t I_%s(uint32_t,uint32_t,uint32_t,uint32_t);\n" % cname(n))
    slots = []
    names = sorted({n for (k, n) in img.m.imports.values() if n})
    idx = {n: i for i, n in enumerate(names)}
    for addr, (k, n) in sorted(img.m.imports.items()):
        if k != "__symbol_stub4" and n:
            slots.append((addr, idx[n]))
    with open(os.path.join(outdir, "recomp_table.c"), "w") as fh:
        fh.write(HEADER + '#include "recomp_funcs.h"\n#include "recomp_imports.h"\n')
        fh.write("const rt_fn_entry rt_fn_table[] = {\n")
        for a in order:
            fh.write(" {0x%xu,%s},\n" % (a, em.fn(a)))
        fh.write("};\nconst uint32_t rt_fn_count = %d;\n" % len(order))
        fh.write("const char *const rt_import_names[] = {\n")
        for n in names:
            fh.write(' "%s",\n' % n)
        fh.write("};\nconst uint32_t rt_import_count = %d;\n" % len(names))
        fh.write("const rt_fn rt_import_fns[] = {\n")
        for n in names:
            fh.write(" I_%s,\n" % cname(n))
        fh.write("};\n")
        fh.write("const uint32_t rt_import_slots[][2] = {\n")
        for addr, i in slots:
            fh.write(" {0x%xu,%du},\n" % (addr, i))
        fh.write("};\nconst uint32_t rt_import_slot_count = %d;\n" % len(slots))
    with open(os.path.join(outdir, "recomp_weak.c"), "w") as fh:
        fh.write(HEADER + '#include "recomp_imports.h"\nvoid rt_missing_import(const char *name);\n')
        for n in imps:
            fh.write('__attribute__((weak)) uint64_t I_%s(uint32_t a,uint32_t b,uint32_t c,uint32_t d)'
                     '{(void)a;(void)b;(void)c;(void)d;rt_missing_import("%s");return 0;}\n' % (cname(n), n))
    # PC-relative LDR/VLDR in __text are folded into C constants, so the guest
    # image does not map original ARM instructions. ADR of strings still needs
    # __cstring/__const, then DATA (vtables, globals, BSS).
    cstr = img.m.sect("__TEXT", "__cstring")
    gbase = (cstr["addr"] if cstr else [x for x in img.m.segments if x[0] == "__TEXT"][0][1]) & ~0xFFF
    dseg = [x for x in img.m.segments if x[0] == "__DATA"][0]
    with open(os.path.join(outdir, "rt_layout.h"), "w") as fh:
        fh.write("/* generated */\n#define GUEST_BASE 0x%xu\n#define GUEST_IMAGE_END 0x%xu\n"
                 "#define GUEST_DATA_END 0x%xu\n#define GUEST_STACK 0x20000u\n"
                 % (gbase, dseg[1] + dseg[4], dseg[1] + dseg[2]))
    with open(os.path.join(outdir, "guest.img"), "wb") as fh:
        fh.write(read_span(img.m, gbase, dseg[1] + dseg[4] - gbase))
    entries = {
        "Init": "__ZN9framework4initEPNS_11OSInterfaceEPN2gr7ContextE",
        "Update": "__ZN7GameApp6updateEfPN2gr7ContextEi",
        "TouchBegan": "__ZN9framework3App10touchBeganERKNS_10TouchEventE",
        "TouchMoved": "__ZN9framework3App10touchMovedERKNS_10TouchEventE",
        "TouchEnded": "__ZN9framework3App10touchEndedERKNS_10TouchEventE",
        "AppGet": "__ZN9framework3App3getEv",
        "SetDataPath": "__ZN9framework3App11setDataPathERKN4lang6StringE",
        "SetRootPath": "__ZN2io18AppDataInputStream11setRootPathERKN4lang6StringE",
        "SetOutRootPath": "__ZN2io19AppDataOutputStream11setRootPathERKN4lang6StringE",
        "EglC1": "__ZN2gr11EGL_ContextC1EiiNS_7Context15OrientationTypeEPFvvE",
        "EglC2": "__ZN2gr11EGL_ContextC2EiiNS_7Context15OrientationTypeEPFvvE",
        "EglCreate": "__ZN2gr17EGL_createContextEiiNS_7Context15OrientationTypeEPFvvE",
        "CreateShaders": "__ZN2gr13createShadersEPNS_11EGL_ContextE",
        "BeginScene": "__ZN2gr11EGL_Context10beginSceneEv",
        "StringC1": "__ZN4lang6StringC1EPKc",
        "StringD1": "__ZN4lang6StringD1Ev",
        "StringCStr": "__ZNK4lang6String5c_strEv",
        "OsiDataPath": "__ZN17IphoneOSInterface18getDefaultDataPathEv",
        "GameAppC2": "__ZN7GameAppC2EPN2gr7ContextEPN9framework11OSInterfaceE",
        "KeyDown": "__ZN7GameApp7keyDownEN9framework3App7KeyTypeE",
        "KeyUp": "__ZN7GameApp5keyUpEN9framework3App7KeyTypeE",
        "MouseMove": "__ZN7GameApp9mouseMoveEii",
        "LoadLevel": "__ZN7GameLua9loadLevelEN4lang6StringE",
        "LuaCall": "__ZN3lua9LuaObject4callEPKc",
        "SetPhysicsEnabled": "__ZN7GameLua17setPhysicsEnabledEb",
        "SetTopLeft": "__ZN7GameLua10setTopLeftEff",
        "SetWorldScale": "__ZN7GameLua13setWorldScaleEf",
        "LuaSetNumber": "__ZN3lua8LuaTable9setNumberEPKcf",
        "LuaSetString": "__ZN3lua8LuaTable9setStringEPKcS2_",
        "LuaSetBool": "__ZN3lua8LuaTable10setBooleanEPKcb",
        "LuaGetTable": "__ZNK3lua8LuaTable8getTableEPKc",
        "LuaGetNumber": "__ZNK3lua8LuaTable9getNumberEPKc",
        "LuaGetString": "__ZNK3lua8LuaTable9getStringEPKc",
        "LuaTableD1": "__ZN3lua8LuaTableD1Ev",
        "SetTheme": "__ZN7GameLua8setThemeEN4lang6StringE",
    }
    with open(os.path.join(outdir, "rt_entry.h"), "w") as fh:
        fh.write("/* generated */\n")
        fh.write("#define AB_VT_IPHONEOS 0x%xu\n" % 0x14004c)
        for k, n in entries.items():
            addr = img.byname.get(n)
            val = addr if addr is not None and addr in seen_set else 0
            fh.write("#define AB_%s 0x%xu\n" % (k, val))
        mi = img.m.sect("__DATA", "__mod_init_func")
        inits = [img.m.u32(mi["addr"] + 4 * k) & ~1 for k in range(mi["size"] // 4)] if mi else []
        inits = [a for a in inits if a in seen_set]
        fh.write("#define AB_INIT_COUNT %d\nstatic const uint32_t ab_inits[] = {%s0};\n"
                 % (len(inits), "".join("0x%xu," % a for a in inits)))
        fh.write("#define AB_APP_GET_SLOT 0x13f064u\n")
    with open(os.path.join(outdir, "layout.txt"), "w") as fh:
        for seg, va, vs, fo, fs in img.m.segments:
            fh.write("%s %x %x %x %x\n" % (seg, va, vs, fo, fs))
    with open(os.path.join(outdir, "stats.txt"), "w") as fh:
        for k, v in sorted(em.stats.items()):
            fh.write("%s %s\n" % (k, v))
        fh.write("imports %d\n" % len(imps))
        fh.write("functions_reachable %d\n" % len(order))
        for t, n in sorted(em.missing_calls.items()):
            fh.write("missing %x %s %d\n" % (t, img.fname.get(t, "?"), n))
    print("recomp: %d functions, %d instructions, %d imports, %d missing call targets, %d unknown ops"
          % (em.stats["functions"], em.stats["instructions"], len(imps),
             len(em.missing_calls), em.stats["unknown_op"]))


if __name__ == "__main__":
    main()
