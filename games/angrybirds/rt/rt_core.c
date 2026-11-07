/* rt_core.c: guest memory, heap, dispatch for nano7kit's Angry Birds runtime (MIT). */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <setjmp.h>
#include "rt.h"
#include "rt_layout.h"
#include "rt_plat.h"

uint8_t *g_mem;
uint32_t SP;
uint32_t g_lo, g_hi;
uint32_t VS[32];
uint32_t FPSCR;
extern const rt_fn_entry rt_fn_table[];
extern const uint32_t rt_fn_count;
extern const char *const rt_import_names[];
extern const rt_fn rt_import_fns[];
extern const uint32_t rt_import_count;
extern const uint32_t rt_import_slots[][2];
extern const uint32_t rt_import_slot_count;

uint32_t g_imparea;                 /* 32 bytes per import name */
static uint32_t heap_lo, heap_hi, stack_lo;

void rt_log(const char *fmt, ...) {
    char buf[256];
    va_list ap; va_start(ap, fmt); vsnprintf(buf, sizeof buf, fmt, ap); va_end(ap);
    plat_log_line(buf);
}

#ifdef RT_CHECK
uint32_t rt_watch_addr;
uint8_t *rt_checked(uint32_t a, uint32_t n) {
    if (a < g_lo || a + n > g_hi || a + n < a) {
        rt_log("guest access %08x (%u bytes) outside %08x..%08x SP=%08x", a, n, g_lo, g_hi, SP);
        plat_fatal("bad guest access");
    }
    return g_mem + a;
}
void rt_watch_store(uint32_t a, uint32_t v) {
    if (rt_watch_addr && a == rt_watch_addr) {
        rt_log("watch %08x := %08x (was %08x) SP=%08x", a, v, gr32(a), SP);
        plat_trace();
    }
    *(u32u *)rt_checked(a, 4) = v;
}
#endif

/* ---- heap: boundary tags, first fit with an address-ordered free list ---------------------
 * block: [u32 size|used][payload ...][u32 size|used]; size counts header+footer, 8-aligned.
 * free blocks keep next/prev free links in the payload. */
#define HDR(p) ((p) - 4)
static uint32_t free_head, used_bytes, peak_bytes;
static uint32_t bsize(uint32_t b) { return gr32(b) & ~7u; }
static int bused(uint32_t b) { return gr32(b) & 1; }
static void bset(uint32_t b, uint32_t size, int used) { gw32(b, size | (uint32_t)used); gw32(b + size - 4, size | (uint32_t)used); }
static uint32_t fnext(uint32_t b) { return gr32(b + 4); }
static uint32_t fprev(uint32_t b) { return gr32(b + 8); }
static void fset_next(uint32_t b, uint32_t v) { gw32(b + 4, v); }
static void fset_prev(uint32_t b, uint32_t v) { gw32(b + 8, v); }
static void funlink(uint32_t b) {
    uint32_t n = fnext(b), p = fprev(b);
    if (p) fset_next(p, n); else free_head = n;
    if (n) fset_prev(n, p);
}
static void fpush(uint32_t b) {
    fset_next(b, free_head); fset_prev(b, 0);
    if (free_head) fset_prev(free_head, b);
    free_head = b;
}
static void heap_init(uint32_t lo, uint32_t hi) {
    heap_lo = (lo + 7) & ~7u; heap_hi = hi & ~7u;
    /* sentinels: a used 8-byte block at each end */
    bset(heap_lo, 8, 1);
    bset(heap_hi - 8, 8, 1);
    uint32_t b = heap_lo + 8, size = heap_hi - 8 - b;
    bset(b, size, 0); free_head = 0; fpush(b);
}
uint32_t g_malloc(uint32_t n) {
#ifdef RT_CHECK
    if (n >= 192u * 1024u) { rt_log("big alloc %u (heap used %u)", n, used_bytes); plat_trace(); }
#endif
    uint32_t need = (n + 8 + 7) & ~7u;
    if (need < 16) need = 16;
    for (uint32_t b = free_head; b; b = fnext(b)) {
        uint32_t size = bsize(b);
        if (size < need) continue;
        funlink(b);
        if (size - need >= 32) {
            bset(b + need, size - need, 0); fpush(b + need);
            size = need;
        }
        bset(b, size, 1);
        used_bytes += size; if (used_bytes > peak_bytes) peak_bytes = used_bytes;
        return b + 4;
    }
    rt_log("guest heap exhausted: %u bytes wanted, %u used of %u", n, used_bytes, heap_hi - heap_lo);
    return 0;
}
void g_free(uint32_t p) {
    if (!p) return;
    uint32_t b = p - 4;
    if (b < heap_lo || b >= heap_hi || !bused(b)) { rt_log("bad free %08x", p); return; }
    uint32_t size = bsize(b);
    used_bytes -= size;
    uint32_t nb = b + size;
    if (!bused(nb)) { funlink(nb); size += bsize(nb); }
    uint32_t pf = gr32(b - 4);
    if (!(pf & 1)) { uint32_t pb = b - (pf & ~7u); funlink(pb); b = pb; size += pf & ~7u; }
    bset(b, size, 0); fpush(b);
}
uint32_t g_realloc(uint32_t p, uint32_t n) {
    if (!p) return g_malloc(n);
    uint32_t old = bsize(p - 4) - 8;
    if (n <= old) return p;
    uint32_t q = g_malloc(n);
    if (q) { memcpy(G8(q), G8(p), old); g_free(p); }
    return q;
}
uint32_t g_strdup(const char *s) {
    uint32_t n = (uint32_t)strlen(s) + 1, p = g_malloc(n);
    if (p) memcpy(G8(p), s, n);
    return p;
}
uint32_t rt_heap_used(void) { return used_bytes; }
uint32_t rt_heap_peak(void) { return peak_bytes; }
uint32_t rt_heap_size(void) { return heap_hi - heap_lo; }

/* ---- dispatch ------------------------------------------------------------------------------ */
static uint32_t missing_seen[64];
uint64_t rt_icall(uint32_t addr, uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3) {
    if (addr >= g_imparea && addr < g_imparea + 32u * rt_import_count) {
        uint32_t i = (addr - g_imparea) / 32u;
        if (rt_import_fns[i]) return rt_import_fns[i](r0, r1, r2, r3);
        rt_log("call to unimplemented import %s", rt_import_names[i]);
        return 0;
    }
    uint32_t a = addr & ~1u, lo = 0, hi = rt_fn_count;
    while (lo < hi) {
        uint32_t mid = (lo + hi) / 2;
        if (rt_fn_table[mid].addr < a) lo = mid + 1; else hi = mid;
    }
    if (lo < rt_fn_count && rt_fn_table[lo].addr == a)
        return rt_fn_table[lo].fn(r0, r1, r2, r3);
    for (int i = 0; i < 64; i++) {
        if (missing_seen[i] == a) return 0;
        if (!missing_seen[i]) { missing_seen[i] = a; break; }
    }
    rt_log("call to %08x: not translated (r0=%08x r1=%08x r2=%08x [r0]=%08x)",
           addr, r0, r1, r2, (r0 >= g_lo && r0 + 4u <= g_hi) ? gr32(r0) : 0);
    if (addr < 0x1000u) plat_fatal("call to a null function pointer");
    return 0;
}
void rt_undef(uint32_t addr) { rt_log("undefined instruction at %08x", addr); plat_fatal("undefined instruction"); }
void rt_swi(uint32_t n) { rt_log("swi %u", n); }

uint32_t rt_call(const char *what, uint32_t addr, uint32_t a0, uint32_t a1, uint32_t a2, uint32_t a3,
                 const uint32_t *stack, int nstack) {
    (void)what;
    uint32_t saved = SP;
    SP -= 4u * (uint32_t)((nstack + 1) & ~1);
    for (int i = 0; i < nstack; i++) gw32(SP + 4u * (uint32_t)i, stack[i]);
    uint64_t q = rt_icall(addr, a0, a1, a2, a3);
    if (SP != saved - 4u * (uint32_t)((nstack + 1) & ~1)) rt_log("%s: stack moved by %d", what, (int)(SP - saved));
    SP = saved;
    return (uint32_t)q;
}

extern void rt_imports_init(void);
int rt_init(void *block, uint32_t block_size) {
    g_mem = (uint8_t *)block - GUEST_BASE;
    g_lo = GUEST_BASE; g_hi = GUEST_BASE + block_size;
    memset(block, 0, block_size);
    uint32_t got = plat_load_image(G8(GUEST_BASE), GUEST_IMAGE_END - GUEST_BASE);
    if (got != GUEST_IMAGE_END - GUEST_BASE) { rt_log("guest image: %u of %u bytes", got, GUEST_IMAGE_END - GUEST_BASE); return -1; }
    g_imparea = (GUEST_DATA_END + 15u) & ~15u;
    for (uint32_t i = 0; i < rt_import_slot_count; i++)
        gw32(rt_import_slots[i][0], g_imparea + 32u * rt_import_slots[i][1]);
    uint32_t heap = (g_imparea + 32u * rt_import_count + 4095u) & ~4095u;
    stack_lo = g_hi - GUEST_STACK;
    if (heap + 65536u > stack_lo) { rt_log("guest block too small"); return -1; }
    heap_init(heap, stack_lo);
    SP = g_hi - 64u;
    memset(VS, 0, sizeof VS);
    FPSCR = 0;
    rt_imports_init();
    return 0;
}

#define MAXJMP 16
static jmp_buf jbufs[MAXJMP];
static uint32_t jkeys[MAXJMP];
jmp_buf *rt_jmp(uint32_t guest) {
    int i, free_i = -1;
    for (i = 0; i < MAXJMP; i++) {
        if (jkeys[i] == guest) return &jbufs[i];
        if (!jkeys[i] && free_i < 0) free_i = i;
    }
    if (free_i < 0) free_i = 0;
    jkeys[free_i] = guest;
    return &jbufs[free_i];
}

/* ARM GCC SjLj: context at r0 is
 *   +0 prev, +4 resumeLocation, +8 resumeParameters[0] (exception),
 *   +24 personality, +28 LSDA, +32 r7, +36 landing pad, +40 guest sp.
 * Register is wrapped with host setjmp in the recompiled caller so a throw
 * can goto the landing pad in that C function. */
#define MAXSJL 64
static jmp_buf sj_bufs[MAXSJL];
static uint32_t sj_keys[MAXSJL];
static uint32_t sj_chain, sj_landed;

static int sj_slot(uint32_t ctx, int alloc) {
    int i, free_i = -1;
    for (i = 0; i < MAXSJL; i++) {
        if (sj_keys[i] == ctx) return i;
        if (!sj_keys[i] && free_i < 0) free_i = i;
    }
    if (!alloc) return -1;
    if (free_i < 0) { rt_log("sjlj slots full"); free_i = 0; }
    sj_keys[free_i] = ctx;
    return free_i;
}

jmp_buf *rt_sjlj_buf(uint32_t ctx) { return &sj_bufs[sj_slot(ctx, 1)]; }
uint32_t rt_sjlj_landed(void) { return sj_landed; }
uint32_t rt_sjlj_top(void) { return sj_chain; }

void rt_sjlj_register(uint32_t ctx) {
    gw32(ctx, sj_chain);
    sj_chain = ctx;
    sj_slot(ctx, 1);
}

void rt_sjlj_unregister(uint32_t ctx) {
    if (!ctx) return;
    if (sj_chain == ctx) sj_chain = gr32(ctx);
    else {
        uint32_t p = sj_chain;
        while (p) {
            uint32_t n = gr32(p);
            if (n == ctx) { gw32(p, gr32(ctx)); break; }
            p = n;
        }
    }
    int i = sj_slot(ctx, 0);
    if (i >= 0) sj_keys[i] = 0;
}

static uint32_t read_uleb(const uint8_t **pp) {
    uint32_t x = 0, s = 0;
    for (;;) {
        uint8_t c = *(*pp)++;
        x |= (uint32_t)(c & 0x7f) << s;
        if (c < 128) return x;
        s += 7;
        if (s > 28) return x;
    }
}

/* 1 = this call site has a catch, 0 = cleanup only / not in a try, -1 = unknown. */
static int sjlj_has_catch(uint32_t ctx) {
    int32_t loc = (int32_t)gr32(ctx + 4);
    if (loc <= 0) return 0;
    uint32_t lsda = gr32(ctx + 28);
    if (lsda < g_lo || lsda + 8 > g_hi) return -1;
    const uint8_t *p = G8(lsda), *end = g_mem + g_hi;
    if (p + 4 >= end) return -1;
    if (*p++ != 0xff) return -1;
    uint8_t ttype = *p++;
    if (ttype != 0xff) read_uleb(&p);
    if (p + 2 >= end) return -1;
    uint8_t cs_enc = *p++;
    uint32_t cs_len = read_uleb(&p);
    if (cs_enc == 3 && cs_len >= 2 && cs_len < 512 && p + cs_len <= end) {
        uint32_t i;
        for (i = 0; i + 1 < cs_len; i += 2)
            if (p[i] == (uint8_t)loc) return p[i + 1] != 0;
        if ((uint32_t)loc * 2u + 1u < cs_len) return p[loc * 2u + 1u] != 0;
        return 0;
    }
    return -1;
}

void rt_sjlj_raise(uint32_t exception) {
    uint32_t ctx = sj_chain, fallback = 0, chosen = 0;
    while (ctx) {
        fallback = ctx;
        int h = sjlj_has_catch(ctx);
        if (h > 0) { chosen = ctx; break; }
        if (h < 0 && !chosen) chosen = ctx;
        ctx = gr32(ctx);
    }
    if (!chosen) chosen = fallback;
    uint32_t pad = chosen ? gr32(chosen + 36) : 0;
    int32_t loc = chosen ? (int32_t)gr32(chosen + 4) : 0;
    int i = chosen ? sj_slot(chosen, 0) : -1;
    if (!chosen || pad < 0x1000u || pad >= 0x200000u || i < 0) {
        rt_log("uncaught C++ exception ctx=%08x loc=%d pad=%08x slot=%d", chosen, (int)loc, pad, i);
        plat_fatal("uncaught C++ exception");
    }
    gw32(chosen + 8, exception);
    gw32(chosen + 12, 1); /* resumeParameters[1]: catch, not cleanup-and-resume */
    sj_chain = chosen;
    sj_landed = chosen;
    rt_log("sjlj catch ctx=%08x loc=%d pad=%08x", chosen, (int)loc, pad);
    longjmp(sj_bufs[i], 1);
}
void rt_missing_import(const char *name) {
    static const char *seen[32]; int i;
    for (i = 0; i < 32 && seen[i]; i++) if (seen[i] == name) return;
    if (i < 32) seen[i] = name;
    rt_log("import %s not implemented (returns 0)", name);
}
