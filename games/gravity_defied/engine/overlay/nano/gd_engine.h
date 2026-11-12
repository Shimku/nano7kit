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
#endif
#ifdef __cplusplus
}
#endif
