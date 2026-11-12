#pragma once

#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

#include "RecordEnumerationImpl.h"

class RecordFilter;
class RecordComparator;

class RecordStore {
    RecordStore(std::string filePath, RecordEnumerationImpl *records);
    void save();
    static RecordEnumerationImpl *load(const std::string& filePath);
    static std::unique_ptr<RecordStore> createRecordStore(std::string name, bool createIfNecessary);
    static void log(std::string s);

    inline static std::unordered_map<std::string, std::unique_ptr<RecordStore>> opened;
    std::string filePath;
    std::unique_ptr<RecordEnumerationImpl> records;

public:
    static void setRecordStoreDir(const char *progName);
    static void shutdown();
    static RecordStore *openRecordStore(std::string name, bool createIfNecessary);
    void closeRecordStore();
    static void deleteRecordStore(std::string name);
    static std::vector<std::string> listRecordStores();
    RecordEnumeration *enumerateRecords(RecordFilter *filter, RecordComparator *comparator, bool keepUpdated);
    int addRecord(std::vector<int8_t> arr, int offset, int numBytes);
    void setRecord(int recordId, std::vector<int8_t> arr, int offset, int numBytes);
};
