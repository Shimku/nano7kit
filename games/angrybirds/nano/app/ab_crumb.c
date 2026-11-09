/* ab_crumb.c: start-up breadcrumbs (nano7kit, MIT). Thumb, no floating point and no
 * printf (newlib's printf saves VFP registers, which traps before payload_entry
 * attaches the FPU). Every step is written to log.txt, then sync.txt after it, so a
 * reboot right after a step loses sync.txt rather than log.txt. Also forwarded to
 * hb_trace_log for ./start apps lasttrace. */
#include <stdint.h>
#include "hb_sdk.h"
#include "hb_heap.h"
#include "ab_crumb.h"

#define DATA_DIR "/Apps/Data/AngryBirds"
#define CAP 4096u
static char store[CAP] __attribute__((aligned(8)));
static char sync_line[192] __attribute__((aligned(8)));
static uint32_t len, t0, step;

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
    len = 0;
    step = 0;
    t0 = hb_time_uptime_us();
}
void ab_crumb(const char *what, uint32_t a)
{
    char line[192], *o = line, *end = line + sizeof line - 1;
    uint32_t sp, n;
    __asm__ volatile("mov %0, sp" : "=r"(sp));
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
    o = put_s(o, end, " free=");
    o = put_u(o, end, hb_os_heap_free());
    o = put_s(o, end, " largest=");
    o = put_u(o, end, hb_os_heap_largest());
    *o = 0;
    n = (uint32_t)(o - line);
    if (len + n + 1 > CAP) return;
    {
        uint32_t i;
        for (i = 0; i < n; i++) store[len++] = line[i];
        store[len++] = '\n';
        hb_fs_mkdir("/Apps/Data");
        hb_fs_mkdir(DATA_DIR);
        hb_fs_write(DATA_DIR "/log.txt", store, len);
        for (i = 0; i < n && i < 190; i++) sync_line[i] = line[i];
        sync_line[i] = '\n';
        hb_fs_write(DATA_DIR "/sync.txt", sync_line, i + 1);
    }
    hb_trace_log("ABCRUMB ", step, a);
}
