# PebbleOS hosted as a NanoApps RAW_SURFACE app.
# The real PebbleOS tree is fetched at build time (see ../../../host/pebbleos.py),
# not vendored here.
#
# Device-like flags: Cortex-A5 with VFPv4-D16, same as the nano 7G. The SDK
# default is soft+neon / cortex-a8; this app overrides CPU and FPU so the
# image is not built against a different register file than the device.
APP_NAME    := pebbleos
SRCS        := pebbleos.c
RAW_SURFACE := 1
HB_CPU      ?= cortex-a5
HB_FLOAT_ABI ?= soft
HB_FPU      ?= vfpv4-d16
RELOC_LDSCRIPT := $(CURDIR)/pebbleos.ld

# Kernel library produced by ./start pebbleos build (fetch + pbl + ninja).
PEBBLEOS_BUILD ?= $(abspath $(CURDIR)/../../../build/pebbleos/build-nano7)
PEBBLEOS_LIB   := $(PEBBLEOS_BUILD)/libpebbleos.a
PEBBLEOS_STATIC_LIBS := $(shell cat $(PEBBLEOS_BUILD)/pebbleos-static-libs.txt 2>/dev/null)
ifneq ($(wildcard $(PEBBLEOS_LIB)),)
  SRCS += host_hal.c host_stubs.c
  EXTRA_CFLAGS += -I$(CURDIR)/overlay/include -mword-relocations
  # Iterable sections and builtin apps live in the archive with no
  # references from the shim, so the whole archive is pulled; gc-sections
  # still drops unused functions. newlib-nano matches the kernel libc.
  EXTRA_LDFLAGS += --specs=nano.specs
  EXTRA_LDLIBS += -Wl,--whole-archive $(PEBBLEOS_LIB) $(PEBBLEOS_STATIC_LIBS) -Wl,--no-whole-archive                   -Wl,--start-group -lc -lm -lgcc -Wl,--end-group
  EXTRA_LINK_DEPS += $(PEBBLEOS_LIB) $(PEBBLEOS_STATIC_LIBS)
else
  SRCS += pebbleos_host.c
endif

include ../../sdk/hb_app.mk
