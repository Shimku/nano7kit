// nano7 PvZ device layer (iPod): what the dry run gets from its desktop-only files (nano7/bot.cpp)
class LawnApp;
void nano7_bot_update(LawnApp*) {}
unsigned long long nano7_music_bytes;
extern "C" {
void* __dso_handle = nullptr;   // one image, never unloaded with destructors run: __cxa_atexit just records
void _init(void) {}
void _fini(void) {}
}
extern "C" {
void n7_fatal(const char*);
void __cxa_pure_virtual() { n7_fatal("pure virtual call"); }
// the unwinder's table lookup for one static image (libgcc only takes the linker symbols when this is absent,
// and an undefined weak symbol cannot go into a relocatable .hbapp)
extern const char __exidx_start[], __exidx_end[];
unsigned long __gnu_Unwind_Find_exidx(unsigned long pc, int* nrec)
{
	(void)pc; *nrec = (int)((__exidx_end - __exidx_start) / 8);
	return (unsigned long)__exidx_start;
}
void __libc_fini(void) {}
}
