/* nano7 PvZ device layer on the iPod nano 7G (ARM state, VFPv3-D16 softfp): files, time, log, sound and the C
 * library glue, on the nano7kit SDK. Data folder /Apps/Data/PvZ. Part of the nano7kit PvZ port. */
#include <reent.h>
#include <sys/stat.h>
#include <time.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../n7alloc.h"
#include "hb_sdk.h"
#include "hb_heap.h"
#include "n7dev.h"
#define DATA_DIR "/Apps/Data/PvZ"
#define SLACK 8192u   /* hb_fs_read may fill whole sectors past the end */

/* ---- time ---- */
static uint32_t t_last, t_high, t0_ms;
uint64_t n7p_us(void)
{
	uint32_t now = hb_time_uptime_us();
	if (now < t_last) t_high++;
	t_last = now;
	return (uint64_t)t_high << 32 | now;
}
uint32_t n7p_ms(void) { return (uint32_t)(n7p_us() / 1000u) - t0_ms; }

/* ---- log: in memory (inside the app image, freed with it), written to log.txt by n7p_log_flush ---- */
#define LOG_CAP 16384u
static char log_store[LOG_CAP + SLACK] __attribute__((aligned(64)));
static uint32_t log_len, log_dirty;
void n7p_log(const char* line)
{
	uint32_t n = (uint32_t)strlen(line);
	if (n > 600) n = 600;
	if (log_len + n + 1 > LOG_CAP) {   /* keep the first half (start-up), drop the oldest of the rest */
		uint32_t keep = LOG_CAP / 2, cut = log_len - keep < n + 1 + 1024 ? log_len - keep : n + 1 + 1024;
		memmove(log_store + keep, log_store + keep + cut, log_len - keep - cut);
		log_len -= cut;
	}
	memcpy(log_store + log_len, line, n); log_len += n; log_store[log_len++] = '\n';
	log_dirty = 1;
}
static void write_sync(void);
void n7p_log_flush(void)
{
	if (!log_dirty) return;
	hb_fs_mkdir(DATA_DIR);
	hb_fs_write(DATA_DIR "/log.txt", log_store, log_len);
	log_dirty = 0;
	write_sync();
}
/* Breadcrumbs (app/pvz_crumb.c): log.txt rewritten whole even if nothing changed, then sync.txt after it, so a
 * reboot right after a step loses sync.txt rather than log.txt (on this iPod the file written last before a reboot
 * can come back stale or empty). sync.txt holds the whole last log line, and every log write (crumbs and the
 * once-a-second flush) writes it, so the two match unless the reboot came between the two writes. If sync.txt is
 * one behind log.txt, the reboot came within a few ms of log.txt's last line. No printf here (it saves VFP registers). */
static char sync_store[700 + SLACK] __attribute__((aligned(64)));
static void write_sync(void)
{
	uint32_t e = log_len, b;
	while (e && log_store[e - 1] == '\n') e--;
	b = e; while (b && log_store[b - 1] != '\n') b--;
	uint32_t k = e - b; if (k > 690) k = 690;
	memcpy(sync_store, log_store + b, k); sync_store[k++] = '\n';
	hb_fs_write(DATA_DIR "/sync.txt", sync_store, k);
}
void n7p_log_sync(unsigned step)
{
	(void)step;
	hb_fs_mkdir("/Apps/Data");
	hb_fs_mkdir(DATA_DIR);
	hb_fs_write(DATA_DIR "/log.txt", log_store, log_len);
	log_dirty = 0;
	write_sync();
}
const char* n7p_data_dir(void) { return DATA_DIR; }

/* ---- files: whole-file reads only ---- */
/* lookups of files that were never shipped are answered from files.lst. Every shipped file is answered from
 * it too (where it is in the d/XX/HHHHHHHH layout, its size, a texture's header), so a read is one OS call in a small
 * folder; see n7index.c. Paths it does not cover (userdata/, the root's .txt files) go to the OS as before. */
static uint32_t fs_calls, min_sp = 0xFFFFFFFFu;
uint32_t n7p_fs_calls(void) { return fs_calls; }
int n7p_mkdir_abs(const char* p) { fs_calls++; return hb_fs_mkdir(p) ? 0 : -1; }
uint32_t n7p_min_sp(void) { return min_sp; }
static void note_sp(void) { uint32_t sp; __asm__ volatile("mov %0, sp" : "=r"(sp)); if (sp < min_sp) min_sp = sp; }
void n7p_note_sp(void) { note_sp(); }   /* also called from the deepest draw leaves (BltTriangles, polygon clip) */
static void idx_load(void)
{
	const char* l = DATA_DIR "/files.lst";
	fs_calls += 3;
	uint32_t sz = hb_fs_exists(l) ? hb_fs_size(l) : 0;
	char* b = sz ? (char*)malloc(sz + SLACK) : 0;
	uint32_t got = b ? hb_fs_read(l, b, sz + SLACK) : 0;
	if (got > sz) got = sz;
	n7x_build(b, got); free(b);
	char m[120]; snprintf(m, sizeof m, "nano7 file list: %u names%s%s", (unsigned)n7x_count(), n7x_state() == 1 ? "" : " (not used)",
	                      n7x_hashed() ? ", d/XX layout" : "");
	n7p_log(m);
}
typedef struct { int r; uint32_t size; const uint16_t* hdr; char dev[160]; } n7res;
/* r: 0 missing, 1 file (dev, size), 2 folder */
static void resolve(const char* p, n7res* o)
{
	note_sp();
	if (!n7x_state()) idx_load();
	o->size = 0; o->hdr = 0; o->dev[0] = 0;
	o->r = n7x_find(p, DATA_DIR, o->dev, sizeof o->dev, &o->size, &o->hdr);
	if (o->r >= 0) return;
	fs_calls++;
	if (!hb_fs_exists(p)) { o->r = 0; return; }
	fs_calls++;
	if (hb_fs_is_dir(p)) { o->r = 2; return; }
	fs_calls++;
	o->r = 1; o->size = hb_fs_size(p); snprintf(o->dev, sizeof o->dev, "%s", p);
}
static unsigned misses;
static void log_miss(const char* p)
{
	char m[190]; misses++;
	if (misses <= 40) { snprintf(m, sizeof m, "nano7 miss %s", p); n7p_log(m); }
	else if (!(misses & (misses - 1))) { snprintf(m, sizeof m, "nano7 misses so far: %u (only the first 40 are listed; answered from files.lst, no OS call)", misses); n7p_log(m); }
}
static int path_of(char* out, size_t n, const char* rel) { return snprintf(out, n, DATA_DIR "/%s", rel) < (int)n; }
int n7p_read_into(const char* rel, void* buf, uint32_t max)
{
	char p[160]; n7res r;
	if (!path_of(p, sizeof p, rel)) return -1;
	resolve(p, &r); if (r.r != 1) return -1;
	fs_calls++;
	uint32_t got = hb_fs_read(r.dev, buf, max + SLACK < max ? max : max + SLACK);   /* the caller's buffer has the slack */
	if (got > r.size) got = r.size;
	return (int)(got < max ? got : max);
}
/* the first bytes of a file: a .n7t header comes from files.lst (no OS call), anything else is one read */
int n7p_read_head(const char* rel, void* buf, uint32_t n)
{
	char p[160]; n7res r;
	if (!path_of(p, sizeof p, rel)) return -1;
	resolve(p, &r); if (r.r != 1) return -1;
	if (r.hdr) return n7x_head(r.hdr, buf, n);
	fs_calls++;
	uint32_t got = hb_fs_read(r.dev, buf, n);
	return got ? (int)(got < n ? got : n) : -1;
}
uint32_t n7p_size(const char* rel) { char p[160]; n7res r; if (!path_of(p, sizeof p, rel)) return 0; resolve(p, &r); return r.r == 1 ? r.size : 0; }
int n7p_exists_rel(const char* rel) { char p[160]; n7res r; if (!path_of(p, sizeof p, rel)) return 0; resolve(p, &r); return r.r != 0; }
int n7p_write_whole(const char* rel, const void* d, uint32_t n)
{
	char p[160]; if (!path_of(p, sizeof p, rel)) return -1;
	/* make the folders on the way (userdata/...) */
	for (char* s = p + sizeof(DATA_DIR); *s; s++) if (*s == '/') { *s = 0; hb_fs_mkdir(p); *s = '/'; }
	return hb_fs_write(p, d, n) ? 0 : -1;
}
void n7p_present(void) {}   /* the resident shows the GL frame when the frame callback returns */
int n7p_wav_play(const char* rel, uint32_t vol)
{
	char p[96];   /* hb_audio_play_wav: path at most 95 characters */
	if (snprintf(p, sizeof p, DATA_DIR "/%s", rel) >= (int)sizeof p) return 0;
	return hb_audio_play_wav(p, vol) ? 1 : 0;
}
uint32_t n7p_os_free(void) { return hb_os_heap_free(); }
uint32_t n7p_os_largest(void) { return hb_os_heap_largest(); }
void n7p_time_reset(void) { t_last = t_high = 0; t0_ms = 0; t0_ms = n7p_ms(); log_len = log_dirty = 0; }

/* ---- C library glue (newlib): malloc lives in n7alloc.c (the arena); no sbrk, no files through stdio ---- */
void* _malloc_r(struct _reent* r, size_t n) { (void)r; return malloc(n); }
void _free_r(struct _reent* r, void* p) { (void)r; free(p); }
void* _calloc_r(struct _reent* r, size_t a, size_t b) { (void)r; return calloc(a, b); }
void* _realloc_r(struct _reent* r, void* p, size_t n) { (void)r; return realloc(p, n); }
void* _memalign_r(struct _reent* r, size_t a, size_t n) { (void)r; extern void* memalign(size_t, size_t); return memalign(a, n); }
void* _sbrk(int n) { (void)n; return (void*)-1; }
int _isatty(int f) { (void)f; return 0; }
int _getpid(void) { return 1; }
int _kill(int p, int s) { (void)p; (void)s; return -1; }
int _link(const char* a, const char* b) { (void)a; (void)b; return -1; }
int _gettimeofday(void* tv, void* tz) { (void)tz; if (tv) { uint64_t u = n7p_us(); ((long*)tv)[0] = (long)(u / 1000000u); ((long*)tv)[1] = (long)(u % 1000000u); } return 0; }
int _times(void* t) { (void)t; return -1; }

void _exit(int c) { (void)c; n7_fatal("exit called"); for (;;) {} }
void abort(void)
{
	/* say why when the memory arena ran out (an uncaught std::bad_alloc ends here) */
	n7_arena_stats_t a; n7_arena_stats(&a);
	if (a.fails) {
		static char m[200];
		snprintf(m, sizeof m, "out of memory: the game arena is full (%u of %u used, a %u-byte request failed): stopped so it does not reboot",
			(unsigned)a.used, (unsigned)a.size, (unsigned)a.biggest_fail);
		n7_fatal(m);
	}
	n7_fatal("abort called");
	for (;;) {}
}
int nanosleep(const struct timespec* a, struct timespec* b) { (void)a; (void)b; return 0; }
int sched_yield(void) { return 0; }
/* the dry-run bot is not on the device */

/* ---- stdio / iostream / std::filesystem on whole files: the engine's saves (userdata/), its registry file and
 * log go through fopen/ofstream. A file opened for reading is read whole into memory; one opened for writing is
 * collected in memory and written whole by hb_fs_write when it is closed. At most N7_FDS at once. ---- */
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#define N7_FDS 6
typedef struct { int used, wr; char path[128]; uint8_t* d; uint32_t n, cap, pos; } n7fd;
static n7fd fds[N7_FDS];
/* the engine's relative paths are relative to the data folder (its working folder) */
static const char* n7_abs(const char* p, char* buf, size_t n)
{
	if (p[0] == '/') return p;
	while (p[0] == '.' && p[1] == '/') p += 2;
	snprintf(buf, n, "%s/%s", DATA_DIR, p);
	return buf;
}
int _open(const char* p, int fl, ...)
{
	char ab[160]; p = n7_abs(p, ab, sizeof ab);
	int i; for (i = 0; i < N7_FDS && fds[i].used; i++) {}
	if (i == N7_FDS || strlen(p) >= sizeof fds[0].path) { errno = EMFILE; return -1; }
	n7fd* f = &fds[i]; memset(f, 0, sizeof *f);
	f->wr = (fl & O_ACCMODE) != O_RDONLY;
	strcpy(f->path, p);
	if (!f->wr || !(fl & O_TRUNC)) {
		n7res r; resolve(p, &r);
		if (r.r != 1) {
			if (!f->wr || !(fl & O_CREAT)) {   /* reads of missing files are in log.txt */
				if (!f->wr && r.r == 0) log_miss(p);
				errno = ENOENT; return -1;
			}
		} else {
			f->d = (uint8_t*)malloc(r.size + SLACK); if (!f->d) { errno = ENOMEM; return -1; }
			fs_calls++;
			f->n = hb_fs_read(r.dev, f->d, r.size + SLACK); if (f->n > r.size) f->n = r.size; f->cap = r.size + SLACK;
		}
	}
	if (fl & O_APPEND) f->pos = f->n;
	f->used = 1;
	return i + 3;
}
static n7fd* fd_of(int f) { return f >= 3 && f < 3 + N7_FDS && fds[f - 3].used ? &fds[f - 3] : 0; }
int _read(int fd, void* b, int n)
{
	n7fd* f = fd_of(fd); if (!f) return 0;
	uint32_t k = f->pos < f->n ? f->n - f->pos : 0; if ((uint32_t)n < k) k = (uint32_t)n;
	memcpy(b, f->d + f->pos, k); f->pos += k; return (int)k;
}
int _write(int fd, const void* b, int n)
{
	n7fd* f = fd_of(fd); if (!f) return n;   /* stdout/stderr: dropped */
	if (!f->wr) { errno = EBADF; return -1; }
	if (f->pos + (uint32_t)n > f->cap) {
		uint32_t c = (f->pos + (uint32_t)n) * 2 + 256; uint8_t* d = (uint8_t*)realloc(f->d, c);
		if (!d) { errno = ENOSPC; return -1; } f->d = d; f->cap = c;
	}
	memcpy(f->d + f->pos, b, (size_t)n); f->pos += (uint32_t)n; if (f->pos > f->n) f->n = f->pos;
	return n;
}
int _close(int fd)
{
	n7fd* f = fd_of(fd); if (!f) return -1;
	int r = 0;
	if (f->wr) { char m[180]; r = hb_fs_write(f->path, f->d ? f->d : (const uint8_t*)"", f->n) ? 0 : -1;
		snprintf(m, sizeof m, "nano7 wrote %s (%u bytes)%s", f->path, (unsigned)f->n, r ? " FAILED" : ""); n7p_log(m); }
	free(f->d); memset(f, 0, sizeof *f);
	return r;
}
int _lseek(int fd, int o, int w)
{
	n7fd* f = fd_of(fd); if (!f) return -1;
	long p = w == SEEK_SET ? o : w == SEEK_CUR ? (long)f->pos + o : (long)f->n + o;
	if (p < 0) { errno = EINVAL; return -1; }
	f->pos = (uint32_t)p; return (int)p;
}
int _fstat(int fd, struct stat* st)
{
	memset(st, 0, sizeof *st);
	n7fd* f = fd_of(fd); if (!f) { st->st_mode = S_IFCHR; return 0; }
	st->st_mode = S_IFREG | 0644; st->st_size = f->n; return 0;
}
int _stat(const char* p, struct stat* st)
{
	char ab[160]; n7res r; p = n7_abs(p, ab, sizeof ab);
	memset(st, 0, sizeof *st);
	resolve(p, &r);
	if (!r.r) { errno = ENOENT; return -1; }
	if (r.r == 2) st->st_mode = S_IFDIR | 0755; else { st->st_mode = S_IFREG | 0644; st->st_size = r.size; }
	return 0;
}
int stat(const char* p, struct stat* st) { return _stat(p, st); }
int lstat(const char* p, struct stat* st) { return _stat(p, st); }
int _unlink(const char* p) { char ab[160]; p = n7_abs(p, ab, sizeof ab); return hb_fs_unlink(p) ? 0 : -1; }
int unlink(const char* p) { return _unlink(p); }
int rmdir(const char* p) { char ab[160]; p = n7_abs(p, ab, sizeof ab); return hb_fs_rmdir(p) ? 0 : -1; }
int access(const char* p, int m) { char ab[160]; n7res r; (void)m; p = n7_abs(p, ab, sizeof ab); resolve(p, &r); return r.r ? 0 : -1; }
char* getcwd(char* b, size_t n) { if (b && n > sizeof DATA_DIR) { strcpy(b, DATA_DIR); return b; } errno = ERANGE; return 0; }
int chdir(const char* p) { (void)p; return 0; }
int mkdir(const char* p, mode_t m) { char ab[160]; (void)m; p = n7_abs(p, ab, sizeof ab); return hb_fs_mkdir(p) ? 0 : -1; }
/* fcaseopen: the iPod's FAT file system ignores case already */
FILE* fcaseopen(const char* p, const char* m) { return fopen(p, m); }
FILE* fcaseopenat(const char* base, const char* p, const char* m)
{
	char b[200];
	if (p[0] == '/' || !base || !base[0]) return fopen(p, m);
	snprintf(b, sizeof b, "%s/%s", base, p);
	return fopen(b, m);
}
