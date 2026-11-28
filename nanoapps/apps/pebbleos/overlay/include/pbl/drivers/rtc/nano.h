/* SPDX-FileCopyrightText: 2026 Nikita Shimkus */
/* SPDX-License-Identifier: Apache-2.0 */

#pragma once

#include <pbl/drivers/rtc.h>

extern const struct pbl_rtc pbl_rtc_nano;

#define NANO_RTC (&pbl_rtc_nano)
