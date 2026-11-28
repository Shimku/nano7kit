#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Run the Tiny LLM .hbapp in Unicorn under iPod-like conditions.

The image is loaded the way the resident loads it (HRL1 header plus the
ABS32 table, see tools/mkrelocapp.py), payload_entry is called, then the
raw-surface frame function once per heartbeat. The OS heap, file system,
clock, buttons and touch are the hooks the NanoApps SDK uses on the nano
7G (pocketjs/emu.py). The model file is served read-only at
/Apps/Data/TinyLLM/model.bin; any write stops the run.

Instructions are counted exactly (Thumb-2 halfwords decoded per block),
and every call of tllm_step is timed in instructions, so the run reports
instructions per token. The emulated clock runs at --mips instructions per
microsecond, which is what the on-screen tok/s figure is based on.

Usage:
  emu.py APP.hbapp OUTDIR [script] [--model FILE] [--os-blocks 2.0,1.0,0.9]
         [--largest BYTES] [--mips 175]
Script lines: a frame count; `shot NAME`; `tap X Y` (panel); `ltap X Y`
(in the sideways view, iPod on its side with Home on the right: panel
(239 - Y, X)); `click up|down|select`; `until-done MAX` (frames until the
story ends); `relaunch`. A shot taken while the app is sideways is saved
turned, 432x240, as it reads in the hand.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(KIT))

from pocketjs.emu import BASE, FB, H, SHARED, STACK_LIMIT, STACK_TOP, W, Emu  # noqa: E402
from unicorn import UC_HOOK_CODE  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP  # noqa: E402

MODEL = "/Apps/Data/TinyLLM/model.bin"
AUX_OFFSET = 24  # hb_shared.aux_block


def symbols(elf: Path) -> dict:
    from elftools.elf.elffile import ELFFile
    out = {}
    with open(elf, "rb") as f:
        tab = ELFFile(f).get_section_by_name(".symtab")
        for s in tab.iter_symbols():
            if s.name and s["st_info"]["type"] in ("STT_FUNC", "STT_OBJECT"):
                out[s.name] = (s["st_value"] & ~1, s["st_size"])
    return out


class TinyEmu(Emu):
    def __init__(self, app, largest, regions, model, mips=175, syms=None):
        served = {MODEL: model} if model is not None else {}
        super().__init__(app, largest, (), 20000, regions, served, {})
        try:
            from unicorn.arm_const import UC_CPU_ARM_CORTEX_A7
            # Unicorn has no Cortex-A5. The A7 is the same ARMv7-A profile
            # with Thumb-2, the DSP/SIMD32 instructions and VFPv4-D16.
            self.uc.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_A7)
        except Exception:
            pass
        self.mips = mips
        self.syms = syms or {}
        syms = self.syms
        self.counts = {}
        self.steps = []          # (instructions, result) per tllm_step call
        self.step_stack = []
        self.ret_hooked = set()
        self.seeds = []
        self.dirs.update({"/Apps", "/Apps/Data", "/Apps/Data/TinyLLM"})
        step = syms.get("tllm_step")
        if step:
            self.uc.hook_add(UC_HOOK_CODE, self.on_step, begin=BASE + step[0], end=BASE + step[0])
        begin = syms.get("tllm_begin")
        if begin:
            self.uc.hook_add(UC_HOOK_CODE, self.on_begin, begin=BASE + begin[0], end=BASE + begin[0])

    # exact instruction count per translated block
    def block(self, uc, address, size, _):
        n = self.counts.get((address, size))
        if n is None:
            code = bytes(uc.mem_read(address & ~1, size))
            n, i = 0, 0
            while i + 1 < len(code):
                hw = code[i] | (code[i + 1] << 8)
                i += 4 if (hw >> 11) in (0x1D, 0x1E, 0x1F) else 2
                n += 1
            self.counts[(address, size)] = n
        self.insns += n
        if self.prof is not None:
            self.prof[address] = self.prof.get(address, 0) + n
        sp = uc.reg_read(UC_ARM_REG_SP)
        if not STACK_LIMIT <= sp <= STACK_TOP:
            return
        if sp < self.min_sp:
            self.min_sp = sp
        if sp < self.min_sp_call:
            self.min_sp_call = sp

    def timer(self, uc, access, address, size, value, _):
        now = self.calls * self.heartbeat_us + self.insns // self.mips
        uc.mem_write(0x3C700000 + 0x84, struct.pack("<I", now & 0xFFFFFFFF))

    def on_step(self, uc, address, size, _):
        ret = uc.reg_read(UC_ARM_REG_LR) & ~1
        self.step_stack.append(self.insns)
        if ret not in self.ret_hooked:
            self.ret_hooked.add(ret)
            uc.hook_add(UC_HOOK_CODE, self.on_step_ret, begin=ret, end=ret)

    def on_step_ret(self, uc, address, size, _):
        if self.step_stack:
            start = self.step_stack.pop()
            self.steps.append((self.insns - start, uc.reg_read(UC_ARM_REG_R0)))

    def on_begin(self, uc, address, size, _):
        lo, hi = uc.reg_read(UC_ARM_REG_R2), uc.reg_read(UC_ARM_REG_R3)
        prompt = self.cstr(uc.reg_read(UC_ARM_REG_R1))
        self.seeds.append(((hi << 32) | lo, prompt))

    def writable(self, path):
        raise RuntimeError(f"the app wrote to {path}; Tiny LLM never writes")

    def button(self, name: str, down: bool) -> None:
        if name in ("up", "down"):
            gpio = 0x3CF00000 + 5 * 0x20 + 4
            bit = 0 if name == "up" else 1
            cur = struct.unpack("<I", bytes(self.uc.mem_read(gpio, 4)))[0]
            cur = cur & ~(1 << bit) if down else cur | (1 << bit)
            self.uc.mem_write(gpio, struct.pack("<I", cur))
        else:  # select = Play/Pause, from the OS key-state cache
            self.uc.mem_write(0x089A54A0 + 6 * 24, bytes((1 if down else 0,)))

    def var(self, name, fmt="<i"):
        addr, _ = self.syms[name]
        return struct.unpack(fmt, bytes(self.uc.mem_read(BASE + addr, struct.calcsize(fmt))))[0]

    def story(self) -> str:
        addr, size = self.syms["s_text"]
        n = self.var("s_len")
        return bytes(self.uc.mem_read(BASE + addr, max(0, min(n, size)))).decode("latin-1")


def classify(raw: bytes, land: bool = False) -> str:
    def has(rgb):
        return bytes((rgb & 255, (rgb >> 8) & 255, rgb >> 16, 0xFF)) in raw

    def px(x, y):
        if land:  # view (x, y) is panel (W - 1 - y, x)
            x, y = W - 1 - y, x
        o = (y * W + x) * 4
        b, g, r = raw[o], raw[o + 1], raw[o + 2]
        return (r << 16) | (g << 8) | b

    if has(0xFF5555):
        return "low-memory"
    if has(0xFFAA00):
        return "missing"
    if has(0x55AAFF):
        return "damaged"
    if px(2, 220 if land else 390) == 0x1A2230:
        return "story"
    if land and px(3, 75) in (0x232C3B, 0x3F6F4F):
        return "keyboard"
    if has(0x2B5D3A):
        return "menu"
    return "other"


def png(raw: bytes, path: Path, land: bool) -> None:
    """The panel as a PNG; sideways views turned 90 degrees anticlockwise."""
    import subprocess
    turn = ["-vf", "transpose=2"] if land else []
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgra",
                    "-s", f"{W}x{H}", "-i", "-"] + turn + ["-frames:v", "1", str(path)],
                   input=raw, check=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("app")
    ap.add_argument("outdir")
    ap.add_argument("script", nargs="?")
    ap.add_argument("--model", default=None, help="host file served as " + MODEL)
    ap.add_argument("--largest", type=int, default=3 * 1024 * 1024)
    ap.add_argument("--os-blocks", default=None, help="free OS heap blocks in MiB, e.g. 2.0,1.0,0.9")
    ap.add_argument("--mips", type=int, default=175,
                    help="emulated clock in instructions per microsecond (175: 320 MHz at 0.55 per cycle)")
    ap.add_argument("--elf", default=None, help="the .reloc.elf (default: next to the .hbapp)")
    a = ap.parse_args(argv)

    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    elf = Path(a.elf) if a.elf else Path(a.app).with_suffix("").with_suffix(".reloc.elf")
    syms = symbols(elf)
    model = Path(a.model).read_bytes() if a.model else None
    regions = [int(float(v) * 1024 * 1024) for v in a.os_blocks.split(",")] if a.os_blocks else None
    emu = TinyEmu(a.app, a.largest, regions, model, a.mips, syms)
    print(f"OS heap: free {emu.heap.free_total():,}, largest {emu.heap.largest():,} "
          f"(image span {emu.image_span:,} placed)" if regions else
          f"OS heap: largest {emu.heap.largest():,}")
    frame_fn = emu.call(BASE + emu.entry, [0, FB, W, H, SHARED])
    print(f"stack: payload_entry {STACK_TOP - 16 - emu.min_sp_call:,} bytes")
    if not frame_fn:
        print("payload_entry refused the launch")
        return 1
    script = Path(a.script).read_text() if a.script else "2\nshot first\n"
    frames = 0
    deepest = 0

    def frame():
        nonlocal frames, deepest
        emu.calls += 1
        emu.call(frame_fn, [])
        deepest = max(deepest, STACK_TOP - 16 - emu.min_sp_call)
        frames += 1

    for line in script.splitlines():
        w = line.split("#")[0].split()
        if not w:
            continue
        if w[0] == "shot":
            land = bool(emu.var("s_land"))
            raw = bytes(emu.uc.mem_read(FB, W * H * 4))
            png(raw, out / f"{w[1]}.png", land)
            kind = classify(raw, land)
            (out / f"{w[1]}.kind").write_text(kind + "\n")
            (out / f"{w[1]}.orient").write_text(("sideways" if land else "upright") + "\n")
            print(f"{w[1]}: frame {frames} ({kind}, {'sideways' if land else 'upright'})")
        elif w[0] == "ltap":
            vx, vy = int(w[1]), int(w[2])
            x, y = W - 1 - vy, vx
            for _ in range(3):
                emu.touch(x, y, True)
                frame()
            emu.touch(x, y, False)
            frame()
        elif w[0] == "tap":
            x, y = int(w[1]), int(w[2])
            for _ in range(3):
                emu.touch(x, y, True)
                frame()
            emu.touch(x, y, False)
            frame()
        elif w[0] == "click":
            emu.button(w[1], True)
            frame()
            frame()
            emu.button(w[1], False)
            frame()
        elif w[0] == "until-done":
            limit = int(w[1])
            n = 0
            while n < limit and not emu.var("s_finished"):
                frame()
                n += 1
            print(f"until-done: {n} frames, finished {emu.var('s_finished')}")
        elif w[0] == "relaunch":
            print("text: " + emu.story().replace("\n", "\\n"))
            frame_fn = emu.call(BASE + emu.entry, [0, FB, W, H, SHARED])
        else:
            for _ in range(int(w[0])):
                frame()

    for tag, v1, v2 in emu.trace:
        print(f"trace {tag:8} {v1:#010x} {v2:#010x}  ({v1}, {v2})")
    print(f"frames {frames}; instructions {emu.insns:,}; stack: deepest {deepest:,} bytes")
    if emu.seeds:
        seed, prompt = emu.seeds[-1]
        print(f"begin: seed {seed} prompt {prompt!r}")
    # group tllm_step calls into tokens (a token ends on TOKEN or DONE)
    per_token, cur = [], 0
    for n, r in emu.steps:
        cur += n
        if r in (1, 2):
            per_token.append(cur)
            cur = 0
    if per_token:
        avg = sum(per_token) // len(per_token)
        marks = [i for i in (0, 31, 63, 127, 191, 254) if i < len(per_token)]
        print(f"tokens {len(per_token)}; instructions per token: average {avg:,}; " +
              ", ".join(f"pos {i + 1} {per_token[i]:,}" for i in marks))
    if "s_text" in syms:
        print("text: " + emu.story().replace("\n", "\\n"))
    print(f"files: {emu.bytes_read:,} bytes read; writes {emu.writes}")
    print(f"OS heap: blocks held {len(emu.heap.blocks)} ({sum(emu.heap.blocks.values()):,} bytes)")
    aux = struct.unpack("<I", bytes(emu.uc.mem_read(SHARED + AUX_OFFSET, 4)))[0]
    print(f"aux block: {'set' if aux else 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
