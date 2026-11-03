// baked plants. The iPod's GL driver can only take a few hundred triangles a frame, and a plant is 10 to
// 40 animated parts of two triangles each (late 1-2: 938 of the 1,568 triangles asked for were plant parts). A plant's
// normal render group is composited here on the CPU, once per look, from the same part pictures (.n7t, made from the
// player's own game files at install time) with the same transforms DrawTrack uses, into one small 4444 picture that
// is drawn as one quad.
// (an earlier build rebooted the iPod after the 2nd-3rd zombie of 1-1: a new texture for every 4th walk frame, 51 made in 20 s):
// every look is a STILL picture. Plants: the first frame of the animation playing, blinking off, attachments (the
// peashooter's head) held at that frame too. Zombies: the first frame of their animation (they slide); a death or
// other play-once animation shows its first then its last frame; zombiewalk.txt brings back 3 walk poses. A new
// animation, a swapped part picture (wall-nut cracks, a lost arm) or a hidden part is a new picture.
// Fewer looks: hidden tracks count only as hidden, the random zombie tongue is left out, walk / walk2 / eat share
// anim_walk's first frame, a fading track colour has 3 steps (1-10: 58 looks; an earlier build made 142 and more as they walked).
// Hard caps: 64 pictures and 320 KB counted as GL stores them (NPOT rows rounded to even, or POT) plus 1 KB a texture,
// least recently used out first (its GL texture deleted, its object reused). New pictures are made after the frame
// is drawn, at most 3 a frame; none while the OS heap is under 1.25 MB (then the parts, as before baking). Scratch is fixed
// (the upload buffer and a 48 KB ring), so baking takes nothing from the arena. nobake.txt turns it all off,
// nobakez.txt only for zombies.
#include "PvzpLib/Reanimator.h"
#include "PvzpLib/Attachment.h"
#include "PvzpLib/FilterEffect.h"
#include "Lawn/Plant.h"
#include "Lawn/Zombie.h"
#include "ConstEnums.h"
#include "graphics/GLImage.h"
#include "graphics/GLInterface.h"
#include "graphics/Graphics.h"
#include "SexyAppBase.h"
#include "LawnApp.h"
#include "PvzpLib/EffectSystem.h"
#include "n7bake.h"
#include "n7dev.h"
#include <cmath>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <string>
using namespace Sexy;

std::string Nano7TexPath(const std::string& theName);   // n7engine.cpp
extern "C" char* n7_getenv(const char* name);
extern int gNano7Npot;   // n7gl_dev_a.inc: NPOT textures allowed

static const float kPlantScale = 0.4f;    // texels per game unit: the same as every other picture (repack SCALE)
static const float kZombieScale = 0.32f;  // zombies a little less, so most fit a 32x64 texture
static const uint32_t kBakeBytesCap = 320u * 1024u;   // GL bytes of baked pictures plus kTexCost each
static const uint32_t kTexCost = 1024u;               // per texture (driver records; an estimate)
static const int kBakeMax = 64;                       // 112 -> 64 picture objects (1-10 has 58 looks)
static const uint32_t kOsBakeFloor = 1280u * 1024u;   // OS heap: no new pictures below this
static const int kMaxTexels = 4096;                   // the largest picture (64x64 texels)
// scratch: the texture upload buffer (72 KB, already there): the picture at its start, the 16-bit sums at 16 KB
uint8_t* Nano7UploadBuffer();   // n7gl_dev_b.inc
static GLImage* sSpare[kBakeMax]; static int sSpareCount;   // evicted pictures, kept for reuse (no heap churn)
static int sWalk = -1;                                // zombiewalk.txt: 3 walk poses (off: one still pose)
static const int kZombieBakesPerFrame = 3;

struct BakeEntry {
	uint32_t key, skey; const void* def; GLImage* img; uint32_t bytes, used, frameUsed; int frame; float bx, by; int bw, bh;
};
static BakeEntry sBake[kBakeMax];
void GfxFlushForBake();   // n7gl_dev_b.inc: draw what is batched before textures go
static int sBakeCount; static uint32_t sBakeBytes, sBakeClock, sBakeMade, sBakeFailed, sFrame, sFrameBakes, sRefused;
static signed char sBakeType[1024];   // per ReanimationType: 0 unknown, 1 plant or mower, 2 zombie, -1 never
static int sBakeOn = -1;
struct Req { unsigned id; uint32_t key; int kind; };   // by ReanimationID: a queued one may be gone by then
static Req sQ[kZombieBakesPerFrame]; static int sQn;
struct LastLook { const Reanimation* r; uint32_t key; };
static LastLook sLast[256];
static uint32_t sNever[64]; static int sNeverAt, sNeverN;   // looks that cannot be baked: drawn as parts
static inline LastLook& LastFor(const Reanimation* r) { uintptr_t h = (uintptr_t)r; h ^= h >> 9; return sLast[(h >> 4) & 255]; }

static int BakeWanted(ReanimationType t)
{
	if (sBakeOn < 0) sBakeOn = (n7_getenv("NANO7_NOBAKE") || n7p_exists_rel("nobake.txt")) ? 0 : 1;   // nobake.txt: parts, as before baking
	if (!sBakeOn || (int)t < 0 || (int)t >= 1024) return 0;
	if (!sBakeType[t]) {
		int b = t == ReanimationType::REANIM_LAWNMOWER ? 1 : 0;
		for (int i = 0; i < (int)SeedType::NUM_SEED_TYPES && !b; i++)
			if (gPlantDefs[i].mReanimationType == t) b = 1;
		for (int i = 0; i < (int)NUM_ZOMBIE_TYPES && !b; i++)
			if (gZombieDefs[i].mReanimationType == t) b = 2;
		if (t == ReanimationType::REANIM_ZOMBIE) b = 2;
		static int sNoZ = -1;
		if (sNoZ < 0) sNoZ = n7p_exists_rel("nobakez.txt") ? 1 : 0;   // nobakez.txt: zombies as parts again
		if (b == 2 && sNoZ) b = 0;
		sBakeType[t] = b ? (signed char)b : -1;
	}
	return sBakeType[t] > 0 ? sBakeType[t] : 0;
}

// texel (x, y) of a part picture as straight RGBA 0..1
static inline void Texel(const uint16_t* px, int tw, int fmt, int x, int y, float* o)
{
	uint16_t v = px[(size_t)y * tw + x];
	if (fmt == 1) { o[0] = ((v >> 11) & 31) / 31.0f; o[1] = ((v >> 5) & 63) / 63.0f; o[2] = (v & 31) / 31.0f; o[3] = 1; }
	else if (fmt == 2) { o[0] = ((v >> 11) & 31) / 31.0f; o[1] = ((v >> 6) & 31) / 31.0f; o[2] = ((v >> 1) & 31) / 31.0f; o[3] = (float)(v & 1); }
	else { o[0] = ((v >> 12) & 15) / 15.0f; o[1] = ((v >> 8) & 15) / 15.0f; o[2] = ((v >> 4) & 15) / 15.0f; o[3] = (v & 15) / 15.0f; }
}

// a part picture's texels: the .n7t file (all its bands) into one buffer, or a CPU picture's bits
struct Part { uint16_t* px = nullptr; int tw = 0, th = 0, fmt = 3; int w = 0, h = 0; bool own = false; const uint32_t* bits = nullptr; };
// the part pictures recently read stay in one fixed ring buffer, oldest out first (1-10's zombies and plants use
// about 61 KB of parts). Files are read through the upload buffer's last 24 KB (reads are whole 512-byte sectors)
// and kept at their exact size. Fixed buffers, not the arena, so baking does not fragment the game's heap.
static const uint32_t kRing = 48u * 1024u;
static uint8_t sRing[kRing] __attribute__((aligned(8)));
struct PartCache { const Image* img; uint32_t off, len; };
static const int kParts = 192;
static PartCache sParts[kParts]; static int sPartCount; static uint32_t sRingHead, sRingUsed;
static void PartCacheClear() { sPartCount = 0; sRingHead = 0; sRingUsed = 0; }
static const uint32_t kStageAt = 48u * 1024u, kStage = 24u * 1024u;   // in the upload buffer (72 KB)
static bool LoadPartFile(MemoryImage* mi, Part& p, uint8_t* o)
{
	int twp = 1; while (twp < p.tw) twp <<= 1;
	int R = 1; while (R * 2 * twp <= 32768) R *= 2;   // the repacker's band rule (n7gl_dev_b.inc)
	if (R > p.th) R = p.th;
	std::string path = Nano7TexPath(mi->mFilePath);
	p.px = (uint16_t*)o;
	for (int i = 0, row = 0; row < p.th; i++, row += R) {
		int rows = std::min(R, p.th - row);
		uint32_t want = (uint32_t)p.tw * rows * 2, head = i == 0 ? 16 : 0;
		std::string f = i == 0 ? path : path + "." + std::to_string(i);
		int got = n7p_read_into(f.c_str(), o, want + head);
		if (got < (int)(want + head)) return false;
		if (head) memmove(o, o + head, want);
		o += want;
	}
	return true;
}
static bool LoadPart(Image* theImage, Part& p)
{
	MemoryImage* mi = dynamic_cast<MemoryImage*>(theImage);
	if (!mi) return false;
	p.w = mi->mWidth; p.h = mi->mHeight;
	if (mi->mN7Pixels) { p.px = (uint16_t*)mi->mN7Pixels; p.tw = mi->mN7TW; p.th = mi->mN7TH; p.fmt = mi->mN7Fmt; return true; }
	if (!mi->mN7T) {
		if (!mi->mBits || mi->mWidth <= 0) return false;
		p.bits = mi->mBits.get(); p.tw = mi->mWidth; p.th = mi->mHeight; return true;
	}
	p.tw = mi->mN7TW; p.th = mi->mN7TH; p.fmt = mi->mN7Fmt;
	if (p.tw <= 0 || p.th <= 0) return false;
	for (int i = 0; i < sPartCount; i++) if (sParts[i].img == theImage) { p.px = (uint16_t*)(sRing + sParts[i].off); return true; }
	uint32_t len = (uint32_t)p.tw * p.th * 2, need = len + 16 + 512;   // reads are whole 512-byte sectors (n7p_read_into)
	uint8_t* stage = Nano7UploadBuffer();
	if (!stage) return false;
	if (need > kStage || len > kRing / 4) {   // a big part: its own block, not kept
		uint8_t* o = (uint8_t*)malloc(need);
		if (!o) return false;
		p.own = true;
		if (!LoadPartFile(mi, p, o)) { free(o); p.px = nullptr; p.own = false; return false; }
		return true;
	}
	if (!LoadPartFile(mi, p, stage + kStageAt)) { p.px = nullptr; return false; }
	uint32_t slot = (len + 7) & ~7u;
	if (sRingHead + slot > kRing) {   // wrap: what lies past the head goes
		for (int i = 0; i < sPartCount; ) if (sParts[i].off >= sRingHead) sParts[i] = sParts[--sPartCount]; else i++;
		sRingHead = 0;
	}
	for (int i = 0; i < sPartCount; ) {   // and what the new part overwrites
		const PartCache& c = sParts[i];
		if (c.off < sRingHead + slot && c.off + c.len > sRingHead) sParts[i] = sParts[--sPartCount]; else i++;
	}
	if (sPartCount >= kParts) PartCacheClear();
	memcpy(sRing + sRingHead, p.px, len);
	p.px = (uint16_t*)(sRing + sRingHead);
	sParts[sPartCount++] = { theImage, sRingHead, slot };
	sRingHead += slot; sRingUsed = std::max(sRingUsed, sRingHead);
	return true;
}
static void FreePart(Part& p) { if (p.own) free(p.px); p.px = nullptr; }
static void Sample(const Part& p, float u, float v, float* o)   // u, v in the picture's logical units; bilinear
{
	float x = u * p.tw / p.w - 0.5f, y = v * p.th / p.h - 0.5f;
	int x0 = (int)floorf(x), y0 = (int)floorf(y); float fx = x - x0, fy = y - y0;
	float acc[4] = { 0, 0, 0, 0 };
	for (int k = 0; k < 4; k++) {
		int xx = x0 + (k & 1), yy = y0 + (k >> 1);
		float wgt = ((k & 1) ? fx : 1 - fx) * ((k >> 1) ? fy : 1 - fy);
		if (wgt <= 0) continue;
		if (xx < 0) xx = 0; if (yy < 0) yy = 0; if (xx >= p.tw) xx = p.tw - 1; if (yy >= p.th) yy = p.th - 1;
		float t[4];
		if (p.bits) { uint32_t c = p.bits[(size_t)yy * p.tw + xx]; t[0] = ((c >> 16) & 255) / 255.0f; t[1] = ((c >> 8) & 255) / 255.0f; t[2] = (c & 255) / 255.0f; t[3] = (c >> 24) / 255.0f; }
		else Texel(p.px, p.tw, p.fmt, xx, yy, t);
		acc[0] += t[0] * t[3] * wgt; acc[1] += t[1] * t[3] * wgt; acc[2] += t[2] * t[3] * wgt; acc[3] += t[3] * wgt;   // premultiplied
	}
	o[0] = acc[0]; o[1] = acc[1]; o[2] = acc[2]; o[3] = acc[3];
}

// per animation definition, found once: the tongue track (left out: a random tongue doubled every zombie's
// looks) and the zombie walk / walk2 / eat animations (all drawn as the first frame of anim_walk: in a still picture
// they look the same, and that is 3 times fewer looks)
struct DefInfo { const void* def; short tongue, walk, walk2, eat; };
static DefInfo sDefs[64]; static int sDefCount;
static const DefInfo& InfoFor(Reanimation* r)
{
	for (int i = 0; i < sDefCount; i++) if (sDefs[i].def == r->mDefinition) return sDefs[i];
	DefInfo d = { r->mDefinition, -1, -1, -1, -1 };
	const int n = r->mDefinition->mTracks.count;
	for (int i = 0; i < n; i++) {
		const char* nm = r->mDefinition->mTracks.tracks[i].mName;
		if (!nm) continue;
		if (!strcasecmp(nm, "anim_tongue")) d.tongue = (short)i;
		short* f = !strcasecmp(nm, "anim_walk") ? &d.walk : !strcasecmp(nm, "anim_walk2") ? &d.walk2 : !strcasecmp(nm, "anim_eat") ? &d.eat : nullptr;
		if (f) { int st = 0, ct = 0; r->GetFramesForLayer(nm, st, ct); *f = (short)st; }
	}
	if (sDefCount == 64) sDefCount = 0;   // (more than 64 kinds in one level does not happen; start over if it does)
	sDefs[sDefCount++] = d;
	return sDefs[sDefCount - 1];
}
static inline bool Shown(Reanimation* r, const DefInfo& d, int i)
{
	return r->mTrackInstances[i].mRenderGroup == RENDER_GROUP_NORMAL && i != d.tongue;
}
static inline int Q3(int v) { return v < 64 ? 0 : v < 192 ? 128 : 255; }   // a fading track colour in 3 steps
static inline Color QColor(const Color& c) { return Color(Q3(c.mRed), Q3(c.mGreen), Q3(c.mBlue), Q3(c.mAlpha)); }

// what a track of this reanimation shows at the baked frame: false if nothing
struct TrackLook { Image* img; Rect src; SexyMatrix3 m; Color color; };
static int LookAt(Reanimation* r, int i, ReanimatorFrameTime* ft, TrackLook& L)
{
	ReanimatorTrackInstance* ti = &r->mTrackInstances[i];
	ReanimatorTransform t;
	r->GetTransformAtTime(i, &t, ft);
	int frame = (int)floorf(t.mFrame + 0.5f);
	if (frame < 0) return 0;
	Image* img = ti->mImageOverride ? ti->mImageOverride : t.mImage;
	if (!img) {
		if (t.mFont && t.mText && *t.mText) return -1;   // text: not baked
		if (!strcasecmp(r->mDefinition->mTracks.tracks[i].mName, "fullscreen")) return -1;
		return 0;
	}
	if (reinterpret_cast<uintptr_t>(img) <= 1000) return -1;   // an atlas handle
	Color c = QColor(ti->mTrackColor);
	int a = (int)floorf(t.mAlpha * c.mAlpha + 0.5f); if (a > 255) a = 255;
	if (a <= 0) return 0;
	c.mAlpha = a;
	int nc = img->mNumCols > 0 ? img->mNumCols : 1;
	while (frame >= nc) frame -= nc;
	int cw = img->GetCelWidth(), ch = img->GetCelHeight();
	L.img = img; L.src = Rect(frame * cw, 0, cw, ch); L.color = c;
	Reanimation::MatrixFromTransform(t, L.m);   // see DrawTrack: the picture is centred where the track's own picture
	Image* pivot = t.mImage ? t.mImage : img;     // (the pivot) would be, so its corner sits at the track's origin
	float ox = (pivot->GetCelWidth() - cw) * 0.5f, oy = (pivot->GetCelHeight() - ch) * 0.5f;
	L.m.m02 += L.m.m00 * ox + L.m.m01 * oy; L.m.m12 += L.m.m10 * ox + L.m.m11 * oy;
	return 1;
}

static void Note()
{
	n7_stats.bakes = sBakeMade; n7_stats.bake_kept = (uint32_t)sBakeCount; n7_stats.bake_bytes = sBakeBytes; n7_stats.bake_refused = sRefused;
	if (n7_stats.bake_kept > n7_stats.bake_kept_peak) n7_stats.bake_kept_peak = n7_stats.bake_kept;
	if (sBakeBytes > n7_stats.bake_bytes_peak) n7_stats.bake_bytes_peak = sBakeBytes;
}
bool Nano7BakeOn() { return BakeWanted(ReanimationType::REANIM_LAWNMOWER) != 0; }
static void Evict(int i)
{
	BakeEntry& e = sBake[i];
	if (e.img->mRenderData) e.img->mRenderData->ReleaseTextures();   // kept for reuse, without its texture
	sSpare[sSpareCount++] = e.img;
	sBakeBytes -= e.bytes;
	sBake[i] = sBake[--sBakeCount];
	Note();
}
void Nano7BakeFreeAll()
{
	GfxFlushForBake();
	while (sBakeCount) Evict(sBakeCount - 1);   // the picture objects stay (made once, at start), without textures
	PartCacheClear();
	sQn = 0; sNeverN = sNeverAt = 0; memset(sLast, 0, sizeof sLast);
	char m[120]; snprintf(m, sizeof m, "nano7 bakes freed: %d kept, %u KB, %u made so far; textures now %u in %u",
		sBakeCount, (unsigned)(sBakeBytes >> 10), (unsigned)sBakeMade, (unsigned)n7_stats.tex_bytes, (unsigned)n7_stats.tex_count);
	n7p_log(m);
}
static void BakeQueued();
// cache draws (seed packets, the award packet) queue their picture here; made at the frame end
struct CacheReq { int type; const char* track; int var; uint32_t key; };
static CacheReq sCR[4]; static int sCRn;
static struct { bool on; int type; const char* track; int var; } sCur;
void Nano7CacheDrawBegin(int theType, const char* theTrack, int theVariation) { sCur.on = true; sCur.type = theType; sCur.track = theTrack; sCur.var = theVariation; }
void Nano7CacheDrawEnd() { sCur.on = false; }
static void BakeCacheQueued()
{
	int q = sCRn; sCRn = 0;
	for (int i = 0; i < q; i++) Nano7CacheBakeBuild(sCR[i].type, sCR[i].track, sCR[i].var);
}
void Nano7BakeFrameEnd()
{
	BakeQueued();
	BakeCacheQueued();
	sFrame++; sFrameBakes = 0;
}

void Nano7BakeInit()
{   // every picture object at start, so they sit at the bottom of the arena, not between a level's blocks
	static bool sMade;
	if (sMade || !BakeWanted(ReanimationType::REANIM_LAWNMOWER)) return;
	sMade = true;
	uint8_t* z = Nano7UploadBuffer();
	if (!z) return;
	memset(z, 0, 64);
	for (int i = 0; i < kBakeMax; i++) {   // with their texture records too (a 2x2 texture, released at once)
		GLImage* im = new GLImage(gSexyAppBase->mGLInterface.get());
		im->mWidth = im->mHeight = 2; im->mN7TW = im->mN7TH = 2; im->mN7Fmt = 3; im->mN7T = true; im->mN7Pixels = (const uint16_t*)z;
		im->mFilePath.reserve(32); im->mFilePath = "nano7bake/spare";
		gSexyAppBase->mGLInterface->CreateImageTexture(im);
		if (im->mRenderData) im->mRenderData->ReleaseTextures();
		im->mN7Pixels = nullptr;
		sSpare[sSpareCount++] = im;
	}
}

static BakeEntry* Bake(Reanimation* r, uint32_t key, uint32_t skey, int kind, ReanimatorFrameTime* ft)
{
	const int n = r->mDefinition->mTracks.count;
	const DefInfo& di = InfoFor(r);
	// the picture's box in the reanimation's own units
	float x0 = 1e9f, y0 = 1e9f, x1 = -1e9f, y1 = -1e9f; int shown = 0;
	for (int i = 0; i < n; i++) {
		if (!Shown(r, di, i)) continue;
		TrackLook L; int k = LookAt(r, i, ft, L);
		if (k < 0) return nullptr;
		if (!k) continue;
		shown++;
		for (int c = 0; c < 4; c++) {
			float u = (c & 1) ? (float)L.src.mWidth : 0.0f, v = (c & 2) ? (float)L.src.mHeight : 0.0f;
			float x = L.m.m00 * u + L.m.m01 * v + L.m.m02, y = L.m.m10 * u + L.m.m11 * v + L.m.m12;
			x0 = std::min(x0, x); y0 = std::min(y0, y); x1 = std::max(x1, x); y1 = std::max(y1, y);
		}
	}
	if (!shown) return nullptr;
	x0 = floorf(x0) - 1; y0 = floorf(y0) - 1; x1 = ceilf(x1) + 1; y1 = ceilf(y1) + 1;
	int bw = (int)(x1 - x0), bh = (int)(y1 - y0);
	const float kBakeScale = kind == 2 ? kZombieScale : kPlantScale;
	if (bw <= 0 || bh <= 0 || bw > 600 || bh > 600) return nullptr;
	int tw = std::max(1, (int)ceilf(bw * kBakeScale)), th = std::max(1, (int)ceilf(bh * kBakeScale));
	if (tw * th > kMaxTexels) return nullptr;   // fixed scratch (and one texture band)
	uint32_t gl;   // as Nano7DevCreateTextures stores it
	if (!gNano7Npot) { int pw = 1; while (pw < tw) pw <<= 1; int ph = 1; while (ph < th) ph <<= 1; gl = (uint32_t)pw * ph * 2; }
	else gl = (uint32_t)((tw + 1) & ~1) * th * 2;
	uint32_t bytes = gl + kTexCost;
	uint32_t os = n7p_os_free();
	if (os && os < kOsBakeFloor) { sRefused++; Note(); return nullptr; }
	uint32_t cap = kBakeBytesCap; int most = kBakeMax;
#ifdef N7_HOST_ENV
	if (const char* e = getenv("NANO7_BAKE_KB")) cap = (uint32_t)atoi(e) * 1024u;   // dry run: measuring the working set
	if (const char* e = getenv("NANO7_BAKE_MAX")) most = std::min(kBakeMax, atoi(e));
#endif
	for (;;) {   // least recently used out, never one drawn this frame
		if (sBakeBytes + bytes <= cap && sBakeCount < most) break;
		int lru = -1;
		for (int i = 0; i < sBakeCount; i++) if (sBake[i].frameUsed != sFrame && (lru < 0 || sBake[i].used < sBake[lru].used)) lru = i;
		if (lru < 0) { sRefused++; Note(); return nullptr; }
		Evict(lru);
	}
	uint8_t* scratch = Nano7UploadBuffer();
	if (!scratch) return nullptr;
	uint16_t* acc = (uint16_t*)(scratch + 16384);   // premultiplied RGBA, 0..65535
	memset(acc, 0, (size_t)tw * th * 4 * sizeof(uint16_t));
	uint16_t* out = (uint16_t*)scratch;   // read in place by Nano7DevCreateTextures
	bool ok = true;
	for (int i = 0; i < n && ok; i++) {   // tracks in draw order, each "over" what is under it
		if (!Shown(r, di, i)) continue;
		TrackLook L; if (LookAt(r, i, ft, L) <= 0) continue;
		Part p; if (!LoadPart(L.img, p)) { ok = false; break; }
		// texel centre -> reanim units -> the part's units (inverse of L.m)
		float det = L.m.m00 * L.m.m11 - L.m.m01 * L.m.m10;
		if (fabsf(det) < 1e-6f) { FreePart(p); continue; }
		float i00 = L.m.m11 / det, i01 = -L.m.m01 / det, i10 = -L.m.m10 / det, i11 = L.m.m00 / det;
		float cr = L.color.mRed / 255.0f, cg = L.color.mGreen / 255.0f, cb = L.color.mBlue / 255.0f, ca = L.color.mAlpha / 255.0f;
		// only the texels under this part
		float px0 = 1e9f, py0 = 1e9f, px1 = -1e9f, py1 = -1e9f;
		for (int c = 0; c < 4; c++) {
			float u = (c & 1) ? (float)L.src.mWidth : 0.0f, v = (c & 2) ? (float)L.src.mHeight : 0.0f;
			float x = L.m.m00 * u + L.m.m01 * v + L.m.m02, y = L.m.m10 * u + L.m.m11 * v + L.m.m12;
			px0 = std::min(px0, x); py0 = std::min(py0, y); px1 = std::max(px1, x); py1 = std::max(py1, y);
		}
		int ox0 = std::max(0, (int)floorf((px0 - x0) * kBakeScale)), oy0 = std::max(0, (int)floorf((py0 - y0) * kBakeScale));
		int ox1 = std::min(tw - 1, (int)ceilf((px1 - x0) * kBakeScale)), oy1 = std::min(th - 1, (int)ceilf((py1 - y0) * kBakeScale));
		for (int oy = oy0; oy <= oy1; oy++) for (int ox = ox0; ox <= ox1; ox++) {
			float X = x0 + (ox + 0.5f) / kBakeScale - L.m.m02, Y = y0 + (oy + 0.5f) / kBakeScale - L.m.m12;
			float u = i00 * X + i01 * Y, v = i10 * X + i11 * Y;
			if (u < 0 || v < 0 || u >= L.src.mWidth || v >= L.src.mHeight) continue;
			float s[4]; Sample(p, u + L.src.mX, v + L.src.mY, s);
			float a = s[3] * ca; if (a <= 0) continue;
			uint16_t* d = acc + ((size_t)oy * tw + ox) * 4;
			float k = 1 - a;
			float o0 = s[0] * cr * ca + d[0] * (k / 65535.0f), o1 = s[1] * cg * ca + d[1] * (k / 65535.0f);
			float o2 = s[2] * cb * ca + d[2] * (k / 65535.0f), o3 = a + d[3] * (k / 65535.0f);
			d[0] = (uint16_t)(std::min(o0, 1.0f) * 65535.0f + 0.5f); d[1] = (uint16_t)(std::min(o1, 1.0f) * 65535.0f + 0.5f);
			d[2] = (uint16_t)(std::min(o2, 1.0f) * 65535.0f + 0.5f); d[3] = (uint16_t)(std::min(o3, 1.0f) * 65535.0f + 0.5f);
		}
		FreePart(p);
	}
	if (ok) for (int i = 0; i < tw * th; i++) {   // premultiplied -> straight 4444
		const uint16_t* d = acc + (size_t)i * 4; float a = d[3] / 65535.0f;
		int R = 0, G = 0, B = 0, A = (int)(a * 15 + 0.5f);
		if (d[3] > 64) { float k = 15.0f / d[3]; R = (int)(d[0] * k + 0.5f); G = (int)(d[1] * k + 0.5f); B = (int)(d[2] * k + 0.5f); }
		R = std::min(R, 15); G = std::min(G, 15); B = std::min(B, 15); A = std::min(A, 15);
		out[i] = (uint16_t)(R << 12 | G << 8 | B << 4 | A);
	}
	if (!ok) { sBakeFailed++; return nullptr; }
	if (!sSpareCount) return nullptr;
	GLImage* img = sSpare[--sSpareCount];
	img->mWidth = bw; img->mHeight = bh;
	img->mN7TW = (uint16_t)tw; img->mN7TH = (uint16_t)th; img->mN7Fmt = 3;
	img->mHasTrans = true; img->mHasAlpha = true; img->mN7T = true; img->mN7Pixels = out; img->mN7PixelsOwned = false;
	char name[64]; snprintf(name, sizeof name, kind == 2 ? "nano7bakez/%d_%08x" : "nano7bake/%d_%08x", (int)r->mReanimationType, (unsigned)key);
	img->mFilePath = name;
	img->mBitsChangedCount++;                              // a reused picture: make its texture again
	gSexyAppBase->mGLInterface->CreateImageTexture(img);  // the texture now, from the fixed buffer
	img->mN7Pixels = nullptr;
	BakeEntry& e = sBake[sBakeCount++];
	e.key = key; e.skey = skey; e.def = r->mDefinition; e.img = img; e.bytes = bytes; e.used = ++sBakeClock; e.frameUsed = sFrame;
	e.frame = ft->mAnimFrameBeforeInt;
	e.bx = x0; e.by = y0; e.bw = bw; e.bh = bh;
	sBakeBytes += bytes; sBakeMade++; Note();
#ifdef N7_HOST_ENV
	if (getenv("NANO7_BAKELOG2")) {
		fprintf(stderr, "BAKETR %d %d:", (int)r->mReanimationType, r->mFrameStart);
		for (int i = 0; i < n; i++) { const ReanimatorTrackInstance& ti = r->mTrackInstances[i];
			if (ti.mRenderGroup != RENDER_GROUP_NORMAL || ti.mImageOverride || ti.mTrackColor.ToInt() != (int)0xffffffff)
				fprintf(stderr, " %s%s%s", r->mDefinition->mTracks.tracks[i].mName, ti.mRenderGroup != RENDER_GROUP_NORMAL ? "/h" : "", ti.mImageOverride ? "/o" : ""); }
		fprintf(stderr, "\n");
	}
	if (getenv("NANO7_BAKELOG")) fprintf(stderr, "BAKE %d %08x %08x %d %d %d %dx%d\n", (int)r->mReanimationType, (unsigned)key, (unsigned)skey, r->mFrameStart, r->mFrameCount, ft->mAnimFrameBeforeInt, tw, th);
#endif
	if (sBakeMade <= 12 || (sBakeMade & (sBakeMade - 1)) == 0) {   // the first dozen, then at each power of two
		char m[180]; snprintf(m, sizeof m, "nano7 baked %s frame %d: %dx%d texels (%u made, %u kept, %u KB as textures, parts %u KB)", name,
			ft->mAnimFrameBeforeInt, tw, th, (unsigned)sBakeMade, (unsigned)sBakeCount, (unsigned)(sBakeBytes >> 10), (unsigned)(sRingUsed >> 10));

		n7p_log(m);
	}
	return &e;
}

// the look of r now: its key (definition, pose, what each shown track shows) and the frame it is baked at
static void LookKey(Reanimation* r, int kind, ReanimatorFrameTime& ft, uint32_t& key, uint32_t& skey)
{
	const int n = r->mDefinition->mTracks.count;
	const DefInfo& di = InfoFor(r);
	if (sWalk < 0) sWalk = n7p_exists_rel("zombiewalk.txt") ? 1 : 0;
	ft.mFraction = 0; ft.mAnimFrameBeforeInt = ft.mAnimFrameAfterInt = r->mFrameStart;
	int lookStart = r->mFrameStart;
	if (kind == 2 && !sWalk && di.walk >= 0 && (r->mFrameStart == di.walk2 || r->mFrameStart == di.eat)) {   // as anim_walk
		lookStart = di.walk; ft.mAnimFrameBeforeInt = ft.mAnimFrameAfterInt = di.walk;
	}
	else if (kind == 2 && r->mFrameCount > 1) {   // zombies hold one pose (play-once: first, then last; zombiewalk.txt: 3)
		ReanimatorFrameTime now; r->GetFrameTime(&now);
		int q = now.mAnimFrameBeforeInt - r->mFrameStart, c = r->mFrameCount;
		if (q < 0) q = 0; if (q >= c) q = c - 1;
		int f = 0;
		if (r->mLoopType != ReanimLoopType::REANIM_LOOP && r->mLoopType != ReanimLoopType::REANIM_LOOP_FULL_LAST_FRAME) f = q * 2 >= c ? c - 1 : 0;
		else if (sWalk) f = (q * 3 / c) * c / 3;
		ft.mAnimFrameBeforeInt = ft.mAnimFrameAfterInt = r->mFrameStart + f;
	}
	key = 2166136261u;
	auto mix = [&key](uint32_t v) { for (int b = 0; b < 4; b++) { key = (key ^ (v & 255)) * 16777619u; v >>= 8; } };
	mix((uint32_t)(uintptr_t)r->mDefinition); mix((uint32_t)lookStart);
	for (int i = 0; i < n; i++) {   // a hidden track is only "hidden" (its picture and colour do not matter)
		const ReanimatorTrackInstance& ti = r->mTrackInstances[i];
		if (!Shown(r, di, i)) { mix(2u); continue; }
		mix(1u);
		mix((uint32_t)(uintptr_t)ti.mImageOverride);
		mix((uint32_t)QColor(ti.mTrackColor).ToInt());
	}
	skey = key;   // the look without the frame
	mix((uint32_t)ft.mAnimFrameBeforeInt);
}

// new pictures are made after the frame is drawn (Nano7BakeFrameEnd), at most 3 a frame, on a shallow stack and
// not in the middle of the frame's draw calls. Until then the reanimation shows the picture it showed last (a zombie
// losing its arm), the nearest pose of its look, or nothing for that one frame (a plant just planted).
static BakeEntry* Find(uint32_t key, const void* def)
{
	for (int i = 0; i < sBakeCount; i++) if (sBake[i].key == key && sBake[i].def == def) return &sBake[i];
	return nullptr;
}
static void BakeQueued()
{
	int q = sQn; sQn = 0;
	for (int i = 0; i < q; i++) {
		Reanimation* r = gLawnApp && gLawnApp->mEffectSystem ? gLawnApp->mEffectSystem->mReanimationHolder->mReanimations.DataArrayTryToGet(sQ[i].id) : nullptr;
		if (!r || !r->mDefinition || r->mDead) continue;
		ReanimatorFrameTime ft; uint32_t key, skey;
		LookKey(r, sQ[i].kind, ft, key, skey);
		if (key != sQ[i].key || Find(key, r->mDefinition)) continue;   // changed since, or made already
		if (!Bake(r, key, skey, sQ[i].kind, &ft)) { sNever[sNeverAt] = key; sNeverAt = (sNeverAt + 1) & 63; if (sNeverN < 64) sNeverN++; }
	}
}

bool Nano7BakedDraw(Reanimation* r, Graphics* g)
{
	int kind = BakeWanted(r->mReanimationType);
	if (!kind) return false;
	if (r->mFilterEffect != FilterEffect::FILTER_EFFECT_NONE || r->mDefinition->mReanimAtlas != nullptr) return false;
	const int n = r->mDefinition->mTracks.count;
	ReanimatorFrameTime ft; uint32_t key, skey;
	LookKey(r, kind, ft, key, skey);
	BakeEntry* e = Find(key, r->mDefinition);
	LastLook& last = LastFor(r);
	if (!e) {
		for (int i = 0; i < sNeverN; i++) if (sNever[i] == key) return false;   // cannot be baked: parts, as before baking
		auto& arr = gLawnApp->mEffectSystem->mReanimationHolder->mReanimations;
		if (arr.DataArrayGetIndex(r) >= arr.mMaxSize) {   // not a board reanimation (a picture being cached)
			// never baked inside the draw (an earlier build did, on the deepest draw chain: file reads and a GL upload under the
			// award packet's first draw). A cache draw queues it for the frame end and is skipped this once; any
			// other temporary reanimation is drawn as parts.
			if (!sCur.on) return false;
			bool queued = false;
			for (int i = 0; i < sCRn; i++) if (sCR[i].key == key) queued = true;
			if (!queued && sCRn < 4) sCR[sCRn++] = { sCur.type, sCur.track, sCur.var, key };
			return true;
		}
		else {
			bool queued = false;
			for (int i = 0; i < sQn; i++) if (sQ[i].key == key) queued = true;
			if (!queued && sQn < kZombieBakesPerFrame) sQ[sQn++] = { arr.DataArrayGetID(r), key, kind };
		}
	}
	if (!e) {
		if (last.r == r) e = Find(last.key, r->mDefinition);   // what it showed last
		if (!e) {   // the nearest pose of the same look
			int best = 1 << 30;
			for (int i = 0; i < sBakeCount; i++)
				if (sBake[i].skey == skey && sBake[i].def == r->mDefinition) {
					int d = abs(sBake[i].frame - ft.mAnimFrameBeforeInt);
					if (d < best) { best = d; e = &sBake[i]; }
				}
		}
		if (!e) return true;   // nothing yet: one frame without it
	}
	else { last.r = r; last.key = key; }
	e->frameUsed = sFrame;
	e->used = ++sBakeClock;

	Color c = r->mColorOverride;
	if (g->GetColorizeImages()) c = ColorsMultiply(c, g->GetColor());
	SexyMatrix3 m; m.LoadIdentity();
	SexyMatrix3Translation(m, e->bw * 0.5f + e->bx, e->bh * 0.5f + e->by);
	SexyMatrix3Multiply(m, r->mOverlayMatrix, m);
	SexyMatrix3Translation(m, g->mTransX, g->mTransY);
	Rect src(0, 0, e->bw, e->bh);
	if (c.mAlpha > 0) {
		r->ReanimBltMatrix(g, e->img, m, g->mClipRect, c, g->mDrawMode, src);
		if (r->mEnableExtraAdditiveDraw) {
			Color x = r->mExtraAdditiveColor; x.mAlpha = ColorComponentMultiply(x.mAlpha, c.mAlpha);
			r->ReanimBltMatrix(g, e->img, m, g->mClipRect, x, Graphics::DRAWMODE_ADDITIVE, src);
		}
		if (r->mEnableExtraOverlayDraw) {   // a white flash: drawn additive with the overlay colour (close enough)
			Color x = r->mExtraOverlayColor; x.mAlpha = ColorComponentMultiply(x.mAlpha, c.mAlpha);
			r->ReanimBltMatrix(g, e->img, m, g->mClipRect, x, Graphics::DRAWMODE_ADDITIVE, src);
		}
	}
	// attachments (the peashooter's head on its stem, effects) as DrawRenderGroup draws them
	// held where the baked frame has them (Update set them from the live frame), so the head does not sway
	for (int i = 0; i < n; i++) {
		ReanimatorTrackInstance* ti = &r->mTrackInstances[i];
		if (ti->mRenderGroup != RENDER_GROUP_NORMAL || ti->mAttachmentID == AttachmentID::ATTACHMENTID_NULL) continue;
		ReanimatorTransform t; r->GetTransformAtTime(i, &t, &ft);
		SexyTransform2D tm; Reanimation::MatrixFromTransform(t, tm);
		SexyMatrix3Multiply(tm, r->mOverlayMatrix, tm);
		SexyTransform2D base, inv; r->GetTrackBasePoseMatrix(i, base); SexyMatrix3Inverse(base, inv);
		SexyTransform2D ov = tm * inv;
		AttachmentUpdateAndSetMatrix(ti->mAttachmentID, ov);
		AttachmentDraw(ti->mAttachmentID, g, FloatRoundToInt(t.mFrame) < 0);
	}
	return true;
}

void Nano7BakeOnly(Reanimation* r)
{
	int kind = BakeWanted(r->mReanimationType);
	if (!kind || !r->mDefinition) return;
	if (r->mFilterEffect != FilterEffect::FILTER_EFFECT_NONE || r->mDefinition->mReanimAtlas != nullptr) return;
	ReanimatorFrameTime ft; uint32_t key, skey;
	LookKey(r, kind, ft, key, skey);
	if (Find(key, r->mDefinition)) return;
	for (int i = 0; i < sNeverN; i++) if (sNever[i] == key) return;
	if (!Bake(r, key, skey, kind, &ft)) { sNever[sNeverAt] = key; sNeverAt = (sNeverAt + 1) & 63; if (sNeverN < 64) sNeverN++; }
}
