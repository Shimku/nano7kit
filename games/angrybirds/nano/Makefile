# libab_nano.a: the recompiled Angry Birds engine, its runtime and the nano backend
# (nano7kit, MIT). make NANOAPPS=... RECOMP=<recomp.py output dir>
NANOAPPS ?= ../../../nanoapps
RECOMP ?= build/recomp
CC := arm-none-eabi-gcc
AR := arm-none-eabi-ar
BUILD ?= build/nano
# Runtime stays ARM+VFP (the firmware traps VFP from Thumb). The translated
# engine is Thumb + software float so the .hbapp fits the ~2.25 MB image budget
# (ARM+VFP was ~3.25 MB text and left no room for the guest block).
ARCH := -marm -mcpu=cortex-a5 -mfloat-abi=softfp -mfpu=vfpv3-d16 -mword-relocations
ARCH_RECOMP := -mthumb -mcpu=cortex-a5 -mfloat-abi=soft -mword-relocations
CCOMMON := -Os -ffp-contract=off -std=gnu99 -ffunction-sections -fdata-sections \
          -fno-common -fno-strict-aliasing -fwrapv -fno-unwind-tables -fno-asynchronous-unwind-tables -fno-exceptions -MMD -MP -DFN_NANO \
          -I$(NANOAPPS)/sdk -I../rt -I$(RECOMP)
CFLAGS := $(ARCH) $(CCOMMON)
CFLAGS_RECOMP := $(ARCH_RECOMP) $(CCOMMON) -fomit-frame-pointer \
          -fno-align-functions -fno-align-jumps -fno-align-loops -fno-align-labels
RT := rt_core rt_imports fngl ab_entry
NANO := be_nano plat_nano fn_nano notice gl_left
GEN := $(basename $(notdir $(wildcard $(RECOMP)/recomp_*.c)))
OBJS := $(RT:%=$(BUILD)/rt/%.o) $(NANO:%=$(BUILD)/nano/%.o) $(GEN:%=$(BUILD)/recomp/%.o) $(BUILD)/nano/fn_jmp.o
all: $(BUILD)/libab_nano.a
$(BUILD)/libab_nano.a: $(OBJS)
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
	$(CC) $(CFLAGS_RECOMP) -w -c $< -o $@
-include $(OBJS:.o=.d)
.PHONY: all
