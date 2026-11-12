#pragma once
#include <cstdint>
#include <string>

class GameCanvas;
class GamePhysics;
class MenuManager;
class LevelLoader;

class Micro {
    int64_t goLoadingStep();
    void destroyApp(bool var1);
    std::string mrgFilePath;
    int menuKind = -1;
    int menuArmed = 0;
    int goalUntil = 0;
    int wasInMenu = 0;

public:
    GameCanvas *gameCanvas = nullptr;
    LevelLoader *levelLoader = nullptr;
    GamePhysics *gamePhysics = nullptr;
    MenuManager *menuManager = nullptr;
    bool field_242 = false;
    int numPhysicsLoops = 2;
    int64_t timeMs = 0;
    int64_t gameTimeMs = 0;
    int64_t field_246 = 0;
    bool isInited = false;
    bool field_248 = false;
    static bool field_249;
    inline static bool isInGameMenu;
    static int gameLoadingStateStage;

    Micro();
    ~Micro();

    void startApp(int argc, char **argv);
    void gameToMenu();
    void menuToGame();
    void init();
    void restart(bool var1);
    void run();
    void tick();
    void goalLoop();
    void setNumPhysicsLoops(int value);
    void setMode(int mode);
    void showHelp(const char *progName);
    void requestMenu(int kind);
    void requestQuit();
    void cancelPending();
};

extern Micro *g_micro;
