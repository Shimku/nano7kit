/* nano7 compact animation format (.n7r), runtime side.
 * Each track keeps a 16-bit frame index plus a table of unique 22-byte records:
 * translation in 1/8 px (int16), skew/scale/frame/alpha as half floats, 16-bit image and text ids, 8-bit font id.
 * Part of the nano7 PvZ port work; links against PvZ-Portable (LGPL-3.0-or-later, wszqkzqk). */
#pragma once
#include <cstdint>
#include <string>
#include <cstdlib>
class ReanimatorDefinition;
class ReanimatorTransform;
namespace Sexy { class Image; class _Font; class MemoryImage; }

#pragma pack(push, 1)
struct Nano7Tf {
	int16_t  tx, ty;          /* 1/8 px */
	uint16_t kx, ky, sx, sy;  /* half floats */
	uint16_t fr, al;          /* half floats */
	uint16_t img, txt;        /* 0xFFFF = none; txt 0 = "" */
	uint8_t  font, pad;       /* 0xFF = none */
};
#pragma pack(pop)
static_assert(sizeof(Nano7Tf) == 22, "Nano7Tf must be 22 bytes");

struct Nano7Def {
	Sexy::Image** images;
	Sexy::_Font** fonts;
	const char**  texts;
	uint16_t nimg, nfont, ntext, pad;
};

extern bool gNano7N7R;
inline bool Nano7PlainAlloc() { static const bool b = getenv("NANO7_PLAIN_ALLOC") != nullptr; return b; }
ReanimatorTransform& Nano7Decode(const Nano7Def* d, const Nano7Tf& r);
bool Nano7LoadN7R(const std::string& theFileName, ReanimatorDefinition* theDef);
void Nano7FreeN7R(ReanimatorDefinition* theDef);
Nano7Def* Nano7DefOf(ReanimatorDefinition* theDef);
size_t Nano7N7RBytes(ReanimatorDefinition* theDef);
void Nano7NoteImageName(Sexy::Image* theImage, const std::string& theName);
void Nano7NoteFontName(Sexy::_Font* theFont, const std::string& theName);
int  Nano7ExportN7R(const char* theOutDir);   /* repacker step: writes every reanim as .n7r, returns count */
void Nano7LevelFree(void* theApp);
void Nano7QuitFree(void* theApp);   /* every texture, on quit */
void Nano7FreeUnusedDefs(void* theApp);
void Nano7CrumbReanim(const char* theFile);   // a breadcrumb naming the reanim, at most every 400 ms
void Nano7NoteSanded(Sexy::MemoryImage* theImage);            /* per-level freeing: unused animations and recoverable textures */
