/* fn_nano.c: the nano frame loop for the recompiled Angry Birds (nano7kit, MIT).
 * The first frame (GL context current) allocates the guest block, loads guest.img and runs
 * the engine's start-up; then each frame feeds the touch and steps the engine. The game is
 * 480x320 landscape; on the 240x432 panel it fills the panel (0.75 across, 0.9 along), so
 * the iPod is held with Home on the right as the iPhone was. Double-tap zooms; a drag on
 * the top or bottom 28 px of the panel pans (slingshot is the rest of the screen).
 * Volume Up / Down also zoom, so a one-finger slingshot still has a zoom. */
#include <math.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "hb_sdk.h"
#include "hb_heap.h"
#include "hb_surface_input.h"
#include "hb_shared.h"
#include "../rt/rt.h"
#include "../rt/rt_plat.h"
#include "../rt/fngl.h"
#include "../rt/ab_entry.h"
#include "notice.h"
#include "gl_left.h"
#include "touch_pick.h"

#define BLOCK_MAX   0x200000u           /* 2.0 MB: cstring+DATA ~80 KB + guest heap + 128 KB stack; 256 KB+ decode is OS heap */
#define BLOCK_MIN   0x190000u           /* 1.56 MB: leftover in the 4.0 MB hole after a ~2.45 MB image */
#define GL_KEEP     0x180000u           /* left to the GL driver; MENU/splash/tutorials at 512, rest 256 */
#define DATA_DIR_GL "/Apps/Data/AngryBirds"
#define BYTES_PER_TRIANGLE 3000u        /* the GL driver's cost per triangle, as measured for Temple Run */
#define AFTER_MIN   0x1A0000u           /* free after the block: UI 512 sheets (~1.5 MB live) + triangles */
extern int be_budget, be_panel_w, be_panel_h, be_stat_tris, be_stat_tiny, be_stat_over, be_stat_draws,
           be_stat_live_tex, be_stat_tex_fail;
extern void plat_log_flush(void);
extern void plat_nano_reset(void);
extern uint32_t rt_varch_reads, rt_varch_bytes;

static int state;                       /* 0 start, 1 running, 2 failed, 3 short of memory */
static char note[2][40];
static const char *note_lines[7];
static int note_n;
static void *block;
static uint32_t frames, last_flush_ms;
static uint64_t last_us;
static int was_down;
static float last_x, last_y;
static uint64_t last_tap_us;
static float last_tap_x, last_tap_y;
static int zoom_dir = 1;
static int gesture;                       /* 0 game, 1 pan, 2 swallow (double-tap zoom) */
static int vol_up_was, vol_dn_was;

/* ---- touch -----------------------------------------------------------------------------
 * An earlier build read the resident's mailbox once a frame. On hardware taps worked and swipes did not:
 * the mailbox is refreshed only when the UI task gets to its touch events, which during a
 * long GL frame is late, so a drag reached the engine as one place held down (a tap). The
 * OS touch list is filled by the touch driver itself and is right at once; Temple Run's port
 * (olievans123/nano-templerun, nano_platform.c plat_poll) reads it the same way. The finger
 * is sampled at the start of the frame and between draw batches (be_nano.c), every change is
 * queued, and fn_frame_q gives the engine one sample per update step. */
#define OS_TOUCH_HEAD ((volatile uint32_t *)0x089a5298u)
#define TQ 8
static ab_tsample tq[TQ];
static int tq_n, t_report;
static uint32_t t_presses, t_from_list, t_from_mb, t_stale, t_held, t_lifts, t_places;
static uint32_t w_ms0, w_frames, w_down, w_dry, w_dry_run, w_dry_max, w_dt_min, w_dt_max;   /* 5 s touch window */
/* 1 with the finger's place if the list holds a touch that is down, 2 if all it holds have
 * lifted, 0 if it is empty or cannot be read. *newest_up: the newest node is a lift (a down
 * and its lift both waiting: the down is used, then the lift). Read only, never altered. */
static int list_touch(int16_t *x, int16_t *y, int *newest_up) {
    volatile uint32_t *head = OS_TOUCH_HEAD;
    *newest_up = 0;
    if (head[5] == 0) return 0;
    uint32_t end = head[4], nodes = 0; int down = 0;
    if (end < 0x08000000u || end >= 0x10000000u) return 0;
    for (uint32_t node = *(volatile uint32_t *)end; node != end && nodes < 8; node = *(volatile uint32_t *)node, nodes++) {
        if (node < 0x08000000u || node >= 0x10000000u) return 0;
        volatile int32_t *value = (volatile int32_t *)(node + 8);
        *newest_up = *((volatile uint8_t *)(node + 8) + 4) == 1;
        if (*newest_up) continue;   /* status 1: lifted */
        *x = (int16_t)value[2]; *y = (int16_t)value[3]; down = 1;
    }
    return down ? 1 : nodes ? 2 : 0;
}
/* touch-snapshot: the launcher copies the newest sample of every OS touch queue at each
 * touch event, before the OS drains them, into a block right after hb_shared
 * (nanoapps/system/silver_resident_na/touch_latch.h). Polling the queue races that drain:
 * when the poll finds the queue empty, a snapshot taken since the last poll stands in for
 * it. Older launchers have no snapshot (the magic differs) and nothing changes. */
#define FN_SNAP_MAGIC 0x504e5354u
typedef struct { uint32_t magic; volatile uint32_t seq; volatile int32_t finger[8][4]; } fn_snap_t;
static uint32_t snap_seq, t_from_snap;
static int snap_touch(int16_t *x, int16_t *y) {
    if (!hb_shared) return 0;
    volatile fn_snap_t *sp = (volatile fn_snap_t *)((char *)hb_shared + sizeof(hb_shared_t));
    if (sp->magic != FN_SNAP_MAGIC) return 0;
    uint32_t seq = sp->seq;
    if (seq == snap_seq) return 0;
    snap_seq = seq;
    uint32_t st = (uint32_t)sp->finger[0][1] & 0xffu;
    if (st == 0xffu) return 0;
    if (st == 1u) return 2;                     /* status 1: lifted */
    *x = (int16_t)sp->finger[0][2]; *y = (int16_t)sp->finger[0][3];
    return 1;
}
static void logf_(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void fn_nano_poll(void) {
    static fn_tpick_t pick;
    if (state != 1) return;
    hb_spoint_t f; hb_surface_touch_read(&f);
    int16_t lx = 0, ly = 0, mx = f.x, my = f.y;
    int newest_up, listed = list_touch(&lx, &ly, &newest_up);
    if (listed) { int16_t sx, sy; (void)snap_touch(&sx, &sy); }   /* the queue had it: just catch up */
    else if ((listed = snap_touch(&lx, &ly)) != 0) t_from_snap++;
    for (int pass = 0; pass < 2; pass++) {
        int down;
        f.x = mx; f.y = my;
        switch (fn_tpick(&pick, (uint32_t)plat_time_us(), listed, lx, ly, f.down, f.x, f.y)) {
        case FN_TP_LIST:    down = 1; f.x = lx; f.y = ly; t_from_list++; break;
        case FN_TP_MAILBOX: down = 1; t_from_mb++; break;
        case FN_TP_STALE:   down = 0; t_stale++; break;
        case FN_TP_HELD:    down = 0; t_held++; break;
        default:            down = 0; break;
        }
        float x = down ? (float)f.x : last_x, y = down ? (float)f.y : last_y;   /* a touch ends where it was last seen */
        if (down && !was_down) {
            uint64_t now = plat_time_us();
            if (y < 28.f || y > 404.f) gesture = 1;                             /* top/bottom strip: pan */
            else if (last_tap_us && now - last_tap_us < 400000u
                     && fabsf(x - last_tap_x) < 24.f && fabsf(y - last_tap_y) < 24.f) {
                ab_zoom_by(zoom_dir > 0 ? 1.25f : 0.8f);
                zoom_dir = -zoom_dir;
                gesture = 2;                                                    /* swallow this tap */
            } else gesture = 0;
            last_tap_us = now; last_tap_x = x; last_tap_y = y;
        }
        if (gesture == 1 && down && was_down) ab_pan_by(x - last_x, y - last_y);
        if (down != was_down || (down && (x != last_x || y != last_y))) {
            if (down && !was_down && ++t_presses <= 3) t_report = 1;
            if (!down && was_down) { t_lifts++; gesture = 0; }
            was_down = down; last_x = x; last_y = y; t_places++;
            if (gesture == 0) {
                if (tq_n < TQ) { tq[tq_n].x = x; tq[tq_n].y = y; tq[tq_n].down = down; tq_n++; }
                else if (!down || tq[TQ - 1].down) { tq[TQ - 1].x = x; tq[TQ - 1].y = y; tq[TQ - 1].down = down; }  /* never lose a release */
            }
        }
        if (listed != 1 || !newest_up) break;
        listed = 2;                     /* the list's newest node is the lift of what it held down */
    }
}

typedef uint32_t fn_jmp_buf[26] __attribute__((aligned(8)));
extern int fn_setjmp(fn_jmp_buf b) __attribute__((returns_twice));
extern void fn_longjmp(fn_jmp_buf b, int v) __attribute__((noreturn));
static fn_jmp_buf unwind;
static int in_engine;
/* a fatal in the engine (bad guest access, out of guest memory) unwinds to the frame and the
 * app stays idle with the reason in log.txt; it never traps, which would reboot the iPod */
void fn_nano_fatal(void) {
    state = 2;
    if (in_engine) fn_longjmp(unwind, 1);
}
static void logf_(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void logf_(const char *fmt, ...) {
    char b[200]; va_list ap; va_start(ap, fmt); vsnprintf(b, sizeof b, fmt, ap); va_end(ap); plat_log_line(b);
}
static void mb(char *o, uint32_t n) { snprintf(o, 12, "%u.%uM", (unsigned)(n >> 20), (unsigned)(((n & 0xFFFFFu) * 10u) >> 20)); }
/* not enough memory: say so on the panel and in log.txt, and never touch the engine */
static int short_of_memory(uint32_t free0, uint32_t largest, uint32_t need_free, uint32_t need_largest) {
    char a[12], b[12], c[12], d[12];
    mb(a, free0); mb(b, largest); mb(c, need_free); mb(d, need_largest);
    snprintf(note[0], sizeof note[0], "FREE %s BIG %s", a, b);
    snprintf(note[1], sizeof note[1], "NEEDS %s BIG %s", c, d);
    static const char *const head[] = {"ANGRY BIRDS", "NOT ENOUGH MEMORY", ""};
    note_n = 0;
    for (int i = 0; i < 3; i++) note_lines[note_n++] = head[i];
    note_lines[note_n++] = note[0];
    note_lines[note_n++] = note[1];
    note_lines[note_n++] = "PAUSE MUSIC, QUIT,";
    note_lines[note_n++] = "THEN OPEN IT AGAIN";
    logf_("nano7kit not enough memory: heap free %u largest %u, needs %u and %u (music playing?); not starting",
          (unsigned)free0, (unsigned)largest, (unsigned)need_free, (unsigned)need_largest);
    return -3;
}
extern uint32_t host_in_guest, host_outside, host_outside_peak, host_outside_count;
static int start(int w, int h) {
    be_panel_w = w; be_panel_h = h;
    uint32_t free_a = hb_os_heap_free(), largest_a = hb_os_heap_largest();
    /* first delete the textures an earlier nano7kit port (this one included) left in the
     * resident's shared GL context, then measure: see gl_left.c */
    gll_init(DATA_DIR_GL "/gl_names.txt");
    uint32_t swept = gll_sweep();
    uint32_t free0 = hb_os_heap_free(), largest = hb_os_heap_largest();
    logf_("nano7kit Angry Birds: panel %dx%d, heap free %u, largest %u; %u old texture names deleted, then free %u, largest %u",
          w, h, (unsigned)free_a, (unsigned)largest_a, (unsigned)swept, (unsigned)free0, (unsigned)largest);
    uint32_t size = largest > BLOCK_MAX + 0x10000u ? BLOCK_MAX : (largest - 0x10000u) & ~0xFFFFu;
    /* pre-flight, before any big allocation: the OS's own allocator reboots the iPod when it
     * runs dry, so the block and the GL driver's share must both be there up front */
    if (largest < BLOCK_MIN + 0x10000u || size < BLOCK_MIN || free0 < size + AFTER_MIN)
        return short_of_memory(free0, largest, BLOCK_MAX + AFTER_MIN, BLOCK_MIN + 0x10000u);
    block = hb_os_alloc(size);
    if (!block) { logf_("nano7kit guest block: %u bytes refused", (unsigned)size); return -1; }
    HB_APP_AUX_BLOCK = block;           /* the resident frees it when the app unloads */
    uint32_t after = hb_os_heap_free();
    /* less free memory: smaller textures, fewer triangles */
    /* textures were halved at convert time; a further runtime shift would miss the .dat coords */
    fe_tex_big_shift = 0;
    fe_tex_shift = 0;
    logf_("nano7kit guest block %u bytes; heap free after %u; texture shift %d/%d (pre-scaled)",
          (unsigned)size, (unsigned)after, fe_tex_shift, fe_tex_big_shift);
    if (rt_init(block, size)) { logf_("nano7kit guest image did not load (Data/AngryBirds/guest.img)"); return -1; }
    uint64_t t0 = plat_time_us();
    if (ab_start()) return -1;
    uint32_t left = hb_os_heap_free();
    int b = (int)(left / BYTES_PER_TRIANGLE);
    be_budget = b < 300 ? 300 : b > 1200 ? 1200 : b;
    logf_("nano7kit started in %u ms: heap free %u largest %u, guest heap %u of %u (peak %u hole %u, front end %u), "
          "outside the block %u now (%u peak, %u allocations), decode %u now (%u peak, %u), textures %d (%d bytes), budget %d",
          (unsigned)((plat_time_us() - t0) / 1000u), (unsigned)left, (unsigned)hb_os_heap_largest(),
          (unsigned)rt_heap_used(), (unsigned)rt_heap_size(), (unsigned)rt_heap_peak(),
          (unsigned)rt_heap_largest_free(), (unsigned)host_in_guest,
          (unsigned)host_outside, (unsigned)host_outside_peak, (unsigned)host_outside_count,
          (unsigned)rt_outside_used(), (unsigned)rt_outside_peak(), (unsigned)rt_outside_count(),
          fe_stat_tex_count, fe_stat_tex_bytes, be_budget);
    plat_log_flush();
    gll_save();
    return 0;
}
void fn_nano_frame(int w, int h, uint32_t n) {
    (void)n;
    if (state == 3) { notice_draw(w, h, note_lines, note_n); gll_save(); return; }
    if (state == 2) {
        static const char *const stopped[] = {"ANGRY BIRDS", "STOPPED", "", "SEE LOG.TXT IN", "DATA/ANGRYBIRDS"};
        notice_draw(w, h, stopped, 5);
        gll_save();
        return;
    }
    if (fn_setjmp(unwind)) { in_engine = 0; state = 2; plat_log_flush(); return; }
    in_engine = 1;
    if (state == 1) {
        int up = hb_button_pressed(HB_BTN_VOL_UP), dn = hb_button_pressed(HB_BTN_VOL_DOWN);
        if (up && !vol_up_was) ab_zoom_by(1.25f);
        if (dn && !vol_dn_was) ab_zoom_by(0.8f);
        vol_up_was = up;
        vol_dn_was = dn;
    }
    if (state == 0) {
        int r = start(w, h);
        if (r) { in_engine = 0; state = r == -3 ? 3 : 2; plat_log_flush(); gll_save(); return; }
        state = 1; last_us = plat_time_us();
    }
    fn_nano_poll();
    uint64_t now = plat_time_us();
    float dt = (float)(now - last_us) / 1e6f;
    last_us = now;
    if (dt > 0.1f) dt = 0.1f;
    if (dt < 0.001f) dt = 0.001f;
    ab_tsample q[TQ];
    int nq = tq_n;
    memcpy(q, tq, (size_t)nq * sizeof q[0]);
    tq_n = 0;
    /* nothing new and still down: ab_frame_q keeps the touch down and runs one still step */
    uint32_t dt_ms = (uint32_t)(dt * 1000.f + 0.5f);
    if (!w_frames || dt_ms < w_dt_min) w_dt_min = dt_ms;
    if (dt_ms > w_dt_max) w_dt_max = dt_ms;
    w_frames++;
    if (was_down || nq) w_down++;
    if (!nq && was_down) { w_dry++; if (++w_dry_run > w_dry_max) w_dry_max = w_dry_run; } else w_dry_run = 0;
    ab_frame_q(dt, q, nq);
    if (frames == 0 || frames % 90u == 0)
        ab_phys_log(frames == 0 ? "start" : "tick");
    if (t_report) {
        t_report = 0;
        logf_("nano7kit press %u at %d,%d: %u samples from the OS list, %u from the mailbox, %u stale",
              (unsigned)t_presses, (int)last_x, (int)last_y, (unsigned)t_from_list, (unsigned)t_from_mb, (unsigned)t_stale);
    }
    in_engine = 0;
    frames++;
    uint32_t ms = (uint32_t)(now / 1000u);
    if (!w_ms0) w_ms0 = ms;
    if (ms - w_ms0 >= 5000u) {          /* touch and timing, every 5 s in which the finger was down */
        if (w_down) {
            uint32_t span = ms - w_ms0;
            logf_("nano7kit touch %us: %u fps, frame %u-%u ms; down %u frames, %u with nothing new (run %u); "
                  "press %u lift %u places %u; list %u snap %u mb %u stale %u held %u",
                  (unsigned)(ms / 1000u), (unsigned)(w_frames * 1000u / (span ? span : 1u)), (unsigned)w_dt_min, (unsigned)w_dt_max,
                  (unsigned)w_down, (unsigned)w_dry, (unsigned)w_dry_max, (unsigned)t_presses, (unsigned)t_lifts,
                  (unsigned)t_places, (unsigned)t_from_list, (unsigned)t_from_snap, (unsigned)t_from_mb,
                  (unsigned)t_stale, (unsigned)t_held);
            plat_log_flush();
        }
        w_ms0 = ms; w_frames = w_down = w_dry = w_dry_max = w_dt_max = 0; w_dt_min = 0;
    }
    if (frames == 60 || ms - last_flush_ms > 20000u) {
        logf_("nano7kit frame %u: %d tris (%d small, %d over budget), %d draws, heap free %u largest %u, guest heap %u hole %u, textures %d live %d failed %d bytes, archive reads %u (%u KB)",
              (unsigned)frames, be_stat_tris, be_stat_tiny, be_stat_over, be_stat_draws, (unsigned)hb_os_heap_free(),
              (unsigned)hb_os_heap_largest(), (unsigned)rt_heap_used(), (unsigned)rt_heap_largest_free(),
              be_stat_live_tex, be_stat_tex_fail,
              fe_stat_tex_bytes, (unsigned)rt_varch_reads, (unsigned)(rt_varch_bytes >> 10));
        ab_phys_log("flush");
        if (host_outside_count)
            logf_("nano7kit front end outside the block: %u bytes now, %u peak, %u allocations",
                  (unsigned)host_outside, (unsigned)host_outside_peak, (unsigned)host_outside_count);
        if (rt_outside_count())
            logf_("nano7kit decode outside the block: %u bytes now, %u peak, %u allocations",
                  (unsigned)rt_outside_used(), (unsigned)rt_outside_peak(), (unsigned)rt_outside_count());
        plat_log_flush();
        gll_save();
        last_flush_ms = ms;
    }
}
void fn_nano_reset(void) {
    plat_nano_reset();
    fngl_reset();
    notice_reset();
    vol_up_was = vol_dn_was = 0;
}
