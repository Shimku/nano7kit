/* pvz_crumb.c: start-up breadcrumbs for PvZ Portable (nano7kit, LGPL-3.0-or-later). Thumb, no floating point and no
 * printf (newlib's printf saves VFP registers, which traps before the frame callback attaches the FPU), so a step
 * can be logged from payload_entry, before the C++ constructors, the arena or GL. Every step is appended to the
 * engine's log and log.txt is rewritten whole, then sync.txt is written after it: on this iPod the newest file
 * written just before a reboot can come back empty, so sync.txt is the one that may be lost, not log.txt.
 * Line: "nano7kit step N what: a=<hex> t=<ms since entry> sp=<hex> minsp=<lowest sp seen> fs=<OS file calls so far>
 * free=<OS heap free> largest=<largest block>". minsp is the lowest sp seen at a crumb or a file call. */
#include <stdint.h>
#include "hb_sdk.h"
#include "hb_heap.h"
extern void n7p_log(const char* line);
extern void n7p_log_sync(unsigned step);
extern uint32_t n7p_fs_calls(void);
extern uint32_t n7p_min_sp(void);
static unsigned s_step;
static uint32_t s_t0;
static char* put_s(char* o, char* end, const char* s) { while (*s && o < end) *o++ = *s++; return o; }
static char* put_u(char* o, char* end, uint32_t v)
{
	char t[11]; int n = 0;
	do { t[n++] = (char)('0' + v % 10u); v /= 10u; } while (v && n < 10);
	while (n && o < end) *o++ = t[--n];
	return o;
}
static char* put_x(char* o, char* end, uint32_t v)
{
	static const char hex[] = "0123456789abcdef";
	for (int i = 28; i >= 0 && o < end; i -= 4) *o++ = hex[(v >> i) & 15u];
	return o;
}
void pvz_step_reset(void) { s_step = 0; s_t0 = hb_time_uptime_us(); }
void n7p_step_a(const char* what, uint32_t a)
{
	char line[200], *o = line, *end = line + sizeof line - 1;
	uint32_t sp;
	__asm__ volatile("mov %0, sp" : "=r"(sp));
	o = put_s(o, end, "nano7kit step "); o = put_u(o, end, ++s_step); o = put_s(o, end, " ");
	o = put_s(o, end, what); o = put_s(o, end, ": a=");
	o = put_x(o, end, a); o = put_s(o, end, " t=");
	o = put_u(o, end, (hb_time_uptime_us() - s_t0) / 1000u); o = put_s(o, end, " sp=");
	o = put_x(o, end, sp); o = put_s(o, end, " minsp=");
	{ uint32_t m = n7p_min_sp(); o = put_x(o, end, m && m < sp ? m : sp); }
	o = put_s(o, end, " fs="); o = put_u(o, end, n7p_fs_calls());
	o = put_s(o, end, " free=");
	o = put_u(o, end, hb_os_heap_free()); o = put_s(o, end, " largest=");
	o = put_u(o, end, hb_os_heap_largest());
	*o = 0;
	n7p_log(line);
	n7p_log_sync(s_step);
}
void n7p_step(const char* what) { n7p_step_a(what, 0); }
