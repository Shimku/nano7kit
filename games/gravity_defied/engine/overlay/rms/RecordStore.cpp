#include "RecordStore.h"
#include "../gd_config.h"
#include "../utils/FileStream.h"

RecordStore::RecordStore(std::string filePath, RecordEnumerationImpl *records)
{
    this->filePath = filePath;
    this->records.reset(records);
}

RecordEnumeration *RecordStore::enumerateRecords(RecordFilter *, RecordComparator *, bool)
{
    return records.get();
}

void RecordStore::closeRecordStore() {}

int RecordStore::addRecord(std::vector<int8_t> arr, int offset, int numBytes)
{
    (void)offset;
    (void)numBytes;
    int id = records->addRecord(arr);
    save();
    return id;
}

void RecordStore::setRecord(int recordId, std::vector<int8_t> arr, int offset, int numBytes)
{
    (void)offset;
    (void)numBytes;
    records->setRecord(recordId, arr);
    save();
}

void RecordStore::save()
{
    FileStream outStream(filePath, std::ios::out | std::ios::binary);
    if (!outStream.isOpen()) {
        gd_log("save: cannot open %s for write", filePath.c_str());
        return;
    }
    records->serialize(&outStream);
    outStream.flushWrite();
}

RecordEnumerationImpl *RecordStore::load(const std::string& filePath)
{
    RecordEnumerationImpl *temp = new RecordEnumerationImpl();
    FileStream inStream(filePath, std::ios::in | std::ios::binary);
    if (!inStream.isOpen()) {
        gd_log("save: %s missing, empty store", filePath.c_str());
        return temp;
    }
    temp->deserialize(&inStream);
    return temp;
}

RecordStore *RecordStore::openRecordStore(std::string name, bool createIfNecessary)
{
    if (opened.find(name) == opened.end())
        opened[name] = createRecordStore(name, createIfNecessary);
    return opened[name].get();
}

std::unique_ptr<RecordStore> RecordStore::createRecordStore(std::string name, bool createIfNecessary)
{
    log("createRecordStore(" + name + ")");
    if (gd_fs_exists(name.c_str()))
        return std::unique_ptr<RecordStore>(new RecordStore(name, load(name)));
    if (createIfNecessary) {
        std::unique_ptr<RecordStore> rs(new RecordStore(name, new RecordEnumerationImpl()));
        rs->save();
        return rs;
    }
    return std::unique_ptr<RecordStore>(new RecordStore(name, new RecordEnumerationImpl()));
}

static std::vector<std::string> g_listed;

static void list_cb(const char *name, void *)
{
    g_listed.push_back(name);
}

std::vector<std::string> RecordStore::listRecordStores()
{
    g_listed.clear();
    gd_fs_list(list_cb, nullptr);
    log("listRecordStores n=" + std::to_string(g_listed.size()));
    return g_listed;
}

void RecordStore::deleteRecordStore(std::string name)
{
    log("deleteRecordStore(" + name + ")");
    gd_fs_remove(name.c_str());
    opened.erase(name);
}

void RecordStore::log(std::string s) { gd_log("%s", s.c_str()); }

void RecordStore::setRecordStoreDir(const char *progName)
{
    (void)progName;
    /* Files sit in /Apps/Data/GravityDefied; gd_fs_* already prefixes that. */
}

void RecordStore::shutdown()
{
    opened.clear();
    g_listed.clear();
}
