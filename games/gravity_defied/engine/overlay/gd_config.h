#pragma once

#include <stdint.h>

#define GD_SCREEN_W 240
#define GD_SCREEN_H 432
/* iOS-style 3x3 keypad: 80x42 cells (at least 40 px tall), 3 px green progress. */
#define GD_PAD_CELL_W 80
#define GD_PAD_CELL_H 42
#define GD_PAD_ROWS 3
#define GD_PROG_H 3
#define GD_PAD_Y (GD_SCREEN_H - (GD_PAD_ROWS * GD_PAD_CELL_H))
#define GD_GAME_H (GD_PAD_Y - GD_PROG_H)
#define GD_BAR_H (GD_SCREEN_H - GD_GAME_H)
#define GD_DATA_DIR "/Apps/Data/GravityDefied"
#define GD_PAUSE_X (GD_SCREEN_W - 40)
#define GD_PAUSE_Y 4
#define GD_PAUSE_W 36
#define GD_PAUSE_H 40
#define GD_MENU_ROW_MIN 36
#define GD_MENU_TEXT_X 24
#define GD_MENU_MARKER_X 10
/* Original Micro::run slept so each outer frame was at least 30 ms, with
 * numPhysicsLoops (2) physics updates per frame. */
#define GD_TICK_MS 30
#define GD_TICK_CATCHUP 3
#define GD_TILT_DEAD 180
#define GD_MAX_FINGERS 8

#ifdef __cplusplus
extern "C" {
#endif

void gd_log(const char *fmt, ...);
void gd_log_flush(void);
void gd_log_close(void);
int gd_log_closed(void);
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
unsigned gd_held_keys(void);
void gd_set_key_mask(unsigned mask);
int gd_pad_digit(int x, int y);
int gd_hit_pause(int x, int y);
void gd_set_progress(int p16);
int gd_tap_fire(void);
int gd_tap_back(void);
int gd_tap_pause(void);
int gd_menu_tap_x(void);
int gd_menu_tap_y(void);
void gd_input_reset_edges(void);
void gd_set_held(int gas, int brake, int ll, int lr);
void gd_set_edges(int fire, int back, int pause, int x, int y);
int gd_bar_zone(int x);
void gd_draw_touch_bar(uint32_t *fb, int w, int h);
unsigned gd_arena_used(void);
unsigned gd_arena_peak(void);
unsigned gd_arena_fails(void);

void gd_fingers_clear(void);
void gd_finger_at(int id, int x, int y);
void gd_finger_up(int id);
void gd_set_tilt_enabled(int on);
int gd_tilt_enabled(void);
void gd_tilt_recalibrate(void);
void gd_set_accel_x(int milli_g);
int gd_accel_x(void);
void gd_recompute_keys(void);
void gd_log_input_state(const char *why);

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
