/*
 * fruitninja - first-party RAW_SURFACE slice shell for Helvetica.
 *
 * This is NOT a Mortar recomp of Halfbrick Fruit Ninja 1.0. It is a
 * hardware-testable swipe/slice toy that:
 *   - never loads fileArcive.bin (8.6 MB) into RAM
 *   - plays with procedural fruit if /Apps/Data/FruitNinja is missing
 *   - optionally blits one small RGB565 banner streamed by the host
 *   - stubs audio (silent) and has no OpenFeint / Facebook
 *
 * Full Mortar recomp (OpenAL silent, social stubbed, Unicorn oracle) lives
 * on the Mac in host/fruitninja.py. That .hbapp will need slot B
 * (__TEXT 1.28 MB). Tonight's shell is RAW, well under the 0.5 MB launch
 * cap, and runs on slot A or on slot B if Mario already switched the pack.
 *
 * TAP-drag to slash. Bomb (dark with a spark) ends the round. Hold ~1 s
 * to reset. Heap free/largest stay on screen.
 */
#include "hb_raw_surface.h"
#include "hb_sdk.h"
#include "hb_heap.h"

#define DATA_DIR    "/Apps/Data/FruitNinja"
#define BANNER_PATH "/Apps/Data/FruitNinja/banner.rgb565"
#define BANNER_W    240
#define BANNER_H    48
#define BANNER_BYTES (BANNER_W * BANNER_H * 2)

#define MAX_FRUIT   6
#define MAX_SLASH   10
#define GRAVITY     3          /* px / frame at ~60 fps */
#define THROW_MIN   14
#define THROW_SPAN  10
#define SPAWN_MS    700u
#define BOMB_CHANCE 6          /* 1 in 6 */

static uint32_t *s_fb;
static int s_w, s_h;
static uint32_t s_free, s_largest;
static uint32_t s_score, s_best, s_lives;
static int s_playing;
static int s_banner_ok;
static uint32_t s_banner[BANNER_W * BANNER_H];
static uint32_t s_spawn_ms;
static uint32_t s_hold_ms;
static int s_prev_down, s_dirty = 1;
static uint32_t s_rng;

struct fruit {
    int x, y, vx, vy, r;
    uint32_t color;
    int bomb;
    int alive;
};

struct slash {
    int x, y;
    int life;
};

static struct fruit s_fr[MAX_FRUIT];
static struct slash s_sl[MAX_SLASH];
static int s_sl_n;
static int s_px, s_py;

static const uint32_t FRUIT_COL[4] = {
    0xe63946, 0xffd60a, 0x2a9d8f, 0xf4a261
};

static char *put_s(char *o, const char *s)
{
    while (*s) *o++ = *s++;
    *o = 0;
    return o;
}

static char *put_u(char *o, uint32_t u)
{
    char t[12];
    int n = 0;
    do { t[n++] = (char)('0' + (u % 10u)); u /= 10u; } while (u);
    while (n) *o++ = t[--n];
    *o = 0;
    return o;
}

static void text(int x, int y, const char *str, int scale, uint32_t fg)
{
    fg |= 0xff000000u;
    for (; *str; str++, x += 8 * scale) {
        const uint8_t *g = hb_font8x8_glyph(*str);
        int r, c, sy, sx, px, py;
        for (r = 0; r < 8; r++)
            for (c = 0; c < 8; c++) {
                if (!(g[r] & (0x80 >> c))) continue;
                for (sy = 0; sy < scale; sy++)
                    for (sx = 0; sx < scale; sx++) {
                        px = x + c * scale + sx;
                        py = y + r * scale + sy;
                        if (px >= 0 && px < s_w && py >= 0 && py < s_h)
                            s_fb[py * s_w + px] = fg;
                    }
            }
    }
}

static int rnd(int n)
{
    s_rng = s_rng * 1103515245u + 12345u;
    if (n <= 0) return 0;
    return (int)((s_rng >> 16) % (uint32_t)n);
}

static int dist2(int x0, int y0, int x1, int y1)
{
    int dx = x1 - x0, dy = y1 - y0;
    return dx * dx + dy * dy;
}

/* Closest-point distance from circle to segment, squared vs r*r. */
static int hit_seg(int x0, int y0, int x1, int y1, int cx, int cy, int r)
{
    int vx = x1 - x0, vy = y1 - y0;
    int wx = cx - x0, wy = cy - y0;
    int c2, t, px, py;
    int vv = vx * vx + vy * vy;
    if (vv < 1) return dist2(x0, y0, cx, cy) <= r * r;
    c2 = wx * vx + wy * vy;
    if (c2 <= 0) return dist2(x0, y0, cx, cy) <= r * r;
    if (c2 >= vv) return dist2(x1, y1, cx, cy) <= r * r;
    t = c2;
    px = x0 + (vx * t) / vv;
    py = y0 + (vy * t) / vv;
    return dist2(px, py, cx, cy) <= r * r;
}

static void load_banner(void)
{
    uint8_t buf[BANNER_BYTES];
    uint32_t n, i;
    s_banner_ok = 0;
    n = hb_fs_read(BANNER_PATH, buf, sizeof buf);
    if (n < BANNER_BYTES) return;
    for (i = 0; i < (uint32_t)(BANNER_W * BANNER_H); i++) {
        uint16_t v = (uint16_t)buf[i * 2] | ((uint16_t)buf[i * 2 + 1] << 8);
        uint32_t r = ((v >> 11) & 31u) * 255u / 31u;
        uint32_t g = ((v >> 5) & 63u) * 255u / 63u;
        uint32_t b = (v & 31u) * 255u / 31u;
        s_banner[i] = 0xff000000u | (r << 16) | (g << 8) | b;
    }
    s_banner_ok = 1;
}

static void blit_banner(int x, int y)
{
    int row, col, dx, dy;
    if (!s_banner_ok) return;
    for (row = 0; row < BANNER_H; row++) {
        dy = y + row;
        if (dy < 0 || dy >= s_h) continue;
        for (col = 0; col < BANNER_W; col++) {
            uint32_t p = s_banner[row * BANNER_W + col];
            if ((p & 0x00ffffffu) == 0) continue;
            dx = x + col;
            if (dx < 0 || dx >= s_w) continue;
            s_fb[dy * s_w + dx] = p;
        }
    }
}

static void spawn_one(void)
{
    int i, side;
    for (i = 0; i < MAX_FRUIT; i++) {
        if (s_fr[i].alive) continue;
        s_fr[i].alive = 1;
        s_fr[i].bomb = (rnd(BOMB_CHANCE) == 0);
        s_fr[i].r = s_fr[i].bomb ? 14 : 12 + rnd(6);
        s_fr[i].x = 24 + rnd(s_w - 48);
        s_fr[i].y = s_h + s_fr[i].r;
        s_fr[i].vy = -(THROW_MIN + rnd(THROW_SPAN));
        side = (s_fr[i].x < s_w / 2) ? 1 : -1;
        s_fr[i].vx = side * (1 + rnd(3));
        s_fr[i].color = s_fr[i].bomb ? 0x2b2b2b : FRUIT_COL[rnd(4)];
        return;
    }
}

static void reset_round(void)
{
    int i;
    for (i = 0; i < MAX_FRUIT; i++) s_fr[i].alive = 0;
    s_sl_n = 0;
    s_score = 0;
    s_lives = 3;
    s_playing = 1;
    s_spawn_ms = 0;
}

static void draw(void)
{
    char line[48];
    char *o;
    int i;

    hb_raw_fill(0x141018);
    hb_raw_fill_rect(0, 0, s_w, 36, 0x1c1418);
    text(8, 8, "SLICE", 2, 0xe8c547);

    o = put_u(line, s_score);
    text(s_w - 8 - (int)(o - line) * 8, 8, line, 1, 0xffffff);

    o = put_s(line, "HP ");
    o = put_u(o, s_lives);
    text(88, 12, line, 1, s_lives ? 0x7ee0a8 : 0xe63946);

    if (s_banner_ok) blit_banner(0, 40);
    else text(8, 44, "NO ARCHIVE (OK)", 1, 0x8a93a8);

    for (i = 0; i < MAX_FRUIT; i++) {
        if (!s_fr[i].alive) continue;
        hb_raw_disc(s_fr[i].x, s_fr[i].y, s_fr[i].r, s_fr[i].color);
        if (s_fr[i].bomb)
            hb_raw_disc(s_fr[i].x + 6, s_fr[i].y - s_fr[i].r + 2, 3, 0xe63946);
        else
            hb_raw_disc(s_fr[i].x - s_fr[i].r / 3, s_fr[i].y - s_fr[i].r / 3,
                        s_fr[i].r / 4, 0xffffff);
    }
    for (i = 0; i < s_sl_n; i++) {
        if (s_sl[i].life > 0)
            hb_raw_disc(s_sl[i].x, s_sl[i].y, 2 + s_sl[i].life / 3, 0xf4f1de);
    }

    if (!s_playing) {
        text(28, 200, "GAME OVER", 2, 0xe63946);
        o = put_s(line, "BEST ");
        o = put_u(o, s_best);
        text(40, 236, line, 1, 0xe8c547);
        text(20, 268, "HOLD RESET", 1, 0x8a93a8);
    }

    o = put_s(line, "HEAP ");
    o = put_u(o, s_free / 1024u);
    put_s(o, "K");
    text(8, s_h - 32, line, 1, 0x5a6270);
    o = put_s(line, "LG ");
    o = put_u(o, s_largest / 1024u);
    put_s(o, "K");
    text(100, s_h - 32, line, 1, 0x5a6270);
    text(8, s_h - 16, "SHELL NOT MORTAR  SILENT", 1, 0x5a6270);
}

static void add_slash(int x, int y)
{
    int i;
    if (s_sl_n < MAX_SLASH) {
        s_sl[s_sl_n].x = x;
        s_sl[s_sl_n].y = y;
        s_sl[s_sl_n].life = 8;
        s_sl_n++;
        return;
    }
    for (i = 0; i < MAX_SLASH - 1; i++) s_sl[i] = s_sl[i + 1];
    s_sl[MAX_SLASH - 1].x = x;
    s_sl[MAX_SLASH - 1].y = y;
    s_sl[MAX_SLASH - 1].life = 8;
}

static void slice_check(int x0, int y0, int x1, int y1)
{
    int i;
    if (!s_playing) return;
    for (i = 0; i < MAX_FRUIT; i++) {
        if (!s_fr[i].alive) continue;
        if (!hit_seg(x0, y0, x1, y1, s_fr[i].x, s_fr[i].y, s_fr[i].r + 4))
            continue;
        if (s_fr[i].bomb) {
            s_playing = 0;
            if (s_score > s_best) s_best = s_score;
            s_fr[i].alive = 0;
            hb_trace_log("FNBOMB", s_score, s_lives);
            return;
        }
        s_fr[i].alive = 0;
        s_score += 1;
    }
}

void hb_raw_init(int w, int h)
{
    s_fb = hb_raw_fb();
    s_w = w;
    s_h = h;
    s_rng = 0xC0FFEEu ^ hb_time_uptime_ms();
    s_free = hb_os_heap_free();
    s_largest = hb_os_heap_largest();
    load_banner();
    reset_round();
    hb_trace_init();
    hb_trace_log("FNINIT", s_free, s_largest);
    draw();
}

void hb_raw_frame(const hb_spoint_t *t)
{
    uint32_t now = hb_time_uptime_ms();
    int i, moved = 0;

    if (t->down) {
        if (!s_prev_down) {
            s_hold_ms = now;
            s_px = t->x;
            s_py = t->y;
            add_slash(t->x, t->y);
        } else {
            if (t->x != s_px || t->y != s_py) {
                slice_check(s_px, s_py, t->x, t->y);
                add_slash(t->x, t->y);
                s_px = t->x;
                s_py = t->y;
                moved = 1;
            }
        }
        if (!s_playing && now - s_hold_ms >= 1000u) {
            reset_round();
            s_hold_ms = now;
            s_dirty = 1;
        }
    }
    s_prev_down = t->down;

    if (s_playing) {
        if (now - s_spawn_ms >= SPAWN_MS) {
            spawn_one();
            s_spawn_ms = now;
        }
        for (i = 0; i < MAX_FRUIT; i++) {
            if (!s_fr[i].alive) continue;
            s_fr[i].vy += GRAVITY;
            s_fr[i].x += s_fr[i].vx;
            s_fr[i].y += s_fr[i].vy;
            if (s_fr[i].x < s_fr[i].r) {
                s_fr[i].x = s_fr[i].r;
                s_fr[i].vx = -s_fr[i].vx;
            }
            if (s_fr[i].x > s_w - s_fr[i].r) {
                s_fr[i].x = s_w - s_fr[i].r;
                s_fr[i].vx = -s_fr[i].vx;
            }
            if (s_fr[i].y > s_h + 40 && s_fr[i].vy > 0) {
                s_fr[i].alive = 0;
                if (!s_fr[i].bomb && s_lives) {
                    s_lives--;
                    if (!s_lives) {
                        s_playing = 0;
                        if (s_score > s_best) s_best = s_score;
                    }
                }
            }
        }
        s_dirty = 1;
    }
    for (i = 0; i < s_sl_n; i++) {
        if (s_sl[i].life > 0) {
            s_sl[i].life--;
            s_dirty = 1;
        }
    }
    while (s_sl_n > 0 && s_sl[s_sl_n - 1].life <= 0) s_sl_n--;

    if ((now % 250u) < 20u) {
        s_free = hb_os_heap_free();
        s_largest = hb_os_heap_largest();
        s_dirty = 1;
    }
    if (s_dirty || moved) {
        draw();
        s_dirty = 0;
    }
}
