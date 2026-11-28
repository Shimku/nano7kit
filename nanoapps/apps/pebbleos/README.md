# PebbleOS (hosted) for the iPod nano 7G

This is a NanoApps host for the real PebbleOS (Apache-2.0, Core Devices),
not a recreation of the UI. PebbleOS is fetched at build time and is not
vendored in this folder.

The 240x432 screen centres a 200x228 Pebble (Emery) panel. Buttons:

| Pebble | nano 7G |
| --- | --- |
| Back | Home (the round button under the screen) |
| Select | Play/Pause |
| Up | Volume Up |
| Down | Volume Down |

Sleep/Wake is not used, so it can still sleep the iPod.

The app must fit and run under both memory levels the launcher sees: about
3.9 MB free with a 2 MB largest block, and about 6 MB / 4 MB. If the
largest block is too small, it shows a clean NOT ENOUGH MEMORY screen and
does not start the kernel.

The real kernel is linked (setjmp fibers on one NanoApps thread, a
framebuffer and button HAL, file-backed flash under `/Apps/Data/PebbleOS`).
A Unicorn run of the device `.hbapp` covers: no host libc unwinder,
`mkrelocapp` layout, Cortex-A5 VFPv4-D16, both memory levels, no save
and a corrupt save, a clean NOT ENOUGH MEMORY screen, and a 240x432
TicToc watchface still (`screenshots/watchface-tictoc-240x432.png`).
That is a host dry run, not a pass on an iPod.

## Build and install (friend-pod, Disk Mode)

```
./start sdk build pebbleos
./start nabase add "PebbleOS"
```

Or `./start pebbleos build`, which fetches the pinned PebbleOS tree and
then runs the same SDK build. On a NanoApps-base iPod, only use
`./start nabase add`, not `./start apps add`.

## Build

```
./start pebbleos fetch
./start pebbleos build
./start nabase add "PebbleOS"
```

`./start pebbleos build` links the real PebbleOS kernel (Cortex-A5
VFPv4-D16, `mkrelocapp` `.hbapp`). Copy `build/pebbleos-data/` to
`/Apps/Data/PebbleOS/` so the first boot has system resources. Do not
use `./start apps add` on a NanoApps base.
