/* be_soft.c: software rasterizer backend for host runs of the recompiled Angry Birds
 * (nano7kit, MIT). 240x432 panel, depth buffer, nearest texels. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../rt/fngl.h"

#define PW 240
#define PH 432
uint8_t be_fb[PH][PW][4];
uint8_t be_last_tex[PH][PW];
static float zb[PH][PW];
typedef struct { int used, w, h, fmt_seen; uint8_t *rgba; uint32_t format, type; } tex;
#define MAXT 512
static tex T[MAXT];
int be_stat_pvrtc, be_stat_uncompressed, be_tex_peak_bytes, be_tex_bytes;
int be_stat_tex_fail, be_stat_live_tex;
int be_check_tiny;           /* triangles under 2 square pixels (the nano driver rule) */
int be_frame_tris, be_frame_tris_max, be_frame_big, be_frame_big_max;   /* big: what the nano draws */

int be_tex_create(void) { for (int i = 1; i < MAXT; i++) if (!T[i].used) { memset(&T[i], 0, sizeof T[i]); T[i].used = 1; return i; } return 0; }
void be_tex_delete(int h) { if (h > 0 && h < MAXT) { if (T[h].rgba) { be_tex_bytes -= T[h].w * T[h].h * 2; free(T[h].rgba); } T[h].used = 0; T[h].rgba = 0; } }
void be_tex_param(int h, uint32_t p, uint32_t v) { (void)h; (void)p; (void)v; }
static void put_rgba(tex *t, int w, int h) {
    if (t->rgba) { be_tex_bytes -= t->w * t->h * 2; free(t->rgba); }
    t->w = w; t->h = h; t->rgba = calloc((size_t)w * h, 4);
    be_tex_bytes += w * h * 2; if (be_tex_bytes > be_tex_peak_bytes) be_tex_peak_bytes = be_tex_bytes;
}
void be_tex_image(int h, int level, int w, int ht, uint32_t format, uint32_t type, const void *px) {
    if (h <= 0 || h >= MAXT || level != 0) return;
    /* Same 512 cap as nano/be_nano.c: a larger upload is a silent no-op on device
     * (white silhouettes) while the host used to succeed and hide the miss. */
    if (w > 512 || ht > 512) {
        be_stat_tex_fail++;
        fprintf(stderr, "tex fail %dx%d >512 (nano be_tex_image cap)\n", w, ht);
        return;
    }
    tex *t = &T[h];
    t->format = format; t->type = type;
    be_stat_uncompressed++;
    if (getenv("FN_TEXLOG")) fprintf(stderr, "tex %d %dx%d fmt %x type %x\n", h, w, ht, format, type);
    put_rgba(t, w, ht);
    if (!px) return;
    const uint8_t *s = px;
    for (int i = 0; i < w * ht; i++) {
        uint8_t *d = t->rgba + 4 * i;
        if (type == 0x8033) { uint16_t v = (uint16_t)(s[2 * i] | s[2 * i + 1] << 8);   /* 4444 */
            d[0] = (uint8_t)((v >> 12) * 17); d[1] = (uint8_t)(((v >> 8) & 15) * 17); d[2] = (uint8_t)(((v >> 4) & 15) * 17); d[3] = (uint8_t)((v & 15) * 17); }
        else if (type == 0x8363) { uint16_t v = (uint16_t)(s[2 * i] | s[2 * i + 1] << 8);  /* 565 */
            d[0] = (uint8_t)((v >> 11) * 255 / 31); d[1] = (uint8_t)(((v >> 5) & 63) * 255 / 63); d[2] = (uint8_t)((v & 31) * 255 / 31); d[3] = 255; }
        else if (type == 0x8034) { uint16_t v = (uint16_t)(s[2 * i] | s[2 * i + 1] << 8);  /* 5551 */
            d[0] = (uint8_t)((v >> 11) * 255 / 31); d[1] = (uint8_t)(((v >> 6) & 31) * 255 / 31); d[2] = (uint8_t)(((v >> 1) & 31) * 255 / 31); d[3] = (v & 1) ? 255 : 0; }
        else if (format == 0x1908) { memcpy(d, s + 4 * i, 4); }                     /* RGBA8 */
        else if (format == 0x1907) { d[0] = s[3 * i]; d[1] = s[3 * i + 1]; d[2] = s[3 * i + 2]; d[3] = 255; }
        else if (format == 0x190A) { d[0] = d[1] = d[2] = s[2 * i]; d[3] = s[2 * i + 1]; }
        else if (format == 0x1906) { d[0] = d[1] = d[2] = 255; d[3] = s[i]; }
        else if (format == 0x1909) { d[0] = d[1] = d[2] = s[i]; d[3] = 255; }
    }
}
/* PVRTC 4bpp/2bpp decode (host only, to see the textures): standard modulation decode */
static void unpack_col(uint32_t word, int b, int *r, int *g, int *bl, int *a) {
    uint32_t c = b ? word >> 16 : word & 0xFFFF;
    if (c & 0x8000) {   /* opaque */
        if (b) { *r = (int)((c >> 10) & 31); *g = (int)((c >> 5) & 31); *bl = (int)(c & 31); }
        else { *r = (int)((c >> 10) & 31); *g = (int)((c >> 5) & 31); *bl = (int)((c >> 1) & 15); *bl = (*bl << 1) | (*bl >> 3); }
        *a = 15;
        *r = (*r << 3) | (*r >> 2); *g = (*g << 3) | (*g >> 2); *bl = (*bl << 3) | (*bl >> 2); *a = 255;
    } else {
        *a = (int)((c >> 12) & 7); *a = (*a << 5) | (*a << 2) | (*a >> 1);
        if (b) { *r = (int)((c >> 8) & 15); *g = (int)((c >> 4) & 15); *bl = (int)(c & 15); }
        else { *r = (int)((c >> 8) & 15); *g = (int)((c >> 4) & 15); *bl = (int)((c >> 1) & 7); *bl = (*bl << 1) | (*bl >> 2); }
        *r = (*r << 4) | *r; *g = (*g << 4) | *g; *bl = (*bl << 4) | *bl;
    }
}
static uint32_t morton(uint32_t x, uint32_t y, uint32_t bx, uint32_t by) {
    uint32_t m = 0, bit = 0, minb = bx < by ? bx : by;
    uint32_t i = 0;
    for (; (1u << i) < minb; i++) { m |= ((y >> i) & 1u) << bit++; m |= ((x >> i) & 1u) << bit++; }
    for (; (1u << i) < bx || (1u << i) < by; i++) {
        if ((1u << i) < bx) m |= ((x >> i) & 1u) << bit++;
        else m |= ((y >> i) & 1u) << bit++;
    }
    return m;
}
static void pvrtc_decode(uint8_t *out, const uint8_t *in, int w, int h, int bpp2) {
    int bw = bpp2 ? 8 : 4, bh = 4;
    int nbx = w / bw, nby = h / bh;
    if (nbx < 2) nbx = 2;
    if (nby < 2) nby = 2;
    for (int y = 0; y < h; y++) for (int x = 0; x < w; x++) {
        int fx = x - bw / 2, fy = y - bh / 2;
        int bx0 = (fx < 0 ? fx - bw + 1 : fx) / bw, by0 = (fy < 0 ? fy - bh + 1 : fy) / bh;
        float tx = (float)(fx - bx0 * bw) / bw, ty = (float)(fy - by0 * bh) / bh;
        float A[4] = {0}, B[4] = {0};
        for (int j = 0; j < 2; j++) for (int i = 0; i < 2; i++) {
            int bx = ((bx0 + i) % nbx + nbx) % nbx, by = ((by0 + j) % nby + nby) % nby;
            const uint8_t *blk = in + 8 * morton((uint32_t)bx, (uint32_t)by, (uint32_t)nbx, (uint32_t)nby);
            uint32_t cw = (uint32_t)blk[4] | (uint32_t)blk[5] << 8 | (uint32_t)blk[6] << 16 | (uint32_t)blk[7] << 24;
            int ca[4], cb[4];
            unpack_col(cw, 0, &ca[0], &ca[1], &ca[2], &ca[3]);
            unpack_col(cw, 1, &cb[0], &cb[1], &cb[2], &cb[3]);
            float wgt = (i ? tx : 1 - tx) * (j ? ty : 1 - ty);
            for (int k = 0; k < 4; k++) { A[k] += wgt * ca[k]; B[k] += wgt * cb[k]; }
        }
        int bx = (x / bw) % nbx, by = (y / bh) % nby;
        const uint8_t *blk = in + 8 * morton((uint32_t)bx, (uint32_t)by, (uint32_t)nbx, (uint32_t)nby);
        uint32_t mw = (uint32_t)blk[0] | (uint32_t)blk[1] << 8 | (uint32_t)blk[2] << 16 | (uint32_t)blk[3] << 24;
        float wv;
        if (!bpp2) {
            int mv = (int)(mw >> (2 * ((y % 4) * 4 + (x % 4)))) & 3;
            static const float W4[4] = {0, 3 / 8.f, 5 / 8.f, 1};
            wv = W4[mv];
        } else {
            int bit = (int)(mw >> ((y % 4) * 8 + (x % 8))) & 1;
            wv = bit ? 1.f : 0.f;
        }
        uint8_t *d = out + 4 * (y * w + x);
        for (int k = 0; k < 4; k++) { float v = A[k] + (B[k] - A[k]) * wv; d[k] = (uint8_t)(v < 0 ? 0 : v > 255 ? 255 : v); }
    }
}
void be_tex_compressed(int h, int level, uint32_t fmt, int w, int ht, const void *data, uint32_t size) {
    (void)size;
    if (h <= 0 || h >= MAXT || level != 0) return;
    be_stat_pvrtc++;
    if (getenv("FN_TEXLOG")) fprintf(stderr, "tex %d %dx%d pvrtc %x %u\n", h, w, ht, fmt, size);
    tex *t = &T[h];
    put_rgba(t, w, ht);
    int bpp2 = fmt == 0x8C01 || fmt == 0x8C03;
    if ((uint32_t)(w * ht * (bpp2 ? 2 : 4) / 8) <= size) pvrtc_decode(t->rgba, data, w, ht, bpp2);
}
void be_clear(int color, int depth, const float c[4], float d) {
    for (int y = 0; y < PH; y++) for (int x = 0; x < PW; x++) {
        if (color) {
            for (int k = 0; k < 4; k++) be_fb[y][x][k] = (uint8_t)(c[k] * 255.f);
            be_last_tex[y][x] = 255;
        }
        if (depth) zb[y][x] = d;
    }
}
void be_frame_begin(void) { be_frame_tris = be_frame_big = 0; }
void be_frame_end(void) {
    if (be_frame_tris > be_frame_tris_max) be_frame_tris_max = be_frame_tris;
    if (be_frame_big > be_frame_big_max) be_frame_big_max = be_frame_big;
}
static float fac(uint32_t f, const float *s, const float *d, int k) {
    switch (f) {
    case 0: return 0; case 1: return 1;
    case 0x302: return s[3]; case 0x303: return 1 - s[3];
    case 0x304: return d[3]; case 0x305: return 1 - d[3];
    case 0x300: return s[k]; case 0x301: return 1 - s[k];
    case 0x306: return d[k]; case 0x307: return 1 - d[k];
    }
    return 1;
}
void be_tris(const be_state *s, const be_vtx *v, int n) {
    tex *t = s->tex > 0 && s->tex < MAXT && T[s->tex].rgba ? &T[s->tex] : NULL;
    for (int i = 0; i + 2 < n; i += 3) {
        const be_vtx *a = &v[i], *b = &v[i + 1], *c = &v[i + 2];
        float area = (b->x - a->x) * (c->y - a->y) - (c->x - a->x) * (b->y - a->y);
        be_frame_tris++;
        if (fabsf(area) < 4.f) be_check_tiny++; else be_frame_big++;
        if (area == 0) continue;
        int x0 = (int)floorf(fminf(a->x, fminf(b->x, c->x))), x1 = (int)ceilf(fmaxf(a->x, fmaxf(b->x, c->x)));
        int y0 = (int)floorf(fminf(a->y, fminf(b->y, c->y))), y1 = (int)ceilf(fmaxf(a->y, fmaxf(b->y, c->y)));
        if (x0 < 0) x0 = 0;
        if (y0 < 0) y0 = 0;
        if (x1 > PW) x1 = PW;
        if (y1 > PH) y1 = PH;
        float iwa = 1 / a->w, iwb = 1 / b->w, iwc = 1 / c->w;
        for (int y = y0; y < y1; y++) for (int x = x0; x < x1; x++) {
            float px = (float)x + .5f, py = (float)y + .5f;
            float w0 = ((b->x - px) * (c->y - py) - (c->x - px) * (b->y - py)) / area;
            float w1 = ((c->x - px) * (a->y - py) - (a->x - px) * (c->y - py)) / area;
            float w2 = 1 - w0 - w1;
            if (w0 < 0 || w1 < 0 || w2 < 0) continue;
            float z = w0 * a->z + w1 * b->z + w2 * c->z;
            float zd = z * 0.5f + 0.5f;
            if (s->depth_test) {
                float old = zb[y][x];
                int pass = s->depth_func == 0x203 ? zd <= old : s->depth_func == 0x201 ? zd < old : s->depth_func == 0x207 ? 1 : zd <= old;
                if (!pass) continue;
            }
            float pw = w0 * iwa + w1 * iwb + w2 * iwc;
            float u = (w0 * a->u * iwa + w1 * b->u * iwb + w2 * c->u * iwc) / pw;
            float vv = (w0 * a->v * iwa + w1 * b->v * iwb + w2 * c->v * iwc) / pw;
            float col[4];
            for (int k = 0; k < 4; k++) col[k] = (w0 * a->c[k] + w1 * b->c[k] + w2 * c->c[k]) / 255.f;
            if (t) {
                int tx = (int)floorf(u * (float)t->w), ty = (int)floorf(vv * (float)t->h);
                tx %= t->w; if (tx < 0) tx += t->w;
                ty %= t->h; if (ty < 0) ty += t->h;
                const uint8_t *tp = t->rgba + 4 * (ty * t->w + tx);
                for (int k = 0; k < 4; k++) col[k] *= tp[k] / 255.f;
            }
            if (s->alpha_test && col[3] <= 0.f) continue;
            uint8_t *d = be_fb[y][x];
            be_last_tex[y][x] = (uint8_t)s->tex;
            if (s->blend) {
                float dc[4] = {d[0] / 255.f, d[1] / 255.f, d[2] / 255.f, d[3] / 255.f};
                for (int k = 0; k < 4; k++) {
                    float r = col[k] * fac(s->sfactor, col, dc, k) + dc[k] * fac(s->dfactor, col, dc, k);
                    d[k] = (uint8_t)(r < 0 ? 0 : r > 1 ? 255 : r * 255.f);
                }
            } else for (int k = 0; k < 4; k++) d[k] = (uint8_t)(col[k] < 0 ? 0 : col[k] > 1 ? 255 : col[k] * 255.f);
            if (s->depth_test && s->depth_write) zb[y][x] = zd;
        }
    }
}
