#ifndef GTA6_NOTICE_H
#define GTA6_NOTICE_H

#include <stdint.h>

void g6_notice_draw(uint32_t *fb, int w, int h, const char *const *lines, int n);
void g6_text5(uint32_t *fb, int w, int h, int x, int y, const char *s, int scale, uint32_t fg);
void g6_text5n(uint32_t *fb, int w, int h, int x, int y, const char *s, int scale, uint32_t fg, int advance);

#endif
