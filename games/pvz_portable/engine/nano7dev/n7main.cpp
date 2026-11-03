/* nano7 PvZ device layer: start-up and the per-frame step, called by the iPod app (or the dry run's main loop).
 * Part of the nano7kit PvZ port; links against PvZ-Portable (LGPL-3.0-or-later, wszqkzqk). */
#include "LawnApp.h"
#include "Resources.h"
#include "PvzpLib/PvzpStringFile.h"
#include "widget/WidgetManager.h"
#include "Lawn/System/ProfileMgr.h"
#include "Lawn/System/PlayerInfo.h"
#include "n7dev.h"
#include "../nano7/n7bake.h"
void Nano7QuitFree(void* theApp);   /* n7r.cpp */
#include "n7alloc.h"
#include "n7gl1.h"
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <string>
using namespace Sexy;

extern "C" void n7sdl_push_mouse(int type, int x, int y);   /* n7sdl.c: 0 motion, 1 down, 2 up */
void Nano7PanelToGame(int px, int py, int* gx, int* gy);   /* n7gl_dev_b.inc */
#define N7_BYTES_PER_TRI 3000u   /* GL driver memory per triangle of a frame (Temple Run measurement) */
#define N7_TRI_MARGIN 0x40000u   /* 256 KB left to the OS beyond the triangles */
#define N7_TRI_MAX 600u          /* the default ceiling (514 measured safe on the device) */
#define N7_TRI_MIN 150u          /* below this a frame is hardly the game; the HUD then shows how short it is */
static uint8_t sBudgetBuf[16 + 8192];   /* hb_fs_read slack */
static int sState;   /* 0 not started, 1 running, 2 stopped */
static int sDown, sLastX, sLastY;
static uint32_t sFrames, sLastLog;

static void LogMem(const char* what)
{
	n7_arena_stats_t a; n7_arena_stats(&a);
	char m[480];
	snprintf(m, sizeof m, "nano7 mem %s: heap %u (peak %u, high-water %u of %u, %u blocks, %u failed, biggest failed %u), "
		"textures %u in %u (peak %u), tris %u (peak %u, needed peak %u, budget %u, %u frames trimmed, %u capped), draws peak %u, images n7t %u decoded %u (%u bytes), "
		"CPU pixels asked %u (%u bytes), files %u (%u KB, biggest %u), sounds %u played %u dropped, OS free %u largest %u",
		what, (unsigned)a.used, (unsigned)a.peak, (unsigned)a.high_water, (unsigned)a.size, (unsigned)a.blocks, (unsigned)a.fails,
		(unsigned)a.biggest_fail, n7_stats.tex_bytes, n7_stats.tex_count, n7_stats.tex_peak, n7_stats.tris_frame, n7_stats.tris_peak,
		n7_stats.tris_need_peak, n7_stats.tri_budget, n7_stats.frames_trimmed, n7_stats.frames_capped, n7_stats.draws_peak, n7_stats.img_n7t, n7_stats.img_decoded, n7_stats.img_decoded_bytes, n7_stats.bits_wanted,
		n7_stats.bits_wanted_bytes, n7_stats.file_opens, n7_stats.file_read_bytes >> 10, n7_stats.file_biggest,
		n7_stats.snd_played, n7_stats.snd_dropped, (unsigned)n7p_os_free(), (unsigned)n7p_os_largest());
	n7p_log(m);
}

/* saves: the engine's user files (userdata/userdata/user1.dat, users.dat) are written whole with one OS write
 * (n7p_write_whole: folders made on the way, then hb_fs_write), not through ofstream / std::filesystem / newlib
 * stdio, and every save and load is named in log.txt. Paths are the engine's (absolute under the data folder). */
static const char* N7Rel(const std::string& p)
{
	const char* d = n7p_data_dir(); size_t n = strlen(d);
	if (p.size() > n && !strncmp(p.c_str(), d, n) && p[n] == '/') return p.c_str() + n + 1;
	return nullptr;
}
/* the save and load lines also go to saves.txt (the last ~3 KB of them, rewritten with every save), because
 * log.txt keeps its first 8 KB and then only the last few seconds: the save lines had scrolled out of it. */
static char sSaveLog[3072]; static uint32_t sSaveLogN;
static void SaveLogAdd(const char* line)
{
	static bool sRead;
	if (!sRead) {   /* the earlier sessions' lines first (the newest ~1.5 KB of them), then a start marker */
		sRead = true;
		static char b[4096 + 64];
		int got = n7p_read_into("saves.txt", b, 4096);
		if (got > 0) {
			uint32_t k = got > 1536 ? (uint32_t)got - 1536 : 0;
			while (k && k < (uint32_t)got && b[k - 1] != '\n') k++;
			memcpy(sSaveLog, b + k, (uint32_t)got - k); sSaveLogN = (uint32_t)got - k;
		}
		const char* mk = "---- start ----\n";
		memcpy(sSaveLog + sSaveLogN, mk, strlen(mk)); sSaveLogN += (uint32_t)strlen(mk);
	}
	uint32_t n = (uint32_t)strlen(line); if (n > 300) n = 300;
	if (sSaveLogN + n + 1 > sizeof sSaveLog) {   /* drop the oldest lines */
		uint32_t cut = sSaveLogN + n + 1 - (uint32_t)sizeof sSaveLog, k = 0;
		while (k < sSaveLogN && (k < cut || sSaveLog[k - 1] != '\n')) k++;
		memmove(sSaveLog, sSaveLog + k, sSaveLogN - k); sSaveLogN -= k;
	}
	char t[24]; snprintf(t, sizeof t, "[%u s] ", (unsigned)(n7p_ms() / 1000));
	uint32_t tn = (uint32_t)strlen(t);
	if (sSaveLogN + tn + n + 1 <= sizeof sSaveLog) {
		memcpy(sSaveLog + sSaveLogN, t, tn); sSaveLogN += tn;
		memcpy(sSaveLog + sSaveLogN, line, n); sSaveLogN += n; sSaveLog[sSaveLogN++] = '\n';
	}
}
static void SaveLogWrite() { n7p_write_whole("saves.txt", sSaveLog, sSaveLogN); }
bool Nano7WriteFile(const std::string& thePath, const void* theData, unsigned long theLen)
{
	const char* rel = N7Rel(thePath);
	char m[240];
	if (!rel) { snprintf(m, sizeof m, "nano7 save %s: outside the data folder, not written", thePath.c_str()); n7p_log(m); SaveLogAdd(m); SaveLogWrite(); return false; }
	uint32_t t0 = n7p_ms();
	int r = n7p_write_whole(rel, theData, (uint32_t)theLen);   /* few OS calls: this runs inside one frame callback */
	snprintf(m, sizeof m, "nano7 save %s: %lu bytes %s (%u ms)", rel, theLen, r ? "FAILED" : "ok", (unsigned)(n7p_ms() - t0));
	n7p_log(m); SaveLogAdd(m); SaveLogWrite(); n7p_log_flush();
	return r == 0;
}
void Nano7MkDirs(const std::string& thePath)
{   /* the folders under the data folder only (the data folder itself exists: the app runs from it) */
	const char* rel = N7Rel(thePath);
	if (!rel) return;
	static std::string sDone;   /* the same folders again (every save asks): no OS calls */
	if (sDone == rel) return;
	sDone = rel;
	std::string r(rel), acc(n7p_data_dir());
	size_t a = 0;
	while (a < r.size()) {
		size_t b = r.find('/', a); if (b == std::string::npos) b = r.size();
		if (b > a) { acc += '/'; acc.append(r, a, b - a); n7p_mkdir_abs(acc.c_str()); }
		a = b + 1;
	}
}
void Nano7SaveNote(const char* what) { n7p_log(what); SaveLogAdd(what); }
static int sWatch;   /* frames still to get a crumb before and after */
void Nano7WatchFrames(const char* what, int a) { n7p_step_a(what, (uint32_t)a); sWatch = 20; }

static bool sFixed;              // budget.txt or NANO7_TRI_BUDGET
static uint32_t sFree0, sTex0;   // OS heap free and texture bytes right after start
static uint32_t BudgetFor(uint32_t f)
{   /* from the OS heap as measured, at most 600. An earlier build also took off the texture bytes loaded since start, but the
	   device logs show textures do not come out of that heap (free went up as the lawn loaded, and was 2.48 MB with
	   1.15 MB of textures), so it ran at 315. 514 triangles were drawn on the device without trouble; 600 is near
	   that, well under the Temple Run failure (1,550 at 3.87 MB free). calibrate.txt measures the real limit. */
	uint32_t k = f > N7_TRI_MARGIN ? (f - N7_TRI_MARGIN) / N7_BYTES_PER_TRI : 0;
	return k < N7_TRI_MIN ? N7_TRI_MIN : k > N7_TRI_MAX ? N7_TRI_MAX : k;
}
/* calibrate.txt: on the title screen the frame gets extra tiny, almost transparent triangles, 50 more every
   second from 300, with no budget; every second a synced crumb names the triangles the last frame really drew, so
   after the reboot sync.txt holds the highest count the device survived. Off unless the file exists. */
static int sCal; static uint32_t sCalExtra;
void Nano7CalibrateDraw(Graphics* g)
{
	if (!sCal || !sCalExtra) return;
	g->SetColor(Color(255, 255, 255, 3));
	for (uint32_t i = 0; i < sCalExtra / 2; i++) g->FillRect((int)(i % 40) * 2, 590 + (int)(i / 40 % 5), 1, 1);
}
extern "C" int n7_init(void* arena, size_t arena_size)
{
	if (arena && n7_arena_init(arena, arena_size)) { n7p_log("nano7 arena init failed"); return -1; }
	uint32_t t0 = n7p_ms();
	static std::string res, save;
	res = std::string("-resdir=") + n7p_data_dir();
	save = std::string("-savedir=") + n7p_data_dir() + "/userdata";
	static char* argv[4] = { (char*)"pvz", (char*)res.c_str(), (char*)save.c_str(), nullptr };
	{   /* the NANO7_* switches as the engine sees them; an earlier build had none on the device (getenv was newlib's) */
		static const char* const k[] = { "NANO7_N7R", "NANO7_LAZY_IMAGES", "NANO7_LEVEL_FREE", "NANO7_NO_POOL", "NANO7_NO_MUSIC",
		                                 "NANO7_COMPACT_REANIM", "NANO7_LAZY_REANIM", "NANO7_POOLS", "NANO7_NO_SOUND" };
		char m[200]; size_t o = (size_t)snprintf(m, sizeof m, "nano7 config");
		unsigned on = 0;
		for (unsigned i = 0; i < sizeof k / sizeof k[0]; i++) {
			bool v = getenv(k[i]) != nullptr; on |= (unsigned)v << i;
			if (o < sizeof m) o += (size_t)snprintf(m + o, sizeof m - o, " %s=%d", k[i] + 6, (int)v);
		}
		n7p_log(m);
		n7p_step_a("engine: config switches (a = bit mask, 1ff = all on)", on);
		extern bool gNano7N7R;
		if (on != 0x1ffu || !gNano7N7R) { n7_fatal("the NANO7_* switches are not set (build without -Dgetenv=n7_getenv?)"); return -1; }
	}
	n7p_step("engine: string colours");
	PvzpStringListSetColors(gLawnStringFormats, gLawnStringFormatCount);
	gExtractResourcesByName = Sexy::ExtractResourcesByName;
	n7p_step("engine: new LawnApp");
	gLawnApp = new LawnApp();
	gLawnApp->SetArgs(3, argv);
	n7p_step("engine: LawnApp::Init (properties, resources.xml, GL, sound, profiles)");
	gLawnApp->Init();
	n7p_step("engine: LawnApp::Init done");
	if (gLawnApp->mShutdown) { n7p_log("nano7 engine init failed (see above)"); sState = 2; return -1; }
	if (!gLawnApp->mPlayerInfo && gLawnApp->mProfileMgr)   // no keyboard for the new-player dialog: make one
	{
		n7p_step("engine: making the player profile");
		gLawnApp->mPlayerInfo = gLawnApp->mProfileMgr->AddProfile("Player");
		if (!gLawnApp->mPlayerInfo) gLawnApp->mPlayerInfo = gLawnApp->mProfileMgr->GetProfile("Player");
		n7p_log(gLawnApp->mPlayerInfo ? "nano7 made the player profile \"Player\"" : "nano7 could not make a player profile");
		if (gLawnApp->mPlayerInfo) {   /* a saved user1.dat from before (users.dat was never written) is read now */
			gLawnApp->mPlayerInfo->LoadDetails();
			gLawnApp->mProfileMgr->Save();   /* users.dat, so the next start finds the profile */
		}
	}
	if (gLawnApp->mPlayerInfo) {
		char m[160];
		snprintf(m, sizeof m, "nano7 player \"%s\" (user%u.dat): adventure level %d, finished %d, coins %d",
			gLawnApp->mPlayerInfo->mName.c_str(), (unsigned)gLawnApp->mPlayerInfo->mId, (int)gLawnApp->mPlayerInfo->mLevel,
			(int)gLawnApp->mPlayerInfo->mFinishedAdventure, (int)gLawnApp->mPlayerInfo->mCoins);
		n7p_log(m); SaveLogAdd(m); SaveLogWrite(); n7p_log_flush();
	}
	n7p_step("engine: start (title screen next)");
	gLawnApp->Nano7StartBegin();
	char m[200]; snprintf(m, sizeof m, "nano7 started in %u ms", (unsigned)(n7p_ms() - t0)); n7p_log(m);
	{   /* triangle budget: what is left in the OS heap now, at about 3000 bytes a triangle, 250..1300; budget.txt
	       in the data folder (a number of triangles) overrides it */
		uint32_t f = n7p_os_free(), b = 0;
		if (f) b = BudgetFor(f);
		sFree0 = f; sTex0 = n7_stats.tex_bytes;
		char t[24] = { 0 };
		int got = n7p_exists_rel("budget.txt") ? n7p_read_into("budget.txt", sBudgetBuf, 16) : -1;
		if (got > 0) { memcpy(t, sBudgetBuf, got < 16 ? got : 16); uint32_t v = (uint32_t)strtoul(t, 0, 10); if (v >= 100 && v <= 5000) { b = v; sFixed = true; } }
		if (const char* e = n7_getenv("NANO7_TRI_BUDGET")) { b = (uint32_t)strtoul(e, 0, 10); sFixed = true; }
		if (n7p_exists_rel("calibrate.txt")) { sCal = 1; sFixed = true; b = 0; n7p_log("nano7 calibrate.txt: no triangle budget; extra triangles on the title screen"); }
		n7_tri_budget_set(b); n7_stats.tri_budget = b ? b : 0xFFFFFFFFu;
		snprintf(m, sizeof m, "nano7 triangle budget %u a frame (OS heap free %u%s)", (unsigned)b, (unsigned)f, got > 0 ? ", budget.txt" : "");
		n7p_log(m);
	}
	Nano7BakeInit();   /* the baked pictures' objects, made before any level */
	LogMem("after start");
	n7p_log_flush();
	sState = 1;
	return 0;
}

#define N7_OS_STOP (640u * 1024u)   /* below this OS heap free the game stops with a message */
extern "C" int n7_frame(void)
{
	if (sState != 1) return 1;
	uint32_t t0 = n7p_ms();
	/* the first 30 frames of every new board get a crumb before and after (an earlier build rebooted in the first one) */
	static const void* sBoardSeen; static uint32_t sBoardFrames;
	const void* board = gLawnApp->mBoard;
	if (board != sBoardSeen) { sBoardSeen = board; sBoardFrames = 0; }
	bool watch = board && sBoardFrames < 30;
	{   /* a crumb at every scene change and around its first 10 frames, and around the 20 frames after an award
	       drop or pick-up (Nano7WatchFrames), so a reboot there leaves the step it was in */
		static int sSceneSeen = -1;
		int sc = (int)gLawnApp->mGameScene;
		if (sc != sSceneSeen) { sSceneSeen = sc; n7p_step_a("scene: changed (a = scene: 1 menu 2 intro 3 playing 5 award)", (uint32_t)sc); if (sWatch < 10) sWatch = 10; }
		if (sWatch > 0) { watch = true; sWatch--; }
	}
	if (watch) n7p_step_a("frame: board frame starts (a = frame on this board)", sBoardFrames);
	bool go = gLawnApp->Nano7Step();
	sFrames++;
	{   /* name every slow callback right after it, so a reboot in the next one still leaves the evidence */
		uint32_t dt = n7p_ms() - t0;
		if (watch) { n7p_step_a("frame: board frame done (a = ms)", dt); sBoardFrames++; }
		else if (dt >= 1500) n7p_step_a("frame: long callback just ended (a = ms)", dt);
	}
	if (!sFixed && sFree0 && sFrames % 30 == 0) {   /* the measured OS heap every 30 frames */
		uint32_t f = n7p_os_free();
		uint32_t b = f ? BudgetFor(f) : n7_stats.tri_budget;
		if (b != n7_stats.tri_budget) {
			n7_tri_budget_set(b);
			if (b + 50 < n7_stats.tri_budget || b > n7_stats.tri_budget + 50) {
				char m[96]; snprintf(m, sizeof m, "nano7 triangle budget now %u (OS free %u)", (unsigned)b, (unsigned)f); n7p_log(m);
			}
			n7_stats.tri_budget = b;
		}
	}
	if ((int)gLawnApp->mGameScene == 3) {   /* the same, while playing only (scene 3) */
		static uint32_t sCut0;
		n7_stats.play_frames++;
		if (n7_stats.tris_need > n7_stats.play_need_peak) n7_stats.play_need_peak = n7_stats.tris_need;
		if (n7_stats.tris_drawn > n7_stats.play_drawn_peak) n7_stats.play_drawn_peak = n7_stats.tris_drawn;
		if (n7_stats.drop_below) n7_stats.play_frames_dropping++;
		if (n7_stats.drop_below > n7_stats.play_drop_max) n7_stats.play_drop_max = n7_stats.drop_below;
		if (n7_stats.prot_cut > sCut0) n7_stats.play_prot_cut += n7_stats.prot_cut - sCut0;
		sCut0 = n7_stats.prot_cut;
	}
	{   /* where the triangle peaks come from (scene: 0 loading 1 menu 2 intro 3 playing 4 lost 5 award ...) */
		static uint32_t sTriLog; static int sScene = -1;
		int sc = (int)gLawnApp->mGameScene;
		if (sc != sScene) { sScene = sc; sTriLog = 0; }
		if (n7_stats.tris_need > sTriLog + sTriLog / 8 + 50) {
			sTriLog = n7_stats.tris_need;
			char m[120]; snprintf(m, sizeof m, "nano7 triangles %u needed, %u drawn in %u draws (scene %d, frame %u)", (unsigned)n7_stats.tris_need, (unsigned)n7_stats.tris_frame, (unsigned)n7_stats.draws_frame, sc, (unsigned)sFrames);
			n7p_log(m);
		}
	}
	uint32_t now = n7p_ms();
	if (sFrames == 60 || now - sLastLog > 30000) { LogMem("running"); n7p_log_flush(); sLastLog = now; }
	{   /* a memory and stack line every second, written out at once (log.txt and sync.txt) */
		static uint32_t sSec;
		if (now - sSec >= 1000) {
			sSec = now;
			n7_arena_stats_t a; n7_arena_stats(&a);
			volatile char probe = 0; uint32_t sp = (uint32_t)(uintptr_t)&probe;   /* about sp, on both targets */
			char m[300];
			snprintf(m, sizeof m, "nano7 each second: t=%u frame %u scene %d mode %d | arena %u high-water %u of %u, %u failed | "
				"textures %u in %u | tris %u budget %u | OS free %u largest %u | sp %08x minsp %08x | fs %u",
				(unsigned)now, (unsigned)sFrames, (int)gLawnApp->mGameScene, (int)gLawnApp->mGameMode, (unsigned)a.used,
				(unsigned)a.high_water, (unsigned)a.size, (unsigned)a.fails, (unsigned)n7_stats.tex_bytes, (unsigned)n7_stats.tex_count,
				(unsigned)n7_stats.tris_frame, (unsigned)n7_stats.tri_budget, (unsigned)n7p_os_free(), (unsigned)n7p_os_largest(),
				(unsigned)sp, (unsigned)n7p_min_sp(), (unsigned)n7p_fs_calls());
			n7p_log(m);
			snprintf(m, sizeof m, "nano7 tris: need %u drawn %u (peak %u), dropping classes below %u, protected cut %u | bakes %u kept of %u made, "
				"%u KB of 320, refused %u | textures peak %u",
				(unsigned)n7_stats.tris_need, (unsigned)n7_stats.tris_drawn, (unsigned)n7_stats.tris_drawn_peak, (unsigned)n7_stats.drop_below,
				(unsigned)n7_stats.prot_cut, (unsigned)n7_stats.bake_kept, (unsigned)n7_stats.bakes, (unsigned)(n7_stats.bake_bytes >> 10),
				(unsigned)n7_stats.bake_refused, (unsigned)n7_stats.tex_count_peak);
			n7p_log(m);
			{   /* stop with a message, not a reboot, when the OS heap runs low */
				uint32_t of = n7p_os_free();
				if (of && (!n7_stats.os_free_min || of < n7_stats.os_free_min)) n7_stats.os_free_min = of;
				if (of && of < N7_OS_STOP) {
					snprintf(m, sizeof m, "the iPod is nearly out of memory (OS heap free %u, textures %u in %u): stopped so it does not reboot",
						(unsigned)of, (unsigned)n7_stats.tex_bytes, (unsigned)n7_stats.tex_count);
					Nano7QuitFree(gLawnApp); n7p_log_flush(); n7_fatal(m);
				}
			}
			n7p_log_flush();
			if (sCal && (int)gLawnApp->mGameScene == 1) {
				snprintf(m, sizeof m, "nano7 calibrate: the last frame drew %u triangles (OS free %u largest %u)", (unsigned)n7_stats.tris_drawn,
					(unsigned)n7p_os_free(), (unsigned)n7p_os_largest());
				n7p_log(m); n7p_log_flush();
				n7p_step_a("calibrate: the last frame drew a triangles", n7_stats.tris_drawn);
				if (sCalExtra < 3000) sCalExtra = sCalExtra ? sCalExtra + 50 : 300;
			}
		}
	}
	if (!go) { Nano7QuitFree(gLawnApp); LogMem("quit"); n7p_log_flush(); sState = 2; return 1; }   /* textures released first */
	return 0;
}

extern "C" void n7_touch(int down, int px, int py)
{
	int gx, gy;
	Nano7PanelToGame(px, py, &gx, &gy);
	if (gx < 0) gx = 0; if (gx > 799) gx = 799; if (gy < 0) gy = 0; if (gy > 599) gy = 599;
	if (down && !sDown) { n7sdl_push_mouse(0, gx, gy); n7sdl_push_mouse(1, gx, gy); }
	else if (down) { if (gx != sLastX || gy != sLastY) n7sdl_push_mouse(0, gx, gy); }
	else if (sDown) n7sdl_push_mouse(2, sLastX, sLastY);
	sDown = down; if (down) { sLastX = gx; sLastY = gy; }
}

extern "C" void n7_log_mem(const char* what) { LogMem(what); }
