/* host harness for Android libangrybirds.so under qemu-arm (nano7kit, MIT). */
#include <execinfo.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <ucontext.h>
#include <unistd.h>
#include "../rt/ab_entry.h"
#include "../rt/audio_stub.h"
#include "../rt/fngl.h"
#include "../rt/rt_plat.h"
#include "../rt/shims.h"

extern uint8_t be_fb[432][240][4];
extern uint8_t be_last_tex[432][240];
extern int be_stat_pvrtc, be_stat_uncompressed, be_tex_peak_bytes, be_check_tiny, be_frame_tris_max, be_frame_big_max;
extern int be_stat_tex_fail;
static const char *data_dir, *out_dir;
static char files_dir[512];
static uint64_t fake_us;

void plat_log_line(const char *l) { fprintf(stderr, "%s\n", l); }
void plat_log_flush(void) { fflush(stderr); }
void plat_fatal(const char *why) { fprintf(stderr, "FATAL: %s\n", why); exit(3); }
void *plat_os_alloc(uint32_t n) { return calloc(1, n ? n : 1); }
void plat_os_free(void *p) { free(p); }
uint32_t plat_os_largest(void) { return 8u << 20; }
uint64_t plat_time_us(void) { return fake_us; }
const char *plat_files_dir(void) { return files_dir; }
const char *plat_data_dir(void) { return data_dir; }

static int open_named(const char *name, FILE **out) {
    char p[512];
    const char *base = name ? name : "";
    const char *slash, *d;
    snprintf(p, sizeof p, "%s/%s", data_dir, base);
    if ((*out = fopen(p, "rb"))) return 0;
    snprintf(p, sizeof p, "%s/docs/%s", out_dir, base);
    if ((*out = fopen(p, "rb"))) return 0;
    if (!strncmp(base, "data/", 5)) {
        snprintf(p, sizeof p, "%s/%s", data_dir, base + 5);
        if ((*out = fopen(p, "rb"))) return 0;
    }
    d = base;
    while (strstr(d, "data/")) d = strstr(d, "data/") + 5;
    if (d != base) {
        snprintf(p, sizeof p, "%s/%s", data_dir, d);
        if ((*out = fopen(p, "rb"))) return 0;
    }
    slash = strrchr(base, '/');
    if (slash) {
        snprintf(p, sizeof p, "%s/%s", data_dir, slash + 1);
        if ((*out = fopen(p, "rb"))) return 0;
    }
    if (!strchr(base, '.')) {
        snprintf(p, sizeof p, "%s.lua", base);
        return open_named(p, out);
    }
    return -1;
}

int plat_read_file(const char *name, void **out, uint32_t *len) {
    FILE *f; long n; void *buf;
    int stub;
    *out = 0; *len = 0;
    fe_note_texfile(name);
    if (open_named(name, &f)) {
        stub = ab_stub_audio(name, out, len);
        return stub == 1 ? 0 : -1;
    }
    fseek(f, 0, SEEK_END); n = ftell(f); fseek(f, 0, SEEK_SET);
    if (n < 0) { fclose(f); return -1; }
    buf = malloc((size_t)n + 1);
    if (!buf) { fclose(f); return -1; }
    if (n && fread(buf, 1, (size_t)n, f) != (size_t)n) { free(buf); fclose(f); return -1; }
    ((char *)buf)[n] = 0;
    fclose(f);
    *out = buf; *len = (uint32_t)n;
    return 0;
}

static int play_pixels(void) {
    int n = 0, y, x;
    for (y = 0; y < 432; y++) for (x = 0; x < 240; x++) {
        unsigned r = be_fb[y][x][0], g = be_fb[y][x][1], b = be_fb[y][x][2];
        if (r >= 230 && g >= 230 && b >= 230) continue;
        if (b > 200 && g > 180 && r < 180) continue;
        n++;
    }
    return n;
}
static int panel_black(void) {
    int n = 0, y, x;
    for (y = 0; y < 432; y++) for (x = 0; x < 240; x++) {
        unsigned r = be_fb[y][x][0], g = be_fb[y][x][1], b = be_fb[y][x][2];
        if (r < 12 && g < 12 && b < 12) n++;
    }
    return n;
}
static void dump_panel(const char *name) {
    char p[512]; FILE *f; int y, x;
    snprintf(p, sizeof p, "%s/%s.ppm", out_dir, name);
    f = fopen(p, "wb"); if (!f) return;
    fprintf(f, "P6 240 432 255\n");
    for (y = 0; y < 432; y++) for (x = 0; x < 240; x++) fwrite(be_fb[y][x], 1, 3, f);
    fclose(f);
    fprintf(stderr, "AB_DUMP %s px60_220=%u,%u,%u last_tex=%u\n",
            name, be_fb[220][60][0], be_fb[220][60][1], be_fb[220][60][2],
            (unsigned)be_last_tex[220][60]);
}
static void shot_panel(int frame) {
    char p[32]; snprintf(p, sizeof p, "panel-%05d", frame); dump_panel(p);
}
static void shot(int frame) {
    char p[512]; FILE *f; int gy, gx;
    snprintf(p, sizeof p, "%s/frame-%05d.ppm", out_dir, frame);
    f = fopen(p, "wb"); if (!f) return;
    fprintf(f, "P6 432 240 255\n");
    for (gy = 0; gy < 240; gy++)
        for (gx = 0; gx < 432; gx++) {
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
static int sample_rect_var(int cx, int cy, int hw, float *mean_out, float *var_out, float *range_out, int *n_out) {
    int x, y, n = 0, mn = 255, mx = 0;
    double sum = 0, sum2 = 0;
    for (y = cy - hw; y <= cy + hw; y++) for (x = cx - hw; x <= cx + hw; x++) {
        int r, g, b, yv;
        if (x < 0 || x >= 240 || y < 0 || y >= 432) continue;
        r = be_fb[y][x][0]; g = be_fb[y][x][1]; b = be_fb[y][x][2];
        yv = (r * 3 + g * 6 + b) / 10;
        sum += yv; sum2 += (double)yv * yv; n++;
        if (yv < mn) mn = yv;
        if (yv > mx) mx = yv;
    }
    if (n < 12) return 0;
    *mean_out = (float)(sum / n);
    *var_out = (float)(sum2 / n - (*mean_out) * (*mean_out));
    *range_out = (float)(mx - mn);
    *n_out = n;
    return 1;
}
static int bodies_textured(const char *tag, const ab_phys_info *a, int *ok_n, int *flat_n) {
    int i, ok = 0, flat = 0, seen = 0;
    for (i = 0; i < a->vis_n; i++) {
        float mean, var, range, bmean, bvar, brange;
        int n, bn, cx, cy, r = 0, g = 0, b = 0;
        cx = (int)(a->vis_px[i] + 0.5f);
        cy = (int)(a->vis_py[i] + 0.5f);
        /* Pause, bird tray and the far HUD sit on UI sheets, not the castle. */
        if (cy < 36 || cy > 400 || cx < 4 || cx > 235) continue;
        if (!sample_rect_var(cx, cy, 8, &mean, &var, &range, &n)) continue;
        seen++;
        if (cx >= 0 && cx < 240 && cy >= 0 && cy < 432) {
            r = be_fb[cy][cx][0]; g = be_fb[cy][cx][1]; b = be_fb[cy][cx][2];
        }
        if (seen <= 8)
            fprintf(stderr, "AB_BODY %s t=%d world=%.2f,%.2f panel=%d,%d rgb=%d,%d,%d mean=%.0f var=%.0f range=%.0f\n",
                    tag, a->vis_type[i], (double)a->vis_x[i], (double)a->vis_y[i],
                    cx, cy, r, g, b, (double)mean, (double)var, (double)range);
        /* Background ring: same size, shifted toward panel centre so we
         * compare the body fill against nearby sky/hills, not against itself. */
        if (!sample_rect_var(cx + (cx < 120 ? 16 : -16), cy + (cy < 216 ? 16 : -16),
                             8, &bmean, &bvar, &brange, &bn)) {
            bmean = 230.f; bvar = 8.f; brange = 10.f;
        }
        /* Untextured Ka3D fills are near-white with almost no variance. */
        if ((r > 240 && g > 240 && b > 240 && range < 24.f) ||
            (mean > 242.f && var < 18.f && range < 24.f))
            flat++;
        else if (var > 48.f || range > 40.f || (mean + 22.f < bmean && var > 18.f))
            ok++;
    }
    if (ok_n) *ok_n = ok;
    if (flat_n) *flat_n = flat;
    fprintf(stderr, "AB_TEXVAR %s vis_bodies=%d sampled=%d tex_ok=%d tex_flat=%d\n",
            tag, a->vis_n, seen, ok, flat);
    return ok >= 4 && flat * 3 <= ok;
}
static int level_sane(const char *name, ab_phys_info *a) {
    int pix, blk, bad = 0, tok = 0, tflat = 0;
    ab_phys_snapshot(a);
    pix = play_pixels();
    blk = panel_black();
    if (a->dynamic_n < 1) bad = 1;
    if (a->vis_dyn < 1 || a->vis_all < 2) bad = 1;
    if (a->world_scale < 0.22f || a->world_scale > 1.05f) bad = 1;
    if (pix < 800) bad = 1;
    if (blk > 240 * 432 / 12) bad = 1;
    if (be_stat_tex_fail) bad = 1;
    if (!bodies_textured(name, a, &tok, &tflat)) bad = 1;
    fprintf(stderr, "AB_GESTURE_TEST %s dyn=%d vis=%d/%d scale=%.3f pix=%d black=%d tex=%d fail=%d tex_ok=%d tex_flat=%d %s\n",
            name, a->dynamic_n, a->vis_dyn, a->vis_all, (double)a->world_scale,
            pix, blk, fe_stat_tex_count, be_stat_tex_fail, tok, tflat, bad ? "FAIL" : "ok");
    return !bad;
}
static float clampf(float v, float lo, float hi) {
    return v < lo ? lo : v > hi ? hi : v;
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
static void pinch_in(int frames) {
    int i;
    ab_touch(0, 1, 30.f, 216.f);
    ab_touch(1, 1, 210.f, 216.f);
    fake_us += 33333; ab_frame(1.f / 30.f);
    for (i = 1; i <= frames; i++) {
        float k = (float)i / (float)frames;
        ab_touch(0, 1, 30.f + 50.f * k, 216.f);
        ab_touch(1, 1, 210.f - 50.f * k, 216.f);
        fake_us += 33333; ab_frame(1.f / 30.f);
    }
    ab_touch(0, 0, 80.f, 216.f);
    ab_touch(1, 0, 160.f, 216.f);
    fake_us += 33333; ab_frame(1.f / 30.f);
}

static void on_crash(int sig, siginfo_t *si, void *ctx) {
    ucontext_t *uc = (ucontext_t *)ctx;
    unsigned long pc = (unsigned long)uc->uc_mcontext.arm_pc;
    unsigned long lr = (unsigned long)uc->uc_mcontext.arm_lr;
    unsigned long sp = (unsigned long)uc->uc_mcontext.arm_sp;
    unsigned long *s;
    int i;
    fprintf(stderr, "CRASH sig=%d addr=%p pc=%08lx lr=%08lx sp=%08lx r0=%08lx r1=%08lx r2=%08lx r3=%08lx r4=%08lx r5=%08lx r6=%08lx r7=%08lx\n",
            sig, si ? si->si_addr : 0, pc, lr, sp,
            (unsigned long)uc->uc_mcontext.arm_r0, (unsigned long)uc->uc_mcontext.arm_r1,
            (unsigned long)uc->uc_mcontext.arm_r2, (unsigned long)uc->uc_mcontext.arm_r3,
            (unsigned long)uc->uc_mcontext.arm_r4, (unsigned long)uc->uc_mcontext.arm_r5,
            (unsigned long)uc->uc_mcontext.arm_r6, (unsigned long)uc->uc_mcontext.arm_r7);
    s = (unsigned long *)sp;
    for (i = 0; i < 24; i++) fprintf(stderr, "  sp[%d]=%08lx\n", i, s[i]);
    _exit(139);
}

int main(int argc, char **argv) {
    int frames, every;
    const char *script;
    {
        struct sigaction sa;
        memset(&sa, 0, sizeof sa);
        sa.sa_sigaction = on_crash;
        sa.sa_flags = SA_SIGINFO;
        sigaction(SIGSEGV, &sa, 0);
        sigaction(SIGILL, &sa, 0);
        sigaction(SIGBUS, &sa, 0);
        sigaction(SIGABRT, &sa, 0);
    }
    if (argc < 5) {
        fprintf(stderr, "usage: %s DATA_DIR libangrybirds.so OUT_DIR FRAMES [SHOT_EVERY] [script]\n", argv[0]);
        return 2;
    }
    data_dir = argv[1];
    ab_set_so_path(argv[2]);
    out_dir = argv[3];
    frames = atoi(argv[4]); every = argc > 5 ? atoi(argv[5]) : 30;
    script = argc > 6 ? argv[6] : "";
    snprintf(files_dir, sizeof files_dir, "%s/docs", out_dir);
    mkdir(out_dir, 0755);
    mkdir(files_dir, 0755);
    fake_us = 1000000ull;
    fngl_reset();
    if (ab_start()) return 1;
    fprintf(stderr, "init ok: heap %u peak %u\n", ab_heap_used(), ab_heap_peak());

    if (getenv("AB_PLAY_TEST")) {
        int fail = 0, i, flying = 0;
        ab_phys_info a, b, mid;
        wait_frames(200);
        if (every) shot(1);
        ab_call_lua("gotoFirstLevel");
        wait_frames(20);
        for (i = 0; i < 8; i++) { tap_at(49.f, 324.f); tap_at(55.f, 333.f); }
        wait_frames(10);
        ab_load_lua_level("Level1");
        wait_frames(50);
        if (every) { shot(6); dump_panel("panel-l1"); }
        ab_phys_enable(1);
        wait_frames(40);
        if (every) shot(7);
        ab_ready_slingshot();
        wait_frames(18);
        ab_phys_snapshot(&a);
        ab_phys_log("play-before-shot");
        if (!ab_phys_resting(&a) || a.dynamic_n < 1) fail++;
        {
            float bx, by, p0x, p0y, p1x, p1y, gdir, ddx, ddy;
            for (i = 0; i < a.vis_n && i < 12; i++) if (a.vis_type[i] == 2)
                fprintf(stderr, "AB_PLAY_TEST vis t=2 world=%.2f,%.2f panel=%.1f,%.1f\n",
                        (double)a.vis_x[i], (double)a.vis_y[i],
                        (double)a.vis_px[i], (double)a.vis_py[i]);
            if (!ab_pouch_world(&bx, &by) || !ab_world_to_panel(bx, by, &p0x, &p0y)) {
                fprintf(stderr, "AB_PLAY_TEST no pouch body\n");
                fail++;
                bx = by = p0x = p0y = p1x = p1y = 0.f;
            } else {
                if (p0y < 28.f || p0y > 400.f || p0x < 8.f || p0x > 232.f) {
                    ab_cam_fit_birds();
                    wait_frames(4);
                    ab_phys_snapshot(&a);
                    ab_pouch_world(&bx, &by);
                    ab_world_to_panel(bx, by, &p0x, &p0y);
                }
                gdir = a.grav_y < 0.f ? -1.f : 1.f;
                /* Pull back about 8 physics units (away from the castle). */
                ab_world_to_panel(bx - 8.5f, by + gdir * 2.8f, &p1x, &p1y);
                fprintf(stderr, "AB_PLAY_TEST pouch raw world %.2f,%.2f panel %.1f,%.1f pull %.1f,%.1f\n",
                        (double)bx, (double)by, (double)p0x, (double)p0y, (double)p1x, (double)p1y);
                if (p0y < 22.f || p0y > 410.f || p0x < 4.f || p0x > 236.f) {
                    fprintf(stderr, "drag missed pouch\n");
                    fail++;
                }
                p0x = clampf(p0x, 12.f, 228.f); p0y = clampf(p0y, 24.f, 408.f);
                p1x = clampf(p1x, 8.f, 232.f); p1y = clampf(p1y, 8.f, 424.f);
                ddx = p1x - p0x; ddy = p1y - p0y;
                if (ddx * ddx + ddy * ddy < 35.f * 35.f) {
                    /* Landscape left is toward the top of the panel. */
                    p1y = clampf(p0y - 56.f, 8.f, 424.f);
                    p1x = clampf(p0x - 6.f, 8.f, 232.f);
                }
                fprintf(stderr, "AB_PLAY_TEST pouch world %.2f,%.2f panel %.1f,%.1f -> %.1f,%.1f scale=%.3f grav=%.2f draws=%d\n",
                        (double)bx, (double)by, (double)p0x, (double)p0y, (double)p1x, (double)p1y,
                        (double)a.world_scale, (double)a.grav_y, fe_stat_draws);
                ab_touch(0, 1, p0x, p0y);
                fake_us += 33333; ab_frame(1.f / 30.f);
                fake_us += 33333; ab_frame(1.f / 30.f);
                fake_us += 33333; ab_frame(1.f / 30.f);
                for (i = 0; i <= 14; i++) {
                    float k = (float)i / 14.f;
                    ab_touch(0, 1, p0x + (p1x - p0x) * k, p0y + (p1y - p0y) * k);
                    fake_us += 33333; ab_frame(1.f / 30.f);
                }
                if (every) shot(8);
                dump_panel("panel-aim");
                fprintf(stderr, "AB_PLAY_TEST aim draws=%d last_tex=%u\n",
                        fe_stat_draws, (unsigned)be_last_tex[220][60]);
                ab_touch(0, 0, p1x, p1y);
            }
        }
        fake_us += 33333; ab_frame(1.f / 30.f);
        wait_frames(25);
        ab_phys_snapshot(&mid);
        ab_phys_log("play-inflight");
        dump_panel("panel-play");
        {
            float maxd = 0.f;
            for (i = 0; i < mid.vis_n; i++) if (mid.vis_type[i] == 2) {
                int j;
                float bestd = 1e9f;
                for (j = 0; j < a.vis_n; j++) if (a.vis_type[j] == 2) {
                    float dx = mid.vis_x[i] - a.vis_x[j], dy = mid.vis_y[i] - a.vis_y[j];
                    float d2 = dx * dx + dy * dy;
                    if (d2 < bestd) bestd = d2;
                }
                if (bestd > maxd) maxd = bestd;
            }
            flying = maxd > 4.f; /* >2 physics units of travel */
            fprintf(stderr, "AB_PLAY_TEST travel2=%.3f draws=%d\n", (double)maxd, fe_stat_draws);
        }
        if (every) shot(9);
        /* Pause while the bird is still in the air, before the level-cleared
         * overlay replaces the pause button. */
        tap_at(215.f, 24.f);
        wait_frames(10);
        if (every) { shot(10); dump_panel("panel-pause"); }
        {
            int pix = play_pixels();
            int blk = panel_black();
            ab_phys_info p;
            ab_phys_snapshot(&p);
            fprintf(stderr, "AB_PLAY_TEST pause pix=%d black=%d scale=%.3f\n",
                    pix, blk, (double)p.world_scale);
            if (pix < 800) fail++;
            if (p.world_scale < 0.25f || p.world_scale > 1.05f) fail++;
        }
        wait_frames(95);
        if (every) shot(5);
        ab_phys_snapshot(&b);
        ab_phys_log("play-after-shot");
        fprintf(stderr, "AB_PLAY_TEST heap used %u peak %u flying=%d before rest=%d dyn=%d after rest=%d dyn=%d\n",
                ab_heap_used(), ab_heap_peak(), flying,
                ab_phys_resting(&a), a.dynamic_n, ab_phys_resting(&b), b.dynamic_n);
        if (ab_malloc_failures()) fail++;
        if (!flying) fail++;
        if (b.dynamic_n < 1) fail++;
        if (a.world_scale < 0.25f || a.world_scale > 1.05f) fail++;
        fprintf(stderr, "AB_PLAY_TEST %s\n", fail ? "FAIL" : "PASS");
        return fail ? 1 : 0;
    }

    if (getenv("AB_GESTURE_TEST")) {
        int fail = 0, i;
        ab_phys_info a, b;
        unsigned pr, pg, pb, qr, qg, qb;
        float ox, oy, sx, sy, scale0, scale1, scale2;
        wait_frames(200);
        ab_call_lua("gotoFirstLevel");
        wait_frames(20);
        for (i = 0; i < 8; i++) { tap_at(49.f, 324.f); tap_at(55.f, 333.f); }
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
        dump_panel("panel-l2");
        scale0 = a.world_scale;
        pinch_out(16);
        wait_frames(10);
        ab_phys_snapshot(&b);
        scale1 = b.world_scale;
        dump_panel("panel-pinch-out");
        fprintf(stderr, "AB_GESTURE_TEST pinch-out L2 scale %.3f -> %.3f vis %d/%d\n",
                (double)scale0, (double)scale1, b.vis_all, b.bodies);
        if (!(scale1 < scale0 - 0.015f) && !(scale1 > scale0 + 0.015f)) fail++;
        if (scale1 < 0.15f || scale1 > 1.05f) fail++;
        if (!level_sane("Level2-pinch-out", &b)) fail++;
        pinch_in(16);
        wait_frames(10);
        ab_phys_snapshot(&b);
        scale2 = b.world_scale;
        dump_panel("panel-pinch-in");
        fprintf(stderr, "AB_GESTURE_TEST pinch-in L2 scale %.3f -> %.3f\n", (double)scale1, (double)scale2);
        if (!(scale2 > scale1 + 0.010f) && !(scale2 < scale1 - 0.010f) && scale2 == scale1) fail++;
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
        dump_panel("panel-l3");
        fprintf(stderr, "AB_GESTURE_TEST %s\n", fail ? "FAIL" : "PASS");
        if (ab_malloc_failures()) return 3;
        return fail ? 1 : 0;
    }

    if (getenv("AB_LEVELS_TEST")) {
        int fail = 0, n, loaded = 0;
        int warm = atoi(getenv("AB_PHYS_WARM") ? getenv("AB_PHYS_WARM") : "200");
        int settle = atoi(getenv("AB_LEVELS_SETTLE") ? getenv("AB_LEVELS_SETTLE") : "90");
        int min_n = atoi(getenv("AB_LEVELS_MIN") ? getenv("AB_LEVELS_MIN") : "1");
        int max_n = atoi(getenv("AB_LEVELS_MAX") ? getenv("AB_LEVELS_MAX") : "3");
        wait_frames(warm);
        ab_call_lua("gotoFirstLevel");
        wait_frames(20);
        tap_at(49.f, 324.f);
        for (n = min_n; n <= max_n; n++) {
            char name[24], file[28];
            ab_phys_info a;
            int bad = 0, pix, blk;
            FILE *lf;
            snprintf(name, sizeof name, "Level%d", n);
            snprintf(file, sizeof file, "Level%d.lua", n);
            if (open_named(file, &lf)) continue;
            fclose(lf);
            ab_load_lua_level(name);
            wait_frames(settle);
            ab_phys_snapshot(&a);
            ab_phys_log(name);
            loaded++;
            pix = play_pixels(); blk = panel_black();
            if (a.dynamic_n < 1) bad = 1;
            if (a.vis_dyn < 1 || a.vis_all < 8) bad = 1;
            if (a.world_scale < 0.22f || a.world_scale > 1.05f) bad = 1;
            if (pix < 800) bad = 1;
            if (blk > 240 * 432 / 12) bad = 1;
            if (be_stat_tex_fail) bad = 1;
            {
                int tok = 0, tflat = 0;
                if (!bodies_textured(name, &a, &tok, &tflat)) bad = 1;
                fprintf(stderr, "AB_LEVELS_TEST %s dyn=%d vis=%d/%d scale=%.3f pix=%d black=%d tex_ok=%d tex_flat=%d %s\n",
                        name, a.dynamic_n, a.vis_all, a.bodies, (double)a.world_scale, pix, blk,
                        tok, tflat, bad ? "FAIL" : "ok");
            }
            if (bad) fail++;
            {
                char pan[24];
                snprintf(pan, sizeof pan, "panel-l%d", n);
                dump_panel(pan);
            }
            if (every) shot(2000 + n);
        }
        fprintf(stderr, "AB_LEVELS_TEST loaded %d failed %d heap %u peak %u\n",
                loaded, fail, ab_heap_used(), ab_heap_peak());
        if (loaded < 1) return 1;
        fprintf(stderr, "AB_LEVELS_TEST %s\n", fail ? "FAIL" : "PASS");
        return fail ? 1 : 0;
    }

    {
        int f;
        for (f = 0; f < frames; f++) {
            fake_us += 33333;
            {
                const char *s = script;
                while (s && *s) {
                    int t0, x0, y0, x1, y1, len;
                    if (sscanf(s, "%d:%d,%d,%d,%d,%d", &t0, &x0, &y0, &x1, &y1, &len) == 6 && f >= t0 && f <= t0 + len) {
                        float k = (float)(f - t0) / (float)len;
                        ab_touch(0, f < t0 + len, (float)x0 + (float)(x1 - x0) * k, (float)y0 + (float)(y1 - y0) * k);
                    }
                    s = strchr(s, ';'); if (s) s++;
                }
            }
            ab_frame(1.f / 30.f);
            if (every && f % every == 0) {
                shot(f);
                fprintf(stderr, "frame %d: heap used %u peak %u, tris max %d, gl tex now %d peak %d\n",
                        f, ab_heap_used(), ab_heap_peak(), be_frame_tris_max, fe_stat_tex_bytes, fe_stat_tex_peak);
            }
        }
    }
    fprintf(stderr, "done: heap peak %u fail %u, pvrtc %d, uncompressed %d, gl tex peak %d\n",
            ab_heap_peak(), ab_malloc_failures(), be_stat_pvrtc, be_stat_uncompressed, fe_stat_tex_peak);
    return ab_malloc_failures() ? 3 : 0;
}
