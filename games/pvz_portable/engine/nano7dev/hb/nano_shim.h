/* nano7kit PvZ (iPod build): single-threaded stand-ins for what newlib's
 * libstdc++ leaves out without gthreads (the game runs on one thread). */
#pragma once
#ifdef __cplusplus
#include <mutex>
#include <thread>
#include <chrono>
#include <condition_variable>
#if !defined(_GLIBCXX_HAS_GTHREADS)
namespace std {
struct mutex { constexpr mutex() noexcept = default; void lock() {} void unlock() {} bool try_lock() { return true; } };
struct recursive_mutex { void lock() {} void unlock() {} bool try_lock() { return true; } };
}
#endif
#endif
#if defined(__cplusplus) && !defined(_GLIBCXX_HAS_GTHREADS)
namespace std { template <class... M> struct scoped_lock { explicit scoped_lock(M &...) {} }; }
#endif
#include <time.h>
#ifdef __cplusplus
extern "C" int nanosleep(const struct timespec *, struct timespec *);
#endif
