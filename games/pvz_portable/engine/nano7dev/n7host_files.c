/* x86 dry run only (never on the device): makes the host file layer behave like the iPod's (hb/n7plat_hb.c).
 *  - the working folder is the data folder; FAT ignores case, so a path missing as written is matched without case
 *  - files.lst answers first (n7index.c): shipped files are opened at their d/XX/HHHHHHHH place, unshipped ones are
 *    "nano7 miss" with no OS call, exactly as on the iPod
 *  - it counts the OS calls the iPod would make (n7h_tot, by folder) so the main loop can estimate each frame
 *    callback's file time on hardware (n7plat_posix.c)
 * Linked with -Wl,--wrap for fopen, access, opendir and musl's time64 stat. */
#include <stdio.h>
#include <string.h>
#include <strings.h>
#include <dirent.h>
#include <unistd.h>
#include <stdlib.h>
#include <errno.h>
#include <sys/stat.h>
#include "n7dev.h"
FILE* __real_fopen(const char*, const char*);
int __real_access(const char*, int);
DIR* __real_opendir(const char*);
int __real___stat_time64(const char*, struct stat*);
static int exists(const char* p) { struct stat st; return __real___stat_time64(p, &st) == 0; }
static int fold(const char* p, char* out, size_t n)
{
	size_t o = 0; const char* s = p;
	if (*s == '/') { out[o++] = '/'; s++; } else if (n > 2) { out[o++] = '.'; out[o++] = '/'; }
	out[o] = 0;
	while (*s) {
		const char* e = strchr(s, '/'); size_t k = e ? (size_t)(e - s) : strlen(s);
		char part[256]; if (k >= sizeof part) return 0; memcpy(part, s, k); part[k] = 0;
		if (k && strcmp(part, ".")) {
			char t[1024]; snprintf(t, sizeof t, "%s%s", out, part);
			if (!exists(t)) {
				DIR* d = __real_opendir(o ? out : "."); struct dirent* de; int hit = 0;
				if (!d) return 0;
				while ((de = readdir(d))) if (!strcasecmp(de->d_name, part)) { strcpy(part, de->d_name); hit = 1; break; }
				closedir(d); if (!hit) return 0;
			}
			int w = snprintf(out + o, n - o, "%s%s", part, e ? "/" : ""); if (w < 0 || (size_t)w >= n - o) return 0; o += (size_t)w;
		}
		if (!e) break; s = e + 1;
	}
	return 1;
}
static const char* host(const char* p, char* b, size_t n) { return exists(p) ? p : fold(p, b, n) ? b : p; }
static void setup(void)
{
	if (n7x_state()) return;
	const char* d = n7p_data_dir(); char l[1024]; snprintf(l, sizeof l, "%s/files.lst", d);
	if (chdir(d)) {}
	FILE* f = __real_fopen(l, "rb"); char* b = 0; long n = 0;
	if (f) { fseek(f, 0, SEEK_END); n = ftell(f); fseek(f, 0, SEEK_SET); b = malloc((size_t)n + 1); if (b && fread(b, 1, (size_t)n, f) != (size_t)n) n = 0; fclose(f); }
	n7x_build(b, (uint32_t)n); free(b);
	char m[120]; snprintf(m, sizeof m, "nano7 file list: %u names%s%s", (unsigned)n7x_count(), n7x_state() == 1 ? "" : " (not used)", n7x_hashed() ? ", d/XX layout" : "");
	n7p_log(m);
}
/* the iPod's OS calls, by folder of the logical path: 0 reanim 1 images 2 particles 3 compiled 4 sounds 5 data
 * 6 properties 7 other; 8 the d/XX layout (any logical folder) */
unsigned n7h_calls[9], n7h_tot[9];
int n7h_weight = 1;
static int folder_of(const char* p)
{
	static const char* const k[] = { "reanim/", "images/", "particles/", "compiled/", "sounds/", "data/", "properties/" };
	const char* d = n7p_data_dir(); size_t dl = strlen(d);
	if (!strncmp(p, d, dl)) { p += dl; while (*p == '/') p++; }
	while (p[0] == '.' && p[1] == '/') p += 2;
	for (int i = 0; i < 7; i++) if (!strncasecmp(p, k[i], strlen(k[i]))) return i;
	return 7;
}
static void count(int k, unsigned n) { n7h_calls[k] += n; n7h_tot[k] += n; }
typedef struct { int r; uint32_t size; const uint16_t* hdr; char dev[1024]; } hres;
static void resolve(const char* p, hres* o)
{
	char a[1024]; setup();
	if (p[0] != '/') { snprintf(a, sizeof a, "%s/%s", n7p_data_dir(), p); p = a; }
	o->size = 0; o->hdr = 0; o->dev[0] = 0;
	o->r = n7x_find(p, n7p_data_dir(), o->dev, sizeof o->dev, &o->size, &o->hdr);
	if (o->r >= 0) return;
	char b[1024]; const char* h = host(p, b, sizeof b); struct stat st;
	int k = folder_of(p);
	if (__real___stat_time64(h, &st)) { count(k, 1); o->r = 0; return; }
	if (S_ISDIR(st.st_mode)) { count(k, 2); o->r = 2; return; }
	count(k, 3); o->r = 1; o->size = (uint32_t)st.st_size; snprintf(o->dev, sizeof o->dev, "%s", h);
}
static void miss(const char* p) { char m[600]; snprintf(m, sizeof m, "nano7 miss %s", p); n7p_log(m); }
/* the dry run's n7p_read_head: a texture header from files.lst costs no call */
int n7h_head(const char* p, void* buf, uint32_t n)
{
	hres r; resolve(p, &r);
	if (r.r == 1 && r.hdr) return n7x_head(r.hdr, buf, n);
	return -2;
}
FILE* __wrap_fopen(const char* p, const char* m)
{
	if (m[0] != 'r') { char b[1024]; return __real_fopen(host(p, b, sizeof b), m); }
	hres r; resolve(p, &r);
	if (r.r != 1) { if (!r.r) miss(p); errno = ENOENT; return 0; }
	count(n7x_hashed() && strstr(r.dev, "/d/") ? 8 : folder_of(p), 1);
	return __real_fopen(r.dev, m);
}
int __wrap_access(const char* p, int m) { hres r; (void)m; resolve(p, &r); if (!r.r) { errno = ENOENT; return -1; } return 0; }
DIR* __wrap_opendir(const char* p) { char b[1024]; return __real_opendir(host(p, b, sizeof b)); }
int __wrap___stat_time64(const char* p, struct stat* st)
{
	hres r; resolve(p, &r);
	if (!r.r) { errno = ENOENT; return -1; }
	memset(st, 0, sizeof *st);
	if (r.r == 2) st->st_mode = S_IFDIR | 0755; else { st->st_mode = S_IFREG | 0644; st->st_size = r.size; }
	return 0;
}
