/* nano7kit measurement: which images became textures, and which were drawn in the current level. */
#include "texlog.h"
#include "memtrack.h"
#include "graphics/MemoryImage.h"
#include <unordered_map>
#include <string>
#include <cmath>
#include <cstdio>
struct TexRec { std::string path; int w, h; bool alpha; int fmt; int touched; };
std::unordered_map<void*, TexRec> gNano7Tex;
int gNano7Epoch = 1;
void nano7_tex_note(void* d, Sexy::MemoryImage* im, int fmt)
{
	MT_TAG(MT_DECODE);  /* measurement bookkeeping */
	TexRec& r = gNano7Tex[d];
	r.path = im->mFilePath.empty() ? std::string("(generated)") : im->mFilePath;
	r.w = im->mWidth; r.h = im->mHeight; r.alpha = im->mHasAlpha || im->mHasTrans; r.fmt = fmt; r.touched = gNano7Epoch;
}
void nano7_tex_drop(void* d) { gNano7Tex.erase(d); }
void nano7_tex_touch(void* d) { auto it = gNano7Tex.find(d); if (it != gNano7Tex.end()) it->second.touched = gNano7Epoch; }
