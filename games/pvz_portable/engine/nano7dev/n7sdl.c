/* nano7 PvZ device layer: the few SDL2 calls the engine makes, without SDL (the iPod has no SDL). Time and files go
 * to the n7p platform; the event queue carries the touches as mouse events. Part of the nano7kit PvZ port. */
#include <SDL.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "n7dev.h"

Uint32 SDL_GetTicks(void) { return n7p_ms(); }
Uint64 SDL_GetTicks64(void) { return n7p_ms(); }
Uint64 SDL_GetPerformanceCounter(void) { return n7p_us(); }
Uint64 SDL_GetPerformanceFrequency(void) { return 1000000; }
void SDL_Delay(Uint32 ms) { (void)ms; }   /* never sleep inside the OS frame callback */

/* events: a small ring of mouse events made from touches */
#define NQ 64
static SDL_Event s_q[NQ]; static int s_qh, s_qt, s_mx, s_my; static Uint32 s_buttons;
void n7sdl_push_mouse(int type, int x, int y)
{
	int n = (s_qt + 1) % NQ; if (n == s_qh) return;   /* full: drop */
	SDL_Event* e = &s_q[s_qt]; memset(e, 0, sizeof *e);
	e->common.timestamp = n7p_ms();
	if (type == 0) { e->type = SDL_MOUSEMOTION; e->motion.x = x; e->motion.y = y; e->motion.state = s_buttons; }
	else { e->type = type == 1 ? SDL_MOUSEBUTTONDOWN : SDL_MOUSEBUTTONUP; e->button.button = SDL_BUTTON_LEFT; e->button.state = type == 1;
		e->button.clicks = 1; e->button.x = x; e->button.y = y; }
	s_qt = n;
}
int SDL_PollEvent(SDL_Event* e)
{
	if (s_qh == s_qt) return 0;
	SDL_Event* q = &s_q[s_qh]; s_qh = (s_qh + 1) % NQ;
	if (q->type == SDL_MOUSEMOTION) { s_mx = q->motion.x; s_my = q->motion.y; }
	else { s_mx = q->button.x; s_my = q->button.y; if (q->type == SDL_MOUSEBUTTONDOWN) s_buttons = SDL_BUTTON_LMASK; else s_buttons = 0; }
	if (e) *e = *q;
	return 1;
}
int SDL_PushEvent(SDL_Event* e) { (void)e; return 0; }
Uint32 SDL_GetMouseState(int* x, int* y) { if (x) *x = s_mx; if (y) *y = s_my; return s_buttons; }
Uint32 SDL_GetGlobalMouseState(int* x, int* y) { return SDL_GetMouseState(x, y); }
int SDL_ShowCursor(int t) { (void)t; return 0; }
SDL_Cursor* SDL_CreateSystemCursor(SDL_SystemCursor id) { (void)id; return (SDL_Cursor*)1; }
SDL_Cursor* SDL_CreateColorCursor(SDL_Surface* s, int x, int y) { (void)s; (void)x; (void)y; return (SDL_Cursor*)1; }
SDL_Surface* SDL_CreateRGBSurfaceFrom(void* p, int w, int h, int d, int pitch, Uint32 r, Uint32 g, Uint32 b, Uint32 a) { return 0; }
void SDL_FreeSurface(SDL_Surface* s) { (void)s; }
void SDL_SetCursor(SDL_Cursor* c) { (void)c; }
void SDL_FreeCursor(SDL_Cursor* c) { (void)c; }
void SDL_WarpMouseInWindow(SDL_Window* w, int x, int y) { (void)w; (void)x; (void)y; }
SDL_bool SDL_IsTextInputActive(void) { return SDL_FALSE; }
void SDL_StartTextInput(void) {}
void SDL_StopTextInput(void) {}
SDL_Keymod SDL_GetModState(void) { return KMOD_NONE; }
const Uint8* SDL_GetKeyboardState(int* n) { static Uint8 k[512]; if (n) *n = 512; return k; }
SDL_Keycode SDL_GetKeyFromScancode(SDL_Scancode s) { (void)s; return 0; }
SDL_Scancode SDL_GetScancodeFromKey(SDL_Keycode k) { (void)k; return 0; }
char* SDL_GetClipboardText(void) { char* s = (char*)malloc(1); if (s) *s = 0; return s; }
int SDL_SetClipboardText(const char* t) { (void)t; return 0; }
SDL_bool SDL_HasClipboardText(void) { return SDL_FALSE; }
int SDL_Init(Uint32 f) { (void)f; return 0; }
int SDL_InitSubSystem(Uint32 f) { (void)f; return 0; }
void SDL_QuitSubSystem(Uint32 f) { (void)f; }
Uint32 SDL_WasInit(Uint32 f) { return f; }
void SDL_Quit(void) {}
const char* SDL_GetError(void) { return ""; }
int SDL_SetError(const char* fmt, ...) { (void)fmt; return -1; }
void SDL_ClearError(void) {}
SDL_bool SDL_SetHint(const char* n, const char* v) { (void)n; (void)v; return SDL_TRUE; }
const char* SDL_GetHint(const char* n) { (void)n; return 0; }
int SDL_ShowSimpleMessageBox(Uint32 f, const char* t, const char* m, SDL_Window* w)
{ char b[300]; snprintf(b, sizeof b, "nano7 message: %s: %s", t ? t : "", m ? m : ""); n7p_log(b); return 0; }
void SDL_Log(const char* fmt, ...) { char b[256]; va_list a; va_start(a, fmt); vsnprintf(b, sizeof b, fmt, a); va_end(a); n7p_log(b); }
void SDL_LogMessage(int c, SDL_LogPriority p, const char* fmt, ...) { char b[256]; va_list a; va_start(a, fmt); vsnprintf(b, sizeof b, fmt, a); va_end(a); n7p_log(b); }
void SDL_LogError(int c, const char* fmt, ...) { char b[256]; va_list a; va_start(a, fmt); vsnprintf(b, sizeof b, fmt, a); va_end(a); n7p_log(b); }
void SDL_LogWarn(int c, const char* fmt, ...) { char b[256]; va_list a; va_start(a, fmt); vsnprintf(b, sizeof b, fmt, a); va_end(a); n7p_log(b); }
void SDL_LogInfo(int c, const char* fmt, ...) { char b[256]; va_list a; va_start(a, fmt); vsnprintf(b, sizeof b, fmt, a); va_end(a); n7p_log(b); }
void SDL_LogDebug(int c, const char* fmt, ...) { (void)c; (void)fmt; }
void* SDL_malloc(size_t n) { return malloc(n); }
void* SDL_calloc(size_t a, size_t b) { return calloc(a, b); }
void* SDL_realloc(void* p, size_t n) { return realloc(p, n); }
void SDL_free(void* p) { free(p); }
char* SDL_strdup(const char* s) { size_t n = strlen(s) + 1; char* d = (char*)malloc(n); if (d) memcpy(d, s, n); return d; }
char* SDL_GetBasePath(void) { size_t n = strlen(n7p_data_dir()); char* d = (char*)malloc(n + 2); if (d) { memcpy(d, n7p_data_dir(), n); d[n] = '/'; d[n + 1] = 0; } return d; }
char* SDL_GetPrefPath(const char* org, const char* app) { (void)org; (void)app; return SDL_GetBasePath(); }
const char* SDL_GetPlatform(void) { return "nano7"; }
int SDL_GetCPUCount(void) { return 1; }
int SDL_GetSystemRAM(void) { return 64; }
int SDL_OpenURL(const char* u) { (void)u; return -1; }
SDL_threadID SDL_ThreadID(void) { return 1; }

/* text: only the UTF-16 -> UTF-8 conversion the string loader needs */
char* SDL_iconv_string(const char* to, const char* from, const char* in, size_t len)
{
	int be = from && strstr(from, "BE") != 0;
	size_t n = len / 2; char* o = (char*)malloc(n * 3 + 1); size_t k = 0;
	if (!o) return 0;
	for (size_t i = 0; i < n; i++) {
		const unsigned char* p = (const unsigned char*)in + i * 2;
		unsigned c = be ? (p[0] << 8 | p[1]) : (p[1] << 8 | p[0]);
		if (c < 0x80) o[k++] = (char)c;
		else if (c < 0x800) { o[k++] = (char)(0xC0 | c >> 6); o[k++] = (char)(0x80 | (c & 63)); }
		else { o[k++] = (char)(0xE0 | c >> 12); o[k++] = (char)(0x80 | ((c >> 6) & 63)); o[k++] = (char)(0x80 | (c & 63)); }
	}
	o[k] = 0; (void)to; return o;
}

/* RWops: whole files in memory (only the music stubs touch these) */
int SDL_RWclose(SDL_RWops* c) { if (c && c->close) return c->close(c); return 0; }
SDL_RWops* SDL_RWFromConstMem(const void* m, int n) { (void)m; (void)n; return 0; }
SDL_RWops* SDL_RWFromMem(void* m, int n) { (void)m; (void)n; return 0; }
SDL_RWops* SDL_RWFromFile(const char* f, const char* m) { (void)f; (void)m; return 0; }

/* windows and GL contexts: the OS owns the panel */
SDL_Window* SDL_CreateWindow(const char* t, int x, int y, int w, int h, Uint32 f) { return (SDL_Window*)1; }
void SDL_DestroyWindow(SDL_Window* w) { (void)w; }
void SDL_GetWindowSize(SDL_Window* w, int* x, int* y) { if (x) *x = n7_panel_w; if (y) *y = n7_panel_h; }
void SDL_GL_GetDrawableSize(SDL_Window* w, int* x, int* y) { SDL_GetWindowSize(w, x, y); }
void SDL_SetWindowTitle(SDL_Window* w, const char* t) { (void)w; (void)t; }
void SDL_SetWindowSize(SDL_Window* w, int x, int y) {}
void SDL_SetWindowPosition(SDL_Window* w, int x, int y) {}
int SDL_SetWindowFullscreen(SDL_Window* w, Uint32 f) { return 0; }
Uint32 SDL_GetWindowFlags(SDL_Window* w) { return SDL_WINDOW_SHOWN | SDL_WINDOW_INPUT_FOCUS | SDL_WINDOW_MOUSE_FOCUS; }
void SDL_ShowWindow(SDL_Window* w) {}
void SDL_RaiseWindow(SDL_Window* w) {}
void SDL_SetWindowIcon(SDL_Window* w, SDL_Surface* s) {}
SDL_GLContext SDL_GL_CreateContext(SDL_Window* w) { return (SDL_GLContext)1; }
void SDL_GL_DeleteContext(SDL_GLContext c) {}
int SDL_GL_MakeCurrent(SDL_Window* w, SDL_GLContext c) { return 0; }
int SDL_GL_SetAttribute(SDL_GLattr a, int v) { return 0; }
int SDL_GL_SetSwapInterval(int i) { return 0; }
int SDL_GL_GetSwapInterval(void) { return 1; }
void SDL_GL_SwapWindow(SDL_Window* w) {}
extern void* nano7_gl_proc(const char* name);
void* SDL_GL_GetProcAddress(const char* p) { return nano7_gl_proc(p); }
int SDL_GetDesktopDisplayMode(int i, SDL_DisplayMode* m) { if (m) { memset(m, 0, sizeof *m); m->w = 800; m->h = 600; m->refresh_rate = 60; } return 0; }
int SDL_GetCurrentDisplayMode(int i, SDL_DisplayMode* m) { return SDL_GetDesktopDisplayMode(i, m); }
int SDL_GetWindowDisplayIndex(SDL_Window* w) { return 0; }
int SDL_GetNumVideoDisplays(void) { return 1; }
void SDL_SetTextInputRect(const SDL_Rect* r) { (void)r; }
SDL_bool SDL_HasEvents(Uint32 a, Uint32 b) { return s_qh != s_qt ? SDL_TRUE : SDL_FALSE; }
size_t SDL_strlcpy(char* d, const char* s, size_t n) { size_t l = strlen(s); if (n) { size_t c = l < n - 1 ? l : n - 1; memcpy(d, s, c); d[c] = 0; } return l; }
SDL_Surface* SDL_CreateRGBSurfaceWithFormatFrom(void* p, int w, int h, int d, int pitch, Uint32 f) { return 0; }
SDL_Cursor* SDL_GetDefaultCursor(void) { return (SDL_Cursor*)1; }
