#include "Time.h"
#include "../gd_config.h"

namespace Time {
int64_t currentTimeMillis()
{
    return (int64_t)gd_millis();
}

void sleep(int64_t ms)
{
    /* The original engine blocked the thread. The nano tick must never
     * wait: the caller already converted blocking loops into ticks. */
    (void)ms;
}
}
