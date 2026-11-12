#include "Micro.h"

#include "GameCanvas.h"
#include "GamePhysics.h"
#include "MenuManager.h"
#include "LevelLoader.h"
#include "utils/Time.h"
#include "lcdui/CanvasImpl.h"
#include "rms/RecordStore.h"
#include "nano/gd_engine.h"
#include "gd_config.h"

#include <algorithm>

bool Micro::field_249 = false;
int Micro::gameLoadingStateStage = 0;
Micro *g_micro = nullptr;

Micro::Micro() { g_micro = this; }
Micro::~Micro() { if (g_micro == this) g_micro = nullptr; }

void Micro::setNumPhysicsLoops(int value) { numPhysicsLoops = value; }

void Micro::gameToMenu()
{
    gameCanvas->removeMenuCommand();
    isInGameMenu = true;
    menuManager->addOkAndBackCommands();
}

void Micro::menuToGame()
{
    menuManager->removeOkAndBackCommands();
    isInGameMenu = false;
    gameCanvas->addMenuCommand();
    if (gameCanvas) gameCanvas->isDrawingTime = true;
}

int64_t Micro::goLoadingStep()
{
    ++gameLoadingStateStage;
    gameCanvas->repaint();
    int64_t startTimeMillis = Time::currentTimeMillis();
    switch (gameLoadingStateStage) {
    case 1:
        levelLoader = new LevelLoader(mrgFilePath);
        break;
    case 2:
        gamePhysics = new GamePhysics(levelLoader);
        gameCanvas->init(gamePhysics);
        break;
    case 3:
        menuManager = new MenuManager(this);
        menuManager->initPart(1);
        break;
    case 4:
        menuManager->initPart(2);
        break;
    case 5:
        menuManager->initPart(3);
        break;
    case 6:
        menuManager->initPart(4);
        break;
    case 7:
        menuManager->initPart(5);
        break;
    case 8:
        menuManager->initPart(6);
        break;
    case 9:
        menuManager->initPart(7);
        break;
    case 10:
        gameCanvas->setMenuManager(menuManager);
        gameCanvas->setViewPosition(-50, 150);
        setMode(1);
        break;
    default:
        --gameLoadingStateStage;
        break;
    }
    return Time::currentTimeMillis() - startTimeMillis;
}

void Micro::init()
{
    /* Stepped from tick(); no 3 s splash waits (those blocked the UI task). */
    if (!gameCanvas) {
        gameCanvas = new GameCanvas(this);
        gameCanvas->requestRepaint(1);
        return;
    }
    if (gameLoadingStateStage < 10) {
        if (gameLoadingStateStage == 4) gameCanvas->requestRepaint(2);
        goLoadingStep();
        return;
    }
    gameCanvas->requestRepaint(0);
    isInited = true;
    gameCanvas->setCommandListener(gameCanvas);
    restart(false);
    requestMenu(0);
}

void Micro::restart(bool var1)
{
    gamePhysics->resetSmth(true);
    timeMs = 0;
    gameTimeMs = 0;
    field_246 = 0;
    if (var1) {
        gameCanvas->scheduleGameTimerTask(
            levelLoader->getName(menuManager->getCurrentLevel(), menuManager->getCurrentTrack()), 3000);
    }
    gameCanvas->method_129();
}

void Micro::destroyApp(bool var1)
{
    (void)var1;
    field_249 = false;
    field_242 = true;
    if (menuManager)
        menuManager->saveSmthToRecordStoreAndCloseIt();
    gd_log("quit: destroyApp saved, requesting home");
    gd_request_home("destroyApp");
}

void Micro::requestQuit() { destroyApp(true); }

void Micro::requestMenu(int kind)
{
    menuKind = kind;
    menuArmed = 1;
}

void Micro::startApp(int argc, char **argv)
{
    if (argc > 1) mrgFilePath = argv[1];
    RecordStore::setRecordStoreDir(gd_data_dir());
    field_249 = true;
}

void Micro::run()
{
    /* Blocking loop removed; tick() is the frame. */
}

void Micro::goalLoop()
{
    /* Blocking 1 s loop removed; tick() uses goalUntil. */
}

void Micro::tick()
{
    if (!field_249) return;

    if (!isInited) {
        init();
        if (gameCanvas) gameCanvas->repaint();
        return;
    }

    int mx = gd_menu_tap_x();
    int my = gd_menu_tap_y();
    if (isInGameMenu && menuManager && mx >= 0 && my >= 0)
        menuManager->nanoTap(mx, my);

    if (gd_tap_pause() && !isInGameMenu) {
        gd_log("input: pause");
        gameToMenu();
        requestMenu(1);
    }

    if (menuArmed) {
        menuArmed = 0;
        menuManager->method_201(menuKind);
        menuKind = -1;
    }

    if (isInGameMenu) {
        if (gamePhysics && gamePhysics->isGenerateInputAI) {
            int var9 = gamePhysics->updatePhysics();
            if (var9 != 0 && var9 != 4) gamePhysics->resetSmth(true);
            gamePhysics->method_53();
        }
        gameCanvas->repaint();
        if (menuManager && menuManager->currentGameMenu == nullptr) {
            field_249 = false;
            gd_log("quit: currentGameMenu null");
            gd_request_home("menu-null");
        }
        if (wasInMenu && !isInGameMenu) {
            /* handled below */
        }
        wasInMenu = 1;
        if (isInGameMenu) return;
    }

    if (wasInMenu && !isInGameMenu) {
        wasInMenu = 0;
        if (menuManager->method_196()) restart(true);
    }

    if (goalUntil) {
        if ((int)gd_millis() >= goalUntil) {
            goalUntil = 0;
            menuManager->method_215(gameTimeMs / 10L);
            requestMenu(2);
            gameCanvas->repaint();
            return;
        }
        for (int i = numPhysicsLoops; i > 0; --i) {
            if (gamePhysics->updatePhysics() == 5) {
                goalUntil = 0;
                return;
            }
        }
        gamePhysics->method_53();
        gameCanvas->repaint();
        return;
    }

    if (gamePhysics->method_21() != menuManager->method_210()) {
        int var5 = gameCanvas->loadSprites(menuManager->method_210());
        gamePhysics->method_22(var5);
        menuManager->method_211(var5);
    }

    for (int i = numPhysicsLoops; i > 0; --i) {
        if (field_248) gameTimeMs += 20L;
        if (timeMs == 0L) timeMs = Time::currentTimeMillis();
        int var5 = gamePhysics->updatePhysics();
        if (var5 == 5) {
            static int crash_n;
            if ((++crash_n % 60) == 1)
                gd_log("physics: crash n=%d gas=%d t=%d", crash_n, gd_held_gas(), (int)(gameTimeMs / 10));
        }
        if (var5 == 3 && field_246 == 0L) {
            field_246 = Time::currentTimeMillis() + 3000L;
            gameCanvas->scheduleGameTimerTask("Crashed", 3000);
        }
        if (field_246 != 0L && field_246 < Time::currentTimeMillis()) restart(true);
        if (var5 == 5) {
            gameCanvas->scheduleGameTimerTask("Crashed", 3000);
            restart(true);
        } else if (var5 == 4) {
            timeMs = 0L;
            gameTimeMs = 0L;
        } else if (var5 == 1 || var5 == 2) {
            if (var5 == 2) gameTimeMs -= 10L;
            if (!gamePhysics->field_69)
                gameCanvas->scheduleGameTimerTask("Wheelie!", 1000);
            else
                gameCanvas->scheduleGameTimerTask("Finished", 1000);
            goalUntil = (int)gd_millis() + 1000;
            field_248 = var5 != 4;
            gameCanvas->repaint();
            return;
        }
        field_248 = var5 != 4;
    }

    gamePhysics->method_53();
    gameCanvas->repaint();
}

void Micro::setMode(int mode) { gamePhysics->setMode(mode); }

void Micro::showHelp(const char *) {}
