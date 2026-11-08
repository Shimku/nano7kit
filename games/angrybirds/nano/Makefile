# libab_nano.a: Android Angry Birds runtime + linked .so object (nano7kit, MIT).
NANOAPPS ?= ../../../nanoapps
SOOBJ ?= build/angrybirds_so.o
ZLIB ?= build/zlib-1.3.1
CC := arm-none-eabi-gcc
AR := arm-none-eabi-ar
BUILD ?= build/nano
ARCH := -marm -mcpu=cortex-a5 -mfloat-abi=softfp -mfpu=vfpv3-d16 -mword-relocations
CFLAGS := $(ARCH) -Os -ffp-contract=off -std=gnu99 -ffunction-sections -fdata-sections \
          -fno-common -fno-strict-aliasing -fwrapv -fno-unwind-tables -fno-asynchronous-unwind-tables -fno-exceptions \
          -MMD -MP -DFN_NANO -I$(NANOAPPS)/sdk -I../rt -I$(ZLIB)
RT := so_load jni shims fngl ab_entry audio_stub
NANO := be_nano plat_nano fn_nano notice gl_left
ZSRC := adler32 crc32 inffast inflate inftrees zutil
OBJS := $(RT:%=$(BUILD)/rt/%.o) $(NANO:%=$(BUILD)/nano/%.o) $(BUILD)/nano/fn_jmp.o \
        $(BUILD)/rt/cxa_hook.o $(ZSRC:%=$(BUILD)/zlib/%.o)
all: $(BUILD)/libab_nano.a
$(BUILD)/libab_nano.a: $(OBJS) $(SOOBJ)
	$(AR) crs $@ $(OBJS) $(SOOBJ)
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
$(BUILD)/rt/cxa_hook.o: ../rt/cxa_hook.S
	@mkdir -p $(dir $@)
	$(CC) $(ARCH) -c $< -o $@
$(BUILD)/zlib/%.o: $(ZLIB)/%.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c $< -o $@
-include $(OBJS:.o=.d)
.PHONY: all
