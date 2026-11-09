/* ab_crumb.c: start-up breadcrumbs (nano7kit, MIT). Thumb, no floating point and no
 * printf (newlib's printf saves VFP registers, which traps before payload_entry
 * attaches the FPU). Lines go through plat_log: the whole log lives in a static
 * buffer, log.txt is rewritten from that buffer, then sync.txt is written after
 * it (PvZ: the file written last before a reboot can come back torn, so sync.txt
 * is the one that may be lost). Also forwarded to hb_trace_log for
 * ./start apps lasttrace. */
#include <stdint.h>
#include "hb_sdk.h"
#include "hb_heap.h"
#include "ab_crumb.h"
extern void plat_log_line(const char *line);
extern void plat_log_flush(void);

static uint32_t t0, step, minsp;

static char *put_s(char *o, char *end, const char *s)
{
    while (*s && o < end) *o++ = *s++;
    return o;
}
static char *put_u(char *o, char *end, uint32_t v)
{
    char t[11];
    int n = 0;
    do { t[n++] = (char)('0' + v % 10u); v /= 10u; } while (v && n < 10);
    while (n && o < end) *o++ = t[--n];
    return o;
}
static char *put_x(char *o, char *end, uint32_t v)
{
    static const char hex[] = "0123456789abcdef";
    int i;
    for (i = 28; i >= 0 && o < end; i -= 4) *o++ = hex[(v >> i) & 15];
    return o;
}
void ab_crumb_reset(void)
{
    step = 0;
    minsp = 0;
    t0 = hb_time_uptime_us();
}
void ab_crumb(const char *what, uint32_t a)
{
    char line[192], *o = line, *end = line + sizeof line - 1;
    uint32_t sp;
    __asm__ volatile("mov %0, sp" : "=r"(sp));
    if (!minsp || sp < minsp) minsp = sp;
    if (!t0) t0 = hb_time_uptime_us();
    o = put_s(o, end, "nano7kit step ");
    o = put_u(o, end, ++step);
    o = put_s(o, end, " ");
    o = put_s(o, end, what);
    o = put_s(o, end, ": a=");
    o = put_x(o, end, a);
    o = put_s(o, end, " t=");
    o = put_u(o, end, (hb_time_uptime_us() - t0) / 1000u);
    o = put_s(o, end, " sp=");
    o = put_x(o, end, sp);
    o = put_s(o, end, " minsp=");
    o = put_x(o, end, minsp);
    o = put_s(o, end, " free=");
    o = put_u(o, end, hb_os_heap_free());
    o = put_s(o, end, " largest=");
    o = put_u(o, end, hb_os_heap_largest());
    *o = 0;
    plat_log_line(line);
    plat_log_flush();
    hb_trace_log("ABCRUMB ", step, a);
}
