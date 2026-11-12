#include "Canvas.h"
#include "CanvasImpl.h"
#include "Graphics.h"
#include "Command.h"
#include "CommandListener.h"
#include "../gd_config.h"

Canvas::Canvas()
{
    impl = std::make_unique<CanvasImpl>(this);
    graphics = std::make_unique<Graphics>(impl->getFramebuffer(), impl->getWidth(), impl->getHeight());
}

Canvas::~Canvas() {}

int Canvas::getWidth() { return impl->getWidth(); }
int Canvas::getHeight() { return impl->getHeight(); }
void Canvas::setWindowTitle(const std::string& title) { impl->setWindowTitle(title); }
bool Canvas::isShown() { return gd_fb() != nullptr; }
CanvasImpl *Canvas::getCanvasImpl() { return impl.get(); }

void Canvas::repaint()
{
    /* Rebuild Graphics each paint: the framebuffer pointer is set in hb_raw_init. */
    graphics = std::make_unique<Graphics>(gd_fb(), impl->getWidth(), impl->getHeight());
    paint(graphics.get());
    impl->processEvents();
    impl->repaint();
}

void Canvas::serviceRepaints() {}

int Canvas::getGameAction(int keyCode)
{
    switch (keyCode) {
    case Keys::UP:
    case Keys::DOWN:
    case Keys::LEFT:
    case Keys::RIGHT:
    case Keys::FIRE:
        return keyCode;
    default:
        return 0;
    }
}

void Canvas::removeCommand(Command *command) { currentCommands.erase(command); }
void Canvas::addCommand(Command *command) { currentCommands.insert(command); }
void Canvas::setCommandListener(CommandListener *listener) { commandListener = listener; }
void Canvas::publicKeyPressed(int keyCode) { keyPressed(keyCode); }
void Canvas::publicKeyReleased(int keyCode) { keyReleased(keyCode); }

void Canvas::pressedEsc()
{
    for (const auto& command : currentCommands) {
        if (command->type == Command::Type::BACK || currentCommands.size() == 1) {
            if (commandListener)
                commandListener->commandAction(command, this);
            return;
        }
    }
}
