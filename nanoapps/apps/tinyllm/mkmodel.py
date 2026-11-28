#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Turn a llama2.c checkpoint and tokenizer into the Tiny LLM model file.

The model file is int8 weights with one float scale per row, the RMSNorm
weights, a RoPE table and the tokenizer, behind a 64-byte header with a
CRC-32 of everything after it. Standard library only, so it runs on a Mac
without numpy.

Usage: mkmodel.py CHECKPOINT.bin TOKENIZER.bin OUT.bin [--seq 256]
"""
from __future__ import annotations

import argparse
import math
import struct
import sys
import zlib
from array import array
from pathlib import Path

MAGIC = 0x314D4C54  # "TLM1"
VERSION = 1


def floats(blob: bytes, offset: int, count: int) -> tuple[array, int]:
    a = array("f")
    a.frombytes(blob[offset:offset + 4 * count])
    if sys.byteorder != "little":
        a.byteswap()
    if len(a) != count:
        raise SystemExit("checkpoint is shorter than its header says")
    return a, offset + 4 * count


def quant_rows(w: array, rows: int, cols: int) -> bytes:
    """Scales (float32 per row) then int8 values, row-major."""
    scales = array("f")
    q = bytearray(rows * cols)
    for r in range(rows):
        row = w[r * cols:(r + 1) * cols]
        peak = max(abs(v) for v in row)
        s = peak / 127.0 if peak > 0 else 1.0
        scales.append(s)
        inv = 1.0 / s
        base = r * cols
        for c, v in enumerate(row):
            n = int(round(v * inv))
            n = 127 if n > 127 else (-127 if n < -127 else n)
            q[base + c] = n & 0xFF
    if sys.byteorder != "little":
        scales.byteswap()
    return scales.tobytes() + bytes(q)


def le(a: array) -> bytes:
    if sys.byteorder != "little":
        a = array(a.typecode, a)
        a.byteswap()
    return a.tobytes()


def convert(ckpt: Path, tok: Path, seq: int) -> bytes:
    blob = ckpt.read_bytes()
    dim, hidden, layers, heads, kv_heads, vocab, ctx = struct.unpack_from("<7i", blob)
    shared = vocab > 0
    vocab = abs(vocab)
    hs = dim // heads
    kv_dim = dim * kv_heads // heads
    if seq > ctx:
        raise SystemExit(f"--seq {seq} is longer than the model's {ctx}")
    off = 28
    emb, off = floats(blob, off, vocab * dim)
    rms_att, off = floats(blob, off, layers * dim)
    wq, off = floats(blob, off, layers * dim * dim)
    wk, off = floats(blob, off, layers * dim * kv_dim)
    wv, off = floats(blob, off, layers * dim * kv_dim)
    wo, off = floats(blob, off, layers * dim * dim)
    rms_ffn, off = floats(blob, off, layers * dim)
    w1, off = floats(blob, off, layers * dim * hidden)
    w2, off = floats(blob, off, layers * hidden * dim)
    w3, off = floats(blob, off, layers * dim * hidden)
    rms_final, off = floats(blob, off, dim)
    if not shared:
        raise SystemExit("only checkpoints with a shared classifier are supported")
    for n in (dim, kv_dim, hidden):
        if n % 4:
            raise SystemExit("row lengths must be multiples of 4")

    out = bytearray()
    out += le(rms_att) + le(rms_ffn) + le(rms_final)
    cos_t, sin_t = array("f"), array("f")
    for pos in range(seq):
        for i in range(hs // 2):
            freq = 1.0 / (10000.0 ** ((2 * i) / hs))
            cos_t.append(math.cos(pos * freq))
            sin_t.append(math.sin(pos * freq))
    out += le(cos_t) + le(sin_t)
    out += quant_rows(emb, vocab, dim)

    def part(w, rows, cols, layer):
        n = rows * cols
        return quant_rows(w[layer * n:(layer + 1) * n], rows, cols)

    for layer in range(layers):
        out += part(wq, dim, dim, layer)
        out += part(wk, kv_dim, dim, layer)
        out += part(wv, kv_dim, dim, layer)
        out += part(wo, dim, dim, layer)
        out += part(w1, hidden, dim, layer)
        out += part(w2, dim, hidden, layer)
        out += part(w3, hidden, dim, layer)

    tb = tok.read_bytes()
    (max_len,) = struct.unpack_from("<i", tb)
    p = 4
    scores = array("f")
    strs = bytearray()
    for _ in range(vocab):
        score, n = struct.unpack_from("<fi", tb, p)
        p += 8
        piece = tb[p:p + n]
        p += n
        if n > 255:
            raise SystemExit("token longer than 255 bytes")
        scores.append(score)
        strs += bytes((n,)) + piece
    while len(strs) % 4:
        strs.append(0)
    tok_part = le(scores) + bytes(strs)
    out += tok_part

    head = struct.pack(
        "<16I", MAGIC, VERSION, dim, hidden, layers, heads, kv_heads, vocab, seq, max_len,
        len(out), zlib.crc32(out) & 0xFFFFFFFF, len(tok_part), 0, 0, 0,
    )
    return head + bytes(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("checkpoint")
    ap.add_argument("tokenizer")
    ap.add_argument("out")
    ap.add_argument("--seq", type=int, default=256, help="longest story in tokens (RoPE table rows)")
    a = ap.parse_args(argv)
    data = convert(Path(a.checkpoint), Path(a.tokenizer), a.seq)
    Path(a.out).write_bytes(data)
    print(f"wrote {a.out}: {len(data):,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
