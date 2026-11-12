#pragma once
#ifdef __cplusplus
extern "C" {
#endif
int gd_engine_start(const char *mrg, const char *data);
void gd_engine_tick(void);
int gd_engine_running(void);
void gd_engine_quit(void);
int gd_engine_in_menu(void);
int gd_engine_loading(void);
void gd_engine_dump_stats(char *buf, unsigned n);
#ifdef GD_HOST
void gd_host_key(int code);
void gd_host_key_up(int code);
int gd_engine_start_track(int level, int track, int league);
int gd_engine_track_result(void);
void gd_engine_clear_result(void);
const char *gd_engine_track_name(void);
void gd_engine_note_physics(int v);
void gd_engine_select_row(int row);
void gd_engine_pick_row(int row);
void gd_engine_set_unlocks(int easy, int medium, int pro);
#endif
#ifdef __cplusplus
}
#endif
