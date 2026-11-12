#pragma once
#include "FileStream.h"

class EmbedFileStream : public FileStream {
public:
    EmbedFileStream(const std::string& embedFilePath)
        : FileStream(embedFilePath, std::ios::in | std::ios::binary)
    {
    }
};
