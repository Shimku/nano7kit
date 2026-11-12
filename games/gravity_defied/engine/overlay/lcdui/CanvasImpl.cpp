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
    int gas = gd_held_gas();
    int brake = gd_held_brake();
    int left = gd_held_lean_l();
    int right = gd_held_lean_r();
    int fire = gd_tap_fire();
    int back = gd_tap_back();
    int pause = gd_tap_pause();
    int mx = gd_menu_tap_x();
    int my = gd_menu_tap_y();

    /* In game, re-assert held buttons every frame so physics keeps gas/brake
     * after a restart that cleared activeActions. Menus still want edges. */
    if (Micro::isInGameMenu) {
        if (gas && !prevGas) canvas->publicKeyPressed(Canvas::Keys::UP);
        if (!gas && prevGas) canvas->publicKeyReleased(Canvas::Keys::UP);
        if (brake && !prevBrake) canvas->publicKeyPressed(Canvas::Keys::DOWN);
        if (!brake && prevBrake) canvas->publicKeyReleased(Canvas::Keys::DOWN);
        if (left && !prevLeft) canvas->publicKeyPressed(Canvas::Keys::LEFT);
        if (!left && prevLeft) canvas->publicKeyReleased(Canvas::Keys::LEFT);
        if (right && !prevRight) canvas->publicKeyPressed(Canvas::Keys::RIGHT);
        if (!right && prevRight) canvas->publicKeyReleased(Canvas::Keys::RIGHT);
    } else {
        if (gas) canvas->publicKeyPressed(Canvas::Keys::UP);
        else if (prevGas) canvas->publicKeyReleased(Canvas::Keys::UP);
        if (brake) canvas->publicKeyPressed(Canvas::Keys::DOWN);
        else if (prevBrake) canvas->publicKeyReleased(Canvas::Keys::DOWN);
        if (left) canvas->publicKeyPressed(Canvas::Keys::LEFT);
        else if (prevLeft) canvas->publicKeyReleased(Canvas::Keys::LEFT);
        if (right) canvas->publicKeyPressed(Canvas::Keys::RIGHT);
        else if (prevRight) canvas->publicKeyReleased(Canvas::Keys::RIGHT);
    }

    if (fire && !prevFire) canvas->publicKeyPressed(Canvas::Keys::FIRE);
    if (!fire && prevFire) canvas->publicKeyReleased(Canvas::Keys::FIRE);
    if (back && !prevBack) canvas->pressedEsc();
    /* Pause and menu row taps are handled in Micro::tick (requestMenu / nanoTap). */

    prevGas = gas;
    prevBrake = brake;
    prevLeft = left;
    prevRight = right;
    prevFire = fire;
    prevBack = back;
    prevPause = pause;
    prevTapX = mx;
    prevTapY = my;
}
