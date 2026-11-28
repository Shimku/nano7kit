/* SPDX-FileCopyrightText: 2026 Nikita Shimkus */
/* SPDX-License-Identifier: MIT */

/* NanoApps side of the hosted PebbleOS kernel: time, file-backed flash
 * under /Apps/Data/PebbleOS, and the pebbleos_host_* hooks.
 */

#include "hb_sdk.h"
#include "hb_heap.h"
#include "pebbleos_host.h"
#include <pbl/nano_hal.h>

#include <string.h>

#define DATA_DIR   "/Apps/Data/PebbleOS"
#define FLASH_DIR  "/Apps/Data/PebbleOS/flash"
#define PBPACK_PATH "/Apps/Data/PebbleOS/system_resources.pbpack"
#define BANK_SIZE  0x10000u
#define BANK_COUNT (PBL_NANO_FLASH_SIZE / BANK_SIZE)

static uint8_t s_bank[BANK_SIZE];
static int s_bank_id = -1;
static int s_bank_dirty;
static int s_started;
static void *s_heap;

static uint64_t prv_monotonic_us(void)
{
    return (uint64_t)hb_time_uptime_us();
}

static int64_t prv_days(int y, int m, int d)
{
    static const uint16_t md[12] = {
        0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334
    };
    int64_t days = (int64_t)(y - 1970) * 365;
    int y0;
    days += (y - 1969) / 4 - (y - 1901) / 100 + (y - 1601) / 400;
    days += md[m - 1] + (d - 1);
    /* Leap day in the current year, if we are past February. */
    y0 = y;
    if (m > 2 && ((y0 % 4 == 0 && y0 % 100 != 0) || (y0 % 400 == 0)))
        days += 1;
    return days;
}

static int64_t prv_unix_time(void)
{
    hb_rtc_time_t t;
    memset(&t, 0, sizeof(t));
    hb_rtc_read(&t);
    if (t.year < 1970 || t.month < 1 || t.month > 12 || t.day_of_month < 1)
        return 0;
    return prv_days(t.year, t.month, t.day_of_month) * 86400
        + (int64_t)t.hours * 3600 + (int64_t)t.minutes * 60 + t.seconds;
}

static void prv_bank_path(char *out, unsigned id)
{
    /* /Apps/Data/PebbleOS/flash/00.bin */
    char *p = out;
    const char *s = FLASH_DIR "/";
    while (*s) *p++ = *s++;
    p[0] = (char)('0' + (id / 16) % 16);
    if (p[0] > '9') p[0] = (char)('a' + (p[0] - '0' - 10));
    p[1] = (char)('0' + (id % 16));
    if (p[1] > '9') p[1] = (char)('a' + (p[1] - '0' - 10));
    p[2] = '.'; p[3] = 'b'; p[4] = 'i'; p[5] = 'n'; p[6] = 0;
}

static void prv_bank_flush(void)
{
    char path[64];
    if (!s_bank_dirty || s_bank_id < 0)
        return;
    prv_bank_path(path, (unsigned)s_bank_id);
    hb_fs_mkdir(DATA_DIR);
    hb_fs_mkdir(FLASH_DIR);
    hb_fs_write(path, s_bank, BANK_SIZE);
    s_bank_dirty = 0;
}

static void prv_bank_load(unsigned id)
{
    char path[64];
    uint32_t got;
    if ((int)id == s_bank_id)
        return;
    prv_bank_flush();
    s_bank_id = (int)id;
    memset(s_bank, 0xFF, BANK_SIZE);
    prv_bank_path(path, id);
    if (!hb_fs_exists(path))
        return;
    got = hb_fs_read(path, s_bank, BANK_SIZE);
    if (got < BANK_SIZE)
        memset(s_bank + got, 0xFF, BANK_SIZE - got);
}

static int prv_flash_read(uint32_t addr, void *buf, uint32_t len)
{
    uint8_t *out = (uint8_t *)buf;
    while (len) {
        unsigned id = addr / BANK_SIZE;
        uint32_t off = addr % BANK_SIZE;
        uint32_t n = BANK_SIZE - off;
        if (n > len) n = len;
        prv_bank_load(id);
        memcpy(out, s_bank + off, n);
        addr += n;
        out += n;
        len -= n;
    }
    return 0;
}

static int prv_flash_write(uint32_t addr, const void *buf, uint32_t len)
{
    const uint8_t *in = (const uint8_t *)buf;
    while (len) {
        unsigned id = addr / BANK_SIZE;
        uint32_t off = addr % BANK_SIZE;
        uint32_t n = BANK_SIZE - off;
        uint32_t i;
        if (n > len) n = len;
        prv_bank_load(id);
        /* NOR flash only clears bits. */
        for (i = 0; i < n; i++)
            s_bank[off + i] &= in[i];
        s_bank_dirty = 1;
        addr += n;
        in += n;
        len -= n;
    }
    prv_bank_flush();
    return 0;
}

static int prv_flash_erase(uint32_t addr, uint32_t len)
{
    while (len) {
        unsigned id = addr / BANK_SIZE;
        uint32_t off = addr % BANK_SIZE;
        uint32_t n = BANK_SIZE - off;
        if (n > len) n = len;
        prv_bank_load(id);
        memset(s_bank + off, 0xFF, n);
        s_bank_dirty = 1;
        addr += n;
        len -= n;
    }
    prv_bank_flush();
    return 0;
}

static const struct pbl_nano_ops s_ops = {
    .monotonic_us = prv_monotonic_us,
    .unix_time = prv_unix_time,
    .flash_read = prv_flash_read,
    .flash_write = prv_flash_write,
    .flash_erase = prv_flash_erase,
};

int pebbleos_host_available(void)
{
    return 1;
}

int pebbleos_host_start(void)
{
    if (s_started)
        return 1;
    hb_fs_mkdir(DATA_DIR);
    hb_fs_mkdir(FLASH_DIR);
    s_heap = hb_os_alloc(PBL_NANO_HEAP_SIZE);
    if (!s_heap)
        return 0;
    /* The kernel heap initialises this block itself. */
    pbl_nano_set_ops(&s_ops);
    pbl_nano_memory_init(s_heap, PBL_NANO_HEAP_SIZE);
    if (!pbl_nano_kernel_start()) {
        hb_os_free(s_heap);
        s_heap = 0;
        return 0;
    }
    s_started = 1;
    return 1;
}

const uint16_t *pebbleos_host_fb(void)
{
    return s_started ? pbl_nano_fb_rgb565() : 0;
}

void pebbleos_host_button(int pebble_key, int down)
{
    if (s_started)
        pbl_nano_button(pebble_key, down);
}

void pebbleos_host_tick(void)
{
    if (!s_started)
        return;
    pbl_nano_timer_poll();
    pbl_nano_kernel_run();
}
