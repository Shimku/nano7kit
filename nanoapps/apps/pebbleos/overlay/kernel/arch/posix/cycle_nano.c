/* SPDX-FileCopyrightText: 2026 Nikita Shimkus */
/* SPDX-License-Identifier: Apache-2.0 */

#include <stdint.h>

#include <pbl_arch_cycle.h>
#include <posix_host.h>

uint32_t arch_cycle_get_32(void) {
  return (uint32_t)posix_host_monotonic_us();
}
