/*
 * Original Mangrove Key caper. No third-party game assets, fonts, maps or names.
 * Integer math only (Thumb-safe). 30 ms tick.
 */
#include "gta6_game.h"
#include <string.h>

#define F_ALIVE 1u
#define F_INCAR 2u
#define F_MARK  4u
#define F_PASS  8u

#define K_NIA   1
#define K_WADE  2
#define K_PED   3
#define K_CAR   4
#define K_POL   5
#define K_GATOR 6

#define JOY_CX 48
#define JOY_CY 378
#define JOY_R  36
#define BTN_AX 198
#define BTN_AY 350
#define BTN_BX 198
#define BTN_BY 402
#define BTN_SX 148
#define BTN_SY 376
#define PAUSE_X 200
#define PAUSE_Y 6

static const int16_t COST[65] = {
    256,256,256,255,255,254,253,252,251,250,248,247,245,243,241,239,237,
    234,231,229,226,223,220,216,213,209,206,202,198,194,190,185,181,177,
    172,167,162,157,152,147,142,137,132,126,121,115,109,104,98,92,86,80,
    74,68,62,56,50,44,38,31,25,19,13,6,0
};

static const char *const YAP[] = {
    "YAP: hatchback stolen on Palm Deck",
    "KEY MAN WRESTLES GATOR FOR LAST BATTERY",
    "KEY MAN LISTS A NANO AS A DEPENDENT",
    "KEY MAN PAYS CAUSEWAY TOLL IN GRAPES",
    "HOLD 96.1: six years of hold music",
    "SWAMP SHUFFLE: all gators, no brakes",
    "CLICK FM: sunsets delayed until Friday",
    "YAP: neon outage on the wharf"
};
#define YAP_N 8

static const char *const RADIO[] = {
    "HOLD 96.1", "SWAMP SHUFFLE", "CLICK FM", "NANO HITS", "GATOR TALK"
};

static int16_t icos(uint8_t a)
{
    int q = (a >> 6) & 3;
    int i = a & 63;
    int v = COST[i];
    if (q == 0) return (int16_t)v;
    if (q == 1) return (int16_t)-COST[64 - i];
    if (q == 2) return (int16_t)-v;
    return (int16_t)COST[64 - i];
}

static int16_t isin(uint8_t a)
{
    return icos((uint8_t)(a - 64u));
}

static int isqrt(int n)
{
    unsigned x, y, z;
    if (n <= 0) return 0;
    x = (unsigned)n;
    y = x;
    for (;;) {
        z = (y + x / y) >> 1;
        if (z >= y) return (int)y;
        y = z;
    }
}

static void clamp_ent(G6Ent *e)
{
    int32_t minv = 8 << 8;
    int32_t maxx = (G6_WORLD_W - 8) << 8;
    int32_t maxy = (G6_WORLD_H - 8) << 8;
    if (!e) return;
    if (e->x < minv) e->x = minv;
    if (e->y < minv) e->y = minv;
    if (e->x > maxx) e->x = maxx;
    if (e->y > maxy) e->y = maxy;
}

static void map_stick(int dx, int dy, int *ax, int *ay)
{
    int d2 = dx * dx + dy * dy;
    int r2 = JOY_R * JOY_R;
    int d, dead, span, lin, mag;
    if (d2 > r2) {
        d = isqrt(d2);
        if (d < 1) d = 1;
        dx = dx * JOY_R / d;
        dy = dy * JOY_R / d;
        d = JOY_R;
    } else {
        d = isqrt(d2);
    }
    dead = (JOY_R * G6_JOY_DEAD_PCT) / 100;
    if (d <= dead) {
        *ax = 0;
        *ay = 0;
        return;
    }
    span = JOY_R - dead;
    if (span < 1) span = 1;
    lin = (d - dead) * 256 / span;
    if (lin > 256) lin = 256;
    mag = lin * lin / 256;
    *ax = dx * mag / d;
    *ay = dy * mag / d;
}

static uint32_t rnd(G6Game *g)
{
    g->rng = g->rng * 1664525u + 1013904223u;
    return g->rng;
}

static int rni(G6Game *g, int n)
{
    if (n <= 0) return 0;
    return (int)(rnd(g) >> 16) % n;
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

void g6_note(G6Game *g, const char *s)
{
    size_t n = 0;
    if (!g || !s) return;
    while (s[n] && n < sizeof(g->logbuf) - 1) n++;
    memcpy(g->logbuf, s, n);
    g->logbuf[n] = 0;
    g->log_ready = 1;
}

size_t g6_size(void) { return sizeof(G6Game); }

uint32_t g6_pick_block(uint32_t free_b, uint32_t largest)
{
    uint32_t size;
    if (largest > G6_BLOCK_MAX + G6_SLOP) size = G6_BLOCK_MAX;
    else size = (largest > G6_SLOP) ? ((largest - G6_SLOP) & ~0xFFFFu) : 0;
    if (free_b > G6_AFTER_MIN) {
        uint32_t cap = (free_b - G6_AFTER_MIN) & ~0xFFFFu;
        if (size > cap) size = cap;
    } else {
        size = 0;
    }
    if (largest < G6_BLOCK_MIN + G6_SLOP || size < G6_BLOCK_MIN) return 0;
    if (size < (uint32_t)sizeof(G6Game) + 4096u) return 0;
    return size;
}

static int tile(const G6Game *g, int tx, int ty)
{
    if (tx < 0 || ty < 0 || tx >= G6_MAP_W || ty >= G6_MAP_H) return T_OCEAN;
    return g->map[ty][tx];
}

static int pix_tile(const G6Game *g, int32_t x, int32_t y)
{
    return tile(g, (int)(x >> 8) / G6_TILE, (int)(y >> 8) / G6_TILE);
}

static int solid_car(int t)
{
    return t == T_BUILD || t == T_ROOF || t == T_NEON || t == T_OCEAN || t == T_SHACK;
}

static int solid_foot(int t)
{
    return t == T_BUILD || t == T_ROOF || t == T_NEON || t == T_SHACK;
}

static int is_road(int t)
{
    return t == T_ROAD || t == T_BRIDGE || t == T_DOCK;
}

static void set_tile(G6Game *g, int x, int y, int t)
{
    if (x >= 0 && y >= 0 && x < G6_MAP_W && y < G6_MAP_H)
        g->map[y][x] = (uint8_t)t;
}

static void hroad(G6Game *g, int y, int x0, int x1)
{
    int x;
    for (x = x0; x <= x1; x++) {
        int t = tile(g, x, y);
        if (t == T_OCEAN) set_tile(g, x, y, T_BRIDGE);
        else if (t != T_SWAMP) set_tile(g, x, y, T_ROAD);
    }
}

static void vroad(G6Game *g, int x, int y0, int y1)
{
    int y;
    for (y = y0; y <= y1; y++) {
        int t = tile(g, x, y);
        if (t == T_OCEAN) set_tile(g, x, y, T_BRIDGE);
        else if (t != T_SWAMP) set_tile(g, x, y, T_ROAD);
    }
}

static void fill(G6Game *g, int x0, int y0, int x1, int y1, int t)
{
    int x, y;
    for (y = y0; y <= y1; y++)
        for (x = x0; x <= x1; x++)
            set_tile(g, x, y, t);
}

static void gen_map(G6Game *g)
{
    int x, y, bx, by;
    fill(g, 0, 0, G6_MAP_W - 1, G6_MAP_H - 1, T_GRASS);
    fill(g, 0, 0, G6_MAP_W - 1, 7, T_OCEAN);
    fill(g, 0, 8, G6_MAP_W - 1, 12, T_SAND);
    fill(g, 54, 14, G6_MAP_W - 1, G6_MAP_H - 1, T_SWAMP);
    fill(g, 0, 42, G6_MAP_W - 1, 46, T_OCEAN);
    fill(g, 2, 70, 22, 92, T_PARK);
    fill(g, 24, 48, 36, 56, T_DOCK);
    hroad(g, 13, 4, 52);
    hroad(g, 21, 4, 52);
    hroad(g, 29, 4, 52);
    hroad(g, 37, 8, 50);
    hroad(g, 44, 4, 52);
    hroad(g, 52, 4, 50);
    hroad(g, 64, 4, 50);
    vroad(g, 8, 13, 68);
    vroad(g, 16, 13, 68);
    vroad(g, 24, 13, 68);
    vroad(g, 32, 13, 68);
    vroad(g, 40, 13, 68);
    vroad(g, 48, 13, 64);
    fill(g, 28, 42, 44, 46, T_BRIDGE);
    for (by = 14; by < 40; by += 8) {
        for (bx = 9; bx < 48; bx += 8) {
            int hue = (bx + by) & 1 ? T_ROOF : T_BUILD;
            if ((bx + by) % 16 == 7) hue = T_NEON;
            for (y = by + 1; y < by + 7; y++)
                for (x = bx + 1; x < bx + 7; x++) {
                    if (tile(g, x, y) == T_GRASS || tile(g, x, y) == T_SAND)
                        set_tile(g, x, y, (uint8_t)hue);
                }
        }
    }
    for (by = 48; by < 64; by += 8) {
        for (bx = 9; bx < 40; bx += 8) {
            for (y = by + 1; y < by + 7 && y < 64; y++)
                for (x = bx + 1; x < bx + 7; x++)
                    if (tile(g, x, y) == T_GRASS) set_tile(g, x, y, T_ROOF);
        }
    }
    set_tile(g, 18, 10, T_SHACK);
    set_tile(g, 30, 50, T_SHACK);
    for (x = 6; x < 50; x += 5) {
        if (tile(g, x, 10) == T_SAND) set_tile(g, x, 10, T_PALM);
        if (tile(g, x, 9) == T_SAND) set_tile(g, x, 9, T_PALM);
    }
    for (x = 4; x < 20; x += 3)
        for (y = 74; y < 90; y += 4)
            if (tile(g, x, y) == T_PARK) set_tile(g, x, y, T_PALM);
    for (x = 4; x < 52; x++)
        for (y = 13; y < 68; y++) {
            int t = tile(g, x, y);
            if (t == T_GRASS && (tile(g, x - 1, y) == T_ROAD || tile(g, x + 1, y) == T_ROAD ||
                                 tile(g, x, y - 1) == T_ROAD || tile(g, x, y + 1) == T_ROAD))
                set_tile(g, x, y, T_WALK);
        }
}

static int32_t tile_center(int t)
{
    return (int32_t)(t * G6_TILE + G6_TILE / 2) << 8;
}

static void clear_ents(G6Game *g)
{
    memset(g->ent, 0, sizeof g->ent);
}

static int spawn(G6Game *g, int kind, int32_t x, int32_t y, uint8_t ang)
{
    int i;
    for (i = 0; i < G6_MAX_ENT; i++) {
        if (g->ent[i].flags & F_ALIVE) continue;
        memset(&g->ent[i], 0, sizeof g->ent[i]);
        g->ent[i].kind = (uint8_t)kind;
        g->ent[i].x = x;
        g->ent[i].y = y;
        g->ent[i].ang = ang;
        g->ent[i].hp = 100;
        g->ent[i].flags = F_ALIVE;
        g->ent[i].car = 255;
        g->ent[i].pal = (uint8_t)rni(g, 6);
        return i;
    }
    return -1;
}

static int find_road(G6Game *g, int *tx, int *ty)
{
    int n = 0, i;
    for (i = 0; i < 40; i++) {
        int x = 4 + rni(g, 48);
        int y = 13 + rni(g, 52);
        if (is_road(tile(g, x, y))) {
            *tx = x; *ty = y;
            n = 1;
            break;
        }
    }
    return n;
}

static void spawn_world(G6Game *g)
{
    int i, tx, ty;
    clear_ents(g);
    spawn(g, K_NIA, tile_center(16), tile_center(11), 64);
    spawn(g, K_WADE, tile_center(17), tile_center(11), 64);
    g->ent[0].pal = 0;
    g->ent[1].pal = 1;
    for (i = 0; i < 8; i++) {
        if (!find_road(g, &tx, &ty)) break;
        spawn(g, K_CAR, tile_center(tx), tile_center(ty), (uint8_t)(rni(g, 4) * 64));
    }
    for (i = 0; i < 8; i++) {
        tx = 6 + rni(g, 44);
        ty = 14 + rni(g, 50);
        if (tile(g, tx, ty) == T_WALK || tile(g, tx, ty) == T_SAND || tile(g, tx, ty) == T_PARK)
            spawn(g, K_PED, tile_center(tx), tile_center(ty), (uint8_t)rni(g, 256));
    }
    for (i = 0; i < 4; i++) {
        tx = 56 + rni(g, 12);
        ty = 20 + rni(g, 60);
        if (tile(g, tx, ty) == T_SWAMP)
            spawn(g, K_GATOR, tile_center(tx), tile_center(ty), (uint8_t)rni(g, 256));
    }
}

static G6Ent *who(G6Game *g)
{
    return &g->ent[g->who & 1];
}

static G6Ent *car_of(G6Game *g, G6Ent *e)
{
    if (!e || e->car >= G6_MAX_ENT) return 0;
    if (!(g->ent[e->car].flags & F_ALIVE)) return 0;
    return &g->ent[e->car];
}

static void set_ticker(G6Game *g, const char *s)
{
    char *o = g->ticker;
    while (*s && (o - g->ticker) < 42) *o++ = *s++;
    *o = 0;
}

static void cycle_ticker(G6Game *g)
{
    g->tick_i = (g->tick_i + 1) % YAP_N;
    set_ticker(g, YAP[g->tick_i]);
}

static void bump_wanted(G6Game *g, int n)
{
    g->wanted += n;
    if (g->wanted > 5) g->wanted = 5;
    if (g->wanted < 0) g->wanted = 0;
    g->wanted_cd = 400;
}

static int spawn_cop(G6Game *g)
{
    int tx, ty, i, n = 0;
    for (i = 0; i < G6_MAX_ENT; i++)
        if ((g->ent[i].flags & F_ALIVE) && g->ent[i].kind == K_POL) n++;
    if (n >= g->wanted + 1) return -1;
    if (!find_road(g, &tx, &ty)) {
        tx = 8; ty = 13;
    }
    i = spawn(g, K_POL, tile_center(tx), tile_center(ty), 0);
    if (i >= 0) {
        g->ent[i].pal = 2;
        g->ent[i].speed = 400;
    }
    return i;
}

static void start_play(G6Game *g)
{
    g->mode = G6_PLAY;
    g->hp = 100;
    g->money = 42;
    g->wanted = 0;
    g->who = 0;
    g->mission = 0;
    g->mstage = 0;
    g->mdone = 0;
    g->radio = 0;
    g->view_map = 0;
    g->want_home = 0;
    spawn_world(g);
    cycle_ticker(g);
    g6_note(g, "play: Mangrove Key, Nia Voss");
}

int g6_init(G6Game *g, uint32_t free_b, uint32_t largest, uint32_t arena)
{
    if (!g) return 0;
    memset(g, 0, sizeof *g);
    g->heap_free = free_b;
    g->heap_largest = largest;
    g->arena_size = arena;
    g->arena_peak = arena;
    g->rng = 0x6a1706u;
    if (!arena) {
        g->low_mem = 1;
        g->mode = G6_SHORT;
        return 0;
    }
    gen_map(g);
    g->mode = G6_TITLE_MODE;
    g->hp = 100;
    g->money = 0;
    set_ticker(g, G6_TAG);
    g6_note(g, "init: title");
    return 1;
}

void g6_set_time(G6Game *g, uint32_t ms)
{
    if (!g) return;
    if (ms < g->now_ms) g->now_ms = ms;
    g->now_ms = ms;
}

static int in_r(int x, int y, int cx, int cy, int r)
{
    int dx = x - cx, dy = y - cy;
    return dx * dx + dy * dy <= r * r;
}

void g6_set_fingers(G6Game *g, const G6Finger *f, int n)
{
    int i, a = 0, b = 0, s = 0, p = 0;
    int jx = 0, jy = 0, got = 0;
    if (!g) return;
    g->prev_a = g->btn_a;
    g->prev_s = g->btn_s;
    g->prev_p = g->btn_p;
    g->prev_b = g->btn_b;
    if (n > G6_MAX_FINGERS) n = G6_MAX_FINGERS;
    for (i = 0; i < n; i++) {
        if (!f[i].down) continue;
        if (g->mode == G6_TITLE_MODE || g->mode == G6_BUSTED || g->mode == G6_WASTED) {
            a = 1;
            continue;
        }
        if (g->mode == G6_MENU || g->mode == G6_PAUSE) {
            if (f[i].x >= PAUSE_X && f[i].y <= 28) continue;
            a = 1;
            jy = f[i].y;
            got = 2;
            continue;
        }
        if (f[i].x >= PAUSE_X && f[i].y <= 28) { p = 1; continue; }
        if (in_r(f[i].x, f[i].y, BTN_AX, BTN_AY, 24)) { a = 1; continue; }
        if (in_r(f[i].x, f[i].y, BTN_BX, BTN_BY, 24)) { b = 1; continue; }
        if (in_r(f[i].x, f[i].y, BTN_SX, BTN_SY, 20)) { s = 1; continue; }
        if (f[i].x < 110 && f[i].y > 310) {
            map_stick(f[i].x - JOY_CX, f[i].y - JOY_CY, &jx, &jy);
            got = 1;
        }
    }
    g->btn_a = a;
    g->btn_b = b;
    g->btn_s = s;
    g->btn_p = p;
    if (got == 2) {
        g->joyx = 0;
        g->joyy = jy;
    } else if (got) {
        g->joyx = jx;
        g->joyy = jy;
    } else {
        g->joyx = 0;
        g->joyy = 0;
    }
}

static void enter_car(G6Game *g, G6Ent *e, int ci)
{
    G6Ent *c = &g->ent[ci];
    e->flags |= F_INCAR;
    e->car = (uint8_t)ci;
    e->x = c->x;
    e->y = c->y;
    c->flags |= F_MARK;
    bump_wanted(g, 1);
    g->horn = 1;
    g6_note(g, "car: borrowed");
    set_ticker(g, "WANTED");
}

static void leave_car(G6Game *g, G6Ent *e)
{
    G6Ent *c = car_of(g, e);
    int32_t nx, ny;
    e->flags &= (uint8_t)~F_INCAR;
    if (c) {
        nx = c->x + ((int32_t)icos((uint8_t)(c->ang + 64)) * 14);
        ny = c->y + ((int32_t)isin((uint8_t)(c->ang + 64)) * 14);
        e->x = nx;
        e->y = ny;
        clamp_ent(e);
        c->speed = 0;
        c->flags &= (uint8_t)~F_MARK;
    }
    e->car = 255;
    e->speed = 0;
}

static int near_car(G6Game *g, G6Ent *e)
{
    int i, best = -1, bd = 40 * 40;
    int32_t ex = e->x >> 8, ey = e->y >> 8;
    for (i = 0; i < G6_MAX_ENT; i++) {
        int dx, dy, d;
        if (!(g->ent[i].flags & F_ALIVE)) continue;
        if (g->ent[i].kind != K_CAR && g->ent[i].kind != K_POL) continue;
        dx = (int)(g->ent[i].x >> 8) - (int)ex;
        dy = (int)(g->ent[i].y >> 8) - (int)ey;
        d = dx * dx + dy * dy;
        if (d < bd) { bd = d; best = i; }
    }
    return best;
}

static void hit_building(G6Game *g, G6Ent *c, int32_t ox, int32_t oy)
{
    c->x = ox;
    c->y = oy;
    c->speed = (int16_t)(-(c->speed / 2));
    c->slide = (int16_t)(-(c->slide / 2));
    c->ang = (uint8_t)(c->ang + 18);
    if (g->hp > 4) g->hp -= 4;
    else g->hp = 0;
}

static int car_max_speed(const G6Ent *c)
{
    /* 8.8 pixels per 30 ms tick. 120 px/s -> 922. */
    int maxs = (G6_CAR_PX_S * G6_TICK_MS * 256) / 1000;
    if (c && c->kind == K_POL) maxs = (maxs * 11) / 10;
    return maxs;
}

static void drive(G6Game *g, G6Ent *c, int gas, int brk, int steer)
{
    int32_t ox = c->x, oy = c->y;
    int t, grip = (g->who == 1) ? 6 : 5;
    int maxs = car_max_speed(c);
    int acc = (maxs * G6_TICK_MS) / G6_CAR_ACC_MS;
    int aspeed;
    if (acc < 1) acc = 1;
    if (gas < 0) gas = 0;
    if (gas > 256) gas = 256;
    if (steer > 256) steer = 256;
    if (steer < -256) steer = -256;
    if (gas) c->speed = (int16_t)(c->speed + acc * gas / 256);
    if (brk) {
        if (c->speed > acc) c->speed = (int16_t)(c->speed - acc * 2);
        else if (c->speed < -acc) c->speed = (int16_t)(c->speed + acc * 2);
        else c->speed = 0;
        c->slide = (int16_t)(c->slide * 3 / 4);
    } else if (!gas) {
        if (c->speed > acc / 4) c->speed = (int16_t)(c->speed - acc / 4);
        else if (c->speed < -acc / 4) c->speed = (int16_t)(c->speed + acc / 4);
        else c->speed = 0;
    }
    if (c->speed > maxs) c->speed = (int16_t)maxs;
    if (c->speed < -(maxs / 3)) c->speed = (int16_t)(-(maxs / 3));
    aspeed = c->speed;
    if (aspeed < 0) aspeed = -aspeed;
    if (steer && aspeed > 12) {
        /* 180 deg/s at full analog and top speed (256 ang = 360 deg). */
        int turn = (steer * 3 / 200) * aspeed / maxs;
        if (turn > 6) turn = 6;
        if (turn < -6) turn = -6;
        c->ang = (uint8_t)(c->ang + turn);
        c->slide = (int16_t)(c->slide + steer / 32);
    }
    c->slide = (int16_t)((int)c->slide * grip / 8);
    c->x += ((int32_t)icos(c->ang) * c->speed) / 256;
    c->y += ((int32_t)isin(c->ang) * c->speed) / 256;
    c->x += ((int32_t)icos((uint8_t)(c->ang + 64)) * c->slide) / 256;
    c->y += ((int32_t)isin((uint8_t)(c->ang + 64)) * c->slide) / 256;
    clamp_ent(c);
    t = pix_tile(g, c->x, c->y);
    if (solid_car(t) && t != T_BRIDGE) hit_building(g, c, ox, oy);
    if (t == T_SWAMP) {
        c->speed = (int16_t)(c->speed * 3 / 4);
        c->slide = (int16_t)(c->slide + 4);
    }
    clamp_ent(c);
}

static void walk(G6Game *g, G6Ent *e, int vx, int vy, int px_s)
{
    int32_t ox = e->x, oy = e->y;
    int32_t dx, dy;
    int t;
    dx = (int32_t)vx * px_s * G6_TICK_MS / 1000;
    dy = (int32_t)vy * px_s * G6_TICK_MS / 1000;
    e->x += dx;
    e->y += dy;
    t = pix_tile(g, e->x, e->y);
    if (solid_foot(t)) {
        e->x = ox;
        e->y = oy;
        clamp_ent(e);
        return;
    }
    if (t == T_OCEAN) {
        e->x = ox + dx / 3;
        e->y = oy + dy / 3;
        if (g->hp > 0 && (g->tick_n & 7u) == 0) g->hp--;
    }
    if (t == T_SWAMP && (g->tick_n & 15u) == 0 && g->hp > 0) g->hp--;
    clamp_ent(e);
    if (vx || vy) {
        if (vx > 40 && vy > 40) e->ang = 32;
        else if (vx > 40 && vy < -40) e->ang = 224;
        else if (vx < -40 && vy > 40) e->ang = 96;
        else if (vx < -40 && vy < -40) e->ang = 160;
        else if (vx > 40) e->ang = 0;
        else if (vx < -40) e->ang = 128;
        else if (vy > 40) e->ang = 64;
        else if (vy < -40) e->ang = 192;
    }
}

static void hit_ped(G6Game *g, G6Ent *c)
{
    int i;
    int32_t cx = c->x >> 8, cy = c->y >> 8;
    if (c->speed < 200 && c->speed > -200) return;
    for (i = 0; i < G6_MAX_ENT; i++) {
        int dx, dy;
        if (!(g->ent[i].flags & F_ALIVE) || g->ent[i].kind != K_PED) continue;
        if (g->ent[i].flags & F_INCAR) continue;
        dx = (int)(g->ent[i].x >> 8) - (int)cx;
        dy = (int)(g->ent[i].y >> 8) - (int)cy;
        if (dx * dx + dy * dy < 12 * 12) {
            g->ent[i].flags = 0;
            bump_wanted(g, 1);
            g->money += 5;
        }
    }
}

static void ai_car(G6Game *g, G6Ent *c, int cop)
{
    G6Ent *p = who(g);
    int gas = 1, brk = 0, steer = 0;
    int t;
    if (cop && g->wanted > 0) {
        int32_t dx = (p->x - c->x) >> 8;
        int32_t dy = (p->y - c->y) >> 8;
        int want = 0;
        if (dx > 8) want = 0;
        else if (dx < -8) want = 128;
        if (dy > 8 && dx * dx < dy * dy) want = 64;
        else if (dy < -8 && dx * dx < dy * dy) want = 192;
        {
            int d = (int)(uint8_t)(want - c->ang);
            if (d > 127) d -= 256;
            if (d < -128) d += 256;
            steer = d * 4;
        }
        gas = 256;
        drive(g, c, gas, brk, steer);
        return;
    }
    t = pix_tile(g, c->x + ((int32_t)icos(c->ang) * 18), c->y + ((int32_t)isin(c->ang) * 18));
    if (!is_road(t) && t != T_WALK) {
        c->ang = (uint8_t)(c->ang + 64);
        c->speed = (int16_t)(c->speed / 2);
    }
    if (rni(g, 80) == 0) c->ang = (uint8_t)(c->ang + (rni(g, 2) ? 64 : -64));
    drive(g, c, 180, 0, 0);
}

static void ai_ped(G6Game *g, G6Ent *e)
{
    int spd = g->wanted ? 28 : 18;
    if (e->cool) { e->cool--; }
    else {
        e->dirx = (int8_t)(rni(g, 3) - 1);
        e->diry = (int8_t)(rni(g, 3) - 1);
        e->cool = (uint8_t)(20 + rni(g, 40));
    }
    if (g->wanted) {
        G6Ent *p = who(g);
        e->dirx = (int8_t)((e->x > p->x) ? 1 : -1);
        e->diry = (int8_t)((e->y > p->y) ? 1 : -1);
    }
    walk(g, e, e->dirx * 256, e->diry * 256, spd);
}

static void ai_gator(G6Game *g, G6Ent *e)
{
    G6Ent *p = who(g);
    int32_t dx = (p->x - e->x) >> 8, dy = (p->y - e->y) >> 8;
    int d = dx * dx + dy * dy;
    if (d < 28 * 28 && !(p->flags & F_INCAR) && pix_tile(g, p->x, p->y) == T_SWAMP) {
        e->x += (dx > 0 ? 200 : -200);
        e->y += (dy > 0 ? 200 : -200);
        if (d < 12 * 12 && g->hp > 8) {
            g->hp -= 8;
            set_ticker(g, "GATOR");
        }
    }
}

static void partner_follow(G6Game *g)
{
    G6Ent *a = who(g);
    G6Ent *b = &g->ent[(g->who ^ 1) & 1];
    int32_t dx, dy;
    if (!(b->flags & F_ALIVE)) return;
    if (a->flags & F_INCAR) {
        if (!(b->flags & F_INCAR) && a->car != 255) {
            dx = (a->x - b->x) >> 8;
            dy = (a->y - b->y) >> 8;
            if (dx * dx + dy * dy < 22 * 22) {
                b->flags |= F_INCAR | F_PASS;
                b->car = a->car;
            } else {
                walk(g, b, dx > 0 ? 256 : -256, dy > 0 ? 256 : -256, 36);
            }
        } else {
            b->x = a->x;
            b->y = a->y;
        }
        return;
    }
    if (b->flags & F_INCAR) leave_car(g, b);
    dx = (a->x - b->x) >> 8;
    dy = (a->y - b->y) >> 8;
    if (dx * dx + dy * dy > 18 * 18)
        walk(g, b, dx > 4 ? 256 : dx < -4 ? -256 : 0, dy > 4 ? 256 : dy < -4 ? -256 : 0, 36);
}

static void mission_setup(G6Game *g, int id)
{
    g->mission = id;
    g->mstage = 1;
    g->mtimer = 0;
    g->cp_n = 0;
    g->cp_at = 0;
    if (id == 1) {
        set_ticker(g, "PICK UP AT THE DOCKS");
        g6_note(g, "mission: cooler run");
    } else if (id == 2) {
        int i;
        set_ticker(g, "STEAL A CAR");
        for (i = 2; i < G6_MAX_ENT; i++)
            if ((g->ent[i].flags & F_ALIVE) && g->ent[i].kind == K_CAR) {
                g->ent[i].pal = 2;
                g->ent[i].flags |= F_MARK;
                break;
            }
        bump_wanted(g, 2);
        g6_note(g, "mission: getaway");
    } else if (id == 3) {
        g->cp_n = 5;
        g->cp_x[0] = tile_center(32); g->cp_y[0] = tile_center(37);
        g->cp_x[1] = tile_center(44); g->cp_y[1] = tile_center(44);
        g->cp_x[2] = tile_center(24); g->cp_y[2] = tile_center(52);
        g->cp_x[3] = tile_center(16); g->cp_y[3] = tile_center(29);
        g->cp_x[4] = tile_center(40); g->cp_y[4] = tile_center(21);
        set_ticker(g, "HIT ALL 5 GATES");
        g6_note(g, "mission: causeway heat");
    }
}

static void mission_tick(G6Game *g)
{
    G6Ent *p = who(g);
    int32_t px = p->x, py = p->y;
    if (g->mission == 0) {
        /* auto-offer first job near shack */
        int32_t sx = tile_center(30), sy = tile_center(50);
        int32_t dx = (px - sx) >> 8, dy = (py - sy) >> 8;
        if (dx * dx + dy * dy < 20 * 20 && (g->btn_a && !g->prev_a))
            mission_setup(g, 1);
        return;
    }
    if (g->mission == 1) {
        if (g->mstage == 1) {
            int32_t sx = tile_center(30), sy = tile_center(50);
            int32_t dx = (px - sx) >> 8, dy = (py - sy) >> 8;
            if (dx * dx + dy * dy < 16 * 16) {
                g->mstage = 2;
                set_ticker(g, "DELIVER TO NEON WHARF");
            }
        } else if (g->mstage == 2) {
            int32_t sx = tile_center(18), sy = tile_center(10);
            int32_t dx = (px - sx) >> 8, dy = (py - sy) >> 8;
            if (dx * dx + dy * dy < 16 * 16) {
                g->money += 500;
                g->mdone |= 1;
                g->mission = 0;
                g->mstage = 0;
                set_ticker(g, "JOB COMPLETE");
                g6_note(g, "mission: cooler done");
            }
        }
    } else if (g->mission == 2) {
        if (g->wanted == 0 && (p->flags & F_INCAR)) {
            g->money += 800;
            g->mdone |= 2;
            g->mission = 0;
            set_ticker(g, "JOB COMPLETE");
            g6_note(g, "mission: getaway done");
        }
    } else if (g->mission == 3 && g->cp_at < g->cp_n) {
        int32_t dx = (px - g->cp_x[g->cp_at]) >> 8;
        int32_t dy = (py - g->cp_y[g->cp_at]) >> 8;
        if (dx * dx + dy * dy < 18 * 18) {
            g->cp_at++;
            if (g->cp_at >= g->cp_n) {
                g->money += 1200;
                g->mdone |= 4;
                g->mission = 0;
                set_ticker(g, "JOB COMPLETE");
                g6_note(g, "mission: race done");
            } else {
                char line[44], *o = put_s(line, "GATE ");
                o = put_u(o, (unsigned)g->cp_at + 1);
                put_s(o, " / 5");
                set_ticker(g, line);
            }
        }
    }
    if (g->mission == 0 && g->mdone == 7)
        set_ticker(g, "ALL JOBS COMPLETE");
}

static void wanted_tick(G6Game *g)
{
    int i, seen = 0, cops = 0;
    G6Ent *p = who(g);
    if (g->wanted <= 0) return;
    for (i = 0; i < G6_MAX_ENT; i++) {
        int32_t dx, dy;
        if (!(g->ent[i].flags & F_ALIVE) || g->ent[i].kind != K_POL) continue;
        cops++;
        dx = (g->ent[i].x - p->x) >> 8;
        dy = (g->ent[i].y - p->y) >> 8;
        if (dx * dx + dy * dy < 160 * 160) seen = 1;
        if (dx * dx + dy * dy < 16 * 16) {
            G6Ent *c = car_of(g, p);
            int slow = c ? (c->speed < 80 && c->speed > -80) : 1;
            if (slow) g->bust_cd++;
            else g->bust_cd = 0;
        }
    }
    while (cops < g->wanted) {
        if (spawn_cop(g) < 0) break;
        cops++;
    }
    if (seen) g->wanted_cd = 400;
    else if (g->wanted_cd > 0) {
        g->wanted_cd--;
        if (g->wanted_cd == 0) {
            g->wanted--;
            g->wanted_cd = 400;
            g->bust_cd = 0;
        }
    }
    if (g->bust_cd > 45) {
        g->mode = G6_BUSTED;
        g->money = g->money / 2;
        g->wanted = 0;
        g->bust_cd = 0;
        if (p->flags & F_INCAR) leave_car(g, p);
        g6_note(g, "busted");
        set_ticker(g, "BUSTED");
    }
}

static void handle_edges(G6Game *g)
{
    G6Ent *p = who(g);
    if (g->mode == G6_TITLE_MODE) {
        if (g->btn_a && !g->prev_a) {
            if (g->frames < (unsigned)G6_TRAILER_END)
                g->frames = (uint32_t)G6_TRAILER_END;
            else start_play(g);
        }
        return;
    }
    if (g->mode == G6_BUSTED || g->mode == G6_WASTED) {
        if (g->btn_a && !g->prev_a) {
            start_play(g);
            g->mode = G6_PLAY;
        }
        return;
    }
    if (g->mode == G6_MENU || g->mode == G6_PAUSE) {
        if (g->btn_a && !g->prev_a) {
            if (g->joyy > 250) {
                g->want_home = 1;
                g6_note(g, "quit: home");
            } else if (g->mission == 0 && g->joyy > 175 && g->joyy < 210) {
                int next = 1;
                if (g->mdone & 1) next = 2;
                if ((g->mdone & 3) == 3) next = 3;
                if (g->mdone == 7) next = 1;
                mission_setup(g, next);
                g->mode = G6_PLAY;
            } else {
                g->mode = G6_PLAY;
            }
        }
        if (g->btn_p && !g->prev_p) g->mode = G6_PLAY;
        return;
    }
    if (g->mode != G6_PLAY) return;
    if (g->btn_p && !g->prev_p) {
        g->mode = G6_PAUSE;
        return;
    }
    if (g->btn_s && !g->prev_s) {
        g->who ^= 1;
        g6_note(g, g->who ? "who: Wade Pike" : "who: Nia Voss");
        set_ticker(g, g->who ? "WADE" : "NIA");
    }
    if (g->btn_a && !g->prev_a) {
        if (p->flags & F_INCAR) {
            G6Ent *c = car_of(g, p);
            if (!c || c->speed < 48) leave_car(g, p);
        } else {
            int ci = near_car(g, p);
            if (ci >= 0) enter_car(g, p, ci);
            else if (g->mission == 0) {
                int32_t sx = tile_center(30), sy = tile_center(50);
                int32_t dx = (p->x - sx) >> 8, dy = (p->y - sy) >> 8;
                if (dx * dx + dy * dy < 24 * 24) mission_setup(g, 1);
                else {
                    sx = tile_center(16); sy = tile_center(21);
                    dx = (p->x - sx) >> 8; dy = (p->y - sy) >> 8;
                    if (dx * dx + dy * dy < 24 * 24) mission_setup(g, 2);
                    else {
                        sx = tile_center(32); sy = tile_center(37);
                        dx = (p->x - sx) >> 8; dy = (p->y - sy) >> 8;
                        if (dx * dx + dy * dy < 24 * 24) mission_setup(g, 3);
                    }
                }
            }
        }
    }
    if (g->btn_b && (p->flags & F_INCAR) && !g->prev_b) {
        g->horn = 1;
        g->radio = (g->radio + 1) % 5;
        set_ticker(g, RADIO[g->radio]);
    }
}

static void play_tick(G6Game *g)
{
    G6Ent *p = who(g);
    int i, spd;
    handle_edges(g);
    if (g->mode != G6_PLAY) return;
    if (p->flags & F_INCAR) {
        G6Ent *c = car_of(g, p);
        if (c) {
            int mag = isqrt(g->joyx * g->joyx + g->joyy * g->joyy);
            int gas = mag;
            int brk = g->btn_b || (g->joyy > 80);
            if (brk) gas = 0;
            drive(g, c, gas, brk, g->joyx);
            hit_ped(g, c);
            p->x = c->x;
            p->y = c->y;
            p->ang = c->ang;
            if (c->kind == K_POL && (g->tick_n & 31u) == 0) bump_wanted(g, 1);
        }
    } else {
        spd = G6_WALK_PX_S;
        walk(g, p, g->joyx, g->joyy, spd);
    }
    partner_follow(g);
    for (i = 2; i < G6_MAX_ENT; i++) {
        if (!(g->ent[i].flags & F_ALIVE)) continue;
        if (g->ent[i].kind == K_CAR) ai_car(g, &g->ent[i], 0);
        else if (g->ent[i].kind == K_POL) ai_car(g, &g->ent[i], 1);
        else if (g->ent[i].kind == K_PED) ai_ped(g, &g->ent[i]);
        else if (g->ent[i].kind == K_GATOR) ai_gator(g, &g->ent[i]);
    }
    for (i = 0; i < G6_MAX_ENT; i++) {
        if (g->ent[i].flags & F_ALIVE) clamp_ent(&g->ent[i]);
    }
    wanted_tick(g);
    mission_tick(g);
    if (g->hp <= 0) {
        g->mode = G6_WASTED;
        g6_note(g, "wasted");
        set_ticker(g, "WASTED");
    }
    g->tick_cd++;
    if (g->tick_cd > 160) {
        g->tick_cd = 0;
        cycle_ticker(g);
    }
    {
        int32_t tx = p->x - ((G6_SCR_W / 2) << 8);
        int32_t ty = p->y - ((200) << 8);
        g->camx += (tx - g->camx) / 8;
        g->camy += (ty - g->camy) / 8;
    }
}

void g6_tick(G6Game *g)
{
    if (!g || g->low_mem) return;
    g->tick_n++;
    g->frames++;
    if (g->mode == G6_TITLE_MODE) handle_edges(g);
    else if (g->mode == G6_PLAY) play_tick(g);
    else handle_edges(g);
    if (g->arena_size > g->arena_peak) g->arena_peak = g->arena_size;
}

void g6_advance(G6Game *g, uint32_t dt_ms)
{
    int steps = 0;
    if (!g || g->low_mem) return;
    if (dt_ms > G6_DT_MAX_MS) dt_ms = G6_DT_MAX_MS;
    g->acc_ms += dt_ms;
    while (g->acc_ms >= (uint32_t)G6_TICK_MS && steps < G6_STEP_CAP) {
        g->acc_ms -= (uint32_t)G6_TICK_MS;
        g6_tick(g);
        steps++;
    }
    if (steps >= G6_STEP_CAP && g->acc_ms >= (uint32_t)G6_TICK_MS)
        g->acc_ms = (uint32_t)(G6_TICK_MS - 1);
}

int g6_player_px(const G6Game *g)
{
    if (!g) return 0;
    return (int)(g->ent[g->who & 1].x >> 8);
}

int g6_player_py(const G6Game *g)
{
    if (!g) return 0;
    return (int)(g->ent[g->who & 1].y >> 8);
}

int g6_title_phase(const G6Game *g)
{
    unsigned f;
    if (!g || g->mode != G6_TITLE_MODE) return -1;
    f = g->frames;
    if (f < (unsigned)G6_FADE_TICKS) return 0;
    f -= (unsigned)G6_FADE_TICKS;
    if (f < (unsigned)(G6_N_SCENES * G6_SCENE_TICKS))
        return 1 + (int)(f / (unsigned)G6_SCENE_TICKS);
    f -= (unsigned)(G6_N_SCENES * G6_SCENE_TICKS);
    if (f < (unsigned)G6_LOGO_TICKS) return 5;
    return 6;
}

void g6_debug_title_frame(G6Game *g, unsigned frames)
{
    if (!g) return;
    g->mode = G6_TITLE_MODE;
    g->frames = frames;
    g->tick_n = frames;
    g->low_mem = 0;
}

int g6_want_home(const G6Game *g) { return g && g->want_home; }
int g6_low_mem(const G6Game *g) { return !g || g->low_mem; }
int g6_mode(const G6Game *g) { return g ? g->mode : G6_SHORT; }
uint32_t g6_arena_peak(const G6Game *g) { return g ? g->arena_peak : 0; }
uint32_t g6_ticks(const G6Game *g) { return g ? g->tick_n : 0; }
int g6_money(const G6Game *g) { return g ? g->money : 0; }
int g6_wanted(const G6Game *g) { return g ? g->wanted : 0; }
int g6_mission(const G6Game *g) { return g ? g->mission : 0; }
int g6_who(const G6Game *g) { return g ? g->who : 0; }
const char *g6_who_name(const G6Game *g) { return (g && g->who) ? "Wade Pike" : "Nia Voss"; }
const char *g6_ticker(const G6Game *g) { return g ? g->ticker : ""; }
const char *g6_log_line(const G6Game *g) { return g ? g->logbuf : ""; }
int g6_horn(G6Game *g)
{
    int h;
    if (!g) return 0;
    h = g->horn;
    g->horn = 0;
    return h;
}

static uint32_t crc32_buf(const uint8_t *p, size_t n)
{
    uint32_t c = 0xffffffffu;
    size_t i;
    int k;
    for (i = 0; i < n; i++) {
        c ^= p[i];
        for (k = 0; k < 8; k++)
            c = (c & 1u) ? (0xedb88320u ^ (c >> 1)) : (c >> 1);
    }
    return c ^ 0xffffffffu;
}

int g6_save(const G6Game *g, void *buf, size_t cap, size_t *out)
{
    uint8_t *b = buf;
    uint32_t payload[16];
    uint32_t crc;
    size_t n;
    G6Ent *p;
    if (!g || !buf) return 0;
    memset(payload, 0, sizeof payload);
    p = (G6Ent *)&g->ent[g->who & 1];
    payload[0] = (uint32_t)p->x;
    payload[1] = (uint32_t)p->y;
    payload[2] = (uint32_t)p->ang | ((uint32_t)g->who << 8) | ((uint32_t)(p->flags & F_INCAR) << 16);
    payload[3] = (uint32_t)g->money;
    payload[4] = (uint32_t)g->wanted;
    payload[5] = (uint32_t)g->hp;
    payload[6] = (uint32_t)g->mission | ((uint32_t)g->mstage << 8) | ((uint32_t)g->mdone << 16);
    payload[7] = g->rng;
    payload[8] = (uint32_t)g->radio;
    payload[9] = p->car;
    crc = crc32_buf((uint8_t *)payload, sizeof payload);
    n = 16;
    if (cap < n) return 0;
    memcpy(b, "G6SV", 4);
    b[4] = (uint8_t)G6_SAVE_VER;
    b[5] = 0;
    b[6] = (uint8_t)sizeof payload;
    b[7] = 0;
    memcpy(b + 8, &crc, 4);
    memcpy(b + 12, payload, sizeof payload);
    n = 12 + sizeof payload;
    if (out) *out = n;
    return 1;
}

int g6_load(G6Game *g, const void *buf, size_t n)
{
    const uint8_t *b = buf;
    uint32_t payload[16];
    uint32_t crc, got;
    G6Ent *p;
    if (!g) return -1;
    if (!buf || n < 12 + sizeof payload) {
        g->save_ok = 0;
        return -1;
    }
    if (memcmp(b, "G6SV", 4) != 0 || b[4] != (uint8_t)G6_SAVE_VER) {
        g->save_bad = 1;
        set_ticker(g, "SAVE DATA CORRUPT - NEW GAME");
        g6_note(g, "save: corrupt magic");
        return -2;
    }
    memcpy(&crc, b + 8, 4);
    memcpy(payload, b + 12, sizeof payload);
    got = crc32_buf((uint8_t *)payload, sizeof payload);
    if (got != crc) {
        g->save_bad = 1;
        set_ticker(g, "SAVE DATA CORRUPT - NEW GAME");
        g6_note(g, "save: corrupt crc");
        return -2;
    }
    start_play(g);
    p = who(g);
    p->x = (int32_t)payload[0];
    p->y = (int32_t)payload[1];
    p->ang = (uint8_t)(payload[2] & 255);
    g->who = (int)((payload[2] >> 8) & 1);
    g->money = (int)payload[3];
    g->wanted = (int)payload[4];
    g->hp = (int)payload[5];
    g->mission = (int)(payload[6] & 255);
    g->mstage = (int)((payload[6] >> 8) & 255);
    g->mdone = (int)((payload[6] >> 16) & 255);
    g->rng = payload[7];
    g->radio = (int)payload[8];
    g->save_ok = 1;
    g->mode = G6_PLAY;
    g6_note(g, "save: loaded");
    return 0;
}

void g6_debug_pose(G6Game *g, int px, int py, int in_car)
{
    G6Ent *p;
    if (!g) return;
    if (g->mode == G6_TITLE_MODE) start_play(g);
    p = who(g);
    p->x = (int32_t)px << 8;
    p->y = (int32_t)py << 8;
    clamp_ent(p);
    g->camx = p->x - ((G6_SCR_W / 2) << 8);
    g->camy = p->y - (200 << 8);
    g->hp = 100;
    g->mode = G6_PLAY;
    if (in_car) {
        int ci = near_car(g, p);
        if (ci < 0) {
            ci = spawn(g, K_CAR, p->x, p->y, p->ang);
        }
        if (ci >= 0) {
            g->ent[ci].x = p->x;
            g->ent[ci].y = p->y;
            enter_car(g, p, ci);
            /* enter_car adds wanted; keep debug poses quiet */
            if (g->wanted > 0) g->wanted--;
        }
    } else if (p->flags & F_INCAR) {
        leave_car(g, p);
    }
}

void g6_debug_wanted(G6Game *g, int stars)
{
    if (!g) return;
    if (g->mode == G6_TITLE_MODE) start_play(g);
    g->wanted = stars;
    if (g->wanted < 0) g->wanted = 0;
    if (g->wanted > 5) g->wanted = 5;
    g->wanted_cd = 400;
    g->hp = 100;
    g->mode = G6_PLAY;
    if (stars > 0) {
        G6Ent *p = who(g);
        int i = spawn(g, K_POL, p->x + (20 << 8), p->y - (12 << 8), p->ang);
        if (i >= 0) {
            g->ent[i].pal = 2;
            g->ent[i].speed = 400;
        }
    }
}

void g6_debug_mission(G6Game *g, int id, int stage)
{
    if (!g) return;
    if (g->mode == G6_TITLE_MODE) start_play(g);
    mission_setup(g, id);
    g->mstage = stage;
}

void g6_debug_view_map(G6Game *g, int on)
{
    if (!g) return;
    if (g->mode == G6_TITLE_MODE) start_play(g);
    g->view_map = on ? 1 : 0;
}

void g6_debug_drive(G6Game *g, int on)
{
    (void)on;
    if (!g) return;
    g6_debug_pose(g, 32 * G6_TILE + 8, 21 * G6_TILE + 8, 1);
}
