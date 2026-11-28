# PebbleOS on the iPod nano 7G

PebbleOS (https://github.com/coredevices/PebbleOS, Apache-2.0) is hosted as
one NanoApps app. The kernel and UI come from that tree, fetched at a pinned
commit at build time, not vendored, and not rewritten as a lookalike.

## Screen and buttons

The nano is 240x432. The Emery Pebble UI is 200x228, drawn at 1x in the
centre of the panel (2x would not fit). Around it is a dark bezel.

| Pebble | nano 7G |
| --- | --- |
| Back | Home |
| Select | Play/Pause |
| Up | Volume Up |
| Down | Volume Down |

Sleep/Wake is left to the iPod.

## Memory

The app has to run under both levels:

- tight: about 3.9 MB free, 2 MB largest block
- roomy: about 6 MB free, 4 MB largest block

If the largest block cannot hold the working set, the app shows
NOT ENOUGH MEMORY and does not start the kernel. Pause music or close
other apps and open it again.

The NanoApps loader puts the whole `.hbapp` into one allocation, so the
image itself has to stay well under the 2 MB largest block. The kernel
heap (384 KiB), a 64 KiB flash bank and the 200x228 framebuffer come from
the remaining free memory. File-backed flash lives under
`/Apps/Data/PebbleOS` (64 KiB banks in `flash/`), not as a 4 MiB RAM
buffer.

## How it is hosted

Kernel threads run on one NanoApps thread (setjmp fibers, Pebble-sized
stacks). The Emscripten posix fiber backend is not used. The image is
built with `-mcpu=cortex-a5 -mfpu=vfpv4-d16` and packed with `mkrelocapp`.
ARM unwinder tables are discarded.

## Device-like tests

A desktop qemu pass that still uses the host libc is not enough. A host
Unicorn run of the device `.hbapp` covers:

- no host libc unwinder
- `mkrelocapp` `.hbapp` layout
- Cortex-A5 VFPv4-D16
- both memory levels (3.9 MB / 2 MB and 6 MB / 4 MB)
- no save, and a corrupt save, recover cleanly
- a clean NOT ENOUGH MEMORY screen when the largest block is too small
- a 240x432 screenshot of a booted watchface (the default TicToc face)

The 200x228 panel is centred on the 240x432 screen. Stills are under
`nanoapps/apps/pebbleos/screenshots/` (`watchface-tictoc-240x432.png`,
`splash-240x432.png`, `low-memory-240x432.png`). That run is a host dry
run, not a pass on an iPod. The official normal shell starts in the
launcher; the nano 7G overlay starts the default watchface when one is
installed (TicToc on a clean filesystem). Closing the face still opens
the launcher.

## Commands

```
./start pebbleos status
./start pebbleos fetch
./start pebbleos build
./start nabase add "PebbleOS"
```

`./start pebbleos build` fetches the pinned tree, applies the nano 7G
overlay, builds `libpebbleos.a`, and links the `.hbapp`. After
`nabase add`, also copy `build/pebbleos-data/` to `/Apps/Data/PebbleOS/`
(resource flash banks). Use `nabase add` on the NanoApps base. Do not
use `./start apps add`.
