/* ab_entry.h: Angry Birds shell boundary (AppController/MyEAGLView replaced) (nano7kit, MIT). */
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
void ab_goto_level(const char *name);   /* map path: levelName + updateLoading, no extra camera force */

/* Box2D world walk for host tests and on-device log.txt (offsets from the 1.0 ARM). */
#define AB_PHYS_SAMPLE 12
#define AB_PHYS_VIS 48
#define AB_PHYS_PIG 24
#define AB_PHYS_BIRD 16
#define AB_PHYS_BLOCK 24
#define AB_PHYS_TO_WORLD 20.f
#define AB_VIEW_W 480.f
#define AB_VIEW_H 320.f
typedef struct {
    uint32_t lua, world;
    int phys_on, bodies, static_n, dynamic_n, fixtures, contacts, contacts_hint;
    uint32_t filter;
    float grav_x, grav_y, acc;
    float dyn_ymin, dyn_ymax, stat_ymin, stat_ymax;
    float world_scale, cam_left, cam_top;
    int vis_dyn, vis_all, obj_n, ctx_w, ctx_h;
    int circles, polygons, shape_bad;
    int sample_n;
    int sample_type[AB_PHYS_SAMPLE];
    float sample_x[AB_PHYS_SAMPLE], sample_y[AB_PHYS_SAMPLE];
    int vis_n;
    float vis_wx[AB_PHYS_VIS], vis_wy[AB_PHYS_VIS];
    int pig_n, bird_n, block_n;
    float pig_wx[AB_PHYS_PIG], pig_wy[AB_PHYS_PIG];
    float bird_wx[AB_PHYS_BIRD], bird_wy[AB_PHYS_BIRD];
    float block_wx[AB_PHYS_BLOCK], block_wy[AB_PHYS_BLOCK];
} ab_phys_info;
void ab_phys_snapshot(ab_phys_info *o);
void ab_phys_log(const char *tag);
int ab_phys_resting(const ab_phys_info *o);
void ab_phys_enable(int on);
void ab_cam_fit_play(void);
void ab_ready_slingshot(void);
void ab_watch_level(void);
void ab_unpause(void);
void ab_show_pause_page(void);         /* inPausePage + PAUSED overlay */
void ab_lua_collect(void);             /* lua_gc so the 2 MB hole stays >300 KB */
#endif
