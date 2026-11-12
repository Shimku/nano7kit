/* notice.h: the "cannot start" screen (nano7kit, MIT) */
#ifndef FN_NOTICE_H
#define FN_NOTICE_H
#include <stdint.h>
void notice_render(uint16_t *px, const char *const *lines, int n);   /* 128x64 RGBA4444 */
void notice_draw(int w, int h, const char *const *lines, int n);
void notice_reset(void);
/* Host software blit: dark red panel + centred 1.75x text, no allocation. */
void notice_fill_rgb(uint8_t *fb, int w, int h, int bpp, int stride, const char *const *lines, int n);
#endif
