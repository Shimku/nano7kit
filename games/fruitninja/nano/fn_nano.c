/* fn_nano.c: the nano frame loop for the recompiled Fruit Ninja (nano7kit, MIT).
 * The first frame (GL context current) allocates the guest block, loads guest.img and runs
 * the engine's start-up; then each frame feeds the touch and steps the engine. The game is
 * landscape drawn into a portrait 320x480 frame; on the 240x432 panel it fills the panel (0.75
 * across, 0.9 along), so the iPod is held sideways as the iPhone was. */
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
#include "../rt/fn_entry.h"
#include "notice.h"
#include "gl_left.h"
#include "touch_pick.h"

#define BLOCK_MAX   0x300000u           /* 3.0 MB: guest image 1.0 MB + heap 1.75 MB (engine ~1 MB, and
                                           the front end's atlas and buffers) + 256 KB stack */
#define BLOCK_MIN   0x260000u           /* 2.4 MB: below this the engine's heap peak (~1 MB) has no room */
#define GL_KEEP     0x140000u           /* left to the GL driver before textures (host/fruitninja_rc.py); was 1.5 MB with the 256 KB atlas outside the block */
#define DATA_DIR_GL "/Apps/Data/FruitNinja"
#define BYTES_PER_TRIANGLE 3000u        /* the GL driver's cost per triangle, as measured for Temple Run */
#define AFTER_MIN   0x180000u           /* free after the block: smallest textures 0.63 MB + 300 triangles
                                           (0.9 MB). An earlier build asked 0x1C0000 with the atlas and scratch
                                           outside the block; now they are inside. Under it: notice */
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
static fn_tsample tq[TQ];
static int tq_n, t_report;
static uint32_t t_presses, t_from_list, t_from_mb, t_stale, t_held;
/* 1 with the finger's place if the list holds a touch that is down, 2 if all it holds have
 * lifted, 0 if it is empty or cannot be read. Read only, never altered. */
static int list_touch(int16_t *x, int16_t *y) {
    volatile uint32_t *head = OS_TOUCH_HEAD;
    if (head[5] == 0) return 0;
    uint32_t end = head[4], nodes = 0; int down = 0;
    if (end < 0x08000000u || end >= 0x10000000u) return 0;
    for (uint32_t node = *(volatile uint32_t *)end; node != end && nodes < 8; node = *(volatile uint32_t *)node, nodes++) {
        if (node < 0x08000000u || node >= 0x10000000u) return 0;
        volatile int32_t *value = (volatile int32_t *)(node + 8);
        if (*((volatile uint8_t *)(node + 8) + 4) == 1) continue;   /* status 1: lifted */
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
/* Diagnostic, read only: the 8 OS touch queue heads (0x18 bytes each from OS_TOUCH_HEAD),
 * with the length of each queue's node list (from head[4]) and of the chain at head[1]. */
static void logf_(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static uint32_t chain_len(uint32_t p, uint32_t stop) {
    uint32_t n = 0;
    while (p && p != stop && n < 999u) {
        if (p < 0x08000000u || p >= 0x0C000000u || (p & 3u)) return 1000u + n;   /* outside the low 64 MB: not followed */
        p = *(volatile uint32_t *)p; n++;
    }
    return n;
}
static void log_queues(const char *when) {
    for (uint32_t q = 0; q < 8; q++) {
        volatile uint32_t *h = (volatile uint32_t *)(0x089a5298u + q * 0x18u);
        uint32_t end = h[4];
        uint32_t len = end >= 0x08000000u && end < 0x0C000000u ? chain_len(*(volatile uint32_t *)end, end) : 9999u;
        logf_("nano7kit queue %s %u: %08x %08x %08x %08x %08x n=%u walk=%u chain1=%u",
              when, (unsigned)q, (unsigned)h[0], (unsigned)h[1], (unsigned)h[2], (unsigned)h[3], (unsigned)end,
              (unsigned)h[5], (unsigned)len, (unsigned)chain_len(h[1], 0));
    }
}
void fn_nano_poll(void) {
    static fn_tpick_t pick;
    if (state != 1) return;
    hb_spoint_t f; hb_surface_touch_read(&f);
    int16_t lx = 0, ly = 0;
    int listed = list_touch(&lx, &ly), down;
    if (listed) { int16_t sx, sy; (void)snap_touch(&sx, &sy); }   /* the queue had it: just catch up */
    else if ((listed = snap_touch(&lx, &ly)) != 0) t_from_snap++;
    switch (fn_tpick(&pick, (uint32_t)plat_time_us(), listed, lx, ly, f.down, f.x, f.y)) {
    case FN_TP_LIST:    down = 1; f.x = lx; f.y = ly; t_from_list++; break;
    case FN_TP_MAILBOX: down = 1; t_from_mb++; break;
    case FN_TP_STALE:   down = 0; t_stale++; break;
    case FN_TP_HELD:    down = 0; t_held++; break;
    default:            down = 0; break;
    }
    float x = down ? (float)f.x : last_x, y = down ? (float)f.y : last_y;   /* a touch ends where it was last seen */
    if (down == was_down && (!down || (x == last_x && y == last_y))) return;
    if (down && !was_down && ++t_presses <= 3) t_report = 1;
    was_down = down; last_x = x; last_y = y;
    if (tq_n < TQ) { tq[tq_n].x = x; tq[tq_n].y = y; tq[tq_n].down = down; tq_n++; }
    else if (!down || tq[TQ - 1].down) { tq[TQ - 1].x = x; tq[TQ - 1].y = y; tq[TQ - 1].down = down; }  /* never lose a release */
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
    static const char *const head[] = {"FRUIT NINJA", "NOT ENOUGH MEMORY", ""};
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
    log_queues("start");
    gll_init(DATA_DIR_GL "/gl_names.txt");
    uint32_t swept = gll_sweep();
    uint32_t free0 = hb_os_heap_free(), largest = hb_os_heap_largest();
    logf_("nano7kit Fruit Ninja: panel %dx%d, heap free %u, largest %u; %u old texture names deleted, then free %u, largest %u",
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
    fe_tex_big_shift = after < GL_KEEP + 0x200000u ? 2 : 1;
    fe_tex_shift = after < GL_KEEP + 0xC0000u ? 2 : 1;
    logf_("nano7kit guest block %u bytes; heap free after %u; texture shift %d/%d",
          (unsigned)size, (unsigned)after, fe_tex_shift, fe_tex_big_shift);
    if (rt_init(block, size)) { logf_("nano7kit guest image did not load (Data/FruitNinja/guest.img)"); return -1; }
    uint64_t t0 = plat_time_us();
    if (fn_start()) return -1;
    uint32_t left = hb_os_heap_free();
    int b = (int)(left / BYTES_PER_TRIANGLE);
    be_budget = b < 300 ? 300 : b > 1200 ? 1200 : b;
    logf_("nano7kit started in %u ms: heap free %u largest %u, guest heap %u of %u (peak %u, front end %u), "
          "outside the block %u now (%u peak, %u allocations), textures %d (%d bytes), budget %d",
          (unsigned)((plat_time_us() - t0) / 1000u), (unsigned)left, (unsigned)hb_os_heap_largest(),
          (unsigned)rt_heap_used(), (unsigned)rt_heap_size(), (unsigned)rt_heap_peak(), (unsigned)host_in_guest,
          (unsigned)host_outside, (unsigned)host_outside_peak, (unsigned)host_outside_count,
          fe_stat_tex_count, fe_stat_tex_bytes, be_budget);
    plat_log_flush();
    gll_save();
    return 0;
}
void fn_nano_frame(int w, int h, uint32_t n) {
    (void)n;
    if (state == 3) { notice_draw(w, h, note_lines, note_n); gll_save(); return; }
    if (state == 2) {
        static const char *const stopped[] = {"FRUIT NINJA", "STOPPED", "", "SEE LOG.TXT IN", "DATA/FRUITNINJA"};
        notice_draw(w, h, stopped, 5);
        gll_save();
        return;
    }
    if (fn_setjmp(unwind)) { in_engine = 0; state = 2; plat_log_flush(); return; }
    in_engine = 1;
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
    fn_tsample q[TQ];
    int nq = tq_n;
    memcpy(q, tq, (size_t)nq * sizeof q[0]);
    tq_n = 0;
    if (!nq && was_down) { q[0].x = last_x; q[0].y = last_y; q[0].down = 1; nq = 1; }   /* held still: keep it down */
    fn_frame_q(dt, q, nq);
    if (t_report) {
        t_report = 0;
        logf_("nano7kit press %u at %d,%d: %u samples from the OS list, %u from the mailbox, %u stale",
              (unsigned)t_presses, (int)last_x, (int)last_y, (unsigned)t_from_list, (unsigned)t_from_mb, (unsigned)t_stale);
    }
    in_engine = 0;
    frames++;
    uint32_t ms = (uint32_t)(now / 1000u);
    if (frames == 60 || ms - last_flush_ms > 20000u) {
        logf_("nano7kit frame %u: %d tris (%d small, %d over budget), %d draws, heap free %u, guest heap %u, textures %d live %d failed, archive reads %u (%u KB)",
              (unsigned)frames, be_stat_tris, be_stat_tiny, be_stat_over, be_stat_draws, (unsigned)hb_os_heap_free(),
              (unsigned)rt_heap_used(), be_stat_live_tex, be_stat_tex_fail, (unsigned)rt_varch_reads, (unsigned)(rt_varch_bytes >> 10));
        logf_("nano7kit touch: %u presses; polls %u from the OS list (%u of them from the launcher snapshot), %u from the mailbox, %u stale, %u held unconfirmed",
              (unsigned)t_presses, (unsigned)t_from_list, (unsigned)t_from_snap, (unsigned)t_from_mb, (unsigned)t_stale, (unsigned)t_held);
        log_queues("run");
        if (host_outside_count)
            logf_("nano7kit front end outside the block: %u bytes now, %u peak, %u allocations",
                  (unsigned)host_outside, (unsigned)host_outside_peak, (unsigned)host_outside_count);
        plat_log_flush();
        gll_save();
        last_flush_ms = ms;
    }
}
void fn_nano_reset(void) { plat_nano_reset(); fngl_reset(); notice_reset(); }
