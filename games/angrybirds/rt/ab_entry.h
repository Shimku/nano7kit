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

#define AB_PHYS_SAMPLE 96
#define AB_VIS_MAX 64
typedef struct {
    uint32_t lua, world;
    int phys_on, bodies, static_n, dynamic_n, fixtures, contacts, contacts_hint;
    uint32_t filter;
    float grav_x, grav_y, acc, score;
    float dyn_ymin, dyn_ymax, stat_ymin, stat_ymax;
    float world_scale, cam_left, cam_top;
    int vis_dyn, vis_all, obj_n, ctx_w, ctx_h;
    int sample_n;
    int sample_type[AB_PHYS_SAMPLE];
    float sample_x[AB_PHYS_SAMPLE], sample_y[AB_PHYS_SAMPLE];
    int vis_n;
    int vis_type[AB_VIS_MAX];
    float vis_x[AB_VIS_MAX], vis_y[AB_VIS_MAX];
    float vis_px[AB_VIS_MAX], vis_py[AB_VIS_MAX];
} ab_phys_info;
void ab_phys_snapshot(ab_phys_info *o);
void ab_phys_log(const char *tag);
int ab_phys_resting(const ab_phys_info *o);
void ab_phys_enable(int on);
float ab_lua_get_number(const char *name);
int ab_lua_get_bool(const char *name);
int ab_lua_has(const char *name);
int ab_lua_xy(const char *table, float *x, float *y);
int ab_lua_obj_name(const char *obj, char *out, int n);
int ab_body_exists(const char *name);
void ab_lua_dump_shot(const char *tag);
void ab_lua_dump_birds(const char *tag);
void ab_cam_fit_play(void);
void ab_cam_fit_world(void);
void ab_cam_fit_aim(void);
void ab_cam_fit_birds(void);
void ab_cam_set(float scale, float left, float top);
void ab_ready_slingshot(void);
void ab_clear_shot_flags(void);
void ab_set_body_velocity(const char *name, float vx, float vy);
int ab_world_to_panel(float bx, float by, float *px, float *py);
int ab_pouch_world(float *bx, float *by);
int ab_sling_world(float *bx, float *by);
const char *ab_so_path(void);
void ab_set_so_path(const char *p);
#endif
