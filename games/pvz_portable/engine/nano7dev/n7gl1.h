/* nano7 PvZ: the OpenGL ES 1.1 calls the device renderer uses. n7glcommon.c keeps texture/triangle counters and the
 * per-texture UV scale, then calls the backend (n7gl1_hb.c = the iPod's GL, n7gl1_soft.c = software for the dry run). */
#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef unsigned int N7GLenum, N7GLuint, N7GLbitfield; typedef int N7GLint, N7GLsizei; typedef float N7GLfloat; typedef unsigned char N7GLboolean;
#define N7_GLES1_LIST(X) \
 X(void, Viewport, (N7GLint x, N7GLint y, N7GLsizei w, N7GLsizei h), (x, y, w, h)) \
 X(void, Scissor, (N7GLint x, N7GLint y, N7GLsizei w, N7GLsizei h), (x, y, w, h)) \
 X(void, Clear, (N7GLbitfield m), (m)) \
 X(void, ClearColor, (N7GLfloat r, N7GLfloat g, N7GLfloat b, N7GLfloat a), (r, g, b, a)) \
 X(void, Enable, (N7GLenum c), (c)) \
 X(void, Disable, (N7GLenum c), (c)) \
 X(void, BlendFunc, (N7GLenum s, N7GLenum d), (s, d)) \
 X(void, TexParameteri, (N7GLenum t, N7GLenum p, N7GLint v), (t, p, v)) \
 X(void, TexEnvi, (N7GLenum t, N7GLenum p, N7GLint v), (t, p, v)) \
 X(void, PixelStorei, (N7GLenum p, N7GLint v), (p, v)) \
 X(void, MatrixMode, (N7GLenum m), (m)) \
 X(void, LoadIdentity, (void), ()) \
 X(void, LoadMatrixf, (const N7GLfloat* m), (m)) \
 X(void, Scalef, (N7GLfloat x, N7GLfloat y, N7GLfloat z), (x, y, z)) \
 X(void, EnableClientState, (N7GLenum a), (a)) \
 X(void, DisableClientState, (N7GLenum a), (a)) \
 X(void, VertexPointer, (N7GLint s, N7GLenum t, N7GLsizei st, const void* p), (s, t, st, p)) \
 X(void, ColorPointer, (N7GLint s, N7GLenum t, N7GLsizei st, const void* p), (s, t, st, p)) \
 X(void, TexCoordPointer, (N7GLint s, N7GLenum t, N7GLsizei st, const void* p), (s, t, st, p)) \
 X(void, Color4f, (N7GLfloat r, N7GLfloat g, N7GLfloat b, N7GLfloat a), (r, g, b, a)) \
 X(void, GetIntegerv, (N7GLenum p, N7GLint* v), (p, v)) \
 X(N7GLenum, GetError, (void), ()) \
 X(const unsigned char*, GetString, (N7GLenum n), (n))
#define N7_DECL(r, n, a, c) r n7gl1_##n a; r n7raw_##n a;
N7_GLES1_LIST(N7_DECL)
#undef N7_DECL
/* tracked by n7glcommon.c */
void n7gl1_BindTexture(N7GLenum t, N7GLuint n);
void n7raw_BindTexture(N7GLenum t, N7GLuint n);
void n7gl1_GenTextures(N7GLsizei n, N7GLuint* t);
void n7gl1_DeleteTextures(N7GLsizei n, const N7GLuint* t);
void n7gl1_TexImage2D(N7GLenum tg, N7GLint lvl, N7GLint ifmt, N7GLsizei w, N7GLsizei h, N7GLint b, N7GLenum fmt, N7GLenum type, const void* d);
void n7gl1_DrawArrays(N7GLenum mode, N7GLint first, N7GLsizei count);
void n7raw_GenTextures(N7GLsizei n, N7GLuint* t);
void n7raw_DeleteTextures(N7GLsizei n, const N7GLuint* t);
void n7raw_TexImage2D(N7GLenum tg, N7GLint lvl, N7GLint ifmt, N7GLsizei w, N7GLsizei h, N7GLint b, N7GLenum fmt, N7GLenum type, const void* d);
void n7raw_DrawArrays(N7GLenum mode, N7GLint first, N7GLsizei count);
/* per-texture UV scale (a texture padded past its picture), applied through the texture matrix */
void n7_texscale_set(N7GLuint name, float us, float vs);
void n7_texscale_apply(N7GLuint name);
void n7_gl_frame_end(void);     /* closes the frame's triangle/draw counters */
/* triangle budget (see n7glcommon.c): classes 0 effects, 1 shadows, 2 small parts, 3 keep */
void n7_tri_budget_set(uint32_t b);  /* 0 = no limit */
uint32_t n7_tri_budget(void);
void n7_texclass_set(N7GLuint name, int cls);
int n7_tri_admit(N7GLuint tex, uint32_t tris);   /* 1 draw it, 0 trimmed */
void* nano7_gl_proc(const char* name);
#ifdef __cplusplus
}
#endif
