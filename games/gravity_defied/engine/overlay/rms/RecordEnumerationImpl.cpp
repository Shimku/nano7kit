#include "RecordEnumerationImpl.h"
#include "../gd_config.h"
#include <cstdint>

RecordEnumerationImpl::RecordEnumerationImpl(std::vector<std::vector<int8_t>> data)
{
    this->data = data;
}

RecordEnumerationImpl::RecordEnumerationImpl() {}
RecordEnumerationImpl::~RecordEnumerationImpl() {}

int RecordEnumerationImpl::numRecords() { return (int)data.size(); }

std::vector<int8_t> RecordEnumerationImpl::nextRecord()
{
    if (currentPos < 0 || currentPos >= (int)data.size()) return {};
    return data[currentPos++];
}

int RecordEnumerationImpl::addRecord(std::vector<int8_t> bytes)
{
    data.push_back(bytes);
    return (int)data.size() - 1;
}

void RecordEnumerationImpl::setRecord(int index, std::vector<int8_t> bytes)
{
    if (index < 0) return;
    if (index >= (int)data.size()) data.resize((size_t)index + 1);
    data[index] = bytes;
}

void RecordEnumerationImpl::reset() { currentPos = 0; }

int RecordEnumerationImpl::nextRecordId()
{
    if (currentPos >= (int)data.size()) return 0;
    return currentPos;
}

void RecordEnumerationImpl::destroy()
{
    /* Upstream cleared the live store here, so later setRecord threw and
     * progress never saved (rgimad issue #8). Leave the records in place. */
}

void RecordEnumerationImpl::serialize(FileStream *outStream)
{
    /* int32_t counts, same on the host harness and the 32-bit iPod. */
    int32_t pos = currentPos;
    int32_t nrec = (int32_t)data.size();
    outStream->writeVariable(&pos);
    outStream->writeVariable(&nrec);
    for (auto i = data.cbegin(); i != data.cend(); i++) {
        int32_t nbytes = (int32_t)i->size();
        outStream->writeVariable(&nbytes);
        for (auto j = i->cbegin(); j != i->cend(); j++) {
            int8_t buffer = *j;
            outStream->writeVariable(&buffer);
        }
    }
}

void RecordEnumerationImpl::deserialize(FileStream *inStream)
{
    int32_t pos = 0;
    int32_t nrec = 0;
    inStream->readVariable(&pos);
    inStream->readVariable(&nrec);
    currentPos = (int)pos;
    if (nrec < 0 || nrec > 64) {
        gd_log("save: corrupt record count %d, starting empty", (int)nrec);
        data.clear();
        currentPos = 0;
        return;
    }
    data.resize((size_t)nrec);
    for (int32_t i = 0; i < nrec; ++i) {
        int32_t nbytes = 0;
        inStream->readVariable(&nbytes);
        if (nbytes < 0 || nbytes > 4096) {
            gd_log("save: corrupt record %d size %d, starting empty", (int)i, (int)nbytes);
            data.clear();
            currentPos = 0;
            return;
        }
        data[(size_t)i].resize((size_t)nbytes);
        for (int32_t j = 0; j < nbytes; ++j)
            inStream->readVariable(&data[(size_t)i][(size_t)j]);
    }
}
