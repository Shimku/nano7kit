/* Stubs for Bluetooth, UART, and voice symbols that stay referenced
 * by built-in apps when those services are compiled out of the
 * hosted kernel. */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

struct pbl_uart;
const struct pbl_uart *const CONSOLE_UART = NULL;

void ams_music_disconnect(void) {}

void ancs_perform_action(uint32_t notification_uid, uint8_t action_id) {
  (void)notification_uid;
  (void)action_id;
}

bool bt_bonding_get_peer_name(const void *addr, char *name) {
  (void)addr;
  if (name) {
    name[0] = '\0';
  }
  return false;
}

void bt_bonding_init(void) {}

void bt_ctl_init(void) {}

bool bt_ctl_is_airplane_mode_on(void) {
  return false;
}

void bt_ctl_set_airplane_mode_async(bool enabled) {
  (void)enabled;
}

void bt_ctl_set_enabled(bool enabled) {
  (void)enabled;
}

bool bt_link_is_connected_to(const void *identity) {
  (void)identity;
  return false;
}

void bt_link_read_peer_name(void) {}

void bt_local_id_copy_address(uint8_t addr_out[6]) {
  if (addr_out) {
    memset(addr_out, 0, 6);
  }
}

void bt_local_id_copy_address_string(char addr_str_out[18]) {
  if (addr_str_out) {
    memcpy(addr_str_out, "00:00:00:00:00:00", 18);
  }
}

void bt_local_id_copy_device_name(char name_out[20]) {
  if (name_out) {
    memcpy(name_out, "Pebble", 7);
  }
}

void pbl_bt_foreach_bond(void (*func)(const void *info, void *user_data), void *user_data) {
  (void)func;
  (void)user_data;
}

void pbl_bt_pairing_confirm(uint16_t conn_handle, bool is_confirmed) {
  (void)conn_handle;
  (void)is_confirmed;
}

void pbl_bt_prf_store_init(void) {}

int pbl_bt_prf_store_wipe(void) {
  return 0;
}

int pbl_bt_unpair(const void *addr) {
  (void)addr;
  return 0;
}

int pbl_uart_early_init(const struct pbl_uart *uart, const void *cfg) {
  (void)uart;
  (void)cfg;
  return 0;
}

void pbl_uart_flush(const struct pbl_uart *uart) {
  (void)uart;
}

void pbl_uart_poll_out(const struct pbl_uart *uart, uint8_t c) {
  (void)uart;
  (void)c;
}

int pbl_uart_rx_disable(const struct pbl_uart *uart) {
  (void)uart;
  return 0;
}

int pbl_uart_rx_enable(const struct pbl_uart *uart, void *cb, void *arg) {
  (void)uart;
  (void)cb;
  (void)arg;
  return 0;
}

void *voice_window_create(char *buffer, size_t buffer_size, int session_type) {
  (void)buffer;
  (void)buffer_size;
  (void)session_type;
  return NULL;
}

void voice_window_destroy(void *voice_window) {
  (void)voice_window;
}

int voice_window_push(void *voice_window) {
  (void)voice_window;
  return 0;
}

void voice_window_reset(void *voice_window) {
  (void)voice_window;
}

void voice_window_set_confirmation_enabled(void *voice_window, bool enabled) {
  (void)voice_window;
  (void)enabled;
}


/* Replace libnosys: those objects use MOVW/MOVT against errno, which the
 * .hbapp loader cannot rebase. A plain int is reached with ABS32 word relocs. */
int errno;

int _close(int fd) {
  (void)fd;
  return -1;
}

int _fstat(int fd, void *st) {
  (void)fd;
  (void)st;
  return -1;
}

int _getpid(void) {
  return 1;
}

int _isatty(int fd) {
  (void)fd;
  return 1;
}

int _kill(int pid, int sig) {
  (void)pid;
  (void)sig;
  return -1;
}

long _lseek(int fd, long off, int whence) {
  (void)fd;
  (void)off;
  (void)whence;
  return -1;
}

int _read(int fd, void *buf, int n) {
  (void)fd;
  (void)buf;
  (void)n;
  return 0;
}

int _write(int fd, const void *buf, int n) {
  (void)fd;
  (void)buf;
  (void)n;
  return n;
}

void _exit(int status) {
  (void)status;
  for (;;) {
  }
}


void *memset(void *s, int c, unsigned n)
{
    unsigned char *p = (unsigned char *)s;
    unsigned char v = (unsigned char)c;
    while (n && ((unsigned long)p & 3u)) {
        *p++ = v;
        n--;
    }
    if (n >= 4) {
        unsigned w = (unsigned)v * 0x01010101u;
        unsigned *wp = (unsigned *)p;
        while (n >= 4) {
            *wp++ = w;
            n -= 4;
        }
        p = (unsigned char *)wp;
    }
    while (n) {
        *p++ = v;
        n--;
    }
    return s;
}

void *memcpy(void *dst, const void *src, unsigned n)
{
    unsigned char *d = (unsigned char *)dst;
    const unsigned char *s = (const unsigned char *)src;
    while (n) {
        *d++ = *s++;
        n--;
    }
    return dst;
}
