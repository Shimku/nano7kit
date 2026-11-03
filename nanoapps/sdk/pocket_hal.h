/*
 * pocket_hal.h -- first-party NanoApps HAL that PocketJS AOT would call.
 *
 * PocketJS `pocket compile --target ipodnano --aot` emits Rust for
 * apps/ipodnano-hero at 176x132 with input.buttons (iPod nano 2G /
 * Rockbox). This header is the 7G contract instead:
 *
 *   - 240 x 432 XRGB8888 (Helvetica silver_resident RAW_SURFACE)
 *   - single-touch (hb_spoint_t), not a click wheel
 *   - no QuickJS guest, no Rust core, no DOM
 *
 * A later MicroTS AOT build on a Mac would `extern "C"` these symbols and
 * link them into a RAW_SURFACE .hbapp. Tonight's JS Hello implements them
 * in C so the path is real and size-bounded (slot A).
 *
 * Not PocketJS. Not vendored. Not flashed.
 */
#ifndef NANO7_POCKET_HAL_H
#define NANO7_POCKET_HAL_H

#include <stdint.h>

#define POCKET_HAL_W 240
#define POCKET_HAL_HEIGHT 432

void pocket_hal_bind(uint32_t *fb, int w, int h);
int pocket_hal_w(void);
int pocket_hal_h(void);
void pocket_hal_fill(uint32_t rgb);
void pocket_hal_fill_rect(int x, int y, int w, int h, uint32_t rgb);
void pocket_hal_text(int x, int y, const char *s, int scale, uint32_t rgb);

#endif /* NANO7_POCKET_HAL_H */
