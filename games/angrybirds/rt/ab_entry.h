/* ab_entry.h: JNI shell around Android libangrybirds.so (nano7kit, MIT). */
#ifndef AB_ENTRY_H
#define AB_ENTRY_H
#include <stdint.h>
int ab_start(void);
void ab_frame(float dt);
typedef struct { float x, y; int down; int id; } ab_tsample;
#define AB_MAX_STEPS 4
#define AB_FINGERS 8
void ab_frame_q(float dt, const ab_tsample *q, int n);
void ab_touch(int id, int down, float panel_x, float panel_y);
void ab_load_level(const char *name);
void ab_call_lua(const char *name);
void ab_load_lua_level(const char *name);

#define AB_PHYS_SAMPLE 32
typedef struct {
    uint32_t lua, world;
    int phys_on, bodies, static_n, dynamic_n, fixtures, contacts, contacts_hint;
    uint32_t filter;
    float grav_x, grav_y, acc;
    float dyn_ymin, dyn_ymax, stat_ymin, stat_ymax;
    float world_scale, cam_left, cam_top;
    int vis_dyn, vis_all, obj_n, ctx_w, ctx_h;
    int sample_n;
    int sample_type[AB_PHYS_SAMPLE];
    float sample_x[AB_PHYS_SAMPLE], sample_y[AB_PHYS_SAMPLE];
} ab_phys_info;
void ab_phys_snapshot(ab_phys_info *o);
void ab_phys_log(const char *tag);
int ab_phys_resting(const ab_phys_info *o);
void ab_phys_enable(int on);
void ab_cam_fit_play(void);
void ab_cam_fit_world(void);
void ab_cam_fit_birds(void);
void ab_ready_slingshot(void);
void ab_set_body_velocity(const char *name, float vx, float vy);
const char *ab_so_path(void);
void ab_set_so_path(const char *p);
#endif
