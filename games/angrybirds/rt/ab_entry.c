/* ab_entry.c: drive Ka3D through JNI natives in libangrybirds.so (nano7kit, MIT). */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "ab_entry.h"
#include "fngl.h"
#include "jni.h"
#include "rt_plat.h"
#include "shims.h"
#include "so_load.h"

typedef void (*jni_init_fn)(JNIEnv *, void *, void *, int, int, void *);
typedef void (*jni_void_fn)(JNIEnv *, void *);
typedef void (*jni_resize_fn)(JNIEnv *, void *, int, int);
typedef void (*jni_input_fn)(JNIEnv *, void *, int, float, float, int);
typedef void (*str_ctor_fn)(void *, const char *);
typedef void (*lua_call_fn)(void *, const char *);
typedef void (*load_fn)(void *, void *);
typedef void (*set_bool_fn)(void *, int);
typedef void (*set_f_fn)(void *, float);
typedef void (*set_ff_fn)(void *, float, float);
typedef void (*set_vel_fn)(void *, void *, float, float);
typedef void (*set_num_fn)(void *, const char *, float);
typedef void (*set_str_fn)(void *, const char *, const char *);
typedef void (*set_bname_fn)(void *, const char *, int);
typedef void *(*get_body_fn)(void *, const void *);
typedef void *(*app_get_fn)(void);

static jni_init_fn j_init;
static jni_void_fn j_update, j_deinit;
static jni_resize_fn j_resize;
static jni_input_fn j_input;
static str_ctor_fn str_ctor;
static lua_call_fn lua_call;
static load_fn load_level;
static set_bool_fn set_phys;
static set_f_fn set_scale;
static set_ff_fn set_topleft;
static set_vel_fn set_vel;
static set_num_fn lua_set_num;
static set_str_fn lua_set_str;
static set_bname_fn lua_set_bool;
static get_body_fn get_body;
static app_get_fn app_get;
static int started;
static int cam_hold;
static float hold_scale, hold_left, hold_top;
static uint32_t scale_saved[2], topleft_saved[2];
static int cam_hooks_on;
static const char *so_path = "libangrybirds.so";
static struct { int down, id; float x, y, gx, gy; } fed[AB_FINGERS];
static int pinch_on;
static float pinch_d0, pinch_s0, pinch_cx, pinch_cy;

#define AB_VIEW_W 480.f
#define AB_VIEW_H 320.f
#define AB_LUA_OFF 0x294u
#define AB_WORLD_OFF 0x100u
#define AB_PHYS_ON_OFF 348u
#define AB_CAM_LEFT 352u
#define AB_CAM_TOP 356u
#define AB_CAM_SCALE 364u
#define AB_ACC_OFF 360u
#define AB_PHYS_TO_WORLD 20.f
#define B2_TAIL 0x19000u
#define B2_BODY_LIST (B2_TAIL + 552u)
#define B2_BODY_COUNT (B2_TAIL + 560u)
#define B2_JOINT_COUNT (B2_TAIL + 564u)
#define B2_GRAV_X (B2_TAIL + 568u)
#define B2_GRAV_Y (B2_TAIL + 572u)

static uint32_t ru32(const void *p) { uint32_t v; memcpy(&v, p, 4); return v; }
static float rf32(const void *p) { float v; memcpy(&v, p, 4); return v; }
static void wu32(void *p, uint32_t v) { memcpy(p, &v, 4); }
static void wf32(void *p, float v) { memcpy(p, &v, 4); }

static int guest_ok(void *p, uint32_t n) {
    uint8_t *b = g_so.base;
    uint8_t *a = (uint8_t *)p;
    if (!b || !a) return 0;
    return a >= b && a + n <= b + g_so.map_size;
}

static void *app_ptr(void) { return app_get ? app_get() : 0; }

static void *game_lua(void) {
    uint8_t *a = (uint8_t *)app_ptr();
    void *lua;
    if (!a) return 0;
    memcpy(&lua, a + AB_LUA_OFF, 4);
    return lua;
}

void ab_set_so_path(const char *p) { if (p) so_path = p; }
const char *ab_so_path(void) { return so_path; }

static void *must_sym(const char *n) {
    void *p = so_sym(&g_so, n);
    if (!p) {
        char b[160];
        snprintf(b, sizeof b, "missing symbol %s", n);
        plat_fatal(b);
    }
    return p;
}

static void patch_arm_jump(void *fn, void *hook);
static void hook_set_scale(void *lua, float s);
static void hook_set_topleft(void *lua, float left, float top);
static void mk_string(void *slot, const char *s);

static void reset_view(void) {
    int i;
    fe_scale = 0.75f; fe_scale_y = 0.9f; fe_off_x = 0.f; fe_off_y = 0.f;
    pinch_on = 0;
    cam_hold = 0;
    hold_scale = hold_left = hold_top = 0.f;
    for (i = 0; i < AB_FINGERS; i++) memset(&fed[i], 0, sizeof fed[i]);
}

int ab_start(void) {
    JNIEnv *env;
    jstring files;
    reset_view();
    fngl_reset();
    if (so_load(so_path, &g_so)) {
        plat_log_line("so_load failed");
        return 1;
    }
    so_run_init(&g_so);
    {
        char b[80];
        snprintf(b, sizeof b, "so base %p size %u init %u x %u",
                 (void *)g_so.base, (unsigned)g_so.map_size, g_so.init_off, g_so.init_count);
        plat_log_line(b);
    }
    j_init = (jni_init_fn)must_sym("Java_com_rovio_ka3d_MyRenderer_nativeInit");
    j_update = (jni_void_fn)must_sym("Java_com_rovio_ka3d_MyRenderer_nativeUpdate");
    j_resize = (jni_resize_fn)must_sym("Java_com_rovio_ka3d_MyRenderer_nativeResize");
    j_input = (jni_input_fn)must_sym("Java_com_rovio_ka3d_MyRenderer_nativeInput");
    j_deinit = (jni_void_fn)so_sym(&g_so, "Java_com_rovio_ka3d_MyRenderer_nativeDeinit");
    str_ctor = (str_ctor_fn)so_sym(&g_so, "_ZN4lang6StringC1EPKc");
    lua_call = (lua_call_fn)so_sym(&g_so, "_ZN3lua9LuaObject4callEPKc");
    load_level = (load_fn)so_sym(&g_so, "_ZN7GameLua9loadLevelEN4lang6StringE");
    set_phys = (set_bool_fn)so_sym(&g_so, "_ZN7GameLua17setPhysicsEnabledEb");
    set_scale = (set_f_fn)so_sym(&g_so, "_ZN7GameLua13setWorldScaleEf");
    set_topleft = (set_ff_fn)so_sym(&g_so, "_ZN7GameLua10setTopLeftEff");
    set_vel = (set_vel_fn)so_sym(&g_so, "_ZN7GameLua11setVelocityEN4lang6StringEff");
    lua_set_num = (set_num_fn)so_sym(&g_so, "_ZN3lua8LuaTable9setNumberEPKcf");
    lua_set_str = (set_str_fn)so_sym(&g_so, "_ZN3lua8LuaTable9setStringEPKcS2_");
    lua_set_bool = (set_bname_fn)so_sym(&g_so, "_ZN3lua8LuaTable10setBooleanEPKcb");
    get_body = (get_body_fn)so_sym(&g_so, "_ZN7GameLua7getBodyERKN4lang6StringE");
    app_get = (app_get_fn)so_sym(&g_so, "_ZN9framework3App3getEv");
    if (set_scale) {
        uint32_t *p = (uint32_t *)set_scale;
        scale_saved[0] = p[0]; scale_saved[1] = p[1];
        patch_arm_jump((void *)set_scale, (void *)hook_set_scale);
    }
    if (set_topleft) {
        uint32_t *p = (uint32_t *)set_topleft;
        topleft_saved[0] = p[0]; topleft_saved[1] = p[1];
        patch_arm_jump((void *)set_topleft, (void *)hook_set_topleft);
    }
    cam_hooks_on = 1;
    env = ab_jni_env();
    files = ab_jni_new_utf(plat_files_dir());
    plat_log_line("ab_start: nativeInit 480x320");
    j_init(env, ab_jni_renderer(), ab_jni_renderer(), 480, 320, files);
    if (j_resize) j_resize(env, ab_jni_renderer(), 480, 320);
    started = 1;
    plat_log_line("ab_start: ok");
    plat_log_flush();
    return 0;
}

static void panel_to_game(float px, float py, float *gx, float *gy) {
    float sx = fe_scale, sy = fe_scale_y;
    if (sx < 1e-6f) sx = 1e-6f;
    if (sy < 1e-6f) sy = 1e-6f;
    *gx = (py - fe_off_y) / sy;
    *gy = fe_fb_h - (px - fe_off_x) / sx;
}

static void game_to_panel(float gx, float gy, float *px, float *py) {
    float sx = fe_scale, sy = fe_scale_y;
    if (sx < 1e-6f) sx = 1e-6f;
    if (sy < 1e-6f) sy = 1e-6f;
    *py = fe_off_y + gx * sy;
    *px = fe_off_x + (fe_fb_h - gy) * sx;
}

int ab_world_to_panel(float bx, float by, float *px, float *py) {
    uint8_t *lua = (uint8_t *)game_lua();
    float scale, left, top, gx, gy;
    if (!lua || !px || !py) return 0;
    scale = rf32(lua + AB_CAM_SCALE);
    left = rf32(lua + AB_CAM_LEFT);
    top = rf32(lua + AB_CAM_TOP);
    if (!(scale > 1e-4f)) return 0;
    gx = (bx * AB_PHYS_TO_WORLD - left) * scale;
    gy = (by * AB_PHYS_TO_WORLD - top) * scale;
    game_to_panel(gx, gy, px, py);
    return 1;
}

static void *body_named(const char *name) {
    void *lua = game_lua();
    char slot[16];
    if (!lua || !get_body || !name || !str_ctor) return 0;
    mk_string(slot, name);
    return get_body(lua, slot);
}

int ab_pouch_world(float *bx, float *by) {
    uint8_t *lua, *world, *b, *plat;
    int nwalk, nmax, found = 0;
    float minx = 1e9f, x0 = 0.f, y0 = 0.f, bestx = -1e9f;
    lua = (uint8_t *)game_lua();
    if (!lua || !bx || !by) return 0;
    memcpy(&world, lua + AB_WORLD_OFF, 4);
    if (!world) return 0;
    nmax = (int)ru32(world + B2_BODY_COUNT);
    plat = (uint8_t *)body_named("ShootingPlatform01");
    memcpy(&b, world + B2_BODY_LIST, 4);
    for (nwalk = 0; b && nwalk < 256 && nwalk < (nmax > 0 ? nmax + 2 : 256); nwalk++) {
        int type = (int)ru32(b);
        float x = rf32(b + 12);
        if (type == 2 && x < minx) minx = x;
        memcpy(&b, b + 104, 4);
    }
    if (!(minx < 1e8f)) return 0;
    /* Waiting birds sit left of the slingshot. The loaded bird is the
     * rightmost of that left cluster. If the shooting platform exists,
     * prefer the bird nearest a point just above/right of it (the pouch). */
    memcpy(&b, world + B2_BODY_LIST, 4);
    for (nwalk = 0; b && nwalk < 256 && nwalk < (nmax > 0 ? nmax + 2 : 256); nwalk++) {
        int type = (int)ru32(b);
        float x = rf32(b + 12), y = rf32(b + 16);
        if (type == 2 && x <= minx + 6.5f && x >= bestx) {
            bestx = x; x0 = x; y0 = y; found = 1;
        }
        memcpy(&b, b + 104, 4);
    }
    if (plat) {
        float px = rf32(plat + 12) + 1.2f, py = rf32(plat + 16) - 1.6f, bestd = 1e9f;
        memcpy(&b, world + B2_BODY_LIST, 4);
        for (nwalk = 0; b && nwalk < 256 && nwalk < (nmax > 0 ? nmax + 2 : 256); nwalk++) {
            int type = (int)ru32(b);
            float x = rf32(b + 12), y = rf32(b + 16), dx, dy, d2;
            if (type == 2 && x <= minx + 6.5f) {
                dx = x - px; dy = y - py; d2 = dx * dx + dy * dy;
                if (d2 < bestd) { bestd = d2; x0 = x; y0 = y; found = 1; }
            }
            memcpy(&b, b + 104, 4);
        }
    }
    if (!found) {
        static const char *birds[] = {
            "RedBird", "YellowBird", "BlueBird", "BlackBird", "WhiteBird", 0
        };
        int i;
        for (i = 0; birds[i]; i++) {
            uint8_t *body = (uint8_t *)body_named(birds[i]);
            if (body && (int)ru32(body) == 2) {
                x0 = rf32(body + 12); y0 = rf32(body + 16); found = 1; break;
            }
        }
    }
    if (!found) return 0;
    *bx = x0; *by = y0;
    return 1;
}

static int finger_down_count(void) {
    int n = 0, i;
    for (i = 0; i < AB_FINGERS; i++) if (fed[i].down) n++;
    return n;
}

void ab_touch(int id, int down, float px, float py) {
    float gx, gy;
    int began, moved, ended, type;
    if (id < 0 || id >= AB_FINGERS) id = 0;
    panel_to_game(px, py, &gx, &gy);
    began = down && !fed[id].down;
    moved = down && fed[id].down;
    ended = !down && fed[id].down;
    if (!began && !moved && !ended) return;
    fed[id].id = id; fed[id].x = px; fed[id].y = py; fed[id].gx = gx; fed[id].gy = gy;
    fed[id].down = down != 0;
    type = began ? 0 : ended ? 2 : 1;
    if (j_input) j_input(ab_jni_env(), ab_jni_renderer(), type, gx, gy, id);
}

static void ab_lua_set_number(const char *name, float v) {
    void *lua = game_lua();
    if (lua && lua_set_num) lua_set_num(lua, name, v);
}
static void ab_lua_set_string(const char *name, const char *val) {
    void *lua = game_lua();
    if (lua && lua_set_str) lua_set_str(lua, name, val);
}
static void ab_lua_set_bool(const char *name, int on) {
    void *lua = game_lua();
    if (lua && lua_set_bool) lua_set_bool(lua, name, on);
}

static void write_cam_fields(void *lua, float scale, float left, float top) {
    uint8_t *p = (uint8_t *)lua, *inner = 0;
    if (!p) return;
    wf32(p + AB_CAM_SCALE, scale);
    wf32(p + AB_CAM_LEFT, left);
    wf32(p + AB_CAM_TOP, top);
    memcpy(&inner, p + 32, 4);
    if (inner) {
        wf32(inner + 644u, scale);
        wf32(inner + 640u, scale);
    }
}

static void orig_set_scale(void *lua, float s);
static void orig_set_topleft(void *lua, float left, float top);

static void apply_cam(float scale, float left, float top) {
    void *lua = game_lua();
    if (!lua || !(scale > 1e-4f)) return;
    write_cam_fields(lua, scale, left, top);
    orig_set_scale(lua, scale);
    orig_set_topleft(lua, left, top);
    ab_lua_set_number("worldScale", scale);
    ab_lua_set_number("currentZoomedScale", scale);
    ab_lua_set_number("zoomLevel", scale);
    ab_lua_set_number("oldZoomLevel", scale);
}

/* defaultCamera writes these every tick. While cam_hold is set, keep the
 * world-fit camera by forwarding the hold values into the original C++
 * setters (the previous no-op left the renderer on the bird camera). */
static void hook_set_scale(void *lua, float s) {
    if (!lua) return;
    if (cam_hold) s = hold_scale;
    orig_set_scale(lua, s);
}
static void hook_set_topleft(void *lua, float left, float top) {
    if (!lua) return;
    if (cam_hold) { left = hold_left; top = hold_top; }
    orig_set_topleft(lua, left, top);
}

static void patch_arm_jump(void *fn, void *hook) {
    uint32_t *p = (uint32_t *)fn;
    if (!p || !hook) return;
    p[0] = 0xe51ff004u; /* ldr pc, [pc, #-4] */
    p[1] = (uint32_t)(uintptr_t)hook;
    __builtin___clear_cache((char *)p, (char *)(p + 2));
}

static void cam_unpatch(void) {
    uint32_t *p;
    if (!cam_hooks_on) return;
    if (set_scale) {
        p = (uint32_t *)set_scale;
        p[0] = scale_saved[0]; p[1] = scale_saved[1];
        __builtin___clear_cache((char *)p, (char *)(p + 2));
    }
    if (set_topleft) {
        p = (uint32_t *)set_topleft;
        p[0] = topleft_saved[0]; p[1] = topleft_saved[1];
        __builtin___clear_cache((char *)p, (char *)(p + 2));
    }
}

static void cam_repatch(void) {
    if (!cam_hooks_on) return;
    if (set_scale) patch_arm_jump((void *)set_scale, (void *)hook_set_scale);
    if (set_topleft) patch_arm_jump((void *)set_topleft, (void *)hook_set_topleft);
}

static void orig_set_scale(void *lua, float s) {
    if (!set_scale) return;
    cam_unpatch();
    set_scale(lua, s);
    cam_repatch();
}

static void orig_set_topleft(void *lua, float left, float top) {
    if (!set_topleft) return;
    cam_unpatch();
    set_topleft(lua, left, top);
    cam_repatch();
}

static void ab_apply_pinch(void) {
    void *lua;
    float d, scale, left, top, ns, minz, maxz;
    if (finger_down_count() != 2 || !fed[0].down || !fed[1].down) { pinch_on = 0; return; }
    {
        float dx = fed[0].gx - fed[1].gx, dy = fed[0].gy - fed[1].gy, s = dx * dx + dy * dy;
        union { float f; uint32_t u; } v;
        v.f = s;
        v.u = (v.u >> 1) + 0x1fc00000u;
        v.f = 0.5f * (v.f + s / v.f);
        d = 0.5f * (v.f + s / v.f);
    }
    if (d < 4.f) return;
    lua = game_lua();
    if (!lua) return;
    scale = rf32((uint8_t *)lua + AB_CAM_SCALE);
    if (!(scale > 1e-4f)) return;
    left = rf32((uint8_t *)lua + AB_CAM_LEFT);
    top = rf32((uint8_t *)lua + AB_CAM_TOP);
    if (!pinch_on) {
        pinch_on = 1; pinch_d0 = d; pinch_s0 = scale;
        pinch_cx = left + 0.5f * AB_VIEW_W / scale;
        pinch_cy = top + 0.5f * AB_VIEW_H / scale;
        return;
    }
    ns = pinch_s0 * (pinch_d0 / d);
    minz = 0.22f; maxz = pinch_s0 * 1.6f;
    if (maxz < pinch_s0) maxz = pinch_s0;
    if (maxz > 1.05f) maxz = 1.05f;
    if (ns < minz) ns = minz;
    if (ns > maxz) ns = maxz;
    left = pinch_cx - 0.5f * AB_VIEW_W / ns;
    top = pinch_cy - 0.5f * AB_VIEW_H / ns;
    hold_scale = ns; hold_left = left; hold_top = top;
    cam_hold = 1;
    apply_cam(ns, left, top);
}

/* Fit every Box2D body into the 480x320 view so pigs and structures stay on
 * screen. Lua's defaultCamera slides back to the slingshot after a few seconds. */
void ab_cam_fit_world(void) {
    uint8_t *lua, *world, *b, *head;
    int nwalk, nmax, n = 0;
    float dminx = 1e9f, dmaxx = -1e9f, dminy = 1e9f, dmaxy = -1e9f;
    float minx, maxx, miny, maxy, w, h, sx, sy, ns, cx, cy, left, top;
    lua = (uint8_t *)game_lua();
    if (!lua) return;
    memcpy(&world, lua + AB_WORLD_OFF, 4);
    if (!world) return;
    nmax = (int)ru32(world + B2_BODY_COUNT);
    memcpy(&head, world + B2_BODY_LIST, 4);
    /* Dynamic bodies first: the ground box is huge and would zoom us into dirt. */
    for (b = head, nwalk = 0; b && nwalk < 256 && nwalk < (nmax > 0 ? nmax + 2 : 256); nwalk++) {
        int type = (int)ru32(b);
        float x = rf32(b + 12), y = rf32(b + 16);
        float wx = x * AB_PHYS_TO_WORLD, wy = y * AB_PHYS_TO_WORLD;
        if (type == 2) {
            if (wx < dminx) dminx = wx;
            if (wx > dmaxx) dmaxx = wx;
            if (wy < dminy) dminy = wy;
            if (wy > dmaxy) dmaxy = wy;
            n++;
        }
        memcpy(&b, b + 104, 4);
    }
    if (n < 1) return;
    minx = dminx; maxx = dmaxx; miny = dminy; maxy = dmaxy;
    for (b = head, nwalk = 0; b && nwalk < 256 && nwalk < (nmax > 0 ? nmax + 2 : 256); nwalk++) {
        int type = (int)ru32(b);
        float x = rf32(b + 12), y = rf32(b + 16);
        float wx = x * AB_PHYS_TO_WORLD, wy = y * AB_PHYS_TO_WORLD;
        if (type == 0 && wy >= dminy - 40.f && wy <= dmaxy + 40.f &&
            wx >= dminx - 80.f && wx <= dmaxx + 80.f) {
            if (wx < minx) minx = wx;
            if (wx > maxx) maxx = wx;
            if (wy < miny) miny = wy;
            if (wy > maxy) maxy = wy;
        }
        memcpy(&b, b + 104, 4);
    }
    /* Keep the slingshot inset so a real pouch drag has room to pull back. */
    minx -= 180.f; maxx += 60.f; miny -= 40.f; maxy += 40.f;
    w = maxx - minx; h = maxy - miny;
    if (w < 160.f) w = 160.f;
    if (h < 100.f) h = 100.f;
    sx = AB_VIEW_W / w; sy = AB_VIEW_H / h;
    ns = sx < sy ? sx : sy;
    if (ns < 0.28f) ns = 0.28f;
    if (ns > 0.85f) ns = 0.85f;
    cx = 0.5f * (minx + maxx);
    cy = 0.5f * (miny + maxy);
    left = cx - 0.5f * AB_VIEW_W / ns;
    top = cy - 0.5f * AB_VIEW_H / ns;
    hold_scale = ns; hold_left = left; hold_top = top;
    cam_hold = 1;
    apply_cam(ns, left, top);
}

void ab_cam_fit_play(void) {
    void *lua = game_lua();
    float scale, left, top, cx, cy, ns, factor;
    if (!lua) return;
    ab_lua_set_number("screenWidth", AB_VIEW_W);
    ab_lua_set_number("screenHeight", AB_VIEW_H);
    ab_lua_set_bool("showBG", 1);
    scale = rf32((uint8_t *)lua + AB_CAM_SCALE);
    if (!(scale > 1.05f)) return;
    left = rf32((uint8_t *)lua + AB_CAM_LEFT);
    top = rf32((uint8_t *)lua + AB_CAM_TOP);
    factor = AB_VIEW_H / 1024.f;
    ns = scale * factor;
    if (ns < 0.35f) ns = 0.35f;
    if (ns > 0.90f) ns = 0.90f;
    cx = left + 0.5f * AB_VIEW_W / scale;
    cy = top + 0.5f * AB_VIEW_H / scale;
    left = cx - 0.5f * AB_VIEW_W / ns;
    top = cy - 0.5f * AB_VIEW_H / ns;
    if (set_scale) set_scale(lua, ns);
    if (set_topleft) set_topleft(lua, left, top);
    ab_lua_set_number("worldScale", ns);
}

void ab_frame_q(float dt, const ab_tsample *q, int n) {
    int i;
    ab_phys_info info;
    (void)dt;
    if (!started) return;
    ab_phys_snapshot(&info);
    if (info.dynamic_n > 0 && !info.phys_on) ab_phys_enable(1);
    for (i = 0; i < n; i++) {
        int id = q[i].id;
        if (id < 0 || id >= AB_FINGERS) id = 0;
        ab_touch(id, q[i].down, q[i].x, q[i].y);
    }
    if (cam_hold && hold_scale > 1e-4f) apply_cam(hold_scale, hold_left, hold_top);
    else ab_cam_fit_play();
    fe_reset_matrices();
    fe_stat_tris = 0; fe_stat_draws = 0;
    be_frame_begin();
    if (j_update) j_update(ab_jni_env(), ab_jni_renderer());
    if (cam_hold && hold_scale > 1e-4f) apply_cam(hold_scale, hold_left, hold_top);
    else ab_cam_fit_play();
    be_frame_end();
    ab_apply_pinch();
}

void ab_frame(float dt) { ab_frame_q(dt, 0, 0); }

static void mk_string(void *slot, const char *s) {
    memset(slot, 0, 16);
    if (str_ctor) str_ctor(slot, s);
    else memcpy(slot, &s, 4);
}

void ab_load_level(const char *name) {
    void *lua = game_lua();
    char slot[16];
    if (!lua || !name || !load_level) { plat_log_line("ab_load_level: no GameLua"); return; }
    mk_string(slot, name);
    load_level(lua, slot);
    ab_phys_enable(1);
    ab_phys_log(name);
}

void ab_call_lua(const char *name) {
    void *lua = game_lua();
    if (!lua || !name || !lua_call) { plat_log_line("ab_call_lua: no GameLua"); return; }
    lua_call(lua, name);
}

void ab_load_lua_level(const char *name) {
    if (!name) return;
    ab_lua_set_number("screenWidth", AB_VIEW_W);
    ab_lua_set_number("screenHeight", AB_VIEW_H);
    ab_lua_set_string("levelName", name);
    ab_lua_set_bool("loadingPageDrawn", 1);
    ab_lua_set_bool("showBG", 1);
    ab_lua_set_bool("showTutorials", 0);
    ab_lua_set_bool("birdTutorialPopups", 0);
    plat_log_line(name);
    fe_reset_matrices();
    ab_call_lua("updateLoading");
    /* Hold the castle / whole-level camera so structures and pigs stay in
     * frame. defaultCamera alone slides back to the slingshot after a few
     * seconds (castleCameraTimer). */
    ab_lua_set_number("castleCameraTimer", 60.f);
    ab_lua_set_number("cameraResetTimer", 60.f);
    ab_lua_set_number("cameraAnimationSlider", 1.f);
    ab_lua_set_number("cameraAnimationSliderTarget", 1.f);
    ab_lua_set_bool("allowResetToBirdCamera", 0);
    ab_call_lua("defaultCamera");
    ab_phys_enable(1);
    ab_cam_fit_world();
    ab_phys_log(name);
}

void ab_cam_fit_birds(void) {
    uint8_t *lua, *world, *b;
    int nwalk, nmax, n = 0, i;
    float xs[64], ys[64], minx = 1e9f, maxx, miny, maxy, w, h, ns, cx, cy, left, top, cut;
    lua = (uint8_t *)game_lua();
    if (!lua) return;
    memcpy(&world, lua + AB_WORLD_OFF, 4);
    if (!world) return;
    nmax = (int)ru32(world + B2_BODY_COUNT);
    memcpy(&b, world + B2_BODY_LIST, 4);
    for (nwalk = 0; b && nwalk < 256 && nwalk < (nmax > 0 ? nmax + 2 : 256); nwalk++) {
        int type = (int)ru32(b);
        float x = rf32(b + 12), y = rf32(b + 16);
        if (type == 2 && n < 64) { xs[n] = x; ys[n] = y; n++; }
        memcpy(&b, b + 104, 4);
    }
    if (n < 1) { cam_hold = 0; return; }
    for (i = 0; i < n; i++) if (xs[i] < minx) minx = xs[i];
    cut = minx + 8.f;
    minx = 1e9f; maxx = -1e9f; miny = 1e9f; maxy = -1e9f;
    for (i = 0; i < n; i++) {
        if (xs[i] > cut) continue;
        if (xs[i] < minx) minx = xs[i];
        if (xs[i] > maxx) maxx = xs[i];
        if (ys[i] < miny) miny = ys[i];
        if (ys[i] > maxy) maxy = ys[i];
    }
    minx = minx * AB_PHYS_TO_WORLD - 40.f;
    maxx = maxx * AB_PHYS_TO_WORLD + 80.f;
    miny = miny * AB_PHYS_TO_WORLD - 25.f;
    maxy = maxy * AB_PHYS_TO_WORLD + 40.f;
    w = maxx - minx; h = maxy - miny;
    if (w < 140.f) w = 140.f;
    if (h < 90.f) h = 90.f;
    ns = AB_VIEW_W / w;
    if (AB_VIEW_H / h < ns) ns = AB_VIEW_H / h;
    if (ns < 0.40f) ns = 0.40f;
    if (ns > 0.80f) ns = 0.80f;
    cx = 0.5f * (minx + maxx);
    cy = 0.5f * (miny + maxy);
    left = cx - 0.5f * AB_VIEW_W / ns;
    top = cy - 0.5f * AB_VIEW_H / ns;
    hold_scale = ns; hold_left = left; hold_top = top;
    cam_hold = 1;
    apply_cam(ns, left, top);
}

void ab_set_body_velocity(const char *name, float vx, float vy) {
    void *lua = game_lua();
    char slot[16];
    if (!lua || !name || !set_vel) return;
    mk_string(slot, name);
    set_vel(lua, slot, vx, vy);
}

void ab_ready_slingshot(void) {
    /* birdTutorialPopups is a table; #birdTutorialPopups throws if we
     * overwrite it with a boolean, which blanked every later frame. */
    ab_lua_set_bool("showTutorials", 0);
    ab_lua_set_bool("birdReady", 1);
    ab_lua_set_bool("fillInNextBird", 1);
    ab_lua_set_number("levelStartTimer", 10.f);
    ab_lua_set_number("birdToSlingshotAnimationTimer", 10.f);
    /* Keep the whole-level camera so the slingshot stays on screen. */
}

void ab_phys_enable(int on) {
    void *lua = game_lua();
    if (lua && set_phys) set_phys(lua, on ? 1 : 0);
}

void ab_phys_snapshot(ab_phys_info *o) {
    uint8_t *lua, *world, *b;
    int nwalk, nmax;
    if (!o) return;
    memset(o, 0, sizeof *o);
    o->dyn_ymin = 1e9f; o->dyn_ymax = -1e9f;
    o->stat_ymin = 1e9f; o->stat_ymax = -1e9f;
    lua = (uint8_t *)game_lua();
    o->lua = (uint32_t)(uintptr_t)lua;
    if (!lua) return;
    o->phys_on = lua[AB_PHYS_ON_OFF];
    o->acc = rf32(lua + AB_ACC_OFF);
    o->cam_left = rf32(lua + AB_CAM_LEFT);
    o->cam_top = rf32(lua + AB_CAM_TOP);
    o->world_scale = rf32(lua + AB_CAM_SCALE);
    memcpy(&world, lua + AB_WORLD_OFF, 4);
    o->world = (uint32_t)(uintptr_t)world;
    if (!world) return;
    nmax = (int)ru32(world + B2_BODY_COUNT);
    o->contacts_hint = (int)ru32(world + B2_JOINT_COUNT);
    o->grav_x = rf32(world + B2_GRAV_X);
    o->grav_y = rf32(world + B2_GRAV_Y);
    memcpy(&b, world + B2_BODY_LIST, 4);
    for (nwalk = 0; b && nwalk < 256 && nwalk < (nmax > 0 ? nmax + 2 : 256); nwalk++) {
        int type = (int)ru32(b);
        float x = rf32(b + 12), y = rf32(b + 16);
        int nfix = (int)ru32(b + 112);
        float wx, wy, sx, sy;
        o->bodies++;
        o->fixtures += nfix > 0 ? nfix : 0;
        wx = x * AB_PHYS_TO_WORLD; wy = y * AB_PHYS_TO_WORLD;
        if (o->world_scale > 1e-4f) {
            sx = (wx - o->cam_left) * o->world_scale;
            sy = (wy - o->cam_top) * o->world_scale;
            if (sx >= -40.f && sx <= AB_VIEW_W + 40.f && sy >= -40.f && sy <= AB_VIEW_H + 40.f) {
                o->vis_all++;
                if (type == 2) o->vis_dyn++;
                if (o->vis_n < AB_VIS_MAX) {
                    int k = o->vis_n++;
                    float px, py;
                    o->vis_type[k] = type;
                    o->vis_x[k] = x; o->vis_y[k] = y;
                    game_to_panel(sx, sy, &px, &py);
                    o->vis_px[k] = px; o->vis_py[k] = py;
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
            o->sample_type[k] = type; o->sample_x[k] = x; o->sample_y[k] = y;
        }
        memcpy(&b, b + 104, 4);
    }
    if (o->static_n == 0) { o->stat_ymin = 0.f; o->stat_ymax = 0.f; }
    if (o->dynamic_n == 0) { o->dyn_ymin = 0.f; o->dyn_ymax = 0.f; }
    (void)guest_ok; (void)wu32; (void)wf32;
}

int ab_phys_resting(const ab_phys_info *o) {
    if (!o || !o->world || !o->phys_on) return 0;
    if (o->static_n < 1 || o->dynamic_n < 1 || o->fixtures < o->bodies) return 0;
    if (o->dyn_ymin < -40.f || o->dyn_ymax > 40.f) return 0;
    return 1;
}

void ab_phys_log(const char *tag) {
    ab_phys_info i;
    char buf[256];
    extern int be_stat_tex_fail;
    ab_phys_snapshot(&i);
    snprintf(buf, sizeof buf,
             "phys %s lua=%08x world=%08x on=%d n=%d st=%d dyn=%d fix=%d g=%.2f,%.2f "
             "dynY=%.2f..%.2f scale=%.3f vis=%d/%d heap=%u peak=%u texfail=%d",
             tag ? tag : "-", i.lua, i.world, i.phys_on, i.bodies, i.static_n, i.dynamic_n,
             i.fixtures, (double)i.grav_x, (double)i.grav_y, (double)i.dyn_ymin, (double)i.dyn_ymax,
             (double)i.world_scale, i.vis_all, i.bodies, ab_heap_used(), ab_heap_peak(), be_stat_tex_fail);
    plat_log_line(buf);
    plat_log_flush();
}
