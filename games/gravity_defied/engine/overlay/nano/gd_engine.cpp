#include "gd_engine.h"
#include "../gd_config.h"
#include "Micro.h"
#include "MenuManager.h"
#include "GameMenu.h"
#include "GameCanvas.h"
#include "lcdui/FontStorage.h"
#include "rms/RecordStore.h"

#include <cstdio>
#include <cstring>
#include <string>

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
#include "LevelLoader.h"

static int s_outcome;
static char s_track_name[48];

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

void gd_engine_clear_result(void) { s_outcome = 0; }

int gd_engine_track_result(void) { return s_outcome; }

const char *gd_engine_track_name(void) { return s_track_name; }

void gd_engine_note_physics(int v)
{
    if (!s_outcome && (v == 1 || v == 2 || v == 3 || v == 5))
        s_outcome = v;
}

int gd_engine_start_track(int level, int track, int league)
{
    if (!s_micro || !s_micro->isInited || !s_micro->menuManager || !s_micro->levelLoader)
        return -1;
    s_outcome = 0;
    s_track_name[0] = 0;
    {
        std::string n = s_micro->levelLoader->getName(level, track);
        std::snprintf(s_track_name, sizeof s_track_name, "%s", n.c_str());
    }
    s_micro->cancelPending();
    s_micro->menuManager->hostStartTrack(level, track, league);
    gd_log("track: start level=%d track=%d league=%d name=%s", level, track, league, s_track_name);
    return 0;
}

void gd_engine_select_row(int row)
{
    if (!s_micro || !s_micro->menuManager || !s_micro->menuManager->currentGameMenu) return;
    s_micro->menuManager->currentGameMenu->selectRow(row);
    gd_log("menu: row %d", row);
}

void gd_engine_pick_row(int row)
{
    if (!s_micro || !s_micro->menuManager || !s_micro->menuManager->currentGameMenu) return;
    s_micro->menuManager->currentGameMenu->selectRow(row);
    s_micro->menuManager->currentGameMenu->processGameActionUpd(1);
    gd_log("menu: pick %d", row);
}

void gd_engine_set_unlocks(int easy, int medium, int pro)
{
    if (!s_micro || !s_micro->menuManager) return;
    s_micro->menuManager->hostSetUnlocks(easy, medium, pro);
}
#endif
