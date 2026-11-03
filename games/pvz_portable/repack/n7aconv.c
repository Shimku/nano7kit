/* nano7 sound converter: decodes one of the user's own .ogg sound effects (stb_vorbis, fetched at build time)
 * and writes a small mono 16-bit PCM WAV for the iPod's own WAV player.
 * Usage: n7aconv RATE IN.ogg OUT.wav */
#define STB_VORBIS_NO_PUSHDATA_API
#include "third_party/stb_vorbis.c"
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>

static void w32(FILE* f, uint32_t v) { fwrite(&v, 4, 1, f); }
static void w16(FILE* f, uint16_t v) { fwrite(&v, 2, 1, f); }

int main(int argc, char** argv)
{
	if (argc != 4) { fprintf(stderr, "usage: n7aconv RATE IN.ogg OUT.wav\n"); return 2; }
	int rate = atoi(argv[1]), ch = 0, sr = 0;
	short* pcm = NULL;
	int n = stb_vorbis_decode_filename(argv[2], &ch, &sr, &pcm);
	if (n < 0 || !pcm || ch < 1 || sr < 1) { fprintf(stderr, "decode failed: %s\n", argv[2]); return 1; }
	/* mono mix, then linear resample to RATE with a small box pre-filter when shrinking */
	int64_t outn = (int64_t)n * rate / sr; if (outn < 1) outn = 1;
	int16_t* out = (int16_t*)malloc((size_t)outn * 2);
	double step = (double)sr / rate;
	for (int64_t i = 0; i < outn; i++) {
		double a = i * step, b = a + step; int ia = (int)a, ib = (int)b; if (ib <= ia) ib = ia + 1; if (ib > n) ib = n;
		double acc = 0; int cnt = 0;
		for (int j = ia; j < ib; j++) { int s = 0; for (int c = 0; c < ch; c++) s += pcm[(size_t)j * ch + c]; acc += (double)s / ch; cnt++; }
		double v = cnt ? acc / cnt : 0; if (v > 32767) v = 32767; if (v < -32768) v = -32768;
		out[i] = (int16_t)v;
	}
	FILE* f = fopen(argv[3], "wb"); if (!f) return 1;
	uint32_t data = (uint32_t)outn * 2;
	fwrite("RIFF", 1, 4, f); w32(f, 36 + data); fwrite("WAVEfmt ", 1, 8, f); w32(f, 16);
	w16(f, 1); w16(f, 1); w32(f, rate); w32(f, rate * 2); w16(f, 2); w16(f, 16);
	fwrite("data", 1, 4, f); w32(f, data); fwrite(out, 2, (size_t)outn, f); fclose(f);
	printf("%d %d %lld\n", sr, ch, (long long)outn);
	free(out); free(pcm);
	return 0;
}
