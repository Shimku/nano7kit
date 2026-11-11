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
void ab_ctype_init(void);
void ab_diag_set(int on);
void ab_diag_reset_logs(void);
int ab_diag_on(void);
void ab_diag_report(const char *where);
uint32_t ab_diag_sp(void);
uint32_t ab_diag_minsp(void);
double ab_strtod(const char *nptr, char **endptr);
void *ab_ctype_ptr(void);
void ab_shims_reset(void);
int ab_persist_name(const char *name);
const char *ab_persist_leaf(const char *name);
int ab_persist_in_docs(const char *path);
int ab_persist_lua_ok(const char *name, const void *buf, uint32_t n);
int ab_persist_empty(void **out, uint32_t *len);
void ab_persist_prepare(void);
void ab_persist_note_open(uint32_t n);
int ab_persist_last_empty(void);
int ab_persist_skip_load(int remaining, const char *name);
void ab_persist_compile_enter(void);
void ab_persist_compile_leave(void);
int ab_persist_in_compile(void);
const char *ab_last_io(void);
void ab_note_io(const char *name);
void ab_note_lua_throw(int err);
void ab_throw_report(void);
int ab_cxa_throw_count(void);
int ab_lua_throw_count(void);
int ab_persist_lua_throw_count(void);
#endif
