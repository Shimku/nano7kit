// plants, lawn mowers and zombies drawn as one baked picture (two triangles) instead of 10 to 40 animated parts.
// Part of the nano7kit PvZ port; links against PvZ-Portable (LGPL-3.0-or-later, wszqkzqk).
#pragma once
class Reanimation;
namespace Sexy { class Graphics; }
// draws theReanim's normal render group from a baked picture (and its attachments as usual); false: draw it as before
bool Nano7BakedDraw(Reanimation* theReanim, Sexy::Graphics* g);
void Nano7BakeFreeAll();   // between levels, with the level's textures
void Nano7BakeFrameEnd();
void Nano7BakeInit();      // at engine start: the picture objects
bool Nano7BakeOn();        // baking is on (plants then do not blink)
// a reanimation drawn for the cache (seed packets, the award packet, the seed chooser) is not baked inside the
// draw any more: the draw names what it is (type, track, variation), the picture is made at the frame end from a fresh
// reanimation set up the same way (Nano7CacheBakeBuild in n7cache_dev.inc), and until then that one draw is skipped
void Nano7CacheDrawBegin(int theType, const char* theTrack, int theVariation);
void Nano7CacheDrawEnd();
void Nano7BakeOnly(Reanimation* theReanim);                                // makes its picture now if it has none
void Nano7CacheBakeBuild(int theType, const char* theTrack, int theVariation);   // n7cache_dev.inc
