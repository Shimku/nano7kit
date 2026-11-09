/* ab_entry.c: drive Ka3D through JNI natives in libangrybirds.so (nano7kit, MIT). */
#include <math.h>
#include <stdint.h>
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
typedef float (*get_num_fn)(void *, const char *);
typedef int (*get_bool_fn)(void *, const char *);
typedef int (*is_name_fn)(void *, const char *);
typedef void (*set_str_fn)(void *, const char *, const char *);
typedef void (*set_bname_fn)(void *, const char *, int);
typedef void *(*get_body_fn)(void *, const void *);
typedef void *(*app_get_fn)(void);
typedef void (*sheet_ctor_fn)(void *, void *, const void *);
typedef void (*spr_ctor_fn)(void *, void *, int, int, int, int, int, int);
typedef const char *(*cstr_fn)(const void *);
typedef void (*tab_dtor_fn)(void *);
typedef void (*str_dtor_fn)(void *);
typedef void (*get_str_fn)(void *, void *, const char *);

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
static get_num_fn lua_get_num;
static get_bool_fn lua_get_bool;
static is_name_fn lua_is_num, lua_is_bool, lua_is_nil, lua_is_str;
typedef void (*get_tab_fn)(void *, void *, const char *);
static get_tab_fn lua_get_tab;
static get_str_fn lua_get_str;
static tab_dtor_fn lua_tab_dtor;
static str_dtor_fn str_dtor;
static set_str_fn lua_set_str;
static set_bname_fn lua_set_bool;
static get_body_fn get_body;
static app_get_fn app_get;
static sheet_ctor_fn sheet_ctor;
static spr_ctor_fn spr_ctor;
static cstr_fn str_cstr;
static int started;
static int cam_hold;
static float hold_scale, hold_left, hold_top;
static float world_minx, world_maxx, world_miny, world_maxy;
static int world_bounds_ok;
static uint32_t scale_saved[2], topleft_saved[2];
static uint32_t sheet_ctor_saved[2], spr_ctor_saved[2];
static int cam_hooks_on;
static int block_sheet_loading;
static int spr_log_n;
static int score_readable;
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
#ifdef FN_NANO
static int ptr_in_ram(void *p) { return (uint32_t)(uintptr_t)p >= 0x08000000u; }
#else
static int ptr_in_ram(void *p) { return p != 0; }
#endif

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
static void hook_sheet_ctor(void *self, void *ctx, const void *name);
static void hook_spr_ctor(void *self, void *sheet, int x, int y, int w, int h, int ox, int oy);
static void mk_string(void *slot, const char *s);
static void install_jump(void *fn, void *hook, uint32_t saved[2]);
static void restore_jump(void *fn, const uint32_t saved[2]);

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
        char b[96];
        snprintf(b, sizeof b, "so base %p size %u init %u x %u",
                 (void *)g_so.base, (unsigned)g_so.map_size, g_so.init_off, g_so.init_count);
        plat_log_line(b);
        plat_log_flush();
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
    lua_get_num = (get_num_fn)so_sym(&g_so, "_ZNK3lua8LuaTable9getNumberEPKc");
    lua_get_bool = (get_bool_fn)so_sym(&g_so, "_ZNK3lua8LuaTable10getBooleanEPKc");
    lua_is_num = (is_name_fn)so_sym(&g_so, "_ZNK3lua8LuaTable8isNumberEPKc");
    lua_is_bool = (is_name_fn)so_sym(&g_so, "_ZNK3lua8LuaTable9isBooleanEPKc");
    lua_is_nil = (is_name_fn)so_sym(&g_so, "_ZNK3lua8LuaTable5isNilEPKc");
    lua_is_str = (is_name_fn)so_sym(&g_so, "_ZNK3lua8LuaTable8isStringEPKc");
    lua_get_tab = (get_tab_fn)so_sym(&g_so, "_ZNK3lua8LuaTable8getTableEPKc");
    lua_get_str = (get_str_fn)so_sym(&g_so, "_ZNK3lua8LuaTable9getStringEPKc");
    lua_tab_dtor = (tab_dtor_fn)so_sym(&g_so, "_ZN3lua8LuaTableD1Ev");
    str_dtor = (str_dtor_fn)so_sym(&g_so, "_ZN4lang6StringD1Ev");
    lua_set_str = (set_str_fn)so_sym(&g_so, "_ZN3lua8LuaTable9setStringEPKcS2_");
    lua_set_bool = (set_bname_fn)so_sym(&g_so, "_ZN3lua8LuaTable10setBooleanEPKcb");
    get_body = (get_body_fn)so_sym(&g_so, "_ZN7GameLua7getBodyERKN4lang6StringE");
    app_get = (app_get_fn)so_sym(&g_so, "_ZN9framework3App3getEv");
    str_cstr = (cstr_fn)so_sym(&g_so, "_ZNK4lang6String5c_strEv");
    sheet_ctor = (sheet_ctor_fn)so_sym(&g_so,
        "_ZN4game11SpriteSheetC1EPN2gr7ContextERKN4lang6StringE");
    spr_ctor = (spr_ctor_fn)so_sym(&g_so,
        "_ZN4game6SpriteC1EPNS_11SpriteSheetEiiiiii");
    if (set_scale) install_jump((void *)set_scale, (void *)hook_set_scale, scale_saved);
    if (set_topleft) install_jump((void *)set_topleft, (void *)hook_set_topleft, topleft_saved);
    cam_hooks_on = 1;
    /* convert_assets halves INGAME/INGAME_1 .dat so UVs fit the 512 texture.
     * createObject calls getSpriteBounds("", sprite) (empty sheet, registry
     * lookup), so a getSpriteWidth hook keyed on "INGAME" never runs. The
     * sheet parser constructs Sprite objects directly. Double width/height/
     * pivot shorts after that ctor; UVs are already baked from the halved
     * rect and stay put. Draw stretches onto the restored body. */
    if (spr_ctor) install_jump((void *)spr_ctor, (void *)hook_spr_ctor, spr_ctor_saved);
    if (sheet_ctor) install_jump((void *)sheet_ctor, (void *)hook_sheet_ctor, sheet_ctor_saved);
    env = ab_jni_env();
    files = ab_jni_new_utf(plat_files_dir());
    plat_log_line("ab_start: nativeInit 480x320");
    plat_log_flush();
    j_init(env, ab_jni_renderer(), ab_jni_renderer(), 480, 320, files);
    plat_log_line("ab_start: nativeInit returned");
    plat_log_flush();
    if (j_resize) {
        plat_log_line("ab_start: nativeResize 480x320");
        plat_log_flush();
        j_resize(env, ab_jni_renderer(), 480, 320);
        plat_log_line("ab_start: nativeResize returned");
        plat_log_flush();
    }
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

int ab_sling_world(float *bx, float *by) {
    uint8_t *plat;
    if (!bx || !by) return 0;
    plat = (uint8_t *)body_named("ShootingPlatform01");
    if (plat) {
        /* Pouch sits a little above/right of the platform body. */
        *bx = rf32(plat + 12) + 1.2f;
        *by = rf32(plat + 16) - 1.6f;
        return 1;
    }
    return ab_pouch_world(bx, by);
}

static int finger_down_count(void) {
    int n = 0, i;
    for (i = 0; i < AB_FINGERS; i++) if (fed[i].down) n++;
    return n;
}

void ab_touch(int id, int down, float px, float py) {
    float gx, gy;
    int began, moved, ended, type;
    static int in_log;
    if (id < 0 || id >= AB_FINGERS) id = 0;
    panel_to_game(px, py, &gx, &gy);
    began = down && !fed[id].down;
    moved = down && fed[id].down;
    ended = !down && fed[id].down;
    if (!began && !moved && !ended) return;
    fed[id].id = id; fed[id].x = px; fed[id].y = py; fed[id].gx = gx; fed[id].gy = gy;
    fed[id].down = down != 0;
    /* Android MotionEvent: ACTION_DOWN=0 ACTION_UP=1 ACTION_MOVE=2.
     * GameApp vtable +36 began, +44 ended, +40 moved. MOVE (2) keeps
     * LBUTTON down and updates cursor; type 1 is the release. Sending
     * iOS 0/1/2 (began/moved/ended) made every drag tick ACTION_UP. */
    type = began ? 0 : ended ? 1 : 2;
    if (in_log < 40) {
        char b[160];
        snprintf(b, sizeof b, "AB_INPUT id=%d type=%d panel=%.1f,%.1f game=%.1f,%.1f",
                 id, type, (double)px, (double)py, (double)gx, (double)gy);
        plat_log_line(b);
        in_log++;
    }
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

static void tab_done(void *tab) {
    if (tab && lua_tab_dtor) lua_tab_dtor(tab);
}

static void sync_lua_screen(void *lua, float scale, float left, float top) {
    char tab[32];
    if (!lua || !lua_get_tab || !lua_set_num) return;
    memset(tab, 0, sizeof tab);
    lua_get_tab(tab, lua, "screen");
    lua_set_num(tab, "left", left);
    lua_set_num(tab, "top", top);
    tab_done(tab);
}

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
    sync_lua_screen(lua, scale, left, top);
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
static void install_jump(void *fn, void *hook, uint32_t saved[2]) {
    uint32_t *p = (uint32_t *)fn;
    if (!p || !hook || !saved) return;
    saved[0] = p[0]; saved[1] = p[1];
    patch_arm_jump(fn, hook);
}
static void restore_jump(void *fn, const uint32_t saved[2]) {
    uint32_t *p = (uint32_t *)fn;
    if (!p || !saved) return;
    p[0] = saved[0]; p[1] = saved[1];
    __builtin___clear_cache((char *)p, (char *)(p + 2));
}
static int is_block_sheet_name(const char *p) {
    const char *slash, *dot;
    char stem[32];
    size_t n;
    if (!p || !p[0]) return 0;
    slash = strrchr(p, '/');
    if (slash) p = slash + 1;
    dot = strrchr(p, '.');
    n = (size_t)((dot && dot > p) ? (size_t)(dot - p) : strlen(p));
    if (n == 0 || n >= sizeof stem) return 0;
    memcpy(stem, p, n);
    stem[n] = 0;
    return strcmp(stem, "INGAME") == 0 || strcmp(stem, "INGAME_1") == 0;
}
static void hook_sheet_ctor(void *self, void *ctx, const void *name) {
    const char *p = (name && str_cstr) ? str_cstr(name) : 0;
    int prev = block_sheet_loading;
    block_sheet_loading = is_block_sheet_name(p);
    {
        char b[192];
        snprintf(b, sizeof b, "AB_SPRITE sheet %s block=%d", p ? p : "?", block_sheet_loading);
        plat_log_line(b);
    }
    if (sheet_ctor) {
        restore_jump((void *)sheet_ctor, sheet_ctor_saved);
        sheet_ctor(self, ctx, name);
        patch_arm_jump((void *)sheet_ctor, (void *)hook_sheet_ctor);
    }
    block_sheet_loading = prev;
}
static void hook_spr_ctor(void *self, void *sheet, int x, int y, int w, int h, int ox, int oy) {
    if (!spr_ctor) return;
    restore_jump((void *)spr_ctor, spr_ctor_saved);
    spr_ctor(self, sheet, x, y, w, h, ox, oy);
    patch_arm_jump((void *)spr_ctor, (void *)hook_spr_ctor);
    if (block_sheet_loading && self) {
        int16_t *d = (int16_t *)((uint8_t *)self + 16);
        int rw = d[0], rh = d[1];
        if (d[0]) d[0] = (int16_t)(d[0] * 2);
        if (d[1]) d[1] = (int16_t)(d[1] * 2);
        if (d[2]) d[2] = (int16_t)(d[2] * 2);
        if (d[3]) d[3] = (int16_t)(d[3] * 2);
        if (spr_log_n < 16) {
            char b[160];
            snprintf(b, sizeof b, "AB_SPRITE raw=%d,%d scaled=%d,%d xy=%d,%d",
                     rw, rh, (int)d[0], (int)d[1], x, y);
            plat_log_line(b);
            spr_log_n++;
        }
    }
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
    /* Fixed min/max so in and out always move. Tying max to pinch_s0
     * * 1.6 left a zoomed-out view unable to pinch back in. */
    minz = 0.22f; maxz = 1.05f;
    if (ns < minz) ns = minz;
    if (ns > maxz) ns = maxz;
    left = pinch_cx - 0.5f * AB_VIEW_W / ns;
    top = pinch_cy - 0.5f * AB_VIEW_H / ns;
    if (world_bounds_ok) {
        float vw = AB_VIEW_W / ns, vh = AB_VIEW_H / ns;
        float bw = world_maxx - world_minx, bh = world_maxy - world_miny;
        if (vw < bw) {
            if (left < world_minx) left = world_minx;
            if (left + vw > world_maxx) left = world_maxx - vw;
        } else {
            left = 0.5f * (world_minx + world_maxx) - 0.5f * vw;
        }
        if (vh < bh) {
            if (top < world_miny) top = world_miny;
            if (top + vh > world_maxy) top = world_maxy - vh;
        } else {
            top = 0.5f * (world_miny + world_maxy) - 0.5f * vh;
        }
    }
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
    /* 480x320 world-fit: pad like the original opening view (birds and
     * castle both on screen). Do not add a huge left pad: that zoomed L2/L3
     * out to the 0.28 clamp and clipped the castle. */
    minx -= 40.f; maxx += 60.f; miny -= 20.f; maxy += 30.f;
    w = maxx - minx; h = maxy - miny;
    if (w < 160.f) w = 160.f;
    if (h < 100.f) h = 100.f;
    sx = AB_VIEW_W / w; sy = AB_VIEW_H / h;
    ns = sx < sy ? sx : sy;
    if (ns < 0.18f) ns = 0.18f;
    if (ns > 0.85f) ns = 0.85f;
    cx = 0.5f * (minx + maxx);
    cy = 0.5f * (miny + maxy);
    left = cx - 0.5f * AB_VIEW_W / ns;
    top = cy - 0.5f * AB_VIEW_H / ns;
    world_minx = minx; world_maxx = maxx; world_miny = miny; world_maxy = maxy;
    world_bounds_ok = 1;
    hold_scale = ns; hold_left = left; hold_top = top;
    cam_hold = 1;
    apply_cam(ns, left, top);
    {
        char b[160];
        snprintf(b, sizeof b, "AB_CAM fit n=%d bbox=%.0f,%.0f..%.0f,%.0f scale=%.3f left=%.0f top=%.0f",
                 n, (double)minx, (double)miny, (double)maxx, (double)maxy,
                 (double)ns, (double)left, (double)top);
        plat_log_line(b);
    }
}

void ab_cam_set(float scale, float left, float top) {
    if (!(scale > 1e-4f)) return;
    hold_scale = scale;
    hold_left = left;
    hold_top = top;
    cam_hold = 1;
    apply_cam(scale, left, top);
}

void ab_cam_fit_aim(void) {
    float ns, cx, cy, left, top;
    if (!(hold_scale > 1e-4f)) ab_cam_fit_world();
    if (!(hold_scale > 1e-4f)) return;
    /* Zoom out so the slingshot is not on the panel edge and a pull-back
     * (landscape left, portrait up) stays on screen. */
    ns = hold_scale * 0.78f;
    if (ns < 0.22f) ns = 0.22f;
    cx = hold_left + 0.5f * AB_VIEW_W / hold_scale;
    cy = hold_top + 0.5f * AB_VIEW_H / hold_scale;
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
    static int first_tick = 1;
    uint32_t lua_p, app_p;
    /* dt is not passed into nativeUpdate. The .so samples gettimeofday and
     * clamps to 0.1s, then GameLua::update steps Box2D at a fixed 1/30.
     * The harness therefore advances plat_time_us by 33333 us per frame. */
    (void)dt;
    if (!started) return;
    lua_p = (uint32_t)(uintptr_t)game_lua();
    app_p = (uint32_t)(uintptr_t)app_ptr();
    if (first_tick) {
        char b[160];
        snprintf(b, sizeof b, "first tick: app=%08x lua=%08x heap=%u peak=%u fail=%u",
                 (unsigned)app_p, (unsigned)lua_p,
                 (unsigned)ab_heap_used(), (unsigned)ab_heap_peak(),
                 (unsigned)ab_malloc_failures());
        plat_log_line(b);
        plat_log_flush();
    }
    memset(&info, 0, sizeof info);
    /* A lua pointer below firmware RAM (0x08) is unrelocated; walking it
     * reboots. nativeUpdate may still create the world. */
    if (ptr_in_ram((void *)(uintptr_t)lua_p)) {
        ab_phys_snapshot(&info);
        if (first_tick) {
            char b[160];
            snprintf(b, sizeof b, "first tick: world=%08x on=%d n=%d",
                     (unsigned)info.world, info.phys_on, info.bodies);
            plat_log_line(b);
            plat_log_flush();
        }
        if (info.dynamic_n > 0 && !info.phys_on) ab_phys_enable(1);
    } else if (first_tick) {
        plat_log_line("first tick: lua pointer not in RAM, skip snapshot");
        plat_log_flush();
    }
    for (i = 0; i < n; i++) {
        int id = q[i].id;
        if (id < 0 || id >= AB_FINGERS) id = 0;
        ab_touch(id, q[i].down, q[i].x, q[i].y);
    }
    if (cam_hold && hold_scale > 1e-4f) apply_cam(hold_scale, hold_left, hold_top);
    else if (ptr_in_ram((void *)(uintptr_t)lua_p)) ab_cam_fit_play();
    /* Do not identity the stacks every frame: cam_hold holds a static view, so
     * Ka3D skips LoadMatrixf when the matrix is unchanged and the world would
     * draw with identity (empty panel). TEXTURE is reloaded by Primitive. */
    fe_stat_tris = 0; fe_stat_draws = 0;
    be_frame_begin();
    if (first_tick) {
        plat_log_line("first tick: nativeUpdate");
        plat_log_flush();
    }
    if (j_update) j_update(ab_jni_env(), ab_jni_renderer());
    if (first_tick) {
        plat_log_line("first tick: nativeUpdate returned");
        plat_log_flush();
        first_tick = 0;
        plat_log_quiet();
    }
    if (cam_hold && hold_scale > 1e-4f) apply_cam(hold_scale, hold_left, hold_top);
    else if (ptr_in_ram((void *)(uintptr_t)lua_p)) ab_cam_fit_play();
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
    void *lua;
    if (!name) return;
    ab_lua_set_number("screenWidth", AB_VIEW_W);
    ab_lua_set_number("screenHeight", AB_VIEW_H);
    ab_lua_set_string("levelName", name);
    ab_lua_set_bool("loadingPageDrawn", 1);
    ab_lua_set_bool("showBG", 1);
    ab_lua_set_bool("showTutorials", 0);
    plat_log_line(name);
    fe_reset_matrices();
    /* Spawn with gravity off so later blocks are not dropped onto the
     * ones that already exist. GameLua::update steps Box2D at 1/30. */
    ab_phys_enable(0);
    lua = game_lua();
    if (lua) wf32((uint8_t *)lua + AB_ACC_OFF, 0.f);
    ab_call_lua("updateLoading");
    /* Hold the castle / whole-level camera so structures and pigs stay in
     * frame. defaultCamera alone slides back to the slingshot after a few
     * seconds (castleCameraTimer). */
    ab_lua_set_number("castleCameraTimer", 60.f);
    ab_lua_set_number("cameraResetTimer", 60.f);
    ab_lua_set_number("cameraAnimationSlider", 1.f);
    ab_lua_set_number("cameraAnimationSliderTarget", 1.f);
    ab_lua_set_bool("allowResetToBirdCamera", 0);
    /* Skip the bird-to-slingshot hop so an idle hold does not walk birds. */
    ab_lua_set_number("levelStartTimer", 100.f);
    ab_lua_set_number("birdToSlingshotAnimationTimer", 100.f);
    ab_call_lua("defaultCamera");
    ab_phys_enable(1);
    score_readable = 1;
    ab_cam_fit_world();
    ab_phys_log(name);
}

float ab_lua_get_number(const char *name) {
    void *lua = game_lua();
    if (!lua || !lua_get_num || !name) return 0.f;
    return lua_get_num(lua, name);
}

int ab_lua_get_bool(const char *name) {
    void *lua = game_lua();
    if (!lua || !lua_get_bool || !name) return 0;
    if (lua_is_bool && !lua_is_bool(lua, name)) return 0;
    if (lua_is_nil && lua_is_nil(lua, name)) return 0;
    return lua_get_bool(lua, name) ? 1 : 0;
}

static void table_xy(void *tab, float *x, float *y) {
    *x = *y = 0.f;
    if (!tab || !lua_get_num) return;
    if (!lua_is_num || lua_is_num(tab, "x")) *x = lua_get_num(tab, "x");
    if (!lua_is_num || lua_is_num(tab, "y")) *y = lua_get_num(tab, "y");
}

int ab_lua_xy(const char *table, float *x, float *y) {
    void *lua = game_lua();
    char tab[32];
    if (!lua || !lua_get_tab || !table || !x || !y) return 0;
    memset(tab, 0, sizeof tab);
    lua_get_tab(tab, lua, table);
    table_xy(tab, x, y);
    tab_done(tab);
    return 1;
}

static int copy_lua_str(void *tab, const char *key, char *out, int n) {
    char str[16];
    const char *s;
    if (!out || n < 1) return 0;
    out[0] = 0;
    if (!tab || !key || !lua_get_str) return 0;
    if (lua_is_str && !lua_is_str(tab, key)) return 0;
    memset(str, 0, sizeof str);
    lua_get_str(str, tab, key);
    s = str_cstr ? str_cstr(str) : 0;
    if (s) {
        strncpy(out, s, (size_t)n - 1);
        out[n - 1] = 0;
    }
    if (str_dtor) str_dtor(str);
    return out[0] != 0;
}

int ab_lua_obj_name(const char *obj, char *out, int n) {
    void *lua = game_lua();
    char tab[32];
    if (!out || n < 1) return 0;
    out[0] = 0;
    if (!lua || !obj) return 0;
    if (lua_is_nil && lua_is_nil(lua, obj)) return 0;
    if (lua_is_str && lua_is_str(lua, obj))
        return copy_lua_str(lua, obj, out, n);
    if (!lua_get_tab) return 0;
    memset(tab, 0, sizeof tab);
    lua_get_tab(tab, lua, obj);
    copy_lua_str(tab, "name", out, n);
    tab_done(tab);
    return out[0] != 0;
}

int ab_body_exists(const char *name) {
    return body_named(name) != 0;
}

void ab_lua_dump_birds(const char *tag) {
    char cur[48], sel[48], fly[48], hop[48], b[400];
    cur[0] = sel[0] = fly[0] = hop[0] = 0;
    ab_lua_obj_name("currentBirdName", cur, (int)sizeof cur);
    ab_lua_obj_name("selectedBird", sel, (int)sizeof sel);
    ab_lua_obj_name("flyingBird", fly, (int)sizeof fly);
    ab_lua_obj_name("birdToSlingshotBirdName", hop, (int)sizeof hop);
    snprintf(b, sizeof b,
             "AB_BIRD %s cur=%s/%d sel=%s/%d fly=%s/%d hop=%s/%d ready=%d fired=%d fill=%d "
             "idx=%.0f shot=%.0f hasFly=%d hasSel=%d moving=%d done=%d fail=%d nextT=%.1f startT=%.1f",
             tag ? tag : "-",
             cur[0] ? cur : "-", cur[0] ? ab_body_exists(cur) : 0,
             sel[0] ? sel : "-", sel[0] ? ab_body_exists(sel) : 0,
             fly[0] ? fly : "-", fly[0] ? ab_body_exists(fly) : 0,
             hop[0] ? hop : "-", hop[0] ? ab_body_exists(hop) : 0,
             ab_lua_get_bool("birdReady"), ab_lua_get_bool("birdFired"),
             ab_lua_get_bool("fillInNextBird"),
             (double)ab_lua_get_number("currentBirdIndex"),
             (double)ab_lua_get_number("birdsShot"),
             ab_lua_has("flyingBird"), ab_lua_has("selectedBird"),
             ab_lua_get_bool("hasMovingObjects"),
             ab_lua_get_bool("levelCompleted"), ab_lua_get_bool("levelFailed"),
             (double)ab_lua_get_number("nextBirdTimer"),
             (double)ab_lua_get_number("levelStartTimer"));
    plat_log_line(b);
}

void ab_lua_dump_shot(const char *tag) {
    void *lua = game_lua();
    float rlen = 0.f, smax = 0.f, srange = 0.f, score = 0.f, ang = 0.f;
    float cx = 0.f, cy = 0.f, cpx = 0.f, cpy = 0.f, rbx = 0.f, rby = 0.f;
    int ready = 0, fill = 0, lbtn = 0, lbtn_m = 0, sel = 0;
    char b[280], tab[32];
    if (!lua) { plat_log_line("AB_SHOT no lua"); return; }
    if (lua_is_num && lua_is_num(lua, "rubberBandLength"))
        rlen = lua_get_num(lua, "rubberBandLength");
    if (lua_is_num && lua_is_num(lua, "shootMaxLength"))
        smax = lua_get_num(lua, "shootMaxLength");
    if (lua_is_num && lua_is_num(lua, "shootRange"))
        srange = lua_get_num(lua, "shootRange");
    if (lua_is_num && lua_is_num(lua, "rubberBandAngle"))
        ang = lua_get_num(lua, "rubberBandAngle");
    if (score_readable) score = ab_lua_get_number("score");
    ready = ab_lua_get_bool("birdReady");
    fill = ab_lua_get_bool("fillInNextBird");
    sel = !(lua_is_nil && lua_is_nil(lua, "selectedBird"));
    table_xy((char *)lua + 96, &cx, &cy);
    if (lua_get_bool) {
        void *kh = (char *)lua + 80;
        if (!lua_is_bool || lua_is_bool(kh, "LBUTTON"))
            lbtn_m = lua_get_bool(kh, "LBUTTON") ? 1 : 0;
    }
    if (lua_get_tab) {
        memset(tab, 0, sizeof tab);
        lua_get_tab(tab, lua, "cursorPhysics");
        table_xy(tab, &cpx, &cpy);
        tab_done(tab);
        memset(tab, 0, sizeof tab);
        lua_get_tab(tab, lua, "rubberBandPos");
        table_xy(tab, &rbx, &rby);
        tab_done(tab);
        memset(tab, 0, sizeof tab);
        lua_get_tab(tab, lua, "keyHold");
        if (lua_get_bool && (!lua_is_bool || lua_is_bool(tab, "LBUTTON")))
            lbtn = lua_get_bool(tab, "LBUTTON") ? 1 : 0;
        tab_done(tab);
    }
    snprintf(b, sizeof b,
             "AB_SHOT %s ready=%d fill=%d sel=%d lbtn=%d/%d rlen=%.3f smax=%.3f srange=%.3f "
             "ang=%.3f cur=%.1f,%.1f phys=%.2f,%.2f rbp=%.2f,%.2f score=%.0f",
             tag ? tag : "-", ready, fill, sel, lbtn, lbtn_m, (double)rlen, (double)smax,
             (double)srange, (double)ang, (double)cx, (double)cy, (double)cpx, (double)cpy,
             (double)rbx, (double)rby, (double)score);
    plat_log_line(b);
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
     * overwrite it with a boolean, which blanked every later frame.
     * updateGame seats the first bird only when levelStartTimer == 0:
     * it sets currentBirdIndex=0 and fillInNextBird so getNextBird(1)
     * is the unfired bird. ab_load_lua_level parks the timer at 100 so
     * an idle hold does not walk birds; this function releases that
     * once. Zeroing it again after shot 1 re-inits index to 0, hop
     * complete then setPosition's the bird that was just launched, and
     * GameLua::setPosition does ldr [body,#0x40] on a destroyed body. */
    ab_lua_set_bool("showTutorials", 0);
    if (ab_lua_get_number("birdsShot") > 0.5f)
        return;
    if (ab_lua_get_bool("birdReady") || ab_lua_get_bool("fillInNextBird"))
        return;
    ab_lua_set_number("levelStartTimer", 0.f);
}

void ab_clear_shot_flags(void) {
    ab_lua_set_bool("dragStarted", 0);
    ab_lua_set_bool("birdFired", 0);
    ab_lua_set_bool("birdSelected", 0);
}

int ab_lua_has(const char *name) {
    void *lua = game_lua();
    if (!lua || !name) return 0;
    if (lua_is_nil && lua_is_nil(lua, name)) return 0;
    return 1;
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
    if (!ptr_in_ram(world)) return;
    /* LuaTable::getNumber throws if the key is missing or not a number.
     * Splash frames have a world but no score yet. */
    if (score_readable) o->score = ab_lua_get_number("score");
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
             "dynY=%.2f..%.2f scale=%.3f vis=%d/%d score=%.0f acc=%.3f heap=%u peak=%u texfail=%d",
             tag ? tag : "-", i.lua, i.world, i.phys_on, i.bodies, i.static_n, i.dynamic_n,
             i.fixtures, (double)i.grav_x, (double)i.grav_y, (double)i.dyn_ymin, (double)i.dyn_ymax,
             (double)i.world_scale, i.vis_all, i.bodies, (double)i.score, (double)i.acc,
             ab_heap_used(), ab_heap_peak(), be_stat_tex_fail);
    plat_log_line(buf);
    plat_log_flush();
}
