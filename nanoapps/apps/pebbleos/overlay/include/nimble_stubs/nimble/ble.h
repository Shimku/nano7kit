/* Minimal NimBLE types so public headers parse when CONFIG_BT is off. */
#pragma once

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

#define BLE_ADDR_LEN 6
#define BLE_ADDR_PUBLIC 0
#define BLE_ADDR_RANDOM 1

typedef struct {
  uint8_t type;
  uint8_t val[BLE_ADDR_LEN];
} ble_addr_t;

#ifdef __cplusplus
}
#endif
