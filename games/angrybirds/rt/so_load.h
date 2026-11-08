/* so_load.h: load an Android ELF32 ET_DYN at a fixed host address (nano7kit, MIT). */
#ifndef AB_SO_LOAD_H
#define AB_SO_LOAD_H
#include <stddef.h>
#include <stdint.h>
typedef struct {
    uint8_t *base;
    size_t map_size;
    uint32_t load_bias;
    uint32_t exidx_off, exidx_count;
    uint32_t init_off, init_count;
    int loaded;
} so_image;
int so_load(const char *path, so_image *out);
void *so_sym(const so_image *img, const char *name);
void so_run_init(const so_image *img);
void *ab_resolve(const char *name);
extern so_image g_so;
#endif
