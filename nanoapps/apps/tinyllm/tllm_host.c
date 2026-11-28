/*
 * tllm_host.c: run the Tiny LLM engine on a computer, for tests.
 * Usage: tllm_host MODEL.bin [-i prompt] [-t temp] [-p topp] [-s seed] [-n limit]
 * Prints the story, then a line "TOKENS n CHK x".
 */
#include "tllm.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv)
{
    const char *prompt = "";
    float temp = 0.0f, topp = 0.9f;
    unsigned long long seed = 42;
    int limit = 256, i, rc, n = 0;
    FILE *f;
    long size;
    uint8_t *arena;
    TllmInfo info;
    static Tllm t;
    if (argc < 2) { fprintf(stderr, "usage: %s MODEL [-i prompt] [-t temp] [-p topp] [-s seed] [-n limit]\n", argv[0]); return 2; }
    for (i = 2; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "-i")) prompt = argv[i + 1];
        else if (!strcmp(argv[i], "-t")) temp = (float)atof(argv[i + 1]);
        else if (!strcmp(argv[i], "-p")) topp = (float)atof(argv[i + 1]);
        else if (!strcmp(argv[i], "-s")) seed = strtoull(argv[i + 1], 0, 10);
        else if (!strcmp(argv[i], "-n")) limit = atoi(argv[i + 1]);
    }
    f = fopen(argv[1], "rb");
    if (!f) { printf("ERROR missing\n"); return 3; }
    fseek(f, 0, SEEK_END);
    size = ftell(f);
    fseek(f, 0, SEEK_SET);
    arena = malloc((size_t)size + 64);
    if (fread(arena, 1, (size_t)size, f) != (size_t)size) { printf("ERROR read\n"); return 3; }
    fclose(f);
    if (size < 64) { printf("ERROR %d\n", TLLM_E_SIZE); return 4; }
    rc = tllm_check(arena, (uint32_t)size, &info);
    if (rc) { printf("ERROR %d\n", rc); return 4; }
    arena = realloc(arena, tllm_arena_bytes(&info));
    rc = tllm_open(&t, arena, tllm_arena_bytes(&info), (uint32_t)size);
    if (rc) { printf("ERROR %d\n", rc); return 4; }
    fprintf(stderr, "file %u bytes, arena %u bytes\n", info.file_bytes, tllm_arena_bytes(&info));
    tllm_begin(&t, prompt, seed, temp, topp, limit);
    for (;;) {
        int r = tllm_step(&t);
        if (r == TLLM_TOKEN) { fputs(t.piece, stdout); n++; }
        if (r == TLLM_DONE) break;
    }
    printf("\nTOKENS %d GEN %d CHK %08x\n", n, t.generated, t.chk);
    return 0;
}
