/* shims.c: bionic FILE/ctype/pthread and GLES name remap (nano7kit, MIT). */
#include "shims.h"
#include "fngl.h"
#include "rt_plat.h"
#include "so_load.h"
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <setjmp.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>
#include "zlib.h"

float floorf(float x) { int i = (int)x; if (x < 0.f && (float)i != x) i--; return (float)i; }
float ceilf(float x) { int i = (int)x; if (x > 0.f && (float)i != x) i++; return (float)i; }
float fminf(float a, float b) { return a < b ? a : b; }
float fmaxf(float a, float b) { return a > b ? a : b; }

#ifdef FN_NANO
/* newlib __libc_fini_array (from -nostdlib + libc.a) wants crt0's _fini.
 * __libc_fini is a weak newlib hook; mkrelocapp rejects leftover undefs. */
void _init(void) {}
void _fini(void) {}
void __libc_fini(void) {}
#endif

/* Host (armel) and device libm are already softfp/GPR, matching the SO.
 * Bind through these C wrappers anyway: the .so's sinf/cosf/sqrtf convert
 * to double and call sin@plt, and Box2D rotations go to garbage if a
 * hardfp libm ever returns in s0/d0 instead of r0/r0:r1. */
double ab_sin(double x) { return sin(x); }
double ab_cos(double x) { return cos(x); }
double ab_sqrt(double x) { return sqrt(x); }
double ab_atan2(double y, double x) { return atan2(y, x); }
double ab_floor(double x) { return floor(x); }
double ab_ceil(double x) { return ceil(x); }
double ab_fmod(double x, double y) { return fmod(x, y); }
double ab_acos(double x) { return acos(x); }
double ab_asin(double x) { return asin(x); }
double ab_atan(double x) { return atan(x); }
double ab_cosh(double x) { return cosh(x); }
double ab_sinh(double x) { return sinh(x); }
double ab_tanh(double x) { return tanh(x); }
double ab_tan(double x) { return tan(x); }
double ab_exp(double x) { return exp(x); }
double ab_log(double x) { return log(x); }
double ab_log10(double x) { return log10(x); }
double ab_pow(double x, double y) { return pow(x, y); }
double ab_frexp(double x, int *e) { return frexp(x, e); }
double ab_ldexp(double x, int e) { return ldexp(x, e); }
double ab_modf(double x, double *i) { return modf(x, i); }

/* Bionic exports _ctype_ as a POINTER to a 257-byte table. newlib's _ctype_
 * is the table itself; the names collide in the hbapp. The .so double-derefs
 * GOT[_ctype_], so the reloc must hit a pointer variable, not newlib's table.
 * so_to_obj remaps the import to ab_ctype_ (unique). */
char ab_ctype_tab[257];
short ab_tolower_tab[257], ab_toupper_tab[257];
char *ab_ctype_ = ab_ctype_tab;
short *ab_tolower_tab_ = ab_tolower_tab;
short *ab_toupper_tab_ = ab_toupper_tab;
void *ab_ctype_ptr(void) { return &ab_ctype_; }
uint32_t ab_stack_chk_guard = 0xa5a5a5a5u;
void *ab_dso_handle;
/* Bionic FILE is 84 bytes; libpng fprintfs to __sF+168 (stderr). */
#define BIONIC_FILE 84
char __sF[3][BIONIC_FILE];

static uint32_t heap_now, heap_peak, heap_fail;
static int n_cxa_throw, n_lua_throw, n_persist_lua_throw;
static int persist_compile_depth;
static uint32_t persist_last_len = ~0u;
static char last_io[96];
static int diag_on;
static uint32_t diag_minsp;
static int strtod_nlog;
void ab_diag_set(int on) { diag_on = on ? 1 : 0; }
void ab_diag_reset_logs(void) { strtod_nlog = 0; }
int ab_diag_on(void) { return diag_on; }
uint32_t ab_diag_sp(void) {
    uint32_t sp;
    __asm__ volatile("mov %0, sp" : "=r"(sp));
    if (!diag_minsp || sp < diag_minsp) diag_minsp = sp;
    return sp;
}
uint32_t ab_diag_minsp(void) { return diag_minsp; }
#define AB_THROW_MAX 32
static char throw_sites[AB_THROW_MAX][160];
static int n_throw_sites;
uint32_t ab_heap_used(void) { return heap_now; }
uint32_t ab_heap_peak(void) { return heap_peak; }
uint32_t ab_malloc_failures(void) { return heap_fail; }
int ab_cxa_throw_count(void) { return n_cxa_throw; }
int ab_lua_throw_count(void) { return n_lua_throw; }
int ab_persist_lua_throw_count(void) { return n_persist_lua_throw; }

void ab_note_io(const char *name) {
    const char *s = name ? name : "";
    size_t n = strlen(s);
    if (n >= sizeof last_io) n = sizeof last_io - 1;
    memcpy(last_io, s, n);
    last_io[n] = 0;
}

static void throw_site(const char *kind, const char *detail) {
    char b[160];
    snprintf(b, sizeof b, "THROW_SITE %s %s last='%s'", kind, detail ? detail : "-", last_io);
    plat_log_line(b);
    plat_log_flush();
    if (n_throw_sites < AB_THROW_MAX) {
        snprintf(throw_sites[n_throw_sites], sizeof throw_sites[0], "%s", b);
        n_throw_sites++;
    }
}

void ab_note_lua_throw(int err) {
    char d[32];
    n_lua_throw++;
    if (persist_compile_depth > 0) n_persist_lua_throw++;
    snprintf(d, sizeof d, "err=%d", err);
    throw_site("lua", d);
}

void ab_throw_report(void) {
    char b[128];
    int i;
    snprintf(b, sizeof b, "THROW_REPORT cxa=%d lua=%d persist_lua=%d malloc_fail=%u sites=%d",
             n_cxa_throw, n_lua_throw, n_persist_lua_throw, (unsigned)heap_fail, n_throw_sites);
    plat_log_line(b);
    plat_log_flush();
    for (i = 0; i < n_throw_sites; i++) {
        plat_log_line(throw_sites[i]);
        plat_log_flush();
    }
}

typedef struct { uint32_t mag, n; } ahdr;
#define AMAG 0x41424d48u
#define PMAG 0x41424850u
/* 16-byte header plus 16-up pool base: payload is 16-aligned even when
 * hb_os_alloc returns 4-mod-8 (f2ef5ea). Lua TValue/LDRD/VLDR on a
 * 4-aligned block abort the A5. qemu-arm 8.2 SIGBUS on odd LDRD. */
typedef struct pchunk {
    uint32_t mag, size;
    struct pchunk *next;
    uint32_t pad;
} pchunk;
typedef char ab_pchunk_align[(sizeof(struct pchunk) == 16) ? 1 : -1];
static uint8_t *pool_lo, *pool_hi;
static pchunk *pfree;

void ab_heap_init(void *p, uint32_t n) {
    uintptr_t a = (uintptr_t)p;
    uintptr_t aligned;
    uint32_t loss;
    char b[128];
    pool_lo = pool_hi = 0;
    pfree = 0;
    if (!p || n < 96) return;
    /* Device hb_os_alloc is 4-aligned (f2ef5ea pool payload %8==4).
     * Bump the base to 16 so header+payload stay 8/16-aligned. */
    aligned = (a + 15u) & ~(uintptr_t)15u;
    loss = (uint32_t)(aligned - a);
    if (n <= loss + 64u) return;
    n -= loss;
    n &= ~15u;
    if (n < 64) return;
    pool_lo = (uint8_t *)aligned;
    pool_hi = pool_lo + n;
    pfree = (pchunk *)pool_lo;
    pfree->mag = PMAG;
    pfree->size = n - (uint32_t)sizeof(pchunk);
    pfree->next = 0;
    snprintf(b, sizeof b, "diag pool raw=%p base=%p raw_align=%u base_align=%u size=%u",
             p, (void *)pool_lo, (unsigned)(a & 15u),
             (unsigned)((uintptr_t)pool_lo & 15u), (unsigned)n);
    plat_log_line(b);
    plat_log_flush();
}
int ab_heap_ready(void) { return pool_lo != 0; }

static int in_pool(void *p) {
    uint8_t *a = (uint8_t *)p;
    return pool_lo && a >= pool_lo && a < pool_hi;
}
int ab_heap_contains(const void *p) { return in_pool((void *)p); }

static void pool_align_fatal(uint32_t n, void *p, uint32_t lr) {
    char b[160];
    snprintf(b, sizeof b, "malloc pool payload not 8-aligned n=%u p=%p lr=%08x",
             (unsigned)n, p, (unsigned)lr);
    plat_log_line(b);
    plat_log_flush();
    plat_fatal("malloc align");
}

static void *pool_alloc(uint32_t n, uint32_t lr) {
    pchunk **pp, *c, *rest;
    n = (n + 15u) & ~15u;
    if (!n) n = 16;
    for (pp = &pfree; *pp; pp = &(*pp)->next) {
        c = *pp;
        if (c->size < n) continue;
        if (c->size >= n + (uint32_t)sizeof(pchunk) + 16u) {
            rest = (pchunk *)((uint8_t *)(c + 1) + n);
            rest->mag = PMAG;
            rest->size = c->size - n - (uint32_t)sizeof(pchunk);
            rest->next = c->next;
            c->size = n;
            *pp = rest;
        } else {
            *pp = c->next;
        }
        c->next = 0;
        memset(c + 1, 0, n);
        {
            void *p = c + 1;
            if (((uintptr_t)p & 7u) != 0)
                pool_align_fatal(n, p, lr);
            return p;
        }
    }
    return 0;
}

static void pool_release(void *p) {
    pchunk *c = (pchunk *)p - 1, **pp, *n;
    if (c->mag != PMAG) return;
    for (pp = &pfree; *pp && *pp < c; pp = &(*pp)->next) {}
    c->next = *pp;
    *pp = c;
    if (c->next && (uint8_t *)(c + 1) + c->size == (uint8_t *)c->next) {
        n = c->next;
        c->size += (uint32_t)sizeof(pchunk) + n->size;
        c->next = n->next;
    }
    if (pp != &pfree) {
        pchunk *prev = pfree;
        while (prev->next && prev->next != c) prev = prev->next;
        if (prev->next == c && (uint8_t *)(prev + 1) + prev->size == (uint8_t *)c) {
            prev->size += (uint32_t)sizeof(pchunk) + c->size;
            prev->next = c->next;
        }
    }
}

void *ab_malloc(size_t n) {
    ahdr *h;
    void *p;
    uint32_t lr = (uint32_t)(uintptr_t)__builtin_return_address(0);
    if (!n) n = 1;
    if (n >= 256u * 1024u) {
        char b[80];
        snprintf(b, sizeof b, "malloc %u", (unsigned)n);
        plat_log_line(b);
    }
    if (pool_lo) {
        p = pool_alloc((uint32_t)n, lr);
        if (!p) {
            char b[96];
            heap_fail++;
            snprintf(b, sizeof b, "malloc FAIL pool %u used=%u peak=%u",
                     (unsigned)n, (unsigned)heap_now, (unsigned)heap_peak);
            plat_log_line(b);
            plat_log_flush();
            throw_site("malloc", b);
            return 0;
        }
        heap_now += ((pchunk *)p - 1)->size;
        if (heap_now > heap_peak) heap_peak = heap_now;
        return p;
    }
    h = malloc(n + sizeof *h);
    if (!h) {
        char b[80];
        heap_fail++;
        snprintf(b, sizeof b, "malloc FAIL libc %u", (unsigned)n);
        plat_log_line(b);
        plat_log_flush();
        throw_site("malloc", b);
        return 0;
    }
    h->mag = AMAG; h->n = (uint32_t)n;
    memset(h + 1, 0, n);
    heap_now += (uint32_t)n;
    if (heap_now > heap_peak) heap_peak = heap_now;
    return h + 1;
}
void ab_free(void *p) {
    ahdr *h;
    if (!p) return;
    if (in_pool(p)) {
        pchunk *c = (pchunk *)p - 1;
        uint32_t n = c->size;
        pool_release(p);
        heap_now = heap_now > n ? heap_now - n : 0;
        return;
    }
    h = (ahdr *)p - 1;
    if (h->mag != AMAG) { free(p); return; }
    h->mag = 0;
    heap_now = heap_now > h->n ? heap_now - h->n : 0;
    free(h);
}
void *ab_realloc(void *p, size_t n) {
    void *q; uint32_t old = 0;
    if (!p) return ab_malloc(n);
    if (in_pool(p)) old = ((pchunk *)p - 1)->size;
    else {
        ahdr *h = (ahdr *)p - 1;
        old = h->mag == AMAG ? h->n : 0;
    }
    q = ab_malloc(n);
    if (!q) return 0;
    memcpy(q, p, old < n ? old : n);
    ab_free(p);
    return q;
}
void *ab_calloc(size_t a, size_t n) {
    size_t bytes;
    if (!a || !n) return ab_malloc(1);
    bytes = a * n;
    if (n && bytes / n != a) return 0;
    return ab_malloc(bytes);
}
void *ab_memalign(size_t align, size_t n) {
    if (align < 16) align = 16;
    if (align & (align - 1)) align = 16;
    if (n < align) n = align;
    /* Pool base and block sizes are 16-aligned. align>16 is unused by
     * this .so (no memalign import); still round the request up. */
    n = (n + align - 1) & ~(align - 1);
    return ab_malloc(n);
}
int ab_posix_memalign(void **p, size_t align, size_t n) {
    void *q;
    if (!p) return 22;
    if (align < sizeof(void *) || (align & (align - 1))) return 22;
    q = ab_memalign(align, n);
    if (!q) return 12;
    *p = q;
    return 0;
}

extern int fn_setjmp(void *b) __attribute__((returns_twice));
extern void fn_longjmp(void *b, int v) __attribute__((noreturn));

const char *ab_last_io(void) { return last_io; }

const char *ab_persist_leaf(const char *name) {
    const char *s;
    if (!name || !name[0]) return "";
    s = strrchr(name, '/');
    return (s && s[1]) ? s + 1 : name;
}

int ab_persist_name(const char *name) {
    const char *leaf = ab_persist_leaf(name);
    /* Leaf only: highscores.lua.bak is leftover junk, not a save.
     * highscores.lua skip invalid leftover; highscores.lua empty (missing). */
    return !strcmp(leaf, "settings.lua") || !strcmp(leaf, "highscores.lua");
}

int ab_persist_in_docs(const char *path) {
    const char *files, *slash;
    size_t n, fn;
    if (!path || !path[0] || !ab_persist_name(path)) return 0;
    files = plat_files_dir();
    if (!files || !files[0]) return 0;
    slash = strrchr(path, '/');
    if (!slash) return 0;
    n = (size_t)(slash - path);
    fn = strlen(files);
    return n == fn && memcmp(path, files, n) == 0;
}

static int persist_has(const unsigned char *p, uint32_t n, const char *s) {
    uint32_t k, i;
    if (!p || !s || !s[0]) return 0;
    k = (uint32_t)strlen(s);
    if (n < k) return 0;
    for (i = 0; i + k <= n; i++) if (memcmp(p + i, s, k) == 0) return 1;
    return 0;
}

int ab_persist_lua_ok(const char *name, const void *buf, uint32_t n) {
    const unsigned char *p = (const unsigned char *)buf;
    const char *leaf = ab_persist_leaf(name);
    uint32_t i;
    int brace = 0;
    if (!ab_persist_name(name)) return 1;
    /* n==0 is Android's missing-save: compile skip, never lua_load. */
    if (n == 0) return 1;
    if (!p || n < 4) return 0;
    if (p[0] == 0x1b && p[1] == 'L' && p[2] == 'u' && p[3] == 'a') return 1;
    /* Interior NUL: leftover FAT slack / pool reuse, not Lua text. */
    for (i = 0; i < n; i++) {
        if (p[i] == 0) return 0;
        if (p[i] == '{') brace++;
        else if (p[i] == '}') {
            brace--;
            if (brace < 0) return 0;
        }
    }
    if (brace != 0) return 0;
    if (!strcmp(leaf, "highscores.lua"))
        return persist_has(p, n, "Level");
    return persist_has(p, n, "lastOpenLevel") || persist_has(p, n, "currentLanguage")
        || persist_has(p, n, "audioEnabled") || persist_has(p, n, "gameStarted");
}

int ab_persist_empty(void **out, uint32_t *len) {
    char *p = ab_heap_ready() ? (char *)ab_malloc(8) : (char *)malloc(1);
    if (!p) {
        if (out) *out = 0;
        if (len) *len = 0;
        return -1;
    }
    p[0] = 0;
    if (out) *out = p;
    if (len) *len = 0;
    return 0;
}

void ab_persist_note_open(uint32_t n) { persist_last_len = n; }
int ab_persist_last_empty(void) { return persist_last_len == 0; }
int ab_persist_skip_load(int remaining, const char *name) {
    /* Missing, zero-length, slack-stripped-to-empty, or invalid persist:
     * never lua_load. The LuaTable stays empty, like Android's catch. */
    if (!ab_persist_name(ab_last_io()) && !ab_persist_name(name)) return 0;
    if (remaining <= 0) return 1;
    return persist_last_len == 0;
}
void ab_persist_compile_enter(void) { persist_compile_depth++; }
void ab_persist_compile_leave(void) {
    if (persist_compile_depth > 0) persist_compile_depth--;
}
int ab_persist_in_compile(void) { return persist_compile_depth > 0; }

static void persist_parent_dir(const char *files, char *out, size_t n) {
    const char *s;
    size_t k;
    if (!out || n < 2) return;
    out[0] = 0;
    if (!files || !files[0]) return;
    s = strrchr(files, '/');
    k = s ? (size_t)(s - files) : 0;
    if (!k) return;
    if (k >= n) k = n - 1;
    memcpy(out, files, k);
    out[k] = 0;
}

void ab_persist_prepare(void) {
    static const char *const names[] = { "settings.lua", "highscores.lua" };
    const char *files = plat_files_dir();
    char parent[192], src[192], dst[192], b[192];
    unsigned i;
    if (!files || !files[0]) return;
    plat_mkdir_abs(files);
    persist_parent_dir(files, parent, sizeof parent);
    for (i = 0; i < sizeof names / sizeof names[0]; i++) {
        void *buf = 0;
        uint32_t n = 0;
        snprintf(dst, sizeof dst, "%s/%s", files, names[i]);
        if (plat_exists_abs(dst)) continue;
        if (!parent[0]) continue;
        snprintf(src, sizeof src, "%s/%s", parent, names[i]);
        if (!plat_exists_abs(src)) continue;
        if (plat_read_abs(src, &buf, &n) || !buf) {
            if (buf) free(buf);
            continue;
        }
        snprintf(b, sizeof b, "readFile leftover '%s' %u bytes", src, (unsigned)n);
        plat_log_line(b);
        plat_log_flush();
        if (!ab_persist_lua_ok(names[i], buf, n)) {
            snprintf(b, sizeof b, "%s skip invalid leftover %u bytes", names[i], (unsigned)n);
            plat_log_line(b);
            plat_log_flush();
            plat_log_line("persist leftover not migrated (invalid)");
            plat_log_flush();
            free(buf);
            continue;
        }
        if (plat_write_abs(dst, buf, n)) {
            snprintf(b, sizeof b, "persist migrate write fail '%s'", dst);
            plat_log_line(b);
            plat_log_flush();
            free(buf);
            continue;
        }
        snprintf(b, sizeof b, "persist migrate leftover '%s' %u bytes", src, (unsigned)n);
        plat_log_line(b);
        plat_log_flush();
        snprintf(b, sizeof b, "readFile leftover '%s' requested '%s'", src, dst);
        plat_log_line(b);
        plat_log_flush();
        if (!strcmp(names[i], "settings.lua") && persist_has(buf, n, "lastOpenLevel = 4")) {
            plat_log_line("persist settings lastOpenLevel=4");
            plat_log_flush();
        }
        snprintf(b, sizeof b, "persist %s %u bytes %02x %02x %02x %02x",
                 names[i], (unsigned)n,
                 n > 0 ? ((unsigned char *)buf)[0] : 0,
                 n > 1 ? ((unsigned char *)buf)[1] : 0,
                 n > 2 ? ((unsigned char *)buf)[2] : 0,
                 n > 3 ? ((unsigned char *)buf)[3] : 0);
        plat_log_line(b);
        plat_log_flush();
        free(buf);
    }
}

int ab_inflateInit_(void *s, const char *ver, int n) { return inflateInit_(s, ver, n); }
int ab_inflate(void *s, int f) { return inflate(s, f); }

uint32_t cxa_throw_lit, cxa_throw_cont;
uint32_t ab_throw_lr;

void ab_cxa_throw_log(void *obj, void *ti, void *dest) {
    const char *n = "?";
    char buf[192];
    unsigned *w = (unsigned *)obj;
    unsigned off = 0, ra_off = 0;
    plat_log_line("cxa_throw");
    plat_log_flush();
    if (ti) {
        const char *s = *(const char **)((uint8_t *)ti + 4);
        if (s) n = s;
    }
    if (g_so.base && (uint8_t *)dest >= g_so.base && (uint8_t *)dest < g_so.base + g_so.map_size)
        off = (unsigned)((uint8_t *)dest - g_so.base);
    if (g_so.base && ab_throw_lr >= (uint32_t)(uintptr_t)g_so.base &&
        ab_throw_lr < (uint32_t)(uintptr_t)(g_so.base + g_so.map_size))
        ra_off = ab_throw_lr - (uint32_t)(uintptr_t)g_so.base;
    snprintf(buf, sizeof buf, "cxa_throw type=%s obj=%p dest=%p d_off=0x%x lr=0x%x",
             n, obj, dest, off, ra_off);
    plat_log_line(buf);
    plat_log_flush();
    n_cxa_throw++;
    {
        char d[96];
        snprintf(d, sizeof d, "type=%s lr=0x%x", n, ra_off);
        throw_site("cxa", d);
    }
    /* IOException is the packed-index / persist miss; dump is a String layout. */
    if (n && strstr(n, "lua_longjmp"))
        plat_log_line("lua_longjmp via EHABI (setjmp missed)");
    /* IOException / LuaException / lua_longjmp: no host libc unwinder.
     * Any C++ throw or EHABI unwind is a reboot on the nano. */
    plat_fatal("cxa_throw");
    if (w) {
        snprintf(buf, sizeof buf, "  obj %08x %08x %08x %08x %08x %08x",
                 w[0], w[1], w[2], w[3], w[4], w[5]);
        plat_log_line(buf);
    }
    {
        const char *(*cstr)(void *) = (const char *(*)(void *))so_sym(&g_so, "_ZNK4lang6String5c_strEv");
        if (cstr && obj) {
            const char *msg = cstr(obj);
            if (msg && msg[0]) {
                snprintf(buf, sizeof buf, "  msg %s", msg);
                plat_log_line(buf);
            }
        }
    }
}

void ab_cxa_throw_hook(void);

static void patch_jump(void *fn, void *hook, uint32_t saved[2]) {
    uint32_t *p = (uint32_t *)fn;
    if (!p || !hook) return;
    saved[0] = p[0];
    saved[1] = p[1];
    p[0] = 0xe51ff004u;
    p[1] = (uint32_t)(uintptr_t)hook;
    __builtin___clear_cache((char *)p, (char *)(p + 2));
}
static void unpatch_jump(void *fn, const uint32_t saved[2]) {
    uint32_t *p = (uint32_t *)fn;
    if (!p) return;
    p[0] = saved[0];
    p[1] = saved[1];
    __builtin___clear_cache((char *)p, (char *)(p + 2));
}

static void *fn_begin_catch, *fn_unwind, *fn_alloc;
static uint32_t saved_begin[2], saved_unwind[2], saved_alloc[2];

static void *hook_begin_catch(void *e) __attribute__((noinline));
static void *hook_alloc_exception(unsigned n) __attribute__((noinline));
static int hook_unwind_raise(void *exc, void *ctx) __attribute__((noinline));

static void *hook_begin_catch(void *e) {
    void *r;
    plat_log_line("cxa_begin_catch");
    plat_log_flush();
    unpatch_jump(fn_begin_catch, saved_begin);
    r = ((void *(*)(void *))fn_begin_catch)(e);
    patch_jump(fn_begin_catch, (void *)hook_begin_catch, saved_begin);
    return r;
}
static void *hook_alloc_exception(unsigned n) {
    void *r;
    char b[64];
    snprintf(b, sizeof b, "cxa_allocate %u", n);
    plat_log_line(b);
    plat_log_flush();
    unpatch_jump(fn_alloc, saved_alloc);
    r = ((void *(*)(unsigned))fn_alloc)(n);
    patch_jump(fn_alloc, (void *)hook_alloc_exception, saved_alloc);
    return r;
}
static int hook_unwind_raise(void *exc, void *ctx) {
    (void)exc;
    (void)ctx;
    plat_log_line("unwind raise");
    plat_log_flush();
    plat_fatal("EHABI unwind");
    return 0;
}

static void *so_fn(const char *name, uint32_t off) {
    void *p = so_sym(&g_so, name);
    if (!p && g_so.base) p = g_so.base + off;
    if (!p || (uint8_t *)p < g_so.base || (uint8_t *)p >= g_so.base + g_so.map_size)
        return 0;
    return p;
}

void ab_install_cxa_hook(void) {
    uint32_t *fn;
    if (!g_so.base) return;
    fn = (uint32_t *)so_fn("__cxa_throw", 0x110ea0);
    if (fn) {
        /* First insn is ldr r3, [pc, #128]; literal at fn+0x88. */
        cxa_throw_lit = fn[0x88 / 4];
        cxa_throw_cont = (uint32_t)(uintptr_t)(fn + 2);
        fn[0] = 0xe51ff004u; /* ldr pc, [pc, #-4] */
        fn[1] = (uint32_t)(uintptr_t)ab_cxa_throw_hook;
        __builtin___clear_cache((char *)fn, (char *)(fn + 2));
    }
    fn_begin_catch = so_fn("__cxa_begin_catch", 0x110138);
    if (fn_begin_catch)
        patch_jump(fn_begin_catch, (void *)hook_begin_catch, saved_begin);
    fn_alloc = so_fn("__cxa_allocate_exception", 0x10f864);
    if (fn_alloc)
        patch_jump(fn_alloc, (void *)hook_alloc_exception, saved_alloc);
    fn_unwind = so_fn("__gnu_Unwind_RaiseException", 0x1171e4);
    if (fn_unwind)
        patch_jump(fn_unwind, (void *)hook_unwind_raise, saved_unwind);
    plat_log_line("cxa/unwind hooks on");
    plat_log_flush();
}

void ab_abort(void) {
    void *ra = __builtin_return_address(0);
    unsigned off = 0;
    char buf[96];
    unsigned *sp = (unsigned *)__builtin_frame_address(0);
    int i;
    if (g_so.base && (uint8_t *)ra >= g_so.base && (uint8_t *)ra < g_so.base + g_so.map_size)
        off = (unsigned)((uint8_t *)ra - g_so.base);
    snprintf(buf, sizeof buf, "abort ra=%p so_off=0x%x", ra, off);
    plat_log_line(buf);
    for (i = 0; i < 16 && sp; i++) {
        snprintf(buf, sizeof buf, "  fp[%d]=%08x", i, sp[i]);
        plat_log_line(buf);
    }
    _exit(134);
}
void ab_stack_chk_fail(void) { plat_fatal("stack smash"); }
int *ab_errno(void) { return &errno; }
int ab_cxa_atexit(void (*f)(void *), void *p, void *d) { (void)f; (void)p; (void)d; return 0; }
void ab_cxa_finalize(void *d) { (void)d; }

typedef unsigned int _Unwind_Ptr;
_Unwind_Ptr ab_gnu_Unwind_Find_exidx(_Unwind_Ptr pc, int *pcount) {
    /* get_eit_entry passes PC-2. Tables live in the mapped .so / .abso at
     * runtime base + trailer exidx_off (PREL31 in .ARM.exidx/.ARM.extab is
     * not in DT_REL; relative offsets stay valid after rebase). A PC in a
     * veneer or our install_jump stub is outside the blob. */
    uint8_t *p = (uint8_t *)(uintptr_t)(pc & ~1u);
    static int nlog;
    char b[128];
    plat_log_line("Find_exidx");
    plat_log_flush();
    if (pcount) *pcount = 0;
    if (!g_so.base || !g_so.exidx_count) return 0;
    if (p < g_so.base || p >= g_so.base + g_so.map_size) {
        if (nlog < 8) {
            snprintf(b, sizeof b, "Find_exidx miss pc=%p so=%p..+%u",
                     (void *)p, (void *)g_so.base, (unsigned)g_so.map_size);
            plat_log_line(b);
            plat_log_flush();
            nlog++;
        }
        return 0;
    }
    if (nlog < 3) {
        snprintf(b, sizeof b, "Find_exidx hit pc_off=0x%x n=%u",
                 (unsigned)(p - g_so.base), (unsigned)g_so.exidx_count);
        plat_log_line(b);
        nlog++;
    }
    if (pcount) *pcount = (int)g_so.exidx_count;
    return (_Unwind_Ptr)(uintptr_t)(g_so.base + g_so.exidx_off);
}

int __srget(void *fp) { (void)fp; return EOF; }

void ab_ctype_init(void) {
    static int done; int i;
    if (done) return;
    done = 1;
    ab_ctype_tab[0] = 0;
    ab_tolower_tab[0] = -1;
    ab_toupper_tab[0] = -1;
    for (i = 0; i < 256; i++) {
        unsigned char c = (unsigned char)i;
        char b = 0;
        if (c >= 'A' && c <= 'Z') b |= 0x01;
        if (c >= 'a' && c <= 'z') b |= 0x02;
        if (c >= '0' && c <= '9') b |= 0x04;
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' || c == '\v') b |= 0x08;
        if (c >= 0x21 && c <= 0x7e && !((b & 0x07))) b |= 0x10;
        if (c < 0x20 || c == 0x7f) b |= 0x20;
        if ((c >= '0' && c <= '9') || (c >= 'A' && c <= 'F') || (c >= 'a' && c <= 'f')) b |= 0x40;
        if (c == ' ') b |= 0x80;
        ab_ctype_tab[i + 1] = b;
        ab_tolower_tab[i + 1] = (short)((c >= 'A' && c <= 'Z') ? c + 32 : c);
        ab_toupper_tab[i + 1] = (short)((c >= 'a' && c <= 'z') ? c - 32 : c);
    }
    ab_ctype_ = ab_ctype_tab;
    ab_tolower_tab_ = ab_tolower_tab;
    ab_toupper_tab_ = ab_toupper_tab;
}

/* Locale-free strtod for Lua (LUA_NUMBER is float; luaO_str2d vmov d6,r0,r1
 * then vcvt.f32.f64). Host glibc strtod and newlib strtod (_ctype_ table,
 * __global_locale) are not the same; the .so import is remapped here. */
double ab_strtod(const char *nptr, char **endptr) {
    const unsigned char *p, *start;
    int sign = 1, exp_s = 1, exp_v = 0, digits = 0, frac_n = 0;
    unsigned long ip = 0;
    double v, frac = 1.0;
    ab_ctype_init();
    if (diag_on && strtod_nlog < 24) {
        char b[96];
        snprintf(b, sizeof b, "strtod enter '%s' sp=%08x", nptr ? nptr : "",
                 (unsigned)ab_diag_sp());
        plat_log_line(b);
        plat_log_flush();
    }
    p = (const unsigned char *)(nptr ? nptr : "");
    while (ab_ctype_tab[*p + 1] & 0x08) p++;
    start = p;
    if (*p == '+' || *p == '-') { if (*p == '-') sign = -1; p++; }
    while (*p >= '0' && *p <= '9') {
        ip = ip * 10u + (unsigned)(*p - '0');
        p++;
        digits = 1;
    }
    v = (double)ip;
    if (*p == '.') {
        p++;
        while (*p >= '0' && *p <= '9') {
            v = v * 10.0 + (double)(*p - '0');
            frac_n++;
            p++;
            digits = 1;
        }
        while (frac_n--) frac *= 10.0;
        if (frac > 1.0) v /= frac;
    }
    if (digits && (*p == 'e' || *p == 'E')) {
        const unsigned char *e = p + 1;
        if (*e == '+' || *e == '-') { if (*e == '-') exp_s = -1; e++; }
        if (*e >= '0' && *e <= '9') {
            p = e;
            while (*p >= '0' && *p <= '9') { exp_v = exp_v * 10 + (*p - '0'); p++; }
            {
                double m = 1.0;
                int k = exp_v;
                while (k--) m *= 10.0;
                if (exp_s < 0) v /= m;
                else v *= m;
            }
        }
    }
    if (!digits) {
        if (endptr) *endptr = (char *)nptr;
        v = 0.0;
    } else {
        if (endptr) *endptr = (char *)p;
        v = (double)sign * v;
        (void)start;
    }
    if (diag_on && strtod_nlog < 24) {
        unsigned bits[2];
        char b[96];
        memcpy(bits, &v, 8);
        snprintf(b, sizeof b, "strtod exit bits=%08x %08x end='%s' sp=%08x",
                 bits[0], bits[1], endptr && *endptr ? *endptr : "",
                 (unsigned)ab_diag_sp());
        plat_log_line(b);
        plat_log_flush();
        strtod_nlog++;
    }
    return v;
}

/* FILE handed to the .so is an 84-byte zeroed bionic FILE so
 * AppDataInputStream::available/read ldrh [FILE,#12] is _flags (not nfile
 * pos). Payload sits after. Host and nano share this: leftover I/O is the
 * same plat_read_file + nfile path. Persist writes flush on fclose.
 * sizeof must be 8-aligned: a 204-byte slot made NF[1].file 4-aligned. */
#define MAXF 64
#define NMAG 0x41424e46u
typedef struct {
    char file[BIONIC_FILE];
    uint32_t mag;
    uint8_t *data;
    uint32_t len, pos, cap;
    int write;
    char path[96];
    uint32_t pad;
} nfile;
typedef char ab_nfile_align[((sizeof(nfile) % 8u) == 0) ? 1 : -1];
static nfile NF[MAXF] __attribute__((aligned(8)));
static void io_free(void *p) {
    if (!p) return;
    if (ab_heap_contains(p)) ab_free(p);
    else free(p);
}
/* Persist/small fopen buffers live in the 8-aligned pool on host and
 * device. Host glibc malloc is 16-aligned and hid the 12-byte pchunk
 * LDRD abort; device malloc already went through the pool. */
static void *io_pool_own(void *buf, uint32_t n) {
    void *a;
    if (!buf || !ab_heap_ready()) return buf;
    if (ab_heap_contains(buf) && ((uintptr_t)buf & 7u) == 0) return buf;
    a = ab_malloc(n ? n : 8);
    if (!a) return buf;
    if (n) memcpy(a, buf, n);
    io_free(buf);
    return a;
}

void ab_diag_report(const char *where) {
    char b[192];
    unsigned sp;
    unsigned char *tab;
    void *p8 = 0;
    ab_ctype_init();
    sp = ab_diag_sp();
    tab = (unsigned char *)ab_ctype_tab;
    snprintf(b, sizeof b, "diag report '%s' sp=%08x minsp=%08x",
             where ? where : "", sp, (unsigned)diag_minsp);
    plat_log_line(b);
    plat_log_flush();
    snprintf(b, sizeof b,
             "diag ctype ptr=%p tab=%p *ptr=%p tab[1]=%02x isalpha_A=%d isdigit_1=%d space=%d",
             (void *)&ab_ctype_, (void *)ab_ctype_tab, (void *)ab_ctype_,
             tab[1], (tab['A' + 1] & 0x01) != 0, (tab['1' + 1] & 0x04) != 0,
             (tab[' ' + 1] & 0x08) != 0);
    plat_log_line(b);
    plat_log_flush();
    snprintf(b, sizeof b, "diag pchunk header=%u payload_expect_align=8 heap_ready=%d nfile=%u nfile_align=%u NF0=%p",
             (unsigned)sizeof(pchunk), ab_heap_ready(), (unsigned)sizeof(nfile),
             (unsigned)((uintptr_t)&NF[0] & 7u), (void *)&NF[0]);
    plat_log_line(b);
    plat_log_flush();
    if (ab_heap_ready()) {
        p8 = ab_malloc(8);
        snprintf(b, sizeof b, "diag malloc8 %p align=%u", p8,
                 p8 ? (unsigned)((uintptr_t)p8 & 7u) : 9u);
        plat_log_line(b);
        plat_log_flush();
        if (p8) {
            void *q = ab_realloc(p8, 64);
            snprintf(b, sizeof b, "diag realloc 8->64 %p align=%u", q,
                     q ? (unsigned)((uintptr_t)q & 7u) : 9u);
            plat_log_line(b);
            plat_log_flush();
            if (q) {
                void *r = ab_realloc(q, 16);
                snprintf(b, sizeof b, "diag realloc 64->16 %p align=%u", r,
                         r ? (unsigned)((uintptr_t)r & 7u) : 9u);
                plat_log_line(b);
                plat_log_flush();
                if (r) ab_free(r);
            }
        }
        {
            void *c = ab_calloc(3, 7), *m = ab_memalign(16, 48), *pm = 0;
            int pe = ab_posix_memalign(&pm, 16, 32);
            snprintf(b, sizeof b, "diag calloc/memalign %p a=%u %p a=%u posix=%d %p a=%u",
                     c, c ? (unsigned)((uintptr_t)c & 7u) : 9u,
                     m, m ? (unsigned)((uintptr_t)m & 7u) : 9u,
                     pe, pm, pm ? (unsigned)((uintptr_t)pm & 7u) : 9u);
            plat_log_line(b);
            plat_log_flush();
            if (c) ab_free(c);
            if (m) ab_free(m);
            if (pm) ab_free(pm);
        }
    }
    {
        double d = ab_strtod("1.5", 0);
        unsigned bits[2];
        memcpy(bits, &d, 8);
        snprintf(b, sizeof b, "diag strtod 1.5 bits=%08x %08x (expect 00000000 3ff80000)",
                 bits[0], bits[1]);
        plat_log_line(b);
        plat_log_flush();
    }
#ifndef FN_NANO
    /* qemu-arm 8.2 SIGBUS on odd-address LDRD (observed pc in this probe,
     * addr&7!=0). Do not execute it: the harness crash handler _exit(139)s.
     * SCTLR.A is not a qemu-user CPU property. Pool payloads are 8-aligned
     * so Lua TValue/LDRD/VLDR should not take this fault. */
    plat_log_line("HOST_PROBE ldrd-unaligned qemu=SIGBUS (not executed; odd LDRD aborts this qemu and the A5)");
    plat_log_flush();
    plat_log_line("HOST_PROBE emulate: pool 16-up=yes 16-round=yes strtod=ab_strtod ctype=ab_ctype_ glibc_strtod=no qemu_sctlr_a=ldrd-traps");
    plat_log_flush();
#else
    plat_log_line("diag ldrd-unaligned skipped (would reboot the nano)");
    plat_log_flush();
#endif
}

static nfile *nf_of(void *p) {
    int i;
    if (!p) return 0;
    for (i = 0; i < MAXF; i++)
        if (NF[i].mag == NMAG && (p == (void *)NF[i].file || p == (void *)&NF[i]))
            return &NF[i];
    return 0;
}
static void persist_miss_log(const char *path) {
    const char *leaf = ab_persist_leaf(path);
    char b[80];
    snprintf(b, sizeof b, "%s empty (missing)", leaf[0] ? leaf : "persist");
    plat_log_line(b);
    plat_log_flush();
}
void *ab_fopen(const char *path, const char *mode) {
    nfile *w = 0; int i, wr;
    const char *leaf;
    if (!path) return 0;
    ab_note_io(path);
    fe_note_texfile(path);
    wr = mode && (strchr(mode, 'w') || strchr(mode, 'a') || strchr(mode, '+'));
    for (i = 0; i < MAXF; i++) if (NF[i].mag != NMAG) { w = &NF[i]; break; }
    if (!w) return 0;
    memset(w, 0, sizeof *w);
    w->mag = NMAG;
    leaf = ab_persist_leaf(path);
    if (ab_persist_name(path) && leaf[0])
        snprintf(w->path, sizeof w->path, "%s/%s", plat_files_dir(), leaf);
    else {
        size_t n = strlen(path);
        if (n >= sizeof w->path) n = sizeof w->path - 1;
        memcpy(w->path, path, n);
        w->path[n] = 0;
    }
    if (!wr) {
        void *buf = 0; uint32_t n = 0;
        char hb[160];
        if (plat_read_file(path, &buf, &n)) {
        if (ab_persist_name(path) && ab_persist_empty(&buf, &n) == 0)
            persist_miss_log(path);
        else {
            w->mag = 0;
            return 0;
        }
        }
        if (buf && (ab_persist_name(path) || n < 4096u))
            buf = io_pool_own(buf, n);
        w->data = buf; w->len = n; w->cap = n ? n : 1;
        if (ab_persist_name(path)) ab_persist_note_open(n);
        if (ab_persist_name(path) || strstr(path, "settings.lua")) {
            unsigned fl = (unsigned char)w->file[12] | ((unsigned)(unsigned char)w->file[13] << 8);
            snprintf(hb, sizeof hb, "nfile open '%s' len=%u flags=%u %02x %02x %02x %02x",
                     path, (unsigned)n, fl,
                     n > 0 && buf ? ((unsigned char *)buf)[0] : 0,
                     n > 1 && buf ? ((unsigned char *)buf)[1] : 0,
                     n > 2 && buf ? ((unsigned char *)buf)[2] : 0,
                     n > 3 && buf ? ((unsigned char *)buf)[3] : 0);
            plat_log_line(hb);
            plat_log_flush();
            snprintf(hb, sizeof hb, "nfile fopen return ptr=%p align=%u data=%p dalign=%u nfile=%u sp=%08x",
                     (void *)w->file, (unsigned)((uintptr_t)w->file & 7u),
                     (void *)w->data, (unsigned)((uintptr_t)(w->data ? w->data : 0) & 7u),
                     (unsigned)sizeof(nfile), (unsigned)ab_diag_sp());
            plat_log_line(hb);
            plat_log_flush();
            if (w->data && ((uintptr_t)w->data & 7u)) {
                plat_log_line("nfile data NOT 8-aligned");
                plat_log_flush();
            }
            if ((uintptr_t)w->file & 7u) {
                plat_log_line("nfile FILE NOT 8-aligned");
                plat_log_flush();
            }
        }
    } else {
        w->write = 1;
        w->data = ab_heap_ready() ? ab_malloc(256) : calloc(1, 256);
        w->cap = 256;
        if (ab_persist_name(path)) plat_mkdir_abs(plat_files_dir());
    }
    return w->file;
}
int ab_fclose(void *fp) {
    nfile *w = nf_of(fp);
    if (!w) return -1;
    if (w->write && w->path[0] && w->data)
        plat_write_abs(w->path, w->data, w->len);
    io_free(w->data);
    w->mag = 0; w->data = 0;
    return 0;
}
size_t ab_fread(void *ptr, size_t s, size_t n, void *fp) {
    nfile *w = nf_of(fp);
    size_t want, got;
    if (!w || !s) return 0;
    want = s * n;
    if (w->pos >= w->len) return 0;
    got = w->len - w->pos;
    if (got > want) got = want;
    got -= got % s;
    memcpy(ptr, w->data + w->pos, got);
    w->pos += (uint32_t)got;
    return got / s;
}
size_t ab_fwrite(const void *ptr, size_t s, size_t n, void *fp) {
    nfile *w = nf_of(fp);
    size_t want;
    if (!w || !s) return 0;
    want = s * n;
    if (w->pos + want > w->cap) {
        uint32_t cap = w->cap ? w->cap : 256;
        uint8_t *q;
        while (cap < w->pos + want) cap *= 2;
        q = ab_heap_contains(w->data) ? ab_realloc(w->data, cap) : realloc(w->data, cap);
        if (!q) return 0;
        w->data = q; w->cap = cap;
    }
    memcpy(w->data + w->pos, ptr, want);
    w->pos += (uint32_t)want;
    if (w->pos > w->len) w->len = w->pos;
    return n;
}
int ab_fseek(void *fp, long off, int wh) {
    nfile *w = nf_of(fp);
    long p;
    if (!w) return -1;
    p = wh == SEEK_END ? (long)w->len + off : wh == SEEK_CUR ? (long)w->pos + off : off;
    if (p < 0) return -1;
    w->pos = (uint32_t)p;
    return 0;
}
long ab_ftell(void *fp) { nfile *w = nf_of(fp); return w ? (long)w->pos : -1L; }
int ab_fflush(void *fp) {
    nfile *w = nf_of(fp);
    if (w && w->write && w->path[0] && w->data)
        plat_write_abs(w->path, w->data, w->len);
    return 0;
}
char *ab_fgets(char *s, int n, void *fp) {
    int i = 0;
    if (!s || n <= 0) return 0;
    while (i < n - 1) {
        char c;
        if (ab_fread(&c, 1, 1, fp) != 1) break;
        s[i++] = c;
        if (c == '\n') break;
    }
    s[i] = 0;
    return i ? s : 0;
}
int ab_fputc(int c, void *fp) { char b = (char)c; return ab_fwrite(&b, 1, 1, fp) == 1 ? c : EOF; }
int ab_fputs(const char *s, void *fp) {
    size_t n = strlen(s);
    return ab_fwrite(s, 1, n, fp) == n ? 0 : EOF;
}
int ab_ungetc(int c, void *fp) {
    nfile *w = nf_of(fp);
    if (!w || !w->pos) return EOF;
    w->pos--;
    w->data[w->pos] = (uint8_t)c;
    return c;
}
int ab_setvbuf(void *fp, char *b, int m, size_t s) { (void)fp; (void)b; (void)m; (void)s; return 0; }
void *ab_freopen(const char *p, const char *m, void *fp) { (void)p; (void)m; return fp; }


int ab_fprintf(void *fp, const char *fmt, ...) {
    char buf[512]; va_list ap;
    va_start(ap, fmt); vsnprintf(buf, sizeof buf, fmt, ap); va_end(ap);
    plat_log_line(buf);
    if (fp) ab_fputs(buf, fp);
    return (int)strlen(buf);
}
int ab_fscanf(void *fp, const char *fmt, ...) {
    va_list ap; int n;
    nfile *w = nf_of(fp);
    if (!w || !w->data) return EOF;
    va_start(ap, fmt);
    n = vsscanf((char *)w->data + w->pos, fmt, ap);
    va_end(ap);
    return n;
}

int ab_printf(const char *fmt, ...) {
    char buf[512]; va_list ap;
    va_start(ap, fmt); vsnprintf(buf, sizeof buf, fmt, ap); va_end(ap);
    plat_log_line(buf);
    return (int)strlen(buf);
}

int ab_gettimeofday(void *tv, void *tz) {
    uint64_t us = plat_time_us();
    uint32_t *t = (uint32_t *)tv;
    (void)tz;
    if (!t) return -1;
    t[0] = (uint32_t)(us / 1000000ull);
    t[1] = (uint32_t)(us % 1000000ull);
    return 0;
}
int32_t ab_time(int32_t *t) {
    int32_t s = (int32_t)(plat_time_us() / 1000000ull);
    if (t) *t = s;
    return s;
}
clock_t ab_clock(void) { return (clock_t)plat_time_us(); }
int ab_usleep(unsigned int us) { (void)us; return 0; }
int ab_select(int n, void *a, void *b, void *c, void *t) { (void)n; (void)a; (void)b; (void)c; (void)t; return 0; }
int ab_system(const char *s) { (void)s; return -1; }
char *ab_getenv(const char *n) { (void)n; return 0; }
char *ab_setlocale(int c, const char *l) { (void)c; (void)l; return (char *)"C"; }
void *ab_tmpfile(void) { return ab_fopen("abtmp", "wb+"); }
char *ab_tmpnam(char *s) { static char b[20]; snprintf(s ? s : b, 20, "abtmp"); return s ? s : b; }

int ab_open(const char *path, int flags, ...) {
    (void)flags;
    return ab_fopen(path, (flags & 1) ? "wb+" : "rb") ? 3 : -1;
}
int ab_close(int fd) {
#ifdef FN_NANO
    (void)fd; return 0;
#else
    return close(fd);
#endif
}
ssize_t ab_read(int fd, void *b, size_t n) {
#ifdef FN_NANO
    (void)fd; (void)b; (void)n; return 0;
#else
    return read(fd, b, n);
#endif
}
ssize_t ab_write(int fd, const void *b, size_t n) {
#ifdef FN_NANO
    (void)fd; (void)b; return (ssize_t)n;
#else
    return write(fd, b, n);
#endif
}
off_t ab_lseek(int fd, off_t o, int w) {
#ifdef FN_NANO
    (void)fd; (void)o; (void)w; return 0;
#else
    return lseek(fd, o, w);
#endif
}
int ab_fcntl(int fd, int cmd, ...) { (void)fd; (void)cmd; return 0; }
int ab_remove(const char *p) {
#ifdef FN_NANO
    (void)p; return 0;
#else
    return remove(p);
#endif
}
int ab_rename(const char *a, const char *b) {
#ifdef FN_NANO
    (void)a; (void)b; return 0;
#else
    return rename(a, b);
#endif
}

int ab_pthread_create(void *t, const void *a, void *(*fn)(void *), void *arg) {
    (void)t; (void)a; (void)fn; (void)arg; return 11;
}
void ab_pthread_exit(void *r) { (void)r; }
int ab_pthread_join(void *t, void **r) { (void)t; (void)r; return 0; }
int ab_pthread_mutex_init(void *m, const void *a) { (void)m; (void)a; return 0; }
int ab_pthread_mutex_destroy(void *m) { (void)m; return 0; }
int ab_pthread_mutex_lock(void *m) { (void)m; return 0; }
int ab_pthread_mutex_trylock(void *m) { (void)m; return 0; }
int ab_pthread_mutex_unlock(void *m) { (void)m; return 0; }
int ab_pthread_mutexattr_init(void *a) { (void)a; return 0; }
int ab_pthread_mutexattr_destroy(void *a) { (void)a; return 0; }
int ab_pthread_mutexattr_settype(void *a, int t) { (void)a; (void)t; return 0; }
static void *tls[32];
int ab_pthread_key_create(unsigned *k, void (*d)(void *)) {
    static unsigned n = 1;
    (void)d;
    if (n >= 32) return 11;
    *k = n++;
    return 0;
}
int ab_pthread_key_delete(unsigned k) { (void)k; return 0; }
int ab_pthread_setspecific(unsigned k, const void *v) { if (k < 32) tls[k] = (void *)v; return 0; }
void *ab_pthread_getspecific(unsigned k) { return k < 32 ? tls[k] : 0; }
int ab_pthread_getschedparam(void *t, int *p, void *sp) { (void)t; (void)p; (void)sp; return 0; }
int ab_pthread_setschedparam(void *t, int p, const void *sp) { (void)t; (void)p; (void)sp; return 0; }
int ab_sched_yield(void) { return 0; }

static long ab_seed48 = 1;
void ab_srand48(long s) { ab_seed48 = s ? s : 1; }
long ab_lrand48(void) { ab_seed48 = ab_seed48 * 1103515245 + 12345; return (ab_seed48 >> 1) & 0x7fffffff; }

typedef struct { const char *n; void *p; } rec;
#define R(s) { #s, (void *)s }
#define W(s) { #s, (void *)s }
#define A(s, p) { #s, (void *)p }

static rec tab[] = {
    A(acos, ab_acos), A(asin, ab_asin), A(atan, ab_atan), A(atan2, ab_atan2),
    A(ceil, ab_ceil), A(cos, ab_cos), A(cosh, ab_cosh), A(exp, ab_exp),
    A(floor, ab_floor), A(fmod, ab_fmod), A(frexp, ab_frexp), A(ldexp, ab_ldexp),
    A(log, ab_log), A(log10, ab_log10), A(modf, ab_modf), A(pow, ab_pow),
    A(sin, ab_sin), A(sinh, ab_sinh), A(sqrt, ab_sqrt), A(tan, ab_tan), A(tanh, ab_tanh),
    A(malloc, ab_malloc), A(free, ab_free), A(realloc, ab_realloc),
    A(calloc, ab_calloc), A(memalign, ab_memalign), A(posix_memalign, ab_posix_memalign),
    A(aligned_alloc, ab_memalign),
    A(_Znwj, ab_malloc), A(_Znaj, ab_malloc), A(_ZdlPv, ab_free), A(_ZdaPv, ab_free),
    R(memcpy), R(memmove), R(memset), R(memcmp), R(memchr),
    R(strcpy), R(strncpy), R(strcat), R(strncat), R(strcmp), R(strncmp),
    R(strcasecmp), R(strchr), R(strrchr), R(strstr), R(strpbrk), R(strcspn),
    R(strlen), A(strtod, ab_strtod), R(strtol), R(strtoul), R(strtok), R(strerror),
    R(strcoll), R(strftime),
    A(printf, ab_printf), A(sprintf, sprintf), A(snprintf, snprintf),
    A(fopen, ab_fopen), A(fclose, ab_fclose), A(fread, ab_fread), A(fwrite, ab_fwrite),
    A(fseek, ab_fseek), A(ftell, ab_ftell), A(fflush, ab_fflush), A(fgets, ab_fgets),
    A(fputc, ab_fputc), A(fputs, ab_fputs), A(ungetc, ab_ungetc), A(setvbuf, ab_setvbuf),
    A(freopen, ab_freopen), A(fprintf, ab_fprintf), A(fscanf, ab_fscanf),
    A(tmpfile, ab_tmpfile), A(tmpnam, ab_tmpnam),
    A(open, ab_open), A(close, ab_close), A(read, ab_read), A(write, ab_write),
    A(lseek, ab_lseek), A(fcntl, ab_fcntl), A(remove, ab_remove), A(rename, ab_rename),
    A(gettimeofday, ab_gettimeofday), A(time, ab_time), A(clock, ab_clock),
    R(gmtime), R(localtime), R(mktime), R(difftime),
    A(usleep, ab_usleep), A(select, ab_select), A(system, ab_system),
    A(getenv, ab_getenv), A(setlocale, ab_setlocale),
    A(abort, ab_abort), R(exit), A(setjmp, fn_setjmp), A(longjmp, fn_longjmp),
    A(pthread_create, ab_pthread_create), A(pthread_exit, ab_pthread_exit), A(pthread_join, ab_pthread_join),
    A(pthread_mutex_init, ab_pthread_mutex_init), A(pthread_mutex_destroy, ab_pthread_mutex_destroy),
    A(pthread_mutex_lock, ab_pthread_mutex_lock), A(pthread_mutex_trylock, ab_pthread_mutex_trylock),
    A(pthread_mutex_unlock, ab_pthread_mutex_unlock),
    A(pthread_mutexattr_init, ab_pthread_mutexattr_init),
    A(pthread_mutexattr_destroy, ab_pthread_mutexattr_destroy),
    A(pthread_mutexattr_settype, ab_pthread_mutexattr_settype),
    A(pthread_key_create, ab_pthread_key_create), A(pthread_key_delete, ab_pthread_key_delete),
    A(pthread_setspecific, ab_pthread_setspecific), A(pthread_getspecific, ab_pthread_getspecific),
    A(pthread_getschedparam, ab_pthread_getschedparam),
    A(pthread_setschedparam, ab_pthread_setschedparam), A(sched_yield, ab_sched_yield),
    A(srand48, ab_srand48), A(lrand48, ab_lrand48),
    R(crc32), A(inflate, ab_inflate), A(inflateEnd, inflateEnd),
    A(inflateInit_, ab_inflateInit_), A(inflateReset, inflateReset),
    A(_ctype_, &ab_ctype_), A(ab_ctype_, &ab_ctype_),
    A(_tolower_tab_, &ab_tolower_tab_), A(ab_tolower_tab_, &ab_tolower_tab_),
    A(_toupper_tab_, &ab_toupper_tab_), A(ab_toupper_tab_, &ab_toupper_tab_),
    R(__sF), R(__srget), A(__errno, ab_errno), A(__stack_chk_guard, &ab_stack_chk_guard),
    A(__stack_chk_fail, ab_stack_chk_fail), A(__cxa_atexit, ab_cxa_atexit),
    A(__dso_handle, &ab_dso_handle), A(__gnu_Unwind_Find_exidx, ab_gnu_Unwind_Find_exidx),
    A(glActiveTexture, ab_glActiveTexture), A(glBindBuffer, ab_glBindBuffer),
    A(glBindFramebufferOES, ab_glBindFramebufferOES), A(glBindRenderbufferOES, ab_glBindRenderbufferOES),
    A(glBindTexture, ab_glBindTexture), A(glBlendFunc, ab_glBlendFunc), A(glBufferData, ab_glBufferData),
    A(glCheckFramebufferStatusOES, ab_glCheckFramebufferStatusOES), A(glClear, ab_glClear),
    A(glClearColor, ab_glClearColor), A(glClientActiveTexture, ab_glClientActiveTexture),
    A(glColor4f, ab_glColor4f), A(glColorMask, ab_glColorMask), A(glColorPointer, ab_glColorPointer),
    A(glCompressedTexImage2D, ab_glCompressedTexImage2D), A(glDeleteBuffers, ab_glDeleteBuffers),
    A(glDeleteFramebuffersOES, ab_glDeleteFramebuffersOES), A(glDeleteRenderbuffersOES, ab_glDeleteRenderbuffersOES),
    A(glDeleteTextures, ab_glDeleteTextures), A(glDepthFunc, ab_glDepthFunc), A(glDepthMask, ab_glDepthMask),
    A(glDisable, ab_glDisable), A(glDisableClientState, ab_glDisableClientState),
    A(glDrawArrays, ab_glDrawArrays), A(glDrawElements, ab_glDrawElements),
    A(glEnable, ab_glEnable), A(glEnableClientState, ab_glEnableClientState),
    A(glFinish, ab_glFinish), A(glFramebufferRenderbufferOES, ab_glFramebufferRenderbufferOES),
    A(glFramebufferTexture2DOES, ab_glFramebufferTexture2DOES), A(glFrontFace, ab_glFrontFace),
    A(glGenBuffers, ab_glGenBuffers), A(glGenFramebuffersOES, ab_glGenFramebuffersOES),
    A(glGenRenderbuffersOES, ab_glGenRenderbuffersOES), A(glGenTextures, ab_glGenTextures),
    A(glGetIntegerv, ab_glGetIntegerv), A(glGetString, ab_glGetString),
    A(glLightfv, ab_glLightfv), A(glLineWidth, ab_glLineWidth), A(glLoadMatrixf, ab_glLoadMatrixf),
    A(glMaterialfv, ab_glMaterialfv), A(glMatrixMode, ab_glMatrixMode),
    A(glNormalPointer, ab_glNormalPointer), A(glPixelStorei, ab_glPixelStorei),
    A(glRenderbufferStorageOES, ab_glRenderbufferStorageOES), A(glScissor, ab_glScissor),
    A(glTexCoordPointer, ab_glTexCoordPointer), A(glTexEnvi, ab_glTexEnvi),
    A(glTexImage2D, ab_glTexImage2D), A(glTexParameteri, ab_glTexParameteri),
    A(glTexSubImage2D, ab_glTexSubImage2D), A(glVertexPointer, ab_glVertexPointer),
    A(glViewport, ab_glViewport),
};

static int rec_cmp(const void *a, const void *b) {
    return strcmp(((const rec *)a)->n, ((const rec *)b)->n);
}
static int sorted;

void *ab_resolve(const char *name) {
    rec key, *hit;
    ab_ctype_init();
    if (!name) return 0;
    if (!sorted) { qsort(tab, sizeof tab / sizeof tab[0], sizeof tab[0], rec_cmp); sorted = 1; }
    key.n = name; key.p = 0;
    hit = bsearch(&key, tab, sizeof tab / sizeof tab[0], sizeof tab[0], rec_cmp);
    return hit ? hit->p : 0;
}

void ab_shims_reset(void) {
    heap_now = heap_peak = heap_fail = 0;
    n_cxa_throw = n_lua_throw = n_persist_lua_throw = n_throw_sites = 0;
    persist_compile_depth = 0;
    persist_last_len = ~0u;
    last_io[0] = 0;
    diag_on = 0;
    diag_minsp = 0;
    strtod_nlog = 0;
    memset(NF, 0, sizeof NF);
    memset(tls, 0, sizeof tls);
}
