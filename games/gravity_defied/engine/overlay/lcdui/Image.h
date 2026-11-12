#pragma once

#include <cstdint>
#include <string>
#include <vector>

class Image {
    int w = 1;
    int h = 1;
    std::vector<uint32_t> pix;

    void blank(int width, int height, uint32_t color);
    int loadRaw(const std::string& path);

public:
    Image(const std::string& embeddedPath);
    Image(int width, int height);
    ~Image();

    int getWidth() const { return w; }
    int getHeight() const { return h; }
    const uint32_t *pixels() const { return pix.empty() ? nullptr : pix.data(); }
};
