/* fn_entry.c: NanoApps entry for the recompiled Angry Birds (nano7kit, MIT).
 * Thumb, no floating point: it attaches the task's FPU context and calls the ARM library. */
#include <stdint.h>
#include "hb_sdk.h"
#include "hb_shared.h"
#include "hb_heap.h"

extern void fn_nano_frame(int w, int h, uint32_t frame);
extern void fn_nano_reset(void);

static void frame_callback(int w, int h, uint32_t frame) {
    hb_fpu_status_t st;
    if (hb_fpu_attach_current_task(&st) < 0) return;
    fn_nano_frame(w, h, frame);
}
__attribute__((section(".text.entry"), used, noinline))
void *payload_entry(int op, void *fb, int w, int h, hb_shared_t *sh) {
    extern uint32_t __bss_start__[], __bss_end__[];
    (void)fb; (void)w; (void)h;
    if (op != 0 || !sh || sh->magic != HB_SHARED_MAGIC) return 0;
    for (uint32_t *p = __bss_start__; p < __bss_end__; p++) *p = 0;
    hb_shared = sh;
    if (HB_APP_AUX_BLOCK) { hb_os_free(HB_APP_AUX_BLOCK); HB_APP_AUX_BLOCK = 0; }
    fn_nano_reset();
    return (void *)frame_callback;
}
