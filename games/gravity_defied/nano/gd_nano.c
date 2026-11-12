/* gd_nano.c: RAW_SURFACE frame loop for Gravity Defied (GPL-2.0 overlay).
 * Pre-flight uses the same BLOCK_* arithmetic as host/gravity_defied.py. */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "hb_raw_surface.h"
#include "hb_sdk.h"
#include "hb_heap.h"
#include "hb_shared.h"
#include "gd_config.h"
#include "gdalloc.h"
#include "gd_engine.h"
#include "notice.h"

#define DATA_DIR  "/Apps/Data/GravityDefied"
#define BLOCK_MAX 0x80000u
#define BLOCK_MIN 0x60000u
#define AFTER_MIN 0x8000u
#define SLOP      0x4000u

static int state; /* 0 start, 1 run, 2 short, 3 saved */
static const char *note_lines[7];
static int note_n;
static uint32_t frames, last_fps_ms, fps_count, fps_shown;
static int prev_down;
static int gas_latched;

static int prepare_unload(void)
{
    gd_engine_quit();
    gd_log("quit: prepare_unload");
    gd_log_flush();
    return 1;
}

static uint32_t pick_block(uint32_t free_b, uint32_t largest)
{
    uint32_t size;
    if (largest > BLOCK_MAX + SLOP) size = BLOCK_MAX;
    else size = (largest > SLOP) ? ((largest - SLOP) & ~0xFFFFu) : 0;
    if (free_b > AFTER_MIN) {
        uint32_t cap = (free_b - AFTER_MIN) & ~0xFFFFu;
        if (size > cap) size = cap;
    } else {
        size = 0;
    }
    if (largest < BLOCK_MIN + SLOP || size < BLOCK_MIN) return 0;
    return size;
}

static void draw_bar(uint32_t *fb, int w, int h)
{
    gd_draw_touch_bar(fb, w, h);
}

static void apply_touch(const hb_spoint_t *touch)
{
    int down = touch && touch->down;
    int16_t x = touch ? touch->x : 0;
    int16_t y = touch ? touch->y : 0;
    int gas = 0, brake = 0, ll = 0, lr = 0;
    int pause = 0, mx = -1, my = -1;
    int32_t acc[3];

    hb_accel_read_milli_g(acc);
    if (acc[0] < -180) ll = 1;
    if (acc[0] > 180) lr = 1;

    if (down && y >= GD_GAME_H) {
        int zone = gd_bar_zone(x);
        if (x < 0) zone = 0;
        if (zone <= 0) { brake = 1; gas_latched = 0; }
        else if (zone == 1) ll = 1;
        else if (zone == 2) {
            if (!prev_down) pause = 1;
        } else if (zone == 3) lr = 1;
        else { gas = 1; gas_latched = 1; }
        if (gas_latched && zone >= 1) gas = 1;
        if (brake) gas = 0;
    } else if (!down) {
        gas_latched = 0;
    }

    if (down && y < GD_GAME_H && !prev_down) {
        mx = x;
        my = y;
        gd_log("input: tap %d,%d", x, y);
    }
    if ((gas || brake || ll || lr) && (frames % 120u) == 1u) {
        gd_log("input: gas=%d brake=%d leanL=%d leanR=%d accelx=%d",
               gas, brake, ll, lr, (int)acc[0]);
    }

    gd_set_held(gas, brake, ll, lr);
    gd_set_edges(0, 0, pause, mx, my);
    prev_down = down;
}

void hb_raw_init(int w, int h)
{
    uint32_t free_b, largest, size;
    void *block;
    char mb1[12], mb2[12];

    hb_fs_mkdir(DATA_DIR);
    gd_set_data_dir(DATA_DIR);
    gd_set_fb(hb_raw_fb(), w, h);
    if (hb_shared) hb_shared->prepare_unload = prepare_unload;

    free_b = hb_os_heap_free();
    largest = hb_os_heap_largest();
    snprintf(mb1, sizeof mb1, "%u.%uM", (unsigned)(free_b >> 20),
             (unsigned)(((free_b & 0xFFFFFu) * 10u) >> 20));
    snprintf(mb2, sizeof mb2, "%u.%uM", (unsigned)(largest >> 20),
             (unsigned)(((largest & 0xFFFFFu) * 10u) >> 20));
    gd_log("heap: free=%s largest=%s (%u %u)", mb1, mb2, (unsigned)free_b, (unsigned)largest);

    size = pick_block(free_b, largest);
    if (!size) {
        note_n = 4;
        note_lines[0] = "NOT ENOUGH MEMORY";
        note_lines[1] = "PAUSE MUSIC";
        note_lines[2] = "TURN BLUETOOTH OFF";
        note_lines[3] = "THEN REOPEN";
        state = 2;
        gd_log("heap: NOT ENOUGH MEMORY free=%u largest=%u", (unsigned)free_b, (unsigned)largest);
        gd_log_flush();
        notice_draw_fb(hb_raw_fb(), w, h, note_lines, note_n);
        return;
    }

    if (HB_APP_AUX_BLOCK) {
        hb_os_free((void *)HB_APP_AUX_BLOCK);
        HB_APP_AUX_BLOCK = 0;
    }
    block = hb_os_alloc(size);
    if (!block) {
        note_n = 2;
        note_lines[0] = "NOT ENOUGH MEMORY";
        note_lines[1] = "ALLOC FAILED";
        state = 2;
        gd_log("heap: alloc %u failed", (unsigned)size);
        gd_log_flush();
        notice_draw_fb(hb_raw_fb(), w, h, note_lines, note_n);
        return;
    }
    HB_APP_AUX_BLOCK = block;
    if (gd_arena_init(block, size) != 0) {
        note_n = 2;
        note_lines[0] = "NOT ENOUGH MEMORY";
        note_lines[1] = "ARENA FAILED";
        state = 2;
        notice_draw_fb(hb_raw_fb(), w, h, note_lines, note_n);
        return;
    }
    gd_log("heap: arena %u at %p", (unsigned)size, block);
    /* The reloc loader does not run .init_array. Static C++ objects malloc,
     * so this is after the arena (same order as PvZ Portable). */
    {
        extern void (*__init_array_start[])(void), (*__init_array_end[])(void);
        void (**ctor)(void);
        unsigned nctor = 0;
        for (ctor = __init_array_start; ctor < __init_array_end; ctor++) {
            (*ctor)();
            nctor++;
        }
        gd_log("init: %u C++ constructors", nctor);
    }
    gd_engine_start("levels.mrg", DATA_DIR);
    last_fps_ms = hb_time_uptime_ms();
    state = 1;
    gd_log_flush();
}

void hb_raw_frame(const hb_spoint_t *touch)
{
    uint32_t now, dt;
    uint32_t *fb = hb_raw_fb();
    int w = hb_raw_w(), h = hb_raw_h();

    if (state == 2) {
        notice_draw_fb(fb, w, h, note_lines, note_n);
        return;
    }
    if (state == 3 || gd_want_home()) {
        static const char *bye[3] = { "SAVED", "PRESS HOME", "TO RETURN" };
        notice_draw_fb(fb, w, h, bye, 3);
        if (state != 3) {
            gd_log("quit: path Exit Game / destroyApp, Home unloads the surface");
            gd_log_flush();
            state = 3;
        }
        return;
    }
    if (state != 1) return;

    apply_touch(touch);
    gd_set_fb(fb, w, h);
    gd_engine_tick();
    gd_input_reset_edges();
    draw_bar(fb, w, h);

    if (gd_want_home()) {
        gd_log("quit: path Exit Game / destroyApp, Home unloads the surface");
        gd_log_flush();
        state = 3;
        return;
    }

    frames++;
    fps_count++;
    now = hb_time_uptime_ms();
    dt = now - last_fps_ms;
    if (dt >= 1000u) {
        fps_shown = fps_count * 1000u / (dt ? dt : 1u);
        gd_log("fps: %u arena_used=%u peak=%u fails=%u",
               (unsigned)fps_shown, gd_arena_used(), gd_arena_peak(), gd_arena_fails());
        fps_count = 0;
        last_fps_ms = now;
        if ((frames & 63u) == 0) gd_log_flush();
    }
}
