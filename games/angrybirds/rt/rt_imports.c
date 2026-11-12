/* rt_imports.c: the iOS library calls Angry Birds' engine makes, on guest memory
 * (nano7kit, MIT). Every pointer argument is a guest address. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <time.h>
#include "rt.h"
#include "rt_plat.h"
#include "fngl.h"

#define IMP(name) uint64_t I_##name(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3)
#define UNUSED (void)r0; (void)r1; (void)r2; (void)r3
#define RET(x) return (uint64_t)(uint32_t)(x)
extern uint32_t g_imparea;
extern const char *const rt_import_names[];
extern const uint32_t rt_import_count;
uint32_t g_realloc(uint32_t p, uint32_t n);

static uint32_t import_addr(const char *name) {
    for (uint32_t i = 0; i < rt_import_count; i++)
        if (!strcmp(rt_import_names[i], name)) return g_imparea + 32u * i;
    return 0;
}
static const char *gstr(uint32_t a) { return a ? (const char *)G8(a) : "(null)"; }

/* ---- C library ----------------------------------------------------------------------------- */
/* virtual archive: fileArcive.bin (8.6 MB) is never loaded; the engine's archive base points
 * at VARCH and its memcpy reads from there turn into file reads (chunked on the nano). */
#define VARCH 0xA0000000u
static uint32_t varch_size, varch_pending;
static const char varch_name[] = "fileArcive.bin";
uint32_t rt_varch_reads, rt_varch_bytes;
IMP(_memcpy) {
    UNUSED;
    if (r1 >= VARCH && r1 - VARCH < varch_size) {
        if (r1 - VARCH + r2 > varch_size) plat_fatal("archive read past end");
        if (plat_file_read(varch_name, r1 - VARCH, G8(r0), r2) != (int32_t)r2) plat_fatal("archive read failed");
        rt_varch_reads++; rt_varch_bytes += r2;
        RET(r0);
    }
    memmove(G8(r0), G8(r1), r2); RET(r0);
}
IMP(_memmove) { UNUSED; memmove(G8(r0), G8(r1), r2); RET(r0); }
IMP(_memset) { UNUSED; memset(G8(r0), (int)r1, r2); RET(r0); }
IMP(_memcmp) { UNUSED; RET(memcmp(G8(r0), G8(r1), r2)); }
IMP(_strlen) { UNUSED; RET(strlen(gstr(r0))); }
IMP(_strcmp) { UNUSED; RET(strcmp(gstr(r0), gstr(r1))); }
IMP(_strncmp) { UNUSED; RET(strncmp(gstr(r0), gstr(r1), r2)); }
IMP(_strcpy) { UNUSED; memmove(G8(r0), G8(r1), strlen(gstr(r1)) + 1); RET(r0); }
IMP(_strchr) { UNUSED; const char *s = gstr(r0), *p = strchr(s, (int)(r1 & 255)); RET(p ? r0 + (uint32_t)(p - s) : 0); }
IMP(_atoi) { UNUSED; RET(atoi(gstr(r0))); }
IMP(_atof) { UNUSED; double d = atof(gstr(r0)); uint64_t q; memcpy(&q, &d, 8); return q; }
IMP(_sqrtf) { UNUSED; return F2U(sqrtf(U2F(r0))); }
IMP(_sinf) { UNUSED; return F2U(sinf(U2F(r0))); }
IMP(_cosf) { UNUSED; return F2U(cosf(U2F(r0))); }
IMP(_floorf) { UNUSED; return F2U(floorf(U2F(r0))); }
IMP(_ceilf) { UNUSED; return F2U(ceilf(U2F(r0))); }
IMP(_roundf) { UNUSED; return F2U(roundf(U2F(r0))); }
IMP(_ceil) { UNUSED; double d = ceil(R2D(r0, r1)); uint64_t q; memcpy(&q, &d, 8); return q; }
static uint32_t rand_state;
IMP(_rand) { UNUSED; rand_state = rand_state * 1103515245u + 12345u; RET((rand_state >> 1) & 0x7fffffff); }
IMP(_srand) { UNUSED; rand_state = r0; RET(0); }
IMP(_malloc) { UNUSED; RET(g_malloc(r0)); }
IMP(_free) { UNUSED; g_free(r0); RET(0); }
IMP(_realloc) { UNUSED; RET(g_realloc(r0, r1)); }
IMP(_exit) { UNUSED; plat_fatal("exit"); RET(0); }
IMP(___error) { UNUSED; static uint32_t e; if (!e) e = g_malloc(4); RET(e); }
IMP(___assert_rtn) { UNUSED; rt_log("assert %s %s:%u %s", gstr(r0), gstr(r1), r2, gstr(r3)); RET(0); }

/* ctype: Apple's rune table masks */
static uint32_t runetype(uint32_t c) {
    uint32_t t = 0;
    if (c > 255) return 0;
    if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')) t |= 0x100;
    if (c < 32 || c == 127) t |= 0x200;
    if (c >= '0' && c <= '9') t |= 0x400 | 0x10000;
    if (c > 32 && c < 127) t |= 0x800 | 0x40000;
    if (c == 32) t |= 0x40000 | 0x20000 | 0x4000;
    if (c >= 'a' && c <= 'z') t |= 0x1000;
    if (c >= 'A' && c <= 'Z') t |= 0x8000;
    if (c > 32 && c < 127 && !(t & 0x500)) t |= 0x2000;
    if (c == 9) t |= 0x20000 | 0x4000;
    if (c >= 10 && c <= 13) t |= 0x4000;
    if ((c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')) t |= 0x10000;
    return t;
}
IMP(___maskrune) { UNUSED; RET(runetype(r0) & r1); }
IMP(___tolower) { UNUSED; RET((r0 >= 'A' && r0 <= 'Z') ? r0 + 32 : r0); }
IMP(___toupper) { UNUSED; RET((r0 >= 'a' && r0 <= 'z') ? r0 - 32 : r0); }

/* ---- printf / scanf ------------------------------------------------------------------------ */
typedef struct { int idx; uint32_t r[4]; uint32_t mem; } args_t;   /* mem!=0: va_list in guest memory */
static uint32_t arg_word(args_t *a) {
    uint32_t v;
    if (a->mem) { v = gr32(a->mem); a->mem += 4; return v; }
    if (a->idx < 4) v = a->r[a->idx]; else v = gr32(SP + 4u * (uint32_t)(a->idx - 4));
    a->idx++;
    return v;
}
static uint32_t format(char *out, uint32_t cap, const char *f, args_t *a) {
    uint32_t n = 0;
#define PUT(ch) do { if (n + 1 < cap) out[n] = (ch); n++; } while (0)
    while (*f) {
        if (*f != '%') { PUT(*f++); continue; }
        char spec[32]; int sl = 0; spec[sl++] = *f++;
        while (*f && strchr("-+ #0", *f) && sl < 20) spec[sl++] = *f++;
        if (*f == '*') { int w = (int)arg_word(a); sl += snprintf(spec + sl, sizeof spec - (size_t)sl, "%d", w); f++; }
        while (*f >= '0' && *f <= '9' && sl < 24) spec[sl++] = *f++;
        if (*f == '.') { spec[sl++] = *f++;
            if (*f == '*') { int w = (int)arg_word(a); sl += snprintf(spec + sl, sizeof spec - (size_t)sl, "%d", w); f++; }
            while (*f >= '0' && *f <= '9' && sl < 28) spec[sl++] = *f++; }
        int lng = 0;
        while (*f == 'l' || *f == 'h' || *f == 'q' || *f == 'z' || *f == 'L') { if (*f == 'l' || *f == 'q') lng++; f++; }
        char conv = *f ? *f++ : 0, tmp[96];
        int len = 0;
        spec[sl] = 0;
        switch (conv) {
        case 'd': case 'i': case 'u': case 'x': case 'X': case 'o': case 'c': {
            uint64_t v = arg_word(a);
            if (lng >= 2) v |= (uint64_t)arg_word(a) << 32;
            spec[sl++] = 'l'; spec[sl++] = 'l'; spec[sl++] = conv == 'i' ? 'd' : conv; spec[sl] = 0;
            if (conv == 'c') { spec[sl - 3] = 'c'; spec[sl - 2] = 0; len = snprintf(tmp, sizeof tmp, spec, (int)v); }
            else if (conv == 'd' || conv == 'i') len = snprintf(tmp, sizeof tmp, spec, lng >= 2 ? (long long)v : (long long)(int32_t)v);
            else len = snprintf(tmp, sizeof tmp, spec, (unsigned long long)v);
            break; }
        case 'p': len = snprintf(tmp, sizeof tmp, "0x%x", (unsigned)arg_word(a)); break;
        case 'f': case 'F': case 'e': case 'E': case 'g': case 'G': {
            uint32_t lo = arg_word(a), hi = arg_word(a);
            spec[sl++] = conv; spec[sl] = 0;
            len = snprintf(tmp, sizeof tmp, spec, R2D(lo, hi));
            break; }
        case 's': {
            const char *s = gstr(arg_word(a));
            spec[sl++] = 's'; spec[sl] = 0;
            char big[512]; len = snprintf(big, sizeof big, spec, s);
            for (int i = 0; i < len && i < (int)sizeof big - 1; i++) PUT(big[i]);
            len = -1;
            break; }
        case '%': tmp[0] = '%'; len = 1; break;
        default: len = 0;
        }
        for (int i = 0; i < len && i < (int)sizeof tmp - 1; i++) PUT(tmp[i]);
    }
    if (cap) out[n < cap ? n : cap - 1] = 0;
#undef PUT
    return n;
}
IMP(_sprintf) { args_t a = {2, {r0, r1, r2, r3}, 0}; RET(format((char *)G8(r0), 0x7fffffff, gstr(r1), &a)); }
IMP(_snprintf) { args_t a = {3, {r0, r1, r2, r3}, 0}; RET(format((char *)G8(r0), r1, gstr(r2), &a)); }
IMP(_vsprintf) { UNUSED; args_t a = {0, {0}, r2}; RET(format((char *)G8(r0), 0x7fffffff, gstr(r1), &a)); }
IMP(_vsnprintf) { UNUSED; args_t a = {0, {0}, r3}; RET(format((char *)G8(r0), r1, gstr(r2), &a)); }
IMP(_printf) { args_t a = {1, {r0, r1, r2, r3}, 0}; char buf[512]; format(buf, sizeof buf, gstr(r0), &a);
    size_t l = strlen(buf); while (l && buf[l - 1] == '\n') buf[--l] = 0;
    if (l) {
        int err = strstr(buf, "error") || strstr(buf, "Error") || strstr(buf, "PANIC")
               || strstr(buf, "stack traceback");
        rt_log(err ? "luaerr %s" : "game: %s", buf);
    }
    RET(l); }
IMP(_NSLog) { UNUSED; RET(0); }

IMP(_sscanf) {
    args_t a = {2, {r0, r1, r2, r3}, 0};
    const char *s = gstr(r0), *f = gstr(r1);
    int got = 0;
    while (*f) {
        if (*f == ' ' || *f == '\t' || *f == '\n') { while (*s == ' ' || *s == '\t' || *s == '\n' || *s == '\r') s++; f++; continue; }
        if (*f != '%') { if (*s != *f) break; s++; f++; continue; }
        f++;
        int skip = 0, lng = 0, width = 0;
        if (*f == '*') { skip = 1; f++; }
        while (*f >= '0' && *f <= '9') width = width * 10 + (*f++ - '0');
        while (*f == 'l' || *f == 'h') { if (*f == 'l') lng++; f++; }
        char c = *f++;
        if (c != 'c') while (*s == ' ' || *s == '\t' || *s == '\n' || *s == '\r') s++;
        if (!*s) break;
        char *end = NULL;
        if (c == 'd' || c == 'i' || c == 'u' || c == 'x') {
            long v = c == 'x' ? (long)strtoul(s, &end, 16) : (c == 'u' ? (long)strtoul(s, &end, 10) : strtol(s, &end, c == 'i' ? 0 : 10));
            if (end == s) break;
            s = end;
            if (!skip) { gw32(arg_word(&a), (uint32_t)v); got++; }
        } else if (c == 'f' || c == 'g' || c == 'e') {
            double v = strtod(s, &end);
            if (end == s) break;
            s = end;
            if (!skip) { uint32_t p = arg_word(&a);
                if (lng) { memcpy(G8(p), &v, 8); } else { float fv = (float)v; memcpy(G8(p), &fv, 4); }
                got++; }
        } else if (c == 's') {
            uint32_t p = skip ? 0 : arg_word(&a); int n = 0;
            while (*s && *s != ' ' && *s != '\t' && *s != '\n' && *s != '\r' && (!width || n < width)) { if (p) *G8(p + (uint32_t)n) = (uint8_t)*s; n++; s++; }
            if (p) { *G8(p + (uint32_t)n) = 0; got++; }
        } else if (c == 'c') {
            uint32_t p = skip ? 0 : arg_word(&a);
            if (p) { *G8(p) = (uint8_t)*s; got++; }
            s++;
        } else break;
    }
    RET(got);
}

/* ---- qsort with a guest comparator --------------------------------------------------------- */
static uint32_t qs_cmp;
static int qs_compare(uint32_t a, uint32_t b) { return (int)(uint32_t)rt_icall(qs_cmp, a, b, 0, 0); }
IMP(_qsort) {
    UNUSED;
    uint32_t base = r0, n = r1, size = r2;
    qs_cmp = r3;
    if (n < 2 || !size) RET(0);
    uint32_t tmp = g_malloc(size);
    for (uint32_t i = 1; i < n; i++) {          /* insertion sort: stable, small n in this game */
        memcpy(G8(tmp), G8(base + i * size), size);
        uint32_t j = i;
        while (j > 0 && qs_compare(base + (j - 1) * size, tmp) > 0) {
            memcpy(G8(base + j * size), G8(base + (j - 1) * size), size);
            j--;
        }
        memcpy(G8(base + j * size), G8(tmp), size);
    }
    g_free(tmp);
    RET(0);
}

/* ---- files: FILE* is a guest block {magic, handle} ----------------------------------------- */
#define MAX_FILES 256                   /* Ka3D keeps every SFX FILE* until the bank is built */
static struct { char name[96]; uint32_t pos, size, wr; uint32_t wbuf, wcap; int used, dummy; } files[MAX_FILES];
static uint32_t fileobj[MAX_FILES];
/* 16 kHz mono s16 LE, 16 silent frames: Ka3D loads every SFX wav at menu start. */
static const uint8_t silent_wav[] = {
    'R','I','F','F', 68,0,0,0, 'W','A','V','E',
    'f','m','t',' ', 16,0,0,0, 1,0, 1,0, 0x80,0x3e,0,0, 0x00,0x7d,0,0, 2,0, 16,0,
    'd','a','t','a', 32,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
};
/* first two frames of Angry Birds 1.0 level_complete.mp3, Xing frames=1. */
static const uint8_t silent_mp3[] = {
    255,243,136,196,0,0,0,0,0,0,0,0,0,88,105,110,
    103,0,0,0,15,0,0,0,1,0,0,1,248,0,3,13,
    17,21,26,29,29,32,35,38,41,43,43,46,49,51,54,58,
    58,65,68,71,73,76,80,80,83,86,88,92,95,95,97,100,
    103,107,109,109,112,115,118,120,123,123,126,128,131,133,136,139,
    139,143,146,148,151,153,153,157,160,163,165,168,168,172,175,178,
    181,184,184,187,190,194,197,199,202,202,205,209,212,215,218,218,
    222,225,227,230,234,234,236,240,243,245,248,248,251,254,254,255,
    255,0,0,0,58,76,65,77,69,51,46,57,56,114,2,80,
    0,0,0,0,46,71,0,0,20,48,36,6,66,34,0,0,
    48,0,0,58,164,157,60,77,39,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    255,243,104,196,0,46,187,250,4,1,67,72,1,255,235,191,
    93,221,221,220,226,32,132,220,12,13,2,6,33,5,16,32,
    97,26,52,98,178,116,115,164,2,177,91,123,238,123,4,104,
    231,252,33,8,77,26,52,123,4,8,33,237,116,98,180,104,
    209,163,64,129,2,4,8,24,132,17,206,136,2,128,16,0,
    0,0,48,0,192,216,173,186,132,20,64,141,28,247,255,224,
    141,189,130,4,12,89,24,32,8,10,9,24,134,127,234,16,
    132,45,116,98,129,64,160,145,2,8,103,254,161,8,121,163,
    20,10,16,100,209,163,111,103,57,206,115,218,81,27,116,128,
    128,145,6,77,2,4,8,16,32,64,129,0,172,86,43,71,
    58,81,140,172,254,16,132,63,207,255,255,206,123,224,129,3,
    22,129,139,70,141,28,231,56,67,206,115,159,130,54,223,110,
    134,0,120,3,128,58,30,30,112,58,47,23,139,196,227,113,
    248,220,126,63,31,139,150,75,
};
static const char *base_name(const char *path) {
    const char *b = strrchr(path, '/');
    return b ? b + 1 : path;
}
static int audio_kind(const char *name) {
    const char *d = strrchr(name, '.');
    if (!d) return 0;
    if (!strcmp(d, ".wav")) return 1;
    if (!strcmp(d, ".mp3")) return 2;
    return 0;
}
static int tex_file_shift(const char *name) {
    static int loaded, nshift;
    static struct { char stem[40]; int sh; } tab[64];
    if (!loaded) {
        loaded = 1;
        int32_t sz = plat_file_size("texscale");
        if (sz > 0 && sz < 4096) {
            char buf[4096];
            int32_t got = plat_file_read("texscale", 0, buf, (uint32_t)sz);
            if (got > 0) {
                buf[got] = 0;
                char *p = buf;
                while (*p && nshift < 64) {
                    char stem[40];
                    int sh = 0;
                    if (sscanf(p, "%39s %d", stem, &sh) == 2 && sh > 0) {
                        snprintf(tab[nshift].stem, sizeof tab[nshift].stem, "%s", stem);
                        tab[nshift].sh = sh;
                        nshift++;
                    }
                    char *nl = strchr(p, '\n');
                    p = nl ? nl + 1 : p + strlen(p);
                }
            }
        }
    }
    const char *dot = strrchr(name, '.');
    size_t n = dot ? (size_t)(dot - name) : strlen(name);
    for (int i = 0; i < nshift; i++)
        if (strlen(tab[i].stem) == n && !strncmp(tab[i].stem, name, n)) return tab[i].sh;
    return 0;
}
IMP(_fopen) {
    UNUSED;
    const char *path = gstr(r0), *mode = gstr(r1), *name = base_name(path);
    int tsh = tex_file_shift(name);
    if (tsh) {
        const char *dot = strrchr(name, '.');
        char stem[40];
        size_t n = dot ? (size_t)(dot - name) : strlen(name);
        if (n >= sizeof stem) n = sizeof stem - 1;
        memcpy(stem, name, n);
        stem[n] = 0;
        fe_queue_tex_shift(tsh, stem);
    }
    int w = strchr(mode, 'w') != NULL || strchr(mode, 'a') != NULL;
    int dummy = 0;
    int32_t size = plat_file_size(name);
    if (!w && size < 0) {
        dummy = audio_kind(name);
        if (dummy == 1) { size = (int32_t)sizeof silent_wav; rt_log("fopen %s: silent stub", name); }
        else if (dummy == 2) { size = (int32_t)sizeof silent_mp3; rt_log("fopen %s: silent stub", name); }
        else { rt_log("fopen %s: missing", name); RET(0); }
    }
    for (int i = 0; i < MAX_FILES; i++) if (!files[i].used) {
        files[i].used = 1; files[i].pos = 0; files[i].size = w ? 0 : (uint32_t)size; files[i].wr = (uint32_t)w;
        files[i].wbuf = 0; files[i].wcap = 0; files[i].dummy = dummy;
        snprintf(files[i].name, sizeof files[i].name, "%s", name);
        if (!fileobj[i]) fileobj[i] = g_malloc(16);
        gw32(fileobj[i], 0x46494c45u); gw32(fileobj[i] + 4, (uint32_t)i);
        RET(fileobj[i]);
    }
    RET(0);
}
static int fidx(uint32_t fp) {
    if (!fp || gr32(fp) != 0x46494c45u) return -1;
    uint32_t i = gr32(fp + 4);
    return i < MAX_FILES && files[i].used ? (int)i : -1;
}
IMP(_fread) {
    UNUSED;
    int i = fidx(r3);
    if (i < 0 || !r1) RET(0);
    if (r0 == VARCH) { files[i].pos = files[i].size; RET(r2); }
    uint32_t want = r1 * r2, left = files[i].size > files[i].pos ? files[i].size - files[i].pos : 0;
    if (want > left) want = left - left % r1;
    int32_t got = 0;
    if (files[i].dummy) {
        const uint8_t *src = files[i].dummy == 2 ? silent_mp3 : silent_wav;
        if (want) memcpy(G8(r0), src + files[i].pos, want);
        got = (int32_t)want;
    } else {
        got = want ? plat_file_read(files[i].name, files[i].pos, G8(r0), want) : 0;
    }
    if (got < 0) got = 0;
    files[i].pos += (uint32_t)got;
    RET((uint32_t)got / r1);
}
IMP(_fwrite) {
    UNUSED;
    int i = fidx(r3);
    if (i < 0 || !files[i].wr) RET(0);
    uint32_t n = r1 * r2, need = files[i].pos + n;
    if (need > files[i].wcap) {
        uint32_t cap = need < 4096 ? 4096 : need * 2, nb = g_malloc(cap);
        if (!nb) RET(0);
        if (files[i].wbuf) { memcpy(G8(nb), G8(files[i].wbuf), files[i].size); g_free(files[i].wbuf); }
        files[i].wbuf = nb; files[i].wcap = cap;
    }
    memcpy(G8(files[i].wbuf + files[i].pos), G8(r0), n);
    files[i].pos += n;
    if (files[i].pos > files[i].size) files[i].size = files[i].pos;
    RET(r2);
}
IMP(_fseek) {
    UNUSED;
    int i = fidx(r0);
    if (i < 0) RET(-1);
    int32_t off = (int32_t)r1;
    uint32_t base = r2 == 0 ? 0 : r2 == 1 ? files[i].pos : files[i].size;
    files[i].pos = (uint32_t)((int32_t)base + off);
    RET(0);
}
IMP(_ftell) {
    UNUSED; int i = fidx(r0);
    if (i >= 0 && !strcmp(files[i].name, varch_name) && files[i].pos == files[i].size) varch_pending = files[i].size;
    RET(i < 0 ? 0xffffffffu : files[i].pos);
}
IMP(_rewind) { UNUSED; int i = fidx(r0); if (i >= 0) files[i].pos = 0; RET(0); }
IMP(_fclose) {
    UNUSED;
    int i = fidx(r0);
    if (i < 0) RET(-1);
    if (files[i].wr && files[i].wbuf) plat_file_write(files[i].name, G8(files[i].wbuf), files[i].size);
    if (files[i].wbuf) g_free(files[i].wbuf);
    files[i].used = 0;
    gw32(r0, 0);
    RET(0);
}

/* ---- time ---------------------------------------------------------------------------------- */
IMP(_mach_absolute_time) { UNUSED; return plat_time_us() * 1000ull; }
IMP(_mach_timebase_info) { UNUSED; gw32(r0, 1); gw32(r0 + 4, 1); RET(0); }
IMP(_CFAbsoluteTimeGetCurrent) { UNUSED; double d = (double)plat_time_us() / 1e6; uint64_t q; memcpy(&q, &d, 8); return q; }

/* ---- C++ runtime --------------------------------------------------------------------------- */
IMP(__Znwm) { UNUSED; uint32_t p = g_malloc(r0 ? r0 : 1); if (!p) plat_fatal("out of guest memory (new)"); RET(p); }
IMP(__Znam) {
    UNUSED;
    if (varch_pending && r0 == varch_pending) { varch_size = varch_pending; varch_pending = 0; rt_log("archive: %u bytes left on disk", varch_size); RET(VARCH); }
    uint32_t p = g_malloc(r0 ? r0 : 1); if (!p) plat_fatal("out of guest memory (new[])"); RET(p); }
IMP(__ZdlPv) { UNUSED; g_free(r0); RET(0); }
IMP(__ZdaPv) { UNUSED; if (r0 != VARCH) g_free(r0); RET(0); }
IMP(___cxa_atexit) { UNUSED; RET(0); }
IMP(___cxa_guard_acquire) { UNUSED; RET(*G8(r0) == 0); }
IMP(___cxa_guard_release) { UNUSED; *G8(r0) = 1; RET(0); }
IMP(___cxa_guard_abort) { UNUSED; RET(0); }
IMP(___cxa_begin_catch) { UNUSED; RET(r0); }
IMP(___cxa_end_catch) { UNUSED; RET(0); }
IMP(___cxa_rethrow) { UNUSED; plat_fatal("C++ rethrow"); RET(0); }
IMP(___cxa_call_unexpected) { UNUSED; plat_fatal("C++ unexpected"); RET(0); }
IMP(__Unwind_SjLj_Register) { UNUSED; rt_sjlj_register(r0); RET(0); }
IMP(__Unwind_SjLj_Unregister) { UNUSED; rt_sjlj_unregister(r0); RET(0); }
IMP(__Unwind_SjLj_Resume) {
    UNUSED;
    uint32_t exc = r0, ctx = rt_sjlj_top();
    if (ctx) rt_sjlj_unregister(ctx);
    rt_sjlj_raise(exc);
    RET(0);
}
IMP(__ZSt17__throw_bad_allocv) { UNUSED; plat_fatal("bad_alloc"); RET(0); }
IMP(__ZSt20__throw_length_errorPKc) { UNUSED; rt_log("length_error %s", gstr(r0)); plat_fatal("length_error"); RET(0); }
IMP(__ZSt20__throw_out_of_rangePKc) { UNUSED; rt_log("out_of_range %s", gstr(r0)); plat_fatal("out_of_range"); RET(0); }
IMP(__ZNSt8ios_base4InitC1Ev) { UNUSED; RET(r0); }
IMP(__ZNSt8ios_base4InitD1Ev) { UNUSED; RET(r0); }
IMP(__ZN9__gnu_cxx18__exchange_and_addEPVii) { UNUSED; uint32_t old = gr32(r0); gw32(r0, old + r1); RET(old); }

/* libstdc++ COW std::string: object = char*, rep {length, capacity, refcount} before the chars */
static uint32_t empty_rep;
static uint32_t str_make(const uint8_t *src, uint32_t len) {
    if (!len) return empty_rep + 12;
    uint32_t rep = g_malloc(12 + len + 1);
    if (!rep) plat_fatal("out of guest memory (string)");
    gw32(rep, len); gw32(rep + 4, len); gw32(rep + 8, 0);
    memcpy(G8(rep + 12), src, len); *G8(rep + 12 + len) = 0;
    return rep + 12;
}
static void str_release(uint32_t chars) {
    uint32_t rep = chars - 12;
    if (rep == empty_rep || !chars) return;
    int32_t rc = (int32_t)gr32(rep + 8);
    if (rc <= 0) g_free(rep); else gw32(rep + 8, (uint32_t)(rc - 1));
}
IMP(__ZNSsC1EPKcRKSaIcE) { UNUSED; gw32(r0, str_make(G8(r1), (uint32_t)strlen(gstr(r1)))); RET(r0); }
IMP(__ZNSsC1ERKSs) { UNUSED; uint32_t c = gr32(r1); gw32(r0, str_make(G8(c), gr32(c - 12))); RET(r0); }
IMP(__ZNSsC1ERKSsmm) {
    UNUSED;
    uint32_t c = gr32(r1), len = gr32(c - 12), pos = r2, n = r3;
    if (pos > len) { rt_log("basic_string::substr"); plat_fatal("out_of_range"); }
    if (n > len - pos) n = len - pos;
    gw32(r0, str_make(G8(c + pos), n));
    RET(r0);
}
IMP(__ZNSsD1Ev) { UNUSED; str_release(gr32(r0)); RET(r0); }
IMP(__ZNSs4_Rep10_M_destroyERKSaIcE) { UNUSED; if (r0 != empty_rep) g_free(r0); RET(0); }
IMP(__ZNSs6assignERKSs) {
    UNUSED;
    uint32_t c = gr32(r1), old = gr32(r0);
    if (c != old) { gw32(r0, str_make(G8(c), gr32(c - 12))); str_release(old); }
    RET(r0);
}
IMP(__ZNSs6assignEPKcm) { UNUSED; uint32_t old = gr32(r0); gw32(r0, str_make(G8(r1), r2)); str_release(old); RET(r0); }

/* std::list */
IMP(__ZNSt15_List_node_base4hookEPS_) {     /* this->hook(position): insert this before position */
    UNUSED;
    uint32_t self = r0, pos = r1, prev = gr32(pos + 4);
    gw32(self, pos); gw32(self + 4, prev);
    gw32(prev, self); gw32(pos + 4, self);
    RET(0);
}
IMP(__ZNSt15_List_node_base6unhookEv) {
    UNUSED;
    uint32_t next = gr32(r0), prev = gr32(r0 + 4);
    gw32(prev, next); gw32(next + 4, prev);
    RET(0);
}

/* std::_Rb_tree: node {color(0 red,1 black), parent, left, right}; header: parent=root, left=min, right=max */
#define RB_COLOR(x) gr32(x)
#define RB_PARENT(x) gr32((x) + 4)
#define RB_LEFT(x) gr32((x) + 8)
#define RB_RIGHT(x) gr32((x) + 12)
#define SET_COLOR(x, v) gw32(x, v)
#define SET_PARENT(x, v) gw32((x) + 4, v)
#define SET_LEFT(x, v) gw32((x) + 8, v)
#define SET_RIGHT(x, v) gw32((x) + 12, v)
static uint32_t rb_inc(uint32_t x) {
    if (RB_RIGHT(x)) { x = RB_RIGHT(x); while (RB_LEFT(x)) x = RB_LEFT(x); return x; }
    uint32_t y = RB_PARENT(x);
    while (x == RB_RIGHT(y)) { x = y; y = RB_PARENT(y); }
    if (RB_RIGHT(x) != y) x = y;
    return x;
}
static uint32_t rb_dec(uint32_t x) {
    if (RB_COLOR(x) == 0 && RB_PARENT(RB_PARENT(x)) == x) return RB_RIGHT(x);   /* header */
    if (RB_LEFT(x)) { uint32_t y = RB_LEFT(x); while (RB_RIGHT(y)) y = RB_RIGHT(y); return y; }
    uint32_t y = RB_PARENT(x);
    while (x == RB_LEFT(y)) { x = y; y = RB_PARENT(y); }
    return y;
}
IMP(__ZSt18_Rb_tree_incrementPSt18_Rb_tree_node_base) { UNUSED; RET(rb_inc(r0)); }
IMP(__ZSt18_Rb_tree_incrementPKSt18_Rb_tree_node_base) { UNUSED; RET(rb_inc(r0)); }
IMP(__ZSt18_Rb_tree_decrementPSt18_Rb_tree_node_base) { UNUSED; RET(rb_dec(r0)); }
static void rot_left(uint32_t x, uint32_t root_slot) {
    uint32_t y = RB_RIGHT(x);
    SET_RIGHT(x, RB_LEFT(y));
    if (RB_LEFT(y)) SET_PARENT(RB_LEFT(y), x);
    SET_PARENT(y, RB_PARENT(x));
    if (x == gr32(root_slot)) gw32(root_slot, y);
    else if (x == RB_LEFT(RB_PARENT(x))) SET_LEFT(RB_PARENT(x), y);
    else SET_RIGHT(RB_PARENT(x), y);
    SET_LEFT(y, x); SET_PARENT(x, y);
}
static void rot_right(uint32_t x, uint32_t root_slot) {
    uint32_t y = RB_LEFT(x);
    SET_LEFT(x, RB_RIGHT(y));
    if (RB_RIGHT(y)) SET_PARENT(RB_RIGHT(y), x);
    SET_PARENT(y, RB_PARENT(x));
    if (x == gr32(root_slot)) gw32(root_slot, y);
    else if (x == RB_RIGHT(RB_PARENT(x))) SET_RIGHT(RB_PARENT(x), y);
    else SET_LEFT(RB_PARENT(x), y);
    SET_RIGHT(y, x); SET_PARENT(x, y);
}
IMP(__ZSt29_Rb_tree_insert_and_rebalancebPSt18_Rb_tree_node_baseS0_RS_) {
    uint32_t left = r0 & 255, x = r1, p = r2, header = r3, root = header + 4;
    SET_PARENT(x, p); SET_LEFT(x, 0); SET_RIGHT(x, 0); SET_COLOR(x, 0);
    if (left) {
        SET_LEFT(p, x);
        if (p == header) { SET_PARENT(header, x); SET_RIGHT(header, x); }
        else if (p == RB_LEFT(header)) SET_LEFT(header, x);
    } else {
        SET_RIGHT(p, x);
        if (p == RB_RIGHT(header)) SET_RIGHT(header, x);
    }
    while (x != gr32(root) && RB_COLOR(RB_PARENT(x)) == 0) {
        uint32_t xp = RB_PARENT(x), xpp = RB_PARENT(xp);
        if (xp == RB_LEFT(xpp)) {
            uint32_t y = RB_RIGHT(xpp);
            if (y && RB_COLOR(y) == 0) { SET_COLOR(xp, 1); SET_COLOR(y, 1); SET_COLOR(xpp, 0); x = xpp; }
            else {
                if (x == RB_RIGHT(xp)) { x = xp; rot_left(x, root); xp = RB_PARENT(x); xpp = RB_PARENT(xp); }
                SET_COLOR(xp, 1); SET_COLOR(xpp, 0); rot_right(xpp, root);
            }
        } else {
            uint32_t y = RB_LEFT(xpp);
            if (y && RB_COLOR(y) == 0) { SET_COLOR(xp, 1); SET_COLOR(y, 1); SET_COLOR(xpp, 0); x = xpp; }
            else {
                if (x == RB_LEFT(xp)) { x = xp; rot_right(x, root); xp = RB_PARENT(x); xpp = RB_PARENT(xp); }
                SET_COLOR(xp, 1); SET_COLOR(xpp, 0); rot_left(xpp, root);
            }
        }
    }
    SET_COLOR(gr32(root), 1);
    RET(0);
}
IMP(__ZSt28_Rb_tree_rebalance_for_erasePSt18_Rb_tree_node_baseRS_) {
    UNUSED;
    uint32_t z = r0, header = r1, root = header + 4, leftmost = header + 8, rightmost = header + 12;
    uint32_t y = z, x, xp;
    if (!RB_LEFT(y)) x = RB_RIGHT(y);
    else if (!RB_RIGHT(y)) x = RB_LEFT(y);
    else { y = RB_RIGHT(y); while (RB_LEFT(y)) y = RB_LEFT(y); x = RB_RIGHT(y); }
    if (y != z) {
        SET_PARENT(RB_LEFT(z), y); SET_LEFT(y, RB_LEFT(z));
        if (y != RB_RIGHT(z)) {
            xp = RB_PARENT(y);
            if (x) SET_PARENT(x, RB_PARENT(y));
            SET_LEFT(RB_PARENT(y), x);
            SET_RIGHT(y, RB_RIGHT(z)); SET_PARENT(RB_RIGHT(z), y);
        } else xp = y;
        if (gr32(root) == z) gw32(root, y);
        else if (RB_LEFT(RB_PARENT(z)) == z) SET_LEFT(RB_PARENT(z), y);
        else SET_RIGHT(RB_PARENT(z), y);
        SET_PARENT(y, RB_PARENT(z));
        uint32_t c = RB_COLOR(y); SET_COLOR(y, RB_COLOR(z)); SET_COLOR(z, c);
        y = z;
    } else {
        xp = RB_PARENT(y);
        if (x) SET_PARENT(x, RB_PARENT(y));
        if (gr32(root) == z) gw32(root, x);
        else if (RB_LEFT(RB_PARENT(z)) == z) SET_LEFT(RB_PARENT(z), x);
        else SET_RIGHT(RB_PARENT(z), x);
        if (gr32(leftmost) == z) {
            if (!RB_RIGHT(z)) gw32(leftmost, RB_PARENT(z));
            else { uint32_t t = x; while (RB_LEFT(t)) t = RB_LEFT(t); gw32(leftmost, t); }
        }
        if (gr32(rightmost) == z) {
            if (!RB_LEFT(z)) gw32(rightmost, RB_PARENT(z));
            else { uint32_t t = x; while (RB_RIGHT(t)) t = RB_RIGHT(t); gw32(rightmost, t); }
        }
    }
    if (RB_COLOR(y) != 0) {
        while (x != gr32(root) && (!x || RB_COLOR(x) == 1)) {
            if (x == RB_LEFT(xp)) {
                uint32_t w = RB_RIGHT(xp);
                if (RB_COLOR(w) == 0) { SET_COLOR(w, 1); SET_COLOR(xp, 0); rot_left(xp, root); w = RB_RIGHT(xp); }
                if ((!RB_LEFT(w) || RB_COLOR(RB_LEFT(w)) == 1) && (!RB_RIGHT(w) || RB_COLOR(RB_RIGHT(w)) == 1)) {
                    SET_COLOR(w, 0); x = xp; xp = RB_PARENT(xp);
                } else {
                    if (!RB_RIGHT(w) || RB_COLOR(RB_RIGHT(w)) == 1) {
                        SET_COLOR(RB_LEFT(w), 1); SET_COLOR(w, 0); rot_right(w, root); w = RB_RIGHT(xp);
                    }
                    SET_COLOR(w, RB_COLOR(xp)); SET_COLOR(xp, 1);
                    if (RB_RIGHT(w)) SET_COLOR(RB_RIGHT(w), 1);
                    rot_left(xp, root);
                    break;
                }
            } else {
                uint32_t w = RB_LEFT(xp);
                if (RB_COLOR(w) == 0) { SET_COLOR(w, 1); SET_COLOR(xp, 0); rot_right(xp, root); w = RB_LEFT(xp); }
                if ((!RB_RIGHT(w) || RB_COLOR(RB_RIGHT(w)) == 1) && (!RB_LEFT(w) || RB_COLOR(RB_LEFT(w)) == 1)) {
                    SET_COLOR(w, 0); x = xp; xp = RB_PARENT(xp);
                } else {
                    if (!RB_LEFT(w) || RB_COLOR(RB_LEFT(w)) == 1) {
                        SET_COLOR(RB_RIGHT(w), 1); SET_COLOR(w, 0); rot_left(w, root); w = RB_LEFT(xp);
                    }
                    SET_COLOR(w, RB_COLOR(xp)); SET_COLOR(xp, 1);
                    if (RB_LEFT(w)) SET_COLOR(RB_LEFT(w), 1);
                    rot_right(xp, root);
                    break;
                }
            }
        }
        if (x) SET_COLOR(x, 1);
    }
    RET(y);
}

/* ---- Objective-C and the rest of iOS: the shell is ours, so these do nothing --------------- */
IMP(_objc_msgSend) {
    UNUSED;
    static int shown;
    if (shown < 40) { shown++; rt_log("objc_msgSend %s (ignored)", gstr(r1)); }
    RET(0);
}
IMP(_objc_msgSendSuper2) { UNUSED; RET(0); }
IMP(_objc_msgSend_stret) { UNUSED; RET(0); }
IMP(_objc_setProperty) { UNUSED; RET(0); }
IMP(_objc_enumerationMutation) { UNUSED; RET(0); }
IMP(_UIApplicationMain) { UNUSED; plat_fatal("UIApplicationMain"); RET(0); }
IMP(___divsi3) { UNUSED; r0 = SDIV(r0, r1); RET(r0); }
IMP(___modsi3) { UNUSED; r0 = SMOD(r0, r1); RET(r0); }
IMP(___udivsi3) { UNUSED; RET(r1 ? r0 / r1 : 0); }
IMP(___umodsi3) { UNUSED; RET(r1 ? r0 % r1 : r0); }

/* OpenAL / AudioQueue / AudioSession: silent (no sound on the nano port) */
static uint32_t al_next;
IMP(_alcOpenDevice) { UNUSED; RET(0x0A1D0001u); }
IMP(_alcCreateContext) { UNUSED; RET(0x0A1C0001u); }
IMP(_alcMakeContextCurrent) { UNUSED; RET(1); }
IMP(_alGenSources) { UNUSED; for (uint32_t i = 0; i < r0; i++) gw32(r1 + 4 * i, al_next++); RET(0); }
IMP(_alGenBuffers) { UNUSED; for (uint32_t i = 0; i < r0; i++) gw32(r1 + 4 * i, al_next++); RET(0); }
IMP(_alBufferData) { UNUSED; RET(0); }
IMP(_alSourcePlay) { UNUSED; RET(0); }
IMP(_alSourceStop) { UNUSED; RET(0); }
IMP(_alSourcef) { UNUSED; RET(0); }
IMP(_alSourcei) { UNUSED; RET(0); }
IMP(_alGetSourcei) { UNUSED; if (r2) gw32(r2, 0x1014 /* AL_STOPPED */ ); RET(0); }
IMP(_alSourceQueueBuffers) { UNUSED; RET(0); }
IMP(_alSourceUnqueueBuffers) { UNUSED; RET(0); }
IMP(_AudioSessionInitialize) { UNUSED; RET(0); }
IMP(_AudioSessionSetActive) { UNUSED; RET(0); }
IMP(_AudioSessionSetProperty) { UNUSED; RET(0); }
IMP(_AudioSessionGetProperty) { UNUSED; RET(1); }
IMP(_AudioFileOpenWithCallbacks) { UNUSED; RET(1); }       /* non-zero OSStatus: no music */
IMP(_AudioFileClose) { UNUSED; RET(0); }
IMP(_AudioFileGetProperty) { UNUSED; RET(1); }
IMP(_AudioFileGetPropertyInfo) { UNUSED; RET(1); }
IMP(_AudioFileReadPacketData) { UNUSED; RET(1); }
IMP(_AudioQueueNewOutput) { UNUSED; RET(1); }
IMP(_AudioQueueAllocateBuffer) { UNUSED; RET(1); }
IMP(_AudioQueueDispose) { UNUSED; RET(0); }
IMP(_AudioQueueEnqueueBufferWithParameters) { UNUSED; RET(1); }
IMP(_AudioQueueSetParameter) { UNUSED; RET(0); }
IMP(_AudioQueueSetProperty) { UNUSED; RET(0); }
IMP(_AudioQueueStart) { UNUSED; RET(1); }

/* ---- extra libc / math / pthread used by Angry Birds 1.0 (Ka3D, Lua, Box2D) ---------------- */
IMP(_acosf) { UNUSED; return F2U(acosf(U2F(r0))); }
IMP(_asinf) { UNUSED; return F2U(asinf(U2F(r0))); }
IMP(_atanf) { UNUSED; return F2U(atanf(U2F(r0))); }
IMP(_atan2f) { UNUSED; return F2U(atan2f(U2F(r0), U2F(r1))); }
IMP(_atan2) { UNUSED; double d = atan2(R2D(r0, r1), R2D(r2, r3)); uint64_t q; memcpy(&q, &d, 8); return q; }
IMP(_cos) { UNUSED; double d = cos(R2D(r0, r1)); uint64_t q; memcpy(&q, &d, 8); return q; }
IMP(_sin) { UNUSED; double d = sin(R2D(r0, r1)); uint64_t q; memcpy(&q, &d, 8); return q; }
IMP(_tan) { UNUSED; double d = tan(R2D(r0, r1)); uint64_t q; memcpy(&q, &d, 8); return q; }
IMP(_tanf) { UNUSED; return F2U(tanf(U2F(r0))); }
IMP(_coshf) { UNUSED; return F2U(coshf(U2F(r0))); }
IMP(_sinhf) { UNUSED; return F2U(sinhf(U2F(r0))); }
IMP(_tanhf) { UNUSED; return F2U(tanhf(U2F(r0))); }
IMP(_expf) { UNUSED; return F2U(expf(U2F(r0))); }
IMP(_logf) { UNUSED; return F2U(logf(U2F(r0))); }
IMP(_log10f) { UNUSED; return F2U(log10f(U2F(r0))); }
IMP(_pow) { UNUSED; double d = pow(R2D(r0, r1), R2D(r2, r3)); uint64_t q; memcpy(&q, &d, 8); return q; }
IMP(_fmod) { UNUSED; double d = fmod(R2D(r0, r1), R2D(r2, r3)); uint64_t q; memcpy(&q, &d, 8); return q; }
IMP(_modf) { UNUSED; double ip; double d = modf(R2D(r0, r1), &ip); memcpy(G8(r2), &ip, 8); uint64_t q; memcpy(&q, &d, 8); return q; }
IMP(_frexp) { UNUSED; int e; double d = frexp(R2D(r0, r1), &e); if (r2) gw32(r2, (uint32_t)e); uint64_t q; memcpy(&q, &d, 8); return q; }
IMP(_ldexp) { UNUSED; double d = ldexp(R2D(r0, r1), (int)r2); uint64_t q; memcpy(&q, &d, 8); return q; }
IMP(_memchr) { UNUSED; const void *p = memchr(G8(r0), (int)r1, r2); RET(p ? r0 + (uint32_t)((const uint8_t *)p - G8(r0)) : 0); }
IMP(_strcat) { UNUSED; strcat((char *)G8(r0), gstr(r1)); RET(r0); }
IMP(_strncat) { UNUSED; strncat((char *)G8(r0), gstr(r1), r2); RET(r0); }
IMP(_strncpy) { UNUSED; strncpy((char *)G8(r0), gstr(r1), r2); RET(r0); }
IMP(_strcasecmp) { UNUSED; RET(strcasecmp(gstr(r0), gstr(r1))); }
IMP(_strcoll) { UNUSED; RET(strcoll(gstr(r0), gstr(r1))); }
IMP(_strcspn) { UNUSED; RET((uint32_t)strcspn(gstr(r0), gstr(r1))); }
IMP(_strerror) { UNUSED; static uint32_t p; if (!p) p = g_strdup("error"); RET(p); }
IMP(_strpbrk) { UNUSED; const char *s = gstr(r0), *q = strpbrk(s, gstr(r1)); RET(q ? r0 + (uint32_t)(q - s) : 0); }
IMP(_strrchr) { UNUSED; const char *s = gstr(r0), *q = strrchr(s, (int)(r1 & 255)); RET(q ? r0 + (uint32_t)(q - s) : 0); }
IMP(_strtod) { UNUSED; char *end = NULL; double d = strtod(gstr(r0), &end);
    if (r1) gw32(r1, end ? r0 + (uint32_t)(end - gstr(r0)) : r0); uint64_t q; memcpy(&q, &d, 8); return q; }
IMP(_strtol) { UNUSED; RET((uint32_t)strtol(gstr(r0), NULL, (int)r2)); }
IMP(_strtoul) { UNUSED; RET((uint32_t)strtoul(gstr(r0), NULL, (int)r2)); }
IMP(_strtok) { UNUSED; static uint32_t last; char *s = r0 ? (char *)G8(r0) : (last ? (char *)G8(last) : NULL);
    char *q = strtok(s, gstr(r1)); if (q) { last = r0 ? r0 + (uint32_t)(q - (char *)G8(r0)) : last + (uint32_t)(q - s); RET(last); } RET(0); }
IMP(_strftime) { UNUSED; RET(strftime((char *)G8(r0), r1, gstr(r2), (const struct tm *)G8(r3))); }
IMP(_clock) { UNUSED; RET((uint32_t)(plat_time_us() / 1000u)); }
IMP(_time) { UNUSED; uint32_t t = (uint32_t)(plat_time_us() / 1000000u); if (r0) gw32(r0, t); RET(t); }
IMP(_gettimeofday) { UNUSED; uint64_t us = plat_time_us(); if (r0) { gw32(r0, (uint32_t)(us / 1000000u)); gw32(r0 + 4, (uint32_t)(us % 1000000u)); } RET(0); }
IMP(_difftime) { UNUSED; double d = (double)((int32_t)r0 - (int32_t)r1); uint64_t q; memcpy(&q, &d, 8); return q; }
IMP(_gmtime) { UNUSED; static uint32_t tm; if (!tm) tm = g_malloc(44); memset(G8(tm), 0, 44); RET(tm); }
IMP(_localtime) { UNUSED; static uint32_t tm; if (!tm) tm = g_malloc(44); memset(G8(tm), 0, 44); RET(tm); }
IMP(_mktime) { UNUSED; RET(0); }
IMP(_fprintf) { UNUSED; RET(0); }
IMP(_fputc) { UNUSED; RET(r0); }
IMP(_fputs) { UNUSED; RET(0); }
IMP(_fgets) { UNUSED; RET(0); }
IMP(_fscanf) { UNUSED; RET(0); }
IMP(_feof) { UNUSED; RET(0); }
IMP(_ferror) { UNUSED; RET(0); }
IMP(_fflush) { UNUSED; RET(0); }
IMP(_clearerr) { UNUSED; RET(0); }
IMP(_getc) { UNUSED; RET((uint32_t)-1); }
IMP(_ungetc) { UNUSED; RET(r0); }
IMP(_freopen) { UNUSED; RET(0); }
IMP(_setvbuf) { UNUSED; RET(0); }
IMP(_tmpfile) { UNUSED; RET(0); }
IMP(_tmpnam) { UNUSED; RET(r0); }
IMP(_close) { UNUSED; RET(0); }
IMP(_fcntl) { UNUSED; RET(0); }
IMP(_lseek) { UNUSED; RET(0); }
IMP(_read) { UNUSED; RET(0); }
IMP(_remove) { UNUSED; RET(0); }
IMP(_rename) { UNUSED; RET(0); }
IMP(_select) { UNUSED; RET(0); }
IMP(_usleep) { UNUSED; RET(0); }
IMP(_getenv) { UNUSED; RET(0); }
IMP(_system) { UNUSED; RET(-1); }
IMP(_abort) { UNUSED; plat_fatal("abort"); RET(0); }
IMP(_sched_yield) { UNUSED; RET(0); }
IMP(_setlocale) { UNUSED; RET(0); }
IMP(_localeconv) { UNUSED; static uint32_t p; if (!p) p = g_malloc(64); memset(G8(p), 0, 64); RET(p); }
IMP(_pthread_create) { UNUSED; RET(1); }
IMP(_pthread_join) { UNUSED; RET(0); }
IMP(_pthread_exit) { UNUSED; RET(0); }
IMP(_pthread_mutex_init) { UNUSED; RET(0); }
IMP(_pthread_mutex_destroy) { UNUSED; RET(0); }
IMP(_pthread_mutex_lock) { UNUSED; RET(0); }
IMP(_pthread_mutex_unlock) { UNUSED; RET(0); }
IMP(_pthread_mutex_trylock) { UNUSED; RET(0); }
IMP(_pthread_mutexattr_init) { UNUSED; RET(0); }
IMP(_pthread_mutexattr_destroy) { UNUSED; RET(0); }
IMP(_pthread_mutexattr_settype) { UNUSED; RET(0); }
IMP(_pthread_getschedparam) { UNUSED; RET(0); }
IMP(_pthread_setschedparam) { UNUSED; RET(0); }
IMP(_setjmp) { UNUSED; RET(0); }
IMP(_longjmp) { UNUSED; plat_fatal("longjmp"); RET(0); }
IMP(___cxa_allocate_exception) { UNUSED; RET(g_malloc(r0 ? r0 : 16)); }
IMP(___cxa_throw) { UNUSED; rt_sjlj_raise(r0); RET(0); }
IMP(__ZSt9terminatev) { UNUSED; plat_fatal("terminate"); RET(0); }
IMP(___gxx_personality_sj0) { UNUSED; RET(0); }
IMP(_NSSearchPathForDirectoriesInDomains) { UNUSED; RET(0); }
IMP(_alDeleteBuffers) { UNUSED; RET(0); }
IMP(_alDeleteSources) { UNUSED; RET(0); }
IMP(_alGetSourcef) { UNUSED; if (r2) gw32(r2, 0); RET(0); }
IMP(_alSource3f) { UNUSED; RET(0); }
IMP(_alcCloseDevice) { UNUSED; RET(0); }
IMP(_alcDestroyContext) { UNUSED; RET(0); }

void rt_imports_init(void) {
    rand_state = 1; al_next = 1;
    uint32_t a = import_addr("__ZNSs4_Rep20_S_empty_rep_storageE");
    empty_rep = a ? a : g_malloc(16);
    memset(G8(empty_rep), 0, 16);
    /* _DefaultRuneLocale: the slot points at a full table so inline ctype macros work */
    uint32_t loc = g_malloc(52 + 4 * 256 + 64), slot = 0;
    for (uint32_t i = 0; i < 256; i++) gw32(loc + 52 + 4 * i, runetype(i));
    extern const uint32_t rt_import_slots[][2];
    extern const uint32_t rt_import_slot_count;
    for (uint32_t i = 0; i < rt_import_slot_count; i++)
        if (!strcmp(rt_import_names[rt_import_slots[i][1]], "__DefaultRuneLocale")) { gw32(rt_import_slots[i][0], loc); slot++; }
    (void)slot;
}
