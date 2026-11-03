#pragma once
#include "memtrack.h"
unsigned int nano7_pool_cap(const char* theName, unsigned int theDefault, unsigned int theItemBytes);
void nano7_pool_note(const char* theName, unsigned int theSize);
[[noreturn]] void nano7_pool_full(const char* theName, unsigned int theMax);
