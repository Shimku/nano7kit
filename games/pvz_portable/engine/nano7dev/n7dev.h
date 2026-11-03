/* nano7 PvZ device layer: what the engine needs from the platform (iPod nano 7G app, or the x86 dry run).
 * Part of the nano7kit PvZ port; links against PvZ-Portable (LGPL-3.0-or-later, wszqkzqk). */
#pragma once
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* ---- platform (n7plat_hb.c on the iPod, n7plat_posix.c in the dry run) ---- */
uint32_t n7p_ms(void);                               /* milliseconds since start */
uint64_t n7p_us(void);
void     n7p_log(const char* line);                  /* buffered; written to log.txt only by n7p_log_flush */
void     n7p_log_flush(void);
void     n7p_note_sp(void);                        /* track the lowest sp (device; no-op in the dry run) */
uint32_t n7p_min_sp(void);
uint32_t n7p_fs_calls(void);
void     n7p_step(const char* what);                 /* breadcrumb: logged and written to log.txt at once */
void     n7p_step_a(const char* what, uint32_t a);
int      n7p_mkdir_abs(const char* path);             /* one folder (absolute path), 0 ok or it exists */
const char* n7p_data_dir(void);                      /* "/Apps/Data/PvZ" on the iPod */
/* files, by path relative to the data folder (lower case). Only whole-file reads, which is what the iPod's SDK does
 * reliably: read_into reads the first max bytes (the buffer must have 8 KB more room: sector slack on the iPod). */
int      n7p_read_into(const char* rel, void* buf, uint32_t max);   /* bytes read, -1 if missing */
int      n7p_read_head(const char* rel, void* buf, uint32_t n);    /* first n bytes, one OS call; -1 if missing */
uint32_t n7p_size(const char* rel);                                /* 0 if missing */
int      n7p_exists_rel(const char* rel);
int      n7p_write_whole(const char* rel, const void* data, uint32_t n); /* 0 ok */
void     n7p_present(void);
int      n7p_wav_play(const char* path, uint32_t vol_0_7fff); /* 1 if started */
uint32_t n7p_os_free(void);                          /* OS heap free bytes (0 if unknown) */
uint32_t n7p_os_largest(void);
/* ---- the shipped file list (n7index.c) ---- */
void     n7x_build(const char* lst, uint32_t n);    /* files.lst contents; NULL: no list, never answer */
int      n7x_state(void);                           /* 0 not built, 1 built, -1 no list */
int      n7x_hashed(void);                          /* 1: the hashed d/XX/HHHHHHHH layout */
uint32_t n7x_count(void);
uint32_t n7x_hash(const char* s, size_t n, uint32_t seed);
int      n7x_find(const char* abs, const char* data_dir, char* dev, size_t devn, uint32_t* size, const uint16_t** hdr);
int      n7x_absent(const char* abs, const char* data_dir);  /* 1: not shipped, no need to ask the file system */
int      n7x_head(const uint16_t* hdr, void* buf, uint32_t n);
/* ---- engine side (n7main.cpp) ---- */
int  n7_init(void* arena, size_t arena_size);        /* 0 ok */
int  n7_frame(void);                                 /* 0 running, 1 quit */
void n7_touch(int down, int x, int y);               /* panel pixels (240x432 portrait) */
void n7_fatal(const char* why);
char* n7_getenv(const char* name);
extern int n7_panel_w, n7_panel_h;
/* counters for the memory readout */
typedef struct {
	uint32_t tex_bytes, tex_peak, tex_count, tex_pieces;    /* GL texture bytes as uploaded (16-bit) */
	uint32_t tris_frame, tris_peak, draws_frame, draws_peak;
	uint32_t img_n7t, img_decoded, img_decoded_bytes;      /* images made from .n7t headers / decoded on the CPU */
	uint32_t bits_wanted, bits_wanted_bytes;               /* CPU pixels asked of a .n7t image (zero-filled) */
	uint32_t file_opens, file_read_bytes, file_biggest;
	uint32_t snd_played, snd_dropped;
	uint32_t upload_buf, tex_fail, file_whole_peak;
	uint32_t tris_need, tris_drawn, tris_skipped, tris_capped, tris_need_peak, frames_trimmed, frames_capped, tri_budget;
	uint32_t tris_drawn_peak, prot_cut, drop_below, bakes;
	uint32_t play_need_peak, play_drawn_peak, play_prot_cut, play_frames, play_frames_dropping, play_drop_max;   /* scene 3 only */
	uint32_t tex_count_peak, bake_kept, bake_bytes, bake_kept_peak, bake_bytes_peak, bake_refused, os_free_min;                                   /* the one texture upload buffer */
} n7_stats_t;
extern n7_stats_t n7_stats;
#ifdef __cplusplus
}
#endif
