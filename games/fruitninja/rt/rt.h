/* rt.h: what nano7kit's Fruit Ninja recompiled code runs on (first-party, MIT).
 *
 * Guest address a is at g_mem + a. The guest block holds the original
 * executable's constant strings and data segment at their original addresses,
 * then the import area, the heap and the stack. Recompiled functions take
 * r0..r3 as arguments and return r1:r0; the guest stack pointer is SP. */
#ifndef FN_RT_H
#define FN_RT_H
#include <stdint.h>
#include <string.h>

typedef uint64_t (*rt_fn)(uint32_t, uint32_t, uint32_t, uint32_t);
typedef struct { uint32_t addr; rt_fn fn; } rt_fn_entry;

extern uint8_t *g_mem;
extern uint32_t SP;
extern uint32_t g_lo, g_hi;          /* guest block bounds (addresses) */

#define RET_MAGIC 0xFFFF0001u

#ifdef RT_CHECK
uint8_t *rt_checked(uint32_t a, uint32_t n);
#define AT(a, n) rt_checked((uint32_t)(a), n)
#else
#define AT(a, n) (M + (uint32_t)(a))
#endif
typedef uint32_t __attribute__((aligned(1), may_alias)) u32u;
typedef uint16_t __attribute__((aligned(1), may_alias)) u16u;
#define L32(a) (*(u32u *)AT(a, 4))
#define L16(a) (*(u16u *)AT(a, 2))
#define L8(a) (*(uint8_t *)AT(a, 1))
#define S32(a, v) (*(u32u *)AT(a, 4) = (uint32_t)(v))
#define S16(a, v) (*(u16u *)AT(a, 2) = (uint16_t)(v))
#define S8(a, v) (*(uint8_t *)AT(a, 1) = (uint8_t)(v))

/* host-side access from runtime code (no local M) */
#define G8(a) (g_mem + (uint32_t)(a))
static inline uint32_t gr32(uint32_t a) { uint32_t v; memcpy(&v, g_mem + a, 4); return v; }
static inline void gw32(uint32_t a, uint32_t v) { memcpy(g_mem + a, &v, 4); }

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
static inline uint32_t lsl_reg_c(uint32_t x, uint32_t n, uint32_t c) { return n == 0 ? c : n <= 32 ? (x >> (32 - n)) & 1 : 0; }
static inline uint32_t lsr_reg_c(uint32_t x, uint32_t n, uint32_t c) { return n == 0 ? c : n <= 32 ? (x >> (n - 1)) & 1 : 0; }
static inline uint32_t asr_reg_c(uint32_t x, uint32_t n, uint32_t c) { return n == 0 ? c : n <= 32 ? (x >> (n - 1)) & 1 : x >> 31; }

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
void rt_undef(uint32_t addr);
void rt_swi(uint32_t n);

/* runtime services for the shell */
int rt_init(void *block, uint32_t block_size);
uint32_t rt_call(const char *what, uint32_t addr, uint32_t a0, uint32_t a1, uint32_t a2, uint32_t a3, const uint32_t *stack, int nstack);
uint32_t g_malloc(uint32_t n);
void g_free(uint32_t p);
uint32_t g_strdup(const char *s);
uint32_t rt_heap_used(void), rt_heap_peak(void), rt_heap_size(void);
void rt_log(const char *fmt, ...);
#endif
