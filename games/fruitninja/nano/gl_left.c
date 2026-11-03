/* gl_left.c: delete the GL textures a quit nano7kit port left behind (nano7kit, MIT).
 *
 * The resident gives an app no quit callback and keeps ONE cached GL view (and so one GL
 * context) for every GL app it launches, so textures an app created are still alive in that
 * context after it quits. On hardware, after Fruit Ninja, Probe read 6.5 MB free / 2.0 MB
 * largest (4.0 before) and Temple Run then stopped with NOT ENOUGH MEMORY.
 *
 * Each port records the range of texture names it was given (glGenTextures hands them out
 * in order), in its own Data folder. The next port to start deletes every recorded range
 * before it creates any texture of its own, then clears the records. Only names a nano7kit
 * port was given are touched: deleting a name that is no longer a texture does nothing in
 * GL. No glIsTexture is needed, so no blind sweep of names the OS might use. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "hb_sdk.h"
#include "gl.h"
#include "gl_left.h"

#define GLL_SLACK 8192u                 /* hb_fs_read may fill whole sectors past the end */
#define GLL_MAX_SPAN 4096u              /* a record wider than this is not ours: ignore it */
static const char *const gll_records[] = {
    "/Apps/Data/FruitNinja/gl_names.txt",
    "/Apps/Data/TempleRun/gl_names.txt",
};
static const char *own;
static uint32_t lo, hi, saved_lo, saved_hi;
static char rbuf[64 + GLL_SLACK] __attribute__((aligned(64)));

void gll_init(const char *own_file) { own = own_file; lo = hi = saved_lo = saved_hi = 0; }
void gll_note(uint32_t name) {
    if (!name) return;
    if (!lo || name < lo) lo = name;
    if (name > hi) hi = name;
}
static void write_record(const char *path, uint32_t a, uint32_t b) {
    static char line[64] __attribute__((aligned(64)));   /* as Temple Run's sync.txt: aligned, not stack */
    int n = snprintf(line, sizeof line, "%u %u\n", (unsigned)a, (unsigned)b);
    hb_fs_write(path, line, (uint32_t)n);
}
uint32_t gll_sweep(void) {
    uint32_t deleted = 0;
    for (unsigned r = 0; r < sizeof gll_records / sizeof gll_records[0]; r++) {
        const char *path = gll_records[r];
        if (!hb_fs_exists(path)) continue;
        memset(rbuf, 0, 64);
        uint32_t got = hb_fs_read(path, rbuf, sizeof rbuf);
        if (!got) continue;
        rbuf[got < 63 ? got : 63] = 0;
        char *end;
        unsigned long a = strtoul(rbuf, &end, 10), b = strtoul(end, NULL, 10);
        if (a && b >= a && b - a < GLL_MAX_SPAN) {
            GLuint names[64];
            for (unsigned long n = a; n <= b;) {
                int k = 0;
                while (k < 64 && n <= b) names[k++] = (GLuint)n++;
                glDeleteTextures(k, names);
                deleted += (uint32_t)k;
            }
        }
        if (a || b) write_record(path, 0, 0);
    }
    return deleted;
}
void gll_save(void) {
    if (!own || (lo == saved_lo && hi == saved_hi)) return;
    write_record(own, lo, hi);
    saved_lo = lo; saved_hi = hi;
}
