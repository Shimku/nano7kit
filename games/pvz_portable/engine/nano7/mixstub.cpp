/* nano7kit measurement: music is silent; these keep Music.cpp linking. */
#include "sound/SDLMusicInterface.h"
extern "C" {
Mix_Music* Mix_LoadMUS_RW(SDL_RWops* src, int freesrc) { if (src && freesrc) SDL_RWclose(src); return nullptr; }
int Mix_PlayMusicStream(Mix_Music*, int) { return 0; }
int Mix_VolumeMusicStream(Mix_Music*, int) { return 0; }
int Mix_HaltMusicStream(Mix_Music*) { return 0; }
int Mix_ModMusicStreamJumpToOrder(Mix_Music*, int) { return 0; }
int Mix_ModMusicStreamSetChannelVolume(Mix_Music*, int, int) { return 0; }
int Mix_PlayingMusicStream(Mix_Music*) { return 0; }
}
Sexy::SDLMusicInfo::SDLMusicInfo() {}
int Sexy::SDLMusicInterface::GetMusicOrder(int) { return 0; }
