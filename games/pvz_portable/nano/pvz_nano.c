/* pvz_nano.c: the nano frame loop for PvZ (PvZ-Portable engine, LGPL-3.0-or-later, wszqkzqk; this file nano7kit,
 * LGPL-3.0-or-later). ARM state. The first frame (GL context current) checks memory, takes the arena block and runs the
 * engine's start-up (resources load in that frame); then each frame feeds the touch and runs one engine step.
 * The 800x600 game is drawn at 0.4 into 320x240 of the 240x432 panel, turned so the iPod is held sideways with the
 * Home button on the right; the memory readout sits in the strip on the left (nohud.txt in Data/PvZ hides it).
 * "nano7kit step" breadcrumbs in log.txt (written at once) before every start-up stage; hello.txt or hello2.txt
 * in Data/PvZ stop it before the arena or before the engine. */
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "hb_sdk.h"
#include "hb_heap.h"
#include "hb_surface_input.h"
#include "notice.h"
#include "gl_left.h"

#define DATA_DIR    "/Apps/Data/PvZ"
#define BLOCK_MAX   0x340000u   /* 3.25 MB arena: the dry run needs 3200 KB for 1-1..1-10 chained, + margin */
#define BLOCK_MIN   0x300000u   /* 3.0 MB: the dry run's smallest arena for 1-1 alone (3072 KB) */
#define AFTER_MIN   0x150000u   /* free after the block: 150 triangles x 3000 + 256 KB + room (64 KB less than before; the
                                   device logs show the GL textures do not come out of this heap) */
#define SLOP        0x10000u
extern int  n7_init(void* arena, unsigned arena_size);
extern int  n7_frame(void);
extern void n7_touch(int down, int x, int y);
extern void n7p_log(const char*);
extern void n7p_log_flush(void);
extern void n7p_time_reset(void);
extern void n7p_step(const char* what);
extern void n7p_step_a(const char* what, uint32_t a);
extern int n7_panel_w, n7_panel_h;

static int state;   /* 0 start, 1 running, 2 stopped, 3 short of memory */
static char note[2][40];
static const char* note_lines[7];
static int note_n;
static uint32_t frames;

static void logf_(const char* fmt, ...) __attribute__((format(printf, 1, 2)));
static void logf_(const char* fmt, ...) { char b[240]; va_list ap; va_start(ap, fmt); vsnprintf(b, sizeof b, fmt, ap); va_end(ap); n7p_log(b); }
static void mb(char* o, uint32_t n) { snprintf(o, 12, "%u.%uM", (unsigned)(n >> 20), (unsigned)(((n & 0xFFFFFu) * 10u) >> 20)); }

/* touch: the OS touch list (right at once) with the resident's mailbox as fallback, as Fruit Ninja */
#define OS_TOUCH_HEAD ((volatile uint32_t*)0x089a5298u)
static int list_touch(int16_t* x, int16_t* y)
{
	volatile uint32_t* head = OS_TOUCH_HEAD;
	if (head[5] == 0) return 0;
	uint32_t end = head[4], nodes = 0; int down = 0;
	if (end < 0x08000000u || end >= 0x10000000u) return 0;
	for (uint32_t node = *(volatile uint32_t*)end; node != end && nodes < 8; node = *(volatile uint32_t*)node, nodes++) {
		if (node < 0x08000000u || node >= 0x10000000u) return 0;
		volatile int32_t* value = (volatile int32_t*)(node + 8);
		if (*((volatile uint8_t*)(node + 8) + 4) == 1) continue;   /* lifted */
		*x = (int16_t)value[2]; *y = (int16_t)value[3]; down = 1;
	}
	return down ? 1 : nodes ? 2 : 0;
}
static void poll_touch(void)
{
	static int was; static int16_t lx, ly;
	hb_spoint_t f; hb_surface_touch_read(&f);
	int16_t x = 0, y = 0; int l = list_touch(&x, &y), down;
	if (l == 1) down = 1; else if (l == 2) down = 0; else { down = f.down; x = f.x; y = f.y; }
	if (!down) { x = lx; y = ly; }
	if (down == was && (!down || (x == lx && y == ly))) return;
	was = down; lx = x; ly = y;
	n7_touch(down, x, y);
}

static int start(int w, int h)
{
	n7_panel_w = w; n7_panel_h = h;
	n7p_step_a("start: GL context current, panel w<<16|h", (uint32_t)w << 16 | (uint32_t)h);
	uint32_t free_a = hb_os_heap_free(), largest_a = hb_os_heap_largest();
	gll_init(DATA_DIR "/gl_names.txt");
	n7p_step("old texture names read (gl_names.txt)");
	uint32_t swept = gll_sweep();
	n7p_step_a("old texture names deleted", swept);
	uint32_t free0 = hb_os_heap_free(), largest = hb_os_heap_largest();
	logf_("nano7kit PvZ m2: panel %dx%d, heap free %u largest %u; %u old texture names deleted, then free %u largest %u",
	      w, h, (unsigned)free_a, (unsigned)largest_a, (unsigned)swept, (unsigned)free0, (unsigned)largest);
	/* as big as BLOCK_MAX, but never so big that less than AFTER_MIN stays free for textures and triangles */
	uint32_t size = largest > BLOCK_MAX + SLOP ? BLOCK_MAX : largest > SLOP ? (largest - SLOP) & ~0xFFFFu : 0;
	if (free0 > AFTER_MIN && size > ((free0 - AFTER_MIN) & ~0xFFFFu)) size = (free0 - AFTER_MIN) & ~0xFFFFu;
	if (free0 <= AFTER_MIN) size = 0;
	if (largest < BLOCK_MIN + SLOP || size < BLOCK_MIN) {
		char a[12], b[12], c[12], d[12];
		mb(a, free0); mb(b, largest); mb(c, BLOCK_MIN + AFTER_MIN); mb(d, BLOCK_MIN + SLOP);
		snprintf(note[0], sizeof note[0], "FREE %s BIG %s", a, b);
		snprintf(note[1], sizeof note[1], "NEEDS %s BIG %s", c, d);
		static const char* const head[] = { "PVZ", "NOT ENOUGH MEMORY", "" };
		note_n = 0;
		for (int i = 0; i < 3; i++) note_lines[note_n++] = head[i];
		note_lines[note_n++] = note[0]; note_lines[note_n++] = note[1];
		note_lines[note_n++] = "PAUSE MUSIC, QUIT,"; note_lines[note_n++] = "THEN OPEN IT AGAIN";
		logf_("nano7kit not enough memory: heap free %u largest %u, needs %u and %u; not starting",
		      (unsigned)free0, (unsigned)largest, (unsigned)(BLOCK_MIN + AFTER_MIN), (unsigned)(BLOCK_MIN + SLOP));
		return -3;
	}
	/* hello test: with hello.txt in Data/PvZ it stops here (loader, entry, FPU, log and GL checked) */
	if (hb_fs_exists(DATA_DIR "/hello.txt")) {
		static const char* const hello[] = { "PVZ HELLO", "", "LOADER, ENTRY, FPU,", "LOG AND GL OK", "", "SEE LOG.TXT" };
		note_n = 0; for (int i = 0; i < 6; i++) note_lines[note_n++] = hello[i];
		n7p_step("hello.txt: stopping before the arena");
		return -3;
	}
	n7p_step_a("pre-flight passed, taking the arena (a = bytes)", size);
	void* block = hb_os_alloc(size);
	if (!block) { logf_("nano7kit arena: %u bytes refused", (unsigned)size); return -1; }
	HB_APP_AUX_BLOCK = block;   /* the resident frees it when the app unloads */
	logf_("nano7kit arena %u bytes at %p; heap free after %u", (unsigned)size, block, (unsigned)hb_os_heap_free());
	n7p_log_flush();
	/* C++ static constructors (the image's .init_array; the loader does not run them) */
	extern void (*__init_array_start[])(void), (*__init_array_end[])(void);
	extern int n7_arena_init(void*, unsigned);
	n7p_step_a("arena taken (a = address)", (uint32_t)(uintptr_t)block);
	if (n7_arena_init(block, size)) { logf_("nano7kit arena init failed"); return -1; }
	n7p_step_a("arena ready; C++ constructors next (a = count)", (uint32_t)(__init_array_end - __init_array_start));
	for (void (**f)(void) = __init_array_start; f < __init_array_end; f++) {
		/* a = the constructor's address: it must lie inside the image (a base-0 value here was an earlier bug) */
		n7p_step_a("constructor", (uint32_t)(uintptr_t)*f);
		(*f)();
	}
	n7p_step("constructors done");
	if (hb_fs_exists(DATA_DIR "/hello2.txt")) {
		static const char* const hello[] = { "PVZ HELLO 2", "", "ARENA AND C++", "CONSTRUCTORS OK", "", "SEE LOG.TXT" };
		note_n = 0; for (int i = 0; i < 6; i++) note_lines[note_n++] = hello[i];
		n7p_step("hello2.txt: stopping before the engine");
		return -3;
	}
	if (n7_init(0, 0)) { n7p_step("engine start-up FAILED (see the lines above)"); return -1; }   /* arena is already set up */
	n7p_step("engine started");
	logf_("nano7kit PvZ running: heap free %u largest %u", (unsigned)hb_os_heap_free(), (unsigned)hb_os_heap_largest());
	n7p_log_flush();
	gll_save();
	return 0;
}
/* a fatal in the engine (out of arena memory, abort) unwinds to the frame with pvz_longjmp (ARM; newlib's setjmp
 * would pull in Thumb VFP code) and the game stops; Home quits the app as usual, and the resident frees the arena */
typedef uint32_t pvz_jmp_buf[26] __attribute__((aligned(8)));
extern int pvz_setjmp(pvz_jmp_buf b) __attribute__((returns_twice));
extern void pvz_longjmp(pvz_jmp_buf b, int v) __attribute__((noreturn));
static pvz_jmp_buf unwind;
static volatile int in_engine;
void n7_fatal(const char* why)
{
	char b[200]; snprintf(b, sizeof b, "nano7kit PvZ fatal: %s", why);
	n7p_log(b); n7p_step("fatal (line above)");
	if (in_engine) pvz_longjmp(unwind, 1);
	for (;;) {}
}

void pvz_nano_frame(int w, int h, uint32_t n)
{
	(void)n;
	if (state == 3) { notice_draw(w, h, note_lines, note_n); gll_save(); return; }
	if (state == 2) {
		static const char* const stopped[] = { "PVZ", "STOPPED", "", "SEE LOG.TXT IN", "DATA/PVZ" };
		notice_draw(w, h, stopped, 5); gll_save(); return;
	}
	if (pvz_setjmp(unwind)) { in_engine = 0; state = 2; n7p_log_flush(); gll_save(); return; }
	in_engine = 1;
	if (state == 0) {
		int r = start(w, h);
		if (r) { in_engine = 0; state = r == -3 ? 3 : 2; n7p_log_flush(); gll_save(); return; }
		state = 1;
	}
	poll_touch();
	if (frames < 3) n7p_step_a("engine frame", frames);
	if (n7_frame()) { state = 2; n7p_log_flush(); }
	if (frames < 3) n7p_step_a("engine frame done", frames);
	in_engine = 0;
	if (++frames % 60 == 0) gll_save();   /* the texture-name range every ~4 s (written only if it changed), so a
	                                         Home quit leaves nothing the next start cannot delete */
}
void pvz_nano_reset(void) { in_engine = 0; state = 0; frames = 0; note_n = 0; n7p_time_reset(); notice_reset(); }
