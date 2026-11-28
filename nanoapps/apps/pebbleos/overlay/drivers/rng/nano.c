/* SPDX-FileCopyrightText: 2026 Nikita Shimkus */
/* SPDX-License-Identifier: Apache-2.0 */

#include <stdint.h>

#include <posix_host.h>

int rng_rand(uint32_t *rand_out) {
  static uint32_t s;
  if (s == 0) {
    s = (uint32_t)posix_host_monotonic_us() ^ 0xC0FFEEu;
    if (s == 0) {
      s = 1;
    }
  }
  s = s * 1664525U + 1013904223U;
  *rand_out = s;
  return 0;
}
