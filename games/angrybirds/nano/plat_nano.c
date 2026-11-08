/* plat_nano.c: files, time, log and C library glue for the recompiled Angry Birds on the nano
 * (nano7kit, MIT). Game files live in /Apps/Data/AngryBirds: guest.img (the
 * translated executable's data) and the converted data/ files (Lua, downscaled
 * textures). wav/mp3 are not copied. hb_fs_read reads whole files. */
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <reent.h>
#include <stdlib.h>
#include <string.h>
#include "hb_sdk.h"
#include "hb_heap.h"
#include "../rt/rt_plat.h"

#define DATA_DIR "/Apps/Data/AngryBirds"
#define SLACK 8192u                     /* hb_fs_read may fill whole sectors past the end */

/* ---- allocation: the C library and the front end use these; the guest has its own heap --
 * these come out of the guest heap, inside the one block the resident frees when the app
 * unloads (HB_APP_AUX_BLOCK). An earlier build took them from the OS heap, and the atlas pages (2 x 128 KB)
 * and anything else still held at quit outlived the app (no quit callback) and sat in the
 * middle of the biggest free block. Only when the guest heap has no room past what the engine
 * itself will need (GUEST_PEAK, measured 973,920 bytes on the host harness) does an allocation
 * fall back to the OS heap; those are counted and logged (host_outside). */
#define AMAGIC 0x464e414cu              /* OS heap */
#define GMAGIC 0x464e4748u              /* guest heap */
#define GUEST_PEAK 1600000u
#define GUEST_SPARE 0x10000u
typedef struct { uint32_t size, magic, base, pad; } ahdr;   /* 16 bytes: payload stays 16-aligned */
extern uint8_t *g_mem;
extern uint32_t g_malloc(uint32_t n);
extern void g_free(uint32_t p);
extern uint32_t rt_heap_used(void), rt_heap_size(void);
uint32_t host_in_guest, host_outside, host_outside_peak, host_outside_count;
static int guest_room(uint32_t n) {
    uint32_t size = rt_heap_size(), used = rt_heap_used();
    if (!g_mem || !size || used >= size) return 0;
    uint32_t engine = used > host_in_guest ? used - host_in_guest : 0;
    uint32_t reserve = (engine < GUEST_PEAK ? GUEST_PEAK - engine : 0) + GUEST_SPARE;
    return size - used >= n + 64u + reserve;
}
void *malloc(size_t n) {
    uint32_t want = (uint32_t)n + sizeof(ahdr) + 15u;
    if (guest_room(want)) {
        uint32_t before = rt_heap_used(), a = g_malloc(want);
        if (a) {
            host_in_guest += rt_heap_used() - before;
            ahdr *h = (ahdr *)(((uintptr_t)(g_mem + a) + sizeof(ahdr) + 15u) & ~(uintptr_t)15u) - 1;
            h->size = (uint32_t)n; h->magic = GMAGIC; h->base = a;
            return h + 1;
        }
    }
    uint8_t *raw = hb_os_alloc(want);
    if (!raw) return NULL;
    ahdr *h = (ahdr *)(((uintptr_t)raw + sizeof(ahdr) + 15u) & ~(uintptr_t)15u) - 1;
    h->size = (uint32_t)n; h->magic = AMAGIC; h->base = (uint32_t)(uintptr_t)raw;
    host_outside += (uint32_t)n; host_outside_count++;
    if (host_outside > host_outside_peak) host_outside_peak = host_outside;
    return h + 1;
}
void free(void *p) {
    if (!p) return;
    ahdr *h = (ahdr *)p - 1;
    if (h->magic == GMAGIC) {
        h->magic = 0;
        uint32_t before = rt_heap_used();
        g_free(h->base);
        uint32_t back = before - rt_heap_used();
        host_in_guest = host_in_guest > back ? host_in_guest - back : 0;
    } else if (h->magic == AMAGIC) {
        h->magic = 0;
        host_outside -= h->size < host_outside ? h->size : host_outside;
        hb_os_free((void *)(uintptr_t)h->base);
    }
}
void *calloc(size_t a, size_t b) { size_t n = a * b; void *p = malloc(n); if (p) memset(p, 0, n); return p; }
void *realloc(void *p, size_t n) {
    if (!p) return malloc(n);
    ahdr *h = (ahdr *)p - 1; void *q = malloc(n);
    if (q) { memcpy(q, p, h->size < n ? h->size : n); free(p); }
    return q;
}
void *_malloc_r(struct _reent *r, size_t n) { (void)r; return malloc(n); }
void _free_r(struct _reent *r, void *p) { (void)r; free(p); }
void *_calloc_r(struct _reent *r, size_t a, size_t b) { (void)r; return calloc(a, b); }
void *_realloc_r(struct _reent *r, void *p, size_t n) { (void)r; return realloc(p, n); }
void *_sbrk(int n) { (void)n; return (void *)-1; }
int _write(int f, const void *b, int n) { (void)f; (void)b; return n; }
int _read(int f, void *b, int n) { (void)f; (void)b; (void)n; return 0; }
int _close(int f) { (void)f; return -1; }
int _lseek(int f, int o, int w) { (void)f; (void)o; (void)w; return 0; }
int _fstat(int f, void *s) { (void)f; (void)s; return -1; }
int _isatty(int f) { (void)f; return 0; }
int _getpid(void) { return 1; }
int _kill(int p, int s) { (void)p; (void)s; return -1; }
void _exit(int c) { (void)c; for (;;) {} }

/* ---- time ------------------------------------------------------------------------------- */
static uint32_t t_last, t_high;
uint64_t plat_time_us(void) {
    uint32_t now = hb_time_uptime_us();
    if (now < t_last) t_high++;
    t_last = now;
    return (uint64_t)t_high << 32 | now;
}

/* ---- log: kept in memory, appended to log.txt after start-up, each level load, every so
 * often and on a fatal. hb_fs_write truncates, so a flush that rewrote the whole buffer
 * after rotation dropped earlier LEVEL lines; append the unflushed tail instead. */
#define LOG_CAP 32768u
/* the log and the archive chunk live in .bss, inside the app image the resident frees on
 * unload: an hb_os_alloc here would outlive the app (there is no quit callback) */
static char log_store[LOG_CAP + SLACK] __attribute__((aligned(64)));
static char *log_buf;
static uint32_t log_len, log_dirty, log_disk;
void plat_log_line(const char *line) {
    if (!log_buf) { log_buf = log_store; log_len = 0; log_disk = 0; }
    uint32_t n = (uint32_t)strlen(line);
    if (n > 200) n = 200;
    if (log_len + n + 1 > LOG_CAP) {
        /* keep what is already on disk; drop only unflushed bytes from the front of
         * the unwritten tail so a 1-1, 1-2, 1-3 run still appends LEVEL lines. */
        if (log_disk > 0 && log_disk <= log_len) {
            memmove(log_buf, log_buf + log_disk, log_len - log_disk);
            log_len -= log_disk;
            log_disk = 0;
        }
        if (log_len + n + 1 > LOG_CAP) return;
    }
    memcpy(log_buf + log_len, line, n); log_len += n; log_buf[log_len++] = '\n';
    log_dirty = 1;
}
void plat_log_flush(void) {
    uint32_t extra, oldn = 0;
    uint8_t *old = NULL;
    void *ptrs[2];
    uint32_t lens[2];
    int nparts = 0;
    if (!log_buf || !log_dirty) return;
    if (log_disk > log_len) log_disk = 0;
    extra = log_len - log_disk;
    if (!extra) { log_dirty = 0; return; }
    hb_fs_mkdir(DATA_DIR);
    if (hb_fs_exists(DATA_DIR "/log.txt")) {
        oldn = hb_fs_size(DATA_DIR "/log.txt");
        if (oldn > 96u * 1024u) {
            uint8_t *full = malloc(oldn);
            uint32_t keep = 64u * 1024u;
            if (full) {
                uint32_t got = hb_fs_read(DATA_DIR "/log.txt", full, oldn);
                if (got > keep) {
                    old = malloc(keep);
                    if (old) { memcpy(old, full + got - keep, keep); oldn = keep; }
                    else oldn = 0;
                } else {
                    old = full; full = NULL; oldn = got;
                }
                free(full);
            } else oldn = 0;
        } else if (oldn) {
            old = malloc(oldn);
            if (old) oldn = hb_fs_read(DATA_DIR "/log.txt", old, oldn);
            else oldn = 0;
        }
    }
    if (old && oldn) { ptrs[nparts] = old; lens[nparts] = oldn; nparts++; }
    ptrs[nparts] = log_buf + log_disk; lens[nparts] = extra; nparts++;
    hb_fs_write_parts(DATA_DIR "/log.txt", ptrs, lens, nparts);
    free(old);
    log_disk = log_len;
    log_dirty = 0;
}
void plat_trace(void) {}
extern void fn_nano_fatal(void);
void plat_fatal(const char *why) {
    char b[160];
    snprintf(b, sizeof b, "nano7kit Angry Birds fatal: %s", why);
    plat_log_line(b);
    plat_log_flush();
    fn_nano_fatal();
}

/* ---- files ------------------------------------------------------------------------------ */
static void path_of(char *out, size_t n, const char *name) { snprintf(out, n, DATA_DIR "/%s", name); }
int32_t plat_file_size(const char *name) {
    char p[96]; path_of(p, sizeof p, name);
    if (!hb_fs_exists(p)) return -1;
    return (int32_t)hb_fs_size(p);
}
int32_t plat_file_read(const char *name, uint32_t off, void *dst, uint32_t n) {
    uint8_t *d = dst;
    char p[96]; path_of(p, sizeof p, name);
    uint32_t size = hb_fs_size(p);
    if (!size || off >= size) return off == size ? 0 : -1;
    uint8_t *tmp = malloc(size + SLACK);
    if (!tmp) return -1;
    int32_t got = (int32_t)hb_fs_read(p, tmp, size + SLACK);
    if (got != (int32_t)size) { free(tmp); return -1; }
    uint32_t take = size - off < n ? size - off : n;
    memcpy(d, tmp + off, take);
    free(tmp);
    return (int32_t)take;
}
int plat_file_write(const char *name, const void *src, uint32_t n) {
    char p[96]; path_of(p, sizeof p, name);
    hb_fs_mkdir(DATA_DIR);
    return hb_fs_write(p, src, n) ? 0 : -1;
}
void *plat_os_alloc(uint32_t n) {
    /* GL scratch (triangle pool, clip buffer) and image decode buffers stay off
     * the guest heap and off .bss. */
    return hb_os_alloc(n ? n : 1u);
}
void plat_os_free(void *p) {
    if (p) hb_os_free(p);
}
uint32_t plat_os_largest(void) {
    return hb_os_heap_largest();
}
uint32_t plat_load_image(void *dst, uint32_t size) {
    /* the guest block has the heap after the image, so the read may run past size by SLACK */
    return hb_fs_read(DATA_DIR "/guest.img", dst, size + SLACK) == size ? size : 0;
}
void plat_nano_reset(void) { host_in_guest = host_outside = host_outside_peak = host_outside_count = 0; t_last = t_high = 0; log_buf = NULL; log_len = log_dirty = log_disk = 0; }
