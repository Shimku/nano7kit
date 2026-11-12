#include "Graphics.h"
#include "../gd_config.h"
#include "../nano/gd_ui_font.h"
#include <cmath>
#include <cstring>

Graphics::Graphics(uint32_t *framebuffer, int width, int height)
{
    fb = framebuffer;
    fbW = width;
    fbH = height;
    clipX = 0;
    clipY = 0;
    clipW = width;
    clipH = height;
    color = 0xff000000u;
}

int Graphics::inClip(int x, int y) const
{
    return x >= clipX && y >= clipY && x < clipX + clipW && y < clipY + clipH
        && x >= 0 && y >= 0 && x < fbW && y < fbH;
}

void Graphics::_putpixel(int x, int y)
{
    if (!fb || !inClip(x, y)) return;
    fb[(long)y * fbW + x] = color | 0xff000000u;
}

void Graphics::setColor(int r, int g, int b)
{
    if (r < 0) r = 0;
    if (g < 0) g = 0;
    if (b < 0) b = 0;
    if (r > 255) r = 255;
    if (g > 255) g = 255;
    if (b > 255) b = 255;
    color = 0xff000000u | ((uint32_t)r << 16) | ((uint32_t)g << 8) | (uint32_t)b;
}

void Graphics::setFont(std::shared_ptr<Font> f) { font = f; }
std::shared_ptr<Font> Graphics::getFont() const { return font; }

void Graphics::setClip(int x, int y, int w, int h)
{
    clipX = x;
    clipY = y;
    clipW = w;
    clipH = h;
}

int Graphics::getAnchorX(int x, int size, int anchor)
{
    if ((anchor & LEFT) != 0) return x;
    if ((anchor & RIGHT) != 0) return x - size;
    if ((anchor & HCENTER) != 0) return x - size / 2;
    return x;
}

int Graphics::getAnchorY(int y, int size, int anchor)
{
    if ((anchor & TOP) != 0) return y;
    if ((anchor & BOTTOM) != 0) return y - size;
    if ((anchor & VCENTER) != 0) return y - size / 2;
    if ((anchor & BASELINE) != 0) return y - size;
    return y;
}

void Graphics::fillRect(int x, int y, int w, int h)
{
    if (!fb || w <= 0 || h <= 0) return;
    int x0 = x, y0 = y, x1 = x + w, y1 = y + h;
    int cx0 = clipX, cy0 = clipY, cx1 = clipX + clipW, cy1 = clipY + clipH;
    if (x0 < cx0) x0 = cx0;
    if (y0 < cy0) y0 = cy0;
    if (x1 > cx1) x1 = cx1;
    if (y1 > cy1) y1 = cy1;
    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (x1 > fbW) x1 = fbW;
    if (y1 > fbH) y1 = fbH;
    uint32_t c = color | 0xff000000u;
    for (int yy = y0; yy < y1; yy++) {
        uint32_t *row = fb + (long)yy * fbW + x0;
        for (int xx = x0; xx < x1; xx++) *row++ = c;
    }
}

void Graphics::drawLine(int x1, int y1, int x2, int y2)
{
    int dx = x2 - x1, dy = y2 - y1;
    int sx = dx < 0 ? -1 : 1, sy = dy < 0 ? -1 : 1;
    if (dx < 0) dx = -dx;
    if (dy < 0) dy = -dy;
    int err = dx - dy;
    for (;;) {
        _putpixel(x1, y1);
        if (x1 == x2 && y1 == y2) break;
        int e2 = err * 2;
        if (e2 > -dy) { err -= dy; x1 += sx; }
        if (e2 < dx) { err += dx; y1 += sy; }
    }
}

void Graphics::drawArc(int x, int y, int width, int height, int startAngle, int arcAngle)
{
    if (width < 0 || height < 0) return;
    int xr = width / 2, yr = height / 2;
    x += xr;
    y += yr;
    if (xr == 0 && yr == 0) return;
    if (arcAngle < 0) {
        startAngle += arcAngle;
        arcAngle = -arcAngle;
    }
    if (arcAngle >= 360) {
        /* Bresenham-style ellipse outline, no per-degree libm. */
        int a = xr, b = yr;
        if (a < 1) a = 1;
        if (b < 1) b = 1;
        long aa = (long)a * a, bb = (long)b * b;
        int xi = 0, yi = b;
        long dx = 0, dy = 2 * aa * yi;
        long err = bb - aa * b + aa / 4;
        while (dx <= dy) {
            _putpixel(x + xi, y + yi);
            _putpixel(x - xi, y + yi);
            _putpixel(x + xi, y - yi);
            _putpixel(x - xi, y - yi);
            xi++;
            dx += 2 * bb;
            err += dx + bb;
            if (err >= 0) {
                yi--;
                dy -= 2 * aa;
                err -= dy;
            }
        }
        xi = a;
        yi = 0;
        dx = 2 * bb * xi;
        dy = 0;
        err = aa - bb * a + bb / 4;
        while (dy <= dx) {
            _putpixel(x + xi, y + yi);
            _putpixel(x - xi, y + yi);
            _putpixel(x + xi, y - yi);
            _putpixel(x - xi, y - yi);
            yi++;
            dy += 2 * aa;
            err += dy + aa;
            if (err >= 0) {
                xi--;
                dx -= 2 * bb;
                err -= dx;
            }
        }
        return;
    }
    const float conv = 3.1415926f / 180.0f;
    int step = arcAngle > 40 ? 3 : 2;
    float cr = (float)xr, sr = (float)yr;
    for (int a = startAngle; a < startAngle + arcAngle; a += step) {
        float a0 = (float)a * conv, a1 = (float)(a + step) * conv;
        drawLine(x + (int)(cr * cosf(a0)), y - (int)(sr * sinf(a0)),
                 x + (int)(cr * cosf(a1)), y - (int)(sr * sinf(a1)));
    }
}

void Graphics::fillArc(int x, int y, int w, int h, int startAngle, int arcAngle)
{
    (void)startAngle;
    (void)arcAngle;
    int xr = w / 2, yr = h / 2;
    int cx = x + xr, cy = y + yr;
    int r2;
    if (xr < 1) xr = 1;
    if (yr < 1) yr = 1;
    r2 = xr * xr;
    for (int yy = -yr; yy <= yr; yy++) {
        for (int xx = -xr; xx <= xr; xx++) {
            if (xx * xx * yr * yr + yy * yy * xr * xr <= r2 * yr * yr)
                _putpixel(cx + xx, cy + yy);
        }
    }
}

void Graphics::fillCircle(int cx, int cy, int r)
{
    int r2, yy, xx;
    if (r < 1) r = 1;
    r2 = r * r;
    for (yy = -r; yy <= r; yy++) {
        for (xx = -r; xx <= r; xx++) {
            if (xx * xx + yy * yy <= r2) _putpixel(cx + xx, cy + yy);
        }
    }
}

void Graphics::drawImage(Image* const image, int x, int y, int anchor)
{
    if (!image || !fb) return;
    int iw = image->getWidth(), ih = image->getHeight();
    const uint32_t *src = image->pixels();
    if (!src || iw <= 0 || ih <= 0) return;
    x = getAnchorX(x, iw, anchor);
    y = getAnchorY(y, ih, anchor);
    int x0 = x, y0 = y, x1 = x + iw, y1 = y + ih;
    int cx0 = clipX, cy0 = clipY, cx1 = clipX + clipW, cy1 = clipY + clipH;
    if (x0 < cx0) x0 = cx0;
    if (y0 < cy0) y0 = cy0;
    if (x1 > cx1) x1 = cx1;
    if (y1 > cy1) y1 = cy1;
    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (x1 > fbW) x1 = fbW;
    if (y1 > fbH) y1 = fbH;
    for (int yy = y0; yy < y1; yy++) {
        int sy = yy - y;
        uint32_t *d = fb + (long)yy * fbW + x0;
        const uint32_t *s = src + (long)sy * iw + (x0 - x);
        for (int xx = x0; xx < x1; xx++, d++, s++) {
            uint32_t p = *s;
            if ((p >> 24) < 16u) continue;
            *d = p | 0xff000000u;
        }
    }
}

void Graphics::drawChar(char c, int x, int y, int anchor)
{
    drawString(std::string(1, c), x, y, anchor);
}

void Graphics::drawString(const std::string& s, int x, int y, int anchor)
{
    int px = 15;
    int tw, th;
    if (font && font->pixelSize() > 0) px = font->pixelSize();
    tw = gd_ui_text_width(px, s.c_str(), (int)s.size());
    th = gd_ui_font_height(px);
    x = getAnchorX(x, tw, anchor);
    y = getAnchorY(y, th, anchor);
    gd_ui_draw_text(fb, fbW, fbH, x, y, s.c_str(), (int)s.size(), px, color,
                    clipX, clipY, clipW, clipH);
    if (font && font->bold() && px < 19) {
        gd_ui_draw_text(fb, fbW, fbH, x + 1, y, s.c_str(), (int)s.size(), px, color,
                        clipX, clipY, clipW, clipH);
    }
}
