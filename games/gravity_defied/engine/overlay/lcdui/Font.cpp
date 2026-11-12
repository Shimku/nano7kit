#include "Font.h"
#include "../nano/gd_ui_font.h"

Font::Font(FontStyle st, FontSize pointSize)
{
    style = st;
    switch (pointSize) {
    case SIZE_LARGE:
        px = 22;
        break;
    case SIZE_SMALL:
        px = 12;
        break;
    case SIZE_MEDIUM:
    default:
        px = 15;
        break;
    }
}

Font::~Font() {}

int Font::getBaselinePosition() const { return gd_ui_font(px)->ascent; }
int Font::getHeight() const { return gd_ui_font_height(px); }

int Font::charWidth(char c)
{
    return gd_ui_char_width(px, (unsigned char)c);
}

int Font::stringWidth(const std::string& s)
{
    return gd_ui_text_width(px, s.c_str(), (int)s.size());
}

int Font::substringWidth(const std::string& string, int offset, int len)
{
    if (offset < 0) offset = 0;
    if (offset > (int)string.size()) offset = (int)string.size();
    if (len < 0) len = 0;
    if (offset + len > (int)string.size()) len = (int)string.size() - offset;
    return gd_ui_text_width(px, string.c_str() + offset, len);
}
