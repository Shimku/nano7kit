/* Host / nano platform bits shared by the GL layer and backends (nano7kit, MIT). */
#ifndef AB_RT_PLAT_H
#define AB_RT_PLAT_H
#include <stdint.h>
void plat_log_line(const char *line);
void plat_log_flush(void);
void plat_fatal(const char *why);
uint64_t plat_time_us(void);
void *plat_os_alloc(uint32_t n);
void plat_os_free(void *p);
uint32_t plat_os_largest(void);
/* Whole-file read for JNI MyRenderer.readFile. 0 on success. */
int plat_read_file(const char *name, void **out, uint32_t *len);
/* Absolute path, no search. Persist migrate / nfile flush use these. */
int plat_read_abs(const char *path, void **out, uint32_t *len);
int plat_write_abs(const char *path, const void *buf, uint32_t n);
int plat_exists_abs(const char *path);
int plat_mkdir_abs(const char *path);
const char *plat_files_dir(void);
const char *plat_data_dir(void);
#endif
