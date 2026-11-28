/* SPDX-FileCopyrightText: 2026 nano7kit contributors */
/* SPDX-License-Identifier: Apache-2.0 */

#include <stdint.h>
#include <time.h>

#include <pbl/drivers/rtc.h>
#include <pbl/drivers/rtc/nano.h>
#include <pbl/nano_hal.h>
#include <posix_host.h>

static int64_t s_offset_s;

void RTC_WriteBackupRegister(uint32_t reg_id, uint32_t value) {
  if (reg_id < POSIX_HOST_RETAINED_WORDS) {
    posix_host_retained()[reg_id] = value;
  }
}

uint32_t RTC_ReadBackupRegister(uint32_t reg_id) {
  return reg_id < POSIX_HOST_RETAINED_WORDS ? posix_host_retained()[reg_id] : 0;
}

static int prv_get_time(const struct pbl_rtc *rtc, time_t *t) {
  const struct pbl_nano_ops *ops = pbl_nano_ops();
  int64_t now = 0;
  (void)rtc;
  if (ops != nullptr && ops->unix_time != nullptr) {
    now = ops->unix_time();
  }
  *t = (time_t)(now + s_offset_s);
  return 0;
}

static int prv_set_time(const struct pbl_rtc *rtc, time_t t) {
  const struct pbl_nano_ops *ops = pbl_nano_ops();
  int64_t now = 0;
  (void)rtc;
  if (ops != nullptr && ops->unix_time != nullptr) {
    now = ops->unix_time();
  }
  s_offset_s = (int64_t)t - now;
  return 0;
}

static const struct pbl_rtc_ops s_ops = {
  .get_time = prv_get_time,
  .set_time = prv_set_time,
};

PBL_DEVICE_STATE_DEFINE(pbl_rtc_nano);
const struct pbl_rtc pbl_rtc_nano = PBL_RTC_INIT(pbl_rtc_nano, "rtc", nullptr, nullptr, &s_ops);
PBL_DEVICE_REGISTER(pbl_rtc_nano, &pbl_rtc_nano.dev);
