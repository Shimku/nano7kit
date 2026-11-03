#!/usr/bin/env python3
"""Run a PocketJS nano7g .hbapp in Unicorn (Cortex-A8, Thumb-2), box or Mac.

A host-side dry run, not a device: it loads the relocatable image the way
the resident does (base + ABS32 table), calls payload_entry(0, fb, 240, 432,
shared), then calls the frame function once per heartbeat with a scripted
touch in the shared mailbox. The four OS heap entry points are emulated
(first-fit allocator over a fake OS heap); the timer reads a counter that
advances with executed instructions; the volume GPIO reads as released.
Writes PNG frames (ffmpeg), the resident trace lines, the deepest stack
use and instructions per frame. Script lines: `frames [x y [x2 y2 ...]] [to X Y ...]` (more pairs are more fingers), `shot NAME`.
`--serve DEVICE_PATH=HOST_FILE` serves a read-only file (a QuickJS package).
Writes are emulated in memory under /Apps/Data/PocketJS/ only (data.fs:
open for writing, write, truncate, sync, remove, mkdir, rmdir, attributes and
directory listing); `--fs-dir DIR` loads that tree from DIR first and saves it
back at the end, so a second run sees the first run's files. A write anywhere
else stops the run.

Usage: emu.py APP.hbapp OUTDIR [script] [--largest BYTES]
"""
import argparse
import struct
import subprocess
import sys
from pathlib import Path

from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE, UC_HOOK_BLOCK, UC_HOOK_MEM_READ, UC_HOOK_MEM_INVALID
from unicorn.arm_const import (
    UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1,
    UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP,
)

RAM = 0x08000000
RAM_SIZE = 0x02000000
BASE = 0x09000000          # app arena (the resident's operator-new block)
SHARED = 0x08F00000
TRACE_FN = 0x08F01000
FB = 0x08E00000            # 240 * 432 * 4 = 0x65400
STACK_TOP = 0x08DF0000
STACK_LIMIT = 0x08D00000   # 960 KB mapped below the top; real task stacks are far smaller
STOP = 0x08F02000
OS_HEAP = 0x09800000
OS_HEAP_SIZE = 0x00800000
TIMER = 0x3C700000
GPIO = 0x3CF00000
A_HEAP_FREESIZE = 0x08417824
A_HEAP_LARGEST = 0x0800CBB8
A_HEAP_ALLOC = 0x0842B440
A_OP_DELETE = 0x0842D86C
A_FS_EXISTS = 0x0841BB7C   # hb_fs_exists -> the OS exists call (path, volume)
A_FS_GETSIZE = 0x08049598  # hb_fs_size -> (path, uint64_t *size, volume)
A_FOBJ_OPEN = 0x084137A8   # the OS file object: open(obj, path, mode 1 = read, ...)
A_FOBJ_READY = 0x08417E18
A_FOBJ_READ = 0x0841CDDC   # read(obj, n, buf, uint64_t *got)
A_FOBJ_CLOSE = 0x08423BE0
A_FOBJ_WRITE = 0x0841BA42  # write(obj, n, buf, uint32_t *put): data.fs only
A_FOBJ_TRUNC = 0x0841B09E  # truncate(obj, size)
A_FOBJ_SYNC = 0x0840718C
A_FS_REMOVE = 0x0840AD9C   # (path, volume) -> 0
A_FS_MKDIR = 0x083FBF22    # (path, volume) -> 0 made, 0xd already there
A_FS_GETATTR = 0x083FA026  # (path, uint8_t *attr, volume): bit 0 = directory
A_FS_RMDIR = 0x08157F3E    # (path, 0, volume, 0)
A_DIR_CTOR = 0x08417B94    # (iter, path, recursive, volume)
A_DIR_NEXT = 0x08417392    # (iter, path_obj, bool *is_dir) -> more
A_DIR_DTOR = 0x084178A8
A_PATH_CTOR = 0x08423A6C
A_PATH_DTOR = 0x0842D96C
A_PATH_ASCHAR = 0x0842D510
NAMES = 0x08F08000         # where listed names are written for PATH_ASCHAR (64 slots of 256 bytes)
WRITABLE = "/Apps/Data/PocketJS/"
ACCEL = 0x08A91E50         # the OS accelerometer task's last sample (3 x int32, 1/65536 g)
HB_SHARED_MAGIC = 0x48534248
HB_TOUCH_MB_MAGIC = 0x54504E54
TOUCH_LISTS = 0x089A5298   # the OS's per-finger sample lists (8 heads, 0x18 bytes each)
A_RTC_INSTANCE = 0x0842BF60  # hb_rtc_read: the OS time manager's instance getter
RTC_OBJ = 0x08F0D000       # the dry run's time manager: vtable pointer, then the vtable
RTC_GET = 0x08F0D100       # its vtable+0x38 get_time(out, self)
BATTERY = 0x0890C070       # the OS PMU cache hb_battery reads (charge state +0, level +0x0c)
# the media player hb_media.c and Home's System sheet drive. The
# dry run plays one track; pause and stop only change its state (no memory
# comes back here: that is a hardware test).
A_SMP_GET, A_SMP_STATE = 0x083F3590, 0x082676CC
A_TASK_GET, A_TASK_PAUSE, A_TASK_RESUME, A_TASK_STOP = 0x0841BFB4, 0x0824DE54, 0x0824DE7C, 0x083BF040
MEDIA_OBJ = 0x08F0E000
TOUCH_NODES = 0x08F03000   # where the dry run keeps each list's sentinel and samples (0x100 a list)
W, H = 240, 432


def relocated(data, base):
    magic, entry, image_size, span, count, align = struct.unpack_from("<6I", data)
    assert magic == 0x314C5248 and align == 16, "not an HRL1 .hbapp"
    image = bytearray(span)
    image[:image_size] = data[24:24 + image_size]
    for (offset,) in struct.iter_unpack("<I", data[24 + image_size:24 + image_size + count * 4]):
        value = struct.unpack_from("<I", image, offset)[0]
        struct.pack_into("<I", image, offset, (value + base) & 0xFFFFFFFF)
    return entry, bytes(image)


class Heap:
    """First-fit over [OS_HEAP, OS_HEAP + size); `cap` limits one block like
    the device's largest-free figure.

    With `regions` (free block sizes in bytes, in address order) the OS heap
    is modelled as those separate free blocks: 64 KB of used memory sits
    between them, so nothing spans two. That is a model of a fragmented heap
    (for example Probe's 7.0 MB free / 4.0 MB largest), not a measurement."""

    GAP = 64 * 1024

    def __init__(self, cap, regions=None, misalign=0):
        self.cap = cap
        # The OS allocator only promises 4-byte alignment, and which
        # blocks come back 4 past an 8-byte boundary depends on what ran
        # before. `misalign` returns every block that many bytes past one.
        self.misalign = misalign
        self.blocks = {}
        self.pinned = {}     # separators and the app image: not the app's blocks
        if regions:
            self.cap = 1 << 31
            cur = OS_HEAP
            for size in regions:
                cur += (size + 15) & ~15
                self.pinned[cur] = self.GAP
                cur += self.GAP
            if cur > OS_HEAP + OS_HEAP_SIZE:
                raise ValueError("the modelled OS heap does not fit the emulator's 8 MB window")
            self.pinned[cur] = OS_HEAP + OS_HEAP_SIZE - cur

    def pin(self, size):
        """Take `size` first-fit for something the app does not own (its image)."""
        ptr = self.alloc(size)
        if ptr:
            start = ptr - self.misalign
            self.pinned[start] = self.blocks.pop(start)
        return ptr

    def free_ranges(self):
        cur, out = OS_HEAP, []
        for start, size in sorted(list(self.blocks.items()) + list(self.pinned.items())):
            if start > cur:
                out.append((cur, start - cur))
            cur = max(cur, start + size)
        if cur < OS_HEAP + OS_HEAP_SIZE:
            out.append((cur, OS_HEAP + OS_HEAP_SIZE - cur))
        return out

    def largest(self):
        return min(self.cap, max((s for _, s in self.free_ranges()), default=0))

    def free_total(self):
        return sum(s for _, s in self.free_ranges())

    def alloc(self, size):
        size = (size + self.misalign + 15) & ~15
        if size > self.cap:
            return 0
        for start, room in self.free_ranges():
            if room >= size:
                self.blocks[start] = size
                return start + self.misalign
        return 0

    def free(self, ptr):
        self.blocks.pop(ptr - self.misalign, None)


class Emu:
    def __init__(self, app, largest, files=(), heartbeat_us=33334, regions=None, served=None, data=None, misalign=0):
        self.prof = None     # block address -> instructions, while profiling
        self.watch = {}      # helper entry address -> name (--callers)
        self.callers = {}    # (helper name, caller address) -> calls, while profiling
        self.uc = uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        try:
            from unicorn.arm_const import UC_CPU_ARM_CORTEX_A8
            uc.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_A8)
        except Exception:
            pass
        uc.mem_map(RAM, RAM_SIZE)
        uc.mem_map(TIMER, 0x1000)
        uc.mem_map(GPIO, 0x1000)
        uc.mem_write(GPIO + 5 * 0x20 + 4, struct.pack("<I", 0xFFFFFFFF))
        self.entry, image = relocated(Path(app).read_bytes(), BASE)
        uc.mem_write(BASE, image)
        self.heap = Heap(largest, regions, misalign)
        self.image_span = 0
        if regions:
            # The resident loads the image (span: code, data and bss) into an
            # OS heap arena before the app runs.
            self.image_span = struct.unpack_from("<6I", Path(app).read_bytes())[3]
            if not self.heap.pin(self.image_span):
                raise RuntimeError(f"no modelled block holds the {self.image_span:,}-byte image")
        self.trace = []
        self.insns = 0
        self.min_sp = STACK_TOP
        self.min_sp_call = STACK_TOP
        self.served = dict(served or {})  # device path -> bytes (read-only)
        self.files = set(files) | set(self.served)  # paths hb_fs_exists reports present
        self.open = {}                # file object address -> [path, position]
        self.data = dict(data or {})  # device path -> bytearray: files written under WRITABLE
        self.dirs = set()             # directories (parents of every file, and made ones)
        for path in list(self.files) + list(self.data):
            self.add_parents(path)
        self.add_parents(WRITABLE + "x")
        self.lists = {}               # directory iterator address -> [(name, is_dir), ...]
        self.name_slot = 0
        self.writes = 0
        self.bytes_written = 0
        self.bytes_read = 0
        self.heartbeat_us = heartbeat_us
        self.calls = 0                # heartbeats so far (the clock advances by heartbeat_us each)
        for address in (A_HEAP_FREESIZE, A_HEAP_LARGEST, A_HEAP_ALLOC, A_OP_DELETE, TRACE_FN, A_FS_EXISTS,
                        A_FS_GETSIZE, A_FOBJ_OPEN, A_FOBJ_READY, A_FOBJ_READ, A_FOBJ_CLOSE,
                        A_FOBJ_WRITE, A_FOBJ_TRUNC, A_FOBJ_SYNC, A_FS_REMOVE, A_FS_MKDIR, A_FS_GETATTR,
                        A_FS_RMDIR, A_DIR_CTOR, A_DIR_NEXT, A_DIR_DTOR, A_PATH_CTOR, A_PATH_DTOR,
                        A_PATH_ASCHAR):
            uc.mem_write(address, b"\x70\x47")  # bx lr
            uc.hook_add(UC_HOOK_CODE, self.os_call, begin=address, end=address)
        # a clock and a battery for the home screen's status bar
        # (Sunday 1 November 2026, 9:41:00, level 12 of 15, on battery).
        for address in (A_RTC_INSTANCE, RTC_GET):
            uc.mem_write(address, b"\x70\x47")
            uc.hook_add(UC_HOOK_CODE, self.os_call, begin=address, end=address)
        uc.mem_write(RTC_OBJ, struct.pack("<I", RTC_OBJ + 4))
        uc.mem_write(RTC_OBJ + 4 + 0x38, struct.pack("<I", RTC_GET | 1))
        uc.mem_write(BATTERY, struct.pack("<4I", 1, 0, 3900, 12))
        self.media_state = 1  # the OS getter's codes: 1 playing, 2 paused, 0 stopped
        self.media_calls = []
        for address in (A_SMP_GET, A_SMP_STATE, A_TASK_GET, A_TASK_PAUSE, A_TASK_RESUME, A_TASK_STOP):
            uc.mem_write(address, b"\x70\x47")
            uc.hook_add(UC_HOOK_CODE, self.os_call, begin=address, end=address)
        uc.hook_add(UC_HOOK_BLOCK, self.block)
        uc.hook_add(UC_HOOK_MEM_READ, self.timer, begin=TIMER, end=TIMER + 0xFFF)
        shared = struct.pack("<2I4iI4BI", HB_SHARED_MAGIC, 1, HB_TOUCH_MB_MAGIC, 0, 0, 0, 0, 0, 0, 0, 0, TRACE_FN | 1)
        uc.mem_write(SHARED, shared)

    def word(self, address, value=None):
        if value is not None:
            self.uc.mem_write(address, struct.pack("<I", value & 0xFFFFFFFF))
        return struct.unpack("<I", self.uc.mem_read(address, 4))[0]

    def block(self, uc, address, size, _):
        # Thumb-2 blocks: about 3 bytes per instruction on average.
        self.insns += max(1, size // 3)
        if self.prof is not None:
            self.prof[address] = self.prof.get(address, 0) + max(1, size // 3)
            helper = self.watch.get(address & ~1)
            if helper:
                key = (helper, uc.reg_read(UC_ARM_REG_LR) & ~1)
                self.callers[key] = self.callers.get(key, 0) + 1
        sp = uc.reg_read(UC_ARM_REG_SP)
        if not STACK_LIMIT <= sp <= STACK_TOP:
            return  # the app's private stack (inside its heap block), not the OS task stack
        if sp < self.min_sp:
            self.min_sp = sp
        if sp < self.min_sp_call:
            self.min_sp_call = sp

    def timer(self, uc, access, address, size, value, _):
        # One heartbeat period per frame call, plus 1 us per 400 instructions
        # (a coarse stand-in for the A8 clock).
        now = self.calls * self.heartbeat_us + self.insns // 400
        uc.mem_write(TIMER + 0x84, struct.pack("<I", now & 0xFFFFFFFF))

    def os_call(self, uc, address, size, _):
        r0 = uc.reg_read(UC_ARM_REG_R0)
        if address == A_RTC_INSTANCE:
            uc.reg_write(UC_ARM_REG_R0, RTC_OBJ)
        elif address in (A_SMP_GET, A_TASK_GET):
            uc.reg_write(UC_ARM_REG_R0, MEDIA_OBJ)
        elif address == A_SMP_STATE:
            uc.reg_write(UC_ARM_REG_R0, self.media_state)
        elif address in (A_TASK_PAUSE, A_TASK_RESUME, A_TASK_STOP):
            self.media_state = {A_TASK_PAUSE: 2, A_TASK_RESUME: 1, A_TASK_STOP: 0}[address]
            self.media_calls.append({A_TASK_PAUSE: "pause", A_TASK_RESUME: "resume", A_TASK_STOP: "stop"}[address])
            print(f"media: {self.media_calls[-1]} (self {r0:#x}, r1 {uc.reg_read(UC_ARM_REG_R1):#x})", flush=True)
        elif address == RTC_GET:
            # hb_sdk.h layout: seconds, minutes, hours, day, month, pad, year, weekday
            uc.mem_write(r0, struct.pack("<6BHB", 0, 41, 9, 1, 11, 0, 2026, 0))
        elif address == A_HEAP_ALLOC:
            uc.reg_write(UC_ARM_REG_R0, self.heap.alloc(r0))
        elif address == A_OP_DELETE:
            self.heap.free(r0)
        elif address == A_HEAP_FREESIZE:
            uc.reg_write(UC_ARM_REG_R0, self.heap.free_total())
        elif address == A_HEAP_LARGEST:
            uc.reg_write(UC_ARM_REG_R0, self.heap.largest())
        elif address == A_FS_EXISTS:
            path = self.cstr(r0)
            uc.reg_write(UC_ARM_REG_R0, 1 if path in self.files or path in self.data or path in self.dirs else 0)
        elif address == A_FS_GETSIZE:
            path = self.cstr(r0)
            body = self.served.get(path, self.data.get(path))
            if body is not None:
                self.uc.mem_write(uc.reg_read(UC_ARM_REG_R1), struct.pack("<Q", len(body)))
                uc.reg_write(UC_ARM_REG_R0, 0)
            else:
                uc.reg_write(UC_ARM_REG_R0, 1)
        elif address == A_FS_GETATTR:
            path = self.cstr(r0)
            if path in self.dirs or path in self.data or path in self.served:
                uc.mem_write(uc.reg_read(UC_ARM_REG_R1), bytes([1 if path in self.dirs else 0]))
                uc.reg_write(UC_ARM_REG_R0, 0)
            else:
                uc.reg_write(UC_ARM_REG_R0, 1)
        elif address == A_FOBJ_OPEN:
            path, mode = self.cstr(uc.reg_read(UC_ARM_REG_R1)), uc.reg_read(UC_ARM_REG_R2)
            if mode != 1:
                self.writable(path)
                if self.parent(path) not in self.dirs or path in self.dirs:
                    self.open[r0] = [None, 0]   # not ready: no folder, or a folder by that name
                    return
                self.data[path] = bytearray()
                self.writes += 1
            self.open[r0] = [path, 0]
        elif address == A_FOBJ_READY:
            entry = self.open.get(r0)
            uc.reg_write(UC_ARM_REG_R0, 1 if entry and (entry[0] in self.served or entry[0] in self.data) else 0)
        elif address == A_FOBJ_WRITE:
            path, pos = self.open[r0]
            n, buf, put = uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2), uc.reg_read(UC_ARM_REG_R3)
            body = self.data[path]
            body[pos:pos + n] = bytes(uc.mem_read(buf, n))
            self.open[r0][1] = pos + n
            self.bytes_written += n
            uc.mem_write(put, struct.pack("<I", n))
            uc.reg_write(UC_ARM_REG_R0, 0)
        elif address == A_FOBJ_TRUNC:
            path = self.open[r0][0]
            del self.data[path][uc.reg_read(UC_ARM_REG_R1):]
            uc.reg_write(UC_ARM_REG_R0, 0)
        elif address == A_FOBJ_SYNC:
            uc.reg_write(UC_ARM_REG_R0, 0)
        elif address == A_FS_REMOVE:
            path = self.cstr(r0)
            if path in self.data:
                self.writable(path)
                del self.data[path]
                uc.reg_write(UC_ARM_REG_R0, 0)
            else:
                uc.reg_write(UC_ARM_REG_R0, 1)
        elif address == A_FS_MKDIR:
            path = self.cstr(r0).rstrip("/")
            if path in self.dirs:
                uc.reg_write(UC_ARM_REG_R0, 0xD)
            elif self.parent(path) in self.dirs and path not in self.data and path not in self.served:
                self.writable(path + "/")
                self.dirs.add(path)
                uc.reg_write(UC_ARM_REG_R0, 0)
            else:
                uc.reg_write(UC_ARM_REG_R0, 1)
        elif address == A_FS_RMDIR:
            path = self.cstr(r0).rstrip("/")
            if path in self.dirs and not self.children(path):
                self.writable(path + "/")
                self.dirs.discard(path)
                uc.reg_write(UC_ARM_REG_R0, 0)
            else:
                uc.reg_write(UC_ARM_REG_R0, 1)
        elif address == A_DIR_CTOR:
            self.lists[r0] = self.children(self.cstr(uc.reg_read(UC_ARM_REG_R1)).rstrip("/"))
        elif address == A_DIR_NEXT:
            pending = self.lists.get(r0) or []
            if not pending:
                uc.reg_write(UC_ARM_REG_R0, 0)
                return
            name, is_dir = pending.pop(0)
            slot = NAMES + (self.name_slot % 64) * 256
            self.name_slot += 1
            uc.mem_write(slot, name.encode("latin-1")[:255] + b"\0")
            self.word(uc.reg_read(UC_ARM_REG_R1), slot)
            uc.mem_write(uc.reg_read(UC_ARM_REG_R2), bytes([1 if is_dir else 0]))
            uc.reg_write(UC_ARM_REG_R0, 1)
        elif address == A_DIR_DTOR:
            self.lists.pop(r0, None)
        elif address == A_PATH_CTOR:
            self.word(r0, 0)
        elif address == A_PATH_ASCHAR:
            uc.reg_write(UC_ARM_REG_R0, self.word(r0))
        elif address == A_PATH_DTOR:
            pass
        elif address == A_FOBJ_READ:
            path, pos = self.open[r0]
            n, buf, got = uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2), uc.reg_read(UC_ARM_REG_R3)
            chunk = bytes(self.served[path][pos:pos + n] if path in self.served else self.data[path][pos:pos + n])
            uc.mem_write(buf, chunk)
            uc.mem_write(got, struct.pack("<Q", len(chunk)))
            self.open[r0][1] = pos + len(chunk)
            self.bytes_read += len(chunk)
            uc.reg_write(UC_ARM_REG_R0, 0)
        elif address == A_FOBJ_CLOSE:
            self.open.pop(r0, None)
        elif address == TRACE_FN:
            tag = bytes(uc.mem_read(r0, 8)).split(b"\0")[0].decode("latin-1")
            v1, v2 = uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2)
            self.trace.append((tag, v1, v2))

    @staticmethod
    def parent(path):
        return path.rstrip("/").rsplit("/", 1)[0] or "/"

    def add_parents(self, path):
        p = self.parent(path)
        while p not in self.dirs:
            self.dirs.add(p)
            if p == "/":
                break
            p = self.parent(p)

    def children(self, path):
        names = {}
        for p in list(self.dirs) + list(self.data) + list(self.files):
            if p != path and self.parent(p) == path:
                names[p.rsplit("/", 1)[1]] = p in self.dirs
        return sorted(names.items())

    @staticmethod
    def writable(path):
        if not path.startswith(WRITABLE):
            raise RuntimeError(f"the app wrote outside {WRITABLE}: {path}")

    def cstr(self, address):
        return bytes(self.uc.mem_read(address, 128)).split(b"\0")[0].decode("latin-1")

    def call(self, fn, args, budget=4_000_000_000):
        uc = self.uc
        self.min_sp_call = STACK_TOP
        uc.reg_write(UC_ARM_REG_SP, STACK_TOP - 16)
        self.word(STACK_TOP - 16, args[4] if len(args) > 4 else 0)
        for reg, value in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), args):
            uc.reg_write(reg, value)
        uc.reg_write(UC_ARM_REG_LR, STOP | 1)
        try:
            uc.emu_start(fn | 1, STOP, count=budget)
        except Exception:
            print(f"fault: pc {uc.reg_read(UC_ARM_REG_PC):#x} lr {uc.reg_read(UC_ARM_REG_LR):#x} "
                  f"r0 {uc.reg_read(UC_ARM_REG_R0):#x} r1 {uc.reg_read(UC_ARM_REG_R1):#x} "
                  f"sp {uc.reg_read(UC_ARM_REG_SP):#x} (image at {BASE:#x})", flush=True)
            raise
        pc = uc.reg_read(UC_ARM_REG_PC)
        if pc != STOP:
            raise RuntimeError(f"stopped at {pc:#x}, not the return address")
        return uc.reg_read(UC_ARM_REG_R0)

    def accel(self, x, y, z):
        self.uc.mem_write(ACCEL, struct.pack("<3i", x, y, z))

    def touch(self, x, y, down, fingers=()):
        """The resident's point in the mailbox, and every finger (the point
        first) in the OS's per-finger lists. `touch_model` says how the
        lists behave (--touch-model):
          ideal   one sample in each finger's list every frame (status 0);
          sparse  a finger's list has a sample only when it lands (status 0),
                  moves (status 2) or lifts (status 1), else it is empty:
                  the OS's touch task took it;
          sparse1 the same with the SDK header's statuses: moves 1, lifts 2
                  (an earlier build then dropped a second finger after one frame,
                  as on the iPod);
          shared  every finger's samples in the first list, by OS touch id."""
        self.uc.mem_write(SHARED + 8, struct.pack("<4i", HB_TOUCH_MB_MAGIC, x, y, 1 if down else 0))
        points = ([(x, y)] + list(fingers)) if down else []
        model = getattr(self, "touch_model", "ideal")
        prev = getattr(self, "touch_prev", [])
        self.touch_prev = points
        self.touch_flip = getattr(self, "touch_flip", 0) ^ 1
        samples = [[] for _ in range(8)]   # per list: (id, status, x, y)
        for slot in range(8):
            now = points[slot] if slot < len(points) else None
            was = prev[slot] if slot < len(prev) else None
            if model == "ideal":
                if now:
                    samples[slot].append((slot, 0, *now))
                continue
            if now and not was:
                sample = (slot, 0, *now)
            elif now and now != was:
                sample = (slot, 1 if model == "sparse1" else 2, *now)
            elif was and not now:
                sample = (slot, 2 if model == "sparse1" else 1, *was)
            else:
                sample = None
            if sample:
                samples[0 if model == "shared" else slot].append(sample)
        for slot in range(8):
            head = TOUCH_LISTS + slot * 0x18
            if not samples[slot]:
                self.uc.mem_write(head, struct.pack("<6I", 0, 0, 0, 0, 0, 0))
                continue
            end = TOUCH_NODES + slot * 0x100
            nodes = [end + 0x10 + (i * 2 + self.touch_flip) * 0x18 for i in range(len(samples[slot]))]
            self.uc.mem_write(head, struct.pack("<6I", 0, 0, 0, 0, end, len(nodes)))
            ring = [end] + nodes
            for i, n in enumerate(ring):
                nxt, prv = ring[(i + 1) % len(ring)], ring[i - 1]
                self.uc.mem_write(n, struct.pack("<2I", nxt, prv))
            for n, (fid, st, fx, fy) in zip(nodes, samples[slot]):
                # next, prev, then the value: id, status, x, y
                self.uc.mem_write(n + 8, struct.pack("<iB3xii", fid, st, fx, fy))

    def png(self, path):
        raw = bytes(self.uc.mem_read(FB, W * H * 4))
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgra",
                        "-s", f"{W}x{H}", "-i", "-", "-frames:v", "1", str(path)], input=raw, check=True)


def profile(prof, elf, frames, top=25):
    """Estimated instructions per function (Rust names demangled loosely)."""
    from bisect import bisect_right
    from elftools.elf.elffile import ELFFile

    syms = []
    with open(elf, "rb") as f:
        tab = ELFFile(f).get_section_by_name(".symtab")
        for sym in tab.iter_symbols():
            if sym["st_info"]["type"] == "STT_FUNC" and sym["st_size"]:
                syms.append((sym["st_value"] & ~1, sym["st_size"], sym.name))
    syms.sort()
    starts = [s[0] for s in syms]
    totals = {}
    for address, n in prof.items():
        off = (address & ~1) - BASE
        i = bisect_right(starts, off) - 1
        name = syms[i][2] if i >= 0 and off < syms[i][0] + syms[i][1] else "?"
        totals[name] = totals.get(name, 0) + n
    whole = sum(totals.values()) or 1
    print(f"profile: {whole:,} instructions (estimated) over {frames} frames")
    for name, n in sorted(totals.items(), key=lambda kv: -kv[1])[:top]:
        print(f"  {100 * n / whole:5.1f}%  {n:>13,}  {short_name(name)}")


def elf_functions(elf):
    from elftools.elf.elffile import ELFFile
    syms = []
    with open(elf, "rb") as f:
        tab = ELFFile(f).get_section_by_name(".symtab")
        for sym in tab.iter_symbols():
            if sym["st_info"]["type"] == "STT_FUNC" and sym["st_size"]:
                syms.append((sym["st_value"] & ~1, sym["st_size"], sym.name))
    syms.sort()
    return syms


def watch_helpers(elf, pattern):
    """Entry addresses (as the emulator sees them) of the functions whose names match `pattern`."""
    import re
    rx = re.compile(pattern)
    return {BASE + start: short_name(name) for start, _, name in elf_functions(elf) if rx.search(name)}


def report_callers(callers, elf, top=30):
    """Calls into the watched helpers, by calling function."""
    from bisect import bisect_right
    syms = elf_functions(elf)
    starts = [s[0] for s in syms]
    totals, per_helper = {}, {}
    for (helper, lr), n in callers.items():
        off = lr - BASE
        i = bisect_right(starts, off) - 1
        name = short_name(syms[i][2]) if i >= 0 and off < syms[i][0] + syms[i][1] else "?"
        totals[name] = totals.get(name, 0) + n
        per_helper.setdefault(name, {})
        per_helper[name][helper] = per_helper[name].get(helper, 0) + n
    whole = sum(totals.values()) or 1
    print(f"callers: {whole:,} calls into the watched helpers")
    for name, n in sorted(totals.items(), key=lambda kv: -kv[1])[:top]:
        parts = ", ".join(f"{h} {c:,}" for h, c in sorted(per_helper[name].items(), key=lambda kv: -kv[1])[:4])
        print(f"  {100 * n / whole:5.1f}%  {n:>10,}  {name}  ({parts})")
    # Return addresses as ELF addresses, for addr2line on the inlined sites.
    sites = sorted(callers.items(), key=lambda kv: -kv[1])[:top]
    print("call sites (ELF address of the return, helper, calls):")
    for (helper, lr), n in sites:
        print(f"  0x{(lr - BASE) & ~1:08x}  {helper}  {n:,}")


def short_name(name):
    """A readable tail of a v0 or legacy mangled Rust symbol."""
    import re
    parts = re.findall(r"\d+([A-Za-z_][A-Za-z0-9_]*)", name) if name.startswith(("_R", "_ZN")) else []
    words = [p for p in parts if not re.match(r"^[0-9a-f]{16}$|^h[0-9a-f]{16}$", p)]
    return "::".join(words[-3:]) if words else name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("app")
    ap.add_argument("outdir")
    ap.add_argument("script", nargs="?")
    ap.add_argument("--largest", type=int, default=3 * 1024 * 1024,
                    help="largest free OS heap block after the app arena is placed (default 3 MiB)")
    ap.add_argument("--os-blocks", default=None,
                    help="model the OS heap as these free blocks in MiB, e.g. 4.0,1.5,1.0,0.5 "
                         "(Probe 7.0 free / 4.0 largest); the image arena is placed first")
    ap.add_argument("--file", action="append", default=[],
                    help="a path hb_fs_exists reports present, e.g. /Apps/Data/PocketJS/no_private_stack.txt")
    ap.add_argument("--serve", action="append", default=[],
                    help="DEVICE_PATH=HOST_FILE: a read-only file, e.g. /Apps/Data/PocketJS/cafe.pocket=cafe.pocket")
    ap.add_argument("--touch-model", default="ideal", choices=("ideal", "sparse", "sparse1", "shared"),
                    help="how the OS's per-finger lists behave (see Emu.touch)")
    ap.add_argument("--fs-dir", default=None,
                    help="host folder holding /Apps/Data/PocketJS/ data.fs files: loaded first, saved at the end")
    ap.add_argument("--profile-from", type=int, default=None,
                    help="count instructions per function from this frame on (needs --elf)")
    ap.add_argument("--elf", default=None, help="the app's .reloc.elf, for --profile-from symbols")
    ap.add_argument("--callers", default=None, metavar="REGEX",
                    help="with --profile-from: count calls into the functions matching REGEX by caller "
                         "(for example soft-float helpers: '__aeabi_f|sf3|__aeabi_[if]2')")
    ap.add_argument("--heartbeat-us", type=int, default=33334,
                    help="clock advance per heartbeat (default one 30 Hz tick; 16667 tests the pacing)")
    ap.add_argument("--os-misalign", type=int, default=0, choices=(0, 4),
                    help="return OS heap blocks this many bytes past an 8-byte boundary (the "
                         "device's allocator only promises 4; JS QuickJS's createNode failed on it)")
    ap.add_argument("--no-home", action="store_true",
                    help="do not serve home.pocket, so JS QuickJS opens its own list (the older scripts)")
    a = ap.parse_args()
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    script = Path(a.script).read_text() if a.script else "30\nshot 00-first\n"
    regions = [int(float(v) * 1024 * 1024) for v in a.os_blocks.split(",")] if a.os_blocks else None
    served = {}
    for item in a.serve:
        device, host = item.split("=", 1)
        if a.no_home and device.rsplit("/", 1)[-1] in ("home.pocket", "home.qjsbc"):
            continue
        served[device] = Path(host).read_bytes()
    data = {}
    if a.fs_dir and Path(a.fs_dir).is_dir():
        for f in sorted(Path(a.fs_dir).rglob("*")):
            if f.is_file():
                data[WRITABLE + f.relative_to(a.fs_dir).as_posix()] = bytearray(f.read_bytes())
    emu = Emu(a.app, a.largest, a.file, a.heartbeat_us, regions, served, data, a.os_misalign)
    emu.touch_model = a.touch_model
    if a.fs_dir and Path(a.fs_dir).is_dir():
        emu.dirs.update(WRITABLE + d.relative_to(a.fs_dir).as_posix() for d in Path(a.fs_dir).rglob("*") if d.is_dir())

    if regions:
        print(f"OS heap model: blocks {a.os_blocks} MiB; image arena {emu.image_span:,} bytes placed; "
              f"then free {emu.heap.free_total():,}, largest {emu.heap.largest():,}")
    frame_fn = emu.call(BASE + emu.entry, [0, FB, W, H, SHARED])
    print(f"stack: payload_entry {STACK_TOP - 16 - emu.min_sp_call:,} bytes")
    stacks = []
    if not frame_fn:
        sys.exit("payload_entry refused the launch")
    frames, per_frame = 0, []
    for line in script.splitlines():
        words = line.split("#")[0].split()
        if not words:
            continue
        if words[0] == "shot":
            emu.png(out / f"{words[1]}.png")
            print(f"{words[1]}: frame {frames}")
            continue
        if words[0] == "accel":
            emu.accel(*(int(w) for w in words[1:4]))
            continue
        if words[0] == "sample":
            continue  # the preview switches apps directly; the emulator taps through the menu

        # `N x y [x2 y2 ...]` holds those fingers N frames (the first is the
        # resident's point); `... to X Y [X2 Y2 ...]` moves them there evenly.
        count = int(words[0])
        rest = words[1:]
        target = None
        if "to" in rest:
            i = rest.index("to")
            rest, target = rest[:i], [int(w) for w in rest[i + 1:]]
        start = [int(w) for w in rest]
        down = len(start) >= 2
        for step in range(count):
            if down:
                pts = start
                if target:
                    t = (step + 1) / count
                    pts = [round(a + (b - a) * t) for a, b in zip(start, target)]
                pairs = [(pts[i], pts[i + 1]) for i in range(0, len(pts) - 1, 2)]
                emu.touch(pairs[0][0], pairs[0][1], True, pairs[1:])
            else:
                emu.touch(0, 0, False)
            if a.profile_from is not None and frames == a.profile_from:
                emu.prof = {}
                if a.callers and a.elf:
                    emu.watch = watch_helpers(a.elf, a.callers)
            before = emu.insns
            emu.calls += 1
            emu.call(frame_fn, [])
            per_frame.append(emu.insns - before)
            stacks.append(STACK_TOP - 16 - emu.min_sp_call)
            frames += 1
    for tag, v1, v2 in emu.trace:
        print(f"trace {tag:8} {v1:#010x} {v2:#010x}  ({v1}, {v2})")
    # One line per frame (estimated instructions) so a profile can drop the
    # menu frames and look at one app.
    (out / "frames.csv").write_text("frame,instructions\n" + "".join(
        f"{i},{n}\n" for i, n in enumerate(per_frame)), encoding="utf-8")
    boot = per_frame[0] if per_frame else 0
    rest = sorted(per_frame[1:]) or [0]
    print(f"frames {frames}; instructions (estimated) boot frame {boot:,}, "
          f"median {rest[len(rest) // 2]:,}, max {rest[-1]:,}")
    print(f"stack: deepest {STACK_TOP - 16 - emu.min_sp:,} bytes below the entry; boot frame {stacks[0] if stacks else 0:,}, "
          f"later frames max {max(stacks[1:] or [0]):,}")
    deep = sorted(range(len(stacks)), key=lambda i: -stacks[i])[:3]
    print("stack: deepest frames " + ", ".join(f"#{i} {stacks[i]:,}" for i in deep))
    if served:
        print(f"files: {emu.bytes_read:,} bytes read from {', '.join(f'{k} ({len(v):,})' for k, v in served.items())}")
    if emu.prof:
        profile(emu.prof, a.elf, frames - a.profile_from)
        if emu.callers:
            report_callers(emu.callers, a.elf)
    if emu.writes or emu.data:
        print(f"data.fs: {emu.writes} files written ({emu.bytes_written:,} bytes); now " +
              (", ".join(f"{k[len(WRITABLE):]} ({len(v)})" for k, v in sorted(emu.data.items())) or "none"))
    if a.fs_dir:
        import shutil
        root = Path(a.fs_dir)
        if root.is_dir():
            shutil.rmtree(root)
        root.mkdir(parents=True)
        for d in sorted(emu.dirs):
            if d.startswith(WRITABLE):
                (root / d[len(WRITABLE):]).mkdir(parents=True, exist_ok=True)
        for k, v in emu.data.items():
            (root / k[len(WRITABLE):]).parent.mkdir(parents=True, exist_ok=True)
            (root / k[len(WRITABLE):]).write_bytes(bytes(v))
    print(f"OS heap: blocks held {len(emu.heap.blocks)} ({sum(emu.heap.blocks.values()):,} bytes)")


if __name__ == "__main__":
    main()
