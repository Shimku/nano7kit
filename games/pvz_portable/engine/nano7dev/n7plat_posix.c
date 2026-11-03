/* nano7 PvZ dry run (x86 Linux, never shipped): the n7p platform on posix, a virtual clock (so a run is repeatable and
 * fast), the 3.5 MB arena from mmap (outside the arena: nothing else), and the frame loop the iPod's OS would drive.
 * Environment: NANO7_DATA (data folder = out/nano/PvZ), NANO7_STEP_MS (virtual ms per frame, default 40),
 * NANO7_FRAMES (stop after), NANO7_SHOTS (folder for screenshots), NANO7_OS_FREE (pretend OS free bytes). */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include "n7dev.h"
#include "n7alloc.h"

static uint64_t s_vms;   /* virtual clock, microseconds */
uint32_t n7p_ms(void) { return (uint32_t)(s_vms / 1000); }
uint64_t n7p_us(void) { return s_vms; }
static char s_log[65536]; static size_t s_logn;
extern unsigned n7h_calls[9];
void n7p_log(const char* line)
{
	fprintf(stderr, "%s\n", line);
	if (!strncmp(line, "LOADING:", 8) || !strncmp(line, "LOADED:", 7) || !strncmp(line, "nano7kit step", 13)) {
		fprintf(stderr, "  fs r%u i%u p%u c%u s%u d%u pr%u o%u hashed%u\n", n7h_calls[0], n7h_calls[1], n7h_calls[2], n7h_calls[3], n7h_calls[4], n7h_calls[5], n7h_calls[6], n7h_calls[7], n7h_calls[8]);
		memset(n7h_calls, 0, sizeof n7h_calls);
	}
	size_t n = strlen(line);
	if (s_logn + n + 2 > sizeof s_log) n7p_log_flush();
	if (n + 2 > sizeof s_log) return;
	memcpy(s_log + s_logn, line, n); s_logn += n; s_log[s_logn++] = '\n';
}
static char s_dir[512];
const char* n7p_data_dir(void) { return s_dir; }
int n7p_mkdir_abs(const char* p) { return mkdir(p, 0755) == 0 || access(p, F_OK) == 0 ? 0 : -1; }
void n7p_log_flush(void)
{
	if (!s_logn) return;
	char p[600]; snprintf(p, sizeof p, "%s/log.txt", s_dir);
	FILE* f = fopen(p, "a"); if (f) { fwrite(s_log, 1, s_logn, f); fclose(f); }
	s_logn = 0;
}
static void full(const char* rel, char* p, size_t n) { snprintf(p, n, "%s/%s", s_dir, rel); }
/* like the iPod's hb_fs_read: reads up to max bytes; then scribbles up to 8 KB past what it read (sector slack), so a
 * caller that forgot the slack shows up here as heap corruption instead of on the device */
int n7p_read_into(const char* rel, void* buf, uint32_t max)
{
	char p[600]; full(rel, p, sizeof p);
	FILE* f = fopen(p, "rb"); if (!f) return -1;
	size_t got = fread(buf, 1, max, f); fclose(f);
	uint32_t slack = (uint32_t)((512 - (got & 511)) & 511);
	memset((char*)buf + got, 0xA5, slack);
	return (int)got;
}
int n7p_read_head(const char* rel, void* buf, uint32_t n)
{
	extern int n7h_head(const char*, void*, uint32_t);
	char p[600]; full(rel, p, sizeof p);
	int h = n7h_head(p, buf, n); if (h != -2) return h;
	return n7p_read_into(rel, buf, n);
}
uint32_t n7p_size(const char* rel) { char p[600]; full(rel, p, sizeof p); struct stat st; return stat(p, &st) ? 0 : (uint32_t)st.st_size; }
int n7p_exists_rel(const char* rel) { char p[600]; full(rel, p, sizeof p); return access(p, F_OK) == 0; }
int n7p_write_whole(const char* rel, const void* d, uint32_t n)
{ char p[600]; full(rel, p, sizeof p); FILE* f = fopen(p, "wb"); if (!f) return -1; size_t w = fwrite(d, 1, n, f); fclose(f); return w == n ? 0 : -1; }
extern int n7soft_frame(int raster);                  /* n7gl1_soft.c */
extern void n7soft_shot(const char* path);
static int s_raster_next; static char s_shot_next[600];
void n7p_present(void) {}
int n7p_wav_play(const char* path, uint32_t vol)
{
	char p[600]; snprintf(p, sizeof p, "%s/%s", s_dir, path);
	if (access(p, F_OK)) { static int miss; if (miss++ < 8) fprintf(stderr, "nano7 dry: missing sound %s\n", path); return 0; }
	return 1;
}
/* OS heap model. An iPod log lost about 30 KB of OS heap while ~41 baked textures were made (textures
 * themselves are not taken from it), so each live GL texture is charged NANO7_TEX_OS bytes (default 768, an estimate
 * from that one log) against NANO7_OS_FREE */
uint32_t n7p_os_free(void)
{
	const char* e = getenv("NANO7_OS_FREE");
	if (!e) return 0;
	uint32_t f = (uint32_t)strtoul(e, 0, 10);
	const char* k = getenv("NANO7_TEX_OS");
	uint32_t each = k ? (uint32_t)strtoul(k, 0, 10) : 768u, c = n7_stats.tex_count * each;
	return c < f ? f - c : 1;
}
uint32_t n7p_os_largest(void) { return n7p_os_free(); }
void* n7_host_block(size_t n)
{
	void* p = mmap(0, n, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	if (p == MAP_FAILED) { fprintf(stderr, "mmap arena failed\n"); exit(3); }
	return p;
}
char* n7_host_getenv(const char* n) { return getenv(n); }
void n7_fatal(const char* why)
{
	fprintf(stderr, "NANO7 FATAL: %s\n", why);
	n7p_log("nano7 fatal:"); n7p_log(why); n7p_log_flush();
	_exit(7);
}
/* the bot asks for a picture after each level */
void n7_dry_request_shot(const char* name)
{
	const char* d = getenv("NANO7_SHOTS"); if (!d) return;
	snprintf(s_shot_next, sizeof s_shot_next, "%s/%s.ppm", d, name); s_raster_next = 1;
}
int main(int argc, char** argv)
{
	const char* d = getenv("NANO7_DATA"); if (!d) { fprintf(stderr, "set NANO7_DATA to the repacked out/nano/PvZ folder\n"); return 2; }
	snprintf(s_dir, sizeof s_dir, "%s", d);
	uint64_t step = getenv("NANO7_STEP_MS") ? strtoull(getenv("NANO7_STEP_MS"), 0, 10) * 1000 : 40000;
	long frames = getenv("NANO7_FRAMES") ? atol(getenv("NANO7_FRAMES")) : 0;
	int every = getenv("NANO7_RASTER_EVERY") ? atoi(getenv("NANO7_RASTER_EVERY")) : 0;
	uint64_t shot_us = getenv("NANO7_SHOT_SEC") ? strtoull(getenv("NANO7_SHOT_SEC"), 0, 10) * 1000000 : 0, next_shot = shot_us;
	s_vms = 1000000;
	/* on the iPod the arena is the app's aux block; here it is created on the first malloc (static constructors) */
	if (n7_init(0, 0)) { fprintf(stderr, "init failed\n"); n7p_log_flush(); return 1; }
	struct timespec t0, t1; clock_gettime(CLOCK_MONOTONIC, &t0);
	for (long f = 0; !frames || f < frames; f++) {
		if (shot_us && s_vms >= next_shot && !s_shot_next[0] && getenv("NANO7_SHOTS")) {
			snprintf(s_shot_next, sizeof s_shot_next, "%s/t%05u.ppm", getenv("NANO7_SHOTS"), (unsigned)(s_vms / 1000000)); s_raster_next = 1; next_shot += shot_us; }
		int raster = s_raster_next || (every && f % every == 0);
		n7soft_frame(raster);
		s_vms += step;
		extern unsigned n7h_tot[9]; unsigned before[9]; memcpy(before, n7h_tot, sizeof before);
		int quit = n7_frame();
		{   /* this callback's file time on the iPod, from its OS calls. ms per call measured on hardware for the
			   old folders (reanim/ 1,977 long names ~200, images/ ~18, compiled/ and the rest ~11); the d/XX layout is an
			   estimate (64-entry and ~50-entry folders of 8.3 names: NANO7_DXX_MS, default 8) */
			static unsigned ms[9] = { 200, 18, 11, 11, 11, 11, 11, 11, 8 };
			if (getenv("NANO7_DXX_MS")) ms[8] = (unsigned)atoi(getenv("NANO7_DXX_MS"));
			unsigned est = 0, d[9]; for (int k = 0; k < 9; k++) { d[k] = n7h_tot[k] - before[k]; est += d[k] * ms[k]; }
			static unsigned long long total; total += est;
			if (est >= 500) fprintf(stderr, "nano7 dry long callback: frame %ld, about %u ms of file calls on the iPod (r%u i%u p%u c%u s%u d%u pr%u o%u dxx%u), %llu ms so far\n", f, est, d[0], d[1], d[2], d[3], d[4], d[5], d[6], d[7], d[8], total);
		}
		if (quit) break;
		if (raster && s_shot_next[0]) { n7soft_shot(s_shot_next); s_shot_next[0] = 0; s_raster_next = 0; }
	}
	clock_gettime(CLOCK_MONOTONIC, &t1);
	extern void n7_log_mem(const char*); n7_log_mem("end of dry run");
	extern void n7_tri_dump(void); n7_tri_dump();
	fprintf(stderr, "dry run: %.1f s wall, %.1f s virtual\n", (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) / 1e9, s_vms / 1e6);
	n7p_log_flush();
	return 0;
}

/* breadcrumbs: on the iPod they are written to log.txt at once (app/pvz_crumb.c); here they are only logged */
void n7p_step_a(const char* what, uint32_t a)
{
	char m[200]; snprintf(m, sizeof m, "nano7kit step %s: a=%08x", what, (unsigned)a); n7p_log(m);
}
void n7p_step(const char* what) { n7p_step_a(what, 0); }

extern unsigned n7h_tot[9];
/* the device tracks the lowest sp; the dry run's stack is the host's, so these only keep the same log lines */
void n7p_note_sp(void) {}
uint32_t n7p_min_sp(void) { return 0; }
uint32_t n7p_fs_calls(void) { unsigned t = 0; for (int i = 0; i < 9; i++) t += n7h_tot[i]; return t; }
