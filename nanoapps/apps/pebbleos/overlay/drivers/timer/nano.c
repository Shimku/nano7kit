/* SPDX-FileCopyrightText: 2026 Nikita Shimkus */
/* SPDX-License-Identifier: Apache-2.0 */

#include <stdint.h>

#include <pbl/drivers/timer.h>
#include <pbl_arch_posix.h>
#include <posix_host.h>

#define HOST_HZ 1000000U

static uint64_t s_deadline_us = UINT64_MAX;

static int prv_cs_init(const struct pbl_device *dev);
static int prv_ce_init(const struct pbl_device *dev);

static uint32_t prv_read(const struct pbl_clocksource *cs) {
  (void)cs;
  return (uint32_t)posix_host_monotonic_us();
}

static const struct pbl_clocksource_ops s_cs_ops = {
  .read = prv_read,
};

PBL_DEVICE_STATE_DEFINE(s_host_cs);
static const struct pbl_clocksource s_host_cs =
    PBL_CLOCKSOURCE_INIT(s_host_cs, "host", prv_cs_init, nullptr, &s_cs_ops, 32);

const struct pbl_clocksource *const pbl_system_clocksource = &s_host_cs;

static void prv_set_next(const struct pbl_clockevent *ce, uint32_t delta) {
  (void)ce;
  s_deadline_us = posix_host_monotonic_us() + delta;
}

static void prv_shutdown(const struct pbl_clockevent *ce) {
  (void)ce;
  s_deadline_us = UINT64_MAX;
}

static const struct pbl_clockevent_ops s_ce_ops = {
  .set_next = prv_set_next,
  .shutdown = prv_shutdown,
};

PBL_DEVICE_STATE_DEFINE(s_host_ce);
static const struct pbl_clockevent s_host_ce =
    PBL_CLOCKEVENT_INIT(s_host_ce, "host", prv_ce_init, nullptr, &s_ce_ops, 1U, UINT32_MAX);

const struct pbl_clockevent *const pbl_system_clockevent = &s_host_ce;

static void prv_isr(void *arg) {
  (void)arg;
  pbl_clockevent_handler(&s_host_ce);
}

void pbl_nano_timer_poll(void) {
  if (s_deadline_us <= posix_host_monotonic_us()) {
    s_deadline_us = UINT64_MAX;
    pbl_posix_irq_run(prv_isr, nullptr);
  }
}

static int prv_cs_init(const struct pbl_device *dev) {
  (void)dev;
  pbl_clocksource_set_hz(&s_host_cs, HOST_HZ);
  return 0;
}

static int prv_ce_init(const struct pbl_device *dev) {
  (void)dev;
  pbl_clockevent_set_hz(&s_host_ce, HOST_HZ);
  return 0;
}
