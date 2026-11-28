#pragma once
#include <stdint.h>

typedef struct {
  uint8_t type;
  union {
    uint16_t u16;
    uint8_t u128[16];
  } value;
} ble_uuid_t;

typedef ble_uuid_t ble_uuid16_t;
typedef ble_uuid_t ble_uuid128_t;
#define BLE_UUID16_INIT(x) { .type = 16, .value.u16 = (x) }
