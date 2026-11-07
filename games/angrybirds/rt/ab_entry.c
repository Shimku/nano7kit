/* ab_entry.c: drives the recompiled Ka3D engine the way the iOS shell did (nano7kit, MIT).
 * Skip UIApplicationMain / ObjC. Allocate IphoneOSInterface + EGL_Context(480,320,landscape)
 * and call framework::init; each frame GameApp::update; touches App::touchBegan/Moved/Ended.
 * TouchEvent is 3 words (pointer id, x, y) in 480x320 landscape pixels. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "rt.h"
#include "fngl.h"
#include "ab_entry.h"
#include "rt_entry.h"
#ifndef AB_BeginScene
#define AB_BeginScene 0xa8880u
#endif

static uint32_t osi, ctx, app, str_data, str_docs;
static int started;
static int fed_down, fed_id;
static float fed_x, fed_y;
static float view_zoom = 1.f;

static uint32_t mk_string(const char *s) {
    uint32_t o = g_malloc(16), p = g_strdup(s);
    if (AB_StringC1) rt_call("StringC1", AB_StringC1, o, p, 0, 0, 0, 0);
    else gw32(o, p);
    return o;
}

/* IphoneOSInterface: vtable then a lang::String at +8 (getDefaultDataPath copies it). */
static uint32_t make_osi(void) {
    uint32_t o = g_malloc(64);
    memset(G8(o), 0, 64);
    gw32(o, AB_VT_IPHONEOS);
    uint32_t s = mk_string("data");
    memcpy(G8(o + 8), G8(s), 16);
    return o;
}

static void reset_view(void) {
    view_zoom = 1.f;
    fe_scale = 0.75f;
    fe_scale_y = 0.9f;
    fe_off_x = 0.f;
    fe_off_y = 0.f;
}

int ab_start(void) {
    int i;
    reset_view();
    rt_log("ab_start: %d static inits", AB_INIT_COUNT);
    for (i = 0; i < AB_INIT_COUNT; i++) rt_call("init", ab_inits[i], 0, 0, 0, 0, 0, 0);
    osi = make_osi();
    /* Root path before init so FileBundle / App::path do not prefix data/ twice.
     * fopen still uses the basename, so a leftover data/data/... still opens. */
    str_data = mk_string("data");
    str_docs = mk_string("docs");
    if (AB_SetRootPath) rt_call("setRootPath", AB_SetRootPath, str_data, 0, 0, 0, 0, 0);
    if (AB_SetOutRootPath) rt_call("setOutRootPath", AB_SetOutRootPath, str_docs, 0, 0, 0, 0, 0);
    ctx = 0;
    /* Orientation 0: width()=480 height()=320. Type 1/3 swap those fields and
     * Image::draw clips the landscape splash against a 320x480 viewport. */
    if (AB_EglCreate) {
        rt_log("ab_start: EGL_createContext %08x", AB_EglCreate);
        ctx = rt_call("EGL_createContext", AB_EglCreate, 480, 320, 0, 0, 0, 0);
    }
    if (!ctx) {
        uint32_t ctor = AB_EglC1 ? AB_EglC1 : AB_EglC2;
        ctx = g_malloc(4096);
        memset(G8(ctx), 0, 4096);
        if (ctor) {
            static const uint32_t present[1] = {0};
            rt_log("ab_start: EGL_Context ctor %08x", ctor);
            rt_call("EGL_Context", ctor, ctx, 480, 320, 0, present, 1);
        }
        if (AB_CreateShaders) {
            rt_log("ab_start: createShaders %08x ctx=%08x", AB_CreateShaders, ctx);
            rt_call("createShaders", AB_CreateShaders, ctx, 0, 0, 0, 0, 0);
        }
    }
    /* lang::Object starts refcount at 0. RenderScene retain/release would
     * otherwise delete the context at the end of the first GameApp::update. */
    if (ctx) gw32(ctx + 4, 4);
    /* present() tails to this+0x2d4; the iOS shell passed EAGL swap. beginScene is bx lr. */
    if (ctx && AB_BeginScene) gw32(ctx + 0x2d4u, AB_BeginScene);
    /* The iOS view called setViewport / setOrthographicProjection. Image::draw
     * clips every sprite if the viewport rect is still 0,0,0,0. */
    if (ctx) {
        uint32_t rect = g_malloc(16);
        gw32(rect, 0); gw32(rect + 4, 0); gw32(rect + 8, 480); gw32(rect + 12, 320);
        rt_call("setViewport", 0xa9560u, ctx, rect, 0, 0, 0, 0);
        rt_call("setOrtho", 0xa8afcu, ctx, 0, 0, 0, 0, 0);
    }
    rt_log("ab_start: framework::init osi=%08x ctx=%08x", osi, ctx);
    if (AB_Init) rt_call("framework::init", AB_Init, osi, ctx, 0, 0, 0, 0);
    if (AB_AppGet) app = rt_call("App::get", AB_AppGet, 0, 0, 0, 0, 0, 0);
    if (!app && AB_APP_GET_SLOT) app = gr32(AB_APP_GET_SLOT);
    rt_log("ab_start: App::get -> %08x", app);
    if (app && AB_SetDataPath) rt_call("setDataPath", AB_SetDataPath, app, str_data, 0, 0, 0, 0);
    started = 1;
    rt_log("ab_start: ok, guest heap %u, ctx vtbl=%08x ref=%u present_cb=%08x",
           rt_heap_used(), ctx ? gr32(ctx) : 0, ctx ? gr32(ctx + 4) : 0,
           ctx ? gr32(ctx + 0x2d4u) : 0);
#ifdef RT_CHECK
    if (getenv("AB_WATCH")) rt_watch_addr = ctx;
#endif
    return 0;
}

/* panel 240x432 (portrait) -> game 480x320 landscape, home on the right.
 * emit_tri: panel_x = off_x + (fb_h - fy)*scale with fy GL-up = lua y-down
 * inverted, so panel_x = off_x + lua_y * scale. Lua menus and the slingshot
 * hit-test y-down (PLAY is at 180, not GL fy 140). */
static void panel_to_game(float px, float py, float *gx, float *gy) {
    float sx = fe_scale, sy = fe_scale_y;
    if (sx < 1e-6f) sx = 1e-6f;
    if (sy < 1e-6f) sy = 1e-6f;
    *gy = (px - fe_off_x) / sx;
    *gx = (py - fe_off_y) / sy;
}

#ifndef AB_MouseMove
#define AB_MouseMove 0x44c4u
#endif
#ifndef AB_LoadLevel
#define AB_LoadLevel 0xdb94u
#endif
#ifndef AB_LuaCall
#define AB_LuaCall 0x7497cu
#endif
#define AB_LBUTTON 57u
static void send_touch(int began, int moved, int ended, int id, float gx, float gy) {
    uint32_t ev = g_malloc(16);
    int32_t ix = (int32_t)(gx + (gx < 0.f ? -0.5f : 0.5f));
    int32_t iy = (int32_t)(gy + (gy < 0.f ? -0.5f : 0.5f));
    gw32(ev, (uint32_t)id);
    gw32(ev + 4, F2U(gx));
    gw32(ev + 8, F2U(gy));
    uint32_t fn = 0;
    if (began) fn = AB_TouchBegan;
    else if (ended) fn = AB_TouchEnded;
    else fn = AB_TouchMoved;
    if (fn && app) rt_call("touch", fn, app, ev, 0, 0, 0, 0);
    g_free(ev);
    if (!app) return;
    /* iOS mapped a finger to LBUTTON + mouseMove; Lua menus read those, not only TouchEvent. */
    if (AB_MouseMove) rt_call("mouseMove", AB_MouseMove, app, (uint32_t)ix, (uint32_t)iy, 0, 0, 0);
    G8(app + 532u + AB_LBUTTON)[0] = (uint8_t)((began || moved) ? 1 : 0);
    if (began && AB_KeyDown) rt_call("keyDown", AB_KeyDown, app, AB_LBUTTON, 0, 0, 0, 0);
    if (ended && AB_KeyUp) rt_call("keyUp", AB_KeyUp, app, AB_LBUTTON, 0, 0, 0, 0);
    if (getenv("AB_TOUCH_LOG"))
        rt_log("touch b=%d m=%d e=%d game=%d,%d lua=%08x kDown=%u kHold=%u kUp=%u",
               began, moved, ended, ix, iy, gr32(app + 652u),
               G8(app + 721u)[0], G8(app + 532u + AB_LBUTTON)[0], G8(app + 807u)[0]);
}

void ab_touch(int id, int down, float px, float py) {
    float gx, gy;
    panel_to_game(px, py, &gx, &gy);
    if (down && !fed_down) send_touch(1, 0, 0, id, gx, gy);
    else if (down && fed_down) send_touch(0, 1, 0, id, gx, gy);
    else if (!down && fed_down) send_touch(0, 0, 1, id, gx, gy);
    fed_down = down != 0;
    fed_id = id;
    fed_x = px;
    fed_y = py;
}

void ab_zoom_by(float factor) {
    float cx = 120.f, cy = 216.f;
    float gx, gy;
    panel_to_game(cx, cy, &gx, &gy);
    view_zoom *= factor;
    if (view_zoom < 0.75f) view_zoom = 0.75f;
    if (view_zoom > 2.5f) view_zoom = 2.5f;
    fe_scale = 0.75f * view_zoom;
    fe_scale_y = 0.9f * view_zoom;
    /* keep the panel centre on the same game point */
    fe_off_x = cx - gy * fe_scale;
    fe_off_y = cy - gx * fe_scale_y;
}

void ab_pan_by(float dx, float dy) {
    fe_off_x += dx;
    fe_off_y += dy;
}

void ab_frame_q(float dt, const ab_tsample *q, int n) {
    int i;
    if (!started) return;
    for (i = 0; i < n; i++) ab_touch(0, q[i].down, q[i].x, q[i].y);
    if (dt > 0.1f) dt = 0.1f;
    if (dt < 0.001f) dt = 0.001f;
    be_frame_begin();
    if (app && AB_Update) rt_call("update", AB_Update, app, F2U(dt), ctx, 0, 0, 0);
    be_frame_end();
}

void ab_frame(float dt) { ab_frame_q(dt, 0, 0); }

void ab_load_level(const char *name) {
    uint32_t glu = app ? gr32(app + 652u) : 0;
    uint32_t s;
    if (!glu || !name) {
        rt_log("ab_load_level: no GameLua");
        return;
    }
    s = mk_string(name);
    rt_log("ab_load_level %s lua=%08x", name, glu);
    rt_call("loadLevel", AB_LoadLevel, glu, s, 0, 0, 0, 0);
}

/* lua::LuaObject::call(char const*): the menu's PLAY sprite is hit-tested at
 * game (240, 180) (MENU_PLAY pivot, not the .dat src rect). gotoFirstLevel is
 * the game's own path onto Level1 after createMenuPages. */
void ab_call_lua(const char *name) {
    uint32_t glu = app ? gr32(app + 652u) : 0;
    uint32_t p;
    if (!glu || !name) {
        rt_log("ab_call_lua: no GameLua");
        return;
    }
    p = g_strdup(name);
    rt_log("ab_call_lua %s lua=%08x", name, glu);
    rt_call("luaCall", AB_LuaCall, glu, p, 0, 0, 0, 0);
}
