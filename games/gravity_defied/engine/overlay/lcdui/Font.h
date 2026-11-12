#pragma once

#include <string>

class Font {
public:
    enum FontSize {
        SIZE_SMALL = 8,
        SIZE_MEDIUM = 0,
        SIZE_LARGE = 16
    };

    enum FontStyle {
        STYLE_PLAIN = 0,
        STYLE_BOLD = 1,
        STYLE_ITALIC = 2
    };

    enum FontFace {
        FACE_SYSTEM = 0
    };

    Font(FontStyle style, FontSize pointSize);
    ~Font();

    int getBaselinePosition() const;
    int getHeight() const;
    int charWidth(char c);
    int stringWidth(const std::string& s);
    int substringWidth(const std::string& string, int offset, int len);
    int pixelSize() const { return px; }
    int bold() const { return style == STYLE_BOLD ? 1 : 0; }

private:
    FontStyle style;
    int px;
};
