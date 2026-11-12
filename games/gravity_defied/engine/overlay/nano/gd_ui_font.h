#pragma once
#include <stdint.h>

/* Small Inter (SIL OFL-1.1) bitmap, ASCII 32-126, rasterized at build.
 * Not the Inter TTF; see CREDITS.md and LICENSES/Inter-OFL-1.1.txt. */

typedef struct {
    uint8_t w, h, adv;
    int8_t xoff, yoff;
    uint16_t off;
} GdUiGlyph;

typedef struct {
    int px, ascent, height;
    const uint8_t *bits;
    const GdUiGlyph *g;
} GdUiFont;

#ifdef __cplusplus
extern "C" {
#endif
const GdUiFont *gd_ui_font(int px);
int gd_ui_text_width(int px, const char *s, int n);
int gd_ui_char_width(int px, int c);
int gd_ui_font_height(int px);
void gd_ui_draw_text(uint32_t *fb, int fb_w, int fb_h,
                     int x, int y, const char *s, int n, int px,
                     uint32_t color, int cx, int cy, int cw, int ch);
#ifdef __cplusplus
}
#endif
