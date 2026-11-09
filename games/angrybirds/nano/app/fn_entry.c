/* fn_entry.c: NanoApps entry for Android Angry Birds (nano7kit, MIT).
 * Thumb, no floating point: it attaches the task's FPU context before any ARM
 * library code (fngl_reset stores floats; the v7a .so is ARM VFPv3). The OS
 * panics if a VFP insn runs on a task with a NULL fp_context. */
#include <stdint.h>
#include "hb_sdk.h"
#include "hb_shared.h"
#include "hb_heap.h"
#include "ab_crumb.h"

extern void fn_nano_frame(int w, int h, uint32_t frame);
extern void fn_nano_reset(void);

static int s_attached;
static void frame_callback(int w, int h, uint32_t frame) {
    hb_fpu_status_t st;
    int r;
    if (!s_attached) ab_crumb("first frame callback, attaching the FPU", frame);
    r = hb_fpu_attach_current_task(&st);
    if (r < 0) {
        if (!s_attached) ab_crumb("FPU attach FAILED", (uint32_t)r);
        s_attached = -1;
        return;
    }
    if (s_attached <= 0) {
        s_attached = 1;
        hb_trace_log("ABFPUFR ", (uint32_t)r, st.context);
        ab_crumb("FPU attached on frame task", (uint32_t)w << 16 | (uint32_t)h);
    }
    fn_nano_frame(w, h, frame);
}
__attribute__((section(".text.entry"), used, noinline))
void *payload_entry(int op, void *fb, int w, int h, hb_shared_t *sh) {
    extern uint32_t __bss_start__[], __bss_end__[];
    hb_fpu_status_t st;
    int r;
    (void)fb; (void)w; (void)h;
    if (op != 0 || !sh || sh->magic != HB_SHARED_MAGIC) return 0;
    for (uint32_t *p = __bss_start__; p < __bss_end__; p++) *p = 0;
    hb_shared = sh;
    if (HB_APP_AUX_BLOCK) { hb_os_free(HB_APP_AUX_BLOCK); HB_APP_AUX_BLOCK = 0; }
    ab_crumb_reset();
    s_attached = 0;
    hb_trace_log("ABENTRY ", (uint32_t)(uintptr_t)&payload_entry, hb_os_heap_largest());
    ab_crumb("entry: image loaded and relocated, bss zeroed",
             (uint32_t)(uintptr_t)&payload_entry);
    /* Thumb soft-float: attach before fn_nano_reset / fngl_reset (ARM VFP). */
    r = hb_fpu_attach_current_task(&st);
    hb_trace_log("ABFPU   ", (uint32_t)r, st.context);
    ab_crumb("FPU attach on entry task", (uint32_t)r);
    if (r < 0) return (void *)frame_callback;
    fn_nano_reset();
    ab_crumb("reset after FPU", 0);
    return (void *)frame_callback;
}
