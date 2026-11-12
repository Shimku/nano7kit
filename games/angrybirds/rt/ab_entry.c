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
static int pan_on, pan_moved;
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
static float lvl_pig_minx, lvl_pig_maxx, lvl_bird_minx, lvl_bird_maxx;
static float lvl_pig_wx[AB_PHYS_PIG], lvl_pig_wy[AB_PHYS_PIG];
static float lvl_bird_saved_wx[AB_PHYS_BIRD], lvl_bird_saved_wy[AB_PHYS_BIRD];
static int lvl_pig_n, lvl_bird_saved_n, lvl_cam_ok, circ_logged;
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
static void ab_drop_stale_render_objects(void);
static void ab_disarm_fbo_masks(void);
static void ab_latch_user_from_play(void);
static int ab_touch_near_bird(float gx, float gy);
static void ab_pan_from_finger(void);
static void ab_note_level_bodies(const ab_phys_info *o);
static void ab_cam_xf(uint32_t xf);
static void ab_cam_xf_write(uint32_t xf);
static void ab_cam_xf_ctx(void);
static void ab_live_span(float *lo, float *hi);
static int ab_xf_hud(uint32_t xf);
static int ab_looks_like_xf(uint32_t p);
static void ab_patch_returned_xf(uint32_t xf);

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
static int cam_patching;
static int cam_user_live(void) {
    return cam_user && user_scale > 0.10f && user_scale < 1.20f;
}
/* Native drawGame reads GameLua+368/372/380. Lua cameraFunction may
 * overwrite those during update; put the latched swipe back before draw. */
static void ab_force_draw_cam(uint32_t glu) {
    if (!glu) glu = game_lua();
    if (!glu || !guest_ok(glu, 384u)) return;
    gw32(glu + 368u, F2U(user_left));
    gw32(glu + 372u, F2U(user_top));
    gw32(glu + 380u, F2U(user_scale));
    if (app && guest_ok(app, 644u)) {
        gw32(app + 636u, F2U(user_scale));
        gw32(app + 640u, F2U(user_scale));
    }
    ab_cam_xf_ctx();
}
/* Ka3D 2D state lives at EGL_Context+0x10C (getRenderState2D). Image::draw
 * clips with (x+tx)*sx against the 480x320 view. HUD (scale ~1) is unchanged. */
static int ab_xf_hud(uint32_t xf) {
    float sx, tx, ty;
    if (!xf || !guest_ok(xf, 56u)) return 1;
    sx = U2F(gr32(xf + 40u));
    tx = U2F(gr32(xf + 32u));
    ty = U2F(gr32(xf + 36u));
    return sx > 0.90f && sx < 1.10f && tx > -80.f && tx < 520.f && ty > -80.f && ty < 360.f;
}
static int ab_looks_like_xf(uint32_t p) {
    float sx, sy, a00, a01, a10, a11, n0, n1;
    if (!p || (p & 3u) || !guest_ok(p, 56u)) return 0;
    sx = U2F(gr32(p + 40u));
    sy = U2F(gr32(p + 44u));
    if (!(sx > 0.08f && sx < 6.f && sy > 0.08f && sy < 6.f)) return 0;
    if (fabsf(sx - sy) > 0.35f) return 0;
    a00 = U2F(gr32(p + 16u));
    a01 = U2F(gr32(p + 20u));
    a10 = U2F(gr32(p + 24u));
    a11 = U2F(gr32(p + 28u));
    n0 = a00 * a00 + a01 * a01;
    n1 = a10 * a10 + a11 * a11;
    if (n0 < 0.45f || n0 > 2.6f || n1 < 0.45f || n1 > 2.6f) return 0;
    return 1;
}
static int cam_xy_baked;   /* Image::draw x,y already camera-relative */
static int cam_xy_screen;  /* baked dest is already * scale; xf.sx = 1 */
static int in_masked_add;  /* MaskedImage::add composites into an FBO */
static int in_sprite_draw; /* Sprite::draw: world sheet, not HUD Image::draw */
static int ro_clear_pending; /* Hashtable::clear once, on first createBox */
static int imgbake_n;        /* AB_DUMP_RO Image::draw dest samples this level */
/* MaskedImage mutates the shared 2D xf. Save it so a nested Image::draw
 * can use the swipe camera, then put the FBO xf back. */
static uint8_t xf_save_ctx[56], xf_save_glu[56];
static int xf_have_ctx, xf_have_glu;
static uint32_t ab_glu_xf_addr(void) {
    uint32_t glu = game_lua(), c;
    if (!glu || !guest_ok(glu, 28u)) return 0;
    c = gr32(glu + 24u);
    if (!c || !guest_ok(c, 0x10Cu + 56u)) return 0;
    return c + 0x10Cu;
}
static void ab_masked_xf_save(void) {
    uint32_t xf;
    xf_have_ctx = xf_have_glu = 0;
    if (ctx && guest_ok(ctx, 0x10Cu + 56u)) {
        memcpy(xf_save_ctx, G8(ctx + 0x10Cu), 56);
        xf_have_ctx = 1;
    }
    xf = ab_glu_xf_addr();
    if (xf) {
        memcpy(xf_save_glu, G8(xf), 56);
        xf_have_glu = 1;
    }
}
static void ab_masked_xf_restore(void) {
    uint32_t xf;
    if (xf_have_ctx && ctx && guest_ok(ctx, 0x10Cu + 56u))
        memcpy(G8(ctx + 0x10Cu), xf_save_ctx, 56);
    xf = ab_glu_xf_addr();
    if (xf_have_glu && xf)
        memcpy(G8(xf), xf_save_glu, 56);
}
static void ab_cam_xf_write(uint32_t xf) {
    float tx, ty, sc;
    if (!xf || !guest_ok(xf, 56u)) return;
    if (!cam_user_live()) return;
    /* Image::draw dest = (x + xf48 + tx) then * (2*sx/viewport) into NDC.
     * Native xf48/xf52 keep the opening camera; adding our tx on top puts
     * L2 pigs (world ~1100) past +1 and the sprite never emits. Dest bake
     * is always world-left (never already-scaled pixels): a 478-wide theme
     * background at xf.sx=1 fills the view and warps L21. */
    tx = cam_xy_baked ? 0.f : -user_left;
    ty = cam_xy_baked ? 0.f : -user_top;
    sc = cam_xy_screen ? 1.f : user_scale;
    gw32(xf + 32u, F2U(tx));
    gw32(xf + 36u, F2U(ty));
    gw32(xf + 40u, F2U(sc));
    gw32(xf + 44u, F2U(sc));
    gw32(xf + 48u, F2U(0.f));
    gw32(xf + 52u, F2U(0.f));
    /* Keep dest = sprite pivot (UV uses destarg - dest). Identity 2x2 so
     * Image::draw's clip AABB matches (x+tx)*scale; 90° ice otherwise
     * inflates min-x NDC past +1 and the sprite never emits. */
    gw32(xf + 16u, F2U(1.f));
    gw32(xf + 20u, F2U(0.f));
    gw32(xf + 24u, F2U(0.f));
    gw32(xf + 28u, F2U(1.f));
}
static void ab_cam_xf(uint32_t xf) {
    if (!xf || !guest_ok(xf, 56u)) return;
    if (!cam_user_live()) return;
    if (ab_xf_hud(xf)) return;
    ab_cam_xf_write(xf);
}
static void ab_patch_returned_xf(uint32_t xf) {
    /* Image::draw / terrain read the pointer getRenderState2D returns, which
     * is not always EGL_Context+0x10C (a wrapper can hand back a copy). */
    if (!cam_user_live()) return;
    if (!ab_looks_like_xf(xf)) return;
    if (ab_xf_hud(xf)) return;
    ab_cam_xf_write(xf);
}
static void ab_cam_xf_glu(uint32_t glu) {
    uint32_t c;
    if (!glu || !guest_ok(glu, 28u)) return;
    c = gr32(glu + 24u);
    if (c && guest_ok(c, 0x10Cu + 56u))
        ab_cam_xf_write(c + 0x10Cu);
}
static void ab_cam_xf_ctx(void) {
    uint32_t glu;
    if (ctx && guest_ok(ctx, 0x10Cu + 56u))
        ab_cam_xf_write(ctx + 0x10Cu);
    glu = game_lua();
    if (glu) ab_cam_xf_glu(glu);
}
static int masked_depth;
static void ab_icall_patch(uint32_t addr, uint32_t *r0, uint32_t *r1, uint32_t *r2, uint32_t *r3) {
    /* MaskedImage mutates the shared 2D xf. Save it; nested Image::draw
     * writes the swipe camera, then after() puts the FBO xf back. */
    if (addr == 0x6c1b4u) in_sprite_draw++;
    if (addr == 0x9ad4u) ab_disarm_fbo_masks();
    if (addr == 0x130a48u) fe_skip_fbo_blit = 1;
    if (addr == 0x130268u || addr == 0x130a48u) {
        if (masked_depth++ == 0) {
            in_masked_add = 1;
            ab_masked_xf_save();
        }
    }
    /* Image::draw this+12 is the EGL_Context. r2/r3 are dest x,y float bits.
     * World sprites keep world x (L2 pigs at ~1100). Bake the swipe into x,y
     * and clear leftover tx so clip AABB is the play view. FBO-local dest
     * stays in 0..400; negative world x (L8 castle) must still bake. */
    if (addr == 0xb15c4u && r0 && *r0 && guest_ok(*r0, 16u)) {
        uint32_t c = gr32(*r0 + 12u);
        float x = r2 ? U2F(*r2) : 0.f;
        float y = r3 ? U2F(*r3) : 0.f;
        uint32_t srcw = guest_ok(SP, 16u) ? gr32(SP + 8u) : 0;
        /* Screen-space HUD keeps dest in 0..480, y>=0. World sprites sit
         * outside that: L2 pigs x~1100, L8 castle y<0 even when x is in
         * 0..520 (the slingshot origin). Huge BACKGROUND/THEME srcw (L21
         * hills y<-80) stay with lua dest + native xf so they do not shear. */
        int huge = (srcw >= 250u && srcw <= 2048u);
        int world = !huge && (x < -20.f || x > 520.f || y < -20.f);
        /* L8 castle sits near the slingshot origin (dest in 0..520, y>=-20)
         * so the AABB test misses it. Sprite::draw with a camera xf is still
         * world space; HUD Image::draw is not. */
        if (!world && !huge && in_sprite_draw && cam_user_live() &&
            c && guest_ok(c, 0x10Cu + 56u) && !ab_xf_hud(c + 0x10Cu))
            world = 1;
        if (world && r2 && U2F(*r2) > 1000.f) fe_stat_castle_img++;
        if (getenv("AB_DUMP_RO") && world && cam_user_live() && r2 && x > 1000.f) {
            uint32_t xf = (c && guest_ok(c, 0x10Cu + 56u)) ? c + 0x10Cu : 0;
            if (imgbake_n < 12) {
                rt_log("imgbake dest=%.0f,%.0f srcw=%u sprite=%d mask=%d xf=%08x a00=%.2f sx=%.3f",
                       (double)x, (double)y, srcw, in_sprite_draw, in_masked_add, xf,
                       xf ? (double)U2F(gr32(xf + 16u)) : 0.0,
                       xf ? (double)U2F(gr32(xf + 40u)) : 0.0);
                imgbake_n++;
            }
        }
        if (world && !huge && cam_user_live() && r2 && r3) {
            if (srcw < 250u || srcw == 0u || srcw > 2048u) {
                *r2 = F2U((x - user_left) * user_scale);
                *r3 = F2U((y - user_top) * user_scale);
                cam_xy_screen = 1;
            } else {
                *r2 = F2U(x - user_left);
                *r3 = F2U(y - user_top);
                cam_xy_screen = 0;
            }
            cam_xy_baked = 1;
            x = U2F(*r2);
            y = U2F(*r3);
        }
        if (c && guest_ok(c, 0x10Cu + 56u) && (!in_masked_add || cam_xy_baked)) {
            /* Huge BACKGROUND keeps world dest. Write the swipe camera xf;
             * leftover identity 2x2 from a dest-baked ice sprite shears
             * theme-2/3 hills (L21). Do not dest-bake: that warped L1-pan. */
            if (huge) {
                if (cam_user_live()) ab_cam_xf_write(c + 0x10Cu);
            } else if (world || cam_xy_baked) ab_cam_xf_write(c + 0x10Cu);
            else ab_cam_xf(c + 0x10Cu);
        }
    }
    if (cam_patching) return;
    if (!cam_user_live()) return;
    if (addr == AB_SetTopLeft && r1) {
        *r1 = F2U(user_left);
        if (r2) *r2 = F2U(user_top);
    } else if (addr == AB_SetWorldScale && r1) {
        *r1 = F2U(user_scale);
    } else if (addr == 0x7eb0u) {
        /* GameLua::update: write bird/screen tables before cameraFunction. */
        cam_patching = 1;
        ab_commit_user_cam();
        cam_patching = 0;
        ab_force_draw_cam(r0 ? *r0 : 0);
        ab_cam_xf_ctx();
    } else if (addr == 0x9ad4u || addr == 0xa5d8u || addr == 0x6ba0u) {
        /* drawGame / drawBackground / drawForegroundNative */
        ab_force_draw_cam(r0 ? *r0 : 0);
        ab_cam_xf_ctx();
    }
}
static void ab_icall_after(uint32_t addr, uint32_t r0, uint32_t ret) {
    uint32_t glu, c, vtbl, xf, getxf;
    if (addr == 0x130268u || addr == 0x130a48u) {
        if (addr == 0x130a48u) fe_skip_fbo_blit = 0;
        if (masked_depth > 0) masked_depth--;
        if (masked_depth == 0) {
            ab_masked_xf_restore();
            in_masked_add = 0;
        }
        cam_xy_baked = 0;
        cam_xy_screen = 0;
        return;
    }
    if (addr == 0x6c1b4u && in_sprite_draw > 0) in_sprite_draw--;
    if (addr == 0xb15c4u || addr == 0x6c1b4u) {
        if (in_masked_add) ab_masked_xf_restore();
        cam_xy_baked = 0;
        cam_xy_screen = 0;
    }
    if (cam_patching) return;
    if (!cam_user_live()) return;
    /* Nested Image::draw getxf still needs the swipe xf. FBO-local getxf
     * (cam_xy_baked=0) keeps the saved MaskedImage transform. */
    if (in_masked_add && !cam_xy_baked) return;
    /* getRenderState2D returns context+0x10C. Patch that pointer, not a
     * wrapper's this+0x10C, so Image::draw / F_130268 read the swipe. */
    if (addr == 0xa876cu) {
        uint32_t xf = ab_looks_like_xf(ret) ? ret : 0;
        if (!xf && r0 && guest_ok(r0, 0x10Cu + 56u)) xf = r0 + 0x10Cu;
        if (getenv("AB_DUMP_CAM") && xf && guest_ok(xf, 44u) && cam_user_live()) {
            float sx = U2F(gr32(xf + 40u));
            static int ngx;
            if (ngx < 12 && fabsf(sx - user_scale) > 0.04f) {
                rt_log("getxf sx=%.3f tx=%.0f user=%.3f,%.0f hud=%d",
                       (double)sx, (double)U2F(gr32(xf + 32u)),
                       (double)user_scale, (double)user_left, ab_xf_hud(xf));
                ngx++;
            }
        }
        if (ab_looks_like_xf(ret)) {
            /* MaskedImage's FBO xf is sx=1/tx=0, which looks like HUD.
             * Dest-baked world sprites still need xf.sx = user_scale or
             * dest~1600 at min zoom clips (L2-out ice). */
            if (cam_xy_baked || in_masked_add || !ab_xf_hud(ret))
                ab_cam_xf_write(ret);
        } else if (r0 && guest_ok(r0, 0x10Cu + 56u)) {
            if (cam_xy_baked) ab_cam_xf_write(r0 + 0x10Cu);
            else ab_cam_xf(r0 + 0x10Cu);
        }
        return;
    }
    ab_patch_returned_xf(ret);
    if (addr == AB_SetTopLeft || addr == AB_SetWorldScale) {
        /* Lua may pass floats in VFP; F_6000 reads r1/r2. Force the store. */
        ab_force_draw_cam(r0);
        return;
    }
    if (addr != 0xa960u) return; /* GameLua::setRenderState */
    glu = r0 ? r0 : game_lua();
    if (!glu || !guest_ok(glu, 28u)) return;
    c = gr32(glu + 24u);
    if (!c || !guest_ok(c, 4u)) return;
    vtbl = gr32(c);
    if (!vtbl || !guest_ok(vtbl, 92u)) return;
    getxf = gr32(vtbl + 88u);
    if (!getxf) return;
    cam_patching = 1;
    xf = rt_call("xf", getxf, c, 0, 0, 0, 0, 0);
    cam_patching = 0;
    if (ab_looks_like_xf(xf) && !ab_xf_hud(xf))
        ab_cam_xf_write(xf);
    else
        ab_cam_xf(xf);
}
static void ab_cam_clear_play(void) {
    play_ground_b = 0.f;
    play_top0 = 0.f;
    play_left0 = 0.f;
    pinch_play = 0.f;
}
static void reset_view(void) {
    int i;
    fe_scale = 0.75f;
    fe_scale_y = 0.9f;
    fe_off_x = 0.f;
    fe_off_y = 0.f;
    lbutton_held = 0;
    pinch_on = 0;
    pan_on = 0;
    pan_moved = 0;
    cam_user = 0;
    in_masked_add = 0;
    masked_depth = 0;
    in_sprite_draw = 0;
    cam_xy_baked = 0;
    cam_xy_screen = 0;
    ab_cam_clear_play();
    for (i = 0; i < AB_FINGERS; i++) memset(&fed[i], 0, sizeof fed[i]);
}

int ab_start(void) {
    int i;
    reset_view();
    rt_icall_patch = ab_icall_patch;
    rt_icall_after = ab_icall_after;
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
     * One finger on empty space is our pan (play scale or zoomed out);
     * lua would treat it as a slingshot pull or a castle-camera sweep. */
    if (n >= 2) n = 0;
    else if (pan_on) n = 0;
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
    if (began && id == 0 && finger_down_count() == 1 && !pinch_on) {
        int zoomed = cam_user && user_scale < pinch_play - 0.02f;
        int hud = (px > 188.f && py < 52.f) || (px < 110.f && py > 248.f && py < 350.f);
        /* Pause / resume chrome is screen-space. Do not steal those taps
         * as a pan (Theme 2 loads with the pause page up). */
        if (hud) {
            pan_on = 0;
            pan_moved = 0;
        } else if (!zoomed && ab_touch_near_bird(gx, gy)) {
            pan_on = 0;
            pan_moved = 0;
        } else {
            if (!cam_user) ab_latch_user_from_play();
            pan_on = 1;
            pan_moved = 0;
            pan_gx0 = gx;
            pan_gy0 = gy;
            pan_left0 = user_left;
            pan_top0 = user_top;
        }
    }
    if (pan_on && id == 0 && down) {
        if (moved) pan_moved = 1;
        ab_pan_from_finger();
    }
    /* Pan/pinch are owned by the latched camera. Forwarding them as
     * LBUTTON made lua cameraTargetObject / doItAllCamera zoom onto a
     * single block (L2 play-scale swipe was two untextured wood boxes). */
    if (!(pan_on || pinch_on || finger_down_count() >= 2))
        send_touch(began, moved, ended, id, gx, gy);
    if (ended && id == 0) {
        int dragged = pan_on && pan_moved;
        pan_on = 0;
        pan_moved = 0;
        if (dragged) tap_us = 0;
        else if (cam_user && finger_down_count() == 0) {
            uint64_t now = plat_time_us();
            float dx = px - tap_x, dy = py - tap_y;
            if (tap_us && now - tap_us < 400000ull && dx * dx + dy * dy < 40.f * 40.f)
                ab_reset_play_cam();
            tap_us = now;
            tap_x = px;
            tap_y = py;
        }
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
    fe_stat_ingame_ok = 0;
    fe_stat_ingame_clip = 0;
    fe_stat_castle_img = 0;
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
    ab_cam_xf_ctx();
    fe_reset_matrices();
    if (ctx && view_rect) {
        rt_call("setViewport", 0xa9560u, ctx, view_rect, 0, 0, 0, 0);
        rt_call("setOrtho", 0xa8afcu, ctx, 0, 0, 0, 0, 0);
    }
    be_frame_begin();
    if (app && AB_Update) rt_call("update", AB_Update, app, F2U(dt), ctx, 0, 0, 0);
    ab_cam_fit_play();
    if (cam_user && user_scale > 0.10f && user_scale < 1.20f)
        ab_commit_user_cam();
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
    static const char *keys[128];
    static uint32_t ptrs[128];
    static int n;
    int i;
    if (!s) return 0;
    for (i = 0; i < n; i++)
        if (keys[i] == s || (keys[i] && strcmp(keys[i], s) == 0)) return ptrs[i];
    if (n >= 128) return g_strdup(s);
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
    static uint32_t slot[8];
    static int si;
    uint32_t t;
    int i;
    if (!parent || !name) return 0;
    if (!slot[0]) {
        for (i = 0; i < 8; i++) {
            slot[i] = g_malloc(32);
            if (slot[i]) memset(G8(slot[i]), 0, 32);
        }
    }
    t = slot[si];
    si = (si + 1) & 7;
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
    float vw, vh;
    if (!cam) return;
    if (!(sx > 1e-4f)) sx = 0.5f;
    vw = AB_VIEW_W / sx;
    vh = AB_VIEW_H / sx;
    lua_table_set_num(cam, "sx", sx);
    lua_table_set_num(cam, "sy", sx);
    lua_table_set_num(cam, "px", px);
    lua_table_set_num(cam, "py", py);
    lua_table_set_num(cam, "left", left);
    lua_table_set_num(cam, "right", left + vw);
    lua_table_set_num(cam, "top", top);
    lua_table_set_num(cam, "bottom", top + vh);
    /* cameraFunction clamps look-at to these; keep them wider than the
     * whole level so a swipe can reach the pigs. */
    lua_table_set_num(cam, "leftLimit", left - 800.f);
    lua_table_set_num(cam, "rightLimit", left + vw + 800.f);
    lua_table_set_num(cam, "groundLimit", top + vh + 200.f);
    lua_table_set_num(cam, "skyLimit", top - 1600.f);
    lua_table_set_num(cam, "screenWidth", AB_VIEW_W);
    lua_table_set_num(cam, "screenHeight", AB_VIEW_H);
}

static void ab_commit_user_cam(void) {
    uint32_t glu = game_lua(), objs, scr, screen;
    float px, py, vw, vh, lo, hi;
    if (!glu || !cam_user) return;
    if (!(user_scale > 0.10f && user_scale < 1.20f)) return;
    px = user_left + 0.5f * AB_VIEW_W / user_scale;
    py = user_top + 0.5f * AB_VIEW_H / user_scale;
    vw = AB_VIEW_W / user_scale;
    vh = AB_VIEW_H / user_scale;
    rt_call("setWorldScale", AB_SetWorldScale, glu, F2U(user_scale), 0, 0, 0, 0);
    rt_call("setTopLeft", AB_SetTopLeft, glu, F2U(user_left), F2U(user_top), 0, 0, 0);
    /* zoomLevel is a delta on currentZoomedScale in doItAllCamera. Keep the
     * pair equal (0) so lua does not add our scale onto itself. */
    ab_lua_set_number("worldScale", user_scale);
    ab_lua_set_number("currentZoomedScale", user_scale);
    ab_lua_set_number("zoomLevel", 0.f);
    ab_lua_set_number("oldZoomLevel", 0.f);
    ab_lua_set_number("animationWorldScale", user_scale);
    ab_lua_set_number("animationWorldScale2", user_scale);
    /* Bird camera (slider 0). Slider 1 is the castle table, whose sx is
     * still 2.25 on iPad levels and a close-up of one block. */
    ab_lua_set_number("cameraAnimationSlider", 0.f);
    ab_lua_set_number("cameraAnimationSliderTarget", 0.f);
    ab_lua_set_number("sweepSpeed", 0.f);
    ab_lua_set_bool("useLevelLimits", 0);
    ab_lua_set_bool("forceSprings", 0);
    ab_lua_set_bool("dragging", 0);
    ab_live_span(&lo, &hi);
    ab_lua_set_number("leftLimit", lo - 800.f);
    ab_lua_set_number("rightLimit", hi + 800.f);
    ab_lua_set_number("groundLimit", (play_ground_b != 0.f ? play_ground_b : py) + 800.f);
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
    screen = lua_subtable(glu, "screen");
    if (screen) {
        lua_table_set_num(screen, "x", px);
        lua_table_set_num(screen, "y", py);
        lua_table_set_num(screen, "left", user_left);
        lua_table_set_num(screen, "top", user_top);
        lua_table_set_num(screen, "right", user_left + vw);
        lua_table_set_num(screen, "bottom", user_top + vh);
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
        float t, lo, hi, look_x;
        t = (pinch_play - minz) > 1e-4f ? (pinch_play - ns) / (pinch_play - minz) : 1.f;
        if (!(t > 0.f)) t = 0.f;
        if (t > 1.f) t = 1.f;
        /* Full pinch-out frames the slingshot and every pig (the real
         * game's minWorldScale view). Sky beyond the tiles is filled. */
        ab_live_span(&lo, &hi);
        lo -= 80.f;
        hi += 120.f;
        look_x = pinch_cx + t * (0.5f * (lo + hi) - pinch_cx);
        user_left = look_x - 0.5f * AB_VIEW_W / ns;
        user_top = play_ground_b - AB_VIEW_H / ns;
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

static void ab_latch_user_from_play(void) {
    uint32_t glu = game_lua();
    float sc, left, top;
    if (!glu) return;
    sc = cam_user && user_scale > 1e-4f ? user_scale : U2F(gr32(glu + AB_CAM_SCALE));
    left = cam_user ? user_left : U2F(gr32(glu + AB_CAM_LEFT));
    top = cam_user ? user_top : U2F(gr32(glu + AB_CAM_TOP));
    /* Castle intro still has iPad sx 2.25. Rescale so a swipe can latch. */
    if (sc > 1.20f) sc *= AB_VIEW_H / 1024.f;
    if (!(sc > 0.10f && sc < 1.20f)) {
        if (pinch_play > 0.20f && pinch_play < 1.05f) sc = pinch_play;
        else sc = 0.60f;
    }
    user_scale = sc;
    user_left = left;
    user_top = top;
    if (!(pinch_play > 0.20f && pinch_play < 1.05f)) pinch_play = sc;
    if (!(play_ground_b > -4000.f && play_ground_b < 4000.f) || play_ground_b == 0.f)
        play_ground_b = top + AB_VIEW_H / sc;
    if (play_top0 == 0.f) play_top0 = top;
    if (play_left0 == 0.f) play_left0 = left;
    if (!lvl_cam_ok) ab_capture_level_cam();
    cam_user = 1;
}

static int ab_touch_near_bird(float gx, float gy) {
    ab_phys_info i;
    int k;
    float scale, bx, by, dx, dy;
    ab_phys_snapshot(&i);
    scale = i.world_scale > 1e-4f ? i.world_scale : 0.5f;
    if (i.bird_n < 1)
        return gx < 260.f;
    for (k = 0; k < i.bird_n; k++) {
        bx = (i.bird_wx[k] - i.cam_left) * scale;
        by = (i.bird_wy[k] - i.cam_top) * scale;
        dx = gx - bx;
        dy = gy - by;
        if (dx * dx + dy * dy < 70.f * 70.f) return 1;
    }
    return 0;
}

static void ab_pan_from_finger(void) {
    float scale;
    if (!cam_user || !pan_on || !fed[0].down) return;
    scale = user_scale > 1e-4f ? user_scale : 0.4f;
    user_left = pan_left0 + (pan_gx0 - fed[0].gx) / scale;
    user_top = pan_top0 + (pan_gy0 - fed[0].gy) / scale;
    ab_clamp_user_view();
    ab_commit_user_cam();
}

/* One-finger drag on empty space pans. At play scale a grab on a bird is
 * the slingshot; zoomed out, any one finger pans. Origin is latched in
 * ab_touch so lua never sees the pan as a pouch pull. */
static void ab_apply_pan(void) {
    if (pinch_on || finger_down_count() != 1 || !fed[0].down) {
        pan_on = 0;
        return;
    }
    if (!pan_on) return;
    ab_pan_from_finger();
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
    ab_lua_set_bool("showTutorials", 0);
    ab_lua_set_bool("birdReady", 1);
    ab_lua_set_bool("fillInNextBird", 1);
    ab_lua_set_number("levelStartTimer", 10.f);
    ab_call_lua("removePopupMenu");
}

/* lua updateLoading calls loadLevelInternal(levelName), which createBox/createCircle.
 * C++ GameLua::loadLevel only fills the objects table. */
static void ab_xf_reset_ctx(void) {
    uint32_t xf = 0, glu_xf;
    if (ctx && guest_ok(ctx, 0x10Cu + 56u)) xf = ctx + 0x10Cu;
    if (xf && guest_ok(xf, 56u)) {
        gw32(xf + 16u, F2U(1.f)); gw32(xf + 20u, F2U(0.f));
        gw32(xf + 24u, F2U(0.f)); gw32(xf + 28u, F2U(1.f));
        gw32(xf + 32u, F2U(0.f)); gw32(xf + 36u, F2U(0.f));
        gw32(xf + 40u, F2U(1.f)); gw32(xf + 44u, F2U(1.f));
        gw32(xf + 48u, F2U(0.f)); gw32(xf + 52u, F2U(0.f));
    }
    glu_xf = ab_glu_xf_addr();
    if (glu_xf && guest_ok(glu_xf, 56u) && glu_xf != xf) {
        gw32(glu_xf + 16u, F2U(1.f)); gw32(glu_xf + 20u, F2U(0.f));
        gw32(glu_xf + 24u, F2U(0.f)); gw32(glu_xf + 28u, F2U(1.f));
        gw32(glu_xf + 32u, F2U(0.f)); gw32(glu_xf + 36u, F2U(0.f));
        gw32(glu_xf + 40u, F2U(1.f)); gw32(glu_xf + 44u, F2U(1.f));
        gw32(glu_xf + 48u, F2U(0.f)); gw32(glu_xf + 52u, F2U(0.f));
    }
    xf_have_ctx = xf_have_glu = 0;
    in_masked_add = 0;
    masked_depth = 0;
    in_sprite_draw = 0;
    cam_xy_baked = 0;
    cam_xy_screen = 0;
}
static void cam_reset(void) {
    reset_view();
    ab_xf_reset_ctx();
    ab_cam_clear_play();
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

/* Past the painted sky tiles the GL clear used to show cyan. The front
 * end now fills that with sky colour, so the camera may look well above
 * the play view. */
static float ab_sky_extra(float play) {
    (void)play;
    return 1600.f;
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
    {
        ab_phys_info bodies;
        ab_phys_snapshot(&bodies);
        ab_note_level_bodies(&bodies);
    }
    rt_log("cam extents bird=%.0f,%.0f castle=%.0f left=%.0f right=%.0f top=%.0f sx=%.3f min=%.3f pigs=%d %.0f..%.0f birds=%.0f..%.0f",
           (double)bpx, (double)bpy, (double)cpx, (double)bleft, (double)bright,
           (double)top, (double)sx, (double)lvl_min_world, lvl_pig_n,
           (double)lvl_pig_minx, (double)lvl_pig_maxx,
           (double)lvl_bird_minx, (double)lvl_bird_maxx);
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

/* Slingshot (birds / bird camera) through every live pig. Used so a swipe
 * can reach the castle and a pinch-out can fit the whole level. */
static void ab_live_span(float *lo, float *hi) {
    ab_phys_info b;
    float l, h;
    int i;
    l = lvl_left;
    h = lvl_right;
    if (lvl_cam_ok) {
        if (lvl_bird_minx != 0.f && lvl_bird_minx < l) l = lvl_bird_minx;
        if (lvl_bird_px != 0.f && lvl_bird_px < l) l = lvl_bird_px;
        if (lvl_pig_minx > 1.f && lvl_pig_minx < l) l = lvl_pig_minx;
        if (lvl_pig_maxx > h) h = lvl_pig_maxx;
        if (lvl_castle_px > h) h = lvl_castle_px;
    }
    ab_phys_snapshot(&b);
    for (i = 0; i < b.bird_n; i++) {
        if (b.bird_wx[i] < l) l = b.bird_wx[i];
        if (b.bird_wx[i] > h) h = b.bird_wx[i];
    }
    for (i = 0; i < b.pig_n; i++) {
        if (b.pig_wx[i] < l) l = b.pig_wx[i];
        if (b.pig_wx[i] > h) h = b.pig_wx[i];
    }
    for (i = 0; i < b.block_n; i++) {
        if (b.block_wx[i] > h) h = b.block_wx[i];
    }
    if (lo) *lo = l;
    if (hi) *hi = h;
}

static void ab_clamp_user_view(void) {
    float vw, vh, min_left, max_left, lo, hi, extra;
    if (!(user_scale > 0.10f)) return;
    vw = AB_VIEW_W / user_scale;
    vh = AB_VIEW_H / user_scale;
    extra = ab_sky_extra(pinch_play);
    if (play_ground_b != 0.f) {
        float max_b = play_ground_b + 24.f;
        if (user_top + vh > max_b) user_top = max_b - vh;
    }
    if (play_top0 != 0.f && user_top < play_top0 - extra)
        user_top = play_top0 - extra;
    ab_live_span(&lo, &hi);
    /* Whole pig / tower, not a corner on the right edge. Pan keeps play
     * scale; pinch-out is what fits slingshot and pigs in one view. */
    lo -= 120.f;
    hi += 140.f;
    if (hi > lo + 80.f) {
        min_left = lo - 40.f;
        max_left = hi - vw;
        if (max_left < min_left) {
            /* View wider than the level: keep slingshot and pigs in frame. */
            user_left = 0.5f * (lo + hi) - 0.5f * vw;
        } else {
            if (user_left < min_left) user_left = min_left;
            if (user_left > max_left) user_left = max_left;
        }
    }
}

/* lua minWorldScale is the iPhone whole-level floor. If it is missing or
 * stuck at the play camera, fit slingshot and pigs into 480x320. Sky
 * beyond the tiles is filled with sky colour, so this is not raised to
 * keep the camera inside the painted hills. */
static float ab_level_min_scale(float play) {
    uint32_t glu = game_lua();
    float mn = 0.18f, lua_mn = 0.f, lo, hi, span, fit;
    if (!(play > 0.20f && play < 1.10f)) play = 0.70f;
    if (AB_LuaGetNumber && glu) {
        lua_mn = U2F(rt_call("getNumber", AB_LuaGetNumber, glu, intern_str("minWorldScale"), 0, 0, 0, 0));
        if (lua_mn > 0.08f && lua_mn < play - 0.04f) mn = lua_mn;
    }
    ab_live_span(&lo, &hi);
    span = hi - lo;
    if (span > 400.f) {
        fit = AB_VIEW_W / (span + 200.f);
        if (fit > 0.10f && fit < play - 0.04f) {
            if (!(mn < play - 0.04f) || fit < mn) mn = fit;
        }
    }
    if (!(mn < play - 0.04f)) mn = play * 0.45f;
    if (mn < 0.12f) mn = 0.12f;
    if (mn > play - 0.04f) mn = play * 0.45f;
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
    /* Play bottom is the grass line. Pinch-out keeps that edge so the
     * structure stays in the view instead of sliding into empty sky. */
    play_ground_b = top + AB_VIEW_H / sx;
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
            play_ground_b = tp + AB_VIEW_H / sc;
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
        rt_log("LEVEL %s scale=%.3f anim=%.3f left=%.1f top=%.1f ctx=%dx%d obj=%d vis=%d/%d tex=%d fail=%d heap=%u hole=%u circ=%d poly=%d shapebad=%d align=%u draws=%d pigs=%d birds=%d",
               name ? name : "-", (double)i.world_scale, (double)anim, (double)i.cam_left, (double)i.cam_top,
               i.ctx_w, i.ctx_h, i.obj_n, i.vis_all, i.bodies,
               fe_stat_tex_count, be_stat_tex_fail, (unsigned)rt_heap_used(),
               (unsigned)rt_heap_largest_free(), i.circles, i.polygons, i.shape_bad,
               (unsigned)rt_align_faults(), fe_stat_draws, i.pig_n, i.bird_n);
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

void ab_unpause(void) {
    ab_lua_set_bool("inPausePage", 0);
    ab_lua_set_bool("paused", 0);
    ab_lua_set_bool("showPausePage", 0);
    ab_lua_set_bool("pausePageReady", 0);
    ab_lua_set_number("pausePageTimer", -1.f);
    ab_lua_set_number("pausePageAlpha", 0.f);
    ab_lua_set_number("ingamePausePageScroll", 0.f);
    ab_lua_set_number("pauseBGw", 0.f);
}

/* GameLua+0xB8 is Hashtable<String,RenderObjectData> (this+8 = glu+192
 * bucket count, +12 buf, stride 96, occupied +92, b2Body* +24, Image*
 * +32, mask +36, angle +76, chain +88). iOS tore this down with the
 * level scene. updateLoading does not: L2 grow leaves dangling overflow
 * chains and leftover +36 mask pointers that send L3 ice through
 * MaskedImage's empty FBO (brown diamonds). Hashtable::clear / D1+C1
 * before "Removing physics" leaks Box2D bodies (lua destroy walks this
 * table) and during createBox wipes block definitions. After physics
 * is gone, drop instance rows and zero empty-slot mask/angle/chain. */
static int ab_body_in_world(uint32_t world, uint32_t body) {
    uint32_t b;
    int n;
    if (!world || !body || !guest_ok(world, B2_BODY_LIST + 4u)) return 0;
    b = gr32(world + B2_BODY_LIST);
    for (n = 0; b && n < 256; n++) {
        if (b == body) return 1;
        if (!guest_ok(b, 108u)) break;
        b = gr32(b + 104u);
    }
    return 0;
}
static int ab_looks_like_image(uint32_t img) {
    uint32_t vt;
    if (!img || !guest_ok(img, 16u)) return 0;
    if (rt_heap_poisoned(img)) return 0;
    vt = gr32(img);
    /* gr::Image vtable is 0x13faac; other Ka3D images sit in the same page. */
    return vt >= 0x13f000u && vt < 0x150000u;
}
void ab_on_create_object(void) {
    if (!ro_clear_pending) return;
    /* First createBox of Loading blocks: physics from the previous level
     * is still live. Only unoccupy rows whose Image was already freed. */
    ro_clear_pending = 0;
    ab_drop_stale_render_objects();
    rt_log("ro-clear instances");
}
void ab_after_remove_physics(void) {
    /* lua just destroyed Box2D bodies. Instance slots and overflow chain
     * nodes still hold Image, mask, and angle from L2 until Loading level
     * inserts L3. Scrub them so drawGame does not blit leftover masks. */
    ab_drop_stale_render_objects();
    rt_log("ro-after-physics");
}
/* GameLua+460 is the MaskedImage compositor. L2 is the first chapter-1
 * level that uses it; after that the no-op FBO attachment stays a solid
 * 2x2 and L3 pan ice is untextured rectangles / brown diamonds. iOS
 * rebuilt this with the level scene. */
#define AB_MASKED_OFF  460u
#define AB_MASKED_D1   0x1315d8u
#define AB_MASKED_C1   0x1315d4u
static void ab_reset_masked_image(void) {
    uint32_t glu = game_lua(), mi, ctx, vt;
    if (!glu || !guest_ok(glu, AB_MASKED_OFF + 4u)) return;
    mi = gr32(glu + AB_MASKED_OFF);
    if (!mi || !guest_ok(mi, 64u)) return;
    vt = gr32(mi);
    /* Pointer to a MaskedImage (vtable 0x141384). A code address here
     * would mean the object is embedded at glu+460; do not D1 that. */
    if (vt < 0x13f000u || vt >= 0x150000u) return;
    ctx = gr32(mi + 8u);
    if (!ctx || !guest_ok(ctx, 16u)) return;
    rt_call("MaskedImageD1", AB_MASKED_D1, mi, 0, 0, 0, 0, 0);
    rt_call("MaskedImageC1", AB_MASKED_C1, mi, ctx, 0, 0, 0, 0);
    xf_have_ctx = xf_have_glu = 0;
    in_masked_add = 0;
    masked_depth = 0;
    rt_log("masked-reset mi=%08x ctx=%08x vt=%08x", mi, ctx, vt);
}
static void ab_disarm_fbo_masks(void) {
    uint32_t glu = game_lua(), n, buf, i, p, next;
    int guard;
    if (!glu || !guest_ok(glu, 200u)) return;
    n = gr32(glu + 192u);
    buf = gr32(glu + 196u);
    if (!n || n > 4096u || !buf || !guest_ok(buf, n * 96u)) return;
    for (i = 0; i < n; i++) {
        p = buf + i * 96u;
        guard = 0;
        while (p && guest_ok(p, 96u) && guard++ < 64) {
            next = gr32(p + 88u);
            if (G8(p + 92u)[0] && gr32(p + 36u))
                gw32(p + 36u, 0);
            if (!next || next == p) break;
            p = next;
        }
    }
}
static void ab_scrub_empty_slot(uint32_t p) {
    gw32(p + 24u, 0);   /* body */
    gw32(p + 32u, 0);   /* Image* */
    gw32(p + 36u, 0);   /* mask / MaskedImage source */
    gw32(p + 76u, 0);   /* angle (drawGame cos/sin) */
    G8(p + 92u)[0] = 0;
    /* Do not zero +88: an unoccupied head may chain to a live overflow
     * node. Only poison-dangling chains are cut in the walk. */
}
static void ab_drop_stale_render_objects(void) {
    uint32_t glu = game_lua(), world, n, buf, i, p, next, body, img, mask;
    int guard, dropped = 0, kept = 0, nmask = 0, nrot = 0;
    float ang;
    if (!glu || !guest_ok(glu, 200u)) return;
    world = gr32(glu + AB_WORLD_OFF);
    n = gr32(glu + 192u);
    buf = gr32(glu + 196u);
    if (!n || n > 4096u || !buf || !guest_ok(buf, n * 96u)) return;
    for (i = 0; i < n; i++) {
        p = buf + i * 96u;
        guard = 0;
        while (p && guest_ok(p, 96u) && guard++ < 64) {
            next = gr32(p + 88u);
            if (next && (rt_heap_poisoned(next) || !guest_ok(next, 96u))) {
                gw32(p + 88u, 0);
                next = 0;
            }
            if (G8(p + 92u)[0]) {
                body = gr32(p + 24u);
                img = gr32(p + 32u);
                mask = gr32(p + 36u);
                /* body==0 slots are block definitions from "Loading blocks".
                 * Instance rows (a body that is gone, a poisoned Image, or a
                 * leftover mask pointing at freed memory) go stale across
                 * levels. Do not test rt_heap_poisoned on interior Box2D
                 * pointers: those are not always the malloc payload start. */
                if (body && (!ab_body_in_world(world, body) ||
                             (img && guest_ok(img, 4u) && gr32(img) == 0xDBDBDBDBu) ||
                             (mask && rt_heap_poisoned(mask)))) {
                    ab_scrub_empty_slot(p);
                    dropped++;
                } else {
                    kept++;
                    if (mask) {
                        /* FBO bind is a no-op. MaskedImage::add then blits a
                         * 2x2 and glDisable(TEXTURE_2D) sticks, so the next
                         * level's ice is untextured. Draw the sprite. */
                        gw32(p + 36u, 0);
                        nmask++;
                    }
                    ang = U2F(gr32(p + 76u));
                    if (ang > 0.05f || ang < -0.05f) nrot++;
                }
            } else {
                /* Unoccupied head: leftover Image or mask still draw if
                 * drawGame ever skips the occupied byte. Zero payload,
                 * keep a live overflow chain. */
                gw32(p + 24u, 0);
                gw32(p + 32u, 0);
                gw32(p + 36u, 0);
                gw32(p + 76u, 0);
            }
            if (!next || next == p) break;
            p = next;
        }
    }
    rt_log("ro-stale drop=%d keep=%d buckets=%u mask=%d rot=%d",
           dropped, kept, n, nmask, nrot);
    if (getenv("AB_DUMP_RO") && kept > 0) {
        int shown = 0;
        for (i = 0; i < n && shown < 40; i++) {
            p = buf + i * 96u;
            guard = 0;
            while (p && guest_ok(p, 96u) && guard++ < 64 && shown < 40) {
                next = gr32(p + 88u);
                if (G8(p + 92u)[0]) {
                    rt_log("ro-slot bkt=%u p=%08x body=%08x img=%08x mask=%08x ang=%.2f ch=%08x",
                           i, p, gr32(p + 24u), gr32(p + 32u), gr32(p + 36u),
                           (double)U2F(gr32(p + 76u)), next);
                    shown++;
                } else if (gr32(p + 32u) || gr32(p + 36u) || gr32(p + 88u)) {
                    rt_log("ro-empty bkt=%u p=%08x img=%08x mask=%08x ch=%08x",
                           i, p, gr32(p + 32u), gr32(p + 36u), next);
                    shown++;
                }
                if (!next || next == p) break;
                p = next;
            }
        }
    }
}

void ab_load_lua_level(const char *name) {
    if (!name) return;
    cam_reset();
    pinch_on = 0;
    cam_user = 0;
    pan_on = 0;
    lvl_cam_ok = 0;
    lvl_pig_n = 0;
    lvl_bird_saved_n = 0;
    circ_logged = 0;
    lvl_pig_minx = lvl_pig_maxx = 0.f;
    lvl_bird_minx = lvl_bird_maxx = 0.f;
    ab_lua_gc();
    ab_lua_iphone_screen();
    ab_unpause();
    ab_lua_set_bool("loadedObjects", 0);
    ab_lua_set_bool("loadingPageDrawn", 0);
    ab_lua_set_string("levelName", name);
    ab_lua_set_bool("loadingPageDrawn", 1);
    ab_lua_set_bool("showBG", 1);
    ab_lua_set_bool("showTutorials", 0);
    rt_log("ab_load_lua_level %s", name);
    imgbake_n = 0;
    fe_reset_matrices();
    ab_reset_masked_image();
    ro_clear_pending = 1;
    ab_call_lua("updateLoading");
    ro_clear_pending = 0;
    ab_drop_stale_render_objects();
    ab_reset_masked_image();
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
    lvl_pig_n = 0;
    lvl_bird_saved_n = 0;
    circ_logged = 0;
    lvl_pig_minx = lvl_pig_maxx = 0.f;
    lvl_bird_minx = lvl_bird_maxx = 0.f;
    ab_lua_gc();
    ab_lua_iphone_screen();
    ab_unpause();
    ab_lua_set_number("zoomLevel", 0.f);
    ab_lua_set_number("oldZoomLevel", 0.f);
    ab_lua_set_number("currentZoomedScale", 0.f);
    ab_lua_set_bool("loadedObjects", 0);
    ab_lua_set_bool("loadingPageDrawn", 0);
    ab_lua_set_string("levelName", name);
    ab_lua_set_bool("loadingPageDrawn", 1);
    ab_lua_set_bool("showBG", 1);
    ab_lua_set_bool("showTutorials", 0);
    rt_log("ab_goto_level %s", name);
    imgbake_n = 0;
    plat_log_step("goto", rt_heap_used());
    fe_reset_matrices();
    ab_reset_masked_image();
    ro_clear_pending = 1;
    ab_call_lua("updateLoading");
    ro_clear_pending = 0;
    ab_drop_stale_render_objects();
    ab_reset_masked_image();
    ab_iphone_cameras();
    ab_phys_enable(1);
}

void ab_phys_snapshot(ab_phys_info *o) {
    uint32_t glu, world, list, b;
    int nwalk, nmax, nc = 0, ci;
    struct { float wx, wy, rad, rest; int npoly; } circs[48];
    float bird_minx;
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
        int type, nfix, fi, ncirc = 0, npoly = 0;
        float x, y, wx, wy, sx, sy, rad0 = 0.f, pig_rest = 0.f;
        uint32_t fx, fx0;
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
        fx0 = fx = gr32(b + 108u);
        for (fi = 0; fx && fi < 32; fi++) {
            uint32_t sh;
            int st;
            if (!guest_ok(fx, 56u)) break;
            sh = gr32(fx + 28u);
            if (sh && guest_ok(sh, 12u)) {
                st = (int)gr32(sh + 4u);
                if (st == 0) {
                    float rad = U2F(gr32(sh + 8u));
                    o->circles++;
                    ncirc++;
                    if (!(rad == rad) || rad <= 0.f || rad > 50.f) o->shape_bad++;
                    else if (ncirc == 1) {
                        static const uint32_t roff[6] = {12u, 16u, 32u, 36u, 48u, 52u};
                        int k;
                        rad0 = rad;
                        for (k = 0; k < 6; k++) {
                            float v = U2F(gr32(fx + roff[k]));
                            if (v >= 0.01f && v <= 0.12f) { pig_rest = v; break; }
                        }
                    }
                } else if (st == 1) {
                    int nv = guest_ok(sh, 152u) ? (int)gr32(sh + 148u) : -1;
                    float rad = U2F(gr32(sh + 8u));
                    o->polygons++;
                    npoly++;
                    if (nv < 3 || nv > 8 || !(rad == rad)) o->shape_bad++;
                } else {
                    o->shape_bad++;
                }
            }
            fx = gr32(fx + 20u);
        }
        if (nfix <= 0 && fi > 0) o->fixtures += fi;
        if (type == 2 && ncirc >= 1 && nc < 48) {
            circs[nc].wx = wx;
            circs[nc].wy = wy;
            circs[nc].rad = rad0;
            circs[nc].rest = pig_rest;
            circs[nc].npoly = npoly;
            nc++;
            if (!circ_logged && nc <= 16)
                rt_log("circ wx=%.0f wy=%.0f rad=%.3f rest=%.4f npoly=%d",
                       (double)wx, (double)wy, (double)rad0, (double)pig_rest, npoly);
        }
        if (type == 2 && npoly >= 1 && o->block_n < AB_PHYS_BLOCK) {
            o->block_wx[o->block_n] = wx;
            o->block_wy[o->block_n] = wy;
            o->block_n++;
        }
        (void)fx0;
        b = gr32(b + 104u);
    }
    bird_minx = 1e9f;
    for (ci = 0; ci < nc; ci++)
        if (circs[ci].npoly == 0 && circs[ci].wx < bird_minx) bird_minx = circs[ci].wx;
    if (!(bird_minx < 1e8f)) bird_minx = lvl_cam_ok ? lvl_bird_px : 0.f;
    for (ci = 0; ci < nc; ci++) {
        int is_pig = 0;
        /* Leftmost circles are the slingshot birds; everything further right
         * with no polygon fixture is a pig (SmallPiglette on L2 sits ~1000
         * world units out, but a helmet pig can be closer). */
        /* Live pig bodies: circle-only, right of the slingshot birds.
         * piglette restitution is 0.05; StoneBlock8 / fruit use 0.10;
         * birds use 0. SmallPiglette radius is 1.075 (not ~1.20). */
        if (circs[ci].npoly == 0 && circs[ci].wx > bird_minx + 80.f) {
            float rest = circs[ci].rest;
            /* piglette restitution is 0.05. Radius fallback counted
             * round wood / helmet extras (L21 7 vs 5 live pigs). */
            if (rest >= 0.03f && rest <= 0.07f)
                is_pig = 1;
        }
        if (is_pig) {
            /* A leftover circle from the previous level can sit far left of
             * this castle (L21 sequential classified 6, isolation 5). */
            if (lvl_pig_n > 0 && circs[ci].wx < lvl_pig_minx - 80.f)
                is_pig = 0;
        }
        if (is_pig) {
            if (o->pig_n < AB_PHYS_PIG) {
                o->pig_wx[o->pig_n] = circs[ci].wx;
                o->pig_wy[o->pig_n] = circs[ci].wy;
                o->pig_n++;
            }
        } else if (circs[ci].npoly == 0 && circs[ci].rest < 0.02f &&
                   o->bird_n < AB_PHYS_BIRD) {
            o->bird_wx[o->bird_n] = circs[ci].wx;
            o->bird_wy[o->bird_n] = circs[ci].wy;
            o->bird_n++;
        }
    }
    if (o->static_n == 0) { o->stat_ymin = 0.f; o->stat_ymax = 0.f; }
    if (o->dynamic_n == 0) { o->dyn_ymin = 0.f; o->dyn_ymax = 0.f; }
    /* Min zoom can drop a pig from the circle walk (L2-out 1/2). Keep the
     * captured play-scale set. Do not ratchet extras from a bad first
     * snapshot (L21 7 vs 5 live pigs). */
    if (o->world_scale < 0.40f && o->pig_n < lvl_pig_n) {
        for (ci = 0; ci < lvl_pig_n && ci < AB_PHYS_PIG; ci++) {
            o->pig_wx[ci] = lvl_pig_wx[ci];
            o->pig_wy[ci] = lvl_pig_wy[ci];
        }
        o->pig_n = lvl_pig_n;
    }
    if (o->world_scale < 0.40f && o->bird_n < lvl_bird_saved_n) {
        for (ci = 0; ci < lvl_bird_saved_n && ci < AB_PHYS_BIRD; ci++) {
            o->bird_wx[ci] = lvl_bird_saved_wx[ci];
            o->bird_wy[ci] = lvl_bird_saved_wy[ci];
        }
        o->bird_n = lvl_bird_saved_n;
    }
    circ_logged = 1;
    if (!(o->world_scale < 0.40f && o->pig_n <= lvl_pig_n && lvl_pig_n > 0))
        ab_note_level_bodies(o);
}

static void ab_note_level_bodies(const ab_phys_info *o) {
    int i;
    if (!o) return;
    if (o->pig_n > 0) {
        lvl_pig_minx = o->pig_wx[0];
        lvl_pig_maxx = o->pig_wx[0];
        for (i = 0; i < o->pig_n && i < AB_PHYS_PIG; i++) {
            lvl_pig_wx[i] = o->pig_wx[i];
            lvl_pig_wy[i] = o->pig_wy[i];
            if (o->pig_wx[i] < lvl_pig_minx) lvl_pig_minx = o->pig_wx[i];
            if (o->pig_wx[i] > lvl_pig_maxx) lvl_pig_maxx = o->pig_wx[i];
        }
        lvl_pig_n = o->pig_n;
    }
    if (o->bird_n > 0) {
        lvl_bird_minx = o->bird_wx[0];
        lvl_bird_maxx = o->bird_wx[0];
        lvl_bird_saved_n = o->bird_n < AB_PHYS_BIRD ? o->bird_n : AB_PHYS_BIRD;
        for (i = 0; i < lvl_bird_saved_n; i++) {
            lvl_bird_saved_wx[i] = o->bird_wx[i];
            lvl_bird_saved_wy[i] = o->bird_wy[i];
            if (o->bird_wx[i] < lvl_bird_minx) lvl_bird_minx = o->bird_wx[i];
            if (o->bird_wx[i] > lvl_bird_maxx) lvl_bird_maxx = o->bird_wx[i];
        }
    }
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
        ab_lua_set_number("cameraAnimationSlider", 0.f);
        ab_lua_set_number("cameraAnimationSliderTarget", 0.f);
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
