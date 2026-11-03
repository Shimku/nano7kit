/* nano7 PvZ: the shipped files, from Data/PvZ/files.lst (written by the repacker), as a table in the game's heap.
 * It first answered lookups of files that were never shipped. It also stores where each file is and what it is:
 *  - the iPod's FAT reads a folder from the start on every lookup, and reanim/ (1,977 long names) cost about 200 ms a
 *    call on hardware (a device log against the dry run's call counts; images/ ~18 ms, compiled/ ~11 ms). So the repacker
 *    stores every shipped file as d/XX/HHHHHHHH: an 8.3 name (one directory entry, no long name) in one of 64 small
 *    folders, where HHHHHHHH is the hash of the lower-case logical path and XX its low 6 bits. The engine still asks for
 *    "reanim/zombie_head.n7t"; the file layers map it here.
 *  - the size of every file, and the 16-byte header of every .n7t texture, so a read is one OS call (it was: exists, size
 *    and read, or four calls for fopen) and registering an image costs none.
 * Paths under userdata/ and the root's .txt files are written at run time and are never answered (-1: ask the OS). */
#include <stdint.h>
#include <string.h>
#include <strings.h>
#include <stdlib.h>
#include <stdio.h>
#include "n7dev.h"
typedef struct { uint32_t h, size; uint16_t hdr[6]; } n7x_e;   /* size 0xFFFFFFFF: a folder */
static n7x_e* ents;
static uint32_t count, salt;
static int state;   /* 0 not built, 1 built, -1 no list */
uint32_t n7x_hash(const char* s, size_t n, uint32_t seed)
{
	uint32_t h = 2166136261u ^ seed;
	for (size_t i = 0; i < n; i++) { unsigned char c = (unsigned char)s[i]; if (c >= 'A' && c <= 'Z') c += 32; if (c == '\\') c = '/'; h = (h ^ c) * 16777619u; }
	return h ? h : 1;
}
static int cmp(const void* a, const void* b) { uint32_t x = ((const n7x_e*)a)->h, y = ((const n7x_e*)b)->h; return x < y ? -1 : x > y; }
static const n7x_e* find(uint32_t h)
{
	uint32_t lo = 0, hi = count;
	while (lo < hi) { uint32_t m = (lo + hi) / 2; if (ents[m].h == h) return &ents[m]; if (ents[m].h < h) lo = m + 1; else hi = m; }
	return 0;
}
static uint32_t hexv(const char* s, int n) { uint32_t v = 0; for (int i = 0; i < n; i++) { char c = s[i]; v = v * 16 + (uint32_t)(c <= '9' ? c - '0' : (c | 32) - 'a' + 10); } return v; }
void n7x_build(const char* lst, uint32_t n)
{
	free(ents); ents = 0; count = 0; salt = 0; state = -1;
	if (!lst || !n) return;
	uint32_t cap = 64;   /* one entry per file and one per folder above it (deduplicated after the sort) */
	for (uint32_t i = 0, tab = 0; i < n; i++) {
		if (lst[i] == '\n') { cap++; tab = 0; } else if (lst[i] == '\t') tab = 1; else if (lst[i] == '/' && !tab) cap++;
	}
	if (!(ents = (n7x_e*)malloc(cap * sizeof *ents))) return;
	const char* p = lst; const char* end = lst + n;
	int layout = 0;
	while (p < end) {
		const char* e = p; while (e < end && *e != '\n') e++;
		if (*p == '#') {   /* "#n7x 2 salt=S": the hashed layout */
			const char* s = p; while (s + 5 < e && strncmp(s, "salt=", 5)) s++;
			if (s + 5 < e) salt = (uint32_t)strtoul(s + 5, 0, 10);
			if (e - p > 6 && !strncmp(p, "#n7x 2", 6)) layout = 2;
		} else if (e > p) {
			const char* t1 = p; while (t1 < e && *t1 != '\t') t1++;
			const char* t2 = t1 < e ? t1 + 1 : e; const char* t3 = t2; while (t3 < e && *t3 != '\t') t3++;
			const char* t4 = t3 < e ? t3 + 1 : e; while (t4 < e && *t4 != '\t') t4++;   /* past the sha column */
			n7x_e* x = &ents[count++]; memset(x, 0, sizeof *x);
			x->h = n7x_hash(p, (size_t)(t1 - p), salt);
			x->size = (uint32_t)strtoul(t2, 0, 10);
			if (t4 + 25 <= e && t4[0] == '\t') for (int k = 0; k < 6; k++) x->hdr[k] = (uint16_t)hexv(t4 + 1 + k * 4, 4);
			for (const char* q = p; q < t1; q++) if (*q == '/' && count < cap) {   /* its folders */
				n7x_e* d = &ents[count++]; memset(d, 0, sizeof *d); d->h = n7x_hash(p, (size_t)(q - p), salt); d->size = 0xFFFFFFFFu;
			}
		}
		p = e + 1;
	}
	qsort(ents, count, sizeof *ents, cmp);
	uint32_t o = 0;   /* folders repeat; a file and a folder never share a hash (the repacker checks) */
	for (uint32_t i = 0; i < count; i++) if (!o || ents[o - 1].h != ents[i].h) ents[o++] = ents[i];
	count = o;
	state = count ? (layout == 2 ? 2 : 1) : -1;
}
int n7x_state(void) { return state > 0 ? 1 : state; }
int n7x_hashed(void) { return state == 2; }
uint32_t n7x_count(void) { return count; }
/* abs -> logical path relative to the data folder (lower case not needed: the hash ignores case). 0 if not in scope */
static size_t rel_of(const char* abs, const char* data_dir, char* r, size_t rn)
{
	size_t dl = strlen(data_dir), o = 0;
	if (strncmp(abs, data_dir, dl) || (abs[dl] != '/' && abs[dl])) return 0;
	for (const char* s = abs + dl; *s; s++) {
		if (*s == '/' && (o == 0 || r[o - 1] == '/')) continue;
		if (*s == '.' && (o == 0 || r[o - 1] == '/') && (s[1] == '/' || !s[1])) { if (s[1]) s++; continue; }
		if (*s == '.' && s[1] == '.') return 0;
		if (o + 1 >= rn) return 0;
		r[o++] = *s == '\\' ? '/' : *s;
	}
	while (o && r[o - 1] == '/') o--;
	if (!o) return 0;
	r[o] = 0;
	if (!strncasecmp(r, "userdata", 8) && (r[8] == '/' || !r[8])) return 0;
	if (!strchr(r, '/') && o > 4 && !strcasecmp(r + o - 4, ".txt")) return 0;
	if (!strcasecmp(r, "files.lst")) return 0;
	return o;
}
/* -1 not answered (ask the OS with abs); 0 not shipped; 1 a shipped file (dev = where it is on the iPod, size, hdr
 * = the .n7t header words or 0); 2 a shipped folder */
int n7x_find(const char* abs, const char* data_dir, char* dev, size_t devn, uint32_t* size, const uint16_t** hdr)
{
	if (state <= 0) return -1;
	char r[220]; size_t n = rel_of(abs, data_dir, r, sizeof r);
	if (!n) return -1;
	uint32_t h = n7x_hash(r, n, salt);
	const n7x_e* x = find(h);
	if (!x) return 0;
	if (x->size == 0xFFFFFFFFu) return 2;
	if (size) *size = x->size;
	if (hdr) *hdr = x->hdr[0] || x->hdr[1] ? x->hdr : 0;
	if (dev) {
		if (state == 2) snprintf(dev, devn, "%s/d/%02X/%08X", data_dir, (unsigned)(h & 63u), (unsigned)h);
		else snprintf(dev, devn, "%s/%s", data_dir, r);
	}
	return 1;
}
int n7x_absent(const char* abs, const char* data_dir) { return n7x_find(abs, data_dir, 0, 0, 0, 0) == 0; }
/* the first n bytes of a .n7t from the table ("N7T1" + 6 words), or -1 */
int n7x_head(const uint16_t* hdr, void* buf, uint32_t n)
{
	if (!hdr || n > 16) return -1;
	uint8_t b[16] = { 'N', '7', 'T', '1' };
	for (int k = 0; k < 6; k++) { b[4 + k * 2] = (uint8_t)hdr[k]; b[5 + k * 2] = (uint8_t)(hdr[k] >> 8); }
	memcpy(buf, b, n);
	return (int)n;
}
