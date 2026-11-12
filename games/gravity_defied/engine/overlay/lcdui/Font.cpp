#include "Font.h"

Font::Font(FontStyle st, FontSize pointSize)
{
    style = st;
    switch (pointSize) {
    case SIZE_LARGE:
        px = 16;
        break;
    case SIZE_SMALL:
        px = 8;
        break;
    case SIZE_MEDIUM:
    default:
        px = 8;
        break;
    }
}

Font::~Font() {}

int Font::getBaselinePosition() const { return px; }
int Font::getHeight() const { return px; }

int Font::charWidth(char)
{
    return px;
}

int Font::stringWidth(const std::string& s)
{
    return (int)s.size() * px;
}

int Font::substringWidth(const std::string& string, int offset, int len)
{
    if (offset < 0) offset = 0;
    if (offset > (int)string.size()) offset = (int)string.size();
    if (len < 0) len = 0;
    if (offset + len > (int)string.size()) len = (int)string.size() - offset;
    return len * px;
}
