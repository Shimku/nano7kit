/* nano7 PvZ device sound: effects only (music is off), played through the iPod OS's own WAV player, which plays one
 * sound at a time. Quiet by default and rate limited, so a busy lawn does not queue a pile of sounds. */
#pragma once
#include "sound/SoundManager.h"
#include "sound/SoundInstance.h"
#include <string>
#include <vector>
namespace Sexy {
class NanoSoundManager : public SoundManager {
public:
	std::vector<std::string> mPaths;   /* by sound id: data-folder path of the .wav, "" if none */
	std::vector<uint32_t> mMs;         /* its length in ms (from the file size) */
	std::vector<float> mBase;
	double mVolume = 1.0;
	bool Initialized() override { return true; }
	bool LoadSound(intptr_t theSfxID, const std::string& theFilename) override;
	intptr_t LoadSound(const std::string& theFilename) override { intptr_t id = GetFreeSoundId(); LoadSound(id, theFilename); return id; }
	void ReleaseSound(intptr_t id) override { if (id >= 0 && (size_t)id < mPaths.size()) mPaths[id].clear(); }
	void SetVolume(double v) override { mVolume = v; }
	bool SetBaseVolume(intptr_t id, double v) override { if (id >= 0 && (size_t)id < mBase.size()) mBase[id] = (float)v; return true; }
	bool SetBasePan(intptr_t, int) override { return true; }
	SoundInstance* GetSoundInstance(intptr_t theSfxID) override;
	void ReleaseSounds() override { mPaths.clear(); mMs.clear(); mBase.clear(); }
	void ReleaseChannels() override {}
	double GetMasterVolume() override { return mVolume; }
	void SetMasterVolume(double v) override { mVolume = v; }
	void Flush() override {}
	void StopAllSounds() override {}
	intptr_t GetFreeSoundId() override { return (intptr_t)mPaths.size() + 1; }
	int GetNumSounds() override { return (int)mPaths.size(); }
	bool Play(intptr_t id, double vol);
};
}
extern "C" unsigned long long nano7_sound_bytes, nano7_sound_count;
