// nano7 PvZ device layer (iPod): std::to_chars for float and double on snprintf. libstdc++'s own (Ryu tables,
// 136 KB of the app image) is then not linked. Same results for every value the engine formats; the shortest form
// is found by trying more digits until the text reads back as the same number.
#include <charconv>
#include <cstdio>
#include <cstdlib>
#include <cstring>
namespace {
template <class T> T back(const char* s);
template <> float back<float>(const char* s) { return strtof(s, nullptr); }
template <> double back<double>(const char* s) { return strtod(s, nullptr); }
std::to_chars_result put(char* f, char* l, const char* b, int n)
{
	if (n < 0 || n > l - f) return { l, std::errc::value_too_large };
	memcpy(f, b, (size_t)n);
	return { f + n, std::errc() };
}
char conv(std::chars_format fmt)
{
	switch (fmt) {
	case std::chars_format::fixed: return 'f';
	case std::chars_format::scientific: return 'e';
	case std::chars_format::hex: return 'a';
	default: return 'g';
	}
}
template <class T> std::to_chars_result shortest(char* f, char* l, T v, char c)
{
	char b[400];
	int n = 0;
	if (c == 'a') {   // to_chars writes hex without the 0x
		n = snprintf(b, sizeof b, "%a", (double)v);
		char* h = strstr(b, "0x"); if (h) { memmove(h, h + 2, strlen(h + 2) + 1); n -= 2; }
		return put(f, l, b, n);
	}
	const int maxp = sizeof(T) == 4 ? 9 : 17;
	for (int p = c == 'g' ? 1 : 0; p <= maxp; p++) {
		char fm[8] = { '%', '.', '*', c, 0 };
		n = snprintf(b, sizeof b, fm, p, (double)v);
		if (back<T>(b) == v) break;
	}
	return put(f, l, b, n);
}
template <class T> std::to_chars_result precise(char* f, char* l, T v, char c, int p)
{
	char b[400], fm[8] = { '%', '.', '*', c, 0 };
	if (p < 0) p = 6;
	if (p > 300) p = 300;
	int n = snprintf(b, sizeof b, fm, p, (double)v);
	if (c == 'a') { char* h = strstr(b, "0x"); if (h) { memmove(h, h + 2, strlen(h + 2) + 1); n -= 2; } }
	return put(f, l, b, n);
}
}
namespace std {
to_chars_result to_chars(char* f, char* l, float v) noexcept { return shortest(f, l, v, 'g'); }
to_chars_result to_chars(char* f, char* l, double v) noexcept { return shortest(f, l, v, 'g'); }
to_chars_result to_chars(char* f, char* l, float v, chars_format fmt) noexcept { return shortest(f, l, v, conv(fmt)); }
to_chars_result to_chars(char* f, char* l, double v, chars_format fmt) noexcept { return shortest(f, l, v, conv(fmt)); }
to_chars_result to_chars(char* f, char* l, float v, chars_format fmt, int p) noexcept { return precise(f, l, v, conv(fmt), p); }
to_chars_result to_chars(char* f, char* l, double v, chars_format fmt, int p) noexcept { return precise(f, l, v, conv(fmt), p); }
}
