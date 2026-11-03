/* nano7 PvZ: GL bookkeeping shared by the iPod and the dry run (texture bytes, triangles per frame, UV scale table)
 * and the GLES2 name table the engine's loader asks for (GLES1 calls map through, shader calls are no-ops). */
#include <string.h>
#include "n7gl1.h"
#include "n7dev.h"
#define GL_TEXTURE_2D 0x0DE1
#define GL_TEXTURE 0x1702
#define GL_MODELVIEW 0x1700
#define N7_TEXMAX 4096
#define N7_CLS_KEEP 4          /* open-addressed: name -> bytes, scale (only textures this port made) */
typedef struct { N7GLuint name; uint32_t bytes; uint16_t us, vs; uint8_t cls; } TexEnt;   /* scale in 1/32768; cls = trim class */
static TexEnt s_tex[N7_TEXMAX];
static N7GLuint s_bound, s_bound_draw;
static int s_matrix_identity = 1;
static TexEnt* find(N7GLuint n, int add)
{
	if (!n) return 0;
	uint32_t h = (n * 2654435761u) & (N7_TEXMAX - 1);
	for (int i = 0; i < N7_TEXMAX; i++, h = (h + 1) & (N7_TEXMAX - 1)) {
		if (s_tex[h].name == n) return &s_tex[h];
		if (s_tex[h].name == 0) { if (!add) return 0; s_tex[h].name = n; s_tex[h].us = s_tex[h].vs = 32768; s_tex[h].bytes = 0; s_tex[h].cls = N7_CLS_KEEP; return &s_tex[h]; }
	}
	return 0;
}
static void drop(N7GLuint n)   /* backward-shift delete keeps probing chains intact */
{
	uint32_t h = (n * 2654435761u) & (N7_TEXMAX - 1);
	for (int i = 0; i < N7_TEXMAX && s_tex[h].name; i++, h = (h + 1) & (N7_TEXMAX - 1)) {
		if (s_tex[h].name != n) continue;
		uint32_t j = h;
		for (;;) {
			s_tex[j].name = 0;
			uint32_t k = j;
			for (;;) {
				k = (k + 1) & (N7_TEXMAX - 1);
				if (!s_tex[k].name) return;
				uint32_t home = (s_tex[k].name * 2654435761u) & (N7_TEXMAX - 1);
				if (j <= k ? (j < home && home <= k) : (j < home || home <= k)) continue;
				s_tex[j] = s_tex[k]; j = k; break;
			}
		}
	}
}
void n7_texscale_set(N7GLuint name, float us, float vs)
{
	TexEnt* e = find(name, 1); if (!e) return;
	e->us = (uint16_t)(us * 32768.0f + 0.5f); e->vs = (uint16_t)(vs * 32768.0f + 0.5f);
}
void n7_texscale_apply(N7GLuint name)
{
	TexEnt* e = find(name, 0);
	int ident = !e || (e->us == 32768 && e->vs == 32768);
	if (ident && s_matrix_identity) return;
	n7raw_MatrixMode(GL_TEXTURE); n7raw_LoadIdentity();
	if (!ident) n7raw_Scalef(e->us / 32768.0f, e->vs / 32768.0f, 1.0f);
	n7raw_MatrixMode(GL_MODELVIEW);
	s_matrix_identity = ident;
}
static int bpp(N7GLenum fmt, N7GLenum type)
{
	if (type == 0x8033 || type == 0x8034 || type == 0x8363) return 2;   /* 4444 5551 565 */
	if (fmt == 0x1908) return 4; if (fmt == 0x1907) return 3; return 1;
}
void n7gl1_GenTextures(N7GLsizei n, N7GLuint* t) { n7raw_GenTextures(n, t); }
void n7gl1_DeleteTextures(N7GLsizei n, const N7GLuint* t)
{
	for (int i = 0; i < n; i++) {
		TexEnt* e = find(t[i], 0);
		if (e) { n7_stats.tex_bytes -= e->bytes; n7_stats.tex_count--; drop(t[i]); }
	}
	n7raw_DeleteTextures(n, t);
}
void n7gl1_TexImage2D(N7GLenum tg, N7GLint lvl, N7GLint ifmt, N7GLsizei w, N7GLsizei h, N7GLint b, N7GLenum fmt, N7GLenum type, const void* d)
{
	n7raw_TexImage2D(tg, lvl, ifmt, w, h, b, fmt, type, d);
	if (lvl) return;
	TexEnt* e = find(s_bound, 1);
	if (!e) return;
	if (!e->bytes) { n7_stats.tex_count++; if (n7_stats.tex_count > n7_stats.tex_count_peak) n7_stats.tex_count_peak = n7_stats.tex_count; }
	n7_stats.tex_bytes -= e->bytes;
	/* a 4-byte upload is stored as 16-bit by this GPU (gl.c notes RGBA8888 -> RGBA4444), but count what was asked */
	e->bytes = (uint32_t)w * (uint32_t)h * (uint32_t)bpp(fmt, type);
	n7_stats.tex_bytes += e->bytes;
	if (n7_stats.tex_bytes > n7_stats.tex_peak) n7_stats.tex_peak = n7_stats.tex_bytes;
}
static uint32_t s_tris, s_draws;
static void budget_frame_end(void);
#ifdef N7_HOST_ENV   /* dry run: which images the triangles of the busiest frame come from */
#include <stdio.h>
#include <stdlib.h>
static const char* s_names[8192]; static uint32_t s_ftri[8192], s_ptri[8192], s_peakf;
static uint32_t s_fneed[8192], s_pneed[8192], s_pneed_drawn[8192], s_peakneed, s_fneed_tot;   /* by what is asked for */
static void tally_need(N7GLuint tex, uint32_t t) { uint32_t b = tex < 8192 ? tex : 0; s_fneed[b] += t; s_fneed_tot += t; }
/* dry run only. The names live outside the arena (a fixed pool): the baked pictures make thousands of textures, and
 * strdup'd names would sit in the arena the iPod build does not have */
static char s_namepool[8192][40];
void n7_texname_note(N7GLuint n, const char* path)
{
	if (n >= 8192) return;
	if (!path) { s_names[n] = 0; return; }
	snprintf(s_namepool[n], sizeof s_namepool[n], "%s", path); s_names[n] = s_namepool[n];
}
static void tally(uint32_t t) { uint32_t b = s_bound_draw < 8192 ? s_bound_draw : 0; s_ftri[b] += t; }
static void tally_end(void)
{
	if (s_tris > s_peakf) { s_peakf = s_tris; memcpy(s_ptri, s_ftri, sizeof s_ptri); }
	if (s_fneed_tot > s_peakneed) { s_peakneed = s_fneed_tot; memcpy(s_pneed, s_fneed, sizeof s_pneed); memcpy(s_pneed_drawn, s_ftri, sizeof s_pneed_drawn); }
	memset(s_ftri, 0, sizeof s_ftri); memset(s_fneed, 0, sizeof s_fneed); s_fneed_tot = 0;
}
void n7_tri_need_reset(void) { s_peakneed = 0; }
void n7_tri_dump(void)
{
	if (!getenv("NANO7_TRIDUMP")) return;
	FILE* f = fopen(getenv("NANO7_TRIDUMP"), "w"); if (!f) return;
	fprintf(f, "peak %u\n", s_peakf);
	for (int i = 0; i < 8192; i++) if (s_ptri[i]) fprintf(f, "%u\t%s\n", s_ptri[i], s_names[i] ? s_names[i] : "(untextured)");
	fprintf(f, "needpeak %u\n", s_peakneed);   /* the frame that asked for the most: needed, drawn, image */
	for (int i = 0; i < 8192; i++) if (s_pneed[i]) fprintf(f, "need\t%u\t%u\t%s\n", s_pneed[i], s_pneed_drawn[i], s_names[i] ? s_names[i] : "(untextured)");
	fclose(f);
}
#else
#define tally(t) ((void)0)
#define tally_end() ((void)0)
#define tally_need(x, t) ((void)0)
#endif
void n7gl1_DrawArrays(N7GLenum mode, N7GLint first, N7GLsizei count)
{
	if (count <= 0) return;
	n7raw_DrawArrays(mode, first, count);
	s_draws++;
	uint32_t t = mode == 4 ? (uint32_t)count / 3 : (count >= 3 ? (uint32_t)count - 2 : 0);   /* GL_TRIANGLES, strip/fan */
	s_tris += t; tally(t);
}
void n7_gl_frame_end(void)
{
	n7_stats.tris_frame = s_tris; n7_stats.draws_frame = s_draws;
	tally_end();
	budget_frame_end();
	if (s_tris > n7_stats.tris_peak) n7_stats.tris_peak = s_tris;
	if (s_draws > n7_stats.draws_peak) n7_stats.draws_peak = s_draws;
	s_tris = s_draws = 0;
}
#define N7_PASS(r, n, a, c) r n7gl1_##n a { return n7raw_##n c; }
N7_GLES1_LIST(N7_PASS)
#undef N7_PASS
/* BindTexture through the engine's loader also records the bound name for TexImage2D accounting */
void n7gl1_BindTexture(N7GLenum t, N7GLuint n) { s_bound = n; s_bound_draw = n; n7raw_BindTexture(t, n); }

/* ---- the GLES2 entry points the engine's glad loader resolves ---- */
static void* nop(void) { return 0; }
static void f_getiv(N7GLuint o, N7GLenum n, N7GLint* p) { if (p) *p = (n == 0x8B81 || n == 0x8B82 || n == 0x8B83) ? 1 : 0; }  /* compile/link ok */
static N7GLuint s_next = 1;
static N7GLuint f_create(void) { return s_next++; }
static N7GLuint f_create_shader(N7GLenum t) { (void)t; return s_next++; }
static void f_genbuf(N7GLsizei n, N7GLuint* p) { for (int i = 0; i < n; i++) p[i] = s_next++; }
static N7GLint f_loc(N7GLuint p, const char* n) { (void)p; (void)n; return 1; }
static unsigned char f_true(N7GLuint x) { (void)x; return 1; }
static N7GLenum f_fbstatus(N7GLenum t) { (void)t; return 0; }     /* no FBO: RecoverBits is off on the device anyway */
static void f_infolog(N7GLuint o, N7GLsizei m, N7GLsizei* l, char* s) { (void)o; if (l) *l = 0; if (s && m > 0) s[0] = 0; }
static void f_glDeleteTextures(N7GLsizei n, const N7GLuint* t) { n7gl1_DeleteTextures(n, t); }
static void f_glTexImage2D(N7GLenum tg, N7GLint lvl, N7GLint ifmt, N7GLsizei w, N7GLsizei h, N7GLint b, N7GLenum fmt, N7GLenum type, const void* d)
{ n7gl1_TexImage2D(tg, lvl, ifmt, w, h, b, fmt, type, d); }
static void f_glActiveTexture(N7GLenum u) { (void)u; }
/* the engine's glad loader only fills its pointers for an "OpenGL ES 2.0" context; the real version string is logged by
 * the device renderer's start-up (N7ProbeNpot), which asks n7gl1_GetString directly */
static const unsigned char* f_getstring(N7GLenum n)
{
	if (n == 0x1F02) return (const unsigned char*)"OpenGL ES 2.0 (nano7 GLES1 bridge)";
	const unsigned char* r = n7gl1_GetString(n);
	return r ? r : (const unsigned char*)"";
}
struct E { const char* n; void* f; };
static const struct E tab[] = {
	{ "glGetString", (void*)f_getstring }, { "glGetIntegerv", (void*)n7gl1_GetIntegerv }, { "glGetError", (void*)n7gl1_GetError },
	{ "glViewport", (void*)n7gl1_Viewport }, { "glClear", (void*)n7gl1_Clear }, { "glClearColor", (void*)n7gl1_ClearColor },
	{ "glEnable", (void*)n7gl1_Enable }, { "glDisable", (void*)n7gl1_Disable }, { "glBlendFunc", (void*)n7gl1_BlendFunc },
	{ "glBindTexture", (void*)n7gl1_BindTexture }, { "glGenTextures", (void*)n7gl1_GenTextures }, { "glDeleteTextures", (void*)f_glDeleteTextures },
	{ "glTexImage2D", (void*)f_glTexImage2D }, { "glTexParameteri", (void*)n7gl1_TexParameteri }, { "glPixelStorei", (void*)n7gl1_PixelStorei },
	{ "glActiveTexture", (void*)f_glActiveTexture }, { "glDrawArrays", (void*)n7gl1_DrawArrays },
	{ "glGetShaderiv", (void*)f_getiv }, { "glGetProgramiv", (void*)f_getiv }, { "glCreateProgram", (void*)f_create },
	{ "glCreateShader", (void*)f_create_shader }, { "glGenBuffers", (void*)f_genbuf }, { "glGenFramebuffers", (void*)f_genbuf },
	{ "glGenRenderbuffers", (void*)f_genbuf }, { "glGetUniformLocation", (void*)f_loc }, { "glGetAttribLocation", (void*)f_loc },
	{ "glIsTexture", (void*)f_true }, { "glIsProgram", (void*)f_true }, { "glIsShader", (void*)f_true },
	{ "glCheckFramebufferStatus", (void*)f_fbstatus }, { "glGetShaderInfoLog", (void*)f_infolog }, { "glGetProgramInfoLog", (void*)f_infolog },
	{ 0, 0 } };
void* nano7_gl_proc(const char* name)
{
	for (const struct E* e = tab; e->n; e++) if (!strcmp(e->n, name)) return e->f;
	return (void*)nop;   /* shaders, uniforms, VBOs, FBOs: nothing to do on GLES1 */
}

/* ---- triangle budget -----------------------------------------------------------------------------------------
 * The nano's GL driver needs memory for every triangle of a frame and the iPod reboots when it cannot get it, so each
 * frame is trimmed to a budget (n7main.cpp). Every texture has a trim class, set when it is made, from the image:
 * 0 particles and effects, 1 shadows, 2 small animation parts (zombie hands, hair), 3 other animation parts (zombies),
 * 4 protected: the sun, coins, every picture under images/ (seed bank, projectiles, cursor, UI), fonts, the lawn, the
 * baked plants and mowers (n7bake.cpp) and untextured fills. Whole classes are dropped lowest first, planned from
 * the frame before; the plan steps up at once and only steps back down after 90 frames in a row that would fit (an earlier build
 * re-planned every frame, so plants flickered). The hard cap keeps room for the protected class (what it needed the
 * frame before), so a busy frame loses zombie parts, never the sun (an earlier build cut whatever was drawn last: the sun). */
#define N7_CLS_N 5
static uint32_t s_budget = 0xFFFFFFFFu, s_need_cls[N7_CLS_N], s_drawn, s_drop_below;   /* classes < s_drop_below are dropped */
static uint32_t s_skipped, s_capped, s_prot_prev, s_prot_now, s_calm;
void n7_tri_budget_set(uint32_t b) { s_budget = b ? b : 0xFFFFFFFFu; }
uint32_t n7_tri_budget(void) { return s_budget; }
void n7_texclass_set(N7GLuint name, int cls) { TexEnt* e = find(name, 1); if (e) e->cls = (uint8_t)cls; }
int n7_tri_admit(N7GLuint tex, uint32_t tris)
{
	TexEnt* e = tex ? find(tex, 0) : 0;
	int c = e ? e->cls : N7_CLS_KEEP;
	if (c >= N7_CLS_N) c = N7_CLS_N - 1;
	s_need_cls[c] += tris;
	tally_need(tex, tris);
	if (c == N7_CLS_N - 1) {   /* protected: only the budget itself stops it */
		s_prot_now += tris;
		if (s_drawn + tris > s_budget) { s_capped += tris; n7_stats.prot_cut += tris; return 0; }
		s_drawn += tris;
		return 1;
	}
	uint32_t want = s_prot_prev + s_prot_prev / 8 + 12;   /* last frame's protected need and a little growth */
	uint32_t reserve = want > s_prot_now ? want - s_prot_now : 0;
	if ((uint32_t)c < s_drop_below || s_drawn + tris + reserve > s_budget) {
		if ((uint32_t)c < s_drop_below) s_skipped += tris; else s_capped += tris;
		return 0;
	}
	s_drawn += tris;
	return 1;
}
static void budget_frame_end(void)
{
	uint32_t need = 0;
	for (int i = 0; i < N7_CLS_N; i++) need += s_need_cls[i];
	n7_stats.tris_need = need; n7_stats.tris_drawn = s_drawn; n7_stats.tris_skipped = s_skipped; n7_stats.tris_capped = s_capped;
	if (need > n7_stats.tris_need_peak) n7_stats.tris_need_peak = need;
	if (s_drawn > n7_stats.tris_drawn_peak) n7_stats.tris_drawn_peak = s_drawn;
	if (s_capped) n7_stats.frames_capped++;
	if (s_skipped) n7_stats.frames_trimmed++;
	uint32_t d = 0, rest = need;
	while (d < N7_CLS_N - 1 && rest > s_budget) rest -= s_need_cls[d++];
	if (d >= s_drop_below) { s_drop_below = d; s_calm = 0; }   /* up at once */
	else if (++s_calm >= 90) { s_drop_below--; s_calm = 0; }   /* down one class after 90 calm frames */
	n7_stats.drop_below = s_drop_below;
	s_prot_prev = s_prot_now;
	memset(s_need_cls, 0, sizeof s_need_cls); s_drawn = s_skipped = s_capped = s_prot_now = 0;
}
