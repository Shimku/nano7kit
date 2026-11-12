#include "../gd_config.h"
#include "gdalloc.h"
#include "gd_ui_font.h"
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

static char s_dir[256] = GD_DATA_DIR;
static uint32_t *s_fb;
static int s_fbw, s_fbh;
static int s_want_home;
static char s_log[8192];
static unsigned s_logn;
static int s_gas, s_brake, s_lean_l, s_lean_r;
static unsigned s_keys;
static int s_progress16;
static int s_fire, s_back, s_pause;
static int s_tapx = -1, s_tapy = -1;

#ifdef GD_HOST
static uint32_t s_host_ms;
static char s_host_root[512];
void gd_host_set_root(const char *p)
{
    snprintf(s_host_root, sizeof s_host_root, "%s", p ? p : ".");
}
void gd_host_advance_ms(uint32_t d) { s_host_ms += d; }
uint32_t gd_host_ms(void) { return s_host_ms; }
void gd_host_set_buttons(int gas, int brake, int ll, int lr)
{
    gd_set_held(gas, brake, ll, lr);
}
void gd_host_tap(int fire, int back, int pause, int x, int y)
{
    s_fire = fire; s_back = back; s_pause = pause; s_tapx = x; s_tapy = y;
}
const char *gd_host_log(void) { return s_log; }
void gd_host_clear_home(void) { s_want_home = 0; }
#endif

void gd_set_data_dir(const char *dir)
{
    if (dir && dir[0]) snprintf(s_dir, sizeof s_dir, "%s", dir);
}

const char *gd_data_dir(void) { return s_dir; }

void gd_set_fb(uint32_t *fb, int w, int h)
{
    s_fb = fb; s_fbw = w; s_fbh = h;
}

uint32_t *gd_fb(void) { return s_fb; }
int gd_fb_w(void) { return s_fbw; }
int gd_fb_h(void) { return s_fbh; }

int gd_want_home(void) { return s_want_home; }
void gd_request_home(const char *why)
{
    s_want_home = 1;
    gd_log("quit: %s", why ? why : "home");
    gd_log_flush();
}

int gd_held_gas(void) { return s_gas; }
int gd_held_brake(void) { return s_brake; }
int gd_held_lean_l(void) { return s_lean_l; }
int gd_held_lean_r(void) { return s_lean_r; }
unsigned gd_held_keys(void) { return s_keys; }
int gd_tap_fire(void) { return s_fire; }
int gd_tap_back(void) { return s_back; }
int gd_tap_pause(void) { return s_pause; }
int gd_menu_tap_x(void) { return s_tapx; }
int gd_menu_tap_y(void) { return s_tapy; }

void gd_input_reset_edges(void)
{
    s_fire = s_back = s_pause = 0;
    s_tapx = s_tapy = -1;
}

void gd_set_key_mask(unsigned mask)
{
    s_keys = mask & 0x3FEu;
    s_gas = !!(s_keys & ((1u << 1) | (1u << 2) | (1u << 3)));
    s_brake = !!(s_keys & ((1u << 5) | (1u << 7) | (1u << 8) | (1u << 9)));
    s_lean_l = !!(s_keys & ((1u << 1) | (1u << 4) | (1u << 7)));
    s_lean_r = !!(s_keys & ((1u << 3) | (1u << 6) | (1u << 9)));
}

void gd_set_held(int gas, int brake, int ll, int lr)
{
    unsigned m = 0;
    if (gas) m |= 1u << 2;
    if (brake) m |= 1u << 8;
    if (ll) m |= 1u << 4;
    if (lr) m |= 1u << 6;
    gd_set_key_mask(m);
}

void gd_set_edges(int fire, int back, int pause, int x, int y)
{
    s_fire = fire;
    s_back = back;
    s_pause = pause;
    s_tapx = x;
    s_tapy = y;
}

void gd_set_progress(int p16)
{
    s_progress16 = p16;
}

int gd_pad_digit(int x, int y)
{
    int col, row;
    if (y < GD_PAD_Y || y >= GD_SCREEN_H || x < 0 || x >= GD_SCREEN_W) return 0;
    col = x / GD_PAD_CELL_W;
    row = (y - GD_PAD_Y) / GD_PAD_CELL_H;
    if (col < 0 || col > 2 || row < 0 || row > 2) return 0;
    return row * 3 + col + 1;
}

int gd_hit_pause(int x, int y)
{
    return x >= GD_PAUSE_X && x < GD_SCREEN_W && y >= GD_PAUSE_Y && y < GD_PAUSE_Y + GD_PAUSE_H;
}

int gd_bar_zone(int x)
{
    if (x < 0) return 0;
    if (x >= GD_SCREEN_W) return 2;
    return x / GD_PAD_CELL_W;
}

void gd_draw_touch_bar(uint32_t *fb, int w, int h)
{
    const uint32_t white = 0xffffffffu, grey = 0xffd8d8d8u, line = 0xffc8c8c8u;
    const uint32_t pressed = 0xffd0d0d0u, ink = 0xff2a2a2au, green = 0xff00cc00u;
    int d, row, col, x, y, x0, y0, tw, fill;
    char lab[2];
    if (!fb || w < GD_SCREEN_W || h < GD_PAD_Y) return;

    y0 = GD_GAME_H;
    if (y0 < 0) y0 = 0;
    for (y = y0; y < GD_PAD_Y && y < h; y++) {
        uint32_t *p = fb + (long)y * w;
        for (x = 0; x < w && x < GD_SCREEN_W; x++) p[x] = grey;
    }
    fill = (int)(((int64_t)GD_SCREEN_W * (int64_t)(s_progress16 < 0 ? 0 : s_progress16)) >> 16);
    if (fill > GD_SCREEN_W) fill = GD_SCREEN_W;
    if (fill < 0) fill = 0;
    for (y = y0; y < GD_PAD_Y && y < h; y++) {
        uint32_t *p = fb + (long)y * w;
        for (x = 0; x < fill; x++) p[x] = green;
    }

    for (d = 1; d <= 9; d++) {
        row = (d - 1) / 3;
        col = (d - 1) % 3;
        x0 = col * GD_PAD_CELL_W;
        y0 = GD_PAD_Y + row * GD_PAD_CELL_H;
        for (y = y0; y < y0 + GD_PAD_CELL_H && y < h; y++) {
            uint32_t *p = fb + (long)y * w;
            uint32_t c = (s_keys & (1u << d)) ? pressed : white;
            for (x = x0; x < x0 + GD_PAD_CELL_W && x < w; x++) p[x] = c;
        }
        lab[0] = (char)('0' + d);
        lab[1] = 0;
        tw = gd_ui_text_width(22, lab, 1);
        gd_ui_draw_text(fb, w, h,
                        x0 + (GD_PAD_CELL_W - tw) / 2,
                        y0 + (GD_PAD_CELL_H - gd_ui_font_height(22)) / 2,
                        lab, 1, 22, ink, x0, y0, GD_PAD_CELL_W, GD_PAD_CELL_H);
    }
    for (col = 1; col < 3; col++) {
        x = col * GD_PAD_CELL_W;
        if (x >= w) break;
        for (y = GD_PAD_Y; y < h && y < GD_SCREEN_H; y++) fb[(long)y * w + x] = line;
    }
    for (row = 1; row < 3; row++) {
        y = GD_PAD_Y + row * GD_PAD_CELL_H;
        if (y >= h) break;
        for (x = 0; x < w && x < GD_SCREEN_W; x++) fb[(long)y * w + x] = line;
    }
}

void gd_log(const char *fmt, ...)
{
    char line[240];
    va_list ap;
    int n;
    va_start(ap, fmt);
    n = vsnprintf(line, sizeof line, fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if ((unsigned)n > sizeof line - 2) n = (int)sizeof line - 2;
    line[n++] = '\n';
    line[n] = 0;
    if (s_logn + (unsigned)n + 1 >= sizeof s_log) {
        gd_log_flush();
        s_logn = 0;
        s_log[0] = 0;
    }
    memcpy(s_log + s_logn, line, (unsigned)n + 1);
    s_logn += (unsigned)n;
#ifdef GD_HOST
    fputs(line, stderr);
#endif
}

#ifdef GD_HOST
#include <stdlib.h>
#include <sys/stat.h>
#include <dirent.h>
#include <unistd.h>

static void host_path(char *out, unsigned n, const char *rel)
{
    if (rel && rel[0] == '/') {
        snprintf(out, n, "%s", rel);
        return;
    }
    if (s_dir[0] == '/') {
        snprintf(out, n, "%s/%s", s_dir, rel ? rel : "");
        return;
    }
    snprintf(out, n, "%s/%s/%s", s_host_root[0] ? s_host_root : ".",
             s_dir, rel ? rel : "");
}

uint32_t gd_millis(void) { return s_host_ms; }
void gd_sleep_ms(int ms) { (void)ms; }

int gd_fs_read(const char *rel, void *buf, unsigned max)
{
    char p[640];
    FILE *f;
    size_t n;
    host_path(p, sizeof p, rel);
    f = fopen(p, "rb");
    if (!f) return 0;
    n = fread(buf, 1, max, f);
    fclose(f);
    return (int)n;
}

int gd_fs_write(const char *rel, const void *buf, unsigned n)
{
    char p[640];
    FILE *f;
    char dir[640];
    char *slash;
    host_path(p, sizeof p, rel);
    snprintf(dir, sizeof dir, "%s", p);
    slash = strrchr(dir, '/');
    if (slash) {
        *slash = 0;
        mkdir(dir, 0777);
    }
    f = fopen(p, "wb");
    if (!f) return 0;
    if (fwrite(buf, 1, n, f) != n) { fclose(f); return 0; }
    fclose(f);
    return 1;
}

int gd_fs_exists(const char *rel)
{
    char p[640];
    struct stat st;
    host_path(p, sizeof p, rel);
    return stat(p, &st) == 0;
}

unsigned gd_fs_size(const char *rel)
{
    char p[640];
    struct stat st;
    host_path(p, sizeof p, rel);
    if (stat(p, &st) != 0 || !S_ISREG(st.st_mode) || st.st_size < 0) return 0;
    return (unsigned)st.st_size;
}

int gd_fs_remove(const char *rel)
{
    char p[640];
    host_path(p, sizeof p, rel);
    return unlink(p) == 0;
}

void gd_fs_list(void (*cb)(const char *name, void *ctx), void *ctx)
{
    char p[640];
    DIR *d;
    struct dirent *e;
    host_path(p, sizeof p, "");
    d = opendir(p);
    if (!d) return;
    while ((e = readdir(d))) {
        if (e->d_name[0] == '.') continue;
        if (strcmp(e->d_name, "assets") == 0 || strcmp(e->d_name, "log.txt") == 0) continue;
        cb(e->d_name, ctx);
    }
    closedir(d);
}

void gd_log_flush(void)
{
    static char old[12288];
    unsigned have;
    if (!s_logn) return;
    have = 0;
    {
        char p[640];
        FILE *f;
        host_path(p, sizeof p, "log.txt");
        f = fopen(p, "rb");
        if (f) {
            have = (unsigned)fread(old, 1, sizeof old, f);
            fclose(f);
        }
    }
    if (have + s_logn > sizeof old) {
        unsigned drop = have + s_logn - (unsigned)sizeof old;
        if (drop < have) {
            memmove(old, old + drop, have - drop);
            have -= drop;
        } else {
            have = 0;
        }
    }
    memcpy(old + have, s_log, s_logn);
    gd_fs_write("log.txt", old, have + s_logn);
    s_logn = 0;
    s_log[0] = 0;
}

void gd_fatal(const char *why)
{
    gd_log("fatal: %s", why ? why : "?");
    gd_log_flush();
    abort();
}

#else
#include "hb_sdk.h"
#include "hb_heap.h"

uint32_t gd_millis(void) { return hb_time_uptime_ms(); }
void gd_sleep_ms(int ms) { (void)ms; }

static void full_path(char *out, unsigned n, const char *rel)
{
    if (rel && rel[0] == '/') snprintf(out, n, "%s", rel);
    else snprintf(out, n, "%s/%s", s_dir, rel ? rel : "");
}

int gd_fs_read(const char *rel, void *buf, unsigned max)
{
    char p[192];
    uint32_t n;
    full_path(p, sizeof p, rel);
    n = hb_fs_read(p, buf, max);
    return (int)n;
}

int gd_fs_write(const char *rel, const void *buf, unsigned n)
{
    char p[192];
    full_path(p, sizeof p, rel);
    hb_fs_mkdir(s_dir);
    return hb_fs_write(p, buf, n) ? 1 : 0;
}

int gd_fs_exists(const char *rel)
{
    char p[192];
    full_path(p, sizeof p, rel);
    return hb_fs_exists(p) ? 1 : 0;
}

unsigned gd_fs_size(const char *rel)
{
    char p[192];
    full_path(p, sizeof p, rel);
    return hb_fs_size(p);
}

int gd_fs_remove(const char *rel)
{
    char p[192];
    full_path(p, sizeof p, rel);
    return hb_fs_remove(p) ? 1 : 0;
}

void gd_fs_list(void (*cb)(const char *name, void *ctx), void *ctx)
{
    hb_dir_t d;
    char name[128];
    bool is_dir = false;
    if (!hb_fs_dir_open(&d, s_dir, false)) return;
    while (hb_fs_dir_next(&d, name, sizeof name, &is_dir)) {
        const char *base = name;
        const char *p, *slash = 0;
        /* sdk/freestanding/string.h has no strrchr */
        for (p = name; *p; p++)
            if (*p == '/') slash = p;
        if (slash) base = slash + 1;
        if (!base[0] || base[0] == '.') continue;
        if (is_dir) continue;
        if (strcmp(base, "log.txt") == 0) continue;
        cb(base, ctx);
    }
    hb_fs_dir_close(&d);
}

void gd_log_flush(void)
{
    char p[192];
    static char old[12288];
    unsigned have;
    if (!s_logn) return;
    snprintf(p, sizeof p, "%s/log.txt", s_dir);
    have = hb_fs_read(p, old, sizeof old);
    if (have > sizeof old) have = (unsigned)sizeof old;
    if (have + s_logn > sizeof old) {
        unsigned drop = have + s_logn - (unsigned)sizeof old;
        if (drop < have) {
            memmove(old, old + drop, have - drop);
            have -= drop;
        } else {
            have = 0;
        }
    }
    memcpy(old + have, s_log, s_logn);
    hb_fs_mkdir(s_dir);
    hb_fs_write(p, old, have + s_logn);
    s_logn = 0;
    s_log[0] = 0;
}

void gd_fatal(const char *why)
{
    gd_log("fatal: %s", why ? why : "?");
    gd_log_flush();
    for (;;) {}
}
#endif
