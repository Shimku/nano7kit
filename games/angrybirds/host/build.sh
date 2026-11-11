#!/bin/sh
# nano7kit, MIT
# Build the qemu-arm host harness. Usage: sh host/build.sh [OUT]
set -e
O=${1:-/tmp/ab-android}
H=$(dirname "$0")
R=$H/../rt
ROOT=$(cd "$H/../../.." && pwd)
ZSRC=$ROOT/build/zlib-1.3.1
ZTGZ=/tmp/zlib-1.3.1.tar.gz
# armel (softfp) matches libangrybirds.so. gnueabihf libm is hard-float and cannot
# be called from the SO without math_wrap.S; the armel libm is already GPR.
CC=${CROSS_CC:-arm-linux-gnueabi-gcc}
# Cortex-A5 VFPv4-D16: same GPR/VFP as the nano. Do not enable NEON/d16-d31/WMMX.
CF="-O1 -g -static -marm -mcpu=cortex-a5 -mfloat-abi=softfp -mfpu=vfpv3-d16 -mno-unaligned-access -fno-strict-aliasing -fwrapv -Wall -I$R -I$H -I$ZSRC"
mkdir -p "$O" "$ROOT/build"
if [ ! -d "$ZSRC" ]; then
  if [ ! -f "$ZTGZ" ]; then
    curl -fsSL -o "$ZTGZ" "https://github.com/madler/zlib/releases/download/v1.3.1/zlib-1.3.1.tar.gz"
  fi
  echo "9a93b2b7dfdac77ceba5a558a580e74667dd6fede4585b91eefb60f03b72df23  $ZTGZ" | sha256sum -c
  tar -xzf "$ZTGZ" -C "$ROOT/build"
fi
$CC $CF -c "$ZSRC/adler32.c" -o "$O/adler32.o"
$CC $CF -c "$ZSRC/crc32.c" -o "$O/crc32.o"
$CC $CF -c "$ZSRC/inffast.c" -o "$O/inffast.o"
$CC $CF -c "$ZSRC/inflate.c" -o "$O/inflate.o"
$CC $CF -c "$ZSRC/inftrees.c" -o "$O/inftrees.o"
$CC $CF -c "$ZSRC/zutil.c" -o "$O/zutil.o"
$CC $CF -c "$R/so_load.c" -o "$O/so_load.o"
$CC $CF -c "$R/jni.c" -o "$O/jni.o"
$CC $CF -c "$R/shims.c" -o "$O/shims.o"
$CC $CF -c "$R/audio_stub.c" -o "$O/audio_stub.o"
$CC $CF -c "$R/fngl.c" -o "$O/fngl.o"
$CC $CF -c "$R/ab_entry.c" -o "$O/ab_entry.o"
$CC $CF -c "$H/be_soft.c" -o "$O/be_soft.o"
$CC $CF -c "$H/main.c" -o "$O/main.o"
# Compact ARM setjmp: glibc jmp_buf is 392 bytes and smashes libpng's png_struct.
$CC $CF -c "$H/../nano/fn_jmp.S" -o "$O/fn_jmp.o"
$CC $CF -c "$R/cxa_hook.S" -o "$O/cxa_hook.o"
$CC $CF -o "$O/abhost" \
  "$O/so_load.o" "$O/jni.o" "$O/shims.o" "$O/audio_stub.o" "$O/fngl.o" "$O/ab_entry.o" \
  "$O/be_soft.o" "$O/main.o" "$O/fn_jmp.o" "$O/cxa_hook.o" \
  "$O/adler32.o" "$O/crc32.o" "$O/inffast.o" "$O/inflate.o" "$O/inftrees.o" "$O/zutil.o" \
  -lm
echo built "$O/abhost"
