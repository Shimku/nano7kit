#pragma once
#include <vector>
#include "RecordEnumeration.h"
#include "../utils/FileStream.h"

class RecordEnumerationImpl : public RecordEnumeration {
    int currentPos = 0;

public:
    RecordEnumerationImpl(std::vector<std::vector<int8_t>> data);
    RecordEnumerationImpl();
    ~RecordEnumerationImpl() override;

    int numRecords() override;
    std::vector<int8_t> nextRecord() override;
    int addRecord(std::vector<int8_t> bytes);
    void setRecord(int index, std::vector<int8_t> bytes);
    void reset() override;
    int nextRecordId() override;
    void destroy() override;

    void serialize(FileStream *outStream);
    void deserialize(FileStream *inStream);

    std::vector<std::vector<int8_t>> data;
};
