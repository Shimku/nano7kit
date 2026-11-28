/*
 * tllm.h: a small llama2.c-style inference engine for the iPod nano 7G.
 *
 * Based on the forward pass, sampler and tokenizer in Andrej Karpathy's
 * llama2.c (MIT, github.com/karpathy/llama2.c), rewritten for a freestanding
 * Cortex-A5 build with soft float: int8 weights with one scale per row,
 * int8 activations for every matrix multiply, and fixed-point attention
 * (int16 key/value cache, integer softmax). No libc, no libm, no malloc;
 * everything lives in one arena the caller provides.
 */
#ifndef TLLM_H
#define TLLM_H

#include <stdint.h>

#define TLLM_MAGIC   0x314D4C54u  /* "TLM1" */
#define TLLM_VERSION 1u
#define TLLM_HEADER  64u

#define TLLM_MAX_LAYERS 8
#define TLLM_MAX_PROMPT 96   /* prompt tokens, BOS included */
#define TLLM_PIECE_MAX  32

/* tllm_check / tllm_open results */
enum {
    TLLM_OK = 0,
    TLLM_E_MAGIC = 1,     /* not a Tiny LLM model file */
    TLLM_E_VERSION = 2,
    TLLM_E_SHAPE = 3,     /* header numbers out of range */
    TLLM_E_SIZE = 4,      /* file length does not match the header */
    TLLM_E_CRC = 5,       /* payload damaged */
    TLLM_E_ARENA = 6,     /* arena too small */
    TLLM_E_TOKENS = 7,    /* tokenizer section damaged */
};

/* tllm_step results */
enum {
    TLLM_BUSY = 0,        /* part of a token was computed */
    TLLM_TOKEN = 1,       /* a token finished; see piece / is_prompt */
    TLLM_DONE = 2,        /* the story ended (BOS) or reached the context */
};

typedef struct {
    uint32_t dim, hidden, n_layers, n_heads, n_kv_heads, vocab, seq_len, max_tok_len;
    uint32_t payload, crc, tok_bytes;
    uint32_t file_bytes;    /* header + payload */
    uint32_t work_bytes;    /* run state, caches and tokenizer index */
} TllmInfo;

typedef struct {
    const float *s;         /* one scale per row */
    const int8_t *q;        /* rows * cols */
} TllmMat;

typedef struct {
    TllmInfo info;
    uint32_t head_size, kv_dim, kv_mul;
    /* weights (inside the file image) */
    const float *rms_att, *rms_ffn, *rms_final;
    const float *rope_cos, *rope_sin;
    TllmMat emb;
    TllmMat wq[TLLM_MAX_LAYERS], wk[TLLM_MAX_LAYERS], wv[TLLM_MAX_LAYERS], wo[TLLM_MAX_LAYERS];
    TllmMat w1[TLLM_MAX_LAYERS], w2[TLLM_MAX_LAYERS], w3[TLLM_MAX_LAYERS];
    /* tokenizer */
    const float *tok_score;
    const uint8_t *tok_blob;
    uint16_t *tok_off;      /* vocab offsets into tok_blob (length byte first) */
    uint16_t *tok_hash;     /* 1024 slots, token id + 1, 0 = empty */
    /* run state (inside the arena, after the file) */
    float *x, *xb, *xb2, *hb, *hb2, *q, *logits;
    int8_t *xq;
    int32_t *att;
    int16_t *kc, *vc;       /* [layer][pos][kv_dim] */
    uint32_t *cand;         /* top-p candidates: vocab entries */
    /* generation */
    int16_t prompt[TLLM_MAX_PROMPT];
    int n_prompt;
    int pos, token, stage;
    int limit;              /* stop after this many positions */
    uint64_t rng;
    float temp;
    int greedy;             /* temp 0: argmax */
    int topp_m;             /* top-p in thousandths; 0 or 1000 turns it off */
    /* last finished token */
    char piece[TLLM_PIECE_MAX];
    int is_prompt;
    int generated;          /* tokens sampled, prompt not counted */
    uint32_t chk;           /* rolling checksum of emitted tokens (tests) */
} Tllm;

/* Check a 64-byte header against the file length. Fills *info. */
int tllm_check(const void *header, uint32_t file_bytes, TllmInfo *info);

/* Bytes the arena needs: the file image plus the work area. */
uint32_t tllm_arena_bytes(const TllmInfo *info);

/* The whole file is at arena[0..file_bytes). Verifies the CRC, then lays
 * out the work area after it. */
int tllm_open(Tllm *t, uint8_t *arena, uint32_t arena_bytes, uint32_t file_bytes);

/* Start a story. prompt may be empty (the model starts from nothing).
 * temp 0 is greedy. limit caps the positions used (<= seq_len). */
void tllm_begin(Tllm *t, const char *prompt, uint64_t seed, float temp, float topp, int limit);

/* Do one slice of work (one layer, or the classifier and sampler). */
int tllm_step(Tllm *t);

uint32_t tllm_crc32(const uint8_t *p, uint32_t n, uint32_t crc);

#endif
