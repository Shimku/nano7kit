/* nano7 PvZ device layer, engine side: images from .n7t headers, whole-file reads for the pak interface, CPU-pixel
 * requests that the device cannot honour (counted), and the memory readout drawn in the panel's spare strip.
 * Part of the nano7kit PvZ port; links against PvZ-Portable (LGPL-3.0-or-later, wszqkzqk). */
#include "SexyAppBase.h"
#include "graphics/GLImage.h"
#include "graphics/GLInterface.h"
#include "graphics/Graphics.h"
#include "graphics/Font.h"
#include "paklib/PakInterface.h"
#include "Resources.h"
#include "n7dev.h"
#include "n7alloc.h"
#include <cstring>
#include <cstdio>
#include <strings.h>
#include <string>
using namespace Sexy;

extern "C" { n7_stats_t n7_stats; }

static std::string Norm(const std::string& theName)
{
	std::string p = theName;
	for (char& c : p) { if (c == '\\') c = '/'; else if (c >= 'A' && c <= 'Z') c = (char)(c - 'A' + 'a'); }
	while (p.size() > 2 && p[0] == '.' && p[1] == '/') p.erase(0, 2);
	return p;
}
std::string Nano7TexPath(const std::string& theName)
{
	std::string p = Norm(theName);
	size_t dot = p.rfind('.'), sl = p.rfind('/');
	if (dot != std::string::npos && (sl == std::string::npos || dot > sl)) {
		std::string e = p.substr(dot);
		if (e == ".png" || e == ".jpg" || e == ".jpeg" || e == ".gif" || e == ".bmp" || e == ".tga") p.resize(dot);
	}
	return p + ".n7t";
}
uint8_t* Nano7UploadBuffer();

GLImage* Nano7DevGetImage(SexyAppBase* theApp, const std::string& theFileName)
{
	std::string p = Nano7TexPath(theFileName);
	uint8_t* b = Nano7UploadBuffer();
	if (!b) return nullptr;
	if (n7p_read_head(p.c_str(), b, 16) < 16 || memcmp(b, "N7T1", 4) != 0) return nullptr;
	uint16_t h[6]; memcpy(h, b + 4, 12);
	GLImage* im = new GLImage(theApp->mGLInterface.get());
	im->mWidth = h[0]; im->mHeight = h[1];
	im->mN7TW = h[2]; im->mN7TH = h[3]; im->mN7Fmt = (uint8_t)h[4];
	im->mHasTrans = h[4] != 1; im->mHasAlpha = h[4] == 3;
	im->mN7T = true;
	im->mFilePath = theFileName;
	im->mPurgeBits = true;
	n7_stats.img_n7t++;
	return im;
}

void Nano7DevBitsWanted(MemoryImage* theImage)
{
	n7_stats.bits_wanted++;
	n7_stats.bits_wanted_bytes += (uint32_t)theImage->mWidth * theImage->mHeight * 4;
	if (n7_stats.bits_wanted <= 24) {
		char m[200]; snprintf(m, sizeof m, "nano7 CPU pixels asked of texture-only image %s (%dx%d), given blank", theImage->mFilePath.c_str(), theImage->mWidth, theImage->mHeight);
		n7p_log(m);
	}
}
void Nano7DevDecoded(int w, int h, const std::string& theName)
{
	n7_stats.img_decoded++;
	n7_stats.img_decoded_bytes += (uint32_t)w * h * 4;
	char m[200]; snprintf(m, sizeof m, "nano7 decoded on the CPU: %s (%dx%d)", theName.c_str(), w, h); n7p_log(m);
}

// a path inside the data folder, relative to it ("" if it is outside)
static bool Nano7Rel(const char* theFileName, std::string& theRel)
{
	std::string p = theFileName; for (char& c : p) if (c == '\\') c = '/';
	std::string d = n7p_data_dir();
	if (p.size() > d.size() && p[d.size()] == '/' && strncasecmp(p.c_str(), d.c_str(), d.size()) == 0) p.erase(0, d.size() + 1);
	else if (!p.empty() && p[0] == '/') return false;   // somewhere else: not ours
	theRel = Norm(p);
	return true;
}
PFILE* Nano7DevOpen(const char* theFileName, const char* theAccess)
{
	if (strchr(theAccess, 'w') || strchr(theAccess, 'a') || strchr(theAccess, '+')) return nullptr;
	std::string p;
	if (!Nano7Rel(theFileName, p)) return nullptr;
	uint32_t size = n7p_size(p.c_str());
	if (size == 0 && !n7p_exists_rel(p.c_str())) return nullptr;
	uint8_t* d = (uint8_t*)malloc(size + 8192 + 1);
	if (!d) { n7p_log("nano7 out of memory reading a file"); return nullptr; }
	int got = n7p_read_into(p.c_str(), d, size);
	if (got < 0) { free(d); return nullptr; }
	if ((uint32_t)got < size) size = (uint32_t)got;
	uint8_t* s = (uint8_t*)realloc(d, size + 1);
	if (s) d = s;
	d[size] = 0;
	n7_stats.file_opens++; n7_stats.file_read_bytes += size;
	if (size > n7_stats.file_biggest) n7_stats.file_biggest = size;
	PFILE* f = new PFILE;
	f->mRecord = nullptr; f->mFP = nullptr; f->mPos = 0;
#ifdef LOW_MEMORY
	f->mBufferStart = 0; f->mBufferLen = 0;
#endif
	f->mN7 = true; f->mN7Data = d; f->mN7Size = (int)size;
	return f;
}

/* ---- memory readout: off unless debug_overlay.txt is in the data folder (earlier builds showed it always), drawn over the
 * top-left of the game now that the game fills the panel; and log.txt lines ---- */
static uint32_t sHudLast, sHudFrames, sHudFps;
static char sHud[8][24];
static int sHudOn = -1;
static void Mb(char* o, size_t n, const char* k, double v) { snprintf(o, n, "%s %.2fM", k, v / 1048576.0); }
void Nano7HudUpdate()
{
	n7_arena_stats_t a; n7_arena_stats(&a);
	Mb(sHud[0], 24, "HEAP", (double)a.used);
	Mb(sHud[1], 24, "HIGH", (double)a.high_water);
	Mb(sHud[2], 24, "OF", (double)a.size);
	Mb(sHud[3], 24, "TEX", (double)n7_stats.tex_bytes);
	snprintf(sHud[4], 24, "TRI %u/%u", (unsigned)n7_stats.tris_frame, (unsigned)n7_stats.tris_need);
	uint32_t f = n7p_os_free();
	if (f) Mb(sHud[5], 24, "OS", (double)f); else snprintf(sHud[5], 24, "OS ?");
	snprintf(sHud[6], 24, "FPS %u", (unsigned)sHudFps);
	snprintf(sHud[7], 24, "CPU %u", (unsigned)n7_stats.bits_wanted);
}
void Nano7HudDraw(Graphics* g)
{
	if (sHudOn < 0) sHudOn = n7p_exists_rel("debug_overlay.txt") ? 1 : 0;
	sHudFrames++;
	uint32_t now = n7p_ms();
	if (now - sHudLast >= 1000) { sHudFps = sHudFrames * 1000 / (now - sHudLast ? now - sHudLast : 1); sHudFrames = 0; sHudLast = now; Nano7HudUpdate(); }
	if (!sHudOn) return;
	_Font* f = FONT_DWARVENTODCRAFT18 ? FONT_DWARVENTODCRAFT18 : FONT_BRIANNETOD16;
	if (!f) return;
	g->SetFont(f);
	g->SetColor(Color(255, 255, 120));
	for (int i = 0; i < 8; i++) g->DrawString(sHud[i], 8, 110 + i * 36);
}
