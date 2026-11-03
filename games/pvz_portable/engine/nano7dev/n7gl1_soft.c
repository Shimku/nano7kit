/* nano7 PvZ dry run (never shipped): a small software OpenGL ES 1.1 backend for n7gl1.h, enough for the device
 * renderer: client arrays, texture matrix, MODULATE, the blend functions the game uses, scissor, 16-bit textures.
 * Texture storage comes from mmap, NOT the game arena: on the iPod the GL driver keeps textures outside the app's
 * heap too (they are counted separately, as texture bytes). Rasterizes only the frames the dry run asks for. */
#define _GNU_SOURCE
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include "n7gl1.h"
#include "n7dev.h"

#define W 240
#define H 432
static float fb[H][W][3];
static int s_raster; static unsigned long s_dbg_tris, s_dbg_px, s_dbg_draw;
typedef struct { int w, h; N7GLenum type; uint16_t* px; size_t cap; } Tex;
#define TMAX 8192
static Tex s_t[TMAX]; static N7GLuint s_next = 1, s_bound;
static float s_mat[3][16]; static int s_mode;   /* 0 modelview 1 projection 2 texture */
static int s_tex2d, s_scissor, s_blend = 1, s_tca, s_ca = 1;
static N7GLenum s_bs = 0x0302, s_bd = 0x0303;
static int s_sc[4] = { 0, 0, W, H }, s_vp[4] = { 0, 0, W, H };
static float s_clear[4], s_col[4] = { 1, 1, 1, 1 };
static const uint8_t* s_vp_ptr; static int s_vp_stride; static const uint8_t* s_c_ptr; static int s_c_stride;
static const uint8_t* s_t_ptr; static int s_t_stride;
static int s_pot_only = -1;
static N7GLenum s_err;

static void ident(float* m) { memset(m, 0, 64); m[0] = m[5] = m[10] = m[15] = 1; }
static void* hostmem(size_t n) { void* p = mmap(0, n, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0); return p == MAP_FAILED ? 0 : p; }
__attribute__((constructor)) static void soft_init(void) { ident(s_mat[0]); ident(s_mat[1]); ident(s_mat[2]); }
int n7soft_frame(int raster) { s_raster = raster; return 0; }
void n7soft_shot(const char* path)
{
	/* turn the portrait panel back to landscape (Home button on the right) so the picture reads like the game */
	if (getenv("NANO7_SOFT_DEBUG")) { fprintf(stderr, "soft: draws %lu tris %lu px %lu\n", s_dbg_draw, s_dbg_tris, s_dbg_px); }
	FILE* f = fopen(path, "wb"); if (!f) return;
	fprintf(f, "P6\n%d %d\n255\n", H, W);
	for (int gy = 0; gy < W; gy++) for (int gx = 0; gx < H; gx++) {
		int px = W - 1 - gy, py = gx;
		for (int c = 0; c < 3; c++) { float v = fb[py][px][c]; fputc(v <= 0 ? 0 : v >= 1 ? 255 : (int)(v * 255 + 0.5f), f); }
	}
	fclose(f);
}

void n7raw_Viewport(N7GLint x, N7GLint y, N7GLsizei w, N7GLsizei h) { s_vp[0] = x; s_vp[1] = y; s_vp[2] = w; s_vp[3] = h; }
void n7raw_Scissor(N7GLint x, N7GLint y, N7GLsizei w, N7GLsizei h) { s_sc[0] = x; s_sc[1] = y; s_sc[2] = w; s_sc[3] = h; }
static void clip(int* x0, int* y0, int* x1, int* y1)   /* in GL coords (y up) */
{
	*x0 = 0; *y0 = 0; *x1 = W; *y1 = H;
	if (s_scissor) { if (s_sc[0] > *x0) *x0 = s_sc[0]; if (s_sc[1] > *y0) *y0 = s_sc[1];
		if (s_sc[0] + s_sc[2] < *x1) *x1 = s_sc[0] + s_sc[2]; if (s_sc[1] + s_sc[3] < *y1) *y1 = s_sc[1] + s_sc[3]; }
}
void n7raw_Clear(N7GLbitfield m)
{
	if (!s_raster) return;
	int x0, y0, x1, y1; clip(&x0, &y0, &x1, &y1);
	for (int y = y0; y < y1; y++) for (int x = x0; x < x1; x++) { float* p = fb[H - 1 - y][x]; p[0] = s_clear[0]; p[1] = s_clear[1]; p[2] = s_clear[2]; }
}
void n7raw_ClearColor(N7GLfloat r, N7GLfloat g, N7GLfloat b, N7GLfloat a) { s_clear[0] = r; s_clear[1] = g; s_clear[2] = b; s_clear[3] = a; }
static void cap(N7GLenum c, int on) { if (c == 0x0DE1) s_tex2d = on; else if (c == 0x0C11) s_scissor = on; else if (c == 0x0BE2) s_blend = on; }
void n7raw_Enable(N7GLenum c) { cap(c, 1); }
void n7raw_Disable(N7GLenum c) { cap(c, 0); }
void n7raw_BlendFunc(N7GLenum s, N7GLenum d) { s_bs = s; s_bd = d; }
void n7raw_TexParameteri(N7GLenum t, N7GLenum p, N7GLint v) {}
void n7raw_TexEnvi(N7GLenum t, N7GLenum p, N7GLint v) {}
void n7raw_PixelStorei(N7GLenum p, N7GLint v) {}
void n7raw_MatrixMode(N7GLenum m) { s_mode = m == 0x1701 ? 1 : m == 0x1702 ? 2 : 0; }
void n7raw_LoadIdentity(void) { ident(s_mat[s_mode]); }
void n7raw_LoadMatrixf(const N7GLfloat* m) { memcpy(s_mat[s_mode], m, 64); }
void n7raw_Scalef(N7GLfloat x, N7GLfloat y, N7GLfloat z) { float* m = s_mat[s_mode]; for (int i = 0; i < 4; i++) { m[i] *= x; m[4 + i] *= y; m[8 + i] *= z; } }
void n7raw_EnableClientState(N7GLenum a) { if (a == 0x8078) s_tca = 1; else if (a == 0x8076) s_ca = 1; }
void n7raw_DisableClientState(N7GLenum a) { if (a == 0x8078) s_tca = 0; else if (a == 0x8076) s_ca = 0; }
void n7raw_VertexPointer(N7GLint s, N7GLenum t, N7GLsizei st, const void* p) { s_vp_ptr = p; s_vp_stride = st; }
void n7raw_ColorPointer(N7GLint s, N7GLenum t, N7GLsizei st, const void* p) { s_c_ptr = p; s_c_stride = st; }
void n7raw_TexCoordPointer(N7GLint s, N7GLenum t, N7GLsizei st, const void* p) { s_t_ptr = p; s_t_stride = st; }
void n7raw_Color4f(N7GLfloat r, N7GLfloat g, N7GLfloat b, N7GLfloat a) { s_col[0] = r; s_col[1] = g; s_col[2] = b; s_col[3] = a; }
void n7raw_GetIntegerv(N7GLenum p, N7GLint* v) { if (p == 0x0D33) *v = 2048; else *v = 0; }   /* MAX_TEXTURE_SIZE */
N7GLenum n7raw_GetError(void) { N7GLenum e = s_err; s_err = 0; return e; }
const unsigned char* n7raw_GetString(N7GLenum n)
{
	if (n == 0x1F03) { if (s_pot_only < 0) s_pot_only = getenv("NANO7_POT") != 0; return (const unsigned char*)(s_pot_only ? "" : "GL_OES_texture_npot"); }
	return (const unsigned char*)(n == 0x1F01 ? "nano7 dry-run software GL" : n == 0x1F02 ? "OpenGL ES-CM 1.1" : "nano7");
}
void n7raw_BindTexture(N7GLenum t, N7GLuint n) { s_bound = n; }
void n7raw_GenTextures(N7GLsizei n, N7GLuint* t)
{
	for (int i = 0; i < n; i++) {
		N7GLuint k = 0;
		for (int j = 0; j < TMAX - 1 && !k; j++) { N7GLuint c = s_next; s_next = s_next % (TMAX - 1) + 1; if (!s_t[c].px && !s_t[c].w) k = c; }
		if (k) s_t[k].w = -1;   /* reserved */
		t[i] = k;
	}
}
void n7raw_DeleteTextures(N7GLsizei n, const N7GLuint* t)
{
	for (int i = 0; i < n; i++) { Tex* x = t[i] && t[i] < TMAX ? &s_t[t[i]] : 0; if (!x) continue; if (x->px) munmap(x->px, x->cap); memset(x, 0, sizeof *x); }
}
static int ispot(int v) { return v > 0 && !(v & (v - 1)); }
void n7raw_TexImage2D(N7GLenum tg, N7GLint lvl, N7GLint ifmt, N7GLsizei w, N7GLsizei h, N7GLint b, N7GLenum fmt, N7GLenum type, const void* d)
{
	if (s_pot_only < 0) s_pot_only = getenv("NANO7_POT") != 0;
	if (s_pot_only && (!ispot(w) || !ispot(h))) { s_err = 0x0501; return; }
	if (!s_bound || s_bound >= TMAX) return;
	Tex* x = &s_t[s_bound];
	if (type != 0x8363 && type != 0x8034 && type != 0x8033) { s_err = 0x0500; return; }   /* only 16-bit textures here */
	size_t n = (size_t)w * h * 2;
	if (x->px) munmap(x->px, x->cap);
	x->px = hostmem(n); x->cap = n; x->w = w; x->h = h; x->type = type;
	if (d && x->px) memcpy(x->px, d, n);
}
static void texel(const Tex* t, float u, float v, float* o)
{
	int x = (int)floorf(u * t->w), y = (int)floorf(v * t->h);
	if (x < 0) x = 0; if (x >= t->w) x = t->w - 1; if (y < 0) y = 0; if (y >= t->h) y = t->h - 1;
	uint16_t p = t->px[(size_t)y * t->w + x];
	if (t->type == 0x8363) { o[0] = (p >> 11) / 31.f; o[1] = ((p >> 5) & 63) / 63.f; o[2] = (p & 31) / 31.f; o[3] = 1; }
	else if (t->type == 0x8034) { o[0] = (p >> 11) / 31.f; o[1] = ((p >> 6) & 31) / 31.f; o[2] = ((p >> 1) & 31) / 31.f; o[3] = p & 1; }
	else { o[0] = (p >> 12) / 15.f; o[1] = ((p >> 8) & 15) / 15.f; o[2] = ((p >> 4) & 15) / 15.f; o[3] = (p & 15) / 15.f; }
}
static float factor(N7GLenum f, const float* s, const float* d, int c)
{
	switch (f) {
	case 0: return 0; case 1: return 1;
	case 0x0300: return s[c]; case 0x0301: return 1 - s[c];
	case 0x0302: return s[3]; case 0x0303: return 1 - s[3];
	case 0x0306: return d[c]; case 0x0307: return 1 - d[c];
	default: return 1;
	}
}
typedef struct { float x, y, u, v, c[4]; } V;
static void xform(int i, V* o)
{
	const float* p = (const float*)(s_vp_ptr + (size_t)i * s_vp_stride);
	const float* P = s_mat[1]; const float* M = s_mat[0];
	float mx = M[0] * p[0] + M[4] * p[1] + M[12], my = M[1] * p[0] + M[5] * p[1] + M[13];
	float nx = P[0] * mx + P[4] * my + P[12], ny = P[1] * mx + P[5] * my + P[13];
	o->x = s_vp[0] + (nx + 1) * 0.5f * s_vp[2]; o->y = s_vp[1] + (ny + 1) * 0.5f * s_vp[3];
	if (s_tca && s_t_ptr) { const float* t = (const float*)(s_t_ptr + (size_t)i * s_t_stride); const float* T = s_mat[2];
		o->u = T[0] * t[0] + T[4] * t[1] + T[12]; o->v = T[1] * t[0] + T[5] * t[1] + T[13]; } else o->u = o->v = 0;
	if (s_ca && s_c_ptr) { const uint8_t* c = s_c_ptr + (size_t)i * s_c_stride; for (int k = 0; k < 4; k++) o->c[k] = c[k] / 255.f; }
	else memcpy(o->c, s_col, 16);
}
static void tri(const V* a, const V* b, const V* c)
{
	float area = (b->x - a->x) * (c->y - a->y) - (c->x - a->x) * (b->y - a->y);
	s_dbg_tris++;
	if (fabsf(area) < 1e-6f) return;
	int x0, y0, x1, y1; clip(&x0, &y0, &x1, &y1);
	int bx0 = (int)floorf(fminf(a->x, fminf(b->x, c->x))), bx1 = (int)ceilf(fmaxf(a->x, fmaxf(b->x, c->x)));
	int by0 = (int)floorf(fminf(a->y, fminf(b->y, c->y))), by1 = (int)ceilf(fmaxf(a->y, fmaxf(b->y, c->y)));
	if (bx0 > x0) x0 = bx0; if (by0 > y0) y0 = by0; if (bx1 < x1) x1 = bx1; if (by1 < y1) y1 = by1;
	const Tex* t = s_tex2d && s_bound < TMAX && s_t[s_bound].px ? &s_t[s_bound] : 0;
	for (int y = y0; y < y1; y++) for (int x = x0; x < x1; x++) {
		float px = x + 0.5f, py = y + 0.5f;
		float w0 = ((b->x - px) * (c->y - py) - (c->x - px) * (b->y - py)) / area;
		float w1 = ((c->x - px) * (a->y - py) - (a->x - px) * (c->y - py)) / area;
		float w2 = 1 - w0 - w1;
		if (w0 < 0 || w1 < 0 || w2 < 0) continue;
		s_dbg_px++;
		float s[4];
		for (int k = 0; k < 4; k++) s[k] = w0 * a->c[k] + w1 * b->c[k] + w2 * c->c[k];
		if (t) { float tx[4]; texel(t, w0 * a->u + w1 * b->u + w2 * c->u, w0 * a->v + w1 * b->v + w2 * c->v, tx); for (int k = 0; k < 4; k++) s[k] *= tx[k]; }
		if (getenv("NANO7_SOFT_DEBUG") && s_dbg_px % 50000 == 1) fprintf(stderr, "px c=%.2f,%.2f,%.2f,%.2f tex=%d bs=%x bd=%x blend=%d uv=%.3f,%.3f\n", s[0], s[1], s[2], s[3], t ? t->type : 0, s_bs, s_bd, s_blend, a->u, a->v);
		float* d = fb[H - 1 - y][x]; float dd[4] = { d[0], d[1], d[2], 1 };
		for (int k = 0; k < 3; k++) d[k] = s_blend ? s[k] * factor(s_bs, s, dd, k) + dd[k] * factor(s_bd, s, dd, k) : s[k];
	}
}
void n7raw_DrawArrays(N7GLenum mode, N7GLint first, N7GLsizei count)
{
	if (s_raster) s_dbg_draw++;
	if (!s_raster || !s_vp_ptr || count < 3) return;
	V a, b, c;
	if (mode == 4) for (int i = first; i + 2 < first + count; i += 3) { xform(i, &a); xform(i + 1, &b); xform(i + 2, &c); tri(&a, &b, &c); }
	else if (mode == 5) for (int i = first; i + 2 < first + count; i++) { xform(i, &a); xform(i + 1, &b); xform(i + 2, &c); tri(&a, &b, &c); }
	else if (mode == 6) { xform(first, &a); for (int i = first + 1; i + 1 < first + count; i++) { xform(i, &b); xform(i + 1, &c); tri(&a, &b, &c); } }
}
