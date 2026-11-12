#pragma once
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct {
    size_t size, used, peak, fails, blocks;
} gd_arena_stats_t;
int gd_arena_init(void *base, size_t size);
int gd_arena_ready(void);
void gd_arena_stats(gd_arena_stats_t *s);
void *gd_malloc(size_t n);
void gd_free(void *p);
void *gd_realloc(void *p, size_t n);
void *gd_calloc(size_t a, size_t b);
#ifdef __cplusplus
}
#endif
