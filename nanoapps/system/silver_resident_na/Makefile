# nano7kit enhanced resident – NanoApps 1.0 silver_resident + heavy-app helpers.
# MIT base: nfzerox NanoApps (see nanoapps/vendor/nanoapps10/NOTICE.md).
APP_NAME := silver_resident
# build/na_silver_icon.c is generated at build time from the fetched SDK
# hb_silver_icon.c (hash-pinned by upstream/lock.json, never edited or
# committed here) with the lean icon LRU of the hardware-tested resident-B:
# 16 buffers of 0xC800 bytes instead of 20 x 56 KB (every icon is 50,208 B).
SRCS     := silver_resident.c bluetooth_fix.c dock_fix.c heavy_launch.c ../../sdk/hb_app_catalog.c \
            build/na_silver_icon.c
RELOC    := 1
EXTRA_CFLAGS += -DHB_RAWMSG_TRACE -DHB_NANO7_HEAVY
include ../../sdk/hb_app.mk
SDK_SRCS := $(filter-out $(SDK_DIR)/hb_silver_icon.c,$(SDK_SRCS))

# Memory (the resident also refuses the 1 MiB icon-cache grow, see
# hb_register_alloc in silver_resident.c).
build/na_silver_icon.c: $(SDK_DIR)/hb_silver_icon.c | $(BUILD)
	sed -e 's/^#define ICON_LRU_N .*/#define ICON_LRU_N    16u/' \
	    -e 's/^#define ICON_BUF_SZ .*/#define ICON_BUF_SZ   0xC800u/' $< > $@.tmp
	grep -q '^#define ICON_LRU_N    16u$$' $@.tmp
	grep -q '^#define ICON_BUF_SZ   0xC800u$$' $@.tmp
	mv $@.tmp $@
