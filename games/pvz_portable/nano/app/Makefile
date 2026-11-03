# PvZ Portable.hbapp (nano7kit): the PvZ-Portable engine (LGPL-3.0-or-later, wszqkzqk) built in ARM and Thumb state into
# libpvz_nano.a by host/pvz_portable.py, linked here with the SDK. Copied to nanoapps/apps/pvz_portable at build time.
APP_NAME := pvz
SRCS := pvz_entry.c pvz_crumb.c ../../sdk/hb_surface_input.c
RELOC := 1
EXTRA_CFLAGS += -DHB_GL_SURFACE_APP -DHB_LV_RELOC
CHECK_NO_THUMB_FP := 1
PVZ_LIB ?= build/libpvz_nano.a
include ../../sdk/hb_app.mk
NEWLIB_FLAGS := -marm -march=armv5te+fp -mfloat-abi=softfp
# the C and C++ libraries come from the ARMv5TE multilib: built without movw/movt, so the loader can rebase them
# (the Thumb v7-a ones use movw/movt and mkrelocapp refuses them)
LDLIBS := -Wl,--whole-archive $(PVZ_LIB) -Wl,--no-whole-archive \
          $(shell $(CC) $(NEWLIB_FLAGS) -print-file-name=libstdc++_nano.a) \
          $(shell $(CC) $(NEWLIB_FLAGS) -print-file-name=libsupc++_nano.a) \
          $(shell $(CC) $(NEWLIB_FLAGS) -print-file-name=libm.a) \
          $(shell $(CC) $(NEWLIB_FLAGS) -print-file-name=libc.a) \
          $(shell $(CC) $(NEWLIB_FLAGS) -print-file-name=libgcc.a) $(LIBGCC)
# the ARM-state libgcc first: its C++ unwinder saves VFP registers in ARM state (the SDK's Thumb one would trap)
# --all-alloc: .init_array (rebased) and .ARM.exidx in the image; --pad-sectors: whole 4096-byte sectors
MKRELOC_FLAGS := --all-alloc --pad-sectors
$(BUILD)/$(APP_NAME).reloc.elf: $(PVZ_LIB)
