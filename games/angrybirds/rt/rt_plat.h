/* rt_plat.h: what the Angry Birds runtime needs from the host or the nano (nano7kit, MIT). */
#ifndef FN_RT_PLAT_H
#define FN_RT_PLAT_H
#include <stdint.h>
void plat_log_line(const char *line);
void plat_fatal(const char *why);
uint32_t plat_load_image(void *dst, uint32_t size);   /* guest.img */
uint64_t plat_time_us(void);
/* game files by name (fileArcive.bin pieces, saves): whole-file reads */
int32_t plat_file_size(const char *name);              /* -1 if absent */
int32_t plat_file_read(const char *name, uint32_t off, void *dst, uint32_t n);
int plat_file_write(const char *name, const void *src, uint32_t n);
void plat_trace(void);                                 /* host: print a backtrace; nano: no-op */
void *plat_os_alloc(uint32_t n);                       /* nano: OS heap; host: calloc. Large GL scratch and image decode. */
void plat_os_free(void *p);
uint32_t plat_os_largest(void);                        /* largest contiguous OS-heap block (pure read on the nano) */
#endif
