#include "gdalloc.h"
#include "../gd_config.h"
#include <new>
#include <cstddef>

void *operator new(std::size_t n)
{
    void *p = gd_malloc(n);
    if (!p) gd_fatal("out of arena");
    return p;
}

void *operator new[](std::size_t n)
{
    void *p = gd_malloc(n);
    if (!p) gd_fatal("out of arena");
    return p;
}

void operator delete(void *p) noexcept { gd_free(p); }
void operator delete[](void *p) noexcept { gd_free(p); }
void operator delete(void *p, std::size_t) noexcept { gd_free(p); }
void operator delete[](void *p, std::size_t) noexcept { gd_free(p); }

void *operator new(std::size_t n, const std::nothrow_t&) noexcept { return gd_malloc(n); }
void *operator new[](std::size_t n, const std::nothrow_t&) noexcept { return gd_malloc(n); }
void operator delete(void *p, const std::nothrow_t&) noexcept { gd_free(p); }
void operator delete[](void *p, const std::nothrow_t&) noexcept { gd_free(p); }

#if defined(__cpp_aligned_new)
void *operator new(std::size_t n, std::align_val_t) { return operator new(n); }
void *operator new[](std::size_t n, std::align_val_t a) { return operator new(n, a); }
void operator delete(void *p, std::align_val_t) noexcept { gd_free(p); }
void operator delete[](void *p, std::align_val_t) noexcept { gd_free(p); }
void operator delete(void *p, std::size_t, std::align_val_t) noexcept { gd_free(p); }
void operator delete[](void *p, std::size_t, std::align_val_t) noexcept { gd_free(p); }
#endif
