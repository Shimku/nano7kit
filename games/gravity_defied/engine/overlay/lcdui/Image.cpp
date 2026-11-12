#include "Image.h"
#include "../gd_config.h"

#include <cstdio>
#include <cstring>

static std::string stem_of(const std::string& path)
{
    std::string s = path;
    auto slash = s.find_last_of("/\\");
    if (slash != std::string::npos) s = s.substr(slash + 1);
    auto dot = s.find_last_of('.');
    if (dot != std::string::npos) s = s.substr(0, dot);
    return s;
}

void Image::blank(int width, int height, uint32_t color)
{
    w = width < 1 ? 1 : width;
    h = height < 1 ? 1 : height;
    pix.assign((size_t)w * (size_t)h, color | 0xff000000u);
}

int Image::loadRaw(const std::string& path)
{
    uint8_t hdr[4];
    int n = gd_fs_read(path.c_str(), hdr, 4);
    if (n < 4) return 0;
    int iw = hdr[0] | (hdr[1] << 8);
    int ih = hdr[2] | (hdr[3] << 8);
    if (iw < 1 || ih < 1 || iw > 1024 || ih > 1024) return 0;
    std::vector<uint8_t> buf(4 + (size_t)iw * (size_t)ih * 4);
    n = gd_fs_read(path.c_str(), buf.data(), (unsigned)buf.size());
    if (n < 4 + iw * ih * 4) return 0;
    w = iw;
    h = ih;
    pix.resize((size_t)w * (size_t)h);
    const uint8_t *p = buf.data() + 4;
    for (int i = 0; i < w * h; i++, p += 4) {
        /* stored as RGBA bytes, kept as 0xAARRGGBB */
        pix[i] = ((uint32_t)p[3] << 24) | ((uint32_t)p[0] << 16) | ((uint32_t)p[1] << 8) | (uint32_t)p[2];
    }
    return 1;
}

Image::Image(int width, int height)
{
    blank(width, height, 0xff000000u);
}

Image::Image(const std::string& embeddedPath)
{
    std::string stem = stem_of(embeddedPath);
    const char *tries[4];
    char a[96], b[96], c[96], d[96];
    std::snprintf(a, sizeof a, "assets/%s.raw", stem.c_str());
    std::snprintf(b, sizeof b, "%s.raw", stem.c_str());
    std::snprintf(c, sizeof c, "%s", embeddedPath.c_str());
    std::snprintf(d, sizeof d, "assets/%s", embeddedPath.c_str());
    tries[0] = a;
    tries[1] = b;
    tries[2] = c;
    tries[3] = d;
    for (int i = 0; i < 4; i++) {
        if (loadRaw(tries[i])) return;
    }
    gd_log("image: missing %s, using placeholder", embeddedPath.c_str());
    /* 8x8 so menu raster fill cannot divide by zero */
    blank(8, 8, 0xff203040u);
}

Image::~Image() {}
