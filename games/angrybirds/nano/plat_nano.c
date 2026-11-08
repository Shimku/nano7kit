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

#define DATA_DIR "/Apps/Data/AngryBirds"
#define FILES_DIR "/Apps/Data/AngryBirds/docs"
#define SLACK 8192u

#define AMAGIC 0x464e414cu
typedef struct { uint32_t size, magic, base, pad; } ahdr;
uint32_t host_outside, host_outside_peak, host_outside_count;

void *malloc(size_t n) {
    uint32_t want = (uint32_t)n + sizeof(ahdr) + 15u;
    uint8_t *raw = hb_os_alloc(want);
    ahdr *h;
    if (!raw) return NULL;
    h = (ahdr *)(((uintptr_t)raw + sizeof(ahdr) + 15u) & ~(uintptr_t)15u) - 1;
    h->size = (uint32_t)n; h->magic = AMAGIC; h->base = (uint32_t)(uintptr_t)raw;
    host_outside += (uint32_t)n; host_outside_count++;
    if (host_outside > host_outside_peak) host_outside_peak = host_outside;
    return h + 1;
}
void free(void *p) {
    ahdr *h;
    if (!p) return;
    h = (ahdr *)p - 1;
    if (h->magic != AMAGIC) return;
    h->magic = 0;
    host_outside -= h->size < host_outside ? h->size : host_outside;
    hb_os_free((void *)(uintptr_t)h->base);
}
void *calloc(size_t a, size_t b) { size_t n = a * b; void *p = malloc(n); if (p) memset(p, 0, n); return p; }
void *realloc(void *p, size_t n) {
    ahdr *h; void *q;
    if (!p) return malloc(n);
    h = (ahdr *)p - 1; q = malloc(n);
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

static uint32_t t_last, t_high;
uint64_t plat_time_us(void) {
    uint32_t now = hb_time_uptime_us();
    if (now < t_last) t_high++;
    t_last = now;
    return (uint64_t)t_high << 32 | now;
}

#define LOG_CAP 32768u
static char log_store[LOG_CAP + SLACK] __attribute__((aligned(64)));
static char *log_buf;
static uint32_t log_len, log_dirty, log_disk;
void plat_log_line(const char *line) {
    uint32_t n;
    if (!log_buf) { log_buf = log_store; log_len = 0; log_disk = 0; }
    n = (uint32_t)strlen(line);
    if (n > 200) n = 200;
    if (log_len + n + 1 > LOG_CAP) {
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
        if (oldn) {
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

int plat_read_file(const char *name, void **out, uint32_t *len) {
    char p[96];
    const char *base = name ? name : "";
    fe_note_texfile(name);
    uint32_t size;
    uint8_t *tmp;
    if (!strncmp(base, "data/", 5)) base += 5;
    snprintf(p, sizeof p, DATA_DIR "/%s", base);
    if (!hb_fs_exists(p)) {
        const char *slash = strrchr(base, '/');
        if (slash) snprintf(p, sizeof p, DATA_DIR "/%s", slash + 1);
    }
    if (!hb_fs_exists(p)) {
        int stub = ab_stub_audio(name, out, len);
        return stub == 1 ? 0 : -1;
    }
    size = hb_fs_size(p);
    tmp = malloc(size + SLACK);
    if (!tmp) return -1;
    if (hb_fs_read(p, tmp, size + SLACK) < size) { free(tmp); return -1; }
    *out = tmp; *len = size;
    return 0;
}

void *plat_os_alloc(uint32_t n) { return hb_os_alloc(n ? n : 1u); }
void plat_os_free(void *p) { if (p) hb_os_free(p); }
uint32_t plat_os_largest(void) { return hb_os_heap_largest(); }
void plat_nano_reset(void) {
    host_outside = host_outside_peak = host_outside_count = 0;
    t_last = t_high = 0; log_buf = NULL; log_len = log_dirty = log_disk = 0;
}
