/* fngl.c: GLES 1.1 state, CPU transform and clipping for the recompiled engine
 * (nano7kit, MIT). Guest pointers come in as guest addresses. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "rt.h"
#include "rt_plat.h"
#include "fngl.h"

#define IMP(name) uint64_t I_##name(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3)
#define UNUSED (void)r0; (void)r1; (void)r2; (void)r3
static uint32_t stackarg(int i) { return gr32(SP + 4u * (uint32_t)i); }

float fe_scale, fe_scale_y, fe_off_x, fe_off_y, fe_fb_h;
int fe_view_h = 480;
int fe_stat_tris, fe_stat_draws, fe_stat_tex_bytes;

typedef struct { float m[16]; } mat;
static mat stk[3][16];
static int top[3], mode;
static float color[4] = {1, 1, 1, 1};
static int vx, vy, vw = 320, vh = 480;
typedef struct { int on, size; uint32_t type; int stride; uint32_t ptr; uint32_t buf; } arr;
static arr va_, ta_, ca_;
static uint32_t bound_buf;
#define MAXBUF 256
static struct { uint32_t gaddr, size; } bufs[MAXBUF];
#define MAXTEX 512
static int tex_bytes[MAXTEX];
int fe_tex_shift, fe_tex_min_side, fe_tex_big_side, fe_tex_big_shift, fe_stat_tex_peak, fe_stat_tex_count, fe_stat_tex_count_peak;
int fe_pending_tex_shift;
int fe_lowmem;
char fe_lowmem_why[160];
static uint8_t uv_div[MAXTEX];
static int texh[MAXTEX];
static uint32_t bound_tex;
static int en_blend, en_tex, en_depth, en_cull, en_alpha;
static uint32_t sf, df, cullmode, depthfunc, frontface = 0x0901u; /* GL_CCW */
static int depthmask;
static float clear_rgba[4], clear_depth = 1.f;

static void ident(mat *m) { memset(m, 0, sizeof *m); m->m[0] = m->m[5] = m->m[10] = m->m[15] = 1; }
static void mul(mat *r, const mat *a, const mat *b) {       /* r = a * b, column major */
    mat t;
    for (int c = 0; c < 4; c++) for (int rr = 0; rr < 4; rr++) {
        float s = 0; for (int k = 0; k < 4; k++) s += a->m[k * 4 + rr] * b->m[c * 4 + k];
        t.m[c * 4 + rr] = s;
    }
    *r = t;
}
static mat *cur(void) { return &stk[mode][top[mode]]; }
static void init_once(void) {
    static int done; if (done) return; done = 1;
    for (int i = 0; i < 3; i++) ident(&stk[i][0]);
}
static mat gmat(uint32_t p) { mat m; memcpy(m.m, G8(p), 64); return m; }

IMP(_glMatrixMode) { UNUSED; init_once(); mode = r0 == 0x1701 ? 1 : r0 == 0x1702 ? 2 : 0; return 0; }
IMP(_glLoadIdentity) { UNUSED; init_once(); ident(cur()); return 0; }
IMP(_glLoadMatrixf) { UNUSED; init_once(); *cur() = gmat(r0); return 0; }
IMP(_glMultMatrixf) { UNUSED; init_once(); mat b = gmat(r0); mul(cur(), cur(), &b); return 0; }
IMP(_glPushMatrix) { UNUSED; init_once(); if (top[mode] < 15) { stk[mode][top[mode] + 1] = *cur(); top[mode]++; } return 0; }
IMP(_glPopMatrix) { UNUSED; if (top[mode] > 0) top[mode]--; return 0; }
IMP(_glViewport) { UNUSED; vx = (int)r0; vy = (int)r1; vw = (int)r2; vh = (int)r3;
    if (getenv("AB_DUMP_V")) fprintf(stderr, "glViewport %d %d %d %d\n", vx, vy, vw, vh);
    return 0; }
IMP(_glClearColor) { UNUSED; clear_rgba[0] = U2F(r0); clear_rgba[1] = U2F(r1); clear_rgba[2] = U2F(r2); clear_rgba[3] = U2F(r3); return 0; }
IMP(_glClearDepthf) { UNUSED; clear_depth = U2F(r0); return 0; }
IMP(_glClear) { UNUSED; be_clear((r0 & 0x4000) != 0, (r0 & 0x100) != 0, clear_rgba, clear_depth); return 0; }
IMP(_glColor4ub) { UNUSED; color[0] = (float)(r0 & 255) / 255.f; color[1] = (float)(r1 & 255) / 255.f;
    color[2] = (float)(r2 & 255) / 255.f; color[3] = (float)(r3 & 255) / 255.f; return 0; }
IMP(_glColor4f) { UNUSED; color[0] = U2F(r0); color[1] = U2F(r1); color[2] = U2F(r2); color[3] = U2F(r3); return 0; }
static void cap(uint32_t c, int on) {
    switch (c) {
    case 0x0BE2: en_blend = on; break;
    case 0x0DE1: en_tex = on; break;
    case 0x0B71: en_depth = on; break;
    case 0x0B44: en_cull = on; break;
    case 0x0BC0: en_alpha = on; break;
    default: break;
    }
}
IMP(_glEnable) { UNUSED; cap(r0, 1); return 0; }
IMP(_glDisable) { UNUSED; cap(r0, 0); return 0; }
IMP(_glBlendFunc) { UNUSED; sf = r0; df = r1; return 0; }
IMP(_glDepthMask) { UNUSED; depthmask = (r0 & 255) != 0; return 0; }
IMP(_glDepthFunc) { UNUSED; depthfunc = r0; return 0; }
IMP(_glCullFace) { UNUSED; cullmode = r0; return 0; }
IMP(_glAlphaFunc) { UNUSED; return 0; }
IMP(_glShadeModel) { UNUSED; return 0; }
IMP(_glHint) { UNUSED; return 0; }
IMP(_glTexEnvi) { UNUSED; return 0; }
IMP(_glTexEnvf) { UNUSED; return 0; }
static arr *which(uint32_t a) { return a == 0x8074 ? &va_ : a == 0x8078 ? &ta_ : a == 0x8076 ? &ca_ : NULL; }
IMP(_glEnableClientState) { UNUSED; arr *a = which(r0); if (a) a->on = 1; return 0; }
IMP(_glDisableClientState) { UNUSED; arr *a = which(r0); if (a) a->on = 0; return 0; }
static void setp(arr *a, int size, uint32_t type, int stride, uint32_t ptr) { a->size = size; a->type = type; a->stride = stride; a->ptr = ptr; a->buf = bound_buf; }
IMP(_glVertexPointer) { setp(&va_, (int)r0, r1, (int)r2, r3); return 0; }
IMP(_glTexCoordPointer) { setp(&ta_, (int)r0, r1, (int)r2, r3); return 0; }
IMP(_glColorPointer) { setp(&ca_, (int)r0, r1, (int)r2, r3); return 0; }

/* buffers live in the guest heap */
IMP(_glGenBuffers) { UNUSED;
    for (uint32_t i = 0; i < r0; i++) { uint32_t n = 0; for (uint32_t k = 1; k < MAXBUF; k++) if (!bufs[k].size && !bufs[k].gaddr) { n = k; bufs[k].size = 1; break; }
        gw32(r1 + 4 * i, n); }
    return 0; }
IMP(_glBindBuffer) { UNUSED; if (r0 == 0x8892) bound_buf = r1 < MAXBUF ? r1 : 0; return 0; }
IMP(_glBufferData) { UNUSED;
    if (r0 != 0x8892 || !bound_buf) return 0;
    if (bufs[bound_buf].gaddr) g_free(bufs[bound_buf].gaddr);
    bufs[bound_buf].gaddr = g_malloc(r1 ? r1 : 4); bufs[bound_buf].size = r1;
    if (r2 && bufs[bound_buf].gaddr) memcpy(G8(bufs[bound_buf].gaddr), G8(r2), r1);
    return 0; }
IMP(_glBufferSubData) { UNUSED;
    if (r0 == 0x8892 && bound_buf && bufs[bound_buf].gaddr && r1 + r2 <= bufs[bound_buf].size) memcpy(G8(bufs[bound_buf].gaddr + r1), G8(stackarg(0)), r2);
    return 0; }
IMP(_glDeleteBuffers) { UNUSED;
    for (uint32_t i = 0; i < r0; i++) { uint32_t n = gr32(r1 + 4 * i); if (n && n < MAXBUF) { if (bufs[n].gaddr) g_free(bufs[n].gaddr); bufs[n].gaddr = 0; bufs[n].size = 0; } }
    return 0; }

/* textures */
/* GL names are ours; a backend texture is made at the first upload that is not atlased */
static uint8_t tex_named[MAXTEX];
typedef struct { int page, cx, cy, w, h; } atl_slot;   /* page 0 = not atlased */
static atl_slot atl[MAXTEX];
IMP(_glGenTextures) { UNUSED;
    for (uint32_t i = 0; i < r0; i++) { uint32_t n = 0; for (uint32_t k = 1; k < MAXTEX; k++) if (!tex_named[k]) { n = k; tex_named[k] = 1; texh[k] = 0; atl[k].page = 0; uv_div[k] = 0; break; }
        gw32(r1 + 4 * i, n); }
    return 0; }
IMP(_glBindTexture) { UNUSED; bound_tex = r1 < MAXTEX ? r1 : 0; return 0; }
static void atlas_free(uint32_t n);
#ifndef FN_NANO
static void tex_drv_release(uint32_t n);
#endif
IMP(_glDeleteTextures) { UNUSED;
    for (uint32_t i = 0; i < r0; i++) { uint32_t n = gr32(r1 + 4 * i);
        if (!n || n >= MAXTEX || !tex_named[n]) continue;
        if (atl[n].page) atlas_free(n);
        if (texh[n]) { be_tex_delete(texh[n]); texh[n] = 0; fe_stat_tex_count--; }
#ifndef FN_NANO
        tex_drv_release(n);
#endif
        fe_stat_tex_bytes -= tex_bytes[n]; tex_bytes[n] = 0; tex_named[n] = 0; uv_div[n] = 0; }
    return 0; }
IMP(_glTexParameteri) { UNUSED; if (bound_tex && texh[bound_tex] && !atl[bound_tex].page) be_tex_param(texh[bound_tex], r1, r2); return 0; }

/* GLES 1.1 / Ka3D pad each side to a power of two. Image UVs are pixel / POT,
 * not pixel / PNG. A 120x207 SubImage into a 128x256 store must stay 128x256. */
static int pot2(int n) {
    int p = 1;
    while (p < n) p <<= 1;
    return p < 1 ? 1 : p;
}
static void *fe_os_alloc(uint32_t n) {
    void *p = plat_os_alloc(n ? n : 1u);
    return p;
}
static void fe_os_free(void *p) { if (p) plat_os_free(p); }
#ifndef FN_NANO
/* Host models the nano GL driver: glTexImage2D(POT, NULL) takes an OS-heap
 * store (PNG RGBA8 512x256x4 = 524288). c9880d1 never charged that, so the
 * leftover 1 MB after the guest still looked free and convert never failed
 * on host. Device: two 398400 decode buffers plus that store, then
 * "texture POT pad: no memory". Keep the store until glDeleteTextures. */
static void *tex_drv_p[MAXTEX];
static uint32_t tex_drv_n[MAXTEX];
static void tex_drv_release(uint32_t n) {
    if (n && n < MAXTEX && tex_drv_p[n]) {
        plat_os_free(tex_drv_p[n]);
        tex_drv_p[n] = 0;
        tex_drv_n[n] = 0;
    }
}
static int tex_drv_charge(uint32_t n, uint32_t bytes) {
    tex_drv_release(n);
    if (!n || n >= MAXTEX || !bytes) return 1;
    tex_drv_p[n] = plat_os_alloc(bytes);
    if (!tex_drv_p[n]) return 0;
    tex_drv_n[n] = bytes;
    return 1;
}
#endif
void fe_enter_lowmem(const char *why) {
    if (fe_lowmem) return;
    fe_lowmem = 1;
    snprintf(fe_lowmem_why, sizeof fe_lowmem_why, "%s", why && why[0] ? why : "low memory");
    rt_log("nano7kit not enough memory: %s; check log.txt; not continuing", fe_lowmem_why);
}
static void tex_convert_fail(int w, int h, const char *why) {
    extern int be_stat_tex_fail;
    if (why && !strcmp(why, "POT pad: no memory")) rt_log("texture POT pad: no memory");
    else rt_log("texture %dx%d: no memory to convert", w, h);
#ifndef FN_NANO
    /* c9880d1 uploaded nothing, left the GL name as uninit GPU RAM (rainbow)
     * and counted fail=0. AB_REPRO_UNINIT repeats that host-side. */
    if (getenv("AB_REPRO_UNINIT")) return;
#endif
    be_stat_tex_fail++;
    fe_enter_lowmem("texture convert failed");
}

/* atlas: the nano's driver takes only about 80 textures, and the game makes about 120, most
 * of them 32x32 or smaller. Those go into 256x256 pages of 34x34 cells (one texel of border
 * repeated around each) and their coordinates are moved into the cell, clamped to it. */
#define ATL_PAGES 4
#define ATL_SIDE 256
#define ATL_CELL 34
#define ATL_PER_ROW (ATL_SIDE / ATL_CELL)
#define ATL_CELLS (ATL_PER_ROW * ATL_PER_ROW)
int fe_atlas_max_side, fe_stat_atlased, fe_stat_atlas_pages;
static struct { int handle; uint16_t *px; uint8_t used[ATL_CELLS]; int dirty; } pages[ATL_PAGES];
static void atlas_free(uint32_t n) {
    int p = atl[n].page - 1, c = (atl[n].cy / ATL_CELL) * ATL_PER_ROW + atl[n].cx / ATL_CELL;
    if (p >= 0 && p < ATL_PAGES) pages[p].used[c] = 0;
    atl[n].page = 0; fe_stat_atlased--;
}
static int atlas_put(uint32_t n, int w, int h, const uint16_t *src4444) {
    for (int p = 0; p < ATL_PAGES; p++) {
        if (!pages[p].px) {
            pages[p].px = (uint16_t *)fe_os_alloc((uint32_t)ATL_SIDE * (uint32_t)ATL_SIDE * 2u);
            if (!pages[p].px) return 0;
            memset(pages[p].px, 0, (size_t)ATL_SIDE * (size_t)ATL_SIDE * 2u);
            pages[p].handle = be_tex_create();
            if (!pages[p].handle) { fe_os_free(pages[p].px); pages[p].px = NULL; return 0; }
            fe_stat_tex_count++; fe_stat_atlas_pages++; fe_stat_tex_bytes += ATL_SIDE * ATL_SIDE * 2;
            if (fe_stat_tex_count > fe_stat_tex_count_peak) fe_stat_tex_count_peak = fe_stat_tex_count;
        }
        for (int c = 0; c < ATL_CELLS; c++) if (!pages[p].used[c]) {
            int cx = (c % ATL_PER_ROW) * ATL_CELL, cy = (c / ATL_PER_ROW) * ATL_CELL;
            for (int y = -1; y <= h; y++) for (int x = -1; x <= w; x++) {
                int sx = x < 0 ? 0 : x >= w ? w - 1 : x, sy = y < 0 ? 0 : y >= h ? h - 1 : y;
                pages[p].px[(cy + 1 + y) * ATL_SIDE + cx + 1 + x] = src4444[sy * w + sx];
            }
            pages[p].used[c] = 1; pages[p].dirty = 1;
            atl[n].page = p + 1; atl[n].cx = cx; atl[n].cy = cy; atl[n].w = w; atl[n].h = h;
            fe_stat_atlased++;
            return 1;
        }
    }
    return 0;
}
static void atlas_upload(void) {
    for (int p = 0; p < ATL_PAGES; p++) if (pages[p].dirty) {
        be_tex_image(pages[p].handle, 0, ATL_SIDE, ATL_SIDE, 0x1908, 0x8033, pages[p].px);
        pages[p].dirty = 0;
    }
}
/* texture policy for the nano: every upload becomes 16 bits per texel (RGBA8 -> 4444,
 * RGB8 -> 565, LA/A/L -> 4444) and anything with a side of fe_tex_min_side or more is box
 * filtered down by 2^fe_tex_shift. Texture coordinates are normalised, so the game cannot tell. */
static void texel(const uint8_t *s, int i, uint32_t fmt, uint32_t type, int c[4]) {
    if (type == 0x8033) { uint16_t v = (uint16_t)(s[2 * i] | s[2 * i + 1] << 8); c[0] = (v >> 12) * 17; c[1] = ((v >> 8) & 15) * 17; c[2] = ((v >> 4) & 15) * 17; c[3] = (v & 15) * 17; return; }
    if (type == 0x8363) { uint16_t v = (uint16_t)(s[2 * i] | s[2 * i + 1] << 8); c[0] = (v >> 11) * 255 / 31; c[1] = ((v >> 5) & 63) * 255 / 63; c[2] = (v & 31) * 255 / 31; c[3] = 255; return; }
    if (type == 0x8034) { uint16_t v = (uint16_t)(s[2 * i] | s[2 * i + 1] << 8); c[0] = (v >> 11) * 255 / 31; c[1] = ((v >> 6) & 31) * 255 / 31; c[2] = ((v >> 1) & 31) * 255 / 31; c[3] = (v & 1) ? 255 : 0; return; }
    if (fmt == 0x1908) { c[0] = s[4 * i]; c[1] = s[4 * i + 1]; c[2] = s[4 * i + 2]; c[3] = s[4 * i + 3]; return; }
    if (fmt == 0x1907) { c[0] = s[3 * i]; c[1] = s[3 * i + 1]; c[2] = s[3 * i + 2]; c[3] = 255; return; }
    if (fmt == 0x190A) { c[0] = c[1] = c[2] = s[2 * i]; c[3] = s[2 * i + 1]; return; }
    if (fmt == 0x1906) { c[0] = c[1] = c[2] = 255; c[3] = s[i]; return; }
    c[0] = c[1] = c[2] = s[i]; c[3] = 255;
}
IMP(_glTexImage2D) {
    /* target, level, internalformat, width | height, border, format, type, pixels */
    int w = (int)r3, h = (int)stackarg(0), level = (int)r1;
    uint32_t fmt = stackarg(2), type = stackarg(3), px = stackarg(4);
    (void)r0; (void)r2;
    if (!bound_tex || !tex_named[bound_tex]) return 0;
    if (level > 0) return 0;              /* base level only: the driver gets no mip chains */
    int sh = (w >= fe_tex_big_side || h >= fe_tex_big_side) ? fe_tex_big_shift : (w >= fe_tex_min_side || h >= fe_tex_min_side) ? fe_tex_shift : 0;
    while (sh && ((w >> sh) < 1 || (h >> sh) < 1)) sh--;
    int ow = w >> sh, oh = h >> sh, k = 1 << sh;
    int opaque = fmt == 0x1907 || type == 0x8363 || fmt == 0x1909;
    uint32_t otype = opaque ? 0x8363u : 0x8033u, ofmt = opaque ? 0x1907u : 0x1908u;
    int pw = pot2(ow), ph = pot2(oh);
    uint16_t *out = NULL;
    if (!px) {
        /* Ka3D glTexImage2D(POT, NULL) then SubImage. Charge the driver's
         * store on the OS heap (RGBA8 for UNSIGNED_BYTE, 16-bit otherwise).
         * Do not upload uninitialised memory: SubImage fills it, or the
         * fail path draws a solid fallback. */
#ifndef FN_NANO
        {
            uint32_t bpp = (type == 0x8033u || type == 0x8363u || type == 0x8034u) ? 2u : 4u;
            uint32_t bytes = (uint32_t)pw * (uint32_t)ph * bpp;
            if (!tex_drv_charge(bound_tex, bytes ? bytes : 16u))
                tex_convert_fail(w, h, pw != w || ph != h ? "POT pad: no memory" : "no memory to convert");
        }
#endif
        return 0;
    }
    /* One POT 16-bit store. Convert row-by-row; no full-size extra buffer. */
    out = (uint16_t *)fe_os_alloc((uint32_t)pw * (uint32_t)ph * 2u);
    if (!out) {
        tex_convert_fail(w, h, pw != ow || ph != oh ? "POT pad: no memory" : "no memory to convert");
        if (!texh[bound_tex]) {
            texh[bound_tex] = be_tex_create();
            if (texh[bound_tex] && ++fe_stat_tex_count > fe_stat_tex_count_peak)
                fe_stat_tex_count_peak = fe_stat_tex_count;
        }
        if (texh[bound_tex]) {
#ifndef FN_NANO
            if (getenv("AB_REPRO_UNINIT")) {
                /* Unwritten GPU store: be_soft fills a loud rainbow. */
                be_tex_image(texh[bound_tex], 0, pw, ph, ofmt, otype, NULL);
                tex_drv_release(bound_tex);
                return 0;
            }
#endif
            {
                uint16_t fb[16];
                int i;
                for (i = 0; i < 16; i++) fb[i] = opaque ? 0xF800u : 0xF00Fu;
                be_tex_image(texh[bound_tex], 0, 4, 4, ofmt, otype, fb);
                rt_log("texture %dx%d: solid fallback (convert failed)", w, h);
            }
        }
#ifndef FN_NANO
        tex_drv_release(bound_tex);
#endif
        return 0;
    }
    memset(out, 0, (size_t)pw * (size_t)ph * 2u);
    {
        const uint8_t *src = G8(px);
        for (int y = 0; y < oh; y++) for (int x = 0; x < ow; x++) {
            int acc[4] = {0, 0, 0, 0}, c[4];
            for (int j = 0; j < k; j++) for (int i = 0; i < k; i++) {
                texel(src, (y * k + j) * w + x * k + i, fmt, type, c);
                acc[0] += c[0] * c[3]; acc[1] += c[1] * c[3]; acc[2] += c[2] * c[3]; acc[3] += c[3];
            }
            int a = acc[3] / (k * k), r = acc[3] ? acc[0] / acc[3] : 0, g = acc[3] ? acc[1] / acc[3] : 0, b = acc[3] ? acc[2] / acc[3] : 0;
            out[y * pw + x] = opaque ? (uint16_t)((r >> 3) << 11 | (g >> 2) << 5 | (b >> 3))
                                     : (uint16_t)((r >> 4) << 12 | (g >> 4) << 8 | (b >> 4) << 4 | (a >> 4));
        }
    }
    ow = pw;
    oh = ph;
    if (atl[bound_tex].page) atlas_free(bound_tex);
    if (out && ow <= fe_atlas_max_side && oh <= fe_atlas_max_side) {
        uint16_t *q = out;
        if (opaque) for (int i = 0; i < ow * oh; i++) {   /* 565 -> 4444 for the shared page */
            uint16_t v = q[i]; q[i] = (uint16_t)((v >> 12) << 12 | ((v >> 7) & 15) << 8 | ((v >> 1) & 15) << 4 | 15);
        }
        if (atlas_put(bound_tex, ow, oh, q)) {
            if (texh[bound_tex]) { be_tex_delete(texh[bound_tex]); texh[bound_tex] = 0; fe_stat_tex_count--; }
            fe_os_free(out);
#ifndef FN_NANO
            tex_drv_release(bound_tex);
#endif
            fe_stat_tex_bytes -= tex_bytes[bound_tex]; tex_bytes[bound_tex] = 0;
            return 0;
        }
        if (opaque) for (int i = 0; i < ow * oh; i++) {   /* no room: back to 565 */
            uint16_t v = q[i]; q[i] = (uint16_t)(((v >> 12) * 31 / 15) << 11 | (((v >> 8) & 15) * 63 / 15) << 5 | ((v >> 4) & 15) * 31 / 15);
        }
    }
    if (!texh[bound_tex]) {
        texh[bound_tex] = be_tex_create();
        if (!texh[bound_tex]) { fe_os_free(out); tex_convert_fail(w, h, "no memory to convert"); return 0; }
        if (++fe_stat_tex_count > fe_stat_tex_count_peak) fe_stat_tex_count_peak = fe_stat_tex_count;
    }
    be_tex_image(texh[bound_tex], 0, ow, oh, ofmt, otype, out);
    fe_os_free(out);
#ifndef FN_NANO
    /* Scratch convert and the NULL RGBA8 driver store are done. Live
     * pixels sit in the GL backend, which is not this OS heap. */
    tex_drv_release(bound_tex);
#endif
    if (fe_pending_tex_shift > 0 && fe_pending_tex_shift < 8) {
        uv_div[bound_tex] = (uint8_t)fe_pending_tex_shift;
        fe_pending_tex_shift = 0;
    }
    fe_stat_tex_bytes += ow * oh * 2 - tex_bytes[bound_tex]; tex_bytes[bound_tex] = ow * oh * 2;
    if (fe_stat_tex_bytes > fe_stat_tex_peak) fe_stat_tex_peak = fe_stat_tex_bytes;
    return 0;
}
IMP(_glCompressedTexImage2D) {
    /* target, level, internalformat, width | height, border, imageSize, data */
    int h = (int)stackarg(0);
    uint32_t size = stackarg(2), data = stackarg(3);
    if (bound_tex && tex_named[bound_tex] && data && r1 == 0) {
        if (atl[bound_tex].page) atlas_free(bound_tex);
        if (!texh[bound_tex]) {
            texh[bound_tex] = be_tex_create();
            if (!texh[bound_tex]) return 0;
            if (++fe_stat_tex_count > fe_stat_tex_count_peak) fe_stat_tex_count_peak = fe_stat_tex_count;
        }
        be_tex_compressed(texh[bound_tex], 0, r2, (int)r3, h, G8(data), size);
        if (fe_pending_tex_shift > 0 && fe_pending_tex_shift < 8) {
            uv_div[bound_tex] = (uint8_t)fe_pending_tex_shift;
            fe_pending_tex_shift = 0;
        }
        fe_stat_tex_bytes += (int)size - tex_bytes[bound_tex]; tex_bytes[bound_tex] = (int)size;
        if (fe_stat_tex_bytes > fe_stat_tex_peak) fe_stat_tex_peak = fe_stat_tex_bytes;
    }
    (void)r0;
    return 0;
}

/* ---- drawing ------------------------------------------------------------------------------- */
typedef struct { float p[4]; float u, v; float c[4]; } cv;   /* clip space */
static uint32_t base_of(const arr *a) {
    if (a->buf) return bufs[a->buf].gaddr + a->ptr;
    return a->ptr;
}
static int tsize(uint32_t t) { return t == 0x1400 || t == 0x1401 ? 1 : t == 0x1402 || t == 0x1403 ? 2 : 4; }
static float fetch1(uint32_t a, uint32_t type, int norm) {
    switch (type) {
    case 0x1406: return U2F(gr32(a));                                  /* FLOAT */
    case 0x140C: return (float)(int32_t)gr32(a) / 65536.f;             /* FIXED */
    case 0x1402: { int16_t s; memcpy(&s, G8(a), 2); return norm ? s / 32767.f : (float)s; }
    case 0x1403: { uint16_t s; memcpy(&s, G8(a), 2); return norm ? s / 65535.f : (float)s; }
    case 0x1400: { int8_t s = (int8_t)*G8(a); return norm ? s / 127.f : (float)s; }
    case 0x1401: { uint8_t s = *G8(a); return norm ? s / 255.f : (float)s; }
    }
    return 0;
}
static void fetch(const arr *a, int i, float *out, int def_w, int norm) {
    int es = tsize(a->type), stride = a->stride ? a->stride : es * a->size;
    uint32_t p = base_of(a) + (uint32_t)(stride * i);
    out[0] = out[1] = out[2] = 0; out[3] = def_w ? 1.f : 1.f;
    for (int k = 0; k < a->size && k < 4; k++) out[k] = fetch1(p + (uint32_t)(es * k), a->type, norm);
}
static void xform(const mat *m, const float *in, float *out) {
    for (int r = 0; r < 4; r++) out[r] = m->m[r] * in[0] + m->m[4 + r] * in[1] + m->m[8 + r] * in[2] + m->m[12 + r] * in[3];
}
static cv lerpv(const cv *a, const cv *b, float t) {
    cv r;
    for (int k = 0; k < 4; k++) { r.p[k] = a->p[k] + (b->p[k] - a->p[k]) * t; r.c[k] = a->c[k] + (b->c[k] - a->c[k]) * t; }
    r.u = a->u + (b->u - a->u) * t; r.v = a->v + (b->v - a->v) * t;
    return r;
}
static float plane(const cv *v, int i) {
    const float *p = v->p;
    switch (i) {
    case 0: return p[3] - 1e-5f;           /* w > 0 */
    case 1: return p[0] + p[3];
    case 2: return p[3] - p[0];
    case 3: return p[1] + p[3];
    case 4: return p[3] - p[1];
    case 5: return p[2] + p[3];
    default: return p[3] - p[2];
    }
}
#define MAXV 768             /* triangles per flush to the backend */
#ifdef FN_NANO
static be_vtx *outv;
#else
static be_vtx outv[MAXV * 3];
#endif
static int outn;
static be_state st;
static int ensure_outv(void) {
#ifdef FN_NANO
    if (!outv) {
        uint32_t n = (uint32_t)(MAXV * 3 * (int)sizeof(be_vtx));
        outv = (be_vtx *)plat_os_alloc(n);
        if (outv) memset(outv, 0, n);
    }
    return outv != 0;
#else
    return 1;
#endif
}
static void flush_out(void) { if (outn) { be_tris(&st, outv, outn); fe_stat_tris += outn / 3; outn = 0; } }
static void emit_tri(const cv *a, const cv *b, const cv *c) {
    cv poly[2][12]; int n = 3, cur_ = 0;
    poly[0][0] = *a; poly[0][1] = *b; poly[0][2] = *c;
    for (int pl = 0; pl < 7 && n >= 3; pl++) {
        int m = 0;
        cv *in = poly[cur_], *out = poly[cur_ ^ 1];
        for (int i = 0; i < n; i++) {
            cv *p = &in[i], *q = &in[(i + 1) % n];
            float dp = plane(p, pl), dq = plane(q, pl);
            if (dp >= 0) out[m++] = *p;
            if ((dp >= 0) != (dq >= 0) && m < 12) out[m++] = lerpv(p, q, dp / (dp - dq));
            if (m >= 12) break;
        }
        n = m; cur_ ^= 1;
    }
    if (n < 3) return;
    be_vtx sv[12];
    for (int i = 0; i < n; i++) {
        cv *p = &poly[cur_][i];
        float iw = 1.f / p->p[3];
        float nx = p->p[0] * iw, ny = p->p[1] * iw, nz = p->p[2] * iw;
        float fx = (float)vx + (nx * 0.5f + 0.5f) * (float)vw, fy = (float)vy + (ny * 0.5f + 0.5f) * (float)vh;
        /* Same home-on-right map as PvZ n7gl_dev_a.inc:
         *   px = x0 + fy * ky, py = y0 + fx * kx
         * (logical top / GL fy = framebuffer height sits at large panel x = portrait RIGHT).
         * Fruit Ninja is already portrait in GL so it only scales fx,fy. An extra
         * (fb_h - fy) here put Angry Birds' top on portrait LEFT, which is a 180
         * turn vs PvZ/FN when held with Home on the right, and a horizontal mirror
         * if the player instead holds Home on the left to make the sky upright.
         * fe_off is the origin of that map (always 0); it is not a user pan. */
        sv[i].x = fe_off_x + fy * fe_scale;
        sv[i].y = fe_off_y + fx * fe_scale_y;
        if (getenv("AB_DUMP_V")) {
            static int dumpn;
            if (dumpn < 400) {
                fprintf(stderr, "v[%d] fx=%.1f fy=%.1f ndc=%.3f,%.3f w=%.3f uv=%.3f,%.3f c=%u,%u,%u,%u tex=%u vp=%dx%d panel=%.1f,%.1f\n",
                        i, fx, fy, nx, ny, p->p[3], p->u, p->v, sv[i].c[0], sv[i].c[1], sv[i].c[2], sv[i].c[3],
                        bound_tex, vw, vh, sv[i].x, sv[i].y);
                dumpn++;
            }
        }
        sv[i].z = nz; sv[i].w = p->p[3];
        sv[i].u = p->u; sv[i].v = p->v;
        for (int k = 0; k < 4; k++) { float cc = p->c[k]; sv[i].c[k] = (uint8_t)(cc <= 0 ? 0 : cc >= 1 ? 255 : cc * 255.f + 0.5f); }
    }
    if (en_cull && cullmode != 0x408) {
        float area = (sv[1].x - sv[0].x) * (sv[2].y - sv[0].y) - (sv[2].x - sv[0].x) * (sv[1].y - sv[0].y);
        /* (fx,fy) -> (fy,fx) is a reflection (det -1), so GL winding reverses in
         * panel pixels, same as Fruit Ninja's y-flip. Panel y still points down. */
        int ccw = area < 0;
        int front = frontface == 0x0900u ? !ccw : ccw; /* GL_CW vs GL_CCW */
        if ((cullmode == 0x405 && !front) || (cullmode == 0x404 && front)) return;
    }
    if (!ensure_outv()) return;
    for (int i = 1; i + 1 < n; i++) {
        if (outn + 3 > MAXV * 3) flush_out();
        outv[outn++] = sv[0]; outv[outn++] = sv[i]; outv[outn++] = sv[i + 1];
    }
}
static void draw(uint32_t modep, int first, int count, uint32_t idx_type, uint32_t idx) {
    if (!va_.on || count <= 0) return;
    if (modep < 4 || modep > 6) return;           /* points and lines are not drawn */
    init_once();                                  /* Ka3D shaders submit clip-space verts; identity MVP */
    fe_stat_draws++;
    mat mvp; mul(&mvp, &stk[1][top[1]], &stk[0][top[0]]);
    const atl_slot *as = en_tex && bound_tex && atl[bound_tex].page ? &atl[bound_tex] : NULL;
    if (as) atlas_upload();
    st.tex = as ? pages[as->page - 1].handle : en_tex && bound_tex ? texh[bound_tex] : 0;
    st.blend = en_blend; st.sfactor = sf; st.dfactor = df;
    st.depth_test = en_depth; st.depth_write = depthmask; st.depth_func = depthfunc;
    st.alpha_test = en_alpha; st.alpha_ref = 0;
    cv w[3]; int k = 0;
    for (int n = 0; n < count; n++) {
        int i = first + n;
        if (idx_type) {
            uint32_t ia = (bound_buf ? 0 : 0) + idx;
            i = idx_type == 0x1403 ? (int)*(uint16_t *)G8(ia + 2u * (uint32_t)n) : (int)*G8(ia + (uint32_t)n);
        }
        cv v;
        float pos[4]; fetch(&va_, i, pos, 1, 0);
        if (va_.size < 4) pos[3] = 1;
        if (va_.size < 3) pos[2] = 0;
        xform(&mvp, pos, v.p);
        if (getenv("AB_DUMP_V") && n == 0) {
            fprintf(stderr, "draw mode=%u n=%d pos0=%.3f,%.3f,%.3f,%.3f mvp0=%.4f,%.4f,%.4f,%.4f tex=%u\n",
                    modep, count, pos[0], pos[1], pos[2], pos[3], mvp.m[0], mvp.m[5], mvp.m[12], mvp.m[13], bound_tex);
        }
        if (ta_.on) { float t[4]; fetch(&ta_, i, t, 0, 0); v.u = t[0]; v.v = t[1]; } else { v.u = v.v = 0; }
        if (bound_tex && uv_div[bound_tex]) {
            float d = (float)(1 << uv_div[bound_tex]);
            v.u /= d; v.v /= d;
        }
        if (as) {
            float u = v.u < 0.f ? 0.f : v.u > 1.f ? 1.f : v.u, vv = v.v < 0.f ? 0.f : v.v > 1.f ? 1.f : v.v;
            v.u = ((float)(as->cx + 1) + u * (float)as->w) * (1.f / ATL_SIDE);
            v.v = ((float)(as->cy + 1) + vv * (float)as->h) * (1.f / ATL_SIDE);
        }
        if (ca_.on) { float c[4]; fetch(&ca_, i, c, 0, 1); for (int q = 0; q < 4; q++) v.c[q] = c[q]; if (ca_.size == 3) v.c[3] = 1; }
        else for (int q = 0; q < 4; q++) v.c[q] = color[q];
        if (modep == 4) { w[k++] = v; if (k == 3) { emit_tri(&w[0], &w[1], &w[2]); k = 0; } }
        else if (modep == 5) {
            if (n < 2) { w[n] = v; continue; }
            if (n & 1) emit_tri(&w[1], &w[0], &v); else emit_tri(&w[0], &w[1], &v);
            w[0] = w[1]; w[1] = v;
        } else {
            if (n == 0) { w[0] = v; continue; }
            if (n == 1) { w[1] = v; continue; }
            emit_tri(&w[0], &w[1], &v); w[1] = v;
        }
    }
    flush_out();
}
IMP(_glDrawArrays) { UNUSED; draw(r0, (int)r1, (int)r2, 0, 0); return 0; }
IMP(_glDrawElements) { UNUSED; draw(r0, 0, (int)r1, r2, r3); return 0; }
/* framebuffer objects: the backend owns the screen */
IMP(_glGenFramebuffersOES) { UNUSED; for (uint32_t i = 0; i < r0; i++) gw32(r1 + 4 * i, 1); return 0; }
IMP(_glGenRenderbuffersOES) { UNUSED; for (uint32_t i = 0; i < r0; i++) gw32(r1 + 4 * i, 1); return 0; }
IMP(_glBindFramebufferOES) { UNUSED; return 0; }
IMP(_glBindRenderbufferOES) { UNUSED; return 0; }
IMP(_glCheckFramebufferStatusOES) { UNUSED; return 0x8CD5; }
IMP(_glActiveTexture) { UNUSED; return 0; }
IMP(_glClientActiveTexture) { UNUSED; return 0; }
IMP(_glNormalPointer) { UNUSED; return 0; }
IMP(_glPixelStorei) { UNUSED; return 0; }
IMP(_glScissor) { UNUSED; return 0; }
IMP(_glLineWidth) { UNUSED; return 0; }
IMP(_glFrontFace) { UNUSED; frontface = r0; return 0; }
IMP(_glLightfv) { UNUSED; return 0; }
IMP(_glMaterialfv) { UNUSED; return 0; }
IMP(_glTexSubImage2D) {
    /* target, level, xoff, yoff | width, height, format, type, pixels */
    int w = (int)stackarg(0), h = (int)stackarg(1);
    uint32_t fmt = stackarg(2), type = stackarg(3), px = stackarg(4);
    if (!bound_tex || !tex_named[bound_tex] || r1 != 0 || !px) return 0;
    /* Ka3D glTexImage2D(POT, NULL) then SubImage of the PNG. TexImage2D pads
     * to POT so a 0,0 SubImage keeps the UV space the engine used. */
    if (r2 == 0 && r3 == 0) {
        uint32_t s0 = gr32(SP), s1 = gr32(SP + 4), s2 = gr32(SP + 8), s3 = gr32(SP + 12), s4 = gr32(SP + 16);
        gw32(SP, (uint32_t)h);
        gw32(SP + 4, 0);
        gw32(SP + 8, fmt);
        gw32(SP + 12, type);
        gw32(SP + 16, px);
        I__glTexImage2D(r0, 0, fmt, (uint32_t)w);
        gw32(SP, s0); gw32(SP + 4, s1); gw32(SP + 8, s2); gw32(SP + 12, s3); gw32(SP + 16, s4);
    }
    return 0;
}
IMP(_glDeleteFramebuffersOES) { UNUSED; return 0; }
IMP(_glDeleteRenderbuffersOES) { UNUSED; return 0; }
IMP(_glFramebufferRenderbufferOES) { UNUSED; return 0; }
IMP(_glFramebufferTexture2DOES) { UNUSED; return 0; }
IMP(_glRenderbufferStorageOES) { UNUSED; return 0; }
IMP(_glGetRenderbufferParameterivOES) { UNUSED; if (r2) gw32(r2, 0); return 0; }
IMP(_glGetIntegerv) {
    UNUSED;
    if (!r1) return 0;
    switch (r0) {
    case 0x0BA2: gw32(r1, (uint32_t)vx); gw32(r1 + 4, (uint32_t)vy); gw32(r1 + 8, (uint32_t)vw); gw32(r1 + 12, (uint32_t)vh); break;
    case 0x0D33: gw32(r1, 512); break;           /* GL_MAX_TEXTURE_SIZE: nano be_tex_image cap */
    case 0x0DE1: gw32(r1, bound_tex); break;
    default: gw32(r1, 0); break;
    }
    return 0;
}

/* every launch starts from zeroed .bss (the app image may be re-entered), so defaults go here */
void fe_reset_matrices(void) {
    /* Unpaired PushMatrix from an iPad-scale camera (Level 14 sx~2.37) left
     * the next level drawing into a white void. Identity, stack depth 0. */
    init_once();
    for (int i = 0; i < 3; i++) { ident(&stk[i][0]); top[i] = 0; }
    mode = 0;
    color[0] = color[1] = color[2] = color[3] = 1.f;
    vx = 0; vy = 0; vw = 480; vh = 320;
}

void fngl_reset(void) {
    sf = 1; df = 0; cullmode = 0x405 /* GL_BACK */; depthfunc = 0x201 /* GL_LESS */; depthmask = 1;
    frontface = 0x0901u; /* GL_CCW until Ka3D calls glFrontFace(GL_CW) for 2D */
    /* 480x320 landscape fills 240x432 when held Home-on-right: 0.75 * 320 = 240, 0.9 * 480 = 432 */
    fe_scale = 0.75f; fe_scale_y = 0.9f; fe_off_x = 0.f; fe_off_y = 0.f; fe_fb_h = 320.f; fe_view_h = 320;
    fe_tex_shift = 0; fe_tex_min_side = 64; fe_tex_big_side = 256; fe_tex_big_shift = 0; fe_atlas_max_side = 32;
    fe_pending_tex_shift = 0;
    fe_lowmem = 0;
    fe_lowmem_why[0] = 0;
    memset(uv_div, 0, sizeof uv_div);
    fe_reset_matrices();
}
