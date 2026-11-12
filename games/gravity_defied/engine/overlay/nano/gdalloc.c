/* One dlmalloc mspace in the app aux block (freed on Home). */
#include "gdalloc.h"
#include "../gd_config.h"
#include <string.h>
#include <errno.h>

#define ONLY_MSPACES 1
#define MSPACES 1
#define HAVE_MMAP 0
#define HAVE_MORECORE 0
#define USE_LOCKS 0
#define USE_DL_PREFIX 1
#define LACKS_SYS_MMAN_H 1
#define NO_MALLOC_STATS 1
#define MALLOC_FAILURE_ACTION errno = ENOMEM
#define DEFAULT_GRANULARITY (32 * 1024)
#define FOOTERS 0
#define INSECURE 1
#if defined(__arm__) && !defined(__linux__)
#define LACKS_UNISTD_H 1
#define LACKS_SYS_PARAM_H 1
#define LACKS_SCHED_H 1
#define LACKS_TIME_H 1
#define MALLOC_ALIGNMENT 8
#define ABORT do { gd_fatal("heap corrupted"); } while (0)
#endif
#include "malloc-2.8.6.c"

static mspace s_ms;
static size_t s_size, s_used, s_peak, s_fails, s_count;
static unsigned char s_bump[8192];
static size_t s_bump_off;

static void refresh_used(void)
{
    if (!s_ms) {
        s_used = s_bump_off;
        if (s_used > s_peak) s_peak = s_used;
        return;
    }
    struct mallinfo mi = mspace_mallinfo(s_ms);
    s_used = (size_t)mi.uordblks;
    if (s_used > s_peak) s_peak = s_used;
}

int gd_arena_init(void *base, size_t size)
{
    s_ms = create_mspace_with_base(base, size, 0);
    if (!s_ms) return -1;
    s_size = size;
    s_used = s_peak = s_fails = s_count = 0;
    refresh_used();
    return 0;
}

int gd_arena_ready(void) { return s_ms != 0; }

void gd_arena_stats(gd_arena_stats_t *s)
{
    if (!s) return;
    s->size = s_size;
    s->used = s_used;
    s->peak = s_peak;
    s->fails = s_fails;
    s->blocks = s_count;
}

void *gd_malloc(size_t n)
{
    void *p;
    if (n == 0) n = 1;
    if (!s_ms) {
        n = (n + 7u) & ~7u;
        if (s_bump_off + n > sizeof s_bump) {
            s_fails++;
            return 0;
        }
        p = s_bump + s_bump_off;
        s_bump_off += n;
        s_count++;
        refresh_used();
        return p;
    }
    p = mspace_malloc(s_ms, n);
    if (!p) {
        s_fails++;
        return 0;
    }
    s_count++;
    refresh_used();
    return p;
}

void gd_free(void *p)
{
    if (!p) return;
    if ((unsigned char *)p >= s_bump && (unsigned char *)p < s_bump + sizeof s_bump)
        return;
    if (!s_ms) return;
    mspace_free(s_ms, p);
    refresh_used();
}

void *gd_realloc(void *p, size_t n)
{
    void *q;
    if (!p) return gd_malloc(n);
    if (!s_ms || ((unsigned char *)p >= s_bump && (unsigned char *)p < s_bump + sizeof s_bump)) {
        q = gd_malloc(n);
        if (q && p) memcpy(q, p, n);
        return q;
    }
    q = mspace_realloc(s_ms, p, n);
    if (!q) s_fails++;
    refresh_used();
    return q;
}

void *gd_calloc(size_t a, size_t b)
{
    size_t n;
    void *p;
    if (b && a > (size_t)-1 / b) return 0;
    n = a * b;
    p = gd_malloc(n);
    if (p) memset(p, 0, n);
    return p;
}

unsigned gd_arena_used(void) { return (unsigned)s_used; }
unsigned gd_arena_peak(void) { return (unsigned)s_peak; }
unsigned gd_arena_fails(void) { return (unsigned)s_fails; }
