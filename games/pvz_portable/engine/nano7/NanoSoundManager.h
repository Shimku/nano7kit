/* nano7kit measurement: silent sound manager that only records how much sound data the game asks for. */
#pragma once
#ifdef NANO7_DEVICE
#include "../nano7dev/n7snd.h"
#else
#include "sound/SoundManager.h"
#include <string>
namespace Sexy {
class NanoSoundManager : public SoundManager {
public:
	bool Initialized() override { return true; }
	bool LoadSound(intptr_t theSfxID, const std::string& theFilename) override;
	intptr_t LoadSound(const std::string& theFilename) override { intptr_t id = GetFreeSoundId(); LoadSound(id, theFilename); return id; }
	void ReleaseSound(intptr_t) override {}
	void SetVolume(double) override {}
	bool SetBaseVolume(intptr_t, double) override { return true; }
	bool SetBasePan(intptr_t, int) override { return true; }
	SoundInstance* GetSoundInstance(intptr_t) override { return nullptr; }
	void ReleaseSounds() override {}
	void ReleaseChannels() override {}
	double GetMasterVolume() override { return 1.0; }
	void SetMasterVolume(double) override {}
	void Flush() override {}
	void StopAllSounds() override {}
	intptr_t GetFreeSoundId() override { return mNext++; }
	int GetNumSounds() override { return (int)mNext; }
	intptr_t mNext = 1;
};
}
extern "C" unsigned long long nano7_sound_bytes, nano7_sound_count;
#endif
