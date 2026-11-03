/* be_nano.c: the nano's GLES 1.1 backend for the recompiled Fruit Ninja (nano7kit, MIT).
 * The front end (fngl.c) has already transformed, clipped and culled; what arrives here is
 * triangles in panel pixels. They are written back as clip coordinates (x*w, y*w, z*w, w) with
 * identity matrices so the driver interpolates with perspective, batched by state, and kept
 * within the driver's rules (no triangle under 2 square pixels or under half a pixel across,
 * a per-frame triangle budget, at most FN_MAX_TEX live textures). */
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "gl.h"
#include "../rt/fngl.h"
#include "gl_left.h"

#define POOL_VERTS 3600                 /* 1200 triangles a frame at most (the budget cap) */
typedef struct { float x, y, z, w, u, v; uint32_t color; } ov;
static ov pool[POOL_VERTS];
static int used, batch_from;
static be_state cur;
static int have_cur;
int be_panel_w = 240, be_panel_h = 432;
int be_budget;                          /* triangles a frame; set from free memory at start */
int be_stat_tris, be_stat_tiny, be_stat_over, be_stat_draws, be_stat_live_tex, be_stat_tex_fail;

static int drawable(const be_vtx *a, const be_vtx *b, const be_vtx *c) {
    float area2 = (b->x - a->x) * (c->y - a->y) - (c->x - a->x) * (b->y - a->y);
    if (area2 < 0.f) area2 = -area2;
    if (!(area2 >= 4.0f)) return 0;     /* 2 square pixels; also rejects NaN */
    float e0 = (b->x - a->x) * (b->x - a->x) + (b->y - a->y) * (b->y - a->y);
    float e1 = (c->x - b->x) * (c->x - b->x) + (c->y - b->y) * (c->y - b->y);
    float e2 = (a->x - c->x) * (a->x - c->x) + (a->y - c->y) * (a->y - c->y);
    float l = e0 > e1 ? (e0 > e2 ? e0 : e2) : (e1 > e2 ? e1 : e2);
    return area2 * area2 >= 0.25f * l;  /* thinnest width (area2 / longest edge) >= 0.5 px */
}
static void apply(const be_state *s) {
    if (s->tex) { glEnable(GL_TEXTURE_2D); glBindTexture(GL_TEXTURE_2D, (GLuint)s->tex); glEnableClientState(GL_TEXTURE_COORD_ARRAY); }
    else { glDisable(GL_TEXTURE_2D); glDisableClientState(GL_TEXTURE_COORD_ARRAY); }
    if (s->blend) { glEnable(GL_BLEND); glBlendFunc(s->sfactor, s->dfactor); } else glDisable(GL_BLEND);
    if (s->depth_test) { glEnable(GL_DEPTH_TEST); glDepthFunc(s->depth_func); } else glDisable(GL_DEPTH_TEST);
    glDepthMask(s->depth_write ? GL_TRUE : GL_FALSE);
    if (s->alpha_test) { glEnable(0x0BC0 /* GL_ALPHA_TEST */); glAlphaFunc(0x0204 /* GL_GREATER */, s->alpha_ref); }
    else glDisable(0x0BC0);
}
extern void fn_nano_poll(void);
static void flush(void) {
    fn_nano_poll();                     /* sample the finger between draw batches (fn_nano.c) */
    if (used > batch_from && have_cur) {
        apply(&cur);
        glDrawArrays(GL_TRIANGLES, batch_from, used - batch_from);
        be_stat_draws++;
    }
    batch_from = used;
}
void be_frame_begin(void) {
    used = batch_from = 0; have_cur = 0;
    be_stat_tris = be_stat_tiny = be_stat_over = be_stat_draws = 0;
    glViewport(0, 0, be_panel_w, be_panel_h);
    glMatrixMode(GL_PROJECTION); glLoadIdentity();
    glMatrixMode(GL_MODELVIEW); glLoadIdentity();
    glMatrixMode(GL_TEXTURE); glLoadIdentity();
    glMatrixMode(GL_MODELVIEW);
    glDisable(GL_CULL_FACE);            /* culled in the front end */
    glDisable(GL_LIGHTING);
    glShadeModel(GL_SMOOTH);
    glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE);
    glEnableClientState(GL_VERTEX_ARRAY);
    glDisableClientState(GL_NORMAL_ARRAY);
    glEnableClientState(GL_COLOR_ARRAY);
    const unsigned char *base = (const unsigned char *)pool;
    glVertexPointer(4, GL_FLOAT, sizeof(ov), base + offsetof(ov, x));
    glColorPointer(4, GL_UNSIGNED_BYTE, sizeof(ov), base + offsetof(ov, color));
    glTexCoordPointer(2, GL_FLOAT, sizeof(ov), base + offsetof(ov, u));
}
void be_clear(int color, int depth, const float c[4], float d) {
    flush();
    GLbitfield m = 0;
    if (color) { glClearColor(c[0], c[1], c[2], c[3]); m |= GL_COLOR_BUFFER_BIT; }
    if (depth) { glClearDepthf(d); glDepthMask(GL_TRUE); m |= GL_DEPTH_BUFFER_BIT; }
    if (m) glClear(m);
}
void be_tris(const be_state *s, const be_vtx *v, int n) {
    if (!have_cur || memcmp(s, &cur, sizeof cur)) { flush(); cur = *s; have_cur = 1; }
    float sx = 2.f / (float)be_panel_w, sy = 2.f / (float)be_panel_h;
    for (int i = 0; i + 2 < n; i += 3) {
        if (!drawable(&v[i], &v[i + 1], &v[i + 2])) { be_stat_tiny++; continue; }
        if ((be_budget && be_stat_tris >= be_budget) || used + 3 > POOL_VERTS) { be_stat_over++; continue; }
        for (int k = 0; k < 3; k++) {
            const be_vtx *a = &v[i + k];
            ov *o = &pool[used++];
            float w = a->w > 1e-6f ? a->w : 1e-6f;
            o->x = (a->x * sx - 1.f) * w; o->y = (1.f - a->y * sy) * w; o->z = a->z * w; o->w = w;
            o->u = a->u; o->v = a->v;
            o->color = (uint32_t)a->c[0] | (uint32_t)a->c[1] << 8 | (uint32_t)a->c[2] << 16 | (uint32_t)a->c[3] << 24;
        }
        be_stat_tris++;
    }
}
void be_frame_end(void) { flush(); }

/* textures: the front end already made them 16 bits per texel and at most 512 on a side */
int be_tex_create(void) { GLuint t = 0; glGenTextures(1, &t); if (t) { be_stat_live_tex++; gll_note(t); } return (int)t; }
void be_tex_delete(int h) { GLuint t = (GLuint)h; if (t) { glDeleteTextures(1, &t); be_stat_live_tex--; } }
void be_tex_param(int h, uint32_t p, uint32_t v) {
    if (!h) return;
    if (p == GL_TEXTURE_MIN_FILTER && v != GL_NEAREST) v = GL_LINEAR;   /* no mip chains are uploaded */
    glBindTexture(GL_TEXTURE_2D, (GLuint)h);
    glTexParameteri(GL_TEXTURE_2D, p, (GLint)v);
}
void be_tex_image(int h, int level, int w, int ht, uint32_t format, uint32_t type, const void *px) {
    if (!h || level) return;
    if (w > 512 || ht > 512) { be_stat_tex_fail++; return; }
    while (glGetError()) {}
    glBindTexture(GL_TEXTURE_2D, (GLuint)h);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexImage2D(GL_TEXTURE_2D, 0, (GLint)format, w, ht, 0, format, type, px);
    if (glGetError()) be_stat_tex_fail++;
}
void be_tex_compressed(int h, int level, uint32_t fmt, int w, int ht, const void *data, uint32_t size) {
    if (!h || level) return;
    while (glGetError()) {}
    glBindTexture(GL_TEXTURE_2D, (GLuint)h);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glCompressedTexImage2D(GL_TEXTURE_2D, 0, fmt, w, ht, 0, (GLsizei)size, data);
    if (glGetError()) be_stat_tex_fail++;
}
