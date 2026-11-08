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

/* Host (armel) and device libm are already softfp/GPR, matching the SO. */

/* Bionic exports these as pointers (GOT is the address of the pointer). */
char ab_ctype_tab[257];
short ab_tolower_tab[257], ab_toupper_tab[257];
char *_ctype_ = ab_ctype_tab;
short *_tolower_tab_ = ab_tolower_tab;
short *_toupper_tab_ = ab_toupper_tab;
uint32_t ab_stack_chk_guard = 0xa5a5a5a5u;
void *ab_dso_handle;
/* Bionic FILE is 84 bytes; libpng fprintfs to __sF+168 (stderr). */
#define BIONIC_FILE 84
char __sF[3][BIONIC_FILE];

static uint32_t heap_now, heap_peak, heap_fail;
uint32_t ab_heap_used(void) { return heap_now; }
uint32_t ab_heap_peak(void) { return heap_peak; }
uint32_t ab_malloc_failures(void) { return heap_fail; }

typedef struct { uint32_t mag, n; } ahdr;
#define AMAG 0x41424d48u
#define PMAG 0x41424850u
typedef struct pchunk { uint32_t mag, size; struct pchunk *next; } pchunk;
static uint8_t *pool_lo, *pool_hi;
static pchunk *pfree;

void ab_heap_init(void *p, uint32_t n) {
    if (!p || n < 64) { pool_lo = pool_hi = 0; pfree = 0; return; }
    n &= ~7u;
    pool_lo = (uint8_t *)p;
    pool_hi = pool_lo + n;
    pfree = (pchunk *)pool_lo;
    pfree->mag = PMAG;
    pfree->size = n - (uint32_t)sizeof(pchunk);
    pfree->next = 0;
}

static int in_pool(void *p) {
    uint8_t *a = (uint8_t *)p;
    return pool_lo && a >= pool_lo && a < pool_hi;
}

static void *pool_alloc(uint32_t n) {
    pchunk **pp, *c, *rest;
    n = (n + 7u) & ~7u;
    if (!n) n = 8;
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
        return c + 1;
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
    if (!n) n = 1;
    if (n >= 256u * 1024u) {
        char b[80];
        snprintf(b, sizeof b, "malloc %u", (unsigned)n);
        plat_log_line(b);
    }
    if (pool_lo) {
        p = pool_alloc((uint32_t)n);
        if (!p) { heap_fail++; return 0; }
        heap_now += ((pchunk *)p - 1)->size;
        if (heap_now > heap_peak) heap_peak = heap_now;
        return p;
    }
    h = malloc(n + sizeof *h);
    if (!h) { heap_fail++; return 0; }
    h->mag = AMAG; h->n = (uint32_t)n;
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

extern int fn_setjmp(void *b);
extern void fn_longjmp(void *b, int v);

int ab_inflateInit_(void *s, const char *ver, int n) { return inflateInit_(s, ver, n); }
int ab_inflate(void *s, int f) { return inflate(s, f); }

uint32_t cxa_throw_lit, cxa_throw_cont;
uint32_t ab_throw_lr;

void ab_cxa_throw_log(void *obj, void *ti, void *dest) {
    const char *n = "?";
    char buf[192];
    unsigned *w = (unsigned *)obj;
    unsigned off = 0, ra_off = 0;
    if (ti) {
        const char *s = *(const char **)((uint8_t *)ti + 4);
        if (s) n = s;
    }
    if (g_so.base && (uint8_t *)dest >= g_so.base && (uint8_t *)dest < g_so.base + g_so.map_size)
        off = (unsigned)((uint8_t *)dest - g_so.base);
    if (g_so.base && ab_throw_lr >= (uint32_t)(uintptr_t)g_so.base &&
        ab_throw_lr < (uint32_t)(uintptr_t)(g_so.base + g_so.map_size))
        ra_off = ab_throw_lr - (uint32_t)(uintptr_t)g_so.base;
    /* bundleIndex.idx / persist files throw IOException that GameApp catches. */
    if (n && n[0] == 'N' && strstr(n, "IOException"))
        return;
    snprintf(buf, sizeof buf, "cxa_throw type=%s obj=%p dest=%p d_off=0x%x lr=0x%x",
             n, obj, dest, off, ra_off);
    plat_log_line(buf);
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

void ab_install_cxa_hook(void) {
    uint32_t *fn;
    if (!g_so.base) return;
    fn = (uint32_t *)so_sym(&g_so, "__cxa_throw");
    if (!fn) fn = (uint32_t *)(g_so.base + 0x110ea0);
    if ((uint8_t *)fn < g_so.base || (uint8_t *)fn >= g_so.base + g_so.map_size) return;
    /* First insn is ldr r3, [pc, #128]; literal at fn+0x88. */
    cxa_throw_lit = fn[0x88 / 4];
    cxa_throw_cont = (uint32_t)(uintptr_t)(fn + 2);
    fn[0] = 0xe51ff004u; /* ldr pc, [pc, #-4] */
    fn[1] = (uint32_t)(uintptr_t)ab_cxa_throw_hook;
    __builtin___clear_cache((char *)fn, (char *)(fn + 2));
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
    uint8_t *p = (uint8_t *)(uintptr_t)pc;
    if (pcount) *pcount = 0;
    if (!g_so.base || !g_so.exidx_count) return 0;
    if (p < g_so.base || p >= g_so.base + g_so.map_size) return 0;
    if (pcount) *pcount = (int)g_so.exidx_count;
    return (_Unwind_Ptr)(uintptr_t)(g_so.base + g_so.exidx_off);
}

int __srget(void *fp) { (void)fp; return EOF; }

static void ctype_init(void) {
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
    _ctype_ = ab_ctype_tab;
    _tolower_tab_ = ab_tolower_tab;
    _toupper_tab_ = ab_toupper_tab;
}

/* ---- FILE: PLT fopen/fread never touch bionic layout; __sF is stdin/out/err. */
#define MAXF 64
#define FMAG 0x41424649u
typedef struct { uint32_t mag; FILE *real; char b[96]; } wrapf;
static wrapf F[MAXF];

static wrapf *wf_of(void *p) {
    int i;
    if (!p) return 0;
    for (i = 0; i < MAXF; i++) if (F[i].mag == FMAG && (p == (void *)F[i].b || p == (void *)&F[i])) return &F[i];
    return 0;
}
static FILE *real_fp(void *p) {
    wrapf *w = wf_of(p);
    if (w) return w->real;
    if (p == (void *)__sF[0] || p == (void *)__sF) return stdin;
    if (p == (void *)__sF[1] || p == (void *)((char *)__sF + BIONIC_FILE)) return stdout;
    if (p == (void *)__sF[2] || p == (void *)((char *)__sF + 2 * BIONIC_FILE)) return stderr;
    return (FILE *)p;
}

static const char *strip_data(const char *name) {
    const char *base = name, *d, *slash;
    while ((d = strstr(base, "data/"))) base = d + 5;
    slash = strrchr(name, '/');
    (void)slash;
    return base;
}

#ifndef FN_NANO
static FILE *try_open(const char *path, const char *mode) {
    FILE *r;
    char p[512], p2[512];
    const char *base, *slash;
    if (!path) return 0;
    snprintf(p, sizeof p, "%s/%s", plat_files_dir(), path);
    if ((r = fopen(p, mode))) return r;
    snprintf(p, sizeof p, "%s/%s", plat_data_dir(), path);
    if ((r = fopen(p, mode))) return r;
    base = strip_data(path);
    if (base != path) {
        snprintf(p, sizeof p, "%s/%s", plat_data_dir(), base);
        if ((r = fopen(p, mode))) return r;
    }
    slash = strrchr(path, '/');
    if (slash) {
        snprintf(p, sizeof p, "%s/%s", plat_data_dir(), slash + 1);
        if ((r = fopen(p, mode))) return r;
    }
    if ((r = fopen(path, mode))) return r;
    /* Lua files are often named without the suffix in Ka3D logs. */
    if (!strchr(path, '.')) {
        snprintf(p2, sizeof p2, "%s.lua", path);
        if ((r = try_open(p2, mode))) return r;
    }
    return 0;
}
#endif

#ifdef FN_NANO
typedef struct {
    uint32_t mag;
    uint8_t *data;
    uint32_t len, pos, cap;
    int write;
} nfile;
#define NMAG 0x41424e46u
static nfile NF[MAXF];
static nfile *nf_of(void *p) {
    int i;
    if (!p) return 0;
    for (i = 0; i < MAXF; i++) if (NF[i].mag == NMAG && p == (void *)&NF[i]) return &NF[i];
    return 0;
}
void *ab_fopen(const char *path, const char *mode) {
    nfile *w = 0; int i, wr;
    if (!path) return 0;
    fe_note_texfile(path);
    wr = mode && (strchr(mode, 'w') || strchr(mode, 'a') || strchr(mode, '+'));
    for (i = 0; i < MAXF; i++) if (NF[i].mag != NMAG) { w = &NF[i]; break; }
    if (!w) return 0;
    memset(w, 0, sizeof *w);
    w->mag = NMAG;
    if (!wr) {
        void *buf = 0; uint32_t n = 0;
        if (plat_read_file(path, &buf, &n)) { w->mag = 0; return 0; }
        w->data = buf; w->len = n; w->cap = n;
    } else {
        w->write = 1;
        w->data = calloc(1, 256);
        w->cap = 256;
    }
    return w;
}
int ab_fclose(void *fp) {
    nfile *w = nf_of(fp);
    if (!w) return -1;
    free(w->data);
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
        q = realloc(w->data, cap);
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
int ab_fflush(void *fp) { (void)fp; return 0; }
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
#else
void *ab_fopen(const char *path, const char *mode) {
    wrapf *w = 0; FILE *r; int i;
    if (!path) return 0;
    fe_note_texfile(path);
    r = try_open(path, mode);
    if (!r) return 0;
    for (i = 0; i < MAXF; i++) if (F[i].mag != FMAG) { w = &F[i]; break; }
    if (!w) { fclose(r); return 0; }
    memset(w, 0, sizeof *w);
    w->mag = FMAG; w->real = r;
    return w->b;
}
int ab_fclose(void *fp) {
    wrapf *w = wf_of(fp);
    int rc;
    if (!w) return fclose(real_fp(fp));
    rc = fclose(w->real);
    w->mag = 0; w->real = 0;
    return rc;
}
size_t ab_fread(void *ptr, size_t s, size_t n, void *fp) { return fread(ptr, s, n, real_fp(fp)); }
size_t ab_fwrite(const void *ptr, size_t s, size_t n, void *fp) { return fwrite(ptr, s, n, real_fp(fp)); }
int ab_fseek(void *fp, long off, int wh) { return fseek(real_fp(fp), off, wh); }
long ab_ftell(void *fp) { return ftell(real_fp(fp)); }
int ab_fflush(void *fp) { return fflush(real_fp(fp)); }
char *ab_fgets(char *s, int n, void *fp) { return fgets(s, n, real_fp(fp)); }
int ab_fputc(int c, void *fp) { return fputc(c, real_fp(fp)); }
int ab_fputs(const char *s, void *fp) { return fputs(s, real_fp(fp)); }
int ab_ungetc(int c, void *fp) { return ungetc(c, real_fp(fp)); }
int ab_setvbuf(void *fp, char *b, int m, size_t s) { return setvbuf(real_fp(fp), b, m, s); }
void *ab_freopen(const char *p, const char *m, void *fp) { (void)p; (void)m; return fp; }
#endif

int ab_fprintf(void *fp, const char *fmt, ...) {
    char buf[512]; va_list ap;
    va_start(ap, fmt); vsnprintf(buf, sizeof buf, fmt, ap); va_end(ap);
#ifdef FN_NANO
    plat_log_line(buf);
    if (fp) ab_fputs(buf, fp);
    return (int)strlen(buf);
#else
    {
        FILE *r = real_fp(fp);
        if (r == stderr || r == stdout) { plat_log_line(buf); return (int)strlen(buf); }
        return fputs(buf, r);
    }
#endif
}
int ab_fscanf(void *fp, const char *fmt, ...) {
    va_list ap; int n;
#ifdef FN_NANO
    nfile *w = nf_of(fp);
    if (!w || !w->data) return EOF;
    va_start(ap, fmt);
    n = vsscanf((char *)w->data + w->pos, fmt, ap);
    va_end(ap);
    return n;
#else
    va_start(ap, fmt); n = vfscanf(real_fp(fp), fmt, ap); va_end(ap);
    return n;
#endif
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
#ifdef FN_NANO
    (void)flags;
    return ab_fopen(path, (flags & 1) ? "wb+" : "rb") ? 3 : -1;
#else
    {
        FILE *r;
        int fd;
        if (!path) return -1;
        fe_note_texfile(path);
        r = try_open(path, "rb");
        if (!r) return -1;
        fd = dup(fileno(r));
        fclose(r);
        return fd;
    }
#endif
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
    W(acos), W(asin), W(atan), W(atan2), W(ceil), W(cos), W(cosh), W(exp),
    W(floor), W(fmod), W(frexp), W(ldexp), W(log), W(log10), W(modf), W(pow),
    W(sin), W(sinh), W(sqrt), W(tan), W(tanh),
    A(malloc, ab_malloc), A(free, ab_free), A(realloc, ab_realloc),
    R(memcpy), R(memmove), R(memset), R(memcmp), R(memchr),
    R(strcpy), R(strncpy), R(strcat), R(strncat), R(strcmp), R(strncmp),
    R(strcasecmp), R(strchr), R(strrchr), R(strstr), R(strpbrk), R(strcspn),
    R(strlen), R(strtod), R(strtol), R(strtoul), R(strtok), R(strerror),
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
    A(_ctype_, &_ctype_), A(_tolower_tab_, &_tolower_tab_), A(_toupper_tab_, &_toupper_tab_),
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
    ctype_init();
    if (!name) return 0;
    if (!sorted) { qsort(tab, sizeof tab / sizeof tab[0], sizeof tab[0], rec_cmp); sorted = 1; }
    key.n = name; key.p = 0;
    hit = bsearch(&key, tab, sizeof tab / sizeof tab[0], sizeof tab[0], rec_cmp);
    return hit ? hit->p : 0;
}

void ab_shims_reset(void) { heap_now = heap_peak = heap_fail = 0; memset(F, 0, sizeof F); memset(tls, 0, sizeof tls); }
