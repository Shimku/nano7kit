/* pvz_entry.c: NanoApps entry for PvZ (nano7kit, LGPL-3.0-or-later). Thumb, no floating point: it attaches the
 * task's FPU context and calls the ARM-state engine library (libpvz_nano.a), and writes breadcrumbs (pvz_crumb.c). */
#include <stdint.h>
#include "hb_sdk.h"
#include "hb_shared.h"
#include "hb_heap.h"
extern void pvz_nano_frame(int w, int h, uint32_t frame);
extern void pvz_nano_reset(void);
extern void pvz_step_reset(void);
extern void n7p_step_a(const char* what, uint32_t a);
static int s_attached;
static void frame_callback(int w, int h, uint32_t frame)
{
	hb_fpu_status_t st;
	if (!s_attached) n7p_step_a("first frame callback, attaching the FPU", frame);
	if (hb_fpu_attach_current_task(&st) < 0) { if (!s_attached) n7p_step_a("FPU attach FAILED", 0); s_attached = -1; return; }
	if (s_attached <= 0) { s_attached = 1; n7p_step_a("FPU attached", (uint32_t)w << 16 | (uint32_t)h); }
	pvz_nano_frame(w, h, frame);
}
__attribute__((section(".text.entry"), used, noinline))
void* payload_entry(int op, void* fb, int w, int h, hb_shared_t* sh)
{
	extern uint32_t __bss_start__[], __bss_end__[];
	(void)fb; (void)w; (void)h;
	if (op != 0 || !sh || sh->magic != HB_SHARED_MAGIC) return 0;
	for (uint32_t* p = __bss_start__; p < __bss_end__; p++) *p = 0;
	hb_shared = sh;
	if (HB_APP_AUX_BLOCK) { hb_os_free(HB_APP_AUX_BLOCK); HB_APP_AUX_BLOCK = 0; }
	pvz_nano_reset();
	/* the first breadcrumb, before the constructors, the arena and GL (a = where the image was loaded) */
	pvz_step_reset();
	s_attached = 0;
	n7p_step_a("entry: image loaded and relocated, bss zeroed", (uint32_t)(uintptr_t)&payload_entry);
	return (void*)frame_callback;
}
