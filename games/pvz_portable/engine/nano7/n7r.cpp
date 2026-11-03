/* nano7 compact animation format, loader, exporter and per-level freeing. See n7r.h. */
#include "n7bake.h"
#include "n7r.h"
#include "memtrack.h"
#include "PvzpLib/Reanimator.h"
#include "PvzpLib/ReanimAtlas.h"
#include "PvzpLib/Definition.h"
#include "LawnApp.h"
#include "graphics/MemoryImage.h"
#include "paklib/PakInterface.h"
#include <map>
#include <cstdio>
#include <cstdint>
#include <unordered_map>
#include <unordered_set>
#include "PvzpLib/PvzpCommon.h"
#include "PvzpLib/EffectSystem.h"
#include "Lawn/System/ReanimationLawn.h"
#include <vector>
#include <new>
#include <memory>
#include <cstdlib>
#include <cstring>
#include <cmath>
#include <cstdio>
#include <sys/stat.h>

using namespace Sexy;

bool gNano7N7R = getenv("NANO7_N7R") != nullptr;
bool gNano7AllowCompile = false;   // only the .n7r exporter (dry run) may compile XML definitions

/* ---- half floats ---- */
static uint16_t f2h(float f)
{
	uint32_t x; memcpy(&x, &f, 4);
	uint32_t sign = (x >> 16) & 0x8000; int e = (int)((x >> 23) & 0xFF) - 127 + 15; uint32_t m = x & 0x7FFFFF;
	if (((x >> 23) & 0xFF) == 0xFF) return (uint16_t)(sign | 0x7C00 | (m ? 0x200 : 0));
	if (e >= 31) return (uint16_t)(sign | 0x7BFF);                       /* clamp to max */
	if (e <= 0) { if (e < -10) return (uint16_t)sign; m |= 0x800000; int sh = 14 - e; uint32_t h = m >> sh; if ((m >> (sh - 1)) & 1) h++; return (uint16_t)(sign | h); }
	uint32_t h = sign | (e << 10) | (m >> 13); if (m & 0x1000) h++;     /* round half up */
	return (uint16_t)h;
}
static float h2f(uint16_t h)
{
	uint32_t sign = (uint32_t)(h & 0x8000) << 16, e = (h >> 10) & 0x1F, m = h & 0x3FF, x;
	if (e == 0) { if (!m) x = sign; else { e = 127 - 15 + 1; while (!(m & 0x400)) { m <<= 1; e--; } m &= 0x3FF; x = sign | (e << 23) | (m << 13); } }
	else if (e == 31) x = sign | 0x7F800000 | (m << 13);
	else x = sign | ((e - 15 + 127) << 23) | (m << 13);
	float f; memcpy(&f, &x, 4); return f;
}

ReanimatorTransform& Nano7Decode(const Nano7Def* d, const Nano7Tf& r)
{
	static thread_local ReanimatorTransform sRing[32];
	static thread_local unsigned sNext;
	ReanimatorTransform& t = sRing[sNext++ & 31];
	t.mTransX = r.tx * 0.125f; t.mTransY = r.ty * 0.125f;
	t.mSkewX = h2f(r.kx); t.mSkewY = h2f(r.ky); t.mScaleX = h2f(r.sx); t.mScaleY = h2f(r.sy);
	t.mFrame = h2f(r.fr); t.mAlpha = h2f(r.al);
	t.mImage = r.img == 0xFFFF ? nullptr : d->images[r.img];
	t.mFont = r.font == 0xFF ? nullptr : d->fonts[r.font];
	t.mText = d->texts[r.txt];
	return t;
}

/* ---- loader ---- */
static inline size_t al8(size_t n) { return (n + 7) & ~(size_t)7; }
struct Rd { const uint8_t* p; const uint8_t* e; bool ok = true;
	template<class T> T get() { T v{}; if (p + sizeof(T) > e) { ok = false; return v; } memcpy(&v, p, sizeof(T)); p += sizeof(T); return v; }
	const uint8_t* take(size_t n) { if (p + n > e) { ok = false; return nullptr; } const uint8_t* q = p; p += n; return q; } };

void Nano7FreeUnusedDefs(void* theApp);
#ifdef NANO7_DEVICE
extern "C" void n7_fatal(const char*);
extern "C" void n7p_log(const char*);
extern "C" void n7p_log_flush(void);
#endif
/* the big one-piece blocks (file and definition) are asked for without throwing. When the arena has no free
   piece that large (it is fragmented by then: worlds 4 and 5 in the dry run), the animations no live object uses
   are released and it is tried once more; if that fails too, a clear stop instead of an uncaught bad_alloc. */
static void* N7rAlloc(size_t n, const std::string& what)
{
	void* p = operator new[](n, std::nothrow);
	if (p) return p;
	char m[200];
	snprintf(m, sizeof m, "nano7 memory: no free %lu-byte piece for %s; releasing unused animations", (unsigned long)n, what.c_str());
#ifdef NANO7_DEVICE
	n7p_log(m);
#endif
	fprintf(stderr, "%s\n", m);
	if (gLawnApp) Nano7FreeUnusedDefs(gLawnApp);
	p = operator new[](n, std::nothrow);
	if (p) return p;
	Nano7BakeFreeAll();   /* then the baked pictures (made again when next drawn) */
	p = operator new[](n, std::nothrow);
	if (p) return p;
	snprintf(m, sizeof m, "out of memory loading %s (no free %lu-byte piece in the game arena): stopped so it does not reboot",
		what.c_str(), (unsigned long)n);
#ifdef NANO7_DEVICE
	n7p_log_flush(); n7_fatal(m);
#endif
	fprintf(stderr, "%s\n", m); abort();
}

bool Nano7LoadN7R(const std::string& theFileName, ReanimatorDefinition* theDef)
{
	if (getenv("NANO7_DEFLIST")) fprintf(stderr, "NANO7 load def %s phase %d\n", theFileName.c_str(), mt_phase);
	std::unique_ptr<uint8_t[]> buf; size_t bufN = 0;
	{
		MT_TAG(MT_FILE);
		PFILE* fp = p_fopen((theFileName + ".n7r").c_str(), "rb");
		if (!fp) return false;
		p_fseek(fp, 0, SEEK_END); long n = p_ftell(fp); p_fseek(fp, 0, SEEK_SET);
		bufN = n > 0 ? (size_t)n : 0;
		buf.reset((uint8_t*)N7rAlloc(bufN ? bufN : 1, theFileName));
		if (n > 0) p_fread(buf.get(), 1, n, fp);
		p_fclose(fp);
	}
	Rd r{buf.get(), buf.get() + bufN};
	if (r.get<uint32_t>() != 0x3152374E) return false;   /* "N7R1" */
	float fps = r.get<float>();
	uint16_t ntracks = r.get<uint16_t>(), nimg = r.get<uint16_t>(), nfont = r.get<uint16_t>(), ntext = r.get<uint16_t>();
	uint32_t blob = r.get<uint32_t>();
	/* pass 1: sizes */
	const uint8_t* strStart = r.p;
	std::vector<std::pair<const char*, uint16_t>> strs;
	for (int i = 0; i < nimg + nfont + ntext; i++) { uint16_t l = r.get<uint16_t>(); const char* s = (const char*)r.take(l); strs.push_back({s, l}); }
	(void)strStart;
	struct TrackIn { const char* name; uint16_t nl, nf, nu; const uint16_t* idx; const Nano7Tf* rec; };
	std::vector<TrackIn> tin(ntracks);
	size_t trackBytes = 0;
	for (int t = 0; t < ntracks; t++) {
		TrackIn& ti = tin[t];
		ti.nl = r.get<uint16_t>(); ti.name = (const char*)r.take(ti.nl);
		ti.nf = r.get<uint16_t>(); ti.nu = r.get<uint16_t>();
		ti.idx = (const uint16_t*)r.take(ti.nf * 2u); ti.rec = (const Nano7Tf*)r.take(ti.nu * sizeof(Nano7Tf));
		trackBytes += al8(sizeof(void*) + ti.nf * 2u + ti.nu * sizeof(Nano7Tf));
		blob += ti.nl + 1;
	}
	if (!r.ok) return false;
	size_t head = al8(sizeof(Nano7Def) + (nimg + nfont + ntext) * sizeof(void*) + sizeof(void*));
	size_t tracksArr = al8(ntracks * sizeof(ReanimatorTrack));
	size_t total = head + tracksArr + trackBytes + blob;
	char* block;
	{ MT_TAG(MT_REANIM); block = (char*)N7rAlloc(total, theFileName); }
	memset(block, 0, head + tracksArr);
	Nano7Def* d = (Nano7Def*)block;
	d->images = (Image**)(block + sizeof(Nano7Def));
	d->fonts = (_Font**)(d->images + nimg);
	d->texts = (const char**)(d->fonts + nfont);
	d->nimg = nimg; d->nfont = nfont; d->ntext = ntext;
	*(void**)(block + head - sizeof(void*)) = block;          /* base pointer just before the tracks */
	ReanimatorTrack* tracks = (ReanimatorTrack*)(block + head);
	char* tb = block + head + tracksArr;
	char* sb = block + head + tracksArr + trackBytes;
	auto putStr = [&](const char* s, uint16_t l) { char* o = sb; memcpy(sb, s, l); sb[l] = 0; sb += l + 1; return (const char*)o; };
	for (int i = 0; i < ntext; i++) d->texts[i] = putStr(strs[nimg + nfont + i].first, strs[nimg + nfont + i].second);
	for (int t = 0; t < ntracks; t++) {
		TrackIn& ti = tin[t];
		tracks[t].mName = putStr(ti.name, ti.nl);
		*(Nano7Def**)tb = d;
		memcpy(tb + sizeof(void*), ti.idx, ti.nf * 2u);
		memcpy(tb + sizeof(void*) + ti.nf * 2u, ti.rec, ti.nu * sizeof(Nano7Tf));
		tracks[t].mTransforms.mTransforms = (ReanimatorTransform*)tb;
		tracks[t].mTransforms.count = ti.nf;
		tb += al8(sizeof(void*) + ti.nf * 2u + ti.nu * sizeof(Nano7Tf));
	}
	for (int i = 0; i < nimg; i++) { MT_TAG(MT_PIXELS); DefinitionLoadImage(&d->images[i], std::string(strs[i].first, strs[i].second)); }
	for (int i = 0; i < nfont; i++) DefinitionLoadFont(&d->fonts[i], std::string(strs[nimg + i].first, strs[nimg + i].second));
	theDef->mFPS = fps;
	theDef->mReanimAtlas = nullptr;
	theDef->mTracks.count = ntracks;
	theDef->mTracks.tracks = tracks;
	return true;
}

Nano7Def* Nano7DefOf(ReanimatorDefinition* theDef)
{
	if (!theDef->mTracks.tracks) return nullptr;
	return (Nano7Def*)*(void**)((char*)theDef->mTracks.tracks - sizeof(void*));
}

void Nano7FreeN7R(ReanimatorDefinition* theDef)
{
	if (theDef->mReanimAtlas) { delete theDef->mReanimAtlas; theDef->mReanimAtlas = nullptr; }
	if (Nano7Def* d = Nano7DefOf(theDef)) operator delete[]((char*)d);
	theDef->mTracks.tracks = nullptr;
	theDef->mTracks.count = 0;
}

/* ---- exporter (repacker step, run on the box from the user's own pak) ---- */
static std::unordered_map<Image*, std::string>* gImgNames;
static std::unordered_map<_Font*, std::string>* gFontNames;
void Nano7NoteImageName(Image* theImage, const std::string& theName) { if (gImgNames && theImage) (*gImgNames)[theImage] = theName; }
void Nano7NoteFontName(_Font* theFont, const std::string& theName) { if (gFontNames && theFont) (*gFontNames)[theFont] = theName; }

static int16_t q8(float v, int& clamps) { float s = std::round(v * 8.0f); if (s > 32767) { s = 32767; clamps++; } if (s < -32768) { s = -32768; clamps++; } return (int16_t)s; }

int Nano7ExportN7R(const char* theOutDir)
{
	gImgNames = new std::unordered_map<Image*, std::string>();
	gFontNames = new std::unordered_map<_Font*, std::string>();
	bool oldN7R = gNano7N7R, oldCompact = gNano7CompactReanim;
	gNano7N7R = false; gNano7CompactReanim = false; gNano7AllowCompile = true;
	int count = 0, clamps = 0; double maxErrT = 0, maxErrH = 0; size_t rawBytes = 0, outBytes = 0;
	for (unsigned k = 0; k < gReanimationParamArraySize; k++) {
		const ReanimationParams& p = gReanimationParamArray[k];
		ReanimatorDefinition def;
		if (!ReanimationLoadDefinition(p.mReanimFileName, &def)) { fprintf(stderr, "N7R export: failed %s\n", p.mReanimFileName); continue; }
		std::vector<std::string> imgs, fonts, texts{""};
		std::map<std::string, uint16_t> imgId, fontId, textId{{"", 0}};
		auto idOf = [](std::map<std::string, uint16_t>& m, std::vector<std::string>& v, const std::string& s) { auto it = m.find(s); if (it != m.end()) return it->second; uint16_t id = (uint16_t)v.size(); m[s] = id; v.push_back(s); return id; };
		struct TrackOut { std::string name; std::vector<uint16_t> idx; std::vector<Nano7Tf> rec; };
		std::vector<TrackOut> to(def.mTracks.count);
		for (int t = 0; t < def.mTracks.count; t++) {
			ReanimatorTrack& tr = def.mTracks.tracks[t];
			to[t].name = tr.mName ? tr.mName : "";
			std::map<std::string, uint16_t> seen;
			for (int f = 0; f < tr.mTransforms.count; f++) {
				ReanimatorTransform& x = tr.mTransforms.mTransforms[f];
				rawBytes += 44;
				Nano7Tf r{};
				r.tx = q8(x.mTransX, clamps); r.ty = q8(x.mTransY, clamps);
				r.kx = f2h(x.mSkewX); r.ky = f2h(x.mSkewY); r.sx = f2h(x.mScaleX); r.sy = f2h(x.mScaleY); r.fr = f2h(x.mFrame); r.al = f2h(x.mAlpha);
				maxErrT = std::max(maxErrT, (double)std::fabs(r.tx * 0.125f - x.mTransX));
				for (float v : {x.mSkewX, x.mSkewY, x.mScaleX, x.mScaleY, x.mAlpha}) { float e = std::fabs(h2f(f2h(v)) - v); if (std::fabs(v) < 1000) maxErrH = std::max(maxErrH, (double)(std::fabs(v) > 1 ? e / std::fabs(v) : e)); }
				if (x.mImage) { auto it = gImgNames->find(x.mImage); if (it == gImgNames->end()) { fprintf(stderr, "N7R export: unnamed image in %s\n", p.mReanimFileName); r.img = 0xFFFF; } else r.img = idOf(imgId, imgs, it->second); } else r.img = 0xFFFF;
				if (x.mFont) { auto it = gFontNames->find(x.mFont); r.font = it == gFontNames->end() ? 0xFF : (uint8_t)idOf(fontId, fonts, it->second); } else r.font = 0xFF;
				r.txt = idOf(textId, texts, x.mText ? x.mText : "");
				std::string key((const char*)&r, sizeof r);
				auto it = seen.find(key);
				if (it == seen.end()) { uint16_t id = (uint16_t)to[t].rec.size(); seen[key] = id; to[t].rec.push_back(r); to[t].idx.push_back(id); }
				else to[t].idx.push_back(it->second);
			}
		}
		std::string out = std::string(theOutDir) + "/" + p.mReanimFileName + ".n7r";
		for (size_t i = 1; i < out.size(); i++) if (out[i] == '/') { std::string dir = out.substr(0, i); mkdir(dir.c_str(), 0755); }
		FILE* f = fopen(out.c_str(), "wb");
		if (!f) { fprintf(stderr, "N7R export: cannot write %s\n", out.c_str()); ReanimationFreeDefinition(&def); continue; }
		auto w16 = [&](uint16_t v) { fwrite(&v, 2, 1, f); };
		uint32_t magic = 0x3152374E, blob = 0;
		for (auto* v : {&imgs, &fonts, &texts}) for (auto& s : *v) blob += (uint32_t)s.size() + 1;
		blob -= 0;  /* image/font names are not kept in the block; the loader only counts texts + track names */
		uint32_t blobTexts = 0; for (auto& s : texts) blobTexts += (uint32_t)s.size() + 1;
		fwrite(&magic, 4, 1, f); fwrite(&def.mFPS, 4, 1, f);
		w16((uint16_t)def.mTracks.count); w16((uint16_t)imgs.size()); w16((uint16_t)fonts.size()); w16((uint16_t)texts.size());
		fwrite(&blobTexts, 4, 1, f);
		for (auto* v : {&imgs, &fonts, &texts}) for (auto& s : *v) { w16((uint16_t)s.size()); fwrite(s.data(), 1, s.size(), f); }
		for (auto& t : to) {
			w16((uint16_t)t.name.size()); fwrite(t.name.data(), 1, t.name.size(), f);
			w16((uint16_t)t.idx.size()); w16((uint16_t)t.rec.size());
			fwrite(t.idx.data(), 2, t.idx.size(), f); fwrite(t.rec.data(), sizeof(Nano7Tf), t.rec.size(), f);
		}
		outBytes += ftell(f);
		fclose(f);
		ReanimationFreeDefinition(&def);
		count++;
	}
	fprintf(stderr, "N7R export: %d files, raw transforms %zu bytes -> %zu bytes on disk, translation clamps %d, max translation error %.4f px, max half error %.5f\n",
		count, rawBytes, outBytes, clamps, maxErrT, maxErrH);
	gNano7N7R = oldN7R; gNano7CompactReanim = oldCompact; gNano7AllowCompile = false;
	return count;
}

/* ---- per-level freeing ---- */
static std::unordered_set<MemoryImage*> gSanded;
void Nano7NoteSanded(MemoryImage* theImage) { gSanded.insert(theImage); }
/* animation definitions no live Reanimation uses (Crazy Dave after his intro, the loading-screen roll) */
#ifdef NANO7_DEVICE
extern "C" void n7p_step(const char* what);
extern "C" uint32_t n7p_ms(void);
#endif
void Nano7CrumbReanim(const char* theFile)
{
#ifdef NANO7_DEVICE
	static uint32_t sLast; static int sAny;
	uint32_t now = n7p_ms();
	if (sAny && now - sLast < 400) return;
	sAny = 1; sLast = now;
	char m[120]; snprintf(m, sizeof m, "reanim: loading %s", theFile ? theFile : "?");
	n7p_step(m);
#else
	(void)theFile;
#endif
}

void Nano7FreeUnusedDefs(void* theApp)
{
	LawnApp* app = (LawnApp*)theApp;
	if (!getenv("NANO7_LEVEL_FREE")) return;
	std::vector<bool> used(gReanimatorDefCount, false);
	for (Reanimation* aReanim : app->mEffectSystem->mReanimationHolder->mReanimations)
		if (aReanim->mDefinition) used[aReanim->mDefinition - gReanimatorDefArray.get()] = true;
	int freed = 0;
	for (unsigned i = 0; i < gReanimatorDefCount; i++)
		if (!used[i] && i != (unsigned)ReanimationType::REANIM_ZOMBIE && gReanimatorDefArray[i].mTracks.tracks) { ReanimationFreeDefinition(&gReanimatorDefArray[i]); freed++; }
	fprintf(stderr, "NANO7 start-of-play free: %d animation defs\n", freed);
}

#include "n7fx.h"
void Nano7LevelFree(void* theApp)
{
	LawnApp* app = (LawnApp*)theApp;
	if (!getenv("NANO7_LEVEL_FREE")) return;
	Nano7FxLevelLog();    /* what the effect trim left out on that level */
	Nano7BakeFreeAll();   /* the baked plant pictures go with the level */
	/* 1) animation definitions with no live Reanimation. Zombie.reanim (one 227 KB block, in every level) stays:
	   loading it again at the next level failed in the dry run when the arena was fragmented (worlds 4 and 5) */
	std::vector<bool> used(gReanimatorDefCount, false);
	for (Reanimation* aReanim : app->mEffectSystem->mReanimationHolder->mReanimations)
		if (aReanim->mDefinition) used[aReanim->mDefinition - gReanimatorDefArray.get()] = true;
	int freed = 0;
	for (unsigned i = 0; i < gReanimatorDefCount; i++)
		if (!used[i] && i != (unsigned)ReanimationType::REANIM_ZOMBIE && gReanimatorDefArray[i].mTracks.tracks) { ReanimationFreeDefinition(&gReanimatorDefArray[i]); freed++; }
	/* 2) textures that can be rebuilt from the pak (bits already purged, file path known) */
	int tex = 0;
	std::vector<MemoryImage*> imgs(app->mMemoryImageSet.begin(), app->mMemoryImageSet.end());
	for (MemoryImage* im : imgs)
		if (im->mRenderData && im->mBits == nullptr && im->mColorIndices == nullptr && !im->mFilePath.empty() && im->mPurgeBits) {
			app->Remove3DData(im);
			if (gSanded.count(im)) im->mRenderFlags |= RENDERIMAGEFLAG_SANDING;   /* sand again when it is rebuilt */
			tex++;
		}
	/* 3) cached plant / mower / zombie frames: redrawn on demand by ReanimatorCache */
	int cached = 0;
	if (getenv("NANO7_FREE_CACHE") && app->mReanimatorCache) {
		ReanimatorCache* c = app->mReanimatorCache.get();
		for (auto& p : c->mPlantImages) if (p) { p.reset(); cached++; }
		for (auto& p : c->mLawnMowers) if (p) { p.reset(); cached++; }
		for (auto& p : c->mZombieImages) if (p) { p.reset(); cached++; }
		while (c->mImageVariationList.mSize != 0) {
			ReanimCacheImageVariation v = c->mImageVariationList.RemoveHead();
			delete v.mImage; cached++;
		}
	}
	fprintf(stderr, "NANO7 level free: %d animation defs, %d textures, %d cached frames\n", freed, tex, cached);
}

#ifdef NANO7_DEVICE
#include "n7dev.h"
/* on quit every texture goes (the baked pictures, then every image's GL data), so the GL context the resident
 * keeps after the app holds nothing of ours; the arena block is the resident's to free */
void Nano7QuitFree(void* theApp)
{
	LawnApp* app = (LawnApp*)theApp;
	Nano7BakeFreeAll();
	int tex = 0;
	std::vector<MemoryImage*> imgs(app->mMemoryImageSet.begin(), app->mMemoryImageSet.end());
	for (MemoryImage* im : imgs)
		if (im->mRenderData) { app->Remove3DData(im); tex++; }
	char m[120]; snprintf(m, sizeof m, "nano7 quit: GL data of %d images released; textures left %u in %u", tex,
		(unsigned)n7_stats.tex_bytes, (unsigned)n7_stats.tex_count);
	n7p_log(m);
}
#endif
