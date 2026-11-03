#include "NanoSoundManager.h"
#include "paklib/PakInterface.h"
#include <cstdio>
unsigned long long nano7_sound_bytes, nano7_sound_count;
bool Sexy::NanoSoundManager::LoadSound(intptr_t, const std::string& theFilename)
{
	static const char* exts[] = { "", ".ogg", ".au", ".wav", ".mp3" };
	for (const char* e : exts) {
		PFILE* fp = p_fopen((theFilename + e).c_str(), "rb");
		if (!fp) continue;
		p_fseek(fp, 0, SEEK_END); long n = p_ftell(fp); p_fclose(fp);
		nano7_sound_bytes += n; nano7_sound_count++;
		return true;
	}
	return true;
}
