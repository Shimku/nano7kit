/* nano7 device build: the desktop heap tracker's interface as stubs (the arena in n7alloc.c is the real count). */
#include "../nano7/memtrack.h"
int mt_phase, mt_atpeak_phase;
void mt_reset_peak(void) {}
size_t mt_live(int t) { (void)t; return 0; }
size_t mt_live_nonpixel(void) { return 0; }
size_t mt_peak(void) { return 0; }
size_t mt_peak_nonpixel(void) { return 0; }
size_t mt_peak_tag(int t) { (void)t; return 0; }
size_t mt_model_live(void) { return 0; }
size_t mt_peak_model(void) { return 0; }
size_t mt_count(void) { return 0; }
size_t mt_nano_live(void) { return 0; }
size_t mt_peak_nano(void) { return 0; }
size_t mt_atpeak_tag(int t) { (void)t; return 0; }
size_t mt_atpeak_last(void) { return 0; }
int mt_atpeak_lasttag(void) { return 0; }
size_t mt_biggest_nonpixel(void) { return 0; }
size_t mt_biggest_pixel(void) { return 0; }
const char* mt_tag_name(int t) { (void)t; return "-"; }
void mt_dump_big(const char* p, size_t m) { (void)p; (void)m; }
