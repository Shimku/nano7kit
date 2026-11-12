# Gravity Defied.hbapp (nano7kit): rgimad/gravity_defied_cpp (GPL-2.0) built in ARM
# state into libgd_nano.a by host/gravity_defied.py, linked here with the SDK.
APP_NAME := gd
SRCS := gd_nano.c notice.c gd_plat.c gd_sys.c
RAW_SURFACE := 1
ENGINE_SRC ?= .
EXTRA_CFLAGS += -DHB_LV_RELOC -I. -I$(ENGINE_SRC) -I$(ENGINE_SRC)/nano
CHECK_NO_THUMB_FP := 1
GD_LIB ?= build/libgd_nano.a
include ../../sdk/hb_app.mk
NEWLIB_FLAGS := -marm -march=armv5te+fp -mfloat-abi=softfp
LDLIBS := -Wl,--whole-archive $(GD_LIB) -Wl,--no-whole-archive \
          $(shell $(CC) $(NEWLIB_FLAGS) -print-file-name=libstdc++_nano.a) \
          $(shell $(CC) $(NEWLIB_FLAGS) -print-file-name=libsupc++_nano.a) \
          $(shell $(CC) $(NEWLIB_FLAGS) -print-file-name=libm.a) \
          $(shell $(CC) $(NEWLIB_FLAGS) -print-file-name=libc.a) \
          $(shell $(CC) $(NEWLIB_FLAGS) -print-file-name=libgcc.a) $(LIBGCC)
MKRELOC_FLAGS := --all-alloc --pad-sectors
$(BUILD)/$(APP_NAME).reloc.elf: $(GD_LIB)
