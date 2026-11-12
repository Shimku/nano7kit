/* n7_ab.c: nano7kit's glue for olievans123/nano-angrybirds on the NanoApps base (nano7kit, MIT).
 *
 * olievans123's port has no OSS licence, so nano7kit never ships his files: host/angrybirds.py
 * fetches the pinned commit, host/angrybirds_patch.py adds a few calls into this file, and the
 * result is built on the user's Mac. Everything here is nano7kit's own.
 *
 * What it adds to the port:
 *  1. Textures a quit nano7kit GL port left in the resident's one shared GL context (Fruit
 *     Ninja, Temple Run, PvZ, and Angry Birds' own previous launch) are deleted on the first GL
 *     frame, before the first malloc makes the game's arena (gl_left.c). The port does not
 *     know about them, and the driver reboots the iPod past about 80 live textures.
 *  2. A memory pre-flight before the engine starts. The OS allocator and the GL driver reboot
 *     the iPod when they run dry, so the game refuses to start, and says so on the panel
 *     (notice.c), unless its 2 MB arena is whole and the texture pages plus the driver's
 *     per-frame share still fit in what is left.
 *  3. The texture budget follows that free memory (upstream is a fixed 3 MB). The port evicts
 *     least-recently-used pages under its budget, so a smaller budget means more page reloads,
 *     not a reboot.
 *  4. A notice instead of a plain red or blank screen when the engine stops, naming log.txt.
 *  5. The texture names the game was given are written to its own gl_names.txt (at most every
 *     3 s, only when they changed), so the next nano7kit port can delete them.
 */
#include <stdint.h>
#include <stdio.h>
#include "hb_sdk.h"
#include "hb_heap.h"
#include "gl.h"
#include "gl_left.h"
#include "notice.h"
#include "n7_ab.h"

#define DATA_DIR "/Apps/Data/AngryBirds"

/* Bytes. host/angrybirds.py reads these four lines for its plan and tests. */
#define N7_ARENA_NEED   0x200000u   /* nano_alloc.c PORT_ARENA_MIN: the Lua + Box2D arena */
#define N7_TEX_MAX      0x300000u   /* nano_platform.c TEXTURE_BUDGET */
#define N7_TEX_MIN      0x1C0000u   /* below this the level's own pages stop fitting */
#define N7_DRIVER       0x140000u   /* GL driver per-frame share plus OS headroom, kept free */

extern uint32_t port_arena_size(void);
extern uint32_t port_arena_largest_free(void);

uint32_t n7_entry_free, n7_entry_largest;
static uint32_t free0, largest0, free1, largest1, swept, budget;
static int early_done, refused, saved_once;
static uint32_t last_save_us;
static char note[2][24];
static const char *lines[7];
static int nlines;

static void mb(char *o, uint32_t n) {
    snprintf(o, 12, "%u.%uM", (unsigned)(n >> 20), (unsigned)(((n & 0xFFFFFu) * 10u) >> 20));
}

void n7_ab_early(void) {
    if (early_done) return;
    early_done = 1;
    free0 = hb_os_heap_free();
    largest0 = hb_os_heap_largest();
    gll_init(DATA_DIR "/gl_names.txt");
    swept = gll_sweep();
    free1 = hb_os_heap_free();
    largest1 = hb_os_heap_largest();
}

static void memory_notice(uint32_t f, uint32_t l) {
    char a[12], b[12], c[12], d[12];
    mb(a, f); mb(b, l);
    mb(c, N7_ARENA_NEED + N7_TEX_MIN + N7_DRIVER); mb(d, N7_ARENA_NEED);
    snprintf(note[0], sizeof note[0], "FREE %s BIG %s", a, b);
    snprintf(note[1], sizeof note[1], "NEEDS %s BIG %s", c, d);
    lines[0] = "ANGRY BIRDS"; lines[1] = "NOT ENOUGH MEMORY"; lines[2] = "";
    lines[3] = note[0]; lines[4] = note[1];
    lines[5] = "PAUSE MUSIC, QUIT,"; lines[6] = "THEN OPEN IT AGAIN";
    nlines = 7;
}

int n7_ab_preflight(void) {
    uint32_t arena = port_arena_size(), f = hb_os_heap_free(), l = hb_os_heap_largest();
    uint32_t room = f > N7_DRIVER ? f - N7_DRIVER : 0u;
    printf("nano7kit: heap free %u largest %u at entry\n", (unsigned)n7_entry_free, (unsigned)n7_entry_largest);
    printf("nano7kit: %u old texture names deleted; heap free %u largest %u before, %u and %u after\n",
           (unsigned)swept, (unsigned)free0, (unsigned)largest0, (unsigned)free1, (unsigned)largest1);
    budget = (room < N7_TEX_MAX ? room : N7_TEX_MAX) & ~0xFFFFu;
    printf("nano7kit: arena %u (needs %u); heap free %u largest %u after it; texture budget %u "
           "(max %u, min %u, driver reserve %u)\n",
           (unsigned)arena, (unsigned)N7_ARENA_NEED, (unsigned)f, (unsigned)l, (unsigned)budget,
           (unsigned)N7_TEX_MAX, (unsigned)N7_TEX_MIN, (unsigned)N7_DRIVER);
    if (arena >= N7_ARENA_NEED && budget >= N7_TEX_MIN) return 0;
    printf("nano7kit not enough memory: arena %u needs %u, heap free %u after it needs %u "
           "(music or Bluetooth on?); not starting\n",
           (unsigned)arena, (unsigned)N7_ARENA_NEED, (unsigned)f, (unsigned)(N7_TEX_MIN + N7_DRIVER));
    /* the panel shows what Probe would have read before the arena was taken */
    memory_notice(f + arena, arena > l ? arena : l);
    refused = 1;
    return 1;
}

uint32_t n7_ab_texture_budget(uint32_t upstream) {
    uint32_t b = budget && budget < upstream ? budget : upstream;
    if (b < upstream) printf("nano7kit: texture budget %u instead of %u (pages reload more often)\n",
                             (unsigned)b, (unsigned)upstream);
    return b;
}

void n7_ab_gl_name(uint32_t name) { gll_note(name); }

void n7_ab_after_init(int rc) {
    gll_save();
    saved_once = 1;
    last_save_us = hb_time_uptime_us();
    printf("nano7kit: game_init %d; arena largest free %u; heap free %u largest %u\n", rc,
           (unsigned)port_arena_largest_free(), (unsigned)hb_os_heap_free(), (unsigned)hb_os_heap_largest());
}

void n7_ab_tick(void) {
    uint32_t now = hb_time_uptime_us();
    if (saved_once && now - last_save_us < 3000000u) return;
    gll_save();                         /* writes only when the recorded range changed */
    saved_once = 1;
    last_save_us = now;
}

void n7_ab_dead_screen(int w, int h) {
    static int logged;
    if (!nlines) {
        lines[0] = "ANGRY BIRDS"; lines[1] = "STOPPED"; lines[2] = "";
        lines[3] = "THE REASON IS IN"; lines[4] = "/APPS/DATA/ANGRYBIRDS"; lines[5] = "LOG.TXT";
        lines[6] = "PRESS HOME TO QUIT";
        nlines = 7;
    }
    glBindBuffer(GL_ARRAY_BUFFER, 0);   /* notice.c draws from client arrays */
    glDisable(GL_SCISSOR_TEST);
    glDisable(0x0BC0);                 /* GL_ALPHA_TEST (not in the SDK's gl.h) */
    notice_draw(w, h, lines, nlines);
    if (!logged) {
        logged = 1;
        printf("nano7kit: %s screen shown\n", refused ? "not enough memory" : "stopped");
        gll_save();                     /* the notice texture too */
    }
}
