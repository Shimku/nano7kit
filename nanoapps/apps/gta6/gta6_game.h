/*
 * GTA 6 on iPod nano: original Mangrove Key crime caper (MIT).
 * Portable sim: device shell and host harness share this file.
 */
#ifndef GTA6_GAME_H
#define GTA6_GAME_H

#include <stddef.h>
#include <stdint.h>

#define G6_SCR_W      240
#define G6_SCR_H      432
#define G6_TICK_MS    30
#define G6_DT_MAX_MS  50
#define G6_STEP_CAP   4
#define G6_WALK_PX_S  40
#define G6_CAR_PX_S   120
#define G6_CAR_ACC_MS 1500
#define G6_STEER_DEG_S 180
#define G6_JOY_DEAD_PCT 15
#define G6_MAP_W      72
#define G6_MAP_H      96
#define G6_TILE       16
#define G6_WORLD_W    (G6_MAP_W * G6_TILE)
#define G6_WORLD_H    (G6_MAP_H * G6_TILE)
#define G6_MAX_ENT    36
#define G6_MAX_FINGERS 8
#define G6_BLOCK_MAX  0x40000u
#define G6_BLOCK_MIN  0x18000u
#define G6_AFTER_MIN  0x8000u
#define G6_SLOP       0x4000u
#define G6_SAVE_VER   1u
#define G6_CITY       "Mangrove Key"
#define G6_TITLE      "GTA 6"
#define G6_TAG        "Finally out. On iPod nano."
#define G6_DATA_DIR   "/Apps/Data/GTA6"

enum {
    G6_TITLE_MODE = 0,
    G6_PLAY,
    G6_PAUSE,
    G6_BUSTED,
    G6_WASTED,
    G6_MENU,
    G6_SHORT
};

enum {
    T_OCEAN = 0,
    T_SAND,
    T_GRASS,
    T_PARK,
    T_SWAMP,
    T_ROAD,
    T_WALK,
    T_BUILD,
    T_ROOF,
    T_BRIDGE,
    T_DOCK,
    T_PALM,
    T_NEON,
    T_SHACK
};

typedef struct {
    int id, x, y, down;
} G6Finger;

typedef struct {
    int32_t x, y;
    int16_t speed, slide;
    uint8_t ang, kind, hp, flags, car, pal, cool;
    int8_t dirx, diry;
} G6Ent;

typedef struct G6Game {
    uint8_t map[G6_MAP_H][G6_MAP_W];
    G6Ent ent[G6_MAX_ENT];
    uint32_t rng, now_ms, acc_ms, tick_n, frames;
    int32_t camx, camy;
    int money, wanted, wanted_cd, bust_cd, hp, who;
    int mode, mission, mstage, mtimer, mdone;
    int save_ok, save_bad, want_home, low_mem, view_map;
    int joyx, joyy, btn_a, btn_b, btn_s, btn_p;
    int prev_a, prev_s, prev_p, prev_b;
    int radio, tick_i, tick_cd, horn;
    uint32_t heap_free, heap_largest, arena_size, arena_peak;
    int32_t cp_x[5], cp_y[5];
    int cp_n, cp_at;
    char ticker[44];
    char logbuf[192];
    int log_ready;
} G6Game;

size_t g6_size(void);
uint32_t g6_pick_block(uint32_t free_b, uint32_t largest);
int g6_init(G6Game *g, uint32_t free_b, uint32_t largest, uint32_t arena);
void g6_set_time(G6Game *g, uint32_t ms);
void g6_set_fingers(G6Game *g, const G6Finger *f, int n);
void g6_tick(G6Game *g);
void g6_advance(G6Game *g, uint32_t dt_ms);
int g6_player_px(const G6Game *g);
int g6_player_py(const G6Game *g);
void g6_draw(G6Game *g, uint32_t *fb, int w, int h);
int g6_want_home(const G6Game *g);
int g6_low_mem(const G6Game *g);
int g6_mode(const G6Game *g);
int g6_save(const G6Game *g, void *buf, size_t cap, size_t *out);
int g6_load(G6Game *g, const void *buf, size_t n);
void g6_debug_pose(G6Game *g, int px, int py, int in_car);
void g6_debug_wanted(G6Game *g, int stars);
void g6_debug_mission(G6Game *g, int id, int stage);
void g6_debug_view_map(G6Game *g, int on);
void g6_debug_drive(G6Game *g, int on);
uint32_t g6_arena_peak(const G6Game *g);
uint32_t g6_ticks(const G6Game *g);
int g6_money(const G6Game *g);
int g6_wanted(const G6Game *g);
int g6_mission(const G6Game *g);
int g6_who(const G6Game *g);
const char *g6_who_name(const G6Game *g);
const char *g6_ticker(const G6Game *g);
const char *g6_log_line(const G6Game *g);
int g6_horn(G6Game *g);
void g6_note(G6Game *g, const char *s);

#endif
