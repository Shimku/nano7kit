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
        var2 += font->getBaselinePosition() + 2;
    }
    if (canvasHeight < 100) {
        ++var2;
    } else {
        var2 += 4;
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

    old_fill = """void MenuManager::fillCanvasWithImage(Graphics* graphics)
{
    for (int y = 0; y < getCanvasHeight(); y += rasterImage->getHeight()) {
        for (int x = 0; x < getCanvasWidth(); x += rasterImage->getWidth()) {
            graphics->drawImage(rasterImage.get(), x, y, 20);
        }
    }
}
"""
    new_fill = """void MenuManager::fillCanvasWithImage(Graphics* graphics)
{
    if (!rasterImage || rasterImage->getWidth() < 1 || rasterImage->getHeight() < 1) return;
    for (int y = 0; y < getCanvasHeight(); y += rasterImage->getHeight()) {
        for (int x = 0; x < getCanvasWidth(); x += rasterImage->getWidth()) {
            graphics->drawImage(rasterImage.get(), x, y, 20);
        }
    }
}
"""
    if old_fill not in text:
        raise PatchError("MenuManager.cpp: missing fillCanvasWithImage")
    text = text.replace(old_fill, new_fill, 1)

    if '#include "gd_config.h"' not in text:
        text = '#include "gd_config.h"\n' + text
    mm.write_text(text, encoding="utf-8")

    mh = src / "MenuManager.h"
    ht = mh.read_text(encoding="utf-8")
    if "void nanoTap" not in ht:
        ht = ht.replace("    void processKeyCode(int keyCode);\n};",
                        "    void processKeyCode(int keyCode);\n    void nanoTap(int x, int y);\n};")
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
