#pragma once
#include <string>
#include <cstdint>

class Canvas;

class CanvasImpl {
    Canvas *canvas;
    int width;
    int height;
    unsigned prevKeys = 0;
    int prevFire = 0, prevBack = 0, prevPause = 0;
    int prevTapX = -1, prevTapY = -1;

public:
    CanvasImpl(Canvas *canvas);
    ~CanvasImpl();

    void repaint();
    int getWidth();
    int getHeight();
    uint32_t *getFramebuffer();
    void processEvents();
    void setWindowTitle(const std::string& title);
};
