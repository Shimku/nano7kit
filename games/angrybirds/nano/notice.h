/* notice.h: the "cannot start" screen (nano7kit, MIT) */
#ifndef FN_NOTICE_H
#define FN_NOTICE_H
#include <stdint.h>
void notice_render(uint16_t *px, const char *const *lines, int n);   /* 128x64 RGBA4444 */
void notice_draw(int w, int h, const char *const *lines, int n);
void notice_reset(void);
#endif
