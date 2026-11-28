/* SPDX-FileCopyrightText: 2026 Nikita Shimkus */
/* SPDX-License-Identifier: MIT */

/* Weak stand-ins: the real implementations will come from the fetched
 * PebbleOS tree once the nano 7G hosted board is linked.
 */
#include "pebbleos_host.h"

int pebbleos_host_available(void) { return 0; }
int pebbleos_host_start(void) { return 0; }
const uint16_t *pebbleos_host_fb(void) { return 0; }
void pebbleos_host_button(int pebble_key, int down) { (void)pebble_key; (void)down; }
void pebbleos_host_tick(void) {}
