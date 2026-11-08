/* audio_stub.h: stand-in wav/mp3 so skipped SFX/Music do not throw (nano7kit, MIT). */
#ifndef AB_AUDIO_STUB_H
#define AB_AUDIO_STUB_H
#include <stdint.h>
/* 1 if name is audio and *out was filled, 0 if not audio, -1 on alloc fail. */
int ab_stub_audio(const char *name, void **out, uint32_t *len);
#endif
