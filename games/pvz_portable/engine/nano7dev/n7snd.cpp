#include "n7snd.h"
#include "n7dev.h"
#include <cstdio>
#include <cstring>
#include <cstdlib>
unsigned long long nano7_sound_bytes, nano7_sound_count;
using namespace Sexy;
static int sOff = -1;
static uint32_t sBusyUntil, sLastStart;
static float sMaster = 0.25f;   /* "low": a quarter of the OS player's full volume, times the game's own setting */

namespace {
class N7SoundInstance : public SoundInstance {
public:
	NanoSoundManager* m; intptr_t id; double vol = 1.0; bool released = false;
	N7SoundInstance(NanoSoundManager* mgr, intptr_t i) : m(mgr), id(i) {}
	void Release() override { delete this; }
	void SetBaseVolume(double) override {}
	void SetBasePan(int) override {}
	void AdjustPitch(double) override {}
	void SetVolume(double v) override { vol = v; }
	void SetPan(int) override {}
	bool Play(bool looping, bool autoRelease) override
	{
		bool ok = !looping && m->Play(id, vol);   /* looping sounds (none in the day levels) stay silent */
		if (autoRelease) delete this;
		return ok;
	}
	void Stop() override {}
	bool IsPlaying() override { return false; }
	bool IsReleased() override { return false; }
	double GetVolume() override { return vol; }
};
}

bool NanoSoundManager::LoadSound(intptr_t theSfxID, const std::string& theFilename)
{
	if (theSfxID < 0) return false;
	if (n7_getenv("NANO7_NO_SOUND")) return true;   /* silent build, nothing shipped, nothing looked up */
	if ((size_t)theSfxID >= mPaths.size()) { mPaths.resize(theSfxID + 1); mMs.resize(theSfxID + 1); mBase.resize(theSfxID + 1, 1.0f); }
	std::string p = theFilename;
	for (char& c : p) { if (c == '\\') c = '/'; else if (c >= 'A' && c <= 'Z') c = (char)(c - 'A' + 'a'); }
	size_t dot = p.rfind('.'), sl = p.rfind('/');
	if (dot != std::string::npos && (sl == std::string::npos || dot > sl)) p.resize(dot);
	p += ".wav";
	uint32_t n = n7p_size(p.c_str());
	if (!n) { mPaths[theSfxID].clear(); return true; }   /* no sound for it: stays silent */
	mPaths[theSfxID] = p;
	mMs[theSfxID] = (n > 44 ? n - 44 : 0) / 44 + 60;     /* 22050 Hz mono 16-bit = 44.1 bytes a ms, plus a margin */
	nano7_sound_bytes += n; nano7_sound_count++;
	return true;
}

SoundInstance* NanoSoundManager::GetSoundInstance(intptr_t theSfxID)
{
	if (theSfxID < 0 || (size_t)theSfxID >= mPaths.size() || mPaths[theSfxID].empty()) return nullptr;
	return new N7SoundInstance(this, theSfxID);
}

bool NanoSoundManager::Play(intptr_t id, double vol)
{
	static int sSilent = -1; if (sSilent < 0) sSilent = n7_getenv("NANO7_NO_SOUND") ? 1 : 0;
	if (sSilent) return false;
	if (sOff < 0) {
		sOff = n7p_exists_rel("nosound.txt") ? 1 : 0;
		uint32_t b = 0; char t[16] = { 0 };
		if (n7p_read_into("volume.txt", t, 8) > 0 && sscanf(t, "%u", &b) == 1 && b <= 100) sMaster = b / 100.0f;
	}
	if (sOff || id < 0 || (size_t)id >= mPaths.size() || mPaths[id].empty()) return false;
	uint32_t now = n7p_ms();
	/* the OS player has one slot: a new sound would cut the one playing, so wait for it, and never more than 4 a second */
	if ((int32_t)(now - sBusyUntil) < 0 || now - sLastStart < 250) { n7_stats.snd_dropped++; return false; }
	double v = vol * mBase[id] * mVolume * sMaster;
	if (v <= 0.001) return false;
	if (v > 1) v = 1;
	if (!n7p_wav_play(mPaths[id].c_str(), (uint32_t)(v * 0x7fff))) { n7_stats.snd_dropped++; return false; }
	sLastStart = now; sBusyUntil = now + mMs[id];
	n7_stats.snd_played++;
	return true;
}
