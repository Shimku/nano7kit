#pragma once
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct { size_t size, used, peak, high_water, fails, biggest_fail, blocks; } n7_arena_stats_t;
int n7_arena_init(void* base, size_t size);
int n7_arena_add(void* base, size_t size);   /* a second block (device: heap split) */
int n7_arena_ready(void);
void n7_arena_stats(n7_arena_stats_t* s);
void n7_arena_reset_peak(void);
size_t n7_arena_largest(void);
#ifdef __cplusplus
}
#endif
