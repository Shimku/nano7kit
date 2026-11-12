/* n7_ab.h: nano7kit's glue for olievans123/nano-angrybirds (nano7kit, MIT).
 * Built into libab_nano.a next to nano7kit's notice.c and gl_left.c. The calls into it are
 * nano7kit's anchored edits to the pinned port (host/angrybirds_patch.py). */
#ifndef N7_AB_H
#define N7_AB_H
#include <stdint.h>

extern uint32_t n7_entry_free, n7_entry_largest;  /* ab_entry.c payload_entry, before any malloc */

void n7_ab_early(void);                     /* first GL frame, FPU attached, before the arena exists */
int n7_ab_preflight(void);                  /* before the engine starts: nonzero means do not start */
uint32_t n7_ab_texture_budget(uint32_t upstream);
void n7_ab_after_init(int rc);              /* game_init returned rc */
void n7_ab_tick(void);                      /* every running frame */
void n7_ab_dead_screen(int w, int h);       /* the engine stopped (or never started) */
void n7_ab_gl_name(uint32_t name);          /* gfx.c: a texture name the game was given */
#endif
