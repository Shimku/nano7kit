#include "CanvasImpl.h"
#include "Canvas.h"
#include "../gd_config.h"
#include "Micro.h"

CanvasImpl::CanvasImpl(Canvas *c)
{
    canvas = c;
    width = GD_SCREEN_W;
    height = GD_GAME_H;
}

CanvasImpl::~CanvasImpl() {}

int CanvasImpl::getWidth() { return width; }
int CanvasImpl::getHeight() { return height; }

uint32_t *CanvasImpl::getFramebuffer() { return gd_fb(); }

void CanvasImpl::repaint()
{
    /* The nano/host frame already presents the framebuffer. */
}

void CanvasImpl::setWindowTitle(const std::string&) {}

void CanvasImpl::processEvents()
{
    unsigned keys = gd_held_keys();
    int fire = gd_tap_fire();
    int back = gd_tap_back();
    int pause = gd_tap_pause();
    int mx = gd_menu_tap_x();
    int my = gd_menu_tap_y();
    int d;

    if (Micro::isInGameMenu) {
        if (prevKeys) {
            for (d = 1; d <= 9; d++) {
                unsigned bit = 1u << d;
                if (prevKeys & bit) canvas->publicKeyReleased(48 + d);
            }
            prevKeys = 0;
        }
    } else {
        for (d = 1; d <= 9; d++) {
            unsigned bit = 1u << d;
            int code = 48 + d;
            if ((keys & bit) && !(prevKeys & bit)) canvas->publicKeyPressed(code);
            if (!(keys & bit) && (prevKeys & bit)) canvas->publicKeyReleased(code);
        }
        /* method_164 zeros activeKeys on restart. Re-assert is a boolean set,
         * not a repeated tap: handleUpdatedInput reads the flag array. */
        for (d = 1; d <= 9; d++) {
            if (keys & (1u << d)) canvas->publicKeyPressed(48 + d);
        }
        prevKeys = keys;
    }

    if (fire && !prevFire) canvas->publicKeyPressed(Canvas::Keys::FIRE);
    if (!fire && prevFire) canvas->publicKeyReleased(Canvas::Keys::FIRE);
    if (back && !prevBack) canvas->pressedEsc();
    /* Pause and menu row taps are handled in Micro::tick. */

    prevFire = fire;
    prevBack = back;
    prevPause = pause;
    prevTapX = mx;
    prevTapY = my;
}
