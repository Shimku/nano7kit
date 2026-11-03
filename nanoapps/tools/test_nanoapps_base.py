#!/usr/bin/env python3
"""nano7kit nanoapps-base: Unicorn tests for the NanoApps 1.0 + heavy-app resident.

Covers: .nanoapp catalog, legacy pack coexistence, heavy (>1.2 MB) image load,
unload freeing arenas, largest-block refuse, /Apps/Data path preference.
"""
from pathlib import Path
import struct
import unittest

from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3

from app_install import HEADER, identity, pack
from package_app import MANIFEST, MAGIC, VERSION, ABI, FIRMWARE
from test_untether_na import ChainMachine, catalog_bundle, load_hrl, RESIDENT
from wrap_resident_slot import fnv1a32

ROOT_NA = Path(__file__).resolve().parents[1]

EXE_SMALL = struct.pack('<6I', 0x314c5248, 0, 4, 4, 0, 4) + b'\x70\x47\0\0'
ICON = bytes(112 * 112 * 4 + 32)


def make_hrl(image_size, span=None, entry=0):
    """Minimal Thumb HRL1: `bx lr` at entry, zero relocs, 16-byte align."""
    if span is None:
        span = max(image_size, 16)
    align = 16
    code = bytearray(image_size)
    code[0:2] = b'\x70\x47'  # bx lr
    hdr = struct.pack('<6I', 0x314c5248, entry, image_size, span, 0, align)
    return hdr + bytes(code)


def app_files(sbid='hb.paint', app_id=0x10001234, folder=None, exe=None):
    folder = folder or (sbid[3:].title() + '.nanoapp')
    exe = exe or EXE_SMALL
    b = catalog_bundle(sbid, sbid, app_id)
    return {
        f'/Apps/{folder}/manifest.napp': MANIFEST.pack(
            MAGIC, VERSION, ABI, FIRMWARE, 0, len(b), len(exe),
            fnv1a32(exe), fnv1a32(ICON)) + b,
        f'/Apps/{folder}/app.hbapp': exe,
        f'/Apps/{folder}/icon.bin': ICON,
    }


class NanoappsBaseTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(RESIDENT.with_suffix('.hbapp').exists(), RESIDENT)
        self.assertTrue(RESIDENT.with_suffix('.reloc.elf').exists())

    def run_catalog(self, files, legacy=None, resident_version=100):
        m = ChainMachine(real_resident=True)
        m.files.pop('/Library/AllApps.pack', None)
        if legacy is not None:
            # hb_app_catalog_load reads whatever path it is given. This test
            # passes the NanoApps path; the resident's own path choice (the
            # /Apps packs on a nano7kit iPod) is covered by the boot_device tests.
            packed = pack(legacy)
            m.files['/Library/AllApps.pack'] = packed
            m.files['/Apps/AllApps.pack'] = packed
            for b in legacy:
                h = HEADER.unpack_from(b)
                name = b[h[11]:].split(b'\0', 1)[0].decode()
                m.files[f'/Apps/Executables/{name}.hbapp'] = EXE_SMALL
                m.files[f'/Apps/Icons/{name}.bin'] = ICON
        m.files.update(files)
        _, span, _ = load_hrl(m.uc, m.res_blob, m.res_base)
        m.heap_next = (m.res_base + span + 0xfff) & ~0xfff
        address = 0x09d00000
        m.uc.mem_write(address, b'/Library/AllApps.pack\0')
        result_address = m.call(m.res_addr('hb_app_catalog_load'),
                                (address, resident_version, 0))
        result = []
        if result_address:
            from app_install import bundles
            count = m.word(result_address + 8)
            end = 12 + count * 4
            for i in range(count):
                off = m.word(result_address + 12 + 4 * i)
                end = max(end, off + m.word(result_address + off + 44))
            result = bundles(bytes(m.uc.mem_read(result_address, end)))
        return result, m

    def test_nanoapp_catalog_loads_paint_like_app(self):
        result, _ = self.run_catalog(app_files())
        self.assertEqual([identity(b) for b in result], ['hb.paint'])

    def test_nanoapp_and_legacy_pack_coexist(self):
        legacy = [catalog_bundle('hb.blocks', 'Blocks', 0x0dad8101)]
        files = app_files('hb.paint', 0x10001234)
        result, _ = self.run_catalog(files, legacy)
        ids = [identity(b) for b in result]
        self.assertIn('hb.paint', ids)
        self.assertIn('hb.blocks', ids)

    # ---- rc2: the resident must find the nano7kit legacy pack on its own ----
    # rc1 passed '/Library/AllApps.pack' to the catalog, a NanoApps 1.0 path that
    # does not exist on a nano7kit iPod. Home then listed only the .nanoapp
    # folders. These tests boot the real resident against a nano7kit device
    # tree (no /Library at all) and read the catalog it registers.

    LEGACY_13 = ('Probe', 'Blocks', 'Calculator', 'Mario 64', 'Notes', 'Chess',
                 'Temple Run', 'Fruit Ninja', 'PvZ Portable', 'JS Hero',
                 'JS Samples', 'JS QuickJS', 'Free RAM')
    SBIDS = {'Probe': 'hb.probe', 'Blocks': 'hb.blocks', 'Calculator': 'hb.calculator',
             'Mario 64': 'hb.sm64', 'Notes': 'hb.notes', 'Chess': 'hb.chess',
             'Temple Run': 'hb.templerun', 'Fruit Ninja': 'hb.fruitninja',
             'PvZ Portable': 'hb.pvz_portable', 'JS Hero': 'hb.pjhero',
             'JS Samples': 'hb.pjsamples', 'JS QuickJS': 'hb.pjquickjs',
             'Free RAM': 'hb.freeram'}

    def legacy_files(self, names, pack_path):
        items = [catalog_bundle(self.SBIDS.get(n, 'hb.x%d' % i), n, 0x0dad8200 + i)
                 for i, n in enumerate(names)]
        files = {pack_path: pack(items)}
        for n in names:
            files[f'/Apps/Executables/{n}.hbapp'] = EXE_SMALL
            files[f'/Apps/Icons/{n}.bin'] = ICON
        return files

    def boot_device(self, files):
        # The harness boots through the NanoApps test bootstrap, which wants
        # /Library/resident.hbapp and a 4-byte /Library/AllApps.pack stub before
        # it hands over. Neither is a real catalog; the stub has no apps.
        m = ChainMachine(real_resident=True)
        self.assertEqual(m.files['/Library/AllApps.pack'], b'HBPK')
        m.files.update(files)
        m.arm(); m.dispatch()
        self.assertIsNotNone(m.registered_catalog, 'resident registered no catalog')
        from app_install import bundles
        got = bundles(m.registered_catalog)
        names = []
        for b in got:
            h = HEADER.unpack_from(b)
            names.append(b[h[11]:].split(b'\0', 1)[0].decode())
        return [identity(b) for b in got], names, m

    def test_device_tree_lists_nanoapps_and_slot_b_pack(self):
        """The rc1 hardware bug: Paint + Free RAM .nanoapp hid all 13 pack apps."""
        files = self.legacy_files(self.LEGACY_13, '/Apps/AllApps-B.pack')
        files['/Apps/Startup/resident-B.active'] = b''
        files.update(app_files('hb.paint', 0x10001234, 'Paint.nanoapp'))
        files.update(app_files('hb.freeram', 0x102ba6a5, 'Free RAM.nanoapp'))
        ids, names, _ = self.boot_device(files)
        for n in self.LEGACY_13:
            self.assertIn(self.SBIDS[n], ids, n)
        self.assertIn('hb.paint', ids)
        self.assertEqual(len(ids), 14, ids)  # 13 pack + Paint, Free RAM once

    def test_free_ram_dedupes_to_the_nanoapp(self):
        files = self.legacy_files(('Probe', 'Free RAM'), '/Apps/AllApps-B.pack')
        files.update(app_files('hb.freeram', 0x102ba6a5, 'Free RAM.nanoapp'))
        ids, names, _ = self.boot_device(files)
        self.assertEqual(ids.count('hb.freeram'), 1, ids)
        # A .nanoapp manifest names itself by SBID; the pack entry says 'Free RAM'.
        self.assertEqual(names[ids.index('hb.freeram')], 'hb.freeram')
        self.assertNotIn('Free RAM', names)
        self.assertIn('hb.probe', ids)

    def test_slot_b_pack_preferred_over_slot_a(self):
        files = self.legacy_files(('Probe',), '/Apps/AllApps.pack')
        files.update(self.legacy_files(('Blocks', 'Mario 64'), '/Apps/AllApps-B.pack'))
        ids, _, _ = self.boot_device(files)
        self.assertEqual(sorted(ids), ['hb.blocks', 'hb.sm64'])

    def test_slot_a_pack_used_when_no_slot_b_pack(self):
        files = self.legacy_files(('Probe', 'Blocks'), '/Apps/AllApps.pack')
        files.update(app_files('hb.paint', 0x10001234, 'Paint.nanoapp'))
        ids, _, _ = self.boot_device(files)
        self.assertEqual(sorted(ids), ['hb.blocks', 'hb.paint', 'hb.probe'])

    def test_nanoapps_layout_pack_still_read(self):
        files = self.legacy_files(('Probe',), '/Library/AllApps.pack')
        files.update(app_files('hb.paint', 0x10001234, 'Paint.nanoapp'))
        ids, _, _ = self.boot_device(files)
        self.assertEqual(sorted(ids), ['hb.paint', 'hb.probe'])

    def test_no_pack_lists_nanoapps_only(self):
        ids, _, _ = self.boot_device(app_files('hb.paint', 0x10001234, 'Paint.nanoapp'))
        self.assertEqual(ids, ['hb.paint'])

    def test_resident_has_no_largest_block_pre_gate(self):
        # rc3: the launcher-level "largest block >= image + 256 KB" pre-gate is
        # gone; only the loader's own check remains.
        m = ChainMachine(real_resident=True)
        self.assertNotIn('hb_heavy_block_ok', m.res_syms)

    def test_heavy_verified_load_over_1_2_mb(self):
        # 1.25 MB image, span matches – exercises chunked read path (>512 KB).
        image_size = 1_250_000
        exe = make_hrl(image_size, span=image_size)
        self.assertGreater(len(exe), 1_200_000)
        m = ChainMachine(real_resident=True)
        m.files['/Apps/Heavy.nanoapp/app.hbapp'] = exe
        m.heap_largest_value = 8 * 1024 * 1024
        _, span, _ = load_hrl(m.uc, m.res_blob, m.res_base)
        m.heap_next = (m.res_base + span + 0xfff) & ~0xfff
        path = 0x09d00000
        m.uc.mem_write(path, b'/Apps/Heavy.nanoapp/app.hbapp\0')
        out_arena = 0x09d00100
        out_len = 0x09d00104
        m.word(out_arena, 0)
        m.word(out_len, 0)
        # AAPCS: r0=path, r1=out_arena*, r2=out_len*, r3=hash
        entry = m.call(m.res_addr('hb_reloc_load_file_verified_chunked'),
                       (path, out_arena, out_len, fnv1a32(exe)))
        self.assertNotEqual(entry, 0, 'heavy image must load')
        self.assertNotEqual(m.word(out_arena), 0)
        self.assertEqual(m.word(out_len), len(exe))
        # Arena was allocated (tracked).
        self.assertTrue(m.live_allocs)

    def test_heavy_load_refuses_when_largest_block_too_small(self):
        exe = make_hrl(1_250_000, span=1_250_000)
        m = ChainMachine(real_resident=True)
        m.files['/Apps/Heavy.nanoapp/app.hbapp'] = exe
        m.heap_largest_value = 200_000  # far too small
        _, span, _ = load_hrl(m.uc, m.res_blob, m.res_base)
        m.heap_next = (m.res_base + span + 0xfff) & ~0xfff
        path = 0x09d00000
        m.uc.mem_write(path, b'/Apps/Heavy.nanoapp/app.hbapp\0')
        out_arena = 0x09d00100
        out_len = 0x09d00104
        m.word(out_arena, 0)
        entry = m.call(m.res_addr('hb_reloc_load_file_verified_chunked'),
                       (path, out_arena, out_len, fnv1a32(exe)))
        self.assertEqual(entry, 0)
        self.assertEqual(m.word(out_arena), 0)

    def test_data_dir_prefers_apps_data(self):
        m = ChainMachine(real_resident=True)
        m.files['/Apps/Data/PocketJS'] = b''  # exists as dir via is_dir->exists hook
        m.files['/Data/PocketJS'] = b''
        _, span, _ = load_hrl(m.uc, m.res_blob, m.res_base)
        m.heap_next = (m.res_base + span + 0xfff) & ~0xfff
        out = 0x09d00200
        leaf = 0x09d00300
        m.uc.mem_write(leaf, b'PocketJS\0')
        m.uc.mem_write(out, bytes(96))
        ok = m.call(m.res_addr('hb_data_dir'), (out, 96, leaf))
        self.assertEqual(ok, 1)
        got = bytes(m.uc.mem_read(out, 96)).split(b'\0', 1)[0].decode()
        self.assertEqual(got, '/Apps/Data/PocketJS')

    def test_unload_frees_other_app_arenas(self):
        m = ChainMachine(real_resident=True)
        _, span, _ = load_hrl(m.uc, m.res_blob, m.res_base)
        m.heap_next = (m.res_base + span + 0xfff) & ~0xfff
        # Seed two fake arenas in the resident's s_app_arena[] via symbols.
        syms = m.res_syms
        self.assertIn('s_app_arena', syms)
        self.assertIn('g_launch_count', syms)
        arena0 = m.heap_next
        m.live_allocs[arena0] = 0x1000
        m.heap_next += 0x1000
        arena1 = m.heap_next
        m.live_allocs[arena1] = 0x1000
        m.heap_next += 0x1000
        base = m.res_base
        m.word(base + (syms['g_launch_count'] & ~1), 2)
        # s_app_arena is void*[HB_MAX_APPS]; write two pointers
        aa = base + (syms['s_app_arena'] & ~1)
        m.word(aa, arena0)
        m.word(aa + 4, arena1)
        # s_sled must be non-null or unload keeps retain-all rule
        if 's_sled' in syms:
            sled = m.heap_next
            m.live_allocs[sled] = 64
            m.heap_next += 64
            m.word(base + (syms['s_sled'] & ~1), sled)
        before = set(m.live_allocs)
        m.call(m.res_addr('hb_unload_other_apps'), (0,))  # keep slot 0
        # arena1 should be freed; arena0 kept
        self.assertIn(arena0, m.live_allocs)
        self.assertNotIn(arena1, m.live_allocs)
        self.assertEqual(m.word(aa + 4), 0)



class FirstFitHeap:
    """A first-fit OS heap model: real holes, so fragmentation shows up in
    the largest-free-block reading, unlike ChainMachine's bump allocator."""

    def __init__(self, m, base, size):
        self.m, self.base, self.end = m, base, base + size
        self.used = {}                       # addr -> rounded size
        self.freed = []                      # (addr, size) freed, not reused
        hooks = {0x0842B440: self.alloc, 0x0842D86C: self.free,
                 0x08417824: self.heap_free, 0x0800CBB8: self.heap_largest}
        for sym, fn in (('hb_os_alloc', self.alloc), ('hb_os_free', self.free),
                        ('hb_os_heap_free', self.heap_free),
                        ('hb_os_heap_largest', self.heap_largest)):
            if sym in m.res_syms:
                hooks[m.res_addr(sym)] = fn
        for addr, fn in hooks.items():
            m.extra_handlers[addr] = fn

    def holes(self):
        cur, out = self.base, []
        for addr in sorted(self.used):
            if addr > cur:
                out.append((cur, addr - cur))
            cur = max(cur, addr + self.used[addr])
        if cur < self.end:
            out.append((cur, self.end - cur))
        return out

    def largest(self):
        return max((n for _, n in self.holes()), default=0)

    def total_free(self):
        return sum(n for _, n in self.holes())

    def alloc(self, uc):
        size = uc.reg_read(UC_ARM_REG_R0)
        self.m.calls.append(('alloc', size))
        need = (max(size, 1) + 31) & ~31
        for addr, n in self.holes():
            if n >= need:
                self.used[addr] = need
                self.freed = [(a, k) for a, k in self.freed
                              if a + k <= addr or a >= addr + need]
                self.m.live_allocs[addr] = size
                return self.m.ret(addr)
        return self.m.ret(0)

    def free(self, uc):
        addr = uc.reg_read(UC_ARM_REG_R0)
        if addr:
            self.m.calls.append(('free', addr))
            size = self.used.pop(addr, None)
            assert size is not None, 'bad or double free %#x' % addr
            self.m.live_allocs.pop(addr, None)
            self.freed.append((addr, size))
            if self.poison:
                uc.mem_write(addr, b'\xA5' * size)
        self.m.ret()

    # Use-after-free watch: every resident load/store that lands in a block
    # freed earlier (and not handed out again) is recorded.
    poison = True

    def watch(self):
        from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE
        self.uaf = []

        def hook(_uc, _access, address, size, _value, _data):
            for a, n in self.freed:
                if a <= address < a + n:
                    self.uaf.append((hex(address), size))
                    return
        return self.m.uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, hook,
                                  begin=self.base, end=self.end)

    def heap_free(self, _uc):
        self.m.ret(self.total_free())

    def heap_largest(self, _uc):
        self.m.ret(self.largest())


def launch_record(app_id, kind, name):
    value = bytearray(80)
    struct.pack_into('<IHH', value, 0, app_id, kind, 0)
    value[48:48 + len(name)] = name.encode()
    return bytes(value)


class NanoappsMemoryLifecycleTests(unittest.TestCase):
    """rc3: the rc2 hardware report. After the first app the largest free block
    fell from 4 MB to 2 MB and never came back, so big apps refused and a
    Mario relaunch failed. Boot the real resident on a first-fit heap, launch
    a big app, leave it, relaunch it, switch apps, and check the heap."""

    BIG = 3_000_000                          # about Mario 64's image
    HEAP = 6 * 1024 * 1024                   # about the free heap after boot
    HEAP_BASE = 0x09200000                   # free emulator RAM below the bootstrap

    def setUp(self):
        self.assertTrue(RESIDENT.with_suffix('.hbapp').exists())

    def boot(self):
        m = ChainMachine(real_resident=True)
        names = (('Probe', 1, make_hrl(4096)), ('Mario 64', 2, make_hrl(self.BIG)),
                 ('Temple Run', 2, make_hrl(700_000)))
        items = [catalog_bundle('hb.x%d' % i, n, 0x0dad8300 + i)
                 for i, (n, _, _) in enumerate(names)]
        m.files['/Apps/AllApps-B.pack'] = pack(items)
        for i, (n, kind, exe) in enumerate(names):
            m.files[f'/Apps/Executables/{n}.hbapp'] = exe
            m.files[f'/Apps/Icons/{n}.bin'] = ICON
            m.register_records.append(launch_record(0x0dad8300 + i, kind, n))
        m.intercept(m.res_addr('hb_silver_push_screen_id'), 'push')
        if 'hb_silver_icon_refresh' in m.res_syms:
            m.intercept(m.res_addr('hb_silver_icon_refresh'), 'zero')
        # The bootstrap and the resident sit at 0x09800000 / 0x09A00000, the
        # stack and scratch above; model the OS heap in the free RAM below.
        heap = FirstFitHeap(m, self.HEAP_BASE, self.HEAP)
        m.arm(); m.dispatch()
        self.assertIsNotNone(m.registered_catalog)
        self.m, self.heap = m, heap
        return m, heap

    def sym(self, name):
        for key in self.m.res_syms:
            if key == name or key.startswith(name + '.'):
                return self.m.res_base + (self.m.res_syms[key] & ~1)
        raise KeyError(name)

    def launch(self, index):
        m = self.m
        m.call(m.res_addr('hb_app_launch'), (0x1111, m.res_addr('g_launch') + 80 * index),
               count=400_000_000)
        return m.word(m.res_addr('s_app_entry') + 4 * index)

    def leave_to_home(self):
        m = self.m
        m.call(self.sym('hb_exit_hook'), (0x09d10400, 0))          # app screen leaves
        m.uc.mem_write(0x08d00000, b'HandleGotoMainScreen\0')
        m.dispatch()                                          # Home action
        self.fire_refresh_timer()

    def fire_refresh_timer(self):
        m = self.m
        msg = 0x09d10000
        m.word(msg + 4, 16)                                   # CLTIMERMSG
        m.word(msg + 8, self.sym('s_refresh_timer'))
        m.call(self.sym('hb_home_message_hook'), (0x1111, msg))

    def usb_return(self):
        m = self.m
        msg, payload = 0x09d10100, 0x09d10200
        m.word(msg + 4, 30)
        m.word(msg + 8, payload)
        m.word(payload + 4, 0x6385)                           # HOME_REFRESH_NOTICE
        m.call(m.word(0x087CBB64), (0x1111, msg))             # RAW_SLOT -> rawmsg_hook
        self.fire_refresh_timer()

    def test_boot_reserves_sled_and_framebuffer_up_front(self):
        m, heap = self.boot()
        sizes = sorted(m.live_allocs.values())
        self.assertIn(512 * 1024, sizes)                      # sled, exactly 512 KB
        self.assertIn(240 * 432 * 4, sizes)                   # framebuffer
        self.assertNotEqual(m.word(self.sym('s_sled')), 0)
        self.assertNotEqual(m.word(self.sym('s_fb')), 0)

    def test_home_frees_big_app_whole_and_relaunch_reuses_block(self):
        # rc4: back on Home the app is torn down and freed (aux first, then
        # image), with no catalog read and no allocation.
        m, heap = self.boot()
        baseline = heap.largest()
        self.assertGreater(baseline, self.BIG + 256 * 1024)
        self.assertNotEqual(self.launch(1), 0, 'Mario must load')
        arena = m.word(m.res_addr('s_app_arena') + 4)
        calls = len(m.calls)
        self.leave_to_home()
        home = m.calls[calls:]
        self.assertFalse([c for c in home if c[0] in ('alloc', 'read', 'register')], home)
        self.assertIn(('free', arena), home)
        self.assertEqual(m.word(m.res_addr('s_app_arena') + 4), 0)
        self.assertEqual(heap.largest(), baseline)
        self.assertNotEqual(self.launch(1), 0, 'Mario must load again')
        self.assertEqual(m.word(m.res_addr('s_app_arena') + 4), arena)

    def test_heavy_exit_then_probe_has_no_use_after_free(self):
        # The rc3 hardware reboot: Mario or PvZ, Home, then Probe. Freed blocks
        # are poisoned and watched while Probe launches and runs a frame.
        m, heap = self.boot()
        baseline = heap.largest()
        self.assertNotEqual(self.launch(1), 0)
        aux = heap.holes()[0][0]                          # the app's own aux block
        heap.used[aux] = 0x10000
        m.live_allocs[aux] = 0x10000
        m.word(m.res_addr('s_shared') + 24, aux)          # hb_shared->aux_block
        self.leave_to_home()
        heap.watch()
        self.assertNotEqual(self.launch(0), 0, 'Probe must load')
        self.leave_to_home()
        self.assertEqual(heap.uaf, [], 'resident touched freed memory')
        self.assertGreaterEqual(heap.largest(), baseline - 64 * 1024)

    def test_repeated_switches_do_not_fragment(self):
        m, heap = self.boot()
        baseline = heap.largest()
        free0 = heap.total_free()
        for i in range(8):
            for index in (1, 2, 0):
                self.assertNotEqual(self.launch(index), 0, (i, index))
                self.leave_to_home()
                self.assertEqual(heap.largest(), baseline, (i, index))
        self.assertEqual(heap.total_free(), free0)

    def test_usb_refresh_unloads_before_reading_catalog(self):
        m, heap = self.boot()
        baseline, free0 = heap.largest(), heap.total_free()
        self.assertNotEqual(self.launch(1), 0)
        # USB return goes straight to Home without HandleGotoMainScreen.
        m.call(self.sym('hb_exit_hook'), (0x09d10400, 0))
        calls = len(m.calls)
        self.usb_return()
        seq = m.calls[calls:]
        frees = [i for i, c in enumerate(seq) if c[0] == 'free']
        reads = [i for i, c in enumerate(seq) if c[0] in ('read', 'size')]
        self.assertTrue(frees and reads, seq)
        self.assertLess(frees[0], reads[0], 'arena must be freed before the catalog read')
        self.assertEqual(m.word(m.res_addr('s_app_arena') + 4), 0)
        # Same catalog: the re-read copy is dropped, so the heap is as at boot.
        self.assertEqual(heap.total_free(), free0)
        self.assertEqual(heap.largest(), baseline)
        self.assertNotEqual(self.launch(1), 0)

    def test_registration_refuses_only_the_icon_cache_grow(self):
        m, heap = self.boot()
        grow = m.call(self.sym('hb_register_alloc'), (1024 * 1024,))
        self.assertEqual(grow, 0)
        small = m.call(self.sym('hb_register_alloc'), (4096,))
        self.assertNotEqual(small, 0)

    def test_icon_lru_is_lean(self):
        from elftools.elf.elffile import ELFFile
        with open(RESIDENT.with_suffix('.reloc.elf'), 'rb') as f:
            symtab = ELFFile(f).get_section_by_name('.symtab')
            size = symtab.get_symbol_by_name('s_icon_state')[0]['st_size']
        self.assertEqual(size, 24 + 64 * 4 + 64 + 16 * 8)      # 16 LRU slots
        mk = (ROOT_NA / 'system/silver_resident_na/Makefile').read_text()
        self.assertIn("#define ICON_LRU_N    16u", mk)
        self.assertIn("#define ICON_BUF_SZ   0xC800u", mk)
        self.assertNotIn('na_silver_icon.c', ' '.join(
            p.name for p in (ROOT_NA / 'system/silver_resident_na').iterdir()))


class NanoappsViewHookTests(unittest.TestCase):
    """rc4: the custom-draw view (Probe, Paint) and the GL view (Mario, PvZ)
    are different OS classes and both stay cached on the shared canvas. Each
    must forward to its OWN class's handlers, whichever was built last."""

    CUSTOM_VT, GL_VT = 0x09d20000, 0x09d21000
    CUSTOM_FN, GL_FN = 0x09d30000, 0x09d34000
    CUSTOM_VIEW, GL_VIEW, CANVAS = 0x09d50000, 0x09d51000, 0x09d52000

    def setUp(self):
        self.assertTrue(RESIDENT.with_suffix('.hbapp').exists())
        m = self.m = ChainMachine(real_resident=True)
        m.arm(); m.dispatch()
        self.hits = []
        for vt, fn, tag in ((self.CUSTOM_VT, self.CUSTOM_FN, 'custom'),
                            (self.GL_VT, self.GL_FN, 'gl')):
            for slot in range(0x800 // 4):
                m.word(vt + 4 * slot, (fn + 4 * slot) | 1)
                m.extra_handlers[fn + 4 * slot] = (
                    lambda uc, t=tag, o=slot * 4: (self.hits.append((t, o)), m.ret(0)))
        m.word(self.CUSTOM_VIEW, self.CUSTOM_VT)
        m.word(self.GL_VIEW, self.GL_VT)

        def ret_value(v):
            return lambda uc: m.ret(v)
        m.extra_handlers[m.res_addr('hb_silver_template_canvas')] = ret_value(self.CANVAS)
        m.extra_handlers[m.res_addr('hb_silver_make_color_view')] = ret_value(self.CUSTOM_VIEW)
        m.extra_handlers[m.res_addr('hb_silver_make_gl_view')] = ret_value(self.GL_VIEW)
        m.extra_handlers[m.res_addr('hb_silver_realize_view')] = ret_value(0)
        for fw in (0x0802dee8, 0x0847b274, 0x080372b8, 0x08423b44, 0x081e0a02,
                   0x0841c42c, 0x0841cd68):
            m.extra_handlers[fw] = ret_value(0)

    def present(self, kind):
        m = self.m
        m.uc.mem_write(m.res_addr('s_cur_app_kind'), struct.pack('<H', kind))
        m.uc.mem_write(m.res_addr('s_inject_armed'), b'\x01')
        m.call(m.res_addr('templ_hook'), (0x09d53000, 0, 0, 0))

    def forwarded(self, hook, view, *args):
        self.hits.clear()
        self.m.call(self.m.res_addr(hook), (view,) + args)
        return [h for h in self.hits if h[1] in (0x00, 0x04, 0x40, 0xec, 0x104)]

    def test_probe_after_gl_app_forwards_to_its_own_class(self):
        self.present(1)                      # Probe: builds the custom-draw view
        self.present(2)                      # Mario: builds the GL view
        self.present(1)                      # Probe again: reuses the custom view
        msg = 0x09d54000
        self.m.word(msg + 4, 3)              # any non-timer message
        self.assertEqual(self.forwarded('hb_msg_hook', self.CUSTOM_VIEW, msg),
                         [('custom', 0x40)])
        self.assertEqual(self.forwarded('hb_visibility_hook', self.CUSTOM_VIEW, 1),
                         [('custom', 0xec)])
        self.assertEqual(self.forwarded('hb_dtor_hook0', self.CUSTOM_VIEW),
                         [('custom', 0x00)])
        self.assertEqual(self.forwarded('hb_dtor_hook1', self.CUSTOM_VIEW),
                         [('custom', 0x04)])
        self.assertEqual(self.forwarded('hb_msg_hook', self.GL_VIEW, msg),
                         [('gl', 0x40)])
        self.assertEqual(self.forwarded('hb_visibility_hook', self.GL_VIEW, 0),
                         [('gl', 0xec)])
        self.assertEqual(self.forwarded('hb_exit_hook', self.GL_VIEW, 0),
                         [('gl', 0x104)])
        self.assertEqual(self.forwarded('hb_exit_hook', self.CUSTOM_VIEW, 0),
                         [('custom', 0x104)])


class NanoappsLastTraceTests(unittest.TestCase):
    """rc4: /Apps/Startup/lasttrace.bin is written on a normal nano7kit boot,
    where the C6/98 boot report page is live (rc1..rc3 skipped it then)."""

    def test_lasttrace_written_when_boot_report_is_live(self):
        from test_untether_na import make_ring, REPORT_BASE
        ring = make_ring(seq=5, count=40)
        m = ChainMachine(real_resident=True, prev_rings=((0x09120000, ring),))
        m.word(REPORT_BASE + 0x2C, 0x32505452)
        m.arm(); m.dispatch()
        self.assertEqual(m.written_files.get('/Apps/Startup/lasttrace.bin'), ring)


class NanoappsSwitchLoopTests(unittest.TestCase):
    """Combined resident: load Paint then Heavy 20 times with unload(-1)."""

    def setUp(self):
        self.assertTrue(RESIDENT.with_suffix('.hbapp').exists())

    def test_switch_paint_and_heavy_twenty_times_no_leak(self):
        """Load Paint then Heavy 20 times; unload(-1) must free the prior arena."""
        paint = make_hrl(4096, span=4096)
        heavy = make_hrl(600_000, span=600_000)
        m = ChainMachine(real_resident=True)
        m.files['/Apps/Paint.nanoapp/app.hbapp'] = paint
        m.files['/Apps/Heavy.nanoapp/app.hbapp'] = heavy
        m.heap_largest_value = 8 * 1024 * 1024
        _, span, _ = load_hrl(m.uc, m.res_blob, m.res_base)
        heap_base = (m.res_base + span + 0xfff) & ~0xfff
        m.heap_next = heap_base
        path = 0x09d00000
        out_arena = 0x09d00100
        out_len = 0x09d00104
        load = m.res_addr('hb_reloc_load_file_verified_chunked')
        unload = m.res_addr('hb_unload_other_apps')
        syms = m.res_syms
        base = m.res_base
        sled = None
        if 's_sled' in syms:
            sled = m.heap_next
            m.live_allocs[sled] = 64
            m.heap_next += 64
            m.word(base + (syms['s_sled'] & ~1), sled)

        free_calls = 0
        peaks = []
        nul = bytes([0])
        for _ in range(20):
            for fat, blob in (
                (b'/Apps/Paint.nanoapp/app.hbapp', paint),
                (b'/Apps/Heavy.nanoapp/app.hbapp', heavy),
            ):
                before_free = sum(1 for c in m.calls if c[0] == 'free')
                m.uc.mem_write(path, fat + nul)
                m.word(out_arena, 0)
                m.word(out_len, 0)
                entry = m.call(load, (path, out_arena, out_len, fnv1a32(blob)))
                self.assertNotEqual(entry, 0, fat.decode())
                arena = m.word(out_arena)
                if 's_app_arena' in syms and 'g_launch_count' in syms:
                    aa = base + (syms['s_app_arena'] & ~1)
                    m.word(aa, arena)
                    m.word(base + (syms['g_launch_count'] & ~1), 1)
                m.call(unload, (0xFFFFFFFF,))
                free_calls += sum(1 for c in m.calls if c[0] == 'free') - before_free
                if sled is not None and set(m.live_allocs.keys()) <= {sled}:
                    m.heap_next = sled + 64
                elif not m.live_allocs:
                    m.heap_next = heap_base
            peaks.append(len(m.live_allocs))
        self.assertGreaterEqual(free_calls, 20)
        self.assertLessEqual(max(peaks), 2)
        self.assertLessEqual(peaks[-1], 2)

    def test_low_memory_heavy_refuses_cleanly(self):
        heavy = make_hrl(1_250_000, span=1_250_000)
        m = ChainMachine(real_resident=True)
        m.files['/Apps/Heavy.nanoapp/app.hbapp'] = heavy
        m.heap_largest_value = 64 * 1024
        _, span, _ = load_hrl(m.uc, m.res_blob, m.res_base)
        m.heap_next = (m.res_base + span + 0xfff) & ~0xfff
        path = 0x09d00000
        m.uc.mem_write(path, b'/Apps/Heavy.nanoapp/app.hbapp' + bytes([0]))
        out_arena = 0x09d00100
        out_len = 0x09d00104
        m.word(out_arena, 0)
        entry = m.call(m.res_addr('hb_reloc_load_file_verified_chunked'),
                       (path, out_arena, out_len, fnv1a32(heavy)))
        self.assertEqual(entry, 0)
        self.assertEqual(m.word(out_arena), 0)
        self.assertEqual(len(m.live_allocs), 0)


def _pocketjs_emu():
    import sys
    pj = str(ROOT_NA.parent / 'pocketjs')
    if pj not in sys.path:
        sys.path.insert(0, pj)
    import emu  # noqa: E402
    return emu


def _quickjs_served():
    data = ROOT_NA.parent / 'untether/artifacts/extra/Data/PocketJS'
    return {'/Apps/Data/PocketJS/' + f.relative_to(data).as_posix(): f.read_bytes()
            for f in sorted(data.rglob('*')) if f.is_file()}


class NanoappsQuickJsAfterOtherAppTests(unittest.TestCase):
    """rc5: the rc4 hardware report. JS QuickJS (PocketJS Home) worked after a
    fresh boot, but after Probe, Mario or any other app it stopped with
    "createNode (status -2, 0 nodes made)", heap peak 1267 KB of 2272 KB, with
    Free RAM still at 4096 largest.

    Status -2 is POCKET_STATUS_INVALID, not out of memory: pocket_runtime_op
    refuses an argument record that is not 8-byte aligned. The records sit on
    JS QuickJS's private stack, whose top was the end of its OS heap block.
    The OS allocator only promises 4-byte alignment, and where the block lands
    (on 8, or 4 past it) depends on what ran since boot. rc5 rounds the stack
    top down to 16 bytes. The emulator hands out the block 4 past an 8-byte
    boundary, as the iPod did after another app; the staged JS QuickJS must
    still create its nodes. The rc4 build fails the same way the iPod did."""

    HBAPP = ROOT_NA.parent / 'untether/artifacts/extra/Executables/JS QuickJS.hbapp'
    MIB = 1024 * 1024

    def run_home(self, hbapp, misalign, frames=40):
        emu = _pocketjs_emu()
        e = emu.Emu(str(hbapp), 0, regions=[int(4.0 * self.MIB), int(1.5 * self.MIB), self.MIB, self.MIB // 2],
                    served=_quickjs_served(), misalign=misalign)
        frame = e.call(emu.BASE + e.entry, [0, emu.FB, emu.W, emu.H, emu.SHARED])
        self.assertTrue(frame)
        # Heartbeats as pocketjs/emu.py's script runner sends them (the clock
        # advances per call); stop once the bundle mounted or the app failed.
        for _ in range(frames):
            e.calls += 1
            e.call(frame, [])
            if any(t[0] in ('PJLIVE', 'PJFAILM') for t in e.trace):
                break
        return e

    def check_home_ran(self, e, misalign):
        tags = [t[0] for t in e.trace]
        self.assertIn('PJHOME', tags)
        self.assertNotIn('PJFAIL', tags, 'JS QuickJS Home stopped (misalign %d)' % misalign)
        self.assertNotIn('PJRETRY', tags)
        self.assertIn('PJLIVE', tags)                   # the bundle ran and mounted
        align = [t for t in e.trace if t[0] == 'PJALIGN']
        self.assertEqual(len(align), 1)
        self.assertEqual(align[0][1] % 8, misalign)     # the block as the OS gave it
        self.assertEqual(align[0][2], 0)                # the stack top: 16-byte aligned

    def test_home_creates_nodes_in_an_8_byte_aligned_block(self):
        self.check_home_ran(self.run_home(self.HBAPP, 0), 0)

    def test_home_creates_nodes_after_another_app_moved_the_block(self):
        # Probe or Mario, then JS QuickJS: its block comes back 4 past 8.
        self.check_home_ran(self.run_home(self.HBAPP, 4), 4)

    def test_rc4_build_fails_like_the_ipod(self):
        # Negative control: the rc4 image (no rounding) in the same model
        # stops with code 3 and a 1267 KB peak of 2272 KB, as on hardware.
        import subprocess, tempfile
        root = ROOT_NA.parent
        try:
            blob = subprocess.run(['git', 'show', 'e10b5cb:untether/artifacts/extra/Executables/JS QuickJS.hbapp'],
                                  cwd=root, capture_output=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            self.skipTest('rc4 commit not in this clone')
        with tempfile.NamedTemporaryFile(suffix='.hbapp') as f:
            f.write(blob); f.flush()
            e = self.run_home(f.name, 4)
        fails = [t for t in e.trace if t[0] == 'PJFAIL']
        self.assertTrue(fails)
        self.assertEqual(fails[0][1], 3)
        peak = [t for t in e.trace if t[0] == 'PJFAILM'][0]
        self.assertEqual((peak[1] // 1024, peak[2] // 1024), (1267, 2272))

    def test_source_rounds_the_private_stack_and_logs_it(self):
        patch = (ROOT_NA.parent / 'pocketjs/nano7g.patch').read_text(encoding='utf-8')
        self.assertIn('+    s_stack_hi = (uint32_t *)(((uintptr_t)s_heap + want) & ~(uintptr_t)15u);', patch)
        self.assertIn('+        "bic r1, r1, #7\\n"', patch)
        self.assertIn('+    pocketnano_log("PJALIGN"', patch)
        import sys
        sys.path.insert(0, str(ROOT_NA.parent))
        from host import pocketjs
        self.assertIn('PJALIGN', pocketjs.LOG_TAGS)
        lines = pocketjs.summarize_trace([('PJALIGN', 4, 0)])
        self.assertEqual(lines, ['  block address +4 past 16, stack top +0'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
