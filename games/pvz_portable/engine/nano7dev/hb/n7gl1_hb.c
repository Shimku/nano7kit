/* nano7 PvZ device layer: the n7gl1 backend on the iPod's OpenGL ES 1.1 (nano7kit SDK gl.h). Every texture name is
 * recorded for gl_left.c, so the next nano7kit port can delete what PvZ leaves in the shared GL context. */
#include "gl.h"
#include "n7gl1.h"
#include "gl_left.h"
#define N7_RAW(r, n, a, c) r n7raw_##n a { return (r)gl##n c; }
N7_GLES1_LIST(N7_RAW)
#undef N7_RAW
void n7raw_BindTexture(N7GLenum t, N7GLuint n) { glBindTexture(t, n); }
void n7raw_GenTextures(N7GLsizei n, N7GLuint* t) { glGenTextures(n, t); for (int i = 0; i < n; i++) gll_note(t[i]); }
void n7raw_DeleteTextures(N7GLsizei n, const N7GLuint* t) { glDeleteTextures(n, t); }
void n7raw_TexImage2D(N7GLenum tg, N7GLint lvl, N7GLint ifmt, N7GLsizei w, N7GLsizei h, N7GLint b, N7GLenum fmt, N7GLenum type, const void* d)
{ glTexImage2D(tg, lvl, ifmt, w, h, b, fmt, type, d); }
void n7raw_DrawArrays(N7GLenum mode, N7GLint first, N7GLsizei count) { glDrawArrays(mode, first, count); }
