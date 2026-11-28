# PebbleOS hosted as a NanoApps RAW_SURFACE app.
# The real PebbleOS tree is fetched at build time (see ../../../../host/pebbleos.py),
# not vendored here.
#
# Device-like flags: Cortex-A5 with VFPv4-D16, same as the nano 7G. The SDK
# default is soft+neon; this app overrides the FPU so the image is not built
# against a different register file than the device.
APP_NAME    := pebbleos
SRCS        := pebbleos.c pebbleos_host.c
RAW_SURFACE := 1
HB_FLOAT_ABI ?= soft
HB_FPU      ?= vfpv4-d16
include ../../sdk/hb_app.mk
