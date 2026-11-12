#pragma once
#include <memory>
#include <string>
#include <cstdint>
#include "Image.h"
#include "Font.h"

class Graphics {
    uint32_t *fb = nullptr;
    int fbW = 0;
    int fbH = 0;
    int clipX = 0, clipY = 0, clipW = 0, clipH = 0;
    uint32_t color = 0xff000000u;
    std::shared_ptr<Font> font;

    void _putpixel(int x, int y);
    int inClip(int x, int y) const;

public:
    enum Anchors {
        HCENTER = 1,
        VCENTER = 2,
        LEFT = 4,
        RIGHT = 8,
        TOP = 16,
        BOTTOM = 32,
        BASELINE = 64
    };
    Graphics(uint32_t *framebuffer, int width, int height);
    void drawString(const std::string& s, int x, int y, int anchor);
    void setColor(int r, int g, int b);
    void setFont(std::shared_ptr<Font> font);
    std::shared_ptr<Font> getFont() const;
    void setClip(int x, int y, int w, int h);
    void drawChar(char c, int x, int y, int anchor);
    void fillRect(int x, int y, int w, int h);
    void fillArc(int x, int y, int w, int h, int startAngle, int arcAngle);
    void drawArc(int x, int y, int w, int h, int startAngle, int arcAngle);
    void drawLine(int x1, int y1, int x2, int y2);
    void drawImage(Image* const image, int x, int y, int anchor);
    static int getAnchorX(int x, int size, int anchor);
    static int getAnchorY(int y, int size, int anchor);
};
