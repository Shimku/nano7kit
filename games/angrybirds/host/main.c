/* host harness for the recompiled Angry Birds (nano7kit, MIT):
 * runs the engine headless, writes PPM frames, plays a scripted slingshot drag. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <execinfo.h>
#include <unistd.h>
#include "../rt/rt.h"
#include "../rt/rt_plat.h"
#include "../rt/fngl.h"
#include "ab_entry.h"

extern uint8_t be_fb[432][240][4];
extern int be_stat_pvrtc, be_stat_uncompressed, be_tex_peak_bytes, be_check_tiny, be_frame_tris_max, be_frame_big_max;
extern int be_stat_tex_fail;
static const char *data_dir, *img_path, *out_dir;
static uint64_t fake_us;
void plat_log_line(const char *l) { fprintf(stderr, "%s\n", l); }
void plat_log_flush(void) {}
void plat_trace(void) { void *b[24]; int n = backtrace(b, 24); char **s = backtrace_symbols(b, n);
    fprintf(stderr, "  at"); for (int i = 1; i < n; i++) { const char *p = strchr(s[i], '('); if (p && p[1] == 'F') fprintf(stderr, " %.*s", (int)(strcspn(p + 1, "+)")), p + 1); } fprintf(stderr, "\n"); }
void *plat_os_alloc(uint32_t n) { return calloc(1, n ? n : 1); }
void plat_os_free(void *p) { free(p); }
uint32_t plat_os_largest(void) { return 8u << 20; }   /* host has plenty; the nano uses hb_os_heap_largest */
void plat_fatal(const char *why) { fprintf(stderr, "FATAL: %s\n", why); plat_trace(); exit(3); }
uint64_t plat_time_us(void) { return fake_us; }
uint32_t plat_load_image(void *dst, uint32_t size) {
    FILE *f = fopen(img_path, "rb"); if (!f) return 0;
    uint32_t n = (uint32_t)fread(dst, 1, size, f); fclose(f); return n;
}
static void path_of(char *buf, size_t n, const char *name) {
    snprintf(buf, n, "%s/docs/%s", out_dir, name);
    struct stat s;
    if (stat(buf, &s) == 0) return;
    snprintf(buf, n, "%s/%s", data_dir, name);
}
int32_t plat_file_size(const char *name) { if (getenv("FN_FILELOG")) fprintf(stderr, "file %s\n", name); char p[512]; struct stat s; path_of(p, sizeof p, name); return stat(p, &s) ? -1 : (int32_t)s.st_size; }
int32_t plat_file_read(const char *name, uint32_t off, void *dst, uint32_t n) {
    char p[512]; path_of(p, sizeof p, name);
    FILE *f = fopen(p, "rb"); if (!f) return -1;
    fseek(f, (long)off, SEEK_SET); int32_t got = (int32_t)fread(dst, 1, n, f); fclose(f); return got;
}
int plat_file_write(const char *name, const void *src, uint32_t n) {
    char p[512]; snprintf(p, sizeof p, "%s/docs", out_dir); mkdir(p, 0755);
    snprintf(p, sizeof p, "%s/docs/%s", out_dir, name);
    FILE *f = fopen(p, "wb"); if (!f) return -1; fwrite(src, 1, n, f); fclose(f);
    fprintf(stderr, "saved %s (%u bytes)\n", name, n); return 0;
}
static int play_pixels(void) {
    /* Count non-white, non-HUD-cyan panel pixels. Empty later levels were
     * physics-in-view but only a cyan bar on white. */
    int n = 0, y, x;
    for (y = 0; y < 432; y++) {
        for (x = 0; x < 240; x++) {
            unsigned r = be_fb[y][x][0], g = be_fb[y][x][1], b = be_fb[y][x][2];
            if (r >= 230 && g >= 230 && b >= 230) continue;
            if (b > 200 && g > 180 && r < 180) continue;
            n++;
        }
    }
    return n;
}
static int panel_black(void) {
    /* Device framebuffer captures are raw 240x432. Uncleared panel shows as
     * black (test 4 shot2/3); the host software clear is often cyan, so this
     * is the nano-visible void. */
    int n = 0, y, x;
    for (y = 0; y < 432; y++) {
        for (x = 0; x < 240; x++) {
            unsigned r = be_fb[y][x][0], g = be_fb[y][x][1], b = be_fb[y][x][2];
            if (r < 12 && g < 12 && b < 12) n++;
        }
    }
    return n;
}
static void dump_panel(const char *name) {
    char p[512]; snprintf(p, sizeof p, "%s/%s.ppm", out_dir, name);
    FILE *f = fopen(p, "wb"); if (!f) return;
    fprintf(f, "P6 240 432 255\n");
    for (int y = 0; y < 432; y++)
        for (int x = 0; x < 240; x++)
            fwrite(be_fb[y][x], 1, 3, f);
    fclose(f);
}
static void shot_panel(int frame) {
    /* Same orientation as a nano framebuffer grab (240x432 portrait, y down). */
    char p[32]; snprintf(p, sizeof p, "panel-%05d", frame);
    dump_panel(p);
}
static void shot(int frame) {
    /* Same home-on-right landscape dump as PvZ n7soft_shot: rotate the portrait
     * panel (240x432, y down) so Home is on the right. A raw 240x432 P6 looked
     * "upright" in a portrait viewer and hid the 180-turn / horizontal mirror
     * that the iPod shows when held like the other landscape nano7kit apps. */
    char p[512]; snprintf(p, sizeof p, "%s/frame-%05d.ppm", out_dir, frame);
    FILE *f = fopen(p, "wb"); if (!f) return;
    fprintf(f, "P6 432 240 255\n");
    for (int gy = 0; gy < 240; gy++)
        for (int gx = 0; gx < 432; gx++) {
            int px = 239 - gy, py = gx;
            fwrite(be_fb[py][px], 1, 3, f);
        }
    fclose(f);
    shot_panel(frame);
}
static void wait_frames(int n) {
    int f;
    for (f = 0; f < n; f++) { fake_us += 33333; ab_frame(1.f / 30.f); }
}
static void tap_at(float x, float y) {
    ab_touch(0, 1, x, y);
    fake_us += 33333; ab_frame(1.f / 30.f);
    fake_us += 33333; ab_frame(1.f / 30.f);
    ab_touch(0, 0, x, y);
    fake_us += 33333; ab_frame(1.f / 30.f);
}
static int level_sane(const char *name, ab_phys_info *a) {
    int pix, blk, bad = 0;
    ab_phys_snapshot(a);
    pix = play_pixels();
    blk = panel_black();
    if (a->dynamic_n < 1) bad = 1;
    if (a->vis_dyn < 1 || a->vis_all < 2) bad = 1;
    if (a->world_scale < 0.22f || a->world_scale > 1.05f) bad = 1;
    if (pix < 800) bad = 1;
    if (blk > 240 * 432 / 12) bad = 1;
    if (be_stat_tex_fail) bad = 1;
    fprintf(stderr, "AB_GESTURE_TEST %s dyn=%d vis=%d/%d scale=%.3f pix=%d black=%d tex=%d fail=%d %s\n",
            name, a->dynamic_n, a->vis_dyn, a->vis_all, (double)a->world_scale,
            pix, blk, fe_stat_tex_count, be_stat_tex_fail, bad ? "FAIL" : "ok");
    return !bad;
}
static void pinch_out(int frames) {
    int i;
    ab_touch(0, 1, 80.f, 216.f);
    ab_touch(1, 1, 160.f, 216.f);
    fake_us += 33333; ab_frame(1.f / 30.f);
    fake_us += 33333; ab_frame(1.f / 30.f);
    for (i = 1; i <= frames; i++) {
        float k = (float)i / (float)frames;
        ab_touch(0, 1, 80.f - 50.f * k, 216.f);
        ab_touch(1, 1, 160.f + 50.f * k, 216.f);
        fake_us += 33333; ab_frame(1.f / 30.f);
    }
    for (i = 0; i < 8; i++) { fake_us += 33333; ab_frame(1.f / 30.f); }
    ab_touch(0, 0, 30.f, 216.f);
    ab_touch(1, 0, 210.f, 216.f);
    fake_us += 33333; ab_frame(1.f / 30.f);
}
int main(int argc, char **argv) {
    if (argc < 5) { fprintf(stderr, "usage: %s DATA_DIR guest.img OUT_DIR FRAMES [SHOT_EVERY] [script]\n", argv[0]); return 2; }
    data_dir = argv[1]; img_path = argv[2]; out_dir = argv[3];
    int frames = atoi(argv[4]), every = argc > 5 ? atoi(argv[5]) : 30;
    const char *script = argc > 6 ? argv[6] : "";
    fngl_reset();
    if (getenv("FN_LETTERBOX")) { fe_scale_y = 0.75f; fe_off_y = 36.f; }   /* the older 0.75 letterboxed picture */
    if (getenv("FN_TEX_SHIFT")) fe_tex_shift = atoi(getenv("FN_TEX_SHIFT"));
    fe_atlas_max_side = getenv("FN_ATLAS") ? atoi(getenv("FN_ATLAS")) : fe_atlas_max_side;
    fe_tex_big_shift = getenv("FN_TEX_BIG") ? atoi(getenv("FN_TEX_BIG")) : fe_tex_shift;
    /* Same 2 MB guest block as nano BLOCK_MAX. A 24 MB host block hid the
     * MENU.png 515100-byte new[] fragmentation the iPod hits. */
    uint32_t size = 0x200000u;
    if (getenv("AB_GUEST_BLOCK")) size = (uint32_t)strtoul(getenv("AB_GUEST_BLOCK"), 0, 0);
    void *block = malloc(size);
    if (!block) { fprintf(stderr, "host block %u failed\n", size); return 1; }
    if (rt_init(block, size)) return 1;
    if (ab_start()) return 1;
    fprintf(stderr, "init ok: guest block %u heap %u used %u peak %u\n",
            size, rt_heap_size(), rt_heap_used(), rt_heap_peak());
    if (getenv("AB_PHYS_TEST")) {
        static const char *levels[] = { "Level1", "Level2", "Level3", "Level4" };
        int warm = atoi(getenv("AB_PHYS_WARM") ? getenv("AB_PHYS_WARM") : "200");
        int steps = atoi(getenv("AB_PHYS_STEPS") ? getenv("AB_PHYS_STEPS") : "300");
        int fail = 0, li;
        for (int f = 0; f < warm; f++) { fake_us += 33333; ab_frame(1.f / 30.f); }
        ab_call_lua("gotoFirstLevel");
        ab_phys_enable(1);
        for (int f = 0; f < 30; f++) { fake_us += 33333; ab_frame(1.f / 30.f); }
        /* TUTORIAL_OK gold check, measured on the PvZ-style 432x240 dump
         * (landscape ~324,190 -> panel 49,324). Dismisses the overlay so lua
         * itself sets physicsEnabled; ab_frame_q also Steps if it stays off. */
        ab_touch(0, 1, 49.f, 324.f);
        fake_us += 33333; ab_frame(1.f / 30.f);
        fake_us += 33333; ab_frame(1.f / 30.f);
        ab_touch(0, 0, 49.f, 324.f);
        fake_us += 33333; ab_frame(1.f / 30.f);
        ab_phys_log("warm");
        for (li = 0; li < 4; li++) {
            int f, ok0, ok;
            ab_phys_info a, b;
            if (li > 0) {
                /* lua loadLevelInternal spawns Box2D; C++ loadLevel does not.
                 * loadNextLevel is the in-game next button; ab_load_lua_level is
                 * the map-select path (set levelName + updateLoading). */
                ab_load_lua_level(levels[li]);
                ab_phys_enable(1);
                ab_phys_log(levels[li]);
            }
            ab_phys_enable(1);
            ab_phys_snapshot(&a);
            ok0 = ab_phys_resting(&a);
            for (f = 0; f < steps; f++) { fake_us += 33333; ab_frame(1.f / 30.f); }
            ab_phys_snapshot(&b);
            ab_phys_log(levels[li]);
            ok = ab_phys_resting(&b);
            fprintf(stderr, "AB_PHYS_TEST %s: start rest=%d n=%d st=%d dyn=%d fix=%d dynY=%.2f after %d rest=%d n=%d st=%d dyn=%d fix=%d dynY=%.2f..%.2f stY=%.2f\n",
                    levels[li], ok0, a.bodies, a.static_n, a.dynamic_n, a.fixtures, (double)a.dyn_ymin,
                    steps, ok, b.bodies, b.static_n, b.dynamic_n, b.fixtures,
                    (double)b.dyn_ymin, (double)b.dyn_ymax, (double)b.stat_ymin);
            /* must not auto-win immediately (bodies gone / fallen) and must rest on ground */
            if (!ok || b.dynamic_n < 1 || (ok0 && b.dynamic_n == 0)) fail++;
            if (every) shot(1000 + li);
        }
        fprintf(stderr, "AB_PHYS_TEST %s (%d levels failed)\n", fail ? "FAIL" : "PASS", fail);
        if (rt_malloc_failures()) {
            fprintf(stderr, "FATAL: %u guest allocations failed\n", rt_malloc_failures());
            return 3;
        }
        return fail ? 1 : 0;
    }
    if (getenv("AB_PLAY_TEST")) {
        /* Splash (auto) -> main menu PLAY -> level select -> Level 1 ->
         * intro cutscene -> TUTORIAL_OK -> pan to slingshot -> pull. */
        int fail = 0, i, flying = 0;
        ab_phys_info a, b, mid;
        wait_frames(200);
        if (every) shot(1);
        tap_at(105.f, 216.f);                         /* MENU_PLAY lua (240,180) */
        wait_frames(45);
        if (every) shot(2);
        /* Level 1: first LS_LEVEL_BG_NORMAL_OPEN. 432x240 dump ~32,88 -> panel 151,32. */
        tap_at(151.f, 32.f);
        wait_frames(20);
        if (every) shot(3);
        /* Opening cutscene (birds_intro) does not skip on a tap; wait until lua
         * loadLevelInternal has spawned Box2D bodies, then dismiss TUTORIAL_OK. */
        for (i = 0; i < 600; i++) {
            fake_us += 33333; ab_frame(1.f / 30.f);
            if (i == 30 || i == 90 || i == 180) tap_at(105.f, 216.f);
            ab_phys_snapshot(&a);
            if (a.dynamic_n > 0) break;
        }
        fprintf(stderr, "AB_PLAY_TEST level bodies after %d cutscene frames dyn=%d\n", i, a.dynamic_n);
        if (every) shot(6);
        /* TUTORIAL_OK lua hotspot is game (370,247) -> panel (55,333).
         * Drain every Level 1 popup; one tap is not always enough. */
        for (i = 0; i < 8; i++) {
            tap_at(49.f, 324.f);
            tap_at(55.f, 333.f);
        }
        wait_frames(30);
        if (every) shot(4);
        ab_phys_enable(1);
        wait_frames(30);
        ab_ready_slingshot();
        wait_frames(20);
        if (every) shot(7);
        ab_phys_snapshot(&a);
        ab_phys_log("play-before-shot");
        /* pigs must already sit on the ground; the level must not have auto-won */
        if (!ab_phys_resting(&a) || a.dynamic_n < 1) fail++;
        /* Pouch bird is the leftmost in-play dynamic (L1 ~ -12.4,-8.5).
         * Convert physics -> 480x320 -> Home-on-right panel so the tap tracks
         * the live play camera. Pull landscape-left / down in game space. */
        {
            float bx = -12.4f, by = -8.5f, scale, gx, gy, p0x, p0y, p1x, p1y;
            for (i = 0; i < a.sample_n; i++) {
                if (a.sample_type[i] == 2 && a.sample_x[i] > -20.f && a.sample_x[i] < 0.f) {
                    bx = a.sample_x[i]; by = a.sample_y[i];
                    break;
                }
            }
            scale = a.world_scale > 1e-4f ? a.world_scale : 0.32f;
            gx = (bx * 20.f - a.cam_left) * scale;
            gy = (by * 20.f - a.cam_top) * scale;
            p0x = fe_off_x + (fe_fb_h - gy) * fe_scale;
            p0y = fe_off_y + gx * fe_scale_y;
            gx -= 50.f;
            gy += 80.f;
            p1x = fe_off_x + (fe_fb_h - gy) * fe_scale;
            p1y = fe_off_y + gx * fe_scale_y;
            if (p1x < 0.f) p1x = 0.f;
            if (p1y < 0.f) p1y = 0.f;
            if (p1x > 239.f) p1x = 239.f;
            if (p1y > 431.f) p1y = 431.f;
            fprintf(stderr, "AB_PLAY_TEST pouch bird=%.1f,%.1f panel %.0f,%.0f -> %.0f,%.0f scale=%.3f\n",
                    (double)bx, (double)by, (double)p0x, (double)p0y,
                    (double)p1x, (double)p1y, (double)scale);
            ab_touch(0, 1, p0x, p0y);
            fake_us += 33333; ab_frame(1.f / 30.f);
            fake_us += 33333; ab_frame(1.f / 30.f);
            for (i = 0; i <= 12; i++) {
                float k = (float)i / 12.f;
                ab_touch(0, 1, p0x + (p1x - p0x) * k, p0y + (p1y - p0y) * k);
                fake_us += 33333; ab_frame(1.f / 30.f);
            }
            if (every) shot(8);
            ab_touch(0, 0, p1x, p1y);
        }
        fake_us += 33333; ab_frame(1.f / 30.f);
        wait_frames(25);
        ab_phys_snapshot(&mid);
        ab_phys_log("play-inflight");
        for (i = 0; i < mid.sample_n; i++)
            if (mid.sample_type[i] == 2 && mid.sample_x[i] > -8.f && mid.sample_x[i] < 30.f)
                flying = 1;
        if (every) shot(9);
        wait_frames(95);
        if (every) shot(5);
        ab_phys_snapshot(&b);
        ab_phys_log("play-after-shot");
        fprintf(stderr, "AB_PLAY_TEST heap used %u peak %u hole %u decode peak %u fails %u flying=%d before rest=%d dyn=%d after rest=%d dyn=%d contacts=%d dynY=%.2f..%.2f\n",
                rt_heap_used(), rt_heap_peak(), rt_heap_largest_free(),
                rt_outside_peak(), rt_malloc_failures(), flying,
                ab_phys_resting(&a), a.dynamic_n, ab_phys_resting(&b), b.dynamic_n, b.contacts,
                (double)b.dyn_ymin, (double)b.dyn_ymax);
        if (rt_malloc_failures()) fail++;
        if (!flying) fail++;
        if (b.dynamic_n < 1) fail++;                  /* must not auto-win */
        if (a.world_scale < 0.25f || a.world_scale > 1.05f) fail++;
        /* Pause is screen-space (game ~24,24 -> panel ~215,24). RECIPE 7's
         * extra lua drawGame composited the world over this overlay. */
        tap_at(215.f, 24.f);
        wait_frames(10);
        if (every) shot(10);
        {
            int blk = panel_black();
            ab_phys_info p;
            ab_phys_snapshot(&p);
            ab_phys_log("play-pause");
            fprintf(stderr, "AB_PLAY_TEST pause black=%d scale=%.3f\n",
                    blk, (double)p.world_scale);
            if (blk > 240 * 432 / 8) fail++;
            if (p.world_scale < 0.25f || p.world_scale > 1.05f) fail++;
        }
        fprintf(stderr, "AB_PLAY_TEST %s\n", fail ? "FAIL" : "PASS");
        return fail ? 1 : 0;
    }
    if (getenv("AB_GESTURE_TEST")) {
        /* Device test 5: a drag must not move the composited frame (HUD/pause
         * stay put). Two fingers pinch the game's worldScale. L1 then L2 then
         * L3 load in one session with a sane camera and no white frame. */
        int fail = 0, i;
        ab_phys_info a, b;
        unsigned pr, pg, pb, qr, qg, qb;
        float ox, oy, sx, sy, scale0, scale1;
        wait_frames(200);
        ab_call_lua("gotoFirstLevel");
        wait_frames(20);
        for (i = 0; i < 8; i++) {
            tap_at(49.f, 324.f);
            tap_at(55.f, 333.f);
        }
        wait_frames(20);
        ab_load_lua_level("Level1");
        wait_frames(90);
        if (!level_sane("Level1", &a)) fail++;
        dump_panel("panel-l1");
        ox = fe_off_x; oy = fe_off_y; sx = fe_scale; sy = fe_scale_y;
        pr = be_fb[24][215][0]; pg = be_fb[24][215][1]; pb = be_fb[24][215][2];
        ab_touch(0, 1, 120.f, 80.f);
        fake_us += 33333; ab_frame(1.f / 30.f);
        for (i = 1; i <= 20; i++) {
            ab_touch(0, 1, 120.f + (float)i, 80.f + 8.f * (float)i);
            fake_us += 33333; ab_frame(1.f / 30.f);
        }
        ab_touch(0, 0, 140.f, 240.f);
        fake_us += 33333; ab_frame(1.f / 30.f);
        qr = be_fb[24][215][0]; qg = be_fb[24][215][1]; qb = be_fb[24][215][2];
        fprintf(stderr, "AB_GESTURE_TEST frame fe_off=%.1f,%.1f fe_scale=%.3f,%.3f hud %u,%u,%u -> %u,%u,%u\n",
                (double)fe_off_x, (double)fe_off_y, (double)fe_scale, (double)fe_scale_y,
                pr, pg, pb, qr, qg, qb);
        if (fe_off_x != 0.f || fe_off_y != 0.f || fe_scale != 0.75f || fe_scale_y != 0.9f) fail++;
        if (fe_off_x != ox || fe_off_y != oy || fe_scale != sx || fe_scale_y != sy) fail++;
        if (pr != qr || pg != qg || pb != qb) fail++;
        ab_load_lua_level("Level2");
        wait_frames(90);
        if (!level_sane("Level2", &a)) fail++;
        scale0 = a.world_scale;
        pinch_out(16);
        wait_frames(10);
        ab_phys_snapshot(&b);
        scale1 = b.world_scale;
        fprintf(stderr, "AB_GESTURE_TEST pinch L2 scale %.3f -> %.3f vis %d/%d\n",
                (double)scale0, (double)scale1, b.vis_all, b.bodies);
        if (!(scale1 < scale0 - 0.015f) && !(scale1 > scale0 + 0.015f)) fail++;
        if (scale1 < 0.15f || scale1 > 1.05f) fail++;
        if (!level_sane("Level2-pinch", &b)) fail++;
        dump_panel("panel-l2");
        tap_at(215.f, 24.f);
        wait_frames(8);
        dump_panel("panel-pause");
        {
            int pix = play_pixels();
            fprintf(stderr, "AB_GESTURE_TEST pause pix=%d\n", pix);
            if (pix < 800) fail++;
        }
        ab_load_lua_level("Level3");
        wait_frames(90);
        if (!level_sane("Level3", &a)) fail++;
        fprintf(stderr, "AB_GESTURE_TEST %s\n", fail ? "FAIL" : "PASS");
        if (rt_malloc_failures()) {
            fprintf(stderr, "FATAL: %u guest allocations failed\n", rt_malloc_failures());
            return 3;
        }
        return fail ? 1 : 0;
    }
    if (getenv("AB_LEVELS_TEST")) {
        int fail = 0, n, loaded = 0, vis_fail = 0;
        int warm = atoi(getenv("AB_PHYS_WARM") ? getenv("AB_PHYS_WARM") : "200");
        int settle = atoi(getenv("AB_LEVELS_SETTLE") ? getenv("AB_LEVELS_SETTLE") : "90");
        int min_n = atoi(getenv("AB_LEVELS_MIN") ? getenv("AB_LEVELS_MIN") : "1");
        int max_n = atoi(getenv("AB_LEVELS_MAX") ? getenv("AB_LEVELS_MAX") : "96");
        wait_frames(warm);
        ab_call_lua("gotoFirstLevel");
        wait_frames(20);
        ab_touch(0, 1, 49.f, 324.f);
        fake_us += 33333; ab_frame(1.f / 30.f);
        fake_us += 33333; ab_frame(1.f / 30.f);
        ab_touch(0, 0, 49.f, 324.f);
        fake_us += 33333; ab_frame(1.f / 30.f);
        for (n = min_n; n <= max_n; n++) {
            char name[24], file[28];
            ab_phys_info a;
            int bad = 0;
            snprintf(name, sizeof name, "Level%d", n);
            snprintf(file, sizeof file, "Level%d.lua", n);
            if (plat_file_size(file) < 0) continue;
            ab_load_lua_level(name);
            wait_frames(settle);
            ab_phys_snapshot(&a);
            ab_phys_log(name);
            loaded++;
            if (a.dynamic_n < 1) bad = 1;
            if (a.vis_dyn < 1 || a.vis_all < 2) { bad = 1; vis_fail++; }
            /* RECIPE 7 AABB-fit used 0.20 and passed 0.19..0.90; that is the
             * device test 4 zoom (camfit scale=0.200 top=-1420). Native iPhone
             * tables are about 0.22..0.70 (tall stages like L28 sit ~0.25);
             * iPad leftovers are >1. */
            if (a.world_scale < 0.22f || a.world_scale > 1.05f) bad = 1;
            if (fe_stat_draws < 1) bad = 1;
            {
                int pix = play_pixels();
                int blk = panel_black();
                if (pix < 800) bad = 1;
                if (blk > 240 * 432 / 12) bad = 1;
                if (a.vis_dyn < 1) bad = 1;
                if (be_stat_tex_fail) bad = 1;
                if (rt_malloc_failures()) bad = 1;
                fprintf(stderr, "AB_LEVELS_TEST %s dyn=%d vis=%d/%d scale=%.3f draws=%d pix=%d black=%d %s\n",
                        name, a.dynamic_n, a.vis_dyn, a.vis_all, (double)a.world_scale,
                        fe_stat_draws, pix, blk, bad ? "FAIL" : "ok");
            }
            if (bad) fail++;
            if (every) shot(2000 + n);
        }
        fprintf(stderr, "AB_LEVELS_TEST loaded %d failed %d vis_fail %d heap %u peak %u texfail %d\n",
                loaded, fail, vis_fail, rt_heap_used(), rt_heap_peak(), be_stat_tex_fail);
        if (rt_malloc_failures()) {
            fprintf(stderr, "FATAL: %u guest allocations failed\n", rt_malloc_failures());
            return 3;
        }
        if (loaded < 1) {
            fprintf(stderr, "AB_LEVELS_TEST FAIL (no Level*.lua)\n");
            return 1;
        }
        fprintf(stderr, "AB_LEVELS_TEST %s\n", fail ? "FAIL" : "PASS");
        return fail ? 1 : 0;
    }
    for (int f = 0; f < frames; f++) {
        fake_us += 33333;
        /* script: "t0:x0,y0,x1,y1,len;..." a drag from (x0,y0) to (x1,y1) over len frames, panel px */
        const char *s = script;
        while (s && *s) {
            int t0, x0, y0, x1, y1, len;
            if (sscanf(s, "%d:%d,%d,%d,%d,%d", &t0, &x0, &y0, &x1, &y1, &len) == 6 && f >= t0 && f <= t0 + len) {
                float k = (float)(f - t0) / (float)len;
                ab_touch(0, f < t0 + len, (float)x0 + (float)(x1 - x0) * k, (float)y0 + (float)(y1 - y0) * k);
            }
            s = strchr(s, ';'); if (s) s++;
        }
        if (getenv("AB_DUMP_AT") && f == atoi(getenv("AB_DUMP_AT"))) setenv("AB_DUMP_V", "1", 1);
        if (getenv("AB_LOAD_LEVEL") && f == atoi(getenv("AB_LOAD_AT") ? getenv("AB_LOAD_AT") : "180"))
            ab_load_level(getenv("AB_LOAD_LEVEL"));
        if (getenv("AB_LUA") && f == atoi(getenv("AB_LUA_AT") ? getenv("AB_LUA_AT") : "200"))
            ab_call_lua(getenv("AB_LUA"));
        if (getenv("AB_PHYS") && (f == 1 || f % 30 == 0))
            ab_phys_log(f == 1 ? "f1" : "frame");
        if (getenv("AB_BURST") && f == 110) {   /* the nano case: a whole drag sampled inside one frame */
            int nb = atoi(getenv("AB_BURST")); ab_tsample q[8];
            for (int i = 0; i < nb && i < 8; i++) {
                q[i].x = 90.f; q[i].y = 300.f - 80.f * (float)i / (float)(nb - 1); q[i].down = 1; q[i].id = 0;
            }
            ab_frame_q(1.f / 30.f, q, nb < 8 ? nb : 8);
        } else if (getenv("AB_BURST") && f == 111) {
            ab_tsample up = {90.f, 220.f, 0};
            ab_frame_q(1.f / 30.f, &up, 1);
        } else
        ab_frame(1.f / 30.f);
        if (every && f % every == 0) {
            shot(f);
            fprintf(stderr, "frame %d: heap used %u peak %u, tris max %d, gl tex now %d peak %d\n", f, rt_heap_used(), rt_heap_peak(), be_frame_tris_max, fe_stat_tex_bytes, fe_stat_tex_peak);
        }
    }
    fprintf(stderr, "done: heap peak %u hole %u, decode peak %u / %u allocs, pvrtc %d, uncompressed %d, gl tex peak %d bytes in %d textures (%d atlased in %d pages), tiny %d, tris max %d (%d drawn on the nano)\n",
            rt_heap_peak(), rt_heap_largest_free(), rt_outside_peak(), rt_outside_count(),
            be_stat_pvrtc, be_stat_uncompressed, fe_stat_tex_peak, fe_stat_tex_count_peak, fe_stat_atlased, fe_stat_atlas_pages, be_check_tiny, be_frame_tris_max, be_frame_big_max);
    if (rt_malloc_failures()) {
        fprintf(stderr, "FATAL: %u guest allocations failed\n", rt_malloc_failures());
        return 3;
    }
    return 0;
}
