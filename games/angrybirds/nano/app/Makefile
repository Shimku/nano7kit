# Angry Birds.hbapp (nano7kit): copied to nanoapps/apps/angrybirds_rc at build time.
APP_NAME := angrybirds
SRCS := fn_entry.c ab_crumb.c ../../sdk/hb_surface_input.c
RELOC := 1
EXTRA_CFLAGS += -DHB_GL_SURFACE_APP -DHB_LV_RELOC
CHECK_NO_THUMB_FP := 1
AB_LIB ?= build/libab_nano.a
include ../../sdk/hb_app.mk
NEWLIB_FLAGS := -marm -march=armv5te+fp -mfloat-abi=softfp
# shims.c owns bionic _ctype_ (a pointer). newlib's ctype_.o is the table; the
# first definition wins. Blob globals stay inside .abso (so_to_obj does not
# re-export them).
# ARM-state libgcc first: its C++ unwinder saves VFP in ARM state (the SDK's
# Thumb one traps). --all-alloc: .init_array (rebased) and .ARM.exidx in the
# image; --pad-sectors: whole 4096-byte FAT sectors.
RELOC_LDFLAGS += -Wl,--allow-multiple-definition
LDLIBS := $(AB_LIB) \
          $(shell $(CC) $(NEWLIB_FLAGS) -print-file-name=libm.a) \
          $(shell $(CC) $(NEWLIB_FLAGS) -print-file-name=libc.a) \
          $(shell $(CC) $(NEWLIB_FLAGS) -print-file-name=libgcc.a) $(LIBGCC)
MKRELOC_FLAGS := --all-alloc --pad-sectors
$(BUILD)/$(APP_NAME).reloc.elf: $(AB_LIB)
