/* SPDX-FileCopyrightText: 2026 Nikita Shimkus */
/* SPDX-License-Identifier: Apache-2.0 */

#include <pbl/device.h>
#include <pbl/input/input.h>
#include <pbl/nano_hal.h>
#include <pbl_arch_posix.h>

static const uint16_t s_codes[] = {
  [PBL_NANO_KEY_BACK] = PBL_INPUT_KEY_BACK,
  [PBL_NANO_KEY_UP] = PBL_INPUT_KEY_UP,
  [PBL_NANO_KEY_SELECT] = PBL_INPUT_KEY_SELECT,
  [PBL_NANO_KEY_DOWN] = PBL_INPUT_KEY_DOWN,
};

static bool s_ready;

struct prv_key_event {
  int key;
  bool pressed;
};

static void prv_key_isr(void *arg) {
  const struct prv_key_event *e = arg;
  if (s_ready && (unsigned)e->key < 4U) {
    pbl_input_report_key(s_codes[e->key], e->pressed, true);
  }
}

void pbl_nano_button(int key, int down) {
  struct prv_key_event e = {.key = key, .pressed = down != 0};
  pbl_posix_irq_run(prv_key_isr, &e);
}

static int prv_init(const struct pbl_device *dev) {
  (void)dev;
  s_ready = true;
  return 0;
}

PBL_DEVICE_STATE_DEFINE(input_nano);
static const struct pbl_device input_nano =
    PBL_DEVICE_INIT(input_nano, "input_nano", prv_init, nullptr);
PBL_DEVICE_REGISTER(input_nano, &input_nano);
