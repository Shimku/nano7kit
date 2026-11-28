#pragma once
#include <nimble/ble.h>
#include <stdint.h>
#include <stdbool.h>

struct ble_store_value_sec {
  ble_addr_t peer_addr;
  uint8_t key[16];
  uint8_t unused[48];
};
