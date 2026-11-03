/* nano7 PvZ device heap: every allocation comes from ONE arena (on the iPod the app's aux block, which the resident
 * frees when the app unloads; in the desktop dry run a fixed block of the same size). dlmalloc (Doug Lea, public
 * domain, fetched at build time) manages it as an mspace. The arena never grows: if it is full, malloc returns NULL.
 * Stats: bytes in use, the peak, and the arena high-water mark (highest byte ever handed out), which is the real
 * size the arena must have, fragmentation included. */
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#include "n7alloc.h"
#ifdef N7_ARENA_LAZY
#include <stdio.h>
#include <stdlib.h>
#endif

#define ONLY_MSPACES 1
#define MSPACES 1
#define HAVE_MMAP 0
#define HAVE_MORECORE 0
#define USE_LOCKS 0
#define USE_DL_PREFIX 1
#define LACKS_SYS_MMAN_H 1
#define NO_MALLOC_STATS 1
#define MALLOC_FAILURE_ACTION errno = ENOMEM
#define DEFAULT_GRANULARITY (64 * 1024)
#define FOOTERS 0
#define INSECURE 1
#if defined(__arm__) && !defined(__linux__)
#define LACKS_UNISTD_H 1
#define LACKS_SYS_PARAM_H 1
#define LACKS_SCHED_H 1
#define LACKS_TIME_H 1
#define MALLOC_ALIGNMENT 8
#define ABORT do { extern void n7_fatal(const char*); n7_fatal("heap corrupted"); } while (0)
#endif
#include "third_party/malloc-2.8.6.c"

static mspace s_ms, s_ms2;   /* the arena, and optionally a second block when the OS heap has no single one big enough */
static uintptr_t s_base, s_end, s_hw, s_base2, s_end2;
static size_t s_size, s_used, s_peak, s_fails, s_count, s_biggest_fail;

int n7_arena_init(void* base, size_t size)
{
	s_ms = create_mspace_with_base(base, size, 0);
	if (!s_ms) return -1;
	s_base = (uintptr_t)base; s_end = s_base + size; s_size = size; s_hw = 0; s_ms2 = 0; s_base2 = s_end2 = 0;
	s_used = s_peak = s_fails = s_count = s_biggest_fail = 0;
	return 0;
}
int n7_arena_add(void* base, size_t size)
{
	if (!s_ms || s_ms2) return -1;
	s_ms2 = create_mspace_with_base(base, size, 0);
	if (!s_ms2) return -1;
	s_base2 = (uintptr_t)base; s_end2 = s_base2 + size; s_size += size;
	return 0;
}
int n7_arena_ready(void) { return s_ms != 0; }
static mspace owner(void* p) { return s_ms2 && (uintptr_t)p >= s_base2 && (uintptr_t)p < s_end2 ? s_ms2 : s_ms; }

#ifdef N7_ARENA_LAZY   /* desktop dry run: a static arena of NANO7_ARENA_KB (default 3584 KB = 3.5 MB) */
#include <stdlib.h>
extern char* n7_getenv(const char*);
static void lazy(void)
{
	if (s_ms) return;
	static char* blk;
	size_t kb = 3584;
	const char* e = n7_getenv("NANO7_ARENA_KB"); if (e) kb = (size_t)strtoul(e, 0, 10);
	extern void* n7_host_block(size_t);
	blk = (char*)n7_host_block(kb * 1024);
	n7_arena_init(blk, kb * 1024);
	const char* e2 = n7_getenv("NANO7_ARENA2_KB");   /* a second block, as the device takes when the heap is split */
	if (e2 && strtoul(e2, 0, 10)) { size_t k2 = (size_t)strtoul(e2, 0, 10); n7_arena_add(n7_host_block(k2 * 1024), k2 * 1024); }
}
#define LAZY() lazy()
#else
#define LAZY() do { } while (0)
#endif

static void note(void* p)
{
	if (!p) return;
	size_t u = mspace_usable_size(p) + 4;
	s_used += u; s_count++;
	if (s_used > s_peak) s_peak = s_used;
	if ((uintptr_t)p >= s_base && (uintptr_t)p < s_end) { uintptr_t top = (uintptr_t)p + u - s_base; if (top > s_hw) s_hw = top; }
}
static void unnote(void* p) { if (p) { s_used -= mspace_usable_size(p) + 4; s_count--; } }
static void* fail(size_t n)
{
	s_fails++; if (n > s_biggest_fail) s_biggest_fail = n;
#ifdef N7_ARENA_LAZY   /* dry run: say which request failed, and stop with a stack when asked */
	fprintf(stderr, "NANO7 ALLOC FAIL %lu bytes (arena used %lu)\n", (unsigned long)n, (unsigned long)s_used);
	if (getenv("NANO7_FAILTRAP")) *(volatile int*)0 = 0;
#endif
	return 0;
}

static void* get(size_t n)
{
	void* p = s_ms ? mspace_malloc(s_ms, n) : 0;
	if (!p && s_ms2) p = mspace_malloc(s_ms2, n);
	return p;
}
void* malloc(size_t n) { LAZY(); void* p = get(n); if (!p) return fail(n); note(p); return p; }
void free(void* p) { if (!p) return; unnote(p); mspace_free(owner(p), p); }
void* calloc(size_t a, size_t b)
{
	LAZY(); size_t n = a * b; if (b && n / b != a) return fail((size_t)-1);
	void* p = get(n); if (!p) return fail(n);
	memset(p, 0, n); note(p); return p;
}
void* realloc(void* p, size_t n)
{
	LAZY();
	if (!p) return malloc(n);
	if (!n) { free(p); return 0; }
	size_t old = mspace_usable_size(p) + 4;
	void* q = mspace_realloc(owner(p), p, n);
	if (!q && s_ms2) {   /* full in its own block: move it to wherever it fits */
		q = get(n);
		if (q) { memcpy(q, p, old - 4 < n ? old - 4 : n); mspace_free(owner(p), p); }
	}
	if (!q) return fail(n);
	s_used -= old; s_count--; note(q);
	return q;
}
void* memalign(size_t a, size_t n)
{
	LAZY(); void* p = s_ms ? mspace_memalign(s_ms, a, n) : 0;
	if (!p && s_ms2) p = mspace_memalign(s_ms2, a, n);
	if (!p) return fail(n); note(p); return p;
}
void* aligned_alloc(size_t a, size_t n) { return memalign(a, n); }
int posix_memalign(void** r, size_t a, size_t n) { void* p = memalign(a, n); if (!p) return ENOMEM; *r = p; return 0; }
size_t malloc_usable_size(void* p) { return p ? mspace_usable_size(p) : 0; }

/* largest single block still available (walks the free bins via mallinfo is not enough; probe by halving) */
size_t n7_arena_largest(void)
{
	size_t best = 0;
	for (int i = 0; i < 2; i++) {
		mspace m = i ? s_ms2 : s_ms;
		if (!m) continue;
		size_t lo = 0, hi = s_size;
		while (hi - lo > 4096) {
			size_t mid = lo + (hi - lo) / 2;
			void* p = mspace_malloc(m, mid);
			if (p) { mspace_free(m, p); lo = mid; } else hi = mid;
		}
		if (lo > best) best = lo;
	}
	return best;
}
void n7_arena_stats(n7_arena_stats_t* s)
{
	s->size = s_size; s->used = s_used; s->peak = s_peak; s->high_water = s_hw;
	s->fails = s_fails; s->biggest_fail = s_biggest_fail; s->blocks = s_count;
}
void n7_arena_reset_peak(void) { s_peak = s_used; }
