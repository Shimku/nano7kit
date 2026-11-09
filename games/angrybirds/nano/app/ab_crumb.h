/* ab_crumb.h: Thumb, no floating point. Start-up breadcrumbs for a device reboot. */
#ifndef AB_CRUMB_H
#define AB_CRUMB_H
#include <stdint.h>
void ab_crumb_reset(void);
void ab_crumb(const char *what, uint32_t a);
#endif
