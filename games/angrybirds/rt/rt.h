/* rt.h: what nano7kit's Angry Birds recompiled code runs on (first-party, MIT).
 *
 * Guest address a is at g_mem + a. The guest block holds the original
 * executable's constant strings and data segment at their original addresses,
 * then the import area, the heap and the stack. Recompiled functions take
 * r0..r3 as arguments and return r1:r0; the guest stack pointer is SP.
 * VFP s0-s31 overlap d0-d15 in VS[]. */
#ifndef FN_RT_H
#define FN_RT_H
#include <stdint.h>
#include <string.h>
#include <setjmp.h>

typedef uint64_t (*rt_fn)(uint32_t, uint32_t, uint32_t, uint32_t);
typedef struct { uint32_t addr; rt_fn fn; } rt_fn_entry;

extern uint8_t *g_mem;
extern uint32_t SP;
extern uint32_t g_lo, g_hi;          /* guest block bounds (addresses) */

#define RET_MAGIC 0xFFFF0001u

/* Image decode buffers of 256 KB or more live on the OS heap (plat_os_alloc).
 * On the nano (32-bit) the guest address is host_ptr - g_mem, so AT stays M+a
 * and the .hbapp does not grow. The 64-bit host harness cannot encode calloc
 * that way; it uses 0xB0000000 slabs and rt_checked/G8 (RT_CHECK). */
uint8_t *rt_outside_ptr(uint32_t a);
#ifdef RT_CHECK
uint8_t *rt_checked(uint32_t a, uint32_t n);
#define AT(a, n) rt_checked((uint32_t)(a), n)
extern uint32_t rt_watch_addr;
void rt_watch_store(uint32_t a, uint32_t v);
#else
#define AT(a, n) (M + (uint32_t)(a))
#endif
typedef uint32_t __attribute__((aligned(1), may_alias)) u32u;
typedef uint16_t __attribute__((aligned(1), may_alias)) u16u;
#define L32(a) (*(u32u *)AT(a, 4))
#define L16(a) (*(u16u *)AT(a, 2))
#define L8(a) (*(uint8_t *)AT(a, 1))
#ifdef RT_CHECK
#define S32(a, v) rt_watch_store((uint32_t)(a), (uint32_t)(v))
#else
#define S32(a, v) (*(u32u *)AT(a, 4) = (uint32_t)(v))
#endif
#define S16(a, v) (*(u16u *)AT(a, 2) = (uint16_t)(v))
#define S8(a, v) (*(uint8_t *)AT(a, 1) = (uint8_t)(v))

/* host-side access from runtime code (no local M) */
#ifdef RT_CHECK
#define G8(a) rt_outside_ptr((uint32_t)(a))
static inline uint32_t gr32(uint32_t a) { uint32_t v; memcpy(&v, rt_outside_ptr(a), 4); return v; }
static inline void gw32(uint32_t a, uint32_t v) { memcpy(rt_outside_ptr(a), &v, 4); }
#else
#define G8(a) (g_mem + (uint32_t)(a))
static inline uint32_t gr32(uint32_t a) { uint32_t v; memcpy(&v, g_mem + a, 4); return v; }
static inline void gw32(uint32_t a, uint32_t v) { memcpy(g_mem + a, &v, 4); }
#endif

#define NZ(x) do { n_ = (uint32_t)(x) >> 31; z_ = (uint32_t)(x) == 0; } while (0)
#define ADDSD(d, A, B) do { uint32_t a_ = (A), b_ = (B), r_ = a_ + b_; c_ = r_ < a_; \
    v_ = ((~(a_ ^ b_)) & (a_ ^ r_)) >> 31; d = r_; NZ(r_); } while (0)
#define SUBSD(d, A, B) do { uint32_t a_ = (A), b_ = (B), r_ = a_ - b_; c_ = a_ >= b_; \
    v_ = ((a_ ^ b_) & (a_ ^ r_)) >> 31; d = r_; NZ(r_); } while (0)
#define SUBS(A, B) do { uint32_t t2_; SUBSD(t2_, A, B); (void)t2_; } while (0)
#define ADDS_(A, B) do { uint32_t t2_; ADDSD(t2_, A, B); (void)t2_; } while (0)
#define ADCS(d, A, B) do { uint32_t a_ = (A), b_ = (B); uint64_t s_ = (uint64_t)a_ + b_ + c_; \
    uint32_t r_ = (uint32_t)s_; c_ = (uint32_t)(s_ >> 32); v_ = ((~(a_ ^ b_)) & (a_ ^ r_)) >> 31; d = r_; NZ(r_); } while (0)
#define SBCS(d, A, B) do { uint32_t a_ = (A), b_ = (B), nb_ = !c_; uint32_t r_ = a_ - b_ - nb_; \
    c_ = (uint64_t)a_ >= (uint64_t)b_ + nb_; v_ = ((a_ ^ b_) & (a_ ^ r_)) >> 31; d = r_; NZ(r_); } while (0)

static inline uint32_t lsl_reg(uint32_t x, uint32_t n) { return n < 32 ? x << n : 0; }
static inline uint32_t lsr_reg(uint32_t x, uint32_t n) { return n < 32 ? x >> n : 0; }
static inline uint32_t asr_reg(uint32_t x, uint32_t n) { return (uint32_t)((int32_t)x >> (n < 32 ? n : 31)); }
static inline uint32_t ror_reg(uint32_t x, uint32_t n) { n &= 31; return n ? (x >> n) | (x << (32 - n)) : x; }
static inline uint32_t lsl_reg_c(uint32_t x, uint32_t n, uint32_t c) { return n == 0 ? c : n <= 32 ? (x >> (32 - n)) & 1 : 0; }
static inline uint32_t lsr_reg_c(uint32_t x, uint32_t n, uint32_t c) { return n == 0 ? c : n <= 32 ? (x >> (n - 1)) & 1 : 0; }
static inline uint32_t asr_reg_c(uint32_t x, uint32_t n, uint32_t c) { return n == 0 ? c : n <= 32 ? (x >> (n - 1)) & 1 : x >> 31; }
static inline uint32_t ror_reg_c(uint32_t x, uint32_t n, uint32_t c) { n &= 31; return n ? (x >> (n - 1)) & 1 : c; }

extern uint32_t VS[32];
extern uint32_t FPSCR;
#define S(n) (*(float *)&VS[(n)])
#define DGET(n) R2D(VS[(n) * 2], VS[(n) * 2 + 1])
#define DSET(n, d) do { uint64_t q_; double dd_ = (d); memcpy(&q_, &dd_, 8); VS[(n) * 2] = (uint32_t)q_; VS[(n) * 2 + 1] = (uint32_t)(q_ >> 32); } while (0)
jmp_buf *rt_jmp(uint32_t guest);
jmp_buf *rt_sjlj_buf(uint32_t ctx);
uint32_t rt_sjlj_landed(void);
uint32_t rt_sjlj_top(void);
void rt_sjlj_register(uint32_t ctx);
void rt_sjlj_unregister(uint32_t ctx);
void rt_sjlj_raise(uint32_t exception);

static inline float U2F(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }
static inline uint32_t F2U(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }
static inline double R2D(uint32_t lo, uint32_t hi) { uint64_t q = ((uint64_t)hi << 32) | lo; double d; memcpy(&d, &q, 8); return d; }
#define D2R(d) do { uint64_t q_; double dd_ = (d); memcpy(&q_, &dd_, 8); r0 = (uint32_t)q_; r1 = (uint32_t)(q_ >> 32); } while (0)
static inline int32_t F2I(double x) { return x != x ? 0 : x >= 2147483647.0 ? 2147483647 : x <= -2147483648.0 ? (int32_t)0x80000000 : (int32_t)x; }
static inline uint32_t F2UI(double x) { return x != x || x <= 0.0 ? 0 : x >= 4294967295.0 ? 0xffffffffu : (uint32_t)x; }
static inline uint32_t SDIV(uint32_t a, uint32_t b) { if (!b) return 0; if ((int32_t)a == (int32_t)0x80000000 && (int32_t)b == -1) return a; return (uint32_t)((int32_t)a / (int32_t)b); }
static inline uint32_t SMOD(uint32_t a, uint32_t b) { if (!b) return a; if ((int32_t)b == -1) return 0; return (uint32_t)((int32_t)a % (int32_t)b); }

#define CALLQ(x) do { uint64_t q_ = (x); r0 = (uint32_t)q_; r1 = (uint32_t)(q_ >> 32); } while (0)
#define RETURN() do { SP = sp; return ((uint64_t)r1 << 32) | r0; } while (0)
#ifdef RT_TRACE
void rt_enter(uint32_t a);
#define ENTER(a) rt_enter(a)
#else
#define ENTER(a) ((void)0)
#endif

uint64_t rt_icall(uint32_t addr, uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3);
/* Optional patch of r0..r3 before a guest call. Angry Birds uses this so a
 * latched pinch/pan wins over lua cameraFunction's setTopLeft/setWorldScale. */
typedef void (*rt_icall_patch_fn)(uint32_t addr, uint32_t *r0, uint32_t *r1, uint32_t *r2, uint32_t *r3);
typedef void (*rt_icall_after_fn)(uint32_t addr, uint32_t r0, uint32_t ret);
extern rt_icall_patch_fn rt_icall_patch;
extern rt_icall_after_fn rt_icall_after;
void rt_undef(uint32_t addr);
void rt_swi(uint32_t n);

/* runtime services for the shell */
int rt_init(void *block, uint32_t block_size);
uint32_t rt_call(const char *what, uint32_t addr, uint32_t a0, uint32_t a1, uint32_t a2, uint32_t a3, const uint32_t *stack, int nstack);
uint32_t g_malloc(uint32_t n);
void g_free(uint32_t p);
uint32_t g_strdup(const char *s);
int rt_heap_poisoned(uint32_t p);   /* 1 if p is in a free guest block (UAF) */
uint32_t rt_heap_used(void), rt_heap_peak(void), rt_heap_size(void);
uint32_t rt_heap_largest_free(void);
/* GameLua::createBox/createCircle: clear leftover RenderObject slots after
 * lua destroy and before the new insert. Generated ENTER() calls this. */
void ab_on_create_object(void);
void ab_after_remove_physics(void); /* lua "Removing physics objects" */
uint32_t rt_outside_used(void), rt_outside_peak(void), rt_outside_count(void);
uint32_t rt_malloc_failures(void);
uint32_t rt_align_faults(void);
void rt_check_align8(uint32_t a);
void rt_log(const char *fmt, ...);
#endif
