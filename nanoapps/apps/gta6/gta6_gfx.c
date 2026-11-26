/*
 * Original Mangrove Key pixels. GTA VI look recreated in integer fills.
 * No third-party logos, fonts, frames or music.
 */
#include "gta6_game.h"
#include "gta6_notice.h"

#define F_ALIVE 1u
#define F_INCAR 2u
#define F_MARK  4u
#define K_NIA   1
#define K_WADE  2
#define K_PED   3
#define K_CAR   4
#define K_POL   5
#define K_GATOR 6
#define JOY_CX  48
#define JOY_CY  378
#define JOY_R   36
#define BTN_AX  198
#define BTN_AY  350
#define BTN_BX  198
#define BTN_BY  402
#define BTN_SX  148
#define BTN_SY  376
#define PAUSE_X 200

static const uint32_t CAR_COL[6] = {
    G6_COL_ORANGE, G6_COL_TEAL, G6_COL_MAGENTA,
    0xffF4E04A, 0xff7EE0A8, 0xffF8F4EE
};

static const uint32_t VI_STOPS[7] = {
    G6_COL_BLUE, G6_COL_INDIGO, G6_COL_PURPLE, G6_COL_MAGENTA,
    G6_COL_PINK, G6_COL_CORAL, G6_COL_ORANGE
};

static const uint32_t ROOF_PAL[8] = {
    0xffD4A090, 0xffC8B8A4, 0xffB09080, 0xffE0C8B0,
    0xffC4A070, 0xffA88890, 0xffB8A878, 0xffD8B8A0
};

static void pset(uint32_t *fb, int w, int h, int x, int y, uint32_t c)
{
    if ((unsigned)x < (unsigned)w && (unsigned)y < (unsigned)h)
        fb[(long)y * w + x] = c | 0xff000000u;
}

static uint32_t lerp(uint32_t a, uint32_t b, int t, int n)
{
    int ar, ag, ab, br, bg, bb, r, g, bl;
    if (n < 1) n = 1;
    if (t < 0) t = 0;
    if (t > n) t = n;
    ar = (int)((a >> 16) & 255); ag = (int)((a >> 8) & 255); ab = (int)(a & 255);
    br = (int)((b >> 16) & 255); bg = (int)((b >> 8) & 255); bb = (int)(b & 255);
    r = ar + (br - ar) * t / n;
    g = ag + (bg - ag) * t / n;
    bl = ab + (bb - ab) * t / n;
    return 0xff000000u | ((uint32_t)r << 16) | ((uint32_t)g << 8) | (uint32_t)bl;
}

static void pset_a(uint32_t *fb, int w, int h, int x, int y, uint32_t c, int a)
{
    uint32_t d;
    if ((unsigned)x >= (unsigned)w || (unsigned)y >= (unsigned)h) return;
    d = fb[(long)y * w + x];
    fb[(long)y * w + x] = lerp(d, c, a, 256);
}

static void fillr(uint32_t *fb, int w, int h, int x, int y, int rw, int rh, uint32_t c)
{
    int i, j, x1, y1;
    c |= 0xff000000u;
    if (rw < 0) { x += rw; rw = -rw; }
    if (rh < 0) { y += rh; rh = -rh; }
    x1 = x + rw; y1 = y + rh;
    if (x < 0) x = 0;
    if (y < 0) y = 0;
    if (x1 > w) x1 = w;
    if (y1 > h) y1 = h;
    for (j = y; j < y1; j++) {
        uint32_t *row = fb + (long)j * w;
        for (i = x; i < x1; i++) row[i] = c;
    }
}

static uint32_t fade_c(uint32_t c, int f)
{
    if (f >= 256) return c | 0xff000000u;
    if (f <= 0) return 0xff000000u;
    return lerp(0, c, f, 256);
}

static uint32_t lite(uint32_t c)
{
    return lerp(c, 0xffffffffu, 1, 3);
}

static uint32_t dim(uint32_t c)
{
    return lerp(c, 0xff000010, 1, 3);
}

static void disc(uint32_t *fb, int w, int h, int cx, int cy, int r, uint32_t c)
{
    int y, x, r2;
    if (r < 1) return;
    r2 = r * r;
    c |= 0xff000000u;
    for (y = -r; y <= r; y++) {
        int yy = y * y;
        for (x = -r; x <= r; x++)
            if (x * x + yy <= r2) pset(fb, w, h, cx + x, cy + y, c);
    }
}

static void disc_a(uint32_t *fb, int w, int h, int cx, int cy, int r, uint32_t c, int a)
{
    int y, x, r2;
    if (r < 1) return;
    r2 = r * r;
    for (y = -r; y <= r; y++) {
        int yy = y * y;
        for (x = -r; x <= r; x++)
            if (x * x + yy <= r2) pset_a(fb, w, h, cx + x, cy + y, c, a);
    }
}

static uint32_t vi_grad(int y, int y0, int y1)
{
    int n = y1 - y0, t, seg, u;
    if (n < 1) n = 1;
    t = y - y0;
    if (t < 0) t = 0;
    if (t > n) t = n;
    t = t * 6;
    seg = t / n;
    u = t % n;
    if (seg >= 6) return VI_STOPS[6];
    return lerp(VI_STOPS[seg], VI_STOPS[seg + 1], u, n);
}

static uint32_t sky_at(int y, int h)
{
    return vi_grad(y, 0, h * 3 / 5);
}

#define VI_Y0    90
#define VI_Y1    358
#define VI_AX    74
#define VI_VL0   10
#define VI_VL1   46
#define VI_VR0   102
#define VI_VR1   138
#define VI_IX    180
#define VI_STEM  30
#define VI_SERW  58
#define VI_SERH  20

static int in_span(int x, int y, int y0, int y1, int a0, int a1, int b0, int b1)
{
    int n, t, lo, hi, tmp;
    if (y < y0 || y >= y1) return 0;
    n = y1 - y0;
    if (n < 1) return 0;
    t = y - y0;
    lo = a0 + (b0 - a0) * t / n;
    hi = a1 + (b1 - a1) * t / n;
    if (lo > hi) { tmp = lo; lo = hi; hi = tmp; }
    return x >= lo && x < hi;
}

static int vi_core(int x, int y, int grow)
{
    int y0 = VI_Y0 - grow, y1 = VI_Y1 + grow;
    int stem0 = VI_IX - VI_STEM / 2 - grow;
    int stem1 = VI_IX + VI_STEM / 2 + grow;
    int s0 = VI_IX - VI_SERW / 2 - grow;
    int s1 = VI_IX + VI_SERW / 2 + grow;
    int sh = VI_SERH + grow;
    if (sh < 10) sh = 10;
    if (in_span(x, y, y0, y1,
                VI_VL0 - grow, VI_VL1 + grow,
                VI_AX - 6 - grow, VI_AX + 3 + grow))
        return 1;
    if (in_span(x, y, y0, y1,
                VI_VR0 - grow, VI_VR1 + grow,
                VI_AX - 3 - grow, VI_AX + 6 + grow))
        return 1;
    if (x >= stem0 && x < stem1 && y >= y0 && y < y1) return 1;
    if (x >= s0 && x < s1 && y >= y0 && y < y0 + sh) return 1;
    if (x >= s0 && x < s1 && y >= y1 - sh && y < y1) return 1;
    return 0;
}

static void text_ol(uint32_t *fb, int w, int h, int x, int y, const char *s, int scale, uint32_t fg, uint32_t ol)
{
    int ox, oy;
    for (oy = -2; oy <= 2; oy++)
        for (ox = -2; ox <= 2; ox++)
            if (ox || oy)
                g6_text5(fb, w, h, x + ox, y + oy, s, scale, ol);
    g6_text5(fb, w, h, x, y, s, scale, fg);
}

static void wordmark(uint32_t *fb, int w, int h, int x, int y, const char *s, uint32_t fg, uint32_t ol)
{
    int ox, oy, adv = 14;
    for (oy = -2; oy <= 2; oy++)
        for (ox = -2; ox <= 2; ox++)
            if (ox || oy)
                g6_text5n(fb, w, h, x + ox, y + oy, s, 3, ol, adv);
    g6_text5n(fb, w, h, x, y, s, 3, fg, adv);
    g6_text5n(fb, w, h, x + 1, y, s, 3, fg, adv);
}

static void draw_palm_sil(uint32_t *fb, int w, int h, int x, int y, uint32_t c)
{
    int i, k;
    fillr(fb, w, h, x, y, 3, 68, c);
    fillr(fb, w, h, x - 1, y + 12, 5, 14, c);
    disc(fb, w, h, x + 1, y - 2, 5, c);
    for (k = 0; k < 7; k++) {
        int dx = (k - 3) * 5;
        int dy = (k == 3) ? -20 : ((k == 0 || k == 6) ? -2 : -12);
        int len = 15;
        for (i = 0; i < len; i++) {
            int px = x + 1 + dx * i / 11;
            int py = y - 4 + dy * i / 11 + (i * i) / 26;
            fillr(fb, w, h, px - 1, py, 3, 2, c);
        }
    }
}

static void draw_sky(uint32_t *fb, int w, int h, int horizon, int fade)
{
    int y;
    for (y = 0; y < horizon; y++)
        fillr(fb, w, h, 0, y, w, 1, fade_c(sky_at(y, horizon), fade));
}

static void draw_water(uint32_t *fb, int w, int h, int y0, int pan, int fade)
{
    int y, x, band;
    for (y = y0; y < h; y++) {
        uint32_t deep = lerp(0xff0A4060, G6_COL_TEAL, y - y0, h - y0 + 1);
        uint32_t col = fade_c(deep, fade);
        fillr(fb, w, h, 0, y, w, 1, col);
        if (((y + pan) & 7) == 0)
            fillr(fb, w, h, (pan * 3 + y) & 31, y, 28, 1, fade_c(G6_COL_FOAM, fade * 2 / 3));
    }
    band = y0 + 8;
    for (x = 0; x < w; x += 11)
        fillr(fb, w, h, x + (pan & 7), band, 6, 1, fade_c(0xffA0E8F0, fade));
}

static void draw_sun(uint32_t *fb, int w, int h, int cx, int cy, int r, int fade)
{
    disc(fb, w, h, cx, cy, r + 8, fade_c(G6_COL_PINK, fade / 2));
    disc(fb, w, h, cx, cy, r, fade_c(G6_COL_ORANGE, fade));
    disc(fb, w, h, cx, cy, r * 2 / 3, fade_c(0xffFFF0C0, fade));
}

static void draw_skyline(uint32_t *fb, int w, int h, int base, int pan, int fade)
{
    int i;
    static const int tw[8] = { 18, 26, 14, 32, 20, 16, 28, 22 };
    static const int th[8] = { 40, 70, 34, 88, 52, 44, 64, 38 };
    static const uint32_t tc[8] = {
        0xffE8A888, 0xff7EC8B0, 0xffD89AA0, 0xffF0C4A0,
        0xffC45A3A, 0xff9AD0C8, 0xffE8B070, 0xffB08090
    };
    int x = -20 - (pan % 40);
    for (i = 0; i < 12; i++) {
        int k = i & 7;
        uint32_t col = fade_c(tc[k], fade);
        fillr(fb, w, h, x, base - th[k], tw[k], th[k], col);
        fillr(fb, w, h, x, base - th[k], tw[k], 3, fade_c(0xffF8E0D0, fade));
        fillr(fb, w, h, x + 3, base - th[k] + 8, 4, 6, fade_c(0xff3A2048, fade));
        fillr(fb, w, h, x + tw[k] - 8, base - th[k] + 8, 4, 6, fade_c(0xff3A2048, fade));
        x += tw[k] + 6;
    }
}

static void draw_car_side(uint32_t *fb, int w, int h, int x, int y, uint32_t col, int fade)
{
    uint32_t body = fade_c(col, fade);
    uint32_t hi = fade_c(lerp(col, 0xffffffff, 1, 2), fade);
    uint32_t win = fade_c(0xff203848, fade);
    fillr(fb, w, h, x, y + 6, 36, 12, fade_c(0xff101018, fade));
    fillr(fb, w, h, x + 2, y, 32, 14, body);
    fillr(fb, w, h, x + 2, y, 32, 3, hi);
    fillr(fb, w, h, x + 8, y + 3, 10, 6, win);
    fillr(fb, w, h, x + 20, y + 3, 8, 6, win);
    fillr(fb, w, h, x + 4, y + 12, 6, 5, fade_c(0xff1A1A1A, fade));
    fillr(fb, w, h, x + 24, y + 12, 6, 5, fade_c(0xff1A1A1A, fade));
    pset(fb, w, h, x + 32, y + 6, fade_c(0xffFFF1A8, fade));
    pset(fb, w, h, x + 2, y + 6, fade_c(0xffff4050, fade));
}

static void scene_sunset(uint32_t *fb, int w, int h, int t, int fade)
{
    int pan = t * 2;
    int hor = 210 - t / 6;
    draw_sky(fb, w, h, hor, fade);
    draw_sun(fb, w, h, 172 + pan / 8, hor - 52, 32 + t / 8, fade);
    draw_skyline(fb, w, h, hor, pan, fade);
    fillr(fb, w, h, 0, hor, w, 3, fade_c(G6_COL_PINK, fade));
    draw_water(fb, w, h, hor + 2, pan, fade);
    disc(fb, w, h, 168 + pan / 8, hor + 40, 10, fade_c(G6_COL_ORANGE, fade / 3));
    draw_palm_sil(fb, w, h, 16 - pan / 3, hor - 8, fade_c(G6_COL_OUTLINE, fade));
    draw_palm_sil(fb, w, h, 70 - pan / 2, hor + 6, fade_c(0xff120818, fade));
    draw_palm_sil(fb, w, h, 200 + pan / 4, hor - 4, fade_c(G6_COL_OUTLINE, fade));
}

static void neon_sign(uint32_t *fb, int w, int h, int x, int y, const char *s, uint32_t glow, int fade)
{
    int n = 0;
    while (s[n]) n++;
    fillr(fb, w, h, x - 4, y - 4, n * 12 + 8, 22, fade_c(lerp(glow, 0, 1, 2), fade));
    fillr(fb, w, h, x - 2, y - 2, n * 12 + 4, 18, fade_c(glow, fade));
    g6_text5(fb, w, h, x, y, s, 2, fade_c(G6_COL_WHITE, fade));
}

static void motel_sign(uint32_t *fb, int w, int h, int x, int y, int fade)
{
    static const char *rows = "MOTEL";
    int i;
    fillr(fb, w, h, x + 6, y + 90, 4, 40, fade_c(0xff2A2030, fade));
    fillr(fb, w, h, x, y, 22, 92, fade_c(0xff4A1028, fade));
    fillr(fb, w, h, x + 2, y + 2, 18, 88, fade_c(G6_COL_MAGENTA, fade));
    for (i = 0; i < 5; i++) {
        char ch[2];
        ch[0] = rows[i];
        ch[1] = 0;
        g6_text5(fb, w, h, x + 6, y + 6 + i * 16, ch, 2, fade_c(G6_COL_WHITE, fade));
    }
}

static void scene_neon(uint32_t *fb, int w, int h, int t, int fade)
{
    int pan = t;
    int x, y, i;
    int curb = 236;
    fillr(fb, w, h, 0, 0, w, h, fade_c(0xff0A0618, fade));
    for (y = 0; y < curb; y++)
        fillr(fb, w, h, 0, y, w, 1, fade_c(lerp(0xff120028, G6_COL_INDIGO, y, curb), fade));
    for (i = 0; i < 6; i++) {
        int bx = (i * 52 - pan) % 312 - 28;
        int bh = 150 + ((i * 37) & 63);
        uint32_t wall = fade_c((i & 1) ? 0xff2A1840 : 0xff1A1028, fade);
        fillr(fb, w, h, bx, curb - bh, 46, bh, wall);
        fillr(fb, w, h, bx, curb - bh, 46, 4, fade_c((i & 1) ? G6_COL_MAGENTA : G6_COL_TEAL, fade));
        for (y = curb - bh + 12; y < curb - 8; y += 14)
            for (x = bx + 6; x < bx + 40; x += 12)
                fillr(fb, w, h, x, y, 7, 8,
                      fade_c(((x + y + t) & 8) ? 0xffFFE8A0 : 0xff3A2050, fade));
        if (i == 1) motel_sign(fb, w, h, bx + 12, curb - bh - 8, fade);
        if (i == 2) neon_sign(fb, w, h, bx + 6, curb - bh + 8, "BAR", G6_COL_TEAL, fade);
        if (i == 4) neon_sign(fb, w, h, bx + 6, curb - bh + 10, "KEY", G6_COL_PINK, fade);
    }
    for (y = curb; y < h; y++) {
        int u = y - curb;
        uint32_t wet = lerp(0xff12101C, 0xff1A1028, u, h - curb);
        fillr(fb, w, h, 0, y, w, 1, fade_c(wet, fade));
        if ((y & 3) == 0)
            fillr(fb, w, h, 0, y, w, 1, fade_c(lerp(G6_COL_MAGENTA, 0, 1, 6), fade / 5));
    }
    fillr(fb, w, h, 0, curb, w, 3, fade_c(0xff3A3048, fade));
    fillr(fb, w, h, 0, curb + 4, w, 2, fade_c(G6_COL_MAGENTA, fade / 2));
    for (x = -20; x < w; x += 22)
        fillr(fb, w, h, x + (pan % 22), curb + 48, 12, 2, fade_c(0xffC8B060, fade / 2));
    for (i = 0; i < 5; i++) {
        int rx = 20 + i * 48 - (pan & 15);
        uint32_t rc = fade_c((i & 1) ? G6_COL_MAGENTA : G6_COL_TEAL, fade / 3);
        fillr(fb, w, h, rx, curb + 8, 18, 110, rc);
        fillr(fb, w, h, rx + 4, curb + 12, 10, 90, fade_c(0xff12101C, fade));
    }
    disc(fb, w, h, 40, curb - 20, 6, fade_c(0xffFFF1A8, fade));
    fillr(fb, w, h, 38, curb - 14, 3, 18, fade_c(0xff2A2030, fade));
    draw_car_side(fb, w, h, 70 + (t & 7), curb + 22, G6_COL_MAGENTA, fade);
    fillr(fb, w, h, 78, curb + 42, 28, 22, fade_c(G6_COL_MAGENTA, fade / 4));
    draw_car_side(fb, w, h, 150, curb + 30, G6_COL_TEAL, fade);
    fillr(fb, w, h, 158, curb + 50, 28, 20, fade_c(G6_COL_TEAL, fade / 4));
    draw_car_side(fb, w, h, 24, curb + 70, 0xffF4E04A, fade);
    fillr(fb, w, h, 32, curb + 90, 28, 16, fade_c(0xffF4E04A, fade / 5));
    fillr(fb, w, h, 0, h - 52, w, 52, fade_c(0xff14101C, fade));
    fillr(fb, w, h, 0, h - 54, w, 3, fade_c(0xff3A3048, fade));
    for (x = 8; x < w; x += 28)
        fillr(fb, w, h, x, h - 40, 18, 2, fade_c(0xff2A2438, fade));
    draw_palm_sil(fb, w, h, 210, h - 70, fade_c(G6_COL_OUTLINE, fade));
    draw_palm_sil(fb, w, h, 18, h - 64, fade_c(0xff100818, fade));
}

static void scene_causeway(uint32_t *fb, int w, int h, int t, int fade)
{
    int pan = t * 3;
    int hor = 190;
    draw_sky(fb, w, h, hor, fade);
    draw_sun(fb, w, h, 40 + t / 4, hor - 20, 22, fade);
    draw_water(fb, w, h, hor, pan, fade);
    fillr(fb, w, h, 0, 248, w, 36, fade_c(G6_COL_ASPHALT, fade));
    fillr(fb, w, h, 0, 246, w, 3, fade_c(0xffC8C0B0, fade));
    fillr(fb, w, h, 0, 282, w, 3, fade_c(0xffC8C0B0, fade));
    {
        int dash;
        for (dash = -20; dash < w; dash += 18)
            fillr(fb, w, h, dash + (pan % 18), 264, 10, 2, fade_c(G6_COL_STAR, fade));
    }
    draw_car_side(fb, w, h, 40 + (t % 80), 250, G6_COL_MAGENTA, fade);
    draw_palm_sil(fb, w, h, 200, 200, fade_c(G6_COL_OUTLINE, fade));
    fillr(fb, w, h, 0, 286, w, 8, fade_c(0xff0A4060, fade));
}

static void person_sil(uint32_t *fb, int w, int h, int x, int y, int who_id, uint32_t c)
{
    fillr(fb, w, h, x - 10, y - 6, 20, 28, c);
    fillr(fb, w, h, x - 8, y - 22, 16, 16, c);
    if (who_id == 0) fillr(fb, w, h, x - 12, y - 26, 24, 8, c);
    else fillr(fb, w, h, x - 8, y - 28, 16, 8, c);
    fillr(fb, w, h, x - 10, y + 20, 8, 28, c);
    fillr(fb, w, h, x + 2, y + 20, 8, 28, c);
    fillr(fb, w, h, x - 16, y + 2, 6, 16, c);
    fillr(fb, w, h, x + 10, y + 2, 6, 16, c);
}

static void scene_duo(uint32_t *fb, int w, int h, int t, int fade)
{
    int hor = 230 - t / 8;
    uint32_t sil = fade_c(0xff08040C, fade);
    draw_sky(fb, w, h, hor, fade);
    draw_sun(fb, w, h, 120, hor - 10, 36 + t / 8, fade);
    fillr(fb, w, h, 0, hor, w, 4, fade_c(G6_COL_ORANGE, fade));
    draw_water(fb, w, h, hor + 4, t, fade);
    fillr(fb, w, h, 0, 360, w, h - 360, fade_c(0xff1A1010, fade));
    person_sil(fb, w, h, 86, 268, 0, sil);
    person_sil(fb, w, h, 138, 258, 1, sil);
    draw_palm_sil(fb, w, h, 18, 220, sil);
    draw_palm_sil(fb, w, h, 204, 210, sil);
}

static void draw_logo_vi(uint32_t *fb, int w, int h, int fade)
{
    int x, y;
    int y0 = VI_Y0 - 14, y1 = VI_Y1 + 14;
    uint32_t outline = fade_c(G6_COL_OUTLINE, fade);
    uint32_t rim = fade_c(G6_COL_WHITE, fade);
    uint32_t black = fade_c(0xff08060C, fade);
    uint32_t white = fade_c(G6_COL_WHITE, fade);
    if (y0 < 0) y0 = 0;
    if (y1 > h) y1 = h;
    for (y = 0; y < h; y++)
        fillr(fb, w, h, 0, y, w, 1, fade_c(lerp(0xff0A0618, G6_COL_OUTLINE, y, h), fade));
    draw_palm_sil(fb, w, h, 8, 300, fade_c(0xff100818, fade));
    draw_palm_sil(fb, w, h, 214, 280, fade_c(0xff100818, fade));
    for (y = y0; y < y1; y++) {
        uint32_t gcol = fade_c(vi_grad(y, VI_Y0, VI_Y1), fade);
        for (x = 0; x < w; x++) {
            int core = vi_core(x, y, 0);
            int inner = vi_core(x, y, -4);
            int white_r = vi_core(x, y, 3);
            int ring = vi_core(x, y, 7);
            if (core)
                pset(fb, w, h, x, y, inner ? gcol : rim);
            else if (white_r)
                pset(fb, w, h, x, y, rim);
            else if (ring)
                pset(fb, w, h, x, y, outline);
        }
    }
    wordmark(fb, w, h, 85, 8, "GRAND", white, black);
    wordmark(fb, w, h, 85, 32, "THEFT", white, black);
    wordmark(fb, w, h, 92, 56, "AUTO", white, black);
}

static void portrait(uint32_t *fb, int w, int h, int cx, int cy, int who_id, int fade)
{
    uint32_t skin = fade_c(who_id ? 0xffE0B898 : 0xffE8C4A8, fade);
    uint32_t ski = fade_c(who_id ? 0xffF0D4B8 : 0xffF4DCC8, fade);
    uint32_t sks = fade_c(who_id ? 0xffB88870 : 0xffC09078, fade);
    uint32_t hair = fade_c(who_id ? 0xff141414 : 0xff2A1810, fade);
    uint32_t hai = fade_c(who_id ? 0xff3A3A3A : 0xff4A3020, fade);
    uint32_t shirt = fade_c(who_id ? 0xffF0E8DC : G6_COL_MAGENTA, fade);
    uint32_t shi = fade_c(who_id ? 0xffffffff : 0xffF060C0, fade);
    uint32_t shs = fade_c(who_id ? 0xffC0B8AC : 0xff8A1860, fade);
    uint32_t lip = fade_c(who_id ? 0xffA06060 : 0xffC05060, fade);
    fillr(fb, w, h, cx - 40, cy + 22, 80, 18, shs);
    fillr(fb, w, h, cx - 34, cy + 18, 68, 16, shirt);
    fillr(fb, w, h, cx - 10, cy + 18, 18, 10, shi);
    fillr(fb, w, h, cx - 30, cy + 32, 60, 88, shirt);
    fillr(fb, w, h, cx - 30, cy + 32, 10, 88, shs);
    fillr(fb, w, h, cx + 20, cy + 40, 10, 80, dim(shirt));
    fillr(fb, w, h, cx - 8, cy + 36, 12, 50, shi);
    fillr(fb, w, h, cx - 9, cy + 10, 18, 18, sks);
    fillr(fb, w, h, cx - 7, cy + 10, 14, 16, skin);
    fillr(fb, w, h, cx - 22, cy - 32, 44, 44, skin);
    disc(fb, w, h, cx, cy - 10, 22, skin);
    fillr(fb, w, h, cx + 6, cy - 28, 18, 40, ski);
    fillr(fb, w, h, cx - 22, cy - 22, 10, 36, sks);
    if (who_id == 0) {
        fillr(fb, w, h, cx - 26, cy - 46, 52, 24, hair);
        fillr(fb, w, h, cx - 28, cy - 30, 14, 50, hair);
        fillr(fb, w, h, cx + 16, cy - 30, 14, 50, hair);
        fillr(fb, w, h, cx - 20, cy - 48, 40, 12, hai);
        fillr(fb, w, h, cx - 16, cy - 28, 32, 10, hair);
        disc(fb, w, h, cx - 24, cy - 2, 3, fade_c(G6_COL_STAR, fade));
    } else {
        fillr(fb, w, h, cx - 22, cy - 44, 44, 18, hair);
        fillr(fb, w, h, cx - 18, cy - 48, 36, 10, hai);
        fillr(fb, w, h, cx - 22, cy - 30, 8, 12, hair);
        fillr(fb, w, h, cx + 14, cy - 30, 8, 12, hair);
        fillr(fb, w, h, cx - 16, cy + 6, 32, 10, fade_c(0xffC4A888, fade));
    }
    fillr(fb, w, h, cx - 14, cy - 14, 10, 5, fade_c(0xffF8F4EE, fade));
    fillr(fb, w, h, cx + 4, cy - 14, 10, 5, fade_c(0xffF8F4EE, fade));
    fillr(fb, w, h, cx - 11, cy - 13, 5, 4, fade_c(0xff1A1A1A, fade));
    fillr(fb, w, h, cx + 7, cy - 13, 5, 4, fade_c(0xff1A1A1A, fade));
    fillr(fb, w, h, cx - 14, cy - 18, 10, 3, hair);
    fillr(fb, w, h, cx + 4, cy - 18, 10, 3, hair);
    fillr(fb, w, h, cx - 1, cy - 8, 4, 6, sks);
    fillr(fb, w, h, cx - 7, cy + 2, 14, 4, lip);
}

static void draw_gator_art(uint32_t *fb, int w, int h, int x, int y, int fade)
{
    fillr(fb, w, h, x, y + 4, 28, 8, fade_c(0xff3A6A32, fade));
    fillr(fb, w, h, x + 20, y + 6, 14, 6, fade_c(0xff2A4A28, fade));
    pset(fb, w, h, x + 32, y + 7, fade_c(0xffE8E050, fade));
}

static void draw_menu(G6Game *g, uint32_t *fb, int w, int h)
{
    int x, y;
    int pulse = ((g->frames / 10) & 1);
    for (y = 0; y < h; y++) {
        for (x = 0; x < w; x++) {
            int s = x + y;
            uint32_t c;
            if (s < 150) c = vi_grad(y, 0, 180);
            else if (s < 280) c = 0xff1A1028;
            else if (s < 400) c = 0xff120018;
            else if (s < 520) c = 0xff102030;
            else c = 0xff0A1810;
            pset(fb, w, h, x, y, c);
        }
    }
    for (y = 0; y < h; y++) {
        int e0 = 150 - y, e1 = 280 - y, e2 = 400 - y, e3 = 520 - y;
        fillr(fb, w, h, e0, y, 3, 1, G6_COL_OUTLINE);
        fillr(fb, w, h, e1, y, 3, 1, G6_COL_PINK);
        fillr(fb, w, h, e2, y, 3, 1, G6_COL_TEAL);
        fillr(fb, w, h, e3, y, 3, 1, G6_COL_ORANGE);
    }
    draw_skyline(fb, w, h, 78, 8, 256);
    draw_palm_sil(fb, w, h, 22, 86, G6_COL_OUTLINE);
    draw_palm_sil(fb, w, h, 52, 104, 0xff120818);
    portrait(fb, w, h, 64, 168, 0, 256);
    portrait(fb, w, h, 176, 248, 1, 256);
    draw_palm_sil(fb, w, h, 18, 320, 0xff100818);
    draw_palm_sil(fb, w, h, 48, 338, G6_COL_OUTLINE);
    fillr(fb, w, h, 4, 352, 70, 18, 0xff101018);
    fillr(fb, w, h, 8, 340, 62, 22, G6_COL_TEAL);
    fillr(fb, w, h, 8, 340, 62, 5, lite(G6_COL_TEAL));
    fillr(fb, w, h, 18, 344, 16, 10, 0xff203848);
    fillr(fb, w, h, 40, 344, 14, 10, 0xff203848);
    fillr(fb, w, h, 12, 356, 10, 8, 0xff1A1A1A);
    fillr(fb, w, h, 50, 356, 10, 8, 0xff1A1A1A);
    fillr(fb, w, h, 66, 346, 3, 3, 0xffFFF1A8);
    fillr(fb, w, h, 72, 364, 56, 16, 0xff101018);
    fillr(fb, w, h, 76, 354, 50, 18, G6_COL_MAGENTA);
    fillr(fb, w, h, 76, 354, 50, 4, lite(G6_COL_MAGENTA));
    fillr(fb, w, h, 84, 358, 12, 8, 0xff203848);
    fillr(fb, w, h, 102, 358, 12, 8, 0xff203848);
    fillr(fb, w, h, 80, 368, 8, 6, 0xff1A1A1A);
    fillr(fb, w, h, 110, 368, 8, 6, 0xff1A1A1A);
    draw_gator_art(fb, w, h, 140, 370, 256);
    fillr(fb, w, h, 148, 18, 86, 40, G6_COL_MAGENTA);
    g6_text5(fb, w, h, 158, 28, "NEON", 2, G6_COL_WHITE);
    disc(fb, w, h, 210, 108, 16, G6_COL_ORANGE);
    fillr(fb, w, h, 0, 392, w, 40, 0xEE0A0618);
    text_ol(fb, w, h, 36, 400, "TAP TO START", 2,
            pulse ? G6_COL_WHITE : G6_COL_PINK, G6_COL_OUTLINE);
    g6_text5(fb, w, h, 8, 418, G6_TAG, 1, 0xffC9ADA7);
}

static void draw_title(G6Game *g, uint32_t *fb, int w, int h)
{
    unsigned f = g->frames;
    int local, fade, scene;
    if (f < (unsigned)G6_FADE_TICKS) {
        fade = (int)f * 256 / G6_FADE_TICKS;
        scene_sunset(fb, w, h, 0, fade);
        return;
    }
    f -= (unsigned)G6_FADE_TICKS;
    if (f < (unsigned)(G6_N_SCENES * G6_SCENE_TICKS)) {
        scene = (int)(f / (unsigned)G6_SCENE_TICKS);
        local = (int)(f % (unsigned)G6_SCENE_TICKS);
        fade = 256;
        if (local < 8) fade = local * 256 / 8;
        if (local > G6_SCENE_TICKS - 8) fade = (G6_SCENE_TICKS - local) * 256 / 8;
        if (scene == 0) scene_sunset(fb, w, h, local, fade);
        else if (scene == 1) scene_neon(fb, w, h, local, fade);
        else if (scene == 2) scene_causeway(fb, w, h, local, fade);
        else scene_duo(fb, w, h, local, fade);
        return;
    }
    f -= (unsigned)(G6_N_SCENES * G6_SCENE_TICKS);
    if (f < (unsigned)G6_LOGO_TICKS) {
        fade = (int)f * 256 / 24;
        if (fade > 256) fade = 256;
        draw_logo_vi(fb, w, h, fade);
        return;
    }
    draw_menu(g, fb, w, h);
}

static int map_tile(const G6Game *g, int tx, int ty)
{
    if (tx < 0 || ty < 0 || tx >= G6_MAP_W || ty >= G6_MAP_H) return T_OCEAN;
    return g->map[ty][tx];
}

static int is_bldg(int t)
{
    return t == T_BUILD || t == T_ROOF || t == T_NEON || t == T_SHACK;
}

static int near_neon(const G6Game *g, int tx, int ty)
{
    int dx, dy;
    for (dy = -2; dy <= 2; dy++)
        for (dx = -2; dx <= 2; dx++)
            if (map_tile(g, tx + dx, ty + dy) == T_NEON) return 1;
    return 0;
}

static int view_night(const G6Game *g)
{
    int ty;
    if (!g) return 0;
    ty = (int)(g->ent[g->who & 1].y >> 8) / G6_TILE;
    return ty >= 26;
}

static uint32_t roof_of(int tx, int ty)
{
    return ROOF_PAL[((tx / 8) * 3 + (ty / 8) * 5) & 7];
}

static uint32_t tile_col(const G6Game *g, int t, int x, int y)
{
    int hsh = (x * 13 + y * 7) & 7;
    int night = (t == T_NEON) || (y >= 30) || near_neon(g, x, y);
    uint32_t c;
    switch (t) {
    case T_OCEAN: c = ((g->tick_n + x + y) & 8) ? 0xff148C9A : 0xff1EC8C0; break;
    case T_SAND: c = hsh & 1 ? G6_COL_SAND : 0xffE8B56A; break;
    case T_GRASS: c = hsh ? 0xff4A9A4A : 0xff3D8C40; break;
    case T_PARK: c = 0xff2F7A3A; break;
    case T_SWAMP: c = hsh & 1 ? 0xff234A2C : 0xff1B3A24; break;
    case T_ROAD: c = G6_COL_ASPHALT; break;
    case T_WALK: c = 0xffD4C4B0; break;
    case T_BUILD:
    case T_ROOF: c = roof_of(x, y); break;
    case T_BRIDGE: c = 0xff6A6458; break;
    case T_DOCK: c = 0xff8A6A44; break;
    case T_PALM: c = 0xff3D8C4A; break;
    case T_NEON: c = 0xff241028; break;
    case T_SHACK: c = 0xffC9A227; break;
    default: c = 0xff203018; break;
    }
    if (t == T_SAND || t == T_OCEAN || t == T_DOCK)
        c = lerp(c, G6_COL_ORANGE, 1, 8);
    if (night) {
        uint32_t nv = 0xff121028;
        if (t == T_OCEAN) nv = 0xff0A2038;
        if (t == T_ROAD || t == T_BRIDGE) nv = 0xff1A1828;
        if (t == T_WALK) nv = 0xff2A2438;
        if (t == T_NEON) nv = 0xff1A1028;
        c = lerp(c, nv, 3, 5);
    }
    return c | 0xff000000u;
}

static void draw_palm_top(uint32_t *fb, int w, int h, int ox, int oy)
{
    fillr(fb, w, h, ox + 10, oy + 12, 7, 4, 0x88141410);
    fillr(fb, w, h, ox + 7, oy + 4, 3, 12, 0xff8A5A28);
    fillr(fb, w, h, ox + 6, oy + 4, 1, 12, 0xffC08040);
    fillr(fb, w, h, ox + 1, oy + 1, 14, 4, 0xff1F6A32);
    fillr(fb, w, h, ox + 3, oy - 1, 10, 4, 0xff2F8A40);
    fillr(fb, w, h, ox + 8, oy + 3, 8, 3, 0xff1F6A32);
    fillr(fb, w, h, ox - 1, oy + 3, 6, 3, 0xff246A38);
}

static void draw_parked(uint32_t *fb, int w, int h, int ox, int oy, uint32_t col, int horiz)
{
    fillr(fb, w, h, ox + 2, oy + 10, 12, 4, 0x66101018);
    if (horiz) {
        fillr(fb, w, h, ox + 1, oy + 4, 14, 8, dim(col));
        fillr(fb, w, h, ox + 2, oy + 4, 12, 6, col);
        fillr(fb, w, h, ox + 2, oy + 4, 12, 2, lite(col));
        fillr(fb, w, h, ox + 4, oy + 6, 4, 3, 0xff203848);
        fillr(fb, w, h, ox + 9, oy + 6, 4, 3, 0xff203848);
    } else {
        fillr(fb, w, h, ox + 4, oy + 1, 8, 14, dim(col));
        fillr(fb, w, h, ox + 5, oy + 2, 6, 12, col);
        fillr(fb, w, h, ox + 5, oy + 2, 2, 12, lite(col));
        fillr(fb, w, h, ox + 6, oy + 4, 4, 3, 0xff203848);
        fillr(fb, w, h, ox + 6, oy + 9, 4, 3, 0xff203848);
    }
}

static void draw_tile(const G6Game *g, uint32_t *fb, int w, int h, int tx, int ty, int ox, int oy, int night)
{
    int t = map_tile(g, tx, ty);
    uint32_t c = tile_col(g, t, tx, ty);
    int n, south, east, west, north, facade;
    if (night && t != T_NEON) {
        if (t == T_ROAD || t == T_BRIDGE) c = 0xff1C1828;
        else if (t == T_WALK) c = 0xff2C2438;
        else if (t == T_OCEAN) c = 0xff0A2840;
        else if (t == T_SAND) c = 0xff4A3A40;
        else if (t == T_GRASS || t == T_PARK || t == T_PALM) c = 0xff1A3020;
        else if (is_bldg(t)) c = dim(roof_of(tx, ty));
        else c = lerp(c, 0xff121028, 3, 5);
    }
    fillr(fb, w, h, ox, oy, G6_TILE, G6_TILE, c);
    if (t == T_ROAD || t == T_BRIDGE) {
        int hr = map_tile(g, tx - 1, ty) == t || map_tile(g, tx + 1, ty) == t;
        int vr = map_tile(g, tx, ty - 1) == t || map_tile(g, tx, ty + 1) == t;
        uint32_t edge = night ? 0xff3A3048 : 0xffC8C0B0;
        uint32_t dash = night ? 0xff8A7840 : 0xffE8D060;
        fillr(fb, w, h, ox, oy, G6_TILE, 1, 0xff2A2824);
        fillr(fb, w, h, ox, oy + 15, G6_TILE, 1, 0xff2A2824);
        if (hr) {
            fillr(fb, w, h, ox, oy + 1, G6_TILE, 1, edge);
            fillr(fb, w, h, ox, oy + 14, G6_TILE, 1, edge);
            if ((ty & 1) == 0) fillr(fb, w, h, ox + 3, oy + 7, 7, 2, dash);
        }
        if (vr) {
            fillr(fb, w, h, ox + 1, oy, 1, G6_TILE, edge);
            fillr(fb, w, h, ox + 14, oy, 1, G6_TILE, edge);
            if ((tx & 1) == 0) fillr(fb, w, h, ox + 7, oy + 3, 2, 7, dash);
        }
        if (hr && vr) {
            fillr(fb, w, h, ox + 2, oy + 2, 12, 2, edge);
            fillr(fb, w, h, ox + 2, oy + 12, 12, 2, edge);
        }
    }
    if (t == T_WALK) {
        uint32_t kerb_l = night ? 0xff3A3448 : 0xffF0E8D8;
        uint32_t kerb_d = night ? 0xff1A1420 : 0xffB0A090;
        fillr(fb, w, h, ox, oy, 2, G6_TILE, kerb_d);
        fillr(fb, w, h, ox + 14, oy, 2, G6_TILE, kerb_l);
        fillr(fb, w, h, ox, oy, G6_TILE, 1, kerb_l);
        fillr(fb, w, h, ox, oy + 15, G6_TILE, 1, kerb_d);
        for (n = 4; n < 12; n += 4)
            fillr(fb, w, h, ox + 3, oy + n, 10, 1, night ? 0xff241C30 : 0xffC8B8A0);
        if (is_bldg(map_tile(g, tx, ty - 1)))
            fillr(fb, w, h, ox, oy, G6_TILE, 3, 0x66141420);
        if (((tx * 5 + ty * 3) % 11) == 2 &&
            (map_tile(g, tx, ty + 1) == T_ROAD || map_tile(g, tx, ty - 1) == T_ROAD))
            draw_parked(fb, w, h, ox, oy, CAR_COL[(tx + ty) % 6], 1);
        else if (((tx * 5 + ty * 3) % 11) == 7 &&
                 (map_tile(g, tx + 1, ty) == T_ROAD || map_tile(g, tx - 1, ty) == T_ROAD))
            draw_parked(fb, w, h, ox, oy, CAR_COL[(tx + ty) % 6], 0);
        else if (((tx + ty * 2) % 6) == 0)
            draw_palm_top(fb, w, h, ox, oy);
    }
    if (t == T_SAND) {
        pset(fb, w, h, ox + (tx & 7), oy + (ty & 7), 0xffD4A060);
        pset(fb, w, h, ox + 9, oy + 4, 0xffF8E0B0);
        if (map_tile(g, tx, ty - 1) == T_OCEAN || map_tile(g, tx, ty + 1) == T_OCEAN)
            fillr(fb, w, h, ox, oy + ((map_tile(g, tx, ty - 1) == T_OCEAN) ? 0 : 13), G6_TILE, 3, G6_COL_FOAM);
        if (map_tile(g, tx - 1, ty) == T_OCEAN)
            fillr(fb, w, h, ox, oy, 3, G6_TILE, G6_COL_FOAM);
    }
    if (t == T_GRASS || t == T_PARK) {
        pset(fb, w, h, ox + 3, oy + 5, 0xff2A6A32);
        pset(fb, w, h, ox + 11, oy + 10, 0xff6ABA50);
        pset(fb, w, h, ox + 7, oy + 2, 0xffE8D060);
    }
    if (t == T_BUILD || t == T_ROOF || t == T_NEON) {
        uint32_t pal = roof_of(tx, ty);
        uint32_t roof;
        south = map_tile(g, tx, ty + 1);
        north = map_tile(g, tx, ty - 1);
        east = map_tile(g, tx + 1, ty);
        west = map_tile(g, tx - 1, ty);
        facade = !is_bldg(south);
        if (night)
            pal = (t == T_NEON) ? 0xff1A1028 : 0xff241830;
        else if (t == T_NEON)
            pal = 0xff8A6880;
        roof = pal;
        fillr(fb, w, h, ox, oy, G6_TILE, G6_TILE, roof);
        if (!is_bldg(north)) fillr(fb, w, h, ox, oy, G6_TILE, 2, lite(roof));
        if (!is_bldg(west)) fillr(fb, w, h, ox, oy, 2, G6_TILE, lite(roof));
        if (!is_bldg(east)) fillr(fb, w, h, ox + 14, oy, 2, G6_TILE, dim(roof));
        if (!is_bldg(south)) fillr(fb, w, h, ox, oy + 14, G6_TILE, 2, dim(roof));
        if (!facade) {
            if (((tx * 7 + ty * 3) & 7) == 0) {
                fillr(fb, w, h, ox + 4, oy + 4, 7, 5, night ? 0xff3A3848 : 0xff6A6870);
                fillr(fb, w, h, ox + 5, oy + 3, 5, 2, night ? 0xff5A5868 : 0xff8A8890);
            }
            if (((tx + ty * 5) & 7) == 3)
                fillr(fb, w, h, ox + 3, oy + 8, 4, 4, night ? 0xff2A2838 : 0xff4A4850);
            if (((tx * 3 + ty) & 11) == 2)
                fillr(fb, w, h, ox + 6, oy + 6, 4, 4, night ? 0xffFFE8A0 : 0xff88A8C8);
            if (t == T_NEON || night) {
                uint32_t neon = ((g->tick_n / 6 + tx) & 1) ? G6_COL_MAGENTA : G6_COL_TEAL;
                if (((tx + ty) & 3) == 0)
                    fillr(fb, w, h, ox + 1, oy + 1, 14, 2, neon);
            }
        } else {
            uint32_t wall = (t == T_NEON || night) ? 0xff1A1028 : lerp(pal, 0xffC89080, 1, 2);
            fillr(fb, w, h, ox, oy + 5, G6_TILE, 11, wall);
            fillr(fb, w, h, ox, oy, G6_TILE, 5, (t == T_NEON || night) ? 0xff2A1840 : 0xffF0E0D0);
            fillr(fb, w, h, ox + 2, oy + 8, 4, 5, night || t == T_NEON ? 0xffFFE8A0 : 0xff3A2848);
            fillr(fb, w, h, ox + 10, oy + 8, 4, 5, night || t == T_NEON ? 0xffFFE8A0 : 0xff3A2848);
            if (t == T_NEON || night) {
                uint32_t neon = ((g->tick_n / 6 + tx) & 1) ? G6_COL_MAGENTA : G6_COL_TEAL;
                fillr(fb, w, h, ox + 1, oy + 3, 14, 3, neon);
            }
        }
    }
    if (t == T_PALM)
        draw_palm_top(fb, w, h, ox, oy);
    if (t == T_SWAMP && ((tx + ty + (int)g->tick_n) & 15) == 0)
        fillr(fb, w, h, ox + 5, oy + 6, 3, 2, 0xff7EE0A8);
    if (t == T_OCEAN) {
        int wv = (int)((g->tick_n / 2 + tx * 3 + ty) & 7);
        fillr(fb, w, h, ox + wv, oy + 8, 6, 1, night ? 0xff3A6880 : 0xffA0E4F0);
        fillr(fb, w, h, ox + ((wv + 4) & 7), oy + 4, 4, 1, night ? 0xff2A5068 : G6_COL_FOAM);
        if (map_tile(g, tx, ty + 1) == T_SAND)
            fillr(fb, w, h, ox, oy + 12, G6_TILE, 4, G6_COL_FOAM);
    }
    if (t == T_SHACK) {
        fillr(fb, w, h, ox + 2, oy + 4, 12, 10, 0xff8A5A20);
        fillr(fb, w, h, ox + 1, oy + 2, 14, 3, 0xff5A3010);
        fillr(fb, w, h, ox + 6, oy + 7, 3, 5, 0xff3A2010);
        fillr(fb, w, h, ox + 3, oy + 14, 2, 2, 0xff5A3010);
        fillr(fb, w, h, ox + 11, oy + 14, 2, 2, 0xff5A3010);
    }
    if (t == T_BRIDGE) {
        fillr(fb, w, h, ox, oy + 2, G6_TILE, 1, 0xffF0E8D0);
        fillr(fb, w, h, ox, oy + 13, G6_TILE, 1, 0xffF0E8D0);
    }
}

static void draw_person(uint32_t *fb, int w, int h, int x, int y, uint32_t body, uint32_t hair, int who_id)
{
    fillr(fb, w, h, x - 3, y + 4, 3, 4, 0xff2A2030);
    fillr(fb, w, h, x, y + 4, 3, 4, 0xff2A2030);
    fillr(fb, w, h, x - 3, y - 2, 7, 8, body);
    fillr(fb, w, h, x - 2, y - 1, 5, 2, lite(body));
    fillr(fb, w, h, x - 3, y - 6, 7, 5, 0xffE8C4A8);
    fillr(fb, w, h, x - 3, y - 8, 7, 3, hair);
    if (who_id == 0) fillr(fb, w, h, x - 4, y - 7, 9, 3, hair);
    pset(fb, w, h, x - 1, y - 5, 0xff1A1A1A);
    pset(fb, w, h, x + 2, y - 5, 0xff1A1A1A);
}

static void draw_car(uint32_t *fb, int w, int h, int x, int y, uint8_t ang, uint32_t col, int cop, int mine)
{
    int dir = ((ang + 16) >> 5) & 7;
    uint32_t hi = lite(col);
    uint32_t lo = dim(col);
    uint32_t win = 0xff203848;
    uint32_t light = ((ang + x) & 8) ? G6_COL_MAGENTA : G6_COL_TEAL;
    fillr(fb, w, h, x - 8, y + 4, 16, 4, 0x66101018);
    if (mine) fillr(fb, w, h, x - 10, y - 9, 20, 18, 0x44FFFFFF);
    if (cop) col = 0xffF4F1DE;
    if (dir == 0 || dir == 4) {
        fillr(fb, w, h, x - 10, y - 5, 20, 11, lo);
        fillr(fb, w, h, x - 9, y - 4, 18, 9, col);
        fillr(fb, w, h, x - 8, y - 4, 18, 2, hi);
        fillr(fb, w, h, x - 7, y - 2, 6, 5, win);
        fillr(fb, w, h, x + 1, y - 2, 6, 5, win);
        fillr(fb, w, h, x - 10, y - 5, 3, 3, 0xff1A1A1A);
        fillr(fb, w, h, x - 10, y + 2, 3, 3, 0xff1A1A1A);
        fillr(fb, w, h, x + 7, y - 5, 3, 3, 0xff1A1A1A);
        fillr(fb, w, h, x + 7, y + 2, 3, 3, 0xff1A1A1A);
        if (dir == 0) {
            pset(fb, w, h, x + 9, y - 2, 0xffFFF1A8);
            pset(fb, w, h, x + 9, y + 2, 0xffFFF1A8);
            pset(fb, w, h, x - 9, y - 1, 0xffff4050);
        } else {
            pset(fb, w, h, x - 9, y - 2, 0xffFFF1A8);
            pset(fb, w, h, x - 9, y + 2, 0xffFFF1A8);
            pset(fb, w, h, x + 9, y - 1, 0xffff4050);
        }
        if (cop) fillr(fb, w, h, x - 3, y - 6, 6, 2, light);
    } else if (dir == 2 || dir == 6) {
        fillr(fb, w, h, x - 5, y - 10, 11, 20, lo);
        fillr(fb, w, h, x - 4, y - 9, 9, 18, col);
        fillr(fb, w, h, x - 4, y - 9, 2, 18, hi);
        fillr(fb, w, h, x - 3, y - 7, 6, 5, win);
        fillr(fb, w, h, x - 3, y + 1, 6, 5, win);
        fillr(fb, w, h, x - 5, y - 10, 3, 3, 0xff1A1A1A);
        fillr(fb, w, h, x + 2, y - 10, 3, 3, 0xff1A1A1A);
        fillr(fb, w, h, x - 5, y + 7, 3, 3, 0xff1A1A1A);
        fillr(fb, w, h, x + 2, y + 7, 3, 3, 0xff1A1A1A);
        if (dir == 6) {
            pset(fb, w, h, x - 2, y - 9, 0xffFFF1A8);
            pset(fb, w, h, x + 2, y - 9, 0xffFFF1A8);
        } else {
            pset(fb, w, h, x - 2, y + 8, 0xffFFF1A8);
            pset(fb, w, h, x + 2, y + 8, 0xffFFF1A8);
        }
        if (cop) fillr(fb, w, h, x - 3, y - 2, 6, 3, light);
    } else {
        fillr(fb, w, h, x - 7, y - 7, 15, 15, lo);
        fillr(fb, w, h, x - 6, y - 6, 13, 13, col);
        fillr(fb, w, h, x - 6, y - 6, 13, 2, hi);
        fillr(fb, w, h, x - 4, y - 4, 5, 5, win);
        fillr(fb, w, h, x, y, 4, 4, win);
        fillr(fb, w, h, x - 7, y - 7, 3, 3, 0xff1A1A1A);
        fillr(fb, w, h, x + 4, y + 4, 3, 3, 0xff1A1A1A);
        if (cop) fillr(fb, w, h, x - 2, y - 7, 5, 2, light);
    }
}

static void draw_gator(uint32_t *fb, int w, int h, int x, int y)
{
    fillr(fb, w, h, x - 4, y + 2, 12, 3, 0xff1A3018);
    fillr(fb, w, h, x - 8, y - 3, 16, 6, 0xff3A6A32);
    fillr(fb, w, h, x - 7, y - 4, 12, 2, 0xff4A7A40);
    fillr(fb, w, h, x + 5, y - 2, 8, 4, 0xff2A4A28);
    pset(fb, w, h, x + 12, y - 1, 0xffE8E050);
    pset(fb, w, h, x + 10, y - 3, 0xff1A1A1A);
}

static void world_to_screen(const G6Game *g, int32_t x, int32_t y, int *sx, int *sy)
{
    *sx = (int)(x >> 8) - (int)(g->camx >> 8);
    *sy = (int)(y >> 8) - (int)(g->camy >> 8);
}

static int32_t tcen(int t)
{
    return (int32_t)(t * G6_TILE + G6_TILE / 2) << 8;
}

static void draw_world(G6Game *g, uint32_t *fb, int w, int h)
{
    int camx = (int)(g->camx >> 8);
    int camy = (int)(g->camy >> 8);
    int tx0 = camx / G6_TILE - 1;
    int ty0 = camy / G6_TILE - 1;
    int tx1 = (camx + w) / G6_TILE + 1;
    int ty1 = (camy + h) / G6_TILE + 1;
    int tx, ty, i;
    int night = view_night(g);
    if (tx0 < 0) tx0 = 0;
    if (ty0 < 0) ty0 = 0;
    if (tx1 >= G6_MAP_W) tx1 = G6_MAP_W - 1;
    if (ty1 >= G6_MAP_H) ty1 = G6_MAP_H - 1;
    fillr(fb, w, h, 0, 0, w, h, night ? 0xff0A2840 : 0xff148C9A);
    for (ty = ty0; ty <= ty1; ty++)
        for (tx = tx0; tx <= tx1; tx++)
            draw_tile(g, fb, w, h, tx, ty, tx * G6_TILE - camx, ty * G6_TILE - camy, night);
    if (g->mission == 1) {
        int sx, sy;
        int32_t mx = (g->mstage == 1) ? tcen(30) : tcen(18);
        int32_t my = (g->mstage == 1) ? tcen(50) : tcen(10);
        world_to_screen(g, mx, my, &sx, &sy);
        fillr(fb, w, h, sx - 4, sy - 4, 8, 8, G6_COL_STAR);
    }
    if (g->mission == 3 && g->cp_at < g->cp_n) {
        int sx, sy;
        world_to_screen(g, g->cp_x[g->cp_at], g->cp_y[g->cp_at], &sx, &sy);
        fillr(fb, w, h, sx - 5, sy - 5, 10, 10, G6_COL_STAR);
        fillr(fb, w, h, sx - 2, sy - 2, 4, 4, G6_COL_MAGENTA);
    }
    for (i = G6_MAX_ENT - 1; i >= 0; i--) {
        G6Ent *e = &g->ent[i];
        int sx, sy;
        if (!(e->flags & F_ALIVE)) continue;
        if ((e->kind == K_NIA || e->kind == K_WADE) && (e->flags & F_INCAR)) continue;
        world_to_screen(g, e->x, e->y, &sx, &sy);
        if (sx < -20 || sy < -20 || sx > w + 20 || sy > h + 20) continue;
        if (e->kind == K_CAR || e->kind == K_POL)
            draw_car(fb, w, h, sx, sy, e->ang,
                     e->kind == K_POL ? 0xffF4F1DE : CAR_COL[e->pal % 6],
                     e->kind == K_POL,
                     (g->ent[g->who & 1].car == (uint8_t)i) || (e->flags & F_MARK));
        else if (e->kind == K_GATOR)
            draw_gator(fb, w, h, sx, sy);
        else if (e->kind == K_PED)
            draw_person(fb, w, h, sx, sy, 0xff8A93A8, 0xff3A2A20, 2);
        else if (e->kind == K_NIA)
            draw_person(fb, w, h, sx, sy, G6_COL_MAGENTA, 0xff2A1A14, 0);
        else if (e->kind == K_WADE)
            draw_person(fb, w, h, sx, sy, 0xffF8F4EE, 0xff1A1A1A, 1);
    }
}

static void draw_minimap(G6Game *g, uint32_t *fb, int w, int h)
{
    const int cx = 42, cy = 292, r = 28;
    int x, y, i;
    (void)w; (void)h;
    disc(fb, w, h, cx, cy, r + 3, G6_COL_OUTLINE);
    disc(fb, w, h, cx, cy, r + 1, G6_COL_WHITE);
    disc(fb, w, h, cx, cy, r, 0xff1A1820);
    for (y = -r; y <= r; y++) {
        for (x = -r; x <= r; x++) {
            int tx, ty;
            if (x * x + y * y > r * r) continue;
            tx = (x + r) * G6_MAP_W / (r * 2);
            ty = (y + r) * G6_MAP_H / (r * 2);
            pset(fb, w, h, cx + x, cy + y, tile_col(g, map_tile(g, tx, ty), tx, ty));
        }
    }
    for (i = 0; i < G6_MAX_ENT; i++) {
        int mx, my, dx, dy;
        if (!(g->ent[i].flags & F_ALIVE)) continue;
        mx = (int)((g->ent[i].x >> 8) * (r * 2) / G6_WORLD_W) - r;
        my = (int)((g->ent[i].y >> 8) * (r * 2) / G6_WORLD_H) - r;
        dx = mx; dy = my;
        if (dx * dx + dy * dy > (r - 2) * (r - 2)) continue;
        if (g->ent[i].kind == K_POL) pset(fb, w, h, cx + dx, cy + dy, G6_COL_TEAL);
        else if (i == (g->who & 1)) {
            fillr(fb, w, h, cx + dx - 1, cy + dy - 1, 3, 3, G6_COL_WHITE);
        } else if (g->ent[i].kind == K_CAR) pset(fb, w, h, cx + dx, cy + dy, G6_COL_ORANGE);
    }
}

static char *put_s(char *o, const char *s)
{
    while (*s) *o++ = *s++;
    *o = 0;
    return o;
}

static char *put_u(char *o, unsigned v)
{
    char t[12];
    int n = 0;
    do { t[n++] = (char)('0' + (v % 10u)); v /= 10u; } while (v);
    while (n) *o++ = t[--n];
    *o = 0;
    return o;
}

static void star5(uint32_t *fb, int w, int h, int x, int y, uint32_t c)
{
    fillr(fb, w, h, x + 3, y, 2, 8, c);
    fillr(fb, w, h, x, y + 3, 8, 2, c);
    pset(fb, w, h, x + 1, y + 1, c);
    pset(fb, w, h, x + 6, y + 1, c);
    pset(fb, w, h, x + 1, y + 6, c);
    pset(fb, w, h, x + 6, y + 6, c);
}

static void draw_hud(G6Game *g, uint32_t *fb, int w, int h)
{
    char line[48];
    char *o;
    int i, stars = g->wanted, len;
    int dx, dy, r, r2, r0;
    o = put_s(line, "$");
    o = put_u(o, (unsigned)g->money);
    len = 0;
    while (line[len]) len++;
    g6_text5(fb, w, h, 148, 5, line, 1, G6_COL_MONEY);
    for (i = 0; i < 5; i++)
        star5(fb, w, h, 148 + i * 10, 16, i < stars ? G6_COL_STAR : 0xff3A3040);
    g6_text5(fb, w, h, 4, 5, g->who ? "WADE" : "NIA", 1, G6_COL_MAGENTA);
    o = put_s(line, "HP ");
    put_u(o, (unsigned)g->hp);
    g6_text5(fb, w, h, 4, 15, line, 1, g->hp > 30 ? G6_COL_WHITE : G6_COL_ORANGE);
    g6_text5(fb, w, h, 216, 5, "II", 1, G6_COL_WHITE);
    if (g->ticker[0])
        g6_text5(fb, w, h, 4, 322, g->ticker, 1, G6_COL_TEAL);
    r = 34;
    r2 = r * r;
    r0 = (r - 3) * (r - 3);
    for (dy = -r; dy <= r; dy++)
        for (dx = -r; dx <= r; dx++) {
            int d2 = dx * dx + dy * dy;
            if (d2 <= r2 && d2 >= r0)
                pset_a(fb, w, h, JOY_CX + dx, JOY_CY + dy, 0xffD8C8E0, 96);
        }
    disc_a(fb, w, h, JOY_CX + g->joyx / 16, JOY_CY + g->joyy / 16, 7, G6_COL_WHITE, 180);
    disc_a(fb, w, h, BTN_AX, BTN_AY, 16, G6_COL_MAGENTA, 120);
    g6_text5(fb, w, h, BTN_AX - 6, BTN_AY - 3, "IN", 1, G6_COL_WHITE);
    disc_a(fb, w, h, BTN_BX, BTN_BY, 16, 0xff2A6A88, 120);
    g6_text5(fb, w, h, BTN_BX - 9, BTN_BY - 3, "BRK", 1, G6_COL_PINK);
    disc_a(fb, w, h, BTN_SX, BTN_SY, 14, 0xff2A8A68, 120);
    g6_text5(fb, w, h, BTN_SX - 6, BTN_SY - 3, "SW", 1, G6_COL_WHITE);
    draw_minimap(g, fb, w, h);
}

static void draw_map_view(G6Game *g, uint32_t *fb, int w, int h)
{
    int x, y, px, py;
    fillr(fb, w, h, 0, 0, w, h, G6_COL_OUTLINE);
    g6_text5(fb, w, h, 16, 8, "MANGROVE KEY", 2, G6_COL_MAGENTA);
    for (y = 0; y < G6_MAP_H; y++) {
        py = 36 + y * 3;
        if (py >= h - 40) break;
        for (x = 0; x < G6_MAP_W; x++) {
            px = 12 + x * 3;
            fillr(fb, w, h, px, py, 3, 3, tile_col(g, map_tile(g, x, y), x, y));
        }
    }
    px = 12 + (int)((g->ent[g->who & 1].x >> 8) * 3 / G6_TILE);
    py = 36 + (int)((g->ent[g->who & 1].y >> 8) * 3 / G6_TILE);
    fillr(fb, w, h, px - 1, py - 1, 4, 4, G6_COL_WHITE);
    g6_text5(fb, w, h, 8, h - 28, "PALM DECK  NEON WHARF", 1, G6_COL_TEAL);
    g6_text5(fb, w, h, 8, h - 16, "ALLIGATOR FLATS  CAUSEWAY", 1, 0xff7EE0A8);
}

static void draw_overlay_msg(uint32_t *fb, int w, int h, const char *a, const char *b)
{
    fillr(fb, w, h, 10, 150, w - 20, 120, 0xEE0A0618);
    g6_text5(fb, w, h, 40, 168, a, 3, G6_COL_MAGENTA);
    g6_text5(fb, w, h, 20, 210, b, 1, G6_COL_WHITE);
}

void g6_draw(G6Game *g, uint32_t *fb, int w, int h)
{
    if (!fb || w <= 0 || h <= 0) return;
    if (!g || g->low_mem || g->mode == G6_SHORT) {
        static const char *lines[] = {
            "NOT ENOUGH MEMORY",
            "PAUSE MUSIC",
            "OR BLUETOOTH",
            "THEN REOPEN"
        };
        g6_notice_draw(fb, w, h, lines, 4);
        return;
    }
    if (g->mode == G6_TITLE_MODE) {
        draw_title(g, fb, w, h);
        return;
    }
    if (g->view_map) {
        draw_map_view(g, fb, w, h);
        return;
    }
    draw_world(g, fb, w, h);
    draw_hud(g, fb, w, h);
    if (g->mode == G6_BUSTED)
        draw_overlay_msg(fb, w, h, "BUSTED", "TAP TO CONTINUE");
    else if (g->mode == G6_WASTED)
        draw_overlay_msg(fb, w, h, "WASTED", "TAP TO CONTINUE");
    else if (g->mode == G6_PAUSE) {
        fillr(fb, w, h, 16, 120, w - 32, 180, 0xEE0A0618);
        g6_text5(fb, w, h, 40, 136, "PAUSED", 2, G6_COL_WHITE);
        g6_text5(fb, w, h, 28, 170, "RESUME", 1, 0xff7EE0A8);
        g6_text5(fb, w, h, 28, 188, "JOB", 1, G6_COL_STAR);
        g6_text5(fb, w, h, 28, 206, "HOME", 1, G6_COL_MAGENTA);
        g6_text5(fb, w, h, 28, 230, G6_CITY, 1, G6_COL_TEAL);
        g6_text5(fb, w, h, 28, 248, G6_TAG, 1, 0xffC9ADA7);
    }
}
