# Mac setup

`./install` checks for the tools it needs and tells you what is missing.
You also need the Xcode Command Line Tools: `xcode-select --install`.

## Apple silicon

Install [Homebrew](https://brew.sh), then:

```sh
brew install make git libusb pkg-config go mtools
brew install --cask --no-binaries gcc-arm-embedded
./start deps
./start doctor
```

The compiler-only `arm-none-eabi-gcc` formula lacks the newlib headers and libraries
needed by game ports. `./start deps` checks both and installs the complete Arm GNU
toolchain when needed. `./start` automatically selects a complete toolchain from
PATH, `build/arm-toolchain`, the Intel bootstrap, or `/Applications/ArmGNUToolchain`.
The cask uses `--no-binaries` to avoid conflicts with existing Homebrew tools.

Rust comes from [rustup](https://rustup.rs). Apple silicon is untested on hardware.

## Intel

Homebrew no longer supports Intel Macs. This script installs everything into
`build/macos-intel` instead, with no password and no changes to your shell files:

```sh
./scripts/macos-intel-bootstrap.sh
source build/macos-intel/env.sh
./start deps
./start doctor
```

`./install` runs it for you when needed. The Go, Arm toolchain, mtools, libzstd and
rustup downloads are pinned and hash-checked; Python, libusb, pkg-config and make come
from conda-forge through micromamba. The Arm toolchain expects Homebrew's libzstd, so
the script builds libzstd from source and points the toolchain at that copy.
Run `source build/macos-intel/env.sh` in each new Terminal window before `./start`.

## Notes

- Commands that talk to the iPod in recovery mode run as root (`sudo -E`). That is how
  macOS lets a command-line tool take the USB device.
- The firmware build is set to `TZ=UTC`. In another timezone the image hash changes
  and the build stops.
- Close Finder windows for the iPod before a write.
