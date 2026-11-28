# Tiny LLM: a 260K-parameter story model as a NanoApps RAW_SURFACE app.
# The model file is made at build time from karpathy/tinyllamas (see
# ../../../host/tinyllm.py) and copied to /Apps/Data/TinyLLM/model.bin by
# ./start nabase add 'Tiny LLM'. It is not part of the .hbapp.
#
# Device-like flags: Cortex-A5, VFPv4-D16 register file, soft float (no FP
# instructions in the image; the integer SIMD in tllm.c is plain ARMv7-A).
APP_NAME     := tinyllm
SRCS         := tinyllm.c tllm.c
RAW_SURFACE  := 1
HB_CPU       ?= cortex-a5
HB_FLOAT_ABI ?= soft
HB_FPU       ?= vfpv4-d16
# The stock hb_app.mk always passes -mcpu=cortex-a8; the later -mcpu here
# wins for every object, so the code is scheduled and checked for the A5.
# -mword-relocations: the loader only rebases 32-bit words.
EXTRA_CFLAGS += -mcpu=cortex-a5 -mword-relocations -std=gnu11 -Wdouble-promotion

-include ../../sdk/hb_app.mk

.PHONY: host
host:
	mkdir -p build
	cc -std=c11 -O2 -Wall -Wextra -fno-strict-aliasing -o build/tllm_host tllm_host.c tllm.c
