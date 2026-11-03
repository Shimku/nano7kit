/*
 * pockethello - first-party RAW_SURFACE HAL prototype for PocketJS on 7G.
 *
 * This is NOT the PocketJS runtime (no QuickJS, no Rust core, no AOT
 * emit). It is the Mac-built NanoApps host that a future
 * `pocket compile --aot` for a 240x432 touch profile would link against.
 *
 * Upstream `--target ipodnano` is 176x132 + click-wheel buttons (nano 2G
 * Rockbox Hero). This app is 240x432 + tap (MKN52 Helvetica).
 *
 * TAP increments a counter. Hold about 1 s to reset. Heap free/largest
 * stay on screen. Slot A.
 */
#include "hb_raw_surface.h"
#include "hb_sdk.h"
#include "hb_heap.h"
#include "pocket_hal.h"

static uint32_t *s_fb;
static int s_w, s_h;
static uint32_t s_free, s_largest;
static uint32_t s_count;
static int s_prev_down;
static uint32_t s_hold_ms;
static int s_dirty = 1;

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

void pocket_hal_bind(uint32_t *fb, int w, int h)
{
    s_fb = fb;
    s_w = w;
    s_h = h;
}

int pocket_hal_w(void)
{
    return s_w;
}

int pocket_hal_h(void)
{
    return s_h;
}

void pocket_hal_fill(uint32_t rgb)
{
    hb_raw_fill(rgb);
}

void pocket_hal_fill_rect(int x, int y, int w, int h, uint32_t rgb)
{
    hb_raw_fill_rect(x, y, w, h, rgb);
}

void pocket_hal_text(int x, int y, const char *str, int scale, uint32_t fg)
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

static void draw(void)
{
    char line[48];
    char *o;

    pocket_hal_fill(0x0f172a);
    pocket_hal_fill_rect(0, 0, s_w, 48, 0x1e3a8a);
    pocket_hal_text(12, 14, "JS HELLO", 2, 0xffffff);

    o = put_s(line, "HEAP ");
    o = put_u(o, s_free / 1024u);
    put_s(o, "K");
    pocket_hal_text(12, 64, line, 1, 0x93c5fd);

    o = put_s(line, "LARGEST ");
    o = put_u(o, s_largest / 1024u);
    put_s(o, "K");
    pocket_hal_text(12, 80, line, 1, 0x93c5fd);

    pocket_hal_text(12, 112, "HAL 240x432 TAP", 1, 0xbfdbfe);
    pocket_hal_text(12, 128, "NOT POCKETJS RT", 1, 0xfbbf24);
    pocket_hal_text(12, 144, "NOT NANO 2G 176", 1, 0xfbbf24);

    o = put_s(line, "COUNT ");
    put_u(o, s_count);
    pocket_hal_text(20, 200, line, 3, 0xffffff);

    pocket_hal_fill_rect(24, 280, 192, 56, 0x2563eb);
    pocket_hal_text(48, 298, "PRESS", 2, 0xffffff);

    pocket_hal_text(12, s_h - 56, "TAP  COUNT++", 1, 0x93c5fd);
    pocket_hal_text(12, s_h - 36, "HOLD RESET", 1, 0x93c5fd);
    pocket_hal_text(12, s_h - 16, "RAW SURFACE  SLOT A", 1, 0x64748b);
}

void hb_raw_init(int w, int h)
{
    pocket_hal_bind(hb_raw_fb(), w, h);
    s_free = hb_os_heap_free();
    s_largest = hb_os_heap_largest();
    s_count = 0;
    hb_trace_init();
    hb_trace_log("JSHELLO", s_free, s_largest);
    draw();
}

void hb_raw_frame(const hb_spoint_t *t)
{
    uint32_t now = hb_time_uptime_ms();
    if (t->down) {
        if (!s_prev_down) s_hold_ms = now;
        else if (now - s_hold_ms >= 1000u && s_count) {
            s_count = 0;
            s_hold_ms = now;
            s_dirty = 1;
        }
    } else if (s_prev_down) {
        if (now - s_hold_ms < 1000u) {
            s_count += 1u;
            s_dirty = 1;
        }
    }
    s_prev_down = t->down;

    if (s_dirty) {
        if ((now % 250u) < 20u) {
            s_free = hb_os_heap_free();
            s_largest = hb_os_heap_largest();
        }
        draw();
        s_dirty = 0;
    }
}
