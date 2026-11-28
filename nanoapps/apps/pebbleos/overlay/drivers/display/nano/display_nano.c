/* SPDX-FileCopyrightText: 2026 Nikita Shimkus */
/* SPDX-License-Identifier: Apache-2.0 */

#include <errno.h>
#include <string.h>

#include <pbl/drivers/display.h>
#include <pbl/drivers/display/nano.h>
#include <pbl/nano_hal.h>

#ifndef CONFIG_DISPLAY_NANO_WIDTH
#define CONFIG_DISPLAY_NANO_WIDTH 200
#endif
#ifndef CONFIG_DISPLAY_NANO_HEIGHT
#define CONFIG_DISPLAY_NANO_HEIGHT 228
#endif

#define W CONFIG_DISPLAY_NANO_WIDTH
#define H CONFIG_DISPLAY_NANO_HEIGHT

static uint8_t s_argb[W * H];
static uint16_t s_rgb565[W * H];
static bool s_enabled;
static bool s_painted;

static uint16_t prv_to_rgb565(uint8_t c) {
  unsigned r = (c >> 4) & 3U;
  unsigned g = (c >> 2) & 3U;
  unsigned b = c & 3U;
  unsigned r5 = (r * 31U) / 3U;
  unsigned g6 = (g * 63U) / 3U;
  unsigned b5 = (b * 31U) / 3U;
  return (uint16_t)((r5 << 11) | (g6 << 5) | b5);
}

static void prv_flush(void) {
  for (int i = 0; i < W * H; i++) {
    s_rgb565[i] = prv_to_rgb565(s_argb[i]);
  }
}

static int prv_init(const struct pbl_display *disp) {
  (void)disp;
  memset(s_argb, 0, sizeof(s_argb));
  prv_flush();
  s_enabled = true;
  return 0;
}

static int prv_enable(const struct pbl_display *disp) {
  (void)disp;
  s_enabled = true;
  return 0;
}

static int prv_disable(const struct pbl_display *disp) {
  (void)disp;
  s_enabled = false;
  return 0;
}

static int prv_set_rotated(const struct pbl_display *disp, bool rotated) {
  (void)disp;
  (void)rotated;
  return 0;
}

static int prv_update(const struct pbl_display *disp, const struct pbl_display_buffer *buf,
                      uint16_t y0, uint16_t y1) {
  for (uint16_t y = y0; y <= y1; y++) {
    const uint8_t *src = &buf->data[y * buf->pitch];
    uint8_t *dst = &s_argb[y * W];
    if (disp->props.format == PBL_DISPLAY_FORMAT_MONO) {
      for (uint16_t x = 0; x < W; x++) {
        dst[x] = (src[x / 8U] & (1U << (x % 8U))) != 0U ? 0xffU : 0xc0U;
      }
    } else {
      memcpy(dst, src, W);
    }
  }
  if (s_enabled) {
    prv_flush();
  }
  s_painted = true;
  pbl_display_complete(disp, 0);
  return 0;
}

static int prv_clear(const struct pbl_display *disp) {
  memset(s_argb, 0, sizeof(s_argb));
  if (s_enabled) {
    prv_flush();
  }
  return 0;
}

static const struct pbl_display_ops s_ops = {
  .init = prv_init,
  .enable = prv_enable,
  .disable = prv_disable,
  .set_rotated = prv_set_rotated,
  .update = prv_update,
  .clear = prv_clear,
};

PBL_DISPLAY_STATE_DEFINE(pbl_display_nano);
const struct pbl_display pbl_display_nano =
    PBL_DISPLAY_INIT(pbl_display_nano, "display", nullptr, &s_ops, W, H,
                     PBL_DISPLAY_FORMAT_ARGB2222, false);
PBL_DEVICE_REGISTER(pbl_display_nano, &pbl_display_nano.dev);

const uint16_t *pbl_nano_fb_rgb565(void) {
  return s_rgb565;
}

bool pbl_nano_display_painted(void) {
  return s_painted;
}
