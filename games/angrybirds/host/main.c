/* host harness for Android libangrybirds.so under qemu-arm (nano7kit, MIT). */
#include <execinfo.h>
#include <signal.h>
#include <stdint.h>
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
#include "../rt/so_load.h"

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
    const char *slash, *d, *leaf;
    if (base[0] == '/' && (*out = fopen(base, "rb"))) return 0;
    if (!strchr(base, '/')) {
        snprintf(p, sizeof p, "%s/%s", files_dir, base);
        if ((*out = fopen(p, "rb"))) return 0;
    }
    snprintf(p, sizeof p, "%s/%s", data_dir, base);
    if ((*out = fopen(p, "rb"))) return 0;
    snprintf(p, sizeof p, "%s/%s", files_dir, base);
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
    leaf = slash ? slash + 1 : base;
    if (leaf[0]) {
        snprintf(p, sizeof p, "%s/%s", files_dir, leaf);
        if ((*out = fopen(p, "rb"))) return 0;
        snprintf(p, sizeof p, "%s/%s", data_dir, leaf);
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
/* nativeUpdate computes dt from gettimeofday (plat_time_us). GameLua::update
 * accumulates that and steps Box2D at a fixed 1/30. Advance 33333 us so each
 * harness frame is one physics step, never a 0.1s clamp catch-up. */
#define AB_FRAME_US 33333ull
#define AB_FRAME_DT (1.f / 30.f)
static void wait_frames(int n) {
    int f;
    for (f = 0; f < n; f++) { fake_us += AB_FRAME_US; ab_frame(AB_FRAME_DT); }
}

static uint32_t lua_u32(const uint8_t *b, uint32_t *i, uint32_t n, int *ok) {
    uint32_t v = 0;
    if (*i + 4 > n) { *ok = 0; return 0; }
    memcpy(&v, b + *i, 4);
    *i += 4;
    return v;
}
static int lua_skip_str(const uint8_t *b, uint32_t *i, uint32_t n) {
    int ok = 1;
    uint32_t ln = lua_u32(b, i, n, &ok);
    if (!ok || *i + ln > n) return 0;
    *i += ln;
    return 1;
}
/* Lua 5.1 SETTABLE of numeric world.x / world.y (physics metres). */
static int load_level_xy(const char *file, float *xs, float *ys, int maxn) {
    void *buf = 0; uint32_t n = 0, i = 12, ncode, nconst, pc, k;
    uint8_t *b; int ok = 1, nxy = 0;
    uint32_t *code; float *nums; char **strs; uint8_t *typ;
    if (plat_read_file(file, &buf, &n) || !buf || n < 24) { free(buf); return 0; }
    b = (uint8_t *)buf;
    if (memcmp(b, "\x1bLua", 4) != 0) { free(buf); return 0; }
    if (!lua_skip_str(b, &i, n)) { free(buf); return 0; }
    lua_u32(b, &i, n, &ok); lua_u32(b, &i, n, &ok);
    if (i + 4 > n) { free(buf); return 0; }
    i += 4;
    ncode = lua_u32(b, &i, n, &ok);
    if (!ok || ncode > 8000 || i + ncode * 4 > n) { free(buf); return 0; }
    code = (uint32_t *)malloc(ncode * 4);
    if (!code) { free(buf); return 0; }
    memcpy(code, b + i, ncode * 4); i += ncode * 4;
    nconst = lua_u32(b, &i, n, &ok);
    if (!ok || nconst > 4000) { free(code); free(buf); return 0; }
    nums = (float *)calloc(nconst, 4);
    strs = (char **)calloc(nconst, sizeof(char *));
    typ = (uint8_t *)calloc(nconst, 1);
    if (!nums || !strs || !typ) { free(code); free(nums); free(strs); free(typ); free(buf); return 0; }
    for (k = 0; k < nconst; k++) {
        if (i >= n) { ok = 0; break; }
        typ[k] = b[i++];
        if (typ[k] == 0) continue;
        if (typ[k] == 1) { if (i >= n) { ok = 0; break; } i++; continue; }
        if (typ[k] == 3) {
            if (i + 4 > n) { ok = 0; break; }
            memcpy(&nums[k], b + i, 4); i += 4; continue;
        }
        if (typ[k] == 4) {
            uint32_t ln = lua_u32(b, &i, n, &ok);
            if (!ok || i + ln > n) { ok = 0; break; }
            strs[k] = (char *)(b + i);
            i += ln;
            continue;
        }
        ok = 0; break;
    }
    if (ok) {
        int kx = -1, ky = -1;
        float pending_x = 0.f; int have_x = 0; int pend_a = -1;
        for (k = 0; k < nconst; k++) {
            if (typ[k] != 4 || !strs[k]) continue;
            if (strs[k][0] == 'x' && strs[k][1] == 0) kx = (int)k;
            if (strs[k][0] == 'y' && strs[k][1] == 0) ky = (int)k;
        }
        for (pc = 0; pc < ncode && nxy < maxn; pc++) {
            uint32_t w = code[pc], op = w & 63u, a = (w >> 6) & 255u;
            uint32_t bb = (w >> 23) & 511u, cc = (w >> 14) & 511u;
            int key, valk;
            if (op != 9) continue;
            if (!(bb & 256u) || !(cc & 256u)) continue;
            key = (int)(bb & 255u); valk = (int)(cc & 255u);
            if (valk >= (int)nconst || typ[valk] != 3) continue;
            if (key == kx) {
                pending_x = nums[valk]; have_x = 1; pend_a = (int)a;
            } else if (key == ky && have_x && pend_a == (int)a) {
                float x = pending_x, y = nums[valk];
                have_x = 0;
                if (x > -80.f && x < 160.f && y > -60.f && y < 40.f) {
                    xs[nxy] = x; ys[nxy] = y; nxy++;
                }
            }
        }
    }
    free(code); free(nums); free(strs); free(typ); free(buf);
    return nxy;
}
static int match_level_file(const char *lvl, const ab_phys_info *a) {
    float xs[128], ys[128];
    char file[40];
    int n, i, hit = 0, miss = 0;
    snprintf(file, sizeof file, "%s.lua", lvl);
    n = load_level_xy(file, xs, ys, 128);
    for (i = 0; i < n; i++) {
        int j, ok = 0;
        float best = 1e9f;
        for (j = 0; j < a->sample_n; j++) {
            float dx = a->sample_x[j] - xs[i], dy = a->sample_y[j] - ys[i];
            float d2 = dx * dx + dy * dy;
            if (d2 < best) best = d2;
            if (d2 < 2.25f) { ok = 1; break; }
        }
        if (ok) hit++;
        else {
            miss++;
            if (miss <= 6)
                fprintf(stderr, "AB_LUA_POS %s miss file=%.2f,%.2f nearest2=%.3f\n",
                        lvl, (double)xs[i], (double)ys[i], (double)best);
        }
    }
    fprintf(stderr, "AB_LUA_POS %s file_xy=%d matched=%d miss=%d bodies=%d\n",
            lvl, n, hit, miss, a->sample_n);
    return n >= 4 && miss * 4 <= n;
}
static int hold_ok(const char *lvl, const ab_phys_info *open, const ab_phys_info *now) {
    float oh, nh, drop, maxdy = 0.f, cut;
    int i, lost, castle_open = 0, castle_now = 0;
    oh = open->dyn_ymax - open->dyn_ymin;
    nh = now->dyn_ymax - now->dyn_ymin;
    if (oh < 0.5f) oh = 0.5f;
    drop = (oh - nh) / oh;
    cut = open->dyn_ymin + 0.35f * oh;
    for (i = 0; i < open->sample_n; i++)
        if (open->sample_type[i] == 2 && open->sample_y[i] < cut) castle_open++;
    for (i = 0; i < now->sample_n; i++)
        if (now->sample_type[i] == 2 && now->sample_y[i] < cut) castle_now++;
    for (i = 0; i < now->sample_n; i++) if (now->sample_type[i] == 2) {
        int j; float best = 1e9f;
        for (j = 0; j < open->sample_n; j++) if (open->sample_type[j] == 2) {
            float dx = now->sample_x[i] - open->sample_x[j];
            float dy = now->sample_y[i] - open->sample_y[j];
            float d2 = dx * dx + dy * dy;
            if (d2 < best) best = d2;
        }
        if (best > maxdy) maxdy = best;
    }
    lost = open->dynamic_n - now->dynamic_n;
    fprintf(stderr, "AB_HOLD %s score=%.0f->%.0f dyn=%d->%d h=%.2f->%.2f drop=%.2f maxd2=%.3f castle=%d->%d\n",
            lvl, (double)open->score, (double)now->score, open->dynamic_n, now->dynamic_n,
            (double)oh, (double)nh, (double)drop, (double)maxdy, castle_open, castle_now);
    if (now->score > 0.5f) return 0;
    if (open->score > 0.5f) return 0;
    if (drop > 0.25f) return 0;
    if (maxdy > (oh * 0.35f) * (oh * 0.35f) && maxdy > 4.f) return 0;
    if (lost > 1) return 0;
    if (castle_open >= 3 && castle_now + 1 < castle_open) return 0;
    return 1;
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
/* Pause is the yellow circle at portrait top-right (11f65be ~205-232,6-43);
 * Score is brown digits at the bottom (~57-159,372-431). A clipped MENU_BUTTON
 * sliver is green, not yellow. */
static int hud_ok(const char *tag) {
    int x, y, yel = 0, dark = 0, ink = 0, n = 0, grn = 0, icon_yel = 0;
    double sum = 0, sum2 = 0;
    for (y = 0; y < 56; y++) for (x = 160; x < 240; x++) {
        int r = be_fb[y][x][0], g = be_fb[y][x][1], b = be_fb[y][x][2];
        if (r > 170 && g > 130 && b < 140 && (int)r + (int)g > 2 * (int)b + 40) yel++;
        if (r < 70 && g < 70 && b < 70) dark++;
        if (g > 140 && r < 110 && b < 110 && g > r + 30) grn++;
    }
    for (y = 4; y < 48; y++) for (x = 196; x < 236; x++) {
        int r = be_fb[y][x][0], g = be_fb[y][x][1], b = be_fb[y][x][2];
        if (r > 170 && g > 130 && b < 140 && (int)r + (int)g > 2 * (int)b + 40) icon_yel++;
    }
    for (y = 372; y < 432; y++) for (x = 8; x < 160; x++) {
        int r = be_fb[y][x][0], g = be_fb[y][x][1], b = be_fb[y][x][2];
        int yv = (r * 3 + g * 6 + b) / 10;
        sum += yv; sum2 += (double)yv * yv; n++;
        if (r > 70 && g > 40 && b < 90 && r > b + 15) ink++;
    }
    {
        int var = n ? (int)(sum2 / n - (sum / n) * (sum / n)) : 0;
        fprintf(stderr, "AB_HUD %s pause_yel=%d icon_yel=%d pause_dark=%d pause_grn=%d score_ink=%d score_var=%d last_tex=%u,%u\n",
                tag, yel, icon_yel, dark, grn, ink, var,
                (unsigned)be_last_tex[20][210], (unsigned)be_last_tex[400][80]);
        /* Pause II bars are light gold, not black; require the yellow disc
         * and brown Score ink against 11f65be, and reject the green-blob miss. */
        return yel >= 60 && icon_yel >= 40 && grn < 50 && ink >= 40 && var >= 80;
    }
}
static int framing_ok(const char *tag, const ab_phys_info *a) {
    float bx, by, px, py;
    int i, n_in = 0, n_clip = 0, n_castle = 0, n_castle_clip = 0;
    if (tag && strstr(tag, "pinch")) return 1;
    if (!ab_pouch_world(&bx, &by) || !ab_world_to_panel(bx, by, &px, &py)) {
        fprintf(stderr, "AB_FRAME %s no pouch\n", tag);
        return 0;
    }
    /* Landscape left (slingshot) is portrait top. Birds may sit near y=8. */
    if (px < 8.f || px > 232.f || py < 4.f || py > 210.f) {
        fprintf(stderr, "AB_FRAME %s pouch panel %.1f,%.1f out of opening view\n",
                tag, (double)px, (double)py);
        return 0;
    }
    for (i = 0; i < a->vis_n; i++) {
        int cx = (int)(a->vis_px[i] + 0.5f);
        int cy = (int)(a->vis_py[i] + 0.5f);
        if (a->vis_type[i] != 2) continue;
        if (cy > 160) {
            if (cy > 420 || cx < 4 || cx > 236) {
                fprintf(stderr, "AB_FRAME %s castle_clip body t=%d panel=%d,%d world=%.2f,%.2f\n",
                        tag, a->vis_type[i], cx, cy,
                        (double)a->vis_x[i], (double)a->vis_y[i]);
                n_castle_clip++; n_clip++; continue;
            }
            n_castle++;
            n_in++;
            continue;
        }
        if (cy > 412 || cx < 8 || cx > 232) { n_clip++; continue; }
        n_in++;
    }
    fprintf(stderr, "AB_FRAME %s pouch=%.1f,%.1f in=%d clip=%d castle=%d castle_clip=%d scale=%.3f\n",
            tag, (double)px, (double)py, n_in, n_clip, n_castle, n_castle_clip,
            (double)a->world_scale);
    return n_in >= 4 && n_castle_clip == 0 && n_castle >= 1 && a->world_scale >= 0.22f;
}
static void dump_castle_crop(const char *name, const ab_phys_info *a) {
    char p[512]; FILE *f;
    int i, cx = 0, cy = 0, n = 0, x, y, u, v, side = 52;
    for (i = 0; i < a->vis_n; i++) {
        if (a->vis_type[i] != 2 || a->vis_py[i] < 160.f) continue;
        cx += (int)(a->vis_px[i] + 0.5f);
        cy += (int)(a->vis_py[i] + 0.5f);
        n++;
    }
    if (n < 1) return;
    cx /= n; cy /= n;
    snprintf(p, sizeof p, "%s/%s.ppm", out_dir, name);
    f = fopen(p, "wb"); if (!f) return;
    fprintf(f, "P6 %d %d 255\n", side * 4, side * 4);
    for (y = cy - side; y < cy + side; y++) for (v = 0; v < 2; v++)
        for (x = cx - side; x < cx + side; x++) for (u = 0; u < 2; u++) {
            int px = x, py = y;
            if (px < 0) px = 0;
            if (px > 239) px = 239;
            if (py < 0) py = 0;
            if (py > 431) py = 431;
            fwrite(be_fb[py][px], 1, 3, f);
        }
    fclose(f);
    fprintf(stderr, "AB_DUMP %s castle 2x at %d,%d n=%d\n", name, cx, cy, n);
}
static int level_sane(const char *name, ab_phys_info *a) {
    int pix, blk, bad = 0, tok = 0, tflat = 0;
    ab_phys_snapshot(a);
    pix = play_pixels();
    blk = panel_black();
    if (a->dynamic_n < 1) bad = 1;
    if (a->vis_dyn < 1 || a->vis_all < 2) bad = 1;
    if (a->world_scale < 0.18f || a->world_scale > 1.05f) bad = 1;
    if (pix < 800) bad = 1;
    if (blk > 240 * 432 / 12) bad = 1;
    if (be_stat_tex_fail) bad = 1;
    if (!bodies_textured(name, a, &tok, &tflat)) bad = 1;
    if (!hud_ok(name)) bad = 1;
    if (!framing_ok(name, a)) bad = 1;
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
static void drag_up(float x, float y) {
    ab_touch(0, 0, x, y);
    fake_us += AB_FRAME_US; ab_frame(AB_FRAME_DT);
}
static int pause_menu_up(void) {
    int y, x, yel_mid = 0, band = 0, black = 0;
    for (y = 0; y < 96; y++) for (x = 0; x < 240; x++) {
        int r = be_fb[y][x][0], g = be_fb[y][x][1], b = be_fb[y][x][2];
        if (r > 170 && g > 130 && b < 140 && (int)r + (int)g > 2 * (int)b + 40 && x < 150)
            yel_mid++;
        if (g > 70 && g < 150 && r < 70 && b < 90) band++;
    }
    for (y = 140; y < 432; y++) for (x = 0; x < 240; x++) {
        int r = be_fb[y][x][0], g = be_fb[y][x][1], b = be_fb[y][x][2];
        if (r < 12 && g < 12 && b < 12) black++;
    }
    /* In-game pause disc is top-right yellow only. The menu has extra
     * yellow buttons plus a green strip and a dark lower overlay. */
    return yel_mid >= 80 && band >= 40 && black >= 15000;
}
static void tap_resume(void) {
    /* Resume is the circular-arrow disc, rightmost large yellow button
     * on the pause strip (about 168,36 on the 240x432 panel). */
    tap_at(168.f, 36.f);
    wait_frames(6);
    if (pause_menu_up()) { tap_at(155.f, 32.f); wait_frames(6); }
    if (pause_menu_up()) { tap_at(178.f, 42.f); wait_frames(8); }
}
static int grab_rubber(float *px, float *py) {
    float bx, by, cx, cy, x, y;
    int i;
    if (!ab_lua_xy("rubberBandPos", &bx, &by) ||
        !ab_world_to_panel(bx, by, &x, &y))
        return 0;
    x = clampf(x, 12.f, 228.f);
    y = clampf(y, 20.f, 410.f);
    /* Click the on-screen bird. Lua picks the sprite in screen space, so
     * do not walk the finger toward cursorPhysics before selectedBird. */
    ab_touch(0, 1, x, y);
    fake_us += AB_FRAME_US; ab_frame(AB_FRAME_DT);
    ab_touch(0, 1, x + 1.f, y + 1.f);
    fake_us += AB_FRAME_US; ab_frame(AB_FRAME_DT);
    ab_touch(0, 1, x, y);
    for (i = 0; i < 8; i++) {
        fake_us += AB_FRAME_US; ab_frame(AB_FRAME_DT);
        ab_lua_xy("cursorPhysics", &cx, &cy);
        ab_lua_xy("rubberBandPos", &bx, &by);
        fprintf(stderr, "AB_GRAB i=%d panel=%.1f,%.1f phys=%.2f,%.2f rbp=%.2f,%.2f sel=%d rlen=%.3f\n",
                i, (double)x, (double)y, (double)cx, (double)cy,
                (double)bx, (double)by, ab_lua_has("selectedBird"),
                (double)ab_lua_get_number("rubberBandLength"));
        if (ab_lua_has("selectedBird"))
            break;
    }
    *px = x;
    *py = y;
    return ab_lua_has("selectedBird");
}
/* Hold LBUTTON and servo cursorPhysics to (tx, ty). +y is down. */
static int pull_toward(float *px, float *py, float tx, float ty) {
    float cx, cy, ex, ey, x = *px, y = *py;
    int i;
    for (i = 0; i < 24; i++) {
        if (!ab_lua_xy("cursorPhysics", &cx, &cy)) break;
        ex = tx - cx;
        ey = ty - cy;
        fprintf(stderr, "AB_PULL i=%d panel=%.1f,%.1f phys=%.2f,%.2f tgt=%.2f,%.2f err=%.2f,%.2f rlen=%.3f\n",
                i, (double)x, (double)y, (double)cx, (double)cy,
                (double)tx, (double)ty, (double)ex, (double)ey,
                (double)ab_lua_get_number("rubberBandLength"));
        if (ex * ex + ey * ey < 0.20f) break;
        y = clampf(y + ex * 5.5f, 8.f, 424.f);
        x = clampf(x - ey * 3.8f, 8.f, 232.f);
        ab_touch(0, 1, x, y);
        fake_us += AB_FRAME_US; ab_frame(AB_FRAME_DT);
    }
    *px = x;
    *py = y;
    return 1;
}
/* Lua sets selectedBird on keyPressed.LBUTTON inside shootRange, not on
 * MOVE. After a servo that walked in from outside, re-press in place. */
static int ensure_selected(float x, float y) {
    int i;
    /* Already on the bird: never ACTION_UP, that would fire a dribble. */
    if (ab_lua_has("selectedBird"))
        return 1;
    for (i = 0; i < 4; i++) {
        fake_us += AB_FRAME_US; ab_frame(AB_FRAME_DT);
        if (ab_lua_has("selectedBird"))
            return 1;
    }
    ab_touch(0, 0, x, y);
    fake_us += AB_FRAME_US; ab_frame(AB_FRAME_DT);
    ab_touch(0, 1, x, y);
    for (i = 0; i < 10; i++) {
        fake_us += AB_FRAME_US; ab_frame(AB_FRAME_DT);
        if (ab_lua_has("selectedBird")) {
            fprintf(stderr, "AB_PLAY_TEST selected after repress frames=%d rlen=%.3f\n",
                    i, (double)ab_lua_get_number("rubberBandLength"));
            return 1;
        }
    }
    fprintf(stderr, "AB_PLAY_TEST still unselected rlen=%.3f has=%d\n",
            (double)ab_lua_get_number("rubberBandLength"), ab_lua_has("selectedBird"));
    return 0;
}
static void log_flyer(const char *tag, const ab_phys_info *open) {
    ab_phys_info n;
    int i, j, besti = -1;
    float best = 0.f;
    ab_phys_snapshot(&n);
    for (i = 0; i < n.sample_n; i++) if (n.sample_type[i] == 2) {
        float bestd = 1e9f;
        for (j = 0; j < open->sample_n; j++) if (open->sample_type[j] == 2) {
            float dx = n.sample_x[i] - open->sample_x[j];
            float dy = n.sample_y[i] - open->sample_y[j];
            float d2 = dx * dx + dy * dy;
            if (d2 < bestd) bestd = d2;
        }
        if (bestd > best) { best = bestd; besti = i; }
    }
    fprintf(stderr, "AB_FLY %s bird=%.2f,%.2f d2=%.2f score=%.0f fired=%d hasFly=%d\n",
            tag ? tag : "-",
            besti >= 0 ? (double)n.sample_x[besti] : 0.0,
            besti >= 0 ? (double)n.sample_y[besti] : 0.0,
            (double)best, (double)n.score,
            ab_lua_get_bool("birdFired"), ab_lua_has("flyingBird"));
}
static int level_over(void);
static int wait_bird_on_sling(int max_frames) {
    int i, j, k;
    float bx, by;
    char name[48];
    for (i = 0; i < max_frames; i++) {
        ab_phys_info n;
        int near = 0;
        wait_frames(1);
        if (level_over()) return 0;
        if (!ab_lua_xy("rubberBandPos", &bx, &by)) continue;
        ab_phys_snapshot(&n);
        for (j = 0; j < n.sample_n; j++) if (n.sample_type[j] == 2) {
            float dx = n.sample_x[j] - bx, dy = n.sample_y[j] - by;
            if (dx * dx + dy * dy < 2.2f * 2.2f) near = 1;
        }
        name[0] = 0;
        ab_lua_obj_name("currentBirdName", name, (int)sizeof name);
        if (near && ab_lua_get_bool("birdReady") && name[0] && ab_body_exists(name)) {
            fprintf(stderr, "AB_PLAY_TEST on-sling frames=%d bird=%s rbp=%.2f,%.2f ready=%d\n",
                    i, name, (double)bx, (double)by, ab_lua_get_bool("birdReady"));
            return 1;
        }
        if (i == 0 || i == max_frames - 1 || i % 25 == 24) {
            fprintf(stderr, "AB_PLAY_TEST waiting-sling i=%d ready=%d name=%s rbp=%.2f,%.2f left=",
                    i, ab_lua_get_bool("birdReady"), name[0] ? name : "-",
                    (double)bx, (double)by);
            k = 0;
            for (j = 0; j < n.sample_n && k < 6; j++) if (n.sample_type[j] == 2 && n.sample_x[j] < 8.f) {
                fprintf(stderr, " %.2f,%.2f", (double)n.sample_x[j], (double)n.sample_y[j]);
                k++;
            }
            fprintf(stderr, "\n");
        }
    }
    return 0;
}
static float body_travel2(const ab_phys_info *a, const ab_phys_info *b) {
    int i;
    float maxd = 0.f;
    for (i = 0; i < b->vis_n; i++) if (b->vis_type[i] == 2) {
        int j;
        float bestd = 1e9f;
        for (j = 0; j < a->vis_n; j++) if (a->vis_type[j] == 2) {
            float dx = b->vis_x[i] - a->vis_x[j], dy = b->vis_y[i] - a->vis_y[j];
            float d2 = dx * dx + dy * dy;
            if (d2 < bestd) bestd = d2;
        }
        if (bestd > maxd) maxd = bestd;
    }
    return maxd;
}

static int level_over(void) {
    return ab_lua_get_bool("levelCompleted") || ab_lua_get_bool("levelFailed");
}
static int end_menu_up(void) {
    int y, x, yel = 0, dark = 0;
    if (!level_over()) return 0;
    for (y = 70; y < 390; y++) for (x = 30; x < 210; x++) {
        int r = be_fb[y][x][0], g = be_fb[y][x][1], b = be_fb[y][x][2];
        if (r > 170 && g > 130 && b < 140 && (int)r + (int)g > 2 * (int)b + 40) yel++;
        if (r < 20 && g < 20 && b < 20) dark++;
    }
    return yel >= 120 && dark >= 4000;
}
static void tap_game(float gx, float gy) {
    float px = fe_off_x + (fe_fb_h - gy) * fe_scale;
    float py = fe_off_y + gx * fe_scale_y;
    tap_at(clampf(px, 8.f, 232.f), clampf(py, 8.f, 424.f));
}
/* levelComplete items: MENU at (sw/2-100, sh/2+106), RESTART at
 * (sw/2, sh/2+106), NEXT at (sw/2+100, sh/2+106). Lua hit-tests
 * nativeInput game coords, so tap those, not the pause-menu
 * BUTTON_RESTART which lives at a different page. */
static void tap_restart_level(void) {
    static const float pts[][2] = {
        {240.f, 266.f}, {240.f, 260.f}, {248.f, 270.f}, {232.f, 266.f}, {240.f, 250.f}
    };
    int i;
    for (i = 0; i < 5 && level_over(); i++) {
        tap_game(pts[i][0], pts[i][1]);
        wait_frames(10);
    }
}
static void tap_next_level(void) {
    static const float pts[][2] = {
        {340.f, 266.f}, {340.f, 260.f}, {348.f, 270.f}, {332.f, 266.f}, {350.f, 250.f}
    };
    int i;
    for (i = 0; i < 5 && level_over(); i++) {
        tap_game(pts[i][0], pts[i][1]);
        wait_frames(10);
    }
}
static int wait_level_end(int max_frames) {
    int i;
    for (i = 0; i < max_frames; i++) {
        wait_frames(2);
        if (end_menu_up()) return 1;
        if (level_over() && i > 40) return 1;
    }
    return end_menu_up() || level_over();
}
static int bird_named_ready(void) {
    char name[48];
    name[0] = 0;
    if (!ab_lua_get_bool("birdReady")) return 0;
    if (!ab_lua_obj_name("currentBirdName", name, (int)sizeof name) || !name[0])
        return 0;
    return ab_body_exists(name);
}
static int seat_next_bird(int max_frames) {
    if (level_over()) return 0;
    /* Do not poke levelStartTimer / fillInNextBird: that re-inits
     * currentBirdIndex to 0. Lua's nextBirdTimer hop is enough. */
    wait_frames(2);
    ab_lua_dump_birds("seat");
    if (level_over()) return 0;
    return wait_bird_on_sling(max_frames);
}
static void wait_shot_settle(int max_frames) {
    int i;
    for (i = 0; i < max_frames && !level_over(); i++) {
        wait_frames(2);
        if (bird_named_ready()) return;
    }
}
static int fire_drag(const char *tag, float back, float down) {
    float bx = 0.f, by = 0.f, q0x = 0.f, q0y = 0.f, tx, ty, rlen;
    if (level_over()) return 0;
    if (!bird_named_ready()) {
        fprintf(stderr, "AB_PLAY_TEST %s not seated ready=%d\n",
                tag, ab_lua_get_bool("birdReady"));
        return 0;
    }
    if (!ab_lua_xy("rubberBandPos", &bx, &by) || !grab_rubber(&q0x, &q0y)) {
        fprintf(stderr, "AB_PLAY_TEST %s grab failed rbp=%.2f,%.2f\n",
                tag, (double)bx, (double)by);
        if (q0x != 0.f || q0y != 0.f) ab_touch(0, 0, q0x, q0y);
        return 0;
    }
    ensure_selected(q0x, q0y);
    ab_lua_xy("rubberBandPos", &bx, &by);
    tx = bx - back;
    ty = by + down;
    pull_toward(&q0x, &q0y, tx, ty);
    rlen = ab_lua_get_number("rubberBandLength");
    fprintf(stderr, "AB_PLAY_TEST %s rlen=%.3f sel=%d\n",
            tag, (double)rlen, ab_lua_has("selectedBird"));
    ab_lua_dump_shot(tag);
    ab_lua_dump_birds(tag);
    if (rlen <= 0.8f) {
        ab_touch(0, 0, q0x, q0y);
        wait_frames(1);
        return 0;
    }
    drag_up(q0x, q0y);
    return 1;
}

static void on_crash(int sig, siginfo_t *si, void *ctx) {
    ucontext_t *uc = (ucontext_t *)ctx;
    unsigned long pc = (unsigned long)uc->uc_mcontext.arm_pc;
    unsigned long lr = (unsigned long)uc->uc_mcontext.arm_lr;
    unsigned long sp = (unsigned long)uc->uc_mcontext.arm_sp;
    unsigned long base = (unsigned long)(uintptr_t)g_so.base;
    unsigned long *s;
    int i;
    fprintf(stderr, "CRASH sig=%d addr=%p pc=%08lx lr=%08lx sp=%08lx r0=%08lx r1=%08lx r2=%08lx r3=%08lx r4=%08lx r5=%08lx r6=%08lx r7=%08lx\n",
            sig, si ? si->si_addr : 0, pc, lr, sp,
            (unsigned long)uc->uc_mcontext.arm_r0, (unsigned long)uc->uc_mcontext.arm_r1,
            (unsigned long)uc->uc_mcontext.arm_r2, (unsigned long)uc->uc_mcontext.arm_r3,
            (unsigned long)uc->uc_mcontext.arm_r4, (unsigned long)uc->uc_mcontext.arm_r5,
            (unsigned long)uc->uc_mcontext.arm_r6, (unsigned long)uc->uc_mcontext.arm_r7);
    if (base)
        fprintf(stderr, "CRASH so=%08lx pc_off=%08lx lr_off=%08lx\n",
                base, pc >= base ? pc - base : pc, lr >= base ? lr - base : lr);
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
        int fail = 0, i, flying = 0, hit = 0, stretched = 0, cleared = 0;
        ab_phys_info a, b, mid, open;
        float p0x = 0.f, p0y = 0.f, p1x = 0.f, p1y = 0.f, rlen = 0.f;
        wait_frames(200);
        if (every) shot(1);
        ab_call_lua("gotoFirstLevel");
        wait_frames(20);
        for (i = 0; i < 8; i++) { tap_at(49.f, 324.f); tap_at(55.f, 333.f); }
        wait_frames(10);
        ab_load_lua_level("Level1");
        wait_frames(50);
        ab_cam_fit_world();
        wait_frames(2);
        if (every) { shot(6); dump_panel("panel-l1"); }
        ab_phys_snapshot(&open);
        if (!hud_ok("play-l1") || !framing_ok("play-l1", &open)) fail++;
        ab_phys_enable(1);
        wait_frames(20);
        if (every) shot(7);
        ab_ready_slingshot();
        for (i = 0; i < 120; i++) {
            wait_frames(1);
            if (bird_named_ready()) break;
        }
        fprintf(stderr, "AB_PLAY_TEST seated frames=%d ready=%d\n", i, ab_lua_get_bool("birdReady"));
        if (!bird_named_ready()) {
            /* Lua never seated a bird; last resort so the drag still runs. */
            ab_ready_slingshot();
        }
        ab_cam_fit_aim();
        wait_frames(4);
        ab_phys_snapshot(&a);
        ab_phys_log("play-before-shot");
        ab_lua_dump_shot("before");
        if (a.dynamic_n < 1) fail++;
        {
            float bx, by, sx, sy, lsx = 0.f, lsy = 0.f, tx, ty;
            (void)ab_sling_world(&sx, &sy);
            (void)ab_pouch_world(&sx, &sy);
            for (i = 0; i < a.vis_n && i < 12; i++) if (a.vis_type[i] == 2)
                fprintf(stderr, "AB_PLAY_TEST vis t=2 world=%.2f,%.2f panel=%.1f,%.1f\n",
                        (double)a.vis_x[i], (double)a.vis_y[i],
                        (double)a.vis_px[i], (double)a.vis_py[i]);
            /* Lua shootRange is around rubberBandPos. Servo the finger
             * until cursorPhysics is inside it, then pull back and down
             * (+y is gravity-down) so release lofts toward the tower. */
            if (!ab_lua_xy("rubberBandPos", &bx, &by)) {
                fprintf(stderr, "AB_PLAY_TEST no rubberBandPos\n");
                fail++;
            } else if (!grab_rubber(&p0x, &p0y)) {
                fprintf(stderr, "AB_PLAY_TEST grab missed rbp=%.2f,%.2f panel=%.1f,%.1f\n",
                        (double)bx, (double)by, (double)p0x, (double)p0y);
                fail++;
                ab_touch(0, 0, p0x, p0y);
            } else {
                ab_lua_xy("rubberBandPos", &bx, &by);
                if (ab_lua_has("levelStartPosition"))
                    ab_lua_xy("levelStartPosition", &lsx, &lsy);
                /* ~30 deg loft: back 4.3, down 2.5, inside shootMaxLength. */
                tx = bx - 4.30f;
                ty = by + 2.50f;
                ab_lua_dump_shot("down");
                ensure_selected(p0x, p0y);
                pull_toward(&p0x, &p0y, tx, ty);
                p1x = p0x;
                p1y = p0y;
                fprintf(stderr, "AB_PLAY_TEST rbp world %.2f,%.2f start %.2f,%.2f panel %.1f,%.1f scale=%.3f\n",
                        (double)bx, (double)by, (double)lsx, (double)lsy,
                        (double)p1x, (double)p1y, (double)a.world_scale);
                ab_lua_dump_shot("aim");
                rlen = ab_lua_get_number("rubberBandLength");
                stretched = rlen > 0.8f;
                if (every) shot(8);
                dump_panel("panel-aim");
                fprintf(stderr, "AB_PLAY_TEST aim rlen=%.3f draws=%d\n", (double)rlen, fe_stat_draws);
                drag_up(p1x, p1y);
            }
        }
        wait_frames(4);
        ab_cam_fit_world();
        wait_frames(2);
        log_flyer("fly-1", &a);
        dump_panel("panel-fly-1");
        wait_frames(10);
        ab_cam_fit_world();
        wait_frames(2);
        log_flyer("fly-2", &a);
        dump_panel("panel-fly-2");
        ab_phys_snapshot(&mid);
        ab_phys_log("play-inflight");
        dump_panel("panel-play");
        {
            float maxd = body_travel2(&a, &mid);
            flying = maxd > 16.f;
            fprintf(stderr, "AB_PLAY_TEST travel2=%.3f flying=%d\n", (double)maxd, flying);
        }
        if (every) shot(9);
        for (i = 0; i < 90; i++) {
            wait_frames(2);
            if ((i % 8) == 0) log_flyer("wait", &a);
            ab_phys_snapshot(&b);
            if (b.score > 0.5f) { hit = 1; break; }
        }
        ab_cam_fit_world();
        wait_frames(2);
        ab_phys_snapshot(&b);
        ab_phys_log("play-after-shot");
        ab_lua_dump_shot("hit");
        dump_panel("panel-hit");
        if (every) shot(5);
        if (b.score > 0.5f) hit = 1;
        fprintf(stderr, "AB_PLAY_TEST hit score=%.0f travel_ok=%d stretched=%d\n",
                (double)b.score, flying, stretched);
        /* Wait for Lua's nextBirdTimer hop (nextBirdTimer=1 on launch).
         * Do not call ab_ready_slingshot: that zeros levelStartTimer and
         * updateGame re-inits currentBirdIndex to 0, so getNextBird(1)
         * is the live flying bird and setPosition drefs a destroyed body. */
        ab_cam_set(open.world_scale, open.cam_left, open.cam_top);
        wait_frames(1);
        ab_cam_fit_aim();
        wait_frames(1);
        ab_lua_dump_birds("pre-shot2");
        if (!level_over()) {
            if (!bird_named_ready() && !seat_next_bird(180))
                fprintf(stderr, "AB_PLAY_TEST shot2 no bird on sling ready=%d\n",
                        ab_lua_get_bool("birdReady"));
            if (bird_named_ready())
                ab_call_lua("startNewTrajectory");
            wait_frames(2);
            dump_panel("panel-trail");
            fprintf(stderr,
                    "AB_PLAY_TEST shot2 idx=%.0f shot=%.0f fired=%d bsel=%d fly=%d nextT=%.1f slider=%.2f n900=%d\n",
                    (double)ab_lua_get_number("currentBirdIndex"),
                    (double)ab_lua_get_number("birdsShot"),
                    ab_lua_get_bool("birdFired"), ab_lua_get_bool("birdSelected"),
                    ab_lua_has("flyingBird"), (double)ab_lua_get_number("nextBirdTimer"),
                    (double)ab_lua_get_number("cameraAnimationSlider"),
                    ab_lua_get_bool("n900"));
            ab_lua_dump_birds("shot2-ready");
            {
                float bx, by, q0x = 0.f, q0y = 0.f;
                ab_phys_info lefts, pre2, mid2;
                int bi, shot2 = 0;
                float maxd;
                ab_phys_snapshot(&lefts);
                fprintf(stderr, "AB_PLAY_TEST shot2 sample_n=%d dyn=%d\n", lefts.sample_n, lefts.dynamic_n);
                for (bi = 0; bi < lefts.sample_n && bi < 24; bi++)
                    fprintf(stderr, "AB_PLAY_TEST shot2 body t=%d %.2f,%.2f\n",
                            lefts.sample_type[bi], (double)lefts.sample_x[bi],
                            (double)lefts.sample_y[bi]);
                ab_phys_snapshot(&pre2);
                if (bird_named_ready() && ab_lua_xy("rubberBandPos", &bx, &by) &&
                    grab_rubber(&q0x, &q0y)) {
                    ensure_selected(q0x, q0y);
                    ab_lua_xy("rubberBandPos", &bx, &by);
                    pull_toward(&q0x, &q0y, bx - 3.6f, by + 2.0f);
                    dump_panel("panel-aim2");
                    ab_lua_dump_shot("aim2");
                    ab_lua_dump_birds("aim2");
                    rlen = ab_lua_get_number("rubberBandLength");
                    if (rlen > 0.8f) cleared = 1;
                    drag_up(q0x, q0y);
                    shot2 = 1;
                } else {
                    dump_panel("panel-aim2");
                    ab_lua_dump_shot("aim2");
                    if (q0x != 0.f || q0y != 0.f) ab_touch(0, 0, q0x, q0y);
                }
                if (shot2) {
                    wait_frames(4);
                    ab_cam_fit_world();
                    wait_frames(2);
                    log_flyer("shot2-fly-1", &pre2);
                    dump_panel("panel-fly-shot2");
                    wait_frames(10);
                    ab_cam_fit_world();
                    wait_frames(2);
                    log_flyer("shot2-fly-2", &pre2);
                    ab_phys_snapshot(&mid2);
                    maxd = body_travel2(&pre2, &mid2);
                    fprintf(stderr, "AB_PLAY_TEST shot2 travel2=%.3f\n", (double)maxd);
                    if (maxd <= 16.f) fail++;
                    wait_shot_settle(80);
                } else fail++;
            }
        } else {
            dump_panel("panel-aim2");
            dump_panel("panel-trail");
            dump_panel("panel-fly-shot2");
            fprintf(stderr, "AB_PLAY_TEST shot2 skipped, level already over\n");
            fail++;
        }
        /* Remaining L1 birds until the win/loss screen. */
        while (!level_over()) {
            char tag[16];
            int nshot = (int)ab_lua_get_number("birdsShot");
            if (nshot >= 8) break;
            ab_cam_fit_aim();
            wait_frames(1);
            if (!bird_named_ready() && !seat_next_bird(180)) break;
            snprintf(tag, sizeof tag, "l1s%d", nshot + 1);
            if (!fire_drag(tag, 4.0f, 2.2f)) break;
            wait_shot_settle(80);
            ab_cam_fit_world();
            wait_frames(2);
        }
        wait_level_end(200);
        dump_panel("panel-end");
        ab_lua_dump_birds("end");
        fprintf(stderr, "AB_PLAY_TEST end done=%d fail=%d menu=%d score=%.0f shot=%.0f\n",
                ab_lua_get_bool("levelCompleted"), ab_lua_get_bool("levelFailed"),
                end_menu_up(), (double)ab_lua_get_number("score"),
                (double)ab_lua_get_number("birdsShot"));
        if (!level_over()) fail++;
        {
            int ended = level_over();
            char lvl[48];
            tap_restart_level();
            wait_frames(40);
            ab_cam_fit_world();
            wait_frames(2);
            dump_panel("panel-restart");
            lvl[0] = 0;
            ab_lua_obj_name("levelName", lvl, (int)sizeof lvl);
            fprintf(stderr, "AB_PLAY_TEST restart name=%s score=%.0f done=%d\n",
                    lvl[0] ? lvl : "-", (double)ab_lua_get_number("score"),
                    ab_lua_get_bool("levelCompleted"));
            if (!ended) fail++;
            if (ab_lua_get_bool("levelCompleted") && ab_lua_get_bool("levelFailed")) fail++;
            if (ab_lua_get_number("score") > 0.5f && level_over()) fail++;
            if (ab_lua_get_number("score") < 0.5f && !level_over()) {
                /* Restarted into a live L1. Play to the end screen again. */
                ab_ready_slingshot();
                wait_bird_on_sling(180);
                ab_cam_fit_aim();
                wait_frames(2);
                fire_drag("restart-s1", 4.3f, 2.5f);
                wait_shot_settle(80);
                while (!level_over()) {
                    if (!bird_named_ready() && !seat_next_bird(180)) break;
                    if (!fire_drag("restart-sn", 4.0f, 2.2f)) break;
                    wait_shot_settle(80);
                }
                wait_level_end(160);
            }
            tap_next_level();
            wait_frames(50);
            ab_cam_fit_world();
            wait_frames(2);
            dump_panel("panel-next");
            lvl[0] = 0;
            ab_lua_obj_name("levelName", lvl, (int)sizeof lvl);
            fprintf(stderr, "AB_PLAY_TEST next name=%s score=%.0f done=%d\n",
                    lvl[0] ? lvl : "-", (double)ab_lua_get_number("score"),
                    ab_lua_get_bool("levelCompleted"));
            /* loadNextLevel follows levelOrder (L1 then Level57), not L1->L2. */
            if (!lvl[0] || ab_lua_get_bool("levelCompleted")) fail++;
            if (lvl[0] && strstr(lvl, "Level1")) fail++;
        }
        /* L2 and L3: at least three real drags each. */
        {
            const char *lvls[2] = { "Level2", "Level3" };
            int li, si, nfire;
            for (li = 0; li < 2; li++) {
                ab_load_lua_level(lvls[li]);
                wait_frames(40);
                ab_cam_fit_world();
                wait_frames(2);
                nfire = 0;
                ab_ready_slingshot();
                wait_bird_on_sling(180);
                for (si = 0; si < 3 && !level_over(); si++) {
                    char tag[20];
                    ab_cam_fit_aim();
                    wait_frames(1);
                    if (si > 0 && !bird_named_ready() && !seat_next_bird(180)) break;
                    snprintf(tag, sizeof tag, "%s-s%d", lvls[li], si + 1);
                    if (!fire_drag(tag, 4.0f, 2.2f)) break;
                    nfire++;
                    wait_shot_settle(80);
                    ab_cam_fit_world();
                    wait_frames(2);
                    log_flyer(tag, &open);
                }
                fprintf(stderr, "AB_PLAY_TEST %s shots=%d score=%.0f\n",
                        lvls[li], nfire, (double)ab_lua_get_number("score"));
                if (nfire < 3) fail++;
            }
        }
        wait_frames(4);
        fprintf(stderr, "AB_PLAY_TEST heap used %u peak %u flying=%d hit=%d stretched=%d aim2=%d dyn=%d\n",
                ab_heap_used(), ab_heap_peak(), flying, hit, stretched, cleared, b.dynamic_n);
        if (ab_malloc_failures()) fail++;
        if (!flying) fail++;
        if (!hit) fail++;
        if (!stretched) fail++;
        if (!cleared) fail++;
        if (b.dynamic_n < 1) fail++;
        fprintf(stderr, "AB_PLAY_TEST %s\n", fail ? "FAIL" : "PASS");
        return fail ? 1 : 0;
    }

    if (getenv("AB_GESTURE_TEST")) {
        int fail = 0, i, paused = 0, resumed = 0;
        ab_phys_info a, b;
        unsigned pr, pg, pb, qr, qg, qb;
        float ox, oy, sx, sy, sc[5];
        wait_frames(200);
        ab_call_lua("gotoFirstLevel");
        wait_frames(20);
        for (i = 0; i < 8; i++) { tap_at(49.f, 324.f); tap_at(55.f, 333.f); }
        wait_frames(20);
        ab_load_lua_level("Level1");
        wait_frames(90);
        ab_cam_fit_world();
        wait_frames(2);
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
        ab_cam_fit_world();
        wait_frames(2);
        if (!level_sane("Level2", &a)) fail++;
        dump_panel("panel-l2");
        sc[0] = a.world_scale;
        /* in, out, in, out: zoom must move both ways every time, and the
         * view must stay over the level (pinch clamp in ab_apply_pinch). */
        pinch_in(16); wait_frames(6); ab_phys_snapshot(&b); sc[1] = b.world_scale;
        dump_panel("panel-pinch-1");
        fprintf(stderr, "AB_GESTURE_TEST pinch-in L2 scale %.3f -> %.3f vis %d/%d\n",
                (double)sc[0], (double)sc[1], b.vis_all, b.bodies);
        pinch_out(16); wait_frames(6); ab_phys_snapshot(&b); sc[2] = b.world_scale;
        dump_panel("panel-pinch-2");
        fprintf(stderr, "AB_GESTURE_TEST pinch-out L2 scale %.3f -> %.3f vis %d/%d\n",
                (double)sc[1], (double)sc[2], b.vis_all, b.bodies);
        pinch_in(16); wait_frames(6); ab_phys_snapshot(&b); sc[3] = b.world_scale;
        dump_panel("panel-pinch-3");
        fprintf(stderr, "AB_GESTURE_TEST pinch-in2 L2 scale %.3f -> %.3f vis %d/%d\n",
                (double)sc[2], (double)sc[3], b.vis_all, b.bodies);
        pinch_out(16); wait_frames(6); ab_phys_snapshot(&b); sc[4] = b.world_scale;
        dump_panel("panel-pinch-4");
        fprintf(stderr, "AB_GESTURE_TEST pinch-out2 L2 scale %.3f -> %.3f vis %d/%d\n",
                (double)sc[3], (double)sc[4], b.vis_all, b.bodies);
        dump_panel("panel-pinch-in");
        dump_panel("panel-pinch-out");
        if (!(sc[1] > sc[0] + 0.012f)) fail++;
        if (!(sc[2] < sc[1] - 0.012f)) fail++;
        if (!(sc[3] > sc[2] + 0.012f)) fail++;
        if (!(sc[4] < sc[3] - 0.012f)) fail++;
        for (i = 1; i <= 4; i++)
            if (sc[i] < 0.20f || sc[i] > 1.05f) fail++;
        if (!level_sane("Level2-pinch-4", &b)) fail++;
        tap_at(215.f, 24.f);
        wait_frames(12);
        dump_panel("panel-pause");
        paused = pause_menu_up();
        {
            int pix = play_pixels();
            fprintf(stderr, "AB_GESTURE_TEST pause pix=%d menu=%d\n", pix, paused);
            if (pix < 800) fail++;
            if (!paused) fail++;
        }
        tap_resume();
        wait_frames(8);
        dump_panel("panel-resume");
        resumed = !pause_menu_up();
        fprintf(stderr, "AB_GESTURE_TEST resume menu=%d hud=%d\n", pause_menu_up(), hud_ok("resume"));
        if (!resumed) fail++;
        if (!hud_ok("resume")) fail++;
        ab_load_lua_level("Level3");
        wait_frames(90);
        ab_cam_fit_world();
        wait_frames(2);
        if (!level_sane("Level3", &a)) fail++;
        dump_panel("panel-l3");
        fprintf(stderr, "AB_GESTURE_TEST %s\n", fail ? "FAIL" : "PASS");
        if (ab_malloc_failures()) return 3;
        return fail ? 1 : 0;
    }

    if (getenv("AB_LEVELS_TEST")) {
        int fail = 0, n, loaded = 0;
        int warm = atoi(getenv("AB_PHYS_WARM") ? getenv("AB_PHYS_WARM") : "200");
        int hold = atoi(getenv("AB_LEVELS_SETTLE") ? getenv("AB_LEVELS_SETTLE") : "150");
        int min_n = atoi(getenv("AB_LEVELS_MIN") ? getenv("AB_LEVELS_MIN") : "1");
        int max_n = atoi(getenv("AB_LEVELS_MAX") ? getenv("AB_LEVELS_MAX") : "3");
        wait_frames(warm);
        ab_call_lua("gotoFirstLevel");
        wait_frames(20);
        tap_at(49.f, 324.f);
        for (n = min_n; n <= max_n; n++) {
            char name[24], file[28];
            ab_phys_info open, held, a;
            int bad = 0, pix, blk;
            FILE *lf;
            snprintf(name, sizeof name, "Level%d", n);
            snprintf(file, sizeof file, "Level%d.lua", n);
            if (open_named(file, &lf)) continue;
            fclose(lf);
            ab_load_lua_level(name);
            wait_frames(2);
            ab_phys_snapshot(&open);
            fprintf(stderr, "AB_SCORE %s open=%.0f\n", name, (double)open.score);
            if (open.score > 0.5f) bad = 1;
            if (!match_level_file(name, &open)) bad = 1;
            wait_frames(hold);
            ab_phys_snapshot(&held);
            fprintf(stderr, "AB_SCORE %s hold=%.0f\n", name, (double)held.score);
            if (!hold_ok(name, &open, &held)) bad = 1;
            /* Re-fit after the idle so the opening dump still has the castle
             * inside the 480x320 view. */
            ab_cam_fit_world();
            wait_frames(2);
            ab_phys_snapshot(&a);
            ab_phys_log(name);
            loaded++;
            pix = play_pixels(); blk = panel_black();
            if (a.dynamic_n < 1) bad = 1;
            if (a.vis_dyn < 1 || a.vis_all < 8) bad = 1;
            if (a.world_scale < 0.18f || a.world_scale > 1.05f) bad = 1;
            if (pix < 800) bad = 1;
            if (blk > 240 * 432 / 12) bad = 1;
            if (be_stat_tex_fail) bad = 1;
            {
                int tok = 0, tflat = 0;
                if (!bodies_textured(name, &a, &tok, &tflat)) bad = 1;
                if (!hud_ok(name)) bad = 1;
                if (!framing_ok(name, &a)) bad = 1;
                fprintf(stderr, "AB_LEVELS_TEST %s dyn=%d vis=%d/%d scale=%.3f pix=%d black=%d tex_ok=%d tex_flat=%d score=%.0f %s\n",
                        name, a.dynamic_n, a.vis_all, a.bodies, (double)a.world_scale, pix, blk,
                        tok, tflat, (double)a.score, bad ? "FAIL" : "ok");
            }
            if (a.score > 0.5f) bad = 1;
            if (bad) fail++;
            {
                char pan[24], crop[32];
                snprintf(pan, sizeof pan, "panel-l%d", n);
                dump_panel(pan);
                snprintf(crop, sizeof crop, "panel-l%d-castle", n);
                dump_castle_crop(crop, &a);
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
