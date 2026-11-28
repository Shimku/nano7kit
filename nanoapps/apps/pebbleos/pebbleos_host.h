/* SPDX-FileCopyrightText: 2026 nano7kit contributors */
/* SPDX-License-Identifier: MIT */

/* Hook for the hosted PebbleOS kernel. When the fetched tree has been built
 * into a library, this starts it and returns 1. The shim then blits the
 * 200x228 Pebble framebuffer into the centred panel and feeds button events.
 * Returns 0 if the library is not linked yet.
 */
#ifndef PEBBLEOS_HOST_H
#define PEBBLEOS_HOST_H

#include <stdint.h>

#define PBL_PANEL_W 200
#define PBL_PANEL_H 228

int pebbleos_host_available(void);
/* 1 when /Apps/Data/PebbleOS holds a complete system resource pack. */
int pebbleos_host_resources_ok(void);
/* 1 started, 0 not enough memory, -1 the kernel stopped while booting. */
int pebbleos_host_start(void);
/* 1 when the kernel asked to reboot or reset and has stopped. */
int pebbleos_host_stopped(void);
/* RGB565 Pebble framebuffer, or NULL if the host is not running. */
const uint16_t *pebbleos_host_fb(void);
void pebbleos_host_button(int pebble_key, int down);
void pebbleos_host_tick(void);

enum {
    PBL_KEY_BACK = 0,
    PBL_KEY_UP = 1,
    PBL_KEY_SELECT = 2,
    PBL_KEY_DOWN = 3
};

#endif
