/* SPDX-FileCopyrightText: 2026 nano7kit contributors */
/* SPDX-License-Identifier: Apache-2.0 */

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Callbacks the NanoApps shim registers before starting the kernel. */

struct pbl_nano_ops {
  uint64_t (*monotonic_us)(void);
  int64_t (*unix_time)(void);
  /* Flash: missing regions read as 0xFF. Return 0 on success. */
  int (*flash_read)(uint32_t addr, void *buf, uint32_t len);
  int (*flash_write)(uint32_t addr, const void *buf, uint32_t len);
  int (*flash_erase)(uint32_t addr, uint32_t len);
};

void pbl_nano_set_ops(const struct pbl_nano_ops *ops);
const struct pbl_nano_ops *pbl_nano_ops(void);

/* Heap must be set before pbl_nano_kernel_start(). */
void pbl_nano_memory_init(void *base, size_t size);

int pbl_nano_kernel_start(void);
void pbl_nano_kernel_run(void);
/* True once the kernel asked for a reboot; the host stops running it. */
bool pbl_nano_wants_reboot(void);
void pbl_nano_timer_poll(void);
void pbl_nano_button(int key, int down);
const uint16_t *pbl_nano_fb_rgb565(void);
bool pbl_nano_display_painted(void);

enum {
  PBL_NANO_KEY_BACK = 0,
  PBL_NANO_KEY_UP = 1,
  PBL_NANO_KEY_SELECT = 2,
  PBL_NANO_KEY_DOWN = 3
};

#define PBL_NANO_FLASH_SIZE 0x400000u
#define PBL_NANO_HEAP_SIZE  (384u * 1024u)
