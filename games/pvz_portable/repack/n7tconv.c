/* nano7 texture converter: decodes the user's own PNG/JPG/GIF (colour + optional alpha file),
 * scales by a factor with premultiplied area filtering and writes a 16-bit .n7t texture.
 * Usage: n7tconv SCALE OUT.n7t COLOR|- [ALPHA|-] [alphaonly-basecolor-hex]
 * .n7t: "N7T1", u16 logical w, h, u16 tex w, h, u8 fmt (1=RGB565 2=RGBA5551 3=RGBA4444), u8 0, u16 0, data (tex w*h*2, LE). */
#define STB_IMAGE_IMPLEMENTATION
#define STBI_NO_STDIO_FALLBACK
#include "stb_image.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <math.h>

static unsigned char* load(const char* p, int* w, int* h) { int c; return stbi_load(p, w, h, &c, 4); }

int main(int argc, char** argv)
{
	if (argc < 4) { fprintf(stderr, "usage\n"); return 2; }
	double k = atof(argv[1]);
	const char* out = argv[2]; const char* cp = argv[3]; const char* ap = argc > 4 ? argv[4] : "-";
	unsigned base = argc > 5 ? (unsigned)strtoul(argv[5], 0, 16) : 0xFFFFFF;
	int w = 0, h = 0, aw = 0, ah = 0;
	unsigned char* px = strcmp(cp, "-") ? load(cp, &w, &h) : NULL;
	unsigned char* al = strcmp(ap, "-") ? load(ap, &aw, &ah) : NULL;
	if (!px && !al) { fprintf(stderr, "n7tconv: cannot decode %s\n", cp); return 1; }
	if (!px) {   /* alpha-only image: base colour + alpha from the red channel, like ImageLib::ApplyAlphaAsImage */
		w = aw; h = ah; px = malloc((size_t)w * h * 4);
		for (int i = 0; i < w * h; i++) { px[i*4] = base >> 16; px[i*4+1] = base >> 8; px[i*4+2] = base; px[i*4+3] = al[i*4]; }
	} else if (al && aw == w && ah == h) {
		for (int i = 0; i < w * h; i++) px[i*4+3] = al[i*4];   /* ComposeAlpha: alpha = red of the alpha image */
	}
	int tw = (int)ceil(w * k), th = (int)ceil(h * k); if (tw < 1) tw = 1; if (th < 1) th = 1;
	float* acc = calloc((size_t)tw * th * 5, sizeof(float));
	/* area filter: each source pixel lands in one destination cell (nearest cell), premultiplied */
	for (int y = 0; y < h; y++) { int dy = (int)(y * (double)th / h); if (dy >= th) dy = th - 1;
		for (int x = 0; x < w; x++) { int dx = (int)(x * (double)tw / w); if (dx >= tw) dx = tw - 1;
			unsigned char* s = px + ((size_t)y * w + x) * 4; float a = s[3] / 255.0f; float* d = acc + ((size_t)dy * tw + dx) * 5;
			d[0] += s[0] * a; d[1] += s[1] * a; d[2] += s[2] * a; d[3] += a; d[4] += 1; } }
	int opaque = 1, binary = 1;
	uint8_t* rgba = malloc((size_t)tw * th * 4);
	for (int i = 0; i < tw * th; i++) { float* d = acc + (size_t)i * 5; float a = d[4] ? d[3] / d[4] : 0;
		uint8_t* o = rgba + (size_t)i * 4;
		if (d[3] > 0) { o[0] = (uint8_t)fminf(255, d[0] / d[3] + 0.5f); o[1] = (uint8_t)fminf(255, d[1] / d[3] + 0.5f); o[2] = (uint8_t)fminf(255, d[2] / d[3] + 0.5f); } else o[0] = o[1] = o[2] = 0;
		o[3] = (uint8_t)(a * 255 + 0.5f);
		if (o[3] != 255) opaque = 0; if (o[3] != 0 && o[3] != 255) binary = 0; }
	int fmt = opaque ? 1 : binary ? 2 : 3;
	FILE* f = fopen(out, "wb"); if (!f) return 1;
	uint16_t hdr[6] = { (uint16_t)w, (uint16_t)h, (uint16_t)tw, (uint16_t)th, (uint16_t)fmt, 0 };
	fwrite("N7T1", 1, 4, f); fwrite(hdr, 2, 6, f);
	for (int i = 0; i < tw * th; i++) { uint8_t* o = rgba + (size_t)i * 4; uint16_t v;
		if (fmt == 1) v = (uint16_t)(((o[0] >> 3) << 11) | ((o[1] >> 2) << 5) | (o[2] >> 3));
		else if (fmt == 2) v = (uint16_t)(((o[0] >> 3) << 11) | ((o[1] >> 3) << 6) | ((o[2] >> 3) << 1) | (o[3] >> 7));
		else v = (uint16_t)(((o[0] >> 4) << 12) | ((o[1] >> 4) << 8) | ((o[2] >> 4) << 4) | (o[3] >> 4));
		fwrite(&v, 2, 1, f); }
	fclose(f);
	printf("%d %d %d %d %d\n", w, h, tw, th, fmt);
	return 0;
}
