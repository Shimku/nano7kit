/* nano7kit measurement: heap tracker (desktop measuring build only, never shipped).
   In the device build (NANO7_DEVICE) there is no tracker: the tags compile away and the functions are stubs
   (nano7dev/memtrack_dev.c) so the dry-run bot still links; the arena keeps the real counts there. */
#pragma once
#include <stddef.h>
enum { MT_OTHER, MT_PIXELS, MT_REANIM, MT_PARTICLE, MT_FONT, MT_POOL, MT_FILE, MT_STRINGS, MT_RESMGR, MT_SOUND, MT_BOARD, MT_DECODE, MT_NTAGS };
#ifdef __cplusplus
extern "C" {
#endif
#ifdef NANO7_DEVICE
#define mt_tag 0
#else
extern __thread int mt_tag;
#endif
void   mt_reset_peak(void);
size_t mt_live(int tag);          /* tag -1 = all */
size_t mt_live_nonpixel(void);
size_t mt_peak(void);
size_t mt_peak_nonpixel(void);
size_t mt_peak_tag(int tag);
size_t mt_model_live(void);       /* nano allocator model: align8(size)+8 per block, non-pixel */
size_t mt_peak_model(void);
size_t mt_count(void);
size_t mt_nano_live(void);        /* nano heap model: non-pixel model + CPU pixel buffers at 0.4 scale (x0.16) */
size_t mt_peak_nano(void);
size_t mt_atpeak_tag(int tag);   /* live bytes per tag at the moment of the nano peak */
size_t mt_atpeak_last(void);
extern int mt_phase, mt_atpeak_phase;  /* scene * 1000 + seconds into it, set by the bot */
int mt_atpeak_lasttag(void);
size_t mt_biggest_nonpixel(void);  /* largest single non-pixel block allocated since reset */
size_t mt_biggest_pixel(void);
const char* mt_tag_name(int tag);
void   mt_dump_big(const char* path, size_t minsize);
#ifdef __cplusplus
}
#ifdef NANO7_DEVICE
struct MtScope { MtScope(int) {} };
#define MT_TAG(t) ((void)0)
#else
struct MtScope { int old; MtScope(int t) : old(mt_tag) { mt_tag = t; } ~MtScope() { mt_tag = old; } };
#define MT_TAG(t) MtScope _mt_scope_##__LINE__(t)
#endif
#endif
