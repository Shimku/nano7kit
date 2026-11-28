/*
 * tllm.c: Tiny LLM inference engine. See tllm.h.
 *
 * The model maths follow llama2.c by Andrej Karpathy (MIT): RMSNorm,
 * grouped-query attention with RoPE, a SwiGLU feed-forward block and a
 * classifier shared with the token embedding. The sampler and the BPE
 * tokenizer follow run.c too. What is different here is the number
 * format, chosen for a Cortex-A5 built with soft float:
 *
 *   - weights are int8 with one float scale per row (mkmodel.py);
 *   - activations are quantised to int8 before each matrix multiply, so
 *     the inner loops are integer multiply-accumulates (SMLAD where the
 *     core has it), and floats only appear once per output row;
 *   - keys are cached as int16 Q9, values as int16 Q12, and the softmax
 *     over past positions is done in integers with a 2^-x table.
 */
#include "tllm.h"

#if defined(__arm__) && !defined(__clang__)
/* The SDK builds with -Os; the inner loops here are worth -O2. */
#pragma GCC optimize("O2", "no-tree-loop-distribute-patterns")
#endif

#if defined(__ARM_FEATURE_SIMD32) && __ARM_FEATURE_SIMD32
#include <arm_acle.h>
#define TLLM_SIMD 1
#endif

#define K_SHIFT 9    /* key cache: value * 512 */
#define V_SHIFT 12   /* value cache: value * 4096 */
#define Q_SHIFT 9    /* query, already divided by sqrt(head_size) */
#define LOG2E_Q16 94548  /* log2(e) * 65536 */
#define SCORE_FRAC (K_SHIFT + Q_SHIFT)

static uint32_t s_exp2_tab[256];   /* 65536 * 2^(-i/256) */
#define SIG_STEPS 16                  /* table points per unit */
#define SIG_RANGE 8                   /* covers -8 .. +8 */
#define SIG_N (2 * SIG_RANGE * SIG_STEPS + 1)
static float s_sig_tab[SIG_N + 1];    /* sigmoid at -8 + i/16 */

/* ---------- small float helpers (no libm) ---------- */

typedef union { float f; uint32_t u; int32_t i; } fbits;

static inline uint32_t f2u(float f) { fbits b; b.f = f; return b.u; }
static inline float u2f(uint32_t u) { fbits b; b.u = u; return b.f; }

/* Order-preserving integer key for a float (no NaNs here). */
static inline uint32_t fkey(float f)
{
    uint32_t u = f2u(f);
    return (u & 0x80000000u) ? ~u : (u | 0x80000000u);
}

static inline int32_t round_i(float v)
{
    return (int32_t)(v + ((f2u(v) & 0x80000000u) ? -0.5f : 0.5f));
}

static inline int16_t sat16(int32_t v)
{
    if (v > 32767) return 32767;
    if (v < -32767) return -32767;
    return (int16_t)v;
}

static float fast_exp(float x)
{
    float t, f, p;
    int32_t n;
    if (fkey(x) < fkey(-80.0f)) return 0.0f;
    if (fkey(x) > fkey(80.0f)) x = 80.0f;
    t = x * 1.44269504f;
    n = round_i(t);
    f = t - (float)n;
    p = 1.0f + f * (0.693147182f + f * (0.240226507f + f * (0.0555041087f +
        f * (0.00961812911f + f * 0.00133335581f))));
    return u2f(f2u(p) + ((uint32_t)n << 23));
}

/* SiLU, v * sigmoid(v), with the sigmoid from a table (linear between
 * points 1/16 apart; error under 1e-4). */
static float silu(float v)
{
    float t;
    int32_t i;
    uint32_t k = fkey(v);
    if (k <= fkey(-(float)SIG_RANGE)) return v * s_sig_tab[0];
    if (k >= fkey((float)SIG_RANGE)) return v;
    t = (v + (float)SIG_RANGE) * (float)SIG_STEPS;
    i = (int32_t)t;
    t -= (float)i;
    return v * (s_sig_tab[i] + t * (s_sig_tab[i + 1] - s_sig_tab[i]));
}

static float rsqrt(float x)
{
    float y = u2f(0x5f3759dfu - (f2u(x) >> 1));
    int i;
    for (i = 0; i < 3; i++)
        y = y * (1.5f - 0.5f * x * y * y);
    return y;
}

/* ---------- CRC-32 (zlib polynomial, nibble table) ---------- */

static const uint32_t s_crc_nib[16] = {
    0x00000000u, 0x1db71064u, 0x3b6e20c8u, 0x26d930acu,
    0x76dc4190u, 0x6b6b51f4u, 0x4db26158u, 0x5005713cu,
    0xedb88320u, 0xf00f9344u, 0xd6d6a3e8u, 0xcb61b38cu,
    0x9b64c2b0u, 0x86d3d2d4u, 0xa00ae278u, 0xbdbdf21cu,
};

uint32_t tllm_crc32(const uint8_t *p, uint32_t n, uint32_t crc)
{
    crc = ~crc;
    while (n--) {
        crc ^= *p++;
        crc = (crc >> 4) ^ s_crc_nib[crc & 15];
        crc = (crc >> 4) ^ s_crc_nib[crc & 15];
    }
    return ~crc;
}

/* ---------- layout ---------- */

static inline uint32_t rd32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static uint32_t al8(uint32_t v) { return (v + 7u) & ~7u; }

/* Offsets of the work area, relative to its start. */
typedef struct {
    uint32_t tok_off, tok_hash, x, xb, xb2, q, hb, hb2, logits, xq, att, kc, vc, cand, end;
} Layout;

static void layout(const TllmInfo *in, Layout *L)
{
    uint32_t o = 0, kv = in->dim / in->n_heads * in->n_kv_heads;
    uint32_t cache = in->n_layers * in->seq_len * kv * 2u;
    uint32_t big = in->hidden > in->dim ? in->hidden : in->dim;
    L->tok_off = o;  o = al8(o + in->vocab * 2u);
    L->tok_hash = o; o = al8(o + 1024u * 2u);
    L->x = o;        o = al8(o + in->dim * 4u);
    L->xb = o;       o = al8(o + in->dim * 4u);
    L->xb2 = o;      o = al8(o + in->dim * 4u);
    L->q = o;        o = al8(o + in->dim * 4u);
    L->hb = o;       o = al8(o + in->hidden * 4u);
    L->hb2 = o;      o = al8(o + in->hidden * 4u);
    L->logits = o;   o = al8(o + in->vocab * 4u);
    L->xq = o;       o = al8(o + big);
    L->att = o;      o = al8(o + in->seq_len * 4u);
    L->kc = o;       o = al8(o + cache);
    L->vc = o;       o = al8(o + cache);
    L->cand = o;     o = al8(o + in->vocab * 4u);
    L->end = o;
}

static uint32_t mat_bytes(uint32_t rows, uint32_t cols) { return rows * 4u + rows * cols; }

int tllm_check(const void *header, uint32_t file_bytes, TllmInfo *in)
{
    const uint8_t *h = (const uint8_t *)header;
    uint32_t hs, kv, need, l;
    Layout L;
    if (rd32(h) != TLLM_MAGIC) return TLLM_E_MAGIC;
    if (rd32(h + 4) != TLLM_VERSION) return TLLM_E_VERSION;
    in->dim = rd32(h + 8);
    in->hidden = rd32(h + 12);
    in->n_layers = rd32(h + 16);
    in->n_heads = rd32(h + 20);
    in->n_kv_heads = rd32(h + 24);
    in->vocab = rd32(h + 28);
    in->seq_len = rd32(h + 32);
    in->max_tok_len = rd32(h + 36);
    in->payload = rd32(h + 40);
    in->crc = rd32(h + 44);
    in->tok_bytes = rd32(h + 48);
    if (in->dim < 4 || in->dim > 1024 || in->hidden < 4 || in->hidden > 4096) return TLLM_E_SHAPE;
    if (in->n_layers < 1 || in->n_layers > TLLM_MAX_LAYERS) return TLLM_E_SHAPE;
    if (in->n_heads < 1 || in->n_heads > 64 || in->dim % in->n_heads) return TLLM_E_SHAPE;
    if (in->n_kv_heads < 1 || in->n_heads % in->n_kv_heads) return TLLM_E_SHAPE;
    if (in->vocab < 260 || in->vocab > 4096 || in->seq_len < 8 || in->seq_len > 1024) return TLLM_E_SHAPE;
    if (in->tok_bytes < in->vocab * 5u || in->tok_bytes > 65536u) return TLLM_E_SHAPE;
    hs = in->dim / in->n_heads;
    kv = hs * in->n_kv_heads;
    if ((hs & 1) || hs > 64 || (in->dim & 3) || (in->hidden & 3) || (kv & 3)) return TLLM_E_SHAPE;
    need = (2u * in->n_layers + 1u) * in->dim * 4u;
    need += 2u * in->seq_len * (hs / 2u) * 4u;
    need += mat_bytes(in->vocab, in->dim);
    for (l = 0; l < in->n_layers; l++) {
        need += 2u * mat_bytes(in->dim, in->dim) + 2u * mat_bytes(kv, in->dim);
        need += 2u * mat_bytes(in->hidden, in->dim) + mat_bytes(in->dim, in->hidden);
    }
    need += in->tok_bytes;
    if (in->payload != need) return TLLM_E_SHAPE;
    in->file_bytes = TLLM_HEADER + need;
    if (file_bytes != in->file_bytes) return TLLM_E_SIZE;
    layout(in, &L);
    in->work_bytes = L.end;
    return TLLM_OK;
}

uint32_t tllm_arena_bytes(const TllmInfo *in)
{
    return al8(in->file_bytes) + in->work_bytes;
}

/* ---------- tokenizer ---------- */

static uint32_t hash_bytes(const uint8_t *s, uint32_t n)
{
    uint32_t h = 2166136261u;
    while (n--) h = (h ^ *s++) * 16777619u;
    return h;
}

static int lookup(const Tllm *t, const uint8_t *s, uint32_t n)
{
    uint32_t slot = hash_bytes(s, n) & 1023u, k;
    for (k = 0; k < 1024u; k++) {
        uint16_t e = t->tok_hash[slot];
        const uint8_t *p;
        uint32_t i;
        if (!e) return -1;
        p = t->tok_blob + t->tok_off[e - 1];
        if (p[0] == n) {
            for (i = 0; i < n && p[1 + i] == s[i]; i++) {}
            if (i == n) return e - 1;
        }
        slot = (slot + 1u) & 1023u;
    }
    return -1;
}

static int tok_setup(Tllm *t)
{
    uint32_t v, off = 0, n = t->info.tok_bytes - t->info.vocab * 4u, k;
    for (k = 0; k < 1024u; k++) t->tok_hash[k] = 0;
    for (v = 0; v < t->info.vocab; v++) {
        uint32_t len, slot;
        if (off >= n) return TLLM_E_TOKENS;
        len = t->tok_blob[off];
        if (off + 1u + len > n) return TLLM_E_TOKENS;
        t->tok_off[v] = (uint16_t)off;
        off += 1u + len;
        if (!len) continue;
        slot = hash_bytes(t->tok_blob + t->tok_off[v] + 1, len) & 1023u;
        for (k = 0; k < 1024u && t->tok_hash[slot]; k++) slot = (slot + 1u) & 1023u;
        if (k == 1024u) return TLLM_E_TOKENS;
        t->tok_hash[slot] = (uint16_t)(v + 1u);
    }
    return TLLM_OK;
}

static int encode(Tllm *t, const char *text, int16_t *out, int cap)
{
    int n = 0, i;
    const uint8_t *s = (const uint8_t *)text;
    uint8_t buf[2 * 256 + 2];
    out[n++] = 1; /* BOS */
    if (s[0]) {
        int sp = lookup(t, (const uint8_t *)" ", 1);
        if (sp >= 0 && n < cap) out[n++] = (int16_t)sp;
    }
    while (*s && n < cap) {
        uint32_t len = 1;
        int id;
        if ((*s & 0xC0) == 0xC0)
            while (len < 4 && (s[len] & 0xC0) == 0x80) len++;
        id = lookup(t, s, len);
        if (id >= 0) {
            out[n++] = (int16_t)id;
        } else {
            uint32_t b;
            for (b = 0; b < len && n < cap; b++) out[n++] = (int16_t)(s[b] + 3);
        }
        s += len;
    }
    for (;;) {
        uint32_t best = 0;
        int best_id = -1, best_i = -1;
        for (i = 1; i + 1 < n; i++) {
            const uint8_t *a = t->tok_blob + t->tok_off[out[i]];
            const uint8_t *b = t->tok_blob + t->tok_off[out[i + 1]];
            uint32_t la = a[0], lb = b[0], j;
            int id;
            for (j = 0; j < la; j++) buf[j] = a[1 + j];
            for (j = 0; j < lb; j++) buf[la + j] = b[1 + j];
            id = lookup(t, buf, la + lb);
            if (id >= 0) {
                uint32_t key = fkey(t->tok_score[id]);
                if (best_id < 0 || key > best) {
                    best = key;
                    best_id = id;
                    best_i = i;
                }
            }
        }
        if (best_id < 0) break;
        out[best_i] = (int16_t)best_id;
        for (i = best_i + 1; i + 1 < n; i++) out[i] = out[i + 1];
        n--;
    }
    return n;
}

static void decode(Tllm *t, int prev, int tok, char *out)
{
    const uint8_t *p = t->tok_blob + t->tok_off[tok];
    uint32_t n = p[0], i = 0, o = 0;
    const uint8_t *s = p + 1;
    if (prev == 1 && n && s[0] == ' ') { i = 1; }
    if (n == 6 && s[0] == '<' && s[1] == '0' && s[2] == 'x' && s[5] == '>') {
        uint32_t v = 0, k;
        for (k = 3; k < 5; k++) {
            uint8_t c = s[k];
            v = v * 16u + (c >= 'a' ? (uint32_t)(c - 'a' + 10) : c >= 'A' ? (uint32_t)(c - 'A' + 10) : (uint32_t)(c - '0'));
        }
        out[0] = (char)v;
        out[1] = 0;
        return;
    }
    for (; i < n && o + 1 < TLLM_PIECE_MAX; i++) out[o++] = (char)s[i];
    out[o] = 0;
}

/* ---------- open ---------- */

int tllm_open(Tllm *t, uint8_t *arena, uint32_t arena_bytes, uint32_t file_bytes)
{
    TllmInfo in;
    Layout L;
    const uint8_t *p;
    uint8_t *w;
    uint32_t l, kv, hs, i;
    int rc = tllm_check(arena, file_bytes, &in);
    if (rc) return rc;
    if (arena_bytes < tllm_arena_bytes(&in)) return TLLM_E_ARENA;
    if (tllm_crc32(arena + TLLM_HEADER, in.payload, 0) != in.crc) return TLLM_E_CRC;
    t->info = in;
    hs = in.dim / in.n_heads;
    kv = hs * in.n_kv_heads;
    t->head_size = hs;
    t->kv_dim = kv;
    t->kv_mul = in.n_heads / in.n_kv_heads;

    p = arena + TLLM_HEADER;
#define TAKE_F(dst, count) do { dst = (const float *)p; p += (count) * 4u; } while (0)
#define TAKE_M(m, rows, cols) do { (m).s = (const float *)p; p += (rows) * 4u; \
        (m).q = (const int8_t *)p; p += (rows) * (cols); } while (0)
    TAKE_F(t->rms_att, in.n_layers * in.dim);
    TAKE_F(t->rms_ffn, in.n_layers * in.dim);
    TAKE_F(t->rms_final, in.dim);
    TAKE_F(t->rope_cos, in.seq_len * hs / 2u);
    TAKE_F(t->rope_sin, in.seq_len * hs / 2u);
    TAKE_M(t->emb, in.vocab, in.dim);
    for (l = 0; l < in.n_layers; l++) {
        TAKE_M(t->wq[l], in.dim, in.dim);
        TAKE_M(t->wk[l], kv, in.dim);
        TAKE_M(t->wv[l], kv, in.dim);
        TAKE_M(t->wo[l], in.dim, in.dim);
        TAKE_M(t->w1[l], in.hidden, in.dim);
        TAKE_M(t->w2[l], in.dim, in.hidden);
        TAKE_M(t->w3[l], in.hidden, in.dim);
    }
    TAKE_F(t->tok_score, in.vocab);
    t->tok_blob = p;
#undef TAKE_F
#undef TAKE_M

    layout(&in, &L);
    w = arena + al8(in.file_bytes);
    t->tok_off = (uint16_t *)(w + L.tok_off);
    t->tok_hash = (uint16_t *)(w + L.tok_hash);
    t->x = (float *)(w + L.x);
    t->xb = (float *)(w + L.xb);
    t->xb2 = (float *)(w + L.xb2);
    t->q = (float *)(w + L.q);
    t->hb = (float *)(w + L.hb);
    t->hb2 = (float *)(w + L.hb2);
    t->logits = (float *)(w + L.logits);
    t->xq = (int8_t *)(w + L.xq);
    t->att = (int32_t *)(w + L.att);
    t->kc = (int16_t *)(w + L.kc);
    t->vc = (int16_t *)(w + L.vc);
    t->cand = (uint32_t *)(w + L.cand);
    rc = tok_setup(t);
    if (rc) return rc;
    for (i = 0; i < 256u; i++)
        s_exp2_tab[i] = (uint32_t)round_i(65536.0f * fast_exp(-0.693147182f * (float)i / 256.0f));
    for (i = 0; i <= SIG_N; i++) {
        float v = (float)((int32_t)i - SIG_RANGE * SIG_STEPS) / (float)SIG_STEPS;
        s_sig_tab[i] = 1.0f / (1.0f + fast_exp(-v));
    }
    t->stage = -1;
    return TLLM_OK;
}

/* ---------- maths ---------- */

static int32_t dot_i8(const int8_t *a, const int8_t *b, uint32_t n)
{
#ifdef TLLM_SIMD
    const uint32_t *pa = (const uint32_t *)a, *pb = (const uint32_t *)b;
    int32_t acc = 0;
    uint32_t i;
    for (i = 0; i < n / 4u; i++) {
        uint32_t va = pa[i], vb = pb[i];
        acc = __smlad(__sxtb16(va), __sxtb16(vb), acc);
        acc = __smlad(__sxtb16(__ror(va, 8)), __sxtb16(__ror(vb, 8)), acc);
    }
    return acc;
#else
    int32_t acc = 0;
    uint32_t i;
    for (i = 0; i < n; i++) acc += (int32_t)a[i] * (int32_t)b[i];
    return acc;
#endif
}

static int32_t dot_i16(const int16_t *a, const int16_t *b, uint32_t n)
{
#ifdef TLLM_SIMD
    const uint32_t *pa = (const uint32_t *)a, *pb = (const uint32_t *)b;
    int32_t acc = 0;
    uint32_t i;
    for (i = 0; i < n / 2u; i++) acc = __smlad(pa[i], pb[i], acc);
    return acc;
#else
    int32_t acc = 0;
    uint32_t i;
    for (i = 0; i < n; i++) acc += (int32_t)a[i] * (int32_t)b[i];
    return acc;
#endif
}

/* Quantise x[n] to int8; returns the scale (0 if x is all zero). */
static float quant(int8_t *q, const float *x, uint32_t n)
{
    uint32_t peak = 0, i;
    float inv;
    for (i = 0; i < n; i++) {
        uint32_t a = f2u(x[i]) & 0x7fffffffu;
        if (a > peak) peak = a;
    }
    if (!peak) {
        for (i = 0; i < n; i++) q[i] = 0;
        return 0.0f;
    }
    inv = 127.0f / u2f(peak);
    for (i = 0; i < n; i++) {
        int32_t v = round_i(x[i] * inv);
        q[i] = (int8_t)(v > 127 ? 127 : v < -127 ? -127 : v);
    }
    return u2f(peak) / 127.0f;
}

/* out[rows] = W[rows x cols] . x, with x already in t->xq at scale xs. */
static void matvec(float *out, const TllmMat *m, const int8_t *xq, float xs, uint32_t rows, uint32_t cols)
{
    uint32_t i;
    const int8_t *row = m->q;
    for (i = 0; i < rows; i++, row += cols)
        out[i] = (float)dot_i8(row, xq, cols) * (m->s[i] * xs);
}

/* The same without the activation scale, for outputs whose next use is
 * linear: the caller folds xs into a constant it multiplies by anyway. */
static void matvec_raw(float *out, const TllmMat *m, const int8_t *xq, uint32_t rows, uint32_t cols)
{
    uint32_t i;
    const int8_t *row = m->q;
    for (i = 0; i < rows; i++, row += cols)
        out[i] = (float)dot_i8(row, xq, cols) * m->s[i];
}

static void rmsnorm(float *o, const float *x, const float *w, uint32_t n)
{
    float ss = 0.0f, r;
    uint32_t i;
    for (i = 0; i < n; i++) ss += x[i] * x[i];
    r = rsqrt(ss / (float)n + 1e-5f);
    for (i = 0; i < n; i++) o[i] = w[i] * (r * x[i]);
}

static void rope(Tllm *t, float *v, uint32_t n, int pos)
{
    uint32_t i, half = t->head_size / 2u;
    const float *c = t->rope_cos + (uint32_t)pos * half;
    const float *s = t->rope_sin + (uint32_t)pos * half;
    for (i = 0; i < n; i += 2) {
        uint32_t k = (i % t->head_size) / 2u;
        float a = v[i], b = v[i + 1];
        v[i] = a * c[k] - b * s[k];
        v[i + 1] = a * s[k] + b * c[k];
    }
}

static void layer(Tllm *t, uint32_t l, int pos)
{
    const TllmInfo *in = &t->info;
    uint32_t dim = in->dim, kv = t->kv_dim, hs = t->head_size, seq = in->seq_len;
    uint32_t h, j;
    int tt;
    float xs;
    int16_t *kc = t->kc + (l * seq + (uint32_t)pos) * kv;
    int16_t *vc = t->vc + (l * seq + (uint32_t)pos) * kv;
    float qscale = (float)(1 << Q_SHIFT) * rsqrt((float)hs);

    rmsnorm(t->xb, t->x, t->rms_att + l * dim, dim);
    xs = quant(t->xq, t->xb, dim);
    /* q, k and v without xs: RoPE is linear, so xs joins the fixed-point
     * conversion constants below */
    matvec_raw(t->q, &t->wq[l], t->xq, dim, dim);
    matvec_raw(t->xb2, &t->wk[l], t->xq, kv, dim);   /* k */
    matvec_raw(t->hb, &t->wv[l], t->xq, kv, dim);    /* v */
    rope(t, t->q, dim, pos);
    rope(t, t->xb2, kv, pos);
    {
        float kmul = (float)(1 << K_SHIFT) * xs, vmul = (float)(1 << V_SHIFT) * xs;
        for (j = 0; j < kv; j++) {
            kc[j] = sat16(round_i(t->xb2[j] * kmul));
            vc[j] = sat16(round_i(t->hb[j] * vmul));
        }
    }
    qscale *= xs;

    for (h = 0; h < in->n_heads; h++) {
        int16_t qh[64] __attribute__((aligned(4)));
        int64_t acc[64];
        int32_t *att = t->att, mx = (int32_t)0x80000000;
        uint32_t kvo = (h / t->kv_mul) * hs, sum = 0;
        const int16_t *kp = t->kc + l * seq * kv + kvo;
        const int16_t *vp = t->vc + l * seq * kv + kvo;
        float inv;
        for (j = 0; j < hs; j++) qh[j] = sat16(round_i(t->q[h * hs + j] * qscale));
        for (tt = 0; tt <= pos; tt++, kp += kv) {
            int32_t s = dot_i16(qh, kp, hs);
            att[tt] = s;
            if (s > mx) mx = s;
        }
        for (tt = 0; tt <= pos; tt++) {
            /* e = (score - max) * log2(e), in SCORE_FRAC fixed point, <= 0 */
            int32_t e = (int32_t)(((int64_t)(att[tt] - mx) * LOG2E_Q16) >> 16);
            uint32_t ne = (uint32_t)(-e), n = ne >> SCORE_FRAC;
            uint32_t w = n >= 17u ? 0u : (s_exp2_tab[(ne >> (SCORE_FRAC - 8)) & 255u] >> n);
            att[tt] = (int32_t)w;
            sum += w;
        }
        for (j = 0; j < hs; j++) acc[j] = 0;
        for (tt = 0; tt <= pos; tt++, vp += kv) {
            int32_t w = att[tt];
            if (!w) continue;
            for (j = 0; j < hs; j++) acc[j] += (int64_t)w * vp[j];
        }
        inv = 1.0f / ((float)sum * (float)(1 << V_SHIFT));
        for (j = 0; j < hs; j++) t->xb[h * hs + j] = (float)acc[j] * inv;
    }

    xs = quant(t->xq, t->xb, dim);
    matvec(t->xb2, &t->wo[l], t->xq, xs, dim, dim);
    for (j = 0; j < dim; j++) t->x[j] += t->xb2[j];

    rmsnorm(t->xb, t->x, t->rms_ffn + l * dim, dim);
    xs = quant(t->xq, t->xb, dim);
    matvec(t->hb, &t->w1[l], t->xq, xs, in->hidden, dim);
    matvec_raw(t->hb2, &t->w3[l], t->xq, in->hidden, dim);
    for (j = 0; j < in->hidden; j++)
        t->hb[j] = silu(t->hb[j]) * t->hb2[j];
    /* quant is scale-free, so w3's xs is applied to the int8 scale */
    xs = quant(t->xq, t->hb, in->hidden) * xs;
    matvec(t->xb, &t->w2[l], t->xq, xs, dim, in->hidden);
    for (j = 0; j < dim; j++) t->x[j] += t->xb[j];
}

/* ---------- sampling ---------- */

static uint32_t rnd(uint64_t *s)
{
    *s ^= *s >> 12;
    *s ^= *s << 25;
    *s ^= *s >> 27;
    return (uint32_t)((*s * 0x2545F4914F6CDD1Dull) >> 32);
}

/* Greedy: the largest logit (float logits, as llama2.c computes them). */
static int argmax(const float *p, uint32_t v)
{
    uint32_t i, best = 0, bestk = 0;
    for (i = 0; i < v; i++) {
        uint32_t k = fkey(p[i]);
        if (k > bestk) { bestk = k; best = i; }
    }
    return (int)best;
}

/* Temperature and top-p sampling, in integers. The classifier output is
 * turned straight into log2 units (Q16) and 2^x comes from the same table
 * as the attention softmax, so each of the vocab rows costs one integer dot
 * product and a few soft-float operations. */
static int sample_int(Tllm *t, float xs)
{
    const TllmInfo *in = &t->info;
    uint32_t v = in->vocab, i, n = 0, sum = 0, cum = 0, r;
    int32_t *lg = (int32_t *)t->logits, mx = (int32_t)0x80000000;
    const int8_t *row = t->emb.q;
    float k = xs * (1.44269504f * 65536.0f) / t->temp;
    for (i = 0; i < v; i++, row += in->dim) {
        int32_t l = round_i((float)dot_i8(row, t->xq, in->dim) * (t->emb.s[i] * k));
        lg[i] = l;
        if (l > mx) mx = l;
    }
    for (i = 0; i < v; i++) {
        uint32_t d = (uint32_t)(mx - lg[i]), sh = d >> 16;
        uint32_t w = sh >= 17u ? 0u : (s_exp2_tab[(d >> 8) & 255u] >> sh);
        lg[i] = (int32_t)w;
        sum += w;
    }
    r = rnd(&t->rng);
    if (t->topp_m <= 0 || t->topp_m >= 1000) {
        uint32_t pick = (uint32_t)(((uint64_t)r * sum) >> 32);
        for (i = 0; i < v; i++) {
            cum += (uint32_t)lg[i];
            if (cum > pick) return (int)i;
        }
        return (int)(v - 1);
    }
    /* keep weights at or above (1 - topp) / (vocab - 1) of the total,
     * largest first, then stop once they hold topp of it */
    {
        uint64_t cut = (uint64_t)sum * (uint32_t)(1000 - t->topp_m);
        for (i = 0; i < v; i++) {
            uint32_t w = (uint32_t)lg[i], j;
            if ((uint64_t)w * (v - 1u) * 1000u < cut) continue;
            j = n++;
            while (j > 0 && (uint32_t)lg[t->cand[j - 1]] < w) {
                t->cand[j] = t->cand[j - 1];
                j--;
            }
            t->cand[j] = i;
        }
    }
    if (!n) return (int)t->cand[0];
    for (i = 0; i < n; i++) {
        cum += (uint32_t)lg[t->cand[i]];
        if ((uint64_t)cum * 1000u > (uint64_t)sum * (uint32_t)t->topp_m) { n = i + 1; break; }
    }
    r = (uint32_t)(((uint64_t)r * cum) >> 32);
    cum = 0;
    for (i = 0; i < n; i++) {
        cum += (uint32_t)lg[t->cand[i]];
        if (cum > r) return (int)t->cand[i];
    }
    return (int)t->cand[n - 1];
}

/* ---------- generation ---------- */

void tllm_begin(Tllm *t, const char *prompt, uint64_t seed, float temp, float topp, int limit)
{
    t->n_prompt = encode(t, prompt ? prompt : "", t->prompt, TLLM_MAX_PROMPT);
    t->pos = 0;
    t->token = t->prompt[0];
    t->stage = 0;
    t->rng = seed ? seed : 0x9E3779B97F4A7C15ull;
    t->temp = temp;
    t->greedy = !(fkey(temp) > fkey(0.0f));
    t->topp_m = round_i(topp * 1000.0f);
    t->limit = (limit > 0 && limit <= (int)t->info.seq_len) ? limit : (int)t->info.seq_len;
    if (t->n_prompt >= t->limit) t->n_prompt = t->limit - 1;
    t->piece[0] = 0;
    t->is_prompt = 0;
    t->generated = 0;
    t->chk = 0;
}

int tllm_step(Tllm *t)
{
    const TllmInfo *in = &t->info;
    uint32_t j;
    int next;
    if (t->stage < 0) return TLLM_DONE;
    if (t->stage == 0) {
        const int8_t *row = t->emb.q + (uint32_t)t->token * in->dim;
        float s = t->emb.s[t->token];
        for (j = 0; j < in->dim; j++) t->x[j] = (float)row[j] * s;
    }
    if (t->stage < (int)in->n_layers) {
        layer(t, (uint32_t)t->stage, t->pos);
        t->stage++;
        return TLLM_BUSY;
    }
    if (t->pos + 1 < t->n_prompt) {
        next = t->prompt[t->pos + 1];
        t->is_prompt = 1;
    } else {
        float xs;
        rmsnorm(t->xb, t->x, t->rms_final, in->dim);
        xs = quant(t->xq, t->xb, in->dim);
        if (t->greedy) {
            matvec(t->logits, &t->emb, t->xq, xs, in->vocab, in->dim);
            next = argmax(t->logits, in->vocab);
        } else {
            next = sample_int(t, xs);
        }
        t->is_prompt = 0;
        t->generated++;
    }
    t->pos++;
    t->chk = t->chk * 31u + (uint32_t)next;
    if (next == 1) {
        t->stage = -1;
        t->piece[0] = 0;
        return TLLM_DONE;
    }
    decode(t, t->token, next, t->piece);
    t->token = next;
    t->stage = t->pos >= t->limit ? -1 : 0;
    return TLLM_TOKEN;
}
