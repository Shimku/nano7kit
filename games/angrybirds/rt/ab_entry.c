/* ab_entry.c: drives the recompiled Ka3D engine the way the iOS shell did (nano7kit, MIT).
 * Skip UIApplicationMain / ObjC. Allocate IphoneOSInterface + EGL_Context(480,320,landscape)
 * and call framework::init; each frame GameApp::update; touches App::touchBegan/Moved/Ended.
 * TouchEvent is 3 words (pointer id, x, y) in 480x320 landscape pixels. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "rt.h"
#include "rt_plat.h"
#include "fngl.h"
#include "ab_entry.h"
#include "rt_entry.h"
#ifndef AB_BeginScene
#define AB_BeginScene 0xa8880u
#endif

static uint32_t osi, ctx, app, str_data, str_docs, view_rect;
static int started;
static struct { int down, id; float x, y, gx, gy; } fed[AB_FINGERS];
static int lbutton_held;
static int pinch_on;
static float pinch_d0, pinch_s0, pinch_cx, pinch_cy, pinch_play;
static int cam_user;                    /* 1: player pinch/pan latched over cameraFunction */
static float user_scale, user_left, user_top;
static int pan_on;
static float pan_gx0, pan_gy0, pan_left0, pan_top0;
/* Play-camera ground/sky and bird/castle look-at, captured once per level.
 * Pinch/pan write birdCameraData.px (cameraFunction draws from that, not
 * from setTopLeft). Restore these on double-tap so the slingshot view
 * comes back. */
static float play_ground_b, play_top0, play_left0;
static float lvl_bird_px, lvl_bird_py, lvl_bird_sx, lvl_bird_left, lvl_bird_top;
static float lvl_castle_px, lvl_castle_py, lvl_castle_sx;
static float lvl_left, lvl_right;
static float lvl_min_world;
static int lvl_cam_ok;
static uint64_t tap_us;
static float tap_x, tap_y;
static char watch_name[32], watch_pending[32];
static int watch_wait, watch_dyn0, cam_hold;
static float watch_x0;
static uint32_t step_n;
static int guest_ok(uint32_t a, uint32_t n);
static uint32_t intern_str(const char *s);
static void ab_lua_iphone_screen(void);
static void ab_lua_set_number(const char *name, float v);
static void ab_lua_set_bool(const char *name, int on);
static uint32_t game_lua(void);
static void ab_apply_pinch(void);
static void ab_apply_pan(void);
static void ab_commit_user_cam(void);
static float cam_get_num(uint32_t tbl, const char *k);
static float ab_level_min_scale(float play);
static float ab_sky_extra(float play);
static void ab_capture_level_cam(void);
static void ab_restore_level_cam_tables(void);
static void ab_clamp_user_view(void);
static void ab_reset_play_cam(void);
static void ab_log_level(const char *name);
static void ab_iphone_cameras(void);
static void ab_apply_bird_cam(void);
static void ab_lua_gc(void);

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

/* Fruit Ninja keeps fe_scale/fe_off fixed after reset: the 480x320 landscape
 * fills 240x432 with Home on the right. Do not pan or zoom this map; only
 * lua cameraFunction moves the world. */
static void reset_view(void) {
    int i;
    fe_scale = 0.75f;
    fe_scale_y = 0.9f;
    fe_off_x = 0.f;
    fe_off_y = 0.f;
    lbutton_held = 0;
    pinch_on = 0;
    for (i = 0; i < AB_FINGERS; i++) memset(&fed[i], 0, sizeof fed[i]);
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
    plat_log_step("ab_start", rt_heap_used());
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
#if !AB_LuaGetTable
#undef AB_LuaGetTable
#define AB_LuaGetTable 0x7ccf8u
#endif
#ifndef AB_LuaGetNumber
#define AB_LuaGetNumber 0x7c858u
#endif
#if !AB_LuaGetNumber
#undef AB_LuaGetNumber
#define AB_LuaGetNumber 0x7c858u
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
#ifndef AB_StringCStr
#define AB_StringCStr 0x4a89cu
#endif
#ifndef AB_StringD1
#define AB_StringD1 0x4abf4u
#endif
#define AB_LBUTTON 57u
/* GameLua::update copies App::touches().size() (App+628) into lua `touches`.
 * updateGame starts a slingshot only when that count is exactly 1; GameApp::update
 * pinches when it is exactly 2 (App+636/640 -> lua zoomLevel). TouchEvent is
 * 12 bytes (int id, x, y) in 480x320 landscape pixels. Pin the vector from the
 * live fingers rather than forcing count=1 / id=0. */
static int finger_down_count(void) {
    int n = 0, i;
    for (i = 0; i < AB_FINGERS; i++) if (fed[i].down) n++;
    return n;
}

static void pin_touch_count(uint32_t a) {
    uint32_t data;
    int n = 0, i;
    if (!a || !guest_ok(a, 636u)) return;
    data = gr32(a + 624u);
    n = finger_down_count();
    /* Two fingers are our pinch. GameApp::update would also pinch App+636
     * (lua zoomLevel) and doItAllCamera would fight the latched scale.
     * One finger while zoomed out is our pan; lua would treat it as a
     * slingshot pull or a castle-camera sweep. */
    if (n >= 2) n = 0;
    else if (n == 1 && cam_user && user_scale < pinch_play - 0.02f) n = 0;
    if (n > 0) {
        if (!data || !guest_ok(data, (uint32_t)n * 12u)) {
            data = g_malloc(12u * AB_FINGERS);
            if (!data) return;
            memset(G8(data), 0, 12u * AB_FINGERS);
            gw32(a + 624u, data);
            gw32(a + 632u, (uint32_t)AB_FINGERS);
        }
        n = 0;
        for (i = 0; i < AB_FINGERS; i++) {
            int32_t ix, iy;
            uint32_t slot;
            if (!fed[i].down) continue;
            ix = (int32_t)(fed[i].gx + (fed[i].gx < 0.f ? -0.5f : 0.5f));
            iy = (int32_t)(fed[i].gy + (fed[i].gy < 0.f ? -0.5f : 0.5f));
            slot = (uint32_t)n * 12u;
            gw32(data + slot, (uint32_t)fed[i].id);
            gw32(data + slot + 4u, (uint32_t)ix);
            gw32(data + slot + 8u, (uint32_t)iy);
            n++;
        }
        gw32(a + 628u, (uint32_t)n);
    } else {
        gw32(a + 628u, 0u);
    }
}

static void sync_lbutton(int id, float gx, float gy) {
    int want, n;
    int32_t ix, iy;
    if (!app) return;
    n = finger_down_count();
    want = (n == 1 && fed[0].down);
    ix = (int32_t)(gx + (gx < 0.f ? -0.5f : 0.5f));
    iy = (int32_t)(gy + (gy < 0.f ? -0.5f : 0.5f));
    /* menus and the slingshot read LBUTTON + mouseMove for finger 0 only.
     * A second finger is a pinch: drop LBUTTON so lua does not grab the pouch. */
    if (id == 0 && AB_MouseMove)
        rt_call("mouseMove", AB_MouseMove, app, (uint32_t)ix, (uint32_t)iy, 0, 0, 0);
    if (want && !lbutton_held) {
        G8(app + 532u + AB_LBUTTON)[0] = 1;
        if (AB_KeyDown) rt_call("keyDown", AB_KeyDown, app, AB_LBUTTON, 0, 0, 0, 0);
        lbutton_held = 1;
    } else if (!want && lbutton_held) {
        G8(app + 532u + AB_LBUTTON)[0] = 0;
        if (AB_KeyUp) rt_call("keyUp", AB_KeyUp, app, AB_LBUTTON, 0, 0, 0, 0);
        lbutton_held = 0;
    } else {
        G8(app + 532u + AB_LBUTTON)[0] = (uint8_t)(want ? 1 : 0);
    }
}

static void send_touch(int began, int moved, int ended, int id, float gx, float gy) {
    static uint32_t ev;
    uint32_t glu, app2;
    int32_t ix, iy;
    uint32_t fn = 0;
    if (!ev) ev = g_malloc(16);
    ix = (int32_t)(gx + (gx < 0.f ? -0.5f : 0.5f));
    iy = (int32_t)(gy + (gy < 0.f ? -0.5f : 0.5f));
    gw32(ev, (uint32_t)id);
    gw32(ev + 4, (uint32_t)ix);
    gw32(ev + 8, (uint32_t)iy);
    if (began) fn = AB_TouchBegan;
    else if (ended) fn = AB_TouchEnded;
    else fn = AB_TouchMoved;
    if (fn && app) rt_call("touch", fn, app, ev, 0, 0, 0, 0);
    if (!app) return;
    glu = gr32(app + 652u);
    app2 = guest_ok(glu, 32u) ? gr32(glu + 28u) : 0;
    pin_touch_count(app);
    if (app2 && app2 != app) pin_touch_count(app2);
    sync_lbutton(id, gx, gy);
    if (getenv("AB_TOUCH_LOG"))
        rt_log("touch b=%d m=%d e=%d id=%d game=%d,%d lua=%08x app2=%08x n=%d n2=%d kDown=%u kHold=%u kUp=%u zoom=%g prev=%g",
               began, moved, ended, id, ix, iy, glu, app2,
               guest_ok(app, 632u) ? (int)gr32(app + 628u) : -1,
               guest_ok(app2, 632u) ? (int)gr32(app2 + 628u) : -1,
               G8(app + 721u)[0], G8(app + 532u + AB_LBUTTON)[0], G8(app + 807u)[0],
               guest_ok(app, 644u) ? (double)U2F(gr32(app + 636u)) : 0.0,
               guest_ok(app, 644u) ? (double)U2F(gr32(app + 640u)) : 0.0);
}

void ab_touch(int id, int down, float px, float py) {
    float gx, gy;
    int began, moved, ended;
    if (id < 0 || id >= AB_FINGERS) id = 0;
    panel_to_game(px, py, &gx, &gy);
    began = down && !fed[id].down;
    moved = down && fed[id].down;
    ended = !down && fed[id].down;
    if (!began && !moved && !ended) return;
    fed[id].id = id;
    fed[id].x = px;
    fed[id].y = py;
    fed[id].gx = gx;
    fed[id].gy = gy;
    fed[id].down = down != 0;
    send_touch(began, moved, ended, id, gx, gy);
    if (ended && id == 0 && cam_user && finger_down_count() == 0) {
        uint64_t now = plat_time_us();
        float dx = px - tap_x, dy = py - tap_y;
        if (tap_us && now - tap_us < 400000ull && dx * dx + dy * dy < 40.f * 40.f)
            ab_reset_play_cam();
        tap_us = now;
        tap_x = px;
        tap_y = py;
    }
}

void ab_frame_q(float dt, const ab_tsample *q, int n) {
    int i;
    if (!started) return;
    if (fe_lowmem) return;
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
    for (i = 0; i < n; i++) {
        int id = q[i].id;
        if (id < 0 || id >= AB_FINGERS) id = 0;
        ab_touch(id, q[i].down, q[i].x, q[i].y);
    }
    if (dt > 0.1f) dt = 0.1f;
    if (dt < 0.001f) dt = 0.001f;
    fe_stat_tris = 0;
    fe_stat_draws = 0;
    /* Identity GL stacks + the iOS 480x320 ortho, then one GameApp::update.
     * update already runs lua cameraFunction and drawGame. A second lua
     * drawGame here painted the world on top of the pause overlay (device
     * test 4, PAUSED 1-2 clipped). Do not AABB-fit the camera before draw:
     * that chased every body, zoomed to 0.20 and left black/white voids.
     * Pinch/pan must run before update so this frame's draw uses the
     * latched camera (after-draw apply left the screenshot one gesture late
     * and cameraFunction could fight the latch). */
    ab_apply_pinch();
    ab_apply_pan();
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
    ab_watch_level();
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
    strncpy(watch_name, name, sizeof watch_name - 1);
    watch_name[sizeof watch_name - 1] = 0;
    ab_log_level(name);
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

static int guest_ok(uint32_t a, uint32_t n) {
    return a >= g_lo && a + n <= g_hi && a + n >= a;
}

static uint32_t game_lua(void) {
    uint32_t glu = app ? gr32(app + AB_LUA_OFF) : 0;
    return guest_ok(glu, AB_CAM_SCALE + 4u) ? glu : 0;
}

static uint32_t intern_str(const char *s) {
    static const char *keys[96];
    static uint32_t ptrs[96];
    static int n;
    int i;
    if (!s) return 0;
    for (i = 0; i < n; i++)
        if (keys[i] == s || (keys[i] && strcmp(keys[i], s) == 0)) return ptrs[i];
    if (n >= 96) return g_strdup(s);
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

static void ab_cam_write_table(uint32_t cam, float sx, float px, float py, float left, float top) {
    if (!cam) return;
    (void)left;
    (void)top;
    lua_table_set_num(cam, "sx", sx);
    lua_table_set_num(cam, "sy", sx);
    lua_table_set_num(cam, "px", px);
    lua_table_set_num(cam, "py", py);
    lua_table_set_num(cam, "screenWidth", AB_VIEW_W);
    lua_table_set_num(cam, "screenHeight", AB_VIEW_H);
}

static void ab_commit_user_cam(void) {
    uint32_t glu = game_lua(), objs, scr;
    float px, py, vw, vh;
    if (!glu || !cam_user) return;
    if (!(user_scale > 0.10f && user_scale < 1.20f)) return;
    px = user_left + 0.5f * AB_VIEW_W / user_scale;
    py = user_top + 0.5f * AB_VIEW_H / user_scale;
    vw = AB_VIEW_W / user_scale;
    vh = AB_VIEW_H / user_scale;
    (void)vw;
    (void)vh;
    rt_call("setWorldScale", AB_SetWorldScale, glu, F2U(user_scale), 0, 0, 0, 0);
    rt_call("setTopLeft", AB_SetTopLeft, glu, F2U(user_left), F2U(user_top), 0, 0, 0);
    /* doItAllCamera reads bird/castle sx and px, plus zoomLevel. Keep those
     * on the latched view. Two-finger GameApp pinch is suppressed in
     * pin_touch_count so zoomLevel is not a competing scale. */
    ab_lua_set_number("worldScale", user_scale);
    ab_lua_set_number("currentZoomedScale", user_scale);
    ab_lua_set_number("zoomLevel", user_scale);
    ab_lua_set_number("oldZoomLevel", user_scale);
    ab_lua_set_number("animationWorldScale", user_scale);
    ab_lua_set_number("animationWorldScale2", user_scale);
    ab_lua_set_number("cameraAnimationSlider", 1.f);
    ab_lua_set_number("cameraAnimationSliderTarget", 1.f);
    ab_lua_set_bool("useLevelLimits", 0);
    if (app && guest_ok(app, 644u)) {
        gw32(app + 636u, F2U(user_scale));
        gw32(app + 640u, F2U(user_scale));
    }
    objs = lua_subtable(glu, "objects");
    if (!objs) return;
    ab_cam_write_table(lua_subtable(objs, "birdCameraData"),
                       user_scale, px, py, user_left, user_top);
    ab_cam_write_table(lua_subtable(objs, "castleCameraData"),
                       user_scale, px, py, user_left, user_top);
    scr = lua_subtable(glu, "animationScreen");
    if (scr) {
        lua_table_set_num(scr, "x", px);
        lua_table_set_num(scr, "y", py);
    }
}

/* GameApp::update writes App+636 = startZoom * (newDist/oldDist) when
 * touches().size()==2, then GameLua::update copies that into lua zoomLevel.
 * lua cameraFunction / setWorldScale then stores the play camera back into
 * 636 and 640, so the pinch never sticks. Latch the ratio here, after
 * update, from minWorldScale (whole level) to the play camera. Fingers
 * apart zoom out. */
static void ab_apply_pinch(void) {
    uint32_t glu;
    float d, scale, left, top, ns, minz, maxz;
    if (finger_down_count() != 2 || !fed[0].down || !fed[1].down) {
        pinch_on = 0;
        return;
    }
    pan_on = 0;
    d = hypotf(fed[0].gx - fed[1].gx, fed[0].gy - fed[1].gy);
    if (d < 4.f) return;
    glu = game_lua();
    if (!glu) return;
    scale = cam_user && user_scale > 1e-4f ? user_scale : U2F(gr32(glu + AB_CAM_SCALE));
    if (!(scale > 1e-4f)) return;
    left = cam_user ? user_left : U2F(gr32(glu + AB_CAM_LEFT));
    top = cam_user ? user_top : U2F(gr32(glu + AB_CAM_TOP));
    if (!pinch_on) {
        pinch_on = 1;
        pinch_d0 = d;
        pinch_s0 = scale;
        pinch_cx = left + 0.5f * AB_VIEW_W / scale;
        pinch_cy = top + 0.5f * AB_VIEW_H / scale;
        if (!(pinch_play > 0.22f && pinch_play < 1.05f)) pinch_play = scale;
        if (!(play_ground_b > -4000.f && play_ground_b < 4000.f) || play_ground_b == 0.f)
            play_ground_b = top + AB_VIEW_H / scale;
        if (play_top0 == 0.f) play_top0 = top;
        if (!lvl_cam_ok) ab_capture_level_cam();
        return;
    }
    ns = pinch_s0 * (pinch_d0 / d);
    minz = ab_level_min_scale(pinch_play > 1e-4f ? pinch_play : scale);
    maxz = pinch_play > minz ? pinch_play : 0.70f;
    if (maxz < minz + 0.02f) maxz = minz + 0.02f;
    if (maxz > 1.05f) maxz = 1.05f;
    if (ns < minz) ns = minz;
    if (ns > maxz) ns = maxz;
    cam_user = 1;
    user_scale = ns;
    {
        float t, far_x, look_x, extra;
        t = (pinch_play - ns) / (pinch_play - minz);
        if (!(t > 0.f)) t = 0.f;
        if (t > 1.f) t = 1.f;
        /* Stop short of the castle so a later one-finger pan still moves. */
        far_x = lvl_cam_ok ? lvl_castle_px : pinch_cx + 700.f;
        if (!(far_x > pinch_cx + 80.f) && lvl_cam_ok && lvl_right > pinch_cx + 80.f)
            far_x = pinch_cx + 0.45f * (lvl_right - pinch_cx);
        look_x = pinch_cx + t * 0.08f * (far_x - pinch_cx);
        user_left = look_x - 0.5f * AB_VIEW_W / ns;
        extra = ab_sky_extra(pinch_play);
        user_top = play_ground_b - AB_VIEW_H / ns;
        if (play_top0 != 0.f && user_top < play_top0 - extra)
            user_top = play_top0 - extra;
        ab_clamp_user_view();
    }
    cam_hold = 0;
    ab_commit_user_cam();
    {
        static float last_ns;
        if (ns != last_ns) {
            last_ns = ns;
            rt_log("pinch scale=%.3f min=%.3f play=%.3f left=%.1f top=%.1f",
                   (double)ns, (double)minz, (double)maxz,
                   (double)user_left, (double)user_top);
        }
    }
}

/* One-finger drag on empty space while zoomed out pans the play camera.
 * At play scale a single finger is the slingshot. */
static void ab_apply_pan(void) {
    float scale, gx, gy;
    if (pinch_on || finger_down_count() != 1 || !fed[0].down) {
        pan_on = 0;
        return;
    }
    if (!cam_user || !(user_scale < pinch_play - 0.02f)) {
        pan_on = 0;
        return;
    }
    scale = user_scale > 1e-4f ? user_scale : 0.4f;
    gx = fed[0].gx;
    gy = fed[0].gy;
    if (!pan_on) {
        pan_on = 1;
        pan_gx0 = gx;
        pan_gy0 = gy;
        pan_left0 = user_left;
        pan_top0 = user_top;
        return;
    }
    user_left = pan_left0 + (pan_gx0 - gx) / scale;
    user_top = pan_top0 + (pan_gy0 - gy) / scale;
    ab_clamp_user_view();
    ab_commit_user_cam();
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

static const char *ab_level_name(void) {
    uint32_t glu = game_lua(), p;
    static uint32_t slot;
    if (!glu || !AB_LuaGetString) return "";
    if (!slot) {
        slot = g_malloc(16);
        if (slot) memset(G8(slot), 0, 16);
    }
    if (!slot) return "";
    if (gr32(slot) && AB_StringD1)
        rt_call("StringD1", AB_StringD1, slot, 0, 0, 0, 0, 0);
    memset(G8(slot), 0, 16);
    rt_call("getString", AB_LuaGetString, slot, glu, intern_str("levelName"), 0, 0, 0);
    p = AB_StringCStr ? rt_call("cstr", AB_StringCStr, slot, 0, 0, 0, 0, 0) : gr32(slot);
    if (!p || !guest_ok(p, 1u)) return "";
    return (const char *)G8(p);
}

static float cam_get_num(uint32_t tbl, const char *k) {
    if (!tbl || !k || !AB_LuaGetNumber) return 0.f;
    return U2F(rt_call("getNumber", AB_LuaGetNumber, tbl, intern_str(k), 0, 0, 0, 0));
}

/* Sky tiles cover about one play-view height plus a bit. Zooming past that
 * paints white (below ground, portrait-left) and cyan (past sky, portrait-right). */
static float ab_sky_extra(float play) {
    float play_h, extra;
    if (!(play > 0.20f && play < 1.10f)) play = 0.70f;
    play_h = AB_VIEW_H / play;
    extra = play_h * 0.42f;
    if (extra < 170.f) extra = 170.f;
    if (extra > 210.f) extra = 210.f;
    return extra;
}

static void ab_capture_level_cam(void) {
    uint32_t glu = game_lua(), objs, bird, castle;
    float bpx, bpy, cpx, cpy, sx, sy, bleft, bright, top;
    if (!glu) return;
    objs = lua_subtable(glu, "objects");
    bird = objs ? lua_subtable(objs, "birdCameraData") : 0;
    castle = objs ? lua_subtable(objs, "castleCameraData") : 0;
    if (!bird) return;
    bpx = cam_get_num(bird, "px");
    bpy = cam_get_num(bird, "py");
    sx = cam_get_num(bird, "sx");
    sy = cam_get_num(bird, "sy");
    if (!(sx > 0.15f) && sy > 0.15f) sx = sy;
    bleft = cam_get_num(bird, "left");
    bright = cam_get_num(bird, "right");
    top = cam_get_num(bird, "top");
    cpx = castle ? cam_get_num(castle, "px") : 0.f;
    cpy = castle ? cam_get_num(castle, "py") : 0.f;
    /* apply_bird_cam used to copy bird.px onto castle, so span was 0 and
     * pinch fell back to a 0.32 floor around the slingshot. Prefer bird.right
     * when castle.px is missing or still the bird look-at. */
    if (!(cpx > bpx + 80.f) && !(cpx < bpx - 80.f)) {
        if (bright > bpx + 80.f) cpx = 0.55f * bpx + 0.45f * bright;
        else cpx = bpx + 700.f;
    }
    if (!(bright > bpx + 40.f)) bright = (cpx > bpx ? cpx : bpx) + 400.f;
    if (!(bleft < bpx - 20.f)) bleft = bpx - 350.f;
    if (!(sx > 0.15f && sx < 1.10f)) sx = 0.60f;
    if (!(top < bpy)) top = bpy - 0.5f * AB_VIEW_H / sx;
    lvl_bird_px = bpx;
    lvl_bird_py = bpy;
    lvl_bird_sx = sx;
    lvl_bird_left = bleft;
    lvl_bird_top = top;
    lvl_castle_px = cpx;
    lvl_castle_py = cpy != 0.f ? cpy : bpy;
    lvl_castle_sx = castle ? cam_get_num(castle, "sx") : sx;
    if (!(lvl_castle_sx > 0.15f && lvl_castle_sx < 1.10f)) lvl_castle_sx = sx;
    lvl_left = bleft;
    lvl_right = bright;
    lvl_min_world = 0.f;
    if (AB_LuaGetNumber)
        lvl_min_world = U2F(rt_call("getNumber", AB_LuaGetNumber, glu, intern_str("minWorldScale"), 0, 0, 0, 0));
    lvl_cam_ok = 1;
    rt_log("cam extents bird=%.0f,%.0f castle=%.0f left=%.0f right=%.0f top=%.0f sx=%.3f min=%.3f",
           (double)bpx, (double)bpy, (double)cpx, (double)bleft, (double)bright,
           (double)top, (double)sx, (double)lvl_min_world);
}

static void ab_restore_level_cam_tables(void) {
    uint32_t glu = game_lua(), objs;
    if (!lvl_cam_ok || !glu) return;
    objs = lua_subtable(glu, "objects");
    if (!objs) return;
    ab_cam_write_table(lua_subtable(objs, "birdCameraData"),
                       lvl_bird_sx, lvl_bird_px, lvl_bird_py,
                       lvl_bird_left, lvl_bird_top);
    ab_cam_write_table(lua_subtable(objs, "castleCameraData"),
                       lvl_castle_sx, lvl_castle_px, lvl_castle_py,
                       lvl_castle_px - 0.5f * AB_VIEW_W / lvl_castle_sx,
                       lvl_castle_py - 0.5f * AB_VIEW_H / lvl_castle_sx);
}

static void ab_clamp_user_view(void) {
    float vw, vh, min_left, max_left, extra, play;
    if (!(user_scale > 0.10f)) return;
    vw = AB_VIEW_W / user_scale;
    vh = AB_VIEW_H / user_scale;
    play = pinch_play > 0.20f ? pinch_play : user_scale;
    extra = ab_sky_extra(play);
    if (play_ground_b != 0.f) {
        float max_b = play_ground_b + 24.f;
        if (user_top + vh > max_b) user_top = max_b - vh;
    }
    if (play_top0 != 0.f && user_top < play_top0 - extra)
        user_top = play_top0 - extra;
    if (lvl_cam_ok && lvl_right > lvl_left + 100.f) {
        float max_look, play_w;
        min_left = lvl_left - 40.f;
        play_w = pinch_play > 0.20f ? AB_VIEW_W / pinch_play : vw;
        max_look = lvl_bird_px + 0.55f * play_w;
        /* L2 look-at 423 still drew tiles; 628 was a wood close-up.
         * L3 look-at 455 was good, 584 was not. */
        if (max_look > 430.f) max_look = 430.f;
        max_left = max_look - 0.5f * vw;
        if (max_left < min_left)
            user_left = min_left;
        else {
            if (user_left < min_left) user_left = min_left;
            if (user_left > max_left) user_left = max_left;
        }
    }
}

/* lua minWorldScale is the iPhone whole-level floor. If it is missing or
 * stuck at the play camera (c9880d1 pinch 0.704 -> 0.692), fit bird and
 * castle look-at into 480x320 so two fingers can show the pigs. Sky tiles
 * do not cover that whole span on long levels, so the floor is the
 * ground-anchored height, not a blind 0.32. */
static float ab_level_min_scale(float play) {
    uint32_t glu = game_lua(), objs, bird, castle;
    float mn = 0.22f, lua_mn, bpx, cpx, span, fit, mn_y, extra, play_h;
    if (!(play > 0.20f && play < 1.10f)) play = 0.70f;
    extra = ab_sky_extra(play);
    play_h = AB_VIEW_H / play;
    mn_y = AB_VIEW_H / (play_h + extra);
    if (AB_LuaGetNumber && glu) {
        lua_mn = U2F(rt_call("getNumber", AB_LuaGetNumber, glu, intern_str("minWorldScale"), 0, 0, 0, 0));
        if (lua_mn > 0.08f && lua_mn < 0.90f) mn = lua_mn;
    }
    if (lvl_cam_ok && lvl_right > lvl_left + 400.f) {
        span = lvl_right - lvl_left;
        fit = AB_VIEW_W / (span + 80.f);
        if (fit > 0.12f && fit < play - 0.04f) mn = fit;
    } else if (!(mn < play - 0.04f) && glu) {
        objs = lua_subtable(glu, "objects");
        bird = objs ? lua_subtable(objs, "birdCameraData") : 0;
        castle = objs ? lua_subtable(objs, "castleCameraData") : 0;
        if (bird && castle) {
            bpx = cam_get_num(bird, "px");
            cpx = cam_get_num(castle, "px");
            span = bpx > cpx ? bpx - cpx : cpx - bpx;
            span += AB_VIEW_W / play;
            if (span > 500.f) {
                fit = AB_VIEW_W / span;
                if (fit > 0.12f && fit < play - 0.04f) mn = fit;
            }
        }
    }
    if (mn < mn_y) mn = mn_y;
    if (!(mn < play - 0.04f)) mn = play * 0.55f;
    if (mn < 0.22f) mn = 0.22f;
    if (mn > play - 0.04f) mn = play * 0.55f;
    return mn;
}

/* Level files ship iPad tables (sx~2.25, screenHeight 1024). loadLevelInternal
 * rescales by lua screenHeight / camera.screenHeight on the first map load,
 * then lua screenHeight becomes 320. The next level's 1024 tables are then
 * multiplied by 320/320 if lua copied camera.screenHeight back, or not
 * rescaled at all if loadNextLevel skips that pass. Draw uses those tables:
 * sx 2.25 on a 480x320 view is one slingshot filling the panel, the rest
 * white (GL clear; sky tiles do not cover that zoom). Pinch overrides
 * C++ worldScale after update, which is why zoom-out showed the real scene. */
static int ab_iphone_cam_table(const char *which) {
    uint32_t glu = game_lua(), objs, cam;
    float sx, sy, sh, factor, nsx, nsy;
    if (!glu || !which) return 0;
    objs = lua_subtable(glu, "objects");
    if (!objs) return 0;
    cam = lua_subtable(objs, which);
    if (!cam) return 0;
    sx = cam_get_num(cam, "sx");
    sy = cam_get_num(cam, "sy");
    sh = cam_get_num(cam, "screenHeight");
    if (!(sh > 400.f) && !(sx > 1.05f)) return 0;
    factor = AB_VIEW_H / (sh > 1.f ? sh : 1024.f);
    nsx = sx * factor;
    nsy = (sy > 1e-4f ? sy : sx) * factor;
    if (nsx < 0.22f) nsx = 0.22f;
    if (nsx > 1.05f) nsx = 0.70f;
    if (nsy < 0.22f) nsy = 0.22f;
    if (nsy > 1.05f) nsy = nsx;
    lua_table_set_num(cam, "sx", nsx);
    lua_table_set_num(cam, "sy", nsy);
    lua_table_set_num(cam, "screenWidth", AB_VIEW_W);
    lua_table_set_num(cam, "screenHeight", AB_VIEW_H);
    rt_log("cam %s iphone sx=%.3f->%.3f sh=%.0f->%d",
           which, (double)sx, (double)nsx, (double)sh, (int)AB_VIEW_H);
    return 1;
}

static void ab_iphone_cameras(void) {
    ab_lua_iphone_screen();
    ab_iphone_cam_table("castleCameraData");
    ab_iphone_cam_table("birdCameraData");
}

static void ab_apply_bird_cam(void) {
    uint32_t glu = game_lua(), objs, cam, castle, scr;
    float sx, sy, px, py, left, top;
    if (!glu) return;
    objs = lua_subtable(glu, "objects");
    if (!objs) return;
    cam = lua_subtable(objs, "birdCameraData");
    if (!cam) return;
    sx = cam_get_num(cam, "sx");
    sy = cam_get_num(cam, "sy");
    px = cam_get_num(cam, "px");
    py = cam_get_num(cam, "py");
    if (sx > 1.05f || sy > 1.05f) {
        float sh = cam_get_num(cam, "screenHeight");
        float factor = AB_VIEW_H / (sh > 400.f ? sh : 1024.f);
        if (!(sx > 0.20f) && sy > 0.20f) sx = sy;
        sx *= factor;
    } else if (!(sx > 0.20f && sx < 1.05f)) {
        if (sy > 0.20f && sy < 1.05f) sx = sy;
        else return;
    }
    if (sx < 0.22f) sx = 0.22f;
    if (sx > 1.05f) sx = 0.70f;
    castle = lua_subtable(objs, "castleCameraData");
    if (castle) {
        lua_table_set_num(castle, "sx", sx);
        lua_table_set_num(castle, "sy", sx);
        lua_table_set_num(castle, "px", px);
        lua_table_set_num(castle, "py", py);
    }
    scr = lua_subtable(glu, "animationScreen");
    if (scr) {
        lua_table_set_num(scr, "x", px);
        lua_table_set_num(scr, "y", py);
    }
    left = px - 0.5f * AB_VIEW_W / sx;
    top = py - 0.5f * AB_VIEW_H / sx;
    play_left0 = left;
    play_top0 = top;
    /* Keep a strip of ground inside the view; the play camera's bottom
     * edge is already at the grass line, so zooming with that as the
     * bottom paints white (below-ground) on long levels. */
    play_ground_b = top + AB_VIEW_H / sx - 70.f;
    rt_call("setWorldScale", AB_SetWorldScale, glu, F2U(sx), 0, 0, 0, 0);
    rt_call("setTopLeft", AB_SetTopLeft, glu, F2U(left), F2U(top), 0, 0, 0);
    ab_lua_set_number("worldScale", sx);
    ab_lua_set_number("currentZoomedScale", sx);
    ab_lua_set_number("zoomLevel", sx);
    ab_lua_set_number("oldZoomLevel", sx);
    ab_lua_set_number("animationWorldScale", sx);
    ab_lua_set_number("animationWorldScale2", sx);
    pinch_play = sx;
    {
        static float lsx, lpx, lpy;
        if (sx != lsx || px != lpx || py != lpy) {
            lsx = sx; lpx = px; lpy = py;
            rt_log("cam bird sx=%.3f px=%.0f py=%.0f left=%.0f top=%.0f",
                   (double)sx, (double)px, (double)py, (double)left, (double)top);
        }
    }
}

static void ab_force_theme(void) {
    uint32_t glu = game_lua(), objs, theme;
    float tn;
    char name[16];
    static uint32_t theme_slot;
    extern int be_stat_tex_fail;
    if (!glu) return;
    tn = U2F(rt_call("getNumber", AB_LuaGetNumber, glu, intern_str("currentThemeNumber"), 0, 0, 0, 0));
    if (!(tn >= 1.f && tn <= 12.f)) tn = 1.f;
    snprintf(name, sizeof name, "theme%.0f", (double)tn);
    rt_log("setTheme %s tex=%d fail=%d", name, fe_stat_tex_count, be_stat_tex_fail);
    /* Do not ping-pong theme2/theme1 here: each flip loads the other
     * theme's Image tables into the 2 MB first-fit heap and chapter 1
     * runs out of 80-byte holes by Level 9. C++ setTheme from objects.theme
     * is enough; stale GL names are rebound when the Image is recreated. */
    if (!theme_slot) {
        theme_slot = g_malloc(16);
        if (theme_slot) memset(G8(theme_slot), 0, 16);
    }
    theme = theme_slot;
    objs = lua_subtable(glu, "objects");
    if (theme) {
        if (gr32(theme) && AB_StringD1)
            rt_call("StringD1", AB_StringD1, theme, 0, 0, 0, 0, 0);
        memset(G8(theme), 0, 16);
    }
    if (objs && theme && AB_LuaGetString)
        rt_call("getString", AB_LuaGetString, theme, objs, intern_str("theme"), 0, 0, 0);
    if (glu && theme && AB_SetTheme && gr32(theme))
        rt_call("setTheme", AB_SetTheme, glu, theme, 0, 0, 0, 0);
    rt_log("setTheme after tex=%d fail=%d", fe_stat_tex_count, be_stat_tex_fail);
}

static void ab_reset_play_cam(void) {
    uint32_t glu;
    cam_user = 0;
    pan_on = 0;
    pinch_on = 0;
    ab_restore_level_cam_tables();
    ab_lua_iphone_screen();
    ab_iphone_cameras();
    if (!lvl_cam_ok) ab_capture_level_cam();
    ab_lua_set_bool("showBG", 1);
    ab_lua_set_number("castleCameraTimer", 3.f);
    ab_lua_set_number("cameraResetTimer", 0.f);
    ab_lua_set_number("cameraAnimationSlider", 1.f);
    ab_lua_set_number("cameraAnimationSliderTarget", 1.f);
    ab_lua_set_bool("allowResetToBirdCamera", 1);
    ab_call_lua("defaultCamera");
    ab_apply_bird_cam();
    ab_cam_fit_play();
    glu = game_lua();
    if (!(pinch_play > 0.22f && pinch_play < 1.05f))
        pinch_play = glu ? U2F(gr32(glu + AB_CAM_SCALE)) : 0.60f;
    if (!(pinch_play > 0.22f && pinch_play < 1.05f)) pinch_play = 0.60f;
    if (glu) {
        float sc = U2F(gr32(glu + AB_CAM_SCALE));
        float tp = U2F(gr32(glu + AB_CAM_TOP));
        if (sc > 0.15f && sc < 1.10f) {
            play_top0 = tp;
            play_left0 = U2F(gr32(glu + AB_CAM_LEFT));
            play_ground_b = tp + AB_VIEW_H / sc - 70.f;
            pinch_play = sc;
        }
    }
    ab_lua_set_number("worldScale", pinch_play);
    ab_lua_set_number("currentZoomedScale", pinch_play);
    ab_lua_set_number("zoomLevel", pinch_play);
    ab_lua_set_number("oldZoomLevel", pinch_play);
    ab_lua_set_bool("useLevelLimits", 1);
    pinch_on = 0;
}

static void ab_log_level(const char *name) {
    ab_phys_info i;
    extern int be_stat_tex_fail;
    ab_phys_snapshot(&i);
    {
        uint32_t glu = game_lua();
        float anim = glu ? U2F(rt_call("getNumber", AB_LuaGetNumber, glu, intern_str("animationWorldScale"), 0, 0, 0, 0)) : 0.f;
        rt_log("LEVEL %s scale=%.3f anim=%.3f left=%.1f top=%.1f ctx=%dx%d obj=%d vis=%d/%d tex=%d fail=%d heap=%u hole=%u circ=%d poly=%d shapebad=%d align=%u draws=%d",
               name ? name : "-", (double)i.world_scale, (double)anim, (double)i.cam_left, (double)i.cam_top,
               i.ctx_w, i.ctx_h, i.obj_n, i.vis_all, i.bodies,
               fe_stat_tex_count, be_stat_tex_fail, (unsigned)rt_heap_used(),
               (unsigned)rt_heap_largest_free(), i.circles, i.polygons, i.shape_bad,
               (unsigned)rt_align_faults(), fe_stat_draws);
    }
    plat_log_step(name ? name : "level", ++step_n);
}

void ab_watch_level(void) {
    const char *name;
    ab_phys_info i;
    extern int be_stat_tex_fail;
#ifndef FN_NANO
    if (getenv("AB_NO_WATCH")) return;
#endif
    if (!started) return;
    /* LuaTable::getString throws if the key is NIL. Menu/splash have no
     * levelName yet; wait until a level has objects. */
    if (!watch_name[0] && !watch_pending[0]) {
        ab_phys_snapshot(&i);
        if (i.dynamic_n < 1 && i.obj_n < 1) return;
    }
    name = ab_level_name();
    if (!name || !name[0]) return;
    if (!watch_pending[0] && watch_name[0] && strcmp(watch_name, name) == 0) return;
    if (!watch_pending[0] || strcmp(watch_pending, name) != 0) {
        /* loadNextLevel never ran lua_gc; dead level tables were ~200 KB
         * a level on c9880d1 (hole 1.25 MB -> 386 KB across L1 then L57). */
        ab_lua_gc();
        strncpy(watch_pending, name, sizeof watch_pending - 1);
        watch_pending[sizeof watch_pending - 1] = 0;
        watch_wait = 0;
        ab_phys_snapshot(&i);
        watch_dyn0 = i.dynamic_n;
        watch_x0 = i.sample_n > 0 ? i.sample_x[0] : 0.f;
        rt_log("LEVEL-PRE %s heap=%u hole=%u tex=%d fail=%d dyn=%d",
               name, (unsigned)rt_heap_used(), (unsigned)rt_heap_largest_free(),
               fe_stat_tex_count, be_stat_tex_fail, i.dynamic_n);
        plat_log_step("level-pre", rt_heap_used());
    }
    watch_wait++;
    /* loadNextLevel only writes levelName; updateLoading creates Box2D on a
     * later frame. defaultCamera/setTheme before that uses the previous
     * level's objects and leaves L2+ white. The first level often already
     * has bodies by the time levelName is readable. */
    ab_phys_snapshot(&i);
    if (i.dynamic_n < 1) return;
    /* updateLoading fills camera tables a few frames after levelName.
     * defaultCamera before that copies the previous iPad close-up. */
    if (watch_wait < 16) return;
    if (watch_name[0] && watch_dyn0 > 0 && watch_wait < 90 &&
        i.dynamic_n == watch_dyn0 &&
        i.sample_n > 0 && i.sample_x[0] == watch_x0)
        return;
    strncpy(watch_name, watch_pending, sizeof watch_name - 1);
    watch_name[sizeof watch_name - 1] = 0;
    watch_pending[0] = 0;
    fe_reset_matrices();
    ab_force_theme();
    ab_reset_play_cam();
    cam_hold = 128;
    ab_phys_enable(1);
    ab_log_level(watch_name);
    ab_lua_gc();
}

void ab_load_lua_level(const char *name) {
    if (!name) return;
    cam_reset();
    pinch_on = 0;
    cam_user = 0;
    pan_on = 0;
    lvl_cam_ok = 0;
    ab_lua_gc();
    ab_lua_iphone_screen();
    ab_lua_set_string("levelName", name);
    ab_lua_set_bool("loadingPageDrawn", 1);
    ab_lua_set_bool("showBG", 1);
    rt_log("ab_load_lua_level %s", name);
    fe_reset_matrices();
    ab_call_lua("updateLoading");
    /* lua setTheme no-ops if objects.theme is unchanged (L14 then L15
     * are both theme1) and the previous sky tiles stay behind. C++ setTheme
     * always rebuilds. castleCameraTimer 3 skips the castle intro. */
    ab_force_theme();
    ab_reset_play_cam();
    ab_phys_enable(1);
    ab_lua_gc();
    strncpy(watch_name, name, sizeof watch_name - 1);
    watch_name[sizeof watch_name - 1] = 0;
    ab_phys_log(name);
    ab_log_level(name);
}

static void ab_lua_gc(void) {
    uint32_t glu, L, g;
#ifndef FN_NANO
    if (getenv("AB_REPRO_UNINIT")) return;
#endif
    glu = game_lua();
    if (!glu || !guest_ok(glu, 12u)) return;
    /* LuaObject::call loads this+8 as the lua_State* (F_74dc8 then
     * reads State+8 only when +8 is a LuaState wrapper). Try both. */
    L = gr32(glu + 8u);
    if (L && guest_ok(L, 20u) && guest_ok(gr32(L + 16u), 8u)) {
        /* lua_State.l_G at +16 */
    } else if (L && guest_ok(L, 12u)) {
        L = gr32(L + 8u);
        if (!L || !guest_ok(L, 20u) || !guest_ok(gr32(L + 16u), 8u)) {
            rt_log("lua_gc skip glu=%08x L=%08x", glu, L);
            return;
        }
    } else {
        rt_log("lua_gc skip glu=%08x L=%08x", glu, L);
        return;
    }
    g = gr32(L + 16u);
    (void)g;
    /* lua_gc(L, LUA_GCCOLLECT, 0). Without this the 2 MB first-fit heap
     * fills by Level 9 (used 1.82 MB, 28-byte hole) because lua keeps
     * dead level tables until a full collect. */
    {
        uint32_t hole0 = rt_heap_largest_free();
        rt_call("lua_gc", 0x7e48cu, L, 2u, 0, 0, 0, 0);
        rt_call("lua_gc", 0x7e48cu, L, 2u, 0, 0, 0, 0);
        rt_log("lua_gc hole %u -> %u used %u L=%08x",
               (unsigned)hole0, (unsigned)rt_heap_largest_free(),
               (unsigned)rt_heap_used(), L);
    }
}

void ab_goto_level(const char *name) {
    /* Same path as tapping a map button: set levelName and let lua
     * updateLoading / loadLevelInternal spawn the world. ab_watch_level
     * applies defaultCamera once the new bodies exist. */
    if (!name) return;
    cam_reset();
    pinch_on = 0;
    cam_user = 0;
    pan_on = 0;
    lvl_cam_ok = 0;
    ab_lua_gc();
    ab_lua_iphone_screen();
    ab_lua_set_number("zoomLevel", 0.f);
    ab_lua_set_number("oldZoomLevel", 0.f);
    ab_lua_set_number("currentZoomedScale", 0.f);
    ab_lua_set_string("levelName", name);
    ab_lua_set_bool("loadingPageDrawn", 1);
    ab_lua_set_bool("showBG", 1);
    rt_log("ab_goto_level %s", name);
    plat_log_step("goto", rt_heap_used());
    fe_reset_matrices();
    ab_call_lua("updateLoading");
    ab_iphone_cameras();
    ab_phys_enable(1);
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
                if (o->vis_n < AB_PHYS_VIS) {
                    o->vis_wx[o->vis_n] = wx;
                    o->vis_wy[o->vis_n] = wy;
                    o->vis_n++;
                }
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
        /* count the fixture list as well, in case m_fixtureCount is stale.
         * Box2D 2.1: circle type 0 size 0x14 (radius +8); polygon type 1 size
         * 0x98, m_vertexCount at +148. Reading every shape as a polygon made
         * circles look like nvert garbage and NaN size on the device log. */
        fx = gr32(b + 108u);
        for (fi = 0; fx && fi < 32; fi++) {
            uint32_t sh;
            int st;
            if (!guest_ok(fx, 32u)) break;
            sh = gr32(fx + 28u);
            if (sh && guest_ok(sh, 12u)) {
                st = (int)gr32(sh + 4u);
                if (st == 0) {
                    float rad = U2F(gr32(sh + 8u));
                    o->circles++;
                    if (!(rad == rad) || rad <= 0.f || rad > 50.f) o->shape_bad++;
                } else if (st == 1) {
                    int nv = guest_ok(sh, 152u) ? (int)gr32(sh + 148u) : -1;
                    float rad = U2F(gr32(sh + 8u));
                    o->polygons++;
                    if (nv < 3 || nv > 8 || !(rad == rad)) o->shape_bad++;
                } else {
                    o->shape_bad++;
                }
            }
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
            int st, nv;
            float rad;
            if (!guest_ok(b, 116u)) break;
            fx = gr32(b + 108u);
            sh = fx && guest_ok(fx, 32u) ? gr32(fx + 28u) : 0;
            st = sh && guest_ok(sh, 8u) ? (int)gr32(sh + 4u) : -1;
            rad = sh && guest_ok(sh, 12u) ? U2F(gr32(sh + 8u)) : 0.f;
            nv = (st == 1 && sh && guest_ok(sh, 152u)) ? (int)gr32(sh + 148u) : (st == 0 ? 0 : -1);
            n += snprintf(buf + n, sizeof buf - (size_t)n,
                          " t%d sh=%d nvert=%d r=%.2f prx=%d",
                          (int)gr32(b), st, nv, (double)rad,
                          fx && guest_ok(fx, 44u) ? (int)gr32(fx + 40u) : -2);
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
    uint32_t glu = game_lua();
    float scale, left, top, cx, cy, ns, factor;
    ab_phys_info info;
    if (!glu) return;
    ab_lua_iphone_screen();
    ab_lua_set_bool("showBG", 1);
    /* getTable(castleCameraData) throws on the menu. Only touch tables
     * once a level has objects. */
    ab_phys_snapshot(&info);
    scale = U2F(gr32(glu + AB_CAM_SCALE));
    if ((info.obj_n > 0 || info.dynamic_n > 0) && scale > 1.05f)
        ab_iphone_cameras();
    if (cam_user) {
        ab_lua_set_number("castleCameraTimer", 3.f);
        ab_lua_set_number("cameraResetTimer", 0.f);
        ab_lua_set_number("cameraAnimationSlider", 1.f);
        ab_lua_set_number("cameraAnimationSliderTarget", 1.f);
        ab_commit_user_cam();
        return;
    }
    if (cam_hold > 0 && finger_down_count() == 0) {
        /* loadLevelInternal resets castleCameraTimer; keep skipping the
         * castle intro so L2+ is not a wood block in a white void. Fingers
         * cancel this so pinch still sticks. */
        static uint64_t hold_us;
        uint64_t now = plat_time_us();
        ab_lua_set_number("castleCameraTimer", 3.f);
        ab_lua_set_number("cameraResetTimer", 0.f);
        ab_lua_set_number("cameraAnimationSlider", 1.f);
        ab_lua_set_number("cameraAnimationSliderTarget", 1.f);
        ab_lua_set_number("levelStartTimer", 10.f);
        ab_lua_set_bool("allowResetToBirdCamera", 1);
        if (now != hold_us) {
            hold_us = now;
            cam_hold--;
        }
        ab_apply_bird_cam();
    } else if (finger_down_count() > 0) {
        cam_hold = 0;
    }
    scale = U2F(gr32(glu + AB_CAM_SCALE));
    if (!(scale > 1.05f)) return;
    left = U2F(gr32(glu + AB_CAM_LEFT));
    top = U2F(gr32(glu + AB_CAM_TOP));
    /* C++ worldScale still iPad after cameraFunction copied table sx.
     * Shrink around the same look-at so this frame's numbers match draw
     * on the next update. */
    factor = AB_VIEW_H / 1024.f;
    ns = scale * factor;
    if (ns < 0.22f) ns = 0.22f;
    if (ns > 0.90f) ns = 0.90f;
    cx = left + 0.5f * AB_VIEW_W / scale;
    cy = top + 0.5f * AB_VIEW_H / scale;
    left = cx - 0.5f * AB_VIEW_W / ns;
    top = cy - 0.5f * AB_VIEW_H / ns;
    rt_call("setWorldScale", AB_SetWorldScale, glu, F2U(ns), 0, 0, 0, 0);
    rt_call("setTopLeft", AB_SetTopLeft, glu, F2U(left), F2U(top), 0, 0, 0);
    ab_lua_set_number("worldScale", ns);
    ab_lua_set_number("currentZoomedScale", ns);
    ab_lua_set_number("zoomLevel", ns);
    rt_log("cam iphone-rescale from %.3f to %.3f left=%.0f top=%.0f",
           (double)scale, (double)ns, (double)left, (double)top);
}
