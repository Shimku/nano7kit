/* Newlib / libstdc++ hooks for the relocatable RAW_SURFACE image.
 * Same idea as PvZ's n7hb_stubs / n7plat_hb: one static image, no host
 * libc, malloc goes through the dlmalloc mspace, abort hangs. */
#include "gd_config.h"
#include "gdalloc.h"
#include <stddef.h>

void *__dso_handle __attribute__((used)) = 0;

void _init(void) {}
void _fini(void) {}
void __libc_fini(void) {}

int __cxa_atexit(void (*fn)(void *), void *arg, void *dso)
{
    (void)fn;
    (void)arg;
    (void)dso;
    return 0;
}

int __aeabi_atexit(void *arg, void (*fn)(void *), void *dso)
{
    return __cxa_atexit(fn, arg, dso);
}

void __cxa_pure_virtual(void)
{
    gd_fatal("pure virtual");
}

void abort(void)
{
    gd_fatal("abort");
    for (;;) {}
}

void _exit(int code)
{
    (void)code;
    gd_fatal("exit");
    for (;;) {}
}

void *_sbrk(int n)
{
    (void)n;
    return (void *)(long)-1;
}

int _isatty(int fd)
{
    (void)fd;
    return 0;
}

int _getpid(void) { return 1; }

int _kill(int pid, int sig)
{
    (void)pid;
    (void)sig;
    return -1;
}

int _write(int fd, const void *buf, int n)
{
    (void)fd;
    (void)buf;
    return n;
}

int _read(int fd, void *buf, int n)
{
    (void)fd;
    (void)buf;
    (void)n;
    return 0;
}

int _close(int fd)
{
    (void)fd;
    return -1;
}

int _lseek(int fd, int off, int whence)
{
    (void)fd;
    (void)off;
    (void)whence;
    return 0;
}

int _fstat(int fd, void *st)
{
    (void)fd;
    (void)st;
    return -1;
}

void *malloc(size_t n) { return gd_malloc(n); }
void free(void *p) { gd_free(p); }
void *realloc(void *p, size_t n) { return gd_realloc(p, n); }
void *calloc(size_t a, size_t b) { return gd_calloc(a, b); }

extern const char __exidx_start[] __attribute__((weak));
extern const char __exidx_end[] __attribute__((weak));

unsigned long __gnu_Unwind_Find_exidx(unsigned long pc, int *nrec)
{
    (void)pc;
    if (nrec)
        *nrec = (int)((__exidx_end - __exidx_start) / 8);
    return (unsigned long)(uintptr_t)__exidx_start;
}
