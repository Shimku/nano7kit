# Fruit Ninja.hbapp (nano7kit): copied to nanoapps/apps/fruitninja_rc at build time.
APP_NAME := fruitninja
SRCS := fn_entry.c ../../sdk/hb_surface_input.c
RELOC := 1
EXTRA_CFLAGS += -DHB_GL_SURFACE_APP -DHB_LV_RELOC
CHECK_NO_THUMB_FP := 1
FN_LIB ?= build/libfn_nano.a
include ../../sdk/hb_app.mk
NEWLIB_FLAGS := -marm -march=armv5te+fp -mfloat-abi=softfp
LDLIBS := $(FN_LIB) \
          $(shell $(CC) $(NEWLIB_FLAGS) -print-file-name=libm.a) \
          $(shell $(CC) $(NEWLIB_FLAGS) -print-file-name=libc.a) $(LIBGCC)
$(BUILD)/$(APP_NAME).reloc.elf: $(FN_LIB)
