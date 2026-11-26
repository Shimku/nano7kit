APP_NAME    := gta6
SRCS        := gta6.c gta6_game.c gta6_gfx.c gta6_notice.c
RAW_SURFACE := 1
-include ../../sdk/hb_app.mk

ARM_CC ?= arm-none-eabi-gcc
ARM_CFLAGS := -mcpu=cortex-a8 -mthumb -mfloat-abi=soft -mfpu=neon \
	-ffreestanding -std=c11 -Wall -Wextra -fno-builtin

.PHONY: host armcheck
host:
	mkdir -p build
	cc -std=c11 -Wall -Wextra -O2 -DG6_HOST -o build/gta6_host \
		gta6_host.c gta6_game.c gta6_gfx.c gta6_notice.c

# Compile the device sources with the real ARM toolchain against SDK headers.
# Catches prototype mismatches such as int vs bool hb_audio_play_wav.
armcheck:
	@command -v $(ARM_CC) >/dev/null || { echo "arm-none-eabi-gcc missing"; exit 2; }
	mkdir -p build
	python3 ensure_sdk_headers.py > build/sdk-inc.path
	$(ARM_CC) $(ARM_CFLAGS) -I$$(cat build/sdk-inc.path) -I$$(cat build/sdk-inc.path)/freestanding -I. \
		-c gta6.c -o build/gta6_arm.o
	$(ARM_CC) $(ARM_CFLAGS) -I$$(cat build/sdk-inc.path) -I$$(cat build/sdk-inc.path)/freestanding -I. \
		-c gta6_game.c -o build/gta6_game_arm.o
	$(ARM_CC) $(ARM_CFLAGS) -I$$(cat build/sdk-inc.path) -I$$(cat build/sdk-inc.path)/freestanding -I. \
		-c gta6_notice.c -o build/gta6_notice_arm.o
	$(ARM_CC) $(ARM_CFLAGS) -I$$(cat build/sdk-inc.path) -I$$(cat build/sdk-inc.path)/freestanding -I. \
		-c gta6_gfx.c -o build/gta6_gfx_arm.o
	@echo "ARM_COMPILE ok sdk=$$(cat build/sdk-inc.path)"
