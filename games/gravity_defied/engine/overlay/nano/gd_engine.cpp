#include "gd_engine.h"
#include "../gd_config.h"
#include "Micro.h"
#include "MenuManager.h"
#include "GameCanvas.h"
#include "lcdui/FontStorage.h"
#include "rms/RecordStore.h"

#include <cstdio>
#include <cstring>

static Micro *s_micro;
static char s_argv0[] = "GravityDefied";
static char s_mrg[256];
static char *s_av[3];

int gd_engine_start(const char *mrg, const char *data)
{
    gd_set_data_dir(data ? data : GD_DATA_DIR);
    s_mrg[0] = 0;
    if (mrg && mrg[0]) {
        std::snprintf(s_mrg, sizeof s_mrg, "%s", mrg);
    } else {
        std::snprintf(s_mrg, sizeof s_mrg, "levels.mrg");
    }
    s_av[0] = s_argv0;
    s_av[1] = s_mrg;
    s_av[2] = nullptr;
    RecordStore::setRecordStoreDir(data ? data : GD_DATA_DIR);
    if (!s_micro) s_micro = new Micro();
    s_micro->startApp(2, s_av);
    gd_log("engine: start mrg=%s data=%s", s_mrg, gd_data_dir());
    return 0;
}

void gd_engine_tick(void)
{
    if (s_micro) s_micro->tick();
}

int gd_engine_running(void)
{
    return Micro::field_249 ? 1 : 0;
}

void gd_engine_quit(void)
{
    if (s_micro) s_micro->requestQuit();
    RecordStore::shutdown();
    FontStorage::clearAll();
}

int gd_engine_in_menu(void)
{
    return Micro::isInGameMenu ? 1 : 0;
}

int gd_engine_loading(void)
{
    return (s_micro && s_micro->isInited) ? 0 : 1;
}

void gd_engine_dump_stats(char *buf, unsigned n)
{
    if (!buf || !n) return;
    std::snprintf(buf, n, "inited=%d menu=%d loading=%d running=%d arena_used=%u peak=%u fails=%u",
                  (s_micro && s_micro->isInited) ? 1 : 0,
                  Micro::isInGameMenu ? 1 : 0,
                  (s_micro && s_micro->isInited) ? 0 : 1,
                  Micro::field_249 ? 1 : 0,
                  gd_arena_used(), gd_arena_peak(), gd_arena_fails());
}

#ifdef GD_HOST
void gd_host_key(int code)
{
    if (s_micro && s_micro->gameCanvas) {
        s_micro->gameCanvas->keyPressed(code);
        s_micro->gameCanvas->keyReleased(code);
    }
}

void gd_host_key_up(int code)
{
    if (s_micro && s_micro->gameCanvas)
        s_micro->gameCanvas->keyReleased(code);
}
#endif
