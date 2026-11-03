#!/usr/bin/env bash
# User-space Intel Mac bootstrap. No sudo. No shell rc edits.
# Homebrew dropped Intel; this is the supported no-brew route.
#
# Installs into build/macos-intel (gitignored) and writes env.sh:
#   micromamba (conda-forge python 3.12, libusb, pkg-config, make)
#   official Go darwin-amd64
#   Arm GNU toolchain 14.2.rel1 darwin-x86_64 (last Intel release)
#   mtools built from source
#   libzstd built from source, so the Arm toolchain runs without Homebrew
#   rustup-init --no-modify-path (cargo/rustup under the prefix)
#
# You still need Xcode Command Line Tools for clang (run
# `xcode-select --install` yourself; this script does not).
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PREFIX="${NANO7KIT_INTEL_PREFIX:-$ROOT/build/macos-intel}"
CACHE="$PREFIX/cache"
ENVSH="$PREFIX/env.sh"

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

if [[ $(uname -s) != Darwin ]]; then
  die "this script is for macOS Intel (x86_64)"
fi
if [[ $(uname -m) != x86_64 ]]; then
  die "this script is for Intel Macs (x86_64). Apple silicon: use Homebrew (docs/MACOS.md)"
fi
if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
  die "do not run this bootstrap as root; it is user-space only (no sudo)"
fi
if ! command -v curl >/dev/null 2>&1; then
  die "curl is required"
fi
if ! command -v cc >/dev/null 2>&1 && ! command -v clang >/dev/null 2>&1; then
  echo "cc/clang not found. Install Xcode Command Line Tools first:" >&2
  echo "  xcode-select --install" >&2
  echo "That Apple installer is not invoked here (no sudo in this script)." >&2
  echo "Re-run when cc exists." >&2
  exit 1
fi

mkdir -p "$PREFIX/bin" "$CACHE"

sha256_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 -- "$1" | awk '{print $1}'
  else
    python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1"
  fi
}

fetch() {
  local url=$1 dest=$2 expect=$3
  if [[ -f $dest ]]; then
    local got
    got=$(sha256_file "$dest")
    if [[ $got == "$expect" ]]; then
      return 0
    fi
    echo "cache hash mismatch for $dest, re-downloading" >&2
    rm -f "$dest"
  fi
  echo "==> fetching $url"
  curl -fL --retry 3 -o "$dest.part" "$url"
  local got
  got=$(sha256_file "$dest.part")
  if [[ $got != "$expect" ]]; then
    echo "hash mismatch: $dest" >&2
    echo "expected $expect" >&2
    echo "actual   $got" >&2
    rm -f "$dest.part"
    exit 1
  fi
  mv "$dest.part" "$dest"
}

# Pinned artifacts (hash-checked). Conda packages come from conda-forge
# repodata via micromamba, not individually pinned.
MICROMAMBA_VER=2.3.0-1
MICROMAMBA_URL="https://github.com/mamba-org/micromamba-releases/releases/download/${MICROMAMBA_VER}/micromamba-osx-64"
MICROMAMBA_SHA=1aac2a0c345c4ee441040a07784c5b6ed432738fa4500b15cb1e758870205320

GO_VER=1.23.4
GO_URL="https://go.dev/dl/go${GO_VER}.darwin-amd64.tar.gz"
GO_SHA=6700067389a53a1607d30aa8d6e01d198230397029faa0b109e89bc871ab5a0e

ARM_VER=14.2.rel1
ARM_TAR="arm-gnu-toolchain-${ARM_VER}-darwin-x86_64-arm-none-eabi.tar.xz"
ARM_URL="https://developer.arm.com/-/media/Files/downloads/gnu/${ARM_VER}/binrel/${ARM_TAR}"
ARM_SHA=2d9e717dd4f7751d18936ae1365d25916534105ebcb7583039eff1092b824505
ARM_DIR="$PREFIX/arm-gnu-toolchain-${ARM_VER}-darwin-x86_64-arm-none-eabi"

MTOOLS_VER=4.0.45
MTOOLS_URL="https://ftp.gnu.org/gnu/mtools/mtools-${MTOOLS_VER}.tar.gz"
MTOOLS_SHA=eea170403f48f0cd19b3d940e4bd12630a82601e25f944f47654b13d9d7eb5d4

ZSTD_VER=1.5.6
ZSTD_URL="https://github.com/facebook/zstd/releases/download/v${ZSTD_VER}/zstd-${ZSTD_VER}.tar.gz"
ZSTD_SHA=8c29e06cf42aacc1eafc4077ae2ec6c6fcb96a626157e0593d5e82a34fd403c1

RUSTUP_VER=1.27.1
RUSTUP_URL="https://static.rust-lang.org/rustup/archive/${RUSTUP_VER}/x86_64-apple-darwin/rustup-init"
RUSTUP_SHA=f547d77c32d50d82b8228899b936bf2b3c72ce0a70fb3b364e7fba8891eba781

echo "==> micromamba ${MICROMAMBA_VER} (conda-forge python 3.12, libusb, pkg-config, make)"
fetch "$MICROMAMBA_URL" "$CACHE/micromamba-osx-64" "$MICROMAMBA_SHA"
install -m 0755 "$CACHE/micromamba-osx-64" "$PREFIX/bin/micromamba"
export MAMBA_ROOT_PREFIX="$PREFIX/micromamba"
mkdir -p "$MAMBA_ROOT_PREFIX"
if [[ ! -x $PREFIX/env/bin/python ]]; then
  "$PREFIX/bin/micromamba" create -y -p "$PREFIX/env" -c conda-forge \
    python=3.12 libusb pkg-config make
fi

echo "==> Go ${GO_VER} darwin-amd64"
if [[ ! -x $PREFIX/go/bin/go ]]; then
  fetch "$GO_URL" "$CACHE/go${GO_VER}.darwin-amd64.tar.gz" "$GO_SHA"
  tar -C "$PREFIX" -xzf "$CACHE/go${GO_VER}.darwin-amd64.tar.gz"
fi

echo "==> Arm GNU toolchain ${ARM_VER} darwin-x86_64 (last Intel release)"
if [[ ! -x $ARM_DIR/bin/arm-none-eabi-gcc ]]; then
  fetch "$ARM_URL" "$CACHE/$ARM_TAR" "$ARM_SHA"
  tar -C "$PREFIX" -xJf "$CACHE/$ARM_TAR"
fi

echo "==> mtools ${MTOOLS_VER} from source"
if [[ ! -x $PREFIX/bin/mcopy ]]; then
  fetch "$MTOOLS_URL" "$CACHE/mtools-${MTOOLS_VER}.tar.gz" "$MTOOLS_SHA"
  rm -rf "$CACHE/mtools-${MTOOLS_VER}"
  tar -C "$CACHE" -xzf "$CACHE/mtools-${MTOOLS_VER}.tar.gz"
  (
    cd "$CACHE/mtools-${MTOOLS_VER}"
    ./configure --prefix="$PREFIX"
    make -j"$(sysctl -n hw.ncpu 2>/dev/null || echo 4)"
    make install
  )
fi

echo "==> libzstd ${ZSTD_VER} from source (the Arm toolchain links Homebrew's libzstd)"
if [[ ! -f $PREFIX/lib/libzstd.1.dylib ]]; then
  fetch "$ZSTD_URL" "$CACHE/zstd-${ZSTD_VER}.tar.gz" "$ZSTD_SHA"
  rm -rf "$CACHE/zstd-${ZSTD_VER}"
  tar -C "$CACHE" -xzf "$CACHE/zstd-${ZSTD_VER}.tar.gz"
  make -C "$CACHE/zstd-${ZSTD_VER}/lib" -j"$(sysctl -n hw.ncpu 2>/dev/null || echo 4)" libzstd
  mkdir -p "$PREFIX/lib"
  cp "$CACHE/zstd-${ZSTD_VER}/lib/libzstd.${ZSTD_VER}.dylib" "$PREFIX/lib/libzstd.1.dylib"
fi
NANO7_ARM_GCC="$ARM_DIR/bin/arm-none-eabi-gcc" PATH="$PREFIX/env/bin:$PATH" \
  bash "$ROOT/scripts/darwin-arm-zstd.sh" --check-or-fix --zstd "$PREFIX/lib/libzstd.1.dylib"

echo "==> rustup-init ${RUSTUP_VER} --no-modify-path"
export CARGO_HOME="$PREFIX/cargo"
export RUSTUP_HOME="$PREFIX/rustup"
mkdir -p "$CARGO_HOME" "$RUSTUP_HOME"
if [[ ! -x $CARGO_HOME/bin/rustup ]]; then
  fetch "$RUSTUP_URL" "$CACHE/rustup-init" "$RUSTUP_SHA"
  chmod +x "$CACHE/rustup-init"
  "$CACHE/rustup-init" -y --no-modify-path --default-toolchain none
fi
"$CARGO_HOME/bin/rustup" toolchain install nightly-2023-12-20 --profile minimal
"$CARGO_HOME/bin/rustup" target add --toolchain nightly-2023-12-20 thumbv6m-none-eabi

write_env_sh() {
  local cflags libs libdir
  export PKG_CONFIG_PATH="$PREFIX/env/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
  if [[ -x $PREFIX/env/bin/pkg-config ]] && \
     "$PREFIX/env/bin/pkg-config" --exists libusb-1.0; then
    cflags=$("$PREFIX/env/bin/pkg-config" --cflags libusb-1.0)
    libs=$("$PREFIX/env/bin/pkg-config" --libs libusb-1.0)
    libdir=$("$PREFIX/env/bin/pkg-config" --variable=libdir libusb-1.0)
  else
    cflags="-I$PREFIX/env/include"
    libs="-L$PREFIX/env/lib -lusb-1.0"
    libdir="$PREFIX/env/lib"
  fi
  cat > "$ENVSH" <<EOF
# Generated by scripts/macos-intel-bootstrap.sh. User-space only. No device writes.
# source this file before ./start on an Intel Mac without Homebrew.
export NANO7KIT_BOOTSTRAP=2
export TZ=UTC
# Use this prefix's Go, not a GOROOT set elsewhere (a mismatch breaks go build).
unset GOROOT
export GOTOOLCHAIN=local
export NANO7KIT_INTEL_PREFIX="$PREFIX"
export MAMBA_ROOT_PREFIX="$PREFIX/micromamba"
export CARGO_HOME="$PREFIX/cargo"
export RUSTUP_HOME="$PREFIX/rustup"
export PATH="$PREFIX/bin:$PREFIX/env/bin:$PREFIX/go/bin:$ARM_DIR/bin:$PREFIX/cargo/bin:\$PATH"
export PKG_CONFIG_PATH="$PREFIX/env/lib/pkgconfig:\${PKG_CONFIG_PATH:-}"
export CGO_ENABLED=1
export CGO_CPPFLAGS="$cflags"
export CGO_LDFLAGS="$libs -Wl,-rpath,$libdir"
EOF
}

write_env_sh

echo
echo "Intel Mac bootstrap ready (Homebrew is not used; no sudo; no rc edits)."
echo "  source $ENVSH"
echo "  ./start deps"
echo "  ./start doctor"
echo "Firmware builds need TZ=UTC (env.sh sets it) so rescue/untethered MSE hashes match."
echo "wInd3x/usbscsi: CGO flags include -Wl,-rpath so a root re-exec still loads this libusb."
