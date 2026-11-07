/* ab_entry.h: Angry Birds shell boundary (AppController/MyEAGLView replaced) (nano7kit, MIT). */
#ifndef AB_ENTRY_H
#define AB_ENTRY_H
#include <stdint.h>
int ab_start(void);
void ab_frame(float dt);
typedef struct { float x, y; int down; } ab_tsample;
#define AB_MAX_STEPS 4
void ab_frame_q(float dt, const ab_tsample *q, int n);
void ab_touch(int id, int down, float panel_x, float panel_y);
void ab_zoom_by(float factor);
void ab_pan_by(float dx, float dy);
void ab_load_level(const char *name);
void ab_call_lua(const char *name);

/* Box2D world walk for host tests and on-device log.txt (offsets from the 1.0 ARM). */
#define AB_PHYS_SAMPLE 12
typedef struct {
    uint32_t lua, world;
    int phys_on, bodies, static_n, dynamic_n, fixtures, contacts, contacts_hint;
    uint32_t filter;
    float grav_x, grav_y, acc;
    float dyn_ymin, dyn_ymax, stat_ymin, stat_ymax;
    int sample_n;
    int sample_type[AB_PHYS_SAMPLE];
    float sample_x[AB_PHYS_SAMPLE], sample_y[AB_PHYS_SAMPLE];
} ab_phys_info;
void ab_phys_snapshot(ab_phys_info *o);
void ab_phys_log(const char *tag);
int ab_phys_resting(const ab_phys_info *o);
void ab_phys_enable(int on);
#endif
