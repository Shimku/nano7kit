/* touch_pick.h: which source to believe for the finger, once per poll (nano7kit, MIT).
 * Two sources: the OS touch list (right at once, but the OS drains it) and the resident's
 * mailbox (latched on every touch event, but only when the UI task gets to the event).
 * When the list shows only a lifted touch while the mailbox still says down, the mailbox
 * is stale and is not believed ("distrust") until it is fresh again.
 *
 * 1.0 cleared distrust only when the mailbox went up, or when the list showed a touch within
 * 12 px of the last place it showed one. 1.0.1 also counts the mailbox as fresh once its
 * place differs from the place it held when it was distrusted (the resident only rewrites
 * the place on a new touch event).
 *
 * 1.1 (launcher 485bef2, touch_latch.h): the mailbox reads down if ANY queued sample is down,
 * so after a lift it can stay down at the last place until the next touch event, and it no
 * longer reads up between two swipes. If the list never showed the lift, nothing marked it
 * stale: Fruit Ninja saw one press that never ended. Now, with the list empty, a down mailbox
 * counts only while it is confirmed: it went down or moved, or the list showed a touch, within
 * the last FN_TP_HOLD_US. A finger held still that long reads as lifted (FN_TP_HELD), which
 * changes nothing in the game (a still blade cuts nothing); when it moves again it is a new
 * press. Only this game reads this file; the launcher and every other app are unchanged. */
#ifndef FN_TOUCH_PICK_H
#define FN_TOUCH_PICK_H
#include <stdint.h>

#define FN_TP_HOLD_US 150000u

typedef struct {
    int distrust, mb_seen, mb_was_down;
    int16_t listed_x, listed_y, stale_x, stale_y, mb_x, mb_y;
    uint32_t fresh_us;
} fn_tpick_t;

enum { FN_TP_UP = 0, FN_TP_LIST = 1, FN_TP_MAILBOX = 2, FN_TP_STALE = 3, FN_TP_HELD = 4 };

/* now_us: a microsecond clock (wraps are fine). listed: 1 down at (lx,ly), 2 only lifted
 * touches, 0 empty (list_touch in fn_nano.c). mb_down, (mx,my): the mailbox. Returns
 * FN_TP_LIST or FN_TP_MAILBOX for down (place from that source), FN_TP_UP, FN_TP_STALE (up:
 * the mailbox says down but is stale) or FN_TP_HELD (up: the mailbox says down at one place
 * and nothing has confirmed it for FN_TP_HOLD_US). */
static inline int fn_tpick(fn_tpick_t *s, uint32_t now_us, int listed, int16_t lx, int16_t ly,
                           int mb_down, int16_t mx, int16_t my) {
    int fresh = mb_down && (!s->mb_seen || !s->mb_was_down || mx != s->mb_x || my != s->mb_y);
    s->mb_seen = 1; s->mb_was_down = mb_down; s->mb_x = mx; s->mb_y = my;
    if (fresh) s->fresh_us = now_us;
    if (!mb_down) s->distrust = 0;
    else if (s->distrust) {
        int dx = mx - s->listed_x, dy = my - s->listed_y;
        if (dx > -12 && dx < 12 && dy > -12 && dy < 12 && listed == 1) s->distrust = 0;
        if (mx != s->stale_x || my != s->stale_y) s->distrust = 0;   /* a new touch event */
    }
    if (listed == 1) { s->listed_x = lx; s->listed_y = ly; s->fresh_us = now_us; return FN_TP_LIST; }
    if (listed == 2) {
        if (!mb_down) return FN_TP_UP;
        s->distrust = 1; s->stale_x = mx; s->stale_y = my;
        return FN_TP_STALE;
    }
    if (!mb_down) return FN_TP_UP;
    if (s->distrust) return FN_TP_STALE;
    if ((uint32_t)(now_us - s->fresh_us) < FN_TP_HOLD_US) return FN_TP_MAILBOX;
    return FN_TP_HELD;
}
#endif
