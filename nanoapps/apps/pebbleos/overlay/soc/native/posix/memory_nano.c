/* SPDX-FileCopyrightText: 2026 nano7kit contributors */
/* SPDX-License-Identifier: Apache-2.0 */

#include <stddef.h>
#include <stdint.h>

#include <pbl/nano_hal.h>
#include <pbl/util/build_id.h>

#define PRV_STR(x)  #x
#define PRV_XSTR(x) PRV_STR(x)
#define PRV_SYM(s)  PRV_XSTR(__USER_LABEL_PREFIX__) #s

/* The kernel finds the heap through _heap_start and _heap_end. On this
 * hosted board the shim allocates the buffer from the NanoApps OS heap
 * so the .hbapp span stays small. */

char *_heap_start;
char *_heap_end;

/* thread.c and others also want the symbol form used on a linked image. */
__asm__(".globl " PRV_SYM(_heap_start) "\n");

void pbl_nano_memory_init(void *base, size_t size) {
  _heap_start = (char *)base;
  _heap_end = (char *)base + size;
}

uint32_t __isr_stack_start__[CONFIG_KERNEL_ISR_STACK_SIZE / sizeof(uint32_t)];

const struct {
  uint32_t name_length;
  uint32_t data_length;
  uint32_t type;
  uint8_t data[4 + BUILD_ID_EXPECTED_LEN];
} TINTIN_BUILD_ID = {
  .name_length = 4,
  .data_length = BUILD_ID_EXPECTED_LEN,
  .type = 3,
  .data = {'G', 'N', 'U', '\0'},
};

static_assert(offsetof(typeof(TINTIN_BUILD_ID), data) == offsetof(ElfExternalNote, data), "");
