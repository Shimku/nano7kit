/* nano7kit measurement: a do-nothing OpenGL ES 2.0 that only keeps books on texture uploads. */
#include <string.h>
#include <stdint.h>
#include <stdio.h>
typedef unsigned int GLenum, GLuint; typedef int GLint, GLsizei; typedef unsigned char GLubyte;
static GLuint next_id = 1, bound[32], unit;
#define MAXT 65536
static uint32_t tbytes[MAXT];
static unsigned long long tex_live, tex_peak; static int tex_count;
unsigned long long fgl_tex_live(void){ return tex_live; }
unsigned long long fgl_tex_peak(void){ return tex_peak; }
int fgl_tex_count(void){ return tex_count; }
void fgl_reset_peak(void){ tex_peak = tex_live; }
static void *noop(void){ return 0; }
static const GLubyte *f_GetString(GLenum n){ return (const GLubyte*)(n==0x1F02 ? "OpenGL ES 2.0 nano7-fake" : n==0x1F03 ? "" : "nano7-fake"); }
static void f_GetIntegerv(GLenum n, GLint *p){ if(!p) return; p[0]=0; if(n==0x0D33) p[0]=2048; else if(n==0x0BA2){p[0]=0;p[1]=0;p[2]=800;p[3]=600;} else if(n==0x8869||n==0x8872||n==0x8B4D) p[0]=8; }
static void f_GetFloatv(GLenum n, float *p){ if(p) p[0]=0; }
static void f_GetBooleanv(GLenum n, unsigned char *p){ if(p) p[0]=0; }
static void f_Getiv(GLuint o, GLenum n, GLint *p){ if(!p) return; p[0] = (n==0x8B81||n==0x8B82||n==0x8B83) ? 1 : 0; }
static GLuint f_Create(void){ return next_id++; }
static GLuint f_CreateShader(GLenum t){ return next_id++; }
static void f_Gen(GLsizei n, GLuint *ids){ for(int i=0;i<n;i++) ids[i]=next_id++; }
static void f_GenTex(GLsizei n, GLuint *ids){ for(int i=0;i<n;i++){ ids[i]=next_id++; } }
static void f_DelTex(GLsizei n, const GLuint *ids){ for(int i=0;i<n;i++){ GLuint t=ids[i]; if(t<MAXT && tbytes[t]){ tex_live-=tbytes[t]; tbytes[t]=0; tex_count--; } } }
static void f_ActiveTexture(GLenum u){ unit=(u-0x84C0)&31; }
static void f_BindTexture(GLenum tg, GLuint t){ bound[unit]=t; }
static int bpp(GLenum fmt, GLenum type){ if(type==0x8033||type==0x8034||type==0x8363) return 2; if(fmt==0x1908) return 4; if(fmt==0x1907) return 3; return 1; }
static void f_TexImage2D(GLenum tg, GLint lvl, GLint ifmt, GLsizei w, GLsizei h, GLint b, GLenum fmt, GLenum type, const void *d){
  GLuint t=bound[unit]; if(t>=MAXT||lvl) return; uint32_t s=(uint32_t)w*h*bpp(fmt,type);
  if(tbytes[t]) tex_live-=tbytes[t]; else tex_count++; tbytes[t]=s; tex_live+=s; if(tex_live>tex_peak) tex_peak=tex_live; }
static GLenum f_CheckFB(GLenum t){ return 0x8CD5; }
static GLint f_GetLoc(GLuint p, const char *n){ return 1; }
static GLenum f_GetError(void){ return 0; }
static unsigned char f_IsTrue(GLuint x){ return 1; }
static void f_ReadPixels(GLint x, GLint y, GLsizei w, GLsizei h, GLenum f, GLenum t, void *d){ if(d) memset(d,0,(size_t)w*h*4); }
static void f_GetInfoLog(GLuint o, GLsizei m, GLsizei *l, char *s){ if(l)*l=0; if(s&&m>0)s[0]=0; }
struct E { const char *n; void *f; };
static const struct E tab[] = {
 {"glGetString",f_GetString},{"glGetIntegerv",f_GetIntegerv},{"glGetFloatv",f_GetFloatv},{"glGetBooleanv",f_GetBooleanv},
 {"glGetShaderiv",f_Getiv},{"glGetProgramiv",f_Getiv},{"glCreateProgram",f_Create},{"glCreateShader",f_CreateShader},
 {"glGenTextures",f_GenTex},{"glGenBuffers",f_Gen},{"glGenFramebuffers",f_Gen},{"glGenRenderbuffers",f_Gen},
 {"glDeleteTextures",f_DelTex},{"glActiveTexture",f_ActiveTexture},{"glBindTexture",f_BindTexture},{"glTexImage2D",f_TexImage2D},
 {"glCheckFramebufferStatus",f_CheckFB},{"glGetUniformLocation",f_GetLoc},{"glGetAttribLocation",f_GetLoc},{"glGetError",f_GetError},
 {"glIsTexture",f_IsTrue},{"glIsProgram",f_IsTrue},{"glIsShader",f_IsTrue},{"glIsBuffer",f_IsTrue},{"glIsFramebuffer",f_IsTrue},{"glIsRenderbuffer",f_IsTrue},
 {"glReadPixels",f_ReadPixels},{"glGetShaderInfoLog",f_GetInfoLog},{"glGetProgramInfoLog",f_GetInfoLog},{0,0}};
void *nano7_gl_proc(const char *name){ for(const struct E *e=tab;e->n;e++) if(!strcmp(e->n,name)) return e->f; return (void*)noop; }
