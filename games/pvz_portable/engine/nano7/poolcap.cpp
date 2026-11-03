#include "poolcap.h"
#include <cstdlib>
#include <cstring>
#include <cstdio>
#include <string>
#include <map>
#ifdef NANO7_DEVICE
extern "C" void n7_fatal(const char*);
#endif
struct PoolRec { unsigned int cap = 0, hw = 0, item = 0, levelhw = 0; };
std::map<std::string, PoolRec> gNano7Pools;
unsigned int nano7_pool_cap(const char* n, unsigned int def, unsigned int item)
{
	unsigned int cap = def;
	if (const char* e = getenv("NANO7_POOLS")) {
		std::string s(e), key = std::string(n) + "=";
		size_t p = s.find(key);
		if (p != std::string::npos && (p == 0 || s[p - 1] == ',')) cap = (unsigned)atoi(s.c_str() + p + key.size());
	}
	PoolRec& r = gNano7Pools[n]; r.cap = cap; r.item = item;
	return cap;
}
void nano7_pool_note(const char* n, unsigned int size)
{
	PoolRec& r = gNano7Pools[n];
	if (size > r.hw) r.hw = size;
	if (size > r.levelhw) r.levelhw = size;
}
void nano7_pool_full(const char* n, unsigned int m)
{
#ifdef NANO7_DEVICE
	{   /* a clear stop message on the iPod instead of a bare exit */
		static char msg[160];
		snprintf(msg, sizeof msg, "too many %s at once (this build keeps at most %u): stopped so it does not reboot", n, m);
		n7_fatal(msg);
	}
#endif
	fprintf(stderr, "NANO7 POOL FULL: %s (%u)\n", n, m);
	FILE* f = fopen(getenv("NANO7_OUT") ? getenv("NANO7_OUT") : "nano7_out.jsonl", "a");
	if (f) { fprintf(f, "{\"event\":\"pool_full\",\"pool\":\"%s\",\"cap\":%u}\n", n, m); fclose(f); }
	_Exit(3);
}
