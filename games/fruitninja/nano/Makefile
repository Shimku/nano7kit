# libfn_nano.a: the recompiled Fruit Ninja engine, its runtime and the nano backend
# (nano7kit, MIT). make NANOAPPS=... RECOMP=<recomp.py output dir>
NANOAPPS ?= ../../../nanoapps
RECOMP ?= build/recomp
CC := arm-none-eabi-gcc
AR := arm-none-eabi-ar
BUILD ?= build/nano
ARCH := -marm -mcpu=cortex-a5 -mfloat-abi=softfp -mfpu=vfpv3-d16 -mword-relocations
CFLAGS := $(ARCH) -Os -ffp-contract=off -std=gnu99 -ffunction-sections -fdata-sections \
          -fno-common -fno-strict-aliasing -fwrapv -fno-unwind-tables -fno-asynchronous-unwind-tables -fno-exceptions -MMD -MP -DFN_NANO \
          -I$(NANOAPPS)/sdk -I../rt -I$(RECOMP)
RT := rt_core rt_imports fngl fn_entry
NANO := be_nano plat_nano fn_nano notice gl_left
GEN := $(basename $(notdir $(wildcard $(RECOMP)/recomp_*.c)))
OBJS := $(RT:%=$(BUILD)/rt/%.o) $(NANO:%=$(BUILD)/nano/%.o) $(BUILD)/nano/fn_jmp.o $(GEN:%=$(BUILD)/recomp/%.o)
all: $(BUILD)/libfn_nano.a
$(BUILD)/libfn_nano.a: $(OBJS)
	$(AR) crs $@ $(OBJS)
	@arm-none-eabi-size -t $@ | tail -1
$(BUILD)/rt/%.o: ../rt/%.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -Wall -c $< -o $@
$(BUILD)/nano/%.o: %.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -Wall -c $< -o $@
$(BUILD)/nano/fn_jmp.o: fn_jmp.S
	@mkdir -p $(dir $@)
	$(CC) $(ARCH) -c $< -o $@
$(BUILD)/recomp/%.o: $(RECOMP)/%.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -w -c $< -o $@
-include $(OBJS:.o=.d)
.PHONY: all
