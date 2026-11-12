#include "FontStorage.h"

std::shared_ptr<Font> FontStorage::getFont(Font::FontStyle style, Font::FontSize size)
{
    auto pair = std::make_pair(style, size);
    auto it = fontsMap.find(pair);
    if (it != fontsMap.end()) return it->second;
    auto font = std::make_shared<Font>(style, size);
    fontsMap.insert({ pair, font });
    return font;
}

void FontStorage::clearAll()
{
    fontsMap.clear();
}
