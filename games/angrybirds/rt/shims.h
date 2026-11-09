/* shims.h: bionic / GLES import resolver for libangrybirds.so (nano7kit, MIT). */
#ifndef AB_SHIMS_H
#define AB_SHIMS_H
#include <stddef.h>
#include <stdint.h>
void *ab_resolve(const char *name);
void ab_install_cxa_hook(void);
uint32_t ab_heap_used(void);
uint32_t ab_heap_peak(void);
uint32_t ab_malloc_failures(void);
void ab_heap_init(void *p, uint32_t n);
int ab_heap_ready(void);
int ab_heap_contains(const void *p);
void *ab_malloc(size_t n);
void ab_free(void *p);
void *ab_realloc(void *p, size_t n);
void ab_shims_reset(void);
int ab_persist_name(const char *name);
int ab_persist_lua_ok(const char *name, const void *buf, uint32_t n);
int ab_persist_empty(void **out, uint32_t *len);
#endif
