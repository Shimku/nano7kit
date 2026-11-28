/* SPDX-FileCopyrightText: 2026 nano7kit contributors */
/* SPDX-License-Identifier: Apache-2.0 */

#include <errno.h>
#include <string.h>

#include <pbl/board/partitions.h>
#include <pbl/drivers/flash.h>
#include <pbl/drivers/flash/nano.h>
#include <pbl/nano_hal.h>

#ifndef PAGE_SIZE_BYTES
#define PAGE_SIZE_BYTES 256U
#endif
#ifndef SECTOR_SIZE_BYTES
#define SECTOR_SIZE_BYTES 65536U
#endif
#ifndef SUBSECTOR_SIZE_BYTES
#define SUBSECTOR_SIZE_BYTES 4096U
#endif

static const struct pbl_flash_geometry s_geometry = {
  .size = PBL_NANO_FLASH_SIZE,
  .page_size = PAGE_SIZE_BYTES,
  .sector_size = SECTOR_SIZE_BYTES,
  .subsector_size = SUBSECTOR_SIZE_BYTES,
  .sector_erase_ms = 1U,
  .subsector_erase_ms = 1U,
};

static int prv_check(uint32_t addr, uint32_t len) {
  if ((uint64_t)addr + len > PBL_NANO_FLASH_SIZE) {
    return -EINVAL;
  }
  return 0;
}

static int prv_read(const struct pbl_flash *flash, uint32_t addr, void *buf, size_t len) {
  const struct pbl_nano_ops *ops = pbl_nano_ops();
  int rc = prv_check(addr, (uint32_t)len);
  (void)flash;
  if (rc != 0) {
    return rc;
  }
  if (ops == nullptr || ops->flash_read == nullptr) {
    memset(buf, 0xFF, len);
    return 0;
  }
  return ops->flash_read(addr, buf, (uint32_t)len);
}

static int prv_write(const struct pbl_flash *flash, uint32_t addr, const void *buf, size_t len) {
  const struct pbl_nano_ops *ops = pbl_nano_ops();
  int rc = prv_check(addr, (uint32_t)len);
  (void)flash;
  if (rc != 0) {
    return rc;
  }
  if (ops == nullptr || ops->flash_write == nullptr) {
    return -ENODEV;
  }
  return ops->flash_write(addr, buf, (uint32_t)len);
}

static int prv_erase(const struct pbl_flash *flash, uint32_t addr, uint32_t size) {
  const struct pbl_nano_ops *ops = pbl_nano_ops();
  int rc = prv_check(addr, size);
  (void)flash;
  if (rc != 0) {
    return rc;
  }
  if (ops == nullptr || ops->flash_erase == nullptr) {
    return -ENODEV;
  }
  return ops->flash_erase(addr, size);
}

static int prv_init(const struct pbl_flash *flash) {
  (void)flash;
  return 0;
}

static const struct pbl_flash_ops s_ops = {
  .init = prv_init,
  .read = prv_read,
  .write = prv_write,
  .erase = prv_erase,
};

PBL_FLASH_STATE_DEFINE(pbl_flash_nano);
const struct pbl_flash pbl_flash_nano =
    PBL_FLASH_INIT(pbl_flash_nano, "flash", nullptr, &s_ops, &s_geometry, nullptr, nullptr);
PBL_DEVICE_REGISTER(pbl_flash_nano, &pbl_flash_nano.dev);
