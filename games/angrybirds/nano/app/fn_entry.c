/* fn_entry.c: NanoApps entry for Android Angry Birds (nano7kit, MIT).
 * Thumb, no floating point. The launcher calls payload_entry on the Home UI
 * task, then push_screen (the first chance at a black GL view). A panic here
 * reboots with no loading screen. Do not run ARM, VFP, constructors, fngl, or
 * the .so before returning the frame callback. The first GL draw attaches FPU
 * (this task may differ from the UI task) and only then calls fn_nano_reset. */
#include <stdint.h>
#include "hb_sdk.h"
#include "hb_shared.h"
#include "hb_heap.h"
#include "ab_crumb.h"

extern void fn_nano_frame(int w, int h, uint32_t frame);
extern void fn_nano_reset(void);

static int s_attached;
static int s_reset;
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
    if (!s_reset) {
        ab_crumb("reset on first frame after FPU", 0);
        fn_nano_reset();
        s_reset = 1;
        ab_crumb("reset returned", 0);
    }
    fn_nano_frame(w, h, frame);
}
__attribute__((section(".text.entry"), used, noinline))
void *payload_entry(int op, void *fb, int w, int h, hb_shared_t *sh) {
    extern uint32_t __bss_start__[], __bss_end__[];
    (void)fb; (void)w; (void)h;
    if (op != 0 || !sh || sh->magic != HB_SHARED_MAGIC) return 0;
    /* Trace before BSS: hb_shared is in BSS, so restore it after the wipe.
     * The DRAM ring is only dumped after a panic reboot; a watchdog leaves
     * no lasttrace.bin. log.txt is the device log. */
    hb_shared = sh;
    hb_trace_log("ABENTRY ", (uint32_t)(uintptr_t)&payload_entry, hb_os_heap_largest());
    for (uint32_t *p = __bss_start__; p < __bss_end__; p++) *p = 0;
    hb_shared = sh;
    if (HB_APP_AUX_BLOCK) { hb_os_free(HB_APP_AUX_BLOCK); HB_APP_AUX_BLOCK = 0; }
    ab_crumb_reset();
    s_attached = 0;
    s_reset = 0;
    ab_crumb("entry: relocated, bss zeroed, returning before push_screen",
             (uint32_t)(uintptr_t)&payload_entry);
    return (void *)frame_callback;
}
