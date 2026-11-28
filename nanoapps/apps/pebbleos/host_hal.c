/* SPDX-FileCopyrightText: 2026 nano7kit contributors */
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

/* Flash banks are cached in RAM and written back once per frame (or
 * when a slot is reused), not on every kernel write. A bank that is all
 * 0xFF is the erased state, so its file is removed instead of written.
 * Slot 0 lives in .bss; up to three more come from the app's aux block
 * when there is room. */
#define MAX_SLOTS  4
#define AUX_SLOTS  3

struct bank_slot {
    uint8_t *buf;
    int id;
    int dirty;
    uint32_t used;
};

static uint8_t s_bank0[BANK_SIZE];
static struct bank_slot s_slots[MAX_SLOTS];
static int s_nslots;
static uint32_t s_clock;
static int s_started;
static void *s_aux;

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

static int prv_all_erased(const uint8_t *buf)
{
    const uint32_t *w = (const uint32_t *)buf;
    uint32_t i;
    for (i = 0; i < BANK_SIZE / 4u; i++)
        if (w[i] != 0xFFFFFFFFu)
            return 0;
    return 1;
}

static void prv_slot_flush(struct bank_slot *sl)
{
    char path[64];
    if (!sl->dirty || sl->id < 0)
        return;
    prv_bank_path(path, (unsigned)sl->id);
    if (prv_all_erased(sl->buf)) {
        if (hb_fs_exists(path))
            hb_fs_remove(path);
    } else {
        hb_fs_mkdir(DATA_DIR);
        hb_fs_mkdir(FLASH_DIR);
        hb_fs_write(path, sl->buf, BANK_SIZE);
    }
    sl->dirty = 0;
}

static void prv_flush_all(void)
{
    int i;
    for (i = 0; i < s_nslots; i++)
        prv_slot_flush(&s_slots[i]);
}

/* The slot holding bank `id`. With `load` 0 the old contents are not read
 * (the caller is about to erase the whole bank). */
static struct bank_slot *prv_bank(unsigned id, int load)
{
    char path[64];
    struct bank_slot *sl = &s_slots[0];
    uint32_t got;
    int i;
    for (i = 0; i < s_nslots; i++) {
        if (s_slots[i].id == (int)id) {
            s_slots[i].used = ++s_clock;
            return &s_slots[i];
        }
        if (s_slots[i].used < sl->used)
            sl = &s_slots[i];
    }
    prv_slot_flush(sl);
    sl->id = (int)id;
    sl->used = ++s_clock;
    memset(sl->buf, 0xFF, BANK_SIZE);
    if (!load)
        return sl;
    prv_bank_path(path, id);
    if (!hb_fs_exists(path))
        return sl;
    got = hb_fs_read(path, sl->buf, BANK_SIZE);
    if (got < BANK_SIZE)
        memset(sl->buf + got, 0xFF, BANK_SIZE - got);
    return sl;
}

static int prv_flash_read(uint32_t addr, void *buf, uint32_t len)
{
    uint8_t *out = (uint8_t *)buf;
    while (len) {
        unsigned id = addr / BANK_SIZE;
        uint32_t off = addr % BANK_SIZE;
        uint32_t n = BANK_SIZE - off;
        if (n > len) n = len;
        memcpy(out, prv_bank(id, 1)->buf + off, n);
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
        struct bank_slot *sl;
        if (n > len) n = len;
        sl = prv_bank(id, 1);
        /* NOR flash only clears bits. */
        for (i = 0; i < n; i++)
            sl->buf[off + i] &= in[i];
        sl->dirty = 1;
        addr += n;
        in += n;
        len -= n;
    }
    return 0;
}

static int prv_flash_erase(uint32_t addr, uint32_t len)
{
    while (len) {
        unsigned id = addr / BANK_SIZE;
        uint32_t off = addr % BANK_SIZE;
        uint32_t n = BANK_SIZE - off;
        struct bank_slot *sl;
        if (n > len) n = len;
        sl = prv_bank(id, !(off == 0 && n == BANK_SIZE));
        memset(sl->buf + off, 0xFF, n);
        sl->dirty = 1;
        addr += n;
        len -= n;
    }
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

static uint32_t prv_le32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

/* The system resources (fonts, icons, the boot logo) are the first flash
 * banks. Without them the kernel cannot draw anything, so check that the
 * pack is there and complete before starting it. PebbleOS checks the CRC
 * itself at boot. */
#define RES_MANIFEST 12u
#define RES_ENTRY    16u
#define RES_TABLE    (RES_MANIFEST + 768u * RES_ENTRY)

int pebbleos_host_resources_ok(void)
{
    char path[64];
    uint32_t n, off, len, end, banks, i;
    prv_bank_path(path, 0);
    if (!hb_fs_exists(path) || hb_fs_size(path) != BANK_SIZE)
        return 0;
    if (hb_fs_read(path, s_bank0, BANK_SIZE) != BANK_SIZE)
        return 0;
    n = prv_le32(s_bank0);
    if (n == 0 || n > 768)
        return 0;
    off = prv_le32(s_bank0 + RES_MANIFEST + (n - 1) * RES_ENTRY + 4);
    len = prv_le32(s_bank0 + RES_MANIFEST + (n - 1) * RES_ENTRY + 8);
    end = RES_TABLE + off + len;
    if (end < off || end > 0x200000u)
        return 0;
    banks = (end + BANK_SIZE - 1) / BANK_SIZE;
    for (i = 1; i < banks; i++) {
        prv_bank_path(path, i);
        if (!hb_fs_exists(path) || hb_fs_size(path) != BANK_SIZE)
            return 0;
    }
    return 1;
}

int pebbleos_host_stopped(void)
{
    return s_started && pbl_nano_wants_reboot();
}

static void prv_slots_init(uint8_t *extra, int count)
{
    int i;
    s_slots[0].buf = s_bank0;
    for (i = 0; i < count; i++)
        s_slots[1 + i].buf = extra + (uint32_t)i * BANK_SIZE;
    s_nslots = 1 + count;
    for (i = 0; i < s_nslots; i++) {
        s_slots[i].id = -1;
        s_slots[i].dirty = 0;
        s_slots[i].used = 0;
    }
}

int pebbleos_host_start(void)
{
    int extra;
    if (s_started)
        return 1;
    hb_fs_mkdir(DATA_DIR);
    hb_fs_mkdir(FLASH_DIR);
    /* One block for the kernel heap and the extra flash slots. It is the
     * app's aux block, so the launcher frees it when the app is closed
     * (there is no quit callback). */
    if (HB_APP_AUX_BLOCK) {
        hb_os_free(HB_APP_AUX_BLOCK);
        HB_APP_AUX_BLOCK = 0;
    }
    s_aux = 0;
    for (extra = AUX_SLOTS; extra >= 0; extra--) {
        s_aux = hb_os_alloc(PBL_NANO_HEAP_SIZE + (uint32_t)extra * BANK_SIZE);
        if (s_aux)
            break;
    }
    if (!s_aux)
        return 0;
    HB_APP_AUX_BLOCK = s_aux;
    prv_slots_init((uint8_t *)s_aux + PBL_NANO_HEAP_SIZE, extra);
    /* The kernel heap initialises this block itself. */
    pbl_nano_set_ops(&s_ops);
    pbl_nano_memory_init(s_aux, PBL_NANO_HEAP_SIZE);
    if (!pbl_nano_kernel_start()) {
        prv_flush_all();
        hb_os_free(s_aux);
        HB_APP_AUX_BLOCK = 0;
        s_aux = 0;
        return -1;
    }
    prv_flush_all();
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
    prv_flush_all();
}
