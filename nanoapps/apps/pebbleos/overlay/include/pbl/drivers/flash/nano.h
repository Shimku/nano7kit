/* SPDX-FileCopyrightText: 2026 Nikita Shimkus */
/* SPDX-License-Identifier: Apache-2.0 */

#pragma once

#include <pbl/drivers/flash.h>

extern const struct pbl_flash pbl_flash_nano;

#define NANO_FLASH (&pbl_flash_nano)
