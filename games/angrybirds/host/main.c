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
#include "notice.h"

extern uint8_t be_fb[432][240][4];
extern int be_stat_pvrtc, be_stat_uncompressed, be_tex_peak_bytes, be_check_tiny, be_frame_tris_max, be_frame_big_max;
extern int be_stat_tex_fail;
static const char *data_dir, *img_path, *out_dir;
static uint64_t fake_us;
#define HOST_LOG_CAP 65536u
static char host_log[HOST_LOG_CAP];
static uint32_t host_log_len, host_log_dirty;
static void host_write_sync(void) {
    char p[512], last[256];
    uint32_t e = host_log_len, b, k;
    FILE *f;
    if (!out_dir) return;
    while (e && host_log[e - 1] == '\n') e--;
    b = e;
    while (b && host_log[b - 1] != '\n') b--;
    k = e - b;
    if (k > 240) k = 240;
    memcpy(last, host_log + b, k);
    last[k++] = '\n';
    snprintf(p, sizeof p, "%s/docs", out_dir);
    mkdir(p, 0755);
    snprintf(p, sizeof p, "%s/docs/sync.txt", out_dir);
    f = fopen(p, "wb");
    if (f) { fwrite(last, 1, k, f); fclose(f); }
}
void plat_log_line(const char *l) {
    uint32_t n;
    fprintf(stderr, "%s\n", l);
    n = (uint32_t)strlen(l);
    if (n > 200) n = 200;
    if (host_log_len + n + 1 > HOST_LOG_CAP) {
        uint32_t keep = HOST_LOG_CAP / 2;
        if (host_log_len > keep) {
            memmove(host_log, host_log + host_log_len - keep, keep);
            host_log_len = keep;
        }
        if (host_log_len + n + 1 > HOST_LOG_CAP) return;
    }
    memcpy(host_log + host_log_len, l, n);
    host_log_len += n;
    host_log[host_log_len++] = '\n';
    host_log_dirty = 1;
}
void plat_log_flush(void) {
    char p[512];
    FILE *f;
    if (!host_log_dirty || !out_dir) return;
    snprintf(p, sizeof p, "%s/docs", out_dir);
    mkdir(p, 0755);
    snprintf(p, sizeof p, "%s/docs/log.txt", out_dir);
    f = fopen(p, "wb");
    if (f) { fwrite(host_log, 1, host_log_len, f); fclose(f); }
    host_write_sync();
    host_log_dirty = 0;
}
void plat_log_step(const char *what, uint32_t a) {
    char b[160];
    snprintf(b, sizeof b, "step %s %u", what ? what : "-", (unsigned)a);
    plat_log_line(b);
    plat_log_flush();
}
void plat_trace(void) { void *b[24]; int n = backtrace(b, 24); char **s = backtrace_symbols(b, n);
    fprintf(stderr, "  at"); for (int i = 1; i < n; i++) { const char *p = strchr(s[i], '('); if (p && p[1] == 'F') fprintf(stderr, " %.*s", (int)(strcspn(p + 1, "+)")), p + 1); } fprintf(stderr, "\n"); }

/* Two device OS-heap profiles from real nano logs, plus a third that is too
 * small to start (red CHECK LOG.TXT screen). Power-of-two runs, address-ordered
 * first-fit, 16-byte payloads. An allocation that does not fit returns NULL.
 *   low   (c9880d1, Bluetooth + music): 3,865,672 free, 2,097,152 largest
 *   clean (Fruit Ninja clean boot):     6,014,264 free, 4,194,304 largest
 *   busy  (below both):                 2,097,152 free, 1,048,576 largest
 * AB_OS_HEAP=low|clean|busy. Default low so PLAY/GESTURE/DEVICE match the
 * tight device. The old plat_os_largest=4 MB plus libc malloc for convert
 * never saw SPLASHES/MENU "no memory". */
#define OS_LOW_FREE    3865672u
#define OS_LOW_2M      2097152u
#define OS_LOW_1M      1048576u
#define OS_LOW_512      524288u
#define OS_CLEAN_FREE  6014264u
#define OS_CLEAN_4M    4194304u
#define OS_CLEAN_1M    1048576u
#define OS_BUSY_FREE   2097152u
#define OS_BUSY_1M     1048576u
#define AB_BLOCK_MAX   0x200000u
#define AB_BLOCK_MIN   0x190000u
#define AB_AFTER_MIN   0x1A0000u
#define OS_MAXB 96
typedef struct { uint32_t off, sz; int used; } os_blk;
static uint8_t os_arena[OS_CLEAN_FREE];
static os_blk osb[OS_MAXB];
static int osn, os_ready, os_profile;   /* 0 low, 1 clean, 2 busy */
static uint32_t os_cap;
static const char *os_profile_name(void) {
    return os_profile == 1 ? "clean" : os_profile == 2 ? "busy" : "low";
}
static uint32_t os_p2floor(uint32_t n) {
    uint32_t p = 0x80000000u;
    if (!n) return 0;
    while (p > n) p >>= 1;
    return p;
}
static void os_coalesce(void) {
    int i, j;
    for (i = 0; i < osn; i++) for (j = i + 1; j < osn; j++)
        if (osb[i].off > osb[j].off) { os_blk t = osb[i]; osb[i] = osb[j]; osb[j] = t; }
    i = 0;
    while (i + 1 < osn) {
        if (!osb[i].used && !osb[i + 1].used && osb[i].off + osb[i].sz == osb[i + 1].off) {
            osb[i].sz += osb[i + 1].sz;
            memmove(&osb[i + 1], &osb[i + 2], (size_t)(osn - i - 2) * sizeof osb[0]);
            osn--;
        } else i++;
    }
}
static void os_init(void) {
    const char *e;
    if (os_ready) return;
    e = getenv("AB_OS_HEAP");
    os_profile = 0;
    if (e && (!strcmp(e, "clean") || !strcmp(e, "fn") || !strcmp(e, "6m"))) os_profile = 1;
    else if (e && (!strcmp(e, "busy") || !strcmp(e, "tiny"))) os_profile = 2;
    memset(osb, 0, sizeof osb);
    if (os_profile == 1) {
        /* 771384 + 4 MB + 1 MB = 6,014,264. Address-ordered first-fit skips
         * the head for a 2 MB guest and splits the 4 MB; leftover + 1 MB
         * coalesces the way the Fruit Ninja log's largest 2 MB did. */
        uint32_t head = OS_CLEAN_FREE - OS_CLEAN_4M - OS_CLEAN_1M;
        os_cap = OS_CLEAN_FREE;
        osn = 3;
        osb[0].off = 0; osb[0].sz = head;
        osb[1].off = head; osb[1].sz = OS_CLEAN_4M;
        osb[2].off = head + OS_CLEAN_4M; osb[2].sz = OS_CLEAN_1M;
    } else if (os_profile == 2) {
        os_cap = OS_BUSY_FREE;
        osn = 2;
        osb[0].off = 0; osb[0].sz = OS_BUSY_1M;
        osb[1].off = OS_BUSY_1M; osb[1].sz = OS_BUSY_1M;
    } else {
        os_cap = OS_LOW_FREE;
        osn = 4;
        osb[0].off = 0; osb[0].sz = OS_LOW_2M;
        osb[1].off = OS_LOW_2M; osb[1].sz = OS_LOW_1M;
        osb[2].off = OS_LOW_2M + OS_LOW_1M; osb[2].sz = OS_LOW_512;
        osb[3].off = OS_LOW_2M + OS_LOW_1M + OS_LOW_512;
        osb[3].sz = OS_LOW_FREE - (OS_LOW_2M + OS_LOW_1M + OS_LOW_512);
    }
    os_ready = 1;
}
static uint32_t os_free_sum(void) {
    uint32_t n = 0; int i;
    for (i = 0; i < osn; i++) if (!osb[i].used) n += osb[i].sz;
    return n;
}
void *plat_os_alloc(uint32_t n) {
    uint32_t want = (n + 15u) & ~15u, off, left;
    int i, best = -1;
    uint8_t *p;
    if (!want) want = 16;
    os_init();
    for (i = 0; i < osn; i++) {
        if (osb[i].used || osb[i].sz < want) continue;
        if (best < 0 || osb[i].off < osb[best].off) best = i;
    }
    if (best < 0) {
        uint32_t lg = 0;
        for (i = 0; i < osn; i++) if (!osb[i].used && osb[i].sz > lg) lg = osb[i].sz;
        fprintf(stderr, "os alloc %u refused (max run %u free %u nblk %d)\n",
                (unsigned)want, (unsigned)lg, (unsigned)os_free_sum(), osn);
        return NULL;
    }
    off = osb[best].off;
    left = osb[best].sz - want;
    osb[best].sz = want;
    osb[best].used = 1;
    if (left && osn < OS_MAXB) {
        osb[osn].off = off + want;
        osb[osn].sz = left;
        osb[osn].used = 0;
        osn++;
    }
    p = os_arena + off;
    /* Unwritten / failed OS memory is a loud stripe, like uninit GPU RAM. */
    for (i = 0; (uint32_t)i < want; i++) p[i] = (uint8_t)(0x39 + ((i * 13) ^ (i >> 3)));
    return p;
}
void plat_os_free(void *p) {
    uint32_t off; int i;
    if (!p || !os_ready) return;
    if ((uint8_t *)p < os_arena || (uint8_t *)p >= os_arena + os_cap) return;
    off = (uint32_t)((uint8_t *)p - os_arena);
    for (i = 0; i < osn; i++) if (osb[i].used && osb[i].off == off) {
        osb[i].used = 0;
        os_coalesce();
        return;
    }
}
uint32_t plat_os_largest(void) {
    uint32_t best = 0; int i;
    os_init();
    for (i = 0; i < osn; i++) if (!osb[i].used) {
        uint32_t p = os_p2floor(osb[i].sz);
        if (p > best) best = p;
    }
    return best;
}
uint32_t plat_os_free_total(void) { os_init(); return os_free_sum(); }
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
static int panel_washed(void) {
    /* Device white L2: almost the whole 240x432 is near-white / HUD-cyan. */
    int n = 0, y, x;
    for (y = 0; y < 432; y++) {
        for (x = 0; x < 240; x++) {
            unsigned r = be_fb[y][x][0], g = be_fb[y][x][1], b = be_fb[y][x][2];
            if (r >= 210 && g >= 210 && b >= 210) n++;
            else if (b > 190 && g > 170 && r < 190) n++;
        }
    }
    return n;
}
static int pix_white(unsigned r, unsigned g, unsigned b) {
    return r >= 230 && g >= 230 && b >= 230;
}
static int pix_cyan_void(unsigned r, unsigned g, unsigned b) {
    /* GL clear (0,1,1) after convert, not the painted hill/sky. */
    return b > 210 && g > 160 && r < 90;
}
static int pix_skyish(unsigned r, unsigned g, unsigned b) {
    if (pix_white(r, g, b) || pix_cyan_void(r, g, b)) return 1;
    if (b >= 200 && g >= 170 && r <= 210 && b >= r + 15) return 1;
    if (b >= 190 && g >= 185 && r >= 145 && (int)b + (int)g > 2 * (int)r + 15) return 1;
    return 0;
}
static int pix_is_ground(unsigned r, unsigned g, unsigned b) {
    if (pix_white(r, g, b) || pix_cyan_void(r, g, b)) return 0;
    if (b >= 200 && g >= 180 && r >= 150) return 0;
    if (g > r + 18 && g > b && g > 55 && g < 210 && r < 150) return 1;
    if (r < 95 && g < 120 && b < 120 && (int)g + (int)b > 40 && (int)r + (int)g + (int)b < 280)
        return 1;
    return 0;
}
static int panel_ground_pixels(void) {
    int n = 0, y, x;
    for (y = 0; y < 432; y++) {
        for (x = 0; x < 56; x++) {
            unsigned r = be_fb[y][x][0], g = be_fb[y][x][1], b = be_fb[y][x][2];
            if (pix_is_ground(r, g, b)) n++;
        }
    }
    return n;
}
static int panel_col_void(int x) {
    int n = 0, y;
    for (y = 0; y < 432; y++) {
        unsigned r = be_fb[y][x][0], g = be_fb[y][x][1], b = be_fb[y][x][2];
        if (pix_white(r, g, b) || pix_cyan_void(r, g, b)) n++;
    }
    return n;
}
static int panel_max_void_run(void) {
    int run = 0, mx = 0, x;
    for (x = 0; x < 240; x++) {
        if (panel_col_void(x) > 432 * 85 / 100) {
            run++;
            if (run > mx) mx = run;
        } else run = 0;
    }
    return mx;
}
static int neighborhood_drawn(int px, int py) {
    int x, y, n = 0, solid = 0;
    int rmin = 255, rmax = 0, gmin = 255, gmax = 0, bmin = 255, bmax = 0;
    for (y = py - 5; y <= py + 5; y++) {
        for (x = px - 5; x <= px + 5; x++) {
            unsigned r, g, b;
            if (x < 0 || x >= 240 || y < 0 || y >= 432) continue;
            r = be_fb[y][x][0]; g = be_fb[y][x][1]; b = be_fb[y][x][2];
            n++;
            if (r < rmin) rmin = (int)r; if (r > rmax) rmax = (int)r;
            if (g < gmin) gmin = (int)g; if (g > gmax) gmax = (int)g;
            if (b < bmin) bmin = (int)b; if (b > bmax) bmax = (int)b;
            if (!pix_skyish(r, g, b)) solid++;
        }
    }
    if (solid >= 4) return 1;
    if (n > 12 && (rmax - rmin > 45 || gmax - gmin > 45 || bmax - bmin > 45)) return 1;
    return 0;
}
static int panel_vis_drawn(const ab_phys_info *a, int *in_panel) {
    int i, drawn = 0, inside = 0;
    if (in_panel) *in_panel = 0;
    if (!a || a->vis_n < 1 || !(a->world_scale > 1e-4f)) return 0;
    for (i = 0; i < a->vis_n; i++) {
        float gx, gy, px, py;
        int ix, iy;
        gx = (a->vis_wx[i] - a->cam_left) * a->world_scale;
        gy = (a->vis_wy[i] - a->cam_top) * a->world_scale;
        px = fe_off_x + (fe_fb_h - gy) * fe_scale;
        py = fe_off_y + gx * fe_scale_y;
        ix = (int)(px + 0.5f);
        iy = (int)(py + 0.5f);
        if (ix < -8 || ix > 247 || iy < -8 || iy > 439) continue;
        if (ix < 0) ix = 0;
        if (ix > 239) ix = 239;
        if (iy < 0) iy = 0;
        if (iy > 431) iy = 431;
        inside++;
        if (neighborhood_drawn(ix, iy)) drawn++;
    }
    if (in_panel) *in_panel = inside;
    return drawn;
}
static int ppm_pixel_diff(const char *a, const char *b) {
    char pa[512], pb[512];
    FILE *fa, *fb;
    char ha[32], hb[32];
    int n = 0, i, na, nb;
    static unsigned char da[240 * 432 * 3], db[240 * 432 * 3];
    snprintf(pa, sizeof pa, "%s/%s.ppm", out_dir, a);
    snprintf(pb, sizeof pb, "%s/%s.ppm", out_dir, b);
    fa = fopen(pa, "rb"); fb = fopen(pb, "rb");
    if (!fa || !fb) { if (fa) fclose(fa); if (fb) fclose(fb); return -1; }
    if (!fgets(ha, sizeof ha, fa) || !fgets(hb, sizeof hb, fb)) {
        fclose(fa); fclose(fb); return -1;
    }
    na = (int)fread(da, 1, sizeof da, fa);
    nb = (int)fread(db, 1, sizeof db, fb);
    fclose(fa); fclose(fb);
    if (na != (int)sizeof da || nb != (int)sizeof db) return -1;
    for (i = 0; i < na; i += 3) {
        int dr = (int)da[i] - (int)db[i];
        int dg = (int)da[i + 1] - (int)db[i + 1];
        int dbb = (int)da[i + 2] - (int)db[i + 2];
        if (dr < 0) dr = -dr;
        if (dg < 0) dg = -dg;
        if (dbb < 0) dbb = -dbb;
        if (dr + dg + dbb > 18) n++;
    }
    return n;
}
#define AB_VOID_COL_MAX 12
#define AB_GROUND_MIN 400
#define AB_PAN_DIFF_MIN 800
static void dump_panel(const char *name);
static void world_to_panel(const ab_phys_info *a, float wx, float wy, int *ix, int *iy) {
    float gx, gy, px, py;
    gx = (wx - a->cam_left) * a->world_scale;
    gy = (wy - a->cam_top) * a->world_scale;
    px = fe_off_x + (fe_fb_h - gy) * fe_scale;
    py = fe_off_y + gx * fe_scale_y;
    *ix = (int)(px + 0.5f);
    *iy = (int)(py + 0.5f);
}
static int pix_pig_green(unsigned r, unsigned g, unsigned b) {
    /* Lime pig body from L1 dumps is (102,238,68). Grass is (85,170,0) /
     * (204,255,0) with b near 0; do not count the ground strip as a pig. */
    if (g < 170 || g > 250) return 0;
    if (r < 55 || r > 170) return 0;
    if (b < 24 || b > 130) return 0;
    if ((int)g < (int)r + 40) return 0;
    if ((int)g < (int)b + 70) return 0;
    return 1;
}
static int neighborhood_pig(int px, int py, int rad) {
    int x, y, n = 0;
    if (rad < 4) rad = 4;
    if (rad > 20) rad = 20;
    for (y = py - rad; y <= py + rad; y++) {
        for (x = px - rad; x <= px + rad; x++) {
            unsigned r, g, b;
            if (x < 28 || x >= 240 || y < 0 || y >= 432) continue;
            r = be_fb[y][x][0]; g = be_fb[y][x][1]; b = be_fb[y][x][2];
            if (pix_pig_green(r, g, b)) n++;
        }
    }
    return n;
}
static int pig_inside(int ix, int iy, int margin) {
    if (margin < 4) margin = 4;
    return ix >= margin && ix < 240 - margin && iy >= margin && iy < 432 - margin;
}
/* FAIL unless every live pig projects inside the panel with lime pig pixels
 * at that spot. Optionally require the slingshot birds and a structure block. */
static int panel_check_pigs(const char *tag, const ab_phys_info *a, int need_sling, int need_block) {
    int i, on = 0, green = 0, birds_on = 0, blocks_on = 0, bad = 0, margin;
    int need_g, rad;
    if (!a) return 0;
    margin = a->world_scale >= 0.45f ? 8 : 6;
    need_g = a->world_scale >= 0.45f ? 6 : 4;
    rad = a->world_scale >= 0.45f ? 22 : (int)(12.f + 40.f * a->world_scale);
    if (a->pig_n < 1) {
        fprintf(stderr, "AB_DEVICE_TEST %s pigs 0 found FAIL\n", tag);
        return 0;
    }
    for (i = 0; i < a->pig_n; i++) {
        int ix, iy, g;
        unsigned pr = 0, pg = 0, pb = 0;
        world_to_panel(a, a->pig_wx[i], a->pig_wy[i], &ix, &iy);
        if (ix >= 0 && ix < 240 && iy >= 0 && iy < 432) {
            pr = be_fb[iy][ix][0]; pg = be_fb[iy][ix][1]; pb = be_fb[iy][ix][2];
        }
        g = neighborhood_pig(ix, iy, rad);
        fprintf(stderr, "AB_DEVICE_TEST %s pig%d world=%.0f,%.0f panel=%d,%d rgb=%u,%u,%u lime=%d scale=%.3f cam=%.1f,%.1f\n",
                tag, i, (double)a->pig_wx[i], (double)a->pig_wy[i], ix, iy,
                pr, pg, pb, g, (double)a->world_scale, (double)a->cam_left,
                (double)a->cam_top);
        if (!pig_inside(ix, iy, margin)) continue;
        on++;
        if (g >= need_g) green++;
    }
    for (i = 0; i < a->bird_n; i++) {
        int ix, iy;
        world_to_panel(a, a->bird_wx[i], a->bird_wy[i], &ix, &iy);
        if (pig_inside(ix, iy, 4)) birds_on++;
    }
    for (i = 0; i < a->block_n; i++) {
        int ix, iy;
        world_to_panel(a, a->block_wx[i], a->block_wy[i], &ix, &iy);
        if (ix < 28 || ix >= 240 || iy < 4 || iy >= 428) continue;
        if (neighborhood_drawn(ix, iy)) blocks_on++;
    }
    {
        int y, x, any_lime = 0;
        for (y = 0; y < 432; y++)
            for (x = 28; x < 240; x++)
                if (pix_pig_green(be_fb[y][x][0], be_fb[y][x][1], be_fb[y][x][2]))
                    any_lime++;
        fprintf(stderr, "AB_DEVICE_TEST %s panel_lime=%d\n", tag, any_lime);
        if (any_lime < 8 && green < a->pig_n) bad = 1;
    }
    if (green < a->pig_n) bad = 1;
    if (on < a->pig_n) bad = 1;
    if (need_sling && birds_on < 1) bad = 1;
    if (need_block && blocks_on < 1) bad = 1;
    fprintf(stderr, "AB_DEVICE_TEST %s pigs %d/%d on=%d green=%d birds=%d/%d blocks=%d draws=%d ingame=%d/%d castle_img=%d %s\n",
            tag, green, a->pig_n, on, green, birds_on, a->bird_n, blocks_on,
            fe_stat_draws, fe_stat_ingame_ok, fe_stat_ingame_ok + fe_stat_ingame_clip, fe_stat_castle_img,
            bad ? "FAIL" : "ok");
    return !bad;
}
/* World-x span of pigs+birds in panel pixels (portrait y). The original
 * iPhone also needed a pan when this exceeded the 432-tall view. */
static float pinch_out_panel_span(const ab_phys_info *a) {
    float minx, maxx, gx;
    int i, any = 0;
    if (!a) return 0.f;
    minx = maxx = 0.f;
    for (i = 0; i < a->pig_n; i++) {
        if (!any) { minx = maxx = a->pig_wx[i]; any = 1; }
        if (a->pig_wx[i] < minx) minx = a->pig_wx[i];
        if (a->pig_wx[i] > maxx) maxx = a->pig_wx[i];
    }
    for (i = 0; i < a->bird_n; i++) {
        if (!any) { minx = maxx = a->bird_wx[i]; any = 1; }
        if (a->bird_wx[i] < minx) minx = a->bird_wx[i];
        if (a->bird_wx[i] > maxx) maxx = a->bird_wx[i];
    }
    if (!any) return 0.f;
    gx = (maxx - minx) * a->world_scale;
    return gx * (fe_scale_y > 0.1f ? fe_scale_y : 0.9f);
}
/* Pinch-out at minWorldScale: pass when pan already showed every pig and
 * either every pig still projects on the panel (lime is just too small) or
 * the castle/slingshot span is wider than the view (original needed pan). */
static int pinch_out_pigs_ok(const char *tag, const ab_phys_info *a, int pan_ok) {
    int i, on = 0;
    float span;
    if (panel_check_pigs(tag, a, 1, 1)) return 1;
    if (!pan_ok || !a || a->pig_n < 1) return 0;
    for (i = 0; i < a->pig_n; i++) {
        int ix, iy;
        world_to_panel(a, a->pig_wx[i], a->pig_wy[i], &ix, &iy);
        if (pig_inside(ix, iy, 6)) on++;
    }
    span = pinch_out_panel_span(a);
    if (on >= a->pig_n || span > 432.f - 24.f) {
        fprintf(stderr, "AB_DEVICE_TEST %s pinch-out needs pan (on=%d/%d span=%.0f) ok\n",
                tag, on, a->pig_n, (double)span);
        return 1;
    }
    return 0;
}
static int panel_check(const char *tag, const ab_phys_info *a, const char *diff_from) {
    int ground, void_run, drawn, inside = 0, diff = -1, bad = 0;
    ground = panel_ground_pixels();
    void_run = panel_max_void_run();
    drawn = panel_vis_drawn(a, &inside);
    if (diff_from) diff = ppm_pixel_diff(diff_from, tag);
    if (a && a->world_scale < 0.35f) {
        if (ground < 80 && !(drawn >= 8 && tag && strstr(tag, "-out")))
            bad = 1;
    } else if (ground < AB_GROUND_MIN) {
        /* Castle pan can leave the grass strip off the portrait-left
         * (L2-pan pigs 2/2, ground=0). Still require some fill at opening. */
        if (!(tag && strstr(tag, "-pan") && drawn >= 8))
            bad = 1;
    }
    if (void_run > AB_VOID_COL_MAX) bad = 1;
    if (inside >= 1 && drawn < 1) bad = 1;
    if (inside >= 4 && drawn * 3 < inside * 2) bad = 1;
    if (diff_from && (diff < 0 || diff < AB_PAN_DIFF_MIN)) bad = 1;
    fprintf(stderr, "AB_DEVICE_TEST %s pixels ground=%d void_col=%d vis_drawn=%d/%d diff=%d %s\n",
            tag, ground, void_run, drawn, inside, diff, bad ? "FAIL" : "ok");
    return !bad;
}
static int dump_and_check(const char *name, const ab_phys_info *a, const char *diff_from) {
    dump_panel(name);
    return panel_check(name, a, diff_from);
}
static const char *const host_lowmem_lines[] = {
    "ANGRY BIRDS", "NOT ENOUGH MEMORY", "CHECK LOG.TXT",
    "PAUSE MUSIC, QUIT,", "THEN OPEN IT AGAIN"
};
static void host_draw_lowmem(void) {
    notice_fill_rgb(&be_fb[0][0][0], 240, 432, 4, 240 * 4, host_lowmem_lines, 5);
}
static int host_panel_is_red(void) {
    int n = 0, y, x;
    for (y = 0; y < 432; y++) for (x = 0; x < 240; x++) {
        unsigned r = be_fb[y][x][0], g = be_fb[y][x][1], b = be_fb[y][x][2];
        if (r > 50 && g < 40 && b < 40) n++;
    }
    return n > 240 * 432 / 2;
}
static void dump_panel(const char *name) {
    char p[512]; snprintf(p, sizeof p, "%s/%s.ppm", out_dir, name);
    FILE *f;
    if (fe_lowmem) host_draw_lowmem();
    f = fopen(p, "wb"); if (!f) return;
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
    for (f = 0; f < n; f++) {
        fake_us += 33333;
        if (fe_lowmem) { host_draw_lowmem(); continue; }
        ab_frame(1.f / 30.f);
        if (fe_lowmem) host_draw_lowmem();
    }
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
    if (a->world_scale < 0.12f || a->world_scale > 1.05f) bad = 1;
    if (blk > 240 * 432 / 12) bad = 1;
    /* Device white L2 is ~93% near-white with one untextured wood strip
     * (pix under 8000). Playable levels can have light sky over 90% wash
     * and still show 40k+ game pixels. Fully zoomed out (minWorldScale)
     * is mostly sky; require bodies in view instead of fill. */
    if (a->world_scale >= 0.35f) {
        if (pix < 8000) bad = 1;
        if (panel_washed() > 240 * 432 * 9 / 10 && pix < 20000) bad = 1;
    } else if (a->vis_all < 5) {
        bad = 1;
    }
    if (be_stat_tex_fail) bad = 1;
    if (a->shape_bad) bad = 1;
    fprintf(stderr, "AB_GESTURE_TEST %s dyn=%d vis=%d/%d scale=%.3f pix=%d black=%d wash=%d tex=%d fail=%d circ=%d poly=%d shapebad=%d %s\n",
            name, a->dynamic_n, a->vis_dyn, a->vis_all, (double)a->world_scale,
            pix, blk, panel_washed(), fe_stat_tex_count, be_stat_tex_fail,
            a->circles, a->polygons, a->shape_bad, bad ? "FAIL" : "ok");
    return !bad;
}
static void pinch_spread(int frames, float from, float to) {
    int i;
    ab_touch(0, 1, 120.f - from, 216.f);
    ab_touch(1, 1, 120.f + from, 216.f);
    fake_us += 33333; ab_frame(1.f / 30.f);
    fake_us += 33333; ab_frame(1.f / 30.f);
    for (i = 1; i <= frames; i++) {
        float k = (float)i / (float)frames;
        float h = from + (to - from) * k;
        ab_touch(0, 1, 120.f - h, 216.f);
        ab_touch(1, 1, 120.f + h, 216.f);
        fake_us += 33333; ab_frame(1.f / 30.f);
    }
    for (i = 0; i < 8; i++) { fake_us += 33333; ab_frame(1.f / 30.f); }
    ab_touch(0, 0, 120.f - to, 216.f);
    ab_touch(1, 0, 120.f + to, 216.f);
    fake_us += 33333; ab_frame(1.f / 30.f);
}
static void pinch_out(int frames) { pinch_spread(frames, 16.f, 110.f); }
static void pinch_in(int frames) { pinch_spread(frames, 110.f, 24.f); }
static void pan_drag(int frames, float x0, float y0, float x1, float y1) {
    int i;
    ab_touch(0, 1, x0, y0);
    fake_us += 33333; ab_frame(1.f / 30.f);
    for (i = 1; i <= frames; i++) {
        float k = (float)i / (float)frames;
        ab_touch(0, 1, x0 + (x1 - x0) * k, y0 + (y1 - y0) * k);
        fake_us += 33333; ab_frame(1.f / 30.f);
    }
    ab_touch(0, 0, x1, y1);
    fake_us += 33333; ab_frame(1.f / 30.f);
    wait_frames(8);
}
static void print_cam(const char *tag, const ab_phys_info *a) {
    fprintf(stderr, "AB_DEVICE_TEST %s scale=%.3f cam_left=%.1f cam_top=%.1f vis=%d/%d pigs=%d birds=%d blocks=%d heap=%u hole=%u texfail=%d\n",
            tag, (double)a->world_scale, (double)a->cam_left, (double)a->cam_top,
            a->vis_all, a->bodies, a->pig_n, a->bird_n, a->block_n,
            rt_heap_used(), rt_heap_largest_free(), be_stat_tex_fail);
}
static void pan_to_right_limit(ab_phys_info *a) {
    float last;
    int k, pigs_on, moved;
    if (!a) return;
    ab_phys_snapshot(a);
    last = a->cam_left;
    for (k = 0; k < 16; k++) {
        int i, on = 0;
        /* panel y is game x. Smaller py raises cam_left (toward the pigs). */
        pan_drag(20, 160.f, 410.f, 160.f, 16.f);
        ab_phys_snapshot(a);
        on = 0;
        for (i = 0; i < a->pig_n; i++) {
            int ix, iy;
            world_to_panel(a, a->pig_wx[i], a->pig_wy[i], &ix, &iy);
            if (pig_inside(ix, iy, 8)) on++;
        }
        pigs_on = on;
        moved = a->cam_left > last + 3.f;
        if (pigs_on >= a->pig_n && a->pig_n > 0 && !moved)
            break;
        if (!moved && k >= 1) break;
        last = a->cam_left;
    }
}
static void pan_to_left_limit(ab_phys_info *a) {
    float last;
    int k;
    if (!a) return;
    ab_phys_snapshot(a);
    last = a->cam_left;
    for (k = 0; k < 8; k++) {
        pan_drag(12, 120.f, 36.f, 120.f, 380.f);
        ab_phys_snapshot(a);
        if (!(a->cam_left < last - 6.f)) break;
        last = a->cam_left;
    }
}
static void double_tap(float x, float y) {
    tap_at(x, y);
    fake_us += 80000; ab_frame(1.f / 30.f);
    tap_at(x, y);
    wait_frames(12);
}
static int level_lua_exists(int n) {
    char file[24];
    snprintf(file, sizeof file, "Level%d.lua", n);
    return plat_file_size(file) >= 0;
}
static int level_wanted(int n) {
    const char *s = getenv("AB_GESTURE_LEVELS");
    char wrap[96], needle[12];
    if (!s || !s[0]) return 1;
    snprintf(wrap, sizeof wrap, ",%s,", s);
    snprintf(needle, sizeof needle, ",%d,", n);
    return strstr(wrap, needle) != NULL;
}
static void dismiss_tutorial(void) {
    int i;
    ab_ready_slingshot();
    ab_unpause();
    /* TUTORIAL_OK is a 60x63 sprite on the comic; L1 used ~50,324 and later
     * chapters put the check on the portrait-left of the yellow frame. */
    for (i = 0; i < 3; i++) {
        tap_at(49.f, 324.f);
        tap_at(55.f, 333.f);
        tap_at(40.f, 390.f);
        tap_at(187.f, 368.f);
        tap_at(120.f, 300.f);
        tap_at(58.f, 292.f);
    }
    wait_frames(8);
}
static int dump_pause_screen(void) {
    int pix;
    tap_at(215.f, 24.f);
    wait_frames(8);
    fe_drop_fullscreen = 1;
    wait_frames(1);
    dump_panel("panel-pause");
    pix = play_pixels();
    fprintf(stderr, "AB_GESTURE_TEST pause pix=%d\n", pix);
    fe_drop_fullscreen = 0;
    tap_at(215.f, 24.f);
    wait_frames(8);
    return pix < 800;
}
/* Opening dump, swipe to pigs, pinch-out (whole level), pan, pinch-in. */
static int play_level_camera(const char *dump, ab_phys_info *a) {
    ab_phys_info b;
    float s0, s1, s2, left0, left1;
    int bad = 0;
    ab_unpause();
    wait_frames(2);
    ab_call_lua("hidePauseMenu");
    wait_frames(2);
    ab_unpause();
    dismiss_tutorial();
    tap_at(62.f, 318.f);
    tap_at(58.f, 292.f);
    wait_frames(4);
    char pan_n[32], out_n[32], back_n[32], in_n[32];
    snprintf(pan_n, sizeof pan_n, "%s-pan", dump);
    snprintf(out_n, sizeof out_n, "%s-out", dump);
    snprintf(back_n, sizeof back_n, "%s-pan-back", dump);
    snprintf(in_n, sizeof in_n, "%s-in", dump);
    print_cam(dump, a);
    s0 = a->world_scale;
    left0 = a->cam_left;
    pan_to_right_limit(&b);
    if (!dump_and_check(pan_n, &b, dump)) bad++;
    int pan_ok = panel_check_pigs(pan_n, &b, 0, 1);
    if (!pan_ok) bad++;
    print_cam("pan-pigs", &b);
    if (!(b.cam_left > left0 + 25.f)) {
        /* Opening already framed the pigs (short levels): still must show them. */
        if (b.pig_n < 1) bad++;
    }
    left1 = b.cam_left;
    pan_to_left_limit(&b);
    dump_panel(back_n);
    print_cam("pan-back", &b);
    if (!(b.cam_left < left1 - 20.f) && left1 > left0 + 25.f) bad++;
    pinch_out(22);
    wait_frames(8);
    ab_phys_snapshot(&b);
    s1 = b.world_scale;
    if (!dump_and_check(out_n, &b, pan_n)) bad++;
    if (!pinch_out_pigs_ok(out_n, &b, pan_ok)) bad++;
    print_cam("pinch-out", &b);
    if (!(s1 < s0 - 0.03f)) bad++;
    left0 = b.cam_left;
    pan_drag(18, 120.f, 360.f, 120.f, 50.f);
    ab_phys_snapshot(&b);
    print_cam("pinch-pan", &b);
    pinch_in(16);
    wait_frames(8);
    ab_phys_snapshot(&b);
    s2 = b.world_scale;
    dump_panel(in_n);
    print_cam("pinch-in", &b);
    if (!(s2 > s1 + 0.02f)) bad++;
    pan_to_left_limit(&b);
    ab_phys_snapshot(&b);
    print_cam("pinch-in-back", &b);
    return !bad;
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
    {
        char p[512];
        snprintf(p, sizeof p, "%s/docs", out_dir);
        mkdir(p, 0755);
        if (getenv("AB_BAD_SAVE")) {
            FILE *sf;
            snprintf(p, sizeof p, "%s/docs/highscores.lua", out_dir);
            sf = fopen(p, "wb");
            if (sf) { fwrite("\xff\x00{{{{not lua", 1, 16, sf); fclose(sf); }
            fprintf(stderr, "AB_BAD_SAVE wrote garbage highscores.lua\n");
        }
    }
    /* Same guest-block arithmetic as nano/fn_nano.c: largest 2 MB -> size =
     * (largest-0x10000)&~0xFFFF = 2031616 on the low profile. Charged to the
     * device-like OS heap so convert/decode compete with it. AB_GUEST_BLOCK
     * overrides. Too little OS memory: red CHECK LOG.TXT screen, never start. */
    {
        uint32_t largest, free0, size;
        void *block;
        os_init();
        largest = plat_os_largest();
        free0 = plat_os_free_total();
        size = largest > AB_BLOCK_MAX + 0x10000u ? AB_BLOCK_MAX : (largest - 0x10000u) & ~0xFFFFu;
        if (getenv("AB_GUEST_BLOCK")) size = (uint32_t)strtoul(getenv("AB_GUEST_BLOCK"), 0, 0);
        fprintf(stderr, "nano7kit Angry Birds host: profile=%s heap free %u, largest %u\n",
                os_profile_name(), (unsigned)free0, (unsigned)largest);
        if (largest < AB_BLOCK_MIN + 0x10000u || size < AB_BLOCK_MIN || free0 < size + AB_AFTER_MIN) {
            char why[160];
            snprintf(why, sizeof why,
                     "heap free %u largest %u, needs %u and %u (music or bluetooth?)",
                     (unsigned)free0, (unsigned)largest,
                     (unsigned)(AB_BLOCK_MAX + AB_AFTER_MIN),
                     (unsigned)(AB_BLOCK_MIN + 0x10000u));
            fe_enter_lowmem(why);
            host_draw_lowmem();
            dump_panel("panel-lowmem");
            fprintf(stderr, "AB_LOWMEM_TEST red=%d profile=%s why=%s\n",
                    host_panel_is_red(), os_profile_name(), why);
            if (getenv("AB_LOWMEM_TEST") || os_profile == 2) {
                fprintf(stderr, "AB_LOWMEM_TEST %s\n", host_panel_is_red() ? "PASS" : "FAIL");
                if (getenv("AB_DEVICE_TEST"))
                    fprintf(stderr, "AB_DEVICE_TEST %s profile=%s (red screen)\n",
                            host_panel_is_red() ? "PASS" : "FAIL", os_profile_name());
                return host_panel_is_red() ? 0 : 1;
            }
            fprintf(stderr, "AB_DEVICE_TEST FAIL profile=%s (pre-flight refused a playable profile)\n",
                    os_profile_name());
            return 1;
        }
        block = plat_os_alloc(size);
        if (!block) { fprintf(stderr, "host block %u failed (os largest %u free %u)\n",
                              size, plat_os_largest(), plat_os_free_total()); return 1; }
        fprintf(stderr, "nano7kit guest block %u bytes; heap free after %u largest %u\n",
                (unsigned)size, (unsigned)plat_os_free_total(), (unsigned)plat_os_largest());
        if (rt_init(block, size)) return 1;
        if (ab_start()) return 1;
        if (fe_lowmem) {
            host_draw_lowmem();
            dump_panel("panel-lowmem");
            fprintf(stderr, "AB_LOWMEM_TEST red=%d profile=%s (texture convert)\n",
                    host_panel_is_red(), os_profile_name());
            if (getenv("AB_REPRO_UNINIT")) {
                /* Keep going: the repro path must still draw rainbow. */
            } else if (getenv("AB_LOWMEM_TEST")) {
                fprintf(stderr, "AB_LOWMEM_TEST %s\n", host_panel_is_red() ? "PASS" : "FAIL");
                return host_panel_is_red() ? 0 : 1;
            } else if (getenv("AB_DEVICE_TEST")) {
                fprintf(stderr, "AB_DEVICE_TEST FAIL profile=%s (texture convert OOM, red screen)\n",
                        os_profile_name());
                return 1;
            }
        }
        fprintf(stderr, "init ok: guest block %u heap %u used %u peak %u os free %u largest %u\n",
                size, rt_heap_size(), rt_heap_used(), rt_heap_peak(),
                plat_os_free_total(), plat_os_largest());
    }
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
        /* Fresh boot -> menu -> one chapter-1 level, no sequential leftover. */
        if (getenv("AB_ISOLATE_LEVEL")) {
            int n = atoi(getenv("AB_ISOLATE_LEVEL"));
            char lname[16], dump[24];
            dump_panel("panel-splash");
            tap_at(105.f, 216.f);
            wait_frames(45);
            dump_panel("panel-menu");
            dump_panel("panel-select");
            if (n < 1) n = 1;
            snprintf(lname, sizeof lname, "Level%d", n);
            snprintf(dump, sizeof dump, "panel-l%d", n); /* panel-l1 panel-l2 ... Level8 */
            if (!level_lua_exists(n)) {
                fprintf(stderr, "AB_ISOLATE_TEST L%d missing FAIL\n", n);
                return 1;
            }
            /* gotoFirstLevel inits lua cameras; then replace L1 with N so
             * this is still a fresh boot of one level, not a sequential run. */
            ab_call_lua("gotoFirstLevel");
            wait_frames(20);
            for (i = 0; i < 8; i++) {
                tap_at(49.f, 324.f);
                tap_at(55.f, 333.f);
            }
            wait_frames(8);
            if (getenv("AB_WARM_LEVEL")) {
                char warm[16];
                snprintf(warm, sizeof warm, "Level%s", getenv("AB_WARM_LEVEL"));
                ab_load_lua_level(warm);
                wait_frames(90);
                ab_unpause();
                dismiss_tutorial();
                wait_frames(4);
            }
            ab_load_lua_level(lname);
            wait_frames(90);
            ab_unpause();
            dismiss_tutorial();
            wait_frames(8);
            if (!level_sane(lname, &a)) fail++;
            if (!dump_and_check(dump, &a, NULL)) fail++;
            if (!play_level_camera(dump, &a)) fail++;
            fprintf(stderr, "AB_ISOLATE_TEST L%d pigs %d texfail=%d %s\n",
                    n, a.pig_n, be_stat_tex_fail, fail ? "FAIL" : "PASS");
            fprintf(stderr, "AB_GESTURE_TEST %s texfail=%d\n", fail ? "FAIL" : "PASS", be_stat_tex_fail);
            return fail ? 1 : 0;
        }
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
        if (!dump_and_check("panel-l1", &a, NULL)) fail++;
        if (level_wanted(1) && !play_level_camera("panel-l1", &a)) fail++;
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
        if (dump_pause_screen()) fail++;
        {
            int n;
            /* Chapter 1 dumps: Level8 / Level21 (Level11 and Level19 are absent). */
            for (n = 2; n <= 21; n++) {
                char lname[16], dump[24];
                if (!level_lua_exists(n) || !level_wanted(n)) continue;
                snprintf(lname, sizeof lname, "Level%d", n);
                snprintf(dump, sizeof dump, "panel-l%d", n); /* panel-l1 panel-l2 ... Level8 */
                ab_load_lua_level(lname);
                wait_frames(90);
                ab_unpause();
                dismiss_tutorial();
                wait_frames(8);
                if (!level_sane(lname, &a)) fail++;
                if (!dump_and_check(dump, &a, NULL)) fail++;
                scale0 = a.world_scale;
                if (!play_level_camera(dump, &a)) fail++;
                ab_phys_snapshot(&b);
                scale1 = b.world_scale;
                fprintf(stderr, "AB_GESTURE_TEST pinch L%d scale %.3f -> %.3f vis %d/%d texfail=%d\n",
                        n, (double)scale0, (double)scale1, b.vis_all, b.bodies, be_stat_tex_fail);
                if (n == 2) {
                    if (scale1 < 0.15f || scale1 > 1.05f) fail++;
                    if (!level_sane("Level2-pinch", &b)) fail++;
                    if (dump_pause_screen()) fail++;
                }
            }
        }
        fprintf(stderr, "AB_GESTURE_TEST %s texfail=%d\n", fail ? "FAIL" : "PASS", be_stat_tex_fail);
        if (rt_malloc_failures()) {
            fprintf(stderr, "FATAL: %u guest allocations failed\n", rt_malloc_failures());
            return 3;
        }
        return fail ? 1 : 0;
    }
    if (getenv("AB_DEVICE_TEST")) {
        /* iPod path: splash, PLAY, Level 1 from the map, then the map-select
         * load (levelName + updateLoading) for Level2 / Level3. loadNextLevel
         * follows lua levelOrder (Level1, Level57, ...) which is not 1-2-3.
         * Pinch out then in on each level. L1 again, then chapter 1. */
        int fail = 0, i, n, ch1_fail = 0;
        ab_phys_info a, b;
        float s0;
        fprintf(stderr, "AB_DEVICE_TEST profile=%s\n", os_profile_name());
        wait_frames(200);
        dump_panel("panel-splash");
        tap_at(105.f, 216.f);
        wait_frames(45);
        dump_panel("panel-menu");
        tap_at(151.f, 32.f);
        wait_frames(12);
        dump_panel("panel-select");
        wait_frames(8);
        for (i = 0; i < 600; i++) {
            fake_us += 33333; ab_frame(1.f / 30.f);
            ab_phys_snapshot(&a);
            if (a.dynamic_n > 0) break;
        }
        for (i = 0; i < 8; i++) { tap_at(49.f, 324.f); tap_at(55.f, 333.f); }
        wait_frames(40);
        if (!level_sane("map-L1", &a)) fail++;
        if (!dump_and_check("panel-l1", &a, NULL)) fail++;
        s0 = a.world_scale;
        if (!play_level_camera("panel-l1", &a)) fail++;
        double_tap(120.f, 200.f);
        ab_phys_snapshot(&b);
        print_cam("L1-double-tap", &b);
        fprintf(stderr, "AB_DEVICE_TEST L1 double-tap scale %.3f (play was %.3f)\n",
                (double)b.world_scale, (double)s0);
        if (b.world_scale < s0 - 0.12f || b.world_scale > s0 + 0.12f) fail++;
        {
        int ch1_max = getenv("AB_DEVICE_CH1") ? atoi(getenv("AB_DEVICE_CH1")) : 21;
        for (n = 2; n <= ch1_max; n++) {
            char tag[24], dump[24], lname[16];
            if (!level_lua_exists(n) || !level_wanted(n)) continue;
            snprintf(lname, sizeof lname, "Level%d", n);
            snprintf(dump, sizeof dump, "panel-l%d", n);
            ab_goto_level(lname);
            wait_frames(90);
            ab_unpause();
            dismiss_tutorial();
            wait_frames(8);
            snprintf(tag, sizeof tag, "ch1-L%d", n);
            if (!level_sane(tag, &a)) {
                char fail_dump[32];
                snprintf(fail_dump, sizeof fail_dump, "panel-ch1-l%d-fail", n);
                dump_panel(fail_dump);
                fail++; ch1_fail++;
            }
            if (!dump_and_check(dump, &a, NULL)) fail++;
            if (!play_level_camera(dump, &a)) fail++;
            if (n == 2) {
                tap_at(215.f, 24.f);
                wait_frames(8);
                dump_panel("panel-pause");
                tap_at(215.f, 24.f);
                wait_frames(8);
            }
            ab_phys_snapshot(&b);
            if (!(b.world_scale > 0.12f && b.world_scale < 1.06f)) fail++;
        }
        }
        plat_log_flush();
        {
            char p[512];
            FILE *lf;
            char buf[512];
            int saw_l1 = 0, saw_level = 0, saw_step = 0, saw_pre = 0;
            snprintf(p, sizeof p, "%s/docs/log.txt", out_dir);
            lf = fopen(p, "rb");
            if (!lf) fail++;
            else {
                while (fgets(buf, sizeof buf, lf)) {
                    if (strstr(buf, "LEVEL Level1")) saw_l1 = 1;
                    if (!strncmp(buf, "LEVEL Level", 11)) saw_level++;
                    if (!strncmp(buf, "LEVEL-PRE ", 10)) saw_pre = 1;
                    if (!strncmp(buf, "step ", 5)) saw_step = 1;
                }
                fclose(lf);
            }
            snprintf(p, sizeof p, "%s/docs/sync.txt", out_dir);
            lf = fopen(p, "rb");
            if (!lf) fail++;
            else fclose(lf);
            fprintf(stderr, "AB_DEVICE_TEST log LEVEL L1=%d n=%d pre=%d step=%d\n",
                    saw_l1, saw_level, saw_pre, saw_step);
            if (!saw_l1 || saw_level < 2 || !saw_pre || !saw_step) fail++;
        }
        fprintf(stderr, "AB_DEVICE_TEST chapter1 extra fails %d heap %u hole %u texfail %d align %u shapebad last=%d\n",
                ch1_fail, rt_heap_used(), rt_heap_largest_free(), be_stat_tex_fail,
                rt_align_faults(), a.shape_bad);
        if (rt_heap_largest_free() < 300000u) fail++;
        if (be_stat_tex_fail) fail++;
        fprintf(stderr, "AB_DEVICE_TEST %s profile=%s\n", fail ? "FAIL" : "PASS", os_profile_name());
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
