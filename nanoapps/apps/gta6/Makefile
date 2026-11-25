APP_NAME    := gta6
SRCS        := gta6.c gta6_game.c gta6_notice.c
RAW_SURFACE := 1
-include ../../sdk/hb_app.mk

.PHONY: host
host:
	mkdir -p build
	cc -std=c11 -Wall -Wextra -O2 -DG6_HOST -o build/gta6_host \
		gta6_host.c gta6_game.c gta6_notice.c
