/* gl_left.h: GL textures a quit nano7kit port left behind (nano7kit, LGPL-3.0-or-later). */
#ifndef GL_LEFT_H
#define GL_LEFT_H
#include <stdint.h>
void gll_init(const char *own_file);    /* this app's record, e.g. /Apps/Data/FruitNinja/gl_names.txt */
uint32_t gll_sweep(void);               /* delete what every port's record lists; returns names deleted */
void gll_note(uint32_t name);           /* a texture name this app created */
void gll_save(void);                    /* write this app's record if it changed */
#endif
