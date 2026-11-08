/* fngl.c: GLES 1.1 state, CPU transform and clipping for the Android .so
 * (nano7kit, MIT). Pointers are real host addresses. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fngl.h"
#include "rt_plat.h"

float fe_scale, fe_scale_y, fe_off_x, fe_off_y, fe_fb_h;
int fe_view_h = 320;
int fe_stat_tris, fe_stat_draws, fe_stat_tex_bytes;
static unsigned fe_draw_seq;
static uint32_t bound_fbo, fbo_tex, fbo_n;

typedef struct { float m[16]; } mat;
static mat stk[3][16];
static int top[3], mode;
static float color[4] = {1, 1, 1, 1};
static int vx, vy, vw = 480, vh = 320;
typedef struct { int on, size; uint32_t type; int stride; const void *ptr; uint32_t buf; } arr;
static arr va_, ta_, ta1_, ca_;
static uint32_t bound_buf;
#define MAXBUF 256
static struct { void *data; uint32_t size; } bufs[MAXBUF];
#define MAXTEX 512
static int tex_bytes[MAXTEX];
int fe_tex_shift, fe_tex_min_side, fe_tex_big_side, fe_tex_big_shift, fe_stat_tex_peak, fe_stat_tex_count, fe_stat_tex_count_peak;
int fe_pending_tex_shift;
static uint8_t uv_div[MAXTEX];
static int texh[MAXTEX];
static uint32_t bound_tex, bound_tex1;
static int tex_unit, client_unit;
static int en_blend, en_tex, en_tex1, en_depth, en_cull, en_alpha, en_scissor;
static int sc_x, sc_y, sc_w = 480, sc_h = 320;
static uint32_t sf, df, cullmode, depthfunc, frontface = 0x0901u;
static int depthmask;
static float clear_rgba[4], clear_depth = 1.f;
static int color_mask[4] = {1, 1, 1, 1};

static void ident(mat *m) { memset(m, 0, sizeof *m); m->m[0] = m->m[5] = m->m[10] = m->m[15] = 1; }
static void mul(mat *r, const mat *a, const mat *b) {
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

void ab_glMatrixMode(GLenum m) {
    init_once();
    /* Ka3D Context::setMatrix passes 0..3 (world/view/tex0/tex1) as well as
     * the GLES enums. Mapping 2 or 3 onto MODELVIEW loaded identity over the
     * camera, which clipped MaskedImage ice/stone batches on L2+. */
    if (m == 0x1701u || m == 1u) mode = 1;
    else if (m == 0x1702u || m == 2u || m == 3u) mode = 2;
    else mode = 0;
}
void ab_glLoadIdentity(void) { init_once(); ident(cur()); }
void ab_glLoadMatrixf(const GLfloat *m) { init_once(); if (m) memcpy(cur()->m, m, 64); }
void ab_glMultMatrixf(const GLfloat *m) {
    mat b;
    init_once();
    if (!m) return;
    memcpy(b.m, m, 64);
    mul(cur(), cur(), &b);
}
void ab_glPushMatrix(void) { init_once(); if (top[mode] < 15) { stk[mode][top[mode] + 1] = *cur(); top[mode]++; } }
void ab_glPopMatrix(void) { if (top[mode] > 0) top[mode]--; }
void ab_glViewport(GLint x, GLint y, GLsizei w, GLsizei h) { vx = x; vy = y; vw = w; vh = h; }
void ab_glClearColor(GLclampf r, GLclampf g, GLclampf b, GLclampf a) {
    clear_rgba[0] = r; clear_rgba[1] = g; clear_rgba[2] = b; clear_rgba[3] = a;
}
void ab_glClear(GLbitfield mask) {
    be_clear((mask & 0x4000) != 0, (mask & 0x100) != 0, clear_rgba, clear_depth);
}
void ab_glColor4f(GLfloat r, GLfloat g, GLfloat b, GLfloat a) {
    color[0] = r; color[1] = g; color[2] = b; color[3] = a;
}
void ab_glColorMask(GLboolean r, GLboolean g, GLboolean b, GLboolean a) {
    color_mask[0] = r; color_mask[1] = g; color_mask[2] = b; color_mask[3] = a;
}
static void cap(uint32_t c, int on) {
    switch (c) {
    case 0x0BE2: en_blend = on; break;
    case 0x0DE1:
        /* TEXTURE_2D is per-unit. MaskedImage enables unit 0 then disables
         * unit 1; a shared en_tex flag turned every later ice/stone draw into
         * an untextured white fill. */
        if (tex_unit != 0) en_tex1 = on;
        else en_tex = on;
        break;
    case 0x0B71: en_depth = on; break;
    case 0x0B44: en_cull = on; break;
    case 0x0BC0: en_alpha = on; break;
    case 0x0C11: en_scissor = on; break;
    default: break;
    }
}
void ab_glEnable(GLenum c) { cap(c, 1); }
void ab_glDisable(GLenum c) { cap(c, 0); }
void ab_glBlendFunc(GLenum s, GLenum d) { sf = s; df = d; }
void ab_glDepthMask(GLboolean flag) { depthmask = flag != 0; }
void ab_glDepthFunc(GLenum func) { depthfunc = func; }
static arr *which(uint32_t a) {
    if (a == 0x8074) return &va_;
    if (a == 0x8078) return client_unit != 0 ? &ta1_ : &ta_;
    if (a == 0x8076) return &ca_;
    return NULL;
}
void ab_glEnableClientState(GLenum a) { arr *p = which(a); if (p) p->on = 1; }
void ab_glDisableClientState(GLenum a) { arr *p = which(a); if (p) p->on = 0; }
static void setp(arr *a, int size, uint32_t type, int stride, const void *ptr) {
    a->size = size; a->type = type; a->stride = stride; a->ptr = ptr; a->buf = bound_buf;
}
void ab_glVertexPointer(GLint size, GLenum type, GLsizei stride, const GLvoid *ptr) { setp(&va_, size, type, stride, ptr); }
void ab_glTexCoordPointer(GLint size, GLenum type, GLsizei stride, const GLvoid *ptr) {
    setp(client_unit != 0 ? &ta1_ : &ta_, size, type, stride, ptr);
}
void ab_glColorPointer(GLint size, GLenum type, GLsizei stride, const GLvoid *ptr) { setp(&ca_, size, type, stride, ptr); }
void ab_glNormalPointer(GLenum type, GLsizei stride, const GLvoid *ptr) { (void)type; (void)stride; (void)ptr; }

void ab_glGenBuffers(GLsizei n, GLuint *buffers) {
    for (int i = 0; i < n; i++) {
        uint32_t k = 0;
        for (uint32_t t = 1; t < MAXBUF; t++) if (!bufs[t].size && !bufs[t].data) { k = t; bufs[t].size = 1; break; }
        buffers[i] = k;
    }
}
void ab_glBindBuffer(GLenum target, GLuint buffer) { if (target == 0x8892) bound_buf = buffer < MAXBUF ? buffer : 0; }
void ab_glBufferData(GLenum target, GLsizei size, const GLvoid *data, GLenum usage) {
    (void)usage;
    if (target != 0x8892 || !bound_buf) return;
    free(bufs[bound_buf].data);
    bufs[bound_buf].data = malloc(size > 0 ? (size_t)size : 4);
    bufs[bound_buf].size = (uint32_t)size;
    if (data && bufs[bound_buf].data) memcpy(bufs[bound_buf].data, data, (size_t)size);
}
void ab_glDeleteBuffers(GLsizei n, const GLuint *buffers) {
    for (int i = 0; i < n; i++) {
        uint32_t t = buffers[i];
        if (t && t < MAXBUF) { free(bufs[t].data); bufs[t].data = 0; bufs[t].size = 0; }
    }
}

static uint8_t tex_named[MAXTEX];
static uint16_t tex_log_w[MAXTEX], tex_log_h[MAXTEX], tex_gpu_w[MAXTEX], tex_gpu_h[MAXTEX];
static uint8_t tex_has_image[MAXTEX], tex_empty_draw[MAXTEX], tex_bind_n[MAXTEX];
static uint32_t tex_fmt[MAXTEX];
static char tex_file[MAXTEX][40];
static char tex_pending_name[40];
typedef struct { int page, cx, cy, w, h; } atl_slot;
static atl_slot atl[MAXTEX];
static void tex_clear_slot(uint32_t t) {
    texh[t] = 0; atl[t].page = 0; uv_div[t] = 0;
    tex_log_w[t] = tex_log_h[t] = tex_gpu_w[t] = tex_gpu_h[t] = 0;
    tex_has_image[t] = 0; tex_empty_draw[t] = 0; tex_bind_n[t] = 0; tex_fmt[t] = 0;
    tex_file[t][0] = 0;
}
static void ab_tex_log(const char *op, unsigned name, int w, int h, unsigned fmt, unsigned type,
                       int level, int nbytes) {
    char b[280];
    unsigned n = name < MAXTEX ? name : 0;
    snprintf(b, sizeof b,
             "AB_TEX %s name=%u level=%d %dx%d fmt=0x%x type=0x%x bytes=%d log=%ux%u gpu=%ux%u "
             "img=%u uvdiv=%u file=%s",
             op, name, level, w, h, fmt, type, nbytes,
             (unsigned)tex_log_w[n], (unsigned)tex_log_h[n],
             (unsigned)tex_gpu_w[n], (unsigned)tex_gpu_h[n],
             (unsigned)tex_has_image[n], (unsigned)uv_div[n],
             tex_file[n][0] ? tex_file[n] : (tex_pending_name[0] ? tex_pending_name : "-"));
    plat_log_line(b);
}
void ab_glGenTextures(GLsizei n, GLuint *textures) {
    for (int i = 0; i < n; i++) {
        uint32_t k = 0;
        for (uint32_t t = 1; t < MAXTEX; t++) if (!tex_named[t]) {
            k = t; tex_named[t] = 1; tex_clear_slot(t); break;
        }
        textures[i] = k;
        ab_tex_log("GEN", k, 0, 0, 0, 0, 0, 0);
    }
}
void ab_glBindTexture(GLenum target, GLuint texture) {
    uint32_t name = texture < MAXTEX ? texture : 0;
    (void)target;
    if (tex_unit != 0) {
        static int u1log;
        bound_tex1 = name;
        if (u1log < 12) {
            ab_tex_log(name ? "BIND1" : "BIND1_0", name,
                       name ? tex_gpu_w[name] : 0, name ? tex_gpu_h[name] : 0,
                       name ? tex_fmt[name] : 0, 0, 0, 0);
            u1log++;
        }
        return;
    }
    bound_tex = name;
    if (bound_tex && tex_pending_name[0] && !tex_file[bound_tex][0])
        memcpy(tex_file[bound_tex], tex_pending_name, sizeof tex_file[bound_tex]);
    if (bound_tex) {
        if (tex_bind_n[bound_tex] < 3 || !tex_has_image[bound_tex])
            ab_tex_log("BIND", bound_tex, tex_gpu_w[bound_tex], tex_gpu_h[bound_tex],
                       tex_fmt[bound_tex], 0, 0, 0);
        if (tex_bind_n[bound_tex] < 255) tex_bind_n[bound_tex]++;
    }
}
static void atlas_free(uint32_t n);
void ab_glDeleteTextures(GLsizei n, const GLuint *textures) {
    for (int i = 0; i < n; i++) {
        uint32_t t = textures[i];
        if (!t || t >= MAXTEX || !tex_named[t]) continue;
        ab_tex_log("DELETE", t, tex_gpu_w[t], tex_gpu_h[t], tex_fmt[t], 0, 0, tex_bytes[t]);
        if (atl[t].page) atlas_free(t);
        if (texh[t]) { be_tex_delete(texh[t]); texh[t] = 0; fe_stat_tex_count--; }
        fe_stat_tex_bytes -= tex_bytes[t]; tex_bytes[t] = 0; tex_named[t] = 0;
        tex_clear_slot(t);
    }
}
void ab_glTexParameteri(GLenum target, GLenum pname, GLint param) {
    uint32_t name = tex_unit != 0 ? bound_tex1 : bound_tex;
    (void)target;
    if (name && texh[name] && !atl[name].page) be_tex_param(texh[name], pname, (uint32_t)param);
}

static int pot2(int n) {
    int p = 1;
    while (p < n) p <<= 1;
    return p < 1 ? 1 : p;
}

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
            pages[p].px = (uint16_t *)calloc(ATL_SIDE * ATL_SIDE, 2);
            if (!pages[p].px) return 0;
            pages[p].handle = be_tex_create();
            if (!pages[p].handle) { free(pages[p].px); pages[p].px = NULL; return 0; }
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
static void upload_tex(int w, int h, uint32_t fmt, uint32_t type, const void *px) {
    int sh, ow, oh, k, opaque;
    uint32_t otype, ofmt;
    uint16_t *out;
    if (!bound_tex || !tex_named[bound_tex]) return;
    sh = (w >= fe_tex_big_side || h >= fe_tex_big_side) ? fe_tex_big_shift : (w >= fe_tex_min_side || h >= fe_tex_min_side) ? fe_tex_shift : 0;
    while (sh && ((w >> sh) < 1 || (h >> sh) < 1)) sh--;
    ow = w >> sh; oh = h >> sh; k = 1 << sh;
    opaque = fmt == 0x1907 || type == 0x8363 || fmt == 0x1909;
    otype = opaque ? 0x8363u : 0x8033u; ofmt = opaque ? 0x1907u : 0x1908u;
    out = px ? (uint16_t *)malloc((size_t)ow * (size_t)oh * 2u) : NULL;
    if (px && !out) { plat_log_line("texture: no memory to convert"); return; }
    if (out) {
        const uint8_t *src = px;
        for (int y = 0; y < oh; y++) for (int x = 0; x < ow; x++) {
            int acc[4] = {0, 0, 0, 0}, c[4];
            for (int j = 0; j < k; j++) for (int i = 0; i < k; i++) {
                texel(src, (y * k + j) * w + x * k + i, fmt, type, c);
                acc[0] += c[0] * c[3]; acc[1] += c[1] * c[3]; acc[2] += c[2] * c[3]; acc[3] += c[3];
            }
            int a = acc[3] / (k * k), r = acc[3] ? acc[0] / acc[3] : 0, g = acc[3] ? acc[1] / acc[3] : 0, b = acc[3] ? acc[2] / acc[3] : 0;
            out[y * ow + x] = opaque ? (uint16_t)((r >> 3) << 11 | (g >> 2) << 5 | (b >> 3))
                                     : (uint16_t)((r >> 4) << 12 | (g >> 4) << 8 | (b >> 4) << 4 | (a >> 4));
        }
    }
    {
        int log_w = ow, log_h = oh;
        if (atl[bound_tex].page) atlas_free(bound_tex);
        if (out && ow <= fe_atlas_max_side && oh <= fe_atlas_max_side) {
            uint16_t *q = out;
            if (opaque) for (int i = 0; i < ow * oh; i++) {
                uint16_t v = q[i]; q[i] = (uint16_t)((v >> 12) << 12 | ((v >> 7) & 15) << 8 | ((v >> 1) & 15) << 4 | 15);
            }
            if (atlas_put(bound_tex, ow, oh, q)) {
                if (texh[bound_tex]) { be_tex_delete(texh[bound_tex]); texh[bound_tex] = 0; fe_stat_tex_count--; }
                free(out);
                fe_stat_tex_bytes -= tex_bytes[bound_tex]; tex_bytes[bound_tex] = 0;
                tex_log_w[bound_tex] = (uint16_t)log_w; tex_log_h[bound_tex] = (uint16_t)log_h;
                tex_gpu_w[bound_tex] = (uint16_t)log_w; tex_gpu_h[bound_tex] = (uint16_t)log_h;
                tex_has_image[bound_tex] = 1; tex_fmt[bound_tex] = ofmt;
                if (fe_pending_tex_shift > 0 && fe_pending_tex_shift < 8) {
                    uv_div[bound_tex] = (uint8_t)fe_pending_tex_shift;
                    fe_pending_tex_shift = 0;
                }
                if (tex_pending_name[0]) memcpy(tex_file[bound_tex], tex_pending_name, sizeof tex_file[bound_tex]);
                ab_tex_log("ATLAS", bound_tex, log_w, log_h, ofmt, otype, 0, log_w * log_h * 2);
                return;
            }
            if (opaque) for (int i = 0; i < ow * oh; i++) {
                uint16_t v = q[i]; q[i] = (uint16_t)(((v >> 12) * 31 / 15) << 11 | (((v >> 8) & 15) * 63 / 15) << 5 | ((v >> 4) & 15) * 31 / 15);
            }
        }
        {
            int pw = pot2(ow), ph = pot2(oh);
            if (pw != ow || ph != oh) {
                uint16_t *pad = (uint16_t *)calloc((size_t)pw * (size_t)ph, 2u);
                if (out && pad) {
                    for (int y = 0; y < oh; y++)
                        memcpy(pad + (size_t)y * (size_t)pw, out + (size_t)y * (size_t)ow, (size_t)ow * 2u);
                }
                free(out);
                out = pad;
                if (px && !out) { plat_log_line("texture POT pad: no memory"); return; }
                {
                    char b[160];
                    snprintf(b, sizeof b, "AB_TEX POT_PAD name=%u log=%dx%d gpu=%dx%d file=%s",
                             bound_tex, log_w, log_h, pw, ph,
                             tex_pending_name[0] ? tex_pending_name :
                             (tex_file[bound_tex][0] ? tex_file[bound_tex] : "-"));
                    plat_log_line(b);
                }
                ow = pw;
                oh = ph;
            }
        }
        if (!texh[bound_tex]) {
            texh[bound_tex] = be_tex_create();
            if (!texh[bound_tex]) { free(out); return; }
            if (++fe_stat_tex_count > fe_stat_tex_count_peak) fe_stat_tex_count_peak = fe_stat_tex_count;
        }
        be_tex_image(texh[bound_tex], 0, ow, oh, ofmt, otype, out);
        free(out);
        tex_log_w[bound_tex] = (uint16_t)log_w; tex_log_h[bound_tex] = (uint16_t)log_h;
        tex_gpu_w[bound_tex] = (uint16_t)ow; tex_gpu_h[bound_tex] = (uint16_t)oh;
        tex_fmt[bound_tex] = ofmt;
        tex_has_image[bound_tex] = px ? 1 : 0;
        if (fe_pending_tex_shift > 0 && fe_pending_tex_shift < 8) {
            uv_div[bound_tex] = (uint8_t)fe_pending_tex_shift;
            fe_pending_tex_shift = 0;
        }
        if (tex_pending_name[0]) memcpy(tex_file[bound_tex], tex_pending_name, sizeof tex_file[bound_tex]);
        fe_stat_tex_bytes += ow * oh * 2 - tex_bytes[bound_tex]; tex_bytes[bound_tex] = ow * oh * 2;
        if (fe_stat_tex_bytes > fe_stat_tex_peak) fe_stat_tex_peak = fe_stat_tex_bytes;
        ab_tex_log(px ? "TEXIMAGE" : "TEXIMAGE_NULL", bound_tex, log_w, log_h, ofmt, otype, 0, ow * oh * 2);
    }
}
void ab_glTexImage2D(GLenum target, GLint level, GLint internalformat, GLsizei width, GLsizei height,
                     GLint border, GLenum format, GLenum type, const GLvoid *pixels) {
    (void)target; (void)internalformat; (void)border;
    if (level > 0) {
        ab_tex_log("TEXIMAGE_MIP", bound_tex, width, height, (unsigned)format, type, level, 0);
        return;
    }
    upload_tex(width, height, format, type, pixels);
}
void ab_glTexSubImage2D(GLenum target, GLint level, GLint xoff, GLint yoff, GLsizei width, GLsizei height,
                        GLenum format, GLenum type, const GLvoid *pixels) {
    (void)target;
    if (!bound_tex || !tex_named[bound_tex] || level != 0 || !pixels) {
        ab_tex_log("TEXSUB_SKIP", bound_tex, width, height, format, type, level, 0);
        return;
    }
    ab_tex_log("TEXSUB", bound_tex, width, height, format, type, level, width * height * 2);
    if (xoff == 0 && yoff == 0) upload_tex(width, height, format, type, pixels);
}
void ab_glCompressedTexImage2D(GLenum target, GLint level, GLenum internalformat, GLsizei width, GLsizei height,
                               GLint border, GLsizei imageSize, const GLvoid *data) {
    (void)target; (void)border;
    if (!bound_tex || !tex_named[bound_tex] || !data || level != 0) {
        ab_tex_log("COMPRESSED_SKIP", bound_tex, width, height, internalformat, 0, level, imageSize);
        return;
    }
    if (atl[bound_tex].page) atlas_free(bound_tex);
    if (!texh[bound_tex]) {
        texh[bound_tex] = be_tex_create();
        if (!texh[bound_tex]) return;
        if (++fe_stat_tex_count > fe_stat_tex_count_peak) fe_stat_tex_count_peak = fe_stat_tex_count;
    }
    be_tex_compressed(texh[bound_tex], 0, internalformat, width, height, data, (uint32_t)imageSize);
    tex_log_w[bound_tex] = (uint16_t)width; tex_log_h[bound_tex] = (uint16_t)height;
    tex_gpu_w[bound_tex] = (uint16_t)width; tex_gpu_h[bound_tex] = (uint16_t)height;
    tex_fmt[bound_tex] = internalformat;
    tex_has_image[bound_tex] = 1;
    if (fe_pending_tex_shift > 0 && fe_pending_tex_shift < 8) {
        uv_div[bound_tex] = (uint8_t)fe_pending_tex_shift;
        fe_pending_tex_shift = 0;
    }
    if (tex_pending_name[0]) memcpy(tex_file[bound_tex], tex_pending_name, sizeof tex_file[bound_tex]);
    fe_stat_tex_bytes += (int)imageSize - tex_bytes[bound_tex]; tex_bytes[bound_tex] = (int)imageSize;
    if (fe_stat_tex_bytes > fe_stat_tex_peak) fe_stat_tex_peak = fe_stat_tex_bytes;
    ab_tex_log("COMPRESSED", bound_tex, width, height, internalformat, 0, 0, imageSize);
}

typedef struct { float p[4]; float u, v; float c[4]; } cv;
static const uint8_t *base_of(const arr *a) {
    if (a->buf && a->buf < MAXBUF && bufs[a->buf].data)
        return (const uint8_t *)bufs[a->buf].data + (uintptr_t)a->ptr;
    return (const uint8_t *)a->ptr;
}
static int tsize(uint32_t t) { return t == 0x1400 || t == 0x1401 ? 1 : t == 0x1402 || t == 0x1403 ? 2 : 4; }
static float u32f(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }
static float fetch1(const uint8_t *a, uint32_t type, int norm) {
    switch (type) {
    case 0x1406: { uint32_t u; memcpy(&u, a, 4); return u32f(u); }
    case 0x140C: { int32_t s; memcpy(&s, a, 4); return (float)s / 65536.f; }
    case 0x1402: { int16_t s; memcpy(&s, a, 2); return norm ? s / 32767.f : (float)s; }
    case 0x1403: { uint16_t s; memcpy(&s, a, 2); return norm ? s / 65535.f : (float)s; }
    case 0x1400: { int8_t s = (int8_t)*a; return norm ? s / 127.f : (float)s; }
    case 0x1401: { uint8_t s = *a; return norm ? s / 255.f : (float)s; }
    }
    return 0;
}
static void fetch(const arr *a, int i, float *out, int def_w, int norm) {
    int es = tsize(a->type), stride = a->stride ? a->stride : es * a->size;
    const uint8_t *p = base_of(a) + stride * i;
    (void)def_w;
    out[0] = out[1] = out[2] = 0; out[3] = 1.f;
    if (!p) return;
    for (int k = 0; k < a->size && k < 4; k++) out[k] = fetch1(p + es * k, a->type, norm);
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
    case 0: return p[3] - 1e-5f;
    case 1: return p[0] + p[3];
    case 2: return p[3] - p[0];
    case 3: return p[1] + p[3];
    case 4: return p[3] - p[1];
    case 5: return p[2] + p[3];
    default: return p[3] - p[2];
    }
}
#define MAXV 768
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
    for (int pl = 0; pl < 7 && n >= 3; pl++) { /* 7 = xyzw clip; AB_CLIP_NOZ uses 5 */
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
        /* Home-on-right map as PvZ n7gl_dev_a.inc / the iOS Angry Birds port:
         *   px = x0 + fy * ky, py = y0 + fx * kx */
        sv[i].x = fe_off_x + fy * fe_scale;
        sv[i].y = fe_off_y + fx * fe_scale_y;
        sv[i].z = nz; sv[i].w = p->p[3];
        sv[i].u = p->u; sv[i].v = p->v;
        for (int k = 0; k < 4; k++) {
            float cc = p->c[k];
            if (!color_mask[k]) cc = k == 3 ? 1.f : 0.f;
            sv[i].c[k] = (uint8_t)(cc <= 0 ? 0 : cc >= 1 ? 255 : cc * 255.f + 0.5f);
        }
    }
    if (en_cull && cullmode != 0x408) {
        float area = (sv[1].x - sv[0].x) * (sv[2].y - sv[0].y) - (sv[2].x - sv[0].x) * (sv[1].y - sv[0].y);
        int ccw = area < 0;
        int front = frontface == 0x0900u ? !ccw : ccw;
        if ((cullmode == 0x405 && !front) || (cullmode == 0x404 && front))
            return;
    }
    if (!ensure_outv()) return;
    for (int i = 1; i + 1 < n; i++) {
        if (outn + 3 > MAXV * 3) flush_out();
        outv[outn++] = sv[0]; outv[outn++] = sv[i]; outv[outn++] = sv[i + 1];
    }
}
static void draw(uint32_t modep, int first, int count, uint32_t idx_type, const void *idx) {
    if (!va_.on || count <= 0) return;
    if (modep < 4 || modep > 6) return;
    init_once();
    fe_stat_draws++;
    fe_draw_seq++;
    /* TEXTURE_2D enable is per-unit. MaskedImage / EGL_Shader_Default::begin
     * leaves TEXTURE1 as the active unit after a bind-0 cleanup; a later
     * Disable then used to clear this flag and drop every textured draw.
     * Skip only near-white untextured fills (Ka3D ice/stone fallback) so a
     * Disable on unit 0 cannot wipe the frame, while rubber-band / HUD
     * colours still draw. */
    (void)en_tex1; (void)ta1_.on;
    if (!en_tex && color[0] > 0.92f && color[1] > 0.92f && color[2] > 0.92f)
        return;
    if (en_tex && bound_tex && tex_named[bound_tex] && !atl[bound_tex].page && !tex_has_image[bound_tex]
        && !tex_empty_draw[bound_tex]) {
        tex_empty_draw[bound_tex] = 1;
        ab_tex_log("DRAW_NO_IMAGE", bound_tex, tex_gpu_w[bound_tex], tex_gpu_h[bound_tex],
                   tex_fmt[bound_tex], 0, 0, 0);
    }
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
        if (idx_type && idx) {
            const uint8_t *ia = (const uint8_t *)idx;
            i = idx_type == 0x1403 ? (int)((const uint16_t *)ia)[n] : (int)ia[n];
        }
        cv v;
        float pos[4]; fetch(&va_, i, pos, 1, 0);
        if (va_.size < 4) pos[3] = 1;
        if (va_.size < 3) pos[2] = 0;
        xform(&mvp, pos, v.p);
        if (ta_.on) { float t[4]; fetch(&ta_, i, t, 0, 0); v.u = t[0]; v.v = t[1]; } else { v.u = v.v = 0; }
        if (bound_tex) {
            /* texscale undoes convert-time sheet shrink. Then map the logical
             * (NPOT) image onto the POT-padded GPU texture so ice/theme-2
             * sprites on the right of INGAME_1 do not sample the zero pad. */
            if (uv_div[bound_tex]) {
                float d = (float)(1 << uv_div[bound_tex]);
                v.u /= d; v.v /= d;
            }
            if (tex_gpu_w[bound_tex] > 0 && tex_log_w[bound_tex] > 0 &&
                tex_gpu_w[bound_tex] != tex_log_w[bound_tex])
                v.u *= (float)tex_log_w[bound_tex] / (float)tex_gpu_w[bound_tex];
            if (tex_gpu_h[bound_tex] > 0 && tex_log_h[bound_tex] > 0 &&
                tex_gpu_h[bound_tex] != tex_log_h[bound_tex])
                v.v *= (float)tex_log_h[bound_tex] / (float)tex_gpu_h[bound_tex];
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
void ab_glDrawArrays(GLenum modep, GLint first, GLsizei count) { draw(modep, first, count, 0, 0); }
void ab_glDrawElements(GLenum modep, GLsizei count, GLenum type, const GLvoid *indices) { draw(modep, 0, count, type, indices); }
void ab_glGenFramebuffersOES(GLsizei n, GLuint *f) {
    for (int i = 0; i < n; i++) f[i] = ++fbo_n ? fbo_n : ++fbo_n;
    ab_tex_log("GEN_FBO", f[0], n, 0, 0, 0, 0, 0);
}
void ab_glGenRenderbuffersOES(GLsizei n, GLuint *r) { for (int i = 0; i < n; i++) r[i] = 1; }
void ab_glBindFramebufferOES(GLenum t, GLuint f) {
    (void)t;
    bound_fbo = f;
}
void ab_glBindRenderbufferOES(GLenum t, GLuint r) { (void)t; (void)r; }
GLenum ab_glCheckFramebufferStatusOES(GLenum t) { (void)t; return 0x8CD5; }
void ab_glDeleteFramebuffersOES(GLsizei n, const GLuint *f) { (void)n; (void)f; }
void ab_glDeleteRenderbuffersOES(GLsizei n, const GLuint *r) { (void)n; (void)r; }
void ab_glFramebufferRenderbufferOES(GLenum t, GLenum att, GLenum rt, GLuint r) { (void)t; (void)att; (void)rt; (void)r; }
void ab_glFramebufferTexture2DOES(GLenum t, GLenum att, GLenum tt, GLuint tex, GLint level) {
    (void)t; (void)att; (void)tt; (void)level;
    fbo_tex = tex;
}
void ab_glRenderbufferStorageOES(GLenum t, GLenum fmt, GLsizei w, GLsizei h) { (void)t; (void)fmt; (void)w; (void)h; }
void ab_glActiveTexture(GLenum t) {
    int u = (int)(t - 0x84C0u);
    if (u >= 0 && u < 2) tex_unit = u;
}
void ab_glClientActiveTexture(GLenum t) {
    int u = (int)(t - 0x84C0u);
    if (u >= 0 && u < 2) client_unit = u;
}
void ab_glPixelStorei(GLenum pname, GLint param) { (void)pname; (void)param; }
void ab_glScissor(GLint x, GLint y, GLsizei w, GLsizei h) {
    sc_x = x; sc_y = y; sc_w = w; sc_h = h;
}
void ab_glLineWidth(GLfloat w) { (void)w; }
void ab_glFrontFace(GLenum m) { frontface = m; }
void ab_glLightfv(GLenum light, GLenum pname, const GLfloat *p) { (void)light; (void)pname; (void)p; }
void ab_glMaterialfv(GLenum face, GLenum pname, const GLfloat *p) { (void)face; (void)pname; (void)p; }
void ab_glTexEnvi(GLenum target, GLenum pname, GLint param) { (void)target; (void)pname; (void)param; }
void ab_glFinish(void) {}
void ab_glGetIntegerv(GLenum pname, GLint *params) {
    if (!params) return;
    switch (pname) {
    case 0x0BA2: params[0] = vx; params[1] = vy; params[2] = vw; params[3] = vh; break;
    case 0x0D33: params[0] = 1024; break; /* MAX_TEXTURE_SIZE */
    case 0x0D3A: params[0] = 1024; params[1] = 1024; break; /* MAX_VIEWPORT_DIMS */
    case 0x84E8: params[0] = 1024; break; /* MAX_RENDERBUFFER_SIZE */
    case 0x8872: params[0] = 8; break;    /* MAX_TEXTURE_IMAGE_UNITS */
    case 0x0DE1: params[0] = (GLint)bound_tex; break;
    default: params[0] = 0; break;
    }
}
const GLubyte *ab_glGetString(GLenum name) {
    switch (name) {
    case 0x1F00: return (const GLubyte *)"nano7kit";
    case 0x1F01: return (const GLubyte *)"fngl";
    case 0x1F02: return (const GLubyte *)"OpenGL ES-CM 1.1";
    case 0x1F03: return (const GLubyte *)"GL_OES_framebuffer_object GL_IMG_texture_compression_pvrtc ";
    default: return (const GLubyte *)"";
    }
}

void fe_reset_matrices(void) {
    init_once();
    for (int i = 0; i < 3; i++) { ident(&stk[i][0]); top[i] = 0; }
    mode = 0;
    color[0] = color[1] = color[2] = color[3] = 1.f;
    vx = 0; vy = 0; vw = 480; vh = 320;
}

void fngl_reset(void) {
    uint32_t t;
    sf = 1; df = 0; cullmode = 0x405; depthfunc = 0x201; depthmask = 1;
    frontface = 0x0901u;
    fe_scale = 0.75f; fe_scale_y = 0.9f; fe_off_x = 0.f; fe_off_y = 0.f; fe_fb_h = 320.f; fe_view_h = 320;
    fe_tex_shift = 0; fe_tex_min_side = 64; fe_tex_big_side = 256; fe_tex_big_shift = 0; fe_atlas_max_side = 32;
    fe_pending_tex_shift = 0;
    tex_pending_name[0] = 0;
    bound_tex = bound_tex1 = 0;
    tex_unit = client_unit = 0;
    en_tex = en_tex1 = 0;
    memset(&ta1_, 0, sizeof ta1_);
    memset(uv_div, 0, sizeof uv_div);
    for (t = 1; t < MAXTEX; t++) {
        if (tex_named[t]) {
            if (atl[t].page) atlas_free(t);
            if (texh[t]) { be_tex_delete(texh[t]); texh[t] = 0; fe_stat_tex_count--; }
            fe_stat_tex_bytes -= tex_bytes[t]; tex_bytes[t] = 0;
        }
        tex_named[t] = 0;
        tex_clear_slot(t);
    }
    fe_reset_matrices();
}

void fe_note_texfile(const char *path) {
    static int loaded, n;
    static char names[80][40];
    static uint8_t shifts[80];
    static int guard;
    const char *base, *dot;
    char stem[40];
    int i, len;
    if (!path || guard) return;
    tex_pending_name[0] = 0;
    if (!loaded) {
        void *buf = 0;
        uint32_t sz = 0;
        loaded = 1;
        guard = 1;
        if (plat_read_file("texscale", &buf, &sz) == 0 && buf) {
            char *s = (char *)buf, *e = s + sz;
            while (s < e && n < 80) {
                char *nl = s;
                int sh = 0, k = 0;
                while (nl < e && *nl != '\n' && *nl != '\r') nl++;
                while (s < nl && *s != ' ' && k < 39) names[n][k++] = *s++;
                names[n][k] = 0;
                while (s < nl && *s == ' ') s++;
                while (s < nl && *s >= '0' && *s <= '9') { sh = sh * 10 + (*s - '0'); s++; }
                if (names[n][0] && sh > 0 && sh < 8) { shifts[n] = (uint8_t)sh; n++; }
                while (nl < e && (*nl == '\n' || *nl == '\r')) nl++;
                s = nl;
            }
            free(buf);
        }
        guard = 0;
    }
    base = strrchr(path, '/');
    base = base ? base + 1 : path;
    dot = strrchr(base, '.');
    len = (int)(dot && dot > base ? (size_t)(dot - base) : strlen(base));
    if (len <= 0 || len >= 40) return;
    memcpy(stem, base, (size_t)len);
    stem[len] = 0;
    memcpy(tex_pending_name, stem, (size_t)len + 1);
    for (i = 0; i < n; i++) if (strcmp(names[i], stem) == 0) {
        fe_pending_tex_shift = shifts[i];
        return;
    }
}
