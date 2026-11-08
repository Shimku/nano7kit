/* ab_entry.c: drives the recompiled Ka3D engine the way the iOS shell did (nano7kit, MIT).
 * Skip UIApplicationMain / ObjC. Allocate IphoneOSInterface + EGL_Context(480,320,landscape)
 * and call framework::init; each frame GameApp::update; touches App::touchBegan/Moved/Ended.
 * TouchEvent is 3 words (pointer id, x, y) in 480x320 landscape pixels. */
#include <math.h>
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

static uint32_t osi, ctx, app, str_data, str_docs, view_rect;
static int started;
static int fed_down, fed_id;
static float fed_x, fed_y;
static float view_zoom = 1.f;
static int guest_ok(uint32_t a, uint32_t n);
static uint32_t intern_str(const char *s);
static void ab_lua_iphone_screen(void);

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
        view_rect = g_malloc(16);
        gw32(view_rect, 0); gw32(view_rect + 4, 0); gw32(view_rect + 8, 480); gw32(view_rect + 12, 320);
        rt_call("setViewport", 0xa9560u, ctx, view_rect, 0, 0, 0, 0);
        rt_call("setOrtho", 0xa8afcu, ctx, 0, 0, 0, 0, 0);
    }
    rt_log("ab_start: framework::init osi=%08x ctx=%08x", osi, ctx);
    if (AB_Init) rt_call("framework::init", AB_Init, osi, ctx, 0, 0, 0, 0);
    if (AB_AppGet) app = rt_call("App::get", AB_AppGet, 0, 0, 0, 0, 0, 0);
    if (!app && AB_APP_GET_SLOT) app = gr32(AB_APP_GET_SLOT);
    rt_log("ab_start: App::get -> %08x", app);
    if (app && AB_SetDataPath) rt_call("setDataPath", AB_SetDataPath, app, str_data, 0, 0, 0, 0);
    started = 1;
    ab_lua_iphone_screen();
    rt_log("ab_start: ok, guest heap %u, ctx vtbl=%08x ref=%u present_cb=%08x",
           rt_heap_used(), ctx ? gr32(ctx) : 0, ctx ? gr32(ctx + 4) : 0,
           ctx ? gr32(ctx + 0x2d4u) : 0);
#ifdef RT_CHECK
    if (getenv("AB_WATCH")) rt_watch_addr = ctx;
#endif
    return 0;
}

/* Inverse of emit_tri, matching PvZ Nano7PanelToGame (n7gl_dev_a.inc):
 *   gx = (py - y0) / kx; gy_down = gh - (px - x0) / ky
 * emit_tri: panel_x = off_x + fy * scale, panel_y = off_y + fx * scale_y
 * with fy GL-up = fe_fb_h - lua y-down. PLAY is lua (240, 180), not GL fy 140. */
static void panel_to_game(float px, float py, float *gx, float *gy) {
    float sx = fe_scale, sy = fe_scale_y;
    if (sx < 1e-6f) sx = 1e-6f;
    if (sy < 1e-6f) sy = 1e-6f;
    *gx = (py - fe_off_y) / sy;
    *gy = fe_fb_h - (px - fe_off_x) / sx;
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
#ifndef AB_SetPhysicsEnabled
#define AB_SetPhysicsEnabled 0x5ff8u
#endif
#ifndef AB_WorldStep
#define AB_WorldStep 0x128d20u
#endif
#ifndef AB_SetTopLeft
#define AB_SetTopLeft 0x6000u
#endif
#ifndef AB_SetWorldScale
#define AB_SetWorldScale 0x600cu
#endif
#ifndef AB_LuaSetNumber
#define AB_LuaSetNumber 0x78c7cu
#endif
#ifndef AB_LuaSetString
#define AB_LuaSetString 0x78d94u
#endif
#ifndef AB_LuaSetBool
#define AB_LuaSetBool 0x786fcu
#endif
#ifndef AB_LuaGetTable
#define AB_LuaGetTable 0x7ccf8u
#endif
#ifndef AB_LuaTableD1
#define AB_LuaTableD1 0x77de8u
#endif
#ifndef AB_LuaGetString
#define AB_LuaGetString 0x7d200u
#endif
#ifndef AB_SetTheme
#define AB_SetTheme 0x92b8u
#endif
#define AB_LBUTTON 57u
/* GameLua::update copies App::touches().size() (App+628) into lua `touches`.
 * updateGame only starts a slingshot drag when that count is exactly 1.
 * TouchBegan/Ended grow a 12-byte slot vector; a missed Ended after a menu
 * tap leaves count at 2+ and the pouch never grabs. Pin the count. */
static void pin_touch_count(uint32_t a, int down, int id, float gx, float gy) {
    uint32_t data;
    if (!a || !guest_ok(a, 636u)) return;
    data = gr32(a + 624u);
    if (down) {
        if (!data || !guest_ok(data, 12u)) {
            data = g_malloc(36);
            if (!data) return;
            memset(G8(data), 0, 36);
            gw32(a + 624u, data);
            gw32(a + 632u, 3u);
        }
        gw32(data, (uint32_t)id);
        gw32(data + 4u, F2U(gx));
        gw32(data + 8u, F2U(gy));
        gw32(a + 628u, 1u);
    } else {
        gw32(a + 628u, 0u);
    }
}

static void send_touch(int began, int moved, int ended, int id, float gx, float gy) {
    static uint32_t ev;
    uint32_t glu, app2;
    if (!ev) ev = g_malloc(16);
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
    if (!app) return;
    glu = gr32(app + 652u);
    app2 = guest_ok(glu, 32u) ? gr32(glu + 28u) : 0;
    pin_touch_count(app, began || moved, id, gx, gy);
    if (app2 && app2 != app) pin_touch_count(app2, began || moved, id, gx, gy);
    /* iOS mapped a finger to LBUTTON + mouseMove; Lua menus read those, not only TouchEvent. */
    if (AB_MouseMove) rt_call("mouseMove", AB_MouseMove, app, (uint32_t)ix, (uint32_t)iy, 0, 0, 0);
    G8(app + 532u + AB_LBUTTON)[0] = (uint8_t)((began || moved) ? 1 : 0);
    if (began && AB_KeyDown) rt_call("keyDown", AB_KeyDown, app, AB_LBUTTON, 0, 0, 0, 0);
    if (ended && AB_KeyUp) rt_call("keyUp", AB_KeyUp, app, AB_LBUTTON, 0, 0, 0, 0);
    if (getenv("AB_TOUCH_LOG"))
        rt_log("touch b=%d m=%d e=%d game=%d,%d lua=%08x app2=%08x n=%d n2=%d kDown=%u kHold=%u kUp=%u",
               began, moved, ended, ix, iy, glu, app2,
               guest_ok(app, 632u) ? (int)gr32(app + 628u) : -1,
               guest_ok(app2, 632u) ? (int)gr32(app2 + 628u) : -1,
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
    /* keep the panel centre on the same game point (emit_tri: px = off + fy*sx) */
    fe_off_x = cx - (fe_fb_h - gy) * fe_scale;
    fe_off_y = cy - gx * fe_scale_y;
}

void ab_pan_by(float dx, float dy) {
    fe_off_x += dx;
    fe_off_y += dy;
}

void ab_frame_q(float dt, const ab_tsample *q, int n) {
    int i;
    if (!started) return;
    /* Tutorial lua calls setPhysicsEnabled(false) until TUTORIAL_OK is tapped.
     * GameLua::update then skips b2World::Step. Enable before the update so a
     * tap in the same frame can still run, and if lua left it off, Step once
     * after (same 1/30, 10, 10 as GameLua at 0x7fcc) so Level 1 is not frozen. */
    {
        ab_phys_info info;
        ab_phys_snapshot(&info);
        if (info.dynamic_n > 0 && !info.phys_on)
            ab_phys_enable(1);
    }
    for (i = 0; i < n; i++) ab_touch(0, q[i].down, q[i].x, q[i].y);
    if (dt > 0.1f) dt = 0.1f;
    if (dt < 0.001f) dt = 0.001f;
    fe_stat_tris = 0;
    fe_stat_draws = 0;
    /* Identity GL stacks + the iOS 480x320 ortho, then one GameApp::update.
     * update already runs lua cameraFunction and drawGame. A second lua
     * drawGame here painted the world on top of the pause overlay (device
     * test 4, PAUSED 1-2 clipped). Do not AABB-fit the camera before draw:
     * that chased every body, zoomed to 0.20 and left black/white voids. */
    ab_cam_fit_play();
    fe_reset_matrices();
    if (ctx && view_rect) {
        rt_call("setViewport", 0xa9560u, ctx, view_rect, 0, 0, 0, 0);
        rt_call("setOrtho", 0xa8afcu, ctx, 0, 0, 0, 0, 0);
    }
    be_frame_begin();
    if (app && AB_Update) rt_call("update", AB_Update, app, F2U(dt), ctx, 0, 0, 0);
    ab_cam_fit_play();
    {
        ab_phys_info info;
        ab_phys_snapshot(&info);
        if (info.dynamic_n > 0 && !info.phys_on && info.world && guest_ok(info.world, 4u)) {
            uint32_t st[2] = {0, 0};
            ab_phys_enable(1);
            rt_call("b2Step", AB_WorldStep, info.world, 0x3d088889u, 10u, 10u, st, 2);
        }
    }
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
    /* Menu calls setPhysicsEnabled(false). loadLevel never turns it back on;
     * lua does after the tutorial OK. The host test and a missed lua bind
     * would otherwise leave bodies frozen (or, on device, we force it on). */
    ab_phys_enable(1);
    ab_phys_log(name);
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
    p = intern_str(name);
    rt_log("ab_call_lua %s lua=%08x", name, glu);
    rt_call("luaCall", AB_LuaCall, glu, p, 0, 0, 0, 0);
}

/* b2World lives at GameLua+180; layout from the 1.0 ARM (CreateBody / World ctor):
 * world+0x19000+552 bodyList, +560 bodyCount, +564 jointCount, +568/+572 gravity,
 * body+0 type (0 static, 2 dynamic), +12/+16 position, +104 next, +108 fixtures, +112 fixtureCount.
 * GameLua+364 is the physics-enable byte; +376 the 1/30 accumulator. */
#define AB_LUA_OFF 652u
#define AB_WORLD_OFF 180u
#define AB_PHYS_ON_OFF 364u
#define AB_ACC_OFF 376u
#define B2_TAIL 0x19000u
#define B2_BODY_LIST (B2_TAIL + 552u)
#define B2_BODY_COUNT (B2_TAIL + 560u)
#define B2_JOINT_COUNT (B2_TAIL + 564u)
#define B2_GRAV_X (B2_TAIL + 568u)
#define B2_GRAV_Y (B2_TAIL + 572u)
#define AB_CAM_LEFT 368u
#define AB_CAM_TOP 372u
#define AB_CAM_SCALE 380u
#define AB_OBJ_COUNT 192u
#define AB_PHYS_TO_WORLD 20.f
#define AB_VIEW_W 480.f
#define AB_VIEW_H 320.f

static int guest_ok(uint32_t a, uint32_t n) {
    return a >= g_lo && a + n <= g_hi && a + n >= a;
}

static uint32_t game_lua(void) {
    uint32_t glu = app ? gr32(app + AB_LUA_OFF) : 0;
    return guest_ok(glu, AB_CAM_SCALE + 4u) ? glu : 0;
}

static uint32_t intern_str(const char *s) {
    static const char *keys[64];
    static uint32_t ptrs[64];
    static int n;
    int i;
    if (!s) return 0;
    for (i = 0; i < n; i++)
        if (keys[i] == s || (keys[i] && strcmp(keys[i], s) == 0)) return ptrs[i];
    if (n >= 64) return g_strdup(s);
    keys[n] = s;
    ptrs[n] = g_strdup(s);
    return ptrs[n++];
}

static void lua_table_set_num(uint32_t tbl, const char *k, float v) {
    if (!tbl || !k) return;
    rt_call("luaSetNumber", AB_LuaSetNumber, tbl, intern_str(k), F2U(v), 0, 0, 0);
}

/* LuaTable::getTable returns by value (hidden r0, this r1, key r2). Scratch
 * slots are reused; D1 drops the previous lua registry ref. */
static uint32_t lua_subtable(uint32_t parent, const char *name) {
    static uint32_t slot[4];
    static int si;
    uint32_t t;
    int i;
    if (!parent || !name) return 0;
    if (!slot[0]) {
        for (i = 0; i < 4; i++) {
            slot[i] = g_malloc(32);
            if (slot[i]) memset(G8(slot[i]), 0, 32);
        }
    }
    t = slot[si];
    si = (si + 1) & 3;
    if (gr32(t)) rt_call("LuaTableD1", AB_LuaTableD1, t, 0, 0, 0, 0, 0);
    memset(G8(t), 0, 32);
    rt_call("getTable", AB_LuaGetTable, t, parent, intern_str(name), 0, 0, 0);
    return guest_ok(t, 16u) && gr32(t + 8u) ? t : 0;
}

static void ab_lua_set_number(const char *name, float v) {
    uint32_t glu = game_lua();
    if (!glu || !name) return;
    lua_table_set_num(glu, name, v);
}

static void ab_lua_set_string(const char *name, const char *val) {
    uint32_t glu = game_lua(), k, v;
    if (!glu || !name || !val) return;
    k = intern_str(name);
    v = g_strdup(val);
    rt_call("luaSetString", AB_LuaSetString, glu, k, v, 0, 0, 0);
}

static void ab_lua_set_bool(const char *name, int on) {
    uint32_t glu = game_lua();
    if (!glu || !name) return;
    rt_call("luaSetBool", AB_LuaSetBool, glu, intern_str(name), on ? 1u : 0u, 0, 0, 0);
}

/* updateGame returns before the slingshot while birdTutorialPopups is
 * non-empty. Taps on TUTORIAL_OK drain that list; these flags let the
 * pouch accept a pull once the overlay is gone. */
void ab_ready_slingshot(void) {
    ab_lua_set_bool("birdReady", 1);
    ab_lua_set_bool("fillInNextBird", 1);
    ab_lua_set_number("levelStartTimer", 10.f);
}

/* lua updateLoading calls loadLevelInternal(levelName), which createBox/createCircle.
 * C++ GameLua::loadLevel only fills the objects table. */
static void cam_reset(void) {
    ab_lua_iphone_screen();
}

/* loadLevelInternal rescales castle/bird cameras by lua screenHeight /
 * camera.screenHeight. iPad tables (1680x1050) stay zoomed-in if lua still
 * thinks the screen is 1024 tall. */
static void ab_lua_iphone_screen(void) {
    ab_lua_set_number("screenWidth", AB_VIEW_W);
    ab_lua_set_number("screenHeight", AB_VIEW_H);
}

void ab_load_lua_level(const char *name) {
    if (!name) return;
    cam_reset();
    ab_lua_iphone_screen();
    ab_lua_set_string("levelName", name);
    ab_lua_set_bool("loadingPageDrawn", 1);
    ab_lua_set_bool("showBG", 1);
    rt_log("ab_load_lua_level %s", name);
    fe_reset_matrices();
    ab_call_lua("updateLoading");
    /* levelStartCamera keeps the castle shot while castleCameraTimer <= 2.
     * RECIPE 7's AABB camera (device test 4) made that look like a floating
     * plank in a void. Timer 0 restarts the hold; 3 skips it so the first
     * playable frame is already heading at the bird/slingshot camera. */
    ab_lua_set_number("castleCameraTimer", 3.f);
    ab_lua_set_number("cameraResetTimer", 0.f);
    ab_lua_set_number("cameraAnimationSlider", 1.f);
    ab_lua_set_number("cameraAnimationSliderTarget", 1.f);
    ab_lua_set_bool("allowResetToBirdCamera", 1);
    ab_call_lua("defaultCamera");
    ab_phys_enable(1);
    {
        uint32_t glu = game_lua(), objs, theme;
        static uint32_t theme_slot;
        /* lua setTheme no-ops if objects.theme is unchanged (L14 then L15
         * are both theme1) and the previous sky tiles stay behind. */
        if (!theme_slot) {
            theme_slot = g_malloc(16);
            if (theme_slot) memset(G8(theme_slot), 0, 16);
        }
        theme = theme_slot;
        objs = lua_subtable(glu, "objects");
        if (theme) memset(G8(theme), 0, 16);
        if (objs && theme && AB_LuaGetString)
            rt_call("getString", AB_LuaGetString, theme, objs, intern_str("theme"), 0, 0, 0);
        if (glu && theme && AB_SetTheme && gr32(theme))
            rt_call("setTheme", AB_SetTheme, glu, theme, 0, 0, 0, 0);
    }
    ab_cam_fit_play();
    ab_phys_log(name);
}

void ab_phys_snapshot(ab_phys_info *o) {
    uint32_t glu, world, list, b;
    int nwalk, nmax;
    if (!o) return;
    memset(o, 0, sizeof *o);
    o->dyn_ymin = 1e9f; o->dyn_ymax = -1e9f;
    o->stat_ymin = 1e9f; o->stat_ymax = -1e9f;
    if (!app || !g_mem) return;
    glu = gr32(app + AB_LUA_OFF);
    o->lua = glu;
    if (!guest_ok(glu, AB_ACC_OFF + 4u)) return;
    o->phys_on = (int)G8(glu + AB_PHYS_ON_OFF)[0];
    o->acc = U2F(gr32(glu + AB_ACC_OFF));
    world = gr32(glu + AB_WORLD_OFF);
    o->world = world;
    if (!guest_ok(world, B2_GRAV_Y + 4u)) return;
    nmax = (int)gr32(world + B2_BODY_COUNT);
    o->contacts_hint = (int)gr32(world + B2_JOINT_COUNT);
    {
        uint32_t cm = world + B2_TAIL + 0x1d8u;
        if (guest_ok(cm, 80u)) {
            o->contacts = (int)gr32(cm + 64u);
            o->filter = gr32(cm + 68u);
        }
    }
    o->grav_x = U2F(gr32(world + B2_GRAV_X));
    o->grav_y = U2F(gr32(world + B2_GRAV_Y));
    o->cam_left = U2F(gr32(glu + AB_CAM_LEFT));
    o->cam_top = U2F(gr32(glu + AB_CAM_TOP));
    o->world_scale = U2F(gr32(glu + AB_CAM_SCALE));
    if (guest_ok(glu, AB_OBJ_COUNT + 4u))
        o->obj_n = (int)gr32(glu + AB_OBJ_COUNT);
    {
        uint32_t gctx = gr32(glu + 24u);
        if (guest_ok(gctx, 696u)) {
            o->ctx_w = (int)gr32(gctx + 672u);
            o->ctx_h = (int)gr32(gctx + 676u);
        }
    }
    list = gr32(world + B2_BODY_LIST);
    b = list;
    for (nwalk = 0; b && nwalk < 256 && nwalk < (nmax > 0 ? nmax + 2 : 256); nwalk++) {
        int type, nfix, fi;
        float x, y, wx, wy, sx, sy;
        uint32_t fx;
        if (!guest_ok(b, 116u)) break;
        type = (int)gr32(b + 0u);
        x = U2F(gr32(b + 12u));
        y = U2F(gr32(b + 16u));
        nfix = (int)gr32(b + 112u);
        o->bodies++;
        o->fixtures += nfix > 0 ? nfix : 0;
        wx = x * AB_PHYS_TO_WORLD;
        wy = y * AB_PHYS_TO_WORLD;
        if (o->world_scale > 1e-4f) {
            sx = (wx - o->cam_left) * o->world_scale;
            sy = (wy - o->cam_top) * o->world_scale;
            if (sx >= -40.f && sx <= AB_VIEW_W + 40.f && sy >= -40.f && sy <= AB_VIEW_H + 40.f) {
                o->vis_all++;
                if (type == 2) o->vis_dyn++;
            }
        }
        if (type == 0) {
            o->static_n++;
            if (y < o->stat_ymin) o->stat_ymin = y;
            if (y > o->stat_ymax) o->stat_ymax = y;
        } else if (type == 2) {
            o->dynamic_n++;
            if (y < o->dyn_ymin) o->dyn_ymin = y;
            if (y > o->dyn_ymax) o->dyn_ymax = y;
        }
        if (o->sample_n < AB_PHYS_SAMPLE) {
            int k = o->sample_n++;
            o->sample_type[k] = type;
            o->sample_x[k] = x;
            o->sample_y[k] = y;
        }
        /* count the fixture list as well, in case m_fixtureCount is stale */
        fx = gr32(b + 108u);
        for (fi = 0; fx && fi < 32; fi++) {
            if (!guest_ok(fx, 28u)) break;
            fx = gr32(fx + 20u);
        }
        if (nfix <= 0 && fi > 0) o->fixtures += fi;
        b = gr32(b + 104u);
    }
    if (o->static_n == 0) { o->stat_ymin = 0.f; o->stat_ymax = 0.f; }
    if (o->dynamic_n == 0) { o->dyn_ymin = 0.f; o->dyn_ymax = 0.f; }
}

int ab_phys_resting(const ab_phys_info *o) {
    /* Level 1 spawn (host dump, physics off): 5 static, 16 dynamic, fixtures on
     * every body, dyn Y in about -22..-1, static Y in about -1..5. Gravity is
     * (0,+20). After 10 s of free fall with no ground, dyn Y is y0+1000.
     * A rest is: physics on, ground statics present, dynamics not fallen away
     * from the static range. */
    if (!o || !o->world || !o->phys_on) return 0;
    if (o->static_n < 1 || o->dynamic_n < 1 || o->fixtures < o->bodies) return 0;
    if (o->contacts < 1) return 0;
    if (o->dyn_ymin < -40.f || o->dyn_ymax > 40.f) return 0;
    return 1;
}

void ab_phys_enable(int on) {
    uint32_t glu = app ? gr32(app + AB_LUA_OFF) : 0;
    if (!glu || !AB_SetPhysicsEnabled) return;
    rt_call("setPhysicsEnabled", AB_SetPhysicsEnabled, glu, on ? 1u : 0u, 0, 0, 0, 0);
}

void ab_phys_log(const char *tag) {
    ab_phys_info i;
    extern int be_stat_tex_fail;
    ab_phys_snapshot(&i);
    rt_log("phys %s lua=%08x world=%08x on=%d n=%d st=%d dyn=%d fix=%d contacts=%d filter=%08x joints=%d "
           "g=%.2f,%.2f acc=%.3f dynY=%.2f..%.2f stY=%.2f..%.2f rest=%d heap=%u peak=%u tex=%d peak=%d fail=%d",
           tag ? tag : "-", i.lua, i.world, i.phys_on, i.bodies, i.static_n, i.dynamic_n,
           i.fixtures, i.contacts, i.filter, i.contacts_hint, (double)i.grav_x, (double)i.grav_y,
           (double)i.acc, (double)i.dyn_ymin, (double)i.dyn_ymax, (double)i.stat_ymin,
           (double)i.stat_ymax, ab_phys_resting(&i),
           (unsigned)rt_heap_used(), (unsigned)rt_heap_peak(),
           fe_stat_tex_bytes, fe_stat_tex_peak, be_stat_tex_fail);
    rt_log("cam %s scale=%.3f left=%.1f top=%.1f ctx=%dx%d obj=%d vis=%d/%d dyn vis=%d/%d tris=%d draws=%d",
           tag ? tag : "-", (double)i.world_scale, (double)i.cam_left, (double)i.cam_top,
           i.ctx_w, i.ctx_h, i.obj_n, i.vis_all, i.bodies, i.vis_dyn, i.dynamic_n,
           fe_stat_tris, fe_stat_draws);
    if (i.lua && guest_ok(i.lua, 24u))
        rt_log("phys %s vtbl lua=%08x +16=%08x +20=%08x filt[0]=%08x",
               tag ? tag : "-", gr32(i.lua), gr32(i.lua + 16u), gr32(i.lua + 20u),
               i.filter && guest_ok(i.filter, 4u) ? gr32(i.filter) : 0);
    if (i.sample_n > 0) {
        int k;
        char buf[192];
        int n = 0;
        n = snprintf(buf, sizeof buf, "phys %s bodies", tag ? tag : "-");
        for (k = 0; k < i.sample_n && n > 0 && n < (int)sizeof buf - 24; k++)
            n += snprintf(buf + n, sizeof buf - (size_t)n, " t%d=%.1f,%.1f",
                          i.sample_type[k], (double)i.sample_x[k], (double)i.sample_y[k]);
        rt_log("%s", buf);
    }
    if (i.world && guest_ok(i.world, B2_BODY_LIST + 4u)) {
        uint32_t b = gr32(i.world + B2_BODY_LIST);
        int nwalk;
        char buf[220];
        int n = snprintf(buf, sizeof buf, "phys %s fx", tag ? tag : "-");
        for (nwalk = 0; b && nwalk < 8 && n > 0 && n < (int)sizeof buf - 48; nwalk++) {
            uint32_t fx, sh;
            if (!guest_ok(b, 116u)) break;
            fx = gr32(b + 108u);
            sh = fx && guest_ok(fx, 56u) ? gr32(fx + 28u) : 0;
            n += snprintf(buf + n, sizeof buf - (size_t)n,
                          " t%d fl=%x prx=%d nvert=%d hx=%.2f hy=%.2f",
                          (int)gr32(b), (unsigned)(gr32(b + 4u) & 0xffffu),
                          fx && guest_ok(fx, 44u) ? (int)gr32(fx + 40u) : -2,
                          sh && guest_ok(sh, 152u) ? (int)gr32(sh + 148u) : -1,
                          sh && guest_ok(sh, 32u) ? (double)U2F(gr32(sh + 28u)) : -1.0,
                          sh && guest_ok(sh, 44u) ? (double)U2F(gr32(sh + 40u)) : -1.0);
            b = gr32(b + 104u);
        }
        rt_log("%s", buf);
    }
    if (i.world && guest_ok(i.world, B2_GRAV_Y + 4u)) {
        uint32_t bp = i.world + B2_TAIL + 0x1d8u;
        uint32_t nodes = guest_ok(bp, 8u) ? gr32(bp + 4u) : 0;
        uint32_t b = gr32(i.world + B2_BODY_LIST);
        int nwalk;
        rt_log("phys %s bp move=%d pair=%d nodes=%08x R0=%.3f,%.3f",
               tag ? tag : "-",
               guest_ok(bp, 44u) ? (int)gr32(bp + 40u) : -1,
               guest_ok(bp, 56u) ? (int)gr32(bp + 52u) : -1,
               nodes,
               i.sample_n > 0 && guest_ok(gr32(i.world + B2_BODY_LIST), 36u)
                   ? (double)U2F(gr32(gr32(i.world + B2_BODY_LIST) + 20u)) : 0.0,
               i.sample_n > 0 && guest_ok(gr32(i.world + B2_BODY_LIST), 36u)
                   ? (double)U2F(gr32(gr32(i.world + B2_BODY_LIST) + 32u)) : 0.0);
        for (nwalk = 0; b && nwalk < 6; nwalk++) {
            uint32_t fx, nid, node;
            int prx;
            if (!guest_ok(b, 116u)) break;
            fx = gr32(b + 108u);
            prx = fx && guest_ok(fx, 44u) ? (int)gr32(fx + 40u) : -2;
            nid = (prx >= 0) ? (uint32_t)prx : 0xffffffffu;
            node = (nodes && nid != 0xffffffffu) ? nodes + (nid << 5) : 0;
            rt_log("phys %s aabb t%d y=%.2f prx=%d fx=%.1f,%.1f..%.1f,%.1f node=%.1f,%.1f..%.1f,%.1f ud=%08x cat=%04x",
                   tag ? tag : "-",
                   (int)gr32(b), (double)U2F(gr32(b + 16u)), prx,
                   fx && guest_ok(fx, 16u) ? (double)U2F(gr32(fx + 0u)) : 0.0,
                   fx && guest_ok(fx, 16u) ? (double)U2F(gr32(fx + 4u)) : 0.0,
                   fx && guest_ok(fx, 16u) ? (double)U2F(gr32(fx + 8u)) : 0.0,
                   fx && guest_ok(fx, 16u) ? (double)U2F(gr32(fx + 12u)) : 0.0,
                   node && guest_ok(node, 16u) ? (double)U2F(gr32(node + 0u)) : 0.0,
                   node && guest_ok(node, 16u) ? (double)U2F(gr32(node + 4u)) : 0.0,
                   node && guest_ok(node, 16u) ? (double)U2F(gr32(node + 8u)) : 0.0,
                   node && guest_ok(node, 16u) ? (double)U2F(gr32(node + 12u)) : 0.0,
                   node && guest_ok(node, 20u) ? gr32(node + 16u) : 0,
                   fx && guest_ok(fx, 48u) ? (unsigned)(gr32(fx + 44u) & 0xffffu) : 0);
            b = gr32(b + 104u);
        }
    }
}

/* Lua cameraFunction owns the play view (castle intro, then the bird camera,
 * pan). Device test 4 of RECIPE 7 fitted an AABB around every body, logged
 * `camfit ... x=-432..2006 scale=0.200 left=-413 top=-1420`, and lua printed
 * "Camera target object outside level limits". Those x bounds are the
 * slingshot-to-castle span (plus the 2000-unit ground), not bodies flying
 * off: the same log has rest=1, dynY=-15..-7, contacts=16. The 2400x1600
 * window left black/white voids (sky tiles only cover the real ~800-wide
 * shot), HUD and the pause page sat in the wrong place, and structures
 * looked like they were floating. iPhone tables are already sx~0.3..0.7
 * at 480x320; only iPad tables (sx>1, screenHeight 1024) need a rescale. */
void ab_cam_fit_play(void) {
    uint32_t glu = game_lua(), objs, cam;
    float scale, left, top, cx, cy, ns, factor;
    if (!glu) return;
    ab_lua_iphone_screen();
    ab_lua_set_bool("showBG", 1);
    scale = U2F(gr32(glu + AB_CAM_SCALE));
    if (!(scale > 1.05f)) return;
    left = U2F(gr32(glu + AB_CAM_LEFT));
    top = U2F(gr32(glu + AB_CAM_TOP));
    /* Level 1 birdCameraData is 1680x1024, sx~2.25. loadLevelInternal
     * multiplies sx by lua screenHeight / camera.screenHeight; if that
     * did not run, shrink around the same look-at. */
    factor = AB_VIEW_H / 1024.f;
    ns = scale * factor;
    if (ns < 0.35f) ns = 0.35f;
    if (ns > 0.90f) ns = 0.90f;
    cx = left + 0.5f * AB_VIEW_W / scale;
    cy = top + 0.5f * AB_VIEW_H / scale;
    left = cx - 0.5f * AB_VIEW_W / ns;
    top = cy - 0.5f * AB_VIEW_H / ns;
    rt_call("setWorldScale", AB_SetWorldScale, glu, F2U(ns), 0, 0, 0, 0);
    rt_call("setTopLeft", AB_SetTopLeft, glu, F2U(left), F2U(top), 0, 0, 0);
    ab_lua_set_number("worldScale", ns);
    objs = lua_subtable(glu, "objects");
    cam = lua_subtable(objs, "castleCameraData");
    lua_table_set_num(cam, "sx", ns);
    lua_table_set_num(cam, "sy", ns);
    lua_table_set_num(cam, "screenWidth", AB_VIEW_W);
    lua_table_set_num(cam, "screenHeight", AB_VIEW_H);
    cam = lua_subtable(objs, "birdCameraData");
    lua_table_set_num(cam, "sx", ns);
    lua_table_set_num(cam, "sy", ns);
    lua_table_set_num(cam, "screenWidth", AB_VIEW_W);
    lua_table_set_num(cam, "screenHeight", AB_VIEW_H);
    rt_log("cam iphone-rescale from %.3f to %.3f left=%.0f top=%.0f",
           (double)scale, (double)ns, (double)left, (double)top);
}
