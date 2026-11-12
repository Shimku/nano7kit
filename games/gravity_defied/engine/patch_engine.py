#!/usr/bin/env python3
"""Apply nano7kit edits to a clean gravity_defied_cpp tree at pin 91bd283.

The LCDUI/RMS/Time/Micro files are copied over afterwards; this script only
touches game logic that we keep compiling from upstream.
"""
from __future__ import annotations

from pathlib import Path


class PatchError(RuntimeError):
    pass


def _sub(path: Path, old: str, new: str, *, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise PatchError(f"{path.name}: missing hunk {label}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def apply(engine: Path) -> None:
    src = Path(engine) / "src"

    _sub(
        src / "GamePhysics.cpp",
        """                int var6;
                if (var5 == 1) {
                    do {
                        method_47(index10);
                        if ((var6 = method_46(index10)) == 0) {
                            return 5;
                        }
                    } while (var6 != 2);
                }
""",
        """                int var6;
                if (var5 == 1) {
                    int spins = 0;
                    do {
                        method_47(index10);
                        if ((var6 = method_46(index10)) == 0) {
                            gd_log("physics: method_46=0 after method_47");
                            return 5;
                        }
                        if (++spins > 16) {
                            gd_log("physics: spin cap");
                            if (method_46(index10) == 0)
                                method_26(true);
                            break;
                        }
                    } while (var6 != 2);
                }
""",
        label="method_39 inner loop cap",
    )
    _sub(
        src / "GamePhysics.cpp",
        """int GamePhysics::method_39(int var1)
{
    bool var2 = field_68;
    int var3 = 0;
    int var4 = var1;

label77:
    do {
""",
        """int GamePhysics::method_39(int var1)
{
    bool var2 = field_68;
    int var3 = 0;
    int var4 = var1;
    int steps = 0;

label77:
    do {
        if (++steps > 1024) {
            gd_log("physics: step cap");
            return 0;
        }
""",
        label="method_39 outer step cap",
    )
    _sub(
        src / "GamePhysics.cpp",
        """            if (var5 == 0) {
                var4 = (var3 + var4) >> 1;
                goto label77;
            }
""",
        """            if (var5 == 0) {
                if (var3 == 0) {
                    int lifts = 0;
                    do {
                        method_26(true);
                        lifts++;
                        var5 = method_46(index10);
                    } while (var5 == 0 && lifts < 24);
                    if (lifts)
                        gd_log("physics: lift buried %d", lifts);
                    if (var5 == 0)
                        return 5;
                    goto label77;
                }
                var4 = (var3 + var4) >> 1;
                goto label77;
            }
""",
        label="method_39 lift when already overlapping",
    )
    _sub(
        src / "GamePhysics.cpp",
        """void GamePhysics::resetSmth(bool unused)
{
    (void)unused;
    field_44 = 0;
    method_27(levelLoader->method_93(), levelLoader->method_94());
    field_31 = 0;
    field_39 = 0;
    field_35 = false;
    field_36 = false;
    field_68 = false;
    field_69 = false;
    isGenerateInputAI = false;
    field_41 = false;
    field_42 = false;
    levelLoader->gameLevel->method_183(field_29[2]->motoComponents[5]->xF16 + 98304 - const175_1_half[0], field_29[1]->motoComponents[5]->xF16 - 98304 + const175_1_half[0]);
}
""",
        """void GamePhysics::resetSmth(bool unused)
{
    (void)unused;
    field_44 = 0;
    method_27(levelLoader->method_93(), levelLoader->method_94());
    field_31 = 0;
    field_39 = 0;
    field_35 = false;
    field_36 = false;
    field_68 = false;
    field_69 = false;
    isGenerateInputAI = false;
    field_41 = false;
    field_42 = false;
    /* method_27 writes index01 only. Copy that pose into index10, then lift
     * out of the track so method_39 does not crash-restart on spawn overlap. */
    for (int p = 0; p < 6; ++p) {
        TimerOrMotoPartOrMenuElem *a = field_29[p]->motoComponents[index01].get();
        TimerOrMotoPartOrMenuElem *b = field_29[p]->motoComponents[index10].get();
        b->xF16 = a->xF16;
        b->yF16 = a->yF16;
        b->angleF16 = a->angleF16;
        b->field_382 = a->field_382;
        b->field_383 = a->field_383;
        b->field_384 = a->field_384;
    }
    {
        int lifts = 0;
        int hit = method_46(index10);
        while (hit == 0 && lifts < 48) {
            method_26(true);
            lifts++;
            hit = method_46(index10);
        }
        if (lifts)
            gd_log("physics: spawn lift %d (hit=%d)", lifts, hit);
        if (hit == 1)
            method_47(index10);
    }
    levelLoader->gameLevel->method_183(field_29[2]->motoComponents[5]->xF16 + 98304 - const175_1_half[0], field_29[1]->motoComponents[5]->xF16 - 98304 + const175_1_half[0]);
}
""",
        label="resetSmth settle spawn",
    )

    _sub(
        src / "IGameMenuElement.h",
        """class IGameMenuElement {
public:
    virtual void setText(std::string text) = 0;
""",
        """class IGameMenuElement {
public:
    virtual ~IGameMenuElement() = default;
    virtual void setText(std::string text) = 0;
""",
        label="virtual dtor",
    )

    _sub(
        src / "GameMenu.cpp",
        """void GameMenu::clearVector()
{
    vector.clear();
    field_105 = 0;
    field_106 = 0;
    field_95 = -1;
}
""",
        """void GameMenu::clearVector()
{
    for (auto *e : vector) {
        if (e && !e->isNotTextRender())
            delete e;
    }
    vector.clear();
    field_105 = 0;
    field_106 = 0;
    field_95 = -1;
}

int GameMenu::hitRowAtY(int y)
{
    int var2 = field_101;
    if (field_94 != "") {
        var2 += font->getBaselinePosition() + 8;
    }
    if (canvasHeight < 100) {
        ++var2;
    } else {
        var2 += 6;
    }
    for (int i = field_105; i < field_106 + 1 && i < (int)vector.size(); ++i) {
        IGameMenuElement *el = vector[i];
        int rowH;
        if (el->isNotTextRender()) {
            rowH = font2->getBaselinePosition() + field_103;
        } else {
            rowH = (TextRender::getBaselinePosition() < GameCanvas::spriteSizeY[5]
                        ? GameCanvas::spriteSizeY[5]
                        : TextRender::getBaselinePosition())
                + field_103;
        }
        if (y >= var2 && y < var2 + rowH && el->isNotTextRender())
            return i;
        var2 += rowH;
    }
    return -1;
}

void GameMenu::selectRow(int i)
{
    if (i < 0 || i >= (int)vector.size()) return;
    field_95 = i;
}
""",
        label="GameMenu clearVector + hit test",
    )
    _sub(
        src / "GameMenu.h",
        """    void clearVector();
    std::string makeString();
""",
        """    void clearVector();
    int hitRowAtY(int y);
    void selectRow(int i);
    std::string makeString();
""",
        label="GameMenu.h hit test",
    )
    gm = src / "GameMenu.cpp"
    t = gm.read_text(encoding="utf-8")
    if '#include "TextRender.h"' not in t:
        gm.write_text(t.replace('#include "lcdui/Graphics.h"\n',
                                '#include "lcdui/Graphics.h"\n#include "TextRender.h"\n'),
                      encoding="utf-8")

    _sub(
        src / "GameMenu.cpp",
        """    field_104 = xPos + 7;
    field_103 = 2;
    field_110 = 0;
""",
        """    xPos = 24;
    field_104 = 24;
    field_103 = 2;
    {
        int need = 36 - font2->getBaselinePosition();
        if (need > field_103) field_103 = need;
    }
    field_110 = 0;
""",
        label="GameMenu 36px rows",
    )
    _sub(
        src / "GameMenu.cpp",
        """        for (i = field_105; i < field_106 + 1; ++i) {
            IGameMenuElement* var4 = vector[i];
            graphics->setColor(0, 0, 0);
            var4->render(graphics, var2, field_104);
            if (i == field_95 && var4->isNotTextRender()) {
                int var5 = xPos - micro->gameCanvas->helmetSpriteWidth / 2;
                int var6 = var2 + font2->getBaselinePosition() / 2 - micro->gameCanvas->helmetSpriteHeight / 2;
                graphics->setClip(var5, var6, micro->gameCanvas->helmetSpriteWidth, micro->gameCanvas->helmetSpriteHeight);
                graphics->drawImage(micro->gameCanvas->helmetImage.get(), var5 - micro->gameCanvas->helmetSpriteWidth * (field_110 % 6), var6 - micro->gameCanvas->helmetSpriteHeight * (field_110 / 6), 20);
                graphics->setClip(0, 0, canvasWidth, canvasHeight);
                ++field_110;
                if (field_110 > 30) {
                    field_110 = 0;
                }
            }

            if (var4->isNotTextRender()) {
                var2 += font2->getBaselinePosition() + field_103;
            } else {
                var2 += (TextRender::getBaselinePosition() < GameCanvas::spriteSizeY[5] ? GameCanvas::spriteSizeY[5] : TextRender::getBaselinePosition()) + field_103;
            }
        }
""",
        """        for (i = field_105; i < field_106 + 1; ++i) {
            IGameMenuElement* var4 = vector[i];
            int rowH = var4->isNotTextRender()
                ? font2->getBaselinePosition() + field_103
                : (TextRender::getBaselinePosition() < GameCanvas::spriteSizeY[5] ? GameCanvas::spriteSizeY[5] : TextRender::getBaselinePosition()) + field_103;
            graphics->setColor(0, 0, 0);
            var4->render(graphics, var2, field_104);
            if (i == field_95 && var4->isNotTextRender()) {
                graphics->fillCircle(10, var2 + rowH / 2 - 2, 3);
            }

            var2 += rowH;
        }
""",
        label="GameMenu selection highlight",
    )
    _sub(
        src / "GameMenu.cpp",
        """            graphics->drawString(field_94, xPos, var2, 20);
            var2 += font->getBaselinePosition() + 2;
""",
        """            graphics->drawString(field_94, xPos, var2, 20);
            var2 += font->getBaselinePosition() + 8;
""",
        label="GameMenu title gap",
    )
    _sub(
        src / "GameMenu.cpp",
        """    field_101 = 1;
""",
        """    field_101 = 8;
""",
        label="GameMenu top padding",
    )

    _sub(
        src / "LevelLoader.h",
        """    LevelLoader(const std::filesystem::path& mrgFilePath);
""",
        """    LevelLoader(const std::string& mrgFilePath);
""",
        label="LevelLoader.h path",
    )
    _sub(
        src / "LevelLoader.cpp",
        """LevelLoader::LevelLoader(const std::filesystem::path& mrgFilePath)
{
    for (int i = 0; i < 3; ++i) {
        field_123[i] = (int)((int64_t)((GamePhysics::const175_1_half[i] + 19660) >> 1) * (int64_t)((GamePhysics::const175_1_half[i] + 19660) >> 1) >> 16);
        field_124[i] = (int)((int64_t)((GamePhysics::const175_1_half[i] - 19660) >> 1) * (int64_t)((GamePhysics::const175_1_half[i] - 19660) >> 1) >> 16);
    }

    if (!mrgFilePath.string().empty()) {
        FileStream* fileStream = new FileStream(mrgFilePath, std::ios::in | std::ios::binary);
        if (!fileStream->isOpen()) {
            throw std::system_error(errno, std::system_category(), "Failed to open " + mrgFilePath.string());
        }
        levelFileStream = fileStream;
    } else {
        EmbedFileStream* embedFileStream = new EmbedFileStream("levels.mrg");
        levelFileStream = static_cast<FileStream*>(embedFileStream);
    }

    loadLevels();
    method_87();
}
""",
        """LevelLoader::LevelLoader(const std::string& mrgFilePath)
{
    for (int i = 0; i < 3; ++i) {
        field_123[i] = (int)((int64_t)((GamePhysics::const175_1_half[i] + 19660) >> 1) * (int64_t)((GamePhysics::const175_1_half[i] + 19660) >> 1) >> 16);
        field_124[i] = (int)((int64_t)((GamePhysics::const175_1_half[i] - 19660) >> 1) * (int64_t)((GamePhysics::const175_1_half[i] - 19660) >> 1) >> 16);
    }

    std::string path = mrgFilePath.empty() ? std::string("levels.mrg") : mrgFilePath;
    FileStream* fileStream = new FileStream(path, std::ios::in | std::ios::binary);
    if (!fileStream->isOpen()) {
        gd_log("levels: cannot open %s", path.c_str());
    }
    levelFileStream = fileStream;

    loadLevels();
    method_87();
}
""",
        label="LevelLoader ctor",
    )
    ll = src / "LevelLoader.cpp"
    t = ll.read_text(encoding="utf-8")
    if '#include "gd_config.h"' not in t:
        ll.write_text('#include "gd_config.h"\n' + t, encoding="utf-8")

    # MenuManager: drop exceptions, destroy(), blocking menu loop, Exit Game.
    mm = src / "MenuManager.cpp"
    text = mm.read_text(encoding="utf-8")
    reps = [
        (
            """        try {
            recordStore = RecordStore::openRecordStore("GDTRStates", true);
            isRecordStoreOpened = true;
            return;
        } catch (RecordStoreException& var9) {
            isRecordStoreOpened = false;
            return;
        }
""",
            """        recordStore = RecordStore::openRecordStore("GDTRStates", true);
        isRecordStoreOpened = recordStore != nullptr;
        return;
""",
        ),
        (
            """        RecordEnumeration* records;
        try {
            records = recordStore->enumerateRecords(nullptr, nullptr, false);
        } catch (RecordStoreNotOpenException& var8) {
            return;
        }

        std::vector<int8_t> var3;
        if (records->numRecords() > 0) {
            try {
                var3 = records->nextRecord();
                records->reset();
                recorcStoreRecordId = records->nextRecordId();
            } catch (RecordStoreException& var7) {
                return;
            }

            if (var3.size() <= 19) {
                for (std::size_t i = 0; i < var3.size(); ++i) {
                    field_278[i] = var3[i];
                }
            }

            records->destroy();
        }
""",
            """        if (!recordStore) {
            return;
        }
        RecordEnumeration* records = recordStore->enumerateRecords(nullptr, nullptr, false);
        std::vector<int8_t> var3;
        if (records && records->numRecords() > 0) {
            var3 = records->nextRecord();
            records->reset();
            recorcStoreRecordId = records->nextRecordId();
            if (var3.size() <= 19) {
                for (std::size_t i = 0; i < var3.size(); ++i) {
                    field_278[i] = var3[i];
                }
            }
        }
""",
        ),
        (
            """        try {
            field_345.at(field_370) = field_369;
        } catch (std::exception& var6) {
            field_370 = 0;
            field_369 = 0;
            field_345[field_370] = field_369;
        }
""",
            """        if (field_370 >= 0 && field_370 < (int)field_345.size()) {
            field_345[field_370] = field_369;
        } else {
            field_370 = 0;
            field_369 = 0;
            field_345[field_370] = field_369;
        }
""",
        ),
        (
            """        try {
            settingsStringTrack->setAvailableOptions(field_342[field_370]);
        } catch (std::exception& var5) {
            settingsStringTrack->setAvailableOptions(0);
        }
""",
            """        if (field_370 >= 0 && field_370 < 3) {
            settingsStringTrack->setAvailableOptions(field_342[field_370]);
        } else {
            settingsStringTrack->setAvailableOptions(0);
        }
""",
        ),
        (
            """            if (menuElement == settingStringExitGame) {
                method_1(currentGameMenu->getGameMenu(), false);
                return;
            }
""",
            """            if (menuElement == settingStringExitGame) {
                gd_log("input: Exit Game");
                micro->requestQuit();
                return;
            }
""",
        ),
        (
            """        try {
            recordStore->closeRecordStore();
            isRecordStoreOpened = false;
        } catch (RecordStoreException& var1) {
        }
""",
            """        recordStore->closeRecordStore();
        isRecordStoreOpened = false;
""",
        ),
        (
            """        try {
            recorcStoreRecordId = recordStore->addRecord(field_278, 0, 19);
        } catch (RecordStoreNotOpenException& var2) {
        } catch (RecordStoreException& var3) {
        }
    } else {
        try {
            recordStore->setRecord(recorcStoreRecordId, field_278, 0, 19);
        } catch (RecordStoreNotOpenException& var4) {
        } catch (RecordStoreException& var5) {
        }
    }
""",
            """        recorcStoreRecordId = recordStore->addRecord(field_278, 0, 19);
        gd_log("save: GDTRStates add id=%d", recorcStoreRecordId);
    } else {
        recordStore->setRecord(recorcStoreRecordId, field_278, 0, 19);
        gd_log("save: GDTRStates set id=%d", recorcStoreRecordId);
    }
""",
        ),
        (
            """        availableLeagues = 0;
        field_344 = 1;
        field_342[0] = 0;
        field_342[1] = 0;
        field_342[2] = -1;
    }
        return;
""",
            """        availableLeagues = 0;
        field_344 = 1;
        field_342[0] = 0;
        field_342[1] = 0;
        field_342[2] = -1;
#ifdef GD_HOST
        /* Host harness plays Easy, Medium and Pro without grinding unlocks. */
        availableLeagues = 2;
        field_344 = 2;
        field_342[0] = 9;
        field_342[1] = 9;
        field_342[2] = 9;
#endif
    }
        return;
""",
        ),
        (
            """        gameMenuIngame->addMenuElement(settingStringContinue);
        gameMenuIngame->addMenuElement(field_333);
        gameMenuIngame->addMenuElement(taskOptions);
        gameMenuIngame->addMenuElement(taskHelp);
        gameMenuIngame->addMenuElement(settingStringPlayMenu);
""",
            """        gameMenuIngame->addMenuElement(settingStringContinue);
        gameMenuIngame->addMenuElement(field_333);
        gameMenuIngame->addMenuElement(taskOptions);
        gameMenuIngame->addMenuElement(taskHelp);
        gameMenuIngame->addMenuElement(settingStringPlayMenu);
        gameMenuIngame->addMenuElement(settingStringExitGame);
""",
        ),
        (
            """        field_360 = field_371;
        return;
""",
            """        field_360 = field_371;
        gd_log("save: loaded leagues=%d levels=%d tracks=%d,%d,%d shadows=%d",
               (int)availableLeagues, (int)field_344,
               (int)field_342[0], (int)field_342[1], (int)field_342[2],
               (int)isDisabledShadows);
        return;
""",
        ),
    ]
    for old, new in reps:
        if old not in text:
            raise PatchError("MenuManager.cpp: missing hunk:\n" + old[:120])
        text = text.replace(old, new, 1)

    wait_start = """    int64_t currentTimeMillis = Time::currentTimeMillis();
    micro->gameCanvas->isDrawingTime = false;
    int64_t var6 = 0L;
    int8_t var8 = 50;
    micro->gamePhysics->method_53();
    micro->gameToMenu();

    while (Micro::isInGameMenu && Micro::field_249 && currentGameMenu != nullptr) {
"""
    wait_end = """    micro->timeMs += Time::currentTimeMillis() - currentTimeMillis;
    micro->gameCanvas->isDrawingTime = true;
    if (currentGameMenu == nullptr) {
        Micro::field_249 = false;
    }
}
"""
    if wait_start not in text or wait_end not in text:
        raise PatchError("MenuManager.cpp: missing method_201 wait loop")
    i0 = text.index(wait_start)
    i1 = text.index(wait_end)
    text = text[:i0] + """    micro->gamePhysics->method_53();
    micro->gameToMenu();
    micro->gameCanvas->isDrawingTime = false;
}

void MenuManager::nanoTap(int x, int y)
{
    (void)x;
    if (!currentGameMenu) return;
    int row = currentGameMenu->hitRowAtY(y);
    if (row < 0) return;
    currentGameMenu->selectRow(row);
    currentGameMenu->processGameActionUpd(1);
}
""" + text[i1 + len(wait_end):]

    old_fill = """void MenuManager::method_202(Graphics* var1)
{
    if (currentGameMenu != nullptr && !field_377) {
        micro->gameCanvas->drawGame(var1);
        fillCanvasWithImage(var1);
        currentGameMenu->render_76(var1);
    }
}
"""
    new_fill = """void MenuManager::method_202(Graphics* var1)
{
    if (currentGameMenu != nullptr && !field_377) {
        micro->gameCanvas->drawGame(var1);
        currentGameMenu->render_76(var1);
    }
}
"""
    if old_fill not in text:
        raise PatchError("MenuManager.cpp: missing method_202")
    text = text.replace(old_fill, new_fill, 1)

    old_raster = """void MenuManager::fillCanvasWithImage(Graphics* graphics)
{
    for (int y = 0; y < getCanvasHeight(); y += rasterImage->getHeight()) {
        for (int x = 0; x < getCanvasWidth(); x += rasterImage->getWidth()) {
            graphics->drawImage(rasterImage.get(), x, y, 20);
        }
    }
}
"""
    new_raster = """void MenuManager::fillCanvasWithImage(Graphics* graphics)
{
    (void)graphics;
}
"""
    if old_raster not in text:
        raise PatchError("MenuManager.cpp: missing fillCanvasWithImage")
    text = text.replace(old_raster, new_raster, 1)

    if '#include "gd_config.h"' not in text:
        text = '#include "gd_config.h"\n' + text
    if "hostStartTrack" not in text:
        text += """
#ifdef GD_HOST
void MenuManager::hostStartTrack(int level, int track, int league)
{
    if (!settingStringLevel || !settingsStringTrack || !settingsStringLeague) return;
    if (level < 0) level = 0;
    if (level > 2) level = 2;
    if (track < 0) track = 0;
    if (league < 0) league = 0;
    micro->cancelPending();
    settingStringLevel->setCurentOptionPos(level);
    settingsStringTrack->setAvailableOptions(field_342[level]);
    settingsStringTrack->setCurentOptionPos(track);
    settingsStringLeague->setCurentOptionPos(league);
    micro->gamePhysics->disableGenerateInputAI();
    micro->levelLoader->method_88(level, track);
    micro->gamePhysics->setMotoLeague(league);
    field_354 = level;
    field_355 = track;
    field_357 = true;
    micro->menuToGame();
    micro->restart(true);
}

void MenuManager::hostSetUnlocks(int easy, int medium, int pro)
{
    field_342[0] = (int8_t)easy;
    field_342[1] = (int8_t)medium;
    field_342[2] = (int8_t)pro;
    availableLeagues = (pro >= 0) ? 2 : (medium > 0) ? 1 : 0;
    field_344 = (pro >= 0) ? 2 : 1;
    if (settingsStringTrack)
        settingsStringTrack->setAvailableOptions(field_342[settingStringLevel ? settingStringLevel->getCurrentOptionPos() : 0]);
    if (settingStringLevel)
        settingStringLevel->setAvailableOptions(field_344);
    if (settingsStringLeague)
        settingsStringLeague->setAvailableOptions(availableLeagues);
    gd_log("unlocks: set tracks=%d,%d,%d leagues=%d levels=%d",
           (int)field_342[0], (int)field_342[1], (int)field_342[2],
           (int)availableLeagues, (int)field_344);
}
#endif
"""
    mm.write_text(text, encoding="utf-8")

    mh = src / "MenuManager.h"
    ht = mh.read_text(encoding="utf-8")
    if "void nanoTap" not in ht:
        ht = ht.replace("    void processKeyCode(int keyCode);\n};",
                        "    void processKeyCode(int keyCode);\n    void nanoTap(int x, int y);\n"
                        "#ifdef GD_HOST\n"
                        "    void hostStartTrack(int level, int track, int league);\n"
                        "    void hostSetUnlocks(int easy, int medium, int pro);\n"
                        "#endif\n};")
        mh.write_text(ht, encoding="utf-8")
    elif "hostStartTrack" not in ht:
        ht = ht.replace("    void nanoTap(int x, int y);\n};",
                        "    void nanoTap(int x, int y);\n"
                        "#ifdef GD_HOST\n"
                        "    void hostStartTrack(int level, int track, int league);\n"
                        "    void hostSetUnlocks(int easy, int medium, int pro);\n"
                        "#endif\n};")
        mh.write_text(ht, encoding="utf-8")

    rm = src / "RecordManager.cpp"
    rt = rm.read_text(encoding="utf-8")
    rm_reps = [
        (
            """    try {
        str = std::to_string(var1) + std::to_string(var2);
        recordStore = RecordStore::openRecordStore(str, true);
    } catch (RecordStoreException& var9) {
        return;
    }
""",
            """    str = std::to_string(var1) + std::to_string(var2);
    recordStore = RecordStore::openRecordStore(str, true);
    if (!recordStore) {
        return;
    }
""",
        ),
        (
            """    RecordEnumeration* recordEnum;
    try {
        recordEnum = recordStore->enumerateRecords(nullptr, nullptr, false);
    } catch (RecordStoreNotOpenException& var8) {
        return;
    }

    if (recordEnum->numRecords() > 0) {
        std::vector<int8_t> var4;
        try {
            var4 = recordEnum->nextRecord();
            recordEnum->reset();
            packedRecordInfoRecordId = recordEnum->nextRecordId();
        } catch (RecordStoreNotOpenException& var5) {
            return;
        } catch (InvalidRecordIDException& var6) {
            return;
        } catch (RecordStoreException& var7) {
            return;
        }

        loadRecordInfo(var4);
        recordEnum->destroy();
    }
""",
            """    RecordEnumeration* recordEnum = recordStore->enumerateRecords(nullptr, nullptr, false);
    if (recordEnum && recordEnum->numRecords() > 0) {
        std::vector<int8_t> var4 = recordEnum->nextRecord();
        recordEnum->reset();
        packedRecordInfoRecordId = recordEnum->nextRecordId();
        loadRecordInfo(var4);
    }
""",
        ),
        (
            """        try {
            packedRecordInfoRecordId = recordStore->addRecord(packedRecordInfo, 0, 96);
        } catch (RecordStoreNotOpenException& var1) {
        } catch (RecordStoreException& var2) {
        }
    } else {
        try {
            recordStore->setRecord(packedRecordInfoRecordId, packedRecordInfo, 0, 96);
        } catch (RecordStoreNotOpenException& var3) {
        } catch (RecordStoreException& var4) {
        }
    }
""",
            """        packedRecordInfoRecordId = recordStore->addRecord(packedRecordInfo, 0, 96);
        gd_log("save: records add %s id=%d", str.c_str(), packedRecordInfoRecordId);
    } else {
        recordStore->setRecord(packedRecordInfoRecordId, packedRecordInfo, 0, 96);
        gd_log("save: records set %s id=%d", str.c_str(), packedRecordInfoRecordId);
    }
""",
        ),
        (
            """            try {
                // RecordStore *var10000 = recordStore;
                RecordStore::deleteRecordStore(names[i]);
            } catch (RecordStoreException& var3) {
            }
""",
            """            RecordStore::deleteRecordStore(names[i]);
""",
        ),
        (
            """        try {
            recordStore->closeRecordStore();
            return;
        } catch (RecordStoreException& var1) {
        }
""",
            """        recordStore->closeRecordStore();
        return;
""",
        ),
    ]
    for old, new in rm_reps:
        if old not in rt:
            raise PatchError("RecordManager.cpp: missing hunk:\n" + old[:160])
        rt = rt.replace(old, new, 1)
    if '#include "gd_config.h"' not in rt:
        rt = '#include "gd_config.h"\n' + rt
    rm.write_text(rt, encoding="utf-8")

    gp = src / "GamePhysics.cpp"
    gt = gp.read_text(encoding="utf-8")
    if '#include "gd_config.h"' not in gt:
        gp.write_text(gt.replace('#include "GamePhysics.h"\n',
                                 '#include "GamePhysics.h"\n#include "gd_config.h"\n'),
                      encoding="utf-8")

    _sub(
        src / "GameCanvas.h",
        """    int field_232 = 2;
""",
        """    int field_232 = 0;
""",
        label="default phone keypad",
    )

    gc = src / "GameCanvas.cpp"
    gct = gc.read_text(encoding="utf-8")
    if '#include "gd_config.h"' not in gct:
        gc.write_text('#include "gd_config.h"\n' + gct, encoding="utf-8")

    _sub(
        src / "GameCanvas.cpp",
        """    gamePhysics->method_30(var1, var2);
}
""",
        """    if (gamePhysics) {
        gamePhysics->method_30(var1, var2);
    }
}
""",
        label="handleUpdatedInput null physics",
    )
    _sub(
        src / "GameCanvas.cpp",
        """        graphics->drawString("0:00.", width - defaultFontWidth00, height2 - 5, 40);
        graphics->drawString("00", width - defaultFontWidth00, height2 - 5, 36);
    } else {
        setColor(0, 0, 0);
        graphics->drawString(stringWithTime, width - defaultFontWidth00, height2 - 5, 40);
        graphics->drawString(time10MsToStringCache[time10MsPart], width - defaultFontWidth00, height2 - 5, 36);
    }
""",
        """        graphics->setFont(FontStorage::getFont(Font::STYLE_PLAIN, Font::SIZE_MEDIUM));
        graphics->drawString("0:00.00", 8, 8, 20);
    } else {
        setColor(0, 0, 0);
        graphics->setFont(FontStorage::getFont(Font::STYLE_PLAIN, Font::SIZE_MEDIUM));
        graphics->drawString(stringWithTime + time10MsToStringCache[time10MsPart], 8, 8, 20);
    }
""",
        label="drawTime top left",
    )
    _sub(
        src / "GameCanvas.cpp",
        """            if (isDrawingTime) {
                drawTime(micro->gameTimeMs / 10L);
            }
""",
        """            if (isDrawingTime) {
                drawTime(micro->gameTimeMs / 10L);
            }
            if (!Micro::isInGameMenu) {
                setColor(40, 40, 40);
                graphics->fillCircle(width - 14, 12, 2);
                graphics->fillCircle(width - 14, 20, 2);
                graphics->fillCircle(width - 14, 28, 2);
            }
""",
        label="pause dots",
    )
    _sub(
        src / "GameCanvas.cpp",
        """void GameCanvas::method_161(int var1, bool mode)
{
    int h = mode ? height : height2;
    setColor(0, 0, 0);
    graphics->fillRect(1, h - 4, width - 2, 3);
    setColor(255, 255, 255);
    graphics->fillRect(2, h - 3, (int)((int64_t)((width - 4) << 16) * (int64_t)var1 >> 16) >> 16, 1);
}
""",
        """void GameCanvas::method_161(int var1, bool mode)
{
    (void)mode;
    gd_set_progress(var1);
}
""",
        label="progress line to keypad",
    )

    _sub(
        src / "SettingsStringRender.cpp",
        """void SettingsStringRender::init()
{
    currentGameMenu = new GameMenu(text, micro, parentGameMenu);
""",
        """void SettingsStringRender::init()
{
    if (currentGameMenu) {
        for (auto *r : settingsStringRenders) delete r;
        settingsStringRenders.clear();
        delete currentGameMenu;
        currentGameMenu = nullptr;
    }
    currentGameMenu = new GameMenu(text, micro, parentGameMenu);
""",
        label="SettingsStringRender init reuse",
    )


if __name__ == "__main__":
    import sys
    apply(Path(sys.argv[1]))
    print("patched", sys.argv[1])
