#pragma once

#include <stdint.h>

#define GD_SCREEN_W 240
#define GD_SCREEN_H 432
#define GD_BAR_H 56
#define GD_GAME_H (GD_SCREEN_H - GD_BAR_H)
#define GD_DATA_DIR "/Apps/Data/GravityDefied"

#ifdef __cplusplus
extern "C" {
#endif

void gd_log(const char *fmt, ...);
void gd_log_flush(void);
void gd_fatal(const char *why);
int gd_fs_read(const char *rel, void *buf, unsigned max);
int gd_fs_write(const char *rel, const void *buf, unsigned n);
int gd_fs_exists(const char *rel);
unsigned gd_fs_size(const char *rel);
int gd_fs_remove(const char *rel);
void gd_fs_list(void (*cb)(const char *name, void *ctx), void *ctx);
const char *gd_data_dir(void);
void gd_set_data_dir(const char *dir);
uint32_t *gd_fb(void);
int gd_fb_w(void);
int gd_fb_h(void);
void gd_set_fb(uint32_t *fb, int w, int h);
int gd_want_home(void);
void gd_request_home(const char *why);
uint32_t gd_millis(void);
void gd_sleep_ms(int ms);
int gd_held_gas(void);
int gd_held_brake(void);
int gd_held_lean_l(void);
int gd_held_lean_r(void);
int gd_tap_fire(void);
int gd_tap_back(void);
int gd_tap_pause(void);
int gd_menu_tap_x(void);
int gd_menu_tap_y(void);
void gd_input_reset_edges(void);
void gd_set_held(int gas, int brake, int ll, int lr);
void gd_set_edges(int fire, int back, int pause, int x, int y);
unsigned gd_arena_used(void);
unsigned gd_arena_peak(void);
unsigned gd_arena_fails(void);

#ifdef GD_HOST
void gd_host_set_root(const char *p);
void gd_host_advance_ms(uint32_t d);
uint32_t gd_host_ms(void);
void gd_host_set_buttons(int gas, int brake, int ll, int lr);
void gd_host_tap(int fire, int back, int pause, int x, int y);
const char *gd_host_log(void);
void gd_host_clear_home(void);
#endif

#ifdef __cplusplus
}
#endif
