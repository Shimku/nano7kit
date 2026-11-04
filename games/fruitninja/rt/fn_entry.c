/* fn_entry.c: drives the recompiled engine the way the iOS shell did (nano7kit, MIT).
 * The iOS shell: SetRootDir(bundle, docs), InitSoundSession, InitializeApp, then per frame
 * two UpdateApp half steps and DrawApp; touches through UpdateTouchScreenInput in view points. */
#include <string.h>
#include "rt.h"
#include "fngl.h"
#include "fn_entry.h"
#include "rt_entry.h"

static float game_time;
int fn_start(void) {
    for (int i = 0; i < FN_INIT_COUNT; i++) rt_call("init", fn_inits[i], 0, 0, 0, 0, 0, 0);
    uint32_t b = g_strdup("/app"), d = g_strdup("/docs");
    rt_call("SetRootDir", FN_SetRootDir, b, d, 0, 0, 0, 0);
    if (FN_InitSoundSession) rt_call("InitSoundSession", FN_InitSoundSession, 0, 0, 0, 0, 0, 0);
    rt_call("InitializeApp", FN_InitializeApp, 0, 0, 0, 0, 0, 0);
    return 0;
}
/* One engine sample per update step: UpdateTouchScreenInput keeps only the latest position
 * of a touch until the next UpdateApp, so several samples given in one step collapse into one
 * and a swipe that the nano saw in one frame became a tap (an earlier build on hardware: menu fruit could
 * not be sliced). So the frame is cut into as many update steps as there are samples (2 to
 * FN_MAX_STEPS), with the frame's time shared between them, and a sample goes in before each
 * with that step's own time stamp.
 *
 * The blade dies for the rest of a touch once the engine runs two update steps in a row in
 * which a down touch did not move (host harness: one frame with no new place anywhere before
 * the fruit, and the swipe cuts nothing, in the menu and in the game). The iPhone gave a new
 * place every half step; a nano frame with one new place left the first step still, and a
 * frame with none (the finger list had nothing new) left three steps still. So, while the
 * touch is down: a place equal to the one the engine has is not given again, one new place
 * fills both steps (the first gets the point halfway from the last place), and a frame with
 * nothing new is a single step of the whole dt. Not down: exactly as before. */
static int fed_down;                    /* what the engine has for the touch (fn_touch) */
static float fed_x, fed_y;
void fn_frame_q(float dt, const fn_tsample *q, int n) {
    fn_tsample s_[2 * FN_MAX_STEPS];
    int m = 0;
    for (int i = 0; i < n; i++) {
        int d = m ? s_[m - 1].down : fed_down;
        float x = m ? s_[m - 1].x : fed_x, y = m ? s_[m - 1].y : fed_y;
        if (q[i].down == d && (!d || (q[i].x == x && q[i].y == y))) continue;   /* nothing new */
        if (m < 2 * FN_MAX_STEPS) s_[m++] = q[i]; else s_[m - 1] = q[i];
    }
    if (m == 1 && s_[0].down && fed_down) {
        s_[1] = s_[0]; m = 2;
        s_[0].x = (fed_x + s_[1].x) * 0.5f; s_[0].y = (fed_y + s_[1].y) * 0.5f;
    }
    int steps = !m && fed_down ? 1 : m < 2 ? 2 : m > FN_MAX_STEPS ? FN_MAX_STEPS : m;
    float h = dt / (float)steps;
    int last_down = -1;
    for (int s = 0; s < steps; s++) {
        game_time += h;                          /* each sample gets its own time stamp: the slice
                                                    speed is distance over time, and 0 s is no slice */
        int from = m * s / steps, to = m * (s + 1) / steps;
        for (int i = from; i < to; i++)          /* state changes inside a bucket, then its last sample */
            if (i == to - 1 || s_[i].down != last_down) { fn_touch(0, s_[i].down, s_[i].x, s_[i].y); last_down = s_[i].down; }
        /* second argument 0 on every step: with a non-zero one (the iOS shell's first half step)
         * the step does not take the touch, measured on the host harness (FN_BURST) */
        rt_call("UpdateApp", FN_UpdateApp, F2U(h), 0, 0, 0, 0, 0);
    }
    uint32_t st[2] = {F2U(dt), F2U(game_time)};
    be_frame_begin();
    rt_call("DrawApp", FN_DrawApp, 320, (uint32_t)fe_view_h, 1, 1, st, 2);
    be_frame_end();
}
void fn_frame(float dt) { fn_frame_q(dt, 0, 0); }
/* panel (240x432; game 0.75 across, 0.9 along, filling the panel) to iPhone view points (320x480 portrait) */
void fn_touch(int id, int down, float px, float py) {
    float vx = (px - fe_off_x) / fe_scale, vy = (py - fe_off_y) / fe_scale_y;
    uint32_t st[1] = {F2U(game_time)};
    fed_down = down != 0; fed_x = px; fed_y = py;
    rt_call("Touch", FN_Touch, (uint32_t)id + 0x1000u, /* 0 marks a free touch slot */ (uint32_t)(down != 0), F2U(vx), F2U(vy), st, 1);
}
