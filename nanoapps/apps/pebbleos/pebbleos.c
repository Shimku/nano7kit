/* SPDX-FileCopyrightText: 2026 nano7kit contributors */
/* SPDX-License-Identifier: MIT */

/*
 * PebbleOS hosted as one NanoApps app on the iPod nano 7G.
 *
 * The 240x432 screen centres a 200x228 Pebble (Emery) panel. Hardware
 * buttons map to the Pebble keys:
 *   Home        -> Back
 *   Play/Pause  -> Select
 *   Volume Up   -> Up
 *   Volume Down -> Down
 * Sleep/Wake is left alone so it can still sleep the iPod.
 *
 * Must run under both memory levels (about 3.9 MB free / 2 MB largest
 * block, and about 6 MB / 4 MB). If the largest block is too small for
 * the working set, a clean NOT ENOUGH MEMORY screen is shown and nothing
 * heavy is allocated.
 *
 * The real PebbleOS kernel is fetched at build time and linked through
 * pebbleos_host_*. ./start sdk build pebbleos always links it; the status
 * screen below is only for a bare `make` without the kernel library.
 * Missing resources and a stopped kernel get their own plain screens.
 */
#include <string.h>

#include "hb_raw_surface.h"
#include "hb_sdk.h"
#include "hb_heap.h"
#include "pebbleos_host.h"

#define SCREEN_W 240
#define SCREEN_H 432

/* The loader has already placed the image (code, and .bss with the 64 KiB
 * flash bank and the fiber stacks). At run time only the 384 KiB kernel
 * heap comes from the OS heap. Ask for 512 KiB so the OS keeps a little
 * room for itself. */
#define PBL_NEED_LARGEST (512u * 1024u)

static uint32_t *s_fb;
static int s_w, s_h;
static uint32_t s_free, s_largest;
static int s_low_mem;
static int s_no_res;
static int s_stopped;
static int s_host_on;
static int s_prev_btn[4];
static int s_dirty = 1;

static int panel_x(void) { return (s_w - PBL_PANEL_W) / 2; }
static int panel_y(void) { return (s_h - PBL_PANEL_H) / 2; }

static void fill(uint32_t colour)
{
    int i, n = s_w * s_h;
    for (i = 0; i < n; i++)
        s_fb[i] = colour | 0xff000000u;
}

static void fill_rect(int x, int y, int w, int h, uint32_t colour)
{
    int ix, iy;
    colour |= 0xff000000u;
    if (x < 0) { w += x; x = 0; }
    if (y < 0) { h += y; y = 0; }
    if (x + w > s_w) w = s_w - x;
    if (y + h > s_h) h = s_h - y;
    if (w <= 0 || h <= 0) return;
    for (iy = 0; iy < h; iy++)
        for (ix = 0; ix < w; ix++)
            s_fb[(y + iy) * s_w + (x + ix)] = colour;
}

static void text(int x, int y, const char *str, int scale, uint32_t fg)
{
    fg |= 0xff000000u;
    for (; *str; str++, x += 8 * scale) {
        const uint8_t *g = hb_font8x8_glyph(*str);
        int r, c, sy, sx, px, py;
        if (!g) continue;
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

static void draw_low_memory(void)
{
    char line[48];
    char *o;
    fill(0x101418);
    text(16, 80, "PEBBLEOS", 3, 0xffffff);
    text(16, 130, "NOT ENOUGH", 2, 0xff5555);
    text(16, 152, "MEMORY", 2, 0xff5555);
    o = put_s(line, "FREE ");
    o = put_u(o, s_free / 1024u);
    o = put_s(o, " KB");
    text(16, 190, line, 2, 0xd0d4d8);
    o = put_s(line, "BLOCK ");
    o = put_u(o, s_largest / 1024u);
    o = put_s(o, " KB");
    text(16, 218, line, 2, 0xd0d4d8);
    text(16, 270, "PAUSE MUSIC", 2, 0xe8c547);
    text(16, 298, "OR CLOSE APPS", 2, 0xe8c547);
    text(16, 350, "THEN OPEN AGAIN", 1, 0x8a93a8);
}

/* Plain screens for when the kernel cannot run. The title colour tells
 * the tests which one it is. */
static void draw_message(const char *title, uint32_t ink, const char *l1, const char *l2,
                         const char *l3)
{
    fill(0x101418);
    text(16, 80, "PEBBLEOS", 3, 0xffffff);
    text(16, 130, title, 2, ink);
    text(16, 200, l1, 2, 0xd0d4d8);
    text(16, 228, l2, 2, 0xd0d4d8);
    text(16, 290, l3, 1, 0x8a93a8);
}

static void draw_no_resources(void)
{
    draw_message("NO RESOURCES", 0xffaa00, "COPY THEM IN", "FROM THE MAC",
                 "./start nabase add PebbleOS");
}

static void draw_stopped(void)
{
    draw_message("STOPPED", 0x55aaff, "PRESS HOME", "TO LEAVE",
                 "THEN OPEN IT AGAIN");
}

static void blit_host_fb(void)
{
    const uint16_t *src = pebbleos_host_fb();
    int x0 = panel_x(), y0 = panel_y();
    int x, y;
    if (!src) return;
    for (y = 0; y < PBL_PANEL_H; y++) {
        for (x = 0; x < PBL_PANEL_W; x++) {
            uint16_t v = src[y * PBL_PANEL_W + x];
            uint32_t r = ((v >> 11) & 0x1f) << 3;
            uint32_t g = ((v >> 5) & 0x3f) << 2;
            uint32_t b = (v & 0x1f) << 3;
            s_fb[(y0 + y) * s_w + (x0 + x)] = 0xff000000u | (r << 16) | (g << 8) | b;
        }
    }
}

static void draw_status(void)
{
    int x0 = panel_x(), y0 = panel_y();
    fill(0x0a0a0a);
    fill_rect(x0 - 6, y0 - 6, PBL_PANEL_W + 12, PBL_PANEL_H + 12, 0x222222);
    fill_rect(x0, y0, PBL_PANEL_W, PBL_PANEL_H, 0x000000);
    if (s_host_on) {
        blit_host_fb();
    } else {
        text(x0 + 12, y0 + 24, "PEBBLEOS", 2, 0xff4700);
        text(x0 + 12, y0 + 56, "HOST READY", 1, 0xd0d4d8);
        text(x0 + 12, y0 + 80, "KERNEL NOT", 1, 0x8a93a8);
        text(x0 + 12, y0 + 96, "LINKED YET", 1, 0x8a93a8);
        text(x0 + 12, y0 + 128, "HOME  BACK", 1, 0x8a93a8);
        text(x0 + 12, y0 + 144, "PLAY  SELECT", 1, 0x8a93a8);
        text(x0 + 12, y0 + 160, "VOL   UP/DOWN", 1, 0x8a93a8);
    }
}

static void poll_buttons(void)
{
    /* Map: Home=Back, Play/Pause=Select, Vol Up=Up, Vol Down=Down. */
    const hb_button_t map[4] = {
        HB_BTN_HOME, HB_BTN_VOL_UP, HB_BTN_PLAY_PAUSE, HB_BTN_VOL_DOWN
    };
    const int keys[4] = { PBL_KEY_BACK, PBL_KEY_UP, PBL_KEY_SELECT, PBL_KEY_DOWN };
    int i, down;
    for (i = 0; i < 4; i++) {
        down = hb_button_pressed(map[i]) ? 1 : 0;
        if (down != s_prev_btn[i]) {
            if (s_host_on)
                pebbleos_host_button(keys[i], down);
            s_prev_btn[i] = down;
            s_dirty = 1;
        }
    }
}

/* The launcher can open the app again in the same loaded image (it keeps
 * arenas for a quick relaunch). .bss is cleared on every launch, but .data
 * is not, and the kernel changes it while it runs. Keep a copy from the
 * first launch and put it back on later ones, so the kernel always starts
 * from the state it was linked with. */
extern char __snap_start__[], __snap_end__[], __pristine_start__[];
static volatile uint32_t s_launched __attribute__((section(".data.pebbleos_launched"))) = 0;

static void restore_data(void)
{
    uint32_t n = (uint32_t)(__snap_end__ - __snap_start__);
    if (s_launched)
        memcpy(__snap_start__, __pristine_start__, n);
    else
        memcpy(__pristine_start__, __snap_start__, n);
    s_launched = 1;
}

void hb_raw_init(int w, int h)
{
    restore_data();
    s_fb = hb_raw_fb();
    s_w = w;
    s_h = h;
    s_free = hb_os_heap_free();
    s_largest = hb_os_heap_largest();
    s_low_mem = (s_largest < PBL_NEED_LARGEST);
    if (s_low_mem) {
        draw_low_memory();
        return;
    }
    if (pebbleos_host_available() && !pebbleos_host_resources_ok()) {
        s_no_res = 1;
        draw_no_resources();
        return;
    }
    if (pebbleos_host_available()) {
        int rc = pebbleos_host_start();
        s_host_on = (rc > 0);
        if (rc < 0) {
            s_stopped = 1;
            draw_stopped();
            return;
        }
        if (rc == 0) {
            s_low_mem = 1;
            draw_low_memory();
            return;
        }
    }
    draw_status();
}

void hb_raw_frame(const hb_spoint_t *t)
{
    (void)t;
    if (s_low_mem || s_no_res || s_stopped)
        return;
    poll_buttons();
    if (s_host_on) {
        pebbleos_host_tick();
        if (pebbleos_host_stopped()) {
            s_stopped = 1;
            draw_stopped();
            return;
        }
        blit_host_fb();
        return;
    }
    if (s_dirty) {
        draw_status();
        s_dirty = 0;
    }
}
