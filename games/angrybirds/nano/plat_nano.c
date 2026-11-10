/* plat_nano.c: files, time, log and C library glue for Android Angry Birds on the nano
 * (nano7kit, MIT). Game files live in /Apps/Data/AngryBirds. wav/mp3 are not copied. */
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <reent.h>
#include <stdlib.h>
#include <string.h>
#include "hb_sdk.h"
#include "hb_heap.h"
#include "../rt/audio_stub.h"
#include "../rt/rt_plat.h"
#include "../rt/fngl.h"
#include "../rt/shims.h"

#define DATA_DIR "/Apps/Data/AngryBirds"
#define FILES_DIR "/Apps/Data/AngryBirds/docs"
#define SLACK 8192u

#define AMAGIC 0x464e414cu
#define GL_KEEP 0x100000u   /* 1 MB leftover largest (music playing); GL uses plat_os_alloc */
typedef struct { uint32_t size, magic, base, pad; } ahdr;
uint32_t host_outside, host_outside_peak, host_outside_count, host_in_pool;

void *malloc(size_t n) {
    uint32_t want;
    uint8_t *raw;
    ahdr *h;
    char b[96];
    if (ab_heap_ready()) {
        void *p = ab_malloc(n);
        if (p) { host_in_pool += (uint32_t)n; return p; }
        /* pool empty: only the OS heap is left, and it must keep GL's share */
    }
    want = (uint32_t)n + sizeof(ahdr) + 15u;
    if (hb_os_heap_largest() < GL_KEEP + want + 0x10000u) {
        snprintf(b, sizeof b, "malloc %u refused (largest %u, keep GL)",
                 (unsigned)n, (unsigned)hb_os_heap_largest());
        plat_log_line(b);
        plat_log_flush();
        return NULL;
    }
    raw = hb_os_alloc(want);
    if (!raw) {
        snprintf(b, sizeof b, "malloc %u OS NULL (free %u largest %u)",
                 (unsigned)n, (unsigned)hb_os_heap_free(), (unsigned)hb_os_heap_largest());
        plat_log_line(b);
        plat_log_flush();
        return NULL;
    }
    h = (ahdr *)(((uintptr_t)raw + sizeof(ahdr) + 15u) & ~(uintptr_t)15u) - 1;
    h->size = (uint32_t)n; h->magic = AMAGIC; h->base = (uint32_t)(uintptr_t)raw;
    host_outside += (uint32_t)n; host_outside_count++;
    if (host_outside > host_outside_peak) host_outside_peak = host_outside;
    if (n >= 4096u) {
        snprintf(b, sizeof b, "malloc OS %u free=%u largest=%u",
                 (unsigned)n, (unsigned)hb_os_heap_free(), (unsigned)hb_os_heap_largest());
        plat_log_line(b);
        plat_log_flush();
    }
    return h + 1;
}
void free(void *p) {
    ahdr *h;
    if (!p) return;
    if (ab_heap_contains(p)) { ab_free(p); return; }
    h = (ahdr *)p - 1;
    if (h->magic != AMAGIC) return;
    h->magic = 0;
    host_outside -= h->size < host_outside ? h->size : host_outside;
    hb_os_free((void *)(uintptr_t)h->base);
}
void *calloc(size_t a, size_t b) { size_t n = a * b; void *p = malloc(n); if (p) memset(p, 0, n); return p; }
void *realloc(void *p, size_t n) {
    ahdr *h; void *q; uint32_t old = 0;
    if (!p) return malloc(n);
    if (ab_heap_contains(p)) return ab_realloc(p, n);
    h = (ahdr *)p - 1;
    old = h->magic == AMAGIC ? h->size : 0;
    q = malloc(n);
    if (q) { memcpy(q, p, old < n ? old : n); free(p); }
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

static uint32_t t_last, t_high;
uint64_t plat_time_us(void) {
    uint32_t now = hb_time_uptime_us();
    if (now < t_last) t_high++;
    t_last = now;
    return (uint64_t)t_high << 32 | now;
}

#define LOG_CAP 32768u
static char log_store[LOG_CAP + SLACK] __attribute__((aligned(64)));
static char sync_store[256] __attribute__((aligned(64)));
static uint32_t log_len, log_dirty;
static void write_sync(void)
{
    uint32_t e = log_len, b, k;
    while (e && log_store[e - 1] == '\n') e--;
    b = e;
    while (b && log_store[b - 1] != '\n') b--;
    k = e - b;
    if (k > 240) k = 240;
    memcpy(sync_store, log_store + b, k);
    sync_store[k++] = '\n';
    hb_fs_write(DATA_DIR "/sync.txt", sync_store, k);
}
void plat_log_line(const char *line) {
    uint32_t n;
    if (!line) return;
    n = (uint32_t)strlen(line);
    if (n > 200) n = 200;
    if (log_len + n + 1 > LOG_CAP) {
        uint32_t keep = LOG_CAP / 2, cut;
        cut = log_len - keep < n + 1 + 1024 ? log_len - keep : n + 1 + 1024;
        memmove(log_store + keep, log_store + keep + cut, log_len - keep - cut);
        log_len -= cut;
    }
    memcpy(log_store + log_len, line, n); log_len += n; log_store[log_len++] = '\n';
    log_dirty = 1;
    /* Every line: log.txt then sync.txt. lasttrace.bin is not written after a
     * watchdog/hard reset (DRAM is gone); this FAT pair is the device log. */
    plat_log_flush();
}
void plat_log_flush(void) {
    if (!log_dirty || !log_len) return;
    hb_fs_mkdir("/Apps/Data");
    hb_fs_mkdir(DATA_DIR);
    hb_fs_write(DATA_DIR "/log.txt", log_store, log_len);
    log_dirty = 0;
    write_sync();
}
extern void fn_nano_fatal(void);
void plat_fatal(const char *why) {
    char b[160];
    snprintf(b, sizeof b, "nano7kit Angry Birds fatal: %s", why);
    plat_log_line(b);
    plat_log_flush();
    fn_nano_fatal();
}

const char *plat_files_dir(void) { return FILES_DIR; }
const char *plat_data_dir(void) { return DATA_DIR; }

int plat_exists_abs(const char *path) {
    return path && path[0] && hb_fs_exists(path) ? 1 : 0;
}
int plat_mkdir_abs(const char *path) {
    if (!path || !path[0]) return -1;
    hb_fs_mkdir("/Apps/Data");
    hb_fs_mkdir(DATA_DIR);
    return hb_fs_mkdir(path) ? 0 : -1;
}
int plat_write_abs(const char *path, const void *buf, uint32_t n) {
    if (!path || !path[0]) return -1;
    return hb_fs_write(path, buf, n) ? 0 : -1;
}
int plat_read_abs(const char *path, void **out, uint32_t *len) {
    uint32_t size, got;
    uint8_t *raw, *tmp;
    if (out) *out = 0;
    if (len) *len = 0;
    if (!path || !path[0] || !hb_fs_exists(path)) return -1;
    size = hb_fs_size(path);
    raw = malloc(size + SLACK + 64 + 1);
    if (!raw) return -1;
    tmp = (uint8_t *)(((uintptr_t)raw + 63u) & ~(uintptr_t)63u);
    memset(tmp, 0, size + 1);
    /* FAT may fill whole sectors past EOF into slack. Never use that
     * count as lua_load's length: leftover valid Lua + 0xA5 junk is
     * LUA_ERRSYNTAX=3. Always expose hb_fs_size bytes, NUL-terminated. */
    got = hb_fs_read(path, tmp, size + SLACK);
    if (got < size) { free(raw); return -1; }
    if (got != size) {
        char b[160];
        snprintf(b, sizeof b, "plat_read_abs '%s' size=%u got=%u (FAT slack ignored)",
                 path, (unsigned)size, (unsigned)got);
        plat_log_line(b);
        plat_log_flush();
    }
    if (tmp != raw) memmove(raw, tmp, size);
    raw[size] = 0;
    if (out) *out = raw;
    if (len) *len = size; /* never slack: leftover+junk is lua err=3 */
    return 0;
}

int plat_read_file(const char *name, void **out, uint32_t *len) {
    char p[96];
    char log[192];
    const char *req = name ? name : "";
    const char *base = req;
    const char *slash;
    const char *leaf;
    uint32_t size;
    void *buf = 0;
    ab_note_io(req);
    fe_note_texfile(name);
    snprintf(log, sizeof log, "readFile begin '%s'", req);
    plat_log_line(log);
    plat_log_flush();
    p[0] = 0;
    /* Persist is docs/ only. Leftover parent saves are copied once by
     * ab_persist_prepare. Packed data/ is never a persist fallback. */
    if (ab_persist_name(req)) {
        leaf = ab_persist_leaf(req);
        if (leaf[0]) snprintf(p, sizeof p, FILES_DIR "/%s", leaf);
        if (!p[0] || !hb_fs_exists(p) || !ab_persist_in_docs(p)) {
            snprintf(log, sizeof log, "readFile miss '%s'", req);
            plat_log_line(log);
            plat_log_flush();
            snprintf(log, sizeof log, "%s empty (missing)", leaf[0] ? leaf : "persist");
            plat_log_line(log);
            plat_log_flush();
            return ab_persist_empty(out, len);
        }
        goto have;
    }
    if (req[0] == '/' && hb_fs_exists(req)) {
        snprintf(p, sizeof p, "%s", req);
        goto have;
    }
    if (!strncmp(base, "data/", 5)) base += 5;
    snprintf(p, sizeof p, DATA_DIR "/%s", base);
    if (hb_fs_exists(p)) goto have;
    slash = strrchr(base, '/');
    leaf = (slash && slash[1]) ? slash + 1 : base;
    if (leaf[0] && !ab_persist_name(leaf)) {
        snprintf(p, sizeof p, DATA_DIR "/%s", leaf);
        if (hb_fs_exists(p)) goto have;
    }
    snprintf(log, sizeof log, "readFile miss '%s'", req);
    plat_log_line(log);
    plat_log_flush();
    {
        int stub = ab_stub_audio(name, out, len);
        return stub == 1 ? 0 : -1;
    }
have:
    if (plat_read_abs(p, &buf, &size)) {
        snprintf(log, sizeof log, "readFile '%s' read fail", p);
        plat_log_line(log);
        plat_log_flush();
        return -1;
    }
    snprintf(log, sizeof log, "readFile resolved '%s' %u bytes (pool used %u fail %u, OS free %u largest %u)",
             p, (unsigned)size, (unsigned)ab_heap_used(), (unsigned)ab_malloc_failures(),
             (unsigned)hb_os_heap_free(), (unsigned)hb_os_heap_largest());
    plat_log_line(log);
    plat_log_flush();
    if (!ab_persist_lua_ok(name, buf, size)) {
        snprintf(log, sizeof log, "%s skip invalid %u bytes",
                 ab_persist_leaf(name), (unsigned)size);
        plat_log_line(log);
        plat_log_flush();
        free(buf);
        snprintf(log, sizeof log, "%s empty (invalid)", ab_persist_leaf(name));
        plat_log_line(log);
        plat_log_flush();
        return ab_persist_empty(out, len);
    }
    *out = buf; *len = size;
    return 0;
}

void *plat_os_alloc(uint32_t n) { return hb_os_alloc(n ? n : 1u); }
void plat_os_free(void *p) { if (p) hb_os_free(p); }
uint32_t plat_os_largest(void) { return hb_os_heap_largest(); }
void plat_nano_reset(void) {
    host_outside = host_outside_peak = host_outside_count = host_in_pool = 0;
    t_last = t_high = 0;
    /* Keep log_store: payload_entry crumbs must survive this reset. */
}
