#include <vector>
/* nano7kit measurement: plays adventure levels 1-1.. by itself and writes one JSON line per level.
   Desktop measuring build only; nothing here ships to the iPod. */
#include "LawnApp.h"
#include "Lawn/Board.h"
#include "Lawn/Coin.h"
#include "Lawn/Plant.h"
#include "Lawn/CursorObject.h"
#include "Lawn/Zombie.h"
#include "Lawn/SeedPacket.h"
#include "Lawn/Cutscene.h"
#include "Lawn/Widget/SeedChooserScreen.h"
#include "Lawn/Widget/AwardScreen.h"
#include "Lawn/Widget/TitleScreen.h"
#include "Lawn/Widget/GameSelector.h"
#include "Lawn/System/PlayerInfo.h"
#include "Lawn/System/ProfileMgr.h"
#include "PvzpLib/Reanimator.h"
#include "PvzpLib/EffectSystem.h"
#include "PvzpLib/PvzpParticle.h"
#include "widget/Dialog.h"
#include "memtrack.h"
#include "graphics/MemoryImage.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cmath>
#include <string>
#include <map>
#include <unordered_map>
#include <algorithm>
using namespace Sexy;

struct TexRec { std::string path; int w, h; bool alpha; int fmt; int touched; };
extern std::unordered_map<void*, TexRec> gNano7Tex;
extern int gNano7Epoch;
struct PoolRec { unsigned int cap = 0, hw = 0, item = 0, levelhw = 0; };
extern std::map<std::string, PoolRec> gNano7Pools;
extern "C" { unsigned long long fgl_tex_live(void), fgl_tex_peak(void); int fgl_tex_count(void); void fgl_reset_peak(void); }
extern unsigned long long nano7_sound_bytes, nano7_sound_count;
unsigned long long nano7_music_bytes;

static int sPhase, sLevel, sFirst, sLast, sTick, sLevelStart, sRetries, sChose, sStateTick, sLastScene = -1;
static FILE* sOut;
static const char* scene_name(int s){ static const char* n[]={"loading","menu","intro","playing","zombies_won","award","credit","challenge"}; return (s>=0&&s<8)?n[s]:"?"; }

static int pot(int v){ int p=1; while(p<v) p<<=1; return p; }
static double sSurvPk;
static LawnApp* sApp;
static double sTexPkMix, sTexPkT16, sTexPkN7T; static int sTexPkN, sTexPkMan;
static void tex_sample();
struct TexSum { double t16=0, p16=0, pvr=0, mix=0, desk=0, n7t=0; int n=0, fromManifest=0; };
static std::map<std::string, double>* sManifest;
static size_t sManifestBytes;  /* the measuring tool's own texture-size table */
static double manifest_bytes(const std::string& path)
{
	MT_TAG(MT_DECODE);  /* measurement bookkeeping, not part of the game heap */
	if (!sManifest) {
		size_t before = mt_live(MT_DECODE);
		sManifest = new std::map<std::string, double>();
		if (const char* mf = getenv("NANO7_TEXMANIFEST")) if (FILE* f = fopen(mf, "r")) {
			char line[1024];
			while (fgets(line, sizeof line, f)) { if (line[0] == '#') continue; char name[900]; int w, h, tw, th, fmt; double b;
				if (sscanf(line, "%899[^\t]\t%d\t%d\t%d\t%d\t%d\t%lf", name, &w, &h, &tw, &th, &fmt, &b) == 7) (*sManifest)[name] = b; }
			fclose(f);
		}
		sManifestBytes = mt_live(MT_DECODE) - before;
	}
	std::string l = path; for (auto& c : l) c = (char)tolower(c);
	for (auto& c : l) if (c == '\\') c = '/';
	size_t dot = l.rfind('.'), sl = l.rfind('/'); if (dot != std::string::npos && (sl == std::string::npos || dot > sl)) l.resize(dot);
	auto it = sManifest->find(l); return it == sManifest->end() ? -1 : it->second;
}
static void tex_sum(TexSum& s, const TexRec& r, double k)
{
	int w = std::max(1, (int)std::ceil(r.w * k)), h = std::max(1, (int)std::ceil(r.h * k));
	double t16 = (double)w*h*2, p16 = (double)pot(w)*pot(h)*2;
	int side = pot(std::max(w,h)); if (side < 8) side = 8;
	double pvr = std::max(32.0, side*(double)side/2);
	std::string lp = r.path; for (auto& c : lp) c = (char)tolower(c);
	bool keep16 = lp.find("font") != std::string::npos || lp.find("seedpacket") != std::string::npos || lp.find("dialog") != std::string::npos || lp.find("button") != std::string::npos;
	double mix = keep16 ? t16 : std::min(pvr, t16);
	double mb = r.path.empty() ? -1 : manifest_bytes(r.path);
	s.n7t += mb >= 0 ? mb : t16; if (mb >= 0) s.fromManifest++;
	s.t16 += t16; s.p16 += p16; s.pvr += pvr; s.mix += mix; s.desk += (double)r.w*r.h*4; s.n++;
}

static void reanim_stats(int& n, double& raw, double& dedupe, double& q16, double& tracks)
{
	n = 0; raw = dedupe = q16 = tracks = 0;
	for (unsigned i = 0; i < gReanimatorDefCount; i++) {
		ReanimatorDefinition& d = gReanimatorDefArray[i];
		if (!d.mTracks.tracks) continue;
		n++;
		double r0 = raw, d0 = dedupe, q0 = q16;
		tracks += d.mTracks.count * (double)sizeof(ReanimatorTrack);
		for (int t = 0; t < d.mTracks.count; t++) {
			ReanimatorTrack& tr = d.mTracks.tracks[t];
			int frames = tr.mTransforms.count, runs = 0;
			for (int f = 0; f < frames; f++) {
				if (f == 0 || memcmp(&ReanimTf(tr.mTransforms, f), &ReanimTf(tr.mTransforms, f-1), sizeof(ReanimatorTransform))) runs++;
			}
			raw += frames * 44.0;                 /* ARM record size */
			dedupe += runs * 44.0 + frames * 2.0; /* unique records + 16-bit index per frame */
			q16 += runs * 20.0 + frames * 2.0;    /* 16-bit fixed fields, 16-bit image/font/text ids */
		}
		if (getenv("NANO7_REANIMDEFS")) {
			const char* nm = "?";
			for (unsigned k = 0; k < gReanimationParamArraySize; k++) if ((unsigned)gReanimationParamArray[k].mReanimationType == i) nm = gReanimationParamArray[k].mReanimFileName;
			FILE* f = fopen(getenv("NANO7_REANIMDEFS"), "a");
			if (f) { fprintf(f, "%d %s %.0f %.0f %.0f\n", sLevel, nm, raw - r0, dedupe - d0, q16 - q0); fclose(f); }
		}
	}
}

static void report(LawnApp* app, const char* result)
{
	if (!sOut) return;
	fprintf(stderr, "NANO7 report 1-%d %s\n", sLevel, result);
#ifdef NANO7_DEVICE
	{ extern void nano7_dev_report(FILE*, int, const char*, int, int, bool); nano7_dev_report(sOut, sLevel, result, sTick - sLevelStart, sRetries, sManifest != nullptr); return; }
#endif
	if (getenv("NANO7_LIVEDUMP")) mt_dump_big((std::string(getenv("NANO7_LIVEDUMP")) + "." + std::to_string(sLevel)).c_str(), 0);
	int n; double raw, dd, q16, tr; reanim_stats(n, raw, dd, q16, tr);
	TexSum drawn, resident; std::map<std::string,double> top;
	for (auto& kv : gNano7Tex) { tex_sum(resident, kv.second, 0.4); if (kv.second.touched == gNano7Epoch) { tex_sum(drawn, kv.second, 0.4); } }
	tex_sample();
	fprintf(sOut, "{\"heap_peak_nano\":%zu,\"heap_live_nano\":%zu,\"tex_peak_mix\":%.0f,\"tex_peak_t16\":%.0f,\"tex_peak_n7t\":%.0f,\"tex_peak_n\":[%d,%d],\"lazy_survivor_peak\":%.0f,", mt_peak_nano(), mt_nano_live(), sTexPkMix, sTexPkT16, sTexPkN7T, sTexPkN, sTexPkMan, sSurvPk);
	fprintf(sOut, "\"level\":%d,\"result\":\"%s\",\"updates\":%d,\"retries\":%d,", sLevel, result, sTick - sLevelStart, sRetries);
	fprintf(sOut, "\"heap_peak_all\":%zu,\"heap_peak_nonpixel\":%zu,\"heap_peak_model\":%zu,\"heap_live_nonpixel\":%zu,\"biggest_nonpixel\":%zu,\"biggest_pixel\":%zu,",
		mt_peak(), mt_peak_nonpixel(), mt_peak_model(), mt_live_nonpixel(), mt_biggest_nonpixel(), mt_biggest_pixel());
	fprintf(sOut, "\"atpeak_phase\":%d,\"bookkeeping_decode\":%zu,", mt_atpeak_phase, sManifestBytes); fprintf(sOut, "\"tags_live\":{"); for (int t = 0; t < MT_NTAGS; t++) fprintf(sOut, "%s\"%s\":%zu", t?",":"", mt_tag_name(t), mt_live(t)); fprintf(sOut, "},");
	fprintf(sOut, "\"tags_atpeak\":{"); for (int t = 0; t < MT_NTAGS; t++) fprintf(sOut, "%s\"%s\":%zu", t?",":"", mt_tag_name(t), mt_atpeak_tag(t)); fprintf(sOut, "},\"atpeak_last\":[%zu,%d],", mt_atpeak_last(), mt_atpeak_lasttag());
	fprintf(sOut, "\"tags_peak\":{"); for (int t = 0; t < MT_NTAGS; t++) fprintf(sOut, "%s\"%s\":%zu", t?",":"", mt_tag_name(t), mt_peak_tag(t)); fprintf(sOut, "},");
	fprintf(sOut, "\"pools\":{"); bool first = true;
	for (auto& kv : gNano7Pools) { fprintf(sOut, "%s\"%s\":[%u,%u,%u,%u]", first?"":",", kv.first.c_str(), kv.second.cap, kv.second.levelhw, kv.second.hw, kv.second.item); first = false; kv.second.levelhw = 0; }
	fprintf(sOut, "},\"reanim\":{\"defs\":%d,\"raw\":%.0f,\"dedupe\":%.0f,\"q16\":%.0f,\"tracks\":%.0f},", n, raw, dd, q16, tr);
	fprintf(sOut, "\"tex_drawn\":{\"n\":%d,\"desk\":%.0f,\"t16\":%.0f,\"p16\":%.0f,\"pvr\":%.0f,\"mix\":%.0f},", drawn.n, drawn.desk, drawn.t16, drawn.p16, drawn.pvr, drawn.mix);
	fprintf(sOut, "\"tex_resident\":{\"n\":%d,\"desk\":%.0f,\"t16\":%.0f,\"p16\":%.0f,\"pvr\":%.0f,\"mix\":%.0f},", resident.n, resident.desk, resident.t16, resident.p16, resident.pvr, resident.mix);
	fprintf(sOut, "\"fakegl_live\":%llu,\"fakegl_peak\":%llu,\"sound_bytes\":%llu,\"sound_files\":%llu,\"music_bytes\":%llu}\n",
		fgl_tex_live(), fgl_tex_peak(), nano7_sound_bytes, nano7_sound_count, nano7_music_bytes);
	fflush(sOut);
	if (getenv("NANO7_REANIMLIST")) {
		fprintf(stderr, "NANO7 live reanims 1-%d:", sLevel);
		for (Reanimation* r : app->mEffectSystem->mReanimationHolder->mReanimations)
			fprintf(stderr, " %d%s%s", (int)r->mReanimationType, r->mIsAttachment ? "a" : "", r->mDead ? "d" : "");
		fprintf(stderr, "\n");
	}
	if (getenv("NANO7_DEFLIST")) {
		fprintf(stderr, "NANO7 loaded defs 1-%d:", sLevel);
		for (unsigned i = 0; i < gReanimatorDefCount; i++) if (gReanimatorDefArray[i].mTracks.tracks) fprintf(stderr, " %u", i);
		fprintf(stderr, "\n");
		fprintf(stderr, "NANO7 live reanims 1-%d:", sLevel);
		for (Reanimation* r : app->mEffectSystem->mReanimationHolder->mReanimations)
			fprintf(stderr, " %d%s%s", (int)r->mReanimationType, r->mIsAttachment ? "a" : "", r->mDead ? "d" : "");
		fprintf(stderr, "\n");
	}
	if (getenv("NANO7_BITSLIST")) {
		std::string p = std::string(getenv("NANO7_BITSLIST")) + "." + std::to_string(sLevel);
		if (FILE* f = fopen(p.c_str(), "w")) {
			for (Sexy::MemoryImage* im : app->mMemoryImageSet)
				if (im->mBits || im->mColorIndices)
					fprintf(f, "%d %d %d %d %d %s\n", im->mWidth * im->mHeight * 4, im->mWidth, im->mHeight, im->mRenderData != nullptr, (int)im->mPurgeBits, im->mFilePath.c_str());
			fclose(f);
		}
	}
	if (getenv("NANO7_TEXLIST")) {
		std::string p = std::string(getenv("NANO7_TEXLIST")) + "." + std::to_string(sLevel);
		if (FILE* f = fopen(p.c_str(), "w")) { for (auto& kv : gNano7Tex) if (kv.second.touched == gNano7Epoch) fprintf(f, "%d %d %d %d %s\n", kv.second.w, kv.second.h, kv.second.alpha, kv.second.fmt, kv.second.path.c_str()); fclose(f); }
	}
}

static void begin_level(int lvl)
{
	sLevel = lvl; sLevelStart = sTick; sChose = 0;
	gNano7Epoch++;
#ifdef NANO7_DEVICE
	{ extern void nano7_dev_level_begin(int); nano7_dev_level_begin(lvl); }
#endif
	mt_reset_peak(); fgl_reset_peak(); sTexPkMix = sTexPkT16 = sTexPkN7T = 0; sSurvPk = 0;
	fprintf(stderr, "NANO7 begin level 1-%d\n", lvl);
}

static void start_level(LawnApp* app, int lvl)
{
	app->mPlayerInfo->mLevel = lvl;
	app->KillGameSelector();
	app->KillBoard();
	app->Nano7ProcessSafeDeleteList();   /* same LOW_MEMORY ordering issue as the award screen */
	app->PreNewGame(GameMode::GAMEMODE_ADVENTURE, false);
}

static bool try_plant(Board* b, SeedPacket* p, int col, int row)
{
	SeedType t = p->mPacketType;
	if (b->CanPlantAt(col, row, t) != PlantingReason::PLANTING_OK) return false;
	p->MouseDown(0, 0, 1);
	if (!b->IsPlantInCursor()) return true;   /* could not pick it up (sun, recharge); stop for now */
	int px = b->GridToPixelX(col, row) + 40, py = b->GridToPixelY(col, row) + 40;
	b->MouseDown(px, py, 1);
	b->MouseUp(px, py, 1);
	if (b->IsPlantInCursor()) b->ClearCursor();
	return true;
}

static void play(LawnApp* app, Board* b)
{
	for (Coin* c : b->mCoins) if (!c->mDead && !c->mIsBeingCollected) c->Collect();
	if (b->mPaused) return;
	if (b->IsPlantInCursor()) b->ClearCursor();
	TutorialState ts = b->mTutorialState;
	if (ts == TutorialState::TUTORIAL_SHOVEL_PICKUP) { b->PickUpTool(GameObjectType::OBJECT_TYPE_SHOVEL); return; }
	if (ts == TutorialState::TUTORIAL_SHOVEL_DIG || ts == TutorialState::TUTORIAL_SHOVEL_KEEP_DIGGING) {
		if (b->mCursorObject->mCursorType != CursorType::CURSOR_TYPE_SHOVEL) { b->PickUpTool(GameObjectType::OBJECT_TYPE_SHOVEL); return; }
		for (Plant* pl : b->mPlants) { if (pl->mDead) continue; b->MouseDown(pl->mX + 40, pl->mY + 40, 1); b->MouseUp(pl->mX + 40, pl->mY + 40, 1); break; }
		return;
	}
	if (b->mCursorObject->mCursorType != CursorType::CURSOR_TYPE_NORMAL && !b->IsPlantInCursor()) b->ClearCursor();
	int sunflowers = 0; for (Plant* pl : b->mPlants) if (!pl->mDead && pl->mSeedType == SeedType::SEED_SUNFLOWER) sunflowers++;
	SeedBank* sb = b->mSeedBank.get();
	for (int i = 0; i < sb->mNumPackets; i++) {
		SeedPacket* p = &sb->mSeedPackets[i];
		if (p->mPacketType == SeedType::SEED_NONE || !p->CanPickUp()) continue;
		SeedType t = p->mPacketType;
		int c0 = 0, c1 = 8;
		if (t == SeedType::SEED_SUNFLOWER) { if (sunflowers >= 10) continue; c0 = 0; c1 = 1; }
		else if (t == SeedType::SEED_PEASHOOTER || t == SeedType::SEED_SNOWPEA || t == SeedType::SEED_REPEATER) { c0 = 2; c1 = 5; }
		else if (t == SeedType::SEED_WALLNUT) { c0 = 6; c1 = 7; }
		else if (t == SeedType::SEED_CHERRYBOMB || t == SeedType::SEED_SQUASH || t == SeedType::SEED_CHOMPER) {
			/* only when a zombie is close */
			bool near = false; int zr = 0, zc = 0;
			for (Zombie* z : b->mZombies) if (!z->mDead && z->mPosX < 500 && z->mPosX > 0) { near = true; zr = z->mRow; zc = b->PixelToGridXKeepOnBoard((int)z->mPosX + 40, (int)z->mPosY); break; }
			if (!near) continue;
			for (int dc = -1; dc <= 1; dc++) if (try_plant(b, p, std::clamp(zc + dc, 0, 8), zr)) return;
			continue;
		}
		else if (t == SeedType::SEED_POTATOMINE) { c0 = 6; c1 = 8; }
		else if (t == SeedType::SEED_CABBAGEPULT || t == SeedType::SEED_KERNELPULT || t == SeedType::SEED_MELONPULT || t == SeedType::SEED_PUFFSHROOM) { c0 = 1; c1 = 4; }
		for (int pass = 0; pass < 2; pass++)
			for (int c = (pass ? 0 : c0); c <= (pass ? 8 : c1); c++)
				for (int k = 0, nr = b->StageHas6Rows() ? 6 : 5; k < nr; k++) { int r = (k + sTick / 7) % nr; if (try_plant(b, p, c, r)) return; }
	}
}

static void pick_seeds(LawnApp* app)
{
	SeedChooserScreen* sc = app->mSeedChooserScreen.get();
	/* sweep: the plants a world needs first (pool: lily pads; roof: pots and lobbers; night: mushrooms) */
	std::vector<SeedType> first;
	Board* bd = app->mBoard;
	if (bd->StageHasRoof()) first = { SeedType::SEED_FLOWERPOT, SeedType::SEED_CABBAGEPULT, SeedType::SEED_KERNELPULT, SeedType::SEED_MELONPULT };
	else if (bd->StageHasPool()) first = { SeedType::SEED_LILYPAD };
	if (bd->StageIsNight()) { first.push_back(SeedType::SEED_PUFFSHROOM); first.push_back(SeedType::SEED_FUMESHROOM); }
	if (bd->StageHasFog()) first.push_back(SeedType::SEED_PLANTERN);
	for (SeedType t : first) {
		if (sc->mSeedsInBank >= bd->mSeedBank->mNumPackets) break;
		ChosenSeed& cs = sc->mChosenSeeds[t];
		if (!app->HasSeedType(t) || cs.mSeedState != ChosenSeedState::SEED_IN_CHOOSER) continue;
		cs.mTimeStartMotion = 0; cs.mTimeEndMotion = 0; cs.mStartX = cs.mX; cs.mStartY = cs.mY;
		sc->GetSeedPositionInBank(sc->mSeedsInBank, cs.mEndX, cs.mEndY);
		cs.mSeedState = ChosenSeedState::SEED_IN_BANK; cs.mSeedIndexInBank = sc->mSeedsInBank; sc->mSeedsInBank++;
	}
	static const SeedType want[] = { SeedType::SEED_SUNFLOWER, SeedType::SEED_PEASHOOTER, SeedType::SEED_WALLNUT, SeedType::SEED_CHERRYBOMB,
		SeedType::SEED_POTATOMINE, SeedType::SEED_SNOWPEA, SeedType::SEED_CHOMPER, SeedType::SEED_REPEATER };
	for (SeedType t : want) {
		if (sc->mSeedsInBank >= app->mBoard->mSeedBank->mNumPackets) break;
		ChosenSeed& cs = sc->mChosenSeeds[t];
		if (!app->HasSeedType(t) || cs.mSeedState != ChosenSeedState::SEED_IN_CHOOSER) continue;
		cs.mTimeStartMotion = 0; cs.mTimeEndMotion = 0; cs.mStartX = cs.mX; cs.mStartY = cs.mY;
		sc->GetSeedPositionInBank(sc->mSeedsInBank, cs.mEndX, cs.mEndY);
		cs.mSeedState = ChosenSeedState::SEED_IN_BANK; cs.mSeedIndexInBank = sc->mSeedsInBank; sc->mSeedsInBank++;
	}
	sc->PickRandomSeeds();   /* fills any slots left, lands the seeds and closes the chooser */
}

#include <csignal>
#include <ucontext.h>
extern "C" char __executable_start, etext;
static void segv(int sig, siginfo_t* si, void* uc)
{
	ucontext_t* u = (ucontext_t*)uc;
	uintptr_t eip = u->uc_mcontext.gregs[REG_EIP], esp = u->uc_mcontext.gregs[REG_ESP];
	fprintf(stderr, "NANO7 SEGV addr %p eip %lx stack:", si->si_addr, (unsigned long)eip);
	uintptr_t* sp = (uintptr_t*)esp; int n = 0;
	for (int i = 0; i < 4096 && n < 24; i++) { uintptr_t v = sp[i]; if (v > (uintptr_t)&__executable_start && v < (uintptr_t)&etext) { fprintf(stderr, " %lx", (unsigned long)v); n++; } }
	fprintf(stderr, "\n"); _Exit(9);
}
static void tex_sample()
{
	TexSum r; for (auto& kv : gNano7Tex) tex_sum(r, kv.second, 0.4);
	if (r.mix > sTexPkMix) sTexPkMix = r.mix;
	if (r.t16 > sTexPkT16) sTexPkT16 = r.t16;
	if (sApp && getenv("NANO7_LAZY_IMAGES")) {
		double surv = 0;
		for (Sexy::MemoryImage* im : sApp->mMemoryImageSet)
			if (im->mBits && im->mPurgeBits && !im->mFilePath.empty() && im->mColorTable == nullptr) surv += (double)im->mWidth * im->mHeight * 4;
		if (surv > sSurvPk) sSurvPk = surv;
	}
	if (r.n7t > sTexPkN7T) { sTexPkN7T = r.n7t; sTexPkN = r.n; sTexPkMan = r.fromManifest; }
}
static struct Nano7SegvInit { Nano7SegvInit() { struct sigaction sa = {}; sa.sa_sigaction = segv; sa.sa_flags = SA_SIGINFO; sigaction(SIGSEGV, &sa, nullptr); } } sNano7SegvInit;
void nano7_bot_update(LawnApp* app)
{
	sTick++;
	sApp = app;
	if (sTick % 50 == 0) tex_sample();
	{ static int sc = -1, t0 = 0; int s = (int)app->mGameScene; if (s != sc) { sc = s; t0 = sTick; } mt_phase = s * 1000 + (sTick - t0) / 100; }
	if (sTick == 1) { struct sigaction sa = {}; sa.sa_sigaction = segv; sa.sa_flags = SA_SIGINFO; sigaction(SIGSEGV, &sa, nullptr); }
	if (sPhase == 0) {
		if (!sOut) {
			sOut = fopen(getenv("NANO7_OUT") ? getenv("NANO7_OUT") : "nano7_out.jsonl", "a");
			sFirst = getenv("NANO7_FIRST") ? atoi(getenv("NANO7_FIRST")) : 1;
			sLast = getenv("NANO7_LAST") ? atoi(getenv("NANO7_LAST")) : 10;
		}
		if (!app->mDialogList.empty() && sTick % 5 == 0) { app->KillDialog(app->mDialogList.front()->mId); return; }
		if (app->mTitleScreen && app->mLoadingThreadCompleted && sTick > 20) { app->LoadingCompleted(); return; }
		if (app->mGameSelector && !app->mTitleScreen && sTick > 40) {
			if (!app->mPlayerInfo) { app->mPlayerInfo = app->mProfileMgr->AddProfile("nano7"); if (!app->mPlayerInfo) app->mPlayerInfo = app->mProfileMgr->GetProfile("nano7"); }
			if (getenv("NANO7_EXPORT_N7R")) { Nano7ExportN7R(getenv("NANO7_EXPORT_N7R")); sPhase = 2; return; }
			/* a startup snapshot: everything resident after boot, before any level */
			fprintf(sOut, "{\"event\":\"boot\",\"heap_live_all\":%zu,\"heap_live_nonpixel\":%zu,\"heap_peak_nonpixel\":%zu,\"heap_model\":%zu,\"tags_live\":{", mt_live(-1), mt_live_nonpixel(), mt_peak_nonpixel(), mt_model_live());
			for (int t = 0; t < MT_NTAGS; t++) fprintf(sOut, "%s\"%s\":%zu", t?",":"", mt_tag_name(t), mt_live(t));
			fprintf(sOut, "}}\n"); fflush(sOut);
			if (getenv("NANO7_DUMP")) mt_dump_big(getenv("NANO7_DUMP"), 16384);
			if (getenv("NANO7_SPEED")) app->mUpdateMultiplier = atof(getenv("NANO7_SPEED"));
			if (getenv("NANO7_CLICK")) {   /* the device's path: click Adventure on the menu like a player */
				fprintf(stderr, "NANO7 click Adventure (profile level %d)\n", app->mPlayerInfo ? app->mPlayerInfo->mLevel : -1);
				app->mGameSelector->ButtonDepress(100);   /* GameSelector_Adventure (private) */
				sPhase = 3; return;
			}
			sPhase = 1; begin_level(sFirst); start_level(app, sFirst);
		}
		return;
	}
	if (sPhase == 3) {   /* after the click: follow the game until it is on a board in Adventure mode, then play */
		static int sSeen = -1;
		if ((int)app->mGameMode != sSeen) { sSeen = (int)app->mGameMode; fprintf(stderr, "NANO7 click: game mode now %d\n", sSeen); }
		if (!app->mDialogList.empty() && sTick % 20 == 0) { app->KillDialog(app->mDialogList.front()->mId); return; }
		if (app->mBoard && app->mGameMode == GameMode::GAMEMODE_ADVENTURE) {
			fprintf(stderr, "NANO7 click: on the board, Adventure level 1-%d\n", app->mBoard->mLevel);
			sPhase = 1; begin_level(app->mBoard->mLevel);
		}
		if (sTick > 200000) { fprintf(stderr, "NANO7 click: timeout\n"); sPhase = 2; }
		return;
	}
	if (sPhase == 2) { app->mShutdown = true; return; }
	int scene = app->mGameScene;
	if (scene != sLastScene) { fprintf(stderr, "NANO7 1-%d tick %d scene %s\n", sLevel, sTick - sLevelStart, scene_name(scene)); sLastScene = scene; sStateTick = sTick; }
	if (sTick - sLevelStart > (sLevel % 10 == 5 ? 40000 : 200000)) {   /* x-5 are minigames the bot cannot play; move on */
		report(app, "timeout");
		if (sLevel >= sLast) { sPhase = 2; return; }
		sRetries = 0; begin_level(sLevel + 1); start_level(app, sLevel); return;
	}
	if (!app->mDialogList.empty() && sTick % 20 == 0) { app->KillDialog(app->mDialogList.front()->mId); return; }
	Board* b = app->mBoard;
	if (scene == GameScenes::SCENE_AWARD) {
		if (sTick - sStateTick > 300) {
			report(app, "won");
			fprintf(stderr, "NANO7 kill award\n");
			app->KillAwardScreen();
			app->Nano7ProcessSafeDeleteList();
			fprintf(stderr, "NANO7 award killed\n");
			if (sLevel >= sLast) { sPhase = 2; return; }
			int next = app->mPlayerInfo->mLevel; if (next <= sLevel) next = sLevel + 1;
			sRetries = 0; begin_level(next); start_level(app, next);
		}
		return;
	}
	if (b && b->mLevel != sLevel && scene != GameScenes::SCENE_AWARD && b->mLevel > 0) {
		/* no award screen between levels: the game went straight on */
		int next = b->mLevel; report(app, "won"); if (sLevel >= sLast) { sPhase = 2; return; } sRetries = 0; begin_level(next);
	}
	if (!b) return;
	if (scene == GameScenes::SCENE_ZOMBIES_WON) {
		if (sTick - sStateTick > 400) { sRetries++; sChose = 0; fprintf(stderr, "NANO7 lost 1-%d, retry %d\n", sLevel, sRetries); start_level(app, sLevel); }
		return;
	}
	if (scene == GameScenes::SCENE_LEVEL_INTRO) {
		if (b->mCutScene && b->mCutScene->IsInShovelTutorial()) { if (sTick % 20 == 0) play(app, b); return; }
		if (sTick % 2000 == 0) fprintf(stderr, "NANO7 intro wait: tut %d dave %d msg %d choosing %d\n", (int)b->mTutorialState, (int)app->mCrazyDaveState, app->mCrazyDaveMessageIndex, (int)(b->mCutScene && b->mCutScene->mSeedChoosing));
		if (sTick % 25 == 0 && b->mCutScene && b->mCutScene->IsShowingCrazyDave()) { b->mCutScene->MouseDown(0, 0); return; }
		if (b->mCutScene && b->mCutScene->mSeedChoosing && app->mSeedChooserScreen && !sChose && sTick - sStateTick > 150) {
			bool ready = true; /* wait for the chooser to settle */
			if (ready) { sChose = 1; pick_seeds(app); }
		}
		return;
	}
	if (scene == GameScenes::SCENE_PLAYING && sTick % 5 == 0) play(app, b);
	if (scene == GameScenes::SCENE_PLAYING && b && getenv("NANO7_RICH") && sTick % 50 == 0 && b->mSunMoney < 2000) b->mSunMoney = 2000;   /* sweep: plenty of sun, so every plant gets used */
	if (scene == GameScenes::SCENE_PLAYING && sTick % 5000 == 0) { int nz = 0; for (Zombie* z : b->mZombies) if (!z->mDead) nz++; fprintf(stderr, "NANO7 1-%d t%d wave %d/%d zombies %d sun %d tut %d\n", sLevel, sTick - sLevelStart, b->mCurrentWave, b->mNumWaves, nz, b->mSunMoney, (int)b->mTutorialState); }
}
