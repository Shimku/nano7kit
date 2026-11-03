/* fn_entry.h: the Fruit Ninja shell boundary (EAGLView/AppDelegate replaced) (nano7kit, MIT). */
#ifndef FN_ENTRY_H
#define FN_ENTRY_H
int fn_start(void);
void fn_frame(float dt);
typedef struct { float x, y; int down; } fn_tsample;
#define FN_MAX_STEPS 4
void fn_frame_q(float dt, const fn_tsample *q, int n);
void fn_touch(int id, int down, float panel_x, float panel_y);
#endif
