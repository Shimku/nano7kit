#pragma once
#include <nimble/ble.h>
#include <stdint.h>

typedef int ble_gap_event_fn(struct ble_gap_event *event, void *arg);

struct ble_gap_event {
  uint8_t type;
};

struct ble_gap_adv_params {
  uint8_t unused;
};

int ble_gap_adv_set_fields(const void *fields);
int ble_gap_adv_start(uint8_t own_addr_type, const ble_addr_t *direct_addr,
                      int32_t duration_ms, const struct ble_gap_adv_params *params,
                      ble_gap_event_fn *cb, void *arg);
