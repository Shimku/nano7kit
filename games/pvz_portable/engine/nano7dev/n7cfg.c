/* nano7 PvZ device configuration: the engine's NANO7_* switches are fixed here instead of read from the
 * environment (the iPod has none). The dry run compiles the engine with -Dgetenv=n7_getenv; the iPod build
 * cannot (libstdc++'s <cstdlib> undefines the macro), so getenv itself is defined below and replaces newlib's. An earlier build
 * had neither on the iPod: every switch was off, and the first reanim went to the XML compiler. Same set as M1. */
#include <string.h>
#include <stdlib.h>
#include "n7dev.h"
static const char* const k_cfg[][2] = {
	{ "NANO7_N7R", "1" }, { "NANO7_LEVEL_FREE", "1" }, { "NANO7_LAZY_IMAGES", "1" }, { "NANO7_NO_POOL", "1" },
	{ "NANO7_FONT_TRIM", "1" }, { "NANO7_PRUNED_OK", "1" }, { "NANO7_PLAIN_ALLOC", "1" }, { "NANO7_FREE_CACHE", "1" },
	{ "NANO7_LAZY_REANIM", "1" }, { "NANO7_NO_MUSIC", "1" }, { "NANO7_COMPACT_REANIM", "1" }, { "NANO7_SMALL_VERTS", "1" },
	{ "NANO7_NO_SOUND", "1" },   /* silent build (no WAVs shipped) */
	{ "NANO7_POOLS", "attachments=98,coins=16,emitters=32,griditems=16,lawnmowers=16,particle systems=74,particles=96,"
	                 "plants=135,projectiles=82,reanims=316,trails=16,zombies=44"   /* effects trimmed (empty systems, no emitters), so fewer particles; roof pots + plants + pumpkins */ },
};
char* n7_getenv(const char* name)
{
	if (!name) return 0;
	for (size_t i = 0; i < sizeof k_cfg / sizeof k_cfg[0]; i++)
		if (!strcmp(k_cfg[i][0], name)) return (char*)k_cfg[i][1];
	if (!strcmp(name, "HOME")) return (char*)n7p_data_dir();
#ifdef N7_HOST_ENV   /* dry run only: the bot's own switches (NANO7_FIRST, NANO7_OUT, ...) come from the shell */
	if (!strncmp(name, "NANO7_", 6)) { extern char* n7_host_getenv(const char*); return n7_host_getenv(name); }
#endif
	return 0;
}
#ifndef N7_HOST_ENV
/* the iPod: the C library's getenv for every caller, engine and newlib alike (newlib's environment is empty) */
#undef getenv
char* getenv(const char* name) { return n7_getenv(name); }
#endif
