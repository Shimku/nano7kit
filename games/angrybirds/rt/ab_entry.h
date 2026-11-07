/* ab_entry.h: Angry Birds shell boundary (AppController/MyEAGLView replaced) (nano7kit, MIT). */
#ifndef AB_ENTRY_H
#define AB_ENTRY_H
int ab_start(void);
void ab_frame(float dt);
typedef struct { float x, y; int down; } ab_tsample;
#define AB_MAX_STEPS 4
void ab_frame_q(float dt, const ab_tsample *q, int n);
void ab_touch(int id, int down, float panel_x, float panel_y);
void ab_zoom_by(float factor);
void ab_pan_by(float dx, float dy);
#endif
