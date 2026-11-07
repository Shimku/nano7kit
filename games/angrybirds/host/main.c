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
static const char *data_dir, *img_path, *out_dir;
static uint64_t fake_us;
void plat_log_line(const char *l) { fprintf(stderr, "%s\n", l); }
void plat_trace(void) { void *b[24]; int n = backtrace(b, 24); char **s = backtrace_symbols(b, n);
    fprintf(stderr, "  at"); for (int i = 1; i < n; i++) { const char *p = strchr(s[i], '('); if (p && p[1] == 'F') fprintf(stderr, " %.*s", (int)(strcspn(p + 1, "+)")), p + 1); } fprintf(stderr, "\n"); }
void *plat_os_alloc(uint32_t n) { return calloc(1, n ? n : 1); }
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
    uint32_t size = 24u << 20;
    void *block = malloc(size);
    if (rt_init(block, size)) return 1;
    if (ab_start()) return 1;
    fprintf(stderr, "init ok: heap used %u peak %u\n", rt_heap_used(), rt_heap_peak());
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
                /* Same path as the in-game next-level button. C++ loadLevel("Level2")
                 * from the harness did not spawn bodies; lua loadNextLevel does. */
                ab_call_lua("loadNextLevel");
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
                q[i].x = 90.f; q[i].y = 300.f - 80.f * (float)i / (float)(nb - 1); q[i].down = 1;
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
    fprintf(stderr, "done: heap peak %u, pvrtc %d, uncompressed %d, gl tex peak %d bytes in %d textures (%d atlased in %d pages), tiny %d, tris max %d (%d drawn on the nano)\n",
            rt_heap_peak(), be_stat_pvrtc, be_stat_uncompressed, fe_stat_tex_peak, fe_stat_tex_count_peak, fe_stat_atlased, fe_stat_atlas_pages, be_check_tiny, be_frame_tris_max, be_frame_big_max);
    return 0;
}
