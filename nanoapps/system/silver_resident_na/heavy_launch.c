/* nano7kit heavy-app helpers – superset on NanoApps 1.0 (MIT base). */
#include "heavy_launch.h"
#include "hb_sdk.h"
#include "hb_heap.h"
#include "hb_reloc.h"

#define CHUNK_MARGIN (256u * 1024u)

__attribute__((used)) int hb_heavy_block_ok(uint32_t image_need)
{
    uint32_t need = image_need + CHUNK_MARGIN;
    if (need < image_need) need = 0xffffffffu;
    return hb_os_heap_largest() >= need ? 1 : 0;
}

__attribute__((used)) int hb_data_dir(char *out, uint32_t out_sz, const char *leaf)
{
    char a[96], b[96];
    uint32_t i, j;
    if (!out || out_sz < 16u || !leaf || !leaf[0]) return 0;
    i = 0;
    for (j = 0; "/Apps/Data/"[j] && i + 1u < sizeof a; j++) a[i++] = "/Apps/Data/"[j];
    for (j = 0; leaf[j] && i + 1u < sizeof a; j++) a[i++] = leaf[j];
    a[i] = 0;
    i = 0;
    for (j = 0; "/Data/"[j] && i + 1u < sizeof b; j++) b[i++] = "/Data/"[j];
    for (j = 0; leaf[j] && i + 1u < sizeof b; j++) b[i++] = leaf[j];
    b[i] = 0;
    if (hb_fs_is_dir(a)) {
        for (i = 0; a[i] && i + 1u < out_sz; i++) out[i] = a[i];
        out[i] = 0;
        return 1;
    }
    if (hb_fs_is_dir(b)) {
        for (i = 0; b[i] && i + 1u < out_sz; i++) out[i] = b[i];
        out[i] = 0;
        return 1;
    }
    hb_fs_mkdir("/Apps");
    hb_fs_mkdir("/Apps/Data");
    hb_fs_mkdir(a);
    for (i = 0; a[i] && i + 1u < out_sz; i++) out[i] = a[i];
    out[i] = 0;
    return hb_fs_is_dir(a) ? 1 : 0;
}

__attribute__((used)) void *hb_reloc_load_file_verified_chunked(const char *path, void **out_arena,
                                          uint32_t *out_len, uint32_t expected_hash)
{
    /* Shared loader already chunk-reads files over 512 KB and refuses when the
     * largest free block is too small. Keep this name for call sites / tests. */
    return hb_reloc_load_file_verified(path, out_arena, out_len, expected_hash);
}
