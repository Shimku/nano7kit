/* SPDX-FileCopyrightText: 2026 Nikita Shimkus */
/* SPDX-License-Identifier: Apache-2.0 */

#include <stdint.h>
#include <string.h>

#include <pbl/kernel/init.h>
#include <pbl/nano_hal.h>
#include <pbl_arch_posix.h>
#include <posix_host.h>

static const struct pbl_nano_ops *s_ops;
static uint32_t s_retained[POSIX_HOST_RETAINED_WORDS];
static uint64_t s_fallback_us;
static uint64_t s_boost_us;
static bool s_reboot;

void pbl_nano_set_ops(const struct pbl_nano_ops *ops) {
  s_ops = ops;
}

const struct pbl_nano_ops *pbl_nano_ops(void) {
  return s_ops;
}

void posix_host_option_register(const struct posix_host_option *option) {
  (void)option;
}

void posix_host_hook_register(const struct posix_host_hook *hook) {
  (void)hook;
}

void posix_host_wake(void) {
}

void posix_host_quit(void) {
}

void posix_host_reboot(void) {
  s_reboot = true;
  /* Return to the host frame; the shim restarts the kernel. */
  pbl_posix_cpu_wait(0);
  for (;;) {
  }
}

uint32_t *posix_host_retained(void) {
  return s_retained;
}

void posix_host_boost_us(uint64_t us) {
  s_boost_us += us;
}

uint64_t posix_host_monotonic_us(void) {
  uint64_t now;
  if (s_ops != nullptr && s_ops->monotonic_us != nullptr) {
    now = s_ops->monotonic_us();
  } else {
    now = ++s_fallback_us;
  }
  return now + s_boost_us;
}

const char *posix_host_exe_dir(void) {
  return "";
}

extern int pbl_app_main(void);

int pbl_nano_kernel_start(void) {
  s_reboot = false;
  pbl_posix_start(pbl_app_main);
  pbl_posix_run();
  return s_reboot ? 0 : 1;
}

void pbl_nano_kernel_run(void) {
  if (s_reboot) {
    return;
  }
  pbl_posix_run();
}

bool pbl_nano_wants_reboot(void) {
  return s_reboot;
}
