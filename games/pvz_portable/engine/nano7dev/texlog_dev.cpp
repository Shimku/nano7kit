/* nano7 device build: the desktop texture log's interface as stubs (n7glcommon.c counts the real texture bytes). */
#include <string>
#include <unordered_map>
namespace Sexy { class MemoryImage; }
struct TexRec { std::string path; int w, h; bool alpha; int fmt; int touched; };
std::unordered_map<void*, TexRec> gNano7Tex;   /* stays empty: no measuring-tool table on the device */
int gNano7Epoch;
void nano7_tex_note(void*, Sexy::MemoryImage*, int) {}
void nano7_tex_drop(void*) {}
void nano7_tex_touch(void*) {}
extern "C" { unsigned long long fgl_tex_live(void) { return 0; } unsigned long long fgl_tex_peak(void) { return 0; } int fgl_tex_count(void) { return 0; } void fgl_reset_peak(void) {} }
