/* notice.h: NOT ENOUGH MEMORY screen. Drawn with no allocation. */
#ifndef GD_NOTICE_H
#define GD_NOTICE_H
#include <stdint.h>
void notice_draw_fb(uint32_t *fb, int w, int h, const char *const *lines, int n);
#endif
