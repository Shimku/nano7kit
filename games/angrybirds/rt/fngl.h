/* fngl.h: GLES 1.1 front end for the Android Angry Birds .so (nano7kit, MIT).
 * The engine's GL imports land on ab_gl* (so_load remaps glFoo -> ab_glFoo so the
 * nano driver can keep the unprefixed names in be_nano.c). Transformed, clipped
 * triangles go to a backend. */
#ifndef FN_GL_H
#define FN_GL_H
#include <stdint.h>
typedef unsigned int GLenum;
typedef unsigned char GLboolean;
typedef unsigned int GLbitfield;
typedef int GLint;
typedef int GLsizei;
typedef unsigned int GLuint;
typedef float GLfloat;
typedef float GLclampf;
typedef void GLvoid;
typedef unsigned char GLubyte;
typedef struct { float x, y, z, w, u, v; uint8_t c[4]; } be_vtx;
typedef struct {
    int tex;
    int blend; uint32_t sfactor, dfactor;
    int depth_test, depth_write; uint32_t depth_func;
    int alpha_test; float alpha_ref;
} be_state;
void be_frame_begin(void);
void be_clear(int color, int depth, const float rgba[4], float d);
void be_tris(const be_state *s, const be_vtx *v, int nverts);
void be_frame_end(void);
int be_tex_create(void);
void be_tex_delete(int h);
void be_tex_image(int h, int level, int w, int ht, uint32_t format, uint32_t type, const void *pixels);
void be_tex_compressed(int h, int level, uint32_t fmt, int w, int ht, const void *data, uint32_t size);
void be_tex_param(int h, uint32_t pname, uint32_t value);
extern float fe_scale, fe_scale_y, fe_off_x, fe_off_y, fe_fb_h;
extern int fe_view_h;
extern int fe_stat_tris, fe_stat_draws, fe_stat_tex_bytes, fe_stat_tex_peak;
extern int fe_tex_shift, fe_tex_min_side, fe_tex_big_side, fe_tex_big_shift, fe_stat_tex_count, fe_stat_tex_count_peak;
extern int fe_atlas_max_side, fe_stat_atlased, fe_stat_atlas_pages;
extern int fe_pending_tex_shift;
void fngl_reset(void);
void fe_reset_matrices(void);
void fe_note_texfile(const char *path);

void ab_glMatrixMode(GLenum mode);
void ab_glLoadIdentity(void);
void ab_glLoadMatrixf(const GLfloat *m);
void ab_glMultMatrixf(const GLfloat *m);
void ab_glPushMatrix(void);
void ab_glPopMatrix(void);
void ab_glViewport(GLint x, GLint y, GLsizei w, GLsizei h);
void ab_glClearColor(GLclampf r, GLclampf g, GLclampf b, GLclampf a);
void ab_glClear(GLbitfield mask);
void ab_glColor4f(GLfloat r, GLfloat g, GLfloat b, GLfloat a);
void ab_glColorMask(GLboolean r, GLboolean g, GLboolean b, GLboolean a);
void ab_glEnable(GLenum cap);
void ab_glDisable(GLenum cap);
void ab_glBlendFunc(GLenum s, GLenum d);
void ab_glDepthMask(GLboolean flag);
void ab_glDepthFunc(GLenum func);
void ab_glEnableClientState(GLenum a);
void ab_glDisableClientState(GLenum a);
void ab_glVertexPointer(GLint size, GLenum type, GLsizei stride, const GLvoid *ptr);
void ab_glTexCoordPointer(GLint size, GLenum type, GLsizei stride, const GLvoid *ptr);
void ab_glColorPointer(GLint size, GLenum type, GLsizei stride, const GLvoid *ptr);
void ab_glNormalPointer(GLenum type, GLsizei stride, const GLvoid *ptr);
void ab_glGenBuffers(GLsizei n, GLuint *buffers);
void ab_glBindBuffer(GLenum target, GLuint buffer);
void ab_glBufferData(GLenum target, GLsizei size, const GLvoid *data, GLenum usage);
void ab_glDeleteBuffers(GLsizei n, const GLuint *buffers);
void ab_glGenTextures(GLsizei n, GLuint *textures);
void ab_glBindTexture(GLenum target, GLuint texture);
void ab_glDeleteTextures(GLsizei n, const GLuint *textures);
void ab_glTexParameteri(GLenum target, GLenum pname, GLint param);
void ab_glTexImage2D(GLenum target, GLint level, GLint internalformat, GLsizei width, GLsizei height,
                     GLint border, GLenum format, GLenum type, const GLvoid *pixels);
void ab_glTexSubImage2D(GLenum target, GLint level, GLint xoff, GLint yoff, GLsizei width, GLsizei height,
                        GLenum format, GLenum type, const GLvoid *pixels);
void ab_glCompressedTexImage2D(GLenum target, GLint level, GLenum internalformat, GLsizei width, GLsizei height,
                               GLint border, GLsizei imageSize, const GLvoid *data);
void ab_glDrawArrays(GLenum mode, GLint first, GLsizei count);
void ab_glDrawElements(GLenum mode, GLsizei count, GLenum type, const GLvoid *indices);
void ab_glGenFramebuffersOES(GLsizei n, GLuint *f);
void ab_glGenRenderbuffersOES(GLsizei n, GLuint *r);
void ab_glBindFramebufferOES(GLenum t, GLuint f);
void ab_glBindRenderbufferOES(GLenum t, GLuint r);
GLenum ab_glCheckFramebufferStatusOES(GLenum t);
void ab_glDeleteFramebuffersOES(GLsizei n, const GLuint *f);
void ab_glDeleteRenderbuffersOES(GLsizei n, const GLuint *r);
void ab_glFramebufferRenderbufferOES(GLenum t, GLenum att, GLenum rt, GLuint r);
void ab_glFramebufferTexture2DOES(GLenum t, GLenum att, GLenum tt, GLuint tex, GLint level);
void ab_glRenderbufferStorageOES(GLenum t, GLenum fmt, GLsizei w, GLsizei h);
void ab_glActiveTexture(GLenum t);
void ab_glClientActiveTexture(GLenum t);
void ab_glPixelStorei(GLenum pname, GLint param);
void ab_glScissor(GLint x, GLint y, GLsizei w, GLsizei h);
void ab_glLineWidth(GLfloat w);
void ab_glFrontFace(GLenum mode);
void ab_glLightfv(GLenum light, GLenum pname, const GLfloat *p);
void ab_glMaterialfv(GLenum face, GLenum pname, const GLfloat *p);
void ab_glTexEnvi(GLenum target, GLenum pname, GLint param);
void ab_glFinish(void);
void ab_glGetIntegerv(GLenum pname, GLint *params);
const GLubyte *ab_glGetString(GLenum name);
#endif
