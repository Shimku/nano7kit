/* fngl.h: GLES 1.1 front end for the recompiled Angry Birds (nano7kit, MIT).
 * The engine's GL calls land here; transformed, clipped triangles go to a backend. */
#ifndef FN_GL_H
#define FN_GL_H
#include <stdint.h>
typedef struct { float x, y, z, w, u, v; uint8_t c[4]; } be_vtx;   /* x,y: panel pixels from top left */
typedef struct {
    int tex;                    /* backend texture handle, 0 = none */
    int blend; uint32_t sfactor, dfactor;
    int depth_test, depth_write; uint32_t depth_func;
    int alpha_test; float alpha_ref;
} be_state;
/* backend (host software raster, or the nano's GL) */
void be_frame_begin(void);
void be_clear(int color, int depth, const float rgba[4], float d);
void be_tris(const be_state *s, const be_vtx *v, int nverts);
void be_frame_end(void);
int be_tex_create(void);
void be_tex_delete(int h);
void be_tex_image(int h, int level, int w, int ht, uint32_t format, uint32_t type, const void *pixels);
void be_tex_compressed(int h, int level, uint32_t fmt, int w, int ht, const void *data, uint32_t size);
void be_tex_param(int h, uint32_t pname, uint32_t value);
void be_set_theme_ground(int theme, int h); /* THEME_GROUND backend handle */
void be_set_sky_tex(int h);                 /* LAYER_4 / background sheet for clamp + checks */
int be_sky_has_rgb(unsigned r, unsigned g, unsigned b);
int be_sky_explains_run(unsigned r, unsigned g, unsigned b, int h);
/* panel mapping, set by the shell: iPhone framebuffer pixel (origin bottom left) -> panel */
extern float fe_scale, fe_scale_y, fe_off_x, fe_off_y, fe_fb_h;
extern int fe_view_h;
extern int fe_stat_tris, fe_stat_draws, fe_stat_tex_bytes, fe_stat_tex_peak;
extern int fe_stat_ingame_ok, fe_stat_ingame_clip, fe_stat_castle_img;
extern int fe_drop_fullscreen;
extern int fe_in_level;                /* 1 once a level camera is live */
extern int fe_skip_fbo_blit;           /* MaskedImage flush: FBO bind is a no-op */
extern int fe_tex_shift, fe_tex_min_side, fe_tex_big_side, fe_tex_big_shift, fe_stat_tex_count, fe_stat_tex_count_peak;   /* texture policy (see fngl.c) */
extern int fe_atlas_max_side, fe_stat_atlased, fe_stat_atlas_pages;   /* 0 = no atlas */
void fngl_reset(void);                 /* defaults; call before anything else */
void fe_reset_matrices(void);          /* identity GL stacks; call on level load */
extern int fe_pending_tex_shift;       /* convert-time downsample; UV is divided by 2^this */
void fe_queue_tex_shift(int sh, const char *stem); /* fopen of a texscale sheet; consumed on upload */
void fe_note_sheet(const char *stem);  /* INGAME_6/7/8 -> THEME_GROUND sampler */
extern int fe_sky_quad;                /* BACKGROUND LAYER_4: one play-screen, clamp UVs */
extern int fe_ground_wrap;             /* GROUND_BLOCK: sample THEME_GROUND */
extern int fe_wrap_hold;               /* wrap survives Image::draw return */
extern int fe_atlas_uv;                /* GROUND_BLOCK 04/05: wrap along the rotated quad */
extern int fe_tex_clamp;               /* sample sky with clamp, not wrap-repeat */
extern float fe_ground_uw, fe_ground_vh;
extern int fe_skip_draw;               /* dummy 480x320 BACKGROUND */
extern int fe_theme_n;                 /* 1/2/3 from lua sky colour */
extern int fe_rot_sky_quad;            /* rotated flat quad covering sky */
extern int fe_lowmem;                  /* 1: convert/start OOM; draw the red CHECK LOG.TXT screen */
extern char fe_lowmem_why[160];
void fe_enter_lowmem(const char *why);
#endif
