#!/usr/bin/env python3
"""Fruit Ninja 1.0 (armv6 Thumb) to C: nano7kit's own static recompiler.

Reads the user's decrypted `Fruit` executable and writes one C function per
original Thumb function that the game engine can reach (the Objective-C shell,
OpenFeint, Facebook and OAuth code is left out). Registers become C locals,
memory is one block of guest memory laid out like the original process, and
calls to iOS libraries go to the runtime (rt.c). Nothing from the game is
stored in nano7kit: this writes into the caller's build directory.

usage: recomp.py Fruit OUTDIR [--roots-only]
"""
import os
import struct
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from macho import MachO, read_span  # noqa: E402

ROOT_NAMES = (
    "__ZN15IPhoneInterface10SetRootDirEPcS0_",
    "__ZN15IPhoneInterface13InitializeAppEv",
    "__ZN15IPhoneInterface9UpdateAppEff",
    "__ZN15IPhoneInterface7DrawAppEiijjff",
    "__ZN15IPhoneInterface22UpdateTouchScreenInputEjbfff",
    "__ZN15IPhoneInterface16InitSoundSessionEv",
    "__ZN15IPhoneInterface19SetDeviceDimentionsEii",
)
SKIP_OWNERS = ("OF", "Feint", "FB", "Facebook", "MPOAuth", "MPURL", "SBJSON", "SBJson")
# functions replaced by "return 0" (the nano port is silent: no AudioQueue music)
STUBS = {"__Z16ChangeMusicTrackP11IphoneMusic"}
SWITCH = {"___switchu8": "u8", "___switch8": "s8", "___switch16": "s16", "___switch32": "s32"}
RET_MAGIC = 0xFFFF0001
IMPORT_BASE = 0xFE000000

# runtime float/int helpers inlined as C (Apple's *vfp helpers return 1 for true)
INLINE = {
    "___addsf3vfp": "r0=F2U(U2F(r0)+U2F(r1));",
    "___subsf3vfp": "r0=F2U(U2F(r0)-U2F(r1));",
    "___mulsf3vfp": "r0=F2U(U2F(r0)*U2F(r1));",
    "___divsf3vfp": "r0=F2U(U2F(r0)/U2F(r1));",
    "___eqsf2vfp": "r0=U2F(r0)==U2F(r1);",
    "___nesf2vfp": "r0=U2F(r0)!=U2F(r1);",
    "___ltsf2vfp": "r0=U2F(r0)<U2F(r1);",
    "___lesf2vfp": "r0=U2F(r0)<=U2F(r1);",
    "___gtsf2vfp": "r0=U2F(r0)>U2F(r1);",
    "___gesf2vfp": "r0=U2F(r0)>=U2F(r1);",
    "___unordsf2vfp": "r0=(U2F(r0)!=U2F(r0))||(U2F(r1)!=U2F(r1));",
    "___fixsfsivfp": "r0=(uint32_t)F2I(U2F(r0));",
    "___fixunssfsivfp": "r0=F2UI(U2F(r0));",
    "___floatsisfvfp": "r0=F2U((float)(int32_t)r0);",
    "___floatunssisfvfp": "r0=F2U((float)r0);",
    "___extendsfdf2vfp": "{double d_=(double)U2F(r0);D2R(d_);}",
    "___truncdfsf2vfp": "r0=F2U((float)R2D(r0,r1));",
    "___adddf3vfp": "{double d_=R2D(r0,r1)+R2D(r2,r3);D2R(d_);}",
    "___subdf3vfp": "{double d_=R2D(r0,r1)-R2D(r2,r3);D2R(d_);}",
    "___muldf3vfp": "{double d_=R2D(r0,r1)*R2D(r2,r3);D2R(d_);}",
    "___divdf3vfp": "{double d_=R2D(r0,r1)/R2D(r2,r3);D2R(d_);}",
    "___eqdf2vfp": "r0=R2D(r0,r1)==R2D(r2,r3);",
    "___nedf2vfp": "r0=R2D(r0,r1)!=R2D(r2,r3);",
    "___ltdf2vfp": "r0=R2D(r0,r1)<R2D(r2,r3);",
    "___ledf2vfp": "r0=R2D(r0,r1)<=R2D(r2,r3);",
    "___gtdf2vfp": "r0=R2D(r0,r1)>R2D(r2,r3);",
    "___gedf2vfp": "r0=R2D(r0,r1)>=R2D(r2,r3);",
    "___unorddf2vfp": "{double a_=R2D(r0,r1),b_=R2D(r2,r3);r0=(a_!=a_)||(b_!=b_);}",
    "___fixdfsivfp": "r0=(uint32_t)F2I(R2D(r0,r1));",
    "___fixunsdfsivfp": "r0=F2UI(R2D(r0,r1));",
    "___floatsidfvfp": "{double d_=(double)(int32_t)r0;D2R(d_);}",
    "___floatunssidfvfp": "{double d_=(double)r0;D2R(d_);}",
    "___divsi3": "r0=SDIV(r0,r1);",
    "___modsi3": "r0=SMOD(r0,r1);",
    "___udivsi3": "r0=r1?r0/r1:0;",
    "___umodsi3": "r0=r1?r0%r1:r0;",
    "___muldi3": "{uint64_t q_=(((uint64_t)r1<<32)|r0)*(((uint64_t)r3<<32)|r2);r0=(uint32_t)q_;r1=(uint32_t)(q_>>32);}",
    "___udivdi3": "{uint64_t a_=((uint64_t)r1<<32)|r0,b_=((uint64_t)r3<<32)|r2,q_=b_?a_/b_:0;r0=(uint32_t)q_;r1=(uint32_t)(q_>>32);}",
    "___cmpdi2": "{int64_t a_=(int64_t)(((uint64_t)r1<<32)|r0),b_=(int64_t)(((uint64_t)r3<<32)|r2);r0=a_<b_?0:a_==b_?1:2;}",
    "___floatdidf": "{double d_=(double)(int64_t)(((uint64_t)r1<<32)|r0);D2R(d_);}",
    "___fixunsdfdi": "{double a_=R2D(r0,r1);uint64_t q_=a_<=0||a_!=a_?0:a_>=18446744073709551615.0?~0ull:(uint64_t)a_;r0=(uint32_t)q_;r1=(uint32_t)(q_>>32);}",
}

COND = ["z_", "!z_", "c_", "!c_", "n_", "!n_", "v_", "!v_", "(c_&&!z_)", "(!c_||z_)",
        "(n_==v_)", "(n_!=v_)", "(!z_&&n_==v_)", "(z_||n_!=v_)"]
REG = ["r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8", "r9", "r10", "r11", "r12", "sp", "lr", "pc"]


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
        self.fstart = [a for a, _, _ in funcs]
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
        for pre in ("__GLOBAL__I_", "__GLOBAL__D_"):
            if name.startswith(pre):
                name = name[len(pre):]
        core = name.lstrip("_")
        for p in ("ZN", "ZNK", "ZThn4_N", "ZThn8_N", "ZTv0_n12_N", "ZTV", "ZTI", "ZTS"):
            if core.startswith(p):
                core = core[len(p):]
                break
        digits = ""
        while core and core[0].isdigit():
            digits += core[0]
            core = core[1:]
        cls = core[: int(digits)] if digits else core
        return cls.startswith(SKIP_OWNERS)


# --------------------------------------------------------------- decoding

class Ins:
    __slots__ = ("a", "size", "hw", "hw2", "kind", "targets", "term", "call", "lits")

    def __init__(self, a, hw):
        self.a, self.hw, self.hw2, self.size = a, hw, 0, 2
        self.kind, self.targets, self.term, self.call, self.lits = "", [], False, None, []


def decode(img, a):
    hw = img.m.u16(a)
    ins = Ins(a, hw)
    top5 = hw >> 11
    if top5 == 0b11110:
        hw2 = img.m.u16(a + 2)
        ins.hw2, ins.size = hw2, 4
        off = ((hw & 0x7FF) << 12)
        if off & 0x400000:
            off -= 1 << 23
        h2 = hw2 >> 11
        if h2 == 0b11111:
            ins.kind, ins.call = "bl", a + 4 + off + ((hw2 & 0x7FF) << 1)
        elif h2 == 0b11101:
            ins.kind, ins.call = "blx", (a + 4 + off + ((hw2 & 0x7FF) << 1)) & ~3
        else:
            ins.kind = "undef"
            ins.term = True
        return ins
    if (hw >> 12) == 0b1101:
        cond = (hw >> 8) & 15
        if cond == 15:
            ins.kind = "swi"
        elif cond == 14:
            ins.kind, ins.term = "undef", True
        else:
            imm = hw & 0xFF
            if imm & 0x80:
                imm -= 256
            ins.kind, ins.targets = "bcond", [a + 4 + imm * 2]
        return ins
    if top5 == 0b11100:
        imm = hw & 0x7FF
        if imm & 0x400:
            imm -= 0x800
        ins.kind, ins.targets, ins.term = "b", [a + 4 + imm * 2], True
        return ins
    if (hw >> 10) == 0b010001:
        op = (hw >> 8) & 3
        rm = (hw >> 3) & 15
        rd = (hw & 7) | ((hw >> 4) & 8)
        if op == 3:
            ins.kind = "blxr" if hw & 0x80 else "bx"
            if ins.kind == "bx":
                ins.term = True
        elif op == 2 and rd == 15:
            ins.kind, ins.term = "movpc", True
        elif op == 0 and rd == 15:
            ins.kind, ins.term = "addpc", True
        else:
            ins.kind = "hi"
        return ins
    if (hw >> 9) == 0b1011110:
        ins.kind, ins.term = "pop", bool(hw & 0x100)
        return ins
    ins.kind = "op"
    return ins


def func_body(img, start, end):
    """Recursive descent over one function. Returns {addr: Ins}, set of label addrs, problems."""
    body, labels, todo, problems = {}, set(), [start], []
    switch_tables = {}
    while todo:
        a = todo.pop()
        while a not in body:
            if not (start <= a < end):
                problems.append("falls outside at %x" % a)
                break
            ins = decode(img, a)
            body[a] = ins
            if ins.kind == "bl" and start < ins.call < end and not img.is_stub(ins.call):
                # Thumb-1 far jump inside the function (lr is clobbered, nothing returns)
                ins.kind, ins.targets, ins.term = "farjump", [ins.call], True
                labels.add(ins.call)
                todo.append(ins.call)
                break
            if ins.kind in ("bl", "blx"):
                tgt = ins.call
                name = img.fname.get(tgt & ~1) if not img.is_stub(tgt) else None
                if name in SWITCH:
                    ins.kind = "switch"
                    ins.term = True
                    tab = a + 4
                    kind = SWITCH[name]
                    if kind in ("u8", "s8"):
                        n = img.m.read(tab, 1)[0]
                        ents = []
                        for i in range(n + 1):
                            v = img.m.read(tab + 1 + i, 1)[0]
                            if kind == "s8" and v >= 128:
                                v -= 256
                            ents.append((v, tab + 2 * v))
                    elif kind == "s16":
                        n = img.m.u16(tab)
                        ents = []
                        for i in range(n + 1):
                            v = struct.unpack("<h", img.m.read(tab + 2 + 2 * i, 2))[0]
                            ents.append((v & 0xFFFFFFFF, tab + 2 * v))
                    else:
                        n = img.m.u32(tab)
                        ents = []
                        for i in range(n + 1):
                            v = img.m.u32(tab + 4 + 4 * i)
                            ents.append((v, (tab + v) & 0xFFFFFFFF & ~1))
                    switch_tables[a] = (n, ents)
                    for _, t in ents:
                        labels.add(t)
                        todo.append(t)
                    break
            if ins.kind in ("bcond", "b"):
                t = ins.targets[0]
                if start <= t < end:
                    labels.add(t)
                    todo.append(t)
            if ins.term:
                break
            a += ins.size
    return body, labels, switch_tables, problems


# --------------------------------------------------------------- emission

class Emitter:
    def __init__(self, img, funcs):
        self.img = img
        self.funcs = funcs          # set of addresses we translate
        self.used_imports = set()
        self.missing_calls = defaultdict(int)
        self.stats = defaultdict(int)

    def fn(self, a):
        return "F_%x" % a

    def call_target(self, tgt, lr_after):
        """C statement(s) for a call to tgt (from bl/blx)."""
        img = self.img
        if img.is_stub(tgt):
            name = img.stub_imports.get(tgt)
            if name in INLINE:
                self.stats["inline_" + name] += 1
                return INLINE[name]
            self.used_imports.add(name)
            return "SP=sp;CALLQ(I_%s(r0,r1,r2,r3));" % cname(name)
        t = tgt & ~1
        if t in self.funcs:
            return "SP=sp;CALLQ(%s(r0,r1,r2,r3));" % self.fn(t)
        self.missing_calls[t] += 1
        return "SP=sp;CALLQ(rt_icall(0x%xu,r0,r1,r2,r3));" % (tgt | 1)

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
        order = sorted(body)
        popped = set()   # regs popped in the current straight-line run
        for a in order:
            ins = body[a]
            if a in labels:
                lines.append("L_%x:;" % a)
                popped = set()
            stmt = self.translate(ins, start, end, switches, popped)
            lines.append(" " + stmt)
            if ins.kind == "pop":
                for r in range(8):
                    if ins.hw & (1 << r):
                        popped.add(r)
            elif ins.term:
                popped = set()
        lines.append(" RETURN();")
        lines.append("}")
        out.append("\n".join(lines))
        self.stats["functions"] += 1
        self.stats["instructions"] += len(body)
        return body

    def tail(self, t):
        if (t & ~1) in self.funcs:
            return "SP=sp;return %s(r0,r1,r2,r3);" % self.fn(t & ~1)
        self.missing_calls[t & ~1] += 1
        return "SP=sp;return rt_icall(0x%xu,r0,r1,r2,r3);" % (t | 1)

    def translate(self, ins, start, end, switches, popped):
        a, hw = ins.a, ins.hw
        k = ins.kind
        R = REG
        if k == "bl" or k == "blx":
            return "lr=0x%xu;" % ((a + 4) | 1) + self.call_target(ins.call | (1 if k == "bl" else 0), a + 4) if not self.img.is_stub(ins.call) else self.call_target(ins.call, a + 4)
        if k == "farjump":
            return "lr=0x%xu;goto L_%x;" % ((a + 4) | 1, ins.targets[0])
        if k == "switch":
            n, ents = switches[a]
            s = ["{uint32_t i_=r0<%du?r0:%du;switch(i_){" % (n, n)]
            for i, (v, t) in enumerate(ents):
                lab = "default" if i == n else "case %d" % i
                s.append("%s:r0=0x%xu;goto L_%x;" % (lab, v & 0xFFFFFFFF, t))
            s.append("}}")
            return "".join(s)
        if k == "bcond":
            t = ins.targets[0]
            cond = COND[(hw >> 8) & 15]
            if start <= t < end:
                return "if(%s)goto L_%x;" % (cond, t)
            return "if(%s){%s}" % (cond, self.tail(t))
        if k == "b":
            t = ins.targets[0]
            if start <= t < end:
                return "goto L_%x;" % t
            return self.tail(t)
        if k == "swi":
            return "rt_swi(0x%x);" % (hw & 0xFF)
        if k == "undef":
            return "rt_undef(0x%xu);" % a
        if k in ("bx", "blxr", "movpc", "addpc", "hi"):
            op = (hw >> 8) & 3
            rm = (hw >> 3) & 15
            rd = (hw & 7) | ((hw >> 4) & 8)
            src = "0x%xu" % (a + 4) if rm == 15 else R[rm]
            if k == "bx":
                if rm == 14 or rm in popped:
                    return "RETURN();"
                self.stats["bx_indirect"] += 1
                return "SP=sp;return rt_icall(%s,r0,r1,r2,r3);" % src
            if k == "blxr":
                return "lr=0x%xu;SP=sp;CALLQ(rt_icall(%s,r0,r1,r2,r3));" % ((a + 2) | 1, src)
            if k == "movpc":
                if rm == 14:
                    return "RETURN();"
                self.stats["movpc"] += 1
                return "SP=sp;return rt_icall(%s|1u,r0,r1,r2,r3);" % src
            if k == "addpc":
                self.stats["addpc"] += 1
                return "rt_undef(0x%xu);RETURN();" % a
            d = R[rd]
            if op == 0:
                return "%s=%s+%s;" % (d, d if rd != 15 else "0", src)
            if op == 1:
                return "SUBS(%s,%s);" % (R[rd] if rd != 15 else "0x%xu" % (a + 4), src)
            if op == 2:
                return "%s=%s;" % (d, src)
        if k == "pop":
            regs = [i for i in range(8) if hw & (1 << i)]
            s = []
            for i, r in enumerate(regs):
                s.append("%s=L32(sp+%d);" % (R[r], 4 * i))
            cnt = len(regs) + (1 if hw & 0x100 else 0)
            s.append("sp+=%d;" % (4 * cnt))
            if hw & 0x100:
                s.append("RETURN();")
            return "".join(s)
        return self.op(ins)

    def op(self, ins):
        a, hw = ins.a, ins.hw
        R = REG
        rd, rs, rn = hw & 7, (hw >> 3) & 7, (hw >> 6) & 7
        d, s_ = R[rd], R[rs]
        top3 = hw >> 13
        if top3 == 0:
            op = (hw >> 11) & 3
            if op < 3:
                imm = (hw >> 6) & 31
                if op == 0:
                    if imm == 0:
                        return "%s=%s;NZ(%s);" % (d, s_, d)
                    return "c_=(%s>>%d)&1;%s=%s<<%d;NZ(%s);" % (s_, 32 - imm, d, s_, imm, d)
                if op == 1:
                    if imm == 0:
                        return "c_=%s>>31;%s=0;NZ(%s);" % (s_, d, d)
                    return "c_=(%s>>%d)&1;%s=%s>>%d;NZ(%s);" % (s_, imm - 1, d, s_, imm, d)
                if imm == 0:
                    return "c_=%s>>31;%s=(uint32_t)((int32_t)%s>>31);NZ(%s);" % (s_, d, s_, d)
                return "c_=(%s>>%d)&1;%s=(uint32_t)((int32_t)%s>>%d);NZ(%s);" % (s_, imm - 1, d, s_, imm, d)
            imm_flag = (hw >> 10) & 1
            sub = (hw >> 9) & 1
            b = "%du" % rn if imm_flag else R[rn]
            return "%s(%s,%s,%s);" % ("SUBSD" if sub else "ADDSD", d, s_, b)
        if top3 == 1:
            op = (hw >> 11) & 3
            r = R[(hw >> 8) & 7]
            imm = hw & 0xFF
            if op == 0:
                return "%s=%du;NZ(%s);" % (r, imm, r)
            if op == 1:
                return "SUBS(%s,%du);" % (r, imm)
            if op == 2:
                return "ADDSD(%s,%s,%du);" % (r, r, imm)
            return "SUBSD(%s,%s,%du);" % (r, r, imm)
        if (hw >> 10) == 0b010000:
            op = (hw >> 6) & 15
            m_ = s_
            if op == 0:
                return "%s&=%s;NZ(%s);" % (d, m_, d)
            if op == 1:
                return "%s^=%s;NZ(%s);" % (d, m_, d)
            if op == 2:
                return "{uint32_t n2_=%s&255;if(n2_){c_=lsl_reg_c(%s,n2_,c_);%s=lsl_reg(%s,n2_);}}NZ(%s);" % (m_, d, d, d, d)
            if op == 3:
                return "{uint32_t n2_=%s&255;if(n2_){c_=lsr_reg_c(%s,n2_,c_);%s=lsr_reg(%s,n2_);}}NZ(%s);" % (m_, d, d, d, d)
            if op == 4:
                return "{uint32_t n2_=%s&255;if(n2_){c_=asr_reg_c(%s,n2_,c_);%s=asr_reg(%s,n2_);}}NZ(%s);" % (m_, d, d, d, d)
            if op == 5:
                return "ADCS(%s,%s,%s);" % (d, d, m_)
            if op == 6:
                return "SBCS(%s,%s,%s);" % (d, d, m_)
            if op == 7:
                return "{uint32_t n2_=%s&255;if(n2_){uint32_t k_=n2_&31;if(k_)%s=(%s>>k_)|(%s<<(32-k_));c_=%s>>31;}}NZ(%s);" % (m_, d, d, d, d, d)
            if op == 8:
                return "{uint32_t t_=%s&%s;NZ(t_);}" % (d, m_)
            if op == 9:
                return "SUBSD(%s,0u,%s);" % (d, m_)
            if op == 10:
                return "SUBS(%s,%s);" % (d, m_)
            if op == 11:
                return "ADDS_(%s,%s);" % (d, m_)
            if op == 12:
                return "%s|=%s;NZ(%s);" % (d, m_, d)
            if op == 13:
                return "%s*=%s;NZ(%s);" % (d, m_, d)
            if op == 14:
                return "%s&=~%s;NZ(%s);" % (d, m_, d)
            return "%s=~%s;NZ(%s);" % (d, m_, d)
        if (hw >> 11) == 0b01001:
            r = R[(hw >> 8) & 7]
            la = ((a + 4) & ~3) + (hw & 0xFF) * 4
            v = self.img.m.u32(la)
            return "%s=0x%xu;" % (r, v)
        if (hw >> 12) == 0b0101:
            op = (hw >> 9) & 7
            addr = "%s+%s" % (s_, R[rn])
            return [
                "S32(%s,%s);" % (addr, d), "S16(%s,%s);" % (addr, d), "S8(%s,%s);" % (addr, d),
                "%s=(uint32_t)(int32_t)(int8_t)L8(%s);" % (d, addr), "%s=L32(%s);" % (d, addr),
                "%s=L16(%s);" % (d, addr), "%s=L8(%s);" % (d, addr),
                "%s=(uint32_t)(int32_t)(int16_t)L16(%s);" % (d, addr),
            ][op]
        if (hw >> 13) == 0b011:
            byte = (hw >> 12) & 1
            load = (hw >> 11) & 1
            imm = (hw >> 6) & 31
            off = imm if byte else imm * 4
            addr = "%s+%du" % (s_, off)
            if byte:
                return ("%s=L8(%s);" % (d, addr)) if load else ("S8(%s,%s);" % (addr, d))
            return ("%s=L32(%s);" % (d, addr)) if load else ("S32(%s,%s);" % (addr, d))
        if (hw >> 12) == 0b1000:
            load = (hw >> 11) & 1
            addr = "%s+%du" % (s_, ((hw >> 6) & 31) * 2)
            return ("%s=L16(%s);" % (d, addr)) if load else ("S16(%s,%s);" % (addr, d))
        if (hw >> 12) == 0b1001:
            load = (hw >> 11) & 1
            r = R[(hw >> 8) & 7]
            addr = "sp+%du" % ((hw & 0xFF) * 4)
            return ("%s=L32(%s);" % (r, addr)) if load else ("S32(%s,%s);" % (addr, r))
        if (hw >> 12) == 0b1010:
            r = R[(hw >> 8) & 7]
            imm = (hw & 0xFF) * 4
            if hw & 0x800:
                return "%s=sp+%du;" % (r, imm)
            self.stats["adr"] += 1
            return "%s=0x%xu;" % (r, ((a + 4) & ~3) + imm)
        if (hw >> 8) == 0b10110000:
            imm = (hw & 0x7F) * 4
            return ("sp-=%du;" % imm) if hw & 0x80 else ("sp+=%du;" % imm)
        if (hw >> 8) == 0b10110010:
            op = (hw >> 6) & 3
            return ["%s=(uint32_t)(int32_t)(int16_t)%s;", "%s=(uint32_t)(int32_t)(int8_t)%s;",
                    "%s=%s&0xffffu;", "%s=%s&0xffu;"][op] % (d, s_)
        if (hw >> 8) == 0b10110100 or (hw >> 8) == 0b10110101:
            regs = [i for i in range(8) if hw & (1 << i)]
            lr_ = bool(hw & 0x100)
            cnt = len(regs) + lr_
            s = ["sp-=%d;" % (4 * cnt)]
            for i, r in enumerate(regs):
                s.append("S32(sp+%d,%s);" % (4 * i, R[r]))
            if lr_:
                s.append("S32(sp+%d,lr);" % (4 * len(regs)))
            return "".join(s)
        if (hw >> 8) == 0b10111010:
            op = (hw >> 6) & 3
            if op == 0:
                return "%s=__builtin_bswap32(%s);" % (d, s_)
            if op == 1:
                return "%s=((%s&0x00ff00ffu)<<8)|((%s>>8)&0x00ff00ffu);" % (d, s_, s_)
            if op == 3:
                return "%s=(uint32_t)(int32_t)(int16_t)(uint16_t)(((%s&0xff)<<8)|((%s>>8)&0xff));" % (d, s_, s_)
        if (hw >> 12) == 0b1100:
            load = (hw >> 11) & 1
            rn_ = (hw >> 8) & 7
            regs = [i for i in range(8) if hw & (1 << i)]
            s = ["{uint32_t b_=%s;" % R[rn_]]
            for i, r in enumerate(regs):
                s.append(("%s=L32(b_+%d);" % (R[r], 4 * i)) if load else ("S32(b_+%d,%s);" % (4 * i, R[r])))
            if not (load and rn_ in regs):
                s.append("%s=b_+%d;" % (R[rn_], 4 * len(regs)))
            s.append("}")
            return "".join(s)
        if (hw >> 8) == 0b10111110:
            return "rt_undef(0x%xu);" % a
        if hw == 0xB662 or hw == 0xB672 or (hw >> 5) == 0b10110110011:
            return ";"
        self.stats["unknown_op"] += 1
        return "rt_undef(0x%xu);/*%04x*/" % (a, hw)


# --------------------------------------------------------------- reachability

def reachable(img):
    m = img.m
    roots = [img.byname[n] for n in ROOT_NAMES if n in img.byname]
    mi = m.sect("__DATA", "__mod_init_func")
    if mi:
        for k in range(mi["size"] // 4):
            roots.append(m.u32(mi["addr"] + 4 * k) & ~1)
    # text pointers anywhere in __DATA, except Objective-C metadata and skipped owners
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
        if img.skip(name) or not img.fthumb.get(f, False) or name in SWITCH:
            continue
        seen.add(f)
        body, labels, sw, probs = func_body(img, f, img.fend[f])
        for ins in body.values():
            if ins.kind in ("bl", "blx") and not img.is_stub(ins.call):
                stack.append(ins.call & ~1)
            if ins.kind in ("b", "bcond"):
                t = ins.targets[0]
                if not (f <= t < img.fend[f]):
                    stack.append(t & ~1)
            if ins.kind == "op" and (ins.hw >> 11) == 0b01001:
                la = ((ins.a + 4) & ~3) + (ins.hw & 0xFF) * 4
                v = m.u32(la)
                if img.t0 <= v < img.t1:
                    stack.append(v & ~1)
    return seen


# --------------------------------------------------------------- output

HEADER = r'''/* generated by nano7kit games/fruitninja/recomp.py from the user's own Fruit binary: do not commit */
#include "rt.h"
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
    per = (len(order) + chunks - 1) // chunks
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
        with open(os.path.join(outdir, "recomp_%02d.c" % c), "w") as fh:
            fh.write(HEADER + '#include "recomp_funcs.h"\n#include "recomp_imports.h"\n\n' + "\n\n".join(out) + "\n")
    imps = sorted({n for n in em.used_imports if n} | {n for (k, n) in img.m.imports.values() if n})
    with open(os.path.join(outdir, "recomp_imports.h"), "w") as fh:
        fh.write("/* generated */\n#include <stdint.h>\n")
        for n in imps:
            fh.write("uint64_t I_%s(uint32_t,uint32_t,uint32_t,uint32_t);\n" % cname(n))
    # import slots in guest memory: every la/nl pointer to an external symbol
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
    text = img.m.sect("__TEXT", "__cstring")["addr"] & ~0xFFF
    dseg = [x for x in img.m.segments if x[0] == "__DATA"][0]
    with open(os.path.join(outdir, "rt_layout.h"), "w") as fh:
        fh.write("/* generated */\n#define GUEST_BASE 0x%xu\n#define GUEST_IMAGE_END 0x%xu\n"
                 "#define GUEST_DATA_END 0x%xu\n#define GUEST_STACK 0x40000u\n"
                 % (text, dseg[1] + dseg[4], dseg[1] + dseg[2]))
    with open(os.path.join(outdir, "guest.img"), "wb") as fh:
        fh.write(read_span(img.m, text, dseg[1] + dseg[4] - text))
    entries = {"SetRootDir": "__ZN15IPhoneInterface10SetRootDirEPcS0_",
               "InitializeApp": "__ZN15IPhoneInterface13InitializeAppEv",
               "UpdateApp": "__ZN15IPhoneInterface9UpdateAppEff",
               "DrawApp": "__ZN15IPhoneInterface7DrawAppEiijjff",
               "Touch": "__ZN15IPhoneInterface22UpdateTouchScreenInputEjbfff",
               "InitSoundSession": "__ZN15IPhoneInterface16InitSoundSessionEv"}
    with open(os.path.join(outdir, "rt_entry.h"), "w") as fh:
        fh.write("/* generated */\n")
        for k, n in entries.items():
            a = img.byname.get(n)
            fh.write("#define FN_%s 0x%xu\n" % (k, a if a is not None and a in seen_set else 0))
        mi = img.m.sect("__DATA", "__mod_init_func")
        inits = [img.m.u32(mi["addr"] + 4 * k) & ~1 for k in range(mi["size"] // 4)] if mi else []
        inits = [a for a in inits if a in seen_set]
        fh.write("#define FN_INIT_COUNT %d\nstatic const uint32_t fn_inits[] = {%s0};\n"
                 % (len(inits), "".join("0x%xu," % a for a in inits)))
    with open(os.path.join(outdir, "layout.txt"), "w") as fh:
        for seg, va, vs, fo, fs in img.m.segments:
            fh.write("%s %x %x %x %x\n" % (seg, va, vs, fo, fs))
    with open(os.path.join(outdir, "stats.txt"), "w") as fh:
        for k, v in sorted(em.stats.items()):
            fh.write("%s %s\n" % (k, v))
        fh.write("imports %d\n" % len(imps))
        for t, n in sorted(em.missing_calls.items()):
            fh.write("missing %x %s %d\n" % (t, img.fname.get(t, "?"), n))
    print("recomp: %d functions, %d instructions, %d imports, %d missing call targets"
          % (em.stats["functions"], em.stats["instructions"], len(imps), len(em.missing_calls)))


if __name__ == "__main__":
    main()
