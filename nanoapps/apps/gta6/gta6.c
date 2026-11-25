/*
 * GTA 6: RAW_SURFACE shell for Helvetica / silver_resident.
 * Original Mangrove Key caper. Name on Home is exactly GTA 6.
 */
#include "hb_raw_surface.h"
#include "hb_sdk.h"
#include "hb_heap.h"
#include "gta6_game.h"
#include "gta6_notice.h"
#include <string.h>

#define DATA_DIR  G6_DATA_DIR
#define SAVE_PATH G6_DATA_DIR "/save.bin"
#define LOG_PATH  G6_DATA_DIR "/log.txt"
#define HORN_PATH G6_DATA_DIR "/horn.wav"

static G6Game *s_g;
static uint32_t s_last_ms;
static int s_prev_down;
static int s_state; /* 0 wait, 1 run, 2 short, 3 bye */
static uint32_t *s_fb;
static int s_w, s_h;

static void log_flush(void)
{
    const char *line;
    uint8_t buf[256];
    uint32_t old = 0;
    size_t n, keep;
    if (!s_g || !s_g->log_ready) return;
    line = g6_log_line(s_g);
    n = 0;
    while (line[n]) n++;
    old = hb_fs_read(LOG_PATH, buf, sizeof buf - 1);
    if (old > 220) old = 0;
    keep = old;
    if (keep + n + 2 > sizeof buf) keep = 0;
    memcpy(buf + keep, line, n);
    buf[keep + n] = '\n';
    hb_fs_mkdir("/Apps/Data");
    hb_fs_mkdir(DATA_DIR);
    hb_fs_write(LOG_PATH, buf, (uint32_t)(keep + n + 1));
    s_g->log_ready = 0;
}

static void write_horn_wav(void)
{
    uint8_t wav[80];
    uint32_t sr = 8000, n = 32, i;
    /* 8-bit mono square beep, original, not a ripped sting */
    memcpy(wav, "RIFF", 4);
    wav[4] = 72; wav[5] = 0; wav[6] = 0; wav[7] = 0;
    memcpy(wav + 8, "WAVEfmt ", 8);
    wav[16] = 16; wav[17] = wav[18] = wav[19] = 0;
    wav[20] = 1; wav[21] = 0;
    wav[22] = 1; wav[23] = 0;
    wav[24] = (uint8_t)sr; wav[25] = (uint8_t)(sr >> 8); wav[26] = 0; wav[27] = 0;
    wav[28] = (uint8_t)sr; wav[29] = (uint8_t)(sr >> 8); wav[30] = 0; wav[31] = 0;
    wav[32] = 1; wav[33] = 0;
    wav[34] = 8; wav[35] = 0;
    memcpy(wav + 36, "data", 4);
    wav[40] = (uint8_t)n; wav[41] = 0; wav[42] = 0; wav[43] = 0;
    for (i = 0; i < n; i++) wav[44 + i] = (uint8_t)((i & 2) ? 200 : 40);
    hb_fs_mkdir("/Apps/Data");
    hb_fs_mkdir(DATA_DIR);
    hb_fs_write(HORN_PATH, wav, 44 + n);
}

static void do_save(void)
{
    uint8_t buf[128];
    size_t n = 0;
    if (!s_g) return;
    if (!g6_save(s_g, buf, sizeof buf, &n) || n == 0) return;
    hb_fs_mkdir("/Apps/Data");
    hb_fs_mkdir(DATA_DIR);
    hb_fs_write(SAVE_PATH, buf, (uint32_t)n);
}

static void do_load(void)
{
    uint8_t buf[128];
    uint32_t n;
    n = hb_fs_read(SAVE_PATH, buf, sizeof buf);
    if (n == 0) return;
    if (g6_load(s_g, buf, n) < 0) {
        /* missing or corrupt: stay on title with ticker already set */
    }
}

static int prepare_unload(void)
{
    if (s_g && !g6_low_mem(s_g)) do_save();
    log_flush();
    return 1;
}

static void apply_touch(const hb_spoint_t *t)
{
    G6Finger f[G6_MAX_FINGERS];
    hb_touch_t fingers[HB_MAX_FINGERS];
    int n, i, live = 0;

    hb_touch_drain_all();
    n = hb_touch_poll_multi(fingers);
    for (i = 0; i < n && live < G6_MAX_FINGERS; i++) {
        if (fingers[i].status == 2) continue;
        f[live].id = (int)fingers[i].touch_id;
        f[live].x = fingers[i].x;
        f[live].y = fingers[i].y;
        f[live].down = 1;
        live++;
    }
    if (live == 0 && t && t->down) {
        f[0].id = 0;
        f[0].x = t->x;
        f[0].y = t->y;
        f[0].down = 1;
        live = 1;
    }
    g6_set_fingers(s_g, f, live);
    s_prev_down = t && t->down;
}

void hb_raw_init(int w, int h)
{
    uint32_t free_b, largest, size;
    void *block;
    const char *short_lines[] = {
        "NOT ENOUGH MEMORY",
        "PAUSE MUSIC",
        "OR BLUETOOTH",
        "THEN REOPEN"
    };

    s_fb = hb_raw_fb();
    s_w = w;
    s_h = h;
    hb_fs_mkdir("/Apps/Data");
    hb_fs_mkdir(DATA_DIR);
    if (hb_shared) hb_shared->prepare_unload = prepare_unload;

    free_b = hb_os_heap_free();
    largest = hb_os_heap_largest();
    size = g6_pick_block(free_b, largest);
    if (!size) {
        s_state = 2;
        g6_notice_draw(s_fb, w, h, short_lines, 4);
        hb_trace_init();
        hb_trace_log("GTA6MEM", free_b, largest);
        return;
    }
    if (HB_APP_AUX_BLOCK) {
        hb_os_free((void *)HB_APP_AUX_BLOCK);
        HB_APP_AUX_BLOCK = 0;
    }
    block = hb_os_alloc(size);
    if (!block) {
        s_state = 2;
        g6_notice_draw(s_fb, w, h, short_lines, 4);
        return;
    }
    HB_APP_AUX_BLOCK = block;
    memset(block, 0, size);
    s_g = (G6Game *)block;
    if (!g6_init(s_g, free_b, largest, size)) {
        s_state = 2;
        g6_draw(s_g, s_fb, w, h);
        return;
    }
    write_horn_wav();
    do_load();
    s_last_ms = hb_time_uptime_ms();
    s_state = 1;
    hb_trace_init();
    hb_trace_log("GTA6IN", free_b, largest);
    g6_note(s_g, "boot");
    log_flush();
    g6_draw(s_g, s_fb, w, h);
}

void hb_raw_frame(const hb_spoint_t *t)
{
    uint32_t now, dt;
    const char *bye[] = { "SAVED", "PRESS HOME", "TO RETURN" };

    if (s_state == 2) {
        const char *lines[] = {
            "NOT ENOUGH MEMORY",
            "PAUSE MUSIC",
            "OR BLUETOOTH",
            "THEN REOPEN"
        };
        g6_notice_draw(hb_raw_fb(), hb_raw_w(), hb_raw_h(), lines, 4);
        return;
    }
    if (s_state == 3 || (s_g && g6_want_home(s_g))) {
        if (s_state != 3) {
            do_save();
            log_flush();
            s_state = 3;
        }
        g6_notice_draw(hb_raw_fb(), hb_raw_w(), hb_raw_h(), bye, 3);
        return;
    }
    if (s_state != 1 || !s_g) return;

    apply_touch(t);
    now = hb_time_uptime_ms();
    dt = now - s_last_ms;
    s_last_ms = now;
    g6_set_time(s_g, now);
    g6_advance(s_g, dt);
    if (g6_horn(s_g))
        hb_audio_play_wav(HORN_PATH, 80);
    if (s_g->log_ready) log_flush();
    if (g6_want_home(s_g)) {
        do_save();
        log_flush();
        s_state = 3;
        g6_notice_draw(hb_raw_fb(), hb_raw_w(), hb_raw_h(), bye, 3);
        return;
    }
    g6_draw(s_g, hb_raw_fb(), hb_raw_w(), hb_raw_h());
}
