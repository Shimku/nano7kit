#include "../gd_config.h"
#include "gdalloc.h"
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
    s_gas = gas; s_brake = brake; s_lean_l = ll; s_lean_r = lr;
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

void gd_set_held(int gas, int brake, int ll, int lr)
{
    s_gas = gas; s_brake = brake; s_lean_l = ll; s_lean_r = lr;
}

void gd_set_edges(int fire, int back, int pause, int x, int y)
{
    s_fire = fire;
    s_back = back;
    s_pause = pause;
    s_tapx = x;
    s_tapy = y;
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
