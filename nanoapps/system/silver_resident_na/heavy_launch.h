/* nano7kit heavy-app launch helpers (superset on NanoApps 1.0).
 * MIT NanoApps resident remains the base; these gates keep large .hbapp
 * images (QuickJS, PvZ, Temple Run, …) launching reliably. */
#ifndef NANO7_HEAVY_LAUNCH_H
#define NANO7_HEAVY_LAUNCH_H
#include <stdint.h>

/* Minimum contiguous free block required before loading an image of
 * `image_need` bytes (arena + small margin). Returns 1 if OK. */
int hb_heavy_block_ok(uint32_t image_need);

/* Refuse reason codes for hb_boot_report / traces. */
enum {
    HB_HEAVY_OK = 0,
    HB_HEAVY_TOO_SMALL = 1,   /* largest free block too small */
    HB_HEAVY_READ_FAIL = 2,
    HB_HEAVY_BAD_IMAGE = 3
};

/* Chunked HRL1 load into a fresh OS-heap arena. Same contract as
 * hb_reloc_load_file_verified, but reads the file in CHUNK-sized pieces
 * so a single FS read never has to move the whole multi-megabyte image. */
void *hb_reloc_load_file_verified_chunked(const char *path, void **out_arena,
                                          uint32_t *out_len, uint32_t expected_hash);

/* Prefer /Apps/Data/<name> when present, else /Data/<name>. Writes the
 * chosen absolute directory into out (out_sz bytes). Returns 1 if at least
 * one root exists or was created. */
int hb_data_dir(char *out, uint32_t out_sz, const char *leaf);

#endif
