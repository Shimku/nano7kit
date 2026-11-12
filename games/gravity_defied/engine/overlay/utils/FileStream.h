#pragma once

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>
#include <ios>

#include "../gd_config.h"

class FileStream {
public:
    enum { in = 1, out = 2, binary = 4 };

    FileStream() {}

    FileStream(const std::string& file, std::ios::openmode mode)
    {
        path = file;
        writable = (mode & std::ios::out) != 0;
        if (writable) {
            buf.clear();
            pos = 0;
            opened = true;
            return;
        }
        unsigned sz = gd_fs_size(file.c_str());
        if (sz == 0) {
            opened = false;
            return;
        }
        buf.resize(sz);
        int n = gd_fs_read(file.c_str(), buf.data(), sz);
        if (n <= 0) {
            buf.clear();
            opened = false;
            return;
        }
        if ((unsigned)n < sz) buf.resize((unsigned)n);
        pos = 0;
        opened = true;
    }

    virtual ~FileStream()
    {
        flushWrite();
    }

    template <class T>
    void readVariable(T *p, bool swapEndianness = false, std::size_t size = 0)
    {
        char *pChar = reinterpret_cast<char *>(p);
        if (!size) size = sizeof(T);
        read_impl(pChar, (std::streamsize)size);
        if (swapEndianness) std::reverse(pChar, pChar + size);
    }

    template <class T>
    void writeVariable(T *p, std::size_t size = 0)
    {
        char *pChar = reinterpret_cast<char *>(p);
        if (!size) size = sizeof(T);
        write_impl(pChar, (std::streamsize)size);
    }

    virtual bool isOpen() { return opened; }

    virtual void setPos(std::streampos p)
    {
        long n = (long)p;
        if (n < 0) n = 0;
        if (n > (long)buf.size()) n = (long)buf.size();
        pos = (size_t)n;
    }

    void flushWrite()
    {
        if (writable && opened && !path.empty()) {
            int ok = gd_fs_write(path.c_str(), buf.data(), (unsigned)buf.size());
            gd_log("save: write %s %u bytes %s", path.c_str(), (unsigned)buf.size(), ok ? "ok" : "FAIL");
        }
        writable = false;
    }

protected:
    virtual void read_impl(char *s, std::streamsize n)
    {
        if (!opened || n <= 0) {
            if (n > 0) std::memset(s, 0, (size_t)n);
            return;
        }
        size_t want = (size_t)n;
        if (pos >= buf.size()) {
            std::memset(s, 0, want);
            return;
        }
        size_t have = buf.size() - pos;
        if (have > want) have = want;
        std::memcpy(s, buf.data() + pos, have);
        if (have < want) std::memset(s + have, 0, want - have);
        pos += have;
    }

    virtual void write_impl(char *s, std::streamsize n)
    {
        if (!opened || n <= 0) return;
        size_t want = (size_t)n;
        if (pos + want > buf.size()) buf.resize(pos + want);
        std::memcpy(buf.data() + pos, s, want);
        pos += want;
    }

    std::string path;
    std::vector<uint8_t> buf;
    size_t pos = 0;
    bool opened = false;
    bool writable = false;
};
